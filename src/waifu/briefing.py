"""Cyrene's morning briefing: the day's data on request, and her own greeting once a day.

`daily_briefing` gives the model today's date, the weather where Master lives, today's
schedule entries, and, when the to-do list is on, the tasks due today and overdue, at any hour
("cuaca hari ini gimana?", "hari ini ada jadwal apa?"). A weather, schedule, or to-do failure
fills its problem field instead of failing the tool, so she can still tell the rest. The spec
carries no data, so it stays byte-identical for the brain's lifetime and keeps the prompt
cache warm.

`BriefingTimer` asks for her greeting once a day, the first time `waifu run` is on between
`briefing.time` and `briefing.until`: it hands `BRIEFING_PROMPT` to `request`, which has the
brain answer it between Master's utterances. The day goes into the state file before the
request, so a crash, a restart, or a second PC start the same morning never greets twice;
a request that fails is not retried until the next day. The file follows the rules of
`memory.py`: it is re-read at every check, replaced atomically, never created by reading,
and a file that is not a briefing state is moved aside, never overwritten.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable, Mapping
from datetime import date, datetime
from types import TracebackType

from waifu.brain.base import ToolSpec
from waifu.brain.persona import WEEKDAYS_ID
from waifu.config import BriefingConfig
from waifu.gacha.files import write_json_atomic
from waifu.schedule import Action, DeviceAction, Entry, Reminder, ScheduleStore
from waifu.todo import Task, TodoStore
from waifu.weather import Weather, WeatherClient, WeatherError

logger = logging.getLogger(__name__)

DAILY_BRIEFING = "daily_briefing"
FILE_VERSION = 1
STOP_TIMEOUT_S = 5.0
# Her own turn, not Master's words: it holds no look word (the screen tool stays shut) and
# does not start with a yes (the confirmation gate stays shut).
BRIEFING_PROMPT = (
    "Ini bukan ucapan Master: ini sapaan pagimu sendiri, yang kamu mulai sendiri sekali "
    "sehari. Sapa Master dengan hangat sesuai waktunya. Panggil daily_briefing, dan "
    "game_status kalau tool itu ada, lalu rangkum hari Master dalam 2-4 kalimat pendek: "
    "cuaca, jadwal dan pengingat hari ini, tugas yang tenggatnya hari ini atau sudah "
    "terlewat, game kalau ada, dan hal dari ingatanmu yang penting untuk hari ini, misalnya "
    "ulang tahun atau ujian. Jangan membacakan datanya satu per satu."
)

_NO_WEATHER = "cuaca belum diatur (isi briefing.city di config.yaml)"
_WEATHER_FAILED = "Cuaca tidak bisa dibaca saat ini."
_SCHEDULE_FAILED = "Jadwal tidak bisa dibaca saat ini."
_TASKS_FAILED = "Daftar tugas tidak bisa dibaca saat ini."
_FAILED = "Ringkasan hari ini tidak bisa dibuat saat ini."
_STATE_KEYS = {"version", "last_date"}
# date.fromisoformat also reads 20261013 and 2026-W42-2; the file holds only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class BriefingTools:
    """A `ToolProvider` for the brain: today's date, weather, schedule, and tasks in one call."""

    def __init__(
        self,
        *,
        weather: WeatherClient | None,
        schedule: ScheduleStore | None,
        todo: TodoStore | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        """Without `todo` (the to-do list is off) the result has no `tasks` field."""
        self._weather = weather
        self._schedule = schedule
        self._todo = todo
        self._now = now

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=DAILY_BRIEFING,
                description=(
                    "Get today's date and weekday, the weather where Master lives (now, "
                    "today's low and high in °C, today's highest chance of rain in percent, "
                    "and tomorrow morning), today's schedule entries (reminders, device "
                    "commands, routines) with their times, and, when Master keeps a to-do "
                    "list, Master's to-do tasks due today, the overdue ones, and how many tasks "
                    "are open. Use it for greetings and whenever Master asks how the weather "
                    "is, what is on today, or for a summary of the day. For the games, call "
                    "game_status too when you have it. Also mention what your memory holds for "
                    "today (a birthday, an exam). When weather_problem, schedule_problem, or "
                    "tasks_problem is set, say briefly that this part is unavailable and tell "
                    "the rest."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            )
        ]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == DAILY_BRIEFING:
                return self._briefing(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except Exception:
            logger.exception("Briefing tool '%s' failed", name)
            return _error(_FAILED)
        return _error(f"Tool '{name}' bukan tool ringkasan harian.")

    def _briefing(self, arguments: object) -> tuple[str, bool]:
        if not isinstance(arguments, Mapping) or arguments:
            raise _InvalidArguments("tool ini tidak menerima argumen; kirim objek kosong {}.")
        now = self._now()
        weather, weather_problem = self._weather_today()
        schedule_today, schedule_problem = self._schedule_today(now.date())
        briefing = {
            "date": f"{WEEKDAYS_ID[now.weekday()]}, {now:%d-%m-%Y}",
            "weather": weather,
            "weather_problem": weather_problem,
            "schedule_today": schedule_today,
            "schedule_problem": schedule_problem,
        }
        if self._todo is not None:
            briefing["tasks"], briefing["tasks_problem"] = _tasks_today(self._todo, now.date())
        return _to_json(briefing), False

    def _weather_today(self) -> tuple[dict[str, object] | None, str | None]:
        """(the weather, None), or (None, why there is none)."""
        if self._weather is None:
            return None, _NO_WEATHER
        try:
            return _weather_json(self._weather.today()), None
        except WeatherError as exc:
            logger.warning("No weather for the briefing: %s", exc)
            return None, str(exc)
        except Exception:
            logger.exception("The weather for the briefing failed")
            return None, _WEATHER_FAILED

    def _schedule_today(self, today: date) -> tuple[list[dict[str, str]], str | None]:
        """(today's entries, soonest first, None), or ([], why they could not be read)."""
        if self._schedule is None:
            return [], None
        try:
            entries = self._schedule.entries()
        except Exception:
            logger.exception("Could not read the schedule for the briefing")
            return [], _SCHEDULE_FAILED
        return [_entry_json(entry) for entry in entries if entry.at.date() == today], None


class BriefingTimer:
    """Asks for her greeting once a day inside the configured window, from a daemon thread."""

    def __init__(
        self,
        config: BriefingConfig,
        *,
        request: Callable[[str], None],
        now: Callable[[], datetime] = datetime.now,
        tick_seconds: float = 30,
    ) -> None:
        self._config = config
        self._path = config.state_path
        self._request = request
        self._now = now
        self._tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick_once(self) -> None:
        """Ask for the greeting when `time <= now < until` and none was asked for today.

        Today is saved first: when the save fails, nothing is asked (it raises, and the next
        tick tries again); a request that fails is logged and not retried today.
        """
        now = self._now()
        if not self._config.time <= now.time() < self._config.until:
            return
        today = now.date()
        if self._last_date(now) == today:
            return
        write_json_atomic(self._path, {"version": FILE_VERSION, "last_date": today.isoformat()})
        logger.info("Asking for the morning briefing of %s", today.isoformat())
        try:
            self._request(BRIEFING_PROMPT)
        except Exception:
            logger.exception("The morning briefing request failed; not asking again today")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="briefing", daemon=True)
        self._thread.start()
        logger.info(
            "Morning briefing between %s and %s, remembered in %s",
            f"{self._config.time:%H:%M}",
            f"{self._config.until:%H:%M}",
            self._path,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_S)
            self._thread = None

    def __enter__(self) -> BriefingTimer:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception:
                logger.exception("A morning briefing check failed; trying again at the next one")
            self._stop.wait(self._tick_seconds)

    def _last_date(self, now: datetime) -> date | None:
        """The day of the last greeting; None when there was none or the file was moved aside."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse_state(json.loads(self._path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return None
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem, now)
            return None

    def _set_aside(self, problem: ValueError, now: datetime) -> None:
        """Rename a file that is not a briefing state, so that no write ever replaces it."""
        name = f"{self._path.name}.corrupt-{now:%Y%m%d-%H%M%S}"
        target = self._path.with_name(name)
        counter = 2
        while target.exists():
            target = self._path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(self._path, target)
        except OSError as exc:
            logger.error(
                "Briefing state %s is not a briefing state (%s) and could not be moved aside: %s",
                self._path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Briefing state %s is not a briefing state (%s); moved it to %s and started anew",
            self._path,
            problem,
            target,
        )


def _parse_state(raw: object) -> date:
    """The day of the last greeting in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _STATE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_STATE_KEYS)}")
    version, last_date = raw["version"], raw["last_date"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if not isinstance(last_date, str) or not _ISO_DATE.fullmatch(last_date):
        raise ValueError("last_date must be a date written YYYY-MM-DD")
    return date.fromisoformat(last_date)  # ValueError for a day that does not exist


def _weather_json(weather: Weather) -> dict[str, object]:
    return {
        "place": weather.place,
        "condition": weather.condition,
        "temperature_c": weather.temperature_c,
        "min_c": weather.min_c,
        "max_c": weather.max_c,
        "rain_chance": weather.rain_chance,
        "tomorrow_morning": weather.tomorrow_morning,
    }


def _entry_json(entry: Entry) -> dict[str, str]:
    return {"time": f"{entry.at:%H:%M}", "what": _describe(entry.action)}


def _describe(action: Action) -> str:
    """What an entry does, in Indonesian: the reminder's text, `ac temp 26`, `rutinitas tidur`."""
    if isinstance(action, Reminder):
        return action.text
    if isinstance(action, DeviceAction):
        value = "" if action.value is None else f" {action.value}"
        return f"{action.device} {action.command}{value}"
    return f"rutinitas {action.name}"


def _tasks_today(todo: TodoStore, today: date) -> tuple[dict[str, object] | None, str | None]:
    """(the tasks due today, the overdue ones, and how many are open; None), or (None, why
    they could not be read)."""
    try:
        due_or_overdue = todo.due_or_overdue(today)
        open_count = len(todo.open_tasks())
    except Exception:
        logger.exception("Could not read the to-do list for the briefing")
        return None, _TASKS_FAILED
    tasks = {
        "due_today": [_task_json(task) for task in due_or_overdue if task.due == today],
        "overdue": [
            {**_task_json(task), "due": f"{task.due:%Y-%m-%d}"}
            for task in due_or_overdue
            if task.due != today
        ],
        "open_count": open_count,
    }
    return tasks, None


def _task_json(task: Task) -> dict[str, str]:
    return {"id": task.id, "text": task.text}


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
