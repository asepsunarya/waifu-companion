"""PC health: the values and the readers. No test reads this PC or runs the real nvidia-smi.

Every Windows function, `shutil.disk_usage`, `subprocess.run`, and the CPU sample's sleep are
fakes passed in. The fixture below also refuses loading any real library and starting any
program, so a forgotten fake fails its test instead of reading the owner's PC.
"""

import collections
import ctypes
import dataclasses
import logging
import shutil
import subprocess
import threading
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from waifu import pc_health
from waifu.pc_health import (
    MEMORYSTATUSEX,
    CpuLoad,
    Drive,
    GpuStatus,
    PcSnapshot,
    fixed_drives,
    gpu_status,
    memory,
    read_snapshot,
    uptime_s,
)

GIB = 2**30
# Win32 values from Microsoft's documentation: GetDriveTypeW's results (fileapi.h), an error code.
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6
ERROR_ACCESS_DENIED = 5
CREATE_NO_WINDOW = 0x08000000  # process creation flags (winbase.h)
# MEMORYSTATUSEX (sysinfoapi.h): two DWORDs, then seven DWORDLONGs.
MEMORYSTATUSEX_SIZE = 64

NVIDIA_SMI = r"C:\Windows\System32\nvidia-smi.exe"
NVIDIA_SMI_ARGS = [
    NVIDIA_SMI,
    "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw",
    "--format=csv,noheader,nounits",
]
RTX_5060_TI_LINE = "NVIDIA GeForce RTX 5060 Ti, 61, 34, 5324, 16311, 88.42"
RTX_5060_TI = GpuStatus("NVIDIA GeForce RTX 5060 Ti", 61, 34, 5324 / 1024, 16311 / 1024, 88.42)

# Microsoft's documented signatures; ULONGLONG and DWORDLONG are unsigned 64-bit integers.
EXPECTED_SIGNATURES = {
    "GetSystemTimes": ([ctypes.POINTER(wintypes.FILETIME)] * 3, wintypes.BOOL),
    "GlobalMemoryStatusEx": ([ctypes.POINTER(MEMORYSTATUSEX)], wintypes.BOOL),
    "GetLogicalDrives": ([], wintypes.DWORD),
    "GetDriveTypeW": ([wintypes.LPCWSTR], wintypes.UINT),
    "QueryDosDeviceW": ([wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD], wintypes.DWORD),
    "GetTickCount64": ([], ctypes.c_ulonglong),
}

Usage = collections.namedtuple("Usage", "total used free")  # what shutil.disk_usage returns


@pytest.fixture(autouse=True)
def refuse_the_real_pc(monkeypatch):
    """A forgotten fake fails here instead of reading this PC's counters or running nvidia-smi."""

    def refuse_library(name, *args, **kwargs):
        raise AssertionError(f"a test tried to load the real {name}")

    def refuse_program(args, *pargs, **kwargs):
        raise AssertionError(f"a test tried to run a real program: {args!r}")

    monkeypatch.setattr(ctypes, "WinDLL", refuse_library, raising=False)
    monkeypatch.setattr(subprocess, "Popen", refuse_program)


@pytest.fixture(autouse=True)
def no_part_warned_yet(monkeypatch):
    monkeypatch.setattr(pc_health, "_warned_parts", set())


@pytest.fixture(autouse=True)
def no_nvidia_smi_stuck_yet(monkeypatch):
    monkeypatch.setattr(pc_health, "_stuck_readers", set())


@pytest.fixture
def access_denied(monkeypatch):
    """Windows' last error after a failed call, as `use_last_error` keeps it."""
    monkeypatch.setattr(ctypes, "get_last_error", lambda: ERROR_ACCESS_DENIED, raising=False)


def warnings_in(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]


def debug_messages_in(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG]


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG, logger="waifu.pc_health")
    return caplog


# --- values -----------------------------------------------------------------------------------


def test_a_snapshot_holds_its_parts_and_cannot_be_changed():
    gpu = GpuStatus("NVIDIA GeForce RTX 5060 Ti", 61, 34, 5.2, 15.9, 88.4)
    snapshot = PcSnapshot(12.5, 18.3, 31.9, (Drive("C:", 24.1, 465.1),), gpu, 7200)

    assert snapshot.drives[0].letter == "C:"
    assert snapshot.gpu is gpu
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.cpu_percent = 99.0  # type: ignore[misc]


def test_a_snapshot_allows_every_part_to_be_unreadable():
    snapshot = PcSnapshot(None, None, None, (), None, None)

    assert (snapshot.cpu_percent, snapshot.gpu, snapshot.uptime_s) == (None, None, None)


