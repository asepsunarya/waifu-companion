import json
from datetime import datetime, timedelta, timezone

import pytest

from waifu.gacha.games import GAMES
from waifu.gacha.history import BannerStats, HistoryReport, NewPull, TopPull
from waifu.gacha.hoyolab import GachaError, GameStatus, WeeklyTask
from waifu.gacha.tools import GachaTools

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 12, 13, 0, tzinfo=WIB)
HSR = GAMES["hsr"]

HSR_STATUS = GameStatus(
    game="hsr",
    stamina=232,
    stamina_max=300,
    stamina_full_in_s=2880,
    daily_current=300,
    daily_max=500,
    daily_done=False,
    weekly=(WeeklyTask("Echo of War", 2, 3),),
    expeditions_total=4,
    expeditions_finished=1,
    checked_in=True,
)
ZZZ_STATUS = GameStatus(
    game="zzz",
    stamina=240,
    stamina_max=240,
    stamina_full_in_s=0,
    daily_current=400,
    daily_max=400,
    daily_done=True,
    weekly=(),
    expeditions_total=0,
    expeditions_finished=0,
    checked_in=None,
)


class FakeStatus:
    def __init__(self, results):
        self.results = results
        self.asked: list[list[str]] = []

    def status(self, games):
        games = list(games)
        self.asked.append(games)
        return {game: self.results[game] for game in games}


class FakeHistory:
    def __init__(self, report: HistoryReport):
        self.report_to_give = report
        self.asked: list[tuple[str, bool, bool]] = []

    def report(self, game_id, *, refresh=True, full=False):
        self.asked.append((game_id, refresh, full))
        return self.report_to_give


CHARACTER, LIGHT_CONE = HSR.banners[0], HSR.banners[1]
REPORT = HistoryReport(
    game=HSR,
    updated=True,
    problem=None,
    complete=True,
    stats=(
        BannerStats(
            banner=CHARACTER,
            pity=12,
            total=577,
            guaranteed=True,
            recent_tops=(
                TopPull(9, "Welt", 74, False, "2026-10-11 22:00:00"),
                TopPull(5, "Acheron", 30, True, "2026-09-01 10:00:00"),
            ),
        ),
        BannerStats(banner=LIGHT_CONE, pity=3, total=23, guaranteed=None, recent_tops=()),
    ),
    new_pulls=(
        NewPull(CHARACTER, "Welt", 5, "2026-10-11 22:00:00", pulls=74, won_5050=False),
        NewPull(CHARACTER, "Tingyun", 4, "2026-10-11 21:59:00", pulls=None, won_5050=None),
    ),
    new_count=10,
    latest_pull=datetime(2026, 10, 11, 21, 0, tzinfo=WIB),
)


def make_tools(status=None, history=None, games=("hsr", "genshin", "zzz")) -> GachaTools:
    return GachaTools(
        status or FakeStatus({"hsr": HSR_STATUS, "zzz": ZZZ_STATUS}),
        history or FakeHistory(REPORT),
        list(games),
        now=lambda: NOW,
    )


def spec(tools: GachaTools, name: str):
    return next(s for s in tools.specs() if s.name == name)


def test_two_game_tools_limited_to_the_configured_games():
    tools = make_tools(games=["hsr", "zzz"])

    assert [s.name for s in tools.specs()] == ["game_status", "gacha_history"]
    status = spec(tools, "game_status").input_schema
    history = spec(tools, "gacha_history").input_schema
    assert status["properties"]["game"]["enum"] == ["hsr", "zzz"]
    assert "required" not in status
    assert history["required"] == ["game"]
    for schema in (status, history):
        assert schema["additionalProperties"] is False


def test_descriptions_say_when_to_use_them_and_never_to_push_spending():
    tools = make_tools()
    assert "never guess" in spec(tools, "game_status").description
    history = spec(tools, "gacha_history").description
    assert "hour late" in history
    assert "Never suggest spending money" in history


def test_game_status_without_a_game_reports_every_configured_game():
    status = FakeStatus(
        {"hsr": HSR_STATUS, "genshin": GachaError("cookie", "x"), "zzz": ZZZ_STATUS}
    )

    result, is_error = make_tools(status).run("game_status", {})

    data = json.loads(result)
    assert status.asked == [["hsr", "genshin", "zzz"]]
    assert is_error is False
    assert [entry["game"] for entry in data["games"]] == ["hsr", "zzz"]
    assert data["errors"] == [{"game": "genshin", "message": "x"}]


def test_game_status_numbers_and_local_times():
    result, _ = make_tools().run("game_status", {"game": "hsr"})

    [hsr] = json.loads(result)["games"]
    assert hsr == {
        "game": "hsr",
        "name": "Honkai: Star Rail",
        "stamina": {
            "name": "Trailblaze Power",
            "current": 232,
            "max": 300,
            "full": False,
            "full_in_minutes": 48,
            "full_at": "2026-10-12 13:48",
        },
        "daily": {"name": "Daily Training", "current": 300, "max": 500, "done": False},
        "weekly": [{"name": "Echo of War", "current": 2, "max": 3}],
        "expeditions": {"finished": 1, "total": 4},
        "checked_in": True,
        "daily_reset": "03:00",
    }


