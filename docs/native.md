# The native app's doorway

The Mac app (`apple/`) shows hearth's figures in native screens.
It computes nothing itself: it starts hearth as a process of its own and asks it for each screen.
`hearth.native` is that doorway, and this document records how it works and what it must never become.

## The wire

`hearth bridge` answers over its standard input and output, one JSON object a line (`hearth.native.stdio`).
Every request carries a number and exactly one of `sign_in`, `sign_out`, `ask` or `act`; its reply carries the same number.

```
-> {"id": 1, "ask": "overview"}
<- {"id": 1, "answer": {"state": "signed_out"}}
-> {"id": 2, "sign_in": {"username": "avery", "password": "..."}}
<- {"id": 2, "outcome": "signed_in"}
-> {"id": 3, "ask": "overview"}
<- {"id": 3, "answer": {"state": "ready", "net_worth": {"value": "162711.83", ...}, ...}}
-> {"id": 4, "ask": "transactions", "with": {"account": "h1LQ...", "limit": 50}}
<- {"id": 4, "answer": {"state": "ready", "total": 412, "entries": [...], ...}}
-> {"id": 5, "act": "sync"}
<- {"id": 5, "outcome": "started"}
-> {"id": 6, "ask": "banks"}
<- {"id": 6, "answer": {"state": "ready", "asking": ["read"], "running": true, ...}}
-> {"id": 7, "act": "accept_group", "with": {"group": "Food and drink: groceries", "rows": {"7a1c9e04b2": "Expenses:FoodAndDrink:Groceries"}}}
<- {"id": 7, "outcome": "accepted", "ruling": 41, "rows": 1, "changed": 0}
-> {"id": 8, "sign_out": true}
<- {"id": 8, "outcome": "signed_out"}
```

- An ask may carry `with`, a JSON object of what narrows it, and an act a `with` of what it is done to; nothing else may.
  A `with` that is not an object, or one beside `sign_in` or `sign_out`, makes the line a bad request.
- An `ask` shows something; an `act` asks hearth to do something (see "Asking hearth to do something").

- There is no socket and no port.
  The only thing that can ask is the process that started the bridge, and the bridge ends when that process closes its end, which the system does for it if it dies.
- A line that is not a request is answered `{"id": null, "error": "bad request"}` and the bridge carries on.
  The refusal never repeats the line, which may hold a password.
- Nothing but replies is written to standard output; anything said for a person goes to standard error.
- `hearth bridge` reads the household in the data directory (`HEARTH_DATA_DIR`, or `~/.local/share/hearth`), and refuses to start where there is none rather than make an empty one.
  It recomputes as `hearth serve` does: at start-up, every morning, and whenever another process changes what the books are computed from.
- `hearth demo --bridge` is the same bridge over the invented household, already signed in as its first member with that member's password. The app's sample runs it.
  Every connection of that household is to hearth's stand-in for Plaid (`hearth.plaid.fake`), so nothing it syncs reaches Plaid or a bank.
- Both write what they ask of Plaid to `hearth.log` in their data directory, as `hearth serve` does (`docs/connections.md`).

## Signing in

A bridge starts signed out, and hearth, not the app, decides who is asking (`hearth.native.bridge`).

- Until a member signs in, every ask is answered `{"state": "signed_out"}`, whatever it names, so an app that has not said who is asking learns nothing about the household, not even which screens there are.
- `sign_in` goes through `hearth.auth.sign_in`, the pages' own sign-in, with its throttling; the app's failures are counted as coming from one client, `native app`.
  Its `outcome` is one of:

  | Outcome | Meaning |
  |---|---|
  | `signed_in` | The member is signed in; asks are answered with their view. |
  | `rejected` | The username or the password is wrong. Which of the two is never said. |
  | `throttled` | Too many recent failures for this username, or from the app. |
  | `must_change_password` | The password is a one-time password. It is changed in hearth's web pages first; the bridge stays signed out. |

- Any `sign_in` signs out whoever was signed in first, whatever its outcome.
- A signed-in bridge holds a session as a page does (`hearth.sessions`) and checks it on every ask.
  A password reset or change that signs the member out everywhere, or a session that has run its course, signs the app out too: its asks are answered `signed_out` again.
- The session is revoked when the app sends `sign_out` or closes its end.
- The password is used once, to sign in, and is never kept, logged or written by the bridge.

The app keeps a member's username and password in the login keychain, one generic password item per household under the service `Hearth` with the data directory as its account (`HearthKit.Keychain`), and hands them to a new engine on every launch, so the engine checks them every time.
If the engine refuses them they are forgotten and the member is asked again; "Sign Out" forgets them too.

## Asking hearth to do something

An `act` names an action, and may carry `with`, what it is done to.
Its reply carries one `outcome` (`hearth.native.common.Acted`).
A review decision's reply also carries `ruling`, the decision an Undo would take back (null when nothing was recorded), `rows`, how many rows it decided or took back, and `changed`, how many of the rows it was shown it left waiting because they changed since.
Every outcome is in `tests/native/fixtures/acted.json`, and a decision's reply in `decided.json`, which both sides' tests read.

These two outcomes are any action's:

| Outcome | Meaning |
|---|---|
| `missing` | There is no such action, it was given something it does not take, or what it names is not on the viewer's desk. The three are one answer. |
| `signed_out` | Nobody is signed in; nothing is done, and nothing is said of what could be. |

### Syncing

`sync` brings the household's connections up to date now, and takes nothing.

| Outcome | Meaning |
|---|---|
| `started` | A sync started, in the background; the `banks` screen follows it. |
| `running` | A sync was already running, and what it brings is what was asked for; none was started. |
| `not_allowed` | The bridge was started approved for nothing a sync asks of Plaid. |

