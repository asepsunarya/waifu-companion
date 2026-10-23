"""The break reminder: continuous activity at the PC, and the watcher that says the line.

No test reads the real keyboard or mouse (tests/conftest.py refuses GetLastInputInfo), sleeps,
speaks, or pushes. A poll is given as its minute since BOOT and the seconds since the last input
then; the tick count and the wall clock move together unless a test splits them.
"""

import logging
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timedelta

from waifu.break_watch import POLL_S, Activity, BreakWatcher, follow_activity
from waifu.config import BreaksConfig
from waifu.input_idle import InputIdle

EVERY = timedelta(minutes=60)
RESET = timedelta(minutes=5)
BOOT = 3_000_000_000  # the tick count at minute 0: about 35 days of uptime
EVENING = datetime(2026, 10, 24, 20, 0)  # the PC's local time at minute 0
MINUTE_MS = 60_000


def tick(minute: float) -> int:
    return BOOT + round(minute * MINUTE_MS)


def reading(minute: float, idle_s: float = 0) -> InputIdle:
    return InputIdle(tick_ms=tick(minute), idle_ms=round(idle_s * 1000))


def wall(minute: float) -> datetime:
    return EVENING + timedelta(minutes=minute)


def follow(activity: Activity | None, minute: float, idle_s: float = 0, **moved):
    return follow_activity(
        activity, reading(minute, idle_s), moved.get("now", wall(minute)), every=EVERY, reset=RESET
    )


def at_the_keyboard(start: float, end: float, step: float = 0.5) -> list[tuple[float, float]]:
    """Polls every `step` minutes from `start` through `end`, each right after an input."""
    count = round((end - start) / step)
    return [(start + n * step, 0) for n in range(count + 1)]


def counted_through(minute: float) -> Activity:
    """The stretch after polls every half minute from 0 through `minute`, each after an input."""
    activity = None
    for at, idle_s in at_the_keyboard(0, minute):
        activity, _ = follow(activity, at, idle_s)
    assert activity is not None
    return activity


def lines_told(polls: Iterable[tuple[float, float]]) -> list[tuple[float, int]]:
    """Follows `polls` (minute, seconds since the last input) from not counting: the minute of
    each line due and the minutes it tells."""
    activity, told = None, []
    for minute, idle_s in polls:
        activity, minutes = follow(activity, minute, idle_s)
        if minutes is not None:
            told.append((minute, minutes))
    return told


# --- continuous activity -----------------------------------------------------------------------


def test_counting_starts_at_a_poll_right_after_an_input():
    assert follow(None, 0, idle_s=10) == (Activity(tick(0), tick(60), tick(0), wall(0)), None)


def test_nothing_is_counted_while_the_keyboard_and_mouse_rest_a_break_s_length():
    assert follow(None, 0, idle_s=300) == (None, None)


def test_a_pause_shorter_than_a_break_keeps_the_count():
    activity, _ = follow(None, 0)

    assert follow(activity, 4.5, idle_s=299) == (
        Activity(tick(0), tick(60), tick(4.5), wall(4.5)),
        None,
    )


def test_a_pause_of_a_break_s_length_ends_the_count():
    activity, _ = follow(None, 0)

    assert follow(activity, 5, idle_s=300) == (None, None)


def test_after_an_hour_at_the_keyboard_the_line_is_due_once():
    assert lines_told(at_the_keyboard(0, 119.5)) == [(60, 60)]


def test_every_hour_after_it_another_line_tells_the_whole_time():
    assert lines_told(at_the_keyboard(0, 180)) == [(60, 60), (120, 120), (180, 180)]


def test_the_lines_keep_to_the_hour_however_late_a_poll_comes():
    # Polls every three and a half minutes: the first after the hour is at 63, yet the next is
    # due at 120, not at 123, and tells two hours, not two hours and three minutes.
    polls = at_the_keyboard(0, 126, step=3.5)

    assert lines_told(polls) == [(63, 60), (122.5, 120)]


def test_a_line_waits_for_an_input_at_or_after_its_time():
    # The last input at 59:50: the poll at 60:00 is ten seconds past it, so nothing yet.
    polls = [*at_the_keyboard(0, 59.5), (60, 10), (60.5, 0)]

    assert lines_told(polls) == [(60.5, 60)]


def test_a_line_due_after_master_got_up_is_never_said_to_an_empty_chair():
    # He leaves at 59; the polls see the pause grow until it is a break at 64.
    away = [(59 + n * 0.5, n * 30) for n in range(1, 11)]
    back = at_the_keyboard(70, 130)  # the count starts again when he is back

    assert lines_told([*at_the_keyboard(0, 59), *away, *back]) == [(130, 60)]


