"""The focus_timer tool: what it does with a real focus timer and log in tmp_path, at a WIB clock
moved by the test, and when it refuses. The timer's thread checks once and then waits until it
is stopped, so nothing happens by itself."""

import json
import logging
import threading
from datetime import datetime, timedelta

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.focus_tools import FocusTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.briefing import BRIEFING_PROMPT
from waifu.finance.values import WIB
from waifu.focus import FocusBlock, FocusStore
from waifu.focus_timer import FocusPlan, FocusTimer
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT

NOW = datetime(2026, 10, 17, 20, 0, tzinfo=WIB)  # Saturday 20:00
NOTHING = {"hours": 0, "minutes": 0}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def wait_for_stop(stop: threading.Event, timeout: float) -> bool:
    return stop.wait()


class Rig:
    """The tool over a started timer, its log, and one clock."""

    def __init__(self, tmp_path, *, start: bool = True) -> None:
        self.clock = Clock(NOW)
        self.store = FocusStore(tmp_path / "focus.json", keep_days=90, now=self.clock)
        self.timer = FocusTimer(self.store, owner_call="Sayang", now=self.clock, wait=wait_for_stop)
        self.tools = FocusTools(self.timer, now=self.clock)
        if start:
            self.timer.start(lambda text, emotion: None)

    def at(self, minutes: float) -> None:
        self.clock.now = NOW + timedelta(seconds=round(minutes * 60))

    def ask(self, arguments=None) -> dict:
        result, is_error = self.tools.run("focus_timer", {} if arguments is None else arguments)
        assert is_error is False, result
        return json.loads(result)

    def failed(self, arguments) -> str:
        result, is_error = self.tools.run("focus_timer", arguments)
        assert is_error is True
        data = json.loads(result)
        assert data["ok"] is False
        return data["message"]


@pytest.fixture
def rig(tmp_path):
    rig = Rig(tmp_path)
    yield rig
    rig.timer.stop()


# --- the spec -----------------------------------------------------------------------------------


def test_one_tool_whose_arguments_may_all_be_left_out_so_it_needs_no_strict(rig):
    [spec] = rig.tools.specs()

    assert spec.name == "focus_timer"
    assert spec.input_schema == {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["start", "stop", "status"]},
            "minutes": {"type": "integer"},
            "break_minutes": {"type": "integer"},
            "rounds": {"type": "integer"},
        },
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "focus_timer"


def test_the_description_says_when_to_use_it_and_how_to_say_it(rig):
    [spec] = rig.tools.specs()

    for guidance in [
        "only while `waifu run` is on",
        "a restart ends the session",
        "temani aku fokus 25 menit",
        "5 to 120, default 25",
        "1 to 60, default 5",
        "1 to 8, default 1",
        "only with `start`",
        "`stopped` false",
        "`minutes_left`",
        "`session_minutes_left` and `session_ends_at`",
        "the block under way included",
        "say nothing by yourself",
        "Master's own reminders still speak",
        "only when Master asks",
        "never for a greeting, the morning briefing, the weekly report",
        "one short sentence",
        "no lists and no parentheses",
        "do not call again in this turn",
    ]:
        assert guidance in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching(rig):
    before = rig.tools.specs()

    rig.ask({"action": "start"})

    assert rig.tools.specs() == before


# --- the answers --------------------------------------------------------------------------------


def test_status_by_default_without_a_session_tells_today_and_this_week(rig):
    rig.store.add(FocusBlock(NOW.replace(hour=8), NOW.replace(hour=9, minute=40)))
    rig.store.add(FocusBlock(NOW - timedelta(days=2), NOW - timedelta(days=2, minutes=-25)))

    assert rig.ask() == {
        "active": False,
        "today": {"hours": 1, "minutes": 40},
        "week": {"hours": 2, "minutes": 5},
    }


def test_start_with_the_defaults_begins_one_block_of_twenty_five_minutes(rig):
    assert rig.ask({"action": "start"}) == {
        "started": True,
        "minutes": 25,
        "break_minutes": 5,
        "rounds": 1,
        "block_ends_at": "20:25",
        "session_ends_at": "20:25",
    }
    assert rig.timer.in_block() is True


def test_start_with_the_lengths_master_asked_for(rig):
    started = rig.ask({"action": "start", "minutes": 50, "break_minutes": 10, "rounds": 3})

    assert started["block_ends_at"] == "20:50"
    assert started["session_ends_at"] == "22:50"  # three blocks of 50 and two breaks of 10
    assert rig.timer.state().plan == FocusPlan(minutes=50, break_minutes=10, rounds=3)


def test_status_in_a_block_tells_the_round_and_the_minutes_left_rounded_up(rig):
    rig.store.add(FocusBlock(NOW.replace(hour=8), NOW.replace(hour=9)))
    rig.ask({"action": "start", "rounds": 4})
    rig.at(12 + 20 / 60)

    assert rig.ask({"action": "status"}) == {
        "active": True,
        "phase": "focus",
        "round": 1,
        "rounds": 4,
        "minutes": 25,
        "break_minutes": 5,
        "minutes_left": 13,
        "ends_at": "20:25",
        "session_minutes_left": 103,  # four blocks and three breaks end at 21:55
        "session_ends_at": "21:55",
        "today": {"hours": 1, "minutes": 12},
        "week": {"hours": 1, "minutes": 12},
    }


