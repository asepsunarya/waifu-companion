from dataclasses import FrozenInstanceError, fields

import pytest

from waifu.gacha.zenless import ZzzAgent, ZzzAgentDetail, ZzzDisc, ZzzWEngine

# ---- values -----------------------------------------------------------------------------------


def test_the_zzz_values_are_frozen():
    w_engine = ZzzWEngine("Deep Sea Visitor", level=60, refinement=1, rarity="S")
    agent = ZzzAgent(
        ref=1191,
        name="Ellen",
        full_name="Ellen Joe",
        rarity="S",
        level=60,
        mindscape=2,
        element="Ice",
        specialty="Attack",
    )
    disc = ZzzDisc(slot=1, set_name="Polar Metal", main_stat="HP", value="2200", level=15)
    detail = ZzzAgentDetail(agent, w_engine, stats=(("HP", "8000"),), discs=(disc,), sets=())

    for value in (w_engine, agent, disc, detail):
        with pytest.raises(FrozenInstanceError):
            setattr(value, fields(value)[0].name, None)
