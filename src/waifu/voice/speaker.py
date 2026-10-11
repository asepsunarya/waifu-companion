"""Owner-only speaker verification.

The owner's voiceprint is the renormalised mean of L2-normalised embeddings
from a few enrollment recordings; an utterance is accepted when the cosine
similarity of its embedding to the voiceprint reaches the threshold.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import numpy as np

from waifu.voice.base import SpeakerEmbedder

logger = logging.getLogger(__name__)

MIN_ENROLL_SAMPLES = 3
MODEL_SAMPLE_RATE = 16000

SpeakerEncoder = Callable[[np.ndarray], np.ndarray]
"""Turns 16 kHz mono float32 audio into an embedding of any shape."""

EncoderLoader = Callable[[str, Path, str], SpeakerEncoder]
"""(model_source, model_dir, device) -> SpeakerEncoder."""


class SpeakerError(Exception):
    """Speaker verification cannot run, e.g. no usable voiceprint is enrolled."""


class SpeakerVerifier:
    def __init__(self, embedder: SpeakerEmbedder, voiceprint_path: Path, threshold: float) -> None:
        self._embedder = embedder
        self._voiceprint_path = Path(voiceprint_path)
        self.threshold = threshold
        self._voiceprint: np.ndarray | None = None

    @property
    def enrolled(self) -> bool:
        return self._voiceprint is not None or self._voiceprint_path.exists()

    def enroll(self, samples: list[np.ndarray], sample_rate: int) -> np.ndarray:
        """Build the voiceprint from the owner's recordings, save it, and return it."""
        if len(samples) < MIN_ENROLL_SAMPLES:
            raise SpeakerError(
                f"Butuh minimal {MIN_ENROLL_SAMPLES} rekaman suara untuk mendaftarkan "
                f"suara Master, baru ada {len(samples)}."
            )
        embeddings = [self._normalised_embedding(audio, sample_rate) for audio in samples]
        voiceprint = _l2_normalise(np.mean(embeddings, axis=0))
        self._voiceprint_path.parent.mkdir(parents=True, exist_ok=True)
        # A file handle keeps the exact configured name; np.save on a path would append ".npy".
        with self._voiceprint_path.open("wb") as file:
            np.save(file, voiceprint, allow_pickle=False)
        self._voiceprint = voiceprint
        logger.info(
            "Enrolled voiceprint from %d samples into %s", len(samples), self._voiceprint_path
        )
        return voiceprint

    def score(self, audio: np.ndarray, sample_rate: int) -> float:
        """Cosine similarity between the utterance and the owner's voiceprint, in [-1, 1]."""
        voiceprint = self._load_voiceprint()
        embedding = self._normalised_embedding(audio, sample_rate)
        if embedding.shape != voiceprint.shape:
            raise SpeakerError(
                "Voiceprint tidak cocok dengan model suara yang dipakai sekarang. "
                "Jalankan 'waifu enroll' lagi."
            )
        return float(np.dot(embedding, voiceprint))

    def verify(self, audio: np.ndarray, sample_rate: int) -> tuple[bool, float]:
        score = self.score(audio, sample_rate)
        return score >= self.threshold, score

    def _normalised_embedding(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        return _l2_normalise(self._embedder.embed(audio, sample_rate))

    def _load_voiceprint(self) -> np.ndarray:
        if self._voiceprint is not None:
            return self._voiceprint
        if not self._voiceprint_path.exists():
            raise SpeakerError("Suara Master belum terdaftar. Jalankan 'waifu enroll' dulu.")
        try:
            loaded = np.load(self._voiceprint_path)
        except (OSError, ValueError, EOFError) as exc:
            raise self._unreadable_voiceprint() from exc
        if not isinstance(loaded, np.ndarray) or loaded.ndim != 1:
            raise self._unreadable_voiceprint()
        self._voiceprint = loaded.astype(np.float32)
        logger.info("Loaded voiceprint from %s", self._voiceprint_path)
        return self._voiceprint

    def _unreadable_voiceprint(self) -> SpeakerError:
        return SpeakerError(
            f"Voiceprint di {self._voiceprint_path} tidak bisa dibaca. "
            "Jalankan 'waifu enroll' lagi."
        )


class EcapaEmbedder(SpeakerEmbedder):
    """SpeechBrain ECAPA-TDNN speaker embeddings, loaded on first use."""

    def __init__(
        self,
        model_source: str,
        model_dir: Path,
        device: str,
        *,
        loader: EncoderLoader | None = None,
        cuda_available: Callable[[], bool] | None = None,
    ) -> None:
        self._model_source = model_source
        self._model_dir = Path(model_dir)
        self._device = device
        self._loader = loader or load_speechbrain_encoder
        self._cuda_available = cuda_available or _torch_cuda_available
        self._encoder: SpeakerEncoder | None = None

    def embed(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        encoder = self._ensure_loaded()
        embedding = encoder(_resample_to_model_rate(audio, sample_rate))
        return np.asarray(embedding, dtype=np.float32).reshape(-1)

    def _ensure_loaded(self) -> SpeakerEncoder:
        if self._encoder is None:
            device = self._device
            if device.startswith("cuda") and not self._cuda_available():
                logger.warning("CUDA is not available; running the speaker model on the CPU")
                device = "cpu"
            logger.info("Loading speaker model %s on %s", self._model_source, device)
            self._encoder = self._loader(self._model_source, self._model_dir, device)
        return self._encoder


def load_speechbrain_encoder(model_source: str, model_dir: Path, device: str) -> SpeakerEncoder:
    """Load ECAPA through speechbrain, copying files because Windows symlinks need admin rights."""
    import torch
    from speechbrain.inference.classifiers import EncoderClassifier
    from speechbrain.utils.fetching import LocalStrategy

    classifier = EncoderClassifier.from_hparams(
        source=model_source,
        savedir=str(model_dir),
        run_opts={"device": device},
        local_strategy=LocalStrategy.COPY,
    )

    def encode(audio: np.ndarray) -> np.ndarray:
        with torch.inference_mode():
            return classifier.encode_batch(torch.from_numpy(audio)[None]).cpu().numpy()

    return encode


def _torch_cuda_available() -> bool:
    import torch

    return torch.cuda.is_available()


def _l2_normalise(vector: np.ndarray) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def _resample_to_model_rate(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    if sample_rate == MODEL_SAMPLE_RATE:
        return np.ascontiguousarray(audio)
    target_length = round(len(audio) * MODEL_SAMPLE_RATE / sample_rate)
    source_times = np.arange(len(audio)) / sample_rate
    target_times = np.arange(target_length) / MODEL_SAMPLE_RATE
    return np.interp(target_times, source_times, audio).astype(np.float32)
