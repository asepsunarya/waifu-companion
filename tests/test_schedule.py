import dataclasses
import json
import logging
import threading
from datetime import date, datetime, timedelta

import pytest

from waifu import schedule
from waifu.schedule import (
    DeviceAction,
    Entry,
    Reminder,
    RoutineAction,
    ScheduleError,
    ScheduleStore,
    due,
    next_occurrence,
    occurrence_on,
    parse_local_time,
)

START = datetime(2026, 10, 13, 14, 30, 45, 123456)  # a Tuesday
CREATED = datetime(2026, 10, 13, 14, 30)
TODAY_15 = datetime(2026, 10, 13, 15, 0)
CORRUPT_NAME = "schedule.json.corrupt-20261013-143045"
MEDICINE = Reminder("Master, sudah jam tiga, waktunya minum obat!")
RAW_MEDICINE = {"type": "reminder", "text": "Master, sudah jam tiga, waktunya minum obat!"}


def entry(entry_id, at, repeat="none", action=MEDICINE):
    return Entry(id=entry_id, at=at, repeat=repeat, action=action, created=CREATED)


class Clock:
    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


def make_store(tmp_path, clock=None, **kwargs):
    return ScheduleStore(tmp_path / "schedule.json", now=clock or Clock(), **kwargs)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_schedule(path, entries, next_id):
    path.write_text(
        json.dumps({"version": 1, "next_id": next_id, "entries": entries}), encoding="utf-8"
    )


def raw_entry(entry_id, at, repeat="none", action=RAW_MEDICINE, created="2026-10-01T08:00"):
    return {"id": entry_id, "at": at, "repeat": repeat, "action": action, "created": created}


# --- values ------------------------------------------------------------------------------------


def test_an_entry_and_its_actions_are_frozen():
    frozen = [
        entry("j1", datetime(2026, 10, 13, 15, 0)),
        MEDICINE,
        DeviceAction("ac", "temp", 26),
        RoutineAction("tidur"),
    ]

    for value in frozen:
        field = dataclasses.fields(value)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, field, "lain")


def test_a_device_action_without_a_value_has_none():
    assert DeviceAction("lampu_kamar", "off") == DeviceAction("lampu_kamar", "off", None)


def test_a_device_action_comes_from_no_routine_unless_it_says_so():
    assert DeviceAction("ac", "off").routine is None
    assert DeviceAction("ac", "off", routine="tidur") != DeviceAction("ac", "off")


def test_an_entry_has_no_day_of_the_month_unless_it_says_so():
    at = datetime(2026, 11, 30, 9, 0)

    assert entry("j1", at).day is None
    assert Entry("j1", at, "monthly", MEDICINE, CREATED, day=31).day == 31


def test_schedule_error_is_an_exception_of_its_own():
    assert issubclass(ScheduleError, Exception)


# --- parse_local_time --------------------------------------------------------------------------


def test_parse_local_time_reads_a_local_date_and_time():
    assert parse_local_time("2026-10-13 15:00") == datetime(2026, 10, 13, 15, 0)


def test_parse_local_time_returns_a_naive_time_at_minute_precision():
    parsed = parse_local_time("2027-01-01 00:05")

    assert parsed.tzinfo is None
    assert (parsed.second, parsed.microsecond) == (0, 0)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "jam tiga sore",
        "2026-10-13",
        "15:00",
        "2026-10-13T15:00",
        "2026-10-13 15:00:00",
        "2026-10-13 3:00",
        "2026-1-5 07:30",
        "13-10-2026 15:00",
        "2026/10/13 15:00",
        " 2026-10-13 15:00",
        "2026-10-13 15:00 ",
        "2026-10-13 15:00\n",
        "2026-10-13  15:00",
        "2026-10-13 15:00+07:00",
        "２０２６-10-13 15:00",
        "2026-02-30 10:00",
        "2026-13-01 10:00",
        "2026-10-13 24:00",
        "2026-10-13 15:60",
    ],
)
def test_parse_local_time_refuses_anything_but_yyyy_mm_dd_hh_mm(text):
    with pytest.raises(ScheduleError, match="YYYY-MM-DD HH:MM") as caught:
        parse_local_time(text)

    if text.strip():
        assert text.strip() not in str(caught.value)


@pytest.mark.parametrize("value", [None, 202610131500, datetime(2026, 10, 13, 15, 0)])
def test_parse_local_time_refuses_a_value_that_is_not_text(value):
    with pytest.raises(ScheduleError, match="YYYY-MM-DD HH:MM"):
        parse_local_time(value)


def test_parse_local_time_accepts_the_29th_of_february_of_a_leap_year_only():
    assert parse_local_time("2028-02-29 07:00") == datetime(2028, 2, 29, 7, 0)
    with pytest.raises(ScheduleError):
        parse_local_time("2027-02-29 07:00")


# --- next_occurrence ---------------------------------------------------------------------------


def test_a_one_shot_entry_has_no_next_occurrence():
    assert next_occurrence(datetime(2026, 10, 13, 15, 0), "none", START) is None


@pytest.mark.parametrize(
    ("at", "repeat", "after", "expected"),
    [
        # Daily: later today when the clock time is still ahead, else tomorrow.
        ("2026-10-13 07:00", "daily", "2026-10-13 06:59", "2026-10-13 07:00"),
        ("2026-10-13 07:00", "daily", "2026-10-13 07:00", "2026-10-14 07:00"),
        ("2026-10-13 07:00", "daily", "2026-10-13 07:20", "2026-10-14 07:00"),
        # Across the end of a month, a year, and a leap February.
        ("2026-10-31 07:00", "daily", "2026-10-31 07:00", "2026-11-01 07:00"),
        ("2026-12-31 23:30", "daily", "2026-12-31 23:30", "2027-01-01 23:30"),
        ("2028-02-28 07:00", "daily", "2028-02-28 07:00", "2028-02-29 07:00"),
        ("2027-02-28 07:00", "daily", "2027-02-28 07:00", "2027-03-01 07:00"),
        # Weekdays: Friday goes on to Monday, Saturday and Sunday too.
        ("2026-10-16 07:00", "weekdays", "2026-10-16 07:00", "2026-10-19 07:00"),
        ("2026-10-16 07:00", "weekdays", "2026-10-17 09:00", "2026-10-19 07:00"),
        ("2026-10-16 07:00", "weekdays", "2026-10-18 09:00", "2026-10-19 07:00"),
        ("2026-10-12 07:00", "weekdays", "2026-10-12 07:00", "2026-10-13 07:00"),
        ("2027-12-31 07:00", "weekdays", "2027-12-31 07:00", "2028-01-03 07:00"),
        # Weekends: Sunday goes on to Saturday, Saturday to Sunday.
        ("2026-10-18 09:00", "weekends", "2026-10-18 09:00", "2026-10-24 09:00"),
        ("2026-10-17 09:00", "weekends", "2026-10-17 09:00", "2026-10-18 09:00"),
        ("2026-10-17 09:00", "weekends", "2026-10-14 12:00", "2026-10-17 09:00"),
        ("2026-12-27 09:00", "weekends", "2026-12-27 09:00", "2027-01-02 09:00"),
        # Weekly: the weekday of `at` (a Monday here), also across the end of a year.
        ("2026-10-19 07:00", "weekly", "2026-10-19 06:59", "2026-10-19 07:00"),
        ("2026-10-19 07:00", "weekly", "2026-10-19 07:00", "2026-10-26 07:00"),
        ("2026-10-19 07:00", "weekly", "2026-10-21 12:00", "2026-10-26 07:00"),
        ("2026-10-19 07:00", "weekly", "2026-10-25 23:59", "2026-10-26 07:00"),
        ("2026-12-28 07:00", "weekly", "2026-12-28 07:00", "2027-01-04 07:00"),
        # Monthly: the day of `at`, later this month or else next month, also across a year.
        ("2026-10-20 08:00", "monthly", "2026-10-20 07:59", "2026-10-20 08:00"),
        ("2026-10-20 08:00", "monthly", "2026-10-20 08:00", "2026-11-20 08:00"),
        ("2026-10-20 08:00", "monthly", "2026-11-05 12:00", "2026-11-20 08:00"),
        ("2026-12-20 08:00", "monthly", "2026-12-20 08:00", "2027-01-20 08:00"),
        ("2026-10-01 00:00", "monthly", "2026-10-31 23:59", "2026-11-01 00:00"),
        # Yearly: the month and day of `at`, later this year or else next year.
        ("2026-05-05 08:00", "yearly", "2026-05-04 23:00", "2026-05-05 08:00"),
        ("2026-05-05 08:00", "yearly", "2026-05-05 08:00", "2027-05-05 08:00"),
        ("2026-05-05 08:00", "yearly", "2026-10-24 14:30", "2027-05-05 08:00"),
        ("2026-12-31 23:30", "yearly", "2026-12-31 23:30", "2027-12-31 23:30"),
        ("2026-01-01 00:00", "yearly", "2026-12-31 23:59", "2027-01-01 00:00"),
    ],
)
def test_next_occurrence_is_the_first_matching_day_later_than_after(at, repeat, after, expected):
    assert next_occurrence(
        parse_local_time(at), repeat, parse_local_time(after)
    ) == parse_local_time(expected)


