# Hearth's own books

Most households start with a bank login and nothing else: no ledger, and no idea what `Liabilities:US:Bank:Card` means.
Hearth keeps the books for them, and a household that has a beancount ledger of its own may inherit it instead of starting from nothing.

## From nothing to books

1. An admin adds the member (`hearth add-member`).
2. The member connects a bank, and Plaid returns its accounts (`docs/connections.md`).
3. On the mapping page the member confirms where each account goes: a ledger name hearth proposes, which they may edit, and a name for the household.
   Those accounts are the household's chart of accounts.
4. Hearth's books open one account for each confirmed one (`hearth.ownbooks`).

No step asks for a file, and none needs beancount knowledge.
A household that has connected nothing yet sees a page that says so and offers to connect a bank, never an error and never a total of zero.

## Derived, never stored twice

Hearth derives its entries in memory on every recompute, from the app database and the feed store, as it lays review rulings over the ledger (`docs/review.md`).
What Plaid sent lives in the feed store; what members decided lives in the app database; nothing is stored twice, so nothing can drift.

Each confirmed account opens on the first day of the history its connection asked for, or on the earliest transaction Plaid sent for it if that is earlier.
An account fed by more than one connection, as when a bank is connected again, opens once, at the earliest of them.
Who owns an account is never written into the books: ownership decides who may see what, so it stays in the app database (`docs/foundation-decisions.md`).

Hearth's entries are checked as beancount checks a file it loads, account names included.
Books that would not load cleanly are refused, and the pages say the figures could not be computed, rather than serve figures from them.

## The books file

Every recompute that succeeds writes the books to `books.beancount` in the data directory, readable by its owner alone.
It is there so that every figure traces to an entry that exists: open it in fava, query it with `bean-query`, diff it, or check it by hand against a statement.

```sh
uvx fava "$HEARTH_DATA_DIR/books.beancount"
```

The file is an output, never an input.
Hearth never reads it back, and its first lines say so.
An edit made to it changes nothing hearth shows and would be lost at the next recompute, so before writing it again hearth checks whether the file is still exactly as it wrote it.
A file someone edited is renamed beside it, to `books.edited-<when>.beancount`, and never discarded.
Books that have not changed leave the file untouched, so a fava watching it reloads only when they do.

## Inheriting a ledger

A household with a ledger of its own sets `HEARTH_LEDGER` to it (`docs/real-ledger.md`).
Hearth reads it and never writes it, or anything beside it.
Hearth's own entries go on top of it: an account the inherited ledger already opens is left to it, and an account a member maps to a new name is opened by hearth.
The books file includes the inherited ledger by its full path, so the file alone holds the whole books.

## What the books hold so far

Accounts only.
Transactions are not booked yet.
A payment from a bank account to a card arrives from both sides, and booking both would count it twice, inflating spending and income together; the import books each such pair once, as a transfer, and each side nothing pairs with against `Equity:Review:TransferPending` (`docs/pairing.md`).
Until they are, an account hearth opened has nothing in the books but its `open`, so its balance is not known there, whatever its bank says.
Every figure that includes such an account (`books.awaiting`) reads "Not known yet", never a total of zero: the account reaches no month, so spending and income read as incomplete too, and the month-end line is not drawn.
A viewer who sees only a member's total learns that it is not known, never which account holds it back.
An inherited ledger's accounts keep beancount's meaning, where an account nothing has moved holds nothing.
Opening balances, derived from the balance each bank reports less the history it sent, are what make those figures real.

When the import books a transaction from a connection, its `source` metadata names the connection and the transaction, `plaid:<item id>:<transaction id>`.
Coverage parses that string to tell which connection vouches for which entries (`docs/connections.md`), so it is written in exactly that form.

An investment account opens like any other, but Plaid sends no investment transactions or holdings without its Investments product, so a brokerage connected this way has no activity in the books.
