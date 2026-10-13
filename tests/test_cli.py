import io
import json
import logging
import os
import re
import runpy
import shutil
import socket
import sys
import tempfile
import threading
import types
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path, PureWindowsPath

import numpy as np
import pytest
from tinytuya.core.error_helper import ERR_PAYLOAD, error_json
from tinytuya.core.XenonDevice import XenonDevice

from waifu import cli
from waifu.app import Assistant
from waifu.autostart import DEFAULT_DELAY_S, build_command
from waifu.avatar.bus import STATES
from waifu.avatar.emotion import EMOTIONS
from waifu.avatar.lipsync import VISEMES, viseme_track
from waifu.avatar.server import AvatarError
from waifu.avatar.window import AvatarWindow
from waifu.brain.base import Brain, BrainReply, ToolCallRecord
from waifu.brain.memory_tools import MemoryTools
from waifu.brain.schedule_tools import ScheduleTools
from waifu.config import AvatarConfig, RvcConfig
from waifu.devices.base import DeviceError
from waifu.devices.ir_codes import IrCodeStore
from waifu.devices.ir_device import IrDevice
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.gacha.games import GAMES
from waifu.gacha.history import BannerStats, GachaHistory, HistoryReport, TopPull
from waifu.gacha.hoyolab import (
    Account,
    CheckinResult,
    GachaError,
    GameStatus,
    HoyolabClient,
    WeeklyTask,
)
from waifu.gacha.tools import GachaTools
from waifu.memory import MemoryStore, memory_prompt_section
from waifu.schedule import DeviceAction, Reminder, RoutineAction, ScheduleStore
from waifu.scheduler import Scheduler
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
WAIT_S = 2.0  # an upper bound for the avatar interaction worker's thread, never a sleep
INTERACTION_WORKER = "avatar-interactions"


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
def no_real_avatar(monkeypatch):
    """A test that forgets the avatar fakes fails instead of binding port 9882 or opening the
    mascot (tests/conftest.py also refuses to spawn electron.exe and msedge.exe)."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the real avatar server, window, or port probe")

    monkeypatch.setattr(cli, "AvatarServer", refuse)
    monkeypatch.setattr(cli, "AvatarWindow", refuse)
    monkeypatch.setattr(cli, "port_is_free", refuse)


@pytest.fixture(autouse=True)
def no_real_game_caches(tmp_path, monkeypatch):
    """`check` and the gacha commands look for game logs here, never in the owner's LocalLow
    (tests/conftest.py also fails any test that reads the real one)."""
    monkeypatch.setattr(cli, "GAME_LOGS_DIR", tmp_path / "LocalLow")


@pytest.fixture(autouse=True)
def no_real_memory_or_schedule(tmp_path, monkeypatch):
    """Every memory and schedule store a command builds lives under tmp_path: a test that leaves
    the working directory at the repository fails instead of reading or writing the owner's
    notes in data/memory.json or schedule in data/schedule.json (building a store touches no
    file, so the refusal comes first)."""
    for name in ("MemoryStore", "ScheduleStore"):
        monkeypatch.setattr(cli, name, only_under(tmp_path, getattr(cli, name)))


@pytest.fixture(autouse=True)
def temporary_folders_under_tmp_path(tmp_path, monkeypatch):
    """The throwaway schedule of a dry run lives in a temporary folder: keep those folders in
    tmp_path, where the store guard above allows them."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def only_under(tmp_path: Path, store_class: type) -> Callable[..., object]:
    def store_under_tmp_path(path, **limits):
        if not Path(path).resolve().is_relative_to(tmp_path.resolve()):
            raise AssertionError(
                f"a test reached the file of a {store_class.__name__} outside tmp_path: {path}"
            )
        return store_class(path, **limits)

    return store_under_tmp_path


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


REPLY = "Siap, Master! Lampu kamar sudah menyala."
TAGGED_REPLY = f"[senang] {REPLY}"


class HouseBrain(Brain):
    """Turns every message into one control_device call on the registry it was built with."""

    reply = REPLY

    def __init__(self, registry: DeviceRegistry, providers=(), memory_section=None) -> None:
        self.registry = registry
        self.providers = list(providers)
        self.memory_section = memory_section
        self.heard: list[str] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.heard.append(user_text)
        result = self.registry.execute("lampu_kamar", "on")
        call = ToolCallRecord(
            "control_device", {"device": "lampu_kamar", "command": "on"}, result.message, False
        )
        return BrainReply(self.reply, [call])

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


@dataclass
class AvatarFakes:
    """The avatar faked: `server_error` fails binding its port, `window_error` opening its
    window, `speak_error` every speak event; `on_start` runs when the page starts being served;
    `viewer_connects` answers wait_for_viewer()."""

    events: list[str]
    buses: list["FakeAvatarBus"] = field(default_factory=list)
    servers: list["FakeAvatarServer"] = field(default_factory=list)
    windows: list["FakeAvatarWindow"] = field(default_factory=list)
    server_error: AvatarError | None = None
    window_error: AvatarError | None = None
    speak_error: Exception | None = None
    on_start: Callable[[], None] | None = None
    viewer_connects: bool = True


class FakeAvatarBus:
    """Stands in for AvatarBus: logs what is shown and keeps what she would mouth.

    Reactions to touches show up from the interaction worker's thread: `wait_for(call)`
    waits (bounded) until a call was made."""

    def __init__(self, fakes: AvatarFakes, name: str, framing: str) -> None:
        self.name = name
        self.framing = framing
        self.events = fakes.events
        self.speak_error = fakes.speak_error
        self.viewer_connects = fakes.viewer_connects
        self.calls: list[tuple] = []
        self.voices: list[tuple[np.ndarray, int]] = []
        self._called = threading.Condition()

    def wait_for(self, call: tuple, count: int = 1) -> bool:
        with self._called:
            return self._called.wait_for(lambda: self.calls.count(call) >= count, WAIT_S)

    def _record(self, call: tuple, event: str) -> None:
        with self._called:
            self.calls.append(call)
            self.events.append(event)
            self._called.notify_all()

    def wait_for_viewer(self, timeout: float) -> bool:
        self._record(("wait_for_viewer", timeout), "avatar awaits a viewer")
        return self.viewer_connects

    def state(self, name: str, followup_s: float | None = None) -> None:
        self._record(("state", name, followup_s), f"avatar {name}")

    def emotion(self, name: str, hold_s: float = 4) -> None:
        self._record(("emotion", name, hold_s), f"avatar looks {name}")

    def speak(self, audio: np.ndarray, sample_rate: int, emotion: str) -> None:
        self.voices.append((audio, sample_rate))
        self._record(("speak", emotion), f"avatar speaks {emotion}")
        if self.speak_error is not None:
            raise self.speak_error


class FakeAvatarServer:
    """Stands in for AvatarServer: never binds a port. `on_interact` is what the page's
    pats, pokes, and double-clicks would be handed to."""

    def __init__(
        self,
        config: AvatarConfig,
        bus: FakeAvatarBus,
        fakes: AvatarFakes,
        on_interact: Callable[[str], None] | None = None,
    ) -> None:
        if fakes.server_error is not None:
            raise fakes.server_error
        self.config = config
        self.bus = bus
        self.on_interact = on_interact
        self.url = f"http://{config.host}:{config.port}/"
        self._fakes = fakes

    def __enter__(self) -> "FakeAvatarServer":
        self._fakes.events.append("avatar served")
        if self._fakes.on_start is not None:
            self._fakes.on_start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._fakes.events.append("avatar server stopped")


class FakeAvatarWindow:
    """Stands in for AvatarWindow: never starts a program."""

    def __init__(self, config: AvatarConfig, url: str, fakes: AvatarFakes) -> None:
        self.config = config
        self.url = url
        self._fakes = fakes

    def __enter__(self) -> "FakeAvatarWindow":
        if self._fakes.window_error is not None:
            raise self._fakes.window_error
        self._fakes.events.append("avatar window opened")
        return self

    def __exit__(self, *exc_info) -> None:
        self._fakes.events.append("avatar window closed")


@pytest.fixture
def avatar(events, monkeypatch) -> AvatarFakes:
    fakes = AvatarFakes(events)

    def bus(name: str = "Cyrene", framing: str = "full") -> FakeAvatarBus:
        fakes.buses.append(FakeAvatarBus(fakes, name, framing))
        return fakes.buses[-1]

    def server(
        config: AvatarConfig,
        bus: FakeAvatarBus,
        *,
        on_interact: Callable[[str], None] | None = None,
    ) -> FakeAvatarServer:
        fakes.servers.append(FakeAvatarServer(config, bus, fakes, on_interact))
        return fakes.servers[-1]

    def window(config: AvatarConfig, url: str) -> FakeAvatarWindow:
        fakes.windows.append(FakeAvatarWindow(config, url, fakes))
        return fakes.windows[-1]

    monkeypatch.setattr(cli, "AvatarBus", bus)
    monkeypatch.setattr(cli, "AvatarServer", server)
    monkeypatch.setattr(cli, "AvatarWindow", window)
    return fakes


VRM = Path("avatars/tsukuyomi_a.vrm")


def enable_avatar(home: Path, *, window: str = "mascot", extra: str = "") -> None:
    """config.yaml with only the avatar set up; its paths are relative to `home`."""
    write_config(
        home,
        f"avatar:\n  enabled: true\n  model: {VRM.as_posix()}\n  window: {window}\n" + extra,
    )


# One line per reaction and no cooldown, so a test knows what she says to every touch.
TOUCH_LINES = (
    "  interactions:\n"
    "    reaction_cooldown_s: 0\n"
    "    lines:\n"
    "      head_pat: [Elusannya enak]\n"
    "      body_touch: [Kaget aku]\n"
    "      welcome_back: [Selamat datang]\n"
    "      listen: [Iya Master]\n"
)


def interaction_workers() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == INTERACTION_WORKER]


@pytest.fixture
def event_player(events, monkeypatch) -> None:
    """A Player that logs each playback in `events`, to see what happens right before it."""

    class EventPlayer:
        def __init__(self, device=None) -> None:
            pass

        def play(self, audio: np.ndarray, sample_rate: int) -> None:
            events.append("play")

    monkeypatch.setattr(cli, "Player", EventPlayer)


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
        (["avatar"], "avatar"),
        (["avatar", "--demo"], "avatar"),
        (["memory", "list"], "memory"),
        (["memory", "add", "Master", "suka", "kopi."], "memory"),
        (["memory", "forget", "m3"], "memory"),
        (["memory", "clear", "--yes"], "memory"),
        (["schedule", "list"], "schedule"),
        (["schedule", "cancel", "j3"], "schedule"),
        (["schedule", "clear", "--yes"], "schedule"),
        (["routine", "list"], "routine"),
        (["routine", "run", "tidur", "--dry-run"], "routine"),
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

    def build(config, registry, *, providers=(), memory_section=None):
        built.append(HouseBrain(registry, providers, memory_section))
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


def test_chat_never_prints_or_speaks_the_emotion_tag(
    home, brains, player, speech, monkeypatch, capsys
):
    monkeypatch.setattr(HouseBrain, "reply", TAGGED_REPLY)
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\n"))

    assert cli.main(["chat", "--dry-run", "--speak"]) == 0

    out = capsys.readouterr().out
    assert f"Cyrene: {REPLY}" in out
    assert "senang" not in out
    assert speech[0].texts == [REPLY]