- A sync is the one every sync is: `hearth.refresh.sync_then_recompute`, run by the bridge's `Refresher`, one at a time, so the books always take in what it fetched.
- The bridge syncs on its own too, exactly as `hearth serve` does: at start-up and every morning, when it was started approved for something a sync asks.
- What it may ask of Plaid is decided when it starts, never on the wire: `hearth bridge --approve-plaid OPERATION`, once for each of `read`, `holdings` and `activity` (`hearth.plaid.approval`), and the same for `hearth demo --bridge`.
  `link` and `remove` cannot be approved for a bridge at all.
  The Plaid client enforces the approval itself, as it does for every run of hearth.
- The app starts the bridge approved for exactly the switches the person turned on in its Settings, and starts it again when one changes (`HearthKit.Consent`, `apple/README.md`).

### Reviewing

Every other action is one of the signed-in member's review decisions (`hearth.native.review.DECISIONS`), made through their own desk (`Disclosure.review_desk`) with the calls the review pages make (`docs/review.md`).
Each is bound to what the app showed, as a page's form is: an accept to the account each row proposed then, every other decision to what each row said then (`says`, from the review screens).
A decision that recorded or took back a ruling starts a recompute in the background, as a page's does.

| Action | Takes | Does |
|---|---|---|
| `accept` | `row`, `shown` | Accepts one row as suggested, while it still proposes `shown` (`ReviewDesk.accept`). |
| `accept_group` | `group`, `rows` | Accepts the rows shown of a group that is one decision, each as the account it was shown with (`ReviewDesk.accept_group`). |
| `book` | `row`, `says`, and `category` or `account` | Books one row against a category or another account by its full name, or one of the viewer's own accounts by its id (`ReviewDesk.book_shown`). |
| `exclude` | `row`, `says` | Rules one row not a transaction (`ReviewDesk.reject_shown`). |
| `move_group` | `title`, `rows`, and `category` or `account` | Answers one group of "Where did it go?": every row shown onto one of the viewer's own accounts hearth does not see, or a category (`ReviewDesk.move_group`). |
| `move_row` | `row`, `says`, and `category` or `account` | Answers one such row on its own (`ReviewDesk.answer_shown`). |
| `undo` | `ruling` | Takes back one of the viewer's own decisions (`ReviewDesk.undo`). |

`rows` is an object of review ids to what each showed, `ruling` a whole number, and everything else text.
What a decision says:

| Outcome | Meaning |
|---|---|
| `accepted` | Every row shown was accepted as suggested. |
| `booked` | Saved under the category chosen. |
| `moved` | Moved to the account of the viewer's own they chose. |
| `excluded` | Not a transaction: it books nothing. |
| `undone` | Taken back; its rows wait again. |
| `changed` | Some of what was shown changed since, and was left waiting; the rest, if any, was decided. |
| `not_one_decision` | The group is not one kind of transaction, so it is not accepted all at once. |
| `not_an_account` | The name given is not a full account name. |
| `not_yours` | A row goes only to a category or one of the viewer's own accounts; which rule an account broke is never said. |
| `banks_account` | That account's balance is its bank's, so only the bank's rows go in it. |
| `banks_row` | Its bank counts the row in the balance it reports, so it cannot be excluded. |
| `not_open` | The books hold that category, but close it before a row's day or keep it for another currency, so no row may be booked there (`desk.NotOpen`); told for `book`, `move_row` and `move_group`. |

The app reads an outcome it does not know as `unknown`, says it cannot tell what became of the decision, and asks for Review afresh.

A row, group or ruling that is not on the viewer's desk is `missing`, exactly like one that does not exist, and changes nothing.
There is no auto-accept, and no action accepts more than one group.

### The household's ledger

Three actions bring a household's own beancount ledger into hearth, say whose its accounts are, and match them to the viewer's connected ones, each doing exactly what `hearth import-ledger`, `hearth add-account` and `hearth match-accounts` do, through the same function (`docs/real-ledger.md`, "From the Mac app").
Each is written from the viewer's own view of the ledger (`Disclosure.ledger_desk`, `hearth.disclosure.ledger`), and neither asks anything of Plaid, so neither needs the bridge approved for anything.

| Action | Takes | Does |
|---|---|---|
| `bring_in_ledger` | `path` | Brings the ledger opened from the file at `path` in as the household's base, in the background, then recomputes the books (`hearth.native.importer`); the `ledger` screen follows it. |
| `match_accounts` | `matches` | Matches the viewer's connected accounts, each by its Plaid id to one of the ledger's accounts the `matching` screen offered it, all at once: every one is checked before any is written (`matching.confirm`); then recomputes the books in the background. |
| `assign_owners` | `owners` | Says whose each of the ledger's accounts nobody owns is, each by its name in the ledger to the usernames of its owners, all at once (`accounts.claim_all`), for an admin alone; then recomputes the books in the background. |

| Outcome | Meaning |
|---|---|
| `started` | The import started, in the background. |
| `running` | An import was already running; none was started. |
| `matched` | The account is matched as asked. |
| `refused` | The matches were refused by the same check that offered them, as something changed since they were shown, and none was written; ask `matching` again. Or an account said to be someone's was claimed since it was shown, and nothing was written; ask `ledger_owners` again. |
| `assigned` | Each account is now owned as said. |

- `path` is an absolute path, text of 1 to 1,024 characters with no NUL, as the Mac's open panel gives one; a relative one, which would be read against wherever the engine started, is `missing`.
- Bringing a ledger in, and asking what a file holds, are an admin's (`access.may_bring_in_ledger`): the base becomes every member's books, so it is a household act, as entering the household's Plaid account is.
  Anyone else is told `missing`, for the screen and the action alike.
