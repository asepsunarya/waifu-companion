"""Rain detection and the rain watcher: no test asks Open-Meteo, speaks, or pushes.

Forecasts are built here as `weather.forecast()` returns them: two days of hourly entries
from DAY 00:00, dry unless a test makes an entry rainy. As in Open-Meteo, an entry holds the
hour before its time: a rainy 16:00 entry is rain from 15:00 to 16:00. Every `RainWatcher` gets
a fake read, a fake local time, a fake announce, and a fake notifier; the thread tests run a
real thread whose waits are real or last a few milliseconds.
"""

import logging
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timedelta

import pytest

from waifu.config import RainAlertConfig
from waifu.rain_watch import (
    DRY_GAP,
    POLL_S,
    RainState,
    RainWatcher,
    RainWindow,
    follow_rain,
    is_rainy,
    rain_window,
    raining_now,
    spoken_hour,
)
from waifu.weather import Forecast, Hour, WeatherError

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
    """The forecast at `now` (hours after DAY 00:00), rainy at the `rain` entries (80 %, 1.2 mm;
    each the hour before its time), with `changes` replacing whole entries."""
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
    assert raining_now(forecast(14.25, current_code=63)) is True


def test_it_rains_now_when_the_hour_running_now_holds_rain_whatever_the_current_code():
    # At 14:15 the hour running now is 14:00-15:00: the 15:00 entry.
    assert raining_now(forecast(14.25, rain=[15])) is True


def test_half_a_millimetre_in_the_hour_running_now_is_rain_now():
    assert raining_now(forecast(14.25, changes={15: Hour(at(15), DRY_CODE, 10, 0.5)})) is True


@pytest.mark.parametrize(
    "dry_now",
    [
        forecast(14.25, rain=[16]),
        forecast(14.25, current_code=51),
        forecast(14.5, rain=[14]),
        forecast(15.0, rain=[15]),
        forecast(14.25, changes={15: Hour(at(15), DRY_CODE, 95, 0.4)}),
        forecast(14.25, changes={15: Hour(at(15), 61, 40, 0.2)}),
    ],
    ids=[
        "rain next hour",
        "drizzle now",
        "rain the hour before",
        "rain that just ended",
        "a chance alone",
        "a rain code forecast for the hour",
    ],
)
def test_it_does_not_rain_now_otherwise(dry_now):
    assert raining_now(dry_now) is False


# --- the next rain ---------------------------------------------------------------------------


def test_the_next_rain_runs_from_the_start_of_its_first_rainy_hour_to_the_end_of_its_last():
    # The 16:00 to 18:00 entries: rain from 15:00 to 18:00, at most 90 %.
    changes = {16: wet(at(16), 70), 17: wet(at(17), 90), 18: wet(at(18), None)}

    window = rain_window(forecast(14.0, changes=changes), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(15), at(18), 90)


def test_a_rain_whose_chance_is_unknown_has_no_chance():
    window = rain_window(forecast(14.0, changes={16: wet(at(16), None)}), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(15), at(16), None)


@pytest.mark.parametrize(
    ("now", "found"),
    [(13 + 59 / 60, False), (14.0, True), (14 + 1 / 60, True), (14.75, True), (15.0, False)],
    ids=[
        "61 minutes ahead",
        "60 minutes ahead",
        "59 minutes ahead",
        "15 minutes ahead",
        "started",
    ],
)
def test_only_a_rain_starting_within_the_lead_is_found(now, found):
    # The 16:00 entry: rain from 15:00, the time the lead counts to.
    window = rain_window(forecast(now, rain=[16]), THRESHOLD, within=LEAD)

    assert (window is not None) == found


def test_a_rain_running_past_the_lead_keeps_its_whole_run():
    window = rain_window(forecast(14.0, rain=[16, 17, 18, 19]), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(15), at(19), 80)


def test_the_rain_already_falling_is_not_the_next_rain():
    # Rain now and on until 16:00, dry until 18:00: the next rain starts at 18:00.
    window = rain_window(forecast(14.25, rain=[15, 16, 19]), THRESHOLD, within=24 * HOUR)

    assert window == RainWindow(at(18), at(19), 80)


