"""Facts about the three HoYoverse games the owner plays, and their server clocks.

Every game resets its dailies at 04:00 server time and its weeklies on Monday at
the same hour; the HoYoLAB check-in day turns at midnight server time. Each banner
type keeps its own pity counter (HSR collab and ZZZ rescreening banners included).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

DAILY_RESET_HOUR = 4
PULL_COST = 160  # premium currency per pull, the same in all three games


@dataclass(frozen=True)
class Banner:
    type: int  # the history API's gacha_type
    name: str
    hard_pity: int
    fifty_fifty: bool = False  # a lost 50/50 gives a standard 5-star and guarantees the next
    endpoint: str = "getGachaLog"


@dataclass(frozen=True)
class Game:
    id: str
    name: str
    short: str
    hoyolab_game: str  # genshin.Game value
    stamina: str
    daily: str
    currency: str
    pull_word: str  # what the game calls a pull, for "open the <word> history in the game"
    top_rarity: int
    log_files: tuple[str, ...]  # under %USERPROFILE%\AppData\LocalLow
    data_folder: str
    game_biz: str
    history_api: str
    banners: tuple[Banner, ...]
    standard: frozenset[str]  # casefolded 5-star names a lost 50/50 gives


GAMES: dict[str, Game] = {
    "hsr": Game(
        id="hsr",
        name="Honkai: Star Rail",
        short="HSR",
        hoyolab_game="hkrpg",
        stamina="Trailblaze Power",
        daily="Daily Training",
        currency="Stellar Jade",
        pull_word="warp",
        top_rarity=5,
        log_files=("Cognosphere/Star Rail/Player.log", "miHoYo/Star Rail/Player.log"),
        data_folder="StarRail_Data",
        game_biz="hkrpg_global",
        history_api="https://public-operation-hkrpg-sg.hoyoverse.com/common/hkrpg_gacha_record/api/",
        banners=(
            Banner(11, "Character Event Warp", 90, fifty_fifty=True),
            Banner(12, "Light Cone Event Warp", 80),
            Banner(1, "Stellar Warp", 90),
            Banner(21, "Collaboration Character Warp", 90, endpoint="getLdGachaLog"),
            Banner(22, "Collaboration Light Cone Warp", 80, endpoint="getLdGachaLog"),
        ),
        standard=frozenset(
            name.casefold()
            for name in ("Bailu", "Bronya", "Clara", "Gepard", "Himeko", "Welt", "Yanqing")
        ),
    ),
    "genshin": Game(
        id="genshin",
        name="Genshin Impact",
        short="Genshin",
        hoyolab_game="genshin",
        stamina="Original Resin",
        daily="Daily Commissions",
        currency="Primogem",
        pull_word="wish",
        top_rarity=5,
        log_files=("miHoYo/Genshin Impact/output_log.txt", "miHoYo/Genshin Impact/Player.log"),
        data_folder="GenshinImpact_Data",
        game_biz="hk4e_global",
        history_api="https://public-operation-hk4e-sg.hoyoverse.com/gacha_info/api/",
        banners=(
            # The API returns the second character banner (gacha_type 400) inside 301.
            Banner(301, "Character Event Wish", 90, fifty_fifty=True),
            Banner(302, "Weapon Event Wish", 80),
            Banner(200, "Wanderlust Invocation", 90),
            Banner(500, "Chronicled Wish", 90),
        ),
        standard=frozenset(
            name.casefold()
            for name in (
                "Dehya",
                "Diluc",
                "Jean",
                "Keqing",
                "Mona",
                "Qiqi",
                "Tighnari",
                "Yumemizuki Mizuki",
            )
        ),
    ),
    "zzz": Game(
        id="zzz",
        name="Zenless Zone Zero",
        short="ZZZ",
        hoyolab_game="nap",
        stamina="Battery Charge",
        daily="Engagement",
        currency="Polychrome",
        pull_word="Signal Search",
        top_rarity=4,  # an S-rank
        log_files=("miHoYo/ZenlessZoneZero/Player.log",),
        data_folder="ZenlessZoneZero_Data",
        game_biz="nap_global",
        history_api="https://public-operation-common-sg.hoyoverse.com/common/gacha_record/api/",
        banners=(
            Banner(2, "Exclusive Channel", 90, fifty_fifty=True),
            Banner(3, "W-Engine Channel", 80),
            Banner(1, "Stable Channel", 90),
            Banner(5, "Bangboo Channel", 80),
            Banner(102, "Exclusive Rescreening", 90),
            Banner(103, "W-Engine Reverberation", 80),
        ),
        # The history may write an agent's short or full name; both are listed.
        standard=frozenset(
            name.casefold()
            for name in (
                "Grace",
                "Grace Howard",
                "Koleda",
                "Koleda Belobog",
                "Lycaon",
                "Von Lycaon",
                "Nekomata",
                "Rina",
                "Alexandrina Sebastiane",
                "Soldier 11",
            )
        ),
    ),
}


def is_standard(game: Game, name: str, extra: Iterable[str] = ()) -> bool:
    """Whether a 5-star is one a lost 50/50 gives (the standard pool or the owner's picks)."""
    folded = name.casefold()
    return folded in game.standard or any(folded == pick.casefold() for pick in extra)


def server_timezone(offset: int) -> timezone:
    return timezone(timedelta(hours=offset))


def game_day(now: datetime, offset: int) -> date:
    """The game's day for `now`; it changes at 04:00 server time."""
    return (now.astimezone(server_timezone(offset)) - timedelta(hours=DAILY_RESET_HOUR)).date()


def checkin_day(now: datetime, offset: int) -> date:
    """The HoYoLAB check-in day for `now`; it changes at midnight server time."""
    return now.astimezone(server_timezone(offset)).date()


def game_day_start(day: date, offset: int) -> datetime:
    return datetime.combine(day, time(DAILY_RESET_HOUR), tzinfo=server_timezone(offset))


def next_daily_reset(now: datetime, offset: int) -> datetime:
    """When the next daily reset happens, in `now`'s timezone."""
    reset = game_day_start(game_day(now, offset) + timedelta(days=1), offset)
    return reset.astimezone(now.tzinfo)


def is_last_day_of_week(day: date) -> bool:
    """Sunday: the weekly reset comes on Monday at 04:00 server time."""
    return day.weekday() == 6


def reminder_due(now: datetime, daily_time: time, offset: int) -> bool:
    """Whether the first local `daily_time` at or after the start of today's game day has passed."""
    start = game_day_start(game_day(now, offset), offset).astimezone(now.tzinfo)
    moment = datetime.combine(start.date(), daily_time, tzinfo=now.tzinfo)
    if moment < start:
        moment += timedelta(days=1)
    return now >= moment
