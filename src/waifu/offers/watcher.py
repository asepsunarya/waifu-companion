"""The watcher inside `waifu run` that tells Master about new free games and AI offers.

Every `games_poll_hours` (timed from the start of the previous poll) it reads the free
games and tells the ones free now that it has not told before, in one notice that credits
GamerPower.com when a game came from it. A game free only soon is told once it is free.
When `last_ai_scan` is older than `ai_scan_days`, or no scan ever ran, the same poll runs the
AI scan (Claude with web search), with the offers already kept, and tells the new ones in
one notice. The first poll comes right after it starts. Each notice is said between
utterances with a happy face and pushed to the phone when notifications are on, opening the
first item's link when tapped; an AI offer's link only when it is on the provider's own site
(`checks.official_link`), since the scan reads pages anyone can write. A failing read,
announcement, or push is logged and the next poll tries again; so is a failed scan, unless
it was paid for (Claude had answered): then the next scan waits a day, across restarts too.
A scan time in the future (a clock set back) counts as long ago. Nothing is raised. She only
tells: claiming and applying are Master's own.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta

from waifu.brain.persona import WEEKDAYS_ID
from waifu.config import FreeOffersConfig, at_sentence_start
from waifu.notify import Notifier
from waifu.offers.checks import official_link
from waifu.offers.errors import OffersError
from waifu.offers.games import EPIC, GAMERPOWER
from waifu.offers.store import OffersStore
from waifu.offers.values import AiOffer, GameGiveaway
from waifu.scheduler import MONTHS_ID

logger = logging.getLogger(__name__)

NOTICE_EMOTION = "happy"  # good news
GAME_TAG = "video_game"  # ntfy shows it as a game controller emoji
AI_TAG = "robot"  # ntfy shows it as a robot emoji
NAMES_IN_NOTICE = 3  # the rest are counted, so a notice stays short to say
# How long a scan that failed after it was paid for keeps the next one away.
FAILED_SCAN_BACKOFF = timedelta(days=1)
STOP_TIMEOUT_S = 5.0
_STORE_NAMES = {EPIC: "Epic"}  # how she says a store's name

ReadGames = Callable[[], tuple[GameGiveaway, ...] | OffersError]
ScanAiOffers = Callable[[Sequence[AiOffer]], list[AiOffer] | OffersError]
# Waits up to the given seconds for the event; True when it was set.
Wait = Callable[[threading.Event, float], bool]


class OffersWatcher:
    def __init__(
        self,
        store: OffersStore,
        read_games: ReadGames,
        scan: ScanAiOffers,
        config: FreeOffersConfig,
        *,
        announce: Callable[[str, str], None] | None,
        notifier: Notifier | None,
        owner_call: str,
        title: str = "Cyrene",
        now: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
        wait: Wait = threading.Event.wait,
    ) -> None:
        """`scan` is given the AI offers already kept; `now` is local time, as the store keeps
        it, and `clock` monotonic seconds."""
        self._store = store
        self._read_games = read_games
        self._scan = scan
        self._config = config
        self._announce = announce
        self._notifier = notifier
        self._owner = at_sentence_start(owner_call)  # every notice opens with it
        self._title = title
        self._now = now
        self._clock = clock
        self._wait = wait
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> list[str]:
        """Tell the new free games, then run the AI scan when it is due and tell its new
        offers; returns the notices given."""
        notices: list[str] = []
        if self._config.games:
            notices.extend(self._new_games())
        if self._config.ai:
            notices.extend(self._new_ai_offers())
        return notices

    def start(self) -> None:
        """Poll now and then every `games_poll_hours` in a daemon thread; nothing runs while
        `free_offers.enabled` or `free_offers.notices` is off."""
        if self._thread is not None:
            return
        if not (self._config.enabled and self._config.notices):
            logger.info("Free offers notices are off; not watching for new ones")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="offers-watcher", daemon=True)
        self._thread.start()
        logger.info("Watching for free offers every %d hours", self._config.games_poll_hours)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def _loop(self) -> None:
        interval_s = self._config.games_poll_hours * 3600
        while not self._stop.is_set():
            started = self._clock()
            self.poll_once()
            # A scan may take minutes: the next poll still comes an interval after this began.
            self._wait(self._stop, max(0.0, started + interval_s - self._clock()))

    def _new_games(self) -> list[str]:
        try:
            games = self._read_games()
            if isinstance(games, OffersError):
                logger.warning("Could not read the free games; trying again at the next poll")
                return []
            now = self._now().astimezone()
            new = self._store.remember_games(game for game in games if _free_now(game, now))
        except Exception:
            logger.exception("Could not look for new free games; trying again at the next poll")
            return []
        if not new:
            return []
        logger.info("Telling Master about %d new free games", len(new))
        text = _games_notice(new, self._owner, now.date())
        self._deliver(text, GAME_TAG, new[0].url)
        return [text]

    def _new_ai_offers(self) -> list[str]:
        try:
            now = self._now()
            scan_every = timedelta(days=self._config.ai_scan_days)
            if _recent(self._store.last_ai_scan(), now, scan_every) or _recent(
                self._store.last_failed_ai_scan(), now, FAILED_SCAN_BACKOFF
            ):
                return []
            found = self._scan(self._store.ai_offers())
            if isinstance(found, OffersError):
                if found.billed:
                    logger.warning(
                        "The AI offers scan failed after it was paid for; trying again in a day"
                    )
                    self._store.record_failed_ai_scan(now)
                else:
                    logger.warning("The AI offers scan failed; trying again at the next poll")
                return []
            new = self._store.merge_ai_offers(found, self._now())
        except Exception:
            logger.exception("Could not scan for AI offers; trying again at the next poll")
            return []
        logger.info("The AI offers scan reported %d offers, %d new", len(found), len(new))
        if not new:
            return []
        link = new[0].url if official_link(new[0].url) else None
        text = _ai_notice(
            new, self._owner, pushed=self._notifier is not None, opens_link=link is not None
        )
        self._deliver(text, AI_TAG, link)
        return [text]

    def _deliver(self, text: str, tag: str, link: str | None) -> None:
        """Say the notice and push it, opening `link` when tapped; a failure of either is
        logged, never raised."""
        if self._announce is not None:
            try:
                self._announce(text, NOTICE_EMOTION)
            except Exception:
                logger.exception("Could not say a free offers notice")
        if self._notifier is not None:
            try:
                self._notifier.send(self._title, text, tags=[tag], click=link)
            except Exception:
                logger.exception("Could not push a free offers notice")


def _recent(moment: datetime | None, now: datetime, span: timedelta) -> bool:
    """Whether `moment` came less than `span` before `now`; one in the future (the clock was
    set back since) does not count as recent."""
    return moment is not None and timedelta(0) <= now - moment < span


def _free_now(game: GameGiveaway, now: datetime) -> bool:
    """Whether the game can be claimed now: a game free only soon is told once it starts."""
    return game.starts_at is None or game.starts_at <= now


def _games_notice(games: Sequence[GameGiveaway], owner: str, today: date) -> str:
    """`Master, ada game gratis baru: Bad Cheese dan Agent A di Epic, sampai Kamis 22.00.`: the
    first games named, grouped by store and end, the rest counted, and GamerPower.com credited
    (as its terms ask) when any of them came from it, without parentheses: her voice drops
    them."""
    groups: dict[tuple[str, str], list[str]] = {}
    for game in games[:NAMES_IN_NOTICE]:
        store = _STORE_NAMES.get(game.platform, game.platform)
        groups.setdefault((store, _until(game, today)), []).append(game.title)
    parts = [
        f"{_joined(titles)} di {store}{f', sampai {until}' if until else ''}"
        for (store, until), titles in groups.items()
    ]
    more = len(games) - NAMES_IN_NOTICE
    rest = f", dan {more} game lainnya" if more > 0 else ""
    credit = f", infonya dari {GAMERPOWER}" if any(g.source == GAMERPOWER for g in games) else ""
    return f"{owner}, ada game gratis baru: {'; '.join(parts)}{rest}{credit}."


def _until(game: GameGiveaway, today: date) -> str:
    """When a game stops being free, as she says it: `Kamis 22.00`, or a day alone for
    GamerPower's dates; empty without an end."""
    if game.ends_at is not None:
        end = game.ends_at.astimezone()  # this PC's time at that moment
        return f"{_day(end.date(), today)} {end:%H.%M}"
    if game.ends_on is not None:
        return _day(game.ends_on, today)
    return ""