def test_chat_answers_a_reply_that_is_only_a_tag_instead_of_printing_nothing(
    home, brains, player, speech, monkeypatch, capsys
):
    monkeypatch.setattr(HouseBrain, "reply", "[senang]")
    monkeypatch.setattr(sys, "stdin", io.StringIO("nyalakan lampu kamar\n"))

    assert cli.main(["chat", "--dry-run", "--speak"]) == 0

    out = capsys.readouterr().out
    assert "Cyrene: Sudah, Master." in out
    assert "Suara gagal" not in out
    assert speech[0].texts == ["Sudah, Master."]


def test_chat_speak_shows_the_avatar_mouthing_each_reply_with_its_emotion(
    home, brains, event_player, speech, avatar, events, monkeypatch
):
    enable_avatar(home)
    monkeypatch.setattr(HouseBrain, "reply", TAGGED_REPLY)
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\n"))

    assert cli.main(["chat", "--dry-run", "--speak"]) == 0

    assert events == [
        "avatar served",
        "avatar window opened",
        f"say {REPLY}",
        "avatar speaks happy",
        "play",
        "avatar window closed",
        "avatar server stopped",
    ]
    assert (avatar.buses[0].name, avatar.buses[0].framing) == ("Cyrene", "full")
    assert avatar.windows[0].url == avatar.servers[0].url == "http://127.0.0.1:9882/"


def test_chat_speak_still_voices_a_reply_the_avatar_cannot_show(
    home, brains, event_player, speech, avatar, events, monkeypatch, caplog
):
    enable_avatar(home)
    avatar.speak_error = ValueError("viseme track failed")
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\n"))
    caplog.set_level(logging.INFO)

    assert cli.main(["chat", "--dry-run", "--speak"]) == 0

    assert events[2:5] == [f"say {REPLY}", "avatar speaks neutral", "play"]
    assert "viseme track failed" in caplog.text


def test_chat_without_speak_never_shows_the_avatar(home, brains, avatar, monkeypatch, capsys):
    enable_avatar(home)
    monkeypatch.setattr(HouseBrain, "reply", TAGGED_REPLY)
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert avatar.servers == [] and avatar.windows == []
    assert f"Cyrene: {REPLY}" in capsys.readouterr().out


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


def test_say_shows_the_avatar_mouthing_the_words_with_their_tag_s_emotion(
    home, event_player, speech, avatar, events
):
    enable_avatar(home)

    assert cli.main(["say", "[senang]", "Halo", "Master"]) == 0

    assert events == [
        "avatar served",
        "avatar window opened",
        "say Halo Master",
        "avatar awaits a viewer",
        "avatar speaks happy",
        "play",
        "avatar window closed",
        "avatar server stopped",
    ]


def test_say_gives_the_just_opened_avatar_window_up_to_five_seconds_to_connect(
    home, player, speech, avatar
):
    # The speak event reaches only viewers already connected: without the wait the mascot,
    # still loading, would miss it and keep its mouth shut.
    enable_avatar(home)

    assert cli.main(["say", "Halo"]) == 0

    assert avatar.buses[0].calls == [("wait_for_viewer", 5.0), ("speak", "neutral")]


def test_say_speaks_anyway_when_the_avatar_window_does_not_connect_in_time(
    home, event_player, speech, avatar, events, caplog
):
    enable_avatar(home)
    avatar.viewer_connects = False
    caplog.set_level(logging.INFO)

    assert cli.main(["say", "Halo"]) == 0

    assert events[2:6] == ["say Halo", "avatar awaits a viewer", "avatar speaks neutral", "play"]
    assert "No avatar viewer" in caplog.text


def test_say_does_not_wait_for_a_viewer_when_the_avatar_opens_no_window(
    home, player, speech, avatar
):
    enable_avatar(home, window="none")

    assert cli.main(["say", "Halo"]) == 0

    assert avatar.buses[0].calls == [("speak", "neutral")]


def test_say_never_speaks_an_emotion_tag(home, player, speech):
    assert cli.main(["say", "[kaget]", "えっ?", "Master!"]) == 0

    assert speech[0].texts == ["えっ? Master!"]


def test_say_speaks_without_the_avatar_when_its_window_cannot_open(
    home, player, speech, avatar, events, caplog, capsys
):
    enable_avatar(home)
    avatar.window_error = AvatarError(
        "Program maskot (Electron) tidak ditemukan: avatar-app\\electron.exe. Pasang dulu."
    )
    caplog.set_level(logging.INFO)

    assert cli.main(["say", "Halo"]) == 0

    assert (
        "! Avatar tidak tersedia: Program maskot (Electron) tidak ditemukan: "
        "avatar-app\\electron.exe. Pasang dulu. Cyrene tetap berjalan tanpa avatar."
        in capsys.readouterr().out.splitlines()
    )
    assert "electron.exe" in caplog.text  # the autostart window closes; the log keeps why
    assert events == ["avatar served", "avatar server stopped", "say Halo"]
    assert len(player.played) == 1


def test_say_closes_the_avatar_even_when_playback_fails(home, speech, avatar, events, monkeypatch):
    class UnpluggedPlayer:
        def __init__(self, device=None) -> None:
            pass

        def play(self, audio: np.ndarray, sample_rate: int) -> None:
            raise RuntimeError("speaker unplugged")

    monkeypatch.setattr(cli, "Player", UnpluggedPlayer)
    enable_avatar(home)

    assert cli.main(["say", "Halo"]) == 1

    assert events[-2:] == ["avatar window closed", "avatar server stopped"]


def test_mics_prints_the_audio_device_list(monkeypatch, capsys):
    monkeypatch.setattr(cli, "list_devices", lambda: "> 1 USB Mic")

    assert cli.main(["mics"]) == 0

    assert "> 1 USB Mic" in capsys.readouterr().out


# --- run -------------------------------------------------------------------------------------


class RecordingAssistant:
    """Records what `waifu run` asks of her. `busy_answers` are what `busy` says, one per
    check (then False); `reacted` counts each reaction (`speak_reaction` or `listen_now`)."""

    instances: list["RecordingAssistant"] = []

    def __init__(self, config, **collaborators) -> None:
        self.collaborators = collaborators
        self.ran_with: tuple | None = None
        self.announced: list[tuple[str, str]] = []
        self.busy_answers: list[bool] = []
        self.reactions: list[tuple[str, ...]] = []
        self.reacted = threading.Semaphore(0)
        RecordingAssistant.instances.append(self)

    def run(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)

    @property
    def busy(self) -> bool:
        return self.busy_answers.pop(0) if self.busy_answers else False

    def speak_reaction(self, text: str, emotion: str) -> None:
        self.reactions.append(("speak", text, emotion))
        self.reacted.release()

    def listen_now(self) -> None:
        self.reactions.append(("listen",))
        self.reacted.release()

    def await_reactions(self, count: int) -> None:
        for _ in range(count):
            assert self.reacted.acquire(timeout=WAIT_S), f"only {self.reactions} after {WAIT_S} s"

    def announce(self, text: str, emotion: str = "neutral") -> None:
        self.announced.append((text, emotion))


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


def test_run_shows_the_avatar_holding_the_lock_after_the_models_and_before_ready(
    announcing, avatar, home, capsys
):
    enable_avatar(home)
    at_start: list[tuple[bool, str]] = []
    avatar.on_start = lambda: at_start.append(
        (lock_is_free(announcing.lock), capsys.readouterr().out)
    )

    assert cli.main(["run", "--dry-run", "--no-speaker-check", "--announce"]) == 0

    ((lock_free, printed_before),) = at_start
    assert not lock_free
    assert "Memuat model suara" in printed_before and "siap" not in printed_before
    assert "Cyrene siap mendengarkan" in capsys.readouterr().out
    events = announcing.events
    assert events.index("whisper loaded") < events.index("avatar served")
    assert events.index("avatar window opened") < events.index("mic opened")
    assert events[-3:] == ["mic closed", "avatar window closed", "avatar server stopped"]


def test_run_hands_the_avatar_to_the_assistant(run_fakes, avatar, home):
    enable_avatar(home, extra="  framing: upper\nassistant:\n  name: Aiko\n  wake_words: [aiko]\n")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [bus] = avatar.buses
    assert RecordingAssistant.instances[0].collaborators["avatar"] is bus
    assert (bus.name, bus.framing) == ("Aiko", "upper")
    assert avatar.servers[0].bus is bus


def test_run_announces_with_the_avatar_and_leaves_her_idle_not_listening(
    announcing, avatar, home, player
):
    enable_avatar(home)

    assert cli.main(["run", "--dry-run", "--no-speaker-check", "--announce"]) == 0

    assert avatar.buses[0].calls == [
        ("wait_for_viewer", 5.0),
        ("state", "speaking", None),
        ("speak", "neutral"),
        ("state", "idle", None),
    ]


def test_run_announce_does_not_wait_for_a_viewer_when_the_avatar_opens_no_window(
    announcing, avatar, home, player
):
    enable_avatar(home, window="none")

    assert cli.main(["run", "--dry-run", "--no-speaker-check", "--announce"]) == 0

    assert ("wait_for_viewer", 5.0) not in avatar.buses[0].calls
    assert ("speak", "neutral") in avatar.buses[0].calls


def test_run_answers_with_the_tag_s_emotion_and_never_says_the_tag(
    announcing, avatar, brains, home, speech, monkeypatch
):
    enable_avatar(home)
    monkeypatch.setattr(HouseBrain, "reply", TAGGED_REPLY)
    frames = [SILENT_FRAME] * 10 + [LOUD_FRAME] * 40 + [SILENT_FRAME] * 40
    monkeypatch.setattr(cli, "MicStream", lambda *args, **kwargs: FakeMic(frames))
    monkeypatch.setattr(cli, "WhisperStt", lambda config, name: HearingStt("Cyrene, lampu"))

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert speech[0].texts == [REPLY]
    assert avatar.buses[0].calls == [
        ("state", "thinking", None),
        ("state", "speaking", None),
        ("speak", "happy"),
        ("state", "listening", 8.0),
    ]


def test_run_listens_without_the_avatar_when_its_port_is_taken(run_fakes, avatar, home, capsys):
    enable_avatar(home)
    avatar.server_error = AvatarError("Port 127.0.0.1:9882 sudah dipakai program lain.")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    out = capsys.readouterr().out
    warning = out.index(
        "! Avatar tidak tersedia: Port 127.0.0.1:9882 sudah dipakai program lain. "
        "Cyrene tetap berjalan tanpa avatar."
    )
    assert warning < out.index("Cyrene siap mendengarkan")
    assert RecordingAssistant.instances[0].collaborators["avatar"] is None
    assert RecordingAssistant.instances[0].ran_with is not None
    assert avatar.windows == []


def test_run_with_the_avatar_off_shows_none(run_fakes, avatar):
    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert RecordingAssistant.instances[0].collaborators["avatar"] is None
    assert avatar.buses == [] and avatar.servers == []


