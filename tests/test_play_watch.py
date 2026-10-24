"""The play time watcher and the bedtime nudge: no test lists the real processes, sleeps, or
speaks.

Every watcher gets a fake process listing, a WIB clock moved by the test, a fake announce, and a
real store in tmp_path (wrapped to count, or to fail, its writes); the thread tests run a real
thread whose waits last a few milliseconds.
"""

import logging
import threading
import time as clock_time
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import get_type_hints

import pytest

from waifu.config import PlayTimeConfig
from waifu.finance.values import WIB
from waifu.play_time import PlayRecord, PlayTimeStore, PlayTotal, Session
from waifu.play_watch import PlayTimeWatcher, in_night, spoken_clock

DAY = datetime(2026, 10, 17, tzinfo=WIB)  # a Saturday
MINUTE = timedelta(minutes=1)
HSR = {"explorer.exe", "hyp.exe", "starrail.exe"}
IDLE = {"explorer.exe", "hyp.exe"}  # the launcher alone is no game


def at(hour: float) -> datetime:
    return DAY + timedelta(seconds=round(hour * 3600))


# --- how she says a clock time -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("clock", "said"),
    [
        (time(23, 0), "jam sebelas malam"),
        (time(0, 0), "jam dua belas malam"),
        (time(1, 0), "jam satu dini hari"),
        (time(23, 30), "jam setengah dua belas"),
        (time(0, 30), "jam setengah satu"),
        (time(18, 30), "jam setengah tujuh"),
        (time(22, 15), "jam sepuluh lima belas"),
        (time(1, 45), "jam satu empat puluh lima"),
        (time(0, 5), "jam dua belas lima"),
    ],
)
def test_a_clock_time_is_said_with_whole_hours_half_hours_and_minutes(clock, said):
    assert spoken_clock(clock) == said


def test_the_clock_times_are_hinted_as_datetime_times_not_the_time_module():
    assert get_type_hints(spoken_clock)["at"] is time
    assert get_type_hints(in_night)["bedtime"] is time


# --- the watcher, one poll at a time -------------------------------------------------------------


class Listing:
    """Stands in for the process lister: gives `names` (or raises them when an exception)."""

    def __init__(self, names: Iterable[str] | Exception = IDLE) -> None:
        self.names = names
        self.calls = 0

    def __call__(self) -> frozenset[str]:
        self.calls += 1
        if isinstance(self.names, Exception):
            raise self.names
        return frozenset(self.names)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class RecordingStore(PlayTimeStore):
    """A real store that records each write and fails the next `failing_saves` saves and
    `failing_marks` bedtime marks, and `failing_reads` reads, as a broken disk would."""

    def __init__(self, path: Path, clock: Clock, **options) -> None:
        options.setdefault("games", ["hsr", "genshin", "zzz"])
        super().__init__(path, keep_days=90, now=clock, **options)
        self.saves: list[tuple[tuple[Session, ...], tuple[Session, ...]]] = []
        self.marks: list[date] = []
        self.failing_saves = 0
        self.failing_marks = 0
        self.failing_reads = 0

    def record(self) -> PlayRecord:
        if self.failing_reads:
            self.failing_reads -= 1
            raise OSError("the disk is gone")
        return super().record()

    def save(self, open_sessions, finished=()) -> None:
        if self.failing_saves:
            self.failing_saves -= 1
            raise OSError("the disk is full")
        self.saves.append((tuple(open_sessions), tuple(finished)))
        super().save(open_sessions, finished)

    def mark_bedtime(self, night: date) -> None:
        if self.failing_marks:
            self.failing_marks -= 1
            raise OSError("the disk is full")
        self.marks.append(night)
        super().mark_bedtime(night)


