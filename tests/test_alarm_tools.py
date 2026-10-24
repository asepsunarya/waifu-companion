"""The alarm tool over a real schedule and bell in tmp_path, at clocks moved by the test."""

import json
import logging
from datetime import datetime

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.alarm import AlarmBell
from waifu.brain.alarm_tools import AlarmTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.briefing import BRIEFING_PROMPT
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.guest import GUEST_REFUSAL
from waifu.journal import JOURNAL_PROMPT
from waifu.schedule import REPEATS, AlarmAction, Entry, Reminder, ScheduleStore

NIGHT = datetime(2026, 10, 24, 22, 15)  # a Saturday
CREATED = datetime(2026, 10, 24, 22, 15)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Switch:
    def __init__(self, on: bool = False) -> None:
        self.on = on

    def __call__(self) -> bool:
        return self.on


class Rig:
    def __init__(self, tmp_path, *, max_entries: int = 50, **bell_kwargs) -> None:
        self.clock = Clock(NIGHT)
        self.guests = Switch()
        self.store = ScheduleStore(
            tmp_path / "schedule.json", max_entries=max_entries, now=self.clock
        )
        self.bell = AlarmBell(
            tmp_path / "alarm.json",
            self.store,
            now=self.clock,
            clock=lambda: 0.0,
            **bell_kwargs,
        )
        self.tools = AlarmTools(self.store, self.bell, guest_mode=self.guests)

    def ask(self, arguments) -> dict:
        result, is_error = self.tools.run("alarm", arguments)
        assert is_error is False, result
        return json.loads(result)

    def failed(self, arguments) -> str:
        result, is_error = self.tools.run("alarm", arguments)
        assert is_error is True, result
        data = json.loads(result)
        assert data["ok"] is False
        return data["message"]

    def ring(self, label: str | None = None) -> None:
        self.bell.start(Entry("j99", self.clock.now, "none", AlarmAction(label), CREATED))


@pytest.fixture
def rig(tmp_path) -> Rig:
    return Rig(tmp_path)


# --- the spec ------------------------------------------------------------------------------------


def test_one_tool_whose_arguments_may_all_be_left_out_so_it_is_not_strict(rig):
    [spec] = rig.tools.specs()

    assert spec.name == "alarm"
    assert spec.input_schema == {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "list", "cancel", "stop", "snooze"]},
            "at": {"type": "string"},
            "repeat": {"type": "string", "enum": list(REPEATS)},
            "label": {"type": "string"},
            "id": {"type": "string"},
            "minutes": {"type": "integer"},
        },
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "alarm"


def test_the_description_says_what_an_alarm_is_for_and_when_it_rings(rig):
    [spec] = rig.tools.specs()

    for guidance in [
        "bangunkan aku jam 6",
        "alarm tiap hari kerja jam 6",
        "not a reminder",
        "every minute",
        "louder",
        "YYYY-MM-DD HH:MM",
        "only while the PC is on and awake",
        "first time in a conversation",
        "weekday",
        "Only when Master asks",
        "do not call again in this turn",
    ]:
        assert guidance in spec.description


@pytest.mark.parametrize(
    ("repeat_seconds", "max_minutes", "cadence"),
    [
        (60, 10, "again every minute, a little louder each time,"),
        (120, 20, "again every 2 minutes, a little louder each time,"),
        (45, 1, "again every 45 seconds, a little louder each time,"),
    ],
)
def test_the_description_tells_the_ring_s_configured_cadence_and_length(
    tmp_path, repeat_seconds, max_minutes, cadence
):
    rig = Rig(tmp_path, repeat_seconds=repeat_seconds, max_minutes=max_minutes)

    [spec] = rig.tools.specs()

    assert cadence in spec.description
    length = "1 minute passes" if max_minutes == 1 else f"{max_minutes} minutes pass"
    assert f"or {length}." in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching(rig):
    before = rig.tools.specs()

    rig.ask({"at": "2026-10-25 06:00"})
    rig.ring()

    assert rig.tools.specs() == before


# --- set -----------------------------------------------------------------------------------------


def test_set_saves_an_alarm_and_tells_its_time(rig):
    answer = rig.ask({"action": "set", "at": "2026-10-25 06:00", "label": "berangkat kerja"})

    assert answer == {
        "ok": True,
        "id": "j1",
        "at": "2026-10-25 06:00",
        "repeat": "none",
        "label": "berangkat kerja",
    }
    [entry] = rig.store.entries()
    assert (entry.at, entry.action) == (
        datetime(2026, 10, 25, 6, 0),
        AlarmAction("berangkat kerja"),
    )


def test_set_is_the_action_when_none_is_given(rig):
    answer = rig.ask({"at": "2026-10-25 06:00"})

    assert answer == {"ok": True, "id": "j1", "at": "2026-10-25 06:00", "repeat": "none"}
    assert rig.store.entries()[0].action == AlarmAction()


