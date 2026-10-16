import copy
from datetime import UTC, datetime, timedelta, timezone

import pytest
from genshin.models import (
    AnomalyArbitration,
    HSREventCalendar,
    StarRailAPCShadow,
    StarRailChallenge,
    StarRailDetailCharacterResponse,
    StarRailPureFiction,
)

from waifu.gacha.starrail import (
    HsrChallengeWindow,
    HsrCharacter,
    HsrEndgame,
    HsrEvent,
    HsrFloor,
    HsrLightCone,
    HsrRelic,
    HsrWarp,
    read_calendar,
    read_character,
    read_endgame,
)

SERVER_TZ = timezone(timedelta(hours=8))
UID = "800000001"
NICKNAME = "Master"


# genshin.py's validators rewrite the payload they are given, so every model gets a deep copy.
def build(model_type, payload: dict):
    return model_type(**copy.deepcopy(payload))


# ---- characters -------------------------------------------------------------------------------

PROPERTY_NAMES = {
    1: "HP",
    2: "ATK",
    3: "DEF",
    4: "SPD",
    5: "CRIT Rate",
    6: "CRIT DMG",
    12: "Lightning DMG Boost",
}


def stat(kind: int, final: str) -> dict:
    return {"property_type": kind, "base": "100", "add": "10", "final": final}


def relic(pos: int, kind: int, value: str, level: int = 15) -> dict:
    return {
        "id": 61000 + pos,
        "pos": pos,
        "rarity": 5,
        "level": level,
        "name": f"Relic piece {pos}",
        "desc": "",
        "icon": "",
        "main_property": {"property_type": kind, "value": value, "times": 0},
        "properties": [{"property_type": 5, "value": "3.2%", "times": 1}],
    }


def avatar(
    avatar_id: int,
    name: str,
    path: int,
    *,
    rarity: int = 5,
    level: int = 80,
    rank: int = 0,
    equip: dict | None = None,
    properties: tuple[dict, ...] = (),
    relics: tuple[dict, ...] = (),
    ornaments: tuple[dict, ...] = (),
    servant: dict | None = None,
) -> dict:
    return {
        "id": avatar_id,
        "element": "Lightning",
        "rarity": rarity,
        "icon": "",
        "name": name,
        "level": level,
        "rank": rank,
        "image": "",
        "equip": equip,
        "relics": list(relics),
        "ornaments": list(ornaments),
        "ranks": [],
        "base_type": path,
        "figure_path": "",
        "properties": list(properties),
        "skills": [],
        "servant_detail": servant,
    }


def characters_payload(*avatars: dict) -> dict:
    return {
        "avatar_list": list(avatars),
        "equip_wiki": {},
        "relic_wiki": {},
        "property_info": {
            str(kind): {
                "property_type": kind,
                "name": name,
                "icon": "",
                "property_name_relic": name,
                "property_name_filter": name,
            }
            for kind, name in PROPERTY_NAMES.items()
        },
        "recommend_property": {
            str(entry["id"]): {
                "recommend_relic_properties": [5],
                "custom_relic_properties": [],
                "is_custom_property_valid": False,
            }
            for entry in avatars
        },
        "relic_properties": [],
    }


ACHERON = avatar(
    1308,
    "Acheron",
    5,
    rank=2,
    equip={
        "id": 23024,
        "level": 80,
        "rank": 1,
        "name": "Along the Passing Shore",
        "desc": "A long description",
        "icon": "",
        "rarity": 5,
    },
    properties=(stat(1, "3512"), stat(2, "3021"), stat(5, "65.2%")),
    relics=(relic(1, 1, "705"), relic(2, 2, "352"), relic(3, 5, "32.4%"), relic(4, 2, "43.2%")),
    ornaments=(relic(5, 12, "38.8%"), relic(6, 2, "43.2%", level=12)),
    servant={
        "servant_id": "0",
        "servant_name": "",
        "servant_icon": "",
        "servant_properties": [],
        "servant_skills": [],
    },
)
DAN_HENG = avatar(1002, "Dan Heng", 2, rarity=4, level=60, properties=(stat(4, "110"),))
AGLAEA = avatar(
    1402,
    "Aglaea",
    8,
    servant={
        "servant_id": "11402",
        "servant_name": "Garmentmaker",
        "servant_icon": "",
        "servant_properties": [stat(1, "4000")],
        "servant_skills": [],
    },
)


