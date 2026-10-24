"""The weather_forecast tool: which days and parts of a day a question asks for, read from a
seven-day outlook built by the test, and the tool itself over a real WeatherClient whose
Open-Meteo is a fake. The outlook request itself is tested in test_weather.py."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from waifu.brain.weather_tools import (
    REST_OF_TODAY,
    ForecastError,
    Period,
    period_of,
    periods,
)
from waifu.weather import DayOutlook, Hour, Outlook

SATURDAY = date(2026, 10, 24)
SUNDAY = date(2026, 10, 25)
# The daily codes of the seven days: badai petir, mendung, hujan, hujan lokal, berawan,
# cerah berawan, cerah.
DAILY_CODES = (95, 3, 61, 80, 2, 1, 0)


def make_outlook(now: datetime, *, days: int = 7) -> Outlook:
    """Seven days from `now`'s date: day n has the code DAILY_CODES[n], 18+n to 28+n °C, a
    10n % chance, and n mm; every hour is berawan with a 10 % chance and no rain, at 20 °C plus
    half a degree per hour of the day. With `days`, the hourly entries stop after that many
    days."""
    first = now.date()
    return Outlook(
        place="Bandung, Jawa Barat",
        now=now,
        days=tuple(
            DayOutlook(
                first + timedelta(days=n), DAILY_CODES[n], 18.0 + n, 28.0 + n, 10 * n, float(n)
            )
            for n in range(7)
        ),
        hours=tuple(
            Hour(at, 2, 10, 0.0, 20.0 + at.hour / 2)
            for at in (
                datetime.combine(first, time()) + timedelta(hours=n) for n in range(24 * days)
            )
        ),
    )


def with_hours(outlook: Outlook, changes: dict[datetime, dict]) -> Outlook:
    """The outlook with the hourly entries at the given times changed."""
    return replace(
        outlook,
        hours=tuple(
            replace(hour, **changes[hour.at]) if hour.at in changes else hour
            for hour in outlook.hours
        ),
    )


def at(day: date, hour: int) -> datetime:
    return datetime.combine(day, time()) + timedelta(hours=hour)


SATURDAY_NIGHT = make_outlook(at(SATURDAY, 20) + timedelta(minutes=15))


# --- a day or a part of it ----------------------------------------------------------------------


def test_a_later_day_without_a_part_is_its_daily_values():
    assert period_of(SATURDAY_NIGHT, SUNDAY, None) == Period(
        SUNDAY, None, "mendung", 19.0, 29.0, 10, 1.0
    )


@pytest.mark.parametrize(
    ("part", "word", "low", "high"),
    [
        ("morning", "pagi", 22.5, 25.5),  # the entries 05:00-11:00: the hours from 04:00 to 11:00
        ("afternoon", "siang", 26.0, 27.5),  # 12:00-15:00
        ("evening", "sore", 28.0, 29.5),  # 16:00-19:00
        ("night", "malam", 20.0, 31.5),  # 20:00-23:00 and the next day's 00:00
    ],
)
def test_a_part_reads_the_entries_after_its_start_up_to_its_end(part, word, low, high):
    assert period_of(SATURDAY_NIGHT, SUNDAY, part) == Period(
        SUNDAY, word, "berawan", low, high, 10, 0.0
    )


def test_an_entry_holds_the_hour_before_it_so_the_part_s_borders_belong_to_their_neighbours():
    outlook = with_hours(
        SATURDAY_NIGHT,
        {
            at(SUNDAY, 15): {"code": 95, "rain_chance": 90},  # 14:00-15:00, siang
            at(SUNDAY, 16): {"code": 61, "rain_chance": 70, "precipitation_mm": 1.2},  # sore
            at(SUNDAY, 20): {"code": 95, "rain_chance": 90},  # 19:00-20:00, malam
        },
    )

    evening = period_of(outlook, SUNDAY, "evening")

    assert (evening.condition, evening.rain_chance, evening.rain_mm) == ("hujan", 70, 1.2)


def test_a_part_is_its_most_severe_code_its_highest_chance_and_its_summed_rain():
    outlook = with_hours(
        SATURDAY_NIGHT,
        {
            at(SUNDAY, 16): {"code": 80, "rain_chance": 40, "precipitation_mm": 0.1},
            at(SUNDAY, 17): {"code": 61, "rain_chance": 90, "precipitation_mm": 0.2},
            at(SUNDAY, 18): {"code": None, "rain_chance": None, "precipitation_mm": None},
        },
    )

    evening = period_of(outlook, SUNDAY, "evening")

    assert (evening.condition, evening.rain_chance, evening.rain_mm) == ("hujan lokal", 90, 0.3)


def test_a_part_whose_values_are_all_unknown_says_nothing_of_them():
    unknown = {"code": None, "rain_chance": None, "precipitation_mm": None, "temperature_c": None}
    outlook = with_hours(SATURDAY_NIGHT, {at(SUNDAY, hour): unknown for hour in range(16, 20)})

    assert period_of(outlook, SUNDAY, "evening") == Period(
        SUNDAY, "sore", None, None, None, None, None
    )


def test_today_without_a_part_is_the_rest_of_today_from_the_hourly_entries():
    outlook = with_hours(
        SATURDAY_NIGHT,
        {
            at(SATURDAY, 20): {"code": 95, "rain_chance": 95},  # 19:00-20:00: past at 20:15
            at(SATURDAY, 21): {"code": 61, "rain_chance": 60, "precipitation_mm": 2.0},
        },
    )

    # Not the daily values (badai petir, 18-28 °C, 0 %): the entries 21:00 to Sunday 00:00.
    assert period_of(outlook, SATURDAY, None) == Period(
        SATURDAY, REST_OF_TODAY, "hujan", 20.0, 31.5, 60, 2.0
    )


def test_a_part_of_today_counts_only_the_hours_from_the_one_running_now():
    outlook = with_hours(
        make_outlook(at(SATURDAY, 16) + timedelta(minutes=30)),
        {at(SATURDAY, 16): {"code": 95}},  # 15:00-16:00: past at 16:30
    )

    evening = period_of(outlook, SATURDAY, "evening")

    assert evening == Period(SATURDAY, "sore", "berawan", 28.5, 29.5, 10, 0.0)


@pytest.mark.parametrize("part", ["morning", "afternoon", "evening"])
def test_a_part_of_today_that_has_passed_is_none(part):
    assert period_of(SATURDAY_NIGHT, SATURDAY, part) is None


def test_tonight_is_never_passed_before_midnight():
    late = make_outlook(at(SATURDAY, 23) + timedelta(minutes=50))

    assert period_of(late, SATURDAY, "night") == Period(
        SATURDAY, "malam", "berawan", 20.0, 20.0, 10, 0.0
    )


def test_the_last_day_s_night_has_no_next_midnight_and_reads_what_is_there():
    last = SATURDAY + timedelta(days=6)

    assert period_of(SATURDAY_NIGHT, last, "night") == Period(
        last, "malam", "berawan", 30.0, 31.5, 10, 0.0
    )


def test_a_part_on_a_day_without_hourly_entries_is_none():
    short = make_outlook(at(SATURDAY, 8), days=2)

    assert period_of(short, SATURDAY + timedelta(days=3), "evening") is None


# --- the days asked -----------------------------------------------------------------------------


def test_today_tomorrow_and_a_date_are_one_day_each():
    assert [period.part for period in periods(SATURDAY_NIGHT, "today", None)] == [REST_OF_TODAY]
    assert periods(SATURDAY_NIGHT, "tomorrow", None) == [period_of(SATURDAY_NIGHT, SUNDAY, None)]
    assert periods(SATURDAY_NIGHT, date(2026, 10, 30), "morning") == [
        period_of(SATURDAY_NIGHT, date(2026, 10, 30), "morning")
    ]


def test_today_s_date_is_today():
    assert periods(SATURDAY_NIGHT, SATURDAY, None) == periods(SATURDAY_NIGHT, "today", None)


@pytest.mark.parametrize(
    ("now", "weekend"),
    [
        (at(date(2026, 10, 26), 9), [date(2026, 10, 31), date(2026, 11, 1)]),  # Monday
        (at(date(2026, 10, 30), 9), [date(2026, 10, 31), date(2026, 11, 1)]),  # Friday
        (at(SATURDAY, 9), [SATURDAY, SUNDAY]),
        (at(SUNDAY, 9), [SUNDAY]),
    ],
    ids=["monday", "friday", "saturday", "sunday"],
)
def test_the_weekend_is_the_saturday_and_sunday_from_today_on(now, weekend):
    outlook = make_outlook(now)

    assert [period.day for period in periods(outlook, "weekend", None)] == weekend


def test_the_weekend_today_is_the_rest_of_today_and_later_days_are_whole():
    weekend = periods(make_outlook(at(SATURDAY, 9)), "weekend", None)

    assert [period.part for period in weekend] == [REST_OF_TODAY, None]


def test_a_weekend_part_that_has_passed_today_leaves_only_tomorrow_s():
    weekend = periods(SATURDAY_NIGHT, "weekend", "evening")

    assert weekend == [period_of(SATURDAY_NIGHT, SUNDAY, "evening")]


def test_a_weekend_part_that_has_passed_on_sunday_has_passed():
    sunday_night = make_outlook(at(SUNDAY, 20))

    with pytest.raises(ForecastError, match=r"^Sore ini sudah lewat\.$"):
        periods(sunday_night, "weekend", "evening")


@pytest.mark.parametrize(
    ("part", "message"),
    [
        ("morning", "Pagi ini sudah lewat."),
        ("afternoon", "Siang ini sudah lewat."),
        ("evening", "Sore ini sudah lewat."),
    ],
)
def test_a_part_of_today_that_has_passed_is_a_forecast_error_saying_so(part, message):
    with pytest.raises(ForecastError) as caught:
        periods(SATURDAY_NIGHT, "today", part)

    assert str(caught.value) == message


@pytest.mark.parametrize(
    "day", [date(2026, 10, 31), date(2026, 10, 23), date(2027, 1, 1)], ids=["+7", "-1", "far"]
)
def test_a_day_out_of_the_seven_says_how_far_the_forecast_goes(day):
    with pytest.raises(ForecastError) as caught:
        periods(SATURDAY_NIGHT, day, None)

    assert str(caught.value) == "Prakiraan cuaca hanya ada untuk hari ini sampai Jumat 30-10-2026."


def test_a_part_without_hourly_entries_is_a_forecast_error_even_when_today_s_has_passed():
    short = make_outlook(at(SATURDAY, 20), days=2)

    with pytest.raises(ForecastError) as caught:
        periods(short, date(2026, 10, 27), "evening")

    assert str(caught.value) == "Prakiraan cuaca untuk waktu itu tidak ada."


def test_a_day_word_the_tool_does_not_know_is_a_programming_error():
    with pytest.raises(ValueError):
        periods(SATURDAY_NIGHT, "lusa", None)


def test_after_midnight_today_is_the_new_date_of_the_reply():
    # 00:30 on Sunday: Open-Meteo's today is Sunday, so "besok" is Monday.
    after_midnight = make_outlook(at(SUNDAY, 0) + timedelta(minutes=30))

    [today] = periods(after_midnight, "today", None)
    [tomorrow] = periods(after_midnight, "tomorrow", None)
    [tonight] = periods(after_midnight, "today", "night")

    assert (today.day, today.part) == (SUNDAY, REST_OF_TODAY)
    assert (tomorrow.day, tomorrow.part) == (date(2026, 10, 26), None)
    assert (tonight.day, tonight.part) == (SUNDAY, "malam")
