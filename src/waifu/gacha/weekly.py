"""Cyrene's weekly game report: Master's week in their HoYoverse games, gathered in code.

`gather_weekly_report` reads, for each configured game, this week's pulls and the pity from the
stored pull history (refreshed first, one game after another, and only for a game that already
has one: a first import is far too slow for a voice turn), this season's endgame and the banners
and events that end within 7 days from HoYoLAB (the same calls the game data tools make, one
worker per game), and the redeem codes not redeemed yet. Every section fails on its own into a
short Indonesian problem, and all the reads share one time budget, so a slow HoYoLAB never
holds her turn for long: a late section says that its time ran out. `report_json` is what the
brain gets; `waifu gacha report` prints the same `WeeklyReport`. Nothing here is logged at INFO:
the facts are Master's own.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, TypeVar, cast

from waifu.config import GameId
from waifu.gacha.codes import RedeemCode
from waifu.gacha.game_data import (
    GAME_MODES,
    Banner,
    Endgame,
    GameCalendar,
    GameDataSource,
    GameEvent,
    is_over,
    is_running,
)
from waifu.gacha.games import GAMES, Game
from waifu.gacha.history import (
    BannerPulls,
    BannerStats,
    HistoryReport,
    StoredHistory,
    banner_stats,
    partial_history_hint,
    pulls_since,
)
from waifu.gacha.hoyolab import GachaError
from waifu.gacha.starrail import HsrCalendar, HsrEndgame, HsrEvent, HsrWarp
from waifu.gacha.tools import HISTORY_NOTE, LOCAL_TIME, HistorySource

T = TypeVar("T")

logger = logging.getLogger(__name__)

WEEK = timedelta(days=7)
BUDGET_S = 30.0  # for every read together; one HoYoLAB call alone may take 20 s
CODES_MAX = 5
TOPS_MAX = 10
BANNERS_MAX = 8
EVENTS_MAX = 10
EVENT_NAME_MAX_CHARS = 60
_ENDED_GAMES = frozenset({"genshin", "zzz"})  # whose seasons say `ended`, as in game_endgame

_NO_HISTORY = (
    "Belum ada riwayat {short} yang tersimpan; jalankan 'waifu gacha history {id} --full' sekali."
)
_STALE_HISTORY = (
    "Riwayat {short} terbaru tidak terbaca, jadi angkanya dari riwayat tersimpan: {why}"
)
_HISTORY_FAILED = "Riwayat {short} tidak terbaca saat ini."
_ENDGAME_FAILED = "Endgame {short} tidak terbaca: {why}"
_MODES_FAILED = "{names} tidak terbaca: {why}"
_CALENDAR_FAILED = "Kalender event {short} tidak terbaca: {why}"
_CODES_FAILED = "Kode redeem {short} tidak terbaca saat ini."
_OUT_OF_TIME = "waktunya habis."
_UNEXPECTED = "terjadi kesalahan."


class StoreSource(Protocol):
    """The part of `GachaStore` the report reads."""

    def load(self, game: Game, uid: int | None = None) -> StoredHistory: ...


class CodeSource(Protocol):
    """The part of `CodeStore` the report reads."""

    def unredeemed(self, game: GameId) -> list[RedeemCode]: ...


@dataclass(frozen=True)
class GameWeek:
    """One game's week; a section that could not be read is empty, with its problem."""

    game: Game
    pulls: tuple[BannerPulls, ...]  # this week's, per banner, without the banners not pulled on
    pity: tuple[BannerStats, ...]  # every banner with a stored pull
    complete: bool  # the stored history was read in full once
    pulls_problem: str | None
    endgame: tuple[HsrEndgame | Endgame, ...]  # the modes read, in the game's order
    endgame_problem: str | None
    banners: tuple[HsrWarp | Banner, ...]  # running and ending within 7 days, soonest first
    events: tuple[HsrEvent | GameEvent, ...]  # running and ending within 7 days, soonest first
    calendar_problem: str | None
    codes: tuple[str, ...] | None  # None: the redeem codes are not tracked
    codes_problem: str | None

    @property
    def hint(self) -> str | None:
        """What to run once when only the newest pages of the history were read."""
        return None if self.complete or not self.pity else partial_history_hint(self.game)

    @property
    def readable(self) -> bool:
        """Whether anything of this game could be read: a history, an endgame, a calendar."""
        return bool(self.pity or self.endgame) or self.calendar_problem is None