def test_back_within_a_break_s_length_the_line_comes_with_the_first_input():
    away = [(59.5, 30), (60, 60), (61, 120), (61.5, 150)]

    assert lines_told([*at_the_keyboard(0, 59), *away, (62, 0)]) == [(62, 60)]


# --- sleeps and stalls -------------------------------------------------------------------------


def test_a_poll_longer_than_a_break_after_the_last_one_starts_the_count_again():
    # The PC slept from 50 to 56 and a key press woke it: the tick count went on through sleep.
    polls = [*at_the_keyboard(0, 50), *at_the_keyboard(56, 120)]

    assert lines_told(polls) == [(116, 60)]


def test_a_poll_a_break_s_length_after_the_last_one_keeps_the_count():
    polls = [*at_the_keyboard(0, 50), *at_the_keyboard(55, 70)]

    assert lines_told(polls) == [(60, 60)]


def test_a_poll_long_after_the_last_one_by_the_tick_count_alone_starts_again():
    # The wall clock was set back meanwhile: the tick count still shows the gap.
    after_sleep, _ = follow(counted_through(50), 56, now=wall(50.5))

    assert after_sleep == Activity(tick(56), tick(116), tick(56), wall(50.5))


def test_a_poll_long_after_the_last_one_by_the_wall_clock_alone_starts_again():
    # Should the tick count ever stop in sleep, the wall clock still shows the gap.
    after_sleep, _ = follow(counted_through(50), 50.5, now=wall(50.5) + timedelta(hours=2))

    assert after_sleep == Activity(
        tick(50.5), tick(110.5), tick(50.5), wall(50.5) + timedelta(hours=2)
    )


def test_a_wall_clock_set_back_a_little_keeps_the_count():
    corrected, _ = follow(counted_through(50), 50.5, now=wall(50) - timedelta(seconds=20))

    assert corrected is not None and corrected.since == tick(0)


# --- the watcher -------------------------------------------------------------------------------

ONE_HOUR = "Sayang, sudah satu jam di depan layar. Istirahatkan matamu sebentar dan minum air, ya."
TWO_HOURS = "Sayang, sudah dua jam di depan layar. Istirahatkan matamu sebentar dan minum air, ya."


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
    """A watcher and the fakes around it: the activity, the PC's local time (`start` at minute
    0), a focus block, the voice, and the phone."""

    def __init__(self, *, owner_call: str = "sayang", start: datetime = EVENING, **config) -> None:
        self.minute = 0.0
        self.idle_s = 0.0
        self.start = start
        self.failure: Exception | None = None
        self.focusing = False
        self.reads = 0
        self.announced: list[tuple[str, str, datetime | None, datetime | None]] = []
        self.notifier = FakeNotifier()
        self.watcher = BreakWatcher(
            self.read,
            BreaksConfig(enabled=True, **config),
            announce=self.announce,
            notifier=self.notifier,
            owner_call=owner_call,
            in_focus_block=lambda: self.focusing,
            now=self.now,
        )

    def now(self) -> datetime:
        return self.start + timedelta(minutes=self.minute)

    def read(self) -> InputIdle:
        self.reads += 1
        if self.failure is not None:
            raise self.failure
        return reading(self.minute, self.idle_s)

    def announce(
        self,
        text: str,
        emotion: str,
        *,
        expires_at: datetime | None = None,
        game_expires_at: datetime | None = None,
    ) -> None:
        self.announced.append((text, emotion, expires_at, game_expires_at))

    def poll(self, minute: float, idle_s: float = 0) -> list[str]:
        self.minute, self.idle_s = minute, idle_s
        return self.watcher.poll_once()

    def at_the_keyboard(self, start: float, end: float) -> list[str]:
        """Polls every half minute from `start` through `end`, each right after an input."""
        return [line for minute, _ in at_the_keyboard(start, end) for line in self.poll(minute)]


def test_after_an_hour_at_the_keyboard_she_suggests_a_break_once_and_pushes_it_with_coffee():
    rig = Rig()

    assert rig.at_the_keyboard(0, 119.5) == [ONE_HOUR]

    assert [(text, emotion) for text, emotion, _, _ in rig.announced] == [(ONE_HOUR, "relaxed")]
    assert rig.notifier.sent == [("Cyrene", ONE_HOUR, ("coffee",))]


