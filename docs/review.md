# Review

Every imported transaction is in the books from the moment it arrives.
Review is a correction pass, not a gate: nothing waits outside the ledger for someone to look at it.
What keeps that honest is the flag.
A row nobody has checked is `!`, beancount's own marker for incomplete, and every figure that rests on such rows says so.
A figure built partly from unreviewed guesses is never presented as though a person checked it.

`hearth.review` holds the engine; the page is `hearth.web.routes.review`.

## The rows

An import writes each row it is not sure of as a `!` entry with two postings: the bank's, and one balancing it against the proposed account, or against `Equity:Review:Unresolved` when nothing could be proposed.
One side of a card payment whose other side is not in the books balances against `Equity:Review:TransferPending` instead (`docs/pairing.md`).
Neither is a proposal: accepting such a row settles nothing, and no ruling may book a row against either.
Metadata carries the row's `review-id`, `review-group`, `model-confidence` and `source`, as beancount_nextgen writes them.
`pile.read_pile` reads them; a row without a review id or a group is refused, because a ruling on it could not be kept.

A row hearth books from a connection (`hearth.plaid.booking`) is named by Plaid's transaction id (`ids.plaid_review_id`), which Plaid keeps when it revises the row.
It is grouped by the category it suggests, so every row in a group suggests the same one, and a row with nothing suggested by Plaid's category.
It carries no `model-confidence`: it says instead why it is suggested as it is, in words (`review-why` and `review-reason`, "Suggestions, and why" below).

## Suggestions, and why

What a row hearth books is suggested as comes from `hearth.proposals`, afresh on every recompute, and is the first of these that gives one:

1. **What the row's owners decided before for the same merchant**: their latest decisions on rows of the merchant, up to the newest 5, when every one of them booked the same category.
   It reads "Dining twice before", counting every decision for the merchant that booked that category.
2. **How the imported ledger books the same merchant**, for a household with one: the ledger's latest entries for it, up to the newest 5, on accounts all the row's owners own, when every one books the same one category.
   It reads "As your ledger books it".
3. **The bank's category, as the ledger names it**, for a household with an imported ledger: the rows of the bank's that the ledger holds, on accounts all the row's owners own, each give the bank's category beside the ledger's own.
   A bank category is mapped to a ledger category when the ledger holds at least 3 rows of it and strictly more than half of them book that one category; a held row the ledger books as no one category, such as a split, counts against every category.
   It reads "Bank says Restaurant", the bank's category by its most specific words.
4. **The bank's category, as hearth names it** (`booking.proposal`: `FOOD_AND_DRINK_COFFEE` is `Expenses:FoodAndDrink:Coffee`); for a household with an imported ledger only when the ledger already books that category on an account all the row's owners own, so hearth never invents a category beside a ledger that has its own.
   It reads "Bank says Coffee".

With none, nothing is suggested and the row is asked plainly: it reads "New merchant" when nothing was decided or booked for its merchant before, and "Booked more than one way before" when the latest decisions or entries disagree.
A row with no merchant at all reads "Nothing suggested".
A transfer or a loan payment is never suggested a category: it is asked where its money went ("Where did it go?", below).
A suggested account must be a category the books can post the row to: open on its day in a ledger hearth reads, if that ledger opens it, and taking its currency; otherwise the next step is tried, so no suggestion can make a recompute fail.
No reason is ever a percentage.

### The same merchant

`hearth.merchants.merchant` is the one rule for which merchant a row is from, for Plaid's rows and the ledger's entries alike:

- the words are Plaid's merchant name where it sends one, else its name for the row, else the bank's own description; a ledger entry's payee, else its narration;
- case is ignored;
- a payment processor's prefix before an asterisk is dropped: `SQ *`, `TST*`, `PAYPAL *`, `SP *` and the like, generic services, never a merchant;
- everything from the first remaining asterisk on is dropped, the network's reference for one payment ("AMZN Mktp US*2K4AB1" is "amzn mktp us");
- everything from the first word after the first that holds a digit or starts with "#" is dropped, with "store", "str", "no" or "location" just before it, so "STARBUCKS #1234 SEATTLE WA" and "Starbucks Store 01234" are "starbucks", while the first word is always kept, so "7-Eleven" stays itself;
- punctuation is a space, but for "&" and "'" within a word.

