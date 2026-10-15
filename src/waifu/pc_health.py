"""What Cyrene reads about the PC: load, memory, fixed drives, the NVIDIA GPU, and uptime.

Gigabytes are GiB (2**30 bytes), as Windows Explorer shows them. A part that cannot be read
is None, so one missing counter never hides the rest.

The CPU, memory, drives, and uptime come from kernel32 through ctypes, the GPU from
`nvidia-smi`, so there is no new dependency. Every Windows function, `shutil.disk_usage`, and
`subprocess.run` is injected: tests pass fakes and never read the real PC. Nothing here raises
to the caller; a part that cannot be read is logged at WARNING the first time and at DEBUG after
that, so a PC without an NVIDIA GPU does not fill the log at every poll.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import shutil
import string
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Drive:
    """A fixed drive, such as "C:", and its space."""

    letter: str
    free_gb: float
    total_gb: float


@dataclass(frozen=True)
class GpuStatus:
    """The first NVIDIA GPU as nvidia-smi reports it; a field it cannot report is None."""

    name: str
    temp_c: int | None
    load_percent: int | None
    vram_used_gb: float | None
    vram_total_gb: float | None
    power_w: float | None


@dataclass(frozen=True)
class PcSnapshot:
    """The PC at one moment."""

    cpu_percent: float | None
    ram_used_gb: float | None
    ram_total_gb: float | None
    drives: tuple[Drive, ...]
    gpu: GpuStatus | None
    uptime_s: int | None


BYTES_PER_GIB = 2**30
MIB_PER_GIB = 1024
FRESH_SAMPLE_S = 0.5
# An older previous sample would average the CPU load over too long a time to call it "now".
MAX_SAMPLE_AGE_S = 10.0
DRIVE_FIXED = 3  # GetDriveTypeW: a hard disk or SSD (fileapi.h)
# QueryDosDeviceW's buffer, in characters: the longest path the wide Windows functions take.
DOS_DEVICE_CHARS = 32_768
# QueryDosDeviceW gives a SUBST letter's folder as an NT path, such as "\??\C:\work"; a drive of
# its own links to its volume, such as "\Device\HarddiskVolume3".
_SUBST_PREFIX = "\\??\\"
NVIDIA_SMI_TIMEOUT_S = 5
# After that timeout subprocess.run kills nvidia-smi and then waits, without a limit, for it to
# exit, which one stuck in the NVIDIA driver may not do for a long time: give up on it after this.
NVIDIA_SMI_GIVE_UP_S = 7
# The fields of GpuStatus, in order; with "nounits" memory comes in MiB and power in watts.
NVIDIA_SMI_QUERY = "name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw"
# CREATE_NO_WINDOW keeps a console from flashing up; 0 is the only value POSIX accepts.
_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# nvidia-smi writes a value it cannot read as "N/A" or as its reason in brackets, such as
# "[N/A]", "[Not Supported]", or "[Unknown Error]".
_NOT_AVAILABLE = "N/A"
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        # DWORDLONG: an unsigned 64-bit integer.
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


_Signature = tuple[list[Any], Any]

# Microsoft's documented signatures (processthreadsapi.h, sysinfoapi.h, fileapi.h).
_KERNEL32_FUNCTIONS: Mapping[str, _Signature] = {
    "GetSystemTimes": ([wintypes.PFILETIME] * 3, wintypes.BOOL),
    "GlobalMemoryStatusEx": ([ctypes.POINTER(MEMORYSTATUSEX)], wintypes.BOOL),
    "GetLogicalDrives": ([], wintypes.DWORD),
    "GetDriveTypeW": ([wintypes.LPCWSTR], wintypes.UINT),
    "QueryDosDeviceW": ([wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD], wintypes.DWORD),
    "GetTickCount64": ([], ctypes.c_ulonglong),  # ULONGLONG
}


def _kernel32(name: str) -> Callable[..., Any]:
    """kernel32's `name` with its full signature, loaded only when it is called.

    ctypes.WinDLL is looked up at every call, never bound at import, so importing this module
    loads nothing and the test suite's guards on ctypes.WinDLL always apply.
    """
    argtypes, restype = _KERNEL32_FUNCTIONS[name]

    def call(*args: Any) -> Any:
        function = getattr(ctypes.WinDLL("kernel32", use_last_error=True), name)
        function.argtypes = argtypes
        function.restype = restype
        return function(*args)

    return call


_get_system_times = _kernel32("GetSystemTimes")
_global_memory_status_ex = _kernel32("GlobalMemoryStatusEx")
_get_logical_drives = _kernel32("GetLogicalDrives")
_get_drive_type = _kernel32("GetDriveTypeW")
_query_dos_device = _kernel32("QueryDosDeviceW")
_get_tick_count_64 = _kernel32("GetTickCount64")

# The parts already logged at WARNING. Process-wide, since the tool and the watcher read the same
# PC; a restart warns again.
_warned_parts: set[str] = set()
_warned_lock = threading.Lock()

# The nvidia-smi readers given up on that have not ended yet. While one runs no other nvidia-smi
# is started: one stuck in the NVIDIA driver would get the next stuck too, piling up a process and
# its threads at every poll. Process-wide, like _warned_parts.
_stuck_readers: set[threading.Thread] = set()
_stuck_lock = threading.Lock()


def _unreadable(part: str, reason: object) -> None:
    """Log that `part` could not be read: at WARNING the first time, at DEBUG after that."""
    with _warned_lock:
        first = part not in _warned_parts
        _warned_parts.add(part)
    logger.log(logging.WARNING if first else logging.DEBUG, "Could not read %s: %s", part, reason)


def _checked(result: int, function: str) -> int:
    """The result of a Win32 call; OSError with Windows' last error when it failed (0)."""
    if not result:
        raise OSError(f"{function} failed (Windows error {ctypes.get_last_error()})")
    return result


