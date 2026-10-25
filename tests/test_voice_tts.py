import asyncio
import dataclasses
import http.client
import io
import json
import logging
import subprocess
import urllib.error
import urllib.parse

import aiohttp
import numpy as np
import pytest
import soundfile as sf
from edge_tts.exceptions import NoAudioReceived, SkewAdjustmentError
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from waifu.config import (
    EdgeVoiceConfig,
    FallbackPitchConfig,
    FallbackTtsConfig,
    GptSovitsVoiceConfig,
    RvcConfig,
    TtsConfig,
)
from waifu.voice.base import TextToSpeech
from waifu.voice.rvc import RvcClient, RvcError, RvcUnavailableError
from waifu.voice.tts import (
    EdgeTts,
    GptSovitsTts,
    SpeechRouter,
    TtsError,
    VoiceConverter,
    WindowsTts,
    build_speech_router,
    clean_for_speech,
    split_by_language,
)
from waifu.voice.windows_voices import WindowsVoice


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


EDGE_SERVICE_URL = URL(
    "wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1"
    "?TrustedClientToken=6A5AA1D4EAFF4E9FB37E23D68491D6F4"
)


def handshake_rejection(status: int) -> aiohttp.WSServerHandshakeError:
    """What aiohttp raises when the Edge service answers the websocket handshake with `status`."""
    request = aiohttp.RequestInfo(EDGE_SERVICE_URL, "GET", CIMultiDictProxy(CIMultiDict()))
    return aiohttp.WSServerHandshakeError(
        request, (), message="Invalid response status", status=status
    )


def edge_tts_error_message(error: Exception, voice: str = "id-ID-GadisNeural") -> str:
    fake = FakeCommunicate(error=error)

    with pytest.raises(TtsError) as caught:
        EdgeTts(EdgeVoiceConfig(voice=voice), communicate_factory=fake).synthesize("Sayang?", "id")
    return str(caught.value)


def test_edge_tts_refused_with_403_tells_the_owner_to_update_edge_tts_and_restart():
    # Oct 2026: Microsoft changed its request token and refused every edge-tts <= 7.2.8.
    message = edge_tts_error_message(handshake_rejection(403))

    assert r".venv\Scripts\python -m pip install -U edge-tts" in message
    assert "waifu run" in message


def test_edge_tts_refused_with_403_names_the_voice_microsoft_refused():
    message = edge_tts_error_message(handshake_rejection(403), voice="ja-JP-NanamiNeural")

    assert "Microsoft" in message
    assert "403" in message
    assert "ja-JP-NanamiNeural" in message


def test_edge_tts_refused_with_403_leaves_the_service_url_out_of_the_message():
    assert "wss://" not in edge_tts_error_message(handshake_rejection(403))


def test_edge_tts_refused_with_403_and_no_server_date_still_tells_the_owner_to_update():
    # edge-tts reads the server's Date to correct the clock before retrying a 403; without
    # one it raises SkewAdjustmentError chained from the refusal instead.
    try:
        raise SkewAdjustmentError("No server date in headers.") from handshake_rejection(403)
    except SkewAdjustmentError as error:
        message = edge_tts_error_message(error)

    assert r".venv\Scripts\python -m pip install -U edge-tts" in message


def test_edge_tts_other_handshake_failures_keep_the_plain_error():
    message = edge_tts_error_message(handshake_rejection(500), voice="id-ID-GadisNeural")

    assert message.startswith("Edge TTS gagal (id-ID-GadisNeural): 500")
    assert "pip install" not in message


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


def test_gpt_sovits_reply_cut_off_mid_audio_raises_tts_error():
    def http_get(url, timeout):
        raise http.client.IncompleteRead(b"RIFF")  # not an OSError

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


# --- WindowsTts --------------------------------------------------------------

ANDIKA = WindowsVoice("Microsoft Andika", "id-ID", "Male", "onecore")
ZIRA = WindowsVoice("Microsoft Zira", "en-US", "Female", "onecore")
DAVID = WindowsVoice("Microsoft David", "en-US", "Male", "onecore")
HARUKA = WindowsVoice("Microsoft Haruka", "ja-JP", "Female", "onecore")
# Not the config defaults (11, 18), so a WindowsTts that ignored its pitches would show.
PITCHES = FallbackPitchConfig(female=7, male=15)


