"""What "Where <month> went" on the Household page says when there is nothing on it to show.

Every value is invented.
"""

import re
from decimal import Decimal
from html import unescape

from hearth.plaid.fake import FakeAccount, FakeHolding, FakeInstitution
from hearth.sharing import Facet
from tests.firstday import PATHS, FirstDay, until_booked, until_transacted

SHALE = FakeInstitution(
    "ins_shale",
    "Shale Brokerage",
    (
        FakeAccount(
            "ira",
            "Individual account",
            "4431",
            "investment",
            "brokerage",
            Decimal("1420.60"),
            holdings=(FakeHolding("ZSHL", "Shale Total Market", Decimal(10), Decimal("142.06")),),
        ),
    ),
)
"""An invented brokerage, whose account cannot show what anyone spent."""


def _text(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


def _went(page: str) -> str:
    return _text(page[page.index('<h2 id="cat-h">') :].split("</section>")[0])


def _arrived(fresh: FirstDay) -> None:
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), PATHS)
    until_transacted(fresh, PATHS["6616"])


def _shares_nothing(fresh: FirstDay, username: str) -> None:
    conn = fresh.connect()
    try:
        for facet in Facet:
            conn.execute(
                "INSERT INTO sharing_preferences (member_id, facet, shared, updated_at)"
                " VALUES (?, ?, 0, ?) ON CONFLICT (member_id, facet)"
                " DO UPDATE SET shared = excluded.shared",
                (fresh.member_ids[username], facet.value, "2026-01-01"),
            )
    finally:
        conn.close()


def test_a_member_nobody_shares_spending_with_is_told_so(fresh: FirstDay) -> None:
    """Blake has connected nothing, and Avery, who has, shares nothing: nothing is coming,
    so the panel says nobody shares spending, not that a month is still on its way."""
    _arrived(fresh)
    _shares_nothing(fresh, "avery")

    went = _went(fresh.signed_in("blake").get("/").text)

    assert "Nobody shares spending with you." in went, went
    assert "Not known yet" not in went


def test_a_whole_month_with_no_spending_is_a_month_with_none(fresh: FirstDay) -> None:
    """Avery has connected only a savings account, which only takes money in, and all of
    September is in: the panel says nothing was spent, not that nobody shares spending."""
    fresh.link("avery", "ins_willowmere")
    fresh.map(fresh.signed_in("avery"), {"7717": PATHS["7717"]})
    until_transacted(fresh, PATHS["7717"])

    went = _went(fresh.signed_in("avery").get("/").text)

    assert went == "Where September went Nothing was spent in September.", went
    assert "Nobody shares spending" not in went
    assert "Not known yet" not in went


def test_accounts_that_cannot_show_spending_never_read_as_figures_on_their_way(
    fresh: FirstDay,
) -> None:
    """Avery has connected only a brokerage, which never sends what was spent or earned: the
    panels say nothing behind them shows it, never that the figures are on their way."""
    fresh.plaid.institutions[SHALE.id] = SHALE
    fresh.link("avery", SHALE.id)
    fresh.map(fresh.signed_in("avery"), {"4431": "Assets:Avery:Shale:Brokerage"})
    until_booked(fresh, "Assets:Avery:Shale:Brokerage")

    page = fresh.signed_in("avery").get("/").text
    went = _went(page)

    assert "Not known yet" not in _text(page), _text(page)
    assert went == (
        "Where September went Not known: none of the accounts behind this that you can see"
        " shows hearth what was spent."
    ), went
