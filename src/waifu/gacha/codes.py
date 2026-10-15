"""Redeem codes for the gacha buddy's games: what a code gives and what redeeming it did.

The codes come from the community API behind Hoyo Buddy's auto-redemption
(hoyo-codes.seria.moe), which checks them against HoYoLAB with an Asia account and marks the
ones found not to work. It has no published terms for others, so each game is asked at most
once per poll. A game whose request fails is logged and left out; only when every game failed
is there a CodesError.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from waifu import __version__
from waifu.config import GameId
from waifu.gacha.games import GAMES

logger = logging.getLogger(__name__)

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


# The outcomes that settle a code: it is never tried again.
FINAL_OUTCOMES = frozenset({"redeemed", "claimed", "expired", "invalid", "region", "level"})

CODES_SOURCE = "hoyo-codes.seria.moe"
CODES_URL = f"https://{CODES_SOURCE}/codes"
# The source names the games as genshin.py does.
SOURCE_GAMES: dict[GameId, str] = {"hsr": "hkrpg", "genshin": "genshin", "zzz": "nap"}
MAX_REWARDS_CHARS = 200
TIMEOUT_S = 15.0
USER_AGENT = f"waifu-companion/{__version__}"

_CODE = re.compile(r"[A-Z0-9]{5,24}")
# Some codes list their rewards as "Primogem*60;Mora*20000": items and counts, read out as is.
_COUNTED_ITEM = re.compile(r"\s*([^*;]+?)\s*\*\s*(\d{1,3}(?:,\d{3})*|\d+)\s*")


class CodesError(Exception):
    """Why no redeem codes could be read; Indonesian and safe to speak."""


class _SourceFailed(Exception):
    """A game's request gave nothing usable; the reason is already logged."""


def normalize_code(text: str) -> str | None:
    """`text` as a redeem code (trimmed and uppercased), or None when it is not one."""
    code = text.strip()
    # ASCII first: a few other letters uppercase to ASCII ones ("ſ" to "S").
    if not code.isascii():
        return None
    code = code.upper()
    return code if _CODE.fullmatch(code) else None


def readable_rewards(text: str) -> str:
    """Rewards in the "Item*count;Item*count" form as she would say them, "60 Primogem, 20.000
    Mora"; any other text as it is."""
    parts = text.split(";")
    items = [_COUNTED_ITEM.fullmatch(part) for part in parts]
    if not all(items):
        return text
    return ", ".join(
        f"{int(match.group(2).replace(',', '')):,}".replace(",", ".") + f" {match.group(1)}"
        for match in items
        if match is not None
    )


def read_codes(
    games: Iterable[GameId], *, http: httpx.Client
) -> dict[GameId, list[tuple[str, str]]] | CodesError:
    """Each game's working codes with their rewards, `(code, rewards)` in the source's order.

    One request per game; a game whose request fails is logged and left out, and a game the
    source lists no working code for has an empty list. A CodesError when every game failed.
    """
    wanted = list(dict.fromkeys(games))
    found: dict[GameId, list[tuple[str, str]]] = {}
    for game in wanted:
        try:
            found[game] = _game_codes(http, game)
        except _SourceFailed:
            continue
    if wanted and not found:
        return CodesError(
            f"Kode redeem tidak bisa diambil: {CODES_SOURCE} tidak bisa dihubungi atau sedang "
            "bermasalah. Cek koneksi internet, lalu coba lagi nanti."
        )
    return found


def _game_codes(http: httpx.Client, game: GameId) -> list[tuple[str, str]]:
    """The working codes the source lists for `game`, each once; _SourceFailed (logged) when
    its answer is not a list of codes."""
    name = GAMES[game].short
    source_game = SOURCE_GAMES[game]
    try:
        response = http.get(
            CODES_URL,
            params={"game": source_game},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "The redeem code source answered HTTP %s for %s", exc.response.status_code, name
        )
        raise _SourceFailed from None
    except httpx.HTTPError as exc:  # timeouts and connection errors
        logger.warning("The redeem code source is unreachable for %s: %r", name, exc)
        raise _SourceFailed from None
    except ValueError as exc:  # not UTF-8 or not JSON
        logger.warning("The redeem code source sent %s something that is not JSON: %s", name, exc)
        raise _SourceFailed from None
    entries = data.get("codes") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        logger.warning("The redeem code source sent %s no list of codes", name)
        raise _SourceFailed
    codes: dict[str, str] = {}
    for entry in entries:
        listed = _working_code(entry, source_game)
        if listed is not None:
            code, rewards = listed
            codes.setdefault(code, rewards)
    return list(codes.items())


def _working_code(entry: Any, source_game: str) -> tuple[str, str] | None:
    """The code and rewards of an entry the source found working; None for any other entry
    (one that cannot be read is logged)."""
    if not isinstance(entry, dict):
        logger.warning("Skipping a redeem code entry that is not an object: %.80r", entry)
        return None
    if entry.get("status") != "OK":
        return None
    if entry.get("game", source_game) != source_game:
        logger.warning("Skipping a redeem code listed for another game: %.80r", entry)
        return None
    code = entry.get("code")
    code = normalize_code(code) if isinstance(code, str) else None
    rewards = entry.get("rewards")
    if code is None or not isinstance(rewards, str | None):
        logger.warning("Skipping a redeem code entry that cannot be read: %.80r", entry)
        return None
    readable = readable_rewards(" ".join((rewards or "").split()))
    return code, readable[:MAX_REWARDS_CHARS].rstrip()