def test_a_rain_after_an_hour_with_only_a_chance_of_it_starts_when_that_hour_ends():
    # At 14:00 the hour running now has a 62 % chance but holds no rain, so it is not a rain
    # falling now: the rain starts at 15:00, without the hour's chance.
    changes = {15: Hour(at(15), DRY_CODE, 62, 0.0), 16: wet(at(16), 90), 17: wet(at(17), 70)}

    window = rain_window(forecast(14.0, changes=changes), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(15), at(17), 90)


def test_the_next_rain_may_start_after_midnight():
    window = rain_window(forecast(23.5, rain=[25, 26]), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(24), at(26), 80)


def test_a_dry_forecast_has_no_next_rain():
    assert rain_window(forecast(14.0), THRESHOLD, within=24 * HOUR) is None


def test_a_rain_in_the_first_entry_starts_an_hour_before_it():
    window = rain_window(forecast(-1.5, rain=[0]), THRESHOLD, within=LEAD)

    assert window == RainWindow(at(-1), at(0), 80)


# --- one rain, one alert -----------------------------------------------------------------------


def follow(
    rain_forecast: Forecast, episode_last: datetime | None = None, *, lead: timedelta = LEAD
) -> RainState:
    return follow_rain(rain_forecast, threshold=THRESHOLD, lead=lead, episode_last=episode_last)


def test_a_coming_rain_is_told_and_becomes_the_episode():
    state = follow(forecast(14.0, rain=[16, 17]))

    assert state == RainState(RainWindow(at(15), at(17), 80), at(17))


def test_nothing_is_told_while_it_rains_and_the_episode_reaches_the_end_of_this_hour():
    assert follow(forecast(14.25, rain=[15, 16])) == RainState(None, at(15))


def test_rain_falling_now_never_shortens_the_episode():
    assert follow(forecast(14.25, current_code=63), at(17)) == RainState(None, at(17))


def test_the_same_rain_coming_closer_is_not_told_again():
    assert follow(forecast(14.5, rain=[16, 17]), at(17)) == RainState(None, at(17))


def test_a_rain_after_two_dry_hours_is_the_same_rain():
    # Rain until 16:00, dry from 16:00 to 18:00, rain again from 18:00 to 20:00.
    assert DRY_GAP == 3 * HOUR
    assert follow(forecast(17.25, rain=[19, 20]), at(16)) == RainState(None, at(20))


def test_a_rain_after_three_dry_hours_is_a_new_rain():
    # Rain until 16:00, dry from 16:00 to 19:00, rain again from 19:00 to 20:00.
    state = follow(forecast(18.25, rain=[20]), at(16))

    assert state == RainState(RainWindow(at(19), at(20), 80), at(20))


def test_a_forecast_that_drops_the_rain_keeps_the_episode_so_it_is_not_told_twice():
    told = follow(forecast(14.0, rain=[16]))
    dropped = follow(forecast(14.25), told.episode_last)
    back = follow(forecast(14.5, rain=[16]), dropped.episode_last)

    assert told.tell is not None
    assert dropped == RainState(None, at(16))
    assert back == RainState(None, at(16))


def test_a_rain_beyond_the_lead_is_not_told_yet():
    # From 15:00, two hours after 13:00.
    assert follow(forecast(13.0, rain=[16])) == RainState(None, None)


def test_the_lead_is_the_configured_one():
    state = follow(forecast(13.0, rain=[16]), lead=2 * HOUR)

    assert state.tell == RainWindow(at(15), at(16), 80)


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


# --- the watcher -------------------------------------------------------------------------------

AFTERNOON = at(14)  # the PC's local time in most watcher tests
THREE_PM = "Master, sekitar jam tiga sore kemungkinan hujan di Cianjur, angkat jemuran ya."
UNREACHABLE = "Server cuaca tidak bisa dihubungi. Cek koneksi internet, lalu coba lagi."


