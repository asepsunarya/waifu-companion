"""The tool that teaches Master Japanese a word at a time: `japanese_lesson`.

"Kata Jepang hari ini apa?", "kuis bahasa Jepang dong", "aku sudah belajar berapa kata?": the
tool gives today's word from the built-in list (`japanese.py`) and counts it as learned, picks
the next learned word due for review, records Master's answer, which the model judges against
the meaning, and tells the progress. Its arguments may all be left out (they are checked here),
so it takes no strict slot. The spec is static English built once, so it stays byte-identical
for the brain's lifetime and keeps the prompt cache warm. In the turns she starts herself (the
morning greeting, the weekly game report, and the nightly journal question) it refuses a quiz
and a grade: a grade comes only from Master's own answer. Guest mode leaves it open (beginner
words are nothing private). `run` never raises.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime

from waifu.brain.base import ToolSpec
from waifu.briefing import BRIEFING_PROMPT
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.japanese import (
    MASTERED_BOX,
    JapaneseStore,
    Word,
    WordProgress,
    learn_word_of_the_day,
    lesson_counts,
    next_quiz,
)
from waifu.journal import JOURNAL_PROMPT
from waifu.play_time import wib_now

logger = logging.getLogger(__name__)

JAPANESE_LESSON = "japanese_lesson"
ACTIONS = ("today", "quiz", "grade", "progress")
_KEYS = frozenset({"action", "word_id", "correct"})
# The turns she starts herself, each with what the tool answers in it.
_OWN_TURN_REFUSALS = {
    BRIEFING_PROMPT: "Kuis bahasa Jepang hanya untuk permintaan Master, bukan untuk sapaan pagi.",
    WEEKLY_REPORT_PROMPT: (
        "Kuis bahasa Jepang hanya untuk permintaan Master, bukan untuk laporan game mingguan."
    ),
    JOURNAL_PROMPT: (
        "Kuis bahasa Jepang hanya untuk permintaan Master, bukan untuk pertanyaan jurnal malam."
    ),
}
_FAILED = "Catatan belajar bahasa Jepang tidak bisa dibaca atau disimpan saat ini."
_NOT_LEARNED = "Kata itu belum dipelajari, jadi belum bisa dinilai."
_NOT_DUE = "Kata itu belum waktunya diulang, jadi tidak dinilai lagi."
_GRADE_NEEDS_BOTH = "'word_id' dan 'correct' wajib untuk action grade."
_ONLY_TO_GRADE = "'word_id' dan 'correct' hanya untuk action grade."


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class _LessonError(Exception):
    """A lesson that cannot be given; the message is Indonesian and safe to say."""


class JapaneseTools:
    """A `ToolProvider` for the brain: today's Japanese word, the quiz, and Master's progress."""

    def __init__(
        self,
        words: Sequence[Word],
        store: JapaneseStore,
        *,
        now: Callable[[], datetime] = wib_now,
    ) -> None:
        """`now` is aware WIB time; its date is the lesson's day."""
        self._words = tuple(words)
        self._by_id = {word.id: word for word in self._words}
        self._store = store
        self._now = now
        self._spec = _spec()
        self._refusal: str | None = None

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with its words as heard: a turn she starts herself, the morning
        greeting, the weekly game report, or the nightly journal question, gets no quiz and no
        grade."""
        self._refusal = _OWN_TURN_REFUSALS.get(user_text)

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        if name != JAPANESE_LESSON:
            return _error(f"Tool '{name}' bukan tool belajar bahasa Jepang.")
        try:
            return _to_json(self._lesson(arguments)), False
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except _LessonError as exc:
            return _error(str(exc))
        except Exception:
            logger.exception("Japanese tool '%s' failed", name)
            return _error(_FAILED)

    def _lesson(self, arguments: object) -> dict[str, object]:
        given = _arguments_mapping(arguments)
        action = _choice(given, "action", ACTIONS) or "today"
        word_id, correct = _word_id(given), _correct(given)
        if action == "grade":
            if word_id is None or correct is None:
                raise _InvalidArguments(_GRADE_NEEDS_BOTH)
            self._refuse_in_her_own_turns()
            return self._grade(word_id, correct, self._now().date())
        if word_id is not None or correct is not None:
            raise _InvalidArguments(_ONLY_TO_GRADE)
        today = self._now().date()
        if action == "quiz":
            self._refuse_in_her_own_turns()
            return self._quiz(today)
        if action == "progress":
            return self._progress(today)
        return self._today(today)

    def _refuse_in_her_own_turns(self) -> None:
        """A quiz and a grade belong to Master's own turns."""
        if self._refusal is not None:
            raise _LessonError(self._refusal)

    def _today(self, today: date) -> dict[str, object]:
        word, new = learn_word_of_the_day(self._words, self._store, today)
        return {"word": _word_json(word), "new": new}

    def _quiz(self, today: date) -> dict[str, object]:
        progress = self._learned()
        word_id = next_quiz(progress, today)
        if word_id is None:
            upcoming = min((word.due for word in progress.values()), default=None)
            return {
                "quiz": None,
                "learned": len(progress),
                "next_due_in_days": None if upcoming is None else (upcoming - today).days,
            }
        word = self._by_id[word_id]
        return {
            "quiz": {
                "word_id": word.id,
                "kana": word.kana,
                "kanji": word.kanji,
                "meaning": word.meaning,
            },
            "due_today": _due_count(progress, today),
        }

    def _grade(self, word_id: str, correct: bool, today: date) -> dict[str, object]:
        if word_id not in self._by_id:
            raise _LessonError(f"Kata '{word_id}' tidak ada di daftar kata.")
        known = self._store.progress().get(word_id)
        if known is not None and known.due > today:
            # Every grade moves `due` past today, so this refuses only a repeat (a turn retried
            # after an API error) or a word the quiz would not offer yet.
            raise _LessonError(_NOT_DUE)
        reviewed = self._store.grade(word_id, correct, today)
        if reviewed is None:
            raise _LessonError(_NOT_LEARNED)
        return {
            "word_id": word_id,
            "correct": correct,
            "box": reviewed.box,
            "mastered": reviewed.box == MASTERED_BOX,
            "next_due_in_days": (reviewed.due - today).days,
            "due_today": _due_count(self._learned(), today),
        }

    def _progress(self, today: date) -> dict[str, object]:
        counts = lesson_counts(self._words, self._store.progress(), today)
        return {
            "learned": counts.learned,
            "mastered": counts.mastered,
            "due_today": counts.due,
            "total": counts.total,
        }

    def _learned(self) -> dict[str, WordProgress]:
        """Master's words that are in this list (a later list may have dropped some)."""
        return {
            word_id: word
            for word_id, word in self._store.progress().items()
            if word_id in self._by_id
        }


