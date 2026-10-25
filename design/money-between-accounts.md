# Money that moves between two accounts: a plan

This is a design and a set of mockups for review, not an implementation.
No application behaviour changed.
The one application file touched is `src/hearth/static/design.css`, kept byte for byte equal to `design/hearth.css` as a test requires; it gains new classes only, which no template uses yet.

## The problem, once

Money moved between two accounts, and hearth may or may not see the other side.
Every shape below is a case of that one fact, and today only the first has an answer.

1. A card payment, both sides connected.
2. A plain transfer, both sides connected (checking to savings).
3. One side connected, the other at an institution the member has not connected yet.
4. One side connected, the other at an institution the member will never connect, or that Plaid cannot reach.
5. One side connected, the other not an institution at all: a person, cash, a loan held by an individual.

"The other side is not connected" is not a gap waiting to close.
It is permanent and ordinary, and a new member meets it on the first day.
So the answer has to work without knowing whether the member will ever connect the other side, and it has to be asked for at the moment the evidence exists.

## Reproduced first

The captain's shape, invented: one bank, five cards, a checking and a savings account, two years, connected through the stand-in Plaid by the real connect path, as a member links a bank in onboarding, then driven in Chrome as the owner.

- The queue holds 1,494 rows waiting, 90 card-payment legs counted apart as "Needs the other side", and the 58 rows with nothing proposed.
- An "Online transfer out" row offers one control: a free-text box with the placeholder `Expenses:Category`, and Save.
- Typed as the member's own connected savings, the desk refuses: "That account's balance is the one its bank reports, so only the bank's own transactions go in it".
  The same message goes on: "If it is a move between two accounts you have connected, it counts once by itself when both banks have sent it".
  **That sentence is untrue** for checking to savings: pairing pairs only card payments.
  It is application copy, so this task does not change it; it is listed under "Found along the way".
- Typed as a new account at another bank, the desk refuses: "An account hearth does not hold yet ... cannot take it yet, so it stays waiting for now."
- The only door left open is a category, which counts a transfer as spending or income.

## The hypothesis, tested against the code

The brief's hypothesis: the desk already lets a row move onto an account that is open, feedable by this member, and not bank-anchored; an account hearth holds by name with no feed meets all three; its balance is simply not known; so the missing pieces are creating one, onboarding asking about it, and the queue offering it.

