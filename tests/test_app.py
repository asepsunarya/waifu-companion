import base64
import contextlib
import http.client
import io
import json
import logging
import random
import struct
import threading
import wave
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from waifu.alarm import AlarmBell
from waifu.app import GAME_HELD, Assistant, PhoneAnswers, Speech
from waifu.avatar.lipsync import viseme_track
from waifu.brain.attachments import CameraTurn, TurnImages
from waifu.brain.base import Brain, BrainReply, ToolCallRecord, ToolImage
from waifu.brain.claude import ClaudeBrain
from waifu.brain.confirm import ConfirmationGate
from waifu.brain.screen_tools import ScreenTools
from waifu.config import (
    AppConfig,
    AssistantConfig,
    AvatarConfig,
    BrainConfig,
    ClaudeConfig,
    GachaConfig,
    PcHealthConfig,
    PhoneConfig,
    SpeakerConfig,
)
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.finance.values import WIB
from waifu.gacha.hoyolab import GameStatus
from waifu.gacha.watcher import GachaWatcher
from waifu.gate import WakeGate
from waifu.journal import JOURNAL_PROMPT
from waifu.lexicon import Lexicon
from waifu.listen_pause import ListenPause
from waifu.pc_health import Drive, GpuStatus, PcSnapshot
from waifu.pc_watch import PcWatcher
from waifu.phone.auth import PhoneAuth
from waifu.phone.server import PhoneBackend, PhoneReply, PhoneServer
from waifu.schedule import AlarmAction, Entry, Reminder, ScheduleStore
from waifu.screen import ScreenImage, encode_png
from waifu.voice.base import SpeechToText, TextToSpeech, Transcript, Utterance
from waifu.voice.diagnosis import (
    ACCEPTED,
    EMPTY,
    NO_WAKE,
    NOT_OWNER,
    UtteranceLog,
    UtteranceRecord,
)
from waifu.voice.nod import NodBank
from waifu.voice.speaker import SpeakerError
from waifu.voice.stt import SttError
from waifu.voice.tts import SpeechRouter, TtsError
from waifu.voice.volume import LevelHold, VolumePlayer

NOW = datetime(2026, 10, 12, 19, 30)
SAMPLE_RATE = 16000
REPLY = "Siap, Master! Lampu kamar sudah menyala."


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeBrain(Brain):
    def __init__(self, reply: str = REPLY) -> None:
        self.reply = reply
        self.turns: list[tuple[str, datetime | None]] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.turns.append((user_text, now))
        call = ToolCallRecord(
            "control_device", {"device": "lampu_kamar", "command": "on"}, '{"ok": true}', False
        )
        return BrainReply(self.reply, [call])

    def reset(self) -> None:
        self.turns.clear()


class FakeStt(SpeechToText):
    """Returns the queued transcripts in order (or raises a queued exception)."""

    def __init__(self, *heard: str | Exception) -> None:
        self.heard = list(heard)
        self.calls = 0
        self.audio: list[tuple[np.ndarray, int]] = []

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        self.calls += 1
        self.audio.append((audio, sample_rate))
        text = self.heard.pop(0)
        if isinstance(text, Exception):
            raise text
        return Transcript(text, "id")


class FakeVerifier:
    def __init__(self, result: tuple[bool, float] | Exception) -> None:
        self.result = result
        self.calls = 0
        self.threshold = 0.45

    def verify(self, audio: np.ndarray, sample_rate: int) -> tuple[bool, float]:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeSpeech:
    """Voices every text as `voice` (audio, sample rate), or fails with `error`."""

    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.texts: list[str] = []
        self.voice = (np.zeros(2400, dtype=np.float32), 24000)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        self.events.append("synthesize")
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        return self.voice


class FakePlayer:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.played: list[int] = []
        self.peaks: list[float] = []  # each clip's loudest sample
        self.on_play: Callable[[], None] = lambda: None  # runs while the audio "plays"
        self.playing = False
        self.cut = threading.Event()  # set by `stop()` while the audio plays

    def play(self, audio: np.ndarray, sample_rate: int) -> bool:
        self.events.append("play")
        self.cut.clear()
        self.playing = True
        try:
            self.on_play()
        finally:
            self.playing = False
        if self.error is not None:
            raise self.error
        self.played.append(sample_rate)
        self.peaks.append(float(np.max(np.abs(audio))))
        return not self.cut.is_set()

    def stop(self) -> bool:
        if not self.playing:
            return False
        self.cut.set()
        return True


class FakeMic:
    """Yields the given frames, then behaves like Ctrl+C pressed while listening."""

    def __init__(self, events: list[str], frames: list[np.ndarray]) -> None:
        self.events = events
        self._frames = frames

    def frames(self) -> Iterator[np.ndarray]:
        yield from self._frames
        raise KeyboardInterrupt

    def pause(self) -> None:
        self.events.append("mic.pause")

    def resume(self) -> None:
        self.events.append("mic.resume")


class FakeSegmenter:
    """Returns the queued `feed` results in order; `busy` follows the queued flags after each."""

    def __init__(
        self, events: list[str], outputs: list[Utterance | None], busy: list[bool] | None = None
    ) -> None:
        self.events = events
        self._outputs = list(outputs)
        self._busy_after = list(busy or [])
        self.busy = False

    def feed(self, frame: np.ndarray) -> Utterance | None:
        self.busy = self._busy_after.pop(0) if self._busy_after else False
        return self._outputs.pop(0)

    def reset(self) -> None:
        self.events.append("segmenter.reset")


class FakeAvatar:
    """Stands in for the AvatarBus: logs what the voice loop shows, or fails like a broken one."""

    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.spoken: list[tuple[int, int, str]] = []
        self.peaks: list[float] = []  # the loudest sample of each clip it mouths

    def state(self, name: str, followup_s: float | None = None) -> None:
        followup = "" if followup_s is None else f" {followup_s:g}"
        self.events.append(f"avatar.state {name}{followup}")
        if self.error is not None:
            raise self.error

    def speak(self, audio: np.ndarray, sample_rate: int, emotion: str) -> None:
        self.events.append(f"avatar.speak {emotion}")
        if self.error is not None:
            raise self.error
        self.spoken.append((len(audio), sample_rate, emotion))
        self.peaks.append(float(np.max(np.abs(audio))))

    def hearing(self, on: bool) -> None:
        self.events.append(f"avatar.hearing {'on' if on else 'off'}")
        if self.error is not None:
            raise self.error


class FakeTimer:
    """A timer that never runs by itself: the test calls `function` once its time has passed."""

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        self.interval = interval
        self.function = function
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class FakeTimers:
    """Makes FakeTimers and keeps them in order (`made`); `fire` runs the last one's function."""

    def __init__(self) -> None:
        self.made: list[FakeTimer] = []

    def __call__(self, interval: float, function: Callable[[], None]) -> FakeTimer:
        self.made.append(FakeTimer(interval, function))
        return self.made[-1]

    def fire(self) -> None:
        self.made[-1].function()


class Rig:
    """An Assistant wired to fakes that share one event log and one clock."""

    def __init__(
        self,
        *heard: str | Exception,
        owner: tuple[bool, float] | Exception = (True, 0.71),
        reply: str = REPLY,
        brain: Callable[[str], Brain] = FakeBrain,
        speech_error: Exception | None = None,
        player_error: Exception | None = None,
        config: AppConfig | None = None,
        with_verifier: bool = True,
        speaker_check: bool = True,
        with_avatar: bool = False,
        avatar_error: Exception | None = None,
        turn_images: TurnImages | None = None,
        camera_turn: CameraTurn | None = None,
        quiet: Callable[[], bool] | None = None,
        game_holding: Callable[[], bool] | None = None,
        guest_mode: Callable[[], bool] | None = None,
        guest_marker: Callable[[], object] | None = None,
        volume: int | None = None,
        alarm: AlarmBell | None = None,
        diagnosis: UtteranceLog | None = None,
        listen_pause: ListenPause | None = None,
        lexicon: Lexicon | None = None,
        speech: Speech | None = None,
        nods: NodBank | None = None,
        real_timers: bool = False,
    ) -> None:
        """`speech` voices her words in place of the rig's FakeSpeech (`self.speech`). Her
        filler timers are fakes (`self.timers`) that only the test fires, or with
        `real_timers` the Assistant's own."""
        config = config or AppConfig()
        self.events: list[str] = []
        self.clock = FakeClock()
        self.now = NOW  # the wall clock the brain's turns are stamped with
        self.brain = brain(reply)
        self.stt = FakeStt(*heard)
        self.verifier = FakeVerifier(owner)
        self.speech = FakeSpeech(self.events, speech_error)
        self.player = FakePlayer(self.events, player_error)
        self.avatar = FakeAvatar(self.events, avatar_error)
        self.gate = WakeGate(
            config.assistant.wake_words, config.assistant.followup_seconds, clock=self.clock
        )
        # Her level of now (`volume`, else 100), which a ring of `alarm` holds for its clip.
        self.hold = LevelHold(lambda: 100 if volume is None else volume)
        played_through = volume is not None or alarm is not None
        self.timers = FakeTimers()
        timers = {} if real_timers else {"timer": self.timers}
        self.assistant = Assistant(
            config,
            brain=self.brain,
            stt=self.stt,
            speech=self.speech if speech is None else speech,
            player=VolumePlayer(self.player, self.hold.level_now)
            if played_through
            else self.player,
            gate=self.gate,
            verifier=self.verifier if with_verifier else None,
            speaker_check=speaker_check,
            avatar=self.avatar if with_avatar or avatar_error else None,
            now=lambda: self.now,
            clock=self.clock,
            turn_images=turn_images,
            camera_turn=camera_turn,
            quiet=quiet,
            game_holding=game_holding,
            guest_mode=guest_mode,
            guest_marker=guest_marker,
            alarm=alarm,
            hold_level=None if alarm is None else self.hold.held,
            diagnosis=diagnosis,
            listen_pause=listen_pause,
            lexicon=lexicon,
            nods=nods,
            **timers,
        )

    def utterance(self) -> Utterance:
        """Speech that started now on the rig's clock."""
        return Utterance(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE, self.clock.now)


def test_owner_calling_cyrene_gets_the_brain_reply_spoken_aloud():
    rig = Rig("Cyrene, nyalakan lampu kamar.")

    reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply == REPLY
    assert rig.brain.turns == [("nyalakan lampu kamar.", NOW)]
    assert rig.speech.texts == [REPLY]
    assert rig.player.played == [24000]


def test_speech_from_someone_else_is_ignored_before_transcription(caplog):
    rig = Rig("Cyrene, matikan lampu.", owner=(False, 0.12))

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply is None
    assert rig.stt.calls == 0
    assert rig.brain.turns == []
    assert rig.speech.texts == []
    assert "0.12" in caplog.text


def test_owner_speech_without_the_wake_word_is_ignored():
    rig = Rig("nyalakan lampu kamar")

    assert rig.assistant.handle_utterance(rig.utterance()) is None
    assert rig.brain.turns == []
    assert rig.speech.texts == []


def test_an_empty_transcript_is_ignored():
    rig = Rig("")

    assert rig.assistant.handle_utterance(rig.utterance()) is None
    assert rig.speech.texts == []


def test_follow_up_is_answered_without_the_wake_word_until_it_expires():
    rig = Rig("Cyrene, nyalakan lampu kamar", "terus nyalakan TV juga", "matikan TV")
    rig.assistant.handle_utterance(rig.utterance())

    rig.clock.now += 3
    follow_up = rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 30
    too_late = rig.assistant.handle_utterance(rig.utterance())

    assert follow_up == REPLY
    assert too_late is None
    assert [text for text, _ in rig.brain.turns] == [
        "nyalakan lampu kamar",
        "terus nyalakan TV juga",
    ]


def test_a_bare_wake_word_gets_an_acknowledgement_without_asking_the_brain():
    rig = Rig("Cyrene?", "nyalakan lampu kamar")

    acknowledgement = rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 2
    rig.assistant.handle_utterance(rig.utterance())

    assert acknowledgement == "はい、Master?"
    assert rig.speech.texts[0] == acknowledgement
    assert rig.brain.turns == [("nyalakan lampu kamar", NOW)]


def test_the_microphone_is_paused_while_she_speaks():
    rig = Rig("Cyrene, nyalakan lampu kamar")
    rig.assistant.set_mic(FakeMic(rig.events, []), FakeSegmenter(rig.events, []))

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.events == ["mic.pause", "synthesize", "play", "mic.resume", "segmenter.reset"]


def test_a_tts_failure_still_returns_the_reply_and_opens_follow_up():
    rig = Rig(
        "Cyrene, nyalakan lampu kamar",
        "terus nyalakan TV juga",
        speech_error=TtsError("Edge TTS gagal"),
    )
    rig.assistant.set_mic(FakeMic(rig.events, []))

    reply = rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 3
    follow_up = rig.assistant.handle_utterance(rig.utterance())

    assert reply == REPLY
    assert follow_up == REPLY
    assert rig.events[:3] == ["mic.pause", "synthesize", "mic.resume"]


def test_a_playback_failure_is_logged_instead_of_raised(caplog):
    rig = Rig("Cyrene, nyalakan lampu kamar", player_error=RuntimeError("PortAudio error"))
    rig.assistant.set_mic(FakeMic(rig.events, []))

    with caplog.at_level(logging.ERROR, logger="waifu.app"):
        reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply == REPLY
    assert rig.events[-1] == "mic.resume"
    assert "PortAudio error" in caplog.text


def test_an_unusable_voiceprint_rejects_speech_and_is_logged_once(caplog):
    rig = Rig(
        "Cyrene, nyalakan lampu",
        "Cyrene, nyalakan lampu",
        owner=SpeakerError("Suara Master belum terdaftar."),
    )

    with caplog.at_level(logging.ERROR, logger="waifu.app"):
        first = rig.assistant.handle_utterance(rig.utterance())
        second = rig.assistant.handle_utterance(rig.utterance())

    assert first is None
    assert second is None
    assert rig.stt.calls == 0
    assert len([r for r in caplog.records if "belum terdaftar" in r.getMessage()]) == 1


@pytest.mark.parametrize(
    "rig_options",
    [
        {"speaker_check": False},
        {"with_verifier": False},
        {"config": AppConfig(speaker=SpeakerConfig(enabled=False))},
    ],
    ids=["no-speaker-check", "no-verifier", "speaker-disabled-in-config"],
)
def test_speaker_verification_is_skipped_when_turned_off(rig_options):
    rig = Rig("Cyrene, nyalakan lampu kamar", owner=(False, 0.05), **rig_options)

    assert rig.assistant.handle_utterance(rig.utterance()) == REPLY
    assert rig.verifier.calls == 0


def test_handle_text_asks_the_brain_without_wake_word_or_voice():
    rig = Rig()

    reply = rig.assistant.handle_text("nyalakan lampu kamar")

    assert reply.text == REPLY
    assert rig.brain.turns == [("nyalakan lampu kamar", NOW)]
    assert rig.speech.texts == []


def test_run_answers_each_utterance_and_returns_cleanly_on_ctrl_c():
    rig = Rig("Cyrene, nyalakan lampu kamar")
    frame = np.zeros(480, dtype=np.float32)
    mic = FakeMic(rig.events, [frame, frame, frame])
    segmenter = FakeSegmenter(rig.events, [None, rig.utterance(), None])

    rig.assistant.run(mic, segmenter)

    assert rig.brain.turns == [("nyalakan lampu kamar", NOW)]
    assert rig.events == ["mic.pause", "synthesize", "play", "mic.resume", "segmenter.reset"]


def test_run_keeps_listening_after_one_utterance_fails(caplog):
    rig = Rig(RuntimeError("Whisper crashed"), "Cyrene, matikan TV")
    frame = np.zeros(480, dtype=np.float32)
    mic = FakeMic(rig.events, [frame, frame])
    segmenter = FakeSegmenter(rig.events, [rig.utterance(), rig.utterance()])

    with caplog.at_level(logging.ERROR, logger="waifu.app"):
        rig.assistant.run(mic, segmenter)

    assert rig.brain.turns == [("matikan TV", NOW)]
    assert "Whisper crashed" in caplog.text


@pytest.mark.parametrize(
    "failure",
    [
        ModuleNotFoundError("No module named 'faster_whisper'", name="faster_whisper"),
        SttError("Whisper gagal berjalan di GPU: CUDA tidak bisa dipakai."),
    ],
    ids=["voice-package-missing", "whisper-unusable"],
)
def test_run_stops_on_a_failure_that_every_utterance_would_hit(failure):
    rig = Rig(failure, "Cyrene, matikan TV")
    frame = np.zeros(480, dtype=np.float32)
    mic = FakeMic(rig.events, [frame, frame])
    segmenter = FakeSegmenter(rig.events, [rig.utterance(), rig.utterance()])

    with pytest.raises(type(failure)):
        rig.assistant.run(mic, segmenter)

    assert rig.stt.calls == 1
    assert rig.brain.turns == []


TAGGED_REPLY = "[senang] Siap, Master! Lampu kamar sudah menyala."


def test_the_emotion_tag_is_never_spoken_nor_returned():
    rig = Rig("Cyrene, nyalakan lampu kamar", reply=TAGGED_REPLY)

    reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply == REPLY
    assert rig.speech.texts == [REPLY]


def test_a_reply_that_is_only_a_tag_after_a_tool_call_is_still_answered_aloud():
    rig = Rig("Cyrene, nyalakan lampu kamar", reply="[senang]", with_avatar=True)

    reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply == "Sudah, Master."
    assert rig.speech.texts == [reply]
    assert rig.player.played == [24000]
    assert "avatar.speak happy" in rig.events


class FailedToolBrain(FakeBrain):
    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        call = ToolCallRecord("control_device", {"device": "ac"}, '{"ok": false}', True)
        return BrainReply(self.reply, [call])


class ToollessBrain(FakeBrain):
    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        return BrainReply(self.reply, [])


@pytest.mark.parametrize(
    ("brain", "expected"),
    [
        (FailedToolBrain, "Maaf Master, ada perintah yang gagal dijalankan."),
        (ToollessBrain, "Maaf Master, aku belum bisa menjawab itu."),
    ],
    ids=["tool-failed", "no-tool"],
)
def test_a_tag_only_reply_says_what_the_tools_did(brain, expected):
    rig = Rig(reply=" [ sedih ] ", brain=brain)

    reply = rig.assistant.handle_text("nyalakan AC")

    assert reply.text == expected


CALLED_SAYANG = AppConfig(assistant=AssistantConfig(owner_call="Sayang"))


def test_a_bare_wake_word_is_answered_with_what_she_calls_the_owner():
    rig = Rig("Cyrene?", config=CALLED_SAYANG)

    assert rig.assistant.handle_utterance(rig.utterance()) == "はい、Sayang?"


@pytest.mark.parametrize(
    ("brain", "expected"),
    [
        (FakeBrain, "Sudah, Sayang."),
        (FailedToolBrain, "Maaf Sayang, ada perintah yang gagal dijalankan."),
        (ToollessBrain, "Maaf Sayang, aku belum bisa menjawab itu."),
    ],
    ids=["done", "tool-failed", "no-tool"],
)
def test_a_tag_only_reply_calls_the_owner_what_she_calls_them(brain, expected):
    rig = Rig(reply="[senang]", brain=brain, config=CALLED_SAYANG)

    assert rig.assistant.handle_text("nyalakan AC").text == expected


def test_handle_text_returns_the_reply_without_its_tag_and_keeps_the_tool_calls():
    rig = Rig(reply="[ kaget ] えっ? Lampu kamar [senang] sudah menyala.")

    reply = rig.assistant.handle_text("nyalakan lampu kamar")

    assert reply.text == "えっ? Lampu kamar sudah menyala."
    assert [call.name for call in reply.tool_calls] == ["control_device"]


def test_the_avatar_thinks_then_speaks_with_the_reply_s_emotion_then_listens():
    rig = Rig("Cyrene, nyalakan lampu kamar", reply=TAGGED_REPLY, with_avatar=True)
    rig.assistant.set_mic(FakeMic(rig.events, []), FakeSegmenter(rig.events, []))

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.events == [
        "avatar.state thinking",
        "mic.pause",
        "synthesize",
        "avatar.state speaking",
        "avatar.speak happy",
        "play",
        "mic.resume",
        "segmenter.reset",
        "avatar.state listening 8",
    ]
    assert rig.avatar.spoken == [(2400, 24000, "happy")]


def test_the_avatar_mouths_exactly_the_audio_that_is_played():
    rig = Rig("Cyrene, nyalakan lampu kamar", with_avatar=True)

    rig.assistant.handle_utterance(rig.utterance())

    assert [(length, rate) for length, rate, _ in rig.avatar.spoken] == [(2400, 24000)]
    assert rig.player.played == [24000]


def test_a_reply_without_a_tag_is_shown_neutral():
    rig = Rig("Cyrene, nyalakan lampu kamar", with_avatar=True)

    rig.assistant.handle_utterance(rig.utterance())

    assert "avatar.speak neutral" in rig.events


def test_the_follow_up_window_shown_by_the_avatar_is_the_configured_one():
    config = AppConfig.model_validate({"assistant": {"followup_seconds": 5.5}})
    rig = Rig("Cyrene, nyalakan lampu kamar", config=config, with_avatar=True)

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.events[-1] == "avatar.state listening 5.5"


def test_without_a_follow_up_window_the_avatar_returns_to_idle_after_a_reply():
    config = AppConfig.model_validate({"assistant": {"followup_seconds": 0}})
    rig = Rig("Cyrene, nyalakan lampu kamar", "matikan lampu", config=config, with_avatar=True)

    rig.assistant.handle_utterance(rig.utterance())
    ignored = rig.assistant.handle_utterance(rig.utterance())

    assert ignored is None  # the wake word is needed again at once
    assert rig.events[-1] == "avatar.state idle"
    assert not any(event.startswith("avatar.state listening") for event in rig.events)


def test_a_bare_wake_word_is_acknowledged_happily_without_thinking():
    rig = Rig("Cyrene?", with_avatar=True)

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.events == [
        "synthesize",
        "avatar.state speaking",
        "avatar.speak happy",
        "play",
        "avatar.state listening 8",
    ]


def test_speech_that_is_ignored_leaves_the_avatar_alone():
    rig = Rig("nyalakan lampu kamar", with_avatar=True)

    assert rig.assistant.handle_utterance(rig.utterance()) is None
    assert rig.events == []


def test_when_the_reply_cannot_be_voiced_the_avatar_still_returns_to_listening():
    rig = Rig("Cyrene, nyalakan lampu kamar", with_avatar=True, speech_error=TtsError("offline"))

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.events == ["avatar.state thinking", "synthesize", "avatar.state listening 8"]


def test_a_broken_avatar_never_stops_the_reply(caplog):
    rig = Rig(
        "Cyrene, nyalakan lampu kamar",
        "terus nyalakan TV juga",
        reply=TAGGED_REPLY,
        avatar_error=RuntimeError("viewer queue broke"),
    )

    with caplog.at_level(logging.WARNING, logger="waifu.app"):
        reply = rig.assistant.handle_utterance(rig.utterance())
        rig.clock.now += 3
        follow_up = rig.assistant.handle_utterance(rig.utterance())

    assert reply == follow_up == REPLY
    assert rig.speech.texts == [REPLY, REPLY]
    assert rig.player.played == [24000, 24000]
    warnings = [record for record in caplog.records if "viewer queue broke" in record.getMessage()]
    assert len(warnings) == 1


