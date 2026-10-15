"""The PC watcher: no test reads the real PC, speaks, or pushes.

Every `PcWatcher` gets a fake `read`, a fake monotonic clock, a fake local time, a fake
announce, and a fake notifier. The thread tests run a real thread whose stop event is real
or waits at most a few milliseconds.
"""

import logging
import threading
import time
from datetime import datetime, timedelta

import pytest

from waifu.config import PcHealthConfig
from waifu.pc_health import Drive, GpuStatus, PcSnapshot
from waifu.pc_watch import PcWatcher

ROOMY = Drive("C:", 120.0, 465.1)
NEARLY_FULL = Drive("C:", 8.43, 465.1)
DISK_C_TEXT = "Master, disk C: tinggal 8,4 GB. Mungkin perlu dibersihkan sedikit."
NIGHT = datetime(2026, 10, 15, 21, 0)
MINUTE = 60.0
HOUR = 3600.0


def gpu(temp_c: int | None) -> GpuStatus:
    return GpuStatus("NVIDIA GeForce RTX 5060 Ti", temp_c, 97, 12.1, 15.9, 171.5)


def pc(*drives: Drive, gpu_status: GpuStatus | None = None) -> PcSnapshot:
    return PcSnapshot(35.0, 14.2, 31.9, drives or (ROOMY,), gpu_status, 5 * 3600)


def hot(temp_c: int = 88) -> PcSnapshot:
    return pc(gpu_status=gpu(temp_c))


QUIET = pc()  # room on C:, no GPU


def gpu_text(temp_c: int, owner: str = "Master") -> str:
    return (
        f"{owner}, GPU-nya sudah {temp_c}°C beberapa menit. "
        "Istirahat sebentar atau cek pendinginnya, ya."
    )


class FakeRead:
    """Stands in for pc_health.read_snapshot: hands out `snapshot`, or raises it."""

    def __init__(self, snapshot: PcSnapshot | Exception) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def __call__(self) -> PcSnapshot:
        self.calls += 1
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot


class FakeNotifier:
    def __init__(self, outcome: bool | Exception = True) -> None:
        self.sent: list[tuple[str, str, tuple[str, ...]]] = []
        self.outcome = outcome

    def send(self, title, message, *, tags=()):
        self.sent.append((title, message, tuple(tags)))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Rig:
    """A watcher and the fakes around it: the monotonic clock, the local time, the voice."""

    def __init__(
        self,
        snapshot: PcSnapshot | Exception = QUIET,
        *,
        owner_call: str = "Master",
        **config,
    ) -> None:
        self.read = FakeRead(snapshot)
        self.seconds = 1000.0
        self.now = NIGHT
        self.notifier = FakeNotifier()
        self.announced: list[tuple[str, str]] = []
        self.watcher = PcWatcher(
            self.read,
            PcHealthConfig(**config),
            announce=lambda text, emotion: self.announced.append((text, emotion)),
            notifier=self.notifier,
            owner_call=owner_call,
            clock=lambda: self.seconds,
            now=lambda: self.now,
        )

    def poll(
        self, snapshot: PcSnapshot | Exception | None = None, *, after_s: float = 0.0
    ) -> list[str]:
        """One poll `after_s` seconds (monotonic) after the last one, reading `snapshot`."""
        self.seconds += after_s
        if snapshot is not None:
            self.read.snapshot = snapshot
        return self.watcher.poll_once()

    def hot_for(self, minutes: int, temp_c: int = 88) -> list[str]:
        """Polls once a minute for `minutes` minutes after a first hot poll now: every notice."""
        notices = self.poll(hot(temp_c))
        for _ in range(minutes):
            notices += self.poll(hot(temp_c), after_s=MINUTE)
        return notices


# --- a nearly full drive -------------------------------------------------------------------


def test_a_nearly_full_drive_is_said_with_a_worried_sad_face_and_pushed():
    rig = Rig(pc(NEARLY_FULL))

    notices = rig.poll()

    assert notices == [DISK_C_TEXT]
    assert rig.announced == [(DISK_C_TEXT, "sad")]
    assert rig.notifier.sent == [("Cyrene", DISK_C_TEXT, ("computer",))]


@pytest.mark.parametrize("free_gb", [120.0, 10.0], ids=["roomy", "exactly at the line"])
def test_a_drive_not_below_the_line_stays_quiet(free_gb):
    rig = Rig(pc(Drive("C:", free_gb, 465.1)))

    assert rig.poll() == []
    assert (rig.announced, rig.notifier.sent) == ([], [])


