"""The three tools that tell Master about their own Honkai: Star Rail account.

`hsr_characters`, `hsr_endgame`, and `hsr_events` read HoYoLAB only when asked. The
specs are static English with no live data, so they stay byte-identical for the brain's
lifetime and keep the prompt cache warm. Results are compact JSON with local times, and
`run` never raises: invalid arguments, unknown names, and HoYoLAB failures become error
results. What HoYoLAB answered is kept in memory for a few minutes, never on disk.
"""

from __future__ import annotations

import difflib
import json
import math
import threading
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Generic, Protocol, TypeVar

from waifu.brain.base import ToolSpec
from waifu.gacha.hoyolab import GachaError
from waifu.gacha.starrail import (
    ENDGAME_MODES,
    HsrCalendar,
    HsrChallengeWindow,
    HsrCharacter,
    HsrEndgame,
    HsrEvent,
    HsrFloor,
    HsrWarp,
    is_over,
    is_running,
)
from waifu.gacha.tools import LOCAL_TIME

T = TypeVar("T")

HSR_CHARACTERS = "hsr_characters"
HSR_ENDGAME = "hsr_endgame"
HSR_EVENTS = "hsr_events"
NAME_MAX_CHARS = 60
ROSTER_COLUMNS = (
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
ROSTER_MAX_ROWS = 120
MATCHES_MAX = 3
MORE_MAX = 6  # every other Path of one name: genshin.py knows 9 Paths
CLOSE_NAMES_MAX = 3
STATS_MAX = 14
RELICS_MAX = 6
WARPS_MAX = 8
EVENTS_MAX = 15
EVENT_NAME_MAX_CHARS = 60
WINDOWS_MAX = 4
CHARACTERS_TTL = timedelta(minutes=15)
ENDGAME_TTL = timedelta(minutes=10)
CALENDAR_TTL = timedelta(minutes=60)
_CHARACTERS_KEYS = frozenset({"name"})
_ENDGAME_KEYS = frozenset({"mode"})
_EVENTS_KEYS: frozenset[str] = frozenset()

_ANSWER_RULES = (
    "Times are local, and `as_of` is when HoYoLAB was read. Call it only when Master asks "
    "about Honkai: Star Rail, never in your own greeting or the morning briefing. Use it "
    "instead of guessing: never guess numbers, names, or dates. Answer only what Master "
    "asked, in one or two short sentences with at most about three names: no lists and no "
    "parentheses, because your voice drops whatever is in parentheses. Keep the official "
    "English terms such as Eidolon, Light Cone, Superimposition, and Memory of Chaos. "
    "Comment only from the returned data, and say so when a character or Light Cone is new "
    "to you. Never suggest pulling, spending money, or topping up. Do not save this data "
    "with `remember` unless Master asks."
)


class HsrSource(Protocol):
    """Where the tools read from: `HoyolabClient`'s HSR methods."""

    def hsr_characters(self) -> tuple[HsrCharacter, ...]: ...

    def hsr_endgame(self, modes: Iterable[str]) -> dict[str, HsrEndgame | GachaError]: ...

    def hsr_calendar(self) -> HsrCalendar: ...


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


@dataclass(frozen=True)
class _Fetched(Generic[T]):
    """What HoYoLAB answered, and the local time it was read."""

    at: datetime
    value: T


def _local_now() -> datetime:
    return datetime.now().astimezone()


class HsrTools:
    """A `ToolProvider` for the brain: Master's Honkai: Star Rail account from HoYoLAB.

    Successful reads are kept per process (`characters`, `endgame:<mode>`, `calendar`) for
    their TTL. The lock guards only the cache's dict: voice and phone turns run on
    different threads, and neither waits for the other's HoYoLAB request.
    """

    def __init__(self, source: HsrSource, *, now: Callable[[], datetime] = _local_now) -> None:
        self._source = source
        self._now = now
        self._cache: dict[str, _Fetched[Any]] = {}
        self._cache_lock = threading.Lock()

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=HSR_CHARACTERS,
                description=(
                    "Master's own Honkai: Star Rail characters, from their HoYoLAB Battle "
                    "Chronicle. Without `name`: every owned character as one row, with rarity, "
                    "level, Eidolon, Path, element, and the equipped Light Cone with its level "
                    "and Superimposition, sorted by rarity and level. With `name`, a character "
                    "name as Master said it, at most 60 characters: up to three matching "
                    "characters with their final stats, relic main stats, and memosprite. "
                    "Characters that share a name, such as both March 7th Paths or the "
                    "Trailblazer's Paths, come back together, told apart by `path`, and a name "
                    "with its Path, such as Trailblazer Harmony, gives that one character. When "
                    "more than three match, `more` names the others with their Path, so ask "
                    "again for the one you need. When nothing matches, `close` holds similar "
                    "names of owned characters, since the name may have been misheard. Use it "
                    "for a character's Eidolon, Light Cone, level, or stats, or for which "
                    "characters of a Path or element Master owns. " + _ANSWER_RULES
                ),
                input_schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name=HSR_ENDGAME,
                description=(
                    "Master's current Honkai: Star Rail endgame season, from HoYoLAB: Memory of "
                    "Chaos, Pure Fiction, Apocalyptic Shadow, and Anomaly Arbitration. Leave "
                    "`mode` out for every mode. Each mode gives the season, when it ends with "
                    "`ends_in_hours`, whether Master has played it this season as `has_data`, "
                    "the stars and extra stars earned, the deepest floor, the battles fought, "
                    "and the best floors with their cycles for Memory of Chaos or their score "
                    "for Pure Fiction and Apocalyptic Shadow. A mode that could not be read is "
                    "under `errors` with its message. The result has no maximum stars, so it "
                    "cannot tell what is left. Never claim how many stars are missing. "
                    + _ANSWER_RULES
                ),
                input_schema={
                    "type": "object",
                    "properties": {"mode": {"type": "string", "enum": list(ENDGAME_MODES)}},
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name=HSR_EVENTS,
                description=(
                    "The Honkai: Star Rail event calendar, from Master's HoYoLAB: the current "
                    "and upcoming warps with their featured 5-star and 4-star characters or "
                    "Light Cones, the running events with Master's progress, and the endgame "
                    "windows, each with when it starts or ends and the hours until then. Use "
                    "it for which banner is on, what comes next, or which event ends soon. "
                    "Present banners only as information, never as a reason to pull. Never "
                    "claim how many stars are missing. " + _ANSWER_RULES
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
        ]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == HSR_CHARACTERS:
                return self._characters(_name_argument(arguments))
            if name == HSR_ENDGAME:
                return self._endgame(_mode_argument(arguments))
            if name == HSR_EVENTS:
                _arguments_mapping(arguments, allowed=_EVENTS_KEYS)
                return self._events()
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except GachaError as error:
            return _error(error.message)
        return _error(f"Tool '{name}' bukan tool HSR.")

    def _characters(self, name: str | None) -> tuple[str, bool]:
        now = self._now()
        roster = self._read("characters", CHARACTERS_TTL, now, self._source.hsr_characters)
        characters = sorted(roster.value, key=_roster_order)
        as_of = _local(roster.at, now)
        if name is None:
            return _to_json(_roster_json(characters, as_of)), False
        return _to_json(_detail_json(characters, name, as_of)), False

    def _endgame(self, mode: str | None) -> tuple[str, bool]:
        now = self._now()
        modes = [mode] if mode else list(ENDGAME_MODES)
        fetched, errors = self._read_endgame(modes, now)
        if not fetched:
            return _error(" ".join(dict.fromkeys(error.message for error in errors.values())))
        data: dict[str, Any] = {
            # The oldest read among the modes shown: some may come from the cache.
            "as_of": _local(min(each.at for each in fetched.values()), now),
            "modes": [
                _endgame_json(each, fetched[each].value, now) for each in modes if each in fetched
            ],
        }
        if errors:
            data["errors"] = [
                {"mode": each, "message": errors[each].message} for each in modes if each in errors
            ]
        return _to_json(data), False

    def _events(self) -> tuple[str, bool]:
        now = self._now()
        calendar = self._read("calendar", CALENDAR_TTL, now, self._source.hsr_calendar)
        return _to_json(_calendar_json(calendar.value, _local(calendar.at, now), now)), False

    def _read_endgame(
        self, modes: list[str], now: datetime
    ) -> tuple[dict[str, _Fetched[HsrEndgame]], dict[str, GachaError]]:
        """Each mode from the cache when fresh; the stale ones from HoYoLAB in one call."""
        fetched: dict[str, _Fetched[HsrEndgame]] = {}
        for mode in modes:
            kept = self._kept(f"endgame:{mode}", ENDGAME_TTL, now)
            if kept is not None:
                fetched[mode] = kept
        stale = [mode for mode in modes if mode not in fetched]
        errors: dict[str, GachaError] = {}
        if stale:
            for mode, result in self._source.hsr_endgame(stale).items():
                if isinstance(result, GachaError):
                    errors[mode] = result
                else:
                    fetched[mode] = self._keep(f"endgame:{mode}", _Fetched(now, result))
        return fetched, errors

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


def _roster_order(character: HsrCharacter) -> tuple[int, int, str]:
    return -character.rarity, -character.level, character.name


def _roster_json(characters: list[HsrCharacter], as_of: str) -> dict[str, Any]:
    data: dict[str, Any] = {
        "as_of": as_of,
        "count": len(characters),
        "columns": list(ROSTER_COLUMNS),
        "rows": [_roster_row(character) for character in characters[:ROSTER_MAX_ROWS]],
    }
    if len(characters) > ROSTER_MAX_ROWS:
        data["truncated"] = True
    return data


def _roster_row(character: HsrCharacter) -> list[object]:
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


def _detail_json(characters: list[HsrCharacter], query: str, as_of: str) -> dict[str, Any]:
    matches = _matching(characters, query)
    shown, others = matches[:MATCHES_MAX], matches[MATCHES_MAX:]
    data: dict[str, Any] = {
        "as_of": as_of,
        "query": query,
        "characters": [_character_json(character) for character in shown],
    }
    if others:
        # Named so they can be asked for by name and Path: the Trailblazer is one character
        # per Path, all named "Trailblazer", and a fourth Path must not vanish.
        data["more"] = [
            {"name": character.name, "path": character.path} for character in others[:MORE_MAX]
        ]
    if not matches:
        data["close"] = _close_names(characters, query)
    return data


def _match_key(text: str) -> str:
    """A name reduced for matching: decomposed (NFKD), casefolded, letters and digits only."""
    return "".join(
        char for char in unicodedata.normalize("NFKD", text).casefold() if char.isalnum()
    )


def _matching(characters: list[HsrCharacter], query: str) -> list[HsrCharacter]:
    """The exact matches, else the characters whose name contains the query."""
    key = _match_key(query)
    if not key:
        return []
    exact = [character for character in characters if key in _exact_keys(character)]
    return exact or [character for character in characters if key in _match_key(character.name)]


def _exact_keys(character: HsrCharacter) -> set[str]:
    """The name, alone or with the Path in either order ("Trailblazer Harmony")."""
    name, path = _match_key(character.name), _match_key(character.path)
    return {name, name + path, path + name}


def _close_names(characters: list[HsrCharacter], query: str) -> list[str]:
    """Owned names that sound alike, for a name the STT misheard (each name once)."""
    names: dict[str, str] = {}
    for character in characters:
        names.setdefault(_match_key(character.name), character.name)
    close = difflib.get_close_matches(_match_key(query), list(names), n=CLOSE_NAMES_MAX)
    return [names[key] for key in close]


def _character_json(character: HsrCharacter) -> dict[str, Any]:
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


def _endgame_json(mode: str, endgame: HsrEndgame, now: datetime) -> dict[str, Any]:
    return {
        "mode": mode,
        "name": ENDGAME_MODES[mode],
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


def _calendar_json(calendar: HsrCalendar, as_of: str, now: datetime) -> dict[str, Any]:
    """The calendar as of `now`: the warps not over, with the upcoming ones, and the events
    and endgame windows running. A kept calendar leaves out whatever ended since its read
    and lists the events and windows that started since."""
    warps = [warp for warp in calendar.warps if not is_over(warp.ends_at, now)]
    events = sorted(
        (event for event in calendar.events if is_running(event.starts_at, event.ends_at, now)),
        key=_end_order,
    )
    windows = [
        window
        for window in calendar.challenges
        if is_running(window.starts_at, window.ends_at, now)
    ]
    return {
        "as_of": as_of,
        **_present(version=calendar.version),
        "warps": [_warp_json(warp, now) for warp in warps[:WARPS_MAX]],
        "events": [_event_json(event, now) for event in events[:EVENTS_MAX]],
        "endgame": [_window_json(window, now) for window in windows[:WINDOWS_MAX]],
    }


def _end_order(event: HsrEvent) -> tuple[bool, float]:
    """Soonest end first; events without an end last."""
    return event.ends_at is None, event.ends_at.timestamp() if event.ends_at else 0.0


def _warp_json(warp: HsrWarp, now: datetime) -> dict[str, Any]:
    """A warp with its end, and with its start only until it starts; its name only when
    HoYoLAB gives one (its calendar often does not)."""
    upcoming = warp.starts_at is not None and warp.starts_at > now
    return {
        "type": warp.kind,
        **({"name": warp.name} if warp.name else {}),
        "featured": list(warp.featured),
        "four_stars": list(warp.four_stars),
        **(_moment("starts", warp.starts_at, now) if upcoming else {}),
        **_moment("ends", warp.ends_at, now),
    }


def _event_json(event: HsrEvent, now: datetime) -> dict[str, Any]:
    return {
        "name": event.name[:EVENT_NAME_MAX_CHARS],
        **_moment("ends", event.ends_at, now),
        **_present(current=event.current, total=event.total),
        "done": event.done,
    }


def _window_json(window: HsrChallengeWindow, now: datetime) -> dict[str, Any]:
    return {
        "mode": window.mode,
        "name": window.name,
        **_moment("ends", window.ends_at, now),
        **_present(current=window.current, total=window.total),
    }


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


def _name_argument(arguments: object) -> str | None:
    name = _arguments_mapping(arguments, allowed=_CHARACTERS_KEYS).get("name")
    if name is None:
        return None
    if not isinstance(name, str) or not name.strip():
        raise _InvalidArguments("'name' harus berupa teks nama karakter, atau dikosongkan.")
    name = name.strip()
    if len(name) > NAME_MAX_CHARS:
        raise _InvalidArguments(f"'name' paling banyak {NAME_MAX_CHARS} karakter.")
    return name


def _mode_argument(arguments: object) -> str | None:
    mode = _arguments_mapping(arguments, allowed=_ENDGAME_KEYS).get("mode")
    if mode is None:
        return None
    if not isinstance(mode, str) or mode not in ENDGAME_MODES:
        raise _InvalidArguments(f"'mode' harus salah satu dari: {', '.join(ENDGAME_MODES)}.")
    return mode


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
