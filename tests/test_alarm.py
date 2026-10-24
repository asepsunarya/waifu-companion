"""Master's alarm: the ring the scheduler starts and the voice loop says, and its words.

Every bell writes under tmp_path with fake clocks: nothing is played, pushed, or slept.
"""

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from waifu.alarm import (
    REREAD_S,
    AlarmBell,
    AlarmRequest,
    Ring,
    alarm_request,
    missed_text,
    wake_text,
)
from waifu.schedule import AlarmAction, Entry, Reminder, ScheduleError, ScheduleStore

ALARM_AT = datetime(2026, 10, 25, 6, 0)  # a Sunday
START = datetime(2026, 10, 25, 6, 0, 4)
CREATED = datetime(2026, 10, 24, 22, 0)
CORRUPT_NAME = "alarm.json.corrupt-20261025-060004"


class Clock:
    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


class Monotonic:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def alarm_entry(entry_id="j7", at=ALARM_AT, label="berangkat kerja", repeat="none") -> Entry:
    return Entry(entry_id, at, repeat, AlarmAction(label), CREATED)


class Bedroom:
    """A bell on a file and a schedule under tmp_path, with one wall clock and one monotonic
    clock."""

    def __init__(self, tmp_path, *, max_entries=50, **bell_kwargs) -> None:
        self.clock = Clock()
        self.monotonic = Monotonic()
        self.store = ScheduleStore(
            tmp_path / "schedule.json", max_entries=max_entries, now=self.clock
        )
        self.path = tmp_path / "alarm.json"
        self.bell = self.another_bell(**bell_kwargs)

    def another_bell(self, **kwargs) -> AlarmBell:
        """A bell of another process (or a restarted one) on the same file."""
        return AlarmBell(self.path, self.store, now=self.clock, clock=self.monotonic, **kwargs)

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds=seconds)
        self.monotonic.now += seconds

    def file(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


# --- starting a ring ------------------------------------------------------------------------------


def test_nothing_rings_before_an_alarm_starts_and_no_file_is_made(tmp_path):
    room = Bedroom(tmp_path)

    assert room.bell.ringing() is False
    assert room.bell.due_ring() is None
    assert room.bell.ring_state() is None
    assert not room.path.exists()


def test_start_writes_the_ring_file_until_the_minutes_it_may_ring(tmp_path):
    room = Bedroom(tmp_path, max_minutes=10)

    room.bell.start(alarm_entry())

    assert room.file() == {
        "version": 1,
        "started": "2026-10-25T06:00:04",
        "until": "2026-10-25T06:10:04",
        "alarms": [{"id": "j7", "at": "2026-10-25T06:00", "label": "berangkat kerja"}],
    }
    assert room.bell.ringing() is True


def test_an_alarm_without_a_label_is_written_with_a_null_label(tmp_path):
    room = Bedroom(tmp_path)

    room.bell.start(alarm_entry(label=None))

    assert room.file()["alarms"] == [{"id": "j7", "at": "2026-10-25T06:00", "label": None}]


def test_the_first_ring_is_due_at_once_at_the_day_level_and_said_once(tmp_path):
    room = Bedroom(tmp_path, day_level=lambda: 80)
    room.bell.start(alarm_entry())

    first = room.bell.due_ring()
    again = room.bell.due_ring()

    assert first == Ring(0, 80, "Master, bangun! Sudah jam 06.00. Berangkat kerja.")
    assert again is None


def test_each_next_ring_comes_after_its_seconds_twenty_percent_louder_with_another_line(
    tmp_path,
):
    room = Bedroom(tmp_path, repeat_seconds=60)
    room.bell.start(alarm_entry(label=None))
    rings = [room.bell.due_ring()]

    room.advance(59)
    rings.append(room.bell.due_ring())
    for _ in range(3):
        room.advance(1)
        rings.append(room.bell.due_ring())
        room.advance(59)

    assert rings == [
        Ring(0, 100, "Master, bangun! Sudah jam 06.00."),
        None,
        Ring(1, 120, "Ayo bangun, Master! Sudah jam 06.01."),
        Ring(2, 140, "Master, bangun dong! Sudah jam 06.02."),
        Ring(3, 150, "Master, bangun! Sudah jam 06.03."),
    ]


@pytest.mark.parametrize(
    ("day_level", "levels"),
    [(30, [30, 50, 70]), (100, [100, 120, 140]), (140, [140, 150, 150]), (150, [150, 150, 150])],
)
def test_the_level_climbs_from_the_day_level_and_never_passes_150(tmp_path, day_level, levels):
    room = Bedroom(tmp_path, repeat_seconds=60, day_level=lambda: day_level)
    room.bell.start(alarm_entry())

    heard = []
    for _ in range(3):
        heard.append(room.bell.due_ring().level)
        room.advance(60)

    assert heard == levels


def test_the_day_level_is_read_at_each_ring(tmp_path):
    level = {"now": 100}
    room = Bedroom(tmp_path, repeat_seconds=60, day_level=lambda: level["now"])
    room.bell.start(alarm_entry())
    room.bell.due_ring()

    level["now"] = 40
    room.advance(60)

    assert room.bell.due_ring().level == 60


def test_a_ring_missed_while_she_spoke_is_skipped_not_said_late(tmp_path):
    room = Bedroom(tmp_path, repeat_seconds=60)
    room.bell.start(alarm_entry())
    room.bell.due_ring()

    room.advance(130)  # she spoke through ring 1
    late = room.bell.due_ring()
    room.advance(40)
    meanwhile = room.bell.due_ring()

    assert (late.index, late.level) == (2, 140)
    assert meanwhile is None


def test_the_owner_call_opens_and_ends_the_lines_as_written(tmp_path):
    room = Bedroom(tmp_path, repeat_seconds=60, owner_call="sayang")
    room.bell.start(alarm_entry(label=None))

    first = room.bell.due_ring()
    room.advance(60)
    second = room.bell.due_ring()

    assert first.text == "Sayang, bangun! Sudah jam 06.00."
    assert second.text == "Ayo bangun, sayang! Sudah jam 06.01."


def test_a_label_that_ends_its_own_sentence_gets_no_second_period(tmp_path):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry(label="rapat jam 8!"))

    assert room.bell.due_ring().text == "Master, bangun! Sudah jam 06.00. Rapat jam 8!"