# --- CPU load ---------------------------------------------------------------------------------


def sample(idle: int, kernel_busy: int, user: int) -> tuple[int, int, int]:
    """GetSystemTimes' idle, kernel, and user time in 100 ns ticks: kernel time includes idle."""
    return idle, idle + kernel_busy, user


def later(
    times: tuple[int, int, int], idle: int, kernel_busy: int, user: int
) -> tuple[int, int, int]:
    """`times` after the given idle, busy kernel, and user ticks more."""
    before_idle, before_kernel, before_user = times
    return before_idle + idle, before_kernel + idle + kernel_busy, before_user + user


# Far above 2**32 ticks, so every value needs both FILETIME halves.
START = sample(idle=40 * 2**32 + 17, kernel_busy=5 * 2**32 + 3, user=10 * 2**32 + 9)
# 0.5 s on 10 logical processors: 5,000,000 ticks, 2,000,000 of them busy (40 %).
AFTER_FIRST_SAMPLE = later(START, idle=3_000_000, kernel_busy=500_000, user=1_500_000)
# Then 4,000,000 ticks, 3,000,000 busy (75 %).
AFTER_SECOND_CALL = later(AFTER_FIRST_SAMPLE, idle=1_000_000, kernel_busy=2_000_000, user=1_000_000)


class FakeSystemTimes:
    """GetSystemTimes over a list of (idle, kernel, user) samples; None is a failed call."""

    def __init__(self, *samples: tuple[int, int, int] | None) -> None:
        self.samples = list(samples)
        self.calls = 0

    def __call__(self, idle, kernel, user) -> int:
        assert all(isinstance(time, wintypes.FILETIME) for time in (idle, kernel, user))
        assert self.samples, "GetSystemTimes called more often than the test expected"
        self.calls += 1
        values = self.samples.pop(0)
        if values is None:
            return 0
        for filetime, ticks in zip((idle, kernel, user), values, strict=True):
            filetime.dwLowDateTime = ticks & 0xFFFFFFFF
            filetime.dwHighDateTime = ticks >> 32
        return 1


class FakeSleep:
    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


class FakeClock:
    """The monotonic clock; its `sleep` records each wait and moves the clock on by it."""

    def __init__(self) -> None:
        self.seconds = 1000.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.seconds

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.seconds += seconds


def test_cpu_load_is_the_busy_share_of_kernel_plus_user_time_kernel_including_idle():
    cpu = CpuLoad(system_times=FakeSystemTimes(START, AFTER_FIRST_SAMPLE), sleep=FakeSleep())

    assert cpu.percent() == 40.0


def test_the_first_call_samples_over_half_a_second_and_a_call_within_10_s_since_the_previous():
    clock = FakeClock()
    system_times = FakeSystemTimes(START, AFTER_FIRST_SAMPLE, AFTER_SECOND_CALL)
    cpu = CpuLoad(system_times=system_times, sleep=clock.sleep, clock=clock)

    first = cpu.percent()
    clock.seconds += 10
    second = cpu.percent()

    assert (first, second) == (40.0, 75.0)
    assert clock.waits == [0.5]
    assert system_times.calls == 3


# After the first call, a while at 3 % (100 * 7.2e9 ticks: two hours on 10 logical processors) ...
MOSTLY_IDLE_SINCE = later(
    AFTER_FIRST_SAMPLE, idle=97 * 7_200_000_000, kernel_busy=7_200_000_000, user=14_400_000_000
)
# ... then half a second at 90 %: a game Master has just started.
HALF_A_SECOND_BUSY = later(MOSTLY_IDLE_SINCE, idle=500_000, kernel_busy=1_500_000, user=3_000_000)


@pytest.mark.parametrize("gap_s", [10.5, 2 * 3600], ids=["just over 10 s", "two hours"])
def test_a_call_more_than_10_s_after_the_previous_one_samples_the_load_now(gap_s):
    # The share since the previous call would be 3 %: an average that hides the load right now.
    clock = FakeClock()
    system_times = FakeSystemTimes(START, AFTER_FIRST_SAMPLE, MOSTLY_IDLE_SINCE, HALF_A_SECOND_BUSY)
    cpu = CpuLoad(system_times=system_times, sleep=clock.sleep, clock=clock)
    cpu.percent()
    clock.seconds += gap_s

    share = cpu.percent()

    assert share == 90.0
    assert clock.waits == [0.5, 0.5]
    assert system_times.calls == 4


