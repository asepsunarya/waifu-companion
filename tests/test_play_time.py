"""Play time: the session rules, the periods and totals, and the store.

Times are aware WIB from Saturday 2026-10-17 (`at(hour)`: 25.5 is Sunday 01:30). Polls are a
minute apart unless a test says otherwise; no test reads the real process list or the owner's
data folder (stores live in tmp_path).
"""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from waifu.finance.values import WIB
from waifu.play_time import (
    RESUME_GAP,
    PlayRecord,
    PlayTimeStore,
    PlayTotal,
    Session,
    Tracked,
    Tracking,
    follow_games,
    games_running,
    period_window,
    play_totals,
    played_seconds,
    playing_now,
    resume_games,
    whole_minutes,
)

DAY = datetime(2026, 10, 17, tzinfo=WIB)  # a Saturday
POLL = timedelta(minutes=1)
GAMES = {
    "hsr": ["StarRail.exe"],
    "genshin": ["GenshinImpact.exe", "YuanShen.exe"],
    "zzz": ["ZenlessZoneZero.exe"],
}


def at(hour: float) -> datetime:
    """`hour` hours after Saturday 00:00 WIB, to the second."""
    return DAY + timedelta(seconds=round(hour * 3600))


def minute(n: int, hour: float = 20) -> datetime:
    """`n` polls (minutes) after `hour` o'clock."""
    return at(hour) + n * POLL


# --- which games are running ------------------------------------------------------------------


def test_a_game_runs_when_one_of_its_executable_names_is_listed_ignoring_case():
    names = {"explorer.exe", "starrail.exe", "yuanshen.exe"}

    assert games_running(names, GAMES) == frozenset({"hsr", "genshin"})
    assert games_running({"STARRAIL.exe"}, GAMES) == frozenset({"hsr"})


def test_the_launcher_and_the_unity_crash_handler_are_no_game():
    names = {"hyp.exe", "hyphelper.exe", "unitycrashhandler64.exe", "launcher.exe"}

    assert games_running(names, GAMES) == frozenset()


def test_only_whole_names_count_never_a_part_of_one():
    names = {"starrail.exe.bak", "mystarrail.exe", "starrail"}

    assert games_running(names, GAMES) == frozenset()


def test_only_the_configured_games_are_counted():
    assert games_running({"starrail.exe", "zenlesszonezero.exe"}, {"zzz": ["ZZZ.exe"]}) == set()
    assert games_running({"zzz.exe"}, {"zzz": ["ZZZ.exe"]}) == frozenset({"zzz"})


# --- one poll after another ---------------------------------------------------------------------


def poll(tracking: Tracking, seen: set[str], when: datetime) -> tuple[Tracking, tuple]:
    return follow_games(tracking, seen, when, poll=POLL)


def idle(when: datetime) -> Tracking:
    return Tracking((), when)


def sessions_of(tracking: Tracking) -> list[Session]:
    return [tracked.session for tracked in tracking.open]


def test_a_session_starts_at_the_first_poll_that_sees_its_game():
    tracking, finished = poll(idle(minute(0)), {"hsr"}, minute(1))

    assert sessions_of(tracking) == [Session("hsr", minute(1), minute(1))]
    assert finished == ()
    assert tracking.last_poll == minute(1)


def test_each_poll_that_sees_the_game_moves_its_end():
    tracking, _ = poll(idle(minute(0)), {"hsr"}, minute(1))
    tracking, finished = poll(tracking, {"hsr"}, minute(2))

    assert sessions_of(tracking) == [Session("hsr", minute(1), minute(2))]
    assert finished == ()


def test_one_missed_poll_never_splits_a_session():
    tracking, _ = poll(idle(minute(0)), {"hsr"}, minute(1))
    tracking, _ = poll(tracking, {"hsr"}, minute(2))
    tracking, missed = poll(tracking, set(), minute(3))
    tracking, finished = poll(tracking, {"hsr"}, minute(4))

    assert (missed, finished) == ((), ())
    assert sessions_of(tracking) == [Session("hsr", minute(1), minute(4))]


