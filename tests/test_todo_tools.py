"""The to-do tools: six tools over Master's task list.

Every `TodoTools` gets a fake store with TodoStore's methods over lists in memory (the store's
own file rules and ordering are tested in test_todo.py) and a fixed time in Indonesia's western
zone (WIB, UTC+7). No file is written.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.todo_tools import TodoTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.todo import KEEP, Task, TodoError

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 16, 9, 30, tzinfo=WIB)  # a Friday
CREATED = datetime(2026, 10, 10, 20, 0, tzinfo=WIB)
TOOL_NAMES = ["add_task", "list_tasks", "complete_task", "reopen_task", "remove_task", "edit_task"]
UNSUPPORTED_STRICT_KEYWORDS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
}

# In the store's order: overdue first, then by due date, then the undated ones.
LAPORAN = Task("t1", "kerjakan laporan", date(2026, 10, 14), CREATED, None)
SUSU = Task("t2", "beli susu", date(2026, 10, 16), CREATED, None)
KADO = Task("t4", "beli kado Rina", date(2026, 10, 20), CREATED, None)
MOTOR = Task("t5", "cuci motor", None, CREATED, None)
OBAT = Task("t3", "tebus obat", None, CREATED, datetime(2026, 10, 15, 20, 15, tzinfo=WIB))
OPEN = [LAPORAN, SUSU, KADO, MOTOR]


class FakeStore:
    """Stands in for TodoStore: the same methods over two lists, recording every call; like the
    store, a task it does not hold gives None, and completing a done task or reopening an open
    one gives it back unchanged. With `error`, every call raises it."""

    def __init__(
        self,
        open_tasks: list[Task] | None = None,
        done_tasks: list[Task] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.open = list(OPEN if open_tasks is None else open_tasks)
        self.done = list([OBAT] if done_tasks is None else done_tasks)
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    def _call(self, *call: object) -> None:
        self.calls.append(call)
        if self.error is not None:
            raise self.error

    def _find(self, tasks: list[Task], task_id: str) -> Task | None:
        return next((task for task in tasks if task.id == task_id), None)

    def add(self, text: str, due: date | None = None) -> Task:
        self._call("add", text, due)
        task = Task(f"t{len(self.open) + len(self.done) + 1}", text, due, NOW, None)
        self.open.append(task)
        return task

    def open_tasks(self) -> list[Task]:
        self._call("open_tasks")
        return list(self.open)

    def done_tasks(self) -> list[Task]:
        self._call("done_tasks")
        return list(self.done)

    def complete(self, task_id: str) -> Task | None:
        self._call("complete", task_id)
        task = self._find(self.open, task_id)
        if task is None:
            return self._find(self.done, task_id)
        self.open.remove(task)
        finished = Task(task.id, task.text, task.due, task.created, NOW)
        self.done.insert(0, finished)
        return finished

    def reopen(self, task_id: str) -> Task | None:
        self._call("reopen", task_id)
        task = self._find(self.done, task_id)
        if task is None:
            return self._find(self.open, task_id)
        self.done.remove(task)
        reopened = Task(task.id, task.text, task.due, task.created, None)
        self.open.append(reopened)
        return reopened

    def remove(self, task_id: str) -> Task | None:
        self._call("remove", task_id)
        for tasks in (self.open, self.done):
            task = self._find(tasks, task_id)
            if task is not None:
                tasks.remove(task)
                return task
        return None

    def edit(self, task_id: str, *, text: str | None = None, due: object = KEEP) -> Task | None:
        self._call("edit", task_id, text, due)
        task = self._find(self.open, task_id) or self._find(self.done, task_id)
        if task is None:
            return None
        new_due = task.due if due is KEEP else due
        assert new_due is None or isinstance(new_due, date)
        return Task(task.id, text or task.text, new_due, task.created, task.done)


class Clock:
    """A settable aware clock."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_tools(store: FakeStore | None = None, now: datetime = NOW) -> TodoTools:
    return TodoTools(store if store is not None else FakeStore(), now=Clock(now))  # type: ignore[arg-type]


def run(tools: TodoTools, name: str, arguments: object) -> tuple[dict, bool]:
    result, is_error = tools.run(name, arguments)
    return json.loads(result), is_error


def spec(tools: TodoTools, name: str):
    [found] = [each for each in tools.specs() if each.name == name]
    return found


def schema_nodes(node: object):
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from schema_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from schema_nodes(child)


def invalid(name: str, problem: str) -> dict:
    return {"ok": False, "message": f"Argumen {name} tidak valid: {problem}"}


# --- specs -------------------------------------------------------------------------------------