def characters(*avatars: dict):
    return build(StarRailDetailCharacterResponse, characters_payload(*avatars)).avatar_list


def test_a_character_is_read_with_eidolon_light_cone_final_stats_relics_and_ornaments():
    (acheron,) = characters(ACHERON)

    assert read_character(acheron) == HsrCharacter(
        name="Acheron",
        rarity=5,
        level=80,
        eidolon=2,
        path="Nihility",
        element="Lightning",
        light_cone=HsrLightCone("Along the Passing Shore", 80, 1, 5),
        stats=(("HP", "3512"), ("ATK", "3021"), ("CRIT Rate", "65.2%")),
        relics=(
            HsrRelic("Head", "HP", "705", 15),
            HsrRelic("Hands", "ATK", "352", 15),
            HsrRelic("Body", "CRIT Rate", "32.4%", 15),
            HsrRelic("Feet", "ATK", "43.2%", 15),
            HsrRelic("Planar Sphere", "Lightning DMG Boost", "38.8%", 15),
            HsrRelic("Link Rope", "ATK", "43.2%", 12),
        ),
        memosprite=None,
    )


def test_a_character_without_a_light_cone_has_none_and_a_two_word_path_reads_naturally():
    (dan_heng,) = characters(DAN_HENG)

    character = read_character(dan_heng)

    assert character.light_cone is None
    assert character.path == "The Hunt"
    assert (character.rarity, character.level, character.eidolon) == (4, 60, 0)
    assert character.stats == (("SPD", "110"),)
    assert character.relics == ()


def test_a_memosprite_is_read_by_its_name():
    (aglaea,) = characters(AGLAEA)

    character = read_character(aglaea)

    assert character.memosprite == "Garmentmaker"
    assert character.path == "Remembrance"


def test_a_character_keeps_no_relic_piece_name_description_or_icon():
    (acheron,) = characters(ACHERON)

    text = repr(read_character(acheron))

    assert "Relic piece" not in text
    assert "A long description" not in text


# ---- endgame ----------------------------------------------------------------------------------


def server_time(month: int, day: int, hour: int, minute: int = 0) -> dict:
    # genshin.py adds the uid's server offset ("tzinfo") to every endgame time it returns.
    return {"year": 2026, "month": month, "day": day, "hour": hour, "minute": minute, "tzinfo": 8}


SEASON_START = server_time(10, 6, 4)
SEASON_END = server_time(11, 17, 3, 59)  # HoYoLAB's 03:59:59, which genshin.py keeps to the minute
SEASON_END_AT = datetime(2026, 11, 17, 4, 0, tzinfo=SERVER_TZ)  # the 04:00 reset


def group(name: str) -> dict:
    return {
        "schedule_id": 1012,
        "name_mi18n": name,
        "status": "challenge_status_open",
        "begin_time": SEASON_START,
        "end_time": SEASON_END,
    }


def chaos_floor(name: str, stars: int, cycles: int) -> dict:
    return {
        "maze_id": 1,
        "name": name,
        "star_num": stars,
        "is_fast": False,
        "round_num": cycles,
        "is_chaos": True,
        "node_1": {"challenge_time": server_time(10, 8, 21), "avatars": []},
    }