class Rig:
    """A watcher over a fake listing, a moving clock, and a store in tmp_path."""

    def __init__(self, tmp_path: Path, *, owner_call: str = "Master", **config) -> None:
        self.clock = Clock(at(20))
        self.listing = Listing()
        self.path = tmp_path / "data" / "playtime.json"
        self.store = RecordingStore(self.path, self.clock)
        self.config = PlayTimeConfig(**config)
        self.said: list[tuple[str, str]] = []
        self.polled: list[tuple[bool, bool]] = []  # each (playing, resumed) told to game mode
        self.ended: list[Session] = []  # each session told as just finished (on_finished)
        self.owner_call = owner_call
        self.watcher = self.new_watcher()

    def new_watcher(self) -> PlayTimeWatcher:
        """Another run of `waifu run` on the same file."""
        return PlayTimeWatcher(
            self.listing,
            self.store,
            self.config,
            announce=lambda text, emotion: self.said.append((text, emotion)),
            owner_call=self.owner_call,
            now=self.clock,
            on_poll=lambda playing, resumed: self.polled.append((playing, resumed)),
            on_finished=self.ended.append,
        )

    def poll(self, names: Iterable[str] | Exception, when: datetime | None = None) -> list[str]:
        if when is not None:
            self.clock.now = when
        self.listing.names = names
        return self.watcher.poll_once()

    def polls(self, names, start: datetime, count: int) -> None:
        """`count` polls a minute apart from `start` seeing `names`."""
        for n in range(count):
            self.poll(names, start + n * MINUTE)

    def record(self) -> PlayRecord:
        return self.store.record()


def test_a_game_seen_is_written_as_an_open_session_at_once(tmp_path):
    rig = Rig(tmp_path)

    assert rig.poll(HSR) == []

    assert rig.record() == PlayRecord(open=(Session("hsr", at(20), at(20)),))


def test_while_she_sees_no_game_nothing_is_written_and_no_file_is_made(tmp_path):
    rig = Rig(tmp_path)

    rig.polls(IDLE, at(20), 5)

    assert rig.store.saves == []
    assert not rig.path.exists()


def test_each_poll_while_a_game_is_open_moves_its_last_poll_in_the_file(tmp_path):
    rig = Rig(tmp_path)

    rig.polls(HSR, at(20), 3)

    assert rig.record() == PlayRecord(open=(Session("hsr", at(20), at(20) + 2 * MINUTE),))
    assert len(rig.store.saves) == 3  # about once a minute while playing


def test_a_game_gone_for_two_polls_is_written_as_a_finished_session(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 3)

    rig.polls(IDLE, at(20) + 3 * MINUTE, 4)

    assert rig.record() == PlayRecord(sessions=(Session("hsr", at(20), at(20) + 2 * MINUTE),))
    assert len(rig.store.saves) == 4  # neither the missed poll nor the idle ones wrote


def test_the_poll_interval_is_the_configured_one(tmp_path):
    rig = Rig(tmp_path, poll_seconds=300)
    rig.poll(HSR, at(20))

    rig.poll(HSR, at(20) + 15 * MINUTE)  # three polls: still one session

    assert rig.record().open == (Session("hsr", at(20), at(20) + 15 * MINUTE),)


def test_a_sleep_between_polls_is_never_counted(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 3)

    rig.poll(HSR, at(23))  # the PC slept with the game open

    assert rig.record() == PlayRecord(
        sessions=(Session("hsr", at(20), at(20) + 2 * MINUTE),),
        open=(Session("hsr", at(23), at(23)),),
    )


def test_only_the_configured_games_are_followed(tmp_path):
    rig = Rig(tmp_path, games={"zzz": ["ZenlessZoneZero.exe"]})

    rig.poll(HSR | {"ZenlessZoneZero.exe"})

    assert rig.record().open == (Session("zzz", at(20), at(20)),)


def test_stopping_ends_every_open_session_at_its_last_poll_in_one_write(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR | {"genshinimpact.exe"}, at(20), 3)
    writes = len(rig.store.saves)

    rig.clock.now = at(21)
    rig.watcher.stop()

    end = at(20) + 2 * MINUTE
    assert rig.record() == PlayRecord(
        sessions=(Session("genshin", at(20), end), Session("hsr", at(20), end))
    )
    assert len(rig.store.saves) == writes + 1


def test_a_poll_after_stopping_changes_nothing(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 2)
    rig.watcher.stop()
    writes = len(rig.store.saves)

    assert rig.poll(HSR, at(20) + 2 * MINUTE) == []
    rig.watcher.stop()

    assert len(rig.store.saves) == writes


def test_stopping_before_any_poll_writes_nothing(tmp_path):
    rig = Rig(tmp_path)

    rig.watcher.stop()

    assert not rig.path.exists()


