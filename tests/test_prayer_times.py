"""The five prayer times, computed offline with the Kemenag RI method.

The published schedules below are Kemenag's for DKI Jakarta as generally known (bimasislam's
jadwal sholat, ihtiyath 2 minutes), held to within 2 minutes: Kemenag's own coordinates for the
city, its elevation correction, and its ephemeris differ a little from these formulas. The
independent check is NOAA's fractional-year formulas (Spencer's Fourier series for the sun),
which share nothing with the USNO formulas under test and must agree on the solar noon, the
sunrise, and the sunset within a minute.
"""

import math
from datetime import date, datetime, time, timedelta

import pytest

from waifu.finance.values import WIB
from waifu.prayer_times import (
    PRAYER_LABELS,
    PRAYERS,
    PrayerTimes,
    prayer_times,
    solar_noon,
    sun_altitude,
    sun_at_altitude,
)

JAKARTA = (-6.17, 106.83)  # about Kemenag's coordinates for DKI Jakarta
BANDUNG = (-6.9175, 107.6191)
SUNSET = -0.8333  # refraction and the sun's half width
TOLERANCE = timedelta(minutes=2)


def clock(times: PrayerTimes, name: str) -> str | None:
    moment = getattr(times, name)
    return None if moment is None else f"{moment:%H:%M}"


def minutes_apart(moment: datetime, expected: str) -> timedelta:
    hours, minutes = map(int, expected.split(":"))
    return abs(moment - datetime.combine(moment.date(), time(hours, minutes), WIB))


def noaa(day: date, latitude: float, longitude: float) -> tuple[float, float, float]:
    """The solar noon, sunrise, and sunset in hours of WIB after the day's midnight, from NOAA's
    general solar position formulas (gml.noaa.gov/grad/solcalc/solareqns.PDF)."""
    gamma = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    equation_of_time = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    phi = math.radians(latitude)
    hour_angle = math.degrees(
        math.acos(
            math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(declination))
            - math.tan(phi) * math.tan(declination)
        )
    )
    noon = (720 - 4 * longitude - equation_of_time) / 60 + 7
    return noon, noon - 4 * hour_angle / 60, noon + 4 * hour_angle / 60


def every_day_of(year: int) -> list[date]:
    first = date(year, 1, 1)
    return [first + timedelta(days=n) for n in range((date(year + 1, 1, 1) - first).days)]


def test_the_prayers_are_the_five_in_the_day_s_order_with_their_written_names():
    assert PRAYERS == ("subuh", "dzuhur", "ashar", "maghrib", "isya")
    assert [PRAYER_LABELS[name] for name in PRAYERS] == [
        "Subuh",
        "Dzuhur",
        "Ashar",
        "Maghrib",
        "Isya",
    ]


@pytest.mark.parametrize("place", [JAKARTA, BANDUNG], ids=["jakarta", "bandung"])
def test_the_solar_noon_agrees_with_noaa_within_a_minute_all_year(place):
    for day in every_day_of(2026):
        noon, _, _ = noaa(day, *place)
        assert abs(solar_noon(day, place[1]) - noon) * 60 <= 1, day


@pytest.mark.parametrize("place", [JAKARTA, BANDUNG], ids=["jakarta", "bandung"])
def test_the_sunrise_and_sunset_agree_with_noaa_within_a_minute_all_year(place):
    for day in every_day_of(2026):
        _, sunrise, sunset = noaa(day, *place)
        rising = sun_at_altitude(day, *place, SUNSET, rising=True)
        setting = sun_at_altitude(day, *place, SUNSET, rising=False)
        assert rising is not None and abs(rising - sunrise) * 60 <= 1, day
        assert setting is not None and abs(setting - sunset) * 60 <= 1, day


def test_the_sun_is_at_the_asked_altitude_at_the_time_found():
    # Far north at the equinox, when the declination moves fastest, Isya comes hours after the
    # first guess: the time holds only if the sun is taken at the time found, not at the guess.
    day, place = date(2026, 3, 20), (60.0, 105.0)
    for altitude, rising in [(-20, True), (SUNSET, True), (SUNSET, False), (-18, False)]:
        found = sun_at_altitude(day, *place, altitude, rising=rising)
        assert found is not None
        assert sun_altitude(day, found, *place) == pytest.approx(altitude, abs=0.001)


