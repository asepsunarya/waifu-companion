"""The owner's routines: named lists of device commands written in config.yaml.

"Cyrene, mode tidur." runs every step of the routine `tidur` in order (lights off, AC
to 26). Only the owner writes routines; the model can run them, by voice or on a
schedule, but never change them.

The PC is never one of the steps: the config already refuses it, and `run_routine`
refuses a device of kind `pc` again at run time, so a routine can never turn the PC
off, restart it, or put it to sleep. That stays a voice command with a confirmation.
"""

from __future__ import annotations

import logging

from waifu.config import RoutineConfig, RoutineStep
from waifu.devices.base import DeviceResult
from waifu.devices.ir_device import resolve_command
from waifu.devices.registry import DeviceRegistry, value_problem

logger = logging.getLogger(__name__)


def run_routine(registry: DeviceRegistry, routine: RoutineConfig) -> list[DeviceResult]:
    """Run every step in order, one result per step; a failed step never stops the rest."""
    results = []
    for step in routine.steps:
        refusal = pc_power_refusal(registry, step.device, "rutinitas")
        if refusal is not None:
            logger.warning("Refused routine step on '%s', which powers the PC", step.device)
            results.append(DeviceResult(ok=False, message=refusal))
        else:
            results.append(registry.execute(step.device, step.command, step.value))
    return results


def describe_routine(name: str, routine: RoutineConfig) -> str:
    """One line for tool descriptions and `waifu routine list`: `tidur: Mode tidur (ac off)`."""
    steps = ", ".join(_describe_step(step) for step in routine.steps)
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
            problems.append(f"Langkah {number} ({_describe_step(step)}): {problem}")
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


def _describe_step(step: RoutineStep) -> str:
    value = f" {step.value}" if step.value is not None else ""
    return f"{step.device} {step.command}{value}"


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
