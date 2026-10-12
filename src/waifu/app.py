"""The listen -> verify -> transcribe -> gate -> think -> speak loop.

Every collaborator is injected, so the loop runs the same against the real
microphone, Whisper, and speakers or against test fakes. Speech output is half
duplex: the microphone is paused while she talks so she never answers herself.
An optional avatar shows what she is doing: thinking, speaking (mouth and face
following the reply), and listening during the follow-up window.
"""

from __future__ import annotations

import dataclasses
import logging
import queue
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Protocol

import numpy as np

from waifu.avatar.emotion import NEUTRAL, split_emotion
from waifu.brain.base import Brain, BrainReply, ToolCallRecord
from waifu.config import AppConfig
from waifu.gate import WakeGate
from waifu.voice.base import SpeechToText, Utterance
from waifu.voice.speaker import SpeakerError
from waifu.voice.stt import SttError
from waifu.voice.tts import TtsError

logger = logging.getLogger(__name__)

ACKNOWLEDGEMENT = "はい、Master?"
ACKNOWLEDGEMENT_EMOTION = "happy"
# A reply that was only an emotion tag (say "[senang]" after a tool call) still gets spoken
# words, picked like the brains pick theirs for an empty reply.
TAG_ONLY_DONE = "Sudah, Master."
TAG_ONLY_FAILED = "Maaf Master, ada perintah yang gagal dijalankan."
TAG_ONLY_NO_ANSWER = "Maaf Master, aku belum bisa menjawab itu."


def split_reply(reply: BrainReply) -> tuple[str, BrainReply]:
    """The reply's emotion and the reply without its tags, never left without words."""
    emotion, text = split_emotion(reply.text)
    if not text:
        text = _tag_only_fallback(reply.tool_calls)
    return emotion, dataclasses.replace(reply, text=text)


def _tag_only_fallback(tool_calls: list[ToolCallRecord]) -> str:
    if any(call.is_error for call in tool_calls):
        return TAG_ONLY_FAILED
    return TAG_ONLY_DONE if tool_calls else TAG_ONLY_NO_ANSWER


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

    @property
    def busy(self) -> bool:
        """Whether an utterance (or its onset) is under way."""
        ...


class Avatar(Protocol):
    """What the voice loop shows on the avatar (the `AvatarBus`)."""

    def state(self, name: str, followup_s: float | None = None) -> None: ...

    def speak(self, audio: np.ndarray, sample_rate: int, emotion: str) -> None: ...


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
        avatar: Avatar | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._name = config.assistant.name
        self._followup_s = config.assistant.followup_seconds
        self._brain = brain
        self._stt = stt
        self._speech = speech
        self._player = player
        self._gate = gate
        self._verifier = verifier if speaker_check and config.speaker.enabled else None
        self._avatar = avatar
        self._now = now
        self._mic: Microphone | None = None
        self._segmenter: Segmenter | None = None
        self._speaker_error_logged = False
        self._avatar_failing = False
        self._announcements: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()

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
        if result.command:
            self._show(lambda avatar: avatar.state("thinking"))
            emotion, reply = self._think(result.command)
            reply_text = reply.text
        else:
            # A bare wake word ("Cyrene?") gets a quick answer without a round trip to the LLM.
            emotion, reply_text = ACKNOWLEDGEMENT_EMOTION, ACKNOWLEDGEMENT
        self.speak(reply_text, emotion)
        return reply_text

    def handle_text(self, text: str) -> BrainReply:
        """Ask the brain directly: no wake word, no voice (text chat); the reply has no tag."""
        return self._think(text)[1]

    def announce(self, text: str, emotion: str = NEUTRAL) -> None:
        """Queue something to say by herself; safe from any thread (the gacha watcher's).

        `run` says it between utterances, never during a reply or while Master speaks.
        """
        self._announcements.put((text, emotion))

    def speak(self, text: str, emotion: str = NEUTRAL, *, followup: bool = True) -> None:
        """Say `text` with the microphone paused and the avatar mouthing it with `emotion`.

        A reply opens the follow-up window afterwards; with `followup=False` (an
        announcement, not a reply) it stays as it was. Failures are logged, never raised.
        """
        if self._mic is not None:
            self._mic.pause()
        try:
            audio, sample_rate = self._speech.synthesize(text)
            self._show(lambda avatar: avatar.state("speaking"))
            self._show(lambda avatar: avatar.speak(audio, sample_rate, emotion))
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
            if followup:
                self._gate.open_followup()
            if followup and self._followup_s > 0:
                self._show(lambda avatar: avatar.state("listening", followup_s=self._followup_s))
            else:  # an announcement, or no follow-up window: only the wake word is heard
                self._show(lambda avatar: avatar.state("idle"))

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
                    if not segmenter.busy:
                        self._say_announcements()
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

    def _say_announcements(self) -> None:
        """Say every queued announcement in one go, with the first one's emotion."""
        texts: list[str] = []
        emotion = NEUTRAL
        while True:
            try:
                text, mood = self._announcements.get_nowait()
            except queue.Empty:
                break
            if not texts:
                emotion = mood
            texts.append(text)
        if texts:
            logger.info("Announcing: %s", " ".join(texts))
            self.speak(" ".join(texts), emotion, followup=False)

    def _think(self, text: str) -> tuple[str, BrainReply]:
        """The brain's reply to `text`, split into its emotion and the reply without the tag."""
        reply = self._brain.respond(text, now=self._now())
        for call in reply.tool_calls:
            # The result itself only at DEBUG: the INFO log (logs\waifu.log under autostart)
            # must not collect the owner's pull history.
            outcome = "error" if call.is_error else "ok"
            logger.info("Tool %s(%s) -> %s", call.name, call.arguments, outcome)
            logger.debug("Tool %s result: %s", call.name, call.result)
        return split_reply(reply)

    def _show(self, update: Callable[[Avatar], None]) -> None:
        """Apply `update` to the avatar, if any; failures are logged once per streak, not raised."""
        if self._avatar is None:
            return
        try:
            update(self._avatar)
        except Exception as exc:  # the avatar is a bonus: the voice loop goes on without it
            if not self._avatar_failing:
                logger.warning("The avatar could not be updated; going on without it: %s", exc)
            self._avatar_failing = True
        else:
            self._avatar_failing = False

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
