"""The watcher inside `waifu run` that counts how long Master's games are open, and the
optional bedtime nudge.

Every `poll_seconds`, timed from the start of the previous poll and first right after it starts,
it lists the running processes (`processes.running_executables`: names only, never a handle on a
game) and follows the configured games into sessions with the rules of `play_time.py`: the run's
first poll resumes a session seen shortly before, the others follow it. It writes the file when
the open sessions changed or a session finished, so about once a minute while a game is open and
never while none is. It polls all day: play is often late at night. `stop()` ends every open
session at its last poll in one write.

With `play_time.bedtime` set, the night runs from that time until 06:00. At a poll inside it that
sees a game opened before the night's 04:00 (or before bedtime, when that is later), she says once
that night, through the patient announce queue (never while Master speaks or the follow-up window
is open): "{owner}, sudah lewat jam setengah dua belas, jangan terlalu malam ya." A game first
opened after that is an early morning, such as the dailies after the 03:00 reset, not a late
night. The night is written to the file before she says it, so a restart never repeats it; a
write that fails says nothing, and the next poll tries again.

After each poll that worked it tells game mode (`on_poll`) whether a tracked game is running,
one missed at a single poll still counting (the two-poll rule: alt-tab or a launcher restart
changes nothing), and whether the run's first poll went on with a session from before a restart
of `waifu run` (a first poll whose write fails leaves that to the next poll that works). Never for
a failed poll, and never from `stop()`.

After each poll that worked it also tells `on_finished` of every session that poll finished
within `SLEEP_POLLS` polls of its end: a game Master just closed, for the gacha planner's
overnight line. A session ended by a sleep, by a restart's first poll after a longer gap, or by
the clock set back, was not just closed and is not told, nor one of a game that poll still sees;
one finished by a poll whose write failed is told at the next poll that works. Never from
`stop()`. `ClosedGames` carries those sessions, and `on_poll`'s word on whether a game is open,
to the gacha watcher's thread.

After each poll that worked, last, it tells `on_started` of every game Master just opened
(`play_time.opened_games`), for the reminders he asked for at its next start (v1.53). Never at the
run's first poll, which finds running whatever was opened while `waifu run` was off, nor after a
sleep or a clock step, after which every running game starts afresh; a start found by a poll whose
write failed is told at the next poll that works. Never from `stop()`.

A failed listing, read, or write is never said: the first of a run of failures is logged at
WARNING and the next poll tries again. What she sees is Master's own: sessions are logged only
at DEBUG. Nothing is raised.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Collection
from datetime import date, datetime, timedelta
from datetime import time as ClockTime  # the module `time` gives time.monotonic

from waifu.avatar.emotion import NEUTRAL
from waifu.config import NIGHT_EARLIEST, NIGHT_ENDS, GameId, PlayTimeConfig, at_sentence_start
from waifu.finance.values import spoken_rupiah as spoken_number
from waifu.play_time import (
    SLEEP_POLLS,
    PlayTimeStore,
    Session,
    Tracking,
    follow_games,
    games_running,
    opened_games,
    resume_games,
    wib_now,
)
from waifu.rain_watch import Wait, spoken_hour

logger = logging.getLogger(__name__)

NOTICE_EMOTION = NEUTRAL  # a gentle word, not a scolding
STOP_TIMEOUT_S = 5.0
# A night's key is its evening's date: 12 hours back from any moment of it, 18:00 until 06:00.
_NIGHT_SHIFT = timedelta(hours=12)
# A game first opened from here on, after bedtime, is an early morning (the Asia servers' daily
# reset is at 03:00 WIB), not a late night: no nudge.
_DAWN = ClockTime(4, 0)


def spoken_clock(at: ClockTime) -> str:
    """`jam setengah dua belas`: a clock time as she says it, whole hours with their part of
    the day."""
    if at.minute == 0:
        return spoken_hour(at.hour)
    if at.minute == 30:
        return f"jam setengah {_hour_word(at.hour + 1)}"
    return f"jam {_hour_word(at.hour)} {spoken_number(at.minute)}"


def _hour_word(hour: int) -> str:
    """The hour on a 12-hour clock: "dua belas" for 0 and 12."""
    return spoken_number(hour % 12 or 12)


def in_night(now: datetime, bedtime: ClockTime) -> bool:
    """Whether `now` is from `bedtime` until the morning's NIGHT_ENDS."""
    moment = now.time()
    if bedtime >= NIGHT_EARLIEST:
        return moment >= bedtime or moment < NIGHT_ENDS
    return bedtime <= moment < NIGHT_ENDS


