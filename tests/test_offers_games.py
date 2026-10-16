import copy
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from waifu.offers.errors import OffersError
from waifu.offers.games import read_free_games
from waifu.offers.values import GameGiveaway

EPIC_HOST = "store-site-backend-static-ipv4.ak.epicgames.com"
GAMERPOWER_HOST = "www.gamerpower.com"
WIB = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 16, 16, 0, tzinfo=WIB)  # 09:00 UTC


def epic_offer(start: str, end: str, percentage: int) -> dict:
    return {
        "promotionalOffers": [
            {
                "startDate": start,
                "endDate": end,
                "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": percentage},
            }
        ]
    }


# Elements of Epic's feed (data.Catalog.searchStore.elements) as it answered on 2026-10-16,
# trimmed to the fields that matter.
BAD_CHEESE = {  # free now
    "title": "Bad Cheese",
    "id": "6a846880836044a2adad614ac771f487",
    "namespace": "a0c80bf673a548bc97b4bb25abfaf2b4",
    "productSlug": None,
    "urlSlug": "ae5d8fc5541f42eea105f64121dba382",
    "offerMappings": [{"pageSlug": "bad-cheese-4256d9", "pageType": "productHome"}],
    "catalogNs": {"mappings": [{"pageSlug": "bad-cheese-4256d9", "pageType": "productHome"}]},
    "price": {
        "totalPrice": {
            "discountPrice": 0,
            "originalPrice": 8999900,
            "fmtPrice": {
                "originalPrice": "IDR 89,999.00",
                "discountPrice": "0",
                "intermediatePrice": "0",
            },
        }
    },
    "promotions": {
        "promotionalOffers": [
            epic_offer("2026-10-15T15:00:00.000Z", "2026-10-22T15:00:00.000Z", 0)
        ],
        "upcomingPromotionalOffers": [],
    },
}
SKER_RITUAL = {  # free soon
    "title": "Sker Ritual",
    "id": "6aab3ddef4f84fbc9dd81c60f9d8549c",
    "productSlug": None,
    "urlSlug": "8dbc8d70481d4c0b8412d3aec76a35a1",
    "offerMappings": [{"pageSlug": "sker-ritual-aac702", "pageType": "productHome"}],
    "price": {"totalPrice": {"discountPrice": 17299900, "originalPrice": 17299900}},
    "promotions": {
        "promotionalOffers": [],
        "upcomingPromotionalOffers": [
            epic_offer("2026-10-22T15:00:00.000Z", "2026-10-29T15:00:00.000Z", 0)
        ],
    },
}
ETERNAL_THREADS = {  # a paid sale soon: 20 % of the price is left to pay
    "title": "Eternal Threads",
    "id": "1f8e6a0b5c2d4e7f9a1b3c5d7e9f0a2b",
    "productSlug": None,
    "urlSlug": "eternal-threads",
    "offerMappings": [{"pageSlug": "eternal-threads-3b1f2a", "pageType": "productHome"}],
    "promotions": {
        "promotionalOffers": [],
        "upcomingPromotionalOffers": [
            epic_offer("2026-10-19T15:00:00.000Z", "2026-11-02T16:00:00.000Z", 20)
        ],
    },
}
THEMS_FIGHTIN_HERDS = {  # no promotion at all
    "title": "Them's Fightin' Herds",
    "id": "d5241c76f178492ea1540fce45616757",
    "productSlug": "thems-fightin-herds/home",
    "urlSlug": "thems-fightin-herds",
    "offerMappings": [],
    "promotions": None,
}
LOST_CASTLE = {  # free now; its offer mapping is an offer page, not the product home
    "title": "Lost Castle",
    "id": "9b1c7d3e5f7a4b6c8d0e2f4a6b8c0d1e",
    "productSlug": None,
    "urlSlug": "lost-castle-db1545",
    "offerMappings": [{"pageSlug": "lost-castle-db1545", "pageType": "offer"}],
    "catalogNs": {"mappings": [{"pageSlug": "lost-castle-7c2a19", "pageType": "productHome"}]},
    "promotions": {
        "promotionalOffers": [
            epic_offer("2026-10-15T15:00:00.000Z", "2026-10-22T15:00:00.000Z", 0)
        ],
        "upcomingPromotionalOffers": [],
    },
}


