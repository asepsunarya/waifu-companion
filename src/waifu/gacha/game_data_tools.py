"""The three tools that tell Master about their own HoYoverse game accounts.

`game_characters`, `game_endgame`, and `game_events` read HoYoLAB only when asked, for one
of the configured games (Honkai: Star Rail, Genshin Impact, Zenless Zone Zero). The specs
are static English built once from the configured games, with no live data, so they stay
byte-identical for the brain's lifetime and keep the prompt cache warm. Results are compact
JSON with local times, and `run` never raises: invalid arguments, unknown names, and
HoYoLAB failures become error results. What HoYoLAB answered is kept in memory for a few
minutes, never on disk.
"""

from __future__ import annotations

import difflib
import json
import math
import threading
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Generic, TypeVar

from waifu.brain.base import ToolSpec
from waifu.gacha.game_data import (
    GAME_MODES,
    MODE_GAMES,
    Banner,
    ChallengeWindow,
    Endgame,
    EndgameStage,
    GameCalendar,
    GameDataSource,
    GameEvent,
    is_over,
    is_running,
)
from waifu.gacha.games import GAMES
from waifu.gacha.genshin_impact import GenshinCharacter, GenshinCharacterDetail, GenshinWeapon
from waifu.gacha.hoyolab import GachaError
from waifu.gacha.starrail import (
    HsrCalendar,
    HsrChallengeWindow,
    HsrCharacter,
    HsrEndgame,
    HsrEvent,
    HsrFloor,
    HsrWarp,
)
from waifu.gacha.tools import LOCAL_TIME
from waifu.gacha.zenless import ZzzAgent, ZzzAgentDetail

T = TypeVar("T")

GAME_CHARACTERS = "game_characters"
GAME_ENDGAME = "game_endgame"
GAME_EVENTS = "game_events"
NAME_MAX_CHARS = 60
HSR_ROSTER_COLUMNS = (
    "name",
    "rarity",
    "level",
    "eidolon",
    "path",
    "element",
    "light_cone",
    "lc_level",
    "superimposition",
)
GENSHIN_ROSTER_COLUMNS = (
    "name",
    "rarity",
    "level",
    "constellation",
    "element",
    "weapon_type",
    "weapon",
    "weapon_level",
    "refinement",
)
ZZZ_ROSTER_COLUMNS = ("name", "rarity", "level", "mindscape", "element", "specialty")
ROSTER_MAX_ROWS = 120
MATCHES_MAX = 3
MORE_MAX = 6  # every other Path of one name: genshin.py knows 9 Paths
CLOSE_NAMES_MAX = 3
STATS_MAX = 14
RELICS_MAX = 6
ARTIFACTS_MAX = 5
DISCS_MAX = 6
STAGES_MAX = 4
BANNERS_MAX = 8
EVENTS_MAX = 15
EVENT_NAME_MAX_CHARS = 60
WINDOWS_MAX = 4
CHARACTERS_TTL = timedelta(minutes=15)
DETAIL_TTL = timedelta(minutes=15)
ENDGAME_TTL = timedelta(minutes=10)
CALENDAR_TTL = timedelta(minutes=60)
_CHARACTERS_KEYS = frozenset({"game", "name"})
_ENDGAME_KEYS = frozenset({"game", "mode"})
_EVENTS_KEYS = frozenset({"game"})
_ZZZ_RANKS = {"S": 2, "A": 1}  # S-rank agents first
_WINDOW_GAMES = frozenset({"hsr", "genshin"})  # ZZZ's calendars carry no endgame windows

