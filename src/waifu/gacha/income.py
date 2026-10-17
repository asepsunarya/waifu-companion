"""What Master earned in their HoYoverse games in a month, from HoYoLAB's monthly diaries.

Genshin's Traveler's Diary, HSR's Trailblaze Monthly Calendar, and ZZZ's Inter-Knot monthly
report, read through genshin.py, become a `GameIncome` here: the premium currency earned and
what it is worth in pulls, the other currencies the diary counts, the month before, where the
premium currency came from, and today's figure. The readers take genshin.py's models as `Any`;
nothing here imports genshin.py, and nothing holds the uid, the nickname, the avatar, or the
region. HoYoLAB keeps this month and the two before, counted in server time.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol

from waifu.gacha.games import PULL_COST, checkin_day

if TYPE_CHECKING:
    from waifu.gacha.hoyolab import GachaError

MONTHS_KEPT = 3  # HoYoLAB's diaries give this month and the two before
ZZZ_PREMIUM = "PolychromesData"  # genshin.py's ZZZCurrencyType.POLYCHROME value
HSR_PASS = "Star Rail Pass"  # HoYoLAB does not say whether Special or regular
# ZZZ's Polychrome sources come as keys only (genshin.py's PolychromeIncomeType values).
ZZZ_SOURCES = {
    "daily_activity_rewards": "Daily Activity",
    "growth_rewards": "Growth",
    "shiyu_rewards": "Shiyu Defense",
    "mail_rewards": "Mail",
    "hollow_rewards": "Hollow Zero",
    "event_rewards": "Events",
    "other_rewards": "Other",
}
_MONTH = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class IncomeCategory:
    """Where some of the premium currency came from, and its share in percent."""

    name: str
    amount: int
    percent: int


@dataclass(frozen=True)
class IncomeCurrency:
    """Another currency the diary counts: Mora, Star Rail Passes, Master Tapes, Boopons."""

    name: str
    amount: int


@dataclass(frozen=True)
class GameIncome:
    game: str
    month: date  # its first day
    earned: int  # the premium currency: Primogem, Stellar Jade, or Polychrome
    others: tuple[IncomeCurrency, ...]  # never part of `pulls`
    last_month: int | None  # the month before, as HoYoLAB counts it (none for ZZZ)
    change_percent: int | None
    sources: tuple[IncomeCategory, ...]  # most first, without the empty ones
    today: int | None  # this month only (none for ZZZ)
    yesterday: int | None  # HSR's, this month only

    @property
    def pulls(self) -> int:
        """What the premium currency earned is worth in pulls."""
        return self.earned // PULL_COST


class IncomeSource(Protocol):
    """Where the income is read from: `HoyolabClient.income`."""

    def income(
        self, games: Iterable[str], month: date, *, this_month: bool
    ) -> dict[str, GameIncome | GachaError]: ...


def read_genshin_income(model: Any, month: date, *, this_month: bool) -> GameIncome:
    """`month` from genshin.py's `Diary` (its own month is only the month's number)."""
    data = model.data
    return GameIncome(
        game="genshin",
        month=month,
        earned=data.current_primogems,
        others=(IncomeCurrency("Mora", data.current_mora),),
        last_month=data.last_primogems,
        change_percent=data.primogems_rate,
        sources=_sources((each.name, each.amount, each.percentage) for each in data.categories),
        today=model.day_data.current_primogems if this_month else None,
        yesterday=None,
    )


def read_hsr_income(model: Any, month: date, *, this_month: bool) -> GameIncome:
    """`month` from genshin.py's `StarRailDiary` (its own month is YYYYMM as a number)."""
    data, day = model.data, model.day_data
    return GameIncome(
        game="hsr",
        month=month,
        earned=data.current_hcoin,
        others=(IncomeCurrency(HSR_PASS, data.current_rails_pass),),
        last_month=data.last_hcoin,
        change_percent=data.hcoin_rate,
        sources=_sources((each.name, each.amount, each.percentage) for each in data.categories),
        today=day.current_hcoin if this_month else None,
        yesterday=day.last_hcoin if this_month else None,
    )


def read_zzz_income(model: Any, month: date, *, this_month: bool) -> GameIncome:
    """`month` from genshin.py's `ZZZDiary`, which gives no month before and no day figures, so
    `this_month` changes nothing. Without a Polychrome entry it cannot be read: a ValueError."""
    currencies = model.income.currencies
    premium = [each.num for each in currencies if each.type.value == ZZZ_PREMIUM]
    if not premium:
        raise ValueError("The ZZZ monthly report has no Polychrome entry")
    return GameIncome(
        game="zzz",
        month=month,
        earned=premium[0],
        others=tuple(
            IncomeCurrency(each.name, each.num)
            for each in currencies
            if each.type.value != ZZZ_PREMIUM
        ),
        last_month=None,
        change_percent=None,
        sources=_sources(
            (ZZZ_SOURCES[each.source.value], each.num, each.percent)
            for each in model.income.polychrome_incomes
        ),
        today=None,
        yesterday=None,
    )


def answers_month(game_id: str, model: Any, month: date) -> bool:
    """Whether genshin.py's diary model is of `month`: Genshin's month is 1-12, HSR's YYYYMM as
    a number, and ZZZ's `data_month` text holds YYYYMM's digits."""
    if game_id == "genshin":
        return model.month == month.month
    if game_id == "hsr":
        return model.month == month.year * 100 + month.month
    return "".join(char for char in model.data_month if char.isdigit()) == f"{month:%Y%m}"


def income_months(now: datetime, offset: int) -> tuple[date, ...]:
    """The months HoYoLAB keeps at `now` (aware), newest first, each as its first day: this
    month in server time (it turns at midnight there) and the ones before."""
    today = checkin_day(now, offset)
    index = today.year * 12 + today.month - 1  # months since year 0, so a year turns by itself
    return tuple(date(n // 12, n % 12 + 1, 1) for n in range(index, index - MONTHS_KEPT, -1))


def month_text(month: date) -> str:
    """`2026-10`."""
    return f"{month:%Y-%m}"


def parse_month(text: str) -> date:
    """`2026-10` as its first day; anything else is a ValueError."""
    match = _MONTH.fullmatch(text)
    if match is None:
        raise ValueError(f"not a YYYY-MM month: {text!r}")
    return date(int(match[1]), int(match[2]), 1)


def _sources(entries: Iterable[tuple[str, int, int]]) -> tuple[IncomeCategory, ...]:
    """The sources that brought something, most first (equal ones in HoYoLAB's order)."""
    found = [
        IncomeCategory(name, amount, percent) for name, amount, percent in entries if amount > 0
    ]
    return tuple(sorted(found, key=lambda source: -source.amount))
