"""What the tepira client, the finance tools, and `waifu tepira` share (v1.24).

tepira answers Cyrene through its owner-only API under /api/cyrene (tepira PR #21: its
app/api/cyrene/**/route.ts and lib/cyrene/core.ts are the source of truth for the limits
below). The client turns every failure into a `FinanceError` whose message is Indonesian and
safe to speak; the tools and the CLI rely only on `FinanceSource`, so their tests pass fakes.
Nothing here does any I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Any, Literal, Protocol

# Western Indonesia Time: UTC+7 all year, with no daylight saving. A fixed offset, because this
# Python has no tz database for zoneinfo to read "Asia/Jakarta" from.
WIB = timezone(timedelta(hours=7), "WIB")

# The labels of tepira's expense categories, copied from its lib/categories.ts
# (`expenseCategories`, in that order; tepira's lib/cyrene/core.ts holds the same copy). An
# expense under any other category never counts against a budget there, so Cyrene sends no other.
CATEGORY_LABELS = (
    "Makan harian",
    "Transport",
    "Pulsa & Internet",
    "Listrik / Air / Kos",
    "Langganan (Spotify, Netflix, dll)",
    "Kesehatan / Obat",
    "Sosial (sedekah / bantu keluarga)",
    "Nongkrong / jajan",
    "Belanja kecil",
)
# Asks recent_transactions for the expenses without a category; never a category to record.
UNCATEGORIZED = "uncategorized"

# What tepira accepts (lib/cyrene/core.ts); the tools check these before anything is sent.
MAX_EXPENSE_AMOUNT = 5_000_000  # rupiah per expense
MAX_NOTE_CHARS = 120
MAX_GOAL_QUERY_CHARS = 50
MAX_WALLET_CHARS = 40  # tepira shows wallet names cut to 40 (its clampText)
MAX_WALLET_NAME_CHARS = 60  # a wallet named in full, as tepira takes one in its API
DEFAULT_TRANSACTIONS = 5
MAX_TRANSACTIONS = 10
MIN_YEAR = 2000
MAX_YEAR = 2100
# tepira deletes an expense made through this API only this long after it was made.
UNDO_WINDOW_S = 15 * 60

# The finance tools. Their arguments never reach the INFO log (the brain's logging rule).
FINANCE_SUMMARY = "finance_summary"
WALLET_BALANCES = "wallet_balances"
SAVINGS_GOALS = "savings_goals"
RECENT_TRANSACTIONS = "recent_transactions"
RECORD_EXPENSE = "record_expense"
UNDO_EXPENSE = "undo_expense"
READ_TOOLS = (FINANCE_SUMMARY, WALLET_BALANCES, SAVINGS_GOALS, RECENT_TRANSACTIONS)
WRITE_TOOLS = (RECORD_EXPENSE, UNDO_EXPENSE)
FINANCE_TOOLS = READ_TOOLS + WRITE_TOOLS

# Why tepira gave no answer (see FinanceError).
FinanceErrorKind = Literal[
    "token",  # 401 or 403: the token was refused
    "off",  # 404 on a read: the feature is off, the owner unknown, or the API not deployed
    "writes_off",  # 404 on a POST: tepira's write switch is off
    "busy",  # 429: rate-limited
    "unreachable",  # no answer, a 5xx, or a Vercel error page; for a write, nothing was sent
    "unconfirmed",  # a write whose outcome is unknown: sent, but no readable answer came back
    "unreadable",  # an answer that is not the JSON this API sends
    "bad_request",  # 400: tepira refused one field
    "wallet_not_found",  # 422 on a POST: no wallet of that name
    "expired",  # 404 on a DELETE: older than 15 minutes, or already deleted
]

TOKEN_REJECTED = (
    "Token tepira ditolak. Buat token baru dengan waifu tepira token, pasang hash-nya di Vercel, "
    "lalu isi tepira.token di config.yaml."
)
NOT_ACTIVE = "Akses Cyrene di tepira belum aktif."
WRITES_OFF = "Pencatatan dari Cyrene belum dinyalakan di tepira (TEPIRA_CYRENE_WRITES di Vercel)."
RATE_LIMITED = "tepira minta jeda sebentar, coba lagi sebentar lagi."
UNREACHABLE = "tepira tidak bisa dihubungi atau sedang bermasalah. Coba lagi nanti."
UNREADABLE = "Balasan tepira tidak bisa dibaca."
RECORD_UNCONFIRMED = (
    "tepira tidak menjawab dengan jelas, jadi aku belum tahu apakah pengeluaran itu sudah "
    "tercatat. Kalau dicatat lagi dalam dua menit, tepira tidak mencatatnya dua kali."
)
UNDO_UNCONFIRMED = (
    "tepira tidak menjawab dengan jelas, jadi aku belum tahu apakah catatan itu sudah "
    "dibatalkan. Cek di tepira dulu."
)
UNDO_EXPIRED = (
    "Catatan itu sudah lewat 15 menit atau sudah dihapus, jadi tidak bisa dibatalkan lagi dari "
    "sini."
)
# Templates the client fills: `field` only as a plain field name, `names` joined with ", ".
BAD_REQUEST = "tepira menolak isian {field}."
WALLET_NOT_FOUND = "Dompet {wallet} tidak ada di tepira."
# For a wallet named by its id: it was there when the wallets were read.
WALLET_GONE = "Dompet itu sudah tidak ada di tepira."
WALLETS_THERE = "Dompet yang ada: {names}."

# A read's JSON object as tepira sent it, without "ok", after the client checked its shape.
FinanceData = dict[str, Any]
ExpenseSource = Literal["gaji", "bonus"]


class FinanceError(Exception):
    """Why tepira gave no answer; `message` is Indonesian and safe to speak."""

    def __init__(self, kind: FinanceErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class RecordedExpense:
    """An expense tepira holds after a POST: the new one, or with `deduped` the same entry made
    in the last two minutes, which tepira returned instead of recording it twice."""

    id: int
    amount: int
    category: str | None
    note: str | None
    date: str  # "YYYY-MM-DD HH:mm" in WIB, as tepira writes it
    wallet: str
    deduped: bool


@dataclass(frozen=True)
class UndoneExpense:
    """An expense tepira deleted, its wallet balance moved back."""

    id: int
    amount: int


class FinanceSource(Protocol):
    """tepira's Cyrene API, one method per route; every failure is a `FinanceError`."""

    def summary(self, month: int, year: int) -> FinanceData:
        """GET /summary: spending, income, and the budget of that month."""
        ...

    def wallets(self) -> FinanceData:
        """GET /wallets: every wallet's balance, and the totals."""
        ...

    def goals(self, query: str | None) -> FinanceData:
        """GET /goals: the savings goals, only those whose title holds `query` when given."""
        ...

    def transactions(self, limit: int, category: str | None) -> FinanceData:
        """GET /transactions: the newest `limit` expenses, of one category when given."""
        ...

    def record(
        self,
        amount: int,
        category: str | None,
        note: str | None,
        wallet: int | str,
        source: ExpenseSource = "gaji",
    ) -> RecordedExpense:
        """POST /expenses, dated now, to the wallet with that id or that name; never retried."""
        ...

    def undo(self, expense_id: int) -> UndoneExpense:
        """DELETE /expenses/{id}, only within 15 minutes of making it; never retried."""
        ...


