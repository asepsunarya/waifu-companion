import base64
import importlib
import socket
import subprocess
from pathlib import Path

import broadlink
import pytest
import tinytuya

from waifu.config import AppConfig, load_config
from waifu.devices.ir_codes import IrCodeStore
from waifu.devices.ir_device import IrDevice
from waifu.devices.mock import MockDevice
from waifu.devices.pc import PcDevice
from waifu.devices.registry import build_registry
from waifu.devices.tuya import TuyaLight, TuyaPlug

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_BLASTER_HOST = "192.168.1.50"
LOCAL_PC_COMMANDS = ["shutdown", "restart", "cancel_shutdown", "sleep"]
AC_TEMPERATURES = [f"temp_{degrees}" for degrees in range(16, 31)]


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def config_with(tmp_path: Path, *devices: dict) -> AppConfig:
    return AppConfig.model_validate(
        {"ir_codes_path": str(tmp_path / "ir_codes.json"), "devices": list(devices)}
    )


def ir_on(device_id: str, ir: dict) -> dict:
    return {"id": device_id, "name": device_id.title(), "type": "ir", "ir": ir}


def tuya_conn(device_id: str) -> dict:
    return {"device_id": device_id, "local_key": "k3y-not-for-logs", "address": "192.168.1.60"}


@pytest.fixture
def example_config(tmp_path) -> AppConfig:
    """config.example.yaml, with its learned IR codes kept in a fresh temp file."""
    config = load_config(ROOT / "config.example.yaml")
    return config.model_copy(update={"ir_codes_path": tmp_path / "ir_codes.json"})


@pytest.fixture
def blocked_io(monkeypatch) -> list[str]:
    """Refuses, and records, every socket or child process the code under test tries to open."""
    attempts: list[str] = []

    def refuse(what: str):
        def opener(*args, **kwargs):
            attempts.append(what)
            raise AssertionError(f"opened a {what} while it must not")

        return opener

    monkeypatch.setattr(socket, "socket", refuse("socket"))
    monkeypatch.setattr(subprocess, "Popen", refuse("subprocess"))
    return attempts


class FakeBroadlinkRemote:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def auth(self) -> bool:
        return True

    def send_data(self, data: bytes) -> None:
        self.sent.append(data)


class FakeBroadlinkNetwork:
    """Replaces broadlink.hello: one fake remote per host, and a log of every discovery."""

    def __init__(self) -> None:
        self.remotes: dict[str, FakeBroadlinkRemote] = {}
        self.discovered: list[str] = []

    def hello(self, host: str, **kwargs: object) -> FakeBroadlinkRemote:
        self.discovered.append(host)
        return self.remotes.setdefault(host, FakeBroadlinkRemote())


@pytest.fixture
def broadlink_network(monkeypatch) -> FakeBroadlinkNetwork:
    network = FakeBroadlinkNetwork()
    monkeypatch.setattr(broadlink, "hello", network.hello)
    return network


class FakeTuyaRemote:
    """Stands in for tinytuya's IRRemoteControlDevice after control-type detection."""

    def __init__(self, **kwargs: object) -> None:
        self.opened_with = kwargs
        self.control_type = 2
        self.sent: list[str] = []

    def send_button(self, base64_code: str) -> None:
        self.sent.append(base64_code)


@pytest.fixture
def tuya_ir_remotes(monkeypatch) -> list[FakeTuyaRemote]:
    opened: list[FakeTuyaRemote] = []

    def open_remote(**kwargs: object) -> FakeTuyaRemote:
        opened.append(FakeTuyaRemote(**kwargs))
        return opened[-1]

    module = importlib.import_module("tinytuya.Contrib.IRRemoteControlDevice")
    monkeypatch.setattr(module, "IRRemoteControlDevice", open_remote)
    return opened


def test_real_mode_builds_the_device_class_for_each_type(example_config):
    registry = build_registry(example_config)

    assert {device.id: type(device) for device in registry.all()} == {
        "lampu_kamar": TuyaLight,
        "ac": IrDevice,
        "proyektor": IrDevice,
        "komputer": PcDevice,
        "colokan_pc": TuyaPlug,
    }