# What each game's description adds; every one is built only from the configured games.
_TERMS = {
    "hsr": ("Eidolon", "Light Cone", "Superimposition", "Memory of Chaos"),
    "genshin": ("Constellation", "Refinement", "Spiral Abyss"),
    "zzz": ("Mindscape Cinema", "W-Engine", "Shiyu Defense"),
}
_DUPLICATES = {"hsr": "Eidolon", "genshin": "Constellation", "zzz": "Mindscape Cinema"}
_GEAR = {"hsr": "Light Cone", "genshin": "weapon", "zzz": "W-Engine"}
_CHARACTERS_FRAGMENTS = {
    "hsr": (
        "Honkai: Star Rail rows give rarity, level, Eidolon, Path, element, and the equipped "
        "Light Cone with its level and Superimposition; a named character adds final stats, "
        "relic main stats, and memosprite. Characters that share a name, such as both March "
        "7th Paths or the Trailblazer's Paths, come back together, told apart by `path`, and a "
        "name with its Path, such as Trailblazer Harmony, gives that one character. "
    ),
    "genshin": (
        "Genshin Impact rows give rarity, level, Constellation, element, weapon type, and the "
        "weapon with its level and Refinement; a named character adds final stats, the "
        "weapon's stats, artifact main stats, and the sets worn, and a name with its element, "
        "such as Traveler Anemo, gives that one character. "
    ),
    "zzz": (
        "Zenless Zone Zero rows give each agent's rank S or A, level, Mindscape Cinema, "
        "element, and specialty; a named agent adds final stats, the W-Engine with its level "
        "and Refinement, Drive Disc main stats, and the sets worn, and the full name, such as "
        "Ellen Joe, works too. "
    ),
}
_ENDGAME_FRAGMENTS = {
    "hsr": (
        "Honkai: Star Rail has Memory of Chaos, Pure Fiction, Apocalyptic Shadow, and Anomaly "
        "Arbitration, each with the stars and extra stars earned, the deepest floor, the "
        "battles fought, and the best floors with their cycles for Memory of Chaos or their "
        "score for Pure Fiction and Apocalyptic Shadow; they give no maximum stars, so they "
        "cannot tell what is left. "
    ),
    "genshin": (
        "Genshin Impact has Spiral Abyss, with its stars, deepest chamber such as 12-3, "
        "battles, and its four highest floors with their stars and `max_stars`; Imaginarium "
        "Theater, with its difficulty, deepest act, and stars; and Stygian Onslaught, with its "
        "difficulty, clear time in `clear_seconds`, and each challenge's clear time. "
    ),
    "zzz": (
        "Zenless Zone Zero has Shiyu Defense, with its rating, score and `max_score`, and "
        "clear time, or its deepest frontier and fastest clear, and each stage's rating; and "
        "Deadly Assault, with its score, stars, and each boss's stars and score. "
    ),
}
_EVENTS_FRAGMENTS = {
    "hsr": (
        "Honkai: Star Rail lists its banners under `warps`, with their featured 5-star and "
        "4-star characters or Light Cones. "
    ),
    "genshin": (
        "Genshin Impact's banners are character, weapon, or chronicled, with the 5-star names "
        "in `featured` and the 4-star ones in `four_stars`, and its endgame windows are Spiral "
        "Abyss and Imaginarium Theater. "
    ),
    "zzz": (
        "Zenless Zone Zero's banners are agent or W-Engine channels, with the S-rank agents in "
        "`featured` and the A-rank ones in `four_stars`, none for a W-Engine channel, and it "
        "has no endgame windows. "
    ),
}


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


@dataclass(frozen=True)
class _Fetched(Generic[T]):
    """What HoYoLAB answered, and the local time it was read."""

    at: datetime
    value: T


@dataclass(frozen=True)
class _Roster:
    """How one game's characters are listed, matched, and shown."""

    columns: tuple[str, ...]
    row: Callable[[Any], list[object]]
    order: Callable[[Any], tuple[Any, ...]]
    exact_keys: Callable[[Any], set[str]]  # what a whole `name` may be, reduced for matching
    names: Callable[[Any], tuple[str, ...]]  # where a part of `name` is looked for
    other: Callable[[Any], dict[str, object]]  # a match beyond the three shown, under `more`
    shown: Callable[[Any, Any], dict[str, Any]]  # a shown match and its detail (None: none)
    detailed: bool  # the roster holds each character's detail already: no second request


def _local_now() -> datetime:
    return datetime.now().astimezone()


