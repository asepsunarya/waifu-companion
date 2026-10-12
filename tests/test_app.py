import logging
import threading
from collections.abc import Callable, Iterator
from datetime import datetime

import numpy as np
import pytest

from waifu.app import (
    ACKNOWLEDGEMENT,
    TAG_ONLY_DONE,
    TAG_ONLY_FAILED,
    TAG_ONLY_NO_ANSWER,
    Assistant,
)
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
        self.on_play: Callable[[], None] = lambda: None  # runs while the audio "plays"

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        self.events.append("play")
        self.on_play()
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
        brain: type[FakeBrain] = FakeBrain,
        speech_error: Exception | None = None,
        player_error: Exception | None = None,
        config: AppConfig | None = None,
        with_verifier: bool = True,
        speaker_check: bool = True,
        with_avatar: bool = False,
        avatar_error: Exception | None = None,
    ) -> None:
        config = config or AppConfig()
        self.events: list[str] = []
        self.clock = FakeClock()
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


TAGGED_REPLY = "[senang] Siap, Master! Lampu kamar sudah menyala."


def test_the_emotion_tag_is_never_spoken_nor_returned():
    rig = Rig("Cyrene, nyalakan lampu kamar", reply=TAGGED_REPLY)

    reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply == REPLY
    assert rig.speech.texts == [REPLY]


def test_a_reply_that_is_only_a_tag_after_a_tool_call_is_still_answered_aloud():
    rig = Rig("Cyrene, nyalakan lampu kamar", reply="[senang]", with_avatar=True)

    reply = rig.assistant.handle_utterance(rig.utterance())

    assert reply == TAG_ONLY_DONE == "Sudah, Master."
    assert rig.speech.texts == [TAG_ONLY_DONE]
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
    [(FailedToolBrain, TAG_ONLY_FAILED), (ToollessBrain, TAG_ONLY_NO_ANSWER)],
    ids=["tool-failed", "no-tool"],
)
def test_a_tag_only_reply_says_what_the_tools_did(brain, expected):
    rig = Rig(reply=" [ sedih ] ", brain=brain)

    reply = rig.assistant.handle_text("nyalakan AC")

    assert reply.text == expected
    assert expected.startswith("Maaf Master")


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
