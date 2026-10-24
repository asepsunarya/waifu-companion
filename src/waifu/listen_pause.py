"""The pause of her hearing that Ctrl+Alt+Q starts: until it ends, Whisper hears nothing.

While Master plays with friends in voice chat, her name said there would wake her. The pause
holds for `hours` at most, then ends by itself; Ctrl+Alt+Q again, Ctrl+Alt+C, a double-click on
the avatar, or the quick button "Dengar lagi" end it sooner. The voice loop reads it at every
audio frame and skips the segmenter while it holds (`waifu.app`); it never pauses the mic.

It lives in a small file while it holds, written atomically and deleted when it ends, so the
quick buttons of `waifu tombol`, another process, show and end it. A read is kept for `REREAD_S`
before the file is read again; a pause whose time has passed is read afresh before its file is
deleted, so a pause another process has just started is never taken for it. A file that is not
a pause is moved aside, never overwritten. Reading never raises into the voice loop: a file that
cannot be read is no pause, warned once, and an ended pause's file that cannot be deleted is
tried again at each read, logged once.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from waifu.gacha.files import write_json_atomic

logger = logging.getLogger(__name__)

FILE_VERSION = 1
REREAD_S = 1.0  # how long a read is kept before the file is read again
_KEYS = {"version", "since", "until"}


@dataclass(frozen=True)
class _Held:
    since: datetime
    until: datetime


class ListenPause:
    def __init__(
        self,
        path: Path | str,
        *,
        hours: float = 2.0,
        now: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`hours` is how long a pause holds at most; `now` is the PC's local time (naive);
        `clock` times how long a read is kept."""
        self.path = Path(path)
        self.hours = hours
        self._now = now
        self._clock = clock
        self._lock = threading.Lock()
        self._kept: _Held | None = None  # the pause last read
        self._read_at: float | None = None  # when, on `clock`
        self._unreadable = False  # warned that the file cannot be read
        self._undeletable: datetime | None = None  # an ended pause left undeleted, logged once

    def until(self) -> datetime | None:
        """When the pause ends, or None when none holds, from a read at most REREAD_S old;
        never raises."""
        with self._lock:
            held = self._held()
            return None if held is None else held.until

    def paused(self) -> bool:
        """Whether the pause holds now; never raises."""
        return self.until() is not None

    def start(self) -> datetime:
        """Hold the pause from now for `hours`; when it ends. OSError when its file cannot be
        written."""
        with self._lock:
            since = self._now().replace(microsecond=0)  # as the file keeps it
            held = _Held(since, since + timedelta(hours=self.hours))
            write_json_atomic(
                self.path,
                {
                    "version": FILE_VERSION,
                    "since": held.since.isoformat(timespec="seconds"),
                    "until": held.until.isoformat(timespec="seconds"),
                },
            )
            self._kept, self._read_at = held, self._clock()
        return held.until

    def end(self) -> bool:
        """End the pause; False when none held. OSError when its file cannot be deleted."""
        with self._lock:
            if self._held(fresh=True) is None:
                return False
            self._remove()
        return True

    def _held(self, *, fresh: bool = False) -> _Held | None:
        """The pause, read again when `fresh`, kept longer than REREAD_S, or ended (the file is
        deleted only as read just now: another process may have started a new pause)."""
        kept_for = None if self._read_at is None else self._clock() - self._read_at
        kept = self._kept
        ended = kept is not None and self._now() >= kept.until
        if fresh or ended or kept_for is None or kept_for >= REREAD_S:
            self._kept, self._read_at = self._read_or_none(), self._clock()
        return self._unless_ended(self._kept)

    def _read_or_none(self) -> _Held | None:
        try:
            held = self._read_file()
        except OSError as exc:
            if not self._unreadable:
                logger.warning(
                    "Could not read the listen pause file %s (%s); taking it as no pause",
                    self.path,
                    exc,
                )
            self._unreadable = True
            return None
        self._unreadable = False
        return held

    def _unless_ended(self, held: _Held | None) -> _Held | None:
        """`held`, or None once it has ended, when its file is deleted; a file that cannot be
        deleted is tried again at each read, logged once."""
        if held is None or self._now() < held.until:
            return held
        logged = self._undeletable == held.since
        if not logged:
            logger.info("The listen pause ended by itself at %s", f"{held.until:%H:%M}")
        try:
            self._remove()
        except OSError as exc:
            if not logged:
                logger.warning(
                    "Could not delete the ended listen pause's file %s: %s", self.path, exc
                )
            self._undeletable, self._kept = held.since, None
        else:
            self._undeletable = None
        return None

    def _read_file(self) -> _Held | None:
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return None
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem)
            return None

    def _remove(self) -> None:
        self.path.unlink(missing_ok=True)
        self._kept, self._read_at = None, self._clock()

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not a pause, so that no write ever replaces it."""
        name = f"{self.path.name}.corrupt-{self._now():%Y%m%d-%H%M%S}"
        target = self.path.with_name(name)
        counter = 2
        while target.exists():
            target = self.path.with_name(f"{name}-{counter}")
            counter += 1
        os.rename(self.path, target)
        logger.error(
            "The listen pause file %s is not a pause (%s); moved it to %s",
            self.path,
            problem,
            target,
        )


def _parse(raw: object) -> _Held:
    """The pause in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_KEYS)}")
    version = raw["version"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    since = _parse_time(raw["since"], "since")
    until = _parse_time(raw["until"], "until")
    if until < since:
        raise ValueError("until must not be before since")
    return _Held(since, until)


def _parse_time(value: object, where: str) -> datetime:
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed
    raise ValueError(f"{where} must be a local ISO 8601 time without a time zone")
