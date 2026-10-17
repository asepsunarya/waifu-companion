"""The watcher inside `waifu run`: polls HoYoLAB, claims the check-in, and tells Master.

Every `poll_minutes` it reads the status of the configured games, works out the
reminders, claims the HoYoLAB check-in once per check-in day, and delivers what
there is to tell as one phone push, said in order. The reminders about a game's
state (stamina, dailies, weeklies, expeditions) may be said only until the next
poll: held longer (a focus block, or a game in game mode), play has likely changed
what they tell, so they are dropped unsaid. Problems and the check-in wait as long
as they must. It runs in a daemon thread; a failing poll is logged and the next one
comes as usual.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Protocol

from waifu.config import GachaConfig
from waifu.gacha.games import checkin_day, game_day
from waifu.gacha.hoyolab import CheckinResult, GachaError, GameStatus
from waifu.gacha.reminders import (
    Notice,
    ReminderState,
    checkin_notice,
    due_notices,
    problem_notice,
)
from waifu.notify import Notifier
from waifu.rain_watch import Announce

logger = logging.getLogger(__name__)

GAME_TAG = "video_game"  # ntfy shows it as a game controller emoji
EMOTION_STRENGTH = {"sad": 3, "surprised": 2, "happy": 1}
RETRY_KINDS = frozenset({"network", "rate_limit"})  # a check-in that may work on the next poll
# What a game's play changes: told as the poll saw it, out of date once the next poll looks again.
STATE_KINDS = frozenset({"stamina", "daily", "weekly", "expeditions"})
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
    ) -> None:
        self._hoyolab = hoyolab
        self._config = config
        self._notifier = notifier
        self._announce = announce
        self._title = title
        self._owner_call = owner_call
        self._now = now
        self._state_path = state_path or config.data_dir / "reminders.json"
        self._state = ReminderState.load(self._state_path)
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
        notices = due_notices(
            readable, now, self._state, self._config.reminders, offset, owner_call=self._owner_call
        )
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
        """One push; then the reminders about the games' state, said only until the next poll,
        and the rest, in that order (the order they were worked out in), each with the
        strongest emotion of them all, so said together they sound as one."""
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
        for told, expires_at in ((state, next_poll), (lasting, None)):
            if told:
                self._announce(" ".join(n.text for n in told), emotion, expires_at=expires_at)
