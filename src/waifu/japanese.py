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

Master may also keep words of his own ("simpan kata itu", v1.56): up to MAX_CUSTOM_WORDS, each
its kana, its kanji form or none, and its Indonesian meaning, under the store's optional
"custom" key with ids "c1", "c2", ...; their Leitner progress sits in "words" with the list's.
A store without them is written exactly as v1.39 wrote it. An older build moves a store with
them aside as broken (renamed, never deleted).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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
_CUSTOM_KEY = "custom"
_PROGRESS_KEYS = {"box", "due", "learned"}
_CUSTOM_WORD_KEYS = {"kana", "kanji", "meaning", "added"}
_CUSTOM_ID = re.compile(r"c[1-9][0-9]*")
MAX_CUSTOM_WORDS = 200
MAX_KANA_CHARS = 20
MAX_KANJI_CHARS = 20
MAX_MEANING_CHARS = 80
# A reading: hiragana, katakana, and the long vowel mark.
_KANA_READING = re.compile(rf"[\u3041-\u3096\u30a1-\u30fa\u30fc]{{1,{MAX_KANA_CHARS}}}")
# A written form: CJK ideographs (with extension A and the compatibility block), kana, the long
# vowel mark, and the iteration mark 々; at least one ideograph.
_IDEOGRAPH = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_KANJI_FORM = re.compile(
    rf"[{_IDEOGRAPH}\u3005\u3041-\u3096\u30a1-\u30fa\u30fc]{{1,{MAX_KANJI_CHARS}}}"
)
_HAS_IDEOGRAPH = re.compile(f"[{_IDEOGRAPH}]")
# Runs of letters and digits in a meaning.
_MEANING_WORD = re.compile(r"[^\W_]+")

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


@dataclass(frozen=True)
class CustomWord:
    """A word Master kept himself: its reading, its usual written form (None for kana alone),
    its Indonesian meaning, and the day he added it."""

    kana: str
    kanji: str | None
    meaning: str
    added: date


class DeckFull(Exception):
    """Master already kept MAX_CUSTOM_WORDS words of his own."""


def is_kana_reading(text: str) -> bool:
    """Whether `text` is a reading: 1 to MAX_KANA_CHARS hiragana, katakana, or "ー"."""
    return _KANA_READING.fullmatch(text) is not None


def is_kanji_form(text: str) -> bool:
    """Whether `text` is a written form: 1 to MAX_KANJI_CHARS characters, at least one of them
    a CJK ideograph and the others kana, "ー", or "々"."""
    return _KANJI_FORM.fullmatch(text) is not None and _HAS_IDEOGRAPH.search(text) is not None


def is_meaning(text: str) -> bool:
    """Whether `text` is a meaning: 1 to MAX_MEANING_CHARS characters on one line."""
    return 0 < len(text) <= MAX_MEANING_CHARS and text.isprintable()


def same_word(kana: str, kanji: str | None, meaning: str, other: Word | CustomWord) -> bool:
    """Whether a word is `other`: the same reading, and the same written form; when either of
    them was given without one, the same meaning instead (the words of one, ignoring case, all
    among the other's), since words read alike may be others (はい "ya", 肺 "paru-paru")."""
    if kana != other.kana:
        return False
    if kanji is not None and other.kanji is not None:
        return kanji == other.kanji
    words, other_words = _meaning_words(meaning), _meaning_words(other.meaning)
    return bool(words and other_words) and (words <= other_words or other_words <= words)


def _meaning_words(meaning: str) -> set[str]:
    return set(_MEANING_WORD.findall(meaning.casefold()))


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


@dataclass(frozen=True)
class LessonCounts:
    learned: int
    mastered: int  # in the last box
    due: int  # due on the day counted
    total: int  # the words of the list


def lesson_counts(
    words: Sequence[Word], progress: Mapping[str, WordProgress], today: date
) -> LessonCounts:
    """How many words of `words` Master learned, mastered, and has due on `today`; words that
    are not in the list (a later list may drop some) are left out."""
    listed = {word.id for word in words}
    known = [word for word_id, word in progress.items() if word_id in listed]
    return LessonCounts(
        learned=len(known),
        mastered=sum(word.box == MASTERED_BOX for word in known),
        due=sum(word.due <= today for word in known),
        total=len(words),
    )


