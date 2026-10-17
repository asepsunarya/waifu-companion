"""The owner's routines: named lists of device commands written in config.yaml.

"Cyrene, mode tidur." runs every step of the routine `tidur` in order (lights off, AC
to 26). Only the owner writes routines; the model can run them, by voice or on a
schedule, but never change them.

A step with `after_minutes` waits: running the routine puts it into the schedule as a
one-shot device command for that many minutes after the routine started (the AC off three
hours later), tagged with the routine's name. The scheduler then runs it like any timed
command. A new run of the routine first cancels the delayed steps an earlier run left
pending, so the latest run wins and the AC is never turned off twice.

The PC is never one of the steps: the config already refuses it, and `run_routine`
refuses a device of kind `pc` again at run time, so a routine can never turn the PC
off, restart it, or put it to sleep. That stays a voice command with a confirmation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from waifu.config import RoutineConfig, RoutineStep
from waifu.devices.base import DeviceResult
from waifu.devices.ir_device import resolve_command
from waifu.devices.registry import DeviceRegistry, value_problem
from waifu.schedule import DeviceAction, ScheduleError, ScheduleStore

logger = logging.getLogger(__name__)

_NO_SCHEDULE = "Langkah yang ditunda butuh jadwal, tapi jadwal mati (schedule.enabled: false)."
_UNSAVED = "File jadwal tidak bisa dibuka, jadi langkah ini tidak dijadwalkan."
_LOCAL_TIME = "%Y-%m-%d %H:%M"
_SMALL_HOURS_END = 5  # 00:00-04:59 is "dini hari": the end of the night before, not "besok"


def run_routine(
    registry: DeviceRegistry,
    routine: RoutineConfig,
    *,
    name: str,
    schedule: ScheduleStore | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> list[DeviceResult]:
    """Run every step in order, one result per step; a failed step never stops the rest.

    A delayed step is never run now: it is scheduled in `schedule` for its delay after the
    routine starts (`now`, read once), and its result says when, as she would say it, with
    the entry's id and time in `state`. Before that, the delayed steps an earlier run of
    `name` left pending are cancelled. A routine without delayed steps never reads the
    schedule.
    """
    started = now()
    if schedule is not None and any(step.after_minutes is not None for step in routine.steps):
        _cancel_pending_steps(schedule, name)
    results = []
    for step in routine.steps:
        if step.after_minutes is None:
            results.append(_run_now(registry, step))
        else:
            delay = timedelta(minutes=step.after_minutes)
            results.append(_schedule_later(registry, step, name, schedule, started, delay))
    return results


def _run_now(registry: DeviceRegistry, step: RoutineStep) -> DeviceResult:
    refusal = pc_power_refusal(registry, step.device, "rutinitas")
    if refusal is not None:
        logger.warning("Refused routine step on '%s', which powers the PC", step.device)
        return DeviceResult(ok=False, message=refusal)
    return registry.execute(step.device, step.command, step.value)


def _schedule_later(
    registry: DeviceRegistry,
    step: RoutineStep,
    name: str,
    schedule: ScheduleStore | None,
    started: datetime,
    delay: timedelta,
) -> DeviceResult:
    """Schedule one step of routine `name` once, `delay` after the routine `started`.

    A step that could never run (by the rule of `waifu check`) is not scheduled: it would
    only fail in the night.
    """
    if schedule is None:
        return DeviceResult(ok=False, message=_NO_SCHEDULE)
    try:
        problem = _step_problem(registry, step)
    except Exception:  # an IR code file that cannot be read; the registry never raises either
        logger.exception("Could not check step '%s' of routine %s", describe_step(step), name)
        device = registry.get(step.device)
        device_name = device.name if device is not None else step.device
        return DeviceResult(
            ok=False,
            message=f"Perintah {device_name} tidak bisa diperiksa sekarang, jadi langkah ini "
            "tidak dijadwalkan.",
        )
    if problem is not None:
        logger.warning(
            "Did not schedule step '%s' of routine %s: %s", describe_step(step), name, problem
        )
        return DeviceResult(ok=False, message=problem)
    action = DeviceAction(step.device, step.command, step.value, routine=name)
    try:
        entry = schedule.add(started + delay, "none", action)
    except ScheduleError as exc:
        return DeviceResult(ok=False, message=str(exc))
    except OSError as exc:
        logger.error(
            "Could not schedule step '%s' of routine %s: %s", describe_step(step), name, exc
        )
        return DeviceResult(ok=False, message=_UNSAVED)
    logger.info(
        "Routine %s scheduled '%s' as %s at %s",
        name,
        describe_step(step),
        entry.id,
        entry.at.strftime(_LOCAL_TIME),
    )
    return DeviceResult(
        ok=True,
        message=f"Dijadwalkan {_spoken_when(entry.at, started)}.",
        state={"id": entry.id, "at": entry.at.strftime(_LOCAL_TIME)},
    )


def _spoken_when(at: datetime, started: datetime) -> str:
    """When a delayed step runs, as she says it at `started`; the model copies it into her reply.

    `jam 16.00 nanti`; `jam 01.00 dini hari nanti` for the night after an evening start, since
    "besok jam satu" said at 22:00 sounds like 13:00 tomorrow; `besok jam 08.00` (`besok jam
    03.00 dini hari` from a start in the small hours) once that night has passed. No id: it
    would be read out, and `state` has it.
    """
    small_hours = at.hour < _SMALL_HOURS_END
    part = " dini hari" if small_hours else ""
    coming_night = small_hours and started.hour >= _SMALL_HOURS_END
    if at.date() != started.date() and not coming_night:
        return f"besok jam {at:%H.%M}{part}"
    return f"jam {at:%H.%M}{part} nanti"


def _cancel_pending_steps(schedule: ScheduleStore, name: str) -> None:
    """Cancel the one-shot device entries an earlier run of routine `name` left pending.

    What Master scheduled himself, and other routines' steps, are never touched.
    """
    cancelled = []
    try:
        for entry in schedule.entries():
            action = entry.action
            if (
                entry.repeat == "none"
                and isinstance(action, DeviceAction)
                and action.routine == name
            ):
                try:
                    schedule.cancel(entry.id)
                except ScheduleError:
                    continue  # fired or cancelled meanwhile
                cancelled.append(entry.id)
    except OSError as exc:
        logger.error("Could not replace the pending steps of routine %s: %s", name, exc)
    if cancelled:
        logger.info(
            "Routine %s ran again: cancelled its pending steps %s", name, ", ".join(cancelled)
        )


def describe_routine(name: str, routine: RoutineConfig) -> str:
    """One line for tool descriptions and `waifu routine list`: `tidur: Mode tidur (ac off)`."""
    steps = ", ".join(describe_step(step) for step in routine.steps)
    description = routine.description.strip()
    return f"{name}: {description} ({steps})" if description else f"{name}: {steps}"


def routine_problems(registry: DeviceRegistry, routine: RoutineConfig) -> list[str]:
    """One Indonesian line per step that cannot run, by the rule `registry.execute` uses.

    Nothing is executed. Empty when every step resolves.
    """
    problems = []
    for number, step in enumerate(routine.steps, start=1):
        problem = _step_problem(registry, step)
        if problem:
            problems.append(f"Langkah {number} ({describe_step(step)}): {problem}")
    return problems


def _step_problem(registry: DeviceRegistry, step: RoutineStep) -> str | None:
    device = registry.get(step.device)
    if device is None:
        return f"perangkat '{step.device}' tidak dikenal."
    refusal = pc_power_refusal(registry, step.device, "rutinitas")
    if refusal is not None:
        return refusal
    resolved = resolve_command(step.command, step.value, device.commands())
    if resolved is None:
        with_value = f" dengan nilai {step.value}" if step.value is not None else ""
        return f"{device.name} tidak punya perintah '{step.command}'{with_value}."
    return value_problem(device, resolved, step.value)


def describe_step(step: RoutineStep) -> str:
    """`ac temp 26`, or `ac off setelah 3 jam` for a delayed step: a step as the owner wrote it."""
    value = f" {step.value}" if step.value is not None else ""
    delay = f" setelah {_delay_text(step.after_minutes)}" if step.after_minutes is not None else ""
    return f"{step.device} {step.command}{value}{delay}"


def _delay_text(minutes: int) -> str:
    """`1 jam 30 menit`: a delay as the owner reads it."""
    hours, minutes = divmod(minutes, 60)
    parts = [f"{hours} jam"] if hours else []
    if minutes:
        parts.append(f"{minutes} menit")
    return " ".join(parts)


def pc_power_refusal(registry: DeviceRegistry, device_id: str, where: str) -> str | None:
    """Why `device_id` never runs from `where` ("jadwal" or "rutinitas"), or None when it may.

    The PC is turned off, restarted, or put to sleep only by a direct voice command, and a plug
    that feeds it is the same as pulling its cable. Shared by routines and the scheduler.
    """
    device = registry.get(device_id)
    if device is None or not registry.powers_pc(device_id):
        return None
    if device.kind == "pc":
        return f"{device.name} adalah PC, dan PC tidak pernah dijalankan dari {where}."
    return (
        f"{device.name} mengalirkan listrik ke PC, jadi tidak pernah dijalankan dari {where}: "
        "mematikannya sama dengan mencabut kabel PC."
    )
