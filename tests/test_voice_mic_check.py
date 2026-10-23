"""Finding the microphone that hears Master: no test opens a real stream.

Every test gets a fake sounddevice module listing the devices PortAudio lists on the owner's PC
(each device once per host API, MME names cut to 31 characters, paired Bluetooth headsets under
WDM-KS) and handing out made-up audio instead of recording.
"""

import itertools
from types import SimpleNamespace

import numpy as np
import pytest

from waifu.config import AudioConfig, VadConfig
from waifu.voice.audio import MicrophoneError
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
from waifu.voice.segmenter import EnergySegmenter

RATE = 44100
VAD_RATE = 16000  # audio.sample_rate: what the VAD hears, resampled
FRAME_LEN = VAD_RATE * 30 // 1000  # one VAD frame (vad.frame_ms)
DELIVERED_S = 4.0  # what a fake stream hands out once started: under MicStream's 5 s queue
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
    """A callback stream that, once started, calls back with `audio` repeated for DELIVERED_S in
    blocks of `blocksize`, then no more, as a mic that went quiet; None never calls back."""

    def __init__(self, sd: "FakeSoundDevice", audio: np.ndarray | None, settings: dict) -> None:
        self._sd = sd
        self._audio = audio
        self._settings = settings
        self.active = False

    def start(self) -> None:
        self._sd.events.append("start")
        self.active = True
        if self._audio is None:
            return
        rate, blocksize = self._settings["samplerate"], self._settings["blocksize"]
        audio = np.resize(self._audio, round(rate * DELIVERED_S)).astype(np.float32)
        for start in range(0, len(audio), blocksize):
            block = audio[start : start + blocksize].reshape(-1, 1)
            self._settings["callback"](block, len(block), None, 0)

    def stop(self) -> None:
        self._sd.events.append("stop")
        self.active = False

    def close(self) -> None:
        self._sd.events.append("close")


class FakeSoundDevice:
    """Stands in for the sounddevice module: `devices`, the default input at `default_input`,
    and input streams that hand out `heard[index]` (silence otherwise; None: nothing at all)
    instead of recording."""

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
        self.opened.append({key: value for key, value in settings.items() if key != "callback"})
        return FakeInputStream(self, self.heard.get(settings["device"], np.zeros(1)), settings)


def tone(amplitude: float, seconds: float = 0.5, rate: int = RATE) -> np.ndarray:
    t = np.arange(round(seconds * rate)) / rate
    return amplitude * np.sin(2 * np.pi * 220 * t)


def noise(rms: float, seconds: float, rate: int = RATE) -> np.ndarray:
    return np.random.default_rng(7).normal(0.0, rms, round(seconds * rate))


def db(value: float) -> float:
    return 20 * np.log10(value)


def amplitude(rms_db: float) -> float:
    """The amplitude of a sine at `rms_db` RMS."""
    return 10 ** (rms_db / 20) * np.sqrt(2)


def onboard_jack_with(bursts: list[tuple[int, int]], burst_db: float) -> np.ndarray:
    """Three seconds of the owner's onboard jack (-51 dB noise) at VAD_RATE, with a tone at
    `burst_db` RMS in each (first frame, frames) of `bursts`, aligned to the VAD's frames."""
    room = noise(10 ** (-51 / 20), 3.0, VAD_RATE)
    for first, frames in bursts:
        span = slice(first * FRAME_LEN, (first + frames) * FRAME_LEN)
        room[span] += tone(amplitude(burst_db), frames * FRAME_LEN / VAD_RATE, VAD_RATE)
    return room.astype(np.float32)


def vad_keeps_an_utterance(audio: np.ndarray) -> bool:
    """Whether the real VAD cuts an utterance out of `audio`, followed by a second of the same
    room so that one still going can end."""
    after = noise(10 ** (-51 / 20), 33 * FRAME_LEN / VAD_RATE, VAD_RATE).astype(np.float32)
    segmenter = EnergySegmenter(VAD_RATE, VadConfig())
    frames = np.concatenate([audio, after]).reshape(-1, FRAME_LEN)
    return any(segmenter.feed(frame) is not None for frame in frames)


class SteppingClock:
    """A monotonic clock that moves on a second at every reading."""

    def __init__(self) -> None:
        self._readings = itertools.count()

    def __call__(self) -> float:
        return float(next(self._readings))


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


