import re
from datetime import time
from pathlib import Path

import pytest

from waifu.config import (
    AppConfig,
    BreaksConfig,
    ConfigError,
    FallbackTtsConfig,
    FocusConfig,
    GameModeConfig,
    GuestModeConfig,
    HotkeysConfig,
    JapaneseConfig,
    JournalConfig,
    PlayTimeConfig,
    PrayerConfig,
    RainAlertConfig,
    TepiraConfig,
    TtsConfig,
    VersionNoticeConfig,
    VoiceVolumeConfig,
    WeeklyReportConfig,
    load_config,
)
from waifu.hotkeys import KEYS, virtual_key

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


def test_the_offline_fallback_voice_is_on_for_indonesian_and_japanese_by_default():
    fallback = AppConfig().tts.fallback
    assert fallback.enabled is True
    assert fallback.voices == {"id": "", "ja": ""}
    assert fallback.retry_s == 600
    # Measured: a female Windows voice (~188 Hz) needs +11 to reach the anime model's
    # ~351 Hz; male ones lie between Mark (+13 to +15) and David (+22).
    assert (fallback.rvc_pitch.female, fallback.rvc_pitch.male) == (11, 18)


def test_a_fallback_voice_can_be_named_for_a_language(tmp_path):
    text = "tts:\n  fallback:\n    voices:\n      id: Microsoft Zira\n"
    assert load_config(write_yaml(tmp_path, text)).tts.fallback.voices == {"id": "Microsoft Zira"}


def test_a_fallback_voice_left_blank_means_the_installed_voice_of_its_language(tmp_path):
    text = "tts:\n  fallback:\n    voices:\n      id:\n      ja: ''\n"
    assert load_config(write_yaml(tmp_path, text)).tts.fallback.voices == {"id": "", "ja": ""}


def test_fallback_voices_left_empty_mean_the_installed_voices_of_both_languages(tmp_path):
    # YAML null: what an owner writes after reading "biarkan tts.fallback.voices kosong".
    text = "tts:\n  fallback:\n    voices:\n"
    assert load_config(write_yaml(tmp_path, text)).tts.fallback.voices == {"id": "", "ja": ""}


@pytest.mark.parametrize("gender", ["female", "male"])
def test_the_fallback_rvc_pitch_is_bounded(tmp_path, gender):
    text = f"tts:\n  fallback:\n    rvc_pitch:\n      {gender}: 25\n"
    with pytest.raises(ConfigError, match=gender):
        load_config(write_yaml(tmp_path, text))


def test_the_example_config_spells_out_the_fallback_voice_defaults():
    fallback = load_config(ROOT / "config.example.yaml").tts.fallback
    assert fallback == FallbackTtsConfig()
    assert "  fallback:\n" in (ROOT / "config.example.yaml").read_text(encoding="utf-8")


def test_masters_pronunciation_rules_live_in_data_by_default():
    assert TtsConfig().lexicon_path == Path("data/tts_lexicon.json")


def test_the_example_config_names_the_pronunciation_rules_file():
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")

    assert load_config(ROOT / "config.example.yaml").tts.lexicon_path == TtsConfig().lexicon_path
    assert "  lexicon_path: data/tts_lexicon.json" in example


def test_the_fallback_pause_must_be_positive(tmp_path):
    with pytest.raises(ConfigError, match="retry_s"):
        load_config(write_yaml(tmp_path, "tts:\n  fallback:\n    retry_s: 0\n"))


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


GOOD_COOKIE = "ltuid_v2=12345; ltoken_v2=v2_SECRETTOKEN; ltmid_v2=abc"
GOOD_TOPIC = "cyrene-AbCdEf123456_-x"


def test_gacha_and_notify_are_off_by_default_for_the_three_asia_games():
    config = AppConfig()
    assert config.gacha.enabled is False
    assert config.gacha.games == ["hsr", "genshin", "zzz"]
    assert config.gacha.server_utc_offset == 8
    assert config.gacha.reminders.daily_time == time(20, 0)
    assert config.gacha.reminders.stamina_minutes == 60
    assert config.notify.enabled is False
    assert config.notify.server == "https://ntfy.sh"


def test_gacha_cookie_is_kept_secret(tmp_path):
    config = load_config(write_yaml(tmp_path, f"gacha:\n  cookie: '{GOOD_COOKIE}'\n"))
    assert config.gacha.cookie.get_secret_value() == GOOD_COOKIE
    assert "SECRETTOKEN" not in repr(config.gacha)


@pytest.mark.parametrize("cookie", ["ltuid_v2=1; ltoken_v2=x", "ltuid=1; ltoken=x; foo=bar"])
def test_gacha_cookie_accepts_either_hoyolab_login_pair(tmp_path, cookie):
    config = load_config(write_yaml(tmp_path, f"gacha:\n  cookie: '{cookie}'\n"))
    assert config.gacha.cookie.get_secret_value() == cookie


def test_gacha_cookie_without_the_login_pair_names_it_but_never_echoes_it(tmp_path):
    text = "gacha:\n  cookie: 'ltoken_v2=v2_SECRETTOKEN; mi18nLang=id-id'\n"
    with pytest.raises(ConfigError) as caught:
        load_config(write_yaml(tmp_path, text))
    assert "ltuid_v2" in str(caught.value)
    assert "SECRETTOKEN" not in str(caught.value)


def test_gacha_daily_time_takes_quoted_or_bare_yaml_hh_mm(tmp_path):
    # YAML 1.1 reads a bare 20:00 as the base-60 number 1200.
    quoted = load_config(write_yaml(tmp_path, "gacha:\n  reminders:\n    daily_time: '07:30'\n"))
    bare = load_config(write_yaml(tmp_path, "gacha:\n  reminders:\n    daily_time: 21:15\n"))
    assert quoted.gacha.reminders.daily_time == time(7, 30)
    assert bare.gacha.reminders.daily_time == time(21, 15)


@pytest.mark.parametrize("value", ["'25:00'", "8pm", "'20.00'"])
def test_gacha_daily_time_must_be_a_time_of_day(tmp_path, value):
    with pytest.raises(ConfigError, match="daily_time"):
        load_config(write_yaml(tmp_path, f"gacha:\n  reminders:\n    daily_time: {value}\n"))


@pytest.mark.parametrize("games", ["[hsr, hsr]", "[]", "[honkai3rd]"])
def test_gacha_games_are_known_unique_and_not_empty(tmp_path, games):
    with pytest.raises(ConfigError, match="games"):
        load_config(write_yaml(tmp_path, f"gacha:\n  games: {games}\n"))


def test_gacha_uids_and_off_banner_names_are_keyed_by_game(tmp_path):
    text = "gacha:\n  uids: {hsr: 800000001}\n  off_banner: {hsr: [Yunli]}\n"
    gacha = load_config(write_yaml(tmp_path, text)).gacha
    assert gacha.uids == {"hsr": 800000001}
    assert gacha.off_banner == {"hsr": ["Yunli"]}
    with pytest.raises(ConfigError, match="uids"):
        load_config(write_yaml(tmp_path, "gacha:\n  uids: {honkai3rd: 1}\n"))


def test_gacha_polling_is_bounded(tmp_path):
    with pytest.raises(ConfigError, match="poll_minutes"):
        load_config(write_yaml(tmp_path, "gacha:\n  poll_minutes: 1\n"))


def test_enabled_notify_needs_a_topic(tmp_path):
    with pytest.raises(ConfigError, match="notify.topic"):
        load_config(write_yaml(tmp_path, "notify:\n  enabled: true\n"))


@pytest.mark.parametrize("topic", ["cyrene", "has space in the topic name", "x" * 65])
def test_notify_topic_must_be_long_and_plain_and_is_never_echoed(tmp_path, topic):
    with pytest.raises(ConfigError, match="topic") as caught:
        load_config(write_yaml(tmp_path, f"notify:\n  topic: '{topic}'\n"))
    assert topic not in str(caught.value)


def test_notify_topic_is_kept_secret(tmp_path):
    text = f"notify:\n  enabled: true\n  topic: {GOOD_TOPIC}\n"
    notify = load_config(write_yaml(tmp_path, text)).notify
    assert notify.topic.get_secret_value() == GOOD_TOPIC
    assert GOOD_TOPIC not in repr(notify)


def test_notify_server_must_use_https(tmp_path):
    with pytest.raises(ConfigError, match="https"):
        load_config(write_yaml(tmp_path, "notify:\n  server: http://ntfy.sh\n"))
    text = "notify:\n  server: https://ntfy.example.com/\n"
    assert load_config(write_yaml(tmp_path, text)).notify.server == "https://ntfy.example.com"


def test_avatar_engine_defaults_to_vrm_with_interactions_on():
    avatar = AppConfig().avatar
    assert avatar.engine == "vrm"
    assert avatar.interactions.enabled is True
    assert avatar.interactions.cursor_follow is True
    assert avatar.interactions.welcome_back_after_min == 10
    for kind in ("head_pat", "body_touch", "welcome_back", "listen"):
        assert avatar.interactions.lines[kind], kind


