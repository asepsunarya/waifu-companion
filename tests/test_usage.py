import dataclasses
import json
import logging
import threading
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaServerToolUsage, BetaUsage

from waifu import usage
from waifu.config import ConfigError, ModelPrices, UsageConfig
from waifu.usage import (
    BUILT_IN_PRICES,
    DayUsage,
    TokenUsage,
    UsageLedger,
    UsageSummary,
    cost_usd,
)

START = datetime(2026, 10, 14, 9, 30, 15, 250000)
TODAY = START.date()
CORRUPT_NAME = "usage.json.corrupt-20261014-093015"
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
# The SPEC prices of claude-opus-5-5, US dollars per million tokens.
OPUS_PRICES = ModelPrices(input=4.00, output=20.00, cache_write=5.00, cache_read=0.20)
SONNET_PRICES = ModelPrices(input=2.00, output=10.00, cache_write=2.50, cache_read=0.20)
# One typical turn: (1200 * 4 + 350 * 20 + 5000 * 5 + 20000 * 0.20) / 1e6 = 0.0408 USD.
TURN = TokenUsage(input=1200, output=350, cache_write=5000, cache_read=20000)
NO_USAGE = UsageSummary(requests=0, tokens=TokenUsage(), cost_usd=0.0, estimated=False, by_day=[])

NOTICE_80 = (
    "Master, biaya API bulan ini sekitar 16,40 dolar, sudah mencapai 80 persen dari "
    "anggaran 20 dolar."
)
NOTICE_100 = (
    "Master, biaya API bulan ini sekitar 20,40 dolar, sudah mencapai batas anggaran 20 dolar."
)


class Clock:
    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


def make_ledger(tmp_path, clock=None, *, prices=None, default_model=OPUS):
    return UsageLedger(
        tmp_path / "usage.json",
        prices=prices or {},
        default_model=default_model,
        now=clock or Clock(),
    )


def spend(ledger, usd):
    """Record one claude-opus-5-5 response that costs `usd` (input at 4 USD per MTok)."""
    ledger.record(OPUS, TokenUsage(input=round(usd * 250_000)))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_usage(path, days, notices=None):
    path.write_text(
        json.dumps({"version": 1, "days": days, "notices": notices or {}}), encoding="utf-8"
    )


def entry(requests=1, input=0, output=0, cache_write=0, cache_read=0, cost=0.0, estimated=False):
    return {
        "requests": requests,
        "input": input,
        "output": output,
        "cache_write": cache_write,
        "cache_read": cache_read,
        "cost_usd": cost,
        "estimated": estimated,
    }


# --- TokenUsage --------------------------------------------------------------------------------


def test_token_usage_reads_the_four_fields_of_an_sdk_usage():
    sdk_usage = BetaUsage(
        input_tokens=1200,
        output_tokens=350,
        cache_creation_input_tokens=5000,
        cache_read_input_tokens=20000,
    )

    assert TokenUsage.from_usage(sdk_usage) == TokenUsage(
        input=1200, output=350, cache_write=5000, cache_read=20000
    )


def test_token_usage_counts_the_unset_cache_fields_of_an_sdk_usage_as_zero():
    sdk_usage = BetaUsage(input_tokens=10, output_tokens=5)  # the cache fields default to None

    assert TokenUsage.from_usage(sdk_usage) == TokenUsage(
        input=10, output=5, cache_write=0, cache_read=0
    )


def test_token_usage_reads_a_plain_mapping_and_counts_missing_or_none_as_zero():
    raw = {"input_tokens": 7, "output_tokens": None, "cache_read_input_tokens": 3}

    assert TokenUsage.from_usage(raw) == TokenUsage(input=7, output=0, cache_write=0, cache_read=3)


def test_token_usage_reads_an_object_that_lacks_some_fields():
    assert TokenUsage.from_usage(SimpleNamespace(output_tokens=9)) == TokenUsage(output=9)


def test_a_response_without_usage_counts_no_tokens():
    assert TokenUsage.from_usage(None) == TokenUsage(0, 0, 0, 0, 0)


