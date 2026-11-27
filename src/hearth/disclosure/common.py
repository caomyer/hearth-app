"""The household as disclosure sees it before any decision, and the view types pages share."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from beancount.core.data import Open

from hearth import figures
from hearth.accounts import Account
from hearth.books import Books
from hearth.disclosure.gate import Disclosed, Gate, Withheld
from hearth.figures import Part, Unplaced
from hearth.members import Member, MemberId
from hearth.months import Month
from hearth.reports.flows import OwnersOf
from hearth.sharing import Facet
from hearth.valuation.networth import Freshness, NetWorth

IDENTITY_HUES = 3
"""Members get one of three validated identity hues, in household order (see design/)."""


@dataclass(frozen=True, slots=True)
class MemberRef:
    username: str
    display_name: str
    hue: int

    @property
    def initial(self) -> str:
        return self.display_name[:1].upper()


@dataclass(frozen=True, slots=True)
class PartRef:
    """A member's sole accounts, or a joint group, as pages name it."""

    owners: tuple[MemberRef, ...]
    is_joint: bool
    is_viewers: bool

    @property
    def name(self) -> str:
        if self.is_joint:
            return "Joint accounts"
        return self.owners[0].display_name

    @property
    def in_prose(self) -> str:
        """The part's name inside a sentence or a list: "Avery", "the joint accounts"."""
        return "the joint accounts" if self.is_joint else self.owners[0].display_name

    @property
    def owner_names(self) -> str:
        names = [owner.display_name for owner in self.owners]
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

    @property
    def hue(self) -> str:
        return "who-joint" if self.is_joint else f"who-{self.owners[0].hue}"


@dataclass(frozen=True, slots=True)
class LeftOut:
    """A part of a figure that leaves out accounts hearth does not see, and how many.

    `count` is None when the viewer may not see the part's balances: the figure says it is
    without some of their accounts, the same words whether one is left out or five.
    """

    part: PartRef
    count: int | None

    def words(self, alone: bool = False) -> str:
        """ "2 accounts", "1 of Jordan's accounts", "some of the joint accounts".

        `alone` is for a figure that is this part's only, as a member's own page is, which
        needs no saying whose.
        """
        if alone:
            whose = ""
        elif self.part.is_joint:
            whose = "the joint "
        elif self.part.is_viewers:
            whose = "your "
        else:
            whose = f"{self.part.owners[0].display_name}\N{RIGHT SINGLE QUOTATION MARK}s "
        if self.count is None:
            return f"some of {whose}accounts" if whose else "some accounts"
        if whose:
            return f"{self.count} of {whose}accounts"
        return f"{self.count} {'account' if self.count == 1 else 'accounts'}"


def left_out(parts: Sequence[LeftOut], alone: bool = False) -> str:
    """What a figure leaves out, in words: "2 accounts hearth does not see", "some of Jordan's
    accounts hearth does not see". A figure of the viewer's own accounts alone says how many."""
    if not parts:
        return ""
    alone = alone or (len(parts) == 1 and parts[0].part.is_viewers and not parts[0].part.is_joint)
    words = [part.words(alone) for part in parts]
    joined = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    return f"{joined} hearth does not see"


@dataclass(frozen=True, slots=True)
class Unreviewed:
    """How much of a total rests on rows nobody has checked yet, at their suggested category.

    `amount` and `rows` count only parts whose review state the viewer may know (their own,
    or parts whose transactions are shared with them). `unknown` names the other parts in the
    total: theirs may rest on unreviewed rows too, and the page says so the same way whether
    or not it does, so nobody's pile is ever counted for someone else.
    """

    amount: Decimal
    rows: int
    unknown: tuple[PartRef, ...]
    unplaced: Unplaced = field(default_factory=Unplaced)
    """Of the same parts, the rows nothing says what they were yet (``books.uncategorised``),
    such as a new merchant's, moving money the total's way: they are in no total at all until
    a member places them, so the total says how many there are and how much they moved."""

    kept: Decimal = Decimal(0)
    """Of the parts in a spending total whose categories or transactions the viewer may see,
    what was booked to expense accounts that are not spending (``hearth.categories``), such as
    a retirement contribution: left out of the total, which says so, as it is part of what was
    left over instead. Which categories a part books to is its detail, so for any other part
    it is not said."""

    @property
    def leans(self) -> bool:
        """Whether the total is known to rest on unreviewed rows, at their suggested category."""
        return self.amount != 0

    @property
    def leaves_out(self) -> bool:
        """Whether the total is known to leave out rows nobody has said what they were yet."""
        return self.unplaced.rows > 0


