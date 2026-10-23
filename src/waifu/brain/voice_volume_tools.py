"""The tool that sets her own voice volume on the PC: `voice_volume`.

"Kecilkan suaramu", "suaramu 60 persen", "nyalakan mode bisik": the tool sets the level her
speech is played at on the PC and switches whisper mode (`voice_level.py`), or tells both. It is
not the Windows volume, which the media device's keys move for everything the PC plays: the
description keeps the two apart. Its arguments may all be left out (they are checked here), so
it takes no strict slot. The spec is static English built once, so it stays byte-identical for
the brain's lifetime and keeps the prompt cache warm. In the turns she starts herself it refuses:
her volume is Master's to change. `run` never raises.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence

from waifu.brain.base import ToolSpec
from waifu.briefing import BRIEFING_PROMPT
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.voice.volume import LEVELS
from waifu.voice_level import DIRECTIONS, VoiceChange, VoiceLevel, VoiceState

logger = logging.getLogger(__name__)

VOICE_VOLUME = "voice_volume"
_KEYS = frozenset({"level", "change", "night"})
_REFUSAL = "Volume suaraku hanya diubah atas permintaan Master, bukan untuk {turn}."
# The turns she starts herself, each with what the tool answers in it.
_OWN_TURN_REFUSALS = {
    BRIEFING_PROMPT: _REFUSAL.format(turn="sapaan pagi"),
    WEEKLY_REPORT_PROMPT: _REFUSAL.format(turn="laporan game mingguan"),
    JOURNAL_PROMPT: _REFUSAL.format(turn="pertanyaan jurnal malam"),
}
_FAILED = "Volume suaraku tidak bisa dibaca atau disimpan saat ini."
_SET = "Suaraku sekarang {level} persen."
_SAME = "Suaraku memang sudah {level} persen."
_BOUND = {
    "louder": "Suaraku sudah paling keras, {level} persen.",
    "softer": "Suaraku sudah paling pelan, {level} persen.",
}
_NIGHT_ON = "Mode bisik aktif: dari jam {start} sampai {until} suaraku paling keras {night} persen."
_NIGHT_ALREADY_ON = "Mode bisik memang sudah aktif."
_NIGHT_OFF = "Mode bisik mati."
_NIGHT_ALREADY_OFF = "Mode bisik memang mati."
_STATE_NIGHT_OFF = "Suaraku {level} persen; mode bisik mati."
_STATE_NIGHT_ON = (
    "Suaraku {level} persen; mode bisik aktif, jam {start} sampai {until} paling keras {night} "
    "persen."
)
_STATE = "Suaraku {level} persen."
# Ends a level message while whisper mode keeps her softer than that level.
_HELD_BACK = ", tapi mode bisik menahannya di {night} persen sampai jam {until}."
_CLOCK = "%H:%M"
_DESCRIPTION = (
    "Your own voice volume on the PC: the level your speech is played at, from 10 to 150 "
    "percent (100 is normal), and whisper mode. Use it only for YOUR voice: "
    '"kecilkan suaramu", "suara kamu terlalu keras", "keraskan suaramu", "kecilkan volume '
    'suaramu", "ngomongnya pelan-pelan aja", "suaramu berapa?", "mode bisik". The possessive '
    'decides: "suaramu", "suara kamu", or "volume suaramu" are your voice even with the word '
    'volume; "volume" alone, "musik", "PC", or the Windows volume are not yours: use '
    "control_device on the media device for them, which presses the Windows volume keys and "
    "changes everything the PC plays, your voice included. "
    "`level` sets a percentage, 10 to 150 percent; `change` is one step of 20 percent, "
    "`louder` or `softer`; give one of them, not both. `night` true turns whisper mode on: "
    "every night inside its window your voice is at most the night level; false turns it off. "
    "Call with nothing to tell the level and whisper mode; `now` is the level you are heard at "
    "right now, and `whispering` whether whisper mode holds you back now. Replies on Master's "
    "phone are played by the phone and are not affected. Use it only when Master asks, never "
    "for a greeting, the morning briefing, the weekly report, or anything you start yourself. "
    "When you speak on the PC, your answer is already heard at `now`, which whisper mode may "
    "keep below the level just set. Answer in one short sentence with the percentage spoken "
    "naturally, such as delapan puluh persen: no lists and no parentheses, because your voice "
    "drops whatever is in parentheses. After an error result, say its message briefly and do "
    "not call again in this turn."
)


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class VoiceVolumeTools:
    """A `ToolProvider` for the brain: her own voice volume and whisper mode."""

    def __init__(self, level: VoiceLevel) -> None:
        self._level = level
        self._spec = ToolSpec(
            name=VOICE_VOLUME,
            description=_DESCRIPTION,
            input_schema={
                "type": "object",
                "properties": {
                    "level": {"type": "integer"},
                    "change": {"type": "string", "enum": list(DIRECTIONS)},
                    "night": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        )
        self._refusal: str | None = None

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with its words as heard: a turn she starts herself, the morning
        greeting, the weekly game report, or the nightly journal question, never changes her
        volume."""
        self._refusal = _OWN_TURN_REFUSALS.get(user_text)

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        if name != VOICE_VOLUME:
            return _error(f"Tool '{name}' bukan tool volume suara.")
        if self._refusal is not None:
            return _error(self._refusal)
        try:
            return _to_json(self._voice_volume(arguments)), False
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except Exception:
            logger.exception("Voice volume tool '%s' failed", name)
            return _error(_FAILED)

    def _voice_volume(self, arguments: object) -> dict[str, object]:
        given = _arguments_mapping(arguments)
        level = _level(given)
        direction = _choice(given, "change", DIRECTIONS)
        night = _switch(given, "night")
        if level is not None and direction is not None:
            raise _InvalidArguments("'level' dan 'change' tidak bisa dipakai bersamaan.")
        if level is None and direction is None and night is None:
            state = self._level.state()
            return self._report(state, self._state_message(state))
        changes: list[VoiceChange] = []
        messages: list[str] = []
        if night is not None:
            change = self._level.set_night(night)
            changes.append(change)
            messages.append(self._night_message(change))
        change = self._set_level(level, direction)
        if change is not None:
            changes.append(change)
            messages.append(self._held_back(_level_message(change, direction), change.after))
        state = changes[-1].after
        changed = any(change.changed for change in changes)
        return {"changed": changed, **self._report(state, " ".join(messages))}

    def _set_level(self, level: int | None, direction: str | None) -> VoiceChange | None:
        if level is not None:
            return self._level.set_level(level)
        if direction is not None:
            return self._level.change(direction)
        return None

    def _report(self, state: VoiceState, message: str) -> dict[str, object]:
        night = self._level.night
        return {
            "level": state.level,
            "night": state.night,
            "night_from": f"{night.from_:{_CLOCK}}",
            "night_until": f"{night.until:{_CLOCK}}",
            "night_level": night.level,
            "now": self._level.level_for(state),
            "whispering": self._level.whispering(state),
            "message": message,
        }

    def _state_message(self, state: VoiceState) -> str:
        if self._is_held_back(state):
            return self._held_back(_STATE.format(level=state.level), state)
        template = _STATE_NIGHT_ON if state.night else _STATE_NIGHT_OFF
        return self._format(template, level=state.level)

    def _night_message(self, change: VoiceChange) -> str:
        if change.after.night:
            return self._format(_NIGHT_ON) if change.changed else _NIGHT_ALREADY_ON
        return _NIGHT_OFF if change.changed else _NIGHT_ALREADY_OFF

    def _held_back(self, message: str, state: VoiceState) -> str:
        """`message` ending with whisper mode's hold while it keeps her softer than `state`'s
        level, so she never claims a louder voice that is not heard."""
        if not self._is_held_back(state):
            return message
        return message.removesuffix(".") + self._format(_HELD_BACK)

    def _is_held_back(self, state: VoiceState) -> bool:
        return self._level.level_for(state) < state.level

    def _format(self, template: str, **values: object) -> str:
        night = self._level.night
        return template.format(
            start=f"{night.from_:{_CLOCK}}",
            until=f"{night.until:{_CLOCK}}",
            night=night.level,
            **values,
        )


def _level_message(change: VoiceChange, direction: str | None) -> str:
    level = change.after.level
    if change.changed:
        return _SET.format(level=level)
    if direction is not None:
        return _BOUND[direction].format(level=level)
    return _SAME.format(level=level)


def _level(arguments: Mapping[str, object]) -> int | None:
    """The whole number `level` in its range when given (never a boolean); null is left out."""
    value = arguments.get("level")
    if value is None:
        return None
    low, high = LEVELS
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _InvalidArguments(f"'level' harus bilangan bulat dari {low} sampai {high}.")
    return value


def _choice(arguments: Mapping[str, object], key: str, allowed: Sequence[str]) -> str | None:
    """The argument `key` when given (null counts as left out, as local models send it)."""
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise _InvalidArguments(f"'{key}' harus salah satu dari: {', '.join(allowed)}.")
    return value


def _switch(arguments: Mapping[str, object], key: str) -> bool | None:
    value = arguments.get(key)
    if value is not None and not isinstance(value, bool):
        raise _InvalidArguments(f"'{key}' harus true, false, atau dikosongkan.")
    return value


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
