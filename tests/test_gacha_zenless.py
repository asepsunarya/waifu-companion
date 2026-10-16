import copy
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone

import pytest
from genshin.models import (
    DeadlyAssault,
    ShiyuDefenseV1,
    ShiyuDefenseV2,
    ZZZEvent,
    ZZZFullAgent,
    ZZZGachaCalendar,
    ZZZPartialAgent,
)

from waifu.gacha.game_data import Banner, Endgame, EndgameStage, GameEvent
from waifu.gacha.zenless import (
    ZzzAgent,
    ZzzAgentDetail,
    ZzzDisc,
    ZzzWEngine,
    read_agent,
    read_calendar,
    read_detail,
    read_endgame,
)

SERVER_TZ = timezone(timedelta(hours=8))
NICKNAME = "Proxy Belle"
AVATAR = "player-avatar-icon"


# genshin.py's validators rewrite the payload they are given, so every model gets a deep copy.
def build(model_type, payload: dict):
    return model_type(**copy.deepcopy(payload))


# ---- values -----------------------------------------------------------------------------------


def test_the_zzz_values_are_frozen():
    w_engine = ZzzWEngine("Deep Sea Visitor", level=60, refinement=1, rarity="S")
    agent = ZzzAgent(
        ref=1191,
        name="Ellen",
        full_name="Ellen Joe",
        rarity="S",
        level=60,
        mindscape=2,
        element="Ice",
        specialty="Attack",
    )
    disc = ZzzDisc(slot=1, set_name="Polar Metal", main_stat="HP", value="2200", level=15)
    detail = ZzzAgentDetail(agent, w_engine, stats=(("HP", "8000"),), discs=(disc,), sets=())

    for value in (w_engine, agent, disc, detail):
        with pytest.raises(FrozenInstanceError):
            setattr(value, fields(value)[0].name, None)


# ---- agents -----------------------------------------------------------------------------------


def agent(
    agent_id: int,
    name: str,
    full_name: str,
    *,
    rarity: str = "S",
    level: int = 60,
    rank: int = 0,
    element: int = 202,
    profession: int = 1,
) -> dict:
    return {
        "id": agent_id,
        "element_type": element,
        "rarity": rarity,
        "name_mi18n": name,
        "full_name_mi18n": full_name,
        "avatar_profession": profession,
        "group_icon_path": "",
        "hollow_icon_path": "",
        "role_square_url": "",
        "camp_name_mi18n": "Victoria Housekeeping",
        "level": level,
        "rank": rank,
    }


def prop(name: str, value: str, prop_id: int = 1) -> dict:
    return {"property_name": name, "property_id": prop_id, "base": value}


def agent_stat(name: str, final: str, prop_id: int = 1) -> dict:
    return {**prop(name, "100", prop_id), "add": "10", "final": final}


def disc(slot: int, set_name: str, main: str, value: str, *, level: int = 15) -> dict:
    return {
        "id": 31000 + slot,
        "level": level,
        "name": f"{set_name} disc [{slot}]",
        "icon": "",
        "rarity": "S",
        "main_properties": [prop(main, value)],
        "properties": [prop("CRIT Rate", "4.8%", 20103)],
        "equip_suit": {
            "suit_id": 31000,
            "name": set_name,
            "own": 4,
            "desc1": "A two-piece description",
            "desc2": "A four-piece description",
        },
        "equipment_type": slot,
    }


DEEP_SEA_VISITOR = {
    "id": 14119,
    "level": 60,
    "name": "Deep Sea Visitor",
    "icon": "",
    "star": 1,
    "rarity": "S",
    "properties": [prop("CRIT Rate", "24%", 20103)],
    "main_properties": [prop("Base ATK", "713", 12101)],
    "talent_title": "Ocean Breeze",
    "talent_content": "A W-Engine effect description",
    "profession": 1,
}


