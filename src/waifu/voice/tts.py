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
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Callable, Coroutine, Iterable, Mapping
from http import HTTPStatus
from itertools import groupby
from typing import Any, NamedTuple, Protocol

import numpy as np
import soundfile as sf

from waifu.config import (
    EdgeVoiceConfig,
    FallbackPitchConfig,
    GptSovitsVoiceConfig,
    TtsConfig,
    at_sentence_start,
)
from waifu.voice.base import TextToSpeech
from waifu.voice.rvc import RvcError, RvcUnavailableError
from waifu.voice.windows_voices import (
    Run,
    WindowsVoice,
    WindowsVoiceError,
    choose_voice,
    language_name,
    list_windows_voices,
    missing_voice_hint,
    speak_wav,
)

logger = logging.getLogger(__name__)

JAPANESE = "ja"
RUN_GAP_S = 0.12
GPT_SOVITS_TIMEOUT_S = 60.0
CONVERSION_RETRY_S = 60.0
# After her voice fails, a language stays on its offline fallback this long before a retry.
FALLBACK_RETRY_S = 600.0
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

    def convert(
        self, audio: np.ndarray, sample_rate: int, *, pitch: int | None = None
    ) -> tuple[np.ndarray, int]:
        """`pitch` shifts this clip by that many semitones instead of the configured pitch."""
        ...


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


class WindowsTts(TextToSpeech):
    """A speech voice installed in Windows: offline, Cyrene's fallback when her voice fails.

    The voice is chosen on the first `synthesize`, never when built, so building a speech
    router starts no PowerShell. A listing that worked is kept for the life of the process,
    so a voice the owner adds in Settings is used once Cyrene is restarted.
    """

    def __init__(
        self,
        language: str,
        wanted: str = "",
        *,
        pitches: FallbackPitchConfig,
        run: Run = subprocess.run,
    ) -> None:
        self._language = language
        self._wanted = wanted
        self._pitches = pitches
        self._run = run
        self._listed = False
        self._voice: WindowsVoice | None = None

    @property
    def voice(self) -> WindowsVoice | None:
        """The voice that speaks; None before the first use, or when none is installed."""
        return self._voice

    @property
    def rvc_pitch(self) -> int | None:
        """Semitones RVC shifts this voice by; None (`tts.rvc.pitch`) when its gender is not
        known."""
        gender = self._voice.gender.lower() if self._voice else ""
        return {"female": self._pitches.female, "male": self._pitches.male}.get(gender)

    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]:
        voice = self._chosen_voice()
        try:
            data = speak_wav(voice.name, text, run=self._run)
        except WindowsVoiceError as exc:
            raise TtsError(str(exc)) from exc
        audio, sample_rate = _decode_audio(data, f"suara Windows {voice.name}")
        if audio.size == 0:
            raise TtsError(f"Suara Windows {voice.name} tidak menghasilkan audio.")
        return audio, sample_rate

    def _chosen_voice(self) -> WindowsVoice:
        if not self._listed:
            try:
                voices = list_windows_voices(run=self._run)
            except WindowsVoiceError as exc:
                raise TtsError(str(exc)) from exc
            self._voice = choose_voice(voices, self._language, self._wanted)
            self._listed = True
            if self._voice is not None:
                logger.info(
                    "Offline fallback voice for %s: %s (%s)",
                    self._language,
                    self._voice.name,
                    self._voice.language,
                )
        if self._voice is not None:
            return self._voice
        if self._wanted:
            raise TtsError(
                f"Suara Windows '{self._wanted}' (tts.fallback.voices.{self._language}) "
                "tidak terpasang; lihat 'waifu voices'."
            )
        raise TtsError(f"{at_sentence_start(missing_voice_hint(self._language))}.")


class FallbackVoice(Protocol):
    """Stands in when a language's voice fails; `WindowsTts` is the real one."""

    @property
    def rvc_pitch(self) -> int | None:
        """Semitones RVC shifts this voice by; None for `tts.rvc.pitch`."""
        ...

    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]: ...


class _Piece(NamedTuple):
    """One spoken run and the pitch RVC shifts it by (None: `tts.rvc.pitch`)."""

    audio: np.ndarray
    sample_rate: int
    rvc_pitch: int | None = None


