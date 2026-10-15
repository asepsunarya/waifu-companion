import dataclasses
from datetime import UTC, date, datetime

import pytest

from waifu.offers.values import AI_ACCESS, AI_KINDS, AiOffer, GameGiveaway


def test_a_game_giveaway_holds_exact_times_or_only_an_end_date():
    epic = GameGiveaway(
        "epic:6a84",
        "Bad Cheese",
        "Epic Games Store",
        "Epic Games Store",
        12.99,
        datetime(2026, 10, 15, 15, tzinfo=UTC),
        datetime(2026, 10, 22, 15, tzinfo=UTC),
        None,
        "https://store.epicgames.com/en-US/p/bad-cheese-4256d9",
    )
    steam = GameGiveaway(
        "gamerpower:3801",
        "Some Game",
        "Steam",
        "GamerPower.com",
        None,
        None,
        None,
        date(2026, 10, 30),
        "https://www.gamerpower.com/open/some-game",
    )

    assert epic.ends_at is not None and epic.ends_on is None
    assert steam.ends_at is None and steam.ends_on == date(2026, 10, 30)
    with pytest.raises(dataclasses.FrozenInstanceError):
        epic.title = "Other"  # type: ignore[misc]


def test_an_ai_offer_says_what_it_is_and_whether_to_claim_or_apply():
    offer = AiOffer(
        "a1b2",
        "Claude for Startups",
        "Anthropic",
        "startup_program",
        "apply",
        "VC-backed startups",
        None,
        "https://www.anthropic.com/startups",
        "API credits for early-stage startups.",
        datetime(2026, 10, 16, 9, tzinfo=UTC),
    )

    assert offer.kind in AI_KINDS and offer.access in AI_ACCESS
    assert AI_KINDS == (
        "free_credits",
        "free_tier",
        "startup_program",
        "student_program",
        "trial",
        "other",
    )
    assert AI_ACCESS == ("claim", "apply")
