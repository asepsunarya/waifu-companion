"""How her Indonesian voices read a word (SPEC, kamus cyrene (v1.49)): built-in rules for the
game abbreviations, and Master's own rules in a small file of their own.

The speech router passes every Indonesian run through `pronounce` right before a voice speaks
it, so the reply's text stays as written everywhere else (the phone, the history, the logs).
The file follows the rules of `voice_override.py`: re-read at every call, replaced atomically,
never created by reading, and a file that is not such a dictionary is moved aside, never
overwritten; one lock serialises the calls of every store in this process.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from waifu.gacha.files import write_json_atomic

logger = logging.getLogger(__name__)

# Matched only in this case: her "Zzz" is a sound, not the game.
BUILT_IN_READINGS: Mapping[str, str] = {
    "HSR": "Ha Es Er",
    "ZZZ": "Zet Zet Zet",
    "HoYoLAB": "Hoyolab",
    "HoYoverse": "Hoyovers",
}
FILE_VERSION = 1
MAX_RULES = 200
TERM_MAX_WORDS = 3
TERM_MAX_CHARS = 30
READING_MAX_WORDS = 8
READING_MAX_CHARS = 60
TERM_PROBLEM = (
    f"Kata yang dibaca harus 1 sampai {TERM_MAX_WORDS} kata, paling banyak {TERM_MAX_CHARS} "
    "karakter, dengan huruf, tanpa huruf Jepang."
)
READING_PROBLEM = (
    f"Bacaannya harus 1 sampai {READING_MAX_WORDS} kata, paling banyak {READING_MAX_CHARS} "
    "karakter, dengan huruf, tanpa huruf Jepang."
)
SAME_PROBLEM = "Bacaannya tidak boleh memuat kata itu sendiri."
FULL_PROBLEM = (
    f"Kamus sudah berisi {MAX_RULES} aturan; hapus satu dulu dengan 'waifu lafal remove'."
)

# Kana, kanji, and half-width katakana: the Japanese voice is never given a rule.
_JAPANESE_SCRIPT = re.compile("[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")

# One lock for every store: two of them on the same file must not lose a change.
_LOCK = threading.Lock()


class LexiconError(ValueError):
    """A rule that cannot be kept; the message is Indonesian and safe to say."""


def pronounce(text: str, rules: Mapping[str, str] | None = None) -> str:
    """`text` with each term read as its rule says, in one pass, longest term first, whole
    words only: the built-in rules in their own case, Master's `rules` in any case (one with
    a built-in's term replaces that built-in)."""
    rules = rules or {}
    owned = {_key(term): reading for term, reading in rules.items()}
    built_in = {
        term: reading for term, reading in BUILT_IN_READINGS.items() if _key(term) not in owned
    }
    if not owned and not built_in:
        return text

    def reading(match: re.Match[str]) -> str:
        found = match.group(0)
        return owned.get(_key(found), built_in.get(found, found))

    return _pattern(tuple(built_in), tuple(rules)).sub(reading, text)


def checked_rule(term: str, reading: str) -> tuple[str, str]:
    """The rule with its whitespace collapsed; LexiconError (Indonesian) when it cannot be one."""
    term, reading = " ".join(term.split()), " ".join(reading.split())
    if not _fits(term, TERM_MAX_WORDS, TERM_MAX_CHARS):
        raise LexiconError(TERM_PROBLEM)
    if not _fits(reading, READING_MAX_WORDS, READING_MAX_CHARS):
        raise LexiconError(READING_PROBLEM)
    if _whole_words(_alternative(term), ignore_case=True).search(reading):
        raise LexiconError(SAME_PROBLEM)
    return term, reading