def test_a_repeating_alarm_names_its_repeat_and_a_weekly_one_its_weekday(rig):
    weekdays = rig.ask({"at": "2026-10-26 06:00", "repeat": "weekdays"})
    weekly = rig.ask({"at": "2026-10-26 05:30", "repeat": "weekly"})

    assert (weekdays["repeat"], weekdays["at"]) == ("weekdays", "2026-10-26 06:00")
    assert weekly["weekday"] == "Senin"


def test_a_label_loses_an_emotion_tag_and_its_extra_whitespace(rig):
    rig.ask({"at": "2026-10-25 06:00", "label": "[senang]  berangkat   kerja "})

    assert rig.store.entries()[0].action == AlarmAction("berangkat kerja")


def test_a_time_that_has_passed_is_refused_by_the_schedule(rig):
    message = rig.failed({"at": "2026-10-24 06:00"})

    assert message == "Waktu itu sudah lewat; pilih waktu mulai dari menit sekarang."
    assert rig.store.entries() == []


# --- list and cancel ------------------------------------------------------------------------------


def test_list_gives_only_the_alarms_soonest_first_and_whether_one_rings(rig):
    rig.ask({"at": "2026-10-26 06:00", "repeat": "weekdays", "label": "kerja"})
    rig.store.add(datetime(2026, 10, 25, 9, 0), "none", Reminder("Master, rapat!"))
    rig.ask({"at": "2026-10-25 07:00"})

    answer = rig.ask({"action": "list"})

    assert answer == {
        "ok": True,
        "alarms": [
            {"id": "j3", "at": "2026-10-25 07:00", "repeat": "none"},
            {"id": "j1", "at": "2026-10-26 06:00", "repeat": "weekdays", "label": "kerja"},
        ],
        "ringing": False,
    }


def test_list_says_when_an_alarm_rings_now(rig):
    rig.ring()

    assert rig.ask({"action": "list"}) == {"ok": True, "alarms": [], "ringing": True}


def test_cancel_removes_an_alarm_and_tells_which(rig):
    rig.ask({"at": "2026-10-25 06:00", "label": "kerja"})

    answer = rig.ask({"action": "cancel", "id": "j1"})

    assert answer == {
        "ok": True,
        "cancelled": {"id": "j1", "at": "2026-10-25 06:00", "repeat": "none", "label": "kerja"},
    }
    assert rig.store.entries() == []


def test_cancel_refuses_an_entry_that_is_no_alarm(rig):
    rig.store.add(datetime(2026, 10, 25, 9, 0), "none", Reminder("Master, rapat!"))

    message = rig.failed({"action": "cancel", "id": "j1"})

    assert message == "j1 bukan alarm; jadwal lain dibatalkan dengan cancel_schedule."
    assert len(rig.store.entries()) == 1


def test_cancel_of_an_unknown_id_is_the_schedule_s_refusal(rig):
    assert rig.failed({"action": "cancel", "id": "j7"}) == "Tidak ada jadwal dengan id itu."


# --- stop and snooze ------------------------------------------------------------------------------


def test_stop_ends_the_ring(rig):
    rig.ring()

    answer = rig.ask({"action": "stop"})

    assert answer == {"ok": True, "stopped": True, "message": "Alarm dimatikan."}
    assert rig.bell.ringing() is False


def test_stop_without_a_ring_says_none_rings(rig):
    assert rig.ask({"action": "stop"}) == {
        "ok": True,
        "stopped": False,
        "message": "Tidak ada alarm yang sedang berbunyi.",
    }


def test_snooze_rings_again_after_the_bell_s_minutes(rig):
    rig.ring("kerja")

    answer = rig.ask({"action": "snooze"})

    assert answer == {
        "ok": True,
        "id": "j1",
        "at": "2026-10-24 22:20",
        "message": "Alarm ditunda sampai jam 22.20.",
    }
    assert rig.bell.ringing() is False
    assert rig.store.entries()[0].action == AlarmAction("kerja")


def test_snooze_takes_the_minutes_master_says(rig):
    rig.ring()

    assert rig.ask({"action": "snooze", "minutes": 15})["at"] == "2026-10-24 22:30"


def test_snooze_without_a_ring_is_an_error_result(rig):
    message = rig.failed({"action": "snooze"})

    assert message == "Tidak ada alarm yang sedang berbunyi untuk ditunda."
    assert rig.store.entries() == []


