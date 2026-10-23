"""The tool that tells the five daily prayer times: `prayer_times`.

"Jadwal sholat hari ini?", "Maghrib jam berapa?", "jadwal sholat besok": the tool gives a day's
imsak, subuh, terbit, dzuhur, ashar, maghrib, and isya at Master's place, computed on this PC by
the Kemenag RI method (`prayer_times.py`) in WIB. The place is looked up only when the tool is
called (`PrayerPlace`). Its one argument may be left out, so it takes no strict slot, and the
spec is static English built once, so it stays byte-identical for the brain's lifetime and keeps
the prompt cache warm. It exists only while `prayer.enabled` is on, and it refuses in the turns
she starts herself (the morning greeting, the weekly game report, and the nightly journal
question), so she speaks of prayer only when Master asks. Guest mode leaves it open: the times
and the city hold nothing private, and a place known only by its coordinates is never named.
`run` never raises.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime

from waifu.brain.base import ToolSpec
from waifu.brain.persona import WEEKDAYS_ID
from waifu.briefing import BRIEFING_PROMPT
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.play_time import wib_now
from waifu.prayer import PrayerError, PrayerPlace
from waifu.prayer_times import prayer_times

logger = logging.getLogger(__name__)

PRAYER_TIMES = "prayer_times"
METHOD = "Kemenag RI"
MAX_DAYS_AWAY = 366
_TIMES = ("imsak", "subuh", "terbit", "dzuhur", "ashar", "maghrib", "isya")
_KEYS = frozenset({"date"})
# date.fromisoformat also reads 20261025 and 2026-W43-7; the tool takes only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# The turns she starts herself, each with what the tool answers in it.
_OWN_TURN_REFUSALS = {
    BRIEFING_PROMPT: "Waktu sholat hanya untuk permintaan Master, bukan untuk sapaan pagi.",
    WEEKLY_REPORT_PROMPT: (
        "Waktu sholat hanya untuk permintaan Master, bukan untuk laporan game mingguan."
    ),
    JOURNAL_PROMPT: (
        "Waktu sholat hanya untuk permintaan Master, bukan untuk pertanyaan jurnal malam."
    ),
}
_FAILED = "Waktu sholat tidak bisa dihitung saat ini."
_BAD_DATE = "'date' harus tanggal YYYY-MM-DD, misalnya 2026-10-25."
_TOO_FAR = "'date' harus dalam satu tahun dari hari ini."


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class PrayerTools:
    """A `ToolProvider` for the brain: a day's prayer times at Master's place."""

    def __init__(
        self,
        place: PrayerPlace,
        *,
        ihtiyath_minutes: int,
        now: Callable[[], datetime] = wib_now,
    ) -> None:
        """`now` is aware WIB time; its date is today."""
        self._place = place
        self._ihtiyath = ihtiyath_minutes
        self._now = now
        self._spec = _spec()
        self._refusal: str | None = None

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with its words as heard: a turn she starts herself, the morning
        greeting, the weekly game report, or the nightly journal question, gets no prayer
        times."""
        self._refusal = _OWN_TURN_REFUSALS.get(user_text)

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        if name != PRAYER_TIMES:
            return _error(f"Tool '{name}' bukan tool waktu sholat.")
        if self._refusal is not None:
            return _error(self._refusal)
        try:
            return _to_json(self._times(arguments)), False
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except PrayerError as exc:
            logger.warning("No prayer times: %s", exc)
            return _error(str(exc))
        except Exception:
            logger.exception("Prayer tool '%s' failed", name)
            return _error(_FAILED)

    def _times(self, arguments: object) -> dict[str, object]:
        day = _day(_arguments_mapping(arguments), self._now().date())
        place = self._place.locate()
        times = prayer_times(day, place.latitude, place.longitude, ihtiyath_minutes=self._ihtiyath)
        clocks = {name: getattr(times, name) for name in _TIMES}
        return {
            # A place known only by its coordinates goes unnamed: they may be Master's home.
            "place": place.place if self._place.named else None,
            "date": day.isoformat(),
            "day": WEEKDAYS_ID[day.weekday()],
            "timezone": "WIB",
            **{name: None if at is None else f"{at:%H:%M}" for name, at in clocks.items()},
            "method": METHOD,
            "ihtiyath_minutes": self._ihtiyath,
        }


def _spec() -> ToolSpec:
    """Built once, so every request sends the same bytes."""
    return ToolSpec(
        name=PRAYER_TIMES,
        description=(
            "The five daily prayer times (waktu sholat) at Master's place, computed on this PC "
            "by the Kemenag RI method (Subuh at -20°, Isya at -18°, Ashar with a shadow factor "
            "of 1) with Master's ihtiyath added, in WIB. Without `date` it gives today's; give "
            "`date` as YYYY-MM-DD for another day, for example tomorrow's from the date in "
            "Master's message. It returns `place` (null when the place has no name: then name "
            "no place), `date`, `day`, and `imsak`, `subuh`, `terbit` "
            "(sunrise, the end of Subuh's time), `dzuhur`, `ashar`, `maghrib`, and `isya` as "
            "HH:MM; a null time means the sun does not reach that angle there on that day. Use "
            "only these times, never times from memory, and say only the ones Master asked "
            "for, or all of them briefly when he asks for the schedule. Speak of prayer "
            "neutrally and respectfully: tell the times, never urge Master to pray, and never "
            "comment on whether or how he prays. Never call it in a greeting or anything you "
            "start yourself. After an error result, say its message briefly and do not call "
            "again in this turn."
        ),
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string"}},
            "additionalProperties": False,
        },
    )


def _day(arguments: Mapping[str, object], today: date) -> date:
    """The day asked for (null counts as left out, as local models send it): today by default,
    else a day within `MAX_DAYS_AWAY` of today."""
    value = arguments.get("date")
    if value is None:
        return today
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise _InvalidArguments(_BAD_DATE)
    try:
        day = date.fromisoformat(value)
    except ValueError:  # a day that does not exist, such as 2026-02-30
        raise _InvalidArguments(_BAD_DATE) from None
    if abs((day - today).days) > MAX_DAYS_AWAY:
        raise _InvalidArguments(_TOO_FAR)
    return day


def _arguments_mapping(arguments: object) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in _KEYS]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