class Lexicon:
    """Master's rules in `path`, as `{"version": 1, "rules": {"Acheron": "A Ke Ron"}}`."""

    def __init__(self, path: Path | str, *, now: Callable[[], datetime] = datetime.now) -> None:
        """`now` names a broken file moved aside."""
        self.path = Path(path)
        self._now = now
        self._read_failing = False

    def read(self) -> dict[str, str]:
        """Master's rules in the file's order; none for a missing file. OSError when it cannot
        be read."""
        with _LOCK:
            return self._read()

    def readings(self) -> dict[str, str]:
        """`read`, never raising: a file that cannot be read gives no rules, warned about once
        until it can be read again (the voices then still have the built-in rules)."""
        try:
            rules = self.read()
        except OSError as exc:
            if not self._read_failing:
                self._read_failing = True
                logger.warning(
                    "Cannot read the pronunciation rules %s; using only the built-in ones: %s",
                    self.path,
                    exc,
                )
            return {}
        self._read_failing = False
        return rules

    def add(self, term: str, reading: str) -> tuple[str, str]:
        """Keep the rule (`checked_rule`) in place of one for the same term in any case; returns
        it. LexiconError for a rule that cannot be one or a full dictionary, OSError from the
        file."""
        term, reading = checked_rule(term, reading)
        with _LOCK:
            rules = {kept: said for kept, said in self._read().items() if _key(kept) != _key(term)}
            if len(rules) >= MAX_RULES:
                raise LexiconError(FULL_PROBLEM)
            rules[term] = reading
            self._write(rules)
        return term, reading

    def remove(self, term: str) -> str | None:
        """Drop Master's rule for `term` in any case; returns its term as kept, None when he has
        none. OSError from the file."""
        with _LOCK:
            rules = self._read()
            found = next((kept for kept in rules if _key(kept) == _key(term)), None)
            if found is None:
                return None
            del rules[found]
            self._write(rules)
        return found

    def _read(self) -> dict[str, str]:
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return {}
        except ValueError as problem:  # also bad JSON, bad UTF-8, and a rule that is not one
            self._set_aside(problem)
            return {}

    def _write(self, rules: dict[str, str]) -> None:
        write_json_atomic(self.path, {"version": FILE_VERSION, "rules": rules})

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not a dictionary, so that no write ever replaces it."""
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
                "The pronunciation rules %s are not such a file (%s) and could not be moved "
                "aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "The pronunciation rules %s are not such a file (%s); moved it to %s and started anew",
            self.path,
            problem,
            target,
        )


def _parse(raw: object) -> dict[str, str]:
    """The rules in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != {"version", "rules"}:
        raise ValueError("expected an object with only a version and rules")
    version = raw["version"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    found = raw["rules"]
    if not isinstance(found, dict) or len(found) > MAX_RULES:
        raise ValueError(f"rules must be an object of at most {MAX_RULES} words")
    rules: dict[str, str] = {}
    for term, reading in found.items():
        if not isinstance(reading, str):
            raise ValueError(f"the reading of {term!r} must be text")
        term, reading = checked_rule(term, reading)
        if any(_key(kept) == _key(term) for kept in rules):
            raise ValueError(f"{term!r} has two rules")
        rules[term] = reading
    return rules


def _fits(text: str, max_words: int, max_chars: int) -> bool:
    return (
        1 <= len(text.split()) <= max_words
        and len(text) <= max_chars
        and any(char.isalpha() for char in text)
        and not _JAPANESE_SCRIPT.search(text)
    )


def _key(term: str) -> str:
    return " ".join(term.split()).casefold()


def _alternative(term: str) -> str:
    """`term` as a pattern in which each space matches any whitespace."""
    return r"\s+".join(re.escape(word) for word in term.split())


def _whole_words(alternatives: str, *, ignore_case: bool = False) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE if ignore_case else 0)


@functools.lru_cache(maxsize=32)
def _pattern(built_in: tuple[str, ...], owned: tuple[str, ...]) -> re.Pattern[str]:
    """One alternation of every term, longest first; Master's terms match in any case."""
    terms = [(term, _alternative(term)) for term in built_in]
    terms += [(term, f"(?i:{_alternative(term)})") for term in owned]
    terms.sort(key=lambda pair: len(pair[0]), reverse=True)
    return _whole_words("|".join(alternative for _, alternative in terms))
