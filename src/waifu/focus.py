"""The focus log: when Master focused with the focus timer (`focus_timer.py`), and the totals.

A block is a time Master focused, from its start to its end: a whole focus block of a session,
or the part of one done before the session was stopped. Totals are per WIB period
(`play_time.period_window`): each block is cut at the period's edges, so a block across midnight
counts on each day.

The file (`focus.path`) follows the rules of `todo.py`: every call re-reads it, one lock
serialises the calls of every store in this process, a write replaces it atomically, reading
never creates it, and a file that is not a focus log is moved aside, never overwritten. Only
`waifu run` writes it (its run lock keeps it the only writer). Times are aware WIB, to the
second; blocks that ended more than `keep_days` ago are left out and dropped at the next write.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from waifu.gacha.files import write_json_atomic
from waifu.play_time import wib_now

logger = logging.getLogger(__name__)

FILE_VERSION = 1
_FILE_KEYS = {"version", "blocks"}
_BLOCK_KEYS = {"start", "end"}

# One lock for every store: two stores on the same file in this process must not lose
# each other's blocks.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class FocusBlock:
    """A time Master focused, `end` after `start`."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class FocusTotal:
    seconds: int
    blocks: int  # the blocks with time in the period


def focus_totals(blocks: Iterable[FocusBlock], since: datetime, until: datetime) -> FocusTotal:
    """The time inside `[since, until]` of every block, each cut at the period's edges, and the
    number of blocks with time in it."""
    seconds, counted = 0, 0
    for block in blocks:
        inside = min(block.end, until) - max(block.start, since)
        if inside > timedelta(0):
            seconds += int(inside.total_seconds())
            counted += 1
    return FocusTotal(seconds, counted)


class FocusStore:
    def __init__(
        self, path: Path | str, *, keep_days: int, now: Callable[[], datetime] = wib_now
    ) -> None:
        """`now` (aware) decides which blocks are kept. A naive clock is a ValueError at every
        call."""
        self.path = Path(path)
        self.keep_days = keep_days
        self._now = now

    def blocks(self) -> tuple[FocusBlock, ...]:
        """The blocks kept, by start; a missing file is an empty log and stays missing."""
        now = self._clock()
        with _LOCK:
            return self._read(now)

    def add(self, block: FocusBlock) -> None:
        """Keep `block` with the others, in one write."""
        now = self._clock()
        with _LOCK:
            self._write((*self._read(now), block), now)

    def totals(self, since: datetime, until: datetime) -> FocusTotal:
        """`focus_totals` of the kept blocks."""
        return focus_totals(self.blocks(), since, until)

    def _clock(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("the focus log's clock must give aware times")
        return now

    def _read(self, now: datetime) -> tuple[FocusBlock, ...]:
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            blocks = _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return ()
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return ()
        return self._kept(blocks, now)

    def _kept(self, blocks: Iterable[FocusBlock], now: datetime) -> tuple[FocusBlock, ...]:
        """The blocks that ended within `keep_days` of `now`, by start."""
        oldest = now - timedelta(days=self.keep_days)
        return tuple(sorted((b for b in blocks if b.end >= oldest), key=lambda b: b.start))

    def _set_aside(self, problem: ValueError, now: datetime) -> None:
        """Rename a file that is not a focus log, so that no write ever replaces it."""
        name = f"{self.path.name}.corrupt-{now:%Y%m%d-%H%M%S}"
        target = self.path.with_name(name)
        counter = 2
        while target.exists():
            target = self.path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(self.path, target)
        except OSError as exc:
            logger.error(
                "Focus file %s is not a focus log (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Focus file %s is not a focus log (%s); moved it to %s and started an empty one",
            self.path,
            problem,
            target,
        )

    def _write(self, blocks: Iterable[FocusBlock], now: datetime) -> None:
        write_json_atomic(
            self.path,
            {
                "version": FILE_VERSION,
                "blocks": [_dump(block) for block in self._kept(blocks, now)],
            },
        )


def _dump(block: FocusBlock) -> dict[str, str]:
    return {
        "start": block.start.isoformat(timespec="seconds"),
        "end": block.end.isoformat(timespec="seconds"),
    }


def _parse(raw: object) -> list[FocusBlock]:
    """The blocks in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version = raw["version"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    entries = raw["blocks"]
    if not isinstance(entries, list):
        raise ValueError("blocks must be a list")
    blocks = []
    for index, entry in enumerate(entries):
        at = f"blocks[{index}]"
        if not isinstance(entry, dict) or entry.keys() != _BLOCK_KEYS:
            raise ValueError(f"{at} must be an object with exactly the keys {sorted(_BLOCK_KEYS)}")
        start = _parse_time(entry["start"], f"{at}.start")
        end = _parse_time(entry["end"], f"{at}.end")
        if end <= start:
            raise ValueError(f"{at}.end must be after its start")
        blocks.append(FocusBlock(start, end))
    return blocks


def _parse_time(value: object, where: str) -> datetime:
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if moment.tzinfo is not None:
                return moment
    raise ValueError(f"{where} must be an ISO 8601 time with its offset")
