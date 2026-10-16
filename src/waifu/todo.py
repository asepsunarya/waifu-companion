"""Master's to-do list: tasks with an optional due date, beside the timed schedules.

A task is something to do without a time of day ("beli susu", "kerjakan laporan, tenggatnya
Jumat"); a timed reminder stays a schedule entry. The open tasks come by due date, so the
overdue ones first, then the ones without a due date in the order they were made. A done task
is kept `keep_done_days` days ("what did I finish?"), then forgotten: it is no longer listed,
and it leaves the file at the next write. Ids ("t1", "t2", ...) are never reused, also after
a task is removed or forgotten.

The file (`todo.path`) follows the rules of `memory.py`: every call re-reads it, so a running
Cyrene sees what `waifu todo` changed in another process. One lock serialises the calls of
every store in this process; between processes the last writer wins. A write goes to a temp
file in the same folder, reaches the disk, and then replaces the file. Reading never creates
it, and a file that is not a to-do list is moved aside before anything is written: it is
never overwritten. Times are aware (local time with its offset), stamped to the second.

A task that looks like a secret is refused here, whatever the model asks for.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Final

from waifu.gacha.files import write_json_atomic
from waifu.memory import looks_secret

logger = logging.getLogger(__name__)

FILE_VERSION = 1
_FILE_KEYS = {"version", "next_id", "tasks"}
_TASK_KEYS = {"id", "text", "due", "created", "done"}
_TASK_ID = re.compile(r"t[1-9][0-9]*")
# date.fromisoformat also reads 20261023 and 2026-W43-5; the file holds only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# One lock for every store: two stores on the same file in this process must not lose
# each other's tasks.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Task:
    """A task on Master's list; `done` is when it was finished, None while it is open."""

    id: str  # "t1", "t2", ...: never reused
    text: str
    due: date | None
    created: datetime
    done: datetime | None


class _Keep(Enum):
    KEEP = "keep"


# `TodoStore.edit(task_id, due=KEEP)` keeps the due date; `due=None` clears it.
KEEP: Final = _Keep.KEEP


class TodoError(Exception):
    """A refused task; the message is Indonesian and safe to show or speak."""


def _local_now() -> datetime:
    return datetime.now().astimezone()


