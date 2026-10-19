"""The `source` an entry booked from a connection carries, which coverage parses back.

The import writes it and `item_of` reads it, so the two must agree for every id Plaid can
send, and nothing else may parse as a connection's entry.
"""

import pytest
from beancount import loader
from beancount.core.data import Transaction

from hearth.connections import item_of, plaid_source

ITEM = "Lq8vRk3Wd9TzPe2yNs7XbA"
"""Shaped like a Plaid Item id: letters and digits, no separator. Invented."""


@pytest.mark.parametrize(
    "transaction_id",
    ["Bx7Pq2Lk9Ms4Nd8Rf3Ty6Hv", "with:colons:inside", "a"],
)
def test_every_booked_entry_names_its_connection(transaction_id: str) -> None:
    source = plaid_source(ITEM, transaction_id)

    assert source == f"plaid:{ITEM}:{transaction_id}"
    assert item_of(source) == ITEM


def test_the_source_survives_the_books_file() -> None:
    text = f"""
2026-01-01 open Assets:Avery:Willowmere:Checking
2026-01-01 open Expenses:Food
2026-01-05 ! "Sorrel Bakery" "Bread"
  source: "{plaid_source(ITEM, "Bx7Pq2Lk9Ms4Nd8Rf3Ty6Hv")}"
  Expenses:Food  12.50 USD
  Assets:Avery:Willowmere:Checking
"""
    entries, errors, _ = loader.load_string(text)

    assert errors == []
    (booked,) = [e for e in entries if isinstance(e, Transaction)]
    assert item_of(booked.meta["source"]) == ITEM


@pytest.mark.parametrize(
    "source",
    [
        None,
        42,
        "",
        "statements/2026-01.csv:14",
        f"plaid:{ITEM}",
        f"plaid:{ITEM}:",
        "plaid:::",
        "plaid::Bx7Pq2Lk9Ms4Nd8Rf3Ty6Hv",
        f"PLAID:{ITEM}:Bx7Pq2Lk9Ms4Nd8Rf3Ty6Hv",
        f"bcng:{ITEM}:Bx7Pq2Lk9Ms4Nd8Rf3Ty6Hv",
    ],
)
def test_nothing_else_passes_for_a_connections_entry(source: object) -> None:
    assert item_of(source) is None


@pytest.mark.parametrize(
    ("item_id", "transaction_id"),
    [("", "Bx7Pq2Lk9Ms4Nd8Rf3Ty6Hv"), (ITEM, ""), ("Lq8v:Rk3W", "Bx7Pq2Lk9Ms4Nd8Rf3Ty6Hv")],
    ids=["no item", "no transaction", "a colon in the item"],
)
def test_a_source_coverage_would_misread_is_never_written(
    item_id: str, transaction_id: str
) -> None:
    with pytest.raises(ValueError, match="a Plaid source needs"):
        plaid_source(item_id, transaction_id)
