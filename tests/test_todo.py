import dataclasses
import json
import logging
import threading
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from waifu import todo
from waifu.todo import KEEP, Task, TodoError, TodoStore

WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 17, 9, 30, 15, 123456, tzinfo=WIB)  # a Saturday
TODAY = NOW.date()
STAMP = NOW.replace(microsecond=0)
CORRUPT_NAME = "todo.json.corrupt-20261017-093015"


class Clock:
    """A settable aware clock."""

    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


def make_store(tmp_path, clock=None, **limits):
    settings = {"max_tasks": 100, "max_chars": 200, "keep_done_days": 7} | limits
    return TodoStore(tmp_path / "todo.json", now=clock or Clock(), **settings)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def saved(store):
    return read_json(store.path)


def write_todo(path, tasks, next_id):
    path.write_text(
        json.dumps({"version": 1, "next_id": next_id, "tasks": tasks}), encoding="utf-8"
    )


def entry(task_id, text, due=None, created="2026-10-16T08:00:00+07:00", done=None):
    return {"id": task_id, "text": text, "due": due, "created": created, "done": done}


def texts(tasks):
    return [task.text for task in tasks]


def test_a_task_has_a_text_an_optional_due_date_and_whether_it_is_done():
    task = Task("t1", "beli susu", date(2026, 10, 18), datetime(2026, 10, 17, 9, tzinfo=UTC), None)

    assert (task.id, task.text, task.due, task.done) == (
        "t1",
        "beli susu",
        date(2026, 10, 18),
        None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        task.text = "lain"  # type: ignore[misc]


# --- reading -----------------------------------------------------------------------------------


def test_a_missing_file_is_an_empty_list_and_no_call_creates_it(tmp_path):
    folder = tmp_path / "data"
    store = TodoStore(
        folder / "todo.json", max_tasks=100, max_chars=200, keep_done_days=7, now=Clock()
    )

    assert store.open_tasks() == []
    assert store.done_tasks() == []
    assert store.due_or_overdue(TODAY) == []
    assert store.complete("t1") is None
    assert store.reopen("t1") is None
    assert store.remove("t1") is None
    assert store.edit("t1", text="beli susu") is None
    assert not folder.exists()


def test_the_store_exposes_its_path_and_limits(tmp_path):
    store = make_store(tmp_path, max_tasks=7, max_chars=150, keep_done_days=3)

    assert store.path == tmp_path / "todo.json"
    assert (store.max_tasks, store.max_chars, store.keep_done_days) == (7, 150, 3)


def test_every_call_rereads_a_file_edited_by_hand(tmp_path):
    store = make_store(tmp_path)
    write_todo(store.path, [entry("t1", "beli susu")], next_id=2)
    assert texts(store.open_tasks()) == ["beli susu"]

    write_todo(store.path, [entry("t1", "beli teh")], next_id=2)

    assert texts(store.open_tasks()) == ["beli teh"]


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    # Notepad and some editors put a UTF-8 BOM in front of a file they save.
    store = make_store(tmp_path)
    content = json.dumps({"version": 1, "next_id": 2, "tasks": [entry("t1", "beli susu")]})
    store.path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    assert texts(store.open_tasks()) == ["beli susu"]


def test_tasks_are_read_back_with_dates_and_aware_times(tmp_path):
    store = make_store(tmp_path)
    write_todo(
        store.path,
        [
            entry("t4", "kerjakan laporan", due="2026-10-23"),
            entry("t5", "beli susu", done="2026-10-17T07:05:00+07:00"),
        ],
        next_id=6,
    )

    created = datetime(2026, 10, 16, 8, tzinfo=WIB)
    assert store.open_tasks() == [Task("t4", "kerjakan laporan", date(2026, 10, 23), created, None)]
    assert store.done_tasks() == [
        Task("t5", "beli susu", None, created, datetime(2026, 10, 17, 7, 5, tzinfo=WIB))
    ]


# --- add ---------------------------------------------------------------------------------------


def test_add_returns_the_first_task_stamped_to_the_second(tmp_path):
    store = make_store(tmp_path)

    task = store.add("beli susu")

    assert task == Task("t1", "beli susu", None, STAMP, None)
    assert store.open_tasks() == [task]


def test_add_keeps_a_due_date(tmp_path):
    store = make_store(tmp_path)

    task = store.add("kerjakan laporan", due=date(2026, 10, 23))

    assert task.due == date(2026, 10, 23)
    assert store.open_tasks() == [task]


def test_add_writes_the_file_in_readable_utf8_with_dates_and_offsets(tmp_path):
    store = make_store(tmp_path)

    store.add("beli tiket konser 夜に駆ける", due=date(2026, 10, 23))

    text = store.path.read_text(encoding="utf-8")
    assert "夜に駆ける" in text
    assert json.loads(text) == {
        "version": 1,
        "next_id": 2,
        "tasks": [
            {
                "id": "t1",
                "text": "beli tiket konser 夜に駆ける",
                "due": "2026-10-23",
                "created": "2026-10-17T09:30:15+07:00",
                "done": None,
            }
        ],
    }


def test_add_creates_the_parent_folder_on_the_first_write(tmp_path):
    path = tmp_path / "data" / "nested" / "todo.json"

    TodoStore(path, max_tasks=100, max_chars=200, keep_done_days=7, now=Clock()).add("beli susu")

    assert path.is_file()


def test_ids_count_up(tmp_path):
    store = make_store(tmp_path)

    ids = [store.add(text).id for text in ["beli susu", "cuci motor", "bayar listrik"]]

    assert ids == ["t1", "t2", "t3"]


def test_text_is_stripped_and_its_whitespace_collapsed(tmp_path):
    store = make_store(tmp_path)

    task = store.add("  kerjakan\n laporan\t\tbulanan　Pak Budi\r\n ")

    assert task.text == "kerjakan laporan bulanan Pak Budi"
    assert texts(store.open_tasks()) == ["kerjakan laporan bulanan Pak Budi"]


def test_the_same_text_may_be_added_twice(tmp_path):
    store = make_store(tmp_path)
    store.add("beli susu")

    assert store.add("beli susu").id == "t2"


@pytest.mark.parametrize("text", ["", "   ", "\n\t 　"])
def test_empty_text_is_refused(tmp_path, text):
    store = make_store(tmp_path)

    with pytest.raises(TodoError, match="kosong"):
        store.add(text)

    assert not store.path.exists()


def test_text_up_to_max_chars_is_saved(tmp_path):
    store = make_store(tmp_path, max_chars=20)

    assert store.add("  " + "a" * 20 + "  ").text == "a" * 20


def test_text_longer_than_max_chars_is_refused_without_echoing_it(tmp_path):
    store = make_store(tmp_path, max_chars=20)
    text = "kerjakan laporan bulanan"

    with pytest.raises(TodoError, match="20 karakter") as caught:
        store.add(text)

    assert text not in str(caught.value)
    assert not store.path.exists()


def test_secret_text_is_refused_without_echoing_it(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(TodoError, match="rahasia") as caught:
        store.add("ganti password wifi jadi kucing123")

    assert "kucing123" not in str(caught.value)
    assert not store.path.exists()


@pytest.mark.parametrize(
    "text",
    ["ganti PIN ATM", "ganti password wifi", "minta kode OTP ke bank", "beli token"],
)
def test_a_task_that_only_names_a_secret_is_refused_too(text, tmp_path):
    # The README says so: the word alone is refused, without the secret itself.
    store = make_store(tmp_path)

    with pytest.raises(TodoError, match="rahasia"):
        store.add(text)

    assert not store.path.exists()


@pytest.mark.parametrize(
    "text", ["beli token listrik", "isi token PLN", "ganti nomor rahasia ATM", "ganti kunci wifi"]
)
def test_the_wordings_the_readme_suggests_instead_are_saved(text, tmp_path):
    assert make_store(tmp_path).add(text).text == text


def test_add_is_refused_when_max_tasks_are_open_and_says_what_to_do(tmp_path):
    store = make_store(tmp_path, max_tasks=2)
    store.add("beli susu")
    store.add("cuci motor")

    with pytest.raises(TodoError, match="penuh") as caught:
        store.add("bayar listrik")

    assert "hapus" in str(caught.value)
    assert texts(store.open_tasks()) == ["beli susu", "cuci motor"]


def test_done_tasks_leave_room_for_new_ones(tmp_path):
    store = make_store(tmp_path, max_tasks=2)
    store.add("beli susu")
    store.add("cuci motor")
    store.complete("t1")

    assert store.add("bayar listrik").id == "t3"


def test_todo_error_is_an_exception_with_its_message():
    assert issubclass(TodoError, Exception)
    assert str(TodoError("Tugasnya kosong.")) == "Tugasnya kosong."


def test_a_time_given_as_the_due_date_is_refused(tmp_path):
    # The file holds days: a datetime would be written as a time and spoil it.
    store = make_store(tmp_path)

    with pytest.raises(ValueError, match="date"):
        store.add("beli susu", due=NOW)

    assert not store.path.exists()


# --- ordering ----------------------------------------------------------------------------------


def test_open_tasks_come_overdue_first_then_by_due_date_then_undated_by_creation(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    for text, due in [
        ("tanpa tenggat pertama", None),
        ("minggu depan", TODAY + timedelta(days=7)),
        ("telat dua hari", TODAY - timedelta(days=2)),
        ("tanpa tenggat kedua", None),
        ("hari ini", TODAY),
        ("telat lima hari", TODAY - timedelta(days=5)),
        ("besok", TODAY + timedelta(days=1)),
    ]:
        store.add(text, due=due)
        clock.advance(minutes=1)

    assert texts(store.open_tasks()) == [
        "telat lima hari",
        "telat dua hari",
        "hari ini",
        "besok",
        "minggu depan",
        "tanpa tenggat pertama",
        "tanpa tenggat kedua",
    ]


def test_tasks_due_the_same_day_come_in_the_order_they_were_added(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    for text in ["pertama", "kedua", "ketiga"]:
        store.add(text, due=TODAY)
        clock.advance(seconds=1)

    assert texts(store.open_tasks()) == ["pertama", "kedua", "ketiga"]


def test_tasks_without_a_due_date_follow_their_creation_time_not_their_id(tmp_path):
    store = make_store(tmp_path)
    write_todo(
        store.path,
        [
            entry("t1", "dibuat belakangan", created="2026-10-16T10:00:00+07:00"),
            entry("t2", "dibuat duluan", created="2026-10-16T09:00:00+07:00"),
        ],
        next_id=3,
    )

    assert texts(store.open_tasks()) == ["dibuat duluan", "dibuat belakangan"]


def test_done_tasks_come_newest_first_and_only_there(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    for text in ["beli susu", "cuci motor", "bayar listrik"]:
        store.add(text)
    store.complete("t2")
    clock.advance(minutes=5)
    store.complete("t1")

    assert texts(store.done_tasks()) == ["beli susu", "cuci motor"]
    assert texts(store.open_tasks()) == ["bayar listrik"]


# --- due_or_overdue ----------------------------------------------------------------------------


def test_due_or_overdue_gives_the_open_tasks_due_by_that_day_overdue_first(tmp_path):
    store = make_store(tmp_path)
    day = date(2026, 10, 20)  # another day than the clock's: the caller's today counts
    store.add("tanpa tenggat")
    store.add("tenggat tanggal 20", due=day)
    store.add("tenggat tanggal 21", due=day + timedelta(days=1))
    store.add("telat sejak tanggal 18", due=day - timedelta(days=2))
    store.add("selesai, tenggat tanggal 19", due=day - timedelta(days=1))
    store.complete("t5")

    assert texts(store.due_or_overdue(day)) == ["telat sejak tanggal 18", "tenggat tanggal 20"]


# --- complete and reopen -----------------------------------------------------------------------


def test_complete_marks_the_task_done_now_and_returns_it(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    task = store.add("beli susu", due=TODAY)
    clock.advance(hours=2)

    done = store.complete("t1")

    assert done == dataclasses.replace(task, done=STAMP + timedelta(hours=2))
    assert store.done_tasks() == [done]
    assert store.open_tasks() == []


def test_complete_of_an_unknown_id_gives_none_and_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    store.add("beli susu")
    before = store.path.read_bytes()

    assert store.complete("t9") is None
    assert store.path.read_bytes() == before


def test_completing_a_done_task_again_keeps_when_it_was_done(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add("beli susu")
    done = store.complete("t1")
    before = store.path.read_bytes()
    clock.advance(hours=1)

    assert store.complete("t1") == done
    assert store.path.read_bytes() == before


def test_reopen_makes_a_done_task_open_again(tmp_path):
    store = make_store(tmp_path)
    task = store.add("kerjakan laporan", due=TODAY)
    store.complete("t1")

    assert store.reopen("t1") == task
    assert store.open_tasks() == [task]
    assert store.done_tasks() == []


def test_reopen_of_an_open_task_returns_it_and_of_an_unknown_id_none(tmp_path):
    store = make_store(tmp_path)
    task = store.add("beli susu")
    before = store.path.read_bytes()

    assert store.reopen("t1") == task
    assert store.reopen("t9") is None
    assert store.path.read_bytes() == before


def test_reopen_is_refused_when_max_tasks_are_open(tmp_path):
    store = make_store(tmp_path, max_tasks=1)
    store.add("beli susu")
    done = store.complete("t1")
    store.add("cuci motor")

    with pytest.raises(TodoError, match="penuh"):
        store.reopen("t1")

    assert store.done_tasks() == [done]


# --- remove ------------------------------------------------------------------------------------


def test_remove_deletes_an_open_or_a_done_task_and_returns_it(tmp_path):
    store = make_store(tmp_path)
    milk = store.add("beli susu")
    store.add("cuci motor")
    done = store.complete("t2")

    assert store.remove("t1") == milk
    assert store.remove("t2") == done
    assert store.open_tasks() == []
    assert store.done_tasks() == []


def test_remove_of_an_unknown_id_gives_none_and_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    store.add("beli susu")
    before = store.path.read_bytes()

    assert store.remove("t9") is None
    assert store.path.read_bytes() == before


# --- edit --------------------------------------------------------------------------------------


def test_edit_changes_the_text_and_keeps_the_rest(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    task = store.add("kerjakan laporan", due=TODAY)
    clock.advance(days=1)

    edited = store.edit("t1", text="  kerjakan\nlaporan   bulanan ")

    assert edited == dataclasses.replace(task, text="kerjakan laporan bulanan")
    assert store.open_tasks() == [edited]


def test_edit_moves_the_due_date(tmp_path):
    store = make_store(tmp_path)
    task = store.add("kerjakan laporan", due=TODAY)

    edited = store.edit("t1", due=date(2026, 10, 19))

    assert edited == dataclasses.replace(task, due=date(2026, 10, 19))
    assert store.open_tasks() == [edited]


def test_edit_keeps_the_due_date_unless_given_and_none_clears_it(tmp_path):
    store = make_store(tmp_path)
    store.add("kerjakan laporan", due=TODAY)

    assert store.edit("t1", text="kerjakan laporan bulanan").due == TODAY
    assert store.edit("t1", text="kerjakan laporan akhir", due=KEEP).due == TODAY
    assert store.edit("t1", due=None).due is None
    assert store.open_tasks()[0].due is None


def test_edit_can_set_a_due_date_on_a_task_without_one(tmp_path):
    store = make_store(tmp_path)
    store.add("beli susu")

    assert store.edit("t1", due=TODAY).due == TODAY


@pytest.mark.parametrize(
    ("text", "match"),
    [("  ", "kosong"), ("x" * 21, "20 karakter"), ("PIN ATM 4321", "rahasia")],
)
def test_edit_checks_the_text_like_add(tmp_path, text, match):
    store = make_store(tmp_path, max_chars=20)
    store.add("beli susu")
    before = store.path.read_bytes()

    with pytest.raises(TodoError, match=match):
        store.edit("t1", text=text)

    assert store.path.read_bytes() == before


def test_edit_refuses_a_time_as_the_due_date(tmp_path):
    store = make_store(tmp_path)
    store.add("beli susu")
    before = store.path.read_bytes()

    with pytest.raises(ValueError, match="date"):
        store.edit("t1", due=NOW)

    assert store.path.read_bytes() == before


def test_edit_of_an_unknown_id_gives_none(tmp_path):
    store = make_store(tmp_path)
    store.add("beli susu")

    assert store.edit("t9", text="beli teh") is None
    assert texts(store.open_tasks()) == ["beli susu"]


def test_edit_without_a_change_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    task = store.add("beli susu", due=TODAY)
    before = store.path.read_bytes()

    assert store.edit("t1", text="beli  susu", due=TODAY) == task
    assert store.edit("t1") == task
    assert store.path.read_bytes() == before


def test_a_done_task_can_be_edited_and_stays_done(tmp_path):
    store = make_store(tmp_path)
    store.add("beli susu")
    done = store.complete("t1")

    assert store.edit("t1", text="beli susu dan roti") == dataclasses.replace(
        done, text="beli susu dan roti"
    )


# --- ids are never reused ----------------------------------------------------------------------


def test_a_removed_id_is_never_reused(tmp_path):
    store = make_store(tmp_path)
    store.add("beli susu")
    store.add("cuci motor")
    store.remove("t2")

    assert store.add("bayar listrik").id == "t3"


def test_ids_are_never_reused_after_a_restart(tmp_path):
    first = make_store(tmp_path)
    first.add("beli susu")
    first.add("cuci motor")
    first.remove("t1")
    first.remove("t2")

    assert make_store(tmp_path).add("bayar listrik").id == "t3"
    assert saved(first)["next_id"] == 4


def test_an_id_added_by_hand_beyond_next_id_is_never_reused(tmp_path):
    store = make_store(tmp_path)
    write_todo(store.path, [entry("t1", "beli susu"), entry("t7", "cuci motor")], next_id=3)

    assert store.add("bayar listrik").id == "t8"


def test_the_id_of_a_forgotten_done_task_is_never_reused(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock, keep_done_days=1)
    store.add("beli susu")
    store.complete("t1")
    clock.advance(days=2)

    assert store.add("cuci motor").id == "t2"


# --- done tasks are forgotten ------------------------------------------------------------------


def test_a_done_task_is_kept_keep_done_days_and_then_forgotten(tmp_path):
    clock = Clock(STAMP)  # on a whole second, like the stamp
    store = make_store(tmp_path, clock, keep_done_days=7)
    store.add("beli susu")
    done = store.complete("t1")

    clock.advance(days=7)
    assert store.done_tasks() == [done]

    clock.advance(seconds=1)
    assert store.done_tasks() == []
    assert store.complete("t1") is None


def test_a_forgotten_done_task_leaves_the_file_at_the_next_write(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock, keep_done_days=7)
    store.add("beli susu")
    store.complete("t1")
    clock.advance(days=8)

    assert store.done_tasks() == []
    assert [task["id"] for task in saved(store)["tasks"]] == ["t1"]  # reading never writes

    store.add("cuci motor")

    assert [task["id"] for task in saved(store)["tasks"]] == ["t2"]


def test_with_keep_done_days_zero_a_done_task_is_forgotten_at_once(tmp_path):
    store = make_store(tmp_path, keep_done_days=0)
    store.add("beli susu")

    assert store.complete("t1") is not None
    assert store.done_tasks() == []


def test_open_tasks_are_never_forgotten(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock, keep_done_days=0)
    store.add("kerjakan laporan", due=TODAY)
    clock.advance(days=400)

    assert texts(store.open_tasks()) == ["kerjakan laporan"]


# --- aware times only --------------------------------------------------------------------------

CALLS = {
    "add": lambda store: store.add("cuci motor"),
    "open_tasks": lambda store: store.open_tasks(),
    "done_tasks": lambda store: store.done_tasks(),
    "due_or_overdue": lambda store: store.due_or_overdue(TODAY),
    "complete": lambda store: store.complete("t1"),
    "reopen": lambda store: store.reopen("t1"),
    "remove": lambda store: store.remove("t1"),
    "edit": lambda store: store.edit("t1", text="beli teh"),
}


@pytest.mark.parametrize("call", CALLS.values(), ids=CALLS.keys())
def test_a_naive_clock_is_refused_before_it_could_spoil_the_file(tmp_path, call):
    make_store(tmp_path).add("beli susu")
    store = make_store(tmp_path, Clock(NOW.replace(tzinfo=None)))
    before = store.path.read_bytes()

    with pytest.raises(ValueError, match="aware"):
        call(store)

    assert store.path.read_bytes() == before


# --- several stores and threads ----------------------------------------------------------------


def test_a_second_store_on_the_same_file_sees_changes_at_once(tmp_path):
    cyrene = make_store(tmp_path)
    terminal = make_store(tmp_path)

    cyrene.add("beli susu")
    assert texts(terminal.open_tasks()) == ["beli susu"]

    terminal.complete("t1")
    assert texts(cyrene.done_tasks()) == ["beli susu"]
    assert cyrene.add("cuci motor").id == "t2"


def run_in_threads(target, count=8):
    errors = []

    def guarded(worker):
        try:
            target(worker)
        except Exception as exc:  # surfaced by the caller's assertion
            errors.append(exc)

    threads = [threading.Thread(target=guarded, args=(worker,)) for worker in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_concurrent_adds_from_two_stores_keep_every_task(tmp_path):
    stores = [make_store(tmp_path), make_store(tmp_path)]

    def add(worker):
        for index in range(5):
            stores[worker % 2].add(f"tugas {worker}-{index}")

    assert run_in_threads(add) == []
    tasks = stores[0].open_tasks()
    assert sorted(task.id for task in tasks) == sorted(f"t{n}" for n in range(1, 41))
    assert len(set(texts(tasks))) == 40
    assert saved(stores[0])["next_id"] == 41


def test_concurrent_changes_from_two_stores_lose_none_of_them(tmp_path):
    stores = [make_store(tmp_path), make_store(tmp_path)]
    for index in range(40):
        stores[0].add(f"tugas {index}")

    def change(worker):
        for number in range(worker + 1, 41, 8):
            store = stores[worker % 2]
            if number % 2:
                store.complete(f"t{number}")
            else:
                store.edit(f"t{number}", due=TODAY)

    assert run_in_threads(change) == []
    assert sorted(int(task.id[1:]) for task in stores[0].done_tasks()) == list(range(1, 41, 2))
    assert all(task.due == TODAY for task in stores[0].open_tasks())
    assert len(stores[0].open_tasks()) == 20


# --- atomic writes -----------------------------------------------------------------------------


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    store = make_store(tmp_path)

    store.add("beli susu")

    assert [path.name for path in tmp_path.iterdir()] == ["todo.json"]


def test_a_failed_replace_keeps_the_previous_file_intact(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add("beli susu")
    before = store.path.read_bytes()

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(todo.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        store.add("cuci motor")

    assert store.path.read_bytes() == before
    assert [path.name for path in tmp_path.iterdir()] == ["todo.json"]


def test_new_tasks_reach_the_disk_before_the_file_is_replaced(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add("beli susu")
    events = []
    real_fsync, real_replace = todo.os.fsync, todo.os.replace

    def recording_fsync(fd):
        real_fsync(fd)
        (temp_file,) = [path for path in tmp_path.iterdir() if path.name != "todo.json"]
        events.append(("fsync", [task["id"] for task in read_json(temp_file)["tasks"]]))

    def recording_replace(src, dst):
        events.append(("replace", None))
        real_replace(src, dst)

    monkeypatch.setattr(todo.os, "fsync", recording_fsync)
    monkeypatch.setattr(todo.os, "replace", recording_replace)

    store.add("cuci motor")

    assert events == [("fsync", ["t1", "t2"]), ("replace", None)]


def test_a_windows_path_with_spaces_works(tmp_path):
    path = tmp_path / "Data Cyrene" / "daftar tugas" / "tugas Master.json"
    settings = {"max_tasks": 100, "max_chars": 200, "keep_done_days": 7}

    TodoStore(path, now=Clock(), **settings).add("beli susu")

    assert texts(TodoStore(path, now=Clock(), **settings).open_tasks()) == ["beli susu"]


# --- corrupt files -----------------------------------------------------------------------------

VALID = entry("t1", "beli susu")


def todo_bytes(tasks, next_id=2, **changes):
    return json.dumps({"version": 1, "next_id": next_id, "tasks": tasks} | changes).encode()


CORRUPT_CONTENTS = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version": 1, "next_id": 2, "tasks": [\xff\xfe]}',
    "a list": b"[]",
    "missing tasks": json.dumps({"version": 1, "next_id": 2}).encode(),
    "an extra key": todo_bytes([], notes=[]),
    "another version": todo_bytes([], version=2),
    "version true": todo_bytes([], version=True),
    "next_id text": todo_bytes([], next_id="2"),
    "next_id true": todo_bytes([], next_id=True),
    "next_id zero": todo_bytes([], next_id=0),
    "tasks an object": todo_bytes({}),
    "task a string": todo_bytes(["beli susu"]),
    "task without done": todo_bytes([{k: v for k, v in VALID.items() if k != "done"}]),
    "task with an extra key": todo_bytes([VALID | {"priority": 1}]),
    "numeric id": todo_bytes([VALID | {"id": 1}]),
    "a memory id": todo_bytes([VALID | {"id": "m1"}]),
    "id t0": todo_bytes([VALID | {"id": "t0"}]),
    "id with a newline": todo_bytes([VALID | {"id": "t1\n"}]),
    "numeric text": todo_bytes([VALID | {"text": 42}]),
    "null text": todo_bytes([VALID | {"text": None}]),
    "due a weekday": todo_bytes([VALID | {"due": "Jumat"}]),
    "due without dashes": todo_bytes([VALID | {"due": "20261023"}]),
    "due a day that does not exist": todo_bytes([VALID | {"due": "2026-02-30"}]),
    "due a time": todo_bytes([VALID | {"due": "2026-10-23T09:00:00+07:00"}]),
    "due a number": todo_bytes([VALID | {"due": 20261023}]),
    "created not a time": todo_bytes([VALID | {"created": "kemarin"}]),
    "created without an offset": todo_bytes([VALID | {"created": "2026-10-16T08:00:00"}]),
    "created null": todo_bytes([VALID | {"created": None}]),
    "done without an offset": todo_bytes([VALID | {"done": "2026-10-17T07:00:00"}]),
    "done a number": todo_bytes([VALID | {"done": 20261017}]),
    "done true": todo_bytes([VALID | {"done": True}]),
    "duplicate ids": todo_bytes([VALID, VALID | {"text": "cuci motor"}], next_id=3),
}


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
def test_a_corrupt_file_is_moved_aside_logged_and_the_list_starts_empty(tmp_path, caplog, content):
    store = make_store(tmp_path)
    store.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.todo"):
        assert store.open_tasks() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert not store.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_a_corrupt_file_is_never_overwritten_by_a_later_task(tmp_path):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    task = store.add("beli susu")

    assert task.id == "t1"
    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"{not json"
    assert texts(store.open_tasks()) == ["beli susu"]


def test_a_corrupt_copy_with_the_same_stamp_gets_a_counter(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / CORRUPT_NAME).write_bytes(b"first")
    (tmp_path / f"{CORRUPT_NAME}-2").write_bytes(b"second")
    store.path.write_bytes(b"third")

    assert store.open_tasks() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"first"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_bytes() == b"second"
    assert (tmp_path / f"{CORRUPT_NAME}-3").read_bytes() == b"third"


def test_nothing_is_written_when_a_corrupt_file_cannot_be_moved(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(todo.os, "rename", failing_rename)
    with pytest.raises(OSError, match="file in use"):
        store.add("beli susu")

    assert store.path.read_bytes() == b"{not json"
    assert [path.name for path in tmp_path.iterdir()] == ["todo.json"]
