from datetime import datetime
from pathlib import Path

import pytest

from waifu.brain.persona import build_system_prompt, format_user_turn, load_persona
from waifu.brain.tools import device_lines
from waifu.config import ConfigError
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry

ROOT = Path(__file__).resolve().parent.parent


def house() -> DeviceRegistry:
    return DeviceRegistry(
        [
            MockDevice(
                "lampu_kamar",
                "Lampu kamar",
                aliases=["lampu", "lampu utama"],
                commands=["on", "off", "toggle", "brightness"],
                kind="light",
            ),
            MockDevice(
                "ac",
                "AC Samsung",
                aliases=["ac"],
                commands=["on", "off", *[f"temp_{t}" for t in range(16, 31)]],
                kind="ir",
            ),
        ]
    )


# load_persona


def test_load_persona_reads_utf8_text(tmp_path):
    path = tmp_path / "persona.md"
    path.write_text("Kamu adalah {name}. はい, Master!\n", encoding="utf-8")

    assert load_persona(path) == "Kamu adalah {name}. はい, Master!\n"


def test_committed_persona_has_a_name_placeholder():
    assert "{name}" in load_persona(ROOT / "config" / "persona.md")


def test_committed_persona_uses_the_game_tools_and_never_pushes_spending():
    persona = load_persona(ROOT / "config" / "persona.md")

    games = persona.split("Game HoYoverse", 1)[1]

    assert "game_status" in games
    assert "gacha_history" in games
    assert "top-up" in games


def test_committed_persona_leaves_the_question_to_the_code_that_holds_turning_the_pc_off():
    # control_device refuses the first call until Master's next words say yes, and the code
    # itself asks her; a model that also asked would ask twice, and one that retried instead
    # would only be refused again.
    persona = load_persona(ROOT / "config" / "persona.md")

    house_rules = persona.split("Mengendalikan rumah", 1)[1].split("\n\n", 1)[0]

    assert "konfirmasi Master dulu" in house_rules
    assert "colokan yang mengalirkan listrik ke PC" in house_rules
    assert "needs_confirmation" in house_rules
    assert "tanpa bertanya dulu" in house_rules
    assert "jangan menanyakannya lagi" in house_rules


def test_missing_persona_file_is_a_config_error_naming_the_path(tmp_path):
    with pytest.raises(ConfigError, match="persona_hilang.md"):
        load_persona(tmp_path / "persona_hilang.md")


def test_persona_file_that_is_not_utf8_is_a_config_error(tmp_path):
    path = tmp_path / "persona.md"
    path.write_bytes("Kamu adalah {name} — ceria".encode("cp1252"))

    with pytest.raises(ConfigError, match="UTF-8"):
        load_persona(path)


# build_system_prompt


def test_system_prompt_fills_every_name_placeholder():
    prompt = build_system_prompt("Kamu adalah {name}. Panggil aku {name}.", "Cyrene", house())

    assert prompt.startswith("Kamu adalah Cyrene. Panggil aku Cyrene.")
    assert "{name}" not in prompt


def test_other_braces_in_the_persona_are_left_alone():
    persona = 'Kamu {name}. Contoh JSON: {"a": 1}. Jangan ganti {mood} atau }{.'

    prompt = build_system_prompt(persona, "Cyrene", house())

    assert 'Kamu Cyrene. Contoh JSON: {"a": 1}. Jangan ganti {mood} atau }{.' in prompt


def test_system_prompt_lists_devices_after_the_persona():
    registry = house()

    prompt = build_system_prompt("Kamu adalah {name}.", "Cyrene", registry)

    persona_end = prompt.index("Kamu adalah Cyrene.")
    section = prompt.index("Perangkat di rumah:")
    assert persona_end < section
    expected_lines = "\n".join(device_lines(registry))
    assert f"Perangkat di rumah:\n{expected_lines}" in prompt


def test_system_prompt_with_no_devices_says_so():
    prompt = build_system_prompt("Kamu adalah {name}.", "Cyrene", DeviceRegistry([]))

    assert "Perangkat di rumah:\n(belum ada perangkat)" in prompt


def test_system_prompt_ends_with_a_note_about_the_bracketed_time():
    prompt = build_system_prompt("Kamu adalah {name}.", "Cyrene", house())

    last_section = prompt.split("\n\n")[-1]
    assert prompt.index("Perangkat di rumah:") < prompt.index(last_section)
    assert "kurung siku" in last_section


def test_time_note_does_not_forbid_the_emotion_tag_brackets():
    # persona.md asks for a leading [senang]-style tag; the time note must only forbid
    # echoing the timestamp, not every square bracket.
    prompt = build_system_prompt("Kamu adalah {name}.", "Cyrene", house())

    assert "jangan menulis kurung siku" not in prompt
    assert "tanda waktu" in prompt.split("\n\n")[-1]


def test_system_prompt_is_byte_identical_for_the_same_input_and_ignores_live_state():
    persona = load_persona(ROOT / "config" / "persona.md")
    registry = house()
    first = build_system_prompt(persona, "Cyrene", registry)

    registry.execute("lampu_kamar", "on")
    registry.execute("ac", "temp", 24)

    assert build_system_prompt(persona, "Cyrene", registry) == first
    assert build_system_prompt(persona, "Cyrene", house()) == first


def test_committed_persona_becomes_a_prompt_with_the_configured_name():
    persona = load_persona(ROOT / "config" / "persona.md")

    prompt = build_system_prompt(persona, "Yuki", house())

    assert "Kamu adalah Yuki" in prompt
    assert "Cyrene" not in prompt


# format_user_turn


def test_user_turn_is_prefixed_with_indonesian_weekday_date_and_time():
    turn = format_user_turn("nyalakan lampu kamar", datetime(2026, 10, 12, 7, 30))

    assert turn == "[Senin, 12-10-2026 07:30] nyalakan lampu kamar"


@pytest.mark.parametrize(
    ("day", "weekday"),
    [
        (12, "Senin"),
        (13, "Selasa"),
        (14, "Rabu"),
        (15, "Kamis"),
        (16, "Jumat"),
        (17, "Sabtu"),
        (18, "Minggu"),
    ],
)
def test_every_weekday_uses_its_indonesian_name(day, weekday):
    turn = format_user_turn("halo", datetime(2026, 10, day, 20, 0))

    assert turn == f"[{weekday}, {day}-10-2026 20:00] halo"


def test_day_month_hour_and_minute_are_zero_padded():
    assert format_user_turn("halo", datetime(2026, 1, 5, 9, 5)) == "[Senin, 05-01-2026 09:05] halo"
