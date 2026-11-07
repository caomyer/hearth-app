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
| `read` | `/item/get`, `/accounts/get`, `/transactions/sync` | `hearth serve --approve-plaid read`, `hearth plaid sync --approved`, `hearth plaid adopt ... --approved` |
| `remove` | `/item/remove` | `hearth serve --approve-plaid remove`, `hearth plaid remove ITEM --approved` |
| `holdings` | `/investments/holdings/get` | `hearth serve --approve-plaid holdings`, `hearth plaid holdings --approved` |

Connecting a bank needs both `link` and `read`; approving one never approves another.
Reading holdings is its own operation, not a `read`: every `read` call asks for something an Item already has, while the first holdings call adds Plaid's Investments product to an Item that only offered it, and Plaid bills that product per Item from then on, until the Item is removed.
So a run approved to sync never adds a product to anyone's Item, and the household decides that separately.
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

## Banks that sign in on their own page

Most large banks sign a member in on their own site through OAuth, not inside Plaid's window.
Plaid Link does this one of two ways, and decides which itself: it opens the bank in a window of its own over hearth's page, or the bank takes over the tab and Plaid brings the member back to hearth afterwards.
Which one a bank gets cannot be told in advance: Plaid reports `oauth` for the institution, not for the way it will open, and banks that report the same work one way or the other.
Plaid's own guidance is to support both ([Plaid's OAuth guide](https://plaid.com/docs/link/oauth/)).
Hearth always supports the window of its own, and the tab taken over whenever it is served over HTTPS, which Plaid's production requires of the way back.

**The window of its own.**
Every hearth page sends `Cross-Origin-Opener-Policy: same-origin`, which cuts off any window a page opens to another site.
On the link page that broke Link: its `window.open` for the bank came back empty, exactly as if the browser had blocked it, while the bank's window still opened, the member still approved access, and Plaid created the Item, holding a slot, with Link never learning of it.
The two link pages, `/connect/link` and `/connect/oauth`, therefore send `same-origin-allow-popups`, which keeps the windows those pages open and nothing else: a window from another site that opens hearth is still cut off.

**The tab taken over.**
This needs three things, and hearth has all three when served over HTTPS.
The Link token carries a `redirect_uri`, hearth's `/connect/oauth`; that route is where Plaid brings the member back; and Link is opened again there with `receivedRedirectUri` set to the full address the browser arrived at, with the same Link token it began with.
Hearth keeps no Link token: the link page puts it in that tab's session storage until Link finishes either way or Plaid stops accepting it, and the page Plaid comes back to reads it from there.
A token is good for one attempt only; another attempt, in that tab or any other, gets a new one.
Coming back to a tab that no longer holds its token is said plainly, never shown as a member closing Plaid.
Neither page needs more of the content security policy than the window of its own does: the bank's page replaces hearth's at the top of the tab, which no frame or script rule governs.

**The redirect URI.**
Plaid's production accepts only an `https://` redirect URI; plain `http://localhost` is allowed in its sandbox alone.
So `hearth serve` offers Plaid a way back only when it serves HTTPS itself:

```sh
mkcert -install && mkcert localhost      # brew install mkcert: a certificate this Mac's browsers trust
uv run hearth serve --tls-cert localhost.pem --tls-key localhost-key.pem --approve-plaid link --approve-plaid read
```

The redirect URI is then `https://localhost:PORT/connect/oauth`, `https://localhost:8000/connect/oauth` on the default port and `https://localhost/connect/oauth` on 443, whose port a browser leaves out of its address, and always `localhost`, whatever `--host` says, so the address to register never depends on how a page was reached.
`hearth serve` prints it at start-up and the connect page shows it.
Plaid refuses a Link token whose redirect URI is not one of the Plaid account's allowed redirect URIs, character for character, so register it in the Plaid dashboard, under the API settings' allowed redirect URIs, before connecting: a refused token costs nothing, since no Link opens.
Link then opens only from the redirect URI's own address, so the member comes back to the browser session and the tab storage they left from; from any other, such as `127.0.0.1` for `localhost`, the page names the address to use and sends nothing.

Served without a certificate, hearth sends no redirect URI, and Link opens every bank's own sign-in in a window of its own, as it did before.
Whether a given bank needs the tab taken over is known only by trying it: Plaid opens the window of its own whenever it can.

**When Link closes without connecting.**
Link says why: Plaid's error type and code, the step the member reached (`status`), the bank, and Plaid's request id, which is what Plaid support asks for.
Hearth keeps all of that in the runtime log and tells the member what is true:
closing before signing in anywhere connected nothing and used nothing;
closing after a bank's own sign-in opened, or after coming back from one, or on an error, may have left an Item at Plaid that hearth never received, so the member is told to check their Plaid dashboard before trying again, with Plaid's code and request id.
Link's own messages are free text and are neither logged nor carried to the page.

## The runtime log

Hearth writes `hearth.log` in its data directory, readable only by the user it runs as, kept to a few rotated files of 1 MB.
`hearth serve` and `hearth plaid` write to it.
It holds every Plaid call by its path and environment, and how it ended: succeeded, refused with Plaid's error type, code and HTTP status, unreachable, or unreadable, each with the request id Plaid gave it.
It holds each new connection by member id, institution and Item id, whether linked or adopted from outside hearth; each return from a bank's own sign-in; and each time Link closed without connecting, with Plaid's codes, the step reached, the institution and Plaid's identifiers.
It never holds a request or response body, an access token, a public token, a Link token, a client id or secret, a balance, an amount, an account number, an account name or a payee.
The client logs only what it chose to read from a response, and every value that came from a browser is checked against the shape Plaid gives it before it is written (`hearth.runlog`, `tests/plaid/test_runlog.py`).
It is owner-only (0600), rotated files included, inside the 0700 data directory.
That is acceptable because `books.beancount` beside it, also 0600, already holds every transaction with payees and amounts, so the log adds nothing a reader of that directory could not already have.

## Adopting a connection made outside hearth

A household may already hold working Items that another tool linked through the same Plaid account.
Linking each bank again would spend a connection on an Item the household already has, so `hearth plaid adopt` takes one in instead, with no Link flow and no slot.

```
hearth plaid adopt --member USERNAME --token-file PATH --approved
jq -r '<where the token is>' tokens.json | hearth plaid adopt --member USERNAME --token-file - --approved
```

The access token comes from a file, or from standard input given `-`, never from the command line, where the shell's history would keep it and every process on the machine could read it.
It is never printed or logged, and is sealed in the vault exactly as a linked token is (`docs/vault.md`).

The first run only reads, and writes nothing.
It verifies the token against Plaid through the Plaid account the member's next connection would use, then says what the Item offers: its institution, when Plaid made it, each account with how many posted transactions Plaid has for it and over which days, the earliest transaction of all, and whether its holdings can be read without linking again.
What it reports is read from Plaid, from the beginning of the Item's history, so it is what the Item offers, not what another tool kept of it.
Plaid does not say how many days an Item was linked with, so the window starts on or before the earliest transaction, and the report says so.
That report is the point: an Item's history is fixed when it is made, so an Item an older tool made with four months keeps four months for good.
Adopting it is right for a bank hearth does not reach yet, and wrong for one hearth already holds with two years.

Run it again with `--confirm ITEM_ID`, naming the Item the first run showed, to adopt it.
Everything is verified again; a token for any other Item is refused.
The Item becomes the member's own connection, as if they had just linked it: it is listed, synced, mapped and imported like any other, its first sync runs at once, and its accounts wait on Connect to be confirmed.
If that first sync cannot run, the Item stays adopted and the command says why; the next sync brings it up to date.
It is counted in hearth from then on and no longer among those used elsewhere, so the connections left do not change.
Its `days_requested` is the 730 hearth asks of every connection, so an account it reaches less far back is marked short, with its first day, like any other.

What it refuses, writing nothing:

- A token made in another Plaid environment than the member's Plaid account's, before anything is sent: a token names its environment.
- A token Plaid does not know under the member's Plaid account: an Item's token works only with the client id and the environment that made it.
- An Item hearth already holds, alike whoever holds it.
- An Item reaching an account a connection hearth holds already reaches, by institution and last digits as `Registry.connected_twice` tells: every transaction would come twice, and hearth's own connection may reach further back.
  The report says how far back each reaches when the connection is the member's own; of another member's it says only that hearth reaches the account, never through whose connection nor since when.
  It weighs them account by account: it says to keep hearth's own only when that reaches every account the Item reaches at least as far back, and to remove it first only when the Item reaches every account hearth's own does, each at least as far back and one further.
  Otherwise it gives no verdict: it names the accounts each reaches further back and the accounts only one of them reaches, and leaves the choice to the member.

A member adopts only into their own connections, through their own Plaid account or the household's once they chose it, as on Connect: the connector the command uses is built by `hearth.disclosure` for that member.

Once adopted, hearth holds the Item's token, so `hearth plaid remove ITEM --approved` can end it and free its slot when the household no longer wants it.
An Item hearth refused to adopt still holds its slot until whatever holds its token removes it.

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
An investment account's transactions do not come through `/transactions/sync`; its holdings' prices come through Plaid's Investments product instead (see "Holdings" below), and its trades and dividends not at all.
Plaid prices positions for today only, so nothing here implies a price history.

## Mapping, confirmed

Each account a connection reaches feeds exactly one ledger account, confirmed by its member, never inferred.
A confirmed account is an account in hearth's own books from the next recompute, which confirming starts (`docs/books.md`).
The choices are a new ledger account, owned by the member and any co-owners they pick, or an account they already own, whose owners stay as they are.
An account the member named because hearth does not see it (`docs/books.md`) is not offered yet: what members moved into it would count a second time beside the bank's own rows until those earlier answers are checked against them.
A new account starts as a proposed ledger name the member can edit, with a name for the household that defaults to the institution's and the bank's names for it and is kept where `hearth add-account --name` keeps it.
When the bank calls several of its accounts the same, such as several cards, the proposed ledger name and the default household name of each carry its last digits; an account called like no other keeps the plain name.
An account connected again after its member removed the connection it came through defaults to the account it fed before, so its history stays in one account (`Registry.fed_before`, which tells the same real account as `connected_twice` does and looks only at that member's own removed connections).
It does so only while that account is still one of the member's own and no other connection feeds it; otherwise the proposal is a new account as usual, and the page says nothing of why.
A typed ledger name must be a beancount account under `Assets` for a bank or investment account, or `Liabilities` for a card or loan, and must not be taken by another row on the page.
A member cannot file into anyone else's account, nor claim an account already in the ledger that nobody owns, since either would hand over its history.
So a typed name is only ever a new account: one the books already have, or one inside or above it, is refused with one answer, the same whoever owns it and whichever of the three it is, and the owners of a new account always include the member.
That answer says something is at, under or above the name and nothing else; `docs/privacy-boundary.md` states that residue and why it is accepted.
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
Only confirmed accounts are imported, from the next recompute, which confirming starts (`docs/books.md`).
What a connection brings in reaches the books only through `hearth.plaid.booking`, which marks every entry it books with `source: "plaid:<item_id>:<transaction id>"` (`connections.plaid_source`): only such entries let a connection's last sync vouch for an account's coverage (`docs/real-ledger.md`).
The format is parsed (`connections.item_of`), not a loose convention, so it is written only through `plaid_source`.
A sync counts as booked once every row it fetched is in the books: each recompute notes every connection's last successful sync before it reads the feed, so a sync that finishes in between vouches only for rows the books already hold, and the next recompute, which the sync starts, holds the rest.

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
`hearth plaid sync --approved` runs the same sync and recompute as the morning's, through one path (`hearth.refresh`), and says both what each connection fetched and whether the books now hold it.
A sync is only worth what it changes in the books, so there is no fetch-only sync.
When the recompute after it fails, the command says what went wrong and when the books file was last written, which stays as it was, and exits 1, as it does when any connection did not sync; what was fetched is kept and is in the books from the first recompute that succeeds.
A `hearth serve` running beside it takes the change into its pages within seconds (`docs/dashboards.md`).

### Holdings

What a brokerage account holds, and its brokerage's price for each holding, are today's and no other, so hearth asks for them when the connection is first synced, at start-up and every morning when started with `holdings` approved, after the sync, and whenever `hearth plaid holdings --approved` is run (`hearth.plaid.holdings`), which recomputes after it and reports the books as `hearth plaid sync` does.
Every working connection that reaches an investment account is asked, whoever holds it: a price is a fact about the market, and pages show it only beside a position its viewer may see.
What each account holds is kept in the feed with when it was said, every answer of it, and the books hold the latest (`docs/books.md`, "What a brokerage holds").
A price is kept even when no position in the books holds what it prices, as when an inherited ledger keeps the account and holds none of the fund: a day not asked can never be priced later, so the price is kept for when one does, and the morning's log names each such fund and says why.
Each price is stored in the app database's `price` table under source `plaid`, with the Plaid security it priced, the day it was current and the time it was fetched (`docs/valuation.md`).
A connection that cannot be asked stops only itself and says why with Plaid's own error code: an institution without the product (`PRODUCTS_NOT_SUPPORTED`), an Item whose member never consented to share investments (`ADDITIONAL_CONSENT_REQUIRED`, which only Link can ask for), or data Plaid is still preparing (`PRODUCT_NOT_READY`, collected the next morning).

An Item that lists `investments` among its available products, as Plaid's `/item/get` says and `hearth plaid adopt` reports, can be asked with no new sign-in and no Link update mode, so hearth opens none.
New Items ask for it as an optional product at Link for the same reason.

This is `/investments/holdings/get` only: positions and their prices.
A brokerage's trades and dividends are `/investments/transactions/get`, a separate call that Plaid bills separately, and hearth does not make it.
So a brokerage account now shows a value, and still shows no trades: what it holds is known from the day it was asked, and how it came to hold it is not known at all.

## Handing rows to a ledger kept elsewhere

A household that keeps its own beancount ledger in fava takes the connections and the rows from hearth and nothing else.
`hearth export --member USERNAME` writes one file, by default `export.json` in the data directory, and makes no live call.

```sh
uv run hearth plaid sync --approved
uv run hearth export --member <username>
```

The file (`buzz.plaid.v2`, `hearth.plaid.export`) holds, for every account the member owns: what the account is, how its connection stands, what the bank last said it holds, and every posted row, each transaction once.
Amounts are Plaid's own strings, positive for money out.
A row's `pair` carries what `hearth.plaid.pairing` knows, because a card's side of a payment rarely has Plaid's payment category.

Hearth reads no ledger to write it, so choosing which rows a ledger still lacks is the other side's job.
The file holds one member's accounts, is readable only by its owner, and names the other side of a pair only if the same member owns it, because a ledger outside hearth has no privacy boundary of its own.

## Where it is kept

- The app database holds connections, never money: Items, their accounts and mappings, credential sets' allowances, and uploaded statements' names (`hearth.db`).
- The feed store, `feed.sqlite3` beside it, holds what Plaid sent: transactions, balances, every answer of what each brokerage account holds, and the record of every sync (`hearth.plaid.feed`).
  It is a staging store for the import, not a second store of record.
- A brokerage's price for each holding is in the app database's `price` table with its provenance, beside every other price: a price is a fact about the market, not about the household (`docs/valuation.md`).
  What the household holds is the household's, so it stays in the feed.
- Access tokens and Plaid keys are in the vault, sealed under the Keychain's master key.
- Uploaded statements are in `statements/` in the data directory, readable only by their owner on the machine.
- What hearth asked of Plaid, and how it went, is in `hearth.log` in the data directory (see the runtime log, above).

## What is not done yet

- Booking uploaded statements: what a connection fetches is booked (`docs/books.md`), what a member uploads is kept but not yet read.
- Carrying an account an inherited ledger holds on from where that ledger stops: the import leaves such an account to the ledger.
- Valuing a brokerage at a month end from the holdings kept that day: the books hold the latest holdings only, so no net worth history is drawn through a part that holds a brokerage.
- Bringing in a brokerage's trades and dividends (`/investments/transactions/get`).
- Reading an account its bank sent no transactions for in a complete history as quiet: its balance is known (`docs/books.md`), its months are not.
