"""What Cyrene knows about Master's Genshin Impact account, as plain values.

The readers at the bottom turn genshin.py's models (taken as `Any`) into these frozen
dataclasses for the HoYoLAB side, and the brain's game data tools turn them into compact
JSON. Nothing here imports genshin.py (the module is not called `genshin` for that
reason), and nothing holds the uid, the nickname, icons, or descriptions; a character's
`ref`, the game's id for it, is kept only to ask HoYoLAB for its detail and never reaches
a result. Every datetime is aware, and a time on a minute's last second (HoYoLAB's 03:59:59
season end) is read as the next whole minute.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from waifu.gacha.game_data import (
    Banner,
    ChallengeWindow,
    Endgame,
    EndgameStage,
    GameCalendar,
    GameEvent,
    is_over,
    is_running,
    on_the_minute,
    worn_sets,
)

T = TypeVar("T")


@dataclass(frozen=True)
class GenshinWeapon:
    name: str
    level: int
    refinement: int
    rarity: int


@dataclass(frozen=True)
class GenshinCharacter:
    ref: int  # the game's character id: only to ask for the detail, never in a result
    name: str
    rarity: int
    level: int
    constellation: int
    element: str
    weapon_type: str
    weapon: GenshinWeapon  # genshin.py requires a weapon on every character


@dataclass(frozen=True)
class GenshinArtifact:
    piece: str  # the slot's name, such as "Plume of Death"
    set_name: str
    main_stat: str
    value: str
    level: int


@dataclass(frozen=True)
class GenshinCharacterDetail:
    character: GenshinCharacter
    weapon_stats: tuple[tuple[str, str], ...]  # (stat name, final value): main, then sub
    stats: tuple[tuple[str, str], ...]  # (stat name, final value as the game shows it)
    artifacts: tuple[GenshinArtifact, ...]
    sets: tuple[tuple[str, int], ...]  # (set name, pieces worn): 2 or more pieces


# genshin.py's `GenshinWeaponType` names by value: character/list gives a plain int.
WEAPON_TYPES = {1: "Sword", 10: "Catalyst", 11: "Claymore", 12: "Bow", 13: "Polearm"}
STAGES_MAX = 4  # the floors or challenges kept per endgame mode
NO_FLOOR = "0-0"  # Spiral Abyss's deepest floor before the season's first battle
UNKNOWN_DIFFICULTY = "UNKNOWN"  # genshin.py's `TheaterDifficulty` when none is given


def read_character(model: Any) -> GenshinCharacter:
    """One owned character from character/list's `Character`, or the character of a
    `GenshinDetailCharacter` (whose weapon type is already genshin.py's enum)."""
    weapon = model.weapon
    return GenshinCharacter(
        ref=model.id,
        name=model.name,
        rarity=model.rarity,
        level=model.level,
        constellation=model.constellation,
        element=model.element,
        weapon_type=WEAPON_TYPES.get(model.weapon_type, str(int(model.weapon_type))),
        weapon=GenshinWeapon(weapon.name, weapon.level, weapon.refinement, weapon.rarity),
    )


def read_detail(model: Any) -> GenshinCharacterDetail:
    """One character's detail from genshin.py's `GenshinDetailCharacter`."""
    weapon = model.weapon
    artifacts = tuple(_artifact(artifact) for artifact in model.artifacts)
    return GenshinCharacterDetail(
        character=read_character(model),
        weapon_stats=tuple(
            (stat.info.name, stat.final)
            for stat in (weapon.main_stat, weapon.sub_stat)
            if stat is not None
        ),
        stats=tuple((stat.info.name, stat.final) for stat in model.selected_properties),
        artifacts=artifacts,
        sets=worn_sets(artifact.set_name for artifact in artifacts),
    )


def read_endgame(mode: str, model: Any, fetched_at: datetime) -> Endgame:
    """This season of one endgame mode, from genshin.py's model of that mode (for Stygian
    Onslaught, its list of `HardChallenge`). The Imaginarium Theater and Stygian Onslaught
    answers may hold several seasons: the one running at `fetched_at` is read, else the
    newest. The Spiral Abyss and Stygian Onslaught times are already aware."""
    if mode == "spiral_abyss":
        return _spiral_abyss(model)
    if mode == "imaginarium_theater":
        return _imaginarium_theater(model, fetched_at)
    if mode == "stygian_onslaught":
        return _stygian_onslaught(model, fetched_at)
    raise ValueError(f"Not a Genshin endgame mode: {mode}")


def read_calendar(model: Any, fetched_at: datetime) -> GameCalendar:
    """The event calendar, from genshin.py's `GenshinEventCalendar`.

    genshin.py gives this calendar no time zone, so its `start_time`/`end_time` are naive
    wall clocks and are never read: every time comes from the unix `start_timestamp` and
    `end_timestamp` (0: none given). Whatever is over at `fetched_at` is dropped; upcoming
    banners, events, and endgame windows stay with their start, so a calendar kept across a
    version change still has what started since it was read.
    """
    pools = (
        ("character", model.character_banners),
        ("weapon", model.weapon_banners),
        ("chronicled", model.chronicled_banners),
    )
    banners = []
    versions = []
    for kind, pool in pools:
        for banner in pool:
            starts_at, ends_at = _window(banner)
            if not is_over(ends_at, fetched_at):
                items = (*banner.characters, *banner.weapons)
                featured, four_stars = _names(items, 5), _names(items, 4)
                name = banner.name or None
                banners.append(Banner(kind, name, featured, four_stars, starts_at, ends_at))
                versions.append(banner.version)
    events = []
    for event in model.events:
        starts_at, ends_at = _window(event)
        if not is_over(ends_at, fetched_at):
            events.append(GameEvent(event.name, starts_at, ends_at, None, None, event.is_finished))
    challenges = []
    for challenge in model.challenges:
        window = _challenge_window(challenge)
        if window is not None and not is_over(window.ends_at, fetched_at):
            challenges.append(window)
    version = (versions[0] or None) if versions else None
    return GameCalendar(version, tuple(banners), tuple(events), tuple(challenges))


def _artifact(model: Any) -> GenshinArtifact:
    main = model.main_stat
    return GenshinArtifact(model.pos_name, model.set.name, main.info.name, main.value, model.level)


def _spiral_abyss(model: Any) -> Endgame:
    """genshin.py gives the season only as a number, so it has no name here."""
    floors = sorted(model.floors, key=lambda floor: floor.floor, reverse=True)[:STAGES_MAX]
    return _endgame(
        "spiral_abyss",
        ends_at=on_the_minute(model.end_time),
        has_data=model.total_battles > 0,
        stars=model.total_stars,
        deepest=None if model.max_floor == NO_FLOOR else model.max_floor,
        battles=model.total_battles,
        stages=tuple(
            EndgameStage(f"Floor {floor.floor}", floor.stars, floor.max_stars, None, None, None)
            for floor in floors
        ),
    )


def _imaginarium_theater(model: Any, fetched_at: datetime) -> Endgame:
    """Read with need_detail=False: the season's summary, without its acts."""
    season = _running(
        model.datas,
        fetched_at,
        lambda data: (_from_unix(data.schedule.start_time), _from_unix(data.schedule.end_time)),
        lambda data: data.schedule.id,
    )
    if season is None:
        return _endgame("imaginarium_theater", has_data=False)
    stats = season.stats
    difficulty = stats.difficulty.name
    return _endgame(
        "imaginarium_theater",
        ends_at=_from_unix(season.schedule.end_time),
        has_data=season.has_data,
        stars=stats.medal_num,
        deepest=f"Act {stats.best_record}" if stats.best_record > 0 else None,
        difficulty=None
        if difficulty == UNKNOWN_DIFFICULTY
        else difficulty.replace("_", " ").title(),
    )


def _stygian_onslaught(model: Any, fetched_at: datetime) -> Endgame:
    """The single-player record of a season. genshin.py names no difficulty, so it is the
    number as text."""
    challenge = _running(model, fetched_at, _season_window, lambda item: item.season.id)
    if challenge is None:
        return _endgame("stygian_onslaught", has_data=False)
    single = challenge.single_player
    best = single.best_record
    return _endgame(
        "stygian_onslaught",
        season=challenge.season.name,
        ends_at=_season_window(challenge)[1],
        has_data=single.has_data,
        difficulty=None if best is None else str(best.difficulty),
        seconds=None if best is None else best.time_used,
        stages=tuple(
            EndgameStage(item.name, None, None, None, None, item.time_used)
            for item in single.challenges[:STAGES_MAX]
        ),
    )


def _endgame(
    mode: str,
    *,
    has_data: bool,
    season: str | None = None,
    ends_at: datetime | None = None,
    stars: int | None = None,
    deepest: str | None = None,
    difficulty: str | None = None,
    seconds: int | None = None,
    battles: int | None = None,
    stages: tuple[EndgameStage, ...] = (),
) -> Endgame:
    """A Genshin mode's season: what no Genshin mode gives (rating, score) is None."""
    return Endgame(
        mode=mode,
        season=season,
        ends_at=ends_at,
        has_data=has_data,
        stars=stars,
        deepest=deepest,
        difficulty=difficulty,
        rating=None,
        score=None,
        max_score=None,
        seconds=seconds,
        battles=battles,
        stages=stages,
    )


def _running(
    items: Sequence[T],
    fetched_at: datetime,
    window: Callable[[T], tuple[datetime | None, datetime | None]],
    newest: Callable[[T], int],
) -> T | None:
    """The item whose window holds `fetched_at`, else the newest one (None: no items)."""
    for item in items:
        if is_running(*window(item), fetched_at):
            return item
    return max(items, key=newest, default=None)


def _season_window(challenge: Any) -> tuple[datetime | None, datetime | None]:
    """A Stygian Onslaught season's start and end, already aware."""
    season = challenge.season
    return on_the_minute(season.start_at), on_the_minute(season.end_at)


def _challenge_window(challenge: Any) -> ChallengeWindow | None:
    """A calendar challenge's Spiral Abyss or Imaginarium Theater window (None: another)."""
    starts_at, ends_at = _window(challenge)
    abyss, theater = challenge.abyss_detail, challenge.theater_detail
    if abyss is not None:
        return ChallengeWindow(
            "spiral_abyss", challenge.name, starts_at, ends_at, abyss.total_star, abyss.max_star
        )
    if theater is not None:
        return ChallengeWindow(
            "imaginarium_theater", challenge.name, starts_at, ends_at, theater.max_round, None
        )
    return None


def _window(item: Any) -> tuple[datetime | None, datetime | None]:
    return _from_unix(item.start_timestamp), _from_unix(item.end_timestamp)


def _from_unix(seconds: int) -> datetime | None:
    """A unix time as an aware UTC datetime, on the minute; 0 is none given."""
    return on_the_minute(datetime.fromtimestamp(seconds, UTC)) if seconds > 0 else None


def _names(items: Iterable[Any], rarity: int) -> tuple[str, ...]:
    return tuple(item.name for item in items if item.rarity == rarity)
