import pytest

from waifu.devices.base import DeviceError
from waifu.devices.ir_codes import IrCodeStore
from waifu.devices.ir_device import IrDevice, resolve_command


class FakeBlaster:
    def __init__(self, learned_code: str = "TEVBUk5FRA==") -> None:
        self.sent: list[str] = []
        self.learn_timeouts: list[float] = []
        self._learned_code = learned_code

    def send(self, code_b64: str) -> None:
        self.sent.append(code_b64)

    def learn(self, timeout_s: float) -> str:
        self.learn_timeouts.append(timeout_s)
        return self._learned_code


def make_ac(tmp_path, codes=None, declared=None, blaster=None):
    store = IrCodeStore(tmp_path / "ir_codes.json")
    for command, code in (codes or {}).items():
        store.put("ac", command, code)
    blaster = blaster or FakeBlaster()
    device = IrDevice("ac", "AC Samsung", ["ac"], blaster, store, declared_commands=declared)
    return device, blaster, store


def test_ir_device_kind_is_ir(tmp_path):
    device, _, _ = make_ac(tmp_path)

    assert device.kind == "ir"
    assert device.describe()["kind"] == "ir"


def test_commands_are_the_learned_codes_for_this_device_only(tmp_path):
    device, _, store = make_ac(tmp_path, {"on": "T04=", "off": "T0ZG"})
    store.put("tv", "power", "UFdS")

    assert device.commands() == ["off", "on"]


def test_execute_sends_the_learned_code_and_reports_success(tmp_path):
    device, blaster, _ = make_ac(tmp_path, {"on": "T04="})

    result = device.execute("on")

    assert blaster.sent == ["T04="]
    assert result.ok
    assert "AC Samsung" in result.message


def test_execute_maps_command_and_value_to_the_learned_combined_command(tmp_path):
    device, blaster, _ = make_ac(tmp_path, {"temp_24": "VDI0", "temp_25": "VDI1"})

    device.execute("temp", 24)

    assert blaster.sent == ["VDI0"]
    assert device.state["last_command"] == "temp_24"


def test_execute_falls_back_to_the_plain_command_when_the_combination_is_not_learned(tmp_path):
    device, blaster, _ = make_ac(tmp_path, {"on": "T04="})

    device.execute("on", 1)

    assert blaster.sent == ["T04="]


def test_execute_tracks_the_last_command_in_state(tmp_path):
    device, _, _ = make_ac(tmp_path, {"on": "T04=", "off": "T0ZG"})

    device.execute("on")
    result = device.execute("off")

    assert device.state["last_command"] == "off"
    assert result.state["last_command"] == "off"


def test_unknown_command_raises_device_error_listing_learned_commands(tmp_path):
    device, blaster, _ = make_ac(tmp_path, {"on": "T04=", "off": "T0ZG", "temp_24": "VDI0"})

    with pytest.raises(DeviceError) as excinfo:
        device.execute("temp", 18)

    message = str(excinfo.value)
    assert "temp_18" in message
    assert "off, on, temp_24" in message
    assert blaster.sent == []


def test_unknown_command_with_nothing_learned_explains_how_to_learn(tmp_path):
    device, _, _ = make_ac(tmp_path)

    with pytest.raises(DeviceError, match="waifu learn-ir ac"):
        device.execute("on")


def test_missing_commands_are_declared_but_unlearned_in_declared_order(tmp_path):
    declared = ["on", "off", "temp_16", "temp_17"]
    device, _, _ = make_ac(tmp_path, {"off": "T0ZG", "temp_16": "VDE2"}, declared=declared)

    assert device.missing_commands() == ["on", "temp_17"]


def test_missing_commands_is_empty_without_declared_commands(tmp_path):
    device, _, _ = make_ac(tmp_path, {"on": "T04="})

    assert device.missing_commands() == []


def test_learn_stores_the_code_captured_by_the_blaster(tmp_path):
    blaster = FakeBlaster(learned_code="TkVXQ09ERQ==")
    device, _, store = make_ac(tmp_path, blaster=blaster)

    device.learn("temp_24", timeout_s=5.0)

    assert blaster.learn_timeouts == [5.0]
    assert store.get("ac", "temp_24") == "TkVXQ09ERQ=="
    assert device.commands() == ["temp_24"]


def test_learn_waits_twenty_seconds_by_default(tmp_path):
    device, blaster, _ = make_ac(tmp_path)

    device.learn("on")

    assert blaster.learn_timeouts == [20.0]


def test_learn_rejects_an_invalid_name_before_asking_the_blaster(tmp_path):
    device, blaster, store = make_ac(tmp_path)

    with pytest.raises(ValueError, match="command"):
        device.learn("Temp 24")

    assert blaster.learn_timeouts == []
    assert store.commands("ac") == []


@pytest.mark.parametrize(
    ("command", "value", "available", "expected"),
    [
        ("temp", 24, ["temp_24"], "temp_24"),
        ("on", None, ["on", "off"], "on"),
        ("brightness", 50, ["brightness"], "brightness"),
        ("temp", 18, ["temp_24"], None),
        ("power", None, ["on"], None),
    ],
)
def test_resolve_command_prefers_the_combined_name_then_the_plain_one(
    command, value, available, expected
):
    assert resolve_command(command, value, available) == expected
