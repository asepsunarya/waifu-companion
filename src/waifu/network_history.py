"""The week of internet outages the watcher keeps, and how much of a span they took.

The file (`network.path`) follows the rules of `play_time.py`: every call re-reads it, one lock
serialises the calls of every store in this process, a write replaces it atomically, reading never
creates it, and a file that is not a network history is moved aside, never overwritten. Only
`waifu run`'s watcher writes it (its run lock keeps it the only writer); the recap, the weekly
report, the tool, `waifu internet`, and `check` read it. Times are aware WIB, to the second;
outages that ended more than `keep_days` ago are left out and dropped at the next write.

It holds the outages that ended, the one still open (its last failed poll moved at every failed
poll, so a crash leaves where it was last seen down), and how many back lines she said on the
latest day, so a restart never says one more than `max_back_per_day`.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from waifu.gacha.files import write_json_atomic
from waifu.play_time import wib_now

logger = logging.getLogger(__name__)

OutageKind = Literal["router", "offline"]
FILE_VERSION = 1
_KINDS = frozenset({"router", "offline"})
_FILE_KEYS = {"version", "outages", "open", "back_told"}
_OUTAGE_KEYS = {"start", "end", "kind"}
_OPEN_KEYS = {"start", "last_failed", "kind"}
_BACK_KEYS = {"date", "count"}
# date.fromisoformat also reads 20261025 and 2026-W43-7; the file holds only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# One lock for every store: two stores on the same file in this process must not lose
# each other's writes.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Outage:
    """The internet out of reach from `start`, its first failed poll, until `end`, the poll
    that saw it back."""

    start: datetime
    end: datetime
    kind: OutageKind


@dataclass(frozen=True)
class OpenOutage:
    """An outage not seen to end yet, down at least until `last_failed`."""

    start: datetime
    last_failed: datetime
    kind: OutageKind


@dataclass(frozen=True)
class BackTold:
    """How many back lines she said on `day`, the latest day she said one."""

    day: date
    count: int


@dataclass(frozen=True)
class NetworkRecord:
    outages: tuple[Outage, ...] = ()  # ended, by start
    open: OpenOutage | None = None
    back_told: BackTold | None = None

    def back_count(self, day: date) -> int:
        """The back lines said on `day`."""
        told = self.back_told
        return told.count if told is not None and told.day == day else 0


@dataclass(frozen=True)
class OutageTotal:
    count: int  # the outages with time in the span
    seconds: int  # their time inside it


def outage_totals(record: NetworkRecord, since: datetime, until: datetime) -> OutageTotal:
    """The outages with time inside `[since, until]`, the open one up to its last failed poll,
    and their seconds inside it."""
    spans = [(outage.start, outage.end) for outage in record.outages]
    if record.open is not None:
        spans.append((record.open.start, record.open.last_failed))
    inside = [min(end, until) - max(start, since) for start, end in spans]
    taken = [span for span in inside if span > timedelta(0)]
    return OutageTotal(len(taken), int(sum(taken, timedelta(0)).total_seconds()))


class NetworkStore:
    def __init__(
        self, path: Path | str, *, keep_days: int, now: Callable[[], datetime] = wib_now
    ) -> None:
        """`now` (aware) decides which outages are kept. A naive clock is a ValueError at every
        call."""
        self.path = Path(path)
        self.keep_days = keep_days
        self._now = now

    def record(self) -> NetworkRecord:
        """The outages kept, the open one, and the back lines said; a missing file is an empty
        history and stays missing."""
        now = self._clock()
        with _LOCK:
            return self._read(now)

    def begin(self, start: datetime, last_failed: datetime, kind: OutageKind) -> None:
        """Keep an outage open from `start`, down at `last_failed`; one still open ends at its
        own last failed poll first."""
        now = self._clock()
        with _LOCK:
            record = self._read(now)
            outages = (
                record.outages if record.open is None else (*record.outages, _ended(record.open))
            )
            opened = OpenOutage(_stamp(start), _stamp(last_failed), kind)
            self._write(dataclasses.replace(record, outages=outages, open=opened), now)

    def still_down(self, at: datetime) -> None:
        """Move the open outage's last failed poll to `at`; nothing without one."""
        now = self._clock()
        with _LOCK:
            record = self._read(now)
            if record.open is None:
                return
            down = dataclasses.replace(record.open, last_failed=_stamp(at))
            self._write(dataclasses.replace(record, open=down), now)

    def end(self, at: datetime) -> Outage | None:
        """End the open outage at `at` and keep it; None, and nothing written, without one."""
        now = self._clock()
        with _LOCK:
            record = self._read(now)
            if record.open is None:
                return None
            ended = Outage(record.open.start, _stamp(at), record.open.kind)
            changed = dataclasses.replace(record, outages=(*record.outages, ended), open=None)
            self._write(changed, now)
            return ended

    def told_back(self, day: date) -> int:
        """Count one more back line said on `day`; the count of that day now."""
        now = self._clock()
        with _LOCK:
            record = self._read(now)
            told = BackTold(day, record.back_count(day) + 1)
            self._write(dataclasses.replace(record, back_told=told), now)
            return told.count

    def _clock(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("the network store's clock must give aware times")
        return now

    def _read(self, now: datetime) -> NetworkRecord:
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            record = _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return NetworkRecord()
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return NetworkRecord()
        return self._kept(record, now)

    def _kept(self, record: NetworkRecord, now: datetime) -> NetworkRecord:
        oldest = now - timedelta(days=self.keep_days)
        kept = tuple(outage for outage in record.outages if outage.end >= oldest)
        return dataclasses.replace(record, outages=kept)

    def _set_aside(self, problem: ValueError, now: datetime) -> None:
        """Rename a file that is not a network history, so that no write ever replaces it."""
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
                "Network file %s is not a network history (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Network file %s is not a network history (%s); moved it to %s and started an "
            "empty one",
            self.path,
            problem,
            target,
        )

    def _write(self, record: NetworkRecord, now: datetime) -> None:
        record = self._kept(record, now)
        opened, told = record.open, record.back_told
        write_json_atomic(
            self.path,
            {
                "version": FILE_VERSION,
                "outages": [
                    {"start": _text(o.start), "end": _text(o.end), "kind": o.kind}
                    for o in sorted(record.outages, key=lambda outage: outage.start)
                ],
                "open": None
                if opened is None
                else {
                    "start": _text(opened.start),
                    "last_failed": _text(opened.last_failed),
                    "kind": opened.kind,
                },
                "back_told": None
                if told is None
                else {"date": told.day.isoformat(), "count": told.count},
            },
        )


