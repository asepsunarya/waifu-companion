"""The phone page's quick buttons: the owner's switches and routines, one tap each.

The buttons are built afresh from the registry and the routines at every request, so a code
learned with `waifu learn-ir` shows at the next load of the page: lights, plugs, IR devices,
and the mocks a dry run or the owner made, each with the switches it has (on, off, toggle, an
IR remote's power button), a light's brightness presets, and an IR device's learned preset
temperatures; and every routine. The phone sends only a button's id, which `find` looks up in
a list built then, so it never names a device, a command, or a value itself.

The PC and the plug that feeds it (`registry.powers_pc`) are never buttons, with any command:
turning the PC off, restarting it, or putting it to sleep stays a voice or chat command with
the confirmation in code. `run` refuses them again, as defence in depth. Media keys and apps
are not buttons either: they are no switches (`play_pause` and `mute` toggle without telling
the state), and opening an app takes the focus from a game.

The brain never hears of a button: the device is run through the registry as `waifu device`
does, and a routine through `run_routine` as `waifu routine run` does.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from waifu.config import QuickActionsConfig, RoutineConfig
from waifu.devices.base import Device, DeviceResult
from waifu.devices.registry import DeviceRegistry
from waifu.routines import pc_power_refusal, run_routine
from waifu.schedule import ScheduleStore

logger = logging.getLogger(__name__)

# What a button's id looks like: device ids, IR command names, and routine names are lowercase
# slugs, so an id is plain ASCII.
ACTION_ID = re.compile(r"dev\.[a-z0-9_]+\.[a-z0-9_]+(\.[0-9]{1,3})?|rt\.[a-z0-9_]+")
ROUTINES_GROUP = "Rutinitas"
# Where a refused device "never runs from", in `pc_power_refusal`'s sentence.
WHERE = "tombol cepat HP"
# The device kinds that may have buttons (a dry run's mocks keep the real device's kind).
QUICK_KINDS = frozenset({"light", "plug", "ir", "mock"})
SWITCH_LABELS = {"on": "Nyala", "off": "Mati", "toggle": "Nyala/mati", "power": "Tombol power"}
BRIGHTNESS_PRESETS = (10, 30, 60, 100)
TEMPERATURE_PRESETS = (18, 20, 22, 24, 26, 28)


@dataclass(frozen=True)
class QuickAction:
    """One button: a device command (`device`, `command`, `value`) or a `routine`."""

    id: str
    label: str
    device: str = ""
    command: str = ""
    value: int | None = None
    routine: str = ""


@dataclass(frozen=True)
class QuickGroup:
    """A device's buttons under its name, or the routines under ROUTINES_GROUP."""

    name: str
    actions: tuple[QuickAction, ...]


@dataclass(frozen=True)
class QuickResult:
    ok: bool
    message: str  # Indonesian, for the page's toast


class QuickActions:
    """The buttons of `registry`'s devices and of `routines`, as `chosen` picks them.

    A routine's delayed steps go into `schedule` (None: they fail, as with the schedule off),
    timed from `now`. Actions run one at a time: the devices have no locks of their own.
    """

    def __init__(
        self,
        registry: DeviceRegistry,
        routines: Mapping[str, RoutineConfig],
        *,
        schedule: ScheduleStore | None = None,
        chosen: QuickActionsConfig | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._registry = registry
        self._routines = routines
        self._schedule = schedule
        self._chosen = chosen or QuickActionsConfig()
        self._now = now
        self._lock = threading.Lock()

    def groups(self) -> list[QuickGroup]:
        """Every device's buttons, in config order (or `chosen`'s), then the routines'."""
        groups = [group for device in self._devices() if (group := _device_group(device))]
        routines = self._routine_actions()
        if routines:
            groups.append(QuickGroup(ROUTINES_GROUP, routines))
        return groups

    def find(self, action_id: str) -> QuickAction | None:
        """The button `action_id` names in the list of now, or None."""
        actions = (action for group in self.groups() for action in group.actions)
        return next((action for action in actions if action.id == action_id), None)

    def run(self, action: QuickAction) -> QuickResult:
        """Runs `action` (from `find`) and says how it went; never raises for a device's failure."""
        with self._lock:
            if action.routine:
                return self._run_routine(action)
            return self._run_device(action)

    def _devices(self) -> list[Device]:
        chosen = self._chosen.devices
        if chosen is None:
            devices = self._registry.all()
        else:
            devices = [device for name in chosen if (device := self._registry.get(name))]
        return [
            device
            for device in devices
            if device.kind in QUICK_KINDS and not self._registry.powers_pc(device.id)
        ]

    def _routine_actions(self) -> tuple[QuickAction, ...]:
        chosen = self._chosen.routines
        names = list(self._routines) if chosen is None else chosen
        return tuple(
            QuickAction(f"rt.{name}", _routine_label(name, self._routines[name]), routine=name)
            for name in names
            if name in self._routines
        )

    def _run_device(self, action: QuickAction) -> QuickResult:
        refusal = pc_power_refusal(self._registry, action.device, WHERE)
        if refusal is not None:
            logger.warning("Refused quick action %s: its device powers the PC", action.id)
            return QuickResult(ok=False, message=refusal)
        result = self._registry.execute(action.device, action.command, action.value)
        return QuickResult(ok=result.ok, message=result.message)

    def _run_routine(self, action: QuickAction) -> QuickResult:
        name = action.routine
        routine = self._routines[name]
        results = run_routine(
            self._registry, routine, name=name, schedule=self._schedule, now=self._now
        )
        return _routine_outcome(_routine_label(name, routine), routine, results)


def _device_group(device: Device) -> QuickGroup | None:
    """`device`'s buttons, or None when it has none or its commands cannot be read."""
    try:
        commands = set(device.commands())
    except Exception as exc:  # an IR code file that is not valid JSON
        logger.warning(
            "No quick buttons for %s: its commands could not be read: %s", device.id, exc
        )
        return None
    prefix = f"dev.{device.id}"
    actions = [
        QuickAction(f"{prefix}.{command}", label, device=device.id, command=command)
        for command, label in SWITCH_LABELS.items()
        if command in commands
    ]
    if device.kind == "light" and "brightness" in commands:
        actions += [
            QuickAction(
                f"{prefix}.brightness.{percent}",
                f"{percent}%",
                device=device.id,
                command="brightness",
                value=percent,
            )
            for percent in BRIGHTNESS_PRESETS
        ]
    if device.kind == "ir":
        actions += [
            QuickAction(f"{prefix}.{command}", f"{degrees}°C", device=device.id, command=command)
            for degrees in TEMPERATURE_PRESETS
            if (command := f"temp_{degrees}") in commands
        ]
    return QuickGroup(device.name, tuple(actions)) if actions else None


def _routine_label(name: str, routine: RoutineConfig) -> str:
    return routine.description.strip() or name


def _routine_outcome(
    label: str, routine: RoutineConfig, results: Sequence[DeviceResult]
) -> QuickResult:
    """`Mode tidur: 2 dari 3 langkah berhasil.`, the first failure, then when the delayed steps
    run ("Dijadwalkan jam 01.00 dini hari nanti."), each different time once."""
    steps = list(zip(routine.steps, results, strict=True))
    done = sum(result.ok for result in results)
    parts = [f"{label}: {done} dari {len(results)} langkah berhasil."]
    parts += [result.message for result in results if not result.ok][:1]
    parts += dict.fromkeys(
        result.message for step, result in steps if step.after_minutes is not None and result.ok
    )
    return QuickResult(ok=done == len(results), message=" ".join(parts))
