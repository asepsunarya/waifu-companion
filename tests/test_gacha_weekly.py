"""The weekly game report: the facts gathered in code, and their JSON for the brain.

No test reaches HoYoLAB or the history API: the game data source and the history refresh are
fakes, while the pull history and the redeem codes are the real stores in tmp_path. Every
clock is fixed; the time-budget tests block a fake on an event they release at their end.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from waifu.gacha.code_store import CodeStore
from waifu.gacha.game_data import GAME_MODES, Banner, Endgame, GameCalendar, GameEvent
from waifu.gacha.games import GAMES
from waifu.gacha.history import GachaRecord, GachaStore, HistoryReport
from waifu.gacha.hoyolab import GachaError
from waifu.gacha.starrail import HsrCalendar, HsrEndgame, HsrEvent, HsrWarp
from waifu.gacha.tools import HISTORY_NOTE
from waifu.gacha.weekly import gather_weekly_report, report_json

WIB = timezone(timedelta(hours=7))
SERVER = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 18, 19, 0, tzinfo=WIB)  # a Sunday, 20:00 on the Asia server
HSR, GENSHIN, ZZZ = GAMES["hsr"], GAMES["genshin"], GAMES["zzz"]
EVERY_GAME = ["hsr", "genshin", "zzz"]
NO_COOKIE = GachaError("no_cookie", "Cookie HoYoLAB belum diisi.")
OFFLINE = GachaError("network", "HoYoLAB tidak bisa dihubungi. Cek koneksi internet.")


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
    whole call (`hsr_endgame`, `zzz_calendar`, ...), and `blocked` an event a call waits for."""

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
        if name in self.blocked:
            self.blocked[name].wait(5)
        if name in self.failures:
            raise self.failures[name]


class FakeHistory:
    """GachaHistory.report over the real store: a refresh saves the pulls in `found` for its
    game, or fails with `problems[game]` (the saved history answers instead), or raises
    `failures[game]`; `blocked` holds an event a refresh waits for. Each call is recorded
    with the thread it ran on."""

    def __init__(
        self, store: GachaStore, *, found=None, problems=None, failures=None, blocked=None
    ):
        self.store = store
        self.found: dict[str, list[GachaRecord]] = found or {}
        self.problems: dict[str, str] = problems or {}
        self.failures: dict[str, Exception] = failures or {}
        self.blocked: dict[str, threading.Event] = blocked or {}
        self.calls: list[tuple[str, str]] = []

    def report(self, game_id: str, *, refresh: bool = True, full: bool = False) -> HistoryReport:
        assert (refresh, full) == (True, False)
        self.calls.append((game_id, threading.current_thread().name))
        if game_id in self.blocked:
            self.blocked[game_id].wait(5)
        if game_id in self.failures:
            raise self.failures[game_id]
        game = GAMES[game_id]
        stored = self.store.load(game)
        problem = self.problems.get(game_id)
        if problem is None and game_id in self.found:
            records = [*stored.records, *self.found[game_id]]
            self.store.save(game, stored.uid, records, complete=stored.complete)
        return HistoryReport(game, problem is None, problem, stored.complete, (), (), 0, None)


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
):
    store = store if store is not None else make_store(tmp_path)
    return gather_weekly_report(
        list(games),
        history=history if history is not None else FakeHistory(store),
        store=store,
        source=source if source is not None else FakeSource(),
        codes=codes,
        now=NOW,
        offset=8,
        extra_standard=extra or {},
        budget_s=budget_s,
    )


def week_of(report, game_id: str):
    [week] = [week for week in report.games if week.game.id == game_id]
    return week


# --- the pulls and the pity ----------------------------------------------------------------------


def test_the_week_is_the_seven_days_up_to_now():
    report = gather_weekly_report(
        [],
        history=None,
        store=None,
        source=None,
        codes=None,
        now=NOW,
        offset=8,
        extra_standard={},
    )

    assert (report.now, report.since, report.games) == (NOW, NOW - timedelta(days=7), ())


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
        "since": "2026-10-11 19:00",
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
