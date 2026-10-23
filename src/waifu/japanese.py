"""Belajar bahasa Jepang: the built-in word list.

The words ship in the package (`japanese_words.json`, next to this module, not under a folder
named `data`, which `.gitignore` ignores anywhere): 300 common words and phrases at JLPT N5
level, each with its reading in kana, its usual written form with kanji when it has one, an
Indonesian meaning, and one short example sentence with its meaning. There is no romaji: her
voice reads Japanese from kana and kanji, and a test checks that every Japanese field reaches
the Japanese voice whole.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

WORDS_FILE = Path(__file__).with_name("japanese_words.json")
WORDS_VERSION = 1
_LIST_KEYS = {"version", "words"}
_WORD_KEYS = {"id", "kana", "kanji", "meaning", "example", "example_meaning"}
_TEXT_KEYS = ("id", "kana", "meaning", "example", "example_meaning")


@dataclass(frozen=True)
class Word:
    id: str
    kana: str  # the reading, in hiragana or katakana only
    kanji: str | None  # the usual written form with kanji; None for a word written in kana
    meaning: str  # Indonesian
    example: str  # one short sentence in kana and kanji
    example_meaning: str  # Indonesian


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
