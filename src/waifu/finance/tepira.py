"""tepira's owner-only Cyrene API over HTTPS (v1.24): the FinanceSource behind the finance
tools and `waifu tepira`.

She is silent while tepira answers, and a Vercel cold start with Supabase can be slow, so one
call has `TIME_BUDGET_S` in all: each attempt's timeout is the time left. A GET is tried once
more only when the first attempt could not connect (so nothing was sent) and at least
`MIN_REQUEST_S` is left; a POST or DELETE is never repeated, since tepira may have acted on it.

Every failure is a FinanceError whose message she can say. The token goes only into the
Authorization header, and the log holds one line per call: the method, the route, and the
status or the exception's class name, never the query, a header, a body, or the token.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, get_args

import httpx
from pydantic import SecretStr

from waifu import __version__
from waifu.finance.values import (
    BAD_REQUEST,
    CATEGORY_LABELS,
    MAX_EXPENSE_AMOUNT,
    MAX_GOAL_QUERY_CHARS,
    MAX_NOTE_CHARS,
    MAX_TRANSACTIONS,
    MAX_WALLET_CHARS,
    MAX_YEAR,
    MIN_YEAR,
    NOT_ACTIVE,
    RATE_LIMITED,
    RECORD_UNCONFIRMED,
    TOKEN_REJECTED,
    UNCATEGORIZED,
    UNDO_EXPIRED,
    UNDO_UNCONFIRMED,
    UNREACHABLE,
    UNREADABLE,
    WALLET_GONE,
    WALLET_NOT_FOUND,
    WALLETS_THERE,
    WRITES_OFF,
    ExpenseSource,
    FinanceData,
    FinanceError,
    FinanceErrorKind,
    RecordedExpense,
    UndoneExpense,
)

logger = logging.getLogger(__name__)

API_PATH = "/api/cyrene"
TIME_BUDGET_S = 8.0  # for one call, a second GET attempt included
MIN_REQUEST_S = 1.0  # a GET is not tried again with less time left
MAX_BODY_BYTES = 32 * 1024  # tepira's largest answer is a few KiB
USER_AGENT = f"waifu-companion/{__version__}"

# Nothing was sent when these happen, so a GET may be tried again.
_CONNECT_FAILURES = (httpx.ConnectError, httpx.ConnectTimeout)
# A field a 400 names goes into a message she says only when it is a plain name.
_FIELD_NAME = re.compile(r"[A-Za-z0-9_]{1,40}")
_UNNAMED_FIELD = "permintaan"
_MAX_WALLET_NAMES = 20
_MAX_WALLET_ID = 2**31 - 1  # tepira's ids are Postgres int4

# What tepira's answers mean for each method beyond a plain success (SPEC v1.24's table): a 404,
# then an answer that leaves unknown whether a write happened (a 5xx, an unexpected 2xx, a
# timeout once the request may have been sent).
_NOT_FOUND: dict[str, tuple[FinanceErrorKind, str]] = {
    "GET": ("off", NOT_ACTIVE),
    "POST": ("writes_off", WRITES_OFF),
    "DELETE": ("expired", UNDO_EXPIRED),
}
_UNCONFIRMED: dict[str, tuple[FinanceErrorKind, str]] = {
    "POST": ("unconfirmed", RECORD_UNCONFIRMED),
    "DELETE": ("unconfirmed", UNDO_UNCONFIRMED),
}

Check = Callable[[Any], bool]


def _int(value: Any) -> bool:
    """A JSON integer: never a boolean, which Python counts as an int."""
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: Any) -> bool:
    return isinstance(value, str)


def _text_or_null(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _boolean(value: Any) -> bool:
    return isinstance(value, bool)


def _object(**fields: Check) -> Check:
    """A JSON object whose named fields pass their checks; any other field may be anything."""

    def check(value: Any) -> bool:
        return isinstance(value, dict) and all(
            passes(value.get(name)) for name, passes in fields.items()
        )

    return check


def _list_of(item: Check) -> Check:
    def check(value: Any) -> bool:
        return isinstance(value, list) and all(item(element) for element in value)

    return check


@dataclass(frozen=True)
class _Shape:
    """What an answer must hold, and tepira's caps on its lists (a longer list is cut)."""

    check: Check
    caps: Mapping[str, int] = field(default_factory=dict)


