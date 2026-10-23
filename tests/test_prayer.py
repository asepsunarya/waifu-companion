"""Where the prayer times are for: `PrayerPlace` over a place lookup (the weather client's)."""

import threading

import pytest

from waifu.prayer import NO_PLACE, PrayerError, PrayerPlace
from waifu.weather import UNREACHABLE, Location, WeatherError

BANDUNG = Location("Bandung, Jawa Barat", -6.90389, 107.61861)


def test_it_gives_the_place_its_lookup_finds():
    assert PrayerPlace(lambda: BANDUNG).locate() == BANDUNG


def test_without_a_lookup_it_says_how_to_set_the_place():
    with pytest.raises(PrayerError) as raised:
        PrayerPlace(None).locate()

    assert str(raised.value) == NO_PLACE
    assert "briefing.city" in NO_PLACE and "prayer.latitude" in NO_PLACE


def test_a_lookup_that_fails_is_a_prayer_error_with_its_spoken_message():
    def unreachable() -> Location:
        raise WeatherError(UNREACHABLE)

    with pytest.raises(PrayerError) as raised:
        PrayerPlace(unreachable).locate()

    assert str(raised.value) == UNREACHABLE


def test_the_tool_and_the_watcher_never_look_the_place_up_at_the_same_moment():
    first_inside, release, second_inside = threading.Event(), threading.Event(), threading.Event()
    calls: list[int] = []

    def lookup() -> Location:
        calls.append(len(calls))
        if len(calls) == 1:
            first_inside.set()
            release.wait(5)
        else:
            second_inside.set()
        return BANDUNG

    place = PrayerPlace(lookup)
    first = threading.Thread(target=place.locate)
    second = threading.Thread(target=place.locate)
    first.start()
    assert first_inside.wait(5)
    second.start()

    assert not second_inside.wait(0.2)  # held while the first lookup runs
    release.set()
    first.join(5)
    second.join(5)
    assert second_inside.is_set()
