"""What Cyrene knows about Master's Zenless Zone Zero account, as plain values.

The readers at the bottom turn genshin.py's models (taken as `Any`) into these frozen
dataclasses for the HoYoLAB side, and the brain's game data tools turn them into compact
JSON. Nothing here imports genshin.py, and nothing holds the uid, the nickname, the avatar,
icons, or descriptions; an agent's `ref`, the game's id for it, is kept only to ask HoYoLAB
for its detail and never reaches a result. Every datetime is aware, and a time on a minute's
last second (HoYoLAB's 03:59:59 season end) is read as the next whole minute.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from waifu.gacha.game_data import (
    Banner,
    Endgame,
    EndgameStage,
    GameCalendar,
    GameEvent,
    is_over,
    on_the_minute,
    worn_sets,
)


@dataclass(frozen=True)
class ZzzWEngine:
    name: str
    level: int
    refinement: int  # genshin.py's name for the W-Engine's duplicate level, 1-5
    rarity: str  # "S", "A", or "B"


@dataclass(frozen=True)
class ZzzAgent:
    ref: int  # the game's agent id: only to ask for the detail, never in a result
    name: str
    full_name: str
    rarity: str  # "S" or "A"
    level: int
    mindscape: int  # Mindscape Cinema
    element: str
    specialty: str


@dataclass(frozen=True)
class ZzzDisc:
    slot: int  # 1-6
    set_name: str
    main_stat: str
    value: str
    level: int


@dataclass(frozen=True)
class ZzzAgentDetail:
    agent: ZzzAgent
    w_engine: ZzzWEngine | None
    stats: tuple[tuple[str, str], ...]  # (stat name, final value as the game shows it)
    discs: tuple[ZzzDisc, ...]
    sets: tuple[tuple[str, int], ...]  # (set name, pieces worn): 2 or more pieces


MAX_STAGES = 4
# The gacha calendar's banner types (genshin.py's `ZZZGachaEventType` names) that Cyrene names
# otherwise; any other type is its name, or the raw text genshin.py keeps, lowercased.
BANNER_KINDS = {"CHARACTER": "agent", "WEAPON": "w_engine"}
NO_TIME = datetime.fromtimestamp(0, UTC)  # a calendar time of 0: HoYoLAB's "no time"


def read_agent(model: Any) -> ZzzAgent:
    """One owned agent from genshin.py's `ZZZPartialAgent`, or from the `ZZZFullAgent` of her
    detail, which has the same fields."""
    return ZzzAgent(
        ref=model.id,
        name=model.name,
        full_name=model.full_name,
        rarity=model.rarity,
        level=model.level,
        mindscape=model.rank,  # genshin.py's name for Mindscape Cinema
        element=model.element.name.title(),
        specialty=model.specialty.name.title(),
    )


def read_detail(model: Any) -> ZzzAgentDetail:
    """One agent's detail from genshin.py's `ZZZFullAgent`."""
    discs = tuple(_disc(disc) for disc in model.discs)
    return ZzzAgentDetail(
        agent=read_agent(model),
        w_engine=None if model.w_engine is None else _w_engine(model.w_engine),
        stats=tuple((stat.name, stat.final) for stat in model.properties),
        discs=discs,
        sets=worn_sets(disc.set_name for disc in discs),
    )


def read_endgame(mode: str, model: Any) -> Endgame:
    """This season of one endgame mode, from genshin.py's model of that mode.

    Season times are aware when genshin.py knows the uid's server offset, which it does
    because the uid is always passed; a naive one is unknown.
    """
    if mode == "deadly_assault":
        return _deadly_assault(model)
    if hasattr(model, "brief_info"):
        return _shiyu_defense_v2(model)
    return _shiyu_defense_v1(model)


def read_calendar(events: Iterable[Any], gacha: Any, fetched_at: datetime) -> GameCalendar:
    """The calendar from `get_zzz_event_calendar`'s events and `get_zzz_gacha_calendar`'s
    banners, whose times genshin.py already makes aware UTC.

    Whatever is over at `fetched_at` is dropped; upcoming banners and events stay with their
    start. These calendars carry no endgame windows.
    """
    pools = (
        *((banner, banner.characters) for banner in gacha.characters),
        *((banner, ()) for banner in gacha.weapons),  # genshin.py names no W-Engine here
    )
    version = None
    banners: list[Banner] = []
    for banner, agents in pools:
        starts_at, ends_at = _calendar_time(banner.start), _calendar_time(banner.end)
        if is_over(ends_at, fetched_at):
            continue
        if not banners:
            version = banner.version or None
        featured, a_ranks = _names(agents, "S"), _names(agents, "A")
        kind = _banner_kind(banner.type)
        banners.append(Banner(kind, None, featured, a_ranks, starts_at, ends_at))
    kept = tuple(
        _event(event) for event in events if not is_over(_calendar_time(event.end), fetched_at)
    )
    return GameCalendar(version, tuple(banners), kept, ())


def _w_engine(engine: Any) -> ZzzWEngine:
    return ZzzWEngine(engine.name, engine.level, engine.refinement, engine.rarity)


def _disc(disc: Any) -> ZzzDisc:
    main = disc.main_properties[0]
    return ZzzDisc(disc.position, disc.set_effect.name, main.name, main.value, disc.level)


def _shiyu_defense_v2(model: Any) -> Endgame:
    """Shiyu Defense as genshin.py's `ShiyuDefenseV2`: the run's rating, score, and clear time,
    and the scored battles of its fifth frontier, which genshin.py gives no name. Each is
    "Frontier 5 battle N": a bare "Frontier N" would name another frontier, as the Critical
    Node's stages do."""
    brief = model.brief_info
    battles = () if model.fifth_frontier is None else model.fifth_frontier.layers
    return Endgame(
        mode="shiyu_defense",
        season=None,  # genshin.py gives the season only as a zone id
        ends_at=_aware(model.end_time),
        has_data=brief is not None,
        stars=None,
        deepest=None,
        difficulty=None,
        rating=None if brief is None else brief.rating,
        score=None if brief is None else brief.score,
        max_score=None if brief is None else brief.max_score,
        seconds=None if brief is None else brief.total_clear_time,
        battles=None,
        stages=tuple(
            EndgameStage(
                f"Frontier 5 battle {position}",
                None,
                None,
                battle.rating,
                battle.score,
                battle.clear_time,
            )
            for position, battle in enumerate(battles[:MAX_STAGES], start=1)
        ),
    )


