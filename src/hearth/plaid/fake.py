"""A stand-in for Plaid, answering in-process, for the demo and the tests. Nothing is real.

It speaks the same JSON over the same `Transport` the client uses for the real API, so the
code under test is the code that would run against Plaid, down to the request bodies: only
the network is missing. It keeps its own Items, cursors and invented transactions, honours
`days_requested` against what each invented institution is willing to send, and can be told
to fail the way Plaid fails: an institution wanting its member to sign in again, data that
changes between pages, or no network at all.

History arrives as it does from Plaid, after linking rather than with it. A new Item's first
sync finds nothing and says NOT_READY; once a caller has caught up, the last month arrives
(INITIAL_UPDATE_COMPLETE), and once they catch up again, the rest of it
(HISTORICAL_UPDATE_COMPLETE). `history_ready` skips the wait, for an Item linked long ago.

Every institution, account, payee and figure in it is invented.
"""

import json
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from hearth.plaid.client import (
    ADDITIONAL_CONSENT_REQUIRED,
    DEMO_HOST,
    LOGIN_REQUIRED,
    MUTATED_DURING_PAGINATION,
    NO_INVESTMENT_ACCOUNTS,
    PRIMARIES,
    PRODUCT_NOT_READY,
    PRODUCTS_NOT_SUPPORTED,
)

LINK_TOKEN_HOURS = 4
"""How long a Link token is good for after it is made, at Plaid and here."""

PAYEES = (
    ("Larkspur Grocer", "FOOD_AND_DRINK_GROCERIES", Decimal("64.20")),
    ("Tinderbox Coffee", "FOOD_AND_DRINK_COFFEE", Decimal("5.75")),
    ("Quillfeather Stationers", "GENERAL_MERCHANDISE_OFFICE_SUPPLIES", Decimal("23.40")),
    ("Marrowbone Transit", "TRANSPORTATION_PUBLIC_TRANSIT", Decimal("2.90")),
    ("Bramble Pharmacy", "MEDICAL_PHARMACIES_AND_SUPPLEMENTS", Decimal("18.65")),
    ("Cobblestone Utilities", "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY", Decimal("92.10")),
)


@dataclass(frozen=True, slots=True)
class FakeTransaction:
    name: str
    amount: Decimal
    day: date
    category: str | None = None
    pending: bool = False


@dataclass(frozen=True, slots=True)
class FakeHolding:
    """A position an invented brokerage holds, priced as it prices it for the last session."""

    ticker: str | None
    name: str
    quantity: Decimal
    price: Decimal
    type: str = "mutual fund"


@dataclass(frozen=True, slots=True)
class FakeAccount:
    key: str
    name: str
    mask: str
    type: str
    subtype: str
    current: Decimal | None
    """What the account holds, or owes on a card, as Plaid reports it; Plaid may send none."""
    available: Decimal | None = None
    transactions: tuple[FakeTransaction, ...] | None = None
    """Exactly these, or, when None, an invented routine over the history the bank sends."""
    holdings: tuple[FakeHolding, ...] = ()
    """What an investment account holds, besides its cash, which is what `current` leaves and
    the stand-in lists as a cash holding, as Plaid does."""


@dataclass(frozen=True, slots=True)
class FakeInstitution:
    id: str
    name: str
    accounts: tuple[FakeAccount, ...]
    history_days: int = 730
    """The most history this invented bank sends, whatever is asked for."""


DEMO_INSTITUTIONS = (
    FakeInstitution(
        "ins_lakeside",
        "Lakeside Bank",
        (
            FakeAccount("chk", "Checking", "5521", "depository", "checking", Decimal("2410.55")),
            FakeAccount("sav", "Savings", "5538", "depository", "savings", Decimal("9120.00")),
        ),
    ),
    FakeInstitution(
        "ins_pinecone",
        "Pinecone Card",
        (FakeAccount("card", "Credit card", "9904", "credit", "credit card", Decimal("684.12")),),
        history_days=90,
    ),
    FakeInstitution(
        "ins_juniper",
        "Juniper Brokerage",
        (
            FakeAccount(
                "ira",
                "Individual account",
                "7710",
                "investment",
                "brokerage",
                Decimal("18452.30"),
                holdings=(
                    FakeHolding(
                        "JNPRX", "Juniper Total Market", Decimal("142.118"), Decimal("88.21")
                    ),
                    FakeHolding("JBNDX", "Juniper Bond Index", Decimal("61.5"), Decimal("41.06")),
                    FakeHolding(None, "Juniper Stable Value Trust", Decimal("12"), Decimal("10")),
                ),
            ),
        ),
    ),
)


