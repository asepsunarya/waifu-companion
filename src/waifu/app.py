"""The listen -> verify -> transcribe -> gate -> think -> speak loop.

Every collaborator is injected, so the loop runs the same against the real
microphone, Whisper, and speakers or against test fakes. Speech output is half
duplex: the microphone is paused while she talks so she never answers herself.
An optional avatar shows what she is doing: thinking, speaking (mouth and face
following the reply), and listening during the follow-up window.

The avatar's interactions (`waifu.avatar.interactions`) speak from their own
thread, so she says one thing at a time: replies, announcements, and reactions
share one voice lock, and `busy` tells the interaction handler when to stay quiet.

Other threads make her speak up by herself, queued and handled by `run` between Master's
utterances: `announce` says a fixed text (a game notice), `ask_aloud` has the brain answer a
prompt (the morning greeting) and says the reply, and `remind` says what Master asked for
himself (a reminder he set, the focus timer's lines). While `quiet` holds (a focus block is
under way, or in game mode a game runs) only what `remind` queued is said; the rest waits, and
what a game held comes out after a short lead-in once it has closed. An announcement or request
still waiting at its expiry is dropped (a rain alert after its rain began), and so is an
announcement a game held past its game expiry (a resin reminder the game played made out of
date), which a focus block alone keeps however long it lasts. A text queued more than once is
said once. A private request (her nightly journal question, the weekly game report) that comes
to be answered while guest mode is on is dropped too.

While an alarm rings (`waifu.alarm`), its rings come first, each its own clip at the ring's
level, also while `quiet` holds, and her requests wait until it stops. Master's whole utterance
"aku sudah bangun" or "tunda" (`alarm_request`) stops or snoozes it without the brain or her
name, like "ulangi": no turn.

When Master's whole utterance asks her to repeat ("ulangi", "tadi bilang apa?": `waifu.repeat`),
she says her last words on the PC again (her reply, the announcements said together, or her
answer to a request) without the brain: no turn, so a question waiting for Master's yes still
waits for his next words. Once guest mode has switched since she said them, there is nothing to
repeat.

The owner's paired phone (`waifu.phone`) talks to her from the phone server's threads:
`reply_for_phone` and `transcribe_for_phone`, handed to the server as `PhoneAnswers`. Brain
turns, the voice loop's and the phone's, are taken one at a time (the history is
append-only), and a phone reply is voiced for the phone, never on the PC. While the phone
camera is offered, a phone message may carry a photo: it waits in the `TurnImages` she shares
with the brain only for the turn of that message. A message sent with the viewfinder open,
with or without its photo, also makes only its own turn a camera turn in the `CameraTurn` she
shares with the screen tool, so "coba lihat ini" is never taken as asking for the PC screen.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import logging
import queue
import threading
import time
import wave
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime
from typing import Protocol

import numpy as np

from waifu.alarm import AlarmBell, AlarmRequest, alarm_request
from waifu.avatar.bus import SPEAK_FPS
from waifu.avatar.emotion import NEUTRAL, split_emotion
from waifu.avatar.lipsync import viseme_track
from waifu.brain.attachments import CameraTurn, TurnImages
from waifu.brain.base import Brain, BrainReply, ToolCallRecord, ToolImage
from waifu.brain.tools import logged_arguments
from waifu.config import AppConfig
from waifu.gate import WakeGate
from waifu.phone.server import PhoneReply
from waifu.repeat import is_repeat_request
from waifu.schedule import ScheduleError
from waifu.voice.audio import resample
from waifu.voice.base import SpeechToText, Utterance
from waifu.voice.speaker import SpeakerError
from waifu.voice.stt import SttError
from waifu.voice.tts import TtsError

logger = logging.getLogger(__name__)

# What she says herself; `{owner}` is filled with what she calls the owner (owner_call).
ACKNOWLEDGEMENT = "はい、{owner}?"
ACKNOWLEDGEMENT_EMOTION = "happy"
# A reply that was only an emotion tag (say "[senang]" after a tool call) still gets spoken
# words, picked like the brains pick theirs for an empty reply.
TAG_ONLY_DONE = "Sudah, {owner}."
TAG_ONLY_FAILED = "Maaf {owner}, ada perintah yang gagal dijalankan."
TAG_ONLY_NO_ANSWER = "Maaf {owner}, aku belum bisa menjawab itu."
# Her voice on the phone: a mono 16-bit PCM WAV at this rate.
PHONE_SAMPLE_RATE = 24000
# What the phone page's camera sends: one JPEG per message.
PHOTO_MEDIA_TYPE = "image/jpeg"
PCM16_SCALE = 32767  # full scale of a 16-bit sample, symmetric so +1 and -1 both fit
# Said before the announcements a game held (game mode), once the game has closed.
GAME_HELD = "Tadi selama main ada kabar."
# Her answer to "ulangi" when she has said nothing she remembers, or guest mode has switched
# since (`Assistant._repeat`).
NOTHING_TO_REPEAT = "Belum ada yang bisa kuulangi, {owner}."
# Her answers to Master's words to a ringing alarm (`Assistant._answer_alarm`), and the emotion
# she rings and answers with.
ALARM_STOPPED = "Oke {owner}, alarmnya kumatikan."
ALARM_SNOOZED = "Oke, kubangunkan lagi jam {time}."
ALARM_NOT_SNOOZED = "Alarmnya tidak bisa ditunda: {problem}"
ALARM_ENDED = "Alarmnya sudah berhenti, {owner}."
ALARM_EMOTION = "happy"


def split_reply(reply: BrainReply, *, owner_call: str = "Master") -> tuple[str, BrainReply]:
    """The reply's emotion and the reply without its tags, never left without words."""
    emotion, text = split_emotion(reply.text)
    if not text:
        text = _tag_only_fallback(reply.tool_calls).format(owner=owner_call)
    return emotion, dataclasses.replace(reply, text=text)


