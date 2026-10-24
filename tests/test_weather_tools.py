"""The weather_forecast tool: which days and parts of a day a question asks for, read from a
seven-day outlook built by the test, and the tool itself over a real WeatherClient whose
Open-Meteo is a fake. The outlook request itself is tested in test_weather.py."""

import json
import logging
import urllib.parse
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.tools import to_anthropic, to_ollama
from waifu.brain.weather_tools import (
    REST_OF_TODAY,
    ForecastError,
    Period,
    WeatherForecastTools,
    period_of,
    periods,
)
from waifu.briefing import BRIEFING_PROMPT
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.weather import UNREACHABLE, DayOutlook, Hour, Outlook, WeatherClient

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


SATURDAY_20_15 = at(SATURDAY, 20) + timedelta(minutes=15)
SATURDAY_NIGHT = make_outlook(SATURDAY_20_15)


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


# --- the tool -----------------------------------------------------------------------------------

GEOCODING_HOST = "geocoding-api.open-meteo.com"
FORECAST_HOST = "api.open-meteo.com"
CIANJUR = {"name": "Cianjur", "latitude": -6.82, "longitude": 107.14, "admin1": "Jawa Barat"}


def open_meteo_reply(now: datetime) -> dict:
    """`make_outlook(now)` as Open-Meteo sends it for the seven-day request."""
    outlook = make_outlook(now)
    return {
        "timezone": "Asia/Jakarta",
        "current": {"time": f"{now:%Y-%m-%dT%H:%M}", "interval": 900, "weather_code": 2},
        "daily": {
            "time": [day.day.isoformat() for day in outlook.days],
            "weather_code": [day.code for day in outlook.days],
            "temperature_2m_max": [day.max_c for day in outlook.days],
            "temperature_2m_min": [day.min_c for day in outlook.days],
            "precipitation_probability_max": [day.rain_chance for day in outlook.days],
            "precipitation_sum": [day.rain_mm for day in outlook.days],
        },
        "hourly": {
            "time": [f"{hour.at:%Y-%m-%dT%H:%M}" for hour in outlook.hours],
            "weather_code": [hour.code for hour in outlook.hours],
            "temperature_2m": [hour.temperature_c for hour in outlook.hours],
            "precipitation_probability": [hour.rain_chance for hour in outlook.hours],
            "precipitation": [hour.precipitation_mm for hour in outlook.hours],
        },
    }


class FakeOpenMeteo:
    """The weather client's fetch: Cianjur for a city lookup and `reply` (or its exception) for a
    forecast; records every URL and never goes online."""

    def __init__(self, reply: object) -> None:
        self.reply = reply
        self.urls: list[str] = []

    def __call__(self, url: str, timeout_s: float) -> object:
        self.urls.append(url)
        if urllib.parse.urlsplit(url).hostname == GEOCODING_HOST:
            return {"results": [CIANJUR]}
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply

    def requests_to(self, host: str) -> list[str]:
        return [url for url in self.urls if urllib.parse.urlsplit(url).hostname == host]


def tool(
    now: datetime = SATURDAY_20_15,
    *,
    reply: object = None,
    place: str | None = "Cianjur",
) -> tuple[WeatherForecastTools, FakeOpenMeteo]:
    fake = FakeOpenMeteo(open_meteo_reply(now) if reply is None else reply)
    weather = WeatherClient(city="Cianjur", fetch=fake)
    return WeatherForecastTools(weather, place=place), fake


def ask(provider: WeatherForecastTools, arguments=None) -> dict:
    result, is_error = provider.run("weather_forecast", {} if arguments is None else arguments)
    assert is_error is False, result
    return json.loads(result)


def failed(provider: WeatherForecastTools, arguments=None) -> str:
    result, is_error = provider.run("weather_forecast", {} if arguments is None else arguments)
    assert is_error is True
    data = json.loads(result)
    assert data["ok"] is False
    return data["message"]


EVERY_PART_BERAWAN = [
    {"part": word, "condition": "berawan", "rain_chance": 10}
    for word in ("pagi", "siang", "sore", "malam")
]


