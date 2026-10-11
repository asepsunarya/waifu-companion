from pathlib import Path

import pytest

from waifu.config import load_config
from waifu.gate import GateResult, WakeGate

EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.example.yaml"
EXAMPLE_WAKE_WORDS = load_config(EXAMPLE_CONFIG).assistant.wake_words


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_wake_word_at_the_start_is_removed_with_its_comma():
    gate = WakeGate(["cyrene"], followup_seconds=8)

    result = gate.check("Cyrene, nyalakan lampu kamar.")

    assert result == GateResult(accepted=True, command="nyalakan lampu kamar.", reason="wake_word")


def test_wake_word_at_the_end_is_removed_with_its_punctuation():
    gate = WakeGate(["cyrene"], followup_seconds=8)

    assert gate.check("Matikan proyektor, Cyrene!").command == "Matikan proyektor"


def test_wake_word_in_the_middle_joins_the_rest_of_the_sentence():
    gate = WakeGate(["cyrene"], followup_seconds=8)

    assert gate.check("Tolong ya Cyrene, AC dua puluh empat derajat").command == (
        "Tolong ya AC dua puluh empat derajat"
    )


@pytest.mark.parametrize("heard", ["Syrene", "Cyrine", "CYRENE"])
def test_misheard_spellings_of_the_name_still_wake(heard):
    gate = WakeGate(["cyrene"], followup_seconds=8)

    result = gate.check(f"{heard}, matikan TV")

    assert result.accepted
    assert result.command == "matikan TV"


def test_full_width_letters_are_normalised_before_matching():
    gate = WakeGate(["cyrene"], followup_seconds=8)

    assert gate.check("Ｃｙｒｅｎｅ　nyalakan lampu").command == "nyalakan lampu"


def test_speech_without_a_wake_word_is_rejected():
    gate = WakeGate(EXAMPLE_WAKE_WORDS, followup_seconds=8)

    result = gate.check("nyalakan lampu kamar")

    assert result == GateResult(accepted=False, command="", reason="no_wake_word")


@pytest.mark.parametrize(
    "everyday_speech",
    [
        "Ada suara sirine ambulans di luar",
        "Ada suara sirene ambulans di luar",
        "Sirene mobil polisi itu keras banget",
        "Bunyi sirenenya kencang",
        "Cuaca hari ini cerah sekali",
        "Saring dulu airnya sebelum diminum",
        "Siaran televisi malam ini bagus",
        "Sirkuit balapnya basah",
        "Serius, aku capek banget",
    ],
)
def test_everyday_indonesian_words_do_not_wake_with_the_example_list(everyday_speech):
    gate = WakeGate(EXAMPLE_WAKE_WORDS, followup_seconds=8)

    assert not gate.check(everyday_speech).accepted


@pytest.mark.parametrize(
    ("everyday_word", "wake_word"),
    [("sirene", "syrene"), ("sirene", "sairen"), ("sirine", "sairin"), ("saring", "sairin")],
)
def test_an_everyday_look_alike_never_counts_as_a_misheard_wake_word(everyday_word, wake_word):
    gate = WakeGate([wake_word], followup_seconds=8)

    assert not gate.check(f"Ada {everyday_word} di sini").accepted


@pytest.mark.parametrize("everyday_word", ["sirine", "sirene"])
def test_an_everyday_look_alike_wakes_when_the_owner_lists_it_verbatim(everyday_word):
    gate = WakeGate([everyday_word], followup_seconds=8)

    assert gate.check(f"{everyday_word.title()}, nyalakan lampu").command == "nyalakan lampu"


def test_the_longest_matching_wake_word_is_removed():
    gate = WakeGate(["cyrene", "hai cyrene"], followup_seconds=8)

    assert gate.check("Hai, Syrene! Nyalakan lampu").command == "Nyalakan lampu"


def test_a_multi_word_wake_word_needs_its_words_next_to_each_other():
    gate = WakeGate(["hai cyrene"], followup_seconds=8)

    assert not gate.check("Hai semuanya, ini Cyrene").accepted


def test_a_japanese_wake_word_matches_inside_unspaced_text():
    gate = WakeGate(EXAMPLE_WAKE_WORDS, followup_seconds=8)

    result = gate.check("キュレネ、電気をつけて")

    assert result.accepted
    assert result.command == "電気をつけて"


def test_a_bare_wake_word_is_accepted_with_an_empty_command():
    gate = WakeGate(EXAMPLE_WAKE_WORDS, followup_seconds=8)

    assert gate.check("Cyrene?") == GateResult(accepted=True, command="", reason="wake_word")


def test_follow_up_accepts_speech_without_a_wake_word_unchanged():
    clock = FakeClock()
    gate = WakeGate(["cyrene"], followup_seconds=8, clock=clock)
    gate.open_followup()
    clock.now += 3

    result = gate.check("Terus nyalakan TV juga, ya?")

    assert result == GateResult(
        accepted=True, command="Terus nyalakan TV juga, ya?", reason="followup"
    )


def test_follow_up_window_closes_after_its_duration():
    clock = FakeClock()
    gate = WakeGate(["cyrene"], followup_seconds=8, clock=clock)
    gate.open_followup()
    clock.now += 8

    assert gate.check("terus nyalakan TV juga") == GateResult(
        accepted=False, command="", reason="no_wake_word"
    )


def test_close_ends_the_follow_up_window_early():
    gate = WakeGate(["cyrene"], followup_seconds=8, clock=FakeClock())
    gate.open_followup()

    gate.close()

    assert not gate.check("terus nyalakan TV juga").accepted


def test_follow_up_counts_from_when_the_owner_started_speaking():
    clock = FakeClock(now=0.0)
    gate = WakeGate(["cyrene"], followup_seconds=8, clock=clock)
    gate.open_followup()
    clock.now = 10.0

    assert gate.check("terus nyalakan TV juga", heard_at=6.5).accepted
    assert not gate.check("terus nyalakan TV juga", heard_at=8.5).accepted


def test_a_wake_word_still_works_after_the_follow_up_window_expires():
    clock = FakeClock()
    gate = WakeGate(["cyrene"], followup_seconds=8, clock=clock)
    gate.open_followup()
    clock.now += 30

    assert gate.check("Cyrene, matikan TV") == GateResult(
        accepted=True, command="matikan TV", reason="wake_word"
    )


@pytest.mark.parametrize("silence", ["", "   ", "...", "、"])
def test_empty_text_is_rejected_even_during_follow_up(silence):
    gate = WakeGate(["cyrene"], followup_seconds=8, clock=FakeClock())
    gate.open_followup()

    assert gate.check(silence) == GateResult(accepted=False, command="", reason="empty")
