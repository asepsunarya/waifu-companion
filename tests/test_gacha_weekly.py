"""The weekly game report: the facts gathered in code, their JSON, the weekly_report tool, her
prompt, and the once-a-week timer.

No test reaches HoYoLAB or the history API: the game data source and the history refresh are
fakes, while the pull history and the redeem codes are the real stores in tmp_path. Every
clock is fixed; the time-budget tests block a fake on an event they release at their end.
"""

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from datetime import time as clock

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.confirm import is_yes
from waifu.brain.music_tools import MusicTools
from waifu.brain.screen_tools import ScreenTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.config import AssistantConfig, WeeklyReportConfig
from waifu.gacha.code_store import CodeStore
from waifu.gacha.game_data import GAME_MODES, Banner, Endgame, GameCalendar, GameEvent
from waifu.gacha.games import GAMES
from waifu.gacha.history import GachaRecord, GachaStore, HistoryReport
from waifu.gacha.hoyolab import GachaError
from waifu.gacha.income import GameIncome, IncomeCategory, IncomeCurrency
from waifu.gacha.starrail import HsrCalendar, HsrEndgame, HsrEvent, HsrWarp
from waifu.gacha.tools import HISTORY_NOTE
from waifu.gacha.weekly import (
    LISTING_LAG,
    WEEKLY_REPORT_PROMPT,
    WeeklyReportTimer,
    WeeklyReportTools,
    gather_weekly_report,
    report_json,
    week_start,
)
from waifu.network_history import NetworkStore, OutageTotal
from waifu.play_time import PlayTimeStore, PlayTotal, Session
from waifu.screen import ScreenImage

WIB = timezone(timedelta(hours=7))
SERVER = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 18, 19, 0, tzinfo=WIB)  # a Sunday, 20:00 on the Asia server
SINCE = datetime(2026, 10, 11, 18, 0, tzinfo=WIB)  # where the week of NOW's report starts
HSR, GENSHIN, ZZZ = GAMES["hsr"], GAMES["genshin"], GAMES["zzz"]
EVERY_GAME = ["hsr", "genshin", "zzz"]
NO_COOKIE = GachaError("no_cookie", "Cookie HoYoLAB belum diisi.")
OFFLINE = GachaError("network", "HoYoLAB tidak bisa dihubungi. Cek koneksi internet.")
HER_NAMES = [AssistantConfig().name, *AssistantConfig().wake_words]


def server(text: str) -> datetime:
    """A moment on the Asia server's clock, `YYYY-MM-DD HH:MM`."""
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=SERVER)


def pull(
    record_id: int, when: str, name: str = "Arrows", *, banner: int = 11, rarity: int = 3
) -> GachaRecord:
    """A pull made at `when`, server time as the history API writes it."""
    return GachaRecord(record_id, 800000001, banner, name, rarity, "Light Cone", when)


def hsr_pulls() -> list[GachaRecord]:
    """Last week a lost 50/50, this week the guaranteed Castorice and five more on the
    character banner, and one Light Cone pull."""
    return [
        *[pull(i, "2026-10-05 12:00:00") for i in range(1, 60)],
        pull(60, "2026-10-05 12:00:01", "Himeko", rarity=5),
        *[pull(i, "2026-10-12 21:00:00") for i in range(61, 90)],
        pull(90, "2026-10-12 21:00:01", "Castorice", rarity=5),
        *[pull(i, "2026-10-17 10:00:00") for i in range(91, 96)],
        pull(96, "2026-10-16 10:00:00", banner=12, rarity=4),
    ]


NEW_HSR_PULL = pull(97, "2026-10-18 19:30:00")  # 18:30 WIB, found by the refresh


def hsr_endgame(mode: str, *, has_data: bool = False, ends_at=None, **facts) -> HsrEndgame:
    known = {"stars": 0, "extra_stars": 0, "deepest_floor": None} | facts
    return HsrEndgame(mode, None, ends_at, has_data, battles=None, floors=(), **known)


def endgame(mode: str, *, has_data: bool = False, ends_at=None, **facts) -> Endgame:
    keys = ("season", "stars", "deepest", "difficulty", "rating", "score", "max_score")
    known = dict.fromkeys(keys) | facts
    return Endgame(
        mode, ends_at=ends_at, has_data=has_data, seconds=None, battles=None, stages=(), **known
    )


def unplayed_seasons() -> dict[str, HsrEndgame | Endgame]:
    """Every mode of every game, not played, ending in ten days."""
    later = NOW + timedelta(days=10)
    seasons: dict[str, HsrEndgame | Endgame] = {
        mode: hsr_endgame(mode, ends_at=later) for mode in GAME_MODES["hsr"]
    }
    for game in ("genshin", "zzz"):
        seasons |= {mode: endgame(mode, ends_at=later) for mode in GAME_MODES[game]}
    return seasons


class FakeSource:
    """HoyolabClient's endgame and calendar methods with fixed answers: `endgames` per mode (a
    GachaError there is that mode's), `calendars` per game, `failures` an exception raised by a
    whole call (`hsr_endgame`, `zzz_calendar`, ...), and `blocked` an event a call waits for.
    Each call is recorded with the thread it ran on."""

    def __init__(self, *, endgames=None, calendars=None, failures=None, blocked=None) -> None:
        self.endgames = unplayed_seasons() | (endgames or {})
        self.calendars = {
            "hsr": HsrCalendar(None, (), (), ()),
            "genshin": GameCalendar(None, (), (), ()),
            "zzz": GameCalendar(None, (), (), ()),
            **(calendars or {}),
        }
        self.failures: dict[str, Exception] = failures or {}
        self.blocked: dict[str, threading.Event] = blocked or {}
        self.calls: list[str] = []
        self.threads: dict[str, threading.Thread] = {}

    def hsr_endgame(self, modes):
        return self._endgame("hsr", modes)

    def genshin_endgame(self, modes):
        return self._endgame("genshin", modes)

    def zzz_endgame(self, modes):
        return self._endgame("zzz", modes)

    def hsr_calendar(self):
        return self._calendar("hsr")

    def genshin_calendar(self):
        return self._calendar("genshin")

    def zzz_calendar(self):
        return self._calendar("zzz")

    def _endgame(self, game: str, modes):
        modes = list(modes)
        self._call(f"{game}_endgame")
        assert modes == list(GAME_MODES[game])
        return {mode: self.endgames[mode] for mode in modes}

    def _calendar(self, game: str):
        self._call(f"{game}_calendar")
        return self.calendars[game]

    def _call(self, name: str) -> None:
        self.calls.append(name)
        self.threads[name] = threading.current_thread()
        if name in self.blocked:
            self.blocked[name].wait(5)
        if name in self.failures:
            raise self.failures[name]


class FakeHistory:
    """GachaHistory.report over the real store: a refresh reads the account in `accounts` for
    its game (else the one saved last) and saves the pulls in `found` for it, or fails with
    `problems[game]` (the saved history answers instead), or raises `failures[game]`;
    `blocked` holds an event a refresh waits for. Each call is recorded with the thread it
    ran on."""

    def __init__(
        self,
        store: GachaStore,
        *,
        accounts=None,
        found=None,
        problems=None,
        failures=None,
        blocked=None,
    ):
        self.store = store
        self.accounts: dict[str, int] = accounts or {}
        self.found: dict[str, list[GachaRecord]] = found or {}
        self.problems: dict[str, str] = problems or {}
        self.failures: dict[str, Exception] = failures or {}
        self.blocked: dict[str, threading.Event] = blocked or {}
        self.calls: list[tuple[str, threading.Thread]] = []

    def report(self, game_id: str, *, refresh: bool = True, full: bool = False) -> HistoryReport:
        assert (refresh, full) == (True, False)
        self.calls.append((game_id, threading.current_thread()))
        if game_id in self.blocked:
            self.blocked[game_id].wait(5)
        if game_id in self.failures:
            raise self.failures[game_id]
        game = GAMES[game_id]
        stored = self.store.load(game, uid=self.accounts.get(game_id))
        problem = self.problems.get(game_id)
        if problem is None and game_id in self.found:
            records = [*stored.records, *self.found[game_id]]
            self.store.save(game, stored.uid, records, complete=stored.complete)
        return HistoryReport(
            game, problem is None, problem, stored.complete, (), (), 0, None, uid=stored.uid
        )


class UnreadableStore:
    def load(self, game, uid=None):
        raise OSError("the disk hiccupped")


def make_store(tmp_path, *, complete: bool = True) -> GachaStore:
    store = GachaStore(tmp_path / "gacha")
    store.save(HSR, 800000001, hsr_pulls(), complete=complete)
    return store


