import copy
import json
import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from waifu import __version__
from waifu.finance.tepira import TepiraClient
from waifu.finance.values import (
    NOT_ACTIVE,
    RATE_LIMITED,
    RECORD_UNCONFIRMED,
    TOKEN_REJECTED,
    UNDO_EXPIRED,
    UNDO_UNCONFIRMED,
    UNREACHABLE,
    UNREADABLE,
    WRITES_OFF,
    FinanceError,
    RecordedExpense,
    UndoneExpense,
)

SECRET = "SECRETTEPIRA"
TOKEN = SecretStr(f"tpcy_{SECRET}" + "Ab3-_" * 6 + "Z")  # made up: 43 characters after tpcy_
URL = "https://tepira.asepsunarya.com"
LOGGER = "waifu.finance.tepira"

# Answers as tepira PR #21's routes build them (app/api/cyrene/**/route.ts).
SUMMARY = {
    "ok": True,
    "as_of": "2026-10-16T09:12:00+07:00",
    "period": {"month": 10, "year": 2026, "month_basis": "web", "day_basis": "wib"},
    "spent": {
        "total": 2350000,
        "count": 41,
        "gaji": 2100000,
        "bonus": 250000,
        "today": 45000,
        "yesterday": 120000,
        "this_week": 310000,
    },
    "income": {
        "salary": 5000000,
        "bonus": 500000,
        "starting_balance": 1000000,
        "net": 3150000,
        "ending_estimate": 4150000,
    },
    "savings_allocation": 1000000,
    "budget": {
        "basis": "gaji+bonus",
        "allocated": 3000000,
        "spent": 2350000,
        "remaining": 650000,
        "used_percent": 78.3,
        "status": "WITHIN_BUDGET",
    },
    "categories": [
        {
            "category": "Makan harian",
            "allocated": 1200000,
            "spent": 980000,
            "remaining": 220000,
            "used_percent": 81.7,
            "over": False,
        },
        {
            "category": "Nongkrong / jajan",
            "allocated": 300000,
            "spent": 342000,
            "remaining": -42000,
            "used_percent": 114,
            "over": True,
        },
    ],
    "other_spent": {"uncategorized": 35000, "unbudgeted": 0},
    "top": [{"category": "Makan harian", "spent": 980000}, {"category": None, "spent": 35000}],
    "insight": {"week_start": "2026-10-12", "health_score": 72, "status": "SEHAT"},
}
WALLETS = {
    "ok": True,
    "as_of": "2026-10-16T09:12:00+07:00",
    "wallets": [
        {"id": 1, "name": "GoPay", "type": "e-wallet", "balance": 250000, "is_debt": False},
        {"id": 2, "name": "BCA", "type": "bank", "balance": 5000000, "is_debt": False},
        {"id": 3, "name": "Kartu kredit", "type": "credit", "balance": -1200000, "is_debt": True},
    ],
    "more": 0,
    "totals": {"assets": 5250000, "debts": 1200000, "net_worth": 4050000},
}
GOALS = {
    "ok": True,
    "as_of": "2026-10-16T09:12:00+07:00",
    "goals": [
        {
            "id": 4,
            "title": "Laptop baru",
            "target": 15000000,
            "saved": 6200000,
            "remaining": 8800000,
            "percent": 41.3,
            "deadline": "2027-03-31",
            "days_left": 166,
            "per_month_needed": 1600000,
            "saved_this_month": 500000,
            "last_contribution": {"date": "2026-10-01", "amount": 500000},
        }
    ],
    "more": 0,
}
TRANSACTIONS = {
    "ok": True,
    "as_of": "2026-10-16T09:12:00+07:00",
    "transactions": [
        {
            "id": 812,
            "date": "2026-10-16 08:41",
            "amount": 18000,
            "category": "Nongkrong / jajan",
            "note": "kopi susu",
            "wallet": "GoPay",
            "source": "gaji",
        },
        {
            "id": 811,
            "date": "2026-10-15 19:02",
            "amount": 35000,
            "category": None,
            "note": None,
            "wallet": None,
            "source": "gaji",
        },
    ],
}
EXPENSE = {
    "id": 813,
    "amount": 18000,
    "category": "Nongkrong / jajan",
    "note": "jajan kopi",
    "date": "2026-10-16 09:12",
    "wallet": "GoPay",
}
RECORDED = {"ok": True, "expense": EXPENSE, "deduped": False}
DEDUPED = {"ok": True, "expense": EXPENSE, "deduped": True}
DELETED = {"ok": True, "deleted": {"id": 813, "amount": 18000}}

READS: dict[str, tuple[Callable[[TepiraClient], Any], dict]] = {
    "summary": (lambda client: client.summary(10, 2026), SUMMARY),
    "wallets": (lambda client: client.wallets(), WALLETS),
    "goals": (lambda client: client.goals("laptop"), GOALS),
    "transactions": (lambda client: client.transactions(5, None), TRANSACTIONS),
}


def record(client: TepiraClient) -> RecordedExpense:
    return client.record(18000, "Nongkrong / jajan", "jajan kopi", "GoPay")


def undo(client: TepiraClient) -> UndoneExpense:
    return client.undo(813)


