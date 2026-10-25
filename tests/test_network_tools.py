import json
import logging
from dataclasses import replace
from datetime import datetime

import pytest

from waifu.brain.network_tools import NETWORK_STATUS, NetworkTools
from waifu.brain.tools import to_anthropic
from waifu.finance.values import WIB
from waifu.network import NetworkCheck, NetworkState
from waifu.network_history import NetworkRecord, OpenOutage, Outage
from waifu.network_probe import Reading

pytestmark = pytest.mark.usefixtures("nothing_beyond_this_pc")

NOW = datetime(2026, 10, 25, 21, 15, tzinfo=WIB)
FINE = Reading(
    at=NOW,
    route=True,
    gateway="192.168.1.1",
    gateway_ms=1.0,
    anchor_ms=24.4,
    dns_checked=True,
    dns_ms=12.0,
    hoyo_ms=(45.0, 40.0, 52.0),
)


class FakeProbe:
    def __init__(self, reading: Reading | Exception = FINE) -> None:
        self.reading = reading
        self.asked: list[dict] = []

    def check(self, *, dns: bool = False, hoyo: bool = False) -> Reading:
        self.asked.append({"dns": dns, "hoyo": hoyo})
        if isinstance(self.reading, Exception):
            raise self.reading
        return self.reading


class FakeHistory:
    def __init__(self, record: NetworkRecord | Exception | None = None) -> None:
        self.result = NetworkRecord() if record is None else record

    def record(self) -> NetworkRecord:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make_tools(
    probe: FakeProbe | None = None,
    history: FakeHistory | None = None,
    state: NetworkState | None = None,
) -> tuple[NetworkTools, FakeProbe]:
    probe = probe or FakeProbe()
    check = NetworkCheck(probe, state or NetworkState(slow_window=5), slow_ms=200)
    return NetworkTools(check, history or FakeHistory(), now=lambda: NOW), probe


def result(tools: NetworkTools, arguments: object = None) -> tuple[dict, bool]:
    payload, is_error = tools.run(NETWORK_STATUS, {} if arguments is None else arguments)
    return json.loads(payload), is_error


def test_the_spec_takes_no_argument_and_is_not_strict():
    tools, _ = make_tools()

    [spec] = tools.specs()

    assert spec.name == NETWORK_STATUS
    assert spec.input_schema == {"type": "object", "properties": {}, "additionalProperties": False}
    assert to_anthropic(spec)["strict"] is False
    assert tools.specs() == [spec]


def test_the_description_keeps_the_hoyo_hosts_apart_from_the_game_and_names_no_address():
    tools, _ = make_tools()

    [spec] = tools.specs()

    assert "not the game server" in spec.description
    assert "Never say an IP address" in spec.description
    assert "internetnya lemot" in spec.description


def test_it_checks_now_with_the_lookup_and_the_hoyo_hosts():
    tools, probe = make_tools()

    data, is_error = result(tools)

    assert probe.asked == [{"dns": True, "hoyo": True}]
    assert is_error is False
    assert data == {
        "ok": True,
        "status": "ok",
        "meaning": "lancar",
        "internet_ms": 24,
        "router": "answered",
        "dns": "ok",
        "hoyo_servers": {"answered": 3, "of": 3, "median_ms": 45},
    }


def test_the_round_trip_told_is_this_check_s_whatever_the_watcher_s_polls_found():
    state = NetworkState(slow_window=5)
    for took in (20.0, 21.0, 22.0, 23.0):
        state.note(replace(FINE, anchor_ms=took), "ok")
    tools, _ = make_tools(FakeProbe(replace(FINE, anchor_ms=450.0)), state=state)

    data, _ = result(tools)

    assert (data["status"], data["internet_ms"]) == ("slow", 450)


def test_the_description_says_the_round_trip_is_now_and_for_the_ping_question():
    tools, _ = make_tools()

    [spec] = tools.specs()

    assert "`internet_ms` is the round trip to the internet right now" in spec.description
    assert 'say it for "ping-ku berapa?"' in spec.description


def test_the_description_says_a_normal_round_trip_is_no_answer_to_a_slow_download():
    tools, _ = make_tools()

    [spec] = tools.specs()

    assert "It measures delay, not download speed" in spec.description


def test_a_cut_off_internet_says_what_answered_and_never_an_address():
    reading = replace(
        FINE, anchor_ms=None, gateway_ms=None, dns_ms=None, hoyo_ms=(None, None, None)
    )
    tools, _ = make_tools(FakeProbe(reading))

    data, _ = result(tools)

    assert data["status"] == "offline"
    assert data["meaning"] == "internet putus"
    assert (data["internet_ms"], data["router"], data["dns"]) == (None, "no_reply", "failed")
    assert data["hoyo_servers"] == {"answered": 0, "of": 3, "median_ms": None}
    assert "192.168" not in json.dumps(data)


def test_a_route_without_a_gateway_has_no_router():
    tools, _ = make_tools(FakeProbe(replace(FINE, gateway=None, gateway_ms=None)))

    assert result(tools)[0]["router"] == "none"


def test_an_outage_the_watcher_confirmed_is_told_with_its_start_and_length():
    state = NetworkState(slow_window=5)
    state.open_outage(datetime(2026, 10, 25, 21, 3, tzinfo=WIB), "offline")
    tools, _ = make_tools(FakeProbe(replace(FINE, anchor_ms=None)), state=state)

    data, _ = result(tools)

    assert data["outage_now"] == {"since": "21:03", "minutes": 12}


def test_today_s_outages_are_told_only_when_there_were_any():
    record = NetworkRecord(
        outages=(
            Outage(
                datetime(2026, 10, 24, 23, 50, tzinfo=WIB), NOW.replace(hour=0, minute=5), "offline"
            ),
            Outage(NOW.replace(hour=8, minute=0), NOW.replace(hour=8, minute=30), "router"),
        ),
        open=OpenOutage(NOW.replace(hour=21, minute=3), NOW.replace(hour=21, minute=10), "offline"),
    )
    tools, _ = make_tools(history=FakeHistory(record))

    assert result(tools)[0]["outages_today"] == {"count": 3, "minutes": 5 + 30 + 7}
    assert "outages_today" not in result(make_tools()[0])[0]


def test_an_unreadable_history_is_left_out_and_logged(caplog):
    tools, _ = make_tools(history=FakeHistory(OSError("locked")))

    with caplog.at_level(logging.WARNING, logger="waifu.brain.network_tools"):
        data, is_error = result(tools)

    assert is_error is False
    assert "outages_today" not in data
    assert "Could not read today's internet outages" in caplog.text


@pytest.mark.parametrize("arguments", [{"hoyo": True}, [], "now"])
def test_an_argument_is_an_error(arguments):
    tools, probe = make_tools()

    data, is_error = result(tools, arguments)

    assert is_error is True
    assert data["message"].startswith("Argumen network_status tidak valid:")
    assert probe.asked == []


def test_a_check_that_fails_unexpectedly_is_an_error_result_and_logged(caplog):
    tools, _ = make_tools(FakeProbe(RuntimeError("unexpected")))

    with caplog.at_level(logging.ERROR, logger="waifu.brain.network_tools"):
        data, is_error = result(tools)

    assert (is_error, data["message"]) == (True, "Internet tidak bisa dicek saat ini.")
    assert "network_status" in caplog.text


def test_another_tool_s_name_is_an_error():
    tools, _ = make_tools()

    payload, is_error = tools.run("pc_health", {})

    assert is_error is True
    assert "bukan tool internet" in json.loads(payload)["message"]
