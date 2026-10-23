import numpy as np
import pytest

from waifu.voice.volume import LEVELS, NORMAL, STEP, VolumePlayer, apply_volume


def sine(amplitude: float, samples: int = 2400) -> np.ndarray:
    return (amplitude * np.sin(np.linspace(0, 40 * np.pi, samples))).astype(np.float32)


class RecordingPlayer:
    """Stands in for the speaker: records each clip; `answer` is what `play` returns."""

    def __init__(self, answer: bool = True, stopped: bool = True) -> None:
        self.answer = answer
        self.stopped = stopped
        self.clips: list[tuple[np.ndarray, int]] = []
        self.stops = 0

    def play(self, audio: np.ndarray, sample_rate: int) -> bool:
        self.clips.append((audio, sample_rate))
        return self.answer

    def stop(self) -> bool:
        self.stops += 1
        return self.stopped


def test_the_levels_run_from_ten_to_one_hundred_fifty_in_steps_of_twenty():
    assert (LEVELS, STEP, NORMAL) == ((10, 150), 20, 100)


def test_at_one_hundred_the_clip_is_played_untouched():
    clip = sine(1.0)

    assert apply_volume(clip, 100) is clip


def test_below_one_hundred_every_sample_is_scaled_by_the_level():
    clip = sine(0.9)

    assert np.array_equal(apply_volume(clip, 50), clip * np.float32(0.5))
    assert np.allclose(apply_volume(clip, 10), clip * 0.1)


def test_above_one_hundred_a_quiet_clip_is_amplified_exactly():
    clip = sine(0.5)  # 0.75 at 150: still below the limiter's knee

    assert np.allclose(apply_volume(clip, 150), clip * 1.5)


def test_above_one_hundred_a_full_scale_clip_never_reaches_full_scale():
    clip = sine(1.0)

    loud = apply_volume(clip, 150)

    assert np.max(np.abs(loud)) < 0.98
    assert np.max(np.abs(apply_volume(np.array([1.0, -1.0], dtype=np.float32), 150))) < 0.98


def test_the_limiter_keeps_the_order_of_the_samples():
    ramp = np.linspace(-1.0, 1.0, 2001, dtype=np.float32)

    limited = apply_volume(ramp, 150)

    assert np.all(np.diff(limited) > 0)


def test_the_limiter_is_continuous_at_its_knee():
    just_below, just_above = np.float32(0.8 / 1.5 - 1e-4), np.float32(0.8 / 1.5 + 1e-4)

    below, above = apply_volume(np.array([just_below, just_above], dtype=np.float32), 150)

    assert above - below == pytest.approx(3e-4, abs=1e-5)


@pytest.mark.parametrize("level", [10, 50, 100, 120, 150])
def test_the_clip_stays_float32(level):
    assert apply_volume(sine(0.9), level).dtype == np.float32


@pytest.mark.parametrize("level", [10, 150])
def test_silence_stays_silent(level):
    assert np.array_equal(apply_volume(np.zeros(240, dtype=np.float32), level), np.zeros(240))
    assert len(apply_volume(np.zeros(0, dtype=np.float32), level)) == 0


def test_the_volume_player_plays_the_clip_at_the_level_it_reads():
    inner = RecordingPlayer()
    clip = sine(0.8)

    assert VolumePlayer(inner, lambda: 50).play(clip, 24000) is True

    [(played, sample_rate)] = inner.clips
    assert sample_rate == 24000
    assert np.allclose(played, clip * 0.5)


def test_the_volume_player_reads_the_level_at_every_clip():
    inner = RecordingPlayer()
    levels = iter([50, 100])
    player = VolumePlayer(inner, lambda: next(levels))
    clip = sine(0.8)

    player.play(clip, 24000)
    player.play(clip, 24000)

    assert np.allclose(inner.clips[0][0], clip * 0.5)
    assert inner.clips[1][0] is clip


def test_the_volume_player_says_when_a_clip_was_cut():
    assert VolumePlayer(RecordingPlayer(answer=False), lambda: 100).play(sine(0.5), 24000) is False


@pytest.mark.parametrize("answer", [True, False])
def test_the_volume_player_passes_stop_through(answer):
    inner = RecordingPlayer(stopped=answer)

    assert VolumePlayer(inner, lambda: 100).stop() is answer
    assert inner.stops == 1
