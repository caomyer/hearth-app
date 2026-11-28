# Bringing in the household's own ledger

Hearth keeps the books itself, and a household with no ledger needs nothing here (`docs/books.md`).
A household that already has a beancount ledger imports it once: hearth keeps a frozen copy, its base, and lays its own entries over it.
This is how, and what changes when the ledger is real.

## Import the ledger

```sh
uv run hearth import-ledger "$HOME/Finance/ledger/main.beancount"
```

Name the file the ledger is opened from, the one fava is pointed at.
For a ledger built by beancount_nextgen's statement import, that is the run's `check.beancount`, not `ledger.beancount`:
`check.beancount` includes both the booked entries and `pending.beancount`, the rows the import set aside, which are what the review page lists.
Importing `ledger.beancount` alone leaves the review queue empty and every balance short by whatever is pending.

The import reads the ledger where it is, through the same path every ledger is read by (`books.load_ledger`), and writes nothing unless it loads cleanly and every balance assertion in it passes.
If it does not, it says why, by file and line and never by amount, and nothing is written.
Each file the ledger reads is read once, and what is loaded is a copy of those bytes, never the files again.
Before any include is followed, every file is held to what a base may hold: a regular file, never a folder, a pipe or a device; one iCloud has downloaded; at most 1,000 files and 64 MB in all, and no line over 64 KB, so that the slowest file within those caps is read in seconds; never named by an absolute path, nor by one starting with `~`, which beancount reads as a folder named `~` beside the file and not the home folder; never by an include that matches nothing, nor by one that climbs above the folder holding every file the ledger reads, even to come back into it; and nothing hearth keeps in its data directory, by whatever path, link or hard link.
The copy is loaded from inside the data directory, so an include that could leave the copy would find hearth's own books there: its shape alone keeps it inside, and the copy's includes are checked again before it is loaded.
These are changes: before them, a ledger inside `HEARTH_DATA_DIR` was imported, a pipe a glob found made the import wait for ever, and an include starting with `~` was called an absolute path.
Every import holds the base's lock from asking whether the household may import through naming the new base, so two at once, from two shells or the app and a shell, take turns; one that waits a minute on another gives up, in a sentence, and a data directory on a file system that cannot lock refuses to import at all.
It then prints the check to hold against the ledger's own figures, in fava or by hand, before trusting the copy:

```text
Imported /Users/.../main.beancount, 5 files, as hearth's base.
  Entries: 20, from Jan 1, 2026 to Jul 2, 2026
  Accounts: 6
  Balance assertions: 4, all of which pass
  Net worth today: 3,054.50 USD on Nov 22, 2026, at the ledger's own prices
  Beancount's own plugins it runs: beancount.plugins.implicit_prices
Kept but not used, 5 lines:
  main.beancount, line 5: plugin "zq_meddler", which is not beancount's own, so hearth never runs it
  main.beancount, line 9: custom "fava-option"
  accounts.beancount, line 1: plugin "beancount.plugins.auto_accounts", in an included file, where beancount itself does not read plugins
  years/2026-1.beancount, line 17: custom "budget"
  years/2026-1.beancount, line 18: document: hearth neither copies nor opens the file it names
The base is in .../ledger-base/358a7b82..., digest 358a7b82....
Hearth reads the base from now on, never the original, which it did not change.
```

Entries are counted as hearth uses them: after beancount's own plugins run, `custom` directives left out.
Net worth is every Assets and Liabilities account today, valued in the ledger's first operating currency at the newest `price` the ledger holds for each commodity; a holding it has no price for is named, and the total is then only a floor.
Hearth's own pages value holdings at the prices it stores (`docs/valuation.md`), so they may differ from this figure by exactly the prices.

### What hearth runs, and what it keeps but does not use

Beancount's own plugins, the modules in `beancount.plugins` and `beancount.ops` that a ledger names, run as beancount runs them.
Any other plugin is kept in the ledger's text and never run, nor imported: it is code hearth did not ship, which could read, write or send anything, and would make the books depend on what happens to be installed beside hearth (`books.runs`).
A ledger's `insert_pythonpath` is never honoured either, so no code from beside a ledger is ever imported.
A ledger that loads only because a plugin of its own runs, one that makes entries, say, does not load in hearth, and the import says where.