def test_run_closes_the_avatar_after_ctrl_c(run_fakes, avatar, home, monkeypatch):
    def interrupted(self, mic, segmenter) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(RecordingAssistant, "run", interrupted)
    enable_avatar(home)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 130

    assert run_fakes.events[-2:] == ["avatar window closed", "avatar server stopped"]


def test_run_shows_no_avatar_when_the_voice_models_fail_to_load(run_fakes, avatar, home):
    enable_avatar(home)
    (home / "data").mkdir()
    (home / "data" / "voiceprint.npy").write_bytes(b"bukan voiceprint")

    assert cli.main(["run", "--dry-run"]) == 1

    assert avatar.servers == []


def touch(avatar: AvatarFakes, *kinds: str) -> None:
    """The avatar page reporting `kinds` (pats, pokes, double-clicks) to Cyrene's server."""
    [server] = avatar.servers
    assert server.on_interact is not None, "the avatar server got nothing to hand touches to"
    for kind in kinds:
        server.on_interact(kind)


def touched_while_listening(avatar: AvatarFakes, *kinds: str, reactions: int):
    """A RecordingAssistant.run that reports `kinds` from the page, then waits for her to react."""

    def run(self: RecordingAssistant, mic, segmenter) -> None:
        touch(avatar, *kinds)
        self.await_reactions(reactions)

    return run


def test_run_answers_pats_pokes_and_double_clicks_on_the_avatar_through_the_assistant(
    run_fakes, avatar, home, monkeypatch
):
    enable_avatar(home, extra=TOUCH_LINES)
    monkeypatch.setattr(
        RecordingAssistant,
        "run",
        touched_while_listening(avatar, "head_pat", "body_touch", "double_click", reactions=4),
    )

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert RecordingAssistant.instances[0].reactions == [
        ("speak", "Elusannya enak", "happy"),
        ("speak", "Kaget aku", "surprised"),
        ("listen",),
        ("speak", "Iya Master", "happy"),
    ]


def test_run_keeps_quiet_about_a_touch_while_she_is_busy(run_fakes, avatar, home, monkeypatch):
    enable_avatar(home, extra=TOUCH_LINES)

    def run(self: RecordingAssistant, mic, segmenter) -> None:
        self.busy_answers = [True]  # still answering Master when the pat comes in
        touch(avatar, "head_pat", "body_touch")
        self.await_reactions(1)

    monkeypatch.setattr(RecordingAssistant, "run", run)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert RecordingAssistant.instances[0].reactions == [("speak", "Kaget aku", "surprised")]


def test_run_ignores_touches_before_she_can_answer_and_after_she_stops(run_fakes, avatar, home):
    enable_avatar(home, extra=TOUCH_LINES)
    # The page is served (and its window loads) while her voice is still starting.
    avatar.on_start = lambda: touch(avatar, "head_pat")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0
    touch(avatar, "body_touch")

    assert RecordingAssistant.instances[0].reactions == []
    assert interaction_workers() == []


def test_run_says_a_pat_like_a_reply_with_the_mic_paused_and_the_avatar_mouthing_it(
    announcing, avatar, home, speech, event_player, monkeypatch
):
    enable_avatar(home, extra=TOUCH_LINES)
    events = announcing.events

    class PattedMic(EventMic):
        def frames(self):
            touch(avatar, "head_pat")
            assert avatar.buses[0].wait_for(("state", "listening", 8.0))
            yield from super().frames()

    monkeypatch.setattr(cli, "MicStream", lambda *args, **kwargs: PattedMic(events))

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert events[events.index("mic opened") :] == [
        "mic opened",
        "mic paused",
        "say Elusannya enak",
        "avatar speaking",
        "avatar speaks happy",
        "play",
        "mic resumed",
        "avatar listening",
        "listening",
        "mic closed",
        "avatar window closed",
        "avatar server stopped",
    ]


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


# --- avatar ----------------------------------------------------------------------------------


class InterruptingSleep:
    """A `sleep` that lets `waits - 1` waits pass, then acts like Ctrl+C during the last."""

    def __init__(self, waits: int) -> None:
        self.waits = waits
        self.seconds: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)
        if len(self.seconds) == self.waits:
            raise KeyboardInterrupt


def test_avatar_shows_her_and_prints_the_page_until_ctrl_c(
    home, avatar, events, monkeypatch, capsys
):
    enable_avatar(home)
    monkeypatch.setattr(cli, "sleep", InterruptingSleep(1))

    assert cli.main(["avatar"]) == 0

    out = capsys.readouterr().out
    assert "http://127.0.0.1:9882/" in out and "Ctrl+C" in out
    assert events == [
        "avatar served",
        "avatar window opened",
        "avatar window closed",
        "avatar server stopped",
    ]
    assert avatar.buses[0].calls == []


def test_avatar_demo_cycles_every_emotion_and_state_with_her_mouth_moving(
    home, avatar, events, monkeypatch
):
    enable_avatar(home)
    sleep = InterruptingSleep(12)
    monkeypatch.setattr(cli, "sleep", sleep)

    assert cli.main(["avatar", "--demo"]) == 0

    calls = avatar.buses[0].calls
    assert calls[:3] == [("state", "idle", None), ("emotion", "neutral", 3.0), ("speak", "neutral")]
    emotions = [call[1] for call in calls if call[0] == "emotion"]
    assert emotions == [EMOTIONS[step % len(EMOTIONS)] for step in range(12)]
    assert set(emotions) == {"neutral", "happy", "sad", "angry", "surprised", "relaxed"}
    assert [call[1] for call in calls if call[0] == "state"] == [
        STATES[step % len(STATES)] for step in range(12)
    ]
    assert [call[1] for call in calls if call[0] == "speak"] == emotions
    assert sleep.seconds == [3.0] * 12
    assert events[-2:] == ["avatar window closed", "avatar server stopped"]


def test_avatar_demo_voice_opens_and_closes_her_mouth_through_every_vowel_shape(
    home, avatar, monkeypatch
):
    enable_avatar(home)
    monkeypatch.setattr(cli, "sleep", InterruptingSleep(1))

    assert cli.main(["avatar", "--demo"]) == 0

    [(voice, sample_rate)] = avatar.buses[0].voices
    track = viseme_track(voice, sample_rate)
    openness = [sum(frame) for frame in track]
    before = [0.0, *openness[:-1]]
    syllables = sum(was <= 0.5 < now for was, now in zip(before, openness, strict=True))
    assert syllables == len(VISEMES)  # the mouth closes between them
    assert max(openness) > 0.8
    shapes = {VISEMES[int(np.argmax(frame))] for frame in track if sum(frame) > 0.5}
    assert shapes == set(VISEMES)
    assert len(voice) / sample_rate < 3.0  # over before the next step


def test_avatar_when_turned_off_explains_how_to_turn_it_on(home, avatar, capsys):
    assert cli.main(["avatar", "--demo"]) == 2

    out = capsys.readouterr().out
    assert "avatar.enabled: true" in out and "README" in out
    assert avatar.servers == []


def test_avatar_that_cannot_open_its_window_says_why_and_exits_1(home, avatar, events, capsys):
    enable_avatar(home)
    avatar.window_error = AvatarError("Program maskot (Electron) tidak ditemukan: x. Pasang dulu.")

    assert cli.main(["avatar", "--demo"]) == 1

    assert "✗ Program maskot (Electron) tidak ditemukan: x. Pasang dulu." in capsys.readouterr().out
    assert events == ["avatar served", "avatar server stopped"]
    assert avatar.buses[0].calls == []


class TouchingSleep:
    """A `sleep` for `waifu avatar`: at the first wait the page reports `kinds`, and once her
    avatar has made `until` (the end of her reaction) it acts like Ctrl+C."""

    def __init__(self, avatar: AvatarFakes, *kinds: str, until: tuple) -> None:
        self.avatar = avatar
        self.kinds = kinds
        self.until = until

    def __call__(self, seconds: float) -> None:
        touch(self.avatar, *self.kinds)
        assert self.avatar.buses[0].wait_for(self.until)
        raise KeyboardInterrupt


def test_avatar_prints_her_answer_to_a_pat_and_shows_it_with_a_face_and_a_moving_mouth(
    home, avatar, monkeypatch, capsys
):
    enable_avatar(home, extra=TOUCH_LINES)
    monkeypatch.setattr(cli, "sleep", TouchingSleep(avatar, "head_pat", until=("speak", "happy")))

    assert cli.main(["avatar"]) == 0

    assert "Cyrene: Elusannya enak" in capsys.readouterr().out.splitlines()
    bus = avatar.buses[0]
    assert bus.calls == [("emotion", "happy", 4), ("speak", "happy")]
    [(voice, sample_rate)] = bus.voices
    assert 0 < len(voice) / sample_rate < 3.0  # a short made-up voice: no TTS, no speakers
    assert interaction_workers() == []


def test_avatar_answers_a_poke_with_a_surprised_face(home, avatar, monkeypatch, capsys):
    enable_avatar(home, extra=TOUCH_LINES)
    monkeypatch.setattr(
        cli, "sleep", TouchingSleep(avatar, "body_touch", until=("speak", "surprised"))
    )

    assert cli.main(["avatar"]) == 0

    assert "Cyrene: Kaget aku" in capsys.readouterr().out.splitlines()
    assert avatar.buses[0].calls == [("emotion", "surprised", 4), ("speak", "surprised")]


def test_avatar_double_click_explains_that_listening_needs_waifu_run(
    home, avatar, monkeypatch, capsys
):
    enable_avatar(home, extra=TOUCH_LINES)
    monkeypatch.setattr(
        cli, "sleep", TouchingSleep(avatar, "double_click", until=("speak", "happy"))
    )

    assert cli.main(["avatar"]) == 0

    lines = capsys.readouterr().out.splitlines()
    hint = next(index for index, line in enumerate(lines) if "'waifu run'" in line)
    assert lines.index("Cyrene: Iya Master") > hint


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


def avatar_lines(out: str) -> list[str]:
    return [
        line for line in out.splitlines() if "avatar" in line.lower() or "live2d" in line.lower()
    ]


@dataclass
class AvatarInstall:
    """Where `check` looks for the avatar's parts under `home`; nothing exists yet."""

    model: Path
    electron_dir: Path
    electron: Path
    probed: list[tuple[str, int]]
    port_free: bool = True


@pytest.fixture
def avatar_install(home, monkeypatch) -> AvatarInstall:
    """The real AvatarWindow, which only names its program here, and a faked port probe."""
    electron_dir = home / "avatar-app"
    install = AvatarInstall(
        model=VRM,
        electron_dir=electron_dir,
        electron=electron_dir / "node_modules" / "electron" / "dist" / "electron.exe",
        probed=[],
    )

    def probe(host: str, port: int) -> bool:
        install.probed.append((host, port))
        return install.port_free

    monkeypatch.setattr(cli, "AvatarWindow", AvatarWindow)
    monkeypatch.setattr(cli, "port_is_free", probe)
    return install


def enable_checked_avatar(home: Path, install: AvatarInstall, *, window: str = "mascot") -> None:
    enable_avatar(home, window=window, extra=f"  electron_dir: {install.electron_dir.as_posix()}\n")


def create(*paths: Path) -> None:
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")