def test_status_in_a_break_tells_the_break(rig):
    rig.ask({"action": "start", "rounds": 4})
    rig.at(25)
    rig.timer.tick_once()
    rig.at(27)

    status = rig.ask()

    assert (status["phase"], status["round"], status["minutes_left"]) == ("break", 1, 3)
    assert status["ends_at"] == "20:30"
    assert status["today"] == {"hours": 0, "minutes": 25}


def test_status_in_a_later_break_tells_when_the_whole_session_ends(rig):
    rig.ask({"action": "start", "rounds": 4})
    for minutes in (25, 30, 55):  # the first block, its break, and the second block end
        rig.at(minutes)
        rig.timer.tick_once()
    rig.at(57)

    status = rig.ask()

    assert (status["phase"], status["round"], status["minutes_left"]) == ("break", 2, 3)
    assert status["ends_at"] == "21:00"
    # Two blocks of 25 and the break between them after this break: never left to the model.
    assert (status["session_minutes_left"], status["session_ends_at"]) == (58, "21:55")


def test_status_in_the_last_block_ends_the_session_with_the_block(rig):
    rig.ask({"action": "start", "rounds": 2})
    for minutes in (25, 30):
        rig.at(minutes)
        rig.timer.tick_once()
    rig.at(40)

    status = rig.ask()

    assert (status["phase"], status["round"]) == ("focus", 2)
    assert status["session_ends_at"] == status["ends_at"] == "20:55"
    assert status["session_minutes_left"] == status["minutes_left"] == 15


def test_stop_ends_the_session_and_tells_its_focus(rig):
    rig.ask({"action": "start", "rounds": 4})
    rig.at(10)

    assert rig.ask({"action": "stop"}) == {"stopped": True, "focused": {"hours": 0, "minutes": 10}}
    assert rig.timer.state() is None
    assert rig.ask()["today"] == {"hours": 0, "minutes": 10}


def test_stop_without_a_session_says_none_was_running(rig):
    assert rig.ask({"action": "stop"}) == {"stopped": False}


def test_a_null_argument_counts_as_left_out(rig):
    assert rig.ask({"action": None, "minutes": None}) == {
        "active": False,
        "today": NOTHING,
        "week": NOTHING,
    }


def test_starting_while_a_session_runs_is_an_error_result(rig):
    rig.ask({"action": "start"})

    assert "sudah berjalan" in rig.failed({"action": "start", "minutes": 50})
    assert rig.timer.state().plan == FocusPlan()


def test_starting_while_the_timer_does_not_run_is_an_error_result(tmp_path):
    rig = Rig(tmp_path, start=False)

    assert "waifu run" in rig.failed({"action": "start"})


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ([], "objek JSON"),
        ({"action": "pause"}, "'action' harus salah satu dari: start, stop, status"),
        ({"action": "start", "minutes": 4}, "'minutes' harus bilangan bulat dari 5 sampai 120"),
        ({"action": "start", "minutes": "25"}, "'minutes'"),
        ({"action": "start", "minutes": 25.5}, "'minutes'"),
        ({"action": "start", "break_minutes": 0}, "'break_minutes' harus bilangan bulat dari 1"),
        ({"action": "start", "rounds": 9}, "'rounds' harus bilangan bulat dari 1 sampai 8"),
        ({"action": "start", "rounds": True}, "'rounds'"),
        ({"minutes": 25}, "hanya untuk action start"),
        ({"action": "stop", "rounds": 2}, "hanya untuk action start"),
        ({"seconds": 30}, "argumen tidak dikenal: seconds"),
    ],
)
def test_bad_arguments_are_an_error_result(rig, arguments, reason):
    message = rig.failed(arguments)

    assert message.startswith("Argumen focus_timer tidak valid:")
    assert reason in message
    assert rig.timer.state() is None


def test_an_unreadable_log_is_an_error_result_and_logged(rig, caplog):
    rig.store.path.mkdir()  # a folder where the file should be

    with caplog.at_level(logging.ERROR, logger="waifu.brain.focus_tools"):
        message = rig.failed({})

    assert message == "Catatan fokus tidak bisa dibaca saat ini."
    assert "Focus tool" in caplog.text


def test_another_tool_name_is_an_error_result(rig):
    result, is_error = rig.tools.run("play_time", {})

    assert is_error is True
    assert "bukan tool timer fokus" in json.loads(result)["message"]


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        (BRIEFING_PROMPT, "Timer fokus hanya untuk permintaan Master, bukan untuk sapaan pagi."),
        (
            WEEKLY_REPORT_PROMPT,
            "Timer fokus hanya untuk permintaan Master, bukan untuk laporan game mingguan.",
        ),
        (
            JOURNAL_PROMPT,
            "Timer fokus hanya untuk permintaan Master, bukan untuk pertanyaan jurnal malam.",
        ),
    ],
    ids=["briefing", "weekly_report", "journal_question"],
)
def test_her_own_turns_get_no_focus_timer_and_master_s_next_turn_does(rig, prompt, message):
    rig.tools.begin_turn(prompt)
    assert rig.failed({"action": "start"}) == message
    assert rig.timer.state() is None
    rig.tools.begin_turn("temani aku fokus 25 menit")

    assert rig.ask({"action": "start"})["started"] is True