@pytest.mark.parametrize(
    ("free_gb", "said"), [(9.96, "9,9"), (9.999, "9,9"), (0.04, "0,0")], ids=str
)
def test_the_free_space_is_rounded_down_so_a_drive_under_the_line_never_reads_as_the_line(
    free_gb, said
):
    rig = Rig(pc(Drive("C:", free_gb, 465.1)))

    assert rig.poll() == [f"Master, disk C: tinggal {said} GB. Mungkin perlu dibersihkan sedikit."]


def test_the_line_is_disk_warn_gb():
    rig = Rig(pc(Drive("D:", 31.5, 931.5)), disk_warn_gb=50)

    assert rig.poll() == ["Master, disk D: tinggal 31,5 GB. Mungkin perlu dibersihkan sedikit."]


def test_a_drive_is_told_once_per_local_date():
    rig = Rig(pc(NEARLY_FULL))

    first = rig.poll()
    rig.now = NIGHT.replace(hour=23, minute=59)
    same_day = rig.poll(after_s=3 * HOUR)
    rig.now = NIGHT.replace(hour=23, minute=59) + timedelta(minutes=2)
    next_day = rig.poll(after_s=2 * MINUTE)

    assert (first, same_day, next_day) == ([DISK_C_TEXT], [], [DISK_C_TEXT])


def test_a_drive_that_recovers_and_dips_again_the_same_day_stays_quiet():
    rig = Rig(pc(NEARLY_FULL))

    rig.poll()
    rig.poll(pc(ROOMY), after_s=HOUR)

    assert rig.poll(pc(NEARLY_FULL), after_s=HOUR) == []
    assert len(rig.announced) == 1


def test_the_once_per_day_rule_follows_the_local_date_not_the_monotonic_clock():
    rig = Rig(pc(NEARLY_FULL))
    rig.poll()

    two_days_on_the_clock = rig.poll(after_s=48 * HOUR)
    rig.now = NIGHT + timedelta(days=1)
    a_new_date_at_once = rig.poll()

    assert (two_days_on_the_clock, a_new_date_at_once) == ([], [DISK_C_TEXT])


def test_each_drive_is_told_on_its_own():
    rig = Rig(pc(NEARLY_FULL, Drive("D:", 500.0, 931.5)))
    rig.poll()

    later = rig.poll(pc(NEARLY_FULL, Drive("D:", 1.46, 931.5)), after_s=HOUR)

    assert later == ["Master, disk D: tinggal 1,4 GB. Mungkin perlu dibersihkan sedikit."]


# --- a GPU that stays hot ------------------------------------------------------------------


def test_a_gpu_hot_in_every_poll_for_gpu_warn_minutes_is_told_with_its_temperature_now():
    rig = Rig()

    quiet = [rig.poll(hot(86)), *(rig.poll(hot(t), after_s=MINUTE) for t in (90, 91))]
    told = rig.poll(hot(87), after_s=MINUTE)

    assert quiet == [[], [], []]
    assert told == [gpu_text(87)]
    assert rig.announced == [(gpu_text(87), "sad")]
    assert rig.notifier.sent == [("Cyrene", gpu_text(87), ("computer",))]


def test_the_streak_is_timed_by_the_clock_not_by_the_number_of_polls():
    rig = Rig()

    rig.poll(hot())
    too_soon = rig.poll(hot(), after_s=3 * MINUTE - 1)
    long_enough = rig.poll(hot(), after_s=1)

    assert (too_soon, long_enough) == ([], [gpu_text(88)])


def test_the_gpu_exactly_at_gpu_warn_c_counts_as_hot():
    assert Rig().hot_for(3, temp_c=85) == [gpu_text(85)]


def test_the_lines_are_gpu_warn_c_and_gpu_warn_minutes():
    rig = Rig(gpu_warn_c=70, gpu_warn_minutes=1)

    assert rig.hot_for(1, temp_c=72) == [gpu_text(72)]


def test_a_cool_poll_restarts_the_streak():
    rig = Rig()
    rig.hot_for(2)  # two minutes hot

    rig.poll(hot(84), after_s=30)  # one poll below the line
    restarted = rig.hot_for(2)
    told = rig.poll(hot(), after_s=MINUTE)

    assert (restarted, told) == ([], [gpu_text(88)])


