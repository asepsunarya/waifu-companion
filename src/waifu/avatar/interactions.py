"""How Cyrene answers the mouse on her avatar: pats, pokes, a double-click, coming back.

The avatar server calls `handle(kind)` on its request threads, so it only queues
the interaction; one worker thread does the work. A head pat or a poke gets a
short spoken line with a face, a double-click makes her listen, and coming back
after a while gets a greeting. Lines come from `avatar.interactions.lines`; she
never says the same line of a kind twice in a row.

She stays quiet while she is busy (answering, speaking, announcing) and for
`reaction_cooldown_s` after the previous line ends, so clicking never piles up
replies. A double-click always opens the follow-up window when she is free
(`listen`, `Assistant.listen_now`); the handler then says a `listen` line through
`speak` like any reaction, so line choice, repeat avoidance, and the cooldown
live here and the window opens even when that line is skipped.

During a focus block (`quiet`) she never welcomes Master back: the greeting is
the one reaction she starts herself, and it would open the follow-up window. The
skipped greeting is dropped, not saved for the break. Pats, pokes, and a
double-click are Master's own and are answered as always.

A double-click while she speaks cuts her off: `handle` calls `interrupt`
(`Assistant.interrupt`) at once on the server's thread. When that stopped her
speech, the worker only opens listening and says no line; it does so although she
is still busy winding the cut speech down, since `listen` waits for that.

The global hotkeys (`waifu.hotkey_actions`) `press` a double-click (Ctrl+Alt+C) or
a listen after a cut they made themselves (Ctrl+Alt+X), handled exactly as above but
whatever the avatar's switches say: the owner who turned the double-click off still
has his keys.
"""

from __future__ import annotations

import logging
import queue
import random
import threading
import time
from collections.abc import Callable
from types import TracebackType

from waifu.config import AvatarInteractionsConfig, at_sentence_start

logger = logging.getLogger(__name__)

QUEUE_SIZE = 32
WELCOME_BACK_GAP_S = 30 * 60
STOP_TIMEOUT_S = 5.0
WORKER_NAME = "avatar-interactions"
# kind -> (avatar.interactions.lines key, emotion of the line)
REACTIONS: dict[str, tuple[str, str]] = {
    "head_pat": ("head_pat", "happy"),
    "body_touch": ("body_touch", "surprised"),
    "double_click": ("listen", "happy"),
    "welcome_back": ("welcome_back", "happy"),
}
# Queued instead of a double-click that cut her off: she only listens.
LISTEN_AFTER_CUT = "listen_after_cut"
# What a global hotkey may `press`.
PRESSABLE = ("double_click", LISTEN_AFTER_CUT)


def _nothing_to_interrupt() -> bool:
    """The default `interrupt`: without her voice there is no speech to cut."""
    return False


