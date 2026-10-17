"""The focus timer: sessions, phases, her lines, and the thread. No test sleeps or speaks.

Every timer gets a WIB clock moved by the test, a recording `say`, and a real focus log in
tmp_path (wrapped to fail its writes on demand). Its thread checks once when started and then
waits until it is stopped, so the tests move time and call `tick_once` themselves; the thread
tests give it waits of a few milliseconds instead.
"""

import logging
import threading
import time as clock_time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from waifu.finance.values import WIB
from waifu.focus import FocusBlock, FocusStore
from waifu.focus_timer import FocusError, FocusPlan, FocusState, FocusTimer, spoken_duration

START = datetime(2026, 10, 17, 20, 0, tzinfo=WIB)  # Saturday 20:00
POMODORO = FocusPlan(minutes=25, break_minutes=5, rounds=4)
BLOCK_DONE = "Sayang, dua puluh lima menit selesai. Istirahat lima menit dulu ya."
BREAK_DONE = "Yuk, lanjut fokus lagi."


def after(minutes: float) -> datetime:
    """`minutes` after the session's start, to the second."""
    return START + timedelta(seconds=round(minutes * 60))


def block(start: float, end: float) -> FocusBlock:
    return FocusBlock(after(start), after(end))


# --- the plan and how she says a length ----------------------------------------------------------


def test_a_plan_is_one_block_of_twenty_five_minutes_with_five_minute_breaks_by_default():
    assert FocusPlan() == FocusPlan(minutes=25, break_minutes=5, rounds=1)


@pytest.mark.parametrize(
    "lengths",
    [
        {"minutes": 4},
        {"minutes": 121},
        {"break_minutes": 0},
        {"break_minutes": 61},
        {"rounds": 0},
        {"rounds": 9},
        {"minutes": 25.0},
        {"rounds": True},
    ],
)
def test_a_plan_s_lengths_are_whole_numbers_in_their_ranges(lengths):
    with pytest.raises(ValueError):
        FocusPlan(**lengths)


def test_the_longest_and_shortest_plans_are_allowed():
    assert FocusPlan(minutes=5, break_minutes=1, rounds=1).minutes == 5
    assert FocusPlan(minutes=120, break_minutes=60, rounds=8).rounds == 8


@pytest.mark.parametrize(
    ("minutes", "said"),
    [
        (25, "dua puluh lima menit"),
        (60, "satu jam"),
        (100, "satu jam empat puluh menit"),
        (120, "dua jam"),
        (125, "dua jam lima menit"),
    ],
)
def test_a_length_is_said_in_hours_and_minutes(minutes, said):
    assert spoken_duration(minutes) == said


# --- one check at a time -------------------------------------------------------------------------


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FailingStore(FocusStore):
    """A real focus log that fails the next `failing_adds` writes, as a broken disk would."""

    def __init__(self, path: Path, clock: Clock) -> None:
        super().__init__(path, keep_days=90, now=clock)
        self.failing_adds = 0

    def add(self, focus_block: FocusBlock) -> None:
        if self.failing_adds:
            self.failing_adds -= 1
            raise OSError("the disk is full")
        super().add(focus_block)


class WaitForStop:
    """Stands in for threading.Event.wait: the thread waits until it is stopped, and `waiting`
    tells that it has checked once and is waiting."""

    def __init__(self) -> None:
        self.waiting = threading.Event()

    def __call__(self, stop: threading.Event, timeout: float) -> bool:
        self.waiting.set()
        return stop.wait()


class Rig:
    """A started timer over a moving clock and a focus log in tmp_path; `said` holds each line
    with its emotion and whether a focus block was under way when it was said."""

    def __init__(self, tmp_path: Path, *, owner_call: str = "Sayang", start: bool = True) -> None:
        self.clock = Clock(START)
        self.store = FailingStore(tmp_path / "focus.json", self.clock)
        self.said: list[tuple[str, str, bool]] = []
        self.voice_error: Exception | None = None
        self.waits = WaitForStop()
        self.timer = FocusTimer(self.store, owner_call=owner_call, now=self.clock, wait=self.waits)
        if start:
            self.timer.start(self.say)
            assert self.waits.waiting.wait(5)  # its first check is done

    def say(self, text: str, emotion: str) -> None:
        if self.voice_error is not None:
            raise self.voice_error
        self.said.append((text, emotion, self.timer.in_block()))

    def lines(self) -> list[str]:
        return [text for text, _, _ in self.said]

    def tick_at(self, minutes: float) -> list[str]:
        self.clock.now = after(minutes)
        return self.timer.tick_once()


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.timer.stop()