def test_next_occurrence_keeps_the_entry_clock_time_not_the_clock_time_of_after():
    at = datetime(2026, 10, 13, 7, 30)

    assert next_occurrence(at, "daily", datetime(2026, 10, 13, 12, 45, 10)) == datetime(
        2026, 10, 14, 7, 30
    )


def test_next_occurrence_after_a_long_absence_is_the_first_one_after_it():
    # Cyrene was off for a week: the entry moves to the first time after `after`.
    at = datetime(2026, 10, 5, 7, 0)

    assert next_occurrence(at, "daily", datetime(2026, 10, 13, 8, 0)) == datetime(
        2026, 10, 14, 7, 0
    )


def test_next_occurrence_is_later_than_after_even_within_the_same_minute():
    at = datetime(2026, 10, 13, 7, 0)

    assert next_occurrence(at, "daily", datetime(2026, 10, 13, 7, 0, 30)) == datetime(
        2026, 10, 14, 7, 0
    )


@pytest.mark.parametrize(
    ("at", "repeat", "after"),
    [
        (datetime(2026, 3, 28, 2, 30), "daily", datetime(2026, 3, 28, 2, 30)),
        (datetime(2026, 3, 22, 2, 30), "weekly", datetime(2026, 3, 22, 2, 30)),
        (datetime(2026, 1, 29, 2, 30), "monthly", datetime(2026, 3, 1, 0, 0)),
        (datetime(2025, 3, 29, 2, 30), "yearly", datetime(2025, 3, 29, 2, 30)),
    ],
)
def test_next_occurrence_steps_whole_calendar_days_of_naive_local_time(at, repeat, after):
    # Naive local times know no daylight saving: the next one is the same clock time, also on
    # the night Europe moves its clocks, when 02:30 does not exist there (WIB never moves).
    assert next_occurrence(at, repeat, after) == datetime(2026, 3, 29, 2, 30)


def test_a_monthly_entry_of_the_31st_fires_on_the_last_day_of_a_shorter_month_only():
    at = datetime(2026, 10, 31, 9, 0)
    walked = []
    for _ in range(17):
        at = next_occurrence(at, "monthly", at, day=31)
        walked.append(f"{at:%Y-%m-%d %H:%M}")

    assert walked == [
        "2026-11-30 09:00",
        "2026-12-31 09:00",
        "2027-01-31 09:00",
        "2027-02-28 09:00",
        "2027-03-31 09:00",
        "2027-04-30 09:00",
        "2027-05-31 09:00",
        "2027-06-30 09:00",
        "2027-07-31 09:00",
        "2027-08-31 09:00",
        "2027-09-30 09:00",
        "2027-10-31 09:00",
        "2027-11-30 09:00",
        "2027-12-31 09:00",
        "2028-01-31 09:00",
        "2028-02-29 09:00",
        "2028-03-31 09:00",
    ]


@pytest.mark.parametrize(
    ("at", "day", "after", "expected"),
    [
        # The 30th and the 29th fall back in February only, and come back in March.
        ("2027-01-30 09:00", 30, "2027-01-30 09:00", "2027-02-28 09:00"),
        ("2027-02-28 09:00", 30, "2027-02-28 09:00", "2027-03-30 09:00"),
        ("2027-01-29 09:00", 29, "2027-01-29 09:00", "2027-02-28 09:00"),
        ("2028-01-29 09:00", 29, "2028-01-29 09:00", "2028-02-29 09:00"),
        # The last day of a shorter month is still to come later in that month.
        ("2026-11-30 09:00", 31, "2026-11-15 12:00", "2026-11-30 09:00"),
        # After months away: the first one later than `after`, on the day, not on `at`'s.
        ("2026-08-31 09:00", 31, "2026-11-30 10:00", "2026-12-31 09:00"),
        ("2027-02-28 09:00", 31, "2027-04-01 08:00", "2027-04-30 09:00"),
    ],
)
def test_a_monthly_entry_fires_on_its_day_or_its_month_s_last_day(at, day, after, expected):
    assert next_occurrence(
        parse_local_time(at), "monthly", parse_local_time(after), day=day
    ) == parse_local_time(expected)


def test_a_monthly_entry_without_a_day_repeats_on_the_day_of_at():
    at = datetime(2026, 11, 30, 9, 0)

    assert next_occurrence(at, "monthly", at) == datetime(2026, 12, 30, 9, 0)


def test_a_yearly_entry_of_29_february_fires_on_28_february_in_common_years():
    at = datetime(2024, 2, 29, 8, 0)
    walked = []
    for _ in range(5):
        at = next_occurrence(at, "yearly", at, day=29)
        walked.append(f"{at:%Y-%m-%d %H:%M}")

    assert walked == [
        "2025-02-28 08:00",
        "2026-02-28 08:00",
        "2027-02-28 08:00",
        "2028-02-29 08:00",
        "2029-02-28 08:00",
    ]


def test_a_yearly_entry_of_29_february_is_still_to_come_on_28_february_of_a_leap_year():
    at = datetime(2027, 2, 28, 8, 0)

    assert next_occurrence(at, "yearly", datetime(2028, 2, 28, 9, 0), day=29) == datetime(
        2028, 2, 29, 8, 0
    )


def test_a_yearly_entry_made_for_a_date_that_passed_this_year_comes_next_year():
    # "Ingatkan ulang tahun Ibu tiap 5 Mei", said on 24 October.
    birthday = datetime(2026, 5, 5, 8, 0)

    assert next_occurrence(birthday, "yearly", datetime(2026, 10, 24, 14, 30)) == datetime(
        2027, 5, 5, 8, 0
    )


@pytest.mark.parametrize("repeat", ["daily", "weekdays", "weekends", "weekly"])
def test_the_day_counts_only_for_monthly_and_yearly_entries(repeat):
    at = datetime(2026, 10, 19, 7, 0)  # a Monday

    assert next_occurrence(at, repeat, at, day=31) == next_occurrence(at, repeat, at)


@pytest.mark.parametrize("repeat", ["hourly", "Daily", "Monthly", "", None])
def test_next_occurrence_refuses_an_unknown_repeat(repeat):
    with pytest.raises(
        ScheduleError, match="none, daily, weekdays, weekends, weekly, monthly, yearly"
    ):
        next_occurrence(datetime(2026, 10, 13, 7, 0), repeat, START)


# --- occurrence_on -----------------------------------------------------------------------------


def test_a_one_shot_entry_occurs_on_its_own_date_only():
    medicine = entry("j1", TODAY_15)

    assert occurrence_on(medicine, date(2026, 10, 13)) == TODAY_15
    assert occurrence_on(medicine, date(2026, 10, 12)) is None
    assert occurrence_on(medicine, date(2026, 10, 14)) is None


@pytest.mark.parametrize(
    ("repeat", "day", "expected"),
    [
        ("daily", date(2026, 10, 14), datetime(2026, 10, 14, 7, 0)),
        ("weekdays", date(2026, 10, 16), datetime(2026, 10, 16, 7, 0)),  # a Friday
        ("weekdays", date(2026, 10, 17), None),  # a Saturday
        ("weekends", date(2026, 10, 18), datetime(2026, 10, 18, 7, 0)),
        ("weekends", date(2026, 10, 19), None),
        ("weekly", date(2026, 10, 20), datetime(2026, 10, 20, 7, 0)),  # its Tuesday
        ("weekly", date(2026, 10, 21), None),
        ("monthly", date(2026, 11, 13), datetime(2026, 11, 13, 7, 0)),
        ("monthly", date(2026, 11, 14), None),
        ("yearly", date(2027, 10, 13), datetime(2027, 10, 13, 7, 0)),
        ("yearly", date(2027, 11, 13), None),
    ],
)
def test_a_repeating_entry_occurs_at_its_clock_time_on_each_of_its_days(repeat, day, expected):
    tuesday = entry("j1", datetime(2026, 10, 13, 7, 0), repeat)
    tuesday = dataclasses.replace(tuesday, day=13 if repeat in ("monthly", "yearly") else None)

    assert occurrence_on(tuesday, day) == expected


