import importlib
import logging
import sys

import pytest
import tinytuya
from tinytuya.core.Device import Device as TuyaCoreDevice
from tinytuya.core.XenonDevice import XenonDevice

from waifu.config import TuyaConnection
from waifu.devices.base import DeviceError
from waifu.devices.tuya import (
    LOOKUP_RETRY_AFTER_S,
    LanAddress,
    TuyaIrBlaster,
    TuyaLight,
    TuyaPlug,
)

LOCAL_KEY = "k3y-not-for-logs"
CONN = TuyaConnection(
    device_id="bf0123456789abcdef", local_key=LOCAL_KEY, address="192.168.1.60", version=3.3
)
OFFLINE = {"Error": "Network Error: Device Unreachable", "Err": "905", "Payload": None}


class RecordingFactory:
    """Stands in for a tinytuya class: records constructor kwargs, returns one fake device."""

    def __init__(self, device):
        self.device = device
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.device


class FakeBulb:
    """Mimics tinytuya.BulbDevice: commands return a response dict, or an error dict.

    Like tinytuya, it learns its bulb type (A, B, or C) on first use, and a brightness
    percentage that maps below that type's minimum turns the bulb off instead.
    """

    def __init__(self, *, is_on=False, reply=None, state_reply=None, bulb_type="B"):
        self.is_on = is_on
        self.reply = reply
        self.state_reply = state_reply
        self.calls: list[tuple] = []
        self._bulb_type = bulb_type
        self.bulb_configured = False
        self.dpset = {"value_min": -1, "value_max": -1}

    def detect_bulb(self):
        self.calls.append(("detect_bulb",))
        if self._bulb_type is not None:
            self.dpset = dict(tinytuya.BulbDevice.DEFAULT_DPSET[self._bulb_type])
            self.bulb_configured = True

    def turn_on(self):
        self.calls.append(("turn_on",))
        return self.reply

    def turn_off(self):
        self.calls.append(("turn_off",))
        return self.reply

    def set_brightness_percentage(self, brightness):
        if not self.bulb_configured:
            self.detect_bulb()
            if not self.bulb_configured:
                raise RuntimeError("set_brightness_percentage: Bulb not configured")
        if int(self.dpset["value_max"] * brightness // 100) < self.dpset["value_min"]:
            return self.turn_off()
        self.calls.append(("set_brightness_percentage", brightness))
        return self.reply

    def state(self):
        self.calls.append(("state",))
        if self.state_reply is not None:
            return self.state_reply
        return {"switch": self.is_on, "is_on": self.is_on, "mode": "white", "brightness": 255}


class FakeOutlet:
    """Mimics tinytuya.OutletDevice: power lives in data point "1" of status()."""

    def __init__(self, *, status=None, reply=None):
        self._status = status
        self.reply = reply
        self.calls: list[tuple] = []

    def turn_on(self):
        self.calls.append(("turn_on",))
        return self.reply

    def turn_off(self):
        self.calls.append(("turn_off",))
        return self.reply

    def status(self):
        self.calls.append(("status",))
        return self._status


class FakeRemote:
    """Mimics tinytuya's IRRemoteControlDevice after control-type detection."""

    def __init__(self, *, control_type=2, received=None):
        self.control_type = control_type
        self.received = received
        self.calls: list[tuple] = []

    def send_button(self, base64_code):
        self.calls.append(("send_button", base64_code))

    def receive_button(self, timeout=30):
        self.calls.append(("receive_button", timeout))
        return self.received


def make_light(bulb, factory=None):
    return TuyaLight(
        "lampu_kamar", "Lampu kamar", ["lampu"], CONN, factory=factory or RecordingFactory(bulb)
    )


def make_plug(outlet):
    return TuyaPlug("colokan", "Colokan meja", [], CONN, factory=RecordingFactory(outlet))


# --- TuyaLight -------------------------------------------------------------------------


def test_light_offers_power_toggle_and_brightness_commands():
    light = make_light(FakeBulb())
    assert light.kind == "light"
    assert light.commands() == ["on", "off", "toggle", "brightness"]


def test_light_on_turns_the_bulb_on_and_records_power():
    bulb = FakeBulb()
    light = make_light(bulb)

    result = light.execute("on")

    assert result.ok
    assert bulb.calls == [("turn_on",)]
    assert light.state["power"] is True
    assert result.state["power"] is True
    assert "Lampu kamar" in result.message


def test_light_off_turns_the_bulb_off_and_records_power():
    bulb = FakeBulb()
    light = make_light(bulb)

    light.execute("off")

    assert bulb.calls == [("turn_off",)]
    assert light.state["power"] is False


def test_light_toggle_asks_the_bulb_because_it_may_have_been_switched_elsewhere():
    bulb = FakeBulb()
    light = make_light(bulb)
    light.execute("on")
    bulb.is_on = False  # the owner switched it off in the Smart Life app

    result = light.execute("toggle")

    assert bulb.calls == [("turn_on",), ("state",), ("turn_on",)]
    assert light.state["power"] is True
    assert result.message == "Lampu kamar dinyalakan."


def test_light_toggle_uses_the_last_known_power_when_the_bulb_does_not_report_it():
    bulb = FakeBulb(state_reply={"switch": None, "is_on": None, "mode": None})
    light = make_light(bulb)
    light.execute("on")

    light.execute("toggle")

    assert bulb.calls == [("turn_on",), ("state",), ("turn_off",)]
    assert light.state["power"] is False


def test_light_toggle_of_an_unreachable_bulb_fails_instead_of_guessing_from_memory():
    bulb = FakeBulb()
    light = make_light(bulb)
    light.execute("on")
    bulb.state_reply = OFFLINE

    with pytest.raises(DeviceError, match="tidak bisa dihubungi"):
        light.execute("toggle")

    assert bulb.calls == [("turn_on",), ("state",)]


def test_light_toggle_without_any_known_power_fails_clearly():
    bulb = FakeBulb(state_reply={"switch": None, "is_on": None, "mode": None})
    light = make_light(bulb)

    with pytest.raises(DeviceError, match="Status Lampu kamar tidak terbaca"):
        light.execute("toggle")

    assert bulb.calls == [("state",)]


def test_light_toggle_queries_the_bulb_when_power_is_unknown():
    bulb = FakeBulb(is_on=True)
    light = make_light(bulb)

    light.execute("toggle")

    assert bulb.calls == [("state",), ("turn_off",)]
    assert light.state["power"] is False


def test_light_brightness_sets_the_percentage_and_records_state():
    bulb = FakeBulb()
    light = make_light(bulb)

    result = light.execute("brightness", 40)

    assert result.ok
    assert bulb.calls == [("detect_bulb",), ("set_brightness_percentage", 40)]
    assert light.state == {"power": True, "brightness": 40}
    assert result.message == "Kecerahan Lampu kamar diatur ke 40%."


@pytest.mark.parametrize("bulb_type", ["A", "C"])
@pytest.mark.parametrize("requested", [1, 5, 9])
def test_light_brightness_below_the_bulbs_minimum_dims_it_instead_of_turning_it_off(
    bulb_type, requested
):
    # Type A/C bulbs take 25-255: tinytuya turns them off for 1-9 %, which map below 25.
    bulb = FakeBulb(bulb_type=bulb_type)
    light = make_light(bulb)

    result = light.execute("brightness", requested)

    assert bulb.calls == [("detect_bulb",), ("set_brightness_percentage", 10)]
    assert light.state == {"power": True, "brightness": 10}
    assert result.message == "Kecerahan Lampu kamar diatur ke 10%, paling redup yang bisa."


def test_light_brightness_down_to_one_percent_is_kept_on_a_bulb_that_supports_it():
    bulb = FakeBulb(bulb_type="B")  # type B takes 10-1000, so 1 % maps to its minimum
    light = make_light(bulb)

    result = light.execute("brightness", 1)

    assert bulb.calls == [("detect_bulb",), ("set_brightness_percentage", 1)]
    assert result.message == "Kecerahan Lampu kamar diatur ke 1%."


def test_light_brightness_on_a_bulb_of_unknown_type_sends_nothing():
    bulb = FakeBulb(bulb_type=None)
    light = make_light(bulb)

    with pytest.raises(DeviceError, match="Lampu kamar"):
        light.execute("brightness", 50)

    assert bulb.calls == [("detect_bulb",)]
    assert light.state == {}


def test_light_brightness_below_the_minimum_keeps_a_real_tinytuya_bulb_on(monkeypatch):
    # Only the LAN round trips are replaced; detection and value mapping are tinytuya's own.
    type_a_status = {"devId": CONN.device_id, "dps": {"1": True, "2": "white", "3": 255, "4": 0}}
    sent: list[tuple] = []
    monkeypatch.setattr(XenonDevice, "status", lambda self, nowait=False: type_a_status)
    monkeypatch.setattr(
        TuyaCoreDevice,
        "set_status",
        lambda self, on, switch=1, nowait=False: sent.append(("set_status", on, switch)),
    )
    monkeypatch.setattr(
        TuyaCoreDevice,
        "set_multiple_values",
        lambda self, data, nowait=False: sent.append(("set_multiple_values", data)),
    )
    light = make_light(None, factory=tinytuya.BulbDevice)

    result = light.execute("brightness", 5)

    assert sent == [("set_multiple_values", {"3": 25})]
    assert result.state == {"power": True, "brightness": 10}


@pytest.mark.parametrize("value", [None, 0, 101, -5])
def test_light_brightness_rejects_a_missing_or_out_of_range_value(value):
    bulb = FakeBulb()
    light = make_light(bulb)

    with pytest.raises(DeviceError, match="1 sampai 100"):
        light.execute("brightness", value)

    assert bulb.calls == []
    assert light.state == {}


def test_light_unknown_command_lists_the_available_ones():
    light = make_light(FakeBulb())

    with pytest.raises(DeviceError, match="on, off, toggle, brightness"):
        light.execute("blink")


# --- Shared connection behaviour ---------------------------------------------------------


def test_connection_opens_lazily_once_with_a_short_timeout():
    factory = RecordingFactory(FakeBulb())
    light = make_light(None, factory=factory)
    assert factory.calls == []

    light.execute("on")
    light.execute("off")

    assert len(factory.calls) == 1
    kwargs = factory.calls[0]
    assert kwargs["dev_id"] == CONN.device_id
    assert kwargs["address"] == CONN.address
    assert kwargs["local_key"] == LOCAL_KEY
    assert kwargs["version"] == CONN.version
    assert kwargs["connection_timeout"] <= 5
    assert kwargs["connection_retry_limit"] <= 2


def test_failed_connection_is_reported_and_retried_on_the_next_command():
    bulb = FakeBulb()
    attempts = []

    def flaky_factory(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise RuntimeError("Unable to find device on network (specify IP address)")
        return bulb

    light = make_light(None, factory=flaky_factory)

    with pytest.raises(DeviceError, match="tidak bisa dihubungi"):
        light.execute("on")
    light.execute("on")

    assert len(attempts) == 2
    assert bulb.calls == [("turn_on",)]


@pytest.mark.parametrize(
    ("err", "hint"),
    [("905", "tidak bisa dihubungi"), ("914", "local_key"), ("907", "tidak mendukung")],
)
def test_tuya_error_payload_becomes_an_indonesian_device_error(err, hint):
    bulb = FakeBulb(reply={"Error": "tinytuya says no", "Err": err, "Payload": None})
    light = make_light(bulb)

    with pytest.raises(DeviceError, match=hint):
        light.execute("on")

    assert "power" not in light.state


def test_vendor_runtime_error_becomes_a_device_error():
    class UnconfiguredBulb(FakeBulb):
        def set_brightness_percentage(self, brightness):
            raise RuntimeError("set_brightness_percentage: Bulb not configured")

    light = make_light(UnconfiguredBulb())

    with pytest.raises(DeviceError, match="Lampu kamar"):
        light.execute("brightness", 50)

    assert light.state == {}


def test_failures_never_reveal_the_local_key(caplog):
    caplog.set_level(logging.DEBUG)
    light = make_light(FakeBulb(reply={**OFFLINE, "Payload": LOCAL_KEY}))

    with pytest.raises(DeviceError) as excinfo:
        light.execute("on")

    assert LOCAL_KEY not in str(excinfo.value)
    assert all(LOCAL_KEY not in record.getMessage() for record in caplog.records)


def test_light_default_factory_builds_a_tinytuya_bulb(monkeypatch):
    factory = RecordingFactory(FakeBulb())
    monkeypatch.setattr(tinytuya, "BulbDevice", factory)
    light = TuyaLight("lampu_kamar", "Lampu kamar", [], CONN)

    light.execute("on")

    assert factory.calls[0]["dev_id"] == CONN.device_id
    assert factory.device.calls == [("turn_on",)]


# --- "Auto" address: a short LAN lookup instead of tinytuya's 18 s scan per command -------


AUTO_CONN = TuyaConnection(device_id="bf0123456789abcdef", local_key=LOCAL_KEY, version=3.3)
FOUND = LanAddress("192.168.1.77", 3.4)


class FakeLan:
    """Stands in for the LAN lookup: answers each lookup in turn, repeating the last answer."""

    def __init__(self, *answers):
        self._answers = list(answers)
        self.lookups: list[tuple[str, float]] = []

    def __call__(self, device_id, scan_s):
        self.lookups.append((device_id, scan_s))
        return self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_auto_light(lan, factory, clock=None):
    return TuyaLight(
        "lampu_kamar",
        "Lampu kamar",
        [],
        AUTO_CONN,
        factory=factory,
        locate=lan,
        clock=clock or FakeClock(),
    )


def test_auto_address_is_looked_up_once_with_a_short_scan_and_the_found_ip_is_used():
    lan = FakeLan(FOUND)
    factory = RecordingFactory(FakeBulb())
    light = make_auto_light(lan, factory)

    light.execute("on")
    light.execute("off")

    assert [device_id for device_id, _ in lan.lookups] == [AUTO_CONN.device_id]
    assert lan.lookups[0][1] <= 6  # tinytuya's own "Auto" scan waits 18 s for a missing device
    assert [(kw["address"], kw["version"]) for kw in factory.calls] == [("192.168.1.77", 3.4)]


def test_auto_address_found_without_a_version_uses_the_configured_one():
    factory = RecordingFactory(FakeBulb())
    light = make_auto_light(FakeLan(LanAddress("192.168.1.77", None)), factory)

    light.execute("on")

    assert factory.calls[0]["version"] == AUTO_CONN.version


def test_auto_device_not_found_fails_fast_until_the_retry_window_passes():
    lan = FakeLan(None, FOUND)
    clock = FakeClock()
    bulb = FakeBulb()
    light = make_auto_light(lan, RecordingFactory(bulb), clock)

    with pytest.raises(DeviceError, match="tidak bisa dihubungi"):
        light.execute("on")
    clock.now += LOOKUP_RETRY_AFTER_S - 1
    with pytest.raises(DeviceError, match="tidak bisa dihubungi"):
        light.execute("on")
    assert len(lan.lookups) == 1

    clock.now += 1
    assert light.execute("on").ok

    assert len(lan.lookups) == 2
    assert bulb.calls == [("turn_on",)]


def test_auto_device_that_stops_answering_is_looked_up_again_on_the_next_command():
    lan = FakeLan(FOUND, LanAddress("192.168.1.78", 3.4))
    bulbs = [FakeBulb(reply=OFFLINE), FakeBulb()]
    opened = []

    def factory(**kwargs):
        opened.append(kwargs["address"])
        return bulbs[len(opened) - 1]

    light = make_auto_light(lan, factory)

    with pytest.raises(DeviceError, match="tidak bisa dihubungi"):
        light.execute("on")
    light.execute("on")

    assert opened == ["192.168.1.77", "192.168.1.78"]
    assert bulbs[1].calls == [("turn_on",)]


def test_auto_device_that_rejects_the_key_is_not_looked_up_again():
    lan = FakeLan(FOUND)
    bulb = FakeBulb(reply={"Error": "Check device key or version", "Err": "914"})
    light = make_auto_light(lan, RecordingFactory(bulb))

    for _ in range(2):
        with pytest.raises(DeviceError, match="local_key"):
            light.execute("on")

    assert len(lan.lookups) == 1


def test_explicit_ip_never_scans_the_lan():
    def no_scan(device_id, scan_s):
        raise AssertionError("scanned the LAN although the IP is configured")

    factory = RecordingFactory(FakeBulb())
    light = TuyaLight("lampu_kamar", "Lampu kamar", [], CONN, factory=factory, locate=no_scan)

    light.execute("on")

    assert factory.calls[0]["address"] == CONN.address


class FakeScanner:
    """Stands in for the tinytuya.scanner module, so its import-time colorama setup never runs."""

    def __init__(self, found=None, error=None):
        self.found = found or {}
        self.error = error
        self.calls: list[dict] = []

    def devices(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.found


@pytest.fixture
def fake_scanner(monkeypatch):
    def install(**kwargs):
        scanner = FakeScanner(**kwargs)
        monkeypatch.setitem(sys.modules, "tinytuya.scanner", scanner)
        monkeypatch.setattr(tinytuya, "scanner", scanner, raising=False)
        return scanner

    return install


def test_default_lookup_listens_only_for_this_device_for_a_few_seconds(fake_scanner):
    found = {AUTO_CONN.device_id: {"ip": "192.168.1.77", "version": "3.4", "gwId": "x"}}
    scanner = fake_scanner(found=found)
    factory = RecordingFactory(FakeBulb())
    light = TuyaLight("lampu_kamar", "Lampu kamar", [], AUTO_CONN, factory=factory)

    light.execute("on")

    [call] = scanner.calls
    assert call["wantids"] == [AUTO_CONN.device_id]
    assert call["scantime"] <= 6
    assert call["byID"] is True
    assert call["poll"] is False
    assert call["verbose"] is False
    assert (factory.calls[0]["address"], factory.calls[0]["version"]) == ("192.168.1.77", 3.4)


@pytest.mark.parametrize(
    "scanner_kwargs",
    [{"found": {}}, {"error": OSError("discovery port already in use")}],
    ids=["not found", "cannot listen"],
)
def test_default_lookup_that_finds_nothing_reports_the_device_unreachable(
    fake_scanner, scanner_kwargs
):
    fake_scanner(**scanner_kwargs)
    factory = RecordingFactory(FakeBulb())
    light = TuyaLight("lampu_kamar", "Lampu kamar", [], AUTO_CONN, factory=factory)

    with pytest.raises(DeviceError, match="tidak bisa dihubungi"):
        light.execute("on")

    assert factory.calls == []


# --- TuyaPlug --------------------------------------------------------------------------


def test_plug_offers_power_and_toggle_only():
    plug = make_plug(FakeOutlet())
    assert plug.kind == "plug"
    assert plug.commands() == ["on", "off", "toggle"]
    with pytest.raises(DeviceError, match="on, off, toggle"):
        plug.execute("brightness", 50)


def test_plug_on_and_off_switch_the_outlet_and_record_power():
    outlet = FakeOutlet()
    plug = make_plug(outlet)

    plug.execute("on")
    assert plug.state["power"] is True
    plug.execute("off")

    assert outlet.calls == [("turn_on",), ("turn_off",)]
    assert plug.state["power"] is False


def test_plug_toggle_reads_the_switch_data_point_when_power_is_unknown():
    outlet = FakeOutlet(status={"devId": CONN.device_id, "dps": {"1": False, "9": 0}})
    plug = make_plug(outlet)

    plug.execute("toggle")

    assert outlet.calls == [("status",), ("turn_on",)]
    assert plug.state["power"] is True


def test_plug_toggle_reads_the_outlet_even_after_switching_it_itself():
    outlet = FakeOutlet(status={"devId": CONN.device_id, "dps": {"1": True}})
    plug = make_plug(outlet)
    plug.execute("off")
    outlet._status = {"devId": CONN.device_id, "dps": {"1": True}}  # switched on by hand

    plug.execute("toggle")

    assert outlet.calls == [("turn_off",), ("status",), ("turn_off",)]
    assert plug.state["power"] is False


def test_plug_toggle_fails_clearly_when_the_status_has_no_power_value():
    outlet = FakeOutlet(status=None)
    plug = make_plug(outlet)

    with pytest.raises(DeviceError, match="Colokan meja"):
        plug.execute("toggle")

    assert outlet.calls == [("status",)]


def test_plug_status_error_payload_becomes_a_device_error():
    plug = make_plug(FakeOutlet(status=OFFLINE))

    with pytest.raises(DeviceError, match="tidak bisa dihubungi"):
        plug.execute("toggle")


def test_plug_default_factory_builds_a_tinytuya_outlet(monkeypatch):
    factory = RecordingFactory(FakeOutlet())
    monkeypatch.setattr(tinytuya, "OutletDevice", factory)
    plug = TuyaPlug("colokan", "Colokan meja", [], CONN)

    plug.execute("off")

    assert factory.calls[0]["local_key"] == LOCAL_KEY
    assert factory.device.calls == [("turn_off",)]


# --- TuyaIrBlaster ---------------------------------------------------------------------


def test_ir_send_presses_the_learned_base64_button():
    remote = FakeRemote()
    factory = RecordingFactory(remote)
    blaster = TuyaIrBlaster(CONN, factory=factory)
    assert factory.calls == []

    blaster.send("AAEC")
    blaster.send("AwQF")

    assert remote.calls == [("send_button", "AAEC"), ("send_button", "AwQF")]
    assert len(factory.calls) == 1
    assert factory.calls[0]["connection_timeout"] <= 5


def test_ir_learn_returns_the_received_code_and_passes_the_timeout():
    remote = FakeRemote(received="JgBQAAAB")
    blaster = TuyaIrBlaster(CONN, factory=RecordingFactory(remote))

    assert blaster.learn(15) == "JgBQAAAB"
    assert remote.calls == [("receive_button", 15)]


@pytest.mark.parametrize("received", [None, {"dps": {"9": 0}}, ""])
def test_ir_learn_without_a_signal_raises_device_error(received):
    blaster = TuyaIrBlaster(CONN, factory=RecordingFactory(FakeRemote(received=received)))

    with pytest.raises(DeviceError, match="Tidak ada sinyal remote"):
        blaster.learn(5)


def test_ir_learn_error_payload_reports_the_blaster_unreachable():
    blaster = TuyaIrBlaster(CONN, factory=RecordingFactory(FakeRemote(received=OFFLINE)))

    with pytest.raises(DeviceError, match="tidak bisa dihubungi"):
        blaster.learn(5)


def test_ir_blaster_that_was_not_detected_is_reported_and_reopened_next_time():
    remotes = [FakeRemote(control_type=0), FakeRemote(control_type=1)]
    opened = []

    def factory(**kwargs):
        opened.append(kwargs)
        return remotes[len(opened) - 1]

    blaster = TuyaIrBlaster(CONN, factory=factory)

    with pytest.raises(DeviceError, match="IR blaster"):
        blaster.send("AAEC")
    blaster.send("AAEC")

    assert len(opened) == 2
    assert remotes[0].calls == []
    assert remotes[1].calls == [("send_button", "AAEC")]


def test_ir_blaster_found_by_lookup_that_does_not_answer_is_looked_up_again():
    lan = FakeLan(FOUND, LanAddress("192.168.1.78", 3.4))
    remotes = [FakeRemote(control_type=0), FakeRemote(control_type=1)]
    opened = []

    def factory(**kwargs):
        opened.append(kwargs["address"])
        return remotes[len(opened) - 1]

    blaster = TuyaIrBlaster(AUTO_CONN, factory=factory, locate=lan, clock=FakeClock())

    with pytest.raises(DeviceError, match="IR blaster"):
        blaster.send("AAEC")
    blaster.send("AAEC")

    assert opened == ["192.168.1.77", "192.168.1.78"]
    assert remotes[1].calls == [("send_button", "AAEC")]


def test_ir_default_factory_builds_the_contrib_ir_remote(monkeypatch):
    factory = RecordingFactory(FakeRemote())
    module = importlib.import_module("tinytuya.Contrib.IRRemoteControlDevice")
    monkeypatch.setattr(module, "IRRemoteControlDevice", factory)
    blaster = TuyaIrBlaster(CONN)

    blaster.send("AAEC")

    assert factory.calls[0]["address"] == CONN.address
    assert factory.device.calls == [("send_button", "AAEC")]