class TodoStore:
    def __init__(
        self,
        path: Path | str,
        *,
        max_tasks: int,
        max_chars: int,
        keep_done_days: int,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        """`max_tasks` limits the open tasks; `now` (aware) stamps the tasks and decides
        which done tasks are forgotten. A naive clock is a ValueError at every call."""
        self.path = Path(path)
        self.max_tasks = max_tasks
        self.max_chars = max_chars
        self.keep_done_days = keep_done_days
        self._now = now

    def add(self, text: str, due: date | None = None) -> Task:
        """Put a new open task on the list and return it."""
        text = self._checked_text(text)
        _check_due(due)
        now = self._clock()
        with _LOCK:
            next_id, tasks = self._read(now)
            self._check_room(tasks)
            task = Task(f"t{next_id}", text, due, _stamp(now), None)
            self._write(next_id + 1, [*tasks, task])
            return task

    def open_tasks(self) -> list[Task]:
        """The tasks not done yet: by due date (so the overdue ones first), then the ones
        without a due date, each group in the order they were made."""
        return sorted((task for task in self._tasks() if task.done is None), key=_open_order)

    def done_tasks(self) -> list[Task]:
        """The done tasks not forgotten yet, the most recently finished first."""
        done = [task for task in self._tasks() if task.done is not None]
        return sorted(done, key=lambda task: (task.done, _number(task)), reverse=True)

    def due_or_overdue(self, today: date) -> list[Task]:
        """The open tasks due on `today` or before it, the overdue ones first."""
        return [task for task in self.open_tasks() if task.due is not None and task.due <= today]

    def complete(self, task_id: str) -> Task | None:
        """Mark task `task_id` done now and return it (a done task keeps when it was done);
        None when there is no such task."""
        now = self._clock()
        with _LOCK:
            next_id, tasks = self._read(now)
            task = _find(tasks, task_id)
            if task is None or task.done is not None:
                return task
            return self._save(next_id, tasks, task, dataclasses.replace(task, done=_stamp(now)))

    def reopen(self, task_id: str) -> Task | None:
        """Make task `task_id` open again and return it; None when there is no such task.
        TodoError when `max_tasks` tasks are open already."""
        now = self._clock()
        with _LOCK:
            next_id, tasks = self._read(now)
            task = _find(tasks, task_id)
            if task is None or task.done is None:
                return task
            self._check_room(tasks)
            return self._save(next_id, tasks, task, dataclasses.replace(task, done=None))

    def remove(self, task_id: str) -> Task | None:
        """Delete task `task_id`, open or done, and return it; None when there is no such
        task."""
        now = self._clock()
        with _LOCK:
            next_id, tasks = self._read(now)
            task = _find(tasks, task_id)
            if task is not None:
                self._write(next_id, [other for other in tasks if other is not task])
            return task

    def edit(
        self, task_id: str, *, text: str | None = None, due: date | _Keep | None = KEEP
    ) -> Task | None:
        """Give task `task_id` a new text and/or due date (KEEP keeps it, None clears it) and
        return it; None when there is no such task. The text is checked like `add`'s."""
        new_text = None if text is None else self._checked_text(text)
        if due is not KEEP:
            _check_due(due)
        now = self._clock()
        with _LOCK:
            next_id, tasks = self._read(now)
            task = _find(tasks, task_id)
            if task is None:
                return None
            edited = dataclasses.replace(
                task,
                text=task.text if new_text is None else new_text,
                due=task.due if due is KEEP else due,
            )
            return self._save(next_id, tasks, task, edited)

    def _clock(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("the to-do list's clock must give aware times")
        return now

    def _checked_text(self, text: str) -> str:
        text = " ".join(text.split())
        if not text:
            raise TodoError("Tugasnya kosong.")
        if len(text) > self.max_chars:
            raise TodoError(f"Tugasnya terlalu panjang, paling banyak {self.max_chars} karakter.")
        if looks_secret(text):
            raise TodoError(
                "Tugas itu terlihat seperti rahasia, misalnya kata sandi, PIN, token, "
                "atau nomor kartu, jadi tidak disimpan."
            )
        return text

    def _check_room(self, tasks: list[Task]) -> None:
        if sum(task.done is None for task in tasks) >= self.max_tasks:
            raise TodoError(
                f"Daftar tugas sudah penuh, {self.max_tasks} tugas belum selesai. "
                "Selesaikan atau hapus tugas lama dulu."
            )

    def _tasks(self) -> list[Task]:
        now = self._clock()
        with _LOCK:
            return self._read(now)[1]

    def _read(self, now: datetime) -> tuple[int, list[Task]]:
        """The next id and the tasks not forgotten at `now`, in the order they were added; a
        missing file is an empty list and stays missing."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            next_id, tasks = _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return 1, []
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return 1, []
        keep = timedelta(days=self.keep_done_days)
        return next_id, [task for task in tasks if task.done is None or now - task.done <= keep]

    def _save(self, next_id: int, tasks: list[Task], old: Task, new: Task) -> Task:
        """Write `tasks` with `old` replaced by `new`, unless they are the same; return `new`."""
        if new != old:
            self._write(next_id, [new if task is old else task for task in tasks])
        return new

    def _set_aside(self, problem: ValueError, now: datetime) -> None:
        """Rename a file that is not a to-do list, so that no write ever replaces it."""
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
                "To-do file %s is not a to-do list (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "To-do file %s is not a to-do list (%s); moved it to %s and started an empty list",
            self.path,
            problem,
            target,
        )

    def _write(self, next_id: int, tasks: list[Task]) -> None:
        write_json_atomic(
            self.path,
            {"version": FILE_VERSION, "next_id": next_id, "tasks": [_dump(t) for t in tasks]},
        )


def _check_due(due: date | None) -> None:
    """A ValueError for a time given as the due date: the file holds days."""
    if isinstance(due, datetime):
        raise ValueError("a task's due date must be a date, not a datetime")


def _stamp(now: datetime) -> datetime:
    return now.replace(microsecond=0)


def _number(task: Task) -> int:
    return int(task.id[1:])


def _open_order(task: Task) -> tuple[bool, date, datetime, int]:
    return task.due is None, task.due or date.max, task.created, _number(task)


def _find(tasks: list[Task], task_id: str) -> Task | None:
    for task in tasks:
        if task.id == task_id:
            return task
    return None


def _dump(task: Task) -> dict[str, str | None]:
    return {
        "id": task.id,
        "text": task.text,
        "due": None if task.due is None else task.due.isoformat(),
        "created": task.created.isoformat(),
        "done": None if task.done is None else task.done.isoformat(),
    }


def _parse(raw: object) -> tuple[int, list[Task]]:
    """The next id and the tasks in `raw`; ValueError says what is wrong, never a task."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version, next_id, entries = raw["version"], raw["next_id"], raw["tasks"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if type(next_id) is not int or next_id < 1:
        raise ValueError("next_id must be a positive integer")
    if not isinstance(entries, list):
        raise ValueError("tasks must be a list")
    tasks = [_parse_task(index, entry) for index, entry in enumerate(entries)]
    numbers = [_number(task) for task in tasks]
    if len(set(numbers)) != len(numbers):
        raise ValueError("two tasks share an id")
    # An id written by hand at or beyond next_id must not be handed out again.
    return max([next_id, *(number + 1 for number in numbers)]), tasks


def _parse_task(index: int, entry: object) -> Task:
    where = f"tasks[{index}]"
    if not isinstance(entry, dict) or entry.keys() != _TASK_KEYS:
        raise ValueError(f"{where} must be an object with exactly the keys {sorted(_TASK_KEYS)}")
    task_id, text, due, done = entry["id"], entry["text"], entry["due"], entry["done"]
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise ValueError(f"{where}.id must look like t1")
    if not isinstance(text, str):
        raise ValueError(f"{where}.text must be a string")
    return Task(
        task_id,
        text,
        None if due is None else _parse_date(due, f"{where}.due"),
        _parse_time(entry["created"], f"{where}.created"),
        None if done is None else _parse_time(done, f"{where}.done"),
    )


def _parse_date(value: object, where: str) -> date:
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass  # a day that does not exist, such as 2026-02-30
    raise ValueError(f"{where} must be a date written YYYY-MM-DD")


def _parse_time(value: object, where: str) -> datetime:
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if moment.tzinfo is not None:
                return moment
    raise ValueError(f"{where} must be an ISO 8601 time with its offset")