def test_real_mode_build_opens_no_connection_and_runs_nothing(example_config, blocked_io):
    build_registry(example_config)

    assert blocked_io == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_every_device_keeps_its_id_name_and_aliases_in_config_order(example_config, dry_run):
    registry = build_registry(example_config, dry_run=dry_run)

    assert [(device.id, device.name, device.aliases) for device in registry.all()] == [
        (device.id, device.name, device.aliases) for device in example_config.devices
    ]


def test_ir_devices_on_one_broadlink_host_share_one_blaster(example_config, broadlink_network):
    store = IrCodeStore(example_config.ir_codes_path)
    store.put("ac", "on", b64(b"ac-on"))
    store.put("proyektor", "power", b64(b"proyektor-power"))
    registry = build_registry(example_config)
    assert broadlink_network.discovered == []

    assert registry.execute("ac", "on").ok
    assert registry.execute("proyektor", "power").ok

    assert broadlink_network.discovered == [EXAMPLE_BLASTER_HOST]
    assert broadlink_network.remotes[EXAMPLE_BLASTER_HOST].sent == [b"ac-on", b"proyektor-power"]


def test_ir_devices_on_different_broadlink_hosts_get_their_own_blaster(tmp_path, broadlink_network):
    config = config_with(
        tmp_path,
        ir_on("ac", {"backend": "broadlink", "host": "192.168.1.50"}),
        ir_on("tv", {"backend": "broadlink", "host": "192.168.1.51"}),
    )
    store = IrCodeStore(config.ir_codes_path)
    store.put("ac", "on", b64(b"ac-on"))
    store.put("tv", "power", b64(b"tv-power"))
    registry = build_registry(config)

    registry.execute("ac", "on")
    registry.execute("tv", "power")

    assert broadlink_network.discovered == ["192.168.1.50", "192.168.1.51"]
    assert broadlink_network.remotes["192.168.1.51"].sent == [b"tv-power"]


def test_ir_devices_on_one_tuya_blaster_share_it(tmp_path, tuya_ir_remotes):
    ir = {"backend": "tuya", "tuya": tuya_conn("bf_ir_blaster")}
    config = config_with(tmp_path, ir_on("ac", ir), ir_on("tv", ir))
    store = IrCodeStore(config.ir_codes_path)
    store.put("ac", "on", "QUMtT04=")
    store.put("tv", "power", "VFYtUE9XRVI=")
    registry = build_registry(config)
    assert tuya_ir_remotes == []

    assert registry.execute("ac", "on").ok
    assert registry.execute("tv", "power").ok

    [remote] = tuya_ir_remotes
    assert remote.opened_with["dev_id"] == "bf_ir_blaster"
    assert remote.sent == ["QUMtT04=", "VFYtUE9XRVI="]


def test_real_ir_device_offers_learned_codes_and_reports_declared_ones_as_missing(
    example_config,
):
    IrCodeStore(example_config.ir_codes_path).put("ac", "on", b64(b"ac-on"))

    ac = build_registry(example_config).get("ac")

    assert ac.commands() == ["on"]
    assert ac.missing_commands() == ["off", *AC_TEMPERATURES]


@pytest.mark.parametrize(
    ("device_type", "vendor_class"),
    [("tuya_light", "BulbDevice"), ("tuya_plug", "OutletDevice")],
)
def test_real_tuya_device_connects_with_its_own_id_only_on_the_first_command(
    tmp_path, monkeypatch, device_type, vendor_class
):
    opened: list[dict] = []

    class FakeTuyaSwitch:
        def __init__(self, **kwargs: object) -> None:
            opened.append(kwargs)

        def turn_on(self) -> None:
            return None

    monkeypatch.setattr(tinytuya, vendor_class, FakeTuyaSwitch)
    device = {"id": "saklar", "name": "Saklar", "type": device_type, "tuya": tuya_conn("bf_one")}
    registry = build_registry(config_with(tmp_path, device))
    assert opened == []

    assert registry.execute("saklar", "on").ok

    assert [kwargs["dev_id"] for kwargs in opened] == ["bf_one"]


