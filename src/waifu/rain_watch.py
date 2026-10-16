"""Telling Master shortly before rain, from the hourly forecast.

An hourly entry is rain when its WMO code is rain, showers, or a thunderstorm, its
precipitation probability reaches the threshold, or it holds at least `RAIN_MM`; drizzle
counts only through its amount or probability. Open-Meteo sums an entry's rain over the hour
before its time; she reads it as rain around that time, as weather apps show it.

A rain is told once: one starting within the lead while it is dry now. While it rains, while
the same rain comes closer, and when a rain starts no more than `DRY_GAP` after the last
rainy hour already known (fewer than three dry entries between), nothing more is said. A
forecast that drops the rain keeps the episode, so a wavering forecast never tells it twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from waifu.weather import RAIN_CODES, Forecast, Hour

RAIN_MM = 0.5  # an hour with at least this much is rain, whatever its probability
DRY_GAP = timedelta(hours=3)  # a rain starting this soon after the last rainy hour is the same
# Hour numbers as she says them, from "dua belas" (0 and 12) on.
_NUMBERS = (
    "dua belas",
    "satu",
    "dua",
    "tiga",
    "empat",
    "lima",
    "enam",
    "tujuh",
    "delapan",
    "sembilan",
    "sepuluh",
    "sebelas",
)


@dataclass(frozen=True)
class RainWindow:
    """A run of consecutive rainy entries."""

    start: datetime  # the first rainy entry's time
    last: datetime  # the last one's
    chance: int | None  # the highest known probability in it, %


@dataclass(frozen=True)
class RainState:
    """What one forecast means: the rain to tell, if any, and the episode's last rainy hour."""

    tell: RainWindow | None
    episode_last: datetime | None


def is_rainy(hour: Hour, threshold: int) -> bool:
    """Whether the entry is rain: its code, its probability against `threshold`, its amount."""
    return (
        hour.code in RAIN_CODES
        or (hour.rain_chance is not None and hour.rain_chance >= threshold)
        or (hour.precipitation_mm is not None and hour.precipitation_mm >= RAIN_MM)
    )


def raining_now(forecast: Forecast, threshold: int) -> bool:
    """Whether the current code is rain or the entry of the current hour is."""
    if forecast.current_code in RAIN_CODES:
        return True
    this_hour = _hour_of(forecast.now)
    return any(hour.at == this_hour and is_rainy(hour, threshold) for hour in forecast.hours)


def rain_window(forecast: Forecast, threshold: int, *, within: timedelta) -> RainWindow | None:
    """The first rain that starts after `forecast.now` and at most `within` later: a rainy
    entry after a dry one (or the first entry), with the rainy entries that follow it."""
    rainy = [is_rainy(hour, threshold) for hour in forecast.hours]
    for first, hour in enumerate(forecast.hours):
        starts = rainy[first] and (first == 0 or not rainy[first - 1])
        if starts and forecast.now < hour.at <= forecast.now + within:
            last = first
            while last + 1 < len(rainy) and rainy[last + 1]:
                last += 1
            run = forecast.hours[first : last + 1]
            chances = [entry.rain_chance for entry in run if entry.rain_chance is not None]
            return RainWindow(hour.at, run[-1].at, max(chances, default=None))
    return None


def follow_rain(
    forecast: Forecast, *, threshold: int, lead: timedelta, episode_last: datetime | None
) -> RainState:
    """The rain to tell after this forecast, and how far the episode now reaches."""
    if raining_now(forecast, threshold):
        this_hour = _hour_of(forecast.now)
        return RainState(None, this_hour if episode_last is None else max(episode_last, this_hour))
    window = rain_window(forecast, threshold, within=lead)
    if window is None:
        return RainState(None, episode_last)
    if episode_last is not None and window.start - episode_last <= DRY_GAP:
        return RainState(None, max(episode_last, window.last))
    return RainState(window, window.last)


def spoken_hour(hour: int) -> str:
    """`jam tiga sore`: a whole hour of the day (0-23) as she says it."""
    if hour == 0:
        part = "malam"
    elif hour <= 3:
        part = "dini hari"
    elif hour <= 10:
        part = "pagi"
    elif hour <= 14:
        part = "siang"
    elif hour <= 18:
        part = "sore"
    else:
        part = "malam"
    return f"jam {_NUMBERS[hour % 12]} {part}"


def _hour_of(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)