def test_live2d_engine_needs_the_cubism_core_and_a_model3_json(tmp_path):
    base = "avatar:\n  enabled: true\n  engine: live2d\n"
    with pytest.raises(ConfigError, match="live2d_core"):
        load_config(write_yaml(tmp_path, base + "  model: C:/x/Cyrene.model3.json\n"))
    with pytest.raises(ConfigError, match="model3.json"):
        load_config(
            write_yaml(tmp_path, base + "  model: C:/x/a.vrm\n  live2d_core: C:/x/core.js\n")
        )
    ok = base + "  model: C:/x/Cyrene.model3.json\n  live2d_core: C:/x/live2dcubismcore.min.js\n"
    assert load_config(write_yaml(tmp_path, ok)).avatar.engine == "live2d"


def test_vrm_engine_model_must_be_a_vrm_file(tmp_path):
    text = "avatar:\n  enabled: true\n  model: C:/x/Cyrene.model3.json\n"
    with pytest.raises(ConfigError, match=r"\.vrm"):
        load_config(write_yaml(tmp_path, text))


def test_default_interaction_lines_call_the_owner_through_the_placeholder():
    kinds = AppConfig().avatar.interactions.lines.values()
    lines = [line for lines_of_kind in kinds for line in lines_of_kind]

    assert not any("Master" in line for line in lines)
    assert "{owner} sudah kembali! Aku kangen." in lines


def test_the_example_config_shows_the_owner_call_and_the_default_lines():
    # Lines copied from the example must not say "Master" to an owner called something else.
    config = load_config(ROOT / "config.example.yaml")

    assert config.assistant.owner_call == "Master"
    assert config.avatar.interactions.lines == AppConfig().avatar.interactions.lines


def test_interaction_lines_must_not_be_empty(tmp_path):
    text = "avatar:\n  interactions:\n    lines:\n      head_pat: []\n"
    with pytest.raises(ConfigError, match="head_pat"):
        load_config(write_yaml(tmp_path, text))


def test_memory_is_on_by_default_in_the_data_folder():
    memory = AppConfig().memory
    assert memory.enabled is True
    assert memory.path == Path("data/memory.json")
    assert memory.max_facts == 100
    assert memory.max_chars == 200


@pytest.mark.parametrize(
    "line", ["max_facts: 0", "max_facts: 501", "max_chars: 19", "max_chars: 1001", "size: 3"]
)
def test_memory_limits_are_bounded_and_unknown_keys_rejected(tmp_path, line):
    with pytest.raises(ConfigError, match="memory"):
        load_config(write_yaml(tmp_path, f"memory:\n  {line}\n"))


def test_memory_settings_are_read(tmp_path):
    text = "memory:\n  enabled: false\n  path: D:/cyrene/ingatan.json\n  max_facts: 50\n"
    memory = load_config(write_yaml(tmp_path, text)).memory
    assert (memory.enabled, memory.path, memory.max_facts) == (
        False,
        Path("D:/cyrene/ingatan.json"),
        50,
    )


def test_schedule_is_on_by_default_in_the_data_folder():
    schedule = AppConfig().schedule
    assert schedule.enabled is True
    assert schedule.path == Path("data/schedule.json")
    assert schedule.max_entries == 50
    assert schedule.late_grace_minutes == 30
    assert AppConfig().routines == {}


@pytest.mark.parametrize(
    "line",
    ["max_entries: 0", "max_entries: 201", "late_grace_minutes: -1", "late_grace_minutes: 721"],
)
def test_schedule_limits_are_bounded(tmp_path, line):
    with pytest.raises(ConfigError, match="schedule"):
        load_config(write_yaml(tmp_path, f"schedule:\n  {line}\n"))


def test_the_alarm_is_on_by_default_and_rings_every_minute_for_ten_minutes():
    alarm = AppConfig().alarm

    assert alarm.enabled is True
    assert (alarm.repeat_seconds, alarm.max_minutes, alarm.snooze_minutes) == (60, 10, 5)
    assert alarm.push is True
    assert alarm.state_path == Path("data/alarm.json")


@pytest.mark.parametrize(
    "line",
    [
        "repeat_seconds: 29",
        "repeat_seconds: 301",
        "max_minutes: 0",
        "max_minutes: 31",
        "snooze_minutes: 0",
        "snooze_minutes: 31",
        "volume: 150",
    ],
)
def test_alarm_settings_are_bounded_and_known(tmp_path, line):
    with pytest.raises(ConfigError, match="alarm"):
        load_config(write_yaml(tmp_path, f"alarm:\n  {line}\n"))


def test_the_global_hotkeys_are_on_by_default_with_c_x_r_and_q():
    hotkeys = AppConfig().hotkeys

    assert hotkeys.enabled is True
    assert hotkeys.bindings == {"talk": "C", "stop": "X", "repeat": "R", "quiet": "Q"}
    assert hotkeys.quiet_hours == 2
    assert hotkeys.pause_path == Path("data/listen_pause.json")


def test_a_hotkey_set_to_null_is_switched_off(tmp_path):
    hotkeys = load_config(write_yaml(tmp_path, "hotkeys:\n  repeat: null\n")).hotkeys

    assert hotkeys.bindings == {"talk": "C", "stop": "X", "quiet": "Q"}


@pytest.mark.parametrize(
    ("value", "key"), [("c", "C"), ("f9", "F9"), ("F12", "F12"), ("'5'", "5"), ("' k '", "K")]
)
def test_a_hotkey_takes_one_key_in_any_case(tmp_path, value, key):
    hotkeys = load_config(write_yaml(tmp_path, f"hotkeys:\n  talk: {value}\n")).hotkeys

    assert hotkeys.talk == key


def test_a_bare_digit_in_yaml_is_a_key(tmp_path):
    # YAML reads `talk: 7` as a number.
    assert load_config(write_yaml(tmp_path, "hotkeys:\n  talk: 7\n")).hotkeys.talk == "7"


def test_every_key_the_config_takes_has_a_virtual_key_code():
    for key in KEYS:
        alone = HotkeysConfig(talk=key.lower(), stop=None, repeat=None, quiet=None)
        assert virtual_key(alone.talk) == virtual_key(key)


@pytest.mark.parametrize(
    "value", ["F13", "F0", "CC", "Ctrl+Alt+C", "''", "10", "true", "'+'", "[C]", "Esc"]
)
def test_a_hotkey_that_is_not_one_key_is_refused_naming_it(tmp_path, value):
    with pytest.raises(ConfigError, match=r"hotkeys\.talk"):
        load_config(write_yaml(tmp_path, f"hotkeys:\n  talk: {value}\n"))


def test_two_hotkeys_on_the_same_key_are_refused(tmp_path):
    with pytest.raises(ConfigError, match="hotkeys: talk and quiet both use Q"):
        load_config(write_yaml(tmp_path, "hotkeys:\n  talk: q\n"))


@pytest.mark.parametrize("line", ["quiet_hours: 0.2", "quiet_hours: 12.5", "volume: 3"])
def test_hotkey_settings_are_bounded_and_known(tmp_path, line):
    with pytest.raises(ConfigError, match="hotkeys"):
        load_config(write_yaml(tmp_path, f"hotkeys:\n  {line}\n"))


def test_the_example_hotkeys_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# hotkeys:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).hotkeys == HotkeysConfig()


ROUTINE_DEVICES = """
devices:
  - id: lampu_kamar
    name: Lampu Kamar
    type: mock
    commands: [on, off]
  - id: ac
    name: AC
    type: mock
    commands: [on, off, temp_26]
  - id: komputer
    name: Komputer
    type: pc
    pc: {}
"""


def test_routines_are_read_with_yaml_on_off_as_commands(tmp_path):
    text = ROUTINE_DEVICES + (
        "routines:\n"
        "  tidur:\n"
        "    description: Mode tidur\n"
        "    steps:\n"
        "      - {device: lampu_kamar, command: off}\n"
        "      - {device: ac, command: temp, value: 26}\n"
    )
    routine = load_config(write_yaml(tmp_path, text)).routines["tidur"]
    assert routine.description == "Mode tidur"
    assert [(s.device, s.command, s.value) for s in routine.steps] == [
        ("lampu_kamar", "off", None),
        ("ac", "temp", 26),
    ]


@pytest.mark.parametrize(
    ("routines", "match"),
    [
        ("  Tidur:\n    steps: [{device: ac, command: off}]\n", "routine name"),
        ("  tidur:\n    steps: []\n", "tidur"),
        ("  tidur:\n    steps: [{device: kulkas, command: off}]\n", "kulkas"),
        ("  tidur:\n    steps: [{device: komputer, command: sleep}]\n", "komputer"),
        ("  tidur:\n    steps: [{device: ac, command: off, speed: 2}]\n", "speed"),
    ],
)
def test_bad_routines_are_refused(tmp_path, routines, match):
    with pytest.raises(ConfigError, match=match):
        load_config(write_yaml(tmp_path, ROUTINE_DEVICES + "routines:\n" + routines))


def test_a_routine_has_at_most_20_steps(tmp_path):
    steps = "".join("      - {device: ac, command: off}\n" for _ in range(21))
    text = ROUTINE_DEVICES + "routines:\n  tidur:\n    steps:\n" + steps
    with pytest.raises(ConfigError, match="tidur"):
        load_config(write_yaml(tmp_path, text))


PC_PLUG = "  - id: colokan_pc\n    name: Colokan PC\n    type: mock\n    powers_pc: true\n"