def epic_feed(*elements: dict) -> dict:
    return {
        "data": {
            "Catalog": {
                "searchStore": {
                    "elements": list(elements),
                    "paging": {"count": 1000, "total": len(elements)},
                }
            }
        },
        "extensions": {},
    }


# GamerPower giveaways (GET /api/giveaways?type=game) as it answered on 2026-10-16.
AGENT_A = {
    "id": 3817,
    "title": "Agent A: A puzzle in disguise (Epic Games) Giveaway",
    "worth": "$19.99",
    "thumbnail": "https://www.gamerpower.com/offers/1/6710a4c1b2a33.jpg",
    "image": "https://www.gamerpower.com/offers/1b/6710a4c1b2a33.jpg",
    "description": "Grab Agent A: A puzzle in disguise for free on the Epic Games Store!",
    "instructions": "1. Click the button to visit the giveaway page.",
    "open_giveaway_url": "https://www.gamerpower.com/open/agent-a-a-puzzle-in-disguise-epic-games-giveaway",
    "published_date": "2026-10-15 11:04:37",
    "type": "Game",
    "platforms": "PC, Android, iOS, Epic Games Store",
    "end_date": "2026-10-22 23:59:00",
    "users": 5240,
    "status": "Active",
    "gamerpower_url": "https://www.gamerpower.com/agent-a-a-puzzle-in-disguise-epic-games-giveaway",
    "open_giveaway": "https://www.gamerpower.com/open/agent-a-a-puzzle-in-disguise-epic-games-giveaway",
}
ROGUE_HARBOR = {
    **AGENT_A,
    "id": 3820,
    "title": "Rogue Harbor (Steam) Giveaway",
    "worth": "$9.99",
    "open_giveaway_url": "https://www.gamerpower.com/open/rogue-harbor-steam-giveaway",
    "platforms": "PC, Steam",
    "end_date": "2026-10-30 23:59:00",
    "gamerpower_url": "https://www.gamerpower.com/rogue-harbor-steam-giveaway",
}
STARWARD_DRIFT = {
    **AGENT_A,
    "id": 3822,
    "title": "Starward Drift (GOG) Giveaway",
    "worth": "N/A",
    "open_giveaway_url": "https://www.gamerpower.com/open/starward-drift-gog-giveaway",
    "platforms": "PC, DRM-Free, GOG",
    "end_date": "N/A",
    "gamerpower_url": "https://www.gamerpower.com/starward-drift-gog-giveaway",
}
NO_GIVEAWAYS = {
    "status": 0,
    "status_message": "No active giveaways available at the moment, please try again later.",
}

Reply = Any  # a JSON value (HTTP 200), an httpx.Response, or an exception to raise


class FakeWeb:
    """Epic and GamerPower behind httpx's MockTransport: nothing leaves this PC."""

    def __init__(self, *, epic: Reply = None, gamerpower: dict[str, Reply] | None = None) -> None:
        self.epic = epic_feed() if epic is None else epic
        self.gamerpower = gamerpower or {}
        self.requests: list[httpx.Request] = []
        self.http = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == EPIC_HOST:
            return self._answer(self.epic)
        assert request.url.host == GAMERPOWER_HOST, f"unexpected request to {request.url}"
        return self._answer(self.gamerpower[request.url.params["platform"]])

    @staticmethod
    def _answer(reply: Reply) -> httpx.Response:
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)


def read(web: FakeWeb, platforms: list[str], now: datetime = NOW) -> tuple[GameGiveaway, ...]:
    games = read_free_games(platforms, http=web.http, now=now)
    assert not isinstance(games, OffersError), games
    return games


def titles(games: tuple[GameGiveaway, ...]) -> list[str]:
    return [game.title for game in games]


def test_an_epic_game_free_now_has_exact_times_and_its_store_page():
    web = FakeWeb(epic=epic_feed(BAD_CHEESE))

    assert read(web, ["epic"]) == (
        GameGiveaway(
            id="epic:6a846880836044a2adad614ac771f487",
            title="Bad Cheese",
            platform="Epic Games Store",
            source="Epic Games Store",
            worth_usd=None,  # Epic prices it in rupiah for Indonesia
            starts_at=datetime(2026, 10, 15, 15, tzinfo=UTC),
            ends_at=datetime(2026, 10, 22, 15, tzinfo=UTC),
            ends_on=None,
            url="https://store.epicgames.com/en-US/p/bad-cheese-4256d9",
        ),
    )


