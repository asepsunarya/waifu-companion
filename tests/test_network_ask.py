import logging
from dataclasses import replace
from datetime import datetime

import pytest

from waifu.finance.values import WIB
from waifu.network import NetworkCheck, NetworkState
from waifu.network_ask import NetworkAnswers, network_question
from waifu.network_probe import Reading

pytestmark = pytest.mark.usefixtures("nothing_beyond_this_pc")

NAMES = ("Cyrene", "Hai Cyrene", "Master")
NOW = datetime(2026, 10, 25, 21, 15, tzinfo=WIB)
FINE = Reading(
    at=NOW,
    route=True,
    gateway="192.168.1.1",
    gateway_ms=1.0,
    anchor_ms=24.0,
    dns_checked=True,
    dns_ms=12.0,
)


@pytest.mark.parametrize(
    "said",
    [
        "Cyrene, internetnya kenapa?",
        "internet mati ya?",
        "Kok lemot?",
        "kok lambat sih",
        "Internet putus?",
        "Kok internetnya lemot banget sih?",
        "Hai Cyrene, internetnya lagi down ya?",
        "Wi-Fi-nya putus?",
        "wifi mati",
        "cek koneksi dong",
        "Apakah jaringannya lagi gangguan?",
        "apa internetnya error",
        "kenapa internetnya",
        "Master, internet aman?",
        "internetnya gimana?",
        # Whisper writes a loanword's -nya hyphenated, and "lagi" after the question is natural
        # right after an outage.
        "WiFi-nya mati ya?",
        "wifi-nya kenapa",
        "Internet-nya kenapa?",
        "Koneksi-nya putus?",
        "Jaringan-nya gangguan?",
        "Internetnya putus lagi?",
        "internet mati lagi ya",
        "kok lemot lagi",
        "Kok internetnya lemot lagi sih?",
    ],
)
def test_a_whole_question_about_the_internet_is_one(said):
    assert network_question(said, NAMES) == "internet"


@pytest.mark.parametrize(
    "said",
    [
        "Server HoYo down?",
        "Cyrene, server HoYoverse lagi bermasalah ya?",
        "server hoyo verse down",
        "server HoYoLAB error?",
        "kok server miHoYo lemot",
        "cek server HoYoLAB",
        "server HoYo?",
        "HoYo down?",
        "hoyolab lagi gangguan ya",
        "HoYoLAB-nya error?",
        "server HoYo-nya down lagi?",
    ],
)
def test_a_whole_question_about_the_hoyo_servers_is_one(said):
    assert network_question(said, NAMES) == "hoyo"


@pytest.mark.parametrize(
    "said",
    [
        "matikan internet",
        "nyalakan wifi",
        "internetnya bagus banget",
        "kok lemot banget PC-nya",
        "internet",
        "server",
        "lemot",
        "kenapa",
        "server Genshin down?",
        "putar lagu internet mati",
        "internetnya kenapa terus restart router",
        "HoYo",
        "mati lagi?",  # what is off again: the lights, the AC, the internet?
        "lemot lagi",
    ],
)
def test_anything_else_is_not_a_question_about_the_internet(said):
    assert network_question(said, NAMES) is None


def test_what_she_calls_the_owner_is_left_out_too():
    assert network_question("Sayang, internet mati?", ("Cyrene", "Sayang")) == "internet"
    assert network_question("Sayang, internet mati?", ("Cyrene",)) is None


class FakeProbe:
    def __init__(self, reading: Reading | Exception = FINE) -> None:
        self.reading = reading
        self.asked: list[dict] = []

    def check(self, *, dns: bool = False, hoyo: bool = False) -> Reading:
        self.asked.append({"dns": dns, "hoyo": hoyo})
        if isinstance(self.reading, Exception):
            raise self.reading
        return self.reading


def answers(probe: FakeProbe) -> NetworkAnswers:
    check = NetworkCheck(probe, NetworkState(slow_window=5), slow_ms=200)
    return NetworkAnswers(check, owner_call="master")


def test_her_answer_comes_from_a_check_made_now_with_the_lookup():
    probe = FakeProbe()

    said = answers(probe).answer("internet")

    assert said.startswith("Master, ping internetnya normal, sekitar 24 milidetik.")
    assert probe.asked == [{"dns": True, "hoyo": False}]


def test_asked_kok_lemot_after_quick_polls_she_says_the_slow_round_trip_of_now():
    state = NetworkState(slow_window=5)
    for took in (20.0, 21.0, 22.0, 23.0):  # the watcher's polls of the last minutes
        state.note(replace(FINE, anchor_ms=took), "ok")
    check = NetworkCheck(FakeProbe(replace(FINE, anchor_ms=450.0)), state, slow_ms=200)

    said = NetworkAnswers(check, owner_call="master").answer("internet")

    assert said.startswith("Master, internetnya lagi lambat, ping-nya sekitar 450 milidetik.")


def test_asked_about_hoyo_she_checks_the_hoyo_hosts_too():
    probe = FakeProbe(replace(FINE, hoyo_ms=(45.0, 40.0, 52.0)))

    said = answers(probe).answer("hoyo")

    assert probe.asked == [{"dns": True, "hoyo": True}]
    assert "Server HoYoLAB dan HoYoverse menjawab" in said


def test_a_check_that_fails_unexpectedly_gets_an_apology_and_is_logged(caplog):
    probe = FakeProbe(RuntimeError("unexpected"))

    with caplog.at_level(logging.ERROR, logger="waifu.network_ask"):
        said = answers(probe).answer("internet")

    assert said == "Maaf master, internetnya tidak bisa kucek sekarang."
    assert "Could not check the internet" in caplog.text