def test_the_ring_ends_by_itself_after_its_minutes_and_the_file_goes(tmp_path, caplog):
    room = Bedroom(tmp_path, repeat_seconds=60, max_minutes=10)
    room.bell.start(alarm_entry())
    room.bell.due_ring()

    room.advance(599)
    last = room.bell.due_ring()
    room.advance(1)
    with caplog.at_level(logging.INFO, logger="waifu.alarm"):
        after = room.bell.due_ring()

    assert last.index == 9
    assert after is None
    assert room.bell.ringing() is False
    assert not room.path.exists()
    assert "The alarm rang until 06:10 without being stopped" in caplog.text


def test_a_second_alarm_joins_the_ring_its_label_added_and_the_escalation_going_on(tmp_path):
    room = Bedroom(tmp_path, repeat_seconds=60, max_minutes=10)
    room.bell.start(alarm_entry())
    for _ in range(3):
        room.bell.due_ring()
        room.advance(60)

    room.bell.start(alarm_entry("j8", datetime(2026, 10, 25, 6, 3), "minum obat"))
    joined = room.bell.due_ring()

    assert room.file() == {
        "version": 1,
        "started": "2026-10-25T06:00:04",
        "until": "2026-10-25T06:13:04",
        "alarms": [
            {"id": "j7", "at": "2026-10-25T06:00", "label": "berangkat kerja"},
            {"id": "j8", "at": "2026-10-25T06:03", "label": "minum obat"},
        ],
    }
    assert joined == Ring(3, 150, "Master, bangun! Sudah jam 06.03. Berangkat kerja, minum obat.")


def test_the_same_label_twice_is_said_once(tmp_path):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry())
    room.bell.start(alarm_entry("j8"))

    assert room.bell.due_ring().text == "Master, bangun! Sudah jam 06.00. Berangkat kerja."


def test_starting_an_alarm_that_already_rings_changes_nothing(tmp_path):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry())
    saved = room.path.read_bytes()

    room.advance(120)
    room.bell.start(alarm_entry())

    assert room.path.read_bytes() == saved


# --- stopping and snoozing ------------------------------------------------------------------------


def test_stop_ends_the_ring_and_deletes_the_file(tmp_path, caplog):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry())

    with caplog.at_level(logging.INFO, logger="waifu.alarm"):
        stopped = room.bell.stop()

    assert stopped is True
    assert room.bell.ringing() is False
    assert room.bell.due_ring() is None
    assert not room.path.exists()
    assert "Alarm stopped" in caplog.text


def test_stop_without_a_ring_says_so(tmp_path):
    assert Bedroom(tmp_path).bell.stop() is False


