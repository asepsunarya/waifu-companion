import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pytest

from waifu.config import VoiceNightConfig
from waifu.voice_level import VoiceChange, VoiceLevel, VoiceState

NOW = datetime(2026, 10, 24, 23, 0)
CORRUPT_NAME = "voice.json.corrupt-20261024-230000"


class Clock:
    """The PC's local clock (naive), moved by the test."""

    def __init__(self, moment: datetime = NOW) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


def make_level(
    path: Path, clock: Clock | None = None, night: VoiceNightConfig | None = None
) -> VoiceLevel:
    return VoiceLevel(path, night or VoiceNightConfig(), now=clock or Clock())


def stored(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(path: Path, level: int = 100, night: bool = False) -> None:
    path.write_text(json.dumps({"version": 1, "level": level, "night": night}), encoding="utf-8")


# --- the file ----------------------------------------------------------------------------------


def test_a_missing_file_is_the_normal_level_without_whisper_mode_and_stays_missing(tmp_path):
    path = tmp_path / "voice.json"
    level = make_level(path)

    assert level.state() == VoiceState(level=100, night=False)
    assert level.level_now() == 100
    assert not path.exists()


def test_a_level_set_is_written_and_read_back_by_another_process(tmp_path):
    path = tmp_path / "data" / "voice.json"

    change = make_level(path).set_level(80)

    assert change == VoiceChange(VoiceState(100, False), VoiceState(80, False))
    assert change.changed
    assert stored(path) == {"version": 1, "level": 80, "night": False}
    assert make_level(path).state() == VoiceState(80, False)


def test_setting_the_level_it_has_writes_nothing(tmp_path):
    path = tmp_path / "voice.json"

    change = make_level(path).set_level(100)

    assert not change.changed
    assert not path.exists()


@pytest.mark.parametrize("bad", [9, 151, 0, -20, True, 80.0, "80"])
def test_a_level_outside_ten_to_one_hundred_fifty_is_refused(tmp_path, bad):
    path = tmp_path / "voice.json"

    with pytest.raises(ValueError, match="10"):
        make_level(path).set_level(bad)

    assert not path.exists()


def test_louder_goes_up_twenty_at_a_time_and_stops_at_one_hundred_fifty(tmp_path):
    level = make_level(tmp_path / "voice.json")

    levels = [level.change("louder").after.level for _ in range(4)]

    assert levels == [120, 140, 150, 150]
    assert not level.change("louder").changed


def test_softer_goes_down_twenty_at_a_time_and_stops_at_ten(tmp_path):
    level = make_level(tmp_path / "voice.json")

    levels = [level.change("softer").after.level for _ in range(6)]

    assert levels == [80, 60, 40, 20, 10, 10]
    assert not level.change("softer").changed


def test_a_step_from_a_level_off_the_grid_keeps_its_offset(tmp_path):
    level = make_level(tmp_path / "voice.json")
    level.set_level(75)

    assert level.change("softer").after.level == 55
    assert level.change("louder").after.level == 75


def test_another_direction_is_refused(tmp_path):
    with pytest.raises(ValueError, match="louder"):
        make_level(tmp_path / "voice.json").change("up")


def test_whisper_mode_is_switched_and_kept_with_the_level(tmp_path):
    path = tmp_path / "voice.json"
    level = make_level(path)
    level.set_level(120)

    turned_on = level.set_night(True)
    again = level.set_night(True)

    assert turned_on == VoiceChange(VoiceState(120, False), VoiceState(120, True))
    assert not again.changed
    assert stored(path) == {"version": 1, "level": 120, "night": True}
    assert level.set_night(False).after == VoiceState(120, False)


def test_changes_are_logged(tmp_path, caplog):
    level = make_level(tmp_path / "voice.json")

    with caplog.at_level(logging.INFO, logger="waifu.voice_level"):
        level.set_level(80)
        level.set_night(True)
        level.set_night(False)

    assert caplog.messages == ["Voice level 80 (was 100)", "Whisper mode on", "Whisper mode off"]


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "voice.json"
    path.write_text('{"version": 1, "level": 60, "night": true}', encoding="utf-8-sig")

    assert make_level(path).state() == VoiceState(60, True)


CORRUPT_STATES = {
    "not JSON": "{not json",
    "a list": "[]",
    "another version": '{"version": 2, "level": 100, "night": false}',
    "a boolean version": '{"version": true, "level": 100, "night": false}',
    "an extra key": '{"version": 1, "level": 100, "night": false, "volume": 1}',
    "a missing key": '{"version": 1, "level": 100}',
    "a level as text": '{"version": 1, "level": "100", "night": false}',
    "a boolean level": '{"version": 1, "level": true, "night": false}',
    "a fractional level": '{"version": 1, "level": 80.0, "night": false}',
    "a level too low": '{"version": 1, "level": 9, "night": false}',
    "a level too high": '{"version": 1, "level": 151, "night": false}',
    "night as a number": '{"version": 1, "level": 100, "night": 1}',
    "night as text": '{"version": 1, "level": 100, "night": "false"}',
}


@pytest.mark.parametrize("content", CORRUPT_STATES.values(), ids=CORRUPT_STATES.keys())
def test_a_file_that_is_not_a_voice_state_is_moved_aside_and_read_as_missing(
    tmp_path, caplog, content
):
    path = tmp_path / "voice.json"
    path.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="waifu.voice_level"):
        assert make_level(path).state() == VoiceState(100, False)

    assert (tmp_path / CORRUPT_NAME).read_text(encoding="utf-8") == content
    assert not path.exists()
    assert CORRUPT_NAME in caplog.text


