"""Belajar bahasa Jepang: the word list shipped in the package.

The list is read from the package itself; other lists live in tmp_path, so no test reads the
owner's data folder.
"""

import json
import re
import tomllib
from pathlib import Path

import pytest

import waifu
from waifu.japanese import WORDS_FILE, Word, load_words
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
