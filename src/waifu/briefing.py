"""Cyrene's morning briefing: the day's data on request, and her own greeting once a day.

`daily_briefing` gives the model today's date, the weather where Master lives, today's
schedule entries, and when the to-do list is on, the tasks due today and overdue, at any hour
("cuaca hari ini gimana?", "hari ini ada jadwal apa?"). When Master learns Japanese, her own
morning greeting (`BRIEFING_PROMPT`, heard by `begin_turn`) also gets the word of the day, and
only it: a weather question later that day must not end with a vocabulary line, nor count the
word learned unsaid (asked for, the word comes from `japanese_lesson`). A weather, schedule, or
to-do failure fills its problem field instead of failing the tool, so she can still tell the
rest. The spec carries no data, so it stays byte-identical for the brain's lifetime and keeps
the prompt cache warm. In guest mode it leaves out the tasks and the reminder entries (their
text is Master's), keeping the weather, the device and routine entries, and the word of the day.

`BriefingTimer` asks for her greeting once a day, the first time `waifu run` is on between
`briefing.time` and `briefing.until`: a `PromptTimer` that hands `BRIEFING_PROMPT` to
`request`, which has the brain answer it between Master's utterances, said only until
`briefing.until` that day. The day goes into the state file before the request, so a crash, a
restart, or a second PC start the same morning never greets twice; a request that fails is not
retried until the next day.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime

from waifu.brain.base import ToolSpec
from waifu.brain.persona import WEEKDAYS_ID
from waifu.config import BriefingConfig
from waifu.japanese import Word
from waifu.prompt_timer import PromptTimer
from waifu.schedule import Action, DeviceAction, Entry, Reminder, ScheduleStore
from waifu.todo import Task, TodoStore
from waifu.weather import Weather, WeatherClient, WeatherError

logger = logging.getLogger(__name__)

DAILY_BRIEFING = "daily_briefing"
# Her own turn, not Master's words: it holds no look word (the screen tool stays shut), no
# play verb or music word (the music tool stays shut), and does not start with a yes (the
# confirmation gate stays shut).
BRIEFING_PROMPT = (
    "Ini bukan ucapan Master: ini sapaan pagimu sendiri, yang kamu mulai sendiri sekali "
    "sehari. Sapa Master dengan hangat sesuai waktunya. Panggil daily_briefing, dan "
    "game_status kalau tool itu ada, lalu rangkum hari Master dalam 2-4 kalimat pendek: "
    "cuaca, jadwal dan pengingat hari ini, tugas yang tenggatnya hari ini atau sudah "
    "terlewat, game kalau ada, kata Jepang hari ini kalau ada, dan hal dari ingatanmu yang "
    "penting untuk hari ini, misalnya ulang tahun atau ujian. Jangan membacakan datanya satu "
    "per satu."
)

_NO_WEATHER = "cuaca belum diatur (isi briefing.city di config.yaml)"
_WEATHER_FAILED = "Cuaca tidak bisa dibaca saat ini."
_SCHEDULE_FAILED = "Jadwal tidak bisa dibaca saat ini."
_TASKS_FAILED = "Daftar tugas tidak bisa dibaca saat ini."
_FAILED = "Ringkasan hari ini tidak bisa dibuat saat ini."


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
        japanese: Callable[[date], Word] | None = None,
        now: Callable[[], datetime] = datetime.now,
        guest_mode: Callable[[], bool] | None = None,
    ) -> None:
        """Without `todo` (the to-do list is off) the result has no `tasks` field, nor while
        `guest_mode` (guest mode's `is_on`) says guests are around, when the reminders are
        left out of the schedule too. With `japanese` (today's word for a date, learned once)
        her morning greeting's result holds `word_of_the_day`, in guest mode too; any other
        turn's, or without it, no such field."""
        self._weather = weather
        self._schedule = schedule
        self._todo = todo
        self._japanese = japanese
        self._now = now
        self._guest_mode = guest_mode
        self._greeting = False

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
                    'is or what is on today. For a look back at what Master did ("rekap hari '
                    'ini", "hari ini aku ngapain aja?", yesterday), use daily_recap instead when '
                    "you have it. For the games, call game_status too when you have it. Also "
                    "mention what your memory holds for today (a birthday, an exam). When "
                    "weather_problem, schedule_problem, or tasks_problem is set, say briefly "
                    "that this part is unavailable and tell the rest. When word_of_the_day is "
                    "set (Master is learning Japanese), end with it in one short sentence: the "
                    "word in kana, then its Indonesian meaning, without romaji or parentheses."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            )
        ]

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with its words as heard: only her morning greeting gets the word of the
        day."""
        self._greeting = user_text == BRIEFING_PROMPT

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
        guests = self._guest_mode is not None and self._guest_mode()
        weather, weather_problem = self._weather_today()
        schedule_today, schedule_problem = self._schedule_today(now.date(), reminders=not guests)
        briefing = {
            "date": f"{WEEKDAYS_ID[now.weekday()]}, {now:%d-%m-%Y}",
            "weather": weather,
            "weather_problem": weather_problem,
            "schedule_today": schedule_today,
            "schedule_problem": schedule_problem,
        }
        if self._todo is not None and not guests:
            briefing["tasks"], briefing["tasks_problem"] = _tasks_today(self._todo, now.date())
        if self._japanese is not None and self._greeting:
            briefing["word_of_the_day"] = _word_of_the_day(self._japanese, now.date())
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

    def _schedule_today(
        self, today: date, *, reminders: bool
    ) -> tuple[list[dict[str, str]], str | None]:
        """(today's entries, soonest first, None), or ([], why they could not be read); the
        reminders only with `reminders`."""
        if self._schedule is None:
            return [], None
        try:
            entries = self._schedule.entries()
        except Exception:
            logger.exception("Could not read the schedule for the briefing")
            return [], _SCHEDULE_FAILED
        return [
            _entry_json(entry)
            for entry in entries
            if entry.at.date() == today and (reminders or not isinstance(entry.action, Reminder))
        ], None


class BriefingTimer(PromptTimer):
    """Asks for her greeting once a day inside the configured window, from a daemon thread."""

    def __init__(
        self,
        config: BriefingConfig,
        *,
        request: Callable[[str, datetime], None],
        now: Callable[[], datetime] = datetime.now,
        tick_seconds: float = 30,
    ) -> None:
        super().__init__(
            BRIEFING_PROMPT,
            due=lambda moment: config.time <= moment.time() < config.until,
            expires=lambda moment: datetime.combine(moment.date(), config.until, moment.tzinfo),
            state_path=config.state_path,
            request=request,
            what="morning briefing",
            when=f"between {config.time:%H:%M} and {config.until:%H:%M}",
            thread_name="briefing",
            now=now,
            tick_seconds=tick_seconds,
        )


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


def _word_of_the_day(japanese: Callable[[date], Word], today: date) -> dict[str, str | None] | None:
    """Today's Japanese word as she says it, or None when it could not be had (logged)."""
    try:
        word = japanese(today)
    except Exception:
        logger.exception("The Japanese word of the day for the briefing failed")
        return None
    return {"kana": word.kana, "kanji": word.kanji, "meaning": word.meaning}


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
