"""What Cyrene knows about Master's Honkai: Star Rail account, as plain values.

The readers at the bottom turn genshin.py's models (taken as `Any`) into these frozen
dataclasses for the HoYoLAB side, and the brain's HSR tools turn them into compact
JSON. Nothing here imports genshin.py, and nothing holds the uid, the nickname, ids,
icons, or descriptions. Every datetime is aware, and a time on a minute's last second
(HoYoLAB's 11:59:59 warp end) is read as the next whole minute.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Literal

from waifu.gacha.game_data import GAME_MODES, is_over, on_the_minute

# The endgame modes in display order: the tool argument and its official English name.
ENDGAME_MODES: dict[str, str] = GAME_MODES["hsr"]

WarpKind = Literal["character", "light_cone"]


@dataclass(frozen=True)
class HsrLightCone:
    name: str
    level: int
    superimposition: int
    rarity: int


@dataclass(frozen=True)
class HsrRelic:
    piece: str
    main_stat: str
    value: str
    level: int


@dataclass(frozen=True)
class HsrCharacter:
    name: str
    rarity: int
    level: int
    eidolon: int
    path: str
    element: str
    light_cone: HsrLightCone | None
    stats: tuple[tuple[str, str], ...]  # (stat name, final value as the game shows it)
    relics: tuple[HsrRelic, ...]  # relics, then planar ornaments
    memosprite: str | None


@dataclass(frozen=True)
class HsrFloor:
    name: str
    stars: int
    cycles: int | None  # Memory of Chaos
    score: int | None  # Pure Fiction and Apocalyptic Shadow


@dataclass(frozen=True)
class HsrEndgame:
    mode: str  # a key of ENDGAME_MODES
    season: str | None
    ends_at: datetime | None
    has_data: bool  # False: not played this season
    stars: int
    extra_stars: int
    deepest_floor: str | None
    battles: int | None
    floors: tuple[HsrFloor, ...]  # in the API's order, at most 3


@dataclass(frozen=True)
class HsrWarp:
    kind: WarpKind
    name: str
    featured: tuple[str, ...]  # the 5-star characters or Light Cones
    four_stars: tuple[str, ...]
    starts_at: datetime | None
    ends_at: datetime | None


@dataclass(frozen=True)
class HsrEvent:
    name: str
    starts_at: datetime | None
    ends_at: datetime | None
    current: int | None
    total: int | None
    done: bool


@dataclass(frozen=True)
class HsrChallengeWindow:
    mode: str  # a key of ENDGAME_MODES
    name: str
    starts_at: datetime | None
    ends_at: datetime | None
    current: int | None
    total: int | None


@dataclass(frozen=True)
class HsrCalendar:
    version: str | None
    warps: tuple[HsrWarp, ...]
    events: tuple[HsrEvent, ...]
    challenges: tuple[HsrChallengeWindow, ...]


# HoYoLAB's relic positions: the four relics, then the two planar ornaments.
RELIC_PIECES = {1: "Head", 2: "Hands", 3: "Body", 4: "Feet", 5: "Planar Sphere", 6: "Link Rope"}
MAX_FLOORS = 3
# The calendar's challenge types (genshin.py's `ChallengeType` values) by endgame mode.
CHALLENGE_MODES = {
    "ChallengeTypeChasm": "memory_of_chaos",
    "ChallengeTypeStory": "pure_fiction",
    "ChallengeTypeBoss": "apocalyptic_shadow",
    "ChallengeTypePeak": "anomaly_arbitration",
}
# Any offset of the calendar API's clock (a time zone, genshin.py's relabelling) is whole
# quarter hours; latency and the PC clock's skew are seconds.
CLOCK_STEP = timedelta(minutes=15)


def read_character(model: Any) -> HsrCharacter:
    """One owned character from genshin.py's detailed `StarRailDetailCharacter`."""
    equip = model.equip
    light_cone = (
        None if equip is None else HsrLightCone(equip.name, equip.level, equip.rank, equip.rarity)
    )
    return HsrCharacter(
        name=model.name,
        rarity=model.rarity,
        level=model.level,
        eidolon=model.rank,
        path=model.path.name.replace("_", " ").title(),
        element=model.element,
        light_cone=light_cone,
        stats=tuple((stat.info.name, stat.final) for stat in model.properties),
        relics=tuple(_relic(relic) for relic in (*model.relics, *model.ornaments)),
        memosprite=None if model.memosprite is None else model.memosprite.name,
    )


def read_endgame(mode: str, model: Any) -> HsrEndgame:
    """This season of one endgame mode, from genshin.py's model of that mode.

    Season times are already aware (genshin.py adds the uid's server offset to them) and
    whole minutes (its `PartialTime` has no seconds), so none is on a minute's last second.
    """
    if mode == "anomaly_arbitration":
        return _anomaly_arbitration(model)
    season = model.seasons[0] if model.seasons else None
    return HsrEndgame(
        mode=mode,
        season=None if season is None else season.name,
        ends_at=None if season is None else season.end_time.datetime,
        has_data=model.has_data,
        stars=model.total_stars,
        extra_stars=model.starward_stars,
        deepest_floor=model.max_floor or None,
        battles=model.total_battles,
        floors=tuple(_floor(mode, floor) for floor in model.floors[:MAX_FLOORS]),
    )