class FakePowerShell:
    """Stands in for subprocess.run of windows_voices.ps1: `-List` answers with `voices` (or
    fails with `list_returncode`), speaking answers with `wav` (or fails with
    `speak_returncode`). Remembers how often it listed and what each voice spoke."""

    def __init__(
        self,
        voices: tuple[WindowsVoice, ...] = (ZIRA, DAVID, ANDIKA),
        *,
        wav: bytes | None = None,
        list_returncode: int = 0,
        speak_returncode: int = 0,
    ) -> None:
        self.voices = voices
        self.wav = encode(tone(0.5, 16000), 16000, "WAV") if wav is None else wav
        self.list_returncode = list_returncode
        self.speak_returncode = speak_returncode
        self.listings = 0
        self.spoken: list[tuple[str, str]] = []

    def __call__(self, args: list[str], **kwargs) -> subprocess.CompletedProcess[bytes]:
        if args[-1] == "-List":
            self.listings += 1
            listing = "".join(json.dumps(dataclasses.asdict(voice)) + "\n" for voice in self.voices)
            return subprocess.CompletedProcess(
                args, self.list_returncode, listing.encode(), b"listing failed\r\n"
            )
        self.spoken.append((kwargs["env"]["WAIFU_TTS_VOICE"], kwargs["input"].decode("utf-8")))
        return subprocess.CompletedProcess(
            args, self.speak_returncode, self.wav, b"synthesis failed\r\n"
        )


def windows_tts(run: FakePowerShell, language: str = "id", wanted: str = "") -> WindowsTts:
    return WindowsTts(language, wanted, pitches=PITCHES, run=run)


def test_windows_tts_starts_no_powershell_when_it_is_built():
    run = FakePowerShell()

    tts = windows_tts(run)

    assert (run.listings, run.spoken) == (0, [])
    assert tts.voice is None and tts.rvc_pitch is None


def test_windows_tts_speaks_with_the_installed_voice_of_its_language():
    run = FakePowerShell()

    audio, sample_rate = windows_tts(run).synthesize("Halo Master", "id")

    assert run.spoken == [("Microsoft Andika", "Halo Master")]
    assert sample_rate == 16000
    assert audio.dtype == np.float32 and audio.ndim == 1 and len(audio) == 8000


def test_windows_tts_lists_the_voices_only_once():
    run = FakePowerShell()
    tts = windows_tts(run)

    tts.synthesize("Halo", "id")
    tts.synthesize("Master", "id")

    assert run.listings == 1
    assert tts.voice == ANDIKA


def test_windows_tts_speaks_with_a_named_voice_of_another_language():
    # The owner's stopgap until the Indonesian voice is added.
    run = FakePowerShell()

    windows_tts(run, wanted="Zira").synthesize("Halo", "id")

    assert run.spoken == [("Microsoft Zira", "Halo")]


def test_windows_tts_without_a_voice_of_its_language_says_where_to_add_one():
    run = FakePowerShell(voices=(ZIRA, DAVID))
    tts = windows_tts(run, language="ja")

    for _ in range(2):
        with pytest.raises(TtsError) as caught:
            tts.synthesize("はい", "ja")

    assert str(caught.value) == (
        "Belum ada suara Windows bahasa Jepang; tambahkan gratis di Settings → "
        "Time & language → Speech → Add voices (Japanese)."
    )
    assert run.listings == 1  # not again on every reply while edge-tts is down
    assert run.spoken == []


def test_windows_tts_with_a_named_voice_that_is_not_installed_says_so():
    with pytest.raises(TtsError) as caught:
        windows_tts(FakePowerShell(), wanted="Microsoft Gadis").synthesize("Halo", "id")

    assert str(caught.value) == (
        "Suara Windows 'Microsoft Gadis' (tts.fallback.voices.id) tidak terpasang; "
        "lihat 'waifu voices'."
    )


def test_windows_tts_lists_the_voices_again_after_a_failed_listing():
    run = FakePowerShell(list_returncode=1)
    tts = windows_tts(run)

    with pytest.raises(TtsError, match="listing failed"):
        tts.synthesize("Halo", "id")
    run.list_returncode = 0
    tts.synthesize("Halo", "id")

    assert run.listings == 2
    assert run.spoken == [("Microsoft Andika", "Halo")]


def test_windows_tts_turns_a_failed_synthesis_into_a_tts_error():
    with pytest.raises(TtsError, match="synthesis failed"):
        windows_tts(FakePowerShell(speak_returncode=1)).synthesize("Halo", "id")


def test_windows_tts_turns_unreadable_audio_into_a_tts_error():
    with pytest.raises(TtsError, match="Microsoft Andika"):
        windows_tts(FakePowerShell(wav=b"not a wav")).synthesize("Halo", "id")


def test_windows_tts_refuses_a_clip_without_sound():
    # Windows answers empty text with a valid WAV of 0 s.
    empty = encode(np.zeros(0, dtype=np.float32), 16000, "WAV")

    with pytest.raises(TtsError, match="Microsoft Andika"):
        windows_tts(FakePowerShell(wav=empty)).synthesize(" ", "id")


