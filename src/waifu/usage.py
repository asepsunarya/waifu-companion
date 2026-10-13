"""Cyrene's API cost monitor: the tokens and estimated price of every Claude response.

The Claude brain hands the `usage` of every response to `UsageLedger.record` (it only
observes: the request never changes). The ledger adds the tokens to that local day's totals
per model and prices them with `cost_usd`; `day` and `month` sum them up for "biaya API
bulan ini berapa?" and `waifu usage`, and `budget_notices` tells Master once a month at 80 %
and once at 100 % of an optional budget. These are her own estimates from the `usage`
fields of her own requests, not the Anthropic console's bill: other apps on the same key
are not seen.

Prices are US dollars per million tokens. A response from a model missing from the table
(a server-side fallback) is priced with `usage.default_model`'s prices and counted as
estimated; a default model without prices is a configuration error.

The file follows the rules of `memory.py`: every call re-reads it, so `waifu usage` sees
what a running Cyrene recorded. One lock serialises the calls of every ledger in this
process; between processes the last writer wins. A write goes to a temp file in the same
folder, reaches the disk, and then replaces the file. Reading never creates it, and a file
that is not a usage record is moved aside before anything is written: it is never
overwritten.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType

from waifu.config import ConfigError, ModelPrices, at_sentence_start
from waifu.gacha.files import write_json_atomic

logger = logging.getLogger(__name__)

FILE_VERSION = 1
# From the claude-api reference. The automatic prompt cache she uses keeps an entry for 5
# minutes, and a 5-minute cache write costs 1.25 times the input price.
BUILT_IN_PRICES: Mapping[str, ModelPrices] = MappingProxyType(
    {"claude-opus-5-5": ModelPrices(input=4.00, output=20.00, cache_write=5.00, cache_read=0.20)}
)

_TOKENS_PER_PRICE = 1_000_000  # prices are per million tokens
# Percentages of the monthly budget; each is told once a month, in this order.
_BUDGET_THRESHOLDS = (80, 100)
_FILE_KEYS = {"version", "days", "notices"}
_TOTALS_KEYS = {"requests", "input", "output", "cache_write", "cache_read", "cost_usd", "estimated"}
_COUNT_KEYS = ("requests", "input", "output", "cache_write", "cache_read")
# date.fromisoformat also reads 20261014 and 2026-W42-3; the file holds only YYYY-MM-DD.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ISO_MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
_NOTICE_KEYS = tuple(str(threshold) for threshold in _BUDGET_THRESHOLDS)

# One lock for every ledger: two ledgers on the same file in this process must not lose
# each other's records.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class TokenUsage:
    input: int = 0  # input tokens not read from or written to the prompt cache
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0

    @classmethod
    def from_usage(cls, usage: object) -> TokenUsage:
        """The tokens of a response's `usage`, an SDK object or a plain mapping.

        A missing or None count is 0 (the SDK leaves the cache counts None when unused).
        """
        return cls(
            input=_count(usage, "input_tokens"),
            output=_count(usage, "output_tokens"),
            cache_write=_count(usage, "cache_creation_input_tokens"),
            cache_read=_count(usage, "cache_read_input_tokens"),
        )

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.input + other.input,
            self.output + other.output,
            self.cache_write + other.cache_write,
            self.cache_read + other.cache_read,
        )


@dataclass(frozen=True)
class DayUsage:
    day: date
    requests: int
    tokens: TokenUsage
    cost_usd: float
    estimated: bool  # part of it was priced with another model's prices


@dataclass(frozen=True)
class UsageSummary:
    requests: int
    tokens: TokenUsage
    cost_usd: float
    estimated: bool
    by_day: list[DayUsage]  # the days with records, oldest first


@dataclass(frozen=True)
class _Totals:
    """What one model used on one day."""

    requests: int = 0
    tokens: TokenUsage = TokenUsage()
    cost_usd: float = 0.0
    estimated: bool = False

    def __add__(self, other: _Totals) -> _Totals:
        return _Totals(
            self.requests + other.requests,
            self.tokens + other.tokens,
            self.cost_usd + other.cost_usd,
            self.estimated or other.estimated,
        )


_Days = dict[date, dict[str, _Totals]]
_Notices = dict[str, list[str]]  # "YYYY-MM" -> the thresholds told that month


def cost_usd(
    model: str, tokens: TokenUsage, prices: Mapping[str, ModelPrices], default_model: str
) -> tuple[float, bool]:
    """The cost of `tokens` in US dollars, and whether it is an estimate.

    `prices` is the whole table (config overrides merged over the built-in prices). A model
    missing from it is priced with `default_model`'s prices, an estimate; `default_model`
    must be in the table (KeyError otherwise; `UsageLedger` refuses such a default).
    """
    rates = prices.get(model)
    if rates is not None:
        return _price(tokens, rates), False
    return _price(tokens, prices[default_model]), True


class UsageLedger:
    def __init__(
        self,
        path: Path | str,
        *,
        prices: Mapping[str, ModelPrices],
        default_model: str,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        """`prices` are the configured ones (`usage.prices`), merged over BUILT_IN_PRICES.

        ConfigError when `default_model` has no prices in them: every response of a model
        missing from the table would then cost nothing, and no budget notice would come.
        """
        self.path = Path(path)
        self.prices: dict[str, ModelPrices] = {**BUILT_IN_PRICES, **prices}
        if default_model not in self.prices:
            raise ConfigError(
                f"usage.default_model {default_model} has no prices: add its input, output, "
                "cache_write, and cache_read prices (US dollars per million tokens) under "
                "usage.prices, or use a model that has them"
            )
        self.default_model = default_model
        self._now = now

    def record(self, model: str, tokens: TokenUsage) -> None:
        """Add one response of `model` to the totals of the current local day."""
        cost, estimated = cost_usd(model, tokens, self.prices, self.default_model)
        response = _Totals(1, tokens, cost, estimated)
        with _LOCK:
            days, notices = self._read()
            today = days.setdefault(self._now().date(), {})
            today[model] = today.get(model, _Totals()) + response
            self._write(days, notices)

    def day(self, day: date) -> UsageSummary:
        """What one local day used."""
        with _LOCK:
            days = self._read()[0]
        return _summary({day: days[day]} if day in days else {})

    def month(self, year: int, month: int) -> UsageSummary:
        """What one calendar month used, day by day."""
        with _LOCK:
            days = self._read()[0]
        return _summary(_in_month(days, year, month))

    def budget_notices(self, budget_usd: float, *, owner_call: str = "Master") -> list[str]:
        """The notices for the thresholds this month's cost has reached and that were not
        told yet this month, in order; they are marked told in the same file."""
        now = self._now()
        month = f"{now:%Y-%m}"
        with _LOCK:
            days, notices = self._read()
            spent = _summary(_in_month(days, now.year, now.month)).cost_usd
            told = notices.get(month, [])
            due = [
                threshold
                for threshold in _BUDGET_THRESHOLDS
                if str(threshold) not in told and spent * 100 >= budget_usd * threshold
            ]
            if not due:
                return []
            notices[month] = [
                str(threshold)
                for threshold in _BUDGET_THRESHOLDS
                if str(threshold) in told or threshold in due
            ]
            self._write(days, notices)
        return [_budget_notice(threshold, spent, budget_usd, owner_call) for threshold in due]

    def _read(self) -> tuple[_Days, _Notices]:
        """The days and the notices; a missing file is an empty ledger and stays missing."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return {}, {}
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem)
            return {}, {}

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not a usage record, so that no write ever replaces it."""
        name = f"{self.path.name}.corrupt-{self._now():%Y%m%d-%H%M%S}"
        target = self.path.with_name(name)
        counter = 2
        while target.exists():
            target = self.path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(self.path, target)
        except OSError as exc:
            logger.error(
                "Usage file %s is not a usage record (%s) and could not be moved aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Usage file %s is not a usage record (%s); moved it to %s and started an empty one",
            self.path,
            problem,
            target,
        )

    def _write(self, days: _Days, notices: _Notices) -> None:
        # Oldest day first, so the owner reads the file in order.
        write_json_atomic(
            self.path,
            {
                "version": FILE_VERSION,
                "days": {
                    day.isoformat(): {model: _dump(totals) for model, totals in models.items()}
                    for day, models in sorted(days.items())
                },
                "notices": dict(sorted(notices.items())),
            },
        )


def _count(usage: object, name: str) -> int:
    value = usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)
    return 0 if value is None else int(value)


def _price(tokens: TokenUsage, rates: ModelPrices) -> float:
    return (
        tokens.input * rates.input
        + tokens.output * rates.output
        + tokens.cache_write * rates.cache_write
        + tokens.cache_read * rates.cache_read
    ) / _TOKENS_PER_PRICE


def _in_month(days: _Days, year: int, month: int) -> _Days:
    return {day: models for day, models in days.items() if (day.year, day.month) == (year, month)}


def _summary(days: _Days) -> UsageSummary:
    by_day: list[DayUsage] = []
    total = _Totals()
    for day, models in sorted(days.items()):
        day_total = sum(models.values(), _Totals())
        by_day.append(
            DayUsage(
                day, day_total.requests, day_total.tokens, day_total.cost_usd, day_total.estimated
            )
        )
        total += day_total
    return UsageSummary(total.requests, total.tokens, total.cost_usd, total.estimated, by_day)


def _budget_notice(threshold: int, spent: float, budget: float, owner_call: str) -> str:
    reached = "batas anggaran" if threshold == 100 else f"{threshold} persen dari anggaran"
    return (
        f"{at_sentence_start(owner_call)}, biaya API bulan ini sekitar {_dollars(spent)}, "
        f"sudah mencapai {reached} {_dollars(budget)}."
    )


def _dollars(amount: float) -> str:
    """`amount` as said in Indonesian: a decimal comma, and cents only when there are any."""
    whole, cents = divmod(round(amount * 100), 100)
    return f"{whole} dolar" if cents == 0 else f"{whole},{cents:02d} dolar"


def _dump(totals: _Totals) -> dict[str, object]:
    return {
        "requests": totals.requests,
        "input": totals.tokens.input,
        "output": totals.tokens.output,
        "cache_write": totals.tokens.cache_write,
        "cache_read": totals.tokens.cache_read,
        "cost_usd": totals.cost_usd,
        "estimated": totals.estimated,
    }


def _parse(raw: object) -> tuple[_Days, _Notices]:
    """The days and the notices in `raw`; ValueError says what is wrong."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version, days, notices = raw["version"], raw["days"], raw["notices"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if not isinstance(days, dict):
        raise ValueError("days must be an object")
    if not isinstance(notices, dict):
        raise ValueError("notices must be an object")
    return (
        {_parse_day(key): _parse_models(value, f"days[{key}]") for key, value in days.items()},
        {
            _parse_month(key): _parse_told(value, f"notices[{key}]")
            for key, value in notices.items()
        },
    )


