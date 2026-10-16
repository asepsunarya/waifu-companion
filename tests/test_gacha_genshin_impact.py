from dataclasses import FrozenInstanceError, fields

import pytest

from waifu.gacha.genshin_impact import (
    GenshinArtifact,
    GenshinCharacter,
    GenshinCharacterDetail,
    GenshinWeapon,
)

# ---- values -----------------------------------------------------------------------------------


def test_the_genshin_values_are_frozen():
    weapon = GenshinWeapon("Splendor of Tranquil Waters", level=90, refinement=1, rarity=5)
    character = GenshinCharacter(
        ref=10000089,
        name="Furina",
        rarity=5,
        level=90,
        constellation=2,
        element="Hydro",
        weapon_type="Sword",
        weapon=weapon,
    )
    artifact = GenshinArtifact("Plume of Death", "Golden Troupe", "ATK", "311", level=20)
    detail = GenshinCharacterDetail(
        character,
        weapon_stats=(("Base ATK", "542"), ("CRIT DMG", "88.2%")),
        stats=(("Max HP", "35307"),),
        artifacts=(artifact,),
        sets=(),
    )

    for value in (weapon, character, artifact, detail):
        with pytest.raises(FrozenInstanceError):
            setattr(value, fields(value)[0].name, None)