def test_a_plug_can_be_marked_as_powering_the_pc(tmp_path):
    devices = load_config(write_yaml(tmp_path, ROUTINE_DEVICES + PC_PLUG)).devices
    assert {device.id: device.powers_pc for device in devices} == {
        "lampu_kamar": False,
        "ac": False,
        "komputer": False,
        "colokan_pc": True,
    }


def test_only_a_plug_can_be_marked_as_powering_the_pc(tmp_path):
    text = "devices:\n  - id: komputer\n    name: Komputer\n    type: pc\n    pc: {}\n"
    with pytest.raises(ConfigError, match="powers_pc"):
        load_config(write_yaml(tmp_path, text + "    powers_pc: true\n"))


def test_a_routine_may_not_use_the_plug_that_powers_the_pc(tmp_path):
    routines = "routines:\n  tidur:\n    steps: [{device: colokan_pc, command: off}]\n"
    with pytest.raises(ConfigError, match="colokan_pc"):
        load_config(write_yaml(tmp_path, ROUTINE_DEVICES + PC_PLUG + routines))


def test_the_example_config_marks_its_pc_plug():
    devices = load_config(ROOT / "config.example.yaml").devices
    assert next(device for device in devices if device.id == "colokan_pc").powers_pc is True


def tidur_with(step: str, devices: str = ROUTINE_DEVICES) -> str:
    """The devices and a routine `tidur` whose third step is `step` (a YAML flow mapping)."""
    return devices + (
        "routines:\n"
        "  tidur:\n"
        "    steps:\n"
        "      - {device: lampu_kamar, command: off}\n"
        "      - {device: ac, command: temp, value: 26}\n"
        f"      - {step}\n"
    )


def test_a_routine_step_may_wait_whole_minutes_after_the_routine_starts(tmp_path):
    text = tidur_with("{device: ac, command: off, after_minutes: 180}")

    steps = load_config(write_yaml(tmp_path, text)).routines["tidur"].steps

    assert [step.after_minutes for step in steps] == [None, None, 180]


@pytest.mark.parametrize("delay", ["1", "1440"])
def test_a_step_delay_may_be_one_minute_to_a_day(tmp_path, delay):
    text = tidur_with(f"{{device: ac, command: off, after_minutes: {delay}}}")

    [*_, last] = load_config(write_yaml(tmp_path, text)).routines["tidur"].steps

    assert last.after_minutes == int(delay)


@pytest.mark.parametrize("delay", ["0", "-5", "1441", "true", "3h", "'180'", "1.5", "180.0"])
def test_a_step_delay_must_be_whole_minutes_from_1_to_1440(tmp_path, delay):
    text = tidur_with(f"{{device: ac, command: off, after_minutes: {delay}}}")

    with pytest.raises(ConfigError) as caught:
        load_config(write_yaml(tmp_path, text))

    message = str(caught.value)
    assert "after_minutes must be a whole number of minutes from 1 to 1440" in message
    assert "like 180 for 3 hours" in message


@pytest.mark.parametrize(
    ("step", "devices", "match"),
    [
        ("{device: komputer, command: sleep, after_minutes: 60}", ROUTINE_DEVICES, "komputer"),
        (
            "{device: colokan_pc, command: off, after_minutes: 60}",
            ROUTINE_DEVICES + PC_PLUG,
            "colokan_pc",
        ),
    ],
    ids=["the PC", "the plug that powers the PC"],
)
def test_a_delayed_step_never_powers_the_pc(tmp_path, step, devices, match):
    with pytest.raises(ConfigError, match=match):
        load_config(write_yaml(tmp_path, tidur_with(step, devices)))


def test_a_delayed_step_needs_the_schedule_on(tmp_path):
    text = tidur_with("{device: ac, command: off, after_minutes: 180}")

    with pytest.raises(ConfigError) as caught:
        load_config(write_yaml(tmp_path, text + "schedule:\n  enabled: false\n"))

    assert (
        "routine 'tidur' step 3 has after_minutes, but delayed steps run from the schedule: "
        "set schedule.enabled: true or remove after_minutes"
    ) in str(caught.value)


def test_a_routine_without_delays_runs_with_the_schedule_off(tmp_path):
    text = tidur_with("{device: ac, command: off}") + "schedule:\n  enabled: false\n"

    assert list(load_config(write_yaml(tmp_path, text)).routines) == ["tidur"]


MEDIA_DEVICE = "  - id: musik\n    name: Musik PC\n    type: media\n"


@pytest.mark.parametrize("command", ["play_pause", "mute"])
def test_a_media_key_that_toggles_is_never_delayed(tmp_path, command):
    step = f"{{device: musik, command: {command}, after_minutes: 30}}"

    with pytest.raises(ConfigError) as caught:
        load_config(write_yaml(tmp_path, tidur_with(step, ROUTINE_DEVICES + MEDIA_DEVICE)))

    message = str(caught.value)
    assert f"routine 'tidur' step 3 may not delay '{command}' on 'musik'" in message
    assert "delay stop or volume instead" in message


@pytest.mark.parametrize(
    "step",
    [
        "{device: musik, command: play_pause}",
        "{device: musik, command: stop, after_minutes: 30}",
        "{device: musik, command: volume, value: 10, after_minutes: 30}",
    ],
)
def test_a_media_toggle_now_or_a_delayed_stop_is_fine(tmp_path, step):
    config = load_config(write_yaml(tmp_path, tidur_with(step, ROUTINE_DEVICES + MEDIA_DEVICE)))

    assert len(config.routines["tidur"].steps) == 3


def apps_device(apps: str) -> str:
    return f"devices:\n  - id: aplikasi\n    name: Aplikasi PC\n    type: apps\n    apps:\n{apps}"


def test_a_media_device_needs_no_connection_block(tmp_path):
    text = "devices:\n  - id: musik\n    name: Musik PC\n    type: media\n"
    [device] = load_config(write_yaml(tmp_path, text)).devices
    assert (device.type, device.apps) == ("media", None)


def test_an_apps_device_maps_slugs_to_programs_shortcuts_and_launcher_urls(tmp_path):
    apps = (
        "      genshin: 'C:/Program Files/HoYoPlay/launcher.exe'\n"
        "      spotify: 'C:\\Users\\Lynx\\Desktop\\Spotify.LNK'\n"
        "      zzz: 'steam://rungameid/2507950'\n"
        "      epic: 'com.epicgames.launcher://apps/fn?action=launch'\n"
        "      web: 'D:/Pintasan/YouTube.url'\n"
    )
    [device] = load_config(write_yaml(tmp_path, apps_device(apps))).devices
    assert list(device.apps) == ["genshin", "spotify", "zzz", "epic", "web"]
    assert device.apps["zzz"] == "steam://rungameid/2507950"


@pytest.mark.parametrize(
    "target",
    [
        "C:/Scripts/run.bat",
        "C:/Scripts/run.cmd",
        "C:/Scripts/run.ps1",
        "C:/Scripts/run.vbs",
        "launcher.exe",
        "Games/launcher.exe",
        "//server/share/launcher.exe",
        "https://example.com/app.exe",
        "file:///C:/Windows/notepad.exe",
        "ms-settings:display",
    ],
)
def test_an_app_target_must_be_a_program_shortcut_or_launcher_url(tmp_path, target):
    with pytest.raises(ConfigError, match="genshin"):
        load_config(write_yaml(tmp_path, apps_device(f"      genshin: '{target}'\n")))


def test_app_names_are_slugs(tmp_path):
    with pytest.raises(ConfigError, match="Genshin Impact"):
        load_config(write_yaml(tmp_path, apps_device("      Genshin Impact: 'C:/g/g.exe'\n")))


def test_an_apps_device_needs_one_to_thirty_apps(tmp_path):
    with pytest.raises(ConfigError, match="apps"):
        load_config(write_yaml(tmp_path, apps_device("      {}\n")))
    no_apps = "devices:\n  - id: aplikasi\n    name: Aplikasi PC\n    type: apps\n"
    with pytest.raises(ConfigError, match="apps"):
        load_config(write_yaml(tmp_path, no_apps))
    many = "".join(f"      app_{n}: 'C:/a/{n}.exe'\n" for n in range(31))
    with pytest.raises(ConfigError, match="apps"):
        load_config(write_yaml(tmp_path, apps_device(many)))


def test_only_an_apps_device_lists_apps(tmp_path):
    text = (
        "devices:\n  - id: musik\n    name: Musik\n    type: media\n"
        "    apps:\n      genshin: 'C:/g/g.exe'\n"
    )
    with pytest.raises(ConfigError, match="apps"):
        load_config(write_yaml(tmp_path, text))


def test_vision_is_on_by_default_at_1280_pixels():
    vision = AppConfig().vision
    assert (vision.enabled, vision.max_side) == (True, 1280)


@pytest.mark.parametrize("line", ["max_side: 511", "max_side: 1569", "camera: 1"])
def test_vision_limits_are_bounded_and_unknown_keys_refused(tmp_path, line):
    with pytest.raises(ConfigError, match="vision"):
        load_config(write_yaml(tmp_path, f"vision:\n  {line}\n"))


def test_vision_settings_are_read(tmp_path):
    vision = load_config(
        write_yaml(tmp_path, "vision:\n  enabled: false\n  max_side: 1568\n")
    ).vision
    assert (vision.enabled, vision.max_side) == (False, 1568)


