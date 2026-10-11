"""The set of devices the brain can control, and the one place failures are contained.

`DeviceRegistry.execute` never raises: every failure becomes a
`DeviceResult(ok=False, ...)` with an Indonesian message the brain can turn
into an honest spoken reply. `build_registry` creates the devices described in
config.yaml without opening any connection; vendor links open on first use.
"""

from __future__ import annotations

import logging
from typing import Any

from waifu.config import AppConfig, DeviceConfig, IrConnection
from waifu.devices.base import Device, DeviceError, DeviceResult
from waifu.devices.broadlink_ir import BroadlinkBlaster
from waifu.devices.ir_codes import IrCodeStore
from waifu.devices.ir_device import Blaster, IrDevice, resolve_command
from waifu.devices.mock import MockDevice
from waifu.devices.pc import PcDevice
from waifu.devices.tuya import TuyaIrBlaster, TuyaLight, TuyaPlug

logger = logging.getLogger(__name__)

# ("broadlink", host) or ("tuya", device id): one physical IR blaster.
BlasterKey = tuple[str, str]


class DeviceRegistry:
    def __init__(self, devices: list[Device]) -> None:
        self._devices: dict[str, Device] = {}
        for device in devices:
            if device.id in self._devices:
                raise ValueError(f"duplicate device id '{device.id}'")
            self._devices[device.id] = device

    def get(self, device_id: str) -> Device | None:
        return self._devices.get(device_id)

    def all(self) -> list[Device]:
        return list(self._devices.values())

    def describe_all(self) -> list[dict[str, Any]]:
        return [device.describe() for device in self._devices.values()]

    def execute(self, device_id: str, command: str, value: int | None = None) -> DeviceResult:
        device = self._devices.get(device_id)
        if device is None:
            return _failure(
                f"Perangkat '{device_id}' tidak dikenal. "
                f"Perangkat yang ada: {_join_or_none(list(self._devices))}."
            )
        try:
            available = device.commands()
            if resolve_command(command, value, available) is None:
                with_value = f" dengan nilai {value}" if value is not None else ""
                return _failure(
                    f"{device.name} tidak punya perintah '{command}'{with_value}. "
                    f"Perintah yang tersedia: {_join_or_none(available)}."
                )
            return device.execute(command, value)
        except DeviceError as exc:
            logger.warning("Device '%s' failed '%s': %s", device_id, command, exc)
            return _failure(str(exc))
        except OSError:
            logger.exception("Device '%s' unreachable while running '%s'", device_id, command)
            return _failure(f"{device.name} tidak bisa dihubungi (koneksi gagal atau waktu habis).")
        except Exception:
            logger.exception("Unexpected error on device '%s' running '%s'", device_id, command)
            return _failure(
                f"Terjadi kesalahan tak terduga saat menjalankan '{command}' pada {device.name}."
            )


def _failure(message: str) -> DeviceResult:
    return DeviceResult(ok=False, message=message)


def _join_or_none(names: list[str]) -> str:
    return ", ".join(names) if names else "(belum ada)"


def build_registry(config: AppConfig, *, dry_run: bool = False) -> DeviceRegistry:
    """Create every configured device; nothing connects or runs until its first command.

    With `dry_run`, each device is swapped for a MockDevice that shows the brain
    the same id, name, aliases, kind, and commands but only records what it is told.
    """
    store = IrCodeStore(config.ir_codes_path)
    blasters: dict[BlasterKey, Blaster] = {}
    devices = [_build_device(device, store, blasters) for device in config.devices]
    if dry_run:
        devices = [_simulated(device) for device in devices]
    logger.info("Built %d device(s)%s", len(devices), " as dry-run mocks" if dry_run else "")
    return DeviceRegistry(devices)


def _build_device(
    device: DeviceConfig, store: IrCodeStore, blasters: dict[BlasterKey, Blaster]
) -> Device:
    # DeviceConfig validation guarantees the connection block each type needs.
    if device.type == "tuya_light":
        return TuyaLight(device.id, device.name, device.aliases, device.tuya)
    if device.type == "tuya_plug":
        return TuyaPlug(device.id, device.name, device.aliases, device.tuya)
    if device.type == "ir":
        blaster = _shared_blaster(device.ir, blasters)
        return IrDevice(device.id, device.name, device.aliases, blaster, store, device.commands)
    if device.type == "pc":
        return PcDevice(device.id, device.name, device.aliases, device.pc)
    return MockDevice(device.id, device.name, device.aliases, device.commands or None)


def _shared_blaster(conn: IrConnection, blasters: dict[BlasterKey, Blaster]) -> Blaster:
    """Return the blaster for this physical box, creating it on first use.

    Every IR device on one box shares the blaster, so they share one connection
    (and, for Broadlink, one authenticated session).
    """
    if conn.backend == "tuya":
        key = ("tuya", conn.tuya.device_id)
        if key not in blasters:
            blasters[key] = TuyaIrBlaster(conn.tuya)
    else:
        key = ("broadlink", conn.host)
        if key not in blasters:
            blasters[key] = BroadlinkBlaster(conn.host)
    return blasters[key]


def _simulated(device: Device) -> MockDevice:
    """A recording stand-in that looks like `device`, without ever calling it."""
    commands = device.commands()
    if isinstance(device, IrDevice):
        # Offer what the owner intends to learn too, so dry runs work before any learning.
        commands = sorted({*commands, *device.declared_commands})
    return MockDevice(device.id, device.name, device.aliases, commands, kind=device.kind)
