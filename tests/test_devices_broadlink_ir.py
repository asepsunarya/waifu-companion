import base64

import broadlink
import pytest

from waifu.devices.base import DeviceError
from waifu.devices.broadlink_ir import BroadlinkBlaster

HOST = "192.168.1.50"
POWER_CODE = bytes.fromhex("2600500000012a94")
NO_CODE_YET = broadlink.exceptions.ReadError(-10, "Read error")


class FakeRemote:
    """Stands in for a broadlink rm/rm4 device object."""

    def __init__(
        self,
        *,
        send_error: Exception | None = None,
        learn_error: Exception | None = None,
        polls: list[bytes | Exception] | None = None,
    ) -> None:
        self.auth_calls = 0
        self.sent: list[bytes] = []
        self.learning = False
        self.send_error = send_error
        self._learn_error = learn_error
        self._polls = list(polls or [])

    def auth(self) -> bool:
        self.auth_calls += 1
        return True

    def send_data(self, data: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)

    def enter_learning(self) -> None:
        if self._learn_error is not None:
            raise self._learn_error
        self.learning = True

    def check_data(self) -> bytes:
        result = self._polls.pop(0) if self._polls else NO_CODE_YET
        if isinstance(result, Exception):
            raise result
        return result


class FakeTime:
    """A clock that only advances when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


class FakeFactory:
    """Replaces broadlink.hello: hands out the given remotes in order."""

    def __init__(self, *remotes: FakeRemote) -> None:
        self._remotes = list(remotes)
        self.hosts: list[str] = []

    def __call__(self, host: str) -> FakeRemote:
        self.hosts.append(host)
        return self._remotes.pop(0)


def connected_blaster(factory: FakeFactory) -> BroadlinkBlaster:
    """A blaster whose first remote is already discovered and authenticated."""
    blaster = BroadlinkBlaster(HOST, factory=factory)
    blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))
    return blaster


def test_no_connection_is_made_before_the_first_command():
    factory = FakeFactory(FakeRemote())

    BroadlinkBlaster(HOST, factory=factory)

    assert factory.hosts == []


def test_send_decodes_base64_and_sends_the_bytes_to_the_blaster():
    remote = FakeRemote()
    blaster = BroadlinkBlaster(HOST, factory=FakeFactory(remote))

    blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))

    assert remote.sent == [POWER_CODE]


def test_send_connects_and_authenticates_only_once_across_commands():
    remote = FakeRemote()
    factory = FakeFactory(remote)
    blaster = BroadlinkBlaster(HOST, factory=factory)

    blaster.send(base64.b64encode(b"\x26\x00\x01").decode("ascii"))
    blaster.send(base64.b64encode(b"\x26\x00\x02").decode("ascii"))

    assert factory.hosts == [HOST]
    assert remote.auth_calls == 1
    assert len(remote.sent) == 2


def test_default_factory_uses_a_short_timeout_for_discovery_and_later_requests(monkeypatch):
    calls = []
    remote = FakeRemote()

    def fake_hello(ip_address: str, port: int = 80, timeout: int = 10) -> FakeRemote:
        calls.append((ip_address, timeout))
        return remote

    monkeypatch.setattr(broadlink, "hello", fake_hello)
    blaster = BroadlinkBlaster(HOST)

    blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))

    assert calls == [(HOST, 5)]
    assert remote.timeout == 5
    assert remote.sent == [POWER_CODE]


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        OSError("network unreachable"),
        broadlink.exceptions.NetworkTimeoutError(-4000, "Network timeout"),
        broadlink.exceptions.AuthenticationError(-1, "Authentication failed"),
        broadlink.exceptions.AuthorizationError(-4012, "Device control ID error"),
        broadlink.exceptions.ConnectionClosedError(-2, "You have been logged out"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_send_reconnects_once_and_retries_when_the_open_session_drops(error):
    stale = FakeRemote()
    fresh = FakeRemote()
    factory = FakeFactory(stale, fresh)
    blaster = connected_blaster(factory)
    stale.send_error = error

    blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))

    assert factory.hosts == [HOST, HOST]
    assert fresh.auth_calls == 1
    assert fresh.sent == [POWER_CODE]


def test_send_raises_device_error_naming_the_host_when_the_retry_also_fails():
    stale = FakeRemote()
    factory = FakeFactory(stale, FakeRemote(send_error=OSError()))
    blaster = connected_blaster(factory)
    stale.send_error = TimeoutError()

    with pytest.raises(DeviceError, match=HOST):
        blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))

    assert factory.hosts == [HOST, HOST]


def test_send_fails_after_one_discovery_when_the_blaster_never_answers():
    # broadlink.hello already resends its probe every second until it times out,
    # so a second discovery would only double the silence before she answers.
    hosts = []

    def offline_factory(host: str) -> FakeRemote:
        hosts.append(host)
        raise broadlink.exceptions.NetworkTimeoutError(-4000, "Network timeout")

    blaster = BroadlinkBlaster(HOST, factory=offline_factory)

    for _ in range(2):
        with pytest.raises(DeviceError, match=HOST):
            blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))

    assert hosts == [HOST, HOST]


def test_first_command_whose_new_session_fails_is_not_retried():
    factory = FakeFactory(FakeRemote(send_error=TimeoutError()), FakeRemote())
    blaster = BroadlinkBlaster(HOST, factory=factory)

    with pytest.raises(DeviceError, match=HOST):
        blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))

    assert factory.hosts == [HOST]


def test_command_after_a_failed_send_starts_a_fresh_connection():
    recovered = FakeRemote()
    factory = FakeFactory(FakeRemote(send_error=TimeoutError()), recovered)
    blaster = BroadlinkBlaster(HOST, factory=factory)
    with pytest.raises(DeviceError):
        blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))

    blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))

    assert recovered.sent == [POWER_CODE]


def test_device_side_failures_are_not_retried():
    error = broadlink.exceptions.CommandNotSupportedError(-4, "Command not supported")
    factory = FakeFactory(FakeRemote(send_error=error), FakeRemote())
    blaster = BroadlinkBlaster(HOST, factory=factory)

    with pytest.raises(broadlink.exceptions.CommandNotSupportedError):
        blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))

    assert factory.hosts == [HOST]


def test_send_rejects_a_corrupted_code_with_a_device_error():
    factory = FakeFactory(FakeRemote())
    blaster = BroadlinkBlaster(HOST, factory=factory)

    with pytest.raises(DeviceError, match="rusak"):
        blaster.send("bukan base64!!")

    assert factory.hosts == []


def test_learn_enters_learning_mode_and_returns_the_captured_code_as_base64():
    remote = FakeRemote(polls=[NO_CODE_YET, NO_CODE_YET, POWER_CODE])
    fake_time = FakeTime()
    blaster = BroadlinkBlaster(
        HOST, factory=FakeFactory(remote), sleep=fake_time.sleep, clock=fake_time.monotonic
    )

    code = blaster.learn(timeout_s=20.0)

    assert remote.learning
    assert base64.b64decode(code) == POWER_CODE


@pytest.mark.parametrize(
    "no_code_yet",
    [
        broadlink.exceptions.ReadError(-10, "Read error"),
        broadlink.exceptions.StorageError(-5, "The device storage is full"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_learn_keeps_polling_about_every_half_second_while_no_code_is_captured(no_code_yet):
    remote = FakeRemote(polls=[no_code_yet, no_code_yet, POWER_CODE])
    fake_time = FakeTime()
    blaster = BroadlinkBlaster(
        HOST, factory=FakeFactory(remote), sleep=fake_time.sleep, clock=fake_time.monotonic
    )

    blaster.learn()

    assert fake_time.sleeps == [0.5, 0.5, 0.5]


def test_learn_raises_device_error_when_no_code_arrives_before_the_timeout():
    fake_time = FakeTime()
    blaster = BroadlinkBlaster(
        HOST, factory=FakeFactory(FakeRemote()), sleep=fake_time.sleep, clock=fake_time.monotonic
    )

    with pytest.raises(DeviceError, match="3 detik"):
        blaster.learn(timeout_s=3.0)

    assert fake_time.sleeps == [0.5] * 6


def test_learn_reconnects_once_when_entering_learning_mode_hits_a_stale_session():
    stale = FakeRemote(learn_error=broadlink.exceptions.AuthorizationError(-4012, "ID error"))
    fresh = FakeRemote(polls=[POWER_CODE])
    fake_time = FakeTime()
    blaster = BroadlinkBlaster(
        HOST, factory=FakeFactory(stale, fresh), sleep=fake_time.sleep, clock=fake_time.monotonic
    )
    blaster.send(base64.b64encode(POWER_CODE).decode("ascii"))

    code = blaster.learn()

    assert fresh.learning
    assert base64.b64decode(code) == POWER_CODE
