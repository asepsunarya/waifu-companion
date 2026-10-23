"""The prayer reminders: when each is said, how often, and the state file that keeps a restart
from saying one twice.

No test speaks, pushes, or looks a place up: the place is a fake, every clock is a fake, and
the state file lives in tmp_path. The times come from `prayer_times` itself (its numbers are
tested in test_prayer_times.py).
"""

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from waifu.config import PrayerConfig
from waifu.finance.values import WIB
from waifu.prayer import PrayerPlace
from waifu.prayer_times import prayer_times
from waifu.prayer_watch import POLL_S, PrayerWatcher
from waifu.weather import UNREACHABLE, Location, WeatherError

BANDUNG = Location("Bandung, Jawa Barat", -6.90389, 107.61861)
TODAY = date(2026, 10, 24)
SECOND = timedelta(seconds=1)
MINUTE = timedelta(minutes=1)


def at(name: str, day: date = TODAY, ihtiyath: int = 2) -> datetime:
    times = prayer_times(day, BANDUNG.latitude, BANDUNG.longitude, ihtiyath_minutes=ihtiyath)
    return getattr(times, name)


class FakeNotifier:
    def __init__(self, outcome: bool | Exception = True) -> None:
        self.sent: list[tuple[str, str, tuple[str, ...]]] = []
        self.outcome = outcome

    def send(self, title, message, *, tags=()):
        self.sent.append((title, message, tuple(tags)))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Rig:
    """A watcher and the fakes around it: the place, WIB time, monotonic seconds, Master's
    reminder queue, and the phone."""

    def __init__(self, tmp_path: Path, *, owner_call: str = "sayang", **config) -> None:
        self.state_path = tmp_path / "prayer.json"
        self.config = PrayerConfig(enabled=True, state_path=self.state_path, **config)
        self.now = datetime.combine(TODAY, datetime.min.time(), WIB)
        self.seconds = 1000.0
        self.failure: Exception | None = None
        self.voice_failure: Exception | None = None
        self.lookups = 0
        self.reminded: list[tuple[str, str]] = []
        self.files_when_reminded: list[bytes | None] = []
        self.notifier = FakeNotifier()
        self.owner_call = owner_call
        self.watcher = self.build()

    def build(self) -> PrayerWatcher:
        """A watcher over the same fakes and state file: a restart of `waifu run`."""
        return PrayerWatcher(
            PrayerPlace(self.locate),
            self.config,
            remind=self.remind,
            notifier=self.notifier,
            owner_call=self.owner_call,
            now=lambda: self.now,
            clock=lambda: self.seconds,
        )

    def locate(self) -> Location:
        self.lookups += 1
        if self.failure is not None:
            raise self.failure
        return BANDUNG

    def remind(self, text: str, emotion: str) -> None:
        self.files_when_reminded.append(
            self.state_path.read_bytes() if self.state_path.is_file() else None
        )
        if self.voice_failure is not None:
            raise self.voice_failure
        self.reminded.append((text, emotion))

    def poll(self, moment: datetime) -> list[str]:
        self.now = moment
        return self.watcher.poll_once()

    def state(self) -> object:
        return json.loads(self.state_path.read_text(encoding="utf-8"))


@pytest.fixture
def rig(tmp_path) -> Rig:
    return Rig(tmp_path)


def every_poll_of_the_day(rig: Rig) -> list[str]:
    said: list[str] = []
    moment = datetime.combine(TODAY, datetime.min.time(), WIB)
    while moment.date() == TODAY:
        said += rig.poll(moment)
        moment += timedelta(seconds=POLL_S)
    return said


# --- when she says it ---------------------------------------------------------------------------


def test_at_dzuhur_s_time_she_says_it_once_through_master_s_reminders(rig):
    assert rig.poll(at("dzuhur")) == ["Sayang, sudah masuk waktu Dzuhur."]
    assert rig.poll(at("dzuhur") + 30 * SECOND) == []

    assert rig.reminded == [("Sayang, sudah masuk waktu Dzuhur.", "neutral")]


