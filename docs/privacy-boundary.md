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

The dashboards and review add five more decisions, each in `hearth.access` and asked only through the gate:

- A failed balance check names an account and amounts, so its detail is shown only to those who may see that account's balance; everyone else reads one sentence that names nothing.
  Beancount's own error messages are never passed on, because they name accounts and amounts.
- A connection to an institution is its holder's alone, joint accounts included; the household learns only how many accounts are behind, never which or whose.
- Only someone who holds a security in an account they own may enter its price by hand, and it is stored as theirs, never as the source's.
- Only an owner may review an account's rows; sharing transactions shares what is in the books, not the right to decide how it is booked.
- How much of a total rests on unreviewed rows is shown only for parts the viewer may review or whose transactions they may see; for anyone else the page says their share may include unchecked rows, the same way whether it does or not, so no pile can be counted by subtraction (`docs/review.md`).

## Where it is enforced

- `src/hearth/access.py` holds the rules.
- `src/hearth/disclosure/gate.py` is the only module that imports `hearth.access`: every decision in the application is asked there.
- The rest of the `hearth.disclosure` package (`household`, `people`, `worth`) is the only code that reads the books and ownership for a page.
  Each module receives the gate and asks it before computing anything, so a figure the viewer may not see is never computed into a view.
  Withheld figures are the value-less `WITHHELD` sentinel.
- Pages under `src/hearth/web/` render only the views `Disclosure` returns, and never touch the books' raw data.
- Figures come from the books (`src/hearth/books.py`): the beancount ledger as loaded and valued by one recompute, daily and on request, never on page load.
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

Tokens are stored encrypted at rest under a master key held in the macOS Keychain; see `docs/vault.md`.