def test_an_avatar_that_fails_again_after_recovering_is_reported_again(caplog):
    rig = Rig("Cyrene, lampu", "Cyrene, TV", "Cyrene, AC", with_avatar=True)

    with caplog.at_level(logging.WARNING, logger="waifu.app"):
        for error in (RuntimeError("first streak"), None, RuntimeError("second streak")):
            rig.avatar.error = error
            rig.assistant.handle_utterance(rig.utterance())

    messages = [record.getMessage() for record in caplog.records]
    assert sum("first streak" in message for message in messages) == 1
    assert sum("second streak" in message for message in messages) == 1


def test_speaking_without_follow_up_leaves_the_window_closed_and_the_avatar_idle():
    rig = Rig("matikan semua lampu", with_avatar=True)

    rig.assistant.speak("Cyrene siap, Master.", followup=False)
    ignored = rig.assistant.handle_utterance(rig.utterance())

    assert ignored is None
    assert rig.speech.texts == ["Cyrene siap, Master."]
    assert rig.events == [
        "synthesize",
        "avatar.state speaking",
        "avatar.speak neutral",
        "play",
        "avatar.state idle",
    ]


# Announcements: what she says by herself (the gacha buddy's reminders)


SILENT_FRAME = np.zeros(480, dtype=np.float32)


def test_an_announcement_is_said_between_utterances_without_opening_a_follow_up():
    rig = Rig(with_avatar=True)
    mic = FakeMic(rig.events, [SILENT_FRAME])
    rig.assistant.announce("Master, Original Resin di Genshin sudah penuh.", "surprised")

    rig.assistant.run(mic, FakeSegmenter(rig.events, [None]))

    assert rig.speech.texts == ["Master, Original Resin di Genshin sudah penuh."]
    assert rig.events == [
        "mic.pause",
        "synthesize",
        "avatar.state speaking",
        "avatar.speak surprised",
        "play",
        "mic.resume",
        "segmenter.reset",
        "avatar.state idle",
    ]
    assert rig.gate.check("matikan lampu").accepted is False  # no follow-up window


def test_an_announcement_waits_while_master_is_still_speaking():
    rig = Rig()
    rig.assistant.announce("Master, ekspedisi HSR sudah selesai.")
    mic = FakeMic(rig.events, [SILENT_FRAME, SILENT_FRAME])

    rig.assistant.run(mic, FakeSegmenter(rig.events, [None, None], busy=[True, True]))

    assert rig.speech.texts == []
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))
    assert rig.speech.texts == ["Master, ekspedisi HSR sudah selesai."]


def test_announcements_queued_together_are_said_in_one_go_with_the_first_emotion():
    rig = Rig(with_avatar=True)
    rig.assistant.announce("Satu.", "sad")
    rig.assistant.announce("Dua.", "happy")

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert rig.speech.texts == ["Satu. Dua."]
    assert "avatar.speak sad" in rig.events


def test_announce_can_be_called_from_another_thread():
    rig = Rig()
    thread = threading.Thread(target=rig.assistant.announce, args=("Halo dari thread lain.",))
    thread.start()
    thread.join()

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert rig.speech.texts == ["Halo dari thread lain."]


class GameBrain(FakeBrain):
    """Answers with one gacha_history call whose result names a 5-star."""

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        result = '{"banners":[{"recent_top_pulls":[{"name":"Yunli","pulls":29}]}]}'
        call = ToolCallRecord("gacha_history", {"game": "hsr"}, result, False)
        return BrainReply("[senang] Pity-mu dua puluh dua, Master.", [call])


def test_tool_results_stay_out_of_the_info_log(caplog):
    # logs\waifu.log is written at INFO; pull history must not end up in it.
    rig = Rig(brain=GameBrain)

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        rig.assistant.handle_text("pity-ku berapa?")

    assert "gacha_history" in caplog.text
    assert "Yunli" not in caplog.text


def test_tool_results_are_logged_for_debugging(caplog):
    rig = Rig(brain=GameBrain)

    with caplog.at_level(logging.DEBUG, logger="waifu.app"):
        rig.assistant.handle_text("pity-ku berapa?")

    assert "Yunli" in caplog.text


class ExpenseBrain(FakeBrain):
    """Answers with one record_expense call (v1.24) whose amount and note are sentinels."""

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        arguments = {"amount": 987654, "description": "SECRETNOTE", "wallet": "SECRETWALLET"}
        result = '{"recorded":{"id":812,"amount":987654,"note":"SECRETNOTE"},"deduped":false}'
        call = ToolCallRecord("record_expense", arguments, result, False)
        return BrainReply("[senang] Sudah kucatat, Master.", [call])


def test_a_finance_tool_call_reaches_the_info_log_without_its_arguments(caplog):
    # logs\waifu.log is written at INFO: Master's amounts and notes stay out of it.
    rig = Rig(brain=ExpenseBrain)

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        rig.assistant.handle_text("yang tadi jadi")

    assert "Tool record_expense(…) -> ok" in [record.getMessage() for record in caplog.records]
    for sentinel in ("987654", "SECRETNOTE", "SECRETWALLET"):
        assert sentinel not in caplog.text


def test_a_finance_tool_result_is_logged_only_for_debugging(caplog):
    rig = Rig(brain=ExpenseBrain)

    with caplog.at_level(logging.DEBUG, logger="waifu.app"):
        rig.assistant.handle_text("yang tadi jadi")

    debug = [record for record in caplog.records if "SECRETNOTE" in record.getMessage()]
    assert [record.levelno for record in debug] == [logging.DEBUG]


# The avatar's interactions: busy, spoken reactions, and listening on a double-click


WAIT_S = 2.0  # an upper bound for events between threads, never a sleep
PAT_LINE = "Hehe, makasih elusannya, Master."
ANNOUNCEMENT = "Master, ekspedisi HSR sudah selesai."


class WatchingBrain(FakeBrain):
    """Notes what `watch` says while the brain thinks."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.watch: Callable[[], bool] = lambda: False
        self.seen: list[bool] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.seen.append(self.watch())
        return super().respond(user_text, now=now)


def test_she_is_not_busy_while_idle():
    rig = Rig()

    assert rig.assistant.busy is False


def test_she_is_busy_from_hearing_an_utterance_until_her_reply_ends():
    rig = Rig("Cyrene, nyalakan lampu kamar", brain=WatchingBrain)
    rig.brain.watch = lambda: rig.assistant.busy
    while_playing: list[bool] = []
    rig.player.on_play = lambda: while_playing.append(rig.assistant.busy)

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.seen == [True]
    assert while_playing == [True]
    assert rig.assistant.busy is False


def test_she_is_busy_while_announcing():
    rig = Rig()
    while_playing: list[bool] = []
    rig.player.on_play = lambda: while_playing.append(rig.assistant.busy)
    rig.assistant.announce("Master, ekspedisi HSR sudah selesai.")

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert while_playing == [True]
    assert rig.assistant.busy is False


def test_she_counts_as_busy_while_master_is_speaking():
    rig = Rig()
    segmenter = FakeSegmenter(rig.events, [])
    rig.assistant.set_mic(FakeMic(rig.events, []), segmenter)

    segmenter.busy = True
    talking = rig.assistant.busy
    segmenter.busy = False

    assert talking is True
    assert rig.assistant.busy is False


def test_she_is_no_longer_busy_after_an_utterance_fails():
    rig = Rig(RuntimeError("Whisper crashed"))

    with pytest.raises(RuntimeError):
        rig.assistant.handle_utterance(rig.utterance())

    assert rig.assistant.busy is False


def test_a_reaction_is_spoken_like_a_reply_and_opens_the_follow_up_window():
    rig = Rig("matikan lampu kamar", with_avatar=True)
    rig.assistant.set_mic(FakeMic(rig.events, []), FakeSegmenter(rig.events, []))
    while_playing: list[bool] = []
    rig.player.on_play = lambda: while_playing.append(rig.assistant.busy)

    rig.assistant.speak_reaction(PAT_LINE, "happy")
    events = list(rig.events)
    rig.clock.now += 3
    follow_up = rig.assistant.handle_utterance(rig.utterance())

    assert rig.speech.texts[0] == PAT_LINE
    assert events == [
        "mic.pause",
        "synthesize",
        "avatar.state speaking",
        "avatar.speak happy",
        "play",
        "mic.resume",
        "segmenter.reset",
        "avatar.state listening 8",
    ]
    assert while_playing[0] is True
    assert follow_up == REPLY  # no wake word needed inside the window


def test_listen_now_opens_the_follow_up_window_and_shows_her_listening():
    rig = Rig("matikan lampu kamar", with_avatar=True)

    rig.assistant.listen_now()
    events = list(rig.events)
    rig.clock.now += 3
    follow_up = rig.assistant.handle_utterance(rig.utterance())

    assert events == ["avatar.state listening 8"]
    assert follow_up == REPLY
    assert rig.speech.texts == [REPLY]  # listen_now itself says nothing


def test_after_listen_now_a_stranger_is_still_ignored_before_transcription():
    # Ctrl+Alt+C and a double-click open the window without her name; never without Master's voice.
    rig = Rig("nyalakan lampu kamar", owner=(False, 0.05), with_avatar=True)

    rig.assistant.listen_now()
    rig.clock.now += 3
    reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply is None
    assert rig.stt.calls == 0
    assert rig.brain.turns == []
    assert rig.speech.texts == []
    assert rig.events == ["avatar.state listening 8"]


def test_listen_now_without_a_follow_up_window_leaves_her_idle():
    config = AppConfig.model_validate({"assistant": {"followup_seconds": 0}})
    rig = Rig(config=config, with_avatar=True)

    rig.assistant.listen_now()

    assert rig.events == ["avatar.state idle"]


def hold_first_play(rig: Rig) -> tuple[threading.Event, threading.Event]:
    """Make the first `play` block until released; returns (playing, release)."""
    playing, release = threading.Event(), threading.Event()

    def hold() -> None:
        if not playing.is_set():
            playing.set()
            release.wait(WAIT_S)

    rig.player.on_play = hold
    return playing, release


def test_a_reaction_waits_until_the_reply_has_been_spoken():
    rig = Rig("Cyrene, nyalakan lampu kamar", with_avatar=True)
    playing, release = hold_first_play(rig)
    reply = threading.Thread(target=rig.assistant.handle_utterance, args=(rig.utterance(),))
    reply.start()
    assert playing.wait(WAIT_S)

    reaction = threading.Thread(target=rig.assistant.speak_reaction, args=(PAT_LINE, "happy"))
    reaction.start()
    reaction.join(0.1)
    waiting, busy, said = reaction.is_alive(), rig.assistant.busy, list(rig.speech.texts)
    release.set()
    reply.join(WAIT_S)
    reaction.join(WAIT_S)

    assert waiting
    assert busy
    assert said == [REPLY]
    assert rig.speech.texts == [REPLY, PAT_LINE]
    assert rig.events == [
        "avatar.state thinking",
        "synthesize",
        "avatar.state speaking",
        "avatar.speak neutral",
        "play",
        "avatar.state listening 8",
        "synthesize",
        "avatar.state speaking",
        "avatar.speak happy",
        "play",
        "avatar.state listening 8",
    ]


def test_listen_now_waits_until_what_she_is_saying_has_been_said():
    # The interaction handler ignores a double-click while she is busy, but an announcement
    # can start between that check and listen_now: the window opens after it, never during it.
    rig = Rig(with_avatar=True)
    playing, release = hold_first_play(rig)
    announcement = threading.Thread(
        target=rig.assistant.speak, args=(ANNOUNCEMENT,), kwargs={"followup": False}
    )
    announcement.start()
    assert playing.wait(WAIT_S)

    listening = threading.Thread(target=rig.assistant.listen_now)
    listening.start()
    listening.join(0.1)
    waiting, shown = listening.is_alive(), list(rig.events)
    release.set()
    announcement.join(WAIT_S)
    listening.join(WAIT_S)

    assert waiting
    assert "avatar.state listening 8" not in shown
    assert rig.events[-2:] == ["avatar.state idle", "avatar.state listening 8"]


def test_a_reply_waits_until_the_reaction_has_been_spoken():
    rig = Rig("Cyrene, nyalakan lampu kamar")
    playing, release = hold_first_play(rig)
    reaction = threading.Thread(target=rig.assistant.speak_reaction, args=(PAT_LINE, "happy"))
    reaction.start()
    assert playing.wait(WAIT_S)

    reply = threading.Thread(target=rig.assistant.handle_utterance, args=(rig.utterance(),))
    reply.start()
    reply.join(0.1)
    waiting, said = reply.is_alive(), list(rig.speech.texts)
    release.set()
    reaction.join(WAIT_S)
    reply.join(WAIT_S)

    assert waiting
    assert said == [PAT_LINE]
    assert rig.speech.texts == [PAT_LINE, REPLY]
    assert rig.events == ["synthesize", "play", "synthesize", "play"]


# Cutting her off: a double-click on the avatar stops what she is saying


def play_until_cut(rig: Rig) -> threading.Event:
    """Make `play` last until the player is stopped; returns the event set once it plays."""
    playing = threading.Event()

    def hold() -> None:
        playing.set()
        rig.player.cut.wait(WAIT_S)

    rig.player.on_play = hold
    return playing


def test_interrupt_cuts_her_reply_short_and_she_still_listens_afterwards(caplog):
    rig = Rig("Cyrene, nyalakan lampu kamar", with_avatar=True)
    rig.assistant.set_mic(FakeMic(rig.events, []), FakeSegmenter(rig.events, []))
    playing = play_until_cut(rig)
    reply = threading.Thread(target=rig.assistant.handle_utterance, args=(rig.utterance(),))

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        reply.start()
        assert playing.wait(WAIT_S)
        interrupted = rig.assistant.interrupt()  # from the avatar server's thread
        reply.join(WAIT_S)

    assert interrupted is True
    assert not reply.is_alive()
    assert rig.events == [
        "avatar.state thinking",
        "mic.pause",
        "synthesize",
        "avatar.state speaking",
        "avatar.speak neutral",
        "play",
        "mic.resume",
        "segmenter.reset",
        "avatar.state listening 8",
    ]
    assert "cut off" in caplog.text
    assert rig.gate.check("matikan TV").accepted is True  # no wake word needed now
    assert (rig.assistant.speaking, rig.assistant.busy) == (False, False)


def test_an_interrupted_announcement_leaves_her_idle_without_a_follow_up_window():
    rig = Rig(with_avatar=True)
    playing = play_until_cut(rig)
    announcement = threading.Thread(
        target=rig.assistant.speak, args=(ANNOUNCEMENT,), kwargs={"followup": False}
    )

    announcement.start()
    assert playing.wait(WAIT_S)
    interrupted = rig.assistant.interrupt()
    announcement.join(WAIT_S)

    assert interrupted is True
    assert not announcement.is_alive()
    assert rig.events[-2:] == ["play", "avatar.state idle"]
    assert rig.gate.check("matikan lampu").accepted is False


def announce_and_cut_the_first_clip(rig: Rig) -> list[bool]:
    """Queue an announcement and cut the first clip while it plays; returns what the cut said."""
    cuts: list[bool] = []

    def announce_and_cut() -> None:
        if not cuts:
            rig.assistant.announce(ANNOUNCEMENT)
            cuts.append(rig.assistant.interrupt())

    rig.player.on_play = announce_and_cut
    return cuts


def test_after_a_cut_an_announcement_waits_until_master_has_been_heard():
    # Master cut her off to talk: an announcement queued during the reply must not pause the
    # microphone again (dropping Master's words) before the follow-up has been heard.
    rig = Rig("Cyrene, nyalakan lampu kamar", "matikan TV", with_avatar=True)
    cuts = announce_and_cut_the_first_clip(rig)
    mic = FakeMic(rig.events, [SILENT_FRAME] * 4)
    segmenter = FakeSegmenter(rig.events, [rig.utterance(), None, rig.utterance(), None])

    rig.assistant.run(mic, segmenter)

    assert cuts == [True]
    after_cut = rig.events[rig.events.index("mic.resume") + 1 :]
    assert after_cut[:3] == ["segmenter.reset", "avatar.state listening 8", "avatar.state thinking"]
    assert [text for text, _ in rig.brain.turns] == ["nyalakan lampu kamar", "matikan TV"]
    assert rig.speech.texts == [REPLY, REPLY, ANNOUNCEMENT]  # held, not lost


def test_an_announcement_held_after_a_cut_is_said_once_the_follow_up_window_closes():
    rig = Rig("Cyrene, nyalakan lampu kamar")
    announce_and_cut_the_first_clip(rig)
    rig.assistant.run(
        FakeMic(rig.events, [SILENT_FRAME] * 2), FakeSegmenter(rig.events, [rig.utterance(), None])
    )
    held = list(rig.speech.texts)

    rig.clock.now += 8  # followup_seconds: Master said nothing
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert held == [REPLY]
    assert rig.speech.texts == [REPLY, ANNOUNCEMENT]


QUESTION = "Komputer mau kumatikan sekarang, Master? Jawab iya kalau jadi."
BUDGET_NOTICE = (
    "Master, biaya API bulan ini sekitar 0,80 dolar, sudah mencapai 80 persen dari anggaran "
    "1 dolar."
)


class AnnouncingBrain(FakeBrain):
    """Runs `announce` while it thinks up its first reply (a reply whose cost crossed the
    budget queues the budget notice)."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.announce: Callable[[], None] = lambda: None

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        if not self.turns:
            self.announce()
        return super().respond(user_text, now=now)


def take_time_to_say(rig: Rig, seconds: dict[str, float]) -> None:
    """Each clip plays for its text's `seconds` on the rig's clock."""

    def play() -> None:
        rig.clock.now += seconds[rig.speech.texts[-1]]

    rig.player.on_play = play


def test_an_announcement_that_waits_for_the_follow_up_window_leaves_it_to_master():
    # Said right after the reply that queued it, a notice of about 8 s would use up the window
    # in which Master answers her question without the wake word.
    rig = Rig("Cyrene, matikan komputer", "iya", reply=QUESTION, brain=AnnouncingBrain)
    rig.brain.announce = lambda: rig.assistant.announce(
        BUDGET_NOTICE, "surprised", wait_for_followup=True
    )
    take_time_to_say(rig, {QUESTION: 3, BUDGET_NOTICE: 8})

    rig.assistant.run(
        FakeMic(rig.events, [SILENT_FRAME] * 2), FakeSegmenter(rig.events, [rig.utterance(), None])
    )
    rig.clock.now += 1  # Master answers a second after she stopped talking
    answer = rig.assistant.handle_utterance(rig.utterance())
    waited = list(rig.speech.texts)
    rig.clock.now += 8  # followup_seconds: the window after her second reply has closed
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert answer == QUESTION
    assert [text for text, _ in rig.brain.turns] == ["matikan komputer", "iya"]
    assert waited == [QUESTION, QUESTION]
    assert rig.speech.texts == [QUESTION, QUESTION, BUDGET_NOTICE]


def test_an_announcement_that_waits_for_the_follow_up_window_is_said_at_once_without_one():
    rig = Rig(with_avatar=True)
    rig.assistant.announce(BUDGET_NOTICE, "surprised", wait_for_followup=True)

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert rig.speech.texts == [BUDGET_NOTICE]
    assert "avatar.speak surprised" in rig.events
    assert rig.gate.check("matikan lampu").accepted is False  # no follow-up window


def test_other_announcements_are_still_said_while_the_follow_up_window_is_open():
    rig = Rig("Cyrene, nyalakan lampu kamar")
    rig.assistant.handle_utterance(rig.utterance())
    rig.assistant.announce(BUDGET_NOTICE, "surprised", wait_for_followup=True)
    rig.assistant.announce(ANNOUNCEMENT)

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))
    inside_the_window = list(rig.speech.texts)
    rig.clock.now += 8  # followup_seconds
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert inside_the_window == [REPLY, ANNOUNCEMENT]
    assert rig.speech.texts == [REPLY, ANNOUNCEMENT, BUDGET_NOTICE]


def test_interrupt_while_she_is_silent_stops_nothing():
    rig = Rig("Cyrene, nyalakan lampu kamar")

    before = rig.assistant.interrupt()
    rig.assistant.handle_utterance(rig.utterance())
    after = rig.assistant.interrupt()

    assert before is False
    assert after is False
    assert rig.player.played == [24000]


def test_interrupt_while_she_thinks_leaves_the_reply_to_be_spoken_in_full(caplog):
    rig = Rig("Cyrene, nyalakan lampu kamar", brain=WatchingBrain)
    rig.brain.watch = rig.assistant.interrupt

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.seen == [False]
    assert rig.player.played == [24000]
    assert "cut off" not in caplog.text


def test_she_is_speaking_only_while_a_clip_plays():
    rig = Rig("Cyrene, nyalakan lampu kamar", brain=WatchingBrain)
    rig.brain.watch = lambda: rig.assistant.speaking
    while_playing: list[tuple[bool, bool]] = []
    rig.player.on_play = lambda: while_playing.append((rig.assistant.speaking, rig.assistant.busy))
    before = rig.assistant.speaking

    rig.assistant.handle_utterance(rig.utterance())

    assert before is False
    assert rig.brain.seen == [False]  # thinking keeps her busy, but she is not speaking yet
    assert while_playing == [(True, True)]
    assert (rig.assistant.speaking, rig.assistant.busy) == (False, False)


def test_she_is_no_longer_speaking_after_playback_fails():
    rig = Rig(player_error=RuntimeError("PortAudio error"))

    rig.assistant.speak(ANNOUNCEMENT, followup=False)

    assert rig.assistant.speaking is False


# Speaking up by herself with the brain's words (the morning greeting)


GREETING_PROMPT = "Ini sapaan pagimu sendiri: sapa Master dan rangkum harinya."


