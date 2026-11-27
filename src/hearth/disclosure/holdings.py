"""Holdings: what the investment accounts the viewer may see hold, what each holding cost and
is worth, and where each price came from.

The positions are the net worth page's (``worth``): an account's goes with its balances, so a
part whose balances the viewer may not see is named as withheld, never listed, and its value
is said to be inside the household's total only where that part shares its total.

Every absence is its own. A holding with no price is not worth zero: it is named, and every
total that leaves it out is a floor. A stale price carries its day and age. A holding whose
cost the books do not hold has no gain, never a gain against zero. An account whose holdings
are not known yet holds nothing hearth can show, and only its owners are told why.

What a holding cost is what the books hold: a ledger's lots at cost (``valuation.holdings``).
What a brokerage reported in hearth's own books says nothing of cost, so it has none here.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from hearth import figures
from hearth.accounts import Account
from hearth.disclosure.common import Household, MemberRef, PartRef
from hearth.disclosure.gate import Concealed
from hearth.disclosure.worth import PrivateHoldings, security_names, viewer_first
from hearth.figures import Part
from hearth.sharing import Facet
from hearth.valuation import institution
from hearth.valuation.networth import Freshness, Valued
from hearth.valuation.pricestore import PriceStore


@dataclass(frozen=True, slots=True)
class Price:
    """The stored price a holding is valued at, and where it came from."""

    value: Decimal
    day: date
    """The day it is the close of."""
    source: str
    fetched_at: datetime
    age: int
    """Calendar days from its day to the books' own."""


@dataclass(frozen=True, slots=True)
class Held:
    """One holding of one account."""

    commodity: str
    ticker: str | None
    name: str | None
    """What the books call it, or None when they give it no name."""
    is_cash: bool
    units: Decimal
    freshness: Freshness
    price: Price | None
    """None for cash, and for a holding with no price it can be valued at."""
    value: Decimal | None
    """What it is worth at `price`; None when it has none, never zero."""
    cost: Decimal | None
    """What it cost, from the books' lots; cash costs itself. None when the books do not hold
    its cost."""

    @property
    def gain(self) -> Decimal | None:
        """What it has gained, or lost, and not realised yet: its value less its cost, when
        both are known. Cash gains nothing, so has none."""
        if self.is_cash or self.value is None or self.cost is None:
            return None
        return self.value - self.cost


@dataclass(frozen=True, slots=True)
class Totals:
    """A sum of holdings at market value and at cost, and what each sum leaves out."""

    value: Decimal
    unpriced: int
    """Holdings with no price, left out of `value`, which is then a floor."""
    cost: Decimal
    uncosted: int
    """Holdings whose cost the books do not hold, left out of `cost`, which is then a floor."""
    gain: Decimal
    ungained: int
    """Holdings, not cash, whose gain is not known, left out of `gain`."""
    waiting: int = 0
    """Accounts whose holdings are not known yet, so are in none of the sums."""


@dataclass(frozen=True, slots=True)
class HeldAccount:
    """One investment account the viewer may see the balance of, and what it holds."""

    id: str
    name: str
    owners: tuple[MemberRef, ...]
    part: PartRef
    known: bool
    """Whether the books know what it holds. One whose brokerage has not said yet holds
    nothing hearth can show, which is never holding nothing."""
    why: str | None
    """Why the books do not know it yet (``plaid.opening.Unknown``), told to its owners alone."""
    held: tuple[Held, ...]
    totals: Totals


@dataclass(frozen=True, slots=True)
class HoldingsView:
    accounts: tuple[HeldAccount, ...]
    totals: Totals
    withheld: tuple[PrivateHoldings, ...]
    """Parts whose positions the viewer may not see, and whether their total is shared."""
    priced_on: date | None
    """The day of the newest price behind what is shown."""


@dataclass(frozen=True, slots=True)
class Close:
    """One day's price of a security, as hearth holds it."""

    day: date
    price: Decimal
    source: str


@dataclass(frozen=True, slots=True)
class PriceHistory:
    """Where a security's price comes from, and every price hearth holds of it."""

    commodity: str
    ticker: str | None
    name: str | None
    freshness: Freshness
    price: Price | None
    """The price it is valued at today, or None when it has none."""
    closes: tuple[Close, ...]
    """Oldest first, one a day: the price a valuation of that day would take."""


