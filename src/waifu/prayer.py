"""Where the prayer times are for: `PrayerPlace`, shared by her tool and the reminder watcher.

The place is looked up only when it is first wanted, never at startup: `run` gives one
`PrayerPlace` over one weather client to both, so a city is geocoded once per run (a lookup
that failed is tried again at the next use). A lock keeps the two from looking it up at the
same moment. Every failure is a `PrayerError` whose Indonesian message is safe to say. A place
Master gave only as coordinates is not `named`: its name is the coordinates, which she never
tells.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from waifu.weather import Location, WeatherError

NO_PLACE = (
    "Lokasi waktu sholat belum diatur. Isi briefing.city, atau prayer.latitude dan "
    "prayer.longitude, di config.yaml."
)


class PrayerError(Exception):
    """Why there are no prayer times; Indonesian and safe to say."""


class PrayerPlace:
    def __init__(self, locate: Callable[[], Location] | None, *, named: bool = True) -> None:
        """`locate` finds the place (a WeatherError when it cannot); None when no place is
        configured. `named` is False when the place's name is only its coordinates."""
        self._locate = locate
        self._lock = threading.Lock()
        self.named = named

    def locate(self) -> Location:
        """The place; PrayerError when there is none or it cannot be found now."""
        if self._locate is None:
            raise PrayerError(NO_PLACE)
        with self._lock:
            try:
                return self._locate()
            except WeatherError as exc:
                raise PrayerError(str(exc)) from None
