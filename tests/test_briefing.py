"""The morning briefing: the daily_briefing tool and the once-a-day greeting timer.

No test reaches the internet or sleeps: the weather client is a fake with a `today()`
method, the to-do list a fake with TodoStore's two reads, every clock is a fake, and the
state and schedule files live in tmp_path.
"""

import json
import logging
import os
import threading
import time
from datetime import date, datetime

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.confirm import is_yes
from waifu.brain.music_tools import MusicTools
from waifu.brain.screen_tools import ScreenTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.briefing import BRIEFING_PROMPT, BriefingTimer, BriefingTools
from waifu.config import AssistantConfig, BriefingConfig
from waifu.japanese import Word
from waifu.schedule import (
    AlarmAction,
    DeviceAction,
    Reminder,
    RoutineAction,
    ScheduleStore,
)
from waifu.screen import ScreenImage
from waifu.todo import Task
from waifu.weather import Weather, WeatherError

NOW = datetime(2026, 10, 13, 7, 30)  # a Tuesday
TODAY = "2026-10-13"
CORRUPT_NAME = "briefing.json.corrupt-20261013-073000"
BANDUNG = Weather(
    place="Bandung",
    condition="cerah berawan",
    temperature_c=21.4,
    min_c=18.2,
    max_c=27.9,
    rain_chance=40,
    tomorrow_morning="hujan",
)
BANDUNG_JSON = {
    "place": "Bandung",
    "condition": "cerah berawan",
    "temperature_c": 21.4,
    "min_c": 18.2,
    "max_c": 27.9,
    "rain_chance": 40,
    "tomorrow_morning": "hujan",
}
MEDICINE = Reminder("Master, sudah jam tiga, waktunya minum obat!")
HER_NAMES = [AssistantConfig().name, *AssistantConfig().wake_words]
CREATED = datetime(2026, 10, 10, 20, 0)
# In the store's order: overdue first, then by due date, then the undated ones.
LAPORAN = Task("t1", "kerjakan laporan", date(2026, 10, 12), CREATED, None)
SUSU = Task("t2", "beli susu", date(2026, 10, 13), CREATED, None)
KADO = Task("t4", "beli kado Rina", date(2026, 10, 20), CREATED, None)
MOTOR = Task("t5", "cuci motor", None, CREATED, None)


class Clock:
    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current


class FakeWeather:
    """Stands in for WeatherClient: never reaches Open-Meteo."""

    def __init__(self, outcome: Weather | Exception = BANDUNG) -> None:
        self.outcome = outcome

    def today(self) -> Weather:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class UnreadableSchedule:
    """A schedule whose file cannot be read: a disk error, or a corrupt file that could not be
    moved aside."""

    def entries(self):
        raise OSError("the disk hiccupped")


class FakeTodo:
    """Stands in for TodoStore: its two reads over a list in memory, in the store's order,
    recording the days asked about."""

    def __init__(self, tasks: list[Task]) -> None:
        self.tasks = tasks
        self.days_asked: list[date] = []

    def open_tasks(self) -> list[Task]:
        return list(self.tasks)

    def due_or_overdue(self, today: date) -> list[Task]:
        self.days_asked.append(today)
        return [task for task in self.tasks if task.due is not None and task.due <= today]


class UnreadableTodo:
    """A to-do list whose file cannot be read."""

    def open_tasks(self):
        raise OSError("the to-do file is locked")

    def due_or_overdue(self, today):
        raise OSError("the to-do file is locked")


def make_store(tmp_path) -> ScheduleStore:
    return ScheduleStore(tmp_path / "schedule.json", now=Clock())


def make_tools(
    tmp_path, *, weather=None, schedule=None, todo=None, japanese=None, now=NOW
) -> BriefingTools:
    return BriefingTools(
        weather=weather if weather is not None else FakeWeather(),
        schedule=schedule if schedule is not None else make_store(tmp_path),
        todo=todo,
        japanese=japanese,
        now=Clock(now),
    )


TABERU = Word("w001", "たべる", "食べる", "makan", "ごはんを食べる。", "Makan nasi.")


class WordOfTheDay:
    """Stands in for today's Japanese word learned once: records the days asked about."""

    def __init__(self, outcome: Word | Exception = TABERU) -> None:
        self.outcome = outcome
        self.days_asked: list[date] = []

    def __call__(self, day: date) -> Word:
        self.days_asked.append(day)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def brief(tools: BriefingTools) -> tuple[dict, bool]:
    result, is_error = tools.run("daily_briefing", {})
    return json.loads(result), is_error


# --- spec ----------------------------------------------------------------------------------------