def test_an_occurrence_that_already_fired_still_counts_for_its_day():
    # Fired at 07:00 on the 14th, the daily entry has moved on to the 15th.
    moved_on = entry("j1", datetime(2026, 10, 15, 7, 0), "daily")

    assert occurrence_on(moved_on, date(2026, 10, 14)) == datetime(2026, 10, 14, 7, 0)


def test_no_occurrence_counts_before_the_entry_was_made():
    made_at_14_30 = entry("j1", datetime(2026, 10, 14, 7, 0), "daily")  # CREATED is 14:30

    assert occurrence_on(made_at_14_30, date(2026, 10, 13)) is None
    assert occurrence_on(made_at_14_30, date(2026, 10, 12)) is None


def test_an_occurrence_in_the_minute_the_entry_was_made_counts():
    now_or_never = entry("j1", datetime(2026, 10, 14, 14, 30), "daily")

    assert occurrence_on(now_or_never, date(2026, 10, 13)) == CREATED


def test_a_monthly_entry_of_the_31st_occurs_on_the_last_day_of_a_shorter_month():
    end_of_month = dataclasses.replace(entry("j1", datetime(2026, 11, 30, 8, 0), "monthly"), day=31)

    assert occurrence_on(end_of_month, date(2026, 11, 30)) == datetime(2026, 11, 30, 8, 0)
    assert occurrence_on(end_of_month, date(2026, 12, 30)) is None
    assert occurrence_on(end_of_month, date(2026, 12, 31)) == datetime(2026, 12, 31, 8, 0)


def test_an_entry_at_midnight_occurs_on_its_day():
    midnight = entry("j1", datetime(2026, 10, 14, 0, 0), "daily")

    assert occurrence_on(midnight, date(2026, 10, 15)) == datetime(2026, 10, 15, 0, 0)


# --- due ---------------------------------------------------------------------------------------

GRACE = timedelta(minutes=30)
NOW = datetime(2026, 10, 13, 15, 0, 20)


def test_nothing_is_due_without_entries():
    assert due([], NOW, GRACE) == ([], [])


def test_an_entry_in_the_future_is_neither_fired_nor_skipped():
    assert due([entry("j1", datetime(2026, 10, 13, 15, 1))], NOW, GRACE) == ([], [])


def test_an_entry_fires_from_its_minute_on():
    on_time = entry("j1", datetime(2026, 10, 13, 15, 0))

    assert due([on_time], NOW, GRACE) == ([on_time], [])
    assert due([on_time], datetime(2026, 10, 13, 15, 0), GRACE) == ([on_time], [])


def test_an_entry_late_by_exactly_the_grace_still_fires():
    late = entry("j1", NOW - GRACE)

    assert due([late], NOW, GRACE) == ([late], [])


def test_an_entry_late_by_more_than_the_grace_is_skipped():
    too_late = entry("j1", datetime(2026, 10, 13, 14, 30))

    assert due([too_late], NOW, GRACE) == ([], [too_late])


def test_with_no_grace_only_the_current_minute_fires():
    on_time = entry("j1", datetime(2026, 10, 13, 15, 0))
    a_minute_late = entry("j2", datetime(2026, 10, 13, 14, 59))

    assert due([a_minute_late, on_time], datetime(2026, 10, 13, 15, 0), timedelta(0)) == (
        [on_time],
        [a_minute_late],
    )


def test_due_lists_both_groups_soonest_first():
    entries = [
        entry("j5", datetime(2026, 10, 13, 14, 50)),
        entry("j1", datetime(2026, 10, 12, 7, 0), "daily"),
        entry("j4", datetime(2026, 10, 13, 16, 0)),
        entry("j3", datetime(2026, 10, 13, 14, 40)),
        entry("j2", datetime(2026, 10, 13, 9, 0)),
        entry("j6", datetime(2026, 10, 13, 14, 40)),
    ]

    fire, skip = due(entries, NOW, GRACE)

    assert [e.id for e in fire] == ["j3", "j6", "j5"]
    assert [e.id for e in skip] == ["j1", "j2"]


# --- store: reading ----------------------------------------------------------------------------


def test_a_missing_file_is_an_empty_schedule_and_reading_creates_nothing(tmp_path):
    path = tmp_path / "data" / "schedule.json"
    store = ScheduleStore(path, now=Clock())

    assert store.entries() == []
    assert not path.parent.exists()


def test_the_store_exposes_its_path_and_limit(tmp_path):
    store = make_store(tmp_path, max_entries=7)

    assert store.path == tmp_path / "schedule.json"
    assert store.max_entries == 7


def test_the_limit_defaults_to_the_spec_value(tmp_path):
    assert ScheduleStore(tmp_path / "schedule.json").max_entries == 50


def test_every_call_rereads_a_file_edited_by_hand(tmp_path):
    store = make_store(tmp_path)
    write_schedule(store.path, [raw_entry("j1", "2026-10-13T15:00")], next_id=2)
    assert [e.at for e in store.entries()] == [TODAY_15]

    write_schedule(store.path, [raw_entry("j1", "2026-10-13T16:00")], next_id=2)

    assert [e.at for e in store.entries()] == [datetime(2026, 10, 13, 16, 0)]


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    # Notepad and some editors put a UTF-8 BOM in front of a file they save.
    store = make_store(tmp_path)
    content = json.dumps(
        {"version": 1, "next_id": 2, "entries": [raw_entry("j1", "2026-10-13T15:00")]}
    )
    store.path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    assert [e.id for e in store.entries()] == ["j1"]


def test_entries_are_read_back_with_every_kind_of_action(tmp_path):
    store = make_store(tmp_path)
    write_schedule(
        store.path,
        [
            raw_entry("j1", "2026-10-13T15:00"),
            raw_entry(
                "j2",
                "2026-10-14T02:00",
                action={"type": "device", "device": "ac", "command": "off", "value": None},
            ),
            raw_entry(
                "j3",
                "2026-10-14T07:00",
                "weekdays",
                {"type": "device", "device": "ac", "command": "temp", "value": 26},
            ),
            raw_entry("j4", "2026-10-13T23:00", "daily", {"type": "routine", "name": "tidur"}),
        ],
        next_id=5,
    )

    created = datetime(2026, 10, 1, 8, 0)
    assert store.entries() == [
        Entry("j1", TODAY_15, "none", MEDICINE, created),
        Entry("j4", datetime(2026, 10, 13, 23, 0), "daily", RoutineAction("tidur"), created),
        Entry("j2", datetime(2026, 10, 14, 2, 0), "none", DeviceAction("ac", "off"), created),
        Entry(
            "j3", datetime(2026, 10, 14, 7, 0), "weekdays", DeviceAction("ac", "temp", 26), created
        ),
    ]


def test_entries_are_listed_soonest_first_then_by_id(tmp_path):
    store = make_store(tmp_path)
    write_schedule(
        store.path,
        [
            raw_entry("j10", "2026-10-13T15:00"),
            raw_entry("j3", "2026-10-20T08:00"),
            raw_entry("j9", "2026-10-13T15:00"),
            raw_entry("j1", "2026-10-13T14:45"),
        ],
        next_id=11,
    )

    assert [e.id for e in store.entries()] == ["j1", "j9", "j10", "j3"]


def test_times_written_by_hand_with_seconds_are_read_at_minute_precision(tmp_path):
    store = make_store(tmp_path)
    write_schedule(
        store.path,
        [raw_entry("j1", "2026-10-13 15:00:59.5", created="2026-10-01T08:00:30")],
        next_id=2,
    )

    (read,) = store.entries()
    assert (read.at, read.created) == (TODAY_15, datetime(2026, 10, 1, 8, 0))