def test_the_briefing_is_on_from_seven_to_noon_without_a_place():
    briefing = AppConfig().briefing
    assert briefing.enabled is True
    assert (briefing.time, briefing.until) == (time(7, 0), time(12, 0))
    assert (briefing.city, briefing.latitude, briefing.longitude) == (None, None, None)
    assert briefing.push is True
    assert briefing.state_path == Path("data/briefing.json")


def test_briefing_clock_times_and_place_are_read_including_bare_yaml_times(tmp_path):
    text = "briefing:\n  time: 06:30\n  until: '11:00'\n  city: Bandung\n"
    briefing = load_config(write_yaml(tmp_path, text)).briefing
    assert (briefing.time, briefing.until, briefing.city) == (time(6, 30), time(11, 0), "Bandung")
    text = "briefing:\n  latitude: -6.9\n  longitude: 107.6\n"
    briefing = load_config(write_yaml(tmp_path, text)).briefing
    assert (briefing.latitude, briefing.longitude) == (-6.9, 107.6)


@pytest.mark.parametrize(
    "lines",
    [
        "  time: '12:00'\n  until: '07:00'\n",
        "  time: '08:00'\n  until: '08:00'\n",
        "  latitude: -6.9\n",
        "  longitude: 107.6\n",
        "  latitude: -91\n  longitude: 107.6\n",
        "  latitude: -6.9\n  longitude: 181\n",
        "  city: ''\n",
        "  weather: true\n",
    ],
)
def test_bad_briefing_settings_are_refused(tmp_path, lines):
    with pytest.raises(ConfigError, match="briefing"):
        load_config(write_yaml(tmp_path, "briefing:\n" + lines))


def test_rain_alerts_are_on_an_hour_ahead_at_sixty_percent_from_six_to_nine_and_pushed():
    rain = AppConfig().rain_alert
    assert rain.enabled is True
    assert (rain.lead_minutes, rain.threshold) == (60, 60)
    assert (rain.active_from, rain.active_until) == (time(6, 0), time(21, 0))
    assert rain.push is True


def test_rain_alert_settings_are_read_including_bare_yaml_times(tmp_path):
    text = (
        "rain_alert:\n  enabled: false\n  lead_minutes: 90\n  threshold: 70\n"
        "  active_from: 05:30\n  active_until: '22:00'\n  push: false\n"
    )
    rain = load_config(write_yaml(tmp_path, text)).rain_alert
    assert (rain.enabled, rain.lead_minutes, rain.threshold, rain.push) == (False, 90, 70, False)
    assert (rain.active_from, rain.active_until) == (time(5, 30), time(22, 0))


ACTIVE_HOURS_REVERSED = "rain_alert.active_from must be before rain_alert.active_until"


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  lead_minutes: 29\n", "rain_alert.lead_minutes"),
        ("  lead_minutes: 181\n", "rain_alert.lead_minutes"),
        ("  threshold: 29\n", "rain_alert.threshold"),
        ("  threshold: 101\n", "rain_alert.threshold"),
        ("  active_from: '21:00'\n  active_until: '06:00'\n", ACTIVE_HOURS_REVERSED),
        ("  active_from: '08:00'\n  active_until: '08:00'\n", ACTIVE_HOURS_REVERSED),
        ("  active_until: '05:00'\n", ACTIVE_HOURS_REVERSED),  # before the default 06:00
        ("  city: Cianjur\n", "rain_alert.city"),  # the place is briefing's
    ],
)
def test_bad_rain_alert_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "rain_alert:\n" + lines))


def test_the_example_rain_alert_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# rain_alert:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).rain_alert == RainAlertConfig()


def test_the_version_notice_checks_every_half_hour_from_eight_to_ten_and_pushes():
    notice = AppConfig().version_notice
    assert (notice.enabled, notice.check_minutes, notice.push) == (True, 30, True)
    assert (notice.active_from, notice.active_until) == (time(8, 0), time(22, 0))


def test_version_notice_settings_are_read_including_bare_yaml_times(tmp_path):
    text = (
        "version_notice:\n  enabled: false\n  check_minutes: 60\n  active_from: 09:30\n"
        "  active_until: '21:00'\n  push: false\n"
    )
    notice = load_config(write_yaml(tmp_path, text)).version_notice
    assert (notice.enabled, notice.check_minutes, notice.push) == (False, 60, False)
    assert (notice.active_from, notice.active_until) == (time(9, 30), time(21, 0))


NOTICE_HOURS_REVERSED = "version_notice.active_from must be before version_notice.active_until"


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  check_minutes: 4\n", "version_notice.check_minutes"),
        ("  check_minutes: 1441\n", "version_notice.check_minutes"),
        ("  active_from: '22:00'\n  active_until: '08:00'\n", NOTICE_HOURS_REVERSED),
        ("  active_from: '08:00'\n  active_until: '08:00'\n", NOTICE_HOURS_REVERSED),
        ("  active_until: '07:00'\n", NOTICE_HOURS_REVERSED),  # before the default 08:00
        ("  restart: true\n", "version_notice.restart"),  # she never restarts herself
    ],
)
def test_bad_version_notice_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "version_notice:\n" + lines))


def test_the_example_version_notice_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# version_notice:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    notice = load_config(write_yaml(tmp_path, written_out)).version_notice
    assert notice == VersionNoticeConfig()


def test_she_calls_the_owner_master_unless_told_otherwise(tmp_path):
    assert AppConfig().assistant.owner_call == "Master"
    text = "assistant:\n  owner_call: Sayang\n"
    assert load_config(write_yaml(tmp_path, text)).assistant.owner_call == "Sayang"


@pytest.mark.parametrize("word", ["''", "'   '", "'" + "x" * 31 + "'", "'Say{ang'"])
def test_the_owner_call_is_a_short_plain_word(tmp_path, word):
    with pytest.raises(ConfigError, match="owner_call"):
        load_config(write_yaml(tmp_path, f"assistant:\n  owner_call: {word}\n"))


def test_usage_is_recorded_by_default_without_a_budget():
    usage = AppConfig().usage
    assert usage.enabled is True
    assert usage.path == Path("data/usage.json")
    assert (usage.monthly_budget_usd, usage.idr_per_usd) == (None, None)
    assert usage.prices == {}
    assert usage.default_model == "claude-opus-5-5"


def test_usage_budget_rate_and_prices_are_read(tmp_path):
    text = (
        "usage:\n  monthly_budget_usd: 10\n  idr_per_usd: 16300\n  prices:\n"
        "    claude-sonnet-5-5: {input: 2, output: 10, cache_write: 2.5, cache_read: 0.2}\n"
    )
    usage = load_config(write_yaml(tmp_path, text)).usage
    assert (usage.monthly_budget_usd, usage.idr_per_usd) == (10, 16300)
    sonnet = usage.prices["claude-sonnet-5-5"]
    assert (sonnet.input, sonnet.output, sonnet.cache_write, sonnet.cache_read) == (2, 10, 2.5, 0.2)


@pytest.mark.parametrize(
    "lines",
    [
        "  monthly_budget_usd: 0\n",
        "  idr_per_usd: -1\n",
        "  prices:\n    m: {input: 1, output: 1, cache_write: 1}\n",
        "  prices:\n    m: {input: -1, output: 1, cache_write: 1, cache_read: 1}\n",
        "  default_model: ''\n",
        "  currency: idr\n",
    ],
)
def test_bad_usage_settings_are_refused(tmp_path, lines):
    with pytest.raises(ConfigError, match="usage"):
        load_config(write_yaml(tmp_path, "usage:\n" + lines))


def test_the_phone_is_off_by_default_on_its_own_port():
    phone = AppConfig().phone
    assert (phone.enabled, phone.port, phone.host) == (False, 9883, None)
    assert phone.state_path == Path("data/phone.json")


def test_the_phone_host_is_a_tailscale_name_kept_in_lowercase(tmp_path):
    text = "phone:\n  enabled: true\n  host: Lynx-PC.Tail1234.TS.NET\n"
    assert load_config(write_yaml(tmp_path, text)).phone.host == "lynx-pc.tail1234.ts.net"


def test_the_phone_camera_is_on_by_default_and_can_be_turned_off(tmp_path):
    assert AppConfig().phone.camera is True
    text = "phone:\n  enabled: true\n  camera: false\n"
    assert load_config(write_yaml(tmp_path, text)).phone.camera is False


@pytest.mark.parametrize(
    "lines",
    [
        "  port: 0\n",
        "  port: 65536\n",
        "  host: example.com\n",
        "  host: lynx-pc.tail1234.ts.net:443\n",
        "  host: https://lynx-pc.tail1234.ts.net\n",
        "  host: ts.net\n",
        "  camera: maybe\n",
        "  webcam: true\n",
    ],
)
def test_bad_phone_settings_are_refused(tmp_path, lines):
    with pytest.raises(ConfigError, match="phone"):
        load_config(write_yaml(tmp_path, "phone:\n" + lines))


QUICK_DEVICES = (
    ROUTINE_DEVICES
    + PC_PLUG
    + "  - id: musik\n    name: Musik PC\n    type: media\n"
    + "  - id: kipas\n    name: Kipas\n    type: mock\n"
    + "routines:\n"
    + "  tidur:\n    steps: [{device: lampu_kamar, command: off}]\n"
    + "  pagi:\n    steps: [{device: lampu_kamar, command: on}]\n"
)


