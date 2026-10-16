"""The watcher inside `waifu run` that tells Master shortly before rain.

Every `POLL_S`, timed from the start of the previous poll and first right after it starts, it
reads the forecast for the briefing's place, but only between `active_from` and `active_until`
on the PC's clock: at night it asks nothing and stays quiet. A rain starting within
`lead_minutes` of the forecast's own time is said between utterances, once the follow-up window
has closed, and pushed to the phone when notifications are on. A failed read is never said: the
first of a run of failures is logged at WARNING and the next poll tries again. What was told
lives only in this process, so a restart may tell a rain once more. Nothing is raised.

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

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from waifu.avatar.emotion import NEUTRAL
from waifu.config import RainAlertConfig, at_sentence_start
from waifu.notify import Notifier
from waifu.weather import RAIN_CODES, Forecast, Hour, WeatherError

logger = logging.getLogger(__name__)

POLL_S = 900.0
NOTICE_EMOTION = NEUTRAL  # a calm heads-up
RAIN_TAG = "umbrella"  # ntfy shows it as an umbrella with rain drops
STOP_TIMEOUT_S = 5.0
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

# Waits up to the given seconds for the event; True when it was set.
Wait = Callable[[threading.Event, float], bool]


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


class RainWatcher:
    def __init__(
        self,
        read_forecast: Callable[[], Forecast],
        config: RainAlertConfig,
        *,
        announce: Callable[[str, str], None] | None,
        notifier: Notifier | None,
        owner_call: str,
        place: str | None,
        title: str = "Cyrene",
        now: Callable[[], datetime] = datetime.now,
        clock: Callable[[], float] = time.monotonic,
        wait: Wait = threading.Event.wait,
    ) -> None:
        """`place` is the city she names (none: she names no place); `now` is the PC's local
        time, for the active hours, and `clock` monotonic seconds, for the polls."""
        self._read_forecast = read_forecast
        self._config = config
        self._announce = announce
        self._notifier = notifier
        self._owner = at_sentence_start(owner_call)  # every alert opens with it
        self._place = place
        self._title = title
        self._now = now
        self._clock = clock
        self._wait = wait
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._episode_last: datetime | None = None  # the last rainy hour of the rain told
        self._failing = False  # the last read failed

    def poll_once(self) -> list[str]:
        """Inside the active hours, read the forecast and give the alert that is due; returns
        its text."""
        if not self._config.active_from <= self._now().time() < self._config.active_until:
            return []
        try:
            forecast = self._read_forecast()
        except Exception as exc:
            self._log_failure(exc)
            return []
        if self._failing:
            self._failing = False
            logger.info("The rain forecast can be read again")
        state = follow_rain(
            forecast,
            threshold=self._config.threshold,
            lead=timedelta(minutes=self._config.lead_minutes),
            episode_last=self._episode_last,
        )
        self._episode_last = state.episode_last
        if state.tell is None:
            return []
        logger.info(
            "Rain is likely from %s (chance %s %%); telling Master",
            f"{state.tell.start:%H:%M}",
            state.tell.chance,
        )
        where = f" di {self._place}" if self._place else ""
        text = (
            f"{self._owner}, sekitar {spoken_hour(state.tell.start.hour)} kemungkinan "
            f"hujan{where}, angkat jemuran ya."
        )
        self._deliver(text)
        return [text]

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="rain-watcher", daemon=True)
        self._thread.start()
        logger.info(
            "Watching for rain %d minutes ahead between %s and %s",
            self._config.lead_minutes,
            f"{self._config.active_from:%H:%M}",
            f"{self._config.active_until:%H:%M}",
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = self._clock()
            self.poll_once()
            self._wait(self._stop, max(0.0, started + POLL_S - self._clock()))

    def _log_failure(self, exc: Exception) -> None:
        """The first failure of a run at WARNING (an unexpected one with its traceback), the
        rest at DEBUG: Open-Meteo out of reach for hours is one line, not one per poll."""
        traceback = not isinstance(exc, WeatherError)
        if self._failing:
            logger.debug("The rain forecast still cannot be read: %s", exc, exc_info=traceback)
            return
        self._failing = True
        logger.warning(
            "Could not read the rain forecast (%s); trying again at every poll",
            exc,
            exc_info=traceback,
        )

    def _deliver(self, text: str) -> None:
        """Say the alert and push it; a failure of either is logged, never raised."""
        if self._announce is not None:
            try:
                self._announce(text, NOTICE_EMOTION)
            except Exception:
                logger.exception("Could not say a rain alert")
        if self._notifier is not None:
            try:
                self._notifier.send(self._title, text, tags=[RAIN_TAG])
            except Exception:
                logger.exception("Could not push a rain alert")