@dataclass(frozen=True)
class WeeklyReport:
    now: datetime  # aware
    since: datetime  # the week is (since, now]
    games: tuple[GameWeek, ...]

    @property
    def readable(self) -> bool:
        """Whether there is anything to tell at all."""
        return any(week.readable for week in self.games)


def gather_weekly_report(
    games: Sequence[str],
    *,
    history: HistorySource,
    store: StoreSource,
    source: GameDataSource,
    codes: CodeSource | None,
    now: datetime,
    offset: int,
    extra_standard: Mapping[str, Sequence[str]],
    budget_s: float = BUDGET_S,
) -> WeeklyReport:
    """The week up to `now` (aware) in each of `games`, in that order. Never raises: each
    section that fails or is not read within `budget_s` seconds carries its problem. Readers
    still running then are left to finish on their own, bounded by their own timeouts."""
    chosen = [GAMES[game_id] for game_id in games]
    before = {game.id: _load(store, game) for game in chosen}
    workers = max(1, len(chosen))
    refreshing = ThreadPoolExecutor(1, thread_name_prefix="weekly-history")
    reading = ThreadPoolExecutor(workers, thread_name_prefix="weekly-hoyolab")
    try:
        refreshes = {
            game.id: refreshing.submit(history.report, game.id)
            for game in chosen
            if (stored := before[game.id]) is not None and stored.records
        }
        # Every endgame first: each worker reads its game's endgame, then a calendar.
        endgames = {
            game.id: reading.submit(_endgame_reader(source, game.id), list(GAME_MODES[game.id]))
            for game in chosen
        }
        calendars = {game.id: reading.submit(_calendar_reader(source, game.id)) for game in chosen}
        done, _ = wait([*refreshes.values(), *endgames.values(), *calendars.values()], budget_s)
    finally:
        refreshing.shutdown(wait=False, cancel_futures=True)
        reading.shutdown(wait=False, cancel_futures=True)
    since = now - WEEK
    weeks = []
    for game in chosen:
        refresh = refreshes.get(game.id)
        pulls = _pulls_section(
            game,
            store,
            before[game.id],
            None if refresh is None else _outcome(refresh, done, f"{game.short} history"),
            since,
            offset,
            extra_standard.get(game.id, ()),
        )
        endgame = _endgame_section(game, _outcome(endgames[game.id], done, f"{game.short} endgame"))
        calendar = _calendar_section(
            game, _outcome(calendars[game.id], done, f"{game.short} calendar"), now
        )
        weeks.append(GameWeek(game, *pulls, *endgame, *calendar, *_codes_section(game, codes)))
    return WeeklyReport(now, since, tuple(weeks))


def _load(store: StoreSource, game: Game) -> StoredHistory | None:
    try:
        return store.load(game)
    except Exception:
        logger.exception("Could not read the %s pull history for the weekly report", game.short)
        return None


def _endgame_reader(
    source: GameDataSource, game_id: str
) -> Callable[[list[str]], Mapping[str, Any]]:
    if game_id == "genshin":
        return source.genshin_endgame
    if game_id == "zzz":
        return source.zzz_endgame
    return source.hsr_endgame


def _calendar_reader(source: GameDataSource, game_id: str) -> Callable[[], Any]:
    if game_id == "genshin":
        return source.genshin_calendar
    if game_id == "zzz":
        return source.zzz_calendar
    return source.hsr_calendar


def _outcome(future: Future[T], done: set[Future[Any]], what: str) -> tuple[T | None, str | None]:
    """(what `future` gave, None), or (None, why it gave nothing): out of time, HoYoLAB's own
    message, or an unexpected failure, which is logged."""
    if future not in done:
        logger.warning("The weekly report's %s read ran out of time", what)
        return None, _OUT_OF_TIME
    try:
        return future.result(), None
    except GachaError as error:
        logger.warning("The weekly report could not read the %s: %s", what, error.kind)
        return None, error.message
    except Exception:
        logger.exception("The weekly report's %s read failed", what)
        return None, _UNEXPECTED


