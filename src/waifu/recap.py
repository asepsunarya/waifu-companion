"""Rekap hari ini: what Master's day held, gathered in code from her local records only.

"Hari ini aku ngapain aja?", "rekap kemarin dong": `DailyRecap.gather` reads, for one WIB day
(from its midnight to the next, or to now for today), the tasks done that day and the open ones
due by it, the time Master focused, how long each tracked game was open, the day's pulls in the
stored pull history, the Japanese words learned and reviewed, how many journal notes the day
holds (never their text), and the reminders and routines due that day. Every source is a local
file read as it is: nothing reaches the network (the pull history is never refreshed here), and
nothing is written. A source whose feature is off is None and its part absent; each part is read
on its own, so one that fails carries a short Indonesian problem and the rest is still told.

The scheduler keeps no record of what it said, so the schedule part is what was due that day by
`occurrence_on`; a one-shot reminder that already fired has left the schedule. The to-do list and
the schedule keep the PC's local time, read here as WIB (the same on the owner's PC).

`recap_json` is what the brain gets; `waifu rekap` prints the same `Recap`. Money is never read
here, and nothing is logged at INFO: the facts are Master's own.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import partial
from typing import Any, Literal, Protocol, TypeVar

from waifu.brain.persona import WEEKDAYS_ID
from waifu.config import GameId
from waifu.finance.values import WIB
from waifu.gacha.games import GAMES, Game
from waifu.gacha.history import BannerPulls, StoredHistory, pulls_since
from waifu.japanese import Word, WordProgress
from waifu.journal import Note
from waifu.play_time import PlayRecord, play_totals, played_seconds, whole_minutes, wib_now
from waifu.schedule import DeviceAction, Entry, Reminder, occurrence_on
from waifu.todo import Task

T = TypeVar("T")

logger = logging.getLogger(__name__)

PARTS = ("tasks", "focus", "play_time", "gacha", "japanese", "journal", "schedule")
DAYS_BACK = 30
LIST_MAX = 10
_ONE_DAY = timedelta(days=1)
_PROBLEMS = {
    "tasks": "Daftar tugas tidak bisa dibaca saat ini.",
    "focus": "Catatan fokus tidak bisa dibaca saat ini.",
    "play_time": "Jam main tidak bisa dibaca saat ini.",
    "gacha": "Riwayat pull tidak bisa dibaca saat ini.",
    "japanese": "Catatan bahasa Jepang tidak bisa dibaca saat ini.",
    "journal": "Jurnal tidak bisa dibaca saat ini.",
    "schedule": "Jadwal tidak bisa dibaca saat ini.",
}
_PULLS_FAILED = "Riwayat pull {short} tidak bisa dibaca saat ini."
_FUTURE = "Hari itu belum terjadi; rekap hanya untuk hari ini atau hari yang sudah lewat."
_TOO_FAR = "Rekap hanya sampai {days} hari ke belakang, paling jauh {oldest}."


class RecapError(Exception):
    """A day the recap does not cover; Indonesian and safe to speak."""


class TodoSource(Protocol):
    """The part of `TodoStore` the recap reads."""

    keep_done_days: int  # how long a done task is kept, then forgotten

    def done_tasks(self) -> list[Task]: ...

    def due_or_overdue(self, today: date) -> list[Task]: ...


class PlaySource(Protocol):
    """The part of `PlayTimeStore` the recap reads: the tracked games, and their sessions."""

    @property
    def games(self) -> Sequence[GameId]: ...

    def record(self) -> PlayRecord: ...


class PullStore(Protocol):
    """The part of `GachaStore` the recap reads: the local files only."""

    def load(self, game: Game, uid: int | None = None) -> StoredHistory: ...


class ProgressSource(Protocol):
    """The part of `JapaneseStore` the recap reads."""

    def progress(self) -> Mapping[str, WordProgress]: ...


class JournalSource(Protocol):
    """The part of `JournalStore` the recap reads."""

    def notes(
        self, since: date | None = None, until: date | None = None
    ) -> Mapping[date, tuple[Note, ...]]: ...


class ScheduleSource(Protocol):
    """The part of `ScheduleStore` the recap reads."""

    def entries(self) -> list[Entry]: ...


# The seconds Master focused inside `[since, until]`.
FocusSeconds = Callable[[datetime, datetime], int]


@dataclass(frozen=True)
class StoredPulls:
    """The pull history as stored for `games`, read without a refresh."""

    store: PullStore
    games: Sequence[str]
    server_utc_offset: int = 8
    extra_standard: Mapping[str, Sequence[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class TasksDay:
    # Finished that day, the most recent first; None when the list no longer keeps all of that
    # day's (a done task is kept `keep_done_days` days): unknown, never none.
    done: tuple[Task, ...] | None
    still_due: tuple[Task, ...]  # open and due that day or before, the overdue first
    keep_done_days: int


@dataclass(frozen=True)
class GamePlay:
    game: GameId
    seconds: int
    sessions: int  # with time that day


@dataclass(frozen=True)
class PlayDay:
    games: tuple[GamePlay, ...]  # every tracked game, in the configured order
    seconds: int  # two games open together count once

    @property
    def played(self) -> tuple[GamePlay, ...]:
        """The games open a whole minute (rounded) that day, the ones the recap tells: with
        none, nothing is told, though games open seconds each may make a minute together."""
        return tuple(game for game in self.games if whole_minutes(game.seconds))


@dataclass(frozen=True)
class GamePulls:
    game: Game
    banners: tuple[BannerPulls, ...]  # the banners pulled on that day, in display order

    @property
    def count(self) -> int:
        return sum(banner.count for banner in self.banners)


@dataclass(frozen=True)
class WordsDay:
    learned: tuple[Word, ...]  # in the word list's order
    reviewed: int  # the words whose last review was that day


@dataclass(frozen=True)
class Planned:
    """A reminder or routine due that day."""

    at: datetime  # naive local
    what: str  # the reminder's text, or `rutinitas <name>`
    passed: bool


@dataclass(frozen=True)
class Recap:
    """One day's parts; a part is None when its feature is off or it failed (with its
    problem in `problems`), and empty when it holds nothing that day."""

    day: date
    now: datetime  # aware WIB
    tasks: TasksDay | None = None
    focus: int | None = None  # seconds
    play_time: PlayDay | None = None
    gacha: tuple[GamePulls, ...] | None = None
    japanese: WordsDay | None = None
    journal: int | None = None  # notes
    schedule: tuple[Planned, ...] | None = None
    problems: Mapping[str, str] = field(default_factory=dict)  # by part, in the parts' order

    @property
    def is_today(self) -> bool:
        return self.day == self.now.date()

    @property
    def readable(self) -> bool:
        """Whether any part could be read at all."""
        return any(getattr(self, part) is not None for part in PARTS)


def recap_day(value: Literal["today", "yesterday"] | date, today: date) -> date:
    """`today`, `yesterday`, or a date from `DAYS_BACK` days before `today` up to it."""
    if not isinstance(value, date):
        return today if value == "today" else today - _ONE_DAY
    day = value
    if day > today:
        raise RecapError(_FUTURE)
    oldest = today - timedelta(days=DAYS_BACK)
    if day < oldest:
        raise RecapError(_TOO_FAR.format(days=DAYS_BACK, oldest=day_text(oldest)))
    return day


def day_text(day: date) -> str:
    """`Kamis 24-09-2026`: a day as the owner reads it."""
    return f"{WEEKDAYS_ID[day.weekday()]} {day:%d-%m-%Y}"


class DailyRecap:
    """Master's day from the local stores, each part read on its own."""

    def __init__(
        self,
        *,
        todo: TodoSource | None = None,
        focus: FocusSeconds | None = None,
        play_time: PlaySource | None = None,
        gacha: StoredPulls | None = None,
        japanese: ProgressSource | None = None,
        words: Sequence[Word] = (),
        journal: JournalSource | None = None,
        schedule: ScheduleSource | None = None,
        now: Callable[[], datetime] = wib_now,
    ) -> None:
        """A source is None when its feature is off; `words` names the Japanese words, and
        `now` is aware."""
        self._todo = todo
        self._focus = focus
        self._play_time = play_time
        self._gacha = gacha
        self._japanese = japanese
        self._words = tuple(words)
        self._journal = journal
        self._schedule = schedule
        self._now = now

    @property
    def parts(self) -> tuple[str, ...]:
        """The parts that are on, in the recap's order."""
        sources = (
            self._todo,
            self._focus,
            self._play_time,
            self._gacha,
            self._japanese,
            self._journal,
            self._schedule,
        )
        return tuple(
            part for part, source in zip(PARTS, sources, strict=True) if source is not None
        )

    def today(self) -> date:
        return self._now().astimezone(WIB).date()

    def gather(self, day: date) -> Recap:
        """The parts of `day` (WIB) that are on, from its midnight up to the next one, or up to
        now for today. Never raises."""
        now = self._now().astimezone(WIB)
        since = datetime.combine(day, time.min, WIB)
        until = min(now, since + _ONE_DAY)
        problems: dict[str, str] = {}  # filled in the parts' order
        read = partial(_read, problems)
        todo, focus, play, gacha = self._todo, self._focus, self._play_time, self._gacha
        japanese, words, journal, schedule = (
            self._japanese,
            self._words,
            self._journal,
            self._schedule,
        )
        return Recap(
            day,
            now,
            tasks=None if todo is None else read("tasks", lambda: _tasks(todo, day, since, now)),
            focus=None if focus is None else read("focus", lambda: focus(since, until)),
            play_time=(
                None if play is None else read("play_time", lambda: _play(play, since, until))
            ),
            gacha=(
                None
                if gacha is None
                else read("gacha", lambda: _pulls(gacha, since, until, problems))
            ),
            japanese=(
                None if japanese is None else read("japanese", lambda: _words(japanese, words, day))
            ),
            journal=(
                None
                if journal is None
                else read("journal", lambda: len(journal.notes(day, day).get(day, ())))
            ),
            schedule=(
                None if schedule is None else read("schedule", lambda: _planned(schedule, day, now))
            ),
            problems=problems,
        )


