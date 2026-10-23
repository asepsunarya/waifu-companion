"""The guest_mode tool over a real GuestMode at a local clock moved by the test."""

import json
from datetime import datetime

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.guest_tools import GuestTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.guest import GuestMode

NOW = datetime(2026, 10, 24, 20, 0)  # Saturday 20:00, the PC's local time


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Rig:
    def __init__(self) -> None:
        self.clock = Clock(NOW)
        self.guest = GuestMode(max_hours=6, now=self.clock)
        self.tools = GuestTools(self.guest)

    def ask(self, arguments=None) -> dict:
        result, is_error = self.tools.run("guest_mode", {} if arguments is None else arguments)
        assert is_error is False, result
        return json.loads(result)

    def failed(self, arguments) -> str:
        result, is_error = self.tools.run("guest_mode", arguments)
        assert is_error is True
        data = json.loads(result)
        assert data["ok"] is False
        return data["message"]


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_one_tool_whose_only_argument_may_be_left_out_so_it_is_not_strict(rig):
    [spec] = rig.tools.specs()

    assert spec.name == "guest_mode"
    assert spec.input_schema == {
        "type": "object",
        "properties": {"on": {"type": "boolean"}},
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "guest_mode"


def test_the_description_says_when_to_use_it_and_what_it_hides(rig):
    [spec] = rig.tools.specs()

    for guidance in [
        "mode tamu",
        "mode tamu selesai",
        "`on` true",
        "`on` false",
        "leave `on` out",
        "money, journal, memory, to-do list",
        "starts a new conversation",
        "ends by itself",
        "05:00",
        "only at Master's own request",
        "one short sentence",
    ]:
        assert guidance in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching(rig):
    before = rig.tools.specs()

    rig.ask({"on": True})

    assert rig.tools.specs() == before


def test_on_turns_guest_mode_on_and_says_until_when(rig):
    assert rig.ask({"on": True}) == {
        "on": True,
        "until": "02:00",
        "message": "Mode tamu aktif sampai jam 02:00.",
    }
    assert rig.guest.is_on()


def test_on_again_renews_it(rig):
    rig.ask({"on": True})
    rig.clock.now = datetime(2026, 10, 24, 22, 0)

    assert rig.ask({"on": True}) == {
        "on": True,
        "until": "04:00",
        "message": "Mode tamu sudah aktif, sekarang sampai jam 04:00.",
    }


def test_off_ends_it(rig):
    rig.ask({"on": True})

    assert rig.ask({"on": False}) == {"on": False, "until": None, "message": "Mode tamu selesai."}
    assert not rig.guest.is_on()


def test_off_while_it_is_off_says_so(rig):
    assert rig.ask({"on": False}) == {
        "on": False,
        "until": None,
        "message": "Mode tamu memang tidak aktif.",
    }


@pytest.mark.parametrize("arguments", [{}, {"on": None}])
def test_without_on_it_only_tells_whether_it_is_on(rig, arguments):
    assert rig.ask(arguments) == {"on": False, "until": None, "message": "Mode tamu tidak aktif."}
    assert not rig.guest.is_on()

    rig.guest.turn_on()

    assert rig.ask(arguments) == {
        "on": True,
        "until": "02:00",
        "message": "Mode tamu aktif sampai jam 02:00.",
    }


def test_the_status_knows_it_ended_by_itself(rig):
    rig.ask({"on": True})
    rig.clock.now = datetime(2026, 10, 25, 2, 0)

    assert rig.ask()["on"] is False


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ("on", "argumen harus berupa objek JSON"),
        ({"on": "ya"}, "'on' harus true, false, atau dikosongkan"),
        ({"on": 1}, "'on' harus true, false, atau dikosongkan"),
        ({"on": True, "hours": 3}, "argumen tidak dikenal: hours"),
    ],
)
def test_bad_arguments_are_an_error_that_changes_nothing(rig, arguments, reason):
    assert reason in rig.failed(arguments)
    assert not rig.guest.is_on()


def test_another_tool_name_is_an_error(rig):
    result, is_error = rig.tools.run("control_device", {})

    assert is_error is True
    assert json.loads(result)["message"] == "Tool 'control_device' bukan tool mode tamu."
