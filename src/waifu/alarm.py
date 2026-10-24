"""Master's wake-up alarm: the ring that goes on until he stops it.

An alarm is a schedule entry (`AlarmAction`). When one comes due, the scheduler starts the
`AlarmBell` and marks the entry fired; the voice loop then asks the bell between Master's
utterances for the ring that is due (`due_ring`) and says it, a little louder each time, until
Master stops it (by voice, the brain's `alarm` tool, or a quick button), snoozes it, or the
ring's minutes have passed. The bell never plays anything itself and starts no thread.

The ring lives in a small file while it rings, written atomically and deleted when it stops, so
a restart of `waifu run` mid-ring goes on and another process (`waifu tombol`, `waifu chat`)
can stop or snooze it. The voice loop asks at every audio frame, so a state read is kept for
`REREAD_S` before the file is read again; a ring about to be said is always read afresh. A file
that is not a ring is moved aside, never overwritten, and one whose ring has ended is deleted,
read afresh first, so a ring another process has just started is never taken for it.
Reading never raises into the voice loop: a file that cannot be read is no ring, warned once,
and an ended ring's file that cannot be deleted is tried again at each read, logged once.

While it rings, Master's whole utterance may stop or snooze it without the brain
(`alarm_request`): only a small set of whole phrases counts, as for "ulangi" (`waifu.repeat`),
so "matikan lampu" or a bare "stop" never touches the alarm.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from waifu.config import at_sentence_start
from waifu.gacha.files import write_json_atomic
from waifu.schedule import (
    MAX_ALARM_LABEL_CHARS,
    AlarmAction,
    Entry,
    ScheduleError,
    ScheduleStore,
)
from waifu.voice.volume import LEVELS, NORMAL, STEP

logger = logging.getLogger(__name__)

FILE_VERSION = 1
REREAD_S = 1.0  # how long a state read is kept before the file is read again
SNOOZE_MINUTES = (1, 30)
_STATE_KEYS = {"version", "started", "until", "alarms"}
_ALARM_KEYS = {"id", "at", "label"}
_ENTRY_ID = re.compile(r"j[1-9][0-9]*")
# Said in turn, one per ring: short, since the microphone is paused while she speaks.
_RING_LINES = (
    "{Owner}, bangun! Sudah jam {time}.",
    "Ayo bangun, {owner}! Sudah jam {time}.",
    "{Owner}, bangun dong! Sudah jam {time}.",
)
_SENTENCE_ENDS = (".", "!", "?")

# Runs of letters and digits in any script, as `waifu.repeat` splits Master's words.
_WORD = re.compile(r"[^\W_]+")
# Always left out, like her names: "master", and words that soften without changing the request.
_SKIPPED = frozenset({"master", "dong", "deh", "ya", "iya", "oke", "ok", "kok", "nih", "tolong"})
_STOP_PHRASES = frozenset(
    {
        ("aku", "sudah", "bangun"),
        ("aku", "udah", "bangun"),
        ("sudah", "bangun"),
        ("udah", "bangun"),
        *(
            (verb, alarm)
            for verb in ("matikan", "matiin", "stop", "hentikan")
            for alarm in ("alarm", "alarmnya")
        ),
    }
)
_SNOOZE_PHRASES = frozenset(
    {
        ("tunda",),
        ("tunda", "dulu"),
        ("tunda", "alarm"),
        ("tunda", "alarmnya"),
        ("snooze",),
        ("sebentar", "lagi"),
        ("bentar", "lagi"),
    }
)
_NUMBER_WORDS = {
    ("satu",): 1,
    ("dua",): 2,
    ("tiga",): 3,
    ("empat",): 4,
    ("lima",): 5,
    ("enam",): 6,
    ("tujuh",): 7,
    ("delapan",): 8,
    ("sembilan",): 9,
    ("sepuluh",): 10,
    ("lima", "belas"): 15,
    ("dua", "puluh"): 20,
    ("tiga", "puluh"): 30,
}
_DIGITS = re.compile(r"[1-9][0-9]?")


@dataclass(frozen=True)
class RingingAlarm:
    """One alarm of the ring: its schedule entry's id and time, and its label."""

    id: str
    at: datetime
    label: str | None