def full_agent(
    base: dict,
    *,
    weapon: dict | None = None,
    properties: tuple[dict, ...] = (),
    discs: tuple[dict, ...] = (),
) -> dict:
    payload = {
        **base,
        "properties": list(properties),
        "equip": list(discs),
        "skills": [],
        "ranks": [],
        "skill_awaken": {
            "has_awaken_system": False,
            "awaken_level": 0,
            "awaken_max_level": 0,
            "skill_awaken_items": [],
        },
        "role_vertical_painting_url": "",
    }
    if weapon is not None:
        payload["weapon"] = weapon
    return payload


ELLEN = agent(1191, "Ellen", "Ellen Joe", rank=2)
NICOLE = agent(1031, "Nicole", "Nicole Demara", rarity="A", level=50, element=205, profession=4)
ELLEN_DETAIL = full_agent(
    ELLEN,
    weapon=DEEP_SEA_VISITOR,
    properties=(
        agent_stat("HP", "8000", 1),
        agent_stat("ATK", "3200", 2),
        agent_stat("CRIT Rate", "68.4%", 5),
    ),
    discs=(
        disc(1, "Polar Metal", "HP", "2200"),
        disc(2, "Polar Metal", "ATK", "316"),
        disc(3, "Woodpecker Electro", "DEF", "184"),
        disc(4, "Polar Metal", "CRIT Rate", "24%"),
        disc(5, "Polar Metal", "Ice DMG Bonus", "30%"),
        disc(6, "Woodpecker Electro", "ATK", "30%", level=12),
    ),
)
ELLEN_AGENT = ZzzAgent(
    ref=1191,
    name="Ellen",
    full_name="Ellen Joe",
    rarity="S",
    level=60,
    mindscape=2,
    element="Ice",
    specialty="Attack",
)


def test_an_agent_is_read_with_her_mindscape_cinema_element_and_specialty():
    assert read_agent(build(ZZZPartialAgent, ELLEN)) == ELLEN_AGENT


@pytest.mark.parametrize(
    ("element", "profession", "names"),
    [
        (203, 3, ("Electric", "Anomaly")),
        (205, 4, ("Ether", "Support")),
        (200, 6, ("Physical", "Rupture")),
        (201, 5, ("Fire", "Defense")),
    ],
)
def test_an_agents_element_and_specialty_read_as_the_game_names_them(element, profession, names):
    payload = agent(1031, "Nicole", "Nicole Demara", element=element, profession=profession)

    read = read_agent(build(ZZZPartialAgent, payload))

    assert (read.element, read.specialty) == names


def test_an_agents_detail_has_her_w_engine_final_stats_discs_and_the_sets_worn():
    detail = read_detail(build(ZZZFullAgent, ELLEN_DETAIL))

    assert detail == ZzzAgentDetail(
        agent=ELLEN_AGENT,
        w_engine=ZzzWEngine("Deep Sea Visitor", level=60, refinement=1, rarity="S"),
        stats=(("HP", "8000"), ("ATK", "3200"), ("CRIT Rate", "68.4%")),
        discs=(
            ZzzDisc(1, "Polar Metal", "HP", "2200", 15),
            ZzzDisc(2, "Polar Metal", "ATK", "316", 15),
            ZzzDisc(3, "Woodpecker Electro", "DEF", "184", 15),
            ZzzDisc(4, "Polar Metal", "CRIT Rate", "24%", 15),
            ZzzDisc(5, "Polar Metal", "Ice DMG Bonus", "30%", 15),
            ZzzDisc(6, "Woodpecker Electro", "ATK", "30%", 12),
        ),
        sets=(("Polar Metal", 4), ("Woodpecker Electro", 2)),
    )


def test_an_agent_without_a_w_engine_or_discs_has_none():
    detail = read_detail(build(ZZZFullAgent, full_agent(NICOLE)))

    assert detail.w_engine is None
    assert (detail.stats, detail.discs, detail.sets) == ((), (), ())
    assert (detail.agent.rarity, detail.agent.level, detail.agent.mindscape) == ("A", 50, 0)