@dataclass
class _Item:
    item_id: str
    client_id: str
    institution: FakeInstitution
    access_token: str
    days: int
    linked: date
    log: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    """What changed, in order: ("added" | "modified" | "removed", transaction)."""
    arriving: list[list[tuple[str, dict[str, Any]]]] = field(default_factory=list)
    """History Plaid has not sent yet, in the order it arrives: the last month, then the rest.
    Each joins the log after a sync that has caught up with what came before."""
    login_required: bool = False
    removed: bool = False
    investments: bool = False
    """Investments was added to the Item, which happens the first time its holdings are read."""


class FakePlaid:
    """An in-process Plaid. Call it as a `Transport`: ``fake(url, body) -> (status, body)``."""

    def __init__(
        self,
        institutions: Sequence[FakeInstitution] = DEMO_INSTITUTIONS,
        *,
        today: Callable[[], date] = date.today,
        accept_any_keys: bool = False,
    ) -> None:
        self.institutions = {i.id: i for i in institutions}
        self.clients: dict[str, str] = {}
        self.accept_any_keys = accept_any_keys
        """For the demo, where a member may type any keys at all and still try the flow."""
        self.calls: list[str] = []
        """Every path asked for, in order, so a test can see what was (and was not) sent."""
        self.unreachable = False
        self.page_size: int | None = None
        self.slow_history = False
        """Never finish preparing history, as a slow institution can for hours."""
        self._today = today
        self._link_tokens: dict[str, tuple[str, int, str | None]] = {}
        self._public_tokens: dict[str, tuple[str, str]] = {}
        self._updates: dict[str, str] = {}
        self._items: dict[str, _Item] = {}
        self._failures: list[tuple[str, str]] = []

    def __repr__(self) -> str:
        return f"FakePlaid({len(self._items)} Items)"

    # What a test or the demo arranges.

    def register_client(self, client_id: str, secret: str) -> None:
        self.clients[client_id] = secret

    def fail_next(self, path: str, error_code: str) -> None:
        """Answer the next request for `path` with Plaid's error `error_code`."""
        self._failures.append((path, error_code))

    def require_login(self, item_id: str) -> None:
        self._items[item_id].login_required = True

    def history_ready(self, item_id: str) -> None:
        """Have the Item's whole history ready at once, as it is hours after linking."""
        item = self._items[item_id]
        for batch in item.arriving:
            item.log.extend(batch)
        item.arriving = []

    def change(
        self,
        item_id: str,
        kind: str,
        transaction: FakeTransaction,
        account: str,
        *,
        transaction_id: str | None = None,
    ) -> str:
        """Add a transaction to an Item's history, as a bank posting a new one would; or, with
        the `transaction_id` of one it sent, revise ("modified") or withdraw ("removed") it."""
        item = self._items[item_id]
        raw = self._raw(item, account, transaction)
        if transaction_id is not None:
            raw["transaction_id"] = transaction_id
        item.log.append((kind, raw))
        return str(raw["transaction_id"])

    def report(self, item_id: str, account: str, current: Decimal | None) -> None:
        """Have the bank report `current` for one of an Item's accounts from now on, as it does
        once a transaction posts."""
        item = self._items[item_id]
        item.institution = replace(
            item.institution,
            accounts=tuple(
                replace(a, current=current) if a.key == account else a
                for a in item.institution.accounts
            ),
        )

    def hold(self, item_id: str, account: str, holdings: tuple[FakeHolding, ...]) -> None:
        """Have the brokerage report `holdings` for one of an Item's investment accounts from
        now on, as it does once a trade settles; the account is worth what it was, the rest
        of it in cash."""
        item = self._items[item_id]
        item.institution = replace(
            item.institution,
            accounts=tuple(
                replace(a, holdings=holdings) if a.key == account else a
                for a in item.institution.accounts
            ),
        )

    def items(self) -> list[str]:
        return [item_id for item_id, item in self._items.items() if not item.removed]

    def item_of(self, access_token: str) -> str | None:
        return next(
            (i.item_id for i in self._items.values() if i.access_token == access_token), None
        )

    def made_elsewhere(
        self, client_id: str, institution_id: str, *, days: int, linked: date
    ) -> tuple[str, str]:
        """An Item another tool linked through the same Plaid account, `days` of history asked
        for on `linked`, all of it sent long ago: its (item id, access token), which hearth has
        never been given."""
        item = _Item(
            item_id=f"item-demo-{secrets.token_hex(6)}",
            client_id=client_id,
            institution=self.institutions[institution_id],
            access_token=f"access-demo-{secrets.token_hex(12)}",
            days=min(days, self.institutions[institution_id].history_days),
            linked=linked,
        )
        item.log = [("added", raw) for raw in self._history(item, until=self._today())]
        self._items[item.item_id] = item
        return item.item_id, item.access_token

    def update_token(self, link_token: str) -> str:
        """What Link returns after a member signs in again through an update-mode token."""
        if link_token not in self._link_tokens or self._link_tokens[link_token][2] is None:
            raise KeyError("no such update-mode link token")
        token = f"public-demo-{secrets.token_hex(8)}"
        self._updates[token] = link_token
        return token

    def signed_in_again(self, public_token: str) -> bool:
        """The member finished signing in again: the Item works from here on."""
        link_token = self._updates.pop(public_token, None)
        if link_token is None:
            return False
        item_id = self._link_tokens[link_token][2]
        assert item_id is not None  # noqa: S101 - only update-mode tokens are recorded
        self._items[item_id].login_required = False
        return True

    def public_token(self, link_token: str, institution_id: str) -> str:
        """What Link would return after a member signs in at `institution_id`."""
        if link_token not in self._link_tokens or institution_id not in self.institutions:
            raise KeyError("no such link token or institution")
        token = f"public-demo-{secrets.token_hex(8)}"
        self._public_tokens[token] = (link_token, institution_id)
        return token

    # The API.

    def __call__(self, url: str, body: bytes) -> tuple[int, bytes]:
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != DEMO_HOST:
            raise ValueError("the stand-in answers only requests addressed to it")
        path = parts.path
        self.calls.append(path)
        if self.unreachable:
            raise ConnectionRefusedError("the stand-in is switched off")
        request = json.loads(body, parse_float=Decimal)
        known = self.clients.get(request.get("client_id")) == request.get("secret")
        if not (known or self.accept_any_keys):
            return _error(400, "INVALID_INPUT", "INVALID_API_KEYS")
        for index, (failing, code) in enumerate(self._failures):
            if failing == path:
                del self._failures[index]
                return _error(400, _type_of(code), code)
        handler = self._handlers().get(path)
        if handler is None:
            return _error(404, "INVALID_REQUEST", "UNKNOWN_ENDPOINT")
        return handler(request)

    def _handlers(self) -> dict[str, Callable[[dict[str, Any]], tuple[int, bytes]]]:
        return {
            "/link/token/create": self._link_token,
            "/item/public_token/exchange": self._exchange,
            "/item/get": self._item_get,
            "/accounts/get": self._accounts_get,
            "/transactions/sync": self._sync,
            "/investments/holdings/get": self._holdings,
            "/investments/transactions/get": self._activity,
            "/item/remove": self._remove,
        }

    def _link_token(self, request: dict[str, Any]) -> tuple[int, bytes]:
        update = None
        if "access_token" in request:
            item = self._by_token(request["access_token"])
            if item is None:
                return _error(400, "INVALID_INPUT", "INVALID_ACCESS_TOKEN")
            update = item.item_id
        days = int(request.get("transactions", {}).get("days_requested", 90))
        token = f"link-demo-{secrets.token_hex(8)}"
        self._link_tokens[token] = (request["client_id"], days, update)
        # Four hours from when it is made, as Plaid's are.
        expires = datetime.now(UTC) + timedelta(hours=LINK_TOKEN_HOURS)
        return _ok({"link_token": token, "expiration": expires.isoformat().replace("+00:00", "Z")})

    def _exchange(self, request: dict[str, Any]) -> tuple[int, bytes]:
        found = self._public_tokens.pop(request.get("public_token", ""), None)
        if found is None:
            return _error(400, "INVALID_INPUT", "INVALID_PUBLIC_TOKEN")
        link_token, institution_id = found
        client_id, days, _ = self._link_tokens[link_token]
        if client_id != request["client_id"]:
            return _error(400, "INVALID_INPUT", "INVALID_PUBLIC_TOKEN")
        item = _Item(
            item_id=f"item-demo-{secrets.token_hex(6)}",
            client_id=client_id,
            institution=self.institutions[institution_id],
            access_token=f"access-demo-{secrets.token_hex(12)}",
            days=min(days, self.institutions[institution_id].history_days),
            linked=self._today(),
        )
        history = [("added", raw) for raw in self._history(item)]
        recent = (item.linked - timedelta(days=30)).isoformat()
        item.arriving = [
            [(k, raw) for k, raw in history if raw["date"] >= recent],
            [(k, raw) for k, raw in history if raw["date"] < recent],
        ]
        self._items[item.item_id] = item
        return _ok({"item_id": item.item_id, "access_token": item.access_token})

    def _usable(self, request: dict[str, Any]) -> _Item | tuple[int, bytes]:
        item = self._by_token(request.get("access_token", ""))
        if item is None or item.client_id != request["client_id"]:
            return _error(400, "INVALID_INPUT", "INVALID_ACCESS_TOKEN")
        if item.login_required:
            return _error(400, "ITEM_ERROR", LOGIN_REQUIRED)
        return item

    def _item_get(self, request: dict[str, Any]) -> tuple[int, bytes]:
        item = self._usable(request)
        if not isinstance(item, _Item):
            return item
        return _ok(
            {
                "item": {
                    "item_id": item.item_id,
                    "institution_id": item.institution.id,
                    "institution_name": item.institution.name,
                    "created_at": f"{item.linked.isoformat()}T09:00:00Z",
                    "products": ["transactions", *(["investments"] * item.investments)],
                    "billed_products": ["transactions", *(["investments"] * item.investments)],
                    "available_products": (
                        ["investments"] if _invests(item) and not item.investments else []
                    ),
                }
            }
        )

    def _accounts_get(self, request: dict[str, Any]) -> tuple[int, bytes]:
        item = self._usable(request)
        if not isinstance(item, _Item):
            return item
        return _ok({"accounts": [self._account(item, a) for a in item.institution.accounts]})

    def _sync(self, request: dict[str, Any]) -> tuple[int, bytes]:
        item = self._usable(request)
        if not isinstance(item, _Item):
            return item
        cursor = request.get("cursor") or ""
        start = int(cursor.removeprefix("c")) if cursor.startswith("c") else 0
        visible = item.log
        size = min(int(request.get("count", 100)), self.page_size or 500)
        page = visible[start : start + size]
        end = start + len(page)
        status = _STATUSES[len(item.arriving)]
        has_more = end < len(visible)
        if start >= len(visible) and item.arriving and not self.slow_history:
            item.log.extend(item.arriving.pop(0))
        return _ok(
            {
                "added": [raw for kind, raw in page if kind == "added"],
                "modified": [raw for kind, raw in page if kind == "modified"],
                "removed": [
                    {"transaction_id": raw["transaction_id"], "account_id": raw["account_id"]}
                    for kind, raw in page
                    if kind == "removed"
                ],
                "next_cursor": f"c{end}",
                "has_more": has_more,
                "transactions_update_status": status,
                "accounts": [],
            }
        )

    def _holdings(self, request: dict[str, Any]) -> tuple[int, bytes]:
        item = self._usable(request)
        if not isinstance(item, _Item):
            return item
        if not _invests(item):
            return _error(400, "ITEM_ERROR", NO_INVESTMENT_ACCOUNTS)
        item.investments = True
        # The institution prices a holding at the last session's close: a weekday, never today.
        priced_on = self._today() - timedelta(days=1)
        while priced_on.weekday() >= 5:
            priced_on -= timedelta(days=1)
        accounts = [a for a in item.institution.accounts if a.type == "investment"]
        holdings, securities = [], {}
        for account in accounts:
            for held in account.holdings:
                security_id = f"sec-{item.institution.id}-{held.ticker or held.name}"
                securities[security_id] = {
                    "security_id": security_id,
                    "name": held.name,
                    "ticker_symbol": held.ticker,
                    "cusip": None,
                    "isin": None,
                    "type": held.type,
                    "is_cash_equivalent": False,
                    "iso_currency_code": "USD",
                }
                holdings.append(
                    {
                        "account_id": f"{item.item_id}-{account.key}",
                        "security_id": security_id,
                        "quantity": held.quantity,
                        "institution_price": held.price,
                        "institution_price_as_of": priced_on.isoformat(),
                        "institution_price_datetime": None,
                        "institution_value": (held.quantity * held.price).quantize(Decimal("0.01")),
                        "cost_basis": None,
                        "iso_currency_code": "USD",
                    }
                )
            # Plaid lists an account's uninvested cash as a holding of its own: what the
            # account is worth beyond its securities.
            invested = sum(
                ((h.quantity * h.price).quantize(Decimal("0.01")) for h in account.holdings),
                Decimal(0),
            )
            if account.current is not None and account.current > invested:
                cash = account.current - invested
                securities[_CASH] = {
                    "security_id": _CASH,
                    "name": "U S Dollar",
                    "ticker_symbol": "CUR:USD",
                    "cusip": None,
                    "isin": None,
                    "type": "cash",
                    "is_cash_equivalent": True,
                    "iso_currency_code": "USD",
                }
                holdings.append(
                    {
                        "account_id": f"{item.item_id}-{account.key}",
                        "security_id": _CASH,
                        "quantity": cash,
                        "institution_price": Decimal(1),
                        "institution_price_as_of": None,
                        "institution_price_datetime": None,
                        "institution_value": cash,
                        "cost_basis": cash,
                        "iso_currency_code": "USD",
                    }
                )
        return _ok(
            {
                "accounts": [self._account(item, a) for a in accounts],
                "holdings": holdings,
                "securities": list(securities.values()),
                "request_id": "demo",
            }
        )

    def _activity(self, request: dict[str, Any]) -> tuple[int, bytes]:
        """An invented brokerage's activity over the days asked for, newest first, a page at a
        time: each holding bought on one day, a dividend each quarter on every fund with a
        ticker, and a contribution each month, all at the holding's own price."""
        item = self._usable(request)
        if not isinstance(item, _Item):
            return item
        if not _invests(item):
            return _error(400, "ITEM_ERROR", NO_INVESTMENT_ACCOUNTS)
        item.investments = True
        start = date.fromisoformat(request["start_date"])
        end = date.fromisoformat(request["end_date"])
        options = request.get("options", {})
        count, offset = int(options.get("count", 100)), int(options.get("offset", 0))
        accounts = [a for a in item.institution.accounts if a.type == "investment"]
        rows, securities = [], {}
        for account in accounts:
            account_id = f"{item.item_id}-{account.key}"
            bought = self._today() - timedelta(days=400)
            for held in account.holdings:
                security_id = f"sec-{item.institution.id}-{held.ticker or held.name}"
                securities[security_id] = {
                    "security_id": security_id,
                    "name": held.name,
                    "ticker_symbol": held.ticker,
                    "type": held.type,
                }
                rows.append(
                    _activity_row(
                        account_id,
                        security_id,
                        bought,
                        f"BUY {held.name}",
                        "buy",
                        "buy",
                        held.quantity,
                        held.price,
                        held.quantity * held.price,
                    )
                )
                if held.ticker is None:
                    continue
                for quarter in range(1, 5):
                    paid = bought + timedelta(days=91 * quarter)
                    dividend = (held.quantity * held.price / 400).quantize(Decimal("0.01"))
                    rows.append(
                        _activity_row(
                            account_id,
                            security_id,
                            paid,
                            f"DIVIDEND {held.ticker}",
                            "cash",
                            "dividend",
                            Decimal(0),
                            Decimal(0),
                            -dividend,
                        )
                    )
            for month in range(13):
                paid = bought + timedelta(days=30 * month)
                rows.append(
                    _activity_row(
                        account_id,
                        None,
                        paid,
                        "CONTRIBUTION",
                        "cash",
                        "contribution",
                        Decimal(0),
                        Decimal(0),
                        Decimal("-250.00"),
                    )
                )
        within = [r for r in rows if start.isoformat() <= r["date"] <= end.isoformat()]
        within.sort(key=lambda r: (r["date"], r["investment_transaction_id"]), reverse=True)
        return _ok(
            {
                "accounts": [self._account(item, a) for a in item.institution.accounts],
                "investment_transactions": within[offset : offset + count],
                "securities": list(securities.values()),
                "total_investment_transactions": len(within),
            }
        )

    def _remove(self, request: dict[str, Any]) -> tuple[int, bytes]:
        item = self._by_token(request.get("access_token", ""))
        if item is None or item.client_id != request["client_id"]:
            return _error(400, "INVALID_INPUT", "INVALID_ACCESS_TOKEN")
        item.removed = True
        item.access_token = ""
        return _ok({})

    def _by_token(self, token: str) -> _Item | None:
        if not token:
            return None
        return next(
            (i for i in self._items.values() if i.access_token == token and not i.removed), None
        )

    def _account(self, item: _Item, account: FakeAccount) -> dict[str, Any]:
        return {
            "account_id": f"{item.item_id}-{account.key}",
            "name": account.name,
            "official_name": None,
            "mask": account.mask,
            "type": account.type,
            "subtype": account.subtype,
            "balances": {
                "current": account.current,
                "available": account.available,
                "iso_currency_code": "USD",
            },
        }

    def _history(self, item: _Item, until: date | None = None) -> list[dict[str, Any]]:
        """What the bank sends from the first day asked for to `until`, by default the day
        the Item was linked."""
        start = item.linked - timedelta(days=item.days - 1)
        end = until or item.linked
        found = []
        for account in item.institution.accounts:
            found_here = account.transactions
            if found_here is None:
                found_here = tuple(_routine(account, start, end))
            for transaction in found_here:
                if start <= transaction.day <= end:
                    found.append(self._raw(item, account.key, transaction))
        return sorted(found, key=lambda raw: (raw["date"], raw["transaction_id"]))

    def _raw(self, item: _Item, account: str, transaction: FakeTransaction) -> dict[str, Any]:
        return {
            "transaction_id": f"tx-{secrets.token_hex(8)}",
            "account_id": f"{item.item_id}-{account}",
            "date": transaction.day.isoformat(),
            "authorized_date": transaction.day.isoformat(),
            "amount": transaction.amount,
            "iso_currency_code": "USD",
            "name": transaction.name,
            "merchant_name": transaction.name,
            "original_description": transaction.name.upper(),
            "personal_finance_category": (
                {"primary": _primary(transaction.category), "detailed": transaction.category}
                if transaction.category
                else None
            ),
            "pending": transaction.pending,
            "pending_transaction_id": None,
        }


