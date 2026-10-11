import logging
import socket

import pytest

from waifu.devices.base import Device, DeviceError, DeviceResult
from waifu.devices.ir_codes import IrCodeStore
from waifu.devices.ir_device import IrDevice
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry


class FixedResultDevice(Device):
    def __init__(self, result: DeviceResult) -> None:
        super().__init__("tv", "TV")
        self.result = result

    def commands(self) -> list[str]:
        return ["power"]

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        return self.result


class RaisingBlaster:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def send(self, code_b64: str) -> None:
        raise self.error

    def learn(self, timeout_s: float) -> str:
        raise self.error


def ir_device_failing_with(tmp_path, error: BaseException) -> IrDevice:
    store = IrCodeStore(tmp_path / "ir_codes.json")
    store.put("ac", "on", "T04=")
    return IrDevice("ac", "AC Samsung", ["ac"], RaisingBlaster(error), store)


def test_duplicate_device_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate device id 'tv'"):
        DeviceRegistry([MockDevice("tv", "TV"), MockDevice("tv", "TV kedua")])


def test_get_finds_a_device_by_id_or_returns_none():
    lamp = MockDevice("lampu_kamar", "Lampu kamar")
    registry = DeviceRegistry([lamp])

    assert registry.get("lampu_kamar") is lamp
    assert registry.get("kulkas") is None


def test_all_returns_devices_in_registration_order():
    lamp, tv = MockDevice("lampu_kamar", "Lampu kamar"), MockDevice("tv", "TV")
    registry = DeviceRegistry([lamp, tv])

    assert registry.all() == [lamp, tv]


def test_describe_all_returns_each_device_description():
    lamp, tv = MockDevice("lampu_kamar", "Lampu kamar"), MockDevice("tv", "TV")
    lamp.execute("on")
    registry = DeviceRegistry([lamp, tv])

    assert registry.describe_all() == [lamp.describe(), tv.describe()]


def test_execute_runs_the_command_on_the_device():
    lamp = MockDevice("lampu_kamar", "Lampu kamar")
    registry = DeviceRegistry([lamp])

    result = registry.execute("lampu_kamar", "on")

    assert result.ok
    assert lamp.calls == [("on", None)]


def test_execute_passes_the_device_result_through_unchanged():
    device_result = DeviceResult(ok=False, message="TV sedang update.", state={"power": "on"})
    registry = DeviceRegistry([FixedResultDevice(device_result)])

    assert registry.execute("tv", "power") is device_result


def test_execute_lets_the_device_resolve_command_and_value():
    ac = MockDevice("ac", "AC Samsung", commands=["on", "off", "temp_24"], kind="ir")
    registry = DeviceRegistry([ac])

    result = registry.execute("ac", "temp", 24)

    assert result.ok
    assert ac.calls == [("temp_24", 24)]


def test_unknown_device_is_a_failure_listing_known_ids():
    registry = DeviceRegistry([MockDevice("lampu_kamar", "Lampu kamar"), MockDevice("tv", "TV")])

    result = registry.execute("kulkas", "on")

    assert not result.ok
    assert "kulkas" in result.message
    assert "lampu_kamar, tv" in result.message


def test_unknown_command_is_a_failure_listing_valid_commands_without_running_it():
    tv = MockDevice("tv", "TV", commands=["power", "volume_up"])
    registry = DeviceRegistry([tv])

    result = registry.execute("tv", "explode")

    assert not result.ok
    assert "explode" in result.message
    assert "power, volume_up" in result.message
    assert tv.calls == []


def test_unknown_combined_command_is_a_failure():
    ac = MockDevice("ac", "AC Samsung", commands=["temp_24"], kind="ir")
    registry = DeviceRegistry([ac])

    result = registry.execute("ac", "temp", 18)

    assert not result.ok
    assert "temp_24" in result.message
    assert ac.calls == []


def test_device_error_becomes_a_failure_carrying_its_message(tmp_path):
    error = DeviceError("Blaster IR tidak menjawab.")
    registry = DeviceRegistry([ir_device_failing_with(tmp_path, error)])

    result = registry.execute("ac", "on")

    assert result == DeviceResult(ok=False, message="Blaster IR tidak menjawab.")


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        ConnectionRefusedError(10061, "refused"),
        socket.gaierror(11001, "getaddrinfo failed"),
    ],
)
def test_timeouts_and_socket_errors_become_unreachable_failures(tmp_path, caplog, error):
    registry = DeviceRegistry([ir_device_failing_with(tmp_path, error)])

    with caplog.at_level(logging.ERROR, logger="waifu.devices.registry"):
        result = registry.execute("ac", "on")

    assert not result.ok
    assert "AC Samsung tidak bisa dihubungi" in result.message
    assert any(record.exc_info and record.exc_info[1] is error for record in caplog.records)


def test_unexpected_exception_becomes_a_generic_failure_logged_with_traceback(tmp_path, caplog):
    error = RuntimeError("internal detail 0xdeadbeef")
    registry = DeviceRegistry([ir_device_failing_with(tmp_path, error)])

    with caplog.at_level(logging.ERROR, logger="waifu.devices.registry"):
        result = registry.execute("ac", "on")

    assert not result.ok
    assert "AC Samsung" in result.message
    assert "0xdeadbeef" not in result.message
    assert any(record.exc_info and record.exc_info[1] is error for record in caplog.records)


def test_failure_while_listing_commands_is_also_contained(tmp_path):
    path = tmp_path / "ir_codes.json"
    path.write_text("{corrupt", encoding="utf-8")
    blaster = RaisingBlaster(AssertionError("blaster must not be reached"))
    registry = DeviceRegistry([IrDevice("ac", "AC Samsung", [], blaster, IrCodeStore(path))])

    result = registry.execute("ac", "on")

    assert not result.ok
