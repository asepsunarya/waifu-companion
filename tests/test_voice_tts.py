import asyncio
import io
import urllib.error
import urllib.parse

import numpy as np
import pytest
import soundfile as sf
from edge_tts.exceptions import NoAudioReceived

from waifu.config import EdgeVoiceConfig, GptSovitsVoiceConfig, TtsConfig
from waifu.voice.base import TextToSpeech
from waifu.voice.tts import (
    EdgeTts,
    GptSovitsTts,
    SpeechRouter,
    TtsError,
    build_speech_router,
    clean_for_speech,
    split_by_language,
)


def encode(audio: np.ndarray, sample_rate: int, fmt: str) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format=fmt)
    return buffer.getvalue()


def tone(seconds: float, sample_rate: int) -> np.ndarray:
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


# --- split_by_language -------------------------------------------------------


def test_split_separates_indonesian_and_japanese_runs():
    runs = split_by_language("Halo Master, おかえりなさい! Mau apa?")
    assert runs == [("id", "Halo Master,"), ("ja", "おかえりなさい!"), ("id", "Mau apa?")]


def test_split_keeps_kana_kanji_and_long_vowel_mark_in_one_japanese_run():
    assert split_by_language("ラーメン食べたい") == [("ja", "ラーメン食べたい")]


def test_split_treats_half_width_katakana_as_japanese():
    assert split_by_language("ﾊｲ Master") == [("ja", "ﾊｲ"), ("id", "Master")]


def test_split_keeps_japanese_quotes_with_the_japanese_run():
    runs = split_by_language("Aku suka 「さくら」 lho")
    assert runs == [("id", "Aku suka"), ("ja", "「さくら」"), ("id", "lho")]


def test_split_attaches_digits_and_ascii_punctuation_to_the_current_run():
    runs = split_by_language("今日は25度です, Master")
    assert runs == [("ja", "今日は25度です,"), ("id", "Master")]


def test_split_attaches_leading_neutral_characters_to_the_first_run():
    assert split_by_language("123 はい") == [("ja", "123 はい")]
    assert split_by_language("... Halo") == [("id", "... Halo")]


def test_split_labels_latin_text_with_the_given_default_language():
    assert split_by_language("Hello there", default="en") == [("en", "Hello there")]


def test_split_merges_a_lone_japanese_punctuation_mark_into_its_neighbours():
    assert split_by_language("Halo 。 Master") == [("id", "Halo 。 Master")]


def test_split_returns_nothing_for_text_without_letters():
    assert split_by_language("") == []
    assert split_by_language("  !!! 42 ...") == []


# --- clean_for_speech --------------------------------------------------------


def test_clean_removes_emoji_and_pictographs():
    assert clean_for_speech("Siap, Master! 😊✨👍🏻 ❤\ufe0f ♪") == "Siap, Master!"


def test_clean_removes_markdown_headings_emphasis_code_and_list_markers():
    text = "# Status\n- Lampu __kamar__ **menyala**\n* AC `mati`\n1. TV siaga"
    assert clean_for_speech(text) == "Status Lampu kamar menyala AC mati TV siaga"


def test_clean_removes_action_asides_in_asterisks_and_parentheses():
    text = "*tersenyum* Halo Master (mengedipkan mata) おかえり！"
    assert clean_for_speech(text) == "Halo Master おかえり！"


def test_clean_collapses_whitespace():
    assert clean_for_speech("  Halo \n\n  Master\t! ") == "Halo Master !"


def test_clean_keeps_japanese_punctuation_and_long_vowel_mark():
    assert clean_for_speech("はい、ラーメンありがとう！") == "はい、ラーメンありがとう！"


def test_clean_keeps_the_degree_sign_used_for_temperatures():
    assert clean_for_speech("AC sudah 24°C.") == "AC sudah 24°C."


# --- EdgeTts -----------------------------------------------------------------


class FakeCommunicate:
    def __init__(self, chunks=(), error: Exception | None = None):
        self.chunks = list(chunks)
        self.error = error
        self.created: list[tuple] = []

    def __call__(self, text, voice, *, rate, volume, pitch):
        self.created.append((text, voice, rate, volume, pitch))
        return self

    async def stream(self):
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error


def mp3_chunks(audio: np.ndarray, sample_rate: int) -> list[dict]:
    data = encode(audio, sample_rate, "MP3")
    middle = len(data) // 2
    return [
        {"type": "audio", "data": data[:middle]},
        {"type": "SentenceBoundary", "offset": 0.0, "duration": 1.0, "text": "Halo"},
        {"type": "audio", "data": data[middle:]},
    ]


