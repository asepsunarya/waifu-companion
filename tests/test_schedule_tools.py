import json
import logging
from datetime import datetime

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.base import ToolSpec
from waifu.brain.memory_tools import MemoryTools
from waifu.brain.schedule_tools import ScheduleTools
from waifu.brain.tools import build_tool_specs, run_tool, to_anthropic, to_ollama
from waifu.config import RoutineConfig, RoutineStep
from waifu.devices.media import MediaKeys
from waifu.devices.mock import MockDevice
from waifu.devices.registry import DeviceRegistry
from waifu.gacha.tools import GachaTools
from waifu.memory import MemoryStore
from waifu.schedule import (
    MAX_REMINDER_CHARS,
    AlarmAction,
    DeviceAction,
    Reminder,
    RoutineAction,
    ScheduleStore,
)

NOW = datetime(2026, 10, 13, 14, 30)  # a Tuesday
MEDICINE = "Master, sudah jam tiga, waktunya minum obat!"
AC_COMMANDS = ["on", "off", *(f"temp_{degrees}" for degrees in range(16, 31))]
PC_COMMANDS = ["wake", "shutdown", "restart", "cancel_shutdown", "sleep"]
MEDIA_COMMANDS = MediaKeys("musik", "Musik PC", []).commands()
GENERIC_FAILURE = {"ok": False, "message": "Jadwal atau rutinitas tidak bisa diproses saat ini."}
UNSUPPORTED_STRICT_KEYWORDS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
}


def step(device: str, command: str, value: int | None = None) -> RoutineStep:
    return RoutineStep(device=device, command=command, value=value)


def routine(*steps: tuple[str, str] | tuple[str, str, int], description: str = "") -> RoutineConfig:
    return RoutineConfig(description=description, steps=[step(*s) for s in steps])


ROUTINES = {
    "tidur": routine(("lampu_kamar", "off"), ("ac", "temp", 26), description="Mode tidur"),
    "pagi": routine(("lampu_kamar", "on")),
}
# Mode tidur with its last step three hours later: the AC off at 17:30 when run at NOW.
DELAYED_TIDUR = RoutineConfig(
    description="Mode tidur",
    steps=[
        step("lampu_kamar", "off"),
        step("ac", "temp", 26),
        RoutineStep(device="ac", command="off", after_minutes=180),
    ],
)


def make_house() -> tuple[DeviceRegistry, dict[str, MockDevice]]:
    devices = {
        "lampu_kamar": MockDevice("lampu_kamar", "Lampu kamar", kind="light"),
        "ac": MockDevice("ac", "AC Samsung", commands=AC_COMMANDS, kind="ir"),
        "komputer": MockDevice("komputer", "Komputer", commands=PC_COMMANDS, kind="pc"),
    }
    return DeviceRegistry(list(devices.values())), devices


def make_store(tmp_path, **kwargs) -> ScheduleStore:
    return ScheduleStore(tmp_path / "schedule.json", now=lambda: NOW, **kwargs)


def make_tools(
    tmp_path, routines=ROUTINES, **store_kwargs
) -> tuple[ScheduleTools, ScheduleStore, dict[str, MockDevice]]:
    registry, devices = make_house()
    store = make_store(tmp_path, **store_kwargs)
    tools = ScheduleTools(store, registry=registry, routines=routines, now=lambda: NOW)
    return tools, store, devices


def spec(tools: ScheduleTools, name: str) -> ToolSpec:
    return next(s for s in tools.specs() if s.name == name)


def run(tools: ScheduleTools, name: str, arguments: object) -> tuple[dict, bool]:
    result, is_error = tools.run(name, arguments)
    return json.loads(result), is_error


def actions(store: ScheduleStore) -> list[tuple[str, datetime, str, object]]:
    return [(entry.id, entry.at, entry.repeat, entry.action) for entry in store.entries()]


def no_device_ran(devices: dict[str, MockDevice]) -> bool:
    return all(device.calls == [] for device in devices.values())


def schema_nodes(node: object):
    if isinstance(node, dict):
        yield node
        for child in node.values():
            yield from schema_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from schema_nodes(child)


class Unreachable:
    """HoYoLAB and the pull history: listing the game tools must not reach them."""

    def status(self, games):
        raise AssertionError("HoYoLAB must not be reached")

    def report(self, game_id, *, refresh=True, full=False):
        raise AssertionError("the pull history must not be read")


class UnreadableCommands(MockDevice):
    """An IR device whose learned codes cannot be read right now."""

    def commands(self) -> list[str]:
        raise OSError("ir_codes.json is locked")


# --- specs ---------------------------------------------------------------------------------


def test_four_tools_when_the_owner_wrote_routines(tmp_path):
    tools, _, _ = make_tools(tmp_path)

    assert [s.name for s in tools.specs()] == [
        "schedule",
        "list_schedules",
        "cancel_schedule",
        "run_routine",
    ]


def test_no_run_routine_tool_without_routines(tmp_path):
    tools, _, _ = make_tools(tmp_path, routines={})

    assert [s.name for s in tools.specs()] == ["schedule", "list_schedules", "cancel_schedule"]


def test_schedule_schema_offers_repeats_schedulable_devices_and_routines(tmp_path):
    tools, _, _ = make_tools(tmp_path)

    assert spec(tools, "schedule").input_schema == {
        "type": "object",
        "properties": {
            "at": {"type": "string"},
            "repeat": {
                "type": "string",
                "enum": ["none", "daily", "weekdays", "weekends", "weekly", "monthly", "yearly"],
            },
            "reminder": {"type": "string"},
            # The PC is never scheduled, so it is not offered.
            "device": {"type": "string", "enum": ["lampu_kamar", "ac"]},
            "command": {"type": "string"},
            "value": {"type": "integer"},
            "routine": {"type": "string", "enum": ["tidur", "pagi"]},
        },
        "required": ["at"],
        "additionalProperties": False,
    }


def test_schedule_leaves_device_and_routine_open_when_there_are_none_to_offer(tmp_path):
    # Strict tool use rejects an empty enum.
    registry = DeviceRegistry([MockDevice("komputer", "Komputer", commands=PC_COMMANDS, kind="pc")])
    tools = ScheduleTools(make_store(tmp_path), registry=registry, routines={})

    properties = spec(tools, "schedule").input_schema["properties"]

    assert properties["device"] == {"type": "string"}
    assert properties["routine"] == {"type": "string"}


