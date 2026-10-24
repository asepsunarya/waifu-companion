"""The tool for Master's wake-up alarms: `alarm`.

"Bangunkan aku jam 6", "alarm tiap hari kerja jam 6", "alarmku apa aja?", "matikan alarm": the
tool sets an alarm (a schedule entry, `AlarmAction`), lists or cancels the alarms, and stops or
snoozes the one that rings (`waifu.alarm`). A plain "aku sudah bangun" while it rings is answered
by the voice loop before the brain hears it; the tool is the way for the rest, and for the
phone's chat.

Every argument may be left out (they are checked here), so it takes no strict slot: the strict
`schedule` tool could take a kind only as one more optional parameter of the strict budget. The
spec is static English built once, so it stays byte-identical for the brain's lifetime and keeps
the prompt cache warm. Guest mode leaves it open, an alarm being a control, but closes the list
and cancelling (the labels, and Master's entries), as `list_schedules` and `cancel_schedule`.
In the turns she starts herself it refuses. `run` never raises.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping

from waifu.alarm import SNOOZE_MINUTES, AlarmBell
from waifu.avatar.emotion import split_emotion
from waifu.brain.base import ToolSpec
from waifu.brain.schedule_tools import LOCAL_TIME, when_json
from waifu.briefing import BRIEFING_PROMPT
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.guest import GUEST_REFUSAL
from waifu.journal import JOURNAL_PROMPT
from waifu.schedule import (
    REPEATS,
    AlarmAction,
    Entry,
    ScheduleError,
    ScheduleStore,
    parse_local_time,
)

logger = logging.getLogger(__name__)

ALARM = "alarm"
ACTIONS = ("set", "list", "cancel", "stop", "snooze")
# The arguments each action takes besides `action`; null counts as left out.
_ACTION_KEYS: dict[str, frozenset[str]] = {
    "set": frozenset({"at", "repeat", "label"}),
    "list": frozenset(),
    "cancel": frozenset({"id"}),
    "stop": frozenset(),
    "snooze": frozenset({"minutes"}),
}
_KEYS = frozenset({"action"}).union(*_ACTION_KEYS.values())
# What guest mode closes: the labels, and cancelling Master's entries.
_PRIVATE_ACTIONS = frozenset({"list", "cancel"})
_REFUSAL = "Alarm hanya diatur atas permintaan Master, bukan untuk {turn}."
# The turns she starts herself, each with what the tool answers in it.
_OWN_TURN_REFUSALS = {
    BRIEFING_PROMPT: _REFUSAL.format(turn="sapaan pagi"),
    WEEKLY_REPORT_PROMPT: _REFUSAL.format(turn="laporan game mingguan"),
    JOURNAL_PROMPT: _REFUSAL.format(turn="pertanyaan jurnal malam"),
}
_NOT_AN_ALARM = "{id} bukan alarm; jadwal lain dibatalkan dengan cancel_schedule."
_STOPPED = "Alarm dimatikan."
_NOTHING_RINGS = "Tidak ada alarm yang sedang berbunyi."
_NOTHING_TO_SNOOZE = "Tidak ada alarm yang sedang berbunyi untuk ditunda."
_SNOOZED = "Alarm ditunda sampai jam {time}."
_FAILED = "Alarm tidak bisa diproses saat ini."
_DESCRIPTION = (
    "Master's wake-up alarms. Use it to wake Master ('bangunkan aku jam 6', 'alarm jam 5.30 "
    "besok', 'alarm tiap hari kerja jam 6'), not a reminder with schedule: at its time you say "
    "it aloud on the PC, then again every minute, a little louder each time, until Master says "
    "he is awake, taps 'Matikan alarm' on his phone or PC, or ten minutes pass. `action`: set "
    "(the default) with `at` as YYYY-MM-DD HH:MM, local, 24-hour, from the time stamp of the "
    "turn (a clock time that has already passed today usually means tomorrow), `repeat` as for "
    "schedule (daily, weekdays (Monday-Friday), weekends (Saturday-Sunday), weekly, monthly, "
    "yearly; leave it out for once), and a short `label` only when Master says what it is for "
    "('berangkat kerja'); list for Master's alarms (`ringing` tells whether one rings now); "
    "cancel with the `id` from set or list; stop to stop the alarm ringing now ('aku sudah "
    "bangun', 'matikan alarm'); snooze to ring it again after `minutes` (1 to 30; leave it out "
    "for the usual snooze). Tell Master the time you set; for a weekly alarm say the `weekday` "
    "of the result. The first time in a conversation that you set an alarm, add in a few words "
    "that it rings only while the PC is on and awake and you are running, with a push to his "
    "phone as the backup when notifications are set up. While it rings, Master's plain 'aku "
    "sudah bangun' or 'tunda' usually stops or snoozes it before you hear it. Only when Master "
    "asks, never in a greeting or anything you start yourself. Answer in one short sentence "
    "with the time spoken naturally: no lists and no parentheses, because your voice drops "
    "whatever is in parentheses. After an error result, say its message briefly and do not "
    "call again in this turn."
)


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class AlarmTools:
    """A `ToolProvider` for the brain: Master's alarms, and the one that rings."""

    def __init__(
        self,
        schedule: ScheduleStore,
        bell: AlarmBell,
        *,
        guest_mode: Callable[[], bool] | None = None,
    ) -> None:
        """`guest_mode` (guest mode's `is_on`) closes the list and cancelling while it is on."""
        self._schedule = schedule
        self._bell = bell
        self._guest_mode = guest_mode
        self._spec = ToolSpec(
            name=ALARM,
            description=_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "at": {"type": "string"},
                    "repeat": {"type": "string", "enum": list(REPEATS)},
                    "label": {"type": "string"},
                    "id": {"type": "string"},
                    "minutes": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        )
        self._refusal: str | None = None

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with its words as heard: a turn she starts herself, the morning
        greeting, the weekly game report, or the nightly journal question, never touches an
        alarm."""
        self._refusal = _OWN_TURN_REFUSALS.get(user_text)

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        if name != ALARM:
            return _error(f"Tool '{name}' bukan tool alarm.")
        if self._refusal is not None:
            return _error(self._refusal)
        try:
            return self._alarm(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except ScheduleError as exc:
            return _error(str(exc))
        except Exception:
            logger.exception("Alarm tool failed")
            return _error(_FAILED)

    def _alarm(self, arguments: object) -> tuple[str, bool]:
        given = _given(arguments)
        action = given.pop("action", "set")
        if not isinstance(action, str) or action not in ACTIONS:
            raise _InvalidArguments(f"'action' harus salah satu dari: {', '.join(ACTIONS)}.")
        for key in given:
            if key not in _ACTION_KEYS[action]:
                raise _InvalidArguments(f"'{key}' tidak dipakai untuk {action}.")
        if action in _PRIVATE_ACTIONS and self._guest_mode is not None and self._guest_mode():
            logger.info("Refused alarm %s while guest mode is on", action)
            return _error(GUEST_REFUSAL)
        if action == "set":
            return self._set(given)
        if action == "list":
            return self._list()
        if action == "cancel":
            return self._cancel(_text(given, "id"))
        if action == "stop":
            stopped = self._bell.stop()
            message = _STOPPED if stopped else _NOTHING_RINGS
            return _to_json({"ok": True, "stopped": stopped, "message": message}), False
        return self._snooze(_minutes(given))

    def _set(self, given: Mapping[str, object]) -> tuple[str, bool]:
        at = parse_local_time(_text(given, "at"))
        repeat = given.get("repeat", "none")
        if not isinstance(repeat, str) or repeat not in REPEATS:
            raise _InvalidArguments(f"'repeat' harus salah satu dari: {', '.join(REPEATS)}.")
        label = given.get("label")
        if label is not None and not isinstance(label, str):
            raise _InvalidArguments("'label' harus berupa teks.")
        # The persona tags every text for Master, but a label is said as it is.
        action = AlarmAction(None if label is None else split_emotion(label)[1])
        entry = self._schedule.add(at, repeat, action)
        return _to_json({"ok": True, **_alarm_json(entry)}), False

    def _list(self) -> tuple[str, bool]:
        alarms = [
            _alarm_json(entry)
            for entry in self._schedule.entries()
            if isinstance(entry.action, AlarmAction)
        ]
        return _to_json({"ok": True, "alarms": alarms, "ringing": self._bell.ringing()}), False

    def _cancel(self, entry_id: str) -> tuple[str, bool]:
        found = next((entry for entry in self._schedule.entries() if entry.id == entry_id), None)
        if found is not None and not isinstance(found.action, AlarmAction):
            return _error(_NOT_AN_ALARM.format(id=found.id))
        cancelled = self._schedule.cancel(entry_id)
        return _to_json({"ok": True, "cancelled": _alarm_json(cancelled)}), False

    def _snooze(self, minutes: int | None) -> tuple[str, bool]:
        entry = self._bell.snooze(minutes)
        if entry is None:
            return _error(_NOTHING_TO_SNOOZE)
        return _to_json(
            {
                "ok": True,
                "id": entry.id,
                "at": entry.at.strftime(LOCAL_TIME),
                "message": _SNOOZED.format(time=f"{entry.at:%H.%M}"),
            }
        ), False


def _alarm_json(entry: Entry) -> dict[str, object]:
    """An alarm's id, next time, repeat (with its weekday or day), and label when it has one."""
    data: dict[str, object] = {"id": entry.id, **when_json(entry)}
    label = entry.action.label if isinstance(entry.action, AlarmAction) else None
    if label is not None:
        data["label"] = label
    return data


def _given(arguments: object) -> dict[str, object]:
    """The arguments that are not null (local models send null for a left-out one)."""
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in _KEYS]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return {str(key): value for key, value in arguments.items() if value is not None}


def _text(given: Mapping[str, object], key: str) -> str:
    value = given.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidArguments(f"'{key}' wajib diisi dengan teks.")
    return value


def _minutes(given: Mapping[str, object]) -> int | None:
    value = given.get("minutes")
    if value is None:
        return None
    low, high = SNOOZE_MINUTES
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _InvalidArguments(f"'minutes' harus bilangan bulat dari {low} sampai {high}.")
    return value


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