- The app brings a ledger in once: a household that holds a base already is refused, in a sentence saying so, as is one whose bridge inherits a ledger at `HEARTH_LEDGER`.
  Every import, the app's and the command line's alike, holds the base's lock (`base.locked`, `flock`) from asking whether the household may import through naming the new base, so two at once take turns and the second is refused as it would have been later.
  `running` is told when this bridge is already bringing one in, for this member or another; whose is never said.
  Only the command line replaces a base, and only until a member rules on anything since.
- A member matches their own connections alone (`access.may_use_connection`), onto accounts they own (`access.may_feed`), with every guard the command line has.
  Each account must be one `matching` offered, or the whole act is `missing`, as a page refuses a target it did not offer.
  The one account nobody has claimed that a member may take is one the ledger itself records is that bank account (`matching.recorded`: at least as many of the connection's Plaid ids as content alone needs, five, agreeing on it, reaching the base's last month there), for an account hearth keeps for them; its claim moves onto it, a joint account's to all its owners (`access.may_carry_on`, `matching.Moving`).

## What an answer is

Every answer has a `state` (`hearth.native.doorway`):

| State | Meaning |
|---|---|
| `ready` | The screen's figures follow. |
| `loading` | The books have not been computed yet, or a slow screen is being worked out: ask again. |
| `failed` | The last recompute failed and there are no earlier books; `summary` is the one sentence anyone may read. |
| `missing` | There is no such screen, or none this viewer may see. The two are one answer. |
| `signed_out` | Nobody is signed in to the bridge (`hearth.native.bridge`). |

Money is a decimal string, never a JSON number, so nothing passes through a binary float on its way to the screen.
Days are ISO dates; moments are UTC to the second, always in the shape `2026-10-15T12:00:00Z`.
Every ready answer says what its books are: `computed_at`, `as_of`, `currency` and `recomputing`.

## The screens

| Ask | Takes | Answers |
|---|---|---|
| `overview` | nothing | The household's net worth and its history (`Disclosure.household`). |
| `accounts` | nothing | Every account the viewer may see, in groups: theirs by kind (Cash, Credit, Investments), then those hearth does not see, then others' they may see into, by whose (`Disclosure.accounts`). |
| `account` | `id` | One of those accounts, and the days its history covers (`Disclosure.standing`). |
| `transactions` | `from`, `to`, `account`, `category`, `categories`, `text`, `show`, `within`, `offset`, `limit` | A window onto the journal (`Disclosure.journal`). |
| `banks` | nothing | How fresh the viewer's own connections are, and where the bridge's syncs stand (`Disclosure.banks`). |
| `review` | `order`, `account`, `category`, `group`, `text` | The viewer's own review queue, counted as the desk counts it, in the order asked (`hearth.review.queue`). |
| `review_row` | `id` | One row on the viewer's desk: where it stands, what it may be booked against, and the rows from the same merchant before it. |
| `where` | nothing | The viewer's rows whose other side hearth does not see, grouped so that one answer settles each (`hearth.review.where`). |
| `statement_summary` | `from`, `to`, `valuation` | This period against the same days a year before: headline figures, the compact income statement and why net worth changed (`Disclosure.statement_summary`). |
| `income_statement` | `from`, `to`, `columns` | The income and expense account tree, a cell per month, quarter or year (`Disclosure.income_statement`). |
| `balance_sheet` | `from`, `to`, `valuation` | Every account at the start of the period beside its end (`Disclosure.balance_sheet`). |
| `year_by_year` | `valuation` | Every year the books hold (`Disclosure.years`). |
| `holdings` | nothing | What the investment accounts the viewer may see hold, each holding with its price, value, cost and gain, and whose positions are withheld (`Disclosure.holdings`). |
| `price_history` | `symbol` | Where one security's price comes from, and every price hearth holds of it (`Disclosure.price_history`). |
| `ledger` | nothing | Where the household's own ledger stands, and the import this bridge last ran for the viewer. |
| `ledger_check` | `path` | What a ledger file holds, read where it is and written nowhere, and why it would not be brought in, if it would not. |
| `matching` | nothing | Each of the viewer's own connected accounts, and which of the imported ledger's accounts it is (`hearth.matching`). |
| `ledger_owners` | nothing | To an admin alone, each of the imported ledger's accounts nobody owns yet, by name, and every member, to say whose each is. |

What an ask takes is read strictly in `hearth.native.doorway`, and anything else is answered `missing`, as a screen that is not there is:

- a key the ask does not take, or any key for an ask that takes nothing;
- `id`, `account` and `symbol` that are not text of 1 to 64 characters;
- `from` and `to` that are not real days written `YYYY-MM-DD`, or a `from` after its `to`;
- `category` that is not `Expenses` or `Income`, alone or with parts beneath it;
- `text` that is not text of 1 to 200 characters;
- `show` that is not `everything`, `unreviewed`, `transfers` or `balance_checks`;
- `offset` that is not a whole number from 0 to 1,000,000, and `limit` that is not one from 1 to 200;
- `order` that is not one of `review.queue`'s orders, and a review `category` that is not an account name or `-`, for nothing suggested;
- `categories` that is not a list of 1 to 500 names each as `category` takes them, and `within` that is not `statements`;
- `valuation` that is not `market` or `cost`, and `columns` that is not `month`, `quarter` or `year`;
- `path` that is not an absolute path of 1 to 1,024 characters with no NUL.

