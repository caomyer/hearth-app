# Inheriting the household's own ledger

Hearth keeps the books itself, and a household with no ledger needs nothing here (`docs/books.md`).
A household that already has a beancount ledger may inherit it instead: hearth reads it, read-only, and lays its own entries over it.
This is how, and what changes when the ledger is real.

## Point hearth at the ledger

Hearth inherits one beancount file, named by `HEARTH_LEDGER`, and everything it includes.
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

Nobody sees an inherited account until it is claimed, so a freshly pointed hearth shows every member nothing.
Ownership lives in hearth's database, never in the ledger, because it decides who may see what.

```sh
uv run hearth add-member <username> --name "<name>" --admin   # the first admin
uv run hearth add-member <username> --name "<name>"           # each other member
uv run hearth accounts                                        # every Assets and Liabilities account in the books, and its owners
uv run hearth add-account <ledger account> --name "<what the household calls it>" --owner <username>
uv run hearth add-account <joint ledger account> --name "<name>" --owner <username> --owner <username>
uv run hearth serve
```

`add-account` refuses an account the books do not open, a member who does not exist, and an account already claimed: a claim is never silently reassigned.
An account a member connects through Plaid instead can be filed into a new ledger account on the mapping page, which records the same name (`docs/connections.md`).
Each claim is recorded in the activity log against every owner.

## Read-only, and how that is kept

Hearth never writes to the inherited ledger or the directory it lives in; what it writes is its own books file, in its data directory, which includes the ledger by path (`docs/books.md`).
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

A part reaches a month once any one of its accounts is known to.
The ledger cannot tell a card with no activity from a card that stopped reporting, so on its own it would let one current account make every account in its part look current.
A connection can tell them apart, but only for data it brought into the books.
An entry booked from a connection carries `source: "plaid:<item_id>:<transaction id>"` (`connections.plaid_source`), and a connection's last successful sync, as `hearth.plaid` records per Item (`last_synced_at`), vouches for an account only when the books hold such an entry from that Item on the account and the sync is on or after the newest of them (`books.vouched`).
A vouched account is known through its last sync, which counts toward its part's reach, and it must also be current through the month itself: by its own entries, or by its connection having last synced after the month (in it, for this month).
A vouched account's own entries are its statement, manual and own-connection entries, never another connection's (`books.account_reaches`, `books.account_ends`): a card payment booked through the checking account's feed proves one transaction on the card, not that the card's own feed is complete, because presence is not completeness.
A card whose connection last worked in May therefore leaves May and every month after it incomplete however current the rest of its part is, and its part's data stops where the connection did.
A quiet card whose connection still works holds its months, so cards with no activity for months never hold their member's figures back.
The books note each connection's last successful sync when they are recomputed (`books.synced_through`); a sync recomputes them when it finishes, from a page or the morning's run, so the figures see it at once.
Like everything else here, this is decided by month alone and is the same for every viewer.

Today no entry in a real ledger derives from a connection, so no connection vouches for anything there, and a Plaid-fed account whose ledger feed has stopped while its connection keeps working still reads as covered by the ledger rule below.
The cause is that connected data has not reached the books yet: it waits in the feed store until the import exists (`docs/connections.md`).
The rule that closes it is in place and activates on its own once the import books rows with the `plaid:<item_id>:...` source; the demo books its bank-feed rows that way already, so it shows the rule at work.

An account no connection vouches for, such as one a statement import brings in, is judged with its part by the ledger alone, and that limitation stays: one that stopped while another in its part carries on reads as complete.
Judging each such account on its own entries would be worse, as every quiet card would hold its member's typical, pace and history back for good.
The honest answer for a statement-fed account is a balance assertion at each statement's close, which says the account is whole through it whether or not anything moved; until the import writes one, keep each account's statements current, or close it with a balance assertion.

**The ledger starts part way through each account's life.**
An account opened years ago whose history in the ledger starts this January has no known value before then.
Net worth history and the changes over a month or a year are drawn only over months the ledger covers, by the same rule the spending bars use (`figures.covers`).
A card with no transactions in the first weeks of the history therefore holds its months back as incomplete, because the ledger cannot tell a quiet card from a missing statement.

**Some members own nothing alone.**
A member whose accounts are all joint is told to connect their own and pointed at the joint accounts on Net worth.
