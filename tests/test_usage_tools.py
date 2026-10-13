import json
import logging
from datetime import datetime, timedelta

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.tools import build_tool_specs, run_tool, to_anthropic, to_ollama
from waifu.brain.usage_tools import UsageTools
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.usage import TokenUsage, UsageLedger

NOW = datetime(2026, 10, 14, 21, 15)
OPUS = "claude-opus-5-5"
# With the built-in prices: (1200 * 4 + 350 * 20 + 5000 * 5 + 20000 * 0.20) / 1e6 = 0.0408 USD.
TURN = TokenUsage(input=1200, output=350, cache_write=5000, cache_read=20000)
SCHEMA = {
    "type": "object",
    "properties": {"period": {"type": "string", "enum": ["today", "month"]}},
    "required": ["period"],
    "additionalProperties": False,
}


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current


def make_ledger(tmp_path, clock: Clock) -> UsageLedger:
    return UsageLedger(tmp_path / "usage.json", prices={}, default_model=OPUS, now=clock)


def ask(tools: UsageTools, arguments: object) -> tuple[dict, bool]:
    result, is_error = tools.run("api_usage", arguments)
    return json.loads(result), is_error


def house() -> DeviceRegistry:
    return DeviceRegistry([MockDevice("lampu_kamar", "Lampu kamar", kind="light")])


# --- the spec ------------------------------------------------------------------------------


def test_one_strict_tool_api_usage_asks_for_today_or_this_month(tmp_path):
    clock = Clock()

    [spec] = UsageTools(make_ledger(tmp_path, clock), now=clock).specs()

    assert spec.name == "api_usage"
    assert spec.input_schema == SCHEMA


def test_the_description_says_these_are_her_own_estimates_from_this_pc(tmp_path):
    clock = Clock()

    [spec] = UsageTools(make_ledger(tmp_path, clock), now=clock).specs()

    for guidance in [
        "today",
        "month",
        "estimates",
        "your own requests on this PC",
        "not the Anthropic bill",
        "other apps",
        "US dollars",
        "rupiah",
        "cost_idr is null",
        "estimated",
        "fallback",
    ]:
        assert guidance in spec.description


def test_the_spec_stays_identical_as_the_costs_grow_for_prompt_caching(tmp_path):
    clock = Clock()
    ledger = make_ledger(tmp_path, clock)
    tools = UsageTools(ledger, idr_per_usd=16_000, now=clock)
    first = tools.specs()

    ledger.record(OPUS, TURN)
    tools.run("api_usage", {"period": "month"})

    assert tools.specs() == first


# --- today and this month ------------------------------------------------------------------


def test_today_counts_the_responses_of_the_current_local_day(tmp_path):
    clock = Clock(NOW - timedelta(days=1))
    ledger = make_ledger(tmp_path, clock)
    ledger.record(OPUS, TURN)  # yesterday
    clock.current = NOW
    ledger.record(OPUS, TURN)
    ledger.record(OPUS, TURN)

    data, is_error = ask(UsageTools(ledger, now=clock), {"period": "today"})

    assert is_error is False
    assert data == {
        "period": "today",
        "requests": 2,
        "input_tokens": 2400,
        "output_tokens": 700,
        "cache_read_tokens": 40000,
        "cache_write_tokens": 10000,
        "cost_usd": 0.0816,
        "cost_idr": None,
        "estimated": False,
    }


def test_month_counts_every_day_of_the_current_calendar_month(tmp_path):
    clock = Clock(datetime(2026, 9, 30, 23, 50))
    ledger = make_ledger(tmp_path, clock)
    ledger.record(OPUS, TURN)  # last month
    for day in (datetime(2026, 10, 1, 8, 0), NOW):
        clock.current = day
        ledger.record(OPUS, TURN)

    data, is_error = ask(UsageTools(ledger, now=clock), {"period": "month"})

    assert is_error is False
    assert data == {
        "period": "month",
        "requests": 2,
        "input_tokens": 2400,
        "output_tokens": 700,
        "cache_read_tokens": 40000,
        "cache_write_tokens": 10000,
        "cost_usd": 0.0816,
        "cost_idr": None,
        "estimated": False,
    }


def test_the_period_follows_the_clock_so_today_starts_empty_after_midnight(tmp_path):
    clock = Clock(datetime(2026, 10, 14, 23, 59))
    ledger = make_ledger(tmp_path, clock)
    tools = UsageTools(ledger, now=clock)
    ledger.record(OPUS, TURN)

    clock.current = datetime(2026, 10, 15, 0, 1)
    today, _ = ask(tools, {"period": "today"})
    month, _ = ask(tools, {"period": "month"})

    assert today["requests"] == 0
    assert month["requests"] == 1