_DIGITS = ("", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan")
_TEENS = ("sepuluh", "sebelas", *(f"{digit} belas" for digit in _DIGITS[2:]))
_SCALES = ("", "ribu", "juta", "miliar")


def spoken_rupiah(amount: int) -> str:
    """`amount` in Indonesian words, as she says it: 18000 is "delapan belas ribu"."""
    if not 1 <= amount < 1000 ** len(_SCALES):
        raise ValueError(f"cannot say {amount} rupiah")
    groups: list[int] = []
    while amount:
        amount, group = divmod(amount, 1000)
        groups.append(group)
    words: list[str] = []
    for scale in reversed(range(len(groups))):
        group = groups[scale]
        if group == 1 and scale == 1:
            words.append("seribu")
        elif group:
            words += _below_a_thousand(group)
            if scale:
                words.append(_SCALES[scale])
    return " ".join(words)


def expense_question(amount: int, description: str, wallet: str, owner_call: str = "Master") -> str:
    """The question the code asks before recording an expense: the amount, what for, the wallet."""
    return (
        f"Catat {spoken_rupiah(amount)} untuk {description} di {wallet}, {owner_call}? "
        "Jawab iya kalau jadi."
    )


def _below_a_thousand(number: int) -> list[str]:
    hundreds, rest = divmod(number, 100)
    tens, units = divmod(rest, 10)
    words = ["seratus"] if hundreds == 1 else [_DIGITS[hundreds], "ratus"] if hundreds else []
    if tens == 1:
        return [*words, _TEENS[units]]
    if tens:
        words += [_DIGITS[tens], "puluh"]
    if units:
        words.append(_DIGITS[units])
    return words