def test_check_reports_the_avatar_off_without_probing_its_port(home, nothing_installed, capsys):
    cli.main(["check"])

    assert avatar_lines(capsys.readouterr().out) == [
        "! Avatar 3D mati (avatar.enabled: false); Cyrene berjalan tanpa avatar"
    ]


def test_check_with_the_avatar_ready_marks_the_model_the_mascot_and_the_free_port(
    home, nothing_installed, avatar_install, capsys
):
    enable_checked_avatar(home, avatar_install)
    create(avatar_install.model, avatar_install.electron)

    cli.main(["check"])

    assert avatar_lines(capsys.readouterr().out) == [
        "✓ Mesin avatar: VRM 3D (avatar.engine: vrm)",
        f"✓ Model VRM avatar: {VRM}",
        f"✓ Jendela maskot avatar (Electron): {avatar_install.electron}",
        "✓ Port avatar 127.0.0.1:9882 bebas",
    ]
    assert avatar_install.probed == [("127.0.0.1", 9882)]


def test_check_marks_a_missing_vrm_and_a_missing_electron_with_how_to_fix_them(
    home, nothing_installed, avatar_install, capsys
):
    enable_checked_avatar(home, avatar_install)

    assert cli.main(["check"]) == 1

    _, model, window, _ = avatar_lines(capsys.readouterr().out)
    assert model == f"✗ Model VRM avatar tidak ditemukan: {VRM} (periksa avatar.model)"
    assert window.startswith(
        f"✗ Jendela maskot avatar (Electron) tidak ditemukan: {avatar_install.electron}"
    )
    assert "npm install" in window and "node node_modules/electron/install.js" in window


def test_check_fails_an_avatar_port_taken_by_another_program(
    home, nothing_installed, avatar_install, capsys
):
    enable_checked_avatar(home, avatar_install)
    create(avatar_install.model, avatar_install.electron)
    avatar_install.port_free = False

    assert cli.main(["check"]) == 1

    port = avatar_lines(capsys.readouterr().out)[-1]
    assert port.startswith("✗ Port avatar 127.0.0.1:9882 sudah dipakai program lain")
    assert "avatar.port" in port


def test_check_only_warns_about_an_avatar_port_held_by_a_running_cyrene(
    home, nothing_installed, avatar_install, capsys
):
    enable_checked_avatar(home, avatar_install)
    create(avatar_install.model, avatar_install.electron)
    avatar_install.port_free = False

    with InstanceLock(home / "data" / "run.lock"):
        cli.main(["check"])

    port = avatar_lines(capsys.readouterr().out)[-1]
    assert port == "! Port avatar 127.0.0.1:9882 dipakai Cyrene yang sedang berjalan"


@pytest.mark.parametrize("installed", [True, False], ids=["edge", "no-edge"])
def test_check_looks_for_edge_for_the_app_window(
    installed, home, nothing_installed, avatar_install, monkeypatch, capsys
):
    enable_checked_avatar(home, avatar_install, window="app")
    create(avatar_install.model)
    for folder in ("ProgramFiles(x86)", "ProgramFiles"):
        monkeypatch.setenv(folder, str(home / folder))
    edge = home / "ProgramFiles" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    if installed:
        create(edge)

    cli.main(["check"])

    window = avatar_lines(capsys.readouterr().out)[2]
    if installed:
        assert window == f"✓ Jendela avatar (Microsoft Edge): {edge}"
    else:
        assert window.startswith("✗ Microsoft Edge untuk jendela avatar tidak ditemukan")
        assert "avatar.window: mascot" in window


def test_check_with_the_avatar_window_off_names_the_page_to_open(
    home, nothing_installed, avatar_install, capsys
):
    enable_checked_avatar(home, avatar_install, window="none")
    create(avatar_install.model)

    assert cli.main(["check"]) == 1  # nothing installed; the avatar lines are all fine

    lines = avatar_lines(capsys.readouterr().out)
    assert lines[2].startswith("✓ Jendela avatar tidak dibuka (avatar.window: none)")
    assert "http://127.0.0.1:9882/" in lines[2]
    assert not any(line.startswith("✗") for line in lines)


LIVE2D_MODEL = Path("avatars/cyrene-live2d/Cyrene.model3.json")
LIVE2D_CORE = Path("avatars/live2d/live2dcubismcore.min.js")
LIVE2D_TEXTURES = ["Cyrene.4096/texture_00.png", "Cyrene.4096/texture_01.png"]


def enable_live2d(home: Path, install: AvatarInstall) -> None:
    """A Live2D avatar under `home` (nothing created yet): the check must never read C:/AI."""
    write_config(
        home,
        "avatar:\n  enabled: true\n  engine: live2d\n"
        f"  model: {LIVE2D_MODEL.as_posix()}\n  live2d_core: {LIVE2D_CORE.as_posix()}\n"
        f"  electron_dir: {install.electron_dir.as_posix()}\n",
    )


def write_model3(references: object) -> None:
    """A Cyrene.model3.json naming `references` as its FileReferences."""
    LIVE2D_MODEL.parent.mkdir(parents=True, exist_ok=True)
    LIVE2D_MODEL.write_text(json.dumps({"Version": 3, "FileReferences": references}), "utf-8")


def create_live2d_model(*, textures: list[str] = LIVE2D_TEXTURES) -> None:
    write_model3({"Moc": "Cyrene.moc3", "Textures": textures, "Physics": "Cyrene.physics3.json"})
    folder = LIVE2D_MODEL.parent
    create(folder / "Cyrene.moc3", *(folder / texture for texture in textures))


def test_check_with_a_live2d_avatar_ready_marks_the_model_its_files_and_the_core(
    home, nothing_installed, avatar_install, capsys
):
    enable_live2d(home, avatar_install)
    create_live2d_model()
    create(LIVE2D_CORE, avatar_install.electron)

    cli.main(["check"])

    engine, model, files, core, window, port = avatar_lines(capsys.readouterr().out)
    assert engine == "✓ Mesin avatar: Live2D (avatar.engine: live2d)"
    assert model == f"✓ Model Live2D avatar: {LIVE2D_MODEL}"
    assert files == "✓ File model Live2D: Cyrene.moc3 dan 2 tekstur"
    assert core.startswith(f"✓ Cubism Core Live2D: {LIVE2D_CORE}")
    assert "jangan pernah disalin ke repo" in core
    assert window == f"✓ Jendela maskot avatar (Electron): {avatar_install.electron}"
    assert port == "✓ Port avatar 127.0.0.1:9882 bebas"


def test_check_marks_a_missing_cubism_core_with_where_to_get_it(
    home, nothing_installed, avatar_install, capsys
):
    enable_live2d(home, avatar_install)
    create_live2d_model()

    assert cli.main(["check"]) == 1

    [core] = [line for line in avatar_lines(capsys.readouterr().out) if "Cubism Core" in line]
    assert core.startswith(f"✗ Cubism Core Live2D tidak ditemukan: {LIVE2D_CORE}")
    assert "avatar.live2d_core" in core
    assert "unduh Cubism SDK for Web dari situs resmi Live2D" in core
    assert "jangan pernah disalin ke repo" in core


def test_check_names_the_live2d_files_that_the_model3_json_needs_but_are_missing(
    home, nothing_installed, avatar_install, capsys
):
    enable_live2d(home, avatar_install)
    # Only the first texture is there; the moc3 is missing and one texture lies outside the
    # model folder, where Cyrene's page server cannot reach it.
    write_model3({"Moc": "Cyrene.moc3", "Textures": [*LIVE2D_TEXTURES[:1], "../texture_99.png"]})
    create(LIVE2D_MODEL.parent / LIVE2D_TEXTURES[0], LIVE2D_MODEL.parent.parent / "texture_99.png")

    assert cli.main(["check"]) == 1

    files = avatar_lines(capsys.readouterr().out)[2]
    assert files.startswith("✗ File model Live2D tidak ditemukan")
    assert "Cyrene.moc3, ../texture_99.png" in files
    assert LIVE2D_TEXTURES[0] not in files


@pytest.mark.parametrize(
    "reference",
    [
        "//attacker.invalid/share/Cyrene.moc3",
        "\\\\attacker.invalid\\share\\Cyrene.moc3",
        "/Cyrene.moc3",
        "C:/Windows/win.ini",
        "D:Cyrene.moc3",
        "https://attacker.invalid/Cyrene.moc3",
    ],
    ids=["unc-slashes", "unc-backslashes", "absolute", "drive", "drive-relative", "url"],
)
def test_check_never_resolves_a_model3_reference_that_is_not_a_path_inside_the_folder(
    reference, home, nothing_installed, avatar_install, capsys, monkeypatch
):
    # A downloaded model3.json may name \\host\share\...: resolving it makes Windows contact
    # that host (offering it the owner's credentials). The guard records the lookup instead.
    real_realpath = os.path.realpath
    unc_lookups: list[str] = []

    def guarded_realpath(target: str | os.PathLike[str], *args: object, **kwargs: object) -> str:
        text = os.fspath(target)
        if text.startswith(("\\\\", "//")):
            unc_lookups.append(text)
            return text
        return real_realpath(target, *args, **kwargs)

    enable_live2d(home, avatar_install)
    create_live2d_model()
    write_model3({"Moc": reference, "Textures": LIVE2D_TEXTURES})
    monkeypatch.setattr(os.path, "realpath", guarded_realpath)

    assert cli.main(["check"]) == 1

    files = avatar_lines(capsys.readouterr().out)[2]
    assert files.startswith("✗ File model Live2D tidak ditemukan")
    assert reference in files
    assert unc_lookups == []


@pytest.mark.parametrize(
    "references",
    [None, {"Textures": LIVE2D_TEXTURES}, {"Moc": "Cyrene.moc3", "Textures": "texture_00.png"}],
    ids=["no-references", "no-moc", "textures-not-a-list"],
)
def test_check_fails_a_model3_json_that_does_not_describe_a_live2d_model(
    references, home, nothing_installed, avatar_install, capsys
):
    enable_live2d(home, avatar_install)
    write_model3(references)

    assert cli.main(["check"]) == 1

    files = avatar_lines(capsys.readouterr().out)[2]
    assert files.startswith(f"✗ {LIVE2D_MODEL.name} bukan model Live2D yang bisa dibaca")


def test_check_fails_a_model3_json_that_is_not_json(
    home, nothing_installed, avatar_install, capsys
):
    enable_live2d(home, avatar_install)
    create(LIVE2D_MODEL)
    LIVE2D_MODEL.write_text("{ bukan json", "utf-8")

    assert cli.main(["check"]) == 1

    files = avatar_lines(capsys.readouterr().out)[2]
    assert files.startswith(f"✗ {LIVE2D_MODEL.name} bukan model Live2D yang bisa dibaca")


def test_check_marks_a_missing_model3_json_and_skips_its_files(
    home, nothing_installed, avatar_install, capsys
):
    enable_live2d(home, avatar_install)
    create(LIVE2D_CORE)

    assert cli.main(["check"]) == 1

    lines = avatar_lines(capsys.readouterr().out)
    assert lines[1] == (
        f"✗ Model Live2D avatar tidak ditemukan: {LIVE2D_MODEL} (periksa avatar.model)"
    )
    assert lines[2].startswith("✓ Cubism Core Live2D")


