import logging
from collections.abc import Iterator
from datetime import datetime

import numpy as np
import pytest

from waifu.app import ACKNOWLEDGEMENT, Assistant
from waifu.brain.base import Brain, BrainReply, ToolCallRecord
from waifu.config import AppConfig, SpeakerConfig
from waifu.gate import WakeGate
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
    def __init__(self) -> None:
        self.turns: list[tuple[str, datetime | None]] = []

    def respond(self, user_text: str, *, now: datetime | None = None) -> BrainReply:
        self.turns.append((user_text, now))
        call = ToolCallRecord(
            "control_device", {"device": "lampu_kamar", "command": "on"}, '{"ok": true}', False
        )
        return BrainReply(REPLY, [call])

    def reset(self) -> None:
        self.turns.clear()


class FakeStt(SpeechToText):
    """Returns the queued transcripts in order (or raises a queued exception)."""

    def __init__(self, *heard: str | Exception) -> None:
        self.heard = list(heard)
        self.calls = 0

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        self.calls += 1
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
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.texts: list[str] = []

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        self.events.append("synthesize")
        self.texts.append(text)
        if self.error is not None:
            raise self.error
        return np.zeros(2400, dtype=np.float32), 24000


class FakePlayer:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.played: list[int] = []

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        self.events.append("play")
        if self.error is not None:
            raise self.error
        self.played.append(sample_rate)


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
    """Returns the queued `feed` results in order."""

    def __init__(self, events: list[str], outputs: list[Utterance | None]) -> None:
        self.events = events
        self._outputs = list(outputs)

    def feed(self, frame: np.ndarray) -> Utterance | None:
        return self._outputs.pop(0)

    def reset(self) -> None:
        self.events.append("segmenter.reset")


class Rig:
    """An Assistant wired to fakes that share one event log and one clock."""

    def __init__(
        self,
        *heard: str | Exception,
        owner: tuple[bool, float] | Exception = (True, 0.71),
        speech_error: Exception | None = None,
        player_error: Exception | None = None,
        config: AppConfig | None = None,
        with_verifier: bool = True,
        speaker_check: bool = True,
    ) -> None:
        config = config or AppConfig()
        self.events: list[str] = []
        self.clock = FakeClock()
        self.brain = FakeBrain()
        self.stt = FakeStt(*heard)
        self.verifier = FakeVerifier(owner)
        self.speech = FakeSpeech(self.events, speech_error)
        self.player = FakePlayer(self.events, player_error)
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
            now=lambda: NOW,
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

    assert acknowledgement == ACKNOWLEDGEMENT == "はい、Master?"
    assert rig.speech.texts[0] == ACKNOWLEDGEMENT
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