def test_a_restart_mid_game_goes_on_with_the_same_session(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 11)  # 20:00 until 20:10
    rig.watcher.stop()

    rig.watcher = rig.new_watcher()  # loading the models took two minutes
    rig.poll(HSR, at(20) + 12 * MINUTE)

    assert rig.record() == PlayRecord(open=(Session("hsr", at(20), at(20) + 12 * MINUTE),))
    assert rig.store.totals(at(0), at(21)) == {
        "hsr": PlayTotal(12 * 60, 1),
        "genshin": PlayTotal(0, 0),
        "zzz": PlayTotal(0, 0),
    }


def test_a_crash_s_open_session_ends_at_its_last_poll_when_the_game_is_gone(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 3)  # then the process died: no stop

    rig.watcher = rig.new_watcher()
    rig.poll(IDLE, at(21))

    assert rig.record() == PlayRecord(sessions=(Session("hsr", at(20), at(20) + 2 * MINUTE),))


def test_a_crash_s_open_session_of_no_length_is_cleared_from_the_file(tmp_path):
    rig = Rig(tmp_path)
    rig.poll(HSR, at(20))  # seen once, then the process died

    rig.watcher = rig.new_watcher()
    rig.poll(IDLE, at(21))

    assert rig.record() == PlayRecord()


def test_a_failed_listing_is_never_said_and_logged_once_until_it_works_again(tmp_path, caplog):
    rig = Rig(tmp_path)

    with caplog.at_level(logging.DEBUG, logger="waifu.play_watch"):
        for _ in range(3):
            assert rig.poll(OSError("CreateToolhelp32Snapshot failed (Windows error 8)")) == []
        rig.poll(HSR)

    levels = [record.levelno for record in caplog.records]
    assert levels == [logging.WARNING, logging.DEBUG, logging.DEBUG, logging.INFO]
    assert "Windows error 8" in caplog.records[0].getMessage()
    assert not caplog.records[0].exc_info  # an OSError needs no traceback
    assert rig.said == []
    assert rig.record().open == (Session("hsr", at(20), at(20)),)


def test_an_unexpected_failure_is_logged_with_its_traceback(tmp_path, caplog):
    rig = Rig(tmp_path)

    with caplog.at_level(logging.WARNING, logger="waifu.play_watch"):
        rig.poll(RuntimeError("odd"))

    [record] = caplog.records
    assert record.exc_info is not None


def test_sessions_a_failed_write_could_not_keep_are_written_with_the_next_write(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 3)
    rig.store.failing_saves = 2

    with caplog.at_level(logging.WARNING, logger="waifu.play_watch"):
        # The session ends at the second idle poll, unwritten, and so does the third's.
        rig.polls(IDLE, at(20) + 3 * MINUTE, 4)

    assert rig.record() == PlayRecord(sessions=(Session("hsr", at(20), at(20) + 2 * MINUTE),))
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_a_first_poll_that_cannot_read_the_file_is_tried_again(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 3)
    rig.watcher.stop()
    rig.watcher = rig.new_watcher()
    rig.store.failing_reads = 1

    rig.poll(HSR, at(20) + 4 * MINUTE)
    rig.poll(HSR, at(20) + 5 * MINUTE)

    assert rig.record().open == (Session("hsr", at(20), at(20) + 5 * MINUTE),)


# --- whether a game is running, for game mode ----------------------------------------------------

GENSHIN = HSR | {"GenshinImpact.exe"}


def test_each_poll_tells_whether_a_tracked_game_is_running(tmp_path):
    rig = Rig(tmp_path)

    rig.poll(IDLE, at(20))  # the launcher alone is no game
    rig.polls(HSR, at(20) + MINUTE, 2)

    assert rig.polled == [(False, False), (True, False), (True, False)]


def test_a_game_missed_at_one_poll_is_still_running_until_the_second(tmp_path):
    # Alt-tab leaves the process list as it is; a launcher or game restart may miss one poll.
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 2)

    rig.poll(IDLE, at(20) + 2 * MINUTE)
    rig.poll(HSR, at(20) + 3 * MINUTE)
    rig.polls(IDLE, at(20) + 4 * MINUTE, 2)

    assert [playing for playing, _ in rig.polled] == [True, True, True, True, True, False]


def test_a_sleep_with_a_game_open_keeps_it_running(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 3)

    rig.poll(HSR, at(23))  # the PC slept with the game open: a fresh session

    assert rig.polled[-1] == (True, False)


