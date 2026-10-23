"""How long ago the owner last touched the keyboard or mouse, for the break reminder.

`read_input_idle` asks Windows for the time of the session's last keyboard or mouse input
(`GetLastInputInfo`) and for its tick count (`GetTickCount64`, milliseconds since Windows
started, counting through sleep), nothing more: never which key or where the mouse is, and no
hook, no injected input. The last input's time is the 32-bit tick count, which wraps every 49.7
days of uptime, so the idle time is their difference modulo 2**32.

user32 and kernel32 come through ctypes with Microsoft's documented signatures (winuser.h,
sysinfoapi.h), so there is no new dependency. They are looked up at every call, never at import,
so the test suite's guard on GetLastInputInfo always applies; both functions are injected, and
tests pass fakes.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Mapping
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

_WRAP = 2**32  # LASTINPUTINFO.dwTime is the low 32 bits of the tick count
# A difference this large is a last input a moment newer than the tick count read after it.
_AHEAD = 2**31


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_Signature = tuple[list[Any], Any]
_FUNCTIONS: Mapping[str, tuple[str, _Signature]] = {
    "GetLastInputInfo": ("user32", ([ctypes.POINTER(LASTINPUTINFO)], wintypes.BOOL)),
    "GetTickCount64": ("kernel32", ([], ctypes.c_ulonglong)),  # ULONGLONG
}


def _windows(name: str) -> Callable[..., Any]:
    """The Windows function `name` with its full signature, loaded only when it is called."""
    library, (argtypes, restype) = _FUNCTIONS[name]

    def call(*args: Any) -> Any:
        function = getattr(ctypes.WinDLL(library, use_last_error=True), name)
        function.argtypes = argtypes
        function.restype = restype
        return function(*args)

    return call


_get_last_input_info = _windows("GetLastInputInfo")
_get_tick_count_64 = _windows("GetTickCount64")


@dataclass(frozen=True)
class InputIdle:
    """Windows' tick count when read, and the milliseconds since the last input before it."""

    tick_ms: int
    idle_ms: int


def read_input_idle(
    *,
    last_input: Callable[[LASTINPUTINFO], int] = _get_last_input_info,
    tick_count: Callable[[], int] = _get_tick_count_64,
) -> InputIdle:
    """The tick count and the time since the session's last keyboard or mouse input.

    The last input is read first, so an input between the two calls is never newer than the
    tick count; one a moment newer all the same (the input's time stamp) is no idle time.
    OSError when Windows refuses the last input."""
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))  # Windows checks it
    if not last_input(info):
        raise OSError(f"GetLastInputInfo failed (Windows error {ctypes.get_last_error()})")
    tick = tick_count()
    idle = (tick - info.dwTime) % _WRAP
    return InputIdle(tick_ms=tick, idle_ms=0 if idle >= _AHEAD else idle)