def gather(
    tmp_path,
    *,
    games=("hsr",),
    store=None,
    history=None,
    source=None,
    codes=None,
    budget_s: float = 5.0,
    extra=None,
    play_time=None,
    income=None,
    network=None,
):
    store = store if store is not None else make_store(tmp_path)
    return gather_weekly_report(
        list(games),
        history=history if history is not None else FakeHistory(store),
        store=store,
        source=source if source is not None else FakeSource(),
        codes=codes,
        now=NOW,
        since=SINCE,
        offset=8,
        extra_standard=extra or {},
        budget_s=budget_s,
        play_time=play_time,
        income=income,
        network=network,
    )


def week_of(report, game_id: str):
    [week] = [week for week in report.games if week.game.id == game_id]
    return week


# --- the pulls and the pity ----------------------------------------------------------------------


def test_the_week_runs_from_its_start_up_to_now():
    report = gather_weekly_report(
        [],
        history=None,
        store=None,
        source=None,
        codes=None,
        now=NOW,
        since=SINCE,
        offset=8,
        extra_standard={},
    )

    assert (report.now, report.since, report.games) == (NOW, SINCE, ())


@pytest.mark.parametrize(
    ("now", "day", "at", "start"),
    [
        (NOW, "sunday", clock(19, 0), SINCE),
        (datetime(2026, 10, 18, 22, 30, tzinfo=WIB), "sunday", clock(19, 0), SINCE),
        (
            datetime(2026, 10, 18, 18, 59, tzinfo=WIB),
            "sunday",
            clock(19, 0),
            datetime(2026, 10, 11, 17, 59, tzinfo=WIB),
        ),
        (
            datetime(2026, 10, 21, 10, 0, tzinfo=WIB),
            "sunday",
            clock(19, 0),
            datetime(2026, 10, 14, 9, 0, tzinfo=WIB),
        ),
        (
            datetime(2026, 10, 24, 23, 0, tzinfo=WIB),
            "saturday",
            clock(20, 30),
            datetime(2026, 10, 17, 19, 30, tzinfo=WIB),
        ),
    ],
    ids=[
        "at the slot",
        "said late that evening",
        "just before the slot",
        "another day",
        "saturday",
    ],
)
def test_the_week_starts_7_days_and_an_hour_before_the_report_slot_else_before_now(
    now, day, at, start
):
    # HoYoverse lists a pull about an hour late, so a report reaches back that hour; counted
    # from the slot, not from when the report is said, consecutive reports always meet.
    assert week_start(now, day, at) == start


def test_this_weeks_pulls_and_the_pity_come_from_the_history_after_its_refresh(tmp_path):
    store = make_store(tmp_path)
    history = FakeHistory(store, found={"hsr": [NEW_HSR_PULL]})

    week = week_of(gather(tmp_path, store=store, history=history), "hsr")

    assert [game for game, _ in history.calls] == ["hsr"]
    character, light_cone = week.pulls
    assert (character.banner.type, character.count, light_cone.count) == (11, 36, 1)
    assert [(top.name, top.pulls, top.won_5050) for top in character.tops] == [
        ("Castorice", 30, None)  # guaranteed by last week's lost 50/50
    ]
    assert [(stats.banner.type, stats.pity, stats.guaranteed) for stats in week.pity] == [
        (11, 6, False),
        (12, 1, None),
    ]
    assert (week.complete, week.pulls_problem) == (True, None)


def test_the_pulls_and_the_pity_are_of_the_account_the_refresh_read(tmp_path):
    # Master's alt was saved last; back on the main account with no new pull, nothing is
    # saved, so the alt is still the latest file in the store.
    store = make_store(tmp_path)
    store.save(
        HSR, 800000009, [pull(i, "2026-10-17 10:00:00") for i in range(200, 210)], complete=True
    )
    history = FakeHistory(store, accounts={"hsr": 800000001})

    week = week_of(gather(tmp_path, store=store, history=history), "hsr")

    assert [banner.count for banner in week.pulls] == [35, 1]
    assert [stats.pity for stats in week.pity] == [5, 1]


def test_the_owners_off_banner_pick_counts_as_a_lost_fifty_fifty(tmp_path):
    store = GachaStore(tmp_path / "gacha")
    records = [pull(i, "2026-10-12 21:00:00") for i in range(1, 40)]
    records.append(pull(40, "2026-10-13 09:00:00", "Yunli", rarity=5))
    store.save(HSR, 800000001, records, complete=True)

    week = week_of(gather(tmp_path, store=store, extra={"hsr": ["Yunli"]}), "hsr")

    assert [(top.name, top.won_5050) for top in week.pulls[0].tops] == [("Yunli", False)]
    assert week.pity[0].guaranteed is True


def test_an_empty_week_has_no_pulls_but_still_the_pity(tmp_path):
    store = GachaStore(tmp_path / "gacha")
    store.save(HSR, 800000001, [pull(1, "2026-10-01 12:00:00")], complete=True)

    week = week_of(gather(tmp_path, store=store), "hsr")

    assert (week.pulls, [stats.pity for stats in week.pity]) == ((), [1])
    assert week.pulls_problem is None


def test_a_game_without_a_stored_history_is_not_refreshed_and_says_how_to_import_it(tmp_path):
    store = make_store(tmp_path)
    history = FakeHistory(store)

    report = gather(tmp_path, games=EVERY_GAME, store=store, history=history)

    assert [game for game, _ in history.calls] == ["hsr"]
    genshin = week_of(report, "genshin")
    assert (genshin.pulls, genshin.pity) == ((), ())
    assert genshin.pulls_problem == (
        "Belum ada riwayat Genshin yang tersimpan; jalankan "
        "'waifu gacha history genshin --full' sekali."
    )


def test_a_failed_refresh_tells_the_stored_numbers_with_its_problem(tmp_path):
    store = make_store(tmp_path)
    history = FakeHistory(
        store, problems={"hsr": "Buka riwayat warp di HSR sekali, lalu tanya lagi."}
    )

    week = week_of(gather(tmp_path, store=store, history=history), "hsr")

    assert [banner.count for banner in week.pulls] == [35, 1]
    assert week.pity[0].pity == 5
    assert week.pulls_problem == (
        "Riwayat HSR terbaru tidak terbaca, jadi angkanya dari riwayat tersimpan: "
        "Buka riwayat warp di HSR sekali, lalu tanya lagi."
    )


def test_a_refresh_that_raises_is_logged_and_the_stored_numbers_still_come(tmp_path, caplog):
    store = make_store(tmp_path)
    history = FakeHistory(store, failures={"hsr": RuntimeError("the store's disk is full")})

    with caplog.at_level(logging.ERROR, logger="waifu.gacha.weekly"):
        week = week_of(gather(tmp_path, store=store, history=history), "hsr")

    assert [banner.count for banner in week.pulls] == [35, 1]
    assert week.pulls_problem == (
        "Riwayat HSR terbaru tidak terbaca, jadi angkanya dari riwayat tersimpan: "
        "terjadi kesalahan."
    )
    assert "the store's disk is full" in caplog.text


def test_a_store_that_cannot_be_read_is_the_pulls_problem_and_the_rest_still_comes(
    tmp_path, caplog
):
    source = FakeSource(endgames={"memory_of_chaos": hsr_endgame("memory_of_chaos", stars=12)})

    with caplog.at_level(logging.ERROR, logger="waifu.gacha.weekly"):
        week = week_of(gather(tmp_path, store=UnreadableStore(), source=source), "hsr")

    assert (week.pulls, week.pity) == ((), ())
    assert week.pulls_problem == "Riwayat HSR tidak terbaca saat ini."
    assert week.endgame[0].stars == 12
    assert "the disk hiccupped" in caplog.text


def test_the_refreshes_run_one_game_after_another_on_one_worker(tmp_path):
    store = make_store(tmp_path)
    store.save(ZZZ, 1300000003, [pull(1, "2026-10-12 21:00:00", banner=2)], complete=True)
    store.save(GENSHIN, 700000001, [pull(1, "2026-10-12 21:00:00", banner=301)], complete=True)
    history = FakeHistory(store)

    gather(tmp_path, games=["zzz", "hsr", "genshin"], store=store, history=history)

    assert [game for game, _ in history.calls] == ["zzz", "hsr", "genshin"]
    assert len({thread for _, thread in history.calls}) == 1


def test_a_partial_history_keeps_its_hint(tmp_path):
    week = week_of(gather(tmp_path, store=make_store(tmp_path, complete=False)), "hsr")

    assert week.complete is False
    assert week.hint == (
        "Riwayat HSR baru terbaca sebagian. Jalankan 'waifu gacha history hsr --full' sekali "
        "untuk impor lengkap."
    )


def test_a_complete_or_missing_history_has_no_hint(tmp_path):
    report = gather(tmp_path, games=["hsr", "zzz"])

    assert [week.hint for week in report.games] == [None, None]


# --- the endgame -----------------------------------------------------------------------------


