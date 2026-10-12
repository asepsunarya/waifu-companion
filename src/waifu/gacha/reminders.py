"""When Cyrene speaks up about the games by herself, and what she says.

Pure rules over a status snapshot: stamina close to full, dailies left in the
evening, weeklies left on the last day of the week, a finished expedition batch,
a HoYoLAB problem the owner must fix, and the check-in she claimed. Each occasion
is told once; `ReminderState` remembers what was told, across restarts too.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from waifu.config import GachaRemindersConfig
from waifu.gacha.files import write_json_atomic
from waifu.gacha.games import (
    GAMES,
    Game,
    game_day,
    is_last_day_of_week,
    next_daily_reset,
    reminder_due,
)
from waifu.gacha.hoyolab import CheckinResult, GachaError, GameStatus

logger = logging.getLogger(__name__)

KEEP_DAYS = 14
TOLD_DAILY = frozenset({"cookie", "notes_off", "captcha", "account"})
ACCOUNT_WIDE = frozenset({"cookie"})  # one cookie serves every game: tell it once, not per game


@dataclass(frozen=True)
class Notice:
    game: str | None
    kind: str  # stamina, daily, weekly, expeditions, checkin, problem
    text: str  # Indonesian, in her voice, no emoji or markdown
    emotion: str


@dataclass
class ReminderState:
    """What was already told: key -> the day it belongs to (ISO date)."""

    fired: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> ReminderState:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls({str(key): str(day) for key, day in raw["fired"].items()})
        except FileNotFoundError:
            return cls()
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Ignoring the unreadable reminder state %s: %s", path, exc)
            return cls()

    def save(self, path: Path, *, today: date) -> None:
        """Write the state, leaving out what is older than two weeks."""
        oldest = (today - timedelta(days=KEEP_DAYS)).isoformat()
        self.fired = {key: day for key, day in self.fired.items() if day >= oldest}
        write_json_atomic(path, {"version": 1, "fired": self.fired})

    def once(self, key: str, day: date) -> bool:
        """True the first time `key` is seen (and remembers it), False afterwards."""
        if key in self.fired:
            return False
        self.fired[key] = day.isoformat()
        return True

    def rearm(self, key: str) -> None:
        self.fired.pop(key, None)


def due_notices(
    statuses: Mapping[str, GameStatus],
    now: datetime,
    state: ReminderState,
    rules: GachaRemindersConfig,
    offset: int,
) -> list[Notice]:
    """What to tell the owner about these statuses right now; `state` remembers it."""
    day = game_day(now, offset)
    evening = reminder_due(now, rules.daily_time, offset)
    reset = next_daily_reset(now, offset).strftime("%H:%M")
    notices: list[Notice] = []
    for game_id, status in statuses.items():
        game = GAMES[game_id]
        candidates = [
            _stamina(game, status, state, rules, day),
            _daily(game, status, state, day, reset) if evening else None,
            _weekly(game, status, state, day, reset) if evening and rules.weekly else None,
            _expeditions(game, status, state, day) if rules.expeditions else None,
        ]
        notices.extend(notice for notice in candidates if notice is not None)
    return notices


def problem_notice(
    game_id: str, error: GachaError, now: datetime, state: ReminderState, offset: int
) -> Notice | None:
    """A problem the owner must fix, told once a game day; passing ones are only logged."""
    if error.kind not in TOLD_DAILY:
        logger.info("Passing %s problem with %s, not told: %s", error.kind, game_id, error.message)
        return None
    day = game_day(now, offset)
    account_wide = error.kind in ACCOUNT_WIDE
    key = f"problem:{error.kind}:{day}" if account_wide else f"problem:{error.kind}:{game_id}:{day}"
    if not state.once(key, day):
        return None
    return Notice(None if account_wide else game_id, "problem", f"Master, {error.message}", "sad")


def checkin_notice(results: Iterable[CheckinResult]) -> Notice | None:
    """One sentence about the rewards she claimed; None when nothing new was claimed."""
    claimed = [
        f"{GAMES[result.game].short} dapat {result.reward}" for result in results if result.claimed
    ]
    if not claimed:
        return None
    return Notice(
        None, "checkin", f"Check-in harian HoYoLAB sudah aku ambil: {_join(claimed)}.", "happy"
    )


def _stamina(
    game: Game, status: GameStatus, state: ReminderState, rules: GachaRemindersConfig, day: date
) -> Notice | None:
    key = f"stamina:{game.id}"
    full = status.stamina >= status.stamina_max or status.stamina_full_in_s <= 0
    if not full and status.stamina_full_in_s > rules.stamina_minutes * 60:
        state.rearm(key)  # Master spent it: the next fill-up is a new occasion
        return None
    if not state.once(key, day):
        return None
    amount = f"{status.stamina} dari {status.stamina_max}"
    if full:
        text = f"Master, {game.stamina} di {game.short} sudah penuh, {amount}."
    else:
        wait = _duration(math.ceil(status.stamina_full_in_s / 60))
        text = f"Master, {game.stamina} di {game.short} sudah {amount}, penuh sekitar {wait} lagi."
    return Notice(game.id, "stamina", f"{text} Jangan sampai terbuang ya.", "surprised")


def _daily(
    game: Game, status: GameStatus, state: ReminderState, day: date, reset: str
) -> Notice | None:
    if status.daily_done or not state.once(f"daily:{game.id}:{day}", day):
        return None
    if status.daily_current >= status.daily_max:
        progress = "sudah selesai, tinggal ambil hadiahnya"
    else:
        progress = f"belum selesai, baru {status.daily_current} dari {status.daily_max}"
    text = f"Master, {game.daily} {game.short} {progress}. Reset jam {reset} nanti."
    return Notice(game.id, "daily", text, "surprised")


def _weekly(
    game: Game, status: GameStatus, state: ReminderState, day: date, reset: str
) -> Notice | None:
    left = [
        f"{task.name} {task.current} dari {task.max}" for task in status.weekly if not task.done
    ]
    if not left or not is_last_day_of_week(day):
        return None
    year, week, _ = day.isocalendar()
    if not state.once(f"weekly:{game.id}:{year}-W{week:02d}", day):
        return None
    text = f"Master, reset mingguan {game.short} jam {reset} nanti. Masih tersisa {_join(left)}."
    return Notice(game.id, "weekly", text, "surprised")


def _expeditions(game: Game, status: GameStatus, state: ReminderState, day: date) -> Notice | None:
    key = f"expeditions:{game.id}"
    if not status.expeditions_total or status.expeditions_finished < status.expeditions_total:
        state.rearm(key)
        return None
    if not state.once(key, day):
        return None
    text = f"Master, semua ekspedisi {game.short} sudah selesai. Ambil hadiahnya yuk."
    return Notice(game.id, "expeditions", text, "surprised")


def _duration(minutes: int) -> str:
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f"{minutes} menit"
    return f"{hours} jam {minutes} menit" if minutes else f"{hours} jam"


def _join(items: list[str]) -> str:
    if len(items) <= 2:
        return " dan ".join(items)
    return f"{', '.join(items[:-1])}, dan {items[-1]}"
