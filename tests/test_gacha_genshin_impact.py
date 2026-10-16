import copy
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone

import pytest
from genshin.models import (
    Character,
    GenshinDetailCharacters,
    GenshinEventCalendar,
    GenshinWeaponType,
    HardChallenge,
    ImgTheater,
    SpiralAbyss,
)
from genshin.models.genshin import constants

from waifu.gacha.game_data import Banner, ChallengeWindow, Endgame, EndgameStage, GameEvent
from waifu.gacha.genshin_impact import (
    GenshinArtifact,
    GenshinCharacter,
    GenshinCharacterDetail,
    GenshinWeapon,
    read_calendar,
    read_character,
    read_detail,
    read_endgame,
)

SERVER_TZ = timezone(timedelta(hours=8))
FETCHED_AT = datetime(2026, 10, 16, 5, 0, tzinfo=UTC)
FURINA_ID = 10000089
BENNETT_ID = 10000032
ICON = "https://example.invalid/UI_AvatarIcon_Icon.png"
WIKI = "https://example.invalid/wiki"
DESCRIPTION = "A long description"
CLOCK_FIELDS = ("year", "month", "day", "hour", "minute", "second")


@pytest.fixture(autouse=True)
def character_names(monkeypatch):
    """genshin.py's character-name list, which it downloads on the first Battle Chronicle
    read: Spiral Abyss avatars carry only an id, so without the list they do not parse."""
    names = {
        FURINA_ID: constants.DBChar(FURINA_ID, "Furina", "Furina", "Hydro", 5),
        BENNETT_ID: constants.DBChar(BENNETT_ID, "Bennett", "Bennett", "Pyro", 4),
    }
    monkeypatch.setattr(constants, "CHARACTER_NAMES", {"en-us": names})


# genshin.py's validators rewrite the payload they are given, so every model gets a deep copy.
def build(model_type, payload: dict):
    return model_type(**copy.deepcopy(payload))


def unix(moment: datetime) -> int:
    return int(moment.timestamp())


def days(count: float) -> datetime:
    return FETCHED_AT + timedelta(days=count)


def server_unix(moment: datetime) -> dict:
    # genshin.py wraps a unix time this way when it knows the uid's server offset.
    return {"timestamp": str(unix(moment)), "tzinfo": 8}


def wall_clock(moment: datetime) -> dict:
    """The naive wall clock HoYoLAB sends beside some unix times (server time here)."""
    local = moment.astimezone(SERVER_TZ)
    return {name: getattr(local, name) for name in CLOCK_FIELDS}


def server_clock(moment: datetime) -> dict:
    # genshin.py adds the uid's server offset ("tzinfo") to a wall clock when it knows it.
    return {**wall_clock(moment), "tzinfo": 8}


# ---- values -----------------------------------------------------------------------------------


def test_the_genshin_values_are_frozen():
    weapon = GenshinWeapon("Splendor of Tranquil Waters", level=90, refinement=1, rarity=5)
    character = GenshinCharacter(
        ref=10000089,
        name="Furina",
        rarity=5,
        level=90,
        constellation=2,
        element="Hydro",
        weapon_type="Sword",
        weapon=weapon,
    )
    artifact = GenshinArtifact("Plume of Death", "Golden Troupe", "ATK", "311", level=20)
    detail = GenshinCharacterDetail(
        character,
        weapon_stats=(("Base ATK", "542"), ("CRIT DMG", "88.2%")),
        stats=(("Max HP", "35307"),),
        artifacts=(artifact,),
        sets=(),
    )

    for value in (weapon, character, artifact, detail):
        with pytest.raises(FrozenInstanceError):
            setattr(value, fields(value)[0].name, None)


# ---- characters -------------------------------------------------------------------------------


def roster_weapon(name: str, *, level=90, refinement=1, rarity=5) -> dict:
    return {
        "id": 11513,
        "icon": ICON,
        "type": 1,
        "rarity": rarity,
        "level": level,
        "affix_level": refinement,
        "name": name,
    }


def character(
    char_id: int,
    name: str,
    *,
    element="Hydro",
    rarity=5,
    level=90,
    constellation=0,
    weapon_type=1,
    weapon: dict | None = None,
) -> dict:
    """One entry of HoYoLAB's character/list."""
    return {
        "id": char_id,
        "icon": ICON,
        "name": name,
        "element": element,
        "fetter": 10,
        "level": level,
        "rarity": rarity,
        "actived_constellation_num": constellation,
        "image": ICON,
        "is_chosen": False,
        "side_icon": ICON,
        "weapon_type": weapon_type,
        "weapon": weapon or roster_weapon("Dull Blade", level=1, rarity=1),
    }