def test_nothing_is_said_a_moment_before_the_time(rig):
    assert rig.poll(at("dzuhur") - SECOND) == []
    assert rig.reminded == []


def test_a_reminder_up_to_30_minutes_late_is_still_said(rig):
    assert rig.poll(at("ashar") + 29 * MINUTE + 59 * SECOND) == ["Sayang, sudah masuk waktu Ashar."]


def test_a_reminder_30_minutes_late_is_not_said(rig):
    # A PC woken, or `waifu run` started, that late says nothing for it.
    assert rig.poll(at("ashar") + 30 * MINUTE) == []
    assert not rig.state_path.exists()


def test_through_a_whole_day_each_of_the_five_is_said_once_in_order(rig):
    assert every_poll_of_the_day(rig) == [
        "Sayang, sudah masuk waktu Subuh.",
        "Sayang, sudah masuk waktu Dzuhur.",
        "Sayang, sudah masuk waktu Ashar.",
        "Sayang, sudah masuk waktu Maghrib.",
        "Sayang, sudah masuk waktu Isya.",
    ]


def test_only_the_prayers_master_listed_are_said(tmp_path):
    rig = Rig(tmp_path, remind=["maghrib", "subuh"])

    assert every_poll_of_the_day(rig) == [
        "Sayang, sudah masuk waktu Subuh.",
        "Sayang, sudah masuk waktu Maghrib.",
    ]


def test_with_minutes_before_she_says_how_long_is_left_once(tmp_path):
    rig = Rig(tmp_path, minutes_before=10)

    assert rig.poll(at("maghrib") - 10 * MINUTE - SECOND) == []
    assert rig.poll(at("maghrib") - 10 * MINUTE) == [
        "Sayang, sepuluh menit lagi masuk waktu Maghrib."
    ]
    assert rig.poll(at("maghrib")) == []


def test_with_minutes_before_a_late_start_tells_the_minutes_really_left(tmp_path):
    rig = Rig(tmp_path, minutes_before=10)

    assert rig.poll(at("maghrib") - 4 * MINUTE - 30 * SECOND) == [
        "Sayang, lima menit lagi masuk waktu Maghrib."
    ]


def test_with_minutes_before_a_start_after_the_time_says_it_has_come(tmp_path):
    rig = Rig(tmp_path, minutes_before=10)

    assert rig.poll(at("maghrib") + 5 * MINUTE) == ["Sayang, sudah masuk waktu Maghrib."]


def test_the_times_use_master_s_ihtiyath(tmp_path):
    rig = Rig(tmp_path, ihtiyath_minutes=0)

    assert rig.poll(at("dzuhur", ihtiyath=0)) == ["Sayang, sudah masuk waktu Dzuhur."]


def test_she_calls_the_owner_as_configured_at_sentence_start(tmp_path):
    rig = Rig(tmp_path, owner_call="master")

    assert rig.poll(at("subuh")) == ["Master, sudah masuk waktu Subuh."]


def test_a_new_day_says_the_prayers_again(rig):
    rig.poll(at("dzuhur"))
    tomorrow = TODAY + timedelta(days=1)

    assert rig.poll(at("dzuhur", tomorrow)) == ["Sayang, sudah masuk waktu Dzuhur."]
    assert rig.state() == {"version": 1, "date": "2026-10-25", "told": ["dzuhur"]}


# --- the phone ----------------------------------------------------------------------------------


def test_it_is_pushed_with_the_mosque_tag_when_the_watcher_has_a_notifier(rig):
    rig.poll(at("isya"))

    assert rig.notifier.sent == [("Cyrene", "Sayang, sudah masuk waktu Isya.", ("mosque",))]


def test_without_a_notifier_it_is_only_said(tmp_path):
    rig = Rig(tmp_path)
    rig.watcher = PrayerWatcher(
        PrayerPlace(rig.locate),
        rig.config,
        remind=rig.remind,
        notifier=None,
        owner_call="sayang",
        now=lambda: rig.now,
        clock=lambda: rig.seconds,
    )

    assert rig.poll(at("isya")) == ["Sayang, sudah masuk waktu Isya."]
    assert rig.notifier.sent == []


