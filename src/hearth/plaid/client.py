"""Plaid's API, the few calls hearth makes, each refused unless its operation is approved.

API reference: https://plaid.com/docs/api/. Every call is a JSON POST carrying the credential
set's client id and secret, and most carry an Item's access token. Those three never leave
`Secret` except inside the one expression that writes the request body, and nothing Plaid
sends back that holds a token is ever bound to a plain variable or shown in a repr. Every
failure is raised afresh, outside the handler that caught the underlying error, so an error
reporter that walks the whole exception chain and prints every frame's locals finds no
request body, response body or key in it (``tests/plaid/test_no_leak.py``).

Every call is noted in the runtime log (``hearth.runlog``): the path, the environment, and how
it ended, with Plaid's error code and the request id Plaid gave it. Nothing else of a request
or a response is ever logged.

Amounts are read as `Decimal`, never as floats, and kept with Plaid's own sign: positive is
money leaving the account.
"""

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from hearth import runlog
from hearth.plaid.approval import Approval, Operation
from hearth.valuation.institution import is_cash
from hearth.vault.secret import Secret

PLAID_VERSION = "2020-09-14"
TIMEOUT_SECONDS = 60
SYNC_PAGE_SIZE = 500
CLIENT_NAME = "Hearth"

_log = logging.getLogger(__name__)


class Environment(StrEnum):
    PRODUCTION = "production"
    SANDBOX = "sandbox"
    DEMO = "demo"
    """Hearth's own stand-in for Plaid (``hearth.plaid.fake``), served in-process for the demo
    and the tests. It never reaches a network and holds nobody's money."""


HOSTS = {
    Environment.PRODUCTION: "https://production.plaid.com",
    Environment.SANDBOX: "https://sandbox.plaid.com",
}
DEMO_HOST = "https://plaid.demo.invalid"
"""Where requests to the stand-in go. `https_transport` refuses it: only the fake answers."""

type Transport = Callable[[str, bytes], tuple[int, bytes]]
"""Performs an HTTPS POST of a JSON body and returns the status code and body, whatever the
status. Raises `OSError` when Plaid cannot be reached."""


