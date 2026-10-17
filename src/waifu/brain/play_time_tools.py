"""The tool that tells Master how long they played: `play_time`.

"Hari ini aku main berapa lama?" or "minggu ini Genshin berapa jam?": the tool reads the play
time store (`play_time.py`) for a WIB period and gives each tracked game's time and sessions,
whether it is open now, and the total, in which time two games were open together counts once.
It is her own count: only while `waifu run` was on, and the time a game was open, minimized or
idle too. The spec is static English built once from the configured games, so it stays
byte-identical for the brain's lifetime and keeps the prompt cache warm. In the turns she starts
herself (the morning greeting, the weekly game report, and the nightly journal question) it
refuses: play time is told only when Master asks, and the weekly report already holds it. `run`
never raises.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Protocol, cast

from waifu.brain.base import ToolSpec
from waifu.briefing import BRIEFING_PROMPT
from waifu.config import GameId, PlayTimeConfig
from waifu.finance.values import WIB
from waifu.gacha.games import GAMES
from waifu.gacha.tools import LOCAL_TIME
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.play_time import (
    PERIODS,
    PlayRecord,
    period_window,
    play_totals,
    played_seconds,
    playing_now,
    whole_minutes,
    wib_now,
)

logger = logging.getLogger(__name__)

PLAY_TIME = "play_time"
_KEYS = frozenset({"period", "game"})
# The turns she starts herself, each with what the tool answers in it.
_OWN_TURN_REFUSALS = {
    BRIEFING_PROMPT: "Jam main hanya untuk pertanyaan Master, bukan untuk sapaan pagi.",
    WEEKLY_REPORT_PROMPT: (
        "Jam main minggu ini sudah ada di weekly_report; jangan panggil tool lain."
    ),
    JOURNAL_PROMPT: "Jam main hanya untuk pertanyaan Master, bukan untuk pertanyaan jurnal malam.",
}
_FAILED = "Catatan jam main tidak bisa dibaca saat ini."


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class RecordSource(Protocol):
    """The part of `PlayTimeStore` the tool reads."""

    def record(self) -> PlayRecord: ...


class PlayTimeTools:
    """A `ToolProvider` for the brain: how long Master's games were open, per period."""

    def __init__(
        self,
        store: RecordSource,
        config: PlayTimeConfig,
        *,
        now: Callable[[], datetime] = wib_now,
    ) -> None:
        """`now` is aware WIB time."""
        self._store = store
        self._games: list[GameId] = list(config.games)
        self._poll = timedelta(seconds=config.poll_seconds)
        self._now = now
        self._spec = _spec(self._games)
        self._refusal: str | None = None

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with its words as heard: a turn she starts herself, the morning
        greeting, the weekly game report, or the nightly journal question, gets no play
        time."""
        self._refusal = _OWN_TURN_REFUSALS.get(user_text)

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        if name != PLAY_TIME:
            return _error(f"Tool '{name}' bukan tool jam main.")
        if self._refusal is not None:
            return _error(self._refusal)
        try:
            return self._play_time(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except Exception:
            logger.exception("Play time tool '%s' failed", name)
            return _error(_FAILED)

    def _play_time(self, arguments: object) -> tuple[str, bool]:
        given = _arguments_mapping(arguments)
        period = _choice(given, "period", PERIODS) or "today"
        game = cast(GameId | None, _choice(given, "game", self._games))
        games = [game] if game is not None else self._games
        now = self._now()
        since, until = period_window(period, now)
        record = self._store.record()
        mine = [s for s in (*record.sessions, *record.open) if s.game in games]
        tracked = [s for s in (*record.sessions, *record.open) if s.game in self._games]
        playing = playing_now(record, now, poll=self._poll)
        result = {
            "period": period,
            "from": _local(since),
            "to": _local(until),
            "games": [
                {
                    "game": game_id,
                    "name": GAMES[game_id].name,
                    **_hours_minutes(total.seconds),
                    "sessions": total.sessions,
                    "playing_now": game_id in playing,
                }
                for game_id, total in play_totals(record, games, since, until).items()
            ],
            "total": _hours_minutes(played_seconds(mine, since, until)),
            "tracked_since": _local(min(s.start for s in tracked)) if tracked else None,
        }
        return _to_json(result), False


def _spec(games: list[GameId]) -> ToolSpec:
    """Built once from the configured games, so every request sends the same bytes."""
    names = ", ".join(f"{GAMES[game].name} = {game}" for game in games)
    return ToolSpec(
        name=PLAY_TIME,
        description=(
            f"How long Master had their games open ({names}), by your own count: for a "
            "period, `today` (from midnight WIB, the default), `yesterday`, `week` (since "
            "Monday 00:00), or `month` (since the 1st), each game's time and number of "
            "sessions, whether it is open now (`playing_now`), and `total`, in which time two "
            "games were open together counts once. Give `game` for one game only. You count "
            "only while `waifu run` is on, and the time a game was open, minimized or idle "
            "too, not how actively Master played: say so when it matters. `tracked_since` is "
            "when your oldest kept record starts; null means nothing is recorded yet, so say "
            "you have not seen Master play while you were on. Use it only when Master asks "
            "how long they played or have been playing, never for a greeting, the morning "
            "briefing, the weekly report, or anything you start yourself. Answer in one or "
            "two short sentences with the time rounded and spoken naturally, such as dua jam "
            "lima belas menit or hampir tiga jam: no lists and no parentheses, because your "
            "voice drops whatever is in parentheses. Never scold or lecture Master about how "
            "long they play; a gentle word is fine. After an error result, say its message "
            "briefly and do not call again in this turn."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {"type": "string", "enum": list(PERIODS)},
                "game": {"type": "string", "enum": list(games)},
            },
            "additionalProperties": False,
        },
    )


def _choice(arguments: Mapping[str, object], key: str, allowed: Sequence[str]) -> str | None:
    """The argument `key` when given (null counts as left out, as local models send it)."""
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise _InvalidArguments(f"'{key}' harus salah satu dari: {', '.join(allowed)}.")
    return value


def _hours_minutes(seconds: int) -> dict[str, int]:
    hours, minutes = divmod(whole_minutes(seconds), 60)
    return {"hours": hours, "minutes": minutes}


def _local(moment: datetime) -> str:
    return moment.astimezone(WIB).strftime(LOCAL_TIME)


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