def test_the_first_poll_after_a_restart_mid_game_tells_it_resumed(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 11)
    rig.watcher.stop()

    rig.watcher = rig.new_watcher()  # loading the models took two minutes
    rig.polled.clear()
    rig.polls(HSR, at(20) + 12 * MINUTE, 2)

    assert rig.polled == [(True, True), (True, False)]


def test_a_game_opened_during_a_restart_beside_a_resumed_one_still_counts_as_resumed(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 11)
    rig.watcher.stop()

    rig.watcher = rig.new_watcher()
    rig.polled.clear()
    rig.poll(GENSHIN, at(20) + 12 * MINUTE)

    assert rig.polled == [(True, True)]


@pytest.mark.parametrize(
    "names_before", [IDLE, HSR], ids=["no-game-before", "game-closed-long-before"]
)
def test_a_first_poll_that_finds_a_game_without_its_session_from_before_is_no_resume(
    tmp_path, names_before
):
    rig = Rig(tmp_path)
    rig.polls(names_before, at(20), 3)
    rig.watcher.stop()

    rig.watcher = rig.new_watcher()
    rig.polled.clear()
    rig.poll(HSR, at(21))  # an hour later: a session last seen more than 5 minutes ago

    assert rig.polled == [(True, False)]


def test_a_failed_poll_tells_nothing(tmp_path):
    rig = Rig(tmp_path)
    rig.poll(HSR, at(20))

    rig.poll(OSError("CreateToolhelp32Snapshot failed (Windows error 8)"), at(20) + MINUTE)
    rig.store.failing_saves = 1
    rig.poll(HSR, at(20) + 2 * MINUTE)

    assert rig.polled == [(True, False)]


def test_a_restart_mid_game_whose_first_write_fails_tells_the_resume_at_the_next_poll(tmp_path):
    # Game mode would otherwise take the game for one just opened and run on_start again.
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 11)
    rig.watcher.stop()

    rig.watcher = rig.new_watcher()
    rig.polled.clear()
    rig.store.failing_saves = 1
    rig.polls(HSR, at(20) + 12 * MINUTE, 3)

    assert rig.polled == [(True, True), (True, False)]


def test_a_restart_mid_game_whose_first_bedtime_write_fails_tells_the_resume_at_the_next_poll(
    tmp_path,
):
    rig = Rig(tmp_path, bedtime=time(20, 11))
    rig.polls(HSR, at(20), 11)  # 20:00 until 20:10, before bedtime
    rig.watcher.stop()

    rig.watcher = rig.new_watcher()
    rig.polled.clear()
    rig.store.failing_marks = 1
    rig.polls(HSR, at(20) + 12 * MINUTE, 3)

    assert rig.polled == [(True, True), (True, False)]


def test_a_game_first_seen_at_a_first_poll_whose_write_fails_is_no_resume(tmp_path):
    rig = Rig(tmp_path)
    rig.store.failing_saves = 1

    rig.polls(HSR, at(20), 2)

    assert rig.polled == [(True, False)]


def test_stopping_tells_nothing_and_a_poll_after_it_neither(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 2)

    rig.watcher.stop()
    rig.poll(IDLE, at(20) + 5 * MINUTE)

    assert rig.polled == [(True, False), (True, False)]


def test_a_failing_game_mode_is_logged_and_the_play_time_goes_on(tmp_path, caplog):
    rig = Rig(tmp_path)

    def broken(playing: bool, resumed: bool) -> None:
        raise RuntimeError("the lamp is gone")

    rig.watcher = PlayTimeWatcher(
        rig.listing,
        rig.store,
        rig.config,
        announce=None,
        owner_call="Master",
        now=rig.clock,
        on_poll=broken,
    )
    with caplog.at_level(logging.ERROR, logger="waifu.play_watch"):
        rig.polls(HSR, at(20), 2)

    assert rig.record().open == (Session("hsr", at(20), at(20) + MINUTE),)
    assert [record.exc_info is not None for record in caplog.records] == [True, True]
    assert "Could not tell game mode whether a game is running" in caplog.text


# --- a game just closed (on_finished) ------------------------------------------------------------