def _pulls_section(
    game: Game,
    store: StoreSource,
    before: StoredHistory | None,
    refreshed: tuple[HistoryReport | None, str | None] | None,
    since: datetime,
    offset: int,
    extra: Iterable[str],
) -> tuple[tuple[BannerPulls, ...], tuple[BannerStats, ...], bool, str | None]:
    """This week's pulls, the pity, whether the history is complete, and the problem: the
    store as it is after the refresh (or as it was, when it cannot be read again)."""
    if before is None:
        return (), (), False, _HISTORY_FAILED.format(short=game.short)
    if refreshed is None:  # nothing stored, so nothing was refreshed
        return (), (), False, _NO_HISTORY.format(short=game.short, id=game.id)
    report, failure = refreshed
    why = failure if report is None else report.problem
    stored = _load(store, game) or before
    extra = list(extra)
    return (
        tuple(pulls_since(game, stored.records, since, offset, extra)),
        tuple(banner_stats(game, stored.records, extra)),
        stored.complete,
        None if why is None else _STALE_HISTORY.format(short=game.short, why=why),
    )


def _endgame_section(
    game: Game, read: tuple[Mapping[str, Any] | None, str | None]
) -> tuple[tuple[HsrEndgame | Endgame, ...], str | None]:
    """The modes read in the game's order, and the problem naming the modes that were not."""
    results, failure = read
    if results is None:
        return (), _ENDGAME_FAILED.format(short=game.short, why=failure)
    modes = GAME_MODES[game.id]
    seasons = tuple(
        result for mode in modes if isinstance(result := results.get(mode), HsrEndgame | Endgame)
    )
    errors = {mode: error for mode in modes if isinstance(error := results.get(mode), GachaError)}
    if not errors:
        return seasons, None
    why = " ".join(dict.fromkeys(error.message for error in errors.values()))
    if not seasons:
        return (), _ENDGAME_FAILED.format(short=game.short, why=why)
    names = ", ".join(modes[mode] for mode in errors)
    return seasons, _MODES_FAILED.format(names=names, why=why)


def _calendar_section(
    game: Game, read: tuple[HsrCalendar | GameCalendar | None, str | None], now: datetime
) -> tuple[tuple[HsrWarp | Banner, ...], tuple[HsrEvent | GameEvent, ...], str | None]:
    calendar, failure = read
    if calendar is None:
        return (), (), _CALENDAR_FAILED.format(short=game.short, why=failure)
    banners = calendar.warps if isinstance(calendar, HsrCalendar) else calendar.banners
    return _ending_soon(banners, now), _ending_soon(calendar.events, now), None


def _ending_soon(items: Iterable[Any], now: datetime) -> tuple[Any, ...]:
    """The banners or events running at `now` that end within 7 days, soonest first."""
    ending = [
        item
        for item in items
        if item.ends_at is not None
        and item.ends_at <= now + WEEK
        and is_running(item.starts_at, item.ends_at, now)
    ]
    return tuple(sorted(ending, key=lambda item: item.ends_at))


def _codes_section(
    game: Game, codes: CodeSource | None
) -> tuple[tuple[str, ...] | None, str | None]:
    if codes is None:
        return None, None
    try:
        unredeemed = codes.unredeemed(cast(GameId, game.id))
    except Exception:
        logger.exception("Could not read the %s redeem codes for the weekly report", game.short)
        return (), _CODES_FAILED.format(short=game.short)
    return tuple(code.code for code in unredeemed[:CODES_MAX]), None


# --- the JSON for the brain ----------------------------------------------------------------


def report_json(report: WeeklyReport) -> dict[str, Any]:
    """The week as compact facts with local times, never what was spent."""
    now = report.now
    return {
        "as_of": _local(now, now),
        "since": _local(report.since, now),
        "note": HISTORY_NOTE,
        "games": [_game_json(week, now) for week in report.games],
    }


