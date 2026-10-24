import json
from pathlib import Path

import pytest

from waifu.config import AppConfig
from waifu.config_patch import Change
from waifu.tuya_import import (
    PLACEHOLDER,
    CloudDevice,
    TuyaImportError,
    TuyaTarget,
    read_cloud_devices,
    suggest,
    tuya_changes,
    tuya_targets,
)

LAMP_KEY = "Lk1'<{x}#y:z\"q!"
# As tinytuya's wizard writes devices.json after "Poll local devices?" found the lamp: `ip` and
# `version` added, `last_ip` the house's public address the cloud last saw.
LAMP = {
    "name": "Lampu Kamar",
    "id": "bf1111111111111111aaaa",
    "key": LAMP_KEY,
    "mac": "aa:bb:cc:dd:ee:01",
    "uuid": "u1",
    "category": "dj",
    "product_name": "Smart Bulb",
    "product_id": "p1",
    "biz_type": 18,
    "model": "",
    "sub": False,
    "icon": "https://images.tuyaus.com/smart/icon/lamp.png",
    "last_ip": "36.80.1.2",
    "ip": "192.168.1.60",
    "version": "3.3",
}
PLUG = {
    "name": "Smart Plug",
    "id": "bf2222222222222222bbbb",
    "key": "PlugKey123456789",
    "category": "cz",
    "sub": False,
    "last_ip": "36.80.1.2",
}
BLASTER = {
    "name": "IR Remote",
    "id": "bf3333333333333333cccc",
    "key": "IrKey12345678901",
    "category": "wnykq",
    "sub": False,
}
AC_REMOTE = {
    "name": "AC Remote",
    "id": "bf4444444444444444dddd",
    "key": "IrKey12345678901",
    "category": "infrared_ac",
    "sub": True,
    "parent": "bf3333333333333333cccc",
}