def test_a_single_disc_of_a_set_is_no_set_worn():
    payload = full_agent(
        NICOLE,
        discs=(disc(1, "Swing Jazz", "HP", "2200"), disc(2, "Freedom Blues", "ATK", "316")),
    )

    assert read_detail(build(ZZZFullAgent, payload)).sets == ()


def test_an_agents_detail_keeps_no_disc_name_description_or_effect():
    text = repr(read_detail(build(ZZZFullAgent, ELLEN_DETAIL)))

    for kept_out in ("disc [", "description", "Ocean Breeze", "Victoria Housekeeping"):
        assert kept_out not in text


# ---- endgame ----------------------------------------------------------------------------------


def server_time(month: int, day: int, hour: int, minute: int = 0, second: int = 0) -> dict:
    # genshin.py adds the uid's server offset ("tzinfo") to every endgame time it returns.
    return {
        "year": 2026,
        "month": month,
        "day": day,
        "hour": hour,
        "minute": minute,
        "second": second,
        "tzinfo": 8,
    }


SHIYU_END = server_time(10, 31, 3, 59, 59)
SHIYU_END_AT = datetime(2026, 10, 31, 3, 59, 59, tzinfo=SERVER_TZ)
BUFF = {"title": "A buff", "text": "A buff description"}
SHIYU_AGENT = {
    "id": 1191,
    "level": 60,
    "rarity": "S",
    "element_type": 202,
    "role_square_url": "",
    "rank": 2,
}


def fifth_frontier_battle(layer_id: int, rating: str, score: int, clear_time: int | None) -> dict:
    battle = {
        "layer_id": layer_id,
        "rating": rating,
        "buffer": BUFF,
        "score": score,
        "max_score": 20000,
        "monster_pic": "",
        "buddy": None,
        "avatar_list": [SHIYU_AGENT],
    }
    if clear_time is not None:
        battle["battle_time"] = clear_time
    return battle


SHIYU_V2 = {
    "zone_id": 62041,
    "hadal_begin_time": server_time(10, 17, 4),
    "hadal_end_time": SHIYU_END,
    "pass_fifth_floor": True,
    "brief": {
        "score": 52000,
        "max_score": 60000,
        "rank_percent": 1234,
        "battle_time": 412,
        "rating": "S",
        "challenge_time": server_time(10, 18, 21),
    },
    "fourth_layer_detail": {
        "buffer": BUFF,
        "challenge_time": server_time(10, 18, 20),
        "rating": "S",
        "layer_challenge_info_list": [
            {"layer_id": 41, "battle_time": 95, "avatar_list": [SHIYU_AGENT], "buddy": None}
        ],
    },
    "fitfh_layer_detail": {  # genshin.py's (and HoYoLAB's) spelling
        "layer_challenge_info_list": [
            fifth_frontier_battle(51, "S", 20000, 120),
            fifth_frontier_battle(52, "A", 17000, 150),
            fifth_frontier_battle(53, "S", 15000, None),
        ]
    },
    # genshin.py copies these two into the model from the top of the answer.
    "nick_name": NICKNAME,
    "icon": AVATAR,
}
UNPLAYED_SHIYU_V2 = {
    **SHIYU_V2,
    "pass_fifth_floor": False,
    "brief": None,
    "fourth_layer_detail": None,
    "fitfh_layer_detail": None,
}


def node() -> dict:
    return {
        "avatars": [SHIYU_AGENT],
        "buddy": None,
        "element_type_list": [202],
        "monster_info": {
            "level": 70,
            "list": [
                {
                    "id": 1,
                    "name": "A monster",
                    "weak_element_type": 202,
                    "ice_weakness": 1,
                    "fire_weakness": 0,
                    "elec_weakness": 0,
                    "ether_weakness": -1,
                    "physics_weakness": 0,
                }
            ],
        },
        "battle_time": 60,
    }


