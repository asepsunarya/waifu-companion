"""The two tools the LLM gets for the house, other providers' tools, and the dispatcher.

Specs are backend-neutral `ToolSpec`s; `to_anthropic` and `to_ollama` convert
them to each vendor's wire format. Schemas stay within strict tool use: every
object has `additionalProperties: false` and there are no numeric bounds.
Specs and device lines carry no live state, so they stay byte-identical across
turns and keep the prompt cache warm. A `ToolProvider` (the gacha buddy's game
tools) adds its own tools beside the device ones.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from waifu.brain.base import ToolCallRecord, ToolSpec
from waifu.brain.confirm import ConfirmationGate
from waifu.devices.base import Device
from waifu.devices.registry import KIND_VALUE_RANGES, DeviceRegistry
from waifu.finance.values import FINANCE_TOOLS

logger = logging.getLogger(__name__)

CONTROL_DEVICE = "control_device"
LIST_DEVICES = "list_devices"
TOOL_NAMES = (CONTROL_DEVICE, LIST_DEVICES)
# Tools whose arguments are Master's money (an amount, what it was for, a wallet, a goal): the
# INFO log, logs\waifu.log under autostart, shows them as "…".
PRIVATE_ARGUMENT_TOOLS = frozenset(FINANCE_TOOLS)

# A trailing number without leading zeros, so command `temp` + value 24 selects `temp_24`.
_NUMBERED_COMMAND = re.compile(r"^(?P<base>.+)_(?P<value>0|[1-9][0-9]*)$")
_NO_COMMANDS = "(belum ada)"
_CONTROL_KEYS = frozenset({"device", "command", "value"})


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class ToolProvider(Protocol):
    """Tools beyond the house, such as the gacha buddy's game tools."""

    def specs(self) -> list[ToolSpec]: ...

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        """(compact JSON, is_error) for one of its own tools; never raises."""
        ...


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
        elif (kind, name) in KIND_VALUE_RANGES:
            allowed = KIND_VALUE_RANGES[(kind, name)]
            parts.append(f"{name} (value {allowed.low}-{allowed.high})")
        else:
            parts.append(name)
    return ", ".join(parts)


def device_lines(registry: DeviceRegistry) -> list[str]:
    """One stable line per device with its id, name, aliases, and commands; no live state."""
    return [_device_line(device, _feeds_pc(registry, device)) for device in registry.all()]


def build_tool_specs(
    registry: DeviceRegistry, providers: Sequence[ToolProvider] = ()
) -> list[ToolSpec]:
    specs = [_control_device_spec(registry), _list_devices_spec()]
    for provider in providers:
        specs.extend(provider.specs())
    names = [spec.name for spec in specs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate tool names: {', '.join(duplicates)}")
    return specs


def run_tool(
    registry: DeviceRegistry,
    name: str,
    arguments: object,
    providers: Sequence[ToolProvider] = (),
    *,
    confirmations: ConfirmationGate | None = None,
) -> tuple[str, bool]:
    """Run one tool call from the model. Returns (compact JSON, is_error) and never raises.

    With `confirmations`, a `control_device` call that would turn the PC off is held until
    Master's next words say yes; the brains always pass one.
    """
    if name == CONTROL_DEVICE:
        return _run_control_device(registry, arguments, confirmations)
    if name == LIST_DEVICES:
        return _run_list_devices(registry, arguments)
    for provider in providers:
        if any(spec.name == name for spec in provider.specs()):
            try:
                return provider.run(name, arguments)
            except Exception:
                logger.exception("Tool '%s' failed", name)
                return _error(f"Tool '{name}' gagal dijalankan.")
    logger.warning("Model called unknown tool '%s'", name)
    available = [*TOOL_NAMES, *(spec.name for p in providers for spec in p.specs())]
    return _error(f"Tool '{name}' tidak dikenal. Tool yang tersedia: {', '.join(available)}.")


def logged_arguments(name: str, arguments: object) -> object:
    """What the INFO line of a tool call shows of its arguments: "…" for a tool in
    PRIVATE_ARGUMENT_TOOLS, else the arguments themselves. Results are logged only at DEBUG."""
    return "…" if name in PRIVATE_ARGUMENT_TOOLS else arguments


def held_for_yes(record: ToolCallRecord) -> bool:
    """Whether a tool call was held for Master's yes instead of run: an error result with
    `needs_confirmation`, as a `control_device` turning the PC off or a `record_expense` gives.
    """
    if not record.is_error:
        return False
    result = json.loads(record.result)
    return isinstance(result, dict) and result.get("needs_confirmation") is True


def to_anthropic(spec: ToolSpec) -> dict[str, Any]:
    """Claude tool definition; `strict` makes the API guarantee schema-valid arguments.

    Only a tool that requires an argument is strict, as every tool that acts on something (a
    device, a list, an account) does. One request takes at most 20 strict tools (and 24
    optional and 16 union-typed parameters across their schemas), and every tool checks its
    own arguments anyway, so the reads whose arguments may all be left out go without it.
    """
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.input_schema,
        "strict": bool(spec.input_schema.get("required")),
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


def _feeds_pc(registry: DeviceRegistry, device: Device) -> bool:
    """A plug feeding the PC (the PC itself needs no note: the persona covers it)."""
    return device.kind != "pc" and registry.powers_pc(device.id)


def _device_line(device: Device, feeds_pc: bool = False) -> str:
    aliases = f" (alias: {', '.join(device.aliases)})" if device.aliases else ""
    commands = describe_commands(device.commands(), device.kind)
    note = ""
    if feeds_pc:
        # So the persona's "the PC waits for Master's yes" covers cutting its power too.
        note = (
            " — mengalirkan listrik ke PC: mematikan atau membaliknya (toggle) sama dengan "
            "mematikan PC, jadi perlu konfirmasi Master dulu"
        )
    elif device.kind == "media":
        # A scheduled toggle runs silently while Master may sleep: it can restart paused music.
        note = (
            " — play_pause dan mute bolak-balik (toggle): di jadwal, pakai stop untuk "
            "menghentikan musik dan volume untuk mengecilkan suara"
        )
    return f"- {device.id}: {device.name}{aliases} — perintah: {commands}{note}"


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
        "All brightness and volume values are percentages, as Master says them. "
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


def _run_control_device(
    registry: DeviceRegistry, arguments: object, confirmations: ConfirmationGate | None
) -> tuple[str, bool]:
    try:
        device_id, command, value = _parse_control_arguments(arguments)
    except _InvalidArguments as exc:
        return _invalid(CONTROL_DEVICE, arguments, exc)
    if confirmations is not None:
        refusal = confirmations.check(registry, device_id, command, value)
        if refusal is not None:
            # An error result, so an empty final reply never tells Master it is done.
            payload = {"ok": False, "needs_confirmation": True, "message": refusal}
            return _to_json(payload), True
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