def test_token_usage_reads_the_web_searches_of_an_sdk_usage():
    sdk_usage = BetaUsage(
        input_tokens=10,
        output_tokens=5,
        server_tool_use=BetaServerToolUsage(web_search_requests=2, web_fetch_requests=0),
    )

    assert TokenUsage.from_usage(sdk_usage) == TokenUsage(input=10, output=5, web_searches=2)


@pytest.mark.parametrize(
    "raw",
    [
        BetaUsage(input_tokens=10, output_tokens=5),  # server_tool_use defaults to None
        {"input_tokens": 10, "output_tokens": 5},
        {"input_tokens": 10, "output_tokens": 5, "server_tool_use": None},
        {"input_tokens": 10, "output_tokens": 5, "server_tool_use": {"web_fetch_requests": 1}},
    ],
    ids=["sdk", "mapping", "none", "fetches_only"],
)
def test_a_usage_without_web_search_requests_counts_no_web_searches(raw):
    assert TokenUsage.from_usage(raw) == TokenUsage(input=10, output=5, web_searches=0)


def test_token_usage_is_frozen():
    tokens = TokenUsage(input=1)

    with pytest.raises(dataclasses.FrozenInstanceError):
        tokens.input = 2  # type: ignore[misc]


# --- prices and cost_usd -----------------------------------------------------------------------


def test_the_built_in_prices_are_the_spec_prices_of_claude_opus_5_5():
    assert BUILT_IN_PRICES[OPUS] == OPUS_PRICES
    # The automatic cache she uses keeps entries 5 minutes: a write costs 1.25 x input.
    assert BUILT_IN_PRICES[OPUS].cache_write == 1.25 * BUILT_IN_PRICES[OPUS].input


def test_the_built_in_table_cannot_be_changed_by_accident():
    with pytest.raises(TypeError):
        BUILT_IN_PRICES["claude-x"] = OPUS_PRICES  # type: ignore[index]


@pytest.mark.parametrize(
    "tokens, usd",
    [
        (TokenUsage(input=1_000_000), 4.00),
        (TokenUsage(output=1_000_000), 20.00),
        (TokenUsage(cache_write=1_000_000), 5.00),
        (TokenUsage(cache_read=1_000_000), 0.20),
        (TURN, 0.0408),
        (
            TokenUsage(
                input=2_500_000, output=400_000, cache_write=1_000_000, cache_read=30_000_000
            ),
            29.00,
        ),
    ],
)
def test_the_cost_is_priced_per_million_tokens_with_the_spec_prices(tokens, usd):
    cost, estimated = cost_usd(OPUS, tokens, BUILT_IN_PRICES, OPUS)

    assert cost == pytest.approx(usd)
    assert estimated is False


def test_the_cost_is_right_to_the_cent_for_uneven_counts():
    # 123457 * 4 + 98765 * 20 = 2469128 micro-dollars.
    cost, _ = cost_usd(OPUS, TokenUsage(input=123_457, output=98_765), BUILT_IN_PRICES, OPUS)

    assert round(cost, 2) == 2.47


def test_the_cost_uses_the_prices_in_the_table_it_is_given():
    prices = {
        **BUILT_IN_PRICES,
        OPUS: ModelPrices(input=3, output=15, cache_write=3.75, cache_read=0.3),
    }

    cost, estimated = cost_usd(OPUS, TokenUsage(input=1_000_000, output=1_000_000), prices, OPUS)

    assert cost == pytest.approx(18.00)
    assert estimated is False


def test_a_model_missing_from_the_table_is_priced_with_the_default_model_and_estimated():
    # A server-side fallback answered with another model.
    tokens = TokenUsage(input=1_000_000, output=100_000)

    cost, estimated = cost_usd("claude-opus-5", tokens, BUILT_IN_PRICES, OPUS)

    assert cost == pytest.approx(6.00)
    assert estimated is True


def test_a_model_with_its_own_prices_is_not_estimated_whatever_the_default_model():
    prices = {**BUILT_IN_PRICES, SONNET: SONNET_PRICES}

    cost, estimated = cost_usd(SONNET, TokenUsage(output=1_000_000), prices, OPUS)

    assert cost == pytest.approx(10.00)
    assert estimated is False


