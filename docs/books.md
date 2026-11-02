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

Each confirmed account opens on the first day of the history its connection asked for, or on the earliest transaction Plaid sent for it if that is earlier, or on its opening balance, the day before that transaction, if that is earlier still.
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
Books that have not changed leave the file untouched, so a fava watching it reloads only when they do, and its modification time is when the books last changed, not when they were last recomputed.
`hearth serve` and a `hearth plaid` command run beside it may both write it, each through a temporary file of its own that replaces it whole.

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

## Opening balances

A bank sends a window of history, not the account's life, so the rows added up are what moved over the window, not what the account holds: read as a balance, a card owing thousands could show as owing nothing, and net worth would be the window's change rather than the balance.
With every sync the bank also reports what each account holds now, its current balance, which the feed keeps (`feed_balance`).
Each recompute works out from it what every connected account held before its history (`hearth.plaid.opening`):

```text
opening = current balance - every posted row booked for the account, in the books' sign
```

It is booked as an opening entry against `Equity:Opening-Balances` on the day before the account's first row, or, while no row has arrived, on the day the balance was reported.
The current balance itself is booked as a `balance` assertion on the day after the later of the day it was reported and the account's last row, as beancount reads an assertion as the balance at the start of its day.
A card's balance is what is owed, so on a Liabilities account it is asserted negated, and a card in credit holds money.
Both carry where they came from: `balance: "current"`, `reported-by` (the institution) and `reported-at` (when the sync recorded it); the opening's `source` is `plaid:<item id>:balance`.
Like everything else hearth derives, they are worked out afresh on every recompute, from the newest balance.

Plaid's current balance leaves pending transactions out, so the opening is worked out from posted rows alone, which are exactly the rows the books hold.
That was measured on a real household, not taken from Plaid's documentation: across five cards and two deposit accounts, the balance worked out this way reproduced every statement balance of the last months to the cent with pending rows left out, and missed each card's by exactly its pending total with them in.
Its two deposit accounts had nothing pending, so for them the data could not tell the two readings apart.

### The proof

The opening is worked out from the rows as the bank sent them; the books hold entries made of those rows by pairing, by a newer connection taking over from an older one, and by every ruling members make.
Every recompute checks each assertion against those entries, exactly, to the cent (`books.compute`), and beancount checks it again when the books file is opened.
Books that do not add up to what the bank says are wrong somewhere: the recompute keeps the previous books and says a balance check failed, through the same path as a statement's failed check (`docs/dashboards.md`).
So Review never offers what would make that happen from an ordinary click: an account whose balance its bank reports is not a target a row can be moved to, and a row its bank counts cannot be marked as not a transaction (`hearth.review.desk`); each is refused with a sentence saying why.
That holds too while the account's balance is not known only for a reason a later sync clears (`opening.PASSING`: no balance reported yet, an unfinished sync, another currency, a gap between connections, a brokerage that has not said what it holds yet), since a ruling made meanwhile would still be there when the assertion returns.
What is inside such an account is the bank's to say; a move between two connected accounts needs no ruling, as pairing books it.
A brokerage account is one of them: what it holds, cash included, is its brokerage's to say (see "What a brokerage holds", below).
An account kept by hand, which nothing asserts, still takes a moved row, and so does one whose balance is never its bank's to assert: an account of another kind, or one that more than one bank account feeds.
Should books still disagree, as with a ruling on record from before, the account's owners read the bank's figure, the books' figure and the difference, and anyone else reads only that the figures wait on one member's records.

### What stays not known

An opening that cannot be worked out is never guessed: the account's balance stays "Not known yet", counts towards "not known" in every total that includes it, and is never shown as zero or as a sum of its rows (`books.unanchored`).
Its owners read why on its page; anyone else who sees its balance reads only that it is not known (`access.may_see_why_unknown`).
The reasons (`opening.Unknown`):

- An investment account whose brokerage has not said what it holds yet: its balance is what its holdings are worth, which no transaction Plaid sends moves, so only its holdings can anchor it (see "What a brokerage holds", below).
  Nor does Plaid send it any transactions, so its spending is not waited on, and it is listed with investments.
- An account of a kind whose balance is not plainly money held or owed: only deposit, credit and loan accounts are worked out.
- More than one real bank account going to one account, as when a reissued card at another bank is filed into it: no one balance is its own.
- No balance reported by the bank.
- A sync that recorded the balance and then did not finish: the rows that balance counts may not all be in. The next sync that finishes puts it right.
- A balance in another currency than the account's rows.
- A bank connected again whose new history does not yet reach back to where the old connection stopped: the rows between them are missing until it does.

An inherited ledger's accounts keep beancount's meaning, where an account nothing has moved holds nothing; hearth works out no opening for them.

### Accounts hearth does not see

