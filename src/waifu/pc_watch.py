"""The watcher inside `waifu run` that speaks up when the PC needs care.

Every `poll_seconds` it reads the PC. A fixed drive with less than `disk_warn_gb` free is
told once per drive per local date; a GPU at or above `gpu_warn_c` in every poll for at
least `gpu_warn_minutes` (timed on the monotonic clock; a poll below the line or without a
reading starts the streak over) is told at most once per hour. Each notice is said between
utterances and pushed to the phone when notifications are on. What was told lives only in
this process, so a restart may repeat a notice once. It runs in a daemon thread; a failing
read, announcement, or push is logged and the next poll comes as usual.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from datetime import date, datetime

from waifu.config import PcHealthConfig, at_sentence_start
from waifu.notify import Notifier
from waifu.pc_health import GpuStatus, PcSnapshot

logger = logging.getLogger(__name__)

# A worried face: "sad" is the closest of avatar.emotion.EMOTIONS, as for the gacha watcher's
# problems.
NOTICE_EMOTION = "sad"
PC_TAG = "computer"  # ntfy shows it as a computer emoji
GPU_NOTICE_GAP_S = 3600.0  # at most one hot-GPU notice per hour
STOP_TIMEOUT_S = 5.0


class PcWatcher:
    def __init__(
        self,
        read: Callable[[], PcSnapshot],
        config: PcHealthConfig,
        *,
        announce: Callable[[str, str], None] | None,
        notifier: Notifier | None,
        owner_call: str,
        title: str = "Cyrene",
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
        sleep_event: Callable[[], threading.Event] = threading.Event,
    ) -> None:
        self._read = read
        self._config = config
        self._announce = announce
        self._notifier = notifier
        self._owner = at_sentence_start(owner_call)  # every notice opens with it
        self._title = title
        self._clock = clock
        self._now = now
        self._stop = sleep_event()  # the thread sleeps on it between polls; stop() sets it
        self._thread: threading.Thread | None = None
        self._disk_told: dict[str, date] = {}  # drive letter -> the local date it was told
        self._hot_since: float | None = None  # clock time of the hot streak's first poll
        self._gpu_told_at: float | None = None  # clock time of the last hot-GPU notice

    def poll_once(self) -> list[str]:
        """Read the PC once and give the notices that are due; returns their texts."""
        try:
            snapshot = self._read()
        except Exception:
            logger.exception("Could not read the PC; trying again at the next poll")
            self._hot_since = None  # a poll without a reading starts the streak over
            return []
        notices = [*self._disk_notices(snapshot), *self._gpu_notices(snapshot.gpu)]
        for text in notices:
            self._deliver(text)
        return notices

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pc-watcher", daemon=True)
        self._thread.start()
        logger.info("Watching the PC every %d s", self._config.poll_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._config.poll_seconds)

    def _disk_notices(self, snapshot: PcSnapshot) -> list[str]:
        """A notice for each drive below the line not yet told today (local date)."""
        today = self._now().date()
        notices: list[str] = []
        for drive in snapshot.drives:
            if (
                drive.free_gb >= self._config.disk_warn_gb
                or self._disk_told.get(drive.letter) == today
            ):
                continue
            self._disk_told[drive.letter] = today
            logger.info("Drive %s has %.1f GB free; telling Master", drive.letter, drive.free_gb)
            notices.append(
                f"{self._owner}, disk {drive.letter} tinggal {_gigabytes_down(drive.free_gb)} GB. "
                "Mungkin perlu dibersihkan sedikit."
            )
        return notices

    def _gpu_notices(self, gpu: GpuStatus | None) -> list[str]:
        """The hot-GPU notice when the streak is long enough and none was given this hour."""
        now = self._clock()
        temp_c = gpu.temp_c if gpu is not None else None
        if temp_c is None or temp_c < self._config.gpu_warn_c:
            self._hot_since = None
            return []
        if self._hot_since is None:
            self._hot_since = now
        hot_long_enough = now - self._hot_since >= self._config.gpu_warn_minutes * 60
        told_this_hour = (
            self._gpu_told_at is not None and now - self._gpu_told_at < GPU_NOTICE_GAP_S
        )
        if not hot_long_enough or told_this_hour:
            return []
        self._gpu_told_at = now
        logger.info(
            "The GPU has stayed at or above %d C (%d C now); telling Master",
            self._config.gpu_warn_c,
            temp_c,
        )
        return [
            f"{self._owner}, GPU-nya sudah {temp_c}°C beberapa menit. "
            "Istirahat sebentar atau cek pendinginnya, ya."
        ]

    def _deliver(self, text: str) -> None:
        """Say the notice and push it; a failure of either is logged, never raised."""
        if self._announce is not None:
            try:
                self._announce(text, NOTICE_EMOTION)
            except Exception:
                logger.exception("Could not say a PC notice")
        if self._notifier is not None:
            try:
                self._notifier.send(self._title, text, tags=[PC_TAG])
            except Exception:
                logger.exception("Could not push a PC notice")


def _gigabytes_down(gigabytes: float) -> str:
    """`8,4`: rounded down to one decimal, so a drive just under the line never reads as the line
    itself (9.96 is 9,9, not 10,0), written with the Indonesian decimal comma."""
    return f"{math.floor(gigabytes * 10) / 10:.1f}".replace(".", ",")
