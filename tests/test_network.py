import threading
from dataclasses import replace
from datetime import datetime

import pytest

from waifu.finance.values import WIB
from waifu.network import (
    DOWN,
    MEANINGS,
    CurrentOutage,
    Finding,
    HoyoFinding,
    NetworkCheck,
    NetworkState,
    answer_line,
    back_line,
    classify,
    hoyo_finding,
)
from waifu.network_probe import Reading

pytestmark = pytest.mark.usefixtures("nothing_beyond_this_pc")

NOW = datetime(2026, 10, 25, 21, 15, tzinfo=WIB)
SINCE = datetime(2026, 10, 25, 21, 3, tzinfo=WIB)
FINE = Reading(at=NOW, route=True, gateway="192.168.1.1", gateway_ms=1.0, anchor_ms=24.0)
NO_INTERNET = replace(FINE, anchor_ms=None)
HOYO_FINE = (45.0, 40.0, 52.0)


def judge(reading: Reading, *, recent=(), slow_ms: int = 200, gateway_seen: bool = False) -> str:
    return classify(reading, recent_ms=recent, slow_ms=slow_ms, gateway_seen=gateway_seen)


# --- classification ----------------------------------------------------------------------------


def test_an_answering_internet_is_ok():
    assert judge(FINE) == "ok"


def test_without_a_route_and_without_the_internet_the_router_is_out_of_reach():
    assert judge(replace(NO_INTERNET, route=False, gateway=None, gateway_ms=None)) == "router"


def test_a_gateway_that_answered_earlier_and_not_now_puts_the_router_out_of_reach():
    reading = replace(NO_INTERNET, gateway_ms=None)

    assert judge(reading, gateway_seen=True) == "router"


def test_a_gateway_that_never_answered_is_never_blamed():
    # Many routers ignore pings.
    assert judge(replace(NO_INTERNET, gateway_ms=None), gateway_seen=False) == "offline"


def test_an_answering_gateway_without_the_internet_is_the_internet_cut_off():
    assert judge(NO_INTERNET, gateway_seen=True) == "offline"


def test_without_a_gateway_on_the_route_a_silent_internet_is_cut_off():
    assert judge(replace(NO_INTERNET, gateway=None, gateway_ms=None)) == "offline"


def test_a_route_without_the_internet_is_cut_off_even_when_the_anchors_answered_nothing_else():
    # An answering anchor is the internet up: a route lookup that failed means nothing then.
    assert judge(replace(FINE, route=False)) == "ok"


def test_a_failed_lookup_with_the_internet_up_is_dns():
    assert judge(replace(FINE, dns_checked=True, dns_ms=None)) == "dns"


def test_a_lookup_not_made_is_never_dns():
    assert judge(replace(FINE, dns_checked=False, dns_ms=None)) == "ok"


def test_slow_is_the_median_of_the_recent_round_trips_with_this_one_above_the_threshold():
    assert judge(replace(FINE, anchor_ms=40.0), recent=(300.0, 250.0)) == "slow"
    # One slow check among quick ones is no slow internet.
    assert judge(replace(FINE, anchor_ms=900.0), recent=(30.0, 25.0)) == "ok"
    assert judge(replace(FINE, anchor_ms=200.0), recent=()) == "ok"  # at the threshold
    assert judge(replace(FINE, anchor_ms=201.0), recent=()) == "slow"


@pytest.mark.parametrize("hoyo", [(45.0, None, 52.0), (None, None, None), (250.0, 300.0, 40.0)])
def test_a_silent_or_slow_hoyo_host_with_the_internet_up_is_hoyo(hoyo):
    assert judge(replace(FINE, hoyo_ms=hoyo)) == "hoyo"


def test_answering_hoyo_hosts_are_ok():
    assert judge(replace(FINE, hoyo_ms=HOYO_FINE)) == "ok"


def test_the_problems_come_in_order_router_offline_dns_slow_hoyo():
    everything = replace(FINE, dns_checked=True, dns_ms=None, hoyo_ms=(None,), anchor_ms=500.0)
    assert judge(everything) == "dns"
    assert judge(replace(everything, dns_ms=10.0)) == "slow"
    assert judge(replace(everything, anchor_ms=None), gateway_seen=True) == "offline"


def test_every_status_has_its_indonesian_meaning_and_two_are_down():
    assert MEANINGS == {
        "ok": "lancar",
        "slow": "lambat",
        "dns": "DNS bermasalah",
        "hoyo": "server HoYo bermasalah",
        "offline": "internet putus",
        "router": "router tidak terjangkau",
    }
    assert {"router", "offline"} == DOWN


# --- the state ---------------------------------------------------------------------------------