def test_snooze_adds_a_one_shot_alarm_rounded_up_to_a_whole_minute_and_stops(tmp_path):
    room = Bedroom(tmp_path, snooze_minutes=5)
    room.bell.start(alarm_entry())

    snoozed = room.bell.snooze()

    assert snoozed == Entry(
        "j1", datetime(2026, 10, 25, 6, 6), "none", AlarmAction("berangkat kerja"), ALARM_AT
    )
    assert room.store.entries() == [snoozed]
    assert room.bell.ringing() is False
    assert not room.path.exists()


def test_snooze_at_a_whole_minute_waits_exactly_its_minutes(tmp_path):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry())
    room.clock.current = datetime(2026, 10, 25, 6, 1)

    assert room.bell.snooze(10).at == datetime(2026, 10, 25, 6, 11)


def test_snooze_keeps_every_label_joined(tmp_path):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry())
    room.bell.start(alarm_entry("j8", label="minum obat"))

    assert room.bell.snooze().action == AlarmAction("berangkat kerja, minum obat")


def test_snooze_keeps_the_first_label_when_the_joined_ones_are_too_long(tmp_path):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry(label="a" * 60))
    room.bell.start(alarm_entry("j8", label="b" * 60))

    assert room.bell.snooze().action == AlarmAction("a" * 60)


def test_snooze_of_an_alarm_without_a_label_has_none(tmp_path):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry(label=None))

    assert room.bell.snooze().action == AlarmAction()


def test_snooze_without_a_ring_gives_none_and_schedules_nothing(tmp_path):
    room = Bedroom(tmp_path)

    assert room.bell.snooze() is None
    assert room.store.entries() == []


def test_a_snooze_the_schedule_refuses_raises_and_the_ring_goes_on(tmp_path):
    room = Bedroom(tmp_path, max_entries=1)
    room.store.add(datetime(2026, 10, 25, 9, 0), "none", Reminder("Master, rapat!"))
    room.bell.start(alarm_entry())

    with pytest.raises(ScheduleError, match="penuh"):
        room.bell.snooze()

    assert room.bell.ringing() is True
    assert room.bell.due_ring() is not None


def test_a_snooze_whose_ring_cannot_be_ended_takes_its_alarm_back_and_raises(tmp_path, monkeypatch):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry())
    unlink = Path.unlink

    def locked(path, missing_ok=False):
        if path == room.path:
            raise PermissionError(32, "The file is used by another process")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)

    with pytest.raises(PermissionError):
        room.bell.snooze()

    assert room.store.entries() == []
    assert room.bell.ringing() is True


@pytest.mark.parametrize("minutes", [0, 31, -5])
def test_snooze_takes_1_to_30_minutes(tmp_path, minutes):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry())

    with pytest.raises(ValueError, match="1 to 30"):
        room.bell.snooze(minutes)

    assert room.bell.ringing() is True


def test_the_bell_tells_its_snooze_minutes(tmp_path):
    assert Bedroom(tmp_path, snooze_minutes=7).bell.snooze_minutes == 7


# --- the file across processes and restarts -------------------------------------------------------


def test_a_restarted_bell_goes_on_at_the_ring_of_the_time_passed(tmp_path):
    room = Bedroom(tmp_path, repeat_seconds=60, day_level=lambda: 100)
    room.bell.start(alarm_entry())
    room.bell.due_ring()

    room.advance(150)
    restarted = room.another_bell(repeat_seconds=60, day_level=lambda: 100)

    assert restarted.due_ring() == Ring(
        2, 140, "Master, bangun dong! Sudah jam 06.02. Berangkat kerja."
    )
    assert restarted.due_ring() is None


def test_a_stop_by_another_process_is_seen_within_the_reread_time(tmp_path):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry())
    assert room.bell.ringing() is True

    assert room.another_bell().stop() is True
    still_cached = room.bell.ringing()
    room.monotonic.now += REREAD_S

    assert still_cached is True
    assert room.bell.ringing() is False


def test_asking_often_never_keeps_a_state_longer_than_the_reread_time(tmp_path):
    room = Bedroom(tmp_path)
    room.bell.start(alarm_entry())
    room.another_bell().stop()

    seen = []
    for _ in range(3):
        seen.append(room.bell.ringing())
        room.monotonic.now += REREAD_S / 2

    assert seen == [True, True, False]


def test_a_due_ring_reads_the_file_again_so_a_stop_elsewhere_is_never_rung(tmp_path):
    room = Bedroom(tmp_path, repeat_seconds=60)
    room.bell.start(alarm_entry())
    room.bell.due_ring()
    room.clock.advance(seconds=60)  # the wall clock only: the state read stays fresh

    room.another_bell().stop()

    assert room.bell.due_ring() is None


