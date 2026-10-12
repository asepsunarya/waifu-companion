import io
import json
import logging
import re
import runpy
import shutil
import sys
import types
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from importlib.machinery import ModuleSpec
from pathlib import Path, PureWindowsPath

import numpy as np
import pytest
from tinytuya.core.error_helper import ERR_PAYLOAD, error_json
from tinytuya.core.XenonDevice import XenonDevice

from waifu import cli
from waifu.app import Assistant
from waifu.autostart import DEFAULT_DELAY_S, build_command
from waifu.brain.base import Brain, BrainReply, ToolCallRecord
from waifu.config import RvcConfig
from waifu.devices.base import DeviceError
from waifu.devices.ir_codes import IrCodeStore
from waifu.devices.ir_device import IrDevice
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.single_instance import InstanceLock
from waifu.voice.base import SpeakerEmbedder, SpeechToText, Transcript
from waifu.voice.rvc import RvcError
from waifu.voice.speaker import SpeakerVerifier
from waifu.voice.stt import WhisperStt
from waifu.voice.tts import TtsError

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_RATE = 16000
FRAME = 480  # 30 ms at 16 kHz, the example config's frame size
SILENT_FRAME = np.zeros(FRAME, dtype=np.float32)
LOUD_FRAME = np.full(FRAME, 0.1, dtype=np.float32)
VOICE_MODULES = ("faster_whisper", "speechbrain", "torch", "sounddevice")
MODEL_LIBRARIES = ("faster_whisper", "speechbrain", "torch")


@pytest.fixture(autouse=True)
def no_real_models(monkeypatch):
    """The voice extra may be installed: a test that forgets a fake fails to import instead
    of loading multi-GB Whisper/ECAPA models onto the GPU."""
    for name in MODEL_LIBRARIES:
        monkeypatch.setitem(sys.modules, name, None)


@pytest.fixture(autouse=True)
def no_real_rvc(monkeypatch):
    """A test that forgets the `rvc` fakes fails instead of spawning Applio on the GPU or
    probing port 9881 (tests/conftest.py also refuses to spawn tools/rvc_server.py)."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the real RVC server manager or client")

    monkeypatch.setattr(cli, "RvcServerProcess", refuse)
    monkeypatch.setattr(cli, "RvcClient", refuse)


@pytest.fixture(autouse=True)
def restore_library_log_levels():
    """main() tunes third-party loggers process-wide; give later tests the levels back."""
    loggers = [logging.getLogger(name) for name in ("tinytuya", "httpx")]
    levels = [logger.level for logger in loggers]
    yield
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """Working directory with config.yaml copied from config.example.yaml (paths stay inside)."""
    shutil.copy(ROOT / "config.example.yaml", tmp_path / "config.yaml")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write_config(home: Path, text: str) -> None:
    (home / "config.yaml").write_text(text, encoding="utf-8")


class HouseBrain(Brain):
    """Turns every message into one control_device call on the registry it was built with."""

    def __init__(self, registry: DeviceRegistry) -> None:
        self.registry = registry
        self.heard: list[str] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.heard.append(user_text)
        result = self.registry.execute("lampu_kamar", "on")
        call = ToolCallRecord(
            "control_device", {"device": "lampu_kamar", "command": "on"}, result.message, False
        )
        return BrainReply("Siap, Master! Lampu kamar sudah menyala.", [call])

    def reset(self) -> None:
        self.heard.clear()


class FakeRouter:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        self.texts.append(text)
        return np.ones(240, dtype=np.float32), 24000


class FakePlayer:
    played: list[tuple[int, int]] = []

    def __init__(self, device=None) -> None:
        self.device = device

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        FakePlayer.played.append((len(audio), sample_rate))


@pytest.fixture
def player(monkeypatch) -> type[FakePlayer]:
    FakePlayer.played = []
    monkeypatch.setattr(cli, "Player", FakePlayer)
    return FakePlayer


class FakeMic:
    """Stands in for MicStream: yields its frames, then ends or raises (e.g. Ctrl+C)."""

    def __init__(self, frames: list[np.ndarray], end_with: BaseException | None = None) -> None:
        self._frames = frames
        self._end_with = end_with
        self.entered = False
        self.exited = False

    def __enter__(self) -> "FakeMic":
        self.entered = True
        return self

    def __exit__(self, *exc_info) -> None:
        self.exited = True

    def frames(self):
        yield from self._frames
        if self._end_with is not None:
            raise self._end_with

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


class CountingEmbedder(SpeakerEmbedder):
    """Each call returns a slightly different direction, like real takes of one voice."""

    def __init__(self) -> None:
        self.lengths: list[int] = []

    def embed(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        self.lengths.append(len(audio))
        return np.array([1.0, 0.1 * len(self.lengths), 0.0], dtype=np.float32)


class FixedEmbedder(SpeakerEmbedder):
    def embed(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        return np.array([1.0, 0.0, 0.0], dtype=np.float32)


def fake_module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__spec__ = ModuleSpec(name, loader=None)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


class FakeHttpResponse(io.BytesIO):
    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


@pytest.fixture
def events() -> list[str]:
    """What the fakes of one test did, in order."""
    return []


@pytest.fixture
def speech(events, monkeypatch) -> list["SpeakingRouter"]:
    """Every speech router a command builds, each remembering the voice converter it got."""
    built: list[SpeakingRouter] = []

    def build(tts, *, converter=None):
        built.append(SpeakingRouter(events, converter))
        return built[-1]

    monkeypatch.setattr(cli, "build_speech_router", build)
    return built


@dataclass
class RvcFakes:
    """The anime voice faked: `served_model` answers served_model() (None: no server
    answers), `start_error` fails ensure_running()."""

    events: list[str]
    servers: list["FakeRvcServer"] = field(default_factory=list)
    clients: list["FakeRvcClient"] = field(default_factory=list)
    served_model: str | None = None
    start_error: Exception | None = None
    on_start: Callable[[], None] | None = None


class FakeRvcServer:
    """Stands in for RvcServerProcess: records ensure_running() and stop(), never spawns."""

    def __init__(self, config: RvcConfig, fakes: RvcFakes) -> None:
        self.config = config
        self._fakes = fakes

    def ensure_running(self) -> None:
        self._fakes.events.append("rvc started")
        if self._fakes.on_start is not None:
            self._fakes.on_start()
        if self._fakes.start_error is not None:
            raise self._fakes.start_error

    def stop(self) -> None:
        self._fakes.events.append("rvc stopped")


class FakeRvcClient:
    """Stands in for RvcClient: served_model() answers as told; conversion is the router's."""

    def __init__(self, config: RvcConfig, fakes: RvcFakes) -> None:
        self.config = config
        self.base_url = f"http://{config.host}:{config.port}"
        self._fakes = fakes

    def served_model(self) -> str | None:
        self._fakes.events.append("rvc health")
        return self._fakes.served_model

    def convert(self, audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int]:
        return audio, sample_rate


@pytest.fixture
def rvc(events, monkeypatch) -> RvcFakes:
    fakes = RvcFakes(events)

    def server(config: RvcConfig) -> FakeRvcServer:
        fakes.servers.append(FakeRvcServer(config, fakes))
        return fakes.servers[-1]

    def client(config: RvcConfig) -> FakeRvcClient:
        fakes.clients.append(FakeRvcClient(config, fakes))
        return fakes.clients[-1]

    monkeypatch.setattr(cli, "RvcServerProcess", server)
    monkeypatch.setattr(cli, "RvcClient", client)
    return fakes


RVC_MODEL = Path("Applio/logs/tsukuyomi/tsukuyomi.pth")
RVC_INDEX = Path("Applio/logs/tsukuyomi/tsukuyomi.index")
RVC_PYTHON = Path("Applio/env/Scripts/python.exe")


def enable_rvc(home: Path, *, index: bool = False, enabled: bool = True, extra: str = "") -> None:
    """config.yaml with only the anime voice set up; its paths are relative to `home`."""
    text = (
        f"tts:\n  rvc:\n    enabled: {str(enabled).lower()}\n    applio_dir: Applio\n"
        f"    model: {RVC_MODEL.as_posix()}\n"
    )
    if index:
        text += f"    index: {RVC_INDEX.as_posix()}\n"
    write_config(home, text + extra)


# --- parsing -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["run", "--no-speaker-check"], "run"),
        (["run", "--startup-delay", "15", "--announce"], "run"),
        (["autostart", "status"], "autostart"),
        (["chat", "--speak"], "chat"),
        (["enroll", "--samples", "8", "--seconds", "3"], "enroll"),
        (["speaker-test"], "speaker-test"),
        (["devices"], "devices"),
        (["device", "ac", "temp", "24"], "device"),
        (["learn-ir", "ac", "on"], "learn-ir"),
        (["learn-ir", "ac", "--missing", "--timeout", "30"], "learn-ir"),
        (["say", "Halo", "Master"], "say"),
        (["say", "--raw", "Halo"], "say"),
        (["mics"], "mics"),
        (["check"], "check"),
    ],
)
def test_every_subcommand_in_the_spec_parses(argv, command):
    assert cli.build_parser().parse_args(argv).command == command