class FakeForecasts:
    """Stands in for WeatherClient.forecast: hands out `reply`, or raises it."""

    def __init__(self, reply: Forecast | Exception) -> None:
        self.reply = reply
        self.calls = 0

    def __call__(self) -> Forecast:
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class FakeNotifier:
    def __init__(self, outcome: bool | Exception = True) -> None:
        self.sent: list[tuple[str, str, tuple[str, ...]]] = []
        self.outcome = outcome

    def send(self, title, message, *, tags=()):
        self.sent.append((title, message, tuple(tags)))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Rig:
    """A watcher and the fakes around it: the forecast, the PC's local time, the voice."""

    def __init__(
        self,
        reply: Forecast | Exception,
        *,
        owner_call: str = "Master",
        place: str | None = "Cianjur",
        **config,
    ) -> None:
        self.read = FakeForecasts(reply)
        self.now = AFTERNOON
        self.notifier = FakeNotifier()
        self.announced: list[tuple[str, str]] = []
        self.watcher = RainWatcher(
            self.read,
            RainAlertConfig(**config),
            announce=lambda text, emotion: self.announced.append((text, emotion)),
            notifier=self.notifier,
            owner_call=owner_call,
            place=place,
            now=lambda: self.now,
        )

    def poll(self, reply: Forecast | Exception | None = None) -> list[str]:
        """One poll, reading `reply` when given (else the last one)."""
        if reply is not None:
            self.read.reply = reply
        return self.watcher.poll_once()


def test_a_rain_within_the_hour_is_said_calmly_and_pushed_with_an_umbrella():
    # At 14:00, the rain from 15:00: the 16:00 and 17:00 entries.
    rig = Rig(forecast(14.0, rain=[16, 17]))

    notices = rig.poll()

    assert notices == [THREE_PM]
    assert rig.announced == [(THREE_PM, "neutral")]
    assert rig.notifier.sent == [("Cyrene", THREE_PM, ("umbrella",))]


def test_she_says_it_as_the_owner_asked_calling_them_at_sentence_start():
    rig = Rig(forecast(14.0, rain=[16]), owner_call="sayang")

    assert rig.poll() == [
        "Sayang, sekitar jam tiga sore kemungkinan hujan di Cianjur, angkat jemuran ya."
    ]


def test_without_a_city_she_names_no_place():
    rig = Rig(forecast(14.0, rain=[16]), place=None)

    assert rig.poll() == ["Master, sekitar jam tiga sore kemungkinan hujan, angkat jemuran ya."]


def test_one_rain_is_told_once_as_it_comes_closer_falls_and_pauses():
    # Rain from 15:00 to 17:00 and from 19:00 to 20:00.
    rig = Rig(forecast(14.0, rain=[16, 17, 20]))
    notices = rig.poll()
    for now in (14.25, 14.5, 14.75, 15.25, 16.25, 17.25, 18.0, 18.25, 19.25):
        rig.now = at(now)
        notices += rig.poll(forecast(now, rain=[16, 17, 20]))

    assert notices == [THREE_PM]
    assert len(rig.notifier.sent) == 1


def test_a_new_rain_after_three_dry_hours_is_told_again():
    # Rain from 15:00 to 16:00, then from 19:00.
    rig = Rig(forecast(14.0, rain=[16]))
    rig.poll()
    rig.now = at(18.25)

    notices = rig.poll(forecast(18.25, rain=[16, 20]))

    assert notices == [
        "Master, sekitar jam tujuh malam kemungkinan hujan di Cianjur, angkat jemuran ya."
    ]


def test_a_wavering_forecast_never_tells_the_same_rain_twice():
    rig = Rig(forecast(14.0, changes={16: Hour(at(16), DRY_CODE, 62, 0.0)}))
    notices = rig.poll()
    notices += rig.poll(forecast(14.25, changes={16: Hour(at(16), DRY_CODE, 58, 0.0)}))
    notices += rig.poll(forecast(14.5, changes={16: Hour(at(16), DRY_CODE, 61, 0.0)}))

    assert notices == [THREE_PM]


