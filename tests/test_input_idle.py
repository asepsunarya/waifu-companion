"""Reading how long ago the owner last touched the keyboard or mouse: no test reads the real input.

The reader takes Windows' two functions as fakes; the last tests put a fake WinDLL in place to
check what the real reader loads, and show that the suite refuses the real GetLastInputInfo.
"""

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from waifu.input_idle import LASTINPUTINFO, InputIdle, read_input_idle

WRAP = 2**32  # GetLastInputInfo's dwTime is the 32-bit tick count
DAY_MS = 24 * 60 * 60 * 1000


class FakeWindows:
    """GetLastInputInfo and GetTickCount64: the last input at `last_input` (the 32-bit tick count
    it was at) and the tick count at `tick`; `calls` holds the order they were called in."""

    def __init__(self, *, tick: int, last_input: int, works: bool = True) -> None:
        self.tick = tick
        self.last_input_tick = last_input
        self.works = works
        self.calls: list[str] = []

    def last_input(self, info: LASTINPUTINFO) -> int:
        self.calls.append("GetLastInputInfo")
        assert info.cbSize == ctypes.sizeof(LASTINPUTINFO)  # Windows checks it
        if not self.works:
            return 0
        info.dwTime = self.last_input_tick
        return 1

    def tick_count(self) -> int:
        self.calls.append("GetTickCount64")
        return self.tick

    def read(self) -> InputIdle:
        return read_input_idle(last_input=self.last_input, tick_count=self.tick_count)


def test_it_gives_the_tick_count_and_the_milliseconds_since_the_last_input():
    windows = FakeWindows(tick=3_600_000, last_input=3_595_500)

    assert windows.read() == InputIdle(tick_ms=3_600_000, idle_ms=4_500)


def test_it_reads_the_last_input_before_the_tick_count():
    # The other way round, an input between the two calls would be newer than the tick count.
    windows = FakeWindows(tick=3_600_000, last_input=3_600_000)

    windows.read()

    assert windows.calls == ["GetLastInputInfo", "GetTickCount64"]


def test_after_49_7_days_of_uptime_the_idle_time_is_counted_across_the_wrap():
    tick = 60 * DAY_MS  # past 2**32 ms: dwTime has wrapped once
    windows = FakeWindows(tick=tick, last_input=(tick - 7_000) % WRAP)

    assert windows.read() == InputIdle(tick_ms=tick, idle_ms=7_000)


def test_an_input_just_before_the_wrap_read_just_after_it_is_still_recent():
    windows = FakeWindows(tick=WRAP + 1_000, last_input=WRAP - 4_000)

    assert windows.read().idle_ms == 5_000


def test_a_last_input_a_moment_newer_than_the_tick_count_is_no_idle_time():
    # The input's time stamp can be a little ahead of the tick count read after it: that is now,
    # not an input 49 days ago.
    tick = 60 * DAY_MS
    windows = FakeWindows(tick=tick, last_input=(tick + 15) % WRAP)

    assert windows.read() == InputIdle(tick_ms=tick, idle_ms=0)


def test_a_refused_reading_is_an_os_error_and_the_tick_count_is_not_read():
    windows = FakeWindows(tick=3_600_000, last_input=0, works=False)

    with pytest.raises(OSError, match="GetLastInputInfo failed"):
        windows.read()

    assert windows.calls == ["GetLastInputInfo"]


# --- the real Windows functions ----------------------------------------------------------------


class FakeFunction:
    """One Windows function: refuses to run undeclared and records the signature it ran with."""

    def __init__(self, signatures: dict, name: str, behaviour) -> None:
        self.signatures = signatures
        self.name = name
        self.behaviour = behaviour
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        assert self.argtypes is not None, f"{self.name} called without argtypes"
        assert self.restype is not None, f"{self.name} called without restype"
        self.signatures[self.name] = (self.argtypes, self.restype)
        return self.behaviour(*args)


def test_the_reader_loads_user32_and_kernel32_with_last_error_and_documented_signatures(
    monkeypatch,
):
    windows = FakeWindows(tick=3_600_000, last_input=3_599_000)
    signatures: dict = {}
    # Only these two exist: looking anything else up fails.
    libraries = {
        "user32": SimpleNamespace(
            GetLastInputInfo=FakeFunction(signatures, "GetLastInputInfo", windows.last_input)
        ),
        "kernel32": SimpleNamespace(
            GetTickCount64=FakeFunction(signatures, "GetTickCount64", windows.tick_count)
        ),
    }
    loads: list = []

    def fake_windll(name, **kwargs):
        loads.append((name, kwargs))
        return libraries[name]

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)

    assert read_input_idle() == InputIdle(tick_ms=3_600_000, idle_ms=1_000)

    assert signatures == {
        "GetLastInputInfo": ([ctypes.POINTER(LASTINPUTINFO)], wintypes.BOOL),
        "GetTickCount64": ([], ctypes.c_ulonglong),
    }
    assert [name for name, _ in loads] == ["user32", "kernel32"]
    assert all(arguments == {"use_last_error": True} for _, arguments in loads)


def test_the_last_input_info_structure_is_windows_layout():
    assert [name for name, _ in LASTINPUTINFO._fields_] == ["cbSize", "dwTime"]
    assert ctypes.sizeof(LASTINPUTINFO) == 8


def test_the_real_reader_never_reaches_the_owner_s_input_in_the_suite(refused_system_calls):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("user32 only exists on Windows")

    with pytest.raises(AssertionError, match="read the owner's real keyboard and mouse activity"):
        read_input_idle()

    assert refused_system_calls == ["user32.GetLastInputInfo"]
    refused_system_calls.clear()  # this one was on purpose
