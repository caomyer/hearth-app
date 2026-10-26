"""Hearth's own books held against a ledger the household kept before (``hearth compare``).

A ledger reconciled to the bank's statements is ground truth: where hearth's books and it
disagree, the comparison must say so, by how much, and which rows make the difference; and it
must not mistake a row the two books date differently for a row one of them is missing. Every
value here is invented.
"""

import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader
from beancount.core.data import Directive

from hearth import db, ownbooks
from hearth.cli import main
from hearth.comparison import (
    ComparisonError,
    Cycle,
    Verdict,
    compare,
    describe,
    statement_cycles,
)
from hearth.config import DATA_DIR_VARIABLE, LEDGER_VARIABLE
from hearth.plaid.fake import FakeAccount, FakeInstitution, FakeTransaction
from tests.conftest import FakeClock
from tests.firstday import CARD, FirstDay, first_day, until_booked

CARD_ACCOUNT = "Liabilities:Avery:Brackenfield:Kestrel"
CHECKING_ACCOUNT = "Assets:Avery:Brackenfield:Checking"

HEARTH = """
2026-08-01 open Liabilities:Avery:Brackenfield:Kestrel USD
2026-08-01 open Expenses:Groceries USD
2026-08-01 open Equity:Opening-Balances USD

2026-08-02 * "Opening balance"
  balance: "current"
  Liabilities:Avery:Brackenfield:Kestrel  -230.00 USD
  Equity:Opening-Balances

2026-08-03 ! "Larkspur Grocer"
  Liabilities:Avery:Brackenfield:Kestrel  -40.10 USD
  Expenses:Groceries

2026-08-31 ! "Tinderbox Coffee"
  Liabilities:Avery:Brackenfield:Kestrel  -12.35 USD
  Expenses:Groceries

2026-09-18 ! "Quill Stationers"
  Liabilities:Avery:Brackenfield:Kestrel  -0.20 USD
  Expenses:Groceries
"""
"""Hearth's books for one card: an opening balance worked out from what the bank reports, as
``hearth.plaid.opening`` writes it, and three rows on the days the bank posted them."""

LEDGER = """
2026-01-01 open Liabilities:Avery:Brackenfield:Kestrel USD
2026-01-01 open Expenses:Groceries USD
2026-01-01 open Equity:Opening-Balances USD

2026-08-02 * "Opening balance, from the statement"
  Liabilities:Avery:Brackenfield:Kestrel  -230.00 USD
  Equity:Opening-Balances

2026-08-03 * "Larkspur Grocer"
  Liabilities:Avery:Brackenfield:Kestrel  -40.10 USD
  Expenses:Groceries

2026-08-29 * "Tinderbox Coffee"
  post-date: 2026-08-31
  Liabilities:Avery:Brackenfield:Kestrel  -12.35 USD
  Expenses:Groceries

2026-09-18 * "Quill Stationers"
  Liabilities:Avery:Brackenfield:Kestrel  -0.20 USD
  Expenses:Groceries

2026-09-30 * "Window ends"
  Expenses:Groceries  0.00 USD
  Equity:Opening-Balances
"""
"""The same card in a ledger built from its statements: the coffee booked on the day it was
bought, Aug 29, and kept with the day it posted, Aug 31, as a statement import keeps it."""

CYCLES = (
    Cycle(CARD_ACCOUNT, date(2026, 8, 3), date(2026, 8, 31)),
    Cycle(CARD_ACCOUNT, date(2026, 9, 1), date(2026, 9, 30)),
)
"""Two statement cycles, closing Aug 31 and Sep 30."""

THROUGH = date(2026, 10, 15)


def hearths(text: str) -> list[Directive]:
    """`text` as hearth's own entries: each carries the origin every entry hearth derives does."""
    entries = _parsed(text)
    return [e._replace(meta={**e.meta, "filename": ownbooks.ORIGIN}) for e in entries]


def _parsed(text: str) -> list[Directive]:
    entries, errors, _ = loader.load_string(text)
    assert errors == []
    return list(entries)


