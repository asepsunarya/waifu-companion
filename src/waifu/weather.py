"""Today's weather from Open-Meteo (https://open-meteo.com): free, no account or key.

A city is geocoded once with Open-Meteo's geocoding API; then one forecast request
returns the conditions now, today's range and rain chance, and two days of hourly codes,
rain chances, and amounts, all in the place's own time zone (`timezone=auto`). `today()`
reads the briefing's weather from it (the code for tomorrow morning among the hourly
values), `forecast()` every hour for the rain alerts. `outlook()` asks a request of its own
for seven days of daily and hourly values, so the briefing's and the rain watcher's request
stay two days. Conditions are WMO weather codes, said in Indonesian (`condition_of`).

She is silent while the weather is fetched, so one call has `TIME_LIMIT_S` for both
requests together: each request's timeout is the time left, and none starts with less
than `MIN_REQUEST_S` left.

Every failure is logged at the client's `failure_level`, WARNING unless a caller that logs its
failures itself (the rain watcher, once per run of them) asks for less.
"""

from __future__ import annotations

import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

from waifu import __version__

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_QUERY = {
    "current": "temperature_2m,weather_code",
    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    "hourly": "weather_code,precipitation_probability,precipitation",
    "timezone": "auto",
    "forecast_days": 2,
}
# Seven days for the questions about the days ahead; `current` only for its time, the place's now.
OUTLOOK_QUERY = {
    "current": "weather_code",
    "daily": (
        "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
        "precipitation_sum"
    ),
    "hourly": "weather_code,temperature_2m,precipitation_probability,precipitation",
    "timezone": "auto",
    "forecast_days": 7,
}
TIME_LIMIT_S = 8.0  # for one call: the city lookup and the forecast together
MIN_REQUEST_S = 1.0
USER_AGENT = f"waifu-companion/{__version__}"
MORNING = "07:00"
# The WMO codes of rain, freezing rain, showers, and thunderstorms; drizzle (51-57) is not rain.
RAIN_CODES = frozenset([*range(61, 68), *range(80, 83), *range(95, 100)])

UNREACHABLE = "Server cuaca tidak bisa dihubungi. Cek koneksi internet, lalu coba lagi."
UNREADABLE = "Data cuaca dari Open-Meteo tidak bisa dibaca. Coba lagi nanti."

# (url, timeout in seconds) -> the decoded JSON reply.
Fetch = Callable[[str, float], Any]
_Read = TypeVar("_Read")


@dataclass(frozen=True)
class Weather:
    place: str
    condition: str  # now
    temperature_c: float  # now
    min_c: float  # today
    max_c: float  # today
    rain_chance: int  # today's highest precipitation probability, %
    tomorrow_morning: str  # the condition at 07:00 tomorrow


@dataclass(frozen=True)
class Hour:
    """One hourly entry; None where Open-Meteo has no value (unknown, never rain).

    Open-Meteo gives the rain chance and sums the amount over the hour before `at`.
    """

    at: datetime  # the place's local time, without an offset
    code: int | None  # WMO weather code
    rain_chance: int | None  # precipitation probability, %
    precipitation_mm: float | None
    temperature_c: float | None = None  # only in the outlook, which asks for it


@dataclass(frozen=True)
class Forecast:
    place: str
    now: datetime  # the reply's current time: the place's local time, in 15-minute steps
    current_code: int  # WMO weather code now
    hours: tuple[Hour, ...]  # every hourly entry of today and tomorrow, in order


@dataclass(frozen=True)
class DayOutlook:
    """One daily entry; None where Open-Meteo has no value (unknown, never zero)."""

    day: date  # the place's local date
    code: int | None  # WMO weather code, the day's most severe
    min_c: float | None
    max_c: float | None
    rain_chance: int | None  # the day's highest precipitation probability, %
    rain_mm: float | None  # the day's summed precipitation


@dataclass(frozen=True)
class Outlook:
    place: str
    now: datetime  # the reply's current time: the place's local time, in 15-minute steps
    days: tuple[DayOutlook, ...]  # today and the next six days, in order
    hours: tuple[Hour, ...]  # every hourly entry of those days with its temperature, in order


class WeatherError(Exception):
    """Why there is no weather; Indonesian and safe to speak."""


@dataclass(frozen=True)
class Location:
    """Where the weather is for: the name she says and its coordinates."""

    place: str
    latitude: float
    longitude: float


