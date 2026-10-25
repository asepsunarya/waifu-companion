import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from waifu.config import NetworkConfig
from waifu.finance.values import WIB
from waifu.network import CurrentOutage, NetworkState
from waifu.network_history import NetworkRecord, NetworkStore, OpenOutage, Outage
from waifu.network_probe import Reading
from waifu.network_watch import BACK_EXPIRES, NOTICE_EMOTION, NetworkWatcher

pytestmark = pytest.mark.usefixtures("nothing_beyond_this_pc")

START = datetime(2026, 10, 25, 20, 58, tzinfo=WIB)
UP = Reading(at=START, route=True, gateway="192.168.1.1", gateway_ms=1.0, anchor_ms=24.0)
DOWN = replace(UP, anchor_ms=None)
BACK = "Master, internetnya sudah nyambung lagi, tadi putus sekitar 12 menit, sejak jam 21.03."


def at(hour: int, minute: float) -> datetime:
    return datetime(2026, 10, 25, hour, tzinfo=WIB) + timedelta(minutes=minute)


class FakeProbe:
    """Answers each check with the reading the rig set for it; records what was asked."""

    def __init__(self) -> None:
        self.next: Reading | Exception = UP
        self.asked: list[dict] = []

    def check(self, *, dns: bool = False, hoyo: bool = False) -> Reading:
        self.asked.append({"dns": dns, "hoyo": hoyo})
        if isinstance(self.next, Exception):
            raise self.next
        return self.next


class Rig:
    """A watcher over a real store in tmp_path, a fake probe, and a wall and monotonic clock
    the test moves together, one minute a poll unless told otherwise."""

    def __init__(self, tmp_path, config: NetworkConfig | None = None, **watcher) -> None:
        self.config = config or NetworkConfig()
        self.wall = START
        self.mono = 5000.0
        self.probe = FakeProbe()
        self.path = tmp_path / "network.json"
        self.store = NetworkStore(self.path, keep_days=7, now=lambda: self.wall)
        self.state = NetworkState(slow_window=self.config.slow_window)
        self.said: list[tuple] = []
        self.polls = 0
        options = {"announce": self.announce, **watcher}
        self.watcher = NetworkWatcher(
            self.probe,
            self.store,
            self.config,
            self.state,
            owner_call="master",
            now=lambda: self.wall,
            clock=lambda: self.mono,
            **options,
        )

    def announce(self, text, emotion, *, expires_at=None, game_expires_at=None) -> None:
        self.said.append((text, emotion, expires_at, game_expires_at))

    def poll(self, reading: Reading | Exception, *, minutes: float = 1, mono_minutes=None) -> list:
        """One poll seeing `reading`, `minutes` after the last by both clocks (or
        `mono_minutes` by the monotonic one); the run's first poll is at START."""
        if self.polls:
            self.wall += timedelta(minutes=minutes)
            self.mono += 60 * (minutes if mono_minutes is None else mono_minutes)
        self.polls += 1
        self.probe.next = reading
        return self.watcher.poll_once()

    def settle(self) -> None:
        """The run's first two polls, which never count, both answered."""
        self.poll(UP)
        self.poll(UP)

    def record(self) -> NetworkRecord:
        return self.store.record()


def outage_from_21_03(rig: Rig, *, until: float = 15) -> None:
    """Settled, up at 21:02, then down from 21:03 to just before 21:`until`."""
    rig.settle()  # 20:58, 20:59
    rig.poll(UP, minutes=3)  # 21:02
    while rig.wall < at(21, until - 1):
        rig.poll(DOWN)


# --- outages -----------------------------------------------------------------------------------


def test_two_failed_polls_in_a_row_start_an_outage_at_the_first(tmp_path, caplog):
    rig = Rig(tmp_path)
    rig.settle()
    rig.poll(UP, minutes=3)  # 21:02

    with caplog.at_level(logging.WARNING, logger="waifu.network_watch"):
        rig.poll(DOWN)  # 21:03
        assert rig.state.outage() is None
        assert not rig.path.exists()
        rig.poll(DOWN)  # 21:04

    assert rig.state.outage() == CurrentOutage(at(21, 3), "offline")
    assert rig.record().open == OpenOutage(at(21, 3), at(21, 4), "offline")
    assert "down since 21:03 (offline)" in caplog.text
    assert "192.168" not in caplog.text