def critical_node_frontier(index: int, rating: str) -> dict:
    return {
        "layer_index": index,
        "rating": rating,
        "layer_id": 100 + index,
        "buffs": [BUFF],
        "node_1": node(),
        "node_2": node(),
        "floor_challenge_time": server_time(10, 18, 20),
        "zone_name": "A zone name",
    }


SHIYU_V1 = {
    "schedule_id": 62041,
    "hadal_begin_time": server_time(10, 17, 4),
    "hadal_end_time": SHIYU_END,
    "has_data": True,
    "rating_list": [{"times": 4, "rating": "S"}, {"times": 1, "rating": "A"}],
    "all_floor_detail": [
        critical_node_frontier(5, "S"),
        critical_node_frontier(7, "A"),
        critical_node_frontier(6, "S"),
        critical_node_frontier(3, "S"),
        critical_node_frontier(4, "S"),
    ],
    "fast_layer_time": 95,
    "max_layer": 7,
}


def boss_challenge(boss: str, score: int, star: int) -> dict:
    return {
        "score": score,
        "star": star,
        "total_star": 3,
        "challenge_time": server_time(10, 22, 21),
        "boss": [{"icon": "", "name": boss, "bg_icon": "", "race_icon": ""}],
        "buffer": [{"name": "A buff", "desc": "A buff description", "icon": ""}],
        "avatar_list": [
            {
                "id": 1191,
                "level": 60,
                "element_type": 202,
                "avatar_profession": 1,
                "rarity": "S",
                "rank": 2,
                "role_square_url": "",
            }
        ],
        "buddy": None,
    }


DEADLY_ASSAULT_END_AT = datetime(2026, 11, 3, 3, 59, 59, tzinfo=SERVER_TZ)
DEADLY_ASSAULT = {
    "zone_id": 30014,
    "start_time": server_time(10, 20, 4),
    "end_time": server_time(11, 3, 3, 59, 59),
    "list": [
        boss_challenge("Notorious - Dead End Butcher", 22000, 3),
        boss_challenge("Notorious - Marionette", 18000, 2),
    ],
    "has_data": True,
    "total_score": 40000,
    "total_star": 5,
    "rank_percent": 523,
    "total_max_score": 60000,
    "room_max_score": 20000,
    "nick_name": NICKNAME,
    "avatar_icon": AVATAR,
}


def test_shiyu_defense_reads_the_runs_rating_score_and_clear_time_and_the_fifth_frontier():
    endgame = read_endgame("shiyu_defense", build(ShiyuDefenseV2, SHIYU_V2))

    assert endgame == Endgame(
        mode="shiyu_defense",
        season=None,  # genshin.py gives the season only as a zone id
        ends_at=SHIYU_END_AT,
        has_data=True,
        stars=None,
        deepest=None,
        difficulty=None,
        rating="S",
        score=52000,
        max_score=60000,
        seconds=412,
        battles=None,
        stages=(
            EndgameStage("Frontier 1", None, None, "S", 20000, 120),
            EndgameStage("Frontier 2", None, None, "A", 17000, 150),
            EndgameStage("Frontier 3", None, None, "S", 15000, None),
        ),
    )
    assert endgame.ends_at.utcoffset() == timedelta(hours=8)


def test_shiyu_defense_without_a_rating_says_so():
    payload = copy.deepcopy(SHIYU_V2)
    payload["brief"]["rating"] = ""

    assert read_endgame("shiyu_defense", build(ShiyuDefenseV2, payload)).rating is None


def test_shiyu_defense_not_played_this_season_has_no_data_and_no_stages():
    endgame = read_endgame("shiyu_defense", build(ShiyuDefenseV2, UNPLAYED_SHIYU_V2))

    assert (endgame.has_data, endgame.ends_at) == (False, SHIYU_END_AT)
    assert (endgame.rating, endgame.score, endgame.max_score, endgame.seconds) == (None,) * 4
    assert endgame.stages == ()


