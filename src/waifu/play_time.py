"""How long Master's games were open: sessions, periods, totals, and the file that keeps them.

The play time watcher (`play_watch.py`) polls the process list and hands each poll's running
games to `follow_games`, and a run's first poll to `resume_games`. A session starts at the first
poll that sees its game and ends at the last poll that saw it, once two polls in a row have
missed it, so one missed poll never splits it. A poll more than `SLEEP_POLLS` intervals after
the previous one by the wall clock, or before it, means the PC slept or the clock moved: every
open session ends at its last poll first, so a sleep is never counted. A session seen at one
poll only has no length and is dropped. At a run's first poll a game whose newest session was
last seen at most `RESUME_GAP` before goes on with that session, so a restart mid-game (which
ends the session at its last poll) counts its gap as play.

Periods are WIB days (`period_window`), and totals are the union of the sessions' time inside a
period: a session across midnight counts on each day, and two games open at once count once in
a total. Everything here counts the time a game was open, minimized or idle too, and only while
`waifu run` ran.

The file (`play_time.path`) follows the rules of `todo.py`: every call re-reads it, one lock
serialises the calls of every store in this process, a write replaces it atomically, reading
never creates it, and a file that is not a play time record is moved aside, never overwritten.
Only `waifu run` writes it (its run lock keeps it the only writer). Times are aware WIB, to the
second; sessions that ended more than `keep_days` ago are left out and dropped at the next write.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import threading
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast, get_args

from waifu.config import GameId
from waifu.finance.values import WIB
from waifu.gacha.files import write_json_atomic

logger = logging.getLogger(__name__)

PERIODS = ("today", "yesterday", "week", "month")
RESUME_GAP = timedelta(minutes=5)  # a restart: stopping, loading the models, the first poll
SLEEP_POLLS = 3  # a longer wall-clock gap between two polls is a break, not play
FILE_VERSION = 1
_GAME_IDS = frozenset(get_args(GameId))
_FILE_KEYS = {"version", "sessions", "open", "bedtime_told"}
_SESSION_KEYS = {"game", "start", "end"}
_OPEN_KEYS = {"game", "start", "last_seen"}
# date.fromisoformat also reads 20261017 and 2026-W42-6; the file holds only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# One lock for every store: two stores on the same file in this process must not lose
# each other's sessions.
_LOCK = threading.Lock()


def wib_now() -> datetime:
    return datetime.now(WIB)


@dataclass(frozen=True)
class Session:
    """A game open from `start` until `end`, the last poll that saw it."""

    game: GameId
    start: datetime
    end: datetime


@dataclass(frozen=True)
class PlayRecord:
    sessions: tuple[Session, ...] = ()  # finished, by start
    open: tuple[Session, ...] = ()  # still followed, or left by a run that crashed
    bedtime_told: date | None = None  # the night the bedtime nudge was said


@dataclass(frozen=True)
class PlayTotal:
    seconds: int
    sessions: int  # the sessions with time in the period


@dataclass(frozen=True)
class Tracked:
    """An open session as the watcher follows it."""

    session: Session
    missed: bool = False  # its game was gone at the last poll


@dataclass(frozen=True)
class Tracking:
    """The open sessions after a poll, and when that poll was."""

    open: tuple[Tracked, ...]
    last_poll: datetime

    @property
    def sessions(self) -> tuple[Session, ...]:
        return tuple(tracked.session for tracked in self.open)


def games_running(
    names: Collection[str], games: Mapping[GameId, Sequence[str]]
) -> frozenset[GameId]:
    """The games with one of their executable names among the process `names`: whole names
    ignoring case, never a part of one."""
    listed = {name.casefold() for name in names}
    return frozenset(
        game
        for game, executables in games.items()
        if any(executable.casefold() in listed for executable in executables)
    )


def follow_games(
    tracking: Tracking, seen: Collection[GameId], now: datetime, *, poll: timedelta
) -> tuple[Tracking, tuple[Session, ...]]:
    """The open sessions after a poll at `now` that saw the games `seen`, and the sessions
    that poll finished."""
    now = _stamp(now)
    if now < tracking.last_poll or now - tracking.last_poll > SLEEP_POLLS * poll:
        return _fresh(seen, now), _lasting(tracking.sessions)
    still: list[Tracked] = []
    finished: list[Session] = []
    for tracked in tracking.open:
        if tracked.session.game in seen:
            still.append(Tracked(dataclasses.replace(tracked.session, end=now)))
        elif tracked.missed:
            finished.append(tracked.session)
        else:
            still.append(dataclasses.replace(tracked, missed=True))
    followed = {tracked.session.game for tracked in still}
    new = [Tracked(Session(game, now, now)) for game in sorted(set(seen) - followed)]
    return Tracking((*still, *new), now), _lasting(finished)


def resume_games(
    record: PlayRecord, seen: Collection[GameId], now: datetime, *, gap: timedelta = RESUME_GAP
) -> tuple[Tracking, tuple[Session, ...]]:
    """A run's first poll at `now`: each running game whose newest stored session was last
    seen at most `gap` before goes on with it; every other open session in `record` ends."""
    now = _stamp(now)
    newest: dict[GameId, Session] = {}
    for session in (*record.sessions, *record.open):
        if session.game not in newest or session.end > newest[session.game].end:
            newest[session.game] = session
    following: list[Tracked] = []
    for game in sorted(seen):
        last = newest.get(game)
        if last is not None and timedelta(0) <= now - last.end <= gap:
            following.append(Tracked(Session(game, last.start, now)))
        else:
            following.append(Tracked(Session(game, now, now)))
    going_on = {(tracked.session.game, tracked.session.start) for tracked in following}
    ended = [session for session in record.open if (session.game, session.start) not in going_on]
    return Tracking(tuple(following), now), _lasting(ended)


def period_window(period: str, now: datetime) -> tuple[datetime, datetime]:
    """`today`, `yesterday`, `week` (from Monday), or `month` (from the 1st) up to `now`, by
    WIB midnights; yesterday ends at today's midnight."""
    now = now.astimezone(WIB)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return midnight, now
    if period == "yesterday":
        return midnight - timedelta(days=1), midnight
    if period == "week":
        return midnight - timedelta(days=now.weekday()), now
    if period == "month":
        return midnight.replace(day=1), now
    raise ValueError(f"unknown period '{period}': one of {', '.join(PERIODS)}")


