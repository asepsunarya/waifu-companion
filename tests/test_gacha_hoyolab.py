import asyncio
import copy
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import aiohttp
import genshin
import pydantic
import pytest
from genshin import errors
from genshin.models import (
    AnomalyArbitration,
    Character,
    GenshinDetailCharacters,
    GenshinEventCalendar,
    HardChallenge,
    HSREventCalendar,
    ImgTheater,
    Notes,
    SpiralAbyss,
    StarRailAPCShadow,
    StarRailChallenge,
    StarRailDetailCharacterResponse,
    StarRailNote,
    StarRailPureFiction,
    ZZZNotes,
)
from genshin.models.genshin import constants as genshin_constants
from genshin.models.genshin.daily import DailyReward, DailyRewardInfo
from genshin.models.hoyolab.record import GenshinAccount

from waifu.gacha import hoyolab
from waifu.gacha.codes import RedeemResult
from waifu.gacha.game_data import GAME_MODES, Endgame, GameCalendar
from waifu.gacha.games import GAMES
from waifu.gacha.hoyolab import (
    Account,
    CheckinResult,
    GachaError,
    GameStatus,
    HoyolabClient,
    WeeklyTask,
    preferred_uids,
)
from waifu.gacha.starrail import ENDGAME_MODES, HsrEndgame

COOKIE = "ltuid_v2=1; ltoken_v2=v2_SECRETCOOKIE; cookie_token_v2=SECRETTOKEN; account_id_v2=1"
UIDS = {"hsr": 800000001, "genshin": 800000002, "zzz": 1300000003}
CHRONICLE_PRIVATE = (
    "Data HSR ini tidak bisa dibaca karena Battle Chronicle di HoYoLAB tidak publik. "
    "Pengaturannya ada di HoYoLAB, di Battle Chronicle; Cyrene tidak mengubahnya."
)

HSR_NOTES = {
    "current_stamina": 232,
    "max_stamina": 300,
    "stamina_recover_time": 2880,
    "accepted_epedition_num": 2,
    "total_expedition_num": 4,
    "expeditions": [
        {"avatars": [], "status": "Finished", "remaining_time": 0, "name": "A", "item_url": ""},
        {"avatars": [], "status": "Ongoing", "remaining_time": 3600, "name": "B", "item_url": ""},
    ],
    "current_train_score": 300,
    "max_train_score": 500,
    "current_rogue_score": 12000,
    "max_rogue_score": 14000,
    "rogue_tourn_weekly_unlocked": True,
    "rogue_tourn_weekly_max": 2000,
    "rogue_tourn_weekly_cur": 2000,
    "weekly_cocoon_cnt": 1,
    "weekly_cocoon_limit": 3,
    "current_reserve_stamina": 0,
    "is_reserve_stamina_full": False,
}
GENSHIN_NOTES = {
    "current_resin": 160,
    "max_resin": 200,
    "resin_recovery_time": "19200",
    "current_home_coin": 0,
    "max_home_coin": 2400,
    "home_coin_recovery_time": "0",
    "finished_task_num": 4,
    "total_task_num": 4,
    "is_extra_task_reward_received": False,
    "remain_resin_discount_num": 2,
    "resin_discount_num_limit": 3,
    "transformer": {"obtained": False},
    "expeditions": [
        {"avatar_side_icon": "", "status": "Finished", "remained_time": "0"},
        {"avatar_side_icon": "", "status": "Finished", "remained_time": "0"},
    ],
    "max_expedition_num": 5,
    "archon_quest_progress": {
        "list": [],
        "is_finish_all_mainline": True,
        "is_open_archon_quest": True,
        "is_finish_all_interchapter": True,
    },
    "daily_task": {
        "total_num": 4,
        "finished_num": 4,
        "is_extra_task_reward_received": False,
        "task_rewards": [],
        "attendance_rewards": [],
        "attendance_visible": False,
        "stored_attendance": "0",
    },
}
ZZZ_NOTES = {
    "energy": {"progress": {"current": 240, "max": 240}, "restore": 0},
    "vitality": {"current": 400, "max": 400},
    "card_sign": "CardSignDone",
    "vhs_sale": {"sale_state": "SaleStateDoing"},
    "bounty_commission": {"num": 2, "total": 4, "refresh_time": 3600},
    "survey_points": {"num": 0, "total": 8000, "is_max_level": False},
    "weekly_task": {"cur_point": 700, "max_point": 1300, "refresh_time": 86400},
    "member_card": {"exp_time": "0", "is_open": False, "member_card_state": "MemberCardStateNo"},
    "temple_running": {
        "bench_state": "BenchStateCanProduce",
        "currency_next_refresh_ts": "0",
        "current_currency": 0,
        "expedition_state": "ExpeditionStateInCanSend",
        "level": 1,
        "shelve_state": "ShelveStateCanSell",
        "weekly_currency_max": 1000,
    },
}
HSR_CHARACTERS = {
    "avatar_list": [
        {
            "id": 1308,
            "element": "Lightning",
            "rarity": 5,
            "icon": "",
            "name": "Acheron",
            "level": 80,
            "rank": 2,
            "image": "",
            "equip": {
                "id": 23024,
                "level": 80,
                "rank": 1,
                "name": "Along the Passing Shore",
                "desc": "",
                "icon": "",
                "rarity": 5,
            },
            "relics": [],
            "ornaments": [],
            "ranks": [],
            "base_type": 5,
            "figure_path": "",
            "properties": [{"property_type": 1, "base": "1000", "add": "2512", "final": "3512"}],
            "skills": [],
            "servant_detail": None,
        }
    ],
    "equip_wiki": {},
    "relic_wiki": {},
    "property_info": {
        "1": {
            "property_type": 1,
            "name": "HP",
            "icon": "",
            "property_name_relic": "HP",
            "property_name_filter": "HP",
        }
    },
    "recommend_property": {
        "1308": {
            "recommend_relic_properties": [],
            "custom_relic_properties": [],
            "is_custom_property_valid": False,
        }
    },
    "relic_properties": [],
}
# genshin.py adds the uid's server offset ("tzinfo") to the endgame times it returns.
SEASON_END = {"year": 2026, "month": 11, "day": 17, "hour": 3, "minute": 59, "tzinfo": 8}
SEASON = {
    "schedule_id": 1012,
    "name_mi18n": "Gleaming Treasure",
    "status": "challenge_status_open",
    "begin_time": {**SEASON_END, "month": 10, "day": 6, "hour": 4, "minute": 0},
    "end_time": SEASON_END,
}
MEMORY_OF_CHAOS = {
    "schedule_id": 1012,
    "begin_time": None,
    "end_time": None,
    "star_num": 30,
    "max_floor": "Stage 12",
    "max_floor_id": 12,
    "battle_num": 12,
    "has_data": True,
    "all_floor_detail": [],
    "groups": [SEASON],
}
PURE_FICTION = {
    "star_num": 12,
    "max_floor": "Stage 4",
    "battle_num": 8,
    "has_data": True,
    "all_floor_detail": [],
    "groups": [SEASON],
    "max_floor_id": 4,
}
APOCALYPTIC_SHADOW = {**PURE_FICTION, "star_num": 9, "battle_num": 6}
ANOMALY_ARBITRATION = {
    "challenge_peak_records": [],
    "challenge_peak_best_record_brief": None,
    "role": {
        "server": "prod_official_asia",
        "nickname": "Master",
        "level": 70,
        "role_id": "800000001",
    },
}
CALENDAR_NOW = 1_800_000_000  # the calendar's own clock, as unix seconds
HSR_CALENDAR = {
    "avatar_card_pool_list": [
        {
            "time_info": {
                "start_ts": str(CALENDAR_NOW - 86400),
                "end_ts": str(CALENDAR_NOW + 5 * 86400),
                "start_time": {"year": 2027},
                "now": str(CALENDAR_NOW),
            },
            "name": "Butterfly on Swordtip",
            "type": "CardPoolRole",
            "avatar_list": [],
            "equip_list": [],
            "is_after_version": False,
            "version": "3.7",
            "id": 1,
        }
    ],
    "equip_card_pool_list": [],
    "act_list": [],
    "challenge_list": [],
    "cur_game_version": "3.7",
    "now": str(CALENDAR_NOW),
}
FETCHED_AT = datetime(2026, 10, 15, 13, 0, tzinfo=UTC)
GENSHIN_ICON = "https://example.invalid/UI_AvatarIcon_Icon.png"
FURINA_ID = 10000089


def unix(moment: datetime) -> int:
    return int(moment.timestamp())


def server_unix(moment: datetime) -> dict:
    # genshin.py wraps a unix time this way when it knows the uid's server offset.
    return {"timestamp": str(unix(moment)), "tzinfo": 8}


def wall_clock(moment: datetime) -> dict:
    """The wall clock of `moment` in server time (UTC+8), as HoYoLAB writes one."""
    server_time = moment + timedelta(hours=8)
    return {key: getattr(server_time, key) for key in ("year", "month", "day", "hour", "minute")}


def genshin_weapon(name: str, refinement: int) -> dict:
    return {
        "id": 11513,
        "icon": GENSHIN_ICON,
        "type": 1,
        "rarity": 5,
        "level": 90,
        "affix_level": refinement,
        "name": name,
    }


def genshin_character(
    char_id: int, name: str, element: str, constellation: int, weapon: dict
) -> dict:
    return {
        "id": char_id,
        "icon": GENSHIN_ICON,
        "name": name,
        "element": element,
        "fetter": 10,
        "level": 90,
        "rarity": 5,
        "actived_constellation_num": constellation,
        "image": GENSHIN_ICON,
        "is_chosen": False,
        "side_icon": GENSHIN_ICON,
        "weapon_type": 1,
        "weapon": weapon,
    }


FURINA = genshin_character(
    FURINA_ID, "Furina", "Hydro", 2, genshin_weapon("Splendor of Tranquil Waters", 1)
)
GENSHIN_CHARACTERS = [
    FURINA,
    genshin_character(10000047, "Kazuha", "Anemo", 0, genshin_weapon("Freedom-Sworn", 5)),
]
GENSHIN_DETAILS = {
    "list": [
        {
            "base": FURINA,
            "weapon": {
                **genshin_weapon("Splendor of Tranquil Waters", 1),
                "desc": "",
                "main_property": {"property_type": 4, "base": "542", "add": "0", "final": "542"},
                "sub_property": None,
                "promote_level": 6,
            },
            "relics": [],
            "constellations": [],
            "costumes": [],
            "skills": [],
            "selected_properties": [
                {"property_type": 2000, "base": "15307", "add": "20000", "final": "35307"}
            ],
            "base_properties": [],
            "extra_properties": [],
            "element_properties": [],
        }
    ],
    "property_map": {
        str(kind): {"property_type": kind, "name": name, "icon": "", "filter_name": name}
        for kind, name in ((4, "Base ATK"), (2000, "Max HP"))
    },
    "relic_property_options": {},
    "relic_wiki": {},
    "weapon_wiki": {},
    "avatar_wiki": {},
}
SPIRAL_ABYSS = {
    "schedule_id": 92,
    "start_time": server_unix(FETCHED_AT - timedelta(days=29)),
    "end_time": server_unix(FETCHED_AT + timedelta(days=1)),
    "total_battle_times": 12,
    "total_win_times": 12,
    "max_floor": "12-3",
    "total_star": 36,
    "is_unlock": True,
    # Without battles: their avatars need genshin.py's character-name list to parse.
    "floors": [{"index": 12, "is_unlock": True, "star": 9, "max_star": 9, "levels": []}],
}