def test_the_critical_node_reads_the_deepest_frontier_the_fastest_clear_and_its_ratings():
    endgame = read_endgame("shiyu_defense", build(ShiyuDefenseV1, SHIYU_V1))

    assert endgame == Endgame(
        mode="shiyu_defense",
        season=None,
        ends_at=SHIYU_END_AT,
        has_data=True,
        stars=None,
        deepest="Frontier 7",
        difficulty=None,
        rating=None,
        score=None,
        max_score=None,
        seconds=95,
        battles=None,
        stages=(
            EndgameStage("Frontier 7", None, None, "A", None, None),
            EndgameStage("Frontier 6", None, None, "S", None, None),
            EndgameStage("Frontier 5", None, None, "S", None, None),
            EndgameStage("Frontier 4", None, None, "S", None, None),
        ),
    )


def test_the_critical_node_not_played_this_season_has_no_deepest_frontier_or_clear_time():
    payload = {
        **SHIYU_V1,
        "has_data": False,
        "rating_list": [],
        "all_floor_detail": [],
        "fast_layer_time": 0,
        "max_layer": 0,
    }

    endgame = read_endgame("shiyu_defense", build(ShiyuDefenseV1, payload))

    assert (endgame.has_data, endgame.deepest, endgame.seconds, endgame.stages) == (
        False,
        None,
        None,
        (),
    )
    assert endgame.ends_at == SHIYU_END_AT


def test_deadly_assault_reads_its_stars_score_and_each_boss():
    endgame = read_endgame("deadly_assault", build(DeadlyAssault, DEADLY_ASSAULT))

    assert endgame == Endgame(
        mode="deadly_assault",
        season=None,
        ends_at=DEADLY_ASSAULT_END_AT,
        has_data=True,
        stars=5,
        deepest=None,
        difficulty=None,
        rating=None,
        score=40000,
        max_score=60000,
        seconds=None,
        battles=None,
        stages=(
            # A boss's own total_star is unverified, so no stage claims a maximum.
            EndgameStage("Notorious - Dead End Butcher", 3, None, None, 22000, None),
            EndgameStage("Notorious - Marionette", 2, None, None, 18000, None),
        ),
    )


def test_deadly_assault_without_a_maximum_score_names_none():
    payload = {key: value for key, value in DEADLY_ASSAULT.items() if key != "total_max_score"}

    assert read_endgame("deadly_assault", build(DeadlyAssault, payload)).max_score is None


def test_deadly_assault_not_played_this_season_has_no_data_and_no_stages():
    payload = {
        **DEADLY_ASSAULT,
        "start_time": None,
        "end_time": None,
        "list": [],
        "has_data": False,
        "total_score": 0,
        "total_star": 0,
        "total_max_score": 0,
    }

    endgame = read_endgame("deadly_assault", build(DeadlyAssault, payload))

    assert (endgame.has_data, endgame.ends_at, endgame.max_score, endgame.stages) == (
        False,
        None,
        None,
        (),
    )


def test_a_season_end_without_the_servers_offset_is_unknown():
    # genshin.py leaves a season time naive when it cannot tell the uid's server.
    naive_end = {key: value for key, value in SHIYU_END.items() if key != "tzinfo"}
    shiyu = build(ShiyuDefenseV2, {**SHIYU_V2, "hadal_end_time": naive_end})
    assault = build(DeadlyAssault, {**DEADLY_ASSAULT, "end_time": naive_end})

    assert read_endgame("shiyu_defense", shiyu).ends_at is None
    assert read_endgame("deadly_assault", assault).ends_at is None


def test_the_players_nickname_and_avatar_never_reach_an_endgame():
    shiyu = build(ShiyuDefenseV2, SHIYU_V2)
    assault = build(DeadlyAssault, DEADLY_ASSAULT)
    assert (shiyu.player_nickname, assault.nickname) == (NICKNAME, NICKNAME)

    text = repr([read_endgame("shiyu_defense", shiyu), read_endgame("deadly_assault", assault)])

    for kept_out in (NICKNAME, AVATAR, "A buff", "A zone name", "62041", "30014"):
        assert kept_out not in text