def _without(text: str, line: str) -> str:
    """`text` with the transaction that `line` heads, and its postings, taken out."""
    head, _, rest = text.partition(line)
    _, _, after = rest.partition("\n\n")
    return head + after


def test_books_that_agree_agree_at_every_statement_close() -> None:
    found = compare(hearths(HEARTH), _parsed(LEDGER), through=THROUGH, cycles=CYCLES)

    (account,) = found.accounts
    assert account.unbalanced is None
    assert [(s.day, s.verdict) for s in account.steps] == [
        (date(2026, 8, 2), Verdict.AGREES),
        (date(2026, 8, 31), Verdict.AGREES),
        (date(2026, 9, 30), Verdict.AGREES),
    ]
    closing = account.steps[1]
    assert (closing.ours, closing.theirs) == (Decimal("-282.45"), Decimal("-282.45"))
    assert (closing.moved_ours, closing.since) == (Decimal("-52.45"), date(2026, 8, 2))


def test_a_ledger_row_is_read_on_the_day_it_posted() -> None:
    """The coffee was bought on Aug 29 and posted on Aug 31: read on the day it was bought,
    the books would differ at Aug 30 though both hold the row."""
    found = compare(
        hearths(HEARTH),
        _parsed(LEDGER),
        through=THROUGH,
        cycles=[Cycle(CARD_ACCOUNT, date(2026, 8, 3), date(2026, 8, 30))],
    )

    assert [s.verdict for s in found.accounts[0].steps] == [Verdict.AGREES, Verdict.AGREES]


def test_a_row_only_hearth_holds_is_named_with_what_it_makes_the_difference() -> None:
    ledger = _without(LEDGER, '2026-09-18 * "Quill Stationers"')

    found = compare(hearths(HEARTH), _parsed(ledger), through=THROUGH, cycles=CYCLES)

    *_, september = found.accounts[0].steps
    assert september.verdict is Verdict.DIFFERS
    assert september.balance_difference == Decimal("-0.20")
    assert september.moved_difference == Decimal("-0.20")
    assert [(r.day, r.amount, r.narration) for r in september.only_ours] == [
        (date(2026, 9, 18), Decimal("-0.20"), "Quill Stationers")
    ]
    assert september.only_theirs == ()
    lines = list(describe(found, "ledger.beancount"))
    assert (
        "  2026-09-01..2026-09-30  DIFFERS  balance differs by -0.20: hearth -282.65,"
        in "\n".join(lines)
    )
    assert "      only in hearth      2026-09-18  -0.20  Quill Stationers" in lines
    assert lines[-1] == ("Checkpoints: 2 agree, 0 agree but for the day rows are dated, 1 differ.")


def test_a_row_only_the_ledger_holds_is_named_too() -> None:
    hearth = _without(HEARTH, '2026-09-18 ! "Quill Stationers"')

    found = compare(hearths(hearth), _parsed(LEDGER), through=THROUGH, cycles=CYCLES)

    *_, september = found.accounts[0].steps
    assert september.verdict is Verdict.DIFFERS
    assert [r.amount for r in september.only_theirs] == [Decimal("-0.20")]


def test_a_row_the_books_date_on_either_side_of_a_checkpoint_is_not_a_missing_row() -> None:
    """A ledger that books every row on the day it was made, with no post-date beside it,
    differs from hearth at a month end a row was made before and posted after."""
    ledger = LEDGER.replace("  post-date: 2026-08-31\n", "")

    found = compare(
        hearths(HEARTH),
        _parsed(ledger),
        through=THROUGH,
        cycles=[Cycle(CARD_ACCOUNT, date(2026, 8, 3), date(2026, 8, 30))],
    )

    *_, august = found.accounts[0].steps
    assert august.verdict is Verdict.DATED_APART
    assert august.balance_difference == Decimal("12.35")
    assert (august.only_ours, august.only_theirs) == ((), ())
    ((pair),) = august.apart
    assert (pair.ours.day, pair.theirs.day) == (date(2026, 8, 31), date(2026, 8, 29))
    assert (
        "      dated apart         2026-08-31 in hearth, 2026-08-29 in the ledger  -12.35"
        "  Tinderbox Coffee"
    ) in list(describe(found, "ledger.beancount"))


