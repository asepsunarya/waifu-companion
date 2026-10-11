"""The listen -> verify -> transcribe -> gate -> think -> speak loop.

Every collaborator is injected, so the loop runs the same against the real
microphone, Whisper, and speakers or against test fakes. Speech output is half
duplex: the microphone is paused while she talks so she never answers herself.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Protocol

import numpy as np

from waifu.brain.base import Brain, BrainReply
from waifu.config import AppConfig
from waifu.gate import WakeGate
from waifu.voice.base import SpeechToText, Utterance
from waifu.voice.speaker import SpeakerError
from waifu.voice.stt import SttError
from waifu.voice.tts import TtsError

logger = logging.getLogger(__name__)

ACKNOWLEDGEMENT = "はい、Master?"


class Speech(Protocol):
    def synthesize(self, text: str) -> tuple[np.ndarray, int]: ...


class AudioPlayer(Protocol):
    def play(self, audio: np.ndarray, sample_rate: int) -> None: ...


class OwnerVerifier(Protocol):
    def verify(self, audio: np.ndarray, sample_rate: int) -> tuple[bool, float]: ...


class Microphone(Protocol):
    def frames(self) -> Iterator[np.ndarray]: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...


class Segmenter(Protocol):
    def feed(self, frame: np.ndarray) -> Utterance | None: ...

    def reset(self) -> None: ...


class Assistant:
    def __init__(
        self,
        config: AppConfig,
        *,
        brain: Brain,
        stt: SpeechToText,
        speech: Speech,
        player: AudioPlayer,
        gate: WakeGate,
        verifier: OwnerVerifier | None = None,
        speaker_check: bool = True,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._name = config.assistant.name
        self._brain = brain
        self._stt = stt
        self._speech = speech
        self._player = player
        self._gate = gate
        self._verifier = verifier if speaker_check and config.speaker.enabled else None
        self._now = now
        self._mic: Microphone | None = None
        self._segmenter: Segmenter | None = None
        self._speaker_error_logged = False

    def set_mic(self, mic: Microphone | None, segmenter: Segmenter | None = None) -> None:
        """The microphone to pause, and the segmenter to reset, while she speaks."""
        self._mic = mic
        self._segmenter = segmenter

    def handle_utterance(self, utterance: Utterance) -> str | None:
        """Answer one utterance aloud; returns what she said, or None when it was not for her."""
        if not self._is_owner(utterance):
            return None
        transcript = self._stt.transcribe(utterance.audio, utterance.sample_rate)
        text = transcript.text.strip()
        if not text:
            logger.debug("Nothing usable was transcribed")
            return None
        logger.info("Heard (%s): %s", transcript.language, text)
        result = self._gate.check(text, heard_at=utterance.started_at)
        if not result.accepted:
            logger.info("Not addressed to %s (%s); ignoring", self._name, result.reason)
            return None
        # A bare wake word ("Cyrene?") gets a quick answer without a round trip to the LLM.
        reply_text = self.handle_text(result.command).text if result.command else ACKNOWLEDGEMENT
        self.speak(reply_text)
        return reply_text

    def handle_text(self, text: str) -> BrainReply:
        """Ask the brain directly: no wake word, no voice (text chat)."""
        reply = self._brain.respond(text, now=self._now())
        for call in reply.tool_calls:
            logger.info(
                "Tool %s(%s) -> %s%s",
                call.name,
                call.arguments,
                call.result,
                " [error]" if call.is_error else "",
            )
        return reply

    def speak(self, text: str) -> None:
        """Say `text` with the microphone paused; failures are logged, never raised."""
        if self._mic is not None:
            self._mic.pause()
        try:
            audio, sample_rate = self._speech.synthesize(text)
            self._player.play(audio, sample_rate)
        except TtsError as exc:
            logger.warning("Could not synthesise the reply: %s", exc)
        except Exception:
            logger.exception("Could not play the reply")
        finally:
            if self._mic is not None:
                self._mic.resume()
            if self._segmenter is not None:
                self._segmenter.reset()
            self._gate.open_followup()

    def run(self, mic: Microphone, segmenter: Segmenter) -> None:
        """Listen until Ctrl+C.

        A failure inside one utterance is logged and listening goes on, except a
        missing package or an unusable Whisper: every utterance would fail the same
        way, so those are raised for the caller to report.
        """
        self.set_mic(mic, segmenter)
        logger.info("%s is listening (Ctrl+C to stop)", self._name)
        try:
            for frame in mic.frames():
                utterance = segmenter.feed(frame)
                if utterance is None:
                    continue
                try:
                    self.handle_utterance(utterance)
                except (ImportError, SttError):
                    raise
                except Exception:
                    logger.exception("Failed to handle an utterance; still listening")
        except KeyboardInterrupt:
            logger.info("Stopped listening")
        finally:
            self.set_mic(None)

    def _is_owner(self, utterance: Utterance) -> bool:
        if self._verifier is None:
            return True
        try:
            accepted, score = self._verifier.verify(utterance.audio, utterance.sample_rate)
        except SpeakerError as exc:
            if not self._speaker_error_logged:
                logger.error("Speaker verification unavailable, ignoring all speech: %s", exc)
                self._speaker_error_logged = True
            return False
        if not accepted:
            logger.info("Ignoring another speaker (similarity %.2f)", score)
        return accepted
