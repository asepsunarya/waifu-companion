"""The prayer_times tool: the day's times at a fake place, at a WIB clock moved by the test, and
when it refuses. The times themselves are tested in test_prayer_times.py."""

import json
import logging
from datetime import date, datetime

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.prayer_tools import PrayerTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.briefing import BRIEFING_PROMPT
from waifu.finance.values import WIB
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.prayer import NO_PLACE, PrayerPlace
from waifu.prayer_times import prayer_times
from waifu.weather import UNREACHABLE, Location, WeatherError

NOW = datetime(2026, 10, 24, 20, 0, tzinfo=WIB)  # Saturday 20:00
TODAY = NOW.date()
BANDUNG = Location("Bandung, Jawa Barat", -6.90389, 107.61861)
NAMES = ("imsak", "subuh", "terbit", "dzuhur", "ashar", "maghrib", "isya")


def tools(place: Location | None = BANDUNG, *, ihtiyath_minutes: int = 2) -> PrayerTools:
    found = PrayerPlace(None if place is None else (lambda: place))
    return PrayerTools(found, ihtiyath_minutes=ihtiyath_minutes, now=lambda: NOW)


def ask(provider: PrayerTools, arguments=None) -> dict:
    result, is_error = provider.run("prayer_times", {} if arguments is None else arguments)
    assert is_error is False, result
    return json.loads(result)


def failed(provider: PrayerTools, arguments=None) -> str:
    result, is_error = provider.run("prayer_times", {} if arguments is None else arguments)
    assert is_error is True
    data = json.loads(result)
    assert data["ok"] is False
    return data["message"]


def clocks(day: date, place: Location, ihtiyath: int = 2) -> dict[str, str | None]:
    times = prayer_times(day, place.latitude, place.longitude, ihtiyath_minutes=ihtiyath)
    return {
        name: None if getattr(times, name) is None else f"{getattr(times, name):%H:%M}"
        for name in NAMES
    }


# --- the spec -----------------------------------------------------------------------------------


def test_one_tool_whose_date_may_be_left_out_so_it_needs_no_strict():
    [spec] = tools().specs()

    assert spec.name == "prayer_times"
    assert spec.input_schema == {
        "type": "object",
        "properties": {"date": {"type": "string"}},
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "prayer_times"


def test_the_description_says_where_the_times_come_from_and_how_to_speak_of_them():
    [spec] = tools().specs()

    for guidance in [
        "Kemenag RI method",
        "in WIB",
        "Without `date` it gives today's",
        "for example tomorrow's from the date in Master's message",
        "a null time means the sun does not reach that angle there on that day",
        "never times from memory",
        "neutrally and respectfully",
        "never urge Master to pray",
        "never comment on whether or how he prays",
        "Never call it in a greeting or anything you start yourself",
        "do not call again in this turn",
    ]:
        assert guidance in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching():
    provider = tools()
    before = provider.specs()

    ask(provider)

    assert provider.specs() == before


# --- the times ----------------------------------------------------------------------------------


def test_today_by_default_gives_the_place_and_its_seven_times():
    answer = ask(tools())

    assert answer == {
        "place": "Bandung, Jawa Barat",
        "date": "2026-10-24",
        "day": "Sabtu",
        "timezone": "WIB",
        **clocks(TODAY, BANDUNG),
        "method": "Kemenag RI",
        "ihtiyath_minutes": 2,
    }
    assert answer["subuh"] < answer["dzuhur"] < answer["maghrib"]


def test_a_date_gives_that_day_s_times():
    answer = ask(tools(), {"date": "2026-10-25"})

    assert (answer["date"], answer["day"]) == ("2026-10-25", "Minggu")
    assert {name: answer[name] for name in NAMES} == clocks(date(2026, 10, 25), BANDUNG)


def test_the_ihtiyath_set_by_master_is_used_and_told():
    answer = ask(tools(ihtiyath_minutes=0))

    assert answer["ihtiyath_minutes"] == 0
    assert {name: answer[name] for name in NAMES} == clocks(TODAY, BANDUNG, ihtiyath=0)


def test_a_time_the_sun_never_reaches_is_null():
    far_north = Location("60.0, 105.0", 60.0, 105.0)

    answer = ask(tools(far_north), {"date": "2027-06-21"})

    assert (answer["imsak"], answer["subuh"], answer["isya"]) == (None, None, None)
    assert answer["dzuhur"] is not None and answer["maghrib"] is not None


def test_a_null_date_counts_as_left_out():
    assert ask(tools(), {"date": None})["date"] == "2026-10-24"


def test_a_day_up_to_a_year_away_either_way_is_answered():
    assert ask(tools(), {"date": "2027-10-25"})["date"] == "2027-10-25"  # 366 days on
    assert ask(tools(), {"date": "2025-10-23"})["date"] == "2025-10-23"  # 366 days back


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ([], "objek JSON"),
        ({"date": "besok"}, "'date' harus tanggal YYYY-MM-DD"),
        ({"date": "25-10-2026"}, "'date' harus tanggal YYYY-MM-DD"),
        ({"date": "20261025"}, "'date' harus tanggal YYYY-MM-DD"),
        ({"date": "2026-02-30"}, "'date' harus tanggal YYYY-MM-DD"),
        ({"date": 20261025}, "'date' harus tanggal YYYY-MM-DD"),
        ({"date": "2027-10-26"}, "'date' harus dalam satu tahun dari hari ini"),
        ({"date": "2025-10-22"}, "'date' harus dalam satu tahun dari hari ini"),
        ({"city": "Bandung"}, "argumen tidak dikenal: city"),
    ],
)
def test_bad_arguments_are_an_error_result(arguments, reason):
    message = failed(tools(), arguments)

    assert message.startswith("Argumen prayer_times tidak valid:")
    assert reason in message