def test_two_missed_polls_in_a_row_end_the_session_at_the_last_poll_that_saw_it():
    tracking, _ = poll(idle(minute(0)), {"hsr"}, minute(1))
    tracking, _ = poll(tracking, {"hsr"}, minute(2))
    tracking, _ = poll(tracking, set(), minute(3))
    tracking, finished = poll(tracking, set(), minute(4))

    assert finished == (Session("hsr", minute(1), minute(2)),)
    assert tracking.open == ()


def test_a_game_back_after_its_session_ended_starts_a_new_one():
    tracking = Tracking((Tracked(Session("hsr", minute(1), minute(2)), missed=True),), minute(3))
    tracking, finished = poll(tracking, set(), minute(4))
    tracking, _ = poll(tracking, {"hsr"}, minute(5))

    assert finished == (Session("hsr", minute(1), minute(2)),)
    assert sessions_of(tracking) == [Session("hsr", minute(5), minute(5))]


def test_a_session_seen_at_one_poll_only_is_dropped_when_it_ends():
    tracking, _ = poll(idle(minute(0)), {"zzz"}, minute(1))
    tracking, _ = poll(tracking, set(), minute(2))
    tracking, finished = poll(tracking, set(), minute(3))

    assert (tracking.open, finished) == ((), ())


def test_two_games_are_followed_each_on_its_own():
    tracking, _ = poll(idle(minute(0)), {"hsr", "genshin"}, minute(1))
    tracking, _ = poll(tracking, {"hsr", "genshin"}, minute(2))
    tracking, _ = poll(tracking, {"hsr"}, minute(3))
    tracking, finished = poll(tracking, {"hsr"}, minute(4))

    assert finished == (Session("genshin", minute(1), minute(2)),)
    assert sessions_of(tracking) == [Session("hsr", minute(1), minute(4))]


def test_a_poll_long_after_the_last_one_ends_every_session_there_and_starts_afresh():
    # The PC slept from 20:02 until 21:00: a gap of more than three polls is a break.
    tracking, _ = poll(idle(minute(0)), {"hsr", "zzz"}, minute(1))
    tracking, _ = poll(tracking, {"hsr", "zzz"}, minute(2))
    tracking, finished = poll(tracking, {"hsr"}, at(21))

    assert set(finished) == {
        Session("hsr", minute(1), minute(2)),
        Session("zzz", minute(1), minute(2)),
    }
    assert sessions_of(tracking) == [Session("hsr", at(21), at(21))]


def test_a_gap_of_three_polls_is_still_one_session_and_more_is_a_break():
    tracking, _ = poll(idle(minute(0)), {"hsr"}, minute(1))
    tracking, _ = poll(tracking, {"hsr"}, minute(2))
    kept, finished = poll(tracking, {"hsr"}, minute(5))
    broken, ended = poll(tracking, {"hsr"}, minute(5) + timedelta(seconds=1))

    assert finished == () and sessions_of(kept) == [Session("hsr", minute(1), minute(5))]
    assert ended == (Session("hsr", minute(1), minute(2)),)


def test_a_clock_set_back_ends_every_session_at_its_last_poll():
    tracking, _ = poll(idle(minute(0)), {"hsr"}, minute(1))
    tracking, _ = poll(tracking, {"hsr"}, minute(2))
    tracking, finished = poll(tracking, {"hsr"}, minute(1))

    assert finished == (Session("hsr", minute(1), minute(2)),)
    assert sessions_of(tracking) == [Session("hsr", minute(1), minute(1))]


def test_the_poll_interval_is_the_given_one():
    tracking = Tracking((Tracked(Session("hsr", minute(0), minute(10))),), minute(10))

    tracking, finished = follow_games(tracking, {"hsr"}, minute(40), poll=timedelta(minutes=10))

    assert finished == () and sessions_of(tracking) == [Session("hsr", minute(0), minute(40))]


def test_times_are_kept_to_the_second():
    tracking, _ = poll(idle(minute(0)), {"hsr"}, minute(1) + timedelta(microseconds=900))

    assert sessions_of(tracking) == [Session("hsr", minute(1), minute(1))]


# --- a run's first poll --------------------------------------------------------------------------


