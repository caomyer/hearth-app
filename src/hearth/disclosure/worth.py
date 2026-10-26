"""Net worth and positions: what the household owns and owes, and where each figure comes from.

Every position carries its price, the day of that price and its source, so a wrong or old
quote is visible rather than baked in (decision 6). A position with no price is never valued
at zero: totals that leave it out are floors, and say what they leave out.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from beancount.core.data import Commodity, Transaction

from hearth import figures
from hearth.disclosure.common import Household, MemberRef, PartRef, Worth
from hearth.disclosure.gate import Disclosed
from hearth.disclosure.household import Headline, headline
from hearth.figures import Part
from hearth.months import Month
from hearth.plaid.opening import anchors
from hearth.reports.common import units
from hearth.sharing import Facet
from hearth.valuation.networth import Freshness, NetWorth, Valued


@dataclass(frozen=True, slots=True)
class Contribution:
    """One part's line in "how it adds up"."""

    part: PartRef
    worth: Disclosed[Worth]
    owned: Decimal | None
    owed: Decimal | None
    accounts: int | None


@dataclass(frozen=True, slots=True)
class Move:
    """One month's change in net worth, split into money put in and the market moving.

    Both are None when either end of the month is a floor, since the split would be guesswork.
    """

    month_end: date
    saved: Decimal | None
    market: Decimal | None


@dataclass(frozen=True, slots=True)
class Position:
    commodity: str
    units: Decimal
    value: Decimal | None
    price: Decimal | None
    price_day: date | None
    source: str | None
    freshness: Freshness
    is_cash: bool


@dataclass(frozen=True, slots=True)
class Holding:
    """One account's positions."""

    account_id: str
    name: str
    owners: tuple[MemberRef, ...]
    part: PartRef
    held: tuple[Position, ...]


@dataclass(frozen=True, slots=True)
class PrivateHoldings:
    part: PartRef
    total_shared: bool


@dataclass(frozen=True, slots=True)
class Provenance:
    sources: tuple[str, ...]
    fetched_at: datetime | None
    priced_on: date | None


@dataclass(frozen=True, slots=True)
class NetWorthView:
    headline: Headline
    adds_up: tuple[Contribution, ...]
    moves: tuple[Move, ...]
    moves_cover: tuple[PartRef, ...]
    moves_leave_out: tuple[PartRef, ...]
    moves_priced_from: date | None
    """Where `moves` start when earlier month ends had no prices to value them at."""
    holdings: tuple[Holding, ...]
    private: tuple[PrivateHoldings, ...]
    provenance: Provenance
    names: dict[str, str]
    """What each visible security is called, from the ledger's commodity directives."""

    @property
    def connected(self) -> bool:
        """Whether anyone in the household has an account in the books yet."""
        return bool(self.adds_up)

    @property
    def stale(self) -> tuple[tuple[Holding, Position], ...]:
        return tuple(
            (h, p) for h in self.holdings for p in h.held if p.freshness is Freshness.STALE
        )

    @property
    def unpriced(self) -> tuple[tuple[Holding, Position], ...]:
        return tuple(
            (h, p) for h in self.holdings for p in h.held if p.freshness is Freshness.UNPRICED
        )

    @property
    def visible_total(self) -> Decimal:
        return sum(
            (p.value for h in self.holdings for p in h.held if p.value is not None),
            Decimal(0),
        )


def net_worth(h: Household) -> NetWorthView:
    parts = [part for part in h.parts if part.accounts]
    detailed = [part for part in parts if h.gate.may(Facet.ACCOUNT_BALANCES, part.owners)]
    moving = [part for part in detailed if h.gate.may(Facet.TRANSACTIONS, part.owners)]
    return NetWorthView(
        headline=headline(h, parts),
        adds_up=tuple(_contribution(h, part) for part in _viewer_first(h, parts)),
        moves=_moves(h, moving),
        moves_cover=tuple(h.part_ref(p) for p in moving),
        moves_leave_out=tuple(h.part_ref(p) for p in parts if p not in moving),
        moves_priced_from=h.priced_from(moving),
        holdings=tuple(
            holding
            for part in _viewer_first(h, detailed)
            for holding in _holdings(h, part)
            if holding.held
        ),
        private=tuple(
            PrivateHoldings(h.part_ref(p), h.gate.may(Facet.NET_WORTH_TOTAL, p.owners))
            for p in parts
            if p not in detailed
        ),
        provenance=_provenance(h, detailed),
        names=_names(h, detailed),
    )