MEMORY_OF_CHAOS = {
    "schedule_id": 1012,
    "begin_time": SEASON_START,
    "end_time": SEASON_END,
    "star_num": 33,
    "extra_star_num": 2,
    "max_floor": "Stage 12",
    "max_floor_id": 12,
    "battle_num": 14,
    "has_data": True,
    "all_floor_detail": [
        chaos_floor("Stage 12", 3, 5),
        chaos_floor("Stage 11", 2, 7),
        chaos_floor("Stage 10", 3, 4),
        chaos_floor("Stage 9", 3, 3),
    ],
    "groups": [group("Gleaming Treasure"), group("An older season")],
}


def scored_node(score: int) -> dict:
    return {"challenge_time": server_time(10, 9, 20), "avatars": [], "buff": None, "score": score}


PURE_FICTION = {
    "star_num": 12,
    "extra_star_num": 0,
    "max_floor": "Stage 4",
    "battle_num": 8,
    "has_data": True,
    "all_floor_detail": [
        {
            "maze_id": 4,
            "name": "Stage 4",
            "star_num": 3,
            "is_fast": False,
            "round_num": 0,
            "node_1": scored_node(20000),
            "node_2": scored_node(20500),
        }
    ],
    "groups": [group("Nameless Tales")],
    "max_floor_id": 4,
}

APOCALYPTIC_SHADOW = {
    "star_num": 9,
    "max_floor": "Difficulty 4",
    "battle_num": 6,
    "has_data": True,
    "all_floor_detail": [
        {
            "maze_id": 4,
            "name": "Difficulty 4",
            "star_num": 3,
            "is_fast": False,
            "node_1": {**scored_node(3800), "boss_defeated": True},
            "node_2": {**scored_node(3500), "boss_defeated": True},
            "last_update_time": server_time(10, 9, 21),
        }
    ],
    "groups": [
        {
            **group("Shadow of the Ranger"),
            "upper_boss": {"id": 1, "name_mi18n": "Upper Boss", "icon": ""},
        }
    ],
    "max_floor_id": 4,
}


def anomaly_record(*, boss_played: bool = True) -> dict:
    record = {
        "group": {
            "group_id": 3,
            "begin_time": SEASON_START,
            "end_time": SEASON_END,
            "status": "challenge_status_open",
            "name_mi18n": "Arbitration of the Lost",
            "game_version": "3.7",
            "theme_pic_path": "",
        },
        "boss_info": {
            "maze_id": 9,
            "name_mi18n": "Lord of the Chamber",
            "hard_mode_name_mi18n": "Hard",
            "icon": "",
        },
        "mob_infos": [{"maze_id": 8, "name": "Knight", "monster_name": "Mob", "monster_icon": ""}],
        "has_challenge_record": True,
        "battle_num": 4,
        "mob_records": [
            {
                "maze_id": 8,
                "has_challenge_record": True,
                "challenge_time": server_time(10, 10, 20),
                "avatars": [],
                "round_num": 2,
                "star_num": 3,
                "is_fast": False,
            }
        ],
        "boss_stars": 3 if boss_played else 0,
        "mob_stars": 9,
    }
    if boss_played:
        record["boss_record"] = {
            "maze_id": 9,
            "has_challenge_record": True,
            "challenge_time": server_time(10, 10, 21),
            "avatars": [],
            "buff": {"id": 1, "name_mi18n": "Buff", "desc_mi18n": "", "icon": ""},
            "hard_mode": False,
            "round_num": 3,
            "star_num": 3,
            "finish_color_medal": False,
            "challenge_peak_rank_icon_type": "",
            "challenge_peak_rank_icon": "",
            "record_unique_key": "key",
        }
    return record


def anomaly_arbitration(*records: dict) -> dict:
    return {
        "challenge_peak_records": list(records),
        "challenge_peak_best_record_brief": None,
        "role": {"server": "prod_official_asia", "nickname": NICKNAME, "level": 70, "role_id": UID},
    }


