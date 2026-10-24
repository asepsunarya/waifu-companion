"""The phone's Suara page (SPEC, diagnosa suara (v1.47)): what the mic hears, what she did with
each utterance, and the mic and the voice settings Master may change from there.

`VoicePanel` is the phone server's `VoiceBackend`, called on its request threads. It reads the
`MicSource`, the segmenter, and the utterance log, which are safe to read from any thread, and
changes only what is safe from one: a mic switch is handed to `MicSource.request_switch` (which
runs it on `run`'s thread), the speaker threshold is one float written whole, and the VAD's gate
is one `VadConfig` swapped whole. Every change is kept in the override file, never config.yaml;
"reset" puts the config's settings back and keeps the mic picked.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from typing import Any, Protocol

from waifu.config import VadConfig
from waifu.phone.server import VoiceAnswer
from waifu.voice.diagnosis import UtteranceLog, UtteranceRecord
from waifu.voice.mic_source import MicSource
from waifu.voice.segmenter import EnergySegmenter
from waifu.voice_override import (
    SPEAKER_BOUNDS,
    VAD_MARGIN_BOUNDS,
    VAD_THRESHOLD_BOUNDS,
    VoiceOverride,
    VoiceOverrideStore,
    checked_tuning,
)

logger = logging.getLogger(__name__)

SWITCH_WAIT_S = 3.0  # how long the page's request waits for `run`'s thread to switch
NOT_LISTED = "Mic itu tidak ada di daftar. Muat ulang halaman ini."
NOTHING_TO_TUNE = "Tidak ada setelan yang diubah."
SPEAKER_OFF = "Verifikasi suara sedang mati."
TUNED = "Setelan suara dipakai dan disimpan."
RESET = "Setelan suara kembali seperti di config."
NOT_KEPT = "Setelan suara dipakai sekarang, tapi tidak bisa disimpan: {problem}"
_SWITCH_STATES_OK = {"done": True, "pending": True, "failed": False, "busy": False}


class Threshold(Protocol):
    """The speaker verifier's threshold (`SpeakerVerifier.threshold`), read at each verify."""

    threshold: float