def test_monthly_and_yearly_entries_are_read_with_their_day(tmp_path):
    store = make_store(tmp_path)
    write_schedule(
        store.path,
        [
            # The 31st on the last day of November; 29 February on 28 February and on itself.
            raw_entry("j1", "2026-11-30T09:00", "monthly") | {"day": 31},
            raw_entry("j2", "2027-02-28T08:00", "yearly") | {"day": 29},
            raw_entry("j3", "2028-02-29T08:00", "yearly") | {"day": 29},
            raw_entry("j4", "2026-10-20T08:00", "monthly") | {"day": 20},
            raw_entry("j5", "2026-10-19T07:00", "weekly"),
        ],
        next_id=6,
    )

    assert [(e.id, e.at, e.repeat, e.day) for e in store.entries()] == [
        ("j5", datetime(2026, 10, 19, 7, 0), "weekly", None),
        ("j4", datetime(2026, 10, 20, 8, 0), "monthly", 20),
        ("j1", datetime(2026, 11, 30, 9, 0), "monthly", 31),
        ("j2", datetime(2027, 2, 28, 8, 0), "yearly", 29),
        ("j3", datetime(2028, 2, 29, 8, 0), "yearly", 29),
    ]


# --- store: add --------------------------------------------------------------------------------


def test_add_returns_the_first_entry_with_its_creation_time_at_minute_precision(tmp_path):
    store = make_store(tmp_path)

    added = store.add(TODAY_15, "none", MEDICINE)

    assert added == Entry("j1", TODAY_15, "none", MEDICINE, CREATED)
    assert store.entries() == [added]