def test_a_game_closed_is_told_once_at_the_poll_that_finishes_its_session(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(23), 3)

    rig.poll(IDLE, at(23) + 3 * MINUTE)  # missed once: maybe an alt-tab
    told_after_one_miss = list(rig.ended)
    rig.polls(IDLE, at(23) + 4 * MINUTE, 3)

    assert told_after_one_miss == []
    assert rig.ended == [Session("hsr", at(23), at(23) + 2 * MINUTE)]


def test_a_session_ended_by_a_sleep_is_never_told(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 3)

    rig.poll(IDLE, at(23))  # the PC slept with the game open: found hours late

    assert rig.record().sessions == (Session("hsr", at(20), at(20) + 2 * MINUTE),)
    assert rig.ended == []


@pytest.mark.parametrize(("restart", "told"), [(2, True), (30, False)])
def test_a_session_a_crashed_run_left_is_told_only_after_a_quick_restart(tmp_path, restart, told):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 3)  # the run crashes without stopping: the session stays open

    rig.watcher = rig.new_watcher()
    rig.poll(IDLE, at(20) + (2 + restart) * MINUTE)

    assert rig.ended == ([Session("hsr", at(20), at(20) + 2 * MINUTE)] if told else [])


def test_a_session_finished_by_a_poll_whose_write_failed_is_told_at_the_next_poll(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 3)
    rig.poll(IDLE, at(20) + 3 * MINUTE)

    rig.store.failing_saves = 1
    rig.poll(IDLE, at(20) + 4 * MINUTE)
    told_after_the_failure = list(rig.ended)
    rig.poll(IDLE, at(20) + 5 * MINUTE)

    assert told_after_the_failure == []
    assert rig.ended == [Session("hsr", at(20), at(20) + 2 * MINUTE)]


def test_stopping_ends_the_open_sessions_without_telling_them(tmp_path):
    rig = Rig(tmp_path)
    rig.polls(HSR, at(20), 3)

    rig.watcher.stop()
    rig.poll(IDLE, at(20) + 5 * MINUTE)

    assert rig.record().sessions == (Session("hsr", at(20), at(20) + 2 * MINUTE),)
    assert rig.ended == []


def test_a_failing_listener_is_logged_and_the_play_time_goes_on(tmp_path, caplog):
    rig = Rig(tmp_path)

    def broken(session: Session) -> None:
        raise RuntimeError("the mailbox is gone")

    rig.watcher = PlayTimeWatcher(
        rig.listing,
        rig.store,
        rig.config,
        announce=None,
        owner_call="Master",
        now=rig.clock,
        on_finished=broken,
    )
    rig.polls(HSR, at(20), 3)
    with caplog.at_level(logging.ERROR, logger="waifu.play_watch"):
        rig.polls(IDLE, at(20) + 3 * MINUTE, 2)
    rig.poll(IDLE | {"GenshinImpact.exe"}, at(20) + 5 * MINUTE)

    assert rig.record().open == (Session("genshin", at(20) + 5 * MINUTE, at(20) + 5 * MINUTE),)
    assert [record.exc_info is not None for record in caplog.records] == [True]
    assert "Could not tell that a game was closed" in caplog.text


# --- the bedtime nudge ---------------------------------------------------------------------------

LATE = "Master, sudah lewat jam setengah dua belas, jangan terlalu malam ya."


def bedtime_rig(tmp_path: Path, bedtime: time = time(23, 30), **options) -> Rig:
    return Rig(tmp_path, bedtime=bedtime, **options)


def test_past_bedtime_with_a_game_open_she_says_it_once_calmly(tmp_path):
    rig = bedtime_rig(tmp_path)
    rig.polls(HSR, at(23.5) - 2 * MINUTE, 2)

    assert rig.poll(HSR, at(23.5)) == [LATE]
    rig.polls(HSR, at(23.5) + MINUTE, 3)
    rig.poll(HSR, at(25))  # still the same night after midnight

    assert rig.said == [(LATE, "neutral")]
    assert rig.record().bedtime_told == date(2026, 10, 17)


def test_she_says_it_as_the_owner_asked_calling_them_at_sentence_start(tmp_path):
    rig = bedtime_rig(tmp_path, owner_call="sayang")

    assert rig.poll(HSR, at(23.75)) == [
        "Sayang, sudah lewat jam setengah dua belas, jangan terlalu malam ya."
    ]


