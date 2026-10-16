"""Bilingual text-to-speech: split a reply into Indonesian and Japanese runs and voice each.

Replies are Indonesian with short Japanese phrases. Each language run goes to the
engine configured for that language (Edge TTS online voices or a local GPT-SoVITS
server), and the pieces are joined into one clip for the speaker. An optional voice
converter (the RVC anime voice) then turns that clip into one character voice.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import http.client
import io
import logging
import math
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from itertools import groupby
from typing import Any, Protocol

import numpy as np
import soundfile as sf

from waifu.config import EdgeVoiceConfig, GptSovitsVoiceConfig, TtsConfig
from waifu.voice.base import TextToSpeech
from waifu.voice.rvc import RvcError, RvcUnavailableError

logger = logging.getLogger(__name__)

JAPANESE = "ja"
RUN_GAP_S = 0.12
GPT_SOVITS_TIMEOUT_S = 60.0
CONVERSION_RETRY_S = 60.0
# Microsoft refuses outdated edge-tts releases at the websocket handshake (HTTP 403)
# whenever it changes its request token, as in Oct 2026 for every edge-tts <= 7.2.8.
EDGE_TTS_UPDATE_COMMAND = r".venv\Scripts\python -m pip install -U edge-tts"

_JAPANESE_CHAR = re.compile(
    "[\u3040-\u309f\u30a0-\u30ff\uff66-\uff9f\u3400-\u4dbf\u4e00-\u9fff「」『』。、・！？ー]"
)
_PICTOGRAPH = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe00-\ufe0f\u200d\u20e3]")
# Symbols in the So category that carry meaning when read aloud ("24°C").
_SPOKEN_SYMBOLS = frozenset("°℃")
_LIST_MARKER = re.compile(r"^[ \t]*(?:[-*+•]|\d+[.)])[ \t]+", re.MULTILINE)
_STRONG = re.compile(r"(\*\*|__)(.+?)\1")
_ASIDE = re.compile(r"\*[^*\n]+\*|\([^()\n]*\)|（[^（）\n]*）")
_MARKUP_LEFTOVER = re.compile(r"[*`#]")
_WHITESPACE = re.compile(r"\s+")

HttpGet = Callable[[str, float], bytes]

# Expected conversion failures: RvcClient raises RvcError; a converter talking to
# a socket directly raises OSError, which includes TimeoutError.
_CONVERSION_ERRORS = (RvcError, OSError)
# The converter is not there at all, as opposed to failing one clip.
_UNREACHABLE_ERRORS = (RvcUnavailableError, OSError)


class TtsError(Exception):
    """Speech could not be synthesised; the message is Indonesian and safe to show."""


class VoiceConverter(Protocol):
    """Turns a finished clip into another voice; `RvcClient` is the real one."""

    def convert(self, audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int]: ...


def split_by_language(text: str, default: str = "id") -> list[tuple[str, str]]:
    """Split text into (language, text) runs: Japanese script is "ja", other letters `default`.

    Spaces, digits, and ASCII punctuation stay with the run they follow (leading
    ones join the first run); runs without any letters are folded into a neighbour.
    """
    labels = [_language_of(char, default) for char in text]
    current = next((label for label in labels if label), None)
    if current is None:
        return []
    for index, label in enumerate(labels):
        if label is None:
            labels[index] = current
        else:
            current = label

    runs: list[tuple[str, str]] = []
    for language, chars in groupby(zip(text, labels, strict=True), key=lambda pair: pair[1]):
        chunk = "".join(char for char, _ in chars)
        if runs and (runs[-1][0] == language or not _has_letters(chunk)):
            runs[-1] = (runs[-1][0], runs[-1][1] + chunk)
        elif runs and not _has_letters(runs[-1][1]):
            runs[-1] = (language, runs[-1][1] + chunk)
        else:
            runs.append((language, chunk))
    return [(language, chunk.strip()) for language, chunk in runs if _has_letters(chunk)]


def clean_for_speech(text: str) -> str:
    """Strip what a voice cannot say: emoji, markdown, and *action* / (action) asides."""
    text = _LIST_MARKER.sub("", text)
    text = _STRONG.sub(r"\2", text)
    text = _ASIDE.sub(" ", text)
    text = _MARKUP_LEFTOVER.sub("", text)
    text = "".join(char for char in text if not _is_pictograph(char))
    return _WHITESPACE.sub(" ", text).strip()


class EdgeTts(TextToSpeech):
    """Microsoft Edge online neural voices: needs internet, no GPU."""

    def __init__(
        self, voice: EdgeVoiceConfig, *, communicate_factory: Callable[..., Any] | None = None
    ) -> None:
        if communicate_factory is None:
            import edge_tts

            communicate_factory = edge_tts.Communicate
        self._voice = voice
        self._communicate = communicate_factory

    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]:
        voice = self._voice
        try:
            communicate = self._communicate(
                text, voice.voice, rate=voice.rate, volume=voice.volume, pitch=voice.pitch
            )
            data = _run_sync(_collect_audio(communicate))
        except Exception as exc:
            if _refused(exc):
                raise TtsError(
                    f"Microsoft menolak suara {voice.voice} (403); biasanya edge-tts di PC ini "
                    f"sudah usang. Perbarui dari folder proyek Cyrene dengan "
                    f"'{EDGE_TTS_UPDATE_COMMAND}', lalu jalankan ulang 'waifu run'."
                ) from exc
            raise TtsError(f"Edge TTS gagal ({voice.voice}): {exc}") from exc
        if not data:
            raise TtsError(f"Edge TTS tidak mengirim audio untuk suara {voice.voice}.")
        return _decode_audio(data, "Edge TTS")


class GptSovitsTts(TextToSpeech):
    """A local GPT-SoVITS api_v2 server cloning the voice of a reference clip."""

    def __init__(self, voice: GptSovitsVoiceConfig, *, http_get: HttpGet | None = None) -> None:
        self._voice = voice
        self._http_get = http_get or _urlopen_get

    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]:
        voice = self._voice
        query = urllib.parse.urlencode(
            {
                "text": text,
                "text_lang": language,
                "ref_audio_path": voice.ref_audio_path,
                "prompt_text": voice.prompt_text,
                "prompt_lang": voice.prompt_lang,
                "speed_factor": voice.speed_factor,
                "media_type": "wav",
            }
        )
        url = f"{voice.url.rstrip('/')}/tts?{query}"
        try:
            data = self._http_get(url, GPT_SOVITS_TIMEOUT_S)
        except (OSError, http.client.HTTPException) as exc:
            raise TtsError(
                f"GPT-SoVITS di {voice.url} gagal: {exc}. "
                "Pastikan server api_v2 GPT-SoVITS sudah berjalan."
            ) from exc
        return _decode_audio(data, "GPT-SoVITS")


class SpeechRouter:
    """Voices mixed-language text by sending each language run to that language's engine.

    With a `converter`, the joined clip is then converted into one voice (the RVC
    anime voice); when conversion fails the plain clip is spoken instead, so a
    broken converter never silences her. After the converter could not be reached,
    replies skip it for CONVERSION_RETRY_S before it is tried again: each attempt at
    a dead server costs ~2 s on Windows, and at a stuck one its whole timeout.
    """

    def __init__(
        self,
        engines: dict[str, TextToSpeech],
        default_language: str,
        *,
        converter: VoiceConverter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if default_language not in engines:
            raise ValueError(f"no TTS engine for the default language '{default_language}'")
        self.engines = dict(engines)
        self.default_language = default_language
        self.converter = converter
        self._clock = clock
        self._conversion_failing = False
        self._skip_conversion_until = -math.inf

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """Return one mono float32 clip; runs that fail are skipped, all failing is a TtsError."""
        runs = split_by_language(clean_for_speech(text), self.default_language)
        if not runs:
            raise TtsError("Tidak ada teks yang bisa diucapkan.")
        default_engine = self.engines[self.default_language]
        pieces: list[tuple[np.ndarray, int]] = []
        failures: list[TtsError] = []
        for language, run_text in runs:
            engine = self.engines.get(language, default_engine)
            try:
                pieces.append(engine.synthesize(run_text, language))
            except TtsError as exc:
                logger.warning("Skipping %s speech run %r: %s", language, run_text, exc)
                failures.append(exc)
        if not pieces:
            raise TtsError(f"Semua bagian teks gagal diucapkan: {failures[-1]}") from failures[-1]
        audio, sample_rate = _join(pieces)
        if self.converter is None:
            return audio, sample_rate
        return self._convert(self.converter, audio, sample_rate)

    def _convert(
        self, converter: VoiceConverter, audio: np.ndarray, sample_rate: int
    ) -> tuple[np.ndarray, int]:
        """The converted clip, else the plain one; a failure streak warns only once."""
        if self._clock() < self._skip_conversion_until:
            logger.debug("Voice converter unreachable a moment ago; speaking the plain voice")
            return audio, sample_rate
        try:
            converted = converter.convert(audio, sample_rate)
        except _CONVERSION_ERRORS as exc:
            if isinstance(exc, _UNREACHABLE_ERRORS):
                self._skip_conversion_until = self._clock() + CONVERSION_RETRY_S
            if self._conversion_failing:
                logger.debug("Voice conversion still failing; speaking the plain voice: %s", exc)
            else:
                self._conversion_failing = True
                logger.warning(
                    "Voice conversion failed; speaking the plain voice until it works again: %s",
                    exc,
                )
            return audio, sample_rate
        if self._conversion_failing:
            self._conversion_failing = False
            logger.info("Voice conversion works again")
        return converted


def build_speech_router(
    config: TtsConfig, *, converter: VoiceConverter | None = None
) -> SpeechRouter:
    engines: dict[str, TextToSpeech] = {
        language: EdgeTts(voice) if isinstance(voice, EdgeVoiceConfig) else GptSovitsTts(voice)
        for language, voice in config.voices.items()
    }
    return SpeechRouter(engines, config.default_language, converter=converter)


def _language_of(char: str, default: str) -> str | None:
    if _JAPANESE_CHAR.match(char):
        return JAPANESE
    if char.isalpha():
        return default
    return None


def _has_letters(text: str) -> bool:
    return any(char.isalpha() for char in text)


def _is_pictograph(char: str) -> bool:
    if char in _SPOKEN_SYMBOLS:
        return False
    return unicodedata.category(char) in ("So", "Sk") or _PICTOGRAPH.match(char) is not None


async def _collect_audio(communicate: Any) -> bytes:
    chunks = [chunk["data"] async for chunk in communicate.stream() if chunk["type"] == "audio"]
    return b"".join(chunks)


def _refused(exc: BaseException) -> bool:
    """Whether the Edge service answered 403, also when edge-tts wrapped that answer (a 403
    without a server Date becomes SkewAdjustmentError chained from it)."""
    return HTTPStatus.FORBIDDEN in (
        getattr(exc, "status", None),
        getattr(exc.__cause__, "status", None),
    )


def _run_sync(coroutine: Coroutine[Any, Any, bytes]) -> bytes:
    """Run a coroutine from sync code, using a worker thread if this thread already runs a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def _urlopen_get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def _decode_audio(data: bytes, source: str) -> tuple[np.ndarray, int]:
    try:
        audio, sample_rate = sf.read(io.BytesIO(data), dtype="float32")
    except sf.SoundFileError as exc:
        raise TtsError(f"Audio dari {source} tidak bisa dibaca: {exc}") from exc
    if audio.ndim == 2:
        audio = audio.mean(axis=1, dtype=np.float32)
    return audio, int(sample_rate)


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    if src_rate == dst_rate or audio.size == 0:
        return audio
    length = round(audio.size * dst_rate / src_rate)
    positions = np.arange(length) * (src_rate / dst_rate)
    return np.interp(positions, np.arange(audio.size), audio).astype(np.float32)


def _join(pieces: list[tuple[np.ndarray, int]]) -> tuple[np.ndarray, int]:
    sample_rate = pieces[0][1]
    gap = np.zeros(round(sample_rate * RUN_GAP_S), dtype=np.float32)
    clips = [_resample(audio, rate, sample_rate) for audio, rate in pieces]
    parts = [part for clip in clips for part in (gap, clip)][1:]
    return np.concatenate(parts), sample_rate