def test_one_tool_whose_arguments_may_be_left_out_so_it_needs_no_strict():
    [spec] = tool()[0].specs()

    assert spec.name == "weather_forecast"
    assert spec.input_schema == {
        "type": "object",
        "properties": {
            "day": {"type": "string"},
            "part": {"type": "string", "enum": ["morning", "afternoon", "evening", "night"]},
        },
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "weather_forecast"


def test_the_description_routes_the_weather_now_to_the_briefing_and_asks_for_one_call():
    [spec] = tool()[0].specs()

    for guidance in [
        "for the weather now or today as a whole use daily_briefing when you have it",
        "never both for one question",
        "Call it once per question",
        "for several parts of a day ask for the whole day",
        "morning (pagi, 04:00-11:00)",
        "afternoon (siang, 11:00-15:00)",
        "evening (sore, 15:00-19:00)",
        "night (malam, 19:00-24:00)",
        '"nanti sore" is day today with part evening',
        '"nanti malam" is day tonight',
        "from the date in Master's message, today to six days on",
        "`place` (null when the place has no name: then name no place)",
        "a value left out is unknown",
        "never read the numbers out one by one",
        "Never call it in a greeting or anything you start yourself",
        "do not call again in this turn",
    ]:
        assert guidance in spec.description


def test_the_description_sends_the_weather_of_today_here_when_there_is_no_briefing():
    # She has this tool with the briefing off too, and then no daily_briefing to send it to.
    [spec] = tool()[0].specs()

    assert "use daily_briefing when you have it" in spec.description
    assert "without daily_briefing, use this tool with day today" in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching():
    provider, _ = tool()
    before = provider.specs()

    ask(provider, {"day": "tomorrow"})

    assert provider.specs() == before


def test_tomorrow_is_the_whole_day_with_each_part_s_condition_and_rain_chance():
    provider, fake = tool()

    assert ask(provider, {"day": "tomorrow"}) == {
        "place": "Cianjur",
        "forecast": [
            {
                "date": "2026-10-25",
                "day": "Minggu",
                "condition": "mendung",
                "min_c": 19,
                "max_c": 29,
                "rain_chance": 10,
                "rain_mm": 1.0,
                "parts": EVERY_PART_BERAWAN,
            }
        ],
    }
    assert len(fake.requests_to(FORECAST_HOST)) == 1


def test_without_a_day_it_is_the_rest_of_today_with_the_parts_still_ahead():
    provider, _ = tool()

    assert ask(provider)["forecast"] == [
        {
            "date": "2026-10-24",
            "day": "Sabtu",
            "part": "sisa hari ini",
            "condition": "berawan",
            "min_c": 20,
            "max_c": 32,  # 31.5 °C at 23:00, in whole degrees
            "rain_chance": 10,
            "rain_mm": 0.0,
            "parts": [{"part": "malam", "condition": "berawan", "rain_chance": 10}],
        }
    ]


def test_a_null_day_and_part_count_as_left_out():
    provider, _ = tool()

    assert ask(provider, {"day": None, "part": None}) == ask(provider)


def test_tonight_is_today_s_night_without_its_parts():
    provider, _ = tool()

    assert ask(provider, {"day": "tonight"})["forecast"] == [
        {
            "date": "2026-10-24",
            "day": "Sabtu",
            "part": "malam",
            "condition": "berawan",
            "min_c": 20,
            "max_c": 32,
            "rain_chance": 10,
            "rain_mm": 0.0,
        }
    ]
    assert ask(provider, {"day": "tonight", "part": "night"}) == ask(provider, {"day": "tonight"})


def test_nanti_sore_is_today_s_evening_from_the_hour_running_now():
    reply = open_meteo_reply(at(SATURDAY, 14) + timedelta(minutes=5))
    reply["hourly"]["weather_code"][17] = 63  # 16:00-17:00 today
    reply["hourly"]["precipitation_probability"][17] = 75
    reply["hourly"]["precipitation"][17] = 2.4
    provider, _ = tool(reply=reply)

    assert ask(provider, {"day": "today", "part": "evening"})["forecast"] == [
        {
            "date": "2026-10-24",
            "day": "Sabtu",
            "part": "sore",
            "condition": "hujan",
            "min_c": 28,
            "max_c": 30,  # 29.5 °C
            "rain_chance": 75,
            "rain_mm": 2.4,
        }
    ]


def test_the_weekend_from_a_weekday_is_the_coming_saturday_and_sunday():
    monday = at(date(2026, 10, 26), 9)
    provider, _ = tool(monday)

    forecast = ask(provider, {"day": "weekend"})["forecast"]

    assert [(day["date"], day["day"]) for day in forecast] == [
        ("2026-10-31", "Sabtu"),
        ("2026-11-01", "Minggu"),
    ]
    assert (forecast[0]["condition"], forecast[1]["condition"]) == ("cerah berawan", "cerah")


def test_a_date_gives_that_day_and_a_part_of_it():
    provider, _ = tool()

    [friday] = ask(provider, {"day": "2026-10-30", "part": "morning"})["forecast"]

    assert (friday["date"], friday["day"], friday["part"]) == ("2026-10-30", "Jumat", "pagi")
    assert "parts" not in friday


def test_unknown_values_are_left_out():
    reply = open_meteo_reply(at(SATURDAY, 20))
    for name in ("precipitation_probability_max", "precipitation_sum", "weather_code"):
        reply["daily"][name][6] = None
    provider, _ = tool(reply=reply)

    [friday] = ask(provider, {"day": "2026-10-30"})["forecast"]

    assert friday == {
        "date": "2026-10-30",
        "day": "Jumat",
        "min_c": 24,
        "max_c": 34,
        "parts": EVERY_PART_BERAWAN,
    }


def test_a_place_known_only_by_its_coordinates_is_never_named():
    fake = FakeOpenMeteo(open_meteo_reply(at(SATURDAY, 20)))
    weather = WeatherClient(latitude=-6.9175, longitude=107.6191, fetch=fake)
    provider = WeatherForecastTools(weather, place=None)

    result, is_error = provider.run("weather_forecast", {"day": "tomorrow"})

    assert is_error is False
    assert json.loads(result)["place"] is None
    assert "6.9175" not in result and "107.6191" not in result


def test_the_place_is_the_city_as_written_never_the_geocoded_name():
    provider, fake = tool()

    answer = ask(provider, {"day": "tomorrow"})
    ask(provider, {"day": "weekend"})

    assert answer["place"] == "Cianjur"  # not "Cianjur, Jawa Barat"
    assert len(fake.requests_to(GEOCODING_HOST)) == 1  # the city is looked up once
    assert len(fake.requests_to(FORECAST_HOST)) == 2


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ([], "objek JSON"),
        ({"day": "lusa"}, "'day' harus today, tonight, tomorrow, weekend, atau tanggal YYYY-MM-DD"),
        ({"day": "25-10-2026"}, "'day' harus"),
        ({"day": "20261025"}, "'day' harus"),
        ({"day": "2026-02-30"}, "'day' harus"),
        ({"day": 20261025}, "'day' harus"),
        ({"day": ["today"]}, "'day' harus"),
        ({"part": "pagi"}, "'part' harus morning, afternoon, evening, atau night"),
        ({"part": ["night"]}, "'part' harus"),
        ({"day": "tonight", "part": "morning"}, "'tonight' sudah berarti malam ini"),
        ({"city": "Bandung"}, "argumen tidak dikenal: city"),
    ],
)
def test_bad_arguments_are_an_error_result_without_asking_open_meteo(arguments, reason):
    provider, fake = tool()

    message = failed(provider, arguments)

    assert message.startswith("Argumen weather_forecast tidak valid:")
    assert reason in message
    assert fake.urls == []