def test_cpu_times_crossing_into_the_high_filetime_word_still_give_the_right_share():
    start = (2**32 - 100, 2**32 - 100 + 50, 2**32 - 10)
    after = later(start, idle=300, kernel_busy=100, user=100)  # every value crosses 2**32
    cpu = CpuLoad(system_times=FakeSystemTimes(start, after), sleep=FakeSleep())

    assert cpu.percent() == 40.0


@pytest.mark.parametrize(
    ("after", "share"),
    [
        # Sampling skew: the idle time moved more than kernel and user time together.
        ((2000, 1900, 50), 0.0),
        # The idle time went back while kernel and user time moved on.
        ((900, 1500, 500), 100.0),
    ],
)
def test_the_cpu_load_never_goes_below_0_or_above_100(after, share):
    cpu = CpuLoad(system_times=FakeSystemTimes((1000, 1000, 0), after), sleep=FakeSleep())

    assert cpu.percent() == share


def test_a_call_within_the_same_timer_tick_samples_over_half_a_second_again():
    sleep = FakeSleep()
    after_wait = later(AFTER_FIRST_SAMPLE, idle=1_000_000, kernel_busy=2_000_000, user=1_000_000)
    system_times = FakeSystemTimes(START, AFTER_FIRST_SAMPLE, AFTER_FIRST_SAMPLE, after_wait)
    cpu = CpuLoad(system_times=system_times, sleep=sleep)
    cpu.percent()

    share = cpu.percent()  # nothing moved since the previous call: nothing to divide by

    assert share == 75.0
    assert sleep.waits == [0.5, 0.5]


def test_cpu_times_that_do_not_move_at_all_are_unreadable(logs):
    cpu = CpuLoad(system_times=FakeSystemTimes(START, START), sleep=FakeSleep())

    assert cpu.percent() is None
    assert warnings_in(logs) == ["Could not read the CPU load: its times did not move in 0.5 s"]


def test_a_failing_get_system_times_is_unreadable_and_warned_about_once(logs, access_denied):
    sleep = FakeSleep()
    cpu = CpuLoad(system_times=FakeSystemTimes(None, None, None), sleep=sleep)

    shares = [cpu.percent() for _ in range(3)]

    assert shares == [None, None, None]
    assert sleep.waits == []
    assert warnings_in(logs) == [
        "Could not read the CPU load: GetSystemTimes failed (Windows error 5)"
    ]
    assert len(debug_messages_in(logs)) == 2


def test_a_second_caller_waits_for_the_first_measurement_and_measures_since_it():
    # The tool (a voice or phone turn) and the watcher share one CpuLoad from their own threads.
    system_times = FakeSystemTimes(START, AFTER_FIRST_SAMPLE, AFTER_SECOND_CALL)
    sleeping, wake = threading.Event(), threading.Event()

    def first_sample_wait(seconds: float) -> None:
        sleeping.set()
        assert wake.wait(5)

    cpu = CpuLoad(system_times=system_times, sleep=first_sample_wait)
    shares: dict[str, float | None] = {}
    first = threading.Thread(target=lambda: shares.update(first=cpu.percent()), daemon=True)
    second = threading.Thread(target=lambda: shares.update(second=cpu.percent()), daemon=True)
    first.start()
    assert sleeping.wait(5)

    second.start()
    second.join(0.2)

    assert second.is_alive()
    assert system_times.calls == 1
    wake.set()
    first.join(5)
    second.join(5)
    assert shares == {"first": 40.0, "second": 75.0}


# --- memory -----------------------------------------------------------------------------------


class FakeMemoryStatus:
    """GlobalMemoryStatusEx: fills the structure, or fails as Windows does when dwLength is not
    the structure's size."""

    def __init__(self, total: int, available: int, *, fail: bool = False) -> None:
        self.total = total
        self.available = available
        self.fail = fail

    def __call__(self, status) -> int:
        assert isinstance(status, MEMORYSTATUSEX)
        if self.fail or status.dwLength != MEMORYSTATUSEX_SIZE:
            return 0
        status.dwMemoryLoad = round(100 * (self.total - self.available) / self.total)
        status.ullTotalPhys = self.total
        status.ullAvailPhys = self.available
        status.ullTotalPageFile = 2 * self.total
        status.ullAvailPageFile = self.total
        status.ullTotalVirtual = 2**47
        status.ullAvailVirtual = 2**46
        return 1


def test_memory_in_use_is_total_minus_available_in_gib():
    used, total = memory(memory_status=FakeMemoryStatus(total=34_091_302_912, available=12 * GIB))

    assert total == pytest.approx(31.75)
    assert used == pytest.approx(19.75)


