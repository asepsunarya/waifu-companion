"""Why Cyrene has no offers to tell about."""

from __future__ import annotations


class OffersError(Exception):
    """Why there are no free games or AI offers right now; Indonesian and safe to speak.

    `billed` says that getting this far already cost money: an AI scan that failed after Claude
    answered at least once.
    """

    def __init__(self, message: str, *, billed: bool = False) -> None:
        super().__init__(message)
        self.billed = billed
