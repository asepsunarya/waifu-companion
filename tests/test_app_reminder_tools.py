"""The app_reminder tool (v1.53): what it keeps, lists, and removes in a store in tmp_path, how
it tells that the game is open now from a play time record, and when it refuses."""

import json
import logging
from datetime import datetime, timedelta

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.app_reminders import AppReminderStore
from waifu.brain.app_reminder_tools import AppReminderTools
from waifu.brain.tools import to_anthropic, to_ollama
from waifu.briefing import BRIEFING_PROMPT
from waifu.config import PlayTimeConfig
from waifu.finance.values import WIB
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.play_time import PlayRecord, Session

NOW = datetime(2026, 10, 24, 20, 30, tzinfo=WIB)


class Played:
    """Stands in for the play time store: the record the test sets."""

    def __init__(self, record: PlayRecord | None = None) -> None:
        self.current = record or PlayRecord()

    def record(self) -> PlayRecord:
        return self.current


def make_store(tmp_path) -> AppReminderStore:
    return AppReminderStore(tmp_path / "app_reminders.json", now=lambda: NOW)


def make_tools(tmp_path, *, store=None, played=None, **config) -> AppReminderTools:
    return AppReminderTools(
        store if store is not None else make_store(tmp_path),
        played if played is not None else Played(),
        PlayTimeConfig(**config),
        now=lambda: NOW,
    )


def ask(tools: AppReminderTools, arguments) -> dict:
    result, is_error = tools.run("app_reminder", arguments)
    assert is_error is False, result
    return json.loads(result)


def failed(tools: AppReminderTools, arguments) -> str:
    result, is_error = tools.run("app_reminder", arguments)
    assert is_error is True
    data = json.loads(result)
    assert data["ok"] is False
    return data["message"]


# --- the spec -----------------------------------------------------------------------------------


def test_one_tool_whose_arguments_may_all_be_left_out_so_it_takes_no_strict_slot(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    assert spec.name == "app_reminder"
    assert spec.input_schema == {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "remove"]},
            "game": {"type": "string", "enum": ["hsr", "genshin", "zzz"]},
            "text": {"type": "string"},
            "every_time": {"type": "boolean"},
            "id": {"type": "string"},
        },
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "app_reminder"


def test_the_games_offered_are_the_tracked_ones(tmp_path):
    [spec] = make_tools(tmp_path, games={"zzz": ["ZenlessZoneZero.exe"]}).specs()

    assert spec.input_schema["properties"]["game"]["enum"] == ["zzz"]
    assert "Zenless Zone Zero = zzz" in spec.description
    assert "Star Rail" not in spec.description


def test_the_description_steers_game_start_reminders_away_from_the_schedule(tmp_path):
    [spec] = make_tools(tmp_path).specs()

    for guidance in [
        "pas buka HSR nanti, ingetin aku farming relic",
        "tiap buka Genshin ingetin cek ekspedisi",
        "nanti kalau buka ZZZ ingetin claim battle pass",
        "never schedule",
        "Honkai: Star Rail = hsr, Genshin Impact = genshin, Zenless Zone Zero = zzz",
        "without 'ingetin aku'",
        "`every_time` true only when Master says every time",
        "never for a game already open when you start or when the PC wakes from sleep",
        "`game_open_now`",
        "never guess an id",
        "Only when Master asks",
        "no lists and no parentheses",
        "do not call again in this turn",
    ]:
        assert guidance in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching(tmp_path):
    tools = make_tools(tmp_path)
    before = tools.specs()

    ask(tools, {"game": "hsr", "text": "farming relic"})

    assert tools.specs() == before


# --- adding -------------------------------------------------------------------------------------


def test_add_is_the_default_and_keeps_a_once_reminder_for_the_next_start(tmp_path):
    store = make_store(tmp_path)

    result = ask(make_tools(tmp_path, store=store), {"game": "hsr", "text": "farming relic"})

    assert result == {
        "ok": True,
        "id": "g1",
        "game": "hsr",
        "name": "HSR",
        "text": "farming relic",
        "every_time": False,
        "game_open_now": False,
    }
    [kept] = store.reminders()
    assert (kept.game, kept.text, kept.every_time) == ("hsr", "farming relic", False)


def test_add_keeps_one_for_every_start_when_asked(tmp_path):
    result = ask(
        make_tools(tmp_path),
        {"action": "add", "game": "genshin", "text": "cek ekspedisi", "every_time": True},
    )

    assert (result["game"], result["name"], result["every_time"]) == ("genshin", "Genshin", True)


def test_an_emotion_tag_the_persona_adds_is_never_kept_in_the_text(tmp_path):
    result = ask(make_tools(tmp_path), {"game": "zzz", "text": "[senang] claim battle pass"})

    assert result["text"] == "claim battle pass"


def test_a_game_open_now_keeps_it_for_the_next_start_and_says_so(tmp_path):
    played = Played(
        PlayRecord(open=(Session("hsr", NOW - timedelta(hours=1), NOW - timedelta(minutes=1)),))
    )

    result = ask(make_tools(tmp_path, played=played), {"game": "hsr", "text": "daily"})

    assert result["game_open_now"] is True
    assert result["message"] == (
        "HSR sedang terbuka sekarang; pengingat ini disimpan untuk saat HSR dibuka berikutnya."
    )


def test_a_session_left_open_by_a_run_that_stopped_long_ago_is_not_open_now(tmp_path):
    played = Played(
        PlayRecord(open=(Session("hsr", NOW - timedelta(hours=3), NOW - timedelta(hours=2)),))
    )

    result = ask(make_tools(tmp_path, played=played), {"game": "hsr", "text": "daily"})

    assert result["game_open_now"] is False
    assert "message" not in result