_SUMMARY = _Shape(
    _object(
        as_of=_text,
        period=_object(),
        spent=_object(total=_int, count=_int),
        income=_object(),
        budget=_object(status=_text),
        other_spent=_object(),
        categories=_list_of(_object()),
        top=_list_of(_object()),
    ),
    {"categories": 10, "top": 5},
)
_WALLETS = _Shape(
    _object(
        as_of=_text,
        wallets=_list_of(_object(id=_int, name=_text, balance=_int)),
        more=_int,
        totals=_object(assets=_int, debts=_int, net_worth=_int),
    ),
    {"wallets": 30},
)
_GOALS = _Shape(
    _object(as_of=_text, goals=_list_of(_object(title=_text, target=_int, saved=_int)), more=_int),
    {"goals": 10},
)
_TRANSACTIONS = _Shape(
    _object(as_of=_text, transactions=_list_of(_object(date=_text, amount=_int))),
    {"transactions": MAX_TRANSACTIONS},
)
_RECORDED = _Shape(
    _object(
        expense=_object(
            id=_int,
            amount=_int,
            date=_text,
            wallet=_text,
            category=_text_or_null,
            note=_text_or_null,
        ),
        deduped=_boolean,
    )
)
_UNDONE = _Shape(_object(deleted=_object(id=_int, amount=_int)))


