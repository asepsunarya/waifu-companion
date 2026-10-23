"""The watcher inside `waifu run` that reminds Master to rest after a long stretch at the PC.

Activity is keyboard and mouse input (`input_idle.read_input_idle`): the stretch goes on while no
pause reaches `idle_reset_minutes`, and it is counted on Windows' tick count, which runs on
through sleep. A poll more than `idle_reset_minutes` after the last one, by the tick count or by
the wall clock, means the PC slept or the watcher could not look: the stretch starts again. Once
the last input reaches `every_minutes` from the start of the stretch, the line is due, and the
next one `every_minutes` after that, on the same grid: "Sayang, sudah satu jam di depan layar.
..." then "dua jam". The last input decides, not the poll, so a line falling due while Master is
away waits for his next input, and a pause of a break's length ends the stretch instead.

Every `POLL_S`, timed from the start of the previous poll and first right after it starts, it
reads the activity, but never during a focus block (the focus timer has its own breaks) and never
outside the active hours: then it reads nothing, and the stretch starts again afterwards. A line
is said between utterances, once the follow-up window has closed, until `idle_reset_minutes`
after the poll that gives it (held longer, by a long talk, a game, or a focus block, it is
dropped), and pushed to the phone when the watcher has a notifier. A failed read is never said
and starts the stretch again: the first of a run of failures is logged at WARNING, and the next
poll tries again. When Master is at the PC is his own: the stretch and the line are logged at
DEBUG only. The stretch lives only in this process, so a restart counts from zero. Nothing is
raised.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from waifu.config import BreaksConfig, at_sentence_start
from waifu.focus_timer import spoken_duration
from waifu.input_idle import InputIdle
from waifu.notify import Notifier
from waifu.rain_watch import Announce, Wait
from waifu.voice_level import in_window

logger = logging.getLogger(__name__)

POLL_S = 30.0  # well inside the shortest break (2 minutes), so a break is seen within a poll
STOP_TIMEOUT_S = 5.0
BREAK_EMOTION = "relaxed"  # a gentle word, not a scolding
BREAK_TAG = "coffee"  # ntfy shows it as a hot drink emoji
BREAK_LINE = (
    "{owner}, sudah {stretch} di depan layar. Istirahatkan matamu sebentar dan minum air, ya."
)
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


class BreakWatcher:
    def __init__(
        self,
        read_activity: Callable[[], InputIdle],
        config: BreaksConfig,
        *,
        announce: Announce | None,
        notifier: Notifier | None,
        owner_call: str,
        title: str = "Cyrene",
        in_focus_block: Callable[[], bool] | None = None,
        now: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
        wait: Wait = threading.Event.wait,
    ) -> None:
        """`in_focus_block` tells whether a focus block is under way (none: never); `now` is
        the PC's local time, for the active hours, the sleeps, and the line's expiry, and
        `clock` monotonic seconds, for the polls."""
        self._read_activity = read_activity
        self._config = config
        self._announce = announce
        self._notifier = notifier
        self._owner = at_sentence_start(owner_call)  # every line opens with it
        self._title = title
        self._in_focus_block = in_focus_block
        self._now = now
        self._clock = clock
        self._wait = wait
        self._every = timedelta(minutes=config.every_minutes)
        self._reset = timedelta(minutes=config.idle_reset_minutes)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._activity: Activity | None = None  # the stretch followed, None between stretches
        self._failing = False  # the last read failed

    def poll_once(self) -> list[str]:
        """Outside a focus block and inside the active hours, read the activity and give the
        line that is due; returns its text."""
        now = self._now()
        if self._paused(now):
            self._follow(None)
            return []
        try:
            reading = self._read_activity()
            activity, minutes = follow_activity(
                self._activity, reading, now, every=self._every, reset=self._reset
            )
        except Exception as exc:
            self._follow(None)
            self._log_failure(exc)
            return []
        if self._failing:
            self._failing = False
            logger.info("The activity at the PC can be read again")
        self._follow(activity)
        if minutes is None:
            return []
        logger.debug("Master has been at the PC for %d minutes; suggesting a break", minutes)
        text = BREAK_LINE.format(owner=self._owner, stretch=spoken_duration(minutes))
        self._deliver(text, now + self._reset)
        return [text]

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="break-watcher", daemon=True)
        self._thread.start()
        logger.info(
            "Suggesting a break after every %d minutes at the PC; a %d-minute pause is a break",
            self._config.every_minutes,
            self._config.idle_reset_minutes,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            self.poll_once()
            self._wait(self._stop, max(0.0, started + POLL_S - self._clock()))

    def _paused(self, now: datetime) -> bool:
        """During a focus block, or outside the active hours."""
        if self._in_focus_block is not None and self._in_focus_block():
            return True
        start, until = self._config.active_from, self._config.active_until
        return start is not None and until is not None and not in_window(now.time(), start, until)

    def _follow(self, activity: Activity | None) -> None:
        """Keep the stretch after a poll, logging at DEBUG when one starts or ends."""
        if activity is not None and (
            self._activity is None or activity.since != self._activity.since
        ):
            logger.debug("A stretch at the PC started")
        elif activity is None and self._activity is not None:
            logger.debug("The stretch at the PC ended")
        self._activity = activity

    def _log_failure(self, exc: Exception) -> None:
        """The first failure of a run at WARNING (an unexpected one with its traceback), the
        rest at DEBUG: a session that cannot be read is one line, not one per poll."""
        traceback = not isinstance(exc, OSError)
        if self._failing:
            logger.debug("The activity at the PC still cannot be read: %s", exc, exc_info=traceback)
            return
        self._failing = True
        logger.warning(
            "Could not read the activity at the PC (%s); trying again at every poll",
            exc,
            exc_info=traceback,
        )

    def _deliver(self, text: str, expires_at: datetime) -> None:
        """Say the line, at the latest at `expires_at`, and push it; a failure of either is
        logged, never raised."""
        if self._announce is not None:
            try:
                self._announce(text, BREAK_EMOTION, expires_at=expires_at)
            except Exception:
                logger.exception("Could not say a break reminder")
        if self._notifier is not None:
            try:
                self._notifier.send(self._title, text, tags=[BREAK_TAG])
            except Exception:
                logger.exception("Could not push a break reminder")
