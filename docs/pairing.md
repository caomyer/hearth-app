# Card-payment pairing

A payment from a checking account to a credit card arrives from Plaid as two rows: money leaving the bank, and money arriving at the card.
Each looks like an ordinary transaction on its own.
Booked as two of them, the payment would be spending on one side and income on the other, so both totals would inflate, and nothing on either row would show why.
It is one transfer between two balance-sheet accounts.
`hearth.plaid.pairing` finds the two rows so the import books the payment once, as that transfer.

Pairing is the import's first step, and books nothing itself; the import (`hearth.plaid.booking`) books each pair once, as a transfer marked `*`, since both banks' rows say it and nothing in it is a guess, and each unpaired leg flagged for review.
`pairing.staged(conn, rows)` pairs the posted rows it is given that are on accounts a member has mapped into the books, and `Pairing.of(transaction_id)` tells the import, for each row, the pair it is in, the unpaired leg it is, or that it is neither.
The import reads the rows from the feed and takes each transaction once before it calls `staged` (see [An account connected twice](#an-account-connected-twice)).
`Pairing` also counts its pairs, its unpaired legs, and how many of those were in a tie (`Pairing.tied`).

## The rule

The rule is beancount_nextgen's (`_pair_card_payments` in `bcng.csvimport.triage`), so the CSV route and the Plaid route pair the same payments, as they already share review ids (`hearth.review.ids`).

- A leg is a row that says it is a card payment: on a depository account, Plaid's card-payment category or the bank's own wording ("Payment to ... card ending in 1234", or one of the issuer patterns bcng knows); on a credit card, money arriving that names a payment, or money leaving that names a returned payment or a refunded credit balance.
  On the card side either Plaid's card-payment category or the wording qualifies a row, because Plaid gives a real card's payments a different category from month to month, contractor income among them, so the category alone would miss most of them.
- A pair is one bank leg and one card leg whose amounts are exactly opposite, as `Decimal`, in the same currency.
  A payment and a returned payment of the same size in the same days pair each with its own kind, never across.
- The bank's row is dated from two days before the card's to five days after it (bcng's `-2 <= gap <= pair_days`, with its default of five).
- A bank row that names the card it pays pairs only with the card of those last digits.
- The closest dates pair first.
  Where a row has two equally close partners, nothing is guessed: neither pairing is made, and none of the rows in the tie pairs later with a partner further away.

The last clause is the one difference from bcng.
bcng's code skips a tied candidate but leaves its rows free, so a row left unpaired by a tie can still pair with a further partner; its own docstring says both are left for review, and here they are.

## A leg nothing pairs with

An unpaired leg is booked flagged, in the review group "Card payment: other side not connected" (bcng's `card-payment-unmatched`), against `Equity:Review:TransferPending`.
It is never booked as spending or income, and never dropped.
Every bank balance stays right, and the transfer-pending account's balance is exactly the money whose other side the books do not hold.
Accepting such a row as proposed settles nothing: the review pile reads `Equity:Review:TransferPending` as no proposal at all, as it reads `Equity:Review:Unresolved`, and no ruling may book a row against either (`docs/review.md`).
A member settles it by naming the other account.

A leg is unpaired for honest reasons pairing cannot tell apart: the other side is at an institution nobody connected, it came outside the window or for a different amount, or it never arrived because a connection stopped.
When the other side does arrive, the next import pairs it.

## Privacy

Pairing crosses accounts, and so crosses members.
It runs in the import over the whole household, and who owns which account is not an input to it: a payment from one member's checking account to another member's card pairs exactly as it would between two accounts of one member.
Nothing it computes reaches a page except through what the import books.
An unpaired leg records nothing about why it is unpaired, so it is the same fact whatever any other account holds: a member reviewing their own leg learns nothing about an account they cannot see, not even that one exists with a payment of the same size.
Pages may not import `hearth.plaid.pairing` (`tests/test_architecture.py`).

## Against a real household's rows

Pairing was run read-only over a real household's feed store, seven accounts at one bank, five of them cards, and checked against that household's own ledger where it reconciles to its statements to the cent.
The two agree on every card payment: each pair is paid from the connected checking account as the ledger says, and each unpaired leg is a payment the ledger books as made from a bank account that is not connected.
No row that is not a card payment was taken for one, and no card payment was missed.

## What pairing does not do

- Transfers between two depository accounts, and loan payments, are not paired here: a loan payment is part principal, part interest.
- Pairing does not find duplicated rows; the next section says why.

## An account connected twice

Pairing assumes each transaction reaches it once.
If it does not, every duplicated leg that has a partner ties, and stays unpaired.
An account's rows arrive twice in two ways.
A bank is connected again without removing the old connection, so both connections send the same rows.
Or a bank is removed and then reconnected: Plaid issues new account ids and pulls two years of history again, overlapping the rows the removed connection already brought in.
`Registry.confirmed()` deliberately includes removed connections, so that what a removed connection brought in stays in the books.
That is exactly why the caller must dedupe: in both cases a payment in the overlap arrives under two account ids with two transaction ids, so its leg has two equally close partners and pairs with neither.

Pairing does not try to detect this.
It cannot tell a duplicated feed from a household that really paid the same card the same sum twice in a day, and guessing would book one of them wrong.
Nor does its tie count tell it.
`Pairing.tied` counts the unpaired legs that were in a tie, for a human reading a run, and is not a stop signal.
Duplication ties only the legs that have partners: a duplicated leg paid from a bank nobody connected stays unpaired and untied, and outside the overlap, older rows from the old connection and newer rows from the new one still pair.
An individual leg still records nothing about why it is unpaired (see [Privacy](#privacy)).

The duplication is a fact about connections, and `Registry.connected_twice()` states it: two confirmed Items that may be at one institution, holding accounts with the same last digits.
Two Items may be at one institution when both have Plaid's institution id and the ids are equal, or when either has no id.
Two Items whose ids differ are never reported, whatever their names.
Names are not compared, because an Item without an id may carry a hint from Link or the fallback name "Your bank".
A missed duplicate would book a duplicated feed, while an extra report only asks the import to look, so an Item without an id counts as possibly at any institution.
It covers both ways in, since either leaves two confirmed Items over one real account.
The import checks it before pairing (`hearth.plaid.booking`).
Where removed connections and a working one reach the same account, each stands for it from the day its history starts, and an older one's rows count only before the history of any connection linked after it starts, which nothing else holds: so removing a bank and connecting it again, however many times, books each transaction once.
The working connection counts as the newest; of removed ones, the one linked later is newer.
Two working connections reaching one account are refused, and the recompute says so: no new transactions are brought in until one of them is removed in Settings, Connections, after which the rule above applies.
Like the rest of the registry, it is for the import pipeline only, and pages may not reach it.
