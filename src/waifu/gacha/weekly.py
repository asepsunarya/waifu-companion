"""Cyrene's weekly game report: Master's week in their HoYoverse games, gathered in code.

`WeeklyReportTimer` asks for her own report once a week, on `weekly_report.day` from
`weekly_report.time` until midnight, handing `WEEKLY_REPORT_PROMPT` to the brain; the one tool
her turn calls, `weekly_report`, gives the facts below as compact JSON.

`gather_weekly_report` reads, for each configured game, this week's pulls (since `week_start`)
and the pity from the stored pull history (refreshed first, one game after another, and only
for a game that already has one: a first import is far too slow for a voice turn), this
season's endgame and the banners and events that end within 7 days from HoYoLAB (the same calls
the game data tools make, one worker per game), the redeem codes not redeemed yet, with play
time counted, how long each tracked game was open in the week (a local file, read at once), with
an income source, this month's premium currency earned so far (HoYoLAB's monthly diaries, one
more worker for every game), and with the internet watcher on, how often and how long the
internet was down in the week (a local file, read at once). Every
section fails on its own into a short Indonesian problem, and all the reads share one time
budget, so a slow HoYoLAB never holds her turn for long: a late section says that its time ran
out. `report_json` is what the brain gets; `waifu gacha report` prints the same `WeeklyReport`.
Nothing here is logged at INFO: the facts are Master's own.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future, wait
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from functools import partial
from typing import Any, Protocol, TypeVar, cast, get_args

from waifu.brain.base import ToolSpec
from waifu.config import GameId, Weekday, WeeklyReportConfig
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
from waifu.gacha.games import GAMES, Game, checkin_day
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
from waifu.gacha.income import GameIncome, IncomeSource, month_text
from waifu.gacha.starrail import HsrCalendar, HsrEndgame, HsrEvent, HsrWarp
from waifu.gacha.tools import HISTORY_NOTE, LOCAL_TIME, HistorySource
from waifu.network_history import NetworkRecord, OutageTotal, outage_totals
from waifu.play_time import PlayTotal, whole_minutes
from waifu.prompt_timer import PromptTimer, end_of_day

T = TypeVar("T")

logger = logging.getLogger(__name__)

WEEKLY_REPORT = "weekly_report"
WEEK = timedelta(days=7)
LISTING_LAG = timedelta(hours=1)  # HoYoverse lists a pull about an hour after it is made
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
_PLAY_FAILED = "Jam main {short} tidak terbaca saat ini."
_NETWORK_FAILED = "Riwayat internet tidak terbaca saat ini."
_INCOME_FAILED = "Pendapatan {short} bulan ini tidak terbaca: {why}"
_OUT_OF_TIME = "waktunya habis."
_UNEXPECTED = "terjadi kesalahan."
_NOTHING_READABLE = (
    "Laporan game minggu ini tidak bisa dibuat: tidak ada riwayat pull tersimpan dan data "
    "HoYoLAB tidak terbaca."
)
_FAILED = "Laporan game minggu ini tidak bisa dibuat saat ini."
# Her own turn, not Master's words: it holds no look word (the screen tool stays shut), no
# play verb or music word (the music tool stays shut), and does not start with a yes (the
# confirmation gate stays shut). It names only weekly_report: the game data tools are for
# Master's own questions.
WEEKLY_REPORT_PROMPT = (
    "Ini bukan ucapan Master: ini laporan game mingguanmu sendiri, yang kamu mulai sendiri "
    "sekali seminggu. Panggil weekly_report saja, tanpa tool lain, lalu ceritakan minggu Master "
    "di game-nya dalam 3-4 kalimat pendek yang hangat, seperti teman yang ikut bermain: hasil "
    "pull minggu ini dan 50/50-nya, pity yang sudah dekat hard pity, progres endgame, jam main "
    "dan pendapatan bulan ini kalau ada, dan yang berakhir dalam 7 hari ke depan. Itu hanya "
    "kabar, jadi jangan pernah menyarankan pull, belanja, atau top up. Jangan membacakan datanya "
    "satu per satu."
)


class StoreSource(Protocol):
    """The part of `GachaStore` the report reads."""

    def load(self, game: Game, uid: int | None = None) -> StoredHistory: ...


class CodeSource(Protocol):
    """The part of `CodeStore` the report reads."""

    def unredeemed(self, game: GameId) -> list[RedeemCode]: ...


class PlayTimeSource(Protocol):
    """The part of `PlayTimeStore` the report reads: the tracked games, and their totals."""

    @property
    def games(self) -> Sequence[str]: ...

    def totals(self, since: datetime, until: datetime) -> Mapping[str, PlayTotal]: ...


class OutageSource(Protocol):
    """The part of `NetworkStore` the report reads."""

    def record(self) -> NetworkRecord: ...


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
    # Both None: play time is not counted for this game.
    play: PlayTotal | None = None
    play_problem: str | None = None
    # Both None: the income is not read (no source).
    income: GameIncome | None = None
    income_problem: str | None = None

    @property
    def hint(self) -> str | None:
        """What to run once when only the newest pages of the history were read."""
        return None if self.complete or not self.pity else partial_history_hint(self.game)

    @property
    def problems(self) -> tuple[str, ...]:
        """The problem of each section that has one, in the sections' order."""
        sections = (self.pulls_problem, self.endgame_problem, self.calendar_problem)
        own = (*sections, self.codes_problem, self.play_problem, self.income_problem)
        return tuple(problem for problem in own if problem)

    @property
    def readable(self) -> bool:
        """Whether anything of this game could be read: a history, an endgame, a calendar."""
        return bool(self.pity or self.endgame) or self.calendar_problem is None


