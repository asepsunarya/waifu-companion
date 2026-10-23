import base64
import http.client
import io
import json
import logging
import struct
import threading
import wave
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from waifu.app import GAME_HELD, Assistant, PhoneAnswers
from waifu.avatar.lipsync import viseme_track
from waifu.brain.attachments import CameraTurn, TurnImages
from waifu.brain.base import Brain, BrainReply, ToolCallRecord, ToolImage
from waifu.brain.claude import ClaudeBrain
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
from waifu.pc_health import Drive, GpuStatus, PcSnapshot
from waifu.pc_watch import PcWatcher
from waifu.phone.auth import PhoneAuth
from waifu.phone.server import PhoneBackend, PhoneReply, PhoneServer
from waifu.screen import ScreenImage, encode_png
from waifu.voice.base import SpeechToText, Transcript, Utterance
from waifu.voice.speaker import SpeakerError
from waifu.voice.stt import SttError
from waifu.voice.tts import TtsError

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
    ) -> None:
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
        self.assistant = Assistant(
            config,
            brain=self.brain,
            stt=self.stt,
            speech=self.speech,
            player=self.player,
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