def write_devices(folder: Path, entries: object, snapshot: object | None = None) -> None:
    (folder / "devices.json").write_text(json.dumps(entries, indent=4), encoding="utf-8")
    if snapshot is not None:
        (folder / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")


def read(folder: Path) -> list[CloudDevice]:
    return read_cloud_devices(folder / "devices.json", folder / "snapshot.json")


def cloud(entry: dict, **changes) -> CloudDevice:
    """`entry` as read, with `changes`."""
    device = CloudDevice(
        id=entry["id"],
        key=entry["key"],
        name=entry["name"],
        category=entry.get("category", ""),
        ip=None,
        version=None,
        sub=entry.get("sub", False),
    )
    return CloudDevice(**{**device.__dict__, **changes})


# --- reading the wizard's files --------------------------------------------------------------


def test_reads_each_device_with_the_address_and_version_the_poll_found(tmp_path):
    write_devices(tmp_path, [LAMP])

    assert read(tmp_path) == [cloud(LAMP, ip="192.168.1.60", version=3.3)]


def test_takes_the_address_and_version_from_the_snapshot_when_devices_json_lacks_them(tmp_path):
    snapshot = {
        "timestamp": 1761370000.5,
        "devices": [{"id": PLUG["id"], "ip": "192.168.1.61", "ver": "3.4", "origin": "broadcast"}],
    }
    write_devices(tmp_path, [PLUG], snapshot)

    assert read(tmp_path) == [cloud(PLUG, ip="192.168.1.61", version=3.4)]


def test_never_takes_last_ip_for_the_address(tmp_path):
    write_devices(tmp_path, [{**PLUG, "last_ip": "192.168.1.70"}])

    assert read(tmp_path)[0].ip is None


@pytest.mark.parametrize(
    "ip",
    ["36.80.1.2", "127.0.0.1", "169.254.1.2", "0.0.0.0", "100.64.0.1", "192.168.1", "", None, 42],
)
def test_takes_only_a_home_lan_ipv4_address(tmp_path, ip):
    write_devices(tmp_path, [{**LAMP, "ip": ip}])

    assert read(tmp_path)[0].ip is None


@pytest.mark.parametrize("ip", ["10.0.0.5", "172.16.3.4", "172.31.255.1", "192.168.100.2"])
def test_takes_each_private_lan_range(tmp_path, ip):
    write_devices(tmp_path, [{**LAMP, "ip": ip}])

    assert read(tmp_path)[0].ip == ip


@pytest.mark.parametrize(
    ("version", "read_as"),
    [
        ("3.3", 3.3),
        (3.4, 3.4),
        ("3.5", 3.5),
        (3.1, 3.1),
        ("", None),
        (0.0, None),
        ("3.9", None),
        (3.35, None),
        (True, None),
        ("abc", None),
    ],
)
def test_takes_only_a_tuya_protocol_version(tmp_path, version, read_as):
    write_devices(tmp_path, [{**LAMP, "version": version}])

    assert read(tmp_path)[0].version == read_as


def test_skips_entries_without_an_id_and_a_key(tmp_path):
    entries = [
        1,
        "x",
        None,
        {"id": "a"},
        {"key": "k"},
        {"id": "", "key": "k"},
        {"id": "a", "key": ""},
        {"id": 5, "key": "k"},
        LAMP,
    ]
    write_devices(tmp_path, entries)

    assert [device.id for device in read(tmp_path)] == [LAMP["id"]]


def test_a_broken_snapshot_is_no_snapshot(tmp_path):
    write_devices(tmp_path, [PLUG])
    (tmp_path / "snapshot.json").write_text("{not json", encoding="utf-8")

    assert read(tmp_path) == [cloud(PLUG)]


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (None, "devices.json belum ada"),
        (f'[{{"key": "{LAMP_KEY}"', "devices.json tidak bisa dibaca"),
        (f'{{"key": "{PLACEHOLDER}x"}}', "devices.json bukan daftar perangkat"),
    ],
)
def test_a_missing_or_broken_device_list_is_refused_without_its_content(tmp_path, content, problem):
    if content is not None:
        (tmp_path / "devices.json").write_text(content, encoding="utf-8")

    with pytest.raises(TuyaImportError) as raised:
        read(tmp_path)

    assert problem in str(raised.value)
    assert LAMP_KEY not in str(raised.value)


def test_a_device_never_shows_its_local_key():
    assert LAMP_KEY not in repr(cloud(LAMP))


# --- the config's Tuya devices ---------------------------------------------------------------


def config_with(*devices: dict) -> AppConfig:
    return AppConfig.model_validate({"devices": list(devices)})


def tuya_block(device_id: str = PLACEHOLDER, local_key: str = PLACEHOLDER) -> dict:
    return {"device_id": device_id, "local_key": local_key}


LAMPU_KAMAR = {
    "id": "lampu_kamar",
    "name": "Lampu kamar",
    "aliases": ["lampu", "lampu utama"],
    "type": "tuya_light",
    "tuya": tuya_block(),
}
COLOKAN_PC = {"id": "colokan_pc", "name": "Colokan PC", "type": "tuya_plug", "tuya": tuya_block()}
TV = {
    "id": "tv",
    "name": "TV",
    "type": "ir",
    "ir": {"backend": "tuya", "tuya": tuya_block()},
    "commands": ["power"],
}
AC = {
    "id": "ac",
    "name": "AC Samsung",
    "type": "ir",
    "ir": {"backend": "broadlink", "host": "192.168.1.50"},
}