def test_a_voice_that_fails_never_stops_the_push(rig, caplog):
    rig.voice_failure = RuntimeError("the avatar is gone")

    with caplog.at_level(logging.ERROR, logger="waifu.prayer_watch"):
        assert rig.poll(at("isya")) == ["Sayang, sudah masuk waktu Isya."]

    assert [message for _, message, _ in rig.notifier.sent] == ["Sayang, sudah masuk waktu Isya."]
    assert "the avatar is gone" in caplog.text


def test_a_push_that_fails_is_logged_and_the_line_still_said(rig, caplog):
    rig.notifier.outcome = OSError("no internet")

    with caplog.at_level(logging.ERROR, logger="waifu.prayer_watch"):
        rig.poll(at("isya"))

    assert rig.reminded == [("Sayang, sudah masuk waktu Isya.", "neutral")]
    assert "no internet" in caplog.text


# --- the state file -----------------------------------------------------------------------------


def test_a_restart_within_the_window_says_nothing_again(rig):
    rig.poll(at("dzuhur"))

    rig.watcher = rig.build()

    assert rig.poll(at("dzuhur") + MINUTE) == []
    assert rig.state() == {"version": 1, "date": "2026-10-24", "told": ["dzuhur"]}


def test_the_prayer_is_saved_as_told_before_she_says_it(rig):
    rig.poll(at("dzuhur"))

    assert rig.files_when_reminded == [b'{"version":1,"date":"2026-10-24","told":["dzuhur"]}']


def test_the_told_prayers_are_kept_in_the_day_s_order(rig):
    rig.state_path.write_text('{"version":1,"date":"2026-10-24","told":["isya"]}')

    rig.poll(at("dzuhur"))

    assert rig.state()["told"] == ["dzuhur", "isya"]


def test_the_file_is_read_only_when_a_prayer_is_due(rig, caplog):
    rig.state_path.mkdir()  # reading it would fail, with a warning

    with caplog.at_level(logging.WARNING, logger="waifu.prayer_watch"):
        assert rig.poll(at("dzuhur") - MINUTE) == []

    assert caplog.records == []


def test_another_day_s_file_counts_as_nothing_told(rig):
    rig.state_path.write_text(
        '{"version":1,"date":"2026-10-23","told":["subuh","dzuhur","ashar","maghrib","isya"]}'
    )

    assert rig.poll(at("dzuhur")) == ["Sayang, sudah masuk waktu Dzuhur."]
    assert rig.state() == {"version": 1, "date": "2026-10-24", "told": ["dzuhur"]}


def test_a_state_saved_with_a_byte_order_mark_is_read(rig):
    rig.state_path.write_bytes(b'\xef\xbb\xbf{"version":1,"date":"2026-10-24","told":["dzuhur"]}')

    assert rig.poll(at("dzuhur")) == []


CORRUPT_STATES = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version":1,"date":"2026-10-24","told":["\xff"]}',
    "a list": b"[]",
    "a key missing": b'{"version":1,"date":"2026-10-24"}',
    "a key too many": b'{"version":1,"date":"2026-10-24","told":[],"extra":1}',
    "another version": b'{"version":2,"date":"2026-10-24","told":[]}',
    "a version as text": b'{"version":"1","date":"2026-10-24","told":[]}',
    "a date not YYYY-MM-DD": b'{"version":1,"date":"24-10-2026","told":[]}',
    "a day that does not exist": b'{"version":1,"date":"2026-02-30","told":[]}',
    "told not a list": b'{"version":1,"date":"2026-10-24","told":"dzuhur"}',
    "an unknown prayer": b'{"version":1,"date":"2026-10-24","told":["dhuha"]}',
    "a prayer twice": b'{"version":1,"date":"2026-10-24","told":["subuh","subuh"]}',
}