def test_a_difference_older_than_the_rows_is_never_put_down_to_dating() -> None:
    ledger = LEDGER.replace("-230.00 USD", "-200.00 USD").replace("  post-date: 2026-08-31\n", "")

    found = compare(
        hearths(HEARTH),
        _parsed(ledger),
        through=THROUGH,
        cycles=[Cycle(CARD_ACCOUNT, date(2026, 8, 3), date(2026, 8, 30))],
    )

    assert [s.verdict for s in found.accounts[0].steps] == [Verdict.DIFFERS, Verdict.DIFFERS]


def test_hearths_opening_balance_is_never_matched_as_a_row() -> None:
    """It says what the card held before its history: a charge of the same amount days later
    is a row only the ledger holds, never the opening dated apart."""
    ledger = LEDGER.replace(
        '2026-08-02 * "Opening balance, from the statement"\n'
        "  Liabilities:Avery:Brackenfield:Kestrel  -230.00 USD",
        '2026-07-01 * "Opening balance, from the statement"\n'
        "  Liabilities:Avery:Brackenfield:Kestrel  -100.00 USD\n"
        "  Equity:Opening-Balances\n\n"
        '2026-07-20 * "Marrowbone Transit"\n'
        "  Liabilities:Avery:Brackenfield:Kestrel  -130.00 USD",
    ).replace(
        '2026-08-03 * "Larkspur Grocer"',
        '2026-08-04 * "Hollins Lettings"\n'
        "  Liabilities:Avery:Brackenfield:Kestrel  -230.00 USD\n"
        "  Expenses:Groceries\n\n"
        '2026-08-03 * "Larkspur Grocer"',
    )

    found = compare(hearths(HEARTH), _parsed(ledger), through=THROUGH, cycles=CYCLES)

    august = found.accounts[0].steps[1]
    assert [(r.day, r.amount) for r in august.only_theirs] == [
        (date(2026, 8, 4), Decimal("-230.00"))
    ]
    assert august.apart == ()


def test_a_ledger_that_never_says_what_a_card_held_is_compared_on_what_moved_alone() -> None:
    """A ledger starting part way through a card's life: what it adds up to is no balance."""
    ledger = _without(LEDGER, '2026-08-02 * "Opening balance, from the statement"')

    found = compare(hearths(HEARTH), _parsed(ledger), through=THROUGH, cycles=CYCLES)

    (account,) = found.accounts
    assert account.unbalanced == "the ledger holds no balance for it"
    assert all(step.theirs is None for step in account.steps)
    assert [(s.day, s.verdict) for s in account.compared] == [
        (date(2026, 8, 31), Verdict.AGREES),
        (date(2026, 9, 30), Verdict.AGREES),
    ]
    assert account.compared[0].moved_theirs == Decimal("-52.45")
    lines = list(describe(found, "ledger.beancount"))
    assert f"{CARD_ACCOUNT}  (the ledger holds no balance for it: only what moved)" in lines
    assert "  2026-08-03..2026-08-31  agrees  moved -52.45" in lines


def test_an_account_whose_balance_hearth_does_not_know_is_compared_on_what_moved_alone() -> None:
    hearth = _without(HEARTH, '2026-08-02 * "Opening balance"')

    found = compare(hearths(hearth), _parsed(LEDGER), through=THROUGH, cycles=CYCLES)

    (account,) = found.accounts
    assert account.unbalanced == "hearth does not know its balance"
    assert all(step.theirs is None for step in account.steps)


