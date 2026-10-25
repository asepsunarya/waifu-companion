"""Her fillers: a short line said while the brain still thinks up a slow reply.

The fillers are synthesised in the background after she is ready and kept in memory, so
saying one never waits for her voice: during a turn there is no time to synthesise. A filler
that her voice could not say, or said in a stand-in voice (the Windows fallback for a failing
Edge voice, the plain voice while RVC fails), is left out: a filler in another voice than the
reply after it sounds like someone else. The missing ones are tried again later, once her voice
may be her own again. The log never holds their words.
"""

from __future__ import annotations

import dataclasses
import logging
import random
import threading
import time
from collections.abc import Callable, Sequence
from typing import Protocol

import numpy as np

from waifu.voice.tts import CONVERSION_RETRY_S, FALLBACK_RETRY_S, TtsError

logger = logging.getLogger(__name__)

# Indonesian only, so each is one speech run and one RVC conversion; `{owner}` is what she
# calls the owner (owner_call).
NODS = (
    "Hmm, bentar ya…",
    "Sebentar, kucek dulu ya.",
    "Oke, kupikirkan dulu…",
    "Tunggu sebentar ya, {owner}.",
    "Hmm, coba kulihat dulu ya.",
    "Sebentar ya, {owner}…",
)
# Past the end of the router's pause, against clock steps.
RETRY_MARGIN_S = 5.0


def retry_after_s(fallback_retry_s: float) -> float:
    """How long `keep_preparing` waits before trying the missing fillers again: past her voice's
    pause on a failing engine (`fallback_retry_s`, `tts.fallback.retry_s`) and on an unreachable
    converter, during which every clip comes in the stand-in voice."""
    return max(fallback_retry_s, CONVERSION_RETRY_S) + RETRY_MARGIN_S


@dataclasses.dataclass(frozen=True)
class Nod:
    """One filler as she says it."""

    text: str
    audio: np.ndarray
    sample_rate: int


class NodVoice(Protocol):
    """Her voice (the `SpeechRouter`)."""

    def synthesize(self, text: str) -> tuple[np.ndarray, int]: ...

    @property
    def degraded(self) -> bool:
        """Whether the clip just made came in a stand-in voice."""
        ...


class NodBank:
    """Her fillers, made by `prepare` and handed out by `pick`; safe across threads."""

    def __init__(
        self,
        voice: NodVoice,
        *,
        owner_call: str = "Master",
        lines: Sequence[str] = NODS,
        rng: random.Random | None = None,
        retry_s: float = retry_after_s(FALLBACK_RETRY_S),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._voice = voice
        self.retry_s = retry_s
        self._sleep = sleep
        # str.replace, not str.format: like the avatar's lines, a line may hold other braces.
        self._lines = [line.replace("{owner}", owner_call) for line in lines]
        self._rng = rng or random.Random()
        self._lock = threading.Lock()  # guards the two below
        self._kept: dict[str, Nod] = {}
        self._last: str | None = None

    def keep_preparing(self) -> None:
        """`prepare`, then again every `retry_s` until every filler is kept, on the calling
        thread: a short outage of her voice while they are made costs them only until then."""
        while not self.prepare():
            logger.info("Trying the missing fillers again in %.0f s", self.retry_s)
            self._sleep(self.retry_s)

    def prepare(self) -> bool:
        """Synthesise every filler not kept yet, one at a time on the calling thread, and keep
        each one her own voice said; True once every filler is kept. A stand-in voice stops it:
        the router keeps it for a while, so the rest would come in it too. Failures are logged,
        never raised."""
        warned = False
        for text in self._lines:
            with self._lock:
                if text in self._kept:
                    continue
            try:
                audio, sample_rate = self._voice.synthesize(text)
            except TtsError as exc:
                if warned:
                    logger.debug("Could not synthesise a filler either; leaving it out: %s", exc)
                else:
                    logger.warning("Could not synthesise a filler; leaving it out: %s", exc)
                    warned = True
                continue
            except Exception:
                logger.exception("Could not synthesise a filler; leaving it out")
                continue
            if self._voice.degraded:
                logger.info("Her voice is a stand-in; leaving out the fillers not kept yet")
                break
            with self._lock:
                self._kept[text] = Nod(text, audio, sample_rate)
        with self._lock:
            kept = len(self._kept)
            kept_all = all(text in self._kept for text in self._lines)
        logger.info("Prepared %d of %d fillers", kept, len(self._lines))
        return kept_all

    def pick(self) -> Nod | None:
        """A random kept filler other than the last one picked; None when there is none."""
        with self._lock:
            choices = [nod for text, nod in self._kept.items() if text != self._last]
            if not choices:
                return None
            nod = self._rng.choice(choices)
            self._last = nod.text
            return nod
