"""The price an institution reports for its own holdings, through Plaid: today's, and no other.

Plaid's `/investments/holdings/get` gives each holding an `institution_price` and the day it
was current, `institution_price_as_of`. It is the brokerage's own valuation, which covers a
fund with no public ticker as well as one with, and it is the household's chosen valuation
(`plaid-only`: no market data feed).

It is today-only. Plaid keeps no history of it, so a day nobody asked is a day with no price,
and no price before the first ask can ever be had. Each one is therefore stored as it arrives,
in the `price` table under source `plaid` with the security's Plaid id and the time it was
fetched, and a net worth history starts at the first month end it can value
(`docs/valuation.md`).

This module only reads a holdings response and stores what it says, and nothing here calls
Plaid: ``hearth.plaid.holdings`` asks each connected brokerage for its holdings, when reading
holdings is approved, and hands every response to `read_many`.
"""

import base64
import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from beancount.core.amount import CURRENCY_RE

from hearth.valuation.pricestore import PriceStore, Recorded
from hearth.valuation.quotes import Quote

SOURCE = "plaid"


class HoldingsMalformedError(ValueError):
    """A holdings response that cannot be read as Plaid documents it."""


@dataclass(frozen=True, slots=True)
class Unpriceable:
    """A holding whose price was not stored, and why: it is listed, never dropped silently."""

    security_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class InstitutionPrices:
    quotes: tuple[Quote, ...]
    unpriceable: tuple[Unpriceable, ...]


NO_PRICE = "no institution price"
NO_DATE = "no date the institution price was current"
NO_CURRENCY = "not priced in an ISO currency"
CONFLICT = "another holding of the same ticker was priced differently that day"


UNTICKERED = "PLAID."
"""How the commodity of a security with no ticker a ledger can name begins (`commodity`)."""


def commodity(security_id: str, ticker: str | None) -> str:
    """The ledger commodity a Plaid security is held and priced as: its ticker, or, for one
    with none a commodity can be named by, such as a trust fund that is not listed, a name
    made from its Plaid `security_id` alone, the same every time it is asked.

    A ticker matches a commodity an inherited ledger already holds; a made name matches the
    position hearth's own books hold of that security (``hearth.plaid.opening``), and nothing
    else, so it is never a guess.
    """
    if ticker and re.fullmatch(CURRENCY_RE, ticker):
        return ticker
    digest = base64.b32encode(hashlib.sha256(security_id.encode()).digest()).decode()
    return f"{UNTICKERED}{digest[:10]}"


def ticker(security: str) -> str | None:
    """The ticker a ledger commodity is named by, or None for one `commodity` made up."""
    return None if security.startswith(UNTICKERED) else security


def is_cash(security: Mapping[str, Any]) -> bool:
    """Whether a Plaid security is money, or as good as money: cash and money market funds,
    which are an account's cash, not a priced security."""
    return security.get("type") == "cash" or security.get("is_cash_equivalent") is True


def read_holdings(payload: Mapping[str, Any], *, fetched_at: datetime) -> InstitutionPrices:
    """Each holding's institution price as a quote, from a parsed holdings response."""
    return read_many([payload], fetched_at=fetched_at)


def read_many(payloads: Iterable[Mapping[str, Any]], *, fetched_at: datetime) -> InstitutionPrices:
    """Each holding's institution price as a quote, from every brokerage's holdings response.

    Each payload must have been parsed with `parse_float=Decimal`: a float here has already
    lost the price it held, so it is refused rather than converted.

    A holding is matched to a ledger commodity by its security's ticker, or by its Plaid
    `security_id` when it has no ticker a commodity can be named by (`commodity`); the
    `security_id` is kept beside it, never the CUSIP, which Plaid sends only under a licence.
    A price with no day is not stored: taking it as today's would claim a freshness nobody
    vouched for. Cash holdings are the account's cash, not a priced security, and are skipped.

    Two brokerages can hold one fund, and the store holds one price for it a day, so they are
    read together: where they disagree, neither is chosen.
    """
    found: dict[tuple[str, date], list[Quote]] = {}
    unpriceable: list[Unpriceable] = []
    for payload in payloads:
        securities = {
            _text(security, "security_id"): security for security in _list(payload, "securities")
        }
        for holding in _list(payload, "holdings"):
            security_id = _text(holding, "security_id")
            security = securities.get(security_id)
            if security is None:
                raise HoldingsMalformedError(f"a holding names an unlisted security {security_id}")
            if is_cash(security):
                continue
            ticker = security.get("ticker_symbol")
            symbol = commodity(security_id, ticker if isinstance(ticker, str) else None)
            price = _decimal(holding, "institution_price")
            as_of = _day(holding, "institution_price_as_of") or _moment(
                holding, "institution_price_datetime"
            )
            currency = holding.get("iso_currency_code")
            if price is None or price <= 0:
                unpriceable.append(Unpriceable(security_id, NO_PRICE))
            elif as_of is None:
                unpriceable.append(Unpriceable(security_id, NO_DATE))
            elif not isinstance(currency, str) or not currency:
                unpriceable.append(Unpriceable(security_id, NO_CURRENCY))
            else:
                quote = Quote(symbol, as_of, price, currency, SOURCE, security_id, fetched_at)
                found.setdefault((symbol, as_of), []).append(quote)
    quotes = []
    for same_day in found.values():
        # The store holds one price per ticker a day. Two holdings that disagree on it are
        # both doubtful, so neither is chosen quietly: both are listed instead.
        if len({(quote.price, quote.currency) for quote in same_day}) == 1:
            quotes.append(same_day[0])
        else:
            unpriceable += [Unpriceable(quote.source_ref, CONFLICT) for quote in same_day]
    return InstitutionPrices(tuple(quotes), tuple(unpriceable))


def record(store: PriceStore, prices: InstitutionPrices) -> list[Recorded]:
    """Store every quote. A changed price for a day already stored is kept as a correction."""
    return [store.record(quote) for quote in prices.quotes]


def _list(raw: Mapping[str, Any], name: str) -> Sequence[Mapping[str, Any]]:
    value = raw.get(name)
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise HoldingsMalformedError(f"the holdings response has no list of {name}")
    return value


def _text(raw: Mapping[str, Any], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value:
        raise HoldingsMalformedError(f"a holding has no {name}")
    return value


def _decimal(raw: Mapping[str, Any], name: str) -> Decimal | None:
    value = raw.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise HoldingsMalformedError(f"{name} is not a decimal; parse with parse_float=Decimal")
    number = Decimal(value)
    return number if number.is_finite() else None


def _day(raw: Mapping[str, Any], name: str) -> date | None:
    value = raw.get(name)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except TypeError, ValueError:
        raise HoldingsMalformedError(f"{name} is not a date") from None


def _moment(raw: Mapping[str, Any], name: str) -> date | None:
    """The day of a moment the institution gave, as it gave it: some send the time a price was
    current instead of its day."""
    value = raw.get(name)
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except TypeError, ValueError:
        raise HoldingsMalformedError(f"{name} is not a date and time") from None