@pytest.mark.parametrize(
    ("voice", "pitch"),
    [
        (HARUKA, 7),
        (WindowsVoice("Microsoft Ichiro", "ja-JP", "Male", "onecore"), 15),
        (WindowsVoice("Microsoft Aoi", "ja-JP", "Neutral", "onecore"), None),
    ],
    ids=["female", "male", "other"],
)
def test_windows_tts_shifts_rvc_by_the_pitch_of_its_voice_s_gender(voice, pitch):
    tts = windows_tts(FakePowerShell(voices=(voice,)), language="ja")

    tts.synthesize("はい", "ja")

    assert tts.rvc_pitch == pitch


# --- SpeechRouter ------------------------------------------------------------


class FakeEngine(TextToSpeech):
    def __init__(
        self,
        samples: int = 100,
        sample_rate: int = 1000,
        fail: bool = False,
        error: str = "engine down",
    ):
        self.samples = samples
        self.sample_rate = sample_rate
        self.fail = fail
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]:
        self.calls.append((text, language))
        if self.fail:
            raise TtsError(self.error)
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


def test_router_joins_runs_at_their_highest_rate_so_no_run_is_downsampled():
    # A 16 kHz Windows voice may open a reply whose next run is 24 kHz edge-tts again; plain
    # interpolation down to 16 kHz would alias that run.
    indonesian = FakeEngine(samples=1000, sample_rate=1000)
    japanese = FakeEngine(samples=2000, sample_rate=2000)
    router = SpeechRouter({"id": indonesian, "ja": japanese}, default_language="id")

    audio, sample_rate = router.synthesize("Halo はい")

    assert sample_rate == 2000
    assert audio.dtype == np.float32
    assert len(audio) == 2000 + 240 + 2000
    assert np.all(audio[2000:2240] == 0)
    assert np.all(audio[2240:] == 1.0)  # the 2 kHz run as it came


def test_router_cleans_text_before_speaking():
    indonesian = FakeEngine()
    router = SpeechRouter({"id": indonesian}, default_language="id")

    router.synthesize("*tersenyum* **Halo** Master 😊")

    assert indonesian.calls == [("Halo Master", "id")]


def test_router_skips_a_failing_run_and_speaks_the_rest():
    router = SpeechRouter({"id": FakeEngine(), "ja": FakeEngine(fail=True)}, default_language="id")

    audio, _ = router.synthesize("Halo, はい, Master")

    assert len(audio) == 100 + 120 + 100


def test_a_skipped_run_is_logged_without_what_she_was_saying(caplog):
    # The warning reaches logs\waifu.log under autostart; her replies may hold Master's money.
    router = SpeechRouter({"id": FakeEngine(fail=True), "ja": FakeEngine()}, default_language="id")

    with caplog.at_level(logging.DEBUG, logger="waifu.voice.tts"):
        router.synthesize("Saldo GoPay-mu dua ratus lima puluh ribu, はい")

    [warning] = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert warning.getMessage() == "Skipping id speech run of 41 characters: engine down"
    assert "GoPay" not in caplog.text


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


# --- SpeechRouter voice conversion -------------------------------------------


class FakeConverter:
    """Stands in for RvcClient: records each call and answers from a script.

    Replies are used in order and the last one repeats; an exception reply is raised.
    """

    def __init__(self, *replies: tuple[np.ndarray, int] | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[np.ndarray, int]] = []
        self.pitches: list[int | None] = []

    def convert(
        self, audio: np.ndarray, sample_rate: int, *, pitch: int | None = None
    ) -> tuple[np.ndarray, int]:
        self.calls.append((audio.copy(), sample_rate))
        self.pitches.append(pitch)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


CONVERTED = (np.full(500, 0.5, dtype=np.float32), 40000)
MIXED_TEXT = "Halo, はい, Master"


def plain_router() -> SpeechRouter:
    return SpeechRouter({"id": FakeEngine(), "ja": FakeEngine()}, default_language="id")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def converting_router(converter: VoiceConverter, clock: FakeClock | None = None) -> SpeechRouter:
    return SpeechRouter(
        {"id": FakeEngine(), "ja": FakeEngine()},
        default_language="id",
        converter=converter,
        clock=clock or FakeClock(),
    )


def conversion_logs(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "waifu.voice.tts" and record.levelno == level
    ]


def test_router_converts_the_joined_audio_once():
    converter = FakeConverter(CONVERTED)
    plain_audio, plain_rate = plain_router().synthesize(MIXED_TEXT)

    converting_router(converter).synthesize(MIXED_TEXT)

    [(sent_audio, sent_rate)] = converter.calls
    assert sent_rate == plain_rate
    assert np.array_equal(sent_audio, plain_audio)