FURINA = character(
    FURINA_ID,
    "Furina",
    constellation=2,
    weapon=roster_weapon("Splendor of Tranquil Waters", refinement=1),
)


def test_a_character_is_read_with_constellation_element_weapon_type_and_weapon():
    assert read_character(build(Character, FURINA)) == GenshinCharacter(
        ref=FURINA_ID,
        name="Furina",
        rarity=5,
        level=90,
        constellation=2,
        element="Hydro",
        weapon_type="Sword",
        weapon=GenshinWeapon("Splendor of Tranquil Waters", level=90, refinement=1, rarity=5),
    )


def test_a_weapons_refinement_is_read():
    payload = character(BENNETT_ID, "Bennett", element="Pyro", rarity=4, weapon_type=1)
    payload["weapon"] = roster_weapon("Mistsplitter Reforged", level=80, refinement=3)

    weapon = read_character(build(Character, payload)).weapon

    assert weapon == GenshinWeapon("Mistsplitter Reforged", level=80, refinement=3, rarity=5)


def test_a_collab_character_has_the_rarity_genshin_py_gives():
    # HoYoLAB sends 105 for a collab character to show a red background; genshin.py takes 100 off.
    payload = character(10000062, "Aloy", element="Cryo", rarity=105, weapon_type=12)

    assert read_character(build(Character, payload)).rarity == 5


@pytest.mark.parametrize("weapon_type", list(GenshinWeaponType), ids=lambda kind: kind.name)
def test_each_weapon_type_reads_as_its_name(weapon_type):
    payload = character(FURINA_ID, "Furina", weapon_type=int(weapon_type))

    assert read_character(build(Character, payload)).weapon_type == weapon_type.name.title()


def test_a_weapon_type_genshin_py_does_not_know_reads_as_its_number():
    payload = character(FURINA_ID, "Furina", weapon_type=14)

    assert read_character(build(Character, payload)).weapon_type == "14"


def test_a_character_keeps_no_icon():
    assert "https://" not in repr(read_character(build(Character, FURINA)))


# ---- detail -----------------------------------------------------------------------------------

PROPERTY_NAMES = {
    2: "HP",
    3: "HP%",
    5: "ATK",
    4: "Base ATK",
    # HoYoLAB writes a no-break space in these two; genshin.py turns it into a space.
    20: "CRIT\xa0Rate",
    22: "CRIT\xa0DMG",
    23: "Energy Recharge",
    42: "Hydro DMG Bonus",
    2000: "Max HP",
    2001: "ATK",
}
SUBSTAT = "13.37%"


def value(kind: int, final: str) -> dict:
    return {"property_type": kind, "base": "1000", "add": "500", "final": final}


def detail_weapon(name: str, *, refinement=1, main=(4, "542"), sub: tuple | None = (22, "88.2%")):
    return {
        **roster_weapon(name, refinement=refinement),
        "desc": DESCRIPTION,
        "main_property": value(*main),
        "sub_property": None if sub is None else value(*sub),
        "promote_level": 6,
    }


def artifact(pos: int, piece: str, set_name: str, kind: int, main: str, level=20) -> dict:
    return {
        "id": 90000 + pos,
        "icon": ICON,
        "name": f"Artifact piece {pos}",
        "pos": pos,
        "pos_name": piece,
        "rarity": 5,
        "level": level,
        # HoYoLAB leaves out `enabled`, so genshin.py calls every set bonus inactive.
        "set": {
            "id": sum(map(ord, set_name)),
            "name": set_name,
            "affixes": [
                {"activation_number": 2, "effect": DESCRIPTION},
                {"activation_number": 4, "effect": DESCRIPTION},
            ],
        },
        "main_property": {"property_type": kind, "value": main, "times": 0},
        "sub_property_list": [{"property_type": 20, "value": SUBSTAT, "times": 3}],
    }