def played_seconds(sessions: Iterable[Session], since: datetime, until: datetime) -> int:
    """The seconds inside `[since, until]` that any of `sessions` covers."""
    spans = sorted(
        (max(session.start, since), min(session.end, until))
        for session in sessions
        if _overlap(session, since, until) > timedelta(0)
    )
    total, reached = timedelta(0), since
    for start, end in spans:  # by start: count only what goes beyond the time counted so far
        start = max(start, reached)
        if end > start:
            total += end - start
            reached = end
    return int(total.total_seconds())


def play_totals(
    record: PlayRecord, games: Iterable[GameId], since: datetime, until: datetime
) -> dict[GameId, PlayTotal]:
    """Each of `games`, in order: its time in `[since, until]` and its sessions with time in
    it, open sessions up to their last poll."""
    totals: dict[GameId, PlayTotal] = {}
    for game in games:
        mine = [s for s in (*record.sessions, *record.open) if s.game == game]
        starts = {s.start for s in mine if _overlap(s, since, until) > timedelta(0)}
        totals[game] = PlayTotal(played_seconds(mine, since, until), len(starts))
    return totals


def whole_minutes(seconds: int) -> int:
    """`seconds` rounded to the nearest minute, a half minute up."""
    return (seconds + 30) // 60


def _overlap(session: Session, since: datetime, until: datetime) -> timedelta:
    return min(session.end, until) - max(session.start, since)


def _fresh(seen: Collection[GameId], now: datetime) -> Tracking:
    return Tracking(tuple(Tracked(Session(game, now, now)) for game in sorted(seen)), now)


def _lasting(sessions: Iterable[Session]) -> tuple[Session, ...]:
    """The sessions with a length: one seen at one poll only is dropped."""
    return tuple(session for session in sessions if session.end > session.start)


def _stamp(moment: datetime) -> datetime:
    return moment.replace(microsecond=0)