def test_an_epic_game_free_soon_starts_in_the_future():
    web = FakeWeb(epic=epic_feed(SKER_RITUAL))

    (game,) = read(web, ["epic"])

    assert game.id == "epic:6aab3ddef4f84fbc9dd81c60f9d8549c"
    assert game.starts_at == datetime(2026, 10, 22, 15, tzinfo=UTC)
    assert game.ends_at == datetime(2026, 10, 29, 15, tzinfo=UTC)
    assert game.url == "https://store.epicgames.com/en-US/p/sker-ritual-aac702"


def test_paid_sales_and_games_without_a_promotion_are_not_free():
    web = FakeWeb(epic=epic_feed(ETERNAL_THREADS, THEMS_FIGHTIN_HERDS, BAD_CHEESE))

    assert titles(read(web, ["epic"])) == ["Bad Cheese"]


def test_epic_is_asked_for_indonesia_with_a_15_second_timeout():
    web = FakeWeb(epic=epic_feed(BAD_CHEESE))

    read(web, ["epic"])

    (request,) = web.requests
    assert request.method == "GET"
    assert request.url.copy_with(query=None) == (
        "https://store-site-backend-static-ipv4.ak.epicgames.com/freeGamesPromotions"
    )
    assert dict(request.url.params) == {
        "locale": "en-US",
        "country": "ID",
        "allowCountries": "ID",
    }
    assert request.extensions["timeout"] == {
        "connect": 15.0,
        "read": 15.0,
        "write": 15.0,
        "pool": 15.0,
    }
    assert request.headers["User-Agent"].startswith("waifu-companion/")


def free_now(**fields: Any) -> dict:
    element = copy.deepcopy(BAD_CHEESE)
    element.update(fields)
    return element


@pytest.mark.parametrize(
    ("fields", "page"),
    [
        ({}, "bad-cheese-4256d9"),
        # The offer's own page comes first, whatever kind of page it is.
        (LOST_CASTLE, "lost-castle-db1545"),
        ({"offerMappings": []}, "bad-cheese-4256d9"),
        (
            {
                "offerMappings": None,
                "catalogNs": {"mappings": [{"pageSlug": "cheese-home", "pageType": "productHome"}]},
            },
            "cheese-home",
        ),
        (
            {"offerMappings": [], "catalogNs": {"mappings": []}, "productSlug": "bad-cheese/home"},
            "bad-cheese",
        ),
        (
            {"offerMappings": [], "catalogNs": {"mappings": []}, "productSlug": "bad-cheese"},
            "bad-cheese",
        ),
        (
            {"offerMappings": [{"pageSlug": None}], "catalogNs": None, "productSlug": None},
            "ae5d8fc5541f42eea105f64121dba382",
        ),
    ],
)
def test_the_epic_store_link_comes_from_the_first_page_it_names(fields, page):
    web = FakeWeb(epic=epic_feed(free_now(**fields)))

    (game,) = read(web, ["epic"])

    assert game.url == f"https://store.epicgames.com/en-US/p/{page}"


def test_an_epic_game_whose_free_time_ended_is_dropped():
    web = FakeWeb(epic=epic_feed(BAD_CHEESE, SKER_RITUAL))
    after_bad_cheese = datetime(2026, 10, 22, 22, 0, tzinfo=WIB)  # 15:00 UTC: Bad Cheese ends

    games = read(web, ["epic"], now=after_bad_cheese)

    assert titles(games) == ["Sker Ritual"]


def test_an_epic_element_that_cannot_be_read_is_skipped(caplog):
    broken_time = free_now(id="broken", title="Broken Time")
    broken_time["promotions"] = {
        "promotionalOffers": [epic_offer("someday", "2026-10-22T15:00:00.000Z", 0)],
        "upcomingPromotionalOffers": [],
    }
    no_title = free_now(id="untitled", title=None)
    web = FakeWeb(epic=epic_feed(broken_time, "not an object", no_title, BAD_CHEESE))

    with caplog.at_level(logging.WARNING):
        games = read(web, ["epic"])

    assert titles(games) == ["Bad Cheese"]
    assert "Epic" in caplog.text


