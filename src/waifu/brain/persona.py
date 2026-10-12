"""The companion's system prompt and the time stamp on each user turn.

The system prompt must stay byte-identical for a whole session so the prompt
cache keeps hitting: it holds only stable facts (persona text, device list
without live state). The current time travels in each user turn instead.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from waifu.brain.tools import device_lines
from waifu.config import ConfigError
from waifu.devices.registry import DeviceRegistry

# Indexed by datetime.weekday(); spelled out so the OS locale never leaks in.
WEEKDAYS_ID = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")

_TIME_NOTE = (
    "Setiap pesan dari Master diawali waktu saat ini dalam kurung siku, "
    "dengan format [Hari, DD-MM-YYYY HH:MM]. Pakai itu kalau perlu tahu hari atau jam, "
    "tapi jangan menulis ulang tanda waktu itu di jawabanmu."
)


def load_persona(path: Path | str) -> str:
    persona_path = Path(path)
    try:
        return persona_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Persona file {persona_path} not found. "
            "Point assistant.persona_file in config.yaml at an existing file "
            "(the default is config/persona.md)."
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Persona file {persona_path} is not valid UTF-8: {exc}") from exc


def build_system_prompt(persona_text: str, assistant_name: str, registry: DeviceRegistry) -> str:
    """Persona with `{name}` filled in, then the device list, then how to read the time stamp."""
    # str.replace, not str.format: persona text may contain other braces.
    persona = persona_text.replace("{name}", assistant_name).strip()
    devices = "\n".join(device_lines(registry)) or "(belum ada perangkat)"
    device_section = (
        f"Perangkat di rumah:\n{devices}\n"
        "Daftar ini tidak memuat status terkini; panggil list_devices kalau perlu tahu "
        "perangkat mana yang sedang menyala."
    )
    return "\n\n".join([persona, device_section, _TIME_NOTE])


def format_user_turn(text: str, now: datetime) -> str:
    """Prefix `text` with "[Senin, 12-10-2026 07:30]" so the model knows the current time."""
    return f"[{WEEKDAYS_ID[now.weekday()]}, {now:%d-%m-%Y %H:%M}] {text}"