`custom` directives are kept and ignored.
Nor does hearth look for documents: beancount's `documents` plugin is not run, so a `document` directive is kept but its file is neither copied nor checked, and an `option "documents"` folder is never searched.
A plugin named in an included file is listed too, because beancount reads plugins only in the file a ledger is opened from.
The rule is applied in the one place a ledger is loaded, uncached, so an inherited ledger and a base are read exactly alike.

### The base

```text
<data dir>/
  ledger-base/
    manifest.json              what was imported, from where, when, and its digest
    <digest>/                  the files as they were, byte for byte
      main.beancount           each at its path relative to the folder that held them all
      accounts.beancount
      years/2026-1.beancount
```

The base is every file the ledger read, includes resolved, copied byte for byte, each at its path relative to the nearest folder that held all of them (`hearth.base`).
So an include relative to the file that has it, a nested include, an include reaching into a sibling folder with `..`, and a glob all resolve in the copy exactly as they did in the original, and a glob finds there only the files it found at the import.
An include by an absolute path would still lead out of the copy, back to the original, so the import refuses it and asks for a relative one; every load of the base checks it reads no file outside it.

The digest is the sha256 of the schema, the path of the file the ledger is opened from, and each file's path, length and bytes in order of path (`base.digest`).
Any byte of any file, any file's path, or which file is opened changes it.
Every recompute checks the files against the digest before reading them, so a base changed on disk is refused, and the pages say the figures could not be computed, rather than read it.

`manifest.json` names the base: its digest, its files with their sizes and sha256, where it was imported from, when, and the newest review ruling at the time.
It is written last and whole, so a recompute finds either the old base or the new one, never half of either; a serving hearth notices an import made by another process within seconds, as `books.standing` includes the digest.

`books.beancount`, beside it in the data directory, is something else: an output hearth writes on every recompute and never reads (`docs/books.md`).
It includes the base by its full path, so the file alone still holds the whole books.

### Importing again

Importing again, the same ledger after a correction say, replaces the base until a member makes a review ruling after the import, or on a row carried on from it.
The base it replaces stays in its own folder, never discarded, and the manifest names the new one.
From the first ruling made after the import on, rulings rest on the base as it is, so importing again is refused with a sentence saying so.
So it is once a member has ruled on a row hearth carries on from the base, or on a reconciling entry (`books.begun`): that row begins at a cut-over the base decides, and another base would move it under the ruling.
Where matched accounts are carried on from the base being imported, the check says from which day each is, and how many of the bank's transactions before then the base does not hold, which hearth never brings in.
Importing a ledger exactly as the base already holds it writes nothing.

### From the Mac app

The Mac app brings a ledger in, and matches accounts to it, through the very functions the two commands call (`docs/native.md`, "The household's ledger"):

- `ledger_import.examine` reads a ledger where it is and works out its check, writing nothing; `hearth import-ledger`, the app's check (`check_ledger`) and the app's import all go through it, so they refuse alike, in the same sentences.
- `matching.weigh` is the survey with what it was worked out from, `matching.refusal` tries one match against it without writing, and `matching.confirm` writes only what that same check allows, recording which member confirmed it when a member did.

Where the app's path differs, it is narrower:

- Only an admin checks a file or brings a ledger in (`access.may_bring_in_ledger`), as the base becomes every member's books; the command line is whoever runs hearth on its machine.
- The app brings a ledger in once: it refuses where a base is in already, saying so, rather than replace it as the command line may until a ruling rests on it.
- A household that holds a base or inherits a ledger is told why it may not bring one in, and nothing of any file, as its own ledger, read again, holds every member's accounts.
- Nothing in hearth's own data directory, its books or the base it froze, is ever read as a ledger to import, by either path.
- Every file a ledger reads is read once and held to what a base may hold, as above (`ledger_import._gathered`), and the check loads a private copy of those bytes.
- A member matches their own connections alone, onto accounts they own, as the Connect page files them; the command line matches every member's.
  An account hearth keeps itself may move onto a base account nobody has claimed, its claim going with it, only where the ledger records that it is that bank account (`matching.recorded`, `access.may_carry_on`); see "An account hearth kept itself" below.
- An admin says whose each of the base's other Assets and Liabilities accounts is, right after bringing the ledger in or later from Settings, Ledger, through the very path `hearth add-account` claims one by (`accounts.claim_all`), every account said in one act or none of them, and only an account nobody has claimed: one claimed since it was shown refuses the act, and nothing is ever given another owner (`access.may_assign_owners`).
  An account is one member's or several's, joint; one left for later stays nobody's.
  The household sees it by its name in the ledger without its kind, each part a word (`disclosure.ledger.household_name`); the command line takes the name its operator gives.