# Every call with a good answer for it: (call, answer, method, path).
CALLS = {
    **{
        name: (call, answer, "GET", f"/api/cyrene/{name}") for name, (call, answer) in READS.items()
    },
    "record": (record, lambda: httpx.Response(201, json=RECORDED), "POST", "/api/cyrene/expenses"),
    "undo": (undo, DELETED, "DELETE", "/api/cyrene/expenses/813"),
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass(frozen=True)
class Took:
    """A reply that comes `seconds` after the request was sent."""

    seconds: float
    reply: Any


# A JSON value (HTTP 200), an httpx.Response, an exception to raise, a Took, or a function
# returning one of those (a fresh Response or exception for every request).
Reply = Any


class FakeTepira:
    """tepira behind httpx's MockTransport: nothing leaves this PC.

    Each request takes the next reply; the last one answers every request after it.
    """

    def __init__(self, *replies: Reply, url: str = URL, follow_redirects: bool = False) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.clock = FakeClock()
        self.http = httpx.Client(
            transport=httpx.MockTransport(self._handle), follow_redirects=follow_redirects
        )
        self.client = TepiraClient(self.http, url, TOKEN, clock=self.clock)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Took):
            self.clock.now += reply.seconds
            reply = reply.reply
        if callable(reply):
            reply = reply()
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)


def without_ok(answer: dict) -> dict:
    return {key: value for key, value in answer.items() if key != "ok"}


def error_json(status: int, error: str, **extra: Any) -> httpx.Response:
    return httpx.Response(status, json={"ok": False, "error": error, **extra})


def html(status: int) -> httpx.Response:
    return httpx.Response(status, html=f"<!DOCTYPE html><html><body>{status}</body></html>")


def client_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == LOGGER]


def failure(call: Callable[[TepiraClient], Any], tepira: FakeTepira) -> FinanceError:
    with pytest.raises(FinanceError) as caught:
        call(tepira.client)
    return caught.value


# --- what is asked ---------------------------------------------------------------------------


def test_summary_asks_for_the_month_and_year_it_is_given():
    tepira = FakeTepira(SUMMARY)

    assert tepira.client.summary(9, 2026) == without_ok(SUMMARY)

    (request,) = tepira.requests
    assert request.method == "GET"
    assert request.url.copy_with(query=None) == f"{URL}/api/cyrene/summary"
    assert list(request.url.params.multi_items()) == [("month", "9"), ("year", "2026")]


def test_wallets_asks_with_no_query():
    tepira = FakeTepira(WALLETS)

    assert tepira.client.wallets() == without_ok(WALLETS)

    (request,) = tepira.requests
    assert request.method == "GET"
    assert str(request.url) == f"{URL}/api/cyrene/wallets"


def test_goals_sends_q_only_when_a_query_is_given():
    tepira = FakeTepira(GOALS)

    assert tepira.client.goals("laptop") == without_ok(GOALS)
    tepira.client.goals(None)

    with_query, without_query = tepira.requests
    assert with_query.url.copy_with(query=None) == f"{URL}/api/cyrene/goals"
    assert list(with_query.url.params.multi_items()) == [("q", "laptop")]
    assert str(without_query.url) == f"{URL}/api/cyrene/goals"


def test_transactions_sends_the_limit_and_the_category_only_when_given():
    tepira = FakeTepira(TRANSACTIONS)

    assert tepira.client.transactions(5, None) == without_ok(TRANSACTIONS)
    tepira.client.transactions(10, "Pulsa & Internet")
    tepira.client.transactions(3, "uncategorized")

    plain, labelled, uncategorized = tepira.requests
    assert plain.url.copy_with(query=None) == f"{URL}/api/cyrene/transactions"
    assert list(plain.url.params.multi_items()) == [("limit", "5")]
    assert list(labelled.url.params.multi_items()) == [
        ("limit", "10"),
        ("category", "Pulsa & Internet"),
    ]
    assert list(uncategorized.url.params.multi_items()) == [
        ("limit", "3"),
        ("category", "uncategorized"),
    ]


def test_record_posts_the_expense_as_json_and_never_a_date():
    tepira = FakeTepira(lambda: httpx.Response(201, json=RECORDED))

    recorded = tepira.client.record(18000, "Nongkrong / jajan", "jajan kopi", "GoPay")

    assert recorded == RecordedExpense(
        id=813,
        amount=18000,
        category="Nongkrong / jajan",
        note="jajan kopi",
        date="2026-10-16 09:12",
        wallet="GoPay",
        deduped=False,
    )
    (request,) = tepira.requests
    assert request.method == "POST"
    assert str(request.url) == f"{URL}/api/cyrene/expenses"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "amount": 18000,
        "wallet": "GoPay",
        "source": "gaji",
        "category": "Nongkrong / jajan",
        "note": "jajan kopi",
    }


def test_record_leaves_out_a_category_and_a_note_it_was_not_given():
    answer = {**RECORDED, "expense": {**EXPENSE, "category": None, "note": None}}
    tepira = FakeTepira(lambda: httpx.Response(201, json=answer))

    recorded = tepira.client.record(18000, None, None, "GoPay")

    assert (recorded.category, recorded.note) == (None, None)
    assert json.loads(tepira.requests[0].content) == {
        "amount": 18000,
        "wallet": "GoPay",
        "source": "gaji",
    }


def test_record_sends_the_source_it_is_given():
    tepira = FakeTepira(lambda: httpx.Response(201, json=RECORDED))

    tepira.client.record(18000, None, "jajan kopi", "GoPay", source="bonus")

    assert json.loads(tepira.requests[0].content)["source"] == "bonus"


def test_record_reports_the_wallet_as_tepira_writes_it():
    tepira = FakeTepira(lambda: httpx.Response(201, json=RECORDED))

    recorded = tepira.client.record(18000, None, "jajan kopi", "gopay")

    assert json.loads(tepira.requests[0].content)["wallet"] == "gopay"
    assert recorded.wallet == "GoPay"


def test_a_repeat_tepira_already_had_is_a_deduplicated_expense():
    tepira = FakeTepira(DEDUPED)  # HTTP 200, not 201

    recorded = record(tepira.client)

    assert recorded.deduped is True
    assert recorded.id == 813


