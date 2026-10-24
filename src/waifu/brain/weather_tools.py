"""The tool that tells the weather of the days ahead: `weather_forecast`.

"Besok cuaca gimana?", "akhir pekan hujan nggak?", "nanti sore hujan?": the answer is read from
a seven-day `Outlook` (`WeatherClient.outlook()`, asked once per call), whose dates and times are
the place's own, so today is the date of the reply's time now. A later day as a whole is its
daily values. A part of a day, and the rest of today, come from the hourly entries: an entry at T
holds the hour before T, so the part from a to b reads the entries after a up to b, and on today
only the entries after now count. The parts carry the words `spoken_hour` gives their hours:
pagi from 04:00, siang from 11:00, sore from 15:00, and malam from 19:00 to midnight.

The tool's arguments may all be left out, so it takes no strict slot, and its spec is static
English built once, so it stays byte-identical for the brain's lifetime and keeps the prompt
cache warm. The place is named as the rain alert names it: the city as written in the config,
none with coordinates only. It refuses in the turns she starts herself (the morning greeting,
the weekly game report, and the nightly journal question), so the greeting stays as it was.
Guest mode leaves it open: the weather of the city she names holds nothing private. `run`
never raises.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from waifu.brain.base import ToolSpec
from waifu.brain.persona import WEEKDAYS_ID
from waifu.briefing import BRIEFING_PROMPT
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.weather import DayOutlook, Hour, Outlook, WeatherClient, WeatherError, condition_of

logger = logging.getLogger(__name__)

WEATHER_FORECAST = "weather_forecast"
PARTS = ("morning", "afternoon", "evening", "night")
_DAY_WORDS = ("today", "tonight", "tomorrow", "weekend")
_KEYS = frozenset({"day", "part"})
# date.fromisoformat also reads 20261025 and 2026-W43-7; the tool takes only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# The turns she starts herself, each with what the tool answers in it.
_OWN_TURN_REFUSALS = {
    BRIEFING_PROMPT: "Prakiraan cuaca hanya untuk pertanyaan Master, bukan untuk sapaan pagi.",
    WEEKLY_REPORT_PROMPT: (
        "Prakiraan cuaca hanya untuk pertanyaan Master, bukan untuk laporan game mingguan."
    ),
    JOURNAL_PROMPT: (
        "Prakiraan cuaca hanya untuk pertanyaan Master, bukan untuk pertanyaan jurnal malam."
    ),
}
_FAILED = "Prakiraan cuaca tidak bisa dibaca saat ini."
_BAD_DAY = (
    "'day' harus today, tonight, tomorrow, weekend, atau tanggal YYYY-MM-DD, misalnya 2026-10-25."
)
_BAD_PART = "'part' harus morning, afternoon, evening, atau night."
_TONIGHT_PART = "'tonight' sudah berarti malam ini; kosongkan 'part' atau isi night."
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


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class WeatherForecastTools:
    """A `ToolProvider` for the brain: the weather of the coming days at Master's place."""

    def __init__(self, weather: WeatherClient, *, place: str | None) -> None:
        """`place` is the name she says, the city as written in the config; None names none."""
        self._weather = weather
        self._place = place
        self._spec = _spec()
        self._refusal: str | None = None

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with its words as heard: a turn she starts herself, the morning
        greeting, the weekly game report, or the nightly journal question, gets no forecast."""
        self._refusal = _OWN_TURN_REFUSALS.get(user_text)

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        if name != WEATHER_FORECAST:
            return _error(f"Tool '{name}' bukan tool prakiraan cuaca.")
        if self._refusal is not None:
            return _error(self._refusal)
        try:
            return _to_json(self._forecast(arguments)), False
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except WeatherError as exc:
            logger.warning("No weather forecast: %s", exc)
            return _error(str(exc))
        except ForecastError as exc:
            logger.info("No weather forecast for the question: %s", exc)
            return _error(str(exc))
        except Exception:
            logger.exception("Weather forecast tool '%s' failed", name)
            return _error(_FAILED)

    def _forecast(self, arguments: object) -> dict[str, object]:
        day, part = _day_and_part(_arguments_mapping(arguments))
        outlook = self._weather.outlook()
        return {
            # A place known only by its coordinates goes unnamed: they may be Master's home.
            "place": self._place,
            "forecast": [
                _period_json(outlook, period, with_parts=part is None)
                for period in periods(outlook, day, part)
            ],
        }


def _spec() -> ToolSpec:
    """Built once, so every request sends the same bytes."""
    return ToolSpec(
        name=WEATHER_FORECAST,
        description=(
            "The weather forecast at Master's place for today and the next six days, from "
            "Open-Meteo. Use it for later today, tonight, tomorrow, the weekend, a date, or a "
            'part of a day ("besok cuaca gimana?", "nanti sore hujan nggak?", "akhir pekan '
            'hujan nggak?", "Jumat cuacanya gimana?"); for the weather now or today as a whole '
            "use daily_briefing instead, and never both for one question. `day`: today (the "
            "default: the rest of today from now), tonight, tomorrow, weekend (the Saturday "
            "and Sunday from today on), or a date as YYYY-MM-DD from the date in Master's "
            "message, today to six days on. `part`: morning (pagi, 04:00-11:00), afternoon "
            "(siang, 11:00-15:00), evening (sore, 15:00-19:00), or night (malam, 19:00-24:00); "
            '"nanti sore" is day today with part evening, "nanti malam" is day tonight, and '
            '"nanti" alone is day today without a part. Call it once per question: each call '
            "waits for Open-Meteo, so for several parts of a day ask for the whole day, whose "
            "`parts` give each part's condition and rain chance. It returns `place` (null when "
            "the place has no name: then name no place) and `forecast`, one entry per day with "
            "`date`, `day`, `part` (a part, or sisa hari ini for the rest of today), "
            "`condition` (Indonesian), `min_c` and `max_c` (whole °C), `rain_chance` (the "
            "highest, %), `rain_mm`, and for a whole day `parts`; a value left out is unknown. "
            "Answer in one or two short sentences: the condition, when rain is likely and its "
            "chance, and the temperatures; never read the numbers out one by one. Never call it "
            "in a greeting or anything you start yourself. After an error result, say its "
            "message briefly and do not call again in this turn."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "day": {"type": "string"},
                "part": {"type": "string", "enum": list(PARTS)},
            },
            "additionalProperties": False,
        },
    )


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


def _period_json(outlook: Outlook, period: Period, *, with_parts: bool) -> dict[str, object]:
    """A period as the model reads it, unknown values left out; `with_parts` (a whole day, or
    the rest of today) adds the condition and rain chance of each of its parts not passed."""
    entry: dict[str, object] = {
        "date": period.day.isoformat(),
        "day": WEEKDAYS_ID[period.day.weekday()],
    }
    if period.part is not None:
        entry["part"] = period.part
    values = {
        "condition": period.condition,
        "min_c": None if period.min_c is None else round(period.min_c),
        "max_c": None if period.max_c is None else round(period.max_c),
        "rain_chance": period.rain_chance,
        "rain_mm": period.rain_mm,
    }
    entry.update({name: value for name, value in values.items() if value is not None})
    if with_parts:
        parts = [
            found for part in PARTS if (found := period_of(outlook, period.day, part)) is not None
        ]
        if parts:
            entry["parts"] = [_part_json(found) for found in parts]
    return entry


def _part_json(period: Period) -> dict[str, object]:
    values = {"part": period.part, "condition": period.condition, "rain_chance": period.rain_chance}
    return {name: value for name, value in values.items() if value is not None}


def _day_and_part(arguments: Mapping[str, object]) -> tuple[str | date, str | None]:
    """The day and part asked (null counts as left out, as local models send it): today by
    default; tonight is today's night."""
    part = arguments.get("part")
    if part is not None and part not in PARTS:
        raise _InvalidArguments(_BAD_PART)
    day = _day(arguments.get("day"))
    if day == "tonight":
        if part not in (None, "night"):
            raise _InvalidArguments(_TONIGHT_PART)
        return "today", "night"
    return day, part


def _day(value: object) -> str | date:
    if value is None:
        return "today"
    if value in _DAY_WORDS:
        return value
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise _InvalidArguments(_BAD_DAY)
    try:
        return date.fromisoformat(value)
    except ValueError:  # a day that does not exist, such as 2026-02-30
        raise _InvalidArguments(_BAD_DAY) from None


def _arguments_mapping(arguments: object) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in _KEYS]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
