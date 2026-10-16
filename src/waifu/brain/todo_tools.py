"""The six tools over Master's to-do list: tasks with an optional due date, no time of day.

"Tambah tugas: beli susu" / "tenggatnya Jumat": `add_task`, the model turning the day into a
date from the turn's time stamp. "Tugasku apa aja?": `list_tasks`, overdue first as the store
orders them. "Beli susu sudah" / "hapus tugas X" / "ganti tenggat laporan jadi Senin":
`complete_task`, `reopen_task`, `remove_task`, `edit_task`, by the id `list_tasks` gives. A
timed reminder stays a schedule entry. Dates are checked here (exactly YYYY-MM-DD, a day that
exists) and so are ids; a null argument counts as left out, as local models send it, so only
`clear_due` true removes a due date. The store refuses empty, too long, and secret texts and a
full list with an Indonesian message that comes back as an error result. Every result is
compact JSON and `run` never raises. The specs carry no tasks, so they stay byte-identical for
the brain's lifetime and keep the prompt cache warm.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

from waifu.brain.base import ToolSpec
from waifu.todo import KEEP, Task, TodoError, TodoStore

logger = logging.getLogger(__name__)

ADD_TASK = "add_task"
LIST_TASKS = "list_tasks"
COMPLETE_TASK = "complete_task"
REOPEN_TASK = "reopen_task"
REMOVE_TASK = "remove_task"
EDIT_TASK = "edit_task"
_ADD_KEYS = frozenset({"text", "due"})
_LIST_KEYS = frozenset({"include_done"})
_ID_KEYS = frozenset({"id"})
_EDIT_KEYS = frozenset({"id", "text", "due", "clear_due"})
# date.fromisoformat also reads 20261023 and 2026-W43-5; a due date is only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TASK_ID = re.compile(r"t[1-9][0-9]*")
_BY_ID = {
    "type": "object",
    "properties": {"id": {"type": "string"}},
    "required": ["id"],
    "additionalProperties": False,
}
_PICK_BY_TEXT = (
    "When Master names the task by its text, call list_tasks first and pick it by its text; "
    "never guess an id."
)
_FAILED = "Daftar tugas tidak bisa dibuka saat ini."


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


def _local_now() -> datetime:
    return datetime.now().astimezone()


class TodoTools:
    """A `ToolProvider` for the brain: Master's to-do list."""

    def __init__(self, store: TodoStore, *, now: Callable[[], datetime] = _local_now) -> None:
        """`now` gives the local time; its date is today, which decides what is overdue."""
        self._store = store
        self._now = now

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=ADD_TASK,
                description=(
                    "Add one task to Master's to-do list: something Master has to do, with an "
                    "optional due date and never a time of day. Add a task only when Master "
                    "asks you to note something to do ('tambah tugas', 'catat'). A task with a "
                    "time of day ('jam 3 sore', 'nanti malam jam 8') belongs in a schedule "
                    "reminder instead: say so, or make both when Master asks. Write `text` "
                    "short, in Indonesian, as Master said it. Never add secrets (passwords, "
                    "PINs, OTPs, tokens, card numbers): they are refused. Give `due` as "
                    "YYYY-MM-DD when Master says a day: every user turn starts with today's "
                    "date and weekday, [weekday, DD-MM-YYYY HH:MM]; turn 'besok', 'Jumat', or "
                    "'minggu depan' into a date from it. Without a day, leave `due` out. "
                    "Confirm briefly in one short sentence; when you set a due date, say its "
                    "weekday and date in it."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "due": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name=LIST_TASKS,
                description=(
                    "List Master's open tasks, overdue first, then by due date, then the ones "
                    "without a date: each with its id (t1, t2, ...), its text, its due date "
                    "(YYYY-MM-DD) when it has one, and overdue: true when that day has passed; "
                    "`today` is today's date. Pass include_done true to also get the tasks "
                    "finished in the last days, when Master asks what they finished. Use it "
                    "when Master asks about their tasks or whether anything is late, and to "
                    "find the task Master names: pick it by its text; never guess an id. Never "
                    "read every task aloud unless Master asks for the whole list: say how many "
                    "there are and the most urgent ones."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"include_done": {"type": "boolean"}},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name=COMPLETE_TASK,
                description=(
                    "Mark one task done by its id (t1, t2, ...: from list_tasks or add_task) "
                    f"when Master says it is finished ('beli susu sudah'). {_PICK_BY_TEXT} "
                    "Confirm briefly."
                ),
                input_schema=_BY_ID,
            ),
            ToolSpec(
                name=REOPEN_TASK,
                description=(
                    "Open a done task again by its id when Master says it is not finished "
                    "after all; list_tasks with include_done true lists the done tasks. "
                    "Confirm briefly."
                ),
                input_schema=_BY_ID,
            ),
            ToolSpec(
                name=REMOVE_TASK,
                description=(
                    "Delete one task, open or done, from the list by its id when Master asks "
                    f"to remove it ('hapus tugas X'). {_PICK_BY_TEXT} When Master finished the "
                    "task, use complete_task instead. Confirm briefly."
                ),
                input_schema=_BY_ID,
            ),
            ToolSpec(
                name=EDIT_TASK,
                description=(
                    "Change one task's text or due date by its id ('ganti tenggat laporan jadi "
                    f"Senin'). {_PICK_BY_TEXT} Give `text` for new wording, `due` as "
                    "YYYY-MM-DD for a new due date (turned into a date from the turn's time "
                    "stamp as for add_task), or clear_due true to remove the due date. Leave "
                    "out what stays the same. Confirm briefly; when you set a due date, say "
                    "its weekday and date."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                        "due": {"type": "string"},
                        "clear_due": {"type": "boolean"},
                    },
                    "required": ["id"],
                    "additionalProperties": False,
                },
            ),
        ]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == ADD_TASK:
                return self._add(_arguments_mapping(arguments, _ADD_KEYS))
            if name == LIST_TASKS:
                return self._list(_arguments_mapping(arguments, _LIST_KEYS))
            if name == COMPLETE_TASK:
                return self._by_id(self._store.complete, arguments)
            if name == REOPEN_TASK:
                return self._by_id(self._store.reopen, arguments)
            if name == REMOVE_TASK:
                return self._by_id(self._store.remove, arguments)
            if name == EDIT_TASK:
                return self._edit(_arguments_mapping(arguments, _EDIT_KEYS))
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except TodoError as exc:
            return _error(str(exc))
        except Exception:
            logger.exception("To-do tool '%s' failed", name)
            return _error(_FAILED)
        return _error(f"Tool '{name}' bukan tool daftar tugas.")

    def _add(self, arguments: Mapping[str, Any]) -> tuple[str, bool]:
        text = _text(arguments, "text")
        due = arguments.get("due")  # null counts as left out, as local models send it
        return self._answer(self._store.add(text, None if due is None else _date(due)))

    def _list(self, arguments: Mapping[str, Any]) -> tuple[str, bool]:
        include_done = _flag(arguments, "include_done")
        today = self._today()
        data: dict[str, Any] = {
            "today": today.isoformat(),
            "open": [_task_json(task, today) for task in self._store.open_tasks()],
        }
        if include_done:
            data["done"] = [_task_json(task, today) for task in self._store.done_tasks()]
        return _to_json(data), False

    def _by_id(self, change: Callable[[str], Task | None], arguments: object) -> tuple[str, bool]:
        task_id = _task_id(_arguments_mapping(arguments, _ID_KEYS))
        return self._found(change(task_id), task_id)

    def _edit(self, arguments: Mapping[str, Any]) -> tuple[str, bool]:
        """A null argument counts as left out, as local models send it: only clear_due true
        clears the due date, so a null `due` beside a new text never loses it."""
        task_id = _task_id(arguments)
        text = None if arguments.get("text") is None else _text(arguments, "text")
        given = arguments.get("due")
        clear = _flag(arguments, "clear_due")
        if clear and given is not None:
            raise _InvalidArguments("isi due atau clear_due, jangan keduanya.")
        if text is None and given is None and not clear:
            raise _InvalidArguments("isi text, due, atau clear_due yang mau diubah.")
        due = None if clear else KEEP if given is None else _date(given)
        return self._found(self._store.edit(task_id, text=text, due=due), task_id)

    def _found(self, task: Task | None, task_id: str) -> tuple[str, bool]:
        """The changed task, or an error result when the store holds no task by that id."""
        if task is None:
            return _error(f"Tidak ada tugas dengan id {task_id}.")
        return self._answer(task)

    def _answer(self, task: Task) -> tuple[str, bool]:
        return _to_json({"ok": True, "task": _task_json(task, self._today())}), False

    def _today(self) -> date:
        return self._now().date()