def test_undo_deletes_the_expense_by_its_id():
    tepira = FakeTepira(DELETED)

    assert tepira.client.undo(813) == UndoneExpense(id=813, amount=18000)

    (request,) = tepira.requests
    assert request.method == "DELETE"
    assert str(request.url) == f"{URL}/api/cyrene/expenses/813"
    assert request.content == b""


@pytest.mark.parametrize("name", sorted(CALLS))
def test_every_call_sends_the_token_only_in_the_bearer_header(name):
    call, answer, method, path = CALLS[name]
    tepira = FakeTepira(answer)

    call(tepira.client)

    (request,) = tepira.requests
    assert (request.method, request.url.path) == (method, path)
    assert request.headers["Authorization"] == f"Bearer {TOKEN.get_secret_value()}"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == f"waifu-companion/{__version__}"
    assert SECRET not in str(request.url)
    assert SECRET not in request.content.decode()


@pytest.mark.parametrize("name", sorted(CALLS))
def test_every_call_has_eight_seconds(name):
    call, answer, _, _ = CALLS[name]
    tepira = FakeTepira(answer)

    call(tepira.client)

    assert tepira.requests[0].extensions["timeout"] == httpx.Timeout(8.0).as_dict()


def test_a_trailing_slash_on_the_url_changes_nothing():
    tepira = FakeTepira(WALLETS, url=f"{URL}/")

    tepira.client.wallets()

    assert str(tepira.requests[0].url) == f"{URL}/api/cyrene/wallets"


def test_the_token_never_goes_to_a_plain_http_url():
    http = httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("sent")))

    with pytest.raises(ValueError, match="https") as caught:
        TepiraClient(http, "http://tepira.asepsunarya.com", TOKEN)

    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_is_never_followed(status):
    redirect = lambda: httpx.Response(status, headers={"Location": f"{URL}/login"})  # noqa: E731
    tepira = FakeTepira(redirect, WALLETS, follow_redirects=True)

    error = failure(READS["wallets"][0], tepira)

    assert (error.kind, error.message) == ("unreadable", UNREADABLE)
    assert len(tepira.requests) == 1


def test_the_client_never_closes_the_http_client_it_was_given():
    tepira = FakeTepira(lambda: httpx.Response(503), WALLETS)

    failure(READS["wallets"][0], tepira)
    tepira.client.wallets()

    assert not tepira.http.is_closed


# --- arguments tepira would refuse -----------------------------------------------------------

INVALID_CALLS: dict[str, Callable[[TepiraClient], Any]] = {
    "month 0": lambda client: client.summary(0, 2026),
    "month 13": lambda client: client.summary(13, 2026),
    "month as a boolean": lambda client: client.summary(True, 2026),
    "month as text": lambda client: client.summary("10", 2026),
    "year 1999": lambda client: client.summary(10, 1999),
    "year 2101": lambda client: client.summary(10, 2101),
    "query over 50 characters": lambda client: client.goals("x" * 51),
    # tepira counts JavaScript string units: an emoji is two of them.
    "query over 50 units": lambda client: client.goals("\U0001f642" * 26),
    "query not text": lambda client: client.goals(5),
    "limit 0": lambda client: client.transactions(0, None),
    "limit 11": lambda client: client.transactions(11, None),
    "limit as a boolean": lambda client: client.transactions(True, None),
    "transactions of an unknown category": lambda client: client.transactions(5, "Kopi"),
    "amount 0": lambda client: client.record(0, None, "kopi", "GoPay"),
    "amount over 5 juta": lambda client: client.record(5_000_001, None, "kopi", "GoPay"),
    "amount as a float": lambda client: client.record(18000.0, None, "kopi", "GoPay"),
    "amount as a boolean": lambda client: client.record(True, None, "kopi", "GoPay"),
    "recording uncategorized": lambda client: client.record(18000, "uncategorized", "k", "GoPay"),
    "recording an unknown category": lambda client: client.record(18000, "Makan", "k", "GoPay"),
    "empty note": lambda client: client.record(18000, None, "", "GoPay"),
    "blank note": lambda client: client.record(18000, None, "   ", "GoPay"),
    "note over 120 characters": lambda client: client.record(18000, None, "x" * 121, "GoPay"),
    "note not text": lambda client: client.record(18000, None, 5, "GoPay"),
    "empty wallet": lambda client: client.record(18000, None, "kopi", ""),
    "blank wallet": lambda client: client.record(18000, None, "kopi", "  "),
    "wallet not text": lambda client: client.record(18000, None, "kopi", None),
    "unknown source": lambda client: client.record(18000, None, "kopi", "GoPay", "tabungan"),
    "expense id 0": lambda client: client.undo(0),
    "negative expense id": lambda client: client.undo(-1),
    "expense id as a boolean": lambda client: client.undo(True),
    "expense id as text": lambda client: client.undo("813"),
}


@pytest.mark.parametrize("name", sorted(INVALID_CALLS))
def test_arguments_tepira_would_refuse_are_refused_before_anything_is_sent(name):
    tepira = FakeTepira(lambda: pytest.fail("sent to tepira"))

    with pytest.raises(ValueError):
        INVALID_CALLS[name](tepira.client)

    assert tepira.requests == []


