import logging
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from waifu.config import SttConfig
from waifu.voice import stt
from waifu.voice.base import SpeechToText, Transcript
from waifu.voice.stt import SttError, WhisperStt, register_cuda_dll_dirs

ONE_SECOND = np.zeros(16000, dtype=np.float32)


def segment(text: str, no_speech_prob: float = 0.1, avg_logprob: float = -0.3) -> SimpleNamespace:
    return SimpleNamespace(text=text, no_speech_prob=no_speech_prob, avg_logprob=avg_logprob)


class FakeWhisperModel:
    """Mimics faster_whisper.WhisperModel.transcribe: (lazy segments, info)."""

    def __init__(self, *runs: tuple[list[SimpleNamespace], str]) -> None:
        self._runs = list(runs)
        self.calls: list[dict] = []

    def transcribe(self, audio: np.ndarray, **kwargs) -> tuple:
        self.calls.append({"audio": audio, **kwargs})
        segments, language = self._runs[len(self.calls) - 1]
        return (s for s in segments), SimpleNamespace(language=language)


def make_stt(model: FakeWhisperModel, config: SttConfig | None = None, **kwargs) -> WhisperStt:
    kwargs.setdefault("register_dlls", lambda: [])
    return WhisperStt(config or SttConfig(), "Cyrene", model_factory=lambda cfg: model, **kwargs)