class SpeechRouter:
    """Voices mixed-language text by sending each language run to that language's engine.

    When a language's engine fails and that language has a fallback (a Windows voice,
    which works offline), the fallback speaks the run instead, and the engine is then
    skipped for `fallback_retry_s` before it is tried again: edge-tts may take its whole
    10 s connect timeout to fail, on every run of every reply.

    With a `converter`, the joined clip is then converted into one voice (the RVC
    anime voice); when conversion fails the plain clip is spoken instead, so a
    broken converter never silences her. After the converter could not be reached,
    replies skip it for CONVERSION_RETRY_S before it is tried again: each attempt at
    a dead server costs ~2 s on Windows, and at a stuck one its whole timeout. Runs
    that need another pitch (a fallback voice) are converted apart from the rest.
    """

    def __init__(
        self,
        engines: dict[str, TextToSpeech],
        default_language: str,
        *,
        fallbacks: Mapping[str, FallbackVoice] | None = None,
        fallback_retry_s: float = FALLBACK_RETRY_S,
        fallback_only: bool = False,
        converter: VoiceConverter | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if default_language not in engines:
            raise ValueError(f"no TTS engine for the default language '{default_language}'")
        self.engines = dict(engines)
        self.default_language = default_language
        self.fallbacks = dict(fallbacks or {})
        self.fallback_only = fallback_only
        self.converter = converter
        self._fallback_retry_s = fallback_retry_s
        self._clock = clock
        # A language whose engine is failing -> its last error, until the engine works again.
        self._engine_errors: dict[str, TtsError] = {}
        self._skip_engine_until: dict[str, float] = {}
        self._conversion_failing = False
        self._skip_conversion_until = -math.inf

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        """Return one mono float32 clip; runs that fail are skipped, all failing is a TtsError."""
        runs = split_by_language(clean_for_speech(text), self.default_language)
        if not runs:
            raise TtsError("Tidak ada teks yang bisa diucapkan.")
        pieces: list[_Piece] = []
        failures: list[TtsError] = []
        for language, run_text in runs:
            try:
                pieces.append(self._speak_run(language, run_text))
            except TtsError as exc:
                # Never her words: a reply may hold Master's money, and this reaches the log file.
                logger.warning(
                    "Skipping %s speech run of %d characters: %s", language, len(run_text), exc
                )
                failures.append(exc)
        if not pieces:
            raise TtsError(f"Semua bagian teks gagal diucapkan: {failures[-1]}") from failures[-1]
        if self.converter is None:
            return _join_pieces(pieces)
        converted = [
            self._convert(self.converter, *_join_pieces(group), pitch)
            for pitch, group in groupby(pieces, key=lambda piece: piece.rvc_pitch)
        ]
        return _join(converted)

    def _speak_run(self, language: str, text: str) -> _Piece:
        fallback = self.fallbacks.get(language)
        if self.fallback_only:
            if fallback is None:
                raise TtsError(
                    f"Belum ada suara cadangan untuk bahasa {language_name(language)} "
                    "(tts.fallback.voices)."
                )
            return _fallback_piece(fallback, text, language)
        engine = self.engines.get(language, self.engines[self.default_language])
        if fallback is None:
            return _Piece(*engine.synthesize(text, language))
        if self._clock() < self._skip_engine_until.get(language, -math.inf):
            logger.debug("The %s voice failed a moment ago; speaking with its fallback", language)
            return self._stand_in(fallback, text, language)
        try:
            piece = _Piece(*engine.synthesize(text, language))
        except TtsError as exc:
            self._engine_failed(language, exc)
            piece = self._stand_in(fallback, text, language)
            self._skip_engine_until[language] = self._clock() + self._fallback_retry_s
            return piece
        if self._engine_errors.pop(language, None) is not None:
            logger.info("The %s voice works again", language)
        return piece

    def _engine_failed(self, language: str, exc: TtsError) -> None:
        """Remember the failure; an outage warns only once."""
        if language in self._engine_errors:
            logger.debug("The %s voice still fails: %s", language, exc)
        else:
            logger.warning(
                "The %s voice failed; trying its offline fallback until it works again: %s",
                language,
                exc,
            )
        self._engine_errors[language] = exc

    def _stand_in(self, fallback: FallbackVoice, text: str, language: str) -> _Piece:
        """The fallback's piece. A fallback that cannot speak ends the pause, so a missing
        Windows voice never keeps a recovered engine away."""
        try:
            return _fallback_piece(fallback, text, language)
        except TtsError as exc:
            self._skip_engine_until.pop(language, None)
            engine_error = self._engine_errors.get(language)
            if engine_error is None:
                raise
            reason = str(engine_error).rstrip(".")
            raise TtsError(f"{reason}. Suara cadangan Windows juga gagal: {exc}") from exc

    def _convert(
        self,
        converter: VoiceConverter,
        audio: np.ndarray,
        sample_rate: int,
        pitch: int | None,
    ) -> tuple[np.ndarray, int]:
        """The converted clip, else the plain one; a failure streak warns only once."""
        if self._clock() < self._skip_conversion_until:
            logger.debug("Voice converter unreachable a moment ago; speaking the plain voice")
            return audio, sample_rate
        try:
            converted = converter.convert(audio, sample_rate, pitch=pitch)
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


def _fallback_piece(fallback: FallbackVoice, text: str, language: str) -> _Piece:
    audio, sample_rate = fallback.synthesize(text, language)
    return _Piece(audio, sample_rate, fallback.rvc_pitch)  # its voice is known once it spoke


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


def _join_pieces(pieces: Iterable[_Piece]) -> tuple[np.ndarray, int]:
    return _join([(piece.audio, piece.sample_rate) for piece in pieces])


def _join(pieces: list[tuple[np.ndarray, int]]) -> tuple[np.ndarray, int]:
    sample_rate = pieces[0][1]
    gap = np.zeros(round(sample_rate * RUN_GAP_S), dtype=np.float32)
    clips = [_resample(audio, rate, sample_rate) for audio, rate in pieces]
    parts = [part for clip in clips for part in (gap, clip)][1:]
    return np.concatenate(parts), sample_rate
