"""A stand-in device that records commands instead of touching hardware.

Used by tests and by `--dry-run`, where it keeps the real device's id, name,
aliases, commands, and kind so the brain sees the same house.
"""

from __future__ import annotations

from waifu.devices.base import Device, DeviceError, DeviceResult
from waifu.devices.ir_device import resolve_command


class MockDevice(Device):
    kind = "mock"

    def __init__(
        self,
        device_id: str,
        name: str,
        aliases: list[str] | None = None,
        commands: list[str] | None = None,
        kind: str = "mock",
    ) -> None:
        super().__init__(device_id, name, aliases)
        self.kind = kind
        self._commands = list(commands) if commands is not None else ["on", "off"]
        self.calls: list[tuple[str, int | None]] = []

    def commands(self) -> list[str]:
        return list(self._commands)

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        resolved = resolve_command(command, value, self._commands)
        if resolved is None:
            raise DeviceError(
                f"{self.name} tidak punya perintah '{command}'. "
                f"Perintah yang tersedia: {', '.join(self._commands)}."
            )
        self.calls.append((resolved, value))
        if resolved in ("on", "off"):
            self.state["power"] = resolved
        self.state["last_command"] = resolved
        return DeviceResult(
            ok=True,
            message=f"{self.name}: perintah '{resolved}' berhasil (simulasi).",
            state=dict(self.state),
        )
