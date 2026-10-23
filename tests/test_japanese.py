"""Belajar bahasa Jepang: the word list shipped in the package, the word of the day, the
Leitner rules, and the store of Master's words.

The list is read from the package itself; other lists and every store live in tmp_path, and
clocks are fake, so no test reads the owner's data folder.
"""

import codecs
import json
import logging
import os
import re
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

import waifu
from waifu.finance.values import WIB
from waifu.japanese import (
    WORDS_FILE,
    JapaneseStore,
    LessonCounts,
    Word,
    WordProgress,
    learn_word_of_the_day,
    lesson_counts,
    load_words,
    next_quiz,
    review,
    word_of_the_day,
)
from waifu.voice.tts import clean_for_speech, split_by_language

ROOT = Path(__file__).resolve().parents[1]
WORDS = load_words()
KANA = re.compile("[\u3040-\u309f\u30a0-\u30ff]+")
KANJI = re.compile("[\u3400-\u4dbf\u4e00-\u9fff]")
# Kana, kanji, and Japanese punctuation only, ending a sentence: no romaji, digits, or spaces.
SENTENCE = re.compile("[\u3040-\u309f\u30a0-\u30ff\u3400-\u4dbf\u4e00-\u9fff、。！？]+[。！？]")


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


def test_no_example_holds_a_form_the_japanese_voice_reads_as_another_word():
    # A lone 十分 is じゅうぶん ("enough") to a speech engine, not じゅっぷん ("ten minutes"),
    # and "歩いて十分です" makes sense either way; 三十分 has one reading.
    misread = re.compile("(?<![一二三四五六七八九])十分")

    assert [word.id for word in WORDS if misread.search(word.example)] == []


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


def test_the_counts_tell_the_learned_the_mastered_and_the_due_words_of_the_list():
    progress = {
        WORDS[0].id: WordProgress(5, DAY - timedelta(days=1), LEARNED),
        WORDS[1].id: WordProgress(2, DAY + timedelta(days=1), LEARNED),
        WORDS[2].id: WordProgress(1, DAY, LEARNED),
        "w999": WordProgress(1, DAY, LEARNED),  # a word of another list is left out
    }

    assert lesson_counts(WORDS, progress, DAY) == LessonCounts(
        learned=3, mastered=1, due=2, total=len(WORDS)
    )


# --- the store --------------------------------------------------------------------------------

NOON = datetime(2026, 10, 24, 12, 0, tzinfo=WIB)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_store(path: Path, clock: Clock | None = None) -> JapaneseStore:
    return JapaneseStore(path, now=clock or Clock(NOON))


def stored_state(path: Path) -> tuple[bytes, int]:
    """The file's content and when it was last replaced."""
    return path.read_bytes(), path.stat().st_mtime_ns


def test_a_missing_file_is_nothing_learned_and_stays_missing(tmp_path):
    path = tmp_path / "data" / "japanese.json"

    assert make_store(path).progress() == {}
    assert not path.exists()


def test_learning_a_word_puts_it_in_box_one_due_the_same_day(tmp_path):
    path = tmp_path / "data" / "japanese.json"
    store = make_store(path)

    assert store.learn("w001", DAY) is True

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "words": {"w001": {"box": 1, "due": "2026-10-24", "learned": "2026-10-24"}},
    }
    assert store.progress() == {"w001": WordProgress(1, DAY, DAY)}


def test_learning_a_word_already_learned_changes_and_writes_nothing(tmp_path):
    path = tmp_path / "japanese.json"
    store = make_store(path)
    store.learn("w001", DAY)
    before = stored_state(path)

    assert store.learn("w001", DAY + timedelta(days=3)) is False

    assert stored_state(path) == before
    assert store.progress() == {"w001": WordProgress(1, DAY, DAY)}


def test_words_are_written_by_their_id(tmp_path):
    path = tmp_path / "japanese.json"
    store = make_store(path)

    store.learn("w010", DAY)
    store.learn("w002", DAY)

    assert list(json.loads(path.read_text(encoding="utf-8"))["words"]) == ["w002", "w010"]


def test_grading_writes_the_review(tmp_path):
    store = make_store(tmp_path / "japanese.json")
    store.learn("w001", DAY)

    assert store.grade("w001", True, DAY) == WordProgress(2, DAY + timedelta(days=2), DAY)
    assert store.progress() == {"w001": WordProgress(2, DAY + timedelta(days=2), DAY)}

    later = DAY + timedelta(days=2)
    assert store.grade("w001", False, later) == WordProgress(1, later + timedelta(days=1), DAY)