### Inheriting the ledger where it is, for now

Until the import is all a household needs, hearth can still inherit a ledger where it is, named by `HEARTH_LEDGER`, and everything it includes:

```sh
export HEARTH_LEDGER="$HOME/Finance/bcng-data/ledger/2026-01_07/check.beancount"
```

It is read exactly as a base is, by the same rules, so a household computes the same books from either.
A data directory with a base and `HEARTH_LEDGER` set as well is refused: the recompute says to unset `HEARTH_LEDGER`, and `hearth import-ledger` refuses to run while it is set.

`HEARTH_DATA_DIR` is hearth's own state: members, sessions, sharing choices, account ownership, prices, review rulings and the base.
Keep it apart from the ledger's own folder, and outside any cloud-synced folder.

```sh
export HEARTH_DATA_DIR="$HOME/.local/share/hearth"   # the default; never inside the ledger's folder
```

## Say who owns each account

Nobody sees an account the ledger opens until it is claimed, so a freshly imported ledger shows every member nothing.
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

## Say which of the base's accounts each connected account is

A household that imported its ledger and connects its banks holds each bank account twice: in the base, as the ledger kept it, and in the feed, as Plaid sends it.
Before hearth can carry an account on from where the base stops, it must know which of the base's accounts each connected one is.
Nobody has to say what the ledger already says, so hearth proposes it from the ledger itself, and an admin confirms it once:

```sh
uv run hearth match-accounts                                # every connected account, what the base says it is, and why
uv run hearth match-accounts --confirm <plaid account>      # match it to the account proposed for it
uv run hearth match-accounts --confirm <plaid account>=<ledger account>   # or to the base's account named
```

It makes no live call and reads every member's connections, as `hearth plaid items` does: it is the machine's operator's view, not a page.
What it prints is counts, days and the names of accounts, never a payee or an amount.

### What hearth proposes (`hearth.matching`)

**By id.**
A ledger fed from hearth carries each bank transaction's Plaid transaction id, in one of two shapes, and both are read:
hearth's own `source: "plaid:<item id>:<transaction id>"` (`connections.plaid_source`), and a bare `plaid-id: "<transaction id>"` that an importer of the household's own writes, with no Item.
A base entry holding one of a connected account's feed ids is that row.
The bank's own side of it is the posting that carries the id itself, as each side of a transfer hearth booked does, or else the entry's Assets or Liabilities posting of the row's amount in the sign the bank means it, Plaid's sign being money leaving the account.
So a card payment's entry, which holds the card's posting and the paying account's of opposite signs, puts the paying account's row on the paying account and never on the card.
The base account a strict majority of the placed ids land on is proposed, saying how many agree and how many land elsewhere; ids split with no majority propose nothing.

**By content, when none of its ids is in the base**, as for a bank connected again, which Plaid gives new ids, or a base kept from statements.
Each account the base opens on the connected account's side, a depository account under Assets and a card or a loan under Liabilities, is held against the feed's posted rows: the most rows the two hold one to one, for the same amount, at most a week apart, on the day each posted (`post-date` where the base says it).
The best is proposed only when it clearly beats the rest, which is three things at once:

- it holds at least 5 of the rows;
- it holds at least twice as many as the runner-up, so two accounts of near-identical activity propose neither;
- it holds at least half of the rows that fall within a week of its own first and last rows, so an account that is not the bank's, holding a few rows of the same amounts by chance, is not proposed for want of a better one.

A brokerage account, which Plaid sends no transactions, and an account of any type other than depository, credit and loan, are not matched by content.

Content is evidence, not proof, and carrying holds a row by content by the same rule (`docs/books.md`, "Carried on from the base").
A frequent charge of one amount can be held by the wrong entry: the base's entry for a charge the bank never posted, within a week of one it did, holds the bank's row, which is then not booked.
That is never silent: the books are short of exactly that row, so the account's difference with its bank, in Review, is its amount, until a member confirms it or puts the cause right.

**Otherwise nothing is proposed**, and the household names the base's account itself, or files the connected account as a new one on the Connect page.

### What a match is