def test_a_game_seen_shortly_before_a_restart_goes_on_with_its_session():
    # The last run stopped at 22:10 and ended HSR's session there; this one starts at 22:12.
    record = PlayRecord(sessions=(Session("hsr", at(20), at(22) + 10 * POLL),))

    tracking, finished = resume_games(record, {"hsr"}, at(22) + 12 * POLL)

    assert sessions_of(tracking) == [Session("hsr", at(20), at(22) + 12 * POLL)]
    assert finished == ()


def test_a_crash_s_open_session_goes_on_when_the_game_still_runs():
    record = PlayRecord(open=(Session("hsr", at(20), at(22)),))

    tracking, finished = resume_games(record, {"hsr"}, at(22) + 3 * POLL)

    assert sessions_of(tracking) == [Session("hsr", at(20), at(22) + 3 * POLL)]
    assert finished == ()


def test_resuming_reaches_back_five_minutes_and_no_further():
    assert timedelta(minutes=5) == RESUME_GAP
    record = PlayRecord(sessions=(Session("hsr", at(20), at(22)),))

    resumed, _ = resume_games(record, {"hsr"}, at(22) + RESUME_GAP)
    fresh, _ = resume_games(record, {"hsr"}, at(22) + RESUME_GAP + timedelta(seconds=1))

    assert sessions_of(resumed) == [Session("hsr", at(20), at(22) + RESUME_GAP)]
    late = at(22) + RESUME_GAP + timedelta(seconds=1)
    assert sessions_of(fresh) == [Session("hsr", late, late)]


def test_only_the_newest_session_of_the_game_is_resumed():
    record = PlayRecord(
        sessions=(Session("hsr", at(18), at(19)), Session("hsr", at(21), at(22))),
    )

    tracking, _ = resume_games(record, {"hsr"}, at(22) + POLL)

    assert sessions_of(tracking) == [Session("hsr", at(21), at(22) + POLL)]


def test_a_crash_s_open_session_of_a_game_no_longer_running_ends_at_its_last_poll():
    record = PlayRecord(open=(Session("hsr", at(20), at(22)), Session("zzz", at(21), at(21))))

    tracking, finished = resume_games(record, set(), at(22) + POLL)

    assert tracking.open == ()
    assert finished == (Session("hsr", at(20), at(22)),)  # the one of no length dropped


def test_a_stale_open_session_ends_and_its_game_starts_afresh():
    record = PlayRecord(open=(Session("hsr", at(10), at(12)),))

    tracking, finished = resume_games(record, {"hsr"}, at(22))

    assert finished == (Session("hsr", at(10), at(12)),)
    assert sessions_of(tracking) == [Session("hsr", at(22), at(22))]


def test_a_session_from_the_future_is_never_resumed():
    record = PlayRecord(sessions=(Session("hsr", at(20), at(23)),))  # the clock went back

    tracking, _ = resume_games(record, {"hsr"}, at(22))

    assert sessions_of(tracking) == [Session("hsr", at(22), at(22))]


def test_the_first_poll_s_time_is_the_last_poll():
    tracking, _ = resume_games(PlayRecord(), set(), at(22))

    assert tracking == Tracking((), at(22))


# --- periods and totals --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("period", "since", "until"),
    [
        ("today", at(0), at(21.5)),
        ("yesterday", at(-24), at(0)),
        ("week", at(-5 * 24), at(21.5)),  # Monday 12 October 00:00
        ("month", datetime(2026, 10, 1, tzinfo=WIB), at(21.5)),
    ],
)
def test_a_period_runs_from_its_wib_midnight(period, since, until):
    assert period_window(period, at(21.5)) == (since, until)


def test_periods_are_counted_in_wib_whatever_zone_now_is_given_in():
    # 18:00 UTC on Saturday is Sunday 01:00 in WIB: today is Sunday.
    now = datetime(2026, 10, 17, 18, tzinfo=UTC)

    assert period_window("today", now) == (at(24), at(25))


def test_an_unknown_period_is_a_value_error():
    with pytest.raises(ValueError, match="period"):
        period_window("year", at(12))


def test_played_time_is_cut_at_the_period_s_edges():
    session = Session("hsr", at(23), at(25.5))  # Saturday 23:00 until Sunday 01:30

    assert played_seconds([session], at(0), at(24)) == 3600
    assert played_seconds([session], at(24), at(48)) == 90 * 60