@pytest.mark.parametrize(
    "unread",
    [pc(), pc(gpu_status=gpu(None)), RuntimeError("counters gone")],
    ids=["no gpu", "no temperature", "read failed"],
)
def test_a_poll_without_a_reading_restarts_the_streak(unread):
    rig = Rig()
    rig.hot_for(2)

    rig.poll(unread, after_s=30)
    restarted = rig.hot_for(2)
    told = rig.poll(hot(), after_s=MINUTE)

    assert (restarted, told) == ([], [gpu_text(88)])


def test_a_gpu_that_stays_hot_is_told_at_most_once_per_hour():
    rig = Rig()
    rig.hot_for(3)  # told now

    within_the_hour = [rig.poll(hot(), after_s=MINUTE) for _ in range(59)]
    an_hour_later = rig.poll(hot(93), after_s=MINUTE - 1)  # 59:59 after the notice
    rig.poll(hot(93), after_s=1)

    assert within_the_hour == [[]] * 59
    assert an_hour_later == []
    assert [text for text, _ in rig.announced] == [gpu_text(88), gpu_text(93)]


def test_a_new_streak_within_the_hour_of_the_last_notice_stays_quiet():
    rig = Rig()
    rig.hot_for(3)  # told at 0:00

    rig.poll(pc(gpu_status=gpu(60)), after_s=10 * MINUTE)
    quiet = rig.hot_for(10)  # hot again from 0:10 to 0:20
    rig.poll(pc(gpu_status=gpu(60)), after_s=10 * MINUTE)
    rig.seconds += 30 * MINUTE
    told = rig.hot_for(3)  # hot from 1:00, told at 1:03

    assert (quiet, told) == ([], [gpu_text(88)])


def test_the_once_per_hour_rule_follows_the_monotonic_clock_not_the_date():
    rig = Rig()
    rig.hot_for(3)

    rig.now = NIGHT + timedelta(days=1)
    a_new_date = rig.poll(hot(), after_s=MINUTE)

    assert a_new_date == []


# --- both, and what she calls the owner ----------------------------------------------------


def test_a_nearly_full_drive_and_a_hot_gpu_in_one_poll_are_two_notices():
    rig = Rig()
    rig.hot_for(2)

    notices = rig.poll(pc(NEARLY_FULL, gpu_status=gpu(88)), after_s=MINUTE)

    assert notices == [DISK_C_TEXT, gpu_text(88)]
    assert rig.announced == [(DISK_C_TEXT, "sad"), (gpu_text(88), "sad")]
    assert [message for _, message, _ in rig.notifier.sent] == notices


def test_the_notices_call_the_owner_what_she_calls_them():
    rig = Rig(owner_call="sayang")
    rig.hot_for(2)

    notices = rig.poll(pc(NEARLY_FULL, gpu_status=gpu(88)), after_s=MINUTE)

    assert notices == [
        "Sayang, disk C: tinggal 8,4 GB. Mungkin perlu dibersihkan sedikit.",
        gpu_text(88, owner="Sayang"),
    ]


def test_the_push_title_is_her_name():
    notifier = FakeNotifier()
    watcher = PcWatcher(
        FakeRead(pc(NEARLY_FULL)),
        PcHealthConfig(),
        announce=None,
        notifier=notifier,
        owner_call="Master",
        title="Hoshino",
        now=lambda: NIGHT,
    )

    watcher.poll_once()

    assert notifier.sent == [("Hoshino", DISK_C_TEXT, ("computer",))]


# --- failures ------------------------------------------------------------------------------


def test_a_failing_read_is_logged_tells_nothing_and_the_next_poll_reads_again(caplog):
    rig = Rig(OSError("GlobalMemoryStatusEx failed"))

    with caplog.at_level(logging.ERROR, logger="waifu.pc_watch"):
        failed = rig.poll()
    after = rig.poll(pc(NEARLY_FULL), after_s=MINUTE)

    assert (failed, after) == ([], [DISK_C_TEXT])
    assert "GlobalMemoryStatusEx failed" in caplog.text


