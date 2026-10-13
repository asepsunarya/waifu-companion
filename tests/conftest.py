"""Suite-wide safety net: no test may change the developer's real PC.

A forgotten fake in an early PC test once put the real machine to sleep on every
suite run. These guards fail such a test loudly instead: no power commands, no
PowrProf, no registry writes, no scheduled-task or reg.exe changes. No test may
reach beyond this PC either: a forgotten fake must not push to the owner's phone
or check in on their HoYoLAB account. Nor may a test read the owner's game logs and
caches, which hold their history authkeys, press real keys (media keys would change the
owner's music and volume), open real programs and URLs, or capture the owner's real screen.
"""

import ctypes
import ipaddress
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path, PurePath

import aiohttp
import pytest
import yarl

from waifu.gacha import history

try:
    import winreg
except ImportError:  # not Windows
    winreg = None

# Set at import time so it is in place before any test runs and is inherited by
# subprocesses that tests spawn (e.g. `python -m waifu ...`).
os.environ["WAIFU_DISABLE_POWER"] = "1"

_POWER_PROGRAMS = {"shutdown", "rundll32"}
# Functions refused per library, each with what a test was trying to do.
_REFUSED_FUNCTIONS = {
    "user32": {
        "sendinput": "press real keys",
        "keybd_event": "press real keys",
        "printwindow": "capture the real screen",
    },
    "gdi32": {"bitblt": "capture the real screen", "stretchblt": "capture the real screen"},
}
_REG_WRITE_VERBS = {"add", "delete", "import", "restore", "copy", "load", "unload"}
_REGISTRY_WRITERS = (
    "SetValue",
    "SetValueEx",
    "DeleteValue",
    "DeleteKey",
    "DeleteKeyEx",
    "CreateKey",
    "CreateKeyEx",
)
_REAL_POPEN = subprocess.Popen
_REAL_WINDLL = getattr(ctypes, "WinDLL", None)
_REAL_URLLIB_OPEN = urllib.request.OpenerDirector.open
_REAL_AIOHTTP_REQUEST = aiohttp.ClientSession._request
_REAL_DATA_FOLDER = history._data_folder
_REAL_GAME_LOGS = Path.home() / "AppData" / "LocalLow"


def _words(args: object) -> list[str]:
    if isinstance(args, bytes):
        args = args.decode()
    if isinstance(args, str):
        return args.split()
    return [str(arg) for arg in args] if args else []  # type: ignore[union-attr]


def _program(word: str) -> str:
    return PurePath(word.strip('"')).stem.lower()


def _is_power_command(args: object) -> bool:
    words = _words(args)
    text = " ".join(words).lower()
    if isinstance(args, (str, bytes)):
        return any(name in text for name in _POWER_PROGRAMS) or "setsuspendstate" in text
    return bool(words) and _program(words[0]) in _POWER_PROGRAMS


def _is_system_change_command(args: object) -> bool:
    words = _words(args)
    programs = [_program(word) for word in words]
    if "schtasks" in programs:
        return True
    if "rvc_server" in programs:  # would load Applio and a GPU model
        return True
    if programs and programs[0] in {"electron", "msedge"}:  # would open a real window
        return True
    if "reg" in programs:
        verb_index = programs.index("reg") + 1
        return verb_index < len(words) and words[verb_index].lower() in _REG_WRITE_VERBS
    return False


class _GuardedPopen(_REAL_POPEN):  # type: ignore[misc, valid-type]
    def __init__(self, args, *pargs, **kwargs):
        if _is_power_command(args):
            raise AssertionError(f"a test tried to run a real power command: {args!r}")
        if _is_system_change_command(args):
            raise AssertionError(f"a test tried to run a real system-change command: {args!r}")
        super().__init__(args, *pargs, **kwargs)


# Refused key presses, screen captures, and startfile calls of the running test: code under
# test may catch the refusal (a device or tool turns errors into results), so the fixture below
# fails the test afterwards too.
_refused_system_calls: list[str] = []


