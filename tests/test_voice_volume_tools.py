"""The voice_volume tool over a real VoiceLevel in tmp_path, at a local clock moved by the test."""

import json
import logging
from datetime import datetime

import ollama
import pytest
from anthropic.types.beta import BetaToolParam

from waifu.brain.tools import to_anthropic, to_ollama
from waifu.brain.voice_volume_tools import VoiceVolumeTools
from waifu.briefing import BRIEFING_PROMPT
from waifu.config import VoiceNightConfig
from waifu.gacha.weekly import WEEKLY_REPORT_PROMPT
from waifu.journal import JOURNAL_PROMPT
from waifu.voice_level import VoiceLevel

EVENING = datetime(2026, 10, 24, 20, 0)  # the PC's local time, before the whisper window
NIGHT = datetime(2026, 10, 24, 23, 0)  # inside it


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Rig:
    def __init__(self, tmp_path) -> None:
        self.clock = Clock(EVENING)
        self.path = tmp_path / "voice.json"
        self.level = VoiceLevel(self.path, VoiceNightConfig(), now=self.clock)
        self.tools = VoiceVolumeTools(self.level)

    def ask(self, arguments=None) -> dict:
        result, is_error = self.tools.run("voice_volume", {} if arguments is None else arguments)
        assert is_error is False, result
        return json.loads(result)

    def failed(self, arguments) -> str:
        result, is_error = self.tools.run("voice_volume", arguments)
        assert is_error is True
        data = json.loads(result)
        assert data["ok"] is False
        return data["message"]

    def stored(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))


@pytest.fixture
def rig(tmp_path) -> Rig:
    return Rig(tmp_path)