def _names(h: Household, parts: Sequence[Part]) -> dict[str, str]:
    held = {
        p.holding.commodity for part in parts for p in figures.worth(h.books.today, part).positions
    }
    return {
        entry.currency: str(entry.meta["name"])
        for entry in h.books.entries
        if isinstance(entry, Commodity) and entry.currency in held and "name" in entry.meta
    }


def _viewer_first(h: Household, parts: Sequence[Part]) -> list[Part]:
    """The viewer's own accounts, then joint accounts, then everyone else."""
    return sorted(parts, key=lambda p: (p.owners != {h.viewer.id}, not p.is_joint))


def _contribution(h: Household, part: Part) -> Contribution:
    detailed = h.gate.may(Facet.ACCOUNT_BALANCES, part.owners)
    mine = figures.worth(h.books.today, part)
    values = [p.value for p in mine.positions if p.value is not None]
    return Contribution(
        part=h.part_ref(part),
        worth=h.disclosed_worth(part),
        owned=sum((v for v in values if v > 0), Decimal(0)) if detailed else None,
        owed=sum((v for v in values if v < 0), Decimal(0)) if detailed else None,
        accounts=len(part.accounts) if detailed else None,
    )


def _moves(h: Household, parts: Sequence[Part]) -> tuple[Move, ...]:
    """Each recent month's change in the parts' net worth, as saved plus market.

    Saved is money that crossed into or out of the parts' accounts: pay in, spending out,
    transfers to and from anyone else's accounts, and to and from accounts hearth does not see,
    which no valuation holds. Market is the rest of the change, which is what prices did to
    what was held.
    """
    if not parts:
        return ()
    accounts = frozenset(
        name for part in parts for name in part.ledger_accounts if name not in h.books.unseen
    )
    moves = []
    for before, after in pairwise(h.history(parts)):
        if Month.of(after.as_of) != Month.of(before.as_of).following():
            moves.append(Move(after.as_of, None, None))
            continue
        start_worth = [figures.worth(before, p) for p in parts]
        end_worth = [figures.worth(after, p) for p in parts]
        if not all(w.complete for w in [*start_worth, *end_worth]):
            moves.append(Move(after.as_of, None, None))
            continue
        change = _total(end_worth) - _total(start_worth)
        saved = _crossing(h, accounts, before.as_of + timedelta(days=1), after.as_of)
        moves.append(Move(after.as_of, saved, change - saved))
    return tuple(moves)


def _total(worths: Sequence[NetWorth]) -> Decimal:
    return sum((w.known_total for w in worths), Decimal(0))


def _crossing(h: Household, accounts: frozenset[str], start: date, end: date) -> Decimal:
    """Money in the household currency that moved into `accounts` from outside them, net."""
    total = Decimal(0)
    for entry in h.books.entries:
        # An opening balance is what an account already held, not money put in.
        if not isinstance(entry, Transaction) or not start <= entry.date <= end or anchors(entry):
            continue
        inside = [p for p in entry.postings if p.account in accounts]
        if not inside or len(inside) == len(entry.postings):
            continue
        for posting in inside:
            amount = units(posting, entry)
            if amount.currency == h.books.currency:
                total += amount.number
    return total


def _holdings(h: Household, part: Part) -> list[Holding]:
    ref = h.part_ref(part)
    return [
        Holding(
            account_id=account.id,
            name=account.name,
            owners=tuple(h.ref(m) for m in h.members if m.id in account.owners),
            part=ref,
            held=tuple(
                _position(p, h.books.currency)
                for p in h.books.today.positions
                if p.holding.account == account.ledger_account
            ),
        )
        for account in part.accounts
    ]


def _position(valued: Valued, currency: str) -> Position:
    quote = valued.quote
    return Position(
        commodity=valued.holding.commodity,
        units=valued.holding.units,
        value=valued.value,
        price=quote.price if quote else None,
        price_day=quote.as_of if quote else None,
        source=quote.source if quote else None,
        freshness=valued.freshness,
        is_cash=valued.holding.commodity == currency,
    )


def _provenance(h: Household, parts: Sequence[Part]) -> Provenance:
    """Where the prices behind the visible positions came from, and when."""
    quotes = [
        p.quote
        for part in parts
        for p in figures.worth(h.books.today, part).positions
        if p.quote is not None
    ]
    return Provenance(
        sources=tuple(sorted({q.source for q in quotes})),
        fetched_at=max((q.fetched_at for q in quotes), default=None),
        priced_on=max((q.as_of for q in quotes), default=None),
    )