def read_calendar(model: Any, fetched_at: datetime) -> HsrCalendar:
    """The event calendar, every time anchored on the calendar's own `now`.

    genshin.py 1.7.31 parses the calendar's times as UTC and relabels them +08:00
    without converting, so a time read on its own is hours off. `fetched_at` is the
    calendar's `now` on Cyrene's clock, read just before the request: their difference is
    the API's clock offset plus seconds of latency and clock skew, so every time is moved
    by that difference rounded to the quarter hour. That is right whichever clock the API
    uses, and to HoYoLAB's own minute; a time on that minute's last second (11:59:59 for a
    warp ending at 12:00) is the next whole minute. Whatever is over is dropped; upcoming
    warps, events, and endgame windows stay with their start, so a calendar kept across a
    season or version change still has what started since it was read.
    """
    shift = _clock_offset(fetched_at - model.now)
    zone = fetched_at.tzinfo

    def window(item: Any) -> tuple[datetime | None, datetime | None]:
        info = item.time_info
        if info is None:
            return None, None
        return (
            on_the_minute((info.start + shift).astimezone(zone)),
            on_the_minute((info.end + shift).astimezone(zone)),
        )

    pools: tuple[tuple[WarpKind, Any], ...] = (
        ("character", model.character_warps),
        ("light_cone", model.light_cone_warps),
    )
    warps = []
    for kind, warp_list in pools:
        for warp in warp_list:
            starts_at, ends_at = window(warp)
            if not is_over(ends_at, fetched_at):
                items = (*warp.characters, *warp.light_cones)
                featured, four_stars = _names(items, 5), _names(items, 4)
                warps.append(HsrWarp(kind, warp.name, featured, four_stars, starts_at, ends_at))
    events = []
    for event in model.events:
        starts_at, ends_at = window(event)
        if not is_over(ends_at, fetched_at):
            current, total = _progress(event)
            events.append(
                HsrEvent(event.name, starts_at, ends_at, current, total, event.all_finished)
            )
    challenges = []
    for challenge in model.challenges:
        mode = CHALLENGE_MODES.get(_type_value(challenge.type))
        starts_at, ends_at = window(challenge)
        if mode is not None and not is_over(ends_at, fetched_at):
            current, total = _progress(challenge)
            challenges.append(
                HsrChallengeWindow(mode, challenge.name, starts_at, ends_at, current, total)
            )
    version = model.cur_game_version or None
    return HsrCalendar(version, tuple(warps), tuple(events), tuple(challenges))


def _clock_offset(difference: timedelta) -> timedelta:
    """The calendar API's clock offset: `difference` rounded to the nearest quarter hour."""
    return CLOCK_STEP * round(difference / CLOCK_STEP)


def _relic(relic: Any) -> HsrRelic:
    main = relic.main_property
    return HsrRelic(RELIC_PIECES[relic.pos], main.info.name, main.value, relic.level)


def _floor(mode: str, floor: Any) -> HsrFloor:
    if mode == "memory_of_chaos":
        return HsrFloor(floor.name, floor.star_num, cycles=floor.round_num, score=None)
    return HsrFloor(floor.name, floor.star_num, cycles=None, score=floor.score)


def _anomaly_arbitration(model: Any) -> HsrEndgame:
    """This season's Anomaly Arbitration: the first record of `schedule_type=1`."""
    if not model.records:
        return HsrEndgame(
            mode="anomaly_arbitration",
            season=None,
            ends_at=None,
            has_data=False,
            stars=0,
            extra_stars=0,
            deepest_floor=None,
            battles=None,
            floors=(),
        )
    record = model.records[0]
    boss = record.boss_record
    return HsrEndgame(
        mode="anomaly_arbitration",
        season=record.season.name,
        ends_at=record.season.end_time.datetime,
        has_data=record.has_data,
        stars=record.boss_stars + record.mini_boss_stars,
        extra_stars=0,
        deepest_floor=record.boss.name if boss is not None and boss.has_data else None,
        battles=record.cycles_used,  # genshin.py's name for the record's battle_num
        floors=(),
    )


def _names(items: Iterable[Any], rarity: int) -> tuple[str, ...]:
    return tuple(item.name for item in items if item.rarity == rarity)


def _progress(item: Any) -> tuple[int | None, int | None]:
    """The owner's progress, or none for an item that counts nothing (a total of 0)."""
    if item.total_progress <= 0:
        return None, None
    return item.current_progress, item.total_progress


def _type_value(kind: Any) -> Any:
    """genshin.py's enum value, or the raw text it keeps for a type it does not know."""
    return kind.value if isinstance(kind, Enum) else kind
