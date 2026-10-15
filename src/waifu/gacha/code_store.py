"""The redeem codes Cyrene knows for Master's games, what redeeming them did, and what she told.

`CodeStore` keeps, per game, every code the source listed (or Master named): its rewards, when
it was first and last listed, whether the latest read of its game listed it, whether Master
named it, its last outcome on HoYoLAB with when that was recorded, and whether she announced
it. A failed attempt (cooldown, cookie, error) never replaces a final outcome: it says nothing
new about the code. The source drops a code once it finds it no longer works, so a code the
latest read left out is no longer one to redeem or announce, unless Master named it; it is
remembered, so it is not new when listed again. A code the source has not listed for 60 days
is forgotten when that game's codes are read next, so one listed again after that is new
again; a code still listed keeps what happened to it however long Cyrene was off. The codes
to redeem or announce come newest first, in the source's order within one read (newest
first too).

The file (`redeem_codes.state_path`) follows the rules of `memory.py`: every call re-reads it,
so `waifu gacha codes` sees what a running Cyrene saved. One lock serialises the calls of every
store in this process; between processes the last writer wins. A write goes to a temp file in
the same folder, reaches the disk, and then replaces the file. Reading never creates it, and a
file that is not a codes record is moved aside before anything is written: it is never
overwritten.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast, get_args

from waifu.config import GameId
from waifu.gacha.codes import (
    FINAL_OUTCOMES,
    MAX_REWARDS_CHARS,
    REDEEM_OUTCOMES,
    RedeemCode,
    RedeemResult,
    normalize_code,
)
from waifu.gacha.files import write_json_atomic

logger = logging.getLogger(__name__)

FILE_VERSION = 1
# How long a code is kept after the source last listed it.
FORGET_AFTER = timedelta(days=60)
_GAME_IDS: tuple[GameId, ...] = get_args(GameId)
_FILE_KEYS = {"version", "games"}
_ENTRY_KEYS = {
    "code",
    "rewards",
    "first_seen",
    "last_seen",
    "listed",
    "named",
    "outcome",
    "outcome_at",
    "announced",
}

# One lock for every store: two stores on the same file in this process must not lose
# each other's changes.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class _Outcome:
    name: str  # one of REDEEM_OUTCOMES
    at: datetime  # when it was recorded


@dataclass(frozen=True)
class _Kept:
    code: RedeemCode
    last_seen: datetime  # the last time the source listed it (or Master named it first)
    listed: bool  # whether the latest read of its game listed it
    named: bool = False  # Master named it: it stays to redeem though no read lists it
    outcome: _Outcome | None = None  # None: never tried
    announced: bool = False


# Each game's codes by code, in the order they were first seen; a game without codes is absent.
_State = dict[GameId, dict[str, _Kept]]


def _local_now() -> datetime:
    return datetime.now().astimezone()


class CodeStore:
    def __init__(self, path: Path | str, *, now: Callable[[], datetime] = _local_now) -> None:
        """`now` (aware) stamps the recorded outcomes and the name of a corrupt file set
        aside."""
        self.path = Path(path)
        self._now = now

    def add(
        self, game: GameId, codes: Iterable[tuple[str, str]], now: datetime
    ) -> list[RedeemCode]:
        """Keep `codes`, `(code, rewards)` a successful read of `game` at `now` (aware) found
        in the source, and return the new ones, in order, each once.

        Every code listed counts as seen at `now`; a non-empty rewards text replaces the one
        kept. The other codes of `game` are no longer listed, and those not listed for 60
        days are forgotten. ValueError, with nothing written, for a game, code, rewards text,
        or time the file cannot hold.
        """
        listed = _checked_listing(game, codes, now)
        with _LOCK:
            before = self._read()
            kept = {
                code: dataclasses.replace(entry, listed=False)
                for code, entry in before.get(game, {}).items()
            }
            new: list[RedeemCode] = []
            for code, rewards in listed:
                old = kept.get(code)
                if old is None:
                    entry = _Kept(RedeemCode(game, code, rewards, now), now, listed=True)
                    new.append(entry.code)
                else:
                    seen = dataclasses.replace(old.code, rewards=rewards or old.code.rewards)
                    entry = dataclasses.replace(old, code=seen, last_seen=now, listed=True)
                kept[code] = entry
            kept = {
                code: entry for code, entry in kept.items() if now - entry.last_seen < FORGET_AFTER
            }
            self._save(before, {**before, game: kept})
        return new

    def add_named(self, game: GameId, codes: Iterable[str], now: datetime) -> None:
        """Keep `codes` Master named for `game` at `now` (aware) as Master's: they stay to
        redeem though no read lists them, and count as announced, since Master knows them. A
        code already kept keeps its rewards. ValueError, with nothing written, for a game,
        code, or time the file cannot hold."""
        named = [code for code, _ in _checked_listing(game, [(text, "") for text in codes], now)]
        with _LOCK:
            before = self._read()
            kept = dict(before.get(game, {}))
            for code in named:
                old = kept.get(code)
                if old is None:
                    old = _Kept(RedeemCode(game, code, "", now), now, listed=False)
                kept[code] = dataclasses.replace(old, named=True, announced=True)
            self._save(before, {**before, game: kept})

    def unredeemed(self, game: GameId) -> list[RedeemCode]:
        """The codes of `game` to redeem: without a final outcome, and listed by the latest
        read or named by Master; newest first."""
        return [entry.code for entry in self._current(game)]

    def unannounced(self, game: GameId) -> list[RedeemCode]:
        """The codes of `game` to redeem that were not announced yet, newest first."""
        return [entry.code for entry in self._current(game) if not entry.announced]

    def recent(self, game: GameId, n: int) -> list[tuple[RedeemCode, str]]:
        """The last `n` codes of `game` with an outcome, the most recently recorded first,
        each with that outcome."""
        tried = [
            (entry.outcome, entry.code) for entry in self._codes(game) if entry.outcome is not None
        ]
        tried.sort(key=lambda pair: pair[0].at, reverse=True)
        return [(code, outcome.name) for outcome, code in tried[:n]]

    def record(self, results: Iterable[RedeemResult]) -> None:
        """Keep each result's outcome as its code's last one, unless a failed attempt would
        replace a final outcome. A result for a code not kept is logged and skipped; an
        outcome not in REDEEM_OUTCOMES, or a naive clock, is a ValueError, with nothing
        recorded."""
        results = list(results)
        for result in results:
            if result.outcome not in REDEEM_OUTCOMES:
                raise ValueError(f"unknown redeem outcome: {result.outcome!r}")
        at = _aware(self._now(), "the time an outcome is recorded")
        with _LOCK:
            before = self._read()
            after = {game: dict(entries) for game, entries in before.items()}
            for result in results:
                entry = after.get(result.game, {}).get(result.code)
                if entry is None:
                    logger.warning(
                        "No %s redeem code %s is kept; its outcome %s is not recorded",
                        result.game,
                        result.code,
                        result.outcome,
                    )
                    continue
                if _settled(entry) and result.outcome not in FINAL_OUTCOMES:
                    continue
                outcome = _Outcome(result.outcome, at)
                after[result.game][result.code] = dataclasses.replace(entry, outcome=outcome)
            self._save(before, after)

    def mark_announced(self, codes: Iterable[RedeemCode]) -> None:
        """Mark `codes` announced, so that a restart never announces them again; codes not
        kept are skipped."""
        with _LOCK:
            before = self._read()
            after = {game: dict(entries) for game, entries in before.items()}
            for code in codes:
                entry = after.get(code.game, {}).get(code.code)
                if entry is not None:
                    after[code.game][code.code] = dataclasses.replace(entry, announced=True)
            self._save(before, after)

    def _codes(self, game: GameId) -> list[_Kept]:
        with _LOCK:
            return list(self._read().get(game, {}).values())

    def _current(self, game: GameId) -> list[_Kept]:
        """The codes of `game` still to redeem, newest first; codes first seen together keep
        the order they were listed in."""
        current = [
            entry
            for entry in self._codes(game)
            if (entry.listed or entry.named) and not _settled(entry)
        ]
        return sorted(current, key=lambda entry: entry.code.first_seen, reverse=True)

    def _read(self) -> _State:
        """What is kept; a missing file is an empty store and stays missing."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return {}
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem)
            return {}

    def _save(self, before: _State, after: _State) -> None:
        """Write `after`, without its games that have no codes, unless `before` already held
        that."""
        after = {game: entries for game, entries in after.items() if entries}
        if after != before:
            self._write(after)

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not a codes record, so that no write ever replaces it."""
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
                "Redeem codes file %s is not a codes record (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Redeem codes file %s is not a codes record (%s); moved it to %s and started an "
            "empty one",
            self.path,
            problem,
            target,
        )

    def _write(self, state: _State) -> None:
        write_json_atomic(
            self.path,
            {
                "version": FILE_VERSION,
                "games": {
                    game: [_dump(entry) for entry in entries.values()]
                    for game, entries in state.items()
                },
            },
        )


def _settled(entry: _Kept) -> bool:
    return entry.outcome is not None and entry.outcome.name in FINAL_OUTCOMES


def _aware(moment: datetime, what: str) -> datetime:
    """`moment`; a naive one is a ValueError, since the file holds only times with an offset."""
    if moment.tzinfo is None:
        raise ValueError(f"{what} must be aware")
    return moment


def _checked_listing(
    game: GameId, codes: Iterable[tuple[str, str]], now: datetime
) -> list[tuple[str, str]]:
    """`codes` normalized; ValueError for what the file cannot hold."""
    if game not in _GAME_IDS:
        raise ValueError(f"unknown game: {game!r}")
    _aware(now, "the time codes are seen")
    listed = []
    for text, rewards in codes:
        code = normalize_code(text)
        if code is None:
            raise ValueError(f"not a redeem code: {text!r:.40}")
        if len(rewards) > MAX_REWARDS_CHARS:
            raise ValueError(f"the rewards of {code} are over {MAX_REWARDS_CHARS} characters")
        listed.append((code, rewards))
    return listed


def _dump(entry: _Kept) -> dict[str, object]:
    outcome = entry.outcome
    return {
        "code": entry.code.code,
        "rewards": entry.code.rewards,
        "first_seen": entry.code.first_seen.isoformat(),
        "last_seen": entry.last_seen.isoformat(),
        "listed": entry.listed,
        "named": entry.named,
        "outcome": None if outcome is None else outcome.name,
        "outcome_at": None if outcome is None else outcome.at.isoformat(),
        "announced": entry.announced,
    }


def _parse(raw: object) -> _State:
    """The state in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version, games = raw["version"], raw["games"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if not isinstance(games, dict):
        raise ValueError("games must be an object of games and their codes")
    state: _State = {}
    for name, entries in games.items():
        if name not in _GAME_IDS:
            raise ValueError(f"games[{name}]: unknown game, expected one of {', '.join(_GAME_IDS)}")
        if not isinstance(entries, list):
            raise ValueError(f"games[{name}] must be a list")
        game = cast(GameId, name)
        kept: dict[str, _Kept] = {}
        for index, entry in enumerate(entries):
            parsed = _parse_entry(game, f"games[{name}][{index}]", entry)
            if parsed.code.code in kept:
                raise ValueError(f"games[{name}] holds {parsed.code.code} twice")
            kept[parsed.code.code] = parsed
        if kept:
            state[game] = kept
    return state


def _parse_entry(game: GameId, where: str, entry: object) -> _Kept:
    if not isinstance(entry, dict) or entry.keys() != _ENTRY_KEYS:
        raise ValueError(f"{where} must be an object with exactly the keys {sorted(_ENTRY_KEYS)}")
    code, rewards, listed, named, outcome, outcome_at, announced = (
        entry["code"],
        entry["rewards"],
        entry["listed"],
        entry["named"],
        entry["outcome"],
        entry["outcome_at"],
        entry["announced"],
    )
    if not isinstance(code, str) or normalize_code(code) != code:
        raise ValueError(f"{where}.code must be 5 to 24 uppercase letters or digits")
    if not isinstance(rewards, str) or len(rewards) > MAX_REWARDS_CHARS:
        raise ValueError(f"{where}.rewards must be a string of at most {MAX_REWARDS_CHARS}")
    if outcome is not None and outcome not in REDEEM_OUTCOMES:
        raise ValueError(f"{where}.outcome must be null or one of {', '.join(REDEEM_OUTCOMES)}")
    if (outcome is None) != (outcome_at is None):
        raise ValueError(f"{where}.outcome and outcome_at must both be null or both be set")
    for key, flag in (("listed", listed), ("named", named), ("announced", announced)):
        if type(flag) is not bool:
            raise ValueError(f"{where}.{key} must be true or false")
    first_seen = _parse_time(entry["first_seen"], f"{where}.first_seen")
    last_seen = _parse_time(entry["last_seen"], f"{where}.last_seen")
    tried = None
    if outcome is not None:
        tried = _Outcome(outcome, _parse_time(outcome_at, f"{where}.outcome_at"))
    return _Kept(
        RedeemCode(game, code, rewards, first_seen), last_seen, listed, named, tried, announced
    )


def _parse_time(value: object, where: str) -> datetime:
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if moment.tzinfo is not None:
                return moment
    raise ValueError(f"{where} must be an ISO 8601 time with its offset")