def test_router_returns_the_converted_audio_at_the_converters_rate():
    audio, sample_rate = converting_router(FakeConverter(CONVERTED)).synthesize(MIXED_TEXT)

    assert sample_rate == 40000
    assert np.array_equal(audio, CONVERTED[0])


@pytest.mark.parametrize(
    "error",
    [
        RvcError("Server suara RVC tidak bisa dihubungi"),
        ConnectionRefusedError(10061, "refused"),
        TimeoutError("timed out"),
    ],
    ids=["rvc-error", "os-error", "timeout"],
)
def test_router_speaks_the_unconverted_audio_when_conversion_fails(error):
    plain_audio, plain_rate = plain_router().synthesize(MIXED_TEXT)

    audio, sample_rate = converting_router(FakeConverter(error)).synthesize(MIXED_TEXT)

    assert sample_rate == plain_rate
    assert np.array_equal(audio, plain_audio)


def test_router_warns_once_per_conversion_failure_streak(caplog):
    router = converting_router(FakeConverter(RvcError("server mati")))

    with caplog.at_level(logging.DEBUG, logger="waifu.voice.tts"):
        router.synthesize(MIXED_TEXT)
        router.synthesize(MIXED_TEXT)

    [warning] = conversion_logs(caplog, logging.WARNING)
    assert "server mati" in warning
    assert len(conversion_logs(caplog, logging.DEBUG)) == 1


def test_router_logs_when_conversion_works_again_and_warns_on_a_new_streak(caplog):
    converter = FakeConverter(RvcError("mati"), RvcError("mati"), CONVERTED, RvcError("lagi"))
    router = converting_router(converter)

    with caplog.at_level(logging.DEBUG, logger="waifu.voice.tts"):
        router.synthesize(MIXED_TEXT)
        router.synthesize(MIXED_TEXT)
        caplog.clear()
        audio, sample_rate = router.synthesize(MIXED_TEXT)
        recovery = conversion_logs(caplog, logging.INFO)
        router.synthesize(MIXED_TEXT)

    assert sample_rate == 40000
    assert np.array_equal(audio, CONVERTED[0])
    assert len(recovery) == 1
    [warning] = conversion_logs(caplog, logging.WARNING)
    assert "lagi" in warning


def test_router_logs_nothing_while_conversion_keeps_working(caplog):
    router = converting_router(FakeConverter(CONVERTED))

    with caplog.at_level(logging.DEBUG, logger="waifu.voice.tts"):
        router.synthesize(MIXED_TEXT)
        router.synthesize(MIXED_TEXT)

    assert [record for record in caplog.records if record.name == "waifu.voice.tts"] == []


UNREACHABLE = [
    RvcUnavailableError("Server suara RVC tidak bisa dihubungi"),
    ConnectionRefusedError(10061, "refused"),
    TimeoutError("timed out"),
]


@pytest.mark.parametrize("error", UNREACHABLE, ids=["rvc-unavailable", "refused", "timeout"])
def test_router_skips_conversion_for_a_minute_after_the_converter_is_unreachable(error, caplog):
    # Each attempt at a dead server costs ~2 s on Windows (30 s at a stuck one) while
    # the mic waits for her to finish speaking.
    clock = FakeClock()
    converter = FakeConverter(error)
    router = converting_router(converter, clock)
    plain_audio, _ = plain_router().synthesize(MIXED_TEXT)

    with caplog.at_level(logging.DEBUG, logger="waifu.voice.tts"):
        router.synthesize(MIXED_TEXT)
        clock.now += 59.9
        audio, _ = router.synthesize(MIXED_TEXT)

    assert len(converter.calls) == 1
    assert np.array_equal(audio, plain_audio)
    assert len(conversion_logs(caplog, logging.WARNING)) == 1


def test_router_tries_the_converter_again_after_the_pause():
    clock = FakeClock()
    converter = FakeConverter(RvcUnavailableError("mati"), CONVERTED)
    router = converting_router(converter, clock)

    router.synthesize(MIXED_TEXT)
    clock.now += 60
    audio, sample_rate = router.synthesize(MIXED_TEXT)

    assert len(converter.calls) == 2
    assert (sample_rate, np.array_equal(audio, CONVERTED[0])) == (40000, True)


def test_router_pauses_again_when_the_retry_finds_the_converter_still_unreachable():
    clock = FakeClock()
    converter = FakeConverter(RvcUnavailableError("mati"))
    router = converting_router(converter, clock)

    router.synthesize(MIXED_TEXT)
    clock.now += 60
    router.synthesize(MIXED_TEXT)
    clock.now += 30
    router.synthesize(MIXED_TEXT)

    assert len(converter.calls) == 2


