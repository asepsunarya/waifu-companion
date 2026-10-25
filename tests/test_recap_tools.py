"""The daily_recap tool: what it answers from a recap of stores in tmp_path at a fixed WIB clock,
its arguments, its failures, and when it refuses."""

import json
import logging
from datetime import date, datetime, timedelta

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.recap_tools import DAILY_RECAP, DailyRecapTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.briefing import BRIEFING_PROMPT
from waifu.finance.values import WIB
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT, JournalStore
from waifu.recap import DailyRecap

MIDNIGHT = datetime(2026, 10, 24, tzinfo=WIB)  # a Saturday
NOW = MIDNIGHT + timedelta(hours=21, minutes=30)
SECRET_NOTE = "hari ini aku nangis di kantor"


def make_journal(tmp_path) -> JournalStore:
    """Two notes today, one yesterday, one on 20 October."""
    journal = JournalStore(
        tmp_path / "journal.json", max_chars=500, max_notes_per_day=10, now=lambda: NOW
    )
    journal.add(SECRET_NOTE)
    journal.add("laporannya selesai")
    journal.add("capek banget", date(2026, 10, 23))
    journal.add("main ke rumah Nenek", date(2026, 10, 20))
    return journal


def tool(tmp_path, now=lambda: NOW, **sources) -> DailyRecapTools:
    if not sources:
        sources = {"journal": make_journal(tmp_path), "focus": lambda since, until: 25 * 60}
    return DailyRecapTools(DailyRecap(now=now, **sources))


def ask(provider: DailyRecapTools, arguments: object = None) -> dict:
    result, is_error = provider.run(DAILY_RECAP, {} if arguments is None else arguments)
    assert is_error is False, result
    return json.loads(result)


def failed(provider: DailyRecapTools, arguments: object = None) -> str:
    result, is_error = provider.run(DAILY_RECAP, {} if arguments is None else arguments)
    assert is_error is True, result
    answer = json.loads(result)
    assert answer["ok"] is False
    return answer["message"]


# --- the spec ----------------------------------------------------------------------------------


