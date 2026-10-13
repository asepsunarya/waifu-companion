import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from waifu.config import RoutineConfig, RoutineStep
from waifu.devices.base import DeviceError, DeviceResult
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.schedule import DeviceAction, Reminder, RoutineAction, ScheduleStore
from waifu.scheduler import Scheduler

START = datetime(2026, 10, 13, 14, 30)  # a Tuesday
TODAY_15 = datetime(2026, 10, 13, 15, 0)
MEDICINE = Reminder("Master, sudah jam tiga, waktunya minum obat!")
LAUNDRY = Reminder("Master, jemurannya diangkat dulu ya!")
AC_COMMANDS = ["on", "off", *(f"temp_{degrees}" for degrees in range(16, 31))]


class Clock:
    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current


class SilentDevice(MockDevice):
    """Never answers, like an IR blaster that was unplugged."""

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        raise DeviceError(f"{self.name} tidak menjawab.")


class BusyDevice(MockDevice):
    """Runs `while_running` inside every command: the clock moving on during a slow command,
    or Master cancelling another entry meanwhile."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.while_running: Callable[[], None] = lambda: None

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        self.while_running()
        return super().execute(command, value)


class FakeNotifier:
    def __init__(self, outcome: bool | BaseException = True) -> None:
        self.outcome = outcome
        self.sent: list[tuple[str, str, tuple[str, ...]]] = []

    def send(self, title, message, *, tags=()):
        self.sent.append((title, message, tuple(tags)))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class House:
    """A scheduler over a real store in tmp_path and mock devices; nothing real ever runs."""

    def __init__(self, tmp_path, *, store_class=ScheduleStore, **scheduler_kwargs) -> None:
        self.clock = Clock()
        self.devices = {
            "lampu_kamar": MockDevice("lampu_kamar", "Lampu kamar", kind="light"),
            "ac": MockDevice("ac", "AC Samsung", commands=AC_COMMANDS, kind="ir"),
            "tv": SilentDevice("tv", "TV", kind="ir"),
            "lampu_teras": BusyDevice("lampu_teras", "Lampu teras", kind="light"),
            "komputer": MockDevice(
                "komputer", "Komputer", commands=["wake", "shutdown", "sleep"], kind="pc"
            ),
        }
        self.store = store_class(tmp_path / "schedule.json", now=self.clock)
        self.announced: list[tuple[str, str]] = []
        self.notifier = FakeNotifier()
        kwargs = {
            "registry": DeviceRegistry(list(self.devices.values())),
            "routines": {},
            "announce": lambda text, emotion: self.announced.append((text, emotion)),
            "notifier": self.notifier,
            "grace_minutes": 30,
            "now": self.clock,
            **scheduler_kwargs,
        }
        self.scheduler = Scheduler(self.store, **kwargs)

    def add(self, action, at: datetime = TODAY_15, repeat: str = "none"):
        return self.store.add(at, repeat, action)

    def tick_at(self, moment: datetime) -> None:
        self.clock.current = moment
        self.scheduler.tick_once()

    @property
    def told(self) -> tuple[list[tuple[str, str]], list[tuple[str, str, tuple[str, ...]]]]:
        return self.announced, self.notifier.sent


def routine(*steps: tuple) -> RoutineConfig:
    return RoutineConfig(steps=[RoutineStep(device=d, command=c, value=v) for d, c, v in steps])


# --- reminders -----------------------------------------------------------------------------------


def test_a_due_reminder_is_spoken_happily_pushed_and_removed(tmp_path):
    house = House(tmp_path)
    house.add(MEDICINE)

    house.tick_at(datetime(2026, 10, 13, 15, 0, 20))

    assert house.told == (
        [(MEDICINE.text, "happy")],
        [("Cyrene", MEDICINE.text, ("alarm_clock",))],
    )
    assert house.store.entries() == []


def test_nothing_fires_before_its_minute(tmp_path):
    house = House(tmp_path)
    added = house.add(MEDICINE)

    house.tick_at(datetime(2026, 10, 13, 14, 59, 59))

    assert house.told == ([], [])
    assert house.store.entries() == [added]


def test_a_repeating_reminder_fires_once_and_moves_to_its_next_day(tmp_path):
    house = House(tmp_path)
    house.add(MEDICINE, repeat="daily")

    house.tick_at(datetime(2026, 10, 13, 15, 0, 5))
    house.tick_at(datetime(2026, 10, 13, 15, 0, 20))

    assert house.announced == [(MEDICINE.text, "happy")]
    [moved] = house.store.entries()
    assert moved.at == datetime(2026, 10, 14, 15, 0)


@pytest.mark.parametrize(
    ("grace_minutes", "moment"),
    [(30, datetime(2026, 10, 13, 15, 30, 59)), (0, datetime(2026, 10, 13, 15, 0, 59))],
)
def test_an_entry_late_by_at_most_the_grace_still_fires(tmp_path, grace_minutes, moment):
    # Minute precision: the seconds of the clock never count against the grace.
    house = House(tmp_path, grace_minutes=grace_minutes)
    house.add(MEDICINE)

    house.tick_at(moment)

    assert house.announced == [(MEDICINE.text, "happy")]
    assert house.store.entries() == []


def test_a_reminder_missed_beyond_the_grace_is_pushed_as_missed_but_not_spoken(tmp_path):
    house = House(tmp_path)
    house.add(MEDICINE)

    house.tick_at(datetime(2026, 10, 13, 15, 31))

    assert house.told == (
        [],
        [("Cyrene", f"Terlewat (15.00): {MEDICINE.text}", ("alarm_clock",))],
    )
    assert house.store.entries() == []


# --- device actions ------------------------------------------------------------------------------


def test_a_device_action_runs_without_a_word(tmp_path):
    house = House(tmp_path)
    house.add(DeviceAction("ac", "temp", 26))

    house.tick_at(TODAY_15)

    assert house.devices["ac"].calls == [("temp_26", 26)]
    assert house.told == ([], [])
    assert house.store.entries() == []


@pytest.mark.parametrize(
    ("action", "expected_start"),
    [
        (DeviceAction("tv", "off"), "Master, jadwal tv off jam 15.00 gagal: TV tidak menjawab."),
        (
            DeviceAction("ac", "temp", 99),
            "Master, jadwal ac temp 99 jam 15.00 gagal: AC Samsung tidak punya perintah 'temp' "
            "dengan nilai 99.",
        ),
        (
            DeviceAction("kulkas", "on"),
            "Master, jadwal kulkas on jam 15.00 gagal: Perangkat 'kulkas' tidak dikenal.",
        ),
    ],
)
def test_a_failed_device_action_is_spoken_sadly_and_pushed(tmp_path, action, expected_start):
    house = House(tmp_path)
    house.add(action)

    house.tick_at(TODAY_15)

    [(spoken, emotion)] = house.announced
    assert spoken.startswith(expected_start)
    assert emotion == "sad"
    assert house.notifier.sent == [("Cyrene", spoken, ("alarm_clock",))]
    assert house.store.entries() == []


def test_the_pc_is_refused_at_run_time_and_never_executed(tmp_path, caplog):
    house = House(tmp_path)
    house.add(DeviceAction("komputer", "shutdown"))

    with caplog.at_level(logging.WARNING, logger="waifu.scheduler"):
        house.tick_at(TODAY_15)

    assert house.devices["komputer"].calls == []
    text = (
        "Master, jadwal komputer shutdown jam 15.00 gagal: Komputer adalah PC, dan PC tidak "
        "pernah dijalankan dari jadwal."
    )
    assert house.told == ([(text, "sad")], [("Cyrene", text, ("alarm_clock",))])
    assert "komputer" in caplog.text
    assert house.store.entries() == []


# --- routines ------------------------------------------------------------------------------------


def test_a_routine_runs_every_step_without_a_word(tmp_path):
    tidur = routine(("lampu_kamar", "off", None), ("ac", "temp", 26))
    house = House(tmp_path, routines={"tidur": tidur})
    house.add(RoutineAction("tidur"))

    house.tick_at(TODAY_15)

    assert house.devices["lampu_kamar"].calls == [("off", None)]
    assert house.devices["ac"].calls == [("temp_26", 26)]
    assert house.told == ([], [])
    assert house.store.entries() == []


def test_a_routine_with_failed_steps_names_each_of_them(tmp_path):
    tidur = routine(("lampu_kamar", "off", None), ("tv", "off", None), ("komputer", "sleep", None))
    house = House(tmp_path, routines={"tidur": tidur})
    house.add(RoutineAction("tidur"))

    house.tick_at(TODAY_15)

    text = (
        "Master, jadwal rutinitas tidur jam 15.00 gagal: Langkah 2 (tv off): TV tidak menjawab. "
        "Langkah 3 (komputer sleep): Komputer adalah PC, dan PC tidak pernah dijalankan dari "
        "rutinitas."
    )
    assert house.told == ([(text, "sad")], [("Cyrene", text, ("alarm_clock",))])
    assert house.devices["lampu_kamar"].calls == [("off", None)]
    assert house.devices["komputer"].calls == []


def test_a_routine_missing_from_the_config_is_a_failure(tmp_path):
    house = House(tmp_path)
    house.add(RoutineAction("pesta"))

    house.tick_at(TODAY_15)

    text = (
        "Master, jadwal rutinitas pesta jam 15.00 gagal: Rutinitas 'pesta' tidak ada di "
        "config.yaml."
    )
    assert house.told == ([(text, "sad")], [("Cyrene", text, ("alarm_clock",))])
    assert house.store.entries() == []


# --- skipped entries -----------------------------------------------------------------------------


def test_a_missed_one_shot_action_is_removed_without_running(tmp_path, caplog):
    house = House(tmp_path)
    house.add(DeviceAction("ac", "off"))

    with caplog.at_level(logging.WARNING, logger="waifu.scheduler"):
        house.tick_at(datetime(2026, 10, 14, 9, 0))

    assert house.devices["ac"].calls == []
    assert house.told == ([], [])
    assert house.store.entries() == []
    assert "j1" in caplog.text


def test_a_missed_repeating_entry_moves_on_without_running(tmp_path):
    house = House(tmp_path)
    house.add(DeviceAction("ac", "off"), repeat="daily")

    house.tick_at(datetime(2026, 10, 14, 9, 0))  # the PC was off from 14:59 until morning

    assert house.devices["ac"].calls == []
    assert house.told == ([], [])
    [moved] = house.store.entries()
    assert moved.at == datetime(2026, 10, 14, 15, 0)


def test_a_reminder_missed_on_an_earlier_day_is_pushed_with_its_date(tmp_path):
    house = House(tmp_path)
    house.add(MEDICINE)

    house.tick_at(datetime(2026, 10, 14, 9, 0))

    assert house.told == (
        [],
        [("Cyrene", f"Terlewat (13-10-2026 15.00): {MEDICINE.text}", ("alarm_clock",))],
    )


WAKE_UP = Reminder("Master, bangun!")


@pytest.mark.parametrize(
    ("repeat", "stored", "back", "missed_day", "following"),
    [
        # Fired on Friday at 07:00, so it waits for Saturday; the PC stays off until Monday.
        (
            "daily",
            datetime(2026, 10, 10, 7, 0),
            datetime(2026, 10, 12, 7, 10, 5),
            "10-10-2026",
            datetime(2026, 10, 13, 7, 0),
        ),
        # Fired on Friday, so it waits for Monday; Master is away until Wednesday.
        (
            "weekdays",
            datetime(2026, 10, 12, 7, 0),
            datetime(2026, 10, 14, 7, 10, 5),
            "12-10-2026",
            datetime(2026, 10, 15, 7, 0),
        ),
    ],
    ids=["daily, back on Monday", "weekdays, back on Wednesday"],
)
def test_after_days_away_today_s_occurrence_within_the_grace_still_fires_once(
    tmp_path, repeat, stored, back, missed_day, following
):
    house = House(tmp_path)
    house.clock.current = stored - timedelta(days=1)
    house.add(WAKE_UP, at=stored, repeat=repeat)
    house.add(DeviceAction("lampu_kamar", "on"), at=stored, repeat=repeat)

    house.tick_at(back)  # ten minutes after today's 07:00, within the 30 minutes of grace
    house.tick_at(back + timedelta(seconds=15))

    # The occurrence missed days ago is only pushed as missed, with its date; today's runs.
    assert house.told == (
        [(WAKE_UP.text, "happy")],
        [
            ("Cyrene", f"Terlewat ({missed_day} 07.00): {WAKE_UP.text}", ("alarm_clock",)),
            ("Cyrene", WAKE_UP.text, ("alarm_clock",)),
        ],
    )
    assert house.devices["lampu_kamar"].calls == [("on", None)]
    assert [entry.at for entry in house.store.entries()] == [following, following]


# --- a busy tick ---------------------------------------------------------------------------------


def test_an_entry_due_while_a_slow_routine_runs_still_fires_also_with_no_grace(tmp_path):
    # Unreachable lamps take minutes to fail; Cyrene was busy, not away.
    def slow() -> None:
        house.clock.current += timedelta(seconds=80)

    tidur = routine(("lampu_teras", "off", None), ("lampu_teras", "off", None))
    house = House(tmp_path, routines={"tidur": tidur}, grace_minutes=0)
    house.devices["lampu_teras"].while_running = slow
    house.add(RoutineAction("tidur"), at=datetime(2026, 10, 13, 23, 0))
    house.add(MEDICINE, at=datetime(2026, 10, 13, 23, 1))

    house.tick_at(datetime(2026, 10, 13, 23, 0, 5))  # the routine runs until 23:02:45
    house.tick_at(datetime(2026, 10, 13, 23, 3))

    assert house.told == (
        [(MEDICINE.text, "happy")],
        [("Cyrene", MEDICINE.text, ("alarm_clock",))],
    )
    assert house.store.entries() == []


def test_an_entry_cancelled_while_an_earlier_one_runs_never_runs(tmp_path):
    # "Batalkan jadwal AC", said while the lamp scheduled for the same minute switches off.
    house = House(tmp_path)
    house.add(DeviceAction("lampu_teras", "off"))
    house.add(DeviceAction("ac", "off"))
    house.devices["lampu_teras"].while_running = lambda: house.store.cancel("j2")

    house.tick_at(TODAY_15)

    assert house.devices["lampu_teras"].calls == [("off", None)]
    assert house.devices["ac"].calls == []
    assert house.store.entries() == []


def test_a_time_in_the_current_minute_fires_at_the_next_tick(tmp_path):
    # "Matikan lampu satu menit lagi" at 14:30:50; the tool call lands at 14:31:02.
    house = House(tmp_path)
    house.clock.current = datetime(2026, 10, 13, 14, 31, 2)
    house.add(DeviceAction("lampu_kamar", "off"), at=datetime(2026, 10, 13, 14, 31))

    house.tick_at(datetime(2026, 10, 13, 14, 31, 10))

    assert house.devices["lampu_kamar"].calls == [("off", None)]
    assert house.store.entries() == []


def test_a_time_in_the_current_minute_made_after_its_last_check_fires_also_with_no_grace(
    tmp_path,
):
    # The last check of 14:31 was at 14:31:50; the entry for 14:31 arrives at 14:31:55.
    house = House(tmp_path, grace_minutes=0)
    house.tick_at(datetime(2026, 10, 13, 14, 31, 50))
    house.clock.current = datetime(2026, 10, 13, 14, 31, 55)
    house.add(DeviceAction("lampu_kamar", "off"), at=datetime(2026, 10, 13, 14, 31))

    house.tick_at(datetime(2026, 10, 13, 14, 32, 5))

    assert house.devices["lampu_kamar"].calls == [("off", None)]
    assert house.store.entries() == []


# --- failures never stop the rest ----------------------------------------------------------------


def test_a_failing_entry_never_stops_the_next_one(tmp_path, caplog):
    announced: list[str] = []

    def announce(text: str, emotion: str) -> None:
        if not announced:
            announced.append("exploded")
            raise RuntimeError("the speaker exploded")
        announced.append(text)

    house = House(tmp_path, announce=announce)
    house.add(MEDICINE)
    house.add(LAUNDRY)

    with caplog.at_level(logging.ERROR, logger="waifu.scheduler"):
        house.tick_at(TODAY_15)

    assert announced == ["exploded", LAUNDRY.text]
    assert house.store.entries() == []  # the failed one is marked too, never retried every tick
    assert "j1" in caplog.text and "the speaker exploded" in caplog.text


@pytest.mark.parametrize("outcome", [False, OSError("no internet")])
def test_a_notifier_that_fails_or_raises_never_stops_anything(tmp_path, outcome):
    house = House(tmp_path)
    house.notifier.outcome = outcome
    house.add(MEDICINE)
    house.add(LAUNDRY)
    house.add(DeviceAction("ac", "on"))

    house.tick_at(TODAY_15)

    assert house.announced == [(MEDICINE.text, "happy"), (LAUNDRY.text, "happy")]
    assert house.devices["ac"].calls == [("on", None)]
    assert house.store.entries() == []


class FlakyStore(ScheduleStore):
    """Cannot save the first `fired` mark, like a file another process holds open."""

    failures = 1

    def fired(self, entry_id, at):
        if self.failures:
            self.failures -= 1
            raise PermissionError("schedule.json is in use")
        return super().fired(entry_id, at)


def test_an_occurrence_whose_mark_was_not_saved_is_marked_again_but_never_rerun(tmp_path, caplog):
    house = House(tmp_path, store_class=FlakyStore)
    house.add(MEDICINE)

    with caplog.at_level(logging.ERROR, logger="waifu.scheduler"):
        house.tick_at(datetime(2026, 10, 13, 15, 0, 5))
    unmarked = house.store.entries()
    house.tick_at(datetime(2026, 10, 13, 15, 0, 20))

    assert [entry.id for entry in unmarked] == ["j1"]
    assert "schedule.json is in use" in caplog.text
    assert house.announced == [(MEDICINE.text, "happy")]
    assert house.store.entries() == []


def test_without_a_voice_or_a_notifier_it_still_runs_and_marks(tmp_path):
    house = House(tmp_path, announce=None, notifier=None)
    house.add(MEDICINE)
    house.add(DeviceAction("tv", "off"))
    house.add(DeviceAction("ac", "on"))

    house.tick_at(TODAY_15)

    assert house.devices["ac"].calls == [("on", None)]
    assert house.store.entries() == []


# --- the thread ----------------------------------------------------------------------------------


def test_the_thread_ticks_at_once_and_stop_wakes_it_at_once(tmp_path):
    spoken = threading.Event()
    house = House(tmp_path, announce=lambda text, emotion: spoken.set(), tick_seconds=3600)
    house.add(MEDICINE)
    house.clock.current = TODAY_15

    with house.scheduler:
        assert spoken.wait(5)
        stopping = time.monotonic()
    # Leaving the block stopped it although the next tick was an hour away.

    assert time.monotonic() - stopping < 1
    assert not any(thread.name == "scheduler" for thread in threading.enumerate())


class HiccupStore(ScheduleStore):
    """Cannot read the file once, then works."""

    hiccups = 1

    def entries(self):
        if self.hiccups:
            self.hiccups -= 1
            raise OSError("the disk hiccupped")
        return super().entries()


def test_a_failing_tick_is_logged_and_the_thread_goes_on(tmp_path, caplog):
    spoken: list[str] = []
    told = threading.Event()

    def announce(text: str, emotion: str) -> None:
        spoken.append(text)
        told.set()

    house = House(tmp_path, store_class=HiccupStore, announce=announce, tick_seconds=0)
    house.add(MEDICINE)
    house.clock.current = TODAY_15

    with caplog.at_level(logging.ERROR, logger="waifu.scheduler"), house.scheduler:
        assert told.wait(5)

    assert spoken == [MEDICINE.text]
    assert "the disk hiccupped" in caplog.text
    assert not any(thread.name == "scheduler" for thread in threading.enumerate())
