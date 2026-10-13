from datetime import time
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