def test_memory_status_structure_matches_sysinfoapi_h():
    offsets = [getattr(MEMORYSTATUSEX, name).offset for name, _ in MEMORYSTATUSEX._fields_]

    assert ctypes.sizeof(MEMORYSTATUSEX) == MEMORYSTATUSEX_SIZE
    assert offsets == [0, 4, 8, 16, 24, 32, 40, 48, 56]
    assert [name for name, _ in MEMORYSTATUSEX._fields_] == [
        "dwLength",
        "dwMemoryLoad",
        "ullTotalPhys",
        "ullAvailPhys",
        "ullTotalPageFile",
        "ullAvailPageFile",
        "ullTotalVirtual",
        "ullAvailVirtual",
        "ullAvailExtendedVirtual",
    ]


def test_a_failing_global_memory_status_is_unreadable_and_warned_about_once(logs, access_denied):
    status = FakeMemoryStatus(total=32 * GIB, available=16 * GIB, fail=True)

    results = [memory(memory_status=status) for _ in range(2)]

    assert results == [None, None]
    assert warnings_in(logs) == [
        "Could not read the memory: GlobalMemoryStatusEx failed (Windows error 5)"
    ]


# --- fixed drives -----------------------------------------------------------------------------


def drive_mask(*letters: str) -> int:
    """GetLogicalDrives' bitmask: bit 0 is drive A, bit 2 is drive C."""
    return sum(1 << (ord(letter) - ord("A")) for letter in letters)


