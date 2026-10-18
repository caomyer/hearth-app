from datetime import date

from beancount.core.data import Directive, Transaction

from hearth.reports.coverage import (
    UNLABELLED,
    AccountCoverage,
    Gap,
    Span,
    account_coverage,
    origin_from_source_meta,
)


def by_account(
    entries: list[Directive], as_of: date = date(2026, 12, 31)
) -> dict[str, AccountCoverage]:
    return {c.account: c for c in account_coverage(entries, as_of=as_of)}


def test_coverage_shows_each_origin_and_the_gap_between_them(entries: list[Directive]) -> None:
    alex = by_account(entries)["Assets:Alex:Checking"]

    assert (alex.opened, alex.earliest, alex.latest) == (
        date(2026, 1, 1),
        date(2026, 1, 1),
        date(2026, 4, 20),
    )
    assert alex.spans == (
        Span("statement-a", date(2026, 1, 1), date(2026, 3, 5), 4),
        Span("feed-a", date(2026, 4, 20), date(2026, 4, 20), 1),
    )
    assert alex.gaps == (
        Gap(date(2026, 1, 25), date(2026, 3, 5)),
        Gap(date(2026, 3, 5), date(2026, 4, 20)),
    )
    assert [gap.days for gap in alex.gaps] == [39, 46]


def test_a_quiet_month_is_not_a_gap(entries: list[Directive]) -> None:
    joint = by_account(entries)["Assets:Joint:Checking"]

    assert joint.gaps == ()


def test_an_account_with_no_transactions_has_no_dates(entries: list[Directive]) -> None:
    brokerage = by_account(entries)["Assets:Blair:Brokerage"]

    assert (brokerage.earliest, brokerage.latest, brokerage.spans) == (None, None, ())


def test_nothing_after_the_as_of_day_counts(entries: list[Directive]) -> None:
    coverage = by_account(entries, as_of=date(2026, 3, 31))

    assert coverage["Assets:Alex:Checking"].latest == date(2026, 3, 5)
    assert "Assets:Blair:Brokerage" not in coverage


def test_only_balance_sheet_accounts_are_covered(entries: list[Directive]) -> None:
    assert {a.split(":")[0] for a in by_account(entries)} == {"Assets", "Liabilities"}


def test_the_gap_threshold_is_a_parameter(entries: list[Directive]) -> None:
    coverage = account_coverage(entries, as_of=date(2026, 12, 31), gap_days=10)
    joint = next(c for c in coverage if c.account == "Assets:Joint:Checking")

    assert [g.days for g in joint.gaps] == [19, 14, 21]


def test_origin_is_the_source_metadata_before_its_first_colon(entries: list[Directive]) -> None:
    first = next(e for e in entries if isinstance(e, Transaction))

    assert origin_from_source_meta(first) == "statement-a"
    assert origin_from_source_meta(first._replace(meta={})) == UNLABELLED
    assert origin_from_source_meta(first._replace(meta={"source": ""})) == UNLABELLED