def test_router_converts_every_reply_again_once_a_retry_succeeds(caplog):
    clock = FakeClock()
    converter = FakeConverter(RvcUnavailableError("mati"), CONVERTED)
    router = converting_router(converter, clock)

    with caplog.at_level(logging.INFO, logger="waifu.voice.tts"):
        router.synthesize(MIXED_TEXT)
        clock.now += 60
        router.synthesize(MIXED_TEXT)
        audio, _ = router.synthesize(MIXED_TEXT)

    assert len(converter.calls) == 3
    assert np.array_equal(audio, CONVERTED[0])
    assert len(conversion_logs(caplog, logging.INFO)) == 1


def test_router_keeps_trying_a_converter_that_answers_with_an_error():
    # A server that answered (HTTP 500 for one clip, say) costs no extra wait.
    converter = FakeConverter(RvcError("Server suara RVC gagal mengubah suara (HTTP 500)"))
    router = converting_router(converter)

    router.synthesize(MIXED_TEXT)
    router.synthesize(MIXED_TEXT)

    assert len(converter.calls) == 2


def test_router_speaks_the_plain_voice_when_the_rvc_server_is_unreachable():
    def refuse(url, data, headers, timeout):
        raise urllib.error.URLError(ConnectionRefusedError(10061, "refused"))

    client = RvcClient(RvcConfig(), http_post=refuse, http_get=refuse)
    plain_audio, _ = plain_router().synthesize(MIXED_TEXT)

    audio, _ = converting_router(client).synthesize(MIXED_TEXT)

    assert np.array_equal(audio, plain_audio)


# --- SpeechRouter offline fallback -------------------------------------------

REFUSED = "Microsoft menolak suara id-ID-GadisNeural (403); perbarui edge-tts."
NO_WINDOWS_VOICE = "Belum ada suara Windows bahasa Indonesia."


class FakeFallback(TextToSpeech):
    """Stands in for WindowsTts: speaks 50 samples of 0.25 at 1 kHz with the RVC pitch of its
    voice, or fails with NO_WINDOWS_VOICE."""

    def __init__(self, rvc_pitch: int | None = None, fail: bool = False) -> None:
        self.rvc_pitch = rvc_pitch
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, language: str) -> tuple[np.ndarray, int]:
        self.calls.append((text, language))
        if self.fail:
            raise TtsError(NO_WINDOWS_VOICE)
        return np.full(50, 0.25, dtype=np.float32), 1000


@dataclasses.dataclass
class FallbackSetup:
    router: SpeechRouter
    primary: FakeEngine
    fallback: FakeFallback
    clock: FakeClock


def falling_back(
    *,
    fallback: FakeFallback | None = None,
    converter: VoiceConverter | None = None,
    **router_options,
) -> FallbackSetup:
    """A router whose Indonesian voice is down (Japanese works) with an Indonesian fallback."""
    primary = FakeEngine(fail=True, error=REFUSED)
    fallback = fallback or FakeFallback()
    clock = FakeClock()
    router = SpeechRouter(
        {"id": primary, "ja": FakeEngine()},
        default_language="id",
        fallbacks={"id": fallback},
        converter=converter,
        clock=clock,
        **router_options,
    )
    return FallbackSetup(router, primary, fallback, clock)


def test_a_run_whose_voice_fails_is_spoken_by_its_fallback():
    setup = falling_back()

    audio, sample_rate = setup.router.synthesize("Halo, はい")

    assert setup.primary.calls == [("Halo,", "id")]
    assert setup.fallback.calls == [("Halo,", "id")]
    assert sample_rate == 1000
    assert len(audio) == 50 + 120 + 100
    assert np.all(audio[:50] == 0.25)


def test_a_failing_language_without_a_fallback_is_skipped_as_before():
    japanese = FakeEngine(fail=True)
    router = SpeechRouter(
        {"id": FakeEngine(), "ja": japanese},
        default_language="id",
        fallbacks={"id": FakeFallback()},
        clock=FakeClock(),
    )

    audio, _ = router.synthesize("Halo, はい")

    assert japanese.calls == [("はい", "ja")]
    assert len(audio) == 100


def test_a_working_voice_never_wakes_its_fallback():
    fallback = FakeFallback()
    router = SpeechRouter(
        {"id": FakeEngine()}, default_language="id", fallbacks={"id": fallback}, clock=FakeClock()
    )

    router.synthesize("Halo Master")

    assert fallback.calls == []


def test_after_a_failure_the_fallback_speaks_for_ten_minutes_without_trying_the_voice():
    # edge-tts may take its 10 s connect timeout to fail on a flaky network, on every run.
    setup = falling_back()

    setup.router.synthesize("Halo")
    setup.clock.now += 599.9
    setup.router.synthesize("Master")

    assert setup.primary.calls == [("Halo", "id")]
    assert setup.fallback.calls == [("Halo", "id"), ("Master", "id")]