Money a member's connected accounts send to, or take from, an account no connection feeds is ordinary and often permanent: savings at a bank nobody connected, a car loan, money lent to a sibling, a bank Plaid cannot reach.
A member names such an account once, from the rows that moved money to or from it (`docs/review.md`, "Where did it go?"): a kind (a bank account, a card, a loan, money owed to them, cash), where it is, a name, its last digits if they like, its owners and its ledger name (`accounts.name_account`).
A loan or a card goes under Liabilities, everything else under Assets.
No balance is asked for: one typed today is wrong tomorrow, as hearth does not see what is spent from the account.

It is stored as an account like any other, with its owners, and marked as named (`named_accounts`).
Each recompute opens it with its name, institution and last digits, and `seen: "moves-only"`, on the earliest of the first row moved onto it and the household's first day of history (`ownbooks.Unseen`), and the rows members moved into it are booked against it.
Those rows are what moved, not what it holds: interest, and anything spent from it, never reach hearth.
So its balance is never read from them, and never asked for:

- `books.unseen` names every such account, and every claimed one nothing holds (below); a named one is `unanchored`, as its balance is not known, and none is `awaiting`, so no spending figure waits on history that will never arrive.
- No valuation holds a figure for it (`books.compute`), so no total can add it in by mistake.
- A total that includes one says it leaves it out: "$48,210 without 2 accounts hearth does not see", the known part, drawn with the dashed grey "not there yet" chip, never "not known yet", which is for a balance about to arrive and would make a member's net worth unknown for good.
  The history chart draws the accounts hearth sees, and says it is without the rest.
  How many it leaves out goes only to a viewer who may see the part's balances; anyone else reads "without some of Jordan's accounts".
- Its own page says its balance is not known, and what moved in and out of it, by its dates.
- An account that is never read as a balance is never read as $0.

Summing the moves into an account instead would make its balance the sum of what moved into it: in an invented household of a real one's shape, about −$118k for two years of card payments made from a checking account nobody connected, and net worth would fall by that, which is a lie.

An account the household claimed that nothing holds is the same fact through an older door: one claimed from a ledger hearth no longer inherits, which no connection feeds and nobody named.
Its balance is never told either, so it is in `books.unseen` and every total leaves it out and says so, exactly as for a named account (`ownbooks.Derived.unfed`).
It differs only in how it is held: nothing opens it, so no row can be moved into it, and its own page says so and offers no moves.
So connecting its bank later counts nothing twice, and the mapping page still offers it as where a connected account goes; once connected, it is opened and anchored like any other, and "without" drops it.

"Not known yet" is kept for an account about to arrive: a connected account whose balance is not in yet, or any account registered since the books were last computed, such as one whose connection has just been filed (`Household.arriving`), which the recompute that follows brings in, connected or unseen.

Connecting the bank later is not done yet for a named account: the mapping page does not offer a named account as where a connected account goes, since what members moved into it would then count a second time beside the bank's own rows.
What it will need is kept: every ruling that moved a row onto the account names it, and each row's review id leads back to the feed row it was booked from, with its day and amount (`RulingStore.ruled_onto`).

### Balances are not history

An opening balance says what an account held before its history, and an assertion what its bank says it holds now; neither says which days the history covers.
So coverage, how far an account reaches, which connection vouches for it, and the transactions a page lists all leave them out (`books.compute`, `figures`), and neither is ever money put in or taken out.
An account whose history has not arrived yet has its balance from the day it is filed, while its months still read as not known and spending is judged as though it might move (`books.awaiting`); its net worth line has today's point and no month end before it.
An account whose complete history holds no transaction at all is the same: its balance is known, its months are not, as nothing yet says a quiet account was quiet rather than missing.

## Checking against a ledger kept before

A household that kept a ledger before it connected its banks holds the best test hearth's books will get.
A ledger reconciled to the bank's statements says what each account held at each statement's close and what moved over each cycle, so where it and hearth's books disagree, hearth's books are wrong until shown otherwise.
`hearth compare` holds them side by side (`hearth.comparison`):

```sh
uv run hearth compare <ledger.beancount> --cycles <run.json>   # at each statement cycle the import reconciled
uv run hearth compare <ledger.beancount> --monthly             # at every month end both books reach
uv run hearth compare <ledger.beancount> --account Liabilities:US:Bank:Card   # refused unless hearth's books open it
```

`--cycles` takes a beancount_nextgen import's `run.json` and compares at the close of every statement cycle it reconciled to the cent, and the day before each opens.
Without it the comparison is monthly; `--monthly` adds month ends to the cycles.