def test_the_endgame_of_every_mode_comes_in_the_games_order(tmp_path):
    chaos = hsr_endgame("memory_of_chaos", has_data=True, stars=30, deepest_floor="Stage 12")
    source = FakeSource(endgames={"memory_of_chaos": chaos})

    week = week_of(gather(tmp_path, source=source), "hsr")

    assert [season.mode for season in week.endgame] == list(GAME_MODES["hsr"])
    assert week.endgame[0] == chaos
    assert week.endgame_problem is None


def test_a_mode_that_cannot_be_read_is_named_and_the_others_still_come(tmp_path):
    shadow = GachaError("other", "Apocalyptic Shadow sedang bermasalah.")
    source = FakeSource(endgames={"apocalyptic_shadow": shadow})

    week = week_of(gather(tmp_path, source=source), "hsr")

    assert [season.mode for season in week.endgame] == [
        "memory_of_chaos",
        "pure_fiction",
        "anomaly_arbitration",
    ]
    assert week.endgame_problem == (
        "Apocalyptic Shadow tidak terbaca: Apocalyptic Shadow sedang bermasalah."
    )


def test_every_mode_failing_alike_is_said_once(tmp_path):
    source = FakeSource(endgames=dict.fromkeys(GAME_MODES["zzz"], OFFLINE))

    week = week_of(gather(tmp_path, games=["zzz"], source=source), "zzz")

    assert week.endgame == ()
    assert week.endgame_problem == (
        "Endgame ZZZ tidak terbaca: HoYoLAB tidak bisa dihubungi. Cek koneksi internet."
    )


# --- what ends within 7 days ---------------------------------------------------------------------


def test_only_the_banners_and_events_running_now_and_ending_within_7_days_come_soonest_first(
    tmp_path,
):
    started = NOW - timedelta(days=10)
    soon, later = NOW + timedelta(days=2), NOW + timedelta(days=5)

    def banner(name, starts_at, ends_at):
        return Banner("character", name, (name,), (), starts_at, ends_at)

    def event(name, starts_at, ends_at):
        return GameEvent(name, starts_at, ends_at, 1, 3, False)

    calendar = GameCalendar(
        "6.1",
        (
            banner("later", started, later),
            banner("soon", started, soon),
            banner("in 8 days", started, NOW + timedelta(days=8)),
            banner("over", started, NOW - timedelta(minutes=1)),
            banner("upcoming", NOW + timedelta(hours=1), soon),
            banner("no end", started, None),
            banner("exactly 7 days", started, NOW + timedelta(days=7)),
        ),
        (
            event("later", started, later),
            event("no end", started, None),
            event("soon", None, soon),
            event("over", started, NOW),
            event("upcoming", NOW + timedelta(hours=1), soon),
        ),
        (),
    )
    source = FakeSource(calendars={"genshin": calendar})

    week = week_of(gather(tmp_path, games=["genshin"], source=source), "genshin")

    assert [banner.name for banner in week.banners] == ["soon", "later", "exactly 7 days"]
    assert [event.name for event in week.events] == ["soon", "later"]
    assert week.calendar_problem is None


def test_hsr_names_its_banners_warps(tmp_path):
    ends = NOW + timedelta(days=3)
    calendar = HsrCalendar(
        "3.7",
        (HsrWarp("character", "Bloom of Death", ("Castorice",), (), None, ends),),
        (HsrEvent("Planar Fissure", None, ends, None, None, False),),
        (),
    )
    week = week_of(gather(tmp_path, source=FakeSource(calendars={"hsr": calendar})), "hsr")

    assert [warp.featured for warp in week.banners] == [("Castorice",)]
    assert [event.name for event in week.events] == ["Planar Fissure"]


def test_hoyolab_failing_for_one_game_leaves_the_other_games_whole(tmp_path):
    source = FakeSource(
        failures={"genshin_endgame": OFFLINE, "genshin_calendar": OFFLINE},
        endgames={"memory_of_chaos": hsr_endgame("memory_of_chaos", stars=9)},
    )

    report = gather(tmp_path, games=EVERY_GAME, source=source)

    genshin = week_of(report, "genshin")
    assert (genshin.endgame, genshin.banners, genshin.events) == ((), (), ())
    assert genshin.endgame_problem == (
        "Endgame Genshin tidak terbaca: HoYoLAB tidak bisa dihubungi. Cek koneksi internet."
    )
    assert genshin.calendar_problem == (
        "Kalender event Genshin tidak terbaca: HoYoLAB tidak bisa dihubungi. Cek koneksi internet."
    )
    hsr = week_of(report, "hsr")
    assert (hsr.endgame[0].stars, hsr.endgame_problem, hsr.calendar_problem) == (9, None, None)
    assert week_of(report, "zzz").calendar_problem is None


def test_an_unexpected_failure_of_a_read_is_logged_and_becomes_its_problem(tmp_path, caplog):
    source = FakeSource(failures={"hsr_calendar": KeyError("warp_type")})

    with caplog.at_level(logging.ERROR, logger="waifu.gacha.weekly"):
        week = week_of(gather(tmp_path, source=source), "hsr")

    assert week.calendar_problem == "Kalender event HSR tidak terbaca: terjadi kesalahan."
    assert "warp_type" in caplog.text
    assert week.endgame_problem is None


# --- the time budget -----------------------------------------------------------------------------


def test_a_section_not_done_within_the_budget_ran_out_of_time_and_the_rest_is_told(tmp_path):
    store = make_store(tmp_path)
    hanging = threading.Event()
    history = FakeHistory(store, found={"hsr": [NEW_HSR_PULL]}, blocked={"hsr": hanging})
    source = FakeSource(blocked={"genshin_calendar": hanging})
    try:
        started = time.monotonic()
        report = gather(
            tmp_path,
            games=["hsr", "genshin"],
            store=store,
            history=history,
            source=source,
            budget_s=0.2,
        )
        took = time.monotonic() - started
    finally:
        hanging.set()

    assert took < 3
    hsr, genshin = report.games
    assert [banner.count for banner in hsr.pulls] == [35, 1]  # the store as it was
    assert hsr.pulls_problem == (
        "Riwayat HSR terbaru tidak terbaca, jadi angkanya dari riwayat tersimpan: waktunya habis."
    )
    assert genshin.calendar_problem == "Kalender event Genshin tidak terbaca: waktunya habis."
    assert (hsr.endgame_problem, hsr.calendar_problem, genshin.endgame_problem) == (
        None,
        None,
        None,
    )


def test_a_read_still_running_after_the_budget_never_holds_up_the_exit(tmp_path):
    # Python joins a thread pool's workers at exit, so a late HoYoLAB or history read would
    # keep `waifu gacha report`, or a closing `waifu run`, waiting long after the 30 s.
    store = make_store(tmp_path)
    hanging = threading.Event()
    history = FakeHistory(store, blocked={"hsr": hanging})
    source = FakeSource(blocked={"hsr_endgame": hanging})
    try:
        gather(tmp_path, store=store, history=history, source=source, budget_s=0.2)
        [(_, refreshing)] = history.calls
        reading = source.threads["hsr_endgame"]
        assert refreshing.is_alive() and reading.is_alive()
        assert (refreshing.daemon, reading.daemon) == (True, True)
    finally:
        hanging.set()


def test_a_read_not_started_within_the_budget_never_starts(tmp_path):
    store = make_store(tmp_path)
    store.save(GENSHIN, 700000001, [pull(1, "2026-10-12 21:00:00", banner=301)], complete=True)
    hanging = threading.Event()
    history = FakeHistory(store, blocked={"hsr": hanging})
    source = FakeSource(blocked={"hsr_endgame": hanging})
    try:
        report = gather(
            tmp_path,
            games=["hsr", "genshin"],
            store=store,
            history=history,
            source=source,
            budget_s=0.2,
        )
    finally:
        hanging.set()
    [(_, refreshing)] = history.calls
    reading = source.threads["hsr_endgame"]
    refreshing.join(5)
    reading.join(5)

    assert [game for game, _ in history.calls] == ["hsr"]  # Genshin's refresh was next
    assert "hsr_calendar" not in source.calls  # HSR's calendar came after its endgame
    genshin = week_of(report, "genshin")
    assert genshin.pulls_problem == (
        "Riwayat Genshin terbaru tidak terbaca, jadi angkanya dari riwayat tersimpan: "
        "waktunya habis."
    )
    assert week_of(report, "hsr").calendar_problem == (
        "Kalender event HSR tidak terbaca: waktunya habis."
    )


# --- the play time -------------------------------------------------------------------------------