def get_json(url: str, timeout_s: float) -> Any:
    """GET a URL and decode its UTF-8 JSON reply (urllib raises HTTPError for 4xx/5xx).

    `timeout_s` bounds the connection and each read (urllib's socket timeout), not the name
    lookup.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


class WeatherClient:
    def __init__(
        self,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        city: str | None = None,
        fetch: Fetch = get_json,
        clock: Callable[[], float] = time.monotonic,
        failure_level: int = logging.WARNING,
    ) -> None:
        if (latitude is None) != (longitude is None):
            raise ValueError("latitude and longitude go together")
        self._city = city
        self._fetch = fetch
        self._clock = clock
        self._failure_level = failure_level
        # Coordinates win over the city, which still names the place she says; a geocoded city
        # is kept here for the client's life.
        self._location: Location | None = None
        if latitude is not None and longitude is not None:
            place = city or f"{latitude}, {longitude}"
            self._location = Location(place, latitude, longitude)

    def location(self) -> Location:
        """The place: its coordinates without a request, else the city geocoded within
        `TIME_LIMIT_S` and kept for the client's life; WeatherError when there is none."""
        return self._locate(self._clock() + TIME_LIMIT_S)

    def today(self) -> Weather:
        """The weather now, today, and tomorrow morning, within `TIME_LIMIT_S`; WeatherError
        when there is none."""
        return self._read_forecast(_weather)

    def forecast(self) -> Forecast:
        """The time and code now and every hourly entry of today and tomorrow, within
        `TIME_LIMIT_S`; WeatherError when there is none."""
        return self._read_forecast(_forecast)

    def outlook(self) -> Outlook:
        """Seven days from today with every hourly entry and its temperature, asked with a
        request of its own within `TIME_LIMIT_S`; WeatherError when there is none."""
        return self._read_forecast(_outlook, OUTLOOK_QUERY)

    def _read_forecast(
        self, parse: Callable[[str, Any], _Read], variables: Mapping[str, object] = FORECAST_QUERY
    ) -> _Read:
        """Asks for the forecast's `variables` at the place and has `parse` read the reply; a
        reply without the fields it reads is the unreadable WeatherError."""
        deadline = self._clock() + TIME_LIMIT_S
        location = self._locate(deadline)
        query = {"latitude": location.latitude, "longitude": location.longitude, **variables}
        data = self._get(FORECAST_URL, query, deadline)
        try:
            return parse(location.place, data)
        except (LookupError, TypeError, ValueError) as exc:
            logger.log(
                self._failure_level,
                "Open-Meteo sent a forecast without the expected fields: %r",
                exc,
            )
            raise WeatherError(UNREADABLE) from None

    def _locate(self, deadline: float) -> Location:
        if self._location is None:
            if self._city is None:
                raise WeatherError(
                    "Lokasi cuaca belum diatur. Isi briefing.city di config.yaml, "
                    "misalnya Bandung, lalu jalankan ulang."
                )
            self._location = self._geocode(self._city, deadline)
            logger.info(
                "Weather for %s (%s, %s)",
                self._location.place,
                self._location.latitude,
                self._location.longitude,
            )
        return self._location

    def _geocode(self, city: str, deadline: float) -> Location:
        data = self._get(GEOCODING_URL, {"name": city, "count": 1, "language": "id"}, deadline)
        try:
            location = _first_place(data)
        except (LookupError, TypeError, ValueError) as exc:
            logger.log(
                self._failure_level,
                "Open-Meteo sent a geocoding reply without the expected fields: %r",
                exc,
            )
            raise WeatherError(UNREADABLE) from None
        if location is None:
            raise WeatherError(
                f"Kota {city} tidak ditemukan. Periksa ejaan briefing.city di config.yaml."
            )
        return location

    def _get(self, base: str, query: Mapping[str, object], deadline: float) -> Any:
        time_left = deadline - self._clock()
        if time_left < MIN_REQUEST_S:
            logger.log(
                self._failure_level,
                "Open-Meteo is too slow: not asking %s with %.1f s left",
                base,
                time_left,
            )
            raise WeatherError(UNREACHABLE)
        # Commas stay literal in the variable lists, as in Open-Meteo's examples; a space is %20.
        url = f"{base}?{urllib.parse.urlencode(query, safe=',', quote_via=urllib.parse.quote)}"
        try:
            return self._fetch(url, time_left)
        except urllib.error.HTTPError as exc:
            logger.log(self._failure_level, "Open-Meteo answered HTTP %s for %s", exc.code, base)
            raise WeatherError(
                f"Server cuaca sedang bermasalah (HTTP {exc.code}). Coba lagi nanti."
            ) from None
        except (OSError, http.client.HTTPException) as exc:  # URLError and timeouts are OSErrors
            logger.log(self._failure_level, "Open-Meteo is unreachable at %s: %s", base, exc)
            raise WeatherError(UNREACHABLE) from None
        except ValueError as exc:  # not UTF-8 or not JSON
            logger.log(
                self._failure_level,
                "Open-Meteo sent something that is not JSON from %s: %s",
                base,
                exc,
            )
            raise WeatherError(UNREADABLE) from None


def _first_place(data: Any) -> Location | None:
    """The first geocoding result, or None when there is none (Open-Meteo omits empty fields)."""
    if not isinstance(data, dict):
        raise TypeError(f"the reply is a {type(data).__name__}, not an object")
    results = data.get("results") or []
    if not results:
        return None
    found = results[0]
    name = found["name"]
    if not isinstance(name, str) or not name:
        raise ValueError(f"the place has no name: {name!r}")
    place = name
    province = found.get("admin1")
    if isinstance(province, str) and province not in ("", name):
        place = f"{name}, {province}"
    return Location(place, _number(found["latitude"]), _number(found["longitude"]))


