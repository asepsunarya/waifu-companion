import json

import pytest

from waifu.devices import ir_codes
from waifu.devices.ir_codes import IrCodeStore, IrCodeStoreError


def test_missing_file_means_no_codes(tmp_path):
    store = IrCodeStore(tmp_path / "ir_codes.json")

    assert store.get("ac", "on") is None
    assert store.commands("ac") == []


def test_put_code_is_readable_by_a_fresh_store(tmp_path):
    path = tmp_path / "ir_codes.json"
    IrCodeStore(path).put("ac", "on", "JgBQAAAB")

    assert IrCodeStore(path).get("ac", "on") == "JgBQAAAB"


def test_commands_are_listed_sorted(tmp_path):
    store = IrCodeStore(tmp_path / "ir_codes.json")
    for command in ["temp_24", "off", "on"]:
        store.put("ac", command, "AAAA")

    assert store.commands("ac") == ["off", "on", "temp_24"]


def test_commands_of_an_unknown_device_are_empty(tmp_path):
    store = IrCodeStore(tmp_path / "ir_codes.json")
    store.put("ac", "on", "AAAA")

    assert store.commands("tv") == []


def test_put_overwrites_the_same_command_and_keeps_other_devices(tmp_path):
    store = IrCodeStore(tmp_path / "ir_codes.json")
    store.put("ac", "on", "OLD=")
    store.put("tv", "power", "TVPW")

    store.put("ac", "on", "NEW=")

    assert store.get("ac", "on") == "NEW="
    assert store.get("tv", "power") == "TVPW"


def test_put_creates_the_parent_folder(tmp_path):
    path = tmp_path / "data" / "nested" / "ir_codes.json"

    IrCodeStore(path).put("ac", "on", "AAAA")

    assert path.is_file()


def test_put_writes_indented_utf8_json_by_device_then_command(tmp_path):
    path = tmp_path / "ir_codes.json"

    IrCodeStore(path).put("ac", "on", "AAAA")

    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == {"ac": {"on": "AAAA"}}
    assert '\n  "ac": {' in text


def test_put_leaves_no_temporary_file_behind(tmp_path):
    path = tmp_path / "ir_codes.json"

    IrCodeStore(path).put("ac", "on", "AAAA")

    assert [p.name for p in tmp_path.iterdir()] == ["ir_codes.json"]


def test_failed_replace_keeps_the_previous_file_intact(tmp_path, monkeypatch):
    path = tmp_path / "ir_codes.json"
    store = IrCodeStore(path)
    store.put("ac", "on", "KEEP")

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(ir_codes.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        store.put("ac", "off", "LOST")

    assert json.loads(path.read_text(encoding="utf-8")) == {"ac": {"on": "KEEP"}}
    assert [p.name for p in tmp_path.iterdir()] == ["ir_codes.json"]


def test_put_forces_the_new_codes_to_disk_before_replacing_the_file(tmp_path, monkeypatch):
    # Without fsync, a power cut right after a rename can leave an empty ir_codes.json.
    path = tmp_path / "ir_codes.json"
    store = IrCodeStore(path)
    store.put("ac", "on", "KEEP")
    events = []
    real_fsync, real_replace = ir_codes.os.fsync, ir_codes.os.replace

    def recording_fsync(fd):
        real_fsync(fd)
        (temp_file,) = [p for p in tmp_path.iterdir() if p.name != "ir_codes.json"]
        events.append(("fsync", json.loads(temp_file.read_text(encoding="utf-8"))))

    def recording_replace(src, dst):
        events.append(("replace", None))
        real_replace(src, dst)

    monkeypatch.setattr(ir_codes.os, "fsync", recording_fsync)
    monkeypatch.setattr(ir_codes.os, "replace", recording_replace)

    store.put("ac", "off", "NEW=")

    assert events == [("fsync", {"ac": {"off": "NEW=", "on": "KEEP"}}), ("replace", None)]


@pytest.mark.parametrize("command", ["", "ON", "temp 24", "temp-24", "suhu/24", "nyalakan!"])
def test_put_rejects_invalid_command_names(tmp_path, command):
    path = tmp_path / "ir_codes.json"

    with pytest.raises(ValueError, match="command"):
        IrCodeStore(path).put("ac", command, "AAAA")

    assert not path.exists()


def test_corrupt_json_raises_an_error_naming_the_file(tmp_path):
    path = tmp_path / "ir_codes.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(IrCodeStoreError, match="ir_codes.json"):
        IrCodeStore(path).commands("ac")


def test_json_with_the_wrong_shape_raises_an_error_naming_the_file(tmp_path):
    path = tmp_path / "ir_codes.json"
    path.write_text('{"ac": ["on", "off"]}', encoding="utf-8")

    with pytest.raises(IrCodeStoreError, match="ir_codes.json"):
        IrCodeStore(path).get("ac", "on")


def test_store_error_is_a_value_error():
    assert issubclass(IrCodeStoreError, ValueError)
