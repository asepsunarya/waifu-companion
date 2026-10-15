"""Master's to-do list: tasks with an optional due date, beside the timed schedules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Task:
    """A task on Master's list; `done` is when it was finished, None while it is open."""

    id: str  # "t1", "t2", ...: never reused
    text: str
    due: date | None
    created: datetime
    done: datetime | None