Whether the viewer may see what an ask names is for `hearth.disclosure` to decide, never the doorway.
An account they may not see, or for `transactions` one whose transactions they may not see, is answered `missing`, exactly like an id that names nothing.
A category or words that find nothing the viewer may see find nothing, whoever else's books hold them.
No answer repeats the words or the category it was asked for, so an answer never carries what the app sent it.

### Accounts

Each account says what its balance rests on, its `standing`, and every absence is its own:

| Standing | Meaning |
|---|---|
| `matches` | It agrees with the balance its bank last reported (`hearth.plaid.opening`). |
| `holdings` | It is what its brokerage last reported the account holds. |
| `checked` | It agrees with the books' own latest balance check, such as a statement's. |
| `unchecked` | It is what the books hold; nothing in them checks it against a bank. |
| `not_known` | Not known yet (`books.unanchored`); `why` is said to its owners alone. |
| `arriving` | Hearth is bringing the account in now. |
| `unseen` | Hearth does not see it (`books.unseen`), and its balance is never written. |
| `failed` | The latest recompute found it disagrees with its bank; `failed` says by how much to those `FailureNotice` tells. |
| `withheld` | Its balance is not shared with the viewer. |

The balance, its kind and its standing go with the account balances facet.
The day its data is known through (`updated`), whether that is long ago (`behind`), and the day its bank's balance covers (`checked`) are when its money last moved, so they go with the transactions facet.
Its bank's name is given only to the member whose connection feeds it, as is `attention`, which says that connection must be signed in to again; for an account hearth does not see, where its owner said it is.
`reconciling` says an `unchecked` account's bank's balance meets the books only by a difference nobody has confirmed yet in Review, and goes with its balance.
An `account` also says, with its transactions, the days of a carried account's history that neither its imported ledger nor its bank holds (`unseen`, `from` and `to`, `books.unseen_between`), and for a matched account its ledger holds back, the last day its data is known through (`held_back.through`) and the day its bank's transactions are brought in from (`held_back.from`, `books.held_back`).

### Holdings

`holdings` is the net worth page's positions (`hearth.disclosure.holdings`): an account's go with its balances, so a part of the household whose balances the viewer may not see is never listed.
It is named under `withheld` instead, with `in_total`, whether its value is inside the household's net worth, which it is only where that part shares its total, exactly as the net worth page says it.

An account is listed when it is a brokerage's (`books.invested`) or holds anything but cash, with `totals` and `held`, its holdings: securities by what they are called, then its cash, with `is_cash`.
Each holding says what the books hold it as (`symbol`), its `ticker` and `name` where it has them, its `units`, and its `freshness`, `current`, `stale` or `unpriced` (`hearth.valuation.networth`).
Every absence is its own, and none is ever written as zero:

- `price` is the stored price it is valued at, with its `day`, its `source`, when it was fetched (`fetched_at`), and its `age` in days; null for cash and for a holding with no price.
- `value` is null for a holding with no price, which every total then leaves out and counts in `unpriced`, so it is a floor.
- `cost` is what its lots cost in the books (`valuation.holdings.Holding.cost`); cash costs itself.
  It is null where the books hold no cost, as for anything a brokerage reported in hearth's own books, and `gain` is then null too: never a gain against zero.
- `known` is false for an account whose holdings the books do not know yet, which shows no holdings and is in no total (`waiting`); `why` says why to its owners alone (`investment`: its brokerage has not said what it holds, which a bridge not approved for `holdings` never asks).

`totals`, for each account and for all of them, are `value` and `cost` with how many holdings each leaves out (`unpriced`, `uncosted`), `gain` with how many holdings that are not cash it leaves out (`ungained`), and how many accounts are not known yet (`waiting`).

`price_history` answers a security the viewer may see a holding of, and nothing else: one held only in an account whose balances they may not see is `missing`, exactly like one nobody holds, as is cash.
It says where today's price came from (`price`), and `closes`, oldest first, one a day up to the books' own: the price a valuation of that day would take from the sources the books were valued from (`books.price_sources`), with its source.

### Review

The review screens are written from the viewer's own desk (`Disclosure.review_desk`) and nothing else: a member is shown only rows they may rule on, and never another member's queue, not even a count, whatever is shared.
The `overview` says how many of the viewer's rows wait (`review.waiting`, those waiting to be told where their money went among them, as the pages count them) and how much of their own spending this month rests on such rows, at their suggested category (`review.spending`).
It also says, as the viewer's own page does, the rows this month nobody has said what they were yet, such as a new merchant's, which are in no total until placed (`review.unplaced_spending` and `review.unplaced_income`, each `rows` and the money they `moved` that way), and what was booked to expense categories that are not spending, such as a retirement contribution, which no spending figure counts (`review.not_spending`).
Tax is never spending: `review.spending` is without it and `review.spending_with_tax` with what of the month's tax rests on unreviewed rows, both worked out by the engine; `review.tax` is the month's own tax, which no spending figure counts, and `review.tax_unreviewed` how much of it is unreviewed.
`taxed` says whether the viewer's statements hold any tax (`statements.taxed`, judged on what their statements read and nothing else), so the app's switch to count tax as spending applies to the Overview too.
Each is null when there is none, or when the viewer's own totals are not complete enough to show (`docs/review.md`, "Totals that lean on unreviewed rows").
A `transactions` entry names the review it waits on (`review`) only to whoever may make it.