def _tag_only_fallback(tool_calls: list[ToolCallRecord]) -> str:
    if any(call.is_error for call in tool_calls):
        return TAG_ONLY_FAILED
    return TAG_ONLY_DONE if tool_calls else TAG_ONLY_NO_ANSWER


def _called_successfully(reply: BrainReply, tool: str) -> bool:
    return any(call.name == tool and not call.is_error for call in reply.tool_calls)


def _expired(expires_at: datetime | None, now: datetime) -> bool:
    """Whether `expires_at` (None: never) has passed at `now`.

    Both are compared as aware times, a naive one read as the PC's local time: the wall clock
    (`datetime.now`) is naive while the journal question expires in WIB, and comparing those
    directly raises TypeError.
    """
    return expires_at is not None and now.astimezone() >= expires_at.astimezone()


def _take_all(announcements: queue.SimpleQueue[_Announcement]) -> list[_Announcement]:
    """Every announcement queued so far, oldest first."""
    taken: list[_Announcement] = []
    while True:
        try:
            taken.append(announcements.get_nowait())
        except queue.Empty:
            return taken


def _marked_held_by_game(announcements: list[_Announcement]) -> list[_Announcement]:
    return [dataclasses.replace(item, held_by_game=True) for item in announcements]


def _pass_on(on_reply: ReplyHandler, text: str) -> None:
    """Hand what she said by herself to `on_reply`; a failure is logged, never raised."""
    try:
        on_reply(text)
    except Exception:
        logger.exception("Could not pass on what she said by herself")


def _pcm16_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    """Mono float audio as a 16-bit PCM WAV file; samples beyond -1..1 are clipped."""
    samples = np.round(np.clip(audio, -1.0, 1.0) * PCM16_SCALE).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(sample_rate)
        clip.writeframes(samples.tobytes())
    return buffer.getvalue()