def _read(problems: dict[str, str], part: str, reader: Callable[[], T]) -> T | None:
    """What `reader` gives, or None with the part's problem when it fails (logged)."""
    try:
        return reader()
    except Exception:
        logger.exception("Could not read the %s for the daily recap", part)
        problems[part] = _PROBLEMS[part]
        return None


def _pulls(
    gacha: StoredPulls, since: datetime, until: datetime, problems: dict[str, str]
) -> tuple[GamePulls, ...]:
    """Each game's pulls in `(since, until]` from the stored history, each game read on its own:
    one that fails is logged and named in the part's problem."""
    found: list[GamePulls] = []
    failed: list[str] = []
    for game_id in gacha.games:
        game = GAMES[game_id]
        try:
            banners = pulls_since(
                game,
                gacha.store.load(game).records,
                since,
                gacha.server_utc_offset,
                gacha.extra_standard.get(game_id, ()),
                until=until,
            )
        except Exception:
            logger.exception("Could not read the %s pulls for the daily recap", game.short)
            failed.append(_PULLS_FAILED.format(short=game.short))
            continue
        if banners:
            found.append(GamePulls(game, tuple(banners)))
    if failed:
        problems["gacha"] = " ".join(failed)
    return tuple(found)


def _tasks(todo: TodoSource, day: date, since: datetime, now: datetime) -> TasksDay:
    """The day from `since`: its done tasks are unknown once a moment of it is more than
    `keep_done_days` before `now`, since the list has forgotten what was finished then."""
    keep = todo.keep_done_days
    still_due = tuple(todo.due_or_overdue(day))
    if since < now - timedelta(days=keep):
        return TasksDay(None, still_due, keep)
    done = tuple(
        task
        for task in todo.done_tasks()
        if task.done is not None and since <= task.done < since + _ONE_DAY
    )
    return TasksDay(done, still_due, keep)


