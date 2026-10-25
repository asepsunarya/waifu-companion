import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from waifu.finance.values import WIB
from waifu.network_history import (
    BackTold,
    NetworkRecord,
    NetworkStore,
    OpenOutage,
    Outage,
    OutageTotal,
    outage_totals,
)

pytestmark = pytest.mark.usefixtures("nothing_beyond_this_pc")

DAY = date(2026, 10, 25)


def at(hour: float, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=WIB) + timedelta(hours=hour)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_store(path: Path, clock: Clock | None = None, keep_days: int = 7) -> NetworkStore:
    return NetworkStore(path, keep_days=keep_days, now=clock or Clock(at(22)))


def test_a_missing_file_is_an_empty_history_and_stays_missing(tmp_path):
    path = tmp_path / "data" / "network.json"

    assert make_store(path).record() == NetworkRecord()
    assert not path.exists()


def test_an_outage_begun_is_kept_open_with_its_last_failed_poll(tmp_path):
    path = tmp_path / "data" / "network.json"
    store = make_store(path)

    store.begin(at(21.05), at(21 + 4 / 60), "offline")

    assert store.record() == NetworkRecord(open=OpenOutage(at(21.05), at(21 + 4 / 60), "offline"))
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "outages": [],
        "open": {
            "start": "2026-10-25T21:03:00+07:00",
            "last_failed": "2026-10-25T21:04:00+07:00",
            "kind": "offline",
        },
        "back_told": None,
    }


def test_each_failed_poll_moves_the_open_outage_s_last_failed_poll(tmp_path):
    store = make_store(tmp_path / "network.json")
    store.begin(at(21), at(21), "router")

    store.still_down(at(21.25))

    assert store.record().open == OpenOutage(at(21), at(21.25), "router")


def test_still_down_without_an_open_outage_changes_nothing(tmp_path):
    path = tmp_path / "network.json"

    make_store(path).still_down(at(21))

    assert not path.exists()


def test_ending_the_open_outage_keeps_it_with_its_end(tmp_path):
    store = make_store(tmp_path / "network.json")
    store.begin(at(21.05), at(21.1), "offline")

    ended = store.end(at(21.25))

    assert ended == Outage(at(21.05), at(21.25), "offline")
    assert store.record() == NetworkRecord(outages=(ended,))


def test_ending_without_an_open_outage_gives_none(tmp_path):
    path = tmp_path / "network.json"

    assert make_store(path).end(at(21)) is None
    assert not path.exists()


def test_beginning_while_one_is_open_ends_that_one_at_its_last_failed_poll(tmp_path):
    store = make_store(tmp_path / "network.json")
    store.begin(at(20), at(20.5), "offline")

    store.begin(at(21), at(21.1), "router")

    assert store.record() == NetworkRecord(
        outages=(Outage(at(20), at(20.5), "offline"),),
        open=OpenOutage(at(21), at(21.1), "router"),
    )


def test_the_back_lines_said_are_counted_per_day(tmp_path):
    store = make_store(tmp_path / "network.json")

    assert store.told_back(DAY) == 1
    assert store.told_back(DAY) == 2
    assert store.record().back_told == BackTold(DAY, 2)
    assert store.record().back_count(DAY) == 2
    assert store.record().back_count(DAY + timedelta(days=1)) == 0

    assert store.told_back(DAY + timedelta(days=1)) == 1  # a new day starts again
    assert store.record().back_told == BackTold(DAY + timedelta(days=1), 1)


def test_outages_ended_more_than_keep_days_ago_are_left_out_and_dropped_at_the_next_write(
    tmp_path,
):
    path = tmp_path / "network.json"
    clock = Clock(at(22, DAY - timedelta(days=3)))
    store = make_store(path, clock, keep_days=2)
    old = Outage(at(8, DAY - timedelta(days=3)), at(9, DAY - timedelta(days=3)), "offline")
    kept = Outage(at(23, DAY - timedelta(days=2)), at(23.5, DAY - timedelta(days=2)), "router")
    for outage in (old, kept):
        store.begin(outage.start, outage.start, outage.kind)
        store.end(outage.end)

    clock.now = at(22)
    assert store.record().outages == (kept,)
    assert len(json.loads(path.read_text(encoding="utf-8"))["outages"]) == 2  # reading writes not

    store.told_back(DAY)

    assert len(json.loads(path.read_text(encoding="utf-8"))["outages"]) == 1


def test_every_call_reads_the_file_again(tmp_path):
    path = tmp_path / "network.json"
    writer, reader = make_store(path), make_store(path)
    assert reader.record() == NetworkRecord()

    writer.begin(at(21), at(21), "offline")

    assert reader.record().open is not None


def test_a_file_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "network.json"
    make_store(path).begin(at(21), at(21), "offline")
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    assert make_store(path).record().open == OpenOutage(at(21), at(21), "offline")


def stored(**changes) -> dict:
    record = {
        "version": 1,
        "outages": [
            {
                "start": "2026-10-25T20:00:00+07:00",
                "end": "2026-10-25T20:10:00+07:00",
                "kind": "offline",
            }
        ],
        "open": None,
        "back_told": {"date": "2026-10-25", "count": 1},
    }
    record.update(changes)
    return record


