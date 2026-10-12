"""Suite-wide safety net: no test may change the developer's real PC.

A forgotten fake in an early PC test once put the real machine to sleep on every
suite run. These guards fail such a test loudly instead: no power commands, no
PowrProf, no registry writes, no scheduled-task or reg.exe changes.
"""

import ctypes
import os
import subprocess
from pathlib import PurePath

import pytest

try:
    import winreg
except ImportError:  # not Windows
    winreg = None

# Set at import time so it is in place before any test runs and is inherited by
# subprocesses that tests spawn (e.g. `python -m waifu ...`).
os.environ["WAIFU_DISABLE_POWER"] = "1"

_POWER_PROGRAMS = {"shutdown", "rundll32"}
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


def _guarded_windll(name, *args, **kwargs):
    if "powrprof" in PurePath(str(name)).stem.lower():
        raise AssertionError("a test tried to load the real PowrProf.dll")
    return _REAL_WINDLL(name, *args, **kwargs)


def _refuse_registry_write(*args, **kwargs):
    raise AssertionError("a test tried to write the real Windows registry")


@pytest.fixture(autouse=True)
def _refuse_real_power_actions(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)
    if _REAL_WINDLL is not None:
        monkeypatch.setattr(ctypes, "WinDLL", _guarded_windll)


@pytest.fixture(autouse=True)
def _refuse_real_registry_writes(monkeypatch):
    if winreg is None:
        return
    for name in _REGISTRY_WRITERS:
        monkeypatch.setattr(winreg, name, _refuse_registry_write)