def test_the_voice_is_tried_again_after_the_pause():
    setup = falling_back()

    setup.router.synthesize("Halo")
    setup.clock.now += 600
    setup.primary.fail = False
    audio, _ = setup.router.synthesize("Master")

    assert len(setup.primary.calls) == 2
    assert setup.fallback.calls == [("Halo", "id")]
    assert np.all(audio == 1)


def test_the_pause_starts_again_when_the_retried_voice_still_fails():
    setup = falling_back()

    setup.router.synthesize("Halo")
    setup.clock.now += 600
    setup.router.synthesize("Halo")
    setup.clock.now += 300
    setup.router.synthesize("Halo")

    assert len(setup.primary.calls) == 2
    assert len(setup.fallback.calls) == 3


def test_the_pause_is_configurable():
    setup = falling_back(fallback_retry_s=60)

    setup.router.synthesize("Halo")
    setup.clock.now += 60
    setup.router.synthesize("Halo")

    assert len(setup.primary.calls) == 2


def test_when_the_fallback_fails_too_the_error_keeps_the_voice_s_hint():
    setup = falling_back(fallback=FakeFallback(fail=True))

    with pytest.raises(TtsError) as caught:
        setup.router.synthesize("Halo")

    assert str(caught.value) == (
        "Semua bagian teks gagal diucapkan: Microsoft menolak suara id-ID-GadisNeural (403); "
        "perbarui edge-tts. Suara cadangan Windows juga gagal: Belum ada suara Windows bahasa "
        "Indonesia."
    )


def test_a_fallback_that_cannot_speak_leaves_the_voice_tried_on_every_reply():
    # A missing Windows voice must never keep a recovered edge-tts away for ten minutes.
    setup = falling_back(fallback=FakeFallback(fail=True))

    for _ in range(2):
        with pytest.raises(TtsError):
            setup.router.synthesize("Halo")

    assert len(setup.primary.calls) == 2


def test_a_fallback_failing_during_the_pause_ends_the_pause():
    setup = falling_back()
    setup.router.synthesize("Halo")
    setup.fallback.fail = True

    with pytest.raises(TtsError, match="403.*Suara cadangan Windows juga gagal"):
        setup.router.synthesize("Halo")
    setup.primary.fail = False
    audio, _ = setup.router.synthesize("Halo")

    assert len(setup.primary.calls) == 2
    assert np.all(audio == 1)


def router_logs(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "waifu.voice.tts" and record.levelno == level
    ]


def test_the_fallback_warns_once_per_outage_without_her_words(caplog):
    setup = falling_back()

    with caplog.at_level(logging.DEBUG, logger="waifu.voice.tts"):
        setup.router.synthesize("Saldo GoPay-mu dua ratus lima puluh ribu")
        setup.router.synthesize("Halo")
        setup.clock.now += 600
        setup.router.synthesize("Halo")

    [warning] = router_logs(caplog, logging.WARNING)
    assert "id" in warning and REFUSED in warning
    assert "GoPay" not in caplog.text


def test_the_fallback_says_once_when_the_voice_works_again_and_warns_on_a_new_outage(caplog):
    setup = falling_back()

    with caplog.at_level(logging.INFO, logger="waifu.voice.tts"):
        setup.router.synthesize("Halo")
        setup.clock.now += 600
        setup.primary.fail = False
        setup.router.synthesize("Halo")
        setup.router.synthesize("Halo")
        recovered = router_logs(caplog, logging.INFO)
        setup.primary.fail = True
        setup.router.synthesize("Halo")

    assert len(recovered) == 1 and "id" in recovered[0]
    assert len(router_logs(caplog, logging.WARNING)) == 2


def test_a_fallback_clip_is_converted_with_its_voice_s_pitch():
    converter = FakeConverter(CONVERTED)

    falling_back(fallback=FakeFallback(rvc_pitch=18), converter=converter).router.synthesize("Halo")

    assert converter.pitches == [18]


def test_a_reply_in_one_voice_is_converted_once_with_the_configured_pitch():
    converter = FakeConverter(CONVERTED)

    converting_router(converter).synthesize(MIXED_TEXT)

    assert converter.pitches == [None]


