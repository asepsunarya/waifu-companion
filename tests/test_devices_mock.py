import pytest

from waifu.devices.base import DeviceError
from waifu.devices.mock import MockDevice


def test_mock_device_accepts_on_and_off_by_default():
    lamp = MockDevice("lampu_kamar", "Lampu kamar")

    assert lamp.commands() == ["on", "off"]


def test_mock_device_describes_its_identity_and_commands():
    lamp = MockDevice("lampu_kamar", "Lampu kamar", aliases=["lampu"], commands=["on", "toggle"])

    description = lamp.describe()

    assert description["id"] == "lampu_kamar"
    assert description["name"] == "Lampu kamar"
    assert description["aliases"] == ["lampu"]
    assert description["commands"] == ["on", "toggle"]
    assert description["kind"] == "mock"


def test_mock_device_reports_the_kind_it_stands_in_for():
    ac = MockDevice("ac", "AC Samsung", kind="ir")

    assert ac.kind == "ir"
    assert ac.describe()["kind"] == "ir"
    assert MockDevice.kind == "mock"


def test_execute_records_the_call_and_returns_an_ok_result():
    lamp = MockDevice("lampu_kamar", "Lampu kamar", commands=["on", "off", "brightness"])

    result = lamp.execute("brightness", 40)

    assert lamp.calls == [("brightness", 40)]
    assert result.ok
    assert "Lampu kamar" in result.message


def test_on_and_off_update_the_power_state():
    lamp = MockDevice("lampu_kamar", "Lampu kamar")

    lamp.execute("on")
    assert lamp.state["power"] == "on"

    result = lamp.execute("off")
    assert lamp.state["power"] == "off"
    assert result.state["power"] == "off"


def test_execute_tracks_the_last_command():
    tv = MockDevice("tv", "TV", commands=["power", "volume_up"])

    tv.execute("volume_up")

    assert tv.state["last_command"] == "volume_up"
    assert "power" not in tv.state


def test_value_resolves_to_a_listed_combined_command_like_an_ir_device():
    ac = MockDevice("ac", "AC Samsung", commands=["on", "off", "temp_24"], kind="ir")

    ac.execute("temp", 24)

    assert ac.calls == [("temp_24", 24)]
    assert ac.state["last_command"] == "temp_24"


def test_unknown_command_raises_device_error_listing_valid_commands():
    lamp = MockDevice("lampu_kamar", "Lampu kamar")

    with pytest.raises(DeviceError, match="on, off"):
        lamp.execute("explode")

    assert lamp.calls == []