class TepiraClient:
    """tepira's Cyrene API at `url` (https and a host, as tepira.url), with `token`.

    The caller owns `http` and keeps one for the process, so a later question reuses the TLS
    connection; the client never closes it and never follows a redirect.
    """

    def __init__(
        self,
        http: httpx.Client,
        url: str,
        token: SecretStr,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not url.startswith("https://"):
            raise ValueError("the tepira url must be https://, since the token goes there")
        self._http = http
        self._base = f"{url.rstrip('/')}{API_PATH}"
        self._token = token
        self._clock = clock

    def summary(self, month: int, year: int) -> FinanceData:
        """GET /summary of that month: Cyrene always names the month and the year."""
        _require_whole_number("month", month, 1, 12)
        _require_whole_number("year", year, MIN_YEAR, MAX_YEAR)
        return self._call("GET", "summary", _SUMMARY, params={"month": month, "year": year})

    def wallets(self) -> FinanceData:
        """GET /wallets: every wallet's balance (at most 30), and the totals."""
        return self._call("GET", "wallets", _WALLETS)

    def goals(self, query: str | None) -> FinanceData:
        """GET /goals, only those whose title holds `query` when one is given."""
        if query is not None and not isinstance(query, str):
            raise ValueError("the goal query must be text")
        # tepira measures it as JavaScript does, in UTF-16 units: an emoji counts twice.
        if query and len(query.encode("utf-16-le")) // 2 > MAX_GOAL_QUERY_CHARS:
            raise ValueError(f"the goal query must be at most {MAX_GOAL_QUERY_CHARS} characters")
        return self._call("GET", "goals", _GOALS, params={"q": query} if query else None)

    def transactions(self, limit: int, category: str | None) -> FinanceData:
        """GET /transactions: the newest `limit` expenses, of one category when given."""
        _require_whole_number("limit", limit, 1, MAX_TRANSACTIONS)
        if category is not None and category not in (*CATEGORY_LABELS, UNCATEGORIZED):
            raise ValueError("the category must be one of tepira's labels or uncategorized")
        params: dict[str, Any] = {"limit": limit}
        if category is not None:
            params["category"] = category
        return self._call("GET", "transactions", _TRANSACTIONS, params=params)

    def record(
        self,
        amount: int,
        category: str | None,
        note: str | None,
        wallet: int | str,
        source: ExpenseSource = "gaji",
    ) -> RecordedExpense:
        """POST /expenses, dated now by tepira, to the wallet with that id (as /wallets lists
        it) or that name; never retried. tepira answers a repeat of the last two minutes with
        that entry, marked `deduped`."""
        _require_whole_number("amount", amount, 1, MAX_EXPENSE_AMOUNT)
        if category is not None and category not in CATEGORY_LABELS:
            raise ValueError("the category must be one of tepira's labels")
        if note is not None and (
            not isinstance(note, str) or not note.strip() or len(note) > MAX_NOTE_CHARS
        ):
            raise ValueError(f"the note must be text of 1-{MAX_NOTE_CHARS} characters")
        if _int(wallet):
            _require_whole_number("wallet id", wallet, 1, _MAX_WALLET_ID)
        elif not isinstance(wallet, str) or not wallet.strip():
            raise ValueError("the wallet must be a name or an id")
        if source not in get_args(ExpenseSource):
            raise ValueError("the source must be gaji or bonus")
        body: dict[str, Any] = {"amount": amount, "wallet": wallet, "source": source}
        if category is not None:
            body["category"] = category
        if note is not None:
            body["note"] = note
        answer = self._call("POST", "expenses", _RECORDED, json=body, wallet=wallet)
        expense = answer["expense"]
        return RecordedExpense(
            id=expense["id"],
            amount=expense["amount"],
            category=expense.get("category"),
            note=expense.get("note"),
            date=expense["date"],
            wallet=expense["wallet"],
            deduped=answer["deduped"],
        )

    def undo(self, expense_id: int) -> UndoneExpense:
        """DELETE /expenses/{id}: tepira deletes only an expense made in the last 15 minutes."""
        _require_whole_number("expense id", expense_id, 1, None)
        answer = self._call("DELETE", f"expenses/{expense_id}", _UNDONE, logged="expenses/{id}")
        return UndoneExpense(id=answer["deleted"]["id"], amount=answer["deleted"]["amount"])

    def _call(
        self,
        method: str,
        route: str,
        shape: _Shape,
        *,
        logged: str | None = None,
        wallet: int | str | None = None,
        **request: Any,
    ) -> dict[str, Any]:
        """tepira's answer without "ok" when it has `shape`; else a FinanceError, logged."""
        logged_route = f"{API_PATH}/{logged or route}"
        started = self._clock()
        try:
            status, data = self._exchange(method, f"{self._base}/{route}", started, **request)
        except httpx.TransportError as exc:
            error = _no_answer(method, connected=not isinstance(exc, _CONNECT_FAILURES))
            logger.warning(
                "tepira %s %s failed: %s (%s)", method, logged_route, type(exc).__name__, error.kind
            )
            raise error from None
        answer = _expected(data, shape) if 200 <= status < 300 else None
        if answer is None:
            error = _failure(method, status, data, wallet)
            logger.warning("tepira %s %s failed: %s (%s)", method, logged_route, status, error.kind)
            raise error
        logger.debug(
            "tepira %s %s %s in %.1f s", method, logged_route, status, self._clock() - started
        )
        return answer

    def _exchange(self, method: str, url: str, started: float, **request: Any) -> tuple[int, Any]:
        """One attempt, and for a GET that could not connect a second one with the time left;
        httpx's TransportError when there is no answer."""
        deadline = started + TIME_BUDGET_S
        try:
            return self._attempt(method, url, deadline - self._clock(), **request)
        except _CONNECT_FAILURES:
            if method != "GET" or deadline - self._clock() < MIN_REQUEST_S:
                raise
        return self._attempt(method, url, deadline - self._clock(), **request)

    def _attempt(self, method: str, url: str, time_left: float, **request: Any) -> tuple[int, Any]:
        """The status and the JSON body (None when unreadable or not needed) of one request."""
        with self._http.stream(
            method,
            url,
            headers={
                "Authorization": f"Bearer {self._token.get_secret_value()}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=time_left,
            follow_redirects=False,
            **request,
        ) as response:
            status = response.status_code
            if 200 <= status < 300 or status in (400, 422):
                return status, _json_body(response)
            return status, None


def _require_whole_number(name: str, value: Any, low: int, high: int | None) -> None:
    """A ValueError unless `value` is an int from `low` to `high`; it never shows the value."""
    if not _int(value) or value < low or (high is not None and value > high):
        limits = f"from {low} to {high}" if high is not None else f"of at least {low}"
        raise ValueError(f"the {name} must be a whole number {limits}")


def _json_body(response: httpx.Response) -> Any:
    """The decoded JSON body; None when it is over MAX_BODY_BYTES, not UTF-8, or not JSON."""
    body = bytearray()
    try:
        for chunk in response.iter_bytes():
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                return None
    except httpx.DecodingError:  # a Content-Encoding that does not decode
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        return None


def _expected(data: Any, shape: _Shape) -> dict[str, Any] | None:
    """The answer without "ok", its lists cut to tepira's caps, when it has the shape."""
    if not isinstance(data, dict) or data.get("ok") is not True:
        return None
    answer = {key: value for key, value in data.items() if key != "ok"}
    for key, cap in shape.caps.items():
        if isinstance(answer.get(key), list):
            answer[key] = answer[key][:cap]
    return answer if shape.check(answer) else None


def _no_answer(method: str, *, connected: bool) -> FinanceError:
    """No answer came. A write may have reached tepira only once a connection was made."""
    if method == "GET" or not connected:
        return FinanceError("unreachable", UNREACHABLE)
    return FinanceError(*_UNCONFIRMED[method])


def _failure(method: str, status: int, data: Any, wallet: int | str | None) -> FinanceError:
    """Why tepira's answer is no answer to `method` (SPEC v1.24's table)."""
    if 200 <= status < 300:
        return FinanceError(*_UNCONFIRMED.get(method, ("unreadable", UNREADABLE)))
    if status == 400:
        return FinanceError("bad_request", BAD_REQUEST.format(field=_field(data)))
    if status in (401, 403):
        return FinanceError("token", TOKEN_REJECTED)
    if status == 404:
        return FinanceError(*_NOT_FOUND[method])
    if status == 422 and method == "POST" and wallet is not None:
        names = _wallet_names(data)
        if names is not None:
            return _wallet_not_found(wallet, names)
    if status == 429:
        return FinanceError("busy", RATE_LIMITED)
    if 500 <= status < 600:
        return FinanceError(*_UNCONFIRMED.get(method, ("unreachable", UNREACHABLE)))
    return FinanceError("unreadable", UNREADABLE)


def _field(data: Any) -> str:
    """The field a 400 names, when it is a plain name of 1-40 letters, digits, or _."""
    named = data.get("field") if isinstance(data, dict) else None
    if isinstance(named, str) and _FIELD_NAME.fullmatch(named):
        return named
    return _UNNAMED_FIELD


def _wallet_names(data: Any) -> list[str] | None:
    """The wallet names of a 422, each cut to 40 characters, at most 20; None without a list."""
    names = data.get("wallets") if isinstance(data, dict) else None
    if not isinstance(names, list):
        return None
    cut = (name[:MAX_WALLET_CHARS].strip() for name in names if isinstance(name, str))
    return [name for name in cut if name][:_MAX_WALLET_NAMES]


def _wallet_not_found(wallet: int | str, names: list[str]) -> FinanceError:
    if isinstance(wallet, int):
        message = WALLET_GONE
    else:
        message = WALLET_NOT_FOUND.format(wallet=wallet.strip())
    if names:
        message = f"{message} {WALLETS_THERE.format(names=', '.join(names))}"
    return FinanceError("wallet_not_found", message)
