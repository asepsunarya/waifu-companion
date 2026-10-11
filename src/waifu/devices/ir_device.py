"""An appliance controlled by replaying learned IR codes (AC, projector, TV).

The commands it accepts are exactly the codes learned for it in the
`IrCodeStore`; a numeric `value` selects a combined code, so `temp` + 24 sends
the code learned as `temp_24`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from waifu.devices.base import Device, DeviceError, DeviceResult
from waifu.devices.ir_codes import IrCodeStore, validate_command_name

logger = logging.getLogger(__name__)


class Blaster(Protocol):
    """An IR transmitter that can replay and capture base64-encoded codes."""

    def send(self, code_b64: str) -> None: ...

    def learn(self, timeout_s: float) -> str: ...


def resolve_command(command: str, value: int | None, available: Sequence[str]) -> str | None:
    """Pick `f"{command}_{value}"` when it is available, else `command`, else None."""
    if value is not None and f"{command}_{value}" in available:
        return f"{command}_{value}"
    if command in available:
        return command
    return None


class IrDevice(Device):
    kind = "ir"

    def __init__(
        self,
        device_id: str,
        name: str,
        aliases: list[str] | None,
        blaster: Blaster,
        store: IrCodeStore,
        declared_commands: list[str] | None = None,
    ) -> None:
        super().__init__(device_id, name, aliases)
        self._blaster = blaster
        self._store = store
        self.declared_commands = list(declared_commands or [])

    def commands(self) -> list[str]:
        return self._store.commands(self.id)

    def missing_commands(self) -> list[str]:
        """Commands declared in config.yaml that have no learned code yet, in declared order."""
        learned = set(self.commands())
        declared = dict.fromkeys(self.declared_commands)
        return [command for command in declared if command not in learned]

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        learned = self.commands()
        resolved = resolve_command(command, value, learned)
        code = self._store.get(self.id, resolved) if resolved else None
        if resolved is None or code is None:
            requested = f"{command}_{value}" if value is not None else command
            raise DeviceError(self._unknown_command_message(requested, learned))
        self._blaster.send(code)
        self.state["last_command"] = resolved
        return DeviceResult(
            ok=True,
            message=f"{self.name}: sinyal '{resolved}' sudah dikirim.",
            state=dict(self.state),
        )

    def learn(self, command: str, timeout_s: float = 20.0) -> None:
        """Capture one remote button with the blaster and store it as `command`."""
        validate_command_name(command)
        code = self._blaster.learn(timeout_s)
        self._store.put(self.id, command, code)
        logger.info("Learned IR code '%s' for device '%s'", command, self.id)

    def _unknown_command_message(self, requested: str, learned: list[str]) -> str:
        if not learned:
            return (
                f"{self.name} belum punya kode IR yang dipelajari. "
                f"Ajari dulu dengan: waifu learn-ir {self.id} <perintah>"
            )
        return (
            f"{self.name} belum punya kode IR '{requested}'. "
            f"Kode yang sudah dipelajari: {', '.join(learned)}."
        )