def _parse_day(key: str) -> date:
    if _ISO_DATE.fullmatch(key):
        try:
            return date.fromisoformat(key)
        except ValueError:
            pass  # a day that does not exist, such as 2026-02-30
    raise ValueError(f"days key {key!r} must be a date written YYYY-MM-DD")


def _parse_models(value: object, where: str) -> dict[str, _Totals]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object of models")
    return {model: _parse_totals(totals, f"{where}[{model}]") for model, totals in value.items()}


def _parse_totals(value: object, where: str) -> _Totals:
    if not isinstance(value, dict) or value.keys() != _TOTALS_KEYS:
        raise ValueError(f"{where} must be an object with exactly the keys {sorted(_TOTALS_KEYS)}")
    for key in _COUNT_KEYS:
        # True is an int to Python, but no count.
        if type(value[key]) is not int or value[key] < 0:
            raise ValueError(f"{where}.{key} must be a whole number of at least 0")
    cost, estimated = value["cost_usd"], value["estimated"]
    if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
        raise ValueError(f"{where}.cost_usd must be a number of at least 0")
    if type(estimated) is not bool:
        raise ValueError(f"{where}.estimated must be true or false")
    tokens = TokenUsage(value["input"], value["output"], value["cache_write"], value["cache_read"])
    return _Totals(value["requests"], tokens, float(cost), estimated)


def _parse_month(key: str) -> str:
    if not _ISO_MONTH.fullmatch(key):
        raise ValueError(f"notices key {key!r} must be a month written YYYY-MM")
    return key


def _parse_told(value: object, where: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(told not in _NOTICE_KEYS for told in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{where} must list each of {', '.join(_NOTICE_KEYS)} at most once")
    return value
