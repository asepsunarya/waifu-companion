"""The play_time tool: what it answers from the play time store in tmp_path, at a fixed WIB
clock, and when it refuses."""

import json
import logging
from datetime import datetime, timedelta

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.play_time_tools import PlayTimeTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.briefing import BRIEFING_PROMPT
from waifu.config import PlayTimeConfig
from waifu.finance.values import WIB
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.play_time import PlayTimeStore, Session

DAY = datetime(2026, 10, 17, tzinfo=WIB)  # a Saturday
NOW = DAY + timedelta(hours=22)


def at(hour: float) -> datetime:
    return DAY + timedelta(seconds=round(hour * 3600))


def make_store(tmp_path, games=("hsr", "genshin", "zzz")) -> PlayTimeStore:
    return PlayTimeStore(
        tmp_path / "playtime.json", games=list(games), keep_days=90, now=lambda: NOW
    )


def make_tools(tmp_path, store=None, **config) -> PlayTimeTools:
    return PlayTimeTools(
        store if store is not None else make_store(tmp_path),
        PlayTimeConfig(**config),
        now=lambda: NOW,
    )


def ask(tools: PlayTimeTools, arguments=None) -> dict:
    result, is_error = tools.run("play_time", {} if arguments is None else arguments)
    assert is_error is False, result
    return json.loads(result)


def failed(tools: PlayTimeTools, arguments) -> str:
    result, is_error = tools.run("play_time", arguments)
    assert is_error is True
    data = json.loads(result)
    assert data["ok"] is False
    return data["message"]


def a_played_saturday(store: PlayTimeStore) -> None:
    """HSR 08:00-09:00 and 20:00-21:30, ZZZ 21:00-21:45, Genshin on Friday night until 00:30,
    and HSR open again since 21:50, last seen at 21:59."""
    store.save(
        [Session("hsr", at(21) + timedelta(minutes=50), at(21) + timedelta(minutes=59))],
        [
            Session("genshin", at(-2), at(0.5)),
            Session("hsr", at(8), at(9)),
            Session("hsr", at(20), at(21.5)),
            Session("zzz", at(21), at(21.75)),
        ],
    )


# --- the spec -----------------------------------------------------------------------------------