def test_an_estimate_needs_prices_for_the_default_model():
    with pytest.raises(KeyError, match=SONNET):
        cost_usd("claude-x", TURN, BUILT_IN_PRICES, SONNET)


# --- the ledger: reading -----------------------------------------------------------------------


def test_a_missing_file_is_an_empty_ledger_and_reading_creates_nothing(tmp_path):
    path = tmp_path / "data" / "usage.json"
    ledger = UsageLedger(path, prices={}, default_model=OPUS, now=Clock())

    assert ledger.day(TODAY) == NO_USAGE
    assert ledger.month(2026, 10) == NO_USAGE
    assert ledger.budget_notices(5.0) == []
    assert not path.parent.exists()


def test_a_default_model_without_prices_is_refused_when_the_ledger_is_built(tmp_path):
    # Otherwise every response of a model missing from the table would cost 0 dollars, and no
    # budget notice would ever come.
    with pytest.raises(ConfigError, match=rf"usage\.default_model {SONNET} .*usage\.prices"):
        make_ledger(tmp_path, default_model=SONNET)


def test_the_ledger_exposes_its_path_merged_prices_and_default_model(tmp_path):
    ledger = make_ledger(tmp_path, prices={SONNET: SONNET_PRICES}, default_model=SONNET)

    assert ledger.path == tmp_path / "usage.json"
    assert ledger.prices == {OPUS: OPUS_PRICES, SONNET: SONNET_PRICES}
    assert ledger.default_model == SONNET


def test_the_ledger_exposes_what_one_web_search_costs(tmp_path):
    configured = UsageLedger(
        tmp_path / "usage.json",
        prices={},
        default_model=OPUS,
        web_search_per_1000_usd=25.0,
        now=Clock(),
    )

    assert configured.web_search_usd == pytest.approx(0.025)
    default = UsageConfig().web_search_per_1000_usd / 1000  # 10 dollars per 1000
    assert make_ledger(tmp_path).web_search_usd == pytest.approx(default)


def test_every_call_rereads_the_file_so_another_process_sees_new_records(tmp_path):
    cyrene, terminal = make_ledger(tmp_path), make_ledger(tmp_path)

    cyrene.record(OPUS, TURN)
    assert terminal.day(TODAY).requests == 1

    cyrene.record(OPUS, TURN)
    assert terminal.month(2026, 10).requests == 2


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    # Notepad and some editors put a UTF-8 BOM in front of a file they save.
    ledger = make_ledger(tmp_path)
    content = json.dumps(
        {"version": 1, "days": {"2026-10-14": {OPUS: entry(requests=3, cost=0.5)}}, "notices": {}}
    )
    ledger.path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))

    assert ledger.day(TODAY).requests == 3


# --- the ledger: recording ---------------------------------------------------------------------


def test_a_record_is_saved_per_local_day_and_model_in_the_spec_file_shape(tmp_path):
    path = tmp_path / "data" / "usage.json"
    ledger = UsageLedger(path, prices={}, default_model=OPUS, now=Clock())

    ledger.record(OPUS, TURN)

    assert read_json(path) == {
        "version": 1,
        "days": {
            "2026-10-14": {
                OPUS: {
                    "requests": 1,
                    "input": 1200,
                    "output": 350,
                    "cache_write": 5000,
                    "cache_read": 20000,
                    "cost_usd": pytest.approx(0.0408),
                    "estimated": False,
                }
            }
        },
        "notices": {},
    }


def test_only_a_model_s_day_with_web_searches_gets_their_count(tmp_path):
    # A day without searches keeps exactly the keys a `waifu run` from before v1.18 reads, so one
    # still running is not made to set the whole file aside as broken.
    clock = Clock()
    ledger = UsageLedger(tmp_path / "usage.json", prices={}, default_model=OPUS, now=clock)
    ledger.record(OPUS, TokenUsage(input=10, web_searches=2))
    clock.advance(days=1)

    ledger.record(OPUS, TokenUsage(input=10))

    days = read_json(ledger.path)["days"]
    assert days["2026-10-14"][OPUS]["web_searches"] == 2
    assert days["2026-10-15"][OPUS].keys() == {
        "requests",
        "input",
        "output",
        "cache_write",
        "cache_read",
        "cost_usd",
        "estimated",
    }