def test_overlapping_sessions_count_their_time_once():
    sessions = [Session("hsr", at(20), at(22)), Session("zzz", at(21), at(23))]

    assert played_seconds(sessions, at(0), at(24)) == 3 * 3600


def test_sessions_outside_the_period_count_nothing():
    sessions = [Session("hsr", at(10), at(11)), Session("hsr", at(30), at(31))]

    assert played_seconds(sessions, at(12), at(24)) == 0


@pytest.mark.parametrize(("seconds", "minutes"), [(0, 0), (29, 0), (30, 1), (89, 1), (3600, 60)])
def test_whole_minutes_round_to_the_nearest_with_a_half_minute_up(seconds, minutes):
    assert whole_minutes(seconds) == minutes


def test_totals_give_every_game_its_time_and_sessions_in_order():
    record = PlayRecord(
        sessions=(
            Session("hsr", at(8), at(9)),
            Session("hsr", at(20), at(21.5)),
            Session("zzz", at(-1), at(0.5)),  # from Friday: half an hour of it today
            Session("genshin", at(-5), at(-4)),  # yesterday only
        ),
        open=(Session("hsr", at(22), at(22.25)),),
    )

    totals = play_totals(record, ["hsr", "genshin", "zzz"], at(0), at(23))

    assert totals == {
        "hsr": PlayTotal(2 * 3600 + 45 * 60, 3),  # 1 h, 1.5 h, and the open quarter hour
        "genshin": PlayTotal(0, 0),
        "zzz": PlayTotal(30 * 60, 1),
    }
    assert list(totals) == ["hsr", "genshin", "zzz"]


def test_a_session_across_midnight_counts_once_on_each_day():
    record = PlayRecord(sessions=(Session("hsr", at(23), at(25)),))

    assert play_totals(record, ["hsr"], at(0), at(24)) == {"hsr": PlayTotal(3600, 1)}
    assert play_totals(record, ["hsr"], at(24), at(48)) == {"hsr": PlayTotal(3600, 1)}


def test_a_session_touching_the_period_only_at_its_edge_is_not_in_it():
    record = PlayRecord(sessions=(Session("hsr", at(22), at(24)),))

    assert play_totals(record, ["hsr"], at(24), at(48)) == {"hsr": PlayTotal(0, 0)}


def test_a_game_is_playing_now_while_its_open_session_was_seen_within_three_polls():
    record = PlayRecord(
        sessions=(Session("zzz", at(21), at(22)),),  # finished: not open, however recent
        open=(Session("hsr", at(20), at(22) - 3 * POLL), Session("genshin", at(20), at(21))),
    )

    assert playing_now(record, at(22), poll=POLL) == frozenset({"hsr"})
    assert playing_now(record, at(22) + timedelta(seconds=1), poll=POLL) == frozenset()


def test_games_not_tracked_are_left_out_of_the_totals():
    record = PlayRecord(sessions=(Session("zzz", at(8), at(9)),))

    assert play_totals(record, ["hsr"], at(0), at(24)) == {"hsr": PlayTotal(0, 0)}


# --- the store --------------------------------------------------------------------------------


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_store(path: Path, clock: Clock | None = None, **options) -> PlayTimeStore:
    options.setdefault("games", ["hsr", "genshin", "zzz"])
    options.setdefault("keep_days", 90)
    return PlayTimeStore(path, now=clock or Clock(at(22)), **options)


def test_a_missing_file_is_an_empty_record_and_stays_missing(tmp_path):
    path = tmp_path / "data" / "playtime.json"
    store = make_store(path)

    assert store.record() == PlayRecord()
    assert store.totals(at(0), at(22)) == {g: PlayTotal(0, 0) for g in ("hsr", "genshin", "zzz")}
    assert not path.exists()


