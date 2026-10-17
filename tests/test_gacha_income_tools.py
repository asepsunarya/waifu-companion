"""The gacha_income tool: what Master earned in a month, per configured game, from HoYoLAB.

The income source is a fake that records each call and answers fixed values; every clock is
fixed. Nothing reaches HoYoLAB.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.tools import to_anthropic, to_ollama
from waifu.gacha.hoyolab import GachaError
from waifu.gacha.income import GameIncome, IncomeCategory, IncomeCurrency
from waifu.gacha.income_tools import GACHA_INCOME, IncomeTools

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 18, 19, 0, tzinfo=WIB)  # 20:00 on the Asia server
OCTOBER = date(2026, 10, 1)
SEPTEMBER = date(2026, 9, 1)
EVERY_GAME = ["hsr", "genshin", "zzz"]
OFFLINE = GachaError("network", "HoYoLAB tidak bisa dihubungi. Cek koneksi internet.")
HIDDEN = GachaError("chronicle_private", "Data ZZZ ini tidak bisa dibaca.")


def hsr_income(month: date = OCTOBER, *, this_month: bool = True) -> GameIncome:
    return GameIncome(
        game="hsr",
        month=month,
        earned=4800,
        others=(IncomeCurrency("Star Rail Pass", 5),),
        last_month=10000,
        change_percent=-52,
        sources=(IncomeCategory("Daily Activity", 1800, 38), IncomeCategory("Events", 1200, 25)),
        today=60 if this_month else None,
        yesterday=120 if this_month else None,
    )


def genshin_income(month: date = OCTOBER, *, this_month: bool = True) -> GameIncome:
    return GameIncome(
        game="genshin",
        month=month,
        earned=5400,
        others=(IncomeCurrency("Mora", 1234567),),
        last_month=9000,
        change_percent=-40,
        sources=(IncomeCategory("Events", 1600, 30),),
        today=0 if this_month else None,
        yesterday=None,
    )


def zzz_income(month: date = OCTOBER, *, this_month: bool = True) -> GameIncome:
    return GameIncome(
        game="zzz",
        month=month,
        earned=3200,
        others=(IncomeCurrency("Encrypted Master Tape", 3), IncomeCurrency("Boopon", 12)),
        last_month=None,
        change_percent=None,
        sources=(IncomeCategory("Daily Activity", 1200, 38),),
        today=None,
        yesterday=None,
    )


READERS = {"hsr": hsr_income, "genshin": genshin_income, "zzz": zzz_income}


class FakeIncome:
    """HoyolabClient.income with fixed answers for any month: each game's value, or the
    GachaError in `failures`. Each call is recorded as (games, month, this_month)."""

    def __init__(self, *, failures=None) -> None:
        self.failures: dict[str, GachaError] = failures or {}
        self.calls: list[tuple[list[str], date, bool]] = []

    def income(self, games, month, *, this_month):
        games = list(games)
        self.calls.append((games, month, this_month))
        return {
            game: self.failures.get(game) or READERS[game](month, this_month=this_month)
            for game in games
        }


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_tools(source=None, *, games=EVERY_GAME, now=NOW) -> IncomeTools:
    clock = now if callable(now) else Clock(now)
    return IncomeTools(
        source if source is not None else FakeIncome(), games, server_utc_offset=8, now=clock
    )


def run(tools: IncomeTools, arguments) -> tuple[dict, bool]:
    result, is_error = tools.run(GACHA_INCOME, arguments)
    return json.loads(result), is_error


# --- the spec ------------------------------------------------------------------------------------


def test_one_tool_whose_arguments_may_all_be_left_out_so_it_needs_no_strict():
    [spec] = make_tools().specs()

    assert spec.name == "gacha_income"
    assert spec.input_schema == {
        "type": "object",
        "properties": {
            "game": {"type": "string", "enum": EVERY_GAME},
            "month": {"type": "string"},
        },
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "gacha_income"


def test_the_description_says_what_it_gives_and_when_to_use_it_and_never_pushes_spending():
    [spec] = make_tools().specs()

    for guidance in [
        "Honkai: Star Rail = hsr, Genshin Impact = genshin, Zenless Zone Zero = zzz",
        "YYYY-MM",
        "this month and the two before",
        "160",
        "counting only that currency",
        "`last_month`",
        "a month still running is mostly below it",
        "may lag behind the game",
        "only when Master asks",
        "never in your own greeting, the morning briefing, or anything you start yourself",
        "information for planning pulls",
        "never suggest pulling, spending money, or topping up",
        "never judge the amount",
        "`errors`",
        "Primogem",
        "Stellar Jade",
        "Polychrome",
        "Star Rail Pass",
        "Master Tape",
    ]:
        assert guidance in spec.description


def test_the_description_names_only_the_configured_games():
    [spec] = make_tools(games=["genshin"]).specs()

    assert "Genshin Impact = genshin" in spec.description
    assert "Traveler's Diary" in spec.description
    for other in ("Star Rail", "Stellar Jade", "Zenless", "Polychrome", "Master Tape"):
        assert other not in spec.description
    assert spec.input_schema["properties"]["game"]["enum"] == ["genshin"]


def test_the_spec_is_the_same_at_every_call_for_prompt_caching():
    tools = make_tools()
    before = tools.specs()

    tools.run(GACHA_INCOME, {"month": "2026-09"})

    assert tools.specs() == before


# --- the answer ----------------------------------------------------------------------------------


def test_without_arguments_every_configured_game_of_this_month_is_read_in_one_call():
    source = FakeIncome()

    data, is_error = run(make_tools(source), {})

    assert is_error is False
    assert source.calls == [(EVERY_GAME, OCTOBER, True)]
    assert (data["as_of"], data["month"], data["this_month"]) == (
        "2026-10-18 19:00",
        "2026-10",
        True,
    )
    assert [game["game"] for game in data["games"]] == EVERY_GAME
    assert "errors" not in data


def test_each_game_tells_its_currency_earned_pulls_sources_and_the_month_before():
    data, _ = run(make_tools(), {})
    hsr, genshin, zzz = data["games"]

    assert hsr == {
        "game": "hsr",
        "name": "Honkai: Star Rail",
        "currency": "Stellar Jade",
        "earned": 4800,
        "pulls": 30,
        "today": 60,
        "yesterday": 120,
        "last_month": 10000,
        "change_percent": -52,
        "sources": [
            {"name": "Daily Activity", "amount": 1800, "percent": 38},
            {"name": "Events", "amount": 1200, "percent": 25},
        ],
        "others": [{"name": "Star Rail Pass", "amount": 5}],
    }
    assert (genshin["currency"], genshin["pulls"], genshin["today"]) == ("Primogem", 33, 0)
    assert "yesterday" not in genshin
    assert zzz == {
        "game": "zzz",
        "name": "Zenless Zone Zero",
        "currency": "Polychrome",
        "earned": 3200,
        "pulls": 20,
        "sources": [{"name": "Daily Activity", "amount": 1200, "percent": 38}],
        "others": [
            {"name": "Encrypted Master Tape", "amount": 3},
            {"name": "Boopon", "amount": 12},
        ],
    }


def test_an_earlier_month_is_read_as_such_without_today():
    source = FakeIncome()

    data, is_error = run(make_tools(source), {"month": "2026-09", "game": "hsr"})

    assert is_error is False
    assert source.calls == [(["hsr"], SEPTEMBER, False)]
    assert (data["month"], data["this_month"]) == ("2026-09", False)
    [hsr] = data["games"]
    assert "today" not in hsr and "yesterday" not in hsr


def test_this_month_named_by_its_year_and_month_is_this_month():
    source = FakeIncome()

    data, _ = run(make_tools(source), {"month": "2026-10"})

    assert source.calls == [(EVERY_GAME, OCTOBER, True)]
    assert data["this_month"] is True


@pytest.mark.parametrize(
    ("arguments", "games", "month"),
    [
        ({"game": None, "month": None}, EVERY_GAME, OCTOBER),
        ({"game": "genshin", "month": None}, ["genshin"], OCTOBER),
        ({"game": None, "month": "2026-09"}, EVERY_GAME, SEPTEMBER),
    ],
    ids=["both null", "a null month", "a null game"],
)
def test_a_null_argument_counts_as_left_out_as_local_models_send_it(arguments, games, month):
    source = FakeIncome()

    data, is_error = run(make_tools(source), arguments)

    assert is_error is False
    assert source.calls == [(games, month, month == OCTOBER)]
    assert [game["game"] for game in data["games"]] == games


def test_the_month_turns_at_midnight_server_time():
    # 23:30 WIB on 31 October is already 1 November on the Asia server.
    source = FakeIncome()

    data, _ = run(make_tools(source, now=datetime(2026, 10, 31, 23, 30, tzinfo=WIB)), {})

    assert source.calls == [(EVERY_GAME, date(2026, 11, 1), True)]
    assert data["month"] == "2026-11"


def test_the_sources_are_at_most_eight():
    many = GameIncome(
        "hsr",
        OCTOBER,
        4800,
        (),
        None,
        None,
        tuple(IncomeCategory(f"Source {n}", 100 - n, 1) for n in range(12)),
        None,
        None,
    )

    class Many(FakeIncome):
        def income(self, games, month, *, this_month):
            return {"hsr": many}

    data, _ = run(make_tools(Many(), games=["hsr"]), {})

    assert [source["name"] for source in data["games"][0]["sources"]] == [
        f"Source {n}" for n in range(8)
    ]


def test_a_game_that_could_not_be_read_is_under_errors_and_the_others_still_come():
    source = FakeIncome(failures={"zzz": HIDDEN})

    data, is_error = run(make_tools(source), {})

    assert is_error is False
    assert [game["game"] for game in data["games"]] == ["hsr", "genshin"]
    assert data["errors"] == [{"game": "zzz", "message": "Data ZZZ ini tidak bisa dibaca."}]


def test_every_game_failing_is_an_error_result_with_each_message_once():
    source = FakeIncome(failures=dict.fromkeys(EVERY_GAME, OFFLINE))

    data, is_error = run(make_tools(source), {})

    assert is_error is True
    assert data == {"ok": False, "message": "HoYoLAB tidak bisa dihubungi. Cek koneksi internet."}


@pytest.mark.parametrize(
    ("arguments", "why"),
    [
        (
            {"month": "2026-07"},
            "'month' harus YYYY-MM, salah satu dari: 2026-10, 2026-09, 2026-08.",
        ),
        (
            {"month": "2026-11"},
            "'month' harus YYYY-MM, salah satu dari: 2026-10, 2026-09, 2026-08.",
        ),
        (
            {"month": "Oktober"},
            "'month' harus YYYY-MM, salah satu dari: 2026-10, 2026-09, 2026-08.",
        ),
        ({"month": 202610}, "'month' harus YYYY-MM, salah satu dari: 2026-10, 2026-09, 2026-08."),
        ({"game": "honkai"}, "'game' harus salah satu dari: hsr, genshin, zzz."),
        ({"currency": "Primogem"}, "argumen tidak dikenal: currency."),
        (["genshin"], "argumen harus berupa objek JSON."),
    ],
    ids=[
        "a month HoYoLAB no longer keeps",
        "a month to come",
        "a month by its name",
        "a month as a number",
        "a game not configured",
        "an unknown key",
        "not an object",
    ],
)
def test_invalid_arguments_are_an_error_result_and_hoyolab_is_never_asked(arguments, why):
    source = FakeIncome()

    data, is_error = run(make_tools(source), arguments)

    assert is_error is True
    assert data == {"ok": False, "message": f"Argumen gacha_income tidak valid: {why}"}
    assert source.calls == []


def test_a_game_that_is_not_configured_is_invalid():
    source = FakeIncome()

    data, is_error = run(make_tools(source, games=["hsr", "genshin"]), {"game": "zzz"})

    assert is_error is True
    assert "'game' harus salah satu dari: hsr, genshin." in data["message"]


def test_another_tools_name_is_an_error_result():
    result, is_error = make_tools().run("game_events", {})

    assert is_error is True
    assert json.loads(result)["message"] == "Tool 'game_events' bukan tool pendapatan gacha."


def test_a_failing_clock_is_an_error_result_never_an_exception(caplog):
    def broken() -> datetime:
        raise RuntimeError("the clock is gone")

    with caplog.at_level(logging.ERROR, logger="waifu.gacha.income_tools"):
        data, is_error = run(make_tools(now=broken), {})

    assert is_error is True
    assert data["message"] == "Pendapatan gacha tidak bisa dibaca saat ini."
    assert "the clock is gone" in caplog.text


# --- what is kept --------------------------------------------------------------------------------


def test_a_read_is_kept_for_15_minutes_per_game_and_month():
    source = FakeIncome()
    clock = Clock()
    tools = make_tools(source, now=clock)

    run(tools, {"game": "hsr"})
    clock.now = NOW + timedelta(minutes=14)
    data, _ = run(tools, {"game": "hsr"})
    assert data["as_of"] == "2026-10-18 19:00"  # when HoYoLAB was read
    run(tools, {"game": "hsr", "month": "2026-09"})
    clock.now = NOW + timedelta(minutes=15)
    run(tools, {"game": "hsr"})

    assert source.calls == [
        (["hsr"], OCTOBER, True),
        (["hsr"], SEPTEMBER, False),
        (["hsr"], OCTOBER, True),
    ]


def test_only_the_games_not_kept_are_asked_and_as_of_is_the_oldest_read():
    source = FakeIncome()
    clock = Clock()
    tools = make_tools(source, now=clock)

    run(tools, {"game": "genshin"})
    clock.now = NOW + timedelta(minutes=5)
    data, _ = run(tools, {})

    assert source.calls == [(["genshin"], OCTOBER, True), (["hsr", "zzz"], OCTOBER, True)]
    assert [game["game"] for game in data["games"]] == EVERY_GAME
    assert data["as_of"] == "2026-10-18 19:00"


def test_a_game_that_failed_is_asked_again_next_time():
    source = FakeIncome(failures={"zzz": OFFLINE})
    tools = make_tools(source)

    run(tools, {})
    run(tools, {})

    assert source.calls == [(EVERY_GAME, OCTOBER, True), (["zzz"], OCTOBER, True)]


def test_this_months_kept_read_never_outlives_midnight_server_time():
    # 22:50 WIB is 23:50 on the Asia server; 23:05 WIB is already the next server day, so
    # yesterday's `today` must not be told as today's.
    source = FakeIncome()
    clock = Clock(datetime(2026, 10, 18, 22, 50, tzinfo=WIB))
    tools = make_tools(source, now=clock)

    run(tools, {"game": "hsr"})
    clock.now = datetime(2026, 10, 18, 23, 5, tzinfo=WIB)
    run(tools, {"game": "hsr"})

    assert len(source.calls) == 2


def test_a_clock_set_back_reads_again():
    source = FakeIncome()
    clock = Clock()
    tools = make_tools(source, now=clock)

    run(tools, {"game": "hsr"})
    clock.now = NOW - timedelta(minutes=1)
    run(tools, {"game": "hsr"})

    assert len(source.calls) == 2