def test_one_tool_whose_arguments_may_all_be_left_out_so_it_is_not_strict(rig):
    [spec] = rig.tools.specs()

    assert spec.name == "voice_volume"
    assert spec.input_schema == {
        "type": "object",
        "properties": {
            "level": {"type": "integer"},
            "change": {"type": "string", "enum": ["louder", "softer"]},
            "night": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    claude_tool = to_anthropic(spec)
    assert claude_tool["strict"] is False
    assert claude_tool.keys() <= BetaToolParam.__optional_keys__ | BetaToolParam.__required_keys__
    assert ollama.Tool.model_validate(to_ollama(spec)).function.name == "voice_volume"


def test_the_description_keeps_her_voice_apart_from_the_windows_volume(rig):
    [spec] = rig.tools.specs()

    for guidance in [
        "only for YOUR voice",
        "suaramu",
        "suara kamu",
        "mode bisik",
        # The possessive decides, so "volume suaramu", the feature's own name, is hers.
        '"kecilkan volume suaramu"',
        '"suaramu", "suara kamu", or "volume suaramu" are your voice even with the word volume',
        '"volume" alone, "musik", "PC", or the Windows volume are not yours',
        "control_device on the media device",
        "everything the PC plays, your voice included",
        "10 to 150 percent",
        "one step of 20 percent",
        "not both",
        "whisper mode",
        "phone",
        "only when Master asks",
        "one short sentence",
        "do not call again in this turn",
    ]:
        assert guidance in spec.description


def test_the_description_says_her_answer_is_heard_at_now_not_at_the_level_just_set(rig):
    # Whisper mode may hold her below the level just set; she must not claim it is heard.
    [spec] = rig.tools.specs()

    assert "When you speak on the PC, your answer is already heard at `now`" in spec.description
    assert "at the new level" not in spec.description


def test_the_spec_is_the_same_at_every_call_for_prompt_caching(rig):
    before = rig.tools.specs()

    rig.ask({"change": "softer"})

    assert rig.tools.specs() == before


# --- the state ---------------------------------------------------------------------------------


def test_without_arguments_she_tells_her_level_and_whisper_mode(rig):
    assert rig.ask() == {
        "level": 100,
        "night": False,
        "night_from": "22:00",
        "night_until": "06:00",
        "night_level": 50,
        "now": 100,
        "whispering": False,
        "message": "Suaraku 100 persen; mode bisik mati.",
    }
    assert not rig.path.exists()  # reading never creates the file


def test_the_state_with_whisper_mode_on_outside_its_window(rig):
    rig.level.set_night(True)

    answer = rig.ask()

    assert (answer["night"], answer["now"], answer["whispering"]) == (True, 100, False)
    assert answer["message"] == (
        "Suaraku 100 persen; mode bisik aktif, jam 22:00 sampai 06:00 paling keras 50 persen."
    )


def test_the_state_while_whispering_says_whisper_mode_holds_her_back(rig):
    rig.level.set_night(True)
    rig.clock.now = NIGHT

    answer = rig.ask()

    assert (answer["level"], answer["now"], answer["whispering"]) == (100, 50, True)
    assert answer["message"] == (
        "Suaraku 100 persen, tapi mode bisik menahannya di 50 persen sampai jam 06:00."
    )


def test_the_state_while_whispering_below_the_night_level(rig):
    rig.level.set_level(30)
    rig.level.set_night(True)
    rig.clock.now = NIGHT

    answer = rig.ask()

    assert (answer["level"], answer["now"], answer["whispering"]) == (30, 30, True)
    assert answer["message"] == (
        "Suaraku 30 persen; mode bisik aktif, jam 22:00 sampai 06:00 paling keras 50 persen."
    )


# --- the level ---------------------------------------------------------------------------------


def test_a_level_is_set_and_saved(rig):
    answer = rig.ask({"level": 80})

    assert (answer["changed"], answer["level"], answer["now"]) == (True, 80, 80)
    assert answer["message"] == "Suaraku sekarang 80 persen."
    assert rig.stored() == {"version": 1, "level": 80, "night": False}


def test_the_level_she_already_has(rig):
    answer = rig.ask({"level": 100})

    assert answer["changed"] is False
    assert answer["message"] == "Suaraku memang sudah 100 persen."


def test_softer_and_louder_go_one_step(rig):
    assert rig.ask({"change": "softer"})["message"] == "Suaraku sekarang 80 persen."
    assert rig.ask({"change": "louder"})["message"] == "Suaraku sekarang 100 persen."
    assert rig.ask({"change": "louder"})["level"] == 120


def test_louder_at_the_top_says_it_is_as_loud_as_it_gets(rig):
    rig.level.set_level(150)

    answer = rig.ask({"change": "louder"})

    assert (answer["changed"], answer["level"]) == (False, 150)
    assert answer["message"] == "Suaraku sudah paling keras, 150 persen."


def test_softer_at_the_bottom_says_it_is_as_soft_as_it_gets(rig):
    rig.level.set_level(10)

    answer = rig.ask({"change": "softer"})

    assert (answer["changed"], answer["level"]) == (False, 10)
    assert answer["message"] == "Suaraku sudah paling pelan, 10 persen."


def test_louder_while_whispering_says_whisper_mode_still_holds_her_back(rig):
    # Otherwise she would claim a louder voice that Master does not hear.
    rig.level.set_night(True)
    rig.clock.now = NIGHT

    answer = rig.ask({"change": "louder"})

    assert (answer["level"], answer["now"], answer["whispering"]) == (120, 50, True)
    assert answer["message"] == (
        "Suaraku sekarang 120 persen, tapi mode bisik menahannya di 50 persen sampai jam 06:00."
    )


def test_softer_while_whispering_is_softer_than_what_master_hears(rig):
    # Stepping 120 to 100 would leave her held at 50: no softer at all to Master's ears.
    rig.level.set_level(120)
    rig.level.set_night(True)
    rig.clock.now = NIGHT

    answer = rig.ask({"change": "softer"})

    assert (answer["level"], answer["now"], answer["whispering"]) == (40, 40, True)
    assert answer["message"] == "Suaraku sekarang 40 persen."
    assert rig.stored() == {"version": 1, "level": 40, "night": True}


# --- whisper mode ------------------------------------------------------------------------------


def test_whisper_mode_turned_on_names_its_window_and_level(rig):
    answer = rig.ask({"night": True})

    assert (answer["changed"], answer["night"]) == (True, True)
    assert answer["message"] == (
        "Mode bisik aktif: dari jam 22:00 sampai 06:00 suaraku paling keras 50 persen."
    )
    assert rig.stored() == {"version": 1, "level": 100, "night": True}


def test_whisper_mode_turned_on_again(rig):
    rig.level.set_night(True)

    answer = rig.ask({"night": True})

    assert answer["changed"] is False
    assert answer["message"] == "Mode bisik memang sudah aktif."


def test_whisper_mode_turned_off(rig):
    rig.level.set_night(True)

    answer = rig.ask({"night": False})

    assert (answer["changed"], answer["night"]) == (True, False)
    assert answer["message"] == "Mode bisik mati."


def test_whisper_mode_turned_off_while_off(rig):
    answer = rig.ask({"night": False})

    assert answer["changed"] is False
    assert answer["message"] == "Mode bisik memang mati."


def test_whisper_mode_and_a_level_together(rig):
    rig.level.set_night(True)

    answer = rig.ask({"night": False, "level": 80})

    assert (answer["changed"], answer["night"], answer["level"]) == (True, False, 80)
    assert answer["message"] == "Mode bisik mati. Suaraku sekarang 80 persen."


def test_whisper_mode_on_and_louder_at_night(rig):
    rig.clock.now = NIGHT

    answer = rig.ask({"night": True, "change": "louder"})

    assert (answer["level"], answer["now"]) == (120, 50)
    assert answer["message"] == (
        "Mode bisik aktif: dari jam 22:00 sampai 06:00 suaraku paling keras 50 persen. "
        "Suaraku sekarang 120 persen, tapi mode bisik menahannya di 50 persen sampai jam 06:00."
    )


# --- errors ------------------------------------------------------------------------------------


def test_a_null_argument_counts_as_left_out(rig):
    answer = rig.ask({"level": None, "change": None, "night": None})

    assert answer["message"] == "Suaraku 100 persen; mode bisik mati."


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ([], "objek JSON"),
        ({"level": 9}, "'level' harus bilangan bulat dari 10 sampai 150"),
        ({"level": 151}, "'level' harus bilangan bulat dari 10 sampai 150"),
        ({"level": "80"}, "'level'"),
        ({"level": 80.0}, "'level'"),
        ({"level": True}, "'level'"),
        ({"change": "up"}, "'change' harus salah satu dari: louder, softer"),
        ({"night": "on"}, "'night' harus true, false, atau dikosongkan"),
        ({"night": 1}, "'night'"),
        ({"level": 80, "change": "louder"}, "'level' dan 'change' tidak bisa dipakai bersamaan"),
        ({"volume": 50}, "argumen tidak dikenal: volume"),
    ],
)
def test_bad_arguments_are_an_error_result_and_change_nothing(rig, arguments, reason):
    message = rig.failed(arguments)

    assert message.startswith("Argumen voice_volume tidak valid:")
    assert reason in message
    assert not rig.path.exists()