class GameDataTools:
    """A `ToolProvider` for the brain: Master's accounts in the configured HoYoverse games.

    Successful reads are kept per process (`characters:<game>`, `detail:<game>:<ref>`,
    `endgame:<mode>`, `calendar:<game>`) for their TTL. The lock guards only the cache's dict:
    voice and phone turns run on different threads, and neither waits for the other's
    HoYoLAB request.
    """

    def __init__(
        self,
        source: GameDataSource,
        games: Sequence[str],
        *,
        now: Callable[[], datetime] = _local_now,
    ) -> None:
        self._source = source
        self._games = list(games)
        self._modes = [mode for game in self._games for mode in GAME_MODES[game]]
        self._now = now
        self._specs = _specs(self._games, self._modes)
        self._cache: dict[str, _Fetched[Any]] = {}
        self._cache_lock = threading.Lock()

    def specs(self) -> list[ToolSpec]:
        return list(self._specs)

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == GAME_CHARACTERS:
                given = _arguments_mapping(arguments, allowed=_CHARACTERS_KEYS)
                character = _name_argument(given.get("name"))
                return self._characters(self._game(given.get("game")), character)
            if name == GAME_ENDGAME:
                given = _arguments_mapping(arguments, allowed=_ENDGAME_KEYS)
                mode = self._mode(given.get("mode"))
                return self._endgame(self._game(given.get("game"), mode), mode)
            if name == GAME_EVENTS:
                given = _arguments_mapping(arguments, allowed=_EVENTS_KEYS)
                return self._events(self._game(given.get("game")))
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except GachaError as error:
            return _error(error.message)
        return _error(f"Tool '{name}' bukan tool data game.")

    def _game(self, given: object, mode: str | None = None) -> str:
        """The game asked about: the one given, else the one `mode` belongs to, else the only
        one configured. A mode of another game than the one given is invalid."""
        if given is not None:
            if not isinstance(given, str) or given not in self._games:
                raise _InvalidArguments(f"'game' harus salah satu dari: {', '.join(self._games)}.")
            if mode is not None and MODE_GAMES[mode] != given:
                raise _InvalidArguments(
                    f"'mode' untuk {given} harus salah satu dari: {', '.join(GAME_MODES[given])}."
                )
            return given
        if mode is not None:
            return MODE_GAMES[mode]
        if len(self._games) == 1:
            return self._games[0]
        raise _InvalidArguments(
            f"'game' harus diisi karena ada beberapa game: {', '.join(self._games)}."
        )

    def _mode(self, mode: object) -> str | None:
        if mode is None:
            return None
        if not isinstance(mode, str) or mode not in self._modes:
            raise _InvalidArguments(f"'mode' harus salah satu dari: {', '.join(self._modes)}.")
        return mode

    def _characters(self, game: str, query: str | None) -> tuple[str, bool]:
        now = self._now()
        roster = self._read(f"characters:{game}", CHARACTERS_TTL, now, self._roster_source(game))
        view = _ROSTERS[game]
        characters = sorted(roster.value, key=view.order)
        if query is None:
            return _to_json(_roster_json(view, characters, _local(roster.at, now))), False
        matches = _matching(view, characters, query)
        shown, others = matches[:MATCHES_MAX], matches[MATCHES_MAX:]
        reads = [roster.at]
        if view.detailed:
            entries = [view.shown(character, None) for character in shown]
        else:
            details = self._details(game, [character.ref for character in shown], now)
            reads += [detail.at for detail in details.values()]
            entries = [
                view.shown(character, _value(details.get(character.ref))) for character in shown
            ]
        data: dict[str, Any] = {
            # The oldest read among what is shown: a kept detail may be older than the roster.
            "as_of": _local(min(reads), now),
            "query": query,
            "characters": entries,
        }
        if others:
            # Named so they can be asked for by name and Path or element: the Trailblazer is
            # one character per Path, all named "Trailblazer", and a fourth must not vanish.
            data["more"] = [view.other(character) for character in others[:MORE_MAX]]
        if not matches:
            data["close"] = _close_names(characters, query)
        return _to_json(data), False

    def _details(self, game: str, refs: list[int], now: datetime) -> dict[int, _Fetched[Any]]:
        """The detail of each shown character: the kept ones from the cache, the others from
        HoYoLAB in one call, none at all when nothing is shown."""
        fetched: dict[int, _Fetched[Any]] = {}
        for ref in refs:
            kept = self._kept(f"detail:{game}:{ref}", DETAIL_TTL, now)
            if kept is not None:
                fetched[ref] = kept
        stale = [ref for ref in refs if ref not in fetched]
        if stale:
            for detail in self._details_source(game)(stale):
                ref = _detail_ref(detail)
                fetched[ref] = self._keep(f"detail:{game}:{ref}", _Fetched(now, detail))
        return fetched

    def _endgame(self, game: str, mode: str | None) -> tuple[str, bool]:
        now = self._now()
        modes = [mode] if mode else list(GAME_MODES[game])
        fetched, errors = self._read_endgame(game, modes, now)
        if not fetched:
            return _error(" ".join(dict.fromkeys(error.message for error in errors.values())))
        to_json = _hsr_endgame_json if game == "hsr" else _endgame_json
        data: dict[str, Any] = {
            # The oldest read among the modes shown: some may come from the cache.
            "as_of": _local(min(each.at for each in fetched.values()), now),
            "modes": [to_json(each, fetched[each].value, now) for each in modes if each in fetched],
        }
        if errors:
            data["errors"] = [
                {"mode": each, "message": errors[each].message} for each in modes if each in errors
            ]
        return _to_json(data), False

    def _events(self, game: str) -> tuple[str, bool]:
        now = self._now()
        calendar = self._read(f"calendar:{game}", CALENDAR_TTL, now, self._calendar_source(game))
        return _to_json(_calendar_json(game, calendar.value, _local(calendar.at, now), now)), False

    def _read_endgame(
        self, game: str, modes: list[str], now: datetime
    ) -> tuple[dict[str, _Fetched[Any]], dict[str, GachaError]]:
        """Each mode from the cache when fresh; the stale ones from HoYoLAB in one call."""
        fetched: dict[str, _Fetched[Any]] = {}
        for mode in modes:
            kept = self._kept(f"endgame:{mode}", ENDGAME_TTL, now)
            if kept is not None:
                fetched[mode] = kept
        stale = [mode for mode in modes if mode not in fetched]
        errors: dict[str, GachaError] = {}
        if stale:
            for mode, result in self._endgame_source(game)(stale).items():
                if isinstance(result, GachaError):
                    errors[mode] = result
                else:
                    fetched[mode] = self._keep(f"endgame:{mode}", _Fetched(now, result))
        return fetched, errors

    # Each source method is looked up only for the game asked about.

    def _roster_source(self, game: str) -> Callable[[], Sequence[Any]]:
        if game == "genshin":
            return self._source.genshin_characters
        if game == "zzz":
            return self._source.zzz_characters
        return self._source.hsr_characters

    def _details_source(self, game: str) -> Callable[[Sequence[int]], Sequence[Any]]:
        if game == "genshin":
            return self._source.genshin_character_details
        return self._source.zzz_character_details

    def _endgame_source(self, game: str) -> Callable[[list[str]], Mapping[str, Any]]:
        if game == "genshin":
            return self._source.genshin_endgame
        if game == "zzz":
            return self._source.zzz_endgame
        return self._source.hsr_endgame

    def _calendar_source(self, game: str) -> Callable[[], HsrCalendar | GameCalendar]:
        if game == "genshin":
            return self._source.genshin_calendar
        if game == "zzz":
            return self._source.zzz_calendar
        return self._source.hsr_calendar

    def _read(self, key: str, ttl: timedelta, now: datetime, fetch: Callable[[], T]) -> _Fetched[T]:
        kept = self._kept(key, ttl, now)
        if kept is not None:
            return kept
        return self._keep(key, _Fetched(now, fetch()))

    def _kept(self, key: str, ttl: timedelta, now: datetime) -> _Fetched[Any] | None:
        with self._cache_lock:
            kept = self._cache.get(key)
        # A clock set back makes the age negative: read again rather than trust it.
        if kept is None or not timedelta(0) <= now - kept.at < ttl:
            return None
        return kept

    def _keep(self, key: str, fetched: _Fetched[T]) -> _Fetched[T]:
        with self._cache_lock:
            self._cache[key] = fetched
        return fetched


