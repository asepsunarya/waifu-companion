"""Game mode: the routines run when Master starts and stops playing, and whether her own
announcements wait. No test runs a real device (MockDevice registries), sleeps (a fake clock),
or touches data/schedule.json (every schedule lives in tmp_path)."""

import logging
from datetime import datetime, timedelta

import pytest

from waifu.config import GameModeConfig, RoutineConfig, RoutineStep
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.game_mode import GameMode
from waifu.schedule import DeviceAction, ScheduleStore

EVENING = datetime(2026, 10, 17, 20, 0)  # the PC's local clock, naive like the scheduler's
POLL = timedelta(seconds=60)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def steps(*commands: tuple[str, str] | RoutineStep) -> RoutineConfig:
    return RoutineConfig(
        steps=[
            command
            if isinstance(command, RoutineStep)
            else RoutineStep(device=command[0], command=command[1])
            for command in commands
        ]
    )


ROUTINES = {
    "redup": steps(("lampu_kamar", "off")),
    "terang": steps(("lampu_kamar", "on")),
}


class Rig:
    """A game mode over a house of mock devices, a fake clock, and an optional schedule."""

    def __init__(self, tmp_path, *, routines=ROUTINES, **config):
        config = {"on_start": "redup", "on_stop": "terang", **config}
        self.clock = Clock(EVENING)
        self.lamp = MockDevice("lampu_kamar", "Lampu kamar", kind="light")
        self.ac = MockDevice("ac", "AC", commands=["on", "off"], kind="ir")
        self.schedule = ScheduleStore(tmp_path / "schedule.json", now=self.clock)
        self.mode = GameMode(
            GameModeConfig(**config),
            registry=DeviceRegistry([self.lamp, self.ac]),
            routines=routines,
            schedule=self.schedule,
            poll=POLL,
            now=self.clock,
        )

    def observe(self, *polls: bool, resumed: bool = False) -> None:
        """One observation a minute apart per poll; only the first may be `resumed`."""
        for number, playing in enumerate(polls):
            self.mode.observe(playing, resumed and number == 0)
            self.clock.now += POLL

    def lamp_commands(self) -> list[str]:
        return [command for command, _ in self.lamp.calls]


# --- the routines ----------------------------------------------------------------------------


def test_the_first_game_opening_runs_the_start_routine_once(tmp_path):
    rig = Rig(tmp_path)

    rig.observe(False, True, True, True)

    assert rig.lamp_commands() == ["off"]


def test_the_last_game_closing_runs_the_stop_routine_once(tmp_path):
    rig = Rig(tmp_path)
    rig.observe(False, True, True)

    rig.observe(False, False, False)

    assert rig.lamp_commands() == ["off", "on"]


def test_every_start_and_stop_of_an_evening_runs_its_routine_once(tmp_path):
    rig = Rig(tmp_path)

    rig.observe(False, True, False, False, True, True, False)

    assert rig.lamp_commands() == ["off", "on", "off", "on"]


def test_a_game_already_open_at_the_run_s_first_poll_runs_the_start_routine(tmp_path):
    rig = Rig(tmp_path)

    rig.observe(True, True)

    assert rig.lamp_commands() == ["off"]


def test_a_restart_mid_game_does_not_run_the_start_routine_again_but_the_close_runs_stop(
    tmp_path,
):
    rig = Rig(tmp_path)

    rig.observe(True, True, False, resumed=True)

    assert rig.lamp_commands() == ["on"]


def test_a_first_poll_without_a_game_runs_nothing(tmp_path):
    # Whatever the room was left as, only a game closing runs the stop routine.
    rig = Rig(tmp_path)

    rig.observe(False, False)

    assert rig.lamp.calls == []


def test_without_routines_a_game_starting_and_stopping_runs_nothing(tmp_path):
    rig = Rig(tmp_path, on_start=None, on_stop=None, announcements="hold")

    rig.observe(False, True, False)

    assert rig.lamp.calls == []


def test_only_the_routine_set_runs(tmp_path):
    rig = Rig(tmp_path, on_start=None)

    rig.observe(False, True, False)

    assert rig.lamp_commands() == ["on"]


def test_a_delayed_step_of_the_start_routine_goes_into_the_schedule(tmp_path):
    later = RoutineStep(device="ac", command="off", after_minutes=180)
    rig = Rig(tmp_path, routines={**ROUTINES, "redup": steps(("lampu_kamar", "off"), later)})

    rig.observe(False, True)

    assert rig.ac.calls == []
    assert [(entry.at, entry.action) for entry in rig.schedule.entries()] == [
        (EVENING + POLL + timedelta(minutes=180), DeviceAction("ac", "off", routine="redup"))
    ]


def test_a_failed_step_is_logged_and_the_others_still_run(tmp_path, caplog):
    routine = steps(("lampu_kamar", "dim"), ("ac", "on"))
    rig = Rig(tmp_path, routines={**ROUTINES, "redup": routine})

    with caplog.at_level(logging.WARNING, logger="waifu.game_mode"):
        rig.observe(False, True)

    assert rig.ac.calls == [("on", None)]
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage().startswith(
        "The game start routine 'redup' failed at step 1 (lampu_kamar dim): Lampu kamar tidak "
        "punya perintah 'dim'"
    )


def test_starts_stops_and_their_routines_stay_out_of_the_info_log(tmp_path, caplog):
    # When Master plays is his own: as the play time watcher's sessions, DEBUG only.
    rig = Rig(tmp_path)

    with caplog.at_level(logging.INFO, logger="waifu"):
        rig.observe(False, True, False)

    assert rig.lamp_commands() == ["off", "on"]
    assert caplog.records == []


# --- whether a game holds her announcements ----------------------------------------------------


def test_a_game_counts_as_running_until_three_polls_after_the_last_poll_that_saw_it(tmp_path):
    # A process listing that keeps failing must not hold her announcements forever.
    rig = Rig(tmp_path)
    rig.mode.observe(True, False)

    rig.clock.now += 3 * POLL
    within = rig.mode.playing()
    rig.clock.now += timedelta(seconds=1)

    assert (within, rig.mode.playing()) == (True, False)


def test_no_game_runs_before_the_first_poll_or_after_one_without_a_game(tmp_path):
    rig = Rig(tmp_path)
    before = rig.mode.playing()

    rig.mode.observe(False, False)

    assert (before, rig.mode.playing()) == (False, False)


@pytest.mark.parametrize(
    ("announcements", "holding", "diverting"),
    [("normal", False, False), ("hold", True, False), ("phone", True, True)],
)
def test_what_each_mode_does_with_her_announcements_while_a_game_runs(
    tmp_path, announcements, holding, diverting
):
    rig = Rig(tmp_path, announcements=announcements)

    rig.mode.observe(True, False)

    assert (rig.mode.holding(), rig.mode.diverting()) == (holding, diverting)


@pytest.mark.parametrize("announcements", ["normal", "hold", "phone"])
def test_no_mode_holds_or_diverts_her_announcements_once_the_game_has_closed(
    tmp_path, announcements
):
    rig = Rig(tmp_path, announcements=announcements)

    rig.observe(True, False)

    assert (rig.mode.holding(), rig.mode.diverting()) == (False, False)
