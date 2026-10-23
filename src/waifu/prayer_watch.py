"""The watcher inside `waifu run` that tells Master as each prayer time comes in.

Every `POLL_S`, timed from the start of the previous poll and first right after it starts, it
computes today's prayer times at the place (`prayer_times.py`, WIB) and says each prayer of
`prayer.remind` once: from `minutes_before` before its time ("Sayang, sepuluh menit lagi masuk
waktu Maghrib.", the minutes really left) or at it ("Sayang, sudah masuk waktu Dzuhur."), until
`LATE` after it; a PC woken or a `waifu run` started later than that says nothing for it. Master
turned these on himself, so the line goes to his own reminder queue (`Assistant.remind`): a
focus block or a game holding her announcements never holds it. It is pushed to the phone when
the watcher has a notifier.

The place is looked up at the first poll, never at startup (`PrayerPlace`); a lookup that fails
is never said, logged at WARNING once per run of failures, and asked again only `RETRY_S` later.

What was told today is kept in memory and in the state file, which follows the rules of
`prompt_timer.py`: read only when a prayer is due and not told in memory, replaced atomically
before she says it, never created by reading, and moved aside, never overwritten, when it is not
such a state. A file that cannot be read or written is logged and she says the prayer all the
same, remembered in memory: only a restart within its window could say it again. Nothing is
raised.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta

from waifu.avatar.emotion import NEUTRAL
from waifu.config import PrayerConfig, at_sentence_start
from waifu.focus_timer import spoken_duration
from waifu.gacha.files import write_json_atomic
from waifu.notify import Notifier
from waifu.play_time import wib_now
from waifu.prayer import PrayerError, PrayerPlace
from waifu.prayer_times import PRAYER_LABELS, PRAYERS, prayer_times
from waifu.rain_watch import Wait

logger = logging.getLogger(__name__)

POLL_S = 30.0
RETRY_S = 900.0  # after a failed lookup of the place, like the rain watcher's poll
LATE = timedelta(minutes=30)  # shorter than the gap between any two prayers in Indonesia
STOP_TIMEOUT_S = 5.0
PRAYER_EMOTION = NEUTRAL  # a calm, respectful word
PRAYER_TAG = "mosque"  # ntfy shows it as a mosque emoji
AT_LINE = "{owner}, sudah masuk waktu {prayer}."
BEFORE_LINE = "{owner}, {left} lagi masuk waktu {prayer}."
FILE_VERSION = 1
_STATE_KEYS = {"version", "date", "told"}
# date.fromisoformat also reads 20261024 and 2026-W43-6; the file holds only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class PrayerWatcher:
    def __init__(
        self,
        place: PrayerPlace,
        config: PrayerConfig,
        *,
        remind: Callable[[str, str], None],
        notifier: Notifier | None,
        owner_call: str,
        title: str = "Cyrene",
        now: Callable[[], datetime] = wib_now,
        clock: Callable[[], float] = time.monotonic,
        wait: Wait = threading.Event.wait,
    ) -> None:
        """`remind(text, emotion)` queues a line Master asked for; `now` is aware WIB time, and
        `clock` monotonic seconds, for the polls and the retries."""
        self._place = place
        self._config = config
        self._remind = remind
        self._notifier = notifier
        self._owner = at_sentence_start(owner_call)  # every line opens with it
        self._title = title
        self._now = now
        self._clock = clock
        self._wait = wait
        self._before = timedelta(minutes=config.minutes_before)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._told_day: date | None = None
        self._told: set[str] = set()  # the prayers told on _told_day
        self._retry_at: float | None = None  # after a failed lookup: no lookup before then
        self._failing = False  # the last lookup failed

    def poll_once(self) -> list[str]:
        """Say the prayers that are due and not told today; returns what she said."""
        if self._retry_at is not None and self._clock() < self._retry_at:
            return []
        try:
            location = self._place.locate()
        except Exception as exc:
            self._retry_at = self._clock() + RETRY_S
            self._log_failure(exc)
            return []
        self._retry_at = None
        if self._failing:
            self._failing = False
            logger.info("The place for the prayer times was found again")
        now = self._now()
        today = now.date()
        times = prayer_times(
            today,
            location.latitude,
            location.longitude,
            ihtiyath_minutes=self._config.ihtiyath_minutes,
        )
        due = {
            name: at
            for name in PRAYERS
            if name in self._config.remind
            and (at := getattr(times, name)) is not None
            and at - self._before <= now < at + LATE
            and not (self._told_day == today and name in self._told)
        }
        if not due:
            return []
        said = []
        for name in self._mark_told(today, due, now):
            text = self._line(name, due[name], now)
            logger.info("Reminding Master of %s at %s", name, f"{due[name]:%H:%M}")
            self._deliver(text)
            said.append(text)
        return said

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="prayer-watcher", daemon=True)
        self._thread.start()
        when = (
            f"{self._config.minutes_before} minutes before their time"
            if self._config.minutes_before
            else "at their time"
        )
        logger.info(
            "Reminding of the prayers %s %s, remembered in %s",
            ", ".join(self._config.remind),
            when,
            self._config.state_path,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            try:
                self.poll_once()
            except Exception:
                logger.exception("A prayer reminder check failed; trying again at the next one")
            self._wait(self._stop, max(0.0, started + POLL_S - self._clock()))

    def _line(self, name: str, at: datetime, now: datetime) -> str:
        prayer = PRAYER_LABELS[name]
        if now >= at:
            return AT_LINE.format(owner=self._owner, prayer=prayer)
        left = math.ceil((at - now).total_seconds() / 60)
        return BEFORE_LINE.format(owner=self._owner, left=spoken_duration(left), prayer=prayer)

    def _mark_told(self, today: date, due: Iterable[str], now: datetime) -> list[str]:
        """The due prayers not told today by the state file either, marked told in memory and
        in the file before she says them."""
        if self._told_day != today:
            self._told_day, self._told = today, set()
        try:
            self._told |= self._read_told(today, now)
            readable = True
        except OSError as exc:
            readable = False
            logger.warning(
                "Could not read the prayer reminder state %s (%s); leaving it alone and "
                "remembering today's reminders in memory",
                self._config.state_path,
                exc,
            )
        fresh = [name for name in due if name not in self._told]
        self._told |= set(fresh)
        if fresh and readable:
            state = {
                "version": FILE_VERSION,
                "date": today.isoformat(),
                "told": [name for name in PRAYERS if name in self._told],
            }
            try:
                write_json_atomic(self._config.state_path, state)
            except OSError as exc:
                logger.warning(
                    "Could not save the prayer reminder state %s (%s); remembering today's "
                    "reminders in memory",
                    self._config.state_path,
                    exc,
                )
        return fresh

    def _read_told(self, today: date, now: datetime) -> set[str]:
        """The prayers the state file says were told today; none when there is no file, it is
        of another day, or it was moved aside. OSError when it cannot be read or moved."""
        path = self._config.state_path
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            day, told = _parse_state(json.loads(path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return set()
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return set()
        return told if day == today else set()

    def _set_aside(self, problem: ValueError, now: datetime) -> None:
        """Rename a file that is not such a state, so that no write ever replaces it."""
        path = self._config.state_path
        name = f"{path.name}.corrupt-{now:%Y%m%d-%H%M%S}"
        target = path.with_name(name)
        counter = 2
        while target.exists():
            target = path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(path, target)
        except OSError as exc:
            logger.error(
                "The prayer reminder state %s is not one (%s) and could not be moved aside: %s",
                path,
                problem,
                exc,
            )
            raise
        logger.error(
            "The prayer reminder state %s is not one (%s); moved it to %s and started anew",
            path,
            problem,
            target,
        )

    def _log_failure(self, exc: Exception) -> None:
        """The first failure of a run at WARNING (an unexpected one with its traceback), the
        rest at DEBUG: a place that cannot be found is one line, not one per retry."""
        traceback = not isinstance(exc, PrayerError)
        if self._failing:
            logger.debug("The place for the prayer times still cannot be found: %s", exc)
            return
        self._failing = True
        logger.warning(
            "Could not find the place for the prayer times (%s); asking again in %d minutes",
            exc,
            RETRY_S // 60,
            exc_info=traceback,
        )

    def _deliver(self, text: str) -> None:
        """Say the line and push it; a failure of either is logged, never raised."""
        try:
            self._remind(text, PRAYER_EMOTION)
        except Exception:
            logger.exception("Could not say a prayer reminder")
        if self._notifier is not None:
            try:
                self._notifier.send(self._title, text, tags=[PRAYER_TAG])
            except Exception:
                logger.exception("Could not push a prayer reminder")


def _parse_state(raw: object) -> tuple[date, set[str]]:
    """The day in `raw` and the prayers told on it; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _STATE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_STATE_KEYS)}")
    version, day, told = raw["version"], raw["date"], raw["told"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if not isinstance(day, str) or not _ISO_DATE.fullmatch(day):
        raise ValueError("date must be a date written YYYY-MM-DD")
    if (
        not isinstance(told, list)
        or not all(isinstance(name, str) and name in PRAYERS for name in told)
        or len(set(told)) != len(told)
    ):
        raise ValueError(f"told must list prayers from {', '.join(PRAYERS)}, each once")
    return date.fromisoformat(day), set(told)  # ValueError for a day that does not exist