def test_gamerpower_is_asked_once_per_other_platform_with_pc_last():
    web = FakeWeb(gamerpower={p: [] for p in ("pc", "steam", "gog", "itchio")})

    read(web, ["pc", "epic", "steam", "gog", "itchio"])

    asked = [request.url for request in web.requests if request.url.host == GAMERPOWER_HOST]
    assert [url.copy_with(query=None) for url in asked] == [
        "https://www.gamerpower.com/api/giveaways"
    ] * 4
    assert [dict(url.params) for url in asked] == [
        {"type": "game", "platform": "steam"},
        {"type": "game", "platform": "gog"},
        {"type": "game", "platform": "itchio"},
        {"type": "game", "platform": "pc"},
    ]
    assert all(r.extensions["timeout"]["read"] == 15.0 for r in web.requests)


def test_a_gamerpower_giveaway_has_a_clean_title_its_worth_and_an_end_date():
    web = FakeWeb(gamerpower={"steam": [ROGUE_HARBOR]})

    assert read(web, ["steam"]) == (
        GameGiveaway(
            id="gamerpower:3820",
            title="Rogue Harbor",
            platform="Steam",
            source="GamerPower.com",
            worth_usd=9.99,
            starts_at=None,
            ends_at=None,
            ends_on=date(2026, 10, 30),
            url="https://www.gamerpower.com/open/rogue-harbor-steam-giveaway",
        ),
    )


def test_gamerpower_n_a_means_no_worth_and_no_end():
    web = FakeWeb(gamerpower={"gog": [STARWARD_DRIFT]})

    (game,) = read(web, ["gog"])

    assert (game.platform, game.worth_usd, game.ends_on) == ("GOG", None, None)


@pytest.mark.parametrize(
    ("title", "clean"),
    [
        ("Agent A: A puzzle in disguise (Epic Games) Giveaway", "Agent A: A puzzle in disguise"),
        ("Rogue Harbor (Steam) Giveaway", "Rogue Harbor"),
        ("Starward Drift Giveaway", "Starward Drift"),
        ("Tiny Tales (Deluxe Edition) (GOG) Giveaway", "Tiny Tales (Deluxe Edition)"),
        ("Moonlit Garden (itch.io)", "Moonlit Garden"),
        ("Plain Title", "Plain Title"),
        # Brackets that name no store are part of the game's name.
        ("Tiny Tales (Deluxe Edition) Giveaway", "Tiny Tales (Deluxe Edition)"),
        ("Hades (2020) Giveaway", "Hades (2020)"),
        ("Hades (2020) (Steam) Giveaway", "Hades (2020)"),
        ("Moonlit Garden (IndieGala) Giveaway", "Moonlit Garden"),
        ("Moonlit Garden (Epic Games Store) Giveaway", "Moonlit Garden"),
    ],
)
def test_gamerpower_titles_lose_the_giveaway_word_and_the_platform(title, clean):
    web = FakeWeb(gamerpower={"steam": [{**ROGUE_HARBOR, "title": title}]})

    assert titles(read(web, ["steam"])) == [clean]


@pytest.mark.parametrize(
    ("title", "clean"),
    [
        # The owner's list once said "Dwarven Realms (Steam) Key di Steam".
        ("Dwarven Realms (Steam) Key Giveaway", "Dwarven Realms"),
        ("Dwarven Realms Steam Key Giveaway", "Dwarven Realms"),
        ("Dwarven Realms Key Giveaway", "Dwarven Realms"),
        ("Dwarven Realms (PC) (Steam) Key Giveaway", "Dwarven Realms"),
        ("Dwarven Realms DLC (Steam) Giveaway", "Dwarven Realms DLC"),
        ("Portal 2 (Soundtrack) (Steam) Key Giveaway", "Portal 2 (Soundtrack)"),
        ("Portal 2 (Soundtrack) Key Giveaway", "Portal 2 (Soundtrack)"),
    ],
)
def test_gamerpower_titles_lose_the_key_word_and_every_platform_at_the_end(title, clean):
    web = FakeWeb(gamerpower={"steam": [{**ROGUE_HARBOR, "title": title}]})

    assert titles(read(web, ["steam"])) == [clean]


