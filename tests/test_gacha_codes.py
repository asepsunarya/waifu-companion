import dataclasses
from datetime import UTC, datetime

import pytest

from waifu.gacha.codes import REDEEM_OUTCOMES, RedeemCode, RedeemResult


def test_a_code_holds_its_game_rewards_and_when_it_was_first_seen():
    code = RedeemCode("hsr", "5S6ZHRWTDNJB", "60 stellar jade", datetime(2026, 10, 17, tzinfo=UTC))

    assert (code.game, code.code, code.rewards) == ("hsr", "5S6ZHRWTDNJB", "60 stellar jade")
    with pytest.raises(dataclasses.FrozenInstanceError):
        code.code = "OTHER"  # type: ignore[misc]


def test_a_result_says_what_happened_to_a_code():
    result = RedeemResult("genshin", "GENSHINGIFT", "claimed", "Kode ini sudah pernah dipakai.")

    assert result.outcome in REDEEM_OUTCOMES
    assert REDEEM_OUTCOMES == (
        "redeemed",
        "claimed",
        "expired",
        "invalid",
        "region",
        "level",
        "cooldown",
        "cookie",
        "error",
    )