def _gib(size_bytes: int) -> float:
    return size_bytes / BYTES_PER_GIB


def _ticks(filetime: wintypes.FILETIME) -> int:
    """A FILETIME's 64-bit count of 100 ns ticks, from its two 32-bit halves."""
    return filetime.dwHighDateTime << 32 | filetime.dwLowDateTime


class _SystemTimes(NamedTuple):
    """GetSystemTimes summed over every logical processor, in 100 ns ticks, and when it was read."""

    idle: int
    total: int  # kernel + user; the kernel time includes the idle time
    at: float  # the monotonic clock


class CpuLoad:
    """How busy the CPU is now, from the system's idle, kernel, and user time.

    A call measures since the previous one when that was at most MAX_SAMPLE_AGE_S ago, and
    otherwise over a fresh half second, so the load is never an average over minutes or hours.
    The tool and the watcher share one from their own threads; a lock keeps their samples apart.
    """

    def __init__(
        self,
        *,
        system_times: Callable[
            [wintypes.FILETIME, wintypes.FILETIME, wintypes.FILETIME], int
        ] = _get_system_times,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._system_times = system_times
        self._sleep = sleep
        self._clock = clock
        self._previous: _SystemTimes | None = None
        self._lock = threading.Lock()

    def percent(self) -> float | None:
        """The busy share of the last few seconds, 0-100: since the previous call when that was
        at most 10 s ago, else (and at the first call) over a fresh 0.5 s."""
        with self._lock:
            try:
                return self._measure()
            except OSError as error:
                _unreadable("the CPU load", error)
                return None

    def _measure(self) -> float | None:
        current = self._sample()
        previous = self._previous
        if (
            previous is None
            or current.at - previous.at > MAX_SAMPLE_AGE_S
            or current.total == previous.total
        ):
            # No earlier sample, one too old to tell the load now, or not one timer tick since
            # it: sample over a fresh half second instead.
            previous = current
            self._sleep(FRESH_SAMPLE_S)
            current = self._sample()
        self._previous = current
        idle, total = current.idle - previous.idle, current.total - previous.total
        if total <= 0:
            _unreadable("the CPU load", f"its times did not move in {FRESH_SAMPLE_S:g} s")
            return None
        # Sampling skew can put the idle share a little outside the total.
        return min(max(100 * (total - idle) / total, 0.0), 100.0)

    def _sample(self) -> _SystemTimes:
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        _checked(self._system_times(idle, kernel, user), "GetSystemTimes")
        return _SystemTimes(_ticks(idle), _ticks(kernel) + _ticks(user), self._clock())


def memory(
    *, memory_status: Callable[[MEMORYSTATUSEX], int] = _global_memory_status_ex
) -> tuple[float, float] | None:
    """RAM in use and in total, in GiB; in use is all but the available (standby, free, zero)."""
    status = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))  # Windows checks it
    try:
        _checked(memory_status(status), "GlobalMemoryStatusEx")
    except OSError as error:
        _unreadable("the memory", error)
        return None
    return _gib(status.ullTotalPhys - status.ullAvailPhys), _gib(status.ullTotalPhys)