def test_the_night_is_written_before_she_says_it(tmp_path):
    rig = bedtime_rig(tmp_path)
    told_when_said: list[date | None] = []
    rig.watcher = PlayTimeWatcher(
        rig.listing,
        rig.store,
        rig.config,
        announce=lambda text, emotion: told_when_said.append(rig.record().bedtime_told),
        owner_call="Master",
        now=rig.clock,
    )

    rig.poll(HSR, at(23.75))

    assert told_when_said == [date(2026, 10, 17)]


def test_a_restart_the_same_night_never_says_it_again(tmp_path):
    rig = bedtime_rig(tmp_path)
    rig.poll(HSR, at(23.75))
    rig.watcher.stop()

    rig.watcher = rig.new_watcher()
    rig.polls(HSR, at(25), 3)

    assert rig.said == [(LATE, "neutral")]


def test_the_next_night_she_may_say_it_again(tmp_path):
    rig = bedtime_rig(tmp_path)
    rig.poll(HSR, at(23.75))

    rig.poll(HSR, at(24 + 23.75))

    assert [text for text, _ in rig.said] == [LATE, LATE]
    assert rig.record().bedtime_told == date(2026, 10, 18)


@pytest.mark.parametrize(
    ("names", "when"),
    [
        (HSR, at(23.5) - MINUTE),  # before bedtime
        (IDLE, at(23.75)),  # no game: the launcher alone is none
        (HSR, at(30)),  # Sunday 06:00: the night is over
        (HSR, at(24 + 17)),  # Sunday afternoon
    ],
    ids=["before_bedtime", "no_game", "morning", "afternoon"],
)
def test_she_says_nothing_outside_the_night_or_without_a_game(tmp_path, names, when):
    rig = bedtime_rig(tmp_path)

    assert rig.poll(names, when) == []
    assert rig.store.marks == []


@pytest.mark.parametrize("opened", [at(28), at(29.25)], ids=["at_four", "at_a_quarter_past_five"])
def test_a_game_first_opened_at_dawn_is_an_early_morning_not_a_late_night(tmp_path, opened):
    rig = bedtime_rig(tmp_path)
    rig.polls(HSR, at(23), 21)  # closed at 23:20, before bedtime: Master went to sleep
    rig.polls(IDLE, at(23) + 21 * MINUTE, 3)

    rig.polls(HSR, opened, 3)  # up again for the dailies after the 03:00 reset

    assert rig.said == []
    assert rig.store.marks == []


def test_a_game_opened_late_in_the_night_before_dawn_still_hears_it(tmp_path):
    rig = bedtime_rig(tmp_path)

    assert rig.poll(HSR, at(28) - MINUTE) == [LATE]  # 03:59


def test_a_game_open_since_before_dawn_still_hears_it_after_dawn(tmp_path):
    rig = bedtime_rig(tmp_path)
    rig.store.failing_marks = 15  # the night could not be written from 03:50 until 04:04
    rig.polls(HSR, at(28) - 10 * MINUTE, 15)

    assert rig.poll(HSR, at(28) + 5 * MINUTE) == [LATE]


def test_with_a_bedtime_after_dawn_a_game_open_at_bedtime_still_hears_it(tmp_path):
    rig = bedtime_rig(tmp_path, bedtime=time(5, 0))
    rig.polls(HSR, at(28.5), 30)  # opened at 04:30, still open at 05:00

    assert rig.poll(HSR, at(29)) == ["Master, sudah lewat jam lima pagi, jangan terlalu malam ya."]


def test_with_a_bedtime_after_dawn_a_game_opened_after_it_is_no_late_night(tmp_path):
    rig = bedtime_rig(tmp_path, bedtime=time(5, 0))

    assert rig.poll(HSR, at(29.25)) == []
    assert rig.store.marks == []


def test_without_a_bedtime_she_never_says_it(tmp_path):
    rig = Rig(tmp_path)

    rig.polls(HSR, at(23), 120)

    assert rig.said == []
    assert rig.record().bedtime_told is None


def test_a_bedtime_after_midnight_counts_for_the_evening_before(tmp_path):
    rig = bedtime_rig(tmp_path, bedtime=time(1, 0))
    assert rig.poll(HSR, at(23.75)) == []  # Saturday evening, before its 01:00

    said = rig.poll(HSR, at(25.25))

    assert said == ["Master, sudah lewat jam satu dini hari, jangan terlalu malam ya."]

    assert rig.record().bedtime_told == date(2026, 10, 17)