def test_an_account_whose_balance_hearth_does_not_know_is_compared_from_its_first_row() -> None:
    """With no opening balance, hearth's books still hold the card from the day before its
    first row: a row of that first stretch the ledger holds and hearth does not is named."""
    hearth = _without(
        _without(HEARTH, '2026-08-02 * "Opening balance"'), '2026-08-31 ! "Tinderbox Coffee"'
    )

    found = compare(hearths(hearth), _parsed(LEDGER), through=THROUGH, cycles=CYCLES)

    (account,) = found.accounts
    assert account.skipped == ()
    august, september = account.compared
    assert (august.since, august.day, august.verdict) == (
        date(2026, 8, 2),
        date(2026, 8, 31),
        Verdict.DIFFERS,
    )
    assert [(r.day, r.amount) for r in august.only_theirs] == [
        (date(2026, 8, 31), Decimal("-12.35"))
    ]
    assert september.verdict is Verdict.AGREES


def _card_row(day: str, narration: str, amount: str, flag: str = "!", meta: str = "") -> str:
    return f"""
{day} {flag} "{narration}"{meta}
  {CARD_ACCOUNT}  {amount} USD
  Expenses:Groceries
"""


def test_two_rows_for_the_same_amount_a_few_days_apart_are_not_one_row_unless_they_agree() -> None:
    """A purchase only hearth holds and another only the ledger holds, for the same money in
    the same week, are two missing rows, never one row dated apart."""
    hearth = HEARTH + _card_row("2026-08-30", "Fernway Books", "-50.00")
    ledger = LEDGER + _card_row("2026-09-03", "Hollow Bakery", "-50.00", flag="*")

    found = compare(hearths(hearth), _parsed(ledger), through=THROUGH, cycles=CYCLES)

    _, august, september = found.accounts[0].steps
    assert august.verdict is Verdict.DIFFERS
    assert [r.narration for r in august.only_ours] == ["Fernway Books"]
    assert september.verdict is Verdict.DIFFERS
    assert [r.narration for r in september.only_theirs] == ["Hollow Bakery"]
    assert (august.apart, september.apart) == ((), ())


def test_a_row_with_two_partners_equally_near_pairs_with_neither() -> None:
    hearth = HEARTH + _card_row("2026-09-05", "Larkspur Grocer", "-9.00")
    ledger = (
        LEDGER
        + _card_row("2026-09-03", "Larkspur Grocer", "-9.00", flag="*")
        + _card_row("2026-09-07", "Larkspur Grocer", "-9.00", flag="*")
    )

    found = compare(hearths(hearth), _parsed(ledger), through=THROUGH, cycles=CYCLES)

    *_, september = found.accounts[0].steps
    assert september.verdict is Verdict.DIFFERS
    assert [r.day for r in september.only_ours] == [date(2026, 9, 5)]
    assert [r.day for r in september.only_theirs] == [date(2026, 9, 3), date(2026, 9, 7)]


def test_a_purchase_and_its_posting_whose_narrations_agree_are_one_row_dated_apart() -> None:
    hearth = HEARTH + _card_row("2026-09-01", "(Sale) GOOGLE *YouTube TV", "-64.99")
    ledger = LEDGER + _card_row("2026-08-30", "YouTube TV", "-64.99", flag="*")

    found = compare(hearths(hearth), _parsed(ledger), through=THROUGH, cycles=CYCLES)

    _, august, september = found.accounts[0].steps
    assert (august.verdict, september.verdict) == (Verdict.DATED_APART, Verdict.DATED_APART)
    ((pair),) = september.apart
    assert (pair.ours.day, pair.theirs.day) == (date(2026, 9, 1), date(2026, 8, 30))