- `review` answers how much waits and what it moves (`remaining`), what the filter leaves (`shown`), how many are reviewed of the `total`, rows that came back (`changed`, `revised`, and `kept`, those whose decision the books do not apply, as `review.decision.guarded` keeps them unreviewed), rows waiting to be told where their money went, counted apart (`elsewhere`), the values it may be narrowed to (`accounts`, `categories`), its `groups` in order, each with its rows by id and whether it is one decision (`one_decision`), and every row the filter leaves (`rows`), in the queue's order.
- A row says why it is suggested in words (`reason`, `review.queue.explain`, the very words the review page gives it): "Dining twice before", "As your ledger books it", "Bank says Restaurant", "New merchant", "Not booked to a category before", or a band an importer's score falls in, never a percentage; whether it may be excluded (`excludable`, false when its bank counts it); and what it says (`says`), which binds a decision to it.
  A row that came back says why in `reason`: its suggestion changed since it was accepted, the bank changed it since it was reviewed or changed how it describes it since it was accepted, or its decision is one the books keep unreviewed, moved onto an account whose balance its bank decides now, or excluded although its bank counts it.
- A group is named for the category its rows suggest, or for the bank's category said to be suggesting nothing (`docs/review.md`, "The queue at a thousand rows"); `least_sure` says, in the same words as a row's `reason`, why the least sure of its rows is suggested as it is.
- A row's `kind` is `transaction`, or `reconciling` for a carried account's difference with its bank (`docs/review.md`, "Where the imported ledger and the bank disagree"): its `narration` says by how much they disagree as of which day, its `reason` what to do about it, and it is confirmed with an `accept` of what it proposes, never accepted with its group (its group is never `one_decision`) nor excluded.
  The app reads a `kind` it does not know as a `transaction`, whose decisions the desk still decides.
- The whole queue is answered at once, as the review page shows it, since a group's accept is bound to every row it shows.

### The ledger

`ledger`, `ledger_check` and `matching` are not of the books, so each is answered before the first recompute ends.
`ledger_check` and `matching` can take seconds on a real household's books, so they are worked out off the wire (`hearth.native.worker`): the first ask starts the work and is answered `loading`, other asks keep being answered meanwhile, and the answer, once there, is handed out once, so the next ask works it out afresh.
An answer is the same one only for the same viewer, whether they are an admin, the same day and where what it is worked out from stands, so one worked out before that changed, or before the viewer was made or unmade an admin, is never handed out.
For `matching` that is everything a recompute reads (`books.standing`); for `ledger_check`, which reads nothing of the books, it is whether the household inherits a ledger and the base it holds (`native.ledger.check_stands`), so a sync that changes the books while the app asks again and again for a check never starts it over.
A new piece of work for a viewer's screen supersedes the one before it, which is asked to stop and must end before the new one starts, so one viewer never has two checks running at once; and any act of the viewer's lets go of every answer being worked out for them, asking each to stop.
A ledger file is read in a process of its own (`hearth.ledger_child`): beancount's parser holds Python's interpreter lock while it parses, so a thread would still stop every other ask.
The child is killed, with every process of its session, once it has read for two minutes, and the check refused, `too_slow`; or as soon as the check is no longer wanted.
The bridge's import runs there too, the base's lock waited on by the bridge before the child starts and handed to it, so an import waiting on another is refused `busy`, never `too_slow`.
The child never outlives the bridge: a guard it forks before reading watches a pipe only the bridge holds the other end of, and when the bridge ends, however it ends, kills the child and takes away the copy it was loading.
Every check's copy is kept in a folder of the data directory held by the system's lock for as long as any process of the check lives, and the next check, import or start takes away every one nobody holds (`base.checking`, `base.sweep_checks`).
What `matching` weighs is kept while everything a recompute reads stands where it stood (`books.standing`), so asking again is cheap and never stale.

`ledger_owners`, to an admin alone (`access.may_assign_owners`), and missing to anyone else, says whose the imported ledger's accounts may be said to be: `imported`, whether a base is in; `you`, the viewer's username; `members`, every member, the viewer first, each `username`, `name` and `hue`; `choices`, each set of usernames an account may be said to be, each member alone and then every two or more together for a joint account; and `accounts`, each of the base's Assets and Liabilities accounts nobody has claimed, by `account`, its name in the ledger, with its `kind`, the days the ledger `opened` and `closed` it, and the `name` the household will see it by.
That is all the ledger's check told an admin before it was brought in, and they hold its file: never a balance or an entry. Income, Expenses and Equity accounts are never anyone's, a row of them seen by whoever owns its other side.
`assign_owners` takes `owners`, an object of those accounts to the usernames of their owners, and says whose each is, all at once through the path `hearth add-account` claims one by (`accounts.claim_all`), then recomputes: `assigned`; `refused` when one was claimed since it was offered, so nothing was written; `missing` for an account not offered, a member who is not there, or anyone but an admin.

`ledger` says `imported`: the day the household's ledger was brought in (`on`), how many files its base holds, and, to whoever may bring a ledger in, how many of the books' Assets and Liabilities accounts nobody has claimed yet, so nobody sees (`unclaimed`).
It says `inherited`, whether `HEARTH_LEDGER` names a ledger, which the app's engine is never given; `importing`, whether this engine is bringing a ledger in now, whoever started it, told only to someone who may bring one in, so they wait on it without learning whose it is; `may_bring_in`, whether the viewer may bring one in now; and `why_not`, why not, to someone who may ask but for where the household stands.
And `bringing_in`, the import this bridge last ran, to the member who started it alone: `running`, `outcome` (`imported`, `unchanged`, `refused` or `failed`), `refused`, `finished_at`, and `books_failed`, whether the recompute after it failed, so the books do not hold it.
A refusal, here and in `ledger_check`, is the engine's word for it, `why`, and `said`, a sentence in the app's words that names no path, no command and no setting (`ledger_import.Refusal`); the command line says the same in its own words.