def test_list_cancel_and_run_routine_schemas(tmp_path):
    tools, _, _ = make_tools(tmp_path)

    assert spec(tools, "list_schedules").input_schema == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert spec(tools, "cancel_schedule").input_schema == {
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": False,
    }
    assert spec(tools, "run_routine").input_schema == {
        "type": "object",
        "properties": {"name": {"type": "string", "enum": ["tidur", "pagi"]}},
        "required": ["name"],
        "additionalProperties": False,
    }


@pytest.mark.parametrize("routines", [ROUTINES, {}], ids=["with routines", "without routines"])
def test_schemas_stay_within_strict_tool_use_limits(tmp_path, routines):
    tools, _, _ = make_tools(tmp_path, routines=routines)

    for tool_spec in tools.specs():
        for node in schema_nodes(tool_spec.input_schema):
            assert not UNSUPPORTED_STRICT_KEYWORDS & node.keys()
            if node.get("type") == "object":
                assert node["additionalProperties"] is False


def test_schedule_says_how_to_write_the_time_the_reminder_and_the_action(tmp_path):
    tools, _, _ = make_tools(tmp_path)
    description = spec(tools, "schedule").description

    for guidance in [
        "time stamp",
        "relative",
        "YYYY-MM-DD HH:MM",
        "already passed today",
        "tomorrow",
        "weekdays (Monday-Friday)",
        "weekends (Saturday-Sunday)",
        "exactly one",
        "what you will say to Master",
        "Indonesian",
        "short",
        f"at most {MAX_REMINDER_CHARS} characters",
        "control_device",
        "silently",
        "run_routine",
        "PC",
        "voice",
    ]:
        assert guidance in description


def test_schedule_explains_weekly_monthly_and_yearly_entries_birthdays_and_bills(tmp_path):
    tools, _, _ = make_tools(tmp_path)
    description = spec(tools, "schedule").description

    for guidance in [
        "weekly (every week on the weekday of `at`)",
        "monthly (every month on the day of the month of `at`",
        "on the last day of a month without it",
        "yearly (every year on the month and day of `at`",
        "29 February on 28 February in other years",
        "an `at` that has passed is fine",
        "a yearly one ignores the year",
        "('tiap tanggal 31' in November), pass the next month that has it",
        "never the month's last day",
        # 2027-02-29 is refused as a date and 2027-02-28 would store the 28th for good.
        "for 29 February pass a leap year's date, such as 2028-02-29 (the year is ignored)",
        "'ingatkan ulang tahun Ibu tiap 5 Mei'",
        "'bayar listrik tiap tanggal 20'",
        # The 08:00 default covers bills too, as the README and the CHANGELOG promise.
        "schedule both at 08:00 unless Master says a time",
        "for a weekly entry, say the `weekday` of the result",
    ]:
        assert guidance in description


# Written in lowercase, the word still opens the example sentence with a capital.
@pytest.mark.parametrize("owner_call", ["Sayang", "sayang"])
def test_the_reminder_example_calls_the_owner_what_she_calls_them(tmp_path, owner_call):
    store = make_store(tmp_path)
    registry, _ = make_house()
    default = spec(ScheduleTools(store, registry=registry, routines={}), "schedule")
    sayang = spec(
        ScheduleTools(store, registry=registry, routines={}, owner_call=owner_call), "schedule"
    )

    assert "('Master, sudah jam tiga, waktunya minum obat!')" in default.description
    assert "('Sayang, sudah jam tiga, waktunya minum obat!')" in sayang.description
    assert "'Master, hari ini ulang tahun Ibu!'" in default.description
    assert "'Sayang, hari ini ulang tahun Ibu!'" in sayang.description
    assert "'Master, hari ini bayar listrik!'" in default.description
    assert "'Sayang, hari ini bayar listrik!'" in sayang.description
    assert "'Master," not in sayang.description
    assert "what you will say to Master" in sayang.description  # her instructions keep Master


def test_schedule_says_never_to_cut_the_power_of_the_pc_through_its_plug(tmp_path):
    # A plug that powers the PC is an ordinary plug to the code; switching it off while
    # Windows runs is pulling the cable, so the model must never schedule it or offer it
    # after the PC itself was refused.
    tools, _, _ = make_tools(tmp_path)
    description = spec(tools, "schedule").description

    assert (
        "Never schedule turning off or toggling a plug that powers the PC (such as a "
        "'Colokan PC'): it cuts the power while Windows runs, like pulling the cable. Never "
        "offer it in place of a refused PC command."
    ) in description


def test_schedule_says_to_use_stop_or_volume_instead_of_the_media_toggles(tmp_path):
    tools, _, _ = make_tools(tmp_path)
    description = spec(tools, "schedule").description

    assert (
        "For music, schedule `stop` or `volume`, never `play_pause` or `mute`: both toggle, so "
        "they could start the music or unmute the PC while Master sleeps."
    ) in description


def test_schedule_sends_waking_master_to_the_alarm_tool(tmp_path):
    tools, _, _ = make_tools(tmp_path)

    assert (
        "To wake Master up ('bangunkan aku jam 6', 'alarm jam 5'), use the alarm tool when you "
        "have it, not a reminder: an alarm rings again and again until he is awake."
    ) in spec(tools, "schedule").description


def test_schedule_sends_a_reminder_for_a_game_s_start_to_app_reminder(tmp_path):
    tools, _, _ = make_tools(tmp_path)

    assert (
        "For a reminder tied to Master opening a game rather than a clock time ('pas buka HSR "
        "nanti, ingetin aku farming relic'), use the app_reminder tool when you have it, never "
        "a guessed time."
    ) in spec(tools, "schedule").description


def test_list_schedules_says_where_the_reminders_for_a_game_s_start_are(tmp_path):
    tools, _, _ = make_tools(tmp_path)

    assert (
        "Reminders for when Master opens a game are not here: list them with the app_reminder "
        "tool when you have it."
    ) in spec(tools, "list_schedules").description


def test_schedule_without_routines_does_not_point_to_a_missing_tool(tmp_path):
    tools, _, _ = make_tools(tmp_path, routines={})

    assert "run_routine" not in spec(tools, "schedule").description


def test_list_and_cancel_say_to_check_the_id_first(tmp_path):
    tools, _, _ = make_tools(tmp_path)

    assert "id" in spec(tools, "list_schedules").description
    cancel = spec(tools, "cancel_schedule").description
    assert "list_schedules" in cancel
    assert "not sure" in cancel