def test_the_line_is_out_of_date_once_a_break_s_length_has_passed_unsaid():
    # Held by a long talk, a game, or a focus block that began after it, it would come too late.
    rig = Rig(idle_reset_minutes=3)

    rig.at_the_keyboard(0, 60)

    [(_, _, expires_at, game_expires_at)] = rig.announced
    assert (expires_at, game_expires_at) == (wall(63), None)


def test_a_line_given_after_its_time_is_out_of_date_a_break_s_length_after_it_is_given():
    # A pause from minute 56 until 60:55 spans the line's time; the poll at 61 gives it.
    rig = Rig()
    rig.at_the_keyboard(0, 56)
    for minute in (56.5, 57, 57.5, 58, 58.5, 59, 59.5, 60, 60.5):
        assert rig.poll(minute, (minute - 56) * 60) == []

    assert rig.poll(61, 5) == [ONE_HOUR]

    [(_, _, expires_at, _)] = rig.announced
    assert expires_at == wall(61) + RESET


def test_still_at_the_keyboard_an_hour_later_she_tells_two_hours():
    rig = Rig()

    assert rig.at_the_keyboard(0, 120) == [ONE_HOUR, TWO_HOURS]


def test_she_tells_the_stretch_as_configured_and_calls_the_owner_at_sentence_start():
    rig = Rig(owner_call="master", every_minutes=90)

    assert rig.at_the_keyboard(0, 90) == [
        "Master, sudah satu jam tiga puluh menit di depan layar. Istirahatkan matamu sebentar "
        "dan minum air, ya."
    ]


def test_a_break_of_its_length_starts_the_count_again():
    rig = Rig()
    rig.at_the_keyboard(0, 40)

    assert rig.poll(45, idle_s=300) == []
    assert rig.at_the_keyboard(50, 109.5) == []
    assert rig.poll(110) == [ONE_HOUR]


def test_during_a_focus_block_nothing_is_read_or_said_and_the_count_starts_after_it():
    # A ten-minute block, shorter than the break length here: the block itself ends the count.
    rig = Rig(idle_reset_minutes=15)
    rig.at_the_keyboard(0, 50)
    reads = rig.reads

    rig.focusing = True
    assert rig.at_the_keyboard(50.5, 60) == []
    assert rig.reads == reads

    rig.focusing = False
    assert rig.at_the_keyboard(60.5, 120) == []
    assert rig.poll(120.5) == [ONE_HOUR]


def test_without_a_focus_timer_she_counts_all_the_same():
    rig = Rig()
    rig.watcher = BreakWatcher(
        rig.read,
        BreaksConfig(enabled=True),
        announce=rig.announce,
        notifier=None,
        owner_call="sayang",
        now=rig.now,
    )

    assert rig.at_the_keyboard(0, 60) == [ONE_HOUR]


def test_outside_the_active_hours_nothing_is_read_or_said():
    # From 01:30, active until 02:00: the polls after 02:00 read nothing.
    rig = Rig(start=datetime(2026, 10, 25, 1, 30), active_from="08:00", active_until="02:00")

    assert rig.at_the_keyboard(0, 90) == []
    assert rig.reads == 60  # 01:30 through 01:59:30


def test_the_active_hours_may_cross_midnight():
    rig = Rig(start=datetime(2026, 10, 24, 23, 30), active_from="08:00", active_until="02:00")

    assert rig.at_the_keyboard(0, 60) == [ONE_HOUR]  # at 00:30


def test_the_count_starts_when_the_active_hours_begin():
    rig = Rig(start=datetime(2026, 10, 24, 7, 30), active_from="08:00", active_until="02:00")

    assert rig.at_the_keyboard(0, 89.5) == []
    assert rig.poll(90) == [ONE_HOUR]  # 09:00, an hour after 08:00


def test_a_failed_read_is_never_said_starts_the_count_again_and_is_logged_once(caplog):
    rig = Rig()
    rig.at_the_keyboard(0, 30)

    with caplog.at_level(logging.DEBUG, logger="waifu.break_watch"):
        rig.failure = OSError("GetLastInputInfo failed (Windows error 5)")
        failures = rig.poll(30.5) + rig.poll(31) + rig.poll(31.5)
        rig.failure = None
        told = rig.at_the_keyboard(32, 91.5)

    assert failures == told == []  # a minute and a half of failures ended the count
    assert rig.poll(92) == [ONE_HOUR]
    warning, recovery = [record for record in caplog.records if record.levelno >= logging.INFO]
    assert (warning.levelno, recovery.levelno) == (logging.WARNING, logging.INFO)
    assert "Windows error 5" in warning.getMessage()
    assert not any(record.exc_info for record in caplog.records)