def test_the_phone_shows_every_quick_button_by_default():
    quick = AppConfig().phone.quick_actions
    assert (quick.devices, quick.routines) == (None, None)


def test_the_quick_buttons_can_be_picked_and_ordered(tmp_path):
    text = QUICK_DEVICES + (
        "phone:\n  quick_actions:\n    devices: [kipas, lampu_kamar]\n    routines: []\n"
    )
    quick = load_config(write_yaml(tmp_path, text)).phone.quick_actions
    assert (quick.devices, quick.routines) == (["kipas", "lampu_kamar"], [])


@pytest.mark.parametrize(
    ("lines", "match"),
    [
        ("    devices: [kulkas]\n", "unknown device 'kulkas'"),
        ("    routines: [siang]\n", "unknown routine 'siang'"),
        ("    devices: [komputer]\n", "'komputer': the PC is never a quick button"),
        ("    devices: [colokan_pc]\n", "'colokan_pc': it powers the PC"),
        ("    devices: [musik]\n", "'musik'"),
        ("    devices: [kipas, kipas]\n", "'kipas' twice"),
        ("    routines: [tidur, tidur]\n", "'tidur' twice"),
        ("    lights: [lampu_kamar]\n", "lights"),
    ],
)
def test_bad_quick_buttons_are_refused(tmp_path, lines, match):
    text = QUICK_DEVICES + "phone:\n  quick_actions:\n" + lines
    with pytest.raises(ConfigError, match=match):
        load_config(write_yaml(tmp_path, text))


def test_an_apps_device_is_never_a_quick_button(tmp_path):
    apps = "      spotify: 'C:/Program Files/Spotify/Spotify.exe'\n"
    text = apps_device(apps) + "phone:\n  quick_actions:\n    devices: [aplikasi]\n"
    with pytest.raises(ConfigError, match="'aplikasi'"):
        load_config(write_yaml(tmp_path, text))


def test_pc_health_is_on_with_notices_by_default():
    pc = AppConfig().pc_health
    assert (pc.enabled, pc.notices, pc.poll_seconds) == (True, True, 60)
    assert (pc.disk_warn_gb, pc.gpu_warn_c, pc.gpu_warn_minutes) == (10, 85, 3)


def test_pc_health_settings_load_from_yaml(tmp_path):
    text = (
        "pc_health:\n  notices: false\n  poll_seconds: 30\n  disk_warn_gb: 25\n"
        "  gpu_warn_c: 80\n  gpu_warn_minutes: 5\n"
    )
    pc = load_config(write_yaml(tmp_path, text)).pc_health
    assert (pc.enabled, pc.notices, pc.poll_seconds) == (True, False, 30)
    assert (pc.disk_warn_gb, pc.gpu_warn_c, pc.gpu_warn_minutes) == (25, 80, 5)


@pytest.mark.parametrize(
    "lines",
    [
        "  poll_seconds: 14\n",
        "  poll_seconds: 3601\n",
        "  disk_warn_gb: 0\n",
        "  disk_warn_gb: 2001\n",
        "  gpu_warn_c: 59\n",
        "  gpu_warn_c: 101\n",
        "  gpu_warn_minutes: 0\n",
        "  gpu_warn_minutes: 61\n",
        "  cpu_warn_c: 90\n",
    ],
)
def test_bad_pc_health_settings_are_refused(tmp_path, lines):
    with pytest.raises(ConfigError, match="pc_health"):
        load_config(write_yaml(tmp_path, "pc_health:\n" + lines))


def test_play_time_counts_the_three_games_every_minute_without_a_bedtime_by_default():
    play = AppConfig().play_time
    assert (play.enabled, play.poll_seconds, play.bedtime) == (True, 60, None)
    assert play.games == {
        "hsr": ["StarRail.exe"],
        "genshin": ["GenshinImpact.exe", "YuanShen.exe"],
        "zzz": ["ZenlessZoneZero.exe"],
    }
    assert (play.keep_days, play.path) == (90, Path("data/playtime.json"))


def test_play_time_settings_load_from_yaml_with_a_bare_bedtime(tmp_path):
    text = (
        "play_time:\n  enabled: false\n  poll_seconds: 120\n  games:\n"
        "    hsr: [StarRail.exe]\n    zzz: [ZenlessZoneZero.exe, ZZZ Beta.exe]\n"
        "  bedtime: 23:30\n  keep_days: 30\n  path: data/main.json\n"
    )
    play = load_config(write_yaml(tmp_path, text)).play_time
    assert (play.enabled, play.poll_seconds, play.keep_days) == (False, 120, 30)
    assert play.games == {"hsr": ["StarRail.exe"], "zzz": ["ZenlessZoneZero.exe", "ZZZ Beta.exe"]}
    assert (play.bedtime, play.path) == (time(23, 30), Path("data/main.json"))


@pytest.mark.parametrize("bedtime", ["'18:00'", "'23:59'", "'00:00'", "'01:30'", "'05:59'"])
def test_a_bedtime_from_six_in_the_evening_until_six_in_the_morning_is_accepted(tmp_path, bedtime):
    play = load_config(write_yaml(tmp_path, f"play_time:\n  bedtime: {bedtime}\n")).play_time
    assert play.bedtime == time.fromisoformat(bedtime.strip("'"))


GAMES_LINE = "play_time.games"


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  poll_seconds: 14\n", "play_time.poll_seconds"),
        ("  poll_seconds: 601\n", "play_time.poll_seconds"),
        ("  keep_days: 0\n", "play_time.keep_days"),
        ("  keep_days: 367\n", "play_time.keep_days"),
        ("  bedtime: '06:00'\n", "play_time.bedtime"),  # the night is over
        ("  bedtime: '12:00'\n", "play_time.bedtime"),
        ("  bedtime: '17:59'\n", "play_time.bedtime"),
        ("  bedtime: 24:00\n", "play_time.bedtime"),  # YAML base 60: 1440 minutes
        ("  bedtime: 2330\n", "play_time.bedtime"),  # the colon forgotten
        ("  bedtime: '23:30+07:00'\n", "play_time.bedtime"),  # a clock with a time zone
        ("  games: {}\n", GAMES_LINE),
        ("  games:\n    hsr: []\n", GAMES_LINE),
        ("  games:\n    wuwa: [Client-Win64-Shipping.exe]\n", GAMES_LINE),
        ("  games:\n    hsr: [StarRail]\n", GAMES_LINE),  # no .exe
        ("  games:\n    hsr: [StarRail.exe.lnk]\n", GAMES_LINE),
        ("  games:\n    hsr: [.exe]\n", GAMES_LINE),
        ("  games:\n    hsr: ['C:\\Games\\StarRail.exe']\n", GAMES_LINE),  # a path
        ("  games:\n    hsr: [Games/StarRail.exe]\n", GAMES_LINE),
        ("  games:\n    hsr: ['Star*.exe']\n", GAMES_LINE),  # a wildcard
        ("  games:\n    hsr: [' StarRail.exe']\n", GAMES_LINE),
        (f"  games:\n    hsr: [{'S' * 61}.exe]\n", GAMES_LINE),  # 65 characters
        ("  games:\n    hsr: [Game.exe]\n    zzz: [game.EXE]\n", GAMES_LINE),  # one name, two games
        ("  games:\n    hsr: [StarRail.exe, starrail.exe]\n", GAMES_LINE),
        ("  foreground_only: true\n", "play_time.foreground_only"),
    ],
)
def test_bad_play_time_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "play_time:\n" + lines))


def test_the_example_play_time_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# play_time:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).play_time == PlayTimeConfig()


def test_an_executable_name_of_64_characters_with_spaces_is_accepted(tmp_path):
    name = "Genshin Impact " + "x" * 45 + ".exe"
    assert len(name) == 64

    play = load_config(write_yaml(tmp_path, f"play_time:\n  games:\n    genshin: [{name}]\n"))

    assert play.play_time.games == {"genshin": [name]}


def test_the_focus_companion_is_on_without_a_push_by_default():
    focus = AppConfig().focus
    assert (focus.enabled, focus.push, focus.keep_days) == (True, False, 90)
    assert focus.path == Path("data/focus.json")


def test_focus_settings_load_from_yaml(tmp_path):
    text = "focus:\n  enabled: false\n  push: true\n  keep_days: 30\n  path: data/fokus.json\n"
    focus = load_config(write_yaml(tmp_path, text)).focus
    assert (focus.enabled, focus.push, focus.keep_days) == (False, True, 30)
    assert focus.path == Path("data/fokus.json")


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  keep_days: 0\n", "focus.keep_days"),
        ("  keep_days: 367\n", "focus.keep_days"),
        ("  minutes: 50\n", "focus.minutes"),  # the lengths are asked for by voice
    ],
)
def test_bad_focus_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "focus:\n" + lines))


def test_the_example_focus_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# focus:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).focus == FocusConfig()


GAME_ROUTINES = ROUTINE_DEVICES + (
    "routines:\n"
    "  redup:\n    steps: [{device: lampu_kamar, command: off}]\n"
    "  terang:\n    steps: [{device: lampu_kamar, command: on}]\n"
)


def test_game_mode_keeps_today_s_announcements_without_routines_by_default():
    game = AppConfig().game_mode
    assert (game.announcements, game.on_start, game.on_stop) == ("normal", None, None)