def test_global_options_work_before_or_after_the_subcommand():
    before = cli.build_parser().parse_args(["--dry-run", "--config", "a.yaml", "-v", "devices"])
    after = cli.build_parser().parse_args(["devices", "--dry-run", "--config", "a.yaml", "-v"])

    for args in (before, after):
        assert (args.dry_run, args.config, args.verbose) == (True, Path("a.yaml"), True)


def test_defaults_are_config_yaml_real_devices_six_enroll_samples_of_four_seconds():
    args = cli.build_parser().parse_args(["enroll"])

    assert (args.config, args.dry_run, args.verbose) == (Path("config.yaml"), False, False)
    assert (args.samples, args.seconds) == (6, 4.0)


def test_device_value_is_parsed_as_an_integer():
    args = cli.build_parser().parse_args(["device", "ac", "temp", "24"])

    assert (args.device_id, args.device_command, args.value) == ("ac", "temp", 24)


# --- config errors and unexpected failures ---------------------------------------------------


def test_missing_config_prints_the_config_error_and_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert cli.main(["devices"]) == 2

    assert "config.yaml not found" in capsys.readouterr().err


def test_invalid_config_prints_the_validation_message_and_exits_2(home, capsys):
    write_config(home, "assistant:\n  nama: Cyrene\n")

    assert cli.main(["devices"]) == 2

    assert "nama" in capsys.readouterr().err


def test_config_errors_name_the_field_but_never_print_a_secret_value(home, caplog, capsys):
    secret = "S3CRETk3y0123456"
    caplog.set_level(logging.INFO)
    write_config(
        home,
        "devices:\n"
        "  - id: colokan_pc\n"
        "    name: Colokan PC\n"
        "    type: tuya_plug\n"
        "    tuya:\n"
        "      device_id: bf0123456789abcdef\n"
        f'      localkey: "{secret}"\n',
    )

    assert cli.main(["devices"]) == 2
    devices_err = capsys.readouterr().err
    assert cli.main(["check"]) == 1
    check_out = capsys.readouterr().out

    for report in (devices_err, check_out, caplog.text):
        assert "devices.0.tuya.localkey" in report
        assert "devices.0.tuya.local_key" in report
        assert secret not in report


def test_unexpected_error_prints_a_short_indonesian_message_and_exits_1(home, monkeypatch, capsys):
    def explode(config, *, dry_run=False):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "build_registry", explode)

    assert cli.main(["devices"]) == 1

    err = capsys.readouterr().err
    assert "kesalahan" in err
    assert "kaboom" in err
    assert "Traceback" not in err


def test_missing_python_package_names_the_package_and_exits_1(monkeypatch, capsys):
    def no_portaudio():
        raise ModuleNotFoundError("No module named 'sounddevice'", name="sounddevice")

    monkeypatch.setattr(cli, "list_devices", no_portaudio)

    assert cli.main(["mics"]) == 1

    assert "sounddevice" in capsys.readouterr().err


def test_redirected_ansi_output_is_written_as_utf8_so_marks_and_kana_survive(monkeypatch):
    redirected = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", redirected)
    monkeypatch.setattr(cli, "list_devices", lambda: "✓ おかえり")

    assert cli.main(["mics"]) == 0

    redirected.flush()
    assert redirected.buffer.getvalue().decode("utf-8").strip() == "✓ おかえり"


# --- log file ----------------------------------------------------------------------------------


def logging_mics(*messages: str):
    """A `list_devices` stand-in that logs each message the way a waifu module would."""

    def list_devices() -> str:
        for message in messages:
            logging.getLogger("waifu.voice.audio").info(message)
        return "> 1 USB Mic"

    return list_devices


def test_log_file_is_a_global_option_before_or_after_the_subcommand():
    before = cli.build_parser().parse_args(["--log-file", "logs/a.log", "mics"])
    after = cli.build_parser().parse_args(["mics", "--log-file", "logs/a.log"])
    default = cli.build_parser().parse_args(["mics"])

    assert before.log_file == after.log_file == Path("logs/a.log")
    assert default.log_file is None


def test_log_file_gets_dated_utf8_lines_including_japanese_in_a_new_folder(
    tmp_path, monkeypatch, caplog, capsys
):
    log_file = tmp_path / "logs" / "nested" / "waifu.log"
    monkeypatch.setattr(cli, "list_devices", logging_mics("Mic siap: おかえりなさい"))
    caplog.set_level(logging.INFO)

    assert cli.main(["--log-file", str(log_file), "mics"]) == 0

    lines = log_file.read_bytes().decode("utf-8").splitlines()
    assert len(lines) == 1
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO waifu\.voice\.audio: Mic siap: おかえりなさい",
        lines[0],
    )
    assert "> 1 USB Mic" in capsys.readouterr().out


def test_log_file_stops_receiving_logs_when_the_command_ends(tmp_path, monkeypatch, caplog):
    log_file = tmp_path / "waifu.log"
    monkeypatch.setattr(cli, "list_devices", logging_mics("during the command"))
    caplog.set_level(logging.INFO)

    cli.main(["--log-file", str(log_file), "mics"])
    logging.getLogger("waifu.voice.audio").info("after the command")

    text = log_file.read_text(encoding="utf-8")
    assert "during the command" in text
    assert "after the command" not in text


def test_log_file_rotates_near_1_mb_and_keeps_3_old_files(tmp_path, monkeypatch, caplog):
    log_file = tmp_path / "waifu.log"
    big_message = "x" * 600_000  # every message after the first crosses 1 MB and rotates
    monkeypatch.setattr(cli, "list_devices", logging_mics(*[big_message] * 5))
    caplog.set_level(logging.INFO)

    assert cli.main(["--log-file", str(log_file), "mics"]) == 0

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "waifu.log",
        "waifu.log.1",
        "waifu.log.2",
        "waifu.log.3",
    ]


def test_log_file_records_a_config_error_so_a_closed_autostart_window_leaves_a_trace(
    tmp_path, caplog, capsys
):
    log_file = tmp_path / "logs" / "waifu.log"
    missing = tmp_path / "missing.yaml"
    caplog.set_level(logging.INFO)

    assert cli.main(["devices", "--config", str(missing), "--log-file", str(log_file)]) == 2

    logged = log_file.read_text(encoding="utf-8")
    assert "ERROR" in logged and "missing.yaml" in logged
    assert "Konfigurasi bermasalah" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ValueError("No input device matching 'USB Mic'"), "No input device matching 'USB Mic'"),
        (ModuleNotFoundError("No module named 'sounddevice'", name="sounddevice"), "sounddevice"),
    ],
)
def test_log_file_records_a_failure_that_ends_the_command(
    error, reason, tmp_path, monkeypatch, caplog, capsys
):
    log_file = tmp_path / "logs" / "waifu.log"

    def failing_mics():
        raise error

    monkeypatch.setattr(cli, "list_devices", failing_mics)
    caplog.set_level(logging.INFO)

    assert cli.main(["mics", "--log-file", str(log_file)]) == 1

    logged = log_file.read_text(encoding="utf-8")
    assert "ERROR" in logged and reason in logged
    assert "Traceback" not in logged  # the traceback is for -v; the log tail stays readable
    assert reason in capsys.readouterr().err


def test_log_file_gets_the_traceback_of_a_failure_with_verbose(
    tmp_path, monkeypatch, caplog, capsys
):
    log_file = tmp_path / "waifu.log"

    def failing_mics():
        raise ValueError("boom")

    monkeypatch.setattr(cli, "list_devices", failing_mics)
    caplog.set_level(logging.DEBUG)

    assert cli.main(["-v", "mics", "--log-file", str(log_file)]) == 1

    logged = log_file.read_text(encoding="utf-8")
    assert "ERROR" in logged and "Traceback" in logged and "ValueError: boom" in logged


def test_log_file_that_cannot_be_created_is_reported_and_the_command_still_runs(
    tmp_path, monkeypatch, capsys
):
    not_a_folder = tmp_path / "file.txt"
    not_a_folder.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, "list_devices", logging_mics("hello"))

    assert cli.main(["--log-file", str(not_a_folder / "waifu.log"), "mics"]) == 0

    captured = capsys.readouterr()
    assert "tidak bisa" in captured.err and "waifu.log" in captured.err
    assert "> 1 USB Mic" in captured.out


def test_python_dash_m_waifu_runs_the_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["waifu", "--config", "tidak_ada.yaml", "devices"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("waifu", run_name="__main__")

    assert exit_info.value.code == 2


# --- devices and device ----------------------------------------------------------------------


def test_devices_dry_run_lists_id_kind_name_and_commands_of_the_example_config(home, capsys):
    assert cli.main(["devices", "--dry-run"]) == 0

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert any(line.split()[:2] == ["lampu_kamar", "light"] for line in lines)
    ac_line = next(line for line in lines if line.startswith("ac "))
    assert "AC Samsung" in ac_line and "temp_24" in ac_line
    assert "komputer" in out and "shutdown" in out


def test_devices_without_dry_run_shows_unlearned_ir_devices_without_connecting(home, capsys):
    assert cli.main(["devices"]) == 0

    ac_line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("ac "))
    assert "temp_24" not in ac_line
    assert "(belum ada)" in ac_line


def test_device_command_with_value_runs_on_the_dry_run_mock(home, capsys):
    assert cli.main(["device", "ac", "temp", "24", "--dry-run"]) == 0

    assert "AC Samsung: perintah 'temp_24' berhasil (simulasi)." in capsys.readouterr().out


