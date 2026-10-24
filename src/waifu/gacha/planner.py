"""What ends before the next reset that is not the daily one, told ahead (v1.50).

Pure rules over a game's event calendar, keyed through `ReminderState` like the other
reminders: from `reminders.daily_time`, one evening line per game per game day names the
endgame seasons ending within three days below their total (each season once) and the events
ending within two days with progress left. Only what the calendar carries counts: an event or
a window without progress (Genshin's events, the Theater, a season not played yet) is never
told.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta

from waifu.config import GachaRemindersConfig, at_sentence_start
from waifu.gacha.game_data import GAME_MODES, ChallengeWindow, GameCalendar, GameEvent, is_running
from waifu.gacha.games import GAMES, game_day, reminder_due, server_timezone
from waifu.gacha.reminders import Notice, ReminderState
from waifu.gacha.starrail import HsrCalendar, HsrChallengeWindow, HsrEvent
from waifu.gacha.zenless import EVENT_REWARD

Calendar = HsrCalendar | GameCalendar
Event = HsrEvent | GameEvent
Season = HsrChallengeWindow | ChallengeWindow
# A game's event calendar, read from HoYoLAB.
CalendarReader = Callable[[str], Calendar]

EVENT_HOURS = 48
SEASON_HOURS = 72
EVENTS_AT_MOST = 2
# Whose event progress counts something with a name of its own.
EVENT_UNITS = {"zzz": EVENT_REWARD}


def ending_events(calendar: Calendar, now: datetime) -> list[Event]:
    """The running events that end within EVENT_HOURS with progress left, soonest first, at
    most EVENTS_AT_MOST."""
    horizon = now + timedelta(hours=EVENT_HOURS)
    ending = [
        event
        for event in calendar.events
        if _ends_by(event.starts_at, event.ends_at, now, horizon)
        and _unfinished(event.current, event.total)
        and not event.done
    ]
    return sorted(ending, key=_end)[:EVENTS_AT_MOST]


def ending_seasons(calendar: Calendar, now: datetime) -> list[Season]:
    """The running endgame windows that end within SEASON_HOURS below their total, soonest
    first."""
    horizon = now + timedelta(hours=SEASON_HOURS)
    ending = [
        season
        for season in calendar.challenges
        if _ends_by(season.starts_at, season.ends_at, now, horizon)
        and _unfinished(season.current, season.total)
    ]
    return sorted(ending, key=_end)


def evening_due(
    game_id: str,
    now: datetime,
    state: ReminderState,
    rules: GachaRemindersConfig,
    offset: int,
) -> bool:
    """Whether `game_id`'s evening line may come now: the planner on, the evening of the daily
    reminder, and no line yet this game day."""
    return (
        rules.planner
        and reminder_due(now, rules.daily_time, offset)
        and _day_key(game_id, now, offset) not in state.fired
    )


def evening_notice(
    game_id: str,
    calendar: Calendar,
    now: datetime,
    state: ReminderState,
    offset: int,
    *,
    owner_call: str = "Master",
) -> Notice | None:
    """The evening line of one game from its calendar, once a game day: the seasons not told
    yet, then the events; None (and nothing remembered) when nothing ends soon."""
    day_key = _day_key(game_id, now, offset)
    if day_key in state.fired:
        return None
    seasons = [
        season
        for season in ending_seasons(calendar, now)
        if _season_key(game_id, season, offset) not in state.fired
    ]
    events = ending_events(calendar, now)
    if not seasons and not events:
        return None
    day = game_day(now, offset)
    state.once(day_key, day)
    for season in seasons:
        state.once(_season_key(game_id, season, offset), day)
    unit = f" {EVENT_UNITS[game_id]}" if game_id in EVENT_UNITS else ""
    parts = [
        *(
            (
                GAME_MODES[game_id][each.mode],
                _end(each),
                f"baru dapat {each.current} dari {each.total}",
            )
            for each in seasons
        ),
        *(
            (f"event {each.name}", _end(each), f"baru {each.current} dari {each.total}{unit}")
            for each in events
        ),
    ]
    sentences = []
    for index, (subject, ends_at, progress) in enumerate(parts):
        if index == 0:  # calls the owner and names the game: "Master, event X di HSR ..."
            opening = f"{at_sentence_start(owner_call)}, {subject} di {GAMES[game_id].short}"
        else:
            opening = at_sentence_start(subject)
        left = _time_left(ends_at - now)
        sentences.append(f"{opening} berakhir sekitar {left} lagi, {progress}.")
    return Notice(game_id, "planner", " ".join(sentences), "surprised")


def _ends_by(
    starts_at: datetime | None, ends_at: datetime | None, now: datetime, horizon: datetime
) -> bool:
    return ends_at is not None and is_running(starts_at, ends_at, now) and ends_at <= horizon


def _unfinished(current: int | None, total: int | None) -> bool:
    return current is not None and total is not None and current < total


def _end(item: Event | Season) -> datetime:
    assert item.ends_at is not None  # only items that end are kept
    return item.ends_at


def _time_left(left: timedelta) -> str:
    """`45 menit` under an hour, else the nearest whole hours: `20 jam`."""
    minutes = math.ceil(left.total_seconds() / 60)
    if minutes < 60:
        return f"{minutes} menit"
    return f"{(minutes + 30) // 60} jam"


def _day_key(game_id: str, now: datetime, offset: int) -> str:
    return f"planner:{game_id}:{game_day(now, offset)}"


def _season_key(game_id: str, season: Season, offset: int) -> str:
    ends = _end(season).astimezone(server_timezone(offset))
    return f"planner-season:{game_id}:{season.mode}:{ends:%Y-%m-%d}"