@pytest.mark.parametrize("announcements", ["normal", "hold", "phone"])
def test_game_mode_settings_load_from_yaml(tmp_path, announcements):
    text = GAME_ROUTINES + (
        f"game_mode:\n  announcements: {announcements}\n  on_start: redup\n  on_stop: terang\n"
    )

    game = load_config(write_yaml(tmp_path, text)).game_mode

    assert (game.announcements, game.on_start, game.on_stop) == (announcements, "redup", "terang")


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  announcements: mute\n", "game_mode.announcements"),
        ("  announcements: true\n", "game_mode.announcements"),
        ("  on_start: 3\n", "game_mode.on_start"),
        ("  games: [hsr]\n", "game_mode.games"),  # the games are play_time's
    ],
)
def test_bad_game_mode_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, GAME_ROUTINES + "game_mode:\n" + lines))


@pytest.mark.parametrize("key", ["on_start", "on_stop"])
def test_a_game_mode_routine_must_be_one_master_wrote(tmp_path, key):
    text = GAME_ROUTINES + f"game_mode:\n  {key}: gelap\n"

    with pytest.raises(ConfigError) as refused:
        load_config(write_yaml(tmp_path, text))

    assert f"game_mode.{key} names the routine 'gelap', which is not in routines" in str(
        refused.value
    )


@pytest.mark.parametrize(
    "lines",
    [
        "  announcements: hold\n",
        "  announcements: phone\n",
        "  on_start: redup\n",
        "  on_stop: terang\n",
    ],
)
def test_game_mode_needs_the_play_time_watcher(tmp_path, lines):
    text = GAME_ROUTINES + "play_time:\n  enabled: false\ngame_mode:\n" + lines

    with pytest.raises(ConfigError) as refused:
        load_config(write_yaml(tmp_path, text))

    assert (
        "game_mode needs play_time.enabled: true: it knows a game is running from the play "
        "time watcher"
    ) in str(refused.value)


def test_the_default_game_mode_loads_with_play_time_off(tmp_path):
    text = "play_time:\n  enabled: false\ngame_mode:\n  announcements: normal\n"

    assert load_config(write_yaml(tmp_path, text)).game_mode == GameModeConfig()


def test_the_example_game_mode_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# game_mode:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).game_mode == GameModeConfig()


def test_guest_mode_ends_by_itself_after_six_hours_by_default():
    assert AppConfig().guest_mode.max_hours == 6


def test_guest_mode_settings_load_from_yaml(tmp_path):
    text = "guest_mode:\n  max_hours: 3\n"

    assert load_config(write_yaml(tmp_path, text)).guest_mode.max_hours == 3


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  max_hours: 0\n", "guest_mode.max_hours"),
        ("  max_hours: 25\n", "guest_mode.max_hours"),
        ("  enabled: false\n", "guest_mode.enabled"),  # it is always there, by voice
    ],
)
def test_bad_guest_mode_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "guest_mode:\n" + lines))


def test_the_example_guest_mode_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# guest_mode:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).guest_mode == GuestModeConfig()


def test_her_voice_whispers_from_ten_at_night_until_six_at_fifty_percent_by_default():
    voice = AppConfig().voice
    assert voice.state_path == Path("data/voice.json")
    assert (voice.night.from_, voice.night.until, voice.night.level) == (time(22), time(6), 50)


def test_voice_settings_load_from_yaml_including_bare_yaml_times(tmp_path):
    text = (
        "voice:\n  state_path: data/suara.json\n  night:\n    from: 21:30\n"
        "    until: '05:00'\n    level: 30\n"
    )

    voice = load_config(write_yaml(tmp_path, text)).voice

    assert voice.state_path == Path("data/suara.json")
    assert (voice.night.from_, voice.night.until, voice.night.level) == (
        time(21, 30),
        time(5, 0),
        30,
    )


def test_the_whisper_window_may_lie_within_one_day(tmp_path):
    text = "voice:\n  night:\n    from: '13:00'\n    until: '15:00'\n"

    night = load_config(write_yaml(tmp_path, text)).voice.night

    assert (night.from_, night.until) == (time(13), time(15))


WINDOW_EMPTY = "voice.night.from must differ from voice.night.until"


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  night:\n    level: 9\n", "voice.night.level"),
        ("  night:\n    level: 101\n", "voice.night.level"),  # whisper mode only softens
        ("  night:\n    from: '06:00'\n", WINDOW_EMPTY),  # the default until
        ("  night:\n    from: '23:00'\n    until: '23:00'\n", WINDOW_EMPTY),
        ("  night:\n    from: 24:00\n", "voice.night.from"),  # 1440: read as UTC seconds
        ("  night:\n    until: 2330\n", "voice.night.until"),
        ("  night:\n    from_: '21:00'\n", "voice.night.from_"),  # only the key from
        ("  night:\n    enabled: true\n", "voice.night.enabled"),  # switched by voice
        ("  level: 80\n", "voice.level"),  # the level is the state file's
    ],
)
def test_bad_voice_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "voice:\n" + lines))


def test_the_example_voice_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# voice:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).voice == VoiceVolumeConfig()


def test_the_journal_is_on_without_the_nightly_question_by_default():
    journal = AppConfig().journal
    assert (journal.enabled, journal.max_chars, journal.max_notes_per_day) == (True, 500, 20)
    assert journal.ask_at is None
    assert (journal.path, journal.state_path) == (
        Path("data/journal.json"),
        Path("data/journal_question.json"),
    )


def test_journal_settings_load_from_yaml_with_a_bare_ask_at(tmp_path):
    text = (
        "journal:\n  enabled: false\n  path: data/jurnal.json\n  max_chars: 300\n"
        "  max_notes_per_day: 5\n  ask_at: 21:30\n  state_path: data/tanya.json\n"
    )
    journal = load_config(write_yaml(tmp_path, text)).journal
    assert (journal.enabled, journal.max_chars, journal.max_notes_per_day) == (False, 300, 5)
    assert journal.ask_at == time(21, 30)
    assert (journal.path, journal.state_path) == (
        Path("data/jurnal.json"),
        Path("data/tanya.json"),
    )


@pytest.mark.parametrize("ask_at", ["'12:00'", "'20:00'", "'23:59'"])
def test_a_nightly_question_from_noon_until_midnight_is_accepted(tmp_path, ask_at):
    journal = load_config(write_yaml(tmp_path, f"journal:\n  ask_at: {ask_at}\n")).journal
    assert journal.ask_at == time.fromisoformat(ask_at.strip("'"))


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  max_chars: 19\n", "journal.max_chars"),
        ("  max_chars: 2001\n", "journal.max_chars"),
        ("  max_notes_per_day: 0\n", "journal.max_notes_per_day"),
        ("  max_notes_per_day: 101\n", "journal.max_notes_per_day"),
        ("  ask_at: '00:30'\n", "journal.ask_at"),  # after midnight: the day is over
        ("  ask_at: '11:59'\n", "journal.ask_at"),
        ("  ask_at: 24:00\n", "journal.ask_at"),  # YAML base 60: 1440 minutes
        ("  ask_at: 2130\n", "journal.ask_at"),  # the colon forgotten
        ("  ask_at: '21:30+07:00'\n", "journal.ask_at"),  # a clock with a time zone
        ("  push: true\n", "journal.push"),  # the journal is never pushed
    ],
)
def test_bad_journal_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "journal:\n" + lines))


def test_the_example_journal_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# journal:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).journal == JournalConfig()


def test_japanese_lessons_are_off_by_default():
    japanese = AppConfig().japanese
    assert japanese.enabled is False
    assert japanese.path == Path("data/japanese.json")


def test_japanese_settings_load_from_yaml(tmp_path):
    text = "japanese:\n  enabled: true\n  path: data/jepang.json\n"
    japanese = load_config(write_yaml(tmp_path, text)).japanese
    assert japanese.enabled is True
    assert japanese.path == Path("data/jepang.json")


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  enabled: kadang\n", "japanese.enabled"),
        ("  words: 10\n", "japanese.words"),  # the list is built in
        ("  push: true\n", "japanese.push"),  # the greeting's own push carries the word
    ],
)
def test_bad_japanese_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "japanese:\n" + lines))


def test_the_example_japanese_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# japanese:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).japanese == JapaneseConfig()


def test_break_reminders_are_off_by_default_and_count_an_hour_with_five_minute_breaks():
    breaks = AppConfig().breaks
    assert (breaks.enabled, breaks.every_minutes, breaks.idle_reset_minutes) == (False, 60, 5)
    assert (breaks.during_games, breaks.push) == (False, False)
    assert (breaks.active_from, breaks.active_until) == (None, None)  # all day


def test_break_settings_load_from_yaml_including_bare_yaml_times(tmp_path):
    text = (
        "breaks:\n  enabled: true\n  every_minutes: 45\n  idle_reset_minutes: 3\n"
        "  during_games: true\n  push: true\n  active_from: 8:00\n  active_until: '02:00'\n"
    )

    breaks = load_config(write_yaml(tmp_path, text)).breaks

    assert (breaks.enabled, breaks.every_minutes, breaks.idle_reset_minutes) == (True, 45, 3)
    assert (breaks.during_games, breaks.push) == (True, True)
    # Across midnight: the owner plays late.
    assert (breaks.active_from, breaks.active_until) == (time(8, 0), time(2, 0))