def test_add_writes_the_spec_file_format_in_readable_utf8(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add(TODAY_15, "none", Reminder("Master, ayo latihan 日本語!"))
    store.add(datetime(2026, 10, 14, 2, 0), "none", DeviceAction("ac", "off"))
    store.add(datetime(2026, 10, 14, 7, 0), "weekdays", DeviceAction("ac", "temp", 26))
    clock.advance(minutes=2)
    store.add(datetime(2026, 10, 13, 23, 0), "daily", RoutineAction("tidur"))

    text = store.path.read_text(encoding="utf-8")
    assert "日本語" in text
    assert json.loads(text) == {
        "version": 1,
        "next_id": 5,
        "entries": [
            {
                "id": "j1",
                "at": "2026-10-13T15:00",
                "repeat": "none",
                "action": {"type": "reminder", "text": "Master, ayo latihan 日本語!"},
                "created": "2026-10-13T14:30",
            },
            {
                "id": "j4",
                "at": "2026-10-13T23:00",
                "repeat": "daily",
                "action": {"type": "routine", "name": "tidur"},
                "created": "2026-10-13T14:32",
            },
            {
                "id": "j2",
                "at": "2026-10-14T02:00",
                "repeat": "none",
                "action": {"type": "device", "device": "ac", "command": "off", "value": None},
                "created": "2026-10-13T14:30",
            },
            {
                "id": "j3",
                "at": "2026-10-14T07:00",
                "repeat": "weekdays",
                "action": {"type": "device", "device": "ac", "command": "temp", "value": 26},
                "created": "2026-10-13T14:30",
            },
        ],
    }


def test_only_a_monthly_or_yearly_entry_is_written_with_its_day(tmp_path):
    store = make_store(tmp_path)
    store.add(datetime(2026, 10, 31, 9, 0), "monthly", Reminder("Master, bayar listrik!"))
    store.add(datetime(2028, 2, 29, 8, 0), "yearly", Reminder("Master, ulang tahun Budi!"))
    store.add(datetime(2026, 10, 19, 7, 0), "weekly", RoutineAction("pagi"))

    entries = read_json(store.path)["entries"]

    assert entries == [
        {
            "id": "j3",
            "at": "2026-10-19T07:00",
            "repeat": "weekly",
            "action": {"type": "routine", "name": "pagi"},
            "created": "2026-10-13T14:30",
        },
        {
            "id": "j1",
            "at": "2026-10-31T09:00",
            "repeat": "monthly",
            "day": 31,
            "action": {"type": "reminder", "text": "Master, bayar listrik!"},
            "created": "2026-10-13T14:30",
        },
        {
            "id": "j2",
            "at": "2027-02-28T08:00",
            "repeat": "yearly",
            "day": 29,
            "action": {"type": "reminder", "text": "Master, ulang tahun Budi!"},
            "created": "2026-10-13T14:30",
        },
    ]
    # The day follows the repeat, where the owner reads it when editing by hand.
    assert list(entries[1]) == ["id", "at", "repeat", "day", "action", "created"]


def test_add_creates_the_parent_folder_on_the_first_write(tmp_path):
    path = tmp_path / "data" / "nested" / "schedule.json"

    ScheduleStore(path, now=Clock()).add(TODAY_15, "none", MEDICINE)

    assert path.is_file()


def test_ids_count_up(tmp_path):
    store = make_store(tmp_path)

    ids = [store.add(TODAY_15 + timedelta(hours=n), "none", MEDICINE).id for n in range(3)]

    assert ids == ["j1", "j2", "j3"]


def test_the_reminder_text_is_stripped_and_its_whitespace_collapsed(tmp_path):
    store = make_store(tmp_path)

    added = store.add(TODAY_15, "none", Reminder("  Master,\n angkat\t\tjemuran　ya!\r\n "))

    assert added.action == Reminder("Master, angkat jemuran ya!")
    assert store.entries()[0].action == Reminder("Master, angkat jemuran ya!")


@pytest.mark.parametrize("text", ["", "   ", "\n\t 　", None, 42])
def test_an_empty_reminder_is_refused(tmp_path, text):
    store = make_store(tmp_path)

    with pytest.raises(ScheduleError, match="kosong"):
        store.add(TODAY_15, "none", Reminder(text))

    assert not store.path.exists()


def test_a_reminder_of_200_characters_is_saved(tmp_path):
    store = make_store(tmp_path)

    assert store.add(TODAY_15, "none", Reminder(" " + "a" * 200 + " ")).action == Reminder(
        "a" * 200
    )


def test_a_longer_reminder_is_refused_without_echoing_it(tmp_path):
    store = make_store(tmp_path)
    text = "Master, " + "jangan lupa minum obat " * 9

    with pytest.raises(ScheduleError, match="200 karakter") as caught:
        store.add(TODAY_15, "none", Reminder(text))

    assert "jangan lupa" not in str(caught.value)
    assert not store.path.exists()


@pytest.mark.parametrize(
    "at",
    [
        datetime(2026, 10, 13, 14, 29, 59),
        datetime(2026, 10, 13, 14, 29),
        datetime(2026, 10, 12, 15, 0),
        datetime(2025, 10, 13, 15, 0),
    ],
)
@pytest.mark.parametrize("repeat", ["none", "daily", "weekdays", "weekends"])
def test_a_time_before_the_current_minute_is_refused(tmp_path, at, repeat):
    store = make_store(tmp_path)

    with pytest.raises(ScheduleError, match="sudah lewat"):
        store.add(at, repeat, MEDICINE)

    assert not store.path.exists()


@pytest.mark.parametrize("at", [datetime(2026, 10, 13, 14, 30), datetime(2026, 10, 13, 14, 30, 50)])
def test_a_time_in_the_current_minute_is_accepted(tmp_path, at):
    # "Satu menit lagi" whose tool call lands after the minute changed; it fires at once.
    store = make_store(tmp_path)

    assert store.add(at, "none", MEDICINE).at == datetime(2026, 10, 13, 14, 30)


def test_the_next_minute_is_accepted(tmp_path):
    store = make_store(tmp_path)

    assert store.add(datetime(2026, 10, 13, 14, 31), "none", MEDICINE).at == datetime(
        2026, 10, 13, 14, 31
    )


def test_a_time_with_seconds_is_saved_at_minute_precision(tmp_path):
    store = make_store(tmp_path)

    assert store.add(datetime(2026, 10, 13, 15, 0, 59, 999), "none", MEDICINE).at == TODAY_15


def test_a_time_366_days_ahead_is_accepted_and_one_minute_more_is_refused(tmp_path):
    store = make_store(tmp_path)
    limit = CREATED + timedelta(days=366)

    assert store.add(limit, "none", MEDICINE).at == datetime(2027, 10, 14, 14, 30)
    with pytest.raises(ScheduleError, match="366 hari"):
        store.add(limit + timedelta(minutes=1), "none", MEDICINE)

    assert [e.id for e in store.entries()] == ["j1"]


def test_add_is_refused_when_the_store_is_full_and_says_what_to_do(tmp_path):
    store = make_store(tmp_path, max_entries=2)
    store.add(TODAY_15, "none", MEDICINE)
    store.add(TODAY_15, "daily", RoutineAction("tidur"))
    saved = store.path.read_bytes()

    with pytest.raises(ScheduleError, match="penuh") as caught:
        store.add(TODAY_15, "none", DeviceAction("ac", "off"))

    assert "2 entri" in str(caught.value)
    assert "Batalkan" in str(caught.value)
    assert store.path.read_bytes() == saved


@pytest.mark.parametrize("repeat", ["hourly", "tiap bulan", "Daily", "", None])
def test_an_unknown_repeat_is_refused_without_echoing_it(tmp_path, repeat):
    store = make_store(tmp_path)

    with pytest.raises(
        ScheduleError, match="none, daily, weekdays, weekends, weekly, monthly, yearly"
    ) as caught:
        store.add(TODAY_15, repeat, MEDICINE)

    assert "tiap bulan" not in str(caught.value)
    assert not store.path.exists()


@pytest.mark.parametrize("value", [None, 0, 26, -5])
def test_a_device_action_takes_no_value_or_an_integer(tmp_path, value):
    store = make_store(tmp_path)

    added = store.add(TODAY_15, "none", DeviceAction("ac", "temp", value))

    assert added.action == DeviceAction("ac", "temp", value)
    assert read_json(store.path)["entries"][0]["action"]["value"] == value


def test_a_routine_step_is_saved_with_its_routine_and_read_back(tmp_path):
    store = make_store(tmp_path)
    delayed = DeviceAction("ac", "off", routine="tidur")

    added = store.add(TODAY_15, "none", delayed)

    assert read_json(store.path)["entries"][0]["action"] == {
        "type": "device",
        "device": "ac",
        "command": "off",
        "value": None,
        "routine": "tidur",
    }
    assert make_store(tmp_path).entries() == [added]
    assert added.action == delayed


def test_a_device_action_of_no_routine_is_saved_without_the_routine_key(tmp_path):
    store = make_store(tmp_path)

    store.add(TODAY_15, "none", DeviceAction("ac", "temp", 26))

    assert read_json(store.path)["entries"][0]["action"] == {
        "type": "device",
        "device": "ac",
        "command": "temp",
        "value": 26,
    }


@pytest.mark.parametrize(
    ("action", "match"),
    [
        (DeviceAction("ac", "off", routine=""), "nama rutinitas"),
        (DeviceAction("ac", "off", routine="  "), "nama rutinitas"),
        (DeviceAction("ac", "off", routine=7), "nama rutinitas"),
        (DeviceAction("", "off"), "perangkat dan perintah"),
        (DeviceAction("   ", "off"), "perangkat dan perintah"),
        (DeviceAction("ac", ""), "perangkat dan perintah"),
        (DeviceAction(None, "off"), "perangkat dan perintah"),
        (DeviceAction("ac", 1), "perangkat dan perintah"),
        (DeviceAction("ac", "temp", True), "bilangan bulat"),
        (DeviceAction("ac", "temp", 25.5), "bilangan bulat"),
        (DeviceAction("ac", "temp", "26"), "bilangan bulat"),
        (RoutineAction(""), "nama rutinitas"),
        (RoutineAction(None), "nama rutinitas"),
        ("ingatkan minum obat", "Jenis jadwal"),
        ({"type": "reminder", "text": "Minum obat."}, "Jenis jadwal"),
        (None, "Jenis jadwal"),
    ],
)
def test_a_malformed_action_is_refused(tmp_path, action, match):
    store = make_store(tmp_path)

    with pytest.raises(ScheduleError, match=match):
        store.add(TODAY_15, "none", action)

    assert not store.path.exists()


@pytest.mark.parametrize(
    ("at", "repeat", "expected"),
    [
        # Saturday 07:00 on weekdays starts on Monday; a weekday is kept.
        (datetime(2026, 10, 17, 7, 0), "weekdays", datetime(2026, 10, 19, 7, 0)),
        (datetime(2026, 10, 14, 7, 0), "weekdays", datetime(2026, 10, 14, 7, 0)),
        # Wednesday on weekends starts on Saturday; a Sunday is kept.
        (datetime(2026, 10, 14, 9, 0), "weekends", datetime(2026, 10, 17, 9, 0)),
        (datetime(2026, 10, 18, 9, 0), "weekends", datetime(2026, 10, 18, 9, 0)),
        (datetime(2026, 10, 17, 7, 0), "daily", datetime(2026, 10, 17, 7, 0)),
        (datetime(2026, 10, 17, 7, 0), "none", datetime(2026, 10, 17, 7, 0)),
        # Weekly, monthly, and yearly entries start on the day they were given.
        (datetime(2026, 10, 17, 7, 0), "weekly", datetime(2026, 10, 17, 7, 0)),
        (datetime(2026, 10, 31, 9, 0), "monthly", datetime(2026, 10, 31, 9, 0)),
        (datetime(2026, 12, 25, 8, 0), "yearly", datetime(2026, 12, 25, 8, 0)),
    ],
)
def test_a_repeating_entry_starts_on_the_first_of_its_days(tmp_path, at, repeat, expected):
    # `at` is always the next time an entry fires: a weekday entry never fires on a Saturday.
    store = make_store(tmp_path)

    assert store.add(at, repeat, MEDICINE).at == expected


@pytest.mark.parametrize(
    ("at", "repeat", "day"),
    [
        (datetime(2026, 10, 31, 9, 0), "monthly", 31),
        (datetime(2027, 5, 5, 8, 0), "yearly", 5),
        (datetime(2026, 10, 17, 7, 0), "weekly", None),
        (datetime(2026, 10, 17, 7, 0), "daily", None),
        (datetime(2026, 10, 17, 7, 0), "none", None),
    ],
)
def test_a_monthly_or_yearly_entry_keeps_the_day_it_was_given(tmp_path, at, repeat, day):
    store = make_store(tmp_path)

    added = store.add(at, repeat, MEDICINE)

    assert added == Entry("j1", at, repeat, MEDICINE, CREATED, day)
    assert make_store(tmp_path).entries() == [added]


@pytest.mark.parametrize(
    ("at", "repeat", "expected"),
    [
        # Monday 07:00 of yesterday's week, and 09:00 today, a Tuesday: next week's.
        (datetime(2026, 10, 12, 7, 0), "weekly", datetime(2026, 10, 19, 7, 0)),
        (datetime(2026, 10, 13, 9, 0), "weekly", datetime(2026, 10, 20, 9, 0)),
        (datetime(2025, 1, 6, 7, 0), "weekly", datetime(2026, 10, 19, 7, 0)),
        # "Tiap tanggal 5": this month's has passed, so next month's.
        (datetime(2026, 10, 5, 8, 0), "monthly", datetime(2026, 11, 5, 8, 0)),
        (datetime(2026, 8, 31, 8, 0), "monthly", datetime(2026, 10, 31, 8, 0)),
        (datetime(2026, 9, 30, 8, 0), "monthly", datetime(2026, 10, 30, 8, 0)),
        (datetime(2026, 10, 13, 14, 29), "monthly", datetime(2026, 11, 13, 14, 29)),
        # "Tiap 5 Mei": this year's has passed, so next year's.
        (datetime(2026, 5, 5, 8, 0), "yearly", datetime(2027, 5, 5, 8, 0)),
        (datetime(2020, 5, 5, 8, 0), "yearly", datetime(2027, 5, 5, 8, 0)),
        # The current minute is still accepted and fires at the next tick.
        (datetime(2026, 10, 13, 14, 30), "weekly", datetime(2026, 10, 13, 14, 30)),
        (datetime(2026, 10, 13, 14, 30), "monthly", datetime(2026, 10, 13, 14, 30)),
        (datetime(2026, 10, 13, 14, 30), "yearly", datetime(2026, 10, 13, 14, 30)),
    ],
)
def test_a_weekly_monthly_or_yearly_entry_given_a_past_time_starts_at_its_next_one(
    tmp_path, at, repeat, expected
):
    store = make_store(tmp_path)

    added = store.add(at, repeat, MEDICINE)

    assert added.at == expected
    assert added.day == (at.day if repeat != "weekly" else None)


def test_a_yearly_entry_made_on_24_october_for_5_may_this_year_starts_next_year(tmp_path):
    store = make_store(tmp_path, Clock(datetime(2026, 10, 24, 14, 30)))

    added = store.add(datetime(2026, 5, 5, 8, 0), "yearly", Reminder("Ulang tahun Ibu!"))

    assert (added.at, added.day) == (datetime(2027, 5, 5, 8, 0), 5)


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        # This year's date is still to come, next year's means the same day.
        (datetime(2026, 12, 25, 8, 0), datetime(2026, 12, 25, 8, 0)),
        (datetime(2027, 12, 25, 8, 0), datetime(2026, 12, 25, 8, 0)),
        # Next year's date that is still more than 366 days away is never too far.
        (datetime(2027, 10, 20, 8, 0), datetime(2026, 10, 20, 8, 0)),
        (datetime(2030, 5, 5, 8, 0), datetime(2027, 5, 5, 8, 0)),
        # 29 February of a leap year starts on 28 February of the next common year.
        (datetime(2028, 2, 29, 8, 0), datetime(2027, 2, 28, 8, 0)),
    ],
)
def test_a_yearly_entry_ignores_the_year_it_was_given(tmp_path, at, expected):
    store = make_store(tmp_path)

    added = store.add(at, "yearly", MEDICINE)

    assert (added.at, added.day) == (expected, at.day)


