"""What Master earned in a month, from HoYoLAB's monthly diaries: the values, the readers of
genshin.py's models, and the month window.

Every model is a real genshin.py model built from a sample dict in HoYoLAB's shape; nothing
reaches HoYoLAB.
"""

import copy
from dataclasses import FrozenInstanceError, fields
from datetime import date, datetime, timedelta, timezone

import pydantic
import pytest
from genshin.models import Diary, StarRailDiary, ZZZDiary

from waifu.gacha.income import (
    GameIncome,
    IncomeCategory,
    IncomeCurrency,
    answers_month,
    income_months,
    month_text,
    parse_month,
    read_genshin_income,
    read_hsr_income,
    read_zzz_income,
)

WIB = timezone(timedelta(hours=7))
OCTOBER = date(2026, 10, 1)
SEPTEMBER = date(2026, 9, 1)
NICKNAME = "Kirana"  # never "Master": ZZZ names a currency Encrypted Master Tape
AVATAR = "master-avatar-icon"

# The Traveler's Diary as HoYoLAB answers it (genshin.py keeps the month as 10).
GENSHIN_DIARY = {
    "uid": 800000002,
    "region": "os_asia",
    "account_id": 1,
    "nickname": NICKNAME,
    "date": "2026-10-18",
    "month": 10,
    "data_month": 10,
    "data_last_month": 9,
    "optional_month": [8, 9, 10],
    "day_data": {"current_primogems": 120, "current_mora": 35000, "last_primogems": 0},
    "month_data": {
        "current_primogems": 5400,
        "current_mora": 1234567,
        "last_primogems": 9000,
        "last_mora": 2000000,
        "current_primogems_level": 2,
        "primogem_rate": -40,
        "mora_rate": -38,
        "group_by": [
            {"action_id": 2, "action": "Events", "num": 1600, "percent": 30},
            {"action_id": 1, "action": "Daily Activity", "num": 1800, "percent": 33},
            {"action_id": 3, "action": "Spiral Abyss", "num": 1200, "percent": 22},
            {"action_id": 4, "action": "Quests", "num": 0, "percent": 0},
            {"action_id": 5, "action": "Mail", "num": 800, "percent": 15},
        ],
    },
    "lantern": False,
}

# The Trailblaze Monthly Calendar (genshin.py keeps the month as 202610).
HSR_DIARY = {
    "uid": 800000001,
    "region": "prod_official_asia",
    "nickname": NICKNAME,
    "login_flag": True,
    "optional_month": ["202608", "202609", "202610"],
    "month": "202610",
    "data_month": "202610",
    "month_data": {
        "current_hcoin": 4800,
        "current_rails_pass": 5,
        "last_hcoin": 10000,
        "last_rails_pass": 12,
        "hcoin_rate": -52,
        "rails_rate": -58,
        "group_by": [
            {"action": "daily_reward", "action_name": "Daily Activity", "num": 1800, "percent": 38},
            {"action": "event_reward", "action_name": "Events", "num": 1200, "percent": 25},
            {
                "action": "abyss_reward",
                "action_name": "Memory of Chaos",
                "num": 1000,
                "percent": 21,
            },
            {"action": "mail_reward", "action_name": "Mail", "num": 800, "percent": 16},
        ],
    },
    "day_data": {
        "current_hcoin": 60,
        "current_rails_pass": 0,
        "last_hcoin": 120,
        "last_rails_pass": 1,
    },
    "version": "",
}

# The Inter-Knot monthly report: currencies by type and Polychrome sources by action key only.
ZZZ_DIARY = {
    "uid": 1300000003,
    "region": "prod_gf_jp",
    "current_month": "202610",
    "data_month": "202610",
    "month_data": {
        "list": [
            {"data_type": "PolychromesData", "count": 3200, "data_name": "Polychrome"},
            {"data_type": "MatserTapeData", "count": 3, "data_name": "Encrypted Master Tape"},
            {"data_type": "BooponsData", "count": 12, "data_name": "Boopon"},
        ],
        "income_components": [
            {"action": "daily_activity_rewards", "num": 1200, "percent": 38},
            {"action": "event_rewards", "num": 1000, "percent": 31},
            {"action": "shiyu_rewards", "num": 600, "percent": 19},
            {"action": "growth_rewards", "num": 400, "percent": 12},
            {"action": "hollow_rewards", "num": 0, "percent": 0},
            {"action": "mail_rewards", "num": 0, "percent": 0},
            {"action": "other_rewards", "num": 0, "percent": 0},
        ],
    },
    "optional_month": ["202608", "202609", "202610"],
    "role_info": {"nickname": NICKNAME, "avatar": AVATAR},
}


