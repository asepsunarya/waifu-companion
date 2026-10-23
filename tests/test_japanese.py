"""Belajar bahasa Jepang: the word list shipped in the package, the word of the day, and the
Leitner rules.

The list is read from the package itself; other lists live in tmp_path, so no test reads the
owner's data folder.
"""

import json
import re
import tomllib
from datetime import date, timedelta
from pathlib import Path

import pytest

import waifu
from waifu.japanese import (
    WORDS_FILE,
    Word,
    WordProgress,
    load_words,
    next_quiz,
    review,
    word_of_the_day,
)
from waifu.voice.tts import clean_for_speech, split_by_language

ROOT = Path(__file__).resolve().parents[1]
WORDS = load_words()
KANA = re.compile("[぀-ゟ゠-ヿ]+")
KANJI = re.compile("[㐀-䶿一-鿿]")
# Kana, kanji, and Japanese punctuation only, ending a sentence: no romaji, digits, or spaces.
SENTENCE = re.compile("[぀-ゟ゠-ヿ㐀-䶿一-鿿、。！？]+[。！？]")


# --- the shipped list -------------------------------------------------------------------------


def test_the_list_ships_as_package_data_outside_any_folder_named_data():
    # `.gitignore` ignores `data/` anywhere in the tree: a list under one would never be
    # committed.
    assert WORDS_FILE.parent == Path(waifu.__file__).parent
    assert WORDS_FILE.name == "japanese_words.json"
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "japanese_words.json" in pyproject["tool"]["setuptools"]["package-data"]["waifu"]


def test_the_list_holds_at_least_300_words_with_unique_ids():
    ids = [word.id for word in WORDS]

    assert len(WORDS) >= 300
    assert [i for i in ids if not re.fullmatch(r"w\d{3}", i)] == []
    assert len(set(ids)) == len(ids)


def test_no_word_and_no_example_comes_twice():
    assert len({(word.kana, word.kanji) for word in WORDS}) == len(WORDS)
    assert len({word.example for word in WORDS}) == len(WORDS)


def test_every_reading_is_kana_only():
    assert [word.id for word in WORDS if not KANA.fullmatch(word.kana)] == []


def test_every_kanji_form_holds_a_kanji_and_reaches_the_japanese_voice_whole():
    # A word usually written in kana has none; one with a form must differ from its reading.
    bad = [
        word.id
        for word in WORDS
        if word.kanji is not None
        and not (
            KANJI.search(word.kanji)
            and word.kanji != word.kana
            and split_by_language(word.kanji) == [("ja", word.kanji)]
        )
    ]

    assert bad == []


def test_every_example_is_one_japanese_sentence_for_the_japanese_voice():
    # 々 or a digit would be split off to the Indonesian voice; romaji would be read as
    # Indonesian.
    bad = [
        word.id
        for word in WORDS
        if not (
            SENTENCE.fullmatch(word.example)
            and split_by_language(word.example) == [("ja", word.example)]
        )
    ]

    assert bad == []


def test_every_meaning_is_indonesian_that_her_voice_says_whole():
    # Her voice drops anything in parentheses, so a meaning must hold none.
    bad = [
        (word.id, text)
        for word in WORDS
        for text in (word.meaning, word.example_meaning)
        if split_by_language(text) != [("id", text)] or clean_for_speech(text) != text
    ]

    assert bad == []


# --- reading a list ---------------------------------------------------------------------------

TABERU = {
    "id": "w001",
    "kana": "たべる",
    "kanji": "食べる",
    "meaning": "makan",
    "example": "ごはんを食べる。",
    "example_meaning": "Makan nasi.",
}


def word_list(*words: dict, **changes) -> dict:
    listed: dict[str, object] = {"version": 1, "words": list(words)}
    listed.update(changes)
    return listed