class PassedOn:
    """An `on_reply` (a push to the phone) that keeps what she passed on, or fails with `error`."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.texts: list[str] = []
        self._threads: list[threading.Thread] = []
        self._called = threading.Event()

    def __call__(self, text: str) -> None:
        self.texts.append(text)
        self._threads.append(threading.current_thread())
        self._called.set()
        if self.error is not None:
            raise self.error

    def wait(self) -> list[str]:
        """What she passed on, once that call has ended on whatever thread made it."""
        if self._called.wait(WAIT_S) and self._threads[0] is not threading.current_thread():
            self._threads[0].join(WAIT_S)
        return self.texts


class SignallingMic(FakeMic):
    """A FakeMic that sets `asked_again` when the voice loop asks for a frame after the first."""

    def __init__(self, events: list[str], frames: list[np.ndarray]) -> None:
        super().__init__(events, frames)
        self.asked_again = threading.Event()

    def frames(self) -> Iterator[np.ndarray]:
        for index, frame in enumerate(self._frames):
            if index:
                self.asked_again.set()
            yield frame
        raise KeyboardInterrupt


def test_a_request_is_answered_aloud_between_utterances_like_a_reply():
    rig = Rig("matikan TV", with_avatar=True)
    replies = PassedOn()
    rig.assistant.ask_aloud(GREETING_PROMPT, on_reply=replies)

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))
    events = list(rig.events)
    rig.clock.now += 3
    follow_up = rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.turns[0] == (GREETING_PROMPT, NOW)
    assert rig.speech.texts[0] == REPLY
    assert replies.wait() == [REPLY]
    assert events == [
        "avatar.state thinking",
        "mic.pause",
        "synthesize",
        "avatar.state speaking",
        "avatar.speak neutral",
        "play",
        "mic.resume",
        "segmenter.reset",
        "avatar.state listening 8",
    ]
    assert follow_up == REPLY  # Master answers without the wake word


def test_the_reply_to_a_request_is_spoken_and_passed_on_without_its_tag():
    rig = Rig(reply="[senang] Selamat pagi, Master! Hari ini cerah.", with_avatar=True)
    replies = PassedOn()
    rig.assistant.ask_aloud(GREETING_PROMPT, on_reply=replies)

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert rig.speech.texts == ["Selamat pagi, Master! Hari ini cerah."]
    assert replies.wait() == ["Selamat pagi, Master! Hari ini cerah."]
    assert "avatar.speak happy" in rig.events


def test_she_is_back_at_the_microphone_while_what_she_said_is_still_being_passed_on():
    # A push to the phone may take ntfy's whole timeout, while the follow-up window is open:
    # the voice loop must not wait for it, or Master's answer is heard too late or lost.
    rig = Rig()
    mic = SignallingMic(rig.events, [SILENT_FRAME] * 2)
    back_at_the_mic: list[bool] = []
    replies = PassedOn()

    def slow_push(text: str) -> None:
        back_at_the_mic.append(mic.asked_again.wait(WAIT_S))
        replies(text)

    rig.assistant.ask_aloud(GREETING_PROMPT, on_reply=slow_push)

    rig.assistant.run(mic, FakeSegmenter(rig.events, [None, None]))

    assert replies.wait() == [REPLY]
    assert back_at_the_mic == [True]


def test_a_request_waits_while_master_is_still_speaking():
    rig = Rig()
    rig.assistant.ask_aloud(GREETING_PROMPT)

    rig.assistant.run(
        FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None], busy=[True])
    )
    waited = list(rig.brain.turns)
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert waited == []
    assert rig.brain.turns == [(GREETING_PROMPT, NOW)]
    assert rig.speech.texts == [REPLY]


class ForgetfulBrain(FakeBrain):
    """A FakeBrain that also notes each reset among its turns."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.calls: list[str] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.calls.append(user_text)
        return super().respond(user_text, now=now)

    def reset(self) -> None:
        self.calls.append("<reset>")
        super().reset()


@pytest.mark.parametrize(
    ("new_session", "calls"),
    [
        (True, ["saldo GoPay berapa?", "<reset>", GREETING_PROMPT]),
        (False, ["saldo GoPay berapa?", GREETING_PROMPT]),
    ],
    ids=["new_session", "same_session"],
)
def test_a_request_may_start_a_new_session_so_its_reply_sees_nothing_said_before(
    new_session, calls
):
    # The morning greeting must not repeat figures from a finance answer moments earlier.
    rig = Rig(brain=ForgetfulBrain)
    rig.assistant.handle_text("saldo GoPay berapa?")
    rig.assistant.ask_aloud(GREETING_PROMPT, new_session=new_session)

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert rig.brain.calls == calls
    assert rig.speech.texts[-1] == REPLY


def test_a_second_request_waits_until_master_has_had_the_chance_to_answer_the_first():
    rig = Rig()
    rig.assistant.ask_aloud(GREETING_PROMPT)
    rig.assistant.ask_aloud("Ingatkan Master minum air.")

    rig.assistant.run(
        FakeMic(rig.events, [SILENT_FRAME] * 2), FakeSegmenter(rig.events, [None, None])
    )
    first = [text for text, _ in rig.brain.turns]
    rig.clock.now += 8  # followup_seconds: Master did not answer
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert first == [GREETING_PROMPT]
    assert [text for text, _ in rig.brain.turns] == [GREETING_PROMPT, "Ingatkan Master minum air."]


def test_a_request_waits_while_master_may_still_answer_her_reply():
    # Master is talking with her ("Komputer mau kumatikan?"): a greeting queued meanwhile
    # must not take over the follow-up window he answers in.
    rig = Rig("Cyrene, nyalakan lampu kamar")
    rig.assistant.ask_aloud(GREETING_PROMPT)

    rig.assistant.run(
        FakeMic(rig.events, [SILENT_FRAME] * 2), FakeSegmenter(rig.events, [rig.utterance(), None])
    )
    waited = [text for text, _ in rig.brain.turns]
    rig.clock.now += 8  # followup_seconds: Master said nothing more
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert waited == ["nyalakan lampu kamar"]
    assert [text for text, _ in rig.brain.turns] == ["nyalakan lampu kamar", GREETING_PROMPT]


def test_master_s_answer_inside_the_window_comes_before_a_waiting_request():
    rig = Rig("Cyrene, matikan komputer", "iya")
    rig.assistant.ask_aloud(GREETING_PROMPT)
    segmenter = FakeSegmenter(rig.events, [rig.utterance(), None, rig.utterance(), None])

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME] * 4), segmenter)
    answered = [text for text, _ in rig.brain.turns]
    rig.clock.now += 8  # the window after her second reply has closed
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert answered == ["matikan komputer", "iya"]
    assert [text for text, _ in rig.brain.turns] == ["matikan komputer", "iya", GREETING_PROMPT]


def test_a_request_waits_while_she_listens_after_a_double_click():
    rig = Rig()
    rig.assistant.ask_aloud(GREETING_PROMPT)

    rig.assistant.listen_now()
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))
    waited = list(rig.brain.turns)
    rig.clock.now += 8
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert waited == []
    assert rig.brain.turns == [(GREETING_PROMPT, NOW)]


def test_a_request_queued_during_a_cut_waits_until_the_follow_up_window_closes():
    rig = Rig("Cyrene, nyalakan lampu kamar")
    cuts: list[bool] = []

    def ask_and_cut() -> None:
        if not cuts:
            rig.assistant.ask_aloud(GREETING_PROMPT)
            cuts.append(rig.assistant.interrupt())

    rig.player.on_play = ask_and_cut
    rig.assistant.run(
        FakeMic(rig.events, [SILENT_FRAME] * 2), FakeSegmenter(rig.events, [rig.utterance(), None])
    )
    held = [text for text, _ in rig.brain.turns]

    rig.clock.now += 8  # followup_seconds: Master said nothing
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert cuts == [True]
    assert held == ["nyalakan lampu kamar"]
    assert [text for text, _ in rig.brain.turns] == ["nyalakan lampu kamar", GREETING_PROMPT]


def test_queued_announcements_are_said_before_a_queued_request():
    rig = Rig()
    rig.assistant.ask_aloud(GREETING_PROMPT)
    rig.assistant.announce(ANNOUNCEMENT)

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))
    announced = list(rig.speech.texts)
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert announced == [ANNOUNCEMENT]  # the request waits for the next frame
    assert rig.speech.texts == [ANNOUNCEMENT, REPLY]


def cut_the_first_clip(rig: Rig) -> list[bool]:
    """Cut the first clip while it plays (a double-click); returns what the cut said."""
    cuts: list[bool] = []

    def cut() -> None:
        if not cuts:
            cuts.append(rig.assistant.interrupt())

    rig.player.on_play = cut
    return cuts


def test_a_request_queued_with_an_announcement_that_is_cut_waits_for_master():
    # The reminder and the greeting were queued together; Master cut the reminder to talk,
    # so the greeting must not pause the microphone again before he has.
    rig = Rig()
    cuts = cut_the_first_clip(rig)
    rig.assistant.announce(ANNOUNCEMENT)
    rig.assistant.ask_aloud(GREETING_PROMPT)

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))
    held = (list(rig.brain.turns), list(rig.speech.texts))
    rig.clock.now += 8  # followup_seconds: Master said nothing
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert cuts == [True]
    assert held == ([], [ANNOUNCEMENT])
    assert rig.brain.turns == [(GREETING_PROMPT, NOW)]
    assert rig.speech.texts == [ANNOUNCEMENT, REPLY]


def test_she_is_busy_while_thinking_up_and_saying_the_answer_to_a_request():
    rig = Rig(brain=WatchingBrain)
    rig.brain.watch = lambda: rig.assistant.busy
    while_playing: list[bool] = []
    rig.player.on_play = lambda: while_playing.append(rig.assistant.busy)
    rig.assistant.ask_aloud(GREETING_PROMPT)

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert rig.brain.seen == [True]
    assert while_playing == [True]
    assert rig.assistant.busy is False


def test_ask_aloud_can_be_called_from_another_thread():
    rig = Rig()
    thread = threading.Thread(target=rig.assistant.ask_aloud, args=(GREETING_PROMPT,))
    thread.start()
    thread.join()

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert rig.speech.texts == [REPLY]


def test_a_failing_on_reply_is_logged_and_she_keeps_listening(caplog):
    rig = Rig()
    push = PassedOn(error=RuntimeError("ntfy is down"))
    rig.assistant.ask_aloud(GREETING_PROMPT, on_reply=push)
    rig.assistant.announce(ANNOUNCEMENT)

    with caplog.at_level(logging.ERROR, logger="waifu.app"):
        rig.assistant.run(
            FakeMic(rig.events, [SILENT_FRAME] * 2), FakeSegmenter(rig.events, [None, None])
        )
        push.wait()

    assert "ntfy is down" in caplog.text
    assert rig.speech.texts == [ANNOUNCEMENT, REPLY]
    assert rig.assistant.busy is False


BRIEFING_TOOL = "daily_briefing"
NO_CONNECTION = "Maaf Master, aku tidak bisa terhubung ke server Claude."


class GreetingBrain(FakeBrain):
    """Greets after one successful daily_briefing call, as a working brain does."""

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.turns.append((user_text, now))
        call = ToolCallRecord(BRIEFING_TOOL, {}, '{"date": "Senin, 12-10-2026"}', False)
        return BrainReply(self.reply, [call])


class ApologyBrain(FakeBrain):
    """Fails as the real brains do: a reply without any tool call, never an exception."""

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.turns.append((user_text, now))
        return BrainReply(self.reply)


class FailedBriefingBrain(FakeBrain):
    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.turns.append((user_text, now))
        call = ToolCallRecord(BRIEFING_TOOL, {"x": 1}, '{"ok": false}', True)
        return BrainReply(self.reply, [call])


def test_a_request_needing_a_tool_is_said_and_passed_on_when_its_turn_called_it():
    rig = Rig(brain=GreetingBrain)
    replies = PassedOn()
    rig.assistant.ask_aloud(GREETING_PROMPT, on_reply=replies, required_tool=BRIEFING_TOOL)

    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert rig.speech.texts == [REPLY]
    assert replies.wait() == [REPLY]


@pytest.mark.parametrize(
    ("brain", "reply"),
    [
        (ApologyBrain, NO_CONNECTION),
        (ApologyBrain, "[senang]"),
        (FakeBrain, REPLY),
        (FailedBriefingBrain, REPLY),
    ],
    ids=["apology", "empty reply", "another tool", "the tool failed"],
)
def test_a_request_whose_turn_did_not_call_its_tool_is_neither_said_nor_passed_on(
    brain, reply, caplog
):
    # Nobody asked: an apology for a failed greeting must not be said into the room, pushed to
    # the phone, or open the follow-up window.
    rig = Rig(brain=brain, reply=reply, with_avatar=True)
    replies: list[str] = []
    rig.assistant.ask_aloud(GREETING_PROMPT, on_reply=replies.append, required_tool=BRIEFING_TOOL)

    with caplog.at_level(logging.WARNING, logger="waifu.app"):
        rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))
    shown = list(rig.events)
    rig.assistant.announce(ANNOUNCEMENT)
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert [text for text, _ in rig.brain.turns] == [GREETING_PROMPT]
    assert "no successful daily_briefing call" in caplog.text
    assert shown == ["avatar.state thinking", "avatar.state idle"]
    assert rig.speech.texts == [ANNOUNCEMENT]
    assert replies == []
    assert rig.gate.check("matikan lampu").accepted is False
    assert rig.assistant.busy is False


class BrokenBrain(FakeBrain):
    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        raise RuntimeError("brain crashed")


# Quiet during a focus block: what she says by herself waits; Master's own reminders do not

BLOCK_DONE = "Sayang, dua puluh lima menit selesai. Istirahat lima menit dulu ya."
MEDICINE = "Master, waktunya minum obat."


class Quiet:
    """Stands in for the focus timer's `in_block`: quiet while `on`."""

    def __init__(self, on: bool = True) -> None:
        self.on = on

    def __call__(self) -> bool:
        return self.on


def listen_once(rig: Rig, *outputs: Utterance | None) -> None:
    """Run the voice loop over one frame per output (a silent frame for None)."""
    frames = [SILENT_FRAME] * max(1, len(outputs))
    rig.assistant.run(
        FakeMic(rig.events, frames), FakeSegmenter(rig.events, list(outputs) or [None])
    )


def test_while_quiet_her_announcements_wait_and_come_out_with_the_block_s_end_line():
    quiet = Quiet()
    rig = Rig(quiet=quiet, with_avatar=True)
    rig.assistant.announce(ANNOUNCEMENT)
    rig.assistant.announce(BUDGET_NOTICE, "surprised", wait_for_followup=True)

    listen_once(rig)
    held = list(rig.speech.texts)
    rig.assistant.remind(BLOCK_DONE, "happy")  # the timer queues its line, then the break starts
    quiet.on = False
    listen_once(rig)

    assert held == []
    assert rig.speech.texts == [f"{BLOCK_DONE} {ANNOUNCEMENT} {BUDGET_NOTICE}"]
    assert "avatar.speak happy" in rig.events
    assert rig.gate.check("matikan lampu").accepted is False  # no follow-up window


def test_a_reminder_master_set_is_said_while_quiet():
    rig = Rig(quiet=Quiet())
    rig.assistant.announce(ANNOUNCEMENT)
    rig.assistant.remind(MEDICINE, "happy")

    listen_once(rig)

    assert rig.speech.texts == [MEDICINE]


def test_a_reminder_is_said_like_an_announcement_without_quiet():
    rig = Rig(with_avatar=True)
    rig.assistant.remind(MEDICINE, "happy")
    rig.assistant.announce(ANNOUNCEMENT)

    listen_once(rig)

    assert rig.speech.texts == [f"{MEDICINE} {ANNOUNCEMENT}"]
    assert "avatar.speak happy" in rig.events
    assert rig.gate.check("matikan lampu").accepted is False  # no follow-up window


def test_a_reminder_is_said_while_the_follow_up_window_is_open():
    rig = Rig("Cyrene, nyalakan lampu kamar", quiet=Quiet())
    rig.assistant.handle_utterance(rig.utterance())
    rig.assistant.remind(MEDICINE, "happy")

    listen_once(rig)

    assert rig.speech.texts == [REPLY, MEDICINE]


def test_a_reminder_waits_while_master_is_still_speaking():
    rig = Rig(quiet=Quiet())
    rig.assistant.remind(MEDICINE, "happy")

    rig.assistant.run(
        FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None], busy=[True])
    )
    waited = list(rig.speech.texts)
    listen_once(rig)

    assert waited == []
    assert rig.speech.texts == [MEDICINE]


def test_while_quiet_master_is_answered_as_always():
    rig = Rig("Cyrene, nyalakan lampu kamar", quiet=Quiet())

    listen_once(rig, rig.utterance())

    assert rig.brain.turns == [("nyalakan lampu kamar", NOW)]
    assert rig.speech.texts == [REPLY]


def test_while_quiet_a_request_waits_and_is_answered_after_it():
    quiet = Quiet()
    rig = Rig(quiet=quiet)
    rig.assistant.ask_aloud(GREETING_PROMPT)

    listen_once(rig, None, None)
    waited = [text for text, _ in rig.brain.turns]
    quiet.on = False
    listen_once(rig)

    assert waited == []
    assert [text for text, _ in rig.brain.turns] == [GREETING_PROMPT]
    assert rig.speech.texts == [REPLY]


def test_after_quiet_her_patient_announcements_still_wait_for_the_follow_up_window():
    quiet = Quiet()
    rig = Rig("Cyrene, nyalakan lampu kamar", quiet=quiet)
    rig.assistant.announce(BUDGET_NOTICE, "surprised", wait_for_followup=True)
    rig.assistant.handle_utterance(rig.utterance())
    quiet.on = False

    listen_once(rig)
    inside_the_window = list(rig.speech.texts)
    rig.clock.now += 8  # followup_seconds
    listen_once(rig)

    assert inside_the_window == [REPLY]
    assert rig.speech.texts == [REPLY, BUDGET_NOTICE]


def test_a_request_that_fails_is_logged_and_she_keeps_listening(caplog):
    rig = Rig(brain=BrokenBrain)
    rig.assistant.ask_aloud(GREETING_PROMPT, on_reply=lambda text: None)

    with caplog.at_level(logging.ERROR, logger="waifu.app"):
        rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))
    rig.assistant.announce(ANNOUNCEMENT)
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))

    assert "brain crashed" in caplog.text
    assert rig.speech.texts == [ANNOUNCEMENT]
    assert rig.assistant.busy is False


# Cyrene on the phone: her answers for the paired phone, voiced there and never on the PC

PHONE_TEXT = "nyalakan lampu kamar"


def wav_bytes(frames: bytes, sample_rate: int, *, channels: int = 1, width: int = 2) -> bytes:
    """A PCM WAV file of `frames`, as the phone page sends it."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as recording:
        recording.setnchannels(channels)
        recording.setsampwidth(width)
        recording.setframerate(sample_rate)
        recording.writeframes(frames)
    return buffer.getvalue()


def read_wav(data: bytes) -> tuple[tuple[int, int, int], np.ndarray]:
    """(channels, sample width, rate) of a WAV file and its frames as 16-bit samples."""
    with wave.open(io.BytesIO(data), "rb") as recording:
        shape = (recording.getnchannels(), recording.getsampwidth(), recording.getframerate())
        frames = recording.readframes(recording.getnframes())
    return shape, np.frombuffer(frames, dtype="<i2")


def test_a_phone_message_gets_her_reply_and_voice_without_a_sound_on_the_pc():
    rig = Rig(reply=f"[senang] {REPLY}", with_avatar=True)
    rig.assistant.set_mic(FakeMic(rig.events, []), FakeSegmenter(rig.events, []))

    reply = rig.assistant.reply_for_phone(PHONE_TEXT)

    assert rig.brain.turns == [(PHONE_TEXT, NOW)]
    assert (reply.text, reply.emotion) == (REPLY, "happy")
    assert rig.speech.texts == [REPLY]
    assert reply.audio_wav is not None
    # No microphone pause, playback, or avatar on the PC: Master is on the phone.
    assert rig.events == ["synthesize"]
    assert rig.player.played == []


def test_a_phone_turn_leaves_the_pc_s_follow_up_window_closed():
    rig = Rig("matikan lampu kamar")

    rig.assistant.reply_for_phone(PHONE_TEXT)
    rig.clock.now += 3

    assert rig.assistant.handle_utterance(rig.utterance()) is None  # the wake word is needed
    assert rig.brain.turns == [(PHONE_TEXT, NOW)]


def test_her_own_question_waits_until_master_has_been_quiet_on_the_phone_for_a_session():
    # At ask_at between two phone messages, the nightly question would wipe the phone chat
    # (a new session) and be said on the PC, where Master does not hear it: his next phone
    # message would then be taken for the answer to a question he never heard.
    rig = Rig(brain=ForgetfulBrain)
    rig.assistant.reply_for_phone(PHONE_TEXT)
    rig.assistant.ask_aloud(JOURNAL_PROMPT, new_session=True)

    listen_once(rig)
    rig.clock.now += 9 * 60  # session_idle_minutes is 10: the phone chat may go on
    rig.assistant.reply_for_phone("terus gimana?")
    rig.clock.now += 9 * 60
    listen_once(rig)
    waited = list(rig.brain.calls)
    rig.clock.now += 60  # session_idle_minutes after his last phone message
    listen_once(rig)

    assert waited == [PHONE_TEXT, "terus gimana?"]
    assert rig.brain.calls == [PHONE_TEXT, "terus gimana?", "<reset>", JOURNAL_PROMPT]
    assert rig.speech.texts[-1] == REPLY


def test_her_phone_voice_is_a_16_bit_mono_wav_at_24_khz_with_the_avatar_s_mouth_track():
    rig = Rig()
    times = np.arange(24000) / 48000  # half a second at 48 kHz
    rig.speech.voice = ((0.5 * np.sin(2 * np.pi * 440 * times)).astype(np.float32), 48000)

    reply = rig.assistant.reply_for_phone(PHONE_TEXT)

    shape, samples = read_wav(reply.audio_wav)
    assert shape == (1, 2, 24000)
    assert len(samples) == 12000
    assert reply.duration == pytest.approx(0.5)
    assert np.abs(samples).max() == pytest.approx(0.5 * 32767, rel=0.01)
    assert reply.fps == 60
    assert len(reply.visemes) == 30
    np.testing.assert_allclose(reply.visemes, viseme_track(samples / 32767, 24000, 60), atol=0.01)


def test_her_phone_voice_is_clipped_where_it_is_too_loud_instead_of_wrapping_around():
    rig = Rig()
    rig.speech.voice = (np.array([1.5, -1.5, 0.25] * 800, dtype=np.float32), 24000)

    reply = rig.assistant.reply_for_phone(PHONE_TEXT)

    assert read_wav(reply.audio_wav)[1][:3].tolist() == [32767, -32767, 8192]


def test_her_voice_volume_lowers_only_the_pc_s_speaker_not_her_mouth_or_the_phone():
    # The gain is in the PC's player: the avatar mouths the clip as it was voiced, and the
    # phone plays its own reply at the phone's volume.
    rig = Rig(with_avatar=True, volume=10)
    rig.speech.voice = (np.full(2400, 0.5, dtype=np.float32), 24000)

    rig.assistant.speak(REPLY)
    reply = rig.assistant.reply_for_phone(PHONE_TEXT)

    assert rig.player.peaks == [pytest.approx(0.05)]
    assert rig.avatar.peaks == [0.5]
    assert np.abs(read_wav(reply.audio_wav)[1]).max() == pytest.approx(0.5 * 32767, rel=0.01)


@pytest.mark.parametrize("error", [TtsError("Edge TTS gagal"), RuntimeError("RVC rusak")])
def test_a_phone_reply_she_cannot_voice_still_comes_back_as_text(error, caplog):
    rig = Rig(reply=f"[senang] {REPLY}", speech_error=error)

    with caplog.at_level(logging.WARNING, logger="waifu.app"):
        reply = rig.assistant.reply_for_phone(PHONE_TEXT)

    assert reply == PhoneReply(REPLY, "happy", None, 60, [], 0.0)
    assert str(error) in caplog.text


class SlowBrain(FakeBrain):
    """Holds its first turn until `release` is set, and counts the turns thought up at once."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.thinking = threading.Event()
        self.release = threading.Event()
        self.most_at_once = 0
        self._at_once = 0
        self._counting = threading.Lock()

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        with self._counting:
            self._at_once += 1
            self.most_at_once = max(self.most_at_once, self._at_once)
        try:
            if not self.thinking.is_set():
                self.thinking.set()
                self.release.wait(WAIT_S)
            return super().respond(user_text, now=now)
        finally:
            with self._counting:
                self._at_once -= 1