# ---- calendar ---------------------------------------------------------------------------------

FETCHED_AT = datetime(2026, 10, 16, 5, 0, tzinfo=UTC)
NOW = int(FETCHED_AT.timestamp())
DAY = 86400


def days(count: int) -> datetime:
    return FETCHED_AT + timedelta(days=count)


def countdowns(start: int, end: int) -> dict:
    return {
        "start_ts": str(start),
        "end_ts": str(end),
        "left_start_ts": max(0, start - NOW),
        "left_end_ts": max(0, end - NOW),
    }


def zzz_event(
    name: str,
    start: int,
    end: int,
    *,
    state: str = "STATE_IN_PROGRESS",
    obtained: int = 0,
    total: int = 0,
) -> dict:
    return {
        "activity_id": 1001,
        "state": state,
        "name": name,
        "monochrome_got_cnt": obtained,
        "monochrome_cnt": total,
        **countdowns(start, end),
    }


EVENTS = [
    zzz_event("Ridu Rush", NOW - 3 * DAY, NOW + 4 * DAY, obtained=240, total=420),
    zzz_event(
        "Daily Visit",
        NOW - DAY,
        NOW + 10 * DAY,
        state="STATE_COMPLETED",
        obtained=300,
        total=300,
    ),
    zzz_event("Photo Week", NOW - DAY, NOW + 2 * DAY),
    zzz_event("Next Event", NOW + 5 * DAY, NOW + 19 * DAY, state="STATE_NOT_START", total=420),
    zzz_event(
        "An Event That Ended",
        NOW - 20 * DAY,
        NOW - DAY,
        state="STATE_COMPLETED",
        obtained=420,
        total=420,
    ),
    zzz_event("A New State", NOW - DAY, NOW + 3 * DAY, state="STATE_SOMETHING_NEW"),
]


def schedule(kind: str, start: int, end: int, *, version: str = "2.3") -> dict:
    return {
        "gacha_type": kind,
        "gacha_state": "GACHA_STATE_IN_PROGRESS",
        "version": version,
        **countdowns(start, end),
        "sup_lock_show": False,
        "insurance_id": 0,
    }


def banner_agent(name: str, rarity: str) -> dict:
    return {
        "avatar_id": 1000 + len(name),
        "avatar_name": name,
        "full_name": f"{name} Full Name",
        "rarity": rarity,
        "icon": "",
        "avatar_profession": 1,
        "avatar_element_type": 202,
        "avatar_sub_element_type": 0,
        "wiki_url": "https://wiki.invalid/agent",
        "jump_cultivate": False,
        "is_forward": True,
        "show_upon": True,
    }


def banner_w_engine(rarity: str) -> dict:
    return {
        "weapon_id": 14119,
        "rarity": rarity,
        "icon": "",
        "profession": 1,
        "talent_title": "Ocean Breeze",
        "talent_content": "A W-Engine effect description",
        "wiki_url": "https://wiki.invalid/w-engine",
        "show_upon": True,
    }


def agent_banner(kind: str, start: int, end: int, *agents: dict, version: str = "2.3") -> dict:
    return {**schedule(kind, start, end, version=version), "avatar_list": list(agents)}


def w_engine_banner(kind: str, start: int, end: int, *w_engines: dict) -> dict:
    return {**schedule(kind, start, end), "weapon_list": list(w_engines)}


