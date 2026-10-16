"""The version notice and its watcher: no test reads a real project folder, speaks, or pushes.

The code on disk is a fake read handing out `CodeVersion`s built here; every `VersionWatcher`
gets a fake local time, a fake announce, and a fake notifier. The thread tests run a real
thread whose waits are real or last a few milliseconds.
"""

import logging
import threading
import time
from datetime import datetime, timedelta

import pytest

from waifu.config import VersionNoticeConfig
from waifu.version import CodeVersion, Release
from waifu.version_watch import (
    VersionNews,
    VersionWatcher,
    notice_text,
    push_text,
    version_news,
)

HASH = "a" * 64
OTHER_HASH = "b" * 64
V124 = Release("v1.24", "keuangan dari tepira")
V125 = Release("v1.25", "peringatan hujan")
V126 = Release("v1.26", "versi baru")
V127 = Release("v1.27", "kamera HP")
RUNNING = CodeVersion((V125, V124), HASH, "1" * 40)
MORNING = datetime(2026, 10, 17, 9, 0)
INSTALL = ".venv\\Scripts\\python -m pip install -e ."


def disk(*releases: Release, dependencies: str | None = HASH, problems=()) -> CodeVersion:
    return CodeVersion(releases, dependencies, None, None, tuple(problems))


# --- what is news ------------------------------------------------------------------------------


def test_a_newer_release_on_disk_is_news():
    assert version_news(RUNNING, disk(V126, V125, V124)) == VersionNews((V126,), False)


def test_every_newer_release_is_in_the_news_newest_first():
    news = version_news(RUNNING, disk(V127, V126, V125))

    assert news == VersionNews((V127, V126), False)


def test_changed_dependencies_join_a_newer_release():
    assert version_news(RUNNING, disk(V126, V125, dependencies=OTHER_HASH)) == VersionNews(
        (V126,), True
    )


@pytest.mark.parametrize(
    ("running", "on_disk"),
    [
        (RUNNING, disk(V126, V125, dependencies=None)),  # pyproject unreadable now
        (CodeVersion((V125,), None), disk(V126, V125, dependencies=OTHER_HASH)),  # at start
    ],
    ids=["on_disk", "at_start"],
)
def test_an_unknown_dependency_hash_leaves_a_newer_release_without_changed_packages(
    running, on_disk
):
    assert version_news(running, on_disk) == VersionNews((V126,), False)


@pytest.mark.parametrize(
    ("running", "on_disk"),
    [
        (RUNNING, disk(V125, V124)),
        (RUNNING, disk(V124)),  # the CHANGELOG went back
        (RUNNING, disk(V125, V124, dependencies=OTHER_HASH)),  # a version still being built
        (CodeVersion((), HASH), disk(V126)),  # no release known at start
        (RUNNING, disk()),  # no release on disk
    ],
    ids=["same", "went_back", "packages_alone", "no_release", "none"],
)
def test_nothing_is_news_without_a_newer_release(running, on_disk):
    assert version_news(running, on_disk) is None


# --- what she says -----------------------------------------------------------------------------


def say(news: VersionNews, *, pushed: bool = True) -> str:
    return notice_text(news, owner="Sayang", name="Cyrene", pushed=pushed)


def test_one_newer_version_is_named_with_its_title():
    assert say(VersionNews((V125,), False)) == (
        "Sayang, ada versi baru Cyrene: v1.25, peringatan hujan. Mulai ulang aku kalau sempat ya."
    )


@pytest.mark.parametrize(("others", "words"), [(1, "satu"), (2, "dua"), (13, "tiga belas")])
def test_several_newer_versions_name_the_newest_and_count_the_others_in_words(others, words):
    older = tuple(Release(f"v1.{26 - n}", "lama") for n in range(others))

    assert say(VersionNews((V127, *older), False)) == (
        f"Sayang, ada versi baru Cyrene: v1.27, kamera HP, dan {words} versi lain. "
        "Mulai ulang aku kalau sempat ya."
    )


def test_changed_packages_ask_for_pip_install_first_and_point_at_the_phone():
    assert say(VersionNews((V126,), True)) == (
        "Sayang, ada versi baru Cyrene: v1.26, versi baru. Paket Python-nya juga berubah. "
        "Tutup aku dulu, jalankan pip install, lalu mulai ulang aku ya; perintahnya ada di HP."
    )