def theater_season(schedule_id: int, start: datetime, end: datetime, best_act: int) -> dict:
    return {
        "detail": None,
        "stat": {
            "difficulty_id": 4,
            "max_round_id": best_act,
            "heraldry": 0,
            "get_medal_round_list": [],
            "medal_num": best_act - 1,
            "coin_num": 20,
            "avatar_bonus_num": 0,
            "rent_cnt": 0,
        },
        "schedule": {
            "start_time": unix(start),
            "end_time": unix(end),
            "schedule_type": 1,
            "schedule_id": schedule_id,
            "start_date_time": {**wall_clock(start), "tzinfo": 8},
            "end_date_time": {**wall_clock(end), "tzinfo": 8},
        },
        "has_data": True,
        "has_detail_data": False,
    }


OCTOBER_START = datetime(2026, 9, 30, 20, 0, tzinfo=UTC)  # 1 October, 04:00 server time
IMAGINARIUM_THEATER = {
    "data": [
        theater_season(16, OCTOBER_START - timedelta(days=30), OCTOBER_START, 8),
        theater_season(17, OCTOBER_START, OCTOBER_START + timedelta(days=31), 10),
    ],
    "is_unlock": True,
}
STYGIAN_ONSLAUGHT = [
    {
        "schedule": {
            "schedule_id": 7,
            "name": "Season of Tides",
            "start_time": server_unix(FETCHED_AT - timedelta(days=9)),
            "end_time": server_unix(FETCHED_AT + timedelta(days=18)),
            "is_valid": True,
        },
        "single": {
            "best": {"difficulty": 5, "second": 145, "icon": "UI_Badge_5"},
            "challenge": [],
            "has_data": True,
        },
        "mp": {"best": None, "challenge": [], "has_data": False},
    }
]


def calendar_entry(start: datetime, end: datetime) -> dict:
    # The naive wall clocks beside the unix times, which genshin.py gives no time zone.
    return {
        "start_timestamp": unix(start),
        "end_timestamp": unix(end),
        "start_time": wall_clock(start),
        "end_time": wall_clock(end),
        "countdown_seconds": 0,
    }


def calendar_event(name: str, start: datetime, end: datetime, **details) -> dict:
    return {
        **calendar_entry(start, end),
        "id": 1,
        "name": name,
        "desc": "",
        "strategy": "",
        "type": "ActTypeOther",
        "status": 2,
        "reward_list": [],
        "is_finished": False,
        **details,
    }


GENSHIN_CALENDAR = {
    "avatar_card_pool_list": [
        {
            **calendar_entry(FETCHED_AT - timedelta(days=1), FETCHED_AT + timedelta(days=5)),
            "pool_id": 1,
            "version_name": "6.1",
            "pool_name": "Ballad of the Fontaine Stage",
            "pool_type": 1,
            "avatars": [
                {"id": FURINA_ID, "icon": "", "name": "Furina", "element": "Hydro", "rarity": 5}
            ],
            "weapon": [],
            "jump_url": "",
            "pool_status": 1,
        }
    ],
    "weapon_card_pool_list": [],
    "mixed_card_pool_list": [],
    "act_list": [
        calendar_event(
            "Lantern Rite", FETCHED_AT - timedelta(days=2), FETCHED_AT + timedelta(days=9)
        )
    ],
    "fixed_act_list": [
        calendar_event(
            "Spiral Abyss",
            FETCHED_AT - timedelta(days=29),
            FETCHED_AT + timedelta(days=1),
            tower_detail={"is_unlock": True, "max_star": 36, "total_star": 36, "has_data": True},
        )
    ],
}


def account(game_biz: str, uid: int, level: int) -> GenshinAccount:
    return GenshinAccount(
        game_biz=game_biz,
        game_uid=str(uid),
        level=level,
        nickname="Master",
        region="asia",
        region_name="Asia",
    )


ACCOUNTS = [
    account("hkrpg_global", 800000009, 30),
    account("hkrpg_global", 800000001, 70),
    account("hk4e_global", 800000002, 60),
    account("nap_global", 1300000003, 55),
    account("bh3_global", 12345678, 80),  # Honkai Impact 3rd is not one of her games
]


class FakeHoyolab:
    """genshin.Client's async surface for these calls, answering with real genshin.py models."""

    def __init__(
        self, *, failures=None, answers=None, accounts=ACCOUNTS, delay_s=0.0, redemptions=None
    ):
        self.failures = failures or {}
        self.answers = answers or {}
        self.accounts = accounts
        self.delay_s = delay_s
        # Per code, what HoYoLAB refuses its attempts with, in turn; once used up it is redeemed.
        self.redemptions = redemptions or {}
        self.calls: list[tuple] = []
        self.in_flight = 0
        self.most_in_flight = 0

    async def _answer(self, call: tuple, value):
        self.calls.append(call)
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
        finally:
            self.in_flight -= 1
        failure = self.failures.get(call[0])
        if failure is not None:
            raise failure
        return self.answers.get(call[0], value)

    async def get_game_accounts(self):
        return await self._answer(("accounts",), self.accounts)

    # genshin.py's validators rewrite the payload they are given, so each call gets a copy.
    async def get_starrail_notes(self, uid, *, autoauth=True):
        notes = StarRailNote(**copy.deepcopy(HSR_NOTES))
        return await self._answer(("hsr_notes", uid, autoauth), notes)

    async def get_genshin_notes(self, uid, *, autoauth=True):
        notes = Notes(**copy.deepcopy(GENSHIN_NOTES))
        return await self._answer(("genshin_notes", uid, autoauth), notes)

    async def get_zzz_notes(self, uid, *, autoauth=True):
        notes = ZZZNotes(**copy.deepcopy(ZZZ_NOTES))
        return await self._answer(("zzz_notes", uid, autoauth), notes)

    async def get_reward_info(self, *, game):
        return await self._answer(("reward_info", game), DailyRewardInfo(True, 11))

    async def claim_daily_reward(self, *, game):
        reward = DailyReward(name="Stellar Jade", cnt=20, icon="")
        return await self._answer(("claim", game), reward)

    async def fetch_authkey(self, uid, *, game):
        return await self._answer(("authkey", uid, game), "fresh-authkey")

    # The HSR Battle Chronicle, with genshin.py's signatures, recording every argument.
    async def get_starrail_characters(self, uid=None, *, lang=None, simple=False):
        response = StarRailDetailCharacterResponse(**copy.deepcopy(HSR_CHARACTERS))
        return await self._answer(("characters", uid, {"lang": lang, "simple": simple}), response)

    async def get_starrail_challenge(self, uid=None, *, previous=False, lang=None, raw=False):
        model = StarRailChallenge(**copy.deepcopy(MEMORY_OF_CHAOS))
        arguments = {"previous": previous, "lang": lang, "raw": raw}
        return await self._answer(("memory_of_chaos", uid, arguments), model)

    async def get_starrail_pure_fiction(self, uid=None, *, previous=False, lang=None, raw=False):
        model = StarRailPureFiction(**copy.deepcopy(PURE_FICTION))
        arguments = {"previous": previous, "lang": lang, "raw": raw}
        return await self._answer(("pure_fiction", uid, arguments), model)

    async def get_starrail_apc_shadow(self, uid=None, *, previous=False, lang=None, raw=False):
        model = StarRailAPCShadow(**copy.deepcopy(APOCALYPTIC_SHADOW))
        arguments = {"previous": previous, "lang": lang, "raw": raw}
        return await self._answer(("apocalyptic_shadow", uid, arguments), model)

    async def get_anomaly_arbitration(
        self, uid=None, *, previous=False, lang=None, raw=False, schedule_type=3
    ):
        model = AnomalyArbitration(**copy.deepcopy(ANOMALY_ARBITRATION))
        arguments = {"previous": previous, "lang": lang, "raw": raw, "schedule_type": schedule_type}
        return await self._answer(("anomaly_arbitration", uid, arguments), model)

    async def get_starrail_event_calendar(self, uid=None, *, lang=None):
        model = HSREventCalendar(**copy.deepcopy(HSR_CALENDAR))
        return await self._answer(("calendar", uid, {"lang": lang}), model)

    # The Genshin Battle Chronicle, with genshin.py's signatures, recording every argument.
    async def get_genshin_characters(self, uid=None, *, lang=None):
        characters = [Character(**copy.deepcopy(item)) for item in GENSHIN_CHARACTERS]
        return await self._answer(("genshin_characters", uid, {"lang": lang}), characters)

    async def get_genshin_detailed_characters(
        self, uid=None, *, characters=None, lang=None, return_raw_data=False
    ):
        response = GenshinDetailCharacters(**copy.deepcopy(GENSHIN_DETAILS))
        arguments = {"characters": characters, "lang": lang, "return_raw_data": return_raw_data}
        return await self._answer(("genshin_details", uid, arguments), response)

    async def get_genshin_spiral_abyss(self, uid=None, *, previous=False, lang=None, raw=False):
        model = SpiralAbyss(**copy.deepcopy(SPIRAL_ABYSS))
        arguments = {"previous": previous, "lang": lang, "raw": raw}
        return await self._answer(("spiral_abyss", uid, arguments), model)

    async def get_imaginarium_theater(
        self, uid=None, *, previous=False, need_detail=True, lang=None, raw=False
    ):
        model = ImgTheater(**copy.deepcopy(IMAGINARIUM_THEATER))
        arguments = {"previous": previous, "need_detail": need_detail, "lang": lang, "raw": raw}
        return await self._answer(("imaginarium_theater", uid, arguments), model)

    async def get_stygian_onslaught(self, uid=None, *, lang=None, raw=False, need_detail=True):
        seasons = [HardChallenge(**copy.deepcopy(season)) for season in STYGIAN_ONSLAUGHT]
        arguments = {"lang": lang, "raw": raw, "need_detail": need_detail}
        return await self._answer(("stygian_onslaught", uid, arguments), seasons)

    async def get_genshin_event_calendar(self, uid=None, *, lang=None):
        model = GenshinEventCalendar(**copy.deepcopy(GENSHIN_CALENDAR))
        return await self._answer(("genshin_calendar", uid, {"lang": lang}), model)

    async def redeem_code(self, code, uid=None, *, game=None, lang=None, region=None):
        await self._answer(("redeem", code, uid, game), None)
        refusals = self.redemptions.get(code)
        if refusals:
            raise refusals.pop(0)

    async def update_settings(self, *args, **kwargs):
        return await self._answer(("update_settings",), None)

    # The ZZZ Battle Chronicle, with genshin.py's signatures, recording every argument; its
    # payloads are with the ZZZ data tests at the end of this file.
    async def get_zzz_agents(self, uid=None, *, lang=None):
        agents = [genshin.models.ZZZPartialAgent(**copy.deepcopy(agent)) for agent in ZZZ_AGENTS]
        return await self._answer(("zzz_agents", uid, {"lang": lang}), agents)

    async def get_zzz_agent_info(self, character_id, *, uid=None, lang=None):
        payload = ZZZ_AGENT_DETAILS.get(character_id)
        agent = None if payload is None else genshin.models.ZZZFullAgent(**copy.deepcopy(payload))
        answer = await self._answer(("zzz_agent_info", character_id, uid, {"lang": lang}), agent)
        if answer is None:  # HoYoLAB lists no agent the account lacks: genshin.py's [0] fails
            raise IndexError("list index out of range")
        return answer

    async def get_shiyu_defense(self, uid=None, *, previous=False, lang=None, raw=False):
        model = genshin.models.ShiyuDefenseV2(**copy.deepcopy(SHIYU_DEFENSE))
        arguments = {"previous": previous, "lang": lang, "raw": raw}
        return await self._answer(("shiyu_defense", uid, arguments), model)

    async def get_deadly_assault(self, uid=None, *, previous=False, lang=None, raw=False):
        model = genshin.models.DeadlyAssault(**copy.deepcopy(DEADLY_ASSAULT))
        arguments = {"previous": previous, "lang": lang, "raw": raw}
        return await self._answer(("deadly_assault", uid, arguments), model)

    async def get_zzz_event_calendar(self, uid=None, *, lang=None):
        events = [genshin.models.ZZZEvent(**copy.deepcopy(event)) for event in ZZZ_EVENTS]
        return await self._answer(("zzz_events", uid, {"lang": lang}), events)

    async def get_zzz_gacha_calendar(self, uid=None, *, lang=None):
        model = genshin.models.ZZZGachaCalendar(**copy.deepcopy(ZZZ_GACHA_CALENDAR))
        return await self._answer(("zzz_gacha", uid, {"lang": lang}), model)


