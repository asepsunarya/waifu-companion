"""The tool that keeps Master company while they focus: `focus_timer`.

"Temani aku fokus 25 menit", "pomodoro empat kali", "sisa berapa menit?", "udah, berhenti
fokusnya": the tool starts, stops, and reads the focus timer (`focus_timer.py`) of this
`waifu run`, and tells how long Master focused today and this week from the focus log, the
block under way included. Its arguments may all be left out (they are checked here), so it takes
no strict slot. The spec is static English built once, so it stays byte-identical for the
brain's lifetime and keeps the prompt cache warm. In the turns she starts herself (the morning
greeting and the weekly game report) it refuses: the focus timer is Master's to start and stop.
`run` never raises.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Protocol

from waifu.brain.base import ToolSpec
from waifu.briefing import BRIEFING_PROMPT
from waifu.finance.values import WIB
from waifu.focus_timer import (
    BREAK_MINUTES_RANGE,
    MINUTES_RANGE,
    ROUNDS_RANGE,
    FocusError,
    FocusPlan,
    FocusState,
)
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.play_time import period_window, whole_minutes, wib_now

logger = logging.getLogger(__name__)

FOCUS_TIMER = "focus_timer"
ACTIONS = ("start", "stop", "status")
_LENGTHS = {"minutes": MINUTES_RANGE, "break_minutes": BREAK_MINUTES_RANGE, "rounds": ROUNDS_RANGE}
_KEYS = frozenset({"action", *_LENGTHS})
# The turns she starts herself, each with what the tool answers in it.
_OWN_TURN_REFUSALS = {
    BRIEFING_PROMPT: "Timer fokus hanya untuk permintaan Master, bukan untuk sapaan pagi.",
    WEEKLY_REPORT_PROMPT: (
        "Timer fokus hanya untuk permintaan Master, bukan untuk laporan game mingguan."
    ),
}
_FAILED = "Catatan fokus tidak bisa dibaca saat ini."
_LENGTHS_ONLY_TO_START = "'minutes', 'break_minutes', dan 'rounds' hanya untuk action start."
_LOCAL_CLOCK = "%H:%M"


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class FocusControl(Protocol):
    """The part of `FocusTimer` the tool uses."""

    def begin(self, plan: FocusPlan) -> FocusState: ...

    def end(self) -> int | None: ...

    def state(self) -> FocusState | None: ...

    def focused_seconds(self, since: datetime, until: datetime) -> int: ...


class FocusTools:
    """A `ToolProvider` for the brain: Master's focus timer of this `waifu run`."""

    def __init__(self, timer: FocusControl, *, now: Callable[[], datetime] = wib_now) -> None:
        """`now` is aware WIB time."""
        self._timer = timer
        self._now = now
        self._spec = _spec()
        self._refusal: str | None = None

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with its words as heard: a turn she starts herself, the morning
        greeting or the weekly game report, gets no focus timer."""
        self._refusal = _OWN_TURN_REFUSALS.get(user_text)

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        if name != FOCUS_TIMER:
            return _error(f"Tool '{name}' bukan tool timer fokus.")
        if self._refusal is not None:
            return _error(self._refusal)
        try:
            return _to_json(self._focus_timer(arguments)), False
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except FocusError as exc:
            return _error(str(exc))
        except Exception:
            logger.exception("Focus tool '%s' failed", name)
            return _error(_FAILED)

    def _focus_timer(self, arguments: object) -> dict[str, object]:
        given = _arguments_mapping(arguments)
        action = _choice(given, "action", ACTIONS) or "status"
        lengths = {key: value for key in _LENGTHS if (value := _length(given, key)) is not None}
        if lengths and action != "start":
            raise _InvalidArguments(_LENGTHS_ONLY_TO_START)
        if action == "start":
            return self._start(FocusPlan(**lengths))
        if action == "stop":
            return self._stop()
        return self._status()

    def _start(self, plan: FocusPlan) -> dict[str, object]:
        state = self._timer.begin(plan)
        whole = plan.minutes * plan.rounds + plan.break_minutes * (plan.rounds - 1)
        return {
            "started": True,
            "minutes": plan.minutes,
            "break_minutes": plan.break_minutes,
            "rounds": plan.rounds,
            "block_ends_at": _local(state.ends_at),
            "session_ends_at": _local(state.phase_start + timedelta(minutes=whole)),
        }

    def _stop(self) -> dict[str, object]:
        focused = self._timer.end()
        if focused is None:
            return {"stopped": False}
        return {"stopped": True, "focused": _hours_minutes(focused)}

    def _status(self) -> dict[str, object]:
        now = self._now()
        state = self._timer.state()
        totals = {
            period: _hours_minutes(self._timer.focused_seconds(*period_window(period, now)))
            for period in ("today", "week")
        }
        if state is None:
            return {"active": False, **totals}
        left = math.ceil((state.ends_at - now).total_seconds() / 60)
        return {
            "active": True,
            "phase": state.phase,
            "round": state.round,
            "rounds": state.plan.rounds,
            "minutes": state.plan.minutes,
            "break_minutes": state.plan.break_minutes,
            "minutes_left": max(0, left),
            "ends_at": _local(state.ends_at),
            **totals,
        }


def _spec() -> ToolSpec:
    """Built once, so every request sends the same bytes."""
    return ToolSpec(
        name=FOCUS_TIMER,
        description=(
            "Master's focus timer (a pomodoro), which you keep only while `waifu run` is on: "
            "a restart ends the session. `start` begins a session when Master asks you to "
            'keep them company while they focus, study, or work, or for a pomodoro ("temani '
            'aku fokus 25 menit", "pomodoro empat kali"): `minutes` per focus block (5 to '
            "120, default 25), `break_minutes` between blocks (1 to 60, default 5), and "
            "`rounds`, how many blocks (1 to 8, default 1; four for a pomodoro said four "
            "times). Give the lengths only with `start`. `stop` ends the session when Master "
            "asks; `stopped` false means none was running. `status`, the default, tells the "
            "phase (`focus` or `break`), the round, the phase's `minutes_left` and `ends_at`, "
            "and `today` and `week`, how long Master focused, the block under way included. "
            "While a block runs you say nothing by yourself: your own notices wait for the "
            "break, and Master's own reminders still speak. You say a short line yourself "
            "when each block, each break, and the session end, so never promise anything "
            "more. Use it only when Master asks, never for a greeting, the morning briefing, "
            "the weekly report, or anything you start yourself. Answer in one short sentence "
            "with the times spoken naturally, such as dua puluh lima menit: no lists and no "
            "parentheses, because your voice drops whatever is in parentheses. After an error "
            "result, say its message briefly and do not call again in this turn."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(ACTIONS)},
                "minutes": {"type": "integer"},
                "break_minutes": {"type": "integer"},
                "rounds": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    )


def _choice(arguments: Mapping[str, object], key: str, allowed: Sequence[str]) -> str | None:
    """The argument `key` when given (null counts as left out, as local models send it)."""
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise _InvalidArguments(f"'{key}' harus salah satu dari: {', '.join(allowed)}.")
    return value


def _length(arguments: Mapping[str, object], key: str) -> int | None:
    """The whole number `key` in its range when given (never a boolean); null is left out."""
    value = arguments.get(key)
    if value is None:
        return None
    low, high = _LENGTHS[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _InvalidArguments(f"'{key}' harus bilangan bulat dari {low} sampai {high}.")
    return value


def _hours_minutes(seconds: int) -> dict[str, int]:
    hours, minutes = divmod(whole_minutes(seconds), 60)
    return {"hours": hours, "minutes": minutes}


def _local(moment: datetime) -> str:
    return moment.astimezone(WIB).strftime(_LOCAL_CLOCK)


def _arguments_mapping(arguments: object) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in _KEYS]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