def _arguments_mapping(arguments: object, allowed: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in allowed]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _flag(arguments: Mapping[str, Any], key: str) -> bool:
    """Whether `key` is true; left out and null are false."""
    value = arguments.get(key)
    if value is not None and type(value) is not bool:
        raise _InvalidArguments(f"'{key}' harus true atau false.")
    return value is True


def _text(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidArguments(f"'{key}' wajib diisi dengan teks.")
    return value


def _task_id(arguments: Mapping[str, Any]) -> str:
    value = arguments.get("id")
    if not isinstance(value, str) or not _TASK_ID.fullmatch(value):
        raise _InvalidArguments("'id' harus id tugas seperti t3.")
    return value


def _date(value: object) -> date:
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:  # a day that does not exist, such as 2026-02-30
            pass
    raise _InvalidArguments("'due' harus tanggal dengan format YYYY-MM-DD.")


def _task_json(task: Task, today: date) -> dict[str, Any]:
    """`{"id", "text", "done"}` for a done task; `{"id", "text", "due"?, "overdue"?}` else."""
    data: dict[str, Any] = {"id": task.id, "text": task.text}
    if task.done is not None:
        data["done"] = task.done.date().isoformat()
    elif task.due is not None:
        data["due"] = task.due.isoformat()
        if task.due < today:
            data["overdue"] = True
    return data


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
