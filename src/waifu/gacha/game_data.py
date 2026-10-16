"""What the game data of Master's HoYoverse games share, whichever game it is from.

Nothing here imports genshin.py. Every datetime is aware.
"""

from __future__ import annotations

from datetime import datetime


def is_over(ends_at: datetime | None, now: datetime) -> bool:
    """Whether something that ends at `ends_at` (None: no end given) is over at `now`."""
    return ends_at is not None and ends_at <= now


def is_running(starts_at: datetime | None, ends_at: datetime | None, now: datetime) -> bool:
    """Whether something has started (None: no start given) and is not over at `now`."""
    return (starts_at is None or starts_at <= now) and not is_over(ends_at, now)