def detail_character(base: dict, weapon: dict, *, relics=(), properties=()) -> dict:
    return {
        "base": base,
        "weapon": weapon,
        "relics": list(relics),
        "constellations": [
            {
                "id": 1,
                "icon": ICON,
                "pos": 1,
                "name": "First constellation",
                "effect": DESCRIPTION,
                "is_actived": True,
            }
        ],
        "costumes": [],
        "selected_properties": list(properties),
        "base_properties": [value(2000, "15307")],
        "extra_properties": [],
        "element_properties": [],
        "skills": [
            {
                "skill_id": 1,
                "skill_type": 1,
                "name": "Normal Attack",
                "level": 10,
                "desc": DESCRIPTION,
                "skill_affix_list": [],
                "icon": ICON,
                "is_unlock": True,
            }
        ],
    }


def details(*characters: dict):
    payload = {
        "list": list(characters),
        "property_map": {
            str(kind): {"property_type": kind, "name": name, "icon": "", "filter_name": name}
            for kind, name in PROPERTY_NAMES.items()
        },
        "relic_property_options": {},
        "relic_wiki": {},
        "weapon_wiki": {},
        "avatar_wiki": {},
    }
    return build(GenshinDetailCharacters, payload).characters


FURINA_DETAIL = detail_character(
    FURINA,
    detail_weapon("Splendor of Tranquil Waters"),
    relics=(
        artifact(1, "Flower of Life", "Golden Troupe", 2, "4780"),
        artifact(2, "Plume of Death", "Golden Troupe", 5, "311"),
        artifact(3, "Sands of Eon", "Golden Troupe", 3, "46.6%"),
        artifact(4, "Goblet of Eonothem", "Tenacity of the Millelith", 3, "46.6%", level=16),
        artifact(5, "Circlet of Logos", "Golden Troupe", 22, "62.2%"),
    ),
    properties=(
        value(2000, "35307"),
        value(2001, "1100"),
        value(20, "68.4%"),
        value(22, "190.2%"),
        value(23, "160.3%"),
    ),
)


def test_a_detail_reads_the_weapon_stats_final_stats_artifacts_and_sets_worn():
    (furina,) = details(FURINA_DETAIL)

    assert read_detail(furina) == GenshinCharacterDetail(
        character=GenshinCharacter(
            ref=FURINA_ID,
            name="Furina",
            rarity=5,
            level=90,
            constellation=2,
            element="Hydro",
            weapon_type="Sword",
            weapon=GenshinWeapon("Splendor of Tranquil Waters", level=90, refinement=1, rarity=5),
        ),
        weapon_stats=(("Base ATK", "542"), ("CRIT DMG", "88.2%")),
        stats=(
            ("Max HP", "35307"),
            ("ATK", "1100"),
            ("CRIT Rate", "68.4%"),
            ("CRIT DMG", "190.2%"),
            ("Energy Recharge", "160.3%"),
        ),
        artifacts=(
            GenshinArtifact("Flower of Life", "Golden Troupe", "HP", "4780", 20),
            GenshinArtifact("Plume of Death", "Golden Troupe", "ATK", "311", 20),
            GenshinArtifact("Sands of Eon", "Golden Troupe", "HP%", "46.6%", 20),
            GenshinArtifact("Goblet of Eonothem", "Tenacity of the Millelith", "HP%", "46.6%", 16),
            GenshinArtifact("Circlet of Logos", "Golden Troupe", "CRIT DMG", "62.2%", 20),
        ),
        sets=(("Golden Troupe", 4),),
    )


def test_a_weapon_without_a_sub_stat_has_only_its_main_stat():
    weapon = detail_weapon("Dull Blade", main=(4, "185"), sub=None)
    (furina,) = details(detail_character(FURINA, weapon))

    assert read_detail(furina).weapon_stats == (("Base ATK", "185"),)


def test_the_sets_worn_are_counted_from_the_pieces_since_genshin_py_says_no_bonus_is_on():
    relics = (
        artifact(1, "Flower of Life", "Shimenawa's Reminiscence", 2, "4780"),
        artifact(2, "Plume of Death", "Gladiator's Finale", 5, "311"),
        artifact(3, "Sands of Eon", "Shimenawa's Reminiscence", 3, "46.6%"),
        artifact(4, "Goblet of Eonothem", "Gladiator's Finale", 42, "46.6%"),
        artifact(5, "Circlet of Logos", "Noblesse Oblige", 22, "62.2%"),
    )
    (furina,) = details(detail_character(FURINA, detail_weapon("Dull Blade"), relics=relics))

    assert not any(effect.active for piece in furina.artifacts for effect in piece.set.effects)
    assert read_detail(furina).sets == (
        ("Gladiator's Finale", 2),
        ("Shimenawa's Reminiscence", 2),
    )