def test_records_of_one_model_on_one_day_add_up(tmp_path):
    ledger = make_ledger(tmp_path)

    ledger.record(OPUS, TURN)
    ledger.record(OPUS, TokenUsage(input=100, output=50))  # 0.0014 USD

    summary = ledger.day(TODAY)
    assert summary.requests == 2
    assert summary.tokens == TokenUsage(input=1300, output=400, cache_write=5000, cache_read=20000)
    assert summary.cost_usd == pytest.approx(0.0422)
    assert summary.estimated is False


def test_each_model_keeps_its_own_totals_and_the_day_sums_them(tmp_path):
    ledger = make_ledger(tmp_path)

    ledger.record(OPUS, TURN)
    ledger.record("claude-opus-5", TokenUsage(input=1000))  # a fallback: 0.004 USD, estimated

    saved = read_json(ledger.path)["days"]["2026-10-14"]
    assert sorted(saved) == ["claude-opus-5", OPUS]
    assert saved["claude-opus-5"]["estimated"] is True
    assert saved[OPUS]["estimated"] is False
    tokens = TokenUsage(input=2200, output=350, cache_write=5000, cache_read=20000)
    assert ledger.day(TODAY) == UsageSummary(
        requests=2,
        tokens=tokens,
        cost_usd=pytest.approx(0.0448),
        estimated=True,
        by_day=[DayUsage(TODAY, 2, tokens, pytest.approx(0.0448), True)],
    )


def test_configured_prices_are_merged_over_the_built_in_table(tmp_path):
    ledger = make_ledger(tmp_path, prices={SONNET: SONNET_PRICES})

    ledger.record(OPUS, TokenUsage(input=1_000_000))  # built-in: 4.00
    ledger.record(SONNET, TokenUsage(output=1_000_000))  # configured: 10.00

    summary = ledger.day(TODAY)
    assert summary.cost_usd == pytest.approx(14.00)
    assert summary.estimated is False


def test_a_configured_price_wins_over_the_built_in_one(tmp_path):
    cheaper = ModelPrices(input=1, output=2, cache_write=3, cache_read=4)
    ledger = make_ledger(tmp_path, prices={OPUS: cheaper})

    ledger.record(OPUS, TokenUsage(1_000_000, 1_000_000, 1_000_000, 1_000_000))

    assert ledger.day(TODAY).cost_usd == pytest.approx(10.00)


def test_each_web_search_costs_a_thousandth_of_the_configured_price_on_top_of_the_tokens(
    tmp_path,
):
    ledger = UsageLedger(
        tmp_path / "usage.json",
        prices={},
        default_model=OPUS,
        web_search_per_1000_usd=25.0,
        now=Clock(),
    )

    ledger.record(OPUS, TokenUsage(input=1_000_000, web_searches=4))  # 4.00 + 4 x 0.025

    summary = ledger.day(TODAY)
    assert summary.cost_usd == pytest.approx(4.10)
    assert summary.tokens == TokenUsage(input=1_000_000, web_searches=4)
    assert summary.estimated is False


def test_web_searches_cost_what_the_config_says_by_default(tmp_path):
    ledger = make_ledger(tmp_path)

    ledger.record(OPUS, TokenUsage(web_searches=3))

    per_search = UsageConfig().web_search_per_1000_usd / 1000  # 10 dollars per 1000
    assert ledger.day(TODAY).cost_usd == pytest.approx(3 * per_search)