def test_one_tool_whose_arguments_may_all_be_left_out_so_it_needs_no_strict(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    assert spec.name == "play_time"
    assert spec.input_schema == {
        "type": "object",
        "properties": {
            "period": {"type": "string", "enum": ["today", "yesterday", "week", "month"]},
            "game": {"type": "string", "enum": ["hsr", "genshin", "zzz"]},
        },
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "play_time"


def test_the_games_offered_are_the_tracked_ones(tmp_path):
    tools = make_tools(tmp_path, games={"zzz": ["ZenlessZoneZero.exe"]})
    [spec] = tools.specs()

    assert spec.input_schema["properties"]["game"]["enum"] == ["zzz"]
    assert "Zenless Zone Zero = zzz" in spec.description
    assert "Star Rail" not in spec.description


def test_the_description_says_what_it_counts_when_to_use_it_and_how_to_say_it(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    for guidance in [
        "Honkai: Star Rail = hsr, Genshin Impact = genshin, Zenless Zone Zero = zzz",
        "only while `waifu run` is on",
        "minimized or idle",
        "counts once",
        "`playing_now`",
        "`tracked_since`",
        "only when Master asks",
        "never for a greeting, the morning briefing, the weekly report",
        "one or two short sentences",
        "no lists and no parentheses",
        "Never scold",
        "do not call again in this turn",
    ]:
        assert guidance in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching(tmp_path):
    store = make_store(tmp_path)
    tools = make_tools(tmp_path, store)
    before = tools.specs()
    a_played_saturday(store)

    tools.run("play_time", {"period": "week"})

    assert tools.specs() == before


# --- the answers --------------------------------------------------------------------------------


def test_today_by_default_gives_each_game_its_time_sessions_and_whether_it_is_open(tmp_path):
    store = make_store(tmp_path)
    a_played_saturday(store)

    assert ask(make_tools(tmp_path, store)) == {
        "period": "today",
        "from": "2026-10-17 00:00",
        "to": "2026-10-17 22:00",
        "games": [
            {
                "game": "hsr",
                "name": "Honkai: Star Rail",
                "hours": 2,
                "minutes": 39,
                "sessions": 3,
                "playing_now": True,
            },
            {
                "game": "genshin",
                "name": "Genshin Impact",
                "hours": 0,
                "minutes": 30,
                "sessions": 1,
                "playing_now": False,
            },
            {
                "game": "zzz",
                "name": "Zenless Zone Zero",
                "hours": 0,
                "minutes": 45,
                "sessions": 1,
                "playing_now": False,
            },
        ],
        # 00:00-00:30, 08:00-09:00, 20:00-21:45 (ZZZ inside HSR's), 21:50-21:59
        "total": {"hours": 3, "minutes": 24},
        "tracked_since": "2026-10-16 22:00",
    }


def test_one_game_and_another_period_can_be_asked_for(tmp_path):
    store = make_store(tmp_path)
    a_played_saturday(store)

    data = ask(make_tools(tmp_path, store), {"period": "yesterday", "game": "genshin"})

    assert (data["period"], data["from"], data["to"]) == (
        "yesterday",
        "2026-10-16 00:00",
        "2026-10-17 00:00",
    )
    assert [(g["game"], g["hours"], g["minutes"], g["sessions"]) for g in data["games"]] == [
        ("genshin", 2, 0, 1)
    ]
    assert data["total"] == {"hours": 2, "minutes": 0}


@pytest.mark.parametrize(
    ("period", "since"),
    [("week", "2026-10-12 00:00"), ("month", "2026-10-01 00:00")],
)
def test_the_week_starts_on_monday_and_the_month_on_the_first(tmp_path, period, since):
    store = make_store(tmp_path)
    a_played_saturday(store)

    data = ask(make_tools(tmp_path, store), {"period": period})

    assert (data["from"], data["to"]) == (since, "2026-10-17 22:00")
    assert data["total"] == {"hours": 5, "minutes": 24}  # Genshin's whole session too


def test_a_null_argument_counts_as_left_out(tmp_path):
    # Local models send null for the optional arguments they do not use.
    data = ask(make_tools(tmp_path), {"period": None, "game": None})

    assert data["period"] == "today"
    assert [game["game"] for game in data["games"]] == ["hsr", "genshin", "zzz"]


def test_a_game_open_but_not_seen_for_three_polls_is_not_playing_now(tmp_path):
    store = make_store(tmp_path)
    store.save([Session("zzz", at(21), NOW - timedelta(minutes=4))])

    data = ask(make_tools(tmp_path, store), {"game": "zzz"})

    assert data["games"][0]["playing_now"] is False
    assert data["games"][0]["minutes"] == 56


def test_playing_now_follows_the_configured_poll_interval(tmp_path):
    store = make_store(tmp_path)
    store.save([Session("zzz", at(21), NOW - timedelta(minutes=4))])

    data = ask(make_tools(tmp_path, store, poll_seconds=120), {"game": "zzz"})

    assert data["games"][0]["playing_now"] is True


def test_minutes_are_rounded_to_the_nearest(tmp_path):
    store = make_store(tmp_path)
    store.save([], [Session("hsr", at(20), at(20) + timedelta(seconds=89))])

    assert ask(make_tools(tmp_path, store))["games"][0]["minutes"] == 1


def test_before_anything_is_recorded_every_game_is_zero_and_nothing_is_tracked_yet(tmp_path):
    data = ask(make_tools(tmp_path))

    assert {(g["hours"], g["minutes"], g["sessions"]) for g in data["games"]} == {(0, 0, 0)}
    assert data["total"] == {"hours": 0, "minutes": 0}
    assert data["tracked_since"] is None
    assert not (tmp_path / "playtime.json").exists()


# --- refusals and errors -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ([], "objek JSON"),
        ({"period": "year"}, "'period' harus salah satu dari: today, yesterday, week, month"),
        ({"game": "wuwa"}, "'game' harus salah satu dari: hsr, genshin, zzz"),
        ({"game": 1}, "'game'"),
        ({"days": 7}, "argumen tidak dikenal: days"),
    ],
)
def test_bad_arguments_are_an_error_result(tmp_path, arguments, reason):
    message = failed(make_tools(tmp_path), arguments)

    assert message.startswith("Argumen play_time tidak valid:")
    assert reason in message


def test_an_unreadable_file_is_an_error_result_and_logged(tmp_path, caplog):
    class UnreadableStore:
        def record(self):
            raise OSError("the disk is gone")

    with caplog.at_level(logging.ERROR, logger="waifu.brain.play_time_tools"):
        message = failed(make_tools(tmp_path, UnreadableStore()), {})

    assert message == "Catatan jam main tidak bisa dibaca saat ini."
    assert "the disk is gone" in caplog.text


def test_another_tool_name_is_an_error_result(tmp_path):
    result, is_error = make_tools(tmp_path).run("api_usage", {})

    assert is_error is True
    assert "bukan tool jam main" in json.loads(result)["message"]


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        (BRIEFING_PROMPT, "Jam main hanya untuk pertanyaan Master, bukan untuk sapaan pagi."),
        (
            WEEKLY_REPORT_PROMPT,
            "Jam main minggu ini sudah ada di weekly_report; jangan panggil tool lain.",
        ),
        (
            JOURNAL_PROMPT,
            "Jam main hanya untuk pertanyaan Master, bukan untuk pertanyaan jurnal malam.",
        ),
    ],
    ids=["briefing", "weekly_report", "journal_question"],
)
def test_her_own_turns_get_no_play_time_and_master_s_next_turn_does(tmp_path, prompt, message):
    tools = make_tools(tmp_path)

    tools.begin_turn(prompt)
    assert failed(tools, {}) == message
    tools.begin_turn("hari ini aku main berapa lama?")

    assert ask(tools)["period"] == "today"