def test_six_tools_with_their_schemas():
    tools = make_tools()
    by_id = {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": False,
    }

    assert [each.name for each in tools.specs()] == TOOL_NAMES
    assert spec(tools, "add_task").input_schema == {
        "type": "object",
        "properties": {"text": {"type": "string"}, "due": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    assert spec(tools, "list_tasks").input_schema == {
        "type": "object",
        "properties": {"include_done": {"type": "boolean"}},
        "additionalProperties": False,
    }
    for name in ("complete_task", "reopen_task", "remove_task"):
        assert spec(tools, name).input_schema == by_id
    assert spec(tools, "edit_task").input_schema == {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "text": {"type": "string"},
            "due": {"type": "string"},
            "clear_due": {"type": "boolean"},
        },
        "required": ["id"],
        "additionalProperties": False,
    }


def test_the_specs_stay_within_strict_tool_use_for_both_backends():
    for tool in make_tools().specs():
        for node in schema_nodes(tool.input_schema):
            assert not UNSUPPORTED_STRICT_KEYWORDS & node.keys()
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
        claude_tool = to_anthropic(tool)
        # Strict, except the list whose one argument may be left out.
        assert claude_tool["strict"] is (tool.name != "list_tasks")
        assert (
            claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
        )
        assert ollama.Tool.model_validate(to_ollama(tool)).function.name == tool.name


def test_the_specs_stay_identical_whatever_the_list_holds_for_prompt_caching():
    store = FakeStore()
    tools = make_tools(store)
    first = tools.specs()

    tools.run("add_task", {"text": "beli beras", "due": "2026-10-17"})
    tools.run("complete_task", {"id": "t2"})

    assert tools.specs() == first
    assert make_tools(FakeStore([], [])).specs() == first
    assert make_tools(now=NOW + timedelta(days=30)).specs() == first


@pytest.mark.parametrize(
    ("name", "guidance"),
    [
        ("add_task", "only when Master asks you to note something to do"),
        ("add_task", "time of day"),
        ("add_task", "schedule reminder instead"),
        ("add_task", "make both"),
        ("add_task", "YYYY-MM-DD"),
        ("add_task", "today's date and weekday"),
        ("add_task", "leave `due` out"),
        ("add_task", "passwords"),
        ("add_task", "Confirm briefly"),
        ("add_task", "say its weekday and date"),
        ("list_tasks", "overdue first"),
        ("list_tasks", "include_done"),
        ("list_tasks", "pick it by its text"),
        ("list_tasks", "never guess an id"),
        ("list_tasks", "Never read every task aloud unless Master asks"),
        ("list_tasks", "how many"),
        ("list_tasks", "most urgent"),
        ("complete_task", "list_tasks"),
        ("complete_task", "by its text"),
        ("complete_task", "Confirm briefly"),
        ("reopen_task", "include_done"),
        ("reopen_task", "Confirm briefly"),
        ("remove_task", "complete_task"),
        ("remove_task", "Confirm briefly"),
        ("edit_task", "clear_due true"),
        ("edit_task", "YYYY-MM-DD"),
        ("edit_task", "Leave out what stays the same"),
        ("edit_task", "Confirm briefly"),
        ("edit_task", "say its weekday and date"),
    ],
)
def test_the_descriptions_say_when_to_use_each_tool(name, guidance):
    assert guidance in spec(make_tools(), name).description


# --- add_task ----------------------------------------------------------------------------------


def test_add_task_saves_the_text_and_answers_with_the_task():
    store = FakeStore([], [])

    result, is_error = make_tools(store).run("add_task", {"text": "beli susu"})

    assert result == '{"ok":true,"task":{"id":"t1","text":"beli susu"}}'
    assert is_error is False
    assert store.calls == [("add", "beli susu", None)]


def test_add_task_with_a_due_date_saves_it_as_a_date():
    store = FakeStore([], [])

    data, is_error = run(
        make_tools(store), "add_task", {"text": "kerjakan laporan", "due": "2026-10-23"}
    )

    assert (data, is_error) == (
        {"ok": True, "task": {"id": "t1", "text": "kerjakan laporan", "due": "2026-10-23"}},
        False,
    )
    assert store.calls == [("add", "kerjakan laporan", date(2026, 10, 23))]


def test_an_explicit_null_due_adds_a_task_without_a_date():
    # Local models may send null for an optional argument instead of leaving it out.
    store = FakeStore([], [])

    data, is_error = run(make_tools(store), "add_task", {"text": "beli susu", "due": None})

    assert (data, is_error) == ({"ok": True, "task": {"id": "t1", "text": "beli susu"}}, False)
    assert store.calls == [("add", "beli susu", None)]


def test_a_task_added_with_a_past_date_says_it_is_overdue():
    data, _ = run(
        make_tools(FakeStore([], [])), "add_task", {"text": "bayar UKT", "due": "2026-10-15"}
    )

    assert data["task"] == {"id": "t1", "text": "bayar UKT", "due": "2026-10-15", "overdue": True}


def test_the_store_refusing_a_task_is_an_error_result_with_its_message():
    refusal = TodoError("Tugas yang terlihat seperti rahasia tidak disimpan.")
    store = FakeStore(error=refusal)

    data, is_error = run(make_tools(store), "add_task", {"text": "PIN ATM 123456"})

    assert (data, is_error) == (
        {"ok": False, "message": "Tugas yang terlihat seperti rahasia tidak disimpan."},
        True,
    )


BAD_DATES = {
    "day first": "23-10-2026",
    "with a time": "2026-10-23 15:00",
    "without dashes": "20261023",
    "a week date": "2026-W43-5",
    "a day that does not exist": "2026-02-30",
    "a weekday name": "Jumat",
    "empty": "",
    "with a newline": "2026-10-23\n",
    "full-width digits": "２０２６-１０-２３",
    "a number": 20261023,
    "true": True,
}


@pytest.mark.parametrize("due", BAD_DATES.values(), ids=BAD_DATES.keys())
def test_a_due_date_not_written_yyyy_mm_dd_is_an_error_result(due):
    store = FakeStore([], [])

    data, is_error = run(make_tools(store), "add_task", {"text": "beli susu", "due": due})

    assert (data, is_error) == (
        invalid("add_task", "'due' harus tanggal dengan format YYYY-MM-DD."),
        True,
    )
    assert store.calls == []


# --- list_tasks --------------------------------------------------------------------------------


def test_list_tasks_gives_today_and_the_open_tasks_in_the_stores_order():
    result, is_error = make_tools().run("list_tasks", {})

    assert json.loads(result) == {
        "today": "2026-10-16",
        "open": [
            {"id": "t1", "text": "kerjakan laporan", "due": "2026-10-14", "overdue": True},
            {"id": "t2", "text": "beli susu", "due": "2026-10-16"},
            {"id": "t4", "text": "beli kado Rina", "due": "2026-10-20"},
            {"id": "t5", "text": "cuci motor"},
        ],
    }
    assert is_error is False
    assert result.startswith('{"today":"2026-10-16","open":[{"id":"t1",')


def test_list_tasks_with_include_done_adds_the_done_tasks_with_their_day():
    data, is_error = run(make_tools(), "list_tasks", {"include_done": True})

    assert data["done"] == [{"id": "t3", "text": "tebus obat", "done": "2026-10-15"}]
    assert len(data["open"]) == 4
    assert is_error is False


@pytest.mark.parametrize("arguments", [{}, {"include_done": False}, {"include_done": None}])
def test_list_tasks_leaves_the_done_tasks_out_unless_asked(arguments):
    store = FakeStore()

    data, _ = run(make_tools(store), "list_tasks", arguments)

    assert "done" not in data
    assert store.calls == [("open_tasks",)]


def test_an_empty_list_has_no_open_tasks():
    data, is_error = run(make_tools(FakeStore([], [])), "list_tasks", {"include_done": True})

    assert (data, is_error) == ({"today": "2026-10-16", "open": [], "done": []}, False)


def test_overdue_follows_the_clock():
    data, _ = run(make_tools(now=datetime(2026, 10, 17, 0, 5, tzinfo=WIB)), "list_tasks", {})

    assert data["today"] == "2026-10-17"
    assert [task.get("overdue", False) for task in data["open"]] == [True, True, False, False]


# --- complete, reopen, remove ------------------------------------------------------------------


def test_complete_task_marks_it_done_and_answers_with_the_day():
    store = FakeStore()

    result, is_error = make_tools(store).run("complete_task", {"id": "t2"})

    assert result == '{"ok":true,"task":{"id":"t2","text":"beli susu","done":"2026-10-16"}}'
    assert is_error is False
    assert store.calls == [("complete", "t2")]


def test_reopen_task_opens_a_done_task_again():
    store = FakeStore()

    data, is_error = run(make_tools(store), "reopen_task", {"id": "t3"})

    assert (data, is_error) == ({"ok": True, "task": {"id": "t3", "text": "tebus obat"}}, False)
    assert store.calls == [("reopen", "t3")]


def test_remove_task_answers_with_the_removed_task():
    store = FakeStore()

    data, is_error = run(make_tools(store), "remove_task", {"id": "t4"})

    assert (data, is_error) == (
        {"ok": True, "task": {"id": "t4", "text": "beli kado Rina", "due": "2026-10-20"}},
        False,
    )
    assert store.calls == [("remove", "t4")]
    assert KADO not in store.open


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("complete_task", {"id": "t9"}),
        ("reopen_task", {"id": "t9"}),
        ("remove_task", {"id": "t9"}),
        ("edit_task", {"id": "t9", "text": "beli susu kotak"}),
    ],
)
def test_an_unknown_id_is_an_error_result(name, arguments):
    data, is_error = run(make_tools(), name, arguments)

    assert (data, is_error) == ({"ok": False, "message": "Tidak ada tugas dengan id t9."}, True)


