"""The focus timer inside `waifu run`: one pomodoro session at a time, and her lines in it.

A session is `rounds` focus blocks of `minutes` with breaks of `break_minutes` between them. Each
phase starts at the previous one's planned end, so the session follows its plan exactly however
late a check comes. A daemon thread checks at once, then at least every `tick_seconds` and as a
phase ends, against the wall clock: a sleep never stretches a phase. At the end of a block she
says it is time for a break, at the end of a break that the next block starts, and at the end of
the last block she praises Master with the session's total; each line goes to `say` before the
phase changes, so what the voice loop holds while a block is under way (`in_block`) never comes
out before it. A phase that ended more than `LATE` ago means the PC slept or the clock jumped:
the session ends without a word, and the block under way is dropped.

Every finished block is logged in the focus log (`focus.py`), and so is the part of a block done
before the session was stopped, when it lasted at least a minute. The session itself lives only
in this process: `stop()`, so a restart, ends it. A failed write or a failing voice is logged and
the session goes on; nothing is raised from the thread.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from waifu.config import at_sentence_start
from waifu.finance.values import spoken_rupiah as spoken_number
from waifu.focus import FocusBlock, FocusTotal
from waifu.play_time import wib_now
from waifu.rain_watch import Wait

logger = logging.getLogger(__name__)

MINUTES_RANGE = (5, 120)
BREAK_MINUTES_RANGE = (1, 60)
ROUNDS_RANGE = (1, 8)
TICK_S = 15.0
# A phase that ended longer ago than this was slept through: the session ends quietly.
LATE = timedelta(minutes=2)
MIN_LOGGED = timedelta(minutes=1)  # a shorter part of a block is not logged when stopped
STOP_TIMEOUT_S = 5.0
FOCUS = "focus"
BREAK = "break"
LINE_EMOTION = "happy"
BLOCK_DONE = "{owner}, {minutes} menit selesai. Istirahat {break_minutes} menit dulu ya."
BREAK_DONE = "Yuk, lanjut fokus lagi."
SESSION_DONE = "{owner}, fokusnya selesai. Hebat, total {total}!"
NOT_RUNNING = "Timer fokus hanya berjalan selama 'waifu run' menyala."
ALREADY_RUNNING = "Sesi fokus sudah berjalan; hentikan dulu kalau Master mau mulai yang baru."

# Hands one of her lines and its emotion to the voice loop; called under the timer's lock, so
# it must only queue the line.
Say = Callable[[str, str], None]


class FocusError(Exception):
    """A session cannot begin; the message is Indonesian, for Master."""


@dataclass(frozen=True)
class FocusPlan:
    """A session: `rounds` blocks of `minutes`, with breaks of `break_minutes` between them."""

    minutes: int = 25
    break_minutes: int = 5
    rounds: int = 1

    def __post_init__(self) -> None:
        for name, (low, high) in (
            ("minutes", MINUTES_RANGE),
            ("break_minutes", BREAK_MINUTES_RANGE),
            ("rounds", ROUNDS_RANGE),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} must be a whole number from {low} to {high}")


@dataclass(frozen=True)
class FocusState:
    """A session's phase: `round` counts from 1; `focused_seconds` are its finished blocks."""

    plan: FocusPlan
    round: int
    phase: str  # FOCUS or BREAK
    phase_start: datetime
    ends_at: datetime
    focused_seconds: int


class BlockLog(Protocol):
    """The part of `FocusStore` the timer uses."""

    def add(self, block: FocusBlock) -> None: ...

    def totals(self, since: datetime, until: datetime) -> FocusTotal: ...


def spoken_duration(minutes: int) -> str:
    """`satu jam empat puluh menit`: a length of at least a minute as she says it."""
    hours, minutes = divmod(minutes, 60)
    parts = [f"{spoken_number(hours)} jam"] if hours else []
    if minutes:
        parts.append(f"{spoken_number(minutes)} menit")
    return " ".join(parts)