def night_of(now: datetime) -> date:
    """The evening's date of the night `now` is in: Sunday 01:00 is Saturday's night."""
    return (now - _NIGHT_SHIFT).date()


def _opened_late_before(now: datetime, bedtime: ClockTime) -> datetime:
    """The moment a game must have been opened before to get the nudge in the night `now` is
    in: that night's 04:00, or its bedtime when that is later."""
    clock = bedtime if _DAWN <= bedtime < NIGHT_ENDS else _DAWN
    return datetime.combine(night_of(now) + timedelta(days=1), clock, tzinfo=now.tzinfo)


def _resumed(tracking: Tracking) -> bool:
    """Whether a game the run's first poll found running went on with a session from before
    the restart (its start is earlier)."""
    return any(t.session.start < tracking.last_poll for t in tracking.open)


class ClosedGames:
    """What the watcher tells the gacha planner (v1.50), from its thread to the gacha
    watcher's: each session of a game Master just closed (`put`, as `on_finished`), and whether
    a tracked game was open at its last poll that worked (`observe`, as `on_poll`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: list[Session] = []
        self._playing = False  # replaced whole, so it is read without the lock

    def put(self, session: Session) -> None:
        with self._lock:
            self._sessions.append(session)

    def observe(self, playing: bool, resumed: bool) -> None:
        """A poll's word: whether a tracked game is open, one missed at a single poll still
        counting; a resume changes nothing here."""
        self._playing = playing

    def take(self) -> list[Session]:
        """The sessions put since the last take, in order."""
        with self._lock:
            sessions, self._sessions = self._sessions, []
        return sessions

    def playing(self) -> bool:
        return self._playing


class PlayTimeWatcher:
    def __init__(
        self,
        list_processes: Callable[[], Collection[str]],
        store: PlayTimeStore,
        config: PlayTimeConfig,
        *,
        announce: Callable[[str, str], None] | None,
        owner_call: str,
        now: Callable[[], datetime] = wib_now,
        clock: Callable[[], float] = time.monotonic,
        wait: Wait = threading.Event.wait,
        on_poll: Callable[[bool, bool], None] | None = None,
        on_finished: Callable[[Session], None] | None = None,
        on_started: Callable[[GameId], None] | None = None,
    ) -> None:
        """`now` is aware WIB time, for the sessions and the night; `clock` monotonic seconds,
        only for the polls. `on_poll(playing, resumed)` hears after each poll that worked
        whether a tracked game is running and whether the run's first poll resumed one;
        `on_finished(session)` each session of a game just closed; `on_started(game)` each game
        just opened."""
        self._list_processes = list_processes
        self._store = store
        self._config = config
        self._announce = announce
        self._owner = at_sentence_start(owner_call)
        self._now = now
        self._clock = clock
        self._wait = wait
        self._on_poll = on_poll
        self._on_finished = on_finished
        self._on_started = on_started
        self._poll = timedelta(seconds=config.poll_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Polls and the stop's last write never overlap.
        self._lock = threading.Lock()
        self._stopped = False
        self._tracking: Tracking | None = None  # None until the run's first poll
        # The run's first poll resumed a session, and game mode has not heard it yet: kept
        # through a poll that fails after following the games (a write), or it would hear of a
        # game just opened and run on_start again.
        self._resumed = False
        self._written: tuple[Session, ...] = ()  # the open sessions the file holds
        self._unwritten: list[Session] = []  # finished, waiting for a write that works
        self._closed: list[Session] = []  # just finished, not told to on_finished yet
        self._opened: list[GameId] = []  # just opened, not told to on_started yet
        self._told: date | None = None  # the night the nudge was told
        self._failing = False  # the last poll failed

    def poll_once(self) -> list[str]:
        """List the processes, follow the games, write what changed, and give the bedtime
        nudge when it is due; returns what she said."""
        try:
            names = self._list_processes()
            with self._lock:
                if self._stopped:
                    return []
                now = self._now()
                seen = games_running(names, self._config.games)
                tracking = self._follow(seen, now)
                playing = [session for session in tracking.sessions if session.game in seen]
                said = self._bedtime(playing, now)
                running = bool(tracking.open), self._resumed
                self._resumed = False  # told below
                closed, self._closed = self._closed, []
                opened, self._opened = self._opened, []
        except Exception as exc:
            self._log_failure(exc)
            return []
        if self._failing:
            self._failing = False
            logger.info("Play time can be followed again")
        self._tell(*running)
        self._tell_closed(closed)
        self._tell_opened(opened)
        return said

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="play-time-watcher", daemon=True)
        self._thread.start()
        logger.info("Watching for game processes every %d s", self._config.poll_seconds)

    def stop(self) -> None:
        """Stop polling, then end every open session at its last poll in one write."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            if self._tracking is None:
                return
            ended = [session for session in self._tracking.sessions if session.end > session.start]
            if not (self._written or self._unwritten or ended):
                return
            try:
                self._store.save([], [*self._unwritten, *ended])
            except Exception:
                logger.exception("Could not write the open play time sessions on stopping")
                return
            logger.debug("Ended the open play sessions on stopping: %s", ended)

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            self.poll_once()
            self._wait(self._stop, max(0.0, started + self._config.poll_seconds - self._clock()))

    def _tell(self, playing: bool, resumed: bool) -> None:
        """Tell game mode, outside the lock (its routine may take a few seconds), unless the
        watcher is stopping; a failure is logged, never raised."""
        if self._on_poll is None or self._stop.is_set():
            return
        try:
            self._on_poll(playing, resumed)
        except Exception:
            logger.exception("Could not tell game mode whether a game is running")

    def _tell_closed(self, sessions: list[Session]) -> None:
        """Tell `on_finished` of the games just closed, outside the lock, unless the watcher is
        stopping; a failure is logged, never raised."""
        if self._on_finished is None or self._stop.is_set():
            return
        for session in sessions:
            try:
                self._on_finished(session)
            except Exception:
                logger.exception("Could not tell that a game was closed")

    def _tell_opened(self, games: list[GameId]) -> None:
        """Tell `on_started` of the games just opened, outside the lock, unless the watcher is
        stopping; a failure is logged, never raised."""
        if self._on_started is None or self._stop.is_set():
            return
        for game in games:
            try:
                self._on_started(game)
            except Exception:
                logger.exception("Could not tell that a game was opened")

    def _follow(self, seen: Collection[GameId], now: datetime) -> Tracking:
        """Follow the games into sessions and write what changed; returns the open sessions
        after the poll."""
        if self._tracking is None:
            record = self._store.record()
            self._written = record.open
            tracking, finished = resume_games(record, seen, now)
            self._resumed = _resumed(tracking)
        else:
            tracking, finished = follow_games(self._tracking, seen, now, poll=self._poll)
            # Kept before the write, which may fail; never at the run's first poll, which finds
            # running whatever was opened while `waifu run` was off.
            self._opened += opened_games(self._tracking, tracking, poll=self._poll)
        self._tracking = tracking
        if finished:
            logger.debug("Play sessions ended: %s", finished)
        self._unwritten += finished
        # Kept before the write, which may fail: a game just closed, never one a sleep, a long
        # restart, or the clock set back ended, nor one still running.
        self._closed += [
            session
            for session in finished
            if session.game not in seen
            and timedelta(0) <= now - session.end <= SLEEP_POLLS * self._poll
        ]
        if tracking.sessions != self._written or self._unwritten:
            self._store.save(tracking.sessions, self._unwritten)
            self._written, self._unwritten = tracking.sessions, []
        return tracking

    def _bedtime(self, playing: Collection[Session], now: datetime) -> list[str]:
        """The nudge, once a night, while a game opened before dawn is open past bedtime."""
        bedtime = self._config.bedtime
        if bedtime is None or not in_night(now, bedtime):
            return []
        before = _opened_late_before(now, bedtime)
        if not any(session.start < before for session in playing):
            return []
        night = night_of(now)
        if self._told == night:
            return []
        if self._store.record().bedtime_told == night:  # told before a restart
            self._told = night
            return []
        self._store.mark_bedtime(night)
        self._told = night
        text = f"{self._owner}, sudah lewat {spoken_clock(bedtime)}, jangan terlalu malam ya."
        logger.debug("A game is open past bedtime; telling Master once tonight")
        if self._announce is not None:
            try:
                self._announce(text, NOTICE_EMOTION)
            except Exception:
                logger.exception("Could not say the bedtime nudge")
        return [text]

    def _log_failure(self, exc: Exception) -> None:
        """The first failure of a run at WARNING (an unexpected one with its traceback), the
        rest at DEBUG: a broken listing or disk is one line, not one per minute."""
        traceback = not isinstance(exc, OSError)
        if self._failing:
            logger.debug("Play time still cannot be followed: %s", exc, exc_info=traceback)
            return
        self._failing = True
        logger.warning(
            "Could not follow play time (%s); trying again at every poll", exc, exc_info=traceback
        )