`ledger_check` reads the file it is asked for as `hearth import-ledger` reads one (`ledger_import.check_ledger`), and writes nothing anywhere, so asking again reads it again.
It says `refused`, why the app's import would refuse it, or null, and `found`, what the file holds, or null where it does not load or the household may not bring a ledger in at all:

- `files`, `entries` (as hearth uses them), `first` and `last`;
- `accounts`, each with its `name`, `kind` (its root), `opened` and `closed`, and `kinds`, how many of each, as a balance sheet orders them;
- `assertions`, how many balance assertions it holds (`count`) and how many fail (`failing`), a failing one refusing it, said by file and line, never by amount;
- `currencies`, the operating currencies, and `commodities`, everything else it holds or prices;
- `worth`, net worth today at the ledger's own prices, a floor (`is_floor`) without what it has no price for (`unpriced`);
- `runs`, beancount's own plugins it runs, and `unused`, every line kept but not used, each `file`, `line` and `what`, with `unused_kinds` counting `plugins`, `custom` and `documents`.

Each file is read once, opened without waiting and judged by what was opened (`ledger_import._gathered`), and beancount loads a private copy of those bytes, gone when the check ends, never the files where they are: so a file swapped for a pipe after it was read is never opened again.
What is read is held to what a base may hold, before any include is followed: regular files, never a folder, a pipe or a device; one iCloud has downloaded; at most 1,000 files and 64 MB in all, and no line longer than 64 KB, counted before a file is parsed; no include by an absolute path, nor one starting with `~`, which beancount reads as a folder named `~` beside the file; no include that matches nothing, nor one whose pattern climbs above the folder that holds every file the ledger reads, even on its way back into it, or has a `..` after a wildcard; and nothing hearth keeps in its data directory, its books and the base it froze among it, whatever path, link or hard link leads to it.
What the copy is then loaded from is checked again before beancount reads it: every include of every copied file finds files of the copy and nothing else (`ledger_import._staged_holds`).
So a copy, kept in the data directory while it is loaded, can never read what lies beside it, hearth's own books among it.
A household that holds a base or inherits a ledger is told why and nothing of the file, as its ledger read again would tell whoever asked every member's accounts.
Beancount's messages are never passed on, only where the first error is, so a file that is not a ledger is never echoed; no answer repeats the path it was asked with, a file being named only by its path within the ledger's own folder, or its name alone; and nothing about the path or the file is logged.

`matching` says `imported`, whether there is a base to match to; `unsure`, the sentence saying hearth cannot tell whether carrying has begun anywhere, while it cannot; `accounts`; and `totals`: how many are `matched` `of` them, how many of their banks' rows the ledger holds already (`in_ledger`), and how many hearth books from their cut-overs on (`booked`), each summed by the engine.
Each of `accounts` is one of the viewer's own connected accounts, never another member's:

- `id` (its Plaid id), `institution`, `name`, `mask`, `kind`, and `state`: `matched`, `kept` (filed as an account hearth keeps itself) or `unmatched`;
- `matched`, the ledger's account it is matched to and the day it was confirmed (`on`), and `fixed`, whether carrying it on has begun, so the match can no longer change (`books.begun`); `kept`, the account hearth keeps it as;
- `proposed`, what the ledger's evidence says it is: `account`, `how` (`id` or `content`), `agreeing`, `elsewhere` and `of`; and `why_not`, why nothing is proposed, or why what is proposed cannot be had: `nothing_sent`, `ids_disagree`, `unplaced`, `no_side`, `unclear`, `wrong_side` or `taken`;
- `choices`, each of the ledger's accounts the viewer may match it to, the proposed one first: its `account`, whether it is `proposed`, what carrying it on there would meet (`facing`), and why matching it there would be refused (`refused`, with its `why` and the `count` and `day` it needs), worked out by the very check `match_accounts` makes (`matching.refusal`);
- `facing`, what carrying it on meets where it is matched now: `cut_over`, `carried`, `postponed`, the bank's `rows`, those the ledger holds already (`in_ledger`: its `count`, `by_id` and `by_content`), those before the cut-over it does not, never brought in (`before`), and those hearth books (`booked`), each a `count` with its first and last day, and the ledger's last entry, balance assertion and pad on the account;
- `reconciling`, whether its bank's balance meets the books only by a difference that waits in Review.

What is said comes from accounts the viewer may name alone: their own, and one nobody has claimed only where it is offered them to move onto.
Evidence that points at any other account, someone else's or one nobody has claimed, reads exactly as no evidence at all, `unclear`, with nothing proposed and no ids counted `elsewhere`; so does a disagreement or a wrong side that involves one; so a member learns neither such an account's name nor, when somebody claims it, that they did.

### Banks

`banks` is not of the books, so it is answered before the first recompute ends, and says nothing of which books are shown.
It says what the bridge may ask (`asking`, of `read`, `holdings` and `activity`), whether it is asking now (`running`), when its last sync ended (`finished_at`), and what stopped that sync before it asked every connection (`stopped`).
Then `banks`, the viewer's own connections and nobody else's (`access.may_use_connection`), each with its institution, when it last synced to the end (`synced_at`), whether its bank wants its member to sign in again (`needs_sign_in`), how many new transactions the bridge's last sync brought from it (`added`, null when the bridge has not asked it), and what went wrong (`trouble`):

| Trouble | Meaning |
|---|---|
| `sign_in` | The bank wants its member to sign in again, which is done in Hearth in the browser: the app never opens Link. |
| `unreachable` | Plaid could not be reached. |
| `keys` | Its access token or its Plaid account's keys could not be opened from the Keychain. |
| `refused` | Plaid answered with an error of its own. |
| `failed` | Anything else; `hearth.log` says what. |