def unreviewed(
    gate: Gate,
    parts: Sequence[Part],
    ref: Callable[[Part], PartRef],
    amount: Callable[[Part], Decimal],
    rows: Callable[[Part], int],
    unplaced: Callable[[Part], Unplaced] | None = None,
    kept: Callable[[Part], Decimal] | None = None,
) -> Unreviewed:
    """What of a total over `parts` is unreviewed, as far as the viewer may know it.

    The caller has already decided the viewer may see each part's share of the total; this
    decides, part by part, whether they may also know how much of that share is unreviewed.
    """
    known = [part for part in parts if gate.may_see_review_state(part.owners)]
    # Which categories a part books money to is its detail, so what it booked as not spending is
    # said only to whoever may see its categories or its transactions.
    detail = [
        part
        for part in parts
        if gate.may(Facet.CATEGORY_BREAKDOWN, part.owners)
        or gate.may(Facet.TRANSACTIONS, part.owners)
    ]
    placed = Unplaced()
    for part in known:
        placed += unplaced(part) if unplaced is not None else Unplaced()
    return Unreviewed(
        amount=sum((amount(part) for part in known), Decimal(0)),
        rows=sum(rows(part) for part in known),
        unknown=tuple(ref(part) for part in parts if part not in known),
        unplaced=placed,
        kept=sum((kept(part) for part in detail), Decimal(0)) if kept is not None else Decimal(0),
    )


@dataclass(frozen=True, slots=True)
class Worth:
    """A net worth figure, honest about what it leaves out.

    When any position has no price, `value` is the floor the priced positions add up to, and
    `unpriced` names what is missing in words the viewer may read.
    """

    value: Decimal
    unpriced: tuple[str, ...]
    awaiting: int | None = 0
    """Accounts in the figure whose balance the books do not hold yet (`books.balance_unknown`).
    While there are any, the figure is not known: `value` counts them as nothing, so it is
    never shown. None when there are some but the viewer may not see the part's balances,
    as how many would say how many accounts its owners have."""
    unseen: int | None = 0
    """Accounts in the figure that hearth does not see (`books.unseen`), whose balance it will
    never be told. They are not "not known yet", which would leave the figure unknown for
    good: `value` is what the rest add up to, and the figure says it is without them. None
    when there are some but the viewer may not see the part's balances, for the same reason
    as `awaiting`."""
    counted: bool = True
    """Whether any account in the figure is one hearth sees. When none is, `value` adds up
    nothing at all, which is not a figure, let alone one of zero."""

    @property
    def is_floor(self) -> bool:
        return bool(self.unpriced)

    @property
    def is_known(self) -> bool:
        return self.awaiting == 0 and self.counted

    @property
    def leaves_out(self) -> bool:
        """Whether the figure is without accounts hearth does not see."""
        return self.unseen != 0


@dataclass(frozen=True, slots=True)
class DataEnds:
    """Where the data behind a figure stops.

    A total is only as current as its stalest part, and a part only as current as its
    stalest connection (`figures.last_day`). The day an account's money last moved, and the
    day a connection stopped bringing it in, are transaction detail, so a viewer who may see
    the transactions of every part the figure adds up is given `day`, the earliest last day
    among them, and `stalest`, that part, when they may see its balances. Anyone else is given
    `month` alone: the earliest of the parts' last months, with `day` and `stalest` None, as
    nothing on that path may depend on a day.
    """

    month: Month
    day: date | None
    stalest: PartRef | None


