"""The tools that schedule reminders, device commands, and routines, and run routines now.

`schedule` saves an entry that the scheduler thread fires while `waifu run` is on;
`list_schedules` and `cancel_schedule` manage the saved entries; `run_routine` runs one of
the owner's routines from config.yaml right away, scheduling its delayed steps ("mode
tidur": the AC off three hours later) as one-shot device commands that can be cancelled.
A device command is checked when it is scheduled, by the rule `registry.execute` uses, so
an entry that could never run is refused while Master is still listening. The PC is never
scheduled: turning it off, restarting it, or putting it to sleep stays a voice command with
a confirmation. Nor are the media keys that toggle (play_pause, mute): run silently at
night, they could restart music Master had paused, or unmute the PC.

The specs carry no entries and no live state, so they stay byte-identical for the brain's
lifetime and keep the prompt cache warm.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime

from waifu.avatar.emotion import split_emotion
from waifu.brain.base import ToolSpec
from waifu.config import MEDIA_TOGGLES, RoutineConfig, RoutineStep, at_sentence_start
from waifu.devices.base import Device, DeviceResult
from waifu.devices.ir_device import resolve_command
from waifu.devices.registry import DeviceRegistry, value_problem
from waifu.routines import describe_routine, run_routine
from waifu.schedule import (
    MAX_REMINDER_CHARS,
    REPEATS,
    Action,
    DeviceAction,
    Entry,
    Reminder,
    RoutineAction,
    ScheduleError,
    ScheduleStore,
    parse_local_time,
)

logger = logging.getLogger(__name__)

SCHEDULE = "schedule"
LIST_SCHEDULES = "list_schedules"
CANCEL_SCHEDULE = "cancel_schedule"
RUN_ROUTINE = "run_routine"
LOCAL_TIME = "%Y-%m-%d %H:%M"
_SCHEDULE_KEYS = frozenset({"at", "repeat", "reminder", "device", "command", "value", "routine"})
_CANCEL_KEYS = frozenset({"id"})
_RUN_ROUTINE_KEYS = frozenset({"name"})
# The kind of action each argument belongs to: device, command, or value makes a device action.
_ACTION_KINDS = {
    "reminder": "reminder",
    "device": "device",
    "command": "device",
    "value": "device",
    "routine": "routine",
}
_NO_ACTION = "isi tepat satu aksi: reminder; atau device dan command (value boleh); atau routine."
_FAILED = "Jadwal atau rutinitas tidak bisa diproses saat ini."


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class ScheduleTools:
    """A `ToolProvider` for the brain: Master's schedule and routines."""

    def __init__(
        self,
        store: ScheduleStore,
        *,
        registry: DeviceRegistry,
        routines: Mapping[str, RoutineConfig],
        owner_call: str = "Master",
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._store = store
        self._registry = registry
        self._routines = dict(routines)
        self._now = now  # when a routine starts: its delayed steps count from then
        # In the reminder example, which she may copy, and in errors Ollama may read out.
        self._owner_call = owner_call

    def specs(self) -> list[ToolSpec]:
        specs = [self._schedule_spec(), _list_schedules_spec(), _cancel_schedule_spec()]
        if self._routines:
            specs.append(self._run_routine_spec())
        return specs

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == SCHEDULE:
                return self._schedule(arguments)
            if name == LIST_SCHEDULES:
                return self._list(arguments)
            if name == CANCEL_SCHEDULE:
                return self._cancel(arguments)
            if name == RUN_ROUTINE and self._routines:
                return self._run_routine(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except ScheduleError as exc:
            return _error(str(exc))
        except Exception:
            logger.exception("Schedule tool '%s' failed", name)
            return _error(_FAILED)
        return _error(f"Tool '{name}' bukan tool jadwal.")

    def _schedule_spec(self) -> ToolSpec:
        # Strict tool use rejects an empty enum, so an empty list leaves the argument open.
        device: dict[str, object] = {"type": "string"}
        if device_ids := self._schedulable_ids():
            device["enum"] = device_ids
        routine: dict[str, object] = {"type": "string"}
        if self._routines:
            routine["enum"] = list(self._routines)
            routines = "a routine Master wrote, to run then (they are listed under run_routine)"
        else:
            routines = "a routine (Master has written none yet, so leave it out)"
        owner = at_sentence_start(self._owner_call)  # opens the reminder example
        return ToolSpec(
            name=SCHEDULE,
            description=(
                "Schedule something for later: a reminder you will say to Master, a device "
                "command, or a routine. Every user turn starts with a time stamp of the "
                "current local time, [weekday, DD-MM-YYYY HH:MM]; turn relative times "
                "('10 menit lagi', 'jam 3 sore', 'besok pagi') into a clock time from it and "
                "pass `at` as YYYY-MM-DD HH:MM (local, 24-hour). A clock time that has "
                "already passed today usually means tomorrow. `repeat`: daily, weekdays "
                "(Monday-Friday), weekends (Saturday-Sunday), weekly (every week on the "
                "weekday of `at`), monthly (every month on the day of the month of `at`, "
                "on the last day of a month without it), or yearly (every year on the month "
                "and day of `at`, 29 February on 28 February in other years); leave it out "
                "for once. For weekly, monthly, and yearly an `at` that has passed is fine: "
                "the entry starts at its next time, and a yearly one ignores the year. For a "
                "day the current month lacks ('tiap tanggal 31' in November), pass the next "
                "month that has it, never the month's last day, which would become the day "
                "it repeats on; for 29 February pass a leap year's date, such as 2028-02-29 "
                "(the year is ignored), never 28 February. A birthday or an anniversary is a "
                "yearly reminder ('ingatkan ulang tahun Ibu tiap 5 Mei', with the reminder "
                f"'{owner}, hari ini ulang tahun Ibu!'), and a bill a monthly one ('bayar "
                f"listrik tiap tanggal 20', with the reminder '{owner}, hari ini bayar "
                "listrik!'); schedule both at 08:00 unless Master says a time. Give "
                "exactly one action: `reminder`, the words for what you will say to Master "
                "when it fires, in Indonesian, short, at most "
                f"{MAX_REMINDER_CHARS} characters ('{owner}, sudah jam tiga, waktunya minum "
                "obat!'); or `device` and `command` (and `value`), picked as for "
                "control_device, which run silently at that time because Master may be "
                f"asleep (only a failure is announced); or `routine`, {routines}. For music, "
                "schedule `stop` or `volume`, never `play_pause` or `mute`: both toggle, so they "
                "could start the music or unmute the PC while Master sleeps. The PC is "
                "never scheduled: it is turned off, restarted, or put to sleep only by voice. "
                "Never schedule turning off or toggling a plug that powers the PC (such as a "
                "'Colokan PC'): it cuts the power while Windows runs, like pulling the cable. "
                "Never offer it in place of a refused PC command. "
                "Tell Master the time you scheduled."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "at": {"type": "string"},
                    "repeat": {"type": "string", "enum": list(REPEATS)},
                    "reminder": {"type": "string"},
                    "device": device,
                    "command": {"type": "string"},
                    "value": {"type": "integer"},
                    "routine": routine,
                },
                "required": ["at"],
                "additionalProperties": False,
            },
        )

    def _run_routine_spec(self) -> ToolSpec:
        routines = "\n".join(
            f"- {describe_routine(name, routine)}" for name, routine in self._routines.items()
        )
        return ToolSpec(
            name=RUN_ROUTINE,
            description=(
                "Run one of Master's routines now: a named list of device commands Master "
                "wrote in config.yaml, such as a 'mode tidur'. Every step runs, also after a "
                "failed one; tell Master honestly which steps failed. A step listed with "
                "'setelah ...' (after a delay) is not run now: it is scheduled once for that "
                "long after the routine starts, and its result has `scheduled` with its id and "
                "time. Mention it briefly with its clock time, like 'AC akan mati jam satu "
                "nanti'; Master can cancel it with cancel_schedule and that id. Running the "
                "routine again replaces its delayed steps that have not run yet. To run a "
                f"routine later, use schedule with `routine`.\nRoutines:\n{routines}"
            ),
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string", "enum": list(self._routines)}},
                "required": ["name"],
                "additionalProperties": False,
            },
        )

    def _schedule(self, arguments: object) -> tuple[str, bool]:
        mapping = _arguments_mapping(arguments, allowed=_SCHEDULE_KEYS)
        at = parse_local_time(_text(mapping, "at"))
        entry = self._store.add(at, _repeat(mapping), self._action(mapping))
        return _to_json({"ok": True, "id": entry.id, **_when_json(entry)}), False

    def _action(self, arguments: Mapping[str, object]) -> Action:
        """The one action the arguments name; null counts as left out, as Ollama sends it."""
        kinds = {kind for key, kind in _ACTION_KINDS.items() if arguments.get(key) is not None}
        if len(kinds) != 1:
            raise _InvalidArguments(_NO_ACTION)
        kind = kinds.pop()
        if kind == "reminder":
            text = arguments["reminder"]
            if not isinstance(text, str):
                raise _InvalidArguments("'reminder' harus berupa teks.")
            # The persona tags every text for Master, but a reminder is said and pushed as it
            # is; the store refuses an empty (tag only) or too long text.
            return Reminder(split_emotion(text)[1])
        if kind == "routine":
            return RoutineAction(self._routine_name(_text(arguments, "routine")))
        return self._device_action(arguments)

    def _device_action(self, arguments: Mapping[str, object]) -> DeviceAction:
        device_id = _text(arguments, "device")
        command = _text(arguments, "command")
        value = arguments.get("value")
        # True is an int to Python, but no temperature or brightness.
        if value is not None and type(value) is not int:
            raise _InvalidArguments("'value' harus bilangan bulat atau dikosongkan.")
        device = self._registry.get(device_id)
        if device is None:
            raise ScheduleError(
                f"Perangkat '{device_id}' tidak dikenal. "
                f"Perangkat yang bisa dijadwalkan: {_join_or_none(self._schedulable_ids())}."
            )
        if self._registry.powers_pc(device_id):
            logger.warning(
                "Refused to schedule '%s' on '%s', which powers the PC", command, device_id
            )
            raise ScheduleError(_refused(device))
        available = device.commands()
        resolved = resolve_command(command, value, available)
        if resolved is None:
            with_value = f" dengan nilai {value}" if value is not None else ""
            raise ScheduleError(
                f"{device.name} tidak punya perintah '{command}'{with_value}. "
                f"Perintah yang tersedia: {_join_or_none(available)}."
            )
        if device.kind == "media" and resolved in MEDIA_TOGGLES:
            raise ScheduleError(
                f"{resolved} pada {device.name} tidak bisa dijadwalkan: tombolnya bolak-balik, "
                "jadi bisa malah memutar musik atau menyalakan suara saat "
                f"{self._owner_call} tidur. Untuk menghentikan musik jadwalkan stop; untuk "
                "mengecilkan suara jadwalkan volume dengan angka kecil, misalnya 10."
            )
        problem = value_problem(device, resolved, value)
        if problem is not None:
            raise ScheduleError(problem)
        return DeviceAction(device_id, command, value)

    def _schedulable_ids(self) -> list[str]:
        """Every device but the PC, in config order."""
        return [
            device.id for device in self._registry.all() if not self._registry.powers_pc(device.id)
        ]

    def _list(self, arguments: object) -> tuple[str, bool]:
        _arguments_mapping(arguments, allowed=frozenset())
        entries = [_entry_json(entry) for entry in self._store.entries()]
        return _to_json({"ok": True, "entries": entries}), False

    def _cancel(self, arguments: object) -> tuple[str, bool]:
        entry_id = _text(_arguments_mapping(arguments, allowed=_CANCEL_KEYS), "id")
        entry = self._store.cancel(entry_id)
        return _to_json({"ok": True, "cancelled": _entry_json(entry)}), False

    def _run_routine(self, arguments: object) -> tuple[str, bool]:
        mapping = _arguments_mapping(arguments, allowed=_RUN_ROUTINE_KEYS)
        name = self._routine_name(_text(mapping, "name"))
        routine = self._routines[name]
        results = run_routine(
            self._registry, routine, name=name, schedule=self._store, now=self._now
        )
        steps = [
            _step_json(step, result) for step, result in zip(routine.steps, results, strict=True)
        ]
        ok = all(result.ok for result in results)
        return _to_json({"ok": ok, "routine": name, "steps": steps}), not ok

    def _routine_name(self, name: str) -> str:
        if not self._routines:
            raise ScheduleError(
                f"Belum ada rutinitas; {self._owner_call} menulisnya di config.yaml."
            )
        if name not in self._routines:
            raise ScheduleError(
                f"Tidak ada rutinitas bernama '{name}'. "
                f"Rutinitas yang ada: {', '.join(self._routines)}."
            )
        return name