def test_a_character_without_artifacts_wears_no_set():
    (furina,) = details(detail_character(FURINA, detail_weapon("Dull Blade")))

    detail = read_detail(furina)

    assert (detail.artifacts, detail.sets) == ((), ())


def test_a_detail_keeps_no_icon_description_constellation_skill_or_substat():
    (furina,) = details(FURINA_DETAIL)

    text = repr(read_detail(furina))

    for left_out in ("https://", DESCRIPTION, "First constellation", "Normal Attack", SUBSTAT):
        assert left_out not in text
    assert "Artifact piece" not in text


# ---- Spiral Abyss -----------------------------------------------------------------------------

ABYSS_START = datetime(2026, 10, 16, 4, 0, tzinfo=SERVER_TZ)
ABYSS_END = datetime(2026, 11, 16, 3, 59, 59, tzinfo=SERVER_TZ)


def abyss_battle(half: int) -> dict:
    # Abyss avatars carry no name: genshin.py takes it from its character-name list.
    avatars = [
        {"id": FURINA_ID, "icon": ICON, "level": 90, "rarity": 5},
        {"id": BENNETT_ID, "icon": ICON, "level": 80, "rarity": 4},
    ]
    return {"index": half, "timestamp": server_unix(days(-0.2)), "avatars": avatars}


def abyss_floor(index: int, stars: int) -> dict:
    return {
        "index": index,
        "icon": ICON,
        "is_unlock": True,
        "settle_time": "0",
        "star": stars,
        "max_star": 9,
        "levels": [
            {
                "index": 1,
                "star": min(stars, 3),
                "max_star": 3,
                "battles": [abyss_battle(1), abyss_battle(2)],
            }
        ],
    }


def abyss_rank(value_: int) -> list[dict]:
    return [{"avatar_id": FURINA_ID, "avatar_icon": ICON, "value": value_, "rarity": 5}]


SPIRAL_ABYSS = {
    "schedule_id": 93,
    "start_time": server_unix(ABYSS_START),
    "end_time": server_unix(ABYSS_END),
    "total_battle_times": 14,
    "total_win_times": 12,
    "max_floor": "12-3",
    "total_star": 33,
    "is_unlock": True,
    "reveal_rank": abyss_rank(14),
    "defeat_rank": abyss_rank(120),
    "damage_rank": abyss_rank(250000),
    "take_damage_rank": abyss_rank(90000),
    "normal_skill_rank": abyss_rank(40),
    "energy_skill_rank": abyss_rank(20),
    "floors": [
        abyss_floor(8, 9),
        abyss_floor(9, 9),
        abyss_floor(10, 9),
        abyss_floor(11, 8),
        abyss_floor(12, 7),
    ],
}


def test_spiral_abyss_reads_this_season_with_its_four_highest_floors_highest_first():
    endgame = read_endgame("spiral_abyss", build(SpiralAbyss, SPIRAL_ABYSS), FETCHED_AT)

    assert endgame == Endgame(
        mode="spiral_abyss",
        season=None,
        ends_at=ABYSS_END,
        has_data=True,
        stars=33,
        deepest="12-3",
        difficulty=None,
        rating=None,
        score=None,
        max_score=None,
        seconds=None,
        battles=14,
        stages=(
            EndgameStage("Floor 12", 7, 9, None, None, None),
            EndgameStage("Floor 11", 8, 9, None, None, None),
            EndgameStage("Floor 10", 9, 9, None, None, None),
            EndgameStage("Floor 9", 9, 9, None, None, None),
        ),
    )
    assert endgame.ends_at.utcoffset() == timedelta(hours=8)


def test_spiral_abyss_not_played_this_season_has_no_data_deepest_floor_or_stages():
    payload = {
        **SPIRAL_ABYSS,
        "total_battle_times": 0,
        "total_win_times": 0,
        "max_floor": "0-0",
        "total_star": 0,
        "floors": [],
        **{key: [] for key in SPIRAL_ABYSS if key.endswith("_rank")},
    }

    endgame = read_endgame("spiral_abyss", build(SpiralAbyss, payload), FETCHED_AT)

    assert (endgame.has_data, endgame.stars, endgame.deepest, endgame.battles) == (
        False,
        0,
        None,
        0,
    )
    assert (endgame.stages, endgame.ends_at) == ((), ABYSS_END)