def test_an_expired_ring_left_by_a_stopped_run_is_deleted_at_the_next_read(tmp_path, caplog):
    room = Bedroom(tmp_path, max_minutes=10)
    room.bell.start(alarm_entry())
    room.advance(3600)
    later = room.another_bell()

    with caplog.at_level(logging.INFO, logger="waifu.alarm"):
        assert later.ringing() is False

    assert later.due_ring() is None
    assert not room.path.exists()
    assert "The alarm rang until 06:10 without being stopped" in caplog.text


def test_an_ended_ring_whose_file_cannot_be_deleted_is_logged_once(tmp_path, caplog, monkeypatch):
    room = Bedroom(tmp_path, max_minutes=10)
    room.bell.start(alarm_entry())
    room.advance(3600)
    unlink = Path.unlink

    def locked(path, missing_ok=False):
        if path == room.path:
            raise PermissionError(32, "The file is used by another process")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)
    seen = []
    with caplog.at_level(logging.INFO, logger="waifu.alarm"):
        for _ in range(3):
            seen += [room.bell.ringing(), room.bell.due_ring()]
            room.advance(REREAD_S)
        monkeypatch.undo()
        seen.append(room.bell.ringing())

    assert seen == [False, None] * 3 + [False]
    assert not room.path.exists()
    assert caplog.text.count("The alarm rang until 06:10 without being stopped") == 1
    assert caplog.text.count("Could not delete the ended alarm's file") == 1


def test_a_kept_ring_that_has_ended_is_read_again_before_its_file_is_deleted(tmp_path):
    room = Bedroom(tmp_path, max_minutes=10)
    run, tombol = room.bell, room.another_bell()
    run.start(alarm_entry("j7"))
    room.advance(599.6)  # 06:10:03.6, the ring of j7 ends at 06:10:04
    assert tombol.ringing() is True

    room.advance(0.5)
    run.start(alarm_entry("j8", datetime(2026, 10, 25, 6, 10), label=None))
    room.advance(0.2)  # within the time tombol keeps its read of j7's ring

    assert tombol.ringing() is True
    assert [alarm["id"] for alarm in room.file()["alarms"]] == ["j8"]
    assert run.due_ring() is not None


def test_ring_state_reads_the_ring_under_way(tmp_path):
    room = Bedroom(tmp_path, max_minutes=10)
    room.bell.start(alarm_entry())

    state = room.another_bell().ring_state()

    assert (state.started, state.until) == (START, START + timedelta(minutes=10))
    assert [alarm.id for alarm in state.alarms] == ["j7"]


VALID = {
    "version": 1,
    "started": "2026-10-25T06:00:04",
    "until": "2026-10-25T06:10:04",
    "alarms": [{"id": "j7", "at": "2026-10-25T06:00", "label": None}],
}
CORRUPT = {
    "not json": b"{not json",
    "a list": b"[]",
    "another version": json.dumps(VALID | {"version": 2}).encode(),
    "an extra key": json.dumps(VALID | {"index": 3}).encode(),
    "started not a time": json.dumps(VALID | {"started": "jam enam"}).encode(),
    "until with a time zone": json.dumps(VALID | {"until": "2026-10-25T06:10:04+07:00"}).encode(),
    "until before started": json.dumps(VALID | {"until": "2026-10-25T05:00:00"}).encode(),
    "no alarms": json.dumps(VALID | {"alarms": []}).encode(),
    "an alarm without its label": json.dumps(
        VALID | {"alarms": [{"id": "j7", "at": "2026-10-25T06:00"}]}
    ).encode(),
    "an alarm id of a memory": json.dumps(
        VALID | {"alarms": [{"id": "m1", "at": "2026-10-25T06:00", "label": None}]}
    ).encode(),
    "a label that is a number": json.dumps(
        VALID | {"alarms": [{"id": "j7", "at": "2026-10-25T06:00", "label": 5}]}
    ).encode(),
    "a blank label": json.dumps(
        VALID | {"alarms": [{"id": "j7", "at": "2026-10-25T06:00", "label": " "}]}
    ).encode(),
}


@pytest.mark.parametrize("content", CORRUPT.values(), ids=CORRUPT.keys())
def test_a_broken_ring_file_is_moved_aside_logged_and_read_as_no_ring(tmp_path, caplog, content):
    room = Bedroom(tmp_path)
    room.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.alarm"):
        assert room.bell.ringing() is False

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert not room.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_a_ring_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    room = Bedroom(tmp_path)
    room.path.write_bytes(b"\xef\xbb\xbf" + json.dumps(VALID).encode())

    assert room.bell.ringing() is True


