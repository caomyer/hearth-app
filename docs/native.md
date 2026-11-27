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

A row, group or ruling that is not on the viewer's desk is `missing`, exactly like one that does not exist, and changes nothing.
There is no auto-accept, and no action accepts more than one group.

## What an answer is

Every answer has a `state` (`hearth.native.doorway`):

| State | Meaning |
|---|---|
| `ready` | The screen's figures follow. |
| `loading` | The books have not been computed yet. |
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
| `transactions` | `from`, `to`, `account`, `category`, `text`, `show`, `offset`, `limit` | A window onto the journal (`Disclosure.journal`). |
| `banks` | nothing | How fresh the viewer's own connections are, and where the bridge's syncs stand (`Disclosure.banks`). |
| `review` | `order`, `account`, `category`, `group`, `text` | The viewer's own review queue, counted as the desk counts it, in the order asked (`hearth.review.queue`). |
| `review_row` | `id` | One row on the viewer's desk: where it stands, what it may be booked against, and the rows from the same merchant before it. |
| `where` | nothing | The viewer's rows whose other side hearth does not see, grouped so that one answer settles each (`hearth.review.where`). |
| `holdings` | nothing | What the investment accounts the viewer may see hold, each holding with its price, value, cost and gain, and whose positions are withheld (`Disclosure.holdings`). |
| `price_history` | `symbol` | Where one security's price comes from, and every price hearth holds of it (`Disclosure.price_history`). |

What an ask takes is read strictly in `hearth.native.doorway`, and anything else is answered `missing`, as a screen that is not there is:

- a key the ask does not take, or any key for an ask that takes nothing;
- `id`, `account` and `symbol` that are not text of 1 to 64 characters;
- `from` and `to` that are not real days written `YYYY-MM-DD`, or a `from` after its `to`;
- `category` that is not `Expenses` or `Income`, alone or with parts beneath it;
- `text` that is not text of 1 to 200 characters;
- `show` that is not `everything`, `unreviewed`, `transfers` or `balance_checks`;
- `offset` that is not a whole number from 0 to 1,000,000, and `limit` that is not one from 1 to 200;
- `order` that is not one of `review.queue`'s orders, and a review `category` that is not an account name or `-`, for nothing suggested.

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
The `overview` says how many of the viewer's rows wait (`review.waiting`, those waiting to be told where their money went among them, as the pages count them) and how much of their own spending this month rests on such rows (`review.spending`).
A `transactions` entry names the review it waits on (`review`) only to whoever may make it.

- `review` answers how much waits and what it moves (`remaining`), what the filter leaves (`shown`), how many are reviewed of the `total`, rows that came back (`changed`, `revised`, and `kept`, those whose decision the books do not apply, as `review.decision.guarded` keeps them unreviewed), rows waiting to be told where their money went, counted apart (`elsewhere`), the values it may be narrowed to (`accounts`, `categories`), its `groups` in order, each with its rows by id and whether it is one decision (`one_decision`), and every row the filter leaves (`rows`), in the queue's order.
- A row says why it is suggested in words (`reason`), never as a percentage; whether it may be excluded (`excludable`, false when its bank counts it); and what it says (`says`), which binds a decision to it.
  A row that came back says why in `reason`: its decision is one the books keep unreviewed, moved onto an account whose balance its bank decides now, or excluded although its bank counts it.
- A row's `kind` is `transaction`, or `reconciling` for a carried account's difference with its bank (`docs/review.md`, "Where the imported ledger and the bank disagree"): its `narration` says by how much they disagree as of which day, its `reason` what to do about it, and it is confirmed with an `accept` of what it proposes, never accepted with its group (its group is never `one_decision`) nor excluded.
  The app reads a `kind` it does not know as a `transaction`, whose decisions the desk still decides.
- The whole queue is answered at once, as the review page shows it, since a group's accept is bound to every row it shows.

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
- `position` is where it stands in what the filter leaves, which is how the app tells entries apart; it says nothing of the household's ledger.

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
  It makes every review decision on every review row in the household and on rows in nobody's books, against every account, as the row said and as it did not, under forged groups, and takes back every ruling: one on anything not on the viewer's desk must answer `missing` and record nothing, and no review screen or decision may name a row on anyone else's desk.
- Anything private answers exactly like something missing.

This is the one place hearth hands figures out as data.
It is not an HTTP endpoint and must not become one: a bridge that listened on a port could be asked by anything on the machine, and the rule that hearth is server-rendered exists to prevent exactly that.

## One shape, checked from both sides

`tests/native/fixtures/overview.json`, `accounts.json`, `account.json`, `transactions.json`, `banks.json`, `review.json`, `review_row.json`, `where.json`, `holdings.json`, `price_history.json` and `decided.json` are example replies, `tests/native/fixtures/outcomes.json` lists every outcome of signing in, and `acted.json` every outcome of an action.
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