What is left is the merchant; nothing left is no merchant, which is never the same merchant as anything.
Plaid's merchant name and the bank's description can still differ ("Blue Bottle Coffee" and "blue bottle"), and are then two merchants: the rule never guesses that two names are one.

### Whose decisions teach whose rows

A suggestion for a row learns only from rows on accounts every one of the row's owners owns, and from rulings made by one of its owners (`access.may_learn`).
So one member's categories never reach another member's rows, through a ruling, the ledger's entries or the bank's categories; a joint account's rows learn from either owner's decisions on accounts both own, never from one owner's own account; and a member's own rows learn from their decisions on the joint account, but not from their co-owner's.
Sharing grants nothing here, and neither does being an admin.
The reason is told only to whoever reviews the row, on the Review page and through the native doorway, never to someone the row's transactions are shared with: "twice before" is about the owners' other rows.
`tests/test_privacy_boundary.py` and `tests/native/test_doorway.py` crawl a household set up for every way a suggestion could carry one member's category to the other (`tests/learned.py`), under every way of sharing it.

### A suggestion is never a decision

A remembered merchant only sets the suggestion.
The row still arrives flagged `!` and waits for a member, however often its merchant was decided before; nothing is accepted on anyone's behalf, after a delay or otherwise.

### Why an accepted row never comes back

An accept is bound to the suggestion its author saw (rule 4 below), so a suggestion must not change under a row once it could have been accepted.
Two rules make sure of it:

- **A ruling teaches a row only if it was made before the row first arrived**, the start of the sync that first stored it (`Feed.arrived`).
  Rulings are only ever added, never changed, so nothing ruled later, a whole merchant accepted at once or one of its rows decided again differently, changes what a row already in the books suggests.
  Each earlier row teaches what the rulings made before the row's arrival decided it as, whatever was ruled on it since.
