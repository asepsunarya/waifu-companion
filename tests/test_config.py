import re
from datetime import time
from pathlib import Path

import pytest

from waifu.config import (
    AppConfig,
    ConfigError,
    FallbackTtsConfig,
    RainAlertConfig,
    TepiraConfig,
    VersionNoticeConfig,
    WeeklyReportConfig,
    load_config,
)

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


@pytest.mark.parametrize("gender", ["female", "male"])
def test_the_fallback_rvc_pitch_is_bounded(tmp_path, gender):
    text = f"tts:\n  fallback:\n    rvc_pitch:\n      {gender}: 25\n"
    with pytest.raises(ConfigError, match=gender):
        load_config(write_yaml(tmp_path, text))


def test_the_example_config_spells_out_the_fallback_voice_defaults():
    fallback = load_config(ROOT / "config.example.yaml").tts.fallback
    assert fallback == FallbackTtsConfig()
    assert "  fallback:\n" in (ROOT / "config.example.yaml").read_text(encoding="utf-8")


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
