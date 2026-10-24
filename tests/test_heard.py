from datetime import datetime

import pytest

from waifu.heard import HEARD_PHRASES, heard_answer, is_heard_request
from waifu.repeat import REPEAT_PHRASES, is_repeat_request
from waifu.voice.diagnosis import ACCEPTED, EMPTY, NO_WAKE, NOT_OWNER, UtteranceRecord

# Her name, wake words, and what she calls the owner, as the Assistant hands them to the gate.
NAMES = ("Cyrene", "cyrene", "hai cyrene", "hei cyrene", "Master")
NOW = datetime(2026, 10, 24, 19, 30)


@pytest.mark.parametrize(
    "text",
    [
        "tadi kamu denger apa?",
        "Tadi kamu dengar apa?",
        "Kamu dengar apa tadi?",
        "kamu denger apa tadi",
        "Kamu tadi denger apa?",
        "tadi denger apa?",
        "Dengar apa tadi?",
        "Maaf, tadi kamu denger apa?",
    ],
)
def test_the_heard_phrases_ask_what_she_heard(text):
    assert is_heard_request(text, NAMES)


@pytest.mark.parametrize("phrase", sorted(HEARD_PHRASES))
def test_every_listed_phrase_asks_what_she_heard(phrase):
    assert is_heard_request(" ".join(phrase), NAMES)


@pytest.mark.parametrize(
    "text",
    [
        "Cyrene, tadi kamu denger apa?",
        "tadi kamu denger apa dong, Cyrene",
        "Hai Cyrene, kamu dengar apa tadi?",
        "Master, tadi denger apa?",
        "coba, tadi kamu dengar apa?",
        "ya tadi kamu denger apa",
    ],
)
def test_her_names_master_and_the_softeners_are_left_out(text):
    assert is_heard_request(text, NAMES)


@pytest.mark.parametrize(
    "text",
    [
        "kamu dengar apa?",  # what she listens to: music, maybe
        "tadi kamu denger apa aja di radio?",
        "tadi kamu denger lagu apa?",
        "aku tadi denger apa ya",
        "tadi kamu bilang apa?",
        "dengar",
        "",
    ],
)
def test_anything_with_other_words_is_not_a_heard_request(text):
    assert not is_heard_request(text, NAMES)


def test_no_heard_phrase_is_a_repeat_phrase():
    assert HEARD_PHRASES.isdisjoint(REPEAT_PHRASES)
    for phrase in HEARD_PHRASES:
        assert not is_repeat_request(" ".join(phrase), NAMES), phrase


def test_no_repeat_phrase_asks_what_she_heard():
    for phrase in REPEAT_PHRASES:
        assert not is_heard_request(" ".join(phrase), NAMES), phrase


def heard(outcome: str, transcript: str | None = None, **known) -> UtteranceRecord:
    return UtteranceRecord(NOW, 1.2, -20.0, -30.0, outcome, transcript=transcript, **known)


@pytest.mark.parametrize(
    ("record", "answer"),
    [
        (None, "Belum ada yang kudengar, Sayang."),
        (
            heard(ACCEPTED, "Cyrene, nyalakan lampu"),
            'Tadi aku dengar "Cyrene, nyalakan lampu", dan sudah kujawab.',
        ),
        (
            heard(NO_WAKE, "matikan lampu"),
            'Tadi aku dengar "matikan lampu", tapi kamu tidak memanggil namaku, '
            "jadi tidak kujawab.",
        ),
        (
            heard(EMPTY),
            "Tadi ada suara 1,2 detik, tapi tidak ada kata yang bisa kutangkap.",
        ),
        (
            heard(NOT_OWNER, score=0.3249, threshold=0.45),
            "Tadi ada suara, tapi kemiripannya dengan suaramu cuma 0,32, padahal butuh 0,45, "
            "jadi tidak kujawab.",
        ),
        (
            heard(NOT_OWNER, threshold=0.45),
            "Tadi ada suara, tapi pengenal suaramu sedang tidak bisa dipakai, jadi tidak kujawab.",
        ),
    ],
    ids=["nothing", "answered", "no-wake", "empty", "not-owner", "no-verifier"],
)
def test_her_answer_tells_what_she_heard_and_what_she_did_with_it(record, answer):
    assert heard_answer(record, "Sayang") == answer
