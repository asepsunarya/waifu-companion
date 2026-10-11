"""Tuya Wi-Fi light, plug, and IR blaster over the local LAN protocol (tinytuya).

tinytuya reports most failures as dicts like {"Error": ..., "Err": "905", ...}
instead of raising, and raises RuntimeError when it cannot reach or identify a
device. Every vendor call goes through `_TuyaLink.call`, which turns both into
a DeviceError with an Indonesian message. The local key is a secret: it goes to
tinytuya and nowhere else, never into a message or a log line.
"""

from __future__ import annotations

import logging
import math
import time
from abc import abstractmethod
from collections.abc import Callable
from typing import Any, NamedTuple

from waifu.config import TuyaConnection
from waifu.devices.base import Device, DeviceError, DeviceResult

logger = logging.getLogger(__name__)

# tinytuya defaults to a 5 s timeout, 5 retries, and 5 s between retries, so a
# device that is off would stall a spoken reply for most of a minute. One 2 s
# connect attempt per request keeps a missing device's failure to a few seconds
# once its IP is known.
CONNECT_TIMEOUT_S = 2.0
RETRY_LIMIT = 1
RETRY_DELAY_S = 0.5

# Given address "Auto", tinytuya's constructor scans the LAN for 18 s whenever the
# device does not answer, on every command. The link looks the device up itself
# instead: one short scan, the IP kept while the device answers, and no new scan
# for a while after one that found nothing, so a lamp switched off at the wall
# fails fast. A fixed IP in config.yaml skips the lookup entirely.
LOOKUP_SCAN_S = 6.0
LOOKUP_RETRY_AFTER_S = 30.0
AUTO_ADDRESSES = ("", "Auto", "0.0.0.0")  # the values tinytuya treats as "find it"

PLUG_SWITCH_DP = "1"
MIN_BRIGHTNESS = 1
MAX_BRIGHTNESS = 100
IR_BLASTER_LABEL = "IR blaster Tuya"

# tinytuya error codes (core/error_helper.py) that deserve a specific hint.
ERR_PAYLOAD = "904"
ERR_FUNCTION = "907"
ERR_KEY_OR_VER = "914"
# Errors that prove the device answered at the address we used.
_ANSWERED_ERRORS = (ERR_PAYLOAD, ERR_FUNCTION, ERR_KEY_OR_VER)

VendorFactory = Callable[..., Any]


class LanAddress(NamedTuple):
    """Where a Tuya device announced itself on the LAN."""

    ip: str
    version: float | None


Locator = Callable[[str, float], LanAddress | None]


def _scan_lan(device_id: str, scan_s: float) -> LanAddress | None:
    """Listen at most `scan_s` seconds for the LAN announcement of `device_id`."""
    from tinytuya import scanner

    try:
        found = scanner.devices(
            verbose=False, scantime=scan_s, poll=False, byID=True, wantids=[device_id]
        )
    except OSError as exc:  # e.g. another Tuya tool holds the discovery ports
        logger.warning("Tuya LAN scan for %s failed: %s", device_id, exc)
        return None
    info = found.get(device_id) or {}
    if not info.get("ip"):
        return None
    try:
        version = float(info.get("version"))
    except (TypeError, ValueError):
        version = None
    return LanAddress(info["ip"], version)


def _bulb_device(**kwargs: Any) -> Any:
    import tinytuya

    return tinytuya.BulbDevice(**kwargs)


def _outlet_device(**kwargs: Any) -> Any:
    import tinytuya

    return tinytuya.OutletDevice(**kwargs)


def _ir_remote_device(**kwargs: Any) -> Any:
    from tinytuya.Contrib.IRRemoteControlDevice import IRRemoteControlDevice

    return IRRemoteControlDevice(**kwargs)


def _dimmest_percentage(bulb: Any) -> int:
    """Lowest brightness percentage tinytuya sets on `bulb` instead of turning it off.

    tinytuya maps a percentage to int(value_max * pct // 100) and turns the bulb off
    below value_min: 25 of 255 for type A and C bulbs (so 1-9 % means off), 10 of
    1000 for type B. Detecting the type first is what set_brightness_percentage does.
    """
    if not bulb.bulb_configured:
        bulb.detect_bulb()
        if not bulb.bulb_configured:
            raise RuntimeError("bulb type not detected, so its brightness range is unknown")
    value_min, value_max = bulb.dpset["value_min"], bulb.dpset["value_max"]
    return max(MIN_BRIGHTNESS, math.ceil(value_min * 100 / value_max))


