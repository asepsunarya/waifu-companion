"""Contracts for the audio pipeline.

All audio crossing these interfaces is mono float32 numpy in [-1.0, 1.0].
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Utterance:
    audio: np.ndarray
    sample_rate: int
    started_at: float

    @property
    def duration(self) -> float:
        return len(self.audio) / self.sample_rate


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str


class SpeechToText(ABC):
    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        """Return the spoken text; an empty string means nothing usable was heard."""


class TextToSpeech(ABC):
    @abstractmethod
    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]:
        """Return (audio, sample_rate) for `text` spoken in `language` ("id", "ja", ...)."""


class SpeakerEmbedder(ABC):
    @abstractmethod
    def embed(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Return a 1-D speaker embedding for the utterance."""