def test_list_schedules_says_to_pick_the_entries_of_a_period_from_the_list(tmp_path):
    # No filter parameter: the list is short, and she counts the repeating entries herself.
    tools, _, _ = make_tools(tmp_path)
    description = spec(tools, "list_schedules").description

    for guidance in [
        "a weekly one with `weekday`, the weekday it repeats on",
        "`day`, the day of the month it repeats on",
        "'jadwal bulan ini apa?'",
        "'ulang tahun siapa bulan ini?'",
        "pick the entries in it yourself, repeating ones included",
        "a yearly entry comes every year on the month and day of its next time",
        "a weekly one every week on its `weekday`",
    ]:
        assert guidance in description


def test_run_routine_lists_every_routine_with_its_steps(tmp_path):
    tools, _, _ = make_tools(tmp_path)
    description = spec(tools, "run_routine").description

    assert "tidur: Mode tidur (lampu_kamar off, ac temp 26)" in description
    assert "pagi: lampu_kamar on" in description
    assert "schedule" in description


def test_specs_stay_identical_while_entries_change_for_prompt_caching(tmp_path):
    tools, _, _ = make_tools(tmp_path)
    first = tools.specs()

    tools.run("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE})
    tools.run("run_routine", {"name": "pagi"})
    tools.run("cancel_schedule", {"id": "j1"})

    assert tools.specs() == first


def test_schedule_tools_convert_to_claude_tools_and_ollama_tools(tmp_path):
    tools, _, _ = make_tools(tmp_path)

    for tool_spec in tools.specs():
        claude_tool = to_anthropic(tool_spec)
        # Strict, except the list that takes no arguments.
        assert claude_tool["strict"] is (tool_spec.name != "list_schedules")
        assert (
            claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
        )

        ollama_tool = ollama.Tool.model_validate(to_ollama(tool_spec))
        assert ollama_tool.function.parameters.required == tool_spec.input_schema.get("required")


# --- schedule ------------------------------------------------------------------------------


def test_schedule_saves_a_reminder_and_answers_with_its_id_and_time(tmp_path):
    tools, store, _ = make_tools(tmp_path)

    result, is_error = tools.run("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE})

    assert result == '{"ok":true,"id":"j1","at":"2026-10-13 15:00","repeat":"none"}'
    assert is_error is False
    assert actions(store) == [("j1", datetime(2026, 10, 13, 15, 0), "none", Reminder(MEDICINE))]


def test_schedule_accepts_the_current_minute(tmp_path):
    # "Satu menit lagi" said at 14:29:50 reaches the tool after the minute changed.
    tools, store, _ = make_tools(tmp_path)

    data, is_error = run(tools, "schedule", {"at": "2026-10-13 14:30", "reminder": MEDICINE})

    assert (data, is_error) == (
        {"ok": True, "id": "j1", "at": "2026-10-13 14:30", "repeat": "none"},
        False,
    )


def test_schedule_saves_a_reminder_without_the_emotion_tag_the_persona_asks_for(tmp_path):
    # The persona starts every text for Master with a tag; the reminder is said and pushed to
    # the phone later, where a tag must never show.
    tools, store, _ = make_tools(tmp_path)

    data, is_error = run(
        tools, "schedule", {"at": "2026-10-13 15:00", "reminder": f"[senang] {MEDICINE}"}
    )
    tag_only, tag_only_is_error = run(
        tools, "schedule", {"at": "2026-10-13 16:00", "reminder": "[senang]"}
    )

    assert (data["ok"], is_error) == (True, False)
    assert actions(store) == [("j1", datetime(2026, 10, 13, 15, 0), "none", Reminder(MEDICINE))]
    assert tag_only_is_error and "kosong" in tag_only["message"]


@pytest.mark.parametrize(
    ("repeat", "first"),
    [
        ("daily", datetime(2026, 10, 17, 7, 0)),
        ("weekdays", datetime(2026, 10, 19, 7, 0)),
        ("weekends", datetime(2026, 10, 17, 7, 0)),
    ],
)
def test_a_repeating_schedule_answers_with_the_first_time_it_fires(tmp_path, repeat, first):
    tools, store, _ = make_tools(tmp_path)

    # 2026-10-17 is a Saturday: a weekday entry starts on Monday.
    data, is_error = run(
        tools, "schedule", {"at": "2026-10-17 07:00", "repeat": repeat, "reminder": "Bangun!"}
    )

    assert (data, is_error) == (
        {"ok": True, "id": "j1", "at": f"{first:%Y-%m-%d %H:%M}", "repeat": repeat},
        False,
    )
    assert actions(store) == [("j1", first, repeat, Reminder("Bangun!"))]


@pytest.mark.parametrize(
    ("arguments", "result"),
    [
        # "Ingatkan ulang tahun Ibu tiap 5 Mei": this year's has passed.
        (
            {"at": "2026-05-05 08:00", "repeat": "yearly", "reminder": "Hari ini ulang tahun Ibu!"},
            '{"ok":true,"id":"j1","at":"2027-05-05 08:00","repeat":"yearly","day":5}',
        ),
        (
            {"at": "2026-10-31 09:00", "repeat": "monthly", "reminder": "Bayar listrik!"},
            '{"ok":true,"id":"j1","at":"2026-10-31 09:00","repeat":"monthly","day":31}',
        ),
        # The weekday is named for her, so she never works it out from the date.
        (
            {"at": "2026-10-12 07:00", "repeat": "weekly", "reminder": "Buang sampah!"},
            '{"ok":true,"id":"j1","at":"2026-10-19 07:00","repeat":"weekly","weekday":"Senin"}',
        ),
        # "Tiap Senin" passed as a Tuesday by a miscounted date: the result shows it.
        (
            {"at": "2026-10-20 07:00", "repeat": "weekly", "reminder": "Buang sampah!"},
            '{"ok":true,"id":"j1","at":"2026-10-20 07:00","repeat":"weekly","weekday":"Selasa"}',
        ),
    ],
    ids=["yearly", "monthly", "weekly", "weekly on a miscounted day"],
)
def test_a_calendar_schedule_answers_with_its_first_time_and_the_day_it_repeats_on(
    tmp_path, arguments, result
):
    tools, _, _ = make_tools(tmp_path)

    assert tools.run("schedule", arguments) == (result, False)


def test_null_optional_arguments_count_as_left_out(tmp_path):
    # Local models may send null for an optional argument instead of leaving it out.
    tools, store, _ = make_tools(tmp_path)
    arguments = {
        "at": "2026-10-13 15:00",
        "repeat": None,
        "reminder": MEDICINE,
        "device": None,
        "command": None,
        "value": None,
        "routine": None,
    }

    data, is_error = run(tools, "schedule", arguments)

    assert (data, is_error) == (
        {"ok": True, "id": "j1", "at": "2026-10-13 15:00", "repeat": "none"},
        False,
    )
    assert actions(store) == [("j1", datetime(2026, 10, 13, 15, 0), "none", Reminder(MEDICINE))]


@pytest.mark.parametrize(
    ("arguments", "action"),
    [
        ({"device": "ac", "command": "off"}, DeviceAction("ac", "off", None)),
        ({"device": "ac", "command": "temp", "value": 26}, DeviceAction("ac", "temp", 26)),
        # "on" with a value still resolves to the bare command, as registry.execute does.
        (
            {"device": "lampu_kamar", "command": "on", "value": 5},
            DeviceAction("lampu_kamar", "on", 5),
        ),
    ],
)
def test_schedule_saves_a_device_command_without_running_it(tmp_path, arguments, action):
    tools, store, devices = make_tools(tmp_path)

    data, is_error = run(tools, "schedule", {"at": "2026-10-14 02:00", **arguments})

    assert (data, is_error) == (
        {"ok": True, "id": "j1", "at": "2026-10-14 02:00", "repeat": "none"},
        False,
    )
    assert actions(store) == [("j1", datetime(2026, 10, 14, 2, 0), "none", action)]
    assert no_device_ran(devices)


def test_schedule_saves_a_routine_without_running_it(tmp_path):
    tools, store, devices = make_tools(tmp_path)

    data, is_error = run(
        tools, "schedule", {"at": "2026-10-13 23:00", "repeat": "daily", "routine": "tidur"}
    )

    assert (data, is_error) == (
        {"ok": True, "id": "j1", "at": "2026-10-13 23:00", "repeat": "daily"},
        False,
    )
    assert actions(store) == [
        ("j1", datetime(2026, 10, 13, 23, 0), "daily", RoutineAction("tidur"))
    ]
    assert no_device_ran(devices)


@pytest.mark.parametrize("command", ["shutdown", "sleep", "restart", "wake"])
def test_schedule_refuses_the_pc_and_says_it_is_only_turned_off_by_voice(tmp_path, caplog, command):
    tools, store, devices = make_tools(tmp_path)

    with caplog.at_level(logging.WARNING, logger="waifu.brain.schedule_tools"):
        data, is_error = run(
            tools, "schedule", {"at": "2026-10-14 02:00", "device": "komputer", "command": command}
        )

    assert (data, is_error) == (
        {
            "ok": False,
            "message": "Komputer adalah PC, dan PC tidak pernah dijadwalkan: PC hanya dimatikan, "
            "di-restart, atau ditidurkan lewat perintah suara langsung. Jangan menggantinya "
            "dengan mematikan colokan yang mengalirkan listrik ke PC: itu sama dengan "
            "mencabut kabelnya.",
        },
        True,
    )
    assert store.entries() == []
    assert not store.path.exists()
    assert devices["komputer"].calls == []
    assert "komputer" in caplog.text


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            {"device": "kulkas", "command": "on"},
            "Perangkat 'kulkas' tidak dikenal. Perangkat yang bisa dijadwalkan: lampu_kamar, ac.",
        ),
        (
            {"device": "ac", "command": "temp", "value": 99},
            "AC Samsung tidak punya perintah 'temp' dengan nilai 99. Perintah yang tersedia: "
            f"{', '.join(AC_COMMANDS)}.",
        ),
        (
            {"device": "lampu_kamar", "command": "explode"},
            "Lampu kamar tidak punya perintah 'explode'. Perintah yang tersedia: on, off.",
        ),
        (
            {"routine": "santai"},
            "Tidak ada rutinitas bernama 'santai'. Rutinitas yang ada: tidur, pagi.",
        ),
    ],
    ids=["unknown device", "unknown value", "unknown command", "unknown routine"],
)
def test_schedule_refuses_what_could_never_run(tmp_path, arguments, message):
    tools, store, devices = make_tools(tmp_path)

    data, is_error = run(tools, "schedule", {"at": "2026-10-14 02:00", **arguments})

    assert (data, is_error) == ({"ok": False, "message": message}, True)
    assert not store.path.exists()
    assert no_device_ran(devices)