def _failure_message(label: str, err_code: str | None) -> str:
    if err_code in (ERR_PAYLOAD, ERR_KEY_OR_VER):
        return f"{label} menolak koneksi. Cek local_key dan version-nya di config.yaml."
    if err_code == ERR_FUNCTION:
        return f"{label} tidak mendukung perintah itu."
    return f"{label} tidak bisa dihubungi. Pastikan perangkatnya menyala dan tersambung ke Wi-Fi."


class _TuyaLink:
    """One tinytuya device object, opened on first use and kept while the device answers."""

    def __init__(
        self,
        label: str,
        conn: TuyaConnection,
        factory: VendorFactory,
        locate: Locator,
        clock: Callable[[], float],
    ) -> None:
        self._label = label
        self._conn = conn
        self._factory = factory
        self._locate = locate
        self._clock = clock
        self._device: Any = None
        self._found: LanAddress | None = None
        self._lookup_failed_at: float | None = None

    def call(self, action: Callable[[Any], Any]) -> Any:
        """Run `action` on the vendor device and return its result, or raise DeviceError."""
        try:
            result = action(self._open())
        except RuntimeError as exc:
            logger.warning("Tuya device %s failed: %s", self._conn.device_id, exc)
            self._lost_contact()
            raise DeviceError(_failure_message(self._label, None)) from exc
        if isinstance(result, dict) and ("Error" in result or "Err" in result):
            err_code = str(result.get("Err"))
            logger.warning(
                "Tuya device %s returned error %s: %s",
                self._conn.device_id,
                err_code,
                result.get("Error"),
            )
            if err_code not in _ANSWERED_ERRORS:
                self._lost_contact()
            raise DeviceError(_failure_message(self._label, err_code))
        return result

    def _open(self) -> Any:
        if self._device is None:
            address, version = self._conn.address, self._conn.version
            if address in AUTO_ADDRESSES:
                found = self._lookup()
                address, version = found.ip, found.version or version
            self._device = self._factory(
                dev_id=self._conn.device_id,
                address=address,
                local_key=self._conn.local_key,
                version=version,
                connection_timeout=CONNECT_TIMEOUT_S,
                connection_retry_limit=RETRY_LIMIT,
                connection_retry_delay=RETRY_DELAY_S,
            )
        return self._device

    def _lookup(self) -> LanAddress:
        if self._found is None:
            failed_at = self._lookup_failed_at
            if failed_at is not None and self._clock() - failed_at < LOOKUP_RETRY_AFTER_S:
                raise DeviceError(_failure_message(self._label, None))
            self._found = self._locate(self._conn.device_id, LOOKUP_SCAN_S)
            if self._found is None:
                self._lookup_failed_at = self._clock()
                logger.warning(
                    "Tuya device %s was not found on the LAN within %.0f s",
                    self._conn.device_id,
                    LOOKUP_SCAN_S,
                )
                raise DeviceError(_failure_message(self._label, None))
            self._lookup_failed_at = None
            logger.info("Found Tuya device %s at %s", self._conn.device_id, self._found.ip)
        return self._found

    def _lost_contact(self) -> None:
        # A device that was looked up may have a new IP since: look it up again next time.
        if self._found is not None:
            self._found = None
            self._device = None


class _TuyaSwitch(Device):
    """A Tuya device with on, off, and toggle."""

    def __init__(
        self,
        device_id: str,
        name: str,
        aliases: list[str] | None,
        conn: TuyaConnection,
        factory: VendorFactory,
        locate: Locator,
        clock: Callable[[], float],
    ) -> None:
        super().__init__(device_id, name, aliases)
        self._link = _TuyaLink(name, conn, factory, locate, clock)

    def commands(self) -> list[str]:
        return ["on", "off", "toggle"]

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        if command == "on":
            return self._switch(on=True)
        if command == "off":
            return self._switch(on=False)
        if command == "toggle":
            return self._switch(on=not self._is_on())
        raise DeviceError(
            f"{self.name} tidak punya perintah '{command}'. "
            f"Perintah yang tersedia: {', '.join(self.commands())}."
        )

    def _switch(self, *, on: bool) -> DeviceResult:
        self._link.call(lambda device: device.turn_on() if on else device.turn_off())
        self.state["power"] = on
        return DeviceResult(
            ok=True,
            message=f"{self.name} {'dinyalakan' if on else 'dimatikan'}.",
            state=dict(self.state),
        )

    def _is_on(self) -> bool:
        # Always ask: the owner may have switched it from the Smart Life app or a remote.
        # Failing to reach the device raises here, since switching it would fail too.
        power = self._query_power()
        if power is None:
            logger.warning("Tuya device %s did not report its power; using the last known", self.id)
            power = self.state.get("power")
        if power is None:
            raise DeviceError(
                f"Status {self.name} tidak terbaca, jadi tidak bisa dibalik. "
                "Coba perintah nyalakan atau matikan."
            )
        return power

    @abstractmethod
    def _query_power(self) -> bool | None:
        """Ask the device whether it is on; None when its answer has no power value."""

    @staticmethod
    def _power_from(value: object) -> bool | None:
        return value if isinstance(value, bool) else None