@pytest.mark.parametrize("content", CORRUPT_STATES.values(), ids=CORRUPT_STATES.keys())
def test_a_file_that_is_no_state_is_moved_aside_and_counts_as_nothing_told(rig, content, caplog):
    rig.state_path.write_bytes(content)
    moment = at("dzuhur")

    with caplog.at_level(logging.ERROR, logger="waifu.prayer_watch"):
        assert rig.poll(moment) == ["Sayang, sudah masuk waktu Dzuhur."]

    moved = rig.state_path.with_name(f"prayer.json.corrupt-{moment:%Y%m%d-%H%M%S}")
    assert moved.read_bytes() == content
    assert rig.state() == {"version": 1, "date": "2026-10-24", "told": ["dzuhur"]}
    assert "moved it to" in caplog.text


def test_a_second_broken_file_the_same_second_gets_its_own_name(rig):
    moment = at("dzuhur")
    taken = rig.state_path.with_name(f"prayer.json.corrupt-{moment:%Y%m%d-%H%M%S}")
    taken.write_bytes(b"older")
    rig.state_path.write_bytes(b"{not json")

    rig.poll(moment)

    assert taken.read_bytes() == b"older"
    assert taken.with_name(f"{taken.name}-2").read_bytes() == b"{not json"


def test_a_file_that_cannot_be_read_is_left_alone_and_she_says_it_once(rig, caplog):
    rig.state_path.mkdir()  # a folder where the file should be

    with caplog.at_level(logging.WARNING, logger="waifu.prayer_watch"):
        assert rig.poll(at("dzuhur")) == ["Sayang, sudah masuk waktu Dzuhur."]
        assert rig.poll(at("dzuhur") + MINUTE) == []

    assert rig.state_path.is_dir()
    assert "Could not read the prayer reminder state" in caplog.text


def test_a_file_that_cannot_be_written_is_logged_and_she_says_it_once(rig, caplog, monkeypatch):
    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    with caplog.at_level(logging.WARNING, logger="waifu.prayer_watch"):
        assert rig.poll(at("dzuhur")) == ["Sayang, sudah masuk waktu Dzuhur."]
        assert rig.poll(at("dzuhur") + MINUTE) == []

    assert not rig.state_path.exists()
    assert "disk full" in caplog.text


def test_prayers_said_while_the_file_could_not_be_read_are_saved_with_the_next_write(rig):
    rig.state_path.mkdir()
    rig.poll(at("dzuhur"))
    rig.state_path.rmdir()

    rig.poll(at("ashar"))

    assert rig.state() == {"version": 1, "date": "2026-10-24", "told": ["dzuhur", "ashar"]}


# --- the place ----------------------------------------------------------------------------------


def test_a_place_that_cannot_be_found_is_never_said_logged_once_and_asked_again_later(rig, caplog):
    rig.failure = WeatherError(UNREACHABLE)

    with caplog.at_level(logging.DEBUG, logger="waifu.prayer_watch"):
        assert rig.poll(at("dzuhur")) == []
        assert rig.poll(at("dzuhur") + 30 * SECOND) == []  # not asked again yet
        rig.seconds += 900
        assert rig.poll(at("dzuhur") + MINUTE) == []  # asked again, still failing
        rig.seconds += 900
        rig.failure = None
        told = rig.poll(at("dzuhur") + 2 * MINUTE)

    assert told == ["Sayang, sudah masuk waktu Dzuhur."]
    assert rig.lookups == 3
    about_the_place = [record for record in caplog.records if "place" in record.getMessage()]
    assert [record.levelno for record in about_the_place] == [
        logging.WARNING,
        logging.DEBUG,
        logging.INFO,
    ]
    assert "Cek koneksi internet" in about_the_place[0].getMessage()
    assert not any(record.exc_info for record in caplog.records)


def test_an_unexpected_lookup_failure_is_logged_with_its_traceback(rig, caplog):
    rig.failure = RuntimeError("boom")

    with caplog.at_level(logging.WARNING, logger="waifu.prayer_watch"):
        assert rig.poll(at("dzuhur")) == []

    [record] = caplog.records
    assert record.exc_info is not None