def make_client(fake: FakeHoyolab, *, cookie=COOKIE, uids=UIDS, **kwargs) -> HoyolabClient:
    cookies: list[str] = []

    def factory(given: str):
        cookies.append(given)
        return fake

    async def sleep(seconds: float) -> None:
        fake.calls.append(("sleep", seconds))  # recorded in order with the requests, never waited

    client = HoyolabClient(cookie, uids=uids, client_factory=factory, sleep=sleep, **kwargs)
    fake.cookies = cookies
    return client


def test_status_maps_the_notes_of_each_game():
    statuses = make_client(FakeHoyolab()).status(["hsr", "genshin", "zzz"])

    assert statuses["hsr"] == GameStatus(
        game="hsr",
        stamina=232,
        stamina_max=300,
        stamina_full_in_s=2880,
        daily_current=300,
        daily_max=500,
        daily_done=False,
        weekly=(WeeklyTask("Echo of War", 2, 3), WeeklyTask("Simulated Universe", 12000, 14000)),
        expeditions_total=2,
        expeditions_finished=1,
        checked_in=True,
    )
    genshin_status = statuses["genshin"]
    assert (genshin_status.stamina, genshin_status.stamina_full_in_s) == (160, 19200)
    # All four commissions done, but the bonus from Katheryne is not claimed yet.
    assert (genshin_status.daily_current, genshin_status.daily_max) == (4, 4)
    assert genshin_status.daily_done is False
    assert genshin_status.weekly == (WeeklyTask("Weekly Bosses", 1, 3),)
    assert (genshin_status.expeditions_total, genshin_status.expeditions_finished) == (2, 2)
    zzz = statuses["zzz"]
    assert (zzz.stamina, zzz.stamina_max, zzz.stamina_full_in_s) == (240, 240, 0)
    assert (zzz.daily_current, zzz.daily_max, zzz.daily_done) == (400, 400, True)
    assert zzz.weekly == (WeeklyTask("Ridu Weekly", 700, 1300),)
    assert (zzz.expeditions_total, zzz.expeditions_finished) == (0, 0)


def test_notes_are_read_for_the_configured_uids_without_touching_hoyolab_settings():
    fake = FakeHoyolab()
    make_client(fake).status(["hsr", "genshin", "zzz"])

    notes_calls = sorted(call for call in fake.calls if call[0].endswith("_notes"))
    assert notes_calls == [
        ("genshin_notes", 800000002, False),
        ("hsr_notes", 800000001, False),
        ("zzz_notes", 1300000003, False),
    ]
    assert fake.cookies == [COOKIE]


def test_the_checkin_state_is_asked_for_each_game_by_its_genshin_game():
    fake = FakeHoyolab()
    make_client(fake).status(["hsr", "zzz"])

    games = [call[1] for call in fake.calls if call[0] == "reward_info"]
    assert sorted(games) == sorted([genshin.Game.STARRAIL, genshin.Game.ZZZ])


def test_without_configured_uids_the_highest_level_account_is_used_and_looked_up_once():
    fake = FakeHoyolab()
    client = make_client(fake, uids={})

    client.status(["hsr", "genshin"])
    client.status(["hsr"])

    assert [call for call in fake.calls if call[0] == "accounts"] == [("accounts",)]
    assert ("hsr_notes", 800000001, False) in fake.calls  # level 70, not the level-30 account


def test_accounts_lists_only_her_three_games():
    accounts = make_client(FakeHoyolab()).accounts()

    assert [(a.game, a.uid, a.level) for a in accounts] == [
        ("hsr", 800000009, 30),
        ("hsr", 800000001, 70),
        ("genshin", 800000002, 60),
        ("zzz", 1300000003, 55),
    ]
    assert accounts[0] == Account("hsr", 800000009, "Master", 30, "Asia")


def test_a_game_without_an_account_is_an_account_error():
    fake = FakeHoyolab(accounts=[account("hkrpg_global", 800000001, 70)])

    statuses = make_client(fake, uids={}).status(["zzz"])

    assert statuses["zzz"].kind == "account"
    assert "ZZZ" in statuses["zzz"].message


def test_one_failing_game_does_not_hide_the_others():
    fake = FakeHoyolab(failures={"zzz_notes": errors.InvalidCookies()})

    statuses = make_client(fake).status(["hsr", "zzz"])

    assert isinstance(statuses["hsr"], GameStatus)
    assert isinstance(statuses["zzz"], GachaError)


def test_checked_in_is_unknown_when_only_the_checkin_state_fails():
    fake = FakeHoyolab(failures={"reward_info": errors.GenshinException({"retcode": -1})})

    assert make_client(fake).status(["hsr"])["hsr"].checked_in is None


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (errors.InvalidCookies(), "cookie"),
        (errors.GenshinException({"retcode": 10102, "message": "Data is not public"}), "notes_off"),
        (errors.DataNotPublic({"retcode": 10102}), "notes_off"),
        (errors.GeetestError({"retcode": 1034}), "captcha"),
        (errors.AccountNotFound(), "account"),
        (errors.TooManyRequests(), "rate_limit"),
        (errors.VisitsTooFrequently(), "rate_limit"),
        (aiohttp.ClientConnectionError("down"), "network"),
        (OSError("unreachable"), "network"),
        (errors.GenshinException({"retcode": -999, "message": "odd"}), "other"),
    ],
)
def test_hoyolab_failures_become_gacha_errors_the_owner_can_act_on(failure, kind):
    fake = FakeHoyolab(failures={"hsr_notes": failure})

    error = make_client(fake).status(["hsr"])["hsr"]

    assert isinstance(error, GachaError)
    assert error.kind == kind
    assert error.message  # an Indonesian sentence for the owner


def test_notes_turned_off_explains_where_to_turn_them_on():
    fake = FakeHoyolab(failures={"genshin_notes": errors.GenshinException({"retcode": 10102})})

    error = make_client(fake).status(["genshin"])["genshin"]

    assert "Real-Time Notes" in error.message
    assert "Genshin" in error.message


def test_a_slow_hoyolab_is_a_network_error():
    fake = FakeHoyolab(delay_s=1.0)

    error = make_client(fake, timeout_s=0.05).status(["hsr"])["hsr"]

    assert error.kind == "network"


def test_without_a_cookie_every_call_says_to_fill_it_in():
    client = HoyolabClient(None, client_factory=lambda cookie: FakeHoyolab())

    assert client.status(["hsr"])["hsr"].kind == "no_cookie"
    for call in (client.accounts, lambda: client.checkin("hsr"), lambda: client.authkey("hsr")):
        with pytest.raises(GachaError) as caught:
            call()
        assert caught.value.kind == "no_cookie"
        assert "gacha.cookie" in caught.value.message


def test_checkin_claims_the_reward_of_the_game():
    fake = FakeHoyolab()

    result = make_client(fake).checkin("hsr")

    assert result == CheckinResult("hsr", claimed=True, reward="20 Stellar Jade")
    assert ("claim", genshin.Game.STARRAIL) in fake.calls


def test_checkin_already_claimed_today_is_not_an_error():
    fake = FakeHoyolab(failures={"claim": errors.AlreadyClaimed()})

    assert make_client(fake).checkin("genshin") == CheckinResult("genshin", False, None)


def test_checkin_that_needs_a_captcha_says_to_check_in_by_hand():
    failure = errors.DailyGeetestTriggered({"retcode": 0}, gt="g", challenge="c")
    fake = FakeHoyolab(failures={"claim": failure})

    with pytest.raises(GachaError) as caught:
        make_client(fake).checkin("zzz")

    assert caught.value.kind == "captcha"


def test_authkey_is_made_from_the_cookie_token_for_the_game_uid():
    fake = FakeHoyolab()

    assert make_client(fake).authkey("zzz") == "fresh-authkey"
    assert ("authkey", 1300000003, genshin.Game.ZZZ) in fake.calls


def test_authkey_without_a_cookie_token_is_refused_before_asking_hoyolab():
    fake = FakeHoyolab()
    client = make_client(fake, cookie="ltuid_v2=1; ltoken_v2=x")

    with pytest.raises(GachaError) as caught:
        client.authkey("hsr")

    assert caught.value.kind == "authkey"
    assert fake.calls == []


def test_the_cookie_never_shows_up_in_errors_or_logs(caplog):
    fake = FakeHoyolab(failures={"hsr_notes": errors.GenshinException({"retcode": -999})})

    with caplog.at_level(logging.DEBUG):
        error = make_client(fake).status(["hsr"])["hsr"]

    for secret in ("SECRETCOOKIE", "SECRETTOKEN"):
        assert secret not in error.message
        assert secret not in str(error)
        assert secret not in caplog.text


def test_the_first_of_the_highest_level_accounts_is_the_one_used():
    accounts = [
        Account("hsr", 800000001, "Main", 70, "Asia"),
        Account("hsr", 800000002, "Alt", 70, "Asia"),
        Account("zzz", 1300000003, "Main", 10, "Asia"),
    ]

    assert preferred_uids(accounts) == {"hsr": 800000001, "zzz": 1300000003}


# ---- HSR data (v1.16) ---------------------------------------------------------------------------

ENDGAME_CALLS = {
    "memory_of_chaos": (800000001, {"previous": False, "lang": None, "raw": False}),
    "pure_fiction": (800000001, {"previous": False, "lang": None, "raw": False}),
    "apocalyptic_shadow": (800000001, {"previous": False, "lang": None, "raw": False}),
    "anomaly_arbitration": (
        800000001,
        {"previous": False, "lang": None, "raw": False, "schedule_type": 1},
    ),
}


def new_path_error() -> pydantic.ValidationError:
    """What genshin.py raises for a character of a Path it does not know yet."""
    payload = copy.deepcopy(HSR_CHARACTERS)
    payload["avatar_list"][0]["base_type"] = 99
    try:
        StarRailDetailCharacterResponse(**payload)
    except pydantic.ValidationError as error:
        return error
    raise AssertionError("genshin.py accepted an unknown Path")


def test_hsr_characters_reads_the_detailed_roster_of_the_hsr_uid():
    fake = FakeHoyolab()

    characters = make_client(fake).hsr_characters()

    assert [character.name for character in characters] == ["Acheron"]
    assert (characters[0].eidolon, characters[0].light_cone.superimposition) == (2, 1)
    assert fake.calls == [("characters", 800000001, {"lang": None, "simple": False})]


def test_hsr_endgame_reads_every_mode_asked_for_this_season_of_the_hsr_uid():
    fake = FakeHoyolab()

    results = make_client(fake).hsr_endgame(ENDGAME_MODES)

    assert list(results) == list(ENDGAME_MODES)
    assert all(isinstance(result, HsrEndgame) for result in results.values())
    assert (results["memory_of_chaos"].stars, results["apocalyptic_shadow"].stars) == (30, 9)
    assert {call[0]: call[1:] for call in fake.calls} == ENDGAME_CALLS


