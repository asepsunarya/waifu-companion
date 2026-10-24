"""The Tuya devices tinytuya's wizard found, matched to the Tuya devices in config.yaml.

`waifu setup tuya` runs the wizard in the owner's terminal; it writes devices.json (each
device's id, local key, name, and category, plus `ip` and `version` once its "Poll local
devices?" scan found the device) and snapshot.json (the scan's own `ip` and `ver`). Only those
two files are read here, never tinytuya.json, which holds the cloud credentials.

`last_ip` in devices.json is the house's public address as the cloud last saw it, never the
device's LAN address, so it is never used: an address is taken only from the scan, and only
when it is a home LAN IPv4 address.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Literal

from waifu.config import AppConfig, TuyaConnection
from waifu.config_patch import DEVICES, Change

# What config.example.yaml has in place of the wizard's values.
PLACEHOLDER = "isi_dari_tinytuya_wizard"
TUYA_VERSIONS = frozenset({3.1, 3.2, 3.3, 3.4, 3.5})
HOME_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
TargetKind = Literal["light", "plug", "ir"]
# Tuya's category codes, as the kind of config device they suit.
CATEGORY_KINDS: dict[str, TargetKind] = {
    "dj": "light",
    "dd": "light",
    "xdd": "light",
    "fwd": "light",
    "dc": "light",
    "cz": "plug",
    "pc": "plug",
    "kg": "plug",
    "wnykq": "ir",
}
CATEGORY_BONUS = 0.2
MATCH_BAR = 0.5


class TuyaImportError(Exception):
    """devices.json is missing or unreadable; the message, in Indonesian, quotes none of it."""


@dataclass(frozen=True)
class CloudDevice:
    """One device as the wizard listed it."""

    id: str
    key: str = field(repr=False)
    name: str
    category: str
    ip: str | None  # its LAN address, when the wizard's scan found it
    version: float | None
    sub: bool  # behind a gateway or a blaster (a virtual remote): never controlled on its own


@dataclass(frozen=True)
class TuyaTarget:
    """A Tuya connection in config.yaml to fill: `keys` lead to its block."""

    device_id: str
    name: str
    aliases: tuple[str, ...]
    kind: TargetKind
    keys: tuple[str, ...]
    current: TuyaConnection  # what config.yaml has now
    filled: bool  # neither device_id nor local_key is the placeholder


def read_cloud_devices(devices_path: Path, snapshot_path: Path) -> list[CloudDevice]:
    """Every device in devices.json with an id and a key, its address and version from it or
    from snapshot.json."""
    try:
        entries = json.loads(devices_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TuyaImportError(f"{devices_path.name} belum ada") from exc
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise TuyaImportError(f"{devices_path.name} tidak bisa dibaca") from exc
    if not isinstance(entries, list):
        raise TuyaImportError(f"{devices_path.name} bukan daftar perangkat")
    scanned = _snapshot(snapshot_path)
    devices = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        device_id, key = entry.get("id"), entry.get("key")
        if not (_text(device_id) and _text(key)):
            continue
        seen = scanned.get(device_id, {})
        devices.append(
            CloudDevice(
                id=device_id,
                key=key,
                name=str(entry.get("name") or "").strip(),
                category=str(entry.get("category") or "").strip(),
                ip=_home_address(entry.get("ip")) or _home_address(seen.get("ip")),
                version=_version(entry.get("version")) or _version(seen.get("ver")),
                sub=entry.get("sub") is True,
            )
        )
    return devices


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _snapshot(path: Path) -> dict[str, dict[str, object]]:
    """The scan's devices by id; nothing when there is no readable snapshot."""
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    devices = snapshot.get("devices") if isinstance(snapshot, dict) else None
    if not isinstance(devices, list):
        return {}
    return {
        device["id"]: device
        for device in devices
        if isinstance(device, dict) and isinstance(device.get("id"), str)
    }


def _home_address(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        address = IPv4Address(value.strip())
    except ValueError:
        return None
    return str(address) if any(address in network for network in HOME_NETWORKS) else None


def _version(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    rounded = round(number, 1)
    return rounded if rounded in TUYA_VERSIONS and abs(number - rounded) < 1e-9 else None


def tuya_targets(config: AppConfig) -> list[TuyaTarget]:
    """The lights, plugs, and Tuya IR blasters in config.yaml, in its order."""
    targets = []
    for device in config.devices:
        if device.type in ("tuya_light", "tuya_plug") and device.tuya is not None:
            kind: TargetKind = "light" if device.type == "tuya_light" else "plug"
            connection, keys = device.tuya, (DEVICES, device.id, "tuya")
        elif device.ir is not None and device.ir.backend == "tuya" and device.ir.tuya is not None:
            kind = "ir"
            connection, keys = device.ir.tuya, (DEVICES, device.id, "ir", "tuya")
        else:
            continue
        targets.append(
            TuyaTarget(
                device.id,
                device.name,
                tuple(device.aliases),
                kind,
                keys,
                connection,
                filled=PLACEHOLDER not in (connection.device_id, connection.local_key),
            )
        )
    return targets


def match_score(target: TuyaTarget, device: CloudDevice) -> float:
    """How alike the device's name is to the target's name, an alias, or its id, with a bonus
    for a category of the target's kind; any blaster suits an IR target."""
    wanted = _words(device.name)
    names = [target.name, *target.aliases, target.device_id.replace("_", " ")]
    score = max(
        (SequenceMatcher(None, _words(name), wanted).ratio() for name in names if wanted),
        default=0.0,
    )
    kind = CATEGORY_KINDS.get(device.category)
    if kind == target.kind:
        score += CATEGORY_BONUS
        if kind == "ir":
            score = max(score, MATCH_BAR)
    return score


def _words(text: str) -> str:
    return " ".join(text.lower().split())


def suggest(targets: list[TuyaTarget], devices: list[CloudDevice]) -> dict[str, int | None]:
    """The index in `devices` to offer each target (by its config id), or None: the best pairs
    first, never a sub-device, a light's or plug's entry given once, while one blaster may serve
    every IR target."""
    pairs = sorted(
        (
            (match_score(target, device), target_index, device_index)
            for target_index, target in enumerate(targets)
            for device_index, device in enumerate(devices)
            if not device.sub
        ),
        key=lambda pair: -pair[0],
    )
    chosen: dict[str, int | None] = {target.device_id: None for target in targets}
    switches: set[int] = set()  # taken by a light or a plug
    blasters: set[int] = set()  # taken by an IR target
    for score, target_index, device_index in pairs:
        target = targets[target_index]
        if score < MATCH_BAR or chosen[target.device_id] is not None:
            continue
        if device_index in switches or (target.kind != "ir" and device_index in blasters):
            continue
        chosen[target.device_id] = device_index
        (blasters if target.kind == "ir" else switches).add(device_index)
    return chosen


def tuya_changes(target: TuyaTarget, device: CloudDevice) -> list[Change]:
    """What to write for `target` from `device`: the id and the local key (secret), and the
    address and version when the scan found them (otherwise those stay as they are), each only
    when config.yaml has another value."""
    current = target.current
    changes = [
        Change((*target.keys, "device_id"), device.id),
        Change((*target.keys, "local_key"), device.key, secret=True),
    ]
    if device.ip is not None:
        changes.append(Change((*target.keys, "address"), device.ip))
    if device.version is not None:
        changes.append(Change((*target.keys, "version"), device.version))
    return [change for change in changes if getattr(current, change.keys[-1]) != change.value]
