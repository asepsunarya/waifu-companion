"""The watcher inside `waifu run` that reminds Master to rest after a long stretch at the PC.

Activity is keyboard and mouse input (`input_idle.read_input_idle`): the stretch goes on while no
pause reaches `idle_reset_minutes`, and it is counted on Windows' tick count, which runs on
through sleep. A poll more than `idle_reset_minutes` after the last one, by the tick count or by
the wall clock, means the PC slept or the watcher could not look: the stretch starts again. Once
the last input reaches `every_minutes` from the start of the stretch, the line is due, and the
next one `every_minutes` after that, on the same grid: "Sayang, sudah satu jam di depan layar.
..." then "dua jam". The last input decides, not the poll, so a line falling due while Master is
away waits for his next input, and a pause of a break's length ends the stretch instead.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timedelta

from waifu.input_idle import InputIdle

_MINUTE_MS = 60_000


@dataclass(frozen=True)
class Activity:
    """A stretch at the PC: since when and when its next line is due (tick count ms), and when
    the last poll was, by the tick count and by the PC's local clock."""

    since: int
    due: int
    last_tick: int
    last_wall: datetime


def follow_activity(
    activity: Activity | None,
    reading: InputIdle,
    now: datetime,
    *,
    every: timedelta,
    reset: timedelta,
) -> tuple[Activity | None, int | None]:
    """The stretch after a poll at `now` that read `reading` (None: a break, nothing counted),
    and the minutes of activity to tell when a line is due."""
    every_ms, reset_ms = _ms(every), _ms(reset)
    if reading.idle_ms >= reset_ms:
        return None, None
    tick = reading.tick_ms
    if activity is not None and (
        tick - activity.last_tick > reset_ms or now - activity.last_wall > reset
    ):
        activity = None
    if activity is None:
        return Activity(tick, tick + every_ms, tick, now), None
    followed = dataclasses.replace(activity, last_tick=tick, last_wall=now)
    if tick - reading.idle_ms < activity.due:
        return followed, None
    minutes = (activity.due - activity.since) // _MINUTE_MS
    return dataclasses.replace(followed, due=activity.due + every_ms), minutes


def _ms(length: timedelta) -> int:
    return round(length.total_seconds() * 1000)