def test_without_a_push_she_points_at_waifu_version_for_the_command():
    assert say(VersionNews((V126,), True), pushed=False).endswith(
        "; perintahnya ada di waifu version."
    )


def test_she_says_her_own_name():
    text = notice_text(VersionNews((V125,), False), owner="Master", name="Kirin", pushed=False)

    assert text.startswith("Master, ada versi baru Kirin: v1.25")


ALL_NEWS = [
    VersionNews((V125,), False),
    VersionNews((V127, V126), False),
    VersionNews((V126,), True),
]


@pytest.mark.parametrize("news", ALL_NEWS)
@pytest.mark.parametrize("pushed", [True, False])
def test_what_she_says_has_no_parentheses_her_voice_would_drop(news, pushed):
    text = say(news, pushed=pushed)

    assert "(" not in text and ")" not in text


@pytest.mark.parametrize("news", [VersionNews((V125,), False), VersionNews((V127, V126), False)])
def test_without_changed_packages_the_push_is_what_she_says(news):
    assert push_text(news, owner="Sayang", name="Cyrene") == say(news)


def test_with_changed_packages_the_push_carries_the_exact_command():
    assert push_text(VersionNews((V126,), True), owner="Sayang", name="Cyrene") == (
        "Sayang, ada versi baru Cyrene: v1.26, versi baru. Paket Python-nya juga berubah. "
        f"Tutup Cyrene dulu, lalu di folder proyek jalankan:\n{INSTALL}\n"
        "Sesudah itu jalankan lagi waifu run."
    )


# --- the watcher -------------------------------------------------------------------------------


class FakeRead:
    """Hands out `reply` (raised when it is an exception) and counts the reads."""

    def __init__(self, reply: CodeVersion | Exception) -> None:
        self.reply = reply
        self.calls = 0

    def __call__(self) -> CodeVersion:
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class FakeNotifier:
    def __init__(self, outcome: bool | Exception = True) -> None:
        self.sent: list[tuple[str, str, tuple[str, ...]]] = []
        self.outcome = outcome

    def send(self, title, message, *, tags=(), click=None):
        self.sent.append((title, message, tuple(tags)))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


V126_TEXT = "Sayang, ada versi baru Cyrene: v1.26, versi baru. Mulai ulang aku kalau sempat ya."


class Rig:
    """A watcher and the fakes around it: the code on disk, the PC's local time, the voice."""

    def __init__(
        self, reply: CodeVersion | Exception, *, owner_call: str = "sayang", **config
    ) -> None:
        self.read = FakeRead(reply)
        self.now = MORNING
        self.notifier = FakeNotifier()
        self.announced: list[tuple[str, str]] = []
        self.watcher = VersionWatcher(
            self.read,
            RUNNING,
            VersionNoticeConfig(**config),
            announce=lambda text, emotion: self.announced.append((text, emotion)),
            notifier=self.notifier,
            owner_call=owner_call,
            now=lambda: self.now,
        )

    def poll(self, reply: CodeVersion | Exception | None = None) -> list[str]:
        """One poll, reading `reply` when given (else the last one)."""
        if reply is not None:
            self.read.reply = reply
        return self.watcher.poll_once()


def test_a_newer_version_is_said_happily_and_pushed_once():
    rig = Rig(disk(V126, V125, V124))

    assert rig.poll() == [V126_TEXT]  # the owner word at sentence start
    assert rig.announced == [(V126_TEXT, "happy")]
    assert rig.notifier.sent == [("Cyrene", V126_TEXT, ("new",))]
    assert rig.poll() == [] and rig.poll() == []
    assert len(rig.announced) == len(rig.notifier.sent) == 1


def test_the_code_she_started_with_is_nothing_to_tell():
    rig = Rig(disk(V125, V124))

    assert rig.poll() == []
    assert (rig.announced, rig.notifier.sent, rig.read.calls) == ([], [], 1)


def test_a_later_version_is_told_again_with_its_own_count():
    rig = Rig(disk(V126, V125))
    rig.poll()

    assert rig.poll(disk(V127, V126, V125)) == [
        "Sayang, ada versi baru Cyrene: v1.27, kamera HP, dan satu versi lain. "
        "Mulai ulang aku kalau sempat ya."
    ]
    assert rig.poll() == []