def test_runs_of_different_pitches_are_converted_apart_and_joined_with_the_gap():
    converter = FakeConverter(CONVERTED)
    setup = falling_back(fallback=FakeFallback(rvc_pitch=18), converter=converter)

    audio, sample_rate = setup.router.synthesize(MIXED_TEXT)  # id (fallback), ja, id (fallback)

    assert converter.pitches == [18, None, 18]
    assert [len(sent) for sent, _ in converter.calls] == [50, 100, 50]
    gap = round(40000 * 0.12)
    assert sample_rate == 40000
    assert len(audio) == 3 * 500 + 2 * gap
    assert np.all(audio[500 : 500 + gap] == 0)


def test_neighbouring_runs_of_one_pitch_are_converted_together():
    converter = FakeConverter(CONVERTED)
    router = SpeechRouter(
        {"id": FakeEngine(fail=True), "ja": FakeEngine(fail=True)},
        default_language="id",
        fallbacks={"id": FakeFallback(rvc_pitch=11), "ja": FakeFallback(rvc_pitch=11)},
        converter=converter,
        clock=FakeClock(),
    )

    router.synthesize("Halo, はい")

    [(sent, _)] = converter.calls
    assert len(sent) == 50 + 120 + 50
    assert converter.pitches == [11]


def test_fallback_only_speaks_every_run_with_its_fallback_without_trying_the_voice():
    indonesian, japanese = FakeEngine(), FakeEngine()
    id_fallback, ja_fallback = FakeFallback(), FakeFallback()
    router = SpeechRouter(
        {"id": indonesian, "ja": japanese},
        default_language="id",
        fallbacks={"id": id_fallback, "ja": ja_fallback},
        fallback_only=True,
        clock=FakeClock(),
    )

    router.synthesize("Halo, はい")

    assert (indonesian.calls, japanese.calls) == ([], [])
    assert id_fallback.calls == [("Halo,", "id")] and ja_fallback.calls == [("はい", "ja")]


def test_fallback_only_has_nothing_for_a_language_without_a_fallback():
    router = SpeechRouter(
        {"id": FakeEngine(), "ja": FakeEngine()},
        default_language="id",
        fallbacks={"id": FakeFallback()},
        fallback_only=True,
        clock=FakeClock(),
    )

    with pytest.raises(TtsError, match="Belum ada suara cadangan untuk bahasa Jepang"):
        router.synthesize("はい")


# --- SpeechRouter stand-in voice (respons instan) ----------------------------


def test_her_voice_is_not_degraded_while_it_works():
    router = converting_router(FakeConverter(CONVERTED))

    before = router.degraded
    router.synthesize(MIXED_TEXT)

    assert (before, router.degraded) == (False, False)


def test_her_voice_is_degraded_while_a_fallback_speaks_for_a_failing_voice():
    setup = falling_back()

    setup.router.synthesize("Halo")
    after_failure = setup.router.degraded
    setup.clock.now += 300
    setup.router.synthesize("Halo")  # the fallback speaks without trying the voice

    assert (after_failure, setup.router.degraded) == (True, True)


def test_her_voice_is_no_longer_degraded_once_the_voice_works_again():
    setup = falling_back()
    setup.router.synthesize("Halo")

    setup.clock.now += 600
    setup.primary.fail = False
    setup.router.synthesize("Halo")

    assert setup.router.degraded is False


def test_her_voice_is_degraded_while_the_converter_fails_until_it_converts_again():
    clock = FakeClock()
    router = converting_router(FakeConverter(RvcUnavailableError("mati"), CONVERTED), clock)

    router.synthesize(MIXED_TEXT)
    failing = router.degraded
    clock.now += 30
    router.synthesize(MIXED_TEXT)  # the plain voice without trying the converter
    skipping = router.degraded
    clock.now += 60
    router.synthesize(MIXED_TEXT)

    assert (failing, skipping, router.degraded) == (True, True, False)


def test_her_voice_is_never_degraded_when_only_the_fallback_is_wanted():
    router = SpeechRouter(
        {"id": FakeEngine(fail=True)},
        default_language="id",
        fallbacks={"id": FakeFallback()},
        fallback_only=True,
        clock=FakeClock(),
    )

    router.synthesize("Halo")

    assert router.degraded is False


# --- SpeechRouter pronunciation (kamus cyrene) -------------------------------


class CountingLexicon:
    """Master's rules as `Lexicon.readings` gives them, counting the reads."""

    def __init__(self, rules: dict[str, str]) -> None:
        self.rules = rules
        self.reads = 0

    def __call__(self) -> dict[str, str]:
        self.reads += 1
        return dict(self.rules)


def test_an_indonesian_run_is_read_with_the_built_in_rules_without_a_lexicon():
    indonesian, japanese = FakeEngine(), FakeEngine()
    router = SpeechRouter({"id": indonesian, "ja": japanese}, default_language="id")

    router.synthesize("Pity HSR-mu 70, Zzz... HSRX belum. HSRはいいね")

    assert indonesian.calls == [("Pity Ha Es Er-mu 70, Zzz... HSRX belum. Ha Es Er", "id")]
    assert japanese.calls == [("はいいね", "ja")]