def _play(play: PlaySource, since: datetime, until: datetime) -> PlayDay:
    record = play.record()
    games = list(play.games)
    totals = play_totals(record, games, since, until)
    mine = [s for s in (*record.sessions, *record.open) if s.game in games]
    return PlayDay(
        tuple(GamePlay(game, total.seconds, total.sessions) for game, total in totals.items()),
        played_seconds(mine, since, until),
    )


def _words(japanese: ProgressSource, words: Sequence[Word], day: date) -> WordsDay:
    """The listed words learned on `day`, and how many had their last review on it."""
    progress = japanese.progress()
    listed = [(word, progress[word.id]) for word in words if word.id in progress]
    return WordsDay(
        tuple(word for word, mine in listed if mine.learned == day),
        sum(_last_review(mine) == day for _, mine in listed),
    )


def _last_review(progress: WordProgress) -> date | None:
    """The day of a word's last review: its due day less the wait of its box, when that is not
    before it was learned (a word never asked is due the day it was learned)."""
    reviewed = progress.due - timedelta(days=2 ** (progress.box - 1))
    return reviewed if reviewed >= progress.learned else None


def _planned(schedule: ScheduleSource, day: date, now: datetime) -> tuple[Planned, ...]:
    """The reminders and routines due on `day`, soonest first; the schedule's naive local times
    are read as WIB."""
    local_now = now.replace(tzinfo=None)
    planned = []
    for entry in schedule.entries():
        action = entry.action
        if isinstance(action, DeviceAction):
            continue
        moment = occurrence_on(entry, day, now=local_now)
        if moment is None:
            continue
        what = action.text if isinstance(action, Reminder) else f"rutinitas {action.name}"
        planned.append(Planned(moment, what, moment <= local_now))
    return tuple(sorted(planned, key=lambda item: item.at))


