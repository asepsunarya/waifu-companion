"""The process lister: no test lists the real processes (tests/conftest.py refuses the
snapshot); every Windows function is a fake that walks a made-up process list."""

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from waifu.processes import (
    ERROR_NO_MORE_FILES,
    INVALID_HANDLE_VALUE,
    PROCESSENTRY32W,
    TH32CS_SNAPPROCESS,
    running_executables,
)

HANDLE = 0x5A4
ERROR_ACCESS_DENIED = 5
ERROR_NOT_ENOUGH_MEMORY = 8


class FakeSnapshot:
    """The four snapshot functions over `names`, in order. A walk ends with `end_error` once the
    names are used up, or `fail_at` (an index) fails with that error instead of giving it."""

    def __init__(
        self,
        names: list[str],
        *,
        handle: int | None = HANDLE,
        end_error: int = ERROR_NO_MORE_FILES,
        fail_at: tuple[int, int] | None = None,
    ) -> None:
        self.names = names
        self.handle = handle
        self.end_error = end_error
        self.fail_at = fail_at
        self.taken: list[tuple[int, int]] = []
        self.closed: list[int] = []
        self.sizes: list[int] = []
        self._next = 0

    def snapshot(self, flags: int, process_id: int) -> int | None:
        self.taken.append((flags, process_id))
        if self.handle == INVALID_HANDLE_VALUE:
            ctypes.set_last_error(ERROR_NOT_ENOUGH_MEMORY)
        return self.handle

    def first(self, handle: int, entry: PROCESSENTRY32W) -> int:
        assert handle == self.handle
        self.sizes.append(entry.dwSize)
        self._next = 0
        return self._give(entry)

    def next_(self, handle: int, entry: PROCESSENTRY32W) -> int:
        assert handle == self.handle
        return self._give(entry)

    def close(self, handle: int) -> int:
        self.closed.append(handle)
        return 1

    def _give(self, entry: PROCESSENTRY32W) -> int:
        index = self._next
        if self.fail_at is not None and self.fail_at[0] == index:
            ctypes.set_last_error(self.fail_at[1])
            return 0
        if index >= len(self.names):
            ctypes.set_last_error(self.end_error)
            return 0
        entry.szExeFile = self.names[index]
        self._next += 1
        return 1

    def list(self) -> frozenset[str]:
        return running_executables(
            snapshot=self.snapshot, first=self.first, next_=self.next_, close=self.close
        )


def test_it_gives_every_running_executable_name_case_folded_from_one_snapshot():
    fake = FakeSnapshot(
        ["[System Process]", "System", "explorer.exe", "StarRail.exe", "HYP.exe", "StarRail.exe"]
    )

    names = fake.list()

    assert names == frozenset(
        {"[system process]", "system", "explorer.exe", "starrail.exe", "hyp.exe"}
    )
    assert fake.taken == [(TH32CS_SNAPPROCESS, 0)]  # every process, once
    assert fake.closed == [HANDLE]


def test_the_entry_carries_its_size_as_windows_requires():
    fake = FakeSnapshot(["explorer.exe"])

    fake.list()

    assert fake.sizes == [ctypes.sizeof(PROCESSENTRY32W)]


def test_the_entry_has_the_documented_layout():
    # tlhelp32.h: 568 bytes on 64-bit Windows (as measured on the owner's PC), 556 on 32-bit.
    expected = 568 if ctypes.sizeof(ctypes.c_void_p) == 8 else 556
    assert ctypes.sizeof(PROCESSENTRY32W) == expected
    assert PROCESSENTRY32W.szExeFile.size == 260 * ctypes.sizeof(wintypes.WCHAR)


def test_an_empty_list_is_no_executable():
    fake = FakeSnapshot([])

    assert fake.list() == frozenset()
    assert fake.closed == [HANDLE]


@pytest.mark.parametrize("handle", [INVALID_HANDLE_VALUE, None])
def test_a_snapshot_windows_refuses_is_an_os_error_and_nothing_is_closed(handle):
    fake = FakeSnapshot(["explorer.exe"], handle=handle)

    with pytest.raises(OSError, match="CreateToolhelp32Snapshot"):
        fake.list()

    assert fake.closed == []


def test_a_walk_that_cannot_start_is_an_os_error_and_the_snapshot_is_closed():
    fake = FakeSnapshot(["explorer.exe"], fail_at=(0, ERROR_ACCESS_DENIED))

    with pytest.raises(OSError, match=r"Process32FirstW failed \(Windows error 5\)"):
        fake.list()

    assert fake.closed == [HANDLE]


def test_a_walk_that_breaks_off_is_an_os_error_and_the_snapshot_is_closed():
    fake = FakeSnapshot(["explorer.exe", "StarRail.exe"], fail_at=(1, ERROR_NOT_ENOUGH_MEMORY))

    with pytest.raises(OSError, match=r"Process32NextW failed \(Windows error 8\)"):
        fake.list()

    assert fake.closed == [HANDLE]


def test_a_walk_ending_with_another_error_than_the_end_of_the_list_is_an_os_error():
    fake = FakeSnapshot(["explorer.exe"], end_error=ERROR_ACCESS_DENIED)

    with pytest.raises(OSError, match="Process32NextW"):
        fake.list()

    assert fake.closed == [HANDLE]


def test_the_snapshot_is_closed_when_a_function_raises():
    fake = FakeSnapshot(["explorer.exe"])

    def broken_next(handle, entry):
        raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError):
        running_executables(
            snapshot=fake.snapshot, first=fake.first, next_=broken_next, close=fake.close
        )

    assert fake.closed == [HANDLE]


# --- the real Windows functions ----------------------------------------------------------------


class FakeFunction:
    """One kernel32 function: refuses to run undeclared and records the signature it ran with."""

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


def test_the_lister_loads_kernel32_with_last_error_and_only_the_snapshot_functions(monkeypatch):
    fake = FakeSnapshot(["explorer.exe", "GenshinImpact.exe"])
    signatures: dict = {}
    # Only these four exist: looking anything else up (OpenProcess, a module snapshot) fails.
    kernel32 = SimpleNamespace(
        CreateToolhelp32Snapshot=FakeFunction(signatures, "snapshot", fake.snapshot),
        Process32FirstW=FakeFunction(signatures, "first", fake.first),
        Process32NextW=FakeFunction(signatures, "next", fake.next_),
        CloseHandle=FakeFunction(signatures, "close", fake.close),
    )
    loads: list = []

    def fake_windll(name, **kwargs):
        loads.append((name, kwargs))
        return kernel32

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)

    assert running_executables() == frozenset({"explorer.exe", "genshinimpact.exe"})

    entry = ctypes.POINTER(PROCESSENTRY32W)
    assert signatures == {
        "snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        "first": ([wintypes.HANDLE, entry], wintypes.BOOL),
        "next": ([wintypes.HANDLE, entry], wintypes.BOOL),
        "close": ([wintypes.HANDLE], wintypes.BOOL),
    }
    assert {load for load, _ in loads} == {"kernel32"}
    assert all(arguments == {"use_last_error": True} for _, arguments in loads)


def test_the_real_lister_never_reaches_the_real_process_list_in_the_suite(refused_system_calls):
    if not hasattr(ctypes, "WinDLL"):
        pytest.skip("kernel32 only exists on Windows")

    with pytest.raises(AssertionError, match="list the real processes"):
        running_executables()

    assert refused_system_calls == ["kernel32.CreateToolhelp32Snapshot"]
    refused_system_calls.clear()  # this one was on purpose