def _spec() -> ToolSpec:
    """Built once, so every request sends the same bytes."""
    return ToolSpec(
        name=JAPANESE_LESSON,
        description=(
            "Master's Japanese lessons from a built-in list of beginner words (JLPT N5), one "
            "word a day. `today`, the default, gives today's word, the same all day: `kana`, "
            "`kanji` (its usual written form, or null), the Indonesian `meaning`, and an "
            "`example` sentence with its `example_meaning`; it counts the word as learned "
            "(`new` true the first time). `quiz` gives the next learned word due for review, "
            "with its meaning, and `due_today`, how many are due; `quiz` null means none is due "
            "now, and `next_due_in_days` says when the next one is (null while nothing is "
            "learned). `grade` records Master's answer to a quiz word: give its `word_id` and "
            "`correct`, which you judge yourself from Master's answer against the meaning (a "
            "synonym or a close meaning counts as right; Master hears only the kana, so when he "
            "gives the meaning of a common word that sounds the same, count it as right or give "
            "a hint and ask again); it tells the word's new `box` (1 to 5), whether it is "
            "`mastered`, and `due_today`, how many are left. `progress` tells "
            "how many words are `learned`, how many `mastered`, how many are due today, and the "
            "`total` in the list. In a quiz ask one word at a time: say the word in kana and "
            "ask what it means, never saying the meaning first; wait for Master's answer, then "
            "call grade, praise him warmly when he is right or gently tell the meaning when he "
            "is wrong, and ask the next due word if he wants to go on. Never call grade without "
            "Master's own answer, and never quiz or grade in a greeting or anything you start "
            "yourself. Write Japanese only in kana or kanji, never romaji, and use no "
            "parentheses, because your voice drops whatever is in parentheses: say the word "
            "once, then its Indonesian meaning. Keep it short and warm, with no grammar "
            "lecture. After an error result, say its message briefly and do not call again in "
            "this turn."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "word_id": {"type": "string"},
                "correct": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    )


def _word_json(word: Word) -> dict[str, object]:
    return {
        "word_id": word.id,
        "kana": word.kana,
        "kanji": word.kanji,
        "meaning": word.meaning,
        "example": word.example,
        "example_meaning": word.example_meaning,
    }


def _due_count(progress: Mapping[str, WordProgress], today: date) -> int:
    return sum(word.due <= today for word in progress.values())


def _choice(arguments: Mapping[str, object], key: str, allowed: Sequence[str]) -> str | None:
    """The argument `key` when given (null counts as left out, as local models send it)."""
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise _InvalidArguments(f"'{key}' harus salah satu dari: {', '.join(allowed)}.")
    return value


def _word_id(arguments: Mapping[str, object]) -> str | None:
    value = arguments.get("word_id")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise _InvalidArguments("'word_id' harus teks, misalnya w001.")
    return value


def _correct(arguments: Mapping[str, object]) -> bool | None:
    value = arguments.get("correct")
    if value is None:
        return None
    if not isinstance(value, bool):
        raise _InvalidArguments("'correct' harus true atau false.")
    return value


def _arguments_mapping(arguments: object) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in _KEYS]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