def test_one_tool_whose_argument_may_be_left_out_so_it_needs_no_strict(tmp_path):
    [spec] = tool(tmp_path).specs()

    assert spec.name == "daily_recap"
    assert spec.input_schema == {
        "type": "object",
        "properties": {"date": {"type": "string"}},
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "daily_recap"


def test_the_description_looks_back_tells_highlights_first_and_never_judges(tmp_path):
    [spec] = tool(tmp_path).specs()

    for guidance in [
        '"hari ini aku ngapain aja?"',
        '"rekap kemarin dong"',
        "daily_briefing tells what is ahead",
        "today (the default: from midnight WIB up to now), yesterday, or YYYY-MM-DD",
        "back to 30 days ago",
        "without the internet",
        "as stored at its last refresh",
        "never their text: journal_read reads them only when Master asks",
        "not a record that they were said",
        "No part at all means nothing was recorded that day: say so warmly",
        "`problems`",
        "It holds no money",
        "use that thing's own tool instead",
        "highlights first",
        "never read the data out one by one",
        "never judge whether it is too much",
        "never for a greeting, the morning briefing, the weekly report, or anything you start "
        "yourself",
        "do not call again in this turn",
    ]:
        assert guidance in spec.description


def test_the_description_says_forgotten_done_tasks_are_unknown_never_none(tmp_path):
    [spec] = tool(tmp_path).specs()

    assert (
        "`done_known` false in place of them when the to-do list no longer keeps that day's "
        "finished tasks: then never say none were finished"
    ) in spec.description


def test_the_description_tells_the_day_s_internet_outages_while_waifu_run_watched(tmp_path):
    [spec] = tool(tmp_path).specs()

    assert (
        "`network` (how many times and how long the internet was down that day while `waifu "
        "run` watched it)"
    ) in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching(tmp_path):
    provider = tool(tmp_path)
    before = provider.specs()

    ask(provider)

    assert provider.specs() == before


# --- the day asked -----------------------------------------------------------------------------


@pytest.mark.parametrize("arguments", [{}, {"date": "today"}, {"date": None}])
def test_today_is_the_default_up_to_now(tmp_path, arguments):
    assert ask(tool(tmp_path), arguments) == {
        "date": "2026-10-24",
        "day": "Sabtu",
        "as_of": "21:30",
        "focus": {"hours": 0, "minutes": 25},
        "journal": {"notes": 2},
    }


def test_yesterday_and_a_date_are_whole_days(tmp_path):
    provider = tool(tmp_path)

    assert ask(provider, {"date": "yesterday"})["journal"] == {"notes": 1}
    assert ask(provider, {"date": "2026-10-20"}) == {
        "date": "2026-10-20",
        "day": "Selasa",
        "focus": {"hours": 0, "minutes": 25},
        "journal": {"notes": 1},
    }


def test_the_journal_s_text_never_reaches_the_result(tmp_path):
    result, _ = tool(tmp_path).run(DAILY_RECAP, {})

    assert SECRET_NOTE not in result
    assert "laporannya" not in result


def test_a_day_with_nothing_recorded_has_only_its_date(tmp_path):
    provider = tool(tmp_path, journal=make_journal(tmp_path))

    assert ask(provider, {"date": "2026-10-21"}) == {"date": "2026-10-21", "day": "Rabu"}


@pytest.mark.parametrize(
    ("day", "message"),
    [
        (
            "2026-10-25",
            "Hari itu belum terjadi; rekap hanya untuk hari ini atau hari yang sudah lewat.",
        ),
        ("2026-09-23", "Rekap hanya sampai 30 hari ke belakang, paling jauh Kamis 24-09-2026."),
    ],
)
def test_a_day_out_of_range_says_how_far_the_recap_goes(tmp_path, day, message, caplog):
    with caplog.at_level(logging.INFO, logger="waifu.brain.recap_tools"):
        assert failed(tool(tmp_path), {"date": day}) == message

    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize(
    "value", ["kemarin", "24-10-2026", "2026-02-30", "20261024", "2026-W43-6", 20261024, True]
)
def test_another_date_is_refused_as_an_invalid_argument(tmp_path, value):
    assert failed(tool(tmp_path), {"date": value}) == (
        "Argumen daily_recap tidak valid: 'date' harus today, yesterday, atau tanggal "
        "YYYY-MM-DD, misalnya 2026-10-23."
    )


def test_an_unknown_key_or_arguments_that_are_no_object_are_refused(tmp_path):
    provider = tool(tmp_path)

    assert failed(provider, {"day": "today"}) == (
        "Argumen daily_recap tidak valid: argumen tidak dikenal: day."
    )
    assert failed(provider, ["today"]) == (
        "Argumen daily_recap tidak valid: argumen harus berupa objek JSON."
    )


# --- failures ----------------------------------------------------------------------------------


def test_a_part_that_fails_is_named_and_the_rest_is_told(tmp_path):
    def broken(since: datetime, until: datetime) -> int:
        raise OSError("locked")

    answer = ask(tool(tmp_path, journal=make_journal(tmp_path), focus=broken))

    assert answer["journal"] == {"notes": 2}
    assert answer["problems"] == ["Catatan fokus tidak bisa dibaca saat ini."]


def test_every_part_failing_is_an_error_result(tmp_path):
    def broken(since: datetime, until: datetime) -> int:
        raise OSError("locked")

    assert failed(tool(tmp_path, focus=broken)) == (
        "Rekap hari itu tidak bisa dibuat: catatannya tidak terbaca saat ini."
    )


def test_anything_unexpected_is_logged_and_an_error_result(tmp_path, caplog):
    def broken_clock() -> datetime:
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="waifu.brain.recap_tools"):
        assert failed(tool(tmp_path, now=broken_clock, focus=lambda since, until: 0)) == (
            "Rekap hari itu tidak bisa dibuat saat ini."
        )

    assert "Daily recap tool" in caplog.text


def test_another_tool_name_is_an_error_result(tmp_path):
    result, is_error = tool(tmp_path).run("daily_briefing", {})

    assert is_error is True
    assert json.loads(result)["message"] == "Tool 'daily_briefing' bukan tool rekap hari."


# --- her own turns -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        (BRIEFING_PROMPT, "Rekap hari hanya untuk pertanyaan Master, bukan untuk sapaan pagi."),
        (
            WEEKLY_REPORT_PROMPT,
            "Rekap hari hanya untuk pertanyaan Master, bukan untuk laporan game mingguan.",
        ),
        (
            JOURNAL_PROMPT,
            "Rekap hari hanya untuk pertanyaan Master, bukan untuk pertanyaan jurnal malam.",
        ),
    ],
    ids=["briefing", "weekly_report", "journal_question"],
)
def test_her_own_turns_get_no_recap_and_master_s_next_turn_does(tmp_path, prompt, message):
    provider = tool(tmp_path)
    provider.begin_turn(prompt)

    assert failed(provider) == message
    provider.begin_turn("hari ini aku ngapain aja?")

    assert ask(provider)["journal"] == {"notes": 2}