def test_a_phone_turn_waits_until_the_voice_loop_s_turn_has_been_thought_up():
    rig = Rig("Cyrene, matikan TV", brain=SlowBrain)
    voice = threading.Thread(target=rig.assistant.handle_utterance, args=(rig.utterance(),))
    voice.start()
    assert rig.brain.thinking.wait(WAIT_S)

    phone = threading.Thread(target=rig.assistant.reply_for_phone, args=(PHONE_TEXT,))
    phone.start()
    phone.join(0.1)
    waiting = phone.is_alive()
    rig.brain.release.set()
    voice.join(WAIT_S)
    phone.join(WAIT_S)

    assert waiting
    assert rig.brain.most_at_once == 1  # the history only ever gets one turn at a time
    assert [text for text, _ in rig.brain.turns] == ["matikan TV", PHONE_TEXT]


def test_a_phone_turn_goes_ahead_while_she_is_still_speaking_on_the_pc():
    rig = Rig("Cyrene, nyalakan lampu kamar")
    playing, release = hold_first_play(rig)
    voice = threading.Thread(target=rig.assistant.handle_utterance, args=(rig.utterance(),))
    voice.start()
    assert playing.wait(WAIT_S)

    reply = rig.assistant.reply_for_phone(PHONE_TEXT)
    still_speaking = voice.is_alive()
    release.set()
    voice.join(WAIT_S)

    assert reply.text == REPLY
    assert still_speaking  # the turn is not held while the PC plays her reply


def test_phone_audio_is_transcribed_without_speaker_verification_or_the_wake_word():
    rig = Rig(" nyalakan lampu kamar ", owner=(False, 0.05))
    samples = np.array([-32768, -16384, 0, 16384, 32767] * 3200, dtype="<i2")

    heard = rig.assistant.transcribe_for_phone(wav_bytes(samples.tobytes(), 16000))

    assert heard == "nyalakan lampu kamar"
    assert rig.verifier.calls == 0
    assert rig.brain.turns == []  # what was heard goes back to the phone first
    audio, sample_rate = rig.stt.audio[0]
    assert sample_rate == 16000
    assert audio.dtype == np.float32
    np.testing.assert_allclose(audio, samples / 32768)


def test_phone_audio_without_words_is_an_empty_transcript():
    rig = Rig("")

    assert rig.assistant.transcribe_for_phone(wav_bytes(bytes(32000), 16000)) == ""


@pytest.mark.parametrize(("channels", "width"), [(2, 2), (1, 1)])
def test_phone_audio_that_is_not_mono_16_bit_pcm_is_refused(channels, width):
    rig = Rig("halo")
    recording = wav_bytes(bytes(3200), 16000, channels=channels, width=width)

    with pytest.raises(ValueError, match="mono 16-bit"):
        rig.assistant.transcribe_for_phone(recording)

    assert rig.stt.calls == 0


def test_the_assistant_answers_the_phone_server_through_phone_answers():
    rig = Rig("halo Cyrene")
    backend: PhoneBackend = PhoneAnswers(rig.assistant)

    heard = backend.transcribe(wav_bytes(bytes(32000), 16000))
    reply = backend.chat(heard)

    assert heard == "halo Cyrene"
    assert rig.brain.turns == [("halo Cyrene", NOW)]
    assert reply.text == REPLY