@dataclass(frozen=True)
class WeeklyReport:
    now: datetime  # aware
    since: datetime  # the week is (since, now], see week_start
    games: tuple[GameWeek, ...]
    # Both None: the internet outages are not read (no source).
    network: OutageTotal | None = None  # in the 7 days up to now
    network_problem: str | None = None

    @property
    def readable(self) -> bool:
        """Whether there is anything to tell at all: the outages alone are no game report."""
        return any(week.readable for week in self.games)


def week_start(now: datetime, day: Weekday, at: time) -> datetime:
    """Where the week up to `now` (aware, local) starts: 7 days and the listing lag before the
    report's slot, `day` at `at`, when `now` is on that day from `at` on, else before `now`.

    A pull made in the hour before a report is listed only after it, so each report reaches
    back that hour; and counted from the slot, not from when the report is said, consecutive
    weekly reports meet however late each is said that evening. A pull already listed in that
    hour may be told in both."""
    slot = datetime.combine(now.date(), at, now.tzinfo)
    on_report_day = now.weekday() == get_args(Weekday).index(day) and now >= slot
    return (slot if on_report_day else now) - WEEK - LISTING_LAG


def gather_weekly_report(
    games: Sequence[str],
    *,
    history: HistorySource,
    store: StoreSource,
    source: GameDataSource,
    codes: CodeSource | None,
    now: datetime,
    since: datetime,
    offset: int,
    extra_standard: Mapping[str, Sequence[str]],
    budget_s: float = BUDGET_S,
    play_time: PlayTimeSource | None = None,
    income: IncomeSource | None = None,
    network: OutageSource | None = None,
) -> WeeklyReport:
    """The week `(since, now]` (aware) in each of `games`, in that order, with `play_time`
    each tracked game's play time in the 7 days up to `now`, with `income` each game's
    premium currency earned so far this month (server time), and with `network` the internet
    outages in the 7 days up to `now`. Never raises: each section that fails or is not read
    within `budget_s` seconds carries its problem. A read still running then is left to finish
    on its daemon thread, bounded by its own timeout, and never holds up the process's exit; a
    read not started by then never starts."""
    chosen = [GAMES[game_id] for game_id in games]
    # Play time and the outages have no listing lag: their week is the 7 days up to now.
    plays = None if play_time is None else _read_play_time(play_time, since + LISTING_LAG, now)
    outages = None if network is None else _read_outages(network, since + LISTING_LAG, now)
    before = {game.id: _load(store, game) for game in chosen}
    refreshes: dict[str, Future[HistoryReport]] = {
        game.id: Future()
        for game in chosen
        if (stored := before[game.id]) is not None and stored.records
    }
    _start_lane(
        "weekly-history",
        [(future, partial(history.report, game_id)) for game_id, future in refreshes.items()],
    )
    endgames: dict[str, Future[Mapping[str, Any]]] = {}
    calendars: dict[str, Future[Any]] = {}
    for game in chosen:  # one lane per game: its endgame, then its calendar
        endgames[game.id], calendars[game.id] = Future(), Future()
        modes = list(GAME_MODES[game.id])
        _start_lane(
            f"weekly-hoyolab-{game.id}",
            [
                (endgames[game.id], partial(_endgame_reader(source, game.id), modes)),
                (calendars[game.id], _calendar_reader(source, game.id)),
            ],
        )
    reads: list[Future[Any]] = [*refreshes.values(), *endgames.values(), *calendars.values()]
    incomes: Future[Mapping[str, Any]] = Future()
    if income is not None:  # one call for every game, on a worker of its own
        month = checkin_day(now, offset).replace(day=1)
        read = partial(income.income, [game.id for game in chosen], month, this_month=True)
        _start_lane("weekly-income", [(incomes, read)])
        reads.append(incomes)
    done, late = wait(reads, budget_s)
    for future in late:
        future.cancel()  # only a read not started yet can be cancelled
    earned = None if income is None else _outcome(incomes, done, "income")
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
        weeks.append(
            GameWeek(
                game,
                *pulls,
                *endgame,
                *calendar,
                *_codes_section(game, codes),
                *_play_section(game, play_time, plays),
                *_income_section(game, earned),
            )
        )
    unread = network is not None and outages is None
    return WeeklyReport(now, since, tuple(weeks), outages, _NETWORK_FAILED if unread else None)


