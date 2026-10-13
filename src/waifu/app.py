"""The listen -> verify -> transcribe -> gate -> think -> speak loop.

Every collaborator is injected, so the loop runs the same against the real
microphone, Whisper, and speakers or against test fakes. Speech output is half
duplex: the microphone is paused while she talks so she never answers herself.
An optional avatar shows what she is doing: thinking, speaking (mouth and face
following the reply), and listening during the follow-up window.

The avatar's interactions (`waifu.avatar.interactions`) speak from their own
thread, so she says one thing at a time: replies, announcements, and reactions
share one voice lock, and `busy` tells the interaction handler when to stay quiet.

Other threads make her speak up by herself in two ways, both queued and handled by
`run` between Master's utterances: `announce` says a fixed text (a reminder), and
`ask_aloud` has the brain answer a prompt (the morning greeting) and says the reply.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import queue
import threading
import time
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


def _called_successfully(reply: BrainReply, tool: str) -> bool:
    return any(call.name == tool and not call.is_error for call in reply.tool_calls)


def _pass_on(on_reply: ReplyHandler, text: str) -> None:
    """Hand what she said by herself to `on_reply`; a failure is logged, never raised."""
    try:
        on_reply(text)
    except Exception:
        logger.exception("Could not pass on what she said by herself")


class Speech(Protocol):
    def synthesize(self, text: str) -> tuple[np.ndarray, int]: ...


class AudioPlayer(Protocol):
    def play(self, audio: np.ndarray, sample_rate: int) -> bool:
        """Play `audio` until it ends; True when it played to the end, False when cut."""
        ...

    def stop(self) -> bool:
        """Cut the clip being played, from any thread; False when nothing is playing."""
        ...


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


# Gets what she said aloud in answer to `ask_aloud` (e.g. to push it to the phone), on a
# thread of its own.
ReplyHandler = Callable[[str], None]


@dataclasses.dataclass(frozen=True)
class _Request:
    """A prompt queued by `ask_aloud`."""

    prompt: str
    on_reply: ReplyHandler | None
    required_tool: str | None


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
        clock: Callable[[], float] = time.monotonic,
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
        self._clock = clock
        self._mic: Microphone | None = None
        self._segmenter: Segmenter | None = None
        self._speaker_error_logged = False
        self._avatar_failing = False
        self._announcements: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._requests: queue.SimpleQueue[_Request] = queue.SimpleQueue()
        # After a cut, on `clock`: announcements and requests wait until then
        # (see `_hold_announcements`).
        self._announcements_held_until: float | None = None
        # When the follow-up window last opened by `_open_followup` ends, on `clock`: requests
        # wait until then, since Master may still answer her.
        self._followup_until: float | None = None
        self._voice = threading.Lock()  # one voice: replies, announcements, and reactions
        self._engaged_lock = threading.Lock()
        self._engaged_count = 0
        self._speaking = False

    @property
    def busy(self) -> bool:
        """Whether she is engaged: handling an utterance, speaking, or announcing.

        Also while Master is speaking (the segmenter is inside an utterance), so a
        reaction never cuts Master off, like announcements. Safe from any thread.
        """
        with self._engaged_lock:
            engaged = self._engaged_count > 0
        segmenter = self._segmenter
        return engaged or (segmenter is not None and segmenter.busy)

    @property
    def speaking(self) -> bool:
        """Whether a clip is playing: narrower than `busy`, which also covers thinking."""
        return self._speaking

    def interrupt(self) -> bool:
        """Stop what she is saying (a double-click on the avatar); True when something was playing.

        Safe from any thread and never waits for her voice: it only stops the player.
        The interrupted `speak` finishes as usual (microphone resumed, segmenter reset,
        follow-up window opened after a reply) and holds queued announcements back so
        Master can talk. A reply still being thought up is not cut.
        """
        return self._player.stop()

    def set_mic(self, mic: Microphone | None, segmenter: Segmenter | None = None) -> None:
        """The microphone to pause, and the segmenter to reset, while she speaks."""
        self._mic = mic
        self._segmenter = segmenter

    def handle_utterance(self, utterance: Utterance) -> str | None:
        """Answer one utterance aloud; returns what she said, or None when it was not for her."""
        with self._engaged():
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
            self._announcements_held_until = None  # Master has been heard after a cut
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

        `run` says it between utterances, never during a reply, while Master speaks, or
        while she listens for Master after being cut off.
        """
        self._announcements.put((text, emotion))

    def ask_aloud(
        self,
        prompt: str,
        *,
        on_reply: ReplyHandler | None = None,
        required_tool: str | None = None,
    ) -> None:
        """Queue a prompt for the brain whose reply she says by herself; safe from any thread.

        `run` handles it between utterances under the same conditions as announcements,
        on a later frame than any announcements queued with it, never while a follow-up
        window is open (Master may still answer her), and one request at a time: she thinks
        up the reply, says it like a reply (the follow-up window opens, so Master can
        answer without the wake word), and hands the spoken text to `on_reply` on a thread
        of its own, so a slow push never keeps `run` from the microphone.

        A brain answers a failure (no connection, a refusal) with an apology, never an
        exception. With `required_tool`, a reply whose turn has no successful call of that
        tool counts as such a failure: nobody asked, so it is logged, not said or passed on.
        """
        self._requests.put(_Request(prompt, on_reply, required_tool))

    def speak(self, text: str, emotion: str = NEUTRAL, *, followup: bool = True) -> None:
        """Say `text` with the microphone paused and the avatar mouthing it with `emotion`.

        A reply opens the follow-up window afterwards, also when `interrupt()` cuts it
        short; with `followup=False` (an announcement, not a reply) it stays as it was.
        Speech that was cut holds queued announcements back (`_hold_announcements`).
        Failures are logged, never raised. Safe from any thread: she says one thing at a
        time, so this waits for whatever she is saying first.
        """
        with self._engaged(), self._voice:
            mic, segmenter = self._mic, self._segmenter
            if mic is not None:
                mic.pause()
            try:
                audio, sample_rate = self._speech.synthesize(text)
                self._show(lambda avatar: avatar.state("speaking"))
                self._show(lambda avatar: avatar.speak(audio, sample_rate, emotion))
                self._speaking = True
                if not self._player.play(audio, sample_rate):
                    logger.info("Speech was cut off before its end")
                    self._hold_announcements()
            except TtsError as exc:
                logger.warning("Could not synthesise the reply: %s", exc)
            except Exception:
                logger.exception("Could not play the reply")
            finally:
                self._speaking = False
                if mic is not None:
                    mic.resume()
                if segmenter is not None:
                    segmenter.reset()
                if followup:
                    self._open_followup()
                else:  # an announcement: only the wake word is heard
                    self._show(lambda avatar: avatar.state("idle"))

    def speak_reaction(self, text: str, emotion: str) -> None:
        """Answer a touch on the avatar aloud, exactly like a reply (`speak`).

        The microphone is paused, the avatar mouths `text` with `emotion`, and the
        follow-up window opens afterwards. Called from the interaction handler's thread.
        """
        self.speak(text, emotion)

    def listen_now(self) -> None:
        """Open the follow-up window now (a double-click on the avatar) and show her listening.

        It says nothing itself: the interaction handler follows it with a `listen` line
        through `speak_reaction`, so the line's choice, repeat avoidance, and cooldown stay
        in the handler, and the window is open even when that line is skipped. The handler
        ignores a double-click while she is `busy`; a reply or announcement that starts
        between that check and this call is waited for, so the window opens after it.
        Safe from any thread.
        """
        with self._voice:
            self._open_followup()

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
                    if not segmenter.busy and not self._announcements_held():
                        self._speak_up()
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

    def _hold_announcements(self) -> None:
        """Keep queued announcements and requests for the follow-up window after a cut.

        Master cut her off to talk, and an announcement would pause the microphone again
        at once. They wait until Master's next words are accepted (`handle_utterance`)
        or `followup_seconds` have passed. Called on the thread that was speaking, before
        the microphone resumes, so `run` never sees the cut without the hold.
        """
        self._announcements_held_until = self._clock() + self._followup_s

    def _announcements_held(self) -> bool:
        held_until = self._announcements_held_until
        return held_until is not None and self._clock() < held_until

    def _followup_open(self) -> bool:
        followup_until = self._followup_until
        return followup_until is not None and self._clock() < followup_until

    def _speak_up(self) -> None:
        """Say the queued announcements, or else answer a request once Master has had his say.

        A request never follows announcements in the same pass: the next frame checks again
        that Master has not started talking and did not cut them short to talk. Nor does it
        come while the follow-up window is open, so a conversation is never taken over.
        """
        if not self._say_announcements() and not self._followup_open():
            self._answer_next_request()

    def _say_announcements(self) -> bool:
        """Say every queued announcement in one go, with the first one's emotion; True when
        there was any."""
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
        if not texts:
            return False
        logger.info("Announcing: %s", " ".join(texts))
        self.speak(" ".join(texts), emotion, followup=False)
        return True

    def _answer_next_request(self) -> None:
        """Answer the oldest `ask_aloud` request aloud, if any.

        One at a time: the next waits until Master has had the chance to answer this reply
        (its follow-up window has closed). A failure is logged and listening goes on, as for
        an utterance.
        """
        try:
            request = self._requests.get_nowait()
        except queue.Empty:
            return
        logger.info("Speaking up by herself in answer to a request")
        try:
            with self._engaged():
                self._show(lambda avatar: avatar.state("thinking"))
                emotion, reply = self._think(request.prompt)
                tool = request.required_tool
                if tool is not None and not _called_successfully(reply, tool):
                    logger.warning(
                        "Not saying the answer to a request: its turn has no successful %s "
                        "call (the brain most likely failed and apologised)",
                        tool,
                    )
                    self._show(lambda avatar: avatar.state("idle"))
                    return
                self.speak(reply.text, emotion)
        except Exception:
            logger.exception("Failed to answer a request aloud; still listening")
            return
        if request.on_reply is not None:
            # A push to the phone may take its whole timeout: never on this thread, which must
            # be back at the microphone while the follow-up window is open.
            threading.Thread(
                target=_pass_on, args=(request.on_reply, reply.text), name="pass-on", daemon=True
            ).start()

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

    @contextlib.contextmanager
    def _engaged(self) -> Iterator[None]:
        """Count the enclosed work towards `busy`."""
        with self._engaged_lock:
            self._engaged_count += 1
        try:
            yield
        finally:
            with self._engaged_lock:
                self._engaged_count -= 1

    def _open_followup(self) -> None:
        """Hear Master without the wake word for `followup_seconds`, and show her listening."""
        self._gate.open_followup()
        self._followup_until = self._clock() + self._followup_s
        if self._followup_s > 0:
            self._show(lambda avatar: avatar.state("listening", followup_s=self._followup_s))
        else:  # no follow-up window: only the wake word is heard
            self._show(lambda avatar: avatar.state("idle"))

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
