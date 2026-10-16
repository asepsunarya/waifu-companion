"""Games free to keep: Epic Games Store from Epic's own feed, other stores from GamerPower.com.

Epic's feed lists the games of its giveaway with exact UTC times, running now
(`promotionalOffers`) or coming soon (`upcomingPromotionalOffers`), next to paid sales.
GamerPower.com (free for any use with attribution and a link to it, at most 10 requests per
second) is asked once per other configured platform; its end dates are only dates. Each
request is a source of its own: one that fails is logged and the others still count, and only
when every one failed is there an OffersError.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, time
from functools import partial
from typing import Any

import httpx

from waifu import __version__
from waifu.offers.errors import OffersError
from waifu.offers.values import GameGiveaway

logger = logging.getLogger(__name__)

EPIC = "Epic Games Store"
EPIC_URL = "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions"
EPIC_QUERY = {"locale": "en-US", "country": "ID", "allowCountries": "ID"}
EPIC_STORE_PAGE = "https://store.epicgames.com/en-US/p/"
GAMERPOWER = "GamerPower.com"
GAMERPOWER_URL = "https://www.gamerpower.com/api/giveaways"
# The configured platforms GamerPower is asked about (its own names too) and what she calls them.
GAMERPOWER_PLATFORMS = {"steam": "Steam", "gog": "GOG", "itchio": "itch.io", "pc": "PC"}
TIMEOUT_S = 15.0
USER_AGENT = f"waifu-companion/{__version__}"

# GamerPower ends a title with "Giveaway", "Key Giveaway", or "Steam Key Giveaway"; a Key
# anywhere else is the game's own ("Skeleton Key (Steam) Giveaway").
_GIVEAWAY_WORDS = re.compile(r"\s+(?:(?:Steam\s+)?Key\s+)?Giveaway$")
_IN_PARENTHESES_AT_THE_END = re.compile(r"\s*\(([^()]*)\)$")
# The stores GamerPower names in a title's brackets besides a giveaway's own platforms.
_STORES_IN_TITLES = frozenset(
    {"epic games", EPIC.casefold(), "indiegala", *map(str.casefold, GAMERPOWER_PLATFORMS.values())}
)
_US_DOLLARS = re.compile(r"\$(\d+(?:\.\d+)?)")
_NEVER = datetime.max.replace(tzinfo=UTC)


class _SourceFailed(Exception):
    """A source gave nothing usable; the reason is already logged."""


def read_free_games(
    platforms: Iterable[str], *, http: httpx.Client, now: datetime
) -> tuple[GameGiveaway, ...] | OffersError:
    """The games free to keep now or soon on `platforms` (free_offers.game_platforms names).

    Games free now come first, the soonest to end first, then the upcoming ones, the soonest to
    start first. Games that ended are dropped (`now` is aware); a game GamerPower lists for
    several platforms is kept once. An OffersError when every source failed.
    """
    wanted = list(platforms)
    read_epic = "epic" in wanted
    sources: list[tuple[str, Callable[[], list[GameGiveaway]]]] = []
    if read_epic:
        sources.append((EPIC, partial(_epic_games, http, now)))
    # `pc` is every PC store, so it is asked last: a game also found on a store keeps its name.
    for platform in sorted((p for p in wanted if p != "epic"), key=lambda p: p == "pc"):
        read = partial(_gamerpower_games, http, platform, now, skip_epic=read_epic)
        sources.append((GAMERPOWER, read))
    games: dict[str, GameGiveaway] = {}
    failed: list[str] = []
    for name, read in sources:
        try:
            found = read()
        except _SourceFailed:
            failed.append(name)
            continue
        for game in found:
            games.setdefault(game.id, game)
    if failed and len(failed) == len(sources):
        names = " dan ".join(dict.fromkeys(failed))
        return OffersError(
            f"Daftar game gratis tidak bisa diambil: {names} tidak bisa dihubungi atau sedang "
            "bermasalah. Cek koneksi internet, lalu coba lagi nanti."
        )
    return tuple(sorted(games.values(), key=partial(_order, now=now)))


def _get_json(http: httpx.Client, url: str, query: dict[str, str], source: str) -> Any:
    """A source's decoded JSON reply; _SourceFailed (logged) when there is none."""
    try:
        response = http.get(
            url, params=query, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("%s answered HTTP %s", source, exc.response.status_code)
        raise _SourceFailed from None
    except httpx.HTTPError as exc:  # timeouts and connection errors
        logger.warning("%s is unreachable: %r", source, exc)
        raise _SourceFailed from None
    except ValueError as exc:  # not UTF-8 or not JSON
        logger.warning("%s sent something that is not JSON: %s", source, exc)
        raise _SourceFailed from None


def _each_game(
    items: list[Any], read: Callable[[dict[str, Any]], GameGiveaway | None], source: str
) -> list[GameGiveaway]:
    """The games among a source's items; an item that cannot be read is logged and skipped."""
    games = []
    for item in items:
        try:
            game = read(_object(item))
        except (LookupError, TypeError, ValueError) as exc:
            logger.warning("Skipping a game from %s that could not be read: %r", source, exc)
            continue
        if game is not None:
            games.append(game)
    return games


def _epic_games(http: httpx.Client, now: datetime) -> list[GameGiveaway]:
    data = _get_json(http, EPIC_URL, EPIC_QUERY, EPIC)
    try:
        elements = _list(data["data"]["Catalog"]["searchStore"]["elements"])
    except (LookupError, TypeError) as exc:
        logger.warning("%s sent a feed without its list of games: %r", EPIC, exc)
        raise _SourceFailed from None
    return _each_game(elements, partial(_epic_game, now=now), EPIC)


def _epic_game(element: dict[str, Any], now: datetime) -> GameGiveaway | None:
    """The element as a game when it has a free-to-keep offer, running or coming, not ended."""
    period = _free_period(_object(element.get("promotions")), now)
    if period is None:
        return None
    starts_at, ends_at = period
    return GameGiveaway(
        id=f"epic:{_text(element.get('id'))}",
        title=_text(element.get("title")),
        platform=EPIC,
        source=EPIC,
        worth_usd=None,  # the feed for Indonesia prices games in rupiah
        starts_at=starts_at,
        ends_at=ends_at,
        ends_on=None,
        url=EPIC_STORE_PAGE + _epic_page(element),
    )


def _free_period(promotions: dict[str, Any], now: datetime) -> tuple[datetime, datetime] | None:
    """The start and end of the first free-to-keep offer, running or coming, that has not ended.

    Epic's discountPercentage is the part of the price left to pay: 0 is free, and paid sales
    show other numbers.
    """
    for kind in ("promotionalOffers", "upcomingPromotionalOffers"):
        for group in _list(promotions.get(kind)):
            for offer in map(_object, _list(_object(group).get("promotionalOffers"))):
                if _object(offer.get("discountSetting")).get("discountPercentage") != 0:
                    continue
                starts_at = _aware_time(offer.get("startDate"))
                ends_at = _aware_time(offer.get("endDate"))
                if ends_at > now:
                    return starts_at, ends_at
    return None


def _aware_time(value: Any) -> datetime:
    """An ISO-8601 time with its zone, such as Epic's 2026-10-15T15:00:00.000Z."""
    moment = datetime.fromisoformat(_text(value))
    if moment.tzinfo is None:
        raise ValueError(f"the time has no zone: {value!r}")
    return moment


def _epic_page(element: dict[str, Any]) -> str:
    """The store page's slug: the offer's page, else the catalog's, else productSlug without its
    /home, else urlSlug."""
    catalog = _object(element.get("catalogNs"))
    for pages in (element.get("offerMappings"), catalog.get("mappings")):
        for page in _list(pages)[:1]:
            slug = _object(page).get("pageSlug")
            if slug:
                return _text(slug)
    product = element.get("productSlug")
    if product:
        return _text(product).removesuffix("/home")
    return _text(element.get("urlSlug"))


def _gamerpower_games(
    http: httpx.Client, platform: str, now: datetime, *, skip_epic: bool
) -> list[GameGiveaway]:
    name = GAMERPOWER_PLATFORMS[platform]
    source = f"GamerPower ({platform})"
    data = _get_json(http, GAMERPOWER_URL, {"type": "game", "platform": platform}, source)
    if isinstance(data, dict) and data.get("status") == 0:
        return []  # HTTP 201: "No active giveaways available at the moment"
    if not isinstance(data, list):
        logger.warning("%s sent a %s, not a list of giveaways", source, type(data).__name__)
        raise _SourceFailed
    read = partial(_gamerpower_game, platform=name, now=now, skip_epic=skip_epic)
    return _each_game(data, read, source)


def _gamerpower_game(
    item: dict[str, Any], *, platform: str, now: datetime, skip_epic: bool
) -> GameGiveaway | None:
    """The giveaway as a game when it is active and has not ended; one also on the Epic Games
    Store is left to Epic's own feed when that is read."""
    if item.get("status") != "Active":
        return None
    if skip_epic and EPIC in (part.strip() for part in _text(item.get("platforms")).split(",")):
        return None
    ends_on = _gamerpower_date(item.get("end_date"))
    if ends_on is not None and ends_on < now.date():
        return None
    return GameGiveaway(
        id=f"gamerpower:{_whole_number(item.get('id'))}",
        title=_text(_clean_title(_text(item.get("title")), item.get("platforms"))),
        platform=platform,
        source=GAMERPOWER,
        worth_usd=_worth_usd(item.get("worth")),
        starts_at=None,
        ends_at=None,
        ends_on=ends_on,
        url=_gamerpower_link(item),
    )


def _clean_title(title: str, platforms: Any) -> str:
    """'Dwarven Realms (Steam) Key Giveaway' -> 'Dwarven Realms': no Giveaway words ('Giveaway',
    'Key Giveaway', 'Steam Key Giveaway'), then, as often as the title ends so, no store in
    brackets, one of `platforms` (GamerPower's list, such as "PC, Steam") or another store;
    other brackets are part of the name ('Hades (2020)'), and so is a Key before them
    ('Skeleton Key (Steam) Giveaway'). GamerPower's own title when nothing else would be left."""
    original = title.strip()
    listed = platforms.split(",") if isinstance(platforms, str) else []
    stores = _STORES_IN_TITLES | {platform.strip().casefold() for platform in listed}
    title = _GIVEAWAY_WORDS.sub("", original)
    while (shorter := _without_store_in_brackets(title, stores)) != title:
        title = shorter
    return title or original


def _without_store_in_brackets(title: str, stores: frozenset[str]) -> str:
    """The title without the store in brackets it ends with, if any."""
    match = _IN_PARENTHESES_AT_THE_END.search(title)
    if match is not None and match[1].strip().casefold() in stores:
        return title[: match.start()]
    return title


def _gamerpower_date(value: Any) -> date | None:
    """GamerPower's "2026-10-22 23:59:00" as a date only (its times are not precise); "N/A" is
    none."""
    text = _text(value)
    return None if text == "N/A" else datetime.fromisoformat(text).date()


def _worth_usd(value: Any) -> float | None:
    """GamerPower's "$19.99" in dollars; "N/A" is none."""
    match = _US_DOLLARS.fullmatch(_text(value))
    return float(match[1]) if match else None


def _gamerpower_link(item: dict[str, Any]) -> str:
    """The giveaway's own link, else its page on GamerPower.com; https only."""
    for key in ("open_giveaway_url", "gamerpower_url"):
        url = item.get(key)
        if isinstance(url, str) and url.startswith("https://"):
            return url
    raise ValueError("the giveaway has no https link")


def _order(game: GameGiveaway, now: datetime) -> tuple[bool, datetime, str]:
    """Free now first, the soonest to end first (no end given: last); then the upcoming ones,
    the soonest to start first."""
    if game.starts_at is not None and game.starts_at > now:
        return True, game.starts_at, game.title
    if game.ends_at is not None:
        return False, game.ends_at, game.title
    if game.ends_on is not None:  # a GamerPower date lasts until that day is over here
        return False, datetime.combine(game.ends_on, time.max, tzinfo=now.tzinfo), game.title
    return False, _NEVER, game.title


def _object(value: Any) -> dict[str, Any]:
    """A JSON object; null (a field the source left empty) is an empty one."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(f"expected an object, got {value!r:.80}")
    return value


def _list(value: Any) -> list[Any]:
    """A JSON array; null is an empty one."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"expected a list, got {value!r:.80}")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"expected text, got {value!r:.80}")
    return value


def _whole_number(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected a whole number, got {value!r:.80}")
    return value
