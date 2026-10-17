"""The names of the programs running on this PC, from one documented process snapshot.

Play time (`play_watch.py`) needs to know whether HSR, Genshin, or ZZZ is open, and nothing
more. Those games run kernel anti-cheat (mhyprot, HoYoKProtect), so this module only takes the
Toolhelp snapshot of the process list, which names every process without opening it, and never
touches a game process itself: no OpenProcess, QueryFullProcessImageNameW, GetModuleBaseNameW,
or EnumProcessModules, no module or thread snapshot, no WMI, no tasklist. Keep it that way.

kernel32 comes through ctypes with Microsoft's documented signatures (tlhelp32.h,
handleapi.h), so there is no new dependency. It is looked up at every call, never at import,
so the test suite's guard on CreateToolhelp32Snapshot always applies; every function is
injected, and tests pass fakes.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable, Mapping
from ctypes import wintypes
from typing import Any

TH32CS_SNAPPROCESS = 0x2  # every process; no modules, threads, or heaps
ERROR_NO_MORE_FILES = 18  # how Process32FirstW/NextW say the list has ended
MAX_PATH = 260
# CreateToolhelp32Snapshot's failure: the handle -1, as a HANDLE restype reads it.
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),  # ULONG_PTR
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),  # the executable's file name, no folder
    ]


_Signature = tuple[list[Any], Any]
_ENTRY = ctypes.POINTER(PROCESSENTRY32W)
_KERNEL32_FUNCTIONS: Mapping[str, _Signature] = {
    "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
    "Process32FirstW": ([wintypes.HANDLE, _ENTRY], wintypes.BOOL),
    "Process32NextW": ([wintypes.HANDLE, _ENTRY], wintypes.BOOL),
    "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
}


def _kernel32(name: str) -> Callable[..., Any]:
    """kernel32's `name` with its full signature, loaded only when it is called."""
    argtypes, restype = _KERNEL32_FUNCTIONS[name]

    def call(*args: Any) -> Any:
        function = getattr(ctypes.WinDLL("kernel32", use_last_error=True), name)
        function.argtypes = argtypes
        function.restype = restype
        return function(*args)

    return call


_create_snapshot = _kernel32("CreateToolhelp32Snapshot")
_process_first = _kernel32("Process32FirstW")
_process_next = _kernel32("Process32NextW")
_close_handle = _kernel32("CloseHandle")


def running_executables(
    *,
    snapshot: Callable[[int, int], int | None] = _create_snapshot,
    first: Callable[[int, PROCESSENTRY32W], int] = _process_first,
    next_: Callable[[int, PROCESSENTRY32W], int] = _process_next,
    close: Callable[[int], int] = _close_handle,
) -> frozenset[str]:
    """The executable names of the processes running now, case-folded ("starrail.exe").

    OSError when Windows refuses the snapshot or its walk stops before the end of the list;
    the snapshot is always closed."""
    handle = snapshot(TH32CS_SNAPPROCESS, 0)
    if handle is None or handle == INVALID_HANDLE_VALUE:
        raise OSError(f"CreateToolhelp32Snapshot failed (Windows error {ctypes.get_last_error()})")
    try:
        entry = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))  # Windows checks it
        names: set[str] = set()
        step, found = "Process32FirstW", first(handle, entry)
        while found:
            names.add(entry.szExeFile.casefold())
            step, found = "Process32NextW", next_(handle, entry)
        error = ctypes.get_last_error()
        if error != ERROR_NO_MORE_FILES:
            raise OSError(f"{step} failed (Windows error {error})")
        return frozenset(names)
    finally:
        close(handle)
