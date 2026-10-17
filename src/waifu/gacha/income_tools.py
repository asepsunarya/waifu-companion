"""The tool that tells Master what they earned in their HoYoverse games in a month.

`gacha_income` reads each configured game's monthly diary on HoYoLAB (Genshin's Traveler's
Diary, HSR's Trailblaze Monthly Calendar, ZZZ's Inter-Knot monthly report) only when asked, for
this month or one of the two before, counted in server time. The spec is static English built
once from the configured games, so it stays byte-identical for the brain's lifetime and keeps
the prompt cache warm. Results are compact JSON, and `run` never raises: invalid arguments,
HoYoLAB failures, and anything unexpected become error results. What HoYoLAB answered is kept
in memory for a few minutes, never on disk.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from waifu.brain.base import ToolSpec
from waifu.gacha.games import GAMES, checkin_day
from waifu.gacha.hoyolab import GachaError
from waifu.gacha.income import GameIncome, IncomeSource, income_months, month_text, parse_month
from waifu.gacha.tools import LOCAL_TIME

logger = logging.getLogger(__name__)

GACHA_INCOME = "gacha_income"
INCOME_TTL = timedelta(minutes=15)
SOURCES_MAX = 8
_KEYS = frozenset({"game", "month"})
_FAILED = "Pendapatan gacha tidak bisa dibaca saat ini."
# What each game's diary gives; the description is built only from the configured games.
_DIARIES = {
    "hsr": (
        "Honkai: Star Rail's Trailblaze Monthly Calendar gives Stellar Jade with `today` and "
        "`yesterday`, the month before, and the Star Rail Pass earned under `others` (HoYoLAB "
        "does not say whether Special or regular). "
    ),
    "genshin": (
        "Genshin Impact's Traveler's Diary gives Primogem with `today`, the month before, and "
        "Mora under `others`. "
    ),
    "zzz": (
        "Zenless Zone Zero's Inter-Knot monthly report gives Polychrome, with the Master Tape and "
        "Boopon earned under `others`, but no month before and no `today`. "
    ),
}


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


@dataclass(frozen=True)
class _Fetched:
    """What HoYoLAB answered for one game and month, and the local time it was read."""

    at: datetime
    value: GameIncome


def _local_now() -> datetime:
    return datetime.now().astimezone()


class IncomeTools:
    """A `ToolProvider` for the brain: Master's monthly income in the configured games.

    Successful reads are kept per process for INCOME_TTL under `income:<game>:<YYYY-MM>`, this
    month's with the server day too, so its `today` never outlives midnight there. The lock
    guards only the cache's dict: voice and phone turns run on different threads."""

    def __init__(
        self,
        source: IncomeSource,
        games: Sequence[str],
        *,
        server_utc_offset: int = 8,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        self._source = source
        self._games = list(games)
        self._offset = server_utc_offset
        self._now = now
        self._spec = _spec(self._games)
        self._cache: dict[str, _Fetched] = {}
        self._cache_lock = threading.Lock()

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == GACHA_INCOME:
                return self._income(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except Exception:
            logger.exception("Gacha income tool '%s' failed", name)
            return _error(_FAILED)
        return _error(f"Tool '{name}' bukan tool pendapatan gacha.")

    def _income(self, arguments: object) -> tuple[str, bool]:
        given = _arguments_mapping(arguments)
        now = self._now()
        months = income_months(now, self._offset)
        games = self._games_asked(given)
        month = _month(given, months)
        this_month = month == months[0]
        fetched, errors = self._read(games, month, this_month, now)
        if not fetched:
            return _error(" ".join(dict.fromkeys(error.message for error in errors.values())))
        data: dict[str, Any] = {
            # The oldest read among the games shown: some may come from the cache.
            "as_of": _local(min(each.at for each in fetched.values()), now),
            "month": month_text(month),
            "this_month": this_month,
            "games": [_game_json(fetched[game].value) for game in games if game in fetched],
        }
        if errors:
            data["errors"] = [
                {"game": game, "message": errors[game].message} for game in games if game in errors
            ]
        return _to_json(data), False

    def _games_asked(self, given: Mapping[str, object]) -> list[str]:
        """The game given, else every configured game."""
        if "game" not in given:
            return list(self._games)
        game = given["game"]
        if not isinstance(game, str) or game not in self._games:
            raise _InvalidArguments(f"'game' harus salah satu dari: {', '.join(self._games)}.")
        return [game]

    def _read(
        self, games: list[str], month: date, this_month: bool, now: datetime
    ) -> tuple[dict[str, _Fetched], dict[str, GachaError]]:
        """Each game from the cache when fresh; the stale ones from HoYoLAB in one call."""
        day = f":{checkin_day(now, self._offset)}" if this_month else ""
        keys = {game: f"income:{game}:{month_text(month)}{day}" for game in games}
        fetched: dict[str, _Fetched] = {}
        for game in games:
            kept = self._kept(keys[game], now)
            if kept is not None:
                fetched[game] = kept
        stale = [game for game in games if game not in fetched]
        errors: dict[str, GachaError] = {}
        if stale:
            for game, result in self._source.income(stale, month, this_month=this_month).items():
                if isinstance(result, GachaError):
                    errors[game] = result
                else:
                    fetched[game] = self._keep(keys[game], _Fetched(now, result))
        return fetched, errors

    def _kept(self, key: str, now: datetime) -> _Fetched | None:
        with self._cache_lock:
            kept = self._cache.get(key)
        # A clock set back makes the age negative: read again rather than trust it.
        if kept is None or not timedelta(0) <= now - kept.at < INCOME_TTL:
            return None
        return kept

    def _keep(self, key: str, fetched: _Fetched) -> _Fetched:
        with self._cache_lock:
            self._cache[key] = fetched
        return fetched


def _spec(games: list[str]) -> ToolSpec:
    """Built once from the configured games, so every request sends the same bytes and the
    prompt cache holds."""
    names = ", ".join(f"{GAMES[game].name} = {game}" for game in games)
    currencies = _listing([GAMES[game].currency for game in games], "or")
    return ToolSpec(
        name=GACHA_INCOME,
        description=(
            "What Master earned in their HoYoverse games in one month, from each game's monthly "
            f"diary on HoYoLAB: {names}. Set `game` for one game, or leave it out for every "
            "game; set `month` as YYYY-MM for an earlier month, or leave it out for this month. "
            "HoYoLAB keeps only this month and the two before, counted in server time. Each "
            "game gives its premium `currency`, how much was `earned` that month, and `pulls`, "
            "what it is worth in pulls at 160 each, counting only that currency; `sources` says "
            "where it came from, most first. "
            + "".join(_DIARIES[game] for game in games)
            + "`last_month` is the month before as HoYoLAB counts it, with `change_percent`: a "
            "month still running is mostly below it. `today` and `yesterday` come only for this "
            "month. HoYoLAB's diary may lag behind the game. A game that could not be read is "
            "under `errors` with its message, and `as_of` is when HoYoLAB was read, in local "
            f"time. Use it only when Master asks what they earned, such as how much {currencies} "
            "came this month or last month, never in your own greeting, the morning briefing, "
            "or anything you start yourself. Answer in one or two short sentences "
            "with the numbers rounded naturally: no lists and no parentheses, because your "
            "voice drops whatever is in parentheses. It is information for planning pulls: "
            "never suggest pulling, spending money, or topping up, and never judge the amount. "
            "Do not save these numbers with `remember` unless Master asks."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "game": {"type": "string", "enum": list(games)},
                "month": {"type": "string"},
            },
            "additionalProperties": False,
        },
    )


def _listing(items: list[str], conjunction: str) -> str:
    """'a', 'a or b', or 'a, b, or c'."""
    if len(items) <= 2:
        return f" {conjunction} ".join(items)
    return f"{', '.join(items[:-1])}, {conjunction} {items[-1]}"


def _month(given: Mapping[str, object], months: tuple[date, ...]) -> date:
    """The month given as YYYY-MM, one HoYoLAB keeps; else this month."""
    if "month" not in given:
        return months[0]
    kept = ", ".join(month_text(each) for each in months)
    invalid = _InvalidArguments(f"'month' harus YYYY-MM, salah satu dari: {kept}.")
    text = given["month"]
    if not isinstance(text, str):
        raise invalid
    try:
        month = parse_month(text)
    except ValueError:
        raise invalid from None
    if month not in months:
        raise invalid
    return month


def _game_json(income: GameIncome) -> dict[str, Any]:
    game = GAMES[income.game]
    return {
        "game": game.id,
        "name": game.name,
        "currency": game.currency,
        "earned": income.earned,
        "pulls": income.pulls,
        **_present(
            today=income.today,
            yesterday=income.yesterday,
            last_month=income.last_month,
            change_percent=income.change_percent,
        ),
        "sources": [
            {"name": source.name, "amount": source.amount, "percent": source.percent}
            for source in income.sources[:SOURCES_MAX]
        ],
        "others": [{"name": other.name, "amount": other.amount} for other in income.others],
    }


def _present(**values: object) -> dict[str, object]:
    """The keys that have a value: keys without one are left out."""
    return {key: value for key, value in values.items() if value is not None}


def _local(when: datetime, now: datetime) -> str:
    return when.astimezone(now.tzinfo).strftime(LOCAL_TIME)


def _arguments_mapping(arguments: object) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in _KEYS]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