GACHA = {
    "avatar_gacha_schedule_list": [
        agent_banner(
            "GACHA_TYPE_CHARACTER_UP",
            NOW - 26 * DAY,
            NOW - 5 * DAY,
            banner_agent("Jane", "S"),
            version="2.2",
        ),
        agent_banner(
            "GACHA_TYPE_CHARACTER_UP",
            NOW - 5 * DAY,
            NOW + 16 * DAY,
            banner_agent("Ellen", "S"),
            banner_agent("Anby", "A"),
            banner_agent("Soukaku", "A"),
        ),
        agent_banner(
            "GACHA_TYPE_CHARACTER_RETURN",
            NOW - 5 * DAY,
            NOW + 16 * DAY,
            banner_agent("Miyabi", "S"),
        ),
        agent_banner(
            "GACHA_TYPE_CHARACTER_UP",
            NOW + 16 * DAY,
            NOW + 37 * DAY,
            banner_agent("Yixuan", "S"),
            banner_agent("Pan Yinhu", "A"),
            version="2.4",
        ),
        # HoYoLAB's "no time" is 0.
        agent_banner("GACHA_TYPE_PERMANENT", 0, 0),
    ],
    "weapon_gacha_schedule_list": [
        w_engine_banner(
            "GACHA_TYPE_WEAPON_UP",
            NOW - 5 * DAY,
            NOW + 16 * DAY,
            banner_w_engine("S"),
            banner_w_engine("A"),
        ),
        w_engine_banner(
            "GACHA_TYPE_WEAPON_RETURN", NOW - 5 * DAY, NOW + 16 * DAY, banner_w_engine("S")
        ),
        w_engine_banner("GACHA_TYPE_SOMETHING_NEW", NOW - DAY, NOW + 6 * DAY),
    ],
}


def calendar(events: list[dict] = EVENTS, gacha: dict = GACHA):
    return read_calendar(
        [build(ZZZEvent, event) for event in events], build(ZZZGachaCalendar, gacha), FETCHED_AT
    )


def test_events_keep_their_start_the_owners_monochrome_progress_and_upcoming_ones():
    assert calendar().events == (
        GameEvent("Ridu Rush", days(-3), days(4), 240, 420, False),
        GameEvent("Daily Visit", days(-1), days(10), 300, 300, True),
        GameEvent("Photo Week", days(-1), days(2), None, None, False),
        GameEvent("Next Event", days(5), days(19), 0, 420, False),
        GameEvent("A New State", days(-1), days(3), None, None, False),
    )


def test_banners_split_their_agents_by_rank_and_keep_upcoming_ones():
    assert calendar().banners == (
        Banner("agent", None, ("Ellen",), ("Anby", "Soukaku"), days(-5), days(16)),
        Banner("exclusive_rescreening", None, ("Miyabi",), (), days(-5), days(16)),
        Banner("agent", None, ("Yixuan",), ("Pan Yinhu",), days(16), days(37)),
        Banner("permanent", None, (), (), None, None),
        # genshin.py names no W-Engine in this calendar.
        Banner("w_engine", None, (), (), days(-5), days(16)),
        Banner("reverberation", None, (), (), days(-5), days(16)),
        Banner("gacha_type_something_new", None, (), (), days(-1), days(6)),
    )


def test_the_calendar_names_the_version_of_its_first_banner_still_on():
    assert calendar().version == "2.3"
    ended = {
        "avatar_gacha_schedule_list": GACHA["avatar_gacha_schedule_list"][:1],
        "weapon_gacha_schedule_list": [],
    }
    assert calendar(gacha=ended).version is None


def test_the_zzz_calendar_has_no_endgame_windows():
    assert calendar().challenges == ()


def test_calendar_times_are_aware_utc():
    read = calendar()

    moments = [
        moment
        for item in (*read.banners, *read.events)
        for moment in (item.starts_at, item.ends_at)
        if moment is not None
    ]
    assert len(moments) == 22
    assert all(moment.utcoffset() == timedelta(0) for moment in moments)


def test_the_calendar_keeps_no_id_description_wiki_link_or_full_name():
    text = repr(calendar())

    for kept_out in ("wiki.invalid", "description", "Ocean Breeze", "Full Name", "1001"):
        assert kept_out not in text