def test_beginning_starts_the_first_block_now(rig):
    rig.clock.now = START + timedelta(microseconds=400)

    state = rig.timer.begin(POMODORO)

    assert state == FocusState(POMODORO, 1, "focus", START, after(25), 0)
    assert rig.timer.state() == state
    assert rig.timer.in_block() is True


def test_without_a_session_no_block_is_under_way(rig):
    assert rig.timer.state() is None
    assert rig.timer.in_block() is False
    assert rig.tick_at(60) == []


def test_a_session_begins_only_while_the_timer_runs(tmp_path):
    rig = Rig(tmp_path, start=False)

    with pytest.raises(FocusError, match="waifu run"):
        rig.timer.begin(POMODORO)

    rig.timer.start(rig.say)
    rig.timer.stop()
    with pytest.raises(FocusError, match="waifu run"):
        rig.timer.begin(POMODORO)


def test_a_second_session_cannot_begin_while_one_runs(rig):
    rig.timer.begin(POMODORO)

    with pytest.raises(FocusError, match="sudah berjalan"):
        rig.timer.begin(FocusPlan())

    assert rig.timer.state().plan == POMODORO


def test_nothing_is_said_before_the_block_ends(rig):
    rig.timer.begin(POMODORO)

    assert rig.tick_at(24 + 59 / 60) == []
    assert rig.timer.in_block() is True
    assert rig.store.blocks() == ()


def test_the_end_of_a_block_is_logged_and_said_before_the_break_starts(rig):
    rig.timer.begin(POMODORO)

    said = rig.tick_at(25)

    assert said == [BLOCK_DONE]
    # Still inside the block when she says it: nothing held for the block comes out first.
    assert rig.said == [(BLOCK_DONE, "happy", True)]
    assert rig.timer.state() == FocusState(POMODORO, 1, "break", after(25), after(30), 25 * 60)
    assert rig.timer.in_block() is False
    assert rig.store.blocks() == (block(0, 25),)


def test_the_end_of_a_break_starts_the_next_block(rig):
    rig.timer.begin(POMODORO)
    rig.tick_at(25)

    assert rig.tick_at(30) == [BREAK_DONE]
    assert rig.said[-1] == (BREAK_DONE, "happy", False)
    assert rig.timer.state() == FocusState(POMODORO, 2, "focus", after(30), after(55), 25 * 60)
    assert rig.timer.in_block() is True


def test_the_last_block_ends_the_session_with_a_praise_and_its_total(rig):
    rig.timer.begin(POMODORO)

    for minutes in (25, 30, 55, 60, 85, 90, 115):
        rig.tick_at(minutes)

    assert rig.lines() == [
        BLOCK_DONE,
        BREAK_DONE,
        BLOCK_DONE,
        BREAK_DONE,
        BLOCK_DONE,
        BREAK_DONE,
        "Sayang, fokusnya selesai. Hebat, total satu jam empat puluh menit!",
    ]
    assert rig.timer.state() is None
    assert rig.store.blocks() == (block(0, 25), block(30, 55), block(60, 85), block(90, 115))


def test_a_single_block_ends_with_the_praise_and_no_break(rig):
    rig.timer.begin(FocusPlan())

    assert rig.tick_at(25) == ["Sayang, fokusnya selesai. Hebat, total dua puluh lima menit!"]
    assert rig.timer.state() is None


def test_the_lines_say_the_lengths_of_the_plan(rig):
    rig.timer.begin(FocusPlan(minutes=50, break_minutes=10, rounds=2))

    assert rig.tick_at(50) == ["Sayang, lima puluh menit selesai. Istirahat sepuluh menit dulu ya."]
    rig.tick_at(60)
    assert rig.tick_at(110) == [
        "Sayang, fokusnya selesai. Hebat, total satu jam empat puluh menit!"
    ]


def test_the_owner_word_opens_her_lines(tmp_path):
    rig = Rig(tmp_path, owner_call="sayang")
    try:
        rig.timer.begin(FocusPlan(rounds=2))
        assert rig.tick_at(25) == [BLOCK_DONE]
    finally:
        rig.timer.stop()


def test_phases_follow_each_other_exactly_however_late_the_check_comes(rig):
    rig.timer.begin(POMODORO)

    rig.tick_at(25 + 10 / 60)

    assert rig.timer.state().ends_at == after(30)
    assert rig.store.blocks() == (block(0, 25),)


