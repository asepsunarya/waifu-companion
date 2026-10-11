from pathlib import Path

import pytest

from waifu.config import AppConfig, ConfigError, load_config

ROOT = Path(__file__).resolve().parent.parent


def write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_example_config_is_valid():
    config = load_config(ROOT / "config.example.yaml")
    assert config.assistant.name == "Cyrene"
    assert {d.id for d in config.devices} >= {"lampu_kamar", "ac", "proyektor", "komputer"}


def test_empty_file_gives_defaults(tmp_path):
    config = load_config(write_yaml(tmp_path, ""))
    assert config == AppConfig()
    assert config.brain.backend == "ollama"
    assert config.brain.claude.model == "claude-opus-5-5"


def test_missing_file_explains_how_to_create_it(tmp_path):
    with pytest.raises(ConfigError, match="config.example.yaml"):
        load_config(tmp_path / "nope.yaml")


def test_unknown_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="wakeword"):
        load_config(write_yaml(tmp_path, "assistant:\n  wakeword: cyrene\n"))


def test_device_type_requires_its_connection_block(tmp_path):
    text = "devices:\n  - id: lampu\n    name: Lampu\n    type: tuya_light\n"
    with pytest.raises(ConfigError, match="needs a 'tuya' block"):
        load_config(write_yaml(tmp_path, text))


def test_broadlink_ir_requires_host(tmp_path):
    text = "devices:\n  - id: ac\n    name: AC\n    type: ir\n    ir:\n      backend: broadlink\n"
    with pytest.raises(ConfigError, match="ir.host"):
        load_config(write_yaml(tmp_path, text))


def test_duplicate_device_ids_are_rejected(tmp_path):
    device = "  - id: tv\n    name: TV\n    type: mock\n"
    with pytest.raises(ConfigError, match="duplicate device id 'tv'"):
        load_config(write_yaml(tmp_path, "devices:\n" + device + device))


def test_device_id_must_be_a_slug(tmp_path):
    text = "devices:\n  - id: Lampu Kamar\n    name: Lampu\n    type: mock\n"
    with pytest.raises(ConfigError, match="lowercase"):
        load_config(write_yaml(tmp_path, text))


def test_wake_words_are_normalised(tmp_path):
    config = load_config(write_yaml(tmp_path, "assistant:\n  wake_words: ['  Cyrene ', '']\n"))
    assert config.assistant.wake_words == ["cyrene"]


def test_gpt_sovits_voice_is_selected_by_engine(tmp_path):
    text = (
        "tts:\n  voices:\n    id:\n      engine: edge\n      voice: id-ID-GadisNeural\n"
        "    ja:\n      engine: gpt_sovits\n      ref_audio_path: ref.wav\n"
        "      prompt_text: こんにちは\n"
    )
    config = load_config(write_yaml(tmp_path, text))
    assert config.tts.voices["ja"].engine == "gpt_sovits"