def test_a_ledger_built_on_the_days_rows_were_made_is_read_from_a_week_after_its_first() -> None:
    """The ledger's window opens Aug 10: the grocer's charge made Aug 8 and posted Aug 12 is
    rightly not in it, so the monthly checkpoints start on Aug 17, and the days before it,
    which nobody asked for, are not listed as not compared."""
    hearth = f"""
2026-07-01 open {CARD_ACCOUNT} USD
2026-07-01 open Expenses:Groceries USD
""" + "".join(
        [
            _card_row("2026-08-11", "Hollow Bakery", "-6.00"),
            _card_row("2026-08-12", "Larkspur Grocer", "-40.10"),
            _card_row("2026-09-18", "Quill Stationers", "-0.20"),
        ]
    )
    ledger = f"""
2026-01-01 open {CARD_ACCOUNT} USD
2026-01-01 open Expenses:Groceries USD
""" + "".join(
        [
            _card_row("2026-08-10", "Hollow Bakery", "-6.00", "*", "\n  post-date: 2026-08-11"),
            _card_row("2026-09-18", "Quill Stationers", "-0.20", "*"),
        ]
    )

    found = compare(hearths(hearth), _parsed(ledger), through=THROUGH, monthly=True)

    (account,) = found.accounts
    assert [(s.since, s.day, s.verdict) for s in account.compared] == [
        (date(2026, 8, 17), date(2026, 8, 31), Verdict.AGREES),
        (date(2026, 8, 31), date(2026, 9, 18), Verdict.AGREES),
    ]
    assert account.skipped == ()


def test_a_row_the_ledger_keeps_from_before_its_window_does_not_open_it_earlier() -> None:
    """A reversal booked Jul 10, the day of the charge it reverses, and posted Aug 12 inside a
    window opening Aug 10: the ledger holds nothing else of July, so hearth's July row is no
    row it lacks."""
    hearth = f"""
2026-07-01 open {CARD_ACCOUNT} USD
2026-07-01 open Expenses:Groceries USD
""" + "".join(
        [
            _card_row("2026-07-20", "Marrowbone Transit", "-12.00"),
            _card_row("2026-08-11", "Hollow Bakery", "-6.00"),
            _card_row("2026-08-12", "Larkspur Grocer reversal", "5.00"),
            _card_row("2026-09-18", "Quill Stationers", "-0.20"),
        ]
    )
    ledger = f"""
2026-01-01 open {CARD_ACCOUNT} USD
2026-01-01 open Expenses:Groceries USD
""" + "".join(
        [
            _card_row(
                "2026-07-10", "Larkspur Grocer reversal", "5.00", "*", "\n  post-date: 2026-08-12"
            ),
            _card_row("2026-08-10", "Hollow Bakery", "-6.00", "*", "\n  post-date: 2026-08-11"),
            _card_row("2026-09-18", "Quill Stationers", "-0.20", "*"),
        ]
    )

    found = compare(hearths(hearth), _parsed(ledger), through=THROUGH, monthly=True)

    (account,) = found.accounts
    assert [(s.since, s.day, s.verdict) for s in account.compared] == [
        (date(2026, 8, 17), date(2026, 8, 31), Verdict.AGREES),
        (date(2026, 8, 31), date(2026, 9, 18), Verdict.AGREES),
    ]
    assert account.skipped == ()


def test_a_bank_row_and_a_ledger_row_whose_payee_agrees_are_one_row_dated_apart() -> None:
    """Hearth books the bank's raw words as the narration and the merchant's name as payee."""
    hearth = (
        HEARTH
        + f"""
2026-09-05 ! "Hollow Bakery" "SQ *HOLLOWBKRY 4421"
  {CARD_ACCOUNT}  -7.25 USD
  Expenses:Groceries
"""
    )
    ledger = LEDGER + _card_row("2026-09-03", "Hollow Bakery", "-7.25", flag="*")

    found = compare(hearths(hearth), _parsed(ledger), through=THROUGH, cycles=CYCLES)

    *_, september = found.accounts[0].steps
    assert september.verdict is Verdict.AGREES
    assert (september.only_ours, september.only_theirs) == ((), ())


@pytest.mark.parametrize(
    "name",
    [
        "Liabilities:Typo",
        "Liabilities:Avery:Oldfield:Kestrel",
        "Expenses:Groceries",
    ],
    ids=["neither book", "only the ledger", "not a balance-sheet account"],
)
def test_an_account_hearths_books_do_not_open_on_the_balance_sheet_is_refused_plainly(
    name: str,
) -> None:
    ledger = (
        LEDGER
        + """
2026-01-01 open Liabilities:Avery:Oldfield:Kestrel USD

2026-09-20 * "Larkspur Grocer"
  Liabilities:Avery:Oldfield:Kestrel  -3.00 USD
  Expenses:Groceries
"""
    )
    with pytest.raises(ComparisonError) as refused:
        compare(
            hearths(HEARTH),
            _parsed(ledger),
            through=THROUGH,
            monthly=True,
            accounts=[CARD_ACCOUNT, name],
        )

    assert str(refused.value) == (f"hearth's books open no Assets or Liabilities account {name}.")


