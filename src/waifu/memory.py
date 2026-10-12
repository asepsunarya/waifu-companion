"""Cyrene's long-term memory: a short list of notes about Master, kept between sessions.

The model saves a note through its `remember` tool, and every new session lists all of
them in the system prompt (`memory_prompt_section`). The owner can manage the notes
from the terminal (`waifu memory`) or edit the JSON file by hand.

Every call re-reads the file, so a running Cyrene sees what another process changed.
One lock serialises the calls of every store in this process; between processes the
last writer wins. A write goes to a temp file in the same folder, reaches the disk, and
then replaces the file, so a crash never leaves half a memory. A file that is not a
memory is moved aside before anything is written: the owner's notes are never
overwritten.

A note that looks like a secret is refused here, whatever the model asks for.
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_VERSION = 1
# The words that name a secret, as patterns. The words of a phrase may be joined by spaces,
# "_", or "-", or written together ("api_key", "apikey").
_SECRET_WORDS = (
    "password",
    "passwd",
    "pwd?",
    "passcode",
    r"kata[\s_-]*sandi",
    # A bare "sandi" is also a code (sandi Morse) or a name: only someone's ("sandinya") or
    # an account's ("sandi wifi") is a password.
    r"sandi(?=(?:nya|ku|mu)(?![^\W\d_])|[\s_-]+(?:wi-?fi|e-?mail|akun|hp|laptop|atm|bank))",
    # A badge or a map pin is no PIN.
    r"pin(?![\s_-]+(?:lokasi|koleksi|genshin|honkai|hsr|zzz))",
    "otp",
    "cvv",
    r"api[\s_-]*key",
    # "token listrik" (or "token PLN") is prepaid electricity.
    r"token(?![\s_-]*(?:listrik|pln))",
    r"seed[\s_-]*phrase",
)

# Whole words only: a letter or digit in front makes another word, and so does a letter
# behind, except the suffixes -nya, -ku, and -mu that Indonesian attaches to the word
# itself. So "pinjam" and "tokenisasi" are not matched, while "passwordnya", "PIN-nya",
# "my_password", and "PIN1234" are.
_SECRET_WORD = re.compile(
    r"(?<![^\W_])(?:" + "|".join(_SECRET_WORDS) + r")(?:nya|ku|mu)?(?![^\W\d_])",
    re.IGNORECASE,
)
_TOKEN_RUN = re.compile(r"[A-Za-z0-9_+/=-]{20,}")
# Digits in groups separated by single spaces or dashes ("4111 1111 1111 1111 12-28").
_DIGIT_RUN = re.compile(r"[0-9](?:[ -]?[0-9])*")
_DIGIT_GROUP_SEPARATOR = re.compile(r"[ -]")
_CARD_MIN_DIGITS, _CARD_MAX_DIGITS = 13, 19
_FACT_ID = re.compile(r"m[1-9][0-9]*")
_FILE_KEYS = {"version", "next_id", "facts"}
_FACT_KEYS = {"id", "text", "created", "updated"}

# One lock for every store: two stores on the same file in this process must not lose
# each other's notes.
_LOCK = threading.Lock()

_SECTION_HEADER = "Ingatanmu tentang Master (catatan dari obrolan sebelumnya, tersimpan di PC ini):"
_SECTION_EMPTY = "Ingatanmu tentang Master: belum ada catatan."
_SECTION_FOOTER = (
    "Pakai ingatan ini secara alami kalau relevan; jangan membacakan daftarnya kecuali "
    "Master memintanya. Simpan hal baru yang penting dengan tool remember, perbarui catatan "
    "yang berubah dengan replaces, dan hapus dengan forget kalau Master memintanya."
)


@dataclass(frozen=True)
class Fact:
    id: str  # "m1", "m2", ... never reused
    text: str
    created: datetime  # local time, minute precision
    updated: datetime


class MemoryError(Exception):
    """A refused note or id; the message is Indonesian and safe to show or speak.

    It shadows the builtin of the same name inside this module only.
    """


def looks_secret(text: str) -> bool:
    """True when `text` names a secret, holds a long mixed token, or a card number."""
    return (
        _SECRET_WORD.search(text) is not None
        or any(_has_letters_and_digits(run) for run in _TOKEN_RUN.findall(text))
        or any(_holds_card_number(run) for run in _DIGIT_RUN.findall(text))
    )


def memory_prompt_section(facts: Sequence[Fact]) -> str:
    """The system-prompt section listing the notes (no square brackets: emotion tags)."""
    if facts:
        lines = [_SECTION_HEADER]
        lines += [f"- {fact.id} ({fact.updated:%d-%m-%Y}): {fact.text}" for fact in facts]
    else:
        lines = [_SECTION_EMPTY]
    return "\n".join([*lines, _SECTION_FOOTER])


class MemoryStore:
    def __init__(
        self,
        path: Path | str,
        *,
        max_facts: int = 100,
        max_chars: int = 200,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.path = Path(path)
        self.max_facts = max_facts
        self.max_chars = max_chars
        self._now = now

    def facts(self) -> list[Fact]:
        """Every note, oldest first."""
        with _LOCK:
            return self._read()[1]

    def add(self, text: str) -> Fact:
        """Save a note; a text already saved (ignoring case) returns that note unchanged."""
        text = self._checked(text)
        with _LOCK:
            next_id, facts = self._read()
            for fact in facts:
                if _same_text(fact.text, text):
                    return fact
            if len(facts) >= self.max_facts:
                raise MemoryError(
                    f"Ingatan sudah penuh, {self.max_facts} catatan. "
                    "Gabungkan atau hapus catatan lama dulu."
                )
            stamp = self._minute()
            fact = Fact(f"m{next_id}", text, stamp, stamp)
            self._write(next_id + 1, [*facts, fact])
            return fact

    def replace(self, fact_id: str, text: str) -> Fact:
        """Give note `fact_id` a new text, keeping its id, place, and `created`."""
        text = self._checked(text)
        with _LOCK:
            next_id, facts = self._read()
            old = _find(facts, fact_id)
            for other in facts:
                if other is not old and _same_text(other.text, text):
                    raise MemoryError(f"Catatan yang sama sudah ada sebagai {other.id}.")
            new = dataclasses.replace(old, text=text, updated=self._minute())
            self._write(next_id, [new if fact is old else fact for fact in facts])
            return new

    def forget(self, fact_id: str) -> Fact:
        """Delete note `fact_id` and return it."""
        with _LOCK:
            next_id, facts = self._read()
            removed = _find(facts, fact_id)
            self._write(next_id, [fact for fact in facts if fact is not removed])
            return removed

    def clear(self) -> int:
        """Delete every note and return how many there were; ids are still never reused."""
        with _LOCK:
            next_id, facts = self._read()
            if facts:
                self._write(next_id, [])
            return len(facts)

    def _checked(self, text: str) -> str:
        text = " ".join(text.split())
        if not text:
            raise MemoryError("Catatannya kosong.")
        if len(text) > self.max_chars:
            raise MemoryError(
                f"Catatannya terlalu panjang, paling banyak {self.max_chars} karakter."
            )
        if looks_secret(text):
            raise MemoryError(
                "Catatan itu terlihat seperti rahasia, misalnya kata sandi, PIN, token, "
                "atau nomor kartu, jadi tidak disimpan."
            )
        return text

    def _minute(self) -> datetime:
        return self._now().replace(second=0, microsecond=0)

    def _read(self) -> tuple[int, list[Fact]]:
        """The next id and the notes; a missing file is an empty memory and stays missing."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return 1, []
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem)
            return 1, []

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not a memory, so that no write ever replaces it."""
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
                "Memory file %s is not a memory (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Memory file %s is not a memory (%s); moved it to %s and started an empty memory",
            self.path,
            problem,
            target,
        )

    def _write(self, next_id: int, facts: list[Fact]) -> None:
        data = {"version": FILE_VERSION, "next_id": next_id, "facts": [_dump(f) for f in facts]}
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


def _same_text(saved: str, text: str) -> bool:
    return " ".join(saved.split()).casefold() == text.casefold()


def _find(facts: list[Fact], fact_id: str) -> Fact:
    for fact in facts:
        if fact.id == fact_id:
            return fact
    raise MemoryError("Tidak ada catatan dengan id itu.")


def _has_letters_and_digits(run: str) -> bool:
    return any(char.isalpha() for char in run) and any(char.isdigit() for char in run)


def _holds_card_number(run: str) -> bool:
    """True when neighbouring whole groups of `run` make a card number.

    A card number said with its expiry or CVV shares the run with them. A group is never
    split, so a long number written without separators is not searched for a card.
    """
    groups = _DIGIT_GROUP_SEPARATOR.split(run)
    for start in range(len(groups)):
        digits = ""
        for group in groups[start:]:
            digits += group
            if len(digits) > _CARD_MAX_DIGITS:
                break
            if _is_card_number(digits):
                return True
    return False


def _is_card_number(digits: str) -> bool:
    """True for 13-19 digits that pass the Luhn check."""
    if not _CARD_MIN_DIGITS <= len(digits) <= _CARD_MAX_DIGITS:
        return False
    total = 0
    for position, digit in enumerate(int(char) for char in reversed(digits)):
        if position % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def _dump(fact: Fact) -> dict[str, str]:
    return {
        "id": fact.id,
        "text": fact.text,
        "created": fact.created.isoformat(timespec="minutes"),
        "updated": fact.updated.isoformat(timespec="minutes"),
    }


def _parse(raw: object) -> tuple[int, list[Fact]]:
    """The next id and the notes in `raw`; ValueError says what is wrong, never a note."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version, next_id, entries = raw["version"], raw["next_id"], raw["facts"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if type(next_id) is not int or next_id < 1:
        raise ValueError("next_id must be a positive integer")
    if not isinstance(entries, list):
        raise ValueError("facts must be a list")
    facts = [_parse_fact(index, entry) for index, entry in enumerate(entries)]
    numbers = [int(fact.id[1:]) for fact in facts]
    if len(set(numbers)) != len(numbers):
        raise ValueError("two facts share an id")
    # An id written by hand at or beyond next_id must not be handed out again.
    return max([next_id, *(number + 1 for number in numbers)]), facts


def _parse_fact(index: int, entry: object) -> Fact:
    where = f"facts[{index}]"
    if not isinstance(entry, dict) or entry.keys() != _FACT_KEYS:
        raise ValueError(f"{where} must be an object with exactly the keys {sorted(_FACT_KEYS)}")
    fact_id, text = entry["id"], entry["text"]
    if not isinstance(fact_id, str) or not _FACT_ID.fullmatch(fact_id):
        raise ValueError(f"{where}.id must look like m1")
    if not isinstance(text, str):
        raise ValueError(f"{where}.text must be a string")
    return Fact(
        fact_id,
        text,
        _parse_time(entry["created"], f"{where}.created"),
        _parse_time(entry["updated"], f"{where}.updated"),
    )


def _parse_time(value: object, where: str) -> datetime:
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(value)
    raise ValueError(f"{where} must be an ISO 8601 time")
