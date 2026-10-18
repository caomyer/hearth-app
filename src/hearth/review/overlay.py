"""The ledger as its members have reviewed it.

Every transaction is in the ledger from the moment it is imported. One nobody has checked is
flagged `!` and balanced against the account the import proposed; the ledger file stays as the
import wrote it, and the rulings live in the app database. Each recompute lays the rulings
over the entries: a reviewed row becomes `*` against the account it was reviewed as, a
rejected row books nothing, and an unreviewed row stays exactly as imported, flag and all, so
every figure can say how much of it rests on rows nobody has looked at.
"""

from collections.abc import Iterable, Iterator, Mapping

from beancount.core.data import Directive, Transaction

from hearth.review.decision import Decision, State

REVIEWED_FLAG = "*"


def reviewed(
    entries: Iterable[Directive], decisions: Mapping[str, Decision]
) -> Iterator[Directive]:
    """`entries` with every decided review row as its decision makes it."""
    for entry in entries:
        decision = _decision(entry, decisions) if isinstance(entry, Transaction) else None
        if decision is None or decision.state is State.UNREVIEWED:
            yield entry
        elif decision.state is State.REVIEWED:
            assert isinstance(entry, Transaction)  # noqa: S101 - only transactions are decided
            yield _booked(entry, decision)
        # A rejected row is not a transaction, so nothing of it is yielded.


def _decision(entry: Transaction, decisions: Mapping[str, Decision]) -> Decision | None:
    if entry.flag != "!":
        return None
    review_id = entry.meta.get("review-id") if isinstance(entry.meta, dict) else None
    return decisions.get(review_id) if isinstance(review_id, str) else None


def _booked(entry: Transaction, decision: Decision) -> Transaction:
    """The row flagged reviewed, its proposal replaced by the account it was reviewed as.

    `read_pile` has already refused a review row that is not exactly the bank's posting and
    the proposal, so the second posting is the one a ruling decides.
    """
    bank, proposal = entry.postings
    account = decision.account or proposal.account
    return entry._replace(flag=REVIEWED_FLAG, postings=[bank, proposal._replace(account=account)])