def play_store(tmp_path, games=("hsr", "genshin")) -> PlayTimeStore:
    """HSR this week (one session reaching back over the week's start, one on Thursday, and
    one open now) and Genshin only before the week, with HSR and Genshin tracked."""
    store = PlayTimeStore(
        tmp_path / "playtime.json", games=list(games), keep_days=90, now=lambda: NOW
    )
    week_starts = SINCE + LISTING_LAG  # Sunday 19:00: play time has no listing lag
    thursday = datetime(2026, 10, 15, 21, 0, tzinfo=WIB)
    store.save(
        [Session("hsr", NOW - timedelta(hours=1), NOW - timedelta(minutes=1))],
        [
            Session("hsr", week_starts - timedelta(minutes=30), week_starts + timedelta(hours=2)),
            Session("hsr", thursday, thursday + timedelta(minutes=140)),
            Session("genshin", week_starts - timedelta(hours=3), week_starts - timedelta(hours=1)),
        ],
    )
    return store


def test_the_week_s_play_time_comes_per_tracked_game_when_a_source_is_given(tmp_path):
    report = gather(tmp_path, games=EVERY_GAME, play_time=play_store(tmp_path))

    # Two hours of the first, the whole second, and 59 minutes of the open one.
    hsr = week_of(report, "hsr")
    assert (hsr.play, hsr.play_problem) == (PlayTotal((120 + 140 + 59) * 60, 3), None)
    assert week_of(report, "genshin").play == PlayTotal(0, 0)  # before the week only
    zzz = week_of(report, "zzz")
    assert (zzz.play, zzz.play_problem) == (None, None)  # not tracked
    assert hsr.problems == ()


def test_without_a_play_time_source_there_is_no_play_time(tmp_path):
    week = week_of(gather(tmp_path), "hsr")

    assert (week.play, week.play_problem) == (None, None)


def test_play_time_that_cannot_be_read_is_each_tracked_game_s_problem(tmp_path, caplog):
    class UnreadablePlayTime:
        games = ("hsr", "zzz")

        def totals(self, since, until):
            raise OSError("the disk is gone")

    with caplog.at_level(logging.ERROR, logger="waifu.gacha.weekly"):
        report = gather(tmp_path, games=EVERY_GAME, play_time=UnreadablePlayTime())

    hsr, genshin = week_of(report, "hsr"), week_of(report, "genshin")
    assert (hsr.play, hsr.play_problem) == (None, "Jam main HSR tidak terbaca saat ini.")
    assert "Jam main HSR tidak terbaca saat ini." in hsr.problems
    assert (genshin.play, genshin.play_problem) == (None, None)
    assert week_of(report, "zzz").play_problem == "Jam main ZZZ tidak terbaca saat ini."
    assert "the disk is gone" in caplog.text


def test_play_time_alone_is_nothing_to_report(tmp_path):
    report = gather(
        tmp_path,
        store=GachaStore(tmp_path / "empty"),
        source=FakeSource(failures=no_cookie_anywhere()),
        play_time=play_store(tmp_path),
    )

    assert report.readable is False


def test_the_json_holds_the_play_time_only_for_a_tracked_game(tmp_path):
    data = report_json(gather(tmp_path, games=EVERY_GAME, play_time=play_store(tmp_path)))
    hsr, genshin, zzz = data["games"]

    assert hsr["play_time"] == {"hours": 5, "minutes": 19, "sessions": 3}
    assert hsr["play_time_problem"] is None
    assert genshin["play_time"] == {"hours": 0, "minutes": 0, "sessions": 0}
    assert "play_time" not in zzz and "play_time_problem" not in zzz


def test_the_json_holds_no_play_time_without_a_source(tmp_path):
    [data] = report_json(gather(tmp_path))["games"]

    assert "play_time" not in data and "play_time_problem" not in data


# --- the internet outages ------------------------------------------------------------------------


def network_store(tmp_path) -> NetworkStore:
    """Down from 18:58 to 19:04 across the week's start (Sunday 19:00), 10 minutes on Thursday,
    and since 18:50 today until its last failed poll at 18:55, still open."""
    store = NetworkStore(tmp_path / "network.json", keep_days=7, now=lambda: NOW)
    week_starts = SINCE + LISTING_LAG
    thursday = datetime(2026, 10, 15, 21, 0, tzinfo=WIB)
    for start, minutes in ((week_starts - timedelta(minutes=2), 6), (thursday, 10)):
        store.begin(start, start, "offline")
        store.end(start + timedelta(minutes=minutes))
    store.begin(NOW - timedelta(minutes=10), NOW - timedelta(minutes=5), "router")
    return store


class UnreadableNetwork:
    def record(self):
        raise OSError("the disk is gone")


def test_the_week_s_internet_outages_come_when_a_source_is_given(tmp_path):
    report = gather(tmp_path, network=network_store(tmp_path))

    assert report.network == OutageTotal(3, (4 + 10 + 5) * 60)
    assert report.network_problem is None


def test_without_a_network_source_there_are_no_outages(tmp_path):
    report = gather(tmp_path)

    assert (report.network, report.network_problem) == (None, None)


def test_an_internet_history_that_cannot_be_read_is_the_network_problem(tmp_path, caplog):
    with caplog.at_level(logging.ERROR, logger="waifu.gacha.weekly"):
        report = gather(tmp_path, network=UnreadableNetwork())

    assert report.network is None
    assert report.network_problem == "Riwayat internet tidak terbaca saat ini."
    assert "the disk is gone" in caplog.text


def test_the_json_holds_the_outages_only_when_there_were_any(tmp_path):
    quiet = NetworkStore(tmp_path / "quiet.json", keep_days=7, now=lambda: NOW)

    data = report_json(gather(tmp_path, network=network_store(tmp_path)))

    assert data["network"] == {"outages": 3, "minutes": 19}
    assert "network_problem" not in data
    for without in (report_json(gather(tmp_path, network=quiet)), report_json(gather(tmp_path))):
        assert "network" not in without and "network_problem" not in without


def test_the_json_names_an_internet_history_that_cannot_be_read(tmp_path):
    data = report_json(gather(tmp_path, network=UnreadableNetwork()))

    assert "network" not in data
    assert data["network_problem"] == "Riwayat internet tidak terbaca saat ini."


def test_outages_alone_are_nothing_to_report(tmp_path):
    report = gather(
        tmp_path,
        store=GachaStore(tmp_path / "empty"),
        source=FakeSource(failures=no_cookie_anywhere()),
        network=network_store(tmp_path),
    )

    assert report.readable is False


# --- this month's income -------------------------------------------------------------------------

OCTOBER = date(2026, 10, 1)


def monthly_income(game: str, month: date) -> GameIncome:
    earned = {"hsr": 4800, "genshin": 5400, "zzz": 3200}[game]
    return GameIncome(
        game=game,
        month=month,
        earned=earned,
        others=(IncomeCurrency("Mora", 1234567),) if game == "genshin" else (),
        last_month=None if game == "zzz" else 9000,
        change_percent=None if game == "zzz" else -40,
        sources=(IncomeCategory("Events", 1600, 30),),
        today=None if game == "zzz" else 60,
        yesterday=120 if game == "hsr" else None,
    )


class FakeIncome:
    """HoyolabClient.income with fixed answers: each game's income, or the GachaError in
    `failures`; `failure` is an exception raised by the whole call, and `blocked` an event the
    call waits for. Each call is recorded with the thread it ran on."""

    def __init__(self, *, failures=None, failure=None, blocked=None) -> None:
        self.failures: dict[str, GachaError] = failures or {}
        self.failure: Exception | None = failure
        self.blocked: threading.Event | None = blocked
        self.calls: list[tuple[list[str], date, bool]] = []
        self.threads: list[threading.Thread] = []

    def income(self, games, month, *, this_month):
        games = list(games)
        self.calls.append((games, month, this_month))
        self.threads.append(threading.current_thread())
        if self.blocked is not None:
            self.blocked.wait(5)
        if self.failure is not None:
            raise self.failure
        return {game: self.failures.get(game) or monthly_income(game, month) for game in games}


def test_this_month_s_income_comes_per_game_from_one_call_when_a_source_is_given(tmp_path):
    income = FakeIncome()

    report = gather(tmp_path, games=EVERY_GAME, income=income)

    assert income.calls == [(EVERY_GAME, OCTOBER, True)]
    hsr = week_of(report, "hsr")
    assert (hsr.income, hsr.income_problem) == (monthly_income("hsr", OCTOBER), None)
    assert week_of(report, "zzz").income.earned == 3200
    assert hsr.problems == ()


def test_the_income_is_read_on_its_own_worker_beside_the_hoyolab_lanes(tmp_path):
    income = FakeIncome()
    source = FakeSource()

    gather(tmp_path, source=source, income=income)

    [worker] = income.threads
    assert worker.name == "weekly-income" and worker.daemon
    assert worker is not source.threads["hsr_endgame"]


