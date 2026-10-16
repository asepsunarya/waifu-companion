"""What Cyrene knows about Master's Zenless Zone Zero account, as plain values.

genshin.py's models (taken as `Any`) become these frozen dataclasses for the HoYoLAB side,
and the brain's game data tools turn them into compact JSON. Nothing here imports
genshin.py, and nothing holds the uid, the nickname, the avatar, icons, or descriptions;
an agent's `ref`, the game's id for it, is kept only to ask HoYoLAB for its detail and
never reaches a result.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZzzWEngine:
    name: str
    level: int
    refinement: int  # genshin.py's name for the W-Engine's duplicate level, 1-5
    rarity: str  # "S", "A", or "B"


@dataclass(frozen=True)
class ZzzAgent:
    ref: int  # the game's agent id: only to ask for the detail, never in a result
    name: str
    full_name: str
    rarity: str  # "S" or "A"
    level: int
    mindscape: int  # Mindscape Cinema
    element: str
    specialty: str


@dataclass(frozen=True)
class ZzzDisc:
    slot: int  # 1-6
    set_name: str
    main_stat: str
    value: str
    level: int


@dataclass(frozen=True)
class ZzzAgentDetail:
    agent: ZzzAgent
    w_engine: ZzzWEngine | None
    stats: tuple[tuple[str, str], ...]  # (stat name, final value as the game shows it)
    discs: tuple[ZzzDisc, ...]
    sets: tuple[tuple[str, int], ...]  # (set name, pieces worn): 2 or more pieces
