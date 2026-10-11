import json
from datetime import datetime

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.base import ToolSpec
from waifu.brain.tools import (
    build_tool_specs,
    describe_commands,
    device_lines,
    run_tool,
    to_anthropic,
    to_ollama,
)
from waifu.devices.base import Device, DeviceResult
from waifu.devices.ir_codes import IrCodeStore
from waifu.devices.ir_device import IrDevice
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry

AC_COMMANDS = ["on", "off", *[f"temp_{t}" for t in range(16, 31)]]
UNSUPPORTED_STRICT_KEYWORDS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
}


def house() -> DeviceRegistry:
    return DeviceRegistry(
        [
            MockDevice(
                "lampu_kamar",
                "Lampu kamar",
                aliases=["lampu", "lampu utama"],
                commands=["on", "off", "toggle", "brightness"],
                kind="light",
            ),
            MockDevice("ac", "AC Samsung", aliases=["ac"], commands=AC_COMMANDS, kind="ir"),
        ]
    )


def spec_named(specs: list[ToolSpec], name: str) -> ToolSpec:
    return next(spec for spec in specs if spec.name == name)


def schema_nodes(node: object):
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from schema_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from schema_nodes(child)


class FixedResultDevice(Device):
    def __init__(self, result: DeviceResult) -> None:
        super().__init__("tv", "TV")
        self.result = result

    def commands(self) -> list[str]:
        return ["power"]

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        return self.result


class UnusedBlaster:
    def send(self, code_b64: str) -> None:
        raise AssertionError("blaster must not be reached")

    def learn(self, timeout_s: float) -> str:
        raise AssertionError("blaster must not be reached")


# describe_commands


def test_plain_commands_are_listed_in_their_order():
    assert describe_commands(["power", "volume_up", "volume_down"]) == (
        "power, volume_up, volume_down"
    )


def test_numbered_command_family_collapses_into_a_value_range():
    assert describe_commands(AC_COMMANDS) == "on, off, temp (value 16-30)"


def test_family_values_are_ordered_numerically_and_gaps_are_kept_visible():
    commands = ["input_10", "input_2", "input_1", "temp_16", "temp_18", "temp_17", "temp_20"]

    assert describe_commands(commands) == "input (value 1-2, 10), temp (value 16-18, 20)"


def test_numbers_with_leading_zeros_stay_literal_because_a_value_cannot_select_them():
    assert describe_commands(["ch_01", "ch_02"]) == "ch_01, ch_02"


def test_bare_command_and_its_numbered_family_are_both_listed():
    assert describe_commands(["temp", "temp_16", "temp_17"]) == "temp, temp (value 16-17)"


def test_light_brightness_shows_its_percentage_range():
    commands = ["on", "off", "toggle", "brightness"]

    assert describe_commands(commands, kind="light") == "on, off, toggle, brightness (value 1-100)"


def test_brightness_on_a_non_light_is_a_plain_command():
    assert describe_commands(["brightness"], kind="ir") == "brightness"


def test_no_commands_reads_as_none_yet():
    assert describe_commands([]) == "(belum ada)"


# device_lines


def test_device_line_lists_id_name_aliases_and_commands():
    assert device_lines(house()) == [
        "- lampu_kamar: Lampu kamar (alias: lampu, lampu utama) — perintah: "
        "on, off, toggle, brightness (value 1-100)",
        "- ac: AC Samsung (alias: ac) — perintah: on, off, temp (value 16-30)",
    ]


def test_device_line_without_aliases_or_commands():
    registry = DeviceRegistry([MockDevice("tv", "TV", commands=[])])

    assert device_lines(registry) == ["- tv: TV — perintah: (belum ada)"]


def test_device_lines_carry_no_live_state():
    registry = house()
    before = device_lines(registry)

    registry.execute("lampu_kamar", "on")

    assert device_lines(registry) == before


# build_tool_specs


def test_exactly_two_tools_are_offered():
    assert [spec.name for spec in build_tool_specs(house())] == ["control_device", "list_devices"]


def test_control_device_schema_enumerates_registry_ids_in_order():
    control = spec_named(build_tool_specs(house()), "control_device")

    assert control.input_schema == {
        "type": "object",
        "properties": {
            "device": {"type": "string", "enum": ["lampu_kamar", "ac"]},
            "command": {"type": "string"},
            "value": {"type": "integer"},
        },
        "required": ["device", "command"],
        "additionalProperties": False,
    }


def test_empty_registry_leaves_device_unconstrained_instead_of_an_empty_enum():
    control = spec_named(build_tool_specs(DeviceRegistry([])), "control_device")

    assert control.input_schema["properties"]["device"] == {"type": "string"}


def test_control_device_description_lists_devices_and_explains_value():
    registry = house()
    control = spec_named(build_tool_specs(registry), "control_device")

    for line in device_lines(registry):
        assert line in control.description
    assert "temp_24" in control.description


def test_list_devices_takes_no_arguments():
    listing = spec_named(build_tool_specs(house()), "list_devices")

    assert listing.input_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert listing.description


def test_schemas_stay_within_strict_tool_use_limits():
    for spec in build_tool_specs(house()):
        nodes = list(schema_nodes(spec.input_schema))
        for node in nodes:
            assert not UNSUPPORTED_STRICT_KEYWORDS & node.keys()
            if node.get("type") == "object":
                assert node["additionalProperties"] is False


def test_specs_are_identical_across_builds_and_state_changes_for_prompt_caching():
    registry = house()
    first = build_tool_specs(registry)

    registry.execute("lampu_kamar", "on")
    registry.execute("ac", "temp", 24)

    assert build_tool_specs(registry) == first


