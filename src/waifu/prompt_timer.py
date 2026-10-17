"""Her own turn, asked for at most once a day: the timer behind the morning briefing.

A `PromptTimer` hands its prompt to `request`, which has the brain answer it between Master's
utterances, at the first check of a day on which `due(now)` holds, with until when it may still
be said (`expires(now)`): held longer, it would speak of a day or a window that has ended. The
day goes into the state file before the request, so a crash, a restart, or a second PC start
the same day never asks twice; a request that fails is not retried until the next day. The file
follows the rules of `memory.py`: it is re-read at every check, replaced atomically, never
created by reading, and a file that is not such a state is moved aside, never overwritten.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import TracebackType

from waifu.gacha.files import write_json_atomic

logger = logging.getLogger(__name__)

FILE_VERSION = 1
STOP_TIMEOUT_S = 5.0
_STATE_KEYS = {"version", "last_date"}
# date.fromisoformat also reads 20261013 and 2026-W42-2; the file holds only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def end_of_day(moment: datetime) -> datetime:
    """The midnight that ends `moment`'s day, in its time zone (a naive moment gives a naive
    midnight)."""
    return datetime.combine(moment.date() + timedelta(days=1), time(0), moment.tzinfo)


class PromptTimer:
    """Asks for `prompt` once on a day when `due(now)` holds, from a daemon thread.

    `request` gets the prompt and `expires(now)`, until when it may be said. `what` names the
    prompt in the logs ("morning briefing"), `when` says when it is due ("between 07:00 and
    12:00"), and `thread_name` names the thread.
    """

    def __init__(
        self,
        prompt: str,
        *,
        due: Callable[[datetime], bool],
        expires: Callable[[datetime], datetime],
        state_path: Path,
        request: Callable[[str, datetime], None],
        what: str,
        when: str,
        thread_name: str,
        now: Callable[[], datetime] = datetime.now,
        tick_seconds: float = 30,
    ) -> None:
        self._prompt = prompt
        self._due = due
        self._expires = expires
        self._path = state_path
        self._request = request
        self._what = what
        self._when = when
        self._thread_name = thread_name
        self._now = now
        self._tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick_once(self) -> None:
        """Ask for the prompt when it is due and was not asked for today.

        Today is saved first: when the save fails, nothing is asked (it raises, and the next
        tick tries again); a request that fails is logged and not retried today.
        """
        now = self._now()
        if not self._due(now):
            return
        today = now.date()
        if self._last_date(now) == today:
            return
        write_json_atomic(self._path, {"version": FILE_VERSION, "last_date": today.isoformat()})
        expires_at = self._expires(now)
        logger.info(
            "Asking for the %s of %s (said until %s)",
            self._what,
            today.isoformat(),
            f"{expires_at:%Y-%m-%d %H:%M}",
        )
        try:
            self._request(self._prompt, expires_at)
        except Exception:
            logger.exception("The %s request failed; not asking again today", self._what)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self._thread_name, daemon=True)
        self._thread.start()
        logger.info("%s %s, remembered in %s", self._what.capitalize(), self._when, self._path)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def __enter__(self) -> PromptTimer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception:
                logger.exception("A %s check failed; trying again at the next one", self._what)
            self._stop.wait(self._tick_seconds)

    def _last_date(self, now: datetime) -> date | None:
        """The day it was last asked for; None when never, or when the file was moved aside."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse_state(json.loads(self._path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return None
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return None

    def _set_aside(self, problem: ValueError, now: datetime) -> None:
        """Rename a file that is not such a state, so that no write ever replaces it."""
        name = f"{self._path.name}.corrupt-{now:%Y%m%d-%H%M%S}"
        target = self._path.with_name(name)
        counter = 2
        while target.exists():
            target = self._path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(self._path, target)
        except OSError as exc:
            logger.error(
                "The %s state %s is not one (%s) and could not be moved aside: %s",
                self._what,
                self._path,
                problem,
                exc,
            )
            raise
        logger.error(
            "The %s state %s is not one (%s); moved it to %s and started anew",
            self._what,
            self._path,
            problem,
            target,
        )


def _parse_state(raw: object) -> date:
    """The day in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _STATE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_STATE_KEYS)}")
    version, last_date = raw["version"], raw["last_date"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if not isinstance(last_date, str) or not _ISO_DATE.fullmatch(last_date):
        raise ValueError("last_date must be a date written YYYY-MM-DD")
    return date.fromisoformat(last_date)  # ValueError for a day that does not exist