def test_web_searches_add_up_per_day_and_month_whichever_model_searched(tmp_path):
    ledger = make_ledger(tmp_path)

    ledger.record(OPUS, TokenUsage(output=10, web_searches=2))
    ledger.record(OPUS, TokenUsage(web_searches=1))
    ledger.record("claude-opus-5", TokenUsage(web_searches=1))  # a server-side fallback

    saved = read_json(ledger.path)["days"]["2026-10-14"]
    assert (saved[OPUS]["web_searches"], saved["claude-opus-5"]["web_searches"]) == (3, 1)
    assert ledger.day(TODAY).tokens == TokenUsage(output=10, web_searches=4)
    assert ledger.month(2026, 10).by_day[0].tokens.web_searches == 4


def test_a_day_recorded_before_web_searches_were_counted_reads_as_none(tmp_path):
    ledger = make_ledger(tmp_path)
    write_usage(ledger.path, {"2026-10-14": {OPUS: entry(requests=2, input=10, cost=0.5)}})

    assert ledger.day(TODAY).tokens == TokenUsage(input=10, web_searches=0)

    ledger.record(OPUS, TokenUsage(web_searches=2))

    assert ledger.day(TODAY).tokens == TokenUsage(input=10, web_searches=2)
    assert read_json(ledger.path)["days"]["2026-10-14"][OPUS]["web_searches"] == 2


def test_a_fallback_model_is_priced_with_the_configured_default_model(tmp_path):
    ledger = make_ledger(tmp_path, prices={SONNET: SONNET_PRICES}, default_model=SONNET)

    ledger.record("claude-opus-5", TokenUsage(input=1_000_000))

    summary = ledger.day(TODAY)
    assert summary.cost_usd == pytest.approx(2.00)
    assert summary.estimated is True


def test_a_response_after_midnight_counts_for_the_new_day(tmp_path):
    clock = Clock(datetime(2026, 10, 14, 23, 59, 30))
    ledger = make_ledger(tmp_path, clock)

    ledger.record(OPUS, TokenUsage(input=1_000_000))
    clock.advance(minutes=1)
    ledger.record(OPUS, TokenUsage(output=1_000_000))

    assert ledger.day(date(2026, 10, 14)).cost_usd == pytest.approx(4.00)
    assert ledger.day(date(2026, 10, 15)).cost_usd == pytest.approx(20.00)
    assert ledger.day(date(2026, 10, 16)) == NO_USAGE


def test_a_response_after_the_month_changes_counts_for_the_new_month(tmp_path):
    clock = Clock(datetime(2026, 10, 31, 23, 50))
    ledger = make_ledger(tmp_path, clock)

    ledger.record(OPUS, TokenUsage(input=1_000_000))
    clock.advance(minutes=20)
    ledger.record(OPUS, TokenUsage(input=500_000))

    assert ledger.month(2026, 10).cost_usd == pytest.approx(4.00)
    november = ledger.month(2026, 11)
    assert november.cost_usd == pytest.approx(2.00)
    assert [day.day for day in november.by_day] == [date(2026, 11, 1)]


def test_a_month_sums_only_its_own_days_and_lists_them_oldest_first(tmp_path):
    ledger = make_ledger(tmp_path)
    write_usage(
        ledger.path,
        {
            "2026-10-20": {OPUS: entry(requests=2, input=10, cost=1.25)},
            "2026-09-30": {OPUS: entry(requests=9, cost=9.0)},
            "2026-10-03": {
                OPUS: entry(requests=1, output=5, cost=0.5),
                "claude-opus-5": entry(requests=1, cache_read=7, cost=0.25, estimated=True),
            },
            "2026-11-01": {OPUS: entry(requests=4, cost=4.0)},
        },
    )

    assert ledger.month(2026, 10) == UsageSummary(
        requests=4,
        tokens=TokenUsage(input=10, output=5, cache_read=7),
        cost_usd=pytest.approx(2.00),
        estimated=True,
        by_day=[
            DayUsage(date(2026, 10, 3), 2, TokenUsage(output=5, cache_read=7), 0.75, True),
            DayUsage(date(2026, 10, 20), 2, TokenUsage(input=10), 1.25, False),
        ],
    )