def test_masters_rules_reach_the_indonesian_runs_and_are_read_once_per_reply():
    indonesian = FakeEngine()
    lexicon = CountingLexicon({"Acheron": "A Ke Ron"})
    router = SpeechRouter(
        {"id": indonesian, "ja": FakeEngine()}, default_language="id", lexicon=lexicon
    )

    router.synthesize("acheron, はい, Acheron lagi")

    assert indonesian.calls == [("A Ke Ron,", "id"), ("A Ke Ron lagi", "id")]
    assert lexicon.reads == 1


def test_a_japanese_run_is_never_rewritten():
    japanese = FakeEngine()
    router = SpeechRouter(
        {"id": FakeEngine(), "ja": japanese},
        default_language="id",
        lexicon=lambda: {"おかえり": "okaeri"},
    )

    router.synthesize("Halo, おかえりなさい")

    assert japanese.calls == [("おかえりなさい", "ja")]


def test_the_offline_fallback_speaks_the_rewritten_run():
    setup = falling_back(lexicon=lambda: {"Kafka": "Kaf Ka"})

    setup.router.synthesize("HSR dan Kafka, はい")

    assert setup.primary.calls == [("Ha Es Er dan Kaf Ka,", "id")]
    assert setup.fallback.calls == [("Ha Es Er dan Kaf Ka,", "id")]


def test_fallback_only_speaks_the_rewritten_run():
    id_fallback, ja_fallback = FakeFallback(), FakeFallback()
    router = SpeechRouter(
        {"id": FakeEngine(), "ja": FakeEngine()},
        default_language="id",
        fallbacks={"id": id_fallback, "ja": ja_fallback},
        fallback_only=True,
        clock=FakeClock(),
    )

    router.synthesize("Cek HoYoLAB, はい")

    assert id_fallback.calls == [("Cek Hoyolab,", "id")]
    assert ja_fallback.calls == [("はい", "ja")]


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


def test_build_speech_router_passes_the_converter_through():
    converter = FakeConverter(CONVERTED)

    assert build_speech_router(TtsConfig(), converter=converter).converter is converter


def test_build_speech_router_has_no_converter_by_default():
    assert build_speech_router(TtsConfig()).converter is None


def test_build_speech_router_passes_masters_rules_through():
    def lexicon() -> dict[str, str]:
        return {"Kafka": "Kaf Ka"}

    assert build_speech_router(TtsConfig(), lexicon=lexicon).lexicon is lexicon


def test_build_speech_router_reads_no_rules_file_by_itself():
    assert build_speech_router(TtsConfig()).lexicon is None


def test_build_speech_router_gives_each_configured_language_a_windows_fallback():
    # Building one starts no PowerShell: tests/conftest.py would refuse it.
    router = build_speech_router(TtsConfig())

    assert set(router.fallbacks) == {"id", "ja"}
    assert all(isinstance(fallback, WindowsTts) for fallback in router.fallbacks.values())
    assert [fallback.language for fallback in router.fallbacks.values()] == ["id", "ja"]
    assert router.fallback_retry_s == 600
    assert router.fallback_only is False


def test_build_speech_router_passes_the_named_fallback_voice_and_its_pause():
    fallback = FallbackTtsConfig(voices={"id": "Microsoft Zira"}, retry_s=120)

    router = build_speech_router(TtsConfig(fallback=fallback))

    assert set(router.fallbacks) == {"id"}
    assert router.fallbacks["id"].wanted == "Microsoft Zira"
    assert router.fallback_retry_s == 120


def test_build_speech_router_converts_the_fallback_voices_with_the_configured_pitch(monkeypatch):
    pitches = FallbackPitchConfig(female=5, male=9)
    converter = FakeConverter(CONVERTED)
    config = TtsConfig(fallback=FallbackTtsConfig(rvc_pitch=pitches))
    router = build_speech_router(config, converter=converter, fallback_only=True)
    for fallback in router.fallbacks.values():  # the builder gives them the real PowerShell
        monkeypatch.setattr(fallback, "_run", FakePowerShell(voices=(ANDIKA, HARUKA)))

    router.synthesize("Halo, はい")

    assert converter.pitches == [9, 5]  # Andika is male, Haruka female


def test_build_speech_router_has_no_fallback_when_it_is_off():
    router = build_speech_router(TtsConfig(fallback=FallbackTtsConfig(enabled=False)))

    assert router.fallbacks == {}


def test_build_speech_router_can_speak_only_through_the_fallback():
    assert build_speech_router(TtsConfig(), fallback_only=True).fallback_only is True