class _GuardedDll:
    """A system library without its refused functions: looking one up already fails."""

    def __init__(self, dll, library: str) -> None:
        self._dll = dll
        self._library = library

    def __getattr__(self, name: str):
        return self[name]

    def __getitem__(self, name: str):
        purpose = _REFUSED_FUNCTIONS[self._library].get(name.lower())
        if purpose:
            _refused_system_calls.append(f"{self._library}.{name}")
            raise AssertionError(f"a test tried to {purpose} with {self._library}.{name}")
        return getattr(self._dll, name)


def _guarded_windll(name, *args, **kwargs):
    stem = PurePath(str(name)).stem.lower()
    if "powrprof" in stem:
        raise AssertionError("a test tried to load the real PowrProf.dll")
    dll = _REAL_WINDLL(name, *args, **kwargs)
    return _GuardedDll(dll, stem) if stem in _REFUSED_FUNCTIONS else dll


def _refuse_startfile(*args, **kwargs):
    _refused_system_calls.append("os.startfile")
    raise AssertionError(f"a test tried to open a real program or URL with os.startfile: {args!r}")


def _refuse_registry_write(*args, **kwargs):
    raise AssertionError("a test tried to write the real Windows registry")


def _is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _refuse_beyond_this_pc(host: str | None) -> None:
    if not _is_loopback(host):
        raise AssertionError(f"a test tried to reach the internet ({host})")


def _guarded_urllib_open(self, fullurl, *args, **kwargs):
    url = fullurl.full_url if isinstance(fullurl, urllib.request.Request) else fullurl
    _refuse_beyond_this_pc(urllib.parse.urlsplit(url).hostname)
    return _REAL_URLLIB_OPEN(self, fullurl, *args, **kwargs)


async def _guarded_aiohttp_request(self, method, str_or_url, *args, **kwargs):
    _refuse_beyond_this_pc(yarl.URL(str(str_or_url)).host)
    return await _REAL_AIOHTTP_REQUEST(self, method, str_or_url, *args, **kwargs)


_real_game_cache_reads: list[str] = []


def _guarded_data_folder(game, locallow):
    if Path(locallow).resolve() == _REAL_GAME_LOGS.resolve():
        _real_game_cache_reads.append(game.id)
        raise AssertionError("a test tried to read the owner's real game logs and caches")
    return _REAL_DATA_FOLDER(game, locallow)


@pytest.fixture(autouse=True)
def _refuse_real_power_actions(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)
    if _REAL_WINDLL is not None:
        monkeypatch.setattr(ctypes, "WinDLL", _guarded_windll)
        monkeypatch.setattr(ctypes, "windll", ctypes.LibraryLoader(_guarded_windll))
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", _refuse_startfile)


@pytest.fixture(autouse=True)
def refused_system_calls():
    """Fails the test afterwards if it reached a refused key press, screen capture, or startfile,
    even when the code under test caught the refusal."""
    _refused_system_calls.clear()
    yield _refused_system_calls
    assert not _refused_system_calls, (
        f"a test reached refused system calls: {_refused_system_calls}"
    )


@pytest.fixture(autouse=True)
def _refuse_real_registry_writes(monkeypatch):
    if winreg is None:
        return
    for name in _REGISTRY_WRITERS:
        monkeypatch.setattr(winreg, name, _refuse_registry_write)


@pytest.fixture(autouse=True)
def _refuse_the_internet(monkeypatch):
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _guarded_urllib_open)
    monkeypatch.setattr(aiohttp.ClientSession, "_request", _guarded_aiohttp_request)


@pytest.fixture(autouse=True)
def real_game_cache_reads(monkeypatch):
    """Refuses reading the real game caches, and fails the test afterwards if it tried:
    `waifu` commands catch every exception, so the refusal alone could go unnoticed."""
    _real_game_cache_reads.clear()
    monkeypatch.setattr(history, "_data_folder", _guarded_data_folder)
    yield _real_game_cache_reads
    assert not _real_game_cache_reads, (
        f"a test tried to read the owner's real game caches: {_real_game_cache_reads}"
    )