EDGE_CALLS: dict[str, tuple[Callable[[TepiraClient], Any], Reply]] = {
    "the first month": (lambda client: client.summary(1, 2000), SUMMARY),
    "the last month": (lambda client: client.summary(12, 2100), SUMMARY),
    "a query of 50 characters": (lambda client: client.goals("x" * 50), GOALS),
    "an empty query": (lambda client: client.goals(""), GOALS),
    "one transaction": (lambda client: client.transactions(1, "Belanja kecil"), TRANSACTIONS),
    "ten transactions": (lambda client: client.transactions(10, None), TRANSACTIONS),
    "one rupiah": (lambda client: client.record(1, None, "kopi", "GoPay"), DEDUPED),
    "five juta": (lambda client: client.record(5_000_000, None, "kopi", "GoPay"), DEDUPED),
    # tepira counts a note's characters by code point (Array.from), so emoji count once.
    "a note of 120 emoji": (
        lambda client: client.record(18000, None, "\U0001f642" * 120, "GoPay"),
        DEDUPED,
    ),
    "expense id 1": (lambda client: client.undo(1), DELETED),
}


@pytest.mark.parametrize("name", sorted(EDGE_CALLS))
def test_arguments_at_tepiras_limits_are_sent(name):
    call, answer = EDGE_CALLS[name]
    tepira = FakeTepira(answer)

    call(tepira.client)

    assert len(tepira.requests) == 1


def test_an_empty_query_asks_for_every_goal():
    tepira = FakeTepira(GOALS)

    tepira.client.goals("")

    assert str(tepira.requests[0].url) == f"{URL}/api/cyrene/goals"


# --- time and retries ------------------------------------------------------------------------


@pytest.mark.parametrize("cause", [httpx.ConnectError, httpx.ConnectTimeout])
def test_a_get_that_could_not_connect_is_tried_once_more_with_the_time_left(cause, caplog):
    tepira = FakeTepira(Took(3.0, lambda: cause("no route")), WALLETS)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert tepira.client.wallets() == without_ok(WALLETS)

    first, second = tepira.requests
    assert first.extensions["timeout"] == httpx.Timeout(8.0).as_dict()
    assert second.extensions["timeout"] == httpx.Timeout(5.0).as_dict()
    assert client_lines(caplog) == ["tepira GET /api/cyrene/wallets 200 in 3.0 s"]


def test_a_get_is_tried_once_more_with_exactly_one_second_left():
    tepira = FakeTepira(Took(7.0, lambda: httpx.ConnectError("refused")), WALLETS)

    tepira.client.wallets()

    assert tepira.requests[1].extensions["timeout"] == httpx.Timeout(1.0).as_dict()


def test_a_get_is_not_tried_again_with_less_than_a_second_left(caplog):
    tepira = FakeTepira(Took(7.1, lambda: httpx.ConnectError("refused")), WALLETS)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        error = failure(READS["wallets"][0], tepira)

    assert (error.kind, error.message) == ("unreachable", UNREACHABLE)
    assert len(tepira.requests) == 1
    assert client_lines(caplog) == [
        "tepira GET /api/cyrene/wallets failed: ConnectError (unreachable)"
    ]


def test_a_get_is_tried_at_most_twice():
    tepira = FakeTepira(lambda: httpx.ConnectError("refused"))

    error = failure(READS["summary"][0], tepira)

    assert error.kind == "unreachable"
    assert len(tepira.requests) == 2


@pytest.mark.parametrize(
    "reply",
    [
        lambda: httpx.ReadTimeout("slow"),
        lambda: httpx.RemoteProtocolError("Server disconnected without sending a response."),
        lambda: httpx.Response(503),
    ],
    ids=["read timeout", "disconnected", "503"],
)
def test_a_get_that_reached_tepira_is_not_tried_again(reply):
    tepira = FakeTepira(reply, SUMMARY)

    error = failure(READS["summary"][0], tepira)

    assert error.kind == "unreachable"
    assert len(tepira.requests) == 1


@pytest.mark.parametrize("name", ["record", "undo"])
@pytest.mark.parametrize(
    "reply",
    [
        lambda: httpx.ConnectError("refused"),
        lambda: httpx.ConnectTimeout("no route"),
        lambda: httpx.ReadTimeout("slow"),
        lambda: httpx.Response(500, json={"ok": False, "error": "server_error"}),
    ],
    ids=["refused", "connect timeout", "read timeout", "500"],
)
def test_a_write_is_never_tried_again(name, reply):
    call, answer, _, _ = CALLS[name]
    tepira = FakeTepira(reply, answer)

    failure(call, tepira)

    assert len(tepira.requests) == 1


# --- answers ---------------------------------------------------------------------------------

Expected = tuple[Callable[[], Reply], str, str]  # reply, kind, message

COMMON_FAILURES: dict[str, Expected] = {
    "400 bad_request": (
        lambda: error_json(400, "bad_request", field="month"),
        "bad_request",
        "tepira menolak isian month.",
    ),
    "401 unauthorized": (
        lambda: httpx.Response(
            401,
            json={"ok": False, "error": "unauthorized"},
            headers={"WWW-Authenticate": "Bearer"},
        ),
        "token",
        TOKEN_REJECTED,
    ),
    "403": (lambda: httpx.Response(403, text="Forbidden"), "token", TOKEN_REJECTED),
    "405": (lambda: httpx.Response(405), "unreadable", UNREADABLE),
    "429 with Retry-After": (
        lambda: httpx.Response(
            429, json={"ok": False, "error": "rate_limited"}, headers={"Retry-After": "12"}
        ),
        "busy",
        RATE_LIMITED,
    ),
    "418": (lambda: httpx.Response(418, text="I'm a teapot"), "unreadable", UNREADABLE),
    "refused": (lambda: httpx.ConnectError("[WinError 10061]"), "unreachable", UNREACHABLE),
    "connect timeout": (lambda: httpx.ConnectTimeout("timed out"), "unreachable", UNREACHABLE),
}