def test_each_failed_poll_of_an_outage_moves_its_last_failed_poll(tmp_path):
    rig = Rig(tmp_path)
    outage_from_21_03(rig, until=10)

    assert rig.record().open == OpenOutage(at(21, 3), at(21, 9), "offline")


def test_one_failed_poll_is_no_outage(tmp_path):
    rig = Rig(tmp_path)
    rig.settle()

    for reading in (DOWN, UP, DOWN, UP):
        rig.poll(reading)

    assert rig.state.outage() is None
    assert not rig.path.exists()
    assert rig.said == []


def test_the_outage_s_kind_is_its_first_failed_poll_s(tmp_path):
    rig = Rig(tmp_path)
    rig.settle()
    no_link = replace(DOWN, route=False, gateway=None, gateway_ms=None)

    rig.poll(no_link)
    rig.poll(DOWN)

    assert rig.state.outage() == CurrentOutage(at(21, 0), "router")


def test_the_poll_that_answers_ends_the_outage_and_she_says_once_that_it_is_back(tmp_path):
    rig = Rig(tmp_path)
    outage_from_21_03(rig)

    said = rig.poll(UP)  # 21:15

    assert said == [BACK]
    assert rig.said == [(BACK, NOTICE_EMOTION, at(21, 45), at(21, 45))]
    assert timedelta(minutes=30) == BACK_EXPIRES
    assert rig.state.outage() is None
    assert rig.record() == NetworkRecord(
        outages=(Outage(at(21, 3), at(21, 15), "offline"),), back_told=rig.record().back_told
    )
    assert rig.record().back_count(at(21, 15).date()) == 1
    assert rig.poll(UP) == []


def test_an_outage_shorter_than_back_after_minutes_is_kept_but_not_said(tmp_path):
    rig = Rig(tmp_path)
    outage_from_21_03(rig, until=5)  # down at 21:03 and 21:04

    assert rig.poll(UP) == []  # 21:05: 2 minutes

    assert rig.record().outages == (Outage(at(21, 3), at(21, 5), "offline"),)
    assert rig.said == []


def test_an_outage_is_said_from_back_after_minutes_counted_in_whole_minutes_half_up(tmp_path):
    rig = Rig(tmp_path)
    outage_from_21_03(rig, until=5)  # down at 21:03 and 21:04

    said = rig.poll(UP, minutes=1.5)  # 21:05:30: 2.5 minutes

    assert said == [
        "Master, internetnya sudah nyambung lagi, tadi putus sekitar 3 menit, sejak jam 21.03."
    ]


def test_without_announce_back_nothing_is_said_but_the_outage_is_kept(tmp_path):
    rig = Rig(tmp_path, NetworkConfig(announce_back=False))
    outage_from_21_03(rig)

    assert rig.poll(UP) == []
    assert rig.record().outages == (Outage(at(21, 3), at(21, 15), "offline"),)
    assert rig.record().back_told is None


def test_at_most_max_back_per_day_lines_are_said_also_across_a_restart(tmp_path):
    config = NetworkConfig(max_back_per_day=1)
    rig = Rig(tmp_path, config)
    outage_from_21_03(rig)
    assert rig.poll(UP) == [BACK]

    restarted = Rig(tmp_path, config)
    restarted.wall = at(22, 0)
    restarted.settle()
    for _ in range(5):
        restarted.poll(DOWN)

    assert restarted.poll(UP) == []
    assert len(restarted.record().outages) == 2


def test_the_count_starts_again_on_a_new_day(tmp_path):
    rig = Rig(tmp_path, NetworkConfig(max_back_per_day=1))
    rig.store.told_back(at(21, 0).date() - timedelta(days=1))
    outage_from_21_03(rig)

    assert rig.poll(UP) == [BACK]