# Taken at import, before the autouse no_real_avatar fixture swaps it out for every test.
REAL_PORT_IS_FREE = cli.port_is_free


def test_port_is_free_is_false_while_another_socket_listens_and_true_once_it_closes():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        assert REAL_PORT_IS_FREE("127.0.0.1", port) is False

    assert REAL_PORT_IS_FREE("127.0.0.1", port) is True


# --- phone notifications ----------------------------------------------------------------------

NOTIFY_TOPIC = "cyrene-AbCdEf123456_-x"


class FakeNotifier:
    def __init__(self, delivered: bool = True) -> None:
        self.delivered = delivered
        self.sent: list[tuple[str, str, tuple[str, ...]]] = []

    def send(self, title: str, message: str, *, tags=()) -> bool:
        self.sent.append((title, message, tuple(tags)))
        return self.delivered


def enable_notify(home: Path) -> None:
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    enabled = text.replace(
        "notify:\n  enabled: false\n", f"notify:\n  enabled: true\n  topic: {NOTIFY_TOPIC}\n"
    )
    assert enabled != text
    config.write_text(enabled, encoding="utf-8")


def use_notifier(monkeypatch, notifier: FakeNotifier) -> None:
    monkeypatch.setattr(cli, "build_notifier", lambda config: notifier if config.enabled else None)


def test_notify_test_pushes_a_test_message_to_the_phone(home, monkeypatch, capsys):
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["notify-test"]) == 0

    [(title, message, tags)] = notifier.sent
    assert title == "Cyrene"
    assert message.startswith("Tes notifikasi dari Cyrene")
    assert tags == ("video_game",)
    out = capsys.readouterr().out
    assert out.startswith("✓")
    assert NOTIFY_TOPIC not in out


def test_notify_test_reports_a_push_that_did_not_go_out(home, monkeypatch, capsys):
    enable_notify(home)
    use_notifier(monkeypatch, FakeNotifier(delivered=False))

    assert cli.main(["notify-test"]) == 1

    out = capsys.readouterr().out
    assert out.startswith("✗")
    assert "https://ntfy.sh" in out


def test_notify_test_with_notifications_off_says_how_to_turn_them_on(home, monkeypatch, capsys):
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["notify-test"]) == 2

    assert notifier.sent == []
    assert "notify.enabled" in capsys.readouterr().out


# --- HoYoverse games ---------------------------------------------------------------------------

GACHA_COOKIE = "ltuid_v2=1; ltoken_v2=v2_SECRETCOOKIE; cookie_token_v2=SECRETTOKEN"


def enable_gacha(home: Path, *, cookie: str | None = GACHA_COOKIE, extra: str = "") -> None:
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    cookie_line = f"  cookie: '{cookie}'\n" if cookie else ""
    enabled = text.replace(
        "gacha:\n  enabled: false\n", f"gacha:\n  enabled: true\n{cookie_line}{extra}"
    )
    assert enabled != text
    config.write_text(enabled, encoding="utf-8")


HSR_STATUS = GameStatus(
    "hsr", 232, 300, 2880, 300, 500, False, (WeeklyTask("Echo of War", 2, 3),), 4, 1, True
)
ZZZ_STATUS = GameStatus("zzz", 240, 240, 0, 400, 400, True, (), 0, 0, None)


class FakeGameClient:
    """HoyolabClient's surface with fixed answers."""

    def __init__(self) -> None:
        self.statuses = {
            "hsr": HSR_STATUS,
            "genshin": GachaError("notes_off", "Real-Time Notes Genshin di HoYoLAB belum aktif."),
            "zzz": ZZZ_STATUS,
        }
        self.checkins = {
            "hsr": CheckinResult("hsr", True, "20 Stellar Jade"),
            "genshin": CheckinResult("genshin", False, None),
            "zzz": GachaError("captcha", "HoYoLAB minta verifikasi captcha ZZZ."),
        }
        self.account_list = [
            Account("hsr", 800000009, "Alt", 20, "Asia"),
            Account("hsr", 800000001, "Master", 70, "Asia"),
            Account("zzz", 1300000003, "Master", 50, "Asia"),
        ]
        self.failure: GachaError | None = None

    def accounts(self):
        if self.failure is not None:
            raise self.failure
        return self.account_list

    def status(self, games):
        return {game: self.statuses[game] for game in games}

    def checkin(self, game_id):
        result = self.checkins[game_id]
        if isinstance(result, GachaError):
            raise result
        return result


class FakeGameHistory:
    def __init__(self, report: HistoryReport) -> None:
        self.report_to_give = report
        self.asked: list[tuple[str, bool]] = []

    def report(self, game_id, *, refresh=True, full=False):
        self.asked.append((game_id, full))
        return self.report_to_give


HSR_GAME = GAMES["hsr"]
HSR_HISTORY = HistoryReport(
    game=HSR_GAME,
    updated=True,
    problem=None,
    complete=True,
    stats=(
        BannerStats(
            HSR_GAME.banners[0],
            22,
            577,
            False,
            (
                TopPull(9, "Yunli", 29, True, "2026-09-29 00:05:00"),
                TopPull(5, "Welt", 20, False, "2026-05-25 10:00:00"),
            ),
        ),
        BannerStats(
            HSR_GAME.banners[1],
            0,
            299,
            None,
            (TopPull(7, "A Star That Lights the Night", 69, None, "2026-07-17 05:19:27"),),
        ),
    ),
    new_pulls=(),
    new_count=3,
    latest_pull=datetime(2026, 10, 11, 21, 18, tzinfo=timezone(timedelta(hours=7))),
)


@pytest.fixture
def games(monkeypatch) -> types.SimpleNamespace:
    parts = types.SimpleNamespace(
        hoyolab=FakeGameClient(), history=FakeGameHistory(HSR_HISTORY), built=[]
    )

    def build(config):
        parts.built.append(config)
        return cli.GachaParts(parts.hoyolab, parts.history)

    monkeypatch.setattr(cli, "build_gacha", build)
    return parts


