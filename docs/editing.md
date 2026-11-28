# Editing the books

Status: approved on 2026-11-28, with every technical decision below.
Plan item 1, the books file holding the whole books, is built; the rest is not.

A household brings its old beancount ledger in once, and from then on hearth's books are the truth (`docs/real-ledger.md`).
The ledger kept outside hearth, and the fava workflow around it, can be retired only once a member can do in the Mac app what they did to their books there.
This milestone gives them three things: change an existing entry, add an entry by hand, and split an entry across categories.

Everything below holds to `AGENTS.md`.
Where the direction this milestone was given meets the code and the code says otherwise, the section "Where the code changes the direction" says so, with the evidence.

## What a person does to their books

What a household does to its books day to day, and what this milestone covers:

| What they do | Here |
|---|---|
| Put a transaction under another category, reviewed or not | Yes |
| Split one transaction across several categories, or part of it to money owed to them | Yes |
| Tidy the payee or the description | Yes, per entry |
| Write a note on a transaction | Yes |
| Record cash spending, or a transaction on an account no bank feeds | Yes, on an account kept by hand |
| Say what an account kept by hand holds on a day, as a statement or a count says | Yes |
| Remove a transaction they made by hand, or one the old ledger holds on an account no bank feeds | Yes |
| Change the date, amount or account of a transaction a bank sent | No: the bank's |
| Rename a merchant for every row at once | No, see "Open questions" |
| Tags and links | No, see "Open questions" |
| Trades, dividends, lots and their cost | No: the brokerage milestone |
| Budgets, forecasts, recurring entries, documents | No |

## The rule

Hearth never writes a ledger.
The imported base stays frozen and digest-checked (`hearth.base`), and the feed stays what the bank sent (`hearth.plaid.feed`).
What a member changes is an amendment: a ruling stored in the app database and laid over the books on every recompute, exactly as review rulings are today (`review.overlay`).
`books.beancount`, an output, shows the books as amended.

Nothing a member may do can make a recompute fail, or make an account disagree with its bank.
So what a bank says of a row on an account whose balance is its bank's, its day, its amount and its account, is never a member's to change; what it was for, what it is called and what the member says of it are.
An entry nobody's bank vouches for, one the member made or one the old ledger holds on an account no bank feeds, may be changed more freely, as long as every balance check the books hold still passes.

Every amendment is checked before it is stored, by the same function that lays it on every recompute, and every amendment on record that would no longer hold when laid, however it came to be so, decides nothing and says why, as `review.decision.guarded` and `postable` already do for rulings.
The first is a courtesy to the member; the second is the guarantee.

## Where the code changes the direction

The direction for this milestone holds, with six corrections the code makes.

1. **`books.beancount` did not show amended base entries, and could not while it included the base.**
   Each recompute wrote `ownbooks.render(ownbooks.own(books.entries), inherits=...)` (`books.from_household`): only the entries hearth derived, with the base pulled in by an `include` of its frozen files.
   A ruling on one of the base's own `!` rows, which `review.overlay` lays over it, was in the figures but already missing from the file.
   An amended base entry would have been missing too, so the file now renders the whole books as laid over, base entries included, rather than include the base.
   A throwaway experiment on an invented base of 20,006 transactions printed the whole books in 0.23 s, 2.2 MB, and beancount loaded the printed file back with no errors and exactly as many entries (20,473).
2. **The app database will hold money.**
   `review.rulings` says no amount is ever stored, as "the ledger is the only place money is recorded".
   A split names how much goes where, and an entry made by hand is nothing but what the member said, so amendments store amounts.
   The rule becomes: a bank's amounts are only ever the feed's, every part of a split adds up to its bank's amount exactly, and an amount a member gives is stored only for an entry no bank vouches for.