def test_saving_writes_the_open_and_finished_sessions_as_aware_wib_times(tmp_path):
    path = tmp_path / "data" / "playtime.json"
    store = make_store(path)

    store.save([Session("zzz", at(21), at(22))], [Session("hsr", at(20), at(21))])

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "sessions": [
            {
                "game": "hsr",
                "start": "2026-10-17T20:00:00+07:00",
                "end": "2026-10-17T21:00:00+07:00",
            }
        ],
        "open": [
            {
                "game": "zzz",
                "start": "2026-10-17T21:00:00+07:00",
                "last_seen": "2026-10-17T22:00:00+07:00",
            }
        ],
        "bedtime_told": None,
    }
    assert store.record() == PlayRecord(
        sessions=(Session("hsr", at(20), at(21)),), open=(Session("zzz", at(21), at(22)),)
    )


def test_open_sessions_replace_the_file_s_and_finished_ones_are_added(tmp_path):
    store = make_store(tmp_path / "playtime.json")
    store.save([Session("hsr", at(20), at(20.5))], [Session("zzz", at(8), at(9))])

    store.save([], [Session("hsr", at(20), at(21))])

    assert store.record() == PlayRecord(
        sessions=(Session("zzz", at(8), at(9)), Session("hsr", at(20), at(21)))
    )


def test_a_resumed_session_replaces_its_stored_one_so_it_is_never_counted_twice(tmp_path):
    store = make_store(tmp_path / "playtime.json")
    store.save([], [Session("hsr", at(20), at(22))])

    store.save([Session("hsr", at(20), at(22.1))])  # resumed after a restart
    assert store.record() == PlayRecord(open=(Session("hsr", at(20), at(22.1)),))
    store.save([], [Session("hsr", at(20), at(23))])  # and finished again

    assert store.record() == PlayRecord(sessions=(Session("hsr", at(20), at(23)),))
    assert store.totals(at(0), at(24))["hsr"] == PlayTotal(3 * 3600, 1)


def test_totals_count_the_open_sessions_up_to_their_last_poll(tmp_path):
    store = make_store(tmp_path / "playtime.json")
    store.save([Session("hsr", at(21), at(21.75))], [Session("hsr", at(8), at(9))])

    assert store.totals(at(0), at(22)) == {
        "hsr": PlayTotal(3600 + 45 * 60, 2),
        "genshin": PlayTotal(0, 0),
        "zzz": PlayTotal(0, 0),
    }


def test_totals_are_for_the_tracked_games_only(tmp_path):
    store = make_store(tmp_path / "playtime.json", games=["zzz"])
    store.save([], [Session("hsr", at(8), at(9))])

    assert store.totals(at(0), at(22)) == {"zzz": PlayTotal(0, 0)}


def test_sessions_older_than_keep_days_are_left_out_and_dropped_at_the_next_write(tmp_path):
    path = tmp_path / "playtime.json"
    clock = Clock(at(-24))
    store = make_store(path, clock, keep_days=2)
    old, kept = Session("hsr", at(-30), at(-27)), Session("hsr", at(-26), at(-25))
    store.save([], [old, kept])

    # Two days before Saturday 22:00 is Thursday 22:00: one ended an hour before, one after.
    clock.now = at(22)
    assert store.record() == PlayRecord(sessions=(kept,))
    starts = [entry["start"] for entry in json.loads(path.read_text(encoding="utf-8"))["sessions"]]
    assert len(starts) == 2  # reading changes nothing

    store.save([Session("zzz", at(21), at(22))])

    stored = json.loads(path.read_text(encoding="utf-8"))["sessions"]
    assert [entry["start"] for entry in stored] == ["2026-10-15T22:00:00+07:00"]


def test_marking_the_bedtime_keeps_the_sessions(tmp_path):
    store = make_store(tmp_path / "playtime.json")
    store.save([Session("hsr", at(21), at(22))], [Session("zzz", at(8), at(9))])

    store.mark_bedtime(date(2026, 10, 17))

    assert store.record() == PlayRecord(
        sessions=(Session("zzz", at(8), at(9)),),
        open=(Session("hsr", at(21), at(22)),),
        bedtime_told=date(2026, 10, 17),
    )


def test_every_call_reads_the_file_again(tmp_path):
    path = tmp_path / "playtime.json"
    writer, reader = make_store(path), make_store(path)
    assert reader.record() == PlayRecord()

    writer.save([], [Session("hsr", at(8), at(9))])

    assert reader.record().sessions == (Session("hsr", at(8), at(9)),)