def test_without_a_place_it_says_how_to_set_one():
    assert failed(tools(None)) == NO_PLACE


def test_a_place_that_cannot_be_found_is_an_error_result_with_its_message(caplog):
    def unreachable() -> Location:
        raise WeatherError(UNREACHABLE)

    provider = PrayerTools(PrayerPlace(unreachable), ihtiyath_minutes=2, now=lambda: NOW)
    with caplog.at_level(logging.WARNING, logger="waifu.brain.prayer_tools"):
        message = failed(provider)

    assert message == UNREACHABLE
    assert "No prayer times" in caplog.text


def test_an_unexpected_failure_is_an_error_result_and_logged(caplog):
    def broken() -> Location:
        raise RuntimeError("boom")

    provider = PrayerTools(PrayerPlace(broken), ihtiyath_minutes=2, now=lambda: NOW)
    with caplog.at_level(logging.ERROR, logger="waifu.brain.prayer_tools"):
        message = failed(provider)

    assert message == "Waktu sholat tidak bisa dihitung saat ini."
    assert "Prayer tool" in caplog.text


def test_another_tool_name_is_an_error_result():
    result, is_error = tools().run("daily_briefing", {})

    assert is_error is True
    assert "bukan tool waktu sholat" in json.loads(result)["message"]


# --- her own turns ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        (BRIEFING_PROMPT, "Waktu sholat hanya untuk permintaan Master, bukan untuk sapaan pagi."),
        (
            WEEKLY_REPORT_PROMPT,
            "Waktu sholat hanya untuk permintaan Master, bukan untuk laporan game mingguan.",
        ),
        (
            JOURNAL_PROMPT,
            "Waktu sholat hanya untuk permintaan Master, bukan untuk pertanyaan jurnal malam.",
        ),
    ],
    ids=["briefing", "weekly_report", "journal_question"],
)
def test_her_own_turns_get_no_prayer_times_and_master_s_next_turn_does(prompt, message):
    provider = tools()
    provider.begin_turn(prompt)

    assert failed(provider) == message
    provider.begin_turn("jadwal sholat hari ini?")

    assert ask(provider)["place"] == "Bandung, Jawa Barat"