def test_lists_lights_plugs_and_tuya_ir_blasters_in_config_order():
    config = config_with(
        LAMPU_KAMAR,
        AC,
        {"id": "komputer", "name": "Komputer", "type": "pc", "pc": {}},
        TV,
        {**COLOKAN_PC, "tuya": tuya_block("bf2222222222222222bbbb", "real-key-12345678")},
    )

    assert tuya_targets(config) == [
        TuyaTarget(
            "lampu_kamar",
            "Lampu kamar",
            ("lampu", "lampu utama"),
            "light",
            ("devices", "lampu_kamar", "tuya"),
            PLACEHOLDER,
            filled=False,
        ),
        TuyaTarget(
            "tv", "TV", (), "ir", ("devices", "tv", "ir", "tuya"), PLACEHOLDER, filled=False
        ),
        TuyaTarget(
            "colokan_pc",
            "Colokan PC",
            (),
            "plug",
            ("devices", "colokan_pc", "tuya"),
            "bf2222222222222222bbbb",
            filled=True,
        ),
    ]


def test_a_device_with_only_its_local_key_still_a_placeholder_is_not_filled():
    config = config_with({**LAMPU_KAMAR, "tuya": tuya_block("bf1111111111111111aaaa")})

    assert tuya_targets(config)[0].filled is False


# --- matching --------------------------------------------------------------------------------


def targets(*devices: dict) -> list[TuyaTarget]:
    return tuya_targets(config_with(*devices))


def test_suggests_by_name_and_by_the_category_of_a_plug():
    found = suggest(targets(LAMPU_KAMAR, COLOKAN_PC), [cloud(LAMP), cloud(PLUG)])

    assert found == {"lampu_kamar": 0, "colokan_pc": 1}


def test_suggests_by_an_alias():
    lamp = {**LAMPU_KAMAR, "id": "lampu_meja", "name": "Lampu meja", "aliases": ["lampu belajar"]}

    found = suggest(targets(lamp), [cloud(PLUG), cloud({**LAMP, "name": "Lampu Belajar"})])

    assert found == {"lampu_meja": 1}


def test_suggests_nothing_below_the_bar():
    assert suggest(targets(COLOKAN_PC), [cloud(LAMP)]) == {"colokan_pc": None}


def test_gives_a_light_or_plug_entry_to_one_device_only():
    second = {**LAMPU_KAMAR, "id": "lampu_kamar_2", "name": "Lampu kamar 2", "aliases": []}

    found = suggest(targets(second, LAMPU_KAMAR), [cloud(LAMP)])

    assert found == {"lampu_kamar_2": None, "lampu_kamar": 0}


def test_never_suggests_a_sub_device():
    found = suggest(targets(LAMPU_KAMAR), [cloud({**LAMP, "sub": True})])

    assert found == {"lampu_kamar": None}


def test_offers_the_blaster_to_every_tuya_ir_device_whatever_its_name():
    radio = {**TV, "id": "radio", "name": "Radio"}

    found = suggest(targets(TV, radio), [cloud(AC_REMOTE), cloud(LAMP), cloud(BLASTER)])

    assert found == {"tv": 2, "radio": 2}


# --- the changes -----------------------------------------------------------------------------


def test_changes_set_the_id_the_hidden_key_and_the_address_and_version_found():
    [lamp] = targets(LAMPU_KAMAR)

    changes = tuya_changes(lamp, cloud(LAMP, ip="192.168.1.60", version=3.3))

    keys = ("devices", "lampu_kamar", "tuya")
    assert changes == [
        Change((*keys, "device_id"), LAMP["id"]),
        Change((*keys, "local_key"), LAMP_KEY, secret=True),
        Change((*keys, "address"), "192.168.1.60"),
        Change((*keys, "version"), 3.3),
    ]


def test_changes_leave_an_unknown_address_and_version_as_they_are():
    [tv] = targets(TV)

    changes = tuya_changes(tv, cloud(BLASTER))

    assert [change.keys[-1] for change in changes] == ["device_id", "local_key"]
    assert changes[0].keys == ("devices", "tv", "ir", "tuya", "device_id")


def test_the_repository_ignores_every_file_the_wizard_writes():
    # `waifu setup tuya` runs the wizard in the project folder: its files hold the cloud
    # credentials and every local key.
    ignored = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")

    assert {"tinytuya.json", "devices.json", "snapshot.json", "tuya-raw.json"} <= set(
        ignored.splitlines()
    )
