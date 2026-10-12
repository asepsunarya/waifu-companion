from datetime import date, datetime, time, timedelta, timezone

import pytest

from waifu.gacha.games import (
    GAMES,
    checkin_day,
    game_day,
    is_last_day_of_week,
    is_standard,
    next_daily_reset,
    reminder_due,
)

WIB = timezone(timedelta(hours=7))
ASIA = 8


def wib(year, month, day, hour, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=WIB)


def test_the_three_games_are_known_by_their_config_ids():
    assert list(GAMES) == ["hsr", "genshin", "zzz"]
    assert [GAMES[g].short for g in GAMES] == ["HSR", "Genshin", "ZZZ"]
    assert GAMES["zzz"].top_rarity == 4  # an S-rank is rank_type 4 in the ZZZ history


def test_banners_are_listed_in_display_order_with_their_hard_pity():
    assert [(b.type, b.hard_pity) for b in GAMES["hsr"].banners] == [
        (11, 90),
        (12, 80),
        (1, 90),
        (21, 90),
        (22, 80),
    ]
    assert [b.type for b in GAMES["genshin"].banners] == [301, 302, 200, 500]
    assert [b.type for b in GAMES["zzz"].banners] == [2, 3, 1, 5, 102, 103]


def test_only_the_regular_character_banner_has_a_fifty_fifty():
    for game in GAMES.values():
        assert [b.type for b in game.banners if b.fifty_fifty] == [game.banners[0].type]


def test_hsr_collab_banners_use_their_own_history_endpoint():
    endpoints = {b.type: b.endpoint for b in GAMES["hsr"].banners}
    assert endpoints == {
        11: "getGachaLog",
        12: "getGachaLog",
        1: "getGachaLog",
        21: "getLdGachaLog",
        22: "getLdGachaLog",
    }


@pytest.mark.parametrize(
    ("game", "name"),
    [
        ("genshin", "Yumemizuki Mizuki"),
        ("genshin", "Dehya"),
        ("hsr", "Himeko"),
        ("zzz", "Soldier 11"),
        ("zzz", "Von Lycaon"),
        ("zzz", "Alexandrina Sebastiane"),
    ],
)
def test_standard_five_stars_count_as_a_lost_fifty_fifty(game, name):
    assert is_standard(GAMES[game], name)


def test_limited_five_stars_are_not_standard_unless_the_owner_lists_them():
    hsr = GAMES["hsr"]
    assert not is_standard(hsr, "Acheron")
    assert not is_standard(hsr, "Yunli")
    assert is_standard(hsr, "Yunli", ["yunli"])  # the owner's Celestial Invitation pick


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (wib(2026, 10, 12, 2, 59), date(2026, 10, 11)),  # 03:59 server time: still yesterday
        (wib(2026, 10, 12, 3, 0), date(2026, 10, 12)),  # 04:00 server time: the daily reset
        (wib(2026, 10, 12, 23, 30), date(2026, 10, 12)),
    ],
)
def test_the_game_day_changes_at_four_in_the_morning_server_time(now, expected):
    assert game_day(now, ASIA) == expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (wib(2026, 10, 12, 22, 59), date(2026, 10, 12)),
        (wib(2026, 10, 12, 23, 0), date(2026, 10, 13)),  # midnight in UTC+8
    ],
)
def test_the_checkin_day_changes_at_midnight_server_time(now, expected):
    assert checkin_day(now, ASIA) == expected


def test_next_daily_reset_is_three_in_the_morning_in_wib():
    reset = next_daily_reset(wib(2026, 10, 12, 13, 0), ASIA)
    assert reset == wib(2026, 10, 13, 3, 0)
    assert next_daily_reset(wib(2026, 10, 13, 2, 0), ASIA) == wib(2026, 10, 13, 3, 0)


def test_sunday_is_the_last_game_day_before_the_weekly_reset():
    assert is_last_day_of_week(date(2026, 10, 18))  # a Sunday
    assert not is_last_day_of_week(date(2026, 10, 17))
    assert not is_last_day_of_week(date(2026, 10, 19))  # Monday: a new week


@pytest.mark.parametrize(
    ("now", "due"),
    [
        (wib(2026, 10, 12, 19, 59), False),
        (wib(2026, 10, 12, 20, 0), True),
        (wib(2026, 10, 13, 1, 0), True),  # past midnight but still the same game day
        (wib(2026, 10, 13, 3, 30), False),  # a new game day has begun
    ],
)
def test_the_evening_reminder_is_due_from_its_local_time_until_the_reset(now, due):
    assert reminder_due(now, time(20, 0), ASIA) is due


def test_a_reminder_time_after_midnight_belongs_to_the_end_of_the_game_day():
    assert not reminder_due(wib(2026, 10, 12, 13, 0), time(2, 0), ASIA)
    assert reminder_due(wib(2026, 10, 13, 2, 30), time(2, 0), ASIA)
