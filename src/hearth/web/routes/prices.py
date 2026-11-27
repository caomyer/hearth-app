"""Getting a better price: asking the source again, or entering one by hand.

Neither recomputes figures silently: each records prices, then starts a recompute the page
shows as in progress, so the number a member sees always has a knowable as-of time.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from hearth.disclosure import Concealed
from hearth.valuation.pricestore import PriceStore
from hearth.valuation.quotes import Quote, as_entered, enterable
from hearth.valuation.refresh import refresh_prices
from hearth.web.deps import Acting, App, Conn, Viewing

router = APIRouter(prefix="/net-worth/prices")

HAND = "hand"
REFRESH_DAYS = 10


@router.post("")
def enter_price(
    app: App,
    conn: Conn,
    session: Acting,
    view: Viewing,
    symbol: Annotated[str, Form()],
    day: Annotated[date, Form()],
    price: Annotated[str, Form()],
) -> RedirectResponse:
    if view.books is None or not view.may_price(symbol):
        raise Concealed
    try:
        amount = Decimal(price)
    except InvalidOperation:
        return _back("price-not-understood")
    if not amount.is_finite() or amount <= 0 or day > view.books.as_of:
        return _back("price-not-understood")
    if not enterable(amount):
        return _back("price-out-of-range")
    PriceStore(conn).record(
        Quote(
            symbol=symbol,
            as_of=day,
            price=as_entered(amount),
            currency=view.books.currency,
            source=HAND,
            source_ref=f"entered by {session.member.username}",
            fetched_at=app.clock(),
        )
    )
    app.bookkeeper.recompute_in_background()
    return _back("price-recorded")


@router.post("/refresh")
def refresh(app: App, conn: Conn, session: Acting, view: Viewing) -> RedirectResponse:
    if view.books is None:
        raise Concealed
    today = view.books.as_of
    report = refresh_prices(
        app.price_source,
        PriceStore(conn),
        view.stale_symbols(),
        today - timedelta(days=REFRESH_DAYS),
        today,
    )
    if report.unavailable:
        return _back("source-unavailable")
    app.bookkeeper.recompute_in_background()
    return _back("source-asked")


def _back(outcome: str) -> RedirectResponse:
    return RedirectResponse(f"/net-worth?outcome={outcome}", status_code=303)