- **The imported ledger teaches a row only if nobody ruled on the row before the ledger was imported** (a ruling numbered at or below the base's `rulings_through`).
  Such a row keeps what it was suggested without the ledger, so an accept made before the import still holds; and the base is frozen once a ruling rests on it (`docs/real-ledger.md`).

What learning gives up for this is a row's first sight: a first import of a thousand rows arrives before any decision, so its rows learn from the ledger and the bank, and only rows arriving after a decision learn from it.
Only the bank can change a suggestion after a row could be accepted, as before: by revising the row's own category or merchant, or by revising or withdrawing an earlier row of the same merchant it learned from; the row is then unreviewed again, saying its suggestion changed since it was accepted.
`tests/test_learning.py` holds this through Review itself, including a whole merchant accepted at once and one of its rows then decided again.

### Where the imported ledger and the bank disagree

An account carried on from an imported base whose bank's balance does not agree with the base and the rows booked since has one reconciling entry, dated its cut-over, against `Equity:Ledger-Bank-Difference` (`docs/books.md`, "Carried on from the base").
It is in Review as its own kind of row, in the group "Where the imported ledger and the bank disagree", saying so: "The imported ledger and <bank> disagree by this much as of <day>", for the difference.
Its owner confirms it, which books it `*` where it is, or puts its cause right, such as a row the bank counts arriving, and it shrinks or goes at the next recompute.
It is confirmed one at a time, never with a group (`desk.one_decision`), and never excluded, as its account's balance is its bank's.
A confirmation is bound to the difference it saw, as every ruling is bound to what its row said, so a different difference asks again.
Until it is confirmed, the account is not shown as matching its bank.
Its review id is made of the account and its cut-over (`ids.reconciling_review_id`), so the entry derived afresh on every recompute is always the same row.

A row booked on an account hearth kept itself that was then moved onto the base's account it is (`docs/real-ledger.md`) keeps its rulings: a ruling is bound to the row as it was booked there (`RulingStore.formerly`).

## Rulings, and how they reach the books

A member can:

- **accept** one row, or a group, as suggested;
- **book** one row against another account: any spending or income category, or one of their own accounts the books hold open on the row's day, or that they named (`docs/books.md`, "Accounts hearth does not see"), never anyone else's, an account nobody claimed, or equity, which are all refused alike so a refusal names nobody;
  never, either, one of their own accounts whose balance its bank reports, or will once a sync clears what keeps it unknown, which takes only its bank's rows, refused with a sentence saying so (`docs/books.md`);
- **move a group** of rows whose other side hearth does not see onto one such account or category, in one decision ("Where did it go?", below);
- **exclude** one row: it is not a transaction, and books nothing; never a row on an account whose balance its bank reports, or will once a sync clears what keeps it unknown, as the bank counts it.

Rulings live in the app database (`review_rulings`, `review_ruling_rows`), append-only and keyed by review id, never by position in a file.
They store who ruled, when, and why (an optional note), and never an amount: the ledger is the only place money is recorded.

The ledger file stays as the import wrote it.
Each recompute of the books (`hearth.books`) lays the rulings over it (`review.overlay`): a reviewed row becomes `*` against the account it was reviewed as, an excluded row is dropped, and an unreviewed row stays exactly as imported.
Balance checks run on the result, so a ruling that would break one is reported like any other disagreement.
Every ruling starts a recompute in the background; one asked for while another runs is run straight after it, never dropped.

Which ruling decides a row (`review.decision`):

1. A ruling on the row itself wins over a ruling on its group.
   A row ruling that leaves the row unreviewed, a reopen or an accept whose proposal has since changed, wins only over earlier group rulings, so a later group accept still decides the row.
2. Among rulings of the same kind, the later one wins, so a ruling is taken back by making another.
3. Every ruling is bound to what its row said when it was made: which account, which day, how much (`ids.says`).
   A bank revises transactions it sent, and a decision about a row as it was is not a decision about it as it is: a charge booked as groceries says nothing about the same charge at three times the amount.
   Keeping the ruling would mark the revised row checked when nobody checked it, and dropping it would lose the decision without a word, so neither happens: the row is unreviewed again, at its proposal, and the queue says the bank changed it after it was reviewed.
   The ruling stays on record.
   A revision that only tidies the description or refines the category changes none of this; a changed category changes the proposal, which the next rule covers.
4. An accept is bound to the proposal its author saw.
   It records, for each row, the account that row proposed when it was accepted, and holds only while the import still proposes that account.
   If a later import proposes something else, the row is unreviewed again, and the queue says its suggestion changed since it was accepted.
5. An accept never covers a row the import proposed nothing for.
6. A reopen takes a decision back: the rows it covers are unreviewed again.
   Taking a decision back is not reviewing, so it does not move when the member last reviewed.

Binding starts at the page.
Every form sends back the rows it showed with the account each proposed then, and the desk accepts a row only if it is still waiting and still proposes that account.
A row that changed between the page and the click is left waiting, and the member is told.
A group accept covers exactly the rows the page showed, so a row that arrives in the group later waits to be seen.

A group can be accepted in one go only when that is one decision about one kind of transaction (`desk.one_decision`): every row waiting in it has the same suggestion, and the group is not one an import made of what it could not place ("Unsure", "Unknown", "Other" and the like).
A group of guesses, of mixed suggestions, or with a row nothing was suggested for, is reviewed row by row.
The desk enforces this, so neither the keyboard nor a forged request can bulk-accept such a group; the page simply offers no button for it.

**Undo** takes back exactly the last decision, whether one row or a whole group, and the page says how many transactions it covers.
It is offered right after the decision and until the next one; after that, any transaction can still be changed from the queue.
A member can only take back their own decisions.

There is deliberately no auto-accept, after a delay or otherwise: it would mark as checked transactions nobody looked at, which is exactly what the flag exists to prevent.

## The queue at a thousand rows

A first import can be over a thousand rows in thirty-odd groups, most of them a few kinds of transaction.
`review.queue` makes that tractable:

- **Grouped by default**, largest group first, so payroll and peer-to-peer transfers are one decision each.
  A row hearth booked is grouped by the category it suggests, named for it ("Food and drink: coffee", "Dining", "Income: wages"), whichever step suggested it, so a group is one decision however many merchants and reasons are in it, and there are about as many groups as categories, never one a merchant.
  A row with nothing suggested is grouped by Plaid's category, as before.
  A group with one clear suggestion is one button, binding every row it shows; a group without one opens its first rows for a look and links to the rest.
- **One by one**, in pages of fifty, for working through rows individually.
- **Ordered by any dimension**: group size, lowest confidence, largest amount, newest, account or suggested category.
  Lowest confidence and largest amount find real errors fastest.
  Each row says why it is suggested as it is ("Dining twice before", "As your ledger books it", "Bank says Restaurant", "New merchant"); a row an importer scored says its band in words ("Low confidence", "Nothing suggested"), as the design asks, never as a percentage.
  Least sure first puts first what was reviewed once and has changed since, then what nothing suggests, then the lowest scores, then the bank's category, then the ledger's and a member's own decisions.
- **Filtered** by account, suggested category or group, and **searched** by merchant or description, in any case, to find one particular row.
- **Honest progress**: how many rows remain and how much money they move, how many are reviewed of the total, and what the current filter leaves, all of it only what the member can act on.

The keyboard drives all of it (`static/review.js`), pressing the page's own controls so nothing depends on the script:
`j`/`k` move between items, `a` accepts, `s` skips, `c` changes the category, `x` excludes, `u` undoes, `]`/`[` jump between groups or pages.
On a group that is not one decision, `a` moves into its first row instead.
After each decision the page returns to the next row, focused and marked, clear of the sticky top bar; what the decision did, and its Undo, stay pinned in view at the foot of the window, since the next row can be a long way below the top of the page.

A row offers only what the desk would do.
A row with nothing proposed has nothing to accept, so its button saves the category typed.
A row its bank counts in the balance it reports offers no Exclude, which the desk would refuse; on a household whose every account is anchored to its bank, that is every row.
A row whose other side hearth does not see is not in the queue at all: it is counted apart, "N more need to know where the money went", whatever the filter, and answered on its own page (below).

## Where did it go?

Money moves between accounts, and hearth often sees one side only: a transfer to savings at a bank nobody connected, a car loan's payment, a card paid from a checking account hearth has never been shown, money from a person.
No category is right for such a row, so it is not asked "is this suggestion right?" but "where did it go?".
That is not a gap waiting to close: the other side may be a bank the member will connect next week, one they never will, one Plaid cannot reach, or a person.

A row is one-sided (`ItemView.one_sided`) when it is one side of a card payment hearth could not match (`docs/pairing.md`), or a row Plaid calls a transfer or a loan payment (`booking.moved`) with nothing proposed.
`review.where` groups a member's one-sided rows so that one answer settles each group: the account they are on, which way the money moved, and where the bank's own words say it went, which for a transfer is often the last digits of the account it went to ("Online transfer to SAV ...0417"); a bank's running number for each transfer is not part of the key.
The card payments hearth could not match on one account are one group a direction, as most come from one account; two cards are two questions, as they may have been paid from two different places.

Each group is answered once:

- **an account of mine that hearth does not see**, one the member names on the spot (`docs/books.md`, "Accounts hearth does not see") or named before: the rows are moves between their own accounts, neither spending nor income;
- **someone else**, a category: it was spending, or income;
- for a loan payment, **a loan of mine** or **a bill**.

The answer is a group ruling (`ReviewDesk.move_group`) bound row by row to what the page showed, as a group accept is: each row with what it said then (`ids.says`), so a row that changed between the page and the click is left waiting and the member is told.
A group whose rows are not all one answer is answered **one by one** (`/review/where/one`): each row with its own answer, one of the member's own accounts hearth does not see, a new one named for that row alone, or a category, saved as a row ruling (`ReviewDesk.set_account`) only while the row still says what the page showed; the rest of the group waits as it was.
Undo takes back exactly that answer: the whole group, or the one row answered alone.
Naming a new account and moving the group into it are one step: the account is open on every row's day from the moment it is named, before the books are next recomputed.

The next row like a group already answered onto a named account is proposed onto it (`ReviewDesk.learned`), "Like 22 past decisions", and waits for the member's yes: a proposal is never a decision, so the row stays `!` until it is accepted.
A proposal names only an account the member may move rows to, so a joint account's rows never propose one owner's private account to the other.

Not done here: pairing transfers between two connected accounts (shape 2 of `design/money-between-accounts.md`), choosing between two equally close sides of a card payment, money between members, and checking earlier answers against an account's own rows once it is connected.
Until a transfer between two connected accounts is paired, it waits here too, and its other account, which its bank reports, cannot be named as where it went.

Group names keep Plaid's abbreviations in capitals ("Bank fees: ATM fees"), and a category is named by its most specific part, so wages and interest read apart.

`hearth demo --first-review` serves an invented household with two years of history, none of it reviewed: one member has about 1,150 rows in 34 groups, the size of a real first import.
Exercising the queue at that size is what found that a one-key accept could settle an "Unsure" group of 212 guesses.

## Totals that lean on unreviewed rows

Every spending and income total on the dashboards says how much of it rests on unreviewed rows, at their suggested category.
That amount is computed only over parts whose review state the viewer may know (`access.may_see_review_state`): their own, or a part whose transactions are shared with them, where every line already shows its flag.
For any other part in a total, the page says that member's share may include rows they have not checked, the same way whether it does or not.
So no member's pile can be counted by subtracting from a household figure: `tests/test_review_web.py` checks that another member's ruling changes not a byte of anyone else's pages.

Net worth does not lean on review: the bank's side of every row is the bank's own record, and only that side touches a balance.
The one exception is a row whose suggestion is itself a balance-sheet account, such as a transfer guessed to the wrong account, which moves value between parts until it is reviewed.

## Reminders

Hearth runs on the household's machine and sends no email, so a reminder is something a page shows.
Each member chooses how often to be reminded (never, daily, weekly or monthly, weekly by default) on the review page (`review.cadence`).
A review is due once that interval has passed since the member last reviewed (an undo does not count), and only if something is waiting; a member who has never reviewed is due as soon as something waits.
The household page says when it is due, and the You page says when the member last reviewed.

## Who reviews what

A member reviews only rows on accounts they own, and never sees anyone else's queue, not even a count.
A row or group on another member's account behaves exactly like one that does not exist: both raise `hearth.disclosure.Concealed`, and an account filter naming someone else's account matches nothing, as a missing one does.
On a joint account either owner may rule, and the later ruling holds for both.
A row on an account nobody owns is reviewed by nobody.

The desk does not decide any of this itself.
It is handed `hearth.access.may_review(member, owners)` and asks it about every row, for the member at the desk and for the author of each ruling, so a ruling stops counting once its author no longer owns the account.
Sharing grants nothing here, and neither does being an admin.

Pages reach the desk only through `hearth.disclosure`: `Disclosure.review_desk` builds the viewer's desk, and `disclosure.rulings_in` gives a recompute every row's decision.
`tests/review/test_policy.py` checks that sharing everything opens nobody else's queue and that being an admin opens none.