class TuyaLight(_TuyaSwitch):
    kind = "light"

    def __init__(
        self,
        device_id: str,
        name: str,
        aliases: list[str] | None,
        conn: TuyaConnection,
        *,
        factory: VendorFactory | None = None,
        locate: Locator = _scan_lan,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(device_id, name, aliases, conn, factory or _bulb_device, locate, clock)

    def commands(self) -> list[str]:
        return [*super().commands(), "brightness"]

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        if command == "brightness":
            return self._set_brightness(value)
        return super().execute(command, value)

    def _set_brightness(self, value: int | None) -> DeviceResult:
        if value is None or not MIN_BRIGHTNESS <= value <= MAX_BRIGHTNESS:
            raise DeviceError(
                f"Kecerahan {self.name} harus angka {MIN_BRIGHTNESS} sampai {MAX_BRIGHTNESS}."
            )
        # tinytuya turns the bulb off for a percentage below its type's minimum, so
        # raise it to that minimum; at or above it, tinytuya switches the bulb on.
        level = max(value, self._link.call(_dimmest_percentage))
        self._link.call(lambda bulb: bulb.set_brightness_percentage(level))
        self.state.update(power=True, brightness=level)
        dimmest = ", paling redup yang bisa" if level > value else ""
        return DeviceResult(
            ok=True,
            message=f"Kecerahan {self.name} diatur ke {level}%{dimmest}.",
            state=dict(self.state),
        )

    def _query_power(self) -> bool | None:
        bulb_state = self._link.call(lambda bulb: bulb.state())
        return self._power_from(bulb_state.get("is_on"))


class TuyaPlug(_TuyaSwitch):
    kind = "plug"

    def __init__(
        self,
        device_id: str,
        name: str,
        aliases: list[str] | None,
        conn: TuyaConnection,
        *,
        factory: VendorFactory | None = None,
        locate: Locator = _scan_lan,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(device_id, name, aliases, conn, factory or _outlet_device, locate, clock)

    def _query_power(self) -> bool | None:
        status = self._link.call(lambda outlet: outlet.status()) or {}
        return self._power_from(status.get("dps", {}).get(PLUG_SWITCH_DP))


class TuyaIrBlaster:
    """Sends and learns raw base64 IR codes through a Tuya universal remote."""

    def __init__(
        self,
        conn: TuyaConnection,
        *,
        factory: VendorFactory | None = None,
        locate: Locator = _scan_lan,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._conn = conn
        self._factory = factory or _ir_remote_device
        self._link = _TuyaLink(IR_BLASTER_LABEL, conn, self._open_remote, locate, clock)

    def send(self, code_b64: str) -> None:
        self._link.call(lambda remote: remote.send_button(code_b64))

    def learn(self, timeout_s: float = 20.0) -> str:
        """Wait up to `timeout_s` for a remote button press and return its base64 code."""
        code = self._link.call(lambda remote: remote.receive_button(timeout=timeout_s))
        if not isinstance(code, str) or not code:
            raise DeviceError(
                f"Tidak ada sinyal remote dalam {timeout_s:g} detik. "
                f"Arahkan remote ke {IR_BLASTER_LABEL}, tekan tombolnya, lalu coba lagi."
            )
        return code

    def _open_remote(self, **kwargs: Any) -> Any:
        # The constructor probes the device to learn which data points it uses;
        # control_type stays 0 when it did not answer, and every send would fail.
        # RuntimeError is how the link learns that a device did not answer.
        remote = self._factory(**kwargs)
        if not remote.control_type:
            raise RuntimeError("IR blaster did not answer control-type detection")
        return remote