_CASH = "sec-cash-usd"
"""The security Plaid lists an investment account's uninvested dollars as."""


def _invests(item: _Item) -> bool:
    return any(account.type == "investment" for account in item.institution.accounts)


def _primary(detailed: str) -> str:
    """The primary category Plaid sends beside a detailed one: ``FOOD_AND_DRINK`` for
    ``FOOD_AND_DRINK_COFFEE``."""
    return next((p for p in PRIMARIES if detailed.startswith(f"{p}_")), detailed)


def _routine(account: FakeAccount, start: date, end: date) -> list[FakeTransaction]:
    """An invented, steady routine, in which money comes and goes as it does in a household:
    a payee every few days on a checking account or a card, pay twice a month, rent, the
    month's surplus moved to savings, a card paid off for what it was used for the month
    before, and a savings account's interest. So an account's history adds up to about what
    it holds, and what it held before the history is a plausible balance, not two years of
    pay that was never spent."""
    found = []
    day = start
    index = 0
    while day <= end:
        name, category, amount = PAYEES[index % len(PAYEES)]
        if account.type in {"depository", "credit"} and account.subtype != "savings":
            found.append(FakeTransaction(name, amount + Decimal(index % 7), day, category))
        if account.subtype == "checking" and day.day in {1, 15}:
            found.append(
                FakeTransaction("Payroll Wren & Co", Decimal("-2140.00"), day, "INCOME_WAGES")
            )
        day += timedelta(days=3)
        index += 1
    monthly = []
    month = start.replace(day=1)
    while month <= end:
        within = [t for t in found if (t.day.year, t.day.month) == (month.year, month.month)]
        monthly += [t for t in _monthly(account, month, within) if start <= t.day <= end]
        month = (month + timedelta(days=32)).replace(day=1)
    return sorted([*found, *monthly], key=lambda t: t.day)


