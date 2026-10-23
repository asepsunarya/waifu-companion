"""The break reminder: continuous activity at the PC, and the watcher that says the line.

No test reads the real keyboard or mouse (tests/conftest.py refuses GetLastInputInfo), sleeps,
speaks, or pushes. A poll is given as its minute since BOOT and the seconds since the last input
then; the tick count and the wall clock move together unless a test splits them.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from waifu.break_watch import Activity, follow_activity
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
