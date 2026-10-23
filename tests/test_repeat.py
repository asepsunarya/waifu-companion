import pytest

from waifu.repeat import REPEAT_PHRASES, is_repeat_request

# Her name, wake words, and what she calls the owner, as the Assistant hands them to the gate.
NAMES = ("Cyrene", "cyrene", "hai cyrene", "hei cyrene", "Master")


@pytest.mark.parametrize(
    "text",
    [
        "ulangi",
        "Ulangi.",
        "Ulangi lagi!",
        "ulang lagi",
        "Bisa diulang?",
        "bisa diulangi?",
        "Diulang dong.",
        "Tadi bilang apa?",
        "tadi kamu bilang apa?",
        "Kamu tadi bilang apa?",
        "kamu bilang apa tadi?",
        "Bilang apa tadi?",
        "Apa tadi?",
        "Sekali lagi.",
        "Maaf, apa?",
        "Hah?",
        "hah",
    ],
)
def test_the_repeat_phrases_ask_her_to_repeat(text):
    assert is_repeat_request(text, NAMES)


@pytest.mark.parametrize("phrase", sorted(REPEAT_PHRASES))
def test_every_listed_phrase_asks_her_to_repeat(phrase):
    assert is_repeat_request(" ".join(phrase), NAMES)


@pytest.mark.parametrize(
    "text", ["Maaf, ulangi?", "maaf, tadi bilang apa?", "Maaf, apa tadi?", "maaf sekali lagi"]
)
def test_maaf_may_come_before_any_phrase(text):
    assert is_repeat_request(text, NAMES)


@pytest.mark.parametrize(
    "text",
    [
        "Cyrene, ulangi.",  # in the follow-up window the gate keeps her name
        "ulangi dong, Cyrene",
        "Hai Cyrene, tadi bilang apa?",
        "Master, apa tadi?",
        "Coba ulangi.",
        "Tolong diulangi ya.",
        "ulangi dong",
        "ulangi deh",
        "ya, ulangi",
        "tolong ulangi lagi dong",
    ],
)
def test_her_names_master_and_the_softeners_are_left_out(text):
    assert is_repeat_request(text, NAMES)


@pytest.mark.parametrize(
    "text",
    [
        "coba sekali lagi",
        "Tolong sekali lagi.",
        "coba deh sekali lagi",
        "coba sekali lagi dong",
        "Cyrene, coba sekali lagi",
        "maaf, coba sekali lagi",
    ],
)
def test_coba_or_tolong_with_sekali_lagi_asks_her_to_try_again_not_to_repeat(text):
    assert not is_repeat_request(text, NAMES)


def test_what_she_calls_the_owner_is_left_out_when_given_as_a_name():
    assert is_repeat_request("Sayang, ulangi.", ("Cyrene", "Sayang"))
    assert not is_repeat_request("Sayang, ulangi.", ("Cyrene",))


def test_without_her_names_a_name_counts_as_another_word():
    assert not is_repeat_request("Cyrene, ulangi")


def test_the_words_are_compared_after_nfkc_and_lowercase():
    assert is_repeat_request("ＵＬＡＮＧＩ", NAMES)


@pytest.mark.parametrize(
    "text",
    [
        "ulangi lagunya",
        "ulangi pengingatnya",
        "ulangi yang tadi",
        "ulangi pelan-pelan",
        "putar sekali lagi",
        "nyalakan sekali lagi",
        "sekali lagi, matikan lampunya",
        "apa tadi lampunya sudah mati?",
        "tadi aku bilang apa?",
        "tadi aku bilang apa ke kamu soal jadwal?",
        "tadi kamu bilang apa soal cuaca?",
        "apa kabar?",
        "apa?",
        "Apa",
        "maaf",
        "maaf ya",
        "dong",
        "ya",
        "ulang",
        "ulangi ulangi",
        "ulangi lagi lagi",
        "hah, serius?",
        "hahaha",
        "bisa",
        "tadi",
        "lagi",
        "tadi bilang",
        "Cyrene",
        "",
        "?",
    ],
)
def test_anything_with_other_words_or_nothing_left_is_not_a_repeat_request(text):
    assert not is_repeat_request(text, NAMES)