@pytest.mark.parametrize(
    ("title", "clean"),
    [
        ("Skeleton Key (Steam) Giveaway", "Skeleton Key"),
        ("The Key (Steam) Giveaway", "The Key"),
        ("Skeleton Key (Steam) Key Giveaway", "Skeleton Key"),
        ("The Last Key Steam Key Giveaway", "The Last Key"),
        ("Skeleton Key (Steam)", "Skeleton Key"),
    ],
)
def test_a_game_named_with_key_keeps_it_the_key_word_goes_only_before_giveaway(title, clean):
    web = FakeWeb(gamerpower={"steam": [{**ROGUE_HARBOR, "title": title}]})

    assert titles(read(web, ["steam"])) == [clean]


def test_a_store_in_brackets_is_one_of_the_giveaway_s_own_platforms_too():
    drm_free = {**STARWARD_DRIFT, "title": "Starward Drift (DRM-Free) Giveaway"}
    web = FakeWeb(gamerpower={"gog": [drm_free]})

    assert titles(read(web, ["gog"])) == ["Starward Drift"]


@pytest.mark.parametrize("title", ["(Steam) Giveaway", "(Steam) Key Giveaway"])
def test_a_title_that_is_only_its_platform_stays_as_gamerpower_wrote_it(title):
    nameless = {**ROGUE_HARBOR, "title": title}
    web = FakeWeb(gamerpower={"steam": [nameless]})

    assert titles(read(web, ["steam"])) == [title]


@pytest.mark.parametrize(
    ("platform", "name"),
    [("steam", "Steam"), ("gog", "GOG"), ("itchio", "itch.io"), ("pc", "PC")],
)
def test_a_gamerpower_game_is_on_the_platform_it_was_asked_for(platform, name):
    web = FakeWeb(gamerpower={platform: [ROGUE_HARBOR]})

    (game,) = read(web, [platform])

    assert game.platform == name


def test_a_game_listed_for_a_store_and_for_pc_keeps_the_store_name():
    web = FakeWeb(gamerpower={"pc": [ROGUE_HARBOR, STARWARD_DRIFT], "steam": [ROGUE_HARBOR]})

    games = read(web, ["pc", "steam"])

    assert [(game.title, game.platform) for game in games] == [
        ("Rogue Harbor", "Steam"),
        ("Starward Drift", "PC"),
    ]


def test_gamerpower_epic_giveaways_are_skipped_only_when_epic_itself_is_read():
    web = FakeWeb(epic=epic_feed(BAD_CHEESE), gamerpower={"pc": [AGENT_A, ROGUE_HARBOR]})

    assert titles(read(web, ["epic", "pc"])) == ["Bad Cheese", "Rogue Harbor"]
    assert titles(read(web, ["pc"])) == ["Agent A: A puzzle in disguise", "Rogue Harbor"]


def test_only_active_gamerpower_giveaways_that_have_not_ended_count():
    expired = {**ROGUE_HARBOR, "id": 3700, "title": "Old One (Steam) Giveaway", "status": "Expired"}
    ended = {**ROGUE_HARBOR, "id": 3701, "title": "Ended (Steam) Giveaway"}
    ended["end_date"] = "2026-10-15 23:59:00"
    last_day = {**ROGUE_HARBOR, "id": 3702, "title": "Last Day (Steam) Giveaway"}
    last_day["end_date"] = "2026-10-16 23:59:00"
    web = FakeWeb(gamerpower={"steam": [expired, ended, last_day]})

    assert titles(read(web, ["steam"])) == ["Last Day"]


def test_the_gamerpower_link_falls_back_to_its_giveaway_page():
    item = {**ROGUE_HARBOR, "open_giveaway_url": ""}
    web = FakeWeb(gamerpower={"steam": [item]})

    (game,) = read(web, ["steam"])

    assert game.url == "https://www.gamerpower.com/rogue-harbor-steam-giveaway"


def test_a_gamerpower_giveaway_that_cannot_be_read_is_skipped(caplog):
    no_id = {**ROGUE_HARBOR, "id": None}
    no_link = {**ROGUE_HARBOR, "id": 3830, "open_giveaway_url": None, "gamerpower_url": "http://x"}
    bad_date = {**ROGUE_HARBOR, "id": 3831, "end_date": "next week"}
    blank_title = {**ROGUE_HARBOR, "id": 3832, "title": "   "}
    web = FakeWeb(
        gamerpower={"gog": [no_id, no_link, "junk", bad_date, blank_title, STARWARD_DRIFT]}
    )

    with caplog.at_level(logging.WARNING):
        games = read(web, ["gog"])

    assert titles(games) == ["Starward Drift"]
    assert "GamerPower" in caplog.text