def test_memory_of_chaos_reads_this_season_and_the_first_three_floors_with_cycles():
    endgame = read_endgame("memory_of_chaos", build(StarRailChallenge, MEMORY_OF_CHAOS))

    assert endgame == HsrEndgame(
        mode="memory_of_chaos",
        season="Gleaming Treasure",
        ends_at=SEASON_END_AT,
        has_data=True,
        stars=33,
        extra_stars=2,
        deepest_floor="Stage 12",
        battles=14,
        floors=(
            HsrFloor("Stage 12", 3, 5, None),
            HsrFloor("Stage 11", 2, 7, None),
            HsrFloor("Stage 10", 3, 4, None),
        ),
    )
    assert endgame.ends_at.utcoffset() == timedelta(hours=8)


@pytest.mark.parametrize(
    ("mode", "model_type", "payload", "season", "floor"),
    [
        (
            "pure_fiction",
            StarRailPureFiction,
            PURE_FICTION,
            "Nameless Tales",
            HsrFloor("Stage 4", 3, None, 40500),
        ),
        (
            "apocalyptic_shadow",
            StarRailAPCShadow,
            APOCALYPTIC_SHADOW,
            "Shadow of the Ranger",
            HsrFloor("Difficulty 4", 3, None, 7300),
        ),
    ],
)
def test_pure_fiction_and_apocalyptic_shadow_floors_carry_their_score(
    mode, model_type, payload, season, floor
):
    endgame = read_endgame(mode, build(model_type, payload))

    assert (endgame.mode, endgame.season, endgame.ends_at) == (mode, season, SEASON_END_AT)
    assert (endgame.has_data, endgame.stars, endgame.extra_stars) == (True, payload["star_num"], 0)
    assert (endgame.deepest_floor, endgame.battles) == (floor.name, payload["battle_num"])
    assert endgame.floors == (floor,)


def test_a_mode_not_played_this_season_has_no_floors_and_no_deepest_floor():
    payload = {
        **MEMORY_OF_CHAOS,
        "star_num": 0,
        "extra_star_num": 0,
        "max_floor": "",
        "max_floor_id": 0,
        "battle_num": 0,
        "has_data": False,
        "all_floor_detail": [],
    }

    endgame = read_endgame("memory_of_chaos", build(StarRailChallenge, payload))

    assert (endgame.has_data, endgame.stars, endgame.deepest_floor) == (False, 0, None)
    assert endgame.floors == ()
    assert (endgame.season, endgame.ends_at) == ("Gleaming Treasure", SEASON_END_AT)


@pytest.mark.parametrize("minute", [0, 58])
def test_a_season_end_on_any_other_minute_stays_as_given(minute):
    season = {**group("Gleaming Treasure"), "end_time": server_time(11, 17, 4, minute)}
    payload = {**MEMORY_OF_CHAOS, "groups": [season]}

    endgame = read_endgame("memory_of_chaos", build(StarRailChallenge, payload))

    assert endgame.ends_at == datetime(2026, 11, 17, 4, minute, tzinfo=SERVER_TZ)


def test_a_mode_without_a_season_has_no_season_or_end():
    payload = {**MEMORY_OF_CHAOS, "groups": []}

    endgame = read_endgame("memory_of_chaos", build(StarRailChallenge, payload))

    assert (endgame.season, endgame.ends_at) == (None, None)


def test_anomaly_arbitration_reads_this_seasons_record():
    model = build(AnomalyArbitration, anomaly_arbitration(anomaly_record()))

    assert read_endgame("anomaly_arbitration", model) == HsrEndgame(
        mode="anomaly_arbitration",
        season="Arbitration of the Lost",
        ends_at=SEASON_END_AT,
        has_data=True,
        stars=12,  # the boss's 3 and the knights' 9
        extra_stars=0,
        deepest_floor="Lord of the Chamber",
        battles=4,
        floors=(),
    )


def test_anomaly_arbitration_without_a_boss_record_names_no_deepest_floor():
    model = build(AnomalyArbitration, anomaly_arbitration(anomaly_record(boss_played=False)))

    endgame = read_endgame("anomaly_arbitration", model)

    assert (endgame.stars, endgame.deepest_floor) == (9, None)


