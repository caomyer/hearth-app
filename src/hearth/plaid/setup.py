"""How this run of hearth reaches Plaid: which calls are approved, and over what.

`hearth serve` reaches the real API over HTTPS and approves nothing unless started with
``--approve-plaid``. The demo and the tests add hearth's stand-in (``hearth.plaid.fake``),
which answers only the ``demo`` environment, in-process. A call to the stand-in is not a
live call, so it needs no approval; a call to Plaid always does, even from the demo.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from hearth.plaid.approval import NOTHING_APPROVED, Approval, Operation
from hearth.plaid.client import Credentials, Environment, PlaidClient, Transport, https_transport
from hearth.plaid.fake import FakePlaid
from hearth.vault.keychain import KeychainKeyStore, KeyStore
from hearth.vault.secret import Secret

DAYS_REQUESTED = 730
"""Two years: this year and last in full. Fixed when an Item is linked; it cannot be widened
later without linking again, which costs another of the Plaid account's connections."""

STAND_IN_APPROVAL = Approval.of(Operation)
"""The stand-in reaches nobody's bank, so every operation on it is approved."""


@dataclass(frozen=True, slots=True)
class PlaidSetup:
    approval: Approval = NOTHING_APPROVED
    transport: Transport = https_transport
    stand_in: FakePlaid | None = None
    keys: Callable[[], KeyStore] = field(default=KeychainKeyStore)
    """The vault's master keys; the Keychain, opened only when a secret is first needed."""
    wait: Callable[[float], None] = field(default=time.sleep)
    """Waits so many seconds between asking Plaid again for history still arriving."""

    @property
    def environments(self) -> tuple[Environment, ...]:
        """Where a Plaid account may be: the stand-in's own environment only when it runs."""
        real = (Environment.PRODUCTION, Environment.SANDBOX)
        return (Environment.DEMO, *real) if self.stand_in is not None else real

    def approval_for(self, environment: Environment) -> Approval:
        return STAND_IN_APPROVAL if environment is Environment.DEMO else self.approval

    def allows(self, environment: Environment, *operations: Operation) -> bool:
        approval = self.approval_for(environment)
        return all(approval.allows(operation) for operation in operations)

    def signed_in_again(self, environment: Environment, public_token: Secret | None) -> None:
        """Tell the stand-in a member signed in again. Plaid itself needs no call for this: an
        update-mode sign-in fixes the Item at Plaid's end."""
        if environment is Environment.DEMO and self.stand_in is not None and public_token:
            self.stand_in.signed_in_again(public_token.reveal())

    def client(self, credentials: Credentials) -> PlaidClient:
        if credentials.environment is Environment.DEMO:
            if self.stand_in is None:
                raise ValueError("the demo environment is served only by the demo")
            return PlaidClient(credentials, STAND_IN_APPROVAL, self.stand_in)
        return PlaidClient(credentials, self.approval, self.transport)