def test_an_hour_with_only_a_chance_of_rain_never_silences_the_rain_after_it():
    # At 13:45 the rain from 15:00 is too far ahead; at 14:00 the hour running now reaches a
    # 62 % chance with clouds and no rain, which is not rain falling now.
    ahead = {15: Hour(at(15), DRY_CODE, 55, 0.0), 16: wet(at(16), 90), 17: wet(at(17), 90)}
    rig = Rig(forecast(13.75, changes=ahead))
    rig.now = at(13.75)
    notices = rig.poll()
    for now in (14.0, 14.25, 14.75):
        rig.now = at(now)
        notices += rig.poll(forecast(now, changes={**ahead, 15: Hour(at(15), DRY_CODE, 62, 0.0)}))

    assert notices == [THREE_PM]


def test_a_rain_of_chances_alone_is_told_once_as_it_passes():
    # From 15:00 to 19:00 at 80 % with clouds and no amount: never rain falling now, so every
    # poll sees its rest start within the hour.
    likely = {h: Hour(at(h), DRY_CODE, 80, 0.0) for h in (16, 17, 18, 19)}
    rig = Rig(forecast(14.0, changes=likely))
    notices = rig.poll()
    for now in (14.25, 14.75, 15.0, 15.5, 16.25, 17.0, 17.75, 18.5, 19.25):
        rig.now = at(now)
        notices += rig.poll(forecast(now, changes=likely))

    assert notices == [THREE_PM]


def test_rain_already_falling_when_she_starts_is_never_told():
    rig = Rig(forecast(14.25, rain=[15, 16, 17]))

    assert rig.poll() == []
    assert rig.notifier.sent == []


def test_the_lead_is_timed_on_the_forecasts_own_clock():
    # The PC says 14:00, but the forecast is of 13:00: the rain from 15:00 is two hours ahead.
    rig = Rig(forecast(13.0, rain=[16]))

    assert rig.poll() == []


@pytest.mark.parametrize(
    ("now", "speaks"),
    [(at(5) + timedelta(minutes=59), False), (at(6), True), (at(20.98), True), (at(21), False)],
    ids=["05:59", "06:00", "20:59", "21:00"],
)
def test_she_speaks_only_in_the_active_hours_and_asks_nothing_outside_them(now, speaks):
    rig = Rig(forecast(now.hour + now.minute / 60, rain=[now.hour + 2]))
    rig.now = now

    notices = rig.poll()

    assert (len(notices), rig.read.calls) == ((1, 1) if speaks else (0, 0))


def test_the_active_hours_lead_and_threshold_are_the_configured_ones():
    rig = Rig(
        forecast(21.5, changes={24: Hour(at(24), DRY_CODE, 70, 0.0)}),
        active_from="07:00",
        active_until="23:00",
        lead_minutes=90,
        threshold=70,
    )
    rig.now = at(21.5)

    assert rig.poll() == [
        "Master, sekitar jam sebelas malam kemungkinan hujan di Cianjur, angkat jemuran ya."
    ]
    rig.now = at(6.5)
    assert (rig.poll(forecast(6.5, rain=[8])), rig.read.calls) == ([], 1)


def test_a_failed_read_is_never_said_and_logged_once_until_it_works_again(caplog):
    rig = Rig(WeatherError(UNREACHABLE))

    with caplog.at_level(logging.DEBUG, logger="waifu.rain_watch"):
        failures = rig.poll() + rig.poll() + rig.poll()
        told = rig.poll(forecast(14.0, rain=[16]))
        rig.poll(WeatherError(UNREACHABLE))

    assert failures == []
    assert told == [THREE_PM]
    assert rig.announced == [(THREE_PM, "neutral")]
    records = [record for record in caplog.records if record.name == "waifu.rain_watch"]
    levels = [record.levelno for record in records]
    assert levels[:3] == [logging.WARNING, logging.DEBUG, logging.DEBUG]
    assert levels[3] == logging.INFO  # it works again
    assert levels[-1] == logging.WARNING  # a new run of failures
    assert UNREACHABLE in records[0].getMessage()
    assert not any(record.exc_info for record in records)


