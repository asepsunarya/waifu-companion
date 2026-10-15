"""Redeem codes for the gacha buddy's games: what a code gives and what redeeming it did."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from waifu.config import GameId

# What redeeming a code on HoYoLAB did. All but cooldown, cookie, and error are final: the code
# is not tried again.
REDEEM_OUTCOMES = (
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


@dataclass(frozen=True)
class RedeemCode:
    """A code for one of Master's games and what it gives."""

    game: GameId
    code: str
    rewards: str
    first_seen: datetime


@dataclass(frozen=True)
class RedeemResult:
    """What redeeming one code did; `message` is Indonesian, for her to say."""

    game: GameId
    code: str
    outcome: str  # one of REDEEM_OUTCOMES
    message: str