def test_a_day_past_what_both_books_reach_is_not_compared_but_said() -> None:
    late = Cycle(CARD_ACCOUNT, date(2026, 10, 1), date(2026, 10, 31))

    found = compare(hearths(HEARTH), _parsed(LEDGER), through=THROUGH, cycles=[*CYCLES, late])

    (account,) = found.accounts
    assert date(2026, 10, 31) not in [s.day for s in account.steps]
    assert [(s.day, s.why) for s in account.skipped] == [
        (date(2026, 10, 31), "the books both reach only to 2026-10-15")
    ]


def test_monthly_checkpoints_run_from_the_first_to_the_last_day_both_books_hold() -> None:
    """The ledger's last row on the card is Sep 18: a row on another account after it says
    nothing of the card."""
    found = compare(hearths(HEARTH), _parsed(LEDGER), through=THROUGH, monthly=True)

    assert [s.day for s in found.accounts[0].steps] == [
        date(2026, 8, 2),
        date(2026, 8, 31),
        date(2026, 9, 18),
    ]


def test_each_account_is_read_only_as_far_as_the_ledger_holds_it() -> None:
    """Checking's statements run to Sep 29 and the card's to Sep 18: the card's row on Sep 25,
    whose statement the ledger has not imported yet, is no row the ledger lacks."""
    hearth = (
        HEARTH
        + f"""
2026-08-01 open {CHECKING_ACCOUNT} USD

2026-08-02 * "Opening balance"
  balance: "current"
  {CHECKING_ACCOUNT}  1000.00 USD
  Equity:Opening-Balances

2026-09-25 ! "Fernway Books"
  {CARD_ACCOUNT}  -15.00 USD
  Expenses:Groceries

2026-09-29 ! "Hollow Bakery"
  {CHECKING_ACCOUNT}  -8.00 USD
  Expenses:Groceries
"""
    )
    ledger = (
        LEDGER
        + f"""
2026-01-01 open {CHECKING_ACCOUNT} USD

2026-08-02 * "Opening balance, from the statement"
  {CHECKING_ACCOUNT}  1000.00 USD
  Equity:Opening-Balances

2026-09-29 * "Hollow Bakery"
  {CHECKING_ACCOUNT}  -8.00 USD
  Expenses:Groceries
"""
    )

    found = compare(hearths(hearth), _parsed(ledger), through=THROUGH, monthly=True)

    checking, card = found.accounts
    assert [(s.day, s.verdict) for s in checking.steps] == [
        (date(2026, 8, 2), Verdict.AGREES),
        (date(2026, 8, 31), Verdict.AGREES),
        (date(2026, 9, 29), Verdict.AGREES),
    ]
    assert [(s.day, s.verdict) for s in card.steps] == [
        (date(2026, 8, 2), Verdict.AGREES),
        (date(2026, 8, 31), Verdict.AGREES),
        (date(2026, 9, 18), Verdict.AGREES),
    ]


def test_an_account_is_read_over_every_statement_cycle_on_it_the_import_reconciled() -> None:
    found = compare(hearths(HEARTH), _parsed(LEDGER), through=THROUGH, cycles=CYCLES, monthly=True)

    assert [s.day for s in found.accounts[0].steps] == [
        date(2026, 8, 2),
        date(2026, 8, 31),
        date(2026, 9, 30),
    ]


def test_an_account_the_ledger_does_not_hold_is_named_and_not_compared() -> None:
    hearth = HEARTH + "\n2026-08-01 open Assets:Avery:Brackenfield:Checking USD\n"

    found = compare(hearths(hearth), _parsed(LEDGER), through=THROUGH, monthly=True)

    assert [a.name for a in found.accounts] == [CARD_ACCOUNT]
    assert found.only_ours == ("Assets:Avery:Brackenfield:Checking",)


