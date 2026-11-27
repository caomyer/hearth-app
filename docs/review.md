# Review

Every imported transaction is in the books from the moment it arrives.
Review is a correction pass, not a gate: nothing waits outside the ledger for someone to look at it.
What keeps that honest is the flag.
A row nobody has checked is `!`, beancount's own marker for incomplete, and every figure that rests on such rows says so.
A figure built partly from unreviewed guesses is never presented as though a person checked it.

`hearth.review` holds the engine; the page is `hearth.web.routes.review`, and the Mac app's way in is `hearth.native.review` (below).

## The rows

An import writes each row it is not sure of as a `!` entry with two postings: the bank's, and one balancing it against the proposed account, or against `Equity:Review:Unresolved` when nothing could be proposed.
One side of a card payment whose other side is not in the books balances against `Equity:Review:TransferPending` instead (`docs/pairing.md`).
Neither is a proposal: accepting such a row settles nothing, and no ruling may book a row against either.
Metadata carries the row's `review-id`, `review-group`, `model-confidence` and `source`, as beancount_nextgen writes them.
`pile.read_pile` reads them; a row without a review id or a group is refused, because a ruling on it could not be kept.

A row hearth books from a connection (`hearth.plaid.booking`) is named by Plaid's transaction id (`ids.plaid_review_id`), which Plaid keeps when it revises the row.
It is grouped by the category it suggests, so every row in a group suggests the same one, and a row with nothing suggested by Plaid's category, said to be suggesting nothing.
It carries no `model-confidence`: it says instead why it is suggested as it is, in words (`review-why` and `review-reason`, "Suggestions, and why" below).

## Suggestions, and why

What a row hearth books is suggested as comes from `hearth.proposals`, afresh on every recompute, and is the first of these that gives one:

1. **What the row's owners decided before for the same merchant**: their latest decisions on rows of the merchant, up to the newest 5, when every one of them booked the same category.
   It reads "Dining twice before", counting every decision for the merchant that booked that category.
2. **How the imported ledger books the same merchant**, for a household with one: the ledger's latest entries for it, up to the newest 5, on accounts with exactly the row's owners, when every one books the same one category.
   It reads "As your ledger books it".
3. **The bank's category, as the ledger names it**, for a household with an imported ledger: the rows of the bank's that the ledger holds, on accounts with exactly the row's owners, each give the bank's category beside the ledger's own.
   A bank category is mapped to a ledger category when the ledger holds at least 3 rows of it and strictly more than half of them book that one category; a held row the ledger books as no one category, such as a split, counts against every category.
   It reads "Bank says Restaurant", the bank's category by its most specific words.
4. **The bank's category, as hearth names it** (`booking.proposal`: `FOOD_AND_DRINK_COFFEE` is `Expenses:FoodAndDrink:Coffee`); for a household with an imported ledger only when the ledger already books that category on an account with exactly the row's owners, so hearth never invents a category beside a ledger that has its own.
   It reads "Bank says Coffee".

With none, nothing is suggested and the row is asked plainly: it reads "New merchant" when nothing was decided or booked for its merchant before, "Booked more than one way before" when the latest decisions or entries disagree, and "Not booked to a category before" when every one of them booked it to no category, moved onto an account or ruled not a transaction.
A row with no merchant at all reads "Nothing suggested".
A transfer or a loan payment is never suggested a category: it is asked where its money went ("Where did it go?", below).
A suggested account must be a category the books can post the row to: open on its day in a ledger hearth reads, if that ledger opens it, and taking its currency; otherwise the next step is tried, so no suggestion can make a recompute fail.
No reason is ever a percentage.

### The same merchant

`hearth.merchants.merchant` is the one rule for which merchant a row is from, for Plaid's rows and the ledger's entries alike:

1. A name is used where there is one: Plaid's merchant name, or a ledger entry's payee.
   Only without one is a description read: Plaid's name for the row, then the bank's original description, or a ledger entry's narration.