BAD_IDS = {
    "empty": "",
    "blank": "  ",
    "a number in text": "9",
    "no number": "t",
    "zero": "t0",
    "a leading zero": "t01",
    "a capital": "T3",
    "a memory note's id": "m3",
    "with a space": "t3 ",
    "negative": "t-1",
    "a number": 3,
    "null": None,
}


@pytest.mark.parametrize("name", ["complete_task", "reopen_task", "remove_task", "edit_task"])
@pytest.mark.parametrize("task_id", BAD_IDS.values(), ids=BAD_IDS.keys())
def test_an_id_that_is_no_task_id_is_an_error_result(name, task_id):
    store = FakeStore()
    arguments = (
        {"id": task_id, "text": "beli susu kotak"} if name == "edit_task" else {"id": task_id}
    )

    data, is_error = run(make_tools(store), name, arguments)

    assert (data, is_error) == (invalid(name, "'id' harus id tugas seperti t3."), True)
    assert store.calls == []


@pytest.mark.parametrize("name", ["complete_task", "reopen_task", "remove_task", "edit_task"])
def test_a_missing_id_is_an_error_result(name):
    store = FakeStore()

    data, is_error = run(make_tools(store), name, {})

    assert (data, is_error) == (invalid(name, "'id' harus id tugas seperti t3."), True)
    assert store.calls == []


