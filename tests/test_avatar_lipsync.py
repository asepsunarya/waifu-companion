import math

import numpy as np
import pytest

from waifu.avatar.lipsync import viseme_track

SAMPLE_RATE = 48000
FPS = 60
FRAME_LEN = SAMPLE_RATE // FPS
AA, IH, OU, EE, OH = range(5)


def tone(seconds: float, freq: float, db: float) -> np.ndarray:
    """A sine whose RMS level is `db` dBFS."""
    t = np.arange(round(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    amplitude = 10 ** (db / 20) * np.sqrt(2)
    return amplitude * np.sin(2 * np.pi * freq * t)


def band(seconds: float, low: float, high: float, db: float, seed: int = 0) -> np.ndarray:
    """White noise kept only between `low` and `high` Hz, at an RMS level of `db` dBFS."""
    noise = np.random.default_rng(seed).standard_normal(round(seconds * SAMPLE_RATE))
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(noise.size, d=1 / SAMPLE_RATE)
    spectrum[(freqs < low) | (freqs > high)] = 0
    filtered = np.fft.irfft(spectrum, n=noise.size)
    return filtered * (10 ** (db / 20) / np.sqrt(np.mean(np.square(filtered))))


def openness(track: list[tuple[float, ...]]) -> list[float]:
    return [sum(frame) for frame in track]


def test_empty_audio_gives_an_empty_track():
    assert viseme_track(np.zeros(0, dtype=np.float32), SAMPLE_RATE) == []


@pytest.mark.parametrize(
    ("samples", "sample_rate", "fps", "frames"),
    [
        (48000, 48000, 60, 60),
        (48001, 48000, 60, 61),
        (1, 48000, 60, 1),
        (75411, 22050, 60, 206),  # 3.42 s
        (12400, 24000, 60, 31),  # exactly 31 frames, although 12400 / 24000 * 60 > 31 in floats
        (4480, 16000, 25, 7),  # exactly 7 frames, although 4480 / 16000 * 25 > 7 in floats
        (12000, 24000, 30, 15),
    ],
)
def test_there_is_one_frame_per_started_frame_period(samples, sample_rate, fps, frames):
    assert len(viseme_track(np.zeros(samples), sample_rate, fps=fps)) == frames


def test_frames_are_five_python_floats_in_aa_ih_ou_ee_oh_order():
    track = viseme_track(tone(0.2, 1300, -10), SAMPLE_RATE)

    assert len(track) == 12
    for frame in track:
        assert isinstance(frame, tuple)
        assert len(frame) == 5
        assert all(type(weight) is float for weight in frame)


def test_silence_keeps_the_mouth_closed():
    track = viseme_track(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)

    assert track == [(0.0, 0.0, 0.0, 0.0, 0.0)] * 60


def test_room_noise_below_minus_50_dbfs_keeps_the_mouth_closed():
    track = viseme_track(band(0.5, 80, 5000, -60), SAMPLE_RATE)

    assert max(openness(track)) == 0.0


def test_a_low_tone_rounds_the_mouth_to_ou_and_oh():
    track = viseme_track(tone(0.5, 250, -10), SAMPLE_RATE)

    for frame in track:
        assert frame[OU] + frame[OH] > frame[EE] + frame[IH]
        assert max(frame) == frame[OU]


def test_a_high_band_spreads_the_lips_to_ih_and_ee():
    track = viseme_track(band(0.5, 3000, 4000, -12), SAMPLE_RATE)

    for frame in track:
        assert frame[IH] + frame[EE] > frame[OU] + frame[OH]
        assert max(frame) == frame[IH]


@pytest.mark.parametrize(
    "signal",
    [tone(0.5, 250, -10), band(0.5, 3000, 4000, -12)],
    ids=["low-tone", "high-band"],
)
def test_every_voiced_frame_keeps_a_share_of_aa_so_the_mouth_always_opens(signal):
    # Without the aa baseline a rounded or spread vowel is a pure ou or ih frame, which
    # barely opens the model's mouth.
    voiced = [frame for frame in viseme_track(signal, SAMPLE_RATE) if sum(frame) > 0]

    assert voiced
    for frame in voiced:
        assert frame[AA] >= 0.3 * sum(frame)


def test_a_mid_tone_opens_to_aa():
    track = viseme_track(tone(0.5, 1300, -10), SAMPLE_RATE)

    for frame in track:
        others = [weight for index, weight in enumerate(frame) if index != AA]
        assert frame[AA] > max(others)


def test_a_centroid_between_two_vowels_blends_them():
    track = viseme_track(tone(0.5, 1800, -10), SAMPLE_RATE)

    aa, ih, ou, ee, oh = track[-1]
    assert aa > 0.35
    assert ee > 0.2
    assert ou == oh == ih == 0.0


def test_weights_sum_to_at_most_the_openness_of_a_half_open_mouth():
    # -32.5 dBFS sits halfway between closed (-50 dBFS) and fully open (-15 dBFS); 1680 Hz fits
    # exactly 28 periods in a frame, so every frame is at that level and blends aa with ee.
    track = viseme_track(tone(0.5, 1680, -32.5), SAMPLE_RATE)

    sums = openness(track)
    assert max(sums) <= 0.5 + 1e-9
    assert sums[-1] == pytest.approx(0.5, abs=0.01)
    assert sum(weight > 0 for weight in track[-1]) >= 2


def test_every_weight_stays_between_0_and_1_for_loud_varied_audio():
    rng = np.random.default_rng(3)
    levels = np.repeat(rng.uniform(0.0, 1.5, 60), FRAME_LEN)
    audio = np.clip(rng.standard_normal(SAMPLE_RATE) * levels, -1.0, 1.0)

    for frame in viseme_track(audio, SAMPLE_RATE):
        assert all(0.0 <= weight <= 1.0 for weight in frame)
        assert sum(frame) <= 1.0 + 1e-9


def test_audio_without_voice_band_energy_still_gives_finite_weights():
    track = viseme_track(np.full(SAMPLE_RATE // 2, 0.1), SAMPLE_RATE)

    for frame in track:
        assert all(math.isfinite(weight) and 0.0 <= weight <= 1.0 for weight in frame)


def test_the_mouth_opens_fast_and_closes_slowly():
    onset, offset, total = 10, 30, 60
    audio = np.zeros(total * FRAME_LEN, dtype=np.float32)
    audio[onset * FRAME_LEN : offset * FRAME_LEN] = tone((offset - onset) / FPS, 1300, -10)

    sums = openness(viseme_track(audio, SAMPLE_RATE))

    assert sums[onset - 1] == 0.0
    assert sums[onset + 1] > 0.5
    assert sums[offset - 1] > 0.99
    assert sums[offset + 2] > 0.1
    frames_to_open = next(i for i in range(onset, total) if sums[i] > 0.5) - onset
    frames_to_close = next(i for i in range(offset, total) if sums[i] < 0.5) - offset
    assert frames_to_open < frames_to_close
    assert sums[-1] < 0.01


def test_stereo_audio_is_downmixed_by_averaging_the_channels():
    left, right = tone(0.3, 250, -10), tone(0.3, 2400, -10)

    stereo = viseme_track(np.column_stack([left, right]).astype(np.float32), SAMPLE_RATE)
    mono = viseme_track((left + right) / 2, SAMPLE_RATE)

    assert np.allclose(stereo, mono)


def test_float32_and_float64_audio_give_the_same_track():
    audio = tone(0.3, 900, -20)

    assert np.allclose(
        viseme_track(audio.astype(np.float32), SAMPLE_RATE), viseme_track(audio, SAMPLE_RATE)
    )


def test_the_track_is_deterministic_and_leaves_the_input_alone():
    audio = band(0.5, 300, 3000, -20, seed=7)
    original = audio.copy()

    assert viseme_track(audio, SAMPLE_RATE) == viseme_track(audio, SAMPLE_RATE)
    assert np.array_equal(audio, original)