OUTAGE = stored()["outages"][0]
OPEN = {
    "start": "2026-10-25T21:00:00+07:00",
    "last_failed": "2026-10-25T21:05:00+07:00",
    "kind": "router",
}


def test_a_written_history_with_every_part_is_read_back(tmp_path):
    path = tmp_path / "network.json"
    path.write_text(json.dumps(stored(open=OPEN)), encoding="utf-8")

    assert make_store(path).record() == NetworkRecord(
        outages=(Outage(at(20), at(20 + 1 / 6), "offline"),),
        open=OpenOutage(at(21), at(21 + 1 / 12), "router"),
        back_told=BackTold(DAY, 1),
    )


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps([]),
        json.dumps(stored(version=2)),
        json.dumps(stored(extra=1)),
        json.dumps({key: value for key, value in stored().items() if key != "open"}),
        json.dumps(stored(outages={})),
        json.dumps(stored(outages=[{**OUTAGE, "kind": "dns"}])),
        json.dumps(stored(outages=[{**OUTAGE, "kind": ["offline"]}])),
        json.dumps(stored(outages=[{**OUTAGE, "extra": 1}])),
        json.dumps(stored(outages=[{**OUTAGE, "start": "2026-10-25T20:00:00"}])),  # naive
        json.dumps(stored(outages=[{**OUTAGE, "end": "yesterday"}])),
        json.dumps(stored(outages=[{**OUTAGE, "end": "2026-10-25T19:00:00+07:00"}])),
        json.dumps(stored(open=[OPEN])),
        json.dumps(stored(open={**OPEN, "last_failed": "2026-10-25T20:00:00+07:00"})),
        json.dumps(stored(open={**OPEN, "end": OPEN["last_failed"]})),
        json.dumps(stored(back_told={"date": "25-10-2026", "count": 1})),
        json.dumps(stored(back_told={"date": "2026-02-30", "count": 1})),
        json.dumps(stored(back_told={"date": "2026-10-25", "count": 0})),
        json.dumps(stored(back_told={"date": "2026-10-25", "count": True})),
        json.dumps(stored(back_told={"date": "2026-10-25"})),
    ],
)
def test_a_file_that_is_not_a_network_history_is_moved_aside_and_never_overwritten(
    tmp_path, content, caplog
):
    path = tmp_path / "network.json"
    path.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="waifu.network_history"):
        assert make_store(path).record() == NetworkRecord()

    [moved] = tmp_path.glob("network.json.corrupt-*")
    assert moved.name == "network.json.corrupt-20261025-220000"
    assert moved.read_text(encoding="utf-8") == content
    assert not path.exists()
    assert "not a network history" in caplog.text


def test_a_broken_file_moved_aside_twice_in_one_second_keeps_both(tmp_path):
    path = tmp_path / "network.json"
    store = make_store(path)
    for content in ("first", "second"):
        path.write_text(content, encoding="utf-8")
        store.record()

    assert sorted(moved.name for moved in tmp_path.glob("network.json.corrupt-*")) == [
        "network.json.corrupt-20261025-220000",
        "network.json.corrupt-20261025-220000-2",
    ]


def test_writing_over_a_broken_file_moves_it_aside_first(tmp_path):
    path = tmp_path / "network.json"
    path.write_text("broken", encoding="utf-8")

    make_store(path).begin(at(21), at(21), "offline")

    assert make_store(path).record().open == OpenOutage(at(21), at(21), "offline")
    [moved] = tmp_path.glob("network.json.corrupt-*")
    assert moved.read_text(encoding="utf-8") == "broken"


def test_a_naive_clock_is_refused(tmp_path):
    store = make_store(tmp_path / "network.json", Clock(datetime(2026, 10, 25, 22)))

    with pytest.raises(ValueError, match="aware"):
        store.record()


# --- totals ------------------------------------------------------------------------------------


def test_the_totals_count_the_outages_with_time_in_a_span_and_their_seconds_inside_it():
    record = NetworkRecord(
        outages=(
            Outage(at(-0.5), at(0.25), "offline"),  # across midnight: 15 minutes today
            Outage(at(8), at(8.5), "router"),
            Outage(at(-3), at(-2), "offline"),  # yesterday only
        ),
        open=OpenOutage(at(21), at(21.1), "offline"),  # up to its last failed poll
    )

    assert outage_totals(record, at(0), at(22)) == OutageTotal(3, 15 * 60 + 30 * 60 + 6 * 60)
    assert outage_totals(record, at(-24), at(0)) == OutageTotal(2, 30 * 60 + 3600)


def test_the_totals_of_a_span_without_outages_are_zero():
    record = NetworkRecord(outages=(Outage(at(8), at(8.5), "router"),))

    assert outage_totals(record, at(9), at(22)) == OutageTotal(0, 0)
    assert outage_totals(NetworkRecord(), at(0), at(22)) == OutageTotal(0, 0)


def test_an_outage_touching_the_span_only_at_its_edge_is_not_in_it():
    record = NetworkRecord(outages=(Outage(at(8), at(9), "router"),))

    assert outage_totals(record, at(9), at(10)) == OutageTotal(0, 0)