def _list_schedules_spec() -> ToolSpec:
    return ToolSpec(
        name=LIST_SCHEDULES,
        description=(
            "List every scheduled reminder, device command, and routine, soonest first: its "
            "id (j1, j2, ...), the next local time it fires, its repeat (a monthly or yearly "
            "one with `day`, the day of the month it repeats on), and what it does. Use it "
            "when Master asks what is scheduled, and before cancel_schedule when you are not "
            "sure of the id. For a period ('jadwal bulan ini apa?', 'ulang tahun siapa bulan "
            "ini?'), pick the entries in it yourself, repeating ones included: a yearly entry "
            "comes every year on the month and day of its next time, a monthly one every "
            "month on its `day`. Summarise it for Master instead of reading it out."
        ),
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )


def _cancel_schedule_spec() -> ToolSpec:
    return ToolSpec(
        name=CANCEL_SCHEDULE,
        description=(
            "Cancel one scheduled entry by its id (j1, j2, ...: from the schedule result or "
            "list_schedules). When you are not sure which id Master means, call "
            "list_schedules first; never guess an id."
        ),
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    )


def _repeat(arguments: Mapping[str, object]) -> str:
    repeat = arguments.get("repeat")
    if repeat is None:
        return "none"
    if not isinstance(repeat, str) or repeat not in REPEATS:
        raise _InvalidArguments(f"'repeat' harus salah satu dari: {', '.join(REPEATS)}.")
    return repeat