def test_grading_a_word_never_learned_is_none_and_writes_nothing(tmp_path):
    path = tmp_path / "japanese.json"

    assert make_store(path).grade("w001", True, DAY) is None
    assert not path.exists()


def test_every_call_reads_the_file_again(tmp_path):
    path = tmp_path / "japanese.json"
    writer, reader = make_store(path), make_store(path)
    assert reader.progress() == {}

    writer.learn("w001", DAY)

    assert reader.progress() == {"w001": WordProgress(1, DAY, DAY)}


def test_a_file_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "japanese.json"
    make_store(path).learn("w001", DAY)
    path.write_bytes(codecs.BOM_UTF8 + path.read_bytes())

    assert make_store(path).progress() == {"w001": WordProgress(1, DAY, DAY)}


PROGRESS = {"box": 2, "due": "2026-10-26", "learned": "2026-10-24"}


def progress_file(**changes) -> dict:
    stored: dict[str, object] = {"version": 1, "words": {"w001": PROGRESS}}
    stored.update(changes)
    return stored


def one_word(**changes) -> str:
    return json.dumps(progress_file(words={"w001": {**PROGRESS, **changes}}))


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps([]),
        json.dumps(progress_file(version=2)),
        json.dumps(progress_file(version=True)),
        json.dumps(progress_file(extra=1)),
        json.dumps({"version": 1}),
        json.dumps(progress_file(words=[PROGRESS])),
        json.dumps(progress_file(words={"": PROGRESS})),
        json.dumps(progress_file(words={"w001": [2, "2026-10-26", "2026-10-24"]})),
        json.dumps(progress_file(words={"w001": {"box": 2, "due": "2026-10-26"}})),
        one_word(extra=1),
        one_word(box=0),
        one_word(box=6),
        one_word(box=True),
        one_word(box="2"),
        one_word(due="26-10-2026"),
        one_word(due="20261026"),
        one_word(due="2026-10-26T08:00"),
        one_word(due="2026-02-30"),
        one_word(learned=1),
        one_word(due="2026-10-23"),  # due before it was learned
    ],
)
def test_a_file_that_is_not_a_japanese_lesson_file_is_moved_aside_and_never_overwritten(
    tmp_path, content, caplog
):
    path = tmp_path / "japanese.json"
    path.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="waifu.japanese"):
        assert make_store(path).progress() == {}

    [moved] = tmp_path.glob("japanese.json.corrupt-*")
    assert moved.name == "japanese.json.corrupt-20261024-120000"
    assert moved.read_text(encoding="utf-8") == content
    assert not path.exists()
    assert "not a Japanese lesson file" in caplog.text


def test_a_broken_file_moved_aside_twice_in_one_second_keeps_both(tmp_path):
    path = tmp_path / "japanese.json"
    store = make_store(path)
    for content in ("first", "second"):
        path.write_text(content, encoding="utf-8")
        store.progress()

    moved = sorted(copy.name for copy in tmp_path.glob("japanese.json.corrupt-*"))
    assert moved == [
        "japanese.json.corrupt-20261024-120000",
        "japanese.json.corrupt-20261024-120000-2",
    ]


def test_learning_over_a_broken_file_moves_it_aside_first(tmp_path):
    path = tmp_path / "japanese.json"
    path.write_text("broken", encoding="utf-8")

    assert make_store(path).learn("w001", DAY) is True

    assert make_store(path).progress() == {"w001": WordProgress(1, DAY, DAY)}
    [moved] = tmp_path.glob("japanese.json.corrupt-*")
    assert moved.read_text(encoding="utf-8") == "broken"


def test_a_broken_file_that_cannot_be_moved_aside_raises_and_stays(tmp_path, monkeypatch):
    path = tmp_path / "japanese.json"
    path.write_text("broken", encoding="utf-8")

    def refuse(source, target):
        raise PermissionError("the file is open in another program")

    monkeypatch.setattr(os, "rename", refuse)

    with pytest.raises(OSError):
        make_store(path).learn("w001", DAY)
    assert path.read_text(encoding="utf-8") == "broken"


def test_the_word_of_the_day_is_learned_once_on_its_day(tmp_path):
    store = make_store(tmp_path / "japanese.json")
    word = word_of_the_day(WORDS, DAY)

    assert learn_word_of_the_day(WORDS, store, DAY) == (word, True)
    assert learn_word_of_the_day(WORDS, store, DAY) == (word, False)
    assert store.progress() == {word.id: WordProgress(1, DAY, DAY)}


def test_a_naive_clock_is_refused(tmp_path):
    store = make_store(tmp_path / "japanese.json", Clock(datetime(2026, 10, 24, 12)))

    with pytest.raises(ValueError, match="aware"):
        store.progress()