class FocusTimer:
    def __init__(
        self,
        store: BlockLog,
        *,
        owner_call: str,
        now: Callable[[], datetime] = wib_now,
        wait: Wait = threading.Event.wait,
        tick_seconds: float = TICK_S,
    ) -> None:
        """`now` is aware WIB time, for the phases and the log."""
        self._store = store
        self._owner = at_sentence_start(owner_call)
        self._now = now
        self._wait = wait
        self._tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # The thread's checks, `begin`, `end`, and `stop` change the session one at a time.
        self._lock = threading.Lock()
        self._say: Say | None = None  # connected while the timer runs
        # Replaced whole, never changed, so `in_block` and `state` read it without the lock.
        self._session: FocusState | None = None

    def start(self, say: Say) -> None:
        """Say the session's lines through `say` and check the phases from a daemon thread."""
        if self._thread is not None:
            return
        with self._lock:
            self._say = say
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="focus-timer", daemon=True)
        self._thread.start()
        logger.info("The focus timer runs, checking at least every %g s", self._tick_seconds)

    def stop(self) -> None:
        """Stop checking, then end a session in progress without a word (see `end`)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None
        with self._lock:
            self._say = None
        self.end()

    def begin(self, plan: FocusPlan) -> FocusState:
        """Start a session whose first block starts now; FocusError while the timer is not
        running or while a session runs."""
        now = _stamp(self._now())
        with self._lock:
            if self._say is None:
                raise FocusError(NOT_RUNNING)
            if self._session is not None:
                raise FocusError(ALREADY_RUNNING)
            self._session = FocusState(plan, 1, FOCUS, now, now + _minutes(plan.minutes), 0)
            session = self._session
        logger.info(
            "Focus session begun: %d x %d min with %d min breaks",
            plan.rounds,
            plan.minutes,
            plan.break_minutes,
        )
        return session

    def end(self) -> int | None:
        """End the session, logging the block under way up to now when it lasted at least a
        minute; the session's focused seconds, or None without a session."""
        now = self._now()
        with self._lock:
            session = self._session
            if session is None:
                return None
            self._session = None
            focused = session.focused_seconds
            if session.phase == FOCUS:
                end = _stamp(min(now, session.ends_at))
                if end - session.phase_start >= MIN_LOGGED:
                    self._log(FocusBlock(session.phase_start, end))
                    focused += _seconds(end - session.phase_start)
        logger.info("Focus session ended after %d s of focus", focused)
        return focused

    def state(self) -> FocusState | None:
        return self._session

    def in_block(self) -> bool:
        """Whether a focus block is under way; safe from any thread, and never waits."""
        session = self._session
        return session is not None and session.phase == FOCUS

    def focused_seconds(self, since: datetime, until: datetime) -> int:
        """The focus time inside `[since, until]`: the log's, and the part of the block under
        way."""
        total = self._store.totals(since, until).seconds
        session = self._session
        if session is not None and session.phase == FOCUS:
            start = max(session.phase_start, since)
            end = min(self._now(), session.ends_at, until)
            if end > start:
                total += _seconds(end - start)
        return total

    def tick_once(self) -> list[str]:
        """Handle every phase that has ended; returns what she said."""
        now = self._now()
        said: list[str] = []
        with self._lock:
            while (session := self._session) is not None and now >= session.ends_at:
                if now - session.ends_at > LATE:
                    logger.info(
                        "A focus %s ended %d s ago (the PC slept or the clock jumped); ending "
                        "the session quietly",
                        session.phase,
                        _seconds(now - session.ends_at),
                    )
                    self._session = None
                    break
                following, line = self._after(session)
                self._tell(line)  # before the phase changes
                said.append(line)
                self._session = following
        return said

    def _after(self, session: FocusState) -> tuple[FocusState | None, str]:
        """The phase after `session`'s, and the line that ends it."""
        plan, ended = session.plan, session.ends_at
        if session.phase == BREAK:
            following = dataclasses.replace(
                session,
                round=session.round + 1,
                phase=FOCUS,
                phase_start=ended,
                ends_at=ended + _minutes(plan.minutes),
            )
            return following, BREAK_DONE
        self._log(FocusBlock(session.phase_start, ended))
        focused = session.focused_seconds + _seconds(ended - session.phase_start)
        if session.round == plan.rounds:
            total = spoken_duration(focused // 60)
            return None, SESSION_DONE.format(owner=self._owner, total=total)
        following = dataclasses.replace(
            session,
            phase=BREAK,
            phase_start=ended,
            ends_at=ended + _minutes(plan.break_minutes),
            focused_seconds=focused,
        )
        line = BLOCK_DONE.format(
            owner=self._owner,
            minutes=spoken_number(plan.minutes),
            break_minutes=spoken_number(plan.break_minutes),
        )
        return following, line

    def _log(self, block: FocusBlock) -> None:
        try:
            self._store.add(block)
        except Exception as exc:
            logger.warning(
                "Could not log a focus block (%s); the session goes on",
                exc,
                exc_info=not isinstance(exc, OSError),
            )

    def _tell(self, line: str) -> None:
        say = self._say
        if say is None:
            return
        try:
            say(line, LINE_EMOTION)
        except Exception:
            logger.exception("Could not say a focus line")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception:
                logger.exception("A focus timer check failed; trying again at the next one")
            self._wait(self._stop, self._until_next_check())

    def _until_next_check(self) -> float:
        """A tick, or less when the phase ends sooner."""
        session = self._session
        if session is None:
            return self._tick_seconds
        left = (session.ends_at - self._now()).total_seconds()
        return min(self._tick_seconds, max(0.0, left))


def _minutes(count: int) -> timedelta:
    return timedelta(minutes=count)


def _seconds(span: timedelta) -> int:
    return int(span.total_seconds())


def _stamp(moment: datetime) -> datetime:
    return moment.replace(microsecond=0)
