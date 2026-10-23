"""Belajar bahasa Jepang: the built-in word list, the word of the day, and the Leitner rules.

The words ship in the package (`japanese_words.json`, next to this module, not under a folder
named `data`, which `.gitignore` ignores anywhere): 300 common words and phrases at JLPT N5
level, each with its reading in kana, its usual written form with kanji when it has one, an
Indonesian meaning, and one short example sentence with its meaning. There is no romaji: her
voice reads Japanese from kana and kanji, and a test checks that every Japanese field reaches
the Japanese voice whole.

The word of the day keeps no state: the words in a fixed shuffle (the SHA-256 of their ids),
one per day from `EPOCH`, so every word comes once before any comes again. Master's words move
through Leitner boxes 1 to `MASTERED_BOX`: a right answer moves a word up a box and waits twice
as long before asking it again, a wrong one sends it back to box 1 for tomorrow.

The store of Master's words (`japanese.path`) follows the rules of `todo.py`: every call
re-reads it, one lock serialises the calls of every store in this process, a write replaces it
atomically, reading never creates it, and a file that is not such a store is moved aside, never
overwritten. `waifu run` and `waifu chat` write it; `waifu jepang` only reads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from waifu.gacha.files import write_json_atomic
from waifu.play_time import wib_now

logger = logging.getLogger(__name__)

WORDS_FILE = Path(__file__).with_name("japanese_words.json")
WORDS_VERSION = 1
_LIST_KEYS = {"version", "words"}
_WORD_KEYS = {"id", "kana", "kanji", "meaning", "example", "example_meaning"}
_TEXT_KEYS = ("id", "kana", "meaning", "example", "example_meaning")
# The first day of the word-of-the-day walk; any fixed day would do.
EPOCH = date(2026, 1, 1)
MASTERED_BOX = 5
STORE_VERSION = 1
_STORE_KEYS = {"version", "words"}
_PROGRESS_KEYS = {"box", "due", "learned"}

# One lock for every store: two stores on the same file in this process must not lose each
# other's words.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Word:
    id: str
    kana: str  # the reading, in hiragana or katakana only
    kanji: str | None  # the usual written form with kanji; None for a word written in kana
    meaning: str  # Indonesian
    example: str  # one short sentence in kana and kanji
    example_meaning: str  # Indonesian


@dataclass(frozen=True)
class WordProgress:
    """Where one of Master's words stands: its Leitner box, the day it is asked again from, and
    the day she introduced it."""

    box: int  # 1 to MASTERED_BOX
    due: date
    learned: date


def load_words(path: Path = WORDS_FILE) -> tuple[Word, ...]:
    """The words of the list at `path`, in its order; ValueError says what is wrong."""
    return _parse(json.loads(path.read_text(encoding="utf-8")))


def _parse(raw: object) -> tuple[Word, ...]:
    if not isinstance(raw, dict) or raw.keys() != _LIST_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_LIST_KEYS)}")
    version = raw["version"]
    if type(version) is not int or version != WORDS_VERSION:
        raise ValueError(f"version must be {WORDS_VERSION}")
    entries = raw["words"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("words must be a list that is not empty")
    words: list[Word] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        at = f"words[{index}]"
        if not isinstance(entry, dict) or entry.keys() != _WORD_KEYS:
            raise ValueError(f"{at} must be an object with exactly the keys {sorted(_WORD_KEYS)}")
        for key in _TEXT_KEYS:
            if not isinstance(entry[key], str) or not entry[key]:
                raise ValueError(f"{at}.{key} must be text that is not empty")
        kanji = entry["kanji"]
        if kanji is not None and (not isinstance(kanji, str) or not kanji):
            raise ValueError(f"{at}.kanji must be text that is not empty, or null")
        if entry["id"] in seen:
            raise ValueError(f"{at}.id {entry['id']!r} comes twice")
        seen.add(entry["id"])
        words.append(
            Word(
                id=entry["id"],
                kana=entry["kana"],
                kanji=kanji,
                meaning=entry["meaning"],
                example=entry["example"],
                example_meaning=entry["example_meaning"],
            )
        )
    return tuple(words)


def word_of_the_day(words: Sequence[Word], day: date) -> Word:
    """The word for `day`: the same all day, and none again before every word has come."""
    shuffled = sorted(words, key=lambda word: hashlib.sha256(word.id.encode("utf-8")).hexdigest())
    return shuffled[(day - EPOCH).days % len(shuffled)]


def review(progress: WordProgress, correct: bool, today: date) -> WordProgress:
    """`progress` after Master answered on `today`: right moves it up a box (the last box
    stays), wrong back to box 1; it is due again after 1, 2, 4, 8, or 16 days by its box."""
    box = min(progress.box + 1, MASTERED_BOX) if correct else 1
    return WordProgress(box, today + timedelta(days=2 ** (box - 1)), progress.learned)


def next_quiz(progress: Mapping[str, WordProgress], today: date) -> str | None:
    """The id of the word to ask on `today`: of the due ones, the lowest box, then the one due
    longest, then the id; None when none is due."""
    due = [(word.box, word.due, word_id) for word_id, word in progress.items() if word.due <= today]
    return min(due)[2] if due else None


class JapaneseStore:
    """Master's words: which ones she introduced, and where each stands in the Leitner boxes."""

    def __init__(self, path: Path | str, *, now: Callable[[], datetime] = wib_now) -> None:
        """`now` (aware) only names a broken file moved aside; the days are passed in. A naive
        clock is a ValueError at every call."""
        self.path = Path(path)
        self._now = now

    def progress(self) -> dict[str, WordProgress]:
        """Every learned word by id; a missing file is nothing learned and stays missing."""
        now = self._clock()
        with _LOCK:
            return self._read(now)

    def learn(self, word_id: str, today: date) -> bool:
        """Put a word not learned yet into box 1, due `today`; True when it was new. A word
        already learned changes nothing and nothing is written."""
        now = self._clock()
        with _LOCK:
            words = self._read(now)
            if word_id in words:
                return False
            words[word_id] = WordProgress(1, today, today)
            self._write(words)
            return True

    def grade(self, word_id: str, correct: bool, today: date) -> WordProgress | None:
        """Write the `review` of Master's answer on `today`; None, writing nothing, for a word
        never learned."""
        now = self._clock()
        with _LOCK:
            words = self._read(now)
            if word_id not in words:
                return None
            words[word_id] = reviewed = review(words[word_id], correct, today)
            self._write(words)
            return reviewed

    def _clock(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("the Japanese lesson store's clock must give aware times")
        return now

    def _read(self, now: datetime) -> dict[str, WordProgress]:
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse_progress(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return {}
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return {}

    def _set_aside(self, problem: ValueError, now: datetime) -> None:
        """Rename a file that is not a Japanese lesson file, so that no write ever replaces it."""
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
                "Japanese file %s is not a Japanese lesson file (%s) and could not be moved "
                "aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Japanese file %s is not a Japanese lesson file (%s); moved it to %s and started an "
            "empty one",
            self.path,
            problem,
            target,
        )

    def _write(self, words: Mapping[str, WordProgress]) -> None:
        write_json_atomic(
            self.path,
            {
                "version": STORE_VERSION,
                "words": {word_id: _dump(words[word_id]) for word_id in sorted(words)},
            },
        )


def learn_word_of_the_day(
    words: Sequence[Word], store: JapaneseStore, day: date
) -> tuple[Word, bool]:
    """The word for `day`, learned once: (the word, whether it was new)."""
    word = word_of_the_day(words, day)
    return word, store.learn(word.id, day)


def _dump(progress: WordProgress) -> dict[str, object]:
    return {
        "box": progress.box,
        "due": progress.due.isoformat(),
        "learned": progress.learned.isoformat(),
    }


def _parse_progress(raw: object) -> dict[str, WordProgress]:
    """The words in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _STORE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_STORE_KEYS)}")
    version = raw["version"]
    if type(version) is not int or version != STORE_VERSION:
        raise ValueError(f"version must be {STORE_VERSION}")
    entries = raw["words"]
    if not isinstance(entries, dict):
        raise ValueError("words must be an object of word ids")
    words: dict[str, WordProgress] = {}
    for word_id, entry in entries.items():
        at = f"words[{word_id!r}]"
        if not word_id:
            raise ValueError("a word id must not be empty")
        if not isinstance(entry, dict) or entry.keys() != _PROGRESS_KEYS:
            raise ValueError(
                f"{at} must be an object with exactly the keys {sorted(_PROGRESS_KEYS)}"
            )
        box = entry["box"]
        if type(box) is not int or not 1 <= box <= MASTERED_BOX:
            raise ValueError(f"{at}.box must be a whole number from 1 to {MASTERED_BOX}")
        due = _parse_day(entry["due"], f"{at}.due")
        learned = _parse_day(entry["learned"], f"{at}.learned")
        if due < learned:
            raise ValueError(f"{at}.due must not be before the day it was learned")
        words[word_id] = WordProgress(box, due, learned)
    return words


def _parse_day(value: object, where: str) -> date:
    if isinstance(value, str):
        try:
            day = date.fromisoformat(value)
        except ValueError:
            pass
        else:
            if day.isoformat() == value:  # YYYY-MM-DD only, not 20261026
                return day
    raise ValueError(f"{where} must be a date YYYY-MM-DD")