READ_FAILURES: dict[str, Expected] = {
    **COMMON_FAILURES,
    "404 feature off": (lambda: error_json(404, "not_found"), "off", NOT_ACTIVE),
    "404 page before PR 21": (lambda: html(404), "off", NOT_ACTIVE),
    "422": (
        lambda: error_json(422, "wallet_not_found", wallets=["GoPay"]),
        "unreadable",
        UNREADABLE,
    ),
    "500 server_error": (lambda: error_json(500, "server_error"), "unreachable", UNREACHABLE),
    "502 vercel page": (lambda: html(502), "unreachable", UNREACHABLE),
    "504 vercel page": (lambda: html(504), "unreachable", UNREACHABLE),
    "200 not json": (
        lambda: httpx.Response(200, text="<html>maintenance</html>"),
        "unreadable",
        UNREADABLE,
    ),
    "200 json list": (lambda: [], "unreadable", UNREADABLE),
    "200 ok false": (lambda: {"ok": False, "error": "server_error"}, "unreadable", UNREADABLE),
    "200 json null": (lambda: None, "unreadable", UNREADABLE),
    "200 not utf-8": (
        lambda: httpx.Response(200, content=b'{"ok":true,"as_of":"\xff"}'),
        "unreadable",
        UNREADABLE,
    ),
    "200 broken gzip": (
        lambda: httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(b"not gzip")
        ),
        "unreadable",
        UNREADABLE,
    ),
    "200 over 32 KiB": (
        lambda: httpx.Response(200, content=b'{"ok":true,"pad":"' + b"x" * 33_000 + b'"}'),
        "unreadable",
        UNREADABLE,
    ),
    "read timeout": (lambda: httpx.ReadTimeout("slow"), "unreachable", UNREACHABLE),
    "disconnected": (
        lambda: httpx.RemoteProtocolError("Server disconnected without sending a response."),
        "unreachable",
        UNREACHABLE,
    ),
}


@pytest.mark.parametrize("route", sorted(READS))
@pytest.mark.parametrize("case", sorted(READ_FAILURES))
def test_a_read_that_fails_is_a_finance_error_in_indonesian(route, case, caplog):
    reply, kind, message = READ_FAILURES[case]
    tepira = FakeTepira(reply)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        error = failure(READS[route][0], tepira)

    assert (error.kind, error.message, str(error)) == (kind, message, message)
    (line,) = client_lines(caplog)
    assert line.startswith(f"tepira GET /api/cyrene/{route} failed: ")
    assert line.endswith(f" ({kind})")


WRITE_FAILURES: dict[str, Expected] = {
    **COMMON_FAILURES,
    "read timeout": (lambda: httpx.ReadTimeout("slow"), "unconfirmed", "<unconfirmed>"),
    "write timeout": (lambda: httpx.WriteTimeout("slow"), "unconfirmed", "<unconfirmed>"),
    "disconnected": (
        lambda: httpx.RemoteProtocolError("Server disconnected without sending a response."),
        "unconfirmed",
        "<unconfirmed>",
    ),
    "500 server_error": (lambda: error_json(500, "server_error"), "unconfirmed", "<unconfirmed>"),
    "502 vercel page": (lambda: html(502), "unconfirmed", "<unconfirmed>"),
    "504 vercel page": (lambda: html(504), "unconfirmed", "<unconfirmed>"),
    "200 not json": (
        lambda: httpx.Response(200, text="<html></html>"),
        "unconfirmed",
        "<unconfirmed>",
    ),
    "200 ok false": (lambda: {"ok": False}, "unconfirmed", "<unconfirmed>"),
    "201 json list": (lambda: httpx.Response(201, json=[]), "unconfirmed", "<unconfirmed>"),
    "204 no content": (lambda: httpx.Response(204), "unconfirmed", "<unconfirmed>"),
    "200 over 32 KiB": (
        lambda: httpx.Response(200, content=b'{"ok":true,"pad":"' + b"x" * 33_000 + b'"}'),
        "unconfirmed",
        "<unconfirmed>",
    ),
}
RECORD_FAILURES: dict[str, Expected] = {
    **WRITE_FAILURES,
    "404 writes off": (lambda: error_json(404, "not_found"), "writes_off", WRITES_OFF),
    "422 without a wallets list": (
        lambda: error_json(422, "unprocessable"),
        "unreadable",
        UNREADABLE,
    ),
    "422 not json": (lambda: httpx.Response(422, text="Unprocessable"), "unreadable", UNREADABLE),
}
UNDO_FAILURES: dict[str, Expected] = {
    **WRITE_FAILURES,
    "404 too late or gone": (lambda: error_json(404, "not_found"), "expired", UNDO_EXPIRED),
    "422": (
        lambda: error_json(422, "wallet_not_found", wallets=["GoPay"]),
        "unreadable",
        UNREADABLE,
    ),
}


@pytest.mark.parametrize("case", sorted(RECORD_FAILURES))
def test_a_record_that_fails_is_a_finance_error_in_indonesian(case, caplog):
    reply, kind, message = RECORD_FAILURES[case]
    message = RECORD_UNCONFIRMED if message == "<unconfirmed>" else message
    tepira = FakeTepira(reply)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        error = failure(record, tepira)

    assert (error.kind, error.message) == (kind, message)
    (line,) = client_lines(caplog)
    assert line.startswith("tepira POST /api/cyrene/expenses failed: ")
    assert line.endswith(f" ({kind})")


@pytest.mark.parametrize("case", sorted(UNDO_FAILURES))
def test_an_undo_that_fails_is_a_finance_error_in_indonesian(case, caplog):
    reply, kind, message = UNDO_FAILURES[case]
    message = UNDO_UNCONFIRMED if message == "<unconfirmed>" else message
    tepira = FakeTepira(reply)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        error = failure(undo, tepira)

    assert (error.kind, error.message) == (kind, message)
    (line,) = client_lines(caplog)
    assert line.startswith("tepira DELETE /api/cyrene/expenses/{id} failed: ")
    assert line.endswith(f" ({kind})")