class JapaneseStore:
    """Master's words: which ones she introduced, and where each stands in the Leitner boxes."""

    def __init__(self, path: Path | str, *, now: Callable[[], datetime] = wib_now) -> None:
        """`now` (aware) only names a broken file moved aside; the days are passed in. A naive
        clock is a ValueError at every call."""
        self.path = Path(path)
        self._now = now

    def progress(self) -> dict[str, WordProgress]:
        """Every learned word by id, Master's own too; a missing file is nothing learned and
        stays missing."""
        now = self._clock()
        with _LOCK:
            return self._read(now)[0]

    def custom_words(self) -> dict[str, CustomWord]:
        """The words Master kept himself, by id in the order added."""
        now = self._clock()
        with _LOCK:
            return self._read(now)[1]

    def learn(self, word_id: str, today: date) -> bool:
        """Put a word not learned yet into box 1, due `today`; True when it was new. A word
        already learned changes nothing and nothing is written."""
        now = self._clock()
        with _LOCK:
            words, custom = self._read(now)
            if word_id in words:
                return False
            words[word_id] = WordProgress(1, today, today)
            self._write(words, custom)
            return True

    def add_custom(
        self, kana: str, kanji: str | None, meaning: str, today: date
    ) -> tuple[str, bool]:
        """Keep a word of Master's own in box 1, due `today`: (its id, whether it was new). A
        word he kept already (`same_word`) is that word, and nothing is written. ValueError for
        a word outside the rules (`is_kana_reading`, `is_kanji_form`, `is_meaning`); DeckFull
        once he kept MAX_CUSTOM_WORDS."""
        if not (
            is_kana_reading(kana)
            and (kanji is None or is_kanji_form(kanji))
            and is_meaning(meaning)
        ):
            raise ValueError("not a word: a kana reading, a kanji form or None, and a meaning")
        now = self._clock()
        with _LOCK:
            words, custom = self._read(now)
            for word_id, word in custom.items():
                if same_word(kana, kanji, meaning, word):
                    return word_id, False
            if len(custom) >= MAX_CUSTOM_WORDS:
                raise DeckFull(f"Master already kept {MAX_CUSTOM_WORDS} words")
            word_id = f"c{max((int(known[1:]) for known in custom), default=0) + 1}"
            custom[word_id] = CustomWord(kana, kanji, meaning, today)
            words[word_id] = WordProgress(1, today, today)
            self._write(words, custom)
            return word_id, True

    def grade(self, word_id: str, correct: bool, today: date) -> WordProgress | None:
        """Write the `review` of Master's answer on `today`; None, writing nothing, for a word
        never learned."""
        now = self._clock()
        with _LOCK:
            words, custom = self._read(now)
            if word_id not in words:
                return None
            words[word_id] = reviewed = review(words[word_id], correct, today)
            self._write(words, custom)
            return reviewed

    def _clock(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("the Japanese lesson store's clock must give aware times")
        return now

    def _read(self, now: datetime) -> tuple[dict[str, WordProgress], dict[str, CustomWord]]:
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse_store(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return {}, {}
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return {}, {}

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

    def _write(self, words: Mapping[str, WordProgress], custom: Mapping[str, CustomWord]) -> None:
        stored: dict[str, object] = {
            "version": STORE_VERSION,
            "words": {word_id: _dump(words[word_id]) for word_id in sorted(words)},
        }
        if custom:
            stored[_CUSTOM_KEY] = {word_id: _dump_custom(word) for word_id, word in custom.items()}
        write_json_atomic(self.path, stored)


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


def _dump_custom(word: CustomWord) -> dict[str, object]:
    return {
        "kana": word.kana,
        "kanji": word.kanji,
        "meaning": word.meaning,
        "added": word.added.isoformat(),
    }


def _parse_store(raw: object) -> tuple[dict[str, WordProgress], dict[str, CustomWord]]:
    """The words in `raw` and Master's own words; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() - {_CUSTOM_KEY} != _STORE_KEYS:
        raise ValueError(
            f"expected an object with exactly the keys {sorted(_STORE_KEYS)}, and maybe "
            f"{_CUSTOM_KEY!r}"
        )
    return _parse_progress(raw), _parse_custom(raw.get(_CUSTOM_KEY, {}))


def _parse_custom(entries: object) -> dict[str, CustomWord]:
    if not isinstance(entries, dict):
        raise ValueError(f"{_CUSTOM_KEY} must be an object of word ids")
    words: dict[str, CustomWord] = {}
    for word_id, entry in entries.items():
        at = f"{_CUSTOM_KEY}[{word_id!r}]"
        if _CUSTOM_ID.fullmatch(word_id) is None:
            raise ValueError(f"{at}: an added word's id must be c1, c2, ...")
        if not isinstance(entry, dict) or entry.keys() != _CUSTOM_WORD_KEYS:
            raise ValueError(
                f"{at} must be an object with exactly the keys {sorted(_CUSTOM_WORD_KEYS)}"
            )
        kana, kanji, meaning = entry["kana"], entry["kanji"], entry["meaning"]
        if not isinstance(kana, str) or not is_kana_reading(kana):
            raise ValueError(f"{at}.kana must be 1-{MAX_KANA_CHARS} kana")
        if kanji is not None and (not isinstance(kanji, str) or not is_kanji_form(kanji)):
            raise ValueError(f"{at}.kanji must be 1-{MAX_KANJI_CHARS} characters with a kanji")
        if not isinstance(meaning, str) or not is_meaning(meaning):
            raise ValueError(f"{at}.meaning must be 1-{MAX_MEANING_CHARS} characters on a line")
        words[word_id] = CustomWord(kana, kanji, meaning, _parse_day(entry["added"], f"{at}.added"))
    return words


def _parse_progress(raw: dict[str, object]) -> dict[str, WordProgress]:
    """The words in `raw`; ValueError says what is wrong."""
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