def test_the_state_remembers_each_gateway_that_answered():
    state = NetworkState(slow_window=5)

    state.note(replace(FINE, gateway="192.168.1.1", gateway_ms=None), "ok")
    assert not state.gateway_answered("192.168.1.1")
    state.note(FINE, "ok")

    assert state.gateway_answered("192.168.1.1")
    assert not state.gateway_answered("10.0.0.1")


def test_the_state_keeps_the_round_trips_of_the_last_window_less_one_checks():
    state = NetworkState(slow_window=3)

    for took in (10.0, 20.0, None, 30.0):
        state.note(replace(FINE, anchor_ms=took), "ok")

    assert state.recent_ms() == (20.0, 30.0)


def test_with_a_window_of_one_no_earlier_round_trip_is_kept():
    state = NetworkState(slow_window=1)

    state.note(FINE, "ok")

    assert state.recent_ms() == ()


def test_the_state_says_down_while_the_last_check_failed():
    state = NetworkState(slow_window=5)
    assert not state.down()

    state.note(NO_INTERNET, "offline")
    assert state.down()
    state.note(FINE, "slow")
    assert not state.down()


def test_a_follower_hears_each_check_that_changes_whether_the_internet_is_down():
    state = NetworkState(slow_window=5)
    heard: list[bool] = []
    state.follow(heard.append)

    for reading, status in [
        (FINE, "ok"),
        (NO_INTERNET, "offline"),
        (NO_INTERNET, "router"),
        (FINE, "dns"),
        (FINE, "ok"),
    ]:
        state.note(reading, status)

    assert heard == [True, False]


def test_the_state_hears_from_the_play_time_polls_whether_a_hoyo_game_runs():
    state = NetworkState(slow_window=5)
    assert not state.hoyo_playing()

    state.observe(True, False)
    assert state.hoyo_playing()
    state.observe(False, False)
    assert not state.hoyo_playing()


def test_an_outage_the_watcher_confirmed_is_kept_until_it_ends():
    state = NetworkState(slow_window=5)
    assert state.outage() is None

    state.open_outage(SINCE, "offline")
    assert state.outage() == CurrentOutage(SINCE, "offline")
    state.close_outage()
    assert state.outage() is None


def test_the_brain_s_note_names_an_open_outage_and_its_start():
    state = NetworkState(slow_window=5)
    assert state.outage_note() is None

    state.open_outage(SINCE, "offline")
    assert state.outage_note() == "Internetnya lagi putus sejak jam 21.03."
    state.open_outage(SINCE, "router")
    assert state.outage_note() == (
        "Internetnya lagi putus sejak jam 21.03: PC tidak tersambung ke router."
    )


def test_before_an_outage_is_confirmed_the_brain_s_note_hedges_on_the_last_failed_check():
    # A failed Claude connect is fresh evidence too: the first minutes are when Master notices.
    state = NetworkState(slow_window=5)

    state.note(NO_INTERNET, "offline")
    assert state.outage_note() == "Sepertinya internetnya lagi putus."
    state.note(NO_INTERNET, "router")
    assert state.outage_note() == (
        "Sepertinya internetnya lagi putus: PC tidak tersambung ke router."
    )
    state.note(FINE, "ok")
    assert state.outage_note() is None


def test_a_confirmed_outage_s_note_wins_over_the_hedge():
    state = NetworkState(slow_window=5)
    state.note(NO_INTERNET, "router")

    state.open_outage(SINCE, "offline")

    assert state.outage_note() == "Internetnya lagi putus sejak jam 21.03."