# --- edit_task ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "call", "task"),
    [
        (
            {"id": "t1", "text": "kerjakan laporan bab 2"},
            ("edit", "t1", "kerjakan laporan bab 2", KEEP),
            {"id": "t1", "text": "kerjakan laporan bab 2", "due": "2026-10-14", "overdue": True},
        ),
        (
            # Local models send null for the optional arguments they do not use.
            {"id": "t1", "text": "kerjakan laporan bab 2", "due": None, "clear_due": None},
            ("edit", "t1", "kerjakan laporan bab 2", KEEP),
            {"id": "t1", "text": "kerjakan laporan bab 2", "due": "2026-10-14", "overdue": True},
        ),
        (
            {"id": "t1", "text": "kerjakan laporan bab 2", "due": None, "clear_due": False},
            ("edit", "t1", "kerjakan laporan bab 2", KEEP),
            {"id": "t1", "text": "kerjakan laporan bab 2", "due": "2026-10-14", "overdue": True},
        ),
        (
            {"id": "t1", "due": "2026-10-19"},
            ("edit", "t1", None, date(2026, 10, 19)),
            {"id": "t1", "text": "kerjakan laporan", "due": "2026-10-19"},
        ),
        (
            {"id": "t1", "text": None, "due": None, "clear_due": True},
            ("edit", "t1", None, None),
            {"id": "t1", "text": "kerjakan laporan"},
        ),
        (
            {"id": "t1", "text": "kerjakan laporan bab 2", "clear_due": True},
            ("edit", "t1", "kerjakan laporan bab 2", None),
            {"id": "t1", "text": "kerjakan laporan bab 2"},
        ),
        (
            {"id": "t5", "text": "cuci motor dan helm", "due": "2026-10-18"},
            ("edit", "t5", "cuci motor dan helm", date(2026, 10, 18)),
            {"id": "t5", "text": "cuci motor dan helm", "due": "2026-10-18"},
        ),
    ],
    ids=[
        "text keeps the date",
        "null keeps the date",
        "clear_due false keeps the date",
        "a new date",
        "only clearing",
        "clearing with a new text",
        "both",
    ],
)
def test_edit_task_changes_what_is_given_and_keeps_the_rest(arguments, call, task):
    store = FakeStore()

    data, is_error = run(make_tools(store), "edit_task", arguments)

    assert (data, is_error) == ({"ok": True, "task": task}, False)
    assert store.calls == [call]


