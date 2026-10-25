"""The watcher inside `waifu run` that notices when the internet drops and comes back.

Every `poll_seconds`, timed from the start of the previous poll and first right after it starts,
it checks the gateway and the anchors (`NetworkCheck`, without the DNS lookup), and the HoYo hosts
only while a HoYo game runs (the play time watcher's word, through the state) and at most every
`hoyo_poll_seconds`. Each check is classified and noted in the run's `NetworkState`.

An outage is two counted failed polls in a row (the internet out of reach: `router` or
`offline`); it starts at the first, is written to the history, and the state knows of it from the
second. The first poll that answers ends it there, and when it lasted at least
`back_after_minutes` (whole minutes, a half up) she says once, through the patient announce queue
(held by a focus block or a game hold like her other announcements, dropped when still unsaid
BACK_EXPIRES later): "Master, internetnya sudah nyambung lagi, tadi putus sekitar 12 menit, sejak
jam 21.03." At most `max_back_per_day` a WIB day, counted in the file before she says it, so a
restart never repeats one.

A break never makes an outage: the run's first poll, a poll before the previous one by the wall
clock or more than `SLEEP_POLLS` polls after it by either clock (Windows' monotonic clock runs on
through sleep), or one whose two gaps differ by more than a poll (a clock step). A break's poll
and the next never count as failed (the Wi-Fi reconnects after a resume, autostart runs before the
network is up), and an outage open across a break ends at its last failed poll without a line:
when it came back is unknown. So does one a crashed run left open, at the run's first poll, and
one still open when the watcher stops.

A check or write that fails is never said: the first of a run of failures is logged at WARNING
(an unexpected one with its traceback), the rest at DEBUG, and INFO once a poll works again. A
status that changes (slow, DNS) and HoYo trouble starting and ending are logged at INFO, never an
address. Nothing is raised.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from waifu.config import NetworkConfig
from waifu.network import DOWN, Finding, NetworkCheck, NetworkState, Probe, Status, back_line
from waifu.network_history import NetworkStore, OpenOutage, OutageKind
from waifu.play_time import SLEEP_POLLS, whole_minutes, wib_now
from waifu.rain_watch import Announce, Wait

logger = logging.getLogger(__name__)

NOTICE_EMOTION = "happy"  # good news
BACK_EXPIRES = timedelta(minutes=30)
GRACE_POLLS = 2  # a break's poll and the next never count as failed
OUTAGE_POLLS = 2  # counted failed polls in a row that make an outage
STOP_TIMEOUT_S = 5.0


class NetworkWatcher:
    def __init__(
        self,
        probe: Probe,
        store: NetworkStore,
        config: NetworkConfig,
        state: NetworkState,
        *,
        announce: Announce | None,
        owner_call: str,
        now: Callable[[], datetime] = wib_now,
        clock: Callable[[], float] = time.monotonic,
        wait: Wait = threading.Event.wait,
    ) -> None:
        """`now` is aware WIB time, for the outages; `clock` monotonic seconds, for the polls
        and, with `now`, for the breaks."""
        self._check = NetworkCheck(probe, state, slow_ms=config.slow_ms)
        self._store = store
        self._config = config
        self._state = state
        self._announce = announce
        self._owner_call = owner_call
        self._now = now
        self._clock = clock
        self._wait = wait
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Polls and the stop's last write never overlap.
        self._lock = threading.Lock()
        self._stopped = False
        self._last: tuple[datetime, float] | None = None  # the previous poll, by both clocks
        self._grace = 0  # the polls left that never count as failed
        self._failed: list[tuple[datetime, OutageKind]] = []  # counted, in a row, no outage
        self._outage: OpenOutage | None = None
        self._last_hoyo: float | None = None  # on `clock`
        self._hoyo_trouble = False
        self._status: Status = "ok"  # the last internet status logged
        self._failing = False  # the last poll failed

    def poll_once(self) -> list[str]:
        """Check, follow the outages, and say that the internet is back when it is due;
        returns what she said."""
        try:
            with self._lock:
                if self._stopped:
                    return []
                said = self._poll()
        except Exception as exc:
            self._log_failure(exc)
            return []
        if self._failing:
            self._failing = False
            logger.info("The internet can be checked again")
        return said

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="network-watcher", daemon=True)
        self._thread.start()
        logger.info("Watching the internet every %d s", self._config.poll_seconds)

    def stop(self) -> None:
        """Stop polling, then end an open outage at its last failed poll."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            try:
                self._end_unseen()
            except Exception:
                logger.exception("Could not end the open internet outage on stopping")

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            self.poll_once()
            self._wait(self._stop, max(0.0, started + self._config.poll_seconds - self._clock()))

    def _poll(self) -> list[str]:
        now, mono = self._now(), self._clock()
        first = self._last is None
        broken = self._broken(now, mono)
        self._last = (now, mono)
        if broken:
            self._failed.clear()
            self._grace = GRACE_POLLS
            self._end_unseen()
            if first:
                self._end_crashed()
        hoyo = self._hoyo_due(mono)
        finding = self._check.now(dns=False, hoyo=hoyo)
        if hoyo:
            self._last_hoyo = mono
            self._log_hoyo(finding)
        self._log_status(finding.status)
        counted = self._grace == 0
        self._grace = max(0, self._grace - 1)
        if finding.status in DOWN:
            if counted:
                self._failed_poll(now, "router" if finding.status == "router" else "offline")
            return []
        self._failed.clear()
        return self._answered_poll(now)

    def _broken(self, now: datetime, mono: float) -> bool:
        """Whether this poll comes after a break: the run's first, a gap of more than
        `SLEEP_POLLS` polls on either clock, the wall clock gone back, or the two gaps apart by
        more than a poll (a clock step)."""
        if self._last is None:
            return True
        poll = self._config.poll_seconds
        wall_gap = (now - self._last[0]).total_seconds()
        mono_gap = mono - self._last[1]
        limit = SLEEP_POLLS * poll
        return (
            wall_gap < 0 or wall_gap > limit or mono_gap > limit or abs(wall_gap - mono_gap) > poll
        )

    def _hoyo_due(self, mono: float) -> bool:
        if not self._state.hoyo_playing():
            return False
        last = self._last_hoyo
        return last is None or mono - last >= self._config.hoyo_poll_seconds

    def _failed_poll(self, now: datetime, kind: OutageKind) -> None:
        if self._outage is not None:
            self._outage = OpenOutage(self._outage.start, now, self._outage.kind)
            self._store.still_down(now)
            return
        self._failed.append((now, kind))
        if len(self._failed) < OUTAGE_POLLS:
            return
        start, first_kind = self._failed[0]
        self._failed.clear()
        self._outage = OpenOutage(start, now, first_kind)
        self._state.open_outage(start, first_kind)
        logger.warning("The internet is down since %s (%s)", f"{start:%H:%M}", first_kind)
        self._store.begin(start, now, first_kind)

    def _answered_poll(self, now: datetime) -> list[str]:
        """End an open outage at this poll, and say that the internet is back when it is due."""
        outage = self._outage
        if outage is None:
            return []
        self._outage = None
        self._state.close_outage()
        minutes = whole_minutes(int((now - outage.start).total_seconds()))
        logger.info("The internet is back after about %d minutes", minutes)
        self._store.end(now)
        return self._tell_back(outage.start, now, minutes)

    def _tell_back(self, start: datetime, back: datetime, minutes: int) -> list[str]:
        config = self._config
        if self._announce is None or not config.announce_back:
            return []
        if minutes < config.back_after_minutes:
            return []
        day = back.date()
        if self._store.record().back_count(day) >= config.max_back_per_day:
            logger.info(
                "Not saying the internet is back: said %d times today", config.max_back_per_day
            )
            return []
        self._store.told_back(day)  # first, so a restart never says it again
        text = back_line(self._owner_call, start, minutes)
        expires = back + BACK_EXPIRES
        try:
            self._announce(text, NOTICE_EMOTION, expires_at=expires, game_expires_at=expires)
        except Exception:
            logger.exception("Could not say that the internet is back")
        return [text]

    def _end_unseen(self) -> None:
        """End an outage whose end this watcher cannot see (a break, stopping) at its last failed
        poll, without a line."""
        outage = self._outage
        if outage is None:
            return
        self._outage = None
        self._state.close_outage()
        logger.info("An internet outage ended unseen; keeping it until its last failed poll")
        self._store.end(outage.last_failed)

    def _end_crashed(self) -> None:
        """End an outage a crashed run left open at its last failed poll."""
        left = self._store.record().open
        if left is not None:
            logger.info("Ending the internet outage an earlier run left open")
            self._store.end(left.last_failed)

    def _log_status(self, status: Status) -> None:
        """A change of the internet's status, at INFO: HoYo trouble is logged on its own, and an
        outage is logged once it is one."""
        internet = "ok" if status == "hoyo" else status
        if internet == self._status:
            return
        self._status = internet
        if internet not in DOWN:
            logger.info("The internet check now finds it %s", internet)

    def _log_hoyo(self, finding: Finding) -> None:
        hoyo = finding.hoyo
        trouble = hoyo is not None and hoyo.trouble
        if trouble == self._hoyo_trouble or hoyo is None:
            return
        self._hoyo_trouble = trouble
        if trouble:
            logger.info(
                "The HoYoLAB/HoYoverse servers are in trouble: %d of %d answered",
                hoyo.answered,
                hoyo.of,
            )
        else:
            logger.info("The HoYoLAB/HoYoverse servers answer again")

    def _log_failure(self, exc: Exception) -> None:
        """The first failure of a run at WARNING (an unexpected one with its traceback), the
        rest at DEBUG: a broken disk is one line, not one per minute."""
        traceback = not isinstance(exc, OSError)
        if self._failing:
            logger.debug("The internet still cannot be checked: %s", exc, exc_info=traceback)
            return
        self._failing = True
        logger.warning(
            "Could not check the internet (%s); trying again at every poll",
            exc,
            exc_info=traceback,
        )