@dataclass(frozen=True)
class RingState:
    """The ring under way: when it started, when it ends by itself, and its alarms."""

    started: datetime
    until: datetime
    alarms: tuple[RingingAlarm, ...]

    @property
    def labels(self) -> list[str]:
        """Every label once, in the order the alarms joined."""
        return list(dict.fromkeys(alarm.label for alarm in self.alarms if alarm.label))


@dataclass(frozen=True)
class AlarmRequest:
    """Master's words to a ringing alarm: stop it, or snooze it (`minutes`, None for the
    bell's own)."""

    kind: Literal["stop", "snooze"]
    minutes: int | None


@dataclass(frozen=True)
class Ring:
    """A ring to say now: its number from 0, the level to say it at, and its words."""

    index: int
    level: int
    text: str


class AlarmBell:
    def __init__(
        self,
        path: Path | str,
        schedule: ScheduleStore,
        *,
        repeat_seconds: int = 60,
        max_minutes: int = 10,
        snooze_minutes: int = 5,
        day_level: Callable[[], int] = lambda: NORMAL,
        owner_call: str = "Master",
        now: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`schedule` takes a snoozed alarm; `day_level` is her level without whisper mode, the
        first ring's; `now` is the PC's local time (naive), as the schedule's; `clock` times how
        long a state read is kept."""
        self.path = Path(path)
        self.repeat_seconds = repeat_seconds
        self.max_minutes = max_minutes
        self.snooze_minutes = snooze_minutes
        self._schedule = schedule
        self._repeat = timedelta(seconds=repeat_seconds)
        self._ring_for = timedelta(minutes=max_minutes)
        self._day_level = day_level
        self._owner_call = owner_call
        self._now = now
        self._clock = clock
        self._lock = threading.Lock()
        self._kept: RingState | None = None  # the state last read
        self._read_at: float | None = None  # when, on `clock`
        self._said: tuple[datetime, int] | None = None  # (started, index) of the last ring said
        self._unreadable = False  # warned that the file cannot be read
        self._undeletable: datetime | None = None  # an ended ring left undeleted, logged once

    def ringing(self) -> bool:
        """Whether an alarm rings, from a state read at most REREAD_S ago; never raises."""
        with self._lock:
            return self._state(fresh=False) is not None

    def ring_state(self) -> RingState | None:
        """The ring under way, read afresh; OSError when the file cannot be read."""
        with self._lock:
            self._kept, self._read_at = self._read_file(), self._clock()
            return self._unless_ended(self._kept)

    def start(self, entry: Entry) -> None:
        """Ring for the alarm `entry` from now; while a ring is under way the alarm joins it."""
        action = entry.action
        if not isinstance(action, AlarmAction):
            raise ValueError(f"schedule entry {entry.id} is no alarm")
        joining = RingingAlarm(entry.id, entry.at, action.label)
        with self._lock:
            now = self._now()
            state = self._state(fresh=True)
            if state is None:
                state = RingState(now, now + self._ring_for, (joining,))
            elif any(alarm.id == entry.id for alarm in state.alarms):
                return
            else:
                until = max(state.until, now + self._ring_for)
                state = RingState(state.started, until, (*state.alarms, joining))
            self._write(state)
        logger.info("Alarm %s rings until %s", entry.id, f"{state.until:%H:%M}")

    def due_ring(self) -> Ring | None:
        """The ring to say now, once per ring, or None; the file is read afresh before one is
        given, so a ring stopped elsewhere is never said. Never raises."""
        with self._lock:
            if self._due_index(self._state(fresh=False)) is None:
                return None
            state = self._state(fresh=True)
            index = self._due_index(state)
            if state is None or index is None:
                return None
            self._said = (state.started, index)
        low, high = LEVELS
        level = min(high, max(low, self._day_level() + STEP * index))
        return Ring(index, level, self._ring_text(index, state.labels))

    def stop(self) -> bool:
        """End the ring; False when none rang."""
        with self._lock:
            if self._state(fresh=True) is None:
                return False
            self._remove()
        logger.info("Alarm stopped")
        return True

    def snooze(self, minutes: int | None = None) -> Entry | None:
        """End the ring and schedule it again as one alarm after `minutes` (the bell's snooze
        minutes when None), rounded up to a whole minute; None when none rang. A refusal of the
        schedule (ScheduleError, a full schedule) is raised, and the ring goes on; so is an
        OSError of the ring's file, with the alarm just added taken back, so a snooze tried
        again never leaves two."""
        minutes = self.snooze_minutes if minutes is None else minutes
        low, high = SNOOZE_MINUTES
        if not low <= minutes <= high:
            raise ValueError(f"snooze takes {low} to {high} minutes")
        with self._lock:
            state = self._state(fresh=True)
            if state is None:
                return None
            at = _minute_up(self._now() + timedelta(minutes=minutes))
            entry = self._schedule.add(at, "none", AlarmAction(_snoozed_label(state.labels)))
            try:
                self._remove()
            except OSError:
                self._take_back(entry)
                raise
        logger.info("Alarm snoozed until %s as %s", f"{entry.at:%H:%M}", entry.id)
        return entry

    def _take_back(self, entry: Entry) -> None:
        try:
            self._schedule.cancel(entry.id)
        except (ScheduleError, OSError) as exc:
            logger.warning("Could not take back the snoozed alarm %s: %s", entry.id, exc)

    def _due_index(self, state: RingState | None) -> int | None:
        """The ring of now when it has not been said yet, else None."""
        if state is None:
            return None
        index = max(0, (self._now() - state.started) // self._repeat)
        said = self._said
        if said is not None and said[0] == state.started and index <= said[1]:
            return None
        return index

    def _ring_text(self, index: int, labels: Sequence[str]) -> str:
        line = _RING_LINES[index % len(_RING_LINES)].format(
            Owner=at_sentence_start(self._owner_call),
            owner=self._owner_call,
            time=f"{self._now():%H.%M}",
        )
        return _with_labels(line, labels)

    def _state(self, *, fresh: bool) -> RingState | None:
        """The ring under way, read again when `fresh`, kept longer than REREAD_S, or ended (the
        file is deleted only as read just now: another process may have started a new ring); a
        file that cannot be read is no ring, warned once until it reads again."""
        kept_for = None if self._read_at is None else self._clock() - self._read_at
        kept = self._kept
        ended = kept is not None and self._now() >= kept.until
        if fresh or ended or kept_for is None or kept_for >= REREAD_S:
            self._kept, self._read_at = self._read_or_none(), self._clock()
        return self._unless_ended(self._kept)

    def _read_or_none(self) -> RingState | None:
        try:
            state = self._read_file()
        except OSError as exc:
            if not self._unreadable:
                logger.warning(
                    "Could not read the alarm file %s (%s); taking it as no alarm ringing",
                    self.path,
                    exc,
                )
            self._unreadable = True
            return None
        self._unreadable = False
        return state

    def _unless_ended(self, state: RingState | None) -> RingState | None:
        """`state`, or None once its ring has ended, when its file is deleted; a file that
        cannot be deleted is tried again at each read, logged once."""
        if state is None or self._now() < state.until:
            return state
        logged = self._undeletable == state.started
        if not logged:
            logger.info("The alarm rang until %s without being stopped", f"{state.until:%H:%M}")
        try:
            self._remove()
        except OSError as exc:
            if not logged:
                logger.warning("Could not delete the ended alarm's file %s: %s", self.path, exc)
            self._undeletable, self._kept = state.started, None
        else:
            self._undeletable = None
        return None

    def _read_file(self) -> RingState | None:
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return None
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem)
            return None

    def _write(self, state: RingState) -> None:
        write_json_atomic(
            self.path,
            {
                "version": FILE_VERSION,
                "started": state.started.isoformat(timespec="seconds"),
                "until": state.until.isoformat(timespec="seconds"),
                "alarms": [
                    {
                        "id": alarm.id,
                        "at": alarm.at.isoformat(timespec="minutes"),
                        "label": alarm.label,
                    }
                    for alarm in state.alarms
                ],
            },
        )
        self._kept, self._read_at = state, self._clock()

    def _remove(self) -> None:
        self.path.unlink(missing_ok=True)
        self._kept, self._read_at = None, self._clock()

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not a ring, so that no write ever replaces it."""
        name = f"{self.path.name}.corrupt-{self._now():%Y%m%d-%H%M%S}"
        target = self.path.with_name(name)
        counter = 2
        while target.exists():
            target = self.path.with_name(f"{name}-{counter}")
            counter += 1
        os.rename(self.path, target)
        logger.error(
            "The alarm file %s is not a ring (%s); moved it to %s", self.path, problem, target
        )


def alarm_request(text: str, names: Iterable[str] = ()) -> AlarmRequest | None:
    """Whether `text` asks a ringing alarm to stop or snooze and nothing else, such as "aku
    sudah bangun" or "Cyrene, tunda 10 menit".

    `names` are her name, the wake words she answers to, and what she calls the owner; their
    words are left out with "master" and the softeners ("dong", "iya", "tolong", ...), and the
    words left must be one of the phrases exactly. Snooze minutes are 1 to 30, in digits or as
    words ("lima", "lima belas").
    """
    skipped = _SKIPPED | {word for name in names for word in _words(name)}
    left = tuple(word for word in _words(text) if word not in skipped)
    if left in _STOP_PHRASES:
        return AlarmRequest("stop", None)
    if left in _SNOOZE_PHRASES:
        return AlarmRequest("snooze", None)
    minutes = None
    if len(left) >= 3 and left[0] in ("tunda", "snooze") and left[-1] == "menit":
        minutes = _minutes(left[1:-1])
    elif len(left) >= 3 and left[-2:] == ("menit", "lagi"):
        minutes = _minutes(left[:-2])
    if minutes is None or not SNOOZE_MINUTES[0] <= minutes <= SNOOZE_MINUTES[1]:
        return None
    return AlarmRequest("snooze", minutes)


def _minutes(words: tuple[str, ...]) -> int | None:
    """The whole number `words` say ("10", "sepuluh", "lima belas"), or None."""
    if len(words) == 1 and _DIGITS.fullmatch(words[0]):
        return int(words[0])
    return _NUMBER_WORDS.get(words)


def _words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", text).lower())


def wake_text(at: datetime, labels: Sequence[str], owner_call: str) -> str:
    """`Master, bangun! Alarm jam 06.00.` with the labels: the push of a ring, and the line said
    once where nothing rings."""
    return _with_labels(f"{at_sentence_start(owner_call)}, bangun! Alarm jam {at:%H.%M}.", labels)


def missed_text(at: datetime, label: str | None, today: date) -> str:
    """`Alarm jam 06.00 terlewat.`, with the date when it was not today's and the label."""
    when = f"jam {at:%H.%M}" if at.date() == today else f"{at:%d-%m-%Y} jam {at:%H.%M}"
    return f"Alarm {when} terlewat" + ("" if label is None else f": {label}") + "."


def _with_labels(text: str, labels: Sequence[str]) -> str:
    """`text` followed by the labels as a sentence of their own."""
    if not labels:
        return text
    told = at_sentence_start(", ".join(labels))
    return f"{text} {told}" + ("" if told.endswith(_SENTENCE_ENDS) else ".")


def _snoozed_label(labels: Sequence[str]) -> str | None:
    joined = ", ".join(labels)
    if len(joined) <= MAX_ALARM_LABEL_CHARS:
        return joined or None
    return labels[0]


def _minute_up(moment: datetime) -> datetime:
    whole = moment.replace(second=0, microsecond=0)
    return whole if whole == moment else whole + timedelta(minutes=1)


def _parse(raw: object) -> RingState:
    """The ring in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _STATE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_STATE_KEYS)}")
    version = raw["version"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    started = _parse_time(raw["started"], "started")
    until = _parse_time(raw["until"], "until")
    if until < started:
        raise ValueError("until must not be before started")
    items = raw["alarms"]
    if not isinstance(items, list) or not items:
        raise ValueError("alarms must be a list of at least one alarm")
    return RingState(started, until, tuple(_parse_alarm(i, item) for i, item in enumerate(items)))


def _parse_alarm(index: int, item: object) -> RingingAlarm:
    where = f"alarms[{index}]"
    if not isinstance(item, dict) or item.keys() != _ALARM_KEYS:
        raise ValueError(f"{where} must be an object with exactly the keys {sorted(_ALARM_KEYS)}")
    alarm_id, label = item["id"], item["label"]
    if not isinstance(alarm_id, str) or not _ENTRY_ID.fullmatch(alarm_id):
        raise ValueError(f"{where}.id must look like j1")
    if label is not None and (
        not isinstance(label, str) or not label.strip() or len(label) > MAX_ALARM_LABEL_CHARS
    ):
        raise ValueError(f"{where}.label must be null or text of 1 to {MAX_ALARM_LABEL_CHARS}")
    return RingingAlarm(alarm_id, _parse_time(item["at"], f"{where}.at"), label)


def _parse_time(value: object, where: str) -> datetime:
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed
    raise ValueError(f"{where} must be a local ISO 8601 time without a time zone")