def test_real_pc_offers_the_commands_its_connection_allows(example_config):
    komputer = build_registry(example_config).get("komputer")

    assert komputer.commands() == LOCAL_PC_COMMANDS


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    ("configured", "expected"),
    [(None, ["on", "off"]), (["low", "high"], ["low", "high"])],
)
def test_mock_device_offers_its_configured_or_default_commands(
    tmp_path, dry_run, configured, expected
):
    device = {"id": "kipas", "name": "Kipas", "type": "mock"}
    if configured is not None:
        device["commands"] = configured

    kipas = build_registry(config_with(tmp_path, device), dry_run=dry_run).get("kipas")

    assert isinstance(kipas, MockDevice)
    assert (kipas.kind, kipas.commands()) == ("mock", expected)


def test_example_config_builds_in_dry_run_without_network_or_subprocess(example_config, blocked_io):
    registry = build_registry(example_config, dry_run=True)

    assert [device.id for device in registry.all()] == [
        "lampu_kamar",
        "ac",
        "proyektor",
        "komputer",
        "colokan_pc",
    ]
    assert all(isinstance(device, MockDevice) for device in registry.all())
    assert blocked_io == []


def test_dry_run_mocks_keep_the_real_kind_and_commands_of_each_device(example_config):
    registry = build_registry(example_config, dry_run=True)

    assert {device.id: (device.kind, device.commands()) for device in registry.all()} == {
        "lampu_kamar": ("light", ["on", "off", "toggle", "brightness"]),
        "ac": ("ir", ["off", "on", *AC_TEMPERATURES]),
        "proyektor": ("ir", ["back", "home", "ok", "power", "volume_down", "volume_up"]),
        "komputer": ("pc", LOCAL_PC_COMMANDS),
        "colokan_pc": ("plug", ["on", "off", "toggle"]),
    }


def test_dry_run_ac_temperature_succeeds_and_records_temp_24(example_config, blocked_io):
    registry = build_registry(example_config, dry_run=True)

    result = registry.execute("ac", "temp", 24)

    assert result.ok
    assert registry.get("ac").calls == [("temp_24", 24)]
    assert blocked_io == []


def test_dry_run_ir_commands_join_learned_codes_and_declared_ones_sorted_once(example_config):
    store = IrCodeStore(example_config.ir_codes_path)
    store.put("ac", "on", b64(b"ac-on"))
    store.put("ac", "swing", b64(b"ac-swing"))

    ac = build_registry(example_config, dry_run=True).get("ac")

    assert ac.commands() == ["off", "on", "swing", *AC_TEMPERATURES]


@pytest.mark.parametrize(
    ("pc", "expected"),
    [
        ({"local": True}, LOCAL_PC_COMMANDS),
        ({"local": True, "mac": "AA:BB:CC:DD:EE:FF"}, ["wake", *LOCAL_PC_COMMANDS]),
        ({"local": False, "mac": "AA:BB:CC:DD:EE:FF"}, ["wake"]),
        ({"local": False}, []),
    ],
)
def test_dry_run_pc_offers_the_commands_its_connection_allows(tmp_path, pc, expected):
    device = {"id": "komputer", "name": "Komputer", "type": "pc", "pc": pc}

    komputer = build_registry(config_with(tmp_path, device), dry_run=True).get("komputer")

    assert (komputer.kind, komputer.commands()) == ("pc", expected)


def test_dry_run_pc_shutdown_is_only_recorded(example_config, blocked_io):
    registry = build_registry(example_config, dry_run=True)

    assert registry.execute("komputer", "shutdown").ok

    assert registry.get("komputer").calls == [("shutdown", None)]
    assert blocked_io == []


def test_dry_run_never_contacts_a_blaster(example_config, broadlink_network):
    IrCodeStore(example_config.ir_codes_path).put("ac", "on", b64(b"ac-on"))
    registry = build_registry(example_config, dry_run=True)

    assert registry.execute("ac", "on").ok

    assert broadlink_network.discovered == []