def test_a_ring_file_that_cannot_be_read_is_no_ring_and_warned_once(tmp_path, caplog):
    room = Bedroom(tmp_path)
    room.path.mkdir()  # a folder where the file should be

    with caplog.at_level(logging.WARNING, logger="waifu.alarm"):
        rings = [room.bell.ringing(), room.bell.due_ring()]
        room.monotonic.now += REREAD_S
        rings.append(room.bell.ringing())

    assert rings == [False, None, False]
    assert caplog.text.count("Could not read the alarm file") == 1


def test_ring_state_raises_when_the_file_cannot_be_read(tmp_path):
    room = Bedroom(tmp_path)
    room.path.mkdir()

    with pytest.raises(OSError):
        room.bell.ring_state()


# --- the words of a push --------------------------------------------------------------------------


def test_the_wake_text_names_the_alarm_s_time_and_its_labels():
    assert wake_text(ALARM_AT, [], "Master") == "Master, bangun! Alarm jam 06.00."
    assert wake_text(ALARM_AT, ["berangkat kerja"], "sayang") == (
        "Sayang, bangun! Alarm jam 06.00. Berangkat kerja."
    )


def test_a_missed_alarm_is_named_with_its_date_only_when_it_was_not_today():
    today = date(2026, 10, 25)

    assert missed_text(ALARM_AT, None, today) == "Alarm jam 06.00 terlewat."
    assert missed_text(ALARM_AT, "berangkat kerja", today) == (
        "Alarm jam 06.00 terlewat: berangkat kerja."
    )
    assert missed_text(ALARM_AT, None, date(2026, 10, 26)) == (
        "Alarm 25-10-2026 jam 06.00 terlewat."
    )


# --- the words that stop or snooze it ------------------------------------------------------------

NAMES = ("Cyrene", "Hai Cyrene", "Sayang")


@pytest.mark.parametrize(
    "text",
    [
        "aku sudah bangun",
        "Aku udah bangun.",
        "sudah bangun",
        "udah bangun kok",
        "iya iya, udah bangun!",
        "Oke, aku sudah bangun",
        "Cyrene, matikan alarm!",
        "matikan alarmnya dong",
        "matiin alarm",
        "Stop alarm.",
        "stop alarmnya",
        "hentikan alarm",
        "Hai Cyrene, tolong matikan alarmnya",
        "sayang, aku udah bangun nih",
        "Master, aku sudah bangun",
    ],
)
def test_the_stop_phrases_stop_the_alarm(text):
    assert alarm_request(text, NAMES) == AlarmRequest("stop", None)


@pytest.mark.parametrize(
    ("text", "minutes"),
    [
        ("tunda", None),
        ("Tunda dulu.", None),
        ("tunda alarmnya", None),
        ("snooze", None),
        ("sebentar lagi", None),
        ("bentar lagi ya", None),
        ("Cyrene, tunda 10 menit", 10),
        ("tunda sepuluh menit", 10),
        ("5 menit lagi", 5),
        ("lima menit lagi", 5),
        ("snooze 15 menit", 15),
        ("lima belas menit lagi", 15),
        ("tunda dua puluh menit dong", 20),
        ("tiga puluh menit lagi", 30),
        ("tunda 1 menit", 1),
        ("tunda satu menit", 1),
    ],
)
def test_the_snooze_phrases_snooze_it_for_the_minutes_said(text, minutes):
    assert alarm_request(text, NAMES) == AlarmRequest("snooze", minutes)


@pytest.mark.parametrize(
    "text",
    [
        "matikan lampu",
        "matikan musik",
        "Cyrene, matikan lampu kamar",
        "stop",
        "matikan",
        "udah",
        "bangun",
        "sudah",
        "alarm",
        "Cyrene",
        "",
        "aku sudah bangun, nyalakan lampu",
        "matikan alarm terus nyalakan lampu",
        "matikan alarm matikan alarm",
        "aku bangun jam berapa",
        "tunda 45 menit",
        "tunda 0 menit",
        "31 menit lagi",
        "tunda 05 menit",
        "tunda menit",
        "matikan alarm besok",
    ],
)
def test_anything_else_is_no_alarm_request(text):
    assert alarm_request(text, NAMES) is None


def test_her_names_count_only_when_given():
    assert alarm_request("Cyrene, matikan alarm") is None
    assert alarm_request("Cyrene, matikan alarm", ["Cyrene"]) == AlarmRequest("stop", None)