def test_failed_device_command_prints_the_reason_and_exits_1(home, capsys):
    assert cli.main(["device", "ac", "terbang", "--dry-run"]) == 1

    assert "tidak punya perintah 'terbang'" in capsys.readouterr().out


def test_verbose_logging_never_reveals_a_tuya_local_key(home, monkeypatch, caplog, capsys):
    secret = "S3CRETk3y0123456"
    write_config(
        home,
        "devices:\n"
        "  - id: colokan_pc\n"
        "    name: Colokan PC\n"
        "    type: tuya_plug\n"
        "    tuya:\n"
        "      device_id: bf0123456789abcdef\n"
        f'      local_key: "{secret}"\n'
        "      address: 192.0.2.10\n",
    )
    # The reply a real tinytuya device gets when the key or protocol version is wrong.
    monkeypatch.setattr(
        XenonDevice, "_send_receive", lambda self, *args, **kwargs: error_json(ERR_PAYLOAD)
    )
    caplog.set_level(logging.DEBUG)

    assert cli.main(["-v", "device", "colokan_pc", "toggle"]) == 1

    assert "Cek local_key" in capsys.readouterr().out
    assert secret not in caplog.text


# --- chat ------------------------------------------------------------------------------------


@pytest.fixture
def brains(monkeypatch) -> list[HouseBrain]:
    built: list[HouseBrain] = []

    def build(config, registry):
        built.append(HouseBrain(registry))
        return built[-1]

    monkeypatch.setattr(cli, "build_brain", build)
    return built