def test_a_failing_announce_is_logged_and_the_watcher_goes_on(tmp_path, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("queue gone")

    rig = Rig(tmp_path, announce=broken)
    outage_from_21_03(rig)

    with caplog.at_level(logging.ERROR, logger="waifu.network_watch"):
        rig.poll(UP)

    assert "Could not say that the internet is back" in caplog.text
    assert rig.record().outages == (Outage(at(21, 3), at(21, 15), "offline"),)


# --- breaks: startup, sleep, clock steps -------------------------------------------------------


def test_the_first_two_polls_of_a_run_never_count(tmp_path):
    # Autostart may run before the network is up.
    rig = Rig(tmp_path)

    for _ in range(3):
        rig.poll(DOWN)  # 20:58 and 20:59 never count; 21:00 is the first that does
    assert rig.state.outage() is None
    rig.poll(DOWN)

    assert rig.state.outage() == CurrentOutage(at(21, 0), "offline")


def test_after_a_sleep_two_polls_never_count(tmp_path):
    rig = Rig(tmp_path)
    rig.settle()

    rig.poll(DOWN, minutes=90)  # the PC woke; the Wi-Fi reconnects
    rig.poll(DOWN)
    assert rig.state.outage() is None
    rig.poll(DOWN)
    rig.poll(DOWN)

    assert rig.state.outage() == CurrentOutage(at(22, 31), "offline")


def test_an_outage_open_across_a_sleep_ends_at_its_last_failed_poll_without_a_line(tmp_path):
    rig = Rig(tmp_path)
    outage_from_21_03(rig, until=11)  # down until 21:10

    said = rig.poll(UP, minutes=120)

    assert said == rig.said == []
    assert rig.state.outage() is None
    assert rig.record().outages == (Outage(at(21, 3), at(21, 10), "offline"),)


@pytest.mark.parametrize(
    ("wall", "mono"),
    [
        (10, 1),  # the clock set forward
        (-5, 1),  # the clock set back
        (1, 10),  # a gap on the monotonic clock only
        (2.5, 0.5),  # the clocks disagree by more than a poll
    ],
)
def test_a_clock_step_or_a_gap_on_either_clock_is_a_break(tmp_path, wall, mono):
    rig = Rig(tmp_path)
    outage_from_21_03(rig, until=6)  # down at 21:03, 21:04, 21:05

    rig.poll(DOWN, minutes=wall, mono_minutes=mono)
    rig.poll(DOWN)

    assert rig.state.outage() is None
    assert rig.record().outages == (Outage(at(21, 3), at(21, 5), "offline"),)


def test_polls_a_little_late_on_both_clocks_are_no_break(tmp_path):
    rig = Rig(tmp_path)
    rig.settle()

    rig.poll(DOWN, minutes=2.9)
    rig.poll(DOWN, minutes=3, mono_minutes=2.5)

    assert rig.state.outage() is not None


def test_an_outage_left_open_by_a_crash_ends_at_its_last_failed_poll_at_the_first_poll(tmp_path):
    rig = Rig(tmp_path)
    rig.store.begin(at(20, 30), at(20, 41), "router")

    said = rig.poll(UP)

    assert said == []
    assert rig.record() == NetworkRecord(outages=(Outage(at(20, 30), at(20, 41), "router"),))


def test_stopping_ends_an_open_outage_at_its_last_failed_poll(tmp_path):
    rig = Rig(tmp_path)
    outage_from_21_03(rig, until=8)

    rig.watcher.stop()

    assert rig.record() == NetworkRecord(outages=(Outage(at(21, 3), at(21, 7), "offline"),))
    assert rig.state.outage() is None
    assert rig.said == []


def test_a_poll_after_stopping_changes_nothing(tmp_path):
    rig = Rig(tmp_path)
    rig.settle()
    rig.watcher.stop()

    for _ in range(4):
        assert rig.poll(DOWN) == []

    assert rig.state.outage() is None
    assert not rig.path.exists()


# --- what each poll checks ---------------------------------------------------------------------


def test_a_poll_checks_without_the_lookup_and_notes_the_state(tmp_path):
    rig = Rig(tmp_path)

    rig.poll(UP)

    assert rig.probe.asked == [{"dns": False, "hoyo": False}]
    assert rig.state.gateway_answered("192.168.1.1")
    assert rig.state.recent_ms() == (24.0,)


def test_the_hoyo_hosts_are_checked_only_while_a_game_runs_at_most_every_hoyo_poll_seconds(
    tmp_path,
):
    rig = Rig(tmp_path)  # hoyo_poll_seconds 150, polls every 60 s
    rig.poll(UP)
    rig.state.observe(True, False)

    for _ in range(6):
        rig.poll(UP)
    rig.state.observe(False, False)
    rig.poll(UP)

    assert [asked["hoyo"] for asked in rig.probe.asked] == [
        False,
        True,
        False,
        False,
        True,
        False,
        False,
        False,
    ]


def test_hoyo_trouble_is_logged_once_when_it_starts_and_when_it_ends(tmp_path, caplog):
    rig = Rig(tmp_path, NetworkConfig(hoyo_poll_seconds=60))
    rig.state.observe(True, False)

    with caplog.at_level(logging.INFO, logger="waifu.network_watch"):
        rig.poll(replace(UP, hoyo_ms=(None, 40.0, 45.0)))
        rig.poll(replace(UP, hoyo_ms=(None, 40.0, 45.0)))
        rig.poll(replace(UP, hoyo_ms=(45.0, 40.0, 50.0)))

    hoyo_lines = [r.getMessage() for r in caplog.records if "HoYo" in r.getMessage()]
    assert hoyo_lines == [
        "The HoYoLAB/HoYoverse servers are in trouble: 2 of 3 answered",
        "The HoYoLAB/HoYoverse servers answer again",
    ]
    assert rig.said == []


def test_a_slow_internet_is_logged_when_it_starts_and_nothing_is_said(tmp_path, caplog):
    rig = Rig(tmp_path, NetworkConfig(slow_window=1))
    rig.poll(UP)

    with caplog.at_level(logging.INFO, logger="waifu.network_watch"):
        rig.poll(replace(UP, anchor_ms=400.0))
        rig.poll(replace(UP, anchor_ms=420.0))
        rig.poll(UP)

    assert [r.getMessage() for r in caplog.records] == [
        "The internet check now finds it slow",
        "The internet check now finds it ok",
    ]
    assert rig.said == []


def test_a_check_that_raises_is_logged_once_and_the_next_poll_goes_on(tmp_path, caplog):
    rig = Rig(tmp_path)

    with caplog.at_level(logging.DEBUG, logger="waifu.network_watch"):
        rig.poll(RuntimeError("unexpected"))
        rig.poll(RuntimeError("unexpected"))
        rig.poll(UP)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and warnings[0].exc_info is not None
    assert "The internet can be checked again" in caplog.text


# --- the thread --------------------------------------------------------------------------------


class ShortWaits:
    """Stands in for threading.Event.wait: records each timeout asked for and waits on the stop
    event a few milliseconds at most."""

    def __init__(self) -> None:
        self.asked: list[float] = []

    def __call__(self, stop: threading.Event, timeout: float) -> bool:
        self.asked.append(timeout)
        return stop.wait(0.005)


class SignallingProbe(FakeProbe):
    def __init__(self) -> None:
        super().__init__()
        self.checked = threading.Event()

    def check(self, *, dns: bool = False, hoyo: bool = False) -> Reading:
        self.checked.set()
        return super().check(dns=dns, hoyo=hoyo)


def watcher_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "network-watcher"]


