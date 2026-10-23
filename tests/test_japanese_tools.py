"""The japanese_lesson tool: what it does with the shipped word list and a real store in
tmp_path, at a WIB clock moved by the test, and when it refuses."""

import json
import logging
from datetime import date, datetime, timedelta

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.japanese_tools import JapaneseTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.briefing import BRIEFING_PROMPT
from waifu.finance.values import WIB
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.japanese import JapaneseStore, WordProgress, load_words, word_of_the_day
from waifu.journal import JOURNAL_PROMPT

WORDS = load_words()
NOW = datetime(2026, 10, 24, 20, 0, tzinfo=WIB)  # Saturday 20:00
TODAY = NOW.date()
FIRST, SECOND, THIRD = (word.id for word in WORDS[:3])


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Rig:
    """The tool over the shipped words, a store in tmp_path, and one clock."""

    def __init__(self, tmp_path) -> None:
        self.clock = Clock(NOW)
        self.store = JapaneseStore(tmp_path / "japanese.json", now=self.clock)
        self.tools = JapaneseTools(WORDS, self.store, now=self.clock)

    def on(self, day: date) -> None:
        self.clock.now = NOW.replace(year=day.year, month=day.month, day=day.day)

    def ask(self, arguments=None) -> dict:
        result, is_error = self.tools.run("japanese_lesson", {} if arguments is None else arguments)
        assert is_error is False, result
        return json.loads(result)

    def failed(self, arguments) -> str:
        result, is_error = self.tools.run("japanese_lesson", arguments)
        assert is_error is True
        data = json.loads(result)
        assert data["ok"] is False
        return data["message"]


@pytest.fixture
def rig(tmp_path) -> Rig:
    return Rig(tmp_path)


def days(count: int) -> timedelta:
    return timedelta(days=count)


# --- the spec -----------------------------------------------------------------------------------


def test_one_tool_whose_arguments_may_all_be_left_out_so_it_needs_no_strict(rig):
    [spec] = rig.tools.specs()

    assert spec.name == "japanese_lesson"
    assert spec.input_schema == {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["today", "quiz", "grade", "progress"]},
            "word_id": {"type": "string"},
            "correct": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "japanese_lesson"


def test_the_description_says_how_to_teach_and_how_to_say_it(rig):
    [spec] = rig.tools.specs()

    for guidance in [
        "beginner words (JLPT N5)",
        "`today`, the default",
        "the same all day",
        "counts the word as learned",
        "`quiz` null means none is due",
        "`next_due_in_days`",
        "which you judge yourself from Master's answer against the meaning",
        "a synonym or a close meaning counts as right",
        "`mastered`",
        "one word at a time",
        "say the word in kana and ask what it means",
        "never saying the meaning first",
        "wait for Master's answer",
        "praise him warmly",
        "gently tell the meaning",
        "Never call grade without Master's own answer",
        "never quiz or grade in a greeting or anything you start yourself",
        "never romaji",
        "no parentheses",
        "no grammar lecture",
        "do not call again in this turn",
    ]:
        assert guidance in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching(rig):
    before = rig.tools.specs()

    rig.ask({"action": "today"})
    rig.ask({"action": "quiz"})

    assert rig.tools.specs() == before


# --- today ------------------------------------------------------------------------------------


def test_today_by_default_gives_today_s_word_whole_and_learns_it(rig):
    word = word_of_the_day(WORDS, TODAY)

    assert rig.ask() == {
        "word": {
            "word_id": word.id,
            "kana": word.kana,
            "kanji": word.kanji,
            "meaning": word.meaning,
            "example": word.example,
            "example_meaning": word.example_meaning,
        },
        "new": True,
    }
    assert rig.store.progress() == {word.id: WordProgress(1, TODAY, TODAY)}


def test_today_again_the_same_day_is_the_same_word_and_not_new(rig):
    first = rig.ask({"action": "today"})

    again = rig.ask({"action": "today"})

    assert again["word"] == first["word"]
    assert again["new"] is False


def test_the_next_day_brings_another_word(rig):
    first = rig.ask()
    rig.on(TODAY + days(1))

    second = rig.ask()

    assert second["word"]["word_id"] != first["word"]["word_id"]
    assert second["new"] is True
    assert len(rig.store.progress()) == 2


# --- quiz -------------------------------------------------------------------------------------


def test_a_quiz_with_nothing_learned_has_no_word(rig):
    assert rig.ask({"action": "quiz"}) == {"quiz": None, "learned": 0, "next_due_in_days": None}


