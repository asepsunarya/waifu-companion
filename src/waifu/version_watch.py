"""The watcher inside `waifu run` that tells Master once newer code is on disk.

The owner keeps one `waifu run` open for days, so versions land in the project folder that the
running one never loaded. Every `check_minutes`, timed from the start of the previous poll and
first right after it starts, it reads the CHANGELOG and pyproject on disk (never git), but only
between `active_from` and `active_until` on the PC's clock: at night it reads nothing and stays
quiet, so a version found then is told in the morning. News is a CHANGELOG version above the one
she started with; when the core dependencies changed since, `pip install` comes first, and the
push carries the command. A commit that adds no version is never news, also when it changes the
dependencies, so fixes and half-built versions stay quiet. Each news is said between utterances,
once the follow-up window has closed, and pushed to the phone when notifications are on, once
per version number: a later version is told again with its own count. A failed read is never
said: the first of a run of failures is logged at WARNING. She never restarts herself or runs
pip. Nothing is raised.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from waifu.config import VersionNoticeConfig, at_sentence_start
from waifu.finance.values import spoken_rupiah as spoken_number
from waifu.notify import Notifier
from waifu.version import (
    INSTALL_COMMAND,
    CodeVersion,
    Release,
    dependencies_changed,
    newer_releases,
)

logger = logging.getLogger(__name__)

NOTICE_EMOTION = "happy"  # good news
VERSION_TAG = "new"  # ntfy shows it as the NEW emoji
STOP_TIMEOUT_S = 5.0

# Waits up to the given seconds for the event; True when it was set.
Wait = Callable[[threading.Event, float], bool]


@dataclass(frozen=True)
class VersionNews:
    """What changed on disk since a run started: its newer releases, newest first (at least
    one), and whether the core dependencies changed too."""

    releases: tuple[Release, ...]
    dependencies_changed: bool


def version_news(running: CodeVersion, disk: CodeVersion) -> VersionNews | None:
    """The news of the code on `disk` for a run that started with `running`; None without a
    newer release, also when only the dependencies changed: a version still being built."""
    releases = newer_releases(running, disk)
    if not releases:
        return None
    return VersionNews(releases, dependencies_changed(running, disk))


def notice_text(news: VersionNews, *, owner: str, name: str, pushed: bool) -> str:
    """What she says, `owner` opening it: the newest version and its title, the others counted,
    then a restart, or with changed packages pip first and where its command is (the phone when
    `pushed`). No parentheses: her voice drops them."""
    if not news.dependencies_changed:
        return f"{_opening(news, owner, name)} Mulai ulang aku kalau sempat ya."
    where = "di HP" if pushed else "di waifu version"
    return (
        f"{_opening(news, owner, name)} Tutup aku dulu, jalankan pip install, lalu mulai ulang "
        f"aku ya; perintahnya ada {where}."
    )


def push_text(news: VersionNews, *, owner: str, name: str) -> str:
    """The phone's text: what she says, but with changed packages the exact command to run."""
    if not news.dependencies_changed:
        return notice_text(news, owner=owner, name=name, pushed=True)
    return (
        f"{_opening(news, owner, name)} Tutup {name} dulu, lalu di folder proyek jalankan:\n"
        f"{INSTALL_COMMAND}\nSesudah itu jalankan lagi waifu run."
    )


def _opening(news: VersionNews, owner: str, name: str) -> str:
    """`Sayang, ada versi baru Cyrene: v1.27, kamera HP, dan dua versi lain.`, and whether the
    packages changed too."""
    newest, others = news.releases[0], len(news.releases) - 1
    more = f", dan {spoken_number(others)} versi lain" if others else ""
    packages = " Paket Python-nya juga berubah." if news.dependencies_changed else ""
    return f"{owner}, ada versi baru {name}: {newest.version}, {newest.title}{more}.{packages}"


class VersionWatcher:
    def __init__(
        self,
        read: Callable[[], CodeVersion],
        running: CodeVersion,
        config: VersionNoticeConfig,
        *,
        announce: Callable[[str, str], None] | None,
        notifier: Notifier | None,
        owner_call: str,
        name: str = "Cyrene",
        now: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
        wait: Wait = threading.Event.wait,
    ) -> None:
        """`read` reads the code on disk, `running` is what this run started with; `name` is
        hers, said in the notice and the push's title; `now` is the PC's local time, for the
        active hours, and `clock` monotonic seconds, for the polls."""
        self._read = read
        self._running = running
        self._config = config
        self._announce = announce
        self._notifier = notifier
        self._owner = at_sentence_start(owner_call)  # every notice opens with it
        self._name = name
        self._now = now
        self._clock = clock
        self._wait = wait
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # The newest release of each news told, by its version alone: a retitled heading, or a
        # read that misses a part, is the same version.
        self._told: set[str] = set()
        self._failing = False  # the last read failed or had problems

    def poll_once(self) -> list[str]:
        """Inside the active hours, read the code on disk and give the notice that is due;
        returns its text."""
        if not self._config.active_from <= self._now().time() < self._config.active_until:
            return []
        try:
            disk = self._read()
        except Exception as exc:
            self._log_failure(str(exc), traceback=True)
            return []
        if disk.problems:
            self._log_failure("; ".join(disk.problems), traceback=False)
        elif self._failing:
            self._failing = False
            logger.info("The code on disk can be read again")
        try:
            news = version_news(self._running, disk)
        except Exception:
            # An odd heading must not end the thread: the next poll reads the files again.
            logger.exception("Could not decide on a version notice")
            return []
        if news is None or news.releases[0].version in self._told:
            return []
        self._told.add(news.releases[0].version)
        logger.info(
            "Newer code on disk (%s, dependencies %s); telling Master",
            ", ".join(release.version for release in news.releases),
            "changed" if news.dependencies_changed else "unchanged",
        )
        text = notice_text(
            news, owner=self._owner, name=self._name, pushed=self._notifier is not None
        )
        self._deliver(text, push_text(news, owner=self._owner, name=self._name))
        return [text]

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="version-watcher", daemon=True)
        self._thread.start()
        logger.info(
            "Watching for a newer version every %d minutes between %s and %s",
            self._config.check_minutes,
            f"{self._config.active_from:%H:%M}",
            f"{self._config.active_until:%H:%M}",
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def _loop(self) -> None:
        interval_s = self._config.check_minutes * 60
        while not self._stop.is_set():
            started = self._clock()
            self.poll_once()
            self._wait(self._stop, max(0.0, started + interval_s - self._clock()))

    def _log_failure(self, reason: str, *, traceback: bool) -> None:
        """The first failure of a run at WARNING (a raised one with its traceback), the rest at
        DEBUG: a CHANGELOG broken for a day is one line, not one per poll."""
        if self._failing:
            logger.debug("The code on disk still cannot be read: %s", reason, exc_info=traceback)
            return
        self._failing = True
        logger.warning(
            "Could not read the code on disk (%s); trying again at every check",
            reason,
            exc_info=traceback,
        )

    def _deliver(self, text: str, push: str) -> None:
        """Say the notice and push it; a failure of either is logged, never raised."""
        if self._announce is not None:
            try:
                self._announce(text, NOTICE_EMOTION)
            except Exception:
                logger.exception("Could not say a version notice")
        if self._notifier is not None:
            try:
                self._notifier.send(self._name, push, tags=[VERSION_TAG])
            except Exception:
                logger.exception("Could not push a version notice")
