import numpy as np
import pytest

from waifu.config import VadConfig
from waifu.voice.segmenter import EnergySegmenter

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_LEN = SAMPLE_RATE * FRAME_MS // 1000
FRAME_S = FRAME_MS / 1000


def noise(n_frames: int, db: float, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n_frames * FRAME_LEN) * 10 ** (db / 20)).astype(np.float32)


def tone(n_frames: int, db: float, freq: float = 220.0) -> np.ndarray:
    t = np.arange(n_frames * FRAME_LEN) / SAMPLE_RATE
    amplitude = 10 ** (db / 20) * np.sqrt(2)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def fluent_speech(n_frames: int, db: float) -> np.ndarray:
    """Four syllables a second, dipping about 20 dB between them but never to the room's floor."""
    t = np.arange(n_frames * FRAME_LEN) / SAMPLE_RATE
    envelope = 0.1 + 0.9 * np.abs(np.sin(2 * np.pi * 2 * t))
    return (envelope * tone(n_frames, db)).astype(np.float32)


def speech_in_room(n_frames: int, voice_db: float, room_db: float, seed: int = 1) -> np.ndarray:
    return tone(n_frames, voice_db) + noise(n_frames, room_db, seed)


def feed_all(segmenter: EnergySegmenter, audio: np.ndarray) -> list:
    results = []
    for start in range(0, len(audio), FRAME_LEN):
        utterance = segmenter.feed(audio[start : start + FRAME_LEN])
        if utterance is not None:
            results.append(utterance)
    return results


def first_loud_sample(audio: np.ndarray, level: float = 0.01) -> int:
    return int(np.argmax(np.abs(audio) > level))


def test_speech_in_silence_yields_one_float32_utterance_with_pre_roll():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig(pre_roll_ms=300))
    audio = np.concatenate([noise(40, -65), tone(40, -20), noise(50, -65, seed=2)])

    utterances = feed_all(segmenter, audio)

    assert len(utterances) == 1
    utterance = utterances[0]
    assert utterance.sample_rate == SAMPLE_RATE
    assert utterance.audio.dtype == np.float32
    assert first_loud_sample(utterance.audio) / SAMPLE_RATE == pytest.approx(0.3, abs=FRAME_S)


def test_trailing_silence_is_trimmed_from_the_utterance():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig(pre_roll_ms=300, silence_ms=700))
    audio = np.concatenate([noise(40, -65), tone(40, -20), noise(50, -65, seed=2)])

    (utterance,) = feed_all(segmenter, audio)

    voice_and_pre_roll_s = 0.3 + 40 * FRAME_S
    assert voice_and_pre_roll_s < utterance.duration <= voice_and_pre_roll_s + 0.2


def test_utterance_ends_once_silence_lasts_silence_ms():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig(silence_ms=700))
    audio = np.concatenate([noise(40, -65), tone(40, -20), noise(60, -65, seed=2)])

    ended_at_frame = None
    for index in range(len(audio) // FRAME_LEN):
        frame = audio[index * FRAME_LEN : (index + 1) * FRAME_LEN]
        if segmenter.feed(frame) is not None:
            ended_at_frame = index
    silent_frames_heard = ended_at_frame + 1 - 80

    assert 0.7 <= silent_frames_heard * FRAME_S < 0.7 + FRAME_S


def test_clicks_too_short_for_onset_never_start_an_utterance():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig(min_speech_ms=0))
    click = tone(2, -10)
    audio = np.concatenate([noise(30, -65), click, noise(30, -65, 2), click, noise(40, -65, 3)])

    assert feed_all(segmenter, audio) == []


def test_burst_shorter_than_min_speech_is_discarded():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig(min_speech_ms=300))
    audio = np.concatenate([noise(30, -65), tone(6, -20), noise(60, -65, seed=2)])

    assert feed_all(segmenter, audio) == []