def _ended(opened: OpenOutage) -> Outage:
    """An open outage ended where it was last seen down."""
    return Outage(opened.start, opened.last_failed, opened.kind)


def _stamp(moment: datetime) -> datetime:
    return moment.replace(microsecond=0)


def _text(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse(raw: object) -> NetworkRecord:
    """The history in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version = raw["version"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    entries = raw["outages"]
    if not isinstance(entries, list):
        raise ValueError("outages must be a list")
    outages = []
    for index, entry in enumerate(entries):
        start, end, kind = _parse_span(entry, f"outages[{index}]", _OUTAGE_KEYS, "end")
        outages.append(Outage(start, end, kind))
    opened = raw["open"]
    if opened is not None:
        start, last_failed, kind = _parse_span(opened, "open", _OPEN_KEYS, "last_failed")
        opened = OpenOutage(start, last_failed, kind)
    told = raw["back_told"]
    return NetworkRecord(
        tuple(sorted(outages, key=lambda outage: outage.start)),
        opened,
        None if told is None else _parse_back_told(told),
    )


def _parse_span(
    entry: object, where: str, keys: set[str], end_key: str
) -> tuple[datetime, datetime, OutageKind]:
    if not isinstance(entry, dict) or entry.keys() != keys:
        raise ValueError(f"{where} must be an object with exactly the keys {sorted(keys)}")
    kind = entry["kind"]
    if not isinstance(kind, str) or kind not in _KINDS:
        raise ValueError(f"{where}.kind must be one of {sorted(_KINDS)}")
    start = _parse_time(entry["start"], f"{where}.start")
    end = _parse_time(entry[end_key], f"{where}.{end_key}")
    if end < start:
        raise ValueError(f"{where}.{end_key} is before its start")
    return start, end, cast(OutageKind, kind)


def _parse_back_told(told: object) -> BackTold:
    if not isinstance(told, dict) or told.keys() != _BACK_KEYS:
        raise ValueError(f"back_told must be an object with exactly the keys {sorted(_BACK_KEYS)}")
    count = told["count"]
    if type(count) is not int or count < 1:
        raise ValueError("back_told.count must be a whole number of at least 1")
    day = told["date"]
    if isinstance(day, str) and _ISO_DATE.fullmatch(day):
        try:
            return BackTold(date.fromisoformat(day), count)
        except ValueError:
            pass  # a day that does not exist, such as 2026-02-30
    raise ValueError("back_told.date must be a date written YYYY-MM-DD")


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
