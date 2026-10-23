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
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

WORDS_FILE = Path(__file__).with_name("japanese_words.json")
WORDS_VERSION = 1
_LIST_KEYS = {"version", "words"}
_WORD_KEYS = {"id", "kana", "kanji", "meaning", "example", "example_meaning"}
_TEXT_KEYS = ("id", "kana", "meaning", "example", "example_meaning")
# The first day of the word-of-the-day walk; any fixed day would do.
EPOCH = date(2026, 1, 1)
MASTERED_BOX = 5


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