BREAK_HOURS_TOGETHER = "breaks.active_from and breaks.active_until go together"
BREAK_HOURS_EMPTY = "breaks.active_from must differ from breaks.active_until"


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  every_minutes: 19\n", "breaks.every_minutes"),
        ("  every_minutes: 181\n", "breaks.every_minutes"),
        ("  idle_reset_minutes: 1\n", "breaks.idle_reset_minutes"),
        ("  idle_reset_minutes: 16\n", "breaks.idle_reset_minutes"),
        ("  active_from: '08:00'\n", BREAK_HOURS_TOGETHER),
        ("  active_until: '02:00'\n", BREAK_HOURS_TOGETHER),
        ("  active_from: '08:00'\n  active_until: '08:00'\n", BREAK_HOURS_EMPTY),
        ("  active_from: 24:00\n  active_until: '02:00'\n", "breaks.active_from"),  # UTC seconds
        ("  active_from: '08:00'\n  active_until: 2330\n", "breaks.active_until"),
        ("  active_from: '08:00+07:00'\n  active_until: '02:00'\n", "breaks.active_from"),
        ("  minutes: 60\n", "breaks.minutes"),
    ],
)
def test_bad_break_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "breaks:\n" + lines))


def test_the_example_breaks_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# breaks:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).breaks == BreaksConfig()


def test_prayer_times_are_off_by_default_with_a_two_minute_ihtiyath_and_all_five_reminders():
    prayer = AppConfig().prayer
    assert (prayer.enabled, prayer.latitude, prayer.longitude) == (False, None, None)
    assert (prayer.ihtiyath_minutes, prayer.minutes_before, prayer.push) == (2, 0, False)
    assert prayer.remind == ["subuh", "dzuhur", "ashar", "maghrib", "isya"]
    assert prayer.state_path == Path("data/prayer.json")


def test_prayer_settings_load_from_yaml(tmp_path):
    text = (
        "prayer:\n  enabled: true\n  latitude: -6.9175\n  longitude: 107.6191\n"
        "  ihtiyath_minutes: 0\n  remind: [maghrib, subuh]\n  minutes_before: 10\n"
        "  push: true\n  state_path: data/sholat.json\n"
    )

    prayer = load_config(write_yaml(tmp_path, text)).prayer

    assert (prayer.enabled, prayer.latitude, prayer.longitude) == (True, -6.9175, 107.6191)
    assert (prayer.ihtiyath_minutes, prayer.minutes_before, prayer.push) == (0, 10, True)
    assert prayer.remind == ["maghrib", "subuh"]
    assert prayer.state_path == Path("data/sholat.json")


def test_prayer_reminders_may_be_left_empty_for_the_times_on_request_only(tmp_path):
    prayer = load_config(write_yaml(tmp_path, "prayer:\n  enabled: true\n  remind: []\n")).prayer

    assert prayer.remind == []


PRAYER_COORDINATES_TOGETHER = "prayer.latitude and prayer.longitude go together"


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  latitude: -6.9\n", PRAYER_COORDINATES_TOGETHER),
        ("  longitude: 107.6\n", PRAYER_COORDINATES_TOGETHER),
        ("  latitude: -91\n  longitude: 107.6\n", "prayer.latitude"),
        ("  latitude: -6.9\n  longitude: 181\n", "prayer.longitude"),
        ("  ihtiyath_minutes: -1\n", "prayer.ihtiyath_minutes"),
        ("  ihtiyath_minutes: 6\n", "prayer.ihtiyath_minutes"),
        ("  minutes_before: -1\n", "prayer.minutes_before"),
        ("  minutes_before: 31\n", "prayer.minutes_before"),
        ("  remind: [dhuha]\n", "prayer.remind"),
        ("  remind: [subuh, subuh]\n", "each prayer may be listed once"),
        ("  jumat: true\n", "prayer.jumat"),
    ],
)
def test_bad_prayer_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "prayer:\n" + lines))


def test_the_example_prayer_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# prayer:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    assert load_config(write_yaml(tmp_path, written_out)).prayer == PrayerConfig()


def test_web_search_is_on_for_indonesia_by_default():
    search = AppConfig().web_search
    assert (search.enabled, search.max_uses) == (True, 3)
    assert (search.country, search.timezone, search.city) == ("ID", "Asia/Jakarta", None)


def test_free_offers_default_to_games_and_a_weekly_ai_scan():
    offers = AppConfig().free_offers
    assert (offers.enabled, offers.games, offers.ai, offers.notices) == (True, True, True, True)
    assert offers.game_platforms == ["epic", "steam", "gog"]
    assert (offers.games_poll_hours, offers.ai_scan_days, offers.ai_max_searches) == (6, 7, 10)
    assert offers.state_path == Path("data/free_offers.json")


def test_web_searches_cost_ten_dollars_per_thousand_by_default():
    assert AppConfig().usage.web_search_per_1000_usd == 10.0


def test_web_search_and_free_offers_load_from_yaml(tmp_path):
    text = (
        "web_search:\n  max_uses: 5\n  city: Cianjur\n"
        "free_offers:\n  game_platforms: [epic, itchio]\n  ai_scan_days: 14\n  notices: false\n"
        "usage:\n  web_search_per_1000_usd: 12.5\n"
    )
    config = load_config(write_yaml(tmp_path, text))
    assert (config.web_search.max_uses, config.web_search.city) == (5, "Cianjur")
    assert config.free_offers.game_platforms == ["epic", "itchio"]
    assert (config.free_offers.ai_scan_days, config.free_offers.notices) == (14, False)
    assert config.usage.web_search_per_1000_usd == 12.5


@pytest.mark.parametrize(
    ("section", "lines"),
    [
        ("web_search", "  max_uses: 0\n"),
        ("web_search", "  max_uses: 11\n"),
        ("web_search", "  country: id\n"),
        ("web_search", "  country: IDN\n"),
        ("web_search", "  timezone: Mars/Olympus\n"),
        ("web_search", "  city: ''\n"),
        ("web_search", "  allowed_domains: [example.com]\n"),
        ("free_offers", "  game_platforms: [epic, xbox]\n"),
        ("free_offers", "  game_platforms: [epic, epic]\n"),
        ("free_offers", "  game_platforms: []\n"),
        ("free_offers", "  games_poll_hours: 0\n"),
        ("free_offers", "  games_poll_hours: 49\n"),
        ("free_offers", "  ai_scan_days: 0\n"),
        ("free_offers", "  ai_scan_days: 31\n"),
        ("free_offers", "  ai_max_searches: 0\n"),
        ("free_offers", "  ai_max_searches: 21\n"),
        ("usage", "  web_search_per_1000_usd: -1\n"),
    ],
)
def test_bad_web_search_and_free_offers_settings_are_refused(tmp_path, section, lines):
    with pytest.raises(ConfigError, match=section):
        load_config(write_yaml(tmp_path, f"{section}:\n{lines}"))


def test_redeem_codes_are_watched_and_redeemed_only_when_asked_by_default():
    codes = AppConfig().redeem_codes
    assert (codes.enabled, codes.poll_hours, codes.auto_redeem, codes.notices) == (
        True,
        6,
        False,
        True,
    )
    assert codes.state_path == Path("data/redeem_codes.json")


def test_redeem_codes_settings_load_from_yaml(tmp_path):
    text = "redeem_codes:\n  poll_hours: 12\n  auto_redeem: true\n"
    codes = load_config(write_yaml(tmp_path, text)).redeem_codes
    assert (codes.poll_hours, codes.auto_redeem) == (12, True)


@pytest.mark.parametrize(
    "lines",
    ["  poll_hours: 0\n", "  poll_hours: 49\n", "  auto_redeem: maybe\n", "  games: [hsr]\n"],
)
def test_bad_redeem_codes_settings_are_refused(tmp_path, lines):
    with pytest.raises(ConfigError, match="redeem_codes"):
        load_config(write_yaml(tmp_path, "redeem_codes:\n" + lines))


def test_the_weekly_game_report_is_on_sunday_at_seven_in_the_evening_and_pushed():
    weekly = AppConfig().weekly_report
    assert (weekly.enabled, weekly.day, weekly.time, weekly.push) == (
        True,
        "sunday",
        time(19, 0),
        True,
    )
    assert weekly.state_path == Path("data/weekly_report.json")


def test_weekly_report_settings_are_read_including_a_bare_yaml_time(tmp_path):
    text = (
        "weekly_report:\n  enabled: false\n  day: saturday\n  time: 20:30\n  push: false\n"
        "  state_path: laporan/minggu.json\n"
    )
    weekly = load_config(write_yaml(tmp_path, text)).weekly_report
    assert (weekly.enabled, weekly.day, weekly.time, weekly.push) == (
        False,
        "saturday",
        time(20, 30),
        False,
    )
    assert weekly.state_path == Path("laporan/minggu.json")


@pytest.mark.parametrize(
    "day", ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
)
def test_every_english_weekday_name_is_a_report_day(tmp_path, day):
    text = f"weekly_report:\n  day: {day}\n"
    assert load_config(write_yaml(tmp_path, text)).weekly_report.day == day