def test_gamerpower_saying_there_are_no_giveaways_means_no_games():
    web = FakeWeb(gamerpower={"gog": httpx.Response(201, json=NO_GIVEAWAYS)})

    assert read(web, ["gog"]) == ()


def test_games_free_now_come_first_by_end_then_upcoming_ones_by_start():
    later_soon = free_now(id="later", title="Later Soon")
    later_soon["promotions"] = {
        "promotionalOffers": [],
        "upcomingPromotionalOffers": [
            epic_offer("2026-10-29T15:00:00.000Z", "2026-11-05T15:00:00.000Z", 0)
        ],
    }
    early_bird = {**ROGUE_HARBOR, "id": 3840, "title": "Early Bird (Steam) Giveaway"}
    early_bird["end_date"] = "2026-10-21 23:59:00"
    same_day = {**ROGUE_HARBOR, "id": 3841, "title": "Same Day (Steam) Giveaway"}
    same_day["end_date"] = "2026-10-22 00:00:00"
    web = FakeWeb(
        epic=epic_feed(later_soon, SKER_RITUAL, BAD_CHEESE),
        gamerpower={"steam": [STARWARD_DRIFT, ROGUE_HARBOR, same_day, early_bird]},
    )

    assert titles(read(web, ["epic", "steam"])) == [
        "Early Bird",  # ends on 21 Oct
        "Bad Cheese",  # ends 22 Oct 22:00 WIB
        "Same Day",  # ends on 22 Oct: a GamerPower date lasts until that day is over here
        "Rogue Harbor",  # ends on 30 Oct
        "Starward Drift",  # no end given
        "Sker Ritual",  # starts 22 Oct
        "Later Soon",  # starts 29 Oct
    ]


Failure = Callable[[], Reply]

FAILURES: dict[str, Failure] = {
    "timeout": lambda: httpx.ReadTimeout("timed out"),
    "refused": lambda: httpx.ConnectError("connection refused"),
    "http 500": lambda: httpx.Response(500, text="Internal Server Error"),
    "http 403": lambda: httpx.Response(403, text="Forbidden"),
    "not json": lambda: httpx.Response(200, text="<html>maintenance</html>"),
    "unexpected json": lambda: {"message": "rate limited"},
    "json list": lambda: [],
}


@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_a_failing_epic_never_hides_gamerpower(failure, caplog):
    web = FakeWeb(epic=FAILURES[failure](), gamerpower={"steam": [ROGUE_HARBOR]})

    with caplog.at_level(logging.WARNING):
        games = read(web, ["epic", "steam"])

    assert titles(games) == ["Rogue Harbor"]
    assert "Epic" in caplog.text


@pytest.mark.parametrize("failure", sorted(set(FAILURES) - {"json list"}))
def test_a_failing_gamerpower_never_hides_epic_or_another_platform(failure, caplog):
    web = FakeWeb(
        epic=epic_feed(BAD_CHEESE),
        gamerpower={"steam": FAILURES[failure](), "gog": [STARWARD_DRIFT]},
    )

    with caplog.at_level(logging.WARNING):
        games = read(web, ["epic", "steam", "gog"])

    assert titles(games) == ["Bad Cheese", "Starward Drift"]
    assert "GamerPower" in caplog.text


def test_every_source_failing_is_an_offers_error_in_indonesian():
    web = FakeWeb(
        epic=httpx.ConnectError("no internet"),
        gamerpower={"steam": httpx.ConnectError("no internet"), "gog": httpx.Response(502)},
    )

    error = read_free_games(["epic", "steam", "gog"], http=web.http, now=NOW)

    assert isinstance(error, OffersError)
    message = str(error)
    assert "Epic Games Store" in message and "GamerPower.com" in message
    assert "coba lagi" in message


def test_the_only_source_failing_is_an_offers_error():
    web = FakeWeb(epic=httpx.Response(503))

    error = read_free_games(["epic"], http=web.http, now=NOW)

    assert isinstance(error, OffersError)
    assert "Epic Games Store" in str(error)
    assert "GamerPower" not in str(error)


def test_a_source_with_no_free_games_is_not_a_failure():
    web = FakeWeb(epic=epic_feed(THEMS_FIGHTIN_HERDS), gamerpower={"gog": NO_GIVEAWAYS})

    assert read(web, ["epic", "gog"]) == ()