def _entry_json(entry: Entry) -> dict[str, object]:
    return {"id": entry.id, **_when_json(entry), "action": _describe_action(entry.action)}


def _when_json(entry: Entry) -> dict[str, object]:
    """Its next time and its repeat, with the day a monthly or yearly entry repeats on."""
    when: dict[str, object] = {"at": entry.at.strftime(LOCAL_TIME), "repeat": entry.repeat}
    if entry.day is not None:
        when["day"] = entry.day
    return when


def _describe_action(action: Action) -> str:
    """A short Indonesian description: `pengingat: <text>`, `perangkat: ac temp 26`."""
    if isinstance(action, Reminder):
        return f"pengingat: {action.text}"
    if isinstance(action, DeviceAction):
        value = f" {action.value}" if action.value is not None else ""
        routine = f" (rutinitas {action.routine})" if action.routine is not None else ""
        return f"perangkat: {action.device} {action.command}{value}{routine}"
    return f"rutinitas: {action.name}"


def _step_json(step: RoutineStep, result: DeviceResult) -> dict[str, object]:
    data: dict[str, object] = {"device": step.device, "command": step.command}
    if step.value is not None:
        data["value"] = step.value
    if step.after_minutes is not None:
        data["after_minutes"] = step.after_minutes
    data["ok"] = result.ok
    data["message"] = result.message
    if step.after_minutes is not None and result.ok:
        data["scheduled"] = result.state  # the id and time of its schedule entry
    return data


def _refused(device: Device) -> str:
    # The model reads this right after the refusal, where it might reach for the PC's plug.
    if device.kind == "pc":
        return (
            f"{device.name} adalah PC, dan PC tidak pernah dijadwalkan: PC hanya dimatikan, "
            "di-restart, atau ditidurkan lewat perintah suara langsung. Jangan menggantinya "
            "dengan mematikan colokan yang mengalirkan listrik ke PC: itu sama dengan mencabut "
            "kabelnya."
        )
    return (
        f"{device.name} mengalirkan listrik ke PC, jadi tidak pernah dijadwalkan: mematikannya "
        "sama dengan mencabut kabel PC. PC hanya dimatikan lewat perintah suara langsung."
    )


def _join_or_none(names: list[str]) -> str:
    return ", ".join(names) if names else "(belum ada)"


def _arguments_mapping(arguments: object, allowed: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in allowed]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _text(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidArguments(f"'{key}' wajib diisi dengan teks.")
    return value


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
