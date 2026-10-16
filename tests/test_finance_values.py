import dataclasses
from datetime import UTC, datetime

import pytest

from waifu.finance.values import (
    CATEGORY_LABELS,
    FINANCE_TOOLS,
    READ_TOOLS,
    UNCATEGORIZED,
    WIB,
    WRITE_TOOLS,
    FinanceError,
    RecordedExpense,
    UndoneExpense,
    expense_question,
    spoken_rupiah,
)


@pytest.mark.parametrize(
    "moment", [datetime(2026, 1, 15, tzinfo=UTC), datetime(2026, 7, 15, tzinfo=UTC)]
)
def test_wib_is_utc_plus_seven_all_year(moment):
    assert moment.astimezone(WIB).utcoffset().total_seconds() == 7 * 3600
    assert moment.astimezone(WIB).tzname() == "WIB"


def test_the_last_evening_of_a_month_in_utc_is_already_the_next_month_in_wib():
    assert datetime(2026, 10, 31, 17, 30, tzinfo=UTC).astimezone(WIB).month == 11


def test_the_categories_are_tepiras_nine_labels_in_its_order():
    assert CATEGORY_LABELS[0] == "Makan harian"
    assert CATEGORY_LABELS[-1] == "Belanja kecil"
    assert len(set(CATEGORY_LABELS)) == len(CATEGORY_LABELS) == 9
    assert UNCATEGORIZED not in CATEGORY_LABELS
    # tepira cuts category names to 40 characters in its answers.
    assert all(len(label) <= 40 for label in CATEGORY_LABELS)


def test_the_finance_tools_are_four_reads_and_two_writes():
    assert READ_TOOLS == (
        "finance_summary",
        "wallet_balances",
        "savings_goals",
        "recent_transactions",
    )
    assert WRITE_TOOLS == ("record_expense", "undo_expense")
    assert FINANCE_TOOLS == READ_TOOLS + WRITE_TOOLS


def test_a_finance_error_carries_its_kind_and_the_message_she_can_say():
    error = FinanceError("busy", "tepira minta jeda sebentar, coba lagi sebentar lagi.")
    assert error.kind == "busy"
    assert error.message == str(error) == "tepira minta jeda sebentar, coba lagi sebentar lagi."


def test_recorded_and_undone_expenses_are_frozen_values():
    recorded = RecordedExpense(
        812, 18000, "Nongkrong / jajan", "kopi", "2026-10-16 08:41", "GoPay", False
    )
    undone = UndoneExpense(812, 18000)
    with pytest.raises(dataclasses.FrozenInstanceError):
        recorded.amount = 80000  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        undone.id = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("amount", "words"),
    [
        (1, "satu"),
        (10, "sepuluh"),
        (11, "sebelas"),
        (17, "tujuh belas"),
        (25, "dua puluh lima"),
        (100, "seratus"),
        (111, "seratus sebelas"),
        (999, "sembilan ratus sembilan puluh sembilan"),
        (1000, "seribu"),
        (1500, "seribu lima ratus"),
        (18000, "delapan belas ribu"),
        (25500, "dua puluh lima ribu lima ratus"),
        (110000, "seratus sepuluh ribu"),
        (1000000, "satu juta"),
        (1001000, "satu juta seribu"),
        (2350000, "dua juta tiga ratus lima puluh ribu"),
        (5000000, "lima juta"),
    ],
)
def test_amounts_are_spoken_in_indonesian_words(amount, words):
    assert spoken_rupiah(amount) == words


@pytest.mark.parametrize("amount", [0, -18000])
def test_only_positive_amounts_are_spoken(amount):
    with pytest.raises(ValueError):
        spoken_rupiah(amount)


def test_the_question_names_the_amount_what_it_was_for_and_the_wallet():
    question = expense_question(18000, "jajan kopi", "GoPay", "Sayang")
    assert (
        question
        == "Catat delapan belas ribu untuk jajan kopi di GoPay, Sayang? Jawab iya kalau jadi."
    )


def test_the_question_calls_the_owner_master_by_default():
    assert expense_question(1500, "parkir", "Tunai").endswith(", Master? Jawab iya kalau jadi.")