def test_long_speech_is_force_split_at_max_speech_s():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig(max_speech_s=2.0))
    audio = np.concatenate([noise(20, -65), tone(170, -20), noise(40, -65, seed=2)])

    utterances = feed_all(segmenter, audio)

    assert len(utterances) >= 2
    assert utterances[0].duration == pytest.approx(2.0, abs=FRAME_S)
    assert all(u.duration <= 2.0 + FRAME_S for u in utterances)


def test_moderate_voice_is_detected_in_a_quiet_room():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig(threshold_db=-45, margin_db=12))
    audio = np.concatenate([noise(30, -70), speech_in_room(30, -42, -70), noise(40, -70, 2)])

    assert len(feed_all(segmenter, audio)) == 1


def test_noise_floor_adapts_so_a_steady_hum_raises_the_speech_threshold():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig(threshold_db=-45, margin_db=12))
    quiet_then_hum = np.concatenate([noise(30, -70), noise(200, -50, seed=2)])
    assert feed_all(segmenter, quiet_then_hum) == []

    moderate_voice = np.concatenate([speech_in_room(30, -42, -50, 3), noise(40, -50, seed=4)])
    loud_voice = np.concatenate([speech_in_room(30, -20, -50, 5), noise(40, -50, seed=6)])

    assert feed_all(segmenter, moderate_voice) == []
    assert len(feed_all(segmenter, loud_voice)) == 1


@pytest.mark.parametrize("mic_paused_during_the_step", [False, True])
def test_noise_floor_follows_a_sudden_hum_louder_than_the_threshold(mic_paused_during_the_step):
    # An AC switched on while she answers: when the mic resumes, the room is suddenly louder.
    vad = VadConfig()
    segmenter = EnergySegmenter(SAMPLE_RATE, vad)
    assert feed_all(segmenter, noise(100, -65)) == []
    if mic_paused_during_the_step:
        segmenter.reset()

    hum_utterances = feed_all(segmenter, noise(2000, -40, seed=2))
    voice = feed_all(
        segmenter, np.concatenate([speech_in_room(40, -20, -40, 3), noise(60, -40, 4)])
    )

    assert len(hum_utterances) <= 1
    assert len(voice) == 1
    assert voice[0].duration < vad.max_speech_s


def test_a_long_sentence_without_pauses_is_kept_whole():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig())
    sentence = fluent_speech(400, -20) + noise(400, -65, seed=2)
    audio = np.concatenate([noise(100, -65), sentence, noise(100, -65, seed=3)])

    (utterance,) = feed_all(segmenter, audio)

    assert utterance.duration >= 400 * FRAME_S


def test_started_at_is_read_from_the_clock_at_onset():
    now = [0.0]
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig(), clock=lambda: now[0])
    audio = np.concatenate([noise(40, -65), tone(40, -20), noise(50, -65, seed=2)])

    utterances = []
    for index in range(len(audio) // FRAME_LEN):
        now[0] = 100.0 + index * FRAME_S
        utterance = segmenter.feed(audio[index * FRAME_LEN : (index + 1) * FRAME_LEN])
        if utterance is not None:
            utterances.append(utterance)

    voice_start = 100.0 + 40 * FRAME_S
    assert voice_start <= utterances[0].started_at <= voice_start + 0.1


def test_reset_discards_a_partial_utterance():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig())
    assert feed_all(segmenter, np.concatenate([noise(40, -65), tone(20, -20)])) == []

    segmenter.reset()

    assert feed_all(segmenter, noise(60, -65, seed=2)) == []


def test_busy_only_while_inside_an_utterance():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig())

    feed_all(segmenter, noise(40, -65))
    assert segmenter.busy is False
    feed_all(segmenter, tone(10, -20))
    assert segmenter.busy is True
    feed_all(segmenter, noise(50, -65, seed=2))
    assert segmenter.busy is False


def test_a_reset_segmenter_is_not_busy():
    segmenter = EnergySegmenter(SAMPLE_RATE, VadConfig())
    feed_all(segmenter, np.concatenate([noise(40, -65), tone(10, -20)]))

    segmenter.reset()

    assert segmenter.busy is False