@pytest.fixture
def fresh_dll_registry(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    added: list[str] = []
    monkeypatch.setattr(stt, "_registered_dll_dirs", set())
    monkeypatch.setattr(os, "add_dll_directory", added.append, raising=False)
    monkeypatch.setenv("PATH", "C:\\Windows")
    return added


def test_transcribe_joins_stripped_segment_text_with_detected_language():
    model = FakeWhisperModel(([segment(" Cyrene, nyalakan"), segment(" lampu kamar. ")], "id"))

    result = make_stt(model).transcribe(ONE_SECOND, 16000)

    assert result == Transcript("Cyrene, nyalakan lampu kamar.", "id")


def test_transcribe_resamples_audio_to_16khz_float32():
    model = FakeWhisperModel(([segment("halo")], "id"))
    one_second_at_48k = np.sin(np.linspace(0, 100, 48000))

    make_stt(model).transcribe(one_second_at_48k, 48000)

    sent = model.calls[0]["audio"]
    assert sent.dtype == np.float32
    assert len(sent) == 16000


def test_transcribe_uses_auto_language_vad_and_a_prompt_with_the_assistant_name():
    model = FakeWhisperModel(([segment("halo")], "id"))

    make_stt(model, SttConfig(beam_size=3)).transcribe(ONE_SECOND, 16000)

    call = model.calls[0]
    assert call["language"] is None
    assert call["beam_size"] == 3
    assert call["vad_filter"] is True
    assert call["condition_on_previous_text"] is False
    assert call["initial_prompt"].startswith("Cyrene, ")


def test_allowed_detected_language_runs_the_model_once():
    model = FakeWhisperModel(([segment("おかえりなさい")], "ja"))

    result = make_stt(model).transcribe(ONE_SECOND, 16000)

    assert result == Transcript("おかえりなさい", "ja")
    assert len(model.calls) == 1


def test_disallowed_detected_language_reruns_with_the_first_configured_language():
    model = FakeWhisperModel(
        ([segment("Sirene, turn on the lamp")], "en"),
        ([segment("Cyrene, nyalakan lampu")], "id"),
    )

    result = make_stt(model, SttConfig(languages=["id", "ja"])).transcribe(ONE_SECOND, 16000)

    assert result == Transcript("Cyrene, nyalakan lampu", "id")
    assert [call["language"] for call in model.calls] == [None, "id"]


def test_both_decodes_pass_the_hotwords_of_that_moment_and_never_a_prefix():
    model = FakeWhisperModel(
        ([segment("Sirene, turn on the lamp")], "en"),
        ([segment("Cyrene, nyalakan lampu meja")], "id"),
    )
    hotwords = iter(["Cyrene, Lampu Meja", "Cyrene, Lampu Meja, Acheron"])

    make_stt(model, hotwords=lambda: next(hotwords)).transcribe(ONE_SECOND, 16000)

    assert [call["hotwords"] for call in model.calls] == [
        "Cyrene, Lampu Meja",
        "Cyrene, Lampu Meja, Acheron",
    ]
    # faster-whisper ignores hotwords when a prefix is set.
    assert all("prefix" not in call for call in model.calls)


def test_the_initial_prompt_stays_one_short_sentence_beside_the_hotwords():
    model = FakeWhisperModel(([segment("halo")], "id"))

    make_stt(model, hotwords=lambda: "Cyrene, Lampu Meja").transcribe(ONE_SECOND, 16000)

    assert model.calls[0]["initial_prompt"] == "Cyrene, tolong nyalakan lampu kamar."


@pytest.mark.parametrize("empty", [None, ""])
def test_no_hotwords_pass_none(empty: str | None):
    model = FakeWhisperModel(([segment("halo")], "id"))

    make_stt(model, hotwords=lambda: empty).transcribe(ONE_SECOND, 16000)

    assert model.calls[0]["hotwords"] is None


def test_without_a_vocabulary_whisper_gets_no_hotwords():
    model = FakeWhisperModel(([segment("halo")], "id"))

    make_stt(model).transcribe(ONE_SECOND, 16000)

    assert model.calls[0]["hotwords"] is None


@pytest.mark.parametrize(
    "hallucination",
    [
        "Terima kasih telah menonton!",
        "TERIMA KASIH SUDAH MENONTON.",
        "Sampai jumpa di video berikutnya...",
        "ご視聴ありがとうございました。",
        "チャンネル登録よろしくお願いします",
        "Subtitle by Rizky",
        "Subtitles by the Amara.org community",
        "Thanks for watching!",
    ],
)
def test_known_hallucination_yields_an_empty_transcript(hallucination: str):
    model = FakeWhisperModel(([segment(hallucination)], "id"))

    assert make_stt(model).transcribe(ONE_SECOND, 16000) == Transcript("", "id")


def test_hallucinated_segment_is_dropped_while_real_speech_is_kept():
    model = FakeWhisperModel(
        ([segment("Cyrene, matikan TV."), segment("Terima kasih telah menonton.")], "id")
    )

    assert make_stt(model).transcribe(ONE_SECOND, 16000).text == "Cyrene, matikan TV."


@pytest.mark.parametrize("noise", ["...", " ♪ ", "?!", ""])
def test_punctuation_only_text_yields_an_empty_transcript(noise: str):
    model = FakeWhisperModel(([segment(noise)], "id"))

    assert make_stt(model).transcribe(ONE_SECOND, 16000) == Transcript("", "id")


def test_segments_that_are_probably_silence_are_dropped():
    model = FakeWhisperModel(
        (
            [
                segment("hantu", no_speech_prob=0.9, avg_logprob=-1.5),
                segment("nyalakan", no_speech_prob=0.9, avg_logprob=-0.5),
                segment("lampu", no_speech_prob=0.2, avg_logprob=-1.5),
            ],
            "id",
        )
    )

    assert make_stt(model).transcribe(ONE_SECOND, 16000).text == "nyalakan lampu"


def test_very_short_audio_returns_empty_without_loading_the_model():
    def refuse_to_load(cfg: SttConfig) -> FakeWhisperModel:
        raise AssertionError("model must not load for a click")

    whisper = WhisperStt(SttConfig(), "Cyrene", model_factory=refuse_to_load)

    result = whisper.transcribe(np.zeros(4000, dtype=np.float32), 16000)

    assert result == Transcript("", "id")


def test_model_is_loaded_lazily_once_with_the_stt_config():
    config = SttConfig(model="small", device="cpu", compute_type="int8")
    loaded_with: list[SttConfig] = []
    model = FakeWhisperModel(([segment("satu")], "id"), ([segment("dua")], "id"))

    def factory(cfg: SttConfig) -> FakeWhisperModel:
        loaded_with.append(cfg)
        return model

    whisper = WhisperStt(config, "Cyrene", model_factory=factory, register_dlls=lambda: [])
    assert loaded_with == []

    whisper.transcribe(ONE_SECOND, 16000)
    whisper.transcribe(ONE_SECOND, 16000)

    assert loaded_with == [config]


def test_cuda_dll_dirs_are_registered_right_before_the_model_loads():
    order: list[str] = []
    model = FakeWhisperModel(([segment("halo")], "id"))

    def register() -> list[str]:
        order.append("register")
        return []

    def factory(cfg: SttConfig) -> FakeWhisperModel:
        order.append("load")
        return model

    whisper = WhisperStt(SttConfig(), "Cyrene", model_factory=factory, register_dlls=register)
    assert order == []

    whisper.transcribe(ONE_SECOND, 16000)

    assert order == ["register", "load"]


def recording_stt(order: list[str], model: FakeWhisperModel | None = None) -> WhisperStt:
    """A WhisperStt whose DLL registration and model load append to `order`."""

    def register() -> list[str]:
        order.append("register")
        return []

    def factory(cfg: SttConfig) -> FakeWhisperModel:
        order.append("load")
        return model or FakeWhisperModel()

    return WhisperStt(SttConfig(), "Cyrene", model_factory=factory, register_dlls=register)


def test_preload_registers_cuda_dlls_and_loads_the_model_before_any_audio():
    order: list[str] = []
    whisper = recording_stt(order)

    whisper.preload()

    assert order == ["register", "load"]


def test_preloading_twice_loads_the_model_once():
    order: list[str] = []
    whisper = recording_stt(order)

    whisper.preload()
    whisper.preload()

    assert order == ["register", "load"]


def test_transcribe_after_preload_reuses_the_preloaded_model():
    order: list[str] = []
    whisper = recording_stt(order, FakeWhisperModel(([segment("halo")], "id")))
    whisper.preload()

    result = whisper.transcribe(ONE_SECOND, 16000)

    assert result == Transcript("halo", "id")
    assert order == ["register", "load"]


def test_missing_cuda_library_during_preload_becomes_stt_error_naming_the_dll():
    def factory(cfg: SttConfig) -> FakeWhisperModel:
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    whisper = WhisperStt(SttConfig(), "Cyrene", model_factory=factory, register_dlls=lambda: [])

    with pytest.raises(SttError, match="cublas64_12.dll"):
        whisper.preload()


def test_speech_to_text_without_a_heavy_model_preloads_as_a_no_op():
    class CannedStt(SpeechToText):
        def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
            return Transcript("halo", "id")

    canned = CannedStt()

    canned.preload()

    assert canned.transcribe(ONE_SECOND, 16000) == Transcript("halo", "id")


def test_missing_cuda_library_at_load_becomes_stt_error_naming_the_dll(caplog):
    def factory(cfg: SttConfig) -> FakeWhisperModel:
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")

    whisper = WhisperStt(SttConfig(), "Cyrene", model_factory=factory, register_dlls=lambda: [])

    with caplog.at_level(logging.ERROR), pytest.raises(SttError, match="cublas64_12.dll"):
        whisper.transcribe(ONE_SECOND, 16000)
    assert "cublas64_12.dll" in caplog.text


def test_cuda_failure_while_decoding_becomes_stt_error():
    class CudnnBrokenModel(FakeWhisperModel):
        def transcribe(self, audio: np.ndarray, **kwargs) -> tuple:
            def segments():
                raise OSError("Could not load library cudnn_ops64_9.dll. Error code 126")
                yield

            return segments(), SimpleNamespace(language="id")

    with pytest.raises(SttError, match="cudnn_ops64_9.dll"):
        make_stt(CudnnBrokenModel()).transcribe(ONE_SECOND, 16000)


def test_errors_unrelated_to_cuda_propagate_unchanged():
    def factory(cfg: SttConfig) -> FakeWhisperModel:
        raise RuntimeError("Unable to open file 'model.bin'")

    whisper = WhisperStt(SttConfig(), "Cyrene", model_factory=factory, register_dlls=lambda: [])

    with pytest.raises(RuntimeError, match="model.bin") as raised:
        whisper.transcribe(ONE_SECOND, 16000)
    assert not isinstance(raised.value, SttError)


def _package(root: Path, name: str, *subdirs: str) -> SimpleNamespace:
    package_dir = root.joinpath(*name.split("."))
    package_dir.mkdir(parents=True)
    for subdir in subdirs:
        (package_dir / subdir).mkdir()
    return SimpleNamespace(
        origin=str(package_dir / "__init__.py"), submodule_search_locations=[str(package_dir)]
    )


def _fake_find_spec(specs: dict[str, SimpleNamespace]):
    def find_spec(name: str) -> SimpleNamespace | None:
        if name.startswith("nvidia.") and not any(n.startswith("nvidia.") for n in specs):
            raise ModuleNotFoundError("No module named 'nvidia'")
        return specs.get(name)

    return find_spec


windows_only = pytest.mark.skipif(os.name != "nt", reason="DLL directories exist only on Windows")


@windows_only
def test_register_adds_torch_lib_and_nvidia_bin_dirs_to_dll_search_and_path(
    tmp_path: Path, fresh_dll_registry: list[str]
):
    find_spec = _fake_find_spec(
        {
            "torch": _package(tmp_path, "torch", "lib"),
            "nvidia.cublas": _package(tmp_path, "nvidia.cublas", "bin"),
            "nvidia.cudnn": _package(tmp_path, "nvidia.cudnn", "bin"),
        }
    )
    expected = [
        str(tmp_path / "torch" / "lib"),
        str(tmp_path / "nvidia" / "cublas" / "bin"),
        str(tmp_path / "nvidia" / "cudnn" / "bin"),
    ]

    added = register_cuda_dll_dirs(find_spec=find_spec)

    assert added == expected
    assert fresh_dll_registry == expected
    assert os.environ["PATH"].split(os.pathsep)[:3] == expected
    assert os.environ["PATH"].endswith("C:\\Windows")


@windows_only
def test_register_is_a_noop_when_no_cuda_packages_are_installed(
    tmp_path: Path, fresh_dll_registry: list[str]
):
    assert register_cuda_dll_dirs(find_spec=_fake_find_spec({})) == []
    assert fresh_dll_registry == []
    assert os.environ["PATH"] == "C:\\Windows"


@windows_only
def test_register_is_idempotent(tmp_path: Path, fresh_dll_registry: list[str]):
    find_spec = _fake_find_spec({"torch": _package(tmp_path, "torch", "lib")})

    first = register_cuda_dll_dirs(find_spec=find_spec)
    second = register_cuda_dll_dirs(find_spec=find_spec)

    assert first == [str(tmp_path / "torch" / "lib")]
    assert second == []
    assert fresh_dll_registry == first
    assert os.environ["PATH"].count(first[0]) == 1


def test_register_does_nothing_where_dll_directories_are_unsupported(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delattr(os, "add_dll_directory", raising=False)

    def find_spec(name: str) -> None:
        raise AssertionError("must not look for packages")

    assert register_cuda_dll_dirs(find_spec=find_spec) == []
