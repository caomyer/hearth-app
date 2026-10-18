# Judgment calls in the foundation

The product decisions firstmate took on the captain's authority are recorded outside this repository.
These are the further calls made while building the foundation, each one easy to overturn.

## Joint accounts and people who do not own them

Every owner of a joint account sees its detail.
Anyone else sees a joint account's balance or transactions only when every owner shares that facet, so one owner cannot expose the other alone.

A member's net worth, spending total and category breakdown cover only the accounts they own alone, and each set of joint owners has its own line on the household page.
This is firstmate's decision of 2026-10-18, accepted from the design crew's proposal.
Folding half of each joint account into its owners' figures let anyone who could see one owner's solo detail subtract it and recover the joint account, which the other owner never agreed to share.
Keeping joint money out of member figures makes that leak impossible by construction rather than defended against by rules about who shares what.
A joint line's totals reach anyone else only when every owner shares that total, which they do by default.

## A household total when a member withholds their part

The total is shown only when every member's figure and every joint line is visible to the viewer; otherwise the page says it is not shared with them.
Decision 3 asks for a range in that case, with a note that a member has withdrawn.
The range is deferred to the dashboard work, as authorised by firstmate on 2026-10-18.
It is presentation: the current behaviour leaks nothing and never presents a smaller number as complete, and a range needs care so that it cannot be narrowed to the withheld figure.

## Private transactions are absent, not masked

The approved mock showed another member's transactions as masked rows such as `-xxx.xx`.
Masked rows still reveal how many transactions there were and roughly how large each was, so a private section shows "Not shared" and nothing else.

## The ledger is behind an interface

`hearth.ledger.Ledger` has one implementation today, in memory, used by the tests and by `hearth demo`.
A real ledger reader, together with per-member secret storage (an encrypted vault), is on a branch stacked directly on this one and lands after it.
Until that stack lands, `hearth serve` shows every figure as zero; that is expected, not a bug.

## Institution tokens

No table for institution tokens exists yet, because nothing uses one.
The intended design is in [privacy-boundary.md](privacy-boundary.md): ciphertext, nonce and key id per member and institution, with the key held in the OS keychain and no plaintext column.

## Passwords an admin hands out

When an admin adds a member or resets a password, hearth generates a one-time password, so an admin never chooses anyone's long-term password.
The member must choose their own at the next sign-in, and until then the one-time password opens nothing but that form.
An admin cannot reset their own password from the web; they change it like anyone else.
