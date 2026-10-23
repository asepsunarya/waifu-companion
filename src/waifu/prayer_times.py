"""The five daily prayer times, computed offline with the Kemenag RI method.

The sun comes from the USNO low-precision formulas (the US Naval Observatory's "approximate
solar coordinates"), good to about a minute of arc from 1950 to 2050: its declination and the
equation of time at any moment. Times are WIB (UTC+7, fixed), whose meridian is 105°E, in hours
after the day's midnight until they are rounded.

Kemenag's angles: Subuh when the sun is 20° below the horizon, terbit and Maghrib at -0.8333°
(refraction and the sun's half width; no elevation correction), Dzuhur at the solar noon, Ashar
when a shadow is its object's length plus the noon shadow (Syafi'i), and Isya at -18°. The
ihtiyath, a safety margin, is added to the five prayers, each then rounded up to a whole minute;
terbit, the end of Subuh's time, has it subtracted and is rounded down, so both err on the safe
side. Imsak is ten minutes before Subuh. Where the sun never reaches an angle that day (far from
the equator), that time is None.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from waifu.finance.values import WIB

PRAYERS = ("subuh", "dzuhur", "ashar", "maghrib", "isya")
PRAYER_LABELS = {
    "subuh": "Subuh",
    "dzuhur": "Dzuhur",
    "ashar": "Ashar",
    "maghrib": "Maghrib",
    "isya": "Isya",
}
SUBUH_ALTITUDE = -20.0
ISYA_ALTITUDE = -18.0
HORIZON_ALTITUDE = -0.8333  # the sun's upper edge on the horizon, refraction included
IMSAK_BEFORE = timedelta(minutes=10)
WIB_MERIDIAN = 105.0  # degrees east: UTC+7
_UTC_OFFSET_HOURS = 7.0
_J2000 = 2451545.0
_ORDINAL_TO_JULIAN_DAY = 1721424.5  # date.toordinal() plus this is the Julian day at 0h UT
_PASSES = 3  # each pass takes the sun at the time the one before found

# The sun's declination (degrees) -> the altitude (degrees) that marks the time, or None.
_Altitude = Callable[[float], float | None]


@dataclass(frozen=True)
class PrayerTimes:
    """A day's times as aware WIB datetimes on whole minutes; None where the sun does not reach
    the angle there that day. A time far north may fall on the next day."""

    day: date
    imsak: datetime | None
    subuh: datetime | None
    terbit: datetime | None
    dzuhur: datetime
    ashar: datetime | None
    maghrib: datetime | None
    isya: datetime | None


def solar_noon(day: date, longitude: float) -> float:
    """When the sun crosses the meridian at `longitude`, in hours of WIB after `day`'s
    midnight."""
    hours = 12.0
    for _ in range(_PASSES):
        _, equation_of_time = _sun(day, hours)
        hours = _noon(longitude, equation_of_time)
    return hours


def sun_altitude(day: date, hours: float, latitude: float, longitude: float) -> float:
    """The altitude of the sun's centre in degrees (no refraction) at `hours` of WIB after
    `day`'s midnight."""
    declination, equation_of_time = _sun(day, hours)
    phi, delta = math.radians(latitude), math.radians(declination)
    hour_angle = math.radians(15 * (hours - _noon(longitude, equation_of_time)))
    return math.degrees(
        math.asin(
            math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.cos(hour_angle)
        )
    )


def sun_at_altitude(
    day: date, latitude: float, longitude: float, altitude: float, *, rising: bool
) -> float | None:
    """When the sun's centre is at `altitude` degrees, before noon when `rising`, else after it,
    in hours of WIB after `day`'s midnight; None when it never is that day."""
    return _time_at(day, latitude, longitude, lambda _: altitude, rising=rising)


