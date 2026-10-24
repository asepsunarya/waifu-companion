"""What Cyrene heard lately and what she did with each utterance (SPEC, diagnosa suara (v1.47)).

The voice loop records each utterance where it already decides (`Assistant.handle_utterance`):
its length and level, the transcript, the wake gate's reason, the speaker score against the
threshold, the outcome, and how long Whisper, the brain, and her voice took. The segmenter
records the segments it drops for too little speech. The phone's Suara page shows the records,
and "tadi kamu denger apa?" tells the last one. They live in memory only, at most RECORDS of
them; the samples are measured and never kept. Each record carries guest mode's generation as
it was heard, and only the current generation's are given out, so nothing heard before guest
mode switched is shown or told after it.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from waifu.voice.base import Utterance
from waifu.voice.segmenter import frame_db

RECORDS = 50

# What she did with an utterance.
ACCEPTED = "accepted"  # it was for her and she answered it
NOT_OWNER = "not-owner"  # speaker verification refused it (or could not run)
NO_WAKE = "no-wake"  # words without her name, outside the follow-up window
EMPTY = "empty"  # Whisper found no words in it
TOO_SHORT = "too-short"  # the segmenter dropped it for too little speech
OUTCOMES = (ACCEPTED, NOT_OWNER, NO_WAKE, EMPTY, TOO_SHORT)


@dataclass(frozen=True)
class UtteranceRecord:
    """One utterance and what she did with it; what is not known (yet) is None."""

    at: datetime  # the wall clock as it was handled
    length_s: float
    peak_db: float
    rms_db: float
    outcome: str  # one of OUTCOMES
    transcript: str | None = None
    wake: str | None = None  # the wake gate's reason, or "alarm" for an alarm phrase
    score: float | None = None  # speaker verification's similarity
    threshold: float | None = None  # the score it needed
    stt_s: float | None = None
    brain_s: float | None = None
    tts_s: float | None = None
    heard_request: bool = False  # it asked what she heard

    @classmethod
    def of(cls, utterance: Utterance, at: datetime, outcome: str, **known: Any) -> UtteranceRecord:
        """The record of `utterance`, measured, with what else is `known` about it."""
        audio = utterance.audio
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        return cls(
            at,
            len(audio) / utterance.sample_rate,
            20 * math.log10(peak + 1e-10),
            frame_db(audio),
            outcome,
            **known,
        )


class UtteranceLog:
    """The last `size` records, safe from any thread: the voice loop adds, the phone reads."""

    def __init__(
        self,
        size: int = RECORDS,
        *,
        marker: Callable[[], object] | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        """`marker` is guest mode's generation (None: never moves); `now` the wall clock."""
        self._marker = marker or (lambda: None)
        self._now = now
        self._records: deque[tuple[object, UtteranceRecord]] = deque(maxlen=size)
        self._lock = threading.Lock()

    def add(self, record: UtteranceRecord) -> None:
        marker = self._marker()
        with self._lock:
            self._records.append((marker, record))

    def add_too_short(self, utterance: Utterance) -> None:
        """Record a segment the segmenter dropped for too little speech."""
        self.add(UtteranceRecord.of(utterance, self._now(), TOO_SHORT))

    def records(self) -> tuple[UtteranceRecord, ...]:
        """What was heard since guest mode last switched, newest first."""
        marker = self._marker()
        with self._lock:
            kept = list(self._records)
        return tuple(record for stamp, record in reversed(kept) if stamp == marker)

    def last_heard(self) -> UtteranceRecord | None:
        """The newest record that went past the VAD and did not ask what she heard, if any: a
        cough or a click between would hide the utterance Master means."""
        return next(
            (
                record
                for record in self.records()
                if record.outcome != TOO_SHORT and not record.heard_request
            ),
            None,
        )