@pytest.mark.parametrize(
    ("field", "named"),
    [
        ("amount", "amount"),
        ("note", "note"),
        ("x" * 40, "x" * 40),
        ("x" * 41, "permintaan"),
        ("two words", "permintaan"),
        ("amount\n", "permintaan"),
        ("jumlah_é", "permintaan"),
        ("", "permintaan"),
        (5, "permintaan"),
        (None, "permintaan"),
    ],
)
def test_a_bad_request_names_the_field_only_when_it_is_a_plain_name(field, named):
    tepira = FakeTepira(lambda: error_json(400, "bad_request", field=field))

    error = failure(record, tepira)

    assert (error.kind, error.message) == ("bad_request", f"tepira menolak isian {named}.")


@pytest.mark.parametrize(
    "reply",
    [lambda: error_json(400, "bad_request"), lambda: httpx.Response(400, text="Bad Request")],
    ids=["no field", "not json"],
)
def test_a_bad_request_without_a_field_names_the_request(reply):
    tepira = FakeTepira(reply)

    error = failure(READS["summary"][0], tepira)

    assert error.message == "tepira menolak isian permintaan."


def test_an_unknown_wallet_lists_the_wallets_tepira_has():
    tepira = FakeTepira(
        lambda: error_json(422, "wallet_not_found", wallets=["GoPay", "BCA", "Kartu kredit"])
    )

    error = failure(lambda client: client.record(18000, None, "kopi", "  Dana "), tepira)

    assert error.kind == "wallet_not_found"
    assert error.message == (
        "Dompet Dana tidak ada di tepira. Dompet yang ada: GoPay, BCA, Kartu kredit."
    )


def test_an_unknown_wallet_without_any_wallet_in_tepira_names_only_the_one_asked_for():
    tepira = FakeTepira(lambda: error_json(422, "wallet_not_found", wallets=[]))

    error = failure(lambda client: client.record(18000, None, "kopi", "Dana"), tepira)

    assert (error.kind, error.message) == ("wallet_not_found", "Dompet Dana tidak ada di tepira.")


def test_tepiras_wallet_names_are_cut_to_40_characters_and_20_names():
    names = [f"{index:02d}" + "x" * 50 for index in range(25)]
    tepira = FakeTepira(lambda: error_json(422, "wallet_not_found", wallets=names))

    error = failure(lambda client: client.record(18000, None, "kopi", "Dana"), tepira)

    listed = error.message.removeprefix("Dompet Dana tidak ada di tepira. Dompet yang ada: ")
    assert listed.removesuffix(".").split(", ") == [name[:40] for name in names[:20]]


def test_wallet_names_that_are_not_text_are_left_out():
    wallets = ["GoPay", 5, None, {"name": "BCA"}, "  ", "Dana tunai"]
    tepira = FakeTepira(lambda: error_json(422, "wallet_not_found", wallets=wallets))

    error = failure(lambda client: client.record(18000, None, "kopi", "Dana"), tepira)

    assert error.message.endswith("Dompet yang ada: GoPay, Dana tunai.")


# --- shapes ----------------------------------------------------------------------------------


def changed(answer: dict, path: tuple[str | int, ...], value: Any = KeyError) -> dict:
    """A deep copy of `answer` with the value at `path` replaced, or removed for KeyError."""
    result = copy.deepcopy(answer)
    *parents, last = path
    target: Any = result
    for key in parents:
        target = target[key]
    if value is KeyError:
        del target[last]
    else:
        target[last] = value
    return result


UNEXPECTED_READS: dict[str, tuple[str, dict]] = {
    "summary without ok": ("summary", changed(SUMMARY, ("ok",))),
    "summary ok as text": ("summary", changed(SUMMARY, ("ok",), "true")),
    "summary ok as 1": ("summary", changed(SUMMARY, ("ok",), 1)),
    "summary without as_of": ("summary", changed(SUMMARY, ("as_of",))),
    "summary as_of a number": ("summary", changed(SUMMARY, ("as_of",), 20261016)),
    "summary without period": ("summary", changed(SUMMARY, ("period",))),
    "summary period a list": ("summary", changed(SUMMARY, ("period",), [10, 2026])),
    "summary without spent": ("summary", changed(SUMMARY, ("spent",))),
    "summary without spent total": ("summary", changed(SUMMARY, ("spent", "total"))),
    "summary spent total a float": ("summary", changed(SUMMARY, ("spent", "total"), 2350000.5)),
    "summary spent count a boolean": ("summary", changed(SUMMARY, ("spent", "count"), True)),
    "summary without income": ("summary", changed(SUMMARY, ("income",))),
    "summary without budget": ("summary", changed(SUMMARY, ("budget",))),
    "summary without budget status": ("summary", changed(SUMMARY, ("budget", "status"))),
    "summary without other_spent": ("summary", changed(SUMMARY, ("other_spent",))),
    "summary without categories": ("summary", changed(SUMMARY, ("categories",))),
    "summary categories an object": ("summary", changed(SUMMARY, ("categories",), {})),
    "summary category not an object": ("summary", changed(SUMMARY, ("categories", 0), "Makan")),
    "summary without top": ("summary", changed(SUMMARY, ("top",))),
    "summary top item a number": ("summary", changed(SUMMARY, ("top", 1), 35000)),
    "wallets without as_of": ("wallets", changed(WALLETS, ("as_of",))),
    "wallets without wallets": ("wallets", changed(WALLETS, ("wallets",))),
    "wallets without a name": ("wallets", changed(WALLETS, ("wallets", 0, "name"))),
    "wallets name a number": ("wallets", changed(WALLETS, ("wallets", 0, "name"), 1)),
    "wallets balance as text": ("wallets", changed(WALLETS, ("wallets", 1, "balance"), "5000000")),
    "wallets balance a boolean": ("wallets", changed(WALLETS, ("wallets", 1, "balance"), True)),
    "wallets without more": ("wallets", changed(WALLETS, ("more",))),
    "wallets more a float": ("wallets", changed(WALLETS, ("more",), 0.0)),
    "wallets without totals": ("wallets", changed(WALLETS, ("totals",))),
    "wallets without assets": ("wallets", changed(WALLETS, ("totals", "assets"))),
    "wallets without debts": ("wallets", changed(WALLETS, ("totals", "debts"))),
    "wallets without net_worth": ("wallets", changed(WALLETS, ("totals", "net_worth"))),
    "goals without as_of": ("goals", changed(GOALS, ("as_of",))),
    "goals without goals": ("goals", changed(GOALS, ("goals",))),
    "goals without a title": ("goals", changed(GOALS, ("goals", 0, "title"))),
    "goals without a target": ("goals", changed(GOALS, ("goals", 0, "target"))),
    "goals saved a boolean": ("goals", changed(GOALS, ("goals", 0, "saved"), False)),
    "goals without more": ("goals", changed(GOALS, ("more",))),
    "transactions without as_of": ("transactions", changed(TRANSACTIONS, ("as_of",))),
    "transactions without transactions": ("transactions", changed(TRANSACTIONS, ("transactions",))),
    "transactions without a date": (
        "transactions",
        changed(TRANSACTIONS, ("transactions", 0, "date")),
    ),
    "transactions amount a float": (
        "transactions",
        changed(TRANSACTIONS, ("transactions", 1, "amount"), 35000.0),
    ),
}


