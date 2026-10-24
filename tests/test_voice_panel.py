"""VoicePanel: the phone's Suara page over the real MicSource, segmenter, log, and override
store, with fake streams (no real mic is opened) and a store under tmp_path."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from waifu.config import VadConfig
from waifu.voice.audio import MicLevel
from waifu.voice.base import SpeakerEmbedder, Utterance
from waifu.voice.diagnosis import ACCEPTED, UtteranceLog, UtteranceRecord
from waifu.voice.mic_check import InputDevice
from waifu.voice.mic_source import MicSource
from waifu.voice.panel import VoicePanel
from waifu.voice.segmenter import EnergySegmenter
from waifu.voice.speaker import SpeakerVerifier
from waifu.voice_override import VoiceOverride, VoiceOverrideStore

RATE = 16000
NOW = datetime(2026, 10, 24, 19, 30, 5)
HEADSET = "Headset (Polaris-BT), MME"
USB = "Microphone (USB), MME"
DEVICES = [
    InputDevice(1, "Headset (Polaris-BT)", "MME", 44100.0),
    InputDevice(2, "Microphone (USB)", "MME", 48000.0),
]
NAMES = {HEADSET: "Headset (Polaris-BT)", USB: "Microphone (USB)", None: "Microphone (Onboard)"}


class FakeStream:
    def __init__(self, device: object) -> None:
        self.device = device
        self.name: str | None = None
        self.host_api: str | None = None
        self.level: MicLevel | None = MicLevel(-51.24, -40.06, 1.0)
        self.closed = False

    def __enter__(self) -> FakeStream:
        self.name, self.host_api = NAMES[self.device], "MME"
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.closed = True

    def frames(self) -> Iterator[np.ndarray]:
        while not self.closed:
            yield np.full(480, 0.001, dtype=np.float32)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


class NoEmbedder(SpeakerEmbedder):
    def embed(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        raise AssertionError("the panel never verifies a voice")


def listed() -> tuple[list[InputDevice], str | None]:
    return DEVICES, "Microphone (Onboard)"


class Panel:
    def __init__(
        self,
        tmp_path: Path,
        *,
        speaker: bool = True,
        picked: str | None = None,
        listing: Callable[[], tuple[list[InputDevice], str | None]] = listed,
    ):
        self.path = tmp_path / "voice_override.json"
        self.store = VoiceOverrideStore(self.path, now=lambda: NOW)
        self.source = MicSource(
            [HEADSET],
            open_stream=FakeStream,
            sample_rate=RATE,
            frame_ms=30,
            picked=picked,
            listing=listing,
        )
        self.segmenter = EnergySegmenter(RATE, VadConfig())
        self.log = UtteranceLog(now=lambda: NOW)
        self.verifier = (
            SpeakerVerifier(NoEmbedder(), tmp_path / "voiceprint.npy", 0.45) if speaker else None
        )
        self.panel = VoicePanel(
            self.source,
            self.segmenter,
            self.log,
            self.store,
            vad=VadConfig(),
            verifier=self.verifier,
            speaker_threshold=0.45,
            switch_wait_s=0,
        )

    def stored(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


@pytest.fixture
def rig(tmp_path) -> Iterator[Panel]:
    panel = Panel(tmp_path)
    with panel.source:
        yield panel


def test_the_status_shows_the_mic_its_level_the_gate_and_the_choices(rig):
    for frame in [np.full(480, 0.001, dtype=np.float32)] * 3:
        rig.segmenter.feed(frame)

    status = rig.panel.status()

    assert status["mic"] == {"name": "Headset (Polaris-BT)", "host_api": "MME", "value": HEADSET}
    assert status["windows_default"] == "Microphone (Onboard)"
    assert status["preferences"] == [HEADSET, None]
    assert status["override_mic"] is None
    assert status["devices"] == [
        {"value": HEADSET, "name": "Headset (Polaris-BT)", "host_api": "MME"},
        {"value": USB, "name": "Microphone (USB)", "host_api": "MME"},
    ]
    assert status["level"] == {"rms_db": -51.2, "peak_db": -40.1}
    assert status["vad"] == {
        "floor_db": -60.0,
        "bar_db": -45.0,
        "threshold_db": -45.0,
        "margin_db": 12.0,
        "default_threshold_db": -45.0,
        "default_margin_db": 12.0,
        "threshold_bounds": [-70.0, -25.0],
        "margin_bounds": [3.0, 30.0],
    }
    assert status["speaker"] == {
        "enabled": True,
        "threshold": 0.45,
        "default": 0.45,
        "bounds": [0.3, 0.7],
    }
    assert status["switch"] == {"pending": False, "last": None}
    assert status["records"] == []
    json.dumps(status, allow_nan=False)  # what the server sends


def test_the_records_come_newest_first_rounded_with_their_time_of_day(rig):
    utterance = Utterance(np.full(RATE, 0.1, dtype=np.float32), RATE, 0.0)
    rig.log.add(
        UtteranceRecord.of(
            utterance,
            NOW,
            ACCEPTED,
            transcript="Cyrene, nyalakan lampu",
            wake="wake_word",
            score=0.7123,
            threshold=0.45,
            stt_s=0.4321,
            brain_s=2.0,
            tts_s=0.75,
        )
    )

    (record,) = rig.panel.status()["records"]

    assert record == {
        "at": "19:30:05",
        "length_s": 1.0,
        "peak_db": -20.0,
        "rms_db": -20.0,
        "outcome": "accepted",
        "transcript": "Cyrene, nyalakan lampu",
        "wake": "wake_word",
        "score": 0.71,
        "threshold": 0.45,
        "stt_s": 0.43,
        "brain_s": 2.0,
        "tts_s": 0.75,
    }


def test_without_speaker_verification_the_status_says_so(tmp_path):
    rig = Panel(tmp_path, speaker=False)
    with rig.source:
        speaker = rig.panel.status()["speaker"]

    assert speaker == {"enabled": False, "threshold": None, "default": 0.45, "bounds": [0.3, 0.7]}


def test_a_listing_that_fails_shows_no_devices(tmp_path, caplog):
    def failing() -> tuple[list[InputDevice], str | None]:
        raise OSError("PortAudio not initialized")

    rig = Panel(tmp_path, listing=failing)
    with caplog.at_level(logging.WARNING, logger="waifu.voice.panel"), rig.source:
        status = rig.panel.status()

    assert (status["devices"], status["windows_default"]) == ([], None)
    assert "PortAudio not initialized" in caplog.text


def test_a_listed_mic_is_switched_to_and_kept_once_it_opened(rig):
    answer = rig.panel.switch_mic(USB)
    assert (answer.ok, answer.pending) == (True, True)  # she takes it between two frames
    assert not rig.path.exists()

    frames = rig.source.frames()
    next(frames)
    next(frames)

    assert rig.stored() == {"version": 1, "input_device": USB}
    status = rig.panel.status()
    assert status["mic"]["name"] == "Microphone (USB)"
    assert status["override_mic"] == USB
    assert status["switch"] == {
        "pending": False,
        "last": {"ok": True, "message": "Sekarang memakai Microphone (USB)."},
    }


def test_a_mic_not_listed_is_never_asked_for(rig):
    answer = rig.panel.switch_mic("Mic tetangga, MME")

    assert answer.ok is False
    assert "daftar" in answer.message
    assert rig.panel.status()["switch"]["pending"] is False


def test_following_the_config_again_drops_the_phone_s_mic(tmp_path):
    rig = Panel(tmp_path, picked=USB)
    rig.store.update(lambda found: VoiceOverride(USB, speaker_threshold=0.5))
    with rig.source:
        rig.panel.switch_mic(None)
        frames = rig.source.frames()
        next(frames)
        next(frames)

        assert rig.panel.status()["mic"]["name"] == "Headset (Polaris-BT)"
    assert rig.stored() == {"version": 1, "speaker_threshold": 0.5}


def test_a_tuning_is_applied_at_once_and_kept(rig):
    answer = rig.panel.tune(speaker_threshold=0.5, vad_threshold_db=-50, vad_margin_db=None)

    assert answer.ok is True
    assert rig.verifier.threshold == 0.5
    assert (rig.segmenter.vad.threshold_db, rig.segmenter.vad.margin_db) == (-50.0, 12.0)
    assert rig.stored() == {"version": 1, "speaker_threshold": 0.5, "vad_threshold_db": -50.0}


@pytest.mark.parametrize(
    "tuning",
    [
        {"speaker_threshold": 0.8, "vad_threshold_db": None, "vad_margin_db": None},
        {"speaker_threshold": None, "vad_threshold_db": None, "vad_margin_db": 40},
        {"speaker_threshold": None, "vad_threshold_db": None, "vad_margin_db": None},
    ],
)
def test_a_tuning_out_of_bounds_or_empty_is_refused_and_changes_nothing(rig, tuning):
    with pytest.raises(ValueError):
        rig.panel.tune(**tuning)

    assert rig.verifier.threshold == 0.45
    assert rig.segmenter.vad == VadConfig()
    assert not rig.path.exists()


def test_the_speaker_threshold_is_refused_while_verification_is_off(tmp_path):
    rig = Panel(tmp_path, speaker=False)

    with pytest.raises(ValueError, match="Verifikasi suara sedang mati"):
        rig.panel.tune(speaker_threshold=0.5, vad_threshold_db=None, vad_margin_db=None)


def test_reset_puts_the_config_s_values_back_and_keeps_the_mic(rig):
    rig.store.update(lambda found: VoiceOverride(USB, 0.6, -55.0, 8.0))
    rig.verifier.threshold = 0.6
    rig.segmenter.set_gate(-55.0, 8.0)

    answer = rig.panel.reset()

    assert answer.ok is True
    assert rig.verifier.threshold == 0.45
    assert rig.segmenter.vad == VadConfig()
    assert rig.stored() == {"version": 1, "input_device": USB}