def _weather(place: str, data: Any) -> Weather:
    """The forecast reply as Weather; its first daily date is today at the place."""
    current, daily, hourly = data["current"], data["daily"], data["hourly"]
    today = daily["time"][0]
    if not isinstance(today, str):
        raise TypeError(f"today's date is not text: {today!r}")
    # Hourly times are the place's local ISO times without an offset, e.g. 2026-10-14T07:00.
    tomorrow_morning = f"{date.fromisoformat(today) + timedelta(days=1)}T{MORNING}"
    hours = hourly["time"]
    if not isinstance(hours, list):
        raise TypeError("the hourly times are not a list")
    return Weather(
        place=place,
        condition=condition_of(current["weather_code"]),
        temperature_c=_number(current["temperature_2m"]),
        min_c=_number(daily["temperature_2m_min"][0]),
        max_c=_number(daily["temperature_2m_max"][0]),
        rain_chance=round(_number(daily["precipitation_probability_max"][0])),
        tomorrow_morning=condition_of(hourly["weather_code"][hours.index(tomorrow_morning)]),
    )


def _forecast(place: str, data: Any) -> Forecast:
    """The forecast reply's time and code now and its hourly entries as a Forecast."""
    current = data["current"]
    return Forecast(
        place=place,
        now=_local_time(current["time"]),
        current_code=int(_number(current["weather_code"])),
        hours=_hours(data["hourly"]),
    )


def _outlook(place: str, data: Any) -> Outlook:
    """The seven-day reply's time now and its daily and hourly entries as an Outlook; a null
    value is None."""
    daily = data["daily"]
    columns = [
        daily[name]
        for name in (
            "time",
            "weather_code",
            "temperature_2m_min",
            "temperature_2m_max",
            "precipitation_probability_max",
            "precipitation_sum",
        )
    ]
    if not all(isinstance(column, list) for column in columns):
        raise TypeError("the daily values are not lists")
    days = tuple(
        DayOutlook(
            day=_local_date(day),
            code=_maybe_code(code),
            min_c=_maybe_number(low),
            max_c=_maybe_number(high),
            rain_chance=_maybe_percent(chance),
            rain_mm=_maybe_number(amount),
        )
        for day, code, low, high, chance, amount in zip(*columns, strict=True)
    )
    if not days:
        raise ValueError("the reply has no day")
    return Outlook(
        place=place,
        now=_local_time(data["current"]["time"]),
        days=days,
        hours=_hours(data["hourly"], temperatures=True),
    )


def _hours(hourly: Any, *, temperatures: bool = False) -> tuple[Hour, ...]:
    """The hourly entries in order, with `temperatures` each with its temperature; a null
    value is None."""
    names = ["time", "weather_code", "precipitation_probability", "precipitation"]
    if temperatures:
        names.append("temperature_2m")
    columns = [hourly[name] for name in names]
    if not all(isinstance(column, list) for column in columns):
        raise TypeError("the hourly values are not lists")
    return tuple(_hour(*values) for values in zip(*columns, strict=True))


def _hour(
    at: object, code: object, chance: object, amount: object, temperature: object = None
) -> Hour:
    return Hour(
        at=_local_time(at),
        code=_maybe_code(code),
        rain_chance=_maybe_percent(chance),
        precipitation_mm=_maybe_number(amount),
        temperature_c=_maybe_number(temperature),
    )


def _local_time(value: object) -> datetime:
    """An ISO time as Open-Meteo sends it, e.g. 2026-10-14T07:00 (the place's local time)."""
    if not isinstance(value, str):
        raise TypeError(f"the time is not text: {value!r}")
    return datetime.fromisoformat(value)


def _local_date(value: object) -> date:
    """An ISO date as Open-Meteo sends it, e.g. 2026-10-14 (the place's local date)."""
    if not isinstance(value, str):
        raise TypeError(f"the date is not text: {value!r}")
    return date.fromisoformat(value)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"not a number: {value!r}")
    return float(value)


def _maybe_number(value: object) -> float | None:
    return None if value is None else _number(value)


def _maybe_code(value: object) -> int | None:
    return None if value is None else int(_number(value))


def _maybe_percent(value: object) -> int | None:
    return None if value is None else round(_number(value))


def condition_of(value: object) -> str:
    """A WMO weather code (Open-Meteo's `weather_code`) in Indonesian."""
    code = int(_number(value))
    if code == 0:
        return "cerah"
    if code == 1:
        return "cerah berawan"
    if code == 2:
        return "berawan"
    if code == 3:
        return "mendung"
    if code in (45, 48):
        return "berkabut"
    if 51 <= code <= 57:
        return "gerimis"
    if 61 <= code <= 67:
        return "hujan deras" if code in (65, 67) else "hujan"
    if 80 <= code <= 82:
        return "hujan lokal deras" if code == 82 else "hujan lokal"
    if 95 <= code <= 99:
        return "badai petir"
    if 71 <= code <= 77 or code in (85, 86):
        return "salju"
    return "tidak diketahui"