def _value(fetched: _Fetched[T] | None) -> T | None:
    return None if fetched is None else fetched.value


def _detail_ref(detail: GenshinCharacterDetail | ZzzAgentDetail) -> int:
    if isinstance(detail, GenshinCharacterDetail):
        return detail.character.ref
    return detail.agent.ref


# --- the specs -----------------------------------------------------------------------------


def _specs(games: list[str], modes: list[str]) -> list[ToolSpec]:
    """The three specs for the configured games: built once, so every request sends the
    same bytes and the prompt cache holds."""
    names = ", ".join(f"{GAMES[game].name} = {game}" for game in games)
    rules = _answer_rules(games)
    return [
        ToolSpec(
            name=GAME_CHARACTERS,
            description=(
                "Master's own characters in one of their HoYoverse games, from their HoYoLAB "
                f"Battle Chronicle: {names}. Set `game` to the game Master asks about. Without "
                "`name`: every owned character as one row, sorted by rarity and level. With "
                "`name`, a character name as Master said it, at most 60 characters: up to "
                "three matching characters in detail; when more match, `more` names the "
                "others, so ask again for the one you need, and when nothing matches, `close` "
                "holds similar names of owned characters, since the name may have been "
                "misheard. "
                + _per_game(_CHARACTERS_FRAGMENTS, games)
                + _character_uses(games)
                + rules
            ),
            input_schema={
                "type": "object",
                "properties": {"game": _game_property(games), "name": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name=GAME_ENDGAME,
            description=(
                "Master's current endgame seasons in one of their HoYoverse games, from "
                f"HoYoLAB: {names}. Set `game`, or a `mode`, which tells its game, and leave "
                "`mode` out for every mode of the game. Each mode gives its season when it has "
                "a name, when it ends with `ends_in_hours`, and whether Master has played it "
                "this season as `has_data`. "
                + _per_game(_ENDGAME_FRAGMENTS, games)
                + "A mode that could not be read is under `errors` with its message. "
                + rules
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "game": _game_property(games),
                    "mode": {"type": "string", "enum": list(modes)},
                },
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name=GAME_EVENTS,
            description=(
                "The event calendar of one of Master's HoYoverse games, from their HoYoLAB: "
                f"{names}. Set `game` to the game Master asks about. It gives the current and "
                "upcoming banners with their featured names, the running events with Master's "
                "progress, and the endgame windows, each with when it starts or ends and the "
                "hours until then. "
                + _per_game(_EVENTS_FRAGMENTS, games)
                + "Use it for which banner is on, what comes next, or which event ends soon. "
                "Present banners only as information, never as a reason to pull. " + rules
            ),
            input_schema={
                "type": "object",
                "properties": {"game": _game_property(games)},
                "additionalProperties": False,
            },
        ),
    ]


def _per_game(fragments: Mapping[str, str], games: list[str]) -> str:
    return "".join(fragments[game] for game in games)


def _character_uses(games: list[str]) -> str:
    asked = [*(_DUPLICATES[game] for game in games), *(_GEAR[game] for game in games)]
    return (
        f"Use it for a character's {_listing([*asked, 'level', 'stats'], 'or')}, or for which "
        "characters of an element Master owns. "
    )


def _game_property(games: list[str]) -> dict[str, object]:
    return {"type": "string", "enum": list(games)}


def _answer_rules(games: list[str]) -> str:
    """How to answer from these tools, with the official terms of the configured games only.
    Only a Spiral Abyss floor says how many stars it holds."""
    missing = "Never claim how many stars are missing"
    if "genshin" in games:
        missing += ", except on a Spiral Abyss floor, which gives its `max_stars`"
    terms = [term for game in games for term in _TERMS[game]]
    new = ["character", *(_GEAR[game] for game in games)]
    return (
        "Times are local, and `as_of` is when HoYoLAB was read. Call it only when Master asks "
        "about these games, never in your own greeting or the morning briefing. Use it "
        "instead of guessing: never guess numbers, names, or dates. Answer only what Master "
        "asked, in one or two short sentences with at most about three names: no lists and no "
        "parentheses, because your voice drops whatever is in parentheses. Keep the official "
        f"English terms such as {_listing(terms, 'and')}. Comment only from the returned data, "
        f"and say so when a {_listing(new, 'or')} is new to you. Never suggest "
        f"pulling, spending money, or topping up. {missing}. Do not save this data with "
        "`remember` unless Master asks."
    )


def _listing(items: list[str], conjunction: str) -> str:
    """'a', 'a or b', or 'a, b, or c'."""
    if len(items) <= 2:
        return f" {conjunction} ".join(items)
    return f"{', '.join(items[:-1])}, {conjunction} {items[-1]}"


# --- characters ----------------------------------------------------------------------------


def _roster_json(view: _Roster, characters: list[Any], as_of: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "as_of": as_of,
        "count": len(characters),
        "columns": list(view.columns),
        "rows": [view.row(character) for character in characters[:ROSTER_MAX_ROWS]],
    }
    if len(characters) > ROSTER_MAX_ROWS:
        data["truncated"] = True
    return data


def _match_key(text: str) -> str:
    """A name reduced for matching: decomposed (NFKD), casefolded, letters and digits only."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", text).casefold() if char.isalnum()
    )


def _matching(view: _Roster, characters: list[Any], query: str) -> list[Any]:
    """The exact matches, else the characters with a name that contains the query."""
    key = _match_key(query)
    if not key:
        return []
    exact = [character for character in characters if key in view.exact_keys(character)]
    return exact or [
        character
        for character in characters
        if any(key in _match_key(name) for name in view.names(character))
    ]


def _close_names(characters: list[Any], query: str) -> list[str]:
    """Owned names that sound alike, for a name the STT misheard (each name once)."""
    names: dict[str, str] = {}
    for character in characters:
        names.setdefault(_match_key(character.name), character.name)
    close = difflib.get_close_matches(_match_key(query), list(names), n=CLOSE_NAMES_MAX)
    return [names[key] for key in close]


def _with_either_order(name: str, label: str) -> set[str]:
    """A name alone or with its label in either order ("Trailblazer Harmony")."""
    name, label = _match_key(name), _match_key(label)
    return {name, name + label, label + name}


def _hsr_row(character: HsrCharacter) -> list[object]:
    cone = character.light_cone
    cone_cells = [cone.name, cone.level, cone.superimposition] if cone else [None, None, None]
    return [
        character.name,
        character.rarity,
        character.level,
        character.eidolon,
        character.path,
        character.element,
        *cone_cells,
    ]


def _hsr_character_json(character: HsrCharacter, _detail: None = None) -> dict[str, Any]:
    cone = character.light_cone
    light_cone = (
        {
            "name": cone.name,
            "level": cone.level,
            "superimposition": cone.superimposition,
            "rarity": cone.rarity,
        }
        if cone is not None
        else None
    )
    return {
        "name": character.name,
        "rarity": character.rarity,
        "level": character.level,
        "eidolon": character.eidolon,
        "path": character.path,
        "element": character.element,
        **_present(light_cone=light_cone),
        "stats": dict(character.stats[:STATS_MAX]),
        "relics": [
            {
                "piece": relic.piece,
                "main": relic.main_stat,
                "value": relic.value,
                "level": relic.level,
            }
            for relic in character.relics[:RELICS_MAX]
        ],
        **_present(memosprite=character.memosprite),
    }


def _genshin_row(character: GenshinCharacter) -> list[object]:
    weapon = character.weapon
    return [
        character.name,
        character.rarity,
        character.level,
        character.constellation,
        character.element,
        character.weapon_type,
        weapon.name,
        weapon.level,
        weapon.refinement,
    ]


def _genshin_character_json(
    character: GenshinCharacter, detail: GenshinCharacterDetail | None
) -> dict[str, Any]:
    """A shown Genshin character from its detail; without one (HoYoLAB left it out), only
    what the roster row holds."""
    if detail is None:
        return _genshin_facts(character, _genshin_weapon_json(character.weapon))
    character = detail.character
    weapon = {**_genshin_weapon_json(character.weapon), "stats": dict(detail.weapon_stats)}
    return {
        **_genshin_facts(character, weapon),
        "stats": dict(detail.stats[:STATS_MAX]),
        "artifacts": [
            {
                "piece": artifact.piece,
                "set": artifact.set_name,
                "main": artifact.main_stat,
                "value": artifact.value,
                "level": artifact.level,
            }
            for artifact in detail.artifacts[:ARTIFACTS_MAX]
        ],
        "sets": dict(detail.sets),
    }


def _genshin_facts(character: GenshinCharacter, weapon: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": character.name,
        "rarity": character.rarity,
        "level": character.level,
        "constellation": character.constellation,
        "element": character.element,
        "weapon_type": character.weapon_type,
        "weapon": weapon,
    }


def _genshin_weapon_json(weapon: GenshinWeapon) -> dict[str, Any]:
    return {
        "name": weapon.name,
        "level": weapon.level,
        "refinement": weapon.refinement,
        "rarity": weapon.rarity,
    }


def _zzz_row(agent: ZzzAgent) -> list[object]:
    return [agent.name, agent.rarity, agent.level, agent.mindscape, agent.element, agent.specialty]


def _zzz_agent_json(agent: ZzzAgent, detail: ZzzAgentDetail | None) -> dict[str, Any]:
    """A shown ZZZ agent from her detail; without one (HoYoLAB left it out), only what the
    roster row holds."""
    if detail is None:
        return _zzz_facts(agent)
    engine = detail.w_engine
    w_engine = (
        {
            "name": engine.name,
            "level": engine.level,
            "refinement": engine.refinement,
            "rarity": engine.rarity,
        }
        if engine is not None
        else None
    )
    return {
        **_zzz_facts(detail.agent),
        **_present(w_engine=w_engine),
        "stats": dict(detail.stats[:STATS_MAX]),
        "discs": [
            {
                "slot": disc.slot,
                "set": disc.set_name,
                "main": disc.main_stat,
                "value": disc.value,
                "level": disc.level,
            }
            for disc in detail.discs[:DISCS_MAX]
        ],
        "sets": dict(detail.sets),
    }


def _zzz_facts(agent: ZzzAgent) -> dict[str, Any]:
    return {
        "name": agent.name,
        "rarity": agent.rarity,
        "level": agent.level,
        "mindscape": agent.mindscape,
        "element": agent.element,
        "specialty": agent.specialty,
    }


_ROSTERS: dict[str, _Roster] = {
    "hsr": _Roster(
        columns=HSR_ROSTER_COLUMNS,
        row=_hsr_row,
        order=lambda character: (-character.rarity, -character.level, character.name),
        exact_keys=lambda character: _with_either_order(character.name, character.path),
        names=lambda character: (character.name,),
        other=lambda character: {"name": character.name, "path": character.path},
        shown=_hsr_character_json,
        detailed=True,
    ),
    "genshin": _Roster(
        columns=GENSHIN_ROSTER_COLUMNS,
        row=_genshin_row,
        order=lambda character: (-character.rarity, -character.level, character.name),
        exact_keys=lambda character: _with_either_order(character.name, character.element),
        names=lambda character: (character.name,),
        other=lambda character: {"name": character.name, "element": character.element},
        shown=_genshin_character_json,
        detailed=False,
    ),
    "zzz": _Roster(
        columns=ZZZ_ROSTER_COLUMNS,
        row=_zzz_row,
        order=lambda agent: (-_ZZZ_RANKS.get(agent.rarity, 0), -agent.level, agent.name),
        exact_keys=lambda agent: {_match_key(agent.name), _match_key(agent.full_name)},
        names=lambda agent: (agent.name, agent.full_name),
        other=lambda agent: {"name": agent.name},
        shown=_zzz_agent_json,
        detailed=False,
    ),
}


# --- endgame -------------------------------------------------------------------------------


def _hsr_endgame_json(mode: str, endgame: HsrEndgame, now: datetime) -> dict[str, Any]:
    return {
        "mode": mode,
        "name": GAME_MODES["hsr"][mode],
        **_present(season=endgame.season),
        **_moment("ends", endgame.ends_at, now),
        "has_data": endgame.has_data,
        "stars": endgame.stars,
        "extra_stars": endgame.extra_stars,
        **_present(deepest_floor=endgame.deepest_floor, battles=endgame.battles),
        "floors": [_floor_json(floor) for floor in endgame.floors],
    }


def _floor_json(floor: HsrFloor) -> dict[str, Any]:
    return {
        "name": floor.name,
        "stars": floor.stars,
        **_present(cycles=floor.cycles, score=floor.score),
    }


def _endgame_json(mode: str, endgame: Endgame, now: datetime) -> dict[str, Any]:
    return {
        "mode": mode,
        "name": GAME_MODES[MODE_GAMES[mode]][mode],
        **_present(season=endgame.season),
        **_moment("ends", endgame.ends_at, now),
        "has_data": endgame.has_data,
        **_present(
            stars=endgame.stars,
            deepest=endgame.deepest,
            difficulty=endgame.difficulty,
            rating=endgame.rating,
            score=endgame.score,
            max_score=endgame.max_score,
            clear_seconds=endgame.seconds,
            battles=endgame.battles,
        ),
        "stages": [_stage_json(stage) for stage in endgame.stages[:STAGES_MAX]],
    }


def _stage_json(stage: EndgameStage) -> dict[str, Any]:
    return {
        "name": stage.name,
        **_present(
            stars=stage.stars,
            max_stars=stage.max_stars,
            rating=stage.rating,
            score=stage.score,
            clear_seconds=stage.seconds,
        ),
    }


# --- calendar ------------------------------------------------------------------------------


def _calendar_json(
    game: str, calendar: HsrCalendar | GameCalendar, as_of: str, now: datetime
) -> dict[str, Any]:
    """The calendar as of `now`: the banners not over, with the upcoming ones, and the events
    and endgame windows running. A kept calendar leaves out whatever ended since its read
    and lists the events and windows that started since. HSR names its banners `warps`."""
    hsr = isinstance(calendar, HsrCalendar)
    banners = [
        banner
        for banner in (calendar.warps if hsr else calendar.banners)
        if not is_over(banner.ends_at, now)
    ]
    events = sorted(
        (event for event in calendar.events if is_running(event.starts_at, event.ends_at, now)),
        key=_end_order,
    )
    data: dict[str, Any] = {
        "as_of": as_of,
        **_present(version=calendar.version),
        "warps" if hsr else "banners": [_banner_json(each, now) for each in banners[:BANNERS_MAX]],
        "events": [_event_json(event, now) for event in events[:EVENTS_MAX]],
    }
    if game in _WINDOW_GAMES:
        windows = [
            window
            for window in calendar.challenges
            if is_running(window.starts_at, window.ends_at, now)
        ]
        data["endgame"] = [_window_json(window, now) for window in windows[:WINDOWS_MAX]]
    return data


def _end_order(event: HsrEvent | GameEvent) -> tuple[bool, float]:
    """Soonest end first; events without an end last."""
    return event.ends_at is None, event.ends_at.timestamp() if event.ends_at else 0.0


def _banner_json(banner: HsrWarp | Banner, now: datetime) -> dict[str, Any]:
    """A banner with its end, and with its start only until it starts; its name only when
    HoYoLAB gives one (its calendars often do not)."""
    upcoming = banner.starts_at is not None and banner.starts_at > now
    return {
        "type": banner.kind,
        **({"name": banner.name} if banner.name else {}),
        "featured": list(banner.featured),
        "four_stars": list(banner.four_stars),
        **(_moment("starts", banner.starts_at, now) if upcoming else {}),
        **_moment("ends", banner.ends_at, now),
    }


def _event_json(event: HsrEvent | GameEvent, now: datetime) -> dict[str, Any]:
    return {
        "name": event.name[:EVENT_NAME_MAX_CHARS],
        **_moment("ends", event.ends_at, now),
        **_present(current=event.current, total=event.total),
        "done": event.done,
    }


def _window_json(window: HsrChallengeWindow | ChallengeWindow, now: datetime) -> dict[str, Any]:
    return {
        "mode": window.mode,
        "name": window.name,
        **_moment("ends", window.ends_at, now),
        **_present(current=window.current, total=window.total),
    }


# --- shared --------------------------------------------------------------------------------


def _present(**values: object) -> dict[str, object]:
    """The keys that have a value: keys without one are left out."""
    return {key: value for key, value in values.items() if value is not None}


def _moment(key: str, when: datetime | None, now: datetime) -> dict[str, object]:
    """`key` in local time and `<key>_in_hours`, rounded up and never below 0, counted from now."""
    if when is None:
        return {}
    hours = max(0, math.ceil((when - now).total_seconds() / 3600))
    return {key: _local(when, now), f"{key}_in_hours": hours}


def _local(when: datetime, now: datetime) -> str:
    return when.astimezone(now.tzinfo).strftime(LOCAL_TIME)


def _name_argument(name: object) -> str | None:
    if name is None:
        return None
    if not isinstance(name, str) or not name.strip():
        raise _InvalidArguments("'name' harus berupa teks nama karakter, atau dikosongkan.")
    name = name.strip()
    if len(name) > NAME_MAX_CHARS:
        raise _InvalidArguments(f"'name' paling banyak {NAME_MAX_CHARS} karakter.")
    return name


def _arguments_mapping(arguments: object, allowed: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in allowed]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
