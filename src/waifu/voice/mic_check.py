"""Which microphone hears Master: the input devices and how loud each one hears him.

`waifu mic` lists every input device once and measures each for a few seconds while Master
speaks; `waifu check` names the one Cyrene uses. PortAudio lists a device once per host API
(MME cuts names to 31 characters), so the list keeps one entry per name, from the host API of
the Windows default input (MME on Windows) when it has one. Never listed: the entries that are
only the Windows default under another name, and WDM-KS, which opens a device exclusively (it
would take the mic from a running Cyrene) and lists paired headsets that are not connected.

A measurement listens as Cyrene does, through `MicStream` (a shared input stream, its audio
resampled to the VAD's rate, an error once it stops delivering), keeps its samples in memory
only for their levels, and never saves them. Speech counts as heard by the VAD's own rules.
sounddevice is passed in (the real module in the CLI, a fake in tests), so no test opens a
real stream.
"""

from __future__ import annotations

import itertools
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from waifu.config import AudioConfig, VadConfig
from waifu.voice.audio import MicStream
from waifu.voice.segmenter import ONSET_MS, frame_db

# What PortAudio calls the Windows default input itself, besides its real entry.
DEFAULT_ALIASES = frozenset({"Microsoft Sound Mapper - Input", "Primary Sound Capture Driver"})
EXCLUSIVE_HOST_APIS = frozenset({"Windows WDM-KS"})
MME_NAME_CHARS = 31  # MME cuts a device name to this many characters
# The quiet frames of a measurement stand in for the room's noise floor, as the VAD learns it.
QUIET_PERCENTILE = 10
BAR_WIDTH = 30
BAR_FLOOR_DB = -80.0  # an empty bar; a full one is 0 dB (full scale)
# Below any real microphone's own noise: muted, blocked, or a Bluetooth headset still
# switching to hands-free, which hands out near-silence for a second or two.
SILENT_DB = -80.0


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
    # The loudest level held through the VAD's onset (ONSET_MS of frames in a row), what must
    # reach the bar to start an utterance: a click does not raise it.
    speech_db: float
    speech_bar_db: float  # what a frame must reach to count as speech
    heard_speech: bool  # the VAD would have kept an utterance of it

    @property
    def silent(self) -> bool:
        """(Almost) nothing came in: a muted or blocked microphone, or a Bluetooth
        headset not yet switched to hands-free."""
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


def measure(
    sd: Any,
    device: InputDevice,
    seconds: float,
    audio: AudioConfig,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Levels:
    """Listen to `device` for `seconds` (whole VAD frames) as Cyrene does: its levels. Raises
    `MicrophoneError` when the device stops delivering audio (`clock` times the stall)."""
    vad = audio.vad
    count = max(1, int(seconds * 1000 // vad.frame_ms))
    with MicStream(audio.sample_rate, vad.frame_ms, device.index, sd_module=sd, clock=clock) as mic:
        frames = list(itertools.islice(mic.frames(), count))
    return levels(np.concatenate(frames), audio.sample_rate, vad)


def levels(audio: np.ndarray, sample_rate: int, vad: VadConfig) -> Levels:
    """The levels of mono `audio`. A frame is speech, as for the VAD, once it reaches
    `threshold_db` and stands `margin_db` above the room's noise (the quiet frames)."""
    frame_len = max(1, round(sample_rate * vad.frame_ms / 1000))
    count = max(1, len(audio) // frame_len)
    frame_dbs = [frame_db(audio[i * frame_len : (i + 1) * frame_len]) for i in range(count)]
    noise_db = float(np.percentile(frame_dbs, QUIET_PERCENTILE))
    speech_bar_db = max(vad.threshold_db, noise_db + vad.margin_db)
    onset = _onset_frames(vad)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    return Levels(
        peak_db=20 * math.log10(peak + 1e-10),
        rms_db=frame_db(audio),
        speech_db=max(min(frame_dbs[i : i + onset]) for i in range(max(1, count - onset + 1))),
        speech_bar_db=speech_bar_db,
        heard_speech=_keeps_an_utterance([db >= speech_bar_db for db in frame_dbs], vad),
    )


def _onset_frames(vad: VadConfig) -> int:
    """How many speech frames in a row start an utterance, as `EnergySegmenter` counts them."""
    return max(1, math.ceil(ONSET_MS / vad.frame_ms))


def _keeps_an_utterance(is_speech: list[bool], vad: VadConfig) -> bool:
    """Whether `EnergySegmenter` would keep an utterance of these frames: one starts with
    ONSET_MS of speech in a row, ends after `silence_ms` without, and is kept once it has
    `min_speech_ms` of speech in it (one still going at the end counts)."""
    onset = _onset_frames(vad)
    end = math.ceil(vad.silence_ms / vad.frame_ms)
    needed = math.ceil(vad.min_speech_ms / vad.frame_ms)
    run = voiced = silent = 0
    in_utterance = False
    for speech in is_speech:
        if not in_utterance:
            run = run + 1 if speech else 0
            in_utterance, voiced, silent = run >= onset, run, 0
        elif speech:
            voiced, silent = voiced + 1, 0
        else:
            silent += 1
            if silent >= end:
                in_utterance, run = False, 0
        if in_utterance and voiced >= needed:
            return True
    return False


def level_bar(found: Levels) -> str:
    """The speech level as a bar from BAR_FLOOR_DB to 0 dB, `|` where speech starts."""

    def position(db: float) -> int:
        share = (db - BAR_FLOOR_DB) / -BAR_FLOOR_DB
        return min(BAR_WIDTH, max(0, round(share * BAR_WIDTH)))

    cells = ["#" if i < position(found.speech_db) else "." for i in range(BAR_WIDTH)]
    cells.insert(position(found.speech_bar_db), "|")
    return f"[{''.join(cells)}]"