2. Letters of every script are kept, accents and all, in Unicode's compatible form, and case is ignored: "CAFÉ" is "café", "寿司 BAR" and "拉麺 BAR" are two merchants.
3. A payment processor's prefix before an asterisk is dropped: `SQ *`, `TST*`, `PAYPAL *`, `SP *`, `IN *`, `DD *` and the like, generic services, never a merchant.
4. After the first remaining asterisk, a first word of letters alone is the same company's service and is kept, as a member books "UBER *EATS" and "UBER *TRIP" apart; anything else there is the network's reference for one payment and is dropped ("AMZN Mktp US*2K4AB1" is "amzn mktp us").
5. In a description only, a phrase the bank starts with to say how the money moved is dropped from the front, only as the whole phrase followed by its date or number, with that date or number: "PURCHASE AUTHORIZED ON 09/12 STARBUCKS" is "starbucks", "ACH DEBIT 0012 GEICO" is "geico", "POS DEBIT 1234 TARGET" is "target", "VISA DDA PUR 12345 ..." loses its first four words.
   A merchant whose name merely starts with one of those words is never cut: "ELECTRONIC ARTS", "CREDIT ONE BANK", "ON THE BORDER", "CARD SERVICES", "SALE AT TARGET", and a name, never a description, is never read so at all ("Card Factory").
6. Everything from the first word holding a digit or starting with "#" is dropped, with "store", "str", "no" or "location" just before it, so "STARBUCKS #1234 SEATTLE WA" and "Starbucks Store 01234" are "starbucks"; but never while the words kept so far are only generic ones or a kind of place ("bar", "cafe", "club", "deli" and the like), so "The 1849 Club" and "The 9 Muses" stay two merchants, "BAR 22 NYC" is not every bar, "A 1 AUTO" stays itself, and "7-Eleven" too.
7. Punctuation is a space, but for "&" and "'" within a word.

What is left is the merchant, unless every word of it is generic ("the", "check", "payment", "debit" and the like) or a number: that is no merchant, which is never the same merchant as anything, so "CHECK 1001" teaches nothing to "CHECK 1002".
Plaid's merchant name and the bank's description can still differ ("Blue Bottle Coffee" and "blue bottle"), and are then two merchants: the rule never guesses that two names are one.
`tests/test_proposals.py` holds it to a table of real-shaped bank text, and to keeping apart every pair of merchants in it that are different.

### Whose decisions teach whose rows

A suggestion for a row learns only from rows on accounts with exactly the row's owners, and from rulings made by one of its owners (`access.may_learn`), for its owners' decisions, the ledger's entries, the bank's categories as the ledger maps them and which categories the ledger has alike.
A suggested category is shown as the row's category to everyone the row's transactions are shared with, so it may come only from accounts shared on exactly the same terms.
So one member's categories never reach another member's rows; a member's own rows never learn from a joint account, whose detail one owner cannot share without the other; and a joint account's rows learn from either owner's decisions on it, never from one owner's own account.
Sharing grants nothing here, and neither does being an admin.
The reason is told only to whoever reviews the row, on the Review page and through the native doorway, never to someone the row's transactions are shared with: "twice before" is about the owners' other rows.
`tests/test_privacy_boundary.py` and `tests/native/test_doorway.py` crawl a household set up for every way a suggestion could carry one account's categories to another's rows, a third member who owns nothing among them, under every way of sharing it (`tests/learned.py`).

### A suggestion is never a decision

A remembered merchant only sets the suggestion.
The row still arrives flagged `!` and waits for a member, however often its merchant was decided before; nothing is accepted on anyone's behalf, after a delay or otherwise.

### Why an accepted row never comes back

A ruling makes its row immune to what hearth learns.
An accept keeps booking the row to the account it recorded, and stays valid, for as long as the bank's own inputs to the row are what they were: what the row says (`ids.says`), which binds every ruling, and the bank's own category, recorded with the accept (rule 4 below).
What hearth learns, from the household's decisions, its imported ledger, the bank's categories as the ledger maps them, who owns which account, what is matched or connected, may change what a row nobody has ruled on suggests, and never anything about a row somebody has.
So claiming, matching or connecting an account, replacing another connection, setting a category's kind, adding a member, another member's rulings, and deciding other rows of the same merchant again all leave an accepted row booked where it was accepted; `tests/test_accept_holds.py` holds every one of them, and that the bank changing a row's amount, day or category still brings it back.