def test_every_phase_that_ended_since_the_last_check_is_handled(rig):
    rig.timer.begin(FocusPlan(minutes=25, break_minutes=1, rounds=2))

    said = rig.tick_at(26.5)

    assert said == [
        "Sayang, dua puluh lima menit selesai. Istirahat satu menit dulu ya.",
        BREAK_DONE,
    ]
    assert rig.timer.state() == FocusState(
        FocusPlan(minutes=25, break_minutes=1, rounds=2), 2, "focus", after(26), after(51), 1500
    )


def test_a_phase_that_ended_two_minutes_ago_is_still_handled(rig):
    rig.timer.begin(POMODORO)

    assert rig.tick_at(27) == [BLOCK_DONE]


def test_a_phase_that_ended_more_than_two_minutes_ago_ends_the_session_quietly(rig, caplog):
    rig.timer.begin(POMODORO)
    rig.tick_at(25)
    rig.tick_at(30)

    with caplog.at_level(logging.INFO, logger="waifu.focus_timer"):
        said = rig.tick_at(57 + 1 / 60)  # the PC slept through the second block's end

    assert said == []
    assert rig.lines() == [BLOCK_DONE, BREAK_DONE]
    assert rig.timer.state() is None
    assert rig.timer.in_block() is False
    assert rig.store.blocks() == (block(0, 25),)  # the block under way is dropped
    assert "ending the session quietly" in caplog.text


def test_a_failed_write_is_logged_and_the_session_goes_on(rig, caplog):
    rig.timer.begin(POMODORO)
    rig.store.failing_adds = 1

    with caplog.at_level(logging.WARNING, logger="waifu.focus_timer"):
        said = rig.tick_at(25)

    assert said == [BLOCK_DONE]
    assert rig.timer.state().phase == "break"
    assert rig.store.blocks() == ()
    assert "Could not log a focus block" in caplog.text


def test_a_failing_voice_is_logged_and_the_session_goes_on(rig, caplog):
    rig.timer.begin(POMODORO)
    rig.voice_error = RuntimeError("no voice")

    with caplog.at_level(logging.ERROR, logger="waifu.focus_timer"):
        rig.tick_at(25)

    assert rig.timer.state().phase == "break"
    assert "Could not say a focus line" in caplog.text


def test_ending_logs_the_block_under_way_and_gives_the_session_s_focus(rig):
    rig.timer.begin(POMODORO)
    rig.tick_at(25)
    rig.tick_at(30)
    rig.clock.now = after(40) + timedelta(microseconds=700)

    focused = rig.timer.end()

    assert focused == 35 * 60
    assert rig.store.blocks() == (block(0, 25), block(30, 40))
    assert rig.timer.state() is None
    assert rig.timer.in_block() is False


def test_a_block_under_way_for_less_than_a_minute_is_not_logged(rig):
    rig.timer.begin(POMODORO)
    rig.clock.now = after(59 / 60)

    assert rig.timer.end() == 0
    assert rig.store.blocks() == ()


def test_ending_during_a_break_logs_nothing_more(rig):
    rig.timer.begin(POMODORO)
    rig.tick_at(25)
    rig.clock.now = after(28)

    assert rig.timer.end() == 25 * 60
    assert rig.store.blocks() == (block(0, 25),)


def test_ending_without_a_session_gives_none(rig):
    assert rig.timer.end() is None


def test_a_new_session_may_begin_once_the_last_one_ended(rig):
    rig.timer.begin(FocusPlan())
    rig.tick_at(25)

    assert rig.timer.begin(FocusPlan(minutes=30)).ends_at == after(55)


def test_focus_time_adds_the_block_under_way_to_the_log(rig):
    rig.store.add(FocusBlock(START.replace(hour=8), START.replace(hour=9)))
    rig.timer.begin(POMODORO)
    rig.clock.now = after(10)
    midnight = START.replace(hour=0)

    assert rig.timer.focused_seconds(midnight, after(10)) == 3600 + 600
    assert rig.timer.focused_seconds(midnight, after(4)) == 3600 + 240
    assert rig.timer.focused_seconds(after(5), after(10)) == 300


def test_focus_time_without_a_block_under_way_is_the_log_s(rig):
    rig.timer.begin(POMODORO)
    rig.tick_at(25)
    rig.clock.now = after(27)

    assert rig.timer.focused_seconds(START.replace(hour=0), after(27)) == 25 * 60


# --- the thread ----------------------------------------------------------------------------------


class ShortWaits:
    """Stands in for threading.Event.wait: records each timeout asked for and waits on the stop
    event a few milliseconds at most."""

    def __init__(self) -> None:
        self.asked: list[float] = []

    def __call__(self, stop: threading.Event, timeout: float) -> bool:
        self.asked.append(timeout)
        return stop.wait(0.005)


def timer_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "focus-timer"]


