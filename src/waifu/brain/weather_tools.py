"""The weather of the days ahead: which days and parts of a day a question asks for.

"Besok cuaca gimana?", "akhir pekan hujan nggak?", "nanti sore hujan?": the answer is read from
a seven-day `Outlook` (`WeatherClient.outlook()`), whose dates and times are the place's own, so
today is the date of the reply's time now. A later day as a whole is its daily values. A part of
a day, and the rest of today, come from the hourly entries: an entry at T holds the hour before
T, so the part from a to b reads the entries after a up to b, and on today only the entries
after now count. The parts carry the words `spoken_hour` gives their hours: pagi from 04:00,
siang from 11:00, sore from 15:00, and malam from 19:00 to midnight.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from waifu.brain.persona import WEEKDAYS_ID
from waifu.weather import DayOutlook, Hour, Outlook, condition_of

PARTS = ("morning", "afternoon", "evening", "night")
# Each part's Indonesian word and the hours it covers, from its start up to its end.
_PART_HOURS = {
    "morning": ("pagi", 4, 11),
    "afternoon": ("siang", 11, 15),
    "evening": ("sore", 15, 19),
    "night": ("malam", 19, 24),
}
REST_OF_TODAY = "sisa hari ini"
_NOT_AVAILABLE = "Prakiraan cuaca untuk waktu itu tidak ada."


class ForecastError(Exception):
    """Why a question about the days ahead has no answer; Indonesian and safe to speak."""


@dataclass(frozen=True)
class Period:
    """The weather of a day or a part of it; None where nothing is known."""

    day: date  # the place's local date
    part: str | None  # the part's Indonesian word or REST_OF_TODAY; None for the whole day
    condition: str | None  # the most severe weather, in Indonesian
    min_c: float | None
    max_c: float | None
    rain_chance: int | None  # the highest precipitation probability, %
    rain_mm: float | None  # the summed precipitation, to a tenth


def periods(outlook: Outlook, day: str | date, part: str | None) -> list[Period]:
    """The weather `day` asks for: "today", "tomorrow", "weekend" (the Saturday and Sunday from
    today on), or a date of the outlook; with `part` (one of `PARTS`) only that part of each day.
    A part of today that has passed is left out of a weekend. ForecastError for a day out of the
    outlook, a part of today that has passed, or a part without hourly entries."""
    days = _days(outlook, day)
    found = [period for when in days if (period := period_of(outlook, when, part)) is not None]
    if found:
        return found
    today = outlook.now.date()
    if part is not None and today in days and outlook.now >= _part_bounds(today, part)[1]:
        word = _PART_HOURS[part][0]
        raise ForecastError(f"{word.capitalize()} ini sudah lewat.")
    raise ForecastError(_NOT_AVAILABLE)


def period_of(outlook: Outlook, day: date, part: str | None) -> Period | None:
    """The weather of `day`, or of its `part` (one of `PARTS`): a later day as a whole is its
    daily values, today as a whole the rest of today. None when no hourly entry is left for it
    (a part of today that has passed). ForecastError for a whole day out of the outlook."""
    today = outlook.now.date()
    if part is None and day != today:
        return _whole_day(_day_of(outlook, day))
    if part is None:
        word, start, end = REST_OF_TODAY, outlook.now, _midnight(day) + timedelta(days=1)
    else:
        word = _PART_HOURS[part][0]
        start, end = _part_bounds(day, part)
    if day == today:
        start = max(start, outlook.now)
    hours = [hour for hour in outlook.hours if start < hour.at <= end]
    return _summary(day, word, hours) if hours else None


def _days(outlook: Outlook, day: str | date) -> list[date]:
    today = outlook.now.date()
    if day == "today":
        days = [today]
    elif day == "tomorrow":
        days = [today + timedelta(days=1)]
    elif day == "weekend":
        days = _weekend(today)
    elif isinstance(day, date):
        days = [day]
    else:
        raise ValueError(f"not a day: {day!r}")
    for when in days:
        _day_of(outlook, when)
    return days


def _weekend(today: date) -> list[date]:
    """The Saturday and Sunday from today on: on a Sunday only today."""
    if today.weekday() == 6:
        return [today]
    saturday = today + timedelta(days=5 - today.weekday())
    return [saturday, saturday + timedelta(days=1)]


def _day_of(outlook: Outlook, day: date) -> DayOutlook:
    for known in outlook.days:
        if known.day == day:
            return known
    last = outlook.days[-1].day
    raise ForecastError(
        f"Prakiraan cuaca hanya ada untuk hari ini sampai {WEEKDAYS_ID[last.weekday()]} "
        f"{last:%d-%m-%Y}."
    )


def _whole_day(day: DayOutlook) -> Period:
    return Period(
        day=day.day,
        part=None,
        condition=None if day.code is None else condition_of(day.code),
        min_c=day.min_c,
        max_c=day.max_c,
        rain_chance=day.rain_chance,
        rain_mm=day.rain_mm,
    )


def _summary(day: date, part: str, hours: list[Hour]) -> Period:
    """The hours' most severe code (as Open-Meteo picks a day's), highest chance, summed rain,
    and lowest and highest temperature, each from the known values only."""
    codes = [hour.code for hour in hours if hour.code is not None]
    chances = [hour.rain_chance for hour in hours if hour.rain_chance is not None]
    amounts = [hour.precipitation_mm for hour in hours if hour.precipitation_mm is not None]
    temperatures = [hour.temperature_c for hour in hours if hour.temperature_c is not None]
    return Period(
        day=day,
        part=part,
        condition=condition_of(max(codes)) if codes else None,
        min_c=min(temperatures) if temperatures else None,
        max_c=max(temperatures) if temperatures else None,
        rain_chance=max(chances) if chances else None,
        rain_mm=round(sum(amounts), 1) if amounts else None,
    )


def _part_bounds(day: date, part: str) -> tuple[datetime, datetime]:
    _, start, end = _PART_HOURS[part]
    return _midnight(day) + timedelta(hours=start), _midnight(day) + timedelta(hours=end)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time())
