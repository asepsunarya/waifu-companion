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


def test_unquoted_on_off_commands_stay_strings(tmp_path):
    text = "devices:\n  - id: tv\n    name: TV\n    type: mock\n    commands: [on, off, OFF]\n"
    config = load_config(write_yaml(tmp_path, text))
    assert config.devices[0].commands == ["on", "off", "off"]


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


def test_claude_max_tokens_above_the_non_streaming_limit_is_rejected(tmp_path):
    # The SDK refuses non-streaming requests that could run past 10 minutes (> 21,333 tokens).
    text = "brain:\n  claude:\n    max_tokens: 32000\n"
    with pytest.raises(ConfigError, match="max_tokens"):
        load_config(write_yaml(tmp_path, text))


def test_device_command_names_must_be_slugs(tmp_path):
    text = "devices:\n  - id: tv\n    name: TV\n    type: mock\n    commands: [on, Volume Up]\n"
    with pytest.raises(ConfigError, match="Volume Up"):
        load_config(write_yaml(tmp_path, text))


def test_config_errors_never_echo_secret_values(tmp_path):
    text = (
        "devices:\n  - id: lampu\n    name: Lampu\n    type: tuya_light\n"
        "    tuya:\n      local_key: SUPERSECRETKEY\n"
    )
    with pytest.raises(ConfigError) as caught:
        load_config(write_yaml(tmp_path, text))
    assert "device_id" in str(caught.value)
    assert "SUPERSECRETKEY" not in str(caught.value)


def test_rvc_is_off_by_default_with_a_tsukuyomi_pitch_shift():
    rvc = AppConfig().tts.rvc
    assert rvc.enabled is False
    assert rvc.pitch == 4
    assert rvc.host == "127.0.0.1"


def test_enabled_rvc_needs_a_model(tmp_path):
    with pytest.raises(ConfigError, match="tts.rvc.model"):
        load_config(write_yaml(tmp_path, "tts:\n  rvc:\n    enabled: true\n"))


def test_rvc_python_defaults_to_the_applio_env(tmp_path):
    text = "tts:\n  rvc:\n    applio_dir: D:/Applio\n"
    rvc = load_config(write_yaml(tmp_path, text)).tts.rvc
    assert rvc.python_path == Path("D:/Applio/env/Scripts/python.exe")


def test_rvc_pitch_is_bounded(tmp_path):
    with pytest.raises(ConfigError, match="pitch"):
        load_config(write_yaml(tmp_path, "tts:\n  rvc:\n    pitch: 40\n"))


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "localhost", "::1"])
def test_rvc_host_must_be_a_loopback_address(tmp_path, host):
    # The RVC server has no authentication; "localhost" also costs ~2 s per request on
    # Windows while IPv6 ::1 is tried first, and the server listens on IPv4 only.
    with pytest.raises(ConfigError, match="tts.rvc.host"):
        load_config(write_yaml(tmp_path, f"tts:\n  rvc:\n    host: {host}\n"))


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2"])
def test_rvc_ipv4_loopback_hosts_are_accepted(tmp_path, host):
    text = f"tts:\n  rvc:\n    host: '{host}'\n"
    assert load_config(write_yaml(tmp_path, text)).tts.rvc.host == host


def test_avatar_is_off_by_default_as_a_desktop_mascot():
    avatar = AppConfig().avatar
    assert avatar.enabled is False
    assert avatar.window == "mascot"
    assert (avatar.host, avatar.port) == ("127.0.0.1", 9882)


def test_enabled_avatar_needs_a_vrm_model(tmp_path):
    with pytest.raises(ConfigError, match="avatar.model"):
        load_config(write_yaml(tmp_path, "avatar:\n  enabled: true\n"))


def test_avatar_host_must_be_ipv4_loopback(tmp_path):
    with pytest.raises(ConfigError, match="avatar.host"):
        load_config(write_yaml(tmp_path, "avatar:\n  host: 0.0.0.0\n"))


def test_avatar_window_mode_is_validated(tmp_path):
    with pytest.raises(ConfigError, match="window"):
        load_config(write_yaml(tmp_path, "avatar:\n  window: fullscreen\n"))
