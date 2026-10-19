# Connecting institutions

A member connects their own institutions through Plaid, and hearth pulls two years of each one's history.
`hearth.plaid` does the work and `/connect` walks a member through it, in the four steps `design/screens/onboarding-*.html` draws.
This document records how it works, what it will not do without approval, and the calls made along the way.

## No live call without approval

Every Plaid call names the operation it belongs to, and the client refuses before anything is sent unless that operation was approved when hearth started (`hearth.plaid.approval`).
The default approves nothing.

| Operation | Plaid calls | Approved by |
|---|---|---|
| `link` | `/link/token/create`, `/item/public_token/exchange` | `hearth serve --approve-plaid link` |
| `read` | `/item/get`, `/accounts/get`, `/transactions/sync` | `hearth serve --approve-plaid read`, `hearth plaid sync --approved` |
| `remove` | `/item/remove` | `hearth serve --approve-plaid remove`, `hearth plaid remove ITEM --approved` |

Connecting a bank needs both `link` and `read`; approving one never approves another.
A page whose step is not approved says so, and sends nothing.
The demo and the tests run against hearth's stand-in for Plaid (`hearth.plaid.fake`), which answers only its own `demo` environment, in-process; calls to it reach no bank, so they need no approval.
A real Plaid account entered in the demo is still refused.

## Whose Plaid account

A free Plaid production account allows about ten Items, and an Item is one sign-in at one institution however many accounts it reaches.
The scarce thing is banks, not accounts, so every Plaid account a member brings adds a whole allowance.

- A member's own Plaid account is the default and is recommended on the page.
  Their data then never passes through an account registered to someone else.
- The household may keep one Plaid account, entered by an admin, as a fallback.
  A member links through it only after choosing to, and the page names who registered it.
- Each set's client id and secret are sealed in the vault, bound to the set (`docs/vault.md`).
  Neither is ever shown again, logged, or put in an exception.
- A set with connections keeps its client id and its environment: an Item's token works only with the client and the environment that created it, so switching either would strand every connection, still holding its slot.

## The connection count

The page shows how many connections are left, "6 of 10 connections remaining", from the set the member's next connection would actually use, never a household total.
It is the set's allowance, less the Items hearth holds through it, less those the member says are in use outside hearth, which Plaid does not report.

When none is left, the page says so and how to make room, and Plaid Link is never opened: the link step refuses before asking Plaid for anything.
Everyone who chooses the household's set sees how many of its connections are left, never whose the others are.

## Listing and removing Items

Settings, Connections lists each of the member's Items with its institution, how many accounts it covers, when it was connected and its last successful update.
That last column is the point: an Item left over from connecting a bank again looks like a live one without it, and still holds a slot.
`hearth plaid items` lists every Item in the household with its member and each set's allowance, for whoever runs hearth on the machine.

Removing calls `/item/remove`, which frees the slot and cannot be undone.
If Plaid answers that it no longer has the Item (`ITEM_NOT_FOUND`), because it was removed before or its member revoked access at Plaid, the connection counts as removed and its token is forgotten.
The confirmation names the institution and says what is kept: every transaction already brought in, which lives in hearth's feed store, not at Plaid, every ledger entry and review decision, and where each account was mapped.

To connect a bank again, remove the old Item first, then connect.
That order never needs more connections than the account has.
The one risk is the time in between, when the bank is not connected and an awkward sign-in can leave a member there for a while; the page says so.
A connection that only needs its member to sign in again is fixed in place through Link's update mode, which keeps the Item and uses no new slot.

## History, asked for and received

Every new Item asks for 730 days, the most Plaid allows.
It is fixed when the Item is created and cannot be widened without connecting again.

What each account actually came with is recorded and shown per account, as a bar against the two years asked for.
Plaid sends the last month first and the rest later; until it says the history is complete, the account reads "History arriving", never short.

Plaid sends history after linking, not with it: the first sync usually finds nothing, then the last month arrives, then the rest.
Plaid announces each step with its `SYNC_UPDATES_AVAILABLE` webhook, but hearth runs on the household's own machine with no public address, so it cannot receive webhooks and has no handler for them.
It asks instead.
After a link, a new sign-in or "Update now", a background thread on the server syncs the Item again after a minute, then waits twice as long each time, never more than 30 minutes, until Plaid says the history is complete (`hearth.plaid.sync.check_waits`).
After six hours it stops asking and the daily sync collects the rest.
Each of these syncs is a `read` like any other, refused unless approved.
The thread keeps going when the member leaves the page; the import page says whether a sync is running, when hearth asks next, and when it has stopped asking.
Once complete, an account that started more than 14 days after the day asked for is marked short, with its first day, and the member is offered a CSV statement upload to fill the gap, now or later.
A short account warns and never blocks.
An investment account's activity comes through Plaid's investments data, which hearth does not bring in yet; new Items ask for it as an optional product so a brokerage connected now does not have to be connected again for it.
Plaid prices positions for today only, so nothing here implies a price history.