def prayer_times(
    day: date, latitude: float, longitude: float, *, ihtiyath_minutes: int = 2
) -> PrayerTimes:
    """`day`'s times at the place by the Kemenag RI method, with the ihtiyath in minutes."""
    margin = timedelta(minutes=ihtiyath_minutes)
    midnight = datetime.combine(day, time(0), WIB)

    def prayer(hours: float) -> datetime:
        return _whole_minute_up(midnight + timedelta(hours=hours) + margin)

    def maybe(hours: float | None) -> datetime | None:
        return None if hours is None else prayer(hours)

    subuh = maybe(sun_at_altitude(day, latitude, longitude, SUBUH_ALTITUDE, rising=True))
    sunrise = sun_at_altitude(day, latitude, longitude, HORIZON_ALTITUDE, rising=True)
    terbit = None if sunrise is None else midnight + timedelta(hours=sunrise) - margin
    ashar = _time_at(day, latitude, longitude, _ashar_altitude(latitude), rising=False)
    return PrayerTimes(
        day=day,
        imsak=None if subuh is None else subuh - IMSAK_BEFORE,
        subuh=subuh,
        terbit=None if terbit is None else _whole_minute_down(terbit),
        dzuhur=prayer(solar_noon(day, longitude)),  # the sun crosses the meridian every day
        ashar=maybe(ashar),
        maghrib=maybe(sun_at_altitude(day, latitude, longitude, HORIZON_ALTITUDE, rising=False)),
        isya=maybe(sun_at_altitude(day, latitude, longitude, ISYA_ALTITUDE, rising=False)),
    )


def _ashar_altitude(latitude: float) -> _Altitude:
    """Ashar's altitude for the sun's declination: cot a = 1 + tan|φ - δ|, the noon shadow
    plus one length; none when the sun is below the horizon at noon."""

    def altitude(declination: float) -> float | None:
        noon_zenith = abs(latitude - declination)
        if noon_zenith >= 90:
            return None
        return math.degrees(math.atan(1 / (1 + math.tan(math.radians(noon_zenith)))))

    return altitude


def _time_at(
    day: date, latitude: float, longitude: float, altitude: _Altitude, *, rising: bool
) -> float | None:
    """When the sun is at `altitude(δ)`, before or after noon; None when it never is."""
    hours = 6.0 if rising else 18.0
    phi = math.radians(latitude)
    for _ in range(_PASSES):
        declination, equation_of_time = _sun(day, hours)
        target = altitude(declination)
        if target is None:
            return None
        delta = math.radians(declination)
        cos_hour_angle = (math.sin(math.radians(target)) - math.sin(phi) * math.sin(delta)) / (
            math.cos(phi) * math.cos(delta)
        )
        if not -1 <= cos_hour_angle <= 1:
            return None
        hour_angle = math.degrees(math.acos(cos_hour_angle)) / 15
        noon = _noon(longitude, equation_of_time)
        hours = noon - hour_angle if rising else noon + hour_angle
    return hours


def _noon(longitude: float, equation_of_time: float) -> float:
    return 12 + (WIB_MERIDIAN - longitude) / 15 - equation_of_time


def _sun(day: date, hours: float) -> tuple[float, float]:
    """The sun's declination (degrees) and the equation of time (hours) at `hours` of WIB on
    `day`, by the USNO low-precision formulas."""
    julian_day = day.toordinal() + _ORDINAL_TO_JULIAN_DAY + (hours - _UTC_OFFSET_HOURS) / 24
    d = julian_day - _J2000
    g = math.radians(357.529 + 0.98560028 * d)  # mean anomaly
    q = (280.459 + 0.98564736 * d) % 360  # mean longitude, degrees
    ecliptic = math.radians(q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    obliquity = math.radians(23.439 - 0.00000036 * d)
    right_ascension = math.degrees(
        math.atan2(math.cos(obliquity) * math.sin(ecliptic), math.cos(ecliptic))
    )
    declination = math.degrees(math.asin(math.sin(obliquity) * math.sin(ecliptic)))
    equation_of_time = (q - right_ascension) / 15
    return declination, (equation_of_time + 12) % 24 - 12


def _whole_minute_up(moment: datetime) -> datetime:
    down = _whole_minute_down(moment)
    return down if down == moment else down + timedelta(minutes=1)


def _whole_minute_down(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)