@pytest.mark.parametrize(
    "arguments",
    [
        {"id": "t1"},
        {"id": "t1", "text": None},
        {"id": "t1", "due": None},
        {"id": "t1", "text": None, "due": None, "clear_due": False},
    ],
)
def test_edit_task_without_a_change_is_an_error_result(arguments):
    store = FakeStore()

    data, is_error = run(make_tools(store), "edit_task", arguments)

    assert (data, is_error) == (
        invalid("edit_task", "isi text, due, atau clear_due yang mau diubah."),
        True,
    )
    assert store.calls == []


def test_edit_task_checks_the_new_date():
    store = FakeStore()

    data, is_error = run(make_tools(store), "edit_task", {"id": "t1", "due": "Senin"})

    assert (data, is_error) == (
        invalid("edit_task", "'due' harus tanggal dengan format YYYY-MM-DD."),
        True,
    )
    assert store.calls == []


def test_the_store_refusing_an_edit_is_an_error_result_with_its_message():
    store = FakeStore(error=TodoError("Tugasnya terlalu panjang, paling banyak 200 karakter."))

    data, is_error = run(make_tools(store), "edit_task", {"id": "t1", "text": "x" * 300})

    assert (data, is_error) == (
        {"ok": False, "message": "Tugasnya terlalu panjang, paling banyak 200 karakter."},
        True,
    )


# --- bad calls ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments", "problem"),
    [
        ("add_task", None, "argumen harus berupa objek JSON."),
        ("add_task", "beli susu", "argumen harus berupa objek JSON."),
        ("add_task", {}, "'text' wajib diisi dengan teks."),
        ("add_task", {"text": ""}, "'text' wajib diisi dengan teks."),
        ("add_task", {"text": " \n "}, "'text' wajib diisi dengan teks."),
        ("add_task", {"text": 42}, "'text' wajib diisi dengan teks."),
        ("add_task", {"text": None}, "'text' wajib diisi dengan teks."),
        ("add_task", {"text": "beli susu", "time": "15:00"}, "argumen tidak dikenal: time."),
        ("list_tasks", None, "argumen harus berupa objek JSON."),
        ("list_tasks", {"include_done": "ya"}, "'include_done' harus true atau false."),
        ("list_tasks", {"include_done": 1}, "'include_done' harus true atau false."),
        ("list_tasks", {"overdue": True}, "argumen tidak dikenal: overdue."),
        ("complete_task", [], "argumen harus berupa objek JSON."),
        ("complete_task", {"id": "t1", "text": "x"}, "argumen tidak dikenal: text."),
        ("remove_task", {"id": "t1", "due": None}, "argumen tidak dikenal: due."),
        ("edit_task", {"id": "t1", "text": ""}, "'text' wajib diisi dengan teks."),
        ("edit_task", {"id": "t1", "text": 5}, "'text' wajib diisi dengan teks."),
        ("edit_task", {"id": "t1", "done": True}, "argumen tidak dikenal: done."),
        ("edit_task", {"id": "t1", "clear_due": "ya"}, "'clear_due' harus true atau false."),
        (
            "edit_task",
            {"id": "t1", "due": "2026-10-19", "clear_due": True},
            "isi due atau clear_due, jangan keduanya.",
        ),
    ],
)
def test_invalid_arguments_are_error_results_that_leave_the_list_alone(name, arguments, problem):
    store = FakeStore()

    data, is_error = run(make_tools(store), name, arguments)

    assert (data, is_error) == (invalid(name, problem), True)
    assert store.calls == []


def test_another_tools_name_is_an_error_result():
    data, is_error = run(make_tools(), "list_schedules", {})

    assert (data, is_error) == (
        {"ok": False, "message": "Tool 'list_schedules' bukan tool daftar tugas."},
        True,
    )


VALID_CALLS = [
    ("add_task", {"text": "beli susu"}),
    ("list_tasks", {"include_done": True}),
    ("complete_task", {"id": "t1"}),
    ("reopen_task", {"id": "t3"}),
    ("remove_task", {"id": "t1"}),
    ("edit_task", {"id": "t1", "clear_due": True}),
]


@pytest.mark.parametrize(("name", "arguments"), VALID_CALLS, ids=[c[0] for c in VALID_CALLS])
def test_a_failing_store_is_logged_and_an_error_result_never_an_exception(name, arguments, caplog):
    store = FakeStore(error=OSError("the disk hiccupped"))

    with caplog.at_level(logging.ERROR, logger="waifu.brain.todo_tools"):
        data, is_error = run(make_tools(store), name, arguments)

    assert (data, is_error) == (
        {"ok": False, "message": "Daftar tugas tidak bisa dibuka saat ini."},
        True,
    )
    assert "the disk hiccupped" in caplog.text