def _start_lane(name: str, jobs: Sequence[tuple[Future[Any], Callable[[], Any]]]) -> None:
    """Run `jobs` one after another on a daemon thread named `name`, each settling its future;
    a job whose future was cancelled before its turn is skipped. Daemon threads, not a pool:
    Python joins a pool's workers at exit, so a read given up on would hold up the exit of
    `waifu gacha report` or a closing `waifu run` until its own timeout."""
    if not jobs:
        return

    def run() -> None:
        for future, job in jobs:
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = job()
            except Exception as error:  # raised again where the result is read
                future.set_exception(error)
            else:
                future.set_result(result)

    threading.Thread(target=run, name=name, daemon=True).start()


def _load(store: StoreSource, game: Game, uid: int | None = None) -> StoredHistory | None:
    try:
        return store.load(game, uid)
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
        logger.info("The weekly report could not read the %s: %s", what, error.kind)
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
    store as it is after the refresh, of the account the refresh read, which need not be the
    one saved last (or the store as it was, when it cannot be read again)."""
    if before is None:
        return (), (), False, _HISTORY_FAILED.format(short=game.short)
    if refreshed is None:  # nothing stored, so nothing was refreshed
        return (), (), False, _NO_HISTORY.format(short=game.short, id=game.id)
    report, failure = refreshed
    why = failure if report is None else report.problem
    stored = _load(store, game, None if report is None else report.uid) or before
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


def _read_play_time(
    play_time: PlayTimeSource, since: datetime, now: datetime
) -> Mapping[str, PlayTotal] | None:
    """The tracked games' play time in `[since, now]`; None when it cannot be read."""
    try:
        return play_time.totals(since, now)
    except Exception:
        logger.exception("Could not read the play time for the weekly report")
        return None


def _read_outages(network: OutageSource, since: datetime, now: datetime) -> OutageTotal | None:
    """The internet outages with time in `[since, now]`; None when they cannot be read."""
    try:
        return outage_totals(network.record(), since, now)
    except Exception:
        logger.exception("Could not read the internet outages for the weekly report")
        return None


def _play_section(
    game: Game, play_time: PlayTimeSource | None, plays: Mapping[str, PlayTotal] | None
) -> tuple[PlayTotal | None, str | None]:
    if play_time is None or game.id not in play_time.games:
        return None, None
    if plays is None:
        return None, _PLAY_FAILED.format(short=game.short)
    return plays[game.id], None