# Every drive type GetDriveTypeW names, keyed by root path (the trailing backslash is required).
DRIVE_TYPES = {
    "Z:\\": DRIVE_FIXED,
    "C:\\": DRIVE_FIXED,
    "D:\\": DRIVE_CDROM,
    "E:\\": DRIVE_REMOVABLE,
    "F:\\": DRIVE_FIXED,
    "G:\\": DRIVE_REMOTE,
    "R:\\": DRIVE_RAMDISK,
    "U:\\": DRIVE_UNKNOWN,
    "V:\\": DRIVE_NO_ROOT_DIR,
}
DISK_USAGE = {
    # A "500 GB" SSD: 500,107,862,016 bytes, which Explorer shows as 465 GB.
    "C:\\": Usage(total=500_107_862_016, used=500_107_862_016 - 26 * GIB, free=26 * GIB),
    "F:\\": Usage(total=2 * 2**40, used=2**40, free=2**40),
    "Z:\\": Usage(total=8 * GIB, used=8 * GIB - GIB // 2, free=GIB // 2),
}


def no_disk_usage(root: str) -> Usage:
    raise AssertionError("no drive to read")


def volume(device: str) -> str:
    """QueryDosDeviceW's target for a drive letter of its own: a volume, not a folder."""
    return r"\Device\HarddiskVolume3"


# `subst S: C:\Users\master\Projects`: what QueryDosDeviceW gives for S: (an NT path).
SUBST_TARGET = r"\??\C:\Users\master\Projects"


def disk_usage_of(usage: dict[str, Usage | OSError]):
    def disk_usage(root: str) -> Usage:
        result = usage[root]
        if isinstance(result, OSError):
            raise result
        return result

    return disk_usage


def test_only_fixed_drives_are_listed_by_letter_with_their_space_in_gib():
    drives = fixed_drives(
        logical_drives=lambda: drive_mask(*(root[0] for root in DRIVE_TYPES)),
        drive_type=DRIVE_TYPES.__getitem__,
        disk_usage=disk_usage_of(DISK_USAGE),
        dos_device=volume,
    )

    assert [drive.letter for drive in drives] == ["C:", "F:", "Z:"]
    assert drives[0] == Drive("C:", 26.0, pytest.approx(465.761, abs=0.001))
    assert drives[1] == Drive("F:", 1024.0, 2048.0)
    assert drives[2] == Drive("Z:", 0.5, 8.0)


@pytest.mark.parametrize(
    ("free_bytes", "free_gb"),
    [
        (GIB, 1.0),
        (10**9, 0.931),  # a decimal gigabyte is less than a GiB
        (round(8.449 * GIB), 8.449),  # left unrounded: the tool rounds once, to 8.4
        (round(9.96 * GIB), 9.96),  # still below a 10 GB warning line
        (0, 0.0),
    ],
)
def test_drive_space_is_exact_gib_left_for_the_caller_to_round(free_bytes, free_gb):
    (drive,) = fixed_drives(
        logical_drives=lambda: drive_mask("C"),
        drive_type=lambda root: DRIVE_FIXED,
        disk_usage=lambda root: Usage(
            total=100 * GIB, used=100 * GIB - free_bytes, free=free_bytes
        ),
        dos_device=volume,
    )

    assert drive.free_gb == pytest.approx(free_gb, abs=0.0005)


def test_a_drive_that_cannot_be_read_is_skipped_and_warned_about_once(logs):
    usage = {**DISK_USAGE, "D:\\": PermissionError(13, "Access is denied")}  # a locked BitLocker D:

    def read() -> tuple[Drive, ...]:
        return fixed_drives(
            logical_drives=lambda: drive_mask("C", "D", "F"),
            drive_type=lambda root: DRIVE_FIXED,
            disk_usage=disk_usage_of(usage),
            dos_device=volume,
        )

    polls = [read() for _ in range(3)]

    assert all([drive.letter for drive in drives] == ["C:", "F:"] for drives in polls)
    assert warnings_in(logs) == ["Could not read drive D:: [Errno 13] Access is denied"]
    assert len(debug_messages_in(logs)) == 2


def test_a_subst_drive_is_not_listed_as_a_second_drive_and_its_folder_is_never_logged(logs):
    # GetDriveTypeW and disk_usage see C:'s volume through S:, so S: would be C: a second time.
    targets = {"C:": r"\Device\HarddiskVolume3", "S:": SUBST_TARGET}

    drives = fixed_drives(
        logical_drives=lambda: drive_mask("C", "S"),
        drive_type=lambda root: DRIVE_FIXED,
        disk_usage=lambda root: DISK_USAGE["C:\\"],
        dos_device=targets.__getitem__,
    )

    assert [drive.letter for drive in drives] == ["C:"]
    assert "master" not in logs.text  # a folder path can hold the user name


def test_a_letter_whose_dos_device_cannot_be_read_is_still_listed_and_warned_about_once(logs):
    def dos_device(device: str) -> str:
        if device == "D:":
            raise OSError("QueryDosDeviceW failed (Windows error 5)")
        return volume(device)

    polls = [
        fixed_drives(
            logical_drives=lambda: drive_mask("C", "D"),
            drive_type=lambda root: DRIVE_FIXED,
            disk_usage=lambda root: DISK_USAGE["C:\\"],
            dos_device=dos_device,
        )
        for _ in range(2)
    ]

    assert all([drive.letter for drive in drives] == ["C:", "D:"] for drives in polls)
    assert warnings_in(logs) == [
        "Could not read what drive D: links to: QueryDosDeviceW failed (Windows error 5)"
    ]


def test_a_failing_get_logical_drives_lists_no_drive_and_is_warned_about_once(logs, access_denied):
    def drive_type(root: str) -> int:
        raise AssertionError("no drive to ask about")

    polls = [
        fixed_drives(logical_drives=lambda: 0, drive_type=drive_type, disk_usage=no_disk_usage)
        for _ in range(2)
    ]

    assert polls == [(), ()]
    assert warnings_in(logs) == [
        "Could not read the drives: GetLogicalDrives failed (Windows error 5)"
    ]


# --- uptime -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("milliseconds", "seconds"),
    [
        (7_384_999, 7384),
        (60 * 86_400_000, 60 * 86_400),  # beyond GetTickCount's 49.7-day wrap at 2**32 ms
    ],
)
def test_uptime_is_whole_seconds_since_windows_started(milliseconds, seconds):
    assert uptime_s(tick_count=lambda: milliseconds) == seconds


def test_an_unreadable_uptime_is_none_and_warned_about_once(logs):
    def tick_count() -> int:
        raise OSError("kernel32 could not be loaded")

    assert [uptime_s(tick_count=tick_count) for _ in range(2)] == [None, None]
    assert warnings_in(logs) == ["Could not read the uptime: kernel32 could not be loaded"]


# --- GPU --------------------------------------------------------------------------------------


class FakeRun:
    """subprocess.run for nvidia-smi: records each call and answers with its output and exit
    code, or raises what the real one would."""

    def __init__(self, stdout: str = "", returncode: int = 0, error: Exception | None = None):
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(args, self.returncode, stdout=self.stdout, stderr="")


def read_gpu(run: FakeRun) -> GpuStatus | None:
    return gpu_status(run=run, nvidia_smi=lambda: NVIDIA_SMI)


def test_gpu_status_parses_the_first_line_of_nvidia_smi_csv():
    assert read_gpu(FakeRun(RTX_5060_TI_LINE + "\n")) == RTX_5060_TI


