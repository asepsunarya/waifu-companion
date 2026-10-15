"""What Cyrene already told the owner about free offers, and the AI offers her scans found.

`OffersStore` keeps four things in one JSON file (`free_offers.state_path`):
- the free games already announced, by id, each with the local day it ends (or, for a game
  without an end, the last day it was still listed); an id is forgotten 30 days after that
  day, so a game given away again later is new again;
- the AI offers the scans found, each with when it was first seen and when a scan last
  reported it; an offer is dropped the day after its deadline, or 90 days after a scan last
  reported it (scans report again the known offers still open, so one unreported that long
  has ended; one found again after that is new again);
- when the last AI scan ran, and when the last one that failed after it was paid for did.

The file follows the rules of `memory.py`: every call re-reads it, so `waifu offers` sees
what a running Cyrene saved. One lock serialises the calls of every store in this process;
between processes the last writer wins. A write goes to a temp file in the same folder,
reaches the disk, and then replaces the file. Reading never creates it, and a file that is
not an offers record is moved aside before anything is written: it is never overwritten.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from waifu.gacha.files import write_json_atomic
from waifu.offers.checks import MAX_CHARS
from waifu.offers.values import AI_ACCESS, AI_KINDS, AiOffer, GameGiveaway

logger = logging.getLogger(__name__)

FILE_VERSION = 1
# How long an announced game is remembered after it ended.
GAME_MEMORY = timedelta(days=30)
# How long an AI offer is kept after a scan last reported it.
AI_OFFER_UNSEEN = timedelta(days=90)
_FILE_KEYS = {"version", "games", "ai_offers", "last_ai_scan"}
# Kept since failed scans back off; a file written before has none.
_FAILED_SCAN_KEY = "last_failed_ai_scan"
_TEXT_KEYS = ("id", "title", "provider", "eligibility", "url", "summary")
_OFFER_KEYS = {*_TEXT_KEYS, "kind", "access", "deadline", "first_seen", "last_seen"}
# date.fromisoformat also reads 20261022 and 2026-W43-4; the file holds only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# One lock for every store: two stores on the same file in this process must not lose
# each other's changes.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class _KeptOffer:
    offer: AiOffer
    last_seen: datetime  # the last scan that reported it


@dataclass(frozen=True)
class _State:
    games: dict[str, date]  # announced game id -> the day it ends (or was last listed)
    offers: tuple[_KeptOffer, ...]  # in the order they were first found
    last_ai_scan: datetime | None
    last_failed_ai_scan: datetime | None = None


_EMPTY = _State({}, (), None)


class OffersStore:
    def __init__(self, path: Path | str, *, now: Callable[[], datetime] = datetime.now) -> None:
        """`now` is local time, like the scans' `first_seen` and `scanned_at`."""
        self.path = Path(path)
        self._now = now

    def games(self) -> dict[str, date]:
        """The games already announced, by id, with the day each ends (or was last listed)."""
        with _LOCK:
            return dict(self._read().games)

    def ai_offers(self) -> list[AiOffer]:
        """The AI offers not dropped yet, in the order they were first found."""
        with _LOCK:
            return [kept.offer for kept in self._read().offers]

    def last_ai_scan(self) -> datetime | None:
        """When the last AI scan ran, or None when none ever did."""
        with _LOCK:
            return self._read().last_ai_scan

    def last_failed_ai_scan(self) -> datetime | None:
        """When the last AI scan that failed after it was paid for ran, or None."""
        with _LOCK:
            return self._read().last_failed_ai_scan

    def record_failed_ai_scan(self, at: datetime) -> None:
        """Keep `at` as the time of the last AI scan that failed after it was paid for."""
        with _LOCK:
            before = self._read()
            self._save(before, dataclasses.replace(before, last_failed_ai_scan=at))

    def remember_games(self, games: Iterable[GameGiveaway]) -> list[GameGiveaway]:
        """Mark `games` announced and return the ones that were not yet, in order, each once.

        A game without an end is kept 30 days after the last call that listed it; a game's
        end never moves earlier.
        """
        today = self._now().date()
        with _LOCK:
            before = self._read()
            ends = dict(before.games)
            new: list[GameGiveaway] = []
            for game in games:
                if game.id not in ends:
                    new.append(game)
                end = _game_end(game, today)
                ends[game.id] = max(ends.get(game.id, end), end)
            after = self._save(before, dataclasses.replace(before, games=ends))
        return [game for game in new if game.id in after.games]

    def merge_ai_offers(self, offers: Iterable[AiOffer], scanned_at: datetime) -> list[AiOffer]:
        """Add what a scan at `scanned_at` reported and return the offers that are new.

        An offer already kept (same id) keeps its `first_seen` and takes everything else from
        the report. An offer whose deadline has passed is neither new nor kept.
        """
        with _LOCK:
            before = self._read()
            kept = {entry.offer.id: entry for entry in before.offers}
            new_ids: list[str] = []
            for offer in offers:
                old = kept.get(offer.id)
                if old is None:
                    new_ids.append(offer.id)
                else:
                    offer = dataclasses.replace(offer, first_seen=old.offer.first_seen)
                kept[offer.id] = _KeptOffer(offer, scanned_at)
            after = self._save(
                before,
                dataclasses.replace(before, offers=tuple(kept.values()), last_ai_scan=scanned_at),
            )
        live = {entry.offer.id: entry.offer for entry in after.offers}
        return [live[offer_id] for offer_id in new_ids if offer_id in live]

    def _read(self) -> _State:
        """What is still kept; a missing file is an empty store and stays missing."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            state = _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return _EMPTY
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem)
            return _EMPTY
        return _live(state, self._now().date())

    def _save(self, before: _State, after: _State) -> _State:
        """Write `after` without what has expired, unless that is what `before` already held."""
        after = _live(after, self._now().date())
        if after != before:
            self._write(after)
        return after

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not an offers record, so that no write ever replaces it."""
        name = f"{self.path.name}.corrupt-{self._now():%Y%m%d-%H%M%S}"
        target = self.path.with_name(name)
        counter = 2
        while target.exists():
            target = self.path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(self.path, target)
        except OSError as exc:
            logger.error(
                "Offers file %s is not an offers record (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Offers file %s is not an offers record (%s); moved it to %s and started an empty one",
            self.path,
            problem,
            target,
        )

    def _write(self, state: _State) -> None:
        write_json_atomic(
            self.path,
            {
                "version": FILE_VERSION,
                "games": {game_id: end.isoformat() for game_id, end in state.games.items()},
                "ai_offers": [_dump(entry) for entry in state.offers],
                "last_ai_scan": _dump_time(state.last_ai_scan),
                _FAILED_SCAN_KEY: _dump_time(state.last_failed_ai_scan),
            },
        )