3. **"Changed more freely" stops at the base's own checks, its pads and its matches.**
   An account the base keeps without a bank still carries the statement balances the ledger asserts, and a `pad` the base filled is fixed when the base is loaded (`books.load_ledger` runs beancount's `pad` before anything is laid over), so an edit inside a padded stretch moves the next check rather than the pad.
   An amount, day or account changed on such an entry must keep every check on the accounts it touches, or it is refused.
   An account matched to a connection is its bank's even where it is not carried yet (`carrying.postponed`, a brokerage), as Review already treats it.
   An entry that holds a security, lots at cost or a price, keeps its figures, as the brokerage milestone owns them.
   And a ledger inherited where it is (`HEARTH_LEDGER`) may still be growing, so none of its entries has an identity that lasts: only an imported base's entries are editable.
4. **A member-stated balance cannot be an ordinary assertion.**
   Asserted as it stands, every later entry or row moved in before a later count would fail it, and the only safe answer would be to refuse ever more acts.
   It is instead the carried account's pattern (`opening.Carrying`): the first count anchors the account, and each later count is asserted after one entry for its difference, derived afresh each recompute from the two counts and what the books hold between them, flagged and in Review.
   It holds by construction, so it can never fail a recompute.
5. **A trial recompute is the wrong proof.**
   A full recompute of that invented base takes 1.6 s on an Apple M4, so a trial on every act would need the slow-answer worker and make every edit asynchronous.
   The one invariant an amendment can break is a base balance check on an account no bank feeds, and that is proved over the latest books in memory in milliseconds ("Proving an act", below).
6. **Undo must withdraw, not reopen.**
   Undo records a reopen today (`ReviewDesk.undo`), which leaves a row unreviewed: right while the app could only decide an unreviewed row, wrong once a reviewed row can be booked again, as taking back the second decision would wipe the first.
   Undo becomes a withdrawal of exactly one ruling, after which whatever decided before it decides again.

## Kinds of entry, and what may change

An entry's **bank facts** are its day, and the amount and account of each of its postings on a balance-sheet account.
Its **booking** is where the rest of its money goes: one category, or parts, each a category or an account of the member's own that takes a moved row.
Its **words** are its payee, its description and a note.

| Kind of entry | Booking | Split | Words | Bank facts | Remove |
|---|---|---|---|---|---|
| A row hearth booked from a bank | Yes | Yes | Yes | No | No, where its bank reports the balance |
| A card payment pairing booked once | No | No | Note and description, only by an owner of both sides | No | No |
| One side of a card payment nothing pairs with | Answered in "Where did it go?" | No | Yes | No | No |
| A row booked on a carried account | Yes, never onto a carried account | Yes, never onto a carried account | Yes | No | No |
| A base entry before a carried account's cut-over | Its categories only | Yes, within its categories | Yes | No | No |
| A base entry on an account no bank feeds or is matched to | Yes | Yes | Yes | Yes, keeping every check | Yes, keeping every check |
| An opening balance, a bank's or a count's | No | No | No | No | No |
| A balance check, the base's or a bank's | No | No | No | No | No |
| A carried account's reconciling entry | Confirm only, as today | No | No | No | No |
| A count's difference, on an account kept by hand | Confirm, or a category | No | Note only | No | No |
| An entry made by hand | Yes | Yes | Yes | Yes | Yes |

Why each falls where it does:

- **A row hearth booked from a bank.**
  Its own posting is what the bank's balance is asserted against (`hearth.plaid.opening`), so its facts are the bank's, as a review ruling never touches them (`overlay._booked` replaces only the proposal).
  Booking it, reviewed or not, is a ruling on its review id; splitting it replaces its second posting with parts that add up to it exactly; its words are laid over the entry, the bank's own kept beside them.
  It is excluded only where the desk already allows it (`desk.BanksRow`).
- **A card payment pairing booked once.**
  It is marked `*` and says only "Card payment", because its two sides may be two members' (`docs/privacy-boundary.md`, "The import adds two more").
  Nothing in it is a guess, so it has no booking to change, and a word on it is shown on both accounts, so only a member who owns both sides may write one.
  Saying a pair is no card payment at all is not offered here.
- **A row on a carried account.**
  As a bank's row, and `review.decision.guarded` already keeps any ruling from laying a row onto a carried account; a part of a split onto one is guarded the same way.
- **A base entry before a cut-over.**
  The reconciling difference is derived from the base's own balance at the cut-over (`carrying.cut_over`, `opening.Carrying`, both from the base alone), so a changed amount or day on the carried account's posting would make the assertion fail.
  Its Income and Expenses postings may be booked again or split, keeping their total, as nothing derived reads them.
- **A base entry on an account no bank feeds or is matched to.**
  Its facts are the ledger's, which the household may correct, as long as the base's own balance checks on every account it touches still pass; a `pad` stays what the base made it.
  An entry that touches a connected, carried or matched account, or holds a security, keeps its facts.
- **Openings, checks and reconciling entries** are balances, not history (`docs/books.md`, "Balances are not history"), derived afresh each recompute; there is nothing to edit, only the inputs they are derived from.
- **A count's difference** is derived like a reconciling entry; placing it under a category is a ruling on it.
- **An entry made by hand** has no original but what its maker said, so every part of it is theirs to change, on the accounts it may be made on ("Entries made by hand", below).

## Amendments, and how they reach the books

### One kind of record

An amendment is a ruling in `review_rulings`, with one more action, `amend`, beside `accept`, `account`, `reject` and `reopen`.
Sharing the table means one sequence of ids, so one Undo, one `RulingStore.newest` (which already refuses importing again once anything rests on the base), one `books.standing` (`max(id) FROM review_rulings` already notices every new ruling), and one `books.begun`.
Its parts go in two new tables beside `review_ruling_rows`, keyed by ruling id:

```text
amendment_words  (ruling_id, field, value)              payee, narration, note; null puts back the original
amendment_parts  (ruling_id, n, account, number, currency, rest)   a split's parts, a free entry's postings
review_rulings   + withdraws INTEGER NULL                the ruling an undo withdraws
```

An amendment sets one or more of three layers, each decided on its own, by the latest ruling on the entry that sets it:

- **booking**: the existing precedence of `review.decision` (a row ruling over a group ruling, the later of a kind winning, bound to what the row said), where a split is an `account` ruling with parts;
- **words**: the latest amendment that sets them, row rulings only;
- **facts**: for an entry made by hand or a free base entry, the latest amendment that sets its day and postings, or removes it.

Each layer an amendment sets is a whole snapshot of that layer, not a change to it: a ruling that sets the words holds every word, so a co-owner's later save, made from what they were shown, never half-overwrites the other's.
One act is one ruling, whatever layers it sets, so one Undo takes it back whole.

### Bound to what was shown

Every amendment is bound, as every ruling is, to what its entry said (`ids.says`, or for a base entry the hash of its day and every balance-sheet posting), and to `version`, the newest ruling deciding any of its layers when it was shown.
A bank revising the row unbinds it: the amendment decides nothing, the row waits again and says the bank changed it, and the amendment stays on record, so its history can offer it back.
A version that moved on since, as when the other owner of a joint account saved first, refuses the act as `changed`, and the app shows the entry as it now is.

### Laid on every recompute

`books.compute` takes a second callable beside `decide`, `amend`, from `hearth.disclosure` (`amendments_in`), which applies the access rule to each amendment's author, so an amendment stops counting once its author no longer may make it, as a ruling does.
After the rulings are laid (`review.overlay.reviewed`), in one pass in ruling order:

1. words are laid on each entry they name; the originals are kept as `was-payee` and `was-narration` metadata, and a note as `note`, so the books file traces every change, and the entry carries `amended: <ruling id>`, which names no member;
2. bookings with parts replace a row's second posting, or a base entry's category postings, each part checked as `postable` checks a single account, and guarded as `guarded` guards one;
3. entries made by hand are added, and free base entries are replaced or left out, account by account in ruling order, each one proved against the balance checks on the accounts it touches given those laid before it; one that no longer holds decides nothing and says why;
4. counts become an opening and checks with their differences ("Accounts kept by hand", below).

Only then are the balance checks run, as now, and every one passes by construction.
Every category a part names opens beside the others, as `ownbooks._ruled` opens every category a ruling names.

### The books file

`books.beancount` renders every entry of the books as laid over, the base's among them, with the base's options and `plugin_processing_mode "raw"`, naming beancount's own `balance` plugin so fava still checks every assertion, and not running `pad` again, since the entries a `pad` made are printed with it.
It says at its top which base, by digest, it was made from, and the base stays in `ledger-base/` exactly as it was imported.
It stays an output: hearth never reads it.

## Identity

An amendment names its entry by an id that holds on every recompute:

| Entry | Its id |
|---|---|
| A row hearth booked from a bank, carried or not | Its review id, from Plaid's transaction id (`ids.plaid_review_id`), which survives Plaid revising it |
| A card payment pairing booked once | A hash of `"pair"` and both rows' transaction ids |
| A row the base set aside for review | Its own `review-id`, as today |
| Any other base entry | A hash of `"base"`, the base's digest, the file's path within the base and the entry's line |
| An entry made by hand | Ten random hex characters, given when it is made and drawn again should they match any id the books hold |
| A count's difference | A hash of `"count"`, the account and the day, as `ids.reconciling_review_id` |

- **A base entry's place is a lasting name** because the base can no longer change once anything rests on it: the digest covers every byte and path (`base.digest`), the app brings a ledger in once, and the command line refuses to import again after a ruling, which an amendment is.
  An entry a plugin made, as `pad`'s `P` entries and implicit prices, and any two entries a plugin left at one place, have no lasting name and are not editable.
- **A pending row is not an entry.**
  Hearth books no pending row (`booking.book`), so there is nothing to amend until it posts, and the posted row has its own review id from the start.
  Saying what a pending row is before it posts is not offered.
- **A bank removed and connected again** sends every row under new transaction ids, so its rows have new review ids, and a decision on an old one decides nothing on the new one.
  The household has decided that review decisions come back after such a reconnect, to the same member on the same account, and ask when the row cannot be recognised with confidence; that is not built yet.
  Amendments are rulings keyed by review id, so they come back by the same means: one table of successions, an old review id to the new one, recorded when a newer connection's row on the same ledger account, with the same owners, is held one to one with a removed connection's row by the rule carrying already uses (`carrying.most_pairs`: same amount and currency, at most a week apart), and read wherever rulings are read (`RulingStore.covering`), as `RulingStore.formerly` already does for an account moved onto the base's.
  A row held by no unique partner is asked again, never guessed.
  It is a PR of this milestone, before the app's, since retiring the outside ledger would otherwise lose every edit at a reconnect.

## Entries made by hand, and accounts kept by hand

### Where an entry may be made

An entry made by hand is a ruling that makes an entry: an account it is made on, its day, payee, description, note and parts, stored in the app database and added to the books on every recompute with `source: "hand:<id>"`, which `connections.item_of` reads as no connection's.

It may be made only on an account **kept by hand**: one of the member's own balance-sheet accounts that no connection feeds or is matched to, that holds no security, and that the books open on its day.
That is an account a member named (`accounts.name_account`), and an account of the base no bank feeds, as an old loan or a bank Plaid cannot reach.
On an account whose balance its bank reports, it is refused for the reason `desk.BanksAccount` refuses a moved row: it would count money the bank says is not there.
A part of it may go to another account kept by hand of the member's own, as money moved between them.

### What it means for the rest of the books

- **Carrying.**
  `carrying.cut_over` is derived from the base alone, and stays so: entries made by hand are not in the base, and `carrying.read` never sees them.
  An account with an entry made by hand, or a base entry whose facts were amended, is never matched to a connection, exactly as an account rows were moved into in Review is not (`matching`, `Refusal.RULED_ONTO`, through `RulingStore.ruled_onto`, which counts these too): carried on, it would hold them beside its bank's own rows.
- **Coverage.**
  No connection vouches for an account kept by hand (`books.vouched` reads only `plaid:` sources), so it is judged by the books alone, as a statement-fed account is (`docs/real-ledger.md`): its months are whole up to its newest entry, or up to a later count.
- **Valuation, and the accounts hearth does not see.**
  An account a member named stays `seen: "moves-only"`, in `books.unseen`, never valued, and an entry made on it is a move like a row moved into it: what it says was spent counts as spending, and what it holds is still not known.
  It is valued only once a member counts it.

### Counting an account kept by hand

A member may say what an account kept by hand holds on a day: a count, as of a wallet, or a statement's balance, as of a loan.

- **The first count anchors the account**, the way a brokerage is anchored at what it reported (`books.positioned`): an opening against `Equity:Opening-Balances`, worked out as a bank account's is, from the count less what the books hold on it up to that day, and an assertion of the count the day after, both with `balance: "count"`.
  It claims nothing about any earlier day: no month before it is whole for its value, and no history is drawn through it, since what was spent from it before was never recorded.
  From then on the account leaves `books.unseen` and is valued, its balance the count and every entry since.
- **Every later count is asserted after its difference**: one entry, dated the count's day, for what the count and the books disagree by, worked out from the count before it, the count, and what the books hold between them, flagged `!` against `Equity:Count-Difference` and in Review in a group of its own, "Where your count and the books disagree".
  It is derived afresh on every recompute, so it shrinks or vanishes when the member adds the entry they forgot, and it never fails the recompute, as a carried account's reconciling entry never does.
  The member places it under a category, as cash spent and not written down usually is, or confirms it as it is.
  Until it is placed or confirmed, the account reads as not checked, as a carried account with an unconfirmed difference does (`books.reconciling`).
- **A count on a base account** goes after the base's last balance check on it: a difference dated earlier would move every check after it, which the base fixes.
- Removing a count is an Undo of it, or a withdrawal from its history; the differences either side of it are derived again.

So a cash wallet becomes an account hearth values, and stays honest: its balance is what the member counted and what they recorded since, every gap between the two is in Review saying how much, and nothing before the first count is shown as known.

## Proving an act before it is stored

An act is proved over the latest books in memory, by the function the recompute lays it with, given the one candidate, and stored only if it holds.
Measured on an invented household with an imported base of 20,006 transactions, 379 balance assertions and eight accounts, on an Apple M4, through `books.from_household` as `hearth serve` runs it:

| What | Time |
|---|---|
| A full recompute (three runs) | 1.56 s, 1.62 s, 1.60 s |
| Deriving the household's entries, base loaded and hearth's own derived and validated | 0.92 s |
| Loading the base alone (`books.load_ledger`) | 0.62 s |
| Reading the books for statements (`statements.read`) | 0.32 s |
| Laying rulings over every entry (`review.overlay.reviewed`) | 2.3 ms |
| Every balance assertion, after a split laid over one base entry | 46 ms |
| beancount's validation of every entry | 82 ms |
| Summing one account's postings over every entry | 5.4 ms |
| Printing the whole books for `books.beancount` | 0.23 s |
| beancount loading that printed file back | 0.61 s, no errors, 20,473 entries as before |

What each act needs proved:

- **Words, a booking or a split on any entry** change no balance-sheet figure, so the proof is the desk's checks alone: the entry is on the member's desk, the version and what it says are as shown, every part is a category or an account of the member's own that takes a moved row (`desk.FREE`, `desk.HELD`, `BanksAccount`), open on its day in its currency (`NotOpen`), and the parts add up to its amount exactly.
  No figure moves, so nothing more is proved.
- **Facts of a free base entry, or an entry made by hand on a base account**, are proved against the base's balance checks on the accounts they touch: a running sum of those accounts' postings through each check, given every amendment laid before it, a few milliseconds.
- **A count, or an entry made by hand on a named account**, can break nothing, as differences are derived; the proof is the desk's checks.

So no act needs the slow-answer worker (`hearth.native.worker`): it is answered on the bridge's own thread in well under the 100 ms that would call for one, and the recompute that follows runs in the background, as a ruling's does.
While the books have not been computed yet, or the last recompute failed, an act is refused as `not_ready`, since there is nothing to prove it over.
A sync that lands between the proof and the next recompute cannot make it fail: whatever no longer holds then is set aside at the recompute ("Laid on every recompute", above).

## Who may edit

One new rule in `hearth.access`, asked only through `disclosure.gate`:

```python
def may_amend(member: MemberId, owners: Collection[Collection[MemberId]]) -> bool:
    """Whether `member` may change an entry whose accounts are owned by each of `owners`:
    only an owner of every one of them. Sharing grants nothing, nor does being an admin.
    What nobody owns, nobody changes."""
    return bool(owners) and all(member in each for each in owners)
```

- An entry's accounts are the balance-sheet accounts it had before anyone changed it: a bank's row has one, a pair two, a base entry each it posts to, an entry made by hand the account it was made on.
  Where a part was split off to an account of the member's own, that does not take the entry from the other owners of a joint account: either may book it again, and the later holds for both, as a ruling does today (`docs/review.md`, "Who reviews what").
- Every target of a booking or a part is checked against the member doing it, never the entry's other owners, so a joint account's row is never booked onto one owner's account by the other (`access.may_feed`, as the desk checks today).
- Anyone else gets exactly `missing`, for the `entry` screen and every act, whether the entry is another member's, nobody's or none.
  Every refusal comes after that answer, and each names only the member's own accounts and categories, so no refusal can tell another member's entry from one that does not exist.
- An entry's id is told only to whoever may amend it: in `transactions`, beside `review`, and nowhere else.
- The words of an entry are its transaction detail, shown to whoever may see its accounts' transactions, and never on a side that `access.may_name_other_side` keeps to its mover: the other owner of a joint account hearth does not see reads that side as today, its day, its amount and "Transfer from another account", without the note.
- An entry's history, who changed it and when, is told only to those who may amend it; its other owners are told each other's names there, as they own it together.

## How it meets the rest of hearth

- **Review.**
  An edit is a ruling.
  Booking a row, or splitting it, reviews it, `*`; words alone review nothing; saving the editor on an unreviewed row with a suggestion shown books it as shown (see "Open questions").
  A split row reads "Split across 3 categories" in the queue and the journal.
  A count's difference is its own kind of row, `kind: "count"`, placed or confirmed one at a time, never with a group.
- **Suggestions.**
  Booking a row teaches as a ruling does today (`hearth.proposals`, step 1).
  A split teaches nothing and counts against every category among the merchant's latest decisions, as the ledger's split already does (step 3), so a merchant split once is "Booked more than one way before".
  Words teach nothing: the merchant is always the bank's (`hearth.merchants`), so a tidied payee never moves a suggestion and an accept stays bound to the bank's own inputs.
  A base entry's category as amended teaches as the base's own entries do (step 2), under `access.may_learn` with its author, so it teaches only accounts with exactly its owners.
  An entry made by hand teaches nothing in this milestone.
- **Statements** read `Books.ledger`, built once from the books as laid over, so every amendment is in every statement that reads its accounts and in none that does not (`access.may_read_statements`); a split's parts are already told apart by `part` in the journal.
- **Spending kinds** come from every category the books name (`categories.kinds`), so a part's category has a kind as any other.
- **`hearth export`** is unchanged, and its schema stays `buzz.plaid.v4`: it carries each row as the bank sent it and has never carried a ruling, so it carries no amendment either.
  A ledger kept outside hearth that still reads it sees none of the household's edits, which is one more reason to retire it, never to change its shape.
- **The record of who did what** is the ruling store itself: every amendment with its member, its moment and what it set, never edited and never deleted.
  It is deliberately not the audit log: admins read the whole log (`access.may_read_event`), so an event per edit would tell an admin when each member works on their books, as `docs/privacy-boundary.md` already decides for naming an account.
- **Undo** withdraws exactly one of the member's own rulings, after which whatever decided before decides again; an amendment may be withdrawn while no later ruling sets the same layer, and is refused as `superseded` otherwise.
  **Put back** returns an entry to what the bank sent and hearth suggested, or what the base holds, as one ruling that sets every layer to its original; an entry made by hand has no original and is removed instead.
  The original is never lost, as the feed and the base are never written.

## The doorway

### Asks

One new screen, `entry`, in `doorway.SCREENS`:

| Ask | Takes | Answers |
|---|---|---|
| `entry` | `id`, or `account` | One entry the viewer may change, or what a new entry on one of their accounts kept by hand may say. |

```text
-> {"id": 9, "ask": "entry", "with": {"id": "3f09a1c2de"}}
<- {"id": 9, "answer": {"state": "ready", ..., "entry": {
     "id": "3f09a1c2de", "kind": "transaction", "from": "bank",
     "date": "2026-11-02", "account": {"id": "h1LQ...", "name": "Everyday checking"},
     "amount": "-42.50", "currency": "USD",
     "payee": "Cobble Street Market", "narration": "Groceries", "note": null,
     "booking": {"parts": [{"category": "Expenses:Food:Groceries", "amount": "-42.50", "rest": true}]},
     "original": {"payee": "COBBLE ST MKT", "narration": "COBBLE ST MKT 0412", "category": null},
     "reviewed": false, "says": "9c1e...", "version": 41,
     "may": {"booking": true, "split": true, "words": true, "facts": false, "remove": false, "put_back": true},
     "fixed": "bank",
     "choices": {"accounts": [{"id": "h7Qw...", "name": "Money owed to me"}]},
     "history": [{"ruling": 41, "by": "you", "at": "2026-11-03T09:12:00Z", "set": ["booking", "note"], "undoable": true}]
   }}}
```

- `from` is `bank`, `ledger` or `hand`; `fixed` says why its facts may not change (`bank`, `carried`, `checked`, `security`), or null; the bank's own name is never in it, as a co-owner who did not connect it is never told which bank feeds an account.
- `amount` is in the account's sign, as the journal gives it; every part's `amount` is in the entry's sign, and the one marked `rest` is what the others leave, worked out by the engine.
- `choices.accounts` are the viewer's own accounts a part may go to (`ReviewDesk.movable`); categories are any, as the journal's `categories` list offers them.
- Asked with `account`, it answers `entry: null` and `may` for a new entry, or `missing` for an account the viewer may not make one on.
- The doorway reads `id` and `account` as it reads every id, 1 to 64 characters; whether the viewer may change the entry is `hearth.disclosure`'s to decide, through `Disclosure.entry`.

`transactions` gains `entry`, the entry's id, beside `review` and on the same terms: only to whoever may change it, null to everyone else.
`account` gains `kept`, whether the viewer may make entries on it and count it, and the standing `counted`, "as you counted it", with `counted_on`, beside `checked`; its difference waiting in Review is `reconciling`, as for a carried account.

### Acts

Four new actions in `hearth.native.bridge.ACTIONS`, each made through the member's own desk (`Disclosure.review_desk`), never a second way, and `undo` taught to withdraw:

| Action | Takes | Does |
|---|---|---|
| `amend` | `entry`, `version`, `says`, `set` | Changes one entry: any of `category` or `parts`, `payee`, `narration`, `note`, and where its facts are free, `date`, `amount`, `account` or `removed`. |
| `enter` | `account`, `date`, `amount`, `category` or `parts`, and optionally `payee`, `narration`, `note` | Makes an entry by hand on one of the viewer's accounts kept by hand. |
| `put_back` | `entry`, `version`, `says` | Puts one entry back as the bank or the base had it. |
| `count` | `account`, `date`, `balance` | Says what an account kept by hand holds at the end of a day. |
| `undo` | `ruling` | As today, now withdrawing exactly that ruling. |

```text
-> {"id": 10, "act": "amend", "with": {"entry": "3f09a1c2de", "version": 41, "says": "9c1e...",
     "set": {"parts": [{"category": "Expenses:Food:Groceries", "amount": "-30.00"},
                       {"account": "h7Qw...", "rest": true}], "note": "Half is Sam's"}}}
<- {"id": 10, "outcome": "amended", "ruling": 42, "rows": 1, "changed": 0}
-> {"id": 11, "act": "enter", "with": {"account": "h3Zp...", "date": "2026-11-04", "amount": "-6.50",
     "category": "Expenses:Food:Coffee", "payee": "Corner cart"}}
<- {"id": 11, "outcome": "entered", "ruling": 43, "rows": 1, "changed": 0, "entry": "b81f2c09aa"}
-> {"id": 12, "act": "amend", "with": {"entry": "3f09a1c2de", "version": 41, "says": "9c1e...",
     "set": {"category": "Expenses:Food:Dining"}}}
<- {"id": 12, "outcome": "changed", "ruling": null, "rows": 0, "changed": 1}
```

- `set` holds only what changes; a word set to null goes back to the original; `parts` is 2 to 20 parts, each a `category` or an `account` id, each an `amount` but exactly one, the `rest`.
- Amounts are decimal strings in the entry's currency, days ISO dates, and anything else the act does not take is `missing`, as every act reads its `with`.

| Outcome | Meaning |
|---|---|
| `amended` | Saved as asked. |
| `entered` | Made; `entry` is its id. |
| `put_back` | Back as the bank or the base had it. |
| `counted` | The count is saved; any difference waits in Review. |
| `changed` | The entry changed since it was shown, here or by its other owner, so nothing was saved. |
| `refused` | Not saved, for the reason in `refused`, `{"why", "said"}`. |
| `missing` | No such entry or account on the viewer's desk, or not theirs. |

`refused.why` is the engine's word and `said` a sentence in the app's words, naming no path, command or setting, as the ledger's refusals are (`ledger_import.Refusal`):

| `why` | `said` |
|---|---|
| `bank_fact` | "The date, amount and account are as the bank sent them, so they stay as they are." |
| `checked` | "That would change a balance your ledger checks on Mar 1, 2026, so it was not saved." |
| `security` | "Hearth does not change entries that buy or sell holdings yet." |
| `banks_account` | "That account's balance is the one its bank reports, so only the bank's own transactions go in it." |
| `not_yours` | "A part can go to a spending or income category, or to one of your own accounts that Hearth does not see." |
| `not_open` | "Your books close that category before the day of this transaction, or keep it for another currency. Choose one that is open on its day." |
| `not_an_account` | "Enter a full category name, like Expenses:Groceries." |
| `too_much` | "The parts come to more than the transaction, so nothing is left for the rest." |
| `too_many_parts` | "A transaction splits into at most 20 parts." |
| `not_kept` | "Hearth keeps this account's balance from its bank, so there is nothing to count." |
| `before_checks` | "Your ledger checks this account up to Mar 1, 2026, so a count goes after that day." |
| `superseded` | "That change was changed again since, so it can no longer be taken back on its own." |
| `not_ready` | "Hearth is still working out your books, so that was not saved. Try again in a moment." |

### What the crawls must learn

- `tests/native/test_doorway.py`: `entry` goes in `NARROWED`, asked for every entry id in the household, gathered from the books themselves and not only from what any member was told, so every member's, the one nobody owns, invented ids and every account by id.
  Every act is made by every member on every entry, as shown and as not shown, with forged versions and parts naming every account in the household; one on anything not on the viewer's desk must answer `missing` and record nothing (`RulingStore.newest` unchanged), and no answer may name anything on another member's desk.
- `tests/household.py` gains amendments of every kind, each word a unique private string: a split with a part to one member's own account on a joint account's row, a note on a row moved into a joint account hearth does not see, an entry made by hand, a count and its difference; so `tests/test_privacy_boundary.py`, the statements crawl and `tests/test_review_web.py`'s "another member's ruling changes not a byte" hold amendments too.
- `tests/native/fixtures` gains `entry.json`, and `acted.json` its new outcomes and refusals, which `apple/HearthKit` decodes.

## The app

The app keeps its two places a transaction is decided, Transactions and Review, and adds editing to both rather than a third.

**Transactions.**
Selecting an entry the member may change shows its inspector, as a review row's does today, with an Edit button (Command-E).
Editing opens the entry's fields in place: Category, Payee, Description and Note, with "Split…" beside Category.
The bank's facts stay as text above them, with one line saying why: "The date, amount and account are as the bank sent them, so they stay as they are."
For an entry the old ledger holds on an account no bank feeds, Date, Amount and Account are fields too, with "Your ledger checks this account's balance, so a change must keep every check it holds."
Save, Cancel, and below them the entry's history: "Changed by you on Nov 3: category, note." or "Changed by Blake on Nov 3: payee.", each of the member's own with Undo while it is the latest, and "Put Back as It Came" or, for a ledger's entry, "Put Back as Your Ledger Had It".
On an unreviewed row Save reads "Save and Review" with "Saving books it as shown and marks it reviewed."
File > New Transaction… (Command-N) opens a new entry on the account the journal is narrowed to, when it is one kept by hand, or asks which: Account, Date, Payee, Description, "Money out" or "Money in", Amount, Category or "Split…", Note.

**Split.**
A sheet, "Split Transaction", lists its parts, each a category or one of the member's own accounts and an amount, and a last row, "The rest", whose amount the engine works out: "The last part takes whatever the others leave."
"Add a Part" and "Remove" change the list; up to 20 parts.

**Review.**
A row's detail keeps C for its category and gains "Split…" (Command-Shift-S) and "Edit Details…" for its words, which save as the same act.
A count's difference reads "Your count and the books disagree by $18.40 as of Nov 2." with Confirm and a category, never with its group.

**Accounts.**
An account kept by hand shows "Count Balance…", a sheet titled "What does it hold?", with "Balance at the end of" a day and an amount, and before the first count: "Hearth will keep this account's balance from what you count and the transactions you add. Months before your first count are not shown as known."
Its standing reads "As you counted it on Nov 2", or "Not checked: your count and the books disagree, in Review".

**Notices**, in the voice of `ReviewWords`:

- "Saved." for words alone; "Saved under Groceries." for a booking; "Saved, split across 3 categories." for a split; "Saved and reviewed." on an unreviewed row.
- "Added to Wallet." for an entry made by hand; "Counted. Where it differs from the books waits in Review." for a count.
- "Put back as the bank sent it." or "Put back as your ledger had it."
- "Taken back. It is as it was before that change." for an Undo.
- `changed`: "That transaction changed since Hearth showed it, so nothing was saved. Here it is as it is now."
- Every refusal shows its `said` as it comes.

## A plan of pull requests

Engine first, each reviewable on its own; each must pass `uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest`, and the app's `swift test --package-path apple/HearthKit`.

1. **The books file renders the whole books.**
   `ownbooks.render` prints every entry as laid over, base and all, in raw mode with beancount's `balance` plugin named; the base stays in `ledger-base/`.
   Tests: a ruling on a base `!` row shows in the file; the file loads in beancount with no errors and the same entries for `tests/ledgers.py`, lots at cost and its `pad` included; it is still written only when it changes.
   Mutants: render only hearth's own entries again; run `pad` a second time; leave out the base's options.
2. **Undo withdraws.**
   `review_rulings.withdraws`, `RulingStore.withdraw`, and `decide` skipping withdrawn rulings; `ReviewDesk.undo` withdraws; a reopen on record keeps its meaning.
   Tests: `test_accept_holds.py` and every review test unchanged; undoing a second booking restores the first; only the author withdraws, and only while it is the latest.
   Mutants: honour a withdrawal by someone else; withdraw a withdrawal; let a withdrawn group accept still decide.
3. **Amendments on review rows: words and splits.**
   `access.may_amend` through the gate; `amendment_words` and `amendment_parts`; the layers in `review.decision`; the overlay lays words and parts, guarded and postable per part; `ReviewDesk.amend` and `put_back` for bank rows, pairs and the base's `!` rows; proposals read a split as no one category.
   Tests: parts add up exactly or are refused; a part onto a bank's or carried account is refused at the desk and set aside at the recompute; a bank's revision unbinds; joint owners' later save holds and a stale version is `changed`; another member's entry is concealed; `tests/test_learning.py` holds that a split and words teach nothing; the privacy crawl's household gains its amendments.
   Mutants: drop the parts' sum check; skip `guarded` for a part; let sharing grant `may_amend`; lay words on a side `may_name_other_side` keeps to its mover.
4. **Reconnected banks keep their decisions.**
   The succession table and its one lookup in `RulingStore.covering`, used by rulings and amendments alike, recorded by the carrying rule's one-to-one hold on the same ledger account with the same owners.
   Tests: remove and connect again in `hearth.plaid.fake`, and every ruling and amendment decides the new row; two rows of one amount a day apart, ambiguous, ask again; another member's connection never inherits.
   Mutants: match across ledger accounts; match by amount alone without the week; take the first of two candidates.
5. **Base entries.**
   Base entry ids from the digest, path and line; free facts on accounts no bank feeds or is matched to, proved against the base's checks given every earlier amendment, and set aside at the recompute where they no longer hold; matching refused for an account with amended facts; amended categories teach as the base does.
   Tests: an edit inside a padded stretch is refused as `checked`; undoing an earlier amendment that a later one leaned on sets the later aside, saying why, and the recompute passes; a matched, postponed or brokerage account's entries keep their facts; an inherited ledger's entries are not editable; importing again is refused once an amendment rests on the base.
   Mutants: leave the digest out of the id; turn the recompute's guard off, which must fail a test by failing a recompute; allow facts on a postponed account.
6. **Entries made by hand and counts.**
   `enter` and `count` at the desk; hand entries in the books with `source: "hand:<id>"`; the first count's opening, later counts' differences in Review, `books.positioned` for a counted account, and its leaving `books.unseen`.
   Tests: a wallet named, used and counted is valued from its first count and no month before it is whole; forgetting an entry shows as a difference that vanishes when it is added; a count before the base's last check is refused; an account with an entry made by hand is never matched; `test_an_account_hearth_does_not_see_never_reads_as_zero` still holds for an account never counted.
   Mutants: work the opening out without the rows before the count, or value months before it; assert a later count without its difference, which must fail a recompute in a test; let a connected account take an entry made by hand.
7. **The doorway.**
   `entry` in `SCREENS`; `amend`, `enter`, `put_back` and `count` in `ACTIONS`; `entry` in `transactions`, `kept` and `counted` in `account`; fixtures; `docs/native.md`.
   Tests: shapes against `entry.json` and `acted.json`; the crawl as above.
   Mutants: refuse before concealing; hand an entry's id to a member who may only see it; name a non-owner in its history; answer an act on another member's entry with anything but `missing`.
8. **The app: changing and splitting.**
   `HearthKit` types and `EntryWords`, the Transactions inspector's editor, Split, and Review's Split and Edit Details, with UI tests on the demo.
9. **The app: entries by hand and counts.**
   New Transaction, Count Balance, the standing words, and naming an account hearth does not see in the app, if the owner agrees below.

## Technical decisions

Decided on 2026-11-28, as recommended.

1. **Amendments in `review_rulings`, with side tables, or a table of their own?**
   Decided: `review_rulings`, for one sequence of ids, one Undo, and `newest`, `standing` and `begun` already seeing them.
2. **Undo as a withdrawal for every ruling, keeping reopen only for what is on record?**
   Decided: yes; for every decision the app makes today it gives the same result, and it is right once reviewed rows can be booked again.
3. **A base entry named by its place in the digest-checked base, or by its content?**
   Decided: its place; the base cannot change once anything rests on it, and content repeats where a ledger holds two identical entries.
4. **`books.beancount` rendering the whole books rather than including the base?**
   Decided: yes, measured at 0.23 s and loading back clean on 20,006 transactions; PR 1 proves it on `tests/ledgers.py`'s lots and `pad`.
5. **Proof over the latest books in memory, with the recompute's guard, and no worker?**
   Decided: yes; every act measured in milliseconds, against 1.6 s for a trial recompute; add the worker only if a real base measures an act over 100 ms.
6. **The split's last part as "the rest", worked out by the engine?**
   Decided: yes, so the app does no arithmetic and no split can fail to add up; the alternative, a live remainder in the app, breaks the rule that the app computes nothing.
7. **Reconnect successions in this milestone, before the app's PRs?**
   Decided: yes, as PR 4; the owner decided decisions come back after a reconnect, and an edit lost at a reconnect would send the household back to the outside ledger.
8. **Original words kept in the books as `was-payee` and `was-narration`, and `amended: <ruling id>`?**
   Decided: yes; the file traces every change, and a ruling id names no member.
9. **Entries made by hand teach nothing in this milestone?**
   Decided: yes; they have no bank merchant, and teaching a card's row from a wallet's entry is a rule of its own to design.
10. **At most 20 parts to a split?**
    Decided: yes; it bounds the act's payload and the queue's words, and no household split seen needs more.

## Open questions

Each is the owner's to answer; nothing that depends on one is built until it is.

### For the owner (product)

1. **Does saving an edit on an unreviewed row mark it reviewed?**
   Recommendation: yes, it books the row as shown, the button says "Save and Review", and words alone on a row with nothing suggested leave it waiting.
2. **What is a count's difference?**
   Recommendation: one entry on the count's day, in Review, which the member places under a category or confirms, with no category suggested for it, since a wallet's gap and a loan's are different things.
3. **Name an account hearth does not see from the app?**
   Today that is only in the browser (`docs/review.md`, "In the Mac app"), and a cash wallet has to be named before it can be kept by hand.
   Recommendation: yes, in PR 9, through `accounts.name_account` as the pages do.
4. **Rename a merchant for every row at once?**
   Recommendation: not in this milestone; a payee is tidied per entry, the bank's words stay beside it, and suggestions never read the tidied name.
5. **Tags and links?**
   Recommendation: not in this milestone; a note covers what they are used for day to day, and the base's own tags stay in the books as they are.
6. **Never change a bank's date or amount, even when the bank is wrong?**
   Recommendation: never, on an account whose balance its bank reports; the note says what happened, and the books stay in step with the bank.
7. **Tell a joint account's other owner who changed an entry?**
   Recommendation: yes, by name, in the entry's history, which only its owners see.
8. **Retire the outside ledger with brokerages still frozen?**
   Trades and dividends come in a later milestone, so after editing ships a brokerage's history stays where the base left it.
   Recommendation: retire the bank sync and the outside ledger once editing ships anyway, as hearth's books then hold everything the household does day to day, and its brokerages' trades come later.
9. **Close an account kept by hand, a loan paid off or a wallet emptied?**
   Recommendation: a small change after this milestone; counting it at zero says what it holds meanwhile.