def _dos_device_target(device: str) -> str:
    """What the DOS device `device` ("S:", no trailing backslash) links to now; OSError when
    Windows cannot say."""
    target = ctypes.create_unicode_buffer(DOS_DEVICE_CHARS)
    _checked(_query_dos_device(device, target, len(target)), "QueryDosDeviceW")
    return target.value  # the first of its strings is the mapping in use


def fixed_drives(
    *,
    logical_drives: Callable[[], int] = _get_logical_drives,
    drive_type: Callable[[str], int] = _get_drive_type,
    disk_usage: Callable[[str], Any] = shutil.disk_usage,
    dos_device: Callable[[str], str] = _dos_device_target,
) -> tuple[Drive, ...]:
    """The fixed drives by letter with their free and total GiB.

    Removable, network, and optical drives and RAM disks are left out, and so are a SUBST
    letter (a folder of another drive, which reports that drive's type and space) and a fixed
    drive that cannot be read (a locked BitLocker drive).
    """
    try:
        mask = _checked(logical_drives(), "GetLogicalDrives")
        # Bit 0 is drive A; GetDriveTypeW needs the root's trailing backslash.
        roots = [
            f"{letter}:\\" for bit, letter in enumerate(string.ascii_uppercase) if mask >> bit & 1
        ]
        fixed = [root for root in roots if drive_type(root) == DRIVE_FIXED]
    except OSError as error:
        _unreadable("the drives", error)
        return ()
    drives = []
    for root in fixed:
        letter = root[:2]
        if _is_subst(letter, dos_device):
            continue
        try:
            usage = disk_usage(root)
        except OSError as error:
            _unreadable(f"drive {letter}", error)
            continue
        drives.append(Drive(letter, _gib(usage.free), _gib(usage.total)))
    return tuple(drives)


def _is_subst(letter: str, dos_device: Callable[[str], str]) -> bool:
    """Whether `letter` ("S:") is a SUBST alias of a folder. A letter Windows cannot say this
    about counts as a drive of its own, so a failing call never hides a real drive. The folder
    is never logged: its path can hold the user name."""
    try:
        return dos_device(letter).startswith(_SUBST_PREFIX)
    except OSError as error:
        _unreadable(f"what drive {letter} links to", error)
        return False


def uptime_s(*, tick_count: Callable[[], int] = _get_tick_count_64) -> int | None:
    """Whole seconds since Windows last restarted: neither sleep nor a Fast Startup shutdown (a
    hibernation) resets it, so it is not the time since the PC was turned on."""
    try:
        return tick_count() // 1000
    except OSError as error:
        _unreadable("the uptime", error)
        return None


def _nvidia_smi_path() -> str:
    """nvidia-smi on the PATH, else where NVIDIA's driver installs it."""
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return shutil.which("nvidia-smi") or os.path.join(system_root, "System32", "nvidia-smi.exe")


