"""Her own voice level on the PC and whisper mode, which Master sets by voice.

The level (10 to 150 percent, `voice/volume.py`) and whether whisper mode is on live in a small
state file shared by `waifu run`, `chat`, and `say`. It follows the rules of `prompt_timer.py`:
it is re-read at every call, replaced atomically, never created by reading, and a file that is
not such a state is moved aside, never overwritten; one lock serialises the calls of every
`VoiceLevel` in this process. While whisper mode is on, her voice is at most the night level
inside the window of `voice.night`, on the PC's local clock. `level_now`, read for every clip she
plays, never raises: a file that cannot be read must never silence her.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, time
from pathlib import Path

from waifu.config import VoiceNightConfig
from waifu.gacha.files import write_json_atomic
from waifu.voice.volume import LEVELS, NORMAL, STEP

logger = logging.getLogger(__name__)

FILE_VERSION = 1
DIRECTIONS = ("louder", "softer")
_STATE_KEYS = {"version", "level", "night"}

# One lock for every `VoiceLevel`: two of them on the same file must not lose a change.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class VoiceState:
    level: int = NORMAL  # percent
    night: bool = False  # whisper mode on


@dataclass(frozen=True)
class VoiceChange:
    before: VoiceState
    after: VoiceState

    @property
    def changed(self) -> bool:
        return self.before != self.after


def in_window(moment: time, start: time, until: time) -> bool:
    """Whether `moment` is in `[start, until)`, which wraps past midnight when `until` comes
    first."""
    if start < until:
        return start <= moment < until
    return moment >= start or moment < until


class VoiceLevel:
    def __init__(
        self,
        path: Path | str,
        night: VoiceNightConfig,
        *,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        """`now` is the PC's local time (naive), as the briefing reads it."""
        self.path = Path(path)
        self.night = night
        self._now = now
        self._last = VoiceState()  # the state last read, for a file that cannot be read
        self._unreadable = False  # warned that the file cannot be read

    def state(self) -> VoiceState:
        """The level and whisper mode; a missing file is the normal level without it."""
        with _LOCK:
            return self._read()

    def set_level(self, level: int) -> VoiceChange:
        low, high = LEVELS
        if isinstance(level, bool) or not isinstance(level, int) or not low <= level <= high:
            raise ValueError(f"the level must be a whole number from {low} to {high}")
        return self._update(lambda state: replace(state, level=level))

    def change(self, direction: str) -> VoiceChange:
        """One step louder or softer, held in `LEVELS`."""
        if direction not in DIRECTIONS:
            raise ValueError(f"the direction must be one of {', '.join(DIRECTIONS)}")
        step = STEP if direction == "louder" else -STEP
        low, high = LEVELS
        return self._update(
            lambda state: replace(state, level=min(high, max(low, state.level + step)))
        )

    def set_night(self, on: bool) -> VoiceChange:
        return self._update(lambda state: replace(state, night=on))

    def whispering(self, state: VoiceState) -> bool:
        """Whether whisper mode holds now for `state`."""
        night = self.night
        return state.night and in_window(self._now().time(), night.from_, night.until)

    def level_for(self, state: VoiceState) -> int:
        """The level a clip is played at now with `state`."""
        return min(state.level, self.night.level) if self.whispering(state) else state.level

    def level_now(self) -> int:
        """The level her next clip is played at. Never raises: when the file cannot be read it
        warns once, until it reads again, and goes on with the state last read."""
        try:
            state = self.state()
        except OSError as exc:
            if not self._unreadable:
                logger.warning(
                    "Could not read the voice level in %s (%s); keeping %d percent",
                    self.path,
                    exc,
                    self._last.level,
                )
            self._unreadable = True
            state = self._last
        else:
            self._unreadable = False
        return self.level_for(state)

    def _update(self, change: Callable[[VoiceState], VoiceState]) -> VoiceChange:
        with _LOCK:
            before = self._read()
            after = change(before)
            if after != before:
                write_json_atomic(
                    self.path,
                    {"version": FILE_VERSION, "level": after.level, "night": after.night},
                )
                self._last = after
                if after.level != before.level:
                    logger.info("Voice level %d (was %d)", after.level, before.level)
                if after.night != before.night:
                    logger.info("Whisper mode %s", "on" if after.night else "off")
        return VoiceChange(before, after)

    def _read(self) -> VoiceState:
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            state = _parse_state(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            state = VoiceState()
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem)
            state = VoiceState()
        self._last = state
        return state

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not a voice state, so that no write ever replaces it."""
        name = f"{self.path.name}.corrupt-{self._now():%Y%m%d-%H%M%S}"
        target = self.path.with_name(name)
        counter = 2
        while target.exists():
            target = self.path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(self.path, target)
        except OSError as exc:
            logger.error(
                "The voice state %s is not one (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "The voice state %s is not one (%s); moved it to %s and started anew",
            self.path,
            problem,
            target,
        )


def _parse_state(raw: object) -> VoiceState:
    """The state in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _STATE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_STATE_KEYS)}")
    version, level, night = raw["version"], raw["level"], raw["night"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    low, high = LEVELS
    if type(level) is not int or not low <= level <= high:
        raise ValueError(f"level must be a whole number from {low} to {high}")
    if type(night) is not bool:
        raise ValueError("night must be true or false")
    return VoiceState(level, night)