def test_a_measurement_listens_as_cyrene_does_in_a_shared_stream_and_closes_it():
    # MicStream: the device's own rate, VAD-sized blocks, resampled to audio.sample_rate.
    speaking = noise(10 ** (-60 / 20), 2.0)
    speaking[RATE // 2 : RATE] += tone(0.1)
    sd = FakeSoundDevice(heard={1: speaking})

    found = measure(sd, HEADSET, 2.0, AudioConfig())

    assert sd.opened == [
        {
            "samplerate": RATE,
            "blocksize": RATE * 30 // 1000,
            "device": 1,
            "channels": 1,
            "dtype": "float32",
        }
    ]
    assert sd.events == ["start", "stop", "close"]
    assert found.heard_speech
    assert found.peak_db == pytest.approx(-20, abs=0.5)


def test_a_mic_that_stops_delivering_ends_the_measurement_instead_of_waiting_forever():
    # A Bluetooth headset that dropped its hands-free link, or a mic pulled out mid-way.
    sd = FakeSoundDevice(heard={1: None})

    with pytest.raises(MicrophoneError, match="Mikrofon terputus"):
        measure(sd, HEADSET, 2.0, AudioConfig(), clock=SteppingClock())

    assert sd.events == ["start", "stop", "close"]


def test_speech_above_the_vad_threshold_is_heard():
    room = noise(10 ** (-60 / 20), 2.5)
    room[RATE : RATE + RATE // 2] += tone(0.1)  # "halo Cyrene" at a -23 dB RMS

    found = levels(room.astype(np.float32), RATE, VadConfig())

    assert found.heard_speech
    assert found.speech_db == pytest.approx(db(0.1 / np.sqrt(2)), abs=0.5)
    assert found.peak_db == pytest.approx(-20, abs=0.5)
    assert found.speech_bar_db == -45  # the room is far below it


def test_the_noise_floor_alone_is_not_speech():
    # The owner's onboard jack: about -51 dB RMS while he spoke.
    found = levels(noise(10 ** (-51 / 20), 3.0).astype(np.float32), RATE, VadConfig())

    assert not found.heard_speech
    assert found.rms_db == pytest.approx(-51, abs=0.5)


@pytest.mark.parametrize(
    ("bursts", "burst_db", "heard"),
    [
        ([(40, 1)], -30.0, False),  # a click: one loud frame
        ([(10, 2), (30, 2), (50, 2), (70, 2), (90, 2)], -36.0, False),  # never 90 ms in a row
        ([(20, 3), (30, 3), (40, 3)], -36.0, False),  # one utterance, 270 ms of speech in it
        ([(20, 3), (30, 3), (40, 3), (50, 3)], -36.0, True),  # 360 ms: kept
        ([(30, 17)], -36.0, True),  # half a second of speech
    ],
    ids=["click", "short_bursts", "too_little_speech", "enough_speech", "sustained"],
)
def test_speech_is_heard_only_when_the_vad_would_keep_an_utterance_of_it(bursts, burst_db, heard):
    # The VAD starts an utterance with ONSET_MS of speech in a row and keeps it with
    # min_speech_ms of speech in it: a click or a cough alone never reaches Cyrene.
    audio = onboard_jack_with(bursts, burst_db)

    found = levels(audio, VAD_RATE, VadConfig())

    assert found.speech_bar_db == pytest.approx(-39, abs=1.0)
    assert found.heard_speech is heard
    assert vad_keeps_an_utterance(audio) is heard


def test_the_speech_level_is_what_stays_up_for_the_vad_s_onset_not_a_click():
    clicked = onboard_jack_with([(20, 17), (60, 1)], -36.0)
    clicked[60 * FRAME_LEN : 61 * FRAME_LEN] *= 30  # a click near full scale

    found = levels(clicked, VAD_RATE, VadConfig())

    assert found.speech_db == pytest.approx(-36, abs=1.0)


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


def test_the_bar_shows_the_speech_level_and_where_speech_starts():
    found = Levels(
        peak_db=-30.0, rms_db=-50.0, speech_db=-40.0, speech_bar_db=-45.0, heard_speech=True
    )

    assert level_bar(found) == "[#############|##...............]"