def test_one_tool_that_takes_no_arguments_so_it_needs_no_strict(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    assert spec.name == "daily_briefing"
    assert spec.input_schema == {"type": "object", "properties": {}, "additionalProperties": False}
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "daily_briefing"


def test_description_says_what_it_holds_and_when_to_use_it(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    for guidance in [
        "today's date and weekday",
        "the weather where Master lives",
        "today's schedule entries",
        "greetings",
        "how the weather is",
        "what is on today",
        "call game_status too",
        "what your memory holds for today",
    ]:
        assert guidance in spec.description


def test_the_description_leaves_a_look_back_at_the_day_to_daily_recap(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    assert "summary of the day" not in spec.description
    assert (
        'For a look back at what Master did ("rekap hari ini", "hari ini aku ngapain aja?", '
        "yesterday), use daily_recap instead when you have it."
    ) in spec.description


def test_the_description_says_the_tasks_come_too_and_what_a_problem_means(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    for guidance in [
        "to-do tasks due today",
        "overdue",
        "how many tasks are open",
        "tasks_problem",
    ]:
        assert guidance in spec.description


def test_the_spec_is_the_same_without_weather_or_schedule_for_prompt_caching(tmp_path):
    configured = make_tools(tmp_path).specs()
    bare = BriefingTools(weather=None, schedule=None, now=Clock()).specs()

    assert bare == configured


def test_the_spec_is_the_same_with_or_without_a_todo_list_for_prompt_caching(tmp_path):
    with_tasks = make_tools(tmp_path, todo=FakeTodo([LAPORAN, SUSU])).specs()

    assert with_tasks == make_tools(tmp_path).specs()


def test_the_spec_is_the_same_with_or_without_japanese_lessons_for_prompt_caching(tmp_path):
    with_word = make_tools(tmp_path, japanese=WordOfTheDay()).specs()

    assert with_word == make_tools(tmp_path).specs()


def test_the_description_says_how_to_end_with_the_word_of_the_day(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    for guidance in [
        "When word_of_the_day is set",
        "end with it in one short sentence",
        "the word in kana, then its Indonesian meaning",
        "without romaji or parentheses",
    ]:
        assert guidance in spec.description


# --- the briefing --------------------------------------------------------------------------------


def test_the_briefing_holds_the_date_the_weather_and_todays_schedule(tmp_path):
    store = make_store(tmp_path)
    store.add(datetime(2026, 10, 13, 23, 30), "none", DeviceAction("lampu_kamar", "off"))
    store.add(datetime(2026, 10, 13, 15, 0), "none", MEDICINE)
    store.add(datetime(2026, 10, 13, 22, 0), "none", DeviceAction("ac", "temp", 26))
    store.add(datetime(2026, 10, 13, 23, 0), "none", RoutineAction("tidur"))
    store.add(datetime(2026, 10, 14, 8, 0), "none", Reminder("Master, besok ujian!"))
    store.add(datetime(2026, 10, 14, 7, 0), "daily", Reminder("Master, bangun!"))

    briefing, is_error = brief(make_tools(tmp_path, schedule=store))

    assert briefing == {
        "date": "Selasa, 13-10-2026",
        "weather": BANDUNG_JSON,
        "weather_problem": None,
        "schedule_today": [
            {"time": "15:00", "what": MEDICINE.text},
            {"time": "22:00", "what": "ac temp 26"},
            {"time": "23:00", "what": "rutinitas tidur"},
            {"time": "23:30", "what": "lampu_kamar off"},
        ],
        "schedule_problem": None,
    }
    assert is_error is False


def test_the_briefing_names_todays_alarms_with_their_labels(tmp_path):
    store = make_store(tmp_path)
    store.add(datetime(2026, 10, 13, 15, 0), "none", AlarmAction("jemput adik"))
    store.add(datetime(2026, 10, 13, 22, 0), "none", AlarmAction())

    briefing, _ = brief(make_tools(tmp_path, schedule=store))

    assert briefing["schedule_today"] == [
        {"time": "15:00", "what": "alarm jemput adik"},
        {"time": "22:00", "what": "alarm"},
    ]


def test_the_result_is_compact_json_with_japanese_text_unescaped(tmp_path):
    tokyo = Weather(
        place="東京",
        condition="berawan",
        temperature_c=18.0,
        min_c=15.0,
        max_c=22.0,
        rain_chance=10,
        tomorrow_morning="cerah",
    )

    result, _ = make_tools(tmp_path, weather=FakeWeather(tokyo)).run("daily_briefing", {})

    assert '"place":"東京"' in result


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (datetime(2026, 10, 12, 7, 0), "Senin, 12-10-2026"),
        (datetime(2026, 10, 13, 7, 0), "Selasa, 13-10-2026"),
        (datetime(2026, 10, 14, 7, 0), "Rabu, 14-10-2026"),
        (datetime(2026, 10, 15, 7, 0), "Kamis, 15-10-2026"),
        (datetime(2026, 10, 16, 7, 0), "Jumat, 16-10-2026"),
        (datetime(2026, 10, 17, 7, 0), "Sabtu, 17-10-2026"),
        (datetime(2026, 10, 18, 7, 0), "Minggu, 18-10-2026"),
    ],
)
def test_the_date_names_the_weekday_in_indonesian(tmp_path, day, expected):
    briefing, _ = brief(make_tools(tmp_path, now=day))

    assert briefing["date"] == expected


def test_an_empty_day_has_an_empty_schedule(tmp_path):
    briefing, is_error = brief(make_tools(tmp_path))

    assert briefing["schedule_today"] == []
    assert briefing["schedule_problem"] is None
    assert is_error is False


def test_a_weather_error_is_told_as_the_weather_problem_and_the_rest_still_comes(tmp_path, caplog):
    store = make_store(tmp_path)
    store.add(datetime(2026, 10, 13, 15, 0), "none", MEDICINE)
    offline = WeatherError("Cuaca tidak bisa diambil: internet sedang putus.")

    with caplog.at_level(logging.WARNING, logger="waifu.briefing"):
        briefing, is_error = brief(
            make_tools(tmp_path, weather=FakeWeather(offline), schedule=store)
        )

    assert briefing["weather"] is None
    assert briefing["weather_problem"] == "Cuaca tidak bisa diambil: internet sedang putus."
    assert briefing["schedule_today"] == [{"time": "15:00", "what": MEDICINE.text}]
    assert briefing["date"] == "Selasa, 13-10-2026"
    assert is_error is False
    assert "internet sedang putus" in caplog.text


def test_without_a_weather_client_the_weather_is_not_set_up(tmp_path):
    tools = BriefingTools(weather=None, schedule=make_store(tmp_path), now=Clock())

    briefing, is_error = brief(tools)

    assert briefing["weather"] is None
    assert briefing["weather_problem"].startswith("cuaca belum diatur")
    assert "briefing.city" in briefing["weather_problem"]
    assert is_error is False


def test_an_unexpected_weather_failure_is_logged_and_becomes_the_weather_problem(tmp_path, caplog):
    with caplog.at_level(logging.ERROR, logger="waifu.briefing"):
        briefing, is_error = brief(
            make_tools(tmp_path, weather=FakeWeather(RuntimeError("the parser tripped")))
        )

    assert briefing["weather"] is None
    assert briefing["weather_problem"] == "Cuaca tidak bisa dibaca saat ini."
    assert briefing["schedule_today"] == []
    assert is_error is False
    assert "the parser tripped" in caplog.text


def test_a_schedule_that_cannot_be_read_is_told_as_the_schedule_problem(tmp_path, caplog):
    with caplog.at_level(logging.ERROR, logger="waifu.briefing"):
        briefing, is_error = brief(make_tools(tmp_path, schedule=UnreadableSchedule()))

    assert briefing["schedule_today"] == []
    assert briefing["schedule_problem"] == "Jadwal tidak bisa dibaca saat ini."
    assert briefing["weather"] == BANDUNG_JSON
    assert is_error is False
    assert "the disk hiccupped" in caplog.text


def test_without_a_schedule_the_day_has_no_entries_and_no_problem(tmp_path):
    tools = BriefingTools(weather=FakeWeather(), schedule=None, now=Clock())

    briefing, is_error = brief(tools)

    assert briefing["schedule_today"] == []
    assert briefing["schedule_problem"] is None
    assert briefing["weather"] == BANDUNG_JSON
    assert is_error is False


def test_with_a_todo_list_the_briefing_holds_the_tasks_due_today_the_overdue_and_the_count(
    tmp_path,
):
    todo = FakeTodo([LAPORAN, SUSU, KADO, MOTOR])

    briefing, is_error = brief(make_tools(tmp_path, todo=todo))

    assert briefing == {
        "date": "Selasa, 13-10-2026",
        "weather": BANDUNG_JSON,
        "weather_problem": None,
        "schedule_today": [],
        "schedule_problem": None,
        "tasks": {
            "due_today": [{"id": "t2", "text": "beli susu"}],
            "overdue": [{"id": "t1", "text": "kerjakan laporan", "due": "2026-10-12"}],
            "open_count": 4,
        },
        "tasks_problem": None,
    }
    assert is_error is False
    assert todo.days_asked == [date(2026, 10, 13)]


def test_the_tasks_follow_the_clock(tmp_path):
    todo = FakeTodo([LAPORAN, SUSU, KADO, MOTOR])

    briefing, _ = brief(make_tools(tmp_path, todo=todo, now=datetime(2026, 10, 20, 7, 0)))

    assert briefing["tasks"] == {
        "due_today": [{"id": "t4", "text": "beli kado Rina"}],
        "overdue": [
            {"id": "t1", "text": "kerjakan laporan", "due": "2026-10-12"},
            {"id": "t2", "text": "beli susu", "due": "2026-10-13"},
        ],
        "open_count": 4,
    }


@pytest.mark.parametrize(
    ("tasks", "open_count"), [([], 0), ([KADO, MOTOR], 2)], ids=["empty", "nothing due"]
)
def test_a_day_without_due_tasks_has_empty_lists_and_the_open_count(tmp_path, tasks, open_count):
    briefing, is_error = brief(make_tools(tmp_path, todo=FakeTodo(tasks)))

    assert briefing["tasks"] == {"due_today": [], "overdue": [], "open_count": open_count}
    assert briefing["tasks_problem"] is None
    assert is_error is False


def test_without_a_todo_list_the_briefing_has_no_tasks(tmp_path):
    briefing, _ = brief(make_tools(tmp_path))

    assert briefing.keys() == {
        "date",
        "weather",
        "weather_problem",
        "schedule_today",
        "schedule_problem",
    }


class GuestSwitch:
    """Stands in for guest mode's `is_on`, switched by the test."""

    def __init__(self, on: bool) -> None:
        self.on = on

    def __call__(self) -> bool:
        return self.on


def test_in_guest_mode_the_briefing_leaves_out_the_tasks_and_the_reminder_texts(tmp_path):
    store = make_store(tmp_path)
    store.add(datetime(2026, 10, 13, 15, 0), "none", MEDICINE)
    store.add(datetime(2026, 10, 13, 22, 0), "none", DeviceAction("ac", "temp", 26))
    store.add(datetime(2026, 10, 13, 23, 0), "none", RoutineAction("tidur"))
    todo = FakeTodo([LAPORAN, SUSU])
    tools = BriefingTools(
        weather=FakeWeather(),
        schedule=store,
        todo=todo,
        now=Clock(),
        guest_mode=GuestSwitch(on=True),
    )

    briefing, is_error = brief(tools)

    assert briefing == {
        "date": "Selasa, 13-10-2026",
        "weather": BANDUNG_JSON,
        "weather_problem": None,
        "schedule_today": [
            {"time": "22:00", "what": "ac temp 26"},
            {"time": "23:00", "what": "rutinitas tidur"},
        ],
        "schedule_problem": None,
    }
    assert is_error is False
    assert todo.days_asked == []  # the list is not even read


def test_in_guest_mode_the_briefing_leaves_out_the_alarms_as_it_does_the_reminders(tmp_path):
    store = make_store(tmp_path)
    store.add(datetime(2026, 10, 13, 15, 0), "none", AlarmAction("jemput adik"))
    store.add(datetime(2026, 10, 13, 22, 0), "none", DeviceAction("ac", "temp", 26))
    tools = BriefingTools(
        weather=FakeWeather(), schedule=store, now=Clock(), guest_mode=GuestSwitch(on=True)
    )

    briefing, _ = brief(tools)

    assert briefing["schedule_today"] == [{"time": "22:00", "what": "ac temp 26"}]


def test_once_guest_mode_is_off_the_briefing_is_whole_again(tmp_path):
    store = make_store(tmp_path)
    store.add(datetime(2026, 10, 13, 15, 0), "none", MEDICINE)
    guest = GuestSwitch(on=True)
    tools = BriefingTools(
        weather=FakeWeather(),
        schedule=store,
        todo=FakeTodo([SUSU]),
        now=Clock(),
        guest_mode=guest,
    )
    brief(tools)

    guest.on = False
    briefing, _ = brief(tools)

    assert briefing["schedule_today"] == [{"time": "15:00", "what": MEDICINE.text}]
    assert briefing["tasks"]["due_today"] == [{"id": "t2", "text": "beli susu"}]


def test_a_todo_list_that_cannot_be_read_is_told_as_the_tasks_problem(tmp_path, caplog):
    store = make_store(tmp_path)
    store.add(datetime(2026, 10, 13, 15, 0), "none", MEDICINE)

    with caplog.at_level(logging.ERROR, logger="waifu.briefing"):
        briefing, is_error = brief(make_tools(tmp_path, schedule=store, todo=UnreadableTodo()))

    assert briefing["tasks"] is None
    assert briefing["tasks_problem"] == "Daftar tugas tidak bisa dibaca saat ini."
    assert briefing["weather"] == BANDUNG_JSON
    assert briefing["schedule_today"] == [{"time": "15:00", "what": MEDICINE.text}]
    assert is_error is False
    assert "the to-do file is locked" in caplog.text


def test_with_japanese_lessons_her_morning_greeting_holds_the_word_of_its_day(tmp_path):
    word = WordOfTheDay()
    tools = make_tools(tmp_path, japanese=word)
    tools.begin_turn(BRIEFING_PROMPT)

    briefing, is_error = brief(tools)

    assert briefing["word_of_the_day"] == {"kana": "たべる", "kanji": "食べる", "meaning": "makan"}
    assert word.days_asked == [date(2026, 10, 13)]
    assert is_error is False


@pytest.mark.parametrize(
    "words", ["Cyrene, cuaca hari ini gimana?", "hari ini ada jadwal apa?", "rangkum hariku"]
)
def test_master_s_own_questions_get_no_word_and_learn_none(tmp_path, words):
    # The word belongs to the greeting; asked for, it comes from japanese_lesson. A weather
    # question at 15:00 must not end with it, nor count it learned unsaid.
    word = WordOfTheDay()
    tools = make_tools(tmp_path, japanese=word)
    tools.begin_turn(BRIEFING_PROMPT)
    brief(tools)
    tools.begin_turn(words)

    briefing, is_error = brief(tools)

    assert "word_of_the_day" not in briefing
    assert word.days_asked == [date(2026, 10, 13)]  # the greeting's alone
    assert is_error is False


def test_before_any_turn_the_briefing_has_no_word(tmp_path):
    word = WordOfTheDay()

    briefing, _ = brief(make_tools(tmp_path, japanese=word))

    assert "word_of_the_day" not in briefing
    assert word.days_asked == []


def test_without_japanese_lessons_the_briefing_has_no_word(tmp_path):
    briefing, _ = brief(make_tools(tmp_path))

    assert "word_of_the_day" not in briefing


def test_guest_mode_keeps_the_word_of_the_day(tmp_path):
    tools = BriefingTools(
        weather=FakeWeather(),
        schedule=make_store(tmp_path),
        japanese=WordOfTheDay(),
        now=Clock(),
        guest_mode=GuestSwitch(on=True),
    )
    tools.begin_turn(BRIEFING_PROMPT)

    briefing, _ = brief(tools)

    assert briefing["word_of_the_day"]["kana"] == "たべる"


def test_a_word_of_the_day_that_fails_is_left_out_and_logged(tmp_path, caplog):
    word = WordOfTheDay(OSError("the Japanese file is locked"))
    tools = make_tools(tmp_path, japanese=word)
    tools.begin_turn(BRIEFING_PROMPT)

    with caplog.at_level(logging.ERROR, logger="waifu.briefing"):
        briefing, is_error = brief(tools)

    assert briefing["word_of_the_day"] is None
    assert briefing["weather"] == BANDUNG_JSON
    assert is_error is False
    assert "the Japanese file is locked" in caplog.text


@pytest.mark.parametrize("arguments", [None, [], "hari ini", {"day": "besok"}])
def test_arguments_are_an_error_result(tmp_path, arguments):
    result, is_error = make_tools(tmp_path).run("daily_briefing", arguments)

    assert is_error is True
    assert json.loads(result) == {
        "ok": False,
        "message": "Argumen daily_briefing tidak valid: tool ini tidak menerima argumen; "
        "kirim objek kosong {}.",
    }


def test_another_tools_name_is_an_error_result(tmp_path):
    result, is_error = make_tools(tmp_path).run("game_status", {})

    assert is_error is True
    assert json.loads(result)["ok"] is False


def test_a_failing_clock_is_an_error_result_never_an_exception(tmp_path, caplog):
    def broken_clock() -> datetime:
        raise OSError("the clock stopped")

    tools = BriefingTools(weather=FakeWeather(), schedule=None, now=broken_clock)

    with caplog.at_level(logging.ERROR, logger="waifu.briefing"):
        result, is_error = tools.run("daily_briefing", {})

    assert is_error is True
    assert json.loads(result) == {
        "ok": False,
        "message": "Ringkasan hari ini tidak bisa dibuat saat ini.",
    }
    assert "the clock stopped" in caplog.text


# --- the prompt ----------------------------------------------------------------------------------


def test_the_prompt_says_it_is_her_own_greeting_and_names_the_tools():
    assert "bukan ucapan Master" in BRIEFING_PROMPT
    assert "daily_briefing" in BRIEFING_PROMPT
    assert "game_status" in BRIEFING_PROMPT
    assert "2-4 kalimat pendek" in BRIEFING_PROMPT
    assert "ingatanmu" in BRIEFING_PROMPT


def test_the_prompt_sums_up_the_tasks_due_today_and_overdue():
    assert "tugas yang tenggatnya hari ini atau sudah terlewat" in BRIEFING_PROMPT


def test_the_prompt_ends_with_the_japanese_word_of_the_day_when_there_is_one():
    assert "kata Jepang hari ini kalau ada" in BRIEFING_PROMPT


def test_the_greeting_stays_spoken_without_a_card():
    assert BRIEFING_PROMPT.endswith("Jawab tanpa kartu.")


def test_the_prompt_never_counts_as_master_saying_yes():
    # The brain hands the prompt to the confirmation gate as this turn's words: a yes here
    # could confirm a PC shutdown Master was asked about just before the greeting.
    assert is_yes(BRIEFING_PROMPT, HER_NAMES) is False


def test_the_prompt_never_lets_her_look_at_the_screen():
    captured: list[int] = []

    def capture(max_side: int) -> ScreenImage:
        captured.append(max_side)
        return ScreenImage(png=b"\x89PNG", width=1, height=1)

    screen = ScreenTools(capture=capture, names=HER_NAMES)

    screen.begin_turn(BRIEFING_PROMPT)
    result, is_error = screen.run("look_at_screen", {})
    assert is_error is True
    assert "tidak meminta melihat layar" in json.loads(result)["message"]
    assert captured == []

    # The same tools do look when Master's own words ask, so the check above is not vacuous.
    screen.begin_turn("Cyrene, lihat layarku")
    assert screen.run("look_at_screen", {})[1] is False
    assert captured == [1280]


def test_the_prompt_never_lets_her_open_a_song():
    # The greeting starts by itself, maybe while Master plays a fullscreen game: a browser
    # opening would take the focus from it. The opener only records (conftest also refuses
    # the real os.startfile).
    opened: list[str] = []
    music = MusicTools(service="youtube", names=HER_NAMES, links=True, startfile=opened.append)

    music.begin_turn(BRIEFING_PROMPT)
    result, is_error = music.run("play_youtube", {"query": "lofi"})
    assert is_error is True
    assert "tidak meminta lagu" in json.loads(result)["message"]
    assert opened == []

    # The same tools do open a song when Master's own words ask, so the check above is not
    # vacuous.
    music.begin_turn("Cyrene, putar lagu lofi")
    assert music.run("play_youtube", {"query": "lofi"})[1] is False
    assert opened == ["https://www.youtube.com/results?search_query=lofi"]


# --- the timer -----------------------------------------------------------------------------------


class Requests:
    """Stands in for `assistant.ask_aloud`: records each prompt, until when it may be said, and
    what the state file said at that moment."""

    def __init__(self, state_path, error: Exception | None = None) -> None:
        self.state_path = state_path
        self.error = error
        self.prompts: list[str] = []
        self.expiries: list[datetime] = []
        self.states_seen: list[object] = []

    def __call__(self, prompt: str, expires_at: datetime) -> None:
        self.prompts.append(prompt)
        self.expiries.append(expires_at)
        self.states_seen.append(json.loads(self.state_path.read_text(encoding="utf-8")))
        if self.error is not None:
            raise self.error


class Morning:
    """A timer over a state file in tmp_path, a fake clock, and a fake request."""

    def __init__(self, tmp_path, *, error: Exception | None = None, **config) -> None:
        self.config = BriefingConfig(state_path=tmp_path / "briefing.json", **config)
        self.state_path = self.config.state_path
        self.clock = Clock()
        self.requests = Requests(self.state_path, error)
        self.restart()

    def restart(self) -> None:
        """A new timer on the same state file, like `waifu run` started again."""
        self.timer = BriefingTimer(self.config, request=self.requests, now=self.clock)

    def tick_at(self, moment: datetime) -> None:
        self.clock.current = moment
        self.timer.tick_once()

    @property
    def state(self) -> object:
        return json.loads(self.state_path.read_text(encoding="utf-8"))


def test_the_first_tick_inside_the_window_asks_for_the_greeting_once(tmp_path):
    morning = Morning(tmp_path)

    morning.tick_at(datetime(2026, 10, 13, 7, 30))
    morning.tick_at(datetime(2026, 10, 13, 7, 30, 30))
    morning.tick_at(datetime(2026, 10, 13, 11, 59))

    assert morning.requests.prompts == [BRIEFING_PROMPT]
    assert morning.state == {"version": 1, "last_date": TODAY}


def test_today_is_written_before_the_request_so_a_crash_never_repeats_it(tmp_path):
    morning = Morning(tmp_path)

    morning.tick_at(NOW)

    assert morning.requests.states_seen == [{"version": 1, "last_date": TODAY}]


@pytest.mark.parametrize(
    ("moment", "asks"),
    [
        (datetime(2026, 10, 13, 0, 0), False),
        (datetime(2026, 10, 13, 6, 59, 59, 999999), False),
        (datetime(2026, 10, 13, 7, 0), True),
        (datetime(2026, 10, 13, 11, 59, 59, 999999), True),
        (datetime(2026, 10, 13, 12, 0), False),
        (datetime(2026, 10, 13, 23, 59), False),
    ],
    ids=[
        "midnight",
        "just before time",
        "exactly time",
        "just before until",
        "exactly until",
        "late",
    ],
)
def test_it_asks_only_from_time_up_to_but_not_at_until(tmp_path, moment, asks):
    morning = Morning(tmp_path)

    morning.tick_at(moment)

    assert morning.requests.prompts == ([BRIEFING_PROMPT] if asks else [])


def test_the_window_comes_from_the_config(tmp_path):
    morning = Morning(tmp_path, time="05:30", until="06:00")

    morning.tick_at(datetime(2026, 10, 13, 5, 29))
    assert morning.requests.prompts == []
    morning.tick_at(datetime(2026, 10, 13, 5, 30))
    assert morning.requests.prompts == [BRIEFING_PROMPT]


def test_the_greeting_may_be_said_until_the_window_ends_that_day(tmp_path):
    # Held by a phone chat into the afternoon, a morning greeting would be out of place.
    morning = Morning(tmp_path, until="10:00")

    morning.tick_at(NOW)

    assert morning.requests.expiries == [datetime(2026, 10, 13, 10, 0)]


def test_outside_the_window_the_state_file_is_never_created(tmp_path):
    morning = Morning(tmp_path)

    morning.tick_at(datetime(2026, 10, 13, 6, 0))
    morning.tick_at(datetime(2026, 10, 13, 13, 0))

    assert not morning.state_path.exists()


def test_a_restart_the_same_day_never_greets_again(tmp_path):
    morning = Morning(tmp_path)
    morning.tick_at(datetime(2026, 10, 13, 7, 30))

    morning.restart()
    morning.tick_at(datetime(2026, 10, 13, 9, 15))

    assert morning.requests.prompts == [BRIEFING_PROMPT]


def test_a_greeting_from_an_earlier_day_does_not_stop_todays(tmp_path):
    morning = Morning(tmp_path)
    morning.state_path.write_text('{"version": 1, "last_date": "2026-10-12"}', encoding="utf-8")

    morning.tick_at(NOW)

    assert morning.requests.prompts == [BRIEFING_PROMPT]
    assert morning.state == {"version": 1, "last_date": TODAY}


def test_a_state_saved_with_a_byte_order_mark_is_read(tmp_path):
    # Notepad may save the file with one.
    morning = Morning(tmp_path)
    morning.state_path.write_bytes(b'\xef\xbb\xbf{"version": 1, "last_date": "2026-10-13"}')

    morning.tick_at(NOW)

    assert morning.requests.prompts == []


def test_the_next_day_greets_again(tmp_path):
    morning = Morning(tmp_path)
    morning.tick_at(datetime(2026, 10, 13, 7, 30))
    morning.tick_at(datetime(2026, 10, 13, 13, 0))

    morning.tick_at(datetime(2026, 10, 14, 7, 0))
    morning.tick_at(datetime(2026, 10, 14, 7, 1))

    assert morning.requests.prompts == [BRIEFING_PROMPT, BRIEFING_PROMPT]
    assert morning.state == {"version": 1, "last_date": "2026-10-14"}


def test_a_request_that_fails_is_logged_and_not_retried_that_day(tmp_path, caplog):
    morning = Morning(tmp_path, error=RuntimeError("the voice loop is gone"))

    with caplog.at_level(logging.ERROR, logger="waifu.briefing"):
        morning.tick_at(datetime(2026, 10, 13, 7, 30))
        morning.tick_at(datetime(2026, 10, 13, 7, 31))
    assert morning.requests.prompts == [BRIEFING_PROMPT]
    assert "the voice loop is gone" in caplog.text

    morning.tick_at(datetime(2026, 10, 14, 7, 30))
    assert morning.requests.prompts == [BRIEFING_PROMPT, BRIEFING_PROMPT]


def test_a_state_that_cannot_be_saved_never_asks_and_keeps_the_previous_one(tmp_path, monkeypatch):
    # Asking without the day saved could greet again at every tick.
    morning = Morning(tmp_path)
    yesterday = b'{"version":1,"last_date":"2026-10-12"}'
    morning.state_path.write_bytes(yesterday)

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        morning.tick_at(NOW)

    assert morning.requests.prompts == []
    assert morning.state_path.read_bytes() == yesterday
    assert [path.name for path in tmp_path.iterdir()] == ["briefing.json"]


# --- a corrupt state -----------------------------------------------------------------------------


def state_bytes(raw: object) -> bytes:
    return json.dumps(raw).encode()


CORRUPT_STATES = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version": 1, "last_date": "\xff\xfe"}',
    "a list": b"[]",
    "a date alone": state_bytes(TODAY),
    "missing last_date": state_bytes({"version": 1}),
    "an extra key": state_bytes({"version": 1, "last_date": TODAY, "count": 1}),
    "another version": state_bytes({"version": 2, "last_date": TODAY}),
    "version true": state_bytes({"version": True, "last_date": TODAY}),
    "version text": state_bytes({"version": "1", "last_date": TODAY}),
    "last_date null": state_bytes({"version": 1, "last_date": None}),
    "last_date a number": state_bytes({"version": 1, "last_date": 20261013}),
    "last_date day first": state_bytes({"version": 1, "last_date": "13-10-2026"}),
    "last_date without dashes": state_bytes({"version": 1, "last_date": "20261013"}),
    "last_date a week date": state_bytes({"version": 1, "last_date": "2026-W42-2"}),
    "last_date with a time": state_bytes({"version": 1, "last_date": "2026-10-13T07:00"}),
    "last_date with a newline": state_bytes({"version": 1, "last_date": "2026-10-13\n"}),
    "last_date that does not exist": state_bytes({"version": 1, "last_date": "2026-02-30"}),
}


@pytest.mark.parametrize("content", CORRUPT_STATES.values(), ids=CORRUPT_STATES.keys())
def test_a_corrupt_state_is_moved_aside_logged_and_the_greeting_still_comes(
    tmp_path, caplog, content
):
    morning = Morning(tmp_path)
    morning.state_path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.briefing"):
        morning.tick_at(NOW)

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert CORRUPT_NAME in caplog.text
    assert morning.requests.prompts == [BRIEFING_PROMPT]
    assert morning.state == {"version": 1, "last_date": TODAY}


def test_a_corrupt_copy_with_the_same_stamp_gets_a_counter(tmp_path):
    morning = Morning(tmp_path)
    (tmp_path / CORRUPT_NAME).write_bytes(b"first")
    (tmp_path / f"{CORRUPT_NAME}-2").write_bytes(b"second")
    morning.state_path.write_bytes(b"third")

    morning.tick_at(NOW)

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"first"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_bytes() == b"second"
    assert (tmp_path / f"{CORRUPT_NAME}-3").read_bytes() == b"third"
    assert morning.requests.prompts == [BRIEFING_PROMPT]


def test_a_corrupt_state_is_only_read_inside_the_window(tmp_path):
    morning = Morning(tmp_path)
    morning.state_path.write_bytes(b"{not json")

    morning.tick_at(datetime(2026, 10, 13, 6, 0))

    assert morning.state_path.read_bytes() == b"{not json"
    assert not (tmp_path / "briefing.json.corrupt-20261013-060000").exists()


def test_nothing_is_written_or_asked_when_a_corrupt_state_cannot_be_moved(
    tmp_path, caplog, monkeypatch
):
    morning = Morning(tmp_path)
    morning.state_path.write_bytes(b"{not json")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(os, "rename", failing_rename)
    with (
        caplog.at_level(logging.ERROR, logger="waifu.briefing"),
        pytest.raises(OSError, match="file in use"),
    ):
        morning.tick_at(NOW)

    assert morning.state_path.read_bytes() == b"{not json"
    assert [path.name for path in tmp_path.iterdir()] == ["briefing.json"]
    assert morning.requests.prompts == []
    assert "could not be moved aside" in caplog.text


# --- the thread ----------------------------------------------------------------------------------


def test_the_thread_ticks_at_once_and_stop_wakes_it_at_once(tmp_path):
    asked = threading.Event()
    config = BriefingConfig(state_path=tmp_path / "briefing.json")
    timer = BriefingTimer(
        config, request=lambda prompt, expires_at: asked.set(), now=Clock(), tick_seconds=3600
    )

    with timer:
        assert asked.wait(5)
        stopping = time.monotonic()
    # Leaving the block stopped it although the next tick was an hour away.

    assert time.monotonic() - stopping < 1
    assert not any(thread.name == "briefing" for thread in threading.enumerate())


def test_a_failing_tick_is_logged_and_the_thread_goes_on(tmp_path, caplog):
    asked = threading.Event()
    hiccups = [OSError("the clock hiccupped")]

    def clock() -> datetime:
        if hiccups:
            raise hiccups.pop()
        return NOW

    config = BriefingConfig(state_path=tmp_path / "briefing.json")
    timer = BriefingTimer(
        config, request=lambda prompt, expires_at: asked.set(), now=clock, tick_seconds=0
    )

    with caplog.at_level(logging.ERROR, logger="waifu.briefing"), timer:
        assert asked.wait(5)

    assert "the clock hiccupped" in caplog.text
    assert not any(thread.name == "briefing" for thread in threading.enumerate())