def test_only_the_cycles_a_statement_import_reconciled_are_taken(tmp_path: Path) -> None:
    run = tmp_path / "run.json"
    run.write_text(
        json.dumps(
            {
                "checks": {
                    "statement_cycles": [
                        {
                            "account": CARD_ACCOUNT,
                            "cycle": ["2026-08-03", "2026-08-31"],
                            "ok": True,
                        },
                        {
                            "account": CARD_ACCOUNT,
                            "cycle": ["2026-09-01", "2026-09-30"],
                            "ok": False,
                        },
                    ]
                }
            }
        )
    )

    assert statement_cycles(run) == [Cycle(CARD_ACCOUNT, date(2026, 8, 3), date(2026, 8, 31))]


def test_a_file_with_no_statement_cycles_is_refused_plainly(tmp_path: Path) -> None:
    run = tmp_path / "run.json"
    run.write_text('{"checks": {}}')

    with pytest.raises(ComparisonError, match="holds no statement cycles"):
        statement_cycles(run)


# The command, over a household's own data directory.

KESTREL = (
    FakeTransaction(
        "Larkspur Grocer", Decimal("40.10"), date(2026, 8, 3), "FOOD_AND_DRINK_GROCERIES"
    ),
    FakeTransaction(
        "Tinderbox Coffee", Decimal("12.35"), date(2026, 8, 31), "FOOD_AND_DRINK_COFFEE"
    ),
    FakeTransaction(
        "Quill Stationers",
        Decimal("0.20"),
        date(2026, 9, 18),
        "GENERAL_MERCHANDISE_OFFICE_SUPPLIES",
    ),
)
"""A card that owed $230.00 before Aug 3, and $282.65 now, as its bank reports."""

BRACKENFIELD = FakeInstitution(
    "ins_brackenfield",
    "Brackenfield Bank",
    (
        FakeAccount(
            "c1", CARD, "1801", "credit", "credit card", Decimal("282.65"), transactions=KESTREL
        ),
        FakeAccount(
            "chk",
            "EVERYDAY CHECKING",
            "1802",
            "depository",
            "checking",
            Decimal("900.00"),
            transactions=(
                FakeTransaction(
                    "Payroll Wren & Co", Decimal("-900.00"), date(2026, 9, 1), "INCOME_WAGES"
                ),
            ),
        ),
    ),
)

PATHS = {"1801": CARD_ACCOUNT, "1802": CHECKING_ACCOUNT}


@pytest.fixture
def household(tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> FirstDay:
    fresh = first_day(tmp_path, clock, institutions=(BRACKENFIELD,))
    fresh.link("avery", BRACKENFIELD.id)
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_booked(fresh, CHECKING_ACCOUNT)
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(fresh.settings.data_dir))
    return fresh


def _cycles(tmp_path: Path) -> Path:
    run = tmp_path / "run.json"
    run.write_text(
        json.dumps(
            {
                "checks": {
                    "statement_cycles": [
                        {"account": c.account, "cycle": [str(c.start), str(c.end)], "ok": True}
                        for c in CYCLES
                    ]
                }
            }
        )
    )
    return run


def _contents(directory: Path) -> dict[str, tuple[bytes, int]]:
    """Every file in `directory`, as it reads and when it was last written."""
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(directory.iterdir())
        # SQLite's own journal beside a database, which even a read-only reader may create.
        if not path.name.endswith(("-wal", "-shm"))
    }