def test_hsr_endgame_reads_only_the_modes_asked_for():
    fake = FakeHoyolab()

    results = make_client(fake).hsr_endgame(["pure_fiction"])

    assert list(results) == ["pure_fiction"]
    assert [call[0] for call in fake.calls] == ["pure_fiction"]


def test_at_most_two_endgame_requests_are_in_flight_at_once():
    fake = FakeHoyolab(delay_s=0.05)

    results = make_client(fake).hsr_endgame(ENDGAME_MODES)

    assert all(isinstance(result, HsrEndgame) for result in results.values())
    assert fake.most_in_flight == 2


def test_one_failing_endgame_mode_does_not_hide_the_others():
    failures = {
        "pure_fiction": errors.DataNotPublic({"retcode": 10102}),
        "apocalyptic_shadow": errors.GenshinException({"retcode": -999}),
    }

    results = make_client(FakeHoyolab(failures=failures)).hsr_endgame(ENDGAME_MODES)

    assert isinstance(results["memory_of_chaos"], HsrEndgame)
    assert isinstance(results["anomaly_arbitration"], HsrEndgame)
    assert results["pure_fiction"].kind == "chronicle_private"
    assert results["apocalyptic_shadow"].kind == "other"


def test_a_failure_of_the_whole_endgame_call_gives_every_mode_that_error():
    fake = FakeHoyolab(failures={"accounts": errors.InvalidCookies()})

    results = make_client(fake, uids={}).hsr_endgame(ENDGAME_MODES)

    assert list(results) == list(ENDGAME_MODES)
    assert {result.kind for result in results.values()} == {"cookie"}


def test_a_slow_hoyolab_fails_every_endgame_mode_as_network():
    fake = FakeHoyolab(delay_s=1.0)

    results = make_client(fake, timeout_s=0.05).hsr_endgame(ENDGAME_MODES)

    assert {result.kind for result in results.values()} == {"network"}


def test_hsr_calendar_anchors_its_times_on_the_fetch_time():
    fake = FakeHoyolab()

    calendar = make_client(fake, now=lambda: FETCHED_AT).hsr_calendar()

    assert calendar.version == "3.7"
    assert calendar.warps[0].starts_at == FETCHED_AT - timedelta(days=1)
    assert calendar.warps[0].ends_at == FETCHED_AT + timedelta(days=5)
    assert fake.calls == [("calendar", 800000001, {"lang": None})]


def test_the_calendar_fetch_time_is_read_before_the_request():
    fake = FakeHoyolab()

    def now() -> datetime:
        fake.calls.append(("now",))
        return FETCHED_AT

    make_client(fake, now=now).hsr_calendar()

    assert [call[0] for call in fake.calls] == ["now", "calendar"]


def test_the_calendar_fetch_time_defaults_to_the_aware_utc_now():
    # A calendar whose clock agrees with the PC's: only the right anchor gives its end back.
    api_now = int(datetime.now(UTC).timestamp())
    payload = copy.deepcopy(HSR_CALENDAR)
    payload["now"] = str(api_now)
    payload["avatar_card_pool_list"][0]["time_info"].update(
        start_ts=str(api_now - 86400), end_ts=str(api_now + 5 * 86400), now=str(api_now)
    )
    fake = FakeHoyolab(answers={"calendar": HSREventCalendar(**payload)})

    ends_at = make_client(fake).hsr_calendar().warps[0].ends_at

    assert ends_at.utcoffset() == timedelta(0)
    assert ends_at == datetime.fromtimestamp(api_now, UTC) + timedelta(days=5)


def test_hidden_battle_chronicle_data_is_chronicle_private_and_says_where_the_setting_is():
    hidden = errors.DataNotPublic({"retcode": 10102})
    names = ("characters", "calendar", "memory_of_chaos", "hsr_notes")
    client = make_client(FakeHoyolab(failures=dict.fromkeys(names, hidden)))

    for call in (client.hsr_characters, client.hsr_calendar):
        with pytest.raises(GachaError) as caught:
            call()
        assert (caught.value.kind, caught.value.message) == ("chronicle_private", CHRONICLE_PRIVATE)
    assert client.hsr_endgame(["memory_of_chaos"])["memory_of_chaos"].kind == "chronicle_private"
    # The Real-Time Notes keep their own kind: they are switched on elsewhere.
    assert client.status(["hsr"])["hsr"].kind == "notes_off"


def test_hsr_data_without_an_hsr_account_is_an_account_error():
    fake = FakeHoyolab(accounts=[account("hk4e_global", 800000002, 60)])
    client = make_client(fake, uids={})

    for call in (client.hsr_characters, client.hsr_calendar):
        with pytest.raises(GachaError) as caught:
            call()
        assert caught.value.kind == "account"
        assert "HSR" in caught.value.message
    assert {result.kind for result in client.hsr_endgame(ENDGAME_MODES).values()} == {"account"}
    assert {call[0] for call in fake.calls} == {"accounts"}


def test_without_configured_uids_hsr_data_is_read_for_the_highest_level_hsr_account():
    fake = FakeHoyolab()

    make_client(fake, uids={}).hsr_characters()

    assert ("characters", 800000001, {"lang": None, "simple": False}) in fake.calls


def test_hsr_data_without_a_cookie_says_to_fill_it_in():
    client = HoyolabClient(None, client_factory=lambda cookie: FakeHoyolab())

    for call in (client.hsr_characters, client.hsr_calendar):
        with pytest.raises(GachaError) as caught:
            call()
        assert caught.value.kind == "no_cookie"
    assert {result.kind for result in client.hsr_endgame(ENDGAME_MODES).values()} == {"no_cookie"}


def test_an_unreachable_hoyolab_is_a_network_error_for_hsr_data():
    failures = {"characters": aiohttp.ClientConnectionError("down"), "calendar": OSError("down")}
    client = make_client(FakeHoyolab(failures=failures))

    for call in (client.hsr_characters, client.hsr_calendar):
        with pytest.raises(GachaError) as caught:
            call()
        assert caught.value.kind == "network"


def test_hsr_data_that_does_not_parse_is_other():
    fake = FakeHoyolab(
        failures={"characters": new_path_error()},
        answers={"calendar": SimpleNamespace(), "apocalyptic_shadow": SimpleNamespace()},
    )
    client = make_client(fake)

    for call in (client.hsr_characters, client.hsr_calendar):
        with pytest.raises(GachaError) as caught:
            call()
        assert caught.value.kind == "other"
        assert "HSR" in caught.value.message
    results = client.hsr_endgame(ENDGAME_MODES)
    assert results["apocalyptic_shadow"].kind == "other"
    assert isinstance(results["memory_of_chaos"], HsrEndgame)


def test_each_hsr_call_is_one_fresh_client_and_never_changes_a_hoyolab_setting():
    hidden = errors.DataNotPublic({"retcode": 10102})
    fake = FakeHoyolab(failures={"pure_fiction": hidden})
    client = make_client(fake)

    client.hsr_characters()
    client.hsr_endgame(ENDGAME_MODES)
    client.hsr_calendar()

    assert fake.cookies == [COOKIE] * 3
    assert ("update_settings",) not in fake.calls


def test_hsr_results_errors_and_logs_never_hold_the_cookie_uid_or_nickname(caplog):
    failures = {
        "characters": new_path_error(),
        "memory_of_chaos": errors.GenshinException({"retcode": -999}),
        "pure_fiction": errors.DataNotPublic({"retcode": 10102}),
        "apocalyptic_shadow": aiohttp.ClientConnectionError("down"),
        "calendar": errors.InvalidCookies(),
    }
    client = make_client(FakeHoyolab(failures=failures))

    with caplog.at_level(logging.DEBUG):
        endgame = client.hsr_endgame(ENDGAME_MODES)
        texts = [repr(endgame)]
        for call in (client.hsr_characters, client.hsr_calendar):
            with pytest.raises(GachaError) as caught:
                call()
            texts.append(str(caught.value))

    for secret in ("SECRETCOOKIE", "SECRETTOKEN", "800000001", "Master"):
        assert all(secret not in text for text in texts)
        assert secret not in caplog.text


# ---- Genshin and ZZZ endgame requests (v1.23 contract) ------------------------------------------


class EndgameRequests(FakeHoyolab):
    """genshin.py's Genshin and ZZZ endgame requests with its signatures, recording every
    argument and answering each mode's name in place of a model."""

    async def get_genshin_spiral_abyss(self, uid=None, *, previous=False, lang=None, raw=False):
        arguments = {"previous": previous, "lang": lang, "raw": raw}
        return await self._answer(("spiral_abyss", uid, arguments), "spiral_abyss")

    async def get_imaginarium_theater(
        self, uid=None, *, previous=False, need_detail=True, lang=None, raw=False
    ):
        arguments = {"previous": previous, "need_detail": need_detail, "lang": lang, "raw": raw}
        return await self._answer(("imaginarium_theater", uid, arguments), "imaginarium_theater")

    async def get_stygian_onslaught(self, uid=None, *, lang=None, raw=False, need_detail=True):
        arguments = {"lang": lang, "raw": raw, "need_detail": need_detail}
        return await self._answer(("stygian_onslaught", uid, arguments), "stygian_onslaught")

    async def get_shiyu_defense(self, uid=None, *, previous=False, lang=None, raw=False):
        arguments = {"previous": previous, "lang": lang, "raw": raw}
        return await self._answer(("shiyu_defense", uid, arguments), "shiyu_defense")

    async def get_deadly_assault(self, uid=None, *, previous=False, lang=None, raw=False):
        arguments = {"previous": previous, "lang": lang, "raw": raw}
        return await self._answer(("deadly_assault", uid, arguments), "deadly_assault")


def read_as_pair(mode: str, model) -> tuple[str, object]:
    return mode, model


def test_genshin_and_zzz_endgame_modes_ask_for_this_season_of_their_games_uid():
    fake = EndgameRequests()
    client = make_client(fake)

    genshin = client._endgame(GAMES["genshin"], GAME_MODES["genshin"], read_as_pair)
    zzz = client._endgame(GAMES["zzz"], GAME_MODES["zzz"], read_as_pair)

    assert genshin == {mode: (mode, mode) for mode in GAME_MODES["genshin"]}
    assert zzz == {mode: (mode, mode) for mode in GAME_MODES["zzz"]}
    this_season = {"previous": False, "lang": None, "raw": False}
    assert {call[0]: call[1:] for call in fake.calls} == {
        "spiral_abyss": (800000002, this_season),
        # The Theater's detail would need genshin.py's character-name list.
        "imaginarium_theater": (800000002, {**this_season, "need_detail": False}),
        "stygian_onslaught": (800000002, {"lang": None, "raw": False, "need_detail": True}),
        "shiyu_defense": (1300000003, this_season),
        "deadly_assault": (1300000003, this_season),
    }


def test_hidden_genshin_and_zzz_battle_chronicle_data_names_its_game():
    hidden = errors.DataNotPublic({"retcode": 10102})
    fake = EndgameRequests(failures={"spiral_abyss": hidden, "deadly_assault": hidden})
    client = make_client(fake)

    genshin = client._endgame(GAMES["genshin"], ["spiral_abyss"], read_as_pair)
    zzz = client._endgame(GAMES["zzz"], ["shiyu_defense", "deadly_assault"], read_as_pair)

    assert (genshin["spiral_abyss"].kind, genshin["spiral_abyss"].message) == (
        "chronicle_private",
        CHRONICLE_PRIVATE.replace("Data HSR", "Data Genshin"),
    )
    assert zzz["shiyu_defense"] == ("shiyu_defense", "shiyu_defense")
    assert (zzz["deadly_assault"].kind, zzz["deadly_assault"].message) == (
        "chronicle_private",
        CHRONICLE_PRIVATE.replace("Data HSR", "Data ZZZ"),
    )