def _game_json(week: GameWeek, now: datetime) -> dict[str, Any]:
    game = week.game
    data: dict[str, Any] = {
        "game": game.id,
        "name": game.name,
        "pulls_this_week": _pulls_json(game, week.pulls) if week.pity else None,
        "pity": [_pity_json(stats) for stats in week.pity],
        "pulls_problem": week.pulls_problem,
    }
    if week.hint:
        data["hint"] = week.hint
    data["endgame"] = [_endgame_json(game, season, now) for season in week.endgame]
    data["endgame_problem"] = week.endgame_problem
    data["ending_soon"] = {
        "banners": [_banner_json(banner, now) for banner in week.banners[:BANNERS_MAX]],
        "events": [_event_json(event, now) for event in week.events[:EVENTS_MAX]],
    }
    data["calendar_problem"] = week.calendar_problem
    if week.codes is not None:
        data["unredeemed_codes"] = list(week.codes)
        data["codes_problem"] = week.codes_problem
    return data


def _pulls_json(game: Game, pulls: tuple[BannerPulls, ...]) -> dict[str, Any]:
    tops = sorted(
        ((banner.banner, top) for banner in pulls for top in banner.tops),
        key=lambda pair: (pair[1].time, pair[1].id),
        reverse=True,
    )
    return {
        "count": sum(banner.count for banner in pulls),
        "banners": [{"banner": banner.banner.name, "count": banner.count} for banner in pulls],
        "top_pulls": [
            {
                "banner": banner.name,
                "name": top.name,
                "rarity": "S" if game.id == "zzz" else "5★",
                "pulls": top.pulls,
                "won_5050": top.won_5050,
            }
            for banner, top in tops[:TOPS_MAX]
        ],
    }


def _pity_json(stats: BannerStats) -> dict[str, Any]:
    pity: dict[str, Any] = {
        "banner": stats.banner.name,
        "pity": stats.pity,
        "hard_pity": stats.banner.hard_pity,
        "to_hard_pity": stats.to_hard_pity,
    }
    if stats.guaranteed is not None:
        pity["guaranteed"] = stats.guaranteed
    return pity


def _endgame_json(game: Game, season: HsrEndgame | Endgame, now: datetime) -> dict[str, Any]:
    mode = season.mode
    data: dict[str, Any] = {
        "mode": mode,
        "name": GAME_MODES[game.id][mode],
        **_present(season=season.season),
        "has_data": season.has_data,
    }
    if isinstance(season, HsrEndgame):
        data |= {"stars": season.stars, "extra_stars": season.extra_stars}
        data |= _present(deepest_floor=season.deepest_floor)
    else:
        data |= _present(
            stars=season.stars,
            deepest=season.deepest,
            difficulty=season.difficulty,
            rating=season.rating,
            score=season.score,
            max_score=season.max_score,
        )
    ends_at = season.ends_at
    if game.id in _ENDED_GAMES and ends_at is not None and is_over(ends_at, now):
        data |= {"ends": _local(ends_at, now), "ended": True}
    else:
        data |= _moment("ends", ends_at, now)
    data["ends_within_7_days"] = ends_at is not None and now < ends_at <= now + WEEK
    return data


def _banner_json(banner: HsrWarp | Banner, now: datetime) -> dict[str, Any]:
    return {
        "type": banner.kind,
        **_present(name=banner.name or None),
        "featured": list(banner.featured),
        **_moment("ends", banner.ends_at, now),
    }


def _event_json(event: HsrEvent | GameEvent, now: datetime) -> dict[str, Any]:
    return {
        "name": event.name[:EVENT_NAME_MAX_CHARS],
        **_moment("ends", event.ends_at, now),
        **_present(current=event.current, total=event.total),
        "done": event.done,
    }


def _present(**values: object) -> dict[str, object]:
    """The keys that have a value: keys without one are left out."""
    return {key: value for key, value in values.items() if value is not None}


def _moment(key: str, when: datetime | None, now: datetime) -> dict[str, object]:
    """`key` in local time and `<key>_in_hours`, rounded up and never below 0, counted from now."""
    if when is None:
        return {}
    hours = max(0, math.ceil((when - now).total_seconds() / 3600))
    return {key: _local(when, now), f"{key}_in_hours": hours}


def _local(when: datetime, now: datetime) -> str:
    return when.astimezone(now.tzinfo).strftime(LOCAL_TIME)