def test_today_s_word_can_be_quizzed_the_same_day(rig):
    word = word_of_the_day(WORDS, TODAY)
    rig.ask()

    assert rig.ask({"action": "quiz"}) == {
        "quiz": {
            "word_id": word.id,
            "kana": word.kana,
            "kanji": word.kanji,
            "meaning": word.meaning,
        },
        "due_today": 1,
    }


def test_the_quiz_asks_the_due_word_in_the_lowest_box_first(rig):
    for word_id in (FIRST, SECOND, THIRD):
        rig.store.learn(word_id, TODAY - days(4))
    rig.store.grade(FIRST, True, TODAY - days(4))  # box 2, due two days ago
    rig.store.grade(THIRD, True, TODAY - days(2))  # box 2, due today

    quiz = rig.ask({"action": "quiz"})

    assert quiz["quiz"]["word_id"] == SECOND  # still in box 1
    assert quiz["due_today"] == 3


def test_a_quiz_with_none_due_tells_in_how_many_days_the_next_one_is(rig):
    rig.store.learn(FIRST, TODAY)
    rig.store.grade(FIRST, True, TODAY)  # box 2, due in two days

    assert rig.ask({"action": "quiz"}) == {"quiz": None, "learned": 1, "next_due_in_days": 2}


# --- grade ------------------------------------------------------------------------------------


def test_a_right_answer_moves_the_word_up_and_tells_what_is_left(rig):
    rig.store.learn(FIRST, TODAY)
    rig.store.learn(SECOND, TODAY)

    assert rig.ask({"action": "grade", "word_id": FIRST, "correct": True}) == {
        "word_id": FIRST,
        "correct": True,
        "box": 2,
        "mastered": False,
        "next_due_in_days": 2,
        "due_today": 1,
    }
    assert rig.store.progress()[FIRST] == WordProgress(2, TODAY + days(2), TODAY)


def test_a_wrong_answer_sends_the_word_back_for_tomorrow(rig):
    rig.store.learn(FIRST, TODAY - days(10))
    for answered in (TODAY - days(10), TODAY - days(8)):
        rig.store.grade(FIRST, True, answered)  # box 3

    graded = rig.ask({"action": "grade", "word_id": FIRST, "correct": False})

    assert (graded["box"], graded["next_due_in_days"], graded["due_today"]) == (1, 1, 0)


def test_a_word_right_in_the_last_box_is_mastered(rig):
    rig.store.learn(FIRST, TODAY - days(30))
    for _ in range(3):
        rig.store.grade(FIRST, True, TODAY - days(30))  # box 4

    graded = rig.ask({"action": "grade", "word_id": FIRST, "correct": True})

    assert (graded["box"], graded["mastered"], graded["next_due_in_days"]) == (5, True, 16)


def test_a_word_not_in_the_list_cannot_be_graded(rig):
    assert rig.failed({"action": "grade", "word_id": "w999", "correct": True}) == (
        "Kata 'w999' tidak ada di daftar kata."
    )


def test_a_word_never_learned_cannot_be_graded_and_nothing_is_written(rig):
    message = rig.failed({"action": "grade", "word_id": FIRST, "correct": True})

    assert message == "Kata itu belum dipelajari, jadi belum bisa dinilai."
    assert not rig.store.path.exists()


@pytest.mark.parametrize("first_answer", [True, False])
def test_a_word_graded_today_is_not_graded_again_and_stays_where_it_is(rig, first_answer):
    # A turn retried after an API error grades Master's repeated answer again: it must not
    # move the word up a second box.
    rig.store.learn(FIRST, TODAY)
    rig.ask({"action": "grade", "word_id": FIRST, "correct": first_answer})
    graded = rig.store.progress()

    message = rig.failed({"action": "grade", "word_id": FIRST, "correct": True})

    assert message == "Kata itu belum waktunya diulang, jadi tidak dinilai lagi."
    assert rig.store.progress() == graded


def test_a_word_not_due_yet_is_graded_again_once_it_is_due(rig):
    rig.store.learn(FIRST, TODAY)
    rig.ask({"action": "grade", "word_id": FIRST, "correct": False})  # box 1, due tomorrow
    rig.on(TODAY + days(1))

    graded = rig.ask({"action": "grade", "word_id": FIRST, "correct": True})

    assert (graded["box"], graded["next_due_in_days"]) == (2, 2)


# --- progress ---------------------------------------------------------------------------------