def _game_end(game: GameGiveaway, today: date) -> date:
    """The local day `game` ends; `today` for a game without an end, since it is listed today."""
    if game.ends_at is not None:
        return game.ends_at.astimezone().date()
    return game.ends_on or today


def _live(state: _State, today: date) -> _State:
    """`state` without the games forgotten and the AI offers dropped by `today`."""
    return dataclasses.replace(
        state,
        games={game_id: end for game_id, end in state.games.items() if today <= end + GAME_MEMORY},
        offers=tuple(entry for entry in state.offers if _is_live(entry, today)),
    )


def _is_live(entry: _KeptOffer, today: date) -> bool:
    deadline = entry.offer.deadline
    return (deadline is None or today <= deadline) and (
        today <= entry.last_seen.date() + AI_OFFER_UNSEEN
    )


def _dump_time(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _dump(entry: _KeptOffer) -> dict[str, object]:
    offer = entry.offer
    return {
        "id": offer.id,
        "title": offer.title,
        "provider": offer.provider,
        "kind": offer.kind,
        "access": offer.access,
        "eligibility": offer.eligibility,
        "deadline": None if offer.deadline is None else offer.deadline.isoformat(),
        "url": offer.url,
        "summary": offer.summary,
        "first_seen": offer.first_seen.isoformat(),
        "last_seen": entry.last_seen.isoformat(),
    }


def _parse(raw: object) -> _State:
    """The state in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() - {_FAILED_SCAN_KEY} != _FILE_KEYS:
        raise ValueError(
            f"expected an object with exactly the keys {sorted(_FILE_KEYS)} "
            f"and maybe {_FAILED_SCAN_KEY}"
        )
    version, games, offers, last_scan, last_failed = (
        raw["version"],
        raw["games"],
        raw["ai_offers"],
        raw["last_ai_scan"],
        raw.get(_FAILED_SCAN_KEY),
    )
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if not isinstance(games, dict):
        raise ValueError("games must be an object of game ids and days")
    if not isinstance(offers, list):
        raise ValueError("ai_offers must be a list")
    kept = tuple(_parse_offer(index, entry) for index, entry in enumerate(offers))
    ids = [entry.offer.id for entry in kept]
    if len(set(ids)) != len(ids):
        raise ValueError("two AI offers share an id")
    return _State(
        {game_id: _parse_date(end, f"games[{game_id}]") for game_id, end in games.items()},
        kept,
        None if last_scan is None else _parse_time(last_scan, "last_ai_scan"),
        None if last_failed is None else _parse_time(last_failed, _FAILED_SCAN_KEY),
    )


def _parse_offer(index: int, entry: object) -> _KeptOffer:
    where = f"ai_offers[{index}]"
    if not isinstance(entry, dict) or entry.keys() != _OFFER_KEYS:
        raise ValueError(f"{where} must be an object with exactly the keys {sorted(_OFFER_KEYS)}")
    for key in _TEXT_KEYS:
        if not isinstance(entry[key], str):
            raise ValueError(f"{where}.{key} must be a string")
    if not entry["id"]:
        raise ValueError(f"{where}.id must not be empty")
    for key, limit in MAX_CHARS.items():
        if len(entry[key]) > limit:
            raise ValueError(f"{where}.{key} must be at most {limit} characters")
    if entry["kind"] not in AI_KINDS:
        raise ValueError(f"{where}.kind must be one of {', '.join(AI_KINDS)}")
    if entry["access"] not in AI_ACCESS:
        raise ValueError(f"{where}.access must be one of {', '.join(AI_ACCESS)}")
    if not entry["url"].startswith("https://"):
        raise ValueError(f"{where}.url must be an https link")
    deadline = entry["deadline"]
    offer = AiOffer(
        id=entry["id"],
        title=entry["title"],
        provider=entry["provider"],
        kind=entry["kind"],
        access=entry["access"],
        eligibility=entry["eligibility"],
        deadline=None if deadline is None else _parse_date(deadline, f"{where}.deadline"),
        url=entry["url"],
        summary=entry["summary"],
        first_seen=_parse_time(entry["first_seen"], f"{where}.first_seen"),
    )
    return _KeptOffer(offer, _parse_time(entry["last_seen"], f"{where}.last_seen"))


def _parse_date(value: object, where: str) -> date:
    if isinstance(value, str) and _ISO_DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass  # a day that does not exist, such as 2026-02-30
    raise ValueError(f"{where} must be a date written YYYY-MM-DD")


def _parse_time(value: object, where: str) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"{where} must be an ISO 8601 time")