def test_another_game_open_now_changes_nothing(tmp_path):
    played = Played(PlayRecord(open=(Session("genshin", NOW, NOW),)))

    result = ask(make_tools(tmp_path, played=played), {"game": "hsr", "text": "daily"})

    assert result["game_open_now"] is False


def test_a_refused_reminder_is_an_error_result_with_the_store_s_message(tmp_path):
    store = make_store(tmp_path)
    for number in range(30):
        store.add("hsr", f"tugas {number}")

    message = failed(make_tools(tmp_path, store=store), {"game": "hsr", "text": "satu lagi"})

    assert message == "Pengingat game sudah penuh, 30 pengingat. Hapus yang lama dulu."


# --- listing and removing -----------------------------------------------------------------------


def test_list_gives_every_reminder_in_the_order_made(tmp_path):
    store = make_store(tmp_path)
    store.add("zzz", "claim battle pass")
    store.add("genshin", "cek ekspedisi", every_time=True)

    result = ask(make_tools(tmp_path, store=store), {"action": "list"})

    assert result == {
        "ok": True,
        "reminders": [
            {
                "id": "g1",
                "game": "zzz",
                "name": "ZZZ",
                "text": "claim battle pass",
                "every_time": False,
            },
            {
                "id": "g2",
                "game": "genshin",
                "name": "Genshin",
                "text": "cek ekspedisi",
                "every_time": True,
            },
        ],
    }


def test_remove_deletes_the_reminder_with_the_id_given(tmp_path):
    store = make_store(tmp_path)
    store.add("zzz", "claim battle pass")
    store.add("genshin", "cek ekspedisi", every_time=True)

    result = ask(make_tools(tmp_path, store=store), {"action": "remove", "id": "g2"})

    assert result == {
        "ok": True,
        "removed": {
            "id": "g2",
            "game": "genshin",
            "name": "Genshin",
            "text": "cek ekspedisi",
            "every_time": True,
        },
    }
    assert [r.id for r in store.reminders()] == ["g1"]


def test_removing_an_unknown_id_is_an_error_result(tmp_path):
    message = failed(make_tools(tmp_path), {"action": "remove", "id": "g9"})

    assert message == "Tidak ada pengingat game dengan id g9."


# --- refusals -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ([], "objek JSON"),
        ({"action": "edit"}, "'action' harus salah satu dari: add, list, remove"),
        ({"text": "daily"}, "'game' harus salah satu dari: hsr, genshin, zzz"),
        ({"game": "wuwa", "text": "daily"}, "'game' harus salah satu dari: hsr, genshin, zzz"),
        ({"game": "hsr"}, "'text' wajib diisi dengan teks"),
        ({"game": "hsr", "text": 42}, "'text' wajib diisi dengan teks"),
        ({"game": "hsr", "text": "daily", "every_time": "ya"}, "'every_time' harus true"),
        ({"action": "remove"}, "'id' wajib diisi dengan teks"),
        ({"action": "list", "game": "hsr"}, "'game' tidak dipakai untuk list"),
        ({"action": "remove", "id": "g1", "text": "x"}, "'text' tidak dipakai untuk remove"),
        ({"game": "hsr", "text": "daily", "at": "21:00"}, "argumen tidak dikenal: at"),
    ],
)
def test_bad_arguments_are_an_error_result(tmp_path, arguments, reason):
    message = failed(make_tools(tmp_path), arguments)

    assert message.startswith("Argumen app_reminder tidak valid:")
    assert reason in message


def test_a_null_argument_counts_as_left_out(tmp_path):
    result = ask(
        make_tools(tmp_path),
        {"action": None, "game": "hsr", "text": "daily", "every_time": None, "id": None},
    )

    assert (result["id"], result["every_time"]) == ("g1", False)


def test_a_game_not_tracked_by_play_time_is_refused(tmp_path):
    tools = make_tools(tmp_path, games={"zzz": ["ZenlessZoneZero.exe"]})

    message = failed(tools, {"game": "hsr", "text": "daily"})

    assert "'game' harus salah satu dari: zzz" in message


def test_an_unreadable_file_is_an_error_result_and_logged(tmp_path, caplog):
    class UnreadableStore:
        def reminders(self):
            raise OSError("the disk is gone")

    with caplog.at_level(logging.ERROR, logger="waifu.brain.app_reminder_tools"):
        message = failed(make_tools(tmp_path, store=UnreadableStore()), {"action": "list"})

    assert message == "Pengingat game tidak bisa dibuka saat ini."
    assert "the disk is gone" in caplog.text


def test_another_tool_name_is_an_error_result(tmp_path):
    result, is_error = make_tools(tmp_path).run("schedule", {})

    assert is_error is True
    assert "bukan tool pengingat game" in json.loads(result)["message"]


@pytest.mark.parametrize(
    ("prompt", "turn"),
    [
        (BRIEFING_PROMPT, "sapaan pagi"),
        (WEEKLY_REPORT_PROMPT, "laporan game mingguan"),
        (JOURNAL_PROMPT, "pertanyaan jurnal malam"),
    ],
    ids=["briefing", "weekly_report", "journal_question"],
)
def test_her_own_turns_never_touch_a_reminder_and_master_s_next_turn_does(tmp_path, prompt, turn):
    tools = make_tools(tmp_path)

    tools.begin_turn(prompt)
    message = failed(tools, {"action": "list"})
    tools.begin_turn("pengingat gameku apa aja?")

    assert message == f"Pengingat game hanya diatur atas permintaan Master, bukan untuk {turn}."
    assert ask(tools, {"action": "list"}) == {"ok": True, "reminders": []}