What another member's connection answered is never in it, nor whether it had any trouble: the sync that brought it is the household's, the outcome of each connection is its member's.

### The journal

`transactions` answers newest first, `limit` entries from `offset` (100 unless asked for fewer), with `total`, how many the whole filter leaves, and `counts`, how many each `show` would leave under the other filters.
The engine never sends the whole journal: the app asks for the next window as the person scrolls to it, and starts again from the first when `computed_at` changes.
Without `from` and `to` the journal is this year so far, and the answer says which days it covered.

An entry is one of `transaction`, `transfer` or `balance` (`hearth.disclosure.journal`):

- It is in the viewer's journal when it moves money in an account whose transactions they may see.
  Its `sides` are its postings: the viewer's accounts by name, its categories, money still waiting to be told where it went, and one `elsewhere` side for every other account, whose amount the named sides already add up to.
- A move between two of the viewer's accounts is one `transfer`, never money out and money in.
- A side on an account hearth does not see says only which way money moved, unless the viewer owns every other side of the entry (`access.may_name_other_side`).
- An opening balance is never an entry.
- A `balance` is a check the books hold, on the last day it covers, for whoever may see both the account's balance and its transactions; every check in the books passed.
- `unreviewed` marks a row nobody has checked; `suggested` says its category is hearth's suggestion, and `waiting` what one with no category waits to be told.
- `reason` says, in words, why an unreviewed row is suggested as it is, or why nothing is: "Dining twice before", "As your ledger books it", "Bank says Restaurant", "New merchant" (`docs/review.md`, "Suggestions, and why").
  It is the row's `reason` in `review`, from the viewer's own desk, so the journal and Review never say two things of one row.
  It is told only on a row that names its `review`, one the viewer reviews, and is null on everything else, as it says what the row's owners decided before; an app that does not know it ignores it, and one that does reads it as none when an older engine leaves it out.
- `position` is where it stands in what the filter leaves, which is how the app tells entries apart; it says nothing of the household's ledger.

`categories` keeps the entries booked to any of the categories it names, each exactly, as a statement's line adds up the rows of every category in it.
`within: "statements"` keeps only the rows the viewer's statements read: entries every account of which is in them, so no move to an account outside them, and no balance check.
A statement's figure opens onto exactly its rows by asking for both, with its period.
Narrowed to `categories`, each entry also says its `part`: the part of its `amount` that is booked to them, in its sign, so the entries of a split row add up to the line that opened them; it is null on any other journal.

### Statements

The four statement screens read the parts of the household the viewer may see in full, and nothing else (`docs/privacy-boundary.md`, "Statements"): their own accounts, joint accounts with their co-owners, and another member's only when that member shares every facet.
Every answer says so in `scope`: whose accounts it reads (`parts`), whose it leaves out (`without`), what it is without of accounts hearth does not see (`leaves_out`, in the words a total uses), how many of its balances are not known yet (`awaiting`), while which nothing that adds up balances is a figure, and whether its books hold any tax at all (`taxed`, `statements.taxed`), over all their history, judged on its own accounts alone.
Without `from` and `to`, a statement is this year so far; a `to` past the books' own day ends on it, and a `from` after it, or before the year the viewer's statements begin in (or 1900), is missing.
`valuation` is `market` unless asked for `cost`, and `columns` is `month` unless asked otherwise; an income statement of more than 36 columns is missing (`statements.MOST_COLUMNS`).
How every figure is defined, and how the change in net worth adds up, is in `docs/reports.md`, "Statements".

- A line (`net_worth`, `savings_rate`, `spending` and each of `lines`) has `now`, `before`, `change` and `share`, a `tone` of `favourable`, `unfavourable` or `quiet`, the `categories` whose rows it adds up, for the journal, and whether it is a `total`, at a `depth`.
  `before` is the same days a year before, or for `net_worth` the start of the period, and is null where the books do not hold that period in full (`covered`, `against_covered`).
  A savings rate is a ratio, written as a decimal string like money.
- Spending is without tax, which is its own line (`docs/reports.md`, "Tax is its own figure").
  The summary's `with_tax` holds the same `spending`, `savings_rate` and `lines` with tax counted as spending, each worked out by the engine, or is null when `scope.taxed` is false, as they would be the same.
  The app shows one or the other whole, by its switch, and adds nothing up: saved from work is the same either way, and a savings rate, being a ratio, could not be had by adding.
  Pay is labelled by the engine (`statements.pay_label`): "Pay before tax" where every pay posting is booked beside its tax, "Take-home pay" where none is, and "Pay" where the books do some of each; Year by year sends its column's heading as `pay_label`.
  Tax is signed: a period with more refunded than paid has a negative tax, labelled "Tax, less refunds", and `review.tax` is negative.
- `why` is null while a balance is not known; otherwise `start`, `saved`, `investment_income`, `market` (null at cost), `other` and each of `others` by `key` and `label`, `end`, and `difference`, which is shown, never hidden.
- `is_floor` says net worth leaves out holdings with no price, so is at least what it says; `late` names the accounts whose holdings the books know only from inside the period, which net worth's start, and why it changed, are without.
- `valuing` says the day of the newest price (`priced_on`), the holdings with no price, not valued at market value (`unpriced`), those with neither a price nor a cost, left out (`unvalued`), how many positions are at a stale price (`stale`), and how many amounts, booked before anything priced them, were converted at the first price there was (`later`); the income statement says `later` of its whole period too.
- `unreviewed` says how many rows nobody has checked yet (`rows`), what of income and expenses rests on them, and how many nothing has placed yet (`unplaced`), with the money they moved each way.
  Of the expenses, it says what is `spending`, `tax`, `spending_with_tax` and `not_spending`, each worked out by the engine, so the app says spending and tax apart, or together when tax is counted as spending.