def test_the_income_month_turns_at_midnight_server_time():
    # 23:30 WIB on 31 October is already 1 November on the Asia server.
    income = FakeIncome()
    now = datetime(2026, 10, 31, 23, 30, tzinfo=WIB)

    gather_weekly_report(
        [],
        history=None,
        store=None,
        source=None,
        codes=None,
        now=now,
        since=now - timedelta(days=7),
        offset=8,
        extra_standard={},
        income=income,
    )

    assert income.calls == [([], date(2026, 11, 1), True)]


def test_without_an_income_source_there_is_no_income(tmp_path):
    week = week_of(gather(tmp_path), "hsr")

    assert (week.income, week.income_problem) == (None, None)


def test_a_game_whose_income_could_not_be_read_has_its_problem_and_the_others_still_come(
    tmp_path,
):
    income = FakeIncome(failures={"zzz": OFFLINE})

    report = gather(tmp_path, games=EVERY_GAME, income=income)

    zzz = week_of(report, "zzz")
    problem = "Pendapatan ZZZ bulan ini tidak terbaca: " + OFFLINE.message
    assert (zzz.income, zzz.income_problem) == (None, problem)
    assert problem in zzz.problems
    assert week_of(report, "hsr").income.earned == 4800


def test_an_income_call_that_fails_as_a_whole_is_every_game_s_problem(tmp_path, caplog):
    with caplog.at_level(logging.ERROR, logger="waifu.gacha.weekly"):
        report = gather(
            tmp_path, games=["hsr", "zzz"], income=FakeIncome(failure=RuntimeError("boom"))
        )

    assert [week.income_problem for week in report.games] == [
        "Pendapatan HSR bulan ini tidak terbaca: terjadi kesalahan.",
        "Pendapatan ZZZ bulan ini tidak terbaca: terjadi kesalahan.",
    ]
    assert "boom" in caplog.text


def test_income_not_read_within_the_budget_ran_out_of_time_and_the_rest_is_told(tmp_path):
    hanging = threading.Event()
    try:
        report = gather(tmp_path, income=FakeIncome(blocked=hanging), budget_s=0.2)
    finally:
        hanging.set()

    hsr = week_of(report, "hsr")
    assert hsr.income_problem == "Pendapatan HSR bulan ini tidak terbaca: waktunya habis."
    assert (hsr.endgame_problem, hsr.calendar_problem) == (None, None)


def test_income_alone_is_nothing_to_report(tmp_path):
    report = gather(
        tmp_path,
        store=GachaStore(tmp_path / "empty"),
        source=FakeSource(failures=no_cookie_anywhere()),
        income=FakeIncome(),
    )

    assert report.readable is False


def test_the_json_holds_this_month_s_income_only_with_a_source(tmp_path):
    income = FakeIncome(failures={"genshin": OFFLINE})

    hsr, genshin, zzz = report_json(gather(tmp_path, games=EVERY_GAME, income=income))["games"]

    assert hsr["income_this_month"] == {
        "month": "2026-10",
        "currency": "Stellar Jade",
        "earned": 4800,
        "pulls": 30,
        "today": 60,
    }
    assert hsr["income_problem"] is None
    assert genshin["income_this_month"] is None
    assert genshin["income_problem"] == "Pendapatan Genshin bulan ini tidak terbaca: " + (
        OFFLINE.message
    )
    assert zzz["income_this_month"] == {
        "month": "2026-10",
        "currency": "Polychrome",
        "earned": 3200,
        "pulls": 20,
    }


def test_the_json_holds_no_income_without_a_source(tmp_path):
    [data] = report_json(gather(tmp_path))["games"]

    assert "income_this_month" not in data and "income_problem" not in data


def test_the_description_names_this_month_s_income_when_it_is_read(tmp_path):
    [spec] = make_tools(tmp_path, income=FakeIncome()).specs()

    assert "premium currency earned so far this month" in spec.description
    assert "`income_this_month`" in spec.description
    assert "worth in pulls" in spec.description


def test_the_description_tells_the_income_only_for_planning_never_judging_the_amount(tmp_path):
    # gacha_income's own rule is not in this tool's description, and Master asking how the
    # week went has no WEEKLY_REPORT_PROMPT around it.
    [spec] = make_tools(tmp_path, income=FakeIncome()).specs()

    assert (
        "Present banners, events, and this month's income only as information for planning: "
        "never suggest pulling, spending money, or topping up, and never judge the amount earned."
    ) in spec.description