def make_media_tools(tmp_path) -> tuple[ScheduleTools, ScheduleStore, dict[str, MockDevice]]:
    # Mocks of the kinds whose value ranges the kind fixes, as in a dry run.
    devices = {
        "musik": MockDevice("musik", "Musik PC", commands=MEDIA_COMMANDS, kind="media"),
        "lampu": MockDevice("lampu", "Lampu", commands=["on", "off", "brightness"], kind="light"),
    }
    store = make_store(tmp_path)
    registry = DeviceRegistry(list(devices.values()))
    return ScheduleTools(store, registry=registry, routines={}), store, devices


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            {"device": "musik", "command": "volume_up", "value": 150},
            "Nilai 'volume_up' untuk Musik PC harus angka 1 sampai 100.",
        ),
        (
            {"device": "musik", "command": "volume"},
            "Nilai 'volume' untuk Musik PC harus angka 0 sampai 100.",
        ),
        (
            {"device": "musik", "command": "volume", "value": 150},
            "Nilai 'volume' untuk Musik PC harus angka 0 sampai 100.",
        ),
        (
            {"device": "lampu", "command": "brightness", "value": 0},
            "Nilai 'brightness' untuk Lampu harus angka 1 sampai 100.",
        ),
        (
            {"device": "lampu", "command": "brightness"},
            "Nilai 'brightness' untuk Lampu harus angka 1 sampai 100.",
        ),
    ],
    ids=["volume step too big", "no level", "level too high", "brightness 0", "no brightness"],
)
def test_schedule_refuses_a_value_the_device_would_refuse_when_it_fires(
    tmp_path, arguments, message
):
    # Otherwise the failure would be said aloud and pushed at the scheduled time, maybe at night.
    tools, store, devices = make_media_tools(tmp_path)

    data, is_error = run(tools, "schedule", {"at": "2026-10-13 23:00", **arguments})

    assert (data, is_error) == ({"ok": False, "message": message}, True)
    assert not store.path.exists()
    assert no_device_ran(devices)