def test_anomaly_arbitration_without_records_has_no_data():
    model = build(AnomalyArbitration, anomaly_arbitration())

    assert read_endgame("anomaly_arbitration", model) == HsrEndgame(
        mode="anomaly_arbitration",
        season=None,
        ends_at=None,
        has_data=False,
        stars=0,
        extra_stars=0,
        deepest_floor=None,
        battles=None,
        floors=(),
    )


def test_endgame_results_never_hold_the_uid_or_the_nickname():
    model = build(AnomalyArbitration, anomaly_arbitration(anomaly_record()))

    text = repr(read_endgame("anomaly_arbitration", model))

    assert UID not in text
    assert NICKNAME not in text


# ---- calendar ---------------------------------------------------------------------------------

FETCHED_AT = datetime(2026, 10, 15, 13, 0, tzinfo=UTC)
API_NOW = int(FETCHED_AT.timestamp())  # the calendar's own clock, as unix seconds
DAY = 86400


def time_info(start: int, end: int) -> dict:
    return {
        "start_ts": str(start),
        "end_ts": str(end),
        "start_time": {"year": 2026},
        "end_time": {"year": 2026},
        "now": str(API_NOW),
    }


NO_TIME = {"start_ts": "0", "end_ts": "0", "start_time": None, "end_time": None, "now": "0"}


def warp(
    name: str,
    start: int,
    end: int,
    *,
    characters: tuple[tuple[str, int], ...] = (),
    light_cones: tuple[tuple[str, int], ...] = (),
) -> dict:
    return {
        "time_info": time_info(start, end),
        "name": name,
        "type": "CardPoolRole",
        "avatar_list": [
            {
                "item_id": 1,
                "item_name": item,
                "icon_url": "",
                "avatar_base_type": 5,
                "rarity": rarity,
                "wiki_url": "",
                "is_forward": True,
                "item_avatar_icon_path": "",
                "damage_type": 1,
            }
            for item, rarity in characters
        ],
        "equip_list": [
            {
                "item_id": 2,
                "item_name": item,
                "item_url": "",
                "avatar_base_type": 5,
                "rarity": rarity,
                "wiki_url": "",
                "is_forward": True,
            }
            for item, rarity in light_cones
        ],
        "is_after_version": False,
        "version": "3.7",
        "id": 1,
    }