def test_an_unexpected_failure_is_logged_with_its_traceback(caplog):
    rig = Rig()
    rig.failure = RuntimeError("ctypes broke")

    with caplog.at_level(logging.WARNING, logger="waifu.break_watch"):
        assert rig.poll(0) == []

    [record] = caplog.records
    assert str(record.exc_info[1]) == "ctypes broke"


def test_the_count_and_the_line_are_logged_only_at_debug(caplog):
    # When Master is at the PC is his own; run logs the line once as it says it.
    rig = Rig()

    with caplog.at_level(logging.DEBUG, logger="waifu.break_watch"):
        rig.at_the_keyboard(0, 60)
        rig.poll(65, idle_s=300)

    assert caplog.records
    assert all(record.levelno == logging.DEBUG for record in caplog.records)


def test_a_voice_that_fails_never_stops_the_push_or_the_count(caplog):
    rig = Rig()

    def broken(text: str, emotion: str, **expiry) -> None:
        raise RuntimeError("the avatar is gone")

    rig.watcher._announce = broken

    with caplog.at_level(logging.ERROR, logger="waifu.break_watch"):
        assert rig.at_the_keyboard(0, 120) == [ONE_HOUR, TWO_HOURS]

    assert [message for _, message, _ in rig.notifier.sent] == [ONE_HOUR, TWO_HOURS]
    assert "the avatar is gone" in caplog.text


def test_a_push_that_fails_is_logged_and_the_line_still_said(caplog):
    rig = Rig()
    rig.notifier.outcome = OSError("no internet")

    with caplog.at_level(logging.ERROR, logger="waifu.break_watch"):
        assert rig.at_the_keyboard(0, 60) == [ONE_HOUR]

    assert [text for text, _, _, _ in rig.announced] == [ONE_HOUR]
    assert "no internet" in caplog.text


def test_without_a_voice_or_a_notifier_it_still_keeps_count():
    rig = Rig()
    rig.watcher = BreakWatcher(
        rig.read,
        BreaksConfig(enabled=True),
        announce=None,
        notifier=None,
        owner_call="sayang",
        now=rig.now,
    )

    assert rig.at_the_keyboard(0, 60) == [ONE_HOUR]


# --- the thread --------------------------------------------------------------------------------


class SignallingActivity:
    """Reads one fixed reading, setting `read` at each call; counts them in `calls`."""

    def __init__(self) -> None:
        self.read = threading.Event()
        self.calls = 0

    def __call__(self) -> InputIdle:
        self.calls += 1
        self.read.set()
        return reading(0)


class ShortWaits:
    """Stands in for threading.Event.wait: records each timeout asked for and waits on the stop
    event a few milliseconds at most."""

    def __init__(self) -> None:
        self.asked: list[float] = []

    def __call__(self, stop: threading.Event, timeout: float) -> bool:
        self.asked.append(timeout)
        return stop.wait(0.005)


def watcher_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "break-watcher"]


def make_watcher(read, **options) -> BreakWatcher:
    return BreakWatcher(
        read,
        BreaksConfig(enabled=True),
        announce=None,
        notifier=None,
        owner_call="sayang",
        now=lambda: EVENING,
        **options,
    )


def test_the_daemon_thread_polls_at_once_and_stop_wakes_it_at_once():
    read = SignallingActivity()
    watcher = make_watcher(read)

    watcher.start()
    try:
        assert read.read.wait(5)
        [thread] = watcher_threads()
        assert thread.daemon is True
    finally:
        stopping = time.monotonic()
        watcher.stop()

    assert time.monotonic() - stopping < 1  # although the next poll was 30 seconds away
    assert watcher_threads() == []
    assert read.calls == 1


def test_the_next_poll_comes_30_seconds_after_the_last_one_began():
    assert POLL_S == 30
    read = SignallingActivity()
    waits = ShortWaits()
    seconds = iter([1000.0, 1002.0] + [2000.0, 2000.0] * 100)  # the first poll took 2 s
    watcher = make_watcher(read, clock=lambda: next(seconds), wait=waits)

    watcher.start()
    try:
        deadline = time.monotonic() + 5
        while len(waits.asked) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        watcher.stop()

    assert waits.asked[:2] == [28.0, 30.0]
    assert read.calls >= 2


def test_starting_twice_runs_one_thread():
    watcher = make_watcher(SignallingActivity(), wait=ShortWaits())

    watcher.start()
    try:
        watcher.start()
        assert len(watcher_threads()) == 1
    finally:
        watcher.stop()

    assert watcher_threads() == []