A confirmed match is the mapping a member confirms on the Connect page, recorded in the same place (`plaid_accounts`, `Registry.confirm`), with no member named as having confirmed it, as a claim made on the command line names none.
From then on hearth carries the account on from the base: its history before its cut-over is the base's, and hearth books the bank's rows from then on that the base does not hold (`docs/books.md`, "Carried on from the base").
A match may be changed until carrying begins, once a member has ruled on a row carried on the account or on its reconciling entry (`books.begun`); until then a wrong match is put right with another `--confirm`, and its rows move with it at the next recompute, since they are derived afresh each time.
After that the command refuses, saying a decision has been made on its transactions, and so does the Connect page.

A match never changes who owns an account:

- the base's account must be claimed already (`hearth add-account`), and its owners must include the member who holds the connection, as the Connect page asks it of the gate (`access.may_feed`);
- an account claimed by members the holder is not among is never proposed to their connection, however strong the evidence, and the command says where the evidence points and why it is not proposed;
- an account another connected account is matched to is not matched again, as one ledger account is fed by one connection at most;
- an account the base closes is not matched, as nothing can be carried on into it.

### An account hearth kept itself while the base held its rows too

A household that connected its bank before importing its ledger may have filed a connected account as an account hearth keeps itself, whose rows the base also holds.
Matching the base's account as well would count that bank account twice, so it is moved onto the base's account instead, with the same `--confirm <plaid account>=<ledger account>`, only when carrying would hold every row hearth has booked on it: each is in the base, or dated from the cut-over on, so hearth books it again on the base's account (`matching.Moving`).
A row before the cut-over that the base does not hold would leave the books, so then nothing is moved, and the command says how many and from which day.
Rulings are bound to what a row said, account and all, so the move is recorded (`carried_over`), and a ruling on a row booked again keeps deciding it; a ruling on a row the base holds decides nothing more, since the base's own entry for it stands.
A name may be moved more than once, as when an account filed later takes a name moved before, and a name moved onto one that was itself moved: every name a row was booked under before still binds its rulings (`RulingStore.formerly`), as a ruling names its row by its review id, one transaction's, so two accounts that once shared a name are never confused.
An account rows were moved into in Review is never matched or moved onto, as carried on it would hold them beside its bank's own rows and its balance could never agree with its bank's.
Who sees the account never changes: the base's account must be unclaimed, and the claim of hearth's own account becomes its claim, or be claimed by exactly the same members, and hearth's own account is then removed.
An account nobody has claimed is taken only where the ledger itself records that it is that bank account (`matching.recorded`): proposed by the connection's Plaid ids, at least five of them, as many as content alone needs to propose anything, agreeing on it, reaching the base's last month on it, so an account that was this bank's years ago and is something else since passes nothing on, and on a strict majority of the account's entries over the two years a bank sends of an account's history, up to the base's last entry on it, or since its first if that is later, so a few of the bank's rows at the end of an account's other history do not hand all of it over, while an account older than anything the bank still sends, its recent entries the bank's, does.
A move hands the account's whole history to the owners of the account hearth kept, every one of a joint account's, so rows alone, which could be anyone's, never do it.
The command line holds to the same rule, and `--claim` is the operator's word that the account is that one anyway: `hearth match-accounts --claim --confirm <plaid account>=<ledger account>`.
This is a change: before it, the command line moved onto any account nobody had claimed.
It is refused for a brokerage, which is not carried yet, and for an account rows were moved into in Review.
The Connect page never makes this move; it sends the member to the command.

### What carrying it on meets

For each matched account, and each one proposed, the command also says what carrying it on from the base meets, by the one rule the books use (`matching.facing`, `carrying.carry`):
how many of the feed's posted rows the base holds already, by Plaid id and, of the rest, by content;
the days of the base's last entry, last balance assertion and last `pad` on the account, and the cut-over worked out from them;
how many it does not hold that are dated before the cut-over, which hearth never brings in, and how many from it on, which hearth books.
A matched brokerage is said not to be carried, with the day its history ends.

A carried account is vouched for through its connection's last successful sync, as one hearth keeps itself is, and the base's entries that hold the bank's own rows count as entries from that connection (`books.vouched_over_base`).
A matched account that is not carried, a brokerage, is vouched for only through the base's own last day on it, never through a later sync: nothing after the base was frozen is in the books, so a month after it reads as incomplete, however current the connection is, rather than as a quiet month.
A live ledger inherited at `HEARTH_LEDGER` is not capped, as an import outside hearth may still be adding to it.

