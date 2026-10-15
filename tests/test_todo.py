import dataclasses
from datetime import UTC, date, datetime

import pytest

from waifu.todo import Task


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