def test_spiral_abyss_keeps_no_character_name():
    text = repr(read_endgame("spiral_abyss", build(SpiralAbyss, SPIRAL_ABYSS), FETCHED_AT))

    assert "Furina" not in text
    assert "Bennett" not in text


# ---- Imaginarium Theater ----------------------------------------------------------------------

SEPTEMBER = (
    datetime(2026, 9, 1, 4, 0, tzinfo=SERVER_TZ),
    datetime(2026, 10, 1, 3, 59, 59, tzinfo=SERVER_TZ),
)
OCTOBER = (
    datetime(2026, 10, 1, 4, 0, tzinfo=SERVER_TZ),
    datetime(2026, 11, 1, 3, 59, 59, tzinfo=SERVER_TZ),
)


def theater_season(
    schedule_id: int,
    window: tuple[datetime, datetime],
    *,
    difficulty=4,
    best_act=10,
    medals=9,
    has_data=True,
) -> dict:
    start, end = window
    return {
        "detail": None,  # asked for with need_detail=False
        "stat": {
            "difficulty_id": difficulty,
            "max_round_id": best_act,
            "heraldry": 0,
            "get_medal_round_list": [True] * medals,
            "medal_num": medals,
            "coin_num": 20,
            "avatar_bonus_num": 3,
            "rent_cnt": 1,
        },
        "schedule": {
            "start_time": unix(start),
            "end_time": unix(end),
            "schedule_type": 1,
            "schedule_id": schedule_id,
            "start_date_time": server_clock(start),
            "end_date_time": server_clock(end),
        },
        "has_data": has_data,
        "has_detail_data": False,
    }


def theater(*seasons: dict):
    return build(ImgTheater, {"data": list(seasons), "is_unlock": True})


# The API's order is not stated, so the older season comes first here.
TWO_SEASONS = (
    theater_season(16, SEPTEMBER, difficulty=3, best_act=8, medals=6),
    theater_season(17, OCTOBER),
)


def test_imaginarium_theater_reads_the_season_running_at_the_fetch_time():
    endgame = read_endgame("imaginarium_theater", theater(*TWO_SEASONS), FETCHED_AT)

    assert endgame == Endgame(
        mode="imaginarium_theater",
        season=None,
        ends_at=OCTOBER[1],
        has_data=True,
        stars=9,
        deepest="Act 10",
        difficulty="Visionary",
        rating=None,
        score=None,
        max_score=None,
        seconds=None,
        battles=None,
        stages=(),
    )
    assert endgame.ends_at.utcoffset() == timedelta(0)


def test_the_running_theater_season_wins_over_a_newer_one():
    in_september = datetime(2026, 9, 20, tzinfo=UTC)

    endgame = read_endgame("imaginarium_theater", theater(*TWO_SEASONS), in_september)

    assert (endgame.ends_at, endgame.difficulty, endgame.deepest) == (SEPTEMBER[1], "Hard", "Act 8")


def test_without_a_running_theater_season_the_newest_one_is_read():
    after_both = datetime(2026, 11, 5, tzinfo=UTC)

    endgame = read_endgame("imaginarium_theater", theater(*TWO_SEASONS), after_both)

    assert endgame.ends_at == OCTOBER[1]


@pytest.mark.parametrize(
    ("difficulty", "name"),
    [(1, "Easy"), (2, "Normal"), (3, "Hard"), (4, "Visionary"), (5, "Arcana Challenge"), (0, None)],
)
def test_theater_difficulties_read_as_their_names(difficulty, name):
    model = theater(theater_season(17, OCTOBER, difficulty=difficulty))

    assert read_endgame("imaginarium_theater", model, FETCHED_AT).difficulty == name


def test_a_theater_season_not_played_has_no_data_or_deepest_act():
    model = theater(theater_season(17, OCTOBER, best_act=0, medals=0, has_data=False))

    endgame = read_endgame("imaginarium_theater", model, FETCHED_AT)

    assert (endgame.has_data, endgame.deepest, endgame.stars) == (False, None, 0)
    assert endgame.ends_at == OCTOBER[1]


def test_a_theater_without_seasons_has_no_data():
    assert read_endgame("imaginarium_theater", theater(), FETCHED_AT) == Endgame(
        mode="imaginarium_theater",
        season=None,
        ends_at=None,
        has_data=False,
        stars=None,
        deepest=None,
        difficulty=None,
        rating=None,
        score=None,
        max_score=None,
        seconds=None,
        battles=None,
        stages=(),
    )