def test_an_unexpected_failure_is_logged_with_its_traceback_and_never_said(caplog):
    rig = Rig(RuntimeError("the forecast broke"))

    with caplog.at_level(logging.WARNING, logger="waifu.rain_watch"):
        assert rig.poll() == []

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert str(record.exc_info[1]) == "the forecast broke"
    assert (rig.announced, rig.notifier.sent) == ([], [])


def test_a_voice_that_fails_never_stops_the_push(caplog):
    rig = Rig(forecast(14.0, rain=[16]))

    def broken(text: str, emotion: str) -> None:
        raise RuntimeError("the avatar is gone")

    rig.watcher._announce = broken

    with caplog.at_level(logging.ERROR, logger="waifu.rain_watch"):
        assert rig.poll() == [THREE_PM]

    assert rig.notifier.sent == [("Cyrene", THREE_PM, ("umbrella",))]
    assert "the avatar is gone" in caplog.text


def test_a_push_that_fails_is_logged_and_the_alert_still_said(caplog):
    rig = Rig(forecast(14.0, rain=[16]))
    rig.notifier.outcome = OSError("no internet")

    with caplog.at_level(logging.ERROR, logger="waifu.rain_watch"):
        assert rig.poll() == [THREE_PM]

    assert rig.announced == [(THREE_PM, "neutral")]
    assert "no internet" in caplog.text


def test_without_a_voice_or_a_notifier_it_still_keeps_track():
    watcher = RainWatcher(
        FakeForecasts(forecast(14.0, rain=[16])),
        RainAlertConfig(),
        announce=None,
        notifier=None,
        owner_call="Master",
        place="Cianjur",
        now=lambda: AFTERNOON,
    )

    assert watcher.poll_once() == [THREE_PM]
    assert watcher.poll_once() == []


# --- the thread --------------------------------------------------------------------------------


class SignallingForecasts(FakeForecasts):
    def __init__(self, reply: Forecast | Exception) -> None:
        super().__init__(reply)
        self.read = threading.Event()

    def __call__(self) -> Forecast:
        self.read.set()
        return super().__call__()


class ShortWaits:
    """Stands in for threading.Event.wait: records each timeout asked for and waits on the stop
    event a few milliseconds at most."""

    def __init__(self) -> None:
        self.asked: list[float] = []

    def __call__(self, stop: threading.Event, timeout: float) -> bool:
        self.asked.append(timeout)
        return stop.wait(0.005)


def watcher_threads() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "rain-watcher"]


def make_watcher(read: FakeForecasts, **options) -> RainWatcher:
    return RainWatcher(
        read,
        RainAlertConfig(),
        announce=None,
        notifier=None,
        owner_call="Master",
        place="Cianjur",
        now=lambda: AFTERNOON,
        **options,
    )


def test_the_daemon_thread_polls_at_once_and_stop_wakes_it_at_once():
    read = SignallingForecasts(forecast(14.0))
    watcher = make_watcher(read)

    watcher.start()
    try:
        assert read.read.wait(5)
        [thread] = watcher_threads()
        assert thread.daemon is True
    finally:
        stopping = time.monotonic()
        watcher.stop()

    assert time.monotonic() - stopping < 1  # although the next poll was 15 minutes away
    assert watcher_threads() == []
    assert read.calls == 1


def test_the_next_poll_comes_15_minutes_after_the_last_one_began():
    assert POLL_S == 900
    read = SignallingForecasts(WeatherError(UNREACHABLE))
    waits = ShortWaits()
    seconds = iter([1000.0, 1006.0] + [2000.0, 2000.0] * 100)  # the first read took 6 s
    watcher = make_watcher(read, clock=lambda: next(seconds), wait=waits)

    watcher.start()
    try:
        deadline = time.monotonic() + 5
        while len(waits.asked) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        watcher.stop()

    assert waits.asked[:2] == [894.0, 900.0]
    assert read.calls >= 2  # a failed read is tried again at the next poll


def test_starting_twice_runs_one_thread():
    watcher = make_watcher(FakeForecasts(forecast(14.0)), wait=ShortWaits())

    watcher.start()
    try:
        watcher.start()
        assert len(watcher_threads()) == 1
    finally:
        watcher.stop()

    assert watcher_threads() == []
