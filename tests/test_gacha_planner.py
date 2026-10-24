"""The planner's pure rules (v1.50): events and endgame seasons ending soon, told in the evening,
and a stamina that fills before the morning after a game closed late at night.

The rules get the calendars' own values (`HsrCalendar`, `GameCalendar`); one test reads a
real-shaped HSR calendar through `starrail.read_calendar` first, as the watcher does.
"""

import copy
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest
from genshin.models import HSREventCalendar

from waifu.config import GachaRemindersConfig
from waifu.gacha.game_data import ChallengeWindow, GameCalendar, GameEvent
from waifu.gacha.hoyolab import GameStatus, WeeklyTask
from waifu.gacha.planner import (
    ending_events,
    ending_seasons,
    evening_due,
    evening_notice,
    overnight_notice,
    stamina_held,
)
from waifu.gacha.reminders import Notice, ReminderState
from waifu.gacha.starrail import HsrCalendar, HsrChallengeWindow, HsrEvent, read_calendar

WIB = timezone(timedelta(hours=7))
ASIA = 8
RULES = GachaRemindersConfig()  # daily_time 20:00, planner on
EVENING = datetime(2026, 10, 17, 20, 0, tzinfo=WIB)  # a Saturday
HOUR = timedelta(hours=1)


def hours(count: float) -> datetime:
    return EVENING + timedelta(hours=count)


def hsr_event(
    name: str, ends_in: float | None, current: int | None = 3, total: int | None = 7, **changes
) -> HsrEvent:
    ends_at = None if ends_in is None else hours(ends_in)
    return replace(HsrEvent(name, hours(-48), ends_at, current, total, False), **changes)


def window(mode: str, ends_in: float, current: int | None = 30, total: int | None = 36, **changes):
    name = mode.replace("_", " ").title()
    return replace(
        HsrChallengeWindow(mode, name, hours(-24 * 9), hours(ends_in), current, total), **changes
    )


def hsr(events=(), challenges=()) -> HsrCalendar:
    return HsrCalendar("3.7", (), tuple(events), tuple(challenges))


GIFT = hsr_event("Gift of Odyssey", 20)
CHAOS = window("memory_of_chaos", 50)


def told(calendar, now=EVENING, state=None, game="hsr", **options) -> Notice | None:
    state = ReminderState() if state is None else state
    return evening_notice(game, calendar, now, state, ASIA, **options)


# --- which events and seasons ------------------------------------------------------------------


def test_only_running_unfinished_events_with_progress_that_end_within_48_hours_count():
    calendar = hsr(
        [
            hsr_event("Ends In Two Days", 48),
            hsr_event("Ends Later", 48.5),
            hsr_event("Finished", 10, current=7, total=7),
            hsr_event("All Rewards Taken", 10, done=True),
            hsr_event("Counts Nothing", 10, current=None, total=None),
            hsr_event("Simulated Universe", None, current=1, total=10),  # permanent: no end
            hsr_event("Not Started", 30, starts_at=hours(2)),
            hsr_event("Over", -1),
            GIFT,
        ]
    )

    assert [event.name for event in ending_events(calendar, EVENING)] == [
        "Gift of Odyssey",
        "Ends In Two Days",
    ]


def test_at_most_two_events_are_named_the_soonest_first():
    calendar = hsr([hsr_event("Third", 40), hsr_event("First", 5), hsr_event("Second", 30)])

    assert [event.name for event in ending_events(calendar, EVENING)] == ["First", "Second"]


def test_only_running_seasons_below_their_total_that_end_within_72_hours_count():
    calendar = hsr(
        challenges=[
            window("pure_fiction", 72, current=4, total=12),
            window("apocalyptic_shadow", 73, current=0, total=12),
            window("anomaly_arbitration", 30, current=12, total=12),  # the maximum
            window("memory_of_chaos", 30, current=None, total=None),  # not played: no maximum
            window("memory_of_chaos", 80, starts_at=hours(70)),  # the next season
            CHAOS,
        ]
    )

    assert [season.mode for season in ending_seasons(calendar, EVENING)] == [
        "memory_of_chaos",
        "pure_fiction",
    ]


