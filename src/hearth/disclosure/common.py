"""The household as disclosure sees it before any decision, and the view types pages share."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from hearth import figures
from hearth.accounts import Account
from hearth.books import Books
from hearth.disclosure.gate import Disclosed, Gate, Withheld
from hearth.figures import Part
from hearth.members import Member, MemberId
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
    def owner_names(self) -> str:
        names = [owner.display_name for owner in self.owners]
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

    @property
    def hue(self) -> str:
        return "who-joint" if self.is_joint else f"who-{self.owners[0].hue}"


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

    @property
    def leans(self) -> bool:
        """Whether the total is known to rest on unreviewed rows."""
        return self.amount != 0


def unreviewed(
    gate: Gate,
    parts: Sequence[Part],
    ref: Callable[[Part], PartRef],
    amount: Callable[[Part], Decimal],
    rows: Callable[[Part], int],
) -> Unreviewed:
    """What of a total over `parts` is unreviewed, as far as the viewer may know it.

    The caller has already decided the viewer may see each part's share of the total; this
    decides, part by part, whether they may also know how much of that share is unreviewed.
    """
    known = [part for part in parts if gate.may_see_review_state(part.owners)]
    return Unreviewed(
        amount=sum((amount(part) for part in known), Decimal(0)),
        rows=sum(rows(part) for part in known),
        unknown=tuple(ref(part) for part in parts if part not in known),
    )


@dataclass(frozen=True, slots=True)
class Worth:
    """A net worth figure, honest about what it leaves out.

    When any position has no price, `value` is the floor the priced positions add up to, and
    `unpriced` names what is missing in words the viewer may read.
    """

    value: Decimal
    unpriced: tuple[str, ...]

    @property
    def is_floor(self) -> bool:
        return bool(self.unpriced)


@dataclass(frozen=True, slots=True)
class Household:
    """Everything disclosure knows, before deciding anything for the viewer."""

    gate: Gate
    members: tuple[Member, ...]
    accounts: tuple[Account, ...]
    parts: tuple[Part, ...]
    ownership: OwnersOf
    books: Books

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
        )

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
        return Worth(mine.known_total, self.unpriced(mine, part))

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
            if not now.complete or not then.complete:
                return None
            return now.known_total - then.known_total

        return self.gate.disclose(Facet.NET_WORTH_TOTAL, part.owners, compute)

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