@pytest.mark.parametrize("repeat", ["weekly", "monthly"])
def test_a_weekly_or_monthly_entry_more_than_366_days_ahead_is_still_refused(tmp_path, repeat):
    store = make_store(tmp_path)

    with pytest.raises(ScheduleError, match="terlalu jauh"):
        store.add(datetime(2027, 10, 15, 15, 0), repeat, MEDICINE)

    assert not store.path.exists()


def test_an_id_added_by_hand_beyond_next_id_is_never_reused(tmp_path):
    store = make_store(tmp_path)
    write_schedule(
        store.path, [raw_entry("j1", "2026-10-13T15:00"), raw_entry("j7", "2026-10-13T16:00")], 3
    )

    assert store.add(TODAY_15, "none", MEDICINE).id == "j8"
    assert read_json(store.path)["next_id"] == 9


# --- store: cancel -----------------------------------------------------------------------------


def test_cancel_removes_the_entry_and_returns_it(tmp_path):
    store = make_store(tmp_path)
    store.add(TODAY_15, "none", MEDICINE)
    ac = store.add(datetime(2026, 10, 14, 2, 0), "none", DeviceAction("ac", "off"))

    assert store.cancel("j2") == ac
    assert [e.id for e in store.entries()] == ["j1"]


def test_cancel_of_an_unknown_id_is_refused_without_echoing_it(tmp_path):
    store = make_store(tmp_path)
    store.add(TODAY_15, "none", MEDICINE)
    saved = store.path.read_bytes()

    with pytest.raises(ScheduleError, match="Tidak ada jadwal") as caught:
        store.cancel("j99")

    assert "j99" not in str(caught.value)
    assert store.path.read_bytes() == saved


def test_a_cancelled_id_is_never_reused(tmp_path):
    store = make_store(tmp_path)
    store.add(TODAY_15, "none", MEDICINE)
    store.add(TODAY_15, "daily", MEDICINE)
    store.cancel("j2")

    assert store.add(TODAY_15, "none", MEDICINE).id == "j3"


# --- store: fired ------------------------------------------------------------------------------


