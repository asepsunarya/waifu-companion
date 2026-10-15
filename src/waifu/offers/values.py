"""The offers Cyrene tells the owner about: free games and AI programs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# What an AI offer gives, and whether it is claimed right away or applied for.
AI_KINDS = ("free_credits", "free_tier", "startup_program", "student_program", "trial", "other")
AI_ACCESS = ("claim", "apply")


@dataclass(frozen=True)
class GameGiveaway:
    """A game free to keep for a while.

    Epic gives exact times (`starts_at`, `ends_at`, aware; a start in the future means the
    game is not free yet); GamerPower's times are not precise, so it gives only `ends_on`.
    """

    id: str  # "epic:<id>" or "gamerpower:<id>"
    title: str
    platform: str
    source: str  # who listed it: "Epic Games Store" or "GamerPower.com"
    worth_usd: float | None
    starts_at: datetime | None
    ends_at: datetime | None
    ends_on: date | None
    url: str


@dataclass(frozen=True)
class AiOffer:
    """A free or apply-for program of an AI provider, found by the AI scan."""

    id: str
    title: str
    provider: str
    kind: str  # one of AI_KINDS
    access: str  # one of AI_ACCESS
    eligibility: str
    deadline: date | None
    url: str  # https
    summary: str
    first_seen: datetime
