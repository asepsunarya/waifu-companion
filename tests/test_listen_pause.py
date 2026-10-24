"""The pause of Ctrl+Alt+Q: a small file while she does not listen, shared with other processes.

Every pause writes under tmp_path with fake clocks."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from waifu.listen_pause import REREAD_S, ListenPause

START = datetime(2026, 10, 24, 14, 30)
CORRUPT_NAME = "listen_pause.json.corrupt-20261024-143000"


class Clock:
    def __init__(self) -> None:
        self.current = START

    def __call__(self) -> datetime:
        return self.current


class Monotonic:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Room:
    """A pause on a file under tmp_path, with one wall clock and one monotonic clock."""

    def __init__(self, tmp_path: Path, hours: float = 2.0) -> None:
        self.clock = Clock()
        self.monotonic = Monotonic()
        self.path = tmp_path / "data" / "listen_pause.json"
        self.hours = hours
        self.pause = self.another_pause()

    def another_pause(self) -> ListenPause:
        """The pause of another process (`waifu tombol`), or of a restarted one."""
        return ListenPause(self.path, hours=self.hours, now=self.clock, clock=self.monotonic)

    def advance(self, seconds: float) -> None:
        self.clock.current += timedelta(seconds=seconds)
        self.monotonic.now += seconds


def test_nothing_is_paused_without_a_file_and_reading_creates_none(tmp_path):
    room = Room(tmp_path)

    assert (room.pause.until(), room.pause.paused()) == (None, False)
    assert not room.path.exists()


def test_a_pause_holds_for_its_hours_from_now_in_a_file(tmp_path):
    room = Room(tmp_path, hours=2)

    until = room.pause.start()

    assert until == datetime(2026, 10, 24, 16, 30)
    assert room.pause.until() == until and room.pause.paused()
    assert json.loads(room.path.read_text(encoding="utf-8")) == {
        "version": 1,
        "since": "2026-10-24T14:30:00",
        "until": "2026-10-24T16:30:00",
    }


def test_a_pause_of_a_quarter_hour_ends_at_its_minute(tmp_path):
    room = Room(tmp_path, hours=0.25)

    assert room.pause.start() == datetime(2026, 10, 24, 14, 45)


def test_ending_a_pause_deletes_its_file(tmp_path):
    room = Room(tmp_path)
    room.pause.start()

    assert room.pause.end() is True

    assert room.pause.paused() is False
    assert not room.path.exists()
    assert room.pause.end() is False


def test_starting_again_holds_from_now(tmp_path):
    room = Room(tmp_path)
    room.pause.start()
    room.advance(600)

    assert room.pause.start() == datetime(2026, 10, 24, 16, 40)


def test_a_pause_another_process_starts_or_ends_is_seen_within_the_reread_time(tmp_path):
    room = Room(tmp_path)
    tombol = room.another_pause()
    assert room.pause.paused() is False

    tombol.start()
    seen = [room.pause.paused()]
    room.monotonic.now += REREAD_S
    seen.append(room.pause.paused())
    tombol.end()
    seen.append(room.pause.paused())
    room.monotonic.now += REREAD_S
    seen.append(room.pause.paused())

    assert seen == [False, True, True, False]


def test_asking_often_never_keeps_a_read_longer_than_the_reread_time(tmp_path):
    room = Room(tmp_path)
    assert room.pause.paused() is False  # read, and kept
    room.another_pause().start()

    seen = []
    for _ in range(3):
        seen.append(room.pause.paused())
        room.monotonic.now += REREAD_S / 2

    assert seen == [False, False, True]


def test_a_pause_ends_by_itself_and_its_file_is_deleted(tmp_path, caplog):
    room = Room(tmp_path, hours=2)
    room.pause.start()
    room.advance(2 * 3600)

    with caplog.at_level(logging.INFO, logger="waifu.listen_pause"):
        assert room.pause.until() is None
        assert room.pause.paused() is False

    assert not room.path.exists()
    assert caplog.text.count("The listen pause ended by itself at 16:30") == 1


def test_a_kept_pause_that_has_ended_is_read_again_before_its_file_is_deleted(tmp_path):
    room = Room(tmp_path, hours=2)
    run, tombol = room.pause, room.another_pause()
    run.start()
    room.advance(2 * 3600 - 0.5)
    assert tombol.paused() is True

    room.advance(0.6)  # the first pause has ended, within the time tombol keeps its read
    again = run.start()

    assert tombol.until() == again
    assert room.path.exists()


def test_an_ended_pause_whose_file_cannot_be_deleted_is_logged_once(tmp_path, caplog, monkeypatch):
    room = Room(tmp_path, hours=2)
    room.pause.start()
    room.advance(2 * 3600)
    unlink = Path.unlink

    def locked(path, missing_ok=False):
        if path == room.path:
            raise PermissionError(32, "The file is used by another process")
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked)
    seen = []
    with caplog.at_level(logging.INFO, logger="waifu.listen_pause"):
        for _ in range(3):
            seen.append(room.pause.paused())
            room.advance(REREAD_S)
        monkeypatch.undo()
        seen.append(room.pause.paused())

    assert seen == [False] * 4
    assert not room.path.exists()
    assert caplog.text.count("The listen pause ended by itself") == 1
    assert caplog.text.count("Could not delete the ended listen pause's file") == 1


VALID = {"version": 1, "since": "2026-10-24T14:00:00", "until": "2026-10-24T16:00:00"}
CORRUPT = {
    "not json": b"{not json",
    "a list": b"[]",
    "another version": json.dumps(VALID | {"version": 2}).encode(),
    "an extra key": json.dumps(VALID | {"why": "game"}).encode(),
    "until not a time": json.dumps(VALID | {"until": "nanti"}).encode(),
    "until with a time zone": json.dumps(VALID | {"until": "2026-10-24T16:00:00+07:00"}).encode(),
    "until before since": json.dumps(VALID | {"until": "2026-10-24T13:00:00"}).encode(),
}


@pytest.mark.parametrize("content", CORRUPT.values(), ids=CORRUPT.keys())
def test_a_broken_pause_file_is_moved_aside_logged_and_read_as_no_pause(tmp_path, caplog, content):
    room = Room(tmp_path)
    room.path.parent.mkdir()
    room.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.listen_pause"):
        assert room.pause.paused() is False

    assert (room.path.parent / CORRUPT_NAME).read_bytes() == content
    assert not room.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_a_pause_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    room = Room(tmp_path)
    room.path.parent.mkdir()
    room.path.write_bytes(b"\xef\xbb\xbf" + json.dumps(VALID).encode())

    assert room.pause.until() == datetime(2026, 10, 24, 16, 0)


def test_a_pause_file_that_cannot_be_read_is_no_pause_and_warned_once(tmp_path, caplog):
    room = Room(tmp_path)
    room.path.mkdir(parents=True)  # a folder where the file should be

    with caplog.at_level(logging.WARNING, logger="waifu.listen_pause"):
        seen = [room.pause.paused()]
        room.monotonic.now += REREAD_S
        seen.append(room.pause.paused())

    assert seen == [False, False]
    assert caplog.text.count("Could not read the listen pause file") == 1


def test_a_pause_that_cannot_be_written_raises(tmp_path):
    room = Room(tmp_path)
    room.path.parent.write_text("a file where the folder should be", encoding="utf-8")

    with pytest.raises(OSError):
        room.pause.start()

    assert room.pause.paused() is False