# genshin.py's validators rewrite the payload they are given, so every model gets a deep copy.
def build(model_type, payload: dict, **changes):
    return model_type(**(copy.deepcopy(payload) | changes))


def zzz_month(month: str, **changes) -> dict:
    return copy.deepcopy(ZZZ_DIARY) | {"current_month": "202610", "data_month": month} | changes


# --- values --------------------------------------------------------------------------------------


def income(earned: int) -> GameIncome:
    return GameIncome("genshin", OCTOBER, earned, (), None, None, (), None, None)


def test_the_income_values_are_frozen():
    for value in (income(1600), IncomeCategory("Events", 1600, 30), IncomeCurrency("Mora", 5)):
        with pytest.raises(FrozenInstanceError):
            setattr(value, fields(value)[0].name, None)


@pytest.mark.parametrize(("earned", "pulls"), [(0, 0), (159, 0), (160, 1), (5400, 33)])
def test_pulls_are_the_premium_currency_at_160_a_pull_rounded_down(earned, pulls):
    assert income(earned).pulls == pulls


# --- the readers ---------------------------------------------------------------------------------


def test_the_travelers_diary_becomes_the_months_primogems_with_its_sources_and_today():
    model = build(Diary, GENSHIN_DIARY)

    assert read_genshin_income(model, OCTOBER, this_month=True) == GameIncome(
        game="genshin",
        month=OCTOBER,
        earned=5400,
        others=(IncomeCurrency("Mora", 1234567),),
        last_month=9000,
        change_percent=-40,
        sources=(
            IncomeCategory("Daily Activity", 1800, 33),
            IncomeCategory("Events", 1600, 30),
            IncomeCategory("Spiral Abyss", 1200, 22),
            IncomeCategory("Mail", 800, 15),
        ),
        today=120,
        yesterday=None,
    )


def test_the_trailblaze_calendar_becomes_the_months_stellar_jade_with_passes_today_and_yesterday():
    model = build(StarRailDiary, HSR_DIARY)

    result = read_hsr_income(model, OCTOBER, this_month=True)

    assert result == GameIncome(
        game="hsr",
        month=OCTOBER,
        earned=4800,
        others=(IncomeCurrency("Star Rail Pass", 5),),
        last_month=10000,
        change_percent=-52,
        sources=(
            IncomeCategory("Daily Activity", 1800, 38),
            IncomeCategory("Events", 1200, 25),
            IncomeCategory("Memory of Chaos", 1000, 21),
            IncomeCategory("Mail", 800, 16),
        ),
        today=60,
        yesterday=120,
    )
    assert result.pulls == 30


def test_the_inter_knot_report_becomes_the_months_polychrome_with_tapes_and_named_sources():
    model = build(ZZZDiary, ZZZ_DIARY)

    assert read_zzz_income(model, OCTOBER, this_month=True) == GameIncome(
        game="zzz",
        month=OCTOBER,
        earned=3200,
        others=(IncomeCurrency("Encrypted Master Tape", 3), IncomeCurrency("Boopon", 12)),
        last_month=None,
        change_percent=None,
        sources=(
            IncomeCategory("Daily Activity", 1200, 38),
            IncomeCategory("Events", 1000, 31),
            IncomeCategory("Shiyu Defense", 600, 19),
            IncomeCategory("Growth", 400, 12),
        ),
        today=None,
        yesterday=None,
    )


def test_every_zzz_source_has_a_name_of_its_own():
    actions = [
        "daily_activity_rewards",
        "growth_rewards",
        "shiyu_rewards",
        "mail_rewards",
        "hollow_rewards",
        "event_rewards",
        "other_rewards",
    ]
    components = [
        {"action": action, "num": 7 - n, "percent": 1} for n, action in enumerate(actions)
    ]
    payload = copy.deepcopy(ZZZ_DIARY)
    payload["month_data"]["income_components"] = components

    result = read_zzz_income(build(ZZZDiary, payload), OCTOBER, this_month=True)

    assert [source.name for source in result.sources] == [
        "Daily Activity",
        "Growth",
        "Shiyu Defense",
        "Mail",
        "Hollow Zero",
        "Events",
        "Other",
    ]


@pytest.mark.parametrize(
    ("read", "model_type", "payload"),
    [
        (read_genshin_income, Diary, GENSHIN_DIARY),
        (read_hsr_income, StarRailDiary, HSR_DIARY),
        (read_zzz_income, ZZZDiary, ZZZ_DIARY),
    ],
    ids=["genshin", "hsr", "zzz"],
)
def test_an_earlier_month_has_no_today_or_yesterday(read, model_type, payload):
    # HoYoLAB's day figures are today's whatever month is asked, so they belong to this month.
    result = read(build(model_type, payload), SEPTEMBER, this_month=False)

    assert (result.month, result.today, result.yesterday) == (SEPTEMBER, None, None)


