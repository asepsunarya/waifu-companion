"""When a conversation session should end, shared by every brain backend.

History is append-only within a session, so instead of trimming old turns a
backend starts a new, empty session once the owner has been quiet for a while
or the session has grown to its turn cap.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class SessionClock:
    def __init__(
        self,
        idle_minutes: float,
        max_turns: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle_seconds = idle_minutes * 60
        self._max_turns = max_turns
        self._clock = clock
        self._turns = 0
        self._last_turn_at: float | None = None

    def should_reset(self) -> bool:
        """True when the session has turns and is either idle too long or at its turn cap."""
        if self._last_turn_at is None:
            return False
        idle_seconds = self._clock() - self._last_turn_at
        return idle_seconds > self._idle_seconds or self._turns >= self._max_turns

    def record_turn(self) -> None:
        self._turns += 1
        self._last_turn_at = self._clock()

    def reset(self) -> None:
        self._turns = 0
        self._last_turn_at = None