@pytest.mark.parametrize("command", ["play_pause", "mute"])
def test_schedule_refuses_the_media_toggles_and_points_to_stop_or_volume(tmp_path, command):
    # Both keys toggle and a device schedule runs silently: "pause" at 23:00 would start music
    # Master had already paused, and "mute" would unmute a PC already muted, while they sleep.
    pressed: list[list[int]] = []
    registry = DeviceRegistry([MediaKeys("musik", "Musik PC", ["musik"], press=pressed.append)])
    store = make_store(tmp_path)
    tools = ScheduleTools(store, registry=registry, routines={})

    data, is_error = run(
        tools, "schedule", {"at": "2026-10-13 23:00", "device": "musik", "command": command}
    )

    assert (data, is_error) == (
        {
            "ok": False,
            "message": f"{command} pada Musik PC tidak bisa dijadwalkan: tombolnya bolak-balik, "
            "jadi bisa malah memutar musik atau menyalakan suara saat Master tidur. Untuk "
            "menghentikan musik jadwalkan stop; untuk mengecilkan suara jadwalkan volume dengan "
            "angka kecil, misalnya 10.",
        },
        True,
    )
    assert not store.path.exists()
    assert pressed == []


@pytest.mark.parametrize(
    ("command", "value"), [("stop", None), ("volume", 10), ("volume_down", 20), ("next", None)]
)
def test_schedule_accepts_the_other_media_commands_without_pressing_them(tmp_path, command, value):
    pressed: list[list[int]] = []
    registry = DeviceRegistry([MediaKeys("musik", "Musik PC", ["musik"], press=pressed.append)])
    store = make_store(tmp_path)
    tools = ScheduleTools(store, registry=registry, routines={})

    data, is_error = run(
        tools,
        "schedule",
        {"at": "2026-10-13 23:00", "device": "musik", "command": command, "value": value},
    )

    assert (data["ok"], is_error) == (True, False)
    assert [entry.action for entry in store.entries()] == [DeviceAction("musik", command, value)]
    assert pressed == []


def test_a_routine_cannot_be_scheduled_before_the_owner_writes_one(tmp_path):
    tools, store, _ = make_tools(tmp_path, routines={})

    data, is_error = run(tools, "schedule", {"at": "2026-10-14 02:00", "routine": "tidur"})

    assert (data, is_error) == (
        {"ok": False, "message": "Belum ada rutinitas; Master menulisnya di config.yaml."},
        True,
    )
    assert not store.path.exists()


def test_errors_she_may_read_out_call_the_owner_what_she_calls_them(tmp_path):
    # Ollama reads a failed tool's message aloud when the model's last reply is empty.
    pressed: list[list[int]] = []
    registry = DeviceRegistry([MediaKeys("musik", "Musik PC", ["musik"], press=pressed.append)])
    store = make_store(tmp_path)
    tools = ScheduleTools(store, registry=registry, routines={}, owner_call="Sayang")

    toggle, _ = run(
        tools, "schedule", {"at": "2026-10-13 23:00", "device": "musik", "command": "mute"}
    )
    routine, _ = run(tools, "schedule", {"at": "2026-10-14 02:00", "routine": "tidur"})

    assert "menyalakan suara saat Sayang tidur." in toggle["message"]
    assert routine["message"] == "Belum ada rutinitas; Sayang menulisnya di config.yaml."
    assert "Master" not in toggle["message"] + routine["message"]
    assert not store.path.exists()
    assert pressed == []


@pytest.mark.parametrize(
    ("arguments", "message_part"),
    [
        ({"at": "2026-10-13 14:29", "reminder": MEDICINE}, "sudah lewat"),
        ({"at": "2026-10-13 09:00", "reminder": MEDICINE}, "sudah lewat"),
        ({"at": "2027-10-15 15:00", "reminder": MEDICINE}, "terlalu jauh"),
        ({"at": "13-10-2026 15:00", "reminder": MEDICINE}, "YYYY-MM-DD HH:MM"),
        ({"at": "2026-10-13 3 sore", "reminder": MEDICINE}, "YYYY-MM-DD HH:MM"),
        ({"at": "2026-02-30 15:00", "reminder": MEDICINE}, "YYYY-MM-DD HH:MM"),
        ({"at": "2026-10-13 15:00", "reminder": " \n "}, "kosong"),
        (
            {"at": "2026-10-13 15:00", "reminder": "a" * (MAX_REMINDER_CHARS + 1)},
            f"paling banyak {MAX_REMINDER_CHARS} karakter",
        ),
    ],
    ids=[
        "a minute ago",
        "earlier today",
        "over a year ahead",
        "day first",
        "words",
        "no such day",
        "blank reminder",
        "long reminder",
    ],
)
def test_the_store_refusals_come_back_as_indonesian_error_results(
    tmp_path, arguments, message_part
):
    tools, store, _ = make_tools(tmp_path)

    data, is_error = run(tools, "schedule", arguments)

    assert is_error is True
    assert data["ok"] is False
    assert message_part in data["message"]
    assert not data["message"].startswith("Argumen")
    assert not store.path.exists()


