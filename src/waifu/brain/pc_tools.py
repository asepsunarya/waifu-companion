"""The tool that lets Cyrene read the PC: `pc_health`.

"PC-ku gimana?", "GPU-ku panas nggak?", "disk C sisa berapa?": the tool reads the PC right
then (`pc_health.read_snapshot`) and answers in compact JSON with rounded numbers, one
decimal for gigabytes and watts and whole percents; a part that could not be read is left
out. Only numbers, drive letters, and the GPU's model name reach the model. The spec carries
no readings, so it stays byte-identical for the brain's lifetime and keeps the prompt cache
warm.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping

from waifu.brain.base import ToolSpec
from waifu.pc_health import Drive, GpuStatus, PcSnapshot

logger = logging.getLogger(__name__)

PC_HEALTH = "pc_health"
_DESCRIPTION = (
    "Read Master's PC right now: the CPU load, RAM in use, each fixed drive's free space, the "
    "NVIDIA GPU's temperature, load, VRAM, and power draw (gigabytes as Windows Explorer shows "
    "them), and uptime_hours, the hours since Windows last restarted (sleep and a Fast Startup "
    "shutdown do not reset it, so it is not how long since Master turned the PC on: call it the "
    "time since the last restart). A part that could not be read is left out; "
    "without an NVIDIA GPU (or nvidia-smi) the gpu part is missing. Use it whenever Master asks "
    "how the PC is doing, about its temperature, load, memory, or disk space; never guess. "
    "Answer in one or two short sentences with rounded numbers, without reading out every "
    "figure. There is no CPU temperature (Windows does not report one): say so when asked. "
    "Never suggest buying hardware; you may suggest closing heavy programs or cleaning the disk."
)


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class PcTools:
    """A `ToolProvider` for the brain: how the PC is doing, read when asked."""

    def __init__(self, read: Callable[[], PcSnapshot], *, owner_call: str) -> None:
        self._read = read
        self._owner_call = owner_call  # in the error result Ollama's fallback reads out

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=PC_HEALTH,
                description=_DESCRIPTION,
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            )
        ]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == PC_HEALTH:
                return self._pc_health(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except Exception:
            logger.exception("PC health tool '%s' failed", name)
            return _error(f"Kondisi PC {self._owner_call} tidak bisa dibaca saat ini.")
        return _error(f"Tool '{name}' bukan tool kesehatan PC.")

    def _pc_health(self, arguments: object) -> tuple[str, bool]:
        if not isinstance(arguments, Mapping) or arguments:
            raise _InvalidArguments("tool ini tidak menerima argumen; kirim objek kosong {}.")
        return _to_json(_snapshot_json(self._read())), False


def _snapshot_json(snapshot: PcSnapshot) -> dict[str, object]:
    """The snapshot as the tool reports it: rounded, with the parts that are None left out."""
    data: dict[str, object] = {}
    if snapshot.cpu_percent is not None:
        data["cpu_percent"] = round(snapshot.cpu_percent)
    if snapshot.ram_used_gb is not None and snapshot.ram_total_gb is not None:
        data["ram"] = {
            "used_gb": round(snapshot.ram_used_gb, 1),
            "total_gb": round(snapshot.ram_total_gb, 1),
            "percent": _percent(snapshot.ram_used_gb, snapshot.ram_total_gb),
        }
    data["drives"] = [_drive_json(drive) for drive in snapshot.drives]
    if snapshot.gpu is not None:
        data["gpu"] = _gpu_json(snapshot.gpu)
    if snapshot.uptime_s is not None:
        data["uptime_hours"] = snapshot.uptime_s // 3600
    return data


def _drive_json(drive: Drive) -> dict[str, object]:
    return {
        "drive": drive.letter,
        "free_gb": round(drive.free_gb, 1),
        "total_gb": round(drive.total_gb, 1),
        "free_percent": _percent(drive.free_gb, drive.total_gb),
    }


def _gpu_json(gpu: GpuStatus) -> dict[str, object]:
    fields: dict[str, object] = {
        "name": gpu.name,
        "temp_c": gpu.temp_c,
        "load_percent": gpu.load_percent,
        "vram_used_gb": _one_decimal(gpu.vram_used_gb),
        "vram_total_gb": _one_decimal(gpu.vram_total_gb),
        "power_w": _one_decimal(gpu.power_w),
    }
    return {key: value for key, value in fields.items() if value is not None}


def _percent(part: float, whole: float) -> int:
    """`part` as a whole percent of `whole`; 0 of nothing (a drive reporting no size) is 0."""
    return round(100 * part / whole) if whole > 0 else 0


def _one_decimal(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