## Mapping, confirmed

Each account a connection reaches feeds exactly one ledger account, confirmed by its member, never inferred.
The choices are a new ledger account, owned by the member and any co-owners they pick, or an account they already own, whose owners stay as they are.
A new account starts as a proposed ledger name the member can edit, with a name for the household that defaults to the institution's and the bank's names for it and is kept where `hearth add-account --name` keeps it.
When the bank calls several of its accounts the same, such as several cards, the proposed ledger name and the default household name of each carry its last digits; an account called like no other keeps the plain name.
A typed ledger name must be a beancount account under `Assets` for a bank or investment account, or `Liabilities` for a card or loan, and must not be taken by another row on the page.
A member cannot file into anyone else's account, nor claim an account already in the ledger that nobody owns, since either would hand over its history.
So a typed name is only ever a new account: one the books already have, or one inside or above it, is refused with one answer, the same whoever owns it and whichever of the three it is, and the owners of a new account always include the member.
That answer says something is at, under or above the name and nothing else; `docs/privacy-boundary.md` states that residue and the open question about it (hearth-nameprobe-1).
The proposal is built only from the accounts the member may see, so it says nothing of anyone else's names; when it meets one they cannot see, it is refused on submit like a typed name and the member edits it.
Two rows on the page cannot go to the same account, nor name new accounts one inside the other.
A household name is given only to a new account: a confirmed row going to an account already in the books, with a name typed that is not the one proposed, comes back held for the member to clear it, never renaming the account and never dropping what they typed.
One ledger account is fed by one connection at most, or every transaction would be imported twice.
An account the member co-owns that another connection already feeds is offered disabled, marked "already receives transactions from another connection", and confirming into it anyway is refused.
For a ledger account you co-own, you may see that it is already fed by another connection.
The feeding member is never named and neither is the institution.
On an account with exactly two owners, a co-owner who knows they did not connect it can infer that the other owner did.
That inference is accepted: both members own the account, and hiding the fed state would make the second owner re-link the same bank and spend one of the household's few connections to create the duplicate feed this refusal exists to prevent.

When two of the targets on the page differ by a single word, such as `Assets:Bank:Checking` and `Assets:Bank:Joint:Checking`, the page names both before anything is confirmed, and puts each bank's balance beside its mapping, so a swap shows up as a number that does not belong.
The check applies to what is sent, not only to what was proposed: when an account chosen from the list or a name typed makes a pair the page did not name, nothing is saved, the page comes back with the pair named and those rows unconfirmed, and the member confirms them again with the warning in view.
A row that cannot be saved comes back the same way, marked, with everything the member typed kept.
Only confirmed accounts are imported.

## Re-sync

`hearth.plaid.sync` asks Plaid only for what changed since the Item's stored cursor.
Each page Plaid sends is stored together with the cursor it moves to, in one transaction, so a sync that stops part way resumes from the last page it stored.
When Plaid says its data changed during a run's pages, every page that run stored is undone, as Plaid's contract asks, and the run starts again from the cursor it began at.
Every run is recorded in the feed store: when, how many pages, how many transactions added, modified and removed, and how it ended.
A run killed mid-way is closed as interrupted, never left looking as if it were still running.
Only one sync of an Item runs at a time, across every path and process: without that, two syncs would page one Item from the same cursor and undo each other's work.
A running run therefore holds a lease on its Item in the feed store, renewed with every page it stores, and any other sync of that Item, from a page, the daily thread or `hearth plaid sync`, is skipped and says so.
The lease names its owner, the process id and the time the system says that process started, because an anonymous lease cannot survive the process that took it.
A run whose owner is alive holds its Item until 15 minutes after its last page; a run whose owner has gone, including one whose process id now belongs to another process, holds nothing, and the next sync closes it as interrupted and carries on at once.
A run that no longer holds its Item is shown as interrupted, so no page waits on a run that cannot finish.
The daily sync and `hearth plaid sync` also leave alone an Item whose history is being followed by a process that is still alive.
`hearth serve` re-syncs every working connection at start-up and every morning when started with `read` approved.

## Where it is kept

- The app database holds connections, never money: Items, their accounts and mappings, credential sets' allowances, and uploaded statements' names (`hearth.db`).
- The feed store, `feed.sqlite3` beside it, holds what Plaid sent: transactions, balances and the record of every sync (`hearth.plaid.feed`).
  It is a staging store for the import, not a second store of record.
- Access tokens and Plaid keys are in the vault, sealed under the Keychain's master key.
- Uploaded statements are in `statements/` in the data directory, readable only by their owner on the machine.

## What is not done yet

- The import that turns fetched transactions and uploaded statements into ledger entries and review rows.
  Until it exists, connected history waits in the feed store; the import page says so.
- Bringing in investment holdings and activity.
- Checking each bank's balance against the ledger once the import has booked the history.
