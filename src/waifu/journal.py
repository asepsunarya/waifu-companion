"""Master's daily journal: short notes kept by WIB date, several a day in the order written.

A note is what Master told her for the journal ("hari ini laporannya selesai"), in Master's
own words; a day holds up to `max_notes_per_day` of them, and the same note that day is kept
once, so a turn retried after an API error never doubles it. Notes are kept until the owner
forgets a day (`waifu jurnal forget`): the journal never forgets by itself. A note written
after midnight about the day that just ended may go on that day, so a note's time may be after
its day.

The file (`journal.path`) follows the rules of `todo.py`: every call re-reads it, so a running
Cyrene sees what `waifu jurnal` changed in another process. One lock serialises the calls of
every store in this process; between processes the last writer wins. A write replaces the file
atomically, reading never creates it, and a file that is not a journal is moved aside, never
overwritten. Times are aware WIB, to the second.

A note that looks like a secret is refused here, whatever the model asks for.

`JournalTimer` asks for her question about Master's day once a day, the first time `waifu run`
is on from `journal.ask_at` until midnight: a `PromptTimer` that hands `JOURNAL_PROMPT` to
`request`, which has the brain answer it between Master's utterances, said only until the
midnight that ends its day. The day goes into the state file before the request, so a crash or
a restart never asks twice; a question that fails or is never said is not asked again that day.

This module imports no `waifu.brain` module: `brain/tools.py` names the journal tools from here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from waifu.config import JournalConfig
from waifu.finance.values import WIB
from waifu.gacha.files import write_json_atomic
from waifu.memory import looks_secret
from waifu.play_time import wib_now
from waifu.prompt_timer import PromptTimer, end_of_day

logger = logging.getLogger(__name__)

# Her journal tools (`brain/journal_tools.py`), named here so `brain/tools.py` can keep their
# arguments out of the INFO log without importing the brain's providers.
JOURNAL_WRITE = "journal_write"
JOURNAL_READ = "journal_read"
JOURNAL_TOOLS = (JOURNAL_WRITE, JOURNAL_READ)
# Her own turn, not Master's words: it holds no look word (the screen tool stays shut), no
# play verb or music word (the music tool stays shut), and does not start with a yes (the
# confirmation gate stays shut). journal_read is the turn's sign of success, as daily_briefing
# is the greeting's; journal_write refuses in it, so only Master's answer is ever written.
JOURNAL_PROMPT = (
    "Ini bukan ucapan Master: ini pertanyaan jurnal malammu sendiri, yang kamu mulai sendiri "
    "sekali sehari. Panggil journal_read untuk hari ini saja, tanpa tool lain, lalu tanyakan "
    "dengan hangat dalam satu kalimat pendek bagaimana hari Master; kalau hari ini sudah ada "
    "catatan, tanyakan apakah ada yang mau ditambahkan. Jangan membacakan catatannya dan "
    "jangan menulis apa pun dulu. Kalau Master menjawab pertanyaan ini di ucapan berikutnya, "
    "simpan jawabannya dengan kata-kata Master sendiri lewat journal_write, lalu balas dengan "
    "satu kalimat pendek; kalau Master tidak mau bercerita, jangan simpan apa pun. Jawab tanpa "
    "kartu."
)

FILE_VERSION = 1
_FILE_KEYS = {"version", "days"}
_NOTE_KEYS = {"text", "at"}
# date.fromisoformat also reads 20261017 and 2026-W42-6; the file holds only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# One lock for every store: two stores on the same file in this process must not lose
# each other's notes.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Note:
    """One note of a day; `at` is when it was written (WIB), which may be after that day."""

    text: str
    at: datetime


_Days = dict[date, tuple[Note, ...]]


@dataclass(frozen=True)
class Saved:
    """Where `JournalStore.add` put a note; `added` is False when it was there already."""

    day: date
    note: Note
    added: bool


class JournalError(Exception):
    """A refused note; the message is Indonesian, safe to show or speak, and never repeats it."""


class JournalStore:
    def __init__(
        self,
        path: Path | str,
        *,
        max_chars: int,
        max_notes_per_day: int,
        now: Callable[[], datetime] = wib_now,
    ) -> None:
        """`now` (aware) stamps the notes and decides which day is today, in WIB. A naive
        clock is a ValueError at every call."""
        self.path = Path(path)
        self.max_chars = max_chars
        self.max_notes_per_day = max_notes_per_day
        self._now = now

    def add(self, text: str, day: date | None = None) -> Saved:
        """Write a note on `day` (today when None) after that day's notes. The same text that
        day, ignoring case, gives the note already there and writes nothing."""
        text = self._checked_text(text)
        now = self._clock()
        _check_day(day)
        day = now.date() if day is None else day
        if day > now.date():
            raise JournalError("Jurnal hanya untuk hari ini atau hari yang sudah lewat.")
        with _LOCK:
            days = self._read(now)
            notes = days.get(day, ())
            for note in notes:
                if " ".join(note.text.split()).casefold() == text.casefold():
                    return Saved(day, note, added=False)
            if len(notes) >= self.max_notes_per_day:
                raise JournalError(
                    f"Jurnal hari itu sudah penuh, {self.max_notes_per_day} catatan. Lihat atau "
                    "hapus catatannya dengan 'waifu jurnal'."
                )
            note = Note(text, now.replace(microsecond=0))
            self._write(days | {day: (*notes, note)})
            return Saved(day, note, added=True)

    def notes(self, since: date | None = None, until: date | None = None) -> _Days:
        """The days with notes from `since` through `until` (None: no bound), oldest first,
        each day's notes in the order they were written; a missing file is an empty journal
        and stays missing."""
        now = self._clock()
        with _LOCK:
            days = self._read(now)
        return {
            day: notes
            for day, notes in days.items()
            if (since is None or since <= day) and (until is None or day <= until)
        }

    def forget(self, day: date) -> int:
        """Delete every note of `day`; how many there were (none writes nothing)."""
        _check_day(day)
        now = self._clock()
        with _LOCK:
            days = self._read(now)
            notes = days.pop(day, ())
            if notes:
                self._write(days)
            return len(notes)

    def _clock(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("the journal's clock must give aware times")
        return now.astimezone(WIB)

    def _checked_text(self, text: str) -> str:
        text = " ".join(text.split())
        if not text:
            raise JournalError("Catatan jurnalnya kosong.")
        if len(text) > self.max_chars:
            raise JournalError(
                f"Catatan jurnalnya terlalu panjang, paling banyak {self.max_chars} karakter."
            )
        if looks_secret(text):
            raise JournalError(
                "Catatan itu terlihat seperti rahasia, misalnya kata sandi, PIN, token, atau "
                "nomor kartu, jadi tidak disimpan di jurnal."
            )
        return text

    def _read(self, now: datetime) -> _Days:
        """The days with notes, oldest first; a missing file is an empty journal and stays
        missing."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return {}
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return {}

    def _set_aside(self, problem: ValueError, now: datetime) -> None:
        """Rename a file that is not a journal, so that no write ever replaces it."""
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
                "Journal file %s is not a journal (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Journal file %s is not a journal (%s); moved it to %s and started an empty journal",
            self.path,
            problem,
            target,
        )

    def _write(self, days: _Days) -> None:
        write_json_atomic(
            self.path,
            {
                "version": FILE_VERSION,
                "days": {
                    day.isoformat(): [_dump(note) for note in notes]
                    for day, notes in sorted(days.items())
                    if notes
                },
            },
        )


