"""What the game data of Master's HoYoverse games share, whichever game it is from.

The endgame modes of each game, the Genshin and ZZZ endgame and calendar values (HSR
keeps its own in `starrail.py`), and `GameDataSource`, the HoYoLAB side the brain's game
data tools read from. Nothing here imports genshin.py, and nothing holds the uid, a
nickname, ids, icons, or descriptions. Every datetime is aware, and the Genshin and ZZZ readers
read every time through `on_the_minute`, as the HSR reader does its calendar times (HSR's
endgame season ends are whole minutes already).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from waifu.gacha.genshin_impact import GenshinCharacter, GenshinCharacterDetail
    from waifu.gacha.hoyolab import GachaError
    from waifu.gacha.starrail import HsrCalendar, HsrCharacter, HsrEndgame
    from waifu.gacha.zenless import ZzzAgent, ZzzAgentDetail

# Each game's endgame modes in display order: the tool argument and its official English name.
GAME_MODES: dict[str, dict[str, str]] = {
    "hsr": {
        "memory_of_chaos": "Memory of Chaos",
        "pure_fiction": "Pure Fiction",
        "apocalyptic_shadow": "Apocalyptic Shadow",
        "anomaly_arbitration": "Anomaly Arbitration",
    },
    "genshin": {
        "spiral_abyss": "Spiral Abyss",
        "imaginarium_theater": "Imaginarium Theater",
        "stygian_onslaught": "Stygian Onslaught",
    },
    "zzz": {
        "shiyu_defense": "Shiyu Defense",
        "deadly_assault": "Deadly Assault",
    },
}
# The game of each mode: no two games share a mode key.
MODE_GAMES: dict[str, str] = {mode: game for game, modes in GAME_MODES.items() for mode in modes}
WORN_SET_PIECES = 2  # the fewest pieces of one set that give a set bonus
LAST_SECOND = 59  # HoYoLAB ends a season, banner, or event on a minute's last second


@dataclass(frozen=True)
class EndgameStage:
    name: str
    stars: int | None
    max_stars: int | None
    rating: str | None
    score: int | None
    seconds: int | None  # the clear time


@dataclass(frozen=True)
class Endgame:
    """This season of one Genshin or ZZZ endgame mode; what a mode does not give is None."""

    mode: str  # a key of GAME_MODES["genshin"] or GAME_MODES["zzz"]
    season: str | None
    ends_at: datetime | None
    has_data: bool  # False: not played this season
    stars: int | None
    deepest: str | None
    difficulty: str | None
    rating: str | None
    score: int | None
    max_score: int | None
    seconds: int | None  # the clear time
    battles: int | None
    stages: tuple[EndgameStage, ...]


@dataclass(frozen=True)
class Banner:
    """A banner of a Genshin or ZZZ event calendar (`games.Banner` is the history's)."""

    kind: str
    name: str | None
    featured: tuple[str, ...]  # the 5-star or S-rank names
    four_stars: tuple[str, ...]  # the 4-star or A-rank names
    starts_at: datetime | None
    ends_at: datetime | None


@dataclass(frozen=True)
class GameEvent:
    name: str
    starts_at: datetime | None
    ends_at: datetime | None
    current: int | None
    total: int | None
    done: bool


@dataclass(frozen=True)
class ChallengeWindow:
    mode: str  # a key of GAME_MODES["genshin"]
    name: str | None  # the window's own name; None when HoYoLAB names it after its mode
    starts_at: datetime | None
    ends_at: datetime | None
    current: int | None
    total: int | None


@dataclass(frozen=True)
class GameCalendar:
    version: str | None
    banners: tuple[Banner, ...]
    events: tuple[GameEvent, ...]
    challenges: tuple[ChallengeWindow, ...]


class GameDataSource(Protocol):
    """Where the game data tools read from: `HoyolabClient`'s game data methods."""

    def hsr_characters(self) -> tuple[HsrCharacter, ...]: ...

    def hsr_endgame(self, modes: Iterable[str]) -> dict[str, HsrEndgame | GachaError]: ...

    def hsr_calendar(self) -> HsrCalendar: ...

    def genshin_characters(self) -> tuple[GenshinCharacter, ...]: ...

    def genshin_character_details(
        self, refs: Sequence[int]
    ) -> tuple[GenshinCharacterDetail, ...]: ...

    def genshin_endgame(self, modes: Iterable[str]) -> dict[str, Endgame | GachaError]: ...

    def genshin_calendar(self) -> GameCalendar: ...

    def zzz_characters(self) -> tuple[ZzzAgent, ...]: ...

    def zzz_character_details(self, refs: Sequence[int]) -> tuple[ZzzAgentDetail, ...]: ...

    def zzz_endgame(self, modes: Iterable[str]) -> dict[str, Endgame | GachaError]: ...

    def zzz_calendar(self) -> GameCalendar: ...


def worn_sets(names: Iterable[str]) -> tuple[tuple[str, int], ...]:
    """Each set worn with at least two pieces and its piece count: most pieces first, then by
    name. The pieces are counted because genshin.py leaves an artifact set bonus inactive
    whenever HoYoLAB does not say."""
    counts = Counter(names)
    worn = [(name, pieces) for name, pieces in counts.items() if pieces >= WORN_SET_PIECES]
    return tuple(sorted(worn, key=lambda item: (-item[1], item[0])))


def is_over(ends_at: datetime | None, now: datetime) -> bool:
    """Whether something that ends at `ends_at` (None: no end given) is over at `now`."""
    return ends_at is not None and ends_at <= now


def is_running(starts_at: datetime | None, ends_at: datetime | None, now: datetime) -> bool:
    """Whether something has started (None: no start given) and is not over at `now`."""
    return (starts_at is None or starts_at <= now) and not is_over(ends_at, now)


def on_the_minute(moment: datetime | None) -> datetime | None:
    """`moment` as the game tells it: HoYoLAB gives an end as the last second before the change
    (03:59:59 for the 04:00 reset, 14:59:59 for a banner ending at 15:00), so a time on a
    minute's last second is the next whole minute. Any other time, and None, stay as given."""
    if moment is None or moment.second != LAST_SECOND:
        return moment
    return moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