@dataclass(frozen=True, slots=True)
class Household:
    """Everything disclosure knows, before deciding anything for the viewer."""

    gate: Gate
    members: tuple[Member, ...]
    accounts: tuple[Account, ...]
    parts: tuple[Part, ...]
    ownership: OwnersOf
    books: Books
    arriving: frozenset[str] = frozenset()
    """Accounts the household holds that the books have not caught up with: registered since
    they were last computed, as an account is the moment its connection is filed. The
    recompute that follows brings each in, so its balance is not known yet, never zero. One
    that nothing will ever bring in is not arriving but unseen (`books.unseen`)."""

    @classmethod
    def of(
        cls, gate: Gate, members: Sequence[Member], accounts: Sequence[Account], books: Books
    ) -> Household:
        return cls(
            gate=gate,
            members=tuple(members),
            accounts=tuple(accounts),
            parts=tuple(figures.parts(members, accounts)),
            ownership=figures.ownership(accounts),
            books=books,
            arriving=frozenset(
                {a.ledger_account for a in accounts}
                - {e.account for e in books.entries if isinstance(e, Open)}
                - books.unseen
            ),
        )

    @property
    def balance_unknown(self) -> frozenset[str]:
        """Every account the household names whose balance the books do not know yet: not
        those hearth does not see, which it never will (`unseen`)."""
        return (self.books.balance_unknown - self.books.unseen) | self.arriving

    @property
    def viewer(self) -> Member:
        return self.gate.viewer

    def ref(self, member: Member) -> MemberRef:
        index = next(i for i, m in enumerate(self.members) if m.id == member.id)
        return MemberRef(member.username, member.display_name, index % IDENTITY_HUES + 1)

    def ref_for(self, member_id: MemberId | None) -> MemberRef | None:
        member = next((m for m in self.members if m.id == member_id), None)
        return self.ref(member) if member else None

    def part_ref(self, part: Part) -> PartRef:
        owners = tuple(self.ref(m) for m in self.members if m.id in part.owners)
        return PartRef(owners, part.is_joint, self.viewer.id in part.owners)

    def solo(self, member: Member) -> Part:
        return next(p for p in self.parts if p.owners == frozenset({member.id}))

    def joint_parts_of(self, *members: Member) -> list[Part]:
        ids = {member.id for member in members}
        return [p for p in self.parts if p.is_joint and ids <= p.owners]

    def worth(self, valuation: NetWorth, part: Part) -> Worth:
        mine = figures.worth(valuation, part)
        awaiting: int | None = self.awaiting(part)
        unseen: int | None = self.unseen(part)
        if not self.gate.may(Facet.ACCOUNT_BALANCES, part.owners):
            awaiting = None if awaiting else 0
            unseen = None if unseen else 0
        return Worth(
            mine.known_total, self.unpriced(mine, part), awaiting, unseen, bool(self.seen(part))
        )

    def awaiting(self, part: Part) -> int:
        """How many of the part's accounts the books do not know the balance of yet. A count,
        never which: it says only that an account's history, or the balance it started from, is
        not in the books yet. The count is released only with the part's balances (`worth`)."""
        return sum(1 for a in part.accounts if a.ledger_account in self.balance_unknown)

    def unseen(self, part: Part) -> int:
        """How many of the part's accounts hearth does not see (`books.unseen`). A count, never
        which, released only with the part's balances (`worth`), as `awaiting` is."""
        return sum(1 for a in part.accounts if a.ledger_account in self.books.unseen)

    def seen(self, part: Part) -> tuple[Account, ...]:
        """The part's accounts hearth sees: every one but those it holds by name alone."""
        return tuple(a for a in part.accounts if a.ledger_account not in self.books.unseen)

    def spends(self, part: Part) -> tuple[Account, ...]:
        """The part's accounts that could show what it spent or earned: every one hearth sees
        but brokerages (`books.invested`), which Plaid sends no transactions for."""
        return tuple(a for a in self.seen(part) if a.ledger_account not in self.books.invested)

    def unpriced(self, valuation: NetWorth, part: Part) -> tuple[str, ...]:
        """Name the unpriced positions, or only count them when holdings are not shown."""
        missing = valuation.unpriced
        if not missing:
            return ()
        if self.gate.may(Facet.ACCOUNT_BALANCES, part.owners):
            return tuple(p.holding.commodity for p in missing)
        count = len(missing)
        return (f"{count} holding{'s' if count > 1 else ''} with no price",)

    def disclosed_worth(self, part: Part) -> Disclosed[Worth]:
        return self.gate.disclose(
            Facet.NET_WORTH_TOTAL, part.owners, lambda: self.worth(self.books.today, part)
        )

    def change_since(self, part: Part, valuation: NetWorth) -> Disclosed[Decimal | None]:
        """The change in the part's net worth since `valuation`'s day; None if either is a floor."""

        def compute() -> Decimal | None:
            now = figures.worth(self.books.today, part)
            then = figures.worth(valuation, part)
            if (
                not now.complete
                or not then.complete
                or self.awaiting(part)
                or not self.seen(part)
                or not self.reaches(part, valuation)
            ):
                return None
            return now.known_total - then.known_total

        return self.gate.disclose(Facet.NET_WORTH_TOTAL, part.owners, compute)

    def ends(self, parts: Sequence[Part]) -> DataEnds | None:
        """Where the data behind a figure over `parts` stops, if the page should say so.

        Only parts whose net worth the viewer may see count, as only they add up to a figure
        the viewer is shown. A viewer who may see the transactions of every one of them is
        told the day, once it is long enough ago to say so. Anyone else is told the month,
        always: deciding when, or naming whose, from the day would give the day away to
        whoever watches the note change.
        """
        shown = [part for part in parts if self.gate.may(Facet.NET_WORTH_TOTAL, part.owners)]
        lasts = [(day, part) for part in shown if (day := self._last_day(part)) is not None]
        if not lasts:
            return None
        if not all(self.gate.may(Facet.TRANSACTIONS, part.owners) for part in shown):
            return DataEnds(min(Month.of(day) for day, _ in lasts), None, None)
        day, part = min(lasts, key=lambda last: last[0])
        if not self.books.behind(day):
            return None
        named = len(shown) > 1 and self.gate.may(Facet.ACCOUNT_BALANCES, part.owners)
        return DataEnds(Month.of(day), day, self.part_ref(part) if named else None)

    def _last_day(self, part: Part) -> date | None:
        return figures.last_day(self.books, part.accounts)

    def reaches(self, part: Part, valuation: NetWorth) -> bool:
        """Whether the ledger's history of the part reaches back to `valuation`'s month.

        Before an account's history starts, the ledger holds nothing for it, so its value
        there is not zero but unknown: a part none of whose accounts had opened yet was not
        worth nothing then, so no change is measured from it. Otherwise it uses the rule the
        spending bars use, `figures.covers`.
        """
        opened = {c.account: c.opened for c in self.books.coverage}
        days = [opened[a.ledger_account] for a in self.seen(part) if a.ledger_account in opened]
        if days and min(days) > valuation.as_of:
            return False
        return figures.covers(self.books, part.accounts, Month.of(valuation.as_of))

    def history(self, parts: Sequence[Part]) -> list[NetWorth]:
        """Each month end `parts` can be valued at, then today.

        That leaves out month ends the ledger does not reach back to and every month end up
        to the last one at which a holding priced today had no price. Prices are looked up as
        of a day, so a month end after a security's first price is at worst stale, never
        unpriced. A price reported one day at a time, as an institution's is, has no past, and
        valuing those months at what the cash alone was worth would draw a rise that never
        happened. A month end from before a holding was bought is priced, but keeping it would
        join it straight to a later one across the months left out. A holding with no price
        today has had none at any month end, so every point leaves it out alike, each a
        labelled floor; it never holds back the months the other holdings can be valued at.
        """
        reached = self._reached(parts)
        today = self._holdings(parts, self.books.today, priced=True)
        unpriced = [
            i for i, v in enumerate(reached) if today & self._holdings(parts, v, priced=False)
        ]
        reached = reached[unpriced[-1] + 1 :] if unpriced else reached
        known = not any(self.awaiting(part) for part in parts)
        return [*reached, *([self.books.today] if known else [])]

    def priced_from(self, parts: Sequence[Part]) -> date | None:
        """The day `history` starts on when it leaves out month ends for want of prices."""
        history, reached = self.history(parts), self._reached(parts)
        if not history or history[: len(reached)] == reached:
            return None
        return history[0].as_of

    def _reached(self, parts: Sequence[Part]) -> list[NetWorth]:
        """Month ends after the first of `parts`' accounts opened, that the ledger reaches.

        None while an account's history is in the books without the balance it started from
        (`books.unanchored`): every month end would be what it moved since, not what it held.
        An account hearth does not see is never in a valuation (`books.unseen`), so the line
        is drawn through the accounts it sees, and says it is without the rest.
        """
        unknown = self.books.unanchored - self.books.unseen
        if any(a.ledger_account in unknown for p in parts for a in p.accounts):
            return []
        opened = {c.account: c.opened for c in self.books.coverage}
        days = [
            opened[a.ledger_account]
            for p in parts
            for a in self.seen(p)
            if a.ledger_account in opened
        ]
        return [
            valuation
            for valuation in self.books.month_ends
            if days
            and min(days) <= valuation.as_of
            and all(self.reaches(part, valuation) for part in parts)
        ]

    @staticmethod
    def _holdings(
        parts: Sequence[Part], valuation: NetWorth, *, priced: bool
    ) -> set[tuple[str, str]]:
        """Each (account, commodity) of `parts` that `valuation` holds with a price, or without."""
        return {
            (p.holding.account, p.holding.commodity)
            for part in parts
            for p in figures.worth(valuation, part).positions
            if (p.freshness is not Freshness.UNPRICED) is priced
        }

    @property
    def month_start_valuation(self) -> NetWorth:
        return self.books.month_ends[-1]

    @property
    def price_date(self) -> date | None:
        """The day of the newest price behind today's valuation, for the as-of line."""
        days = [p.quote.as_of for p in self.books.today.positions if p.quote is not None]
        return max(days, default=None)

    @property
    def computed_at(self) -> datetime:
        return self.books.computed_at


def is_withheld(value: object) -> bool:
    return isinstance(value, Withheld)


def stale_positions(valuation: NetWorth) -> int:
    return sum(1 for p in valuation.positions if p.freshness is Freshness.STALE)