def test_a_failure_of_a_whole_genshin_or_zzz_endgame_call_names_its_game_for_every_mode():
    fake = EndgameRequests(accounts=[account("hkrpg_global", 800000001, 70)])
    client = make_client(fake, uids={})

    results = client._endgame(GAMES["zzz"], GAME_MODES["zzz"], read_as_pair)

    assert {result.kind for result in results.values()} == {"account"}
    assert {result.message for result in results.values()} == {
        "Akun ZZZ tidak ditemukan di HoYoLAB ini."
    }
    assert [call[0] for call in fake.calls] == ["accounts"]


# ---- Genshin data (v1.23) -----------------------------------------------------------------------

GENSHIN_MODES = GAME_MODES["genshin"]
GENSHIN_ENDGAME_CALLS = {
    "spiral_abyss": (800000002, {"previous": False, "lang": None, "raw": False}),
    "imaginarium_theater": (
        800000002,
        {"previous": False, "need_detail": False, "lang": None, "raw": False},
    ),
    "stygian_onslaught": (800000002, {"lang": None, "raw": False, "need_detail": True}),
}
GENSHIN_PRIVATE = CHRONICLE_PRIVATE.replace("Data HSR", "Data Genshin")


def parse_error(build: Callable[[], object]) -> Exception:
    """What genshin.py raises for a payload it cannot read."""
    try:
        build()
    except Exception as error:
        return error
    raise AssertionError("genshin.py accepted the payload")


def genshin_data_calls(client: HoyolabClient) -> tuple:
    """Each Genshin data call that answers with a value or raises a GachaError."""
    return (
        client.genshin_characters,
        lambda: client.genshin_character_details([FURINA_ID]),
        client.genshin_calendar,
    )


def test_genshin_characters_reads_the_roster_of_the_genshin_uid():
    fake = FakeHoyolab()

    characters = make_client(fake).genshin_characters()

    assert [(item.name, item.constellation, item.weapon.refinement) for item in characters] == [
        ("Furina", 2, 1),
        ("Kazuha", 0, 5),
    ]
    assert fake.calls == [("genshin_characters", 800000002, {"lang": None})]


def test_genshin_character_details_asks_once_for_exactly_those_characters():
    fake = FakeHoyolab()

    details = make_client(fake).genshin_character_details((FURINA_ID,))

    assert [detail.character.name for detail in details] == ["Furina"]
    assert details[0].stats == (("Max HP", "35307"),)
    arguments = {"characters": [FURINA_ID], "lang": None, "return_raw_data": False}
    assert fake.calls == [("genshin_details", 800000002, arguments)]


def test_genshin_character_details_of_no_character_asks_nothing():
    fake = FakeHoyolab()

    assert make_client(fake).genshin_character_details([]) == ()
    assert (fake.calls, fake.cookies) == ([], [])


def test_genshin_endgame_reads_every_mode_asked_for_this_season_of_the_genshin_uid():
    fake = FakeHoyolab()

    results = make_client(fake, now=lambda: FETCHED_AT).genshin_endgame(GENSHIN_MODES)

    assert list(results) == list(GENSHIN_MODES)
    assert all(isinstance(result, Endgame) for result in results.values())
    assert results["spiral_abyss"].stars == 36
    assert results["imaginarium_theater"].deepest == "Act 10"
    assert results["stygian_onslaught"].difficulty == "5"
    assert {call[0]: call[1:] for call in fake.calls} == GENSHIN_ENDGAME_CALLS


def test_genshin_endgame_reads_the_theater_season_running_at_the_fetch_time():
    in_september = OCTOBER_START - timedelta(days=10)
    client = make_client(FakeHoyolab(), now=lambda: in_september)

    theater = client.genshin_endgame(["imaginarium_theater"])["imaginarium_theater"]

    assert (theater.deepest, theater.ends_at) == ("Act 8", OCTOBER_START)


def test_the_genshin_endgame_fetch_time_is_read_before_the_requests():
    fake = FakeHoyolab()

    def now() -> datetime:
        fake.calls.append(("now",))
        return FETCHED_AT

    make_client(fake, now=now).genshin_endgame(GENSHIN_MODES)

    assert fake.calls[0] == ("now",)
    assert len(fake.calls) == 1 + len(GENSHIN_MODES)


def test_one_failing_genshin_endgame_mode_does_not_hide_the_others():
    failures = {
        "spiral_abyss": errors.DataNotPublic({"retcode": 10102}),
        "stygian_onslaught": errors.GenshinException({"retcode": -999}),
    }

    results = make_client(FakeHoyolab(failures=failures)).genshin_endgame(GENSHIN_MODES)

    assert isinstance(results["imaginarium_theater"], Endgame)
    abyss, stygian = results["spiral_abyss"], results["stygian_onslaught"]
    assert (abyss.kind, abyss.message) == ("chronicle_private", GENSHIN_PRIVATE)
    assert (stygian.kind, stygian.message) == (
        "other",
        "HoYoLAB menolak permintaan Genshin (kode -999).",
    )


def test_genshin_calendar_reads_its_times_from_the_unix_timestamps():
    fake = FakeHoyolab()

    calendar = make_client(fake, now=lambda: FETCHED_AT).genshin_calendar()

    assert isinstance(calendar, GameCalendar)
    assert calendar.version == "6.1"
    assert calendar.banners[0].featured == ("Furina",)
    assert calendar.banners[0].ends_at == FETCHED_AT + timedelta(days=5)
    assert [event.name for event in calendar.events] == ["Lantern Rite"]
    assert [window.mode for window in calendar.challenges] == ["spiral_abyss"]
    assert fake.calls == [("genshin_calendar", 800000002, {"lang": None})]


def test_the_genshin_calendar_drops_what_is_over_at_its_fetch_time():
    client = make_client(FakeHoyolab(), now=lambda: FETCHED_AT + timedelta(days=6))

    calendar = client.genshin_calendar()

    assert (calendar.banners, calendar.version) == ((), None)
    assert [event.name for event in calendar.events] == ["Lantern Rite"]


def test_the_genshin_calendar_fetch_time_is_read_before_the_request():
    fake = FakeHoyolab()

    def now() -> datetime:
        fake.calls.append(("now",))
        return FETCHED_AT

    make_client(fake, now=now).genshin_calendar()

    assert [call[0] for call in fake.calls] == ["now", "genshin_calendar"]


def test_hidden_genshin_battle_chronicle_data_is_chronicle_private_and_names_genshin():
    hidden = errors.DataNotPublic({"retcode": 10102})
    names = (
        "genshin_characters",
        "genshin_details",
        "genshin_calendar",
        "imaginarium_theater",
        "genshin_notes",
    )
    client = make_client(FakeHoyolab(failures=dict.fromkeys(names, hidden)))

    for call in genshin_data_calls(client):
        with pytest.raises(GachaError) as caught:
            call()
        assert (caught.value.kind, caught.value.message) == ("chronicle_private", GENSHIN_PRIVATE)
    theater = client.genshin_endgame(["imaginarium_theater"])["imaginarium_theater"]
    assert (theater.kind, theater.message) == ("chronicle_private", GENSHIN_PRIVATE)
    # The Real-Time Notes keep their own kind: they are switched on elsewhere.
    assert client.status(["genshin"])["genshin"].kind == "notes_off"


def test_genshin_data_without_a_genshin_account_is_an_account_error():
    fake = FakeHoyolab(accounts=[account("hkrpg_global", 800000001, 70)])
    client = make_client(fake, uids={})

    for call in genshin_data_calls(client):
        with pytest.raises(GachaError) as caught:
            call()
        assert (caught.value.kind, caught.value.message) == (
            "account",
            "Akun Genshin tidak ditemukan di HoYoLAB ini.",
        )
    assert {result.kind for result in client.genshin_endgame(GENSHIN_MODES).values()} == {"account"}
    assert {call[0] for call in fake.calls} == {"accounts"}


def test_without_configured_uids_genshin_data_is_read_for_the_highest_level_genshin_account():
    fake = FakeHoyolab()

    make_client(fake, uids={}).genshin_characters()

    assert fake.calls == [("accounts",), ("genshin_characters", 800000002, {"lang": None})]


def test_genshin_data_without_a_cookie_says_to_fill_it_in():
    client = HoyolabClient(None, client_factory=lambda cookie: FakeHoyolab())

    for call in genshin_data_calls(client):
        with pytest.raises(GachaError) as caught:
            call()
        assert caught.value.kind == "no_cookie"
    kinds = {result.kind for result in client.genshin_endgame(GENSHIN_MODES).values()}
    assert kinds == {"no_cookie"}


def test_genshin_data_that_does_not_parse_is_other(monkeypatch):
    # genshin.py's character-name list is not loaded: Spiral Abyss avatars carry only an id.
    monkeypatch.setattr(genshin_constants, "CHARACTER_NAMES", {})
    abyss = copy.deepcopy(SPIRAL_ABYSS)
    battle = {"index": 1, "timestamp": server_unix(FETCHED_AT), "avatars": [{"id": FURINA_ID}]}
    abyss["floors"][0]["levels"] = [{"index": 1, "star": 3, "max_star": 3, "battles": [battle]}]
    details = copy.deepcopy(GENSHIN_DETAILS)
    details["list"][0]["base"] = {**FURINA, "weapon_type": 99}  # a weapon type new to genshin.py
    theater = copy.deepcopy(IMAGINARIUM_THEATER)
    theater["data"][1]["stat"]["difficulty_id"] = 6
    calendar = copy.deepcopy(GENSHIN_CALENDAR)
    calendar["avatar_card_pool_list"][0]["avatars"][0]["rarity"] = 3
    failures = {
        "genshin_characters": parse_error(lambda: Character(**{**FURINA, "weapon": None})),
        "genshin_details": parse_error(lambda: GenshinDetailCharacters(**details)),
        "spiral_abyss": parse_error(lambda: SpiralAbyss(**abyss)),
        "imaginarium_theater": parse_error(lambda: ImgTheater(**theater)),
        "genshin_calendar": parse_error(lambda: GenshinEventCalendar(**calendar)),
    }
    assert "Character names not loaded" in str(failures["spiral_abyss"])
    client = make_client(FakeHoyolab(failures=failures))

    for call in genshin_data_calls(client):
        with pytest.raises(GachaError) as caught:
            call()
        assert (caught.value.kind, caught.value.message) == (
            "other",
            "Data HoYoLAB Genshin tidak bisa dibaca saat ini.",
        )
    results = client.genshin_endgame(GENSHIN_MODES)
    assert (results["spiral_abyss"].kind, results["imaginarium_theater"].kind) == ("other", "other")
    assert isinstance(results["stygian_onslaught"], Endgame)


def test_each_genshin_call_is_one_fresh_client_and_never_changes_a_hoyolab_setting():
    hidden = errors.DataNotPublic({"retcode": 10102})
    fake = FakeHoyolab(failures={"spiral_abyss": hidden})
    client = make_client(fake)

    client.genshin_characters()
    client.genshin_character_details([FURINA_ID])
    client.genshin_endgame(GENSHIN_MODES)
    client.genshin_calendar()

    assert fake.cookies == [COOKIE] * 4
    assert ("update_settings",) not in fake.calls