class InteractionHandler:
    def __init__(
        self,
        config: AvatarInteractionsConfig,
        *,
        speak: Callable[[str, str], None],
        listen: Callable[[], None],
        is_busy: Callable[[], bool],
        interrupt: Callable[[], bool] = _nothing_to_interrupt,
        quiet: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        owner_call: str = "Master",
    ) -> None:
        """`quiet` (the focus timer's `in_block`) says when she keeps her welcome-back to
        herself."""
        self._config = config
        self._speak = speak
        self._listen = listen
        self._is_busy = is_busy
        self._interrupt = interrupt
        self._quiet = quiet
        self._owner_call = owner_call
        self._clock = clock
        self._rng = rng or random.Random()
        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=QUEUE_SIZE)
        # Guards _accepting, _worker, _stopping; a double-click cuts her off under it, so a
        # stopped handler never does.
        self._lock = threading.Lock()
        self._accepting = True
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()
        self._react_lock = threading.Lock()  # one reaction at a time; guards the state below
        self._quiet_since: float | None = None  # when the previous line ended
        self._welcomed_at: float | None = None
        self._previous_line: dict[str, str] = {}

    def handle(self, kind: str) -> None:
        """Queue one interaction from the page; never blocks. A no-op after `stop()`.

        A double-click first cuts off whatever she is saying, right here (`interrupt` never
        waits for her voice); when it did, only her listening is queued.
        """
        if kind not in REACTIONS:
            logger.warning("Ignoring an unknown avatar interaction %r", kind)
            return
        if not self._switched_on(kind):
            logger.debug("Avatar interaction %s is switched off", kind)
            return
        self._queue_interaction(kind)

    def press(self, kind: str) -> None:
        """Queue a global hotkey's double-click (talk now) or listen after a cut it made itself
        (LISTEN_AFTER_CUT), whatever the avatar's switches say; never blocks. A no-op after
        `stop()`. ValueError for any other kind."""
        if kind not in PRESSABLE:
            raise ValueError(f"a hotkey presses only {PRESSABLE}, not {kind!r}")
        self._queue_interaction(kind)

    def _queue_interaction(self, kind: str) -> None:
        """Queue `kind`, a double-click first cutting off whatever she is saying."""
        with self._lock:
            if not self._accepting:
                return
            if kind == "double_click" and self._interrupt():
                logger.info("A double-click cut her off; she listens next")
                kind = LISTEN_AFTER_CUT
            try:
                self._queue.put_nowait(kind)
            except queue.Full:
                logger.warning("Avatar interaction queue is full; dropping %s", kind)

    def drain(self) -> None:
        """Handle every queued interaction now, in the calling thread, as the worker does."""
        while True:
            try:
                kind = self._queue.get_nowait()
            except queue.Empty:
                return
            if kind is not None:
                self._react(kind)

    def start(self) -> None:
        """Start the worker (again after `stop()`); starting a running handler does nothing."""
        with self._lock:
            if self._worker is not None:
                return
            self._accepting = True
            self._stopping = threading.Event()
            self._worker = threading.Thread(
                target=self._work, args=(self._stopping,), name=WORKER_NAME, daemon=True
            )
            self._worker.start()

    def stop(self) -> None:
        """Stop taking interactions, drop the queued ones, and wait for the worker (bounded)."""
        with self._lock:
            self._accepting = False
            worker, self._worker = self._worker, None
            self._stopping.set()
            self._discard_queued()
            if worker is not None:
                self._queue.put_nowait(None)  # wakes the worker; the queue was just emptied
        if worker is None:
            return
        worker.join(STOP_TIMEOUT_S)
        if worker.is_alive():
            logger.warning("The avatar interaction worker is still busy; leaving it behind")

    def __enter__(self) -> InteractionHandler:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def _work(self, stopping: threading.Event) -> None:
        while True:
            kind = self._queue.get()
            if stopping.is_set():
                return
            if kind is not None:
                self._react(kind)

    def _discard_queued(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _switched_on(self, kind: str) -> bool:
        config = self._config
        switches = {
            "head_pat": config.head_pat,
            "body_touch": config.body_touch,
            "double_click": config.double_click_listen,
            "welcome_back": config.welcome_back_after_min > 0,
        }
        return config.enabled and switches[kind]

    def _react(self, kind: str) -> None:
        with self._react_lock:
            try:
                self._respond(kind)
            except Exception:  # the worker must outlive a broken voice or avatar
                logger.exception("Reacting to the avatar interaction %s failed", kind)

    def _respond(self, kind: str) -> None:
        if kind == LISTEN_AFTER_CUT:
            # She is still busy ending the cut speech: `listen` waits for it, so neither
            # the busy check nor the cooldown applies, and no line follows.
            self._listen()
            return
        if kind == "welcome_back" and self._quiet is not None and self._quiet():
            logger.debug("Not welcoming Master back: a focus block is under way")
            return
        if self._is_busy():
            logger.debug("Ignoring %s: she is busy", kind)
            return
        if kind == "double_click":
            self._listen()
        now = self._clock()
        if _within(now, self._quiet_since, self._config.reaction_cooldown_s):
            logger.debug("Not answering %s: the previous reaction was just now", kind)
            return
        if kind == "welcome_back":
            if _within(now, self._welcomed_at, WELCOME_BACK_GAP_S):
                logger.debug("Not welcoming Master back again so soon")
                return
            self._welcomed_at = now
        lines_key, emotion = REACTIONS[kind]
        line = self._pick_line(lines_key)
        logger.info("Reacting to %s: %s", kind, line)
        try:
            self._speak(line, emotion)
        finally:
            self._quiet_since = self._clock()

    def _pick_line(self, lines_key: str) -> str:
        """A random line of `lines_key`, never the previous one when there is another, with
        `{owner}` filled with what she calls the owner (capitalised when it opens the line)."""
        lines = self._config.lines[lines_key]
        previous = self._previous_line.get(lines_key)
        line = self._rng.choice([line for line in lines if line != previous] or lines)
        self._previous_line[lines_key] = line
        # str.replace, not str.format: a line the owner wrote may hold other braces.
        filled = line.replace("{owner}", self._owner_call)
        return at_sentence_start(filled) if line.startswith("{owner}") else filled


def _within(now: float, since: float | None, span_s: float) -> bool:
    """Whether less than `span_s` has passed since `since` (never, when it is None)."""
    return since is not None and now - since < span_s