def ask_phone_server(
    rig: Rig,
    state_path: Path,
    route: str,
    body: bytes,
    content_type: str,
    *,
    camera_max_side: int | None = None,
) -> tuple[int, dict]:
    """POSTs `body` from a paired phone to a real PhoneServer on 127.0.0.1 (a free port) whose
    backend is the rig's Assistant, its camera on with `camera_max_side`; the status and the
    JSON answer."""
    auth = PhoneAuth(state_path)
    token = auth.redeem(auth.new_code(), "Android")
    with PhoneServer(
        PhoneConfig().model_copy(update={"port": 0}),  # a free port, never 9883
        AvatarConfig(),
        auth,
        PhoneAnswers(rig.assistant),
        owner_call="Master",
        assistant_name="Cyrene",
        camera_max_side=camera_max_side,
    ) as server:
        host, port = server.server_address[:2]
        connection = http.client.HTTPConnection(host, port, timeout=WAIT_S)
        try:
            connection.request(
                "POST",
                route,
                body,
                {
                    "Origin": f"http://{host}:{port}",
                    "Content-Type": content_type,
                    "Cookie": f"cyrene_device={token}",
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()


def test_a_paired_phone_s_message_gets_her_reply_and_voice_from_the_phone_server(tmp_path):
    rig = Rig(reply=f"[senang] {REPLY}")
    message = json.dumps({"text": PHONE_TEXT}).encode()

    status, answer = ask_phone_server(
        rig, tmp_path / "phone.json", "/chat", message, "application/json"
    )

    assert status == 200
    assert (answer["text"], answer["emotion"]) == (REPLY, "happy")
    shape, samples = read_wav(base64.b64decode(answer["audio"]))
    assert (shape, len(samples)) == ((1, 2, 24000), 2400)
    assert (answer["fps"], len(answer["visemes"]), answer["duration"]) == (60, 6, 0.1)
    assert rig.brain.turns == [(PHONE_TEXT, NOW)]
    assert rig.player.played == []


def test_what_a_paired_phone_says_is_heard_and_answered_through_the_phone_server(tmp_path):
    rig = Rig(PHONE_TEXT, owner=(False, 0.05))
    recording = wav_bytes(bytes(32000), 16000)

    status, answer = ask_phone_server(rig, tmp_path / "phone.json", "/talk", recording, "audio/wav")

    assert status == 200
    assert (answer["heard"], answer["text"]) == (PHONE_TEXT, REPLY)
    assert rig.verifier.calls == 0
    assert rig.brain.turns == [(PHONE_TEXT, NOW)]


# Seeing through the phone camera: a photo goes with the phone message it was sent with


def camera_jpeg(width: int = 640, height: int = 480) -> bytes:
    """The head of a JPEG as the page's canvas makes one, built by hand: SOI, a baseline frame
    header of `width` x `height` with three components, and EOI. Nothing reads further."""
    components = b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    frame = struct.pack(">HBHHB", 8 + len(components), 8, height, width, 3) + components
    return b"\xff\xd8\xff\xc0" + frame + b"\xff\xd9"


PHOTO = camera_jpeg()
SEEN_PHOTO = ToolImage("image/jpeg", PHOTO)


class SeeingBrain(FakeBrain):
    """Takes the photo waiting for each turn from `turn_images`, as the Claude brain does, at the
    end of its turn instead of the start (a turn may take its time)."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.turn_images = TurnImages()
        self.seen: list[list[ToolImage]] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        reply = super().respond(user_text, now=now)
        self.seen.append(self.turn_images.take())
        return reply


def camera_rig(*heard: str, brain: type[SeeingBrain] = SeeingBrain) -> Rig:
    """A Rig whose Assistant and brain share one TurnImages, as `waifu run` wires them while the
    phone camera is offered."""
    images = TurnImages()
    rig = Rig(*heard, brain=brain, turn_images=images)
    rig.brain.turn_images = images
    return rig


def test_a_phone_photo_goes_to_her_with_the_words_it_was_sent_with():
    rig = camera_rig()

    reply = rig.assistant.reply_for_phone(PHONE_TEXT, PHOTO)

    assert rig.brain.turns == [(PHONE_TEXT, NOW)]
    assert rig.brain.seen == [[SEEN_PHOTO]]
    assert reply.text == REPLY
    assert rig.brain.turn_images.take() == []  # nothing waits for the next turn


def test_a_phone_message_without_a_photo_and_a_voice_turn_carry_none():
    rig = camera_rig("Cyrene, nyalakan lampu kamar")

    rig.assistant.reply_for_phone(PHONE_TEXT)
    rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.seen == [[], []]


class CrashingBrain(SeeingBrain):
    """Fails its first turn before taking the photo (say a provider's begin_turn breaks), then
    thinks as usual."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.crashed = False

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("brain crashed")
        return super().respond(user_text, now=now)


def test_a_phone_turn_that_fails_never_leaks_its_photo_into_the_next_voice_turn():
    rig = camera_rig("Cyrene, matikan TV", brain=CrashingBrain)

    with pytest.raises(RuntimeError, match="brain crashed"):
        rig.assistant.reply_for_phone(PHONE_TEXT, PHOTO)
    rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.turns == [("matikan TV", NOW)]
    assert rig.brain.seen == [[]]


class SlowSeeingBrain(SeeingBrain):
    """Holds its first turn until `release` is set, then takes the photo waiting for it."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.thinking = threading.Event()
        self.release = threading.Event()

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        if not self.thinking.is_set():
            self.thinking.set()
            self.release.wait(WAIT_S)
        return super().respond(user_text, now=now)


def test_a_phone_photo_waits_for_the_voice_loop_s_turn_and_goes_only_with_the_phone_s_words():
    rig = camera_rig("Cyrene, matikan TV", brain=SlowSeeingBrain)
    voice = threading.Thread(target=rig.assistant.handle_utterance, args=(rig.utterance(),))
    voice.start()
    assert rig.brain.thinking.wait(WAIT_S)

    phone = threading.Thread(target=rig.assistant.reply_for_phone, args=(PHONE_TEXT, PHOTO))
    phone.start()
    phone.join(0.1)
    rig.brain.release.set()
    voice.join(WAIT_S)
    phone.join(WAIT_S)

    assert [text for text, _ in rig.brain.turns] == ["matikan TV", PHONE_TEXT]
    assert rig.brain.seen == [[], [SEEN_PHOTO]]


def test_a_phone_photo_is_dropped_with_a_warning_while_the_camera_is_off(caplog):
    rig = Rig(reply=f"[senang] {REPLY}")

    with caplog.at_level(logging.DEBUG):
        reply = rig.assistant.reply_for_phone(PHONE_TEXT, PHOTO)

    assert rig.brain.turns == [(PHONE_TEXT, NOW)]
    assert (reply.text, reply.emotion) == (REPLY, "happy")
    [warning] = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert f"{len(PHOTO)} bytes" in warning.getMessage()
    # Photos are private: only their size is ever logged.
    assert base64.b64encode(PHOTO).decode("ascii") not in caplog.text
    assert repr(PHOTO)[2:-1] not in caplog.text


def test_phone_answers_hands_her_the_photo_a_message_carries():
    rig = camera_rig()
    backend: PhoneBackend = PhoneAnswers(rig.assistant)

    reply = backend.chat(PHONE_TEXT, image=PHOTO)

    assert reply.text == REPLY
    assert rig.brain.seen == [[SEEN_PHOTO]]


def test_a_photo_typed_with_on_the_phone_reaches_her_through_the_phone_server(tmp_path):
    rig = camera_rig()
    message = {"text": PHONE_TEXT, "image": base64.b64encode(PHOTO).decode("ascii")}

    status, answer = ask_phone_server(
        rig,
        tmp_path / "phone.json",
        "/chat",
        json.dumps(message).encode(),
        "application/json",
        camera_max_side=1280,
    )

    assert (status, answer["text"]) == (200, REPLY)
    assert rig.brain.turns == [(PHONE_TEXT, NOW)]
    assert rig.brain.seen == [[SEEN_PHOTO]]


def test_a_photo_said_with_on_the_phone_reaches_her_with_what_she_heard(tmp_path):
    rig = camera_rig(PHONE_TEXT)
    message = {
        "audio": base64.b64encode(wav_bytes(bytes(32000), 16000)).decode("ascii"),
        "image": base64.b64encode(PHOTO).decode("ascii"),
    }

    status, answer = ask_phone_server(
        rig,
        tmp_path / "phone.json",
        "/talk",
        json.dumps(message).encode(),
        "application/json",
        camera_max_side=1280,
    )

    assert (status, answer["heard"], answer["text"]) == (200, PHONE_TEXT, REPLY)
    assert rig.brain.seen == [[SEEN_PHOTO]]


# A phone message sent through the camera never lets a bare look word photograph the PC screen


class CameraWatchingBrain(FakeBrain):
    """Notes, for each turn, whether it was thought up as sent through the phone camera."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.camera_turn = CameraTurn()
        self.through_camera: list[bool] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.through_camera.append(self.camera_turn.active)
        return super().respond(user_text, now=now)


class CrashingCameraBrain(CameraWatchingBrain):
    """Fails its first turn, then thinks as usual."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.crashed = False

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        if not self.crashed:
            self.crashed = True
            raise RuntimeError("brain crashed")
        return super().respond(user_text, now=now)


class SlowCameraWatchingBrain(CameraWatchingBrain):
    """Notes whether each turn is a camera turn as it starts, and holds the first turn until
    `release` is set."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.thinking = threading.Event()
        self.release = threading.Event()

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        reply = super().respond(user_text, now=now)
        if not self.thinking.is_set():
            self.thinking.set()
            self.release.wait(WAIT_S)
        return reply


def camera_watching_rig(*heard: str, brain: type[CameraWatchingBrain] = CameraWatchingBrain) -> Rig:
    """A Rig whose Assistant and brain share one CameraTurn, as `waifu run` shares it with the
    screen tool while the phone camera is offered."""
    camera = CameraTurn()
    rig = Rig(*heard, brain=brain, camera_turn=camera)
    rig.brain.camera_turn = camera
    return rig


def test_a_phone_message_sent_with_the_viewfinder_open_is_thought_up_as_a_camera_turn():
    rig = camera_watching_rig()

    rig.assistant.reply_for_phone("coba lihat ini", camera=True)

    assert rig.brain.turns == [("coba lihat ini", NOW)]
    assert rig.brain.through_camera == [True]
    assert rig.brain.camera_turn.active is False  # over with its turn


def test_a_phone_photo_always_makes_its_turn_a_camera_turn():
    rig = camera_watching_rig()

    rig.assistant.reply_for_phone(PHONE_TEXT, PHOTO)

    assert rig.brain.through_camera == [True]


def test_phone_messages_without_the_camera_and_voice_turns_are_no_camera_turns():
    rig = camera_watching_rig("Cyrene, coba lihat")

    rig.assistant.reply_for_phone(PHONE_TEXT)
    rig.assistant.handle_utterance(rig.utterance())
    rig.assistant.handle_text("coba lihat")

    assert rig.brain.through_camera == [False, False, False]


def test_a_failed_camera_turn_never_marks_the_next_voice_turn():
    rig = camera_watching_rig("Cyrene, coba lihat", brain=CrashingCameraBrain)

    with pytest.raises(RuntimeError, match="brain crashed"):
        rig.assistant.reply_for_phone("coba lihat ini", camera=True)
    rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.through_camera == [False]
    assert rig.brain.camera_turn.active is False


def test_a_phone_message_sent_through_the_camera_is_marked_only_once_it_has_the_turn():
    rig = camera_watching_rig(
        "Cyrene, matikan TV", "Cyrene, coba lihat", brain=SlowCameraWatchingBrain
    )
    voice = threading.Thread(target=rig.assistant.handle_utterance, args=(rig.utterance(),))
    voice.start()
    assert rig.brain.thinking.wait(WAIT_S)
    queued_voice = threading.Thread(target=rig.assistant.handle_utterance, args=(rig.utterance(),))
    queued_voice.start()
    queued_voice.join(0.1)

    phone = threading.Thread(
        target=rig.assistant.reply_for_phone, args=("coba lihat ini",), kwargs={"camera": True}
    )
    phone.start()
    phone.join(0.1)
    waiting = phone.is_alive()
    marked_while_waiting = rig.brain.camera_turn.active
    rig.brain.release.set()
    for thread in (voice, queued_voice, phone):
        thread.join(WAIT_S)

    assert waiting
    # While it waits for the turn lock, a turn that gets it first (here the voice turn queued
    # before it) must not be taken for a camera turn: its "coba lihat" may mean the PC screen.
    assert marked_while_waiting is False
    turns = [text for text, _ in rig.brain.turns]
    assert dict(zip(turns, rig.brain.through_camera, strict=True)) == {
        "matikan TV": False,
        "coba lihat": False,
        "coba lihat ini": True,
    }


def test_phone_answers_tells_her_the_message_came_through_the_camera():
    rig = camera_watching_rig()
    backend: PhoneBackend = PhoneAnswers(rig.assistant)

    backend.chat("coba lihat ini", camera=True)
    backend.chat(PHONE_TEXT)

    assert rig.brain.through_camera == [True, False]


def test_a_message_sent_through_the_camera_without_a_photo_is_a_camera_turn_via_the_server(
    tmp_path,
):
    rig = camera_watching_rig()
    message = {"text": "coba lihat ini", "camera": True}

    status, answer = ask_phone_server(
        rig,
        tmp_path / "phone.json",
        "/chat",
        json.dumps(message).encode(),
        "application/json",
        camera_max_side=1280,
    )

    assert (status, answer["text"]) == (200, REPLY)
    assert rig.brain.through_camera == [True]


SCREEN = ScreenImage(png=encode_png(np.full((9, 16, 3), 200, dtype=np.uint8)), width=16, height=9)
DESCRIBED = "Begitu, Master."


class ScreenCapture:
    """Stands in for capture_screen: counts the screenshots, never touches the real screen."""

    def __init__(self) -> None:
        self.captures = 0

    def __call__(self, max_side: int) -> ScreenImage:
        self.captures += 1
        return SCREEN


class LookingClaude:
    """Stands in for anthropic.Anthropic as a model that answers every message by calling
    look_at_screen, then saying DESCRIBED; keeps each tool result it was sent."""

    api_key = "test-key"
    auth_token = None
    credentials = None

    def __init__(self) -> None:
        self.results: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request: object) -> SimpleNamespace:
        content = request["messages"][-1]["content"]
        if isinstance(content, list) and content[0]["type"] == "tool_result":
            self.results.append(content[0])
            reply = [SimpleNamespace(type="text", text=DESCRIBED)]
            return SimpleNamespace(content=reply, stop_reason="end_turn", stop_details=None)
        call = SimpleNamespace(type="tool_use", id="toolu_1", name="look_at_screen", input={})
        return SimpleNamespace(content=[call], stop_reason="tool_use", stop_details=None)


def looking_rig() -> tuple[Rig, ScreenCapture, LookingClaude]:
    """A Rig around a real Claude brain with the screen tool, sharing the photo holder and the
    camera turn as `waifu run` does while the phone camera is offered."""
    capture = ScreenCapture()
    client = LookingClaude()
    images = TurnImages()
    camera = CameraTurn()
    claude = ClaudeBrain(
        BrainConfig(backend="claude", claude=ClaudeConfig(model="claude-opus-5-5")),
        DeviceRegistry([MockDevice("lampu_kamar", "Lampu kamar", commands=["on", "off"])]),
        "Kamu Cyrene.",
        client=client,
        providers=[ScreenTools(capture=capture, camera_turn=camera)],
        turn_images=images,
    )
    rig = Rig(brain=lambda reply: claude, turn_images=images, camera_turn=camera)
    return rig, capture, client


@pytest.mark.parametrize(
    ("image", "camera"), [(PHOTO, False), (None, True)], ids=["with its photo", "photo not taken"]
)
@pytest.mark.parametrize("words", ["coba lihat ini", "lihat ini, bagus nggak bajuku?"])
def test_a_bare_look_word_from_the_phone_camera_never_photographs_the_pc_screen(
    words, image, camera
):
    rig, capture, client = looking_rig()

    reply = rig.assistant.reply_for_phone(words, image, camera=camera)

    assert capture.captures == 0
    [result] = client.results
    assert result["is_error"] is True
    assert "kamera HP" in result["content"]
    assert reply.text == DESCRIBED


def test_naming_the_pc_screen_from_the_phone_camera_still_looks_at_it():
    rig, capture, client = looking_rig()

    rig.assistant.reply_for_phone("lihat layarku", camera=True)

    assert capture.captures == 1
    [result] = client.results
    assert result["is_error"] is False


def test_after_a_camera_turn_a_bare_look_word_on_the_pc_looks_at_the_screen_again():
    rig, capture, _ = looking_rig()

    rig.assistant.reply_for_phone("coba lihat ini", camera=True)
    rig.assistant.handle_text("coba lihat ini")

    assert capture.captures == 1


# Requests held past their expiry: what she would have asked about a day that has ended

EXPIRY = datetime(2026, 10, 12, 21, 0)  # on the rig's wall clock, which starts at NOW (19:30)


def held_by_the_follow_up_window(rig: Rig, quiet: Quiet) -> Callable[[], None]:
    """Master may still answer her (a double-click opened the window); returns its end."""
    rig.assistant.listen_now()

    def lift() -> None:
        rig.clock.now += 8  # followup_seconds

    return lift


def held_by_a_cut(rig: Rig, quiet: Quiet) -> Callable[[], None]:
    """Master cut an announcement short to talk; returns the end of the hold."""
    cut_the_first_clip(rig)
    rig.assistant.announce(ANNOUNCEMENT)

    def lift() -> None:
        rig.clock.now += 8  # followup_seconds: Master said nothing

    return lift


def held_by_a_focus_block(rig: Rig, quiet: Quiet) -> Callable[[], None]:
    """A focus block is under way; returns its end."""
    quiet.on = True

    def lift() -> None:
        quiet.on = False

    return lift


def held_by_a_phone_chat(rig: Rig, quiet: Quiet) -> Callable[[], None]:
    """Master chats on the phone; returns the end of his session."""
    rig.assistant.reply_for_phone(PHONE_TEXT)

    def lift() -> None:
        rig.clock.now += 10 * 60  # session_idle_minutes after his last phone message

    return lift


HOLDS = pytest.mark.parametrize(
    "hold",
    [held_by_the_follow_up_window, held_by_a_cut, held_by_a_focus_block, held_by_a_phone_chat],
    ids=["follow_up_window", "cut", "focus_block", "phone_chat"],
)


def asked(rig: Rig) -> list[str]:
    return [text for text, _ in rig.brain.turns]


@HOLDS
def test_a_request_held_past_its_expiry_is_dropped_and_never_said(hold, caplog):
    # Her nightly question held past midnight would ask about "hari ini" on the new date.
    quiet = Quiet(on=False)
    rig = Rig(quiet=quiet, with_avatar=True)
    replies = PassedOn()
    rig.assistant.ask_aloud(GREETING_PROMPT, on_reply=replies, expires_at=EXPIRY)
    lift = hold(rig, quiet)
    listen_once(rig, None, None)  # the second frame would answer it, but for the hold
    said, shown = list(rig.speech.texts), len(rig.events)

    rig.now = EXPIRY
    lift()
    with caplog.at_level(logging.INFO, logger="waifu.app"):
        listen_once(rig)

    assert GREETING_PROMPT not in asked(rig)
    assert rig.speech.texts == said
    assert replies.texts == []
    assert [event for event in rig.events[shown:] if event.startswith("avatar")] == []
    assert "Not saying a request held past 2026-10-12 21:00:00: it is out of date" in caplog.text
    assert "Speaking up by herself" not in caplog.text


@HOLDS
def test_a_request_whose_expiry_has_not_passed_is_answered_after_the_hold(hold):
    quiet = Quiet(on=False)
    rig = Rig(quiet=quiet)
    replies = PassedOn()
    rig.assistant.ask_aloud(GREETING_PROMPT, on_reply=replies, expires_at=EXPIRY)
    lift = hold(rig, quiet)
    listen_once(rig, None, None)  # the second frame would answer it, but for the hold
    waited = asked(rig)

    rig.now = EXPIRY - timedelta(seconds=1)
    lift()
    listen_once(rig)

    assert GREETING_PROMPT not in waited
    assert asked(rig)[-1] == GREETING_PROMPT
    assert replies.wait() == [REPLY]


def test_a_request_without_an_expiry_is_answered_however_long_it_waited():
    rig = Rig()
    rig.assistant.ask_aloud(GREETING_PROMPT, expires_at=None)
    rig.now = NOW + timedelta(days=400)

    listen_once(rig)

    assert rig.brain.turns == [(GREETING_PROMPT, rig.now)]
    assert rig.speech.texts == [REPLY]


def test_every_expired_request_goes_in_one_pass_and_the_next_one_in_date_is_answered(caplog):
    # The phone held the weekly report and the journal question past midnight.
    midnight = datetime(2026, 10, 13)
    rig = Rig()
    rig.assistant.ask_aloud("Ini laporan game mingguanmu sendiri.", expires_at=midnight)
    rig.assistant.ask_aloud(JOURNAL_PROMPT, expires_at=midnight)
    rig.assistant.ask_aloud(GREETING_PROMPT, expires_at=midnight + timedelta(hours=12))
    rig.now = midnight + timedelta(minutes=5)

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        listen_once(rig)

    assert asked(rig) == [GREETING_PROMPT]
    assert rig.speech.texts == [REPLY]
    assert caplog.text.count("Not saying a request held past 2026-10-13 00:00:00") == 2


@pytest.mark.parametrize(
    ("offset", "answered"),
    [(timedelta(minutes=-1), True), (timedelta(seconds=1), False)],
    ids=["before", "after"],
)
def test_the_naive_wall_clock_is_compared_with_an_aware_expiry_as_the_pc_s_local_time(
    offset, answered
):
    # datetime.now is naive, while the journal question expires at midnight WIB.
    midnight_wib = datetime(2026, 10, 18, tzinfo=WIB)
    rig = Rig()
    rig.assistant.ask_aloud(JOURNAL_PROMPT, expires_at=midnight_wib)
    rig.now = (midnight_wib + offset).astimezone().replace(tzinfo=None)

    listen_once(rig)

    assert asked(rig) == ([JOURNAL_PROMPT] if answered else [])


# Private requests in guest mode: the journal question and the weekly report are skipped

WEEKLY_PROMPT = "Ini laporan game mingguanmu sendiri."


class GuestSwitch:
    """Stands in for guest mode's `is_on`, switched by the test."""

    def __init__(self, on: bool) -> None:
        self.on = on

    def __call__(self) -> bool:
        return self.on


def test_a_private_request_is_dropped_in_guest_mode_before_its_turn(caplog):
    rig = Rig(brain=ForgetfulBrain, with_avatar=True, guest_mode=GuestSwitch(on=True))
    replies = PassedOn()
    rig.assistant.ask_aloud(JOURNAL_PROMPT, on_reply=replies, new_session=True, private=True)

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        listen_once(rig)

    assert rig.brain.calls == []  # not even a new session for nothing
    assert rig.speech.texts == []
    assert replies.texts == []
    assert [event for event in rig.events if event.startswith("avatar")] == []
    assert "Not saying a private request while guest mode is on" in caplog.text
    assert JOURNAL_PROMPT not in caplog.text


@pytest.mark.parametrize("guest_mode", [GuestSwitch(on=False), None], ids=["off", "none"])
def test_a_private_request_is_answered_as_always_without_guests(guest_mode):
    rig = Rig(guest_mode=guest_mode)
    replies = PassedOn()
    rig.assistant.ask_aloud(JOURNAL_PROMPT, on_reply=replies, private=True)

    listen_once(rig)

    assert asked(rig) == [JOURNAL_PROMPT]
    assert replies.wait() == [REPLY]


def test_a_request_that_is_not_private_is_answered_in_guest_mode():
    # The morning greeting is said in guest mode, reduced by the briefing tool.
    rig = Rig(guest_mode=GuestSwitch(on=True))
    rig.assistant.ask_aloud(GREETING_PROMPT)

    listen_once(rig)

    assert asked(rig) == [GREETING_PROMPT]
    assert rig.speech.texts == [REPLY]


def test_every_private_request_goes_in_one_pass_and_the_next_one_is_answered(caplog):
    rig = Rig(guest_mode=GuestSwitch(on=True))
    rig.assistant.ask_aloud(WEEKLY_PROMPT, private=True)
    rig.assistant.ask_aloud(JOURNAL_PROMPT, private=True)
    rig.assistant.ask_aloud(GREETING_PROMPT)

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        listen_once(rig)

    assert asked(rig) == [GREETING_PROMPT]
    assert caplog.text.count("Not saying a private request while guest mode is on") == 2


def test_a_private_request_queued_before_guests_came_is_dropped_once_they_are_here():
    # Checked when she would answer it, after a hold: guest mode turned on meanwhile.
    guests = GuestSwitch(on=False)
    rig = Rig(guest_mode=guests)
    rig.assistant.ask_aloud(WEEKLY_PROMPT, private=True)
    rig.assistant.listen_now()  # the follow-up window holds it
    listen_once(rig)

    guests.on = True
    rig.clock.now += 8
    listen_once(rig)

    assert asked(rig) == []


# Announcements held past their expiry: a rain alert said after its rain began

RAIN_ALERT = "Master, sekitar jam sembilan malam kemungkinan hujan di Cianjur, angkat jemuran ya."


def test_an_announcement_held_past_its_expiry_is_dropped_and_never_said(caplog):
    quiet = Quiet()  # a focus block, or a game in game mode
    rig = Rig(quiet=quiet, with_avatar=True)
    rig.assistant.announce(RAIN_ALERT, wait_for_followup=True, expires_at=EXPIRY)
    listen_once(rig)

    rig.now = EXPIRY
    quiet.on = False
    with caplog.at_level(logging.INFO, logger="waifu.app"):
        listen_once(rig)

    assert rig.speech.texts == []
    assert [event for event in rig.events if event.startswith("avatar")] == []
    assert "Not saying an announcement held past 2026-10-12 21:00:00: it is out of date" in (
        caplog.text
    )
    assert RAIN_ALERT not in caplog.text


def test_a_patient_announcement_held_by_the_follow_up_window_past_its_expiry_is_dropped():
    rig = Rig()
    rig.assistant.listen_now()
    rig.assistant.announce(RAIN_ALERT, wait_for_followup=True, expires_at=EXPIRY)
    listen_once(rig)

    rig.now = EXPIRY + timedelta(seconds=1)
    rig.clock.now += 8  # followup_seconds
    listen_once(rig)

    assert rig.speech.texts == []


def test_the_announcements_still_in_date_are_said_together_with_the_first_one_s_emotion():
    quiet = Quiet()
    rig = Rig(quiet=quiet, with_avatar=True)
    rig.assistant.announce(RAIN_ALERT, "surprised", expires_at=EXPIRY)
    rig.assistant.announce(ANNOUNCEMENT, "happy", expires_at=EXPIRY + timedelta(hours=1))
    rig.assistant.announce(BUDGET_NOTICE, "sad", wait_for_followup=True)
    listen_once(rig)

    rig.now = EXPIRY
    rig.assistant.remind(BLOCK_DONE, "neutral")
    quiet.on = False
    listen_once(rig)

    assert rig.speech.texts == [f"{BLOCK_DONE} {ANNOUNCEMENT} {BUDGET_NOTICE}"]
    assert "avatar.speak neutral" in rig.events


def test_an_announcement_whose_expiry_has_not_passed_is_said_after_the_hold():
    quiet = Quiet()
    rig = Rig(quiet=quiet)
    rig.assistant.announce(RAIN_ALERT, wait_for_followup=True, expires_at=EXPIRY)
    listen_once(rig)

    rig.now = EXPIRY - timedelta(seconds=1)
    quiet.on = False
    listen_once(rig)

    assert rig.speech.texts == [RAIN_ALERT]


# Announcements a game held (game mode): said once it has closed, briefly and framed

GPU_HOT = "Master, GPU-nya sudah 86°C beberapa menit. Istirahat sebentar atau cek pendinginnya, ya."


def test_a_notice_queued_twice_while_held_is_said_once():
    quiet = Quiet()
    rig = Rig(quiet=quiet)
    rig.assistant.announce(GPU_HOT, "sad")
    rig.assistant.announce(ANNOUNCEMENT)
    rig.assistant.announce(GPU_HOT, "sad")
    listen_once(rig)

    quiet.on = False
    listen_once(rig)

    assert rig.speech.texts == [f"{GPU_HOT} {ANNOUNCEMENT}"]


def test_announcements_a_game_held_come_out_after_a_short_lead_in_and_the_reminders():
    game = Quiet()
    rig = Rig(quiet=game, game_holding=game, with_avatar=True)
    rig.assistant.announce(ANNOUNCEMENT, "happy")
    rig.assistant.announce(BUDGET_NOTICE, "surprised", wait_for_followup=True)
    listen_once(rig)

    rig.assistant.remind(MEDICINE, "neutral")  # due as the game closes
    game.on = False
    listen_once(rig)

    assert rig.speech.texts == [f"{MEDICINE} {GAME_HELD} {ANNOUNCEMENT} {BUDGET_NOTICE}"]
    assert "avatar.speak neutral" in rig.events


def test_announcements_a_focus_block_held_come_out_without_the_game_s_lead_in():
    focus = Quiet()
    rig = Rig(quiet=focus, game_holding=Quiet(on=False))
    rig.assistant.announce(ANNOUNCEMENT)
    listen_once(rig)

    focus.on = False
    listen_once(rig)

    assert rig.speech.texts == [ANNOUNCEMENT]


def test_the_lead_in_comes_once_and_news_after_the_game_has_none():
    game = Quiet()
    rig = Rig(quiet=game, game_holding=game)
    rig.assistant.announce(ANNOUNCEMENT)
    listen_once(rig)
    game.on = False
    listen_once(rig)

    rig.assistant.announce(GPU_HOT, "sad")
    listen_once(rig)

    assert rig.speech.texts == [f"{GAME_HELD} {ANNOUNCEMENT}", GPU_HOT]


def test_when_everything_a_game_held_has_expired_she_says_nothing_and_later_news_has_no_lead_in():
    game = Quiet()
    rig = Rig(quiet=game, game_holding=game)
    rig.assistant.announce(RAIN_ALERT, expires_at=EXPIRY)
    listen_once(rig)
    rig.now = EXPIRY
    game.on = False
    listen_once(rig)

    rig.assistant.announce(ANNOUNCEMENT)
    listen_once(rig)

    assert rig.speech.texts == [ANNOUNCEMENT]


def test_an_announcement_without_an_expiry_is_said_however_long_it_waited():
    quiet = Quiet()
    rig = Rig(quiet=quiet)
    rig.assistant.announce(ANNOUNCEMENT, expires_at=None)
    listen_once(rig)

    rig.now = NOW + timedelta(days=400)
    quiet.on = False
    listen_once(rig)

    assert rig.speech.texts == [ANNOUNCEMENT]


# What only a game makes out of date: the gacha reminders about a game's state and the hot-GPU
# notice wait through a focus block however long it lasts (v1.30); a game held them, they go

EVENING = datetime(2026, 10, 12, 20, 30, tzinfo=WIB)  # the dailies are mentioned from 20:00
STAMINA_NEARLY_FULL_DAILIES_OPEN = GameStatus(
    game="hsr",
    stamina=295,
    stamina_max=300,
    stamina_full_in_s=30 * 60,
    daily_current=300,
    daily_max=500,
    daily_done=False,
    weekly=(),
    expeditions_total=4,
    expeditions_finished=1,
    checked_in=True,
)
HOT_GPU = PcSnapshot(
    35.0,
    14.2,
    31.9,
    (Drive("C:", 120.0, 465.1),),
    GpuStatus("NVIDIA GeForce RTX 5060 Ti", 88, 97, 12.1, 15.9, 171.5),
    5 * 3600,
)
NO_GAME_HOLDS = pytest.mark.parametrize(
    "game_holding", [None, lambda: False], ids=["no_game_mode", "no_game_running"]
)


class StatusHoyolab:
    """HoYoLAB as the gacha watcher reads it: every game in `status`; the check-in is off."""

    def __init__(self, status: GameStatus) -> None:
        self._status = status

    def status(self, games: list[str]) -> dict[str, GameStatus]:
        return {game: self._status for game in games}

    def checkin(self, game_id: str) -> None:
        raise AssertionError("the check-in is off")


def gacha_reminders(rig: Rig, tmp_path: Path) -> list[str]:
    """One poll of the real gacha watcher on the rig's wall clock, telling the rig's Assistant
    (no push), at EVENING: its notices' texts (resin nearly full, the dailies open)."""
    rig.now = EVENING
    config = GachaConfig(enabled=True, games=["hsr"], checkin=False, data_dir=tmp_path / "gacha")
    watcher = GachaWatcher(
        StatusHoyolab(STAMINA_NEARLY_FULL_DAILIES_OPEN),
        config,
        notifier=None,
        announce=rig.assistant.announce,
        now=lambda: rig.now,
    )
    told = watcher.poll_once()
    assert [notice.kind for notice in told] == ["stamina", "daily"]
    return [notice.text for notice in told]


def hot_gpu_notice(rig: Rig) -> str:
    """The real PC watcher on the rig's clocks, telling the rig's Assistant (no push), once a
    minute until the GPU has been hot for gpu_warn_minutes: its notice."""
    watcher = PcWatcher(
        lambda: HOT_GPU,
        PcHealthConfig(),
        announce=rig.assistant.announce,
        notifier=None,
        owner_call="Master",
        clock=rig.clock,
        now=lambda: rig.now,
    )
    told = watcher.poll_once()
    for _ in range(3):  # gpu_warn_minutes
        rig.clock.now += 60
        told += watcher.poll_once()
    [notice] = told
    return notice


@NO_GAME_HOLDS
def test_a_focus_block_keeps_the_gacha_reminders_about_a_game_s_state_however_long_it_lasts(
    game_holding, tmp_path
):
    # As before game mode: nothing was played, so what they tell has not changed by playing.
    focus = Quiet()
    rig = Rig(quiet=focus, game_holding=game_holding)
    reminders = gacha_reminders(rig, tmp_path)
    listen_once(rig)

    rig.now += timedelta(minutes=30)  # past the watcher's next poll (poll_minutes 15)
    focus.on = False
    listen_once(rig)

    assert rig.speech.texts == [" ".join(reminders)]


def test_a_game_drops_the_gacha_reminders_about_its_state_held_past_the_next_poll(tmp_path, caplog):
    game = Quiet()
    rig = Rig(quiet=game, game_holding=game)
    gacha_reminders(rig, tmp_path)
    listen_once(rig)

    rig.now += timedelta(minutes=30)
    game.on = False
    with caplog.at_level(logging.INFO, logger="waifu.app"):
        listen_once(rig)

    assert rig.speech.texts == []
    assert "Not saying an announcement a game held past 2026-10-12 20:45:00+07:00" in caplog.text


def test_gacha_reminders_a_game_held_until_before_the_next_poll_are_said_after_the_lead_in(
    tmp_path,
):
    game = Quiet()
    rig = Rig(quiet=game, game_holding=game)
    reminders = gacha_reminders(rig, tmp_path)
    listen_once(rig)

    rig.now += timedelta(minutes=10)
    game.on = False
    listen_once(rig)

    assert rig.speech.texts == [" ".join([GAME_HELD, *reminders])]


def test_a_game_started_during_a_focus_block_drops_the_reminders_it_held_past_the_next_poll(
    tmp_path,
):
    # Master played during the block, so the resin and the dailies may have changed.
    focus, game = Quiet(), Quiet(on=False)
    rig = Rig(quiet=lambda: focus() or game(), game_holding=game)
    gacha_reminders(rig, tmp_path)
    listen_once(rig)

    game.on = True
    listen_once(rig)
    rig.now += timedelta(minutes=30)
    focus.on = game.on = False
    listen_once(rig)

    assert rig.speech.texts == []


def test_a_game_closed_during_a_focus_block_leaves_what_came_after_it_to_the_block(tmp_path):
    # Only the block held the reminders queued once the game had closed: kept however long it
    # lasts, said after what the game held.
    focus, game = Quiet(), Quiet()
    rig = Rig(quiet=lambda: focus() or game(), game_holding=game)
    rig.assistant.announce(ANNOUNCEMENT)
    listen_once(rig)
    game.on = False
    listen_once(rig)
    reminders = gacha_reminders(rig, tmp_path)
    listen_once(rig)

    rig.now += timedelta(minutes=30)
    focus.on = False
    listen_once(rig)

    assert rig.speech.texts == [" ".join([GAME_HELD, ANNOUNCEMENT, *reminders])]


@NO_GAME_HOLDS
def test_a_focus_block_keeps_the_hot_gpu_notice_however_long_it_lasts(game_holding):
    focus = Quiet()
    rig = Rig(quiet=focus, game_holding=game_holding)
    notice = hot_gpu_notice(rig)
    listen_once(rig)

    rig.now += timedelta(hours=2)
    focus.on = False
    listen_once(rig)

    assert rig.speech.texts == [notice]


def test_a_game_drops_the_hot_gpu_notice_it_held_for_an_hour():
    game = Quiet()
    rig = Rig(quiet=game, game_holding=game)
    hot_gpu_notice(rig)
    listen_once(rig)

    rig.now += timedelta(hours=1)
    game.on = False
    listen_once(rig)

    assert rig.speech.texts == []


# Ulangi (v1.38): her last words on the PC, said again without the brain

LAMP_ON = "[senang] Lampu kamar sudah menyala, Master."
NOTHING_TO_REPEAT = "Belum ada yang bisa kuulangi, Master."


def test_ulangi_says_her_last_reply_again_word_for_word_with_its_emotion_without_the_brain(
    caplog,
):
    rig = Rig("Cyrene, nyalakan lampu kamar", "ulangi", reply=LAMP_ON, with_avatar=True)
    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 2  # inside the follow-up window

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        repeated = rig.assistant.handle_utterance(rig.utterance())

    assert repeated == "Lampu kamar sudah menyala, Master."
    assert asked(rig) == ["nyalakan lampu kamar"]
    assert rig.speech.texts == [repeated, repeated]
    assert rig.events.count("avatar.speak happy") == 2
    assert "Repeating what she said last" in caplog.text
    assert repeated not in caplog.text  # reply text stays out of the INFO log


ADDRESSED = pytest.mark.parametrize(
    ("addressed", "wait_s"),
    [("{}", 2), ("Cyrene, {}", 2), ("Cyrene, {}", 30)],
    ids=["follow_up_window", "named_in_the_follow_up_window", "wake_word"],
)


@ADDRESSED
@pytest.mark.parametrize(
    "phrase",
    [
        "ulangi",
        "Ulang lagi.",
        "Bisa diulang?",
        "Tadi bilang apa?",
        "Apa tadi?",
        "Sekali lagi.",
        "Maaf, apa?",
        "Hah?",
    ],
)
def test_each_repeat_phrase_is_answered_without_the_brain(phrase, addressed, wait_s):
    rig = Rig("Cyrene, nyalakan lampu kamar", addressed.format(phrase))
    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += wait_s

    assert rig.assistant.handle_utterance(rig.utterance()) == REPLY
    assert asked(rig) == ["nyalakan lampu kamar"]


@pytest.mark.parametrize(
    "lookalike",
    [
        "ulangi lagunya",
        "putar sekali lagi",
        "apa tadi lampunya sudah mati?",
        "tadi aku bilang apa?",
    ],
)
def test_a_command_that_only_looks_like_a_repeat_request_goes_to_the_brain(lookalike):
    rig = Rig(f"Cyrene, {lookalike}", "ulangi", reply="Baik, Master.")
    first = rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 2
    repeated = rig.assistant.handle_utterance(rig.utterance())

    assert asked(rig) == [lookalike]
    assert first == repeated == "Baik, Master."


def test_coba_sekali_lagi_after_a_failure_asks_the_brain_to_try_again():
    rig = Rig(
        "Cyrene, nyalakan lampu kamar",
        "coba sekali lagi",
        reply="Maaf Master, lampu kamar tidak merespons.",
    )
    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 2  # inside the follow-up window, so no name is needed
    rig.brain.reply = REPLY

    retried = rig.assistant.handle_utterance(rig.utterance())

    assert retried == REPLY
    assert asked(rig) == ["nyalakan lampu kamar", "coba sekali lagi"]


def test_with_nothing_said_yet_she_says_so_and_listens_for_master(caplog):
    rig = Rig("Cyrene, ulangi", "nyalakan lampu kamar", with_avatar=True)

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        said = rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 2
    rig.assistant.handle_utterance(rig.utterance())

    assert said == NOTHING_TO_REPEAT
    assert rig.speech.texts == [NOTHING_TO_REPEAT, REPLY]
    assert rig.events[:3] == ["synthesize", "avatar.state speaking", "avatar.speak neutral"]
    assert "Nothing to repeat" in caplog.text
    assert asked(rig) == ["nyalakan lampu kamar"]  # her line opened the follow-up window


def test_nothing_to_repeat_calls_the_owner_what_she_calls_him():
    rig = Rig("Cyrene, ulangi", "Sayang, ulangi", config=CALLED_SAYANG)

    said = rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 2
    again = rig.assistant.handle_utterance(rig.utterance())

    assert said == again == "Belum ada yang bisa kuulangi, Sayang."
    assert rig.brain.turns == []


def test_the_acknowledgement_her_reactions_and_the_startup_line_are_never_repeated():
    rig = Rig("Cyrene, nyalakan lampu kamar", "Cyrene?", "ulangi")
    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 30
    rig.assistant.speak("Cyrene siap, Master.", followup=False)  # `waifu run --announce`
    rig.assistant.handle_utterance(rig.utterance())  # the acknowledgement
    rig.assistant.listen_now()
    rig.assistant.speak_reaction(PAT_LINE, "happy")
    rig.clock.now += 2

    assert rig.assistant.handle_utterance(rig.utterance()) == REPLY
    assert asked(rig) == ["nyalakan lampu kamar"]


def cut_off(rig: Rig) -> None:
    cut_the_first_clip(rig)


def not_voiced(rig: Rig) -> None:
    rig.speech.error = TtsError("edge-tts is unreachable")


@pytest.mark.parametrize("trouble", [cut_off, not_voiced], ids=["cut_off", "not_voiced"])
def test_a_reply_cut_off_or_not_voiced_is_repeated_whole(trouble):
    rig = Rig("Cyrene, nyalakan lampu kamar", "Cyrene, ulangi")
    trouble(rig)
    rig.assistant.handle_utterance(rig.utterance())

    repeated = rig.assistant.handle_utterance(rig.utterance())

    assert repeated == REPLY
    assert rig.speech.texts == [REPLY, REPLY]
    assert asked(rig) == ["nyalakan lampu kamar"]


def test_announcements_said_in_one_go_are_repeated_as_said_with_the_first_emotion():
    rig = Rig("Cyrene, ulangi", with_avatar=True)
    rig.assistant.remind(MEDICINE, "happy")
    rig.assistant.announce(ANNOUNCEMENT, "sad")
    listen_once(rig)

    repeated = rig.assistant.handle_utterance(rig.utterance())

    assert repeated == f"{MEDICINE} {ANNOUNCEMENT}"
    assert rig.speech.texts == [repeated, repeated]
    assert rig.events.count("avatar.speak happy") == 2
    assert rig.brain.turns == []


def test_her_spoken_answer_to_a_request_is_repeated_with_its_emotion():
    rig = Rig("ulangi", reply="[senang] Selamat pagi, Master! Hari ini cerah.", with_avatar=True)
    rig.assistant.ask_aloud(GREETING_PROMPT)
    listen_once(rig)
    rig.clock.now += 2  # inside the window her answer opened

    repeated = rig.assistant.handle_utterance(rig.utterance())

    assert repeated == "Selamat pagi, Master! Hari ini cerah."
    assert rig.speech.texts == [repeated, repeated]
    assert rig.events.count("avatar.speak happy") == 2
    assert asked(rig) == [GREETING_PROMPT]


def test_an_answer_to_a_request_that_was_not_said_is_never_repeated():
    rig = Rig("Cyrene, nyalakan lampu kamar", "Cyrene, ulangi")
    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 30
    rig.brain.reply = "Maaf Master, ringkasannya belum bisa kuambil."
    rig.assistant.ask_aloud(GREETING_PROMPT, required_tool="daily_briefing")
    listen_once(rig)  # its turn has no daily_briefing call: logged, not said

    assert rig.assistant.handle_utterance(rig.utterance()) == REPLY
    assert asked(rig) == ["nyalakan lampu kamar", GREETING_PROMPT]


def test_a_phone_reply_is_never_repeated_on_the_pc_and_a_phone_ulangi_goes_to_the_brain():
    rig = Rig("Cyrene, nyalakan lampu kamar", "Cyrene, ulangi")
    rig.assistant.handle_utterance(rig.utterance())
    rig.brain.reply = "Yang tadi: lampu kamar sudah menyala, Master."

    on_phone = rig.assistant.reply_for_phone("ulangi")
    repeated = rig.assistant.handle_utterance(rig.utterance())

    assert on_phone.text == "Yang tadi: lampu kamar sudah menyala, Master."
    assert repeated == REPLY
    assert asked(rig) == ["nyalakan lampu kamar", "ulangi"]


SHUTDOWN_QUESTION = "PC mau kumatikan sekarang, Master? Jawab iya kalau jadi."


class AskingBrain(FakeBrain):
    """Turns the PC off when Master asks for it or says yes, held by a real ConfirmationGate
    until his yes in the next turn, like the model calling the gated tool; `ran` counts the
    shutdowns that ran."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.gate = ConfirmationGate(clock=FakeClock(), names=["Cyrene"])
        self.ran = 0

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.turns.append((user_text, now))
        self.gate.begin_turn(user_text)
        words = user_text.lower()
        if "matikan" not in words and not words.startswith("iya"):
            return BrainReply("Hmm?", [])
        if self.gate.check_action("pc_off", label="Mematikan PC", question=SHUTDOWN_QUESTION):
            return BrainReply(self.gate.with_question("Baik."), [])
        self.ran += 1
        return BrainReply("Sudah kumatikan, Master.", [])


def test_a_repeat_says_her_question_again_and_leaves_it_waiting_for_master_s_yes():
    rig = Rig("Cyrene, matikan PC", "ulangi", "iya", brain=AskingBrain)
    question = rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 2
    repeated = rig.assistant.handle_utterance(rig.utterance())
    ran_after_the_repeat = rig.brain.ran
    rig.clock.now += 2  # inside the window the repeat opened
    rig.assistant.handle_utterance(rig.utterance())

    assert question == repeated == f"Baik. {SHUTDOWN_QUESTION}"
    assert ran_after_the_repeat == 0  # the repeat is never a yes
    assert asked(rig) == ["matikan PC", "iya"]
    assert rig.brain.ran == 1


def test_while_quiet_a_repeat_is_answered_and_her_held_announcements_still_wait():
    quiet = Quiet()
    rig = Rig("Cyrene, nyalakan lampu kamar", "Cyrene, ulangi", quiet=quiet)
    rig.assistant.announce(ANNOUNCEMENT)

    listen_once(rig, rig.utterance(), None, rig.utterance(), None)
    during = list(rig.speech.texts)
    quiet.on = False
    listen_once(rig)

    assert during == [REPLY, REPLY]
    assert asked(rig) == ["nyalakan lampu kamar"]
    assert rig.speech.texts == [REPLY, REPLY, ANNOUNCEMENT]


def test_a_repeat_opens_the_follow_up_window_like_a_reply():
    rig = Rig("Cyrene, nyalakan lampu kamar", "ulangi", "matikan TV")
    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 6
    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 6  # past the reply's window, inside the repeat's

    assert rig.assistant.handle_utterance(rig.utterance()) == REPLY
    assert asked(rig) == ["nyalakan lampu kamar", "matikan TV"]


class GuestMarker:
    """Stands in for guest mode's `generation`, moved by the test or a brain."""

    def __init__(self) -> None:
        self.generation = 0

    def __call__(self) -> int:
        return self.generation


def test_once_guest_mode_has_switched_her_earlier_words_are_not_repeated():
    marker = GuestMarker()
    rig = Rig("Cyrene, nyalakan lampu kamar", "Cyrene, ulangi", guest_marker=marker)
    rig.assistant.handle_utterance(rig.utterance())
    marker.generation += 1  # switched on the phone, or ended by itself: nothing said on the PC

    assert rig.assistant.handle_utterance(rig.utterance()) == NOTHING_TO_REPEAT
    assert asked(rig) == ["nyalakan lampu kamar"]


class SwitchingBrain(FakeBrain):
    """Moves guest mode's generation in each of its turns, as the guest_mode tool does."""

    def __init__(self, reply: str, marker: GuestMarker) -> None:
        super().__init__(reply)
        self.marker = marker

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.marker.generation += 1
        return super().respond(user_text, now=now)


def test_a_reply_whose_turn_switched_guest_mode_is_never_repeated():
    # Guest mode refuses per tool call, so the balance was fetched before the switch.
    marker = GuestMarker()
    rig = Rig(
        "Cyrene, berapa saldoku, terus nyalakan mode tamu",
        "ulangi",
        reply="Saldo BCA Master 12 juta. Mode tamu aktif sampai jam dua.",
        brain=lambda reply: SwitchingBrain(reply, marker),
        guest_marker=marker,
    )
    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 2

    assert rig.assistant.handle_utterance(rig.utterance()) == NOTHING_TO_REPEAT
    assert asked(rig) == ["berapa saldoku, terus nyalakan mode tamu"]


def test_an_answer_to_a_request_whose_turn_switched_guest_mode_is_never_repeated():
    marker = GuestMarker()  # switched on the phone while her answer was thought up
    rig = Rig(
        "ulangi",
        reply="Selamat pagi, Master! Saldo BCA 12 juta.",
        brain=lambda reply: SwitchingBrain(reply, marker),
        guest_marker=marker,
    )
    rig.assistant.ask_aloud(GREETING_PROMPT)
    listen_once(rig)
    rig.clock.now += 2  # inside the window her answer opened

    assert rig.assistant.handle_utterance(rig.utterance()) == NOTHING_TO_REPEAT
    assert asked(rig) == [GREETING_PROMPT]


def test_a_reply_in_a_turn_after_guest_mode_switched_is_repeated():
    marker = GuestMarker()
    rig = Rig("Cyrene, nyalakan lampu kamar", "Cyrene, ulangi", guest_marker=marker)
    marker.generation += 1  # guest mode turned on before this turn

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.assistant.handle_utterance(rig.utterance()) == REPLY
    assert asked(rig) == ["nyalakan lampu kamar"]


# --- the alarm ---------------------------------------------------------------------------------

HALF_SCALE = (np.full(2400, 0.5, dtype=np.float32), 24000)  # peaks at half the level played


class AlarmRig(Rig):
    """A Rig with an alarm bell on files under `tmp_path`, on the rig's clocks; every clip peaks
    at half its level, so `player.peaks` tells the level of each."""

    def __init__(
        self, tmp_path, *heard: str | Exception, day_level: int = 100, max_entries=50, **kwargs
    ) -> None:
        self.store = ScheduleStore(
            tmp_path / "schedule.json", max_entries=max_entries, now=lambda: self.now
        )
        self.bell = AlarmBell(
            tmp_path / "alarm.json",
            self.store,
            repeat_seconds=60,
            day_level=lambda: day_level,
            now=lambda: self.now,
            clock=lambda: self.clock.now,
        )
        super().__init__(*heard, alarm=self.bell, **kwargs)
        self.speech.voice = HALF_SCALE

    def start_alarm(self, label: str | None = None) -> None:
        self.bell.start(Entry("j1", self.now, "none", AlarmAction(label), self.now))

    def pass_seconds(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.clock.now += seconds


def test_a_due_ring_is_said_by_itself_at_its_level_as_a_reply(tmp_path):
    rig = AlarmRig(tmp_path, with_avatar=True)
    rig.start_alarm()

    listen_once(rig)

    assert rig.speech.texts == ["Master, bangun! Sudah jam 19.30."]
    assert rig.player.peaks == [pytest.approx(0.5)]
    assert "avatar.speak happy" in rig.events
    assert rig.gate.check("aku masih ngantuk").accepted is True  # the follow-up window opened


def test_each_next_ring_comes_after_its_minute_louder(tmp_path):
    rig = AlarmRig(tmp_path)
    rig.start_alarm("berangkat kerja")

    for _ in range(3):
        listen_once(rig, None, None)  # one ring per minute, however often she asks
        rig.pass_seconds(60)

    assert rig.speech.texts == [
        "Master, bangun! Sudah jam 19.30. Berangkat kerja.",
        "Ayo bangun, Master! Sudah jam 19.31. Berangkat kerja.",
        "Master, bangun dong! Sudah jam 19.32. Berangkat kerja.",
    ]
    assert rig.player.peaks == [pytest.approx(0.5), pytest.approx(0.6), pytest.approx(0.7)]


def test_a_ring_is_never_whispered_and_her_next_reply_is_at_her_level_again(tmp_path):
    rig = AlarmRig(tmp_path, "Cyrene, nyalakan lampu kamar", volume=30, day_level=100)
    rig.start_alarm()

    listen_once(rig, None, rig.utterance())

    assert rig.speech.texts == ["Master, bangun! Sudah jam 19.30.", REPLY]
    assert rig.player.peaks == [pytest.approx(0.5), pytest.approx(0.15)]


def test_a_ring_comes_first_on_its_own_and_the_announcements_after_it(tmp_path):
    rig = AlarmRig(tmp_path)
    rig.assistant.remind("Master, waktunya minum obat!")
    rig.assistant.announce(ANNOUNCEMENT)
    rig.start_alarm()

    listen_once(rig)
    first = list(rig.speech.texts)
    listen_once(rig)

    assert first == ["Master, bangun! Sudah jam 19.30."]
    assert rig.speech.texts == [
        "Master, bangun! Sudah jam 19.30.",
        f"Master, waktunya minum obat! {ANNOUNCEMENT}",
    ]


def test_a_ring_is_said_while_quiet_holds_her_announcements(tmp_path):
    rig = AlarmRig(tmp_path, quiet=Quiet(on=True))
    rig.assistant.announce(ANNOUNCEMENT)
    rig.start_alarm()

    listen_once(rig, None, None)

    assert rig.speech.texts == ["Master, bangun! Sudah jam 19.30."]


def test_a_ring_waits_while_master_speaks(tmp_path):
    rig = AlarmRig(tmp_path)
    rig.start_alarm()

    rig.assistant.run(
        FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None], busy=[True])
    )
    while_he_spoke = list(rig.speech.texts)
    listen_once(rig)

    assert while_he_spoke == []
    assert rig.speech.texts == ["Master, bangun! Sudah jam 19.30."]


def test_her_requests_wait_while_it_rings_and_come_after_the_stop(tmp_path):
    rig = AlarmRig(tmp_path)
    rig.assistant.ask_aloud(GREETING_PROMPT)
    rig.start_alarm()

    listen_once(rig)
    rig.pass_seconds(10)  # past the follow-up window, before the next ring
    listen_once(rig)
    while_ringing = asked(rig)
    rig.bell.stop()
    listen_once(rig)

    assert while_ringing == []
    assert asked(rig) == [GREETING_PROMPT]


def test_a_ring_is_never_what_ulangi_repeats(tmp_path):
    rig = AlarmRig(tmp_path, "Cyrene, nyalakan lampu kamar", "ulangi")
    rig.assistant.handle_utterance(rig.utterance())
    rig.start_alarm()
    listen_once(rig)

    repeated = rig.assistant.handle_utterance(rig.utterance())

    assert repeated == REPLY


@pytest.mark.parametrize("phrase", ["aku sudah bangun", "Cyrene, matikan alarm", "udah bangun"])
def test_a_stop_phrase_stops_the_ring_without_the_brain(tmp_path, phrase):
    rig = AlarmRig(tmp_path, phrase)
    rig.start_alarm()
    listen_once(rig)

    answer = rig.assistant.handle_utterance(rig.utterance())
    rig.pass_seconds(60)
    listen_once(rig)

    assert answer == "Oke Master, alarmnya kumatikan."
    assert rig.speech.texts == ["Master, bangun! Sudah jam 19.30.", answer]
    assert asked(rig) == []
    assert rig.bell.ringing() is False


def test_a_stop_phrase_needs_no_name_also_after_the_follow_up_window(tmp_path, caplog):
    rig = AlarmRig(tmp_path, "udah bangun")
    rig.start_alarm()
    listen_once(rig)
    rig.pass_seconds(30)  # the ring's window has closed

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        answer = rig.assistant.handle_utterance(rig.utterance())

    assert answer == "Oke Master, alarmnya kumatikan."
    assert rig.bell.ringing() is False
    assert "Alarm stopped by voice" in caplog.text


def test_a_snooze_phrase_snoozes_it_without_the_brain(tmp_path, caplog):
    rig = AlarmRig(tmp_path, "tunda 10 menit")
    rig.start_alarm("berangkat kerja")
    listen_once(rig)

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        answer = rig.assistant.handle_utterance(rig.utterance())

    assert answer == "Oke, kubangunkan lagi jam 19.40."
    assert [(e.at, e.action) for e in rig.store.entries()] == [
        (datetime(2026, 10, 12, 19, 40), AlarmAction("berangkat kerja"))
    ]
    assert rig.bell.ringing() is False
    assert asked(rig) == []
    assert "Alarm snoozed by voice" in caplog.text


def test_a_snooze_the_schedule_refuses_is_said_and_the_ring_goes_on(tmp_path):
    rig = AlarmRig(tmp_path, "tunda", max_entries=1)
    rig.store.add(datetime(2026, 10, 12, 21, 0), "none", Reminder("Master, telepon Ibu!"))
    rig.start_alarm()
    listen_once(rig)

    answer = rig.assistant.handle_utterance(rig.utterance())

    assert answer == (
        "Alarmnya tidak bisa ditunda: Jadwal sudah penuh, 1 entri. Batalkan jadwal lama dulu."
    )
    assert rig.bell.ringing() is True


@pytest.mark.parametrize(
    ("phrase", "said"),
    [
        ("aku sudah bangun", "Maaf Master, alarmnya belum bisa kumatikan. Coba lagi sebentar."),
        ("tunda", "Maaf Master, alarmnya belum bisa kutunda. Coba lagi sebentar."),
    ],
)
def test_a_ring_whose_file_cannot_be_changed_says_so_and_goes_on(
    tmp_path, monkeypatch, caplog, phrase, said
):
    rig = AlarmRig(tmp_path, phrase)
    rig.start_alarm()
    listen_once(rig)
    unlink = Path.unlink

    def locked(path, missing_ok=False):
        if path == rig.bell.path:
            raise PermissionError(32, "The file is used by another process")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)

    with caplog.at_level(logging.WARNING, logger="waifu.app"):
        answer = rig.assistant.handle_utterance(rig.utterance())

    assert answer == said
    assert rig.speech.texts[-1] == said
    assert rig.bell.ringing() is True
    assert rig.store.entries() == []
    assert "Could not change the ringing alarm by voice" in caplog.text


