"""Energy-based utterance segmenter.

Cuts the continuous microphone stream into utterances: a frame counts as
speech when it is loud both in absolute terms (`threshold_db`) and relative to
an adaptive noise floor (`margin_db` above a slow average of quiet frames), so
a fan or a humming fridge raises the bar instead of triggering endless speech.
The bar also never sits below `margin_db` above the quietest frame of the last
few seconds: a voice dips between syllables, so sound that stays up that long
is the room getting louder (an AC or PC fans starting up), even when it is
above `threshold_db` and the slow average has not learned it yet.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

import numpy as np

from waifu.config import VadConfig
from waifu.voice.base import Utterance

FLOOR_ALPHA = 0.05
NOISE_WINDOW_S = 5.0
ONSET_MS = 90
TRAILING_SILENCE_MS = 150


def frame_db(frame: np.ndarray) -> float:
    """Level of one frame in dBFS."""
    rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
    return 20 * math.log10(rms + 1e-10)


class EnergySegmenter:
    """Turns fixed-size frames into `Utterance`s, one `feed` call per frame.

    `reset()` drops any partial utterance; the learned noise floor is kept
    because it describes the room, not the utterance. `relearn()` forgets it too, for a new mic.
    `floor_db` and `bar_db` are the last frame's, for other threads to read; `set_gate` changes
    the threshold and margin from any thread, counted from the next frame. A segment dropped for
    too little speech goes to `on_too_short`, on the feeding thread.
    """

    def __init__(
        self,
        sample_rate: int,
        vad: VadConfig,
        clock: Callable[[], float] = time.monotonic,
        *,
        on_too_short: Callable[[Utterance], None] | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._vad = vad
        self._clock = clock
        self._on_too_short = on_too_short
        self.floor_db: float | None = None
        self.bar_db: float | None = None
        self._onset_frames = max(1, math.ceil(ONSET_MS / vad.frame_ms))
        self._end_frames = math.ceil(vad.silence_ms / vad.frame_ms)
        self._trailing_frames = min(self._end_frames, math.ceil(TRAILING_SILENCE_MS / vad.frame_ms))
        self._min_voiced_frames = math.ceil(vad.min_speech_ms / vad.frame_ms)
        self._max_samples = round(vad.max_speech_s * sample_rate)
        self._pre_roll: deque[np.ndarray] = deque(maxlen=math.ceil(vad.pre_roll_ms / vad.frame_ms))
        self._recent_db: deque[float] = deque(
            maxlen=math.ceil(NOISE_WINDOW_S * 1000 / vad.frame_ms)
        )
        self._floor_db: float | None = None
        self.reset()

    @property
    def busy(self) -> bool:
        """Whether someone may be speaking: an utterance, or its onset, is under way."""
        return bool(self._frames or self._candidate)

    def reset(self) -> None:
        self._pre_roll.clear()
        self._candidate: list[np.ndarray] = []
        self._frames: list[np.ndarray] = []
        self._samples = 0
        self._voiced_frames = 0
        self._silent_run = 0
        self._started_at = 0.0

    def relearn(self) -> None:
        """`reset()`, and forget the noise floor: after a switch it describes the old mic."""
        self.reset()
        self._floor_db = None
        self._recent_db.clear()
        self.floor_db = self.bar_db = None

    def set_gate(self, threshold_db: float, margin_db: float) -> None:
        """Use this threshold and margin from the next frame; the timings stay as built."""
        self._vad = self._vad.model_copy(
            update={"threshold_db": threshold_db, "margin_db": margin_db}
        )

    def feed(self, frame: np.ndarray) -> Utterance | None:
        frame = np.asarray(frame, dtype=np.float32)
        is_speech = self._classify(frame)
        if self._frames:
            return self._continue(frame, is_speech)
        self._listen(frame, is_speech)
        return None

    def _classify(self, frame: np.ndarray) -> bool:
        vad = self._vad  # once per frame: `set_gate` may swap it from another thread
        db = frame_db(frame)
        self._recent_db.append(db)
        if self._floor_db is None:
            self._floor_db = db
        floor = self._floor_db
        if len(self._recent_db) == self._recent_db.maxlen:
            floor = max(floor, min(self._recent_db))
        bar = _speech_bar(vad, floor)
        self.floor_db, self.bar_db = floor, bar
        # Only frames quiet by the slow floor's own measure teach it, so the dips
        # of a long sentence never drag the floor up into the voice.
        if db < _speech_bar(vad, self._floor_db):
            self._floor_db += FLOOR_ALPHA * (db - self._floor_db)
        return db >= bar

    def _listen(self, frame: np.ndarray, is_speech: bool) -> None:
        if not is_speech:
            self._pre_roll.extend(self._candidate)
            self._pre_roll.append(frame)
            self._candidate = []
            return
        self._candidate.append(frame)
        if len(self._candidate) >= self._onset_frames:
            self._frames = [*self._pre_roll, *self._candidate]
            self._samples = sum(len(f) for f in self._frames)
            self._voiced_frames = len(self._candidate)
            self._started_at = self._clock()
            self._pre_roll.clear()
            self._candidate = []

    def _continue(self, frame: np.ndarray, is_speech: bool) -> Utterance | None:
        self._frames.append(frame)
        self._samples += len(frame)
        if is_speech:
            self._voiced_frames += 1
            self._silent_run = 0
        else:
            self._silent_run += 1

        if self._silent_run >= self._end_frames:
            cut = len(self._frames) - self._silent_run + self._trailing_frames
            self._pre_roll.extend(self._frames[cut:])
            del self._frames[cut:]
            return self._finish()
        if self._samples >= self._max_samples:
            return self._finish()
        return None

    def _finish(self) -> Utterance | None:
        frames, voiced, started_at = self._frames, self._voiced_frames, self._started_at
        self._frames, self._samples, self._voiced_frames, self._silent_run = [], 0, 0, 0
        if voiced >= self._min_voiced_frames:
            return self._segment(frames, started_at)
        if self._on_too_short is not None:
            self._on_too_short(self._segment(frames, started_at))
        return None

    def _segment(self, frames: list[np.ndarray], started_at: float) -> Utterance:
        return Utterance(
            audio=np.concatenate(frames), sample_rate=self._sample_rate, started_at=started_at
        )


def _speech_bar(vad: VadConfig, floor_db: float) -> float:
    return max(vad.threshold_db, floor_db + vad.margin_db)