def test_an_announcement_that_raises_is_logged_and_the_push_still_goes(caplog):
    def announce(text: str, emotion: str) -> None:
        raise RuntimeError("the voice loop is gone")

    notifier = FakeNotifier()
    watcher = PcWatcher(
        FakeRead(pc(NEARLY_FULL)),
        PcHealthConfig(),
        announce=announce,
        notifier=notifier,
        owner_call="Master",
        now=lambda: NIGHT,
    )

    with caplog.at_level(logging.ERROR, logger="waifu.pc_watch"):
        notices = watcher.poll_once()

    assert notices == [DISK_C_TEXT]
    assert notifier.sent == [("Cyrene", DISK_C_TEXT, ("computer",))]
    assert "the voice loop is gone" in caplog.text


@pytest.mark.parametrize("outcome", [False, OSError("no internet")], ids=["refused", "raised"])
def test_a_push_that_fails_or_raises_never_stops_anything(outcome, caplog):
    rig = Rig(pc(NEARLY_FULL, Drive("D:", 2.0, 931.5)))
    rig.notifier.outcome = outcome

    with caplog.at_level(logging.WARNING, logger="waifu.pc_watch"):
        notices = rig.poll()

    assert len(notices) == 2
    assert [text for text, _ in rig.announced] == notices
    assert [message for _, message, _ in rig.notifier.sent] == notices
    assert ("no internet" in caplog.text) == isinstance(outcome, OSError)


def test_without_a_voice_or_a_notifier_it_still_keeps_track():
    watcher = PcWatcher(
        FakeRead(pc(NEARLY_FULL)),
        PcHealthConfig(),
        announce=None,
        notifier=None,
        owner_call="Master",
        now=lambda: NIGHT,
    )

    assert watcher.poll_once() == [DISK_C_TEXT]
    assert watcher.poll_once() == []


# --- the thread ----------------------------------------------------------------------------


class SignallingRead(FakeRead):
    """Signals each read; raises `failures` times first."""

    def __init__(self, snapshot: PcSnapshot, failures: int = 0) -> None:
        super().__init__(snapshot)
        self.failures = failures
        self.read = threading.Event()

    def __call__(self) -> PcSnapshot:
        self.read.set()
        if self.failures:
            self.failures -= 1
            self.calls += 1
            raise RuntimeError("the counters hiccupped")
        return super().__call__()


class ShortWaits(threading.Event):
    """A stop event whose waits last a few milliseconds; records each timeout asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.asked.append(timeout)
        return super().wait(0.005)


def watcher_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "pc-watcher"]


def test_the_daemon_thread_polls_at_once_and_stop_wakes_it_at_once():
    read = SignallingRead(pc())
    watcher = PcWatcher(
        read, PcHealthConfig(poll_seconds=3600), announce=None, notifier=None, owner_call="Master"
    )

    watcher.start()
    try:
        assert read.read.wait(5)
        [thread] = watcher_threads()
        assert thread.daemon is True
    finally:
        stopping = time.monotonic()
        watcher.stop()
    # It stopped although the next poll was an hour away.

    assert time.monotonic() - stopping < 1
    assert watcher_threads() == []
    assert read.calls == 1


def test_the_thread_waits_poll_seconds_and_goes_on_after_a_failing_read(caplog):
    read = SignallingRead(pc(NEARLY_FULL), failures=2)
    waits = ShortWaits()
    told = threading.Event()
    spoken: list[str] = []

    def announce(text: str, emotion: str) -> None:
        spoken.append(text)
        told.set()

    watcher = PcWatcher(
        read,
        PcHealthConfig(poll_seconds=90),
        announce=announce,
        notifier=None,
        owner_call="Master",
        now=lambda: NIGHT,
        sleep_event=lambda: waits,
    )

    with caplog.at_level(logging.ERROR, logger="waifu.pc_watch"):
        watcher.start()
        try:
            assert told.wait(5)
        finally:
            watcher.stop()

    assert spoken == [DISK_C_TEXT]
    assert read.calls >= 3
    assert set(waits.asked) == {90}
    failures = [str(record.exc_info[1]) for record in caplog.records if record.exc_info]
    assert failures == ["the counters hiccupped"] * 2
    assert watcher_threads() == []


def test_starting_twice_runs_one_thread():
    waits = ShortWaits()
    watcher = PcWatcher(
        FakeRead(pc()),
        PcHealthConfig(),
        announce=None,
        notifier=None,
        owner_call="Master",
        sleep_event=lambda: waits,
    )

    watcher.start()
    try:
        watcher.start()
        assert len(watcher_threads()) == 1
    finally:
        watcher.stop()

    assert watcher_threads() == []
