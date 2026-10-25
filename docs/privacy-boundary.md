# The privacy boundary

Each household member decides what the rest of the household can see of their finances.
This document describes how the server enforces that, and how the tests prove it.

## The rule

There are six facets of a member's finances, defined in `src/hearth/sharing.py`:
net worth total, monthly spending total, monthly income total, spending by category, transactions, and account balances.
A new member shares the net worth and spending totals and nothing else, and no facet covers more than its title says.
Monthly income is private until its member turns it on, so household income and the savings rate are labelled "without" each member who has not.

For any viewer, facet, and set of owners, `hearth.access.may_see` decides:

- An owner always sees what they own.
  A joint account has several owners, so every one of them sees its detail.
- Anyone else sees a facet only when every owner shares it.
  One owner of a joint account cannot expose the other owner's detail alone.
- Being an admin grants nothing.
  Admins create members and reset passwords; they do not read anyone's money.
- Something with no owner is seen by nobody.

Member-level facets (the totals and spending by category) have one owner: the member.
Account-level facets (balances and transactions) have the account's owners.

A member's net worth, spending total and category breakdown cover only the accounts that member owns alone.
No part of a joint account is ever in a member's figure, so nothing about a joint account can be worked out by subtracting what one owner shares.
Joint accounts form their own line on the household page, one per set of co-owners, whose net worth is the sum of their joint accounts' balances and whose spending is those accounts' spending for the month.
A joint line's net worth or spending is shown only when `may_see` allows that facet for its owners: every owner shares it, or the viewer is one of them.

A household figure is a sum over parts, and only over parts the viewer may see that figure of.
When every part is visible it is the household total.
When some part is withheld the figure is labelled "without" each withheld part, with that part attached to the number as "+ Sam, not shared", so it never reads as complete; every part in it is one the viewer may see on its own, so nothing shown can be taken apart into something withheld.

Decision 3 was amended by firstmate on 2026-10-18 under authority the captain delegated in writing ("you can decide on my behalf because I am going to sleep").
The captain's original criterion asked for a range.
A range bounds the withheld value from BOTH sides and therefore reveals more about the member who withdrew than a labelled partial does, so the amendment makes the product more private, not less.
The captain has not yet reviewed this amendment; it is listed for him in the overnight brief and he may overturn it.

Spending that went through accounts nobody owns, or through several owners' accounts at once, belongs to no part; it is in nobody's figure, its amount is never shown, and a household figure says it leaves it out.

Detail that is finer than its facet goes with the finer facet.
Day-by-day spending, and "typical by the 18th", read off when money went out, so they need the transactions facet, not the monthly total.
The same holds for categories: a category's "typical by this date", and the day a fixed bill was paid with its single payment, appear only where the viewer may see the transactions of every part in the panel.
Anyone who sees only the category facet gets each category's amount for the month so far, with a fixed bill as an ordinary category row.

The day a member's data stops is the day their money last moved, which is transaction detail too.
A viewer who may see the transactions of every part behind a figure is told that day, once it is more than a week ago, and whose accounts stop there when they may see those accounts.
Anyone else reads one fixed sentence on every such figure, whatever the day: "The accounts behind this figure end in October 2026."
Its month is the earliest month any of the parts ends in, and nothing about it, whether it appears, its wording, or whose it names, depends on a day.
Which months count as complete is decided the same way, by month alone (`figures.covers`).
An account's "Updated" day and the days each of its sources covers go with the transactions facet too; a viewer who sees only its balance reads the month its history starts.

Why an account's balance is not known (its bank reported none, a sync of it did not finish, it was connected again) is what its connection did, and a connection is its member's alone.
So only the account's owners are told why; anyone else who may see its balance reads that it is not known (`access.may_see_why_unknown`).
The same holds when a balance its bank reported is not what its transactions add up to: the bank's figure, the day it covers and the difference go to the account's owners, who alone can review it, and everyone else reads that the figures wait on one member's records.

### Sharing a total means sharing when it changes

A member who shares a total shares every value it takes.
Someone who looks at it day by day can tell on which days the sharer's money moved, because those are the days the total changed.
They cannot tell how much moved in any one account, where it went, or what it was for.
This is how a shared total works, not a gap in the boundary, and nothing tries to hide it.
Anyone unwilling to reveal which days their money moves should not share the total.

The dashboards and review add five more decisions, each in `hearth.access` and asked only through the gate:

- A failed balance check names an account and amounts, so its detail is shown only to those who may see that account's balance; everyone else reads one sentence that names nothing.
  Beancount's own error messages are never passed on, because they name accounts and amounts.
- A connection to an institution is its holder's alone, joint accounts included.
  Only its holder is told it stopped updating, and on which day.
  When it stops, its accounts' data stops there too, which the figures say by the rule above for where data stops: the day to whoever may see the transactions of every part behind a figure, and to anyone else only the month, in the one fixed sentence every such figure carries.
  There is no separate notice of how many accounts are behind or since when: a count and a day taken from connections would give away, whenever they changed, the day a member's connection stopped, which is close to the day their money last moved.
  One carve-out: for a ledger account the viewer co-owns, the mapping page shows that it is already fed by another connection, so it is not fed twice.
  The feeding member is never named and neither is the institution.
  On an account with exactly two owners, a co-owner who knows they did not connect it can infer that the other owner did.
  That inference is accepted: both members own the account, and hiding the fed state would make the second owner re-link the same bank and spend one of the household's few connections to create the duplicate feed this refusal exists to prevent.
  An account the viewer does not co-own is never offered, so nothing about it is shown at all.