def test_genshin_has_its_spiral_abyss_season_and_no_event_progress():
    abyss = ChallengeWindow("spiral_abyss", "Abyssal Moon Spire", hours(-200), hours(30), 30, 36)
    theater = ChallengeWindow("imaginarium_theater", None, hours(-200), hours(30), None, None)
    lantern = GameEvent("Lantern Rite", hours(-48), hours(10), None, None, False)
    calendar = GameCalendar("6.1", (), (lantern,), (abyss, theater))

    assert ending_events(calendar, EVENING) == []
    assert ending_seasons(calendar, EVENING) == [abyss]
    assert told(calendar, game="genshin") == Notice(
        "genshin",
        "planner",
        "Master, Spiral Abyss di Genshin berakhir sekitar 30 jam lagi, baru dapat 30 dari 36.",
        "surprised",
    )


# --- the evening line ----------------------------------------------------------------------------


def test_an_unfinished_event_ending_soon_is_told_with_its_progress():
    assert told(hsr([GIFT])) == Notice(
        "hsr",
        "planner",
        "Master, event Gift of Odyssey di HSR berakhir sekitar 20 jam lagi, baru 3 dari 7.",
        "surprised",
    )


def test_a_season_comes_first_and_the_events_follow_in_one_line():
    calendar = hsr(
        [GIFT, hsr_event("Planar Fissure", 40, current=0, total=4)],
        [CHAOS, window("pure_fiction", 60, current=4, total=12)],
    )

    assert told(calendar).text == (
        "Master, Memory of Chaos di HSR berakhir sekitar 50 jam lagi, baru dapat 30 dari 36. "
        "Pure Fiction berakhir sekitar 60 jam lagi, baru dapat 4 dari 12. "
        "Event Gift of Odyssey berakhir sekitar 20 jam lagi, baru 3 dari 7. "
        "Event Planar Fissure berakhir sekitar 40 jam lagi, baru 0 dari 4."
    )


def test_a_zzz_event_counts_its_polychrome():
    ridu = GameEvent("Ridu Rush", hours(-72), hours(20), 240, 420, False)

    assert told(GameCalendar("2.3", (), (ridu,), ()), game="zzz").text == (
        "Master, event Ridu Rush di ZZZ berakhir sekitar 20 jam lagi, baru 240 dari 420 Polychrome."
    )


def test_the_time_left_is_in_minutes_under_an_hour_and_else_the_nearest_hour():
    def left(ends_in: float) -> str:
        return told(hsr([hsr_event("Gift of Odyssey", ends_in)])).text.split("sekitar ")[1]

    assert left(0.75).startswith("45 menit lagi")
    assert left(20.4).startswith("20 jam lagi")
    assert left(20.5).startswith("21 jam lagi")


def test_the_line_calls_the_owner_what_she_calls_them():
    assert told(hsr([GIFT]), owner_call="sayang").text.startswith("Sayang, event Gift")


def test_one_line_per_game_per_game_day_and_a_season_only_once():
    state = ReminderState()
    calendar = hsr([GIFT], [CHAOS])
    told(calendar, state=state)

    again = told(calendar, now=EVENING + 2 * HOUR, state=state)
    # The next evening the event still ends within 48 hours, unfinished: told again, alone.
    next_evening = EVENING + 24 * HOUR
    tomorrow = told(hsr([replace(GIFT, ends_at=hours(40))], [CHAOS]), next_evening, state)

    assert again is None
    assert tomorrow.text == (
        "Master, event Gift of Odyssey di HSR berakhir sekitar 16 jam lagi, baru 3 dari 7."
    )


def test_another_game_has_its_own_line_the_same_evening():
    state = ReminderState()
    told(hsr([GIFT]), state=state)

    ridu = GameEvent("Ridu Rush", hours(-72), hours(20), 240, 420, False)
    assert told(GameCalendar("2.3", (), (ridu,), ()), state=state, game="zzz") is not None


def test_nothing_ending_says_nothing_and_keeps_the_day_open():
    state = ReminderState()

    assert told(hsr([hsr_event("Gift of Odyssey", 60)], [window("memory_of_chaos", 80)])) is None
    assert told(hsr(), state=state) is None
    assert state.fired == {}
    assert told(hsr([GIFT]), now=EVENING + HOUR, state=state) is not None


def test_a_season_told_is_remembered_across_a_restart(tmp_path):
    path = tmp_path / "reminders.json"
    state = ReminderState()
    told(hsr(challenges=[CHAOS]), state=state)
    state.save(path, today=date(2026, 10, 17))

    restarted = ReminderState.load(path)

    assert told(hsr(challenges=[CHAOS]), EVENING + 24 * HOUR, restarted) is None


# --- when the evening line is due ----------------------------------------------------------------