## What the ledger teaches

Once a ledger is imported, hearth suggests a category for every row it books in the ledger's own terms, never inventing one beside it (`docs/review.md`, "Suggestions, and why"):
as the row's owners decided before for the same merchant; as the ledger's latest entries for the merchant book it; as the rows of the bank's that the ledger holds say the bank's category is, by a strict majority of at least 3; and as hearth names the bank's category only when the ledger already books that category.
Each learns only from accounts with exactly the row's owners, so the ledger never carries one member's categories to another's rows, one owner's to a joint account's, nor a joint account's to one owner's own.
A row a member ruled on, before the import or since, is never moved by what the ledger teaches: an accept holds while the bank's own inputs to the row are what they were (`docs/review.md`).
Otherwise nothing is suggested and the row is asked plainly, "New merchant".

### What kind of money each category is

Every income and expense account is a kind of money (`hearth.categories`): pay, investment income or other income under Income; spending, tax, or not spending under Expenses.
"Not spending" is a move booked on the expense side, such as a retirement contribution taken from pay.
Kinds are guessed from generic words in the account's name: "Salary" or "Wages" is pay, "Interest" or "Dividends" investment income, "Tax" tax, "Retirement", "401k", "IRA", "Roth" or "Pension" not spending, and only those, never a word such as "Contribution", "Transfer" or "Savings" that names fees and charity as often; anything else is spending under Expenses and other income under Income, so nothing is ever left out.
A guess is never stored; a kind is stored only when a person sets one:

```sh
uv run hearth categories                                            # every income and expense account, its kind, and whether it is set or guessed
uv run hearth categories --kind Expenses:Payroll:Retirement=not-spending   # say what kind one is
uv run hearth categories --guess Expenses:Payroll:Retirement               # forget it, and guess from the name again
```

It refuses an account the books do not name and a kind its root does not take, changing nothing; a serving hearth sees the change at once (`books.standing`).
Every figure that means spending leaves "not spending" out, says so in a plain line with how much, to whoever may see the categories or transactions of the parts behind it, and what it leaves out is in what was left over, so the household's figures still add up (`docs/reports.md`).

## Read-only, and how that is kept

Hearth never writes to the ledger it imports or inherits, or the directory it lives in; what it writes is the base and its own books file, both in its data directory (`docs/books.md`).
That includes a write beancount itself would make: its default loader keeps a `.<name>.picklecache` beside the ledger whenever a load takes over a second, and deletes one it finds stale.
Hearth loads through beancount's uncached path instead, and `tests/test_books.py` and `tests/test_ledger_import.py` hold it to that.

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

Every entry the import books from a connection carries that source (`docs/books.md`), so an account hearth opened for a connection is vouched for by it from the first recompute after its history arrives.
An account the household's ledger opens and hearth does not carry on, inherited, or imported and not matched, or a matched brokerage, is left to that ledger, and the Plaid import books nothing into it, so its connection vouches for nothing there unless the ledger's own entries on it name that connection in hearth's `source`, as a ledger fed from hearth's export may, and then, on an imported base, only through the base's last day on the account (see "What carrying it on meets", above).
An account carried on from an imported base is vouched for as one hearth keeps itself is.
The demo marks its ledger's bank-feed rows with the source the import writes, so it shows the rule at work on an inherited ledger too.

An account no connection vouches for, such as one a statement import brings in, is judged with its part by the ledger alone, and that limitation stays: one that stopped while another in its part carries on reads as complete.
Judging each such account on its own entries would be worse, as every quiet card would hold its member's typical, pace and history back for good.
The honest answer for a statement-fed account is a balance assertion at each statement's close, which says the account is whole through it whether or not anything moved; until the import writes one, keep each account's statements current, or close it with a balance assertion.

**The ledger starts part way through each account's life.**
An account opened years ago whose history in the ledger starts this January has no known value before then.
Net worth history and the changes over a month or a year are drawn only over months the ledger covers, by the same rule the spending bars use (`figures.covers`).
A card with no transactions in the first weeks of the history therefore holds its months back as incomplete, because the ledger cannot tell a quiet card from a missing statement.

**Some members own nothing alone.**
A member whose accounts are all joint is told to connect their own and pointed at the joint accounts on Net worth.
