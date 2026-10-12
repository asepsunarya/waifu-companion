"""Viseme track: the avatar's mouth shapes for the audio she is about to play.

Every 1/fps s frame gets five VRM viseme weights in the order (aa, ih, ou, ee,
oh). How far the mouth opens follows the frame's loudness, smoothed with a fast
attack and a slower release so it snaps open on a syllable and closes softly.
Which vowel it shapes follows the spectral centroid of the voice band: rounded
vowels (u, o) keep their energy low, spread vowels (e, i) push more of it high.
A share of aa is always mixed in so the mouth visibly opens on every sound.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

Visemes = tuple[float, float, float, float, float]

VISEMES = ("aa", "ih", "ou", "ee", "oh")
CLOSED_DB = -50.0
OPEN_DB = -15.0
ATTACK = 0.6
RELEASE = 0.25
AA_BASELINE = 0.35
MIN_WINDOW = 512
VOICE_BAND_HZ = (80.0, 5000.0)
# Spectral centroid at which each shape is pure, ascending. Between two
# neighbours the shape blends linearly, so the crossovers sit at 700, 1100,
# 1800 and 2600 Hz; below ou and above ih the shape stays pure.
SHAPE_CENTRE_HZ = {"ou": 500.0, "oh": 900.0, "aa": 1300.0, "ee": 2300.0, "ih": 2900.0}


def viseme_track(audio: np.ndarray, sample_rate: int, fps: int = 60) -> list[Visemes]:
    """Viseme weights for each 1/fps s frame of `audio`, `ceil(duration * fps)` frames.

    `audio` is float mono, or (samples, channels) which is averaged to mono.
    Each weight is within 0..1 and a frame's weights sum to its openness.
    """
    samples = np.asarray(audio, dtype=np.float64)
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    if samples.size == 0:
        return []
    # Ceiling in integers: in floats, 12400 samples at 24 kHz make 31.000000000000004 frames.
    frames = -(-samples.size * fps // sample_rate)
    bounds = np.minimum(np.arange(frames + 1) * sample_rate // fps, samples.size)
    starts, ends = bounds[:-1], bounds[1:]
    openness = _smoothed_openness(_level_db(samples, starts, ends))
    window = max(-(-sample_rate // fps), MIN_WINDOW)
    shapes = _vowel_shapes(_centroids(samples, sample_rate, (starts + ends) // 2, window))
    baseline = np.array([1.0 if name == "aa" else 0.0 for name in VISEMES])
    weights = openness[:, np.newaxis] * (AA_BASELINE * baseline + (1 - AA_BASELINE) * shapes)
    return [tuple(frame) for frame in weights.tolist()]


def _level_db(samples: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """RMS level of each frame `samples[start:end]` in dBFS."""
    energy = np.add.reduceat(np.square(samples), starts)
    rms = np.sqrt(energy / (ends - starts))
    return 20 * np.log10(np.maximum(rms, 1e-10))


def _smoothed_openness(levels_db: np.ndarray) -> np.ndarray:
    """Map levels linearly from closed to fully open, then smooth frame by frame."""
    targets = np.clip((levels_db - CLOSED_DB) / (OPEN_DB - CLOSED_DB), 0.0, 1.0)
    openness = np.empty_like(targets)
    current = 0.0
    for index, target in enumerate(targets.tolist()):
        current += (ATTACK if target > current else RELEASE) * (target - current)
        openness[index] = current
    return openness


def _centroids(
    samples: np.ndarray, sample_rate: int, centres: np.ndarray, window: int
) -> np.ndarray:
    """Spectral centroid of the voice band in a Hann window around each frame centre."""
    half = window // 2
    padded = np.pad(samples, (half, window - half))
    # Row `centre` of the padded view starts `half` samples before that centre.
    windows = sliding_window_view(padded, window)[centres]
    magnitude = np.abs(np.fft.rfft(windows * np.hanning(window), axis=1))
    freqs = np.fft.rfftfreq(window, d=1 / sample_rate)
    low, high = VOICE_BAND_HZ
    in_band = (freqs >= low) & (freqs <= high)
    total = magnitude[:, in_band].sum(axis=1)
    weighted = magnitude[:, in_band] @ freqs[in_band]
    # Sound with nothing in the voice band (a DC offset) gets the neutral aa shape.
    fallback = np.full_like(total, SHAPE_CENTRE_HZ["aa"])
    return np.divide(weighted, total, out=fallback, where=total > 0)


def _vowel_shapes(centroids: np.ndarray) -> np.ndarray:
    """Blend of pure vowel shapes for each centroid, columns in `VISEMES` order, rows sum to 1."""
    centres = np.array(list(SHAPE_CENTRE_HZ.values()))
    shapes = np.empty((centroids.size, len(VISEMES)))
    for column, name in enumerate(VISEMES):
        pure = np.array([1.0 if shape == name else 0.0 for shape in SHAPE_CENTRE_HZ])
        shapes[:, column] = np.interp(centroids, centres, pure)
    return shapes
