"""What Cyrene knows about Master's Genshin Impact account, as plain values.

genshin.py's models (taken as `Any`) become these frozen dataclasses for the HoYoLAB side,
and the brain's game data tools turn them into compact JSON. Nothing here imports
genshin.py (the module is not called `genshin` for that reason), and nothing holds the
uid, the nickname, icons, or descriptions; a character's `ref`, the game's id for it, is
kept only to ask HoYoLAB for its detail and never reaches a result.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenshinWeapon:
    name: str
    level: int
    refinement: int
    rarity: int


@dataclass(frozen=True)
class GenshinCharacter:
    ref: int  # the game's character id: only to ask for the detail, never in a result
    name: str
    rarity: int
    level: int
    constellation: int
    element: str
    weapon_type: str
    weapon: GenshinWeapon  # genshin.py requires a weapon on every character


@dataclass(frozen=True)
class GenshinArtifact:
    piece: str  # the slot's name, such as "Plume of Death"
    set_name: str
    main_stat: str
    value: str
    level: int


@dataclass(frozen=True)
class GenshinCharacterDetail:
    character: GenshinCharacter
    weapon_stats: tuple[tuple[str, str], ...]  # (stat name, final value): main, then sub
    stats: tuple[tuple[str, str], ...]  # (stat name, final value as the game shows it)
    artifacts: tuple[GenshinArtifact, ...]
    sets: tuple[tuple[str, int], ...]  # (set name, pieces worn): 2 or more pieces