@pytest.mark.parametrize("command", ["Cyrene, matikan lampu", "matikan musik", "stop"])
def test_a_command_that_only_looks_like_a_stop_goes_on_as_before(tmp_path, command):
    rig = AlarmRig(tmp_path, command)
    rig.start_alarm()
    listen_once(rig)  # the ring opens the follow-up window

    answer = rig.assistant.handle_utterance(rig.utterance())

    assert answer == REPLY
    assert asked(rig) == [command]  # in the follow-up window the gate keeps her name
    assert rig.bell.ringing() is True


def test_without_a_ring_the_stop_phrase_is_an_utterance_like_any_other(tmp_path):
    rig = AlarmRig(tmp_path, "aku sudah bangun", "Cyrene, matikan alarm")

    unaddressed = rig.assistant.handle_utterance(rig.utterance())
    addressed = rig.assistant.handle_utterance(rig.utterance())

    assert unaddressed is None
    assert addressed == REPLY
    assert asked(rig) == ["matikan alarm"]


def test_a_stop_by_another_voice_is_ignored(tmp_path):
    rig = AlarmRig(tmp_path, "aku sudah bangun", owner=(False, 0.2))
    rig.start_alarm()

    assert rig.assistant.handle_utterance(rig.utterance()) is None
    assert rig.bell.ringing() is True


def test_a_stop_leaves_a_question_waiting_for_master_s_yes(tmp_path):
    rig = AlarmRig(tmp_path, "Cyrene, matikan PC", "aku sudah bangun", "iya", brain=AskingBrain)
    rig.assistant.handle_utterance(rig.utterance())
    rig.start_alarm()
    listen_once(rig)

    rig.assistant.handle_utterance(rig.utterance())
    ran_after_the_stop = rig.brain.ran
    rig.assistant.handle_utterance(rig.utterance())  # inside the window the stop line opened

    assert ran_after_the_stop == 0
    assert asked(rig) == ["matikan PC", "iya"]
    assert rig.brain.ran == 1


# --- the voice diagnosis -----------------------------------------------------------------------


def takes(rig: Rig, *, stt: float = 0.0, brain: float = 0.0, tts: float = 0.0) -> None:
    """Make Whisper, the brain, and her voice take these seconds on the rig's clock."""

    def slow(method: Callable, seconds: float) -> Callable:
        def run(*args, **kwargs):
            rig.clock.now += seconds
            return method(*args, **kwargs)

        return run

    rig.stt.transcribe = slow(rig.stt.transcribe, stt)
    rig.brain.respond = slow(rig.brain.respond, brain)
    rig.speech.synthesize = slow(rig.speech.synthesize, tts)


class FailingBrain(FakeBrain):
    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        raise RuntimeError("the brain broke")


def heard_once(*heard: str | Exception, **kwargs) -> UtteranceRecord:
    """The one record of handling one utterance that hears `heard`."""
    log = UtteranceLog(now=lambda: NOW)
    rig = Rig(*heard, diagnosis=log, **kwargs)
    with contextlib.suppress(Exception):
        rig.assistant.handle_utterance(rig.utterance())
    (record,) = log.records()
    return record


def test_an_answered_utterance_is_recorded_with_what_was_heard_and_how_long_each_step_took():
    log = UtteranceLog(now=lambda: NOW)
    rig = Rig("Cyrene, nyalakan lampu kamar.", diagnosis=log)
    takes(rig, stt=0.5, brain=2.0, tts=0.75)

    rig.assistant.handle_utterance(rig.utterance())

    (record,) = log.records()
    assert record == UtteranceRecord.of(
        rig.utterance(),
        NOW,
        ACCEPTED,
        transcript="Cyrene, nyalakan lampu kamar.",
        wake="wake_word",
        score=0.71,
        threshold=0.45,
        stt_s=0.5,
        brain_s=2.0,
        tts_s=0.75,
    )


def test_another_speaker_is_recorded_with_the_score_and_no_transcript():
    record = heard_once("Cyrene, matikan lampu.", owner=(False, 0.12))

    assert (record.outcome, record.score, record.threshold) == (NOT_OWNER, 0.12, 0.45)
    assert (record.transcript, record.stt_s) == (None, None)


def test_an_unusable_verifier_is_recorded_as_not_the_owner_without_a_score():
    record = heard_once("Cyrene, matikan lampu.", owner=SpeakerError("no voiceprint"))

    assert (record.outcome, record.score, record.threshold) == (NOT_OWNER, None, 0.45)


def test_without_speaker_verification_a_record_has_no_score():
    record = heard_once("Cyrene, matikan lampu.", speaker_check=False)

    assert (record.outcome, record.score, record.threshold) == (ACCEPTED, None, None)


def test_a_transcript_without_words_is_recorded_as_empty():
    assert heard_once("").outcome == EMPTY
    assert heard_once("...").outcome == EMPTY


def test_words_without_her_name_are_recorded_with_the_gate_s_reason():
    record = heard_once("nyalakan lampu kamar")

    assert (record.outcome, record.wake, record.transcript) == (
        NO_WAKE,
        "no_wake_word",
        "nyalakan lampu kamar",
    )
    assert record.brain_s is None


@pytest.mark.parametrize("heard", ["Cyrene?", "Cyrene, ulangi"])
def test_what_she_answers_without_the_brain_is_recorded_as_answered(heard):
    log = UtteranceLog(now=lambda: NOW)
    rig = Rig(heard, diagnosis=log)
    takes(rig, tts=0.25)

    rig.assistant.handle_utterance(rig.utterance())

    (record,) = log.records()
    assert (record.outcome, record.brain_s, record.tts_s) == (ACCEPTED, None, 0.25)


def test_a_stop_of_a_ringing_alarm_is_recorded_as_answered(tmp_path):
    log = UtteranceLog(now=lambda: NOW)
    rig = AlarmRig(tmp_path, "aku sudah bangun", diagnosis=log)
    rig.start_alarm()

    rig.assistant.handle_utterance(rig.utterance())

    (record,) = log.records()
    assert (record.outcome, record.wake) == (ACCEPTED, "alarm")


def test_an_utterance_whose_answer_failed_is_still_recorded():
    record = heard_once("Cyrene, nyalakan lampu kamar.", brain=FailingBrain)

    assert (record.outcome, record.brain_s) == (ACCEPTED, None)