The one other thing that can bring an accepted row back is the books being unable to hold it: a ledger imported since that closes the account it was booked to, or opens it for another currency (`decision.postable`).
Laying it there would fail the recompute, so it is asked again instead.
A row is never booked to such a category in the first place: the desk refuses a category the books hold that is not open on the row's day, or not in its currency (`desk.NotOpen`), and the page says so.

What a row nobody has ruled on suggests is also kept from shifting under a member while they work: a ruling teaches a row only if it was made before the row first arrived, the start of the sync that first stored it (`Feed.arrived`).
So a whole merchant accepted at once teaches the rows that arrive after it, not the rows beside it in the queue the member is looking at.

A ruling on record that would move a row onto a carried account, or leave out one of its bank's rows, decides nothing (`decision.guarded`): the row waits again at its proposal, and Review says which it was, "Moved onto an account that now takes only its bank's transactions" or "Excluded, but its bank now counts it in the balance it reports", and counts such rows apart from those whose suggestion or bank changed.

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
4. An accept is bound to what its author was shown, and books the row to the account it recorded for it.
   For a row hearth booked from a bank, it holds while the bank's own inputs to the row are what they were: what the row says (rule 3), and the bank's category, recorded with each accept since hearth began learning suggestions.
   An accept recorded before then holds while the bank's category alone still names the account it accepted (`booking.proposal`), exactly as it always did.
   What hearth learns never undoes either ("Why an accepted row never comes back", above).
   For a row an imported ledger set aside, it holds while the ledger still proposes that account.
   When it stops holding, the row is unreviewed again, and the queue says why: that the bank changed how it describes the transaction since it was accepted, when the bank's category changed, or that its suggestion changed, for an accept recorded before then or a ledger's row.
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
  A row with nothing suggested is grouped by Plaid's category, said to be suggesting nothing ("Food and drink: fast food, nothing suggested"), so no group's name names a category some of its rows do not suggest; a transfer or a loan payment keeps Plaid's words alone, which "Where did it go?" reads.
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

## In the Mac app

The Mac app is a second way in to the same desk (`docs/native.md`, "Reviewing"): its Review screen and its Transactions decide through `hearth.native.review`, which makes the calls these pages make, as the member signed in to the app, and nothing else.
Everything above holds there as it does here: binding, one decision per group, Undo of exactly the last decision, and nobody's queue but the member's own.

- Booking a row against another account and excluding one are bound to what the row said when the app showed it (`ReviewDesk.book_shown`, `reject_shown`), as answering a row alone under "Where did it go?" is here, so a row its bank changed in between is left waiting there too.
- The queue's keys are menu commands: the arrows move between items, Return settles the row as chosen or accepts a group that is one decision, C changes the category, X excludes where offered, S skips, and Command-Z is Edit > Undo.
- A difference between an imported ledger and a bank is its own kind of row there too, with its own words and one action, Confirm, which accepts what it proposes: the app offers it no other category, no Exclude and no accept with its group, and C does nothing on it.
- Naming an account hearth does not see stays on these pages; the app answers "Where did it go?" with an account already named, or a category.
- `hearth demo --first-review` is the app's sample too, with `-firstReview YES` in a debug build (`apple/README.md`).

## Totals that lean on unreviewed rows

Every spending and income total on the dashboards says how much of it rests on unreviewed rows, at their suggested category.
A row nothing is suggested for, such as a new merchant's, waits against `Equity:Review:Unresolved` and is in no total at all until a member places it, so every total also says how many such rows it leaves out and how much money they moved its way ("Leaves out 3 transactions of $41 nobody has said what they were yet").
A transfer or a loan payment waiting to be told where it went is not counted so: it is a move, not spending or income.
Both amounts are computed only over parts whose review state the viewer may know (`access.may_see_review_state`): their own, or a part whose transactions are shared with them, where every line already shows its flag.
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
