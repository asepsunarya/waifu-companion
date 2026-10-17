import logging
from datetime import datetime

import pytest

from waifu.config import RoutineConfig, RoutineStep
from waifu.devices.base import DeviceResult
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.routines import describe_routine, describe_step, routine_problems, run_routine
from waifu.schedule import DeviceAction, Entry, Reminder, RoutineAction, ScheduleStore

AC_COMMANDS = ["on", "off", *(f"temp_{degrees}" for degrees in range(16, 31))]
BEDTIME = datetime(2026, 10, 17, 22, 0, 40)  # a Saturday night
AFTERNOON = datetime(2026, 10, 17, 14, 30, 40)


class JournalDevice(MockDevice):
    """A MockDevice that also writes every command into a journal shared by the house."""

    def __init__(self, journal: list[tuple[str, str]], *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.journal = journal

    def execute(self, command: str, value: int | None = None) -> DeviceResult:
        self.journal.append((self.id, command))
        return super().execute(command, value)


def step(device: str, command: str, value: int | None = None) -> RoutineStep:
    return RoutineStep(device=device, command=command, value=value)


def later(device: str, command: str, after: int, value: int | None = None) -> RoutineStep:
    """A step that waits `after` minutes after the routine starts."""
    return RoutineStep(device=device, command=command, value=value, after_minutes=after)


def routine(*steps: tuple | RoutineStep, description: str = "") -> RoutineConfig:
    return RoutineConfig(
        description=description,
        steps=[s if isinstance(s, RoutineStep) else step(*s) for s in steps],
    )


def mode_tidur() -> RoutineConfig:
    """Lamp off and AC to 26 now, the AC off three hours later."""
    return routine(("lampu_kamar", "off"), ("ac", "temp", 26), later("ac", "off", 180))


def make_store(tmp_path, at: datetime = BEDTIME, **kwargs) -> ScheduleStore:
    return ScheduleStore(tmp_path / "schedule.json", now=lambda: at, **kwargs)


def run_at(at: datetime, registry, routine_config, store, name: str = "tidur"):
    return run_routine(registry, routine_config, name=name, schedule=store, now=lambda: at)


def make_house() -> tuple[DeviceRegistry, dict[str, MockDevice]]:
    devices = {
        "lampu_kamar": MockDevice("lampu_kamar", "Lampu kamar", kind="light"),
        "ac": MockDevice("ac", "AC Samsung", commands=AC_COMMANDS, kind="ir"),
        "tv": MockDevice("tv", "TV", kind="ir"),
        "komputer": MockDevice(
            "komputer", "Komputer", commands=["wake", "shutdown", "restart", "sleep"], kind="pc"
        ),
    }
    return DeviceRegistry(list(devices.values())), devices


def test_run_routine_runs_every_step_in_order():
    journal: list[tuple[str, str]] = []
    lamp = JournalDevice(journal, "lampu_kamar", "Lampu kamar")
    tv = JournalDevice(journal, "tv", "TV")
    registry = DeviceRegistry([lamp, tv])

    results = run_routine(
        registry, routine(("tv", "off"), ("lampu_kamar", "off"), ("tv", "on")), name="tv"
    )

    assert journal == [("tv", "off"), ("lampu_kamar", "off"), ("tv", "on")]
    assert [result.ok for result in results] == [True, True, True]
    assert results[1].message == "Lampu kamar: perintah 'off' berhasil (simulasi)."


def test_run_routine_sends_a_numbered_command_for_a_value():
    registry, devices = make_house()

    results = run_routine(registry, routine(("ac", "temp", 26)), name="dingin")

    assert [result.ok for result in results] == [True]
    assert devices["ac"].calls == [("temp_26", 26)]


@pytest.mark.parametrize(
    ("failing_step", "message_part"),
    [
        (("ac", "temp", 99), "AC Samsung tidak punya perintah 'temp' dengan nilai 99"),
        (("kulkas", "on"), "Perangkat 'kulkas' tidak dikenal"),
    ],
)
def test_run_routine_continues_after_a_failed_step(failing_step, message_part):
    registry, devices = make_house()

    results = run_routine(
        registry, routine(("lampu_kamar", "off"), failing_step, ("tv", "off")), name="tidur"
    )

    assert [result.ok for result in results] == [True, False, True]
    assert message_part in results[1].message
    assert devices["lampu_kamar"].calls == [("off", None)]
    assert devices["ac"].calls == []
    assert devices["tv"].calls == [("off", None)]


def test_run_routine_refuses_a_pc_without_ever_executing_it(caplog):
    registry, devices = make_house()

    with caplog.at_level(logging.WARNING, logger="waifu.routines"):
        results = run_routine(
            registry,
            routine(("lampu_kamar", "off"), ("komputer", "shutdown"), ("tv", "off")),
            name="tidur",
        )

    assert results[1] == DeviceResult(
        ok=False, message="Komputer adalah PC, dan PC tidak pernah dijalankan dari rutinitas."
    )
    assert devices["komputer"].calls == []
    assert [results[0].ok, results[2].ok] == [True, True]
    assert devices["tv"].calls == [("off", None)]
    assert "komputer" in caplog.text


def test_describe_routine_names_the_routine_its_description_and_steps():
    tidur = routine(("lampu_kamar", "off"), ("ac", "temp", 26), description="Mode tidur")

    assert describe_routine("tidur", tidur) == "tidur: Mode tidur (lampu_kamar off, ac temp 26)"


def test_describe_routine_without_a_description_lists_just_the_steps():
    pagi = routine(("lampu_kamar", "on"), ("tv", "on"))

    assert describe_routine("pagi", pagi) == "pagi: lampu_kamar on, tv on"


def test_routine_problems_is_empty_for_a_routine_that_resolves():
    registry, _ = make_house()
    # "on" with a value still resolves to the bare command, as registry.execute does.
    tidur = routine(("lampu_kamar", "off"), ("ac", "temp", 26), ("tv", "on", 5))

    assert routine_problems(registry, tidur) == []


def test_routine_problems_names_every_bad_step_in_order():
    registry, devices = make_house()
    broken = routine(
        ("lampu_kamar", "off"),
        ("kulkas", "on"),
        ("komputer", "sleep"),
        ("ac", "temp", 99),
        ("tv", "explode"),
    )

    assert routine_problems(registry, broken) == [
        "Langkah 2 (kulkas on): perangkat 'kulkas' tidak dikenal.",
        "Langkah 3 (komputer sleep): Komputer adalah PC, dan PC tidak pernah dijalankan dari "
        "rutinitas.",
        "Langkah 4 (ac temp 99): AC Samsung tidak punya perintah 'temp' dengan nilai 99.",
        "Langkah 5 (tv explode): TV tidak punya perintah 'explode'.",
    ]
    assert all(device.calls == [] for device in devices.values())


def test_routine_problems_names_a_value_outside_its_kind_range():
    musik = MockDevice(
        "musik", "Musik PC", commands=["stop", "volume_up", "volume_down", "volume"], kind="media"
    )
    registry = DeviceRegistry([musik])
    tidur = routine(("musik", "stop"), ("musik", "volume_down", 300), ("musik", "volume"))

    assert routine_problems(registry, tidur) == [
        "Langkah 2 (musik volume_down 300): Nilai 'volume_down' untuk Musik PC harus angka 1 "
        "sampai 100.",
        "Langkah 3 (musik volume): Nilai 'volume' untuk Musik PC harus angka 0 sampai 100.",
    ]
    assert musik.calls == []


def test_the_plug_that_powers_the_pc_is_refused_and_never_switched():
    plug = MockDevice("colokan_pc", "Colokan PC", commands=["on", "off"], kind="plug")
    lamp = MockDevice("lampu_kamar", "Lampu kamar", kind="light")
    registry = DeviceRegistry([plug, lamp], pc_power_ids={"colokan_pc"})
    bedtime = routine(("colokan_pc", "off"), ("lampu_kamar", "off"))

    results = run_routine(registry, bedtime, name="tidur")

    assert plug.calls == []
    assert [result.ok for result in results] == [False, True]
    assert results[0].message == (
        "Colokan PC mengalirkan listrik ke PC, jadi tidak pernah dijalankan "
        "dari rutinitas: mematikannya sama dengan mencabut kabel PC."
    )
    [problem] = routine_problems(registry, bedtime)
    assert problem == (
        "Langkah 1 (colokan_pc off): Colokan PC mengalirkan listrik ke PC, jadi "
        "tidak pernah dijalankan dari rutinitas: mematikannya sama dengan "
        "mencabut kabel PC."
    )


# --- delayed steps (mode tidur) ------------------------------------------------------------------


def test_a_delayed_step_is_scheduled_once_for_its_delay_and_never_run_now(tmp_path):
    registry, devices = make_house()
    store = make_store(tmp_path)

    results = run_at(BEDTIME, registry, mode_tidur(), store)

    assert devices["lampu_kamar"].calls == [("off", None)]
    assert devices["ac"].calls == [("temp_26", 26)]
    assert [(entry.id, entry.at, entry.repeat, entry.action) for entry in store.entries()] == [
        ("j1", datetime(2026, 10, 18, 1, 0), "none", DeviceAction("ac", "off", routine="tidur"))
    ]
    assert results[2] == DeviceResult(
        ok=True,
        message="Dijadwalkan besok jam 01.00 (j1).",
        state={"id": "j1", "at": "2026-10-18 01:00"},
    )
    assert [result.ok for result in results] == [True, True, True]


def test_a_delayed_step_later_the_same_day_names_just_the_clock_time(tmp_path):
    registry, _ = make_house()
    store = make_store(tmp_path, AFTERNOON)

    [result] = run_at(
        AFTERNOON, registry, routine(later("ac", "temp", 90, value=24)), store, "sore"
    )

    assert result.message == "Dijadwalkan jam 16.00 (j1)."
    assert result.state == {"id": "j1", "at": "2026-10-17 16:00"}
    [entry] = store.entries()
    assert entry.action == DeviceAction("ac", "temp", 24, routine="sore")


def test_every_delay_counts_from_the_start_of_the_routine(tmp_path):
    registry, devices = make_house()
    store = make_store(tmp_path)
    malam = routine(
        later("tv", "off", 120), later("lampu_kamar", "off", 60), later("ac", "off", 60)
    )

    run_at(BEDTIME, registry, malam, store, "malam")

    assert [(entry.at, entry.action.device) for entry in store.entries()] == [
        (datetime(2026, 10, 17, 23, 0), "lampu_kamar"),
        (datetime(2026, 10, 17, 23, 0), "ac"),
        (datetime(2026, 10, 18, 0, 0), "tv"),
    ]
    assert all(device.calls == [] for device in devices.values())


@pytest.mark.parametrize(
    ("bad_step", "problem"),
    [
        (later("kulkas", "off", 60), "perangkat 'kulkas' tidak dikenal."),
        (
            later("komputer", "sleep", 60),
            "Komputer adalah PC, dan PC tidak pernah dijalankan dari rutinitas.",
        ),
        (
            later("ac", "temp", 60, value=99),
            "AC Samsung tidak punya perintah 'temp' dengan nilai 99.",
        ),
    ],
    ids=["an unknown device", "the PC", "an unknown command"],
)
def test_a_delayed_step_that_could_never_run_is_not_scheduled(tmp_path, bad_step, problem):
    registry, devices = make_house()
    store = make_store(tmp_path)

    results = run_at(BEDTIME, registry, routine(("lampu_kamar", "off"), bad_step), store)

    assert results[1] == DeviceResult(ok=False, message=problem)
    assert results[0].ok is True
    assert devices["komputer"].calls == []
    assert store.entries() == []


class UnreadableCommands(MockDevice):
    """An IR device whose learned codes cannot be read right now."""

    def commands(self) -> list[str]:
        raise OSError("ir_codes.json is locked")


def test_a_delayed_step_whose_commands_cannot_be_read_is_not_scheduled(tmp_path, caplog):
    registry = DeviceRegistry([UnreadableCommands("ac", "AC Samsung", kind="ir")])
    store = make_store(tmp_path)

    with caplog.at_level(logging.ERROR, logger="waifu.routines"):
        [result] = run_at(BEDTIME, registry, routine(later("ac", "off", 180)), store)

    assert result == DeviceResult(
        ok=False,
        message="Perintah AC Samsung tidak bisa diperiksa sekarang, jadi langkah ini tidak "
        "dijadwalkan.",
    )
    assert store.entries() == []
    assert "ir_codes.json is locked" in caplog.text


def test_a_full_schedule_fails_only_the_delayed_step(tmp_path):
    registry, devices = make_house()
    store = make_store(tmp_path, max_entries=1)
    store.add(datetime(2026, 10, 18, 7, 0), "daily", Reminder("Master, bangun!"))

    results = run_at(BEDTIME, registry, mode_tidur(), store)

    assert results[2] == DeviceResult(
        ok=False, message="Jadwal sudah penuh, 1 entri. Batalkan jadwal lama dulu."
    )
    assert devices["ac"].calls == [("temp_26", 26)]
    assert [entry.action for entry in store.entries()] == [Reminder("Master, bangun!")]


def test_a_schedule_file_that_cannot_be_opened_fails_only_the_delayed_step(tmp_path, caplog):
    registry, devices = make_house()
    store = make_store(tmp_path)
    store.path.mkdir()  # a folder where the file should be

    with caplog.at_level(logging.ERROR, logger="waifu.routines"):
        results = run_at(BEDTIME, registry, mode_tidur(), store)

    assert results[2] == DeviceResult(
        ok=False, message="File jadwal tidak bisa dibuka, jadi langkah ini tidak dijadwalkan."
    )
    assert [results[0].ok, results[1].ok] == [True, True]
    assert devices["ac"].calls == [("temp_26", 26)]
    assert "tidur" in caplog.text


def test_without_a_schedule_a_delayed_step_fails_and_never_runs():
    registry, devices = make_house()

    results = run_routine(registry, mode_tidur(), name="tidur", now=lambda: BEDTIME)

    assert results[2] == DeviceResult(
        ok=False,
        message="Langkah yang ditunda butuh jadwal, tapi jadwal mati (schedule.enabled: false).",
    )
    assert devices["ac"].calls == [("temp_26", 26)]


def test_a_new_run_replaces_only_the_pending_delayed_steps_of_its_routine(tmp_path, caplog):
    registry, _ = make_house()
    store = make_store(tmp_path)
    own = store.add(datetime(2026, 10, 18, 2, 0), "none", DeviceAction("ac", "off"))
    other = store.add(
        datetime(2026, 10, 18, 1, 0), "none", DeviceAction("tv", "off", routine="film")
    )
    scheduled_routine = store.add(datetime(2026, 10, 18, 23, 0), "daily", RoutineAction("tidur"))
    run_at(BEDTIME, registry, mode_tidur(), store)
    again = datetime(2026, 10, 17, 23, 0, 5)

    with caplog.at_level(logging.INFO, logger="waifu.routines"):
        results = run_at(again, registry, mode_tidur(), make_store(tmp_path, again))

    assert results[2].state == {"id": "j5", "at": "2026-10-18 02:00"}
    assert store.entries() == [
        other,
        own,
        Entry(
            "j5",
            datetime(2026, 10, 18, 2, 0),
            "none",
            DeviceAction("ac", "off", routine="tidur"),
            datetime(2026, 10, 17, 23, 0),
        ),
        scheduled_routine,
    ]
    assert "j4" in caplog.text


def test_a_routine_without_delayed_steps_never_touches_the_schedule(tmp_path):
    registry, devices = make_house()
    store = make_store(tmp_path)

    results = run_at(BEDTIME, registry, routine(("lampu_kamar", "off")), store)

    assert [result.ok for result in results] == [True]
    assert devices["lampu_kamar"].calls == [("off", None)]
    assert not store.path.exists()


@pytest.mark.parametrize(
    ("step_config", "text"),
    [
        (step("ac", "off"), "ac off"),
        (step("ac", "temp", 26), "ac temp 26"),
        (later("ac", "off", 180), "ac off setelah 3 jam"),
        (later("ac", "off", 90), "ac off setelah 1 jam 30 menit"),
        (later("ac", "off", 45), "ac off setelah 45 menit"),
        (later("ac", "temp", 120, value=28), "ac temp 28 setelah 2 jam"),
    ],
)
def test_describe_step_names_the_command_and_its_delay(step_config, text):
    assert describe_step(step_config) == text


def test_describe_routine_names_the_delayed_step():
    tidur = routine(*mode_tidur().steps, description="Mode tidur")

    assert describe_routine("tidur", tidur) == (
        "tidur: Mode tidur (lampu_kamar off, ac temp 26, ac off setelah 3 jam)"
    )


def test_routine_problems_names_a_delayed_step_with_its_delay():
    registry, _ = make_house()

    assert routine_problems(registry, routine(later("kulkas", "off", 180))) == [
        "Langkah 1 (kulkas off setelah 3 jam): perangkat 'kulkas' tidak dikenal."
    ]