def test_progress_counts_the_learned_the_mastered_and_the_due_words(rig):
    for word_id in (FIRST, SECOND, THIRD):
        rig.store.learn(word_id, TODAY - days(40))
    for _ in range(4):
        rig.store.grade(FIRST, True, TODAY - days(20))  # box 5, due long ago
    rig.store.grade(SECOND, True, TODAY)  # box 2, due in two days

    assert rig.ask({"action": "progress"}) == {
        "learned": 3,
        "mastered": 1,
        "due_today": 2,
        "total": len(WORDS),
    }


def test_words_in_the_file_but_not_in_the_list_are_left_out(rig):
    rig.store.learn("w999", TODAY)  # a word of another list

    assert rig.ask({"action": "progress"})["learned"] == 0
    assert rig.ask({"action": "quiz"})["quiz"] is None


# --- arguments and failures -------------------------------------------------------------------


def test_a_null_argument_counts_as_left_out(rig):
    answer = rig.ask({"action": None, "word_id": None, "correct": None})

    assert answer["word"]["word_id"] == word_of_the_day(WORDS, TODAY).id


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ([], "objek JSON"),
        ({"action": "lesson"}, "'action' harus salah satu dari: today, quiz, grade, progress"),
        ({"action": "grade", "correct": True}, "wajib untuk action grade"),
        ({"action": "grade", "word_id": "w001"}, "wajib untuk action grade"),
        ({"action": "today", "word_id": "w001"}, "hanya untuk action grade"),
        ({"action": "progress", "correct": True}, "hanya untuk action grade"),
        ({"word_id": "w001", "correct": True}, "hanya untuk action grade"),
        ({"action": "grade", "word_id": "w001", "correct": "yes"}, "'correct' harus true"),
        ({"action": "grade", "word_id": "w001", "correct": 1}, "'correct' harus true"),
        ({"action": "grade", "word_id": 1, "correct": True}, "'word_id' harus teks"),
        ({"action": "grade", "word_id": "", "correct": True}, "'word_id' harus teks"),
        ({"answer": "makan"}, "argumen tidak dikenal: answer"),
    ],
)
def test_bad_arguments_are_an_error_result(rig, arguments, reason):
    message = rig.failed(arguments)

    assert message.startswith("Argumen japanese_lesson tidak valid:")
    assert reason in message
    assert not rig.store.path.exists()


def test_a_store_that_cannot_be_read_is_an_error_result_and_logged(rig, caplog):
    rig.store.path.mkdir()  # a folder where the file should be

    with caplog.at_level(logging.ERROR, logger="waifu.brain.japanese_tools"):
        message = rig.failed({"action": "progress"})

    assert message == "Catatan belajar bahasa Jepang tidak bisa dibaca atau disimpan saat ini."
    assert "Japanese tool" in caplog.text


def test_another_tool_name_is_an_error_result(rig):
    result, is_error = rig.tools.run("focus_timer", {})

    assert is_error is True
    assert "bukan tool belajar bahasa Jepang" in json.loads(result)["message"]


# --- her own turns ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        (
            BRIEFING_PROMPT,
            "Kuis bahasa Jepang hanya untuk permintaan Master, bukan untuk sapaan pagi.",
        ),
        (
            WEEKLY_REPORT_PROMPT,
            "Kuis bahasa Jepang hanya untuk permintaan Master, bukan untuk laporan game mingguan.",
        ),
        (
            JOURNAL_PROMPT,
            "Kuis bahasa Jepang hanya untuk permintaan Master, bukan untuk pertanyaan jurnal "
            "malam.",
        ),
    ],
    ids=["briefing", "weekly_report", "journal_question"],
)
def test_her_own_turns_get_no_quiz_and_no_grade_and_master_s_next_turn_does(rig, prompt, message):
    rig.store.learn(FIRST, TODAY)
    rig.tools.begin_turn(prompt)

    assert rig.failed({"action": "quiz"}) == message
    assert rig.failed({"action": "grade", "word_id": FIRST, "correct": True}) == message
    assert rig.store.progress()[FIRST] == WordProgress(1, TODAY, TODAY)
    rig.tools.begin_turn("kuis bahasa Jepang dong")

    assert rig.ask({"action": "quiz"})["quiz"]["word_id"] == FIRST


def test_her_own_turns_may_still_name_today_s_word_and_the_progress(rig):
    rig.tools.begin_turn(BRIEFING_PROMPT)

    assert rig.ask({"action": "today"})["new"] is True
    assert rig.ask({"action": "progress"})["learned"] == 1