def test_a_fired_one_shot_entry_is_removed(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add(TODAY_15, "none", MEDICINE)
    later = store.add(TODAY_15 + timedelta(hours=1), "none", MEDICINE)
    clock.current = TODAY_15

    assert store.fired("j1", TODAY_15) is None
    assert store.entries() == [later]


def test_a_fired_daily_entry_moves_to_the_same_time_tomorrow(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    added = store.add(TODAY_15, "daily", MEDICINE)
    clock.current = TODAY_15 + timedelta(seconds=5)

    moved = store.fired("j1", TODAY_15)

    assert moved == dataclasses.replace(added, at=datetime(2026, 10, 14, 15, 0))
    assert store.entries() == [moved]
    assert read_json(store.path)["entries"][0]["at"] == "2026-10-14T15:00"


@pytest.mark.parametrize(
    ("at", "repeat", "expected"),
    [
        (datetime(2026, 10, 16, 7, 0), "weekdays", datetime(2026, 10, 19, 7, 0)),
        (datetime(2026, 10, 18, 9, 0), "weekends", datetime(2026, 10, 24, 9, 0)),
        (datetime(2026, 12, 31, 23, 30), "daily", datetime(2027, 1, 1, 23, 30)),
        (datetime(2026, 12, 28, 7, 0), "weekly", datetime(2027, 1, 4, 7, 0)),
        (datetime(2026, 12, 20, 8, 0), "monthly", datetime(2027, 1, 20, 8, 0)),
        (datetime(2026, 12, 25, 8, 0), "yearly", datetime(2027, 12, 25, 8, 0)),
    ],
)
def test_a_fired_repeating_entry_moves_to_its_next_day(tmp_path, at, repeat, expected):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add(at, repeat, MEDICINE)
    clock.current = at

    assert store.fired("j1", at).at == expected


def test_a_fired_monthly_entry_of_the_31st_comes_back_to_the_31st_after_a_short_month(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add(datetime(2026, 10, 31, 9, 0), "monthly", MEDICINE)
    moved = []
    for _ in range(5):
        (current,) = store.entries()  # read back from the file each time
        clock.current = current.at
        moved.append(store.fired(current.id, current.at))

    assert [(entry.at, entry.day) for entry in moved] == [
        (datetime(2026, 11, 30, 9, 0), 31),
        (datetime(2026, 12, 31, 9, 0), 31),
        (datetime(2027, 1, 31, 9, 0), 31),
        (datetime(2027, 2, 28, 9, 0), 31),
        (datetime(2027, 3, 31, 9, 0), 31),
    ]


def test_a_fired_yearly_entry_of_29_february_comes_back_to_it_in_a_leap_year(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add(datetime(2028, 2, 29, 8, 0), "yearly", MEDICINE)  # first on 28 February 2027
    clock.current = datetime(2027, 2, 28, 8, 0, 10)

    moved = store.fired("j1", datetime(2027, 2, 28, 8, 0))

    assert (moved.at, moved.day) == (datetime(2028, 2, 29, 8, 0), 29)
    assert store.entries() == [moved]


def test_a_late_fire_moves_the_entry_past_now_so_it_never_fires_twice(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add(TODAY_15, "daily", MEDICINE)
    clock.current = TODAY_15 + timedelta(minutes=20)

    moved = store.fired("j1", TODAY_15)

    assert moved.at == datetime(2026, 10, 14, 15, 0)
    assert due(store.entries(), clock.current, timedelta(minutes=30)) == ([], [])


def test_an_entry_skipped_after_days_moves_to_its_first_time_after_now(tmp_path):
    store = make_store(tmp_path)
    write_schedule(store.path, [raw_entry("j1", "2026-10-05T07:00", "daily")], next_id=2)

    assert store.fired("j1", datetime(2026, 10, 5, 7, 0)).at == datetime(2026, 10, 14, 7, 0)


def test_firing_the_same_occurrence_twice_moves_the_entry_once(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add(TODAY_15, "daily", MEDICINE)
    clock.current = TODAY_15

    first = store.fired("j1", TODAY_15)
    second = store.fired("j1", TODAY_15)

    assert first == second
    assert [e.at for e in store.entries()] == [datetime(2026, 10, 14, 15, 0)]


def test_firing_an_unknown_id_returns_none_and_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    store.add(TODAY_15, "daily", MEDICINE)
    saved = store.path.read_bytes()

    assert store.fired("j9", TODAY_15) is None
    assert store.path.read_bytes() == saved


def test_firing_on_an_empty_schedule_creates_no_file(tmp_path):
    store = make_store(tmp_path)

    assert store.fired("j1", TODAY_15) is None
    assert not store.path.exists()


def test_an_entry_cancelled_before_it_is_marked_fired_stays_cancelled(tmp_path):
    # Master cancels from the terminal while Cyrene is firing the entry.
    clock = Clock()
    cyrene, terminal = make_store(tmp_path, clock), make_store(tmp_path, clock)
    cyrene.add(TODAY_15, "daily", MEDICINE)
    other = cyrene.add(TODAY_15 + timedelta(hours=1), "daily", RoutineAction("tidur"))
    clock.current = TODAY_15
    (firing,), _ = due(cyrene.entries(), clock.current, timedelta(minutes=30))

    terminal.cancel(firing.id)

    assert cyrene.fired(firing.id, firing.at) is None
    assert cyrene.entries() == [other]


def test_an_entry_added_after_a_cancel_is_untouched_by_firing_the_old_id(tmp_path):
    clock = Clock()
    store = make_store(tmp_path, clock)
    store.add(TODAY_15, "none", MEDICINE)
    store.cancel("j1")
    new = store.add(TODAY_15, "none", MEDICINE)
    clock.current = TODAY_15

    assert store.fired("j1", TODAY_15) is None
    assert store.entries() == [new]


# --- store: skipped ----------------------------------------------------------------------------

EARLIEST = datetime(2026, 10, 13, 14, 0)  # the clock's 14:30 less 30 minutes of grace


def test_a_skipped_one_shot_entry_is_removed(tmp_path):
    store = make_store(tmp_path)
    write_schedule(
        store.path,
        [raw_entry("j1", "2026-10-12T15:00"), raw_entry("j2", "2026-10-13T15:00")],
        next_id=3,
    )

    assert store.skipped("j1", datetime(2026, 10, 12, 15, 0), EARLIEST) is None
    assert [e.id for e in store.entries()] == ["j2"]


@pytest.mark.parametrize(
    ("at", "repeat", "expected"),
    [
        # Today's occurrence is within the grace, so it is the next one to fire.
        ("2026-10-10T14:15", "daily", datetime(2026, 10, 13, 14, 15)),
        ("2026-10-10T14:00", "daily", datetime(2026, 10, 13, 14, 0)),
        ("2026-10-09T14:15", "weekdays", datetime(2026, 10, 13, 14, 15)),
        # Today's is too late as well.
        ("2026-10-10T13:59", "daily", datetime(2026, 10, 14, 13, 59)),
        ("2026-10-10T14:15", "weekends", datetime(2026, 10, 17, 14, 15)),
    ],
)
def test_a_skipped_repeating_entry_moves_to_its_first_time_not_before_earliest(
    tmp_path, at, repeat, expected
):
    store = make_store(tmp_path)
    write_schedule(store.path, [raw_entry("j1", at, repeat)], next_id=2)

    moved = store.skipped("j1", datetime.fromisoformat(at), EARLIEST)

    assert moved.at == expected
    assert store.entries() == [moved]


@pytest.mark.parametrize(
    ("raw", "earliest", "expected"),
    [
        # Missed last Monday; next Monday is the first one not before earliest.
        (raw_entry("j1", "2026-10-05T07:00", "weekly"), EARLIEST, datetime(2026, 10, 19, 7, 0)),
        # The 31st, missed on 30 September: back to the 31st in October, not the 30th.
        (
            raw_entry("j1", "2026-09-30T09:00", "monthly") | {"day": 31},
            EARLIEST,
            datetime(2026, 10, 31, 9, 0),
        ),
        # 29 February, missed on 28 February 2027: the next one is 29 February 2028.
        (
            raw_entry("j1", "2027-02-28T08:00", "yearly") | {"day": 29},
            datetime(2027, 3, 1, 9, 0),
            datetime(2028, 2, 29, 8, 0),
        ),
        # Today's 5 May is still within the grace, so it fires now.
        (
            raw_entry("j1", "2025-05-05T08:00", "yearly") | {"day": 5},
            datetime(2026, 5, 5, 7, 45),
            datetime(2026, 5, 5, 8, 0),
        ),
    ],
)
def test_a_skipped_weekly_monthly_or_yearly_entry_moves_on_with_its_day(
    tmp_path, raw, earliest, expected
):
    store = make_store(tmp_path)
    write_schedule(store.path, [raw], next_id=2)

    moved = store.skipped("j1", datetime.fromisoformat(raw["at"]), earliest)

    assert (moved.at, moved.day) == (expected, raw.get("day"))
    assert store.entries() == [moved]


def test_skipping_an_unknown_id_returns_none_and_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    store.add(TODAY_15, "daily", MEDICINE)
    saved = store.path.read_bytes()

    assert store.skipped("j9", TODAY_15, EARLIEST) is None
    assert store.path.read_bytes() == saved


# --- store: clear ------------------------------------------------------------------------------


def test_clear_removes_every_entry_returns_the_count_and_never_reuses_ids(tmp_path):
    store = make_store(tmp_path)
    store.add(TODAY_15, "none", MEDICINE)
    store.add(TODAY_15, "daily", RoutineAction("tidur"))

    assert store.clear() == 2
    assert store.entries() == []
    assert store.add(TODAY_15, "none", MEDICINE).id == "j3"


def test_clear_of_an_empty_schedule_writes_nothing(tmp_path):
    store = make_store(tmp_path)

    assert store.clear() == 0
    assert not store.path.exists()


# --- several stores and threads ----------------------------------------------------------------


def test_a_second_store_on_the_same_file_sees_changes_at_once(tmp_path):
    cyrene = make_store(tmp_path)
    terminal = make_store(tmp_path)

    cyrene.add(TODAY_15, "none", MEDICINE)
    assert [e.id for e in terminal.entries()] == ["j1"]

    terminal.cancel("j1")
    assert cyrene.entries() == []
    assert cyrene.add(TODAY_15, "none", MEDICINE).id == "j2"


def run_threads(*targets):
    errors = []

    def guarded(target):
        try:
            target()
        except Exception as exc:  # surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=guarded, args=(target,)) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []


def test_concurrent_adds_from_threads_keep_every_entry(tmp_path):
    stores = [make_store(tmp_path), make_store(tmp_path)]

    def adder(worker):
        def add_entries():
            for index in range(5):
                stores[worker % 2].add(TODAY_15, "none", Reminder(f"Pengingat {worker}-{index}."))

        return add_entries

    run_threads(*(adder(worker) for worker in range(8)))

    entries = stores[0].entries()
    assert sorted(e.id for e in entries) == sorted(f"j{n}" for n in range(1, 41))
    assert len({e.action for e in entries}) == 40
    assert read_json(stores[0].path)["next_id"] == 41


def test_concurrent_adds_cancels_and_fires_lose_nothing(tmp_path):
    clock = Clock()
    cyrene, terminal = make_store(tmp_path, clock), make_store(tmp_path, clock)
    for _ in range(10):
        cyrene.add(TODAY_15, "none", MEDICINE)  # j1-j10
    for _ in range(10):
        cyrene.add(TODAY_15, "daily", MEDICINE)  # j11-j20
    clock.current = TODAY_15

    run_threads(
        lambda: [cyrene.fired(f"j{n}", TODAY_15) for n in range(1, 11)],
        lambda: [terminal.cancel(f"j{n}") for n in range(11, 21)],
        lambda: [terminal.add(TODAY_15 + timedelta(days=1), "none", MEDICINE) for _ in range(10)],
    )

    assert sorted(e.id for e in cyrene.entries()) == sorted(f"j{n}" for n in range(21, 31))
    assert read_json(cyrene.path)["next_id"] == 31


# --- atomic writes -----------------------------------------------------------------------------


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    store = make_store(tmp_path)

    store.add(TODAY_15, "none", MEDICINE)

    assert [path.name for path in tmp_path.iterdir()] == ["schedule.json"]


def test_a_failed_replace_keeps_the_previous_file_intact(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add(TODAY_15, "none", MEDICINE)
    saved = store.path.read_bytes()

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(schedule.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        store.add(TODAY_15, "daily", MEDICINE)

    assert store.path.read_bytes() == saved
    assert [path.name for path in tmp_path.iterdir()] == ["schedule.json"]


def test_new_entries_reach_the_disk_before_the_file_is_replaced(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.add(TODAY_15, "none", MEDICINE)
    events = []
    real_fsync, real_replace = schedule.os.fsync, schedule.os.replace

    def recording_fsync(fd):
        real_fsync(fd)
        (temp_file,) = [path for path in tmp_path.iterdir() if path.name != "schedule.json"]
        events.append(("fsync", [e["id"] for e in read_json(temp_file)["entries"]]))

    def recording_replace(src, dst):
        events.append(("replace", None))
        real_replace(src, dst)

    monkeypatch.setattr(schedule.os, "fsync", recording_fsync)
    monkeypatch.setattr(schedule.os, "replace", recording_replace)

    store.add(TODAY_15, "daily", MEDICINE)

    assert events == [("fsync", ["j1", "j2"]), ("replace", None)]


def test_a_windows_path_with_spaces_works(tmp_path):
    path = tmp_path / "Data Cyrene" / "jadwal saya" / "jadwal Master.json"
    ScheduleStore(path, now=Clock()).add(TODAY_15, "none", MEDICINE)

    assert [e.id for e in ScheduleStore(path).entries()] == ["j1"]


# --- corrupt files -----------------------------------------------------------------------------

VALID = raw_entry("j1", "2026-10-13T15:00")


def corrupt_file(entries, next_id=2, version=1):
    return json.dumps({"version": version, "next_id": next_id, "entries": entries}).encode()


CORRUPT_CONTENTS = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version": 1, "next_id": 2, "entries": [\xff\xfe]}',
    "a list": b"[]",
    "missing entries": json.dumps({"version": 1, "next_id": 2}).encode(),
    "an extra key": json.dumps(
        {"version": 1, "next_id": 2, "entries": [], "routines": []}
    ).encode(),
    "another version": corrupt_file([], version=2),
    "version true": corrupt_file([], version=True),
    "next_id text": corrupt_file([], next_id="2"),
    "next_id true": corrupt_file([], next_id=True),
    "next_id zero": corrupt_file([], next_id=0),
    "entries an object": corrupt_file({}),
    "entry a string": corrupt_file(["minum obat"]),
    "entry without created": corrupt_file([{k: v for k, v in VALID.items() if k != "created"}]),
    "entry with an extra key": corrupt_file([VALID | {"note": "obat"}]),
    "numeric id": corrupt_file([VALID | {"id": 1}]),
    "id of a memory": corrupt_file([VALID | {"id": "m1"}]),
    "id j0": corrupt_file([VALID | {"id": "j0"}]),
    "id with a newline": corrupt_file([VALID | {"id": "j1\n"}]),
    "at not a time": corrupt_file([VALID | {"at": "jam tiga"}]),
    "at a number": corrupt_file([VALID | {"at": 202610131500}]),
    "at with a time zone": corrupt_file([VALID | {"at": "2026-10-13T15:00+07:00"}]),
    "created in utc": corrupt_file([VALID | {"created": "2026-10-13T08:00Z"}]),
    "unknown repeat": corrupt_file([VALID | {"repeat": "hourly"}]),
    "null repeat": corrupt_file([VALID | {"repeat": None}]),
    "day on a one-shot entry": corrupt_file([VALID | {"day": 13}]),
    "day on a daily entry": corrupt_file([VALID | {"repeat": "daily", "day": 13}]),
    "day on a weekly entry": corrupt_file([VALID | {"repeat": "weekly", "day": 13}]),
    "monthly without day": corrupt_file([VALID | {"repeat": "monthly"}]),
    "yearly without day": corrupt_file([VALID | {"repeat": "yearly"}]),
    "day null": corrupt_file([VALID | {"repeat": "monthly", "day": None}]),
    "day text": corrupt_file([VALID | {"repeat": "monthly", "day": "13"}]),
    "day a float": corrupt_file([VALID | {"repeat": "monthly", "day": 13.0}]),
    "day true": corrupt_file(
        [VALID | {"at": "2026-10-01T15:00", "repeat": "monthly", "day": True}]
    ),
    "day 0": corrupt_file([VALID | {"repeat": "monthly", "day": 0}]),
    "day 32": corrupt_file([VALID | {"at": "2026-10-31T15:00", "repeat": "monthly", "day": 32}]),
    "day other than the day of at": corrupt_file([VALID | {"repeat": "monthly", "day": 14}]),
    "day 31 on the 30th of a long month": corrupt_file(
        [VALID | {"at": "2026-10-30T15:00", "repeat": "monthly", "day": 31}]
    ),
    "day 29 on 28 february of a leap year": corrupt_file(
        [VALID | {"at": "2028-02-28T15:00", "repeat": "yearly", "day": 29}]
    ),
    "yearly on 31 april": corrupt_file(
        [VALID | {"at": "2027-04-30T15:00", "repeat": "yearly", "day": 31}]
    ),
    "yearly on 30 february": corrupt_file(
        [VALID | {"at": "2027-02-28T15:00", "repeat": "yearly", "day": 30}]
    ),
    "action a string": corrupt_file([VALID | {"action": "minum obat"}]),
    "action without type": corrupt_file([VALID | {"action": {"text": "Minum obat."}}]),
    "unknown action type": corrupt_file([VALID | {"action": {"type": "pc", "command": "sleep"}}]),
    "action type a list": corrupt_file([VALID | {"action": {"type": ["reminder"], "text": "x"}}]),
    "reminder with an extra key": corrupt_file(
        [VALID | {"action": RAW_MEDICINE | {"device": "ac"}}]
    ),
    "reminder text a number": corrupt_file([VALID | {"action": {"type": "reminder", "text": 3}}]),
    "device without value": corrupt_file(
        [VALID | {"action": {"type": "device", "device": "ac", "command": "off"}}]
    ),
    "device value true": corrupt_file(
        [VALID | {"action": {"type": "device", "device": "ac", "command": "on", "value": True}}]
    ),
    "device value a float": corrupt_file(
        [VALID | {"action": {"type": "device", "device": "ac", "command": "t", "value": 2.5}}]
    ),
    "device value text": corrupt_file(
        [VALID | {"action": {"type": "device", "device": "ac", "command": "t", "value": "26"}}]
    ),
    "device command null": corrupt_file(
        [VALID | {"action": {"type": "device", "device": "ac", "command": None, "value": None}}]
    ),
    "device routine a number": corrupt_file(
        [
            VALID
            | {
                "action": {
                    "type": "device",
                    "device": "ac",
                    "command": "off",
                    "value": None,
                    "routine": 1,
                }
            }
        ]
    ),
    "device routine null": corrupt_file(
        [
            VALID
            | {
                "action": {
                    "type": "device",
                    "device": "ac",
                    "command": "off",
                    "value": None,
                    "routine": None,
                }
            }
        ]
    ),
    "routine name a number": corrupt_file([VALID | {"action": {"type": "routine", "name": 1}}]),
    "duplicate ids": corrupt_file([VALID, VALID | {"at": "2026-10-13T16:00"}], next_id=3),
}


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
def test_a_corrupt_file_is_moved_aside_logged_and_the_schedule_starts_empty(
    tmp_path, caplog, content
):
    store = make_store(tmp_path)
    store.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.schedule"):
        assert store.entries() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert not store.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_the_log_of_a_corrupt_file_never_quotes_an_entry(tmp_path, caplog):
    store = make_store(tmp_path)
    secret = "Master, transfer uang ke Budi jam tiga"
    write_schedule(
        store.path,
        [raw_entry("j1", "2026-10-13T15:00", "hourly", {"type": "reminder", "text": secret})],
        next_id=2,
    )

    with caplog.at_level(logging.ERROR, logger="waifu.schedule"):
        store.entries()

    assert "repeat" in caplog.text
    assert secret not in caplog.text
    assert "hourly" not in caplog.text


def test_the_log_of_a_wrong_day_names_the_key_and_never_quotes_the_entry(tmp_path, caplog):
    store = make_store(tmp_path)
    secret = "Master, bayar cicilan ke Budi"
    write_schedule(
        store.path,
        [
            raw_entry("j1", "2026-10-13T15:00", "monthly", {"type": "reminder", "text": secret})
            | {"day": 14}
        ],
        next_id=2,
    )

    with caplog.at_level(logging.ERROR, logger="waifu.schedule"):
        store.entries()

    assert "entries[0].day" in caplog.text
    assert secret not in caplog.text


def test_a_corrupt_file_is_never_overwritten_by_a_later_entry(tmp_path):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    added = store.add(TODAY_15, "none", MEDICINE)

    assert added.id == "j1"
    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"{not json"
    assert store.entries() == [added]


def test_a_corrupt_copy_with_the_same_stamp_gets_a_counter(tmp_path):
    store = make_store(tmp_path)
    (tmp_path / CORRUPT_NAME).write_bytes(b"first")
    (tmp_path / f"{CORRUPT_NAME}-2").write_bytes(b"second")
    store.path.write_bytes(b"third")

    assert store.entries() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"first"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_bytes() == b"second"
    assert (tmp_path / f"{CORRUPT_NAME}-3").read_bytes() == b"third"


@pytest.mark.parametrize(
    "change",
    [
        lambda store: store.add(TODAY_15, "none", MEDICINE),
        lambda store: store.fired("j1", TODAY_15),
        lambda store: store.clear(),
    ],
    ids=["add", "fired", "clear"],
)
def test_nothing_is_written_when_a_corrupt_file_cannot_be_moved(tmp_path, monkeypatch, change):
    store = make_store(tmp_path)
    store.path.write_bytes(b"{not json")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(schedule.os, "rename", failing_rename)
    with pytest.raises(OSError, match="file in use"):
        change(store)

    assert store.path.read_bytes() == b"{not json"
    assert [path.name for path in tmp_path.iterdir()] == ["schedule.json"]
