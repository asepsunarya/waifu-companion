import logging

import pytest

from waifu.config import RoutineConfig, RoutineStep
from waifu.devices.base import DeviceResult
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.routines import describe_routine, routine_problems, run_routine

AC_COMMANDS = ["on", "off", *(f"temp_{degrees}" for degrees in range(16, 31))]


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


def routine(*steps: tuple[str, str] | tuple[str, str, int], description: str = "") -> RoutineConfig:
    return RoutineConfig(description=description, steps=[step(*s) for s in steps])


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

    results = run_routine(registry, routine(("tv", "off"), ("lampu_kamar", "off"), ("tv", "on")))

    assert journal == [("tv", "off"), ("lampu_kamar", "off"), ("tv", "on")]
    assert [result.ok for result in results] == [True, True, True]
    assert results[1].message == "Lampu kamar: perintah 'off' berhasil (simulasi)."


def test_run_routine_sends_a_numbered_command_for_a_value():
    registry, devices = make_house()

    results = run_routine(registry, routine(("ac", "temp", 26)))

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

    results = run_routine(registry, routine(("lampu_kamar", "off"), failing_step, ("tv", "off")))

    assert [result.ok for result in results] == [True, False, True]
    assert message_part in results[1].message
    assert devices["lampu_kamar"].calls == [("off", None)]
    assert devices["ac"].calls == []
    assert devices["tv"].calls == [("off", None)]


def test_run_routine_refuses_a_pc_without_ever_executing_it(caplog):
    registry, devices = make_house()

    with caplog.at_level(logging.WARNING, logger="waifu.routines"):
        results = run_routine(
            registry, routine(("lampu_kamar", "off"), ("komputer", "shutdown"), ("tv", "off"))
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


def test_the_plug_that_powers_the_pc_is_refused_and_never_switched():
    plug = MockDevice("colokan_pc", "Colokan PC", commands=["on", "off"], kind="plug")
    lamp = MockDevice("lampu_kamar", "Lampu kamar", kind="light")
    registry = DeviceRegistry([plug, lamp], pc_power_ids={"colokan_pc"})
    bedtime = routine(("colokan_pc", "off"), ("lampu_kamar", "off"))

    results = run_routine(registry, bedtime)

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