class VoicePanel:
    def __init__(
        self,
        source: MicSource,
        segmenter: EnergySegmenter,
        log: UtteranceLog,
        store: VoiceOverrideStore,
        *,
        vad: VadConfig,
        verifier: Threshold | None,
        speaker_threshold: float,
        switch_wait_s: float = SWITCH_WAIT_S,
    ) -> None:
        """`vad` and `speaker_threshold` are the config's (what "reset" puts back); `verifier`
        is None while speaker verification is off."""
        self._source = source
        self._segmenter = segmenter
        self._log = log
        self._store = store
        self._vad = vad
        self._verifier = verifier
        self._speaker_threshold = speaker_threshold
        self._switch_wait_s = switch_wait_s

    def status(self) -> dict[str, Any]:
        mic = self._source.status()
        try:
            devices, default = self._source.devices()
        except Exception as exc:  # PortAudio could not list them right now
            logger.warning("Could not list the microphones for the phone: %s", exc)
            devices, default = [], None
        gate = self._segmenter.vad
        last = mic.last_switch
        return {
            "mic": None if mic.mic is None else dataclasses.asdict(mic.mic),
            "windows_default": default,
            "preferences": mic.preferences,
            "override_mic": mic.picked,
            "devices": [
                {"value": device.config_value, "name": device.name, "host_api": device.host_api}
                for device in devices
            ],
            "level": None
            if mic.level is None
            else {"rms_db": _round(mic.level.rms_db, 1), "peak_db": _round(mic.level.peak_db, 1)},
            "vad": {
                "floor_db": _round(self._segmenter.floor_db, 1),
                "bar_db": _round(self._segmenter.bar_db, 1),
                "threshold_db": float(gate.threshold_db),
                "margin_db": float(gate.margin_db),
                "default_threshold_db": float(self._vad.threshold_db),
                "default_margin_db": float(self._vad.margin_db),
                "threshold_bounds": list(VAD_THRESHOLD_BOUNDS),
                "margin_bounds": list(VAD_MARGIN_BOUNDS),
            },
            "speaker": {
                "enabled": self._verifier is not None,
                "threshold": None
                if self._verifier is None
                else _round(self._verifier.threshold, 2),
                "default": _round(self._speaker_threshold, 2),
                "bounds": list(SPEAKER_BOUNDS),
            },
            "switch": {
                "pending": mic.switching,
                "last": None
                if last is None
                else {"ok": _SWITCH_STATES_OK[last.state], "message": last.message},
            },
            "records": [_record_json(record) for record in self._log.records()],
        }

    def switch_mic(self, device: str | None) -> VoiceAnswer:
        """Only a mic the page listed (or None: follow the config); kept once it opened."""
        if device is not None:
            listed = {found.config_value for found in self._source.devices()[0]}
            if device not in listed:
                return VoiceAnswer(False, NOT_LISTED)
        result = self._source.request_switch(
            device, wait_s=self._switch_wait_s, on_switched=self._keep_mic
        )
        return VoiceAnswer(
            _SWITCH_STATES_OK[result.state], result.message, result.state == "pending"
        )

    def tune(
        self,
        *,
        speaker_threshold: float | None,
        vad_threshold_db: float | None,
        vad_margin_db: float | None,
    ) -> VoiceAnswer:
        speaker, threshold, margin = checked_tuning(
            speaker_threshold=speaker_threshold,
            vad_threshold_db=vad_threshold_db,
            vad_margin_db=vad_margin_db,
        )
        if speaker is None and threshold is None and margin is None:
            raise ValueError(NOTHING_TO_TUNE)
        if speaker is not None and self._verifier is None:
            raise ValueError(SPEAKER_OFF)
        if speaker is not None and self._verifier is not None:
            self._verifier.threshold = speaker
        gate = self._segmenter.vad
        if threshold is not None or margin is not None:
            self._segmenter.set_gate(
                gate.threshold_db if threshold is None else threshold,
                gate.margin_db if margin is None else margin,
            )
        logger.info(
            "Voice settings changed on the phone: speaker %s, VAD threshold %s, margin %s",
            speaker,
            threshold,
            margin,
        )

        def tuned(found: VoiceOverride) -> VoiceOverride:
            return VoiceOverride(
                found.input_device,
                found.speaker_threshold if speaker is None else speaker,
                found.vad_threshold_db if threshold is None else threshold,
                found.vad_margin_db if margin is None else margin,
            )

        return self._kept(tuned, TUNED)

    def reset(self) -> VoiceAnswer:
        if self._verifier is not None:
            self._verifier.threshold = self._speaker_threshold
        self._segmenter.set_gate(self._vad.threshold_db, self._vad.margin_db)
        logger.info("Voice settings put back to the config's on the phone")
        return self._kept(lambda found: VoiceOverride(found.input_device), RESET)

    def _kept(self, change: Callable[[VoiceOverride], VoiceOverride], message: str) -> VoiceAnswer:
        try:
            self._store.update(change)
        except OSError as exc:
            logger.warning("Could not keep the voice settings in %s: %s", self._store.path, exc)
            return VoiceAnswer(False, NOT_KEPT.format(problem=exc))
        return VoiceAnswer(True, message)

    def _keep_mic(self, device: str | None) -> None:
        """Keep the mic that opened (None: drop the phone's pick); on `run`'s thread."""
        self._store.update(lambda found: dataclasses.replace(found, input_device=device))


def _round(value: float | None, places: int) -> float | None:
    return None if value is None else round(float(value), places)


def _record_json(record: UtteranceRecord) -> dict[str, Any]:
    return {
        "at": f"{record.at:%H:%M:%S}",
        "length_s": _round(record.length_s, 2),
        "peak_db": _round(record.peak_db, 1),
        "rms_db": _round(record.rms_db, 1),
        "outcome": record.outcome,
        "transcript": record.transcript,
        "wake": record.wake,
        "score": _round(record.score, 2),
        "threshold": _round(record.threshold, 2),
        "stt_s": _round(record.stt_s, 2),
        "brain_s": _round(record.brain_s, 2),
        "tts_s": _round(record.tts_s, 2),
    }