class JournalTimer(PromptTimer):
    """Asks for her question about Master's day once a day from `ask_at` until midnight, from a
    daemon thread, to be said by midnight WIB. A day the PC was off at night is skipped."""

    def __init__(
        self,
        config: JournalConfig,
        *,
        request: Callable[[str, datetime], None],
        now: Callable[[], datetime] = wib_now,
        tick_seconds: float = 30,
    ) -> None:
        """`now` is aware WIB time. ValueError without `config.ask_at`: no question is asked."""
        ask_at = config.ask_at
        if ask_at is None:
            raise ValueError("the nightly journal question needs journal.ask_at")
        super().__init__(
            JOURNAL_PROMPT,
            due=lambda moment: moment.time() >= ask_at,
            expires=end_of_day,
            state_path=config.state_path,
            request=request,
            what="nightly journal question",
            when=f"from {ask_at:%H:%M} until midnight",
            thread_name="journal",
            now=now,
            tick_seconds=tick_seconds,
        )


def _check_day(day: date | None) -> None:
    """A ValueError for a time given as the day: the file holds days."""
    if isinstance(day, datetime):
        raise ValueError("a journal day must be a date, not a datetime")


def _dump(note: Note) -> dict[str, str]:
    return {"text": note.text, "at": note.at.isoformat()}


def _parse(raw: object) -> _Days:
    """The days with notes in `raw`, oldest first; ValueError says what is wrong, never a
    note."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version, days = raw["version"], raw["days"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if not isinstance(days, dict):
        raise ValueError("days must be an object")
    parsed: _Days = {}
    for index, (key, entries) in enumerate(days.items()):
        where = f"days[{index}]"
        day = _parse_date(key, where)
        if not isinstance(entries, list):
            raise ValueError(f"{where} must be a list of notes")
        notes = tuple(_parse_note(f"{where}[{number}]", e) for number, e in enumerate(entries))
        if notes:
            parsed[day] = notes
    return dict(sorted(parsed.items()))


def _parse_note(where: str, entry: object) -> Note:
    if not isinstance(entry, dict) or entry.keys() != _NOTE_KEYS:
        raise ValueError(f"{where} must be an object with exactly the keys {sorted(_NOTE_KEYS)}")
    if not isinstance(entry["text"], str):
        raise ValueError(f"{where}.text must be a string")
    return Note(entry["text"], _parse_time(entry["at"], f"{where}.at"))


def _parse_date(value: str, where: str) -> date:
    if _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass  # a day that does not exist, such as 2026-02-30
    raise ValueError(f"{where} must be a day written YYYY-MM-DD")


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