def test_the_command_says_where_a_households_books_agree_with_its_ledger(
    household: FirstDay, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.beancount"
    ledger.write_text(LEDGER)

    main(["compare", str(ledger), "--cycles", str(_cycles(tmp_path))])

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"Hearth's own books against {ledger}"
    assert f"{CARD_ACCOUNT}" in lines
    assert "  2026-08-02  agrees  balance -230.00" in lines
    assert "  2026-08-03..2026-08-31  agrees  balance -282.45; moved -52.45" in lines
    assert "  2026-09-01..2026-09-30  agrees  balance -282.65; moved -0.20" in lines
    assert f"{CHECKING_ACCOUNT}  not in the ledger: nothing to compare" in lines
    assert lines[-1] == "Checkpoints: 3 agree, 0 agree but for the day rows are dated, 0 differ."


def test_the_command_names_the_row_a_ledger_does_not_hold(
    household: FirstDay, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.beancount"
    ledger.write_text(_without(LEDGER, '2026-09-18 * "Quill Stationers"'))

    main(["compare", str(ledger), "--cycles", str(_cycles(tmp_path))])

    lines = capsys.readouterr().out.splitlines()
    assert (
        "  2026-09-01..2026-09-30  DIFFERS  balance differs by -0.20: hearth -282.65, the ledger"
        " -282.45; moved differs by -0.20: hearth -0.20, the ledger 0.00"
    ) in lines
    assert "      only in hearth      2026-09-18  -0.20  Quill Stationers" in lines


def test_the_command_compares_hearths_own_books_not_the_ledger_a_household_inherits(
    household: FirstDay,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Were the ledger inherited, hearth would leave its accounts to it, and the comparison
    would hold the ledger against itself."""
    ledger = tmp_path / "ledger.beancount"
    ledger.write_text(_without(LEDGER, '2026-09-18 * "Quill Stationers"'))
    monkeypatch.setenv(LEDGER_VARIABLE, str(ledger))

    main(["compare", str(ledger), "--cycles", str(_cycles(tmp_path))])

    assert "      only in hearth      2026-09-18  -0.20  Quill Stationers" in (
        capsys.readouterr().out.splitlines()
    )


def test_the_command_changes_nothing_of_the_households(
    household: FirstDay, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not even a database an older hearth left, which serving would bring up to date: the
    one before rulings recorded what each row said, or members named accounts. Nor the books
    file: hearth leaves one it would write unchanged untouched, so it is taken away first, and
    must stay away."""
    conn = sqlite3.connect(household.settings.database)
    try:
        conn.execute("DROP TABLE named_accounts")
        conn.execute("ALTER TABLE review_ruling_rows DROP COLUMN says")
        conn.execute(f"PRAGMA user_version = {len(db.MIGRATIONS) - 2}")
    finally:
        conn.close()
    household.settings.books_file.unlink()
    ledger = tmp_path / "ledger" / "ledger.beancount"
    ledger.parent.mkdir()
    ledger.write_text(LEDGER)
    data_dir = household.settings.data_dir
    before, ledger_before = _contents(data_dir), _contents(ledger.parent)

    main(["compare", str(ledger), "--monthly"])

    assert "agrees" in capsys.readouterr().out
    assert _contents(data_dir) == before
    assert _contents(ledger.parent) == ledger_before


def test_the_command_refuses_a_data_directory_with_no_household(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIR_VARIABLE, str(tmp_path / "empty"))
    ledger = tmp_path / "ledger.beancount"
    ledger.write_text(LEDGER)

    with pytest.raises(SystemExit, match="there is no household in"):
        main(["compare", str(ledger)])

    assert not (tmp_path / "empty").exists()


def test_the_command_says_plainly_a_ledger_that_is_not_there_cannot_be_read(
    household: FirstDay, tmp_path: Path
) -> None:
    ledger = tmp_path / "missing.beancount"

    with pytest.raises(SystemExit) as refused:
        main(["compare", str(ledger)])

    assert refused.value.code == f"hearth: {ledger} cannot be read: No such file or directory."


def test_the_command_refuses_an_account_hearths_books_do_not_open(
    household: FirstDay, tmp_path: Path
) -> None:
    ledger = tmp_path / "ledger.beancount"
    ledger.write_text(LEDGER)

    with pytest.raises(SystemExit) as refused:
        main(["compare", str(ledger), "--account", "Liabilities:Typo"])

    assert refused.value.code == (
        "hearth: hearth's books open no Assets or Liabilities account Liabilities:Typo."
    )