def test_packages_alone_are_never_told_and_join_the_version_that_follows():
    # A version is built task by task: its packages land first, its CHANGELOG heading last.
    rig = Rig(disk(V125, V124, dependencies=OTHER_HASH))
    assert rig.poll() == []

    [text] = rig.poll(disk(V126, V125, dependencies=OTHER_HASH))

    assert text.startswith("Sayang, ada versi baru Cyrene: v1.26, versi baru. Paket Python-nya")
    assert INSTALL in rig.notifier.sent[-1][1]
    assert rig.poll() == []
    assert len(rig.announced) == len(rig.notifier.sent) == 1


def test_packages_that_change_after_a_told_version_are_told_with_the_next_version():
    rig = Rig(disk(V126, V125))
    rig.poll()

    assert rig.poll(disk(V126, V125, dependencies=OTHER_HASH)) == []
    [text] = rig.poll(disk(V127, V126, V125, dependencies=OTHER_HASH))

    assert text.startswith("Sayang, ada versi baru Cyrene: v1.27, kamera HP, dan satu versi lain.")
    assert "Paket Python-nya juga berubah" in text and text.endswith("ada di HP.")
    assert INSTALL in rig.notifier.sent[-1][1]


def test_a_told_version_given_a_new_title_is_not_told_again():
    rig = Rig(disk(V126, V125))
    rig.poll()

    assert rig.poll(disk(Release("v1.26", "kabar versi baru"), V125)) == []
    assert len(rig.announced) == len(rig.notifier.sent) == 1


@pytest.mark.parametrize(
    "half_read",
    [
        disk(V127, V126, V125, dependencies=None, problems=["pyproject.toml mid-write"]),
        disk(dependencies=OTHER_HASH, problems=["CHANGELOG.md mid-write"]),
    ],
    ids=["pyproject", "changelog"],
)
def test_a_half_read_after_a_told_version_never_tells_it_again_with_less(half_read):
    rig = Rig(disk(V127, V126, V125, dependencies=OTHER_HASH))
    [told] = rig.poll()

    assert rig.poll(half_read) == []
    assert rig.poll(disk(V127, V126, V125, dependencies=OTHER_HASH)) == []
    assert rig.announced == [(told, "happy")]


def test_without_a_notifier_she_says_where_to_find_the_command():
    rig = Rig(disk(V126, V125, dependencies=OTHER_HASH))
    rig.watcher._notifier = None

    [text] = rig.poll()

    assert text.endswith("perintahnya ada di waifu version.")
    assert rig.announced == [(text, "happy")]


def test_she_calls_the_owner_and_names_herself_as_configured():
    watcher = VersionWatcher(
        FakeRead(disk(V126, V125)),
        RUNNING,
        VersionNoticeConfig(),
        announce=None,
        notifier=None,
        owner_call="Master",
        name="Kirin",
        now=lambda: MORNING,
    )

    assert watcher.poll_once() == [
        "Master, ada versi baru Kirin: v1.26, versi baru. Mulai ulang aku kalau sempat ya."
    ]


@pytest.mark.parametrize(
    ("now", "speaks"),
    [
        (datetime(2026, 10, 17, 7, 59), False),
        (datetime(2026, 10, 17, 8, 0), True),
        (datetime(2026, 10, 17, 21, 59), True),
        (datetime(2026, 10, 17, 22, 0), False),
    ],
    ids=["07:59", "08:00", "21:59", "22:00"],
)
def test_she_speaks_only_in_the_active_hours_and_reads_nothing_outside_them(now, speaks):
    rig = Rig(disk(V126, V125))
    rig.now = now

    notices = rig.poll()

    assert (len(notices), rig.read.calls) == ((1, 1) if speaks else (0, 0))


def test_a_version_found_at_night_is_told_at_the_first_poll_of_the_morning():
    rig = Rig(disk(V126, V125))
    rig.now = datetime(2026, 10, 16, 23, 30)
    assert rig.poll() == []

    rig.now = datetime(2026, 10, 17, 8, 0)
    assert rig.poll() == [V126_TEXT]


def test_the_active_hours_are_the_configured_ones():
    rig = Rig(disk(V126, V125), active_from="06:00", active_until="09:00")

    rig.now = datetime(2026, 10, 17, 6, 0)
    assert rig.poll() == [V126_TEXT]
    rig.now = datetime(2026, 10, 17, 9, 0)
    assert (rig.poll(disk(V127, V126, V125)), rig.read.calls) == ([], 1)


