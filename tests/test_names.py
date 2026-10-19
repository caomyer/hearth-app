"""A ledger account's name, read by someone who has never seen a ledger."""

import pytest

from hearth.names import readable


@pytest.mark.parametrize(
    ("component", "reads"),
    [
        ("GasAndElectricity", "Gas and electricity"),
        ("PharmaciesAndSupplements", "Pharmacies and supplements"),
        ("Groceries", "Groceries"),
        ("ATM", "ATM"),
        ("ATMFees", "ATM fees"),
        ("Pub-Crawl", "Pub crawl"),
    ],
)
def test_a_joined_up_name_reads_as_words(component: str, reads: str) -> None:
    assert readable(component) == reads