def test_edge_tts_sends_voice_settings_and_decodes_the_mp3_stream():
    fake = FakeCommunicate(mp3_chunks(tone(0.5, 24000), 24000))
    voice = EdgeVoiceConfig(voice="id-ID-GadisNeural", rate="+5%", volume="-10%", pitch="+15Hz")

    audio, sample_rate = EdgeTts(voice, communicate_factory=fake).synthesize("Halo Master", "id")

    assert fake.created == [("Halo Master", "id-ID-GadisNeural", "+5%", "-10%", "+15Hz")]
    assert sample_rate == 24000
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert abs(len(audio) - 12000) < 2400
    assert np.abs(audio).max() > 0.1


def test_edge_tts_downmixes_stereo_audio_to_mono():
    stereo = np.stack([tone(0.5, 24000), tone(0.5, 24000)], axis=1)
    fake = FakeCommunicate(mp3_chunks(stereo, 24000))

    audio, _ = EdgeTts(EdgeVoiceConfig(voice="v"), communicate_factory=fake).synthesize("x", "id")

    assert audio.ndim == 1


def test_edge_tts_raises_tts_error_when_no_audio_arrives():
    fake = FakeCommunicate([{"type": "SentenceBoundary", "offset": 0.0, "duration": 1.0}])

    with pytest.raises(TtsError):
        EdgeTts(EdgeVoiceConfig(voice="v"), communicate_factory=fake).synthesize("Halo", "id")


def test_edge_tts_turns_service_errors_into_tts_error():
    fake = FakeCommunicate(error=NoAudioReceived("No audio was received."))

    with pytest.raises(TtsError):
        EdgeTts(EdgeVoiceConfig(voice="v"), communicate_factory=fake).synthesize("Halo", "id")


def test_edge_tts_works_when_called_from_inside_a_running_event_loop():
    fake = FakeCommunicate(mp3_chunks(tone(0.3, 24000), 24000))
    tts = EdgeTts(EdgeVoiceConfig(voice="v"), communicate_factory=fake)

    async def call_synchronously():
        return tts.synthesize("Halo", "id")

    audio, sample_rate = asyncio.run(call_synchronously())

    assert sample_rate == 24000
    assert len(audio) > 0


# --- GptSovitsTts ------------------------------------------------------------


def sovits_voice(**overrides) -> GptSovitsVoiceConfig:
    fields = {
        "url": "http://127.0.0.1:9880/",
        "ref_audio_path": "D:/voices/cyrene_ref.wav",
        "prompt_text": "参照音声の文",
        "prompt_lang": "ja",
        "speed_factor": 1.1,
    }
    return GptSovitsVoiceConfig(**(fields | overrides))


def test_gpt_sovits_requests_the_tts_endpoint_with_the_reference_voice():
    requests: list[tuple[str, float]] = []

    def http_get(url, timeout):
        requests.append((url, timeout))
        return encode(tone(0.25, 32000), 32000, "WAV")

    audio, sample_rate = GptSovitsTts(sovits_voice(), http_get=http_get).synthesize("はい", "ja")

    [(url, timeout)] = requests
    parsed = urllib.parse.urlsplit(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "http://127.0.0.1:9880/tts"
    assert dict(urllib.parse.parse_qsl(parsed.query)) == {
        "text": "はい",
        "text_lang": "ja",
        "ref_audio_path": "D:/voices/cyrene_ref.wav",
        "prompt_text": "参照音声の文",
        "prompt_lang": "ja",
        "speed_factor": "1.1",
        "media_type": "wav",
    }
    assert timeout == 60
    assert sample_rate == 32000
    assert audio.dtype == np.float32
    assert len(audio) == 8000


def test_gpt_sovits_unreachable_server_raises_tts_error_with_a_hint():
    def http_get(url, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

    with pytest.raises(TtsError, match="api_v2"):
        GptSovitsTts(sovits_voice(), http_get=http_get).synthesize("はい", "ja")


def test_gpt_sovits_http_error_raises_tts_error():
    def http_get(url, timeout):
        raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)

    with pytest.raises(TtsError, match="400"):
        GptSovitsTts(sovits_voice(), http_get=http_get).synthesize("はい", "ja")


def test_gpt_sovits_unreadable_audio_raises_tts_error():
    def http_get(url, timeout):
        return b'{"message": "tts failed"}'

    with pytest.raises(TtsError):
        GptSovitsTts(sovits_voice(), http_get=http_get).synthesize("はい", "ja")


# --- SpeechRouter ------------------------------------------------------------


class FakeEngine(TextToSpeech):
    def __init__(self, samples: int = 100, sample_rate: int = 1000, fail: bool = False):
        self.samples = samples
        self.sample_rate = sample_rate
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]:
        self.calls.append((text, language))
        if self.fail:
            raise TtsError("engine down")
        return np.ones(self.samples, dtype=np.float32), self.sample_rate


