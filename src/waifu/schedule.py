"""Cyrene's schedule: reminders and timed device commands that fire while `waifu run` is on.

The model adds an entry through its `schedule` tool; the scheduler thread fires what is
due (`due`) and then marks it fired, which removes a one-shot entry and moves a repeating
one to its next occurrence. The owner can list and cancel entries from the terminal
(`waifu schedule`) or edit the JSON file by hand.

Times are naive local datetimes at minute precision, read from the PC clock like the time
stamp of every turn. Moving to the next occurrence adds whole calendar days and keeps the
clock time, so a daylight saving change (Indonesia has none) never shifts an entry.

The file follows the rules of `memory.py`: every call re-reads it, so a running Cyrene
sees what `waifu schedule` changed in another process. One lock serialises the calls of
every store in this process; between processes the last writer wins. A write goes to a
temp file in the same folder, reaches the disk, and then replaces the file. A file that is
not a schedule is moved aside before anything is written: it is never overwritten.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

FILE_VERSION = 1
MAX_REMINDER_CHARS = 200
MAX_DAYS_AHEAD = 366
Repeat = Literal["none", "daily", "weekdays", "weekends"]
REPEATS: tuple[Repeat, ...] = ("none", "daily", "weekdays", "weekends")
# The days (Monday is 0) a repeating entry fires on.
_REPEAT_DAYS: dict[Repeat, frozenset[int]] = {
    "daily": frozenset(range(7)),
    "weekdays": frozenset(range(5)),
    "weekends": frozenset({5, 6}),
}
_LOCAL_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}")
_LOCAL_TIME_FORMAT = "%Y-%m-%d %H:%M"
_ONE_DAY = timedelta(days=1)
_ONE_MINUTE = timedelta(minutes=1)
_ENTRY_ID = re.compile(r"j[1-9][0-9]*")
_FILE_KEYS = {"version", "next_id", "entries"}
_ENTRY_KEYS = {"id", "at", "repeat", "action", "created"}
# The key sets each kind of action may have. A routine's delayed step also names its routine;
# that key is written only then, so a file without delayed steps reads as it always did.
_ACTION_KEYS = {
    "reminder": ({"type", "text"},),
    "device": (
        {"type", "device", "command", "value"},
        {"type", "device", "command", "value", "routine"},
    ),
    "routine": ({"type", "name"},),
}

# One lock for every store: two stores on the same file in this process must not lose
# each other's entries.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Reminder:
    text: str  # what she says to Master when it fires


@dataclass(frozen=True)
class DeviceAction:
    device: str
    command: str
    value: int | None = None
    # The routine whose delayed step this is ("mode tidur": the AC off later); None when Master
    # scheduled the command himself. A new run of that routine replaces its pending ones.
    routine: str | None = None


@dataclass(frozen=True)
class RoutineAction:
    name: str  # a routine of config.yaml


Action = Reminder | DeviceAction | RoutineAction


@dataclass(frozen=True)
class Entry:
    id: str  # "j1", "j2", ... never reused
    at: datetime  # local, minute precision: the next time it fires
    repeat: Repeat
    action: Action
    created: datetime


class ScheduleError(Exception):
    """A refused time, entry, or id; the message is Indonesian and safe to show or speak."""


def parse_local_time(text: str) -> datetime:
    """The local time written exactly as `YYYY-MM-DD HH:MM`."""
    if isinstance(text, str) and _LOCAL_TIME.fullmatch(text):
        try:
            return datetime.strptime(text, _LOCAL_TIME_FORMAT)
        except ValueError:
            pass  # a day or an hour that does not exist, such as 2026-02-30
    raise ScheduleError(
        "Waktunya harus tanggal dan jam yang ada, dengan format YYYY-MM-DD HH:MM "
        "(tahun-bulan-tanggal jam:menit)."
    )


def next_occurrence(at: datetime, repeat: Repeat, after: datetime) -> datetime | None:
    """The first time on the repeat's days, at the clock time of `at`, later than `after`.

    None for a one-shot entry.
    """
    repeat = _checked_repeat(repeat)
    if repeat == "none":
        return None
    candidate = datetime.combine(after.date(), at.time())
    if candidate <= after:
        candidate += _ONE_DAY
    return _first_on(_REPEAT_DAYS[repeat], candidate)


def due(
    entries: Iterable[Entry], now: datetime, grace: timedelta
) -> tuple[list[Entry], list[Entry]]:
    """(the entries to fire now, the entries missed by more than `grace`), soonest first."""
    fire: list[Entry] = []
    skip: list[Entry] = []
    for entry in sorted(entries, key=_soonest_first):
        if entry.at <= now:
            (fire if now - entry.at <= grace else skip).append(entry)
    return fire, skip


class ScheduleStore:
    def __init__(
        self,
        path: Path | str,
        *,
        max_entries: int = 50,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.path = Path(path)
        self.max_entries = max_entries
        self._now = now

    def entries(self) -> list[Entry]:
        """Every entry, soonest first."""
        with _LOCK:
            return sorted(self._read()[1], key=_soonest_first)

    def add(self, at: datetime, repeat: str, action: Action) -> Entry:
        """Save an entry that fires at `at`, a local time, and then per `repeat`.

        `at` is always the next time an entry fires, so a repeating entry starts on the
        first of its days: a weekday entry asked for a Saturday starts on Monday. The current
        minute is still accepted and fires at the next tick: "in one minute" said just before
        the minute changes reaches the store after it did.
        """
        checked_repeat = _checked_repeat(repeat)
        action = _checked_action(action)
        at, now = _minute(at), _minute(self._now())
        if at < now:
            raise ScheduleError("Waktu itu sudah lewat; pilih waktu mulai dari menit sekarang.")
        if at - now > timedelta(days=MAX_DAYS_AHEAD):
            raise ScheduleError(
                f"Waktu itu terlalu jauh; jadwal paling lama {MAX_DAYS_AHEAD} hari dari sekarang."
            )
        if checked_repeat != "none":
            at = _first_on(_REPEAT_DAYS[checked_repeat], at)
        with _LOCK:
            next_id, entries = self._read()
            if len(entries) >= self.max_entries:
                raise ScheduleError(
                    f"Jadwal sudah penuh, {self.max_entries} entri. Batalkan jadwal lama dulu."
                )
            entry = Entry(f"j{next_id}", at, checked_repeat, action, now)
            self._write(next_id + 1, [*entries, entry])
            return entry

    def cancel(self, entry_id: str) -> Entry:
        """Delete entry `entry_id` and return it."""
        with _LOCK:
            next_id, entries = self._read()
            removed = _find(entries, entry_id)
            if removed is None:
                raise ScheduleError("Tidak ada jadwal dengan id itu.")
            self._write(next_id, [entry for entry in entries if entry is not removed])
            return removed

    def fired(self, entry_id: str, at: datetime) -> Entry | None:
        """Mark the occurrence `at` of entry `entry_id` as fired.

        A one-shot entry is removed (None). A repeating one moves to its first time later
        than both `at` and now, so a late fire never fires it twice, and is returned. An
        unknown id, an entry cancelled meanwhile, changes nothing and gives None.
        """
        return self._move_on(entry_id, _minute(at), self._now())

    def skipped(self, entry_id: str, at: datetime, earliest: datetime) -> Entry | None:
        """Mark the occurrence `at` of entry `entry_id` as missed: too late to run.

        A one-shot entry is removed (None). A repeating one moves to its first time at or
        after `earliest`, the oldest time an entry may still fire, and is returned: after
        days away, today's occurrence still fires when it is within the grace. An unknown
        id, an entry cancelled meanwhile, changes nothing and gives None.
        """
        return self._move_on(entry_id, _minute(at), _minute(earliest) - _ONE_MINUTE)

    def _move_on(self, entry_id: str, at: datetime, after: datetime) -> Entry | None:
        """Remove a one-shot entry, or move a repeating one to its first time later than both
        `at` and `after`; None when it was removed or is not there."""
        with _LOCK:
            next_id, entries = self._read()
            entry = _find(entries, entry_id)
            if entry is None:
                return None
            rest = [other for other in entries if other is not entry]
            following = next_occurrence(at, entry.repeat, max(at, after))
            if following is None:
                self._write(next_id, rest)
                return None
            moved = dataclasses.replace(entry, at=following)
            self._write(next_id, [*rest, moved])
            return moved

    def clear(self) -> int:
        """Delete every entry and return how many there were; ids are still never reused."""
        with _LOCK:
            next_id, entries = self._read()
            if entries:
                self._write(next_id, [])
            return len(entries)

    def _read(self) -> tuple[int, list[Entry]]:
        """The next id and the entries; a missing file is an empty schedule and stays missing."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return 1, []
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem)
            return 1, []

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not a schedule, so that no write ever replaces it."""
        name = f"{self.path.name}.corrupt-{self._now():%Y%m%d-%H%M%S}"
        target = self.path.with_name(name)
        counter = 2
        while target.exists():
            target = self.path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(self.path, target)
        except OSError as exc:
            logger.error(
                "Schedule file %s is not a schedule (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Schedule file %s is not a schedule (%s); moved it to %s and started an empty one",
            self.path,
            problem,
            target,
        )

    def _write(self, next_id: int, entries: list[Entry]) -> None:
        # Soonest first, so the owner reads the file in the order the entries fire.
        data = {
            "version": FILE_VERSION,
            "next_id": next_id,
            "entries": [_dump(entry) for entry in sorted(entries, key=_soonest_first)],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                json.dump(data, temp_file, ensure_ascii=False, indent=2)
                temp_file.write("\n")
                # NTFS can commit the rename before the data; push the data to disk first.
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, self.path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise


def _checked_repeat(value: object) -> Repeat:
    for repeat in REPEATS:
        if value == repeat:
            return repeat
    raise ScheduleError("Pengulangannya harus salah satu dari: none, daily, weekdays, weekends.")


def _checked_action(action: object) -> Action:
    """`action` with a reminder's whitespace collapsed; ScheduleError when it is malformed.

    Whether the device, its command, or the routine exists is for the caller to check.
    """
    if isinstance(action, Reminder):
        text = " ".join(action.text.split()) if isinstance(action.text, str) else ""
        if not text:
            raise ScheduleError("Teks pengingatnya kosong.")
        if len(text) > MAX_REMINDER_CHARS:
            raise ScheduleError(
                f"Teks pengingatnya terlalu panjang, paling banyak {MAX_REMINDER_CHARS} karakter."
            )
        return Reminder(text)
    if isinstance(action, DeviceAction):
        if not (_filled(action.device) and _filled(action.command)):
            raise ScheduleError("Jadwal perangkat butuh nama perangkat dan perintahnya.")
        # True is an int to Python, but no temperature or brightness.
        if action.value is not None and type(action.value) is not int:
            raise ScheduleError("Nilai perintahnya harus bilangan bulat.")
        if action.routine is not None and not _filled(action.routine):
            raise ScheduleError("Langkah rutinitas yang ditunda butuh nama rutinitasnya.")
        return action
    if isinstance(action, RoutineAction):
        if not _filled(action.name):
            raise ScheduleError("Jadwal rutinitas butuh nama rutinitasnya.")
        return action
    raise ScheduleError("Jenis jadwalnya tidak dikenal: pengingat, perangkat, atau rutinitas.")


def _filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _first_on(days: frozenset[int], candidate: datetime) -> datetime:
    """`candidate`, or the same clock time on the first later day among `days`."""
    while candidate.weekday() not in days:
        candidate += _ONE_DAY
    return candidate


def _minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def _soonest_first(entry: Entry) -> tuple[datetime, int]:
    return entry.at, int(entry.id[1:])


def _find(entries: list[Entry], entry_id: str) -> Entry | None:
    for entry in entries:
        if entry.id == entry_id:
            return entry
    return None


def _dump(entry: Entry) -> dict[str, object]:
    return {
        "id": entry.id,
        "at": entry.at.isoformat(timespec="minutes"),
        "repeat": entry.repeat,
        "action": _dump_action(entry.action),
        "created": entry.created.isoformat(timespec="minutes"),
    }


def _dump_action(action: Action) -> dict[str, object]:
    if isinstance(action, Reminder):
        return {"type": "reminder", "text": action.text}
    if isinstance(action, DeviceAction):
        data: dict[str, object] = {
            "type": "device",
            "device": action.device,
            "command": action.command,
            "value": action.value,
        }
        if action.routine is not None:
            data["routine"] = action.routine
        return data
    return {"type": "routine", "name": action.name}


def _parse(raw: object) -> tuple[int, list[Entry]]:
    """The next id and the entries in `raw`; ValueError says what is wrong, never an entry."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version, next_id, items = raw["version"], raw["next_id"], raw["entries"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if type(next_id) is not int or next_id < 1:
        raise ValueError("next_id must be a positive integer")
    if not isinstance(items, list):
        raise ValueError("entries must be a list")
    entries = [_parse_entry(index, item) for index, item in enumerate(items)]
    numbers = [int(entry.id[1:]) for entry in entries]
    if len(set(numbers)) != len(numbers):
        raise ValueError("two entries share an id")
    # An id written by hand at or beyond next_id must not be handed out again.
    return max([next_id, *(number + 1 for number in numbers)]), entries