class PlayTimeStore:
    def __init__(
        self,
        path: Path | str,
        *,
        games: Sequence[GameId],
        keep_days: int,
        now: Callable[[], datetime] = wib_now,
    ) -> None:
        """`games` are the tracked games `totals` gives; `now` (aware) decides which sessions
        are kept. A naive clock is a ValueError at every call."""
        self.path = Path(path)
        self.games = tuple(games)
        self.keep_days = keep_days
        self._now = now

    def record(self) -> PlayRecord:
        """The sessions kept, the open ones, and the night the bedtime nudge was told; a
        missing file is an empty record and stays missing."""
        now = self._clock()
        with _LOCK:
            return self._read(now)

    def save(self, open_sessions: Sequence[Session], finished: Sequence[Session] = ()) -> None:
        """Write `open_sessions` as the open sessions and add `finished`; a stored session of
        the same game and start as one of them is replaced, so a resumed session is never
        counted twice."""
        now = self._clock()
        with _LOCK:
            record = self._read(now)
            given = {(s.game, s.start) for s in (*open_sessions, *finished)}
            kept = [s for s in record.sessions if (s.game, s.start) not in given]
            sessions = sorted((*kept, *finished), key=lambda s: (s.start, s.game))
            changed = PlayRecord(tuple(sessions), tuple(open_sessions), record.bedtime_told)
            self._write(changed, now)

    def mark_bedtime(self, night: date) -> None:
        """Keep `night` as the night the bedtime nudge was told."""
        now = self._clock()
        with _LOCK:
            self._write(dataclasses.replace(self._read(now), bedtime_told=night), now)

    def totals(self, since: datetime, until: datetime) -> dict[GameId, PlayTotal]:
        """`play_totals` of the tracked games."""
        return play_totals(self.record(), self.games, since, until)

    def _clock(self) -> datetime:
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("the play time store's clock must give aware times")
        return now

    def _read(self, now: datetime) -> PlayRecord:
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            record = _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return PlayRecord()
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return PlayRecord()
        return self._kept(record, now)

    def _kept(self, record: PlayRecord, now: datetime) -> PlayRecord:
        oldest = now - timedelta(days=self.keep_days)
        return dataclasses.replace(
            record,
            sessions=tuple(s for s in record.sessions if s.end >= oldest),
            open=tuple(s for s in record.open if s.end >= oldest),
        )

    def _set_aside(self, problem: ValueError, now: datetime) -> None:
        """Rename a file that is not a play time record, so that no write ever replaces it."""
        name = f"{self.path.name}.corrupt-{now:%Y%m%d-%H%M%S}"
        target = self.path.with_name(name)
        counter = 2
        while target.exists():
            target = self.path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(self.path, target)
        except OSError as exc:
            logger.error(
                "Play time file %s is not a play time record (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Play time file %s is not a play time record (%s); moved it to %s and started "
            "an empty one",
            self.path,
            problem,
            target,
        )

    def _write(self, record: PlayRecord, now: datetime) -> None:
        record = self._kept(record, now)
        write_json_atomic(
            self.path,
            {
                "version": FILE_VERSION,
                "sessions": [_dump(session, "end") for session in record.sessions],
                "open": [_dump(session, "last_seen") for session in record.open],
                "bedtime_told": None
                if record.bedtime_told is None
                else record.bedtime_told.isoformat(),
            },
        )


def _dump(session: Session, end_key: str) -> dict[str, str]:
    return {
        "game": session.game,
        "start": session.start.isoformat(timespec="seconds"),
        end_key: session.end.isoformat(timespec="seconds"),
    }


def _parse(raw: object) -> PlayRecord:
    """The record in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version = raw["version"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    sessions = _parse_sessions(raw["sessions"], "sessions", _SESSION_KEYS, "end")
    open_sessions = _parse_sessions(raw["open"], "open", _OPEN_KEYS, "last_seen")
    games = [session.game for session in open_sessions]
    if len(set(games)) != len(games):
        raise ValueError("a game has two open sessions")
    told = raw["bedtime_told"]
    return PlayRecord(
        tuple(sessions),
        tuple(open_sessions),
        None if told is None else _parse_date(told, "bedtime_told"),
    )


def _parse_sessions(entries: object, where: str, keys: set[str], end_key: str) -> list[Session]:
    if not isinstance(entries, list):
        raise ValueError(f"{where} must be a list")
    sessions = []
    for index, entry in enumerate(entries):
        at = f"{where}[{index}]"
        if not isinstance(entry, dict) or entry.keys() != keys:
            raise ValueError(f"{at} must be an object with exactly the keys {sorted(keys)}")
        game = entry["game"]
        if game not in _GAME_IDS:
            raise ValueError(f"{at}.game must be one of {sorted(_GAME_IDS)}")
        start = _parse_time(entry["start"], f"{at}.start")
        end = _parse_time(entry[end_key], f"{at}.{end_key}")
        if end < start:
            raise ValueError(f"{at}.{end_key} is before its start")
        sessions.append(Session(cast(GameId, game), start, end))
    return sessions


def _parse_date(value: object, where: str) -> date:
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass  # a day that does not exist, such as 2026-02-30
    raise ValueError(f"{where} must be a date written YYYY-MM-DD")


def _parse_time(value: object, where: str) -> datetime:
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if moment.tzinfo is not None:
                return moment
    raise ValueError(f"{where} must be an ISO 8601 time with its offset")