def test_genshin_results_errors_and_logs_never_hold_the_cookie_uid_or_nickname(caplog):
    failures = {
        "genshin_details": errors.InvalidCookies(),
        "spiral_abyss": errors.GenshinException({"retcode": -999}),
        "imaginarium_theater": errors.DataNotPublic({"retcode": 10102}),
        "genshin_calendar": aiohttp.ClientConnectionError("down"),
    }
    # Without configured uids the account lookup, which holds the nickname, runs too.
    client = make_client(FakeHoyolab(failures=failures), uids={})

    with caplog.at_level(logging.DEBUG):
        texts = [repr(client.genshin_characters()), repr(client.genshin_endgame(GENSHIN_MODES))]
        for call in genshin_data_calls(client)[1:]:
            with pytest.raises(GachaError) as caught:
                call()
            texts.append(str(caught.value))

    for secret in ("SECRETCOOKIE", "SECRETTOKEN", "800000002", "Master"):
        assert all(secret not in text for text in texts)
        assert secret not in caplog.text


# ---- redeem codes (v1.19) -----------------------------------------------------------------------

CODES = ["JADE2026AAA", "JADE2026BBB", "JADE2026CCC"]
STAR_RAIL = genshin.Game.STARRAIL
COOKIE_REFUSED = (
    "Cookie HoYoLAB tidak bisa dipakai untuk menukarkan kode. Penukaran butuh cookie_token_v2, "
    "dan cookie-nya mungkin sudah kedaluwarsa: salin ulang dari HoYoLAB lalu isi gacha.cookie "
    "di config.yaml."
)
UNREACHABLE = "HoYoLAB tidak bisa dihubungi. Cek koneksi internet."


def refusal(retcode: int) -> errors.GenshinException:
    """What genshin.py raises when HoYoLAB answers a redemption with this retcode."""
    with pytest.raises(errors.GenshinException) as caught:
        errors.raise_for_retcode({"retcode": retcode, "message": ""})
    return caught.value


def redeemed_codes(fake: FakeHoyolab) -> list[str]:
    return [call[1] for call in fake.calls if call[0] == "redeem"]


def test_codes_are_redeemed_one_at_a_time_for_the_game_uid_six_seconds_apart():
    fake = FakeHoyolab()

    results = make_client(fake).redeem_codes("hsr", CODES)

    assert [(r.game, r.code, r.outcome) for r in results] == [
        ("hsr", "JADE2026AAA", "redeemed"),
        ("hsr", "JADE2026BBB", "redeemed"),
        ("hsr", "JADE2026CCC", "redeemed"),
    ]
    assert fake.calls == [
        ("redeem", "JADE2026AAA", 800000001, STAR_RAIL),
        ("sleep", 6.0),
        ("redeem", "JADE2026BBB", 800000001, STAR_RAIL),
        ("sleep", 6.0),
        ("redeem", "JADE2026CCC", 800000001, STAR_RAIL),
    ]


def test_a_redeemed_code_says_the_rewards_are_in_the_in_game_mail():
    results = make_client(FakeHoyolab()).redeem_codes("zzz", ["NAPGIFT2026"])

    assert results == [
        RedeemResult(
            "zzz",
            "NAPGIFT2026",
            "redeemed",
            "Kode NAPGIFT2026 berhasil ditukarkan. Hadiahnya ada di surat dalam game ZZZ.",
        )
    ]


@pytest.mark.parametrize(
    ("game", "uid", "hoyolab_game"),
    [("genshin", 800000002, genshin.Game.GENSHIN), ("zzz", 1300000003, genshin.Game.ZZZ)],
)
def test_codes_are_redeemed_for_the_uid_and_genshin_game_of_their_game(game, uid, hoyolab_game):
    fake = FakeHoyolab()

    make_client(fake).redeem_codes(game, ["GIFT2026AAA"])

    assert fake.calls == [("redeem", "GIFT2026AAA", uid, hoyolab_game)]


def test_without_configured_uids_codes_go_to_the_highest_level_account():
    fake = FakeHoyolab()

    make_client(fake, uids={}).redeem_codes("hsr", ["GIFT2026AAA"])

    assert fake.calls == [("accounts",), ("redeem", "GIFT2026AAA", 800000001, STAR_RAIL)]


def test_a_cooldown_waits_six_seconds_and_tries_the_code_once_more():
    fake = FakeHoyolab(redemptions={"JADE2026AAA": [refusal(-2016)]})

    results = make_client(fake).redeem_codes("hsr", CODES[:2])

    assert [r.outcome for r in results] == ["redeemed", "redeemed"]
    assert [call[:2] for call in fake.calls] == [
        ("redeem", "JADE2026AAA"),
        ("sleep", 6.0),
        ("redeem", "JADE2026AAA"),
        ("sleep", 6.0),
        ("redeem", "JADE2026BBB"),
    ]


def test_a_second_cooldown_leaves_that_code_and_goes_on_with_the_next():
    fake = FakeHoyolab(redemptions={"JADE2026AAA": [refusal(-2016), refusal(-2016)]})

    results = make_client(fake).redeem_codes("hsr", CODES[:2])

    assert results[0] == RedeemResult(
        "hsr",
        "JADE2026AAA",
        "cooldown",
        "Kode JADE2026AAA belum bisa ditukarkan: HoYoLAB sedang membatasi penukaran kode. "
        "Coba lagi sebentar lagi.",
    )
    assert results[1].outcome == "redeemed"
    assert [call[:2] for call in fake.calls] == [
        ("redeem", "JADE2026AAA"),
        ("sleep", 6.0),
        ("redeem", "JADE2026AAA"),
        ("sleep", 6.0),
        ("redeem", "JADE2026BBB"),
    ]


def test_a_refusal_after_a_cooldown_is_that_codes_outcome():
    fake = FakeHoyolab(redemptions={"JADE2026AAA": [refusal(-2016), refusal(-2017)]})

    [result] = make_client(fake).redeem_codes("hsr", ["JADE2026AAA"])

    assert result.outcome == "claimed"


@pytest.mark.parametrize(
    ("retcode", "outcome", "message"),
    [
        (-2017, "claimed", "Kode JADE2026AAA sudah pernah ditukarkan di akun HSR ini."),
        (-2018, "claimed", "Kode JADE2026AAA sudah pernah ditukarkan di akun HSR ini."),
        (-2001, "expired", "Kode JADE2026AAA sudah kedaluwarsa."),
        (-2003, "invalid", "Kode JADE2026AAA tidak valid menurut HoYoLAB."),  # malformed
        (-2004, "invalid", "Kode JADE2026AAA tidak valid menurut HoYoLAB."),
        (-2006, "invalid", "Kode JADE2026AAA tidak valid menurut HoYoLAB."),  # used up
        (-2008, "region", "Kode JADE2026AAA tidak berlaku di server akun HSR ini."),
        (-2011, "level", "Level akun HSR belum cukup untuk kode JADE2026AAA."),
        (-2021, "level", "Level akun HSR belum cukup untuk kode JADE2026AAA."),
    ],
)
def test_hoyolab_refusals_become_outcomes_with_a_sentence_for_her(retcode, outcome, message):
    fake = FakeHoyolab(redemptions={"JADE2026AAA": [refusal(retcode)]})

    results = make_client(fake).redeem_codes("hsr", ["JADE2026AAA"])

    assert results == [RedeemResult("hsr", "JADE2026AAA", outcome, message)]


@pytest.mark.parametrize(
    ("failure", "problem"),
    [
        (
            errors.GenshinException({"retcode": -9999}),
            "HoYoLAB menolak permintaan HSR (kode -9999).",
        ),
        (
            errors.RedemptionException({"retcode": -2099, "message": "redemption failed"}),
            "HoYoLAB menolak permintaan HSR (kode -2099).",
        ),
        (ValueError("odd answer"), "Data HoYoLAB HSR tidak bisa dibaca saat ini."),
    ],
)
def test_any_other_failure_is_an_error_and_the_next_code_is_still_tried(failure, problem):
    fake = FakeHoyolab(redemptions={"JADE2026AAA": [failure]})

    results = make_client(fake).redeem_codes("hsr", CODES[:2])

    assert results[0] == RedeemResult(
        "hsr", "JADE2026AAA", "error", f"Kode JADE2026AAA belum bisa ditukarkan: {problem}"
    )
    assert results[1].outcome == "redeemed"


RATE_LIMITED = "HoYoLAB sedang membatasi permintaan. Coba lagi beberapa menit lagi."
CAPTCHA = (
    "HoYoLAB minta verifikasi captcha HSR. Buka HoYoLAB di browser sekali (untuk check-in, ambil "
    "manual dulu), lalu coba lagi."
)


@pytest.mark.parametrize(
    ("failure", "problem"),
    [
        (aiohttp.ClientConnectionError("down"), UNREACHABLE),
        (OSError("network is unreachable"), UNREACHABLE),
        (errors.TooManyRequests(), RATE_LIMITED),
        (refusal(-110), RATE_LIMITED),  # VisitsTooFrequently
        (errors.GeetestError({"retcode": 1034}), CAPTCHA),
    ],
    ids=["connection", "os-error", "too-many-requests", "visits-too-frequently", "captcha"],
)
def test_hoyolab_unreachable_rate_limiting_or_asking_for_a_captcha_stops_the_rest_untried(
    failure, problem
):
    fake = FakeHoyolab(redemptions={"JADE2026BBB": [failure]})

    results = make_client(fake).redeem_codes("hsr", [*CODES, "JADE2026DDD"])

    assert [(r.code, r.outcome, r.message) for r in results] == [
        (
            "JADE2026AAA",
            "redeemed",
            "Kode JADE2026AAA berhasil ditukarkan. Hadiahnya ada di surat dalam game HSR.",
        ),
        ("JADE2026BBB", "error", f"Kode JADE2026BBB belum bisa ditukarkan: {problem}"),
        ("JADE2026CCC", "error", f"Kode JADE2026CCC belum bisa ditukarkan: {problem}"),
        ("JADE2026DDD", "error", f"Kode JADE2026DDD belum bisa ditukarkan: {problem}"),
    ]
    assert [call[:2] for call in fake.calls] == [
        ("redeem", "JADE2026AAA"),
        ("sleep", 6.0),
        ("redeem", "JADE2026BBB"),
    ]


@pytest.mark.parametrize("failure", [refusal(-1071), errors.InvalidCookies()])
def test_a_refused_cookie_gives_this_and_every_remaining_code_the_cookie_outcome_untried(failure):
    fake = FakeHoyolab(redemptions={"JADE2026BBB": [failure]})

    results = make_client(fake).redeem_codes("hsr", [*CODES, "JADE2026DDD"])

    assert results == [
        RedeemResult(
            "hsr",
            "JADE2026AAA",
            "redeemed",
            "Kode JADE2026AAA berhasil ditukarkan. Hadiahnya ada di surat dalam game HSR.",
        ),
        RedeemResult("hsr", "JADE2026BBB", "cookie", COOKIE_REFUSED),
        RedeemResult("hsr", "JADE2026CCC", "cookie", COOKIE_REFUSED),
        RedeemResult("hsr", "JADE2026DDD", "cookie", COOKIE_REFUSED),
    ]
    assert redeemed_codes(fake) == ["JADE2026AAA", "JADE2026BBB"]


def test_a_cookie_refused_while_finding_the_account_gives_every_code_the_cookie_outcome():
    fake = FakeHoyolab(failures={"accounts": errors.InvalidCookies()})

    results = make_client(fake, uids={}).redeem_codes("hsr", CODES[:2])

    assert [(r.code, r.outcome, r.message) for r in results] == [
        ("JADE2026AAA", "cookie", COOKIE_REFUSED),
        ("JADE2026BBB", "cookie", COOKIE_REFUSED),
    ]
    assert fake.calls == [("accounts",)]


def test_a_game_without_an_account_gives_every_code_an_error_untried():
    fake = FakeHoyolab(accounts=[account("hkrpg_global", 800000001, 70)])

    results = make_client(fake, uids={}).redeem_codes("zzz", ["NAPGIFT2026"])

    assert results == [
        RedeemResult(
            "zzz",
            "NAPGIFT2026",
            "error",
            "Kode NAPGIFT2026 belum bisa ditukarkan: Akun ZZZ tidak ditemukan di HoYoLAB ini.",
        )
    ]
    assert fake.calls == [("accounts",)]


