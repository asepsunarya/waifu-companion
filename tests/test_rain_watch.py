"""Rain detection and the rain watcher: no test asks Open-Meteo, speaks, or pushes.

Forecasts are built here as `weather.forecast()` returns them: two days of hourly entries
from DAY 00:00, dry unless a test makes an hour rainy.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

import pytest

from waifu.rain_watch import (
    DRY_GAP,
    RainState,
    RainWindow,
    follow_rain,
    is_rainy,
    rain_window,
    raining_now,
    spoken_hour,
)
from waifu.weather import Forecast, Hour

DAY = datetime(2026, 10, 17)
HOUR = timedelta(hours=1)
LEAD = timedelta(minutes=60)
THRESHOLD = 60
DRY_CODE = 3  # mendung: clouds, no rain


def at(hour: float) -> datetime:
    """`hour` hours after DAY 00:00: 15 is today 15:00, 24.5 tomorrow 00:30."""
    return DAY + timedelta(hours=hour)


def dry(moment: datetime) -> Hour:
    return Hour(moment, DRY_CODE, 10, 0.0)


def wet(moment: datetime, chance: int | None = 80) -> Hour:
    return Hour(moment, 61, chance, 1.2)


def forecast(
    now: float,
    *,
    rain: Iterable[int] = (),
    current_code: int = DRY_CODE,
    changes: dict[int, Hour] | None = None,
) -> Forecast:
    """The forecast at `now` (hours after DAY 00:00), rainy at the `rain` hours (80 %, 1.2 mm),
    with `changes` replacing whole entries."""
    rainy = set(rain)
    hours = [wet(at(h)) if h in rainy else dry(at(h)) for h in range(48)]
    for h, hour in (changes or {}).items():
        hours[h] = hour
    return Forecast("Cianjur, Jawa Barat", at(now), current_code, tuple(hours))


# --- what counts as rain ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "rainy"),
    [
        (Hour(at(15), DRY_CODE, 60, 0.0), True),  # the threshold exactly
        (Hour(at(15), DRY_CODE, 59, 0.0), False),
        (Hour(at(15), DRY_CODE, 10, 0.5), True),  # half a millimetre, whatever the chance
        (Hour(at(15), DRY_CODE, 10, 0.4), False),
        (Hour(at(15), 61, 10, 0.0), True),  # slight rain
        (Hour(at(15), 65, 10, 0.0), True),  # heavy rain
        (Hour(at(15), 66, 10, 0.0), True),  # freezing rain
        (Hour(at(15), 80, 10, 0.0), True),  # showers
        (Hour(at(15), 82, 10, 0.0), True),
        (Hour(at(15), 95, 10, 0.0), True),  # thunderstorm
        (Hour(at(15), 99, 10, 0.0), True),
        (Hour(at(15), 53, 10, 0.2), False),  # drizzle alone
        (Hour(at(15), 55, 10, 0.6), True),  # drizzle with enough of it
        (Hour(at(15), 45, 10, 0.0), False),  # fog
        (Hour(at(15), None, None, None), False),  # nothing known
        (Hour(at(15), None, 70, None), True),
        (Hour(at(15), None, None, 2.0), True),
    ],
)
def test_an_hour_is_rain_by_its_code_its_chance_or_its_amount(hour, rainy):
    assert is_rainy(hour, THRESHOLD) is rainy


def test_the_threshold_is_the_configured_one():
    hour = Hour(at(15), DRY_CODE, 70, 0.0)

    assert (is_rainy(hour, 70), is_rainy(hour, 71)) == (True, False)


def test_it_rains_now_when_the_current_code_is_rain():
    assert raining_now(forecast(14.25, current_code=63), THRESHOLD) is True


def test_it_rains_now_when_the_hour_now_is_rainy_whatever_the_current_code():
    assert raining_now(forecast(14.25, rain=[14]), THRESHOLD) is True


@pytest.mark.parametrize(
    "dry_now",
    [forecast(14.25, rain=[15]), forecast(14.25, current_code=51), forecast(14.0, rain=[13])],
    ids=["rain next hour", "drizzle now", "rain the hour before"],
)
def test_it_does_not_rain_now_otherwise(dry_now):
    assert raining_now(dry_now, THRESHOLD) is False


# --- the next rain ---------------------------------------------------------------------------


def test_the_next_rain_runs_from_its_first_to_its_last_rainy_hour_with_its_highest_chance():
    changes = {15: wet(at(15), 70), 16: wet(at(16), 90), 17: wet(at(17), None)}

    window = rain_window(forecast(14.0, changes=changes), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(15), at(17), 90)


def test_a_rain_whose_chance_is_unknown_has_no_chance():
    window = rain_window(forecast(14.0, changes={15: wet(at(15), None)}), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(15), at(15), None)


@pytest.mark.parametrize(
    ("now", "found"),
    [(13 + 59 / 60, False), (14.0, True), (14 + 1 / 60, True), (14.75, True)],
    ids=["61 minutes ahead", "60 minutes ahead", "59 minutes ahead", "15 minutes ahead"],
)
def test_only_a_rain_starting_within_the_lead_is_found(now, found):
    window = rain_window(forecast(now, rain=[15]), THRESHOLD, within=LEAD)

    assert (window is not None) == found


def test_a_rain_running_past_the_lead_keeps_its_whole_run():
    window = rain_window(forecast(14.0, rain=[15, 16, 17, 18]), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(15), at(18), 80)


def test_the_rain_already_falling_is_not_the_next_rain():
    # Rain now and on through 15:00, dry until 18:00: the next rain to start is at 18:00.
    window = rain_window(forecast(14.25, rain=[14, 15, 18]), THRESHOLD, within=24 * HOUR)

    assert window == RainWindow(at(18), at(18), 80)


def test_the_next_rain_may_start_after_midnight():
    window = rain_window(forecast(23.5, rain=[24, 25]), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(24), at(25), 80)


def test_a_dry_forecast_has_no_next_rain():
    assert rain_window(forecast(14.0), THRESHOLD, within=24 * HOUR) is None


def test_a_rain_in_the_first_entry_starts_there():
    window = rain_window(forecast(-0.5, rain=[0]), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(0), at(0), 80)


# --- one rain, one alert -----------------------------------------------------------------------


def follow(
    rain_forecast: Forecast, episode_last: datetime | None = None, *, lead: timedelta = LEAD
) -> RainState:
    return follow_rain(rain_forecast, threshold=THRESHOLD, lead=lead, episode_last=episode_last)


def test_a_coming_rain_is_told_and_becomes_the_episode():
    state = follow(forecast(14.0, rain=[15, 16]))

    assert state == RainState(RainWindow(at(15), at(16), 80), at(16))


def test_nothing_is_told_while_it_rains_and_the_episode_reaches_this_hour():
    assert follow(forecast(14.25, rain=[14, 15])) == RainState(None, at(14))


def test_rain_falling_now_never_shortens_the_episode():
    assert follow(forecast(14.25, current_code=63), at(16)) == RainState(None, at(16))


def test_the_same_rain_coming_closer_is_not_told_again():
    assert follow(forecast(14.5, rain=[15, 16]), at(16)) == RainState(None, at(16))


def test_a_rain_after_a_break_of_three_hours_is_the_same_rain():
    # Rainy at 16:00, dry at 17:00 and 18:00, rainy again at 19:00 and 20:00.
    assert DRY_GAP == 3 * HOUR
    assert follow(forecast(18.25, rain=[19, 20]), at(16)) == RainState(None, at(20))


def test_a_rain_after_three_dry_hours_is_a_new_rain():
    # Rainy at 16:00, dry from 17:00 through 19:00, rainy again at 20:00.
    state = follow(forecast(19.25, rain=[20]), at(16))

    assert state == RainState(RainWindow(at(20), at(20), 80), at(20))


def test_a_forecast_that_drops_the_rain_keeps_the_episode_so_it_is_not_told_twice():
    told = follow(forecast(14.0, rain=[15]))
    dropped = follow(forecast(14.25), told.episode_last)
    back = follow(forecast(14.5, rain=[15]), dropped.episode_last)

    assert told.tell is not None
    assert dropped == RainState(None, at(15))
    assert back == RainState(None, at(15))


def test_a_rain_beyond_the_lead_is_not_told_yet():
    assert follow(forecast(13.0, rain=[15])) == RainState(None, None)


def test_the_lead_is_the_configured_one():
    state = follow(forecast(13.0, rain=[15]), lead=2 * HOUR)

    assert state.tell == RainWindow(at(15), at(15), 80)


# --- the hour she says -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "said"),
    [
        (0, "jam dua belas malam"),
        (1, "jam satu dini hari"),
        (2, "jam dua dini hari"),
        (3, "jam tiga dini hari"),
        (4, "jam empat pagi"),
        (5, "jam lima pagi"),
        (6, "jam enam pagi"),
        (7, "jam tujuh pagi"),
        (8, "jam delapan pagi"),
        (9, "jam sembilan pagi"),
        (10, "jam sepuluh pagi"),
        (11, "jam sebelas siang"),
        (12, "jam dua belas siang"),
        (13, "jam satu siang"),
        (14, "jam dua siang"),
        (15, "jam tiga sore"),
        (16, "jam empat sore"),
        (17, "jam lima sore"),
        (18, "jam enam sore"),
        (19, "jam tujuh malam"),
        (20, "jam delapan malam"),
        (21, "jam sembilan malam"),
        (22, "jam sepuluh malam"),
        (23, "jam sebelas malam"),
    ],
)
def test_a_whole_hour_is_said_with_its_part_of_the_day(hour, said):
    assert spoken_hour(hour) == said