For each account both books hold, and each checkpoint both reach, it says whether they agree, or by how much they differ, and lists the rows that make the difference: rows one book holds and the other does not, and rows the two books date on either side of a checkpoint.
A checkpoint whose whole difference is rows dated apart reads "agrees but for the day rows are dated": a ledger that books a card's charge on the day it was made, rather than the day it posted, differs from hearth that way across every month end.
Two rows are read as one row dated apart only when they are for the same amount, at most a week apart, their narrations or payees share a word that is not one any bank row carries ("sale", "payment" and the like), and neither has another such partner as near; anything else is a row one book holds and the other does not.
Balances are at the end of each day, in the ledger's sign.
Hearth books a row on the day the bank posted it, as a statement counts it, so a ledger row is read on its `post-date` where it carries one.
A ledger is read only over the days it holds every row of: a window built on the days rows were made holds rows posted after its last day, but not every row posted then, and lacks rows made before its first day and posted after it, so an account any of whose rows carries a `post-date` other than the day it was booked is read from a week after the later of its first booking and first posting, as a row kept from before the window, such as a reversal booked on the day of the charge it reverses, says nothing of the days before it.
Monthly checkpoints are chosen only within what both books hold; a statement cycle's checkpoint outside it is listed as not compared, with why.
Each account is read over its own rows, as each closes its statements on its own days, and over every statement cycle on it the import reconciled.
Where either book does not say what an account held, as a ledger that starts part way through a card's life does not, only what moved is compared, never a sum of rows as though it were a balance.

It reads and never writes.
The books are computed from a copy of the household's two databases in a throwaway directory, exactly as a recompute computes them but with no ledger to inherit, so the comparison is of hearth's own books even for a household that inherits the ledger it is compared with.
The ledger is read through the same read-only path as an inherited one, and the books file is not written.
What it finds is printed to the terminal and is the household's: keep it out of the repository, its tests and its documents, like every other figure of theirs.

## What Plaid does not send

An investment account opens like any other, but `/transactions/sync` sends it nothing: a brokerage's holdings and its trades come through Plaid's Investments product, a separate product from Transactions.
Investments is not something a connection lacks: an Item lists it among its available products when its institution offers it, and hearth can then ask for it with no new sign-in, though only when asked, and only once the household approves `holdings` (`docs/connections.md`, "Holdings").
Hearth asks for holdings, what each account holds and the price the brokerage gives each, and books what each account holds (see "What a brokerage holds", below), valued at those prices (`docs/valuation.md`).
It never asks for a brokerage's trades and dividends, which are a separate call: a brokerage connected through Plaid shows a value, and still has no buys, sells or dividends in the books.
Do not read the one as the other: the value says what the account holds, never how it came to hold it.
Nor is investment history a household once brought in from downloaded statements, which hearth's own books do not read.
So books driven by Plaid alone hold no investment activity at all; the connect page and the account's own page say so rather than letting it read as empty.
A position an inherited ledger holds is valued at the brokerage's price from the first day hearth asked for it.

### What a brokerage holds

A brokerage account is anchored as a bank account is (`hearth.plaid.opening`), at what its brokerage last said it holds rather than at its current balance, which is only what those holdings were worth at prices hearth keeps apart.
Every answer is kept in the feed with when it came (`Feed.holdings`), as Plaid keeps none of them and a day not kept is gone; the books hold the latest.
Its anchor is one entry dated the day the brokerage reported it, `Holdings as reported` against `Equity:Opening-Balances`, with an assertion of each security's units and of its cash the day after, carrying `balance: "holdings"`, who reported it and when.
Each security is a commodity named by its ticker, or, when it has none beancount can name it by (a trust fund that is not listed), by a name made from its Plaid security (`valuation.institution.commodity`); the books declare each with what it is called, unless an inherited ledger declares it already.
Units are decimals as Plaid sent them, never floats: fractional shares are ordinary.
Cash, and money as good as cash such as a money market fund, is held as money at what the brokerage says it is worth.
Any rows Plaid did send for the account are money, so its cash before them is worked out as a bank account's opening is, from the rows up to the day it was reported, and the assertion still proves the books add up to the report.
A row after that day is not in the report, so it is added on top of it, and today's balance shows it.
A brokerage that says the account holds nothing anchors it at nothing, a zero somebody established; one that has not answered yet leaves it not known (`Unknown.INVESTMENT`), never empty.

The anchor claims nothing about any earlier day.
Plaid gives holdings and prices for today only and no trades, so the books hold nothing in the account before the day it was reported, and no month that ended earlier is whole for its value (`books.positioned`, `figures.covers`).
Net worth is therefore a total from that day, and no history is drawn through a part that holds a brokerage: an earlier month end would value it as empty, or as its cash alone, which nobody said.
That is a question of value alone: what its rows say was spent or earned is judged by its history, as any account's is (`figures.flows_cover`).
A member whose accounts are all brokerages, or brokerages and accounts hearth does not see, has no spending or income anyone established, so those read as not known on their page and add nothing to the household's for anyone who may see that member's balances; to anyone else they read as not known, since what kind an account is goes with its balance (`docs/privacy-boundary.md`).