UNREADABLE = "Could not read the releases in CHANGELOG.md: no version heading (## v1.2: title)"


def test_a_read_with_problems_is_never_said_and_logged_once_until_it_reads_cleanly(caplog):
    broken = disk(dependencies=None, problems=[UNREADABLE])
    rig = Rig(broken)

    with caplog.at_level(logging.DEBUG, logger="waifu.version_watch"):
        failures = rig.poll() + rig.poll() + rig.poll()
        told = rig.poll(disk(V126, V125))
        rig.poll(broken)

    assert failures == []
    assert told == [V126_TEXT]
    records = [record for record in caplog.records if record.name == "waifu.version_watch"]
    levels = [record.levelno for record in records]
    assert levels[:3] == [logging.WARNING, logging.DEBUG, logging.DEBUG]
    assert levels[3] == logging.INFO  # it reads cleanly again
    assert levels[-1] == logging.WARNING  # a new run of failures
    assert UNREADABLE in records[0].getMessage()
    assert not any(record.exc_info for record in records)


def test_a_version_first_seen_in_a_read_with_problems_waits_for_a_clean_read():
    # Told now, v1.27 would go without its pip step for good: it is told only once.
    rig = Rig(disk(V127, V126, V125, dependencies=None, problems=["pyproject.toml mid-write"]))

    assert rig.poll() == []
    [text] = rig.poll(disk(V127, V126, V125, dependencies=OTHER_HASH))

    assert text.startswith("Sayang, ada versi baru Cyrene: v1.27, kamera HP, dan satu versi lain.")
    assert "Paket Python-nya juga berubah" in text


def test_a_failing_read_is_logged_with_its_traceback_and_never_said(caplog):
    rig = Rig(RuntimeError("the disk broke"))

    with caplog.at_level(logging.WARNING, logger="waifu.version_watch"):
        assert rig.poll() == [] and rig.poll() == []

    [record] = caplog.records  # the second failure only at DEBUG
    assert str(record.exc_info[1]) == "the disk broke"
    assert (rig.announced, rig.notifier.sent) == ([], [])
    assert rig.poll(disk(V126, V125)) == [V126_TEXT]


def test_a_release_that_breaks_the_decision_is_logged_and_the_next_poll_still_works(caplog):
    rig = Rig(disk(Release("v1.x", "rusak")))

    with caplog.at_level(logging.ERROR, logger="waifu.version_watch"):
        assert rig.poll() == []

    assert "Could not decide on a version notice" in caplog.text
    assert rig.poll(disk(V126, V125)) == [V126_TEXT]


def test_a_voice_that_fails_never_stops_the_push(caplog):
    rig = Rig(disk(V126, V125))

    def broken(text: str, emotion: str) -> None:
        raise RuntimeError("the avatar is gone")

    rig.watcher._announce = broken

    with caplog.at_level(logging.ERROR, logger="waifu.version_watch"):
        assert rig.poll() == [V126_TEXT]

    assert rig.notifier.sent == [("Cyrene", V126_TEXT, ("new",))]
    assert "the avatar is gone" in caplog.text


def test_a_push_that_fails_is_logged_and_the_notice_still_said(caplog):
    rig = Rig(disk(V126, V125))
    rig.notifier.outcome = OSError("no internet")

    with caplog.at_level(logging.ERROR, logger="waifu.version_watch"):
        assert rig.poll() == [V126_TEXT]

    assert rig.announced == [(V126_TEXT, "happy")]
    assert "no internet" in caplog.text
    assert rig.poll() == []  # told is told, even when the push failed


# --- the thread --------------------------------------------------------------------------------


class SignallingRead(FakeRead):
    def __init__(self, reply: CodeVersion | Exception) -> None:
        super().__init__(reply)
        self.read = threading.Event()

    def __call__(self) -> CodeVersion:
        self.read.set()
        return super().__call__()


class ShortWaits:
    """Stands in for threading.Event.wait: records each timeout asked for and waits on the stop
    event a few milliseconds at most."""

    def __init__(self) -> None:
        self.asked: list[float] = []

    def __call__(self, stop: threading.Event, timeout: float) -> bool:
        self.asked.append(timeout)
        return stop.wait(0.005)


def watcher_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "version-watcher"]