def test_a_month_never_counts_the_same_month_of_another_year(tmp_path):
    # The ledger keeps every day: a year on, both Octobers are in the file.
    ledger = make_ledger(tmp_path, Clock(datetime(2027, 10, 20, 9, 0)))
    write_usage(
        ledger.path,
        {
            "2026-10-14": {OPUS: entry(requests=3, cost=4.0)},
            "2027-10-14": {OPUS: entry(requests=1, cost=1.0)},
        },
    )

    october = ledger.month(2027, 10)

    assert (october.requests, october.cost_usd) == (1, 1.0)
    assert [day.day for day in october.by_day] == [date(2027, 10, 14)]
    assert ledger.budget_notices(5.0) == []  # 1 dollar spent this month, not 5


def test_a_day_summary_lists_only_that_day(tmp_path):
    ledger = make_ledger(tmp_path)
    write_usage(
        ledger.path,
        {
            "2026-10-13": {OPUS: entry(requests=5, cost=5.0)},
            "2026-10-14": {OPUS: entry(requests=1, input=3, cost=0.5)},
        },
    )

    assert ledger.day(TODAY) == UsageSummary(
        requests=1,
        tokens=TokenUsage(input=3),
        cost_usd=0.5,
        estimated=False,
        by_day=[DayUsage(TODAY, 1, TokenUsage(input=3), 0.5, False)],
    )


# --- budget notices ----------------------------------------------------------------------------


def test_no_notice_below_80_percent_of_the_budget(tmp_path):
    ledger = make_ledger(tmp_path)
    spend(ledger, 15.99)

    assert ledger.budget_notices(20.0) == []
    assert read_json(ledger.path)["notices"] == {}


def test_the_80_percent_notice_is_given_once_and_marked_in_the_same_file(tmp_path):
    ledger = make_ledger(tmp_path)
    spend(ledger, 16.40)

    assert ledger.budget_notices(20.0) == [NOTICE_80]
    assert read_json(ledger.path)["notices"] == {"2026-10": ["80"]}

    spend(ledger, 0.50)
    assert ledger.budget_notices(20.0) == []


def test_exactly_80_percent_counts_as_reached(tmp_path):
    ledger = make_ledger(tmp_path)
    spend(ledger, 16.00)

    assert ledger.budget_notices(20.0) == [
        "Master, biaya API bulan ini sekitar 16 dolar, sudah mencapai 80 persen dari "
        "anggaran 20 dolar."
    ]


def test_the_100_percent_notice_is_given_once_after_the_80_percent_one(tmp_path):
    ledger = make_ledger(tmp_path)
    spend(ledger, 16.40)
    assert ledger.budget_notices(20.0) == [NOTICE_80]

    spend(ledger, 4.00)

    assert ledger.budget_notices(20.0) == [NOTICE_100]
    assert read_json(ledger.path)["notices"] == {"2026-10": ["80", "100"]}
    spend(ledger, 1.00)
    assert ledger.budget_notices(20.0) == []


def test_passing_both_thresholds_at_once_gives_both_notices_in_order(tmp_path):
    ledger = make_ledger(tmp_path)
    spend(ledger, 20.40)

    assert ledger.budget_notices(20.0) == [
        "Master, biaya API bulan ini sekitar 20,40 dolar, sudah mencapai 80 persen dari "
        "anggaran 20 dolar.",
        NOTICE_100,
    ]
    assert read_json(ledger.path)["notices"] == {"2026-10": ["80", "100"]}


def test_the_notices_come_again_in_the_next_month(tmp_path):
    clock = Clock(datetime(2026, 10, 31, 22, 0))
    ledger = make_ledger(tmp_path, clock)
    spend(ledger, 20.40)
    assert len(ledger.budget_notices(20.0)) == 2

    clock.current = datetime(2026, 11, 1, 9, 0)
    assert ledger.budget_notices(20.0) == []  # a new month starts at zero

    spend(ledger, 16.40)
    assert ledger.budget_notices(20.0) == [NOTICE_80]
    assert read_json(ledger.path)["notices"] == {"2026-10": ["80", "100"], "2026-11": ["80"]}