def test_the_evening_line_is_due_from_daily_time_until_its_game_day_ends_or_it_was_told():
    state = ReminderState()

    assert not evening_due("hsr", EVENING - timedelta(minutes=1), state, RULES, ASIA)
    assert evening_due("hsr", EVENING, state, RULES, ASIA)
    assert evening_due("hsr", EVENING + 6 * HOUR, state, RULES, ASIA)  # 02:00, same game day
    told(hsr([GIFT]), state=state)
    assert not evening_due("hsr", EVENING + HOUR, state, RULES, ASIA)
    assert evening_due("zzz", EVENING + HOUR, state, RULES, ASIA)


def test_the_evening_line_is_never_due_with_the_planner_off():
    rules = GachaRemindersConfig(planner=False)

    assert not evening_due("hsr", EVENING, ReminderState(), rules, ASIA)


# --- a real-shaped calendar ----------------------------------------------------------------------

FETCHED_AT = EVENING.astimezone(UTC)
API_NOW = int(FETCHED_AT.timestamp())


def time_info(start: datetime, end: datetime) -> dict:
    return {
        "start_ts": str(int(start.timestamp())),
        "end_ts": str(int(end.timestamp())),
        "start_time": {"year": 2026},
        "end_time": {"year": 2026},
        "now": str(API_NOW),
    }


def api_event(name: str, start: datetime, end: datetime, *, current: int, total: int) -> dict:
    """An `act_list` entry as HoYoLAB gives it (the fixture of test_gacha_starrail.py)."""
    return {
        "time_info": time_info(start, end),
        "id": 1,
        "version": "3.7",
        "name": name,
        "panel_desc": "An event description",
        "act_type": "ActivityTypeOther",
        "act_time_type": "ActTimeTypeDefault",
        "act_status": "OtherActStatusUnFinish",
        "reward_list": [],
        "total_progress": total,
        "current_progress": current,
        "special_reward": {
            "item_id": 0,
            "name": "",
            "icon": "",
            "wiki_url": "",
            "num": 0,
            "rarity": 0,
        },
        "is_after_version": False,
        "all_finished": current >= total > 0,
        "show_text": "",
        "strategy": "",
        "multiple_drop_type": 0,
        "multiple_drop_type_list": [],
        "count_refresh_type": 0,
        "count_value": 0,
        "drop_multiple": 0,
        "panel_id": 0,
        "sort_weight": 0,
    }


def api_challenge(kind: str, name: str, start: datetime, end: datetime, **progress) -> dict:
    return {
        "time_info": time_info(start, end),
        "group_id": 1,
        "name_mi18n": name,
        "challenge_type": kind,
        "status": "challengeStatusInProgress",
        "reward_list": [],
        "special_reward": None,
        "total_progress": progress.get("total", 0),
        "current_progress": progress.get("current", 0),
        "show_text": "",
    }


SERVER = timezone(timedelta(hours=ASIA))


def test_a_real_shaped_hsr_calendar_gives_the_evening_line():
    # HoYoLAB ends events and seasons at 03:59:59 server time, before the 04:00 reset.
    event_end = datetime(2026, 10, 18, 3, 59, 59, tzinfo=SERVER)  # Sunday 03:00 WIB: 7 hours
    season_end = datetime(2026, 10, 20, 3, 59, 59, tzinfo=SERVER)  # Tuesday: 55 hours
    payload = {
        "avatar_card_pool_list": [],
        "equip_card_pool_list": [],
        "act_list": [
            api_event("Gift of Odyssey", hours(-48), event_end, current=3, total=7),
            api_event("Planar Fissure", hours(-24), event_end, current=0, total=0),
        ],
        "challenge_list": [
            api_challenge(
                "ChallengeTypeChasm",
                "Gleaming Treasure",
                hours(-200),
                season_end,
                current=30,
                total=36,
            ),
            api_challenge("ChallengeTypeStory", "Nameless Tales", hours(-100), season_end),
        ],
        "cur_game_version": "3.7",
        "now": str(API_NOW),
    }
    calendar = read_calendar(HSREventCalendar(**copy.deepcopy(payload)), FETCHED_AT)

    assert told(calendar).text == (
        "Master, Memory of Chaos di HSR berakhir sekitar 55 jam lagi, baru dapat 30 dari 36. "
        "Event Gift of Odyssey berakhir sekitar 7 jam lagi, baru 3 dari 7."
    )


