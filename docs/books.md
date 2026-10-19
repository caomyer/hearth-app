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
Hearth's own entries go on top of it: an account the inherited ledger already opens is left to it, transactions and all, and an account a member maps to a new name is opened by hearth.
The import books nothing into an inherited account, since that would count every transaction the ledger already holds a second time; carrying an inherited account on from where its ledger stops is not done yet, and the import page says which accounts it leaves to the household's own records.
The books file includes the inherited ledger by its full path, so the file alone holds the whole books.

## What the books hold

Every transaction a connected bank has posted, from the day its history starts (`hearth.plaid.booking`).
Each recompute reads the feed afresh, so what is booked is always exactly what Plaid last sent: a row Plaid revises is booked as it now stands, and a row Plaid withdraws is not booked at all.
Running it again never books anything twice.

- A row is booked flagged `!`, against the account Plaid's category proposes (`INCOME_WAGES` is `Income:Wages`, `FOOD_AND_DRINK_COFFEE` is `Expenses:FoodAndDrink:Coffee`), and waits in Review (`docs/review.md`).
  A transfer, a loan payment, or a category Plaid could not decide proposes nothing, and balances against `Equity:Review:Unresolved` until a member says where it went.
- A payment from a bank account to a card the household also connects is booked once, as a transfer between the two, and marked `*`: both banks' own rows say it, and nothing in it is a guess (`docs/pairing.md`).
  A side of a card payment nothing pairs with balances against `Equity:Review:TransferPending`, never as spending or income.
- A pending row is not booked: it may still change or vanish, and its posted version would compete with it.
- An account reached through two connections sends every row twice.
  When older ones were removed, each connection stands for the account from the day its history starts, and every older one keeps only the days before the history of any connection linked after it starts, so removing a bank and connecting it again, however many times, books each transaction once.
  Two working connections are refused: the recompute says that one must be removed in Settings, Connections, and removing it is the way out.

Every category the entries name, and every category a member has reviewed a row as, opens beside the accounts, so the books file loads as it stands.

When the import books a transaction from a connection, its `source` metadata names the connection and the transaction, `plaid:<item id>:<transaction id>`, written by `connections.plaid_source`.
A transfer is one entry booked from two rows, so each of its postings carries its own row's `source` as well.
Coverage parses that string to tell which connection vouches for which entries (`docs/connections.md`), so it is written in exactly that form.

## Balances are not known yet

A bank sends a window of history, not the account's life, so the rows added up are what moved over the window, not what the account holds: read as a balance, a card owing thousands could show as owing nothing.
So an account hearth opened has no known balance until something in the books says what it held, a balance assertion or an opening balance against equity (`books.unanchored`).
Until then every net worth figure that includes it reads "Not known yet", never a total of zero, and the month-end line is not drawn; spending and income are figures as soon as their months are covered.
An account whose history has not arrived at all is the same, and its months read as incomplete too (`books.awaiting`).
A viewer who sees only a member's total learns that it is not known, never which account holds it back.
An inherited ledger's accounts keep beancount's meaning, where an account nothing has moved holds nothing.
Opening balances, derived from the balance each bank reports less the history it sent, are what will make those figures real.

## What Plaid does not send

An investment account opens like any other, but Plaid sends no investment transactions or holdings without its Investments product, which a connection has to have been granted and which is billed separately.
A brokerage connected without it has no buys, sells or dividends in the books.
Nor is investment history a household once brought in from downloaded statements, which hearth's own books do not read.
So books driven by Plaid alone hold no investment activity at all; the connect page says so for each such account rather than letting it read as empty.