def event(
    name: str,
    info: dict,
    *,
    current: int = 0,
    total: int = 0,
    finished: bool = False,
) -> dict:
    return {
        "time_info": info,
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
        "all_finished": finished,
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


def challenge(kind: str, name: str, start: int, end: int, *, current=0, total=0) -> dict:
    return {
        "time_info": time_info(start, end),
        "group_id": 1,
        "name_mi18n": name,
        "challenge_type": kind,
        "status": "challengeStatusInProgress",
        "reward_list": [],
        "special_reward": None,
        "total_progress": total,
        "current_progress": current,
        "show_text": "",
    }


CALENDAR = {
    "avatar_card_pool_list": [
        warp(
            "Butterfly on Swordtip",
            API_NOW - 3 * DAY,
            API_NOW + 18 * DAY,
            characters=(("Acheron", 5), ("Pela", 4), ("Gallagher", 4), ("Hook", 4)),
        ),
        warp(
            "Gentle Eclipse",
            API_NOW + 18 * DAY,
            API_NOW + 39 * DAY,
            characters=(("Firefly", 5), ("Lynx", 4)),
        ),
        warp("A Finished Warp", API_NOW - 30 * DAY, API_NOW - 9 * DAY, characters=(("Seele", 5),)),
    ],
    "equip_card_pool_list": [
        warp(
            "Brilliant Fixation",
            API_NOW - 3 * DAY,
            API_NOW + 18 * DAY,
            light_cones=(("Along the Passing Shore", 5), ("Good Night and Sleep Well", 4)),
        ),
    ],
    "act_list": [
        event(
            "Gift of Odyssey", time_info(API_NOW - 2 * DAY, API_NOW + 5 * DAY), current=3, total=7
        ),
        event("Planar Fissure", time_info(API_NOW - 1 * DAY, API_NOW + 2 * DAY)),
        event("A Later Event", time_info(API_NOW + 4 * DAY, API_NOW + 20 * DAY), total=5),
        event(
            "A Finished Event",
            time_info(API_NOW - 5 * DAY, API_NOW + 9 * DAY),
            current=7,
            total=7,
            finished=True,
        ),
        event("An Event That Ended", time_info(API_NOW - 20 * DAY, API_NOW - 1 * DAY), total=3),
        event("Simulated Universe", NO_TIME, current=1, total=10),
    ],
    "challenge_list": [
        challenge(
            "ChallengeTypeChasm",
            "Gleaming Treasure",
            API_NOW - 9 * DAY,
            API_NOW + 33 * DAY,
            current=30,
            total=36,
        ),
        challenge("ChallengeTypeStory", "Nameless Tales", API_NOW - 2 * DAY, API_NOW + 40 * DAY),
        challenge("ChallengeTypeBoss", "Shadow of the Ranger", API_NOW - DAY, API_NOW + 41 * DAY),
        challenge("ChallengeTypePeak", "Arbitration", API_NOW - 4 * DAY, API_NOW + 38 * DAY),
        challenge("ChallengeTypeSomethingNew", "A New Mode", API_NOW - DAY, API_NOW + 20 * DAY),
        challenge("ChallengeTypeChasm", "Next Season", API_NOW + 33 * DAY, API_NOW + 75 * DAY),
    ],
    "cur_game_version": "3.7",
    "now": str(API_NOW),
}


def calendar_model(payload: dict = CALENDAR) -> HSREventCalendar:
    return build(HSREventCalendar, payload)


@pytest.mark.parametrize(
    "fetched_at",
    [
        FETCHED_AT,
        # HoYoLAB stamps its whole-second `now` when it answers: after the connection, the
        # request, and its own work...
        FETCHED_AT - timedelta(seconds=2),
        # ...which may just cross a second...
        FETCHED_AT - timedelta(seconds=0.05),
        # ...and the PC clock may run a little ahead of HoYoLAB's.
        FETCHED_AT + timedelta(minutes=2),
    ],
)
def test_calendar_times_keep_hoyolabs_minute_whatever_the_latency_or_clock_skew(fetched_at):
    model = calendar_model()

    current = read_calendar(model, fetched_at).warps[0]

    assert current.starts_at == FETCHED_AT - timedelta(days=3)
    assert current.ends_at == FETCHED_AT + timedelta(days=18)


def moved(payload: object, seconds: int) -> object:
    """`payload` with every unix time moved by `seconds`, as if the API counted in another clock."""
    if isinstance(payload, dict):
        return {
            key: str(int(value) + seconds) if key in UNIX_TIMES else moved(value, seconds)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [moved(item, seconds) for item in payload]
    return payload


UNIX_TIMES = {"start_ts", "end_ts", "now"}


def unix(moment: datetime) -> int:
    return int(moment.timestamp())


def test_calendar_times_on_a_minutes_last_second_read_as_the_next_whole_minute():
    # HoYoLAB ends a warp at 11:59:59 server time, which printed "berakhir 10:59" WIB where the
    # game says 11:00 WIB, and an event or endgame window at 03:59:59 for the 04:00 reset.
    warp_end = datetime(2026, 10, 21, 11, 59, 59, tzinfo=SERVER_TZ)
    event_end = datetime(2026, 10, 20, 3, 59, 59, tzinfo=SERVER_TZ)
    season_end = datetime(2026, 11, 17, 3, 59, 59, tzinfo=SERVER_TZ)
    payload = {
        **CALENDAR,
        "avatar_card_pool_list": [
            warp("Bloom of Death", API_NOW - 6 * DAY, unix(warp_end)),
            # A start given on a minute's last second reads the same way.
            warp("Next Phase", unix(warp_end), unix(warp_end) + 21 * DAY),
        ],
        "equip_card_pool_list": [],
        "act_list": [event("Planar Fissure", time_info(API_NOW - DAY, unix(event_end)))],
        "challenge_list": [
            challenge("ChallengeTypeChasm", "Gleaming Treasure", API_NOW - DAY, unix(season_end))
        ],
    }

    calendar = read_calendar(calendar_model(payload), FETCHED_AT)

    assert calendar.warps[0].ends_at == datetime(2026, 10, 21, 12, 0, tzinfo=SERVER_TZ)
    assert calendar.warps[1].starts_at == datetime(2026, 10, 21, 12, 0, tzinfo=SERVER_TZ)
    assert calendar.events[0].ends_at == datetime(2026, 10, 20, 4, 0, tzinfo=SERVER_TZ)
    assert calendar.challenges[0].ends_at == datetime(2026, 11, 17, 4, 0, tzinfo=SERVER_TZ)
    assert calendar.warps[0].starts_at == FETCHED_AT - timedelta(days=6)  # a whole minute stays


def test_a_calendar_end_on_minute_59_of_its_hour_reads_as_the_next_whole_hour():
    # The owner's real calendar (2026-10-17): HoYoLAB gave the warp end as 11:59:00 server
    # time, to the minute only, and the next phase's start as 12:00; it printed "10:59" WIB.
    warp_end = datetime(2026, 10, 21, 11, 59, tzinfo=SERVER_TZ)
    next_start = datetime(2026, 10, 21, 12, 0, tzinfo=SERVER_TZ)
    payload = {
        **CALENDAR,
        "avatar_card_pool_list": [
            warp("Bloom of Death", API_NOW - 6 * DAY, unix(warp_end)),
            warp("Next Phase", unix(next_start), unix(next_start) + 21 * DAY),
        ],
        "equip_card_pool_list": [],
    }

    warps = read_calendar(calendar_model(payload), FETCHED_AT).warps

    assert warps[0].ends_at == next_start == warps[1].starts_at


def test_a_calendar_end_on_any_other_minute_stays_as_given():
    end = datetime(2026, 10, 21, 11, 58, tzinfo=SERVER_TZ)
    payload = {
        **CALENDAR,
        "avatar_card_pool_list": [warp("Bloom of Death", API_NOW - 6 * DAY, unix(end))],
        "equip_card_pool_list": [],
    }

    assert read_calendar(calendar_model(payload), FETCHED_AT).warps[0].ends_at == end


def test_an_endgame_season_ends_when_its_calendar_window_does():
    # HoYoLAB ends both at 03:59:59 server time for the 04:00 reset: the calendar gives that
    # second, the endgame only its minute (03:59), which printed "berakhir 02:59" WIB for the
    # season where the calendar window said 03:00.
    season_end = datetime(2026, 11, 17, 3, 59, 59, tzinfo=SERVER_TZ)
    challenges = [
        challenge("ChallengeTypeChasm", "Gleaming Treasure", API_NOW - DAY, unix(season_end))
    ]
    model = calendar_model({**CALENDAR, "challenge_list": challenges})

    window = read_calendar(model, FETCHED_AT).challenges[0]
    season = read_endgame("memory_of_chaos", build(StarRailChallenge, MEMORY_OF_CHAOS))

    assert season.ends_at == window.ends_at


@pytest.mark.parametrize("offset", [timedelta(hours=8), timedelta(hours=-5, minutes=-45)])
def test_calendar_times_are_right_whichever_clock_the_api_counts_in(offset):
    fetched_at = FETCHED_AT - timedelta(seconds=2)
    model = calendar_model(moved(CALENDAR, int(offset.total_seconds())))

    calendar = read_calendar(model, fetched_at)

    assert calendar == read_calendar(calendar_model(), FETCHED_AT)
    assert calendar.warps[0].ends_at == FETCHED_AT + timedelta(days=18)


def test_calendar_times_undo_genshins_relabelled_timezone():
    # genshin.py 1.7.31 reads the API's times as UTC and relabels them +08:00 without
    # converting, so reading a time on its own is eight hours early.
    model = calendar_model()
    naive_end = model.character_warps[0].time_info.end

    current = read_calendar(model, FETCHED_AT).warps[0]

    assert current.ends_at == FETCHED_AT + timedelta(days=18)
    assert current.ends_at - naive_end == timedelta(hours=8)


def test_warps_split_their_items_by_rarity_and_keep_upcoming_ones():
    warps = read_calendar(calendar_model(), FETCHED_AT).warps

    assert warps == (
        HsrWarp(
            "character",
            "Butterfly on Swordtip",
            ("Acheron",),
            ("Pela", "Gallagher", "Hook"),
            FETCHED_AT - timedelta(days=3),
            FETCHED_AT + timedelta(days=18),
        ),
        HsrWarp(
            "character",
            "Gentle Eclipse",
            ("Firefly",),
            ("Lynx",),
            FETCHED_AT + timedelta(days=18),
            FETCHED_AT + timedelta(days=39),
        ),
        HsrWarp(
            "light_cone",
            "Brilliant Fixation",
            ("Along the Passing Shore",),
            ("Good Night and Sleep Well",),
            FETCHED_AT - timedelta(days=3),
            FETCHED_AT + timedelta(days=18),
        ),
    )


def days(count: int) -> datetime:
    return FETCHED_AT + timedelta(days=count)


def test_events_keep_their_start_the_owners_progress_and_upcoming_ones():
    # An upcoming event stays, so a calendar kept across its start still lists it.
    events = read_calendar(calendar_model(), FETCHED_AT).events

    assert events == (
        HsrEvent("Gift of Odyssey", days(-2), days(5), 3, 7, False),
        HsrEvent("Planar Fissure", days(-1), days(2), None, None, False),
        HsrEvent("A Later Event", days(4), days(20), 0, 5, False),
        HsrEvent("A Finished Event", days(-5), days(9), 7, 7, True),
        HsrEvent("Simulated Universe", None, None, 1, 10, False),
    )


def test_endgame_windows_name_their_mode_keep_upcoming_ones_and_skip_unknown_types():
    challenges = read_calendar(calendar_model(), FETCHED_AT).challenges

    assert challenges == (
        HsrChallengeWindow("memory_of_chaos", "Gleaming Treasure", days(-9), days(33), 30, 36),
        HsrChallengeWindow("pure_fiction", "Nameless Tales", days(-2), days(40), None, None),
        HsrChallengeWindow(
            "apocalyptic_shadow", "Shadow of the Ranger", days(-1), days(41), None, None
        ),
        HsrChallengeWindow("anomaly_arbitration", "Arbitration", days(-4), days(38), None, None),
        HsrChallengeWindow("memory_of_chaos", "Next Season", days(33), days(75), None, None),
    )


def test_the_calendar_names_the_game_version():
    assert read_calendar(calendar_model(), FETCHED_AT).version == "3.7"
    empty_version = calendar_model({**CALENDAR, "cur_game_version": ""})
    assert read_calendar(empty_version, FETCHED_AT).version is None


def test_calendar_times_are_aware():
    calendar = read_calendar(calendar_model(), FETCHED_AT)

    items = (*calendar.warps, *calendar.events, *calendar.challenges)
    moments = [
        moment for item in items for moment in (item.starts_at, item.ends_at) if moment is not None
    ]
    assert len(moments) == 24
    assert all(moment.utcoffset() is not None for moment in moments)


def test_the_calendar_keeps_no_description():
    assert "An event description" not in repr(read_calendar(calendar_model(), FETCHED_AT))