def test_follow_up_words_are_recorded_with_the_follow_up_as_the_reason():
    log = UtteranceLog(now=lambda: NOW)
    rig = Rig("Cyrene, nyalakan lampu kamar.", "terus matikan TV", diagnosis=log)

    rig.assistant.handle_utterance(rig.utterance())
    rig.assistant.handle_utterance(rig.utterance())

    assert [record.wake for record in log.records()] == ["followup", "wake_word"]


def test_speak_still_answers_nothing():
    rig = Rig()

    assert rig.assistant.speak("Halo, Master.") is None


# --- "tadi kamu denger apa?" -------------------------------------------------------------------

UNADDRESSED = (
    'Tadi aku dengar "matikan lampu", tapi kamu tidak memanggil namaku, jadi tidak kujawab.'
)


def test_she_tells_what_she_heard_last_and_why_she_did_not_answer_without_the_brain():
    rig = Rig("matikan lampu", "Cyrene, tadi kamu denger apa?", diagnosis=UtteranceLog())

    rig.assistant.handle_utterance(rig.utterance())
    said = rig.assistant.handle_utterance(rig.utterance())

    assert said == UNADDRESSED
    assert rig.speech.texts == [UNADDRESSED]
    assert rig.brain.turns == []
    assert rig.gate.check("terus?").accepted is True  # said like a reply: the window opened


def test_she_tells_another_voice_s_score_against_the_threshold():
    rig = Rig("Cyrene, kamu dengar apa tadi?", diagnosis=UtteranceLog())  # Whisper runs once
    rig.verifier.result = (False, 0.31)
    rig.assistant.handle_utterance(rig.utterance())
    rig.verifier.result = (True, 0.7)

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.speech.texts == [
        "Tadi ada suara, tapi kemiripannya dengan suaramu cuma 0,31, padahal butuh 0,45, "
        "jadi tidak kujawab."
    ]


def test_asking_twice_tells_the_same_utterance_and_the_question_is_still_recorded():
    log = UtteranceLog()
    rig = Rig(
        "matikan lampu", "Cyrene, tadi kamu denger apa?", "Cyrene, tadi denger apa?", diagnosis=log
    )

    for _ in range(3):
        rig.assistant.handle_utterance(rig.utterance())

    assert rig.speech.texts == [UNADDRESSED, UNADDRESSED]
    assert [record.heard_request for record in log.records()] == [True, True, False]


def test_with_nothing_heard_yet_she_says_so():
    rig = Rig("Cyrene, tadi kamu denger apa?", diagnosis=UtteranceLog())

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.speech.texts == ["Belum ada yang kudengar, Master."]


def test_without_a_log_she_still_answers_without_the_brain():
    rig = Rig("Cyrene, tadi kamu denger apa?")

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.speech.texts == ["Belum ada yang kudengar, Master."]
    assert rig.brain.turns == []


def test_her_answer_is_never_what_ulangi_repeats():
    rig = Rig(
        "Cyrene, nyalakan lampu kamar",
        "Cyrene, tadi kamu denger apa?",
        "Cyrene, ulangi",
        diagnosis=UtteranceLog(),
    )

    for _ in range(3):
        rig.assistant.handle_utterance(rig.utterance())

    told = 'Tadi aku dengar "Cyrene, nyalakan lampu kamar", dan sudah kujawab.'
    assert rig.speech.texts == [REPLY, told, REPLY]


def test_what_she_heard_before_guest_mode_switched_is_never_told():
    marker = GuestMarker()
    rig = Rig(
        "matikan lampu",
        "Cyrene, tadi kamu denger apa?",
        guest_marker=marker,
        diagnosis=UtteranceLog(marker=marker),
    )
    rig.assistant.handle_utterance(rig.utterance())
    marker.generation += 1  # guest mode turned on

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.speech.texts == ["Belum ada yang kudengar, Master."]


def test_what_she_heard_in_the_turn_that_switched_guest_mode_is_never_told():
    marker = GuestMarker()  # the brain turns guest mode on while it answers
    rig = Rig(
        "Cyrene, catat pengeluaran dua juta buat obat, terus nyalakan mode tamu",
        "Cyrene, tadi kamu denger apa?",
        brain=lambda reply: SwitchingBrain(reply, marker),
        guest_marker=marker,
        diagnosis=UtteranceLog(marker=marker),
    )
    rig.assistant.handle_utterance(rig.utterance())

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.speech.texts == [REPLY, "Belum ada yang kudengar, Master."]


def test_her_answer_never_reaches_the_info_log(caplog):
    rig = Rig("matikan lampu", "Cyrene, tadi kamu denger apa?", diagnosis=UtteranceLog())
    rig.assistant.handle_utterance(rig.utterance())

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        rig.assistant.handle_utterance(rig.utterance())

    assert "Telling what she heard last" in caplog.text
    assert UNADDRESSED not in caplog.text


# --- "cara baca X itu Y" (kamus cyrene) ---------------------------------------------------------

TAUGHT_ACHERON = "Oke Master. Acheron mulai sekarang kubaca A Ke Ron."
ASKED_ACHERON = "Jadi Acheron kubaca A Ke Ron? Bilang iya kalau benar."
TEACH_ACHERON = "Cyrene, cara baca Acheron itu A Ke Ron."