def test_nvidia_smi_is_asked_for_six_fields_without_header_or_units_hidden_and_with_a_timeout():
    run = FakeRun(RTX_5060_TI_LINE + "\n")

    read_gpu(run)

    ((args, kwargs),) = run.calls
    assert args == NVIDIA_SMI_ARGS
    assert kwargs["timeout"] == 5
    assert kwargs["creationflags"] == CREATE_NO_WINDOW  # no console flashes up during a game
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_only_the_first_gpu_is_read():
    second = "NVIDIA GeForce GTX 1060 6GB, 45, 0, 300, 6144, 9.87"

    assert read_gpu(FakeRun(f"{RTX_5060_TI_LINE}\n{second}\n")) == RTX_5060_TI


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "NVIDIA GeForce RTX 3050 Laptop GPU, 52, [N/A], 410, 4096, [Not Supported]",
            GpuStatus("NVIDIA GeForce RTX 3050 Laptop GPU", 52, None, 410 / 1024, 4.0, None),
        ),
        (
            "NVIDIA GeForce GTX 1050, N/A, 3, [Unknown Error], 2048, N/A",
            GpuStatus("NVIDIA GeForce GTX 1050", None, 3, None, 2.0, None),
        ),
        (
            "NVIDIA RTX A2000, [Insufficient Permissions], [N/A], [N/A], [N/A], [N/A]",
            GpuStatus("NVIDIA RTX A2000", None, None, None, None, None),
        ),
    ],
)
def test_a_field_nvidia_smi_cannot_report_is_none(line, expected):
    assert read_gpu(FakeRun(line + "\n")) == expected


def test_a_gpu_name_with_a_comma_keeps_its_comma():
    status = read_gpu(FakeRun("Quadro RTX 3000, with Max-Q Design, 70, 12, 1024, 6144, 35.5\n"))

    assert status == GpuStatus("Quadro RTX 3000, with Max-Q Design", 70, 12, 1.0, 6.0, 35.5)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "\n\n",
        "No devices were found\n",
        "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.\n",
        "a, b, c, d, e, f\n",
        "NVIDIA GeForce RTX 5060 Ti, 61C, 34 %, 5324 MiB, 16311 MiB, 88.42 W\n",  # units left in
        "NVIDIA GeForce RTX 5060 Ti, 61, 34, 5324, 16311\n",  # a field short
    ],
)
def test_output_that_is_not_one_gpu_line_is_unreadable(logs, output):
    assert read_gpu(FakeRun(output)) is None
    assert len(warnings_in(logs)) == 1
    assert warnings_in(logs)[0].startswith("Could not read the GPU: ")


@pytest.mark.parametrize(
    ("run", "reason"),
    [
        # No NVIDIA driver: nvidia-smi does not exist.
        (FakeRun(error=FileNotFoundError(2, "The system cannot find the file specified")), "file"),
        (FakeRun(error=subprocess.TimeoutExpired(NVIDIA_SMI_ARGS, 5)), "timed out after 5"),
        # Return code 9: the NVIDIA driver is not loaded.
        (FakeRun("NVIDIA-SMI has failed\n", returncode=9), "nvidia-smi exited with 9"),
    ],
)
def test_a_missing_failing_or_hung_nvidia_smi_is_unreadable_and_warned_about_once(
    logs, run, reason
):
    results = [read_gpu(run) for _ in range(3)]

    assert results == [None, None, None]
    (warning,) = warnings_in(logs)
    assert warning.startswith("Could not read the GPU: ")
    assert reason in warning
    assert len(debug_messages_in(logs)) == 2


