import inspect
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone

import pytest

from waifu.gacha.game_data import (
    GAME_MODES,
    MODE_GAMES,
    Banner,
    ChallengeWindow,
    Endgame,
    EndgameStage,
    GameCalendar,
    GameDataSource,
    GameEvent,
    is_over,
    is_running,
    on_the_minute,
    worn_sets,
)
from waifu.gacha.games import GAMES
from waifu.gacha.hoyolab import HoyolabClient
from waifu.gacha.starrail import ENDGAME_MODES

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


# ---- modes ----------------------------------------------------------------------------------


def test_each_game_lists_its_endgame_modes_in_display_order_with_their_official_names():
    assert list(GAME_MODES) == list(GAMES)
    assert GAME_MODES["genshin"] == {
        "spiral_abyss": "Spiral Abyss",
        "imaginarium_theater": "Imaginarium Theater",
        "stygian_onslaught": "Stygian Onslaught",
    }
    assert list(GAME_MODES["zzz"].items()) == [
        ("shiyu_defense", "Shiyu Defense"),
        ("deadly_assault", "Deadly Assault"),
    ]


def test_the_hsr_modes_are_the_hsr_endgame_modes_in_their_order():
    assert list(GAME_MODES["hsr"].items()) == list(ENDGAME_MODES.items())
    assert list(ENDGAME_MODES) == [
        "memory_of_chaos",
        "pure_fiction",
        "apocalyptic_shadow",
        "anomaly_arbitration",
    ]


def test_each_mode_belongs_to_exactly_one_game():
    assert MODE_GAMES == {
        "memory_of_chaos": "hsr",
        "pure_fiction": "hsr",
        "apocalyptic_shadow": "hsr",
        "anomaly_arbitration": "hsr",
        "spiral_abyss": "genshin",
        "imaginarium_theater": "genshin",
        "stygian_onslaught": "genshin",
        "shiyu_defense": "zzz",
        "deadly_assault": "zzz",
    }
    assert sum(len(modes) for modes in GAME_MODES.values()) == len(MODE_GAMES)


# ---- values ---------------------------------------------------------------------------------


def test_the_shared_values_are_frozen():
    stage = EndgameStage("Floor 12", stars=9, max_stars=9, rating=None, score=None, seconds=None)
    endgame = Endgame(
        mode="spiral_abyss",
        season=None,
        ends_at=NOW,
        has_data=True,
        stars=36,
        deepest="12-3",
        difficulty=None,
        rating=None,
        score=None,
        max_score=None,
        seconds=None,
        battles=12,
        stages=(stage,),
    )
    banner = Banner("character", None, ("Furina",), ("Xingqiu",), starts_at=None, ends_at=NOW)
    event = GameEvent("Event", starts_at=None, ends_at=NOW, current=None, total=None, done=False)
    window = ChallengeWindow("spiral_abyss", "Spiral Abyss", None, NOW, current=30, total=36)
    calendar = GameCalendar("5.2", (banner,), (event,), (window,))

    for value in (stage, endgame, banner, event, window, calendar):
        with pytest.raises(FrozenInstanceError):
            setattr(value, fields(value)[0].name, None)


# ---- worn_sets ------------------------------------------------------------------------------


def test_worn_sets_counts_the_pieces_of_each_set_worn_at_least_twice():
    names = ["Golden Troupe"] * 4 + ["Gladiator's Finale"]

    assert worn_sets(names) == (("Golden Troupe", 4),)


def test_worn_sets_lists_the_most_pieces_first_then_by_name():
    names = ["Woodpecker Electro"] * 3 + ["Swing Jazz"] * 2 + ["Hormone Punk"] * 2
    names.append("Freedom Blues")

    assert worn_sets(names) == (
        ("Woodpecker Electro", 3),
        ("Hormone Punk", 2),
        ("Swing Jazz", 2),
    )


def test_worn_sets_without_a_pair_is_empty():
    assert worn_sets(["Golden Troupe", "Gladiator's Finale", "Noblesse Oblige"]) == ()
    assert worn_sets([]) == ()


# ---- times ----------------------------------------------------------------------------------


def test_something_without_an_end_is_never_over_and_is_over_from_its_end_on():
    assert not is_over(None, NOW)
    assert not is_over(NOW + timedelta(seconds=1), NOW)
    assert is_over(NOW, NOW)


def test_something_runs_from_its_start_until_its_end():
    assert is_running(NOW - HOUR, NOW + HOUR, NOW)
    assert is_running(None, None, NOW)
    assert not is_running(NOW + HOUR, None, NOW)
    assert not is_running(None, NOW, NOW)


def test_a_time_on_the_last_second_of_a_minute_is_the_next_whole_minute():
    """HoYoLAB ends a season at 03:59:59 for the game's 04:00 reset."""
    last_second = datetime(2026, 11, 16, 3, 59, 59, 500, tzinfo=timezone(timedelta(hours=8)))

    assert on_the_minute(last_second) == datetime(2026, 11, 16, 4, 0, tzinfo=last_second.tzinfo)
    assert on_the_minute(last_second).utcoffset() == timedelta(hours=8)


def test_any_other_time_and_none_stay_as_they_are():
    for moment in (NOW, NOW + timedelta(minutes=59, seconds=58), NOW - timedelta(seconds=59)):
        assert on_the_minute(moment) == moment
    assert on_the_minute(None) is None


# ---- the source -----------------------------------------------------------------------------


def test_the_source_names_every_method_the_game_tools_call():
    methods = {name for name in vars(GameDataSource) if not name.startswith("_")}

    assert methods == {
        f"{game}_{method}" for game in GAMES for method in ("characters", "endgame", "calendar")
    } | {"genshin_character_details", "zzz_character_details"}


def test_hoyolab_client_offers_every_method_of_the_source_as_it_is():
    """The game data tools and the `waifu gacha <game>` views read HoyolabClient through
    GameDataSource, so each method takes the same parameters and names the same types."""
    for name in (name for name in vars(GameDataSource) if not name.startswith("_")):
        expected = inspect.signature(getattr(GameDataSource, name))
        assert inspect.signature(getattr(HoyolabClient, name)) == expected, name
