"""What each global hotkey (`waifu.hotkeys`) does: talk now, cut her off, repeat, stop listening.

`press` is the hotkey thread's `on_press` and never blocks, so Ctrl+Alt+X reaches her while she
speaks. Talk (Ctrl+Alt+C) is a double-click on her avatar, pressed into the interaction handler
whatever its switches say: she is cut off if she speaks, and she listens with her `listen` line.
Stop (Ctrl+Alt+X) cuts her off right there (`interrupt` never waits for her voice) and, when it
did, she listens next like after a double-click that cut her; while the listen pause holds, a cut
is only a cut, so cutting a reminder never quietly opens her mic again. Repeat (Ctrl+Alt+R) and
quiet (Ctrl+Alt+Q) wait for her voice, so a worker of their own runs them: repeat says her last
words again, quiet starts or ends the listen pause (`waifu.listen_pause`) and says so.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Protocol

from waifu.avatar.interactions import LISTEN_AFTER_CUT
from waifu.listen_pause import ListenPause

logger = logging.getLogger(__name__)

QUEUE_SIZE = 8
STOP_TIMEOUT_S = 5.0
WORKER_NAME = "hotkey-actions"
# What she says as Ctrl+Alt+Q switches the pause; `{owner}` is what she calls the owner.
QUIET_ON = (
    "Oke {owner}, aku tidak mendengarkan dulu sampai jam {time}. Tekan tombol yang sama kalau "
    "mau ngobrol lagi."
)
QUIET_OFF = "Oke {owner}, aku dengar lagi."
QUIET_FAILED = "Maaf {owner}, aku belum bisa {done}. Coba lagi sebentar."


class Presses(Protocol):
    """The avatar's interaction handler, as the hotkeys press it."""

    def press(self, kind: str) -> None: ...


class HotkeyActions:
    def __init__(
        self,
        *,
        reactions: Presses,
        interrupt: Callable[[], bool],
        repeat: Callable[[], None],
        speak: Callable[..., None],
        pause: ListenPause | None = None,
        owner_call: str = "Master",
    ) -> None:
        """`reactions` is the avatar's interaction handler; `interrupt`, `repeat`, and `speak`
        are the Assistant's `interrupt`, `repeat_now`, and `speak`; `pause` is the listen pause
        quiet switches (None: quiet is not registered)."""
        self._reactions = reactions
        self._interrupt = interrupt
        self._repeat = repeat
        self._speak = speak
        self._pause = pause
        self._owner_call = owner_call
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=QUEUE_SIZE)
        self._lock = threading.Lock()  # guards _accepting and _worker
        self._accepting = False
        self._worker: threading.Thread | None = None

    def press(self, action: str) -> None:
        """Do what the hotkey of `action` does, or queue it for the worker; never blocks. A
        no-op unless started."""
        with self._lock:
            if not self._accepting:
                return
            if action == "talk":
                self._reactions.press("double_click")
            elif action == "stop":
                self._cut()
            elif action == "quiet" and self._pause is None:
                logger.warning("The quiet hotkey has no listen pause to switch; ignoring it")
            elif action in ("repeat", "quiet"):
                self._queue_for_worker(action)
            else:
                logger.warning("Ignoring an unknown hotkey action %r", action)

    def start(self) -> None:
        """Start the worker; starting a running one does nothing."""
        with self._lock:
            if self._worker is not None:
                return
            self._accepting = True
            self._worker = threading.Thread(target=self._work, name=WORKER_NAME, daemon=True)
            self._worker.start()

    def stop(self) -> None:
        """Take no more presses, drop the queued ones, and wait for the worker (bounded)."""
        with self._lock:
            self._accepting = False
            worker, self._worker = self._worker, None
            while not self._queue.empty():
                self._queue.get_nowait()
            if worker is not None:
                self._queue.put_nowait(None)  # wakes the worker; the queue was just emptied
        if worker is None:
            return
        worker.join(STOP_TIMEOUT_S)
        if worker.is_alive():
            logger.warning("The hotkeys' worker is still busy; leaving it behind")

    def __enter__(self) -> HotkeyActions:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def _cut(self) -> None:
        if not self._interrupt():
            logger.debug("Nothing to cut off")
            return
        if self._pause is not None and self._pause.paused():
            logger.info("Cut her off; the listen pause holds, so she does not listen")
            return
        logger.info("Cut her off; she listens next")
        self._reactions.press(LISTEN_AFTER_CUT)

    def _queue_for_worker(self, action: str) -> None:
        try:
            self._queue.put_nowait(action)
        except queue.Full:
            logger.warning("The hotkeys' queue is full; dropping %s", action)

    def _work(self) -> None:
        while (action := self._queue.get()) is not None:
            try:
                if action == "repeat":
                    self._repeat()
                else:
                    self._switch_pause()
            except Exception:  # the worker must outlive a broken voice
                logger.exception("The hotkey action %s failed", action)

    def _switch_pause(self) -> None:
        """Start the listen pause, saying until when, or end it, saying so as a reply (the
        follow-up window opens); a pause file that cannot be changed is said and logged."""
        pause = self._pause
        assert pause is not None  # `press` queues quiet only with a pause
        owner = self._owner_call
        paused = pause.paused()
        try:
            if paused:
                pause.end()
            else:
                until = pause.start()
        except OSError as exc:
            logger.warning("Could not %s the listen pause: %s", "end" if paused else "start", exc)
            done = "mendengarkan lagi" if paused else "berhenti mendengarkan"
            self._speak(QUIET_FAILED.format(owner=owner, done=done), followup=False)
            return
        if paused:
            self._speak(QUIET_OFF.format(owner=owner))
        else:
            self._speak(QUIET_ON.format(owner=owner, time=f"{until:%H.%M}"), followup=False)
