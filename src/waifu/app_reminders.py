"""Master's reminders for when he next opens a game: "pas buka HSR nanti, ingetin aku farming
relic".

A reminder belongs to one tracked game and is said when the play time watcher sees that game
start (`play_watch.PlayTimeWatcher(on_started=...)`): once by default, then gone, or at every
start (`every_time`) until Master removes it. Ids ("g1", "g2", ...) are never reused, also after a
reminder is removed or said.

The file (`app_reminders.path`) follows the rules of `todo.py`: every call re-reads it, so a
running Cyrene sees what `waifu pengingat-game` or `waifu chat` changed in another process. One
lock serialises the calls of every store in this process; between processes the last writer
wins. A write replaces the file atomically, reading never creates it, and a file that is not an
app reminder list is moved aside before anything is written: it is never overwritten. Times are
aware WIB, stamped to the second. It is not the schedule file: older versions set that whole file
aside on a field they do not know.

A reminder that looks like a secret is refused here, whatever the model asks for.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast, get_args

from waifu.config import GameId
from waifu.gacha.files import write_json_atomic
from waifu.memory import looks_secret
from waifu.play_time import wib_now

logger = logging.getLogger(__name__)

FILE_VERSION = 1
MAX_REMINDERS = 30
MAX_CHARS = 200
_GAME_IDS = frozenset(get_args(GameId))
_FILE_KEYS = {"version", "next_id", "reminders"}
_REMINDER_KEYS = {"id", "game", "text", "every_time", "created"}
_REMINDER_ID = re.compile(r"g[1-9][0-9]*")

# One lock for every store: two stores on the same file in this process must not lose
# each other's reminders.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class AppReminder:
    """What to tell Master when `game` starts: once, or at every start with `every_time`."""

    id: str  # "g1", "g2", ...: never reused
    game: GameId
    text: str
    every_time: bool
    created: datetime


class AppReminderError(Exception):
    """A refused reminder; the message is Indonesian and safe to show or speak."""


class AppReminderStore:
    def __init__(
        self,
        path: Path | str,
        *,
        max_reminders: int = MAX_REMINDERS,
        max_chars: int = MAX_CHARS,
        now: Callable[[], datetime] = wib_now,
    ) -> None:
        """`now` (aware) stamps the reminders; a naive clock is a ValueError at every call."""
        self.path = Path(path)
        self.max_reminders = max_reminders
        self.max_chars = max_chars
        self._now = now

    def add(self, game: GameId, text: str, *, every_time: bool = False) -> AppReminder:
        """Keep a new reminder for `game`'s next start (every start with `every_time`)."""
        if game not in _GAME_IDS:
            raise ValueError(f"unknown game '{game}': one of {sorted(_GAME_IDS)}")
        text = self._checked_text(text)
        now = self._clock()
        with _LOCK:
            next_id, reminders = self._read(now)
            if len(reminders) >= self.max_reminders:
                raise AppReminderError(
                    f"Pengingat game sudah penuh, {self.max_reminders} pengingat. "
                    "Hapus yang lama dulu."
                )
            reminder = AppReminder(f"g{next_id}", game, text, every_time, _stamp(now))
            self._write(next_id + 1, [*reminders, reminder])
            return reminder

    def reminders(self) -> list[AppReminder]:
        """Every reminder, in the order they were made."""
        now = self._clock()
        with _LOCK:
            return self._read(now)[1]

    def for_game(self, game: GameId) -> list[AppReminder]:
        """`game`'s reminders, in the order they were made."""
        return [reminder for reminder in self.reminders() if reminder.game == game]

    def remove(self, reminder_id: str) -> AppReminder | None:
        """Delete reminder `reminder_id` and return it; None when there is no such reminder."""
        now = self._clock()
        with _LOCK:
            next_id, reminders = self._read(now)
            found = next((r for r in reminders if r.id == reminder_id), None)
            if found is not None:
                self._write(next_id, [r for r in reminders if r is not found])
            return found

    def drop(self, reminder_ids: Collection[str]) -> None:
        """Delete those of `reminder_ids` still kept, in one write; none when none is."""
        now = self._clock()
        with _LOCK:
            next_id, reminders = self._read(now)
            kept = [r for r in reminders if r.id not in reminder_ids]
            if len(kept) != len(reminders):
                self._write(next_id, kept)

    def _clock(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("the app reminder store's clock must give aware times")
        return now

    def _checked_text(self, text: str) -> str:
        text = " ".join(text.split())
        if not text:
            raise AppReminderError("Pengingatnya kosong.")
        if len(text) > self.max_chars:
            raise AppReminderError(
                f"Pengingatnya terlalu panjang, paling banyak {self.max_chars} karakter."
            )
        if looks_secret(text):
            raise AppReminderError(
                "Pengingat itu terlihat seperti rahasia, misalnya kata sandi, PIN, token, "
                "atau nomor kartu, jadi tidak disimpan."
            )
        return text

    def _read(self, now: datetime) -> tuple[int, list[AppReminder]]:
        """The next id and the reminders; a missing file is an empty list and stays missing."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return 1, []
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return 1, []

    def _set_aside(self, problem: ValueError, now: datetime) -> None:
        """Rename a file that is not an app reminder list, so that no write ever replaces it."""
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
                "App reminder file %s is not an app reminder list (%s) and could not be moved "
                "aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "App reminder file %s is not an app reminder list (%s); moved it to %s and started "
            "an empty list",
            self.path,
            problem,
            target,
        )

    def _write(self, next_id: int, reminders: list[AppReminder]) -> None:
        write_json_atomic(
            self.path,
            {
                "version": FILE_VERSION,
                "next_id": next_id,
                "reminders": [_dump(reminder) for reminder in reminders],
            },
        )


def _stamp(now: datetime) -> datetime:
    return now.replace(microsecond=0)


def _dump(reminder: AppReminder) -> dict[str, object]:
    return {
        "id": reminder.id,
        "game": reminder.game,
        "text": reminder.text,
        "every_time": reminder.every_time,
        "created": reminder.created.isoformat(),
    }


def _parse(raw: object) -> tuple[int, list[AppReminder]]:
    """The next id and the reminders in `raw`; ValueError says what is wrong, never a text."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version, next_id, entries = raw["version"], raw["next_id"], raw["reminders"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if type(next_id) is not int or next_id < 1:
        raise ValueError("next_id must be a positive integer")
    if not isinstance(entries, list):
        raise ValueError("reminders must be a list")
    reminders = [_parse_reminder(index, entry) for index, entry in enumerate(entries)]
    numbers = [int(reminder.id[1:]) for reminder in reminders]
    if len(set(numbers)) != len(numbers):
        raise ValueError("two reminders share an id")
    # An id written by hand at or beyond next_id must not be handed out again.
    return max([next_id, *(number + 1 for number in numbers)]), reminders


def _parse_reminder(index: int, entry: object) -> AppReminder:
    where = f"reminders[{index}]"
    if not isinstance(entry, dict) or entry.keys() != _REMINDER_KEYS:
        raise ValueError(
            f"{where} must be an object with exactly the keys {sorted(_REMINDER_KEYS)}"
        )
    reminder_id, game, text, every_time = (
        entry["id"],
        entry["game"],
        entry["text"],
        entry["every_time"],
    )
    if not isinstance(reminder_id, str) or not _REMINDER_ID.fullmatch(reminder_id):
        raise ValueError(f"{where}.id must look like g1")
    if not isinstance(game, str) or game not in _GAME_IDS:
        raise ValueError(f"{where}.game must be one of {sorted(_GAME_IDS)}")
    if not isinstance(text, str):
        raise ValueError(f"{where}.text must be a string")
    if not isinstance(every_time, bool):
        raise ValueError(f"{where}.every_time must be true or false")
    return AppReminder(
        reminder_id, cast(GameId, game), text, every_time, _parse_time(entry["created"], where)
    )


def _parse_time(value: object, where: str) -> datetime:
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if moment.tzinfo is not None:
                return moment
    raise ValueError(f"{where}.created must be an ISO 8601 time with its offset")
