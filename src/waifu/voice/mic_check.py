"""Which microphone hears Master: the input devices and how loud each one hears him.

`waifu mic` lists every input device once and measures each for a few seconds while Master
speaks; `waifu check` names the one Cyrene uses. PortAudio lists a device once per host API
(MME cuts names to 31 characters), so the list keeps one entry per name, from the host API of
the Windows default input (MME on Windows) when it has one. Never listed: the entries that are
only the Windows default under another name, and WDM-KS, which opens a device exclusively (it
would take the mic from a running Cyrene) and lists paired headsets that are not connected.

A measurement opens a shared input stream, keeps its samples in memory only for their levels,
and never saves them. sounddevice is passed in (the real module in the CLI, a fake in tests),
so no test opens a real stream.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from waifu.config import VadConfig
from waifu.voice.segmenter import frame_db

# What PortAudio calls the Windows default input itself, besides its real entry.
DEFAULT_ALIASES = frozenset({"Microsoft Sound Mapper - Input", "Primary Sound Capture Driver"})
EXCLUSIVE_HOST_APIS = frozenset({"Windows WDM-KS"})
MME_NAME_CHARS = 31  # MME cuts a device name to this many characters
# The quiet frames of a measurement stand in for the room's noise floor, as the VAD learns it.
QUIET_PERCENTILE = 10
BAR_WIDTH = 30
BAR_FLOOR_DB = -80.0  # an empty bar; a full one is 0 dB (full scale)
SILENT_DB = -120.0  # quieter than any real microphone: no sound reaches it at all


@dataclass(frozen=True)
class InputDevice:
    """An input device as one host API lists it."""

    index: int
    name: str
    host_api: str
    sample_rate: float

    @property
    def config_value(self) -> str:
        """What `audio.input_device` takes to pick exactly this entry: sounddevice matches a
        device's name and host API joined by a comma."""
        return f"{self.name}, {self.host_api}"

    @property
    def config_line(self) -> str:
        """The `input_device` line under `audio:` in config.yaml (a YAML double-quoted
        string)."""
        return f"input_device: {json.dumps(self.config_value, ensure_ascii=False)}"


@dataclass(frozen=True)
class Levels:
    """How loud a microphone heard the room, in dBFS."""

    peak_db: float  # the loudest sample
    rms_db: float  # the whole measurement
    loudest_frame_db: float  # the loudest VAD frame: what the VAD compares
    speech_bar_db: float  # what a frame must reach to count as speech

    @property
    def heard_speech(self) -> bool:
        return self.loudest_frame_db >= self.speech_bar_db

    @property
    def silent(self) -> bool:
        """Nothing at all came in: a muted, disabled, or blocked microphone."""
        return self.peak_db <= SILENT_DB


def same_device_name(first: str, second: str) -> bool:
    """Whether two host APIs' names are one device: equal, or one is MME's cut of the other."""
    shorter, longer = sorted((first, second), key=len)
    return shorter == longer or (len(shorter) == MME_NAME_CHARS and longer.startswith(shorter))


def input_devices(sd: Any) -> list[InputDevice]:
    """Every input device once (see the module docstring), the default input's host API first,
    each host API in PortAudio's order."""
    host_apis = [api["name"] for api in sd.query_hostapis()]
    default_index = sd.default.device[0]
    entries = [
        info
        for info in sd.query_devices()
        if info["max_input_channels"] > 0
        and info["name"] not in DEFAULT_ALIASES
        and host_apis[info["hostapi"]] not in EXCLUSIVE_HOST_APIS
    ]
    preferred = next((info["hostapi"] for info in entries if info["index"] == default_index), None)
    entries.sort(key=lambda info: info["hostapi"] != preferred)  # stable: order kept otherwise
    devices: list[InputDevice] = []
    for info in entries:
        if not any(same_device_name(info["name"], device.name) for device in devices):
            devices.append(
                InputDevice(
                    info["index"],
                    info["name"],
                    host_apis[info["hostapi"]],
                    info["default_samplerate"],
                )
            )
    return devices


def windows_default_input(sd: Any) -> str | None:
    """The name of the Windows default recording device; None when Windows has none."""
    index = sd.default.device[0]
    return None if index < 0 else sd.query_devices(index)["name"]


def measure(sd: Any, device: InputDevice, seconds: float, vad: VadConfig) -> Levels:
    """Listen to `device` for `seconds` in a shared stream at its own rate: its levels."""
    sample_rate = round(device.sample_rate)
    with sd.InputStream(
        samplerate=sample_rate, device=device.index, channels=1, dtype="float32"
    ) as stream:
        audio, _overflowed = stream.read(max(1, round(sample_rate * seconds)))
    return levels(np.asarray(audio, dtype=np.float32)[:, 0], sample_rate, vad)


def levels(audio: np.ndarray, sample_rate: int, vad: VadConfig) -> Levels:
    """The levels of mono `audio`. A frame is speech, as for the VAD, once it reaches
    `threshold_db` and stands `margin_db` above the room's noise (the quiet frames)."""
    frame_len = max(1, round(sample_rate * vad.frame_ms / 1000))
    count = max(1, len(audio) // frame_len)
    frame_dbs = [frame_db(audio[i * frame_len : (i + 1) * frame_len]) for i in range(count)]
    noise_db = float(np.percentile(frame_dbs, QUIET_PERCENTILE))
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    return Levels(
        peak_db=20 * math.log10(peak + 1e-10),
        rms_db=frame_db(audio),
        loudest_frame_db=max(frame_dbs),
        speech_bar_db=max(vad.threshold_db, noise_db + vad.margin_db),
    )


def level_bar(found: Levels) -> str:
    """The loudest frame as a bar from BAR_FLOOR_DB to 0 dB, `|` where speech starts."""

    def position(db: float) -> int:
        share = (db - BAR_FLOOR_DB) / -BAR_FLOOR_DB
        return min(BAR_WIDTH, max(0, round(share * BAR_WIDTH)))

    cells = ["#" if i < position(found.loudest_frame_db) else "." for i in range(BAR_WIDTH)]
    cells.insert(position(found.speech_bar_db), "|")
    return f"[{''.join(cells)}]"