# ---- Stygian Onslaught ------------------------------------------------------------------------

SEASON_7 = (
    datetime(2026, 10, 6, 12, 0, tzinfo=SERVER_TZ),
    datetime(2026, 11, 3, 3, 59, 59, tzinfo=SERVER_TZ),
)
SEASON_6 = (
    datetime(2026, 9, 1, 12, 0, tzinfo=SERVER_TZ),
    datetime(2026, 9, 29, 3, 59, 59, tzinfo=SERVER_TZ),
)


def stygian_challenge(name: str, seconds: int) -> dict:
    return {
        "name": name,
        "second": seconds,
        "teams": [
            {
                "avatar_id": FURINA_ID,
                "name": "Furina",
                "element": "Hydro",
                "image": ICON,
                "level": 90,
                "rarity": 5,
                "rank": 2,
            }
        ],
        "best_avatar": [{"avatar_id": FURINA_ID, "side_icon": ICON, "dps": "1234567", "type": 1}],
        "monster": {
            "monster_id": 1,
            "name": f"{name} enemy",
            "level": 95,
            "icon": ICON,
            "desc": [DESCRIPTION],
            "tags": [{"type": 1, "desc": "{SPRITE_PRESET#11003}"}],
        },
    }


STYGIAN_CHALLENGES = (
    ("Knuckle Duckle", 40),
    ("Gluttonous Yumkasaur Mountain King", 55),
    ("Tenebrous Papilla", 50),
)


def stygian_season(
    schedule_id: int,
    name: str,
    window: tuple[datetime, datetime],
    *,
    best: tuple[int, int] | None = (5, 145),
    challenges=STYGIAN_CHALLENGES,
) -> dict:
    start, end = window
    return {
        "schedule": {
            "schedule_id": schedule_id,
            "name": name,
            "start_time": server_unix(start),
            "end_time": server_unix(end),
            "is_valid": True,
        },
        "single": {
            "best": None
            if best is None
            else {"difficulty": best[0], "second": best[1], "icon": "UI_Badge,UI_Badge_5"},
            "challenge": [stygian_challenge(*item) for item in challenges],
            "has_data": best is not None,
        },
        "mp": {"best": None, "challenge": [], "has_data": False},
    }


def stygian(*seasons: dict) -> list:
    return [build(HardChallenge, season) for season in seasons]


TWO_STYGIAN_SEASONS = (
    stygian_season(6, "Season of Embers", SEASON_6, best=(4, 300)),
    stygian_season(7, "Season of Tides", SEASON_7),
)


def test_stygian_onslaught_reads_the_season_running_at_the_fetch_time():
    endgame = read_endgame("stygian_onslaught", stygian(*TWO_STYGIAN_SEASONS), FETCHED_AT)

    assert endgame == Endgame(
        mode="stygian_onslaught",
        season="Season of Tides",
        ends_at=SEASON_7[1],
        has_data=True,
        stars=None,
        deepest=None,
        difficulty="5",
        rating=None,
        score=None,
        max_score=None,
        seconds=145,
        battles=None,
        stages=(
            EndgameStage("Knuckle Duckle", None, None, None, None, 40),
            EndgameStage("Gluttonous Yumkasaur Mountain King", None, None, None, None, 55),
            EndgameStage("Tenebrous Papilla", None, None, None, None, 50),
        ),
    )


def test_the_running_stygian_season_wins_over_a_newer_one():
    in_september = datetime(2026, 9, 10, tzinfo=UTC)

    endgame = read_endgame("stygian_onslaught", stygian(*TWO_STYGIAN_SEASONS), in_september)

    assert (endgame.season, endgame.difficulty, endgame.seconds) == ("Season of Embers", "4", 300)


def test_without_a_running_stygian_season_the_newest_one_is_read():
    between = datetime(2026, 10, 1, tzinfo=UTC)

    endgame = read_endgame("stygian_onslaught", stygian(*TWO_STYGIAN_SEASONS), between)

    assert endgame.season == "Season of Tides"


def test_a_stygian_season_without_a_best_record_has_no_difficulty_or_time():
    model = stygian(stygian_season(7, "Season of Tides", SEASON_7, best=None, challenges=()))

    endgame = read_endgame("stygian_onslaught", model, FETCHED_AT)

    assert (endgame.has_data, endgame.difficulty, endgame.seconds) == (False, None, None)
    assert (endgame.season, endgame.ends_at, endgame.stages) == ("Season of Tides", SEASON_7[1], ())