def test_router_speaks_each_run_with_its_language_engine():
    indonesian, japanese = FakeEngine(), FakeEngine()
    router = SpeechRouter({"id": indonesian, "ja": japanese}, default_language="id")

    router.synthesize("Halo Master, おかえりなさい! Mau apa?")

    assert indonesian.calls == [("Halo Master,", "id"), ("Mau apa?", "id")]
    assert japanese.calls == [("おかえりなさい!", "ja")]


def test_router_falls_back_to_the_default_engine_for_a_language_without_one():
    indonesian = FakeEngine()
    router = SpeechRouter({"id": indonesian}, default_language="id")

    router.synthesize("Halo, おかえり")

    assert indonesian.calls == [("Halo,", "id"), ("おかえり", "ja")]


def test_router_joins_runs_with_120_ms_of_silence():
    router = SpeechRouter({"id": FakeEngine(), "ja": FakeEngine()}, default_language="id")

    audio, sample_rate = router.synthesize("Halo, はい, Master")

    assert sample_rate == 1000
    assert len(audio) == 100 + 120 + 100 + 120 + 100
    assert np.all(audio[100:220] == 0)
    assert np.all(audio[320:440] == 0)
    assert np.all(audio[:100] == 1)


def test_router_resamples_every_run_to_the_first_runs_rate():
    indonesian = FakeEngine(samples=1000, sample_rate=1000)
    japanese = FakeEngine(samples=2000, sample_rate=2000)
    router = SpeechRouter({"id": indonesian, "ja": japanese}, default_language="id")

    audio, sample_rate = router.synthesize("Halo はい")

    assert sample_rate == 1000
    assert audio.dtype == np.float32
    assert len(audio) == 1000 + 120 + 1000
    assert np.allclose(audio[1120:], 1.0)


def test_router_cleans_text_before_speaking():
    indonesian = FakeEngine()
    router = SpeechRouter({"id": indonesian}, default_language="id")

    router.synthesize("*tersenyum* **Halo** Master 😊")

    assert indonesian.calls == [("Halo Master", "id")]


def test_router_skips_a_failing_run_and_speaks_the_rest():
    router = SpeechRouter({"id": FakeEngine(), "ja": FakeEngine(fail=True)}, default_language="id")

    audio, _ = router.synthesize("Halo, はい, Master")

    assert len(audio) == 100 + 120 + 100


def test_router_raises_tts_error_when_every_run_fails():
    router = SpeechRouter({"id": FakeEngine(fail=True)}, default_language="id")

    with pytest.raises(TtsError):
        router.synthesize("Halo Master")


def test_router_raises_tts_error_when_there_is_nothing_to_say():
    indonesian = FakeEngine()
    router = SpeechRouter({"id": indonesian}, default_language="id")

    with pytest.raises(TtsError):
        router.synthesize("😊 *tersenyum*")
    assert indonesian.calls == []


def test_router_requires_an_engine_for_the_default_language():
    with pytest.raises(ValueError, match="id"):
        SpeechRouter({"ja": FakeEngine()}, default_language="id")


# --- build_speech_router -----------------------------------------------------


def test_build_speech_router_uses_edge_voices_from_the_default_config():
    router = build_speech_router(TtsConfig())

    assert router.default_language == "id"
    assert set(router.engines) == {"id", "ja"}
    assert all(isinstance(engine, EdgeTts) for engine in router.engines.values())


def test_build_speech_router_uses_gpt_sovits_where_configured():
    config = TtsConfig(
        voices={"id": EdgeVoiceConfig(voice="id-ID-GadisNeural"), "ja": sovits_voice()}
    )

    router = build_speech_router(config)

    assert isinstance(router.engines["id"], EdgeTts)
    assert isinstance(router.engines["ja"], GptSovitsTts)
