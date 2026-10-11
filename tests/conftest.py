"""Suite-wide safety net: no test may power off, restart, or sleep the developer's PC.

A forgotten fake in an early PC test once put the real machine to sleep on every
suite run. These guards fail such a test loudly instead.
"""

import ctypes
import os
import subprocess
from pathlib import PurePath

import pytest

# Set at import time so it is in place before any test runs and is inherited by
# subprocesses that tests spawn (e.g. `python -m waifu ...`).
os.environ["WAIFU_DISABLE_POWER"] = "1"

_POWER_PROGRAMS = {"shutdown", "rundll32"}
_REAL_POPEN = subprocess.Popen
_REAL_WINDLL = getattr(ctypes, "WinDLL", None)


def _is_power_command(args: object) -> bool:
    if isinstance(args, (str, bytes)):
        text = args.decode() if isinstance(args, bytes) else args
        lowered = text.lower()
        return any(name in lowered for name in _POWER_PROGRAMS) or "setsuspendstate" in lowered
    first = next(iter(args), "") if args else ""  # type: ignore[call-overload]
    return PurePath(str(first)).stem.lower() in _POWER_PROGRAMS


class _GuardedPopen(_REAL_POPEN):  # type: ignore[misc, valid-type]
    def __init__(self, args, *pargs, **kwargs):
        if _is_power_command(args):
            raise AssertionError(f"a test tried to run a real power command: {args!r}")
        super().__init__(args, *pargs, **kwargs)


def _guarded_windll(name, *args, **kwargs):
    if "powrprof" in PurePath(str(name)).stem.lower():
        raise AssertionError("a test tried to load the real PowrProf.dll")
    return _REAL_WINDLL(name, *args, **kwargs)


@pytest.fixture(autouse=True)
def _refuse_real_power_actions(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)
    if _REAL_WINDLL is not None:
        monkeypatch.setattr(ctypes, "WinDLL", _guarded_windll)