class RecordingVoice(TextToSpeech):
    """An Indonesian voice for a real SpeechRouter: records each text it is given."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]:
        self.texts.append(text)
        return np.zeros(240, dtype=np.float32), 24000


def handle_all(rig: Rig) -> list[str | None]:
    """Hand the rig every utterance it still hears, in order; what she said to each."""
    return [rig.assistant.handle_utterance(rig.utterance()) for _ in list(rig.stt.heard)]


def test_she_asks_before_keeping_a_reading_and_keeps_nothing_yet(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig(TEACH_ACHERON, lexicon=Lexicon(path))

    said = rig.assistant.handle_utterance(rig.utterance())

    assert said == ASKED_ACHERON
    assert rig.speech.texts == [ASKED_ACHERON]
    assert rig.brain.turns == []
    assert not path.exists()
    assert rig.gate.check("iya").accepted is True  # asked like a reply: the window opened


def test_masters_yes_keeps_the_reading_and_she_says_the_word_and_its_reading(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(TEACH_ACHERON, "iya", lexicon=lexicon)

    said = handle_all(rig)

    assert said == [ASKED_ACHERON, TAUGHT_ACHERON]
    assert rig.speech.texts == [ASKED_ACHERON, TAUGHT_ACHERON]
    assert rig.brain.turns == []
    assert lexicon.read() == {"Acheron": "A Ke Ron"}
    assert rig.gate.check("terus?").accepted is True  # said like a reply: the window opened


@pytest.mark.parametrize(
    "answer",
    [
        "Iya, Master.",
        "ya",
        "oke, catat aja",
        "iya bener",
        "Cyrene, boleh",
        "IYA!",
        "Bener.",
        "bener, Cyrene",
        "iya bener banget",
        "iya, betul sekali",
    ],
)
def test_a_plain_yes_in_other_words_keeps_the_reading(tmp_path, answer):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(TEACH_ACHERON, answer, lexicon=lexicon)

    handle_all(rig)

    assert lexicon.read() == {"Acheron": "A Ke Ron"}
    assert rig.brain.turns == []


@pytest.mark.parametrize(
    "answer",
    [
        "nyalakan lampu kamar",
        "bukan",
        "nggak",
        "iya?",
        "iya, tapi nanti",
        "ya kali",
        "iya, matikan",
        "cara baca HSR itu gimana",
    ],
)
def test_anything_but_a_yes_keeps_nothing_and_is_answered_as_usual(tmp_path, answer):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig(TEACH_ACHERON, answer, "iya", lexicon=Lexicon(path))

    said = handle_all(rig)

    assert said == [ASKED_ACHERON, REPLY, REPLY]
    assert rig.brain.turns == [(answer, NOW), ("iya", NOW)]  # her question is gone
    assert not path.exists()


def test_ulangi_after_her_question_drops_it(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig(TEACH_ACHERON, "ulangi", "iya", lexicon=Lexicon(path))

    said = handle_all(rig)

    assert said == [ASKED_ACHERON, "Belum ada yang bisa kuulangi, Master.", REPLY]
    assert rig.brain.turns == [("iya", NOW)]
    assert not path.exists()


def test_a_new_reading_request_asks_anew_and_a_yes_keeps_only_that_one(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(TEACH_ACHERON, "cara baca Acheron itu A Ke Ron Ron", "iya", lexicon=lexicon)

    said = handle_all(rig)

    assert said[1] == "Jadi Acheron kubaca A Ke Ron Ron? Bilang iya kalau benar."
    assert lexicon.read() == {"Acheron": "A Ke Ron Ron"}


@pytest.mark.parametrize(
    "command",
    [
        "cara baca buku itu penting",
        "cara baca jam itu begini",
        "cara baca peta itu pakai kompas",
        "cara baca buku itu pelan-pelan",
    ],
)
def test_a_sentence_about_reading_never_keeps_a_rule_without_a_yes(tmp_path, command):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig(f"Cyrene, {command}", "nyalakan lampu kamar", lexicon=Lexicon(path))

    handle_all(rig)

    assert not path.exists()
    assert rig.brain.turns[-1] == ("nyalakan lampu kamar", NOW)


def test_a_yes_60_seconds_after_her_question_still_keeps_the_reading(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(TEACH_ACHERON, "Cyrene, iya", lexicon=lexicon)

    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 60
    said = rig.assistant.handle_utterance(rig.utterance())

    assert said == TAUGHT_ACHERON
    assert lexicon.read() == {"Acheron": "A Ke Ron"}


def test_a_yes_after_60_seconds_keeps_nothing_and_goes_to_the_brain(tmp_path, caplog):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig(TEACH_ACHERON, "Cyrene, iya", lexicon=Lexicon(path))

    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 61
    with caplog.at_level(logging.INFO, logger="waifu.app"):
        said = rig.assistant.handle_utterance(rig.utterance())

    assert said == REPLY
    assert rig.brain.turns == [("iya", NOW)]
    assert not path.exists()
    assert "Dropped the reading she asked about" in caplog.text


def test_words_not_addressed_to_her_leave_her_question_waiting(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(TEACH_ACHERON, "eh, lampunya", "Cyrene, iya", lexicon=lexicon)

    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 30  # the follow-up window has closed
    ignored = rig.assistant.handle_utterance(rig.utterance())
    said = rig.assistant.handle_utterance(rig.utterance())

    assert ignored is None
    assert said == TAUGHT_ACHERON
    assert lexicon.read() == {"Acheron": "A Ke Ron"}


def test_a_bare_wake_word_leaves_her_question_waiting_and_never_answers_the_brains(tmp_path):
    # Two yes-questions at once: the brain's shutdown, then her reading. "Cyrene" answers
    # neither, so the "iya" after her acknowledgement is for the question she asked last.
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(
        "Cyrene, matikan PC", TEACH_ACHERON, "Cyrene", "iya", brain=AskingBrain, lexicon=lexicon
    )

    rig.assistant.handle_utterance(rig.utterance())
    asked_reading = rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 15  # the follow-up window has closed
    acknowledged = rig.assistant.handle_utterance(rig.utterance())
    taught = rig.assistant.handle_utterance(rig.utterance())

    assert asked_reading == ASKED_ACHERON
    assert acknowledged == "はい、Master?"
    assert taught == TAUGHT_ACHERON
    assert lexicon.read() == {"Acheron": "A Ke Ron"}
    assert asked(rig) == ["matikan PC"]
    assert rig.brain.ran == 0


def test_a_yes_after_a_bare_wake_word_still_comes_within_60_seconds_of_her_question(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig(TEACH_ACHERON, "Cyrene", "iya", lexicon=Lexicon(path))

    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 55
    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 6  # inside the window her acknowledgement opened, 61 s after her question
    said = rig.assistant.handle_utterance(rig.utterance())

    assert said == REPLY
    assert asked(rig) == ["iya"]
    assert not path.exists()


CODE_NOTICE = "Master, ada 2 kode HSR baru: 60 Stellar Jade. Mau kutukarkan?"


@pytest.mark.parametrize("queue", ["announce", "remind"])
def test_an_announcement_said_after_her_question_drops_it(tmp_path, queue):
    # Master's "iya" may answer the announcement's own question: never her reading's.
    path = tmp_path / "tts_lexicon.json"
    rig = Rig(TEACH_ACHERON, "Cyrene, iya", lexicon=Lexicon(path))

    rig.assistant.handle_utterance(rig.utterance())
    rig.clock.now += 20  # the follow-up window has closed
    getattr(rig.assistant, queue)(CODE_NOTICE)
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))
    said = rig.assistant.handle_utterance(rig.utterance())

    assert rig.speech.texts[:2] == [ASKED_ACHERON, CODE_NOTICE]
    assert said == REPLY
    assert asked(rig) == ["iya"]
    assert not path.exists()


def test_an_announcement_waiting_for_the_follow_up_window_leaves_her_question_waiting(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(TEACH_ACHERON, "iya", lexicon=lexicon)

    rig.assistant.handle_utterance(rig.utterance())
    rig.assistant.announce(CODE_NOTICE, wait_for_followup=True)
    rig.assistant.run(FakeMic(rig.events, [SILENT_FRAME]), FakeSegmenter(rig.events, [None]))
    said = rig.assistant.handle_utterance(rig.utterance())

    assert said == TAUGHT_ACHERON
    assert lexicon.read() == {"Acheron": "A Ke Ron"}


def test_a_phone_message_after_her_question_drops_it(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig(TEACH_ACHERON, "iya", lexicon=Lexicon(path))

    rig.assistant.handle_utterance(rig.utterance())
    rig.assistant.reply_for_phone(PHONE_TEXT)
    said = rig.assistant.handle_utterance(rig.utterance())

    assert said == REPLY
    assert rig.brain.turns == [(PHONE_TEXT, NOW), ("iya", NOW)]
    assert not path.exists()


def test_she_asks_and_keeps_a_reading_in_guest_mode_too(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(
        TEACH_ACHERON, "iya", guest_mode=lambda: True, guest_marker=lambda: 1, lexicon=lexicon
    )

    said = handle_all(rig)

    assert said == [ASKED_ACHERON, TAUGHT_ACHERON]
    assert lexicon.read() == {"Acheron": "A Ke Ron"}


def test_she_names_the_word_taught_as_she_read_it_until_now_then_reads_it_anew(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    voice = RecordingVoice()
    router = SpeechRouter({"id": voice}, default_language="id", lexicon=lexicon.readings)
    rig = Rig(
        TEACH_ACHERON,
        "iya",
        "Cyrene, build Acheron gimana",
        reply="Acheron siap, Master.",
        lexicon=lexicon,
        speech=router,
    )

    handle_all(rig)

    assert voice.texts == [ASKED_ACHERON, TAUGHT_ACHERON, "A Ke Ron siap, Master."]
    assert len(rig.player.played) == 3


def test_a_reading_is_kept_when_her_voice_fails(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(
        TEACH_ACHERON,
        "iya",
        speech_error=TtsError("Suara tidak tersedia."),
        lexicon=lexicon,
    )

    said = handle_all(rig)

    assert said == [ASKED_ACHERON, TAUGHT_ACHERON]
    assert lexicon.read() == {"Acheron": "A Ke Ron"}
    assert rig.player.played == []


def test_she_learns_a_reading_inside_the_follow_up_window_without_her_name(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig("Cyrene", "cara baca Kafka itu Kaf Ka", "iya", lexicon=lexicon)

    handle_all(rig)

    assert lexicon.read() == {"Kafka": "Kaf Ka"}
    assert rig.brain.turns == []


def test_she_learns_a_reading_said_after_masters_form_of_address(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(
        "Cyrene",
        "Sayang, cara baca Kafka itu Kaf Ka.",
        "Iya, Sayang.",
        config=CALLED_SAYANG,
        lexicon=lexicon,
    )

    said = handle_all(rig)

    assert said[1] == "Jadi Kafka kubaca Kaf Ka? Bilang iya kalau benar."
    assert lexicon.read() == {"Kafka": "Kaf Ka"}
    assert rig.brain.turns == []


def test_a_question_about_a_reading_goes_to_the_brain_and_writes_nothing(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig("Cyrene, cara baca HSR itu gimana?", lexicon=Lexicon(path))

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.turns == [("cara baca HSR itu gimana?", NOW)]
    assert not path.exists()


@pytest.mark.parametrize("command", ["Cara baca HSR itu salah.", "cara baca kamu itu salah"])
def test_a_complaint_about_her_reading_goes_to_the_brain_and_writes_nothing(tmp_path, command):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig("Cyrene", command, lexicon=Lexicon(path))

    rig.assistant.handle_utterance(rig.utterance())
    rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.turns == [(command, NOW)]
    assert not path.exists()


def test_without_a_lexicon_a_reading_request_goes_to_the_brain():
    rig = Rig("Cyrene, cara baca Acheron itu A Ke Ron")

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.turns == [("cara baca Acheron itu A Ke Ron", NOW)]


def test_a_reading_that_holds_its_word_is_refused_aloud_without_a_question(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig("Cyrene, cara baca Kafka itu kafka saja", "iya", lexicon=Lexicon(path))

    handle_all(rig)

    assert rig.speech.texts == [
        "Maaf Master, belum bisa kucatat. Bacaannya tidak boleh memuat kata itu sendiri.",
        REPLY,
    ]
    assert rig.brain.turns == [("iya", NOW)]
    assert not path.exists()


def test_a_dictionary_that_cannot_be_written_is_said_and_warned_about(tmp_path, caplog):
    blocked = tmp_path / "tts_lexicon.json"
    blocked.mkdir()  # a folder where the file should be: neither read nor written
    rig = Rig("Cyrene, cara baca Kafka itu Kaf Ka", "iya", lexicon=Lexicon(blocked))

    with caplog.at_level(logging.WARNING, logger="waifu.app"):
        handle_all(rig)

    assert rig.speech.texts[-1] == "Maaf Master, kamusku belum bisa disimpan. Coba lagi sebentar."
    # The question, then the confirmation synthesised but never played, then the failure.
    assert rig.events == ["synthesize", "play", "synthesize", "synthesize", "play"]
    assert "Could not save a reading" in caplog.text


def test_what_she_says_when_taught_is_never_what_ulangi_repeats(tmp_path):
    rig = Rig(
        "Cyrene, nyalakan lampu kamar",
        "Cyrene, cara baca Acheron itu A Ke Ron",
        "Cyrene, iya",
        "Cyrene, ulangi",
        lexicon=Lexicon(tmp_path / "tts_lexicon.json"),
    )

    handle_all(rig)

    assert rig.speech.texts == [REPLY, ASKED_ACHERON, TAUGHT_ACHERON, REPLY]


def test_only_the_heard_line_names_the_word_and_the_reading_taught(tmp_path, caplog):
    rig = Rig("Cyrene, cara baca Acheron itu A Ke Ron", "iya", lexicon=Lexicon(tmp_path / "x.json"))

    with caplog.at_level(logging.DEBUG):
        handle_all(rig)

    messages = [record.getMessage() for record in caplog.records]
    naming = [message for message in messages if "Acheron" in message or "Ke Ron" in message]
    assert naming == ["Heard (id): Cyrene, cara baca Acheron itu A Ke Ron"]
    assert "Asking Master to confirm a reading for a word" in messages
    assert "Learned a reading for a word" in messages


def test_a_dropped_reading_is_logged_without_its_words(tmp_path, caplog):
    rig = Rig(TEACH_ACHERON, "nyalakan lampu kamar", lexicon=Lexicon(tmp_path / "x.json"))

    with caplog.at_level(logging.DEBUG):
        handle_all(rig)

    messages = [record.getMessage() for record in caplog.records]
    naming = [message for message in messages if "Acheron" in message or "Ke Ron" in message]
    assert naming == [f"Heard (id): {TEACH_ACHERON}"]
    assert "Dropped the reading she asked about: no yes from Master in time" in messages


def test_she_forgets_masters_reading_without_the_brain_and_names_the_word(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    lexicon.add("Acheron", "A Ke Ron")
    lexicon.add("Kafka", "Kaf Ka")
    rig = Rig("Cyrene, lupakan cara baca acheron", lexicon=lexicon)

    said = rig.assistant.handle_utterance(rig.utterance())

    assert said == "Oke Master. Cara baca Acheron sudah kulupakan."  # the word as it was kept
    assert lexicon.read() == {"Kafka": "Kaf Ka"}
    assert rig.brain.turns == []
    assert rig.gate.check("terus?").accepted is True  # said like a reply: the window opened


def test_she_forgets_a_reading_asked_for_with_untuk_and_sekarang(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    lexicon.add("Acheron", "A Ke Ron")
    rig = Rig("Cyrene, hapus cara baca untuk Acheron sekarang", lexicon=lexicon)

    said = rig.assistant.handle_utterance(rig.utterance())

    assert said == "Oke Master. Cara baca Acheron sudah kulupakan."
    assert lexicon.read() == {}


def test_she_says_the_forgotten_word_as_she_reads_it_again(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    lexicon.add("HSR", "Honkai Star Rail")
    voice = RecordingVoice()
    router = SpeechRouter({"id": voice}, default_language="id", lexicon=lexicon.readings)
    rig = Rig("Cyrene, hapus cara baca HSR", lexicon=lexicon, speech=router)

    rig.assistant.handle_utterance(rig.utterance())

    assert voice.texts == ["Oke Master. Cara baca Ha Es Er sudah kulupakan."]  # the built-in


def test_a_built_in_reading_is_never_forgotten(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    rig = Rig("Cyrene, lupakan cara baca hsr", lexicon=Lexicon(path))

    said = rig.assistant.handle_utterance(rig.utterance())

    assert said == "Maaf Master, cara baca HSR itu bawaan, jadi tidak bisa kulupakan."
    assert rig.brain.turns == []
    assert not path.exists()


def test_she_says_when_there_is_no_reading_to_forget(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    lexicon.add("Acheron", "A Ke Ron")
    rig = Rig("Cyrene", "Sayang, lupakan cara baca Kafka ya", config=CALLED_SAYANG, lexicon=lexicon)

    said = handle_all(rig)

    assert said[1] == "Aku belum punya cara baca khusus untuk Kafka, Sayang."
    assert lexicon.read() == {"Acheron": "A Ke Ron"}
    assert rig.brain.turns == []


def test_a_dictionary_that_cannot_be_written_when_forgetting_is_said_and_warned_about(
    tmp_path, caplog
):
    blocked = tmp_path / "tts_lexicon.json"
    blocked.mkdir()  # a folder where the file should be: neither read nor written
    rig = Rig("Cyrene, lupakan cara baca Kafka", lexicon=Lexicon(blocked))

    with caplog.at_level(logging.WARNING, logger="waifu.app"):
        said = rig.assistant.handle_utterance(rig.utterance())

    assert said == "Maaf Master, kamusku belum bisa disimpan. Coba lagi sebentar."
    assert "Could not forget a reading" in caplog.text


def test_without_a_lexicon_a_forget_request_goes_to_the_brain():
    rig = Rig("Cyrene, lupakan cara baca Acheron")

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.turns == [("lupakan cara baca Acheron", NOW)]


def test_forgetting_drops_the_reading_she_asked_about(tmp_path):
    lexicon = Lexicon(tmp_path / "tts_lexicon.json")
    rig = Rig(TEACH_ACHERON, "lupakan cara baca Acheron", "iya", lexicon=lexicon)

    said = handle_all(rig)

    assert said[1] == "Aku belum punya cara baca khusus untuk Acheron, Master."
    assert said[2] == REPLY
    assert lexicon.read() == {}


def test_only_the_heard_line_names_the_word_forgotten(tmp_path, caplog):
    lexicon = Lexicon(tmp_path / "x.json")
    lexicon.add("Acheron", "A Ke Ron")
    rig = Rig("Cyrene, lupakan cara baca Acheron", lexicon=lexicon)

    with caplog.at_level(logging.DEBUG):
        rig.assistant.handle_utterance(rig.utterance())

    messages = [record.getMessage() for record in caplog.records]
    naming = [message for message in messages if "Acheron" in message]
    assert naming == ["Heard (id): Cyrene, lupakan cara baca Acheron"]
    assert "Forgot a reading for a word" in messages


def test_the_phone_shows_her_words_as_written_while_its_voice_reads_them_by_the_rules():
    voice = RecordingVoice()
    router = SpeechRouter({"id": voice}, default_language="id", lexicon=lambda: {"Kafka": "Kaf Ka"})
    rig = Rig(reply="Pity HSR-mu 70, Kafka belum.", speech=router)

    reply = rig.assistant.reply_for_phone(PHONE_TEXT)

    assert reply.text == "Pity HSR-mu 70, Kafka belum."
    assert voice.texts == ["Pity Ha Es Er-mu 70, Kaf Ka belum."]


# --- the listen pause (Ctrl+Alt+Q) -------------------------------------------------------------

PAUSE_ENDED = "Aku mendengarkan lagi sekarang, Master."


class PauseRig(AlarmRig):
    """An AlarmRig whose Assistant has a listen pause on a file under `tmp_path`, on the rig's
    clocks; its segmenter records each frame it is fed."""

    def __init__(self, tmp_path, *heard: str | Exception, hours: float = 2.0, **kwargs) -> None:
        self.pause = ListenPause(
            tmp_path / "listen_pause.json",
            hours=hours,
            now=lambda: self.now,
            clock=lambda: self.clock.now,
        )
        super().__init__(tmp_path, *heard, listen_pause=self.pause, **kwargs)

    def listen(self, *outputs: Utterance | None, mic: FakeMic | None = None) -> None:
        """Run the voice loop over one frame per output, as `listen_once` does."""
        frames = [SILENT_FRAME] * max(1, len(outputs))
        self.assistant.run(
            mic or FakeMic(self.events, frames),
            FeedingSegmenter(self.events, list(outputs) or [None]),
        )


class FeedingSegmenter(FakeSegmenter):
    def feed(self, frame: np.ndarray) -> Utterance | None:
        self.events.append("segmenter.feed")
        return super().feed(frame)


class PausingMic(FakeMic):
    """Yields one frame, starts the pause, then yields the rest."""

    def __init__(self, events: list[str], pause: ListenPause, count: int) -> None:
        super().__init__(events, [SILENT_FRAME] * count)
        self.pause = pause

    def frames(self) -> Iterator[np.ndarray]:
        yield SILENT_FRAME
        self.pause.start()
        yield from self._frames
        raise KeyboardInterrupt


def test_while_the_pause_holds_no_frame_reaches_the_segmenter_or_whisper(tmp_path):
    rig = PauseRig(tmp_path, "Cyrene, nyalakan lampu kamar")
    rig.pause.start()

    rig.listen(None, rig.utterance(), None)

    assert "segmenter.feed" not in rig.events
    assert rig.stt.calls == 0 and rig.brain.turns == []


def test_a_pause_that_starts_drops_the_utterance_under_way_once(tmp_path):
    rig = PauseRig(tmp_path)

    rig.listen(mic=PausingMic(rig.events, rig.pause, 3))

    assert rig.events == ["segmenter.feed", "segmenter.reset"]


def test_reminders_and_announcements_are_said_while_the_pause_holds(tmp_path):
    rig = PauseRig(tmp_path)
    rig.pause.start()
    rig.assistant.remind("Master, waktunya minum obat!")
    rig.assistant.announce(ANNOUNCEMENT)

    rig.listen()

    assert rig.speech.texts == [f"Master, waktunya minum obat! {ANNOUNCEMENT}"]


def test_while_an_alarm_rings_she_hears_through_the_pause(tmp_path):
    rig = PauseRig(tmp_path, "aku sudah bangun")
    rig.pause.start()
    rig.start_alarm()

    rig.listen(None, rig.utterance())
    rig.listen(None)

    assert rig.speech.texts == [
        "Master, bangun! Sudah jam 19.30.",
        "Oke Master, alarmnya kumatikan.",
    ]
    assert rig.events.count("segmenter.feed") == 2  # while it rang; the pause holds again after
    assert rig.pause.paused()


def test_her_requests_wait_while_the_pause_holds(tmp_path):
    rig = PauseRig(tmp_path)
    rig.assistant.ask_aloud(GREETING_PROMPT)
    rig.pause.start()

    rig.listen()
    while_paused = asked(rig)
    rig.pause.end()
    rig.listen()

    assert while_paused == []
    assert asked(rig) == [GREETING_PROMPT]


def test_she_is_never_shown_listening_while_the_pause_holds(tmp_path):
    rig = PauseRig(tmp_path, with_avatar=True)
    rig.pause.start()

    rig.assistant.speak("Master, bangun!")

    assert rig.events[-1] == "avatar.state idle"


def test_listen_now_ends_the_pause_and_shows_her_listening(tmp_path, caplog):
    rig = PauseRig(tmp_path, with_avatar=True)
    rig.pause.start()

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        rig.assistant.listen_now()

    assert rig.pause.paused() is False
    assert rig.events == ["avatar.state listening 8"]
    assert "The listen pause ended: Master wants to talk" in caplog.text


def test_listen_now_keeps_a_pause_whose_file_cannot_be_changed(tmp_path, caplog, monkeypatch):
    rig = PauseRig(tmp_path, with_avatar=True)
    rig.pause.start()

    def locked() -> bool:
        raise PermissionError(32, "The file is used by another process")

    monkeypatch.setattr(rig.pause, "end", locked)

    with caplog.at_level(logging.WARNING, logger="waifu.app"):
        rig.assistant.listen_now()

    assert rig.pause.paused() is True
    assert rig.events == ["avatar.state idle"]
    assert "Could not end the listen pause" in caplog.text


def test_a_pause_starting_and_ending_is_logged_once_and_shown_on_the_avatar(tmp_path, caplog):
    rig = PauseRig(tmp_path, with_avatar=True)

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        rig.listen()
        rig.pause.start()
        rig.listen(None, None)
        rig.pause.end()
        rig.listen(None, None)

    assert [event for event in rig.events if event.startswith("avatar.hearing")] == [
        "avatar.hearing off",
        "avatar.hearing on",
    ]
    assert caplog.text.count("Not listening until 21:30") == 1
    assert caplog.text.count("Listening again") == 1
    assert rig.speech.texts == []  # Ctrl+Alt+Q and the button speak for themselves


def test_a_pause_that_ends_by_itself_is_said_once(tmp_path):
    rig = PauseRig(tmp_path, hours=2)
    rig.pause.start()
    rig.listen()

    rig.pass_seconds(2 * 3600)
    rig.listen(None, None)
    rig.listen()

    assert rig.speech.texts == [PAUSE_ENDED]
    assert rig.pause.paused() is False


def test_a_pause_another_process_deleted_after_its_time_is_still_said_to_have_ended(tmp_path):
    rig = PauseRig(tmp_path, hours=2)
    rig.pause.start()
    rig.listen()
    rig.pass_seconds(2 * 3600)
    tombol = ListenPause(rig.pause.path, now=lambda: rig.now, clock=lambda: rig.clock.now)

    assert tombol.paused() is False  # deletes the ended pause's file
    rig.listen()

    assert rig.speech.texts == [PAUSE_ENDED]


def test_the_line_that_the_pause_ended_comes_through_a_focus_block(tmp_path):
    rig = PauseRig(tmp_path, hours=2, quiet=Quiet(on=True))
    rig.pause.start()
    rig.listen()

    rig.pass_seconds(2 * 3600)
    rig.listen()

    assert rig.speech.texts == [PAUSE_ENDED]


def test_once_the_pause_ends_she_hears_again(tmp_path):
    rig = PauseRig(tmp_path, "Cyrene, nyalakan lampu kamar")
    rig.pause.start()
    rig.listen()
    rig.pause.end()

    rig.listen(rig.utterance())

    assert asked(rig) == ["nyalakan lampu kamar"]


# --- repeat_now (Ctrl+Alt+R) -------------------------------------------------------------------


def test_repeat_now_says_her_last_words_again_without_the_brain(caplog):
    rig = Rig("Cyrene, nyalakan lampu kamar", reply=LAMP_ON, with_avatar=True)
    rig.assistant.handle_utterance(rig.utterance())

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        rig.assistant.repeat_now()

    said = "Lampu kamar sudah menyala, Master."
    assert rig.speech.texts == [said, said]
    assert asked(rig) == ["nyalakan lampu kamar"]
    assert rig.events.count("avatar.speak happy") == 2
    assert "Repeating what she said last" in caplog.text


def test_repeat_now_with_nothing_said_yet_says_so():
    rig = Rig()

    rig.assistant.repeat_now()

    assert rig.speech.texts == [NOTHING_TO_REPEAT]


def test_repeat_now_while_she_is_busy_is_ignored(caplog):
    rig = Rig("Cyrene, nyalakan lampu kamar")
    rig.assistant.handle_utterance(rig.utterance())
    segmenter = FakeSegmenter(rig.events, [])
    segmenter.busy = True  # Master is speaking
    rig.assistant.set_mic(None, segmenter)

    with caplog.at_level(logging.INFO, logger="waifu.app"):
        rig.assistant.repeat_now()

    assert rig.speech.texts == [REPLY]
    assert "Not repeating her last words: she is busy" in caplog.text


def test_repeat_now_never_repeats_what_she_said_before_guest_mode_switched():
    marker = GuestMarker()
    rig = Rig("Cyrene, nyalakan lampu kamar", guest_marker=marker)
    rig.assistant.handle_utterance(rig.utterance())
    marker.generation += 1

    rig.assistant.repeat_now()

    assert rig.speech.texts == [REPLY, NOTHING_TO_REPEAT]


def test_she_is_busy_while_repeat_now_speaks():
    rig = Rig("Cyrene, nyalakan lampu kamar")
    rig.assistant.handle_utterance(rig.utterance())
    while_playing: list[bool] = []
    rig.player.on_play = lambda: while_playing.append(rig.assistant.busy)

    rig.assistant.repeat_now()

    assert while_playing == [True]


# --- respons instan: a filler while a slow reply is thought up -----------------------------------

SLOW_QUESTION = "Cyrene, besok hujan nggak?"
HMM = "Hmm, bentar ya…"
CHECKING = "Sebentar, kucek dulu ya."
NOD_RATE = 16000  # the clips of her fillers, apart from the 24 kHz of her replies


class FakeNodVoice:
    """Her voice for the fillers: a clip as long as each text, at NOD_RATE."""

    degraded = False

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        return np.full(len(text), 0.25, dtype=np.float32), NOD_RATE


def nod_bank(*lines: str) -> NodBank:
    """A bank of `lines` (HMM alone by default), made in her voice."""
    bank = NodBank(FakeNodVoice(), lines=lines or (HMM,), rng=random.Random(1))
    bank.prepare()
    return bank


class PonderingBrain(FakeBrain):
    """Runs `while_thinking` inside each turn: the time a slow reply takes."""

    def __init__(self, reply: str = REPLY) -> None:
        super().__init__(reply)
        self.while_thinking: Callable[[], None] = lambda: None

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.while_thinking()
        return super().respond(user_text, now=now)


def slow_rig(*heard: str, nods: NodBank | None = None, **kwargs) -> Rig:
    """A rig whose brain takes longer than nod.after_ms on every turn: its filler timer fires."""
    rig = Rig(*(heard or (SLOW_QUESTION,)), brain=PonderingBrain, nods=nods or nod_bank(), **kwargs)
    rig.brain.while_thinking = rig.timers.fire
    return rig


def fillers_said(rig: Rig) -> list[int]:
    """The length of each filler the avatar mouthed, in order (a filler is as long as its text)."""
    return [length for length, rate, _ in rig.avatar.spoken if rate == NOD_RATE]


def test_a_reply_not_ready_after_nod_after_ms_comes_after_a_filler():
    rig = slow_rig(with_avatar=True)
    rig.assistant.set_mic(FakeMic(rig.events, []), FakeSegmenter(rig.events, []))

    reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply == REPLY
    assert rig.player.played == [NOD_RATE, 24000]
    assert rig.speech.texts == [REPLY]  # the filler was ready in memory
    assert rig.events == [
        "avatar.state thinking",
        "mic.pause",
        "avatar.state speaking",
        "avatar.speak neutral",
        "play",
        "mic.resume",
        "segmenter.reset",
        "avatar.state thinking",
        "synthesize",  # her reply's voice, made while the filler played
        "mic.pause",
        "avatar.state speaking",
        "avatar.speak neutral",
        "play",
        "mic.resume",
        "segmenter.reset",
        "avatar.state listening 8",
    ]


def test_the_filler_timer_waits_1200_ms_by_default_and_is_cancelled_by_the_reply():
    rig = Rig("Cyrene, nyalakan lampu kamar", nods=nod_bank())

    rig.assistant.handle_utterance(rig.utterance())

    [timer] = rig.timers.made
    assert (timer.interval, timer.started, timer.cancelled) == (1.2, True, True)


def test_the_filler_timer_waits_the_configured_nod_after_ms():
    config = AppConfig.model_validate({"nod": {"after_ms": 2500}})
    rig = Rig("Cyrene, nyalakan lampu kamar", config=config, nods=nod_bank())

    rig.assistant.handle_utterance(rig.utterance())

    assert [timer.interval for timer in rig.timers.made] == [2.5]


def test_by_default_her_filler_comes_from_a_daemon_timer_after_nod_after_ms():
    # A timer that kept the app alive would hold a Ctrl+C back until the filler ends.
    config = AppConfig.model_validate({"nod": {"after_ms": 600}})
    rig = Rig(SLOW_QUESTION, brain=PonderingBrain, config=config, nods=nod_bank(), real_timers=True)
    nodded = threading.Event()
    nodding_threads: list[threading.Thread] = []

    def note_the_filler() -> None:
        if not rig.player.played:  # the first clip is the filler
            nodding_threads.append(threading.current_thread())
            nodded.set()

    rig.player.on_play = note_the_filler
    rig.brain.while_thinking = lambda: nodded.wait(WAIT_S)

    rig.assistant.handle_utterance(rig.utterance())

    [timer] = nodding_threads
    assert isinstance(timer, threading.Timer)
    assert (timer.interval, timer.daemon) == (0.6, True)
    assert rig.player.played == [NOD_RATE, 24000]


def test_by_default_a_reply_ready_in_time_cancels_her_filler_timer_and_its_thread_ends():
    rig = Rig(SLOW_QUESTION, brain=PonderingBrain, nods=nod_bank(), real_timers=True)
    before = set(threading.enumerate())
    started: list[threading.Thread] = []
    rig.brain.while_thinking = lambda: started.extend(set(threading.enumerate()) - before)

    rig.assistant.handle_utterance(rig.utterance())
    [timer] = started
    timer.join(WAIT_S)

    assert isinstance(timer, threading.Timer)
    assert (timer.interval, timer.daemon) == (1.2, True)
    assert not timer.is_alive()
    assert rig.player.played == [24000]


def test_a_reply_ready_in_time_has_no_filler_even_from_a_timer_that_fires_late():
    rig = Rig("Cyrene, nyalakan lampu kamar", nods=nod_bank())
    rig.assistant.handle_utterance(rig.utterance())

    rig.timers.fire()  # Timer.cancel cannot stop a timer that has just fired

    assert rig.player.played == [24000]


def test_a_turn_says_one_filler_at_most():
    rig = slow_rig(nods=nod_bank(HMM, CHECKING))

    def fire_twice() -> None:
        rig.timers.fire()
        rig.timers.fire()

    rig.brain.while_thinking = fire_twice

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.player.played == [NOD_RATE, 24000]


def test_her_reply_waits_until_the_filler_has_been_said():
    rig = slow_rig()
    playing, release = hold_first_play(rig)
    nodding = threading.Thread(target=rig.timers.fire)  # the timer's own thread

    def think_past_the_filler() -> None:
        nodding.start()
        playing.wait(WAIT_S)

    rig.brain.while_thinking = think_past_the_filler
    turn = threading.Thread(target=rig.assistant.handle_utterance, args=(rig.utterance(),))
    turn.start()
    assert playing.wait(WAIT_S)
    turn.join(0.1)
    waiting, events = turn.is_alive(), list(rig.events)
    release.set()
    nodding.join(WAIT_S)
    turn.join(WAIT_S)

    assert waiting
    assert events == ["play", "synthesize"]  # the reply's voice was made meanwhile
    assert rig.player.played == [NOD_RATE, 24000]


def test_a_filler_still_waiting_for_her_voice_once_the_reply_is_ready_is_never_said():
    rig = slow_rig()
    playing, release = hold_first_play(rig)
    reaction = threading.Thread(target=rig.assistant.speak_reaction, args=(PAT_LINE, "happy"))
    reaction.start()
    assert playing.wait(WAIT_S)
    nodding = threading.Thread(target=rig.timers.fire)  # its time passed while a reaction spoke
    rig.brain.while_thinking = nodding.start
    turn = threading.Thread(target=rig.assistant.handle_utterance, args=(rig.utterance(),))
    turn.start()
    turn.join(0.1)  # the reply is ready and waits for her voice as well
    release.set()
    for thread in (reaction, nodding, turn):
        thread.join(WAIT_S)

    assert rig.speech.texts == [PAT_LINE, REPLY]
    assert rig.player.played == [24000, 24000]


def test_two_slow_turns_never_say_the_same_filler():
    rig = slow_rig(SLOW_QUESTION, SLOW_QUESTION, nods=nod_bank(HMM, CHECKING), with_avatar=True)

    handle_all(rig)

    assert sorted(fillers_said(rig)) == sorted([len(HMM), len(CHECKING)])


def test_with_only_the_last_filler_ready_a_slow_turn_has_none():
    rig = slow_rig(SLOW_QUESTION, SLOW_QUESTION)

    handle_all(rig)

    assert rig.player.played == [NOD_RATE, 24000, 24000]


def test_without_a_ready_filler_a_slow_turn_is_answered_as_before():
    rig = slow_rig(nods=NodBank(FakeNodVoice(), lines=(HMM,)))  # not prepared yet

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.player.played == [24000]


def test_without_fillers_no_timer_is_set():
    rig = Rig("Cyrene, nyalakan lampu kamar")

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.timers.made == []


def test_ulangi_repeats_her_reply_never_the_filler():
    rig = slow_rig(SLOW_QUESTION, "ulangi")

    said = handle_all(rig)

    assert said == [REPLY, REPLY]
    assert rig.speech.texts == [REPLY, REPLY]


def test_cutting_the_filler_short_still_lets_the_reply_come_and_holds_announcements():
    rig = slow_rig()
    rig.player.on_play = lambda: rig.assistant.interrupt() if not rig.player.played else None

    reply = rig.assistant.handle_utterance(rig.utterance())
    rig.assistant.announce(ANNOUNCEMENT)
    listen_once(rig)

    assert reply == REPLY
    assert rig.player.played == [NOD_RATE, 24000]
    assert rig.speech.texts == [REPLY]  # Master cut her off to talk: the announcement waits


def test_a_filler_that_cannot_be_played_is_logged_and_the_reply_still_comes(caplog):
    rig = slow_rig(with_avatar=True)

    def broken_once() -> None:
        if rig.events.count("play") == 1:
            raise RuntimeError("PortAudio error")

    rig.player.on_play = broken_once

    with caplog.at_level(logging.ERROR, logger="waifu.app"):
        reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply == REPLY
    assert "Could not say a filler" in caplog.text
    assert rig.events[rig.events.index("play") + 1] == "avatar.state thinking"
    assert rig.player.played == [24000]


def test_her_reply_after_a_filler_is_timed_without_the_filler():
    log = UtteranceLog(now=lambda: NOW)
    rig = slow_rig(diagnosis=log)
    takes(rig, brain=2.0, tts=0.25)

    rig.assistant.handle_utterance(rig.utterance())

    (record,) = log.records()
    assert (record.brain_s, record.tts_s) == (2.0, 0.25)


class OnceFailingSpeech(FakeSpeech):
    """Fails its first synthesis, then voices every text."""

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        failing, self.error = self.error, None
        if failing is not None:
            self.texts.append(text)
            raise failing
        return super().synthesize(text)


def test_a_reply_whose_voice_could_not_be_made_during_the_filler_is_voiced_again():
    speech = OnceFailingSpeech([], TtsError("edge mati"))
    rig = slow_rig(speech=speech)

    reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply == REPLY
    assert speech.texts == [REPLY, REPLY]
    assert rig.player.played == [NOD_RATE, 24000]


def test_a_filler_is_said_while_quiet_holds_since_master_is_talking():
    rig = slow_rig(quiet=lambda: True, game_holding=lambda: True)

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.player.played == [NOD_RATE, 24000]


@pytest.mark.parametrize(
    "heard",
    ["Cyrene?", "Cyrene, ulangi", "Cyrene, tadi kamu denger apa?", "nyalakan lampu kamar"],
    ids=["wake-word", "ulangi", "heard", "not-for-her"],
)
def test_words_answered_without_the_brain_set_no_filler_timer(heard):
    rig = slow_rig(heard)

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.brain.turns == []
    assert rig.timers.made == []


def test_a_reading_asked_confirmed_and_forgotten_sets_no_filler_timer(tmp_path):
    rig = slow_rig(
        TEACH_ACHERON,
        "iya",
        "Cyrene, lupakan cara baca Acheron",
        lexicon=Lexicon(tmp_path / "tts_lexicon.json"),
    )

    handle_all(rig)

    assert rig.brain.turns == []
    assert rig.timers.made == []


def test_the_words_that_stop_a_ringing_alarm_set_no_filler_timer(tmp_path):
    rig = AlarmRig(tmp_path, "aku sudah bangun", brain=PonderingBrain, nods=nod_bank())
    rig.start_alarm()

    rig.assistant.handle_utterance(rig.utterance())

    assert rig.bell.ringing() is False
    assert rig.timers.made == []


def test_a_request_she_answers_by_herself_has_no_filler():
    rig = Rig(nods=nod_bank())
    rig.assistant.ask_aloud(GREETING_PROMPT)

    listen_once(rig)

    assert [text for text, _ in rig.brain.turns] == [GREETING_PROMPT]
    assert rig.timers.made == []


def test_a_phone_message_and_the_text_chat_have_no_filler():
    rig = Rig(nods=nod_bank())

    rig.assistant.reply_for_phone(PHONE_TEXT)
    rig.assistant.handle_text(PHONE_TEXT)

    assert len(rig.brain.turns) == 2
    assert rig.timers.made == []
    assert rig.player.played == []