def test_gacha_accounts_marks_the_highest_level_account_of_each_game(home, games, capsys):
    assert cli.main(["gacha", "accounts"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[:3] == [
        "  HSR      800000009  Alt (level 20, Asia)",
        "→ HSR      800000001  Master (level 70, Asia)",
        "→ ZZZ      1300000003  Master (level 50, Asia)",
    ]
    assert "gacha.uids" in lines[3]


def test_gacha_accounts_marks_the_uid_from_config_yaml(home, games, capsys):
    enable_gacha(home, extra="  uids: {hsr: 800000009}\n")

    assert cli.main(["gacha", "accounts"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("→ HSR      800000009")
    assert lines[1].startswith("  HSR      800000001")


def test_gacha_accounts_without_a_working_cookie_says_why(home, games, capsys):
    games.hoyolab.failure = GachaError("no_cookie", "Cookie HoYoLAB belum diisi.")

    assert cli.main(["gacha", "accounts"]) == 1

    assert capsys.readouterr().out == "✗ Cookie HoYoLAB belum diisi.\n"


def test_gacha_status_prints_each_game_and_fails_when_one_cannot_be_read(home, games, capsys):
    assert cli.main(["gacha", "status"]) == 1

    out = capsys.readouterr().out
    hsr, rest = out.split("Genshin (Genshin Impact)\n")
    genshin, zzz = rest.split("ZZZ (Zenless Zone Zero)\n")
    assert hsr.startswith("HSR (Honkai: Star Rail)\n  Trailblaze Power 232/300 (penuh ")
    assert "48 menit lagi)" in hsr
    for line in (
        "  Daily Training 300/500: belum selesai",
        "  Mingguan: Echo of War 2/3",
        "  Ekspedisi selesai 1/4",
        "  Check-in HoYoLAB hari ini: sudah",
    ):
        assert line in hsr.splitlines()
    assert "  Reset harian jam " in hsr
    assert genshin == "  ✗ Real-Time Notes Genshin di HoYoLAB belum aktif.\n"
    assert "  Battery Charge 240/240 (sudah penuh)" in zzz.splitlines()
    assert "Ekspedisi" not in zzz
    assert "  Check-in HoYoLAB hari ini: tidak diketahui" in zzz.splitlines()


def test_gacha_status_of_one_game(home, games, capsys):
    assert cli.main(["gacha", "status", "hsr"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("HSR (Honkai: Star Rail)")
    assert "Genshin" not in out and "ZZZ" not in out


def test_gacha_checkin_reports_each_game(home, games, capsys):
    assert cli.main(["gacha", "checkin"]) == 1  # ZZZ needed a captcha

    assert capsys.readouterr().out.splitlines() == [
        "✓ HSR: dapat 20 Stellar Jade",
        "✓ Genshin: sudah diambil hari ini",
        "✗ ZZZ: HoYoLAB minta verifikasi captcha ZZZ.",
    ]


def test_gacha_history_prints_each_banners_pity_and_last_five_stars(home, games, capsys):
    assert cli.main(["gacha", "history", "hsr"]) == 0

    assert games.history.asked == [("hsr", False)]
    assert capsys.readouterr().out.splitlines() == [
        "Membaca riwayat warp HSR...",
        "✓ 3 pull baru tersimpan, terakhir 2026-10-11 21:18.",
        "Character Event Warp: pity 22/90, berikutnya 50/50, total 577 pull",
        "  5★ terakhir: Yunli (29 pull, menang 50/50), Welt (20 pull, kalah 50/50)",
        "Light Cone Event Warp: pity 0/80, total 299 pull",
        "  5★ terakhir: A Star That Lights the Night (69 pull)",
    ]


def test_gacha_history_full_reads_every_page(home, games, capsys):
    assert cli.main(["gacha", "history", "hsr", "--full"]) == 0

    assert games.history.asked == [("hsr", True)]
    assert "semua halaman" in capsys.readouterr().out


def test_gacha_history_that_could_not_refresh_shows_the_saved_numbers_and_why(home, games, capsys):
    games.history.report_to_give = HistoryReport(
        HSR_GAME,
        False,
        "Buka riwayat warp di HSR sekali, lalu tanya lagi.",
        False,
        HSR_HISTORY.stats,
        (),
        0,
        None,
    )

    assert cli.main(["gacha", "history", "hsr"]) == 1

    out = capsys.readouterr().out.splitlines()
    assert out[1] == "! Buka riwayat warp di HSR sekali, lalu tanya lagi."
    assert "pull baru" not in "\n".join(out)
    assert out[-1].startswith("! Riwayat HSR baru terbaca sebagian")


def test_gacha_history_without_anything_saved_fails(home, games, capsys):
    games.history.report_to_give = HistoryReport(
        HSR_GAME, False, "Buka riwayat warp di HSR sekali.", False, (), (), 0, None
    )

    assert cli.main(["gacha", "history", "hsr"]) == 1

    assert "✗ Belum ada riwayat HSR yang tersimpan." in capsys.readouterr().out


class FakeWatcher:
    instances: list["FakeWatcher"] = []

    def __init__(self, hoyolab, config, *, notifier, announce, title) -> None:
        self.hoyolab, self.config, self.notifier = hoyolab, config, notifier
        self.announce, self.title = announce, title
        self.ran_when_started: bool | None = None
        self.ran_when_stopped: bool | None = None
        FakeWatcher.instances.append(self)

    def __enter__(self) -> "FakeWatcher":
        self.ran_when_started = RecordingAssistant.instances[0].ran_with is not None
        return self

    def __exit__(self, *exc) -> None:
        self.ran_when_stopped = RecordingAssistant.instances[0].ran_with is not None


@pytest.fixture
def watchers(monkeypatch) -> list[FakeWatcher]:
    FakeWatcher.instances = []
    monkeypatch.setattr(cli, "GachaWatcher", FakeWatcher)
    return FakeWatcher.instances


def test_run_with_the_games_on_gives_the_brain_the_game_tools_and_watches_while_listening(
    home, run_fakes, brains, games, watchers
):
    enable_gacha(home)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [tools, memory_tools, schedule] = brains[0].providers
    assert (type(tools), type(memory_tools), type(schedule)) == (
        GachaTools,
        MemoryTools,
        ScheduleTools,
    )
    assert [spec.name for spec in tools.specs()] == ["game_status", "gacha_history"]
    [watcher] = watchers
    assistant = RecordingAssistant.instances[0]
    assert watcher.hoyolab is games.hoyolab
    assert watcher.title == "Cyrene"
    assert watcher.notifier is None  # notify is off in the example config
    assert (watcher.ran_when_started, watcher.ran_when_stopped) == (False, True)
    watcher.announce("Master, resin penuh.", "surprised")
    assert assistant.announced == [("Master, resin penuh.", "surprised")]


def test_run_without_a_cookie_has_the_game_tools_but_no_watcher(
    home, run_fakes, brains, games, watchers
):
    enable_gacha(home, cookie=None)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        GachaTools,
        MemoryTools,
        ScheduleTools,
    ]
    assert watchers == []


def test_run_with_the_games_off_builds_none_of_it(home, run_fakes, brains, games, watchers):
    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert (games.built, watchers) == ([], [])
    assert [type(tools) for tools in brains[0].providers] == [MemoryTools, ScheduleTools]


def test_chat_with_the_games_on_gives_the_brain_the_game_tools(home, brains, games, monkeypatch):
    enable_gacha(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        GachaTools,
        MemoryTools,
        ScheduleTools,
    ]


def test_check_reports_the_games_off(home, nothing_installed, capsys):
    cli.main(["check"])

    out = capsys.readouterr().out
    assert "! Teman main game mati (gacha.enabled: false)" in out
    assert "! Notifikasi HP mati (notify.enabled: false)" in out


def test_check_reports_the_cookie_keys_and_each_games_history_cache(
    home, nothing_installed, tmp_path, monkeypatch, capsys
):
    enable_gacha(home)
    locallow = tmp_path / "LocalLow"
    data_dir = tmp_path / "Games" / "StarRail_Data"
    log = locallow / "Cognosphere" / "Star Rail" / "Player.log"
    log.parent.mkdir(parents=True)
    log.write_text(f"Loading from {data_dir.as_posix()}/data.unity3d\n", encoding="utf-8")
    data_2 = data_dir / "webCaches" / "2.54.0.0" / "Cache" / "Cache_Data" / "data_2"
    data_2.parent.mkdir(parents=True)
    data_2.write_bytes(
        b"\x00https://public-operation-hkrpg-sg.hoyoverse.com/common/hkrpg_gacha_record/api/"
        b"getGachaLog?authkey_ver=1&authkey=SECRETKEY&gacha_type=11\x00"
    )
    monkeypatch.setattr(cli, "GAME_LOGS_DIR", locallow)

    cli.main(["check"])

    out = capsys.readouterr().out
    assert "✓ Cookie HoYoLAB ada, termasuk cookie_token_v2 untuk riwayat pull" in out
    assert "✓ Riwayat HSR: alamat riwayat ada di cache game" in out
    assert "! Riwayat Genshin: belum ada di cache game; buka riwayat wish di game sekali" in out
    for secret in ("SECRETCOOKIE", "SECRETTOKEN", "SECRETKEY"):
        assert secret not in out


def test_check_warns_about_a_cookie_without_the_history_token(home, nothing_installed, capsys):
    enable_gacha(home, cookie="ltuid_v2=1; ltoken_v2=v2_SECRETCOOKIE")

    cli.main(["check"])

    out = capsys.readouterr().out
    assert "! Cookie HoYoLAB ada tanpa cookie_token_v2" in out
    assert "SECRETCOOKIE" not in out


def test_check_shows_only_the_start_of_the_ntfy_topic(home, nothing_installed, capsys):
    enable_notify(home)

    cli.main(["check"])

    out = capsys.readouterr().out
    assert (
        "✓ Notifikasi HP lewat https://ntfy.sh, topik cyre…; uji dengan: waifu notify-test" in out
    )
    assert NOTIFY_TOPIC not in out


def test_build_gacha_follows_the_config(home):
    enable_gacha(home, extra="  uids: {hsr: 800000001}\n")

    parts = cli.build_gacha(cli.load_config(home / "config.yaml"))

    assert isinstance(parts.hoyolab, HoyolabClient)
    assert parts.hoyolab.has_cookie_token
    assert isinstance(parts.history, GachaHistory)


def test_gacha_accounts_marks_the_account_she_uses_when_levels_tie(home, games, capsys):
    games.hoyolab.account_list = [
        Account("hsr", 800000001, "Main", 70, "Asia"),
        Account("hsr", 800000002, "Alt", 70, "Asia"),
    ]

    assert cli.main(["gacha", "accounts"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("→ HSR      800000001")
    assert lines[1].startswith("  HSR      800000002")


# --- long-term memory --------------------------------------------------------------------------


def memory_file(home: Path) -> Path:
    """Where the example config keeps her notes, inside the test's working directory."""
    return home / "data" / "memory.json"


def saved_notes(home: Path) -> list[str]:
    return [fact.text for fact in MemoryStore(memory_file(home)).facts()]


def disable_memory(home: Path) -> None:
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    disabled = text.replace("memory:\n  enabled: true\n", "memory:\n  enabled: false\n")
    assert disabled != text
    config.write_text(disabled, encoding="utf-8")


def test_memory_outside_tmp_path_is_refused_so_no_test_touches_the_owner_s_notes():
    with pytest.raises(AssertionError, match="MemoryStore outside tmp_path"):
        cli.MemoryStore(ROOT / "data" / "memory.json")


def test_run_gives_the_brain_her_memory_tools_and_the_notes_at_each_session_start(
    home, run_fakes, brains
):
    write_config(home, "memory:\n  path: notes/ingatan.json\n  max_chars: 30\n")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    brain = brains[0]
    [tools, _] = brain.providers  # then the schedule tools
    assert isinstance(tools, MemoryTools)
    assert brain.memory_section() == memory_prompt_section([])
    assert tools.run("remember", {"text": "Master alergi udang."})[1] is False
    notes = MemoryStore(home / "notes" / "ingatan.json").facts()
    assert [fact.text for fact in notes] == ["Master alergi udang."]
    assert brain.memory_section() == memory_prompt_section(notes)  # read again, not cached
    result, is_error = tools.run("remember", {"text": "Master suka kopi susu tanpa gula."})
    assert is_error and "30 karakter" in result


def test_chat_gives_the_brain_the_notes_saved_in_data_memory_json(home, brains, monkeypatch):
    MemoryStore(memory_file(home)).add("Master suka kopi.")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [MemoryTools, ScheduleTools]
    assert "Master suka kopi." in brains[0].memory_section()


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_the_memory_off_the_brain_gets_neither_the_tools_nor_the_notes(
    argv, home, run_fakes, brains, monkeypatch
):
    disable_memory(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [ScheduleTools]
    assert brains[0].memory_section is None
    assert not memory_file(home).exists()


def test_memory_needs_an_action():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["memory"])


def test_memory_list_without_notes_says_so_and_creates_no_file(home, capsys):
    assert cli.main(["memory", "list"]) == 0

    assert capsys.readouterr().out == "Belum ada catatan.\n"
    assert not memory_file(home).exists()


def test_memory_list_prints_each_note_with_its_id_and_the_date_it_last_changed(home, capsys):
    facts = [
        {"id": "m3", "text": "Master alergi udang.", "updated": "2026-10-12T08:30"},
        {"id": "m12", "text": "Master ujian Jumat 16-10-2026.", "updated": "2026-10-13T21:05"},
    ]
    memory_file(home).parent.mkdir()
    memory_file(home).write_text(
        json.dumps(
            {
                "version": 1,
                "next_id": 13,
                "facts": [{**fact, "created": "2026-10-01T09:00"} for fact in facts],
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(["memory", "list"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "m3   12-10-2026  Master alergi udang.",
        "m12  13-10-2026  Master ujian Jumat 16-10-2026.",
    ]


def test_memory_add_saves_the_words_as_one_note_also_with_the_memory_off(home, capsys):
    disable_memory(home)

    assert cli.main(["memory", "add", "Master", "alergi", "udang."]) == 0

    assert capsys.readouterr().out == "✓ Tersimpan sebagai m1: Master alergi udang.\n"
    assert saved_notes(home) == ["Master alergi udang."]


def test_memory_add_of_a_note_already_saved_names_that_note(home, capsys):
    MemoryStore(memory_file(home)).add("Master alergi udang.")

    assert cli.main(["memory", "add", "master ALERGI udang."]) == 0

    assert capsys.readouterr().out == "✓ Catatan itu sudah ada sebagai m1: Master alergi udang.\n"
    assert saved_notes(home) == ["Master alergi udang."]


@pytest.mark.parametrize(
    ("words", "reason"),
    [
        (["PIN", "ATM-ku", "4821"], "rahasia"),
        (["kopi" * 51], "terlalu panjang"),
        (["  "], "kosong"),
    ],
)
def test_memory_add_prints_why_a_note_is_refused_and_exits_1(words, reason, home, capsys):
    assert cli.main(["memory", "add", *words]) == 1

    out = capsys.readouterr().out
    assert out.startswith("✗ ") and reason in out
    assert "4821" not in out
    assert not memory_file(home).exists()


def test_memory_add_keeps_to_the_number_of_notes_set_in_the_config(home, capsys):
    write_config(home, "memory:\n  path: notes/ingatan.json\n  max_facts: 1\n")
    assert cli.main(["memory", "add", "Master", "alergi", "udang."]) == 0
    capsys.readouterr()

    assert cli.main(["memory", "add", "Master", "suka", "kopi."]) == 1

    out = capsys.readouterr().out
    assert out.startswith("✗ ") and "penuh" in out
    notes = MemoryStore(home / "notes" / "ingatan.json").facts()
    assert [fact.text for fact in notes] == ["Master alergi udang."]


def test_memory_forget_deletes_one_note_and_says_which(home, capsys):
    store = MemoryStore(memory_file(home))
    store.add("Master alergi udang.")
    store.add("Master suka kopi.")

    assert cli.main(["memory", "forget", "m1"]) == 0

    assert capsys.readouterr().out == "✓ Dihapus m1: Master alergi udang.\n"
    assert saved_notes(home) == ["Master suka kopi."]


def test_memory_forget_of_an_unknown_id_fails_with_the_reason(home, capsys):
    assert cli.main(["memory", "forget", "m9"]) == 1

    assert capsys.readouterr().out == "✗ Tidak ada catatan dengan id itu.\n"


def test_memory_clear_without_yes_only_says_how_many_would_go_and_how_to_confirm(home, capsys):
    store = MemoryStore(memory_file(home))
    store.add("Master alergi udang.")
    store.add("Master suka kopi.")

    assert cli.main(["memory", "clear"]) == 0

    out = capsys.readouterr().out
    assert "2 catatan" in out and "waifu memory clear --yes" in out
    assert saved_notes(home) == ["Master alergi udang.", "Master suka kopi."]


def test_memory_clear_yes_deletes_every_note(home, capsys):
    store = MemoryStore(memory_file(home))
    store.add("Master alergi udang.")
    store.add("Master suka kopi.")

    assert cli.main(["memory", "clear", "--yes"]) == 0

    assert capsys.readouterr().out == "✓ 2 catatan dihapus.\n"
    assert saved_notes(home) == []


@pytest.mark.parametrize("argv", [["memory", "clear"], ["memory", "clear", "--yes"]])
def test_memory_clear_without_notes_has_nothing_to_delete(argv, home, capsys):
    assert cli.main(argv) == 0

    assert capsys.readouterr().out == "Belum ada catatan; tidak ada yang dihapus.\n"
    assert not memory_file(home).exists()


def memory_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Ingatan" in line or "ingatan" in line]
    return line


def test_check_counts_her_notes_and_names_the_memory_file(home, nothing_installed, capsys):
    MemoryStore(memory_file(home)).add("Master alergi udang.")

    cli.main(["check"])

    line = memory_line(capsys.readouterr().out)
    assert line == f"✓ Ingatan: 1 catatan ({Path('data/memory.json')})"


def test_check_reports_the_memory_off(home, nothing_installed, capsys):
    disable_memory(home)

    cli.main(["check"])

    assert memory_line(capsys.readouterr().out) == "! Ingatan mati (memory.enabled: false)"


def test_check_shows_where_a_broken_memory_file_was_moved(home, nothing_installed, capsys):
    memory_file(home).parent.mkdir()
    memory_file(home).write_text("{catatan yang rusak", encoding="utf-8")

    cli.main(["check"])

    [moved] = memory_file(home).parent.glob("memory.json.corrupt-*")
    line = memory_line(capsys.readouterr().out)
    assert line.startswith(f"! Ingatan: 0 catatan ({Path('data/memory.json')})")
    assert str(Path("data") / moved.name) in line
    assert moved.read_text(encoding="utf-8") == "{catatan yang rusak"
    assert not memory_file(home).exists()


def test_check_fails_a_memory_file_it_cannot_read(home, nothing_installed, capsys):
    memory_file(home).mkdir(parents=True)  # a folder where the file should be

    cli.main(["check"])

    line = memory_line(capsys.readouterr().out)
    assert line.startswith(f"✗ File ingatan {Path('data/memory.json')} tidak bisa dibaca")


# --- schedules, reminders, and routines ------------------------------------------------------


def test_schedule_outside_tmp_path_is_refused_so_no_test_touches_the_owner_s_schedule():
    with pytest.raises(AssertionError, match="ScheduleStore outside tmp_path"):
        cli.ScheduleStore(ROOT / "data" / "schedule.json")


def schedule_file(home: Path) -> Path:
    """Where the example config keeps her schedule, inside the test's working directory."""
    return home / "data" / "schedule.json"


def disable_schedule(home: Path) -> None:
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    disabled = text.replace("schedule:\n  enabled: true\n", "schedule:\n  enabled: false\n")
    assert disabled != text
    config.write_text(disabled, encoding="utf-8")


def enable_example_routines(home: Path) -> None:
    """The example config with its commented `routines` example written out (the `tidur`
    routine: lampu_kamar off, ac temp 26)."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    commented = text[text.index("# routines:\n") :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())
    config.write_text(text.replace(commented, written_out), encoding="utf-8")


def tomorrow_at(hour: int) -> datetime:
    """A local time the schedule accepts whenever the test runs."""
    return (datetime.now() + timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def schedule_tools(brain: HouseBrain) -> ScheduleTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, ScheduleTools)]
    return tools


def schedule_at(tools: ScheduleTools, at: datetime, **action) -> tuple[str, bool]:
    return tools.run("schedule", {"at": f"{at:%Y-%m-%d %H:%M}", **action})


def listening() -> bool:
    return any(assistant.ran_with is not None for assistant in RecordingAssistant.instances)


class FakeScheduler:
    """Stands in for Scheduler without a thread, remembering whether `waifu run` listened when
    it started and stopped. `tick_at(moment)` runs one tick of a real Scheduler built with the
    same arguments, to see what would fire at that moment."""

    def __init__(self, store, **arguments) -> None:
        self.store = store
        self.arguments = arguments
        self.states: list[str] = []
        self.listening: list[bool] = []

    def __enter__(self) -> "FakeScheduler":
        self.states.append("started")
        self.listening.append(listening())
        return self

    def __exit__(self, *exc_info) -> None:
        self.states.append("stopped")
        self.listening.append(listening())

    def tick_at(self, moment: datetime) -> None:
        Scheduler(self.store, **self.arguments, now=lambda: moment).tick_once()


@pytest.fixture
def schedulers(monkeypatch) -> list[FakeScheduler]:
    built: list[FakeScheduler] = []
    RecordingAssistant.instances = []

    def build(store, **arguments) -> FakeScheduler:
        built.append(FakeScheduler(store, **arguments))
        return built[-1]

    monkeypatch.setattr(cli, "Scheduler", build)
    return built


def scheduler_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "scheduler"]


MEDICINE = "Master, sudah jam tiga, waktunya minum obat!"


def test_run_gives_the_brain_the_schedule_tools_and_fires_her_schedule_while_she_listens(
    home, run_fakes, brains, schedulers, monkeypatch
):
    enable_example_routines(home)
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)
    at = tomorrow_at(15)
    refused: list[bool] = []
    left: list[int] = []

    def listen(self, mic, segmenter) -> None:
        # A dry run's schedule lasts as long as the session, so everything happens in it.
        self.ran_with = (mic, segmenter)
        for action in (
            {"reminder": MEDICINE},
            {"device": "ac", "command": "temp", "value": 24},
            {"routine": "tidur"},
        ):
            refused.append(schedule_at(schedule_tools(brains[0]), at, **action)[1])
        schedulers[0].tick_at(at)
        left.append(len(schedulers[0].store.entries()))

    monkeypatch.setattr(RecordingAssistant, "run", listen)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    brain = brains[0]
    assert [type(tools) for tools in brain.providers] == [MemoryTools, ScheduleTools]
    [scheduler] = schedulers
    assert scheduler.states == ["started", "stopped"]
    assert scheduler.listening == [False, True]
    assert refused == [False, False, False]
    # The reminder is said and pushed; the device command and the routine run without a word.
    assert RecordingAssistant.instances[0].announced == [(MEDICINE, "happy")]
    assert notifier.sent == [("Cyrene", MEDICINE, ("alarm_clock",))]
    assert brain.registry.get("ac").calls == [("temp_24", 24), ("temp_26", 26)]
    assert brain.registry.get("lampu_kamar").calls == [("off", None)]
    assert left == [0]


def test_run_keeps_her_schedule_where_and_as_the_config_says(home, run_fakes, brains, schedulers):
    write_config(
        home, "schedule:\n  path: jadwal/jadwal.json\n  max_entries: 1\n  late_grace_minutes: 5\n"
    )

    # Not a dry run, which keeps a schedule of its own; this config has no devices to reach.
    assert cli.main(["run", "--no-speaker-check"]) == 0

    tools = schedule_tools(brains[0])
    at = tomorrow_at(7)
    assert schedule_at(tools, at, reminder="Master, bangun!")[1] is False
    result, is_error = schedule_at(tools, at, reminder="Master, sarapan!")
    assert is_error and "penuh" in result
    store = ScheduleStore(home / "jadwal" / "jadwal.json")
    assert [entry.action for entry in store.entries()] == [Reminder("Master, bangun!")]
    schedulers[0].tick_at(at + timedelta(minutes=6))  # later than the 5 minutes of grace
    assert RecordingAssistant.instances[0].announced == []
    assert store.entries() == []


def test_run_checks_her_schedule_in_a_thread_only_while_she_listens(run_fakes, monkeypatch):
    threads_while_listening: list[int] = []

    def listen(self, mic, segmenter) -> None:
        threads_while_listening.append(len(scheduler_threads()))

    monkeypatch.setattr(RecordingAssistant, "run", listen)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert threads_while_listening == [1]
    assert scheduler_threads() == []


def test_chat_prints_what_her_schedule_fires_and_pushes_nothing_to_the_phone(
    home, brains, schedulers, monkeypatch, capsys
):
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat"]) == 0  # no `waifu run` is running

    [scheduler] = schedulers
    assert scheduler.states == ["started", "stopped"]
    assert scheduler.store.path.resolve() == schedule_file(home).resolve()
    at = tomorrow_at(15)
    assert schedule_at(schedule_tools(brains[0]), at, reminder=MEDICINE)[1] is False
    capsys.readouterr()
    scheduler.tick_at(at)
    assert capsys.readouterr().out == f"Cyrene: {MEDICINE}\n"
    assert notifier.sent == []


def test_chat_leaves_firing_her_schedule_to_a_running_waifu_run(
    home, brains, schedulers, monkeypatch, capsys
):
    # Two schedulers on one file: whichever ticks first takes the entry, and in chat it would
    # only be printed, never spoken or pushed.
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    with InstanceLock(home / "data" / "run.lock"):
        assert cli.main(["chat"]) == 0

    assert schedulers == []
    assert cli.SCHEDULE_IN_RUN in capsys.readouterr().out
    # She still makes, lists, and cancels entries, for the running Cyrene to fire.
    assert schedule_at(schedule_tools(brains[0]), tomorrow_at(15), reminder=MEDICINE)[1] is False
    assert [entry.action for entry in ScheduleStore(schedule_file(home)).entries()] == [
        Reminder(MEDICINE)
    ]


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_a_dry_run_keeps_a_schedule_of_its_own_and_never_touches_the_real_one(
    argv, home, run_fakes, brains, schedulers, monkeypatch, capsys
):
    saved_schedule(schedule_file(home))  # Master's real schedule: j2 is "ac off" at 02:00
    real = schedule_file(home).read_bytes()
    refused: list[bool] = []

    def try_a_schedule(*args, **kwargs) -> BrainReply:
        # Master tries a schedule in the dry run while the real "ac off" comes due.
        tools = schedule_tools(brains[0])
        refused.append(
            schedule_at(tools, tomorrow_at(15), device="ac", command="temp", value=24)[1]
        )
        schedulers[0].tick_at(datetime(2026, 10, 14, 2, 0, 5))
        return BrainReply("Siap, Master.", [])

    monkeypatch.setattr(RecordingAssistant, "run", try_a_schedule)
    monkeypatch.setattr(HouseBrain, "respond", try_a_schedule)
    monkeypatch.setattr(sys, "stdin", io.StringIO("matikan AC jam 2\nkeluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert refused == [False]
    assert schedule_file(home).read_bytes() == real  # neither used up nor added to
    assert brains[0].registry.get("ac").calls == []
    [scheduler] = schedulers
    assert not scheduler.store.path.parent.exists()  # the throwaway schedule ended with it
    assert cli.DRY_RUN_SCHEDULE.format(path=Path("data/schedule.json")) in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_the_schedule_off_she_gets_neither_the_tools_nor_a_scheduler(
    argv, home, run_fakes, brains, schedulers, monkeypatch
):
    disable_schedule(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [MemoryTools]
    assert schedulers == []
    assert not schedule_file(home).exists()


SAVED_ON = datetime(2026, 10, 13, 14, 30)  # a Tuesday


def saved_schedule(path: Path) -> ScheduleStore:
    """A schedule saved on SAVED_ON: reminders, device commands, and a routine, some repeating."""
    store = ScheduleStore(path, now=lambda: SAVED_ON)
    store.add(datetime(2026, 10, 13, 15, 0), "none", Reminder(MEDICINE))
    store.add(datetime(2026, 10, 14, 2, 0), "none", DeviceAction("ac", "off"))
    store.add(datetime(2026, 10, 13, 23, 0), "daily", RoutineAction("tidur"))
    store.add(datetime(2026, 10, 17, 7, 0), "weekdays", Reminder("Master, bangun!"))  # Monday
    store.add(datetime(2026, 10, 17, 22, 0), "weekends", DeviceAction("ac", "temp", 26))
    return store


def scheduled_ids(path: Path) -> list[str]:
    return [entry.id for entry in ScheduleStore(path).entries()]


def test_schedule_and_routine_need_an_action():
    for command in ("schedule", "routine"):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args([command])


def test_schedule_list_without_entries_says_so_and_creates_no_file(home, capsys):
    assert cli.main(["schedule", "list"]) == 0

    assert capsys.readouterr().out == "Belum ada jadwal.\n"
    assert not schedule_file(home).exists()


def test_schedule_list_prints_each_entry_soonest_first_also_with_the_schedule_off(home, capsys):
    saved_schedule(schedule_file(home))
    disable_schedule(home)

    assert cli.main(["schedule", "list"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "ID  BERIKUTNYA               ULANG             ISI",
        f'j1  Selasa 13-10-2026 15:00  sekali            pengingat "{MEDICINE}"',
        "j3  Selasa 13-10-2026 23:00  tiap hari         rutinitas tidur",
        "j2  Rabu 14-10-2026 02:00    sekali            perangkat ac off",
        "j5  Sabtu 17-10-2026 22:00   tiap akhir pekan  perangkat ac temp 26",
        'j4  Senin 19-10-2026 07:00   tiap hari kerja   pengingat "Master, bangun!"',
    ]


def test_schedule_cancel_deletes_one_entry_and_says_which(home, capsys):
    saved_schedule(schedule_file(home))

    assert cli.main(["schedule", "cancel", "j2"]) == 0

    assert capsys.readouterr().out == (
        "✓ Dibatalkan j2: perangkat ac off, Rabu 14-10-2026 02:00, sekali\n"
    )
    assert scheduled_ids(schedule_file(home)) == ["j1", "j3", "j5", "j4"]


def test_schedule_cancel_of_an_unknown_id_fails_with_the_reason(home, capsys):
    assert cli.main(["schedule", "cancel", "j9"]) == 1

    assert capsys.readouterr().out == "✗ Tidak ada jadwal dengan id itu.\n"
    assert not schedule_file(home).exists()


def test_schedule_clear_without_yes_only_says_how_many_would_go_and_how_to_confirm(home, capsys):
    saved_schedule(schedule_file(home))

    assert cli.main(["schedule", "clear"]) == 0

    out = capsys.readouterr().out
    assert "5 jadwal" in out and "waifu schedule clear --yes" in out
    assert len(scheduled_ids(schedule_file(home))) == 5


def test_schedule_clear_yes_deletes_every_entry_in_the_file_the_config_names(home, capsys):
    write_config(home, "schedule:\n  path: jadwal/jadwal.json\n")
    saved_schedule(home / "jadwal" / "jadwal.json")

    assert cli.main(["schedule", "clear", "--yes"]) == 0

    assert capsys.readouterr().out == "✓ 5 jadwal dihapus.\n"
    assert scheduled_ids(home / "jadwal" / "jadwal.json") == []


@pytest.mark.parametrize("argv", [["schedule", "clear"], ["schedule", "clear", "--yes"]])
def test_schedule_clear_without_entries_has_nothing_to_delete(argv, home, capsys):
    assert cli.main(argv) == 0

    assert capsys.readouterr().out == "Belum ada jadwal; tidak ada yang dihapus.\n"
    assert not schedule_file(home).exists()


@pytest.fixture
def registries(monkeypatch) -> list[tuple[DeviceRegistry, bool]]:
    """Every registry a command builds, with whether it was the dry-run one."""
    built: list[tuple[DeviceRegistry, bool]] = []
    real_build = cli.build_registry

    def build(config, *, dry_run=False) -> DeviceRegistry:
        built.append((real_build(config, dry_run=dry_run), dry_run))
        return built[-1][0]

    monkeypatch.setattr(cli, "build_registry", build)
    return built


def test_routine_list_describes_each_routine_of_the_config(home, capsys):
    enable_example_routines(home)

    assert cli.main(["routine", "list"]) == 0

    assert capsys.readouterr().out == "tidur: Mode tidur (lampu_kamar off, ac temp 26)\n"


def test_routine_list_without_routines_says_where_to_write_them(home, capsys):
    assert cli.main(["routine", "list"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("Belum ada rutinitas.") and "config.yaml" in out


def test_routine_run_with_dry_run_runs_every_step_on_the_simulated_devices(
    home, registries, capsys
):
    enable_example_routines(home)

    assert cli.main(["routine", "run", "tidur", "--dry-run"]) == 0

    [(registry, dry_run)] = registries
    assert dry_run is True
    assert registry.get("lampu_kamar").calls == [("off", None)]
    assert registry.get("ac").calls == [("temp_26", 26)]
    assert capsys.readouterr().out.splitlines() == [
        "Menjalankan rutinitas tidur (perangkat simulasi)...",
        "✓ Langkah 1 (lampu_kamar off): Lampu kamar: perintah 'off' berhasil (simulasi).",
        "✓ Langkah 2 (ac temp 26): AC Samsung: perintah 'temp_26' berhasil (simulasi).",
    ]


def test_routine_run_goes_on_after_a_failed_step_and_exits_1(home, registries, capsys):
    with (home / "config.yaml").open("a", encoding="utf-8") as config:
        config.write(
            "routines:\n"
            "  film:\n"
            "    steps:\n"
            "      - {device: proyektor, command: terbang}\n"
            "      - {device: lampu_kamar, command: off}\n"
        )

    assert cli.main(["routine", "run", "film", "--dry-run"]) == 1

    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("✗ Langkah 1 (proyektor terbang): ")
    assert "tidak punya perintah 'terbang'" in lines[1]
    assert lines[2].startswith("✓ Langkah 2 (lampu_kamar off): ")
    assert registries[0][0].get("lampu_kamar").calls == [("off", None)]


def test_routine_run_without_dry_run_uses_the_devices_of_the_config(home, registries, capsys):
    write_config(
        home,
        "devices:\n"
        "  - {id: kipas, name: Kipas, type: mock}\n"
        "routines:\n"
        "  angin:\n"
        "    steps:\n"
        "      - {device: kipas, command: on}\n",
    )

    assert cli.main(["routine", "run", "angin"]) == 0

    [(registry, dry_run)] = registries
    assert dry_run is False
    assert registry.get("kipas").calls == [("on", None)]
    assert capsys.readouterr().out.splitlines()[0] == "Menjalankan rutinitas angin..."


def test_routine_run_of_an_unknown_routine_names_the_ones_there_are(home, registries, capsys):
    enable_example_routines(home)

    assert cli.main(["routine", "run", "pesta", "--dry-run"]) == 1

    assert capsys.readouterr().out == (
        "✗ Tidak ada rutinitas bernama 'pesta' di config.yaml. Rutinitas yang ada: tidur.\n"
    )
    assert registries == []


def schedule_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Jadwal" in line or "jadwal" in line]
    return line


def routine_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if "Rutinitas" in line]


def test_check_counts_her_schedule_and_names_the_schedule_file(home, nothing_installed, capsys):
    saved_schedule(schedule_file(home))

    cli.main(["check"])

    line = schedule_line(capsys.readouterr().out)
    assert line == f"✓ Jadwal: 5 entri ({Path('data/schedule.json')})"


def test_check_reports_the_schedule_off(home, nothing_installed, capsys):
    disable_schedule(home)

    cli.main(["check"])

    assert schedule_line(capsys.readouterr().out) == "! Jadwal mati (schedule.enabled: false)"


def test_check_shows_where_a_broken_schedule_file_was_moved(home, nothing_installed, capsys):
    schedule_file(home).parent.mkdir()
    schedule_file(home).write_text("{jadwal yang rusak", encoding="utf-8")

    cli.main(["check"])

    [moved] = schedule_file(home).parent.glob("schedule.json.corrupt-*")
    line = schedule_line(capsys.readouterr().out)
    assert line.startswith(f"! Jadwal: 0 entri ({Path('data/schedule.json')})")
    assert str(Path("data") / moved.name) in line
    assert moved.read_text(encoding="utf-8") == "{jadwal yang rusak"
    assert not schedule_file(home).exists()


def test_check_fails_a_schedule_file_it_cannot_read(home, nothing_installed, capsys):
    schedule_file(home).mkdir(parents=True)  # a folder where the file should be

    cli.main(["check"])

    line = schedule_line(capsys.readouterr().out)
    assert line.startswith(f"✗ File jadwal {Path('data/schedule.json')} tidak bisa dibaca")


def test_check_names_each_routine_step_the_real_devices_cannot_run(home, nothing_installed, capsys):
    enable_example_routines(home)  # the AC has learned no IR code yet

    cli.main(["check"])

    assert routine_lines(capsys.readouterr().out) == [
        "✗ Rutinitas tidur: Langkah 2 (ac temp 26): AC Samsung tidak punya perintah 'temp' "
        "dengan nilai 26."
    ]


def test_check_marks_a_routine_whose_every_step_can_run(home, nothing_installed, capsys):
    enable_example_routines(home)
    IrCodeStore(home / "data" / "ir_codes.json").put("ac", "temp_26", "JgBQAAABKJIUEhQ3")

    cli.main(["check"])

    assert routine_lines(capsys.readouterr().out) == [
        "✓ Rutinitas tidur: Mode tidur (lampu_kamar off, ac temp 26)"
    ]


def test_check_fails_a_routine_whose_ir_codes_cannot_be_read(home, nothing_installed, capsys):
    enable_example_routines(home)
    (home / "data").mkdir()
    (home / "data" / "ir_codes.json").write_text("{kode yang rusak", encoding="utf-8")

    cli.main(["check"])

    [line] = routine_lines(capsys.readouterr().out)
    assert line.startswith("✗ Rutinitas tidur: kode IR tidak bisa dibaca (")