def _parse_entry(index: int, item: object) -> Entry:
    where = f"entries[{index}]"
    if not isinstance(item, dict) or item.keys() != _ENTRY_KEYS:
        raise ValueError(f"{where} must be an object with exactly the keys {sorted(_ENTRY_KEYS)}")
    entry_id = item["id"]
    if not isinstance(entry_id, str) or not _ENTRY_ID.fullmatch(entry_id):
        raise ValueError(f"{where}.id must look like j1")
    try:
        repeat = _checked_repeat(item["repeat"])
    except ScheduleError:
        raise ValueError(f"{where}.repeat must be one of {', '.join(REPEATS)}") from None
    return Entry(
        entry_id,
        _parse_time(item["at"], f"{where}.at"),
        repeat,
        _parse_action(item["action"], f"{where}.action"),
        _parse_time(item["created"], f"{where}.created"),
    )


def _parse_action(raw: object, where: str) -> Action:
    kind = raw.get("type") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or not isinstance(kind, str)
        or raw.keys() not in _ACTION_KEYS.get(kind, ())
    ):
        raise ValueError(
            f"{where} must be a reminder, device, or routine action with exactly its keys"
        )
    if kind == "reminder":
        return Reminder(_parse_text(raw["text"], f"{where}.text"))
    if kind == "device":
        value = raw["value"]
        if value is not None and type(value) is not int:
            raise ValueError(f"{where}.value must be an integer or null")
        routine = _parse_text(raw["routine"], f"{where}.routine") if "routine" in raw else None
        return DeviceAction(
            _parse_text(raw["device"], f"{where}.device"),
            _parse_text(raw["command"], f"{where}.command"),
            value,
            routine,
        )
    return RoutineAction(_parse_text(raw["name"], f"{where}.name"))


def _parse_text(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a string")
    return value


def _parse_time(value: object, where: str) -> datetime:
    """A local time; seconds written by hand are dropped (entries fire by the minute)."""
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return _minute(parsed)
    raise ValueError(f"{where} must be a local ISO 8601 time without a time zone")
