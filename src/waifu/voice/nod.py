"""Her fillers: a short line said while the brain still thinks up a slow reply.

The fillers are synthesised once, in the background after she is ready, and kept in memory,
so saying one never waits for her voice: during a turn there is no time to synthesise. A
filler that her voice could not say, or said in a stand-in voice (the Windows fallback for a
failing Edge voice, the plain voice while RVC fails), is left out: a filler in another voice
than the reply after it sounds like someone else. The log never holds their words.
"""

from __future__ import annotations

import dataclasses
import logging
import random
import threading
from collections.abc import Sequence
from typing import Protocol

import numpy as np

from waifu.voice.tts import TtsError

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
    ) -> None:
        self._voice = voice
        # str.replace, not str.format: like the avatar's lines, a line may hold other braces.
        self._lines = [line.replace("{owner}", owner_call) for line in lines]
        self._rng = rng or random.Random()
        self._lock = threading.Lock()  # guards the two below
        self._kept: dict[str, Nod] = {}
        self._last: str | None = None

    def prepare(self) -> None:
        """Synthesise every filler not kept yet, one at a time on the calling thread, and keep
        each one her own voice said. Failures are logged, never raised."""
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
                logger.info("Leaving out a filler made in a stand-in voice")
                continue
            with self._lock:
                self._kept[text] = Nod(text, audio, sample_rate)
        with self._lock:
            kept = len(self._kept)
        logger.info("Prepared %d of %d fillers", kept, len(self._lines))

    def pick(self) -> Nod | None:
        """A random kept filler other than the last one picked; None when there is none."""
        with self._lock:
            choices = [nod for text, nod in self._kept.items() if text != self._last]
            if not choices:
                return None
            nod = self._rng.choice(choices)
            self._last = nod.text
            return nod