def test_without_a_place_nothing_is_said(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.watcher = PrayerWatcher(
        PrayerPlace(None),
        rig.config,
        remind=rig.remind,
        notifier=rig.notifier,
        owner_call="sayang",
        now=lambda: rig.now,
        clock=lambda: rig.seconds,
    )

    with caplog.at_level(logging.WARNING, logger="waifu.prayer_watch"):
        assert rig.poll(at("dzuhur")) == []

    assert "belum diatur" in caplog.text


# --- the thread ---------------------------------------------------------------------------------


class SignallingPlace:
    """A lookup that sets `looked` at each call and counts them in `calls`."""

    def __init__(self) -> None:
        self.looked = threading.Event()
        self.calls = 0

    def __call__(self) -> Location:
        self.calls += 1
        self.looked.set()
        return BANDUNG


class ShortWaits:
    """Stands in for threading.Event.wait: records each timeout asked for and waits on the stop
    event a few milliseconds at most."""

    def __init__(self) -> None:
        self.asked: list[float] = []

    def __call__(self, stop: threading.Event, timeout: float) -> bool:
        self.asked.append(timeout)
        return stop.wait(0.005)


def watcher_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "prayer-watcher"]


def make_watcher(tmp_path, lookup, **options) -> PrayerWatcher:
    return PrayerWatcher(
        PrayerPlace(lookup),
        PrayerConfig(enabled=True, state_path=tmp_path / "prayer.json"),
        remind=lambda text, emotion: None,
        notifier=None,
        owner_call="sayang",
        now=lambda: datetime(2026, 10, 24, 20, 0, tzinfo=WIB),
        **options,
    )


def test_the_daemon_thread_polls_at_once_and_stop_wakes_it_at_once(tmp_path):
    lookup = SignallingPlace()
    watcher = make_watcher(tmp_path, lookup)

    watcher.start()
    try:
        assert lookup.looked.wait(5)
        [thread] = watcher_threads()
        assert thread.daemon is True
    finally:
        stopping = time.monotonic()
        watcher.stop()

    assert time.monotonic() - stopping < 1  # although the next poll was 30 seconds away
    assert watcher_threads() == []
    assert lookup.calls == 1


def test_the_next_poll_comes_30_seconds_after_the_last_one_began(tmp_path):
    assert POLL_S == 30
    lookup = SignallingPlace()
    waits = ShortWaits()
    seconds = iter([1000.0, 1002.0] + [2000.0, 2000.0] * 100)  # the first poll took 2 s
    watcher = make_watcher(tmp_path, lookup, clock=lambda: next(seconds), wait=waits)

    watcher.start()
    try:
        deadline = time.monotonic() + 5
        while len(waits.asked) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        watcher.stop()

    assert waits.asked[:2] == [28.0, 30.0]
    assert lookup.calls >= 2


def test_a_poll_that_raises_never_stops_the_thread(tmp_path, caplog):
    waits = ShortWaits()
    watcher = PrayerWatcher(
        PrayerPlace(SignallingPlace()),
        PrayerConfig(enabled=True, state_path=tmp_path / "prayer.json"),
        remind=lambda text, emotion: None,
        notifier=None,
        owner_call="sayang",
        now=lambda: 1 / 0,  # a bug in a poll
        wait=waits,
    )

    with caplog.at_level(logging.ERROR, logger="waifu.prayer_watch"):
        watcher.start()
        try:
            deadline = time.monotonic() + 5
            while len(waits.asked) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            watcher.stop()

    assert len(waits.asked) >= 2
    assert "ZeroDivisionError" in caplog.text


def test_starting_twice_runs_one_thread(tmp_path):
    watcher = make_watcher(tmp_path, SignallingPlace(), wait=ShortWaits())

    watcher.start()
    try:
        watcher.start()
        assert len(watcher_threads()) == 1
    finally:
        watcher.stop()

    assert watcher_threads() == []