def test_a_failed_write_of_the_night_says_nothing_and_the_next_poll_tries_again(tmp_path, caplog):
    rig = bedtime_rig(tmp_path)
    rig.store.failing_marks = 1

    with caplog.at_level(logging.WARNING, logger="waifu.play_watch"):
        assert rig.poll(HSR, at(23.75)) == []
    assert rig.poll(HSR, at(23.75) + MINUTE) == [LATE]

    assert rig.said == [(LATE, "neutral")]
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_a_voice_that_fails_is_logged_and_the_nudge_is_not_repeated(tmp_path, caplog):
    rig = bedtime_rig(tmp_path)

    def broken(text: str, emotion: str) -> None:
        raise RuntimeError("the voice loop is gone")

    rig.watcher = PlayTimeWatcher(
        rig.listing, rig.store, rig.config, announce=broken, owner_call="Master", now=rig.clock
    )

    with caplog.at_level(logging.ERROR, logger="waifu.play_watch"):
        rig.poll(HSR, at(23.75))
        assert rig.poll(HSR, at(23.75) + MINUTE) == []

    assert "Could not say the bedtime nudge" in caplog.text
    assert rig.store.marks == [date(2026, 10, 17)]


# --- the thread ----------------------------------------------------------------------------------


class ShortWaits:
    """Stands in for threading.Event.wait: records each timeout asked for and waits on the stop
    event a few milliseconds at most."""

    def __init__(self) -> None:
        self.asked: list[float] = []

    def __call__(self, stop: threading.Event, timeout: float) -> bool:
        self.asked.append(timeout)
        return stop.wait(0.005)


class SignallingListing(Listing):
    def __init__(self, names) -> None:
        super().__init__(names)
        self.listed = threading.Event()

    def __call__(self) -> frozenset[str]:
        self.listed.set()
        return super().__call__()


def watcher_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "play-time-watcher"]


def make_watcher(tmp_path: Path, listing: Listing, **options) -> PlayTimeWatcher:
    clock = Clock(at(20))
    return PlayTimeWatcher(
        listing,
        RecordingStore(tmp_path / "playtime.json", clock),
        PlayTimeConfig(),
        announce=None,
        owner_call="Master",
        now=clock,
        **options,
    )


def test_the_daemon_thread_polls_at_once_and_stop_wakes_it_at_once(tmp_path):
    listing = SignallingListing(IDLE)
    watcher = make_watcher(tmp_path, listing)

    watcher.start()
    try:
        assert listing.listed.wait(5)
        [thread] = watcher_threads()
        assert thread.daemon is True
    finally:
        stopping = clock_time.monotonic()
        watcher.stop()

    assert clock_time.monotonic() - stopping < 1  # although the next poll was a minute away
    assert watcher_threads() == []
    assert listing.calls == 1


def test_the_next_poll_comes_a_poll_interval_after_the_last_one_began(tmp_path):
    listing = SignallingListing(OSError("refused"))
    waits = ShortWaits()
    seconds = iter([1000.0, 1002.0] + [2000.0, 2000.0] * 100)  # the first poll took 2 s
    watcher = make_watcher(tmp_path, listing, clock=lambda: next(seconds), wait=waits)

    watcher.start()
    try:
        deadline = clock_time.monotonic() + 5
        while len(waits.asked) < 2 and clock_time.monotonic() < deadline:
            clock_time.sleep(0.01)
    finally:
        watcher.stop()

    assert waits.asked[:2] == [58.0, 60.0]
    assert listing.calls >= 2  # a failed listing is tried again at the next poll


def test_starting_twice_runs_one_thread(tmp_path):
    watcher = make_watcher(tmp_path, Listing(IDLE), wait=ShortWaits())

    watcher.start()
    try:
        watcher.start()
        assert len(watcher_threads()) == 1
    finally:
        watcher.stop()

    assert watcher_threads() == []


def test_starting_logs_how_often_it_looks_and_nothing_of_what_it_sees(tmp_path, caplog):
    watcher = make_watcher(tmp_path, SignallingListing(HSR), wait=ShortWaits())

    with caplog.at_level(logging.INFO, logger="waifu.play_watch"):
        watcher.start()
        watcher.stop()

    assert [record.getMessage() for record in caplog.records] == [
        "Watching for game processes every 60 s"
    ]