def _shiyu_defense_v1(model: Any) -> Endgame:
    """Shiyu Defense as genshin.py's `ShiyuDefenseV1`, the Critical Node: its frontiers, each
    rated by the time left, deepest first."""
    deepest_first = sorted(model.floors, key=lambda floor: floor.index, reverse=True)
    return Endgame(
        mode="shiyu_defense",
        season=None,
        ends_at=_aware(model.end_time),
        has_data=model.has_data,
        stars=None,
        deepest=f"Frontier {model.max_floor}" if model.max_floor > 0 else None,
        difficulty=None,
        rating=None,
        score=None,
        max_score=None,
        seconds=_positive(model.fastest_clear_time),
        battles=None,
        stages=tuple(
            EndgameStage(f"Frontier {floor.index}", None, None, floor.rating, None, None)
            for floor in deepest_first[:MAX_STAGES]
        ),
    )


def _deadly_assault(model: Any) -> Endgame:
    """Deadly Assault: its stars, its score, and each boss. A boss's `total_star` is
    unverified, so no stage claims a maximum; Adversity Mode is not read."""
    return Endgame(
        mode="deadly_assault",
        season=None,
        ends_at=_aware(model.end_time),
        has_data=model.has_data,
        stars=model.total_star,
        deepest=None,
        difficulty=None,
        rating=None,
        score=model.total_score,
        max_score=_positive(model.total_max_score),  # 0 is genshin.py's default
        seconds=None,
        battles=None,
        stages=tuple(
            EndgameStage(challenge.boss.name, challenge.star, None, None, challenge.score, None)
            for challenge in model.challenges
        ),
    )


def _event(event: Any) -> GameEvent:
    """One event; its progress is the reward currency it gives, when it counts any."""
    counted = event.max_monochromes > 0
    return GameEvent(
        name=event.name,
        starts_at=_calendar_time(event.start),
        ends_at=_calendar_time(event.end),
        current=event.obtained_monochromes if counted else None,
        total=event.max_monochromes if counted else None,
        done=_member_name(event.status) == "COMPLETED",
    )


def _banner_kind(kind: Any) -> str:
    member = _member_name(kind)
    if member is None:
        return str(kind).lower()
    return BANNER_KINDS.get(member, member.lower())


def _member_name(value: Any) -> str | None:
    """The name of genshin.py's enum member, or None for the raw text it keeps for a value it
    does not know."""
    return value.name if isinstance(value, Enum) else None


def _names(agents: Iterable[Any], rarity: str) -> tuple[str, ...]:
    return tuple(agent.name for agent in agents if agent.rarity == rarity)


def _aware(moment: datetime | None) -> datetime | None:
    return on_the_minute(moment) if moment is not None and moment.utcoffset() is not None else None


def _calendar_time(moment: datetime) -> datetime | None:
    return None if moment == NO_TIME else on_the_minute(moment)


def _positive(value: int) -> int | None:
    return value if value > 0 else None