def test_a_file_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "playtime.json"
    make_store(path).save([], [Session("hsr", at(8), at(9))])
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    assert make_store(path).record().sessions == (Session("hsr", at(8), at(9)),)


def stored(**changes) -> dict:
    record = {
        "version": 1,
        "sessions": [
            {
                "game": "hsr",
                "start": "2026-10-17T20:00:00+07:00",
                "end": "2026-10-17T21:00:00+07:00",
            }
        ],
        "open": [],
        "bedtime_told": None,
    }
    record.update(changes)
    return record


SESSION = {"game": "hsr", "start": "2026-10-17T20:00:00+07:00", "end": "2026-10-17T21:00:00+07:00"}
OPEN = {
    "game": "hsr",
    "start": "2026-10-17T21:00:00+07:00",
    "last_seen": "2026-10-17T22:00:00+07:00",
}


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps([]),
        json.dumps(stored(version=2)),
        json.dumps(stored(extra=1)),
        json.dumps({key: value for key, value in stored().items() if key != "open"}),
        json.dumps(stored(sessions={})),
        json.dumps(stored(sessions=[{**SESSION, "game": "wuwa"}])),
        json.dumps(stored(sessions=[{**SESSION, "game": ["hsr"]}])),  # not hashable
        json.dumps(stored(sessions=[{**SESSION, "game": {"id": "hsr"}}])),
        json.dumps(stored(open=[{**OPEN, "game": ["hsr"]}])),
        json.dumps(stored(sessions=[{**SESSION, "extra": 1}])),
        json.dumps(stored(sessions=[{**SESSION, "start": "2026-10-17T20:00:00"}])),  # naive
        json.dumps(stored(sessions=[{**SESSION, "end": "yesterday"}])),
        json.dumps(stored(sessions=[{**SESSION, "end": "2026-10-17T19:00:00+07:00"}])),
        json.dumps(stored(open=[{**OPEN, "end": OPEN["last_seen"]}])),
        json.dumps(stored(open=[OPEN, {**OPEN, "start": "2026-10-17T21:30:00+07:00"}])),
        json.dumps(stored(bedtime_told="17-10-2026")),
        json.dumps(stored(bedtime_told="2026-02-30")),
    ],
)
def test_a_file_that_is_not_a_play_time_record_is_moved_aside_and_never_overwritten(
    tmp_path, content, caplog
):
    path = tmp_path / "playtime.json"
    path.write_text(content, encoding="utf-8")
    store = make_store(path)

    with caplog.at_level(logging.ERROR, logger="waifu.play_time"):
        assert store.record() == PlayRecord()

    [moved] = tmp_path.glob("playtime.json.corrupt-*")
    assert moved.name == "playtime.json.corrupt-20261017-220000"
    assert moved.read_text(encoding="utf-8") == content
    assert not path.exists()
    assert "not a play time record" in caplog.text


def test_a_broken_file_moved_aside_twice_in_one_second_keeps_both(tmp_path):
    path = tmp_path / "playtime.json"
    store = make_store(path)
    for content in ("first", "second"):
        path.write_text(content, encoding="utf-8")
        store.record()

    moved = sorted(path.name for path in tmp_path.glob("playtime.json.corrupt-*"))
    assert moved == [
        "playtime.json.corrupt-20261017-220000",
        "playtime.json.corrupt-20261017-220000-2",
    ]


def test_saving_over_a_broken_file_moves_it_aside_first(tmp_path):
    path = tmp_path / "playtime.json"
    path.write_text("broken", encoding="utf-8")

    make_store(path).save([], [Session("hsr", at(8), at(9))])

    assert make_store(path).record().sessions == (Session("hsr", at(8), at(9)),)
    [moved] = tmp_path.glob("playtime.json.corrupt-*")
    assert moved.read_text(encoding="utf-8") == "broken"


def test_a_naive_clock_is_refused(tmp_path):
    store = make_store(tmp_path / "playtime.json", Clock(datetime(2026, 10, 17, 22)))

    with pytest.raises(ValueError, match="aware"):
        store.record()