def make_watcher(read: FakeRead, config: VersionNoticeConfig | None = None, **options):
    return VersionWatcher(
        read,
        RUNNING,
        config or VersionNoticeConfig(),
        announce=None,
        notifier=None,
        owner_call="Master",
        now=lambda: MORNING,
        **options,
    )


def test_the_daemon_thread_polls_at_once_and_stop_wakes_it_at_once():
    read = SignallingRead(disk(V125, V124))
    watcher = make_watcher(read)

    watcher.start()
    try:
        assert read.read.wait(5)
        [thread] = watcher_threads()
        assert thread.daemon is True
    finally:
        stopping = time.monotonic()
        watcher.stop()

    assert time.monotonic() - stopping < 1  # although the next poll was 30 minutes away
    assert watcher_threads() == []
    assert read.calls == 1


@pytest.mark.parametrize(("minutes", "interval"), [(30, 1800.0), (5, 300.0)])
def test_the_next_poll_comes_check_minutes_after_the_last_one_began(minutes, interval):
    read = SignallingRead(disk(V125, V124))
    waits = ShortWaits()
    seconds = iter([1000.0, 1006.0] + [5000.0, 5000.0] * 100)  # the first poll took 6 s
    watcher = make_watcher(
        read,
        VersionNoticeConfig(check_minutes=minutes),
        clock=lambda: next(seconds),
        wait=waits,
    )

    watcher.start()
    try:
        deadline = time.monotonic() + 5
        while len(waits.asked) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        watcher.stop()

    assert waits.asked[:2] == [interval - 6, interval]
    assert read.calls >= 2


class PassingTime:
    """The PC's clock for a watcher thread: each wait moves it on by the whole timeout at once
    (the stop event still ends the wait), and each read of the code on disk notes the time."""

    def __init__(self, start: datetime) -> None:
        self.start = self.now = start
        self.reads: list[datetime] = []

    def clock(self) -> float:
        return (self.now - self.start).total_seconds()

    def wait(self, stop: threading.Event, timeout: float) -> bool:
        self.now += timedelta(seconds=timeout)
        return stop.wait(0.001)

    def read(self) -> CodeVersion:
        self.reads.append(self.now)
        return disk(V125, V124)


@pytest.mark.parametrize(
    ("settings", "started", "first_read"),
    [
        ({}, datetime(2026, 10, 17, 7, 30), datetime(2026, 10, 17, 8, 0)),
        ({}, datetime(2026, 10, 16, 23, 0), datetime(2026, 10, 17, 8, 0)),
        (
            {"active_from": "06:00", "active_until": "09:00", "check_minutes": 480},
            datetime(2026, 10, 17, 10, 0),
            datetime(2026, 10, 18, 6, 0),
        ),
    ],
    ids=["daily_from_07:30", "daily_from_23:00", "8_hours_in_06-09_from_10:00"],
)
def test_a_poll_outside_the_active_hours_waits_no_longer_than_until_they_begin(
    settings, started, first_read
):
    # A day-long interval would otherwise land every poll outside the hours, at night for good.
    time_ = PassingTime(started)
    watcher = VersionWatcher(
        time_.read,
        RUNNING,
        VersionNoticeConfig(**{"check_minutes": 1440, **settings}),
        announce=None,
        notifier=None,
        owner_call="Master",
        now=lambda: time_.now,
        clock=time_.clock,
        wait=time_.wait,
    )

    watcher.start()
    try:
        deadline = time.monotonic() + 5
        while not time_.reads and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        watcher.stop()

    assert time_.reads[:1] == [first_read]


def test_starting_twice_runs_one_thread():
    watcher = make_watcher(FakeRead(disk(V125, V124)), wait=ShortWaits())

    watcher.start()
    try:
        watcher.start()
        assert len(watcher_threads()) == 1
    finally:
        watcher.stop()

    assert watcher_threads() == []


def test_a_poll_that_overruns_the_interval_is_followed_by_the_next_one_at_once():
    # A poll slower than the interval must not make the wait negative.
    read = SignallingRead(disk(V125, V124))
    waits = ShortWaits()
    seconds = iter([0.0, 4000.0] + [9000.0, 9000.0] * 100)
    watcher = make_watcher(read, clock=lambda: next(seconds), wait=waits)

    watcher.start()
    try:
        deadline = time.monotonic() + 5
        while not waits.asked and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        watcher.stop()

    assert waits.asked[0] == 0.0
