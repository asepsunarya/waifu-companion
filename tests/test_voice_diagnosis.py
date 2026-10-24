"""What Cyrene heard lately (SPEC, diagnosa suara (v1.47)): records in memory, never on disk."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from waifu.voice.base import Utterance
from waifu.voice.diagnosis import (
    ACCEPTED,
    NO_WAKE,
    NOT_OWNER,
    TOO_SHORT,
    UtteranceLog,
    UtteranceRecord,
)

RATE = 16000
NOW = datetime(2026, 10, 24, 19, 30, 5)


def segment(seconds: float, amplitude: float) -> Utterance:
    return Utterance(np.full(round(seconds * RATE), amplitude, dtype=np.float32), RATE, 0.0)


def record(outcome: str = ACCEPTED, transcript: str | None = "nyalakan lampu", **known):
    return UtteranceRecord(NOW, 1.0, -20.0, -26.0, outcome, transcript=transcript, **known)


def test_a_record_measures_its_segment():
    found = UtteranceRecord.of(segment(1.5, 0.1), NOW, NO_WAKE, transcript="matikan lampu")

    assert found.at == NOW
    assert found.length_s == pytest.approx(1.5)
    assert found.peak_db == pytest.approx(-20.0, abs=0.01)
    assert found.rms_db == pytest.approx(-20.0, abs=0.01)
    assert (found.outcome, found.transcript) == (NO_WAKE, "matikan lampu")
    assert (found.score, found.stt_s, found.brain_s, found.tts_s) == (None, None, None, None)


def test_the_log_keeps_the_last_fifty_newest_first():
    log = UtteranceLog()
    for index in range(55):
        log.add(record(transcript=f"kalimat {index}"))

    records = log.records()

    assert isinstance(records, tuple)
    assert len(records) == 50
    assert [found.transcript for found in records[:2]] == ["kalimat 54", "kalimat 53"]
    assert records[-1].transcript == "kalimat 5"


def test_a_too_short_segment_is_recorded_at_the_time_it_was_dropped():
    log = UtteranceLog(now=lambda: NOW + timedelta(seconds=3))

    log.add_too_short(segment(0.2, 0.3))

    (found,) = log.records()
    assert found.outcome == TOO_SHORT
    assert found.at == NOW + timedelta(seconds=3)
    assert found.length_s == pytest.approx(0.2)
    assert found.transcript is None


def test_what_she_heard_last_skips_too_short_segments_and_the_question_itself():
    log = UtteranceLog()
    log.add(record(NOT_OWNER, None, score=0.31, threshold=0.45))
    log.add(record(NO_WAKE, "matikan lampu"))
    log.add_too_short(segment(0.2, 0.3))
    log.add(record(transcript="tadi kamu denger apa", heard_request=True))

    assert log.last_heard().transcript == "matikan lampu"


def test_nothing_heard_yet_is_none():
    log = UtteranceLog()
    log.add_too_short(segment(0.2, 0.3))

    assert log.last_heard() is None


class Generation:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        return self.value


def test_what_was_heard_before_guest_mode_switched_is_forgotten():
    generation = Generation()
    log = UtteranceLog(marker=generation)
    log.add(record(transcript="berapa saldoku"))

    generation.value += 1  # guest mode turned on
    assert log.records() == ()
    assert log.last_heard() is None

    log.add(record(transcript="halo semuanya"))
    assert [found.transcript for found in log.records()] == ["halo semuanya"]


def test_a_record_keeps_the_generation_it_was_heard_in_though_added_after_a_switch():
    generation = Generation()
    log = UtteranceLog(marker=generation)
    heard_in = log.stamp()  # as the utterance is heard
    generation.value += 1  # its own turn turned guest mode on

    log.add(record(transcript="nyalakan mode tamu"), stamp=heard_in)

    assert log.records() == ()
    assert log.last_heard() is None
