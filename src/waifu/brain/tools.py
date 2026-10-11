"""The two tools the LLM gets for the house, and the dispatcher that runs them.

Specs are backend-neutral `ToolSpec`s; `to_anthropic` and `to_ollama` convert
them to each vendor's wire format. Schemas stay within strict tool use: every
object has `additionalProperties: false` and there are no numeric bounds.
Specs and device lines carry no live state, so they stay byte-identical across
turns and keep the prompt cache warm.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from waifu.brain.base import ToolSpec
from waifu.devices.base import Device
from waifu.devices.registry import DeviceRegistry

logger = logging.getLogger(__name__)

CONTROL_DEVICE = "control_device"
LIST_DEVICES = "list_devices"
TOOL_NAMES = (CONTROL_DEVICE, LIST_DEVICES)

# A trailing number without leading zeros, so command `temp` + value 24 selects `temp_24`.
_NUMBERED_COMMAND = re.compile(r"^(?P<base>.+)_(?P<value>0|[1-9][0-9]*)$")
# Value ranges fixed by the device kind rather than spelled out in command names.
_KIND_VALUE_RANGES = {("light", "brightness"): (1, 100)}
_NO_COMMANDS = "(belum ada)"
_CONTROL_KEYS = frozenset({"device", "command", "value"})


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


def describe_commands(commands: list[str], kind: str | None = None) -> str:
    """Summarise commands for the model: `temp_16`..`temp_30` becomes "temp (value 16-30)"."""
    if not commands:
        return _NO_COMMANDS
    entries: dict[tuple[str, bool], list[int]] = {}
    for command in commands:
        numbered = _NUMBERED_COMMAND.match(command)
        if numbered:
            entries.setdefault((numbered["base"], True), []).append(int(numbered["value"]))
        else:
            entries.setdefault((command, False), [])
    parts = []
    for (name, is_family), values in entries.items():
        if is_family:
            parts.append(f"{name} (value {_format_values(values)})")
        elif (kind, name) in _KIND_VALUE_RANGES:
            low, high = _KIND_VALUE_RANGES[(kind, name)]
            parts.append(f"{name} (value {low}-{high})")
        else:
            parts.append(name)
    return ", ".join(parts)


def device_lines(registry: DeviceRegistry) -> list[str]:
    """One stable line per device with its id, name, aliases, and commands; no live state."""
    return [_device_line(device) for device in registry.all()]


def build_tool_specs(registry: DeviceRegistry) -> list[ToolSpec]:
    return [_control_device_spec(registry), _list_devices_spec()]


def run_tool(registry: DeviceRegistry, name: str, arguments: object) -> tuple[str, bool]:
    """Run one tool call from the model. Returns (compact JSON, is_error) and never raises."""
    if name == CONTROL_DEVICE:
        return _run_control_device(registry, arguments)
    if name == LIST_DEVICES:
        return _run_list_devices(registry, arguments)
    logger.warning("Model called unknown tool '%s'", name)
    return _error(f"Tool '{name}' tidak dikenal. Tool yang tersedia: {', '.join(TOOL_NAMES)}.")


def to_anthropic(spec: ToolSpec) -> dict[str, Any]:
    """Claude tool definition; `strict` makes the API guarantee schema-valid arguments."""
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "strict": True,
    }


def to_ollama(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema,
        },
    }


def _format_values(values: list[int]) -> str:
    runs: list[tuple[int, int]] = []
    for value in sorted(set(values)):
        if runs and value == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], value)
        else:
            runs.append((value, value))
    return ", ".join(str(low) if low == high else f"{low}-{high}" for low, high in runs)


def _device_line(device: Device) -> str:
    aliases = f" (alias: {', '.join(device.aliases)})" if device.aliases else ""
    commands = describe_commands(device.commands(), device.kind)
    return f"- {device.id}: {device.name}{aliases} — perintah: {commands}"


def _control_device_spec(registry: DeviceRegistry) -> ToolSpec:
    device_ids = [device.id for device in registry.all()]
    device_schema: dict[str, Any] = {"type": "string"}
    if device_ids:
        # Strict tool use rejects an empty enum, so an empty house leaves `device` open.
        device_schema["enum"] = device_ids
    devices = "\n".join(device_lines(registry)) or "(no devices configured yet)"
    description = (
        "Run one command on one smart-home device. For several devices or commands, "
        "make one call for each. Pick `command` from that device's list below. "
        "Use `value` for brightness/temperature and other numbers: a command listed as "
        "'temp (value 16-30)' is called with command 'temp' and value 24, which means temp_24. "
        "Leave `value` out for commands without a number.\n"
        f"Devices:\n{devices}"
    )
    return ToolSpec(
        name=CONTROL_DEVICE,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "device": device_schema,
                "command": {"type": "string"},
                "value": {"type": "integer"},
            },
            "required": ["device", "command"],
            "additionalProperties": False,
        },
    )


def _list_devices_spec() -> ToolSpec:
    return ToolSpec(
        name=LIST_DEVICES,
        description=(
            "List every device with its id, name, aliases, kind, commands, and current state "
            "(for example power and last command). Use it to check what is on before answering."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )


def _run_control_device(registry: DeviceRegistry, arguments: object) -> tuple[str, bool]:
    try:
        device_id, command, value = _parse_control_arguments(arguments)
    except _InvalidArguments as exc:
        return _invalid(CONTROL_DEVICE, arguments, exc)
    result = registry.execute(device_id, command, value)
    payload = {"ok": result.ok, "message": result.message, "state": result.state}
    return _to_json(payload), not result.ok


def _run_list_devices(registry: DeviceRegistry, arguments: object) -> tuple[str, bool]:
    try:
        _arguments_mapping(arguments, allowed=frozenset())
    except _InvalidArguments as exc:
        return _invalid(LIST_DEVICES, arguments, exc)
    try:
        descriptions = registry.describe_all()
    except Exception:
        logger.exception("Could not describe devices for list_devices")
        return _error("Daftar perangkat tidak bisa dibaca saat ini.")
    return _to_json(descriptions), False


def _parse_control_arguments(arguments: object) -> tuple[str, str, int | None]:
    mapping = _arguments_mapping(arguments, allowed=_CONTROL_KEYS)
    device_id = _required_text(mapping, "device")
    command = _required_text(mapping, "command")
    value = mapping.get("value")
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise _InvalidArguments("'value' harus bilangan bulat atau dikosongkan.")
    return device_id, command, value


def _arguments_mapping(arguments: object, allowed: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in allowed]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _required_text(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidArguments(f"'{key}' wajib diisi dengan teks.")
    return value


def _invalid(tool: str, arguments: object, exc: _InvalidArguments) -> tuple[str, bool]:
    logger.warning("Rejected %s arguments %r: %s", tool, arguments, exc)
    return _error(f"Argumen {tool} tidak valid: {exc}")


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