def wait_for(condition, seconds: float = 5) -> None:
    deadline = clock_time.monotonic() + seconds
    while not condition() and clock_time.monotonic() < deadline:
        clock_time.sleep(0.005)
    assert condition()


def test_the_daemon_thread_checks_at_once_and_stop_wakes_it_at_once(tmp_path):
    rig = Rig(tmp_path)
    [thread] = timer_threads()

    assert thread.daemon is True
    stopping = clock_time.monotonic()
    rig.timer.stop()

    assert clock_time.monotonic() - stopping < 1  # although the next check was 15 s away
    assert timer_threads() == []


def test_the_thread_waits_a_tick_or_until_the_phase_ends(tmp_path):
    clock = Clock(START)
    waits = ShortWaits()
    timer = FocusTimer(
        FocusStore(tmp_path / "focus.json", keep_days=90, now=clock),
        owner_call="Sayang",
        now=clock,
        wait=waits,
    )

    timer.start(lambda text, emotion: None)
    try:
        wait_for(lambda: len(waits.asked) >= 1)
        timer.begin(FocusPlan())
        clock.now = after(25) - timedelta(seconds=4)
        wait_for(lambda: 4.0 in waits.asked)
    finally:
        timer.stop()

    assert max(waits.asked) == 15.0  # before the session: a whole tick


def test_without_a_session_the_thread_waits_a_whole_tick(tmp_path):
    clock = Clock(START)
    waits = ShortWaits()
    timer = FocusTimer(
        FocusStore(tmp_path / "focus.json", keep_days=90, now=clock),
        owner_call="Sayang",
        now=clock,
        wait=waits,
        tick_seconds=20,
    )

    timer.start(lambda text, emotion: None)
    try:
        wait_for(lambda: len(waits.asked) >= 1)
    finally:
        timer.stop()

    assert waits.asked[0] == 20


def test_the_thread_says_the_lines_when_a_phase_ends(tmp_path):
    clock = Clock(START)
    said: list[str] = []
    timer = FocusTimer(
        FocusStore(tmp_path / "focus.json", keep_days=90, now=clock),
        owner_call="Sayang",
        now=clock,
        wait=ShortWaits(),
    )

    timer.start(lambda text, emotion: said.append(text))
    try:
        timer.begin(FocusPlan(rounds=2))
        clock.now = after(25)
        wait_for(lambda: said == [BLOCK_DONE])
    finally:
        timer.stop()


class BreakingClock(Clock):
    """A clock that raises once, the first time it is read at or after `breaks_at`;
    `broke_after_waits` is how many waits the thread had asked for by then."""

    def __init__(self, now: datetime, *, breaks_at: datetime, waits: ShortWaits) -> None:
        super().__init__(now)
        self.breaks_at = breaks_at
        self.waits = waits
        self.broke_after_waits: int | None = None

    def __call__(self) -> datetime:
        if self.broke_after_waits is None and self.now >= self.breaks_at:
            self.broke_after_waits = len(self.waits.asked)
            raise RuntimeError("the clock broke")
        return self.now


def test_a_failing_check_is_logged_and_the_thread_tries_again_a_tick_later(tmp_path, caplog):
    waits = ShortWaits()
    clock = BreakingClock(START, breaks_at=after(25), waits=waits)
    said: list[str] = []
    timer = FocusTimer(
        FocusStore(tmp_path / "focus.json", keep_days=90, now=Clock(START)),
        owner_call="Sayang",
        now=clock,
        wait=waits,
    )

    with caplog.at_level(logging.ERROR, logger="waifu.focus_timer"):
        timer.start(lambda text, emotion: said.append(text))
        try:
            timer.begin(FocusPlan(rounds=2))
            clock.now = after(25)  # the block ends, and the next reading of the clock fails
            wait_for(lambda: said == [BLOCK_DONE])
            assert len(timer_threads()) == 1  # the thread outlived the failure
        finally:
            timer.stop()

    assert "A focus timer check failed" in caplog.text
    assert "the clock broke" in caplog.text
    # A whole tick, never at once: a check that keeps failing as a phase ends must not spin.
    assert waits.asked[clock.broke_after_waits] == 15.0


def test_starting_twice_runs_one_thread(rig):
    rig.timer.start(rig.say)

    assert len(timer_threads()) == 1


def test_stopping_ends_the_session_and_logs_the_block_under_way(rig):
    rig.timer.begin(POMODORO)
    rig.clock.now = after(10)

    rig.timer.stop()

    assert rig.timer.state() is None
    assert rig.store.blocks() == (block(0, 10),)
    assert rig.lines() == []  # a restart ends the session without a word
