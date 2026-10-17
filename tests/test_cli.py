import asyncio
import base64
import hashlib
import inspect
import io
import json
import logging
import os
import re
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import types
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path, PureWindowsPath

import anthropic
import genshin
import httpx
import httpx2
import numpy as np
import pytest
from genshin.models import HSREventStatus
from genshin.models.model import prevent_enum_error
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
from waifu.brain import claude as claude_module
from waifu.brain.attachments import CameraTurn, TurnImages
from waifu.brain.base import Brain, BrainReply, ToolCallRecord
from waifu.brain.finance_tools import FinanceTools
from waifu.brain.focus_tools import FocusTools
from waifu.brain.memory_tools import MemoryTools
from waifu.brain.music_tools import MusicTools
from waifu.brain.offers_tools import OffersTools
from waifu.brain.pc_tools import PcTools
from waifu.brain.persona import WEEKDAYS_ID
from waifu.brain.play_time_tools import PlayTimeTools
from waifu.brain.schedule_tools import ScheduleTools
from waifu.brain.screen_tools import ScreenTools
from waifu.brain.todo_tools import TodoTools
from waifu.brain.tools import to_anthropic
from waifu.brain.usage_tools import UsageTools
from waifu.briefing import BRIEFING_PROMPT, DAILY_BRIEFING, BriefingTimer, BriefingTools
from waifu.config import (
    AvatarConfig,
    FreeOffersConfig,
    MusicConfig,
    PcHealthConfig,
    PlayTimeConfig,
    RainAlertConfig,
    RedeemCodesConfig,
    RvcConfig,
    TodoConfig,
    VersionNoticeConfig,
    WeeklyReportConfig,
)
from waifu.devices.base import DeviceError
from waifu.devices.ir_codes import IrCodeStore
from waifu.devices.ir_device import IrDevice
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.finance.tepira import TepiraClient
from waifu.finance.values import (
    FINANCE_TOOLS,
    READ_TOOLS,
    TOKEN_REJECTED,
    WIB,
    FinanceError,
    RecordedExpense,
    UndoneExpense,
)
from waifu.focus import FocusBlock, FocusStore
from waifu.focus_timer import FocusTimer
from waifu.gacha.code_store import CodeStore
from waifu.gacha.code_tools import CodeTools
from waifu.gacha.codes import CodesError, RedeemResult
from waifu.gacha.game_data import (
    GAME_MODES,
    Banner,
    ChallengeWindow,
    Endgame,
    GameCalendar,
    GameEvent,
)
from waifu.gacha.game_data_tools import GameDataTools
from waifu.gacha.games import GAMES
from waifu.gacha.genshin_impact import GenshinCharacter, GenshinWeapon
from waifu.gacha.history import (
    BannerStats,
    GachaHistory,
    GachaRecord,
    GachaStore,
    HistoryReport,
    TopPull,
)
from waifu.gacha.hoyolab import (
    NO_COOKIE,
    REDEEM_MESSAGES,
    Account,
    CheckinResult,
    GachaError,
    GameStatus,
    HoyolabClient,
    WeeklyTask,
)
from waifu.gacha.starrail import (
    ENDGAME_MODES,
    HsrCalendar,
    HsrChallengeWindow,
    HsrCharacter,
    HsrEndgame,
    HsrEvent,
    HsrFloor,
    HsrLightCone,
    HsrWarp,
)
from waifu.gacha.tools import GachaTools
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT, WeeklyReportTimer, WeeklyReportTools
from waifu.gacha.zenless import ZzzAgent
from waifu.memory import MemoryStore, memory_prompt_section
from waifu.offers.errors import OffersError
from waifu.offers.store import OffersStore
from waifu.offers.values import AiOffer, GameGiveaway
from waifu.pc_health import Drive, GpuStatus, PcSnapshot
from waifu.phone.auth import CODE_LIFETIME, PhoneAuth, Refusal
from waifu.phone.server import PhoneError, PhoneReply
from waifu.play_time import PlayTimeStore, Session
from waifu.processes import running_executables
from waifu.schedule import DeviceAction, Reminder, RoutineAction, ScheduleStore
from waifu.scheduler import MONTHS_ID, Scheduler
from waifu.screen import ScreenImage
from waifu.single_instance import InstanceLock
from waifu.todo import TodoStore
from waifu.usage import TokenUsage, UsageLedger, UsageSummary
from waifu.version import CodeVersion, Release, RunningVersion, read_running, write_running
from waifu.voice.base import SpeakerEmbedder, SpeechToText, Transcript
from waifu.voice.rvc import RvcError
from waifu.voice.speaker import SpeakerVerifier
from waifu.voice.stt import SttError, WhisperStt
from waifu.voice.tts import TtsError
from waifu.voice.windows_voices import WindowsVoice, WindowsVoiceError
from waifu.weather import WeatherClient, WeatherError

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
def no_real_todo_list(tmp_path, monkeypatch):
    """Every to-do store a command builds lives under tmp_path: a test that leaves the working
    directory at the repository fails instead of reading or writing the owner's tasks in
    data/todo.json (building a store touches no file, so the refusal comes first)."""
    monkeypatch.setattr(cli, "TodoStore", only_under(tmp_path, cli.TodoStore))


@pytest.fixture(autouse=True)
def no_real_usage_ledger(tmp_path, monkeypatch):
    """Every API cost ledger a command builds lives under tmp_path: a test that leaves the
    working directory at the repository fails instead of reading or writing the owner's
    records in data/usage.json (building a ledger touches no file, so the refusal comes
    first)."""
    monkeypatch.setattr(cli, "UsageLedger", only_under(tmp_path, cli.UsageLedger))


@pytest.fixture(autouse=True)
def no_real_phone(tmp_path, monkeypatch):
    """A test that forgets the phone fakes fails instead of binding port 9883, reading or
    writing the owner's paired phones in data/phone.json, or running the owner's Tailscale CLI
    (which `waifu phone setup` and `check` look for only under tmp_path here)."""

    def refuse_server(*args, **kwargs):
        raise AssertionError("a test reached the real phone server")

    def refuse_tailscale(command):
        raise AssertionError(f"a test ran the Tailscale CLI: {command}")

    monkeypatch.setattr(cli, "PhoneServer", refuse_server)
    monkeypatch.setattr(cli, "PhoneAuth", only_under(tmp_path, cli.PhoneAuth))
    monkeypatch.setattr(cli, "TAILSCALE_RUNNER", refuse_tailscale)
    monkeypatch.setattr(cli, "TAILSCALE_SEARCH_PATH", str(tmp_path / "PATH"))
    monkeypatch.setattr(
        cli, "TAILSCALE_INSTALLED", tmp_path / "Program Files" / "Tailscale" / "tailscale.exe"
    )


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
def no_real_weather(monkeypatch):
    """A test that forgets the Open-Meteo fake fails instead of asking the internet for the
    weather (tests/conftest.py refuses it too, but the briefing turns any weather failure into
    a problem field, so the refusal alone could go unnoticed). Yields the URLs it refused."""
    reached: list[str] = []

    def refuse(url: str, timeout_s: float) -> object:
        reached.append(url)
        raise AssertionError(f"a test reached Open-Meteo: {url}")

    monkeypatch.setattr(cli, "WeatherClient", lambda **place: WeatherClient(**place, fetch=refuse))
    yield reached
    assert not reached, f"a test asked Open-Meteo for the weather: {reached}"


class FakeBriefingTimer:
    """Stands in for BriefingTimer without a thread, remembering whether `waifu run` listened
    when it started and stopped. `tick_at(moment)` runs one check of a real BriefingTimer built
    with the same arguments, to see what it would ask for at that moment."""

    def __init__(self, config, *, request) -> None:
        self.config = config
        self.request = request
        self.listening: list[bool] = []

    def __enter__(self) -> "FakeBriefingTimer":
        self.listening.append(listening())
        return self

    def __exit__(self, *exc_info) -> None:
        self.listening.append(listening())

    def tick_at(self, moment: datetime) -> None:
        BriefingTimer(self.config, request=self.request, now=lambda: moment).tick_once()


@pytest.fixture(autouse=True)
def briefing_timers(monkeypatch) -> list[FakeBriefingTimer]:
    """The real timer greets by the wall clock: between 07:00 and 12:00 it would ask for the
    morning greeting in every `run` test, so every test gets the fake."""
    built: list[FakeBriefingTimer] = []

    def build(config, *, request) -> FakeBriefingTimer:
        built.append(FakeBriefingTimer(config, request=request))
        return built[-1]

    monkeypatch.setattr(cli, "BriefingTimer", build)
    return built


class FakeWeeklyReportTimer:
    """Stands in for WeeklyReportTimer without a thread, remembering whether `waifu run`
    listened when it started and stopped. `tick_at(moment)` runs one check of a real
    WeeklyReportTimer built with the same arguments, to see what it would ask for then."""

    def __init__(self, config, *, request) -> None:
        self.config = config
        self.request = request
        self.listening: list[bool] = []

    def __enter__(self) -> "FakeWeeklyReportTimer":
        self.listening.append(listening())
        return self

    def __exit__(self, *exc_info) -> None:
        self.listening.append(listening())

    def tick_at(self, moment: datetime) -> None:
        WeeklyReportTimer(self.config, request=self.request, now=lambda: moment).tick_once()


@pytest.fixture(autouse=True)
def weekly_report_timers(monkeypatch) -> list[FakeWeeklyReportTimer]:
    """The real timer reports by the wall clock: on a Sunday evening it would ask for the
    weekly game report in every `run` test with the games on, so every test gets the fake."""
    built: list[FakeWeeklyReportTimer] = []

    def build(config, *, request) -> FakeWeeklyReportTimer:
        built.append(FakeWeeklyReportTimer(config, request=request))
        return built[-1]

    monkeypatch.setattr(cli, "WeeklyReportTimer", build)
    return built


# The voices on the owner's PC before any language was added in Settings.
DAVID = WindowsVoice("Microsoft David", "en-US", "Male", "onecore")
ZIRA = WindowsVoice("Microsoft Zira", "en-US", "Female", "onecore")
MARK = WindowsVoice("Microsoft Mark", "en-US", "Male", "onecore")
ANDIKA = WindowsVoice("Microsoft Andika", "id-ID", "Male", "onecore")
HARUKA = WindowsVoice("Microsoft Haruka", "ja-JP", "Female", "onecore")


@dataclass
class FakeWindowsVoices:
    """Stands in for list_windows_voices, which would run PowerShell: answers with `voices`
    (the owner's English voices) or raises `error`, and counts the listings."""

    voices: list[WindowsVoice] = field(default_factory=lambda: [DAVID, ZIRA, MARK])
    error: Exception | None = None
    listings: int = 0

    def __call__(self) -> list[WindowsVoice]:
        self.listings += 1
        if self.error is not None:
            raise self.error
        return list(self.voices)


@pytest.fixture(autouse=True)
def windows_voices(monkeypatch) -> FakeWindowsVoices:
    """`check` and `waifu voices` list Windows' voices through PowerShell, which no test may
    start (tests/conftest.py refuses it too): every test gets this fake."""
    fake = FakeWindowsVoices()
    monkeypatch.setattr(cli, "list_windows_voices", fake)
    return fake


QUIET_PC = PcSnapshot(12.0, 9.5, 31.9, (Drive("C:", 120.0, 465.1),), None, 3600)


class FakeCpuLoad:
    """Stands in for pc_health.CpuLoad, which would ask Windows for the CPU times."""


class PcReadings:
    """Stands in for pc_health's readers: `read_snapshot` hands out `snapshot` and remembers
    the CPU sampler of each reading; `gpu_status` hands out `gpu` and counts its readings."""

    def __init__(self) -> None:
        self.snapshot = QUIET_PC
        self.gpu: GpuStatus | None = None
        self.samplers: list[FakeCpuLoad] = []
        self.gpu_reads = 0

    def read_snapshot(self, *, cpu: FakeCpuLoad) -> PcSnapshot:
        self.samplers.append(cpu)
        return self.snapshot

    def gpu_status(self) -> GpuStatus | None:
        self.gpu_reads += 1
        return self.gpu


@pytest.fixture(autouse=True)
def pc_readings(monkeypatch) -> PcReadings:
    """The example config reads the PC (pc_health is on by default) in `run`, `chat`, and
    `check`: every test gets these fakes instead of Windows' counters and nvidia-smi, which
    no test may reach."""
    readings = PcReadings()
    monkeypatch.setattr(cli, "CpuLoad", FakeCpuLoad)
    monkeypatch.setattr(cli, "read_snapshot", readings.read_snapshot)
    monkeypatch.setattr(cli, "gpu_status", readings.gpu_status)
    return readings


class FakePcWatcher:
    """Stands in for PcWatcher without a thread, remembering what it was given, whether
    `waifu run` listened when it started and stopped, and the test's `events` up to then."""

    def __init__(
        self, read, config, *, announce, notifier, owner_call, title, events: list[str]
    ) -> None:
        self.read, self.config, self.announce = read, config, announce
        self.notifier, self.owner_call, self.title = notifier, owner_call, title
        self._events = events
        self.listening: list[bool] = []
        self.events_when_started: list[str] | None = None
        self.events_when_stopped: list[str] | None = None

    def start(self) -> None:
        self.listening.append(listening())
        self.events_when_started = list(self._events)

    def stop(self) -> None:
        self.listening.append(listening())
        self.events_when_stopped = list(self._events)


@pytest.fixture(autouse=True)
def pc_watchers(events, monkeypatch) -> list[FakePcWatcher]:
    """The real watcher reads the PC in a thread through every `run` (the example config has
    pc_health and its notices on), so every test gets the fake."""
    built: list[FakePcWatcher] = []

    def build(read, config, **arguments) -> FakePcWatcher:
        built.append(FakePcWatcher(read, config, **arguments, events=events))
        return built[-1]

    monkeypatch.setattr(cli, "PcWatcher", build)
    return built


class FakeRainWatcher:
    """Stands in for RainWatcher without a thread, remembering what it was given, whether
    `waifu run` listened when it started and stopped, and the test's `events` up to then."""

    def __init__(
        self,
        read_forecast,
        config,
        *,
        announce,
        notifier,
        owner_call,
        place,
        title,
        events: list[str],
    ) -> None:
        self.read_forecast, self.config, self.announce = read_forecast, config, announce
        self.notifier, self.owner_call, self.place, self.title = notifier, owner_call, place, title
        self._events = events
        self.listening: list[bool] = []
        self.events_when_started: list[str] | None = None
        self.events_when_stopped: list[str] | None = None

    def start(self) -> None:
        self.listening.append(listening())
        self.events_when_started = list(self._events)

    def stop(self) -> None:
        self.listening.append(listening())
        self.events_when_stopped = list(self._events)


@pytest.fixture(autouse=True)
def rain_watchers(events, monkeypatch) -> list[FakeRainWatcher]:
    """The real watcher would ask Open-Meteo in a thread through every `run` whose config names
    a weather place (the alerts are on by default), so every test gets the fake."""
    built: list[FakeRainWatcher] = []

    def build(read_forecast, config, **arguments) -> FakeRainWatcher:
        built.append(FakeRainWatcher(read_forecast, config, **arguments, events=events))
        return built[-1]

    monkeypatch.setattr(cli, "RainWatcher", build)
    return built


class FakePlayTimeWatcher:
    """Stands in for PlayTimeWatcher without a thread, remembering what it was given, whether
    `waifu run` listened when it started and stopped, and the test's `events` up to then."""

    def __init__(
        self, list_processes, store, config, *, announce, owner_call, events: list[str]
    ) -> None:
        self.list_processes, self.store, self.config = list_processes, store, config
        self.announce, self.owner_call = announce, owner_call
        self._events = events
        self.listening: list[bool] = []
        self.events_when_started: list[str] | None = None
        self.events_when_stopped: list[str] | None = None

    def start(self) -> None:
        self.listening.append(listening())
        self.events_when_started = list(self._events)

    def stop(self) -> None:
        self.listening.append(listening())
        self.events_when_stopped = list(self._events)


@pytest.fixture(autouse=True)
def play_time_watchers(events, monkeypatch) -> list[FakePlayTimeWatcher]:
    """The real watcher would list the PC's processes in a thread through every `run` (play time
    is on by default), so every test gets the fake."""
    built: list[FakePlayTimeWatcher] = []

    def build(list_processes, store, config, **arguments) -> FakePlayTimeWatcher:
        built.append(FakePlayTimeWatcher(list_processes, store, config, **arguments, events=events))
        return built[-1]

    monkeypatch.setattr(cli, "PlayTimeWatcher", build)
    return built


@pytest.fixture(autouse=True)
def no_real_play_time(tmp_path, monkeypatch):
    """Every play time store a command builds lives under tmp_path: a test that leaves the
    working directory at the repository fails instead of reading the owner's sessions in
    data/playtime.json (building a store touches no file, so the refusal comes first)."""
    monkeypatch.setattr(cli, "PlayTimeStore", only_under(tmp_path, cli.PlayTimeStore))


@pytest.fixture(autouse=True)
def no_real_focus_log(tmp_path, monkeypatch):
    """Every focus log a command builds lives under tmp_path: a test that leaves the working
    directory at the repository fails instead of reading or writing the owner's focus blocks in
    data/focus.json (building a store touches no file, so the refusal comes first)."""
    monkeypatch.setattr(cli, "FocusStore", only_under(tmp_path, cli.FocusStore))


def wait_until_stopped(stop: threading.Event, timeout: float) -> bool:
    return stop.wait()


@pytest.fixture(autouse=True)
def focus_timers(monkeypatch) -> list[FocusTimer]:
    """The real focus timer checks the wall clock from a thread through every `run` (focus is on
    by default): every test gets one whose thread checks once and then waits until it is
    stopped, so a test moves time itself (`tick_once`). Holds every timer a command built."""
    built: list[FocusTimer] = []

    def build(store, **options) -> FocusTimer:
        built.append(FocusTimer(store, **options, wait=wait_until_stopped))
        return built[-1]

    monkeypatch.setattr(cli, "FocusTimer", build)
    return built


V124 = Release("v1.24", "keuangan dari tepira")
V125 = Release("v1.25", "peringatan hujan")
DISK_COMMIT = "ec4aadf" + "1" * 33
DISK_HASH = "d" * 64
DISK_CODE = CodeVersion((V125, V124), DISK_HASH, DISK_COMMIT)


class FakeCodeReader:
    """Stands in for read_code_version, which would read this repository and run git: hands
    out `disk` and remembers each read's `run` (None: neither git nor the source files). Like
    the real one it has no default `run`, so `runs` is what the caller asked for."""

    def __init__(self) -> None:
        self.disk = DISK_CODE
        self.runs: list[object] = []

    def __call__(self, *, run) -> CodeVersion:
        self.runs.append(run)
        return self.disk


@pytest.fixture(autouse=True)
def code_on_disk(monkeypatch) -> FakeCodeReader:
    """`run`, `check`, and `version` read the version of this repository's code, with git:
    every test gets the fake (tests/conftest.py refuses git too)."""
    reader = FakeCodeReader()
    monkeypatch.setattr(cli, "read_code_version", reader)
    return reader


@pytest.fixture(autouse=True)
def alive_processes(monkeypatch) -> set[int]:
    """The process ids that run: only this test's own unless a test adds one, so a record
    that `run` wrote here names a live process and no real process is ever asked about."""
    alive = {os.getpid()}
    monkeypatch.setattr(cli, "process_alive", lambda pid: pid in alive)
    return alive


@pytest.fixture(autouse=True)
def running_records_under_tmp_path(tmp_path, monkeypatch):
    """A test that leaves the working directory at the repository fails instead of writing
    over the owner's data/running.json."""

    def write_under_tmp_path(path, running) -> None:
        if not Path(path).resolve().is_relative_to(tmp_path.resolve()):
            raise AssertionError(f"a test wrote a running record outside tmp_path: {path}")
        write_running(path, running)

    monkeypatch.setattr(cli, "write_running", write_under_tmp_path)


@pytest.fixture(autouse=True)
def project_folder_is_tmp_path(tmp_path, monkeypatch) -> None:
    """`waifu version` reads the run lock and record in the project folder from any working
    directory: in every test that is tmp_path, never this repository with the owner's lock."""
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)


class FakeVersionWatcher:
    """Stands in for VersionWatcher without a thread, remembering what it was given, whether
    `waifu run` listened when it started and stopped, and the test's `events` up to then."""

    def __init__(
        self, read, running, config, *, announce, notifier, owner_call, name, events: list[str]
    ) -> None:
        self.read, self.running, self.config, self.announce = read, running, config, announce
        self.notifier, self.owner_call, self.name = notifier, owner_call, name
        self._events = events
        self.listening: list[bool] = []
        self.events_when_started: list[str] | None = None

    def start(self) -> None:
        self.listening.append(listening())
        self.events_when_started = list(self._events)

    def stop(self) -> None:
        self.listening.append(listening())


@pytest.fixture(autouse=True)
def version_watchers(events, monkeypatch) -> list[FakeVersionWatcher]:
    """The real watcher would read this repository's files in a thread through every `run`
    (the notice is on by default), so every test gets the fake."""
    built: list[FakeVersionWatcher] = []

    def build(read, running, config, **arguments) -> FakeVersionWatcher:
        built.append(FakeVersionWatcher(read, running, config, **arguments, events=events))
        return built[-1]

    monkeypatch.setattr(cli, "VersionWatcher", build)
    return built


@pytest.fixture(autouse=True)
def free_games_asked(tmp_path, monkeypatch):
    """Every offers store a command builds lives under tmp_path, and no test asks Epic or
    GamerPower.com for the free games (tests/conftest.py does not cover httpx): a test that
    forgets the fake fails afterwards, also when the tool turned the refusal into an error
    result. Yields the platforms of each refused read."""
    monkeypatch.setattr(cli, "OffersStore", only_under(tmp_path, cli.OffersStore))
    refused: list[list[str]] = []

    def refuse(platforms, *, http, now) -> object:
        refused.append(list(platforms))
        raise AssertionError("a test asked Epic or GamerPower.com for the free games")

    monkeypatch.setattr(cli, "read_free_games", refuse)
    yield refused
    assert not refused, f"a test asked for the free games of {refused}"


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """A Claude client is only built over a fake transport: a test that forgets one fails
    instead of calling the API (tests/conftest.py does not cover the SDK's httpx2)."""
    real = anthropic.Anthropic

    def only_with_a_fake_transport(*args, http_client=None, **kwargs) -> anthropic.Anthropic:
        if http_client is None:
            raise AssertionError("a test built a Claude client without a fake transport")
        return real(*args, http_client=http_client, **kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", only_with_a_fake_transport)


class FakeOffersWatcher:
    """Stands in for OffersWatcher without a thread, remembering what it was given, whether
    `waifu run` listened when it started and stopped, and the test's `events` up to then."""

    def __init__(
        self,
        store,
        read_games,
        scan,
        config,
        *,
        announce,
        notifier,
        owner_call,
        title,
        events: list[str],
    ) -> None:
        self.store, self.read_games, self.scan, self.config = store, read_games, scan, config
        self.announce, self.notifier = announce, notifier
        self.owner_call, self.title = owner_call, title
        self._events = events
        self.listening: list[bool] = []
        self.events_when_started: list[str] | None = None
        self.events_when_stopped: list[str] | None = None

    def start(self) -> None:
        self.listening.append(listening())
        self.events_when_started = list(self._events)

    def stop(self) -> None:
        self.listening.append(listening())
        self.events_when_stopped = list(self._events)


@pytest.fixture(autouse=True)
def offers_watchers(events, monkeypatch) -> list[FakeOffersWatcher]:
    """The real watcher reads the free games and runs the AI scan in a thread through every
    `run` (free_offers and its notices are on by default), so every test gets the fake."""
    built: list[FakeOffersWatcher] = []

    def build(store, read_games, scan, config, **arguments) -> FakeOffersWatcher:
        built.append(FakeOffersWatcher(store, read_games, scan, config, **arguments, events=events))
        return built[-1]

    monkeypatch.setattr(cli, "OffersWatcher", build)
    return built


@pytest.fixture(autouse=True)
def code_source_asked(tmp_path, monkeypatch):
    """Every redeem code store a command builds lives under tmp_path, and no test asks the code
    source (hoyo-codes.seria.moe; tests/conftest.py also refuses httpx beyond loopback): a test
    that forgets the fake fails afterwards. Yields the games of each refused read."""
    monkeypatch.setattr(cli, "CodeStore", only_under(tmp_path, cli.CodeStore))
    refused: list[list[str]] = []

    def refuse(games, *, http) -> object:
        refused.append(list(games))
        raise AssertionError("a test asked the redeem code source")

    monkeypatch.setattr(cli, "read_codes", refuse)
    yield refused
    assert not refused, f"a test asked the redeem code source for {refused}"


@pytest.fixture(autouse=True)
def tepira_asked(monkeypatch):
    """No test reaches tepira, the owner's finance app (tests/conftest.py refuses the network
    beyond loopback too, but the finance tools turn any failure into an error result, so that
    refusal alone could go unnoticed): building a real TepiraClient is refused, and a test that
    forgets the `tepira` fake fails afterwards, also when `main` turned the refusal into exit 1.
    Yields the url of each refused client."""
    refused: list[str] = []

    def refuse(http, url, token, **options) -> object:
        refused.append(url)
        raise AssertionError("a test built a real TepiraClient")

    monkeypatch.setattr(cli, "TepiraClient", refuse)
    yield refused
    assert not refused, f"a test built a TepiraClient for {refused}"


class FakeCodeWatcher:
    """Stands in for CodeWatcher without a thread, remembering what it was given and whether
    `waifu run` listened when it started and stopped."""

    def __init__(
        self,
        store,
        read_codes,
        hoyolab,
        config,
        games,
        *,
        announce,
        notifier,
        owner_call,
        title,
    ) -> None:
        self.store, self.read_codes, self.hoyolab = store, read_codes, hoyolab
        self.config, self.games, self.announce = config, games, announce
        self.notifier, self.owner_call, self.title = notifier, owner_call, title
        self.listening: list[bool] = []

    def start(self) -> None:
        self.listening.append(listening())

    def stop(self) -> None:
        self.listening.append(listening())


@pytest.fixture(autouse=True)
def code_watchers(monkeypatch) -> list[FakeCodeWatcher]:
    """The real watcher reads the code source in a thread through every `run` with the games on
    (redeem codes and their notices are on by default), so every test gets the fake."""
    built: list[FakeCodeWatcher] = []

    def build(*arguments, **options) -> FakeCodeWatcher:
        built.append(FakeCodeWatcher(*arguments, **options))
        return built[-1]

    monkeypatch.setattr(cli, "CodeWatcher", build)
    return built


@pytest.fixture(autouse=True)
def restore_library_log_levels():
    """main() tunes third-party loggers process-wide; give later tests the levels back."""
    names = ("tinytuya", "httpx", "anthropic", "genshin.client", "genshin.models")
    loggers = [logging.getLogger(name) for name in names]
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

    def __init__(
        self,
        registry: DeviceRegistry,
        providers=(),
        memory_section=None,
        on_usage=None,
        turn_images=None,
        confirmations=None,
    ) -> None:
        self.registry = registry
        self.providers = list(providers)
        self.memory_section = memory_section
        self.on_usage = on_usage
        self.turn_images = turn_images
        self.confirmations = confirmations
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

    def play(self, audio: np.ndarray, sample_rate: int) -> bool:
        FakePlayer.played.append((len(audio), sample_rate))
        return True  # played to the end

    def stop(self) -> bool:
        return False  # nothing is ever still playing


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
    """Every speech router a command builds, each remembering the voice converter it got and
    whether it speaks only through the offline fallback."""
    built: list[SpeakingRouter] = []

    def build(tts, *, converter=None, fallback_only=False):
        built.append(SpeakingRouter(events, converter, fallback_only))
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

    def convert(
        self, audio: np.ndarray, sample_rate: int, *, pitch: int | None = None
    ) -> tuple[np.ndarray, int]:
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
# The same, with a listen line the owner wrote for whatever she calls them, and Sayang as that.
SAYANG_TOUCH_LINES = (
    TOUCH_LINES.replace("[Iya Master]", '["Iya, {owner}"]') + "assistant:\n  owner_call: Sayang\n"
)


def interaction_workers() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == INTERACTION_WORKER]


@pytest.fixture
def event_player(events, monkeypatch) -> None:
    """A Player that logs each playback in `events`, to see what happens right before it."""

    class EventPlayer:
        def __init__(self, device=None) -> None:
            pass

        def play(self, audio: np.ndarray, sample_rate: int) -> bool:
            events.append("play")
            return True

        def stop(self) -> bool:
            return False

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
        (["say", "--fallback", "--raw", "Halo"], "say"),
        (["mics"], "mics"),
        (["voices"], "voices"),
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
        (["briefing"], "briefing"),
        (["rain"], "rain"),
        (["usage"], "usage"),
        (["usage", "--month", "2026-10"], "usage"),
        (["phone", "setup"], "phone"),
        (["phone", "pair"], "phone"),
        (["phone", "devices"], "phone"),
        (["phone", "forget", "d1"], "phone"),
        (["gacha", "codes"], "gacha"),
        (["gacha", "codes", "hsr", "--redeem"], "gacha"),
        (["todo"], "todo"),
        (["todo", "list", "--done"], "todo"),
        (["todo", "add", "kerjakan", "laporan", "--due", "2099-10-16"], "todo"),
        (["todo", "done", "t3"], "todo"),
        (["todo", "remove", "t3"], "todo"),
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

    def build(
        config,
        registry,
        *,
        providers=(),
        memory_section=None,
        on_usage=None,
        turn_images=None,
        confirmations=None,
    ):
        built.append(
            HouseBrain(registry, providers, memory_section, on_usage, turn_images, confirmations)
        )
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


def test_chat_answers_a_tag_only_reply_with_what_she_calls_the_owner(
    home, brains, monkeypatch, capsys
):
    write_config(home, "assistant:\n  owner_call: Sayang\n")
    monkeypatch.setattr(HouseBrain, "reply", "[senang]")
    monkeypatch.setattr(sys, "stdin", io.StringIO("nyalakan lampu kamar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert "Cyrene: Sudah, Sayang." in capsys.readouterr().out


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
    monkeypatch.setattr(
        cli, "build_speech_router", lambda tts, converter=None, fallback_only=False: router
    )

    assert cli.main(["say", "Halo", "Master,", "おかえりなさい!"]) == 0

    assert router.texts == ["Halo Master, おかえりなさい!"]
    assert player.played == [(240, 24000)]


def test_say_with_the_anime_voice_off_starts_no_rvc_server(home, player, rvc, speech, events):
    assert cli.main(["say", "Halo"]) == 0

    assert rvc.servers == [] and rvc.clients == []
    assert speech[0].converter is None
    assert events == ["say Halo"]


def test_say_speaks_with_her_voice_keeping_the_fallback_for_its_failures(home, player, speech):
    assert cli.main(["say", "Halo"]) == 0

    assert speech[0].fallback_only is False


def test_say_fallback_speaks_only_through_the_offline_fallback(home, player, rvc, speech, events):
    assert cli.main(["say", "--fallback", "Halo", "Master"]) == 0

    assert speech[0].fallback_only is True
    assert events == ["say Halo Master"]
    assert len(player.played) == 1


def test_say_fallback_goes_through_the_anime_voice(home, player, rvc, speech, events):
    # To hear the fallback as she would sound with edge-tts down, and tune its pitch.
    enable_rvc(home)

    assert cli.main(["say", "--fallback", "Halo"]) == 0

    assert speech[0].fallback_only is True
    assert speech[0].converter is rvc.clients[0]
    assert events == ["rvc started", "say Halo", "rvc stopped"]


def test_say_fallback_raw_speaks_the_fallback_without_the_anime_voice(
    home, player, rvc, speech, events
):
    enable_rvc(home)

    assert cli.main(["say", "--fallback", "--raw", "Halo"]) == 0

    assert speech[0].fallback_only is True and speech[0].converter is None
    assert rvc.servers == []


def test_say_fallback_refuses_while_the_fallback_is_off(home, player, rvc, speech, capsys):
    write_config(home, "tts:\n  fallback:\n    enabled: false\n")

    assert cli.main(["say", "--fallback", "Halo"]) == 1

    assert capsys.readouterr().out.splitlines() == [
        "! Suara cadangan mati (tts.fallback.enabled: false)."
    ]
    assert speech == [] and player.played == []


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


ADD_INDONESIAN = (
    "belum ada suara Windows bahasa Indonesia; tambahkan gratis di Settings → "
    "Time & language → Speech → Add voices (Indonesian)"
)
ADD_JAPANESE = (
    "belum ada suara Windows bahasa Jepang; tambahkan gratis di Settings → "
    "Time & language → Speech → Add voices (Japanese)"
)


def test_voices_lists_windows_voices_per_language_then_each_fallback(home, windows_voices, capsys):
    windows_voices.voices = [DAVID, ZIRA, HARUKA, MARK]

    assert cli.main(["voices"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Suara Windows di PC ini (OneCore):",
        "  en-US: Microsoft David (laki-laki), Microsoft Mark (laki-laki), "
        "Microsoft Zira (perempuan)",
        "  ja-JP: Microsoft Haruka (perempuan)",
        f"! Suara cadangan bahasa Indonesia: {ADD_INDONESIAN}",
        "✓ Suara cadangan bahasa Jepang: Microsoft Haruka (ja-JP, perempuan)",
    ]


def test_voices_names_the_desktop_voices_when_only_those_can_be_read(home, windows_voices, capsys):
    windows_voices.voices = [WindowsVoice("Microsoft Zira Desktop", "en-US", "Female", "sapi")]

    cli.main(["voices"])

    assert capsys.readouterr().out.splitlines()[:2] == [
        "Suara Windows di PC ini (SAPI desktop):",
        "  en-US: Microsoft Zira Desktop (perempuan)",
    ]


def test_voices_says_when_windows_has_none(home, windows_voices, capsys):
    windows_voices.voices = []

    assert cli.main(["voices"]) == 0

    assert capsys.readouterr().out.splitlines()[0] == "Belum ada suara Windows di PC ini."


def test_voices_fails_when_windows_cannot_list_them(home, windows_voices, capsys):
    windows_voices.error = WindowsVoiceError("PowerShell tidak selesai dalam 20 detik.")

    assert cli.main(["voices"]) == 1

    assert capsys.readouterr().out.splitlines() == [
        "✗ Suara Windows tidak terbaca: PowerShell tidak selesai dalam 20 detik."
    ]


def test_voices_says_when_the_fallback_is_off(home, windows_voices, capsys):
    write_config(home, "tts:\n  fallback:\n    enabled: false\n")

    assert cli.main(["voices"]) == 0

    assert capsys.readouterr().out.splitlines()[-1] == (
        "! Suara cadangan offline mati (tts.fallback.enabled: false); "
        "kalau edge-tts gagal, Cyrene diam"
    )


# --- run -------------------------------------------------------------------------------------


class RecordingAssistant:
    """Records what `waifu run` asks of her. `busy_answers` are what `busy` says, one per
    check (then False), and `cut_answers` what `interrupt` says, one per call (then False:
    nothing was playing); `cuts` counts those calls; `reacted` counts each reaction
    (`speak_reaction` or `listen_now`); `asked` holds each `ask_aloud` with its `on_reply` and
    `required_tool`, and `new_sessions` whether each starts a new session;
    `waiting_for_followup` the texts of announcements that wait while the follow-up window is
    open; `reminded` what `remind` queued (Master's own lines); `phone_messages`, `phone_photos`
    (None for a message without one), `phone_cameras` (whether each was sent through the
    camera), and `phone_recordings` what the phone asked."""

    instances: list["RecordingAssistant"] = []

    def __init__(self, config, **collaborators) -> None:
        self.collaborators = collaborators
        self.ran_with: tuple | None = None
        self.announced: list[tuple[str, str]] = []
        self.waiting_for_followup: list[str] = []
        self.reminded: list[tuple[str, str]] = []
        self.asked: list[tuple[str, Callable[[str], None] | None, str | None]] = []
        self.new_sessions: list[bool] = []
        self.busy_answers: list[bool] = []
        self.cut_answers: list[bool] = []
        self.cuts = 0
        self.reactions: list[tuple[str, ...]] = []
        self.reacted = threading.Semaphore(0)
        self.phone_messages: list[str] = []
        self.phone_photos: list[bytes | None] = []
        self.phone_cameras: list[bool] = []
        self.phone_recordings: list[bytes] = []
        RecordingAssistant.instances.append(self)

    def run(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)

    @property
    def busy(self) -> bool:
        return self.busy_answers.pop(0) if self.busy_answers else False

    def interrupt(self) -> bool:
        self.cuts += 1
        return self.cut_answers.pop(0) if self.cut_answers else False

    def speak_reaction(self, text: str, emotion: str) -> None:
        self.reactions.append(("speak", text, emotion))
        self.reacted.release()

    def listen_now(self) -> None:
        self.reactions.append(("listen",))
        self.reacted.release()

    def await_reactions(self, count: int) -> None:
        for _ in range(count):
            assert self.reacted.acquire(timeout=WAIT_S), f"only {self.reactions} after {WAIT_S} s"

    def announce(self, text: str, emotion: str = "neutral", *, wait_for_followup=False) -> None:
        self.announced.append((text, emotion))
        if wait_for_followup:
            self.waiting_for_followup.append(text)

    def remind(self, text: str, emotion: str = "neutral") -> None:
        self.reminded.append((text, emotion))

    def ask_aloud(self, prompt: str, *, on_reply=None, required_tool=None, new_session=False):
        self.asked.append((prompt, on_reply, required_tool))
        self.new_sessions.append(new_session)

    def reply_for_phone(
        self, text: str, image: bytes | None = None, *, camera: bool = False
    ) -> PhoneReply:
        self.phone_messages.append(text)
        self.phone_photos.append(image)
        self.phone_cameras.append(camera)
        return PhoneReply(REPLY, "happy", None, 60, [], 0.0)

    def transcribe_for_phone(self, wav_bytes: bytes) -> str:
        self.phone_recordings.append(wav_bytes)
        return "nyalakan lampu kamar"


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
    def __init__(self, events: list[str], converter=None, fallback_only: bool = False) -> None:
        super().__init__()
        self.events = events
        self.converter = converter
        self.fallback_only = fallback_only

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


def test_run_announce_calls_the_owner_what_she_calls_them(announcing, home):
    write_config(home, "assistant:\n  owner_call: Sayang\n")

    assert cli.main(["run", "--dry-run", "--announce"]) == 0

    assert "say Cyrene siap, Sayang." in announcing.events


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


def test_run_reactions_call_the_owner_what_she_calls_them(run_fakes, avatar, home, monkeypatch):
    enable_avatar(home, extra=SAYANG_TOUCH_LINES)
    monkeypatch.setattr(
        RecordingAssistant, "run", touched_while_listening(avatar, "double_click", reactions=2)
    )

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert RecordingAssistant.instances[0].reactions == [
        ("listen",),
        ("speak", "Iya, Sayang", "happy"),
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


def test_run_cuts_her_off_on_a_double_click_while_she_speaks_and_only_listens(
    run_fakes, avatar, home, monkeypatch
):
    enable_avatar(home, extra=TOUCH_LINES)

    def run(self: RecordingAssistant, mic, segmenter) -> None:
        self.cut_answers = [True]  # reading out a long reply when Master double-clicks
        touch(avatar, "double_click")
        self.await_reactions(1)

    monkeypatch.setattr(RecordingAssistant, "run", run)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assistant = RecordingAssistant.instances[0]
    assert assistant.cuts == 1
    assert assistant.reactions == [("listen",)]


class CutOffPlayer:
    """A Player whose clip, a long announcement, plays until `stop()` cuts it (or WAIT_S)."""

    def __init__(self) -> None:
        self.playing = threading.Event()
        self.stopped = threading.Event()

    def play(self, audio: np.ndarray, sample_rate: int) -> bool:
        self.playing.set()
        cut = self.stopped.wait(WAIT_S)
        self.playing.clear()
        return not cut

    def stop(self) -> bool:
        if not self.playing.is_set():
            return False
        self.stopped.set()
        return True


def test_run_double_click_from_the_page_stops_her_voice_and_she_listens_without_a_line(
    announcing, avatar, home, monkeypatch
):
    enable_avatar(home, extra=TOUCH_LINES)
    player = CutOffPlayer()
    monkeypatch.setattr(cli, "Player", lambda device=None: player)

    class ListeningMic(EventMic):
        def frames(self):
            assert avatar.buses[0].wait_for(("state", "listening", 8.0))
            yield from super().frames()

    monkeypatch.setattr(cli, "MicStream", lambda *args, **kwargs: ListeningMic(announcing.events))

    def double_click_while_she_talks() -> None:  # on the avatar server's request thread
        if player.playing.wait(WAIT_S):
            touch(avatar, "double_click")

    page = threading.Thread(target=double_click_while_she_talks)
    page.start()
    try:
        assert cli.main(["run", "--dry-run", "--announce"]) == 0
    finally:
        page.join(WAIT_S)

    assert player.stopped.is_set()
    # The cut announcement leaves her idle; the double-click then opens the follow-up window.
    assert avatar.buses[0].calls[-2:] == [("state", "idle", None), ("state", "listening", 8.0)]
    assert "say Iya Master" not in announcing.events


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


def test_avatar_prints_her_answer_with_what_she_calls_the_owner(home, avatar, monkeypatch, capsys):
    enable_avatar(home, extra=SAYANG_TOUCH_LINES)
    monkeypatch.setattr(
        cli, "sleep", TouchingSleep(avatar, "double_click", until=("speak", "happy"))
    )

    assert cli.main(["avatar"]) == 0

    assert "Cyrene: Iya, Sayang" in capsys.readouterr().out.splitlines()


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


def fallback_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if "cadangan" in line or "Suara Windows" in line]


def test_check_names_the_fallback_voice_of_each_language_before_the_anime_voice(
    home, nothing_installed, windows_voices, capsys
):
    windows_voices.voices = [ZIRA, ANDIKA, HARUKA]

    cli.main(["check"])

    out = capsys.readouterr().out
    assert fallback_lines(out) == [
        "✓ Suara cadangan bahasa Indonesia: Microsoft Andika (id-ID, laki-laki)",
        "✓ Suara cadangan bahasa Jepang: Microsoft Haruka (ja-JP, perempuan)",
    ]
    lines = out.splitlines()
    assert lines.index(fallback_lines(out)[-1]) + 1 == lines.index(rvc_lines(out)[0])
    assert windows_voices.listings == 1


def test_check_says_where_to_add_the_missing_fallback_voices(
    home, nothing_installed, windows_voices, capsys
):
    # The owner's PC: English voices only.
    assert cli.main(["check"]) == 1  # Ollama and the voice extra are missing, not the voices

    assert fallback_lines(capsys.readouterr().out) == [
        f"! Suara cadangan bahasa Indonesia: {ADD_INDONESIAN}",
        f"! Suara cadangan bahasa Jepang: {ADD_JAPANESE}",
    ]


def test_check_points_out_a_named_fallback_voice_that_is_not_installed(
    home, nothing_installed, windows_voices, capsys
):
    write_config(home, "tts:\n  fallback:\n    voices:\n      id: Microsoft Andika\n")

    cli.main(["check"])

    assert fallback_lines(capsys.readouterr().out) == [
        "! Suara cadangan bahasa Indonesia: 'Microsoft Andika' (tts.fallback.voices.id) "
        "tidak terpasang; lihat 'waifu voices'"
    ]


def test_check_accepts_a_named_fallback_voice_of_another_language(
    home, nothing_installed, windows_voices, capsys
):
    write_config(home, "tts:\n  fallback:\n    voices:\n      id: Zira\n")

    cli.main(["check"])

    assert fallback_lines(capsys.readouterr().out) == [
        "✓ Suara cadangan bahasa Indonesia: Microsoft Zira (en-US, perempuan)"
    ]


def test_check_reports_the_fallback_off_without_listing_voices(
    home, nothing_installed, windows_voices, capsys
):
    write_config(home, "tts:\n  fallback:\n    enabled: false\n")

    cli.main(["check"])

    assert fallback_lines(capsys.readouterr().out) == [
        "! Suara cadangan offline mati (tts.fallback.enabled: false); "
        "kalau edge-tts gagal, Cyrene diam"
    ]
    assert windows_voices.listings == 0


def test_check_warns_when_windows_voices_cannot_be_read(
    home, nothing_installed, windows_voices, capsys
):
    windows_voices.error = WindowsVoiceError("PowerShell tidak selesai dalam 20 detik.")

    cli.main(["check"])

    assert fallback_lines(capsys.readouterr().out) == [
        "! Suara Windows tidak terbaca (PowerShell tidak selesai dalam 20 detik); "
        "suara cadangan offline tidak bisa dipakai"
    ]


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


# Reaction lines copied from the example before owner_call existed: "Master" written in them.
OLD_EXAMPLE_LINES = (
    "  interactions:\n"
    "    lines:\n"
    '      head_pat: ["えへへ, geli, Master!", "Mm, enak dielus begini."]\n'
    '      body_touch: ["Jangan iseng, ya!"]\n'
    '      welcome_back: ["Master sudah kembali! Aku kangen."]\n'
    '      listen: ["Iya, aku dengar."]\n'
)
CALLED_SAYANG = "assistant:\n  owner_call: Sayang\n"
OLD_LINES_WARNING = "Kalimat reaksi avatar masih menyebut"


def test_check_warns_about_reaction_lines_that_still_say_master_to_an_owner_called_otherwise(
    home, nothing_installed, avatar_install, capsys
):
    enable_avatar(home, extra=OLD_EXAMPLE_LINES + CALLED_SAYANG)

    cli.main(["check"])

    assert (
        '! Kalimat reaksi avatar masih menyebut "Master", padahal assistant.owner_call: Sayang '
        '(avatar.interactions.lines: head_pat, welcome_back); ganti dengan "{owner}" atau '
        "hapus jenisnya untuk memakai bawaannya"
    ) in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize(
    "extra",
    [
        OLD_EXAMPLE_LINES,
        CALLED_SAYANG,
        OLD_EXAMPLE_LINES.replace("  interactions:\n", "  interactions:\n    enabled: false\n")
        + CALLED_SAYANG,
    ],
    ids=["called_master", "owner_placeholder", "reactions_off"],
)
def test_check_is_quiet_about_reaction_lines_she_says_with_the_right_word_or_never_says(
    home, nothing_installed, avatar_install, capsys, extra
):
    enable_avatar(home, extra=extra)

    cli.main(["check"])

    assert OLD_LINES_WARNING not in capsys.readouterr().out


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
        self.clicks: list[str | None] = []  # the link each notification opens when tapped

    def send(self, title: str, message: str, *, tags=(), click=None) -> bool:
        self.sent.append((title, message, tuple(tags)))
        self.clicks.append(click)
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
        self.hsr_roster: tuple[HsrCharacter, ...] = ()
        self.hsr_modes: dict[str, HsrEndgame | GachaError] = {}
        self.hsr_events = HsrCalendar(None, (), (), ())
        self.hsr_failure: GachaError | None = None
        self.hsr_asked: list[object] = []
        self.redeemed: list[tuple[str, list[str]]] = []  # nothing ever reaches HoYoLAB
        self.redeem_outcomes: dict[str, str] = {}  # by code; redeemed when not listed
        self.redeem_failure: GachaError | None = None

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

    def hsr_characters(self):
        self.hsr_asked.append("characters")
        if self.hsr_failure is not None:
            raise self.hsr_failure
        return self.hsr_roster

    def hsr_endgame(self, modes):
        modes = list(modes)
        self.hsr_asked.append(("endgame", modes))
        return {mode: self.hsr_modes[mode] for mode in modes}

    def hsr_calendar(self):
        self.hsr_asked.append("calendar")
        if self.hsr_failure is not None:
            raise self.hsr_failure
        return self.hsr_events

    def redeem_codes(self, game_id, codes):
        self.redeemed.append((game_id, list(codes)))
        if self.redeem_failure is not None:
            raise self.redeem_failure
        results = []
        for code in codes[:10]:
            outcome = self.redeem_outcomes.get(code, "redeemed")
            message = REDEEM_MESSAGES[outcome].format(
                code=code, game=GAMES[game_id].short, problem="HoYoLAB bermasalah."
            )
            results.append(RedeemResult(game_id, code, outcome, message))
        return results


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

    def __init__(self, hoyolab, config, *, notifier, announce, title, owner_call) -> None:
        self.hoyolab, self.config, self.notifier = hoyolab, config, notifier
        self.announce, self.title, self.owner_call = announce, title, owner_call
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

    [
        tools,
        data_tools,
        code_tools,
        weekly,
        memory_tools,
        schedule,
        todo,
        focus,
        briefing,
        pc,
        play_time,
        offers,
        music,
    ] = brains[0].providers
    assert (
        type(tools),
        type(memory_tools),
        type(schedule),
        type(todo),
        type(focus),
        type(briefing),
        type(pc),
        type(play_time),
        type(offers),
        type(music),
    ) == (
        GachaTools,
        MemoryTools,
        ScheduleTools,
        TodoTools,
        FocusTools,
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    )
    assert (type(data_tools), type(code_tools), type(weekly)) == (
        GameDataTools,
        CodeTools,
        WeeklyReportTools,
    )
    assert [spec.name for spec in tools.specs()] == ["game_status", "gacha_history"]
    [watcher] = watchers
    assistant = RecordingAssistant.instances[0]
    assert watcher.hoyolab is games.hoyolab
    assert watcher.title == "Cyrene"
    assert watcher.owner_call == "Master"
    assert watcher.notifier is None  # notify is off in the example config
    assert (watcher.ran_when_started, watcher.ran_when_stopped) == (False, True)
    watcher.announce("Master, resin penuh.", "surprised")
    assert assistant.announced == [("Master, resin penuh.", "surprised")]


def call_the_owner(home: Path, word: str) -> None:
    """The example config with `assistant.owner_call` set to `word`."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    called = text.replace("  owner_call: Master\n", f"  owner_call: {word}\n", 1)
    assert called != text
    config.write_text(called, encoding="utf-8")


def test_run_watches_the_games_for_an_owner_she_calls_sayang(
    home, run_fakes, brains, games, watchers
):
    enable_gacha(home)
    call_the_owner(home, "Sayang")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert watchers[0].owner_call == "Sayang"


def test_run_without_a_cookie_has_the_game_tools_but_no_watcher(
    home, run_fakes, brains, games, watchers
):
    enable_gacha(home, cookie=None)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        GachaTools,
        GameDataTools,
        CodeTools,
        WeeklyReportTools,
        MemoryTools,
        ScheduleTools,
        TodoTools,
        FocusTools,
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
    assert watchers == []


def test_run_with_the_games_off_builds_none_of_it(home, run_fakes, brains, games, watchers):
    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert (games.built, watchers) == ([], [])
    assert [type(tools) for tools in brains[0].providers] == [
        MemoryTools,
        ScheduleTools,
        TodoTools,
        FocusTools,
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]


def test_chat_with_the_games_on_gives_the_brain_the_game_tools(home, brains, games, monkeypatch):
    enable_gacha(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        GachaTools,
        GameDataTools,
        CodeTools,
        WeeklyReportTools,
        MemoryTools,
        ScheduleTools,
        TodoTools,
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]


@pytest.fixture
def data_sources(monkeypatch) -> list[tuple[object, list[str]]]:
    """Builds the real GameDataTools, remembering where each one reads Master's game accounts
    and for which games."""
    sources: list[tuple[object, list[str]]] = []

    def build(source, games):
        sources.append((source, list(games)))
        return GameDataTools(source, games)

    monkeypatch.setattr(cli, "GameDataTools", build)
    return sources


def play_only(home: Path, games: str) -> None:
    """The example config with `gacha.games` set to `games`."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    chosen = text.replace("  games: [hsr, genshin, zzz]\n", f"  games: {games}\n", 1)
    assert chosen != text
    config.write_text(chosen, encoding="utf-8")


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
@pytest.mark.parametrize(
    ("played", "configured"),
    [
        ("[hsr]", ["hsr"]),
        ("[genshin, zzz]", ["genshin", "zzz"]),
        ("[zzz, hsr]", ["zzz", "hsr"]),
    ],
)
def test_the_data_tools_follow_the_game_tools_and_read_hoyolab_for_the_configured_games(
    played, configured, argv, home, run_fakes, brains, games, watchers, data_sources, monkeypatch
):
    enable_gacha(home)
    play_only(home, played)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    [tools, data_tools, code_tools, *_] = brains[0].providers
    assert (type(tools), type(data_tools), type(code_tools)) == (
        GachaTools,
        GameDataTools,
        CodeTools,
    )
    assert [spec.name for spec in data_tools.specs()] == [
        "game_characters",
        "game_endgame",
        "game_events",
    ]
    assert data_sources == [(games.hoyolab, configured)]


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_the_data_tools_offer_only_the_configured_games_and_their_modes(
    argv, home, run_fakes, brains, games, watchers, monkeypatch
):
    enable_gacha(home)
    play_only(home, "[genshin, zzz]")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    [data_tools] = [p for p in brains[0].providers if isinstance(p, GameDataTools)]
    endgame = next(spec for spec in data_tools.specs() if spec.name == "game_endgame")
    assert endgame.input_schema["properties"]["game"]["enum"] == ["genshin", "zzz"]
    assert endgame.input_schema["properties"]["mode"]["enum"] == [
        "spiral_abyss",
        "imaginarium_theater",
        "stygian_onslaught",
        "shiyu_defense",
        "deadly_assault",
    ]


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_the_games_off_there_are_no_data_tools(
    argv, home, run_fakes, brains, games, watchers, data_sources, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert not any(isinstance(tools, GameDataTools) for tools in brains[0].providers)
    assert (games.built, data_sources) == ([], [])


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


def hsr_character(
    name: str,
    rarity: int,
    level: int,
    eidolon: int,
    light_cone: HsrLightCone | None,
    path: str = "Nihility",
) -> HsrCharacter:
    return HsrCharacter(name, rarity, level, eidolon, path, "Lightning", light_cone, (), (), None)


def from_now(**delta: float) -> datetime:
    """A moment that far from the wall clock: HoYoLAB's answers carry real times, and the
    command counts the time left from the wall clock."""
    return datetime.now(UTC) + timedelta(**delta)


def local(moment: datetime) -> str:
    return f"{moment.astimezone():%Y-%m-%d %H:%M}"


def test_gacha_hsr_characters_prints_one_line_per_character_strongest_first(home, games, capsys):
    games.hoyolab.hsr_roster = (
        hsr_character("Pela", 4, 80, 6, HsrLightCone("Resolution Shines As Pearls", 80, 5, 4)),
        hsr_character("Silver Wolf", 5, 70, 0, None),
        hsr_character("Acheron", 5, 80, 2, HsrLightCone("Along the Passing Shore", 80, 1, 5)),
    )

    assert cli.main(["gacha", "hsr", "characters"]) == 0

    assert games.hoyolab.hsr_asked == ["characters"]
    assert capsys.readouterr().out.splitlines() == [
        "Acheron ★5 Lv80 E2 (Nihility), Along the Passing Shore Lv80 S1",
        "Silver Wolf ★5 Lv70 E0 (Nihility), tanpa Light Cone",
        "Pela ★4 Lv80 E6 (Nihility), Resolution Shines As Pearls Lv80 S5",
    ]


def test_gacha_hsr_characters_tells_the_trailblazer_paths_apart(home, games, capsys):
    # HoYoLAB names every Path of the Trailblazer "Trailblazer", each with its own Eidolon.
    games.hoyolab.hsr_roster = (
        hsr_character("Trailblazer", 5, 80, 6, None, path="Remembrance"),
        hsr_character("Trailblazer", 5, 80, 2, None, path="Destruction"),
    )

    assert cli.main(["gacha", "hsr", "characters"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Trailblazer ★5 Lv80 E2 (Destruction), tanpa Light Cone",
        "Trailblazer ★5 Lv80 E6 (Remembrance), tanpa Light Cone",
    ]


def test_gacha_hsr_characters_without_any_character_says_so(home, games, capsys):
    assert cli.main(["gacha", "hsr", "characters"]) == 1

    assert capsys.readouterr().out.startswith("! ")


def test_gacha_hsr_endgame_prints_one_line_per_mode(home, games, capsys):
    moc_ends, pf_ends = from_now(days=4, hours=12, minutes=30), from_now(hours=5, minutes=30)
    as_ends = from_now(days=2)
    floor = HsrFloor("Stage 12", 3, 4, None)
    games.hoyolab.hsr_modes = {
        "memory_of_chaos": HsrEndgame(
            "memory_of_chaos", "Lupine Moon", moc_ends, True, 30, 3, "Stage 12", 14, (floor,)
        ),
        "pure_fiction": HsrEndgame(
            "pure_fiction", "Nameless", pf_ends, False, 0, 0, None, None, ()
        ),
        "apocalyptic_shadow": HsrEndgame(
            "apocalyptic_shadow", "Dawn", as_ends, True, 9, 0, "Difficulty 4", 4, ()
        ),
        "anomaly_arbitration": HsrEndgame(
            "anomaly_arbitration", None, None, True, 2, 0, "Lord of the Abyss", None, ()
        ),
    }

    assert cli.main(["gacha", "hsr", "endgame"]) == 0

    assert games.hoyolab.hsr_asked == [("endgame", list(ENDGAME_MODES))]
    assert capsys.readouterr().out.splitlines() == [
        "Memory of Chaos: 30 bintang (+3 ekstra), lantai terdalam Stage 12, "
        f"berakhir {local(moc_ends)} (4 hari 12 jam lagi)",
        f"Pure Fiction: belum dimainkan musim ini, berakhir {local(pf_ends)} (5 jam 30 menit lagi)",
        "Apocalyptic Shadow: 9 bintang, lantai terdalam Difficulty 4, "
        f"berakhir {local(as_ends)} (2 hari lagi)",
        "Anomaly Arbitration: 2 bintang, lantai terdalam Lord of the Abyss",
    ]


def test_gacha_hsr_endgame_shows_the_other_modes_when_one_cannot_be_read(home, games, capsys):
    played = HsrEndgame("memory_of_chaos", None, None, True, 12, 0, None, 5, ())
    refused = GachaError("other", "HoYoLAB menolak permintaan HSR (kode -1).")
    games.hoyolab.hsr_modes = {
        mode: refused if mode == "pure_fiction" else played for mode in ENDGAME_MODES
    }

    assert cli.main(["gacha", "hsr", "endgame"]) == 1

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Memory of Chaos: 12 bintang"
    assert lines[1] == "Pure Fiction: ✗ HoYoLAB menolak permintaan HSR (kode -1)."
    assert len(lines) == 4


def test_gacha_hsr_events_names_a_warp_hoyolab_leaves_unnamed_by_its_kind(home, games, capsys):
    ends = from_now(days=5, hours=11)
    games.hoyolab.hsr_events = HsrCalendar(
        "4.6",
        (HsrWarp("character", "", ("Evanescia",), ("Qingque",), from_now(days=-9), ends),),
        (),
        (),
    )

    assert cli.main(["gacha", "hsr", "events"]) == 0

    warp_line = capsys.readouterr().out.splitlines()[2]
    assert (
        warp_line
        == f"  Warp karakter: 5★ Evanescia; 4★ Qingque; berakhir {local(ends)} (5 hari 11 jam lagi)"
    )


def test_gacha_hsr_events_prints_the_warps_events_and_endgame_windows(home, games, capsys):
    warp_ends, next_warp = from_now(days=4, hours=12, minutes=30), from_now(days=6, hours=2)
    soon, later, season_ends = from_now(hours=5, minutes=30), from_now(days=9), from_now(days=20)
    started = from_now(days=-2)
    games.hoyolab.hsr_events = HsrCalendar(
        "3.6",
        (
            HsrWarp(
                "character",
                "Butterfly on Swordtip",
                ("Acheron",),
                ("Pela", "Gallagher", "Lynx"),
                from_now(days=-10),
                warp_ends,
            ),
            HsrWarp(
                "light_cone",
                "Brilliant Fixation",
                ("Along the Passing Shore",),
                ("Resolution Shines As Pearls",),
                next_warp,
                from_now(days=27),
            ),
        ),
        (
            HsrEvent("Gift of Odyssey", started, later, 3, 7, False),
            HsrEvent("Cosmic Hide and Seek", None, None, 5, 5, True),
            HsrEvent("Planar Fissure", started, soon, None, None, False),
        ),
        (HsrChallengeWindow("memory_of_chaos", "Lupine Moon", started, season_ends, 30, 36),),
    )

    assert cli.main(["gacha", "hsr", "events"]) == 0

    assert games.hoyolab.hsr_asked == ["calendar"]
    assert capsys.readouterr().out.splitlines() == [
        "Kalender HSR versi 3.6",
        "Warp:",
        "  Butterfly on Swordtip (karakter): 5★ Acheron; 4★ Pela, Gallagher, Lynx; "
        f"berakhir {local(warp_ends)} (4 hari 12 jam lagi)",
        "  Brilliant Fixation (Light Cone): 5★ Along the Passing Shore; "
        f"4★ Resolution Shines As Pearls; mulai {local(next_warp)} (6 hari 2 jam lagi)",
        "Event:",
        f"  Planar Fissure: berakhir {local(soon)} (5 jam 30 menit lagi)",
        f"  Gift of Odyssey: 3/7, berakhir {local(later)} (9 hari lagi)",
        "  Cosmic Hide and Seek: selesai",
        "Endgame:",
        f"  Memory of Chaos (Lupine Moon): 30/36, berakhir {local(season_ends)} (20 hari lagi)",
    ]


def test_gacha_hsr_events_lists_only_the_events_and_endgame_windows_running_now(
    home, games, capsys
):
    started, ends, ended = from_now(days=-1), from_now(days=9), from_now(hours=-1)
    games.hoyolab.hsr_events = HsrCalendar(
        None,
        (),
        (
            HsrEvent("Gift of Odyssey", started, ends, None, None, False),
            HsrEvent("Ember Festival", from_now(days=2), from_now(days=20), 0, 4, False),
            HsrEvent("Planar Fissure", from_now(days=-9), ended, None, None, False),
        ),
        (
            HsrChallengeWindow("memory_of_chaos", "Lupine Moon", started, ends, 30, 36),
            HsrChallengeWindow("memory_of_chaos", "Next Season", ends, from_now(days=51), 0, 36),
            HsrChallengeWindow("pure_fiction", "Ended Season", from_now(days=-42), ended, 8, 12),
        ),
    )

    assert cli.main(["gacha", "hsr", "events"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Kalender HSR",
        "Warp: tidak ada",
        "Event:",
        f"  Gift of Odyssey: berakhir {local(ends)} (9 hari lagi)",
        "Endgame:",
        f"  Memory of Chaos (Lupine Moon): 30/36, berakhir {local(ends)} (9 hari lagi)",
    ]


def test_gacha_hsr_events_with_an_empty_calendar_says_there_is_nothing(home, games, capsys):
    assert cli.main(["gacha", "hsr", "events"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Kalender HSR",
        "Warp: tidak ada",
        "Event: tidak ada",
        "Endgame: tidak ada",
    ]


@pytest.mark.parametrize(("view", "asked"), [("characters", "characters"), ("events", "calendar")])
def test_gacha_hsr_that_hoyolab_refuses_prints_why_and_fails(view, asked, home, games, capsys):
    games.hoyolab.hsr_failure = GachaError(
        "chronicle_private", "Data HSR ini tidak bisa dibaca karena Battle Chronicle tidak publik."
    )

    assert cli.main(["gacha", "hsr", view]) == 1

    assert games.hoyolab.hsr_asked == [asked]
    assert capsys.readouterr().out == (
        "✗ Data HSR ini tidak bisa dibaca karena Battle Chronicle tidak publik.\n"
    )


@pytest.mark.parametrize("view", ["characters", "endgame", "events"])
def test_gacha_hsr_works_with_the_gacha_buddy_off(view, home, games):
    games.hoyolab.hsr_roster = (hsr_character("Acheron", 5, 80, 2, None),)
    games.hoyolab.hsr_modes = dict.fromkeys(
        ENDGAME_MODES, HsrEndgame("memory_of_chaos", None, None, False, 0, 0, None, None, ())
    )

    assert cli.main(["gacha", "hsr", view]) == 0

    [config] = games.built
    assert config.gacha.enabled is False


def test_verbose_logging_never_reveals_the_hsr_uid_genshin_puts_in_request_urls(
    home, games, monkeypatch, caplog
):
    uid = "800000001"

    def characters_through_genshin() -> tuple[HsrCharacter, ...]:
        # HoyolabClient builds a fresh genshin.Client for every call, after the logging is set
        # up, and its constructor sets the "genshin" logger back to NOTSET. Before each request
        # genshin.py logs the URL with its query, the uid included, at DEBUG.
        client = genshin.Client("ltuid_v2=1; ltoken_v2=v2_SAMPLECOOKIE", lang="en-us")
        url = "https://bbs-api-os.hoyolab.com/game_record/hkrpg/api/avatar/info"
        params = {"role_id": uid, "server": "prod_official_asia", "need_wiki": "true"}
        asyncio.run(client._request_hook("GET", url, params=params))
        return (hsr_character("Acheron", 5, 80, 2, None),)

    monkeypatch.setattr(games.hoyolab, "hsr_characters", characters_through_genshin)
    caplog.set_level(logging.DEBUG)

    assert cli.main(["-v", "gacha", "hsr", "characters"]) == 0

    assert uid not in caplog.text
    assert "SAMPLECOOKIE" not in caplog.text


@pytest.mark.parametrize(
    ("flags", "level"), [([], logging.INFO), (["-v"], logging.DEBUG)], ids=["default", "verbose"]
)
def test_genshin_model_parsing_warnings_stay_off_the_console_but_cyrenes_own_gacha_warnings_show(
    flags, level, home, games, monkeypatch, caplog
):
    def calendar_with_an_event_status_genshin_cannot_name() -> HsrCalendar:
        # As on the owner's PC: genshin.py keeps an act_status it has no enum member for, and
        # warns about it from genshin.models.model on every read of the HSR calendar.
        prevent_enum_error("DoubleRewardActStatusFinish", HSREventStatus)
        # A HoYoLAB failure Cyrene logs herself, as HoyolabClient does for a timeout.
        logging.getLogger("waifu.gacha.hoyolab").warning("HoYoLAB unreachable: TimeoutError")
        return HsrCalendar(None, (), (), ())

    monkeypatch.setattr(
        games.hoyolab, "hsr_calendar", calendar_with_an_event_status_genshin_cannot_name
    )
    caplog.set_level(level)

    assert cli.main([*flags, "gacha", "hsr", "events"]) == 0

    assert "please report to developer" not in caplog.text
    assert "HoYoLAB unreachable: TimeoutError" in caplog.text


# --- Genshin and ZZZ account data views (v1.23) ------------------------------------------------


class FakeGameDataClient(FakeGameClient):
    """FakeGameClient with Master's Genshin and ZZZ accounts too: what the views read of
    GameDataSource beside the HSR methods."""

    def __init__(self) -> None:
        super().__init__()
        self.rosters: dict[str, tuple[object, ...]] = {"genshin": (), "zzz": ()}
        self.modes: dict[str, Endgame | GachaError] = {}
        self.calendars = dict.fromkeys(("genshin", "zzz"), GameCalendar(None, (), (), ()))
        self.data_failure: GachaError | None = None
        self.asked: list[object] = []

    def genshin_characters(self):
        return self._roster("genshin")

    def genshin_endgame(self, modes):
        return self._endgame("genshin", modes)

    def genshin_calendar(self):
        return self._calendar("genshin")

    def zzz_characters(self):
        return self._roster("zzz")

    def zzz_endgame(self, modes):
        return self._endgame("zzz", modes)

    def zzz_calendar(self):
        return self._calendar("zzz")

    def _roster(self, game: str) -> tuple[object, ...]:
        self.asked.append((game, "characters"))
        if self.data_failure is not None:
            raise self.data_failure
        return self.rosters[game]

    def _endgame(self, game: str, modes) -> dict[str, Endgame | GachaError]:
        modes = list(modes)
        self.asked.append((game, "endgame", modes))
        return {mode: self.modes[mode] for mode in modes}

    def _calendar(self, game: str) -> GameCalendar:
        self.asked.append((game, "calendar"))
        if self.data_failure is not None:
            raise self.data_failure
        return self.calendars[game]


@pytest.fixture
def game_data(games) -> FakeGameDataClient:
    games.hoyolab = FakeGameDataClient()
    return games.hoyolab


def test_the_views_fake_offers_only_what_hoyolab_client_offers():
    """The gacha views are tested against FakeGameDataClient, so each method it offers must
    be one HoyolabClient has, with the same parameters first; otherwise a view could pass
    here and fail on Master's real account."""
    for name, method in inspect.getmembers(FakeGameDataClient, inspect.isfunction):
        if name.startswith("_"):
            continue
        given = list(inspect.signature(method).parameters)
        real = list(inspect.signature(getattr(HoyolabClient, name)).parameters)
        assert real[: len(given)] == given, name


def genshin_character(
    name: str,
    rarity: int,
    level: int,
    constellation: int,
    element: str,
    weapon_type: str,
    weapon: GenshinWeapon,
) -> GenshinCharacter:
    return GenshinCharacter(
        10000089, name, rarity, level, constellation, element, weapon_type, weapon
    )


def zzz_agent(
    name: str, rarity: str, level: int, mindscape: int, element: str, specialty: str
) -> ZzzAgent:
    return ZzzAgent(1191, name, name, rarity, level, mindscape, element, specialty)


def one_character_each() -> dict[str, tuple[object, ...]]:
    furina_weapon = GenshinWeapon("Favonius Sword", 90, 5, 4)
    return {
        "genshin": (genshin_character("Furina", 5, 90, 2, "Hydro", "Sword", furina_weapon),),
        "zzz": (zzz_agent("Ellen", "S", 60, 2, "Ice", "Attack"),),
    }


def endgame(mode: str, *, has_data: bool = True, ends_at: datetime | None = None, **facts):
    """This season of one mode with only the facts given: a mode gives what it has."""
    known = dict.fromkeys(
        ("season", "stars", "deepest", "difficulty", "rating", "score", "max_score", "seconds")
    )
    return Endgame(
        mode=mode, ends_at=ends_at, has_data=has_data, battles=None, stages=(), **known | facts
    )


def test_gacha_help_offers_the_account_data_of_each_game(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "300")
    monkeypatch.setenv("PYTHON_COLORS", "0")

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["gacha", "--help"])

    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "data akun game (karakter, endgame, kalender event)" in out
    for short in ("HSR", "Genshin", "ZZZ"):
        assert f"Data akun {short} dari HoYoLAB: karakter, endgame musim ini, atau kalender" in out


def test_gacha_genshin_characters_prints_one_line_per_character_strongest_first(
    home, game_data, capsys
):
    flute = GenshinWeapon("The Flute", 90, 5, 4)
    fragments = GenshinWeapon("Sacrificial Fragments", 80, 3, 4)
    splendor = GenshinWeapon("Splendor of Tranquil Waters", 90, 1, 5)
    game_data.rosters["genshin"] = (
        genshin_character("Bennett", 4, 90, 6, "Pyro", "Sword", flute),
        genshin_character("Nahida", 5, 80, 0, "Dendro", "Catalyst", fragments),
        genshin_character("Furina", 5, 90, 2, "Hydro", "Sword", splendor),
    )

    assert cli.main(["gacha", "genshin", "characters"]) == 0

    assert game_data.asked == [("genshin", "characters")]
    assert capsys.readouterr().out.splitlines() == [
        "Furina ★5 Lv90 C2 (Hydro, Sword), Splendor of Tranquil Waters Lv90 R1",
        "Nahida ★5 Lv80 C0 (Dendro, Catalyst), Sacrificial Fragments Lv80 R3",
        "Bennett ★4 Lv90 C6 (Pyro, Sword), The Flute Lv90 R5",
    ]


def test_gacha_zzz_characters_prints_one_line_per_agent_s_rank_first(home, game_data, capsys):
    game_data.rosters["zzz"] = (
        zzz_agent("Anby", "A", 60, 6, "Electric", "Stun"),
        zzz_agent("Miyabi", "S", 50, 0, "Ice", "Anomaly"),
        zzz_agent("Ellen", "S", 60, 2, "Ice", "Attack"),
    )

    assert cli.main(["gacha", "zzz", "characters"]) == 0

    assert game_data.asked == [("zzz", "characters")]
    assert capsys.readouterr().out.splitlines() == [
        "Ellen S Lv60 M2 (Ice, Attack)",
        "Miyabi S Lv50 M0 (Ice, Anomaly)",
        "Anby A Lv60 M6 (Electric, Stun)",
    ]


@pytest.mark.parametrize(
    ("game", "warning"),
    [
        ("genshin", "! Battle Chronicle HoYoLAB tidak memuat satu pun karakter Genshin.\n"),
        ("zzz", "! Battle Chronicle HoYoLAB tidak memuat satu pun agent ZZZ.\n"),
    ],
    ids=["genshin", "zzz"],
)
def test_gacha_game_characters_without_any_character_says_so(
    game, warning, home, game_data, capsys
):
    assert cli.main(["gacha", game, "characters"]) == 1

    assert capsys.readouterr().out == warning


def test_gacha_genshin_endgame_prints_one_line_per_mode_with_the_facts_it_has(
    home, game_data, capsys
):
    abyss_ends = from_now(days=30, hours=4, minutes=30)
    theater_ends, stygian_ends = from_now(days=12, hours=3), from_now(hours=5, minutes=30)
    game_data.modes = {
        "spiral_abyss": endgame("spiral_abyss", ends_at=abyss_ends, stars=36, deepest="12-3"),
        "imaginarium_theater": endgame(
            "imaginarium_theater",
            ends_at=theater_ends,
            stars=10,
            deepest="Act 10",
            difficulty="Visionary",
        ),
        "stygian_onslaught": endgame(
            "stygian_onslaught",
            season="Driftwood Reverie",
            ends_at=stygian_ends,
            difficulty="6",
            seconds=95,
        ),
    }

    assert cli.main(["gacha", "genshin", "endgame"]) == 0

    assert game_data.asked == [("genshin", "endgame", list(GAME_MODES["genshin"]))]
    assert capsys.readouterr().out.splitlines() == [
        "Spiral Abyss: 36 bintang, terdalam 12-3, "
        f"berakhir {local(abyss_ends)} (30 hari 4 jam lagi)",
        "Imaginarium Theater: 10 bintang, terdalam Act 10, difficulty Visionary, "
        f"berakhir {local(theater_ends)} (12 hari 3 jam lagi)",
        "Stygian Onslaught: difficulty 6, 95 detik, "
        f"berakhir {local(stygian_ends)} (5 jam 30 menit lagi)",
    ]


def test_gacha_zzz_endgame_says_which_mode_is_not_played_this_season(home, game_data, capsys):
    shiyu_ends, assault_ends = from_now(days=9), from_now(days=2, hours=6)
    game_data.modes = {
        "shiyu_defense": endgame("shiyu_defense", has_data=False, ends_at=shiyu_ends),
        "deadly_assault": endgame("deadly_assault", ends_at=assault_ends, stars=7, score=30000),
    }

    assert cli.main(["gacha", "zzz", "endgame"]) == 0

    assert game_data.asked == [("zzz", "endgame", list(GAME_MODES["zzz"]))]
    assert capsys.readouterr().out.splitlines() == [
        f"Shiyu Defense: belum dimainkan musim ini, berakhir {local(shiyu_ends)} (9 hari lagi)",
        "Deadly Assault: 7 bintang, skor 30000, "
        f"berakhir {local(assault_ends)} (2 hari 6 jam lagi)",
    ]


def test_gacha_zzz_endgame_shows_the_other_mode_when_one_cannot_be_read(home, game_data, capsys):
    game_data.modes = {
        "shiyu_defense": endgame(
            "shiyu_defense", rating="S+", score=52000, max_score=60000, seconds=400
        ),
        "deadly_assault": GachaError("other", "HoYoLAB menolak permintaan ZZZ (kode -1)."),
    }

    assert cli.main(["gacha", "zzz", "endgame"]) == 1

    assert capsys.readouterr().out.splitlines() == [
        "Shiyu Defense: rating S+, skor 52000/60000, 400 detik",
        "Deadly Assault: ✗ HoYoLAB menolak permintaan ZZZ (kode -1).",
    ]


def test_gacha_genshin_events_prints_the_banners_running_events_and_endgame_windows(
    home, game_data, capsys
):
    banner_ends, next_banner = from_now(days=4, hours=12, minutes=30), from_now(days=6, hours=2)
    soon, later, season_ends = from_now(hours=5, minutes=30), from_now(days=9), from_now(days=20)
    started, ended = from_now(days=-2), from_now(hours=-1)
    game_data.calendars["genshin"] = GameCalendar(
        "5.2",
        (
            Banner(
                "character",
                "Ballad in Goblets",
                ("Venti",),
                ("Xiangling", "Bennett", "Noelle"),
                from_now(days=-10),
                banner_ends,
            ),
            Banner(
                "weapon",
                "Epitome Invocation",
                ("Elegy for the End", "Skyward Harp"),
                ("The Flute",),
                next_banner,
                from_now(days=27),
            ),
            Banner("chronicled", None, ("Albedo",), (), from_now(days=-10), banner_ends),
        ),
        (
            GameEvent("Lantern Rite", started, later, None, None, False),
            GameEvent("Hydro Trial", started, soon, None, None, False),
            GameEvent("Login Gift", None, None, None, None, True),
            GameEvent("Windblume Fest", from_now(days=2), from_now(days=20), None, None, False),
        ),
        (
            ChallengeWindow("spiral_abyss", "Abyssal Moon Spire", started, season_ends, 30, 36),
            # HoYoLAB names this window after its mode: the reader gives it no name.
            ChallengeWindow("imaginarium_theater", None, started, later, 8, None),
            ChallengeWindow(
                "imaginarium_theater", "Ended Season", from_now(days=-30), ended, 10, None
            ),
        ),
    )

    assert cli.main(["gacha", "genshin", "events"]) == 0

    assert game_data.asked == [("genshin", "calendar")]
    assert capsys.readouterr().out.splitlines() == [
        "Kalender Genshin versi 5.2",
        "Banner:",
        "  Ballad in Goblets (karakter): 5★ Venti; 4★ Xiangling, Bennett, Noelle; "
        f"berakhir {local(banner_ends)} (4 hari 12 jam lagi)",
        "  Epitome Invocation (weapon): 5★ Elegy for the End, Skyward Harp; 4★ The Flute; "
        f"mulai {local(next_banner)} (6 hari 2 jam lagi)",
        f"  Banner chronicled: 5★ Albedo; berakhir {local(banner_ends)} (4 hari 12 jam lagi)",
        "Event:",
        f"  Hydro Trial: berakhir {local(soon)} (5 jam 30 menit lagi)",
        f"  Lantern Rite: berakhir {local(later)} (9 hari lagi)",
        "  Login Gift: selesai",
        "Endgame:",
        f"  Spiral Abyss (Abyssal Moon Spire): 30/36, berakhir {local(season_ends)} (20 hari lagi)",
        f"  Imaginarium Theater: berakhir {local(later)} (9 hari lagi)",
    ]


def test_gacha_zzz_events_names_the_banners_by_kind_and_rank_without_endgame_windows(
    home, game_data, capsys
):
    banner_ends, next_banner = from_now(days=4, hours=12, minutes=30), from_now(days=6, hours=2)
    later = from_now(days=9)
    game_data.calendars["zzz"] = GameCalendar(
        None,
        (
            Banner("agent", None, ("Ellen",), ("Soukaku", "Anton"), from_now(days=-3), banner_ends),
            Banner("w_engine", None, (), (), from_now(days=-3), banner_ends),
            Banner("exclusive_rescreening", None, ("Miyabi",), (), next_banner, from_now(days=20)),
        ),
        (GameEvent("Ridu Weekly", from_now(days=-2), later, 300, 1000, False),),
        (),
    )

    assert cli.main(["gacha", "zzz", "events"]) == 0

    assert game_data.asked == [("zzz", "calendar")]
    assert capsys.readouterr().out.splitlines() == [
        "Kalender ZZZ",
        "Banner:",
        "  Banner agent: S Ellen; A Soukaku, Anton; "
        f"berakhir {local(banner_ends)} (4 hari 12 jam lagi)",
        f"  Banner W-Engine: berakhir {local(banner_ends)} (4 hari 12 jam lagi)",
        f"  Banner exclusive_rescreening: S Miyabi; mulai {local(next_banner)} (6 hari 2 jam lagi)",
        "Event:",
        # A ZZZ event counts the Polychrome it gives, not quest steps.
        f"  Ridu Weekly: 300/1000 Polychrome, berakhir {local(later)} (9 hari lagi)",
    ]


@pytest.mark.parametrize(
    ("game", "lines"),
    [
        (
            "genshin",
            ["Kalender Genshin", "Banner: tidak ada", "Event: tidak ada", "Endgame: tidak ada"],
        ),
        ("zzz", ["Kalender ZZZ", "Banner: tidak ada", "Event: tidak ada"]),
    ],
    ids=["genshin", "zzz"],
)
def test_gacha_game_events_with_an_empty_calendar_says_there_is_nothing(
    game, lines, home, game_data, capsys
):
    assert cli.main(["gacha", game, "events"]) == 0

    assert capsys.readouterr().out.splitlines() == lines


@pytest.mark.parametrize("game", ["genshin", "zzz"])
@pytest.mark.parametrize(("view", "asked"), [("characters", "characters"), ("events", "calendar")])
def test_gacha_game_data_that_hoyolab_refuses_prints_why_and_fails(
    game, view, asked, home, game_data, capsys
):
    refusal = (
        f"Data {GAMES[game].short} ini tidak bisa dibaca karena Battle Chronicle tidak publik."
    )
    game_data.data_failure = GachaError("chronicle_private", refusal)

    assert cli.main(["gacha", game, view]) == 1

    assert game_data.asked == [(game, asked)]
    assert capsys.readouterr().out == f"✗ {refusal}\n"


@pytest.mark.parametrize("game", ["genshin", "zzz"])
@pytest.mark.parametrize("view", ["characters", "endgame", "events"])
def test_gacha_game_data_works_with_the_gacha_buddy_off(game, view, home, games, game_data):
    game_data.rosters = one_character_each()
    game_data.modes = {mode: endgame(mode, has_data=False) for mode in GAME_MODES[game]}

    assert cli.main(["gacha", game, view]) == 0

    [config] = games.built
    assert config.gacha.enabled is False


@pytest.mark.parametrize(
    ("game", "method", "url", "query", "body"),
    [
        (
            "genshin",
            "POST",
            "https://sg-public-api.hoyolab.com/event/game_record/genshin/api/character/list",
            None,
            {"role_id": 800000001, "server": "os_asia"},
        ),
        (
            "zzz",
            "GET",
            "https://sg-act-public-api.hoyolab.com/event/game_record_zzz/api/zzz/avatar/basic",
            {"role_id": 1300000003, "server": "prod_gf_jp"},
            None,
        ),
    ],
    ids=["genshin", "zzz"],
)
def test_verbose_logging_never_reveals_the_genshin_or_zzz_uid_genshin_logs_with_each_request(
    game, method, url, query, body, home, game_data, monkeypatch, caplog
):
    uid = str((query or body)["role_id"])

    def characters_through_genshin() -> tuple[object, ...]:
        # Every new genshin.Client sets the "genshin" logger back to NOTSET, and before each
        # request genshin.py's own hook logs the URL with its query, or the POST body, at
        # DEBUG: the uid either way. The hook runs here on its logger, without a client.
        logging.getLogger("genshin").setLevel(logging.NOTSET)
        base = genshin.client.components.base.BaseClient
        hooked = types.SimpleNamespace(logger=base.logger)
        asyncio.run(base._request_hook(hooked, method, url, params=query, data=body))
        return one_character_each()[game]

    monkeypatch.setattr(game_data, f"{game}_characters", characters_through_genshin)
    caplog.set_level(logging.DEBUG)

    assert cli.main(["-v", "gacha", game, "characters"]) == 0

    assert uid not in caplog.text


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
    # Then the schedule, to-do, focus, briefing, PC, play time, offers, and music.
    [tools, _, _, _, _, _, _, _, _] = brain.providers
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

    assert [type(tools) for tools in brains[0].providers] == [
        MemoryTools,
        ScheduleTools,
        TodoTools,
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
    assert "Master suka kopi." in brains[0].memory_section()


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_the_memory_off_the_brain_gets_neither_the_tools_nor_the_notes(
    argv, home, run_fakes, brains, monkeypatch
):
    disable_memory(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        ScheduleTools,
        TodoTools,
        *([FocusTools] if argv[0] == "run" else []),
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
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
    # Whole words only: "Peringatan hujan" holds "ingatan" too.
    [line] = [line for line in out.splitlines() if re.search(r"\b[Ii]ngatan\b", line)]
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
    assert [type(tools) for tools in brain.providers] == [
        MemoryTools,
        ScheduleTools,
        TodoTools,
        FocusTools,
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
    [scheduler] = schedulers
    assert scheduler.states == ["started", "stopped"]
    assert scheduler.listening == [False, True]
    assert refused == [False, False, False]
    # The reminder is said and pushed; the device command and the routine run without a word.
    # Master set it himself, so it is said through `remind`, also during a focus block.
    assert RecordingAssistant.instances[0].reminded == [(MEDICINE, "happy")]
    assert RecordingAssistant.instances[0].announced == []
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
    assert RecordingAssistant.instances[0].reminded == [
        ("Master, ada pengingat yang terlewat jam 07.00: Master, bangun!", "sad")
    ]
    assert store.entries() == []


def test_run_gives_her_tools_and_schedule_what_she_calls_the_owner(
    home, run_fakes, brains, schedulers
):
    write_config(home, "assistant:\n  owner_call: Sayang\nschedule:\n  late_grace_minutes: 5\n")

    assert cli.main(["run", "--no-speaker-check"]) == 0

    [memory_tools] = [tools for tools in brains[0].providers if isinstance(tools, MemoryTools)]
    [remember] = [spec for spec in memory_tools.specs() if spec.name == "remember"]
    assert "('Sayang alergi udang.')" in remember.description
    tools = schedule_tools(brains[0])
    [schedule] = [spec for spec in tools.specs() if spec.name == "schedule"]
    assert "('Sayang, sudah jam tiga, waktunya minum obat!')" in schedule.description
    at = tomorrow_at(7)
    assert schedule_at(tools, at, reminder="Bangun!")[1] is False
    schedulers[0].tick_at(at + timedelta(minutes=6))  # later than the 5 minutes of grace
    assert RecordingAssistant.instances[0].reminded == [
        ("Sayang, ada pengingat yang terlewat jam 07.00: Bangun!", "sad")
    ]


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

    assert [type(tools) for tools in brains[0].providers] == [
        MemoryTools,
        TodoTools,
        *([FocusTools] if argv[0] == "run" else []),
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
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


# --- PC media and apps -----------------------------------------------------------------------


def write_out_example_device(home: Path, device_id: str) -> None:
    """The example config with its commented device `device_id` (`musik` or `aplikasi`)
    written out, from its `# - id:` line to the next blank line."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    commented = text[text.index(f"  # - id: {device_id}\n") :].split("\n\n", 1)[0]
    written_out = "\n".join(line.replace("# ", "", 1) for line in commented.splitlines())
    config.write_text(text.replace(commented, written_out), encoding="utf-8")


def app_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line[2:].startswith("Aplikasi ")]


def test_check_marks_each_app_found_missing_or_a_launcher_url_it_cannot_check(
    home, nothing_installed, capsys
):
    spotify = home / "Pintasan" / "Spotify.lnk"
    spotify.parent.mkdir()
    spotify.write_bytes(b"")
    genshin = home / "HoYoPlay" / "launcher.exe"
    write_config(
        home,
        "devices:\n"
        "  - id: aplikasi\n"
        "    name: Aplikasi PC\n"
        "    type: apps\n"
        "    apps:\n"
        f"      spotify: '{spotify}'\n"
        f"      genshin: '{genshin}'\n"
        "      zzz: 'steam://rungameid/2507950'\n"
        "      hsr: 'Steam://rungameid/1234'\n",
    )

    cli.main(["check"])

    assert app_lines(capsys.readouterr().out) == [
        f"✓ Aplikasi spotify: {spotify}",
        f"✗ Aplikasi genshin: file tidak ditemukan ({genshin})",
        "✓ Aplikasi zzz: steam://rungameid/2507950 (URL, tidak dicek)",
        # Config and the launcher read the scheme in any case, so the check does too.
        "✓ Aplikasi hsr: Steam://rungameid/1234 (URL, tidak dicek)",
    ]


def test_check_fails_a_url_shortcut_that_opens_anything_but_a_launcher_url(
    home, nothing_installed, capsys
):
    # A .url shortcut opens whatever its URL= line says, a web page or a script too; check only
    # reads it (nothing is opened).
    shortcuts = {
        "hades": "[InternetShortcut]\r\nIDList=\r\nURL=steam://rungameid/1145360\r\n",
        "fortnite": "[{000214A0-0000-0000-C000-000000000046}]\nProp3=19,0\n"
        "[InternetShortcut]\nURL=COM.EPICGAMES.LAUNCHER://apps/fn?action=launch\n",
        "web": "[InternetShortcut]\nURL=https://example.com/\n",
        "skrip": "[InternetShortcut]\nURL=file:///C:/x/run.bat\n",
        "kosong": "[InternetShortcut]\nIconIndex=0\n",
        "rusak": "bukan shortcut",
    }
    paths = {}
    for slug, text in shortcuts.items():
        paths[slug] = home / f"{slug}.url"
        paths[slug].write_bytes(text.encode())
    write_config(
        home,
        "devices:\n  - id: aplikasi\n    name: Aplikasi PC\n    type: apps\n    apps:\n"
        + "".join(f"      {slug}: '{path}'\n" for slug, path in paths.items()),
    )

    cli.main(["check"])

    launcher = "bukan URL steam:// atau com.epicgames.launcher://"
    assert app_lines(capsys.readouterr().out) == [
        f"✓ Aplikasi hades: {paths['hades']}",
        f"✓ Aplikasi fortnite: {paths['fortnite']}",
        f"✗ Aplikasi web: shortcut ini membuka https://example.com/, {launcher} ({paths['web']})",
        f"✗ Aplikasi skrip: shortcut ini membuka file:///C:/x/run.bat, {launcher} "
        f"({paths['skrip']})",
        f"✗ Aplikasi kosong: alamat di shortcut ini tidak bisa dibaca ({paths['kosong']})",
        f"✗ Aplikasi rusak: alamat di shortcut ini tidak bisa dibaca ({paths['rusak']})",
    ]


def test_check_finds_an_app_written_with_forward_slashes(home, nothing_installed, capsys):
    spotify = home / "Spotify.lnk"
    spotify.write_bytes(b"")
    target = spotify.as_posix()
    write_config(
        home,
        "devices:\n"
        "  - {id: aplikasi, name: Aplikasi PC, type: apps, apps: {spotify: '" + target + "'}}\n",
    )

    cli.main(["check"])

    assert app_lines(capsys.readouterr().out) == [f"✓ Aplikasi spotify: {target}"]


def test_the_example_media_and_apps_devices_are_valid_once_written_out(home, capsys):
    write_out_example_device(home, "musik")
    write_out_example_device(home, "aplikasi")

    assert cli.main(["devices", "--dry-run"]) == 0

    rows = {line.split()[0]: line.split()[1] for line in capsys.readouterr().out.splitlines()}
    assert (rows["musik"], rows["aplikasi"]) == ("media", "apps")


def test_check_marks_the_example_shortcuts_missing_until_they_point_at_this_pc(
    home, nothing_installed, capsys
):
    write_out_example_device(home, "aplikasi")

    assert cli.main(["check"]) == 1

    lines = app_lines(capsys.readouterr().out)
    shortcuts = [line for line in lines if "Start Menu" in line]
    assert shortcuts and all(
        line.startswith("✗ ") and "file tidak ditemukan" in line for line in shortcuts
    )
    assert any(line.startswith("✓ ") and "steam://rungameid/" in line for line in lines)


# --- looking at the screen -------------------------------------------------------------------


class FakeCapture:
    """Stands in for capture_screen: records each requested size, never touches the screen."""

    png = b"\x89PNG synthetic"

    def __init__(self) -> None:
        self.sizes: list[int] = []

    def __call__(self, max_side: int) -> ScreenImage:
        self.sizes.append(max_side)
        return ScreenImage(png=self.png, width=16, height=9)


@pytest.fixture
def capture(monkeypatch) -> FakeCapture:
    fake = FakeCapture()
    monkeypatch.setattr(cli, "capture_screen", fake)
    return fake


def use_claude(home: Path) -> None:
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    claude = text.replace("  backend: ollama", "  backend: claude", 1)
    assert claude != text
    config.write_text(claude, encoding="utf-8")


def write_out_example_vision(home: Path, *, enabled: bool = True) -> None:
    """The example config with its commented `vision` block written out."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    commented = text[text.index("# vision:\n") :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())
    if not enabled:
        written_out = written_out.replace("enabled: true", "enabled: false")
    config.write_text(text.replace(commented, written_out), encoding="utf-8")


def screen_tools(brain: HouseBrain) -> ScreenTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, ScreenTools)]
    return tools


def vision_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Melihat layar" in line]
    return line


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_claude_she_may_look_at_the_screen_at_the_configured_size(
    argv, home, run_fakes, brains, capture, monkeypatch
):
    use_claude(home)
    with (home / "config.yaml").open("a", encoding="utf-8") as config:
        config.write("\nvision:\n  max_side: 1024\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        MemoryTools,
        ScheduleTools,
        TodoTools,
        *([FocusTools] if argv[0] == "run" else []),
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
        ScreenTools,
        UsageTools,
    ]
    screen = screen_tools(brains[0])
    screen.begin_turn("Cyrene, lihat layarku")
    assert screen.run("look_at_screen", {}) == ('{"ok":true,"width":16,"height":9}', False)
    assert capture.sizes == [1024]


def test_her_name_and_wake_words_never_ask_her_to_look(home, brains, capture, monkeypatch):
    write_config(
        home,
        "assistant:\n  name: Monitor\n  wake_words: [monitor, hai screen]\n"
        "brain:\n  backend: claude\n",
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    screen = screen_tools(brains[0])
    for words in ["Monitor, nyalakan lampu kamar.", "Hai screen, aku pulang."]:
        screen.begin_turn(words)
        assert screen.run("look_at_screen", {})[1] is True
    screen.begin_turn("Monitor, lihat layarku.")
    assert screen.run("look_at_screen", {})[1] is False
    assert capture.sizes == [1280]


def test_what_she_calls_the_owner_never_asks_her_to_look(home, brains, capture, monkeypatch):
    # A made-up call that is a look word: it is left out of Master's words like her names.
    write_config(home, "assistant:\n  owner_call: Monitor\nbrain:\n  backend: claude\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    screen = screen_tools(brains[0])
    screen.begin_turn("Monitor, aku pulang.")
    assert screen.run("look_at_screen", {})[1] is True
    assert capture.sizes == []


def sse_event(name: str, data: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()


def streamed_message(message: dict) -> httpx2.Response:
    """`message` as the API streams it (as it does with web search), each block whole."""
    started = {**message, "content": [], "stop_reason": None}
    events = [sse_event("message_start", {"type": "message_start", "message": started})]
    for index, block in enumerate(message["content"]):
        start = {"type": "content_block_start", "index": index, "content_block": block}
        stop = {"type": "content_block_stop", "index": index}
        events += [sse_event("content_block_start", start), sse_event("content_block_stop", stop)]
    delta = {"stop_reason": message["stop_reason"], "stop_sequence": None}
    finished = {"type": "message_delta", "delta": delta, "usage": message["usage"]}
    events += [sse_event("message_delta", finished), sse_event("message_stop", {})]
    return httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, content=b"".join(events)
    )


def test_verbose_logging_never_writes_out_a_screenshot_or_the_conversation(
    home, capture, monkeypatch, caplog
):
    # The real Claude brain and Anthropic SDK, with a mock transport instead of the network.
    use_claude(home)
    (home / "config").mkdir()
    shutil.copy(ROOT / "config" / "persona.md", home / "config" / "persona.md")
    sent: list[dict] = []
    usage = {"input_tokens": 10, "output_tokens": 5}
    replies = [
        [{"type": "tool_use", "id": "toolu_1", "name": "look_at_screen", "input": {}}],
        [{"type": "text", "text": "Kelihatan, Master!"}],
        [{"type": "text", "text": "Sama-sama, Master!"}],
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        content = replies[len(sent) - 1]
        stop_reason = "tool_use" if content[0]["type"] == "tool_use" else "end_turn"
        message = {
            "id": f"msg_{len(sent)}",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "usage": usage,
        }
        assert sent[-1]["stream"] is True  # web search is on by default
        return streamed_message(message)

    real_anthropic = anthropic.Anthropic

    def build(**kwargs: object) -> anthropic.Anthropic:
        transport = httpx2.MockTransport(handler)
        return real_anthropic(
            api_key="test-key", http_client=httpx2.Client(transport=transport), **kwargs
        )

    monkeypatch.setattr(claude_module.anthropic, "Anthropic", build)
    monkeypatch.setattr(sys, "stdin", io.StringIO("Cyrene, lihat layarku\nmakasih\nkeluar\n"))
    caplog.set_level(logging.DEBUG)

    assert cli.main(["-v", "chat", "--dry-run"]) == 0

    screenshot = base64.b64encode(capture.png).decode("ascii")
    # Sent with the look and again with the next turn, which carries the whole session...
    assert [screenshot in json.dumps(body) for body in sent] == [False, True, True]
    # ...but -v output gets pasted when asking for help, and --log-file keeps it on disk.
    assert screenshot not in caplog.text
    assert "makasih" not in caplog.text


def test_the_example_vision_block_is_valid_once_written_out(home, brains, capture, monkeypatch):
    use_claude(home)
    write_out_example_vision(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    screen = screen_tools(brains[0])
    screen.begin_turn("Cyrene, lihat layarku")
    screen.run("look_at_screen", {})
    assert capture.sizes == [1280]


@pytest.mark.parametrize(
    ("claude", "vision"),
    [(False, True), (True, False), (False, False)],
    ids=["ollama", "claude_with_vision_off", "ollama_with_vision_off"],
)
def test_without_claude_or_with_vision_off_the_brain_gets_no_screen_tool(
    claude, vision, home, brains, capture, monkeypatch
):
    if claude:
        use_claude(home)
    write_out_example_vision(home, enabled=vision)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        MemoryTools,
        ScheduleTools,
        TodoTools,
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
        *([UsageTools] if claude else []),  # what her requests cost, with Claude only
    ]
    assert capture.sizes == []


@pytest.mark.parametrize(
    ("claude", "vision", "expected"),
    [
        (True, True, "✓ Melihat layar: aktif (Claude)"),
        (False, True, "! Melihat layar: tidak tersedia dengan Ollama"),
        (True, False, "! Melihat layar mati"),
        (False, False, "! Melihat layar mati"),
    ],
    ids=["claude", "ollama", "claude_with_vision_off", "ollama_with_vision_off"],
)
def test_check_reports_whether_she_can_look_at_the_screen(
    claude, vision, expected, home, nothing_installed, capture, capsys
):
    if claude:
        use_claude(home)
    write_out_example_vision(home, enabled=vision)

    cli.main(["check"])

    assert vision_line(capsys.readouterr().out) == expected
    assert capture.sizes == []


# --- morning briefing ------------------------------------------------------------------------

GEOCODING_HOST = "geocoding-api.open-meteo.com"
FORECAST_HOST = "api.open-meteo.com"
BANDUNG = {
    "id": 1650357,
    "name": "Bandung",
    "latitude": -6.90389,
    "longitude": 107.61861,
    "country_code": "ID",
    "timezone": "Asia/Jakarta",
    "country": "Indonesia",
    "admin1": "Jawa Barat",
}


def open_meteo_forecast(today: date) -> dict:
    """A forecast shaped like Open-Meteo's documented reply to the briefing's request (current
    values, two days of hourly and daily arrays in the place's time zone), trimmed to the
    fields the weather client reads and the time zone it was given."""
    days = [today, today + timedelta(days=1)]
    return {
        "timezone": "Asia/Jakarta",
        "utc_offset_seconds": 25200,
        "current": {
            "time": f"{today}T07:15",
            "interval": 900,
            "temperature_2m": 21.4,
            "weather_code": 2,
        },
        "hourly": {
            "time": [f"{day}T{hour:02d}:00" for day in days for hour in range(24)],
            "weather_code": [2] * 24 + [61] * 24,
            "precipitation_probability": [10] * 24 + [70] * 24,
            "precipitation": [0.0] * 24 + [1.5] * 24,
        },
        "daily": {
            "time": [day.isoformat() for day in days],
            "weather_code": [95, 61],
            "temperature_2m_max": [27.9, 28.3],
            "temperature_2m_min": [18.6, 18.9],
            "precipitation_probability_max": [85, 40],
        },
    }


class FakeOpenMeteo:
    """The weather client's fetch: answers like Open-Meteo's geocoding and forecast APIs for
    today (with `forecast`, which a test may change), or raises `failure`; records every URL
    and never goes online."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.failure: Exception | None = None
        self.forecast = open_meteo_forecast(date.today())

    def __call__(self, url: str, timeout_s: float) -> object:
        self.urls.append(url)
        if self.failure is not None:
            raise self.failure
        if urllib.parse.urlsplit(url).hostname == GEOCODING_HOST:
            return {"results": [BANDUNG], "generationtime_ms": 0.6}
        return self.forecast

    def queries(self, host: str) -> list[dict[str, list[str]]]:
        return [
            urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            for url in self.urls
            if urllib.parse.urlsplit(url).hostname == host
        ]


@pytest.fixture
def open_meteo(monkeypatch) -> FakeOpenMeteo:
    fake = FakeOpenMeteo()
    monkeypatch.setattr(cli, "WeatherClient", lambda **place: WeatherClient(**place, fetch=fake))
    return fake


def write_out_example_briefing(home: Path) -> None:
    """The example config with its commented `briefing` block written out (city Bandung)."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    commented = text[text.index("# briefing:\n") :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())
    config.write_text(text.replace(commented, written_out), encoding="utf-8")


def append_config(home: Path, text: str) -> None:
    with (home / "config.yaml").open("a", encoding="utf-8") as config:
        config.write(f"\n{text}")


def save_today_s_schedule(path: Path) -> None:
    """Two entries today (15:00 and 23:00, saved as of midnight, whatever the time is now) and
    one tomorrow, which today's briefing leaves out."""
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    store = ScheduleStore(path, now=lambda: midnight)
    store.add(midnight.replace(hour=23), "daily", RoutineAction("tidur"))
    store.add(midnight.replace(hour=15), "none", Reminder(MEDICINE))
    store.add(midnight + timedelta(days=1, hours=7), "none", Reminder("Master, bangun!"))


def briefing_tools(brain: HouseBrain) -> BriefingTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, BriefingTools)]
    return tools


def daily_briefing(brain: HouseBrain) -> dict:
    result, is_error = briefing_tools(brain).run("daily_briefing", {})
    assert is_error is False
    return json.loads(result)


def today_text() -> str:
    now = datetime.now()
    return f"{WEEKDAYS_ID[now.weekday()]}, {now:%d-%m-%Y}"


def test_run_greets_once_a_day_while_she_listens_and_pushes_the_greeting_to_the_phone(
    home, run_fakes, brains, briefing_timers, monkeypatch
):
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert [type(tools) for tools in brains[0].providers][-5:] == [
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
    [timer] = briefing_timers
    assert timer.listening == [False, True]
    timer.tick_at(datetime(2026, 10, 14, 7, 30))
    timer.tick_at(datetime(2026, 10, 14, 9, 0))  # she already greeted today
    [(prompt, on_reply, required_tool)] = RecordingAssistant.instances[0].asked
    assert prompt == BRIEFING_PROMPT
    assert required_tool == DAILY_BRIEFING  # a failed turn (an apology) is never said
    # In a new session, so no figure from an earlier finance answer reaches the greeting or its
    # push.
    assert RecordingAssistant.instances[0].new_sessions == [True]
    on_reply("Selamat pagi, Master! Hari ini cerah.")
    assert notifier.sent == [("Cyrene", "Selamat pagi, Master! Hari ini cerah.", ("sunrise",))]


def test_run_greets_in_the_configured_window_and_remembers_the_day_where_the_config_says(
    home, run_fakes, briefing_timers
):
    write_config(
        home, "briefing:\n  time: '06:00'\n  until: '09:30'\n  state_path: sapa/terakhir.json\n"
    )

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [timer] = briefing_timers
    timer.tick_at(datetime(2026, 10, 14, 9, 30))  # too late today
    late = list(RecordingAssistant.instances[0].asked)
    timer.tick_at(datetime(2026, 10, 15, 6, 0))
    assert late == []
    assert [prompt for prompt, _, _ in RecordingAssistant.instances[0].asked] == [BRIEFING_PROMPT]
    saved = json.loads((home / "sapa" / "terakhir.json").read_text(encoding="utf-8"))
    assert saved["last_date"] == "2026-10-15"


@pytest.mark.parametrize(
    ("push", "notify"), [(False, True), (True, False)], ids=["push_off", "notify_off"]
)
def test_run_greets_without_a_push_when_push_or_notifications_are_off(
    push, notify, home, run_fakes, briefing_timers, monkeypatch
):
    if notify:
        enable_notify(home)
    append_config(home, f"briefing:\n  push: {str(push).lower()}\n")
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    briefing_timers[0].request(BRIEFING_PROMPT)
    [(_, on_reply, _)] = RecordingAssistant.instances[0].asked
    assert on_reply is None
    assert notifier.sent == []


def test_chat_gives_the_brain_the_briefing_tool_but_never_greets_by_herself(
    home, brains, briefing_timers, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers][-5:] == [
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
    assert briefing_timers == []


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_the_briefing_off_she_gets_neither_the_tool_nor_the_greeting(
    argv, home, run_fakes, brains, briefing_timers, monkeypatch
):
    append_config(home, "briefing:\n  enabled: false\n  city: Bandung\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        MemoryTools,
        ScheduleTools,
        TodoTools,
        *([FocusTools] if argv[0] == "run" else []),
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
    assert briefing_timers == []


def test_the_briefing_tool_tells_the_weather_of_the_configured_city_and_today_s_schedule(
    home, brains, schedulers, open_meteo, monkeypatch
):
    write_out_example_briefing(home)
    save_today_s_schedule(schedule_file(home))
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat"]) == 0

    briefing = daily_briefing(brains[0])
    assert briefing["date"] == today_text()
    assert briefing["weather"]["place"] == "Bandung, Jawa Barat"
    assert briefing["weather_problem"] is None
    assert briefing["schedule_today"] == [
        {"time": "15:00", "what": MEDICINE},
        {"time": "23:00", "what": "rutinitas tidur"},
    ]
    assert [query["name"] for query in open_meteo.queries(GEOCODING_HOST)] == [["Bandung"]]


def test_the_briefing_tool_asks_for_the_weather_at_the_coordinates_rather_than_the_city(
    home, brains, open_meteo, monkeypatch
):
    write_config(home, "briefing:\n  city: Jakarta\n  latitude: -6.9175\n  longitude: 107.6191\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert daily_briefing(brains[0])["weather"]["place"] == "Jakarta"  # the name she says
    assert open_meteo.queries(GEOCODING_HOST) == []
    [forecast] = open_meteo.queries(FORECAST_HOST)
    assert (forecast["latitude"], forecast["longitude"]) == (["-6.9175"], ["107.6191"])


def test_without_a_place_the_briefing_tool_says_how_to_set_one_and_asks_no_one(
    home, brains, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0  # the example config leaves briefing commented

    briefing = daily_briefing(brains[0])
    assert briefing["weather"] is None
    assert "briefing.city" in briefing["weather_problem"]


def test_a_test_that_forgets_the_open_meteo_fake_fails_instead_of_asking_it(no_real_weather):
    with pytest.raises(AssertionError, match="a test reached Open-Meteo"):
        cli.WeatherClient(city="Bandung").today()

    assert [urllib.parse.urlsplit(url).hostname for url in no_real_weather] == [GEOCODING_HOST]
    no_real_weather.clear()  # reached on purpose: the guard fails any other test that does


def test_briefing_prints_today_s_date_weather_and_schedule_without_the_brain(
    home, brains, open_meteo, capsys
):
    write_out_example_briefing(home)
    save_today_s_schedule(schedule_file(home))

    assert cli.main(["briefing"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"Ringkasan hari ini: {today_text()}",
        "✓ Cuaca di Bandung, Jawa Barat: berawan, 21.4°C; hari ini 18.6-27.9°C, "
        "peluang hujan 85%; besok pagi hujan",
        "Jadwal hari ini:",
        f"  15:00  {MEDICINE}",
        "  23:00  rutinitas tidur",
    ]
    assert brains == []


def test_briefing_whose_weather_fails_still_prints_the_rest_and_exits_1(home, open_meteo, capsys):
    write_out_example_briefing(home)
    open_meteo.failure = urllib.error.URLError("getaddrinfo failed")

    assert cli.main(["briefing"]) == 1

    assert capsys.readouterr().out.splitlines() == [
        f"Ringkasan hari ini: {today_text()}",
        "✗ Cuaca: Server cuaca tidak bisa dihubungi. Cek koneksi internet, lalu coba lagi.",
        "Tidak ada jadwal hari ini.",
    ]


def test_briefing_whose_schedule_cannot_be_read_still_prints_the_rest_and_exits_1(
    home, open_meteo, capsys
):
    write_out_example_briefing(home)
    schedule_file(home).mkdir(parents=True)  # a folder where the file should be: unreadable

    assert cli.main(["briefing"]) == 1

    assert capsys.readouterr().out.splitlines() == [
        f"Ringkasan hari ini: {today_text()}",
        "✓ Cuaca di Bandung, Jawa Barat: berawan, 21.4°C; hari ini 18.6-27.9°C, "
        "peluang hujan 85%; besok pagi hujan",
        "✗ Jadwal: Jadwal tidak bisa dibaca saat ini.",
    ]


def test_briefing_without_a_place_says_how_to_set_one_and_exits_1(home, capsys):
    assert cli.main(["briefing"]) == 1

    out = capsys.readouterr().out
    assert "✗ Cuaca: cuaca belum diatur (isi briefing.city di config.yaml)" in out


def test_briefing_shows_the_weather_also_with_the_greeting_and_the_schedule_off(
    home, open_meteo, capsys
):
    disable_schedule(home)
    append_config(home, "briefing:\n  enabled: false\n  city: Bandung\n")

    assert cli.main(["briefing"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[1].startswith("✓ Cuaca di Bandung, Jawa Barat: ")
    assert lines[2] == "! Jadwal mati (schedule.enabled: false)"


def briefing_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Sapaan pagi" in line]
    return line


def coordinates_and_hours(home: Path) -> None:
    append_config(
        home,
        "briefing:\n  time: '06:30'\n  until: '10:00'\n  latitude: -6.9175\n"
        "  longitude: 107.6191\n",
    )


def city_and_coordinates(home: Path) -> None:
    append_config(home, "briefing:\n  city: Bandung\n  latitude: -6.9175\n  longitude: 107.6191\n")


def briefing_off(home: Path) -> None:
    append_config(home, "briefing:\n  enabled: false\n")


@pytest.mark.parametrize(
    ("configure", "expected"),
    [
        (lambda home: None, "! Sapaan pagi: cuaca belum diatur (isi briefing.city)"),
        (write_out_example_briefing, "✓ Sapaan pagi: jam 07:00-12:00, cuaca untuk Bandung"),
        (coordinates_and_hours, "✓ Sapaan pagi: jam 06:30-10:00, cuaca untuk -6.9175, 107.6191"),
        (
            city_and_coordinates,  # the forecast is for the coordinates, which she calls Bandung
            "✓ Sapaan pagi: jam 07:00-12:00, cuaca untuk Bandung (-6.9175, 107.6191)",
        ),
        (briefing_off, "! Sapaan pagi mati"),
    ],
    ids=["example", "example_written_out", "coordinates", "city_and_coordinates", "off"],
)
def test_check_reports_when_she_greets_and_where_the_weather_is_for_without_asking_it(
    configure, expected, home, nothing_installed, capsys
):
    configure(home)

    cli.main(["check"])

    assert briefing_line(capsys.readouterr().out) == expected


# --- rain alerts -------------------------------------------------------------------------------

RAIN_TEXT = "Master, sekitar jam tiga sore kemungkinan hujan di Bandung, angkat jemuran ya."


def forecast_hour(
    open_meteo: FakeOpenMeteo, hour: int, *, code: int, chance: int | None, mm: float | None = 0.0
) -> None:
    """Sets today's `hour` (24 and on: tomorrow's) in the fake's forecast."""
    hourly = open_meteo.forecast["hourly"]
    hourly["weather_code"][hour] = code
    hourly["precipitation_probability"][hour] = chance
    hourly["precipitation"][hour] = mm


def dry_tomorrow(open_meteo: FakeOpenMeteo) -> None:
    """The fake rains all day tomorrow; this makes tomorrow as dry as today."""
    for hour in range(24, 48):
        forecast_hour(open_meteo, hour, code=2, chance=10)


def test_run_watches_for_rain_at_the_briefing_s_city_while_she_listens_and_pushes_it(
    home, run_fakes, brains, rain_watchers, open_meteo, monkeypatch
):
    write_out_example_briefing(home)
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = rain_watchers
    assert watcher.config == RainAlertConfig()  # the example config's settings
    assert (watcher.owner_call, watcher.title, watcher.place, watcher.notifier) == (
        "Master",
        "Cyrene",
        "Bandung",  # the city as written, not the geocoded "Bandung, Jawa Barat"
        notifier,
    )
    assert watcher.listening == [False, True]
    assert "mic opened" in watcher.events_when_started  # once she is ready to listen
    assert "mic closed" not in watcher.events_when_stopped
    assert watcher.read_forecast().place == "Bandung, Jawa Barat"  # the briefing's place
    [query] = open_meteo.queries(FORECAST_HOST)
    assert query["hourly"] == ["weather_code,precipitation_probability,precipitation"]
    watcher.announce(RAIN_TEXT, "neutral")
    assistant = RecordingAssistant.instances[0]
    assert assistant.announced == [(RAIN_TEXT, "neutral")]
    # Never inside a conversation: she says it once the follow-up window has closed.
    assert assistant.waiting_for_followup == [RAIN_TEXT]


def test_run_s_rain_watcher_reads_from_a_client_that_leaves_logging_failures_to_it(
    home, run_fakes, brains, rain_watchers, open_meteo, caplog
):
    write_out_example_briefing(home)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = rain_watchers
    open_meteo.failure = urllib.error.URLError("getaddrinfo failed")
    with caplog.at_level(logging.DEBUG, logger="waifu.weather"), pytest.raises(WeatherError):
        watcher.read_forecast()
    levels = [record.levelno for record in caplog.records if record.name == "waifu.weather"]
    assert levels == [logging.DEBUG]  # the watcher logs a run of failures once itself


def test_run_watches_for_rain_as_configured_also_with_the_greeting_off(
    home, run_fakes, brains, rain_watchers
):
    call_the_owner(home, "Sayang")
    append_config(
        home,
        "briefing:\n  enabled: false\n  latitude: -6.82\n  longitude: 107.14\n"
        "rain_alert:\n  lead_minutes: 90\n  threshold: 70\n",
    )

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = rain_watchers
    assert watcher.config == RainAlertConfig(lead_minutes=90, threshold=70)
    # The greeting's place is still where the weather is for; with coordinates only she names
    # no place.
    assert (watcher.owner_call, watcher.place, watcher.notifier) == ("Sayang", None, None)


@pytest.mark.parametrize(
    ("push", "notify"), [(False, True), (True, False)], ids=["push_off", "notify_off"]
)
def test_run_warns_of_rain_without_a_push_when_push_or_notifications_are_off(
    push, notify, home, run_fakes, brains, rain_watchers, monkeypatch
):
    if notify:
        enable_notify(home)
    append_config(home, f"briefing:\n  city: Bandung\nrain_alert:\n  push: {str(push).lower()}\n")
    use_notifier(monkeypatch, FakeNotifier())

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = rain_watchers
    assert watcher.notifier is None


def rain_alerts_off(home: Path) -> None:
    write_out_example_briefing(home)
    append_config(home, "rain_alert:\n  enabled: false\n")


@pytest.mark.parametrize(
    "configure", [lambda home: None, rain_alerts_off], ids=["no_place", "alerts_off"]
)
def test_run_watches_for_no_rain_without_a_place_or_with_the_alerts_off(
    configure, home, run_fakes, brains, rain_watchers
):
    configure(home)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert rain_watchers == []


@pytest.mark.parametrize(
    ("failure", "exit_code"),
    [(SttError("Whisper berhenti."), 1), (KeyboardInterrupt(), 130)],
    ids=["whisper-fails", "ctrl-c"],
)
def test_run_stops_watching_for_rain_however_listening_ends(
    failure, exit_code, home, run_fakes, brains, rain_watchers, monkeypatch
):
    write_out_example_briefing(home)

    def listen_until_it_fails(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)
        raise failure

    monkeypatch.setattr(RecordingAssistant, "run", listen_until_it_fails)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == exit_code

    [watcher] = rain_watchers
    assert watcher.listening == [False, True]


def test_chat_never_watches_for_rain(home, brains, rain_watchers, monkeypatch):
    write_out_example_briefing(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert rain_watchers == []


def test_rain_prints_the_next_rain_as_she_would_say_it_without_the_brain(
    home, brains, open_meteo, capsys
):
    write_out_example_briefing(home)
    # Open-Meteo's 16:00 and 17:00 entries hold the rain from 15:00 to 17:00.
    forecast_hour(open_meteo, 16, code=3, chance=80, mm=1.2)
    forecast_hour(open_meteo, 17, code=80, chance=90, mm=4.0)

    assert cli.main(["rain"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Prakiraan hujan di Bandung, Jawa Barat, per 07:15 (peluang 60% ke atas):",
        "Hujan berikutnya: sekitar jam tiga sore (15:00-17:00, peluang 90%).",
    ]
    assert brains == []


def test_rain_tomorrow_shows_its_day(home, open_meteo, capsys):
    write_out_example_briefing(home)  # the fake rains all day tomorrow
    forecast_hour(open_meteo, 24, code=2, chance=10)  # dry until tomorrow 00:00

    assert cli.main(["rain"]) == 0

    assert capsys.readouterr().out.splitlines()[1:] == [
        "Hujan berikutnya: sekitar jam dua belas malam (besok 00:00-23:00, peluang 70%)."
    ]


def test_rain_into_tomorrow_shows_the_day_it_ends(home, open_meteo, capsys):
    write_out_example_briefing(home)  # tomorrow's 00:00 entry holds the rain from 23:00 today

    assert cli.main(["rain"]) == 0

    assert capsys.readouterr().out.splitlines()[1:] == [
        "Hujan berikutnya: sekitar jam sebelas malam (23:00-besok 23:00, peluang 70%)."
    ]


def test_rain_with_a_dry_day_ahead_says_so(home, open_meteo, capsys):
    write_out_example_briefing(home)
    dry_tomorrow(open_meteo)

    assert cli.main(["rain"]) == 0

    assert capsys.readouterr().out.splitlines()[1:] == ["Tidak ada hujan dalam 24 jam ke depan."]


def test_rain_while_it_rains_says_so_and_looks_for_another_rain(home, open_meteo, capsys):
    write_out_example_briefing(home)
    dry_tomorrow(open_meteo)
    open_meteo.forecast["current"]["weather_code"] = 63

    assert cli.main(["rain"]) == 0

    assert capsys.readouterr().out.splitlines()[1:] == [
        "Sekarang sedang hujan.",
        "Tidak ada hujan lain dalam 24 jam ke depan.",
    ]


def test_rain_with_only_a_chance_of_rain_now_shows_the_rain_after_it(home, open_meteo, capsys):
    write_out_example_briefing(home)
    dry_tomorrow(open_meteo)
    forecast_hour(open_meteo, 8, code=3, chance=95)  # 07:00-08:00, the hour running at 07:15
    forecast_hour(open_meteo, 9, code=61, chance=90, mm=2.0)

    assert cli.main(["rain"]) == 0

    assert capsys.readouterr().out.splitlines()[1:] == [
        "Hujan berikutnya: sekitar jam delapan pagi (08:00-09:00, peluang 90%)."
    ]


def test_rain_counts_rain_by_the_configured_threshold_also_with_the_alerts_off(
    home, open_meteo, capsys
):
    append_config(
        home, "briefing:\n  city: Bandung\nrain_alert:\n  enabled: false\n  threshold: 90\n"
    )
    dry_tomorrow(open_meteo)
    forecast_hour(open_meteo, 15, code=3, chance=80)  # under the line
    forecast_hour(open_meteo, 19, code=95, chance=None, mm=None)  # a thunderstorm, chance unknown

    assert cli.main(["rain"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Prakiraan hujan di Bandung, Jawa Barat, per 07:15 (peluang 90% ke atas):",
        "Hujan berikutnya: sekitar jam enam sore (18:00-19:00).",
    ]


def test_rain_without_a_place_says_how_to_set_one_and_exits_1(home, capsys):
    assert cli.main(["rain"]) == 1

    assert capsys.readouterr().out.splitlines() == [
        "✗ Cuaca belum diatur (isi briefing.city di config.yaml)"
    ]


def test_rain_whose_forecast_fails_says_why_and_exits_1(home, open_meteo, capsys):
    write_out_example_briefing(home)
    open_meteo.failure = urllib.error.URLError("getaddrinfo failed")

    assert cli.main(["rain"]) == 1

    assert capsys.readouterr().out.splitlines() == [
        "✗ Server cuaca tidak bisa dihubungi. Cek koneksi internet, lalu coba lagi."
    ]


def rain_alert_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Peringatan hujan" in line]
    return line


def rain_settings_and_coordinates(home: Path) -> None:
    append_config(
        home,
        "briefing:\n  city: Bandung\n  latitude: -6.9175\n  longitude: 107.6191\n"
        "rain_alert:\n  lead_minutes: 90\n  threshold: 70\n  active_from: '07:00'\n"
        "  active_until: '20:00'\n",
    )


@pytest.mark.parametrize(
    ("configure", "expected"),
    [
        (lambda home: None, "! Peringatan hujan: cuaca belum diatur (isi briefing.city)"),
        (
            write_out_example_briefing,
            "✓ Peringatan hujan: 60 menit sebelumnya, peluang 60% ke atas, jam 06:00-21:00, "
            "untuk Bandung",
        ),
        (
            rain_settings_and_coordinates,
            "✓ Peringatan hujan: 90 menit sebelumnya, peluang 70% ke atas, jam 07:00-20:00, "
            "untuk Bandung (-6.9175, 107.6191)",
        ),
        (rain_alerts_off, "! Peringatan hujan mati"),
    ],
    ids=["example", "example_written_out", "settings_and_coordinates", "off"],
)
def test_check_reports_how_and_where_she_warns_of_rain_without_asking_anyone(
    configure, expected, home, nothing_installed, capsys
):
    configure(home)

    cli.main(["check"])

    assert rain_alert_line(capsys.readouterr().out) == expected


def test_check_reports_the_rain_alerts_right_after_the_morning_greeting(
    home, nothing_installed, capsys
):
    cli.main(["check"])

    lines = capsys.readouterr().out.splitlines()
    greeting = next(index for index, line in enumerate(lines) if "Sapaan pagi" in line)
    assert "Peringatan hujan" in lines[greeting + 1]


# --- versi baru --------------------------------------------------------------------------------

V111 = Release("v1.11", "memotong bicaranya")
V126 = Release("v1.26", "versi baru")
OLD_COMMIT = "1a2b3c4" + "5" * 33
OLD_HASH = "e" * 64
STARTED = datetime(2026, 10, 13, 8, 15, 4)
INSTALL = ".venv\\Scripts\\python -m pip install -e ."


def running_file(home: Path) -> Path:
    return home / "data" / "running.json"


def test_run_records_what_it_runs_before_its_startup_delay(run_fakes, code_on_disk, monkeypatch):
    path = running_file(run_fakes.lock.parent.parent)
    recorded_before_waiting: list[RunningVersion | None] = []
    monkeypatch.setattr(
        cli, "sleep", lambda seconds: recorded_before_waiting.append(read_running(path))
    )
    before = datetime.now().replace(microsecond=0)

    assert cli.main(["run", "--dry-run", "--no-speaker-check", "--startup-delay", "15"]) == 0

    [record] = recorded_before_waiting
    assert record == read_running(path)
    assert record.code == CodeVersion((V125,), DISK_HASH, DISK_COMMIT)  # the newest release
    assert record.pid == os.getpid()
    assert before <= record.started <= datetime.now()
    assert code_on_disk.runs == [subprocess.run]  # with git, once, before anything else


def test_a_refused_run_leaves_the_running_record_of_the_live_one_alone(
    home, run_fakes, code_on_disk
):
    # Autostart and a manual start, or a double click: the second must not record itself.
    record_a_run(home, DISK_CODE, pid=4321)

    with InstanceLock(run_fakes.lock):
        assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 2

    assert read_running(running_file(home)).pid == 4321
    assert code_on_disk.runs == []  # nor did it run git


def test_run_logs_what_it_runs_and_each_part_it_could_not_read(run_fakes, code_on_disk, caplog):
    code_on_disk.disk = CodeVersion(
        (V125,), DISK_HASH, None, datetime(2026, 10, 17, 9, 12), ("Could not read the git commit",)
    )
    caplog.set_level(logging.INFO, logger="waifu.cli")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    records = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "waifu.cli"]
    assert (logging.WARNING, "Could not read the git commit") in records
    assert (
        logging.INFO,
        "Running v1.25 (peringatan hujan), source files of 2026-10-17 09:12:00",
    ) in records


def test_run_that_cannot_record_its_version_still_runs(run_fakes, monkeypatch, caplog):
    def refuse(path, running) -> None:
        raise PermissionError("another process has it open")

    monkeypatch.setattr(cli, "write_running", refuse)

    with caplog.at_level(logging.WARNING, logger="waifu.cli"):
        assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert RecordingAssistant.instances[0].ran_with is not None
    assert "Could not record the running version" in caplog.text


def test_enroll_and_chat_never_record_a_running_version(home, recording_mics, brains, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["enroll", "--samples", "3", "--seconds", "0.5"]) == 0
    assert cli.main(["chat", "--dry-run"]) == 0

    assert not running_file(home).exists()


def test_run_watches_for_a_newer_version_while_she_listens_and_pushes_it(
    home, run_fakes, brains, version_watchers, code_on_disk, monkeypatch
):
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = version_watchers
    assert watcher.running == DISK_CODE  # what it read at the start, every release
    assert watcher.config == VersionNoticeConfig()  # the example config's settings
    assert (watcher.owner_call, watcher.name, watcher.notifier) == ("Master", "Cyrene", notifier)
    assert watcher.listening == [False, True]
    assert "mic opened" in watcher.events_when_started  # once she is ready to listen
    code_on_disk.disk = CodeVersion((V126, V125), DISK_HASH)
    assert watcher.read() == code_on_disk.disk
    assert code_on_disk.runs[-1] is None  # neither git nor the source files at every check
    text = "Master, ada versi baru Cyrene: v1.26, versi baru. Mulai ulang aku kalau sempat ya."
    watcher.announce(text, "happy")
    assistant = RecordingAssistant.instances[0]
    assert assistant.announced == [(text, "happy")]
    # Never inside a conversation: she says it once the follow-up window has closed.
    assert assistant.waiting_for_followup == [text]


def test_run_watches_for_a_newer_version_as_configured(home, run_fakes, brains, version_watchers):
    call_the_owner(home, "Sayang")
    append_config(home, "version_notice:\n  check_minutes: 60\n  active_from: '09:00'\n")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = version_watchers
    assert watcher.config == VersionNoticeConfig(check_minutes=60, active_from="09:00")
    assert (watcher.owner_call, watcher.notifier) == ("Sayang", None)  # notify is off


def test_run_tells_a_newer_version_without_a_push_when_push_is_off(
    home, run_fakes, brains, version_watchers, monkeypatch
):
    enable_notify(home)
    append_config(home, "version_notice:\n  push: false\n")
    use_notifier(monkeypatch, FakeNotifier())

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = version_watchers
    assert watcher.notifier is None


def test_run_with_the_notice_off_watches_nothing_but_still_records_its_version(
    home, run_fakes, brains, version_watchers
):
    append_config(home, "version_notice:\n  enabled: false\n")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert version_watchers == []
    assert read_running(running_file(home)).code.release == V125


@pytest.mark.parametrize(
    ("failure", "exit_code"),
    [(SttError("Whisper berhenti."), 1), (KeyboardInterrupt(), 130)],
    ids=["whisper-fails", "ctrl-c"],
)
def test_run_stops_watching_for_a_newer_version_however_listening_ends(
    failure, exit_code, home, run_fakes, brains, version_watchers, monkeypatch
):
    def listen_until_it_fails(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)
        raise failure

    monkeypatch.setattr(RecordingAssistant, "run", listen_until_it_fails)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == exit_code

    [watcher] = version_watchers
    assert watcher.listening == [False, True]


def test_chat_never_watches_for_a_newer_version(home, brains, version_watchers, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert version_watchers == []


def record_a_run(home: Path, code: CodeVersion, *, pid: int | None = None) -> None:
    """data/running.json as a `waifu run` started at STARTED with `code` would write it."""
    write_running(running_file(home), RunningVersion(code, STARTED, pid or os.getpid()))


def version_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if line[2:].startswith("waifu run")]
    return line


def check_while_running(home: Path, capsys) -> str:
    """The version line of `waifu check` while a `waifu run` holds the run lock."""
    with InstanceLock(home / "data" / "run.lock"):
        cli.main(["check"])
    return version_line(capsys.readouterr().out)


def test_check_without_a_running_cyrene_says_so_and_names_the_version_on_disk(
    home, nothing_installed, capsys
):
    record_a_run(home, CodeVersion((V111,), DISK_HASH))  # a run that has ended

    cli.main(["check"])

    assert version_line(capsys.readouterr().out) == (
        "✓ waifu run tidak berjalan; versi di disk v1.25, peringatan hujan"
    )


def test_check_with_a_free_run_lock_says_she_is_not_running(home, nothing_installed, capsys):
    with InstanceLock(home / "data" / "run.lock"):  # taken and dropped by a run that ended
        pass
    record_a_run(home, DISK_CODE)

    cli.main(["check"])

    assert version_line(capsys.readouterr().out).startswith("✓ waifu run tidak berjalan")


CLOSED_WITH_NEW_PACKAGES = (
    "! waifu run tidak berjalan; versi di disk v1.25, peringatan hujan; paket Python-nya berubah "
    f"sejak terakhir dijalankan: kalau belum, jalankan {INSTALL} di folder proyek sebelum "
    "waifu run"
)


def test_check_without_a_running_cyrene_still_gives_the_pip_command_her_last_run_needs(
    home, nothing_installed, capsys
):
    # She asked to be closed before pip install: the command must still be there afterwards.
    record_a_run(home, CodeVersion((V125,), OLD_HASH, OLD_COMMIT))

    cli.main(["check"])

    assert version_line(capsys.readouterr().out) == CLOSED_WITH_NEW_PACKAGES


def test_check_says_a_running_cyrene_runs_the_newest_version(home, nothing_installed, capsys):
    record_a_run(home, CodeVersion((V125,), DISK_HASH, DISK_COMMIT))

    assert check_while_running(home, capsys) == "✓ waifu run: v1.25, terbaru"


@pytest.mark.parametrize(
    ("running", "disk"),
    [
        (CodeVersion((V125,), DISK_HASH, OLD_COMMIT), DISK_CODE),
        (
            CodeVersion((V125,), DISK_HASH, None, datetime(2026, 10, 17, 9, 0)),
            CodeVersion((V125,), DISK_HASH, None, datetime(2026, 10, 17, 9, 30)),
        ),
    ],
    ids=["another_commit", "a_newer_source_file"],
)
def test_check_mentions_a_small_change_since_the_running_cyrene_started(
    running, disk, home, nothing_installed, code_on_disk, capsys
):
    record_a_run(home, running)
    code_on_disk.disk = disk

    assert check_while_running(home, capsys) == (
        "✓ waifu run: v1.25, terbaru; ada perubahan kecil sejak dimulai, mulai ulang kalau sempat"
    )


def test_check_asks_for_a_restart_while_an_older_version_runs(home, nothing_installed, capsys):
    record_a_run(home, CodeVersion((V111,), DISK_HASH, OLD_COMMIT))

    assert check_while_running(home, capsys) == (
        "! waifu run yang sedang jalan masih v1.11 (dimulai 2026-10-13); "
        "mulai ulang supaya v1.25 aktif"
    )


def test_check_gives_the_pip_command_when_the_packages_changed(
    home, nothing_installed, code_on_disk, capsys
):
    record_a_run(home, CodeVersion((V125,), OLD_HASH, OLD_COMMIT))
    code_on_disk.disk = CodeVersion((V126, V125), DISK_HASH, DISK_COMMIT)

    assert check_while_running(home, capsys) == (
        "! waifu run yang sedang jalan masih v1.25 (dimulai 2026-10-13) dan paket Python-nya "
        f"berubah: tutup Cyrene, jalankan {INSTALL} di folder proyek, lalu mulai ulang supaya "
        "v1.26 aktif"
    )


def test_check_gives_the_pip_command_when_only_the_packages_changed(
    home, nothing_installed, capsys
):
    record_a_run(home, CodeVersion((V125,), OLD_HASH, DISK_COMMIT))

    assert check_while_running(home, capsys).endswith(
        f"tutup Cyrene, jalankan {INSTALL} di folder proyek, lalu mulai ulang"
    )


NOT_RECORDED = (
    "! waifu run yang sedang jalan versinya tidak tercatat (lebih lama dari v1.26); "
    "mulai ulang supaya v1.25 aktif"
)


def test_check_says_a_run_from_before_v126_has_no_recorded_version(home, nothing_installed, capsys):
    assert check_while_running(home, capsys) == NOT_RECORDED


def test_check_never_trusts_a_record_of_a_process_that_ended(
    home, nothing_installed, alive_processes, capsys
):
    # The lock is held by a run that recorded nothing; the record is an earlier run's.
    record_a_run(home, DISK_CODE, pid=4321)

    assert check_while_running(home, capsys) == NOT_RECORDED


def test_check_ignores_a_record_it_cannot_read(home, nothing_installed, capsys):
    running_file(home).parent.mkdir(exist_ok=True)
    running_file(home).write_text("{rusak", encoding="utf-8")

    assert check_while_running(home, capsys) == NOT_RECORDED
    assert running_file(home).read_text(encoding="utf-8") == "{rusak"


def test_check_says_when_the_running_cyrene_could_not_read_its_version(
    home, nothing_installed, capsys
):
    record_a_run(home, CodeVersion((), DISK_HASH, DISK_COMMIT))

    assert check_while_running(home, capsys) == (
        "! waifu run yang sedang jalan versinya tidak terbaca (dimulai 2026-10-13); "
        "mulai ulang supaya v1.25 aktif"
    )


def test_check_says_when_the_version_on_disk_cannot_be_read(
    home, nothing_installed, code_on_disk, capsys
):
    code_on_disk.disk = CodeVersion((), DISK_HASH, DISK_COMMIT, None, ("no heading",))

    cli.main(["check"])

    assert version_line(capsys.readouterr().out) == (
        "! waifu run: versi kode di disk tidak terbaca (lihat CHANGELOG.md)"
    )


def notice_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Kabar versi baru" in line]
    return line


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ("", "✓ Kabar versi baru: dicek tiap 30 menit, jam 08:00-22:00"),
        (
            "version_notice:\n  check_minutes: 60\n  active_from: '09:00'\n"
            "  active_until: '21:00'\n",
            "✓ Kabar versi baru: dicek tiap 60 menit, jam 09:00-21:00",
        ),
        ("version_notice:\n  enabled: false\n", "! Kabar versi baru mati"),
    ],
    ids=["example", "settings", "off"],
)
def test_check_reports_how_she_watches_for_a_newer_version(
    settings, expected, home, nothing_installed, capsys
):
    append_config(home, settings)

    cli.main(["check"])

    assert notice_line(capsys.readouterr().out) == expected


def test_check_reports_the_version_right_after_the_config(
    home, nothing_installed, code_on_disk, capsys
):
    cli.main(["check"])

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("✓ Konfigurasi valid")
    assert lines[1] == version_line("\n".join(lines))
    assert lines[2] == notice_line("\n".join(lines))
    assert code_on_disk.runs == [subprocess.run]  # with the commit, for a small change


def test_version_prints_the_version_on_disk_and_that_nothing_runs(home, code_on_disk, capsys):
    assert cli.main(["version"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Kode di disk: v1.25, peringatan hujan (commit ec4aadf)",
        "✓ waifu run tidak berjalan; versi di disk v1.25, peringatan hujan",
    ]
    assert code_on_disk.runs == [subprocess.run]  # with git, for the commit it prints


def test_version_after_she_was_closed_still_gives_the_pip_command(home, capsys):
    record_a_run(home, CodeVersion((V111,), OLD_HASH, OLD_COMMIT))

    assert cli.main(["version"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Kode di disk: v1.25, peringatan hujan (commit ec4aadf)",
        CLOSED_WITH_NEW_PACKAGES,
    ]


def test_version_prints_the_running_version_beside_the_one_on_disk(home, capsys):
    record_a_run(home, CodeVersion((V111,), OLD_HASH, OLD_COMMIT))

    with InstanceLock(home / "data" / "run.lock"):
        assert cli.main(["version"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Kode di disk: v1.25, peringatan hujan (commit ec4aadf)",
        "Yang berjalan: v1.11, memotong bicaranya (commit 1a2b3c4), dimulai 2026-10-13 08:15",
        "! waifu run yang sedang jalan masih v1.11 (dimulai 2026-10-13) dan paket Python-nya "
        f"berubah: tutup Cyrene, jalankan {INSTALL} di folder proyek, lalu mulai ulang supaya "
        "v1.25 aktif",
    ]


@pytest.mark.parametrize(
    ("disk", "expected"),
    [
        (
            CodeVersion((V125,), DISK_HASH, None, datetime(2026, 10, 17, 9, 12, 30)),
            "Kode di disk: v1.25, peringatan hujan (file terbaru 2026-10-17 09:12)",
        ),
        (CodeVersion((), None, DISK_COMMIT), "Kode di disk: versi tidak terbaca (commit ec4aadf)"),
        (CodeVersion((V125,), None), "Kode di disk: v1.25, peringatan hujan"),
    ],
    ids=["without_git", "without_a_release", "neither"],
)
def test_version_shows_what_it_could_read_of_the_code_on_disk(
    disk, expected, home, code_on_disk, capsys
):
    code_on_disk.disk = disk

    assert cli.main(["version"]) == 0

    assert capsys.readouterr().out.splitlines()[0] == expected


def test_version_from_another_folder_reads_the_run_in_the_project_folder(home, monkeypatch, capsys):
    record_a_run(home, CodeVersion((V111,), DISK_HASH, OLD_COMMIT))
    elsewhere = home / "elsewhere"  # the venv's waifu works from any folder
    elsewhere.mkdir()

    with InstanceLock(home / "data" / "run.lock"):
        monkeypatch.chdir(elsewhere)
        assert cli.main(["version"]) == 0

    assert capsys.readouterr().out.splitlines()[1:] == [
        "Yang berjalan: v1.11, memotong bicaranya (commit 1a2b3c4), dimulai 2026-10-13 08:15",
        "! waifu run yang sedang jalan masih v1.11 (dimulai 2026-10-13); "
        "mulai ulang supaya v1.25 aktif",
    ]


def test_version_needs_no_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # no config.yaml here

    assert cli.main(["version"]) == 0

    assert capsys.readouterr().out.startswith("Kode di disk: v1.25")


# --- API cost monitor --------------------------------------------------------------------------

OPUS = "claude-opus-5-5"
BUDGET_PUSH = "budget-push"
# The notices of a 1-dollar budget; claude-opus-5-5 costs 20 dollars per million output tokens.
EIGHTY_PERCENT = (
    "{owner}, biaya API bulan ini sekitar 0,80 dolar, "
    "sudah mencapai 80 persen dari anggaran 1 dolar."
)
FULL_BUDGET = (
    "{owner}, biaya API bulan ini sekitar {spent} dolar, sudah mencapai batas anggaran 1 dolar."
)


def usage_file(home: Path) -> Path:
    """Where the example config keeps her API cost records, inside the test's working directory."""
    return home / "data" / "usage.json"


def record_usage(path: Path, at: datetime, model: str, tokens: TokenUsage) -> None:
    UsageLedger(path, prices={}, default_model=OPUS, now=lambda: at).record(model, tokens)


def api_usage(brain: HouseBrain, period: str) -> dict:
    [tools] = [provider for provider in brain.providers if isinstance(provider, UsageTools)]
    result, is_error = tools.run("api_usage", {"period": period})
    assert is_error is False
    return json.loads(result)


def spend_while_listening(brains: list[HouseBrain], monkeypatch, *responses: TokenUsage) -> None:
    """While `waifu run` listens, her brain gets one Claude response of each of `responses`."""

    def run(self: RecordingAssistant, mic, segmenter) -> None:
        for tokens in responses:
            brains[0].on_usage(OPUS, tokens)

    monkeypatch.setattr(RecordingAssistant, "run", run)


def await_budget_pushes() -> None:
    for thread in threading.enumerate():
        if thread.name == BUDGET_PUSH:
            thread.join(WAIT_S)


def test_usage_ledger_outside_tmp_path_is_refused_so_no_test_touches_the_owner_s_records():
    with pytest.raises(AssertionError, match="UsageLedger outside tmp_path"):
        cli.UsageLedger(ROOT / "data" / "usage.json", prices={}, default_model=OPUS)


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_claude_she_records_each_response_where_the_config_says_and_can_tell_the_cost(
    argv, home, run_fakes, brains, monkeypatch
):
    use_claude(home)
    append_config(
        home,
        "usage:\n  path: biaya/api.json\n  idr_per_usd: 16000\n  prices:\n"
        "    claude-sonnet-5-5: {input: 2, output: 10, cache_write: 2.5, cache_read: 0.2}\n",
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    brain = brains[0]
    assert type(brain.providers[-1]) is UsageTools
    # The built-in prices for Opus 5.5 ($0.026 here), the configured ones for Sonnet ($0.01).
    brain.on_usage(OPUS, TokenUsage(input=1000, output=500, cache_write=2000, cache_read=10_000))
    brain.on_usage("claude-sonnet-5-5", TokenUsage(output=1000))
    assert api_usage(brain, "month") == {
        "period": "month",
        "requests": 2,
        "input_tokens": 1000,
        "output_tokens": 1500,
        "cache_read_tokens": 10_000,
        "cache_write_tokens": 2000,
        "cost_usd": 0.036,
        "cost_idr": 576,
        "estimated": False,
    }
    assert (home / "biaya" / "api.json").exists()
    assert not usage_file(home).exists()


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_a_model_missing_from_the_prices_is_priced_like_the_configured_default_model(
    argv, home, run_fakes, brains, monkeypatch
):
    use_claude(home)
    append_config(
        home,
        "usage:\n  default_model: claude-sonnet-5-5\n  prices:\n"
        "    claude-sonnet-5-5: {input: 2, output: 10, cache_write: 2.5, cache_read: 0.2}\n",
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    brains[0].on_usage("claude-opus-5", TokenUsage(output=1000))  # a server-side fallback
    month = api_usage(brains[0], "month")
    # Sonnet's 10 dollars per million output tokens; Opus 5.5's 20 would make it 0.02.
    assert (month["cost_usd"], month["estimated"]) == (0.01, True)


def test_with_claude_each_web_search_is_priced_as_the_config_says(home, brains, monkeypatch):
    use_claude(home)
    append_config(home, "usage:\n  web_search_per_1000_usd: 20\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    brains[0].on_usage(OPUS, TokenUsage(web_searches=50))
    assert api_usage(brains[0], "month")["cost_usd"] == 1.0  # the default 10 would make it 0.5


def write_out_example_usage(home: Path) -> None:
    """The example config with its commented `usage` block written out."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    commented = text[text.index("# usage:\n") :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())
    config.write_text(text.replace(commented, written_out), encoding="utf-8")


def test_the_example_usage_block_is_valid_once_written_out(home, brains, monkeypatch):
    use_claude(home)
    write_out_example_usage(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    brains[0].on_usage(OPUS, TokenUsage(output=50_000))  # 1 dollar
    month = api_usage(brains[0], "month")
    assert (month["cost_usd"], month["cost_idr"]) == (1.0, 16_300)
    assert usage_file(home).exists()


@pytest.mark.parametrize("claude", [False, True], ids=["ollama", "claude_with_usage_off"])
@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_ollama_or_the_monitor_off_nothing_is_recorded_or_offered(
    claude, argv, home, run_fakes, brains, monkeypatch
):
    if claude:
        use_claude(home)
        append_config(home, "usage:\n  enabled: false\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert brains[0].on_usage is None
    assert not any(isinstance(tools, UsageTools) for tools in brains[0].providers)
    assert not usage_file(home).exists()


def test_run_says_and_pushes_each_budget_notice_once_while_she_listens(
    home, run_fakes, brains, monkeypatch
):
    use_claude(home)
    enable_notify(home)
    append_config(home, "usage:\n  monthly_budget_usd: 1\n")
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)
    # 80 cents, 2 more, then 20 more: past 80 % of the budget, still under it, then past it.
    spend_while_listening(
        brains,
        monkeypatch,
        TokenUsage(output=40_000),
        TokenUsage(output=1_000),
        TokenUsage(output=10_000),
    )

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    await_budget_pushes()
    eighty = EIGHTY_PERCENT.format(owner="Master")
    full = FULL_BUDGET.format(owner="Master", spent="1,02")
    assistant = RecordingAssistant.instances[0]
    assert assistant.announced == [(eighty, "surprised"), (full, "surprised")]
    # Each is queued during the reply that crossed the line: it must leave Master the window
    # in which he answers that reply.
    assert assistant.waiting_for_followup == [eighty, full]
    assert notifier.sent == [
        ("Cyrene", eighty, ("money_with_wings",)),
        ("Cyrene", full, ("money_with_wings",)),
    ]


def test_run_pushes_a_budget_notice_without_holding_up_her_reply(
    home, run_fakes, brains, monkeypatch
):
    use_claude(home)
    enable_notify(home)
    append_config(home, "usage:\n  monthly_budget_usd: 1\n")
    released = threading.Event()

    class SlowNotifier(FakeNotifier):
        def send(self, title: str, message: str, *, tags=()) -> bool:
            assert released.wait(WAIT_S)
            return super().send(title, message, tags=tags)

    notifier = SlowNotifier()
    use_notifier(monkeypatch, notifier)
    pushed_while_she_answered: list[list] = []

    def run(self: RecordingAssistant, mic, segmenter) -> None:
        brains[0].on_usage(OPUS, TokenUsage(output=40_000))  # back while the push still waits
        pushed_while_she_answered.append(list(notifier.sent))
        released.set()

    monkeypatch.setattr(RecordingAssistant, "run", run)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    await_budget_pushes()
    assert pushed_while_she_answered == [[]]
    assert [message for _, message, _ in notifier.sent] == [EIGHTY_PERCENT.format(owner="Master")]


def test_run_with_notifications_off_only_says_the_budget_notice(
    home, run_fakes, brains, monkeypatch
):
    use_claude(home)
    append_config(home, "usage:\n  monthly_budget_usd: 1\n")
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)
    spend_while_listening(brains, monkeypatch, TokenUsage(output=40_000))

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    await_budget_pushes()
    assert RecordingAssistant.instances[0].announced == [
        (EIGHTY_PERCENT.format(owner="Master"), "surprised")
    ]
    assert notifier.sent == []


def test_run_without_a_budget_never_tells_the_cost_by_herself(home, run_fakes, brains, monkeypatch):
    use_claude(home)
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)
    spend_while_listening(brains, monkeypatch, TokenUsage(output=500_000))  # 10 dollars

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    await_budget_pushes()
    assert RecordingAssistant.instances[0].announced == []
    assert notifier.sent == []
    assert (
        UsageLedger(usage_file(home), prices={}, default_model=OPUS)
        .month(date.today().year, date.today().month)
        .requests
        == 1
    )


def test_a_budget_notice_is_never_used_up_while_no_one_can_say_it(home, run_fakes, brains):
    use_claude(home)
    append_config(home, "usage:\n  monthly_budget_usd: 1\n")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0
    brains[0].on_usage(OPUS, TokenUsage(output=40_000))  # after she stopped listening

    assert RecordingAssistant.instances[0].announced == []
    ledger = UsageLedger(usage_file(home), prices={}, default_model=OPUS)
    assert ledger.budget_notices(1) == [EIGHTY_PERCENT.format(owner="Master")]  # still due


def test_chat_prints_each_budget_notice_once_with_what_she_calls_the_owner(
    home, brains, monkeypatch, capsys
):
    use_claude(home)
    call_the_owner(home, "sayang")
    append_config(home, "usage:\n  monthly_budget_usd: 1\n")
    respond = HouseBrain.respond

    def respond_for_80_cents(self: HouseBrain, user_text: str, *, now=None) -> BrainReply:
        self.on_usage(OPUS, TokenUsage(output=40_000))
        return respond(self, user_text, now=now)

    monkeypatch.setattr(HouseBrain, "respond", respond_for_80_cents)
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\nhalo lagi\nsekali lagi\nkeluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    lines = capsys.readouterr().out.splitlines()
    said = [line.split("Cyrene: ", 1)[1] for line in lines if "Cyrene: " in line]
    assert said == [
        EIGHTY_PERCENT.format(owner="Sayang"),
        REPLY,
        FULL_BUDGET.format(owner="Sayang", spent="1,60"),
        REPLY,
        REPLY,
    ]


def test_chat_leaves_budget_notices_to_a_running_waifu_run(home, brains, monkeypatch, capsys):
    # Chat would mark the notice told and only print it: the running Cyrene would then never
    # say it or push it to the phone that month. Dry runs use the real API and records too.
    use_claude(home)
    append_config(home, "usage:\n  monthly_budget_usd: 1\n")
    respond = HouseBrain.respond

    def respond_for_80_cents(self: HouseBrain, user_text: str, *, now=None) -> BrainReply:
        self.on_usage(OPUS, TokenUsage(output=40_000))
        return respond(self, user_text, now=now)

    monkeypatch.setattr(HouseBrain, "respond", respond_for_80_cents)
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\nkeluar\n"))

    with InstanceLock(home / "data" / "run.lock"):
        assert cli.main(["chat", "--dry-run"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert [line.split("Cyrene: ", 1)[1] for line in lines if "Cyrene: " in line] == [REPLY]
    ledger = UsageLedger(usage_file(home), prices={}, default_model=OPUS)
    assert ledger.budget_notices(1) == [EIGHTY_PERCENT.format(owner="Master")]  # still due


def test_usage_without_records_says_so_and_creates_no_file(home, capsys):
    assert cli.main(["usage"]) == 0

    assert capsys.readouterr().out == "Belum ada catatan biaya.\n"
    assert not usage_file(home).exists()


def test_usage_prints_this_month_day_by_day_and_the_total_with_ollama_too(home, capsys):
    now = datetime.now()
    tokens = TokenUsage(input=1000, output=500, cache_write=2000, cache_read=10_000)
    record_usage(usage_file(home), now, OPUS, tokens)  # $0.026
    record_usage(usage_file(home), now, OPUS, TokenUsage(output=1000))  # $0.02

    assert cli.main(["usage"]) == 0  # the example config uses Ollama

    assert capsys.readouterr().out.splitlines() == [
        f"Biaya API Claude {MONTHS_ID[now.month - 1]} {now.year}, dari catatan Cyrene sendiri:",
        "TANGGAL     PERMINTAAN  INPUT  OUTPUT  CACHE TULIS  CACHE BACA  USD",
        f"{now:%d-%m-%Y}  2           1000   1500    2000         10000       $0.05",
        "Total       2           1000   1500    2000         10000       $0.05",
    ]


def test_usage_month_shows_that_month_in_rupiah_too_and_marks_estimates_with_the_monitor_off(
    home, capsys
):
    append_config(home, "usage:\n  enabled: false\n  path: biaya/api.json\n  idr_per_usd: 16000\n")
    path = home / "biaya" / "api.json"
    # A month that is never the current one, between records that must not show.
    record_usage(path, datetime(2025, 2, 28, 23, 59), OPUS, TokenUsage(output=50_000))
    record_usage(path, datetime.now(), OPUS, TokenUsage(output=70_000))
    tokens = TokenUsage(input=2000, output=1000, cache_read=50_000)
    record_usage(path, datetime(2025, 3, 1, 8, 0), OPUS, tokens)  # $0.038
    # A server-side fallback, missing from the price table: estimated with Opus 5.5's prices.
    record_usage(path, datetime(2025, 3, 13, 21, 0), "claude-opus-5", TokenUsage(output=10_000))

    assert cli.main(["usage", "--month", "2025-03"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Biaya API Claude Maret 2025, dari catatan Cyrene sendiri:",
        "TANGGAL     PERMINTAAN  INPUT  OUTPUT  CACHE TULIS  CACHE BACA  USD    IDR",
        "01-03-2025  1           2000   1000    0            50000       $0.04  Rp608",
        "13-03-2025  1           0      10000   0            0           $0.20  Rp3.200"
        "  (perkiraan)",
        "Total       2           2000   11000   0            50000       $0.24  Rp3.808"
        "  (perkiraan)",
        "(perkiraan): sebagian respons datang dari model yang tidak ada di tabel harga, jadi "
        "dihitung dengan harga claude-opus-5-5.",
    ]


def test_usage_shows_the_web_searches_when_the_month_has_any(home, capsys):
    now = datetime.now()
    record_usage(usage_file(home), now, OPUS, TokenUsage(output=1000, web_searches=3))  # $0.05
    record_usage(usage_file(home), now, OPUS, TokenUsage(output=1000))  # $0.02

    assert cli.main(["usage"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"Biaya API Claude {MONTHS_ID[now.month - 1]} {now.year}, dari catatan Cyrene sendiri:",
        "TANGGAL     PERMINTAAN  INPUT  OUTPUT  CACHE TULIS  CACHE BACA  PENCARIAN WEB  USD",
        f"{now:%d-%m-%Y}  2           0      2000    0            0           3              $0.07",
        "Total       2           0      2000    0            0           3              $0.07",
    ]


@pytest.mark.parametrize("month", ["2026-13", "2026-00", "10-2026", "2026-1", "Oktober"])
def test_usage_month_must_be_written_year_dash_month(month, capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["usage", "--month", month])

    assert "tulis bulannya YYYY-MM, misalnya 2026-10" in capsys.readouterr().err


def usage_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "biaya" in line.lower()]
    return line


def test_check_shows_this_month_s_cost_and_requests_with_claude(home, nothing_installed, capsys):
    use_claude(home)
    now = datetime.now()
    record_usage(usage_file(home), now, OPUS, TokenUsage(output=40_000))
    record_usage(usage_file(home), now, OPUS, TokenUsage(output=1000))
    last_month = now.replace(day=1) - timedelta(days=1)
    record_usage(usage_file(home), last_month, OPUS, TokenUsage(output=90_000))

    cli.main(["check"])

    assert usage_line(capsys.readouterr().out) == "✓ Biaya API: $0.82 bulan ini (2 permintaan)"


@pytest.mark.parametrize(
    ("claude", "enabled", "expected"),
    [
        (False, True, "! Biaya API dicatat hanya dengan backend Claude"),
        (True, False, "! Pencatatan biaya mati"),
        (False, False, "! Pencatatan biaya mati"),
    ],
    ids=["ollama", "claude_with_usage_off", "ollama_with_usage_off"],
)
def test_check_says_when_her_api_cost_is_not_recorded(
    claude, enabled, expected, home, nothing_installed, capsys
):
    if claude:
        use_claude(home)
    if not enabled:
        append_config(home, "usage:\n  enabled: false\n")

    cli.main(["check"])

    assert usage_line(capsys.readouterr().out) == expected
    assert not usage_file(home).exists()


def test_check_shows_where_a_broken_usage_file_was_moved(home, nothing_installed, capsys):
    use_claude(home)
    usage_file(home).parent.mkdir()
    usage_file(home).write_text("{catatan yang rusak", encoding="utf-8")

    cli.main(["check"])

    [moved] = usage_file(home).parent.glob("usage.json.corrupt-*")
    line = usage_line(capsys.readouterr().out)
    assert line.startswith("! Biaya API: $0.00 bulan ini (0 permintaan)")
    assert str(Path("data") / moved.name) in line
    assert moved.read_text(encoding="utf-8") == "{catatan yang rusak"


UNPRICED_DEFAULT = "usage:\n  default_model: claude-sonnet-5-5\n"


def test_check_fails_a_default_model_without_prices(home, nothing_installed, capsys):
    # Every response of a model missing from the table would cost 0 dollars under a green ✓.
    use_claude(home)
    append_config(home, UNPRICED_DEFAULT)

    cli.main(["check"])

    line = usage_line(capsys.readouterr().out)
    assert line.startswith("✗ Biaya API: usage.default_model claude-sonnet-5-5 has no prices")
    assert "usage.prices" in line


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check", "--dry-run"], ["chat"], ["usage"]])
def test_a_default_model_without_prices_is_a_configuration_problem(
    argv, home, run_fakes, brains, monkeypatch, capsys
):
    use_claude(home)
    append_config(home, UNPRICED_DEFAULT)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(argv) == 2

    err = capsys.readouterr().err
    assert "Konfigurasi bermasalah: usage.default_model claude-sonnet-5-5 has no prices" in err
    assert brains == []


def test_check_fails_a_usage_file_it_cannot_read(home, nothing_installed, capsys):
    use_claude(home)
    usage_file(home).mkdir(parents=True)  # a folder where the file should be

    cli.main(["check"])

    line = usage_line(capsys.readouterr().out)
    assert line.startswith(f"✗ File biaya API {Path('data/usage.json')} tidak bisa dibaca")


# --- Cyrene on the phone -----------------------------------------------------------------------

TAILNET_NAME = "cyrene-pc.tail1234.ts.net"
PHONE_ON = f"phone:\n  enabled: true\n  host: {TAILNET_NAME}\n"
# The phone shows the avatar's model, also while the mascot on the PC is off.
PHONE_READY = PHONE_ON + f"avatar:\n  model: {VRM.as_posix()}\n"


def phone_file(home: Path) -> Path:
    """Where the example config keeps the paired phones, inside the test's working directory."""
    return home / "data" / "phone.json"


def pair_phone(home: Path, at: datetime) -> str:
    """Pairs one phone at `at` through the pairing file, as the phone server would; its token."""
    auth = PhoneAuth(phone_file(home), now=lambda: at)
    token = auth.redeem(auth.new_code(), "Android")
    assert isinstance(token, str)
    return token


def tailscale_status(name: str = TAILNET_NAME, state: str = "Running") -> dict:
    """What `tailscale status --json` says about this PC (the parts Cyrene reads)."""
    return {"BackendState": state, "Self": {"DNSName": f"{name}.", "OS": "windows"}}


def serving(
    port: int = 9883, *, https_port: int = 443, target: str = "127.0.0.1", funnel: bool = False
) -> dict[str, object]:
    """What `tailscale serve status --json` says after
    `tailscale serve --bg --https=<https_port> http://<target>:<port>`; with `funnel`, after
    `tailscale funnel` turned Funnel on for that host and port as well."""
    status: dict[str, object] = {
        "TCP": {str(https_port): {"HTTPS": True}},
        "Web": {
            f"{TAILNET_NAME}:{https_port}": {
                "Handlers": {"/": {"Proxy": f"http://{target}:{port}"}}
            }
        },
    }
    if funnel:
        status["AllowFunnel"] = {f"{TAILNET_NAME}:{https_port}": True}
    return status


class FakeTailscale:
    """Stands in for the Tailscale CLI: answers the two commands that only read its state with
    `status` and `serve`, or fails as `failure` says (an exception it raises, or the finished
    process it returns), and records every command. Any other command, one that could change
    Tailscale, fails the test."""

    def __init__(self) -> None:
        self.status: object = tailscale_status()
        self.serve: object = serving()
        self.failure: Exception | subprocess.CompletedProcess[str] | None = None
        self.commands: list[list[str]] = []

    def __call__(self, command) -> subprocess.CompletedProcess[str]:
        command = [str(word) for word in command]
        self.commands.append(command)
        answers = {("status", "--json"): self.status, ("serve", "status", "--json"): self.serve}
        arguments = tuple(command[1:])
        assert arguments in answers, f"a test ran a Tailscale command that changes it: {command}"
        if isinstance(self.failure, Exception):
            raise self.failure
        if self.failure is not None:
            return self.failure
        return subprocess.CompletedProcess(command, 0, json.dumps(answers[arguments]), "")


@pytest.fixture
def tailscale(monkeypatch) -> FakeTailscale:
    """Tailscale installed where its Windows installer puts it (here under tmp_path, in a folder
    named Program Files), its CLI answering from a FakeTailscale."""
    cli.TAILSCALE_INSTALLED.parent.mkdir(parents=True)
    cli.TAILSCALE_INSTALLED.write_bytes(b"MZ")
    fake = FakeTailscale()
    monkeypatch.setattr(cli, "TAILSCALE_RUNNER", fake)
    return fake


def installed_serve_command(port: int = 9883) -> str:
    return f'"{cli.TAILSCALE_INSTALLED}" serve --bg --https=443 http://127.0.0.1:{port}'


def test_phone_auth_outside_tmp_path_is_refused_so_no_test_touches_the_owner_s_phones():
    with pytest.raises(AssertionError, match="PhoneAuth outside tmp_path"):
        cli.PhoneAuth(ROOT / "data" / "phone.json")


def test_phone_setup_prints_the_serve_command_the_url_and_the_phone_host_line(
    home, tailscale, capsys
):
    assert cli.main(["phone", "setup"]) == 0

    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
    assert f"✓ Tailscale: {cli.TAILSCALE_INSTALLED}" in lines
    assert installed_serve_command() in lines
    assert f"host: {TAILNET_NAME}" in lines
    assert f"https://{TAILNET_NAME}/" in lines
    # Only read: running `tailscale serve` is left to the owner.
    assert tailscale.commands == [[str(cli.TAILSCALE_INSTALLED), "status", "--json"]]


def test_phone_setup_finds_tailscale_on_the_path_and_shares_the_configured_port(
    home, tailscale, tmp_path, capsys
):
    on_path = tmp_path / "PATH" / "tailscale.exe"
    on_path.parent.mkdir()
    on_path.write_bytes(b"MZ")
    append_config(home, "phone:\n  port: 9990\n")

    assert cli.main(["phone", "setup"]) == 0

    lines = [line.strip() for line in capsys.readouterr().out.splitlines()]
    assert "tailscale serve --bg --https=443 http://127.0.0.1:9990" in lines
    assert os.path.samefile(tailscale.commands[0][0], on_path)


def test_phone_setup_without_tailscale_says_where_to_get_it(home, capsys):
    assert cli.main(["phone", "setup"]) == 1

    out = capsys.readouterr().out
    assert out.startswith("✗ Tailscale belum terpasang")
    assert "https://tailscale.com/download" in out


@pytest.mark.parametrize(
    ("status", "explained"),
    [
        (tailscale_status(state="NeedsLogin"), "login"),
        (tailscale_status(state="Stopped"), "login"),
        (tailscale_status(name=""), "MagicDNS"),
        ({"BackendState": "Running"}, "MagicDNS"),
        ([], "login"),
    ],
)
def test_phone_setup_explains_a_tailscale_that_cannot_share_her_yet(
    status, explained, home, tailscale, capsys
):
    tailscale.status = status

    assert cli.main(["phone", "setup"]) == 1

    out = capsys.readouterr().out
    assert any(line.startswith("✗") and explained in line for line in out.splitlines())
    assert "serve --bg" not in out


@pytest.mark.parametrize(
    ("failure", "shown"),
    [
        (OSError("Access is denied"), "Access is denied"),
        (subprocess.TimeoutExpired(["tailscale"], 10), "10 detik"),
        (
            subprocess.CompletedProcess(
                [], 1, "", "failed to connect to local Tailscale service; is Tailscale running?\n"
            ),
            "is Tailscale running?",
        ),
        (subprocess.CompletedProcess([], 0, "not json", ""), "JSON"),
    ],
)
def test_phone_setup_reports_a_tailscale_cli_that_fails(failure, shown, home, tailscale, capsys):
    tailscale.failure = failure

    assert cli.main(["phone", "setup"]) == 1

    failed = [line for line in capsys.readouterr().out.splitlines() if line.startswith("✗")]
    assert len(failed) == 1 and shown in failed[0]


def test_phone_pair_prints_a_code_that_pairs_one_phone_and_is_never_logged_or_saved(
    home, caplog, capsys
):
    append_config(home, PHONE_ON)
    before = datetime.now()

    with caplog.at_level(logging.DEBUG):
        assert cli.main(["phone", "pair"]) == 0
    after = datetime.now()

    out = capsys.readouterr().out
    [code] = re.findall(r"\b[0-9]{6}\b", out)
    assert code not in caplog.text
    assert code not in phone_file(home).read_text(encoding="utf-8")
    auth = PhoneAuth(phone_file(home))
    assert isinstance(auth.redeem(code, "Android"), str)
    assert auth.redeem(code, "Android") is Refusal.NO_CODE  # one phone per code
    expiries = {f"jam {moment + CODE_LIFETIME:%H.%M}" for moment in (before, after)}
    assert any(expiry in out for expiry in expiries)
    assert f"https://{TAILNET_NAME}/" in out


def test_phone_pair_says_what_is_missing_before_the_phone_can_open_her(home, capsys):
    assert cli.main(["phone", "pair"]) == 0

    out = capsys.readouterr().out
    assert re.search(r"\b[0-9]{6}\b", out)
    assert "phone.enabled" in out
    assert "waifu phone setup" in out


def test_phone_devices_lists_each_paired_phone_with_when_it_was_paired_and_last_used(home, capsys):
    pair_phone(home, datetime(2026, 10, 13, 20, 5))
    pair_phone(home, datetime(2026, 10, 14, 7, 30))

    assert cli.main(["phone", "devices"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["ID", "NAMA", "DIPASANGKAN", "TERAKHIR", "DIPAKAI"]
    assert lines[1].split() == ["d1", "Android", "13-10-2026", "20:05", "13-10-2026", "20:05"]
    assert lines[2].split() == ["d2", "Android", "14-10-2026", "07:30", "14-10-2026", "07:30"]


def test_phone_devices_without_a_paired_phone_says_how_to_pair_one(home, capsys):
    assert cli.main(["phone", "devices"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("Belum ada HP yang dipasangkan.") and "waifu phone pair" in out


def test_phone_forget_unpairs_the_phone_so_its_cookie_no_longer_opens_her(home, capsys):
    token = pair_phone(home, datetime(2026, 10, 14, 7, 30))

    assert cli.main(["phone", "forget", "d1"]) == 0

    assert capsys.readouterr().out.startswith("✓ HP d1 dilupakan")
    assert PhoneAuth(phone_file(home)).check(token) is None


def test_phone_forget_of_an_unknown_phone_fails_and_points_at_the_list(home, capsys):
    pair_phone(home, datetime(2026, 10, 14, 7, 30))

    assert cli.main(["phone", "forget", "d9"]) == 1

    out = capsys.readouterr().out
    assert out.startswith("✗") and "waifu phone devices" in out
    assert [device.id for device in PhoneAuth(phone_file(home)).devices()] == ["d1"]


class FakePhoneServer:
    """Stands in for PhoneServer without a port, remembering whether `waifu run` listened when it
    started and stopped serving."""

    def __init__(
        self, config, avatar, auth, backend, *, owner_call, assistant_name, camera_max_side=None
    ) -> None:
        self.config = config
        self.avatar = avatar
        self.auth = auth
        self.backend = backend
        self.owner_call = owner_call
        self.assistant_name = assistant_name
        self.camera_max_side = camera_max_side
        self.url = f"http://127.0.0.1:{config.port}/"
        self.listening: list[bool] = []

    def __enter__(self) -> "FakePhoneServer":
        self.listening.append(listening())
        return self

    def __exit__(self, *exc_info) -> None:
        self.listening.append(listening())


@pytest.fixture
def phone_servers(monkeypatch) -> list[FakePhoneServer]:
    built: list[FakePhoneServer] = []

    def build(*args, **kwargs) -> FakePhoneServer:
        built.append(FakePhoneServer(*args, **kwargs))
        return built[-1]

    monkeypatch.setattr(cli, "PhoneServer", build)
    return built


def test_run_serves_her_to_the_paired_phone_for_as_long_as_she_listens(
    home, run_fakes, phone_servers, capsys
):
    write_config(home, PHONE_ON + "assistant:\n  owner_call: Sayang\n")

    assert cli.main(["run", "--no-speaker-check", "--dry-run"]) == 0

    [server] = phone_servers
    assert server.listening == [False, True]
    assert (server.config.host, server.config.port) == (TAILNET_NAME, 9883)
    assert server.avatar.engine == "vrm"
    assert server.auth.path == Path("data/phone.json")
    assert (server.owner_call, server.assistant_name) == ("Sayang", "Cyrene")
    assert f"https://{TAILNET_NAME}/" in capsys.readouterr().out


def test_run_hands_the_phone_s_messages_and_recordings_to_her(home, run_fakes, phone_servers):
    append_config(home, PHONE_ON)
    assert cli.main(["run", "--no-speaker-check", "--dry-run"]) == 0
    [server] = phone_servers
    assistant = RecordingAssistant.instances[0]

    heard = server.backend.transcribe(b"RIFF....WAVE")
    reply = server.backend.chat(heard)

    assert assistant.phone_recordings == [b"RIFF....WAVE"]
    assert assistant.phone_messages == ["nyalakan lampu kamar"]
    assert reply.text == REPLY


def test_run_says_the_phone_cannot_reach_her_until_phone_host_is_set(
    home, run_fakes, phone_servers, capsys
):
    append_config(home, "phone:\n  enabled: true\n")

    assert cli.main(["run", "--no-speaker-check", "--dry-run"]) == 0

    assert len(phone_servers) == 1
    line = next(line for line in capsys.readouterr().out.splitlines() if "phone.host" in line)
    assert line.startswith("!") and "waifu phone setup" in line


def test_run_listens_without_the_phone_when_its_port_is_taken(home, run_fakes, monkeypatch, capsys):
    append_config(home, PHONE_ON)

    def taken(*args, **kwargs):
        raise PhoneError("Port 127.0.0.1:9883 sudah dipakai program lain.")

    monkeypatch.setattr(cli, "PhoneServer", taken)

    assert cli.main(["run", "--no-speaker-check", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "! Cyrene di HP tidak tersedia: Port 127.0.0.1:9883 sudah dipakai program lain." in out
    assert RecordingAssistant.instances[0].ran_with is not None


@pytest.mark.parametrize(
    ("argv", "phone"),
    [(["run", "--no-speaker-check", "--dry-run"], ""), (["chat", "--dry-run"], PHONE_ON)],
    ids=["run-with-the-phone-off", "chat"],
)
def test_the_phone_is_served_only_by_run_and_only_when_it_is_on(
    argv, phone, home, run_fakes, phone_servers, monkeypatch
):
    append_config(home, phone)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(argv) == 0

    assert phone_servers == []


def phone_lines(out: str) -> list[str]:
    """The phone part of `waifu check`, its last lines."""
    lines = out.splitlines()
    return lines[next(index for index, line in enumerate(lines) if "Cyrene di HP" in line) :]


def test_check_reports_the_phone_off_without_running_tailscale(home, nothing_installed, capsys):
    cli.main(["check"])

    assert phone_lines(capsys.readouterr().out) == ["! Cyrene di HP mati (phone.enabled: false)"]


@pytest.mark.parametrize("target", ["127.0.0.1", "localhost"])
def test_check_with_the_phone_ready_marks_tailscale_its_serve_the_host_and_the_paired_phone(
    target, home, nothing_installed, tailscale, capsys
):
    write_config(home, PHONE_READY)
    tailscale.serve = serving(target=target)
    pair_phone(home, datetime(2026, 10, 14, 7, 30))

    cli.main(["check"])

    assert phone_lines(capsys.readouterr().out) == [
        "✓ Cyrene di HP: server di 127.0.0.1:9883 selama 'waifu run'",
        f"✓ Tailscale: {cli.TAILSCALE_INSTALLED}",
        f"✓ Tailscale tersambung; nama PC ini {TAILNET_NAME}",
        f"✓ tailscale serve membagikan https://{TAILNET_NAME}/ ke 127.0.0.1:9883",
        f"✓ phone.host: {TAILNET_NAME}",
        "! Kamera HP: tidak tersedia dengan Ollama",  # the example config's brain
        "✓ HP terpasang: 1 (lihat 'waifu phone devices')",
    ]
    assert [command[1:] for command in tailscale.commands] == [
        ["status", "--json"],
        ["serve", "status", "--json"],
    ]


@pytest.mark.parametrize(
    "serve",
    [
        {},
        serving(9990),
        serving(https_port=8443),
        serving(target="192.168.1.20"),
        {"Foreground": {"session": serving()}},
        {"Web": {f"{TAILNET_NAME}:443": {"Handlers": {"/": {"Path": "C:/web"}}}}},
        {"Web": []},
    ],
    ids=[
        "nothing",
        "another-port",
        "another-https-port",
        "another-computer",
        "only-in-the-foreground",
        "files",
        "not-a-serve-config",
    ],
)
def test_check_fails_a_phone_that_tailscale_does_not_serve_and_gives_the_command(
    serve, home, nothing_installed, tailscale, capsys
):
    append_config(home, PHONE_ON)
    tailscale.serve = serve

    assert cli.main(["check"]) == 1

    [line] = [line for line in phone_lines(capsys.readouterr().out) if "tailscale serve" in line]
    assert line == (
        "✗ tailscale serve belum membagikan 127.0.0.1:9883 lewat HTTPS port 443. Jalankan "
        f"sekali di Command Prompt: {installed_serve_command()}"
    )


def test_check_fails_a_phone_server_that_tailscale_funnel_opens_to_the_internet(
    home, nothing_installed, tailscale, capsys
):
    append_config(home, PHONE_ON)
    tailscale.serve = serving(funnel=True)

    assert cli.main(["check"]) == 1

    [line] = [line for line in phone_lines(capsys.readouterr().out) if "Funnel" in line]
    assert line == (
        f"✗ Tailscale Funnel menyala untuk {TAILNET_NAME}: server HP terbuka ke seluruh "
        "internet, bukan hanya ke jaringan Tailscale-mu. Matikan sekali di Command Prompt: "
        f'"{cli.TAILSCALE_INSTALLED}" funnel --https=443 off, lalu bagikan lagi dengan: '
        f"{installed_serve_command()}"
    )


def test_check_leaves_funnel_on_another_https_port_alone(
    home, nothing_installed, tailscale, capsys
):
    append_config(home, PHONE_ON)
    serve = serving()
    serve["AllowFunnel"] = {f"{TAILNET_NAME}:8443": True, "other-pc.tail1234.ts.net:443": True}
    tailscale.serve = serve

    cli.main(["check"])

    lines = phone_lines(capsys.readouterr().out)
    assert f"✓ tailscale serve membagikan https://{TAILNET_NAME}/ ke 127.0.0.1:9883" in lines


def test_check_warns_that_the_phone_shows_no_avatar_without_avatar_model(
    home, nothing_installed, capsys
):
    append_config(home, PHONE_ON)  # the example config leaves avatar.model out

    cli.main(["check"])

    [line] = [line for line in phone_lines(capsys.readouterr().out) if "avatar.model" in line]
    assert line == (
        "! avatar.model belum diisi, jadi halaman HP hanya menampilkan chat tanpa avatar. Isi "
        "avatar.model (dan avatar.live2d_core untuk Live2D) di config.yaml, juga kalau "
        "avatar.enabled: false"
    )


def test_check_fails_a_phone_without_tailscale_and_still_checks_the_rest(
    home, nothing_installed, capsys
):
    write_config(home, PHONE_READY)

    cli.main(["check"])

    lines = phone_lines(capsys.readouterr().out)
    assert lines[1].startswith("✗ Tailscale belum terpasang")
    assert lines[2:] == [
        f"✓ phone.host: {TAILNET_NAME}",
        "! Kamera HP: tidak tersedia dengan Ollama",
        "! Belum ada HP yang dipasangkan; jalankan 'waifu phone pair'",
    ]


def test_check_fails_a_phone_whose_tailscale_is_not_logged_in(
    home, nothing_installed, tailscale, capsys
):
    append_config(home, PHONE_ON)
    tailscale.status = tailscale_status(state="NeedsLogin")

    cli.main(["check"])

    lines = phone_lines(capsys.readouterr().out)
    assert any(line.startswith("✗ Tailscale belum tersambung") for line in lines)
    assert f"✓ phone.host: {TAILNET_NAME}" in lines


def test_check_fails_a_phone_host_that_is_not_this_pc_s_tailscale_name(
    home, nothing_installed, tailscale, capsys
):
    append_config(home, "phone:\n  enabled: true\n  host: other-pc.tail1234.ts.net\n")

    cli.main(["check"])

    [line] = [line for line in phone_lines(capsys.readouterr().out) if "phone.host" in line]
    assert line == (
        "✗ phone.host other-pc.tail1234.ts.net bukan nama PC ini di Tailscale; ganti dengan "
        f"host: {TAILNET_NAME}"
    )


def test_check_fails_a_phone_without_phone_host_and_says_how_to_get_it(
    home, nothing_installed, capsys
):
    append_config(home, "phone:\n  enabled: true\n")

    cli.main(["check"])

    [line] = [line for line in phone_lines(capsys.readouterr().out) if "phone.host" in line]
    assert line == (
        "✗ phone.host belum diisi; jalankan 'waifu phone setup' lalu isi baris host yang "
        "ditampilkannya"
    )


def test_check_fails_a_pairing_file_it_cannot_read(home, nothing_installed, capsys):
    append_config(home, PHONE_ON)
    phone_file(home).mkdir(parents=True)  # a folder where the file should be

    cli.main(["check"])

    line = phone_lines(capsys.readouterr().out)[-1]
    assert line.startswith(f"✗ File HP terpasang {Path('data/phone.json')} tidak bisa dibaca")


# --- seeing through the phone camera ------------------------------------------------------------

RUN_ARGV = ["run", "--no-speaker-check", "--dry-run"]
PHOTO = b"\xff\xd8\xff\xc0 a photo from the phone camera"


def test_run_shares_one_photo_holder_with_her_brain_and_the_phone_while_the_camera_is_offered(
    home, run_fakes, brains, phone_servers
):
    use_claude(home)
    append_config(home, PHONE_ON + "vision:\n  max_side: 1024\n")

    assert cli.main(RUN_ARGV) == 0

    images = brains[0].turn_images
    assert isinstance(images, TurnImages)
    assert RecordingAssistant.instances[0].collaborators["turn_images"] is images
    [server] = phone_servers
    assert server.camera_max_side == 1024  # photos as large as her screenshots


def test_run_lets_a_turn_sent_through_the_phone_camera_look_at_the_screen_only_when_named(
    home, run_fakes, brains, phone_servers, capture
):
    use_claude(home)
    append_config(home, PHONE_ON)
    assert cli.main(RUN_ARGV) == 0
    camera_turn = RecordingAssistant.instances[0].collaborators["camera_turn"]
    screen = screen_tools(brains[0])

    camera_turn.begin()
    screen.begin_turn("coba lihat ini")
    refused = screen.run("look_at_screen", {})[1]
    screen.begin_turn("lihat layarku")
    named = screen.run("look_at_screen", {})[1]
    camera_turn.end()
    screen.begin_turn("coba lihat ini")
    after = screen.run("look_at_screen", {})[1]

    assert isinstance(camera_turn, CameraTurn)
    assert (refused, named, after) == (True, False, False)
    assert capture.sizes == [1280, 1280]


@pytest.mark.parametrize(
    ("argv", "claude", "settings"),
    [
        (RUN_ARGV, False, PHONE_ON),
        (RUN_ARGV, True, PHONE_ON + "  camera: false\n"),
        (RUN_ARGV, True, PHONE_ON + "vision:\n  enabled: false\n"),
        (RUN_ARGV, True, ""),
        (["chat", "--dry-run"], True, PHONE_ON),
    ],
    ids=["ollama", "camera-off", "vision-off", "phone-off", "chat"],
)
def test_without_the_camera_offered_nothing_holds_a_photo_and_the_phone_refuses_them(
    argv, claude, settings, home, run_fakes, brains, phone_servers, monkeypatch
):
    if claude:
        use_claude(home)
    append_config(home, settings)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(argv) == 0

    assert brains[0].turn_images is None
    assert [a.collaborators["turn_images"] for a in RecordingAssistant.instances] in ([], [None])
    assert [a.collaborators["camera_turn"] for a in RecordingAssistant.instances] in ([], [None])
    assert all(server.camera_max_side is None for server in phone_servers)


def test_run_hands_a_photo_from_the_phone_to_her_with_its_message(home, run_fakes, phone_servers):
    use_claude(home)
    append_config(home, PHONE_ON)
    assert cli.main(RUN_ARGV) == 0
    [server] = phone_servers
    assistant = RecordingAssistant.instances[0]

    reply = server.backend.chat("ini apa?", image=PHOTO, camera=True)
    server.backend.chat("coba lihat ini", camera=True)
    server.backend.chat("makasih")

    assert assistant.phone_messages == ["ini apa?", "coba lihat ini", "makasih"]
    assert assistant.phone_photos == [PHOTO, None, None]
    assert assistant.phone_cameras == [True, True, False]
    assert reply.text == REPLY


def write_out_example_phone(home: Path) -> None:
    """The example config with its commented `phone` block written out."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    commented = text[text.index("# phone:\n") :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())
    config.write_text(text.replace(commented, written_out), encoding="utf-8")


@pytest.mark.parametrize(("camera", "max_side"), [(True, 1280), (False, None)])
def test_the_example_phone_block_is_valid_once_written_out_and_switches_the_camera(
    camera, max_side, home, run_fakes, phone_servers
):
    use_claude(home)
    write_out_example_phone(home)
    if not camera:
        config = home / "config.yaml"
        text = config.read_text(encoding="utf-8")
        assert "  camera: true" in text
        config.write_text(text.replace("  camera: true", "  camera: false"), encoding="utf-8")

    assert cli.main(RUN_ARGV) == 0

    [server] = phone_servers
    assert server.config.host == "nama-pc.tailnet-kamu.ts.net"
    assert server.camera_max_side == max_side


def camera_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Kamera HP" in line]
    return line


@pytest.mark.parametrize(
    ("claude", "settings", "expected"),
    [
        (True, "", "✓ Kamera HP: aktif"),
        (False, "", "! Kamera HP: tidak tersedia dengan Ollama"),
        (True, "vision:\n  enabled: false\n", "! Kamera HP mati (vision.enabled: false)"),
        (True, "  camera: false\n", "! Kamera HP mati (phone.camera: false)"),
        (False, "  camera: false\n", "! Kamera HP mati (phone.camera: false)"),
    ],
    ids=["claude", "ollama", "vision-off", "camera-off", "ollama-with-camera-off"],
)
def test_check_reports_whether_the_phone_page_offers_its_camera(
    claude, settings, expected, home, nothing_installed, capsys
):
    if claude:
        use_claude(home)
    append_config(home, PHONE_ON + settings)

    cli.main(["check"])

    assert camera_line(capsys.readouterr().out) == expected


# --- PC health -----------------------------------------------------------------------------------

RTX = GpuStatus("NVIDIA GeForce RTX 5060 Ti", 54, 12, 3.21, 15.93, 45.07)
BUSY_PC = PcSnapshot(
    23.4,
    12.34,
    31.9,
    (Drive("C:", 120.46, 465.1), Drive("D:", 8.43, 931.5)),
    RTX,
    5 * 3600 + 12 * 60 + 30,
)


def write_out_example_pc_health(home: Path, *, enabled: bool = True, notices: bool = True) -> None:
    """The example config with its commented `pc_health` block written out."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    commented = text[text.index("# pc_health:\n") :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())
    if not enabled:
        written_out = written_out.replace("enabled: true", "enabled: false")
    if not notices:
        written_out = written_out.replace("notices: true", "notices: false")
    config.write_text(text.replace(commented, written_out), encoding="utf-8")


def pc_tools(brain: HouseBrain) -> PcTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, PcTools)]
    return tools


def test_run_gives_the_brain_the_pc_tool_and_watches_the_pc_while_she_listens(
    home, run_fakes, brains, pc_readings, pc_watchers, monkeypatch
):
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = pc_watchers
    assert watcher.config == PcHealthConfig()  # the example config's settings
    assert (watcher.owner_call, watcher.title, watcher.notifier) == ("Master", "Cyrene", notifier)
    assert watcher.listening == [False, True]
    assert "mic opened" in watcher.events_when_started  # once she is ready to listen
    assert "mic closed" not in watcher.events_when_stopped
    watcher.announce("Master, disk C: tinggal 8,4 GB.", "sad")
    assert RecordingAssistant.instances[0].announced == [("Master, disk C: tinggal 8,4 GB.", "sad")]
    # One CPU sampler for the tool and the watcher: a reading within seconds of the other's
    # measures since it instead of sampling half a second again.
    result, is_error = pc_tools(brains[0]).run("pc_health", {})
    assert (json.loads(result)["cpu_percent"], is_error) == (12, False)
    assert watcher.read() == QUIET_PC
    [tool_sampler, watcher_sampler] = pc_readings.samplers
    assert tool_sampler is watcher_sampler


def test_run_watches_the_pc_for_an_owner_she_calls_sayang_without_pushing_when_notify_is_off(
    home, run_fakes, brains, pc_watchers
):
    call_the_owner(home, "Sayang")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = pc_watchers
    assert (watcher.owner_call, watcher.notifier) == ("Sayang", None)


@pytest.mark.parametrize(
    ("failure", "exit_code"),
    [(SttError("Whisper berhenti."), 1), (KeyboardInterrupt(), 130)],
    ids=["whisper-fails", "ctrl-c"],
)
def test_run_stops_watching_the_pc_however_listening_ends(
    failure, exit_code, home, run_fakes, brains, pc_watchers, monkeypatch
):
    def listen_until_it_fails(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)
        raise failure

    monkeypatch.setattr(RecordingAssistant, "run", listen_until_it_fails)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == exit_code

    [watcher] = pc_watchers
    assert watcher.listening == [False, True]


def test_run_with_the_pc_notices_off_answers_about_the_pc_but_never_speaks_up_about_it(
    home, run_fakes, brains, pc_watchers
):
    write_out_example_pc_health(home, notices=False)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert pc_tools(brains[0]).run("pc_health", {})[1] is False
    assert pc_watchers == []


@pytest.mark.parametrize("claude", [False, True], ids=["ollama", "claude"])
def test_chat_gives_either_brain_the_pc_tool_but_never_watches_the_pc(
    claude, home, brains, pc_readings, pc_watchers, monkeypatch
):
    if claude:
        use_claude(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    tools = pc_tools(brains[0])
    for _ in range(2):
        result, is_error = tools.run("pc_health", {})
        assert (json.loads(result)["cpu_percent"], is_error) == (12, False)
    [first, second] = pc_readings.samplers
    assert first is second  # a reading within seconds of her previous one measures since it
    assert pc_watchers == []


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_the_pc_health_off_she_neither_reads_nor_watches_the_pc(
    argv, home, run_fakes, brains, pc_readings, pc_watchers, monkeypatch
):
    write_out_example_pc_health(home, enabled=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert not any(isinstance(tools, PcTools) for tools in brains[0].providers)
    assert (pc_watchers, pc_readings.samplers) == ([], [])


def test_pc_prints_one_line_per_part_rounded_with_a_decimal_comma(home, pc_readings, capsys):
    pc_readings.snapshot = BUSY_PC

    assert cli.main(["pc"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "CPU: 23%",
        "RAM: 12,3 dari 31,9 GB terpakai (39%)",
        "Disk C: tinggal 120,5 dari 465,1 GB (26% kosong)",
        "Disk D: tinggal 8,4 dari 931,5 GB (1% kosong)",
        "GPU: NVIDIA GeForce RTX 5060 Ti, 54°C, beban 12%, VRAM 3,2 dari 15,9 GB, daya 45,1 W",
        "Sejak restart: 5 jam 12 menit",
    ]
    [sampler] = pc_readings.samplers  # one reading: the first samples the CPU over 0.5 s
    assert isinstance(sampler, FakeCpuLoad)


def test_pc_names_each_part_it_could_not_read(home, pc_readings, capsys):
    pc_readings.snapshot = PcSnapshot(None, None, None, (), None, None)

    assert cli.main(["pc"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "CPU: tidak terbaca",
        "RAM: tidak terbaca",
        "Disk: tidak terbaca",
        "GPU: tidak terbaca (bukan NVIDIA atau nvidia-smi tidak ada)",
        "Sejak restart: tidak terbaca",
    ]


@pytest.mark.parametrize(
    ("uptime_s", "text"),
    [
        (26 * 86400 + 13 * 3600 + 28 * 60, "26 hari 13 jam"),
        (3 * 86400 + 59, "3 hari"),
        (86400 - 60, "23 jam 59 menit"),
    ],
)
def test_pc_gives_a_long_uptime_in_days_and_hours(home, pc_readings, capsys, uptime_s, text):
    pc_readings.snapshot = PcSnapshot(23.4, 12.34, 31.9, (), None, uptime_s)

    assert cli.main(["pc"]) == 0

    assert capsys.readouterr().out.splitlines()[-1] == f"Sejak restart: {text}"


def test_pc_leaves_out_the_gpu_readings_nvidia_smi_could_not_give(home, pc_readings, capsys):
    gpu = GpuStatus("NVIDIA GeForce RTX 5060 Ti", 54, None, None, 15.93, None)
    pc_readings.snapshot = PcSnapshot(23.4, 12.34, 31.9, (), gpu, 420)

    assert cli.main(["pc"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert out[-2:] == ["GPU: NVIDIA GeForce RTX 5060 Ti, 54°C", "Sejak restart: 7 menit"]


def test_pc_reads_the_pc_with_the_pc_health_off_too(home, pc_readings, capsys):
    write_out_example_pc_health(home, enabled=False)

    assert cli.main(["pc"]) == 0

    assert capsys.readouterr().out.startswith("CPU: 12%\n")


def pc_health_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Kesehatan PC" in line]
    return line


@pytest.mark.parametrize(
    ("enabled", "gpu", "expected"),
    [
        (True, RTX, "✓ Kesehatan PC: aktif, GPU NVIDIA GeForce RTX 5060 Ti"),
        (True, None, "! Kesehatan PC: aktif, GPU tidak terbaca"),
        (False, RTX, "! Kesehatan PC mati"),
    ],
    ids=["nvidia", "no-gpu", "off"],
)
def test_check_reports_whether_she_reads_the_pc_and_which_gpu(
    enabled, gpu, expected, home, nothing_installed, pc_readings, capsys
):
    write_out_example_pc_health(home, enabled=enabled)
    pc_readings.gpu = gpu

    cli.main(["check"])

    assert pc_health_line(capsys.readouterr().out) == expected
    assert pc_readings.gpu_reads == (1 if enabled else 0)  # nvidia-smi only while it is on


# --- play time ---------------------------------------------------------------------------------

LATE = "Master, sudah lewat jam setengah dua belas, jangan terlalu malam ya."
# Saturday 17 October 2026, 22:00 WIB: `waifu playtime` and the tool read the file at this time.
PLAY_NOW = datetime(2026, 10, 17, 22, 0, tzinfo=timezone(timedelta(hours=7)))


def play_at(hour: float) -> datetime:
    """`hour` hours after Saturday 00:00 WIB."""
    return PLAY_NOW.replace(hour=0) + timedelta(seconds=round(hour * 3600))


def play_time_tools(brain: HouseBrain) -> PlayTimeTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, PlayTimeTools)]
    return tools


@pytest.fixture
def played(home, monkeypatch) -> PlayTimeStore:
    """The clock at PLAY_NOW and a Saturday in the example config's play time file: HSR 08:00
    until 09:00 and 20:00 until 21:30, ZZZ 21:00 until 21:45, Genshin from Friday 22:00 until
    00:30, and HSR open again since 21:50, last seen at 21:59."""
    monkeypatch.setattr(cli, "wib_now", lambda: PLAY_NOW)
    store = PlayTimeStore(
        home / "data" / "playtime.json",
        games=["hsr", "genshin", "zzz"],
        keep_days=90,
        now=lambda: PLAY_NOW,
    )
    store.save(
        [Session("hsr", play_at(21) + timedelta(minutes=50), play_at(21) + timedelta(minutes=59))],
        [
            Session("genshin", play_at(-2), play_at(0.5)),
            Session("hsr", play_at(8), play_at(9)),
            Session("hsr", play_at(20), play_at(21.5)),
            Session("zzz", play_at(21), play_at(21.75)),
        ],
    )
    return store


def test_run_counts_play_time_while_she_listens_and_nudges_only_between_conversations(
    home, run_fakes, brains, play_time_watchers
):
    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = play_time_watchers
    assert watcher.config == PlayTimeConfig()  # the example config's settings
    assert watcher.list_processes is running_executables  # names only, never a game's handle
    assert watcher.store.path == Path("data") / "playtime.json"
    assert watcher.store.games == ("hsr", "genshin", "zzz")
    assert watcher.owner_call == "Master"
    assert watcher.listening == [False, True]
    assert "mic opened" in watcher.events_when_started  # once she is ready to listen
    assert "mic closed" not in watcher.events_when_stopped
    watcher.announce(LATE, "neutral")
    assistant = RecordingAssistant.instances[0]
    assert assistant.announced == [(LATE, "neutral")]
    # Never inside a conversation: she says it once the follow-up window has closed.
    assert assistant.waiting_for_followup == [LATE]


def test_run_counts_the_configured_games_with_the_bedtime_for_an_owner_she_calls_sayang(
    home, run_fakes, brains, play_time_watchers
):
    call_the_owner(home, "Sayang")
    append_config(
        home,
        "play_time:\n  poll_seconds: 120\n  games:\n    zzz: [ZenlessZoneZero.exe]\n"
        "  bedtime: 23:30\n  keep_days: 30\n  path: data/main.json\n",
    )

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = play_time_watchers
    assert watcher.config == PlayTimeConfig(
        poll_seconds=120,
        games={"zzz": ["ZenlessZoneZero.exe"]},
        bedtime=time(23, 30),
        keep_days=30,
        path=Path("data/main.json"),
    )
    assert (watcher.store.path, watcher.store.games, watcher.store.keep_days) == (
        Path("data/main.json"),
        ("zzz",),
        30,
    )
    assert watcher.owner_call == "Sayang"


@pytest.mark.parametrize(
    ("failure", "exit_code"),
    [(SttError("Whisper berhenti."), 1), (KeyboardInterrupt(), 130)],
    ids=["whisper-fails", "ctrl-c"],
)
def test_run_stops_counting_play_time_however_listening_ends(
    failure, exit_code, home, run_fakes, brains, play_time_watchers, monkeypatch
):
    def listen_until_it_fails(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)
        raise failure

    monkeypatch.setattr(RecordingAssistant, "run", listen_until_it_fails)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == exit_code

    [watcher] = play_time_watchers
    assert watcher.listening == [False, True]


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_run_and_chat_give_the_brain_the_play_time_tool_and_only_run_counts(
    argv, home, run_fakes, brains, play_time_watchers, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert play_time_tools(brains[0]).specs()[0].name == "play_time"
    assert len(play_time_watchers) == (1 if argv[0] == "run" else 0)


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_play_time_off_there_is_neither_the_tool_nor_the_watcher(
    argv, home, run_fakes, brains, play_time_watchers, monkeypatch
):
    append_config(home, "play_time:\n  enabled: false\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert not any(isinstance(tools, PlayTimeTools) for tools in brains[0].providers)
    assert play_time_watchers == []


def test_the_play_time_tool_reads_the_file_run_keeps(home, brains, played, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))
    assert cli.main(["chat", "--dry-run"]) == 0

    result, is_error = play_time_tools(brains[0]).run("play_time", {"game": "hsr"})

    assert is_error is False
    data = json.loads(result)
    assert (data["from"], data["to"]) == ("2026-10-17 00:00", "2026-10-17 22:00")
    assert data["games"] == [
        {
            "game": "hsr",
            "name": "Honkai: Star Rail",
            "hours": 2,
            "minutes": 39,
            "sessions": 3,
            "playing_now": True,
        }
    ]


def test_the_weekly_report_holds_the_play_time_with_play_time_on(
    home, brains, game_data, games, monkeypatch
):
    enable_gacha(home)
    play_only(home, "[hsr]")
    save_hsr_week(home)
    hsr_week_on_hoyolab(game_data)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))
    assert cli.main(["chat", "--dry-run"]) == 0

    result, _ = weekly_report_tools(brains[0]).run("weekly_report", {})

    [hsr] = json.loads(result)["games"]
    assert hsr["play_time"] == {"hours": 0, "minutes": 0, "sessions": 0}
    assert hsr["play_time_problem"] is None
    assert "the time each game was open" in weekly_report_tools(brains[0]).specs()[0].description


def test_the_weekly_report_leaves_the_play_time_out_with_play_time_off(
    home, brains, game_data, games, monkeypatch
):
    enable_gacha(home)
    play_only(home, "[hsr]")
    save_hsr_week(home)
    hsr_week_on_hoyolab(game_data)
    append_config(home, "play_time:\n  enabled: false\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))
    assert cli.main(["chat", "--dry-run"]) == 0

    result, _ = weekly_report_tools(brains[0]).run("weekly_report", {})

    [hsr] = json.loads(result)["games"]
    assert "play_time" not in hsr


def test_playtime_prints_today_s_time_per_game_and_the_total_from_her_own_count(
    home, played, capsys
):
    assert cli.main(["playtime"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Jam main hari ini, Sabtu 17-10-2026, dari catatan Cyrene:",
        "GAME               WAKTU           SESI",
        "Honkai: Star Rail  2 jam 39 menit  3     sedang main",
        "Genshin Impact     30 menit        1",
        "Zenless Zone Zero  45 menit        1",
        "Total              3 jam 24 menit  5",
        "Dihitung hanya selama 'waifu run' menyala, sejak Jumat 16-10-2026 22:00; game yang "
        "terbuka tapi tidak dimainkan ikut terhitung.",
    ]


def test_playtime_prints_yesterday_with_a_dash_for_a_game_not_played_and_nothing_open(
    home, played, capsys
):
    assert cli.main(["playtime", "yesterday"]) == 0

    assert capsys.readouterr().out.splitlines()[:6] == [
        "Jam main kemarin, Jumat 16-10-2026, dari catatan Cyrene:",
        "GAME               WAKTU  SESI",
        "Honkai: Star Rail  -      0",  # open now, but yesterday is over
        "Genshin Impact     2 jam  1",
        "Zenless Zone Zero  -      0",
        "Total              2 jam  1",
    ]


@pytest.mark.parametrize(
    ("period", "heading"),
    [
        ("week", "Jam main minggu ini, sejak Senin 12-10-2026, dari catatan Cyrene:"),
        ("month", "Jam main bulan ini, Oktober 2026, dari catatan Cyrene:"),
    ],
)
def test_playtime_prints_the_week_or_the_month_when_asked(home, played, capsys, period, heading):
    assert cli.main(["playtime", period]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == heading
    assert lines[2:6] == [
        "Honkai: Star Rail  2 jam 39 menit  3     sedang main",
        "Genshin Impact     2 jam 30 menit  1",  # from Friday 22:00
        "Zenless Zone Zero  45 menit        1",
        "Total              5 jam 24 menit  5",
    ]


def test_playtime_says_when_nothing_is_recorded_and_makes_no_file(home, capsys):
    assert cli.main(["playtime"]) == 0

    assert capsys.readouterr().out.splitlines() == ["Belum ada catatan jam main."]
    assert not (home / "data" / "playtime.json").exists()


def test_playtime_reads_the_file_with_play_time_off_and_says_so(home, played, capsys):
    append_config(home, "play_time:\n  enabled: false\n")

    assert cli.main(["playtime"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == (
        "! Jam main mati (play_time.enabled: false): tidak ada yang dihitung; ini catatan lama."
    )
    assert lines[1] == "Jam main hari ini, Sabtu 17-10-2026, dari catatan Cyrene:"


def test_playtime_exits_1_when_the_file_cannot_be_read(home, played, monkeypatch, capsys):
    def unreadable(self):
        raise PermissionError("access denied")

    monkeypatch.setattr(PlayTimeStore, "record", unreadable)

    assert cli.main(["playtime"]) == 1

    assert capsys.readouterr().out.splitlines() == [
        f"✗ File jam main {Path('data') / 'playtime.json'} tidak bisa dibaca: access denied"
    ]


def test_gacha_report_tells_the_week_s_play_time(home, game_data, games, capsys):
    play_only(home, "[hsr]")
    save_hsr_week(home)
    hsr_week_on_hoyolab(game_data)
    now = datetime.now(timezone(timedelta(hours=7))).replace(microsecond=0)
    PlayTimeStore(home / "data" / "playtime.json", games=["hsr"], keep_days=90).save(
        [], [Session("hsr", now - timedelta(hours=3), now - timedelta(hours=1))]
    )

    assert cli.main(["gacha", "report"]) == 0

    assert "  Jam main minggu ini: 2 jam dalam 1 sesi" in capsys.readouterr().out.splitlines()


def play_time_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "jam main" in line.lower()]
    return line


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (None, "✓ Jam main: aktif (HSR, Genshin, ZZZ)"),
        (
            "play_time:\n  games:\n    zzz: [ZenlessZoneZero.exe]\n  bedtime: 23:30\n",
            "✓ Jam main: aktif (ZZZ), pengingat tidur 23:30",
        ),
        ("play_time:\n  enabled: false\n", "! Jam main mati"),
    ],
    ids=["default", "bedtime", "off"],
)
def test_check_says_which_games_she_counts_without_listing_any_process(
    settings, expected, home, nothing_installed, capsys
):
    if settings is not None:
        append_config(home, settings)

    cli.main(["check"])

    assert play_time_line(capsys.readouterr().out) == expected
    assert not (home / "data" / "playtime.json").exists()


def test_check_moves_a_broken_play_time_file_aside_and_points_at_it(
    home, nothing_installed, capsys
):
    (home / "data").mkdir(exist_ok=True)
    (home / "data" / "playtime.json").write_text("broken", encoding="utf-8")

    cli.main(["check"])

    line = play_time_line(capsys.readouterr().out)
    [moved] = (home / "data").glob("playtime.json.corrupt-*")
    assert line == (
        f"! Jam main: aktif (HSR, Genshin, ZZZ); file jam main yang rusak dipindah ke "
        f"{Path('data') / moved.name} (lihat README, bagian Jam main)"
    )


def test_check_fails_when_the_play_time_file_cannot_be_read(
    home, nothing_installed, monkeypatch, capsys
):
    def unreadable(self):
        raise PermissionError("access denied")

    monkeypatch.setattr(PlayTimeStore, "record", unreadable)

    assert cli.main(["check"]) == 1

    assert play_time_line(capsys.readouterr().out) == (
        f"✗ File jam main {Path('data') / 'playtime.json'} tidak bisa dibaca: access denied"
    )


# --- focus companion ---------------------------------------------------------------------------

# Saturday 17 October 2026, 22:00 WIB: `run`'s focus timer and `waifu focus` read this clock.
FOCUS_NOW = datetime(2026, 10, 17, 22, 0, tzinfo=timezone(timedelta(hours=7)))
BLOCK_DONE = "Master, dua puluh lima menit selesai. Istirahat lima menit dulu ya."
FOCUS_PUSH = "focus-push"


class FocusClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def focus_clock(monkeypatch) -> FocusClock:
    """The WIB clock of `run`'s focus timer and tool and of `waifu focus`, at FOCUS_NOW."""
    clock = FocusClock(FOCUS_NOW)
    monkeypatch.setattr(cli, "wib_now", clock)
    return clock


def focus_tools(brain: HouseBrain) -> FocusTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, FocusTools)]
    return tools


def start_focus(brain: HouseBrain, **lengths) -> dict:
    result, is_error = focus_tools(brain).run("focus_timer", {"action": "start", **lengths})
    assert is_error is False, result
    return json.loads(result)


def focus_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "focus-timer"]


def await_focus_pushes() -> None:
    for thread in threading.enumerate():
        if thread.name == FOCUS_PUSH:
            thread.join(WAIT_S)


def focus_log(home: Path, clock: FocusClock) -> FocusStore:
    """The example config's focus log, inside the test's working directory."""
    return FocusStore(home / "data" / "focus.json", keep_days=90, now=clock)


def test_run_keeps_one_focus_timer_while_she_listens_and_holds_her_own_speech_in_a_block(
    home, run_fakes, brains, focus_timers, focus_clock, monkeypatch
):
    seen: dict[str, object] = {}

    def listen(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)
        quiet = self.collaborators["quiet"]
        seen["before"] = quiet()
        seen["started"] = start_focus(brains[0], rounds=2)
        seen["in_block"] = quiet()
        focus_clock.now += timedelta(minutes=25)
        seen["said"] = focus_timers[0].tick_once()
        seen["at_the_break"] = quiet()
        focus_clock.now += timedelta(minutes=5)
        focus_timers[0].tick_once()
        focus_clock.now += timedelta(minutes=5)  # five minutes into the second block

    monkeypatch.setattr(RecordingAssistant, "run", listen)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [timer] = focus_timers
    assistant = RecordingAssistant.instances[0]
    assert assistant.collaborators["quiet"] == timer.in_block
    assert (seen["before"], seen["in_block"], seen["at_the_break"]) == (False, True, False)
    assert seen["started"]["block_ends_at"] == "22:25"
    assert seen["said"] == [BLOCK_DONE]
    # Master asked for the session, so its lines are never held back like her own notices.
    assert assistant.reminded == [(BLOCK_DONE, "happy"), ("Yuk, lanjut fokus lagi.", "happy")]
    assert assistant.announced == []
    # Stopping `run` ends the session without a word, logging the part of the block done.
    assert timer.state() is None
    assert focus_threads() == []
    assert focus_log(home, focus_clock).blocks() == (
        FocusBlock(FOCUS_NOW, FOCUS_NOW + timedelta(minutes=25)),
        FocusBlock(FOCUS_NOW + timedelta(minutes=30), FOCUS_NOW + timedelta(minutes=35)),
    )
    assert to_anthropic(focus_tools(brains[0]).specs()[0])["strict"] is False


def test_run_skips_her_welcome_back_during_a_focus_block_but_answers_a_pat(
    home, run_fakes, avatar, brains, focus_timers, focus_clock, monkeypatch
):
    enable_avatar(home, extra=TOUCH_LINES)

    def listen(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)
        start_focus(brains[0])
        touch(avatar, "welcome_back", "head_pat")  # Master touches the mouse again mid-block
        self.await_reactions(1)
        focus_clock.now += timedelta(minutes=25)
        focus_timers[0].tick_once()
        touch(avatar, "welcome_back")
        self.await_reactions(1)

    monkeypatch.setattr(RecordingAssistant, "run", listen)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert RecordingAssistant.instances[0].reactions == [
        ("speak", "Elusannya enak", "happy"),
        ("speak", "Selamat datang", "happy"),
    ]


@pytest.mark.parametrize("push", [True, False], ids=["push", "no-push"])
def test_run_pushes_her_focus_lines_to_the_phone_only_with_focus_push(
    push, home, run_fakes, brains, focus_timers, focus_clock, monkeypatch
):
    call_the_owner(home, "Sayang")
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)
    if push:
        append_config(home, "focus:\n  push: true\n")

    def listen(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)
        start_focus(brains[0])
        focus_clock.now += timedelta(minutes=25)
        focus_timers[0].tick_once()
        await_focus_pushes()

    monkeypatch.setattr(RecordingAssistant, "run", listen)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    done = "Sayang, fokusnya selesai. Hebat, total dua puluh lima menit!"
    assert RecordingAssistant.instances[0].reminded == [(done, "happy")]
    assert notifier.sent == ([("Cyrene", done, ("hourglass_flowing_sand",))] if push else [])


@pytest.mark.parametrize(
    ("failure", "exit_code"),
    [(SttError("Whisper berhenti."), 1), (KeyboardInterrupt(), 130)],
    ids=["whisper-fails", "ctrl-c"],
)
def test_run_ends_the_focus_session_however_listening_ends(
    failure, exit_code, home, run_fakes, brains, focus_timers, monkeypatch
):
    def listen_until_it_fails(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)
        start_focus(brains[0])
        raise failure

    monkeypatch.setattr(RecordingAssistant, "run", listen_until_it_fails)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == exit_code

    [timer] = focus_timers
    assert timer.state() is None
    assert focus_threads() == []


def test_chat_gets_neither_the_focus_timer_nor_its_tool(home, brains, focus_timers, monkeypatch):
    # No voice to keep quiet, and `run` stays the only writer of the focus log.
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert not any(isinstance(tools, FocusTools) for tools in brains[0].providers)
    assert focus_timers == []


def test_with_focus_off_run_has_neither_the_timer_nor_its_tool_and_is_never_quiet(
    home, run_fakes, brains, focus_timers
):
    append_config(home, "focus:\n  enabled: false\n")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert not any(isinstance(tools, FocusTools) for tools in brains[0].providers)
    assert focus_timers == []
    assert RecordingAssistant.instances[0].collaborators["quiet"] is None


def at_focus(day_offset: int, hour: int, minute: int = 0) -> datetime:
    """`day_offset` days from Saturday (FOCUS_NOW's day) at `hour`:`minute` WIB."""
    return FOCUS_NOW.replace(hour=hour, minute=minute) + timedelta(days=day_offset)


@pytest.fixture
def focused(home, focus_clock) -> FocusStore:
    """The clock at FOCUS_NOW and the example config's focus log: four blocks of 25 minutes on
    Saturday morning, an hour on Thursday evening, and an hour on the Sunday before this week."""
    store = focus_log(home, focus_clock)
    for start in (0, 30, 60, 90):
        store.add(
            FocusBlock(
                at_focus(0, 8) + timedelta(minutes=start),
                at_focus(0, 8) + timedelta(minutes=start + 25),
            )
        )
    store.add(FocusBlock(at_focus(-2, 20), at_focus(-2, 21)))
    store.add(FocusBlock(at_focus(-6, 10), at_focus(-6, 11)))
    return store


FOCUS_NOTE = (
    "Dicatat hanya selama 'waifu run' menyala; sesi fokus dimulai dan dihentikan lewat Cyrene."
)


def test_focus_prints_today_s_and_this_week_s_focus_time(home, focused, capsys):
    assert cli.main(["focus"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Fokus hari ini, Sabtu 17-10-2026: 1 jam 40 menit dalam 4 blok",
        "Fokus minggu ini, sejak Senin 12-10-2026: 2 jam 40 menit dalam 5 blok",
        FOCUS_NOTE,
    ]


def test_focus_says_belum_ada_for_a_period_without_a_block(home, focus_clock, capsys):
    focus_log(home, focus_clock).add(FocusBlock(at_focus(-2, 20), at_focus(-2, 21)))

    assert cli.main(["focus"]) == 0

    assert capsys.readouterr().out.splitlines()[:2] == [
        "Fokus hari ini, Sabtu 17-10-2026: belum ada",
        "Fokus minggu ini, sejak Senin 12-10-2026: 1 jam dalam 1 blok",
    ]


def test_focus_says_when_nothing_is_logged_and_makes_no_file(home, focus_clock, capsys):
    assert cli.main(["focus"]) == 0

    assert capsys.readouterr().out.splitlines() == ["Belum ada catatan fokus."]
    assert not (home / "data" / "focus.json").exists()


def test_focus_reads_the_log_with_focus_off_and_says_so(home, focused, capsys):
    append_config(home, "focus:\n  enabled: false\n")

    assert cli.main(["focus"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "! Teman fokus mati (focus.enabled: false): ini catatan lama."
    assert lines[1] == "Fokus hari ini, Sabtu 17-10-2026: 1 jam 40 menit dalam 4 blok"


def test_focus_exits_1_when_the_log_cannot_be_read(home, focused, monkeypatch, capsys):
    def unreadable(self):
        raise PermissionError("access denied")

    monkeypatch.setattr(FocusStore, "blocks", unreadable)

    assert cli.main(["focus"]) == 1

    assert capsys.readouterr().out.splitlines() == [
        f"✗ File fokus {Path('data') / 'focus.json'} tidak bisa dibaca: access denied"
    ]


def focus_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "fokus" in line.lower()]
    return line


@pytest.mark.parametrize(
    ("settings", "notify", "expected"),
    [
        (None, False, "✓ Teman fokus: aktif"),
        ("focus:\n  push: true\n", True, "✓ Teman fokus: aktif, juga ke HP"),
        ("focus:\n  push: true\n", False, "✓ Teman fokus: aktif"),
        ("focus:\n  enabled: false\n", False, "! Teman fokus mati"),
    ],
    ids=["default", "push", "push-without-notify", "off"],
)
def test_check_says_whether_the_focus_companion_is_on(
    settings, notify, expected, home, nothing_installed, capsys
):
    if notify:
        enable_notify(home)
    if settings is not None:
        append_config(home, settings)

    cli.main(["check"])

    assert focus_line(capsys.readouterr().out) == expected
    assert not (home / "data" / "focus.json").exists()


def test_check_moves_a_broken_focus_log_aside_and_points_at_it(home, nothing_installed, capsys):
    (home / "data").mkdir(exist_ok=True)
    (home / "data" / "focus.json").write_text("broken", encoding="utf-8")

    cli.main(["check"])

    line = focus_line(capsys.readouterr().out)
    [moved] = (home / "data").glob("focus.json.corrupt-*")
    assert line == (
        f"! Teman fokus: aktif; file fokus yang rusak dipindah ke {Path('data') / moved.name} "
        "(lihat README, bagian Teman fokus)"
    )


def test_check_fails_when_the_focus_log_cannot_be_read(
    home, nothing_installed, monkeypatch, capsys
):
    def unreadable(self):
        raise PermissionError("access denied")

    monkeypatch.setattr(FocusStore, "blocks", unreadable)

    assert cli.main(["check"]) == 1

    assert focus_line(capsys.readouterr().out) == (
        f"✗ File fokus {Path('data') / 'focus.json'} tidak bisa dibaca: access denied"
    )


# --- web search and free offers ----------------------------------------------------------------


def offers_file(home: Path) -> Path:
    """Where the example config keeps the free offers, inside the test's working directory."""
    return home / "data" / "free_offers.json"


def offers_tools(brain: HouseBrain) -> OffersTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, OffersTools)]
    return tools


def local_at(days: int, hour: int) -> datetime:
    """An aware time in this PC's zone, `days` from today at `hour` o'clock."""
    day = date.today() + timedelta(days=days)
    return datetime(day.year, day.month, day.day, hour).astimezone()


def when_text(moment: datetime) -> str:
    """`Kamis 22-10-2026 22:00`, as the CLI writes a local time."""
    return f"{WEEKDAYS_ID[moment.weekday()]} {moment:%d-%m-%Y %H:%M}"


def day_text(day: date) -> str:
    return f"{WEEKDAYS_ID[day.weekday()]} {day:%d-%m-%Y}"


def free_games_now() -> tuple[GameGiveaway, ...]:
    """Bad Cheese free on Epic until next week, Portal on Steam (from GamerPower.com), and Sker
    Ritual free on Epic from next week."""
    return (
        GameGiveaway(
            id="epic:6a846880836044a2adad614ac771f487",
            title="Bad Cheese",
            platform="Epic Games Store",
            source="Epic Games Store",
            worth_usd=None,
            starts_at=local_at(-1, 22),
            ends_at=local_at(6, 22),
            ends_on=None,
            url="https://store.epicgames.com/en-US/p/bad-cheese-4256d9",
        ),
        GameGiveaway(
            id="gamerpower:3820",
            title="Portal",
            platform="Steam",
            source="GamerPower.com",
            worth_usd=9.99,
            starts_at=None,
            ends_at=None,
            ends_on=date.today() + timedelta(days=9),
            url="https://www.gamerpower.com/open/portal-steam-giveaway",
        ),
        GameGiveaway(
            id="epic:6aab3ddef4f84fbc9dd81c60f9d8549c",
            title="Sker Ritual",
            platform="Epic Games Store",
            source="Epic Games Store",
            worth_usd=None,
            starts_at=local_at(6, 22),
            ends_at=local_at(13, 22),
            ends_on=None,
            url="https://store.epicgames.com/en-US/p/sker-ritual-aac702",
        ),
    )


class FreeGames:
    """Stands in for offers.games.read_free_games: hands out `result` and remembers the
    platforms of each read."""

    def __init__(self, result: tuple[GameGiveaway, ...] | OffersError) -> None:
        self.result = result
        self.platforms: list[list[str]] = []

    def __call__(self, platforms, *, http, now) -> tuple[GameGiveaway, ...] | OffersError:
        assert isinstance(http, httpx.Client) and not http.is_closed
        assert now.tzinfo is not None
        self.platforms.append(list(platforms))
        return self.result


def use_free_games(monkeypatch, result: tuple[GameGiveaway, ...] | OffersError) -> FreeGames:
    fake = FreeGames(result)
    monkeypatch.setattr(cli, "read_free_games", fake)
    return fake


# Each provider's own site, where its offers' links lead.
PROVIDER_SITES = {"Anthropic": "www.anthropic.com", "Google": "ai.google.dev"}


def ai_offer(title: str, provider: str, **fields: object) -> AiOffer:
    slug = title.lower().replace(" ", "-")
    defaults: dict[str, object] = {
        "id": f"ai:{slug}",
        "kind": "free_credits",
        "access": "claim",
        "eligibility": "Siapa saja.",
        "deadline": None,
        "url": f"https://{PROVIDER_SITES[provider]}/{slug}",
        "summary": "Kredit gratis.",
        "first_seen": datetime.now().replace(microsecond=0),
    }
    return AiOffer(title=title, provider=provider, **{**defaults, **fields})


def keep_ai_offers(home: Path, *offers: AiOffer) -> datetime:
    """`offers` kept in the example config's offers file by a scan two days ago; its time."""
    scanned = datetime.now().replace(second=0, microsecond=0) - timedelta(days=2)
    OffersStore(offers_file(home)).merge_ai_offers(offers, scanned)
    return scanned


def claude_reports(monkeypatch, *offers: dict, searches: int = 3) -> list[dict]:
    """Claude, over a fake transport, answers every request with a report_offers call holding
    `offers`, after `searches` web searches; returns the bodies of the requests."""
    sent: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        report = {"type": "tool_use", "id": "toolu_1", "name": "report_offers"}
        return httpx2.Response(
            200,
            json={
                "id": f"msg_{len(sent)}",
                "type": "message",
                "role": "assistant",
                "model": OPUS,
                "content": [{**report, "input": {"offers": list(offers)}}],
                "stop_reason": "tool_use",
                "usage": {
                    "input_tokens": 20_000,
                    "output_tokens": 2_000,
                    "server_tool_use": {"web_search_requests": searches, "web_fetch_requests": 0},
                },
            },
        )

    use_claude_transport(monkeypatch, handler)
    return sent


def use_claude_transport(monkeypatch, handler, **options: object) -> None:
    """Every Claude client a command builds answers through `handler` instead of the API."""
    real_anthropic = anthropic.Anthropic

    def build(**kwargs: object) -> anthropic.Anthropic:
        transport = httpx2.MockTransport(handler)
        return real_anthropic(
            api_key="test-key", http_client=httpx2.Client(transport=transport), **options, **kwargs
        )

    monkeypatch.setattr(anthropic, "Anthropic", build)


GEMINI_REPORT = {
    "title": "Gemini API free tier",
    "provider": "Google",
    "kind": "free_tier",
    "access": "claim",
    "eligibility": "Siapa saja dengan akun Google.",
    "deadline": None,
    "url": "https://ai.google.dev/pricing",
    "summary": "Kuota gratis Gemini API setiap hari.",
}


def write_out_example_block(home: Path, name: str) -> None:
    """The example config with its commented `name` block written out."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    commented = text[text.index(f"# {name}:\n") :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())
    config.write_text(text.replace(commented, written_out), encoding="utf-8")


def month_usage(home: Path) -> UsageSummary:
    today = date.today()
    return UsageLedger(usage_file(home), prices={}, default_model=OPUS).month(
        today.year, today.month
    )


def test_run_gives_the_brain_the_offers_tools_and_watches_for_offers_while_she_listens(
    home, run_fakes, brains, offers_watchers, monkeypatch
):
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert [type(tools) for tools in brains[0].providers][-4:] == [
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
    [watcher] = offers_watchers
    assert watcher.config == FreeOffersConfig()  # the example config's settings
    assert (watcher.owner_call, watcher.title, watcher.notifier) == ("Master", "Cyrene", notifier)
    assert watcher.store.path == Path("data") / "free_offers.json"
    assert watcher.listening == [False, True]
    assert "mic opened" in watcher.events_when_started  # once she is ready to listen
    assert "mic closed" not in watcher.events_when_stopped
    watcher.announce("Master, ada game gratis baru: Portal di Steam.", "happy")
    assert RecordingAssistant.instances[0].announced == [
        ("Master, ada game gratis baru: Portal di Steam.", "happy")
    ]


def test_run_watches_for_offers_for_an_owner_she_calls_sayang_without_pushing_when_notify_is_off(
    home, run_fakes, brains, offers_watchers
):
    call_the_owner(home, "Sayang")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = offers_watchers
    assert (watcher.owner_call, watcher.notifier) == ("Sayang", None)


def test_the_offers_tool_sends_a_link_to_the_phone_through_her_notifier(
    home, run_fakes, brains, monkeypatch
):
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)
    claude_offer = ai_offer("Claude for Startups", "Anthropic", access="apply")
    keep_ai_offers(home, claude_offer)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    result, is_error = offers_tools(brains[0]).run("send_offer_link", {"id": claude_offer.id})
    assert is_error is False, result
    assert notifier.sent == [
        (
            "Claude for Startups",
            f"Anthropic: Kredit gratis. Perlu mendaftar.\n{claude_offer.url}",
            ("link",),
        )
    ]
    assert notifier.clicks == [claude_offer.url]


@pytest.mark.parametrize("claude", [False, True], ids=["ollama", "claude"])
def test_chat_gives_either_brain_the_offers_tools_but_never_watches(
    claude, home, brains, offers_watchers, monkeypatch
):
    if claude:
        use_claude(home)
    games = use_free_games(monkeypatch, free_games_now())
    keep_ai_offers(home, ai_offer("Gemini API free tier", "Google"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    result, is_error = offers_tools(brains[0]).run("free_offers", {})
    offers = json.loads(result)
    assert is_error is False
    assert [game["title"] for game in offers["games"]] == ["Bad Cheese", "Portal", "Sker Ritual"]
    assert [offer["title"] for offer in offers["ai"]["offers"]] == ["Gemini API free tier"]
    assert games.platforms == [["epic", "steam", "gog"]]  # the default platforms
    assert offers_watchers == []


def test_chat_sends_a_link_to_the_phone_when_master_asks(home, brains, monkeypatch):
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)
    gemini = ai_offer("Gemini API free tier", "Google")
    keep_ai_offers(home, gemini)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat"]) == 0

    assert offers_tools(brains[0]).run("send_offer_link", {"id": gemini.id})[1] is False
    assert notifier.clicks == [gemini.url]


def test_the_offers_tool_reads_the_configured_platforms(home, brains, monkeypatch):
    append_config(home, "free_offers:\n  game_platforms: [gog, itchio]\n")
    games = use_free_games(monkeypatch, ())
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat"]) == 0

    assert offers_tools(brains[0]).run("free_offers", {"category": "games"})[1] is False
    assert games.platforms == [["gog", "itchio"]]


def test_with_the_games_off_the_offers_tool_says_so_without_reading_them(home, brains, monkeypatch):
    append_config(home, "free_offers:\n  games: false\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat"]) == 0

    result, is_error = offers_tools(brains[0]).run("free_offers", {"category": "games"})
    assert (json.loads(result)["message"], is_error) == (
        "Pencarian game gratis dimatikan (free_offers.games: false).",
        True,
    )


def test_run_with_the_offers_notices_off_answers_about_offers_but_never_speaks_up(
    home, run_fakes, brains, offers_watchers
):
    append_config(home, "free_offers:\n  notices: false\n")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert any(isinstance(tools, OffersTools) for tools in brains[0].providers)
    assert offers_watchers == []


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_the_free_offers_off_she_neither_tells_nor_watches(
    argv, home, run_fakes, brains, offers_watchers, monkeypatch
):
    append_config(home, "free_offers:\n  enabled: false\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert not any(isinstance(tools, OffersTools) for tools in brains[0].providers)
    assert offers_watchers == []


@pytest.mark.parametrize(
    ("failure", "exit_code"),
    [(SttError("Whisper berhenti."), 1), (KeyboardInterrupt(), 130)],
    ids=["whisper-fails", "ctrl-c"],
)
def test_run_stops_watching_for_offers_however_listening_ends(
    failure, exit_code, home, run_fakes, brains, offers_watchers, monkeypatch
):
    def listen_until_it_fails(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)
        raise failure

    monkeypatch.setattr(RecordingAssistant, "run", listen_until_it_fails)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == exit_code

    [watcher] = offers_watchers
    assert watcher.listening == [False, True]


def test_the_watcher_reads_the_configured_platforms(
    home, run_fakes, brains, offers_watchers, monkeypatch
):
    append_config(home, "free_offers:\n  game_platforms: [epic, pc]\n")
    games = use_free_games(monkeypatch, free_games_now())

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = offers_watchers
    assert [game.title for game in watcher.read_games()] == ["Bad Cheese", "Portal", "Sker Ritual"]
    assert games.platforms == [["epic", "pc"]]


def test_the_watcher_s_scan_asks_the_configured_claude_and_records_its_cost_with_claude(
    home, run_fakes, brains, offers_watchers, monkeypatch
):
    use_claude(home)
    append_config(home, "free_offers:\n  ai_max_searches: 4\n")
    sent = claude_reports(monkeypatch, GEMINI_REPORT)
    known = ai_offer("Claude for Startups", "Anthropic")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = offers_watchers
    [found] = watcher.scan([known])
    assert (found.title, found.url) == ("Gemini API free tier", "https://ai.google.dev/pricing")
    [request] = sent
    assert request["model"] == "claude-opus-5-5"
    assert [tool["name"] for tool in request["tools"]] == ["web_search", "report_offers"]
    assert request["tools"][0]["max_uses"] == 4
    assert "Claude for Startups (Anthropic)" in request["messages"][0]["content"]
    # 20,000 input tokens ($0.08), 2,000 output tokens ($0.04), 3 searches ($0.03).
    month = month_usage(home)
    assert (month.requests, month.tokens.web_searches) == (1, 3)
    assert round(month.cost_usd, 4) == 0.15


def test_the_watcher_s_scan_asks_for_no_fallback_when_the_claude_fallbacks_are_off(
    home, run_fakes, brains, offers_watchers, monkeypatch
):
    use_claude(home)
    config = home / "config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8").replace("fallbacks: true", "fallbacks: false", 1),
        encoding="utf-8",
    )
    sent = claude_reports(monkeypatch, GEMINI_REPORT)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = offers_watchers
    watcher.scan([])
    [request] = sent
    assert "fallbacks" not in request


def test_with_ollama_the_watcher_s_scan_still_asks_claude_but_records_nothing(
    home, run_fakes, brains, offers_watchers, monkeypatch
):
    sent = claude_reports(monkeypatch)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = offers_watchers
    assert watcher.scan([]) == []
    assert len(sent) == 1
    assert not usage_file(home).exists()


def test_offers_games_prints_each_game_with_its_link_and_credits_gamerpower(
    home, monkeypatch, capsys
):
    games = free_games_now()
    reads = use_free_games(monkeypatch, games)
    bad_cheese, portal, sker_ritual = games

    assert cli.main(["offers", "games"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Game gratis sekarang:",
        f"  Bad Cheese di Epic Games Store, sampai {when_text(bad_cheese.ends_at)}",
        f"    {bad_cheese.url}",
        f"  Portal di Steam (biasanya $9.99), sampai {day_text(portal.ends_on)}",
        f"    {portal.url}",
        "Segera gratis:",
        f"  Sker Ritual di Epic Games Store, mulai {when_text(sker_ritual.starts_at)}, "
        f"sampai {when_text(sker_ritual.ends_at)}",
        f"    {sker_ritual.url}",
        "Sumber: GamerPower.com (https://www.gamerpower.com)",
    ]
    assert reads.platforms == [["epic", "steam", "gog"]]


def test_offers_games_from_epic_alone_needs_no_credit(home, monkeypatch, capsys):
    bad_cheese = free_games_now()[0]
    use_free_games(monkeypatch, (bad_cheese,))

    assert cli.main(["offers", "games"]) == 0

    assert "GamerPower" not in capsys.readouterr().out


def test_offers_games_reads_them_with_the_free_offers_off_too(home, monkeypatch, capsys):
    append_config(home, "free_offers:\n  enabled: false\n  games: false\n")
    use_free_games(monkeypatch, ())

    assert cli.main(["offers", "games"]) == 0

    assert capsys.readouterr().out == "Tidak ada game gratis saat ini.\n"


def test_offers_games_that_cannot_be_read_fail_with_the_reason(home, monkeypatch, capsys):
    use_free_games(monkeypatch, OffersError("Daftar game gratis tidak bisa diambil."))

    assert cli.main(["offers", "games"]) == 1

    assert capsys.readouterr().out == "✗ Daftar game gratis tidak bisa diambil.\n"


def test_offers_ai_prints_the_kept_offers_newest_first_with_their_links(home, capsys):
    deadline = date.today() + timedelta(days=60)
    older = ai_offer(
        "Claude for Startups",
        "Anthropic",
        kind="startup_program",
        access="apply",
        eligibility="Startup yang didukung modal ventura mitra Anthropic.",
        summary="Kredit API Claude gratis untuk startup.",
        url="https://www.anthropic.com/startups",
        first_seen=datetime.now().replace(microsecond=0) - timedelta(days=9),
    )
    newer = ai_offer("Gemini API free tier", "Google", kind="free_tier", deadline=deadline)
    keep_ai_offers(home, older)
    scanned = keep_ai_offers(home, newer)

    assert cli.main(["offers", "ai"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        f"Promo AI (pencarian terakhir {when_text(scanned)}):",
        "  Gemini API free tier dari Google: paket gratis, bisa langsung diklaim, sampai "
        f"{day_text(deadline)}",
        "    Kredit gratis.",
        "    Untuk: Siapa saja.",
        f"    {newer.url}",
        "  Claude for Startups dari Anthropic: program startup, perlu mendaftar",
        "    Kredit API Claude gratis untuk startup.",
        "    Untuk: Startup yang didukung modal ventura mitra Anthropic.",
        "    https://www.anthropic.com/startups",
    ]


def test_offers_ai_marks_a_link_off_the_provider_s_own_site_as_unverified(home, capsys):
    keep_ai_offers(
        home, ai_offer("Claude Pro gratis", "Anthropic", url="https://claude-pro-free.example/")
    )

    assert cli.main(["offers", "ai"]) == 0

    assert capsys.readouterr().out.splitlines()[-1] == (
        "    https://claude-pro-free.example/  (belum terverifikasi: bukan situs resmi "
        "penyedia yang dikenal)"
    )


NEVER_SCANNED = (
    "Promo AI belum pernah dicari. Jalankan 'waifu offers ai --scan' (memakai Claude dengan "
    "pencarian web, berbayar)."
)


def test_offers_ai_before_any_scan_says_how_to_run_one(home, capsys):
    assert cli.main(["offers", "ai"]) == 0

    assert capsys.readouterr().out == f"{NEVER_SCANNED}\n"
    assert not offers_file(home).exists()


def test_offers_ai_after_a_scan_that_kept_nothing_says_when_it_ran(home, capsys):
    scanned = keep_ai_offers(home)

    assert cli.main(["offers", "ai"]) == 0

    assert capsys.readouterr().out == (
        f"Belum ada promo AI yang tersimpan (pencarian terakhir {when_text(scanned)}).\n"
    )


def test_offers_prints_the_games_and_then_the_ai_offers(home, monkeypatch, capsys):
    use_free_games(monkeypatch, ())

    assert cli.main(["offers"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Tidak ada game gratis saat ini.",
        "",
        NEVER_SCANNED,
    ]


def test_offers_fails_when_the_games_cannot_be_read_but_still_prints_the_ai_offers(
    home, monkeypatch, capsys
):
    use_free_games(monkeypatch, OffersError("Daftar game gratis tidak bisa diambil."))

    assert cli.main(["offers"]) == 1

    assert capsys.readouterr().out.splitlines()[-1] == NEVER_SCANNED


def test_offers_ai_scan_runs_a_scan_now_keeps_it_and_prints_the_new_offers_and_its_cost(
    home, monkeypatch, capsys
):
    use_claude(home)
    sent = claude_reports(monkeypatch, GEMINI_REPORT, searches=6)
    before = datetime.now().replace(microsecond=0)

    assert cli.main(["offers", "ai", "--scan"]) == 0

    store = OffersStore(offers_file(home))
    [kept] = store.ai_offers()
    assert kept.title == "Gemini API free tier"
    assert store.last_ai_scan() >= before
    assert len(sent) == 1
    assert capsys.readouterr().out.splitlines() == [
        "Mencari promo AI dengan Claude dan pencarian web, bisa beberapa menit...",
        "✓ 1 promo AI baru:",
        "  Gemini API free tier dari Google: paket gratis, bisa langsung diklaim",
        "    Kuota gratis Gemini API setiap hari.",
        "    Untuk: Siapa saja dengan akun Google.",
        "    https://ai.google.dev/pricing",
        # 20,000 input tokens ($0.08), 2,000 output tokens ($0.04), 6 searches ($0.06).
        "Perkiraan biaya pencarian ini: $0.18 (1 permintaan, 6 pencarian web).",
    ]
    assert month_usage(home).tokens.web_searches == 6  # recorded with her other Claude costs


def test_offers_ai_scan_that_finds_only_known_offers_says_nothing_is_new(home, monkeypatch, capsys):
    use_claude(home)
    claude_reports(monkeypatch, GEMINI_REPORT)
    assert cli.main(["offers", "ai", "--scan"]) == 0
    capsys.readouterr()

    assert cli.main(["offers", "ai", "--scan"]) == 0

    assert capsys.readouterr().out.splitlines()[1:] == [
        "✓ Tidak ada promo AI baru. Semua yang tersimpan: 'waifu offers ai'.",
        "Perkiraan biaya pencarian ini: $0.15 (1 permintaan, 3 pencarian web).",
    ]


def test_offers_ai_scan_with_ollama_prints_its_cost_and_says_it_is_not_recorded(
    home, monkeypatch, capsys
):
    claude_reports(monkeypatch)

    assert cli.main(["offers", "ai", "--scan"]) == 0

    assert capsys.readouterr().out.splitlines()[-2:] == [
        "Perkiraan biaya pencarian ini: $0.15 (1 permintaan, 3 pencarian web).",
        "(Tidak dicatat di 'waifu usage': biaya API hanya dicatat dengan brain.backend: claude "
        "dan usage.enabled: true.)",
    ]
    assert not usage_file(home).exists()


def test_offers_ai_scan_that_fails_says_why_and_still_prints_what_it_cost(
    home, monkeypatch, capsys
):
    use_claude(home)

    def refuse(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": OPUS,
                "content": [],
                "stop_reason": "refusal",
                "usage": {"input_tokens": 10_000, "output_tokens": 0},
            },
        )

    use_claude_transport(monkeypatch, refuse)

    assert cli.main(["offers", "ai", "--scan"]) == 1

    assert capsys.readouterr().out.splitlines()[1:] == [
        "✗ Claude menolak mencari promo AI kali ini.",
        "Perkiraan biaya pencarian ini: $0.04 (1 permintaan, 0 pencarian web).",
    ]
    assert OffersStore(offers_file(home)).last_ai_scan() is None  # the next scan tries again


def hold_the_scan_lock(home: Path) -> InstanceLock:
    """Another scan (`waifu run`'s watcher, or a second `--scan`) is running."""
    lock = InstanceLock(home / "data" / "free_offers.json.scan.lock")
    assert lock.acquire()
    return lock


SCAN_RUNNING = (
    "Pencarian promo AI lain sedang berjalan, misalnya oleh 'waifu run'. Coba lagi setelah selesai."
)


def test_offers_ai_scan_while_another_scan_runs_says_so_and_asks_nothing(home, monkeypatch, capsys):
    use_claude(home)
    sent = claude_reports(monkeypatch, GEMINI_REPORT)
    lock = hold_the_scan_lock(home)
    try:
        assert cli.main(["offers", "ai", "--scan"]) == 1
    finally:
        lock.release()

    assert sent == []
    assert capsys.readouterr().out.splitlines()[1:] == [f"✗ {SCAN_RUNNING}"]
    assert cli.main(["offers", "ai", "--scan"]) == 0  # and once it is done, it runs
    assert len(sent) == 1


def test_the_watcher_s_scan_skips_while_another_scan_runs_and_retries_at_the_next_poll(
    home, run_fakes, brains, offers_watchers, monkeypatch
):
    sent = claude_reports(monkeypatch, GEMINI_REPORT)
    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0
    [watcher] = offers_watchers

    lock = hold_the_scan_lock(home)
    try:
        skipped = watcher.scan([])
    finally:
        lock.release()

    assert isinstance(skipped, OffersError)
    assert (str(skipped), skipped.billed) == (SCAN_RUNNING, False)
    assert sent == []


def test_offers_ai_scan_without_claude_credentials_says_so_and_asks_nothing(
    home, monkeypatch, capsys
):
    real_anthropic = anthropic.Anthropic
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(home / "no-profile"))

    def without_credentials(**kwargs: object) -> anthropic.Anthropic:
        unreachable = httpx2.MockTransport(lambda request: pytest.fail("Claude was asked"))
        return real_anthropic(http_client=httpx2.Client(transport=unreachable), **kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", without_credentials)

    assert cli.main(["offers", "ai", "--scan"]) == 1

    assert capsys.readouterr().out.splitlines()[1:] == [
        "✗ API key Claude belum diatur atau tidak valid, jadi promo AI belum bisa dicari."
    ]


def test_offers_ai_scan_names_the_workspace_an_organization_key_needs(home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", " wrkspc_01 ")
    workspaces: list[str | None] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        workspaces.append(request.headers.get("anthropic-workspace-id"))
        return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error"}})

    use_claude_transport(monkeypatch, handler, max_retries=0)

    assert cli.main(["offers", "ai", "--scan"]) == 1

    assert workspaces == ["wrkspc_01"]


@pytest.mark.parametrize("argv", [["offers", "games", "--scan"], ["offers", "--scan"]])
def test_only_the_ai_offers_can_be_scanned(argv):
    with pytest.raises(SystemExit) as exit_info:
        cli.build_parser().parse_args(argv)

    assert exit_info.value.code == 2


def web_search_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Pencarian web" in line]
    return line


@pytest.mark.parametrize(
    ("claude", "enabled", "expected"),
    [
        (
            True,
            True,
            "✓ Pencarian web: aktif (Claude, paling banyak 3 pencarian per permintaan, 18 per "
            "pertanyaan)",
        ),
        (False, True, "! Pencarian web: tidak tersedia dengan Ollama"),
        (True, False, "! Pencarian web mati"),
        (False, False, "! Pencarian web mati"),
    ],
    ids=["claude", "ollama", "claude_with_search_off", "ollama_with_search_off"],
)
def test_check_reports_whether_she_can_search_the_web(
    claude, enabled, expected, home, nothing_installed, capsys
):
    if claude:
        use_claude(home)
    if not enabled:
        append_config(home, "web_search:\n  enabled: false\n")

    cli.main(["check"])

    assert web_search_line(capsys.readouterr().out) == expected


def free_offers_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "promo gratis" in line.lower()]
    return line


def test_check_reports_the_free_offers_before_any_scan_without_creating_the_file(
    home, nothing_installed, capsys
):
    cli.main(["check"])

    assert free_offers_line(capsys.readouterr().out) == (
        "✓ Promo gratis: game Epic, Steam, GOG; promo AI belum pernah dicari"
    )
    assert not offers_file(home).exists()


def test_check_reports_when_the_ai_offers_were_last_searched_for(home, nothing_installed, capsys):
    scanned = keep_ai_offers(home)

    cli.main(["check"])

    assert free_offers_line(capsys.readouterr().out) == (
        f"✓ Promo gratis: game Epic, Steam, GOG; promo AI terakhir dicari {when_text(scanned)}"
    )


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ("  enabled: false\n", "! Promo gratis mati"),
        (
            "  games: false\n  ai: false\n  notices: false\n",
            "✓ Promo gratis: game mati; promo AI mati; tanpa pemberitahuan",
        ),
        (
            "  notices: false\n",
            "✓ Promo gratis: game Epic, Steam, GOG; promo AI belum pernah dicari; tanpa "
            "pemberitahuan dan tanpa pencarian promo AI mingguan",
        ),
        (
            "  game_platforms: [itchio, pc]\n",
            "✓ Promo gratis: game itch.io, PC; promo AI belum pernah dicari",
        ),
    ],
    ids=["off", "parts-off", "notices-off", "platforms"],
)
def test_check_reports_the_free_offers_settings(
    settings, expected, home, nothing_installed, capsys
):
    append_config(home, f"free_offers:\n{settings}")

    cli.main(["check"])

    assert free_offers_line(capsys.readouterr().out) == expected


def test_check_shows_where_a_broken_offers_file_was_moved(home, nothing_installed, capsys):
    offers_file(home).parent.mkdir()
    offers_file(home).write_text("{promo yang rusak", encoding="utf-8")

    cli.main(["check"])

    [moved] = offers_file(home).parent.glob("free_offers.json.corrupt-*")
    line = free_offers_line(capsys.readouterr().out)
    assert line.startswith("! Promo gratis: game Epic, Steam, GOG; promo AI belum pernah dicari")
    assert str(Path("data") / moved.name) in line
    assert moved.read_text(encoding="utf-8") == "{promo yang rusak"


def test_check_fails_an_offers_file_it_cannot_read(home, nothing_installed, capsys):
    offers_file(home).mkdir(parents=True)  # a folder where the file should be

    cli.main(["check"])

    line = free_offers_line(capsys.readouterr().out)
    assert line.startswith(f"✗ File promo gratis {Path('data/free_offers.json')} tidak bisa dibaca")


def test_the_example_web_search_block_is_valid_once_written_out(home, nothing_installed, capsys):
    use_claude(home)
    write_out_example_block(home, "web_search")

    cli.main(["check"])

    assert web_search_line(capsys.readouterr().out).startswith("✓ Pencarian web: aktif")


def test_the_example_free_offers_block_is_valid_once_written_out(
    home, run_fakes, brains, offers_watchers
):
    write_out_example_block(home, "free_offers")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = offers_watchers
    assert watcher.config == FreeOffersConfig()  # the example writes out the defaults


def test_the_example_usage_block_prices_the_web_searches(home):
    write_out_example_block(home, "usage")

    assert cli.load_config(home / "config.yaml").usage.web_search_per_1000_usd == 10.0


# --- redeem codes --------------------------------------------------------------------------------

# From the source's real answer on 2026-10-17.
JADE_60 = ("5S6ZHRWTDNJB", "60 stellar jade")
JADE_30 = (
    "4TKSX77Y58QK",
    "30 stellar jade, three traveler’s guides, five condensed aether, four lost gold "
    "fragments, and 20k credits",
)
STAR_RAIL_GIFT = (
    "STARRAILGIFT",
    "50 stellar jade, 10k credits, two traveler’s guides, and five bottled soda",
)
GENSHIN_GIFT = ("GENSHINGIFT", "50 primogems and three hero’s wit")
CODES_CREDIT = "Sumber kode: hoyo-codes.seria.moe (API komunitas di balik Hoyo Buddy)"


class CodeSource:
    """Stands in for codes.read_codes: hands out `found` and remembers the games it was asked
    for and the HTTP client it was given."""

    def __init__(self, found: dict | CodesError) -> None:
        self.found = found
        self.asked: list[list[str]] = []
        self.clients: list[object] = []

    def __call__(self, games, *, http) -> dict | CodesError:
        self.asked.append(list(games))
        self.clients.append(http)
        return self.found


def use_code_source(monkeypatch, found: dict | CodesError) -> CodeSource:
    source = CodeSource(found)
    monkeypatch.setattr(cli, "read_codes", source)
    return source


def codes_file(home: Path) -> Path:
    return home / "data" / "redeem_codes.json"


def kept_codes(home: Path) -> CodeStore:
    return CodeStore(codes_file(home))


def code_tools(brain: HouseBrain) -> CodeTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, CodeTools)]
    return tools


def test_run_with_the_games_on_watches_for_new_codes_while_she_listens(
    home, run_fakes, brains, games, watchers, code_watchers, monkeypatch
):
    enable_gacha(home)
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = code_watchers
    assert watcher.store.path == Path("data") / "redeem_codes.json"
    assert watcher.hoyolab is games.hoyolab
    assert watcher.config == RedeemCodesConfig()  # the defaults
    assert watcher.games == ["hsr", "genshin", "zzz"]
    assert (watcher.owner_call, watcher.title, watcher.notifier) == ("Master", "Cyrene", notifier)
    assert watcher.listening == [False, True]  # once she is ready to listen
    watcher.announce("Master, ada 1 kode HSR baru: 60 stellar jade. Mau kutukarkan?", "happy")
    assert RecordingAssistant.instances[0].announced == [
        ("Master, ada 1 kode HSR baru: 60 stellar jade. Mau kutukarkan?", "happy")
    ]


def test_the_code_watcher_reads_the_source_for_the_games_it_is_given_over_httpx(
    home, run_fakes, brains, games, watchers, code_watchers, monkeypatch
):
    enable_gacha(home)
    source = use_code_source(monkeypatch, {"hsr": [JADE_60]})

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [watcher] = code_watchers
    assert watcher.read_codes(["hsr"]) == {"hsr": [JADE_60]}
    assert source.asked == [["hsr"]]
    assert isinstance(source.clients[0], httpx.Client)


def test_run_without_a_cookie_still_watches_for_codes(
    home, run_fakes, brains, games, watchers, code_watchers
):
    enable_gacha(home, cookie=None)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert len(code_watchers) == 1  # listing codes needs no cookie


def test_the_code_tools_redeem_through_hoyolab_and_keep_codes_in_the_state_path(
    home, brains, games, monkeypatch
):
    enable_gacha(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    result, is_error = code_tools(brains[0]).run(
        "redeem_codes", {"game": "hsr", "codes": ["ABCDE12345"]}
    )
    assert is_error is False, result
    assert games.hoyolab.redeemed == [("hsr", ["ABCDE12345"])]
    assert [code for code, _ in kept_codes(home).recent("hsr", 5)][0].code == "ABCDE12345"


def test_chat_with_the_games_on_never_watches_for_codes(
    home, brains, games, code_watchers, monkeypatch
):
    enable_gacha(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert code_watchers == []


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_the_redeem_codes_off_there_are_no_code_tools_and_no_watcher(
    argv, home, run_fakes, brains, games, watchers, code_watchers, monkeypatch
):
    enable_gacha(home)
    append_config(home, "redeem_codes:\n  enabled: false\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert not any(isinstance(tools, CodeTools) for tools in brains[0].providers)
    assert code_watchers == []


def test_with_the_code_notices_off_the_watcher_still_keeps_her_answers_about_codes_current(
    home, run_fakes, brains, games, watchers, code_watchers
):
    # The watcher then only reads and keeps the codes: it never tells or redeems them.
    enable_gacha(home)
    append_config(home, "redeem_codes:\n  notices: false\n")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert any(isinstance(tools, CodeTools) for tools in brains[0].providers)
    [watcher] = code_watchers
    assert watcher.config == RedeemCodesConfig(notices=False)
    assert watcher.listening == [False, True]


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_the_code_tools_and_watcher_cover_only_the_configured_games(
    argv, home, run_fakes, brains, games, watchers, code_watchers, monkeypatch
):
    enable_gacha(home)
    play_only(home, "[hsr, zzz]")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert [
        spec.input_schema["properties"]["game"]["enum"] for spec in code_tools(brains[0]).specs()
    ] == [
        ["hsr", "zzz"],
        ["hsr", "zzz"],
    ]
    assert [watcher.games for watcher in code_watchers] == (
        [["hsr", "zzz"]] if argv[0] == "run" else []
    )


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_the_games_off_there_are_no_code_tools_and_no_watcher(
    argv, home, run_fakes, brains, games, watchers, code_watchers, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert not any(isinstance(tools, CodeTools) for tools in brains[0].providers)
    assert code_watchers == []


@pytest.mark.parametrize(
    ("failure", "exit_code"),
    [(SttError("Whisper berhenti."), 1), (KeyboardInterrupt(), 130)],
    ids=["whisper-fails", "ctrl-c"],
)
def test_run_stops_watching_for_codes_however_listening_ends(
    failure, exit_code, home, run_fakes, brains, games, watchers, code_watchers, monkeypatch
):
    def listen_until_it_fails(self, mic, segmenter) -> None:
        self.ran_with = (mic, segmenter)
        raise failure

    enable_gacha(home)
    monkeypatch.setattr(RecordingAssistant, "run", listen_until_it_fails)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == exit_code

    [watcher] = code_watchers
    assert watcher.listening == [False, True]


def test_gacha_codes_reads_the_source_keeps_the_new_codes_and_prints_each_game(
    home, monkeypatch, capsys
):
    three_days_ago = datetime.now().astimezone() - timedelta(days=3)
    store = kept_codes(home)
    old_code = ("OLDCODE123", "10 stellar jade")
    store.add("hsr", [old_code, STAR_RAIL_GIFT, ("EXPIRED123", "")], three_days_ago)
    store.record([RedeemResult("hsr", STAR_RAIL_GIFT[0], "claimed", "Sudah dipakai.")])
    source = use_code_source(
        monkeypatch,
        {"hsr": [JADE_60, JADE_30, old_code], "genshin": [GENSHIN_GIFT], "zzz": []},
    )

    assert cli.main(["gacha", "codes"]) == 0

    assert source.asked == [["hsr", "genshin", "zzz"]]
    # Newest first; EXPIRED123, which the source no longer lists, is not one to redeem.
    assert capsys.readouterr().out.splitlines() == [
        "HSR (Honkai: Star Rail)",
        "  Belum ditukarkan:",
        f"    {JADE_60[0]}  {JADE_60[1]}  (baru)",
        f"    {JADE_30[0]}  {JADE_30[1]}  (baru)",
        f"    OLDCODE123  10 stellar jade  (sejak {three_days_ago:%d-%m-%Y})",
        "  Terakhir dicoba:",
        f"    {STAR_RAIL_GIFT[0]}  sudah pernah ditukarkan  ({STAR_RAIL_GIFT[1]})",
        "Genshin (Genshin Impact)",
        "  Belum ditukarkan:",
        f"    {GENSHIN_GIFT[0]}  {GENSHIN_GIFT[1]}  (baru)",
        "  Terakhir dicoba: tidak ada",
        "ZZZ (Zenless Zone Zero)",
        "  Belum ditukarkan: tidak ada",
        "  Terakhir dicoba: tidak ada",
        CODES_CREDIT,
    ]
    # Kept, and left for a running Cyrene to announce: printing them here tells no phone.
    assert [code.code for code in kept_codes(home).unannounced("hsr")] == [
        JADE_60[0],
        JADE_30[0],
        "OLDCODE123",
    ]


def test_gacha_codes_of_one_game_reads_only_that_game(home, monkeypatch, capsys):
    source = use_code_source(monkeypatch, {"zzz": []})

    assert cli.main(["gacha", "codes", "zzz"]) == 0

    assert source.asked == [["zzz"]]
    assert capsys.readouterr().out.splitlines()[0] == "ZZZ (Zenless Zone Zero)"


def test_gacha_codes_works_with_the_games_and_the_codes_off(home, monkeypatch, capsys):
    append_config(home, "redeem_codes:\n  enabled: false\n")
    use_code_source(monkeypatch, {"hsr": [JADE_60], "genshin": [], "zzz": []})

    assert cli.main(["gacha", "codes"]) == 0

    assert f"    {JADE_60[0]}  {JADE_60[1]}  (baru)" in capsys.readouterr().out.splitlines()


def test_gacha_codes_that_cannot_be_read_show_the_kept_ones_and_fail(home, monkeypatch, capsys):
    kept_codes(home).add("hsr", [JADE_60], datetime.now().astimezone())
    use_code_source(monkeypatch, CodesError("Kode redeem tidak bisa diambil."))

    assert cli.main(["gacha", "codes", "hsr"]) == 1

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "! Kode redeem tidak bisa diambil. Ini kode yang tersimpan."
    assert lines[3].startswith(f"    {JADE_60[0]}  {JADE_60[1]}  (sejak ")


def test_gacha_codes_says_which_game_the_source_left_out_and_fails(home, monkeypatch, capsys):
    use_code_source(monkeypatch, {"hsr": [JADE_60]})

    assert cli.main(["gacha", "codes"]) == 1

    out = capsys.readouterr().out.splitlines()
    assert out[:2] == [
        "! Kode Genshin tidak terbaca dari hoyo-codes.seria.moe; ini yang tersimpan.",
        "! Kode ZZZ tidak terbaca dari hoyo-codes.seria.moe; ini yang tersimpan.",
    ]
    assert f"    {JADE_60[0]}  {JADE_60[1]}  (baru)" in out


def test_gacha_codes_redeem_needs_a_game(home, games, capsys):
    assert cli.main(["gacha", "codes", "--redeem"]) == 2

    assert capsys.readouterr().out == (
        "✗ --redeem butuh satu game, misalnya: waifu gacha codes hsr --redeem\n"
    )
    assert games.hoyolab.redeemed == []


def test_gacha_codes_redeem_redeems_that_game_s_codes_and_prints_each_result(
    home, games, monkeypatch, capsys
):
    games.hoyolab.redeem_outcomes = {JADE_30[0]: "expired"}
    use_code_source(monkeypatch, {"hsr": [JADE_60, JADE_30]})
    kept_codes(home).add("genshin", [GENSHIN_GIFT], datetime.now().astimezone())

    assert cli.main(["gacha", "codes", "hsr", "--redeem"]) == 0

    assert games.hoyolab.redeemed == [("hsr", [JADE_60[0], JADE_30[0]])]
    assert capsys.readouterr().out.splitlines() == [
        "Menukarkan 2 kode HSR di HoYoLAB satu per satu, sekitar 6 detik per kode...",
        f"✓ Kode {JADE_60[0]} berhasil ditukarkan. Hadiahnya ada di surat dalam game HSR.",
        f"! Kode {JADE_30[0]} sudah kedaluwarsa.",
    ]
    store = kept_codes(home)
    assert [(code.code, outcome) for code, outcome in store.recent("hsr", 5)] == [
        (JADE_60[0], "redeemed"),
        (JADE_30[0], "expired"),
    ]
    assert store.unannounced("hsr") == []
    assert [code.code for code in store.unredeemed("genshin")] == [GENSHIN_GIFT[0]]


def test_gacha_codes_redeem_fails_when_a_code_is_left_to_redeem_again(
    home, games, monkeypatch, capsys
):
    games.hoyolab.redeem_outcomes = {JADE_60[0]: "cooldown"}
    use_code_source(monkeypatch, {"hsr": [JADE_60]})

    assert cli.main(["gacha", "codes", "hsr", "--redeem"]) == 1

    assert capsys.readouterr().out.splitlines()[-1] == (
        f"✗ Kode {JADE_60[0]} belum bisa ditukarkan: HoYoLAB sedang membatasi penukaran kode. "
        "Coba lagi sebentar lagi."
    )
    assert [code.code for code in kept_codes(home).unredeemed("hsr")] == [JADE_60[0]]
    assert kept_codes(home).unannounced("hsr") == []  # Master saw it here


def test_gacha_codes_redeem_prints_each_result_even_when_they_cannot_be_saved(
    home, games, monkeypatch, capsys
):
    # HoYoLAB has redeemed them by then: only the bookkeeping may be lost.
    use_code_source(monkeypatch, {"hsr": [JADE_60]})

    def file_in_use(self, results) -> None:
        raise PermissionError("[WinError 5] Access is denied")

    monkeypatch.setattr(CodeStore, "record", file_in_use)

    assert cli.main(["gacha", "codes", "hsr", "--redeem"]) == 0

    assert capsys.readouterr().out.splitlines()[1:] == [
        f"✓ Kode {JADE_60[0]} berhasil ditukarkan. Hadiahnya ada di surat dalam game HSR.",
        f"! Hasil penukaran tidak bisa disimpan di {Path('data') / 'redeem_codes.json'}: "
        "[WinError 5] Access is denied",
    ]


def test_gacha_codes_redeem_without_a_cookie_says_how_to_set_it(home, games, monkeypatch, capsys):
    games.hoyolab.redeem_failure = GachaError("no_cookie", NO_COOKIE)
    use_code_source(monkeypatch, {"hsr": [JADE_60]})

    assert cli.main(["gacha", "codes", "hsr", "--redeem"]) == 1

    assert capsys.readouterr().out.splitlines()[-1] == f"✗ {NO_COOKIE}"
    assert [code.code for code in kept_codes(home).unredeemed("hsr")] == [JADE_60[0]]


def test_gacha_codes_redeem_with_nothing_to_redeem_asks_no_one(home, games, monkeypatch, capsys):
    use_code_source(monkeypatch, {"hsr": []})

    assert cli.main(["gacha", "codes", "hsr", "--redeem"]) == 0

    assert capsys.readouterr().out == "Tidak ada kode HSR yang belum ditukarkan.\n"
    assert games.hoyolab.redeemed == []


def test_gacha_codes_redeem_redeems_ten_codes_at_a_time(home, games, monkeypatch, capsys):
    codes = [(f"CODE{n:02d}AAA", "10 stellar jade") for n in range(12)]
    use_code_source(monkeypatch, {"hsr": codes})

    assert cli.main(["gacha", "codes", "hsr", "--redeem"]) == 0

    assert games.hoyolab.redeemed == [("hsr", [code for code, _ in codes[:10]])]
    assert capsys.readouterr().out.splitlines()[-1] == (
        "! Masih ada 2 kode HSR; jalankan perintah ini lagi."
    )


def test_gacha_codes_redeem_that_cannot_read_the_source_still_redeems_the_kept_codes(
    home, games, monkeypatch, capsys
):
    kept_codes(home).add("hsr", [JADE_60], datetime.now().astimezone())
    use_code_source(monkeypatch, CodesError("Kode redeem tidak bisa diambil."))

    assert cli.main(["gacha", "codes", "hsr", "--redeem"]) == 1

    assert games.hoyolab.redeemed == [("hsr", [JADE_60[0]])]
    assert capsys.readouterr().out.splitlines()[0] == (
        "! Kode redeem tidak bisa diambil. Ini kode yang tersimpan."
    )


def redeem_codes_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Kode redeem" in line]
    return line


def test_check_reports_the_redeem_codes_off_with_the_games(home, nothing_installed, capsys):
    cli.main(["check"])

    assert redeem_codes_line(capsys.readouterr().out) == (
        "! Kode redeem mati (gacha.enabled: false); 'waifu gacha codes' tetap bisa dipakai"
    )


def test_check_reports_the_redeem_codes_off(home, nothing_installed, capsys):
    enable_gacha(home)
    append_config(home, "redeem_codes:\n  enabled: false\n")

    cli.main(["check"])

    assert redeem_codes_line(capsys.readouterr().out) == (
        "! Kode redeem mati (redeem_codes.enabled: false); 'waifu gacha codes' tetap bisa dipakai"
    )


@pytest.mark.parametrize(
    ("cookie", "settings", "line"),
    [
        (
            GACHA_COOKIE,
            "",
            "✓ Kode redeem: aktif, cek tiap 6 jam, ditukarkan hanya kalau diminta; cookie "
            "memuat cookie_token_v2",
        ),
        (
            GACHA_COOKIE,
            "  auto_redeem: true\n  poll_hours: 3\n",
            "✓ Kode redeem: aktif, cek tiap 3 jam, kode baru langsung ditukarkan (auto_redeem); "
            "cookie memuat cookie_token_v2",
        ),
        (
            GACHA_COOKIE,
            "  auto_redeem: true\n  notices: false\n",
            "✓ Kode redeem: aktif, cek tiap 6 jam tanpa pemberitahuan, ditukarkan hanya kalau "
            "diminta; cookie memuat cookie_token_v2",
        ),
        (
            "ltuid_v2=1; ltoken_v2=v2_SECRETCOOKIE",
            "",
            "! Kode redeem: aktif, cek tiap 6 jam, ditukarkan hanya kalau diminta; cookie "
            "HoYoLAB tanpa cookie_token_v2, jadi HoYoLAB akan menolak penukarannya",
        ),
        (
            None,
            "",
            "! Kode redeem: aktif, cek tiap 6 jam, ditukarkan hanya kalau diminta; cookie "
            "HoYoLAB belum diisi (gacha.cookie), jadi kode hanya diberitahukan",
        ),
        (
            None,
            "  notices: false\n",
            "! Kode redeem: aktif, cek tiap 6 jam tanpa pemberitahuan, ditukarkan hanya kalau "
            "diminta; cookie HoYoLAB belum diisi (gacha.cookie), jadi penukaran gagal sampai "
            "cookie diisi",
        ),
        (
            None,
            "  auto_redeem: true\n",
            "! Kode redeem: aktif, cek tiap 6 jam, kode baru langsung ditukarkan (auto_redeem); "
            "cookie HoYoLAB belum diisi (gacha.cookie), jadi kode hanya diberitahukan dan belum "
            "ditukarkan otomatis",
        ),
    ],
    ids=[
        "defaults",
        "auto-redeem",
        "no-notices",
        "no-cookie-token",
        "no-cookie",
        "no-cookie-no-notices",
        "no-cookie-auto-redeem",
    ],
)
def test_check_reports_how_she_handles_codes_and_whether_the_cookie_can_redeem_them(
    cookie, settings, line, home, nothing_installed, capsys
):
    enable_gacha(home, cookie=cookie)
    if settings:
        append_config(home, f"redeem_codes:\n{settings}")

    cli.main(["check"])

    out = capsys.readouterr().out
    assert redeem_codes_line(out) == line
    for secret in ("SECRETCOOKIE", "SECRETTOKEN"):
        assert secret not in out


def test_the_example_redeem_codes_block_is_valid_once_written_out(home):
    write_out_example_block(home, "redeem_codes")

    assert cli.load_config(home / "config.yaml").redeem_codes == RedeemCodesConfig()


# --- laporan game mingguan -----------------------------------------------------------------------

SERVER_TIME = timezone(timedelta(hours=8))


def weekly_report_tools(brain: HouseBrain) -> WeeklyReportTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, WeeklyReportTools)]
    return tools


def save_hsr_week(home: Path) -> None:
    """Master's HSR history in data/gacha: nine days ago a lost 50/50 to Himeko, two days ago
    the guaranteed Castorice, and five pulls since."""

    def pulled(record_id: int, days_ago: int, name: str = "Arrows", rarity: int = 3):
        moment = datetime.now(SERVER_TIME) - timedelta(days=days_ago)
        when = f"{moment:%Y-%m-%d %H:%M:%S}"
        return GachaRecord(record_id, 800000001, 11, name, rarity, "Character", when)

    records = [pulled(i, 9) for i in range(1, 60)]
    records.append(pulled(60, 9, "Himeko", 5))
    records += [pulled(i, 2) for i in range(61, 90)]
    records.append(pulled(90, 2, "Castorice", 5))
    records += [pulled(i, 1) for i in range(91, 96)]
    GachaStore(home / "data" / "gacha").save(GAMES["hsr"], 800000001, records, complete=True)


def hsr_season(mode: str, **known) -> HsrEndgame:
    """This season of an HSR mode, not played, without an end, unless `known` says more."""
    values = {"season": None, "ends_at": None, "has_data": False, "stars": 0, "extra_stars": 0}
    values |= {"deepest_floor": None, "battles": None, "floors": ()} | known
    return HsrEndgame(mode, **values)


def hsr_week_on_hoyolab(game_data: "FakeGameDataClient") -> dict[str, datetime]:
    """This HSR season and calendar on HoYoLAB; returns the ends it gave."""
    ends = {
        "chaos": from_now(days=1, hours=8),
        "fiction": from_now(days=10),
        "warp": from_now(days=2, hours=19),
        "fissure": from_now(days=1, hours=8),
    }
    game_data.hsr_modes = {mode: hsr_season(mode) for mode in ENDGAME_MODES}
    game_data.hsr_modes["memory_of_chaos"] = hsr_season(
        "memory_of_chaos",
        has_data=True,
        stars=30,
        extra_stars=2,
        deepest_floor="Stage 12",
        ends_at=ends["chaos"],
    )
    game_data.hsr_modes["pure_fiction"] = hsr_season("pure_fiction", ends_at=ends["fiction"])
    game_data.hsr_events = HsrCalendar(
        "3.7",
        (
            HsrWarp(
                "character",
                "Bloom of Death",
                ("Castorice",),
                ("Pela", "Lynx"),
                from_now(days=-10),
                ends["warp"],
            ),
            HsrWarp("light_cone", "Later", ("Song",), (), from_now(days=-1), from_now(days=9)),
        ),
        (HsrEvent("Planar Fissure", None, ends["fissure"], 3, 7, False),),
        (),
    )
    return ends


def every_genshin_and_zzz_mode_unplayed(game_data: "FakeGameDataClient") -> None:
    game_data.modes = {
        mode: endgame(mode, has_data=False)
        for game in ("genshin", "zzz")
        for mode in GAME_MODES[game]
    }


def test_run_gives_the_weekly_report_once_a_week_while_she_listens_and_pushes_it_as_a_game(
    home, run_fakes, brains, games, watchers, weekly_report_timers, monkeypatch
):
    enable_gacha(home)
    enable_notify(home)
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert [type(tools) for tools in brains[0].providers][:4] == [
        GachaTools,
        GameDataTools,
        CodeTools,
        WeeklyReportTools,
    ]
    [timer] = weekly_report_timers
    assert timer.config == WeeklyReportConfig()
    assert timer.listening == [False, True]
    timer.tick_at(datetime(2026, 10, 18, 19, 0))  # a Sunday
    timer.tick_at(datetime(2026, 10, 18, 21, 0))  # she already gave it today
    assistant = RecordingAssistant.instances[0]
    [(prompt, on_reply, required_tool)] = assistant.asked
    assert prompt == WEEKLY_REPORT_PROMPT
    assert required_tool == "weekly_report"  # a turn that never read the week is never said
    # In a new session, so no figure from an earlier finance answer reaches the report.
    assert assistant.new_sessions == [True]
    on_reply("Minggu ini Master dapat Castorice!")
    assert notifier.sent == [("Cyrene", "Minggu ini Master dapat Castorice!", ("video_game",))]


def test_run_gives_the_weekly_report_on_the_configured_day_and_remembers_it_there(
    home, run_fakes, games, watchers, weekly_report_timers
):
    enable_gacha(home)
    append_config(
        home,
        "weekly_report:\n  day: saturday\n  time: '20:30'\n  state_path: laporan/minggu.json\n",
    )

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    [timer] = weekly_report_timers
    timer.tick_at(datetime(2026, 10, 18, 21, 0))  # Sunday
    timer.tick_at(datetime(2026, 10, 24, 20, 30))  # Saturday
    assert [prompt for prompt, _, _ in RecordingAssistant.instances[0].asked] == [
        WEEKLY_REPORT_PROMPT
    ]
    saved = json.loads((home / "laporan" / "minggu.json").read_text(encoding="utf-8"))
    assert saved["last_date"] == "2026-10-24"


@pytest.mark.parametrize(
    ("push", "notify"), [(False, True), (True, False)], ids=["push_off", "notify_off"]
)
def test_run_gives_the_weekly_report_without_a_push_when_push_or_notifications_are_off(
    push, notify, home, run_fakes, games, watchers, weekly_report_timers, monkeypatch
):
    enable_gacha(home)
    if notify:
        enable_notify(home)
    append_config(home, f"weekly_report:\n  push: {str(push).lower()}\n")
    notifier = FakeNotifier()
    use_notifier(monkeypatch, notifier)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    weekly_report_timers[0].request(WEEKLY_REPORT_PROMPT)
    [(_, on_reply, _)] = RecordingAssistant.instances[0].asked
    assert on_reply is None


def test_chat_gives_the_brain_the_weekly_report_tool_but_never_reports_by_herself(
    home, brains, games, weekly_report_timers, monkeypatch
):
    enable_gacha(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert any(isinstance(tools, WeeklyReportTools) for tools in brains[0].providers)
    assert weekly_report_timers == []


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
@pytest.mark.parametrize(
    "settings",
    ["weekly_report:\n  enabled: false\n", None],
    ids=["weekly_report_off", "games_off"],
)
def test_without_the_weekly_report_or_the_games_there_is_neither_the_tool_nor_the_timer(
    settings, argv, home, run_fakes, brains, games, watchers, weekly_report_timers, monkeypatch
):
    if settings is not None:
        enable_gacha(home)
        append_config(home, settings)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert not any(isinstance(tools, WeeklyReportTools) for tools in brains[0].providers)
    assert weekly_report_timers == []


def test_the_weekly_report_tool_reads_the_history_hoyolab_and_codes_of_the_configured_games(
    home, brains, game_data, games, monkeypatch
):
    enable_gacha(home)
    play_only(home, "[hsr, zzz]")
    save_hsr_week(home)
    hsr_week_on_hoyolab(game_data)
    every_genshin_and_zzz_mode_unplayed(game_data)
    CodeStore(home / "data" / "redeem_codes.json").add(
        "hsr", [("STARRAILGIFT", "50 Stellar Jade")], datetime.now().astimezone()
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))
    assert cli.main(["chat", "--dry-run"]) == 0

    result, is_error = weekly_report_tools(brains[0]).run("weekly_report", {})

    assert is_error is False
    hsr, zzz = json.loads(result)["games"]
    assert (hsr["game"], zzz["game"]) == ("hsr", "zzz")
    assert hsr["pulls_this_week"]["count"] == 35
    assert hsr["endgame"][0]["stars"] == 30
    assert [banner["name"] for banner in hsr["ending_soon"]["banners"]] == ["Bloom of Death"]
    assert (hsr["unredeemed_codes"], zzz["unredeemed_codes"]) == (["STARRAILGIFT"], [])
    assert zzz["pulls_this_week"] is None
    assert games.history.asked == [("hsr", False)]  # only a stored history is refreshed


def test_the_weekly_report_tool_leaves_the_codes_out_when_they_are_off(
    home, brains, game_data, games, monkeypatch
):
    enable_gacha(home)
    play_only(home, "[hsr]")
    save_hsr_week(home)
    hsr_week_on_hoyolab(game_data)
    append_config(home, "redeem_codes:\n  enabled: false\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))
    assert cli.main(["chat", "--dry-run"]) == 0

    result, _ = weekly_report_tools(brains[0]).run("weekly_report", {})

    [hsr] = json.loads(result)["games"]
    assert "unredeemed_codes" not in hsr


def test_gacha_report_prints_the_week_of_each_game_without_the_brain_also_with_the_games_off(
    home, game_data, games, capsys
):
    play_only(home, "[hsr]")
    save_hsr_week(home)
    ends = hsr_week_on_hoyolab(game_data)

    assert cli.main(["gacha", "report"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Membaca riwayat pull dan HoYoLAB, tunggu sebentar..."
    since = datetime.now() - timedelta(days=7, hours=1)  # pulls are listed about an hour late
    assert lines[1].startswith(
        f"Laporan game 7 hari terakhir, sejak {WEEKDAYS_ID[since.weekday()]}, "
    )
    assert lines[2:] == [
        "HSR (Honkai: Star Rail)",
        "  Pull minggu ini: 35 (Character Event Warp 35)",
        "  5★ minggu ini: Castorice (30 pull)",
        "  Character Event Warp: pity 5/90, berikutnya 50/50, total 95 pull",
        "  Memory of Chaos: 30 bintang (+2 ekstra), lantai terdalam Stage 12, "
        f"segera berakhir {local(ends['chaos'])} (1 hari 8 jam lagi)",
        f"  Pure Fiction: belum dimainkan musim ini, berakhir {local(ends['fiction'])} "
        "(10 hari lagi)",
        "  Apocalyptic Shadow: belum dimainkan musim ini",
        "  Anomaly Arbitration: belum dimainkan musim ini",
        "  Berakhir dalam 7 hari:",
        "    Bloom of Death (karakter): 5★ Castorice; 4★ Pela, Lynx; "
        f"berakhir {local(ends['warp'])} (2 hari 19 jam lagi)",
        f"    Planar Fissure: 3/7, berakhir {local(ends['fissure'])} (1 hari 8 jam lagi)",
        # With the games off no watcher keeps the codes fresh, so the stored list says so.
        "  Kode belum ditukarkan (daftar tersimpan, perbarui dengan 'waifu gacha codes'): "
        "tidak ada",
        "  Jam main minggu ini: belum main",
        "Catatan: HoYoverse mencatat pull sekitar satu jam setelahnya, jadi pull barusan "
        "mungkin belum ada.",
    ]
    assert games.history.asked == [("hsr", False)]


def test_gacha_report_counts_from_an_hour_before_last_weeks_report_slot_on_the_report_day(
    home, game_data, games, capsys
):
    # As her own report does, so the week `waifu gacha report` prints is the one she told.
    today = datetime.now()
    report_day = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    play_only(home, "[hsr]")
    append_config(home, f"weekly_report:\n  day: {report_day[today.weekday()]}\n  time: '00:00'\n")
    save_hsr_week(home)
    hsr_week_on_hoyolab(game_data)

    assert cli.main(["gacha", "report"]) == 0

    since = datetime.combine(today.date(), datetime.min.time()) - timedelta(days=7, hours=1)
    assert capsys.readouterr().out.splitlines()[1] == (
        f"Laporan game 7 hari terakhir, sejak {WEEKDAYS_ID[since.weekday()]}, "
        f"{since:%d-%m-%Y %H:%M}"
    )


def test_gacha_report_prints_what_it_could_read_and_exits_1_when_a_part_failed(
    home, game_data, games, capsys
):
    save_hsr_week(home)
    hsr_week_on_hoyolab(game_data)
    every_genshin_and_zzz_mode_unplayed(game_data)
    game_data.data_failure = GachaError("network", "HoYoLAB tidak bisa dihubungi.")
    stale = HistoryReport(
        GAMES["hsr"], False, "Buka riwayat warp di HSR sekali.", True, (), (), 0, None
    )
    games.history = FakeGameHistory(stale)

    assert cli.main(["gacha", "report"]) == 1

    out = capsys.readouterr().out
    hsr, rest = out.split("Genshin (Genshin Impact)\n")
    genshin, zzz = rest.split("ZZZ (Zenless Zone Zero)\n")
    assert (
        "  ! Riwayat HSR terbaru tidak terbaca, jadi angkanya dari riwayat tersimpan: Buka "
        "riwayat warp di HSR sekali.\n  Pull minggu ini: 35 (Character Event Warp 35)\n"
    ) in hsr
    assert "  Berakhir dalam 7 hari:\n" in hsr
    assert genshin.splitlines()[0] == (
        "  ✗ Belum ada riwayat Genshin yang tersimpan; jalankan "
        "'waifu gacha history genshin --full' sekali."
    )
    assert "  Spiral Abyss: belum dimainkan musim ini" in genshin.splitlines()
    assert "  ✗ Kalender event Genshin tidak terbaca: HoYoLAB tidak bisa dihubungi." in (
        genshin.splitlines()
    )
    assert "  ✗ Kalender event ZZZ tidak terbaca: HoYoLAB tidak bisa dihubungi." in (
        zzz.splitlines()
    )


def test_gacha_report_shows_this_weeks_s_ranks_codes_and_an_unreadable_endgame_mode(
    home, game_data, games, capsys
):
    play_only(home, "[zzz]")
    moment = datetime.now(SERVER_TIME) - timedelta(days=1)
    when = f"{moment:%Y-%m-%d %H:%M:%S}"
    records = [GachaRecord(i, 1300000003, 2, "Lycaon", 3, "Agents", when) for i in range(1, 10)]
    records.append(GachaRecord(10, 1300000003, 2, "Ellen", 4, "Agents", when))
    GachaStore(home / "data" / "gacha").save(GAMES["zzz"], 1300000003, records, complete=True)
    every_genshin_and_zzz_mode_unplayed(game_data)
    game_data.modes["deadly_assault"] = GachaError("other", "Deadly Assault bermasalah.")
    CodeStore(home / "data" / "redeem_codes.json").add(
        "zzz", [("ZENLESSGIFT", "60 Polychrome")], datetime.now().astimezone()
    )

    assert cli.main(["gacha", "report"]) == 1

    lines = capsys.readouterr().out.splitlines()
    assert "  S-rank minggu ini: Ellen (10 pull, menang 50/50)" in lines
    assert "  Shiyu Defense: belum dimainkan musim ini" in lines
    assert "  ✗ Deadly Assault tidak terbaca: Deadly Assault bermasalah." in lines
    assert "  Berakhir dalam 7 hari: tidak ada" in lines
    assert (
        "  Kode belum ditukarkan (daftar tersimpan, perbarui dengan 'waifu gacha codes'): "
        "ZENLESSGIFT"
    ) in lines


def test_gacha_report_tells_the_codes_plainly_while_waifu_run_keeps_them_fresh(
    home, game_data, games, capsys
):
    enable_gacha(home)
    play_only(home, "[hsr]")
    save_hsr_week(home)
    hsr_week_on_hoyolab(game_data)
    CodeStore(home / "data" / "redeem_codes.json").add(
        "hsr", [("STARRAILGIFT", "50 Stellar Jade")], datetime.now().astimezone()
    )

    assert cli.main(["gacha", "report"]) == 0

    assert "  Kode belum ditukarkan: STARRAILGIFT" in capsys.readouterr().out.splitlines()


def test_gacha_report_never_prints_what_was_spent(home, game_data, games, capsys):
    play_only(home, "[hsr]")
    save_hsr_week(home)
    hsr_week_on_hoyolab(game_data)

    cli.main(["gacha", "report"])

    out = capsys.readouterr().out
    for word in ("Stellar Jade", "160", "habis dipakai", "top up"):
        assert word not in out


def test_gacha_help_offers_the_weekly_report(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "300")
    monkeypatch.setenv("PYTHON_COLORS", "0")

    with pytest.raises(SystemExit):
        cli.main(["gacha", "--help"])

    out = capsys.readouterr().out
    assert "report" in out
    assert "laporan mingguan" in out


def weekly_report_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Laporan game mingguan" in line]
    return line


def test_check_reports_the_weekly_report_off_with_the_games(home, nothing_installed, capsys):
    cli.main(["check"])

    assert weekly_report_line(capsys.readouterr().out) == (
        "! Laporan game mingguan mati (gacha.enabled: false); 'waifu gacha report' tetap bisa "
        "dipakai"
    )


def test_check_reports_the_weekly_report_off(home, nothing_installed, capsys):
    enable_gacha(home)
    append_config(home, "weekly_report:\n  enabled: false\n")

    cli.main(["check"])

    assert weekly_report_line(capsys.readouterr().out) == (
        "! Laporan game mingguan mati (weekly_report.enabled: false); 'waifu gacha report' "
        "tetap bisa dipakai"
    )


@pytest.mark.parametrize(
    ("cookie", "notify", "settings", "line"),
    [
        (GACHA_COOKIE, False, "", "✓ Laporan game mingguan: tiap Minggu jam 19:00"),
        (GACHA_COOKIE, True, "", "✓ Laporan game mingguan: tiap Minggu jam 19:00, juga ke HP"),
        (
            GACHA_COOKIE,
            True,
            "  day: saturday\n  time: '20:30'\n  push: false\n",
            "✓ Laporan game mingguan: tiap Sabtu jam 20:30",
        ),
        (
            None,
            False,
            "",
            "! Laporan game mingguan: tiap Minggu jam 19:00; cookie HoYoLAB belum diisi "
            "(gacha.cookie), jadi endgame dan event tidak terbaca",
        ),
    ],
    ids=["defaults", "pushed", "saturday", "no-cookie"],
)
def test_check_reports_when_she_gives_the_weekly_report(
    cookie, notify, settings, line, home, nothing_installed, capsys
):
    enable_gacha(home, cookie=cookie)
    if notify:
        enable_notify(home)
    if settings:
        append_config(home, f"weekly_report:\n{settings}")

    cli.main(["check"])

    out = capsys.readouterr().out
    assert weekly_report_line(out) == line
    for secret in ("SECRETCOOKIE", "SECRETTOKEN"):
        assert secret not in out


def test_check_reports_the_weekly_report_right_after_the_redeem_codes(
    home, nothing_installed, capsys
):
    cli.main(["check"])

    lines = capsys.readouterr().out.splitlines()
    codes = next(index for index, line in enumerate(lines) if "Kode redeem" in line)
    assert "Laporan game mingguan" in lines[codes + 1]


# --- to-do list ----------------------------------------------------------------------------------

# Due dates whose weekday never changes: one long past (overdue whenever the test runs), one far
# ahead.
LONG_AGO = date(2020, 1, 6)  # a Monday
FAR_AHEAD = date(2099, 10, 16)  # a Friday


def todo_file(home: Path) -> Path:
    """Where the example config keeps Master's tasks, inside the test's working directory."""
    return home / "data" / "todo.json"


def todo_store(path: Path) -> TodoStore:
    """The tasks in `path` with the default limits, on the wall clock like the commands."""
    return TodoStore(path, max_tasks=100, max_chars=200, keep_done_days=7)


def disable_todo(home: Path) -> None:
    append_config(home, "todo:\n  enabled: false\n")


def todo_tools(brain: HouseBrain) -> TodoTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, TodoTools)]
    return tools


def test_todo_outside_tmp_path_is_refused_so_no_test_touches_the_owner_s_tasks():
    with pytest.raises(AssertionError, match="TodoStore outside tmp_path"):
        cli.TodoStore(ROOT / "data" / "todo.json", max_tasks=100, max_chars=200, keep_done_days=7)


def test_run_gives_the_brain_her_to_do_tools_and_the_tasks_due_today_in_the_briefing(
    home, run_fakes, brains
):
    write_config(home, "todo:\n  path: tugas/daftar.json\n  max_chars: 20\n")

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    brain = brains[0]
    assert [type(tools) for tools in brain.providers] == [
        MemoryTools,
        ScheduleTools,
        TodoTools,
        FocusTools,
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
    tools = todo_tools(brain)
    today = date.today()
    assert tools.run("add_task", {"text": "Kerjakan laporan", "due": f"{today}"})[1] is False
    saved = todo_store(home / "tugas" / "daftar.json").open_tasks()  # a dry run keeps real tasks
    assert [(task.text, task.due) for task in saved] == [("Kerjakan laporan", today)]
    assert daily_briefing(brain)["tasks"] == {
        "due_today": [{"id": "t1", "text": "Kerjakan laporan"}],
        "overdue": [],
        "open_count": 1,
    }
    result, is_error = tools.run("add_task", {"text": "Beli susu, roti, dan telur"})
    assert is_error and "20 karakter" in result


@pytest.mark.parametrize("claude", [False, True], ids=["ollama", "claude"])
def test_chat_gives_the_brain_the_tasks_saved_in_data_todo_json_with_either_backend(
    claude, home, brains, monkeypatch
):
    if claude:
        use_claude(home)
    todo_store(todo_file(home)).add("Beli susu", LONG_AGO)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        MemoryTools,
        ScheduleTools,
        TodoTools,
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
        *([ScreenTools, UsageTools] if claude else []),
    ]
    listed = json.loads(todo_tools(brains[0]).run("list_tasks", {})[0])
    assert listed["open"] == [
        {"id": "t1", "text": "Beli susu", "due": "2020-01-06", "overdue": True}
    ]
    assert daily_briefing(brains[0])["tasks"]["overdue"] == [
        {"id": "t1", "text": "Beli susu", "due": "2020-01-06"}
    ]


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_the_todo_list_off_the_brain_gets_neither_the_tools_nor_the_tasks(
    argv, home, run_fakes, brains, monkeypatch
):
    disable_todo(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        MemoryTools,
        ScheduleTools,
        *([FocusTools] if argv[0] == "run" else []),
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
    ]
    assert "tasks" not in daily_briefing(brains[0])
    assert not todo_file(home).exists()


@pytest.mark.parametrize("argv", [["todo"], ["todo", "list"]])
def test_todo_without_tasks_says_so_and_creates_no_file(argv, home, capsys):
    assert cli.main(argv) == 0

    assert capsys.readouterr().out == "Tidak ada tugas terbuka.\n"
    assert not todo_file(home).exists()


def test_todo_lists_the_open_tasks_overdue_first_then_by_due_date_then_the_rest(home, capsys):
    store = todo_store(todo_file(home))
    store.add("beli susu")
    store.add("kerjakan laporan", FAR_AHEAD)
    store.add("bayar listrik", LONG_AGO)

    assert cli.main(["todo"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "ID  TENGGAT                       TUGAS",
        "t3  Senin 06-01-2020 (terlambat)  bayar listrik",
        "t2  Jumat 16-10-2099              kerjakan laporan",
        "t1  -                             beli susu",
    ]


def test_todo_marks_the_tasks_due_today(home, capsys):
    today = date.today()
    todo_store(todo_file(home)).add("kirim tugas kuliah", today)

    assert cli.main(["todo", "list"]) == 0

    [_, line] = capsys.readouterr().out.splitlines()
    assert line == f"t1  {day_text(today)} (hari ini)  kirim tugas kuliah"


def test_todo_list_done_also_lists_the_tasks_finished_lately(home, capsys):
    store = todo_store(todo_file(home))
    store.add("beli susu")
    store.add("bayar listrik")
    store.complete("t1")

    assert cli.main(["todo", "list", "--done"]) == 0

    today = day_text(date.today())
    assert capsys.readouterr().out.splitlines() == [
        "ID  TENGGAT  TUGAS",
        "t2  -        bayar listrik",
        "",
        "Sudah selesai:",
        f"ID  {'SELESAI'.ljust(len(today))}  TUGAS",
        f"t1  {today}  beli susu",
    ]


def test_todo_keeps_the_finished_tasks_as_many_days_as_the_config_says(home, capsys):
    append_config(home, "todo:\n  keep_done_days: 0\n")
    assert cli.main(["todo", "add", "beli", "susu"]) == 0
    assert cli.main(["todo", "done", "t1"]) == 0
    capsys.readouterr()

    assert cli.main(["todo", "list", "--done"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Tidak ada tugas terbuka.",
        "",
        "Belum ada tugas yang selesai.",
    ]


def test_todo_list_done_without_tasks_says_so_twice(home, capsys):
    assert cli.main(["todo", "list", "--done"]) == 0

    assert capsys.readouterr().out.splitlines() == [
        "Tidak ada tugas terbuka.",
        "",
        "Belum ada tugas yang selesai.",
    ]


def test_todo_add_saves_the_words_as_one_task_also_with_the_list_off(home, capsys):
    disable_todo(home)

    assert cli.main(["todo", "add", "beli", "susu"]) == 0

    assert capsys.readouterr().out == "✓ Tugas t1 ditambahkan: beli susu\n"
    [task] = todo_store(todo_file(home)).open_tasks()
    assert (task.text, task.due) == ("beli susu", None)


def test_todo_add_with_a_due_date_saves_it_and_names_the_day(home, capsys):
    assert cli.main(["todo", "add", "kerjakan", "laporan", "--due", "2099-10-16"]) == 0

    out = capsys.readouterr().out
    assert out == "✓ Tugas t1 ditambahkan: kerjakan laporan (tenggat Jumat 16-10-2099)\n"
    [task] = todo_store(todo_file(home)).open_tasks()
    assert (task.text, task.due) == ("kerjakan laporan", FAR_AHEAD)


@pytest.mark.parametrize("due", ["16-10-2099", "20991016", "2099-02-30", "Jumat"])
def test_todo_add_refuses_a_due_date_not_written_yyyy_mm_dd(due, home, capsys):
    with pytest.raises(SystemExit) as exited:
        cli.main(["todo", "add", "kerjakan", "laporan", "--due", due])

    assert exited.value.code == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err
    assert not todo_file(home).exists()


@pytest.mark.parametrize(
    ("words", "reason"),
    [
        (["ganti", "PIN", "ATM-ku", "4821"], "rahasia"),
        (["susu" * 51], "terlalu panjang"),
        (["  "], "kosong"),
    ],
)
def test_todo_add_prints_why_a_task_is_refused_and_exits_1(words, reason, home, capsys):
    assert cli.main(["todo", "add", *words]) == 1

    out = capsys.readouterr().out
    assert out.startswith("✗ ") and reason in out
    assert "4821" not in out
    assert not todo_file(home).exists()


def test_todo_add_keeps_to_the_number_of_open_tasks_set_in_the_config(home, capsys):
    write_config(home, "todo:\n  path: tugas/daftar.json\n  max_tasks: 1\n")
    assert cli.main(["todo", "add", "beli", "susu"]) == 0
    capsys.readouterr()

    assert cli.main(["todo", "add", "bayar", "listrik"]) == 1

    out = capsys.readouterr().out
    assert out.startswith("✗ ") and "penuh" in out
    tasks = todo_store(home / "tugas" / "daftar.json").open_tasks()
    assert [task.text for task in tasks] == ["beli susu"]


def test_todo_done_marks_one_task_done_and_says_which(home, capsys):
    store = todo_store(todo_file(home))
    store.add("beli susu")
    store.add("bayar listrik")

    assert cli.main(["todo", "done", "t1"]) == 0

    assert capsys.readouterr().out == "✓ Selesai t1: beli susu\n"
    assert [task.text for task in store.open_tasks()] == ["bayar listrik"]
    assert [task.text for task in store.done_tasks()] == ["beli susu"]


def test_todo_remove_deletes_one_task_and_says_which(home, capsys):
    store = todo_store(todo_file(home))
    store.add("beli susu")
    store.add("bayar listrik")

    assert cli.main(["todo", "remove", "t2"]) == 0

    assert capsys.readouterr().out == "✓ Dihapus t2: bayar listrik\n"
    assert [task.text for task in store.open_tasks()] == ["beli susu"]
    assert store.done_tasks() == []


@pytest.mark.parametrize("action", ["done", "remove"])
def test_todo_done_or_remove_of_an_unknown_id_fails_with_the_reason(action, home, capsys):
    assert cli.main(["todo", action, "t9"]) == 1

    assert capsys.readouterr().out == "✗ Tidak ada tugas dengan id t9.\n"
    assert not todo_file(home).exists()


def todo_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "aftar tugas" in line]
    return line


def test_check_counts_the_open_tasks(home, nothing_installed, capsys):
    store = todo_store(todo_file(home))
    store.add("beli susu")
    store.add("bayar listrik")
    store.add("cuci motor")
    store.complete("t1")  # 2 open, 1 done, 3 in all: each count differs

    cli.main(["check"])

    assert todo_line(capsys.readouterr().out) == "✓ Daftar tugas: 2 terbuka"


def test_check_reports_the_todo_list_off(home, nothing_installed, capsys):
    disable_todo(home)

    cli.main(["check"])

    assert todo_line(capsys.readouterr().out) == "! Daftar tugas mati"


def test_check_shows_where_a_broken_todo_file_was_moved(home, nothing_installed, capsys):
    todo_file(home).parent.mkdir()
    todo_file(home).write_text("{tugas yang rusak", encoding="utf-8")

    cli.main(["check"])

    [moved] = todo_file(home).parent.glob("todo.json.corrupt-*")
    line = todo_line(capsys.readouterr().out)
    assert line.startswith("! Daftar tugas: 0 terbuka; ")
    assert str(Path("data") / moved.name) in line
    assert moved.read_text(encoding="utf-8") == "{tugas yang rusak"
    assert not todo_file(home).exists()


def test_check_fails_a_todo_file_it_cannot_read(home, nothing_installed, capsys):
    todo_file(home).mkdir(parents=True)  # a folder where the file should be

    cli.main(["check"])

    line = todo_line(capsys.readouterr().out)
    assert line.startswith(f"✗ File daftar tugas {Path('data/todo.json')} tidak bisa dibaca")


def test_the_example_todo_block_is_valid_once_written_out(home):
    write_out_example_block(home, "todo")

    assert cli.load_config(home / "config.yaml").todo == TodoConfig()


def strict_parameters(schema: dict) -> list[dict]:
    """The parameter schemas of every object in a strict input schema, nested ones too."""
    found: list[dict] = []
    for value in schema.get("properties", {}).values():
        found.append(value)
        found += strict_parameters(value)
        for branch in value.get("anyOf", []):
            found += strict_parameters(branch)
    if "items" in schema:
        found += strict_parameters(schema["items"])
    return found


def optional_parameters(schema: dict) -> int:
    """The parameters left out of their object's `required`, nested ones too."""
    left_out = len(set(schema.get("properties", {})) - set(schema.get("required", [])))
    nested = [*schema.get("properties", {}).values()]
    nested += [branch for value in nested for branch in value.get("anyOf", [])]
    if "items" in schema:
        nested.append(schema["items"])
    return left_out + sum(optional_parameters(inner) for inner in nested)


def test_the_claude_request_with_every_tool_on_stays_within_the_strict_tool_limits(
    home, games, tepira, monkeypatch
):
    # Anthropic refuses a request with more than 20 strict tools, 24 optional parameters, or
    # 16 parameters with a union type across its strict schemas: every turn would be a 400.
    use_claude(home)
    enable_gacha(home)
    enable_tepira(home, writes=True)
    append_config(
        home, "routines:\n  tidur:\n    steps:\n      - {device: lampu_kamar, command: off}\n"
    )
    (home / "config").mkdir()
    shutil.copy(ROOT / "config" / "persona.md", home / "config" / "persona.md")
    sent: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        message = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": "Halo, Master!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        return streamed_message(message)

    use_claude_transport(monkeypatch, handler)
    monkeypatch.setattr(sys, "stdin", io.StringIO("halo\nkeluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    [request] = sent
    names = {tool["name"] for tool in request["tools"]}
    assert {
        "game_events",
        "weekly_report",
        "run_routine",
        "add_task",
        "play_youtube",
        "look_at_screen",
        "api_usage",
        "play_time",
        *FINANCE_TOOLS,
    } <= names
    strict = [tool for tool in request["tools"] if tool.get("strict")]
    schemas = [tool["input_schema"] for tool in strict]
    parameters = [parameter for schema in schemas for parameter in strict_parameters(schema)]
    assert len(strict) <= 20
    assert sum(optional_parameters(schema) for schema in schemas) <= 24
    unions = [p for p in parameters if "anyOf" in p or isinstance(p.get("type"), list)]
    assert len(unions) <= 16
    # Every tool that acts on something stays strict.
    assert {
        "control_device",
        "redeem_codes",
        "remember",
        "forget",
        "schedule",
        "cancel_schedule",
        "run_routine",
        "add_task",
        "complete_task",
        "reopen_task",
        "remove_task",
        "edit_task",
        "send_offer_link",
        "play_youtube",
        "record_expense",
    } <= {tool["name"] for tool in strict}


# --- songs on YouTube ------------------------------------------------------------------------

VIDEO = "dQw4w9WgXcQ"
PLAYLIST = "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"
WATCH = f"https://www.youtube.com/watch?v={VIDEO}"


class FakeOpener:
    """Stands in for music.open_in_browser: records each link instead of opening it in the
    browser (tests/conftest.py also refuses the real os.startfile). With `error`, raises it
    after recording, as Windows does when it cannot open the link."""

    def __init__(self) -> None:
        self.links: list[str] = []
        self.error: OSError | None = None

    def __call__(self, link: str) -> None:
        self.links.append(link)
        if self.error is not None:
            raise self.error


@pytest.fixture
def opener(monkeypatch) -> FakeOpener:
    fake = FakeOpener()
    monkeypatch.setattr(cli, "open_in_browser", fake)
    return fake


def disable_music(home: Path) -> None:
    append_config(home, "music:\n  enabled: false\n")


def music_tools(brain: HouseBrain) -> MusicTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, MusicTools)]
    return tools


def play_song(tools: MusicTools, arguments: dict) -> tuple[dict, bool]:
    result, is_error = tools.run("play_youtube", arguments)
    return json.loads(result), is_error


@pytest.mark.parametrize("claude", [False, True], ids=["ollama", "claude"])
@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_run_and_chat_give_the_brain_the_music_tool_with_either_backend(
    argv, claude, home, run_fakes, brains, opener, monkeypatch
):
    if claude:
        use_claude(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        MemoryTools,
        ScheduleTools,
        TodoTools,
        *([FocusTools] if argv[0] == "run" else []),
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
        MusicTools,
        *([ScreenTools, UsageTools] if claude else []),
    ]
    tools = music_tools(brains[0])
    tools.begin_turn("Cyrene, putar lagu Hope Is the Thing With Feathers")
    assert play_song(tools, {"query": "Hope Is the Thing With Feathers"}) == (
        {"ok": True, "opened": "search", "service": "youtube"},
        False,
    )
    # Through the opener `waifu youtube` uses too, never one bound elsewhere.
    assert opener.links == [
        "https://www.youtube.com/results?search_query=Hope+Is+the+Thing+With+Feathers"
    ]


@pytest.mark.parametrize(
    ("claude", "block", "links"),
    [
        (False, "", False),
        (True, "web_search:\n  enabled: false\n", False),
        (True, "", True),
    ],
    ids=["ollama", "claude_without_web_search", "claude_with_web_search"],
)
def test_only_claude_with_her_web_search_may_pass_a_song_s_link(
    claude, block, links, home, brains, opener, monkeypatch
):
    # A brain that cannot look the link up could only make one up: the search page opens.
    if claude:
        use_claude(home)
    append_config(home, block)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    tools = music_tools(brains[0])
    [spec] = tools.specs()
    assert ("url" in spec.input_schema["properties"]) is links
    tools.begin_turn("Cyrene, putar lagu lofi")
    result = play_song(tools, {"query": "lofi", "url": f"https://youtu.be/{VIDEO}"})
    assert result == (
        {"ok": True, "opened": "video" if links else "search", "service": "youtube"},
        False,
    )
    assert opener.links == [WATCH if links else "https://www.youtube.com/results?search_query=lofi"]


def test_with_youtube_music_her_songs_open_on_youtube_music(home, brains, opener, monkeypatch):
    use_claude(home)
    append_config(home, "music:\n  service: youtube_music\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    tools = music_tools(brains[0])
    tools.begin_turn("Cyrene, play Hoyo-Mix")
    assert play_song(tools, {"query": "Hoyo-Mix", "url": f"https://youtu.be/{VIDEO}"}) == (
        {"ok": True, "opened": "video", "service": "youtube_music"},
        False,
    )
    assert opener.links == [f"https://music.youtube.com/watch?v={VIDEO}"]


def test_her_name_and_wake_words_never_ask_for_music(home, brains, opener, monkeypatch):
    write_config(home, "assistant:\n  name: Lagu\n  wake_words: [lagu, hai musik]\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    tools = music_tools(brains[0])
    for words in ["Lagu, nyalakan lampu kamar.", "Hai musik, aku pulang."]:
        tools.begin_turn(words)
        assert play_song(tools, {"query": "lofi"})[1] is True
    tools.begin_turn("Lagu, putar lofi.")
    assert play_song(tools, {"query": "lofi"})[1] is False
    assert opener.links == ["https://www.youtube.com/results?search_query=lofi"]


@pytest.mark.parametrize("argv", [["run", "--no-speaker-check"], ["chat"]])
def test_with_music_off_the_brain_gets_no_music_tool(
    argv, home, run_fakes, brains, opener, monkeypatch
):
    disable_music(home)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main([*argv, "--dry-run"]) == 0

    assert [type(tools) for tools in brains[0].providers] == [
        MemoryTools,
        ScheduleTools,
        TodoTools,
        *([FocusTools] if argv[0] == "run" else []),
        BriefingTools,
        PcTools,
        PlayTimeTools,
        OffersTools,
    ]
    assert opener.links == []


@pytest.mark.parametrize(
    ("url", "link"),
    [
        (f"https://youtu.be/{VIDEO}?si=Ab12-cd_EF34gh&t=42", WATCH),
        (f"https://m.youtube.com/watch?v={VIDEO}&list={PLAYLIST}", f"{WATCH}&list={PLAYLIST}"),
        (
            f"https://youtube.com/playlist?list={PLAYLIST}&si=Ab12",
            f"https://www.youtube.com/playlist?list={PLAYLIST}",
        ),
    ],
    ids=["video", "video_in_a_playlist", "playlist"],
)
def test_youtube_opens_a_youtube_link_as_its_canonical_link(url, link, home, opener, capsys):
    assert cli.main(["youtube", url]) == 0

    assert opener.links == [link]
    assert capsys.readouterr().out == f"✓ Dibuka di YouTube: {link}\n"


def test_youtube_opens_the_search_page_for_the_words(home, opener, capsys):
    assert cli.main(["youtube", "Hope", "Is", "the", "Thing", "With", "Feathers"]) == 0

    search = "https://www.youtube.com/results?search_query=Hope+Is+the+Thing+With+Feathers"
    assert opener.links == [search]
    assert capsys.readouterr().out == f"✓ Pencarian dibuka di YouTube: {search}\n"


@pytest.mark.parametrize(
    "url",
    [
        f"https://evil.example/watch?v={VIDEO}",
        f"https://www.youtube.com.evil.example/watch?v={VIDEO}",
        f"http://www.youtube.com/watch?v={VIDEO}",
        f"https://www.youtube.com/shorts/{VIDEO}",
        "file:///C:/Windows/System32/calc.exe",
    ],
)
def test_youtube_only_searches_for_any_other_link_and_never_opens_it(url, home, opener, capsys):
    assert cli.main(["youtube", url]) == 0

    [link] = opener.links
    parts = urllib.parse.urlsplit(link)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "www.youtube.com", "/results")
    assert urllib.parse.parse_qs(parts.query) == {"search_query": [url]}
    assert capsys.readouterr().out.startswith("✓ Pencarian dibuka di YouTube: ")


def test_youtube_opens_on_youtube_music_when_the_config_says_so(home, opener, capsys):
    append_config(home, "music:\n  service: youtube_music\n")

    assert cli.main(["youtube", WATCH]) == 0
    assert cli.main(["youtube", "lofi"]) == 0

    music_watch = f"https://music.youtube.com/watch?v={VIDEO}"
    music_search = "https://music.youtube.com/search?q=lofi"
    assert opener.links == [music_watch, music_search]
    assert capsys.readouterr().out.splitlines() == [
        f"✓ Dibuka di YouTube Music: {music_watch}",
        f"✓ Pencarian dibuka di YouTube Music: {music_search}",
    ]


def test_youtube_works_with_the_music_tool_off(home, opener, capsys):
    disable_music(home)

    assert cli.main(["youtube", "lofi"]) == 0

    assert opener.links == ["https://www.youtube.com/results?search_query=lofi"]


def test_youtube_says_why_when_windows_cannot_open_the_browser(home, opener, capsys):
    opener.error = OSError("no default browser")

    assert cli.main(["youtube", "lofi"]) == 1

    out = capsys.readouterr().out
    assert out.startswith("✗ Browser tidak bisa dibuka") and "no default browser" in out
    assert opener.links == ["https://www.youtube.com/results?search_query=lofi"]


def test_youtube_with_only_spaces_opens_nothing(home, opener, capsys):
    assert cli.main(["youtube", " ", ""]) == 1

    assert capsys.readouterr().out.startswith("✗ ")
    assert opener.links == []


def test_youtube_needs_a_link_or_words(capsys):
    with pytest.raises(SystemExit) as exited:
        cli.build_parser().parse_args(["youtube"])

    assert exited.value.code == 2
    assert "required: lagu" in capsys.readouterr().err


def music_line(out: str) -> str:
    [line] = [line for line in out.splitlines() if "Lagu YouTube" in line]
    return line


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ("", "✓ Lagu YouTube: aktif (YouTube)"),
        ("music:\n  service: youtube_music\n", "✓ Lagu YouTube: aktif (YouTube Music)"),
        ("music:\n  enabled: false\n", "! Lagu YouTube mati"),
    ],
    ids=["youtube", "youtube_music", "off"],
)
def test_check_reports_whether_she_opens_songs_on_youtube(
    block, expected, home, nothing_installed, opener, capsys
):
    append_config(home, block)

    cli.main(["check"])

    assert music_line(capsys.readouterr().out) == expected
    assert opener.links == []


def test_the_example_music_block_is_valid_once_written_out(home):
    write_out_example_block(home, "music")

    assert cli.load_config(home / "config.yaml").music == MusicConfig()


# --- Master's money from tepira (v1.24) --------------------------------------------------------

# A made-up token in the tpcy_ form: it must never show in a line, a log record, or an error.
TEPIRA_TOKEN = "tpcy_SECRETTEPIRA" + "0" * 31
TEPIRA_AS_OF = "2026-10-16T09:12:00+07:00"
# tepira's answers as the client hands them on (without "ok"), from the API's own examples.
TEPIRA_SUMMARY = {
    "as_of": TEPIRA_AS_OF,
    "period": {"month": 10, "year": 2026, "month_basis": "web", "day_basis": "wib"},
    "spent": {
        "total": 2350000,
        "count": 41,
        "gaji": 2100000,
        "bonus": 250000,
        "today": 45000,
        "yesterday": 120000,
        "this_week": 310000,
    },
    "income": {
        "salary": 5000000,
        "bonus": 500000,
        "starting_balance": 1000000,
        "net": 3150000,
        "ending_estimate": 4150000,
    },
    "savings_allocation": 500000,
    "budget": {
        "basis": "gaji+bonus",
        "allocated": 3000000,
        "spent": 2350000,
        "remaining": 650000,
        "used_percent": 78.3,
        "status": "WITHIN_BUDGET",
    },
    "categories": [
        {
            "category": "Makan harian",
            "allocated": 1200000,
            "spent": 980000,
            "remaining": 220000,
            "used_percent": 81.7,
            "over": False,
        },
        {
            "category": "Nongkrong / jajan",
            "allocated": 300000,
            "spent": 342000,
            "remaining": -42000,
            "used_percent": 114,
            "over": True,
        },
    ],
    "other_spent": {"uncategorized": 35000, "unbudgeted": 0},
    "top": [{"category": "Makan harian", "spent": 980000}],
    "insight": {"week_start": "2026-10-12", "health_score": 72, "status": "SEHAT"},
}
TEPIRA_WALLETS = {
    "as_of": TEPIRA_AS_OF,
    "wallets": [
        {"id": 1, "name": "GoPay", "type": "e-wallet", "balance": 250000, "is_debt": False},
        {"id": 2, "name": "BCA", "type": "bank", "balance": 5000000, "is_debt": False},
        {"id": 3, "name": "Kartu kredit", "type": "credit", "balance": -1200000, "is_debt": True},
    ],
    "more": 0,
    "totals": {"assets": 5250000, "debts": 1200000, "net_worth": 4050000},
}
TEPIRA_GOALS = {
    "as_of": TEPIRA_AS_OF,
    "goals": [
        {
            "id": 3,
            "title": "Laptop baru",
            "target": 15000000,
            "saved": 6200000,
            "remaining": 8800000,
            "percent": 41.3,
            "deadline": "2027-03-31",
            "days_left": 166,
            "per_month_needed": 1600000,
            "saved_this_month": 500000,
            "last_contribution": {"date": "2026-10-05", "amount": 500000},
        },
        {
            "id": 4,
            "title": "Dana darurat",
            "target": 5000000,
            "saved": 5000000,
            "remaining": 0,
            "percent": 100,
            "saved_this_month": 0,
        },
    ],
    "more": 0,
}
TEPIRA_TRANSACTIONS = {
    "as_of": TEPIRA_AS_OF,
    "transactions": [
        {
            "id": 812,
            "date": "2026-10-16 08:41",
            "amount": 18000,
            "category": "Nongkrong / jajan",
            "note": "kopi susu",
            "wallet": "GoPay",
            "source": "gaji",
        },
        {
            "id": 811,
            "date": "2026-10-15 19:02",
            "amount": 35000,
            "category": None,
            "note": None,
            "wallet": "BCA",
            "source": "bonus",
        },
    ],
}


def enable_tepira(home: Path, *, writes: bool = False) -> None:
    """The example config with tepira on and the made-up token; with `writes`, recording by voice
    to the default wallet GoPay as well."""
    config = home / "config.yaml"
    text = config.read_text(encoding="utf-8")
    changes = [
        ("\ntepira:\n  enabled: false\n", "\ntepira:\n  enabled: true\n"),
        ("  # token: tpcy_isi-dengan-hasil-waifu-tepira-token\n", f"  token: {TEPIRA_TOKEN}\n"),
    ]
    if writes:
        changes += [
            ("  writes: false ", "  writes: true  "),
            ("  # default_wallet: GoPay ", "  default_wallet: GoPay "),
        ]
    for old, new in changes:
        assert old in text
        text = text.replace(old, new, 1)
    config.write_text(text, encoding="utf-8")


class FakeTepira:
    """Stands in for TepiraClient, a FinanceSource: `build` remembers each client a command made
    (its httpx client, url, and token) and hands out this fake, which remembers every request and
    answers each read with `answers[route]` (raised when it is an exception)."""

    def __init__(self) -> None:
        self.built: list[tuple[httpx.Client, str, object]] = []
        self.asked: list[tuple[object, ...]] = []
        self.answers: dict[str, object] = {
            "summary": TEPIRA_SUMMARY,
            "wallets": TEPIRA_WALLETS,
            "goals": TEPIRA_GOALS,
            "transactions": TEPIRA_TRANSACTIONS,
        }

    def build(self, http: httpx.Client, url: str, token: object) -> "FakeTepira":
        self.built.append((http, url, token))
        return self

    def summary(self, month: int, year: int) -> dict:
        return self._answer("summary", month, year)

    def wallets(self) -> dict:
        return self._answer("wallets")

    def goals(self, query: str | None) -> dict:
        return self._answer("goals", query)

    def transactions(self, limit: int, category: str | None) -> dict:
        return self._answer("transactions", limit, category)

    def record(
        self, amount: int, category: str | None, note: str | None, wallet: int | str, source="gaji"
    ) -> RecordedExpense:
        self.asked.append(("record", amount, category, note, wallet, source))
        [name] = [
            each["name"]
            for each in TEPIRA_WALLETS["wallets"]
            if wallet in (each["id"], each["name"])
        ]
        return RecordedExpense(812, amount, category, note, "2026-10-16 08:41", name, False)

    def undo(self, expense_id: int) -> UndoneExpense:
        self.asked.append(("undo", expense_id))
        return UndoneExpense(expense_id, 18000)

    def _answer(self, route: str, *arguments: object) -> dict:
        self.asked.append((route, *arguments))
        answer = self.answers[route]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def tepira(monkeypatch) -> FakeTepira:
    fake = FakeTepira()
    monkeypatch.setattr(cli, "TepiraClient", fake.build)
    return fake


def test_the_fake_tepira_has_the_real_client_s_methods():
    # So the views and the wiring tested over the fake work over the real client.
    for name in ("summary", "wallets", "goals", "transactions", "record", "undo"):
        fake, real = getattr(FakeTepira, name), getattr(TepiraClient, name)
        assert list(inspect.signature(fake).parameters) == list(inspect.signature(real).parameters)
    assert list(inspect.signature(FakeTepira.build).parameters) == ["self", "http", "url", "token"]
    assert list(inspect.signature(TepiraClient).parameters) == ["http", "url", "token", "clock"]


def finance_tools(brain: HouseBrain) -> FinanceTools:
    [tools] = [provider for provider in brain.providers if isinstance(provider, FinanceTools)]
    return tools


def test_run_with_tepira_on_reads_master_s_money_after_the_free_offers(
    home, run_fakes, brains, tepira
):
    enable_tepira(home)

    assert cli.main(["run", "--dry-run", "--no-speaker-check"]) == 0

    assert [type(tools) for tools in brains[0].providers][-5:] == [
        PcTools,
        PlayTimeTools,
        OffersTools,
        FinanceTools,
        MusicTools,
    ]
    [(http, url, token)] = tepira.built
    assert isinstance(http, httpx.Client)
    assert url == "https://tepira.asepsunarya.com"
    assert token.get_secret_value() == TEPIRA_TOKEN
    # Only the reads: recording is off in the example config. Building asked tepira nothing.
    assert [spec.name for spec in finance_tools(brains[0]).specs()] == list(READ_TOOLS)
    assert tepira.asked == []


def test_chat_with_tepira_off_builds_neither_the_finance_tools_nor_a_client(
    home, brains, tepira, monkeypatch
):
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert not any(isinstance(tools, FinanceTools) for tools in brains[0].providers)
    assert tepira.built == []


def test_with_writes_on_and_claude_she_may_also_record_and_undo(home, brains, tepira, monkeypatch):
    use_claude(home)
    enable_tepira(home, writes=True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert [spec.name for spec in finance_tools(brains[0]).specs()] == list(FINANCE_TOOLS)


def test_with_writes_on_and_ollama_she_only_reads(home, brains, tepira, monkeypatch):
    # Recording needs the brain that picks tools reliably: Claude.
    enable_tepira(home, writes=True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    assert [spec.name for spec in finance_tools(brains[0]).specs()] == list(READ_TOOLS)


def test_the_finance_tools_hold_an_expense_with_the_brain_s_own_gate(
    home, brains, tepira, monkeypatch
):
    # One gate: the question the tools hold an expense with is the one the brain asks, and the
    # PC and an expense share the one question of a turn.
    use_claude(home)
    enable_tepira(home, writes=True)
    monkeypatch.setattr(sys, "stdin", io.StringIO("keluar\n"))
    assert cli.main(["chat", "--dry-run"]) == 0
    gate, tools = brains[0].confirmations, finance_tools(brains[0])

    gate.begin_turn("catat jajan kopi 18 ribu")
    tools.begin_turn("catat jajan kopi 18 ribu")
    result, is_error = tools.run("record_expense", {"amount": 18000, "description": "jajan kopi"})

    assert is_error and json.loads(result)["needs_confirmation"] is True
    assert gate.with_question("Baik.") == (
        "Baik. Catat delapan belas ribu untuk jajan kopi di GoPay, Master? Jawab iya kalau jadi."
    )
    assert [asked for asked in tepira.asked if asked[0] == "record"] == []


def claude_replies(sent: list[dict], replies: list[list[dict]]) -> Callable:
    """A Claude transport handler answering the n-th request with the n-th content list."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        content = replies[len(sent) - 1]
        message = {
            "id": f"msg_{len(sent)}",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": "tool_use" if content[0]["type"] == "tool_use" else "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        return streamed_message(message)

    return handler


def test_chat_records_an_expense_only_after_master_s_yes_in_the_next_turn(
    home, tepira, monkeypatch, capsys
):
    # The real Claude brain and finance tools over the fake tepira, Claude over a mock transport.
    use_claude(home)
    enable_tepira(home, writes=True)
    (home / "config").mkdir()
    shutil.copy(ROOT / "config" / "persona.md", home / "config" / "persona.md")
    record = {
        "type": "tool_use",
        "name": "record_expense",
        "input": {"amount": 18000, "description": "jajan kopi"},
    }
    sent: list[dict] = []
    replies = [
        [{**record, "id": "toolu_1"}],
        [{"type": "text", "text": "Baik, Master."}],
        [{**record, "id": "toolu_2"}],
        [{"type": "text", "text": "Sudah kucatat, Master."}],
    ]
    use_claude_transport(monkeypatch, claude_replies(sent, replies))
    monkeypatch.setattr(sys, "stdin", io.StringIO("catat jajan kopi 18 ribu\niya\nkeluar\n"))

    assert cli.main(["chat", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert (
        "Cyrene: Baik, Master. Catat delapan belas ribu untuk jajan kopi di GoPay, Master? "
        "Jawab iya kalau jadi." in out
    )
    assert "Cyrene: Sudah kucatat, Master." in out
    records = [asked for asked in tepira.asked if asked[0] == "record"]
    assert records == [("record", 18000, None, "jajan kopi", 1, "gaji")]  # GoPay's id


def test_tepira_summary_prints_this_month_in_wib(home, tepira, capsys):
    enable_tepira(home)

    assert cli.main(["tepira", "ringkasan"]) == 0

    today = datetime.now(WIB)
    assert tepira.asked == [("summary", today.month, today.year)]
    assert capsys.readouterr().out.splitlines() == [
        f"Keuangan {MONTHS_ID[today.month - 1]} {today.year} (per 2026-10-16 09:12 WIB)",
        "Pengeluaran: Rp2.350.000 dari 41 transaksi (gaji Rp2.100.000, bonus Rp250.000); "
        "hari ini Rp45.000, kemarin Rp120.000, minggu ini Rp310.000",
        "Pemasukan: gaji Rp5.000.000, bonus Rp500.000, saldo awal Rp1.000.000; "
        "sisa Rp3.150.000, perkiraan saldo akhir Rp4.150.000",
        "Budget (gaji+bonus): Rp3.000.000, terpakai Rp2.350.000 (78,3%), sisa Rp650.000",
        "- Makan harian: sisa Rp220.000 dari Rp1.200.000 (81,7%)",
        "- Nongkrong / jajan: lewat Rp42.000 dari Rp300.000 (114%)",
        "Tanpa kategori Rp35.000; di luar budget Rp0",
        "Skor mingguan: 72 (SEHAT), minggu mulai 2026-10-12",
    ]
    # One connection for the command, closed when it ends.
    [(http, url, token)] = tepira.built
    assert http.is_closed
    assert (url, token.get_secret_value()) == ("https://tepira.asepsunarya.com", TEPIRA_TOKEN)


def test_tepira_without_an_action_prints_the_summary(home, tepira, capsys):
    enable_tepira(home)

    assert cli.main(["tepira"]) == 0

    assert [asked[0] for asked in tepira.asked] == ["summary"]
    assert capsys.readouterr().out.startswith("Keuangan ")


def test_tepira_summary_leaves_out_what_tepira_left_out(home, tepira, capsys):
    # A month without salary, bonus, or starting balance rows, and no weekly score yet.
    enable_tepira(home)
    spent = {"total": 120000, "count": 3, "gaji": 120000, "bonus": 0}
    tepira.answers["summary"] = {
        **TEPIRA_SUMMARY,
        "spent": spent,
        "income": {"net": -120000, "ending_estimate": -120000},
        "budget": {
            "basis": "gaji+bonus",
            "allocated": 100000,
            "spent": 120000,
            "remaining": -20000,
            "used_percent": 120,
            "status": "OVER_BUDGET",
        },
        "categories": [],
    }
    del tepira.answers["summary"]["insight"]

    assert cli.main(["tepira", "ringkasan"]) == 0

    assert capsys.readouterr().out.splitlines()[1:] == [
        "Pengeluaran: Rp120.000 dari 3 transaksi (gaji Rp120.000, bonus Rp0)",
        "Pemasukan: sisa -Rp120.000, perkiraan saldo akhir -Rp120.000",
        "Budget (gaji+bonus): Rp100.000, terpakai Rp120.000 (120%), lewat Rp20.000",
        "Tanpa kategori Rp35.000; di luar budget Rp0",
    ]


def test_tepira_summary_of_a_month_without_a_budget_says_so(home, tepira, capsys):
    enable_tepira(home)
    budget = {**TEPIRA_SUMMARY["budget"], "allocated": 0, "status": "NO_BUDGET"}
    tepira.answers["summary"] = {**TEPIRA_SUMMARY, "budget": budget, "categories": []}

    assert cli.main(["tepira"]) == 0

    assert "Budget (gaji+bonus): belum diatur" in capsys.readouterr().out.splitlines()


def test_tepira_wallets_lists_each_balance_then_the_totals(home, tepira, capsys):
    enable_tepira(home)
    tepira.answers["wallets"] = {**TEPIRA_WALLETS, "more": 2}

    assert cli.main(["tepira", "dompet"]) == 0

    assert tepira.asked == [("wallets",)]
    assert capsys.readouterr().out.splitlines() == [
        "- GoPay (e-wallet): Rp250.000",
        "- BCA (bank): Rp5.000.000",
        "- Kartu kredit (credit): -Rp1.200.000",
        "dan 2 dompet lagi",
        "Aset Rp5.250.000, kewajiban Rp1.200.000, kekayaan bersih Rp4.050.000",
    ]


def test_tepira_goals_shows_each_goal_s_progress(home, tepira, capsys):
    enable_tepira(home)

    assert cli.main(["tepira", "tujuan"]) == 0

    assert tepira.asked == [("goals", None)]
    assert capsys.readouterr().out.splitlines() == [
        "- Laptop baru: Rp6.200.000 dari Rp15.000.000 (41,3%), kurang Rp8.800.000, "
        "tenggat 2027-03-31 (166 hari lagi), perlu Rp1.600.000 per bulan",
        "- Dana darurat: Rp5.000.000 dari Rp5.000.000 (100%), tercapai",
    ]


def test_tepira_goals_with_a_name_asks_for_that_name(home, tepira, capsys):
    enable_tepira(home)
    tepira.answers["goals"] = {**TEPIRA_GOALS, "goals": TEPIRA_GOALS["goals"][:1]}

    assert cli.main(["tepira", "tujuan", "laptop", " ", "baru"]) == 0

    assert tepira.asked == [("goals", "laptop baru")]
    assert capsys.readouterr().out.startswith("- Laptop baru: Rp6.200.000")


def test_tepira_goals_says_when_no_goal_matches(home, tepira, capsys):
    enable_tepira(home)
    tepira.answers["goals"] = {**TEPIRA_GOALS, "goals": []}

    assert cli.main(["tepira", "tujuan", "motor"]) == 0

    assert capsys.readouterr().out == "Tidak ada tujuan tabungan yang cocok.\n"


def test_tepira_goals_without_any_goal_says_there_is_none_yet(home, tepira, capsys):
    enable_tepira(home)
    tepira.answers["goals"] = {**TEPIRA_GOALS, "goals": []}

    assert cli.main(["tepira", "tujuan"]) == 0

    assert capsys.readouterr().out == "Belum ada tujuan tabungan.\n"


def test_tepira_goals_counts_the_goals_tepira_left_out(home, tepira, capsys):
    enable_tepira(home)
    tepira.answers["goals"] = {**TEPIRA_GOALS, "more": 3}

    assert cli.main(["tepira", "tujuan"]) == 0

    assert capsys.readouterr().out.splitlines()[-1] == "dan 3 tujuan lagi"


def test_tepira_goals_refuses_a_name_tepira_would_refuse(home, tepira, capsys):
    # tepira counts the name in UTF-16 units, as JavaScript does: an emoji counts twice.
    enable_tepira(home)

    assert cli.main(["tepira", "tujuan", "🎮" * 25 + "x"]) == 2

    assert capsys.readouterr().out == "✗ Nama tujuan paling banyak 50 karakter.\n"
    assert tepira.built == []


def test_tepira_transactions_shows_the_newest_first(home, tepira, capsys):
    enable_tepira(home)

    assert cli.main(["tepira", "transaksi"]) == 0

    assert tepira.asked == [("transactions", 5, None)]
    assert capsys.readouterr().out.splitlines() == [
        "- 2026-10-16 08:41 Rp18.000 Nongkrong / jajan: kopi susu (GoPay)",
        "- 2026-10-15 19:02 Rp35.000 tanpa kategori (BCA)",
    ]


def test_tepira_transactions_shows_n_of_them(home, tepira, capsys):
    enable_tepira(home)

    assert cli.main(["tepira", "transaksi", "-n", "10"]) == 0

    assert tepira.asked == [("transactions", 10, None)]


def test_tepira_transactions_says_when_there_are_none(home, tepira, capsys):
    enable_tepira(home)
    tepira.answers["transactions"] = {**TEPIRA_TRANSACTIONS, "transactions": []}

    assert cli.main(["tepira", "transaksi"]) == 0

    assert capsys.readouterr().out == "Belum ada transaksi.\n"


@pytest.mark.parametrize("count", ["0", "11", "lima"])
def test_tepira_transactions_takes_1_to_10(count, capsys):
    with pytest.raises(SystemExit) as exited:
        cli.build_parser().parse_args(["tepira", "transaksi", "-n", count])

    assert exited.value.code == 2
    assert "1 sampai 10" in capsys.readouterr().err


@pytest.mark.parametrize("action", [[], ["ringkasan"], ["dompet"], ["tujuan"], ["transaksi"]])
def test_tepira_off_says_how_to_turn_it_on(action, home, tepira, capsys):
    assert cli.main(["tepira", *action]) == 2

    assert capsys.readouterr().out == (
        "Keuangan tepira mati (tepira.enabled: false). Buat token dengan: waifu tepira token, "
        "lalu isi bagian tepira di config.yaml.\n"
    )
    assert tepira.built == []


def test_tepira_says_what_went_wrong_and_exits_1(home, tepira, capsys):
    enable_tepira(home)
    tepira.answers["wallets"] = FinanceError("token", TOKEN_REJECTED)

    assert cli.main(["tepira", "dompet"]) == 1

    assert capsys.readouterr().out == f"✗ {TOKEN_REJECTED}\n"


def test_tepira_calls_an_answer_without_a_field_it_shows_unreadable(home, tepira, capsys):
    enable_tepira(home)
    wallets = [{"name": "GoPay", "balance": 250000}]  # tepira always sends the type
    tepira.answers["wallets"] = {**TEPIRA_WALLETS, "totals": {}, "wallets": wallets}

    assert cli.main(["tepira", "dompet"]) == 1

    assert capsys.readouterr().out == "✗ Balasan tepira tidak bisa dibaca.\n"


def test_tepira_token_prints_a_new_token_and_its_sha256_once(tmp_path, monkeypatch, capsys, caplog):
    # No config.yaml and no network: a new token works before tepira is set up at all.
    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.DEBUG)

    assert cli.main(["-v", "tepira", "token"]) == 0

    token_line, sha_line, how = capsys.readouterr().out.splitlines()
    token = token_line.removeprefix("token : ")
    assert re.fullmatch(r"tpcy_[A-Za-z0-9_-]{43}", token)
    assert sha_line == f"sha256: {hashlib.sha256(token.encode()).hexdigest()}"
    assert "tepira.token" in how and "TEPIRA_CYRENE_TOKEN_SHA256" in how
    assert token not in caplog.text


def test_tepira_token_makes_a_different_token_every_time(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    cli.main(["tepira", "token"])
    cli.main(["tepira", "token"])

    tokens = [line for line in capsys.readouterr().out.splitlines() if line.startswith("token")]
    assert len(set(tokens)) == 2


def use_tepira_transport(monkeypatch, handler) -> None:
    """The real TepiraClient over an httpx MockTransport instead of the network."""
    real_client = httpx.Client
    monkeypatch.setattr(cli, "TepiraClient", TepiraClient)
    monkeypatch.setattr(
        cli.httpx, "Client", lambda **options: real_client(transport=httpx.MockTransport(handler))
    )


def test_tepira_through_the_real_client_sends_the_token_only_in_its_header(
    home, monkeypatch, capsys, caplog
):
    enable_tepira(home)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, **TEPIRA_WALLETS})

    use_tepira_transport(monkeypatch, handler)
    caplog.set_level(logging.DEBUG)

    assert cli.main(["-v", "tepira", "dompet"]) == 0

    [request] = requests
    assert str(request.url) == "https://tepira.asepsunarya.com/api/cyrene/wallets"
    assert request.headers["Authorization"] == f"Bearer {TEPIRA_TOKEN}"
    assert capsys.readouterr().out.startswith("- GoPay (e-wallet): Rp250.000\n")
    assert "SECRETTEPIRA" not in caplog.text


def test_tepira_with_its_token_refused_names_the_fix_and_never_the_token(
    home, monkeypatch, capsys, caplog
):
    enable_tepira(home)
    refused = httpx.Response(401, json={"ok": False, "error": "unauthorized"})
    use_tepira_transport(monkeypatch, lambda request: refused)
    caplog.set_level(logging.DEBUG)

    assert cli.main(["-v", "tepira"]) == 1

    out = capsys.readouterr().out
    assert out == f"✗ {TOKEN_REJECTED}\n"
    assert "SECRETTEPIRA" not in out + caplog.text


TEPIRA_CHECKED = "tepira.asepsunarya.com, token ada"


@pytest.mark.parametrize(
    ("backend", "tepira_on", "writes", "expected"),
    [
        ("ollama", False, False, "! Keuangan tepira mati (tepira.enabled: false)"),
        (
            "ollama",
            True,
            False,
            f"✓ Keuangan tepira: {TEPIRA_CHECKED}; catat transaksi mati; uji dengan: waifu tepira",
        ),
        (
            "claude",
            True,
            True,
            f"✓ Keuangan tepira: {TEPIRA_CHECKED}; catat transaksi aktif ke dompet GoPay; "
            "uji dengan: waifu tepira",
        ),
        (
            "ollama",
            True,
            True,
            f"! Keuangan tepira: {TEPIRA_CHECKED}; catat transaksi tidak tersedia dengan Ollama; "
            "uji dengan: waifu tepira",
        ),
    ],
    ids=["off", "reads", "writes_with_claude", "writes_with_ollama"],
)
def test_check_reports_master_s_money_from_tepira_offline_without_the_token(
    backend, tepira_on, writes, expected, home, nothing_installed, tepira, capsys
):
    if backend == "claude":
        use_claude(home)
    if tepira_on:
        enable_tepira(home, writes=writes)

    cli.main(["check"])

    out = capsys.readouterr().out
    lines = out.splitlines()
    [line] = [line for line in lines if "Keuangan tepira" in line]
    assert line == expected
    assert lines[lines.index(line) + 1] == "! Notifikasi HP mati (notify.enabled: false)"
    assert "tpcy_" not in out
    assert "SECRETTEPIRA" not in out
    assert tepira.built == []  # offline: no client, no request
