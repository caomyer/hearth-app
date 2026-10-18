# Reviewing the set-aside pile

`hearth.review` is the engine behind hearth's central claim: nothing is booked that nobody checked.
It has no routes or templates; pages reach it through the access policy, as below.

## The pile

An import sets aside every row it could not book with confidence.
`pile.read_pile` reads beancount_nextgen's `pending.beancount`, where each set-aside row is a `!` entry with two postings: the bank's, and one balancing it against the proposed account, or against `Equity:Review:Unresolved` when nothing could be proposed.
Each `PileItem` carries its review id, group, date, account, amount, the proposed account (`None` when there is none), the categoriser's confidence, the source file and row the import read it from, and where it sits in the pile file.
A row without a review id or a group is refused, because a ruling on it could not be kept.

## Review ids

A ruling must stay on the transaction it was made for.
`ids.review_id` derives a row's id from what it says (account, date, amount, and description with whitespace normalised), never from where it sits in a file, so a re-download that adds, drops or reorders rows changes no id.
Rows that say exactly the same thing, such as two identical payroll credits on one day, are told apart by their order among themselves.
The contract and the hash are beancount_nextgen's own (`bcng.csvimport.triage.review_ids`), so ids agree between the two.

## Rulings

A member can:

- **accept a group**: book every row of the group on their pile against the account it proposes now;
- **set the account** of one row;
- **reject** one row, which is not a transaction and books nothing.

Rulings live in the app database (`review_rulings`, `review_ruling_rows`), append-only and keyed by review id.
They store who ruled, when, and why (an optional note), and never an amount: the ledger is the only place money is recorded.

Which ruling decides a row:

1. A ruling on the row itself wins over a ruling on its group.
2. Among rulings of the same kind, the later one wins, so a ruling is taken back by making another.
3. An accept is bound to the proposals its author saw.
   It records, for each row it covers, the account that row proposed when it was accepted, and books the row only while the import still proposes that account.
   If a later import proposes something else, or nothing, the row is pending again, saying its proposal changed since it was accepted.
4. Accepting a group never covers a row the import proposed nothing for, so a proposal that arrives later is not booked by the old accept; that row stays pending until someone rules on it.
   If no row of the group has a proposal, the accept records nothing and says so.

A group ruling covers exactly the rows that were on its author's pile when it was made.
A row that arrives in the group later is pending until someone looks at it.
There is deliberately no auto-accept, after a delay or otherwise: it would book transactions nobody looked at.

`desk.resolve` gives every row's decision for the import that books them; it is not a view for anyone to read.

## Who reviews what

A member reviews only rows on accounts they own, and never sees anyone else's pile, not even a count.
A row on another member's account behaves exactly like a row that does not exist: both raise `hearth.disclosure.Concealed`.
On a joint account either owner may rule, and the later ruling holds for both.
A row on an account nobody owns is reviewed by nobody.

The desk does not decide any of this itself.
It is handed `hearth.access.may_review(member, owners)` and asks it about every row, both for the member at the desk and for the author of each ruling, so a ruling stops counting once its author no longer owns the account.
`may_review` is true for an owner and nobody else.
Unlike `may_see`, sharing grants nothing here: a member who shares their transactions shares what has been booked, not the right to decide what gets booked.
Being an admin grants nothing either.

Pages reach the desk only through `hearth.disclosure`, the one module that consults the access policy: `Disclosure.review_desk` builds the viewer's desk, and `disclosure.review_decisions` gives the import every row's decision.
`tests/review/test_policy.py` checks that sharing everything still opens nobody else's pile and that being an admin opens none.
`tests/test_architecture.py` guards against a second module consulting `may_review`, as an erosion check rather than proof.
