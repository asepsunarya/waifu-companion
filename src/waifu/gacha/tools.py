"""The two game tools the brain gets when the gacha buddy is on.

`game_status` reads HoYoLAB live; `gacha_history` refreshes the pull history from
the game. Both answer in compact JSON with local times, and never raise: a game
that failed shows up under "errors" or "problem" with what the owner can do.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol

from waifu.brain.base import ToolSpec
from waifu.gacha.games import GAMES, PULL_COST, Game, next_daily_reset
from waifu.gacha.history import HistoryReport, NewPull
from waifu.gacha.hoyolab import GachaError, GameStatus

GAME_STATUS = "game_status"
GACHA_HISTORY = "gacha_history"
HISTORY_NOTE = (
    "HoYoverse lists a pull about an hour after it is made, so pulls from just now may be missing."
)
LOCAL_TIME = "%Y-%m-%d %H:%M"


class StatusSource(Protocol):
    def status(self, games: Iterable[str]) -> dict[str, GameStatus | GachaError]: ...


class HistorySource(Protocol):
    def report(
        self, game_id: str, *, refresh: bool = True, full: bool = False
    ) -> HistoryReport: ...


class _InvalidArguments(Exception):
    pass


def _local_now() -> datetime:
    return datetime.now().astimezone()


class GachaTools:
    """A `ToolProvider` for the brain: Master's HoYoverse games."""

    def __init__(
        self,
        hoyolab: StatusSource,
        history: HistorySource,
        games: Sequence[str],
        *,
        server_utc_offset: int = 8,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        self._hoyolab = hoyolab
        self._history = history
        self._games = list(games)
        self._offset = server_utc_offset
        self._now = now

    def specs(self) -> list[ToolSpec]:
        game = {"type": "string", "enum": self._games}
        names = ", ".join(f"{GAMES[g].name} = {g}" for g in self._games)
        return [
            ToolSpec(
                name=GAME_STATUS,
                description=(
                    f"Live status of Master's HoYoverse games from HoYoLAB ({names}): stamina "
                    "(Trailblaze Power, Original Resin, Battery Charge) and when it is full, "
                    "the daily progress, weekly rewards left, expeditions, whether today's "
                    "HoYoLAB check-in is done, and when the daily reset comes (local times). "
                    "Use it whenever Master asks about these; never guess numbers. Leave `game` "
                    "out for every game."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"game": game},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name=GACHA_HISTORY,
                description=(
                    "Master's pull history for one game (warps, wishes, Signal Searches), "
                    "refreshed from the game: each banner's pity (pulls since the last 5-star, "
                    "an S-rank in ZZZ), pulls left to hard pity, whether the next 5-star is "
                    "guaranteed, the last 5-stars with the pulls each took and whether the 50/50 "
                    "was won (judged from the standard pool, so it can be wrong), and the pulls "
                    "that are new since the last check. Use it when Master just pulled or asks "
                    "about pity. HoYoverse lists pulls about an hour late, so a pull made just "
                    "now may be missing: say so. React like a friend: celebrate a won 50/50, "
                    "comfort Master after a lost one. Never suggest spending money or topping up."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"game": game},
                    "required": ["game"],
                    "additionalProperties": False,
                },
            ),
        ]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == GAME_STATUS:
                return self._status(self._game_argument(arguments, required=False))
            if name == GACHA_HISTORY:
                return self._report(self._game_argument(arguments, required=True))
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        return _error(f"Tool '{name}' bukan tool game.")

    def _game_argument(self, arguments: object, *, required: bool) -> str | None:
        if not isinstance(arguments, Mapping):
            raise _InvalidArguments("argumen harus berupa objek JSON.")
        unknown = [str(key) for key in arguments if key != "game"]
        if unknown:
            raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
        game = arguments.get("game")
        if game is None and not required:
            return None
        if game not in self._games:
            raise _InvalidArguments(f"'game' harus salah satu dari: {', '.join(self._games)}.")
        return game

    def _status(self, game: str | None) -> tuple[str, bool]:
        now = self._now()
        results = self._hoyolab.status([game] if game else self._games)
        games, errors = [], []
        for game_id, result in results.items():
            if isinstance(result, GachaError):
                errors.append({"game": game_id, "message": result.message})
            else:
                games.append(self._status_entry(GAMES[game_id], result, now))
        return _to_json({"games": games, "errors": errors}), not games

    def _status_entry(self, game: Game, status: GameStatus, now: datetime) -> dict[str, Any]:
        full = status.stamina >= status.stamina_max or status.stamina_full_in_s <= 0
        full_at = now + timedelta(seconds=status.stamina_full_in_s)
        entry: dict[str, Any] = {
            "game": game.id,
            "name": game.name,
            "stamina": {
                "name": game.stamina,
                "current": status.stamina,
                "max": status.stamina_max,
                "full": full,
                "full_in_minutes": 0 if full else math.ceil(status.stamina_full_in_s / 60),
                "full_at": None if full else full_at.strftime(LOCAL_TIME),
            },
            "daily": {
                "name": game.daily,
                "current": status.daily_current,
                "max": status.daily_max,
                "done": status.daily_done,
            },
            "weekly": [
                {"name": task.name, "current": task.current, "max": task.max}
                for task in status.weekly
            ],
        }
        if status.expeditions_total:
            entry["expeditions"] = {
                "finished": status.expeditions_finished,
                "total": status.expeditions_total,
            }
        entry["checked_in"] = status.checked_in
        entry["daily_reset"] = next_daily_reset(now, self._offset).strftime("%H:%M")
        return entry

    def _report(self, game: str) -> tuple[str, bool]:
        report = self._history.report(game)
        return _to_json(_report_json(report)), not report.stats and report.problem is not None


def _report_json(report: HistoryReport) -> dict[str, Any]:
    game = report.game
    data: dict[str, Any] = {
        "game": game.id,
        "name": game.name,
        "updated": report.updated,
        "note": HISTORY_NOTE,
        "latest_pull": report.latest_pull.strftime(LOCAL_TIME) if report.latest_pull else None,
        "new_pulls_count": report.new_count,
        "new_pulls": [_new_pull_json(game, pull) for pull in report.new_pulls],
        "banners": [],
        "currency_spent_estimate": {
            "currency": game.currency,
            "amount": sum(stats.total for stats in report.stats) * PULL_COST,
        },
    }
    for stats in report.stats:
        banner: dict[str, Any] = {
            "banner": stats.banner.name,
            "pity": stats.pity,
            "hard_pity": stats.banner.hard_pity,
            "to_hard_pity": stats.to_hard_pity,
            "total": stats.total,
        }
        if stats.guaranteed is not None:
            banner["guaranteed"] = stats.guaranteed
        banner["recent_top_pulls"] = [
            {"name": top.name, "pulls": top.pulls, "won_5050": top.won_5050, "date": top.time[:10]}
            for top in stats.recent_tops
        ]
        data["banners"].append(banner)
    if report.problem:
        data["problem"] = report.problem
    if report.hint:
        data["hint"] = report.hint
    return data


def _new_pull_json(game: Game, pull: NewPull) -> dict[str, Any]:
    top = pull.rarity == game.top_rarity
    entry: dict[str, Any] = {
        "banner": pull.banner.name,
        "name": pull.name,
        "rarity": _rarity_label(game, top),
    }
    if top:
        entry["pulls"] = pull.pulls
        entry["won_5050"] = pull.won_5050
    return entry


def _rarity_label(game: Game, top: bool) -> str:
    if game.id == "zzz":
        return "S" if top else "A"
    return "5★" if top else "4★"


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