def gpu_status(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    nvidia_smi: Callable[[], str] = _nvidia_smi_path,
    thread: Callable[..., threading.Thread] = threading.Thread,
) -> GpuStatus | None:
    """The first NVIDIA GPU; None without an NVIDIA driver or when nvidia-smi fails or hangs.

    nvidia-smi runs in a daemon `thread` that is given up on after NVIDIA_SMI_GIVE_UP_S, so one
    that does not exit when killed holds up a voice turn or the watcher no longer than that; until
    it has exited, a call is None at once and starts no other nvidia-smi.
    """

    def read() -> subprocess.CompletedProcess[str]:
        return run(
            [nvidia_smi(), f"--query-gpu={NVIDIA_SMI_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=NVIDIA_SMI_TIMEOUT_S,
            creationflags=_CREATION_FLAGS,
        )

    try:
        result = _bounded(read, thread)
    except (OSError, subprocess.SubprocessError) as error:  # not found, timed out, or hung
        _unreadable("the GPU", error)
        return None
    if result.returncode != 0:
        _unreadable("the GPU", f"nvidia-smi exited with {result.returncode}")
        return None
    try:
        return _parse_gpu(result.stdout)
    except ValueError as error:
        _unreadable("the GPU", error)
        return None


def _bounded(
    read: Callable[[], subprocess.CompletedProcess[str]],
    thread: Callable[..., threading.Thread],
) -> subprocess.CompletedProcess[str]:
    """What `read` returns or raises in a daemon thread, or TimeoutError (an OSError) when it
    has not finished within NVIDIA_SMI_GIVE_UP_S. The thread is then left to end on its own or
    with the process, and until it has ended a call raises TimeoutError at once, without
    starting `read` again. OSError when no thread can be started."""
    with _stuck_lock:
        _stuck_readers.difference_update(
            {reader for reader in _stuck_readers if not reader.is_alive()}
        )
        if _stuck_readers:
            raise TimeoutError("the nvidia-smi given up on earlier has not exited yet")
    outcome: list[subprocess.CompletedProcess[str]] = []
    failure: list[Exception] = []

    def target() -> None:
        try:
            outcome.append(read())
        except Exception as error:  # raised again in the caller's thread
            failure.append(error)

    worker = thread(target=target, name="nvidia-smi", daemon=True)
    try:
        worker.start()
    except RuntimeError as error:  # the OS has no room for another thread
        raise OSError(f"could not start a thread for nvidia-smi: {error}") from error
    worker.join(NVIDIA_SMI_GIVE_UP_S)
    if failure:
        raise failure[0]
    if not outcome:
        with _stuck_lock:
            _stuck_readers.add(worker)
        raise TimeoutError(f"nvidia-smi did not exit within {NVIDIA_SMI_GIVE_UP_S} s")
    return outcome[0]


def _parse_gpu(output: str) -> GpuStatus:
    """The first GPU's line of nvidia-smi's CSV; ValueError when it is not one."""
    lines = output.strip().splitlines()
    if not lines:
        raise ValueError("nvidia-smi listed no GPU")
    fields = [field.strip() for field in lines[0].rsplit(",", 5)]  # a name may hold a comma
    if len(fields) != 6:
        raise ValueError(f"unexpected nvidia-smi output {lines[0][:80]!r}")
    name, temp, load, vram_used, vram_total, power = fields
    temp_c, load_percent = _reading(temp), _reading(load)
    used_mib, total_mib = _reading(vram_used), _reading(vram_total)
    return GpuStatus(
        name=name,
        temp_c=None if temp_c is None else round(temp_c),
        load_percent=None if load_percent is None else round(load_percent),
        vram_used_gb=None if used_mib is None else used_mib / MIB_PER_GIB,
        vram_total_gb=None if total_mib is None else total_mib / MIB_PER_GIB,
        power_w=_reading(power),
    )


def _reading(field: str) -> float | None:
    """A number nvidia-smi reported, None for one it could not; ValueError for anything else."""
    if field == _NOT_AVAILABLE or (field.startswith("[") and field.endswith("]")):
        return None
    if not _NUMBER.fullmatch(field):
        raise ValueError(f"unexpected nvidia-smi value {field[:40]!r}")
    return float(field)


def read_snapshot(
    *,
    cpu: CpuLoad,
    memory: Callable[[], tuple[float, float] | None] = memory,
    drives: Callable[[], tuple[Drive, ...]] = fixed_drives,
    gpu: Callable[[], GpuStatus | None] = gpu_status,
    uptime: Callable[[], int | None] = uptime_s,
) -> PcSnapshot:
    """The PC right now; every part that cannot be read is None (no drives: an empty tuple)."""
    ram = memory()
    ram_used_gb, ram_total_gb = (None, None) if ram is None else ram
    return PcSnapshot(
        cpu_percent=cpu.percent(),
        ram_used_gb=ram_used_gb,
        ram_total_gb=ram_total_gb,
        drives=drives(),
        gpu=gpu(),
        uptime_s=uptime(),
    )