def test_a_full_stamina_has_no_time_and_a_game_without_expeditions_leaves_them_out():
    result, _ = make_tools().run("game_status", {"game": "zzz"})

    [zzz] = json.loads(result)["games"]
    assert zzz["stamina"]["full"] is True
    assert zzz["stamina"]["full_at"] is None
    assert "expeditions" not in zzz
    assert zzz["checked_in"] is None


def test_game_status_is_an_error_only_when_every_game_failed():
    status = FakeStatus({"hsr": GachaError("no_cookie", "Cookie HoYoLAB belum diisi.")})

    result, is_error = make_tools(status).run("game_status", {"game": "hsr"})

    assert is_error is True
    assert json.loads(result)["errors"] == [
        {"game": "hsr", "message": "Cookie HoYoLAB belum diisi."}
    ]


@pytest.mark.parametrize(
    "arguments", [{"game": "honkai3rd"}, {"game": 1}, {"games": "hsr"}, "hsr", None]
)
def test_invalid_game_status_arguments_are_errors_without_asking_hoyolab(arguments):
    status = FakeStatus({})

    result, is_error = make_tools(status, games=["hsr"]).run("game_status", arguments)

    assert is_error is True
    assert json.loads(result)["ok"] is False
    assert status.asked == []


def test_gacha_history_reports_pity_guarantee_new_pulls_and_spending():
    history = FakeHistory(REPORT)

    result, is_error = make_tools(history=history).run("gacha_history", {"game": "hsr"})

    data = json.loads(result)
    assert is_error is False
    assert history.asked == [("hsr", True, False)]
    assert data["game"] == "hsr"
    assert data["updated"] is True
    assert data["latest_pull"] == "2026-10-11 21:00"
    assert "hour" in data["note"] or "jam" in data["note"]
    assert data["new_pulls_count"] == 10
    assert data["new_pulls"] == [
        {
            "banner": "Character Event Warp",
            "name": "Welt",
            "rarity": "5★",
            "pulls": 74,
            "won_5050": False,
        },
        {"banner": "Character Event Warp", "name": "Tingyun", "rarity": "4★"},
    ]
    character, light_cone = data["banners"]
    assert character == {
        "banner": "Character Event Warp",
        "pity": 12,
        "hard_pity": 90,
        "to_hard_pity": 78,
        "total": 577,
        "guaranteed": True,
        "recent_top_pulls": [
            {"name": "Welt", "pulls": 74, "won_5050": False, "date": "2026-10-11"},
            {"name": "Acheron", "pulls": 30, "won_5050": True, "date": "2026-09-01"},
        ],
    }
    assert "guaranteed" not in light_cone
    assert data["currency_spent_estimate"] == {"currency": "Stellar Jade", "amount": 600 * 160}
    assert "problem" not in data and "hint" not in data


def test_zzz_rarities_are_s_and_a_ranks():
    zzz = GAMES["zzz"]
    report = HistoryReport(
        game=zzz,
        updated=True,
        problem=None,
        complete=True,
        stats=(),
        new_pulls=(NewPull(zzz.banners[0], "Ellen", 4, "2026-10-11 22:00:00", 70, True),),
        new_count=1,
        latest_pull=None,
    )

    result, _ = make_tools(history=FakeHistory(report)).run("gacha_history", {"game": "zzz"})

    assert json.loads(result)["new_pulls"][0]["rarity"] == "S"


def test_a_history_problem_and_hint_are_passed_on_with_the_saved_numbers():
    report = HistoryReport(
        game=HSR,
        updated=False,
        problem="Buka riwayat warp di HSR sekali, lalu tanya lagi.",
        complete=False,
        stats=REPORT.stats,
        new_pulls=(),
        new_count=0,
        latest_pull=None,
    )

    result, is_error = make_tools(history=FakeHistory(report)).run("gacha_history", {"game": "hsr"})

    data = json.loads(result)
    assert is_error is False  # the saved history still answers
    assert data["problem"] == "Buka riwayat warp di HSR sekali, lalu tanya lagi."
    assert "waifu gacha history hsr --full" in data["hint"]


def test_a_history_without_any_data_is_an_error():
    report = HistoryReport(HSR, False, "Buka riwayat warp di HSR sekali.", False, (), (), 0, None)

    _, is_error = make_tools(history=FakeHistory(report)).run("gacha_history", {"game": "hsr"})

    assert is_error is True


@pytest.mark.parametrize("arguments", [{}, {"game": "genshin"}, {"game": "hsr", "full": True}])
def test_invalid_gacha_history_arguments_are_errors(arguments):
    history = FakeHistory(REPORT)

    _, is_error = make_tools(history=history, games=["hsr"]).run("gacha_history", arguments)

    assert is_error is True
    assert history.asked == []