@pytest.mark.parametrize("case", sorted(UNEXPECTED_READS))
def test_a_read_of_another_shape_is_unreadable_not_a_crash(case):
    route, answer = UNEXPECTED_READS[case]
    tepira = FakeTepira(answer)

    error = failure(READS[route][0], tepira)

    assert (error.kind, error.message) == ("unreadable", UNREADABLE)


UNEXPECTED_RECORDS: dict[str, dict] = {
    "without ok": changed(RECORDED, ("ok",)),
    "without expense": changed(RECORDED, ("expense",)),
    "without an id": changed(RECORDED, ("expense", "id")),
    "id a boolean": changed(RECORDED, ("expense", "id"), True),
    "without an amount": changed(RECORDED, ("expense", "amount")),
    "amount as text": changed(RECORDED, ("expense", "amount"), "18000"),
    "without a date": changed(RECORDED, ("expense", "date")),
    "without a wallet": changed(RECORDED, ("expense", "wallet")),
    "wallet null": changed(RECORDED, ("expense", "wallet"), None),
    "category a number": changed(RECORDED, ("expense", "category"), 5),
    "note a list": changed(RECORDED, ("expense", "note"), ["kopi"]),
    "without deduped": changed(RECORDED, ("deduped",)),
    "deduped as text": changed(RECORDED, ("deduped",), "false"),
}


@pytest.mark.parametrize("case", sorted(UNEXPECTED_RECORDS))
def test_a_record_answered_in_another_shape_is_unconfirmed(case):
    answer = UNEXPECTED_RECORDS[case]
    tepira = FakeTepira(lambda: httpx.Response(201, json=answer))

    error = failure(record, tepira)

    assert (error.kind, error.message) == ("unconfirmed", RECORD_UNCONFIRMED)


@pytest.mark.parametrize(
    "answer",
    [
        changed(DELETED, ("deleted",)),
        changed(DELETED, ("deleted", "id")),
        changed(DELETED, ("deleted", "amount"), True),
        changed(DELETED, ("ok",), False),
    ],
    ids=["without deleted", "without an id", "amount a boolean", "ok false"],
)
def test_an_undo_answered_in_another_shape_is_unconfirmed(answer):
    tepira = FakeTepira(answer)

    error = failure(undo, tepira)

    assert (error.kind, error.message) == ("unconfirmed", UNDO_UNCONFIRMED)


def test_a_read_passes_every_other_key_through_untouched():
    answer = {**SUMMARY, "forecast": {"end_of_month": 4100000}, "notes": ["data, not orders"]}
    tepira = FakeTepira(answer)

    assert tepira.client.summary(10, 2026) == without_ok(answer)


def test_a_summary_without_the_keys_tepira_may_leave_out_is_read():
    # A past month with no salary, bonus, or starting balance, and no weekly score yet.
    answer = {
        **{
            key: value
            for key, value in SUMMARY.items()
            if key not in ("savings_allocation", "insight")
        },
        "spent": {"total": 2350000, "count": 41, "gaji": 2350000, "bonus": 0},
        "income": {"net": -2350000, "ending_estimate": -2350000},
    }
    tepira = FakeTepira(answer)

    assert tepira.client.summary(9, 2026) == without_ok(answer)


@pytest.mark.parametrize(
    ("route", "key", "item", "cap"),
    [
        ("summary", "categories", SUMMARY["categories"][0], 10),
        ("summary", "top", SUMMARY["top"][0], 5),
        ("wallets", "wallets", WALLETS["wallets"][0], 30),
        ("goals", "goals", GOALS["goals"][0], 10),
        ("transactions", "transactions", TRANSACTIONS["transactions"][0], 10),
    ],
)
def test_a_list_longer_than_tepiras_cap_is_cut_to_it(route, key, item, cap):
    call, answer = READS[route]
    longer = {**answer, key: [{**item, "id": index} for index in range(cap + 3)]}
    tepira = FakeTepira(longer)

    assert call(tepira.client)[key] == longer[key][:cap]


