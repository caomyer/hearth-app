# The privacy boundary

Each household member decides what the rest of the household can see of their finances.
This document describes how the server enforces that, and how the tests prove it.

## The rule

There are five facets of a member's finances, defined in `src/hearth/sharing.py`:
net worth total, monthly spending total, category breakdown, individual transactions, and account balances.
A new member shares the two totals and nothing else.

For any viewer, facet, and set of owners, `hearth.access.may_see` decides:

- An owner always sees what they own.
  A joint account has several owners, so every one of them sees its detail.
- Anyone else sees a facet only when every owner shares it.
  One owner of a joint account cannot expose the other owner's detail alone.
- Being an admin grants nothing.
  Admins create members and reset passwords; they do not read anyone's money.
- Something with no owner is seen by nobody.

Member-level facets (the totals and the category breakdown) have one owner: the member.
Account-level facets (balances and transactions) have the account's owners.

A member's net worth, spending total and category breakdown cover only the accounts that member owns alone.
No part of a joint account is ever in a member's figure, so nothing about a joint account can be worked out by subtracting what one owner shares.
Joint accounts form their own line on the household page, one per set of co-owners, whose net worth is the sum of their joint accounts' balances and whose spending is those accounts' spending for the month.
A joint line's net worth or spending is shown only when `may_see` allows that facet for its owners: every owner shares it, or the viewer is one of them.

A household total is the sum of every member's figure and every joint line, and is shown only when every one of those parts is visible to the viewer.
Otherwise the withheld part could be recovered by subtraction, and a partial total would be a smaller number posing as complete.

## Where it is enforced

- `src/hearth/access.py` is the only place that decides.
- `src/hearth/disclosure.py` is the only place that reads the ledger and ownership for a page.
  It asks `access` before computing anything, and a figure the viewer may not see is never computed into a view.
  Withheld figures are the value-less `WITHHELD` sentinel.
- Pages under `src/hearth/web/` render only the views `Disclosure` returns.
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
  It is the enforcement of the boundary.
- `tests/test_architecture.py` is an architecture fitness check against erosion: it fails if a second module starts consulting the policy or turning the ledger into figures, a call site the crawl might not cover.
  It is not proof of the boundary, and it cannot see aliasing, re-exports, `getattr` or dynamic imports.

## Sessions and passwords

Passwords are hashed with Argon2id.
A session cookie is a random token with an HMAC, `HttpOnly` and `SameSite=Lax`; the database stores only a hash of the token, so sessions can be revoked and a copy of the database cannot be replayed.
Every form carries a per-session token, and state changes started from another site are refused.
Failed sign-ins are throttled per username (whether or not it exists) and per client.

An admin can reset a member's password.
That hands the admin a password that works as the member until they change it, so a reset signs the member out everywhere, forces a new password at next sign-in, and is recorded in the audit log, which the member can read under Activity.

## Secrets still to come

Institution tokens arrive with the Plaid work.
They are to be stored encrypted at rest with a key held in the OS keychain: a table holding ciphertext, nonce and key id per member and institution, never a plaintext column, so a copy of the database alone reveals nothing.
