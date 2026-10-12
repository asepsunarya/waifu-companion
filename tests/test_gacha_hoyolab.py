import asyncio
import copy
import logging

import aiohttp
import genshin
import pytest
from genshin import errors
from genshin.models import Notes, StarRailNote, ZZZNotes
from genshin.models.genshin.daily import DailyReward, DailyRewardInfo
from genshin.models.hoyolab.record import GenshinAccount

from waifu.gacha.hoyolab import (
    Account,
    CheckinResult,
    GachaError,
    GameStatus,
    HoyolabClient,
    WeeklyTask,
    preferred_uids,
)

COOKIE = "ltuid_v2=1; ltoken_v2=v2_SECRETCOOKIE; cookie_token_v2=SECRETTOKEN; account_id_v2=1"
UIDS = {"hsr": 800000001, "genshin": 800000002, "zzz": 1300000003}

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

    def __init__(self, *, failures=None, accounts=ACCOUNTS, delay_s=0.0):
        self.failures = failures or {}
        self.accounts = accounts
        self.delay_s = delay_s
        self.calls: list[tuple] = []

    async def _answer(self, call: tuple, value):
        self.calls.append(call)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        failure = self.failures.get(call[0])
        if failure is not None:
            raise failure
        return value

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


def make_client(fake: FakeHoyolab, *, cookie=COOKIE, uids=UIDS, **kwargs) -> HoyolabClient:
    cookies: list[str] = []

    def factory(given: str):
        cookies.append(given)
        return fake

    client = HoyolabClient(cookie, uids=uids, client_factory=factory, **kwargs)
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