def test_an_item_past_tepiras_cap_is_dropped_unread():
    items = [TRANSACTIONS["transactions"][0]] * 10 + ["not an object"]
    tepira = FakeTepira({**TRANSACTIONS, "transactions": items})

    assert tepira.client.transactions(10, None)["transactions"] == items[:10]


def test_a_body_of_exactly_32_kib_is_read():
    padding = json.dumps({**WALLETS, "pad": ""}).encode()
    body = json.dumps({**WALLETS, "pad": "x" * (32 * 1024 - len(padding))}).encode()
    assert len(body) == 32 * 1024
    tepira = FakeTepira(lambda: httpx.Response(200, content=body))

    assert tepira.client.wallets()["pad"] == "x" * (32 * 1024 - len(padding))


def test_a_body_one_byte_over_32_kib_is_unreadable():
    padding = json.dumps({**WALLETS, "pad": ""}).encode()
    body = json.dumps({**WALLETS, "pad": "x" * (32 * 1024 + 1 - len(padding))}).encode()
    tepira = FakeTepira(lambda: httpx.Response(200, content=body))

    assert failure(READS["wallets"][0], tepira).kind == "unreadable"


class EndlessBody(httpx.SyncByteStream):
    """A body that never ends, counting the KiB read from it."""

    def __init__(self) -> None:
        self.read_kib = 0

    def __iter__(self):
        while True:
            self.read_kib += 1
            yield b"x" * 1024


def test_an_endless_body_is_read_only_past_32_kib():
    body = EndlessBody()
    tepira = FakeTepira(lambda: httpx.Response(200, stream=body))

    assert failure(READS["wallets"][0], tepira).kind == "unreadable"
    assert body.read_kib == 33


# --- logging and the token -------------------------------------------------------------------


def test_a_success_is_one_debug_line_with_the_route_status_and_time(caplog):
    tepira = FakeTepira(Took(0.4, SUMMARY))

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        tepira.client.summary(10, 2026)

    assert client_lines(caplog) == ["tepira GET /api/cyrene/summary 200 in 0.4 s"]
    assert caplog.records[-1].levelno == logging.DEBUG


def test_a_failure_is_one_warning_with_the_status_or_the_exception_class(caplog):
    tepira = FakeTepira(lambda: httpx.ReadTimeout("slow"))
    busy = FakeTepira(lambda: httpx.Response(429, headers={"Retry-After": "12"}))

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        failure(record, tepira)
        failure(READS["wallets"][0], busy)

    assert client_lines(caplog) == [
        "tepira POST /api/cyrene/expenses failed: ReadTimeout (unconfirmed)",
        "tepira GET /api/cyrene/wallets failed: 429 (busy)",
    ]
    assert {record.levelno for record in caplog.records if record.name == LOGGER} == {
        logging.WARNING
    }


def test_an_undo_is_logged_without_the_expense_id(caplog):
    tepira = FakeTepira(DELETED)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        tepira.client.undo(813)

    assert client_lines(caplog) == ["tepira DELETE /api/cyrene/expenses/{id} 200 in 0.0 s"]


def test_the_log_never_holds_a_query_an_amount_a_note_or_a_name(caplog):
    tepira = FakeTepira(
        GOALS,
        TRANSACTIONS,
        lambda: httpx.Response(201, json=RECORDED),
        lambda: error_json(422, "wallet_not_found", wallets=["GoPay"]),
        lambda: httpx.ReadTimeout("kopi rahasia 18000"),
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        tepira.client.goals("laptop rahasia")
        tepira.client.transactions(7, "Nongkrong / jajan")
        tepira.client.record(18000, "Nongkrong / jajan", "kopi rahasia", "GoPay")
        failure(lambda client: client.record(18000, None, "kopi rahasia", "Dompet rahasia"), tepira)
        failure(lambda client: client.record(18000, None, "kopi rahasia", "GoPay"), tepira)

    logged = "\n".join(client_lines(caplog))
    assert len(client_lines(caplog)) == 5
    for private in ("rahasia", "laptop", "18000", "GoPay", "Nongkrong", "?", "limit", "7"):
        assert private not in logged


SECRET_RUN: list[tuple[Callable[[TepiraClient], Any], Reply]] = [
    (READS["summary"][0], SUMMARY),
    (READS["wallets"][0], WALLETS),
    (READS["goals"][0], GOALS),
    (READS["transactions"][0], TRANSACTIONS),
    (record, lambda: httpx.Response(201, json=RECORDED)),
    (undo, DELETED),
    *((READS["summary"][0], reply) for reply, _, _ in READ_FAILURES.values()),
    *((record, reply) for reply, _, _ in RECORD_FAILURES.values()),
    *((undo, reply) for reply, _, _ in UNDO_FAILURES.values()),
]


def test_the_token_is_never_in_a_log_record_an_error_or_a_url(caplog):
    shown: list[str] = []

    with caplog.at_level(logging.DEBUG):  # every logger, httpx's own included
        for call, reply in SECRET_RUN:
            tepira = FakeTepira(reply)
            shown.append(repr(tepira.client))
            try:
                call(tepira.client)
            except FinanceError as error:
                shown += [str(error), repr(error), "".join(traceback.format_exception(error))]
            shown += [str(request.url) for request in tepira.requests]

    assert caplog.records
    assert SECRET not in caplog.text
    assert all(SECRET not in record.getMessage() for record in caplog.records)
    assert all(SECRET not in text for text in shown)


def test_a_finance_error_hides_the_httpx_error_behind_it():
    tepira = FakeTepira(lambda: httpx.ReadTimeout("slow"))

    error = failure(READS["wallets"][0], tepira)

    assert error.__cause__ is None
    assert error.__suppress_context__
