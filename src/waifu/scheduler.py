"""The scheduler inside `waifu run`: fires reminders, device commands, and routines on time.

Every `tick_seconds` it asks the schedule what is due. A reminder is spoken and pushed to
the phone; a device command or a routine runs without a word (Master may be asleep), and
only a failure is spoken and pushed. Each occurrence is then marked fired, which removes a
one-shot entry and moves a repeating one on. An occurrence missed by more than the grace
(the PC or Cyrene was off) is marked without running: a one-shot entry is removed, and a
repeating one moves to its first occurrence still within the grace, so after days away
today's still fires. A missed reminder is pushed; a missed one-shot, monthly, or yearly
reminder is also told, all of a tick's together in one announcement, so it is never lost
when the phone gets no push (one that repeats within a week comes again soon anyway; a bill
or a birthday would come back only a month or a year later). The PC itself never runs from
a schedule. A routine's delayed steps ("mode tidur": the AC off three hours later) go into
this schedule when the routine runs, counted from then, and fire later like any device
command.

It runs in a daemon thread. A failing entry is logged and the other entries go on; an
occurrence that ran but could not be marked is marked again at the next tick, never run
twice.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from types import TracebackType

from waifu.config import RoutineConfig, at_sentence_start
from waifu.devices.registry import DeviceRegistry
from waifu.notify import Notifier
from waifu.routines import describe_step, pc_power_refusal, run_routine
from waifu.schedule import DeviceAction, Entry, Reminder, ScheduleStore, due

logger = logging.getLogger(__name__)

SCHEDULE_TAG = "alarm_clock"  # ntfy shows it as an alarm clock emoji
# The repeats of a missed reminder that is told as well as pushed: it never comes again, or
# only a month or a year later. One that comes again within a week is only pushed.
_TOLD_WHEN_MISSED = frozenset({"none", "monthly", "yearly"})
# Indexed by month - 1; spelled out so the OS locale never leaks in.
MONTHS_ID = (
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
)
STOP_TIMEOUT_S = 5.0


class Scheduler:
    def __init__(
        self,
        store: ScheduleStore,
        *,
        registry: DeviceRegistry,
        routines: Mapping[str, RoutineConfig],
        announce: Callable[[str, str], None] | None,
        notifier: Notifier | None,
        grace_minutes: int,
        title: str = "Cyrene",
        owner_call: str = "Master",
        now: Callable[[], datetime] = datetime.now,
        tick_seconds: float = 15,
    ) -> None:
        self._store = store
        self._registry = registry
        self._routines = routines
        self._announce = announce
        self._notifier = notifier
        self._grace = timedelta(minutes=grace_minutes)
        self._title = title
        self._owner_call = owner_call
        self._now = now
        self._tick_seconds = tick_seconds
        # (id, at) of occurrences that ran but whose fired mark could not be saved.
        self._unmarked: set[tuple[str, datetime]] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick_once(self) -> None:
        """Skip what was missed by more than the grace, fire what is due, and mark them all.

        One entry at a time, soonest first, each from a fresh read of the schedule: an entry
        cancelled while an earlier one ran never runs, and one that came due meanwhile (after
        a slow routine) runs in this tick. The grace counts back from the start of the tick:
        Cyrene was busy, not away. An entry is missed only when it is also older than the
        minute of the check before this one, `tick_seconds` ago: one made in that minute after
        the check ("in one minute", said just before the minute changed) still fires, also
        with no grace.
        """
        started = self._now()
        earliest = min(
            _minute(started) - self._grace,
            _minute(started - timedelta(seconds=self._tick_seconds)),
        )
        handled: set[tuple[str, datetime]] = set()
        missed: list[tuple[datetime, str]] = []
        while (entry := self._next_due(earliest, handled)) is not None:
            handled.add((entry.id, entry.at))
            self._settle(entry, earliest, missed)
        if missed:
            self._say(_missed_text(missed, started, self._owner_call), "sad")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        logger.info("Checking the schedule %s every %g s", self._store.path, self._tick_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def __enter__(self) -> Scheduler:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception:
                logger.exception("A schedule tick failed; trying again at the next one")
            self._stop.wait(self._tick_seconds)

    def _next_due(self, earliest: datetime, handled: set[tuple[str, datetime]]) -> Entry | None:
        """The soonest occurrence due now that this tick has not handled yet."""
        # Minute precision: an entry is due during its whole minute, also with no grace.
        now = _minute(self._now())
        fire, skip = due(self._store.entries(), now, now - earliest)
        return next(
            (entry for entry in [*skip, *fire] if (entry.id, entry.at) not in handled), None
        )

    def _settle(self, entry: Entry, earliest: datetime, missed: list[tuple[datetime, str]]) -> None:
        """Fire one occurrence, or skip it when it is older than `earliest`, once; then mark it.

        Failures are logged, never raised. A failed occurrence is marked too: retrying it
        every tick would repeat it until the grace ran out. A skipped repeating entry moves
        to its first time not older than `earliest`, which fires next when it is due. A
        skipped one-shot, monthly, or yearly reminder is added to `missed` (its time and text)
        for the tick to tell.
        """
        late = entry.at < earliest
        occurrence = (entry.id, entry.at)
        if occurrence not in self._unmarked:
            try:
                if late:
                    self._skip(entry, missed)
                else:
                    self._fire(entry)
            except Exception:
                logger.exception("Schedule entry %s failed", entry.id)
        try:
            if late:
                self._store.skipped(entry.id, entry.at, earliest)
            else:
                self._store.fired(entry.id, entry.at)
        except Exception:
            logger.exception(
                "Could not mark schedule entry %s as handled; marking it at the next tick", entry.id
            )
            self._unmarked.add(occurrence)
        else:
            self._unmarked.discard(occurrence)

    def _fire(self, entry: Entry) -> None:
        """Say and push a reminder; run a device command or a routine, telling only a failure."""
        action = entry.action
        if isinstance(action, Reminder):
            logger.info("Schedule entry %s: reminding Master", entry.id)
            self._say(action.text, "happy")
            self._push(action.text)
            return
        if isinstance(action, DeviceAction):
            what = _command_words(action.device, action.command, action.value)
            if action.routine is not None:  # a routine's delayed step: say where it came from
                what = f"{what} dari rutinitas {action.routine}"
            problem = self._device_problem(action)
        else:
            what = f"rutinitas {action.name}"
            problem = self._routine_problem(action.name)
        if problem is None:
            logger.info("Schedule entry %s ran %s", entry.id, what)
            return
        logger.warning("Schedule entry %s could not run %s: %s", entry.id, what, problem)
        owner = at_sentence_start(self._owner_call)
        text = f"{owner}, jadwal {what} jam {entry.at:%H.%M} gagal: {problem}"
        self._say(text, "sad")
        self._push(text)

    def _skip(self, entry: Entry, missed: list[tuple[datetime, str]]) -> None:
        """Push a missed reminder, and keep it in `missed` to tell when it repeats only monthly
        or yearly or never (`_TOLD_WHEN_MISSED`); nothing runs.

        The push names the day too when the missed one was not today's, so it never reads as
        if today's had been missed.
        """
        logger.warning(
            "Schedule entry %s due at %s was missed by more than %s; %s",
            entry.id,
            f"{entry.at:%Y-%m-%d %H:%M}",
            self._grace,
            "removing it" if entry.repeat == "none" else "moving it to its next time",
        )
        if isinstance(entry.action, Reminder):
            when = f"{entry.at:%H.%M}"
            if entry.at.date() != self._now().date():
                when = f"{entry.at:%d-%m-%Y} {when}"
            # Kept to tell before the push, which may raise when the phone is unreachable.
            if entry.repeat in _TOLD_WHEN_MISSED:
                missed.append((entry.at, entry.action.text))
            self._push(f"Terlewat ({when}): {entry.action.text}")

    def _device_problem(self, action: DeviceAction) -> str | None:
        """Why the device command failed, or None when it ran; nothing powering the PC runs."""
        refusal = pc_power_refusal(self._registry, action.device, "jadwal")
        if refusal is not None:
            logger.warning(
                "Refused a scheduled command on '%s', which powers the PC", action.device
            )
            return refusal
        result = self._registry.execute(action.device, action.command, action.value)
        return None if result.ok else result.message

    def _routine_problem(self, name: str) -> str | None:
        """The failed steps of the routine, or None when every step ran or was scheduled.

        Its delayed steps go into this schedule, counted from now: when it actually ran.
        """
        routine = self._routines.get(name)
        if routine is None:
            return f"Rutinitas '{name}' tidak ada di config.yaml."
        results = run_routine(
            self._registry, routine, name=name, schedule=self._store, now=self._now
        )
        failed = [
            f"Langkah {number} ({describe_step(step)}): {result.message}"
            for number, (step, result) in enumerate(zip(routine.steps, results, strict=True), 1)
            if not result.ok
        ]
        return " ".join(failed) or None

    def _say(self, text: str, emotion: str) -> None:
        if self._announce is not None:
            self._announce(text, emotion)

    def _push(self, text: str) -> None:
        if self._notifier is not None:
            self._notifier.send(self._title, text, tags=[SCHEDULE_TAG])


def _minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def _missed_text(missed: list[tuple[datetime, str]], now: datetime, owner_call: str) -> str:
    """One announcement for every one-shot reminder a tick found missed, oldest first."""

    def when(at: datetime) -> str:
        if at.date() == now.date():
            return f"jam {at:%H.%M}"
        day = f"tanggal {at.day}"
        if (at.year, at.month) != (now.year, now.month):
            day += f" {MONTHS_ID[at.month - 1]}"
        if at.year != now.year:
            day += f" {at.year}"
        return f"{day} jam {at:%H.%M}"

    texts = [(when(at), text) for at, text in missed]
    owner = at_sentence_start(owner_call)
    if len(texts) == 1:
        [(moment, text)] = texts
        return f"{owner}, ada pengingat yang terlewat {moment}: {text}"
    told = " ".join(f"{at_sentence_start(moment)}: {text}" for moment, text in texts)
    return f"{owner}, ada {len(texts)} pengingat yang terlewat. {told}"


def _command_words(device: str, command: str, value: int | None) -> str:
    """`ac temp 26`: a device command as the owner wrote it."""
    return f"{device} {command}" if value is None else f"{device} {command} {value}"
