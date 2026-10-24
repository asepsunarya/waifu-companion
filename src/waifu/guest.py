"""Guest mode: Master's private things stay closed while guests are around, for a few hours.

One `GuestMode` per process (`waifu run`, `waifu chat`), shared by the brain, its tools, the
briefing tool, and the Assistant, as `TurnImages` is. Master turns it on and off through the
`guest_mode` tool (by voice, in chat, or on the phone); it ends by itself `max_hours` after it
was turned on, or at 05:00 on the PC's local clock, whichever comes first, so a forgotten guest
mode never lasts into the next day. The end is noticed lazily, by the next caller that asks.
The state lives in memory only: a restart ends it.

While it is on, the tools that read or write Master's own records or accounts (`PRIVATE_TOOLS`)
are refused in the shared dispatcher, and every switch (on, off, the automatic end) moves
`generation` on, so the brains start a new session at the next turn. The names are written out
here, not imported, so this module stays a leaf that any brain module may import; a test checks
them against each module's constant.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

logger = logging.getLogger(__name__)

GUEST_REFUSAL = "Mode tamu sedang aktif: data pribadi Master tidak dibuka dulu."
# Guest mode never lasts past this time of the PC's local clock.
END_OF_NIGHT = time(5, 0)
PRIVATE_TOOLS = frozenset(
    {
        # Money (tepira)
        "finance_summary",
        "wallet_balances",
        "savings_goals",
        "recent_transactions",
        "record_expense",
        "undo_expense",
        # Journal
        "journal_write",
        "journal_read",
        # Memory: a guest's fact must not be saved as Master's, nor Master's notes deleted
        "remember",
        "forget",
        # To-do list
        "add_task",
        "list_tasks",
        "complete_task",
        "reopen_task",
        "remove_task",
        "edit_task",
        # Play time. Not focus_timer: it is a control, and a block started before the guests
        # came must still stop on Master's word.
        "play_time",
        # Daily recap: Master's day from the private stores (tasks, games, pulls, reminders)
        "daily_recap",
        # Game accounts
        "game_status",
        "gacha_history",
        "gacha_income",
        "game_characters",
        "game_endgame",
        "game_events",  # Master's event progress and endgame stars, not just the calendar
        "weekly_report",
        "redeem_codes",
        "redeem_codes_list",
        # Schedule list: its reminder texts, and cancelling Master's entries
        "list_schedules",
        "cancel_schedule",
        # API cost
        "api_usage",
        # Screen: the PC may show private things
        "look_at_screen",
    }
)
_LOG_TIME = "%Y-%m-%d %H:%M"


@dataclass(frozen=True)
class GuestStatus:
    on: bool
    until: datetime | None  # when it ends by itself; None while off


@dataclass(frozen=True)
class Toggle:
    """What turning guest mode on or off did."""

    changed: bool  # False: it was on already (renewed) or off already
    until: datetime | None  # when it ends by itself; None once off


class GuestMode:
    """Whether guest mode is on, safe from any thread."""

    def __init__(self, *, max_hours: int = 6, now: Callable[[], datetime] = datetime.now) -> None:
        self._max = timedelta(hours=max_hours)
        self._now = now
        self._lock = threading.Lock()
        self._until: datetime | None = None
        self._generation = 0

    def turn_on(self) -> Toggle:
        """Turn it on until the earlier of `max_hours` from now and the next 05:00; while it is
        on already, renew it from now by the same rule, without a new generation."""
        with self._lock:
            now = self._now()
            self._end_if_due(now)
            changed = self._until is None
            self._until = min(now + self._max, next_end_of_night(now))
            if changed:
                self._generation += 1
                logger.info("Guest mode on until %s", f"{self._until:{_LOG_TIME}}")
            else:
                logger.info("Guest mode renewed until %s", f"{self._until:{_LOG_TIME}}")
            return Toggle(changed, self._until)

    def turn_off(self) -> Toggle:
        """Turn it off; nothing changes when it is off already."""
        with self._lock:
            self._end_if_due(self._now())
            if self._until is None:
                return Toggle(False, None)
            self._until = None
            self._generation += 1
            logger.info("Guest mode off")
            return Toggle(True, None)

    def is_on(self) -> bool:
        return self.status().on

    def status(self) -> GuestStatus:
        with self._lock:
            self._end_if_due(self._now())
            return GuestStatus(self._until is not None, self._until)

    def generation(self) -> int:
        """A number that moves on whenever guest mode turns on, off, or ends by itself."""
        with self._lock:
            self._end_if_due(self._now())
            return self._generation

    def refusal(self, name: str) -> str | None:
        """GUEST_REFUSAL for a private tool while guest mode is on, else None."""
        return GUEST_REFUSAL if name in PRIVATE_TOOLS and self.is_on() else None

    def _end_if_due(self, now: datetime) -> None:
        """End it once `now` has reached its end. Called under the lock."""
        if self._until is not None and now >= self._until:
            logger.info("Guest mode ended by itself at %s", f"{self._until:{_LOG_TIME}}")
            self._until = None
            self._generation += 1


def next_end_of_night(moment: datetime) -> datetime:
    """The first END_OF_NIGHT strictly after `moment`, in its own time zone."""
    today = datetime.combine(moment.date(), END_OF_NIGHT, moment.tzinfo)
    return today if moment < today else today + timedelta(days=1)
