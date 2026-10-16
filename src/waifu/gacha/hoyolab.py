"""HoYoLAB for the owner's games via genshin.py: accounts, notes, check-in, game data, redeem codes.

genshin.py is async (aiohttp). Each public call here is one `asyncio.run` with a new
`genshin.Client`, so the voice loop (the brain's tool calls) and the watcher thread
never share an event loop, a session, or a lock. Failures become `GachaError`s whose
Indonesian message tells the owner what to do; the cookie never reaches a message or
a log line. Notes are read with `autoauth=False` and the Battle Chronicle is only read:
Cyrene never changes HoYoLAB settings.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from waifu.config import cookie_names
from waifu.gacha import genshin_impact
from waifu.gacha.codes import RedeemResult
from waifu.gacha.game_data import Endgame, GameCalendar
from waifu.gacha.games import GAMES, Game
from waifu.gacha.genshin_impact import GenshinCharacter, GenshinCharacterDetail
from waifu.gacha.starrail import (
    HsrCalendar,
    HsrCharacter,
    HsrEndgame,
    read_calendar,
    read_character,
    read_endgame,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")
ErrorKind = Literal[
    "no_cookie",
    "cookie",
    "notes_off",
    "captcha",
    "account",
    "rate_limit",
    "network",
    "authkey",
    "locked",
    "chronicle_private",
    "other",
]
ClientFactory = Callable[[str], Any]
Sleep = Callable[[float], Awaitable[None]]

# 10102: Real-Time Notes are off for the account; 10104: the notes are not readable.
NOTES_OFF_RETCODES = frozenset({10102, 10104})
GAME_BIZ_PREFIXES = {"hkrpg_": "hsr", "hk4e_": "genshin", "nap_": "zzz"}
NO_COOKIE = "Cookie HoYoLAB belum diisi. Isi gacha.cookie di config.yaml (lihat README)."
# Formatted with the game's short name (HSR, Genshin, ZZZ).
CHRONICLE_PRIVATE = (
    "Data {game} ini tidak bisa dibaca karena Battle Chronicle di HoYoLAB tidak publik. "
    "Pengaturannya ada di HoYoLAB, di Battle Chronicle; Cyrene tidak mengubahnya."
)
ENDGAME_REQUESTS_AT_ONCE = 2
HSR = GAMES["hsr"]
GENSHIN = GAMES["genshin"]
# Each endgame mode's Battle Chronicle request: this season, for the given uid. Anomaly
# Arbitration's schedule_type=1 is this season (genshin.py's default 3 returns the last
# three). The Imaginarium Theater's answer always holds several seasons (`previous` only
# warns), and its detail would need genshin.py's character-name list.
_ENDGAME_REQUESTS: dict[str, Callable[[Any, int], Awaitable[Any]]] = {
    "memory_of_chaos": lambda client, uid: client.get_starrail_challenge(uid),
    "pure_fiction": lambda client, uid: client.get_starrail_pure_fiction(uid),
    "apocalyptic_shadow": lambda client, uid: client.get_starrail_apc_shadow(uid),
    "anomaly_arbitration": lambda client, uid: client.get_anomaly_arbitration(uid, schedule_type=1),
    "spiral_abyss": lambda client, uid: client.get_genshin_spiral_abyss(uid),
    "imaginarium_theater": lambda client, uid: client.get_imaginarium_theater(
        uid, need_detail=False
    ),
    "stygian_onslaught": lambda client, uid: client.get_stygian_onslaught(uid),
    "shiyu_defense": lambda client, uid: client.get_shiyu_defense(uid),
    "deadly_assault": lambda client, uid: client.get_deadly_assault(uid),
}
# Codes tried per redeem_codes call; the rest are left out of its results for a later call.
REDEEM_CODES_AT_MOST = 10
# Between two redemption attempts: HoYoLAB refuses one about 5 s after the last (cooldown).
REDEEM_GAP_S = 6.0
EXPIRED_RETCODE = -2001  # genshin.py raises RedemptionInvalid for it as for an unknown code
# What fails every later code of a redemption run the same way: the run stops there, and each
# remaining code gets the same outcome, untried.
STOPS_REDEEMING: frozenset[ErrorKind] = frozenset({"cookie", "network", "rate_limit", "captcha"})
# How the message of a code left to redeem again (cooldown, error) begins; what kept it follows.
NOT_REDEEMED = "Kode {code} belum bisa ditukarkan: "
# What she says about each outcome; `problem` is the GachaError message of an error.
REDEEM_MESSAGES = {
    "redeemed": "Kode {code} berhasil ditukarkan. Hadiahnya ada di surat dalam game {game}.",
    "claimed": "Kode {code} sudah pernah ditukarkan di akun {game} ini.",
    "expired": "Kode {code} sudah kedaluwarsa.",
    "invalid": "Kode {code} tidak valid menurut HoYoLAB.",
    "region": "Kode {code} tidak berlaku di server akun {game} ini.",
    "level": "Level akun {game} belum cukup untuk kode {code}.",
    "cooldown": NOT_REDEEMED + "HoYoLAB sedang membatasi penukaran kode. Coba lagi sebentar lagi.",
    "cookie": (
        "Cookie HoYoLAB tidak bisa dipakai untuk menukarkan kode. Penukaran butuh "
        "cookie_token_v2, dan cookie-nya mungkin sudah kedaluwarsa: salin ulang dari HoYoLAB "
        "lalu isi gacha.cookie di config.yaml."
    ),
    "error": NOT_REDEEMED + "{problem}",
}


class GachaError(Exception):
    """A HoYoLAB or history problem, with what the owner can do about it."""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class Account:
    game: str
    uid: int
    nickname: str
    level: int
    server_name: str


@dataclass(frozen=True)
class WeeklyTask:
    name: str
    current: int
    max: int

    @property
    def done(self) -> bool:
        return self.current >= self.max


@dataclass(frozen=True)
class GameStatus:
    game: str
    stamina: int
    stamina_max: int
    stamina_full_in_s: int
    daily_current: int
    daily_max: int
    daily_done: bool
    weekly: tuple[WeeklyTask, ...]
    expeditions_total: int
    expeditions_finished: int
    checked_in: bool | None


@dataclass(frozen=True)
class CheckinResult:
    game: str
    claimed: bool  # False: it had already been claimed today
    reward: str | None


def _default_client_factory(cookie: str) -> Any:
    import genshin

    return genshin.Client(cookie, lang="en-us")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class HoyolabClient:
    def __init__(
        self,
        cookie: str | None,
        *,
        uids: Mapping[str, int] | None = None,
        client_factory: ClientFactory | None = None,
        timeout_s: float = 20.0,
        now: Callable[[], datetime] = _utc_now,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._cookie = cookie
        self._uids = dict(uids or {})
        self._client_factory = client_factory or _default_client_factory
        self._timeout_s = timeout_s
        self._now = now  # when a calendar or an endgame season is read
        self._sleep = sleep  # the waits between redemption attempts
        self._uids_lock = threading.Lock()

    @property
    def has_cookie_token(self) -> bool:
        """Whether the cookie can make history authkeys (it holds cookie_token_v2)."""
        return self._cookie is not None and "cookie_token_v2" in cookie_names(self._cookie)

    def accounts(self) -> list[Account]:
        """The owner's accounts of the three games bound to the cookie."""
        return self._run(self._accounts, None)

    def status(self, games: Iterable[str]) -> dict[str, GameStatus | GachaError]:
        """Each game's live notes and check-in state; a failing game does not hide the others."""
        games = list(games)

        async def all_games(client: Any) -> list[GameStatus | BaseException]:
            await self._resolve_uids(client, games)
            return await asyncio.gather(
                *(self._status(client, GAMES[game]) for game in games), return_exceptions=True
            )

        try:
            results = self._run(all_games, None)
        except GachaError as error:
            return dict.fromkeys(games, error)
        return {
            game: _to_error(result, GAMES[game]) if isinstance(result, BaseException) else result
            for game, result in zip(games, results, strict=True)
        }

    def checkin(self, game_id: str) -> CheckinResult:
        """Claim today's HoYoLAB check-in reward of one game."""
        game = GAMES[game_id]

        async def claim(client: Any) -> CheckinResult:
            from genshin import errors

            try:
                reward = await client.claim_daily_reward(game=_hoyolab_game(game))
            except errors.AlreadyClaimed:
                return CheckinResult(game.id, claimed=False, reward=None)
            return CheckinResult(game.id, claimed=True, reward=f"{reward.amount} {reward.name}")

        return self._run(claim, game)

    def authkey(self, game_id: str) -> str:
        """A fresh history authkey for one game, made from the cookie's cookie_token_v2."""
        game = GAMES[game_id]
        if self._cookie is not None and not self.has_cookie_token:
            raise GachaError("authkey", "Cookie HoYoLAB tidak memuat cookie_token_v2.")

        async def fetch(client: Any) -> str:
            uid = (await self._resolve_uids(client, [game.id]))[game.id]
            return await client.fetch_authkey(uid, game=_hoyolab_game(game))

        return self._run(fetch, game)

    def hsr_characters(self) -> tuple[HsrCharacter, ...]:
        """Every owned HSR character in detail, from the HoYoLAB Battle Chronicle."""

        async def characters(client: Any) -> tuple[HsrCharacter, ...]:
            uid = await self._game_uid(client, HSR)
            response = await _chronicle(client.get_starrail_characters(uid), HSR)
            return tuple(read_character(character) for character in response.avatar_list)

        return self._run(characters, HSR)

    def hsr_endgame(self, modes: Iterable[str]) -> dict[str, HsrEndgame | GachaError]:
        """This season of each endgame mode; a failing mode does not hide the others."""
        return self._endgame(HSR, modes, read_endgame)

    def hsr_calendar(self) -> HsrCalendar:
        """The HSR event calendar: warps, events, and endgame windows, in absolute times."""

        async def calendar(client: Any) -> HsrCalendar:
            uid = await self._game_uid(client, HSR)
            fetched_at = self._now()
            model = await _chronicle(client.get_starrail_event_calendar(uid), HSR)
            return read_calendar(model, fetched_at)

        return self._run(calendar, HSR)

    def genshin_characters(self) -> tuple[GenshinCharacter, ...]:
        """Every owned Genshin character with its weapon, from the HoYoLAB Battle Chronicle."""

        async def characters(client: Any) -> tuple[GenshinCharacter, ...]:
            uid = await self._game_uid(client, GENSHIN)
            models = await _chronicle(client.get_genshin_characters(uid), GENSHIN)
            return tuple(genshin_impact.read_character(model) for model in models)

        return self._run(characters, GENSHIN)

    def genshin_character_details(self, refs: Sequence[int]) -> tuple[GenshinCharacterDetail, ...]:
        """The detail of the Genshin characters with these refs, in one request (none without
        refs). The refs are always passed: without them genshin.py first reads the whole
        roster (a second request) and HoYoLAB answers every owned character in detail."""
        wanted = list(refs)
        if not wanted:
            return ()

        async def details(client: Any) -> tuple[GenshinCharacterDetail, ...]:
            uid = await self._game_uid(client, GENSHIN)
            request = client.get_genshin_detailed_characters(uid, characters=wanted)
            response = await _chronicle(request, GENSHIN)
            return tuple(genshin_impact.read_detail(model) for model in response.characters)

        return self._run(details, GENSHIN)

    def genshin_endgame(self, modes: Iterable[str]) -> dict[str, Endgame | GachaError]:
        """This season of each Genshin endgame mode; a failing mode does not hide the others.
        Of the seasons an answer holds, the one running now is read."""
        fetched_at = self._now()

        def read(mode: str, model: Any) -> Endgame:
            return genshin_impact.read_endgame(mode, model, fetched_at)

        return self._endgame(GENSHIN, modes, read)

    def genshin_calendar(self) -> GameCalendar:
        """The Genshin event calendar: banners, events, and endgame windows, in absolute times."""

        async def calendar(client: Any) -> GameCalendar:
            uid = await self._game_uid(client, GENSHIN)
            fetched_at = self._now()
            model = await _chronicle(client.get_genshin_event_calendar(uid), GENSHIN)
            return genshin_impact.read_calendar(model, fetched_at)

        return self._run(calendar, GENSHIN)

    def redeem_codes(
        self,
        game_id: str,
        codes: Sequence[str],
        *,
        on_result: Callable[[RedeemResult], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> list[RedeemResult]:
        """Redeem codes on HoYoLAB for the owner's account of one game, one at a time.

        Only the first REDEEM_CODES_AT_MOST codes are tried and answered; the caller keeps
        the rest for a later call. Attempts are REDEEM_GAP_S apart, each with its own
        timeout, and a cooldown answer is tried once more. A refused cookie, or HoYoLAB
        unreachable (a timeout or connection failure), rate-limiting, or asking for a
        captcha, stops there: that code and every remaining one get the cookie or the error
        outcome, the remaining ones untried. A failure of the call as a whole (the account
        lookup, the overall bound) does the same for each code without an outcome yet,
        keeping the outcomes already known. `on_result` (which must not raise) gets each
        result as soon as it is known, in order, so a run cut short keeps them.
        `should_stop`, asked before each attempt and each gap, ends the run once it says
        so: an attempt HoYoLAB is answering is kept, a cooldown answer is not tried again,
        and the codes not tried yet are left out of the results for a later call. Without a
        cookie it raises GachaError no_cookie before asking HoYoLAB.
        """
        game = GAMES[game_id]
        if len(codes) > REDEEM_CODES_AT_MOST:
            logger.info(
                "Redeeming %d of %d %s codes; the rest wait for a later call",
                REDEEM_CODES_AT_MOST,
                len(codes),
                game.short,
            )
        codes = list(codes[:REDEEM_CODES_AT_MOST])
        results: list[RedeemResult] = []

        def keep(result: RedeemResult) -> None:
            results.append(result)
            if on_result is not None:
                on_result(result)

        def stopped() -> bool:
            return should_stop is not None and should_stop()

        async def redeem(client: Any) -> None:
            uid = await asyncio.wait_for(self._game_uid(client, game), self._timeout_s)
            for index, code in enumerate(codes):
                go_on = await self._next_attempt(stopped) if index else not stopped()
                if not go_on:
                    return
                keep(await self._redeem(client, game, uid, code, stopped))

        # Ten attempts that all time out, with the gaps between them, fit in this bound.
        bound_s = REDEEM_CODES_AT_MOST * (self._timeout_s + REDEEM_GAP_S)
        try:
            self._run(redeem, game, timeout_s=bound_s)
        except GachaError as error:
            if error.kind == "no_cookie":
                raise
            outcome = "cookie" if error.kind == "cookie" else "error"
            for code in codes[len(results) :]:
                keep(_redeem_result(game, code, outcome, error.message))
        if len(results) < len(codes):
            logger.info(
                "Stopped redeeming %s codes; %d left untried for later",
                game.short,
                len(codes) - len(results),
            )
        logger.info(
            "Redeeming %s codes: %s",
            game.short,
            ", ".join(f"{result.code} {result.outcome}" for result in results),
        )
        return results

    def _run(
        self,
        work: Callable[[Any], Awaitable[T]],
        game: Game | None,
        *,
        timeout_s: float | None = None,
    ) -> T:
        if self._cookie is None:
            raise GachaError("no_cookie", NO_COOKIE)
        cookie = self._cookie
        bound_s = self._timeout_s if timeout_s is None else timeout_s

        async def main() -> T:
            return await asyncio.wait_for(work(self._client_factory(cookie)), bound_s)

        try:
            return asyncio.run(main())
        except GachaError:
            raise
        except Exception as exc:
            raise _to_error(exc, game) from None

    def _endgame(
        self, game: Game, modes: Iterable[str], read: Callable[[str, Any], T]
    ) -> dict[str, T | GachaError]:
        """This season of each of one game's endgame modes, each read from its model with
        `read(mode, model)`: at most ENDGAME_REQUESTS_AT_ONCE requests in flight, a failing
        mode does not hide the others, and a failure of the whole call (the account lookup,
        the timeout) is every mode's error."""
        modes = list(modes)

        async def all_modes(client: Any) -> list[T | BaseException]:
            uid = await self._game_uid(client, game)
            at_once = asyncio.Semaphore(ENDGAME_REQUESTS_AT_ONCE)

            async def one(mode: str) -> T:
                async with at_once:
                    model = await _chronicle(_ENDGAME_REQUESTS[mode](client, uid), game)
                return read(mode, model)

            return await asyncio.gather(*(one(mode) for mode in modes), return_exceptions=True)

        try:
            results = self._run(all_modes, game)
        except GachaError as error:
            return dict.fromkeys(modes, error)
        return {
            mode: _to_error(result, game) if isinstance(result, BaseException) else result
            for mode, result in zip(modes, results, strict=True)
        }

    async def _accounts(self, client: Any) -> list[Account]:
        accounts = []
        for account in await client.get_game_accounts():
            game = _game_of(account.game_biz)
            if game is not None:
                accounts.append(
                    Account(game, account.uid, account.nickname, account.level, account.server_name)
                )
        return accounts

    async def _resolve_uids(self, client: Any, games: Iterable[str]) -> dict[str, int]:
        """The uid of each game: from config, else the highest-level account (looked up once)."""
        missing = [game for game in games if game not in self._uids]
        if missing:
            preferred = preferred_uids(await self._accounts(client))
            with self._uids_lock:
                for game, uid in preferred.items():
                    self._uids.setdefault(game, uid)
        return dict(self._uids)

    async def _game_uid(self, client: Any, game: Game) -> int:
        """The uid of one game, passed to every request.

        genshin.py then never looks one up itself, and it knows the server offset that
        makes the endgame times aware and the server a code is redeemed on.
        """
        uid = (await self._resolve_uids(client, [game.id])).get(game.id)
        if uid is None:
            raise GachaError("account", f"Akun {game.short} tidak ditemukan di HoYoLAB ini.")
        return uid

    async def _redeem(
        self, client: Any, game: Game, uid: int, code: str, stopped: Callable[[], bool]
    ) -> RedeemResult:
        """One code: a cooldown answer waits REDEEM_GAP_S and tries once more, unless the run
        is stopped. A failure that would fail every later code too (STOPS_REDEEMING) is
        raised as its GachaError."""
        from genshin import errors

        for attempt in range(2):
            if attempt and not await self._next_attempt(stopped):
                break
            try:
                await asyncio.wait_for(
                    client.redeem_code(code, uid, game=_hoyolab_game(game)), self._timeout_s
                )
            except errors.RedemptionCooldown:
                continue
            except Exception as exc:
                return _redemption_failure(game, code, exc)
            return _redeem_result(game, code, "redeemed")
        return _redeem_result(game, code, "cooldown")

    async def _next_attempt(self, stopped: Callable[[], bool]) -> bool:
        """Wait REDEEM_GAP_S for the next redemption attempt; False, without waiting or after
        it, once the run is stopped."""
        if stopped():
            return False
        await self._sleep(REDEEM_GAP_S)
        return not stopped()

    async def _status(self, client: Any, game: Game) -> GameStatus:
        uid = self._uids.get(game.id)
        if uid is None:
            raise GachaError("account", f"Akun {game.short} tidak ditemukan di HoYoLAB ini.")
        read_notes = {
            "hsr": client.get_starrail_notes,
            "genshin": client.get_genshin_notes,
            "zzz": client.get_zzz_notes,
        }[game.id]
        notes, info = await asyncio.gather(
            read_notes(uid, autoauth=False),
            client.get_reward_info(game=_hoyolab_game(game)),
            return_exceptions=True,
        )
        if isinstance(notes, BaseException):
            raise notes
        if isinstance(info, BaseException):
            logger.info("Check-in state of %s unknown: %s", game.short, type(info).__name__)
            checked_in = None
        else:
            checked_in = info.signed_in
        return _STATUS_READERS[game.id](notes, checked_in)


def preferred_uids(accounts: Iterable[Account]) -> dict[str, int]:
    """Each game's highest-level account (the first of equals, as genshin.py picks too)."""
    best: dict[str, Account] = {}
    for account in accounts:
        if account.game not in best or account.level > best[account.game].level:
            best[account.game] = account
    return {game: account.uid for game, account in best.items()}


def _hoyolab_game(game: Game) -> Any:
    import genshin

    return genshin.Game(game.hoyolab_game)


async def _chronicle(request: Awaitable[T], game: Game) -> T:
    """A Battle Chronicle data request of `game`: data hidden from HoYoLAB is
    chronicle_private, with a message that names the game.

    The same retcode means notes_off for the Real-Time Notes, so only these requests
    turn genshin.py's `DataNotPublic` into this kind.
    """
    from genshin import errors

    try:
        return await request
    except errors.DataNotPublic:
        message = CHRONICLE_PRIVATE.format(game=game.short)
        raise GachaError("chronicle_private", message) from None


def _redeem_result(game: Game, code: str, outcome: str, problem: str = "") -> RedeemResult:
    message = REDEEM_MESSAGES[outcome].format(code=code, game=game.short, problem=problem)
    return RedeemResult(game.id, code, outcome, message)


def _redemption_failure(game: Game, code: str, exc: Exception) -> RedeemResult:
    """The outcome of a redemption HoYoLAB refused or that failed on the way; a failure that
    would fail every later code too (STOPS_REDEEMING) is raised as its GachaError instead."""
    from genshin import errors

    if isinstance(exc, errors.RedemptionClaimed):
        return _redeem_result(game, code, "claimed")
    if isinstance(exc, errors.RedemptionInvalid):
        return _redeem_result(
            game, code, "expired" if exc.retcode == EXPIRED_RETCODE else "invalid"
        )
    if isinstance(exc, errors.RedemptionRegionLock):
        return _redeem_result(game, code, "region")
    if isinstance(exc, errors.RedeemGameLevelTooLow):
        return _redeem_result(game, code, "level")
    error = _to_error(exc, game)
    if error.kind in STOPS_REDEEMING:
        raise error from None
    return _redeem_result(game, code, "error", error.message)


def _game_of(game_biz: str) -> str | None:
    return next(
        (game for prefix, game in GAME_BIZ_PREFIXES.items() if game_biz.startswith(prefix)), None
    )


def _seconds(delta: Any) -> int:
    return max(0, int(delta.total_seconds()))


def _hsr_status(notes: Any, checked_in: bool | None) -> GameStatus:
    echo_of_war = notes.max_weekly_discounts - notes.remaining_weekly_discounts
    return GameStatus(
        game="hsr",
        stamina=notes.current_stamina,
        stamina_max=notes.max_stamina,
        stamina_full_in_s=_seconds(notes.stamina_recover_time),
        daily_current=notes.current_train_score,
        daily_max=notes.max_train_score,
        daily_done=notes.current_train_score >= notes.max_train_score,
        weekly=(
            WeeklyTask("Echo of War", echo_of_war, notes.max_weekly_discounts),
            WeeklyTask("Simulated Universe", notes.current_rogue_score, notes.max_rogue_score),
        ),
        expeditions_total=len(notes.expeditions),
        expeditions_finished=sum(expedition.finished for expedition in notes.expeditions),
        checked_in=checked_in,
    )


def _genshin_status(notes: Any, checked_in: bool | None) -> GameStatus:
    tasks = notes.daily_task
    weekly_bosses = notes.max_resin_discounts - notes.remaining_resin_discounts
    return GameStatus(
        game="genshin",
        stamina=notes.current_resin,
        stamina_max=notes.max_resin,
        stamina_full_in_s=_seconds(notes.remaining_resin_recovery_time),
        daily_current=tasks.completed_tasks,
        daily_max=tasks.max_tasks,
        # The four commissions only count once their bonus has been claimed in the city.
        daily_done=tasks.completed_tasks >= tasks.max_tasks and tasks.claimed_commission_reward,
        weekly=(WeeklyTask("Weekly Bosses", weekly_bosses, notes.max_resin_discounts),),
        expeditions_total=len(notes.expeditions),
        expeditions_finished=sum(expedition.finished for expedition in notes.expeditions),
        checked_in=checked_in,
    )


def _zzz_status(notes: Any, checked_in: bool | None) -> GameStatus:
    weekly = notes.weekly_task
    return GameStatus(
        game="zzz",
        stamina=notes.battery_charge.current,
        stamina_max=notes.battery_charge.max,
        stamina_full_in_s=max(0, notes.battery_charge.seconds_till_full),
        daily_current=notes.engagement.current,
        daily_max=notes.engagement.max,
        daily_done=notes.engagement.current >= notes.engagement.max,
        weekly=(WeeklyTask("Ridu Weekly", weekly.cur_point, weekly.max_point),) if weekly else (),
        expeditions_total=0,
        expeditions_finished=0,
        checked_in=checked_in,
    )


_STATUS_READERS: dict[str, Callable[[Any, bool | None], GameStatus]] = {
    "hsr": _hsr_status,
    "genshin": _genshin_status,
    "zzz": _zzz_status,
}


def _to_error(exc: BaseException, game: Game | None) -> GachaError:
    """The owner-facing error for a failed HoYoLAB call (logged without any secret)."""
    import aiohttp
    from genshin import errors

    if isinstance(exc, GachaError):
        return exc
    name = f" {game.short}" if game is not None else ""
    if isinstance(exc, (errors.GeetestError, errors.DailyGeetestTriggered)):
        return GachaError(
            "captcha",
            f"HoYoLAB minta verifikasi captcha{name}. Buka HoYoLAB di browser sekali "
            "(untuk check-in, ambil manual dulu), lalu coba lagi.",
        )
    if isinstance(exc, (errors.TooManyRequests, errors.VisitsTooFrequently)):
        return GachaError(
            "rate_limit", "HoYoLAB sedang membatasi permintaan. Coba lagi beberapa menit lagi."
        )
    if isinstance(exc, errors.InvalidCookies):
        return GachaError(
            "cookie",
            "Cookie HoYoLAB tidak berlaku lagi. Ambil ulang dari hoyolab.com lalu isi "
            "gacha.cookie di config.yaml.",
        )
    if isinstance(exc, errors.AccountNotFound):
        return GachaError("account", f"Akun{name} tidak ditemukan di HoYoLAB ini.")
    if isinstance(exc, errors.GenshinException):
        if exc.retcode in NOTES_OFF_RETCODES:
            return GachaError(
                "notes_off",
                f"Real-Time Notes{name} di HoYoLAB belum aktif. Aktifkan di HoYoLAB: "
                "Battle Chronicle, lalu Settings.",
            )
        logger.warning("HoYoLAB refused a%s request: retcode %s", name, exc.retcode)
        return GachaError("other", f"HoYoLAB menolak permintaan{name} (kode {exc.retcode}).")
    if isinstance(exc, (TimeoutError, aiohttp.ClientError, OSError)):
        logger.warning("HoYoLAB unreachable: %s", type(exc).__name__)
        return GachaError("network", "HoYoLAB tidak bisa dihubungi. Cek koneksi internet.")
    logger.warning("Unexpected HoYoLAB failure: %s", type(exc).__name__)
    return GachaError("other", f"Data HoYoLAB{name} tidak bisa dibaca saat ini.")