def _monthly(
    account: FakeAccount, month: date, within: list[FakeTransaction]
) -> list[FakeTransaction]:
    """What the routine moves once a month, given the month's other rows."""
    if account.subtype == "savings":
        return [FakeTransaction("Interest", Decimal("-1.15"), month, "INCOME_INTEREST_EARNED")]
    if account.subtype == "checking":
        rent = Decimal("1850.00")
        surplus = -sum((t.amount for t in within), Decimal(0)) - rent
        moved = [
            FakeTransaction(
                "Hollins Lettings", rent, month.replace(day=2), "RENT_AND_UTILITIES_RENT"
            )
        ]
        if surplus >= 100:
            moved.append(
                FakeTransaction(
                    "Transfer to savings",
                    (surplus // 100) * 100,
                    month.replace(day=28),
                    "TRANSFER_OUT_SAVINGS",
                )
            )
        return moved
    if account.type == "credit" and within:
        spent = sum((t.amount for t in within), Decimal(0))
        due = (month + timedelta(days=32)).replace(day=20)
        return [
            FakeTransaction("Payment Thank You", -spent, due, "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT")
        ]
    return []


_STATUSES = ("HISTORICAL_UPDATE_COMPLETE", "INITIAL_UPDATE_COMPLETE", "NOT_READY")
"""Plaid's `transactions_update_status`, by how many batches of history are still to come."""


def _activity_row(
    account_id: str,
    security_id: str | None,
    day: date,
    name: str,
    type_: str,
    subtype: str,
    quantity: Decimal,
    price: Decimal,
    amount: Decimal,
) -> dict[str, Any]:
    """One invented investment transaction, its id fixed by what it is, as Plaid's are."""
    return {
        "investment_transaction_id": f"inv-{account_id}-{security_id}-{day}-{subtype}",
        "account_id": account_id,
        "security_id": security_id,
        "date": day.isoformat(),
        "name": name,
        "type": type_,
        "subtype": subtype,
        "quantity": quantity,
        "price": price,
        "amount": amount.quantize(Decimal("0.01")),
        "fees": Decimal(0),
        "iso_currency_code": "USD",
        "cancel_transaction_id": None,
    }


def _type_of(code: str) -> str:
    if code == LOGIN_REQUIRED:
        return "ITEM_ERROR"
    if code == MUTATED_DURING_PAGINATION:
        return "TRANSACTIONS_ERROR"
    if code in {NO_INVESTMENT_ACCOUNTS, PRODUCTS_NOT_SUPPORTED, ADDITIONAL_CONSENT_REQUIRED}:
        return "ITEM_ERROR"
    if code == PRODUCT_NOT_READY:
        return "ITEM_ERROR"
    return "API_ERROR"


def _encode(value: object) -> bytes:
    def default(obj: object) -> object:
        if isinstance(obj, Decimal):
            return float(obj)
        raise TypeError(type(obj).__name__)

    return json.dumps(value, default=default).encode()


def _ok(value: dict[str, Any]) -> tuple[int, bytes]:
    # Plaid names every request it answers, as its support asks for that name.
    return 200, _encode({**value, "request_id": f"demo{secrets.token_hex(6)}"})


def _error(status: int, error_type: str, code: str) -> tuple[int, bytes]:
    return status, _encode(
        {
            "error_type": error_type,
            "error_code": code,
            "error_message": code.lower(),
            "request_id": "demo",
        }
    )