def test_the_description_leaves_the_income_out_without_a_source(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    assert "income" not in spec.description


def test_the_tool_s_report_holds_this_month_s_income_when_it_is_read(tmp_path):
    result, is_error = make_tools(tmp_path, income=FakeIncome()).run("weekly_report", {})

    assert is_error is False
    hsr = json.loads(result)["games"][0]
    assert hsr["income_this_month"]["earned"] == 4800


# --- the redeem codes ----------------------------------------------------------------------------


def test_the_codes_not_redeemed_yet_come_when_they_are_tracked(tmp_path):
    codes = CodeStore(tmp_path / "redeem_codes.json")
    listed = [(f"HSRCODE{n}", "60 Stellar Jade") for n in range(7)]
    codes.add("hsr", listed, NOW)

    report = gather(tmp_path, games=["hsr", "zzz"], codes=codes)

    assert week_of(report, "hsr").codes == tuple(f"HSRCODE{n}" for n in range(5))
    assert week_of(report, "zzz").codes == ()
    assert week_of(report, "hsr").codes_problem is None


def test_without_a_code_store_the_codes_are_not_tracked(tmp_path):
    week = week_of(gather(tmp_path), "hsr")

    assert (week.codes, week.codes_problem) == (None, None)


def test_codes_that_cannot_be_read_are_the_codes_problem(tmp_path, caplog):
    class UnreadableCodes:
        def unredeemed(self, game):
            raise OSError("locked")

    with caplog.at_level(logging.ERROR, logger="waifu.gacha.weekly"):
        week = week_of(gather(tmp_path, codes=UnreadableCodes()), "hsr")

    assert (week.codes, week.codes_problem) == ((), "Kode redeem HSR tidak terbaca saat ini.")
    assert "locked" in caplog.text


# --- whether there is anything to tell -----------------------------------------------------------


def test_nothing_readable_at_all_is_not_a_report(tmp_path):
    source = FakeSource(
        failures={
            f"{game}_{part}": NO_COOKIE for game in EVERY_GAME for part in ("endgame", "calendar")
        }
    )

    report = gather(tmp_path, games=EVERY_GAME, store=GachaStore(tmp_path / "empty"), source=source)

    assert report.readable is False


@pytest.mark.parametrize(
    "readable_part", ["history", "endgame", "calendar"], ids=["history", "endgame", "calendar"]
)
def test_one_readable_part_of_one_game_is_a_report(tmp_path, readable_part):
    failures = {
        f"{game}_{part}": NO_COOKIE for game in EVERY_GAME for part in ("endgame", "calendar")
    }
    if readable_part != "history":
        del failures[f"zzz_{readable_part}"]
    store = make_store(tmp_path) if readable_part == "history" else GachaStore(tmp_path / "empty")

    report = gather(tmp_path, games=EVERY_GAME, store=store, source=FakeSource(failures=failures))

    assert report.readable is True


# --- the JSON for the brain ----------------------------------------------------------------------


def full_hsr_week(tmp_path):
    store = make_store(tmp_path, complete=False)
    endgames = {
        "memory_of_chaos": hsr_endgame(
            "memory_of_chaos",
            has_data=True,
            stars=30,
            extra_stars=2,
            deepest_floor="Stage 12",
            ends_at=server("2026-10-20 04:00"),
        ),
        "pure_fiction": hsr_endgame("pure_fiction", ends_at=server("2026-10-27 04:00")),
        "apocalyptic_shadow": GachaError("other", "Apocalyptic Shadow sedang bermasalah."),
        "anomaly_arbitration": hsr_endgame("anomaly_arbitration"),
    }
    calendar = HsrCalendar(
        "3.7",
        (
            HsrWarp(
                "character",
                "Bloom of Death",
                ("Castorice",),
                ("Pela", "Lynx"),
                server("2026-10-01 12:00"),
                server("2026-10-21 15:00"),
            ),
        ),
        (HsrEvent("Planar Fissure", None, server("2026-10-20 04:00"), 3, 7, False),),
        (),
    )
    source = FakeSource(endgames=endgames, calendars={"hsr": calendar})
    codes = CodeStore(tmp_path / "redeem_codes.json")
    codes.add("hsr", [("STARRAILGIFT", "50 Stellar Jade")], NOW)
    history = FakeHistory(store, found={"hsr": [NEW_HSR_PULL]})
    return gather(tmp_path, store=store, history=history, source=source, codes=codes)


def test_the_json_holds_the_whole_week_of_a_game_in_local_time(tmp_path):
    data = report_json(full_hsr_week(tmp_path))

    assert data == {
        "as_of": "2026-10-18 19:00",
        "since": "2026-10-11 18:00",
        "note": HISTORY_NOTE,
        "games": [
            {
                "game": "hsr",
                "name": "Honkai: Star Rail",
                "pulls_this_week": {
                    "count": 37,
                    "banners": [
                        {"banner": "Character Event Warp", "count": 36},
                        {"banner": "Light Cone Event Warp", "count": 1},
                    ],
                    "top_pulls": [
                        {
                            "banner": "Character Event Warp",
                            "name": "Castorice",
                            "rarity": "5★",
                            "pulls": 30,
                            "won_5050": None,
                        }
                    ],
                },
                "pity": [
                    {
                        "banner": "Character Event Warp",
                        "pity": 6,
                        "hard_pity": 90,
                        "to_hard_pity": 84,
                        "guaranteed": False,
                    },
                    {
                        "banner": "Light Cone Event Warp",
                        "pity": 1,
                        "hard_pity": 80,
                        "to_hard_pity": 79,
                    },
                ],
                "pulls_problem": None,
                "hint": (
                    "Riwayat HSR baru terbaca sebagian. Jalankan 'waifu gacha history hsr "
                    "--full' sekali untuk impor lengkap."
                ),
                "endgame": [
                    {
                        "mode": "memory_of_chaos",
                        "name": "Memory of Chaos",
                        "has_data": True,
                        "stars": 30,
                        "extra_stars": 2,
                        "deepest_floor": "Stage 12",
                        "ends": "2026-10-20 03:00",
                        "ends_in_hours": 32,
                        "ends_within_7_days": True,
                    },
                    {
                        "mode": "pure_fiction",
                        "name": "Pure Fiction",
                        "has_data": False,
                        "stars": 0,
                        "extra_stars": 0,
                        "ends": "2026-10-27 03:00",
                        "ends_in_hours": 200,
                        "ends_within_7_days": False,
                    },
                    {
                        "mode": "anomaly_arbitration",
                        "name": "Anomaly Arbitration",
                        "has_data": False,
                        "stars": 0,
                        "extra_stars": 0,
                        "ends_within_7_days": False,
                    },
                ],
                "endgame_problem": (
                    "Apocalyptic Shadow tidak terbaca: Apocalyptic Shadow sedang bermasalah."
                ),
                "ending_soon": {
                    "banners": [
                        {
                            "type": "character",
                            "name": "Bloom of Death",
                            "featured": ["Castorice"],
                            "ends": "2026-10-21 14:00",
                            "ends_in_hours": 67,
                        }
                    ],
                    "events": [
                        {
                            "name": "Planar Fissure",
                            "ends": "2026-10-20 03:00",
                            "ends_in_hours": 32,
                            "current": 3,
                            "total": 7,
                            "done": False,
                        }
                    ],
                },
                "calendar_problem": None,
                "unredeemed_codes": ["STARRAILGIFT"],
                "codes_problem": None,
            }
        ],
    }


def test_the_json_never_holds_spending(tmp_path):
    text = str(report_json(full_hsr_week(tmp_path)))

    for word in ("currency", "spent", "Stellar Jade", "160"):
        assert word not in text


def test_a_game_without_a_history_has_no_pulls_this_week_and_no_codes_when_untracked(tmp_path):
    [data] = report_json(gather(tmp_path, games=["zzz"]))["games"]

    assert data["pulls_this_week"] is None
    assert data["pity"] == []
    assert data["pulls_problem"].startswith("Belum ada riwayat ZZZ")
    assert "hint" not in data
    assert "unredeemed_codes" not in data and "codes_problem" not in data


def test_a_zzz_s_rank_is_an_s_in_the_json(tmp_path):
    store = GachaStore(tmp_path / "gacha")
    records = [pull(i, "2026-10-12 21:00:00", banner=2) for i in range(1, 11)]
    records.append(pull(11, "2026-10-12 21:00:01", "Ellen", banner=2, rarity=4))
    store.save(ZZZ, 1300000003, records, complete=True)

    [data] = report_json(gather(tmp_path, games=["zzz"], store=store))["games"]

    assert [(top["name"], top["rarity"]) for top in data["pulls_this_week"]["top_pulls"]] == [
        ("Ellen", "S")
    ]


def test_a_genshin_season_already_over_says_ended_and_never_ends_this_week(tmp_path):
    abyss = endgame(
        "spiral_abyss",
        has_data=True,
        stars=36,
        deepest="12-3",
        ends_at=NOW - timedelta(hours=3),
    )
    theater = endgame(
        "imaginarium_theater",
        has_data=True,
        difficulty="Visionary",
        ends_at=NOW + timedelta(days=1),
    )
    source = FakeSource(endgames={"spiral_abyss": abyss, "imaginarium_theater": theater})

    [data] = report_json(gather(tmp_path, games=["genshin"], source=source))["games"]

    spiral, imaginarium, _ = data["endgame"]
    assert spiral == {
        "mode": "spiral_abyss",
        "name": "Spiral Abyss",
        "has_data": True,
        "stars": 36,
        "deepest": "12-3",
        "ends": "2026-10-18 16:00",
        "ended": True,
        "ends_within_7_days": False,
    }
    assert imaginarium["difficulty"] == "Visionary"
    assert (imaginarium["ends_in_hours"], imaginarium["ends_within_7_days"]) == (24, True)


def test_the_json_keeps_each_game_small(tmp_path):
    store = GachaStore(tmp_path / "gacha")
    records = []
    for n in range(12):  # twelve 5-stars this week, ten pulls apart
        records += [pull(10 * n + i, "2026-10-12 21:00:00") for i in range(1, 10)]
        records.append(pull(10 * n + 10, f"2026-10-13 {n:02d}:00:00", f"Unit {n}", rarity=5))
    store.save(HSR, 800000001, records, complete=True)
    ends = NOW + timedelta(days=1)
    calendar = HsrCalendar(
        None,
        tuple(HsrWarp("character", f"Warp {n}", (), (), None, ends) for n in range(12)),
        tuple(HsrEvent("E" * 80 + str(n), None, ends, None, None, False) for n in range(12)),
        (),
    )

    [data] = report_json(
        gather(tmp_path, store=store, source=FakeSource(calendars={"hsr": calendar}))
    )["games"]

    tops = data["pulls_this_week"]["top_pulls"]
    assert [top["name"] for top in tops] == [f"Unit {n}" for n in range(11, 1, -1)]
    assert len(data["ending_soon"]["banners"]) == 8
    assert len(data["ending_soon"]["events"]) == 10
    assert {len(event["name"]) for event in data["ending_soon"]["events"]} == {60}


# --- the tool ------------------------------------------------------------------------------------


def make_tools(
    tmp_path,
    *,
    games=EVERY_GAME,
    store=None,
    source=None,
    codes=None,
    now=NOW,
    play_time=None,
    income=None,
    network=None,
):
    store = store if store is not None else make_store(tmp_path)
    return WeeklyReportTools(
        games,
        history=FakeHistory(store),
        store=store,
        source=source if source is not None else FakeSource(),
        codes=codes,
        report_day="sunday",
        report_time=clock(19, 0),
        server_utc_offset=8,
        extra_standard={},
        now=lambda: now,
        budget_s=5.0,
        play_time=play_time,
        income=income,
        network=network,
    )


def no_cookie_anywhere() -> dict[str, GachaError]:
    return {f"{game}_{part}": NO_COOKIE for game in EVERY_GAME for part in ("endgame", "calendar")}


def test_one_tool_that_takes_no_arguments_so_it_needs_no_strict(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    assert spec.name == "weekly_report"
    assert spec.input_schema == {"type": "object", "properties": {}, "additionalProperties": False}
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "weekly_report"


def test_the_description_says_what_it_holds_when_to_use_it_and_never_to_push_spending(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    for guidance in [
        "Honkai: Star Rail = hsr, Genshin Impact = genshin, Zenless Zone Zero = zzz",
        "the pulls of the last 7 days",
        "whether the 50/50 was won",
        "pity",
        "endgame",
        "end within 7 days",
        "your own weekly game report",
        "when Master asks how their week in these games went",
        "_problem",
        "about an hour late",
        "never suggest pulling, spending money, or topping up",
    ]:
        assert guidance in spec.description
    assert "redeem codes" not in spec.description
    assert "the time each game was open" not in spec.description
    assert "lecture" not in spec.description


def test_the_description_names_the_play_time_when_it_is_counted(tmp_path):
    [spec] = make_tools(tmp_path, play_time=play_store(tmp_path)).specs()

    assert "the time each game was open in the last 7 days" in spec.description
    assert "minimized or idle" in spec.description


def test_the_description_tells_the_play_time_as_news_never_as_a_lecture(tmp_path):
    # The play_time tool's own rule never reaches her report turn: that tool is shut there.
    [spec] = make_tools(tmp_path, play_time=play_store(tmp_path)).specs()

    assert "Tell the play time only as news" in spec.description
    assert "never judge whether it is too much" in spec.description
    assert "never scold or lecture Master about how long they play" in spec.description


def test_the_tool_s_report_holds_the_play_time_when_it_is_counted(tmp_path):
    tools = make_tools(tmp_path, play_time=play_store(tmp_path))

    result, is_error = tools.run("weekly_report", {})

    assert is_error is False
    hsr = json.loads(result)["games"][0]
    assert hsr["play_time"] == {"hours": 5, "minutes": 19, "sessions": 3}


def test_the_description_names_the_internet_outages_when_they_are_kept(tmp_path):
    [spec] = make_tools(tmp_path, network=network_store(tmp_path)).specs()
    [without] = make_tools(tmp_path).specs()

    told = (
        "`network`, there only when it happened, counts the times Master's internet was down "
        "in the last 7 days while `waifu run` watched it, with their minutes"
    )
    assert told in spec.description
    assert "`network`" not in without.description


def test_the_tool_s_report_holds_the_week_s_internet_outages(tmp_path):
    tools = make_tools(tmp_path, network=network_store(tmp_path))

    result, is_error = tools.run("weekly_report", {})

    assert is_error is False
    assert json.loads(result)["network"] == {"outages": 3, "minutes": 19}


def test_the_description_names_only_the_configured_games_and_the_codes_when_tracked(tmp_path):
    codes = CodeStore(tmp_path / "redeem_codes.json")
    [spec] = make_tools(tmp_path, games=["zzz"], codes=codes).specs()

    assert "Zenless Zone Zero = zzz" in spec.description
    assert "Star Rail" not in spec.description and "Genshin" not in spec.description
    assert "the redeem codes not redeemed yet" in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching(tmp_path):
    tools = make_tools(tmp_path)
    before = tools.specs()

    tools.run("weekly_report", {})

    assert tools.specs() == before


def test_the_report_is_the_weeks_compact_json_up_to_the_clock(tmp_path):
    result, is_error = make_tools(tmp_path).run("weekly_report", {})

    data = json.loads(result)
    assert is_error is False
    assert (data["as_of"], data["since"]) == ("2026-10-18 19:00", "2026-10-11 18:00")
    assert [game["game"] for game in data["games"]] == EVERY_GAME
    assert data["games"][0]["pulls_this_week"]["count"] == 36
    assert '"rarity":"5★"' in result


def test_a_pull_listed_after_a_report_is_told_in_the_next_one_however_late_that_is_said(
    tmp_path,
):
    # Castorice, pulled at 18:30 WIB, is listed by HoYoverse only after the 19:00 report;
    # the next report, said at 22:00 a week later, still has her.
    store = GachaStore(tmp_path / "gacha")
    records = [pull(i, "2026-10-12 21:00:00") for i in range(1, 30)]
    records.append(pull(30, "2026-10-18 19:30:00", "Castorice", rarity=5))
    store.save(HSR, 800000001, records, complete=True)
    next_week_late = datetime(2026, 10, 25, 22, 0, tzinfo=WIB)

    result, _ = make_tools(tmp_path, games=["hsr"], store=store, now=next_week_late).run(
        "weekly_report", {}
    )

    data = json.loads(result)
    assert data["since"] == "2026-10-18 18:00"
    [hsr] = data["games"]
    assert [top["name"] for top in hsr["pulls_this_week"]["top_pulls"]] == ["Castorice"]


def test_nothing_readable_is_an_error_result_so_her_apology_is_never_said_unasked(tmp_path):
    tools = make_tools(
        tmp_path,
        store=GachaStore(tmp_path / "empty"),
        source=FakeSource(failures=no_cookie_anywhere()),
    )

    result, is_error = tools.run("weekly_report", {})

    assert is_error is True
    data = json.loads(result)
    assert data["ok"] is False
    assert data["message"] == (
        "Laporan game minggu ini tidak bisa dibuat: tidak ada riwayat pull tersimpan dan data "
        "HoYoLAB tidak terbaca."
    )
    assert "Endgame HSR tidak terbaca: Cookie HoYoLAB belum diisi." in data["problems"]
    assert len(data["problems"]) == len(set(data["problems"]))


@pytest.mark.parametrize("arguments", [None, [], "minggu ini", {"game": "hsr"}])
def test_arguments_are_an_error_result(tmp_path, arguments):
    result, is_error = make_tools(tmp_path).run("weekly_report", arguments)

    assert is_error is True
    assert json.loads(result) == {
        "ok": False,
        "message": "Argumen weekly_report tidak valid: tool ini tidak menerima argumen; "
        "kirim objek kosong {}.",
    }


def test_another_tools_name_is_an_error_result(tmp_path):
    result, is_error = make_tools(tmp_path).run("game_status", {})

    assert is_error is True
    assert json.loads(result)["ok"] is False


def test_a_failing_clock_is_an_error_result_never_an_exception(tmp_path, caplog):
    def broken_clock() -> datetime:
        raise OSError("the clock stopped")

    store = make_store(tmp_path)
    tools = WeeklyReportTools(
        ["hsr"],
        history=FakeHistory(store),
        store=store,
        source=FakeSource(),
        codes=None,
        report_day="sunday",
        report_time=clock(19, 0),
        server_utc_offset=8,
        extra_standard={},
        now=broken_clock,
    )

    with caplog.at_level(logging.ERROR, logger="waifu.gacha.weekly"):
        result, is_error = tools.run("weekly_report", {})

    assert is_error is True
    assert json.loads(result) == {
        "ok": False,
        "message": "Laporan game minggu ini tidak bisa dibuat saat ini.",
    }
    assert "the clock stopped" in caplog.text


# --- the prompt ----------------------------------------------------------------------------------


def test_the_prompt_says_it_is_her_own_weekly_report_and_names_only_its_tool():
    assert "bukan ucapan Master" in WEEKLY_REPORT_PROMPT
    assert "sekali seminggu" in WEEKLY_REPORT_PROMPT
    assert "Panggil weekly_report saja, tanpa tool lain" in WEEKLY_REPORT_PROMPT
    assert "3-4 kalimat pendek" in WEEKLY_REPORT_PROMPT
    for other_tool in ("game_status", "gacha_history", "game_endgame", "game_events"):
        assert other_tool not in WEEKLY_REPORT_PROMPT


def test_the_prompt_names_the_week_s_play_time_once():
    assert WEEKLY_REPORT_PROMPT.count("jam main") == 1


def test_the_report_may_put_its_details_on_a_card_after_its_spoken_sentences():
    assert "3-4 kalimat pendek" in WEEKLY_REPORT_PROMPT
    assert "Rincian per game boleh kamu taruh di kartu sesudahnya." in WEEKLY_REPORT_PROMPT


def test_the_prompt_names_this_month_s_income_once():
    assert WEEKLY_REPORT_PROMPT.count("pendapatan bulan ini") == 1


def test_the_prompt_tells_only_information_and_never_a_reason_to_spend():
    assert "jangan pernah menyarankan pull, belanja, atau top up" in WEEKLY_REPORT_PROMPT


def test_the_prompt_asks_for_what_ends_in_the_next_7_days_never_this_week():
    # The facts look 7 days ahead: at the default Sunday evening slot nearly all of it ends
    # next week, so "minggu ini" would have her say a Wednesday banner ends this week.
    assert "yang berakhir dalam 7 hari ke depan" in WEEKLY_REPORT_PROMPT
    assert "berakhir minggu ini" not in WEEKLY_REPORT_PROMPT


def test_the_prompt_never_counts_as_master_saying_yes():
    # The brain hands the prompt to the confirmation gate as this turn's words: a yes here
    # could confirm a PC shutdown Master was asked about just before the report.
    assert is_yes(WEEKLY_REPORT_PROMPT, HER_NAMES) is False


def test_the_prompt_never_lets_her_look_at_the_screen():
    captured: list[int] = []

    def capture(max_side: int) -> ScreenImage:
        captured.append(max_side)
        return ScreenImage(png=b"\x89PNG", width=1, height=1)

    screen = ScreenTools(capture=capture, names=HER_NAMES)

    screen.begin_turn(WEEKLY_REPORT_PROMPT)
    result, is_error = screen.run("look_at_screen", {})
    assert is_error is True
    assert "tidak meminta melihat layar" in json.loads(result)["message"]
    assert captured == []

    # The same tools do look when Master's own words ask, so the check above is not vacuous.
    screen.begin_turn("Cyrene, lihat layarku")
    assert screen.run("look_at_screen", {})[1] is False
    assert captured == [1280]


def test_the_prompt_never_lets_her_open_a_song():
    # The report starts by itself, maybe while Master plays a fullscreen game: a browser
    # opening would take the focus from it (conftest also refuses the real os.startfile).
    opened: list[str] = []
    music = MusicTools(service="youtube", names=HER_NAMES, links=True, startfile=opened.append)

    music.begin_turn(WEEKLY_REPORT_PROMPT)
    result, is_error = music.run("play_youtube", {"query": "lofi"})
    assert is_error is True
    assert "tidak meminta lagu" in json.loads(result)["message"]
    assert opened == []

    # The same tools do open a song when Master's own words ask.
    music.begin_turn("Cyrene, putar lagu lofi")
    assert music.run("play_youtube", {"query": "lofi"})[1] is False
    assert opened == ["https://www.youtube.com/results?search_query=lofi"]


# --- the timer -----------------------------------------------------------------------------------


class Clock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current


class Requests:
    """Stands in for `assistant.ask_aloud`: records each prompt, until when it may be said, and
    what the state file said at that moment."""

    def __init__(self, state_path, error: Exception | None = None) -> None:
        self.state_path = state_path
        self.error = error
        self.prompts: list[str] = []
        self.expiries: list[datetime] = []
        self.states_seen: list[object] = []

    def __call__(self, prompt: str, expires_at: datetime) -> None:
        self.prompts.append(prompt)
        self.expiries.append(expires_at)
        self.states_seen.append(json.loads(self.state_path.read_text(encoding="utf-8")))
        if self.error is not None:
            raise self.error


SUNDAY_EVENING = datetime(2026, 10, 18, 19, 0)  # the local wall clock, as datetime.now gives it


class Weekly:
    """A timer over a state file in tmp_path, a fake clock, and a fake request."""

    def __init__(self, tmp_path, *, error: Exception | None = None, **config) -> None:
        self.config = WeeklyReportConfig(state_path=tmp_path / "weekly_report.json", **config)
        self.state_path = self.config.state_path
        self.clock = Clock(SUNDAY_EVENING)
        self.requests = Requests(self.state_path, error)
        self.restart()

    def restart(self) -> None:
        """A new timer on the same state file, like `waifu run` started again."""
        self.timer = WeeklyReportTimer(self.config, request=self.requests, now=self.clock)

    def tick_at(self, moment: datetime) -> None:
        self.clock.current = moment
        self.timer.tick_once()

    @property
    def state(self) -> object:
        return json.loads(self.state_path.read_text(encoding="utf-8"))


def test_the_first_tick_on_sunday_evening_asks_for_the_report_once(tmp_path):
    weekly = Weekly(tmp_path)

    weekly.tick_at(datetime(2026, 10, 18, 19, 0))
    weekly.tick_at(datetime(2026, 10, 18, 19, 0, 30))
    weekly.tick_at(datetime(2026, 10, 18, 23, 59))

    assert weekly.requests.prompts == [WEEKLY_REPORT_PROMPT]
    assert weekly.state == {"version": 1, "last_date": "2026-10-18"}


def test_the_report_may_be_said_until_midnight_of_the_report_day(tmp_path):
    # Held into Monday, the report would be told on the wrong day, about the wrong week.
    weekly = Weekly(tmp_path)

    weekly.tick_at(datetime(2026, 10, 18, 21, 0))

    [expiry] = weekly.requests.expiries
    assert expiry == datetime(2026, 10, 19, 0, 0)
    assert expiry.tzinfo is None  # the naive wall clock's midnight


def test_the_day_is_written_before_the_request_so_a_crash_never_repeats_it(tmp_path):
    weekly = Weekly(tmp_path)

    weekly.tick_at(SUNDAY_EVENING)

    assert weekly.requests.states_seen == [{"version": 1, "last_date": "2026-10-18"}]


@pytest.mark.parametrize(
    ("moment", "asks"),
    [
        (datetime(2026, 10, 18, 0, 0), False),
        (datetime(2026, 10, 18, 18, 59, 59, 999999), False),
        (datetime(2026, 10, 18, 19, 0), True),
        (datetime(2026, 10, 18, 23, 59, 59, 999999), True),
        (datetime(2026, 10, 19, 0, 0), False),
        (datetime(2026, 10, 19, 19, 0), False),
        (datetime(2026, 10, 17, 19, 0), False),
        (datetime(2026, 10, 17, 23, 59), False),
    ],
    ids=[
        "sunday midnight",
        "just before time",
        "exactly time",
        "last moment of sunday",
        "monday midnight",
        "monday at time",
        "saturday at time",
        "saturday night",
    ],
)
def test_it_asks_only_on_the_report_day_from_its_time_until_midnight(tmp_path, moment, asks):
    weekly = Weekly(tmp_path)

    weekly.tick_at(moment)

    assert weekly.requests.prompts == ([WEEKLY_REPORT_PROMPT] if asks else [])


def test_a_pc_started_later_on_the_report_day_gets_it_once_also_across_a_restart(tmp_path):
    weekly = Weekly(tmp_path)

    weekly.tick_at(datetime(2026, 10, 18, 22, 15))
    weekly.restart()
    weekly.tick_at(datetime(2026, 10, 18, 22, 45))

    assert weekly.requests.prompts == [WEEKLY_REPORT_PROMPT]


def test_a_report_day_the_pc_was_off_is_skipped_until_the_next_week(tmp_path):
    weekly = Weekly(tmp_path)
    weekly.state_path.write_text('{"version": 1, "last_date": "2026-10-11"}', encoding="utf-8")

    for day in range(19, 25):  # Monday to Saturday, all day long
        for hour in (0, 12, 19, 23):
            weekly.tick_at(datetime(2026, 10, day, hour, 0))
    assert weekly.requests.prompts == []

    weekly.tick_at(datetime(2026, 10, 25, 19, 0))
    assert weekly.requests.prompts == [WEEKLY_REPORT_PROMPT]


def test_the_next_week_reports_again(tmp_path):
    weekly = Weekly(tmp_path)
    weekly.tick_at(datetime(2026, 10, 18, 19, 0))

    weekly.tick_at(datetime(2026, 10, 25, 19, 0))
    weekly.tick_at(datetime(2026, 10, 25, 19, 1))

    assert weekly.requests.prompts == [WEEKLY_REPORT_PROMPT, WEEKLY_REPORT_PROMPT]
    assert weekly.state == {"version": 1, "last_date": "2026-10-25"}


def test_the_day_and_time_come_from_the_config(tmp_path):
    weekly = Weekly(tmp_path, day="saturday", time="20:30")

    weekly.tick_at(datetime(2026, 10, 18, 21, 0))  # Sunday
    weekly.tick_at(datetime(2026, 10, 24, 20, 29))
    assert weekly.requests.prompts == []
    weekly.tick_at(datetime(2026, 10, 24, 20, 30))
    assert weekly.requests.prompts == [WEEKLY_REPORT_PROMPT]


def test_a_request_that_fails_is_logged_and_not_retried_that_week(tmp_path, caplog):
    weekly = Weekly(tmp_path, error=RuntimeError("the voice loop is gone"))

    with caplog.at_level(logging.ERROR):
        weekly.tick_at(datetime(2026, 10, 18, 19, 0))
        weekly.tick_at(datetime(2026, 10, 18, 20, 0))

    assert weekly.requests.prompts == [WEEKLY_REPORT_PROMPT]
    assert "the voice loop is gone" in caplog.text


def test_a_corrupt_state_is_moved_aside_and_the_report_still_comes(tmp_path, caplog):
    weekly = Weekly(tmp_path)
    weekly.state_path.write_bytes(b"{not json")

    with caplog.at_level(logging.ERROR):
        weekly.tick_at(SUNDAY_EVENING)

    corrupt = tmp_path / "weekly_report.json.corrupt-20261018-190000"
    assert corrupt.read_bytes() == b"{not json"
    assert corrupt.name in caplog.text
    assert weekly.requests.prompts == [WEEKLY_REPORT_PROMPT]
    assert weekly.state == {"version": 1, "last_date": "2026-10-18"}


def test_a_state_that_cannot_be_saved_never_asks(tmp_path, monkeypatch):
    weekly = Weekly(tmp_path)

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        weekly.tick_at(SUNDAY_EVENING)

    assert weekly.requests.prompts == []


def test_the_thread_is_named_ticks_at_once_and_stop_wakes_it_at_once(tmp_path):
    asked = threading.Event()
    config = WeeklyReportConfig(state_path=tmp_path / "weekly_report.json")
    timer = WeeklyReportTimer(
        config,
        request=lambda prompt, expires_at: asked.set(),
        now=Clock(SUNDAY_EVENING),
        tick_seconds=3600,
    )

    with timer:
        assert asked.wait(5)
        assert any(thread.name == "weekly-report" for thread in threading.enumerate())
        stopping = time.monotonic()

    assert time.monotonic() - stopping < 1
    assert not any(thread.name == "weekly-report" for thread in threading.enumerate())