def test_a_snooze_the_schedule_refuses_is_its_message_and_the_ring_goes_on(tmp_path):
    rig = Rig(tmp_path, max_entries=1)
    rig.store.add(datetime(2026, 10, 25, 9, 0), "none", Reminder("Master, rapat!"))
    rig.ring()

    message = rig.failed({"action": "snooze"})

    assert message == "Jadwal sudah penuh, 1 entri. Batalkan jadwal lama dulu."
    assert rig.bell.ringing() is True


# --- arguments ------------------------------------------------------------------------------------


def test_a_null_argument_counts_as_left_out(rig):
    answer = rig.ask(
        {"action": None, "at": "2026-10-25 06:00", "repeat": None, "label": None, "id": None}
    )

    assert answer["repeat"] == "none"


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ([], "objek JSON"),
        ({"waktu": "06:00"}, "argumen tidak dikenal: waktu"),
        ({"action": "ring"}, "'action' harus salah satu dari: set, list, cancel, stop, snooze"),
        ({}, "'at' wajib diisi dengan teks"),
        ({"at": 600}, "'at' wajib diisi dengan teks"),
        ({"at": "2026-10-25 06:00", "repeat": "hourly"}, "'repeat' harus salah satu dari"),
        ({"at": "2026-10-25 06:00", "label": 7}, "'label' harus berupa teks"),
        ({"at": "2026-10-25 06:00", "minutes": 5}, "'minutes' tidak dipakai untuk set"),
        ({"action": "stop", "at": "2026-10-25 06:00"}, "'at' tidak dipakai untuk stop"),
        ({"action": "list", "id": "j1"}, "'id' tidak dipakai untuk list"),
        ({"action": "cancel"}, "'id' wajib diisi dengan teks"),
        ({"action": "snooze", "minutes": 0}, "'minutes' harus bilangan bulat dari 1 sampai 30"),
        ({"action": "snooze", "minutes": 31}, "'minutes' harus bilangan bulat dari 1 sampai 30"),
        ({"action": "snooze", "minutes": True}, "'minutes'"),
        ({"action": "snooze", "minutes": "5"}, "'minutes'"),
    ],
)
def test_bad_arguments_are_an_error_result_and_change_nothing(rig, arguments, reason):
    rig.ring()

    message = rig.failed(arguments)

    assert message.startswith("Argumen alarm tidak valid:")
    assert reason in message
    assert rig.store.entries() == []
    assert rig.bell.ringing() is True


def test_a_bad_time_is_the_schedule_s_refusal(rig):
    assert rig.failed({"at": "besok jam 6"}).startswith("Waktunya harus tanggal dan jam")


def test_anything_unexpected_is_an_error_result_and_logged(rig, caplog):
    rig.store.path.mkdir()  # a folder where the schedule file should be

    with caplog.at_level(logging.ERROR, logger="waifu.brain.alarm_tools"):
        message = rig.failed({"at": "2026-10-25 06:00"})

    assert message == "Alarm tidak bisa diproses saat ini."
    assert "Alarm tool failed" in caplog.text


def test_another_tool_name_is_an_error_result(rig):
    result, is_error = rig.tools.run("schedule", {})

    assert is_error is True
    assert "bukan tool alarm" in json.loads(result)["message"]


# --- guest mode and her own turns -----------------------------------------------------------------


@pytest.mark.parametrize("arguments", [{"action": "list"}, {"action": "cancel", "id": "j1"}])
def test_guest_mode_closes_the_list_and_cancel(rig, arguments):
    rig.ask({"at": "2026-10-25 06:00", "label": "kerja"})
    rig.guests.on = True

    assert rig.failed(arguments) == GUEST_REFUSAL
    assert len(rig.store.entries()) == 1


def test_guest_mode_leaves_setting_stopping_and_snoozing_open(rig):
    rig.guests.on = True

    rig.ask({"at": "2026-10-25 06:00"})
    rig.ring()
    rig.ask({"action": "snooze"})
    rig.ring()

    assert rig.ask({"action": "stop"})["stopped"] is True
    assert len(rig.store.entries()) == 2


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        (BRIEFING_PROMPT, "Alarm hanya diatur atas permintaan Master, bukan untuk sapaan pagi."),
        (
            WEEKLY_REPORT_PROMPT,
            "Alarm hanya diatur atas permintaan Master, bukan untuk laporan game mingguan.",
        ),
        (
            JOURNAL_PROMPT,
            "Alarm hanya diatur atas permintaan Master, bukan untuk pertanyaan jurnal malam.",
        ),
    ],
    ids=["briefing", "weekly_report", "journal_question"],
)
def test_her_own_turns_never_touch_an_alarm_and_master_s_next_turn_does(rig, prompt, message):
    rig.ring()
    rig.tools.begin_turn(prompt)

    assert rig.failed({"action": "stop"}) == message
    assert rig.bell.ringing() is True
    rig.tools.begin_turn("matikan alarm")

    assert rig.ask({"action": "stop"})["stopped"] is True