def test_the_sun_is_highest_at_the_solar_noon():
    day, place = date(2026, 10, 24), BANDUNG
    noon = solar_noon(day, place[1])
    minute = 1 / 60
    highest = sun_altitude(day, noon, *place)
    assert highest > sun_altitude(day, noon - minute, *place)
    assert highest > sun_altitude(day, noon + minute, *place)


def test_on_the_equator_at_the_equinox_the_angles_fall_where_geometry_puts_them():
    # With the sun on the celestial equator, an altitude a is reached at the hour angle 90° - a:
    # Subuh 110° (7h20m) before noon, Isya 108° (7h12m) after it, the sunset 90.8333° after it.
    day, place = date(2026, 3, 20), (0.0, 105.0)
    noon = solar_noon(day, 105.0)
    for altitude, rising, hours in [
        (-20, True, -110 / 15),
        (-18, False, 108 / 15),
        (SUNSET, False, 90.8333 / 15),
    ]:
        found = sun_at_altitude(day, *place, altitude, rising=rising)
        assert found is not None and abs(found - (noon + hours)) * 60 < 1


def test_on_the_equator_at_the_equinox_ashar_is_three_hours_after_noon():
    # No noon shadow, so Ashar is when a shadow equals its object: the sun at 45°, 45° past noon.
    times = prayer_times(date(2026, 3, 20), 0.0, 105.0, ihtiyath_minutes=0)
    noon = solar_noon(date(2026, 3, 20), 105.0)
    ashar = times.ashar - datetime.combine(times.day, time(0), WIB)
    assert abs(ashar.total_seconds() / 3600 - (noon + 3)) * 60 <= 1


@pytest.mark.parametrize(
    ("day", "published"),
    [
        # 1 Ramadan 1445 H
        (
            date(2024, 3, 12),
            {
                "imsak": "04:32",
                "subuh": "04:42",
                "dzuhur": "12:04",
                "ashar": "15:13",
                "maghrib": "18:10",
                "isya": "19:19",
            },
        ),
        # Independence Day
        (
            date(2024, 8, 17),
            {
                "subuh": "04:43",
                "dzuhur": "11:59",
                "ashar": "15:21",
                "maghrib": "17:57",
                "isya": "19:07",
            },
        ),
        # New Year's Day
        (
            date(2025, 1, 1),
            {
                "subuh": "04:18",
                "dzuhur": "11:58",
                "ashar": "15:25",
                "maghrib": "18:13",
                "isya": "19:28",
            },
        ),
    ],
    ids=["ramadan-2024", "agustus-2024", "januari-2025"],
)
def test_jakarta_matches_kemenag_s_published_schedule_within_two_minutes(day, published):
    times = prayer_times(day, *JAKARTA)

    for name, expected in published.items():
        moment = getattr(times, name)
        assert moment is not None and minutes_apart(moment, expected) <= TOLERANCE, (name, moment)


def test_jakarta_s_dzuhur_is_earliest_in_early_november_and_latest_in_mid_february():
    assert clock(prayer_times(date(2026, 11, 3), *JAKARTA), "dzuhur") == "11:39"
    assert clock(prayer_times(date(2026, 2, 15), *JAKARTA), "dzuhur") == "12:09"


def test_the_times_are_whole_minutes_of_wib_on_the_day_in_the_day_s_order():
    times = prayer_times(date(2026, 10, 24), *BANDUNG)

    moments = [times.imsak, times.subuh, times.terbit, times.dzuhur, times.ashar]
    moments += [times.maghrib, times.isya]
    assert times.day == date(2026, 10, 24)
    assert all(moment.tzinfo is WIB and moment.date() == times.day for moment in moments)
    assert all(moment.second == 0 and moment.microsecond == 0 for moment in moments)
    assert moments == sorted(moments)