def test_no_usage_yet_is_zero_and_creates_no_file(tmp_path):
    clock = Clock()
    ledger = make_ledger(tmp_path, clock)

    data, is_error = ask(UsageTools(ledger, idr_per_usd=16_000, now=clock), {"period": "month"})

    assert is_error is False
    assert data == {
        "period": "month",
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_usd": 0.0,
        "cost_idr": 0,
        "estimated": False,
    }
    assert not ledger.path.exists()


# --- the cost ------------------------------------------------------------------------------


def test_cost_idr_is_the_cost_at_masters_rate_rounded_to_whole_rupiah(tmp_path):
    clock = Clock()
    ledger = make_ledger(tmp_path, clock)
    ledger.record(OPUS, TURN)

    result, _ = UsageTools(ledger, idr_per_usd=16_312.5, now=clock).run(
        "api_usage", {"period": "today"}
    )

    # 0.0408 USD * 16312.5 = 665.55 rupiah, said as a whole number.
    assert '"cost_idr":666,' in result


def test_the_cost_in_dollars_carries_no_float_noise(tmp_path):
    clock = Clock()
    ledger = make_ledger(tmp_path, clock)
    ledger.record(OPUS, TokenUsage(input=25_000))  # 0.1 USD
    ledger.record(OPUS, TokenUsage(input=50_000))  # 0.2 USD; the sum is 0.30000000000000004

    data, _ = ask(UsageTools(ledger, now=clock), {"period": "today"})

    assert data["cost_usd"] == 0.3


def test_a_response_from_a_fallback_model_makes_the_cost_an_estimate(tmp_path):
    clock = Clock()
    ledger = make_ledger(tmp_path, clock)
    ledger.record(OPUS, TURN)
    ledger.record("claude-opus-5", TURN)  # priced with claude-opus-5-5's prices

    data, _ = ask(UsageTools(ledger, now=clock), {"period": "today"})

    assert data["estimated"] is True
    assert data["cost_usd"] == 0.0816


# --- bad calls -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        ["today"],
        "today",
        {},
        {"period": None},
        {"period": 1},
        {"period": "week"},
        {"period": "Today"},
        {"period": "today", "day": "2026-10-14"},
    ],
    ids=[
        "null",
        "list",
        "string",
        "no period",
        "null period",
        "number",
        "other period",
        "capitalised",
        "unknown key",
    ],
)
def test_invalid_arguments_are_an_error_result(tmp_path, arguments):
    clock = Clock()

    data, is_error = ask(UsageTools(make_ledger(tmp_path, clock), now=clock), arguments)

    assert is_error is True
    assert data["ok"] is False
    assert data["message"].startswith("Argumen api_usage tidak valid: ")


def test_an_unknown_tool_name_is_an_error_result(tmp_path):
    clock = Clock()
    tools = UsageTools(make_ledger(tmp_path, clock), now=clock)

    result, is_error = tools.run("remember", {"period": "today"})

    assert (json.loads(result), is_error) == (
        {"ok": False, "message": "Tool 'remember' bukan tool biaya API."},
        True,
    )


def test_a_usage_file_that_cannot_be_read_is_logged_and_a_generic_error(tmp_path, caplog):
    # A folder where the file should be: reading it raises an OSError.
    clock = Clock()
    ledger = make_ledger(tmp_path, clock)
    ledger.path.mkdir()

    with caplog.at_level(logging.ERROR, logger="waifu.brain.usage_tools"):
        data, is_error = ask(UsageTools(ledger, now=clock), {"period": "today"})

    assert (data, is_error) == (
        {"ok": False, "message": "Catatan biaya API tidak bisa dibaca saat ini."},
        True,
    )
    [record] = [record for record in caplog.records if record.name == "waifu.brain.usage_tools"]
    assert isinstance(record.exc_info[1], OSError)


# --- beside the other tools ----------------------------------------------------------------


def test_the_usage_tool_comes_after_the_device_tools_and_run_tool_hands_it_over(tmp_path):
    clock = Clock()
    ledger = make_ledger(tmp_path, clock)
    ledger.record(OPUS, TURN)
    registry = house()
    providers = [UsageTools(ledger, now=clock)]

    specs = build_tool_specs(registry, providers)
    result, is_error = run_tool(registry, "api_usage", {"period": "today"}, providers)

    assert [spec.name for spec in specs] == ["control_device", "list_devices", "api_usage"]
    assert (json.loads(result)["requests"], is_error) == (1, False)
    assert registry.get("lampu_kamar").calls == []


def test_the_usage_tool_converts_to_a_strict_claude_tool_and_an_ollama_tool(tmp_path):
    clock = Clock()
    [spec] = UsageTools(make_ledger(tmp_path, clock), now=clock).specs()

    claude_tool = to_anthropic(spec)
    ollama_tool = ollama.Tool.model_validate(to_ollama(spec))

    assert claude_tool["strict"] is True
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama_tool.function.parameters.required == ["period"]
