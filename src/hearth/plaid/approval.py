"""Which live Plaid calls this hearth may make. None, unless someone approved them by name.

A live call reaches a real institution through a real Plaid account, and some of them spend
something that cannot be had back: creating an Item takes one of the account's few
connections. So approval is not a comment or a convention. Every call the client makes names
the operation it belongs to, and the client refuses before any byte leaves this machine
unless that operation was approved when hearth was started (``hearth serve --approve-plaid``,
``hearth plaid sync --approved``). The default approves nothing.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Operation(StrEnum):
    LINK = "link"
    """Opening Plaid Link and exchanging what it returns: this creates Items, which use slots."""

    READ = "read"
    """Reading an existing Item: its institution, its accounts and balances, its transactions."""

    REMOVE = "remove"
    """Removing an Item at Plaid, which ends the connection and frees its slot."""

    HOLDINGS = "holdings"
    """Reading a brokerage's holdings and the price it gives each. Not a `READ`: the first ask
    adds Plaid's Investments product to an Item that only offered it, which Plaid then bills
    for that Item from then on, and no later call takes it off again."""

    ACTIVITY = "activity"
    """Reading a brokerage's investment activity: its trades, dividends, contributions, fees
    and transfers. Not `HOLDINGS`: the first ask adds Plaid's Investments Transactions
    subscription to the Item, billed apart from holdings for as long as the Item lasts."""


DESCRIPTIONS = {
    Operation.LINK: "connect a bank (creates a Plaid Item, which uses one connection)",
    Operation.READ: "read connected banks (accounts, balances and transactions)",
    Operation.REMOVE: "remove a connection at Plaid (frees its slot)",
    Operation.HOLDINGS: "read connected brokerages' holdings and today's prices"
    " (adds Plaid's Investments product to each brokerage's Item)",
    Operation.ACTIVITY: "read connected brokerages' trades, dividends and other activity"
    " (adds Plaid's Investments Transactions subscription to each brokerage's Item)",
}


class ApprovalRequired(RuntimeError):  # noqa: N818 - it names an outcome, as bcng's does
    """A live Plaid call was attempted without approval for its operation. Nothing was sent."""


@dataclass(frozen=True, slots=True)
class Approval:
    """The operations approved for this run of hearth."""

    operations: frozenset[Operation] = frozenset()

    @classmethod
    def of(cls, operations: Iterable[Operation | str]) -> Approval:
        return cls(frozenset(Operation(operation) for operation in operations))

    def allows(self, operation: Operation) -> bool:
        return operation in self.operations

    def require(self, operation: Operation, what: str) -> None:
        if operation not in self.operations:
            raise ApprovalRequired(
                f"{what} is a live Plaid call ({operation.value}), and this hearth was not"
                f" started with approval for it; nothing was sent"
            )


NOTHING_APPROVED = Approval()
