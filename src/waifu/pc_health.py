"""What Cyrene reads about the PC: load, memory, fixed drives, the NVIDIA GPU, and uptime.

Gigabytes are GiB (2**30 bytes), as Windows Explorer shows them. A part that cannot be read
is None, so one missing counter never hides the rest.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Drive:
    """A fixed drive, such as "C:", and its space."""

    letter: str
    free_gb: float
    total_gb: float


@dataclass(frozen=True)
class GpuStatus:
    """The first NVIDIA GPU as nvidia-smi reports it; a field it cannot report is None."""

    name: str
    temp_c: int | None
    load_percent: int | None
    vram_used_gb: float | None
    vram_total_gb: float | None
    power_w: float | None


@dataclass(frozen=True)
class PcSnapshot:
    """The PC at one moment."""

    cpu_percent: float | None
    ram_used_gb: float | None
    ram_total_gb: float | None
    drives: tuple[Drive, ...]
    gpu: GpuStatus | None
    uptime_s: int | None