def test_a_question_out_of_reach_is_an_error_result_with_its_message(caplog):
    provider, _ = tool()

    with caplog.at_level(logging.INFO, logger="waifu.brain.weather_tools"):
        passed = failed(provider, {"day": "today", "part": "evening"})
    too_far = failed(provider, {"day": "2026-10-31"})

    assert passed == "Sore ini sudah lewat."
    assert too_far == "Prakiraan cuaca hanya ada untuk hari ini sampai Jumat 30-10-2026."
    assert [record.levelno for record in caplog.records] == [logging.INFO]


def test_open_meteo_out_of_reach_is_an_error_result_with_its_message(caplog):
    provider, _ = tool(reply=TimeoutError("timed out"))

    with caplog.at_level(logging.WARNING, logger="waifu.brain.weather_tools"):
        message = failed(provider)

    assert message == UNREACHABLE
    assert "No weather forecast" in caplog.text


def test_an_unexpected_failure_is_an_error_result_and_logged(caplog):
    provider, _ = tool(reply=RuntimeError("boom"))

    with caplog.at_level(logging.ERROR, logger="waifu.brain.weather_tools"):
        message = failed(provider)

    assert message == "Prakiraan cuaca tidak bisa dibaca saat ini."
    assert "Weather forecast tool" in caplog.text


def test_another_tool_name_is_an_error_result():
    result, is_error = tool()[0].run("daily_briefing", {})

    assert is_error is True
    assert "bukan tool prakiraan cuaca" in json.loads(result)["message"]


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        (
            BRIEFING_PROMPT,
            "Prakiraan cuaca hanya untuk pertanyaan Master, bukan untuk sapaan pagi.",
        ),
        (
            WEEKLY_REPORT_PROMPT,
            "Prakiraan cuaca hanya untuk pertanyaan Master, bukan untuk laporan game mingguan.",
        ),
        (
            JOURNAL_PROMPT,
            "Prakiraan cuaca hanya untuk pertanyaan Master, bukan untuk pertanyaan jurnal malam.",
        ),
    ],
    ids=["briefing", "weekly_report", "journal_question"],
)
def test_her_own_turns_get_no_forecast_and_master_s_next_turn_does(prompt, message):
    provider, fake = tool()
    provider.begin_turn(prompt)

    assert failed(provider, {"day": "tomorrow"}) == message
    assert fake.urls == []
    provider.begin_turn("besok cuaca gimana?")

    assert ask(provider, {"day": "tomorrow"})["place"] == "Cianjur"