def test_stygian_onslaught_shows_at_most_four_challenges():
    challenges = tuple((f"Enemy {number}", 30 + number) for number in range(1, 6))
    model = stygian(stygian_season(7, "Season of Tides", SEASON_7, challenges=challenges))

    stages = read_endgame("stygian_onslaught", model, FETCHED_AT).stages

    assert [stage.name for stage in stages] == ["Enemy 1", "Enemy 2", "Enemy 3", "Enemy 4"]


def test_stygian_onslaught_without_a_season_has_no_data():
    endgame = read_endgame("stygian_onslaught", [], FETCHED_AT)

    assert (endgame.has_data, endgame.season, endgame.ends_at) == (False, None, None)
    assert (endgame.difficulty, endgame.seconds, endgame.stages) == (None, None, ())


def test_stygian_onslaught_keeps_no_team_enemy_or_description():
    text = repr(read_endgame("stygian_onslaught", stygian(*TWO_STYGIAN_SEASONS), FETCHED_AT))

    for left_out in ("Furina", "enemy", DESCRIPTION, "UI_Badge", "1234567"):
        assert left_out not in text


# ---- calendar ---------------------------------------------------------------------------------


def banner(
    name: str,
    version: str,
    start: datetime,
    end: datetime,
    *,
    characters: tuple[tuple[str, int], ...] = (),
    weapons: tuple[tuple[str, int], ...] = (),
) -> dict:
    return {
        "pool_id": 1,
        "version_name": version,
        "pool_name": name,
        "pool_type": 1,
        "avatars": [
            {"id": 1, "icon": ICON, "name": item, "element": "Hydro", "rarity": rarity}
            for item, rarity in characters
        ],
        "weapon": [
            {"id": 2, "icon": ICON, "rarity": rarity, "name": item, "wiki_url": WIKI}
            for item, rarity in weapons
        ],
        "start_timestamp": unix(start),
        "end_timestamp": unix(end),
        "start_time": wall_clock(start),
        "end_time": wall_clock(end),
        "jump_url": WIKI,
        "pool_status": 1,
        "countdown_seconds": max(0, unix(end) - unix(FETCHED_AT)),
    }


def event(
    name: str,
    start: datetime | None,
    end: datetime | None,
    *,
    finished=False,
    abyss: dict | None = None,
    theater_detail: dict | None = None,
) -> dict:
    return {
        "id": 1,
        "name": name,
        "desc": DESCRIPTION,
        "strategy": WIKI,
        "type": "ActTypeOther",
        "start_timestamp": 0 if start is None else unix(start),
        "end_timestamp": 0 if end is None else unix(end),
        "start_time": None if start is None else wall_clock(start),
        "end_time": None if end is None else wall_clock(end),
        "status": 2,
        "countdown_seconds": 0,
        "reward_list": [],
        "is_finished": finished,
        "explore_detail": None,
        "double_detail": None,
        "tower_detail": abyss,
        "role_combat_detail": theater_detail,
    }


CALENDAR = {
    "avatar_card_pool_list": [
        banner("An Ended Wish", "6.0", days(-26), days(-5), characters=(("Neuvillette", 5),)),
        banner(
            "Ballad of the Fontaine Stage",
            "6.1",
            days(-5),
            days(16),
            characters=(("Furina", 5), ("Xingqiu", 4), ("Bennett", 4), ("Yaoyao", 4)),
        ),
        banner(
            "Next Wish",
            "6.1",
            days(16),
            days(37),
            characters=(("Mavuika", 5), ("Iansan", 4)),
        ),
    ],
    "weapon_card_pool_list": [
        banner(
            "Epitome Invocation",
            "6.1",
            days(-5),
            days(16),
            weapons=(
                ("Splendor of Tranquil Waters", 5),
                ("A Thousand Blazing Suns", 5),
                ("The Flute", 4),
                ("Sacrificial Bow", 4),
            ),
        ),
    ],
    "mixed_card_pool_list": [
        banner(
            "Chronicled Wish",
            "6.1",
            days(-5),
            days(16),
            characters=(("Diluc", 5), ("Barbara", 4)),
            weapons=(("Wolf's Gravestone", 5), ("Favonius Sword", 4)),
        ),
    ],
    "act_list": [
        event("Lantern Rite", days(-3), days(10)),
        event("A Later Event", days(4), days(20)),
        event("An Event That Ended", days(-20), days(-1)),
        event("A Finished Event", days(-5), days(9), finished=True),
        event("An Event Without Times", None, None),
    ],
    "fixed_act_list": [
        event(
            "Spiral Abyss",
            ABYSS_START,
            ABYSS_END,
            abyss={"is_unlock": True, "max_star": 36, "total_star": 30, "has_data": True},
        ),
        event(
            "Imaginarium Theater",
            *OCTOBER,
            theater_detail={"is_unlock": True, "max_round_id": 8, "has_data": True},
        ),
        event("Stygian Onslaught", *SEASON_7),
        event(
            "Spiral Abyss",
            days(-31),
            days(-0.5),
            abyss={"is_unlock": True, "max_star": 36, "total_star": 36, "has_data": True},
        ),
    ],
}


