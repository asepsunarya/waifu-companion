"""The focus log: the totals per period and the store.

Times are aware WIB from Saturday 2026-10-17 (`at(hour)`: 24.5 is Sunday 00:30); no test reads
the owner's data folder (stores live in tmp_path).
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from waifu.finance.values import WIB
from waifu.focus import FocusBlock, FocusStore, FocusTotal, focus_totals

DAY = datetime(2026, 10, 17, tzinfo=WIB)  # a Saturday


def at(hour: float) -> datetime:
    """`hour` hours after Saturday 00:00 WIB, to the second."""
    return DAY + timedelta(seconds=round(hour * 3600))


# --- totals -----------------------------------------------------------------------------------


def test_every_block_inside_the_period_counts_whole():
    blocks = [FocusBlock(at(8), at(8.5)), FocusBlock(at(9), at(9.25))]

    assert focus_totals(blocks, at(0), at(22)) == FocusTotal(45 * 60, 2)


def test_a_block_is_cut_at_the_period_s_edges():
    blocks = [FocusBlock(at(23.5), at(24.5))]

    assert focus_totals(blocks, at(0), at(24)) == FocusTotal(30 * 60, 1)
    assert focus_totals(blocks, at(24), at(48)) == FocusTotal(30 * 60, 1)


def test_blocks_outside_the_period_or_only_touching_it_count_nothing():
    blocks = [FocusBlock(at(-1), at(0)), FocusBlock(at(22), at(23))]

    assert focus_totals(blocks, at(0), at(22)) == FocusTotal(0, 0)


# --- the store --------------------------------------------------------------------------------


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_store(path: Path, clock: Clock | None = None, *, keep_days: int = 90) -> FocusStore:
    return FocusStore(path, keep_days=keep_days, now=clock or Clock(at(22)))


def test_a_missing_file_is_an_empty_log_and_stays_missing(tmp_path):
    path = tmp_path / "data" / "focus.json"
    store = make_store(path)

    assert store.blocks() == ()
    assert store.totals(at(0), at(22)) == FocusTotal(0, 0)
    assert not path.exists()


def test_adding_writes_the_block_as_aware_wib_times(tmp_path):
    path = tmp_path / "data" / "focus.json"
    store = make_store(path)

    store.add(FocusBlock(at(20), at(20) + timedelta(minutes=25)))

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "blocks": [{"start": "2026-10-17T20:00:00+07:00", "end": "2026-10-17T20:25:00+07:00"}],
    }
    assert store.blocks() == (FocusBlock(at(20), at(20) + timedelta(minutes=25)),)


def test_blocks_are_kept_by_their_start(tmp_path):
    store = make_store(tmp_path / "focus.json")

    store.add(FocusBlock(at(20), at(20.5)))
    store.add(FocusBlock(at(8), at(8.5)))

    assert store.blocks() == (FocusBlock(at(8), at(8.5)), FocusBlock(at(20), at(20.5)))


def test_totals_count_the_kept_blocks(tmp_path):
    store = make_store(tmp_path / "focus.json")
    store.add(FocusBlock(at(8), at(9)))
    store.add(FocusBlock(at(20), at(20.5)))

    assert store.totals(at(0), at(22)) == FocusTotal(90 * 60, 2)


def test_blocks_older_than_keep_days_are_left_out_and_dropped_at_the_next_write(tmp_path):
    path = tmp_path / "focus.json"
    clock = Clock(at(-24))
    store = make_store(path, clock, keep_days=2)
    old, kept = FocusBlock(at(-30), at(-27)), FocusBlock(at(-26), at(-25))
    store.add(old)
    store.add(kept)

    # Two days before Saturday 22:00 is Thursday 22:00: one ended an hour before, one after.
    clock.now = at(22)
    assert store.blocks() == (kept,)
    assert len(json.loads(path.read_text(encoding="utf-8"))["blocks"]) == 2  # reading keeps

    store.add(FocusBlock(at(21), at(22)))

    starts = [block["start"] for block in json.loads(path.read_text(encoding="utf-8"))["blocks"]]
    assert starts == ["2026-10-15T22:00:00+07:00", "2026-10-17T21:00:00+07:00"]


def test_every_call_reads_the_file_again(tmp_path):
    path = tmp_path / "focus.json"
    writer, reader = make_store(path), make_store(path)
    assert reader.blocks() == ()

    writer.add(FocusBlock(at(8), at(9)))

    assert reader.blocks() == (FocusBlock(at(8), at(9)),)


def test_a_file_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "focus.json"
    make_store(path).add(FocusBlock(at(8), at(9)))
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    assert make_store(path).blocks() == (FocusBlock(at(8), at(9)),)


BLOCK = {"start": "2026-10-17T20:00:00+07:00", "end": "2026-10-17T20:25:00+07:00"}


def stored(**changes) -> dict:
    log: dict[str, object] = {"version": 1, "blocks": [BLOCK]}
    log.update(changes)
    return log


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps([]),
        json.dumps(stored(version=2)),
        json.dumps(stored(version=True)),
        json.dumps(stored(extra=1)),
        json.dumps({"version": 1}),
        json.dumps(stored(blocks={})),
        json.dumps(stored(blocks=[[BLOCK["start"], BLOCK["end"]]])),
        json.dumps(stored(blocks=[{**BLOCK, "extra": 1}])),
        json.dumps(stored(blocks=[{**BLOCK, "start": "2026-10-17T20:00:00"}])),  # naive
        json.dumps(stored(blocks=[{**BLOCK, "end": "yesterday"}])),
        json.dumps(stored(blocks=[{**BLOCK, "end": 1}])),
        json.dumps(stored(blocks=[{**BLOCK, "end": BLOCK["start"]}])),
        json.dumps(stored(blocks=[{**BLOCK, "end": "2026-10-17T19:00:00+07:00"}])),
    ],
)
def test_a_file_that_is_not_a_focus_log_is_moved_aside_and_never_overwritten(
    tmp_path, content, caplog
):
    path = tmp_path / "focus.json"
    path.write_text(content, encoding="utf-8")
    store = make_store(path)

    with caplog.at_level(logging.ERROR, logger="waifu.focus"):
        assert store.blocks() == ()

    [moved] = tmp_path.glob("focus.json.corrupt-*")
    assert moved.name == "focus.json.corrupt-20261017-220000"
    assert moved.read_text(encoding="utf-8") == content
    assert not path.exists()
    assert "not a focus log" in caplog.text


def test_a_broken_file_moved_aside_twice_in_one_second_keeps_both(tmp_path):
    path = tmp_path / "focus.json"
    store = make_store(path)
    for content in ("first", "second"):
        path.write_text(content, encoding="utf-8")
        store.blocks()

    moved = sorted(path.name for path in tmp_path.glob("focus.json.corrupt-*"))
    assert moved == ["focus.json.corrupt-20261017-220000", "focus.json.corrupt-20261017-220000-2"]


def test_adding_over_a_broken_file_moves_it_aside_first(tmp_path):
    path = tmp_path / "focus.json"
    path.write_text("broken", encoding="utf-8")

    make_store(path).add(FocusBlock(at(8), at(9)))

    assert make_store(path).blocks() == (FocusBlock(at(8), at(9)),)
    [moved] = tmp_path.glob("focus.json.corrupt-*")
    assert moved.read_text(encoding="utf-8") == "broken"


def test_a_naive_clock_is_refused(tmp_path):
    store = make_store(tmp_path / "focus.json", Clock(datetime(2026, 10, 17, 22)))

    with pytest.raises(ValueError, match="aware"):
        store.blocks()
