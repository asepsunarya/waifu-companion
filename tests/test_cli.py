import io
import json
import logging
import runpy
import shutil
import sys
import types
import urllib.error
import urllib.request
from datetime import datetime
from importlib.machinery import ModuleSpec
from pathlib import Path

import numpy as np
import pytest
from tinytuya.core.error_helper import ERR_PAYLOAD, error_json
from tinytuya.core.XenonDevice import XenonDevice

from waifu import cli
from waifu.brain.base import Brain, BrainReply, ToolCallRecord
from waifu.devices.base import DeviceError
from waifu.devices.ir_codes import IrCodeStore
from waifu.devices.ir_device import IrDevice
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.voice.base import SpeakerEmbedder
from waifu.voice.speaker import SpeakerVerifier
from waifu.voice.stt import WhisperStt

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_RATE = 16000
FRAME = 480  # 30 ms at 16 kHz, the example config's frame size
SILENT_FRAME = np.zeros(FRAME, dtype=np.float32)
LOUD_FRAME = np.full(FRAME, 0.1, dtype=np.float32)
VOICE_MODULES = ("faster_whisper", "speechbrain", "torch", "sounddevice")


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


# --- parsing -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["run", "--no-speaker-check"], "run"),
        (["chat", "--speak"], "chat"),
        (["enroll", "--samples", "8", "--seconds", "3"], "enroll"),
        (["speaker-test"], "speaker-test"),
        (["devices"], "devices"),
        (["device", "ac", "temp", "24"], "device"),
        (["learn-ir", "ac", "on"], "learn-ir"),
        (["learn-ir", "ac", "--missing", "--timeout", "30"], "learn-ir"),
        (["say", "Halo", "Master"], "say"),
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


def test_config_errors_name_the_field_but_never_print_a_secret_value(home, capsys):
    secret = "S3CRETk3y0123456"
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

    for report in (devices_err, check_out):
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
    monkeypatch.setattr(cli, "build_speech_router", lambda tts: router)
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\n"))

    assert cli.main(["chat", "--dry-run", "--speak"]) == 0

    assert router.texts == ["Siap, Master! Lampu kamar sudah menyala."]
    assert player.played == [(240, 24000)]


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
    monkeypatch.setattr(cli, "build_speech_router", lambda tts: router)

    assert cli.main(["say", "Halo", "Master,", "おかえりなさい!"]) == 0

    assert router.texts == ["Halo Master, おかえりなさい!"]
    assert player.played == [(240, 24000)]


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


@pytest.fixture
def run_fakes(home, brains, player, monkeypatch):
    RecordingAssistant.instances = []
    mics: list[FakeMic] = []

    def open_mic(sample_rate, frame_ms, device=None):
        mics.append(FakeMic([]))
        return mics[-1]

    monkeypatch.setattr(cli, "Assistant", RecordingAssistant)
    monkeypatch.setattr(cli, "MicStream", open_mic)
    monkeypatch.setattr(cli, "EcapaEmbedder", lambda *args, **kwargs: FixedEmbedder())
    monkeypatch.setattr(cli, "build_speech_router", lambda tts: FakeRouter())
    return types.SimpleNamespace(mics=mics)


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


@pytest.fixture
def one_utterance(home, brains, player, monkeypatch):
    """`waifu run` with the real Assistant and Whisper wrapper, hearing one spoken phrase."""
    frames = [SILENT_FRAME] * 10 + [LOUD_FRAME] * 40 + [SILENT_FRAME] * 40
    monkeypatch.setattr(cli, "MicStream", lambda *args, **kwargs: FakeMic(frames))
    monkeypatch.setattr(cli, "build_speech_router", lambda tts: FakeRouter())
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