def calendar_model(payload: dict = CALENDAR) -> GenshinEventCalendar:
    return build(GenshinEventCalendar, payload)


def test_banners_split_their_names_by_rarity_and_keep_upcoming_ones():
    banners = read_calendar(calendar_model(), FETCHED_AT).banners

    assert banners == (
        Banner(
            "character",
            "Ballad of the Fontaine Stage",
            ("Furina",),
            ("Xingqiu", "Bennett", "Yaoyao"),
            days(-5),
            days(16),
        ),
        Banner("character", "Next Wish", ("Mavuika",), ("Iansan",), days(16), days(37)),
        Banner(
            "weapon",
            "Epitome Invocation",
            ("Splendor of Tranquil Waters", "A Thousand Blazing Suns"),
            ("The Flute", "Sacrificial Bow"),
            days(-5),
            days(16),
        ),
        Banner(
            "chronicled",
            "Chronicled Wish",
            ("Diluc", "Wolf's Gravestone"),
            ("Barbara", "Favonius Sword"),
            days(-5),
            days(16),
        ),
    )


def test_calendar_times_come_from_the_unix_timestamps_never_the_naive_wall_clocks():
    model = calendar_model()
    naive_end = model.character_banners[1].end_time

    current = read_calendar(model, FETCHED_AT).banners[0]

    assert naive_end.tzinfo is None  # genshin.py gives this calendar no time zone
    assert current.ends_at == days(16)
    assert current.ends_at.utcoffset() == timedelta(0)


def test_a_banner_without_a_pool_name_has_no_name():
    payload = {
        **CALENDAR,
        "avatar_card_pool_list": [banner("", "6.1", days(-5), days(16))],
    }

    assert read_calendar(calendar_model(payload), FETCHED_AT).banners[0].name is None


def test_events_keep_their_start_whether_they_are_finished_and_upcoming_ones():
    events = read_calendar(calendar_model(), FETCHED_AT).events

    assert events == (
        GameEvent("Lantern Rite", days(-3), days(10), None, None, False),
        GameEvent("A Later Event", days(4), days(20), None, None, False),
        GameEvent("A Finished Event", days(-5), days(9), None, None, True),
        GameEvent("An Event Without Times", None, None, None, None, False),
    )


def test_endgame_windows_come_from_the_abyss_and_theater_challenges_and_skip_the_rest():
    challenges = read_calendar(calendar_model(), FETCHED_AT).challenges

    assert challenges == (
        ChallengeWindow("spiral_abyss", "Spiral Abyss", ABYSS_START, ABYSS_END, 30, 36),
        ChallengeWindow("imaginarium_theater", "Imaginarium Theater", *OCTOBER, 8, None),
    )


def test_the_calendar_names_the_version_of_its_first_banner_still_running():
    assert read_calendar(calendar_model(), FETCHED_AT).version == "6.1"
    without_banners = {
        **CALENDAR,
        "avatar_card_pool_list": [],
        "weapon_card_pool_list": [],
        "mixed_card_pool_list": [],
    }
    assert read_calendar(calendar_model(without_banners), FETCHED_AT).version is None


def test_calendar_times_are_aware():
    calendar = read_calendar(calendar_model(), FETCHED_AT)

    items = (*calendar.banners, *calendar.events, *calendar.challenges)
    moments = [
        moment for item in items for moment in (item.starts_at, item.ends_at) if moment is not None
    ]
    assert len(moments) == 18
    assert all(moment.utcoffset() is not None for moment in moments)


def test_the_calendar_keeps_no_description_icon_or_link():
    text = repr(read_calendar(calendar_model(), FETCHED_AT))

    assert DESCRIPTION not in text
    assert "https://" not in text