def test_chat_dry_run_sends_lines_to_the_brain_until_keluar(home, brains, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("nyalakan lampu\n\nkeluar\nini tidak dibaca\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "Kamu: " in out
    assert "Cyrene: Siap, Master! Lampu kamar sudah menyala." in out
    assert "control_device" not in out
    assert brains[0].heard == ["nyalakan lampu"]
    assert brains[0].registry.get("lampu_kamar").calls == [("on", None)]


def test_chat_ends_at_end_of_input_and_shows_tool_calls_when_verbose(
    home, brains, monkeypatch, capsys
):
    monkeypatch.setattr(sys, "stdin", io.StringIO("nyalakan lampu\n"))

    assert cli.main(["chat", "--dry-run", "-v"]) == 0

    out = capsys.readouterr().out
    assert "control_device" in out
    assert '"device": "lampu_kamar"' in out
    assert "berhasil (simulasi)" in out


def test_chat_reads_piped_japanese_as_utf8(home, brains, monkeypatch):
    # What Windows gives Python for a pipe or a mintty terminal: the ANSI code page.
    piped = io.TextIOWrapper(
        io.BytesIO("おはよう\nkeluar\n".encode()), encoding="cp1252", errors="surrogateescape"
    )
    monkeypatch.setattr(sys, "stdin", piped)

    assert cli.main(["chat", "--dry-run"]) == 0

    assert brains[0].heard == ["おはよう"]


def test_chat_exit_word_is_case_insensitive(home, brains, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("EXIT\nnyalakan lampu\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert brains[0].heard == []


def test_chat_speak_voices_each_reply(home, brains, player, monkeypatch):
    router = FakeRouter()
    monkeypatch.setattr(cli, "build_speech_router", lambda tts, converter=None: router)
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\n"))

    assert cli.main(["chat", "--dry-run", "--speak"]) == 0

    assert router.texts == ["Siap, Master! Lampu kamar sudah menyala."]
    assert player.played == [(240, 24000)]


def test_chat_speak_voices_replies_with_the_anime_voice_and_stops_the_server_at_the_end(
    home, brains, player, rvc, speech, events, monkeypatch
):
    enable_rvc(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\nlagi\n"))

    assert cli.main(["chat", "--dry-run", "--speak"]) == 0

    reply = "say Siap, Master! Lampu kamar sudah menyala."
    assert events == ["rvc started", reply, reply, "rvc stopped"]
    assert speech[0].converter is rvc.clients[0]


def test_chat_speak_uses_the_plain_voice_when_the_rvc_server_cannot_start(
    home, brains, player, rvc, speech, monkeypatch, capsys
):
    enable_rvc(home)
    rvc.start_error = RvcError("Server suara RVC berhenti saat mulai (kode 1).")
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\n"))

    assert cli.main(["chat", "--dry-run", "--speak"]) == 0

    assert "Suara anime tidak tersedia: Server suara RVC berhenti" in capsys.readouterr().out
    assert speech[0].converter is None
    assert len(player.played) == 1


def test_chat_without_speak_never_starts_the_rvc_server(home, brains, rvc, speech, monkeypatch):
    enable_rvc(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert rvc.servers == [] and speech == []


# --- learn-ir --------------------------------------------------------------------------------


class FakeBlaster:
    """Replays queued learn results: a base64 code, or an exception such as a timeout."""

    def __init__(self, *results: str | Exception) -> None:
        self.results = list(results)
        self.timeouts: list[float] = []

    def send(self, code_b64: str) -> None:
        pass

    def learn(self, timeout_s: float) -> str:
        self.timeouts.append(timeout_s)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def ir_home(home, monkeypatch):
    """A real registry holding one IR device (on a fake blaster) and one non-IR device."""
    store = IrCodeStore(home / "data" / "ir_codes.json")
    blaster = FakeBlaster()
    ac = IrDevice("ac", "AC Samsung", [], blaster, store, ["on", "off", "temp_24"])
    registry = DeviceRegistry([ac, MockDevice("lampu", "Lampu")])
    built: list[bool] = []

    def build(config, *, dry_run=False):
        built.append(dry_run)
        return registry

    monkeypatch.setattr(cli, "build_registry", build)
    return types.SimpleNamespace(store=store, blaster=blaster, built=built)


def test_learn_ir_is_refused_in_dry_run(ir_home, capsys):
    assert cli.main(["learn-ir", "ac", "on", "--dry-run"]) == 2

    assert "--dry-run" in capsys.readouterr().out
    assert ir_home.built == []


def test_learn_ir_learns_one_command_with_the_given_timeout(ir_home, capsys):
    ir_home.blaster.results = ["Y29kZS1vbg=="]

    assert cli.main(["learn-ir", "ac", "on", "--timeout", "5"]) == 0

    out = capsys.readouterr().out
    assert "Arahkan remote ke blaster lalu tekan tombol on..." in out
    assert ir_home.store.get("ac", "on") == "Y29kZS1vbg=="
    assert ir_home.blaster.timeouts == [5.0]
    assert ir_home.built == [False]


def test_learn_ir_missing_walks_unlearned_commands_and_skips_timeouts(ir_home, capsys):
    ir_home.store.put("ac", "on", "b24=")
    ir_home.blaster.results = ["b2Zm", DeviceError("Tidak ada sinyal IR yang tertangkap.")]

    assert cli.main(["learn-ir", "ac", "--missing"]) == 1

    out = capsys.readouterr().out
    assert "tekan tombol off" in out and "tekan tombol temp_24" in out
    assert "dilewati" in out
    assert ir_home.store.commands("ac") == ["off", "on"]
    assert ir_home.blaster.timeouts == [20.0, 20.0]


def test_learn_ir_missing_with_everything_learned_has_nothing_to_do(ir_home, capsys):
    for command in ("on", "off", "temp_24"):
        ir_home.store.put("ac", command, "Y29kZQ==")

    assert cli.main(["learn-ir", "ac", "--missing"]) == 0

    assert ir_home.blaster.timeouts == []


def test_learn_ir_rejects_a_device_that_is_not_ir(ir_home, capsys):
    assert cli.main(["learn-ir", "lampu", "on"]) == 1

    assert "bukan perangkat IR" in capsys.readouterr().out


def test_learn_ir_rejects_an_unknown_device(ir_home, capsys):
    assert cli.main(["learn-ir", "kulkas", "on"]) == 1

    assert "kulkas" in capsys.readouterr().out


def test_learn_ir_needs_exactly_one_of_a_command_or_missing(ir_home):
    assert cli.main(["learn-ir", "ac"]) == 2
    assert cli.main(["learn-ir", "ac", "on", "--missing"]) == 2
    assert ir_home.blaster.timeouts == []


def test_learn_ir_rejects_an_invalid_command_name(ir_home, capsys):
    assert cli.main(["learn-ir", "ac", "Suhu Naik"]) == 2

    assert "tidak valid" in capsys.readouterr().out


@pytest.fixture
def tv_with_a_bad_name(home, monkeypatch) -> IrCodeStore:
    """An IR device whose config.yaml commands include a name that cannot be learned."""
    store = IrCodeStore(home / "data" / "ir_codes.json")
    tv = IrDevice("tv", "TV", [], FakeBlaster("b24=", "b2Zm"), store, ["volume-up", "on", "off"])
    monkeypatch.setattr(
        cli, "build_registry", lambda config, *, dry_run=False: DeviceRegistry([tv])
    )
    return store


def test_learn_ir_missing_skips_an_invalid_declared_name_and_learns_the_rest(
    tv_with_a_bad_name, capsys
):
    assert cli.main(["learn-ir", "tv", "--missing"]) == 1

    out = capsys.readouterr().out
    assert "✗" in out and "tidak valid" in out and "volume-up" in out
    assert tv_with_a_bad_name.commands("tv") == ["off", "on"]


def test_check_flags_an_invalid_declared_ir_name_instead_of_suggesting_learn_ir(
    tv_with_a_bad_name, nothing_installed, capsys
):
    assert cli.main(["check"]) == 1

    lines = capsys.readouterr().out.splitlines()
    tv_line = next(line for line in lines if line[2:].startswith("TV:"))
    assert tv_line.startswith("✗")
    assert "volume-up" in tv_line and "tidak valid" in tv_line
    assert "--missing" not in tv_line


# --- say and mics ----------------------------------------------------------------------------


def test_say_speaks_the_joined_words_through_the_router_and_player(home, player, monkeypatch):
    router = FakeRouter()
    monkeypatch.setattr(cli, "build_speech_router", lambda tts, converter=None: router)

    assert cli.main(["say", "Halo", "Master,", "おかえりなさい!"]) == 0

    assert router.texts == ["Halo Master, おかえりなさい!"]
    assert player.played == [(240, 24000)]


def test_say_with_the_anime_voice_off_starts_no_rvc_server(home, player, rvc, speech, events):
    assert cli.main(["say", "Halo"]) == 0

    assert rvc.servers == [] and rvc.clients == []
    assert speech[0].converter is None
    assert events == ["say Halo"]


def test_say_converts_with_the_anime_voice_and_stops_the_server_afterwards(
    home, player, rvc, speech, events
):
    enable_rvc(home)

    assert cli.main(["say", "Halo", "Master"]) == 0

    assert events == ["rvc started", "say Halo Master", "rvc stopped"]
    assert speech[0].converter is rvc.clients[0]
    assert rvc.servers[0].config.model == rvc.clients[0].config.model == RVC_MODEL
    assert len(player.played) == 1


def test_say_points_out_a_model_that_is_set_while_the_anime_voice_is_off(
    home, player, rvc, speech, capsys
):
    # Comparing checkpoints with `say` before enabling the anime voice would play the
    # same plain voice for every one of them.
    enable_rvc(home, enabled=False)

    assert cli.main(["say", "Halo"]) == 0

    assert (
        "! Suara anime mati (tts.rvc.enabled: false), jadi tts.rvc.model tidak dipakai."
        in capsys.readouterr().out.splitlines()
    )
    assert rvc.servers == [] and speech[0].converter is None
    assert len(player.played) == 1


def test_say_raw_does_not_point_out_the_anime_voice_being_off(home, player, rvc, speech, capsys):
    enable_rvc(home, enabled=False)

    assert cli.main(["say", "--raw", "Halo"]) == 0

    assert "Suara anime mati" not in capsys.readouterr().out


def test_say_raw_speaks_the_plain_voice_without_starting_the_rvc_server(
    home, player, rvc, speech, events
):
    enable_rvc(home)

    assert cli.main(["say", "--raw", "Halo"]) == 0

    assert rvc.servers == [] and rvc.clients == []
    assert speech[0].converter is None
    assert events == ["say Halo"]


def test_say_uses_the_plain_voice_when_the_rvc_server_cannot_start(
    home, player, rvc, speech, events, caplog, capsys
):
    enable_rvc(home)
    rvc.start_error = RvcError("Model RVC (.pth) tidak ditemukan: x.pth. Periksa tts.rvc.model.")
    caplog.set_level(logging.INFO)

    assert cli.main(["say", "Halo"]) == 0

    assert (
        "! Suara anime tidak tersedia: Model RVC (.pth) tidak ditemukan: x.pth. "
        "Periksa tts.rvc.model. Cyrene memakai suara biasa." in capsys.readouterr().out.splitlines()
    )
    assert "x.pth" in caplog.text  # the autostart window closes; the log keeps the reason
    assert speech[0].converter is None and rvc.clients == []
    assert len(player.played) == 1


def test_say_uses_the_plain_voice_when_the_rvc_server_log_cannot_be_opened(
    home, player, rvc, speech, capsys
):
    enable_rvc(home)
    rvc.start_error = PermissionError(13, "Permission denied", "logs\\rvc_server.log")

    assert cli.main(["say", "Halo"]) == 0

    assert "Suara anime tidak tersedia" in capsys.readouterr().out
    assert speech[0].converter is None
    assert len(player.played) == 1


def test_say_stops_the_rvc_server_even_when_playback_fails(home, rvc, speech, events, monkeypatch):
    class UnpluggedPlayer:
        def __init__(self, device=None) -> None:
            pass

        def play(self, audio: np.ndarray, sample_rate: int) -> None:
            raise RuntimeError("speaker unplugged")

    monkeypatch.setattr(cli, "Player", UnpluggedPlayer)
    enable_rvc(home)

    assert cli.main(["say", "Halo"]) == 1

    assert events[-1] == "rvc stopped"


def test_mics_prints_the_audio_device_list(monkeypatch, capsys):
    monkeypatch.setattr(cli, "list_devices", lambda: "> 1 USB Mic")

    assert cli.main(["mics"]) == 0

    assert "> 1 USB Mic" in capsys.readouterr().out


# --- run -------------------------------------------------------------------------------------


class RecordingAssistant:
    instances: list["RecordingAssistant"] = []

    def __init__(self, config, **collaborators) -> None:
        self.collaborators = collaborators
        self.ran_with: tuple | None = None
        RecordingAssistant.instances.append(self)

    def run(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)


class PreloadingStt(SpeechToText):
    """Stands in for WhisperStt: hears nothing, records when it is built and preloaded."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        events.append("whisper built")

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        return Transcript("", "id")

    def preload(self) -> None:
        self.events.append("whisper loaded")


class PreloadingEmbedder(FixedEmbedder):
    def __init__(self, events: list[str]) -> None:
        self.events = events
        events.append("speaker model built")

    def preload(self) -> None:
        self.events.append("speaker model loaded")


class EventMic(FakeMic):
    """A silent FakeMic that records when it is opened, paused, resumed, read, and closed."""

    def __init__(self, events: list[str]) -> None:
        super().__init__([])
        self.events = events

    def __enter__(self) -> "EventMic":
        self.events.append("mic opened")
        super().__enter__()
        return self

    def __exit__(self, *exc_info) -> None:
        self.events.append("mic closed")
        super().__exit__(*exc_info)

    def frames(self):
        self.events.append("listening")
        yield from super().frames()

    def pause(self) -> None:
        self.events.append("mic paused")

    def resume(self) -> None:
        self.events.append("mic resumed")


class SpeakingRouter(FakeRouter):
    def __init__(self, events: list[str], converter=None) -> None:
        super().__init__()
        self.events = events
        self.converter = converter

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        self.events.append(f"say {text}")
        return super().synthesize(text)


def lock_is_free(path: Path) -> bool:
    probe = InstanceLock(path)
    acquired = probe.acquire()
    probe.release()
    return acquired


@pytest.fixture
def enrolled(home) -> Path:
    voiceprint = home / "data" / "voiceprint.npy"
    SpeakerVerifier(FixedEmbedder(), voiceprint, 0.3).enroll([LOUD_FRAME] * 3, SAMPLE_RATE)
    return voiceprint


@pytest.fixture
def run_fakes(home, brains, player, events, speech, monkeypatch):
    """`waifu run` with every model, the mic, and the speech engine faked; `events` shows order."""
    RecordingAssistant.instances = []
    mics: list[FakeMic] = []

    def open_mic(sample_rate, frame_ms, device=None):
        mics.append(EventMic(events))
        return mics[-1]

    monkeypatch.setattr(cli, "Assistant", RecordingAssistant)
    monkeypatch.setattr(cli, "MicStream", open_mic)
    monkeypatch.setattr(cli, "EcapaEmbedder", lambda *args, **kwargs: PreloadingEmbedder(events))
    monkeypatch.setattr(cli, "WhisperStt", lambda config, name: PreloadingStt(events))
    return types.SimpleNamespace(mics=mics, events=events, lock=home / "data" / "run.lock")


def test_run_without_a_voiceprint_explains_enroll_and_exits_2(run_fakes, capsys):
    assert cli.main(["run", "--dry-run"]) == 2

    assert "waifu enroll" in capsys.readouterr().out
    assert RecordingAssistant.instances == []
    assert run_fakes.mics == []


def test_run_with_a_voiceprint_listens_with_speaker_verification(home, run_fakes, capsys):
    SpeakerVerifier(FixedEmbedder(), home / "data" / "voiceprint.npy", 0.3).enroll(
        [LOUD_FRAME] * 3, SAMPLE_RATE
    )

    assert cli.main(["run", "--dry-run"]) == 0

    assistant = RecordingAssistant.instances[0]
    assert isinstance(assistant.collaborators["verifier"], SpeakerVerifier)
    assert assistant.collaborators["speaker_check"] is True
    mic, segmenter = assistant.ran_with
    assert mic is run_fakes.mics[0] and mic.entered and mic.exited
    assert isinstance(segmenter, cli.EnergySegmenter)
    out = capsys.readouterr().out
    assert "Cyrene siap mendengarkan" in out and "sairin" in out


def test_run_no_speaker_check_skips_the_voiceprint(run_fakes):
    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assistant = RecordingAssistant.instances[0]
    assert assistant.collaborators["verifier"] is None
    assert assistant.collaborators["speaker_check"] is False
    assert assistant.ran_with is not None


def test_run_starts_at_once_and_quietly_by_default():
    args = cli.build_parser().parse_args(["run"])

    assert (args.startup_delay, args.announce) == (0.0, False)


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "nanti"])
def test_run_rejects_a_startup_delay_that_is_not_a_finite_non_negative_number(value, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["run", "--startup-delay", value])

    assert exit_info.value.code == 2
    assert "argument --startup-delay: harus angka 0 atau lebih" in capsys.readouterr().err


def test_run_refuses_while_another_instance_holds_the_lock_and_loads_nothing(
    run_fakes, brains, capsys
):
    with InstanceLock(run_fakes.lock):
        assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 2

    assert capsys.readouterr().out.strip() == (
        "Cyrene sudah berjalan (mungkin dari autostart). Tutup jendelanya dulu, "
        "atau jalankan 'waifu autostart status'."
    )
    assert run_fakes.events == []
    assert brains == [] and RecordingAssistant.instances == []


def test_run_refusal_reaches_the_log_file_of_the_window_that_closes(home, run_fakes, caplog):
    log_file = home / "logs" / "waifu.log"
    caplog.set_level(logging.INFO)

    with InstanceLock(run_fakes.lock):
        assert cli.main(["run", "--log-file", str(log_file)]) == 2

    warning = next(
        line for line in log_file.read_text(encoding="utf-8").splitlines() if "WARNING" in line
    )
    assert "run.lock" in warning


def test_run_holds_the_lock_while_listening_and_frees_it_afterwards(run_fakes, monkeypatch):
    free_while_listening: list[bool] = []

    def listen(self, mic, segmenter) -> None:
        free_while_listening.append(lock_is_free(run_fakes.lock))

    monkeypatch.setattr(RecordingAssistant, "run", listen)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert free_while_listening == [False]
    assert lock_is_free(run_fakes.lock)


def test_run_frees_the_lock_when_it_stops_early(run_fakes):
    assert cli.main(["run", "--dry-run"]) == 2  # no voiceprint

    assert lock_is_free(run_fakes.lock)


def test_run_waits_the_startup_delay_holding_the_lock_before_loading_anything(
    run_fakes, monkeypatch
):
    waits: list[tuple[float, bool, list[str]]] = []

    def wait(seconds: float) -> None:
        waits.append((seconds, lock_is_free(run_fakes.lock), list(run_fakes.events)))

    monkeypatch.setattr(cli, "sleep", wait)

    assert cli.main(["run", "--dry-run", "--no-speaker-check", "--startup-delay", "2.5"]) == 0

    assert waits == [(2.5, False, [])]
    assert "whisper loaded" in run_fakes.events


def test_run_starts_without_waiting_by_default(run_fakes, monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(cli, "sleep", waits.append)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert waits == []


def test_ctrl_c_during_the_startup_delay_stops_cleanly_and_frees_the_lock(
    run_fakes, monkeypatch, capsys
):
    def interrupted(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "sleep", interrupted)

    assert cli.main(["run", "--dry-run", "--no-speaker-check", "--startup-delay", "15"]) == 130

    assert "Dihentikan" in capsys.readouterr().out
    assert run_fakes.events == []
    assert lock_is_free(run_fakes.lock)


def test_run_loads_the_speaker_model_and_whisper_once_before_opening_the_mic(enrolled, run_fakes):
    assert cli.main(["run", "--dry-run"]) == 0

    events = run_fakes.events
    assert events.count("speaker model loaded") == events.count("whisper loaded") == 1
    opened = events.index("mic opened")
    assert events.index("speaker model loaded") < opened
    assert events.index("whisper loaded") < opened


def test_run_without_speaker_check_loads_only_whisper(run_fakes):
    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert "whisper loaded" in run_fakes.events
    assert "speaker model built" not in run_fakes.events


def test_run_with_an_unreadable_voiceprint_stops_before_opening_the_mic(home, run_fakes, capsys):
    (home / "data").mkdir()
    (home / "data" / "voiceprint.npy").write_bytes(b"bukan voiceprint")

    assert cli.main(["run", "--dry-run"]) == 1

    out = capsys.readouterr().out
    assert "✗" in out and "waifu enroll" in out
    assert run_fakes.mics == []


@pytest.fixture
def announcing(run_fakes, enrolled, monkeypatch):
    """`waifu run` with the real Assistant, so speaking really pauses the (fake) mic."""
    monkeypatch.setattr(cli, "Assistant", Assistant)
    return run_fakes


def test_run_announce_says_she_is_ready_once_after_loading_with_the_mic_paused(
    announcing, player, capsys
):
    assert cli.main(["run", "--dry-run", "--announce"]) == 0

    events = announcing.events
    assert events[events.index("mic opened") :] == [
        "mic opened",
        "mic paused",
        "say Cyrene siap, Master.",
        "mic resumed",
        "listening",
        "mic closed",
    ]
    assert events.index("whisper loaded") < events.index("say Cyrene siap, Master.")
    assert events.index("speaker model loaded") < events.index("say Cyrene siap, Master.")
    assert len(player.played) == 1
    assert "Cyrene siap mendengarkan" in capsys.readouterr().out


def test_run_announce_uses_the_configured_name(announcing, home):
    write_config(home, "assistant:\n  name: Aiko\n  wake_words: [aiko]\n")

    assert cli.main(["run", "--dry-run", "--announce"]) == 0

    assert "say Aiko siap, Master." in announcing.events


class HearingStt(PreloadingStt):
    """Transcribes every utterance as the same text."""

    def __init__(self, text: str) -> None:
        super().__init__([])
        self.text = text

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        return Transcript(self.text, "id")


class SilentRouter(FakeRouter):
    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        raise TtsError("no network")


@pytest.mark.parametrize("router", [FakeRouter, SilentRouter], ids=["spoken", "tts_failed"])
def test_run_announce_does_not_open_the_followup_window(router, announcing, brains, monkeypatch):
    # Right after "Cyrene siap, Master." the owner says something not meant for her.
    frames = [SILENT_FRAME] * 10 + [LOUD_FRAME] * 40 + [SILENT_FRAME] * 40
    monkeypatch.setattr(cli, "MicStream", lambda *args, **kwargs: FakeMic(frames))
    monkeypatch.setattr(cli, "WhisperStt", lambda config, name: HearingStt("matikan semua lampu"))
    monkeypatch.setattr(cli, "build_speech_router", lambda tts, converter=None: router())

    assert cli.main(["run", "--dry-run", "--announce"]) == 0

    assert brains[0].heard == []


def test_run_announce_still_answers_the_wake_word(announcing, brains, monkeypatch):
    frames = [SILENT_FRAME] * 10 + [LOUD_FRAME] * 40 + [SILENT_FRAME] * 40
    monkeypatch.setattr(cli, "MicStream", lambda *args, **kwargs: FakeMic(frames))
    monkeypatch.setattr(
        cli, "WhisperStt", lambda config, name: HearingStt("Cyrene, matikan semua lampu")
    )

    assert cli.main(["run", "--dry-run", "--announce"]) == 0

    assert brains[0].heard == ["matikan semua lampu"]


def test_run_without_announce_stays_quiet_until_spoken_to(announcing, player):
    assert cli.main(["run", "--dry-run"]) == 0

    assert not any(event.startswith("say ") for event in announcing.events)
    assert player.played == []


def test_run_starts_the_anime_voice_holding_the_lock_after_the_models_and_before_ready(
    announcing, rvc, speech, home, capsys
):
    enable_rvc(home)
    at_start: list[tuple[bool, str]] = []
    rvc.on_start = lambda: at_start.append((lock_is_free(announcing.lock), capsys.readouterr().out))

    assert cli.main(["run", "--dry-run", "--announce"]) == 0

    ((lock_free, printed_before),) = at_start
    assert not lock_free
    assert "Memuat model suara" in printed_before and "siap" not in printed_before
    assert "Cyrene siap mendengarkan" in capsys.readouterr().out
    events = announcing.events
    assert events.index("speaker model loaded") < events.index("rvc started")
    assert events.index("whisper loaded") < events.index("rvc started")
    assert events.index("rvc started") < events.index("mic opened")
    assert events.index("rvc started") < events.index("say Cyrene siap, Master.")
    assert events[-2:] == ["mic closed", "rvc stopped"]
    assert speech[0].converter is rvc.clients[0]


def test_run_listens_with_the_plain_voice_when_the_rvc_server_cannot_start(
    run_fakes, rvc, speech, home, capsys
):
    enable_rvc(home)
    rvc.start_error = RvcError("Server suara RVC belum siap setelah 180 detik.")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    out = capsys.readouterr().out
    warning = out.index("Suara anime tidak tersedia: Server suara RVC belum siap")
    assert warning < out.index("Cyrene siap mendengarkan")
    assert speech[0].converter is None and rvc.clients == []
    assert RecordingAssistant.instances[0].ran_with is not None


def test_run_with_the_anime_voice_off_starts_no_rvc_server(run_fakes, rvc, speech):
    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert rvc.servers == []
    assert speech[0].converter is None


def test_run_stops_the_rvc_server_after_ctrl_c(run_fakes, rvc, home, monkeypatch):
    def interrupted(self, mic, segmenter) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(RecordingAssistant, "run", interrupted)
    enable_rvc(home)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 130

    assert run_fakes.events[-2:] == ["mic closed", "rvc stopped"]


def test_run_starts_no_rvc_server_when_the_voice_models_fail_to_load(run_fakes, rvc, home):
    enable_rvc(home)
    (home / "data").mkdir()
    (home / "data" / "voiceprint.npy").write_bytes(b"bukan voiceprint")

    assert cli.main(["run", "--dry-run"]) == 1

    assert rvc.servers == []


def test_run_refused_by_the_lock_starts_no_rvc_server(run_fakes, rvc, home):
    enable_rvc(home)

    with InstanceLock(run_fakes.lock):
        assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 2

    assert rvc.servers == []


@pytest.fixture
def one_utterance(home, brains, player, monkeypatch):
    """`waifu run` with the real Assistant and Whisper wrapper, hearing one spoken phrase."""
    frames = [SILENT_FRAME] * 10 + [LOUD_FRAME] * 40 + [SILENT_FRAME] * 40
    monkeypatch.setattr(cli, "MicStream", lambda *args, **kwargs: FakeMic(frames))
    monkeypatch.setattr(cli, "build_speech_router", lambda tts, converter=None: FakeRouter())
    monkeypatch.setattr(
        cli, "WhisperStt", lambda config, name: WhisperStt(config, name, register_dlls=list)
    )
    return brains


def test_run_without_the_voice_packages_stops_and_names_the_missing_one(
    one_utterance, monkeypatch, capsys
):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 1

    assert "Paket Python 'faster_whisper' belum terpasang" in capsys.readouterr().err
    assert one_utterance[0].heard == []


def test_run_stops_with_the_cuda_hint_when_whisper_cannot_use_the_gpu(
    one_utterance, monkeypatch, capsys
):
    def missing_cublas(*args, **kwargs):
        raise OSError("Could not load library cublas64_12.dll. Error code 126")

    monkeypatch.setitem(
        sys.modules, "faster_whisper", fake_module("faster_whisper", WhisperModel=missing_cublas)
    )

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 1

    out = capsys.readouterr().out
    assert "✗ Whisper gagal berjalan di GPU: cublas64_12.dll" in out
    assert "cu128" in out


# --- autostart -------------------------------------------------------------------------------


class FakeRunKey:
    """The per-user Run key held in a dict, with someone else's entry that must survive."""

    def __init__(self) -> None:
        self.values = {"OneDrive": r'"C:\OneDrive.exe" /background'}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, command: str) -> None:
        self.values[name] = command

    def delete(self, name: str) -> bool:
        return self.values.pop(name, None) is not None


@pytest.fixture(autouse=True)
def no_real_run_key(monkeypatch):
    """Even reading the real Run key would make a test depend on this PC's login setup."""

    def refuse():
        raise AssertionError("a test reached the real Windows Run key")

    monkeypatch.setattr(cli, "RUN_KEY_FACTORY", refuse)


@pytest.fixture
def run_key(home, monkeypatch) -> FakeRunKey:
    """A fake Run key; `home` keeps data/run.lock and logs/ inside tmp_path."""
    key = FakeRunKey()
    monkeypatch.setattr(cli, "RUN_KEY_FACTORY", lambda: key)
    return key


@pytest.fixture
def waifu_exe(home, monkeypatch) -> Path:
    exe = home / "venv" / "Scripts" / "waifu.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(cli, "installed_waifu_exe", lambda: exe)
    return exe


def login_command(home: Path, waifu_exe: Path, delay_s: int = DEFAULT_DELAY_S) -> str:
    return build_command(home.resolve(), waifu_exe, delay_s=delay_s)


@pytest.mark.parametrize(
    "argv",
    [["autostart", "enable", "--delay", "30", "--force"], ["autostart", "disable"]],
)
def test_autostart_actions_parse(argv):
    assert cli.build_parser().parse_args(argv).action == argv[1]


def test_autostart_enable_waits_the_default_delay_and_does_not_force_by_default():
    args = cli.build_parser().parse_args(["autostart", "enable"])

    assert (args.delay, args.force) == (DEFAULT_DELAY_S, False)


def test_autostart_needs_an_action():
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(["autostart"])

    assert exit_info.value.code == 2


@pytest.mark.parametrize("value", ["-1", "1.5", "lama"])
def test_autostart_enable_rejects_a_delay_that_is_not_a_whole_non_negative_number(value, capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["autostart", "enable", "--delay", value])

    assert "argument --delay: harus bilangan bulat 0 atau lebih" in capsys.readouterr().err


def test_autostart_help_says_dry_run_does_not_change_what_is_registered(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "300")
    monkeypatch.setenv("PYTHON_COLORS", "0")

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["autostart", "--help"])

    assert exit_info.value.code == 0
    assert "--dry-run tidak berpengaruh" in capsys.readouterr().out


def test_installed_waifu_exe_is_the_launcher_in_this_environment_s_scripts_folder():
    assert cli.installed_waifu_exe() == Path(sys.prefix) / "Scripts" / "waifu.exe"


def test_autostart_enable_registers_the_login_command_and_explains_stop_and_undo(
    enrolled, run_key, waifu_exe, home, capsys
):
    assert cli.main(["autostart", "enable"]) == 0

    command = login_command(home, waifu_exe)
    assert run_key.values == {"OneDrive": r'"C:\OneDrive.exe" /background', "Cyrene": command}
    out = capsys.readouterr().out
    assert command in out
    assert 'jendela "Cyrene"' in out and "minimize" in out
    assert "tutup jendela" in out
    assert "waifu autostart disable" in out
    # Task Manager names a Run entry after its program (cmd.exe), not after the value.
    task_manager = next(line for line in out.splitlines() if "Task Manager" in line)
    assert '"Windows Command Processor"' in task_manager


def test_autostart_enable_uses_the_given_delay(enrolled, run_key, waifu_exe, home):
    assert cli.main(["autostart", "enable", "--delay", "30"]) == 0

    assert run_key.values["Cyrene"] == login_command(home, waifu_exe, delay_s=30)


def test_autostart_enable_with_dry_run_registers_the_same_real_command(
    enrolled, run_key, waifu_exe, home, capsys
):
    assert cli.main(["autostart", "enable", "--dry-run"]) == 0

    assert run_key.values["Cyrene"] == login_command(home, waifu_exe)
    assert "--dry-run" in capsys.readouterr().out


def test_autostart_enable_without_a_voiceprint_refuses_and_registers_nothing(
    run_key, waifu_exe, capsys
):
    assert cli.main(["autostart", "enable"]) == 2

    out = capsys.readouterr().out
    assert "Jalankan 'waifu enroll' dulu" in out and "--force" in out
    assert "Cyrene" not in run_key.values


def test_autostart_enable_without_config_yaml_refuses(enrolled, run_key, waifu_exe, home, capsys):
    (home / "config.yaml").unlink()

    assert cli.main(["autostart", "enable"]) == 2

    out = capsys.readouterr().out
    assert "config.yaml" in out and "--force" in out
    assert "Cyrene" not in run_key.values


def test_autostart_enable_with_an_invalid_config_refuses(
    enrolled, run_key, waifu_exe, home, capsys
):
    write_config(home, "brain:\n  backend: gpt\n")

    assert cli.main(["autostart", "enable"]) == 2

    assert "brain.backend" in capsys.readouterr().out
    assert "Cyrene" not in run_key.values


def test_autostart_enable_force_registers_without_config_or_voiceprint(run_key, waifu_exe, home):
    (home / "config.yaml").unlink()

    assert cli.main(["autostart", "enable", "--force"]) == 0

    assert run_key.values["Cyrene"] == login_command(home, waifu_exe)


def test_autostart_enable_needs_no_voiceprint_when_speaker_check_is_off(run_key, waifu_exe, home):
    write_config(home, "speaker:\n  enabled: false\n")

    assert cli.main(["autostart", "enable"]) == 0

    assert "Cyrene" in run_key.values


def test_autostart_enable_without_waifu_exe_fails_even_with_force(
    enrolled, run_key, home, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "installed_waifu_exe", lambda: home / "hilang" / "waifu.exe")

    assert cli.main(["autostart", "enable", "--force"]) == 1

    assert "waifu.exe" in capsys.readouterr().out
    assert "Cyrene" not in run_key.values


def test_autostart_enable_refuses_a_project_folder_that_cmd_would_rewrite(
    run_key, waifu_exe, tmp_path, monkeypatch, capsys
):
    project = tmp_path / "proyek 100%"
    project.mkdir()
    monkeypatch.chdir(project)

    assert cli.main(["autostart", "enable", "--force"]) == 1

    assert "%" in capsys.readouterr().out
    assert "Cyrene" not in run_key.values


def test_autostart_disable_removes_only_cyrene_and_says_so(run_key, capsys):
    run_key.values["Cyrene"] = "the command"

    assert cli.main(["autostart", "disable"]) == 0

    assert run_key.values == {"OneDrive": r'"C:\OneDrive.exe" /background'}
    assert "dimatikan" in capsys.readouterr().out


def test_autostart_disable_when_already_off_says_nothing_was_removed(run_key, capsys):
    assert cli.main(["autostart", "disable"]) == 0

    assert "tidak ada yang dihapus" in capsys.readouterr().out


def test_autostart_status_when_enabled_for_this_project(run_key, waifu_exe, home, capsys):
    command = login_command(home, waifu_exe)
    run_key.values["Cyrene"] = command

    assert cli.main(["autostart", "status"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "Autostart: aktif" in lines
    assert any(command in line for line in lines)
    assert any(line.startswith("✓") and "sesuai" in line for line in lines)
    assert any("tidak sedang berjalan" in line for line in lines)
    log_line = next(line for line in lines if line.startswith("Log:"))
    assert str(home.resolve() / "logs" / "waifu.log") in log_line and "belum ada" in log_line


def test_autostart_status_keeps_a_custom_delay_from_looking_stale(run_key, waifu_exe, home, capsys):
    run_key.values["Cyrene"] = login_command(home, waifu_exe, delay_s=45)

    cli.main(["autostart", "status"])

    assert any(
        line.startswith("✓") and "sesuai" in line for line in capsys.readouterr().out.splitlines()
    )


def test_autostart_status_warns_when_the_project_moved(run_key, waifu_exe, home, capsys):
    old_project = PureWindowsPath(r"D:\lama\waifu")
    run_key.values["Cyrene"] = build_command(old_project, old_project / "waifu.exe")

    assert cli.main(["autostart", "status"]) == 0

    lines = capsys.readouterr().out.splitlines()
    warning = next(line for line in lines if line.startswith("!"))
    assert "waifu autostart enable" in warning
    assert any(login_command(home, waifu_exe) in line for line in lines)


def test_autostart_status_from_another_folder_reports_the_registered_project(
    run_key, waifu_exe, home, monkeypatch, capsys
):
    run_key.values["Cyrene"] = login_command(home, waifu_exe)
    log_file = home / "logs" / "waifu.log"
    log_file.parent.mkdir()
    log_file.write_text("Cyrene is listening\n", encoding="utf-8")
    elsewhere = home / "dokumen"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    with InstanceLock(home / "data" / "run.lock"):
        assert cli.main(["autostart", "status"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "Cyrene sedang berjalan." in lines
    assert any(line.startswith("Log:") and str(log_file) in line for line in lines)
    assert "  Cyrene is listening" in lines
    hint = next(line for line in lines if line.startswith("!"))
    assert "folder proyek" in hint and str(home) in hint
    assert "dipindah" not in hint


@pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive paths are a Windows trait")
def test_autostart_status_matches_from_a_differently_cased_working_directory(
    run_key, waifu_exe, home, monkeypatch, capsys
):
    run_key.values["Cyrene"] = login_command(home, waifu_exe)
    monkeypatch.chdir(str(home).swapcase())

    assert cli.main(["autostart", "status"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("✓") and "sesuai" in line for line in lines)


def test_autostart_status_when_disabled(run_key, waifu_exe, capsys):
    assert cli.main(["autostart", "status"]) == 0

    out = capsys.readouterr().out
    assert "Autostart: mati" in out and "waifu autostart enable" in out


def test_autostart_status_sees_a_running_instance_by_its_lock(run_key, waifu_exe, home, capsys):
    lock_path = home / "data" / "run.lock"
    with InstanceLock(lock_path):
        assert cli.main(["autostart", "status"]) == 0
        still_held = not lock_is_free(lock_path)

    lines = capsys.readouterr().out.splitlines()
    assert any("sedang berjalan" in line for line in lines)
    assert not any("tidak sedang berjalan" in line for line in lines)
    assert still_held


def test_autostart_status_leaves_no_lock_file_behind(run_key, waifu_exe, home):
    assert cli.main(["autostart", "status"]) == 0

    assert not (home / "data").exists()


def test_autostart_status_shows_the_log_path_and_its_last_five_lines(
    run_key, waifu_exe, home, capsys
):
    log_file = home / "logs" / "waifu.log"
    log_file.parent.mkdir()
    lines = [f"baris {number}" for number in range(1, 7)] + ["おかえりなさい, Master"]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert cli.main(["autostart", "status"]) == 0

    out = capsys.readouterr().out
    assert str(log_file.resolve()) in out
    for shown in lines[-5:]:
        assert shown in out
    assert "baris 1" not in out and "baris 2" not in out


# --- enroll and speaker-test -----------------------------------------------------------------


def test_enroll_records_each_phrase_saves_a_voiceprint_and_suggests_a_threshold(
    home, monkeypatch, capsys
):
    embedder = CountingEmbedder()
    opened: list[FakeMic] = []

    def open_mic(sample_rate, frame_ms, device=None):
        opened.append(FakeMic([LOUD_FRAME] * 100))
        return opened[-1]

    monkeypatch.setattr(cli, "MicStream", open_mic)
    monkeypatch.setattr(cli, "EcapaEmbedder", lambda *args, **kwargs: embedder)
    monkeypatch.setattr(cli, "sleep", lambda seconds: None)

    assert cli.main(["enroll", "--samples", "3", "--seconds", "0.5"]) == 0

    assert len(opened) == 3 and all(mic.exited for mic in opened)
    assert embedder.lengths[:3] == [8000, 8000, 8000]
    assert (home / "data" / "voiceprint.npy").exists()
    out = capsys.readouterr().out
    assert out.count("Bacakan:") == 3
    assert "Rekaman 3:" in out
    assert "Saran threshold" in out


@pytest.fixture
def recording_mics(monkeypatch) -> list[FakeMic]:
    """Every MicStream a command opens, each hearing the owner speak."""
    opened: list[FakeMic] = []

    def open_mic(sample_rate, frame_ms, device=None):
        opened.append(FakeMic([LOUD_FRAME] * 100))
        return opened[-1]

    monkeypatch.setattr(cli, "MicStream", open_mic)
    monkeypatch.setattr(cli, "EcapaEmbedder", lambda *args, **kwargs: FixedEmbedder())
    monkeypatch.setattr(cli, "sleep", lambda seconds: None)
    return opened


def test_enroll_refuses_while_cyrene_is_running_and_records_nothing(home, recording_mics, capsys):
    # A running Cyrene would hear the phrases (several start with her name) and obey them.
    with InstanceLock(home / "data" / "run.lock"):
        assert cli.main(["enroll", "--samples", "3", "--seconds", "0.5"]) == 2

    assert "Cyrene sudah berjalan" in capsys.readouterr().out
    assert recording_mics == []
    assert not (home / "data" / "voiceprint.npy").exists()


def test_enroll_holds_the_run_lock_while_recording(home, recording_mics, monkeypatch):
    free_while_recording: list[bool] = []
    lock = home / "data" / "run.lock"
    monkeypatch.setattr(
        cli, "sleep", lambda seconds: free_while_recording.append(lock_is_free(lock))
    )

    assert cli.main(["enroll", "--samples", "3", "--seconds", "0.5"]) == 0

    assert free_while_recording and not any(free_while_recording)
    assert lock_is_free(lock)


def test_speaker_test_refuses_while_cyrene_is_running(enrolled, home, recording_mics, capsys):
    with InstanceLock(home / "data" / "run.lock"):
        assert cli.main(["speaker-test"]) == 2

    assert "Cyrene sudah berjalan" in capsys.readouterr().out
    assert recording_mics == []


def test_enroll_refuses_fewer_samples_than_a_voiceprint_needs(home, capsys):
    assert cli.main(["enroll", "--samples", "2"]) == 2

    assert not (home / "data" / "voiceprint.npy").exists()


def test_speaker_test_prints_each_utterance_score_until_ctrl_c(home, monkeypatch, capsys):
    voiceprint = home / "data" / "voiceprint.npy"
    SpeakerVerifier(FixedEmbedder(), voiceprint, 0.3).enroll([LOUD_FRAME] * 3, SAMPLE_RATE)
    frames = [SILENT_FRAME] * 10 + [LOUD_FRAME] * 40 + [SILENT_FRAME] * 40
    mic = FakeMic(frames, end_with=KeyboardInterrupt())
    monkeypatch.setattr(cli, "MicStream", lambda *args, **kwargs: mic)
    monkeypatch.setattr(cli, "EcapaEmbedder", lambda *args, **kwargs: FixedEmbedder())

    assert cli.main(["speaker-test"]) == 0

    out = capsys.readouterr().out
    assert "✓" in out and "1.00" in out
    assert mic.exited


def test_speaker_test_without_a_voiceprint_explains_enroll(home, monkeypatch, capsys):
    monkeypatch.setattr(cli, "EcapaEmbedder", lambda *args, **kwargs: FixedEmbedder())

    assert cli.main(["speaker-test"]) == 2

    assert "waifu enroll" in capsys.readouterr().out


# --- check -----------------------------------------------------------------------------------


@pytest.fixture
def nothing_installed(monkeypatch):
    """Voice extra, torch, and PortAudio absent; Ollama not running; no Claude key."""
    for name in VOICE_MODULES:
        monkeypatch.setitem(sys.modules, name, None)

    def refuse(url, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(cli, "register_cuda_dll_dirs", list)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


class BrokenInstall:
    """Import hook for a package that is installed but fails while importing (e.g. a DLL)."""

    def __init__(self, name: str, error: Exception, events: list[str]) -> None:
        self.name = name
        self.error = error
        self.events = events

    def find_spec(self, fullname, path=None, target=None) -> ModuleSpec | None:
        return ModuleSpec(fullname, self) if fullname == self.name else None

    def create_module(self, spec) -> None:
        return None

    def exec_module(self, module) -> None:
        self.events.append(f"import {self.name}")
        raise self.error


def test_check_reports_whisper_that_is_installed_but_cannot_load(
    home, nothing_installed, monkeypatch, capsys
):
    events: list[str] = []
    cublas = OSError("Could not load library cublas64_12.dll. Error code 126")
    monkeypatch.delitem(sys.modules, "faster_whisper")
    monkeypatch.setattr(sys, "meta_path", [BrokenInstall("faster_whisper", cublas, events)])
    monkeypatch.setattr(cli, "register_cuda_dll_dirs", lambda: events.append("register dlls"))

    assert cli.main(["check"]) == 1

    whisper = next(line for line in capsys.readouterr().out.splitlines() if "Whisper" in line)
    assert whisper.startswith("✗ Whisper (faster_whisper) gagal dimuat")
    assert "cublas64_12.dll" in whisper and "cu128" in whisper
    assert events == ["register dlls", "import faster_whisper"]


def test_check_with_nothing_installed_reports_failures_without_crashing(
    home, nothing_installed, capsys
):
    assert cli.main(["check"]) == 1

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("✓")
    assert any(line.startswith("✓") and "5 perangkat" in line for line in lines)
    assert any(
        line.startswith("!") and "AC Samsung" in line and "temp_24" in line for line in lines
    )
    assert any(line.startswith("✗") and "waifu enroll" in line for line in lines)
    assert any(line.startswith("✗") and "Ollama" in line for line in lines)
    for package in ("faster_whisper", "speechbrain", "torch"):
        assert any(line.startswith("✗") and package in line for line in lines)
    assert any(line.startswith("✗") and "Mikrofon" in line for line in lines)
    assert not any("CUDA" in line for line in lines)


def test_check_counts_configured_devices_the_same_in_dry_run(home, nothing_installed, capsys):
    cli.main(["check"])
    real = capsys.readouterr().out
    cli.main(["check", "--dry-run"])

    assert real == capsys.readouterr().out


def test_check_with_claude_and_no_key_warns_that_a_login_profile_may_work(
    home, nothing_installed, capsys
):
    write_config(home, "brain:\n  backend: claude\n")

    cli.main(["check"])

    out = capsys.readouterr().out
    assert any(
        line.startswith("!") and "ANTHROPIC_API_KEY" in line and "ant auth login" in line
        for line in out.splitlines()
    )
    assert "Ollama" not in out


def test_check_with_claude_key_and_workspace_names_the_workspace(
    home, nothing_installed, capsys, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test123")
    write_config(home, "brain:\n  backend: claude\n")

    cli.main(["check"])

    claude_line = next(line for line in capsys.readouterr().out.splitlines() if "Claude" in line)
    assert claude_line.startswith("✓")
    assert "wrkspc_test123" in claude_line
    assert "sk-ant-test" not in claude_line


def test_check_with_an_invalid_config_reports_it_and_exits_1(home, nothing_installed, capsys):
    write_config(home, "brain:\n  backend: gpt\n")

    assert cli.main(["check"]) == 1

    assert capsys.readouterr().out.startswith("✗")


def test_check_with_everything_ready_exits_0(home, monkeypatch, capsys):
    (home / "data").mkdir()
    (home / "data" / "voiceprint.npy").write_bytes(b"")
    requested: list[tuple[str, float]] = []

    def ollama_tags(url, timeout):
        requested.append((url, timeout))
        tags = {"models": [{"name": "qwen3:8b", "model": "qwen3:8b"}]}
        return FakeHttpResponse(json.dumps(tags).encode())

    cuda = types.SimpleNamespace(
        is_available=lambda: True, get_device_name=lambda *args: "NVIDIA GeForce RTX 5060 Ti"
    )
    monkeypatch.setattr(urllib.request, "urlopen", ollama_tags)
    monkeypatch.setattr(cli, "register_cuda_dll_dirs", list)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module("faster_whisper"))
    monkeypatch.setitem(sys.modules, "speechbrain", fake_module("speechbrain"))
    classifiers = fake_module("speechbrain.inference.classifiers")
    monkeypatch.setitem(sys.modules, "speechbrain.inference.classifiers", classifiers)
    monkeypatch.setitem(sys.modules, "torch", fake_module("torch", cuda=cuda))
    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        fake_module("sounddevice", query_devices=lambda device, kind: {"name": "USB Mic"}),
    )

    assert cli.main(["check"]) == 0

    out = capsys.readouterr().out
    assert "✓ Whisper (faster_whisper) dapat dimuat" in out
    assert "✓ ECAPA (speechbrain) dapat dimuat" in out
    assert "✓ CUDA tersedia: NVIDIA GeForce RTX 5060 Ti" in out
    assert "USB Mic" in out
    assert requested == [("http://127.0.0.1:11434/api/tags", 2.0)]


def test_check_reports_an_ollama_model_that_is_not_pulled(
    home, nothing_installed, monkeypatch, capsys
):
    def other_model(url, timeout):
        return FakeHttpResponse(json.dumps({"models": [{"model": "llama3:8b"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", other_model)

    cli.main(["check"])

    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("✗") and "ollama pull qwen3:8b" in line for line in lines)


def rvc_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if "RVC" in line or "Applio" in line]


def test_check_reports_the_anime_voice_off_without_probing_a_server(
    home, nothing_installed, rvc, capsys
):
    cli.main(["check"])

    lines = rvc_lines(capsys.readouterr().out)
    assert len(lines) == 1
    assert lines[0].startswith("! Suara anime (RVC) mati")
    assert rvc.events == [] and rvc.clients == []


@pytest.fixture
def rvc_install(home, monkeypatch) -> dict[str, Path]:
    """Where `check` looks for each part of the anime voice; nothing exists yet."""
    script = home / "repo" / "tools" / "rvc_server.py"
    monkeypatch.setattr(cli, "SERVER_SCRIPT", script)
    return {
        "Python Applio": RVC_PYTHON,
        "Model RVC": RVC_MODEL,
        "Index RVC": RVC_INDEX,
        "Skrip server RVC": script,
    }


def test_check_with_the_anime_voice_ready_marks_each_part_and_the_answering_server(
    home, nothing_installed, rvc, rvc_install, capsys
):
    enable_rvc(home, index=True)
    for path in rvc_install.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    rvc.served_model = RVC_MODEL.name

    cli.main(["check"])

    lines = rvc_lines(capsys.readouterr().out)
    assert lines[0] == f"✓ Folder Applio: {Path('Applio')}"
    for label, path in rvc_install.items():
        assert f"✓ {label}: {path}" in lines
    assert lines[-1] == "✓ Server suara RVC menjawab di http://127.0.0.1:9881 (model tsukuyomi.pth)"
    assert len(lines) == 6
    assert rvc.servers == []


def test_check_marks_each_missing_anime_voice_file_with_its_path(
    home, nothing_installed, rvc, rvc_install, capsys
):
    enable_rvc(home, index=True)

    assert cli.main(["check"]) == 1

    lines = rvc_lines(capsys.readouterr().out)
    expected = {"Folder Applio": Path("Applio"), **rvc_install}
    for label, path in expected.items():
        line = next(line for line in lines if line.startswith(f"✗ {label}"))
        assert "tidak ditemukan" in line and str(path) in line


def test_check_says_a_stopped_rvc_server_starts_with_cyrene_and_never_spawns_it(
    home, nothing_installed, rvc, rvc_install, capsys
):
    enable_rvc(home)

    cli.main(["check"])

    server = rvc_lines(capsys.readouterr().out)[-1]
    assert server.startswith("! Server suara RVC belum berjalan di http://127.0.0.1:9881")
    assert "waifu run" in server
    assert rvc.events == ["rvc health"]
    assert rvc.servers == []


def test_check_fails_a_running_rvc_server_that_serves_another_model(
    home, nothing_installed, rvc, rvc_install, capsys
):
    # run/say would refuse it and speak the plain voice, so the setup is not ready.
    enable_rvc(home)
    rvc.served_model = "tsukuyomi_50e_2250s.pth"

    assert cli.main(["check"]) == 1

    server = rvc_lines(capsys.readouterr().out)[-1]
    assert server.startswith("✗ Server suara RVC lama dengan model tsukuyomi_50e_2250s.pth")
    assert "tts.rvc.model (tsukuyomi.pth)" in server
    assert rvc.servers == []


def test_check_fails_an_rvc_host_beyond_this_pc_without_probing_it(
    home, nothing_installed, rvc, rvc_install, capsys
):
    enable_rvc(home, extra="    host: 0.0.0.0\n")

    assert cli.main(["check"]) == 1

    # load_config rejects the host, so check reports an invalid config and never probes it.
    out = capsys.readouterr().out
    assert "✗ Konfigurasi tidak valid" in out and "tts.rvc.host" in out
    assert rvc.events == [] and rvc.clients == []


def test_check_leaves_out_the_index_when_none_is_configured(
    home, nothing_installed, rvc, rvc_install, capsys
):
    enable_rvc(home)

    cli.main(["check"])

    assert not any("Index" in line for line in rvc_lines(capsys.readouterr().out))