- Only someone who holds a security in an account they own may enter its price by hand, and it is stored as theirs, never as the source's.
- Only an owner may review an account's rows; sharing transactions shares what is in the books, not the right to decide how it is booked.
- How much of a total rests on unreviewed rows is shown only for parts the viewer may review or whose transactions they may see; for anyone else the page says their share may include unchecked rows, the same way whether it does or not, so no pile can be counted by subtraction (`docs/review.md`).

Connecting institutions adds three more (`docs/connections.md`):

- A member's own Plaid account is theirs alone: nobody else links through it, sees its allowance, or is told it exists.
  The one exception is the fed state of a co-owned ledger account above, and what a co-owner may infer from it.
  The household's Plaid account is open to every member to choose, and choosing it shows how many of its connections are left, never whose the others are.
- Only an admin enters or replaces the household's Plaid account.
- A member files a connection's account only into an account they own, or a new one they will own.
  A ledger account's name is shown only to its owners, on the mapping page, with one residue below.
- The ledger account hearth proposes for a new account is computed only from the accounts the member may see, so it discloses nothing and is not part of the residue.
  A proposal can therefore meet an account the member cannot see and be refused on submit, like any typed name; the member edits the field.
- The residue, accepted on 2026-10-24: a name a member types and submits may be refused as unavailable, which tells them something is at, under or above that name and nothing else.
  It is accepted because beancount's account names are one namespace, so a taken name must be refused, and the check must consult accounts the member cannot see, or a member could create a name that makes the ledger invalid.
  The declined alternative was namespacing every new account under its member, which closes the probe but breaks inheriting an existing ledger's chart of accounts.

The import adds two more (`docs/books.md`, `docs/pairing.md`):

- A payment from one account to a card the household also connects is booked once, as a transfer marked `*`, never as a `!` row waiting for review.
  This is deliberate, and must not be "fixed" to `!` for consistency with every other imported row.
  The two accounts may be two members', and a review row belongs to the first account's owners: as `!`, it would show one member the other member's card account name as its suggestion in Review, and a ruling one member made on it would move the other member's card.
  Both banks' own rows say the transfer happened, so nothing in it is a guess for anyone to correct.
- That transfer says only "Card payment".
  It is one entry on both accounts, so it repeats neither bank's own description, which is that account's transaction detail.

## Where it is enforced

- `src/hearth/access.py` holds the rules.
- `src/hearth/disclosure/gate.py` is the only module that imports `hearth.access`: every decision in the application is asked there.
- The rest of the `hearth.disclosure` package (`household`, `people`, `worth`) is the only code that reads the books and ownership for a page.
  Each module receives the gate and asks it before computing anything, so a figure the viewer may not see is never computed into a view.
  Withheld figures are the value-less `WITHHELD` sentinel.
- Pages under `src/hearth/web/` render only the views `Disclosure` returns, and never touch the books' raw data.
- Figures come from the books (`src/hearth/books.py`): hearth's own entries over any inherited ledger, as derived and valued by one recompute, daily and on request, never on page load.
  The books file a recompute writes (`docs/books.md`) holds every member's accounts, so it stays in the data directory, readable by its owner alone, and carries no ownership.
- Anything that does not exist and anything the viewer may not see raise the same `Concealed`, which renders one fixed not-found page.
  The answer depends on who is asking, never on what was asked for.

The app is server-rendered, so private data is never serialised into a response for the browser to hide.
There is no JSON API, and `/docs` and `/openapi.json` are disabled.

## How it is tested

`tests/household.py` is an invented household where every private datum is a unique string, with the sharing contract restated there independently of the implementation.

- `tests/test_privacy_boundary.py` crawls every route as every member, across sharing scenarios, with every path parameter a member could try (other members' account ids, invented ids, ledger names, case and encoding variants) and tampered query parameters and headers.
  It fails if any response carries a datum the viewer may not see, and also if a datum the viewer may see never appears, which proves the crawl reached the data.
  It fails if a route exists that it does not know how to attack.
- `tests/test_concealment.py` requires private and missing things to produce byte-identical responses, covers redirects to and after sign-in, open redirects, sign-in error messages and throttling.
- `tests/test_tampering.py` attempts to change another member's sharing, to act as an admin, and to forge requests, and checks nothing changed.
- `tests/plaid/test_connect_pages.py` attacks connections: another member's Item, forged mappings, a full allowance and unapproved calls.
- `tests/test_disclosure.py` and `tests/test_dashboard_boundary.py` attack what the dashboards add: the default dashboard showing every total, a joint balance recovered by subtraction, failure details, connection names, hand-entered prices, other members' review rows and unpriced floors.

These behavioural suites are the enforcement of the boundary.
`tests/test_architecture.py` is an architecture fitness check against erosion: it fails if anything but the gate consults the policy, if anything outside `hearth.disclosure` turns the ledger into figures, or if a page reads the books directly, which is a call site the crawl might not cover.
It is not proof of the boundary, and it cannot see aliasing, re-exports, `getattr` or dynamic imports.

## Sessions and passwords

Passwords are hashed with Argon2id.
A session cookie is a random token with an HMAC, `HttpOnly` and `SameSite=Lax`; the database stores only a hash of the token, so sessions can be revoked and a copy of the database cannot be replayed.
Every form carries a per-session token, and state changes started from another site are refused.
Failed sign-ins are throttled per username (whether or not it exists) and per client.

An admin can reset a member's password.
That hands the admin a password that works as the member until they change it, so a reset signs the member out everywhere, forces a new password at next sign-in, and is recorded in the audit log, which the member can read under Activity.

## Institution tokens

Tokens and Plaid keys are stored encrypted at rest under a master key held in the macOS Keychain; see `docs/vault.md`.
What Plaid sends is kept in the feed store, apart from the app database, and no page shows a fetched transaction (`docs/connections.md`).