def https_transport(url: str, body: bytes) -> tuple[int, bytes]:
    if not url.startswith(tuple(f"{host}/" for host in HOSTS.values())):
        raise ValueError("refusing to send a request outside the Plaid API")
    request = urllib.request.Request(  # noqa: S310 - https only, checked above
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Plaid-Version": PLAID_VERSION},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


@dataclass(frozen=True, slots=True)
class Credentials:
    """One Plaid account's keys. Both are secrets; neither is ever shown or logged."""

    client_id: Secret
    secret: Secret
    environment: Environment

    def __repr__(self) -> str:
        return f"Credentials(environment={self.environment.value})"


class PlaidError(Exception):
    """A Plaid call did not succeed. The message never carries a key, a token or a body."""


class PlaidUnreachableError(PlaidError):
    """Plaid could not be reached at all."""


class PlaidRejectedError(PlaidError):
    """Plaid answered with an error, named by Plaid's own error type and code."""

    def __init__(
        self, error_type: str, error_code: str, status: int, request_id: str | None = None
    ) -> None:
        super().__init__(f"Plaid refused the request: {error_type} {error_code} (HTTP {status})")
        self.error_type = error_type
        self.error_code = error_code
        self.status = status
        self.request_id = request_id
        """What Plaid support asks for to find this request; never a secret."""


class PlaidMalformedError(PlaidError):
    """Plaid sent something hearth cannot read."""


LOGIN_REQUIRED = "ITEM_LOGIN_REQUIRED"
MUTATED_DURING_PAGINATION = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
ITEM_NOT_FOUND = "ITEM_NOT_FOUND"
"""The Item is already gone at Plaid: removed before, or its member revoked access there."""
NO_INVESTMENT_ACCOUNTS = "NO_INVESTMENT_ACCOUNTS"
PRODUCTS_NOT_SUPPORTED = "PRODUCTS_NOT_SUPPORTED"
"""The institution does not offer the product asked for, here Investments."""
ADDITIONAL_CONSENT_REQUIRED = "ADDITIONAL_CONSENT_REQUIRED"
"""The member never consented to share the product's data: only Link can ask them to."""


class _Payload(dict[str, Any]):
    """A parsed response. Its repr shows only its keys, since some responses hold a token."""

    def __repr__(self) -> str:
        return f"Payload(keys={sorted(self)})"


@dataclass(frozen=True, slots=True)
class _Reply:
    status: int
    body: bytes

    def __repr__(self) -> str:
        return f"Reply(status={self.status}, {len(self.body)} bytes)"


@dataclass(frozen=True, slots=True)
class LinkToken:
    """What Plaid Link opens with. Short-lived and made for the browser, so not a secret."""

    token: str
    expires: datetime | None


@dataclass(frozen=True, slots=True)
class Exchanged:
    item_id: str
    access_token: Secret


@dataclass(frozen=True, slots=True)
class Institution:
    id: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class ItemDetails:
    """What Plaid says of an Item: where it is, when it was made, and what it may be asked for."""

    item_id: str
    institution: Institution
    created_at: datetime | None
    products: frozenset[str]
    """Every product the Item has or may be asked for without linking again: Plaid's
    ``products``, ``billed_products`` and ``available_products`` together."""


@dataclass(frozen=True, slots=True)
class BankAccount:
    account_id: str
    name: str
    official_name: str | None
    mask: str | None
    type: str
    subtype: str | None
    current: Decimal | None
    available: Decimal | None
    currency: str | None


PRIMARIES = (
    "INCOME",
    "TRANSFER_IN",
    "TRANSFER_OUT",
    "LOAN_PAYMENTS",
    "LOAN_DISBURSEMENTS",
    "BANK_FEES",
    "ENTERTAINMENT",
    "FOOD_AND_DRINK",
    "GENERAL_MERCHANDISE",
    "HOME_IMPROVEMENT",
    "MEDICAL",
    "PERSONAL_CARE",
    "GENERAL_SERVICES",
    "GOVERNMENT_AND_NON_PROFIT",
    "TRANSPORTATION",
    "TRAVEL",
    "RENT_AND_UTILITIES",
    "OTHER",
)
"""Plaid's primary personal finance categories. A detailed category is its primary, an
underscore, and the detail: ``FOOD_AND_DRINK_COFFEE``."""


@dataclass(frozen=True, slots=True)
class Transaction:
    transaction_id: str
    account_id: str
    day: date
    """Plaid's ``date``: when the transaction posted (for a card, when it was made)."""
    authorized_date: date | None
    amount: Decimal
    currency: str | None
    name: str | None
    merchant_name: str | None
    original_description: str | None
    category: str | None
    pending: bool
    pending_transaction_id: str | None


@dataclass(frozen=True, slots=True)
class Position:
    """One holding in an investment account, as its brokerage reported it: how much of which
    security, and what the brokerage says it cost, never how it came to be held."""

    account_id: str
    security_id: str
    ticker: str | None
    name: str | None
    cash: bool
    """Whether the security is money, or as good as money: Plaid's security `type` "cash",
    which covers cash and money market funds, or `is_cash_equivalent`."""
    quantity: Decimal
    value: Decimal | None
    """Plaid's ``institution_value``: what the brokerage says the holding is worth."""
    currency: str | None
    cost_basis: Decimal | None = None
    """Plaid's ``cost_basis``: what the brokerage says was paid for every unit of the holding
    together, or None where it did not say. One figure for the position, never its lots."""


@dataclass(frozen=True, slots=True)
class Holdings:
    """What one ``/investments/holdings/get`` answer says each investment account holds.

    `accounts` are every investment account it answered for, including one that holds
    nothing, which is something the brokerage said, unlike an account it did not answer for."""

    accounts: tuple[str, ...]
    positions: tuple[Position, ...]


@dataclass(frozen=True, slots=True)
class SyncPage:
    added: tuple[Transaction, ...]
    modified: tuple[Transaction, ...]
    removed: tuple[str, ...]
    next_cursor: str
    has_more: bool
    update_status: str | None
    """Plaid's ``transactions_update_status``: whether the history it will send is all in."""


class PlaidClient:
    """The calls hearth makes to Plaid, for one credential set.

    Each refuses with `ApprovalRequired` before sending anything unless `approval` allows its
    operation. Nothing here mutates a bank account; the only calls that change anything at
    Plaid create an Item (Link) or remove one.
    """

    def __init__(self, credentials: Credentials, approval: Approval, transport: Transport) -> None:
        self._credentials = credentials
        self._approval = approval
        self._transport = transport

    def __repr__(self) -> str:
        return f"PlaidClient(environment={self._credentials.environment.value})"

    def create_link_token(
        self,
        *,
        user: str,
        days_requested: int,
        access_token: Secret | None = None,
        redirect_uri: str | None = None,
    ) -> LinkToken:
        """A token for Plaid Link: for a new Item, or, with `access_token`, to sign an existing
        Item in again (update mode), which keeps the Item and so uses no new connection.

        With `redirect_uri`, a bank that takes over the whole page for its own sign-in sends the
        member back there, where Link carries on (``docs/connections.md``). Plaid refuses the
        call unless the address is one of the account's allowed redirect URIs, exactly."""
        self._approval.require(Operation.LINK, "creating a Link token")
        fields: dict[str, object] = {
            "client_name": CLIENT_NAME,
            "language": "en",
            "country_codes": ["US"],
            "user": {"client_user_id": user},
        }
        if redirect_uri is not None:
            fields["redirect_uri"] = redirect_uri
        secrets: dict[str, Secret] = {}
        if access_token is None:
            fields["products"] = ["transactions"]
            # Asked for now so a brokerage connected today can bring in its positions later
            # without connecting again, which would use another of the account's few Items.
            fields["optional_products"] = ["investments"]
            fields["transactions"] = {"days_requested": days_requested}
        else:
            secrets["access_token"] = access_token
        payload = self._call("/link/token/create", fields, secrets)
        expires = payload.get("expiration")
        return LinkToken(
            token=_text(payload, "link_token"),
            expires=datetime.fromisoformat(expires) if isinstance(expires, str) else None,
        )

    def exchange(self, public_token: Secret) -> Exchanged:
        """Turn what Link returned into the Item's long-lived access token."""
        self._approval.require(Operation.LINK, "exchanging a Link public token")
        payload = self._call("/item/public_token/exchange", {}, {"public_token": public_token})
        return Exchanged(_text(payload, "item_id"), Secret(_text(payload, "access_token")))

    def institution(self, access_token: Secret) -> Institution:
        return self.item(access_token).institution

    def item(self, access_token: Secret) -> ItemDetails:
        self._approval.require(Operation.READ, "reading an Item")
        payload = self._call("/item/get", {}, {"access_token": access_token})
        if not isinstance(payload.get("item"), dict):
            raise PlaidMalformedError("Plaid sent an Item without its details")
        item = _Payload(payload["item"])
        created = _optional_text(item, "created_at")
        try:
            created_at = datetime.fromisoformat(created) if created else None
        except ValueError:
            created_at = None
        products = frozenset(
            product
            for name in ("products", "billed_products", "available_products")
            if isinstance(item.get(name), list)
            for product in item[name]
            if isinstance(product, str)
        )
        return ItemDetails(
            item_id=_text(item, "item_id"),
            institution=Institution(
                _optional_text(item, "institution_id"), _optional_text(item, "institution_name")
            ),
            created_at=created_at,
            products=products,
        )

    def accounts(self, access_token: Secret) -> list[BankAccount]:
        """The Item's accounts with the balances Plaid last fetched."""
        self._approval.require(Operation.READ, "reading an Item's accounts")
        payload = self._call("/accounts/get", {}, {"access_token": access_token})
        return [_account(raw) for raw in _list(payload, "accounts")]

    def sync_page(self, access_token: Secret, cursor: str | None) -> SyncPage:
        """One page of what changed since `cursor`; with no cursor, from the beginning."""
        self._approval.require(Operation.READ, "reading an Item's transactions")
        fields: dict[str, object] = {
            "count": SYNC_PAGE_SIZE,
            "options": {"include_original_description": True},
        }
        if cursor:
            fields["cursor"] = cursor
        payload = self._call("/transactions/sync", fields, {"access_token": access_token})
        removed = []
        for raw in _list(payload, "removed"):
            removed.append(_text(raw, "transaction_id"))
        has_more = payload.get("has_more")
        if not isinstance(has_more, bool):
            raise PlaidMalformedError("Plaid sent a sync page without has_more")
        return SyncPage(
            added=tuple(_transaction(raw) for raw in _list(payload, "added")),
            modified=tuple(_transaction(raw) for raw in _list(payload, "modified")),
            removed=tuple(removed),
            next_cursor=_text(payload, "next_cursor"),
            has_more=has_more,
            update_status=_optional_text(payload, "transactions_update_status"),
        )

    def holdings(self, access_token: Secret) -> Mapping[str, Any]:
        """The Item's investment holdings and their securities, as Plaid sent them, read with
        every number a `Decimal` (``hearth.valuation.institution`` reads it).

        This is ``/investments/holdings/get``: positions and the institution's price for each,
        today's only. A brokerage's trades and dividends are ``/investments/transactions/get``,
        which hearth does not call."""
        self._approval.require(Operation.HOLDINGS, "reading an Item's holdings")
        payload = self._call("/investments/holdings/get", {}, {"access_token": access_token})
        _list(payload, "holdings")
        _list(payload, "securities")
        return payload

    def remove(self, access_token: Secret) -> None:
        """End the Item at Plaid. Its access token stops working and its slot is freed.

        An Item Plaid no longer has is already removed, which is all this asks for."""
        self._approval.require(Operation.REMOVE, "removing an Item")
        try:
            self._call("/item/remove", {}, {"access_token": access_token})
        except PlaidRejectedError as error:
            if error.error_code != ITEM_NOT_FOUND:
                raise

    def _call(
        self, path: str, fields: Mapping[str, object], secrets: Mapping[str, Secret]
    ) -> _Payload:
        environment = self._credentials.environment
        url = f"{HOSTS.get(environment, DEMO_HOST)}{path}"
        unreachable: str | None = None
        try:
            reply = _Reply(*self._transport(url, _body(self._credentials, fields, secrets)))
        except OSError as error:
            unreachable = type(error).__name__
        # Raised here, outside the handler, so the transport's own frames and error are not
        # this exception's context: they hold the request body.
        if unreachable is not None:
            _log.warning("%s %s: unreachable (%s)", environment.value, path, unreachable)
            raise PlaidUnreachableError(f"Plaid could not be reached ({unreachable})")
        try:
            payload = _parse(reply)
        except PlaidMalformedError:
            _log.warning("%s %s: unreadable reply (HTTP %d)", environment.value, path, reply.status)
            raise
        request_id = runlog.identifier(payload.get("request_id"))
        if reply.status != 200:
            error_type = runlog.code(payload.get("error_type")) or "UNKNOWN"
            error_code = runlog.code(payload.get("error_code")) or "UNKNOWN"
            _log.warning(
                "%s %s: refused %s %s (HTTP %d) request_id=%s",
                environment.value,
                path,
                error_type,
                error_code,
                reply.status,
                request_id,
            )
            raise PlaidRejectedError(error_type, error_code, reply.status, request_id)
        _log.info("%s %s: ok request_id=%s", environment.value, path, request_id)
        return payload


def _body(
    credentials: Credentials, fields: Mapping[str, object], secrets: Mapping[str, Secret]
) -> bytes:
    return json.dumps(
        {
            "client_id": credentials.client_id.reveal(),
            "secret": credentials.secret.reveal(),
            **fields,
            **{name: value.reveal() for name, value in secrets.items()},
        }
    ).encode()


def _parse(reply: _Reply) -> _Payload:
    payload = _Payload()
    malformed = False
    try:
        parsed = json.loads(reply.body, parse_float=Decimal)
        if isinstance(parsed, dict):
            payload = _Payload(parsed)
        else:
            malformed = True
        # Whatever it was, it may hold a token: it must not stay behind as a plain local.
        del parsed
    except ValueError:
        malformed = True
    # Outside the handler: the decoder's frames hold the body, which may hold a token.
    if malformed:
        raise PlaidMalformedError(
            f"Plaid sent a response that is not a JSON object (HTTP {reply.status})"
        )
    return payload


def _text(raw: Mapping[str, Any], name: str) -> str:
    if not isinstance(raw.get(name), str) or not raw[name]:
        raise PlaidMalformedError(f"Plaid's response has no {name}")
    return str(raw[name])


def _optional_text(raw: Mapping[str, Any], name: str) -> str | None:
    value = raw.get(name)
    return value if isinstance(value, str) and value else None


def _list(raw: Mapping[str, Any], name: str) -> list[_Payload]:
    """The objects listed under `name`, each with a redacted repr like the response itself."""
    if not isinstance(raw.get(name), list) or not all(isinstance(e, dict) for e in raw[name]):
        raise PlaidMalformedError(f"Plaid's response has no list of {name}")
    return [_Payload(entry) for entry in raw[name]]


def _decimal(raw: Mapping[str, Any], name: str) -> Decimal | None:
    value = raw.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise PlaidMalformedError(f"Plaid sent a {name} that is not a number")
    return Decimal(value)


def _day(raw: Mapping[str, Any], name: str) -> date | None:
    value = raw.get(name)
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except TypeError, ValueError:
        raise PlaidMalformedError(f"Plaid sent a {name} that is not a date") from None


def _account(raw: Mapping[str, Any]) -> BankAccount:
    if not isinstance(raw.get("balances"), dict):
        raise PlaidMalformedError("Plaid sent an account without balances")
    balances = _Payload(raw["balances"])
    return BankAccount(
        account_id=_text(raw, "account_id"),
        name=_text(raw, "name"),
        official_name=_optional_text(raw, "official_name"),
        mask=_optional_text(raw, "mask"),
        type=_text(raw, "type"),
        subtype=_optional_text(raw, "subtype"),
        current=_decimal(balances, "current"),
        available=_decimal(balances, "available"),
        currency=_optional_text(balances, "iso_currency_code")
        or _optional_text(balances, "unofficial_currency_code"),
    )


def held(payload: Mapping[str, Any]) -> Holdings:
    """What each investment account holds, from a holdings answer read with every number a
    `Decimal` (`PlaidClient.holdings`). A holding naming an account or a security the answer
    does not list, or with no quantity, is refused, never read as holding nothing. Two
    holdings of one security in one account are one position, their units added together."""
    investments = tuple(
        _text(account, "account_id")
        for account in _list(payload, "accounts")
        if account.get("type") == "investment"
    )
    securities = {_text(s, "security_id"): s for s in _list(payload, "securities")}
    positions: dict[tuple[str, str], Position] = {}
    for raw in _list(payload, "holdings"):
        account_id, security_id = _text(raw, "account_id"), _text(raw, "security_id")
        security = securities.get(security_id)
        quantity = _decimal(raw, "quantity")
        if account_id not in investments or security is None or quantity is None:
            raise PlaidMalformedError("Plaid sent a holding it does not account for")
        value = _decimal(raw, "institution_value")
        cost = _decimal(raw, "cost_basis")
        known = positions.get((account_id, security_id))
        if known is not None:
            positions[account_id, security_id] = replace(
                known,
                quantity=known.quantity + quantity,
                value=_together(known.value, value),
                cost_basis=_together(known.cost_basis, cost),
            )
            continue
        positions[account_id, security_id] = Position(
            account_id=account_id,
            security_id=security_id,
            ticker=_optional_text(security, "ticker_symbol"),
            name=_optional_text(security, "name"),
            cash=is_cash(security),
            quantity=quantity,
            value=value,
            currency=_optional_text(raw, "iso_currency_code")
            or _optional_text(raw, "unofficial_currency_code"),
            cost_basis=cost,
        )
    return Holdings(investments, tuple(positions.values()))


def _together(one: Decimal | None, other: Decimal | None) -> Decimal | None:
    """Two holdings' figures as one position's: not known if either is not."""
    return None if one is None or other is None else one + other


def _transaction(raw: Mapping[str, Any]) -> Transaction:
    amount = _decimal(raw, "amount")
    day = _day(raw, "date")
    if amount is None or day is None:
        raise PlaidMalformedError("Plaid sent a transaction without an amount or a date")
    category = (
        _Payload(raw["personal_finance_category"])
        if isinstance(raw.get("personal_finance_category"), dict)
        else None
    )
    return Transaction(
        transaction_id=_text(raw, "transaction_id"),
        account_id=_text(raw, "account_id"),
        day=day,
        authorized_date=_day(raw, "authorized_date"),
        amount=amount,
        currency=_optional_text(raw, "iso_currency_code")
        or _optional_text(raw, "unofficial_currency_code"),
        name=_optional_text(raw, "name"),
        merchant_name=_optional_text(raw, "merchant_name"),
        original_description=_optional_text(raw, "original_description"),
        category=_optional_text(category, "detailed") if category is not None else None,
        pending=raw.get("pending") is True,
        pending_transaction_id=_optional_text(raw, "pending_transaction_id"),
    )
