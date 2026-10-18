# Serving the household's own ledger

`hearth demo` serves an invented household.
This is how to serve the real one, read-only, and what changes when the ledger is real.

## Point hearth at the ledger

Hearth reads one beancount file, named by `HEARTH_LEDGER`, and everything it includes.
For a ledger built by beancount_nextgen's statement import, that file is the run's `check.beancount`, not `ledger.beancount`:
`check.beancount` includes both the booked entries and `pending.beancount`, the rows the import set aside, which are what the review page lists.
Pointing at `ledger.beancount` alone leaves the review queue empty and every balance short by whatever is pending.

```sh
export HEARTH_LEDGER="$HOME/Finance/bcng-data/ledger/2026-01_07/check.beancount"
export HEARTH_DATA_DIR="$HOME/.local/share/hearth"   # the default; never inside the data root
```

`HEARTH_DATA_DIR` is hearth's own state: members, sessions, sharing choices, account ownership, prices and review rulings.
Keep it apart from the ledger's data root, and outside any cloud-synced folder.

## Say who owns each account

Nobody sees an account until it is claimed, so a freshly pointed hearth shows every member nothing.
Ownership lives in hearth's database, never in the ledger, because it decides who may see what.

```sh
uv run hearth add-member <username> --name "<name>" --admin   # the first admin
uv run hearth add-member <username> --name "<name>"           # each other member
uv run hearth accounts                                        # every Assets and Liabilities account, and its owners
uv run hearth add-account <ledger account> --name "<what the household calls it>" --owner <username>
uv run hearth add-account <joint ledger account> --name "<name>" --owner <username> --owner <username>
uv run hearth serve
```

`add-account` refuses an account the ledger does not open, a member who does not exist, and an account already claimed: a claim is never silently reassigned.
Each claim is recorded in the activity log against every owner.

## Read-only, and how that is kept

Hearth never writes to the ledger or the directory it lives in.
That includes a write beancount itself would make: its default loader keeps a `.<name>.picklecache` beside the ledger whenever a load takes over a second, and deletes one it finds stale.
Hearth loads through beancount's uncached path instead, and `tests/test_books.py` holds it to that.

## What a real ledger looks like on the pages

**The ledger stops before today.**
A statement import ends at its period's last day, and a bank feed stops when it last ran.
Figures are as of today all the same, and each account's data is known through its newest transaction or the day before its newest balance assertion.
A month a part's data has not reached reads as incomplete, by the rule the spending bars use (`figures.covers`), never as a month with nothing spent.
A past month is whole once an account has a transaction or a balance assertion dated in a later month, so a statement closing on Sep 30, imported with an Oct 1 assertion, leaves September whole.
The current month is whole only once an account has a transaction in it: an assertion dated this month says nothing about the rest of it, so until the next statement arrives this month reads as incomplete.
Only months decide this, never days.
Spending and income figures judge only the accounts that can move them, those with a posting beside an Expenses or Income account (`figures.flows_cover`): a retirement account that only takes transfers never leaves spending incomplete, while a quiet card does.
Net worth history, its monthly moves and the change since the 1st judge every account, so a quiet account holds them back until it moves or carries a balance assertion in a later month.
A move is only ever between consecutive month ends; a month missing from the middle reads as unknown, never folded into the next.
Net worth history leaves out month ends a part's data does not reach.
Each figure's as-of line describes the data behind that figure, not the ledger as a whole, because a total is only as current as its stalest part.
A viewer who may see the transactions of every part behind a figure is told the earliest last day once it is more than a week behind, naming the stalest member only when they may see that member's accounts.
Anyone else always reads "The accounts behind this figure end in <month>.", since the day an account last moved is transaction detail (`docs/privacy-boundary.md`).

A part reaches a month once any one of its accounts does.
The ledger cannot tell a card with no activity from a card that stopped reporting, so an account lagging behind a current one in the same part is not detected, and its months read as complete.
The remedy, for a Plaid-fed account, is to take coverage from the connection's last successful sync, which `hearth.plaid` records per Item (`last_synced_at`), independent of ledger entries.
Coverage does not read it yet; until it does, keep each account's statements or feed current, or close it with a balance assertion.

**The ledger starts part way through each account's life.**
An account opened years ago whose history in the ledger starts this January has no known value before then.
Net worth history and the changes over a month or a year are drawn only over months the ledger covers, by the same rule the spending bars use (`figures.covers`).
A card with no transactions in the first weeks of the history therefore holds its months back as incomplete, because the ledger cannot tell a quiet card from a missing statement.

**Some members own nothing alone.**
A member whose accounts are all joint is told to connect their own and pointed at the joint accounts on Net worth.
