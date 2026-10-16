"""The tool that tells Cyrene what her Claude requests cost: `api_usage`.

"Biaya API bulan ini berapa?" or "hari ini sudah habis berapa?": the tool sums the ledger
(`usage.py`) for today or this calendar month, local time: the tokens, the web searches and
their fees (already in the cost), and the cost. The figures are her own estimates from the
`usage` the API reported for her own requests on this PC, not the Anthropic console's bill.
The spec carries no figures, so it stays byte-identical for the brain's lifetime and keeps
the prompt cache warm.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime

from waifu.brain.base import ToolSpec
from waifu.usage import UsageLedger, UsageSummary

logger = logging.getLogger(__name__)

API_USAGE = "api_usage"
PERIODS = ("today", "month")
_API_USAGE_KEYS = frozenset({"period"})
# A hundredth of a cent: one cheap turn still shows, and a sum such as 0.30000000000000004
# reaches the model as 0.3.
_USD_DECIMALS = 4


class _InvalidArguments(Exception):
    """The model's tool arguments do not match the tool's schema."""


class UsageTools:
    """A `ToolProvider` for the Claude brain: what the API cost today or this month."""

    def __init__(
        self,
        ledger: UsageLedger,
        *,
        idr_per_usd: float | None = None,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._ledger = ledger
        self._idr_per_usd = idr_per_usd
        self._now = now

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name=API_USAGE,
                description=(
                    "How much you used the Claude API and what it cost: `today` (since local "
                    "midnight) or `month` (this calendar month, local time). The result counts "
                    "your requests and their tokens (input, output, prompt-cache reads and "
                    "writes) and gives the cost in US dollars, and in rupiah when Master has "
                    "set an exchange rate (cost_idr is null otherwise: then give the cost in "
                    "dollars only). When you searched the web in that period, it also gives "
                    "the number of searches (web_searches) and their fees (web_search_cost_usd, "
                    "and web_search_cost_idr like cost_idr): cost_usd already includes them, so "
                    "never add them again; these keys are absent when you did not search. These "
                    "are your own estimates, from the usage the API reported for your own "
                    "requests on this PC, priced per model: not the Anthropic bill, and other "
                    "apps using the same API key are not counted. "
                    "`estimated` is true when a fallback model answered part of it and was "
                    "priced with your usual model's prices. Use it when Master asks what the "
                    "API cost; say the cost rounded and that it is an estimate, without "
                    "reading out every number."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"period": {"type": "string", "enum": list(PERIODS)}},
                    "required": ["period"],
                    "additionalProperties": False,
                },
            )
        ]

    def run(self, name: str, arguments: object) -> tuple[str, bool]:
        try:
            if name == API_USAGE:
                return self._api_usage(arguments)
        except _InvalidArguments as exc:
            return _error(f"Argumen {name} tidak valid: {exc}")
        except Exception:
            logger.exception("Usage tool '%s' failed", name)
            return _error("Catatan biaya API tidak bisa dibaca saat ini.")
        return _error(f"Tool '{name}' bukan tool biaya API.")

    def _api_usage(self, arguments: object) -> tuple[str, bool]:
        period = _period(_arguments_mapping(arguments, allowed=_API_USAGE_KEYS))
        now = self._now()
        if period == "today":
            summary = self._ledger.day(now.date())
        else:
            summary = self._ledger.month(now.year, now.month)
        return _to_json(self._result(period, summary)), False

    def _result(self, period: str, summary: UsageSummary) -> dict[str, object]:
        return {
            "period": period,
            "requests": summary.requests,
            "input_tokens": summary.tokens.input,
            "output_tokens": summary.tokens.output,
            "cache_read_tokens": summary.tokens.cache_read,
            "cache_write_tokens": summary.tokens.cache_write,
            "cost_usd": round(summary.cost_usd, _USD_DECIMALS),
            "cost_idr": self._rupiah(summary.cost_usd),
            **self._web_searches(summary),
            "estimated": summary.estimated,
        }

    def _web_searches(self, summary: UsageSummary) -> dict[str, object]:
        """The searches and their fees (part of the cost already), only when there are any:
        like the column of `waifu usage`.

        The ledger keeps how many searches there were, not what each cost, so the fees are at
        the current price; after a price rise they are still never more than the cost that
        holds them at the price of their day.
        """
        searches = summary.tokens.web_searches
        if not searches:
            return {}
        fees_usd = min(searches * self._ledger.web_search_usd, summary.cost_usd)
        return {
            "web_searches": searches,
            "web_search_cost_usd": round(fees_usd, _USD_DECIMALS),
            "web_search_cost_idr": self._rupiah(fees_usd),
        }

    def _rupiah(self, usd: float) -> int | None:
        """`usd` in whole rupiah at Master's rate; None without one."""
        if self._idr_per_usd is None:
            return None
        return round(usd * self._idr_per_usd)


def _period(arguments: Mapping[str, object]) -> str:
    period = arguments.get("period")
    if not isinstance(period, str) or period not in PERIODS:
        raise _InvalidArguments(f"'period' harus salah satu dari: {', '.join(PERIODS)}.")
    return period


def _arguments_mapping(arguments: object, allowed: frozenset[str]) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise _InvalidArguments("argumen harus berupa objek JSON.")
    unknown = [str(key) for key in arguments if key not in allowed]
    if unknown:
        raise _InvalidArguments(f"argumen tidak dikenal: {', '.join(unknown)}.")
    return arguments


def _error(message: str) -> tuple[str, bool]:
    return _to_json({"ok": False, "message": message}), True


def _to_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
