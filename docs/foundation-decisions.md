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

The foundation hid such a total and deferred decision 3's range to the dashboard work.
The dashboards replaced both with a labelled partial, under decision 3 as amended; see [privacy-boundary.md](privacy-boundary.md).

## Private transactions are absent, not masked

The approved mock showed another member's transactions as masked rows such as `-xxx.xx`.
Masked rows still reveal how many transactions there were and roughly how large each was, so a private section shows "Not shared" and nothing else.

## The ledger

The foundation read figures through an in-memory stand-in for the ledger.
That is gone: figures now come from the beancount ledger itself, through `hearth.books` (see `docs/dashboards.md`).

## Institution tokens

The foundation deliberately created no token table, because nothing used one yet.
The vault has since landed as designed: envelope-encrypted tokens under a master key in the macOS Keychain, with no plaintext column (see `docs/vault.md`).

## Passwords an admin hands out

When an admin adds a member or resets a password, hearth generates a one-time password, so an admin never chooses anyone's long-term password.
The member must choose their own at the next sign-in, and until then the one-time password opens nothing but that form.
An admin cannot reset their own password from the web; they change it like anyone else.