def test_a_full_schedule_refuses_a_new_entry(tmp_path):
    tools, store, _ = make_tools(tmp_path, max_entries=1)
    tools.run("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE})

    data, is_error = run(
        tools, "schedule", {"at": "2026-10-13 16:00", "reminder": "Angkat jemuran!"}
    )

    assert (data, is_error) == (
        {"ok": False, "message": "Jadwal sudah penuh, 1 entri. Batalkan jadwal lama dulu."},
        True,
    )
    assert [entry.id for entry in store.entries()] == ["j1"]


@pytest.mark.parametrize(
    "actions_given",
    [
        {},
        {"repeat": "daily"},
        {"reminder": MEDICINE, "device": "ac", "command": "off"},
        {"reminder": MEDICINE, "routine": "tidur"},
        {"device": "ac", "command": "off", "routine": "tidur"},
        {"reminder": MEDICINE, "value": 26},
    ],
    ids=[
        "no action",
        "only a repeat",
        "reminder and device",
        "reminder and routine",
        "device and routine",
        "reminder and value",
    ],
)
def test_schedule_needs_exactly_one_action(tmp_path, actions_given):
    tools, store, _ = make_tools(tmp_path)

    data, is_error = run(tools, "schedule", {"at": "2026-10-14 02:00", **actions_given})

    assert is_error is True
    assert data["message"] == (
        "Argumen schedule tidak valid: isi tepat satu aksi: reminder; atau device dan command "
        "(value boleh); atau routine."
    )
    assert not store.path.exists()


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("schedule", None),
        ("schedule", "2026-10-13 15:00 minum obat"),
        ("schedule", ["2026-10-13 15:00"]),
        ("schedule", {"reminder": MEDICINE}),
        ("schedule", {"at": "", "reminder": MEDICINE}),
        ("schedule", {"at": 1500, "reminder": MEDICINE}),
        ("schedule", {"at": "2026-10-13 15:00", "reminder": 42}),
        ("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE, "repeat": "hourly"}),
        ("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE, "repeat": 1}),
        ("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE, "note": "obat"}),
        ("schedule", {"at": "2026-10-13 15:00", "device": "ac"}),
        ("schedule", {"at": "2026-10-13 15:00", "command": "off"}),
        ("schedule", {"at": "2026-10-13 15:00", "device": "", "command": "off"}),
        ("schedule", {"at": "2026-10-13 15:00", "device": "ac", "command": " "}),
        ("schedule", {"at": "2026-10-13 15:00", "device": "ac", "command": "temp", "value": "26"}),
        ("schedule", {"at": "2026-10-13 15:00", "device": "ac", "command": "temp", "value": 26.0}),
        ("schedule", {"at": "2026-10-13 15:00", "device": "ac", "command": "on", "value": True}),
        ("schedule", {"at": "2026-10-13 15:00", "routine": ""}),
        ("schedule", {"at": "2026-10-13 15:00", "routine": 5}),
        ("list_schedules", None),
        ("list_schedules", {"all": True}),
        ("cancel_schedule", None),
        ("cancel_schedule", {}),
        ("cancel_schedule", {"id": ""}),
        ("cancel_schedule", {"id": 1}),
        ("cancel_schedule", {"id": "j1", "reason": "batal"}),
        ("run_routine", None),
        ("run_routine", {}),
        ("run_routine", {"name": ""}),
        ("run_routine", {"name": ["tidur"]}),
        ("run_routine", {"name": "tidur", "at": "2026-10-13 23:00"}),
    ],
)
def test_invalid_arguments_are_error_results_that_change_nothing(tmp_path, name, arguments):
    tools, store, devices = make_tools(tmp_path)
    tools.run("schedule", {"at": "2026-10-13 23:00", "routine": "tidur"})
    saved = store.path.read_bytes()

    data, is_error = run(tools, name, arguments)

    assert is_error is True
    assert data["ok"] is False
    assert data["message"].startswith(f"Argumen {name} tidak valid: ")
    assert store.path.read_bytes() == saved
    assert no_device_ran(devices)


# --- list_schedules ------------------------------------------------------------------------


def test_list_schedules_without_entries_is_an_empty_list(tmp_path):
    tools, store, _ = make_tools(tmp_path)

    result, is_error = tools.run("list_schedules", {})

    assert (result, is_error) == ('{"ok":true,"entries":[]}', False)
    assert not store.path.exists()


def test_list_schedules_describes_every_entry_soonest_first_in_indonesian(tmp_path):
    tools, _, _ = make_tools(tmp_path)
    tools.run(
        "schedule", {"at": "2026-10-14 02:00", "device": "ac", "command": "temp", "value": 26}
    )
    tools.run("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE})
    tools.run("schedule", {"at": "2026-10-13 23:00", "repeat": "daily", "routine": "tidur"})
    tools.run(
        "schedule",
        {"at": "2026-10-17 07:00", "repeat": "weekends", "device": "ac", "command": "off"},
    )

    data, is_error = run(tools, "list_schedules", {})

    assert (data, is_error) == (
        {
            "ok": True,
            "entries": [
                {
                    "id": "j2",
                    "at": "2026-10-13 15:00",
                    "repeat": "none",
                    "action": f"pengingat: {MEDICINE}",
                },
                {
                    "id": "j3",
                    "at": "2026-10-13 23:00",
                    "repeat": "daily",
                    "action": "rutinitas: tidur",
                },
                {
                    "id": "j1",
                    "at": "2026-10-14 02:00",
                    "repeat": "none",
                    "action": "perangkat: ac temp 26",
                },
                {
                    "id": "j4",
                    "at": "2026-10-17 07:00",
                    "repeat": "weekends",
                    "action": "perangkat: ac off",
                },
            ],
        },
        False,
    )


def test_list_schedules_names_an_alarm_with_its_label_never_as_a_routine(tmp_path):
    tools, store, _ = make_tools(tmp_path)
    store.add(datetime(2026, 10, 14, 6, 0), "none", AlarmAction("berangkat kerja"))
    store.add(datetime(2026, 10, 15, 5, 30), "weekdays", AlarmAction())

    data, _ = run(tools, "list_schedules", {})

    assert [entry["action"] for entry in data["entries"]] == [
        "alarm: berangkat kerja",
        "alarm",
    ]


def test_list_schedules_gives_the_weekday_of_weekly_and_the_day_of_monthly_and_yearly_entries(
    tmp_path,
):
    tools, _, _ = make_tools(tmp_path)
    tools.run("schedule", {"at": "2026-10-31 09:00", "repeat": "monthly", "reminder": "Listrik!"})
    tools.run("schedule", {"at": "2028-02-29 08:00", "repeat": "yearly", "reminder": "Budi!"})
    tools.run("schedule", {"at": "2026-10-19 07:00", "repeat": "weekly", "routine": "pagi"})

    result, is_error = tools.run("list_schedules", {})

    assert (result, is_error) == (
        '{"ok":true,"entries":['
        '{"id":"j3","at":"2026-10-19 07:00","repeat":"weekly","weekday":"Senin",'
        '"action":"rutinitas: pagi"},'
        '{"id":"j1","at":"2026-10-31 09:00","repeat":"monthly","day":31,'
        '"action":"pengingat: Listrik!"},'
        '{"id":"j2","at":"2027-02-28 08:00","repeat":"yearly","day":29,'
        '"action":"pengingat: Budi!"}]}',
        False,
    )


def test_cancelling_a_yearly_entry_answers_with_its_day(tmp_path):
    tools, _, _ = make_tools(tmp_path)
    tools.run("schedule", {"at": "2027-05-05 08:00", "repeat": "yearly", "reminder": "Ibu!"})

    data, is_error = run(tools, "cancel_schedule", {"id": "j1"})

    assert (data, is_error) == (
        {
            "ok": True,
            "cancelled": {
                "id": "j1",
                "at": "2027-05-05 08:00",
                "repeat": "yearly",
                "day": 5,
                "action": "pengingat: Ibu!",
            },
        },
        False,
    )


def test_list_schedules_keeps_japanese_readable_in_compact_json(tmp_path):
    tools, _, _ = make_tools(tmp_path)
    tools.run("schedule", {"at": "2026-10-13 22:00", "reminder": "Master, おやすみなさい!"})

    result, _ = tools.run("list_schedules", {})

    assert result == (
        '{"ok":true,"entries":[{"id":"j1","at":"2026-10-13 22:00","repeat":"none",'
        '"action":"pengingat: Master, おやすみなさい!"}]}'
    )


# --- cancel_schedule -----------------------------------------------------------------------


def test_cancel_schedule_removes_the_entry_and_says_which(tmp_path):
    tools, store, _ = make_tools(tmp_path)
    tools.run("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE})
    tools.run("schedule", {"at": "2026-10-14 02:00", "device": "ac", "command": "off"})

    data, is_error = run(tools, "cancel_schedule", {"id": "j1"})

    assert (data, is_error) == (
        {
            "ok": True,
            "cancelled": {
                "id": "j1",
                "at": "2026-10-13 15:00",
                "repeat": "none",
                "action": f"pengingat: {MEDICINE}",
            },
        },
        False,
    )
    assert [entry.id for entry in store.entries()] == ["j2"]


def test_cancelling_an_unknown_id_is_an_error_result(tmp_path):
    tools, store, _ = make_tools(tmp_path)
    tools.run("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE})

    data, is_error = run(tools, "cancel_schedule", {"id": "j9"})

    assert (data, is_error) == ({"ok": False, "message": "Tidak ada jadwal dengan id itu."}, True)
    assert [entry.id for entry in store.entries()] == ["j1"]


# --- run_routine ---------------------------------------------------------------------------


def test_run_routine_runs_every_step_now_and_reports_each(tmp_path):
    tools, store, devices = make_tools(tmp_path)

    data, is_error = run(tools, "run_routine", {"name": "tidur"})

    assert (data, is_error) == (
        {
            "ok": True,
            "routine": "tidur",
            "steps": [
                {
                    "device": "lampu_kamar",
                    "command": "off",
                    "ok": True,
                    "message": "Lampu kamar: perintah 'off' berhasil (simulasi).",
                },
                {
                    "device": "ac",
                    "command": "temp",
                    "value": 26,
                    "ok": True,
                    "message": "AC Samsung: perintah 'temp_26' berhasil (simulasi).",
                },
            ],
        },
        False,
    )
    assert devices["lampu_kamar"].calls == [("off", None)]
    assert devices["ac"].calls == [("temp_26", 26)]
    assert not store.path.exists()


def test_a_failed_step_makes_the_routine_an_error_but_the_other_steps_still_run(tmp_path):
    routines = {"panas": routine(("ac", "temp", 99), ("lampu_kamar", "off"))}
    tools, _, devices = make_tools(tmp_path, routines=routines)

    data, is_error = run(tools, "run_routine", {"name": "panas"})

    assert is_error is True
    assert data["ok"] is False
    assert [step["ok"] for step in data["steps"]] == [False, True]
    assert "tidak punya perintah 'temp' dengan nilai 99" in data["steps"][0]["message"]
    assert devices["lampu_kamar"].calls == [("off", None)]


def test_run_routine_refuses_a_pc_step_and_runs_the_rest(tmp_path):
    # config.yaml already refuses a PC step; a device whose kind is pc is refused at run time too.
    routines = {"matikan_semua": routine(("lampu_kamar", "off"), ("komputer", "shutdown"))}
    tools, _, devices = make_tools(tmp_path, routines=routines)

    data, is_error = run(tools, "run_routine", {"name": "matikan_semua"})

    assert is_error is True
    assert data["steps"][1] == {
        "device": "komputer",
        "command": "shutdown",
        "ok": False,
        "message": "Komputer adalah PC, dan PC tidak pernah dijalankan dari rutinitas.",
    }
    assert devices["komputer"].calls == []
    assert devices["lampu_kamar"].calls == [("off", None)]


def test_run_routine_with_an_unknown_name_is_an_error_result(tmp_path):
    tools, _, devices = make_tools(tmp_path)

    data, is_error = run(tools, "run_routine", {"name": "santai"})

    assert (data, is_error) == (
        {
            "ok": False,
            "message": "Tidak ada rutinitas bernama 'santai'. Rutinitas yang ada: tidur, pagi.",
        },
        True,
    )
    assert no_device_ran(devices)


def test_run_routine_schedules_a_delayed_step_and_names_its_id_and_time(tmp_path):
    tools, store, devices = make_tools(tmp_path, routines={"tidur": DELAYED_TIDUR})

    data, is_error = run(tools, "run_routine", {"name": "tidur"})

    assert is_error is False
    assert data["ok"] is True
    assert data["steps"][2] == {
        "device": "ac",
        "command": "off",
        "after_minutes": 180,
        "ok": True,
        "message": "Dijadwalkan jam 17.30 nanti.",
        "scheduled": {"id": "j1", "at": "2026-10-13 17:30"},
    }
    assert "after_minutes" not in data["steps"][1]
    assert devices["ac"].calls == [("temp_26", 26)]
    assert actions(store) == [
        ("j1", datetime(2026, 10, 13, 17, 30), "none", DeviceAction("ac", "off", routine="tidur"))
    ]


def test_run_routine_at_bedtime_names_the_coming_night_without_besok_or_the_id(tmp_path):
    bedtime = datetime(2026, 10, 17, 22, 0)
    registry, _ = make_house()
    store = ScheduleStore(tmp_path / "schedule.json", now=lambda: bedtime)
    tools = ScheduleTools(
        store, registry=registry, routines={"tidur": DELAYED_TIDUR}, now=lambda: bedtime
    )

    data, _ = run(tools, "run_routine", {"name": "tidur"})

    # She copies this into her reply: "besok jam satu" sounds like 13:00 tomorrow, and an id
    # in the sentence gets read out. The id is in `scheduled`, for cancel_schedule.
    delayed = data["steps"][2]
    assert delayed["message"] == "Dijadwalkan jam 01.00 dini hari nanti."
    assert delayed["scheduled"] == {"id": "j1", "at": "2026-10-18 01:00"}


def test_a_delayed_step_is_listed_with_its_routine_and_cancelled_by_its_id(tmp_path):
    tools, store, devices = make_tools(tmp_path, routines={"tidur": DELAYED_TIDUR})
    run(tools, "run_routine", {"name": "tidur"})

    listed, _ = run(tools, "list_schedules", {})
    cancelled, is_error = run(tools, "cancel_schedule", {"id": "j1"})

    entry = {
        "id": "j1",
        "at": "2026-10-13 17:30",
        "repeat": "none",
        "action": "perangkat: ac off (rutinitas tidur)",
    }
    assert listed == {"ok": True, "entries": [entry]}
    assert (cancelled, is_error) == ({"ok": True, "cancelled": entry}, False)
    assert store.entries() == []
    assert ("off", None) not in devices["ac"].calls


def test_running_a_routine_again_replaces_its_pending_delayed_step(tmp_path):
    tools, store, _ = make_tools(tmp_path, routines={"tidur": DELAYED_TIDUR})

    run(tools, "run_routine", {"name": "tidur"})
    data, _ = run(tools, "run_routine", {"name": "tidur"})

    assert data["steps"][2]["scheduled"] == {"id": "j2", "at": "2026-10-13 17:30"}
    assert [entry.id for entry in store.entries()] == ["j2"]


def test_a_delayed_step_that_does_not_fit_in_the_schedule_is_an_honest_error(tmp_path):
    tools, store, devices = make_tools(tmp_path, routines={"tidur": DELAYED_TIDUR}, max_entries=1)
    store.add(datetime(2026, 10, 14, 7, 0), "daily", Reminder("Master, bangun!"))

    data, is_error = run(tools, "run_routine", {"name": "tidur"})

    assert is_error is True
    assert data["steps"][2] == {
        "device": "ac",
        "command": "off",
        "after_minutes": 180,
        "ok": False,
        "message": "Jadwal sudah penuh, 1 entri. Batalkan jadwal lama dulu.",
    }
    assert devices["ac"].calls == [("temp_26", 26)]


def test_run_routine_says_how_to_mention_and_cancel_a_delayed_step(tmp_path):
    tools, _, _ = make_tools(tmp_path, routines={"tidur": DELAYED_TIDUR})
    description = spec(tools, "run_routine").description

    assert "tidur: Mode tidur (lampu_kamar off, ac temp 26, ac off setelah 3 jam)" in description
    for guidance in [
        "setelah",
        "not run now",
        "AC akan mati jam satu nanti",
        "cancel_schedule",
        "replaces",
    ]:
        assert guidance in description


@pytest.mark.parametrize("name", ["run_routine", "remember", "control_device"])
def test_a_tool_that_is_not_offered_is_an_error_result(tmp_path, name):
    tools, _, devices = make_tools(tmp_path, routines={})

    data, is_error = run(tools, name, {"name": "tidur"})

    assert (data, is_error) == ({"ok": False, "message": f"Tool '{name}' bukan tool jadwal."}, True)
    assert no_device_ran(devices)


# --- unexpected failures -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE}),
        ("list_schedules", {}),
        ("cancel_schedule", {"id": "j1"}),
    ],
)
def test_a_schedule_file_that_cannot_be_read_is_logged_and_a_generic_error(
    tmp_path, caplog, name, arguments
):
    # A folder where the file should be: reading it raises an OSError, not a refused entry.
    tools, store, _ = make_tools(tmp_path)
    store.path.mkdir()

    with caplog.at_level(logging.ERROR, logger="waifu.brain.schedule_tools"):
        data, is_error = run(tools, name, arguments)

    assert (data, is_error) == (GENERIC_FAILURE, True)
    assert name in caplog.text
    assert any(record.exc_info for record in caplog.records)


def test_a_device_whose_commands_cannot_be_read_is_logged_and_a_generic_error(tmp_path, caplog):
    registry = DeviceRegistry([UnreadableCommands("ac", "AC Samsung", kind="ir")])
    store = make_store(tmp_path)
    tools = ScheduleTools(store, registry=registry, routines={})

    with caplog.at_level(logging.ERROR, logger="waifu.brain.schedule_tools"):
        data, is_error = run(
            tools, "schedule", {"at": "2026-10-14 02:00", "device": "ac", "command": "off"}
        )

    assert (data, is_error) == (GENERIC_FAILURE, True)
    assert "schedule" in caplog.text
    assert not store.path.exists()


# --- beside the other tools ----------------------------------------------------------------


def test_schedule_tools_come_after_the_device_game_and_memory_tools(tmp_path):
    registry, _ = make_house()
    games = GachaTools(Unreachable(), Unreachable(), ["hsr", "genshin", "zzz"])
    notes = MemoryTools(MemoryStore(tmp_path / "memory.json", now=lambda: NOW))
    schedules = ScheduleTools(make_store(tmp_path), registry=registry, routines=ROUTINES)

    specs = build_tool_specs(registry, [games, notes, schedules])

    assert [s.name for s in specs] == [
        "control_device",
        "list_devices",
        "game_status",
        "gacha_history",
        "remember",
        "forget",
        "schedule",
        "list_schedules",
        "cancel_schedule",
        "run_routine",
    ]


def test_run_tool_hands_the_schedule_tools_their_calls(tmp_path):
    registry, devices = make_house()
    store = make_store(tmp_path)
    notes = MemoryTools(MemoryStore(tmp_path / "memory.json", now=lambda: NOW))
    providers = [notes, ScheduleTools(store, registry=registry, routines=ROUTINES)]

    def call(name: str, arguments: dict) -> tuple[dict, bool]:
        result, is_error = run_tool(registry, name, arguments, providers)
        return json.loads(result), is_error

    assert call("schedule", {"at": "2026-10-13 15:00", "reminder": MEDICINE}) == (
        {"ok": True, "id": "j1", "at": "2026-10-13 15:00", "repeat": "none"},
        False,
    )
    assert call("list_schedules", {})[0]["entries"][0]["id"] == "j1"
    assert call("cancel_schedule", {"id": "j1"})[0]["ok"] is True
    assert store.entries() == []
    assert call("run_routine", {"name": "pagi"})[0]["ok"] is True
    assert devices["lampu_kamar"].calls == [("on", None)]
    assert call("control_device", {"device": "ac", "command": "off"})[0]["ok"] is True
    assert devices["ac"].calls == [("off", None)]
    assert call("remember", {"text": "Master minum obat jam tiga."})[0]["id"] == "m1"


def test_the_plug_that_powers_the_pc_can_be_neither_scheduled_nor_offered(tmp_path):
    registry, devices = make_house()
    plug = MockDevice("colokan_pc", "Colokan PC", commands=["on", "off"], kind="plug")
    registry = DeviceRegistry([*devices.values(), plug], pc_power_ids={"colokan_pc"})
    store = make_store(tmp_path)
    tools = ScheduleTools(store, registry=registry, routines={})

    result, is_error = tools.run(
        "schedule", {"at": "2026-10-13 23:00", "device": "colokan_pc", "command": "off"}
    )

    assert is_error is True
    assert json.loads(result)["message"] == (
        "Colokan PC mengalirkan listrik ke PC, jadi tidak pernah dijadwalkan: "
        "mematikannya sama dengan mencabut kabel PC. PC hanya dimatikan lewat "
        "perintah suara langsung."
    )
    assert store.entries() == []
    device_enum = spec(tools, "schedule").input_schema["properties"]["device"]["enum"]
    assert "colokan_pc" not in device_enum
    assert "komputer" not in device_enum
