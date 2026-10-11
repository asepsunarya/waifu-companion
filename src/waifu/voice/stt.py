"""Speech-to-text with faster-whisper.

The Whisper model loads on the first real utterance, so tests and `chat --dry-run`
never touch the GPU. On Windows, ctranslate2 needs cuBLAS/cuDNN DLLs that ship in
pip wheels (torch, nvidia-*); their folders are registered right before loading.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any

import numpy as np

from waifu.config import SttConfig
from waifu.voice.base import SpeechToText, Transcript

logger = logging.getLogger(__name__)

WHISPER_SAMPLE_RATE = 16000
MIN_AUDIO_SECONDS = 0.3
SILENCE_NO_SPEECH_PROB = 0.6
SILENCE_AVG_LOGPROB = -1.0
CUDA_WHEEL_PACKAGES = ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime", "nvidia.cuda_nvrtc")
KNOWN_HALLUCINATIONS = (
    "terima kasih telah menonton",
    "terima kasih sudah menonton",
    "sampai jumpa di video berikutnya",
    "ご視聴ありがとうございました",
    "チャンネル登録",
    "subtitle by",
    "subtitles by",
    "thanks for watching",
    "amara.org",
)
CUDA_HINT = (
    "Pasang torch versi CUDA (cu128) atau paket nvidia-cublas-cu12 dan nvidia-cudnn-cu12, "
    'atau ubah stt.device menjadi "cpu" di config.yaml.'
)

_CUDA_ERROR = re.compile(r"cuda|cublas|cudnn", re.IGNORECASE)
_DLL_NAME = re.compile(r"[\w.-]+\.dll", re.IGNORECASE)
_registered_dll_dirs: set[str] = set()

ModelFactory = Callable[[SttConfig], Any]


class SttError(Exception):
    """Whisper cannot run; the message is an Indonesian hint for the owner."""


def register_cuda_dll_dirs(
    *, find_spec: Callable[[str], ModuleSpec | None] = importlib.util.find_spec
) -> list[str]:
    """Make CUDA DLLs from installed wheels loadable on Windows; returns the folders added now.

    Folders are located through import specs, so torch itself is never imported.
    Folders registered by an earlier call are skipped.
    """
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return []
    added = [
        directory
        for directory in _cuda_dll_dir_candidates(find_spec)
        if directory not in _registered_dll_dirs and os.path.isdir(directory)
    ]
    for directory in added:
        os.add_dll_directory(directory)
        _registered_dll_dirs.add(directory)
    if added:
        # ctranslate2 resolves some CUDA DLLs through PATH instead of the DLL search path.
        os.environ["PATH"] = os.pathsep.join([*added, os.environ.get("PATH", "")])
        logger.info("Registered CUDA DLL directories: %s", ", ".join(added))
    return added


class WhisperStt(SpeechToText):
    def __init__(
        self,
        config: SttConfig,
        assistant_name: str,
        *,
        model_factory: ModelFactory | None = None,
        register_dlls: Callable[[], list[str]] = register_cuda_dll_dirs,
    ) -> None:
        self._config = config
        self._initial_prompt = f"{assistant_name}, tolong nyalakan lampu kamar."
        self._model_factory = model_factory or _load_faster_whisper
        self._register_dlls = register_dlls
        self._model: Any = None

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        fallback_language = self._config.languages[0]
        if len(audio) < MIN_AUDIO_SECONDS * sample_rate:
            return Transcript("", fallback_language)
        samples = _resample_to_whisper_rate(audio, sample_rate)
        with _cuda_errors_as_stt_error():
            model = self._loaded_model()
            segments, info = self._decode(model, samples, language=None)
            language = info.language
            if language not in self._config.languages:
                logger.info(
                    "Detected language %r is not in %s; re-running as %r",
                    language,
                    self._config.languages,
                    fallback_language,
                )
                segments, _ = self._decode(model, samples, language=fallback_language)
                language = fallback_language
            text = " ".join(_usable_texts(segments))
        return Transcript(text, language)

    def preload(self) -> None:
        with _cuda_errors_as_stt_error():
            self._loaded_model()

    def _loaded_model(self) -> Any:
        if self._model is None:
            self._register_dlls()
            logger.info(
                "Loading Whisper %s on %s (%s)",
                self._config.model,
                self._config.device,
                self._config.compute_type,
            )
            self._model = self._model_factory(self._config)
        return self._model

    def _decode(
        self, model: Any, samples: np.ndarray, language: str | None
    ) -> tuple[Iterable[Any], Any]:
        return model.transcribe(
            samples,
            language=language,
            beam_size=self._config.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=self._initial_prompt,
        )


def _load_faster_whisper(config: SttConfig) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(
        config.model,
        device=config.device,
        compute_type=config.compute_type,
        download_root=str(config.model_dir),
    )


def _cuda_dll_dir_candidates(find_spec: Callable[[str], ModuleSpec | None]) -> list[str]:
    candidates = []
    torch_spec = _spec_or_none(find_spec, "torch")
    if torch_spec is not None and torch_spec.origin:
        candidates.append(str(Path(torch_spec.origin).parent / "lib"))
    for package in CUDA_WHEEL_PACKAGES:
        spec = _spec_or_none(find_spec, package)
        if spec is not None:
            locations = spec.submodule_search_locations or []
            candidates.extend(str(Path(location) / "bin") for location in locations)
    return candidates


def _spec_or_none(find_spec: Callable[[str], ModuleSpec | None], name: str) -> ModuleSpec | None:
    try:
        return find_spec(name)
    except ImportError:
        return None


@contextmanager
def _cuda_errors_as_stt_error() -> Iterator[None]:
    try:
        yield
    except (RuntimeError, OSError) as exc:
        if not _CUDA_ERROR.search(str(exc)):
            raise
        missing_dll = _DLL_NAME.search(str(exc))
        if missing_dll:
            logger.error("Whisper could not load CUDA library %s: %s", missing_dll.group(0), exc)
            problem = f"{missing_dll.group(0)} tidak ditemukan atau gagal dimuat"
        else:
            logger.error("Whisper could not use CUDA: %s", exc)
            problem = "CUDA tidak bisa dipakai"
        raise SttError(f"Whisper gagal berjalan di GPU: {problem}. {CUDA_HINT}") from exc


def _resample_to_whisper_rate(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    samples = np.asarray(audio, dtype=np.float32)
    if sample_rate == WHISPER_SAMPLE_RATE:
        return samples
    target_length = round(len(samples) * WHISPER_SAMPLE_RATE / sample_rate)
    source_times = np.arange(len(samples)) / sample_rate
    target_times = np.arange(target_length) / WHISPER_SAMPLE_RATE
    return np.interp(target_times, source_times, samples).astype(np.float32)


def _usable_texts(segments: Iterable[Any]) -> Iterator[str]:
    for segment in segments:
        text = segment.text.strip()
        if _is_probably_silence(segment) or _is_noise(text):
            logger.debug("Dropping Whisper segment %r", text)
            continue
        yield text


def _is_probably_silence(segment: Any) -> bool:
    return (
        segment.no_speech_prob > SILENCE_NO_SPEECH_PROB
        and segment.avg_logprob < SILENCE_AVG_LOGPROB
    )


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


_NORMALIZED_HALLUCINATIONS = tuple(_normalize(phrase) for phrase in KNOWN_HALLUCINATIONS)


def _is_noise(text: str) -> bool:
    """Punctuation-only text, or a phrase Whisper invents from silence (subtitle credits etc.)."""
    normalized = _normalize(text)
    return not normalized or any(phrase in normalized for phrase in _NORMALIZED_HALLUCINATIONS)