# run_tool: control_device


def test_control_device_runs_the_command_and_reports_the_result():
    registry = house()

    result, is_error = run_tool(
        registry, "control_device", {"device": "lampu_kamar", "command": "on"}
    )

    assert not is_error
    payload = json.loads(result)
    assert payload["ok"] is True
    assert "Lampu kamar" in payload["message"]
    assert payload["state"]["power"] == "on"
    assert registry.get("lampu_kamar").calls == [("on", None)]


def test_control_device_passes_the_value_so_temp_24_is_selected():
    registry = house()

    _, is_error = run_tool(
        registry, "control_device", {"device": "ac", "command": "temp", "value": 24}
    )

    assert not is_error
    assert registry.get("ac").calls == [("temp_24", 24)]


def test_control_device_accepts_an_explicit_null_value():
    registry = house()

    _, is_error = run_tool(
        registry, "control_device", {"device": "ac", "command": "off", "value": None}
    )

    assert not is_error
    assert registry.get("ac").calls == [("off", None)]


def test_result_is_compact_json_that_keeps_non_ascii_text():
    registry = DeviceRegistry([MockDevice("lampu", "ランプ")])

    result, _ = run_tool(registry, "control_device", {"device": "lampu", "command": "on"})

    assert "ランプ" in result
    assert result == json.dumps(json.loads(result), ensure_ascii=False, separators=(",", ":"))


def test_device_failure_is_reported_as_an_error_with_the_registry_message():
    registry = house()

    result, is_error = run_tool(registry, "control_device", {"device": "kulkas", "command": "on"})

    assert is_error
    payload = json.loads(result)
    assert payload["ok"] is False
    assert "kulkas" in payload["message"]


def test_unknown_command_is_an_error_and_never_reaches_the_device():
    registry = house()

    result, is_error = run_tool(registry, "control_device", {"device": "ac", "command": "explode"})

    assert is_error
    assert "explode" in json.loads(result)["message"]
    assert registry.get("ac").calls == []


def test_state_values_that_are_not_json_are_still_reported():
    stamp = datetime(2026, 10, 12, 7, 30)
    registry = DeviceRegistry(
        [FixedResultDevice(DeviceResult(ok=True, message="TV menyala.", state={"since": stamp}))]
    )

    result, is_error = run_tool(registry, "control_device", {"device": "tv", "command": "power"})

    assert not is_error
    assert json.loads(result)["state"]["since"] == str(stamp)


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        ["ac", "on"],
        "ac on",
        {"command": "on"},
        {"device": "ac"},
        {"device": "", "command": "on"},
        {"device": "ac", "command": "   "},
        {"device": 5, "command": "on"},
        {"device": "ac", "command": ["on"]},
        {"device": "ac", "command": "temp", "value": "24"},
        {"device": "ac", "command": "temp", "value": 24.5},
        {"device": "ac", "command": "temp", "value": True},
        {"device": "ac", "command": "on", "speed": 3},
    ],
)
def test_invalid_control_arguments_are_errors_without_touching_devices(arguments):
    registry = house()

    result, is_error = run_tool(registry, "control_device", arguments)

    assert is_error
    payload = json.loads(result)
    assert payload["ok"] is False
    assert payload["message"]
    assert registry.get("ac").calls == []


# run_tool: list_devices and unknown tools


def test_list_devices_returns_current_descriptions_including_live_state():
    registry = house()
    registry.execute("lampu_kamar", "on")

    result, is_error = run_tool(registry, "list_devices", {})

    assert not is_error
    assert json.loads(result) == registry.describe_all()
    assert json.loads(result)[0]["state"]["power"] == "on"


def test_list_devices_rejects_unexpected_arguments():
    result, is_error = run_tool(house(), "list_devices", {"device": "ac"})

    assert is_error
    assert json.loads(result)["ok"] is False


def test_list_devices_failure_becomes_an_error_instead_of_raising(tmp_path):
    path = tmp_path / "ir_codes.json"
    path.write_text("{corrupt", encoding="utf-8")
    registry = DeviceRegistry(
        [IrDevice("ac", "AC Samsung", [], UnusedBlaster(), IrCodeStore(path))]
    )

    result, is_error = run_tool(registry, "list_devices", {})

    assert is_error
    assert json.loads(result)["ok"] is False


def test_unknown_tool_is_an_error_naming_the_available_tools():
    result, is_error = run_tool(house(), "open_garage", {})

    assert is_error
    payload = json.loads(result)
    assert payload["ok"] is False
    assert "open_garage" in payload["message"]
    assert "control_device" in payload["message"]
    assert "list_devices" in payload["message"]


# wire formats


def test_to_anthropic_marks_the_tool_strict():
    spec = spec_named(build_tool_specs(house()), "control_device")

    tool = to_anthropic(spec)

    assert tool == {
        "name": "control_device",
        "description": spec.description,
        "input_schema": spec.input_schema,
        "strict": True,
    }
    assert tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__


def test_to_ollama_wraps_the_spec_as_a_function_tool():
    spec = spec_named(build_tool_specs(house()), "control_device")

    tool = to_ollama(spec)

    assert tool == {
        "type": "function",
        "function": {
            "name": "control_device",
            "description": spec.description,
            "parameters": spec.input_schema,
        },
    }


def test_to_ollama_output_is_accepted_by_the_ollama_client_model():
    spec = spec_named(build_tool_specs(house()), "control_device")

    tool = ollama.Tool.model_validate(to_ollama(spec))

    assert tool.function.name == "control_device"
    assert tool.function.parameters.required == ["device", "command"]
    assert tool.function.parameters.properties["device"].enum == ["lampu_kamar", "ac"]