def test_a_list_is_read_into_words_in_its_order(tmp_path):
    path = tmp_path / "words.json"
    hai = {**TABERU, "id": "w002", "kana": "はい", "kanji": None, "meaning": "ya"}
    path.write_text(json.dumps(word_list(TABERU, hai), ensure_ascii=False), encoding="utf-8")

    assert load_words(path) == (
        Word("w001", "たべる", "食べる", "makan", "ごはんを食べる。", "Makan nasi."),
        Word("w002", "はい", None, "ya", "ごはんを食べる。", "Makan nasi."),
    )


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps([TABERU]),
        json.dumps(word_list(TABERU, version=2)),
        json.dumps(word_list(TABERU, extra=1)),
        json.dumps(word_list()),
        json.dumps(word_list(words={})),
        json.dumps(word_list([TABERU])),
        json.dumps(word_list({**TABERU, "extra": 1})),
        json.dumps(word_list({k: v for k, v in TABERU.items() if k != "example"})),
        json.dumps(word_list({**TABERU, "meaning": ""})),
        json.dumps(word_list({**TABERU, "meaning": 1})),
        json.dumps(word_list({**TABERU, "kanji": ""})),
        json.dumps(word_list({**TABERU, "kana": None})),
        json.dumps(word_list(TABERU, {**TABERU, "kana": "のむ"})),  # the same id twice
    ],
)
def test_a_file_that_is_not_a_word_list_is_refused(tmp_path, content):
    path = tmp_path / "words.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        load_words(path)


# --- the word of the day ----------------------------------------------------------------------

DAY = date(2026, 10, 24)


def days_from(start: date, count: int) -> list[date]:
    return [start + timedelta(days=n) for n in range(count)]


def test_the_word_of_the_day_is_the_same_all_day_and_another_the_next_day():
    assert word_of_the_day(WORDS, DAY) == word_of_the_day(WORDS, date(2026, 10, 24))
    assert word_of_the_day(WORDS, DAY + timedelta(days=1)) != word_of_the_day(WORDS, DAY)


def test_every_word_comes_once_before_any_word_comes_again():
    picked = [word_of_the_day(WORDS, day).id for day in days_from(DAY, 2 * len(WORDS))]

    assert sorted(picked[: len(WORDS)]) == sorted(word.id for word in WORDS)
    assert picked[len(WORDS) :] == picked[: len(WORDS)]


def test_the_days_do_not_walk_the_list_in_its_order():
    # The list is grouped by theme (greetings, the weekdays, the numbers): in its order a
    # beginner would get a week of weekdays in a row.
    position = {word.id: index for index, word in enumerate(WORDS)}
    picked = [position[word_of_the_day(WORDS, day).id] for day in days_from(DAY, 7)]

    steps = [after - before for before, after in zip(picked, picked[1:], strict=False)]
    assert steps != [1] * 6


def test_a_short_list_comes_round_in_as_many_days_as_it_has_words():
    three = WORDS[:3]

    picked = [word_of_the_day(three, day) for day in days_from(DAY, 6)]

    assert set(picked[:3]) == set(three)
    assert picked[3:] == picked[:3]


# --- the Leitner rules ------------------------------------------------------------------------

LEARNED = date(2026, 10, 20)


@pytest.mark.parametrize(
    ("box", "next_box", "days"), [(1, 2, 2), (2, 3, 4), (3, 4, 8), (4, 5, 16), (5, 5, 16)]
)
def test_a_right_answer_moves_the_word_up_a_box_and_waits_longer(box, next_box, days):
    reviewed = review(WordProgress(box, DAY, LEARNED), True, DAY)

    assert reviewed == WordProgress(next_box, DAY + timedelta(days=days), LEARNED)


@pytest.mark.parametrize("box", [1, 3, 5])
def test_a_wrong_answer_sends_the_word_back_to_box_one_for_tomorrow(box):
    reviewed = review(WordProgress(box, DAY - timedelta(days=3), LEARNED), False, DAY)

    assert reviewed == WordProgress(1, DAY + timedelta(days=1), LEARNED)


def test_the_quiz_asks_the_due_word_in_the_lowest_box_then_the_one_due_longest():
    progress = {
        "w003": WordProgress(2, DAY - timedelta(days=1), LEARNED),
        "w001": WordProgress(3, DAY - timedelta(days=5), LEARNED),
        "w002": WordProgress(2, DAY - timedelta(days=3), LEARNED),
        "w004": WordProgress(1, DAY + timedelta(days=1), LEARNED),  # not due yet
    }

    assert next_quiz(progress, DAY) == "w002"


def test_due_words_alike_in_box_and_day_go_by_their_id():
    progress = {
        "w009": WordProgress(1, DAY, LEARNED),
        "w005": WordProgress(1, DAY, LEARNED),
    }

    assert next_quiz(progress, DAY) == "w005"


def test_there_is_no_quiz_when_no_word_is_due():
    assert next_quiz({"w001": WordProgress(1, DAY + timedelta(days=1), LEARNED)}, DAY) is None
    assert next_quiz({}, DAY) is None