def holdings(h: Household) -> HoldingsView:
    """What the viewer may see of the household's investment positions."""
    parts = [part for part in h.parts if part.accounts]
    detailed = [part for part in parts if h.gate.may(Facet.ACCOUNT_BALANCES, part.owners)]
    names = security_names(h, detailed)
    listed = tuple(
        found
        for part in viewer_first(h, detailed)
        for account in part.accounts
        if (found := _account(h, part, account, names)) is not None
    )
    prices = [p.price for a in listed for p in a.held if p.price is not None]
    return HoldingsView(
        accounts=listed,
        totals=_totals(
            (p for a in listed for p in a.held), waiting=sum(1 for a in listed if not a.known)
        ),
        withheld=tuple(
            PrivateHoldings(h.part_ref(p), h.gate.may(Facet.NET_WORTH_TOTAL, p.owners))
            for p in parts
            if p not in detailed
        ),
        priced_on=max((p.day for p in prices), default=None),
    )


def price_history(h: Household, commodity: str, store: PriceStore) -> PriceHistory:
    """Where the price of a security the viewer may see a holding of comes from, and every
    price hearth holds of it. One the viewer holds nothing of that they may see is concealed,
    whoever else holds it: that it is held at all is theirs."""
    found = next(
        (
            p
            for a in holdings(h).accounts
            for p in a.held
            if p.commodity == commodity and not p.is_cash
        ),
        None,
    )
    if found is None:
        raise Concealed
    books = h.books
    closes: dict[date, tuple[datetime, Close]] = {}
    for source in books.price_sources:
        for quote in store.history(commodity, source):
            if quote.as_of > books.as_of or quote.currency != books.currency:
                continue
            kept = closes.get(quote.as_of)
            # As a valuation takes it: the newest day, then the one fetched last.
            if kept is None or quote.fetched_at > kept[0]:
                closes[quote.as_of] = (
                    quote.fetched_at,
                    Close(quote.as_of, quote.price, quote.source),
                )
    return PriceHistory(
        commodity=commodity,
        ticker=found.ticker,
        name=found.name,
        freshness=found.freshness,
        price=found.price,
        closes=tuple(closes[day][1] for day in sorted(closes)),
    )


def _account(
    h: Household, part: Part, account: Account, names: dict[str, str]
) -> HeldAccount | None:
    """One of the part's accounts, when it is an investment account: a brokerage's, or one
    that holds anything but cash. Hearth does not see one it holds by name alone."""
    ledger = account.ledger_account
    if ledger in h.books.unseen:
        return None
    held = tuple(
        sorted(
            (
                _held(p, h.books.currency, names)
                for p in figures.worth(h.books.today, part).positions
                if p.holding.account == ledger
            ),
            key=lambda p: (p.is_cash, (p.name or p.commodity).casefold()),
        )
    )
    if ledger not in h.books.invested and all(p.is_cash for p in held):
        return None
    known = ledger not in h.balance_unknown
    why = h.books.unknown.get(ledger)
    return HeldAccount(
        id=account.id,
        name=account.name,
        owners=tuple(h.ref(m) for m in h.members if m.id in account.owners),
        part=h.part_ref(part),
        known=known,
        why=(
            why.value
            if why is not None and not known and h.gate.may_see_why_unknown(account.owners)
            else None
        ),
        held=held if known else (),
        totals=_totals(held if known else (), waiting=0 if known else 1),
    )


def _held(valued: Valued, currency: str, names: dict[str, str]) -> Held:
    holding, quote = valued.holding, valued.quote
    is_cash = holding.commodity == currency
    cost = holding.cost
    return Held(
        commodity=holding.commodity,
        ticker=None if is_cash else institution.ticker(holding.commodity),
        name=None if is_cash else names.get(holding.commodity),
        is_cash=is_cash,
        units=holding.units,
        freshness=valued.freshness,
        price=(
            Price(quote.price, quote.as_of, quote.source, quote.fetched_at, valued.price_age_days)
            if quote is not None
            and valued.value is not None
            and valued.price_age_days is not None
            and not is_cash
            else None
        ),
        value=valued.value,
        cost=(
            holding.units
            if is_cash
            else cost.number
            if cost is not None and cost.currency == currency
            else None
        ),
    )


def _totals(held: Iterable[Held], *, waiting: int) -> Totals:
    found: Sequence[Held] = tuple(held)
    return Totals(
        value=sum((p.value for p in found if p.value is not None), Decimal(0)),
        unpriced=sum(1 for p in found if p.value is None),
        cost=sum((p.cost for p in found if p.cost is not None), Decimal(0)),
        uncosted=sum(1 for p in found if p.cost is None),
        gain=sum((g for p in found if (g := p.gain) is not None), Decimal(0)),
        ungained=sum(1 for p in found if not p.is_cash and p.gain is None),
        waiting=waiting,
    )