def _read_pcm16_wav(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    """The float32 samples (-1..1) and rate of a mono 16-bit PCM WAV.

    ValueError for a WAV of another shape; wave.Error or EOFError for bytes that are not one.
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as recording:
        if (recording.getnchannels(), recording.getsampwidth()) != (1, 2):
            raise ValueError("expected a mono 16-bit PCM WAV")
        sample_rate = recording.getframerate()
        frames = recording.readframes(recording.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768, sample_rate


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
class _Announcement:
    """A fixed text queued by `announce` or `remind`."""

    text: str
    emotion: str
    expires_at: datetime | None
    game_expires_at: datetime | None  # counted only when a game held it
    held_by_game: bool = False  # a game held it (`Assistant._note_game_hold`)


@dataclasses.dataclass(frozen=True)
class _Said:
    """What she last said on the PC by `_say_remembered`, for Master's "ulangi"."""

    text: str
    emotion: str
    marker: object  # `guest_marker` before the turn that thought it up (None without one)


@dataclasses.dataclass(frozen=True)
class _Request:
    """A prompt queued by `ask_aloud`."""

    prompt: str
    on_reply: ReplyHandler | None
    required_tool: str | None
    new_session: bool
    expires_at: datetime | None
    private: bool  # dropped while guest mode is on


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
        turn_images: TurnImages | None = None,
        camera_turn: CameraTurn | None = None,
        quiet: Callable[[], bool] | None = None,
        game_holding: Callable[[], bool] | None = None,
        guest_mode: Callable[[], bool] | None = None,
        guest_marker: Callable[[], object] | None = None,
        alarm: AlarmBell | None = None,
        hold_level: Callable[[int], AbstractContextManager[object]] | None = None,
    ) -> None:
        """`quiet` (the focus timer's `in_block`, in game mode also whether a game holds her
        announcements) says when what she would say by herself must wait: only `remind` is
        said then. `game_holding` (game mode's `holding`, part of `quiet`) tells the waits a
        game made: the announcements it held come out after GAME_HELD. `guest_mode` (guest
        mode's `is_on`) says when her private requests are dropped. `guest_marker` (guest
        mode's `generation`) moves whenever guest mode switches: her last words said before
        it moved are never repeated. `alarm` is the bell whose rings she says; `hold_level`
        (`LevelHold.held`) plays one clip, a ring, at a level of its own."""
        self._name = config.assistant.name
        self._owner_call = config.assistant.owner_call
        # Left out of Master's words when they ask her to repeat (`is_repeat_request`).
        self._names = (self._name, *config.assistant.wake_words, self._owner_call)
        self._followup_s = config.assistant.followup_seconds
        self._session_idle_s = config.assistant.session_idle_minutes * 60
        self._brain = brain
        self._stt = stt
        self._speech = speech
        self._player = player
        self._gate = gate
        self._verifier = verifier if speaker_check and config.speaker.enabled else None
        self._avatar = avatar
        self._now = now
        self._clock = clock
        # Shared with the brain while the phone camera is offered; None otherwise.
        self._turn_images = turn_images
        # Shared with the screen tool while the phone camera is offered; None otherwise.
        self._camera_turn = camera_turn
        self._quiet = quiet
        self._game_holding = game_holding
        self._guest_mode = guest_mode
        self._guest_marker = guest_marker
        self._alarm = alarm
        self._hold_level = hold_level
        self._mic: Microphone | None = None
        self._segmenter: Segmenter | None = None
        self._speaker_error_logged = False
        self._avatar_failing = False
        self._announcements: queue.SimpleQueue[_Announcement] = queue.SimpleQueue()
        # Announcements that also wait while the follow-up window is open (`announce`).
        self._patient_announcements: queue.SimpleQueue[_Announcement] = queue.SimpleQueue()
        # What Master asked for himself (`remind`): also said while `quiet` holds.
        self._reminders: queue.SimpleQueue[_Announcement] = queue.SimpleQueue()
        self._requests: queue.SimpleQueue[_Request] = queue.SimpleQueue()
        # What a game held, out of `_announcements` and `_patient_announcements` in the order
        # queued and marked `held_by_game` (`_note_game_hold`); only `run`'s thread uses them.
        self._game_held: list[_Announcement] = []
        self._patient_game_held: list[_Announcement] = []
        # After a cut, on `clock`: announcements and requests wait until then
        # (see `_hold_announcements`).
        self._announcements_held_until: float | None = None
        # When the follow-up window last opened by `_open_followup` ends, on `clock`: requests
        # wait until then, since Master may still answer her.
        self._followup_until: float | None = None
        # Until when, on `clock`, the brain's conversation may still be Master's phone chat
        # (`session_idle_minutes` after his last phone message): requests wait until then.
        self._phone_chat_until: float | None = None
        self._voice = threading.Lock()  # one voice: replies, announcements, and reactions
        # One brain turn at a time, the voice loop's or the phone's; never held while speaking.
        self._turn = threading.Lock()
        self._engaged_lock = threading.Lock()
        self._engaged_count = 0
        self._speaking = False
        # Her last words on the PC, for "ulangi"; only `run`'s thread uses it (`_say_remembered`).
        self._last_said: _Said | None = None

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
            request = self._alarm_request(text)
            if request is not None:
                self._announcements_held_until = None  # Master has been heard after a cut
                return self._answer_alarm(request)
            result = self._gate.check(text, heard_at=utterance.started_at)
            if not result.accepted:
                logger.info("Not addressed to %s (%s); ignoring", self._name, result.reason)
                return None
            self._announcements_held_until = None  # Master has been heard after a cut
            if not result.command:
                # A bare wake word ("Cyrene?") gets a quick answer without a round trip to the LLM.
                reply_text = ACKNOWLEDGEMENT.format(owner=self._owner_call)
                self.speak(reply_text, ACKNOWLEDGEMENT_EMOTION)
            elif is_repeat_request(result.command, self._names):
                reply_text = self._repeat()
            else:
                self._show(lambda avatar: avatar.state("thinking"))
                marker = self._guest_generation()
                emotion, reply = self._think(result.command)
                reply_text = reply.text
                self._say_remembered(reply_text, emotion, marker)
            return reply_text

    def handle_text(self, text: str) -> BrainReply:
        """Ask the brain directly: no wake word, no voice (text chat); the reply has no tag."""
        return self._think(text)[1]

    def reply_for_phone(
        self, text: str, image: bytes | None = None, *, camera: bool = False
    ) -> PhoneReply:
        """Her reply to what Master typed or said on the paired phone, voiced for the phone.

        One brain turn like any other (the confirmation and screen gates count it), taken
        after the turn another thread is thinking up. `image` is the JPEG the phone camera
        took as the message was sent: she sees it in this turn only. Without the camera's
        TurnImages (the camera is not offered) it is dropped with a warning. `camera` tells
        that the message was sent with the viewfinder open, also when its photo could not be
        taken (a photo always was): that turn is a camera turn, in which only words naming the
        screen let her look at the PC's. Nothing is said on the PC, whose microphone,
        follow-up window, and avatar stay as they are, but `ask_aloud` requests wait until
        `session_idle_minutes` after the message (`_speak_up`). Her voice comes back as a
        16-bit WAV at PHONE_SAMPLE_RATE with the avatar's mouth track; when it cannot be made
        (the logs say why), the reply is only its text. Safe from any thread.
        """
        logger.info("Phone message: %s", text)
        self._phone_chat_until = self._clock() + self._session_idle_s
        emotion, reply = self._think(text, photo=image, camera=camera or image is not None)
        try:
            audio, sample_rate = self._speech.synthesize(reply.text)
        except Exception as exc:
            if isinstance(exc, TtsError):
                logger.warning("Could not synthesise the phone reply: %s", exc)
            else:
                logger.exception("Could not synthesise the phone reply")
            return PhoneReply(reply.text, emotion, None, SPEAK_FPS, [], 0.0)
        voice = resample(audio, sample_rate, PHONE_SAMPLE_RATE)
        return PhoneReply(
            reply.text,
            emotion,
            _pcm16_wav(voice, PHONE_SAMPLE_RATE),
            SPEAK_FPS,
            viseme_track(voice, PHONE_SAMPLE_RATE, SPEAK_FPS),
            len(voice) / PHONE_SAMPLE_RATE,
        )

    def transcribe_for_phone(self, wav_bytes: bytes) -> str:
        """The words in a mono 16-bit PCM WAV recorded on the paired phone; empty when none.

        No speaker verification (the voiceprint fits the PC's microphone, and only a paired
        phone gets here) and no wake word: everything said on the phone is for her. Safe
        from any thread.
        """
        audio, sample_rate = _read_pcm16_wav(wav_bytes)
        transcript = self._stt.transcribe(audio, sample_rate)
        text = transcript.text.strip()
        if text:
            logger.info("Heard on the phone (%s): %s", transcript.language, text)
        return text

    def announce(
        self,
        text: str,
        emotion: str = NEUTRAL,
        *,
        wait_for_followup: bool = False,
        expires_at: datetime | None = None,
        game_expires_at: datetime | None = None,
    ) -> None:
        """Queue something to say by herself; safe from any thread (the gacha watcher's).

        `run` says it between utterances, never during a reply, while Master speaks, or
        while she listens for Master after being cut off. With `wait_for_followup` it also
        waits while the follow-up window is open, like an `ask_aloud` request: news that is
        not due at a set time (the budget notice, queued while she thinks up the reply that
        crossed the budget) never uses up the window in which Master answers her. One still
        waiting when `expires_at` (on `now`) has passed is dropped, logged and never said: a
        rain alert held through a focus block or a game until its rain began. None never
        expires. `game_expires_at` is an expiry only for what a game held (game mode): one a
        game held that is still waiting then is dropped the same way, since the game played may
        have changed what it tells (a resin reminder, a hot GPU), while a focus block alone
        keeps it however long the block lasts, as before game mode.
        """
        announcement = _Announcement(text, emotion, expires_at, game_expires_at)
        if wait_for_followup:
            self._patient_announcements.put(announcement)
        else:
            self._announcements.put(announcement)

    def remind(self, text: str, emotion: str = NEUTRAL) -> None:
        """Queue something Master asked for himself, a reminder he set or the focus timer's
        line; safe from any thread.

        Said like a plain `announce`ment (between utterances, also while the follow-up window
        is open, before the announcements queued with it), and also while `quiet` holds.
        """
        self._reminders.put(_Announcement(text, emotion, None, None))

    def ask_aloud(
        self,
        prompt: str,
        *,
        on_reply: ReplyHandler | None = None,
        required_tool: str | None = None,
        new_session: bool = False,
        expires_at: datetime | None = None,
        private: bool = False,
    ) -> None:
        """Queue a prompt for the brain whose reply she says by herself; safe from any thread.

        `run` handles it between utterances under the same conditions as announcements,
        on a later frame than any announcements queued with it, never while a follow-up
        window is open (Master may still answer her), Master chats on the phone (`_speak_up`),
        or `quiet` holds, and one request at a time: she thinks up the reply, says it like a
        reply (the follow-up window opens, so Master can answer without the wake word), and
        hands the spoken text to `on_reply` on a thread of its own, so a slow push never keeps
        `run` from the microphone.

        A brain answers a failure (no connection, a refusal) with an apology, never an
        exception. With `required_tool`, a reply whose turn has no successful call of that
        tool counts as such a failure: nobody asked, so it is logged, not said or passed on.
        With `new_session` the brain forgets the conversation first, so the reply sees nothing
        said before it: the morning greeting never repeats an earlier finance answer's figures.
        A request still waiting when `expires_at` (on `now`) has passed is dropped, logged and
        never said: her nightly question held past midnight would ask about a day that has
        ended. None never expires. A `private` request (Master's journal, his game accounts)
        that comes to be answered while guest mode is on is dropped the same way, before its
        turn: guests may be listening.
        """
        self._requests.put(
            _Request(prompt, on_reply, required_tool, new_session, expires_at, private)
        )

    def speak(
        self,
        text: str,
        emotion: str = NEUTRAL,
        *,
        followup: bool = True,
        level: int | None = None,
    ) -> None:
        """Say `text` with the microphone paused and the avatar mouthing it with `emotion`.

        A reply opens the follow-up window afterwards, also when `interrupt()` cuts it
        short; with `followup=False` (an announcement, not a reply) it stays as it was. With
        `level` (an alarm's ring) the clip is played at that level through `hold_level`.
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
                with self._held_level(level):
                    played = self._player.play(audio, sample_rate)
                if not played:
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

    def _phone_chatting(self) -> bool:
        phone_chat_until = self._phone_chat_until
        return phone_chat_until is not None and self._clock() < phone_chat_until

    def _speak_up(self) -> None:
        """Say the queued announcements, or else answer a request once Master has had his say.

        A request never follows announcements in the same pass: the next frame checks again
        that Master has not started talking and did not cut them short to talk. Nor does it,
        or an announcement that waits for the window, come while the follow-up window is
        open, so a conversation is never taken over. Nor does a request come while Master
        chats on the phone (until `session_idle_minutes` after his last phone message): its
        new session would wipe that conversation, and he would not hear it on the PC, so his
        next phone message would answer a question he never heard. While `quiet` holds, only
        reminders are said: the rest waits in its queue. A due alarm ring comes before all of
        them, alone in its pass; while an alarm rings, requests wait.
        """
        if self._say_ring():
            return
        followup_open = self._followup_open()
        quiet = self._quiet is not None and self._quiet()
        ringing = self._alarm is not None and self._alarm.ringing()
        if not self._say_announcements(followup_open, quiet) and not (
            followup_open or quiet or ringing or self._phone_chatting()
        ):
            self._answer_next_request()

    def _say_ring(self) -> bool:
        """Say the alarm's due ring, if any, as its own clip at its level, like a reply (so
        "aku sudah bangun" needs no name); never remembered for "ulangi". True when it rang."""
        ring = None if self._alarm is None else self._alarm.due_ring()
        if ring is None:
            return False
        logger.info("Ringing the alarm (ring %d, %d percent)", ring.index + 1, ring.level)
        self.speak(ring.text, ALARM_EMOTION, level=ring.level)
        return True

    def _alarm_request(self, text: str) -> AlarmRequest | None:
        """What Master's whole utterance asks of the alarm while it rings, or None."""
        if self._alarm is None or not self._alarm.ringing():
            return None
        return alarm_request(text, self._names)

    def _answer_alarm(self, request: AlarmRequest) -> str:
        """Stop or snooze the ringing alarm without the brain and say so like a reply; returns
        what she said. No turn is taken, so a question waiting for Master's yes still waits."""
        assert self._alarm is not None  # only asked while a bell rings
        owner = self._owner_call
        if request.kind == "stop":
            self._alarm.stop()
            logger.info("Alarm stopped by voice")
            text = ALARM_STOPPED.format(owner=owner)
        else:
            try:
                entry = self._alarm.snooze(request.minutes)
            except ScheduleError as exc:
                logger.info("Could not snooze the alarm by voice: %s", exc)
                text = ALARM_NOT_SNOOZED.format(problem=exc)
            else:
                if entry is None:  # it rang out meanwhile
                    text = ALARM_ENDED.format(owner=owner)
                else:
                    logger.info("Alarm snoozed by voice")
                    text = ALARM_SNOOZED.format(time=f"{entry.at:%H.%M}")
        self.speak(text, ALARM_EMOTION)
        return text

    def _held_level(self, level: int | None) -> AbstractContextManager[object]:
        if level is None or self._hold_level is None:
            return nullcontext()
        return self._hold_level(level)

    def _say_announcements(self, followup_open: bool, quiet: bool) -> bool:
        """Say every queued reminder and announcement in one go, reminders first, with the
        first one's emotion, except the announcements while `quiet`, those that wait while
        the follow-up window is open, and those that have expired (dropped; by their game
        expiry too when a game held them). A text queued more than once is said once, and the
        announcements a game held come first, after GAME_HELD. True when there was any to say."""
        reminders = _take_all(self._reminders)
        announcements: list[_Announcement] = []
        if quiet:
            self._note_game_hold()
        else:
            announcements = self._game_held + _take_all(self._announcements)
            self._game_held = []
        if not (quiet or followup_open):
            announcements += self._patient_game_held + _take_all(self._patient_announcements)
            self._patient_game_held = []
        news = sorted(
            (item for item in announcements if self._in_date(item)),
            key=lambda item: not item.held_by_game,
        )
        lead_in = [GAME_HELD] if news and news[0].held_by_game else []
        due = reminders + news
        if not due:
            return False
        texts = [reminder.text for reminder in reminders] + lead_in + [item.text for item in news]
        text = " ".join(dict.fromkeys(texts))
        logger.info("Announcing: %s", text)
        self._say_remembered(text, due[0].emotion, self._guest_generation(), followup=False)
        return True

    def _note_game_hold(self) -> None:
        """While a game holds them, mark the queued announcements as held by a game (for
        GAME_HELD and their game expiry) and keep them aside in order. One queued after the
        game has closed stays unmarked, though a focus block may hold it on."""
        if self._game_holding is None or not self._game_holding():
            return
        self._game_held += _marked_held_by_game(_take_all(self._announcements))
        self._patient_game_held += _marked_held_by_game(_take_all(self._patient_announcements))

    def _in_date(self, announcement: _Announcement) -> bool:
        """Whether `announcement` has not expired, its game expiry counted only when a game
        held it; an expired one is logged, not its text."""
        now = self._now()
        if _expired(announcement.expires_at, now):
            logger.info(
                "Not saying an announcement held past %s: it is out of date",
                announcement.expires_at,
            )
            return False
        if announcement.held_by_game and _expired(announcement.game_expires_at, now):
            logger.info(
                "Not saying an announcement a game held past %s: it is out of date",
                announcement.game_expires_at,
            )
            return False
        return True

    def _answer_next_request(self) -> None:
        """Answer the oldest `ask_aloud` request aloud, if any.

        One at a time: the next waits until Master has had the chance to answer this reply
        (its follow-up window has closed). A failure is logged and listening goes on, as for
        an utterance.
        """
        request = self._next_request_due()
        if request is None:
            return
        logger.info("Speaking up by herself in answer to a request")
        try:
            with self._engaged():
                self._show(lambda avatar: avatar.state("thinking"))
                marker = self._guest_generation()
                emotion, reply = self._think(request.prompt, new_session=request.new_session)
                tool = request.required_tool
                if tool is not None and not _called_successfully(reply, tool):
                    logger.warning(
                        "Not saying the answer to a request: its turn has no successful %s "
                        "call (the brain most likely failed and apologised)",
                        tool,
                    )
                    self._show(lambda avatar: avatar.state("idle"))
                    return
                self._say_remembered(reply.text, emotion, marker)
        except Exception:
            logger.exception("Failed to answer a request aloud; still listening")
            return
        if request.on_reply is not None:
            # A push to the phone may take its whole timeout: never on this thread, which must
            # be back at the microphone while the follow-up window is open.
            threading.Thread(
                target=_pass_on, args=(request.on_reply, reply.text), name="pass-on", daemon=True
            ).start()

    def _next_request_due(self) -> _Request | None:
        """The oldest queued request that has not expired and is not private while guest mode
        is on, if any; the dropped ones before it go in the same pass. Checked once, before the
        turn: a reply thought up in time is said even when its speech ends after the expiry,
        since its turn saw the old day."""
        while True:
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                return None
            if _expired(request.expires_at, self._now()):
                logger.info(
                    "Not saying a request held past %s: it is out of date", request.expires_at
                )
            elif request.private and self._guest_mode is not None and self._guest_mode():
                logger.info("Not saying a private request while guest mode is on")
            else:
                return request

    def _say_remembered(
        self, text: str, emotion: str, marker: object, *, followup: bool = True
    ) -> None:
        """Say `text` like `speak`, remembered first as her last words for "ulangi" (`_repeat`),
        so a reply that was cut off or could not be voiced can still be asked for again.

        Only `run`'s thread calls it, for what she says as her own: a reply to Master's
        words, the announcements said together, or her answer to a request. Never for a
        reaction, the acknowledgement, the startup line, or the phone. `marker` is the guest
        marker read before the turn that thought `text` up, so a reply whose turn switched
        guest mode is never repeated: guest mode refuses per tool call, so that turn may have
        fetched something private before the switch, and the brains drop it with their new
        session.
        """
        self._last_said = _Said(text, emotion, marker)
        self.speak(text, emotion, followup=followup)

    def _repeat(self) -> str:
        """Say her last words again like a reply, without the brain; returns what she said.

        No turn is taken, so the brain, its history, and its gates (a question waiting for
        Master's yes) never see it. With nothing to repeat, also once guest mode has switched
        since (her words are then forgotten), she says NOTHING_TO_REPEAT. The text never
        reaches the INFO log, like a reply's.
        """
        said = self._last_said
        if said is not None and said.marker != self._guest_generation():
            self._last_said = said = None
        if said is None:
            logger.info("Nothing to repeat")
            text = NOTHING_TO_REPEAT.format(owner=self._owner_call)
            self.speak(text)
            return text
        logger.info("Repeating what she said last")
        self.speak(said.text, said.emotion)
        return said.text

    def _guest_generation(self) -> object:
        return None if self._guest_marker is None else self._guest_marker()

    def _think(
        self,
        text: str,
        *,
        photo: bytes | None = None,
        camera: bool = False,
        new_session: bool = False,
    ) -> tuple[str, BrainReply]:
        """The brain's reply to `text`, split into its emotion and the reply without the tag.

        Waits while another thread's turn is thought up: the history takes one turn at a time.
        `photo` (a JPEG from the phone camera) goes with this turn only (`_showing`), and with
        `camera` only this turn is a camera turn (`_through_camera`). With `new_session` the
        brain forgets the conversation first, under the same lock.
        """
        with self._turn, self._through_camera(camera), self._showing(photo):
            if new_session:
                logger.info("Starting a new conversation for a request she answers by herself")
                self._brain.reset()
            reply = self._brain.respond(text, now=self._now())
        for call in reply.tool_calls:
            # The result itself only at DEBUG: the INFO log (logs\waifu.log under autostart)
            # must not collect the owner's pull history, nor a finance or journal call's arguments.
            outcome = "error" if call.is_error else "ok"
            arguments = logged_arguments(call.name, call.arguments)
            logger.info("Tool %s(%s) -> %s", call.name, arguments, outcome)
            logger.debug("Tool %s result: %s", call.name, call.result)
        return split_reply(reply, owner_call=self._owner_call)

    @contextlib.contextmanager
    def _showing(self, photo: bytes | None) -> Iterator[None]:
        """Hand `photo` to the brain for the turn inside the block, under the turn lock.

        The shared TurnImages is cleared afterwards, also when the turn fails, so the photo
        never reaches a later turn (a voice turn included). Without one (the camera is not
        offered) the photo is dropped with a warning. Photos are private: only their size is
        ever logged.
        """
        images = self._turn_images
        if photo is not None and images is None:
            logger.warning(
                "Dropped a photo of %d bytes from the phone: the camera is not offered", len(photo)
            )
        if photo is None or images is None:
            yield
            return
        images.put(ToolImage(PHOTO_MEDIA_TYPE, photo))
        try:
            yield
        finally:
            images.clear()

    @contextlib.contextmanager
    def _through_camera(self, camera: bool) -> Iterator[None]:
        """With `camera`, mark the turn inside the block, under the turn lock, as sent through
        the phone camera in the CameraTurn shared with the screen tool.

        The mark ends afterwards, also when the turn fails, so a later turn (a voice turn
        included) is never taken for one. Without a CameraTurn (the camera is not offered)
        there is nothing to mark.
        """
        camera_turn = self._camera_turn
        if not camera or camera_turn is None:
            yield
            return
        camera_turn.begin()
        try:
            yield
        finally:
            camera_turn.end()

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


class PhoneAnswers:
    """The Assistant as the phone server's `PhoneBackend`."""

    def __init__(self, assistant: Assistant) -> None:
        self._assistant = assistant

    def chat(self, text: str, image: bytes | None = None, *, camera: bool = False) -> PhoneReply:
        return self._assistant.reply_for_phone(text, image, camera=camera)

    def transcribe(self, wav_bytes: bytes) -> str:
        return self._assistant.transcribe_for_phone(wav_bytes)
