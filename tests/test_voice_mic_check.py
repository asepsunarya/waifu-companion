"""Finding the microphone that hears Master: no test opens a real stream.

Every test gets a fake sounddevice module listing the devices PortAudio lists on the owner's PC
(each device once per host API, MME names cut to 31 characters, paired Bluetooth headsets under
WDM-KS) and handing out made-up audio instead of recording.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from waifu.config import VadConfig
from waifu.voice.mic_check import (
    InputDevice,
    Levels,
    input_devices,
    level_bar,
    levels,
    measure,
    same_device_name,
    windows_default_input,
)

RATE = 44100
HOST_APIS = [
    {"name": "MME"},
    {"name": "Windows DirectSound"},
    {"name": "Windows WASAPI"},
    {"name": "Windows WDM-KS"},
]
BT_HANDS_FREE = "Headset (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0\r\n;(Polaris-BT))"


def entry(index, name, hostapi, inputs=0, outputs=0, rate=float(RATE)) -> dict:
    return {
        "index": index,
        "name": name,
        "hostapi": hostapi,
        "max_input_channels": inputs,
        "max_output_channels": outputs,
        "default_samplerate": rate,
    }


PC_DEVICES = [
    entry(0, "Microsoft Sound Mapper - Input", 0, inputs=2),
    entry(1, "Headset (Polaris-BT)", 0, inputs=1),
    entry(2, "Microphone (High Definition Aud", 0, inputs=2),
    entry(3, "Microsoft Sound Mapper - Output", 0, outputs=2),
    entry(4, "Headphones (High Definition Aud", 0, outputs=2),
    entry(5, "Primary Sound Capture Driver", 1, inputs=2),
    entry(6, "Headset (Polaris-BT)", 1, inputs=1),
    entry(7, "Microphone (High Definition Audio Device)", 1, inputs=2),
    entry(8, "Headset (Polaris-BT)", 2, inputs=1, rate=16000.0),
    entry(9, "Microphone (High Definition Audio Device)", 2, inputs=2, rate=48000.0),
    entry(10, BT_HANDS_FREE, 3, inputs=1),
    entry(11, "Microphone (HD Audio Microphone)", 3, inputs=2),
]
HEADSET = InputDevice(1, "Headset (Polaris-BT)", "MME", float(RATE))
ONBOARD = InputDevice(2, "Microphone (High Definition Aud", "MME", float(RATE))


class FakeInputStream:
    def __init__(self, sd: "FakeSoundDevice", audio: np.ndarray) -> None:
        self._sd = sd
        self._audio = audio

    def __enter__(self) -> "FakeInputStream":
        self._sd.events.append("start")
        return self

    def __exit__(self, *exc_info) -> None:
        self._sd.events.append("close")

    def read(self, frames: int) -> tuple[np.ndarray, bool]:
        self._sd.events.append(f"read {frames}")
        audio = np.resize(self._audio, frames).astype(np.float32)
        return audio.reshape(-1, 1), False


class FakeSoundDevice:
    """Stands in for the sounddevice module: `devices`, the default input at `default_input`,
    and input streams that hand out `heard[index]` (silence otherwise) instead of recording."""

    def __init__(self, devices=PC_DEVICES, *, default_input=1, heard=None) -> None:
        self.devices = devices
        self.default = SimpleNamespace(device=(default_input, 4))
        self.heard = heard or {}
        self.opened: list[dict] = []
        self.events: list[str] = []

    def query_hostapis(self) -> list[dict]:
        return HOST_APIS

    def query_devices(self, device=None, kind=None):
        if device is None:
            return self.devices
        return self.devices[device]

    def InputStream(self, **settings) -> FakeInputStream:  # noqa: N802 - sounddevice's name
        self.opened.append(settings)
        return FakeInputStream(self, self.heard.get(settings["device"], np.zeros(1)))


def tone(amplitude: float, seconds: float = 0.5, rate: int = RATE) -> np.ndarray:
    t = np.arange(round(seconds * rate)) / rate
    return amplitude * np.sin(2 * np.pi * 220 * t)


def noise(rms: float, seconds: float, rate: int = RATE) -> np.ndarray:
    return np.random.default_rng(7).normal(0.0, rms, round(seconds * rate))


def db(value: float) -> float:
    return 20 * np.log10(value)


# --- the list -------------------------------------------------------------------------------


def test_each_input_is_listed_once_from_the_default_input_s_host_api():
    # Never the default's aliases, outputs, or WDM-KS (exclusive, and paired headsets).
    assert input_devices(FakeSoundDevice()) == [HEADSET, ONBOARD]


def test_the_default_input_s_host_api_wins_when_it_is_another_one():
    sd = FakeSoundDevice(default_input=9)  # a WASAPI entry

    assert input_devices(sd) == [
        InputDevice(8, "Headset (Polaris-BT)", "Windows WASAPI", 16000.0),
        InputDevice(9, "Microphone (High Definition Audio Device)", "Windows WASAPI", 48000.0),
    ]


def test_a_device_only_another_host_api_lists_comes_after_the_default_s():
    usb = entry(12, "Mikrofon USB", 2, inputs=1, rate=48000.0)

    devices = input_devices(FakeSoundDevice([*PC_DEVICES, usb]))

    assert devices == [HEADSET, ONBOARD, InputDevice(12, "Mikrofon USB", "Windows WASAPI", 48000.0)]


def test_without_any_input_device_the_list_is_empty():
    outputs = [device for device in PC_DEVICES if device["max_input_channels"] == 0]

    assert input_devices(FakeSoundDevice(outputs, default_input=-1)) == []


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("Headset (Polaris-BT)", "Headset (Polaris-BT)", True),
        ("Microphone (High Definition Aud", "Microphone (High Definition Audio Device)", True),
        ("Headset", "Headset (Polaris-BT)", False),  # a prefix, but not MME's cut
        ("Microphone (High Definition Aud", "Microphone (HD Audio Microphone)", False),
    ],
)
def test_two_names_are_one_device_when_equal_or_one_is_mme_s_cut_of_the_other(first, second, same):
    assert same_device_name(first, second) is same
    assert same_device_name(second, first) is same


def test_the_windows_default_input_is_named_and_none_without_one():
    assert windows_default_input(FakeSoundDevice()) == "Headset (Polaris-BT)"
    assert windows_default_input(FakeSoundDevice(default_input=-1)) is None


def test_the_config_line_picks_that_one_entry_however_many_names_match(monkeypatch):
    # "Headset (Polaris-BT)" alone matches four input entries: sounddevice refuses it. This runs
    # sounddevice's own name matching over the listing above; no device is queried or opened.
    try:
        import sounddevice
    except (ImportError, OSError):  # the voice extra, or PortAudio, is not installed
        pytest.skip("sounddevice is not installed")
    monkeypatch.setattr(sounddevice, "query_devices", lambda: PC_DEVICES)
    monkeypatch.setattr(sounddevice, "query_hostapis", lambda index: HOST_APIS[index])

    with pytest.raises(ValueError, match="Multiple input devices"):
        sounddevice._get_device_id("Headset (Polaris-BT)", "input", raise_on_error=True)
    for device in (HEADSET, ONBOARD):
        found = sounddevice._get_device_id(device.config_value, "input", raise_on_error=True)
        assert found == device.index
    assert HEADSET.config_line == 'input_device: "Headset (Polaris-BT), MME"'


# --- measuring ------------------------------------------------------------------------------


def test_a_measurement_reads_a_shared_mono_stream_at_the_device_s_rate_and_closes_it():
    sd = FakeSoundDevice()

    measure(sd, HEADSET, 2.0, VadConfig())

    assert sd.opened == [{"samplerate": RATE, "device": 1, "channels": 1, "dtype": "float32"}]
    assert sd.events == ["start", f"read {2 * RATE}", "close"]


def test_speech_above_the_vad_threshold_is_heard():
    room = noise(10 ** (-60 / 20), 2.5)
    room[RATE : RATE + RATE // 2] += tone(0.1)  # "halo Cyrene" at a -23 dB RMS

    found = levels(room.astype(np.float32), RATE, VadConfig())

    assert found.heard_speech
    assert found.loudest_frame_db == pytest.approx(db(0.1 / np.sqrt(2)), abs=0.5)
    assert found.peak_db == pytest.approx(-20, abs=0.5)
    assert found.speech_bar_db == -45  # the room is far below it


def test_the_noise_floor_alone_is_not_speech():
    # The owner's onboard jack: about -51 dB RMS while he spoke.
    found = levels(noise(10 ** (-51 / 20), 3.0).astype(np.float32), RATE, VadConfig())

    assert not found.heard_speech
    assert found.rms_db == pytest.approx(-51, abs=0.5)


def test_a_steady_noise_above_the_threshold_is_not_speech():
    # A fan at -40 dB: the VAD wants margin_db above it, as for the room's noise floor.
    found = levels(noise(10 ** (-40 / 20), 3.0).astype(np.float32), RATE, VadConfig())

    assert not found.heard_speech
    assert found.speech_bar_db == pytest.approx(-40 + 12, abs=1.0)


def test_digital_silence_is_silent_and_not_speech():
    found = levels(np.zeros(RATE, dtype=np.float32), RATE, VadConfig())

    assert found.silent
    assert not found.heard_speech


def test_near_silence_below_any_mic_s_own_noise_counts_as_silent():
    # The owner's Bluetooth headset in its first two seconds: a peak of about -90 dB.
    assert levels(noise(10 ** (-97 / 20), 2.0).astype(np.float32), RATE, VadConfig()).silent
    assert not levels(noise(10 ** (-60 / 20), 2.0).astype(np.float32), RATE, VadConfig()).silent


def test_the_bar_shows_the_loudest_frame_and_where_speech_starts():
    found = Levels(peak_db=-30.0, rms_db=-50.0, loudest_frame_db=-40.0, speech_bar_db=-45.0)

    assert level_bar(found) == "[#############|##...............]"
