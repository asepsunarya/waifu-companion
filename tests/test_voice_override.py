"""The mic and the voice settings picked on the phone's Suara page, kept in their own small file
(never config.yaml), under tmp_path only."""

import json
import logging
import math
import os
from datetime import datetime

import pytest

from waifu.voice_override import (
    VoiceOverride,
    VoiceOverrideStore,
    checked_tuning,
)

NOW = datetime(2026, 10, 24, 23, 0, 0)
CORRUPT_NAME = "voice_override.json.corrupt-20261024-230000"


def make_store(path) -> VoiceOverrideStore:
    return VoiceOverrideStore(path, now=lambda: NOW)


def stored(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_missing_file_overrides_nothing_and_stays_missing(tmp_path):
    path = tmp_path / "voice_override.json"

    assert make_store(path).read() == VoiceOverride()
    assert not path.exists()


def test_a_change_is_written_and_read_back_by_another_process(tmp_path):
    path = tmp_path / "voice_override.json"

    make_store(path).update(lambda found: VoiceOverride(input_device="Headset (Polaris-BT), MME"))

    assert stored(path) == {"version": 1, "input_device": "Headset (Polaris-BT), MME"}
    assert make_store(path).read() == VoiceOverride(input_device="Headset (Polaris-BT), MME")


def test_a_change_keeps_what_it_does_not_touch(tmp_path):
    path = tmp_path / "voice_override.json"
    store = make_store(path)
    store.update(lambda found: VoiceOverride(input_device="Headset (Polaris-BT), MME"))

    changed = store.update(
        lambda found: VoiceOverride(
            found.input_device, speaker_threshold=0.5, vad_threshold_db=-50.0, vad_margin_db=10.0
        )
    )

    assert changed == VoiceOverride("Headset (Polaris-BT), MME", 0.5, -50.0, 10.0)
    assert stored(path) == {
        "version": 1,
        "input_device": "Headset (Polaris-BT), MME",
        "speaker_threshold": 0.5,
        "vad_threshold_db": -50.0,
        "vad_margin_db": 10.0,
    }


def test_whole_numbers_are_read_as_settings(tmp_path):
    path = tmp_path / "voice_override.json"
    path.write_text('{"version": 1, "vad_threshold_db": -50, "vad_margin_db": 10}', "utf-8")

    found = make_store(path).read()

    assert (found.vad_threshold_db, found.vad_margin_db) == (-50.0, 10.0)


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "voice_override.json"
    path.write_text('{"version": 1, "speaker_threshold": 0.4}', encoding="utf-8-sig")

    assert make_store(path).read() == VoiceOverride(speaker_threshold=0.4)


def test_whether_it_tunes_anything():
    assert not VoiceOverride(input_device="Headset (Polaris-BT), MME").tunes
    assert VoiceOverride(vad_margin_db=10.0).tunes


CORRUPT_FILES = {
    "not JSON": "{not json",
    "a list": "[]",
    "no version": '{"input_device": "Headset"}',
    "another version": '{"version": 2}',
    "an extra key": '{"version": 1, "volume": 3}',
    "an empty mic": '{"version": 1, "input_device": ""}',
    "a numbered mic": '{"version": 1, "input_device": 3}',
    "a threshold as text": '{"version": 1, "speaker_threshold": "0.5"}',
    "a boolean threshold": '{"version": 1, "speaker_threshold": true}',
    "a threshold too low": '{"version": 1, "speaker_threshold": 0.29}',
    "a threshold too high": '{"version": 1, "speaker_threshold": 0.71}',
    "a gate too low": '{"version": 1, "vad_threshold_db": -71}',
    "a gate too high": '{"version": 1, "vad_threshold_db": -24}',
    "a margin too small": '{"version": 1, "vad_margin_db": 2}',
    "a margin too large": '{"version": 1, "vad_margin_db": 31}',
    "not a number": '{"version": 1, "vad_margin_db": NaN}',
}


@pytest.mark.parametrize("content", CORRUPT_FILES.values(), ids=CORRUPT_FILES.keys())
def test_a_file_that_is_not_an_override_is_moved_aside_and_read_as_none(tmp_path, caplog, content):
    path = tmp_path / "voice_override.json"
    path.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="waifu.voice_override"):
        assert make_store(path).read() == VoiceOverride()

    assert (tmp_path / CORRUPT_NAME).read_text(encoding="utf-8") == content
    assert not path.exists()
    assert CORRUPT_NAME in caplog.text


def test_a_broken_file_moved_aside_twice_in_one_second_keeps_both(tmp_path):
    path = tmp_path / "voice_override.json"
    store = make_store(path)
    for content in ("first", "second"):
        path.write_text(content, encoding="utf-8")
        store.read()

    assert sorted(copy.name for copy in tmp_path.glob("voice_override.json.corrupt-*")) == [
        CORRUPT_NAME,
        f"{CORRUPT_NAME}-2",
    ]


def test_a_broken_file_that_cannot_be_moved_aside_raises_and_stays(tmp_path, monkeypatch):
    path = tmp_path / "voice_override.json"
    path.write_text("broken", encoding="utf-8")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(os, "rename", failing_rename)
    with pytest.raises(OSError, match="file in use"):
        make_store(path).read()

    assert path.read_text(encoding="utf-8") == "broken"


def test_a_tuning_within_its_bounds_is_taken():
    assert checked_tuning(speaker_threshold=0.3, vad_threshold_db=-70, vad_margin_db=30) == (
        0.3,
        -70.0,
        30.0,
    )
    assert checked_tuning(vad_margin_db=3) == (None, None, 3.0)


@pytest.mark.parametrize(
    ("tuning", "problem"),
    [
        ({"speaker_threshold": 0.71}, "0,30 sampai 0,70"),
        ({"speaker_threshold": True}, "0,30 sampai 0,70"),
        ({"vad_threshold_db": -20}, "-70 sampai -25 dB"),
        ({"vad_threshold_db": math.nan}, "-70 sampai -25 dB"),
        ({"vad_margin_db": math.inf}, "3 sampai 30 dB"),
        ({"vad_margin_db": "10"}, "3 sampai 30 dB"),
    ],
)
def test_a_tuning_out_of_its_bounds_is_refused_in_indonesian(tuning, problem):
    with pytest.raises(ValueError, match=problem):
        checked_tuning(**tuning)