def test_redeeming_without_a_cookie_raises_no_cookie_before_asking_hoyolab():
    fake = FakeHoyolab()
    client = make_client(fake, cookie=None)

    with pytest.raises(GachaError) as caught:
        client.redeem_codes("hsr", CODES)

    assert caught.value.kind == "no_cookie"
    assert "gacha.cookie" in caught.value.message
    assert (fake.calls, fake.cookies) == ([], [])


def test_at_most_ten_codes_are_redeemed_per_call_and_the_rest_are_left_for_a_later_one():
    codes = [f"JADE2026{n:03d}" for n in range(12)]
    fake = FakeHoyolab()

    results = make_client(fake).redeem_codes("hsr", codes)

    assert [r.code for r in results] == codes[:10]
    assert redeemed_codes(fake) == codes[:10]


def test_each_attempt_has_its_own_timeout_and_a_slow_one_leaves_the_rest_untried():
    # Without its own timeout the attempt would answer after 1 s, within the call's bound.
    fake = FakeHoyolab(delay_s=1.0)

    results = make_client(fake, timeout_s=0.05).redeem_codes("hsr", CODES[:2])

    assert [(r.outcome, r.message) for r in results] == [
        ("error", f"Kode JADE2026AAA belum bisa ditukarkan: {UNREACHABLE}"),
        ("error", f"Kode JADE2026BBB belum bisa ditukarkan: {UNREACHABLE}"),
    ]
    assert [call[:2] for call in fake.calls] == [("redeem", "JADE2026AAA")]


def test_a_slow_account_lookup_is_an_error_for_every_code_and_redeems_none():
    # The gaps are not waited here, so the call's bound is a minute: only the lookup's own
    # timeout can end it this soon.
    fake = FakeHoyolab(delay_s=1.5)
    started = time.monotonic()

    results = make_client(fake, uids={}, timeout_s=0.05).redeem_codes("hsr", CODES[:2])

    assert [(r.code, r.outcome, r.message) for r in results] == [
        ("JADE2026AAA", "error", f"Kode JADE2026AAA belum bisa ditukarkan: {UNREACHABLE}"),
        ("JADE2026BBB", "error", f"Kode JADE2026BBB belum bisa ditukarkan: {UNREACHABLE}"),
    ]
    assert fake.calls == [("accounts",)]
    assert time.monotonic() - started < 1.0


def test_each_result_is_handed_over_as_soon_as_it_is_known():
    fake = FakeHoyolab(redemptions={"JADE2026BBB": [refusal(-2017)]})

    def handed_over(result: RedeemResult) -> None:
        fake.calls.append(("result", result.code, result.outcome))

    results = make_client(fake).redeem_codes("hsr", CODES[:2], on_result=handed_over)

    assert [r.outcome for r in results] == ["redeemed", "claimed"]
    assert [call[:3] for call in fake.calls] == [
        ("redeem", "JADE2026AAA", 800000001),
        ("result", "JADE2026AAA", "redeemed"),
        ("sleep", 6.0),
        ("redeem", "JADE2026BBB", 800000001),
        ("result", "JADE2026BBB", "claimed"),
    ]


@pytest.mark.parametrize(
    ("redemption_failure", "lookup_failure"),
    [
        (errors.InvalidCookies(), None),
        (aiohttp.ClientConnectionError("down"), None),
        (None, aiohttp.ClientConnectionError("down")),
    ],
    ids=["cookie", "unreachable", "account-lookup"],
)
def test_codes_left_untried_are_handed_over_too(redemption_failure, lookup_failure):
    fake = FakeHoyolab(
        redemptions={"JADE2026BBB": [redemption_failure]} if redemption_failure else None,
        failures={"accounts": lookup_failure} if lookup_failure else None,
    )
    handed_over: list[RedeemResult] = []

    results = make_client(fake, uids={}).redeem_codes("hsr", CODES, on_result=handed_over.append)

    assert handed_over == results
    assert len(results) == 3


def test_the_whole_call_is_bounded_and_keeps_the_outcomes_it_already_has(monkeypatch):
    # Without the gaps the bound is ten attempt timeouts (2 s), while every code here takes two
    # 0.12 s attempts (a cooldown, then another): the bound ends the call before the last code.
    monkeypatch.setattr(hoyolab, "REDEEM_GAP_S", 0.0)
    codes = [f"JADE2026{n:03d}" for n in range(10)]
    fake = FakeHoyolab(delay_s=0.12, failures={"redeem": refusal(-2016)})

    results = make_client(fake, timeout_s=0.2).redeem_codes("hsr", codes)

    assert [r.code for r in results] == codes
    assert results[0].outcome == "cooldown"
    assert codes[-1] not in redeemed_codes(fake)
    assert results[-1] == RedeemResult(
        "hsr", codes[-1], "error", f"Kode {codes[-1]} belum bisa ditukarkan: {UNREACHABLE}"
    )


@pytest.mark.parametrize(
    ("stopped_after", "calls"),
    [
        ("redeem", [("redeem", "JADE2026AAA")]),  # the gap to the next code is not waited either
        ("sleep", [("redeem", "JADE2026AAA"), ("sleep", 6.0)]),
    ],
    ids=["while-a-code-is-answered", "during-the-gap"],
)
def test_a_run_told_to_stop_tries_no_more_codes_and_leaves_them_out(stopped_after, calls):
    # As when Cyrene stops while her watcher redeems: no code is tried once it is told to stop.
    fake = FakeHoyolab()
    handed_over: list[RedeemResult] = []

    def should_stop() -> bool:
        return any(call[0] == stopped_after for call in fake.calls)

    results = make_client(fake).redeem_codes(
        "hsr", CODES, on_result=handed_over.append, should_stop=should_stop
    )

    assert [(r.code, r.outcome) for r in results] == [("JADE2026AAA", "redeemed")]
    assert handed_over == results
    assert [call[:2] for call in fake.calls] == calls


def test_a_run_told_to_stop_while_finding_the_account_tries_no_code():
    fake = FakeHoyolab()

    results = make_client(fake, uids={}).redeem_codes(
        "hsr", CODES, should_stop=lambda: ("accounts",) in fake.calls
    )

    assert results == []
    assert fake.calls == [("accounts",)]


def test_a_cooldown_answered_to_a_run_told_to_stop_is_not_tried_again():
    fake = FakeHoyolab(redemptions={"JADE2026AAA": [refusal(-2016)]})

    results = make_client(fake).redeem_codes(
        "hsr", CODES, should_stop=lambda: bool(redeemed_codes(fake))
    )

    assert [(r.code, r.outcome) for r in results] == [("JADE2026AAA", "cooldown")]
    assert redeemed_codes(fake) == ["JADE2026AAA"]


def test_redemption_results_and_logs_never_hold_the_cookie_or_the_uid(caplog):
    redemptions = {
        "JADE2026AAA": [refusal(-2001)],
        "JADE2026BBB": [errors.GenshinException({"retcode": -9999})],
        "JADE2026CCC": [refusal(-1071)],
    }
    fake = FakeHoyolab(redemptions=redemptions)

    with caplog.at_level(logging.DEBUG):
        results = make_client(fake).redeem_codes("hsr", [*CODES, "JADE2026DDD"])

    for secret in ("SECRETCOOKIE", "SECRETTOKEN", "800000001"):
        assert secret not in repr(results)
        assert secret not in caplog.text


def test_redeeming_is_one_fresh_client_and_never_changes_a_hoyolab_setting():
    fake = FakeHoyolab()

    make_client(fake).redeem_codes("hsr", CODES)

    assert fake.cookies == [COOKIE]
    assert ("update_settings",) not in fake.calls


# ---- ZZZ data (v1.23) ---------------------------------------------------------------------------

ZZZ_UID = 1300000003
ZZZ_NOW = int(FETCHED_AT.timestamp())
ZZZ_PRIVATE = CHRONICLE_PRIVATE.replace("Data HSR", "Data ZZZ")
# genshin.py adds the uid's server offset ("tzinfo") to the endgame times it returns.
ZZZ_SEASON_END = {"year": 2026, "month": 10, "day": 31, "hour": 3, "minute": 59, "tzinfo": 8}


def zzz_agent(agent_id: int, name: str, *, rarity: str = "S", rank: int = 0) -> dict:
    return {
        "id": agent_id,
        "element_type": 202,
        "rarity": rarity,
        "name_mi18n": name,
        "full_name_mi18n": f"{name} Full Name",
        "avatar_profession": 1,
        "group_icon_path": "",
        "hollow_icon_path": "",
        "role_square_url": "",
        "camp_name_mi18n": "",
        "level": 60,
        "rank": rank,
    }