@pytest.mark.parametrize(
    ("lines", "named"),
    [
        ("  day: minggu\n", "weekly_report.day"),  # config values are English identifiers
        ("  day: Sunday\n", "weekly_report.day"),
        ("  day: 6\n", "weekly_report.day"),
        ("  time: '25:00'\n", "weekly_report.time"),
        ("  time: sore\n", "weekly_report.time"),
        ("  games: [hsr]\n", "weekly_report.games"),  # the games are gacha.games
    ],
)
def test_bad_weekly_report_settings_are_refused_naming_the_setting(tmp_path, lines, named):
    with pytest.raises(ConfigError, match=re.escape(named)):
        load_config(write_yaml(tmp_path, "weekly_report:\n" + lines))


def test_the_todo_list_is_on_with_room_for_a_hundred_tasks_by_default():
    todo = AppConfig().todo
    assert (todo.enabled, todo.path) == (True, Path("data/todo.json"))
    assert (todo.max_tasks, todo.max_chars, todo.keep_done_days) == (100, 200, 7)


@pytest.mark.parametrize(
    "lines",
    [
        "  max_tasks: 0\n",
        "  max_tasks: 501\n",
        "  max_chars: 19\n",
        "  max_chars: 1001\n",
        "  keep_done_days: -1\n",
        "  keep_done_days: 91\n",
        "  reminders: true\n",
    ],
)
def test_bad_todo_settings_are_refused(tmp_path, lines):
    with pytest.raises(ConfigError, match="todo"):
        load_config(write_yaml(tmp_path, "todo:\n" + lines))


def test_music_opens_youtube_by_default():
    music = AppConfig().music
    assert (music.enabled, music.service) == (True, "youtube")


def test_music_can_open_youtube_music_instead(tmp_path):
    text = "music:\n  service: youtube_music\n"
    assert load_config(write_yaml(tmp_path, text)).music.service == "youtube_music"


@pytest.mark.parametrize("lines", ["  service: spotify\n", "  autoplay: true\n"])
def test_bad_music_settings_are_refused(tmp_path, lines):
    with pytest.raises(ConfigError, match="music"):
        load_config(write_yaml(tmp_path, "music:\n" + lines))


# tepira (v1.24): the token is a sentinel that must never show up in an error or a repr.
GOOD_TEPIRA_TOKEN = "tpcy_SECRETTEPIRA" + "Ab3-_" * 6 + "Z"


def test_tepira_is_off_by_default_without_a_token_or_writes():
    tepira = AppConfig().tepira
    assert (tepira.enabled, tepira.token, tepira.writes, tepira.default_wallet) == (
        False,
        None,
        False,
        None,
    )
    assert tepira.url == "https://tepira.asepsunarya.com"


def test_the_example_config_leaves_tepira_off():
    assert load_config(ROOT / "config.example.yaml").tepira == TepiraConfig()


def test_the_example_tepira_block_is_valid_once_its_comments_are_filled_in(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    block = example[example.index("\ntepira:\n") + 1 :].split("\n\n", 1)[0]
    filled = re.sub(r"(?m)^  enabled: false", "  enabled: true", block)
    filled = re.sub(r"(?m)^  writes: false", "  writes: true", filled)
    filled = re.sub(r"(?m)^  # default_wallet:", "  default_wallet:", filled)
    filled = re.sub(r"(?m)^  # token: \S+", f"  token: {GOOD_TEPIRA_TOKEN}", filled)

    tepira = load_config(write_yaml(tmp_path, filled)).tepira

    assert (tepira.enabled, tepira.writes, tepira.default_wallet) == (True, True, "GoPay")
    assert tepira.token.get_secret_value() == GOOD_TEPIRA_TOKEN


def test_tepira_token_is_kept_secret(tmp_path):
    text = f"tepira:\n  enabled: true\n  token: {GOOD_TEPIRA_TOKEN}\n"
    config = load_config(write_yaml(tmp_path, text))
    assert config.tepira.token.get_secret_value() == GOOD_TEPIRA_TOKEN
    for shown in (repr(config.tepira), repr(config), config.model_dump_json()):
        assert "SECRETTEPIRA" not in shown


def test_enabled_tepira_needs_a_token(tmp_path):
    with pytest.raises(ConfigError, match="tepira.token") as caught:
        load_config(write_yaml(tmp_path, "tepira:\n  enabled: true\n"))
    assert "waifu tepira token" in str(caught.value)


@pytest.mark.parametrize(
    "token",
    [
        "'tpcy_SECRETTEPIRA'",
        f"'{GOOD_TEPIRA_TOKEN}x'",
        f"'{GOOD_TEPIRA_TOKEN.replace('tpcy_', 'tpco_')}'",
        f"'{GOOD_TEPIRA_TOKEN.replace('-', '+')}'",
        f'"{GOOD_TEPIRA_TOKEN}\n"',
    ],
)
def test_tepira_token_must_have_the_cyrene_form_and_is_never_echoed(tmp_path, token):
    with pytest.raises(ConfigError, match="tpcy_") as caught:
        load_config(write_yaml(tmp_path, f"tepira:\n  token: {token}\n"))
    assert "SECRETTEPIRA" not in str(caught.value)


def test_tepira_url_is_a_plain_https_host_without_the_trailing_slash(tmp_path):
    for written, kept in [
        ("https://tepira.asepsunarya.com/", "https://tepira.asepsunarya.com"),
        ("HTTPS://Tepira.Example.com", "https://tepira.example.com"),
    ]:
        text = f"tepira:\n  url: {written}\n"
        assert load_config(write_yaml(tmp_path, text)).tepira.url == kept


@pytest.mark.parametrize(
    "url",
    [
        "http://tepira.asepsunarya.com",
        "tepira.asepsunarya.com",
        "https://tepira.asepsunarya.com/api",
        "https://user@tepira.asepsunarya.com",
        "https://tepira.asepsunarya.com:8443",
        "'https://tepira.asepsunarya.com?x=1'",
        "https://localhost",
    ],
)
def test_tepira_url_is_refused_unless_https_and_a_host_name_only(tmp_path, url):
    # The token goes to this address, so nothing may hide another host or path in it.
    with pytest.raises(ConfigError, match="tepira.url"):
        load_config(write_yaml(tmp_path, f"tepira:\n  url: {url}\n"))


def test_tepira_writes_need_a_default_wallet(tmp_path):
    with pytest.raises(ConfigError, match="tepira.default_wallet"):
        load_config(write_yaml(tmp_path, "tepira:\n  writes: true\n"))
    text = "tepira:\n  writes: true\n  default_wallet: '  GoPay  '\n"
    assert load_config(write_yaml(tmp_path, text)).tepira.default_wallet == "GoPay"


@pytest.mark.parametrize("wallet", ["''", "'   '", "'" + "x" * 61 + "'", '"Go\tPay"'])
def test_tepira_default_wallet_is_a_short_printable_name(tmp_path, wallet):
    with pytest.raises(ConfigError, match="tepira.default_wallet"):
        load_config(write_yaml(tmp_path, f"tepira:\n  default_wallet: {wallet}\n"))


def test_tepira_default_wallet_may_be_a_long_name_in_full(tmp_path):
    # tepira shows it cut to 40 characters; Cyrene matches it the way tepira cuts it.
    name = "Tabungan Rencana Pendidikan Anak Pertama Kita Bersama Ibu"

    config = load_config(write_yaml(tmp_path, f"tepira:\n  default_wallet: {name}\n"))

    assert config.tepira.default_wallet == name


def test_unknown_tepira_settings_are_refused(tmp_path):
    with pytest.raises(ConfigError, match="write_token"):
        load_config(write_yaml(tmp_path, "tepira:\n  write_token: x\n"))


def test_the_example_weekly_report_block_written_out_holds_the_defaults(tmp_path):
    example = (ROOT / "config.example.yaml").read_text(encoding="utf-8")
    commented = example[example.index("\n# weekly_report:\n") + 1 :].split("\n\n", 1)[0]
    written_out = "\n".join(line.removeprefix("# ") for line in commented.splitlines())

    weekly = load_config(write_yaml(tmp_path, written_out)).weekly_report
    assert weekly == WeeklyReportConfig()


# --- the microphones in order of preference (v1.47) -------------------------------------------


def test_without_input_device_cyrene_prefers_no_mic_but_the_windows_default():
    audio = AppConfig().audio

    assert audio.input_preferences == []
    assert audio.override_path == Path("data/voice_override.json")


@pytest.mark.parametrize(
    ("written", "preferences"),
    [
        ('"Headset (Polaris-BT), MME"', ["Headset (Polaris-BT), MME"]),
        ("3", [3]),
        (
            '["Headset (Polaris-BT), MME", "Microphone (USB), MME", 2]',
            ["Headset (Polaris-BT), MME", "Microphone (USB), MME", 2],
        ),
    ],
)
def test_input_device_names_one_mic_or_several_in_order(tmp_path, written, preferences):
    config = load_config(write_yaml(tmp_path, f"audio:\n  input_device: {written}\n"))

    assert config.audio.input_preferences == preferences


def test_an_empty_list_of_mics_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="input_device"):
        load_config(write_yaml(tmp_path, "audio:\n  input_device: []\n"))


def test_the_voice_override_file_can_live_elsewhere(tmp_path):
    text = "audio:\n  override_path: data/suara_hp.json\n"

    assert load_config(write_yaml(tmp_path, text)).audio.override_path == Path("data/suara_hp.json")