def _income_section(
    game: Game, read: tuple[Mapping[str, Any] | None, str | None] | None
) -> tuple[GameIncome | None, str | None]:
    """This game's income this month and its problem; both None when it is not read."""
    if read is None:
        return None, None
    results, failure = read
    result = None if results is None else results.get(game.id)
    if isinstance(result, GameIncome):
        return result, None
    why = result.message if isinstance(result, GachaError) else failure or _UNEXPECTED
    return None, _INCOME_FAILED.format(short=game.short, why=why)


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


def _local_now() -> datetime:
    return datetime.now().astimezone()


class WeeklyReportTools:
    """A `ToolProvider` for the brain: the week in Master's games in one call."""

    def __init__(
        self,
        games: Sequence[str],
        *,
        history: HistorySource,
        store: StoreSource,
        source: GameDataSource,
        codes: CodeSource | None,
        report_day: Weekday,
        report_time: time,
        server_utc_offset: int = 8,
        extra_standard: Mapping[str, Sequence[str]] | None = None,
        now: Callable[[], datetime] = _local_now,
        budget_s: float = BUDGET_S,
        play_time: PlayTimeSource | None = None,
        income: IncomeSource | None = None,
        network: OutageSource | None = None,
    ) -> None:
        """Without `codes` (the redeem codes are off) the report holds no codes, without
        `play_time` (play time is off) no play time, without `income` no monthly income, and
        without `network` (the internet watcher is off) no outages; the week starts as
        `week_start` says from her weekly report's slot, `report_day` at `report_time`."""
        self._games = list(games)
        self._history = history
        self._store = store
        self._source = source
        self._codes = codes
        self._day = report_day
        self._time = report_time
        self._offset = server_utc_offset
        self._extra = extra_standard or {}
        self._now = now
        self._budget_s = budget_s
        self._play_time = play_time
        self._income = income
        self._network = network
        self._spec = _spec(
            self._games,
            codes=codes is not None,
            play=play_time is not None,
            income=income is not None,
            network=network is not None,
        )

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == WEEKLY_REPORT:
                return self._report(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except Exception:
            logger.exception("Weekly report tool '%s' failed", name)
            return _error(_FAILED)
        return _error(f"Tool '{name}' bukan tool laporan game mingguan.")

    def gather(self) -> WeeklyReport:
        """The week up to now, as the tool reads it; `waifu gacha report` prints it."""
        now = self._now()
        return gather_weekly_report(
            self._games,
            history=self._history,
            store=self._store,
            source=self._source,
            codes=self._codes,
            now=now,
            since=week_start(now, self._day, self._time),
            offset=self._offset,
            extra_standard=self._extra,
            budget_s=self._budget_s,
            play_time=self._play_time,
            income=self._income,
            network=self._network,
        )

    def _report(self, arguments: object) -> tuple[str, bool]:
        if not isinstance(arguments, Mapping) or arguments:
            raise _InvalidArguments("tool ini tidak menerima argumen; kirim objek kosong {}.")
        report = self.gather()
        if not report.readable:
            # An error result: a reply without a successful call is never said by herself.
            failed = {"ok": False, "message": _NOTHING_READABLE, "problems": _problems(report)}
            return _to_json(failed), True
        return _to_json(report_json(report)), False


def _spec(games: list[str], *, codes: bool, play: bool, income: bool, network: bool) -> ToolSpec:
    """Built once from the configured games, so every request sends the same bytes and the
    prompt cache holds."""
    names = ", ".join(f"{GAMES[game].name} = {game}" for game in games)
    sections = ["the banners and events that end within 7 days"]
    if codes:
        sections.append("the redeem codes not redeemed yet")
    if play:
        sections.append(
            "the time each game was open in the last 7 days by your own count while `waifu "
            "run` was on (`play_time`; minimized or idle counts too)"
        )
    if income:
        sections.append(
            "the premium currency earned so far this month by HoYoLAB's monthly diary, with "
            "what it is worth in pulls at 160 each (`income_this_month`)"
        )
    last = f"{', '.join(sections[:-1])}, and {sections[-1]}" if len(sections) > 1 else sections[0]
    # gacha_income's no-judging rule is not here when Master asks how the week went.
    information = (
        "Present banners, events, and this month's income only as information for planning: "
        "never suggest pulling, spending money, or topping up, and never judge the amount earned."
        if income
        else "Present banners and events only as information: never suggest pulling, spending "
        "money, or topping up."
    )
    # The play_time tool's own no-lecture rule is shut out of her report turn with that tool.
    news = (
        " Tell the play time only as news: never judge whether it is too much, and never scold "
        "or lecture Master about how long they play."
        if play
        else ""
    )
    outages = (
        "`network`, there only when it happened, counts the times Master's internet was down "
        "in the last 7 days while `waifu run` watched it, with their minutes. "
        if network
        else ""
    )
    return ToolSpec(
        name=WEEKLY_REPORT,
        description=(
            f"Master's week in their HoYoverse games ({names}), read now in one call. For each "
            "game: the pulls of the last 7 days (how many on each banner, and each 5-star, an "
            "S-rank in ZZZ, with the pulls it took and whether the 50/50 was won, judged from "
            "the standard pool, so it can be wrong), each banner's pity and whether the next "
            "5-star is guaranteed, this season's endgame progress in every mode with the "
            f"seasons that end within 7 days, {last}. {outages}Use it for your own weekly game "
            "report and when Master asks how their week in these games went. Times are local. "
            "A part with a `*_problem` could not be read: say briefly that it is unavailable and "
            "tell the rest; `hint` says how to read the whole history. HoYoverse lists pulls "
            "about an hour late. "
            f"{information}{news}"
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )


def _problems(report: WeeklyReport) -> list[str]:
    """Every problem of the report, each once, in the order of the games and their sections."""
    return list(dict.fromkeys(problem for week in report.games for problem in week.problems))


class WeeklyReportTimer(PromptTimer):
    """Asks for her weekly game report once on the configured day, the first time `waifu run`
    is on from the configured time until midnight, from a daemon thread. A report day the PC
    was off is skipped, and a report still waiting at midnight is not said: the week's report
    is never said on another day."""

    def __init__(
        self,
        config: WeeklyReportConfig,
        *,
        request: Callable[[str, datetime], None],
        now: Callable[[], datetime] = datetime.now,
        tick_seconds: float = 30,
    ) -> None:
        day = get_args(Weekday).index(config.day)
        super().__init__(
            WEEKLY_REPORT_PROMPT,
            due=lambda moment: moment.weekday() == day and moment.time() >= config.time,
            expires=end_of_day,
            state_path=config.state_path,
            request=request,
            what="weekly game report",
            when=f"on {config.day.capitalize()} from {config.time:%H:%M}",
            thread_name="weekly-report",
            now=now,
            tick_seconds=tick_seconds,
        )


# --- the JSON for the brain ----------------------------------------------------------------


def report_json(report: WeeklyReport) -> dict[str, Any]:
    """The week as compact facts with local times, never what was spent."""
    now = report.now
    data: dict[str, Any] = {
        "as_of": _local(now, now),
        "since": _local(report.since, now),
        "note": HISTORY_NOTE,
        "games": [_game_json(week, now) for week in report.games],
    }
    outages = report.network
    if outages is not None and outages.count:
        data["network"] = {"outages": outages.count, "minutes": whole_minutes(outages.seconds)}
    if report.network_problem is not None:
        data["network_problem"] = report.network_problem
    return data


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
    if week.play is not None or week.play_problem is not None:
        data["play_time"] = None if week.play is None else _play_json(week.play)
        data["play_time_problem"] = week.play_problem
    if week.income is not None or week.income_problem is not None:
        data["income_this_month"] = None if week.income is None else _income_json(week.income)
        data["income_problem"] = week.income_problem
    return data


def _income_json(income: GameIncome) -> dict[str, Any]:
    return {
        "month": month_text(income.month),
        "currency": GAMES[income.game].currency,
        "earned": income.earned,
        "pulls": income.pulls,
        **_present(today=income.today),
    }


def _play_json(play: PlayTotal) -> dict[str, int]:
    hours, minutes = divmod(whole_minutes(play.seconds), 60)
    return {"hours": hours, "minutes": minutes, "sessions": play.sessions}


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


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