def test_a_notice_opens_with_the_owner_call(tmp_path):
    ledger = make_ledger(tmp_path)
    spend(ledger, 16.40)

    assert ledger.budget_notices(20.0, owner_call="sayang") == [
        "Sayang, biaya API bulan ini sekitar 16,40 dolar, sudah mencapai 80 persen dari "
        "anggaran 20 dolar."
    ]


def test_a_notice_says_cents_with_a_decimal_comma_and_whole_dollars_without(tmp_path):
    ledger = make_ledger(tmp_path)
    spend(ledger, 6.00)

    assert ledger.budget_notices(7.5) == [
        "Master, biaya API bulan ini sekitar 6 dolar, sudah mencapai 80 persen dari "
        "anggaran 7,50 dolar."
    ]


def test_a_budget_check_without_new_notices_writes_nothing(tmp_path, monkeypatch):
    ledger = make_ledger(tmp_path)
    spend(ledger, 16.40)
    ledger.budget_notices(20.0)
    saved = ledger.path.read_bytes()

    def refused_replace(src, dst):
        raise AssertionError("nothing new to save")

    monkeypatch.setattr(usage.os, "replace", refused_replace)

    assert ledger.budget_notices(20.0) == []
    assert ledger.path.read_bytes() == saved
    assert [path.name for path in tmp_path.iterdir()] == ["usage.json"]


# --- concurrency -------------------------------------------------------------------------------