def _day(day: date, today: date) -> str:
    """`hari ini`, `besok`, a weekday within the coming week, else `29 Oktober`."""
    days = (day - today).days
    if days == 0:
        return "hari ini"
    if days == 1:
        return "besok"
    if 1 < days < 7:
        return WEEKDAYS_ID[day.weekday()]
    return f"{day.day} {MONTHS_ID[day.month - 1]}"


def _ai_notice(offers: Sequence[AiOffer], owner: str, *, pushed: bool, opens_link: bool) -> str:
    """`Master, aku menemukan 2 promo AI baru: ... `: the first offers named, the rest counted,
    then what Master can do. She never asks a yes-or-no question here: an announcement opens
    no follow-up window, and her conversation never hears it. The push (`pushed`) opens the
    first offer's link when it is on the provider's own site (`opens_link`)."""
    names = [_offer_name(offer) for offer in offers[:NAMES_IN_NOTICE]]
    more = len(offers) - NAMES_IN_NOTICE
    if more > 0:
        names.append(f"{more} lainnya")
    if not pushed:
        ending = "Tanya aku kalau mau tahu lebih lanjut."
    elif not opens_link:
        ending = "Minta aku kalau mau linknya dikirim ke HP."
    elif len(offers) == 1:
        ending = "Linknya ada di notifikasi HP."
    else:
        ending = "Link yang pertama ada di notifikasi HP; minta aku kalau mau link yang lain."
    return f"{owner}, aku menemukan {len(offers)} promo AI baru: {_joined(names)}. {ending}"


def _offer_name(offer: AiOffer) -> str:
    """`Claude for Startups dari Anthropic`; a title that names its provider says it once."""
    if offer.provider.casefold() in offer.title.casefold():
        return offer.title
    return f"{offer.title} dari {offer.provider}"


def _joined(names: Sequence[str]) -> str:
    """`A`, `A dan B`, or `A, B, dan C`."""
    if len(names) <= 2:
        return " dan ".join(names)
    return f"{', '.join(names[:-1])}, dan {names[-1]}"
