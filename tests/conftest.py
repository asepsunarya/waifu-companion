"""Suite-wide safety net: no test may change the developer's real PC.

A forgotten fake in an early PC test once put the real machine to sleep on every
suite run. These guards fail such a test loudly instead: no power commands, no
PowrProf, no registry writes, no scheduled-task or reg.exe changes. No test may
reach beyond this PC either: a forgotten fake must not push to the owner's phone
or check in on their HoYoLAB account. Nor may a test read the owner's game logs and
caches, which hold their history authkeys, press real keys (media keys would change the
owner's music and volume), open real programs and URLs, or capture the owner's real screen.
Nor may a test run git: the version reader finds the repository from its own file, so a
forgotten fake would ask the real repository, which the builder commits to. Nor PowerShell:
the offline fallback voice speaks through it, so a forgotten fake would use the owner's
Windows voices. Nor the real process list: the play time watcher reads it, so a forgotten fake
would make a test pass or fail by whether the owner is playing. Nor the owner's last keyboard or
mouse input: the break reminder reads it, so a forgotten fake would make a test pass or fail by
whether the owner is at the PC. Nor the owner's microphones and speakers: opening a sounddevice
stream, playing, recording, or restarting PortAudio is refused, also in the test that imports
sounddevice first, and so is winmm's list of the microphones plugged in, which the mic switch
reads.
"""

import ctypes
import importlib.abc
import ipaddress
import os
import socket
import subprocess
import sys
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
        "getlastinputinfo": "read the owner's real keyboard and mouse activity",
    },
    "gdi32": {"bitblt": "capture the real screen", "stretchblt": "capture the real screen"},
    # Only the process snapshot: pc_health and process_alive need the rest of kernel32.
    "kernel32": {"createtoolhelp32snapshot": "list the real processes"},
    "winmm": {
        "waveingetnumdevs": "list the owner's real microphones",
        "waveingetdevcapsw": "list the owner's real microphones",
    },
}
# sounddevice's functions that reach the owner's microphone, speakers, or the PortAudio every
# test shares, each with what a test was trying to do.
_REFUSED_SOUNDDEVICE = {
    "InputStream": "open a real audio stream",
    "RawInputStream": "open a real audio stream",
    "OutputStream": "open a real audio stream",
    "RawOutputStream": "open a real audio stream",
    "Stream": "open a real audio stream",
    "RawStream": "open a real audio stream",
    "play": "play real audio",
    "playrec": "play real audio",
    "rec": "record real audio",
    "_initialize": "restart the real PortAudio",
    "_terminate": "restart the real PortAudio",
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
_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
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
    if _is_powershell_command(args):  # would speak with Windows' voices
        return True
    if "reg" in programs:
        verb_index = programs.index("reg") + 1
        return verb_index < len(words) and words[verb_index].lower() in _REG_WRITE_VERBS
    return False


def _is_powershell_command(args: object) -> bool:
    words = _words(args)
    return bool(words) and _program(words[0]) in {"powershell", "pwsh"}


def _is_git_command(args: object) -> bool:
    words = _words(args)
    return bool(words) and _program(words[0]) == "git"


class _GuardedPopen(_REAL_POPEN):  # type: ignore[misc, valid-type]
    def __init__(self, args, *pargs, **kwargs):
        if _is_power_command(args):
            raise AssertionError(f"a test tried to run a real power command: {args!r}")
        if _is_git_command(args):
            raise AssertionError(f"a test tried to run git: {args!r}")
        if _is_system_change_command(args):
            if _is_powershell_command(args):  # the fallback voice's callers catch everything
                _refused_system_calls.append("powershell")
            raise AssertionError(f"a test tried to run a real system-change command: {args!r}")
        super().__init__(args, *pargs, **kwargs)


# Refused key presses, screen captures, process listings, input readings, startfile calls, and
# PowerShell runs of the running test: code under test may catch the refusal (a device or tool
# turns errors into results, a watcher logs them), so the fixture below fails the test afterwards
# too.
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


def _refusing_sounddevice(name: str, purpose: str):
    def refuse(*args, **kwargs):
        _refused_system_calls.append(f"sounddevice.{name}")
        raise AssertionError(f"a test tried to {purpose} with sounddevice.{name}")

    return refuse


def _refuse_real_audio(sounddevice, monkeypatch) -> None:
    for name, purpose in _REFUSED_SOUNDDEVICE.items():
        monkeypatch.setattr(sounddevice, name, _refusing_sounddevice(name, purpose), raising=False)


class _SounddeviceImportGuard(importlib.abc.MetaPathFinder):
    """Finds sounddevice as the other finders do, with a loader that refuses its real audio
    through the running test's `monkeypatch` as soon as the module has run: the code imports it
    lazily, so a forgotten fake in a test run on its own is the first to import it, after the
    fixture below looked for it. Put back after the test, like every guard: sounddevice's exit
    handler stops PortAudio."""

    def __init__(self) -> None:
        self.monkeypatch: pytest.MonkeyPatch | None = None  # the running test's, else None

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "sounddevice" or self.monkeypatch is None:
            return None
        for finder in sys.meta_path:
            find = getattr(finder, "find_spec", None)
            spec = None if finder is self or find is None else find(fullname, path, target)
            if spec is not None and spec.loader is not None:
                spec.loader = _RefusingLoader(spec.loader, self.monkeypatch)
                return spec
        return None


class _RefusingLoader(importlib.abc.Loader):
    """sounddevice's own loader, refusing its real audio once the module has run."""

    def __init__(self, loader, monkeypatch: pytest.MonkeyPatch) -> None:
        self._loader = loader
        self._monkeypatch = monkeypatch

    def __getattr__(self, name: str):
        return getattr(self._loader, name)

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module) -> None:
        self._loader.exec_module(module)
        _refuse_real_audio(module, self._monkeypatch)


_SOUNDDEVICE_IMPORT_GUARD = _SounddeviceImportGuard()
sys.meta_path.insert(0, _SOUNDDEVICE_IMPORT_GUARD)


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


# Below every client (httpx, the anthropic SDK's httpx2, anything else): a host name is looked
# up first, and an address given as digits is connected to directly.
_UNSPECIFIED_HOSTS = (None, "", "0.0.0.0", "::")  # a server binding, not a connection


def _guarded_getaddrinfo(host, *args, **kwargs):
    name = host.decode() if isinstance(host, bytes) else host
    if name not in _UNSPECIFIED_HOSTS:
        _refuse_beyond_this_pc(name)
    return _REAL_GETADDRINFO(host, *args, **kwargs)


def _guarded_socket_connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _refuse_beyond_this_pc(address[0])
    return _REAL_SOCKET_CONNECT(self, address)


def _guarded_socket_connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _refuse_beyond_this_pc(address[0])
    return _REAL_SOCKET_CONNECT_EX(self, address)


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
    # Never imported here, which would start PortAudio for every run: refused now when it was
    # imported already, else by the import guard as the test imports it.
    sounddevice = sys.modules.get("sounddevice")
    if sounddevice is not None:
        _refuse_real_audio(sounddevice, monkeypatch)
    _SOUNDDEVICE_IMPORT_GUARD.monkeypatch = monkeypatch
    yield
    _SOUNDDEVICE_IMPORT_GUARD.monkeypatch = None


@pytest.fixture(autouse=True)
def refused_system_calls():
    """Fails the test afterwards if it reached a refused key press, screen capture, process
    listing, input reading, startfile, or PowerShell run, even when the code under test caught
    the refusal."""
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
    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", _guarded_socket_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_socket_connect_ex)


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
