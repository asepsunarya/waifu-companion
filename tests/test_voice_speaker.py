import contextlib
import logging
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from waifu.voice.base import SpeakerEmbedder
from waifu.voice.speaker import EcapaEmbedder, SpeakerError, SpeakerVerifier

SAMPLE_RATE = 16000
OWNER = np.array([1.0, 0.2, 0.0, 0.1], dtype=np.float32)
GUEST = np.array([0.0, 0.1, 1.0, 0.3], dtype=np.float32)


class FakeEmbedder(SpeakerEmbedder):
    """Treats the first four samples of the audio as its (un-normalised) speaker embedding."""

    def embed(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        return audio[:4].copy()


def speech(embedding: np.ndarray, loudness: float = 1.0) -> np.ndarray:
    audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    audio[:4] = embedding * loudness
    return audio


def owner_samples() -> list[np.ndarray]:
    return [speech(OWNER, 0.5), speech(OWNER + [0.0, 0.05, 0.02, 0.0]), speech(OWNER, 0.8)]


def make_verifier(tmp_path: Path, threshold: float = 0.5) -> SpeakerVerifier:
    return SpeakerVerifier(FakeEmbedder(), tmp_path / "voiceprint.npy", threshold)


def test_enroll_stores_the_normalised_mean_of_normalised_embeddings(tmp_path):
    verifier = make_verifier(tmp_path)
    loud_x, quiet_y = [10.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]
    samples = [speech(np.array(loud_x)), speech(np.array(quiet_y)), speech(np.array(quiet_y))]

    voiceprint = verifier.enroll(samples, SAMPLE_RATE)

    expected = np.array([1.0, 2.0, 0.0, 0.0]) / np.sqrt(5.0)
    np.testing.assert_allclose(voiceprint, expected, atol=1e-6)
    np.testing.assert_allclose(np.load(tmp_path / "voiceprint.npy"), expected, atol=1e-6)


def test_enroll_creates_the_missing_data_folder(tmp_path):
    path = tmp_path / "data" / "speaker" / "voiceprint.npy"
    verifier = SpeakerVerifier(FakeEmbedder(), path, threshold=0.5)

    verifier.enroll(owner_samples(), SAMPLE_RATE)

    assert path.is_file()
    assert verifier.enrolled


def test_enroll_writes_exactly_the_configured_path_even_without_npy_suffix(tmp_path):
    path = tmp_path / "voiceprint.bin"
    verifier = SpeakerVerifier(FakeEmbedder(), path, threshold=0.5)

    verifier.enroll(owner_samples(), SAMPLE_RATE)

    reloaded = SpeakerVerifier(FakeEmbedder(), path, threshold=0.5)
    assert path.is_file()
    assert reloaded.verify(speech(OWNER), SAMPLE_RATE)[0]


def test_enroll_needs_at_least_three_samples(tmp_path):
    verifier = make_verifier(tmp_path)

    with pytest.raises(SpeakerError, match="minimal 3"):
        verifier.enroll([speech(OWNER), speech(OWNER)], SAMPLE_RATE)

    assert not verifier.enrolled


def test_verify_accepts_the_owner(tmp_path):
    verifier = make_verifier(tmp_path, threshold=0.5)
    verifier.enroll(owner_samples(), SAMPLE_RATE)

    accepted, score = verifier.verify(speech(OWNER, 0.3), SAMPLE_RATE)

    assert accepted
    assert score > 0.95


def test_verify_rejects_another_voice(tmp_path):
    verifier = make_verifier(tmp_path, threshold=0.5)
    verifier.enroll(owner_samples(), SAMPLE_RATE)

    accepted, score = verifier.verify(speech(GUEST), SAMPLE_RATE)

    assert not accepted
    assert score < 0.5


def test_score_is_cosine_similarity_independent_of_loudness(tmp_path):
    verifier = make_verifier(tmp_path)
    verifier.enroll([speech(OWNER), speech(OWNER, 2.0), speech(OWNER, 0.1)], SAMPLE_RATE)

    assert verifier.score(speech(OWNER, 0.01), SAMPLE_RATE) == pytest.approx(1.0)
    assert verifier.score(speech(-OWNER), SAMPLE_RATE) == pytest.approx(-1.0)


def test_score_equal_to_threshold_is_accepted(tmp_path):
    verifier = make_verifier(tmp_path, threshold=0.0)
    x_axis = np.array([1.0, 0.0, 0.0, 0.0])
    verifier.enroll([speech(x_axis)] * 3, SAMPLE_RATE)

    accepted, score = verifier.verify(speech(np.array([0.0, 1.0, 0.0, 0.0])), SAMPLE_RATE)

    assert score == 0.0
    assert accepted


def test_verify_without_voiceprint_tells_the_owner_to_enroll(tmp_path):
    verifier = make_verifier(tmp_path)

    assert not verifier.enrolled
    with pytest.raises(SpeakerError, match="Jalankan 'waifu enroll' dulu"):
        verifier.verify(speech(OWNER), SAMPLE_RATE)


def test_voiceprint_survives_a_new_verifier_instance(tmp_path):
    make_verifier(tmp_path).enroll(owner_samples(), SAMPLE_RATE)

    reloaded = make_verifier(tmp_path)

    assert reloaded.enrolled
    assert reloaded.verify(speech(OWNER), SAMPLE_RATE)[0]
    assert not reloaded.verify(speech(GUEST), SAMPLE_RATE)[0]


def test_unreadable_voiceprint_file_asks_for_a_new_enrollment(tmp_path):
    (tmp_path / "voiceprint.npy").write_bytes(b"not a numpy file")
    verifier = make_verifier(tmp_path)

    with pytest.raises(SpeakerError, match="waifu enroll"):
        verifier.verify(speech(OWNER), SAMPLE_RATE)


def test_voiceprint_from_a_different_model_asks_for_a_new_enrollment(tmp_path):
    np.save(tmp_path / "voiceprint.npy", np.ones(192, dtype=np.float32))
    verifier = make_verifier(tmp_path)

    with pytest.raises(SpeakerError, match="waifu enroll"):
        verifier.verify(speech(OWNER), SAMPLE_RATE)


class FakeLoader:
    """Stands in for loading the speechbrain model; records how it was asked to load."""

    def __init__(self, embedding_shape: tuple[int, ...] = (1, 1, 192)) -> None:
        self.loads: list[tuple[str, Path, str]] = []
        self.encoded: list[np.ndarray] = []
        self._embedding_shape = embedding_shape

    def __call__(self, source: str, model_dir: Path, device: str):
        self.loads.append((source, model_dir, device))
        return self._encode

    def _encode(self, audio: np.ndarray) -> np.ndarray:
        self.encoded.append(audio)
        return np.full(self._embedding_shape, 0.5, dtype=np.float64)


def make_ecapa(loader: FakeLoader, device: str = "cpu", cuda: bool = False) -> EcapaEmbedder:
    return EcapaEmbedder(
        "speechbrain/spkrec-ecapa-voxceleb",
        Path("models/spkrec-ecapa"),
        device,
        loader=loader,
        cuda_available=lambda: cuda,
    )


def test_ecapa_embedder_loads_the_model_once_on_first_embed():
    loader = FakeLoader()
    embedder = make_ecapa(loader)
    assert loader.loads == []

    embedder.embed(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)
    embedder.embed(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)

    assert loader.loads == [
        ("speechbrain/spkrec-ecapa-voxceleb", Path("models/spkrec-ecapa"), "cpu")
    ]


def test_ecapa_embedder_resamples_audio_to_16khz_float32():
    loader = FakeLoader()
    embedder = make_ecapa(loader)
    one_second_at_48k = np.sin(np.linspace(0, 200 * np.pi, 48000)).astype(np.float64)

    embedder.embed(one_second_at_48k, 48000)

    (sent,) = loader.encoded
    assert sent.shape == (16000,)
    assert sent.dtype == np.float32


def test_ecapa_embedder_returns_a_flat_float32_embedding():
    embedder = make_ecapa(FakeLoader(embedding_shape=(1, 1, 192)))

    embedding = embedder.embed(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)

    assert embedding.shape == (192,)
    assert embedding.dtype == np.float32


def test_ecapa_embedder_falls_back_to_cpu_when_cuda_is_missing(caplog):
    loader = FakeLoader()
    embedder = make_ecapa(loader, device="cuda", cuda=False)

    with caplog.at_level(logging.WARNING, logger="waifu.voice.speaker"):
        embedder.embed(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)

    assert loader.loads[0][2] == "cpu"
    assert "CUDA" in caplog.text


def test_ecapa_embedder_keeps_cuda_when_it_is_available():
    loader = FakeLoader()
    embedder = make_ecapa(loader, device="cuda", cuda=True)

    embedder.embed(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)

    assert loader.loads[0][2] == "cuda"


class FakeTensor:
    def __init__(self, array: np.ndarray) -> None:
        self.array = np.asarray(array)

    def __getitem__(self, index: object) -> "FakeTensor":
        return FakeTensor(self.array[index])

    def cpu(self) -> "FakeTensor":
        return self

    def numpy(self) -> np.ndarray:
        return self.array


class FakeSpeechbrain:
    """Records what the default loader asks of speechbrain's EncoderClassifier."""

    LocalStrategy = types.SimpleNamespace(SYMLINK="symlink", COPY="copy")

    def __init__(self) -> None:
        self.from_hparams_kwargs: list[dict] = []
        self.batches: list[np.ndarray] = []

    def from_hparams(self, **kwargs: object) -> "FakeSpeechbrain":
        self.from_hparams_kwargs.append(kwargs)
        return self

    def encode_batch(self, wavs: FakeTensor) -> FakeTensor:
        self.batches.append(wavs.array)
        return FakeTensor(np.ones((1, 1, 192), dtype=np.float32))


@pytest.fixture
def fake_speechbrain(monkeypatch) -> FakeSpeechbrain:
    """Replace torch and speechbrain in sys.modules so the default loader runs without them."""
    fake = FakeSpeechbrain()
    modules = {
        "torch": types.SimpleNamespace(
            from_numpy=FakeTensor, inference_mode=contextlib.nullcontext
        ),
        "speechbrain": types.ModuleType("speechbrain"),
        "speechbrain.inference": types.ModuleType("speechbrain.inference"),
        "speechbrain.inference.classifiers": types.SimpleNamespace(EncoderClassifier=fake),
        "speechbrain.utils": types.ModuleType("speechbrain.utils"),
        "speechbrain.utils.fetching": types.SimpleNamespace(LocalStrategy=fake.LocalStrategy),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return fake


def test_default_loader_copies_model_files_instead_of_symlinking(fake_speechbrain, tmp_path):
    embedder = EcapaEmbedder("speechbrain/spkrec-ecapa-voxceleb", tmp_path / "ecapa", "cpu")

    embedder.embed(np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)

    assert fake_speechbrain.from_hparams_kwargs == [
        {
            "source": "speechbrain/spkrec-ecapa-voxceleb",
            "savedir": str(tmp_path / "ecapa"),
            "run_opts": {"device": "cpu"},
            "local_strategy": FakeSpeechbrain.LocalStrategy.COPY,
        }
    ]


def test_default_loader_encodes_a_single_item_batch_at_16khz(fake_speechbrain, tmp_path):
    embedder = EcapaEmbedder("speechbrain/spkrec-ecapa-voxceleb", tmp_path / "ecapa", "cpu")

    embedding = embedder.embed(np.zeros(8000, dtype=np.float32), 8000)

    (batch,) = fake_speechbrain.batches
    assert batch.shape == (1, 16000)
    assert embedding.shape == (192,)