- The income statement's `income` and `expenses` are trees of `name` (the ledger name, which narrows the journal to it and everything beneath it), `label`, `kind`, `cells`, `total` and `children`, largest first; `columns` says each column's days, whether the period cuts it short (`partial`) and its `unreviewed`; `net` is income less expenses.
  `totals` are figures by kind rather than by the tree, each with `key`, `label`, a cell per column, `total` and the `categories` whose rows it adds up: `spending` (without tax), `tax`, and `spending_with_tax`, each column worked out by the engine.
- The balance sheet's `assets`, `liabilities` and `net_worth` are rows of `label`, `account` (its id and name, which opens its page, for an account), `since` (the day an account's history begins, when that is inside the period), `start` and `end` (each `value`, `cost` and `gain` as Holdings gives them, null where nothing says what it cost, whether it holds `securities`, and whether `value` `is_floor`; null while not known), `change`, and `children`.
- `year_by_year` has a row a year: `year`, whether it is in progress (`partial`) or `sparse`, `net_worth`, `pay`, `spending` (without tax), `tax`, `spending_with_tax`, `saved`, `savings_rate` (over pay after tax) and `savings_rate_with_tax` (over pay, tax counted as spending).

Every figure with tax counted in it is sent beside the one without, never worked out by the app.
That costs a few more figures on the wire and leaves no arithmetic to the app: no sum to round or to get wrong, no rate to divide, and nothing to keep in step when what spending means changes.

## The boundary

The doorway is a page by another name, and is held to everything a page is.

- Every answer is built from a view of `hearth.disclosure`, for the one member signed in to the bridge.
  The doorway decides nothing about who may see what.
- Only `hearth.native.bridge` builds a `Doorway`, and only for the member of a live session; `tests/test_architecture.py` holds it to that.
- It imports no ledger reader and touches none of the books' raw data; `tests/test_architecture.py` holds `hearth.native` to the rules it holds `hearth.web` to.
- `tests/native/test_doorway.py` signs in to a bridge as every member under every sharing scenario the page crawl uses, asks every screen, and fails if an answer holds a datum its viewer may not see.
  A new screen is covered the moment it is added to `SCREENS`; one that takes something to narrow it must be named in the crawl's `NARROWED`, or its plain ask is missing and the crawl fails.
  Narrowed screens are asked for every account in the household by id, every member's and the one nobody owns, every category and payee in it, each chip, periods and windows, and every security in it, cash and one nobody holds for a price history, and a narrowed journal must hold nothing the whole of it did not.
  Beside the household's secrets, it looks for each position's units and price as the doorway writes them, which every close of its history is too.
  Every statement is asked for periods that cut months, a single day, a quarter and none, at market value and at cost, split every way, and so is the journal of a statement's rows, by every category; and again, statements alone, with every member sharing every facet but one, for each facet.
  It makes every review decision on every review row in the household and on rows in nobody's books, against every account, as the row said and as it did not, under forged groups, and takes back every ruling: one on anything not on the viewer's desk must answer `missing` and record nothing, and no review screen or decision may name a row on anyone else's desk.
  It asks what a ledger file nobody imported holds, a file that is not a ledger, the household's own ledger where it is inherited, a folder and nothing at all: what only that ledger says is told to an admin alone, and what the file that is not one says to nobody; and it tries to bring a ledger in and to match every account, neither of which may change anything there.
  `tests/native/test_ledger.py` asks the ledger's screens and actions of a household that imported its ledger and connected every member's banks, and fails if a member is told of another member's connections, or the name of an account the evidence points at that is not theirs.
- Anything private answers exactly like something missing.

This is the one place hearth hands figures out as data.
It is not an HTTP endpoint and must not become one: a bridge that listened on a port could be asked by anything on the machine, and the rule that hearth is server-rendered exists to prevent exactly that.

## One shape, checked from both sides

`tests/native/fixtures/overview.json`, `accounts.json`, `account.json`, `transactions.json`, `banks.json`, `review.json`, `review_row.json`, `where.json`, `holdings.json`, `price_history.json`, `statement_summary.json`, `income_statement.json`, `balance_sheet.json`, `year_by_year.json`, `ledger.json`, `ledger_check.json`, `matching.json` and `decided.json` are example replies, `tests/native/fixtures/outcomes.json` lists every outcome of signing in, and `acted.json` every outcome of an action.
The Python tests check that the bridge still answers in their shape; the Swift tests (`apple/HearthKit`) decode the same files.
A change of shape that only one side has been taught fails that side's tests.

## Adding an action

An action is the one kind of request that changes something, so it is added rarely and kept thin.

1. Add its name to `hearth.native.bridge.ACTIONS`, or a review decision to `hearth.native.review.DECISIONS`, and do it through the module that already does it for every other path, never a second way.
2. Anything it does in the background is followed through a screen, never through the action's reply.
3. Teach `apple/HearthKit` its outcomes, and `acted.json` any new one.

## Adding a screen

1. Write the function from `Disclosure` to a JSON object in `hearth.native.doorway` and add it to `SCREENS`, with the reader of what it takes; a view it needs is built in `hearth.disclosure`, never in the doorway.
2. Add an example to `tests/native/fixtures` and a shape test beside the overview's.
3. Add the type that decodes it to `apple/HearthKit`, with a test that reads the same example.
