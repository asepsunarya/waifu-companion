"""Her pronunciation dictionary: the built-in rules, Master's rules in their small file (under
tmp_path only), and the voice phrase that teaches one."""

import json
import logging
from datetime import datetime

import pytest

from waifu.lexicon import (
    BUILT_IN_READINGS,
    MAX_RULES,
    Lexicon,
    LexiconError,
    checked_rule,
    pronounce,
    reading_request,
)

NOW = datetime(2026, 10, 25, 21, 0, 0)
CORRUPT_NAME = "tts_lexicon.json.corrupt-20261025-210000"


def make_lexicon(path) -> Lexicon:
    return Lexicon(path, now=lambda: NOW)


def stored(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- pronounce ------------------------------------------------------------------------------


def test_the_built_in_rules_read_the_game_abbreviations_aloud():
    assert BUILT_IN_READINGS == {
        "HSR": "Ha Es Er",
        "ZZZ": "Zet Zet Zet",
        "HoYoLAB": "Hoyolab",
        "HoYoverse": "Hoyovers",
    }
    assert (
        pronounce("Pity HSR-ku 70, ZZZ juga; cek di HoYoLAB dari HoYoverse.")
        == "Pity Ha Es Er-ku 70, Zet Zet Zet juga; cek di Hoyolab dari Hoyovers."
    )


@pytest.mark.parametrize("text", ["HSRX", "aHSR", "HSR2", "ZZZZ", "HoYoLABs", "_HSR"])
def test_a_term_inside_a_longer_word_stays(text):
    assert pronounce(text) == text


@pytest.mark.parametrize("text", ["Zzz... zzz", "hsr", "Hsr", "hoyolab"])
def test_the_built_in_rules_match_only_their_own_case(text):
    assert pronounce(text) == text


def test_masters_rules_match_any_case():
    rules = {"Acheron": "A Ke Ron"}

    assert pronounce("Acheron, acheron, ACHERON!", rules) == "A Ke Ron, A Ke Ron, A Ke Ron!"


def test_masters_rule_replaces_the_built_in_rule_of_the_same_term():
    assert pronounce("HSR dan hsr", {"hsr": "Honkai Star Rail"}) == (
        "Honkai Star Rail dan Honkai Star Rail"
    )


def test_the_longest_term_wins_where_two_start_together():
    rules = {"Game": "Gem", "Game Master": "Gem Mastah"}

    assert pronounce("Game Master main Game", rules) == "Gem Mastah main Gem"


def test_a_space_in_a_term_matches_any_whitespace():
    assert pronounce("Game  Master\nGame\tMaster", {"Game Master": "GM"}) == "GM\nGM"


def test_a_reading_is_never_rewritten_again():
    rules = {"Kafka": "Kaf Ka HSR", "Ka": "Kah"}

    assert pronounce("Kafka", rules) == "Kaf Ka HSR"


def test_text_without_a_rule_is_unchanged():
    text = "Lampu kamar sudah menyala, Master. はい!"

    assert pronounce(text, {"Acheron": "A Ke Ron"}) == text


# --- checked_rule ---------------------------------------------------------------------------


def test_a_rule_is_given_back_with_its_whitespace_collapsed():
    assert checked_rule("  Game \n Master ", " Gem   Mastah ") == ("Game Master", "Gem Mastah")


@pytest.mark.parametrize(
    "term",
    ["", "   ", "satu dua tiga empat", "x" * 31, "123", "...", "鈴蘭", "Cyrene さん"],
)
def test_a_term_must_be_one_to_three_words_of_at_most_30_characters_with_a_letter(term):
    with pytest.raises(LexiconError, match="Kata yang dibaca harus 1 sampai 3 kata"):
        checked_rule(term, "Sesuatu")


@pytest.mark.parametrize("reading", ["", "a b c d e f g h i", "y" * 61, "42", "かふか", "Kaf カ"])
def test_a_reading_must_be_one_to_eight_words_of_at_most_60_characters_with_a_letter(reading):
    with pytest.raises(LexiconError, match="Bacaannya harus 1 sampai 8 kata"):
        checked_rule("Kafka", reading)


@pytest.mark.parametrize("reading", ["Kafka", "kafka", "KAFKA saja", "si kafka!"])
def test_a_reading_may_not_hold_its_own_term(reading):
    with pytest.raises(LexiconError, match="Bacaannya tidak boleh memuat kata itu sendiri"):
        checked_rule("Kafka", reading)


def test_a_reading_may_hold_the_term_inside_a_longer_word():
    assert checked_rule("Ka", "Kah Kafka") == ("Ka", "Kah Kafka")


# --- the store ------------------------------------------------------------------------------


def test_a_missing_file_holds_no_rules_and_stays_missing(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    lexicon = make_lexicon(path)

    assert lexicon.read() == {}
    assert lexicon.readings() == {}
    assert not path.exists()


def test_a_rule_is_written_and_read_back_by_another_process(tmp_path):
    path = tmp_path / "data" / "tts_lexicon.json"

    added = make_lexicon(path).add("Acheron", " A  Ke Ron ")

    assert added == ("Acheron", "A Ke Ron")
    assert stored(path) == {"version": 1, "rules": {"Acheron": "A Ke Ron"}}
    assert make_lexicon(path).read() == {"Acheron": "A Ke Ron"}


def test_a_rule_for_the_same_term_in_another_case_replaces_the_earlier_one(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    lexicon = make_lexicon(path)
    lexicon.add("Acheron", "A Ke Ron")
    lexicon.add("Kafka", "Kaf Ka")

    lexicon.add("acheron", "A Che Ron")

    assert lexicon.read() == {"Kafka": "Kaf Ka", "acheron": "A Che Ron"}


def test_a_refused_rule_writes_nothing(tmp_path):
    path = tmp_path / "tts_lexicon.json"

    with pytest.raises(LexiconError):
        make_lexicon(path).add("Kafka", "kafka")

    assert not path.exists()


def test_the_dictionary_holds_at_most_200_rules_but_a_full_one_still_takes_a_new_reading(
    tmp_path,
):
    path = tmp_path / "tts_lexicon.json"
    rules = {f"Kata{n}": f"Baca {n}" for n in range(MAX_RULES)}
    path.write_text(json.dumps({"version": 1, "rules": rules}), encoding="utf-8")
    lexicon = make_lexicon(path)

    with pytest.raises(LexiconError, match="sudah berisi 200 aturan"):
        lexicon.add("Kafka", "Kaf Ka")
    lexicon.add("kata7", "Baca tujuh")

    assert len(lexicon.read()) == MAX_RULES
    assert lexicon.read()["kata7"] == "Baca tujuh"


def test_a_rule_is_removed_by_its_term_in_any_case(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    lexicon = make_lexicon(path)
    lexicon.add("Acheron", "A Ke Ron")
    lexicon.add("Kafka", "Kaf Ka")

    assert lexicon.remove("ACHERON") == "Acheron"
    assert lexicon.remove("Acheron") is None
    assert stored(path) == {"version": 1, "rules": {"Kafka": "Kaf Ka"}}


def test_removing_from_a_missing_file_leaves_it_missing(tmp_path):
    path = tmp_path / "tts_lexicon.json"

    assert make_lexicon(path).remove("Kafka") is None
    assert not path.exists()


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    path.write_text('{"version": 1, "rules": {"Kafka": "Kaf Ka"}}', encoding="utf-8-sig")

    assert make_lexicon(path).read() == {"Kafka": "Kaf Ka"}


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"rules": {}}',
        '{"version": 2, "rules": {}}',
        '{"version": 1, "rules": []}',
        '{"version": 1, "rules": {}, "extra": 1}',
        '{"version": 1, "rules": {"Kafka": 5}}',
        '{"version": 1, "rules": {"Kafka": "kafka"}}',
        '{"version": 1, "rules": {"Kafka": "Kaf Ka", "KAFKA": "Kaf Kah"}}',
    ],
)
def test_a_file_that_is_not_a_dictionary_is_moved_aside_and_read_as_empty(
    tmp_path, caplog, content
):
    path = tmp_path / "tts_lexicon.json"
    path.write_text(content, encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="waifu.lexicon"):
        assert make_lexicon(path).read() == {}

    assert not path.exists()
    assert (tmp_path / CORRUPT_NAME).read_text(encoding="utf-8") == content
    assert "moved it to" in caplog.text


def test_a_file_that_is_not_utf8_is_moved_aside(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    path.write_bytes(b'{"version": 1, "rules": {"K\xe4fka": "Kaf Ka"}}')

    assert make_lexicon(path).read() == {}
    assert (tmp_path / CORRUPT_NAME).exists()


def test_a_dictionary_over_200_rules_is_moved_aside(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    rules = {f"Kata{n}": f"Baca {n}" for n in range(MAX_RULES + 1)}
    path.write_text(json.dumps({"version": 1, "rules": rules}), encoding="utf-8")

    assert make_lexicon(path).read() == {}
    assert (tmp_path / CORRUPT_NAME).exists()


def test_a_rule_added_after_a_broken_file_starts_anew_and_keeps_the_broken_one(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    path.write_text("{not json", encoding="utf-8")
    (tmp_path / CORRUPT_NAME).write_text("older", encoding="utf-8")

    make_lexicon(path).add("Kafka", "Kaf Ka")

    assert stored(path) == {"version": 1, "rules": {"Kafka": "Kaf Ka"}}
    assert (tmp_path / CORRUPT_NAME).read_text(encoding="utf-8") == "older"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_text(encoding="utf-8") == "{not json"


def test_a_file_that_cannot_be_read_raises_from_read(tmp_path):
    path = tmp_path / "tts_lexicon.json"
    path.mkdir()

    with pytest.raises(OSError):
        make_lexicon(path).read()


def test_readings_never_raise_and_warn_once_while_the_file_cannot_be_read(tmp_path, caplog):
    path = tmp_path / "tts_lexicon.json"
    path.mkdir()
    lexicon = make_lexicon(path)

    with caplog.at_level(logging.WARNING, logger="waifu.lexicon"):
        assert lexicon.readings() == {}
        assert lexicon.readings() == {}

    assert len(caplog.records) == 1
    path.rmdir()
    lexicon.add("Kafka", "Kaf Ka")
    assert lexicon.readings() == {"Kafka": "Kaf Ka"}


# --- reading_request ------------------------------------------------------------------------

NAMES = ("Cyrene", "cyrene", "hai cyrene")


@pytest.mark.parametrize(
    ("command", "rule"),
    [
        ("cara baca HSR itu Ha Es Er", ("HSR", "Ha Es Er")),
        ("Cara Baca acheron itu A Ke Ron.", ("acheron", "A Ke Ron")),
        ("Cyrene, tolong cara baca Kafka itu Kaf Ka ya", ("Kafka", "Kaf Ka")),
        ("Hai Cyrene, cara baca Kafka itu, Kaf Ka!", ("Kafka", "Kaf Ka")),
        ("cara baca Kafka itu Kaf Ka, Cyrene.", ("Kafka", "Kaf Ka")),
        ("cara baca GM itu Game Master", ("GM", "Game Master")),
        ('cara baca "HoYoLAB" itu "Ho Yo Lab".', ("HoYoLAB", "Ho Yo Lab")),
        ("cara baca Star Rail itu Star Rel", ("Star Rail", "Star Rel")),
        ("cara baca Dan Heng IL itu Dan Heng I El", ("Dan Heng IL", "Dan Heng I El")),
        (
            "cara baca X itu satu dua tiga empat lima enam tujuh delapan",
            ("X", "satu dua tiga empat lima enam tujuh delapan"),
        ),
    ],
)
def test_a_reading_request_gives_the_term_and_the_reading_as_said(command, rule):
    assert reading_request(command, NAMES) == rule


@pytest.mark.parametrize(
    ("command", "rule"),
    [
        ("cara baca Gallagher itu Ga Le Ger", ("Gallagher", "Ga Le Ger")),
        ("Cara baca Ganyu itu Ga Nyu.", ("Ganyu", "Ga Nyu")),
        ("cara baca Kanzaki itu Kan Za Ki", ("Kanzaki", "Kan Za Ki")),
        ("cara baca Manami itu Ma Na Mi", ("Manami", "Ma Na Mi")),
    ],
)
def test_a_syllable_that_doubles_as_a_question_tag_is_kept_inside_a_reading(command, rule):
    assert reading_request(command, NAMES) == rule


@pytest.mark.parametrize(
    "command",
    [
        "cara baca HSR itu Ha Es Er ga",
        "cara baca HSR itu Ha Es Er gak ya",
        "cara baca HSR itu Ha Es Er kan",
        "cara baca HSR itu Ha Es Er kah",
        "cara baca HSR itu yang mana",
        "cara baca HSR itu ga",
    ],
)
def test_a_question_tag_ending_the_reading_makes_it_a_question(command):
    assert reading_request(command, NAMES) is None


@pytest.mark.parametrize(
    "command",
    [
        "cara baca HSR itu gimana?",
        "cara baca HSR itu Ha Es Er?",
        "cara baca HSR itu gimana",
        "cara baca HSR itu apa ya",
        "cara baca HSR itu Ha Es Er bener nggak",
        "cara baca HSR itu kayak apa",
        "cara baca siapa itu Kafka",
        "cara baca satu dua tiga empat itu Sesuatu",
        "cara baca X itu satu dua tiga empat lima enam tujuh delapan sembilan",
        "cara baca HSR",
        "cara baca HSR itu",
        "cara baca itu Ha Es Er",
        "gimana cara baca HSR itu Ha Es Er",
        "cara membaca HSR itu Ha Es Er",
        "nyalakan lampu kamar",
        "",
    ],
)
def test_anything_else_is_not_a_reading_request(command):
    assert reading_request(command, NAMES) is None
