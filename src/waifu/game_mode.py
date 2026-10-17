"""Game mode: what she does while one of Master's tracked games runs.

The play time watcher tells `observe` after each poll that worked whether a tracked game is
running (its two-poll rule already keeps alt-tab and a launcher restart from counting as a stop)
and whether the run's first poll resumed a session from before a restart of `waifu run`. When
the first game opens, `game_mode.on_start` runs once; when the last one has closed,
`game_mode.on_stop`. A restart mid-game finds the room set already, so its first poll runs
nothing, and stopping `waifu run` tells nothing, so it never runs `on_stop`.

The voice loop asks `holding()` at every frame: with `announcements` set to `hold` or `phone`,
her own announcements wait while a game runs (the v1.30 quiet), and with `phone` a notice that
also went to the phone is not said at all (`diverting()`). A game counts as running only while
the last poll that saw it is at most three polls old, counted from the end of the routine that
poll ran, so a process listing that keeps failing never holds her announcements forever and a
slow routine never ends the hold early.

Routines run on the watcher's thread without a word; a failed step is logged at WARNING. When
Master plays is his own: starts and stops are logged only at DEBUG.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from waifu.config import GameModeConfig, RoutineConfig
from waifu.devices.registry import DeviceRegistry
from waifu.play_time import SLEEP_POLLS
from waifu.routines import describe_step, run_routine
from waifu.schedule import ScheduleStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Seen:
    """What the last poll told, and when the watcher was done with it: None while its routine
    still runs, which never counts as stale (lamps out of reach can make a routine outlast
    three polls, and a slow routine is no failing listing)."""

    playing: bool
    at: datetime | None


class GameMode:
    def __init__(
        self,
        config: GameModeConfig,
        *,
        registry: DeviceRegistry,
        routines: Mapping[str, RoutineConfig],
        schedule: ScheduleStore | None = None,
        poll: timedelta,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        """`poll` is the play time watcher's interval; `now` the PC's local clock, as the
        scheduler's, for the routines' delayed steps and the age of the last poll."""
        self._config = config
        self._registry = registry
        self._routines = routines
        self._schedule = schedule
        self._stale_after = SLEEP_POLLS * poll
        self._now = now
        # Replaced whole, so the voice loop reads it without a lock.
        self._seen: _Seen | None = None

    def observe(self, playing: bool, resumed: bool) -> None:
        """Take one poll's word: run `on_start` when a game has opened, `on_stop` when the
        last one has closed, each once. Before the run's first poll no game ran, unless that
        poll `resumed` one from before the restart (only a running game is resumed)."""
        before = self._seen
        self._seen = _Seen(playing, None)
        was_playing = before.playing if before is not None else resumed
        try:
            if playing and not was_playing:
                logger.debug("A game is running")
                self._run("start", self._config.on_start)
            elif was_playing and not playing:
                logger.debug("No game is running any more")
                self._run("stop", self._config.on_stop)
        finally:
            self._seen = _Seen(playing, self._now())

    def playing(self) -> bool:
        """Whether the last poll saw a game and is recent enough to believe: at most three
        polls old, counted from the end of the routine it ran."""
        seen = self._seen
        if seen is None or not seen.playing:
            return False
        return seen.at is None or self._now() - seen.at <= self._stale_after

    def holding(self) -> bool:
        """Whether her own announcements wait now: a game runs, and the mode holds them."""
        return self._config.announcements != "normal" and self.playing()

    def diverting(self) -> bool:
        """Whether a notice that also went to the phone is left unsaid now."""
        return self._config.announcements == "phone" and self.playing()

    def _run(self, when: str, name: str | None) -> None:
        """Run the routine `name`, if any, logging each failed step (the registry turns a
        device's failure into a failed step, and the watcher logs anything raised)."""
        if name is None:
            return
        routine = self._routines[name]
        results = run_routine(
            self._registry, routine, name=name, schedule=self._schedule, now=self._now
        )
        for number, (step, result) in enumerate(zip(routine.steps, results, strict=True), 1):
            if not result.ok:
                logger.warning(
                    "The game %s routine '%s' failed at step %d (%s): %s",
                    when,
                    name,
                    number,
                    describe_step(step),
                    result.message,
                )
        logger.debug("Ran the game %s routine '%s'", when, name)
