"""Contract every controllable device implements.

The brain only ever sees `Device.describe()` and `DeviceResult`; it never
touches a vendor library directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class DeviceError(Exception):
    """A device command failed in a way worth telling the user about."""


@dataclass(frozen=True)
class DeviceResult:
    ok: bool
    message: str
    state: dict[str, Any] = field(default_factory=dict)


class Device(ABC):
    """A named thing in the house that accepts string commands.

    `execute` raises DeviceError (or lets vendor exceptions escape); the
    registry turns every failure into a `DeviceResult(ok=False, ...)`.
    """

    kind: str = "device"

    def __init__(self, device_id: str, name: str, aliases: list[str] | None = None) -> None:
        self.id = device_id
        self.name = name
        self.aliases = list(aliases or [])
        self.state: dict[str, Any] = {}

    @abstractmethod
    def commands(self) -> list[str]:
        """Command names this device accepts right now, e.g. ["on", "off"]."""

    @abstractmethod
    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        """Run one command. `value` carries brightness, temperature, and similar numbers."""

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "aliases": self.aliases,
            "kind": self.kind,
            "commands": self.commands(),
            "state": dict(self.state),
        }
