"""Her own voice volume on the PC: a gain on each clip right before the speaker plays it.

The gain comes after synthesis and the anime voice (RVC), never before: the clip goes to the
RVC server as 16-bit PCM, which would hard-clip a gain above full scale. It is linear in
amplitude (50 halves every sample). Above 100 every sample also goes through a soft limiter, so
no peak reaches full scale, whatever level the voice came at. The avatar mouths the clip before
the gain, since `VolumePlayer` applies it inside the player, after the avatar got the clip.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:  # the voice package never imports the voice loop
    from waifu.app import AudioPlayer

LEVELS = (10, 150)  # percent, the lowest and the highest level
STEP = 20  # percent, one "louder" or "softer"
NORMAL = 100
# The soft limiter: linear up to the knee, then a tanh curve that flattens out below the ceiling.
_KNEE = 0.8
_HEADROOM = 0.18  # the ceiling is _KNEE + _HEADROOM, 0.98 of full scale


def apply_volume(audio: np.ndarray, level: int) -> np.ndarray:
    """`audio` (float32) at `level` percent: itself at 100, scaled below, scaled and
    soft-limited above."""
    if level == NORMAL:
        return audio
    scaled = audio * np.float32(level / NORMAL)
    if level < NORMAL:
        return scaled
    magnitude = np.abs(scaled)
    over = magnitude > _KNEE
    limited = _KNEE + _HEADROOM * np.tanh((magnitude[over] - _KNEE) / _HEADROOM)
    scaled[over] = np.copysign(limited, scaled[over])
    return scaled


class VolumePlayer:
    """An `AudioPlayer` that plays every clip through `player` at `level()` percent, read at
    each clip. It makes no PortAudio call itself, so the wrapped `Player`'s one-thread rule
    holds."""

    def __init__(self, player: AudioPlayer, level: Callable[[], int]) -> None:
        self._player = player
        self._level = level

    def play(self, audio: np.ndarray, sample_rate: int) -> bool:
        return self._player.play(apply_volume(audio, self._level()), sample_rate)

    def stop(self) -> bool:
        return self._player.stop()