class HungRun(FakeRun):
    """subprocess.run once nvidia-smi timed out but does not exit when killed (stuck in the
    driver): it waits for it without a limit, here until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.released = threading.Event()

    def __call__(self, args, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append((args, kwargs))
        assert self.released.wait(5)
        raise subprocess.TimeoutExpired(args, 5)


class ShortJoinThread(threading.Thread):
    """A real thread whose join waits at most 50 ms whatever it is asked; records each timeout."""

    def __init__(self, joins: list[float | None], **kwargs) -> None:
        super().__init__(**kwargs)
        self.joins = joins

    def join(self, timeout: float | None = None) -> None:
        self.joins.append(timeout)
        super().join(0.05)


def short_join_threads(joins: list[float | None], threads: list[threading.Thread]):
    """threading.Thread for gpu_status: makes ShortJoinThreads and keeps each one in `threads`."""

    def thread(**kwargs) -> threading.Thread:
        threads.append(ShortJoinThread(joins, **kwargs))
        return threads[-1]

    return thread


def test_a_nvidia_smi_that_never_exits_after_its_timeout_is_given_up_on_after_7_s(logs):
    run = HungRun()
    joins: list[float | None] = []
    threads: list[threading.Thread] = []

    try:
        status = gpu_status(
            run=run, nvidia_smi=lambda: NVIDIA_SMI, thread=short_join_threads(joins, threads)
        )
    finally:
        run.released.set()

    assert status is None
    assert joins == [7]
    [worker] = threads
    assert worker.daemon is True  # left behind, it never keeps Cyrene from exiting
    assert warnings_in(logs) == ["Could not read the GPU: nvidia-smi did not exit within 7 s"]
    threading.Thread.join(worker, 5)
    assert not worker.is_alive()


def test_while_a_given_up_nvidia_smi_still_runs_no_other_is_started(logs):
    run = HungRun()
    joins: list[float | None] = []
    threads: list[threading.Thread] = []
    thread = short_join_threads(joins, threads)

    try:
        # Three polls while the first nvidia-smi stays stuck in the driver.
        results = [
            gpu_status(run=run, nvidia_smi=lambda: NVIDIA_SMI, thread=thread) for _ in range(3)
        ]
    finally:
        run.released.set()

    assert results == [None, None, None]
    assert len(run.calls) == 1  # not one more stuck nvidia-smi and thread at every poll
    [worker] = threads
    assert joins == [7]  # and the later polls do not wait for it again
    assert warnings_in(logs) == ["Could not read the GPU: nvidia-smi did not exit within 7 s"]
    assert len(debug_messages_in(logs)) == 2
    threading.Thread.join(worker, 5)


def test_once_the_given_up_nvidia_smi_has_exited_the_next_call_runs_it_again():
    hung = HungRun()
    threads: list[threading.Thread] = []

    try:
        status = gpu_status(
            run=hung, nvidia_smi=lambda: NVIDIA_SMI, thread=short_join_threads([], threads)
        )
    finally:
        hung.released.set()
    [worker] = threads
    threading.Thread.join(worker, 5)

    assert status is None
    assert read_gpu(FakeRun(RTX_5060_TI_LINE)) == RTX_5060_TI


class UnstartableThread(threading.Thread):
    """A thread the OS has no room for, as when the process has too many already."""

    def start(self) -> None:
        raise RuntimeError("can't start new thread")


def test_a_thread_that_cannot_be_started_is_unreadable_not_raised(logs):
    run = FakeRun(RTX_5060_TI_LINE)

    status = gpu_status(run=run, nvidia_smi=lambda: NVIDIA_SMI, thread=UnstartableThread)

    assert status is None
    assert run.calls == []
    assert warnings_in(logs) == [
        "Could not read the GPU: could not start a thread for nvidia-smi: can't start new thread"
    ]


def test_nvidia_smi_is_found_on_the_path_first(monkeypatch):
    found = r"D:\Tools\nvidia-smi.EXE"
    monkeypatch.setattr(shutil, "which", lambda name: found if name == "nvidia-smi" else None)
    run = FakeRun(RTX_5060_TI_LINE)

    gpu_status(run=run)

    assert run.calls[0][0][0] == found


@pytest.mark.parametrize(
    ("system_root", "path"),
    [
        (r"E:\WINDOWS", r"E:\WINDOWS\System32\nvidia-smi.exe"),
        (None, r"C:\Windows\System32\nvidia-smi.exe"),
    ],
)
def test_nvidia_smi_off_the_path_is_looked_for_in_system32(monkeypatch, system_root, path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    if system_root is None:
        monkeypatch.delenv("SYSTEMROOT", raising=False)
    else:
        monkeypatch.setenv("SYSTEMROOT", system_root)
    run = FakeRun(RTX_5060_TI_LINE)

    gpu_status(run=run)

    assert run.calls[0][0][0] == path


# --- the snapshot -----------------------------------------------------------------------------


def test_a_snapshot_composes_every_part():
    drives = (Drive("C:", 24.1, 465.8), Drive("D:", 900.0, 931.5))

    snapshot = read_snapshot(
        cpu=SimpleNamespace(percent=lambda: 12.5),
        memory=lambda: (18.25, 31.75),
        drives=lambda: drives,
        gpu=lambda: RTX_5060_TI,
        uptime=lambda: 7200,
    )

    assert snapshot == PcSnapshot(12.5, 18.25, 31.75, drives, RTX_5060_TI, 7200)


def test_unreadable_memory_leaves_both_ram_numbers_out_and_the_rest_in():
    snapshot = read_snapshot(
        cpu=SimpleNamespace(percent=lambda: 3.0),
        memory=lambda: None,
        drives=lambda: (),
        gpu=lambda: None,
        uptime=lambda: 60,
    )

    assert snapshot == PcSnapshot(3.0, None, None, (), None, 60)


def test_every_part_that_stays_unreadable_is_warned_about_once_not_at_every_poll(
    logs, access_denied
):
    def no_kernel32() -> int:
        raise OSError("kernel32 could not be loaded")

    cpu = CpuLoad(system_times=lambda idle, kernel, user: 0, sleep=FakeSleep())
    parts = {
        "memory": lambda: memory(memory_status=lambda status: 0),
        "drives": lambda: fixed_drives(
            logical_drives=lambda: 0, drive_type=DRIVE_TYPES.__getitem__, disk_usage=no_disk_usage
        ),
        "gpu": lambda: read_gpu(FakeRun(error=FileNotFoundError(2, "No such file"))),
        "uptime": lambda: uptime_s(tick_count=no_kernel32),
    }

    polls = [read_snapshot(cpu=cpu, **parts) for _ in range(3)]

    assert polls == [PcSnapshot(None, None, None, (), None, None)] * 3
    assert sorted(warning.split(":")[0] for warning in warnings_in(logs)) == [
        "Could not read the CPU load",
        "Could not read the GPU",
        "Could not read the drives",
        "Could not read the memory",
        "Could not read the uptime",
    ]
    assert len(debug_messages_in(logs)) == 10


# --- the real readers' Windows functions ------------------------------------------------------


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


def fake_kernel32(monkeypatch, behaviours: dict) -> tuple[dict, list]:
    """kernel32 with `behaviours` as its functions: the signatures they ran with and each load."""
    signatures: dict = {}
    kernel32 = SimpleNamespace(
        **{
            name: FakeFunction(signatures, name, behaviour)
            for name, behaviour in behaviours.items()
        }
    )
    loads: list = []

    def fake_windll(name, **kwargs):
        loads.append((name, kwargs))
        return kernel32

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)
    return signatures, loads


def query_dos_device(targets: dict[str, str]):
    """QueryDosDeviceW: writes the device's target into the buffer, or fails (0) without one."""

    def call(device: str, buffer, size: int) -> int:
        assert size == len(buffer)
        if device not in targets:
            return 0
        buffer.value = targets[device]  # the first string is the mapping in use
        return len(targets[device]) + 2  # with the string's and the list's terminating nulls

    return call