# --- the overnight line --------------------------------------------------------------------------

MORNING = time(7, 0)  # briefing.time
CLOSED = datetime(2026, 10, 17, 23, 40, tzinfo=WIB)
POLL = datetime(2026, 10, 17, 23, 50, tzinfo=WIB)  # the gacha poll after the game closed
HSR_STATUS = GameStatus(
    game="hsr",
    stamina=208,
    stamina_max=300,
    stamina_full_in_s=(4 * 60 + 20) * 60,  # full at 04:10
    daily_current=500,
    daily_max=500,
    daily_done=True,
    weekly=(WeeklyTask("Echo of War", 3, 3),),
    expeditions_total=4,
    expeditions_finished=1,
    checked_in=True,
)


def overnight(
    ended_at=CLOSED, status=HSR_STATUS, now=POLL, state=None, game="hsr", **options
) -> Notice | None:
    state = ReminderState() if state is None else state
    return overnight_notice(game, ended_at, status, now, state, MORNING, **options)


def test_a_stamina_filling_before_the_morning_after_a_late_game_is_told():
    assert overnight() == Notice(
        "hsr",
        "planner",
        "Master, Trailblaze Power di HSR penuh jam 04.10, sebelum kamu bangun.",
        "surprised",
    )


def test_the_line_says_the_minute_it_is_surely_full_and_calls_the_owner_her_way():
    status = replace(HSR_STATUS, stamina_full_in_s=HSR_STATUS.stamina_full_in_s + 10)

    assert overnight(status=status, owner_call="sayang").text == (
        "Sayang, Trailblaze Power di HSR penuh jam 04.11, sebelum kamu bangun."
    )


def test_another_game_names_its_own_stamina():
    status = replace(HSR_STATUS, game="genshin", stamina=100, stamina_max=200)

    assert overnight(status=status, game="genshin").text.startswith(
        "Master, Original Resin di Genshin penuh jam 04.10"
    )


def at_clock(hour: int, minute: int, day: int = 17) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=WIB)


@pytest.mark.parametrize(
    ("ended_at", "now", "late"),
    [
        (at_clock(21, 59), at_clock(22, 5), False),
        (at_clock(22, 0), at_clock(22, 5), True),
        (at_clock(2, 59, day=18), at_clock(3, 5, day=18), True),
        (at_clock(3, 0, day=18), at_clock(3, 5, day=18), False),
    ],
)
def test_only_a_game_closed_from_22_00_until_03_00_counts(ended_at, now, late):
    status = replace(HSR_STATUS, stamina_full_in_s=3 * 3600)

    assert (overnight(ended_at=ended_at, now=now, status=status) is not None) is late


def test_a_stamina_full_only_after_the_morning_or_already_full_says_nothing():
    at_seven = replace(HSR_STATUS, stamina_full_in_s=(7 * 60 + 10) * 60)  # 07:00 itself
    full = replace(HSR_STATUS, stamina=300, stamina_full_in_s=0)

    assert overnight(status=at_seven) is None
    assert overnight(status=full) is None  # the stamina reminder says it at once


def test_nothing_is_told_once_the_morning_has_come():
    assert overnight(ended_at=at_clock(1, 0, day=18), now=at_clock(7, 0, day=18)) is None


def test_the_overnight_line_is_told_once_a_night():
    state = ReminderState()
    overnight(state=state)

    again = overnight(ended_at=at_clock(1, 0, day=18), now=at_clock(1, 5, day=18), state=state)
    next_night = overnight(ended_at=CLOSED + 24 * HOUR, now=POLL + 24 * HOUR, state=state)

    assert again is None
    assert next_night is not None


def test_the_stamina_line_is_held_until_the_morning_after_the_overnight_line():
    state = ReminderState()
    assert not stamina_held("hsr", POLL, state, MORNING)
    overnight(state=state)

    assert stamina_held("hsr", POLL, state, MORNING)
    assert stamina_held("hsr", at_clock(6, 59, day=18), state, MORNING)
    assert not stamina_held("hsr", at_clock(7, 0, day=18), state, MORNING)
    assert not stamina_held("genshin", POLL, state, MORNING)


def test_the_hold_survives_a_restart(tmp_path):
    path = tmp_path / "reminders.json"
    state = ReminderState()
    overnight(state=state)
    state.save(path, today=date(2026, 10, 17))

    assert stamina_held("hsr", at_clock(3, 10, day=18), ReminderState.load(path), MORNING)
