"""Broadlink RM-series IR blaster: sends and learns base64 IR codes over the LAN.

Implements the blaster protocol `IrDevice` relies on: `send(code_b64)` and
`learn(timeout_s) -> str`. The device is discovered and authenticated on the
first command, not at construction, so building the registry needs no network.
"""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Callable
from typing import Protocol, TypeVar

import broadlink
from broadlink.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConnectionClosedError,
    NetworkTimeoutError,
    ReadError,
    StorageError,
)

from waifu.devices.base import DeviceError

logger = logging.getLogger(__name__)

SOCKET_TIMEOUT_S = 5
LEARN_POLL_S = 0.5

# socket.timeout is TimeoutError, an OSError; broadlink re-raises its own socket
# timeouts as NetworkTimeoutError. The auth errors mean the session went stale
# (blaster rebooted or logged us out), which a fresh auth() fixes.
_CONNECTION_ERRORS = (
    OSError,
    NetworkTimeoutError,
    AuthenticationError,
    AuthorizationError,
    ConnectionClosedError,
)

# Until a code has been captured, check_data() fails with a firmware error code:
# ReadError (-10) or StorageError (-5), depending on the RM model and firmware.
_NO_CODE_YET = (ReadError, StorageError)

T = TypeVar("T")


class BroadlinkRemote(Protocol):
    """The subset of a broadlink `rm*` device object this module uses."""

    def auth(self) -> bool: ...

    def send_data(self, data: bytes) -> None: ...

    def enter_learning(self) -> None: ...

    def check_data(self) -> bytes: ...


def _hello(host: str) -> BroadlinkRemote:
    device = broadlink.hello(host, timeout=SOCKET_TIMEOUT_S)
    # hello() only bounds discovery; later requests would use broadlink's 10 s default.
    device.timeout = SOCKET_TIMEOUT_S
    return device


class BroadlinkBlaster:
    """IR blaster at `host`; one instance is shared by every IrDevice on that blaster."""

    def __init__(
        self,
        host: str,
        *,
        factory: Callable[[str], BroadlinkRemote] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._host = host
        self._factory = factory or _hello
        self._sleep, self._clock = sleep, clock
        self._device: BroadlinkRemote | None = None

    def send(self, code_b64: str) -> None:
        try:
            data = base64.b64decode(code_b64, validate=True)
        except ValueError as exc:
            raise DeviceError("Kode IR yang tersimpan rusak, coba pelajari ulang.") from exc
        self._call(lambda device: device.send_data(data))

    def learn(self, timeout_s: float = 20.0) -> str:
        """Wait for the owner to press a remote button at the blaster; return its code."""
        self._call(lambda device: device.enter_learning())
        deadline = self._clock() + timeout_s
        while self._clock() < deadline:
            self._sleep(LEARN_POLL_S)
            try:
                packet = self._call(lambda device: device.check_data())
            except _NO_CODE_YET:
                continue
            logger.info("Learned a %d-byte IR code at %s", len(packet), self._host)
            return base64.b64encode(packet).decode("ascii")
        raise DeviceError(
            f"Tidak ada sinyal IR yang tertangkap dalam {timeout_s:g} detik. "
            "Arahkan remote ke blaster lalu tekan tombolnya."
        )

    def _call(self, operation: Callable[[BroadlinkRemote], T]) -> T:
        """Run one device operation, reconnecting once if an open session dropped.

        A command that had to discover the blaster first is not retried: hello()
        already resends its probe every second until it times out, so a second
        discovery would only double the wait for a blaster that is unplugged.
        """
        had_session = self._device is not None
        try:
            return operation(self._connected())
        except _CONNECTION_ERRORS as exc:
            self._device = None
            if not had_session:
                raise self._unreachable() from exc
            logger.warning("Broadlink blaster at %s failed (%s); reconnecting", self._host, exc)
        try:
            return operation(self._connected())
        except _CONNECTION_ERRORS as exc:
            self._device = None
            raise self._unreachable() from exc

    def _unreachable(self) -> DeviceError:
        logger.warning("Broadlink blaster at %s is unreachable", self._host)
        return DeviceError(
            f"IR blaster Broadlink di {self._host} tidak merespons. "
            "Pastikan blaster menyala dan terhubung ke Wi-Fi."
        )

    def _connected(self) -> BroadlinkRemote:
        if self._device is None:
            device = self._factory(self._host)
            device.auth()
            logger.info("Authenticated with Broadlink blaster at %s", self._host)
            self._device = device
        return self._device
