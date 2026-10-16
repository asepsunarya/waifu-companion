import inspect
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta

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


# ---- the source -----------------------------------------------------------------------------


def test_the_source_names_every_method_the_game_tools_call():
    methods = {name for name in vars(GameDataSource) if not name.startswith("_")}

    assert methods == {
        f"{game}_{method}" for game in GAMES for method in ("characters", "endgame", "calendar")
    } | {"genshin_character_details", "zzz_character_details"}


def test_the_source_takes_the_hsr_methods_of_hoyolab_client_as_they_are():
    for name in ("hsr_characters", "hsr_endgame", "hsr_calendar"):
        expected = inspect.signature(getattr(HoyolabClient, name))
        assert inspect.signature(getattr(GameDataSource, name)) == expected