def test_the_state_is_safe_from_many_threads():
    state = NetworkState(slow_window=5)

    def note_many() -> None:
        for _ in range(500):
            state.note(FINE, "ok")
            state.recent_ms()

    threads = [threading.Thread(target=note_many) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state.recent_ms() == (24.0,) * 4


# --- a check when asked ------------------------------------------------------------------------


class FakeProbe:
    def __init__(self, *readings: Reading) -> None:
        self.readings = list(readings)
        self.asked: list[dict] = []

    def check(self, *, dns: bool = False, hoyo: bool = False) -> Reading:
        self.asked.append({"dns": dns, "hoyo": hoyo})
        return self.readings.pop(0)


def test_a_check_asked_for_looks_up_the_name_and_is_noted_in_the_state():
    state = NetworkState(slow_window=3)
    reading = replace(FINE, anchor_ms=20.0, dns_checked=True, dns_ms=12.0)
    probe = FakeProbe(reading)

    found = NetworkCheck(probe, state, slow_ms=200).now(hoyo=True)

    assert probe.asked == [{"dns": True, "hoyo": True}]
    assert found == Finding(reading, "ok", 20.0, None)
    assert state.recent_ms() == (20.0,)


def quick_polls(state: NetworkState, *took: float) -> None:
    for each in took:
        state.note(replace(FINE, anchor_ms=each), "ok")


def test_a_check_asked_for_is_slow_by_its_own_round_trip_after_quick_polls():
    # "Kok lemot?" asks about now: four quick polls must not outvote it.
    state = NetworkState(slow_window=5)
    quick_polls(state, 20.0, 21.0, 22.0, 23.0)
    reading = replace(FINE, anchor_ms=450.0, dns_checked=True, dns_ms=12.0)

    found = NetworkCheck(FakeProbe(reading), state, slow_ms=200).now(hoyo=False)

    assert (found.status, found.judged_ms) == ("slow", 450.0)


def test_a_check_asked_for_is_quick_by_its_own_round_trip_once_a_slow_spell_ended():
    state = NetworkState(slow_window=5)
    quick_polls(state, 400.0, 420.0, 450.0, 430.0)
    reading = replace(FINE, anchor_ms=20.0, dns_checked=True, dns_ms=12.0)

    found = NetworkCheck(FakeProbe(reading), state, slow_ms=200).now(hoyo=False)

    assert (found.status, found.judged_ms) == ("ok", 20.0)


def test_the_watcher_s_poll_makes_no_lookup_and_is_slow_by_the_median_with_the_recent_ones():
    state = NetworkState(slow_window=3)
    quick_polls(state, 300.0, 260.0)
    probe = FakeProbe(replace(FINE, anchor_ms=20.0))

    found = NetworkCheck(probe, state, slow_ms=200).poll(hoyo=False)

    assert probe.asked == [{"dns": False, "hoyo": False}]
    assert (found.status, found.judged_ms) == ("slow", 260.0)
    assert state.recent_ms() == (260.0, 20.0)


def test_a_check_blames_the_router_only_once_its_gateway_answered_in_this_run():
    state = NetworkState(slow_window=5)
    check = NetworkCheck(
        FakeProbe(NO_INTERNET, FINE, replace(FINE, gateway_ms=None, anchor_ms=None)),
        state,
        slow_ms=200,
    )

    assert check.now(hoyo=False).status == "offline"
    check.now(hoyo=False)
    assert check.now(hoyo=False).status == "router"


def test_a_check_of_the_hoyo_hosts_tells_how_many_answered_and_how_quickly():
    reading = replace(FINE, hoyo_ms=(250.0, None, 300.0))

    found = NetworkCheck(FakeProbe(reading), NetworkState(slow_window=5), slow_ms=200).now(
        hoyo=True
    )

    assert found.hoyo == HoyoFinding(answered=2, of=3, median_ms=275.0, slow=True)
    assert found.hoyo.trouble


def test_answering_hoyo_hosts_are_no_trouble_and_silent_ones_have_no_median():
    assert not hoyo_finding((45.0, 40.0, 52.0), 200).trouble
    assert hoyo_finding((None, None), 200) == HoyoFinding(0, 2, None, False)


def test_a_check_carries_the_outage_the_watcher_confirmed():
    state = NetworkState(slow_window=5)
    state.open_outage(SINCE, "offline")

    finding = NetworkCheck(FakeProbe(NO_INTERNET), state, slow_ms=200).now(hoyo=False)

    assert finding.outage == CurrentOutage(SINCE, "offline")
    assert state.down()


# --- her words ---------------------------------------------------------------------------------


def finding(reading: Reading, status: str, judged: float | None = None, outage=None) -> Finding:
    hoyo = None if reading.hoyo_ms is None else hoyo_finding(reading.hoyo_ms, 200)
    return Finding(reading, status, judged, outage, hoyo)


def test_with_a_normal_round_trip_she_says_what_she_measured_and_what_else_may_be_slow():
    # "Kok lemot?" with a normal ping: the speed or the app may still be slow, which she cannot
    # measure, so she never says flatly that the internet is fine.
    said = answer_line(finding(FINE, "ok", 24.4), "internet", "master")

    assert said == (
        "Master, ping internetnya normal, sekitar 24 milidetik. Kalau masih terasa lemot, mungkin "
        "kecepatannya yang turun, atau dari aplikasinya."
    )


def test_she_says_a_slow_internet_with_the_round_trip_she_judged_by():
    said = answer_line(finding(FINE, "slow", 349.6), "internet", "Master")

    assert said == (
        "Master, internetnya lagi lambat, ping-nya sekitar 350 milidetik. Mungkin ada yang sedang "
        "download, atau jaringannya lagi padat."
    )


def test_she_says_the_router_is_out_of_reach_and_what_to_check():
    said = answer_line(finding(NO_INTERNET, "router"), "internet", "sayang")

    assert said == "Sayang, router tidak terjangkau. Coba cek Wi-Fi, kabel, atau routernya."


OFFLINE_ADVICE = (
    "Coba restart modem atau routernya. Kalau tetap putus, mungkin ada gangguan dari provider."
)


def test_she_says_the_internet_is_cut_off_beyond_an_answering_router_and_what_to_try():
    said = answer_line(finding(NO_INTERNET, "offline"), "internet", "Master")

    assert said == (
        "Master, internetnya putus: routernya tersambung, tapi internet di luar tidak bisa "
        f"dihubungi. {OFFLINE_ADVICE}"
    )


def test_without_an_answering_router_she_says_only_that_the_internet_is_cut_off():
    reading = replace(NO_INTERNET, gateway_ms=None)

    said = answer_line(finding(reading, "offline"), "internet", "Master")

    assert (
        said
        == f"Master, internetnya putus: internet di luar tidak bisa dihubungi. {OFFLINE_ADVICE}"
    )


def test_with_an_outage_open_she_says_since_when_before_what_to_try():
    outage = CurrentOutage(SINCE, "offline")

    said = answer_line(finding(NO_INTERNET, "offline", outage=outage), "internet", "Master")

    assert said.endswith(f"tidak bisa dihubungi. Putusnya sejak jam 21.03. {OFFLINE_ADVICE}")


def test_she_says_dns_is_failing_whatever_was_asked():
    reading = replace(FINE, dns_checked=True, dns_ms=None)

    for topic in ("internet", "hoyo"):
        assert answer_line(finding(reading, "dns", 24.0), topic, "Master") == (
            "Master, internetnya tersambung, tapi DNS-nya bermasalah, jadi alamat situs tidak "
            "bisa dicari. Coba restart routernya, atau ganti DNS-nya ke Cloudflare atau Google."
        )


def test_asked_about_hoyo_she_says_its_hosts_answer_and_that_they_are_not_the_game_server():
    reading = replace(FINE, hoyo_ms=HOYO_FINE)

    said = answer_line(finding(reading, "ok", 24.0), "hoyo", "Master")

    assert said == (
        "Master, ping internetnya normal, sekitar 24 milidetik. Server HoYoLAB dan HoYoverse "
        "menjawab, sekitar 45 milidetik. Itu server HoYoLAB dan HoYoverse, bukan server game-nya."
    )


@pytest.mark.parametrize(
    ("hoyo", "told"),
    [
        (
            (45.0, None, 52.0),
            "Server HoYoLAB dan HoYoverse lagi bermasalah: 1 dari 3 tidak menjawab.",
        ),
        ((None, None, None), "Server HoYoLAB dan HoYoverse tidak menjawab."),
        ((420.0, 380.0, 500.0), "Server HoYoLAB dan HoYoverse lagi lambat, sekitar 420 milidetik."),
    ],
)
def test_she_says_how_the_hoyo_hosts_are_failing(hoyo, told):
    reading = replace(FINE, hoyo_ms=hoyo)

    said = answer_line(finding(reading, "hoyo", 24.0), "hoyo", "Master")

    assert said == (
        f"Master, ping internetnya normal, sekitar 24 milidetik. {told} Itu server HoYoLAB dan "
        "HoYoverse, bukan server game-nya."
    )


def test_asked_about_hoyo_with_the_internet_down_she_says_the_internet():
    said = answer_line(finding(NO_INTERNET, "offline"), "hoyo", "Master")

    assert said.startswith("Master, internetnya putus:")
    assert "HoYo" not in said


def test_hoyo_hosts_checked_for_the_internet_question_are_said_without_the_game_note():
    reading = replace(FINE, hoyo_ms=(None, None, None))

    said = answer_line(finding(reading, "hoyo", 24.0), "internet", "Master")

    assert said == (
        "Master, ping internetnya normal, sekitar 24 milidetik. Server HoYoLAB dan HoYoverse "
        "tidak menjawab."
    )


def test_she_never_says_an_address():
    for status, reading in (("ok", FINE), ("offline", NO_INTERNET), ("router", NO_INTERNET)):
        said = answer_line(finding(reading, status, 24.0), "internet", "Master")
        assert "192.168" not in said


def test_the_back_line_says_how_long_and_since_when_without_parentheses():
    # Her voice drops whatever is in parentheses.
    assert back_line("master", SINCE, 12) == (
        "Master, internetnya sudah nyambung lagi, tadi putus sekitar 12 menit, sejak jam 21.03."
    )


def test_the_back_line_says_a_long_outage_in_hours_and_minutes_like_the_recap():
    assert back_line("master", SINCE, 125) == (
        "Master, internetnya sudah nyambung lagi, tadi putus sekitar 2 jam 5 menit, sejak jam "
        "21.03."
    )
    assert "sekitar 1 jam, sejak" in back_line("master", SINCE, 60)