def zzz_agent_detail(agent: dict, weapon: dict | None = None) -> dict:
    detail = {
        **agent,
        "properties": [
            {"property_name": "HP", "property_id": 1, "base": "100", "add": "10", "final": "8000"}
        ],
        "equip": [],
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
        detail["weapon"] = weapon
    return detail


ZZZ_AGENTS = [
    zzz_agent(1191, "Ellen", rank=2),
    zzz_agent(1031, "Nicole", rarity="A"),
    zzz_agent(1141, "Lycaon", rank=1),
]
DEEP_SEA_VISITOR = {
    "id": 14119,
    "level": 60,
    "name": "Deep Sea Visitor",
    "icon": "",
    "star": 2,
    "rarity": "S",
    "properties": [],
    "main_properties": [],
    "talent_title": "",
    "talent_content": "",
    "profession": 1,
}
ZZZ_AGENT_DETAILS = {
    1191: zzz_agent_detail(ZZZ_AGENTS[0], DEEP_SEA_VISITOR),
    1031: zzz_agent_detail(ZZZ_AGENTS[1]),
    1141: zzz_agent_detail(ZZZ_AGENTS[2]),
}
SHIYU_DEFENSE = {
    "zone_id": 62041,
    "hadal_begin_time": {**ZZZ_SEASON_END, "day": 17, "hour": 4, "minute": 0},
    "hadal_end_time": ZZZ_SEASON_END,
    "pass_fifth_floor": True,
    "brief": {"score": 52000, "max_score": 60000, "rank_percent": 1234, "rating": "S"},
    "fitfh_layer_detail": {
        "layer_challenge_info_list": [
            {
                "layer_id": 51,
                "rating": "S",
                "buffer": {"title": "", "text": ""},
                "score": 20000,
                "max_score": 20000,
                "monster_pic": "",
                "avatar_list": [],
            }
        ]
    },
    # genshin.py copies the player's nickname and avatar into the model.
    "nick_name": "Master",
    "icon": "master-avatar-icon",
}
DEADLY_ASSAULT = {
    "zone_id": 30014,
    "start_time": {**ZZZ_SEASON_END, "day": 20, "hour": 4, "minute": 0},
    "end_time": {**ZZZ_SEASON_END, "month": 11, "day": 3},
    "list": [
        {
            "score": 22000,
            "star": 3,
            "total_star": 3,
            "challenge_time": {**ZZZ_SEASON_END, "day": 22},
            "boss": [
                {"icon": "", "name": "Notorious - Marionette", "bg_icon": "", "race_icon": ""}
            ],
            "buffer": [],
            "avatar_list": [],
        }
    ],
    "has_data": True,
    "total_score": 40000,
    "total_star": 5,
    "rank_percent": 523,
    "total_max_score": 60000,
    "nick_name": "Master",
    "avatar_icon": "master-avatar-icon",
}


def zzz_times(start: int, end: int) -> dict:
    return {
        "start_ts": str(start),
        "end_ts": str(end),
        "left_start_ts": max(0, start - ZZZ_NOW),
        "left_end_ts": max(0, end - ZZZ_NOW),
    }


def zzz_event(name: str, start: int, end: int) -> dict:
    return {
        "activity_id": 1001,
        "state": "STATE_IN_PROGRESS",
        "name": name,
        "monochrome_got_cnt": 240,
        "monochrome_cnt": 420,
        **zzz_times(start, end),
    }


def zzz_schedule(kind: str) -> dict:
    return {
        "gacha_type": kind,
        "gacha_state": "GACHA_STATE_IN_PROGRESS",
        "version": "2.3",
        **zzz_times(ZZZ_NOW - 5 * 86400, ZZZ_NOW + 16 * 86400),
        "sup_lock_show": False,
        "insurance_id": 0,
    }


def zzz_banner_agent(name: str, rarity: str) -> dict:
    return {
        "avatar_id": 1191,
        "avatar_name": name,
        "full_name": name,
        "rarity": rarity,
        "icon": "",
        "avatar_profession": 1,
        "avatar_element_type": 202,
        "avatar_sub_element_type": 0,
        "wiki_url": "",
        "jump_cultivate": False,
        "is_forward": True,
        "show_upon": True,
    }


ZZZ_EVENTS = [
    zzz_event("Ridu Rush", ZZZ_NOW - 3 * 86400, ZZZ_NOW + 4 * 86400),
    zzz_event("An Event That Ended", ZZZ_NOW - 20 * 86400, ZZZ_NOW - 86400),
]
ZZZ_GACHA_CALENDAR = {
    "avatar_gacha_schedule_list": [
        {
            **zzz_schedule("GACHA_TYPE_CHARACTER_UP"),
            "avatar_list": [zzz_banner_agent("Ellen", "S"), zzz_banner_agent("Nicole", "A")],
        }
    ],
    "weapon_gacha_schedule_list": [{**zzz_schedule("GACHA_TYPE_WEAPON_UP"), "weapon_list": []}],
}
ZZZ_MODES = list(GAME_MODES["zzz"])


def zzz_data_calls(client: HoyolabClient) -> tuple:
    """Every ZZZ data method that raises a GachaError (endgame gives one per mode instead)."""
    return (
        client.zzz_characters,
        lambda: client.zzz_character_details([1191]),
        client.zzz_calendar,
    )


def new_element_error() -> pydantic.ValidationError:
    """What genshin.py raises for an agent of an element it does not know yet."""
    try:
        genshin.models.ZZZPartialAgent(**{**ZZZ_AGENTS[0], "element_type": 299})
    except pydantic.ValidationError as error:
        return error
    raise AssertionError("genshin.py accepted an unknown element")


def test_zzz_characters_reads_the_roster_of_the_zzz_uid():
    fake = FakeHoyolab()

    agents = make_client(fake).zzz_characters()

    assert [(agent.name, agent.rarity, agent.mindscape) for agent in agents] == [
        ("Ellen", "S", 2),
        ("Nicole", "A", 0),
        ("Lycaon", "S", 1),
    ]
    assert fake.calls == [("zzz_agents", ZZZ_UID, {"lang": None})]


def test_zzz_character_details_ask_for_each_agent_on_its_own_and_keep_the_order_given():
    fake = FakeHoyolab()

    details = make_client(fake).zzz_character_details([1031, 1191])

    assert [detail.agent.name for detail in details] == ["Nicole", "Ellen"]
    assert (details[0].w_engine, details[1].w_engine.refinement) == (None, 2)
    # Never a list: genshin.py would gather one request per agent without a limit.
    assert fake.calls == [
        ("zzz_agent_info", 1031, ZZZ_UID, {"lang": None}),
        ("zzz_agent_info", 1191, ZZZ_UID, {"lang": None}),
    ]


def test_at_most_two_agent_details_are_asked_for_at_once():
    fake = FakeHoyolab(delay_s=0.05)

    details = make_client(fake).zzz_character_details([1191, 1031, 1141])

    assert [detail.agent.name for detail in details] == ["Ellen", "Nicole", "Lycaon"]
    assert fake.most_in_flight == 2


def test_no_agent_details_asked_for_ask_hoyolab_nothing():
    fake = FakeHoyolab()

    assert make_client(fake).zzz_character_details([]) == ()
    assert (fake.calls, fake.cookies) == ([], [])


def test_an_agent_hoyolab_does_not_return_fails_the_whole_detail_call():
    with pytest.raises(GachaError) as caught:
        make_client(FakeHoyolab()).zzz_character_details([1191, 9999])

    assert (caught.value.kind, caught.value.message) == (
        "other",
        "Data HoYoLAB ZZZ tidak bisa dibaca saat ini.",
    )


def test_zzz_endgame_reads_this_season_of_both_modes_of_the_zzz_uid():
    fake = FakeHoyolab()

    results = make_client(fake).zzz_endgame(ZZZ_MODES)

    assert list(results) == ["shiyu_defense", "deadly_assault"]
    assert all(isinstance(result, Endgame) for result in results.values())
    assert (results["shiyu_defense"].rating, results["deadly_assault"].score) == ("S", 40000)
    this_season = (ZZZ_UID, {"previous": False, "lang": None, "raw": False})
    assert {call[0]: call[1:] for call in fake.calls} == {
        "shiyu_defense": this_season,
        "deadly_assault": this_season,
    }


def test_an_unknown_shiyu_defense_version_is_other_and_does_not_hide_deadly_assault():
    unknown = ValueError("Unknown Shiyu Defense version: 'v3'")  # what genshin.py raises
    fake = FakeHoyolab(failures={"shiyu_defense": unknown})

    results = make_client(fake).zzz_endgame(ZZZ_MODES)

    assert (results["shiyu_defense"].kind, results["shiyu_defense"].message) == (
        "other",
        "Data HoYoLAB ZZZ tidak bisa dibaca saat ini.",
    )
    assert isinstance(results["deadly_assault"], Endgame)


def test_zzz_calendar_reads_events_and_banners_in_two_requests_after_the_fetch_time():
    fake = FakeHoyolab()

    def now() -> datetime:
        fake.calls.append(("now",))
        return FETCHED_AT

    calendar = make_client(fake, now=now).zzz_calendar()

    assert [event.name for event in calendar.events] == ["Ridu Rush"]  # one ended at FETCHED_AT
    assert [(banner.kind, banner.featured, banner.four_stars) for banner in calendar.banners] == [
        ("agent", ("Ellen",), ("Nicole",)),
        ("w_engine", (), ()),
    ]
    assert calendar.version == "2.3"
    assert fake.calls == [
        ("now",),
        ("zzz_events", ZZZ_UID, {"lang": None}),
        ("zzz_gacha", ZZZ_UID, {"lang": None}),
    ]


@pytest.mark.parametrize("failing", ["zzz_events", "zzz_gacha"])
def test_either_zzz_calendar_request_failing_fails_the_call(failing):
    fake = FakeHoyolab(failures={failing: errors.GenshinException({"retcode": -999})})

    with pytest.raises(GachaError) as caught:
        make_client(fake).zzz_calendar()

    assert (caught.value.kind, caught.value.message) == (
        "other",
        "HoYoLAB menolak permintaan ZZZ (kode -999).",
    )


def test_hidden_zzz_battle_chronicle_data_is_chronicle_private_and_names_zzz():
    hidden = errors.DataNotPublic({"retcode": 10102})
    names = ("zzz_agents", "zzz_agent_info", "zzz_events", "deadly_assault", "zzz_notes")
    client = make_client(FakeHoyolab(failures=dict.fromkeys(names, hidden)))

    for call in zzz_data_calls(client):
        with pytest.raises(GachaError) as caught:
            call()
        assert (caught.value.kind, caught.value.message) == ("chronicle_private", ZZZ_PRIVATE)
    results = client.zzz_endgame(ZZZ_MODES)
    assert (results["deadly_assault"].kind, results["deadly_assault"].message) == (
        "chronicle_private",
        ZZZ_PRIVATE,
    )
    assert isinstance(results["shiyu_defense"], Endgame)
    # The Real-Time Notes keep their own kind: they are switched on elsewhere.
    assert client.status(["zzz"])["zzz"].kind == "notes_off"


def test_zzz_data_without_a_zzz_account_is_an_account_error():
    fake = FakeHoyolab(accounts=[account("hkrpg_global", 800000001, 70)])
    client = make_client(fake, uids={})

    for call in zzz_data_calls(client):
        with pytest.raises(GachaError) as caught:
            call()
        assert (caught.value.kind, caught.value.message) == (
            "account",
            "Akun ZZZ tidak ditemukan di HoYoLAB ini.",
        )
    assert {result.kind for result in client.zzz_endgame(ZZZ_MODES).values()} == {"account"}
    assert {call[0] for call in fake.calls} == {"accounts"}


def test_without_configured_uids_zzz_data_is_read_for_the_highest_level_zzz_account():
    fake = FakeHoyolab()

    make_client(fake, uids={}).zzz_characters()

    assert ("zzz_agents", ZZZ_UID, {"lang": None}) in fake.calls


def test_zzz_data_without_a_cookie_says_to_fill_it_in():
    client = HoyolabClient(None, client_factory=lambda cookie: FakeHoyolab())

    for call in zzz_data_calls(client):
        with pytest.raises(GachaError) as caught:
            call()
        assert caught.value.kind == "no_cookie"
    assert {result.kind for result in client.zzz_endgame(ZZZ_MODES).values()} == {"no_cookie"}


def test_zzz_data_that_does_not_parse_is_other():
    fake = FakeHoyolab(
        failures={"zzz_agents": new_element_error()},
        answers={"zzz_gacha": SimpleNamespace(), "deadly_assault": SimpleNamespace()},
    )
    client = make_client(fake)

    for call in (client.zzz_characters, client.zzz_calendar):
        with pytest.raises(GachaError) as caught:
            call()
        assert (caught.value.kind, caught.value.message) == (
            "other",
            "Data HoYoLAB ZZZ tidak bisa dibaca saat ini.",
        )
    results = client.zzz_endgame(ZZZ_MODES)
    assert results["deadly_assault"].kind == "other"
    assert isinstance(results["shiyu_defense"], Endgame)


def test_each_zzz_call_is_one_fresh_client_and_never_changes_a_hoyolab_setting():
    fake = FakeHoyolab(failures={"deadly_assault": errors.DataNotPublic({"retcode": 10102})})
    client = make_client(fake)

    client.zzz_characters()
    client.zzz_character_details([1191, 1031])
    client.zzz_endgame(ZZZ_MODES)
    client.zzz_calendar()

    assert fake.cookies == [COOKIE] * 4
    assert ("update_settings",) not in fake.calls


def test_zzz_results_errors_and_logs_never_hold_the_cookie_uid_or_nickname(caplog):
    failures = {
        "zzz_agent_info": errors.GenshinException({"retcode": -999}),
        "zzz_events": aiohttp.ClientConnectionError("down"),
    }
    client = make_client(FakeHoyolab(failures=failures))

    with caplog.at_level(logging.DEBUG):
        # Both endgame models hold the player's nickname and avatar.
        texts = [repr(client.zzz_characters()), repr(client.zzz_endgame(ZZZ_MODES))]
        for call in (lambda: client.zzz_character_details([1191]), client.zzz_calendar):
            with pytest.raises(GachaError) as caught:
                call()
            texts.append(str(caught.value))

    for secret in ("SECRETCOOKIE", "SECRETTOKEN", str(ZZZ_UID), "Master", "master-avatar-icon"):
        assert all(secret not in text for text in texts)
        assert secret not in caplog.text