def test_the_readers_load_kernel32_with_last_error_and_declare_every_function(monkeypatch):
    signatures, loads = fake_kernel32(
        monkeypatch,
        {
            "GetSystemTimes": FakeSystemTimes(START, AFTER_FIRST_SAMPLE),
            "GlobalMemoryStatusEx": FakeMemoryStatus(total=32 * GIB, available=24 * GIB),
            "GetLogicalDrives": lambda: drive_mask("C", "D", "S"),
            "GetDriveTypeW": {"C:\\": DRIVE_FIXED, "D:\\": DRIVE_CDROM, "S:\\": DRIVE_FIXED}.get,
            "QueryDosDeviceW": query_dos_device({"C:": volume("C:"), "S:": SUBST_TARGET}),
            "GetTickCount64": lambda: 3_600_000,
        },
    )

    snapshot = read_snapshot(
        cpu=CpuLoad(sleep=FakeSleep()),
        drives=lambda: fixed_drives(disk_usage=disk_usage_of(DISK_USAGE)),
        gpu=lambda: None,
    )

    assert snapshot == PcSnapshot(
        40.0, 8.0, 32.0, (Drive("C:", 26.0, 500_107_862_016 / GIB),), None, 3600
    )
    assert signatures == EXPECTED_SIGNATURES
    assert loads
    assert all(load == ("kernel32", {"use_last_error": True}) for load in loads)
    # The structures are passed as they are; ctypes adds the byref.
    wintypes.PFILETIME.from_param(wintypes.FILETIME())
    ctypes.POINTER(MEMORYSTATUSEX).from_param(MEMORYSTATUSEX())
    wintypes.LPWSTR.from_param(ctypes.create_unicode_buffer(8))


def test_a_failing_query_dos_device_keeps_the_letter(monkeypatch, logs, access_denied):
    fake_kernel32(monkeypatch, {"QueryDosDeviceW": query_dos_device({})})

    drives = fixed_drives(
        logical_drives=lambda: drive_mask("C"),
        drive_type=lambda root: DRIVE_FIXED,
        disk_usage=disk_usage_of(DISK_USAGE),
    )

    assert [drive.letter for drive in drives] == ["C:"]
    assert warnings_in(logs) == [
        "Could not read what drive C: links to: QueryDosDeviceW failed (Windows error 5)"
    ]