def test_the_daemon_thread_polls_at_once_and_stop_wakes_it_at_once(tmp_path):
    probe = SignallingProbe()
    store = NetworkStore(tmp_path / "network.json", keep_days=7)
    watcher = NetworkWatcher(
        probe, store, NetworkConfig(), NetworkState(5), announce=None, owner_call="Master"
    )

    watcher.start()
    try:
        assert probe.checked.wait(5)
        [thread] = watcher_threads()
        assert thread.daemon is True
        watcher.start()
        assert len(watcher_threads()) == 1
    finally:
        stopping = time.monotonic()
        watcher.stop()

    assert time.monotonic() - stopping < 1  # although the next poll was a minute away
    assert watcher_threads() == []


def test_the_next_poll_comes_poll_seconds_after_the_last_one_began(tmp_path):
    probe = FakeProbe()
    waits = ShortWaits()
    seconds = iter([1000.0, 1000.0, 1002.5] + [2000.0, 2000.0, 2000.0] * 100)
    watcher = NetworkWatcher(
        probe,
        NetworkStore(tmp_path / "network.json", keep_days=7, now=lambda: START),
        NetworkConfig(poll_seconds=60),
        NetworkState(5),
        announce=None,
        owner_call="Master",
        now=lambda: START,
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

    assert waits.asked[:2] == [57.5, 60.0]  # the first check took 2.5 s