def test_dzuhur_is_the_first_whole_minute_after_the_solar_noon_plus_the_ihtiyath():
    day = date(2026, 10, 24)
    noon = solar_noon(day, BANDUNG[1]) * 60
    for ihtiyath in range(6):
        dzuhur = prayer_times(day, *BANDUNG, ihtiyath_minutes=ihtiyath).dzuhur
        minutes = dzuhur.hour * 60 + dzuhur.minute
        assert 0 <= minutes - (noon + ihtiyath) < 1


def test_the_ihtiyath_moves_every_prayer_later_and_terbit_earlier():
    day = date(2026, 10, 24)
    plain = prayer_times(day, *BANDUNG, ihtiyath_minutes=0)
    careful = prayer_times(day, *BANDUNG, ihtiyath_minutes=3)

    for name in ("imsak", *PRAYERS):
        assert getattr(careful, name) - getattr(plain, name) == timedelta(minutes=3), name
    assert plain.terbit - careful.terbit == timedelta(minutes=3)


def test_terbit_is_the_sunrise_rounded_down_and_maghrib_the_sunset_rounded_up():
    day = date(2026, 10, 24)
    times = prayer_times(day, *BANDUNG, ihtiyath_minutes=0)
    sunrise = sun_at_altitude(day, *BANDUNG, SUNSET, rising=True) * 60
    sunset = sun_at_altitude(day, *BANDUNG, SUNSET, rising=False) * 60

    assert 0 <= sunrise - (times.terbit.hour * 60 + times.terbit.minute) < 1
    assert 0 <= (times.maghrib.hour * 60 + times.maghrib.minute) - sunset < 1


def test_imsak_is_ten_minutes_before_subuh():
    times = prayer_times(date(2026, 10, 24), *BANDUNG)

    assert times.subuh - times.imsak == timedelta(minutes=10)


def test_at_60_degrees_north_at_the_june_solstice_there_is_no_subuh_or_isya():
    # The sun sinks only to about -6.6° at midnight there, never to -18° or -20°.
    times = prayer_times(date(2026, 6, 21), 60.0, 105.0)

    assert (times.imsak, times.subuh, times.isya) == (None, None, None)
    assert clock(times, "dzuhur") == "12:04"
    assert times.terbit is not None and times.terbit.hour == 2
    assert times.ashar is not None and times.maghrib is not None and times.maghrib.hour == 21


@pytest.mark.parametrize(
    ("latitude", "has_subuh", "has_isya"),
    [(46.4, True, True), (47.5, False, True), (48.7, False, False)],
)
def test_at_the_june_solstice_subuh_goes_from_about_46_6_degrees_north_and_isya_from_48_6(
    latitude, has_subuh, has_isya
):
    # The sun sinks only to latitude + 23.44 - 90 degrees at midnight: past -20° (Subuh) up to
    # 46.56°N, past -18° (Isya) up to 48.56°N.
    times = prayer_times(date(2026, 6, 21), latitude, 10.0)

    assert (times.subuh is not None, times.imsak is not None) == (has_subuh, has_subuh)
    assert (times.isya is not None) == has_isya


def test_under_the_midnight_sun_there_is_no_terbit_or_maghrib():
    times = prayer_times(date(2026, 6, 21), 70.0, 105.0)

    assert (times.terbit, times.maghrib, times.subuh, times.isya) == (None, None, None, None)
    assert times.ashar is not None and times.dzuhur is not None


def test_in_a_polar_night_there_is_no_terbit_ashar_or_maghrib():
    # The sun stays below the horizon at noon (-3.4°), so it casts no shadow for Ashar either,
    # but it still passes -20° and -18° on its way down to -43.4° at midnight.
    times = prayer_times(date(2026, 12, 21), 70.0, 105.0)

    assert (times.terbit, times.ashar, times.maghrib) == (None, None, None)
    assert None not in (times.imsak, times.subuh, times.dzuhur, times.isya)


def test_at_the_pole_it_still_answers():
    times = prayer_times(date(2026, 6, 21), 90.0, 105.0)

    assert times.dzuhur is not None and times.subuh is None and times.maghrib is None