def test_a_file_that_cannot_be_read_is_an_error_result_and_logged(rig, caplog):
    rig.path.mkdir()  # a folder where the file should be

    with caplog.at_level(logging.ERROR, logger="waifu.brain.voice_volume_tools"):
        message = rig.failed({"change": "softer"})

    assert message == "Volume suaraku tidak bisa dibaca atau disimpan saat ini."
    assert "Voice volume tool" in caplog.text


def test_another_tool_name_is_an_error_result(rig):
    result, is_error = rig.tools.run("guest_mode", {})

    assert is_error is True
    assert "bukan tool volume suara" in json.loads(result)["message"]


@pytest.mark.parametrize(
    ("prompt", "message"),
    [
        (
            BRIEFING_PROMPT,
            "Volume suaraku hanya diubah atas permintaan Master, bukan untuk sapaan pagi.",
        ),
        (
            WEEKLY_REPORT_PROMPT,
            "Volume suaraku hanya diubah atas permintaan Master, bukan untuk laporan game "
            "mingguan.",
        ),
        (
            JOURNAL_PROMPT,
            "Volume suaraku hanya diubah atas permintaan Master, bukan untuk pertanyaan jurnal "
            "malam.",
        ),
    ],
    ids=["briefing", "weekly_report", "journal_question"],
)
def test_her_own_turns_never_change_her_volume_and_master_s_next_turn_does(rig, prompt, message):
    rig.tools.begin_turn(prompt)
    assert rig.failed({"change": "softer"}) == message
    assert not rig.path.exists()
    rig.tools.begin_turn("kecilkan suaramu")

    assert rig.ask({"change": "softer"})["level"] == 80