def run_in_threads(target, count=8):
    errors = []

    def guarded(worker):
        try:
            target(worker)
        except Exception as exc:  # surfaced by the caller's assertion
            errors.append(exc)

    threads = [threading.Thread(target=guarded, args=(worker,)) for worker in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_concurrent_records_from_threads_keep_every_response(tmp_path):
    ledgers = [make_ledger(tmp_path), make_ledger(tmp_path)]

    def record(worker):
        for _ in range(10):
            ledgers[worker % 2].record(OPUS, TokenUsage(input=100, output=10))  # 0.0006 USD

    assert run_in_threads(record) == []
    summary = ledgers[0].day(TODAY)
    assert summary.requests == 80
    assert summary.tokens == TokenUsage(input=8000, output=800)
    assert summary.cost_usd == pytest.approx(80 * 0.0006)


def test_concurrent_budget_checks_give_each_notice_only_once(tmp_path):
    ledgers = [make_ledger(tmp_path), make_ledger(tmp_path)]
    spend(ledgers[0], 16.40)
    given = []

    def check(worker):
        given.extend(ledgers[worker % 2].budget_notices(20.0))

    assert run_in_threads(check) == []
    assert given == [NOTICE_80]


# --- atomic writes -----------------------------------------------------------------------------


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    ledger = make_ledger(tmp_path)

    ledger.record(OPUS, TURN)

    assert [path.name for path in tmp_path.iterdir()] == ["usage.json"]


def test_a_failed_replace_keeps_the_previous_file_intact(tmp_path, monkeypatch):
    ledger = make_ledger(tmp_path)
    ledger.record(OPUS, TURN)
    saved = ledger.path.read_bytes()

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(usage.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        ledger.record(OPUS, TURN)

    assert ledger.path.read_bytes() == saved
    assert [path.name for path in tmp_path.iterdir()] == ["usage.json"]


def test_a_windows_path_with_spaces_works(tmp_path):
    path = tmp_path / "Data Cyrene" / "biaya API" / "pemakaian Master.json"
    ledger = UsageLedger(path, prices={}, default_model=OPUS, now=Clock())

    ledger.record(OPUS, TURN)

    assert UsageLedger(path, prices={}, default_model=OPUS).day(TODAY).requests == 1


# --- corrupt files -----------------------------------------------------------------------------

VALID_DAY = {OPUS: entry(requests=1, input=10, output=5, cost=0.00014)}


def file_with(**changes):
    return json.dumps({"version": 1, "days": {}, "notices": {}} | changes).encode()


def day_with(**changes):
    return {"2026-10-14": {OPUS: VALID_DAY[OPUS] | changes}}


CORRUPT_CONTENTS = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version": 1, "days": {}, "notices": {"\xff": []}}',
    "a list": b"[]",
    "missing notices": json.dumps({"version": 1, "days": {}}).encode(),
    "an extra key": file_with(total=3),
    "another version": file_with(version=2),
    "version true": file_with(version=True),
    "days a list": file_with(days=[]),
    "notices a list": file_with(notices=[]),
    "day written another way": file_with(days={"14-10-2026": VALID_DAY}),
    "day with a time": file_with(days={"2026-10-14T09:00": VALID_DAY}),
    "day that does not exist": file_with(days={"2026-02-30": VALID_DAY}),
    "day a list of models": file_with(days={"2026-10-14": [VALID_DAY]}),
    "model entry a number": file_with(days={"2026-10-14": {OPUS: 5}}),
    "entry without estimated": file_with(
        days={"2026-10-14": {OPUS: {k: v for k, v in VALID_DAY[OPUS].items() if k != "estimated"}}}
    ),
    "entry with an extra key": file_with(days=day_with(model=OPUS)),
    "requests negative": file_with(days=day_with(requests=-1)),
    "requests true": file_with(days=day_with(requests=True)),
    "input a float": file_with(days=day_with(input=10.0)),
    "output text": file_with(days=day_with(output="5")),
    "cache_write null": file_with(days=day_with(cache_write=None)),
    "web_searches negative": file_with(days=day_with(web_searches=-1)),
    "web_searches a float": file_with(days=day_with(web_searches=1.0)),
    "web_searches true": file_with(days=day_with(web_searches=True)),
    "web_searches null": file_with(days=day_with(web_searches=None)),
    "cost text": file_with(days=day_with(cost_usd="0.1")),
    "cost negative": file_with(days=day_with(cost_usd=-0.1)),
    "cost true": file_with(days=day_with(cost_usd=True)),
    "cost not a number": file_with(days=day_with(cost_usd=float("nan"))),
    "cost infinite": file_with(days=day_with(cost_usd=float("inf"))),
    "estimated a number": file_with(days=day_with(estimated=0)),
    "notice month that does not exist": file_with(notices={"2026-13": ["80"]}),
    "notice month a day": file_with(notices={"2026-10-14": ["80"]}),
    "notices not a list": file_with(notices={"2026-10": "80"}),
    "unknown notice": file_with(notices={"2026-10": ["90"]}),
    "notice a number": file_with(notices={"2026-10": [80]}),
    "notice given twice": file_with(notices={"2026-10": ["80", "80"]}),
}


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
def test_a_corrupt_file_is_moved_aside_logged_and_the_ledger_starts_empty(
    tmp_path, caplog, content
):
    ledger = make_ledger(tmp_path)
    ledger.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.usage"):
        assert ledger.day(TODAY) == NO_USAGE

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert not ledger.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_a_corrupt_file_is_never_overwritten_by_a_later_record(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.path.write_bytes(b"{not json")

    ledger.record(OPUS, TURN)

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"{not json"
    assert ledger.day(TODAY).requests == 1


def test_a_corrupt_copy_with_the_same_stamp_gets_a_counter(tmp_path):
    ledger = make_ledger(tmp_path)
    (tmp_path / CORRUPT_NAME).write_bytes(b"first")
    (tmp_path / f"{CORRUPT_NAME}-2").write_bytes(b"second")
    ledger.path.write_bytes(b"third")

    assert ledger.month(2026, 10) == NO_USAGE

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"first"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_bytes() == b"second"
    assert (tmp_path / f"{CORRUPT_NAME}-3").read_bytes() == b"third"


def test_nothing_is_written_when_a_corrupt_file_cannot_be_moved(tmp_path, monkeypatch):
    ledger = make_ledger(tmp_path)
    ledger.path.write_bytes(b"{not json")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(usage.os, "rename", failing_rename)
    with pytest.raises(OSError, match="file in use"):
        ledger.record(OPUS, TURN)

    assert ledger.path.read_bytes() == b"{not json"
    assert [path.name for path in tmp_path.iterdir()] == ["usage.json"]