def test_the_month_is_the_one_asked_never_the_models():
    # genshin.py keeps HSR's month as 202610 and Genshin's as 10: neither is a date.
    hsr = read_hsr_income(build(StarRailDiary, HSR_DIARY), OCTOBER, this_month=True)
    genshin = read_genshin_income(build(Diary, GENSHIN_DIARY), OCTOBER, this_month=True)

    assert (hsr.month, genshin.month) == (OCTOBER, OCTOBER)


def test_a_zzz_report_without_polychrome_cannot_be_read():
    payload = copy.deepcopy(ZZZ_DIARY)
    payload["month_data"]["list"] = payload["month_data"]["list"][1:]

    with pytest.raises(ValueError, match="Polychrome"):
        read_zzz_income(build(ZZZDiary, payload), OCTOBER, this_month=True)


def test_a_zzz_action_genshin_py_does_not_know_fails_its_model():
    # A new HoYoLAB source makes the whole ZZZ read fail as "other" in HoyolabClient.
    payload = copy.deepcopy(ZZZ_DIARY)
    payload["month_data"]["income_components"].append(
        {"action": "battle_pass_rewards", "num": 10, "percent": 1}
    )

    with pytest.raises(pydantic.ValidationError):
        build(ZZZDiary, payload)


def test_no_value_holds_the_uid_nickname_avatar_or_region():
    texts = [
        repr(read_genshin_income(build(Diary, GENSHIN_DIARY), OCTOBER, this_month=True)),
        repr(read_hsr_income(build(StarRailDiary, HSR_DIARY), OCTOBER, this_month=True)),
        repr(read_zzz_income(build(ZZZDiary, ZZZ_DIARY), OCTOBER, this_month=True)),
    ]

    for secret in ("800000002", "800000001", "1300000003", NICKNAME, AVATAR, "asia", "prod_"):
        assert all(secret not in text for text in texts)


# --- the months ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("game_id", "model_type", "payload", "month", "answers"),
    [
        ("genshin", Diary, GENSHIN_DIARY, OCTOBER, True),
        ("genshin", Diary, GENSHIN_DIARY, SEPTEMBER, False),
        ("hsr", StarRailDiary, HSR_DIARY, OCTOBER, True),
        ("hsr", StarRailDiary, HSR_DIARY, date(2025, 10, 1), False),
        ("zzz", ZZZDiary, ZZZ_DIARY, OCTOBER, True),
        ("zzz", ZZZDiary, zzz_month("202609"), OCTOBER, False),
        ("zzz", ZZZDiary, zzz_month("2026-10"), OCTOBER, True),
    ],
    ids=[
        "genshin",
        "genshin another month",
        "hsr",
        "hsr a year before",
        "zzz",
        "zzz another month",
        "zzz written with a dash",
    ],
)
def test_an_answer_tells_whether_it_is_of_the_month_asked(
    game_id, model_type, payload, month, answers
):
    assert answers_month(game_id, build(model_type, payload), month) is answers


@pytest.mark.parametrize(
    ("now", "months"),
    [
        (datetime(2026, 10, 18, 19, 0, tzinfo=WIB), (OCTOBER, SEPTEMBER, date(2026, 8, 1))),
        (
            datetime(2026, 1, 5, 9, 0, tzinfo=WIB),
            (date(2026, 1, 1), date(2025, 12, 1), date(2025, 11, 1)),
        ),
        # 23:30 WIB on 31 October is 00:30 on 1 November on the Asia server.
        (
            datetime(2026, 10, 31, 23, 30, tzinfo=WIB),
            (date(2026, 11, 1), OCTOBER, SEPTEMBER),
        ),
        (
            datetime(2026, 10, 31, 22, 59, tzinfo=WIB),
            (OCTOBER, SEPTEMBER, date(2026, 8, 1)),
        ),
    ],
    ids=["mid-month", "across the new year", "past midnight server time", "before it"],
)
def test_the_months_kept_are_this_month_and_the_two_before_in_server_time(now, months):
    assert income_months(now, 8) == months


def test_a_month_is_written_and_read_as_year_dash_month():
    assert month_text(SEPTEMBER) == "2026-09"
    assert parse_month("2026-09") == SEPTEMBER


@pytest.mark.parametrize(
    "text", ["2026-9", "202609", "2026-13", "2026-00", " 2026-09", "2026-09-01", "Sept", ""]
)
def test_a_month_written_otherwise_is_refused(text):
    with pytest.raises(ValueError):
        parse_month(text)
