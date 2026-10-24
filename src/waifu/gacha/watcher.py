"""The watcher inside `waifu run`: polls HoYoLAB, claims the check-in, and tells Master.

Every `poll_minutes` it reads the status of the configured games, works out the
reminders, claims the HoYoLAB check-in once per check-in day, and delivers what
there is to tell as one phone push, said in order. The reminders about a game's
state (stamina, dailies, weeklies, expeditions) held by a game (game mode) past the
next poll are dropped unsaid: the game played has likely changed what they tell. A
focus block alone holds them as long as it lasts, like problems and the check-in,
which wait as long as they must. It runs in a daemon thread; a failing poll is
logged and the next one comes as usual.

With `reminders.planner` on and no guests around (v1.50) it also plans ahead: from the evening
of `reminders.daily_time` it reads each game's event calendar once a game day for the
planner's evening line, and once no tracked game is open (the play time watcher's word) it
hands each game Master closed late at night with that game's status to the overnight line,
whose game's stamina reminder then waits for the morning while no game is open. Planner lines
are reminders about a game's state too.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import TracebackType
from typing import Protocol

from waifu.config import GachaConfig
from waifu.gacha.games import checkin_day, game_day
from waifu.gacha.hoyolab import CheckinResult, GachaError, GameStatus
from waifu.gacha.planner import (
    Calendar,
    CalendarReader,
    evening_due,
    evening_notice,
    overnight_notice,
    stamina_held,
)
from waifu.gacha.reminders import (
    Notice,
    ReminderState,
    checkin_notice,
    due_notices,
    problem_notice,
)
from waifu.notify import Notifier
from waifu.play_time import Session
from waifu.play_watch import ClosedGames
from waifu.rain_watch import Announce

logger = logging.getLogger(__name__)

GAME_TAG = "video_game"  # ntfy shows it as a game controller emoji
EMOTION_STRENGTH = {"sad": 3, "surprised": 2, "happy": 1}
# A check-in or a calendar read that may work on the next poll.
RETRY_KINDS = frozenset({"network", "rate_limit"})
# What a game's play changes: told as the poll saw it, out of date once a game held it past the
# next poll.
STATE_KINDS = frozenset({"stamina", "daily", "weekly", "expeditions", "planner"})
STOP_TIMEOUT_S = 5.0


class GameClient(Protocol):
    def status(self, games: Iterable[str]) -> dict[str, GameStatus | GachaError]: ...

    def checkin(self, game_id: str) -> CheckinResult: ...


def _local_now() -> datetime:
    return datetime.now().astimezone()


class GachaWatcher:
    def __init__(
        self,
        hoyolab: GameClient,
        config: GachaConfig,
        *,
        notifier: Notifier | None,
        announce: Announce | None,
        title: str = "Cyrene",
        owner_call: str = "Master",
        now: Callable[[], datetime] = _local_now,
        state_path: Path | None = None,
        read_calendar: CalendarReader | None = None,
        guest_mode: Callable[[], bool] | None = None,
        closed_games: ClosedGames | None = None,
        morning: time = time(7, 0),
    ) -> None:
        """Without `read_calendar` there is no evening planner line, and without `closed_games`
        (the games Master closed, and whether one is still open) no overnight one; `guest_mode`
        says whether guests are around, `morning` (the briefing's time) when Master wakes."""
        self._hoyolab = hoyolab
        self._config = config
        self._notifier = notifier
        self._announce = announce
        self._title = title
        self._owner_call = owner_call
        self._now = now
        self._state_path = state_path or config.data_dir / "reminders.json"
        self._state = ReminderState.load(self._state_path)
        self._read_calendar = read_calendar
        self._guest_mode = guest_mode
        self._closed_games = closed_games
        self._morning = morning
        self._closed: dict[str, Session] = {}  # per game, the last closed, waiting for a status
        self._calendar_read: dict[str, date] = {}  # per game, the game day its calendar was read
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> list[Notice]:
        """One round: reminders, problems, and the check-in; returns what was told."""
        now = self._now()
        offset = self._config.server_utc_offset
        statuses = self._hoyolab.status(self._config.games)
        readable = {
            game: status for game, status in statuses.items() if isinstance(status, GameStatus)
        }
        self._take_closed_games()
        planning = self._planning()
        # While a tracked game is open Master is at the PC: a game he closed waits for him to
        # leave it, and no stamina reminder waits for the morning.
        away = self._away()
        notices = self._overnight(readable, now) if planning and away else []
        held = [
            game
            for game in readable
            if away and stamina_held(game, now, self._state, self._morning)
        ]
        notices += due_notices(
            readable,
            now,
            self._state,
            self._config.reminders,
            offset,
            owner_call=self._owner_call,
            stamina_held=held,
        )
        if planning:
            notices += self._evening(readable, now)
        for game, result in statuses.items():
            if isinstance(result, GachaError):
                notice = problem_notice(
                    game, result, now, self._state, offset, owner_call=self._owner_call
                )
                notices.extend([notice] if notice else [])
        if self._config.checkin:
            notices.extend(self._checkin(now))
        self._deliver(notices, now)
        self._state.save(self._state_path, today=game_day(now, offset))
        return notices

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="gacha-watcher", daemon=True)
        self._thread.start()
        logger.info(
            "Watching %s every %g minutes", ", ".join(self._config.games), self._config.poll_minutes
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def __enter__(self) -> GachaWatcher:
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
                self.poll_once()
            except Exception:
                logger.exception("A HoYoLAB poll failed; trying again at the next one")
            self._stop.wait(self._config.poll_minutes * 60)

    def _planning(self) -> bool:
        """Whether the planner speaks now: on, and no guests around."""
        guests = self._guest_mode is not None and self._guest_mode()
        return self._config.reminders.planner and not guests

    def _away(self) -> bool:
        """Whether no tracked game was open at the play time watcher's last poll; without its
        word, as if none were."""
        return self._closed_games is None or not self._closed_games.playing()

    def _take_closed_games(self) -> None:
        """Keep, per game, the last session Master closed: a game opened and closed again
        replaces the close before."""
        if self._closed_games is None:
            return
        for session in self._closed_games.take():
            self._closed[session.game] = session

    def _overnight(self, readable: dict[str, GameStatus], now: datetime) -> list[Notice]:
        """Each closed game whose status this poll read, handed to the overnight line."""
        notices = []
        for game in [game for game in self._closed if game in readable]:
            session = self._closed.pop(game)
            notice = overnight_notice(
                game,
                session.end,
                readable[game],
                now,
                self._state,
                self._morning,
                stamina_minutes=self._config.reminders.stamina_minutes,
                owner_call=self._owner_call,
            )
            notices.extend([notice] if notice else [])
        return notices

    def _evening(self, readable: dict[str, GameStatus], now: datetime) -> list[Notice]:
        """The evening line of each readable game that is due one and whose calendar was not
        read yet this game day."""
        offset = self._config.server_utc_offset
        day = game_day(now, offset)
        notices = []
        for game in readable:
            if self._read_calendar is None or self._calendar_read.get(game) == day:
                continue
            if not evening_due(game, now, self._state, self._config.reminders, offset):
                continue
            calendar = self._calendar(self._read_calendar, game, day)
            if calendar is None:
                continue
            notice = evening_notice(
                game, calendar, now, self._state, offset, owner_call=self._owner_call
            )
            notices.extend([notice] if notice else [])
        return notices

    def _calendar(self, read: CalendarReader, game: str, day: date) -> Calendar | None:
        """`game`'s calendar, read once for `day`; None after an error, which is read again at
        the next poll only when it may work then."""
        try:
            calendar = read(game)
        except GachaError as error:
            if error.kind in RETRY_KINDS:
                logger.info(
                    "Could not read the %s event calendar (%s); trying again at the next poll",
                    game,
                    error.kind,
                )
                return None
            logger.warning(
                "Could not read the %s event calendar (%s); no planner line for it this game day",
                game,
                error.kind,
            )
            self._calendar_read[game] = day
            return None
        self._calendar_read[game] = day
        return calendar

    def _checkin(self, now: datetime) -> list[Notice]:
        """Claim every game's check-in once per check-in day; retry later after a network error."""
        offset = self._config.server_utc_offset
        day = checkin_day(now, offset)
        key = f"checkin:{day}"
        if key in self._state.fired:
            return []
        results: list[CheckinResult] = []
        problems: list[Notice] = []
        retry = False
        for game in self._config.games:
            try:
                results.append(self._hoyolab.checkin(game))
            except GachaError as error:
                if error.kind in RETRY_KINDS:
                    logger.info("Check-in of %s failed (%s); trying again later", game, error.kind)
                    retry = True
                    continue
                notice = problem_notice(
                    game, error, now, self._state, offset, owner_call=self._owner_call
                )
                problems.extend([notice] if notice else [])
        if not retry:
            self._state.once(key, day)
        claimed = checkin_notice(results)
        return [*([claimed] if claimed else []), *problems]

    def _deliver(self, notices: list[Notice], now: datetime) -> None:
        """One push; then the reminders about the games' state, which a game may hold only until
        the next poll, and the rest, in that order (the order they were worked out in), each
        with the strongest emotion of them all, so said together they sound as one."""
        if not notices:
            return
        emotion = max(
            (notice.emotion for notice in notices), key=lambda e: EMOTION_STRENGTH.get(e, 0)
        )
        if self._notifier is not None:
            self._notifier.send(self._title, "\n".join(n.text for n in notices), tags=[GAME_TAG])
        if self._announce is None:
            return
        next_poll = now + timedelta(minutes=self._config.poll_minutes)
        state = [notice for notice in notices if notice.kind in STATE_KINDS]
        lasting = [notice for notice in notices if notice.kind not in STATE_KINDS]
        for told, game_expires_at in ((state, next_poll), (lasting, None)):
            if told:
                text = " ".join(n.text for n in told)
                self._announce(text, emotion, game_expires_at=game_expires_at)