def test_a_broken_file_moved_aside_twice_in_one_second_keeps_both(tmp_path):
    path = tmp_path / "voice.json"
    level = make_level(path)
    for content in ("first", "second"):
        path.write_text(content, encoding="utf-8")
        level.state()

    assert sorted(copy.name for copy in tmp_path.glob("voice.json.corrupt-*")) == [
        CORRUPT_NAME,
        f"{CORRUPT_NAME}-2",
    ]


def test_a_change_over_a_broken_file_moves_it_aside_first(tmp_path):
    path = tmp_path / "voice.json"
    path.write_text("broken", encoding="utf-8")

    make_level(path).change("softer")

    assert stored(path) == {"version": 1, "level": 80, "night": False}
    assert (tmp_path / CORRUPT_NAME).read_text(encoding="utf-8") == "broken"


def test_a_broken_file_that_cannot_be_moved_aside_raises_and_stays(tmp_path, monkeypatch, caplog):
    path = tmp_path / "voice.json"
    path.write_text("broken", encoding="utf-8")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(os, "rename", failing_rename)
    with (
        caplog.at_level(logging.ERROR, logger="waifu.voice_level"),
        pytest.raises(OSError, match="file in use"),
    ):
        make_level(path).set_level(60)

    assert path.read_text(encoding="utf-8") == "broken"
    assert [copy.name for copy in tmp_path.iterdir()] == ["voice.json"]
    assert "could not be moved aside" in caplog.text


# --- whisper mode ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clock", "whispering"),
    [
        ("21:59", False),
        ("22:00", True),  # from counts
        ("23:30", True),
        ("00:00", True),
        ("05:59", True),
        ("06:00", False),  # until does not
        ("12:00", False),
    ],
)
def test_whisper_mode_holds_from_its_start_until_its_end_across_midnight(
    tmp_path, clock, whispering
):
    hours, minutes = map(int, clock.split(":"))
    level = make_level(tmp_path / "voice.json", Clock(NOW.replace(hour=hours, minute=minutes)))

    assert level.whispering(VoiceState(100, night=True)) is whispering


@pytest.mark.parametrize(
    ("clock", "whispering"),
    [("12:59", False), ("13:00", True), ("14:59", True), ("15:00", False), ("23:00", False)],
)
def test_a_whisper_window_within_one_day(tmp_path, clock, whispering):
    hours, minutes = map(int, clock.split(":"))
    night = VoiceNightConfig.model_validate({"from": "13:00", "until": "15:00"})
    level = make_level(
        tmp_path / "voice.json", Clock(NOW.replace(hour=hours, minute=minutes)), night
    )

    assert level.whispering(VoiceState(100, night=True)) is whispering


def test_with_whisper_mode_off_she_never_whispers(tmp_path):
    level = make_level(tmp_path / "voice.json", Clock(NOW))

    assert level.whispering(VoiceState(100, night=False)) is False


def test_while_whispering_her_voice_is_at_most_the_night_level(tmp_path):
    path = tmp_path / "voice.json"
    write_state(path, level=120, night=True)

    assert make_level(path, Clock(NOW)).level_now() == 50


def test_a_level_below_the_night_level_stays_while_whispering(tmp_path):
    path = tmp_path / "voice.json"
    write_state(path, level=30, night=True)

    assert make_level(path, Clock(NOW)).level_now() == 30


def test_the_morning_greeting_at_the_end_of_the_window_is_at_the_normal_level(tmp_path):
    path = tmp_path / "voice.json"
    write_state(path, level=120, night=True)
    clock = Clock(datetime(2026, 10, 25, 5, 59))
    level = make_level(path, clock)
    assert level.level_now() == 50

    clock.moment = datetime(2026, 10, 25, 6, 0)

    assert level.level_now() == 120


def test_without_whisper_mode_the_night_is_at_the_normal_level(tmp_path):
    path = tmp_path / "voice.json"
    write_state(path, level=120, night=False)

    assert make_level(path, Clock(NOW)).level_now() == 120


def test_the_level_is_read_again_at_every_clip(tmp_path):
    path = tmp_path / "voice.json"
    level = make_level(path)
    assert level.level_now() == 100

    write_state(path, level=40)  # another process, or a hand edit

    assert level.level_now() == 40


# --- a file that cannot be read ------------------------------------------------------------


def make_unreadable(path: Path) -> None:
    """A folder where the file should be: reading it is an OSError on every system."""
    path.unlink(missing_ok=True)
    path.mkdir()


def test_an_unreadable_file_never_silences_her(tmp_path, caplog):
    path = tmp_path / "voice.json"
    make_unreadable(path)
    level = make_level(path)

    with caplog.at_level(logging.WARNING, logger="waifu.voice_level"):
        assert level.level_now() == 100
        assert level.level_now() == 100

    assert len(caplog.records) == 1  # one warning until the file reads again
    assert "voice.json" in caplog.text


def test_an_unreadable_file_keeps_the_last_state_read(tmp_path):
    path = tmp_path / "voice.json"
    write_state(path, level=60, night=True)
    level = make_level(path, Clock(NOW))
    assert level.level_now() == 50

    make_unreadable(path)

    assert level.level_now() == 50  # still whispering: the last state was read with night on


def test_the_warning_comes_again_after_the_file_read_once_more(tmp_path, caplog):
    path = tmp_path / "voice.json"
    make_unreadable(path)
    level = make_level(path)
    with caplog.at_level(logging.WARNING, logger="waifu.voice_level"):
        level.level_now()
        path.rmdir()
        assert level.level_now() == 100
        make_unreadable(path)
        level.level_now()

    assert len(caplog.records) == 2


def test_a_broken_file_that_cannot_be_moved_aside_never_silences_her(tmp_path, monkeypatch):
    path = tmp_path / "voice.json"
    path.write_text("broken", encoding="utf-8")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(os, "rename", failing_rename)

    assert make_level(path).level_now() == 100