**The desk rule holds.**
`ReviewDesk.set_account` (`src/hearth/review/desk.py`) accepts a balance-sheet target when it is in `_movable` (open on the row's day and `may_move_to(owners)`, which is `access.may_feed`: the viewer owns it) and not in `banks`.
A hand-held account is in none of the bank sets, so it passes the third test.

**Four things around it do not hold, and each would make the naive version lie or break.**

1. **Such an account is never open.**
   With no inherited ledger, `ownbooks.derive` opens an account only for a confirmed connection, plus the categories rulings name; `_ruled` deliberately skips balance-sheet accounts ("hearth does not open one because a ruling named it").
   An account in the app database with no connection has no `open`, so `disclosure.Household.outside` holds it and the desk's `opened` does not: the move is refused as `NotTheirs`.
   There is also no way to create one in a household without a ledger: accounts are registered only by mapping a connected account (`connector.py`), by the CLI, or by the demo.
2. **One unknown balance blanks the whole net worth, permanently.**
   `Household.balance_unknown` is `books.unanchored | outside`, and `Worth.awaiting` counts every such account in a part.
   While the count is above zero the hero reads "Not known yet" and shows no figure at all (`templates/_macros.html`, `hero_value`), and `_reached` drops every month end from the history chart.
   That is right for an account whose balance is about to arrive.
   For an account hearth will never be told the balance of, it turns a member's net worth into "not known" forever: honest, and useless.
3. **It would make every month's spending "not known".**
   An account hearth opens that holds no history is `books.awaiting`, and `figures.flows_cover` judges spending as though it might flow through it.
   A hand-held account with no rows yet would hold every spending figure hostage to history that will never arrive.
4. **Connecting it later fails the recompute.**
   `opening.derive` takes the opening balance as the bank's current balance less the rows the bank sent, and asserts the current balance against the books.
   The books also hold every row a member moved into the account by hand, so the assertion is off by exactly their sum, and a disagreement fails the recompute.
   Concretely: checking to savings, $500, ruled into "Savings (not connected)"; then savings is connected and sends its own +$500 row; the books hold +$1,000 where the bank reported $500 of movement, and the household's figures stop.
   It is loud, not silent, which is the only good thing about it.

So the hypothesis is the right core, and it needs four pieces it did not name: open such an account in the books, count it as "left out" rather than "not known" in totals, keep it out of `awaiting`, and reconcile what was ruled into it when its bank arrives.
No spike was built: each point above is read off the code paths named, and the reproduction shows the refusals.

## The answer, by shape

The central idea is a **named account hearth does not see**: an account of the member's, at an institution or not, held by name, with owners, open in the books, never bank-anchored.
Hearth sees only the money that moves between it and the member's connected accounts, so its balance is never known from rows, and every total says it leaves the account out.
It is the same account whether the member connects it next week or never, which is why one answer covers shapes 3 and 4.

### 1. Card payment, both sides connected

Unchanged: pairing books it once as a transfer, `*`.
New: a tie (two equally close candidates) is no longer a dead end when both candidates are on the member's own accounts.
The row offers "Which of these is the other side?" with the candidates, and choosing one is a ruling that pairs the two rows.
Candidates are drawn only from accounts the member may review, so nothing about another member's account is shown.

### 2. Plain transfer, both sides connected

Today: nothing pairs it, and every door is locked.
Proposal: **transfer pairing**, the same machinery as card pairing, extended to a move between two depository accounts (and from a depository account to a connected loan).
**Decided: keyed on the mask the sending bank names, as card pairing keys on the card's digits.**
On the captain's real feed, 38 of 38 outgoing transfers name the destination's last four digits in the bank's own wording ("to CHK ...NNNN", "to <bank> NNNN"; hearth-transferpair-rate-1).
A sending row that names a connected account's mask pairs only with that account: exactly opposite amount, the same window as cards, closest first, ties never guessed.
A sending row that names a mask no connected account has is certainly one-sided: no candidate search, and the review says so (shape 3).
Only a row that names no mask falls back to amount and date, where the tie flow from shape 1 is the safety net.
The key is used from the sending side only: no connected account has ever received a transfer on the measured data, so nothing here assumes how a receiving bank words one.
A leg is a row on a depository account (or towards a connected loan) whose wording names a transfer or a mask, with Plaid's `TRANSFER_IN`/`TRANSFER_OUT` as a hint, not the test: Plaid also labels a remote cheque deposit and ordinary card charges as transfers.
Booked once, `*`, narration "Transfer between accounts", as card payments are booked, so neither bank's wording crosses between members when the two accounts have different owners.

### 3. The other side is at an institution not connected yet

The member names the other account once: "Bramblewood Credit Union, checking", owned by them (or jointly), kind bank account.
Every row that moved money to or from it is ruled onto it, `*`.
For many rows that is one decision: the group is keyed on the mask the sending bank names ("Online transfer to SAV ...0417"), so one choice settles every transfer to that account, and the next one is proposed exactly, not "like past decisions".
On the captain's data this is the main path: all 38 of his transfers name one of two masks, neither connected, so two answers settle them.
Later connecting Bramblewood matches those rulings with Bramblewood's own rows (see "Connecting it later").

### 4. The other side is at an institution never to be connected, or Plaid cannot reach

Exactly shape 3.
The member does not have to decide whether they will connect it, and nothing nags them to.
The account page offers "Connect it" as a quiet action, never a badge or a reminder.

### 5. The other side is not an institution

Three different things, and the design keeps them apart, because only one of them is an account of the member's:

- **Money to or from someone else** (rent to a landlord, paying a friend back, a Zelle from a friend): a category, as today.
  It is spending or income, and the review row says so in words: "Paid someone, or someone paid you: choose what it was for".
- **A loan held by a person or a lender not connected** (a car loan, money owed to a parent, money lent to a sibling): a named account hearth does not see, of kind loan (Liabilities) or money owed to you (Assets), balance not known.
  A loan payment is part principal, part interest, and the bank row is the whole of it; booked whole onto the loan, the interest is not in spending.
  That is no worse than today and it is honest, because the loan's balance is not known anyway; splitting it is listed as not done.
- **Cash**: a category, "Cash", by default.
  A household that keeps a cash float and wants it as an account can name one, kind cash, with the same rules.

**Money to or from another member's account hearth does not see** is the one case none of these fits: Jordan cannot name, see or post into Alex's account.
Proposal: a ruling "To or from someone in this household", booked against `Equity:Household:BetweenMembers`.
It is not spending, it invents no asset, and it says nothing about Alex's accounts, so it discloses nothing.
It is listed under uncertainty below.

### The 58 rows with nothing proposed

On the captain's shape they are `OTHER` (no category, "POS DEBIT"), `TRANSFER_OUT`, `TRANSFER_IN` and `LOAN_PAYMENTS`.
- Transfers between two connected accounts: settled by transfer pairing, never in the queue.
- Transfers to or from an account hearth does not see: shapes 3 and 4.
- Money from a person ("Zelle from a friend"): shape 5, a category.
- Loan payments: shape 5, a loan hearth does not see.
- "POS DEBIT" with no category: a category, as today.

### The 90 unpaired card-payment legs

Each is a payment into a card from an account hearth does not hold.
On the review page they become one group with one question, "Paid from", and one answer settles the group: the member's named account hearth does not see.
A member who pays from two such accounts chooses the one most come from and moves the rest row by row; the later connection catches any guess that was wrong (a payment Bramblewood's own records do not show comes back to review).
The group's name stops saying "other side not connected", which is false whenever a tie left a leg unpaired: it reads "Card payments hearth could not match".

## Onboarding

**Decision: a change to step 4, "Import and review", not a new step.**

The question "where else does your money go?" cannot be answered well before the import.
Asked up front at step 2 or 3, it is a questionnaire: the member has to remember every account they have ever moved money to, they will miss some, and hearth has nothing to show them.
After the import, hearth has the evidence: the rows themselves, grouped, with amounts and descriptions ("Online transfer to SAV ...0417", "ASHGROVE AUTO LOAN").
Asked then, it is not "list your accounts", it is "these 22 transfers went somewhere: where?", which a person can answer in seconds.

So step 4 gains one block between the import's progress and the handover to review, shown only when there is something in it:

- what hearth matched by itself ("21 moves between your accounts, each counted once");
- what it can see only one side of, as at most four groups with counts and amounts;
- two actions: **Tell hearth where it went** (the primary action, to the "Where did it go?" page) and **Connect another bank** (back to step 2, for a member who realises they should connect it now).
  Starting the review stays available, as the second action of the handover.

The stepper keeps four steps.
A household with no one-sided rows never sees the block, which is the main reason it is not a step: a step everyone walks through to find it empty teaches people to skip it.

It also surfaces later, at the right moment: whenever an import brings in a row hearth can see only one side of, it joins the same "Where did it go?" page, and the Review page's summary links to it ("4 need to know where the money went").
Naming the account answers every later row like it: the next transfer to SAV ...0417 is proposed onto Bramblewood checking, and a proposal is one keystroke to accept.

Step 2, "Connect institutions", gains one sentence and no control: "Connect every bank your money moves between, if you can. Any you cannot, or would rather not, you can name after the import."
That sets the expectation without asking anything.

## What changes in the books

1. **A new kind of account, held by name.**
   Stored as an account like any other (`accounts`, `account_owners`), with a kind (bank account, card, loan, money owed to you, cash) and an optional institution name, and marked as not connected.
   `ownbooks.derive` opens it, with `name` and `institution` metadata and a `seen: "moves-only"` metadata line, dated at the earliest of: the first row ruled onto it, the household's first history day.
   Whether it is created as `Assets:...` or `Liabilities:...` follows its kind.
2. **It is never anchored and never awaiting.**
   `books.unanchored` keeps reporting it (its balance is not known), but a new set, `books.unseen`, names every account held by name, and `awaiting` leaves those out, so spending figures are never held hostage to them.
3. **Rulings onto it are ordinary `ACCOUNT` rulings.**
   The desk rule needs no change for a single row.
   It needs one new capability: a **group ruling onto an account** ("these 22 went to Bramblewood checking"), bound row by row to what the page showed, as group accepts already are.
   Undo takes back the whole group, as it does an accept.
4. **New proposals learn from a ruling.**
   A row whose description and direction match rows already ruled onto a named account proposes that account, as "Like 22 past decisions".
   It is a proposal, so it is `!` until accepted.
5. **Transfer pairing** (shape 2), in `hearth.plaid.pairing`, next to card pairing, with its own group name, keyed on the mask the sending row names; a row naming an unconnected mask is booked as certainly one-sided, grouped by that mask.
6. **A tie ruling** (shapes 1 and 2): "this row's other side is that row", recorded on both review ids, only when the member may review both.
   The two rows are then booked as one transfer.
7. **Between members** (shape 5, last case): `Equity:Household:BetweenMembers`, opened when first ruled onto, under the same rule that already refuses every other Equity target, with this one name allowed.

## Connecting it later

This is where the naive design double-counts, and where it fails the recompute today.

When a member connects the bank and maps one of its accounts, the mapping page offers their named account as the target, first and preselected when the institution name matches: "Bramblewood checking, the account you have been keeping by name".
Mapping onto it turns it from held-by-name into connected: it is opened, fed and anchored like any connected account, and `books.unseen` stops naming it.

On the next recompute, before booking, every ruling that moved a row onto that account is checked against the account's own rows:

- **Matched.** The account's own feed holds a row of the exactly opposite amount within the pairing window: the two are booked once, as a transfer, exactly as pairing books a pair.
  The ruling stays on record and is satisfied; nothing is double-counted, and the opening balance comes out right because the matched row is one of the bank's.
- **Before its history.** The ruled row is dated before the first day the bank sent: the bank's opening balance already contains it.
  The row's other side is booked against `Equity:Opening-Balances`, so the connected account's history starts at the bank's opening, untouched.
- **Not in its records.** Inside the history, no matching row: the member's guess was wrong (most often a card paid from a different account).
  The row goes back to review, unreviewed, with the reason "Bramblewood's own records do not show this payment"; the ruling stays on record, as a revised row's does.

Card legs need nothing special: once both sides are connected, card pairing pairs them, and they leave the pile.
The connect page's import step says what happened in one line: "36 matched, counted once. 4 were before its history, and are in its opening balance. 2 are not in its records: back in Review."
Because unmatched rows go back to review rather than staying on the account, the balance assertion is never broken by an old ruling, and the recompute never fails for this reason.

## What changes on screen

Every screen below has a mockup in `design/`; `design/index.html` lists them under "Money between accounts".

| Mockup | New or changed | What it shows |
|---|---|---|
| `screens/onboarding-4-import.html` | changed | The import, finished; what hearth matched by itself; the one-sided groups; "Tell hearth where it went". |
| `screens/where-it-went.html` | new | The page that settles one-sided rows, one decision per group: card payments, transfers out, a loan, money from people, and a tie. |
| `screens/add-account.html` | new | Naming an account hearth does not see: kind, where, name, owners, ledger name, and what it does to totals. |
| `states/not-connected-account.html` | new | The member's own page with accounts hearth does not see: net worth "without" them, the account rows, an account's own page, and what another member sees. |
| `states/connected-later.html` | new | Connecting the bank later: the mapping offering the named accounts, and how the import resolved every earlier answer. |
| `screens/review.html` | changed | The summary links to the one-sided rows; transfers between connected accounts no longer appear as a group; its header matches the review model that shipped. |
| `screens/me.html` | changed | The same: the transfer to joint checking is matched, not waiting for review. |
| `screens/onboarding-2-connect.html` | changed | One sentence: connect every bank you can; name the rest after the import. |
| `system.html`, `hearth.css` | changed | Two new absence specimens: the dashed "left out" chip and the two-sided strip (`.unseen-chip`, `.tag-unseen`, `.sides`). |

All of them are listed in `design/index.html` under "Money between accounts", and checked in light and dark, at desktop and phone widths, with no horizontal overflow.
The figures agree across pages: 1,512 fetched, 21 moves matched (42 rows), 62 rows seen from one side, 1,408 ready for review; net worth $10,846 without three accounts, $22,301 once Bramblewood is connected.

## What it does to net worth

The rule: **whatever an unseen other side becomes, it invents no asset and no liability, and every total that leaves something out says so.**

- **Today.** The unconnected account does not exist in hearth.
  Net worth is the connected accounts at their banks' balances, shown as if complete.
  The 90 legs sit against `Equity:Review:TransferPending`, which is not in net worth, so they change nothing.
  The figure is right about what it counts and silent about what it leaves out.
- **Proposal: a named account hearth does not see.**
  Its balance is not known, so it contributes nothing to the known total and is counted towards "left out".
  The connected accounts are still at their banks' balances, so the known total is exactly today's figure.
  What changes is that it now says what it leaves out: "$48,210 without 2 accounts hearth does not see", with the accounts named to their owner.
  So net worth is unchanged as a number and more honest as a statement.
- **Why "without" and not "not known".** An account whose balance is about to arrive (connected, syncing) keeps today's "Not known yet", because the figure will be complete in minutes.
  An account hearth will never be told the balance of would make "not known" permanent.
  The known part, labelled with what it leaves out, is the same presentation hearth already uses when a member withdraws their net worth (rationale, departure 1), drawn with the "not there yet" absence (dashed, grey) rather than the "withheld" one (violet).
- **The history chart** draws what hearth sees, labelled "without the 2 accounts hearth does not see", instead of disappearing.
- **The household page**, for anyone who may see the member's net worth: "without some of Jordan's accounts" for a viewer who may not see Jordan's balances, the count for one who may, as `Worth.awaiting` already releases it.
- **Why not `Assets:<Member>:Outside`, an asset bucket.** Settling the 90 legs into it gives it a balance of the sum of the legs, about −$118k in the captain's invented shape, and net worth would fall by that, which is a lie.
  The named account would have the same sum of rows, which is exactly why its balance is never read from its rows.
- **Why not an equity bucket for everything ("moved outside hearth").** It invents nothing, so it does not lie about the number.
  But it answers "where did it go?" with "somewhere", so net worth goes on looking complete while an account is missing, and there is nothing for a later connection to reconcile against.
  It survives only for money between members, where naming the other account is not allowed.
- **Loans.** A car loan hearth does not see is a liability left out, so the known figure is too high, and it says so ("without Ashgrove auto loan").
- **Later connection.** The account becomes anchored and is counted at its bank's balance; "without" drops it.
  Matching makes sure nothing ruled onto it adds to that balance.

## Privacy

- **Ownership decides everything, as for a connected account.**
  A named account has owners, chosen when it is created; only an owner sees it, rules onto it or connects it.
  `access.may_feed` already says who may create an account they will own and move rows into it; no new rule is needed in `hearth.access` for that.
- **Joint named accounts.** Jordan may create one "Joint with Alex"; Alex then sees it and its rows, as with a joint connected account.
  A row Jordan moves from a solo account into it is shown to Alex as the joint account's side only: date, amount, "Transfer from another account", never Jordan's account or the bank's wording, as card pairing already narrates "Card payment" across members.
  This needs its own adversarial test: Alex must learn nothing about Jordan's solo account from the joint account's page.
- **Another member's accounts are never offered.** The "Where did it go?" choices, tie candidates and the mapping target list are built from accounts the viewer may feed or review, through the gate.
  "To or from someone in this household" names nobody.
- **The name probe.** Creating a named account takes a ledger name, so it inherits the accepted name-existence probe (decisions, 2026-10-24): one refusal for every collision, nothing listed.
- **"Without" on another member's figure.** It says that Jordan's figure leaves something out, which is the same fact "Not known yet" discloses today; the count goes only to a viewer who may see Jordan's balances.
- **The structural guards stay as they are.** Everything above is computed in the books and the review desk, decided in `hearth.access`, applied in `hearth.disclosure`; pages render views from `Disclosure` only.
  `tests/test_privacy_boundary.py` must learn the new routes (creating a named account, the "Where did it go?" page, the group ruling, the tie ruling), each answering for another member's row or account exactly like a missing one.

## What I rejected, and why

- **`Assets:<Member>:Outside`.** Understates net worth by about the sum of the legs (above).
- **One equity "outside" bucket for every case.** Honest about the number, silent about what is missing, and nothing to reconcile when the bank connects.
- **A new onboarding step "Other accounts" before the import.** A questionnaire with no evidence; members cannot answer it well, and an empty step teaches skipping.
- **Asking for a balance when an account is named.** A balance typed today is stale tomorrow, because hearth does not see the account's own spending; shown as current it would be a lie, shown with its date it still drifts.
  Considered for later (below), not proposed.
- **Treating "not connected yet" as a temporary queue state** (firstmate's earlier option 2, applied in hearth-reviewscale-1).
  It keeps the rows honest but unresolvable, and for shapes 4 and 5 "connect it" is not an answer at all.
- **Letting a member move rows onto a bank-anchored account** to settle shape 2.
  It breaks the balance assertion for the same reason connecting later does; pairing is the right tool.

## What I am unsure about

1. **Transfer pairing's false-pair rate.** Measured after this plan was first written, read-only on the captain's real feed (hearth-transferpair-rate-1): it is 0/0, not low.
   In two years there is not one transfer between two connected accounts: the connected savings account's 20 rows are all interest, and the statement-verified Jan-Jul 2026 ledger agrees for its window.
   All 38 outgoing transfer legs name a destination mask that matches none of the seven connected accounts, so pairing would make 0 pairs, 0 ties and 0 mispairs.
   That settled the keying (shape 2): the mask the sending bank names, never amount and date alone where a mask exists.
   What remains unknown: how a receiving bank words a transfer, since no connected account has received one; and the false-pair rate of the amount-and-date fallback, which this household cannot exercise.
   For the captain, shape 3 is the main path and shape 2 never fires; the mockups' invented household, with 21 matched moves, is a household that does move money between connected accounts, and is drawn so that both halves can be reviewed.
2. **"Without" vs "not known" for a household with many named accounts.** If most of a member's money is in accounts hearth does not see, the known figure is a small fraction of the truth, and "without 3 accounts" may not be loud enough.
   A possible rule: if named accounts are the majority of a member's accounts, say "Mostly not known" instead.
3. **A balance for a named account.** Some households will want the car loan's balance in net worth.
   The honest version is a balance the member enters from a statement, shown with its date, ageing amber like a stale price; I have not designed it.
4. **Between members.** `Equity:Household:BetweenMembers` keeps both members' figures right but cannot reconcile when the other member later connects their account, because it names no account.
   Their bank's row would arrive as an ordinary row in their own review, unpaired: correct, but not matched.
5. **Loan interest.** Booking a whole loan payment onto the loan leaves interest out of spending.
   Splitting needs the lender's schedule or the member's input per payment.
6. **A guess across two source accounts.** "Most came from Bramblewood checking" books guesses as reviewed until a later connection checks them; if Bramblewood is never connected, nothing ever does.
   The alternative, row by row for all 90, is honest and miserable.
   I chose the group answer with the reason shown on each row, and I am not sure it is right.
7. **Old ledgers.** A household that inherits a ledger often already names its other accounts there.
   An inherited account the member owns could serve as the named account directly; I have not designed the overlap with an inherited ledger's own entries for the same transfers.

## Found along the way

- The desk's refusal for a bank-anchored target says a move between two connected accounts "counts once by itself"; true only for card payments (reproduced above).
  Application copy, so left to the implementation task.
- `pairing.GROUP` still reads "Card payment: other side not connected", which is false for a leg left unpaired by a tie (hearth-reviewscale-1 pipeline note); the design renames it.
- `design/screens/review.html` said "Nothing is booked until someone checks it", the review model the captain replaced on 2026-10-18; fixed in the mockup.
- `design/states/connection-expired.html` said net worth uses the expired card's last balance and the household sees "1 account not updated since Oct 13"; that notice was retired (docs/privacy-boundary.md).
  Fixed to what the app does: figures carry on from what hearth has, each says where its data stops, and nobody else is told.
- `design/states/new-member.html` offered "Import CSV statements" in place of connecting, which does not exist, and said transactions appear "after you review them", the replaced model.
  Fixed; its promise about balances and review now matches what shipped.
- `design/screens/onboarding-1-member.html` said a new member starts out sharing their income total; income has defaulted to private since 2026-10-18.
  Fixed, and `states/new-member.html`'s "Totals only" tag now reads "Spending total only", as the app says.
- `design/screens/review.html` and `screens/me.html` showed "Transfers to joint checking" waiting for review with "Transfer" suggested; under this design a move between two connected accounts is matched and never waits.
  Replaced with an ordinary groceries group, totals adjusted.
- At first-import scale the phone tab bar's review badge ("1,467") is wide enough to crowd the icon beside it.
  Not changed here: it is the shared tab bar, and the app's own count rendering should decide it (for example "999+").