# --- the JSON for the brain ----------------------------------------------------------------


def recap_json(recap: Recap) -> dict[str, Any]:
    """The day as compact facts: the parts with something only, lists of at most `LIST_MAX`."""
    day = recap.day
    data: dict[str, Any] = {"date": day.isoformat(), "day": WEEKDAYS_ID[day.weekday()]}
    if recap.is_today:
        data["as_of"] = f"{recap.now:%H:%M}"
    tasks = recap.tasks
    if tasks is not None and (tasks.done is None or tasks.done or tasks.still_due):
        data["tasks"] = {
            **_done_json(tasks.done),
            "still_due_count": len(tasks.still_due),
            "still_due": [
                {"text": task.text, "due": f"{task.due:%Y-%m-%d}"}
                for task in tasks.still_due[:LIST_MAX]
            ],
        }
    if recap.focus is not None and whole_minutes(recap.focus):
        data["focus"] = _hours_minutes(recap.focus)
    play = recap.play_time
    if play is not None and play.played:
        data["play_time"] = {
            "games": [
                {"name": GAMES[g.game].name, **_hours_minutes(g.seconds), "sessions": g.sessions}
                for g in play.played
            ],
            "total": _hours_minutes(play.seconds),
        }
    if recap.gacha:
        data["gacha"] = [_pulls_json(game) for game in recap.gacha]
    words = recap.japanese
    if words is not None and (words.learned or words.reviewed):
        data["japanese"] = {
            "learned_count": len(words.learned),
            "learned": [_word_json(word) for word in words.learned[:LIST_MAX]],
            "reviewed": words.reviewed,
        }
    if recap.journal:
        data["journal"] = {"notes": recap.journal}
    if recap.schedule:
        data["schedule"] = {
            "count": len(recap.schedule),
            "entries": [
                {"time": f"{item.at:%H:%M}", "what": item.what, "passed": item.passed}
                for item in recap.schedule[:LIST_MAX]
            ],
        }
    if recap.problems:
        data["problems"] = list(recap.problems.values())
    return data


def _done_json(done: tuple[Task, ...] | None) -> dict[str, Any]:
    """The tasks finished that day, or that the list no longer knows them."""
    if done is None:
        return {"done_known": False}
    return {"done_count": len(done), "done": [task.text for task in done[:LIST_MAX]]}


def _hours_minutes(seconds: int) -> dict[str, int]:
    hours, minutes = divmod(whole_minutes(seconds), 60)
    return {"hours": hours, "minutes": minutes}


def _pulls_json(pulls: GamePulls) -> dict[str, Any]:
    game = pulls.game
    tops = sorted(
        ((banner.banner, top) for banner in pulls.banners for top in banner.tops),
        key=lambda pair: (pair[1].time, pair[1].id),
        reverse=True,
    )
    return {
        "name": game.name,
        "pulls": pulls.count,
        "top_pulls": [
            {
                "banner": banner.name,
                "name": top.name,
                "rarity": "S" if game.id == "zzz" else "5★",
                "pulls": top.pulls,
                "won_5050": top.won_5050,
            }
            for banner, top in tops[:LIST_MAX]
        ],
    }


def _word_json(word: Word) -> dict[str, str]:
    data = {"kana": word.kana, "kanji": word.kanji, "meaning": word.meaning}
    return {key: value for key, value in data.items() if value is not None}
