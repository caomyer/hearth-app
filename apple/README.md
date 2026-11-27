# Hearth for Mac

A native Mac app over hearth's engine.
The app is SwiftUI; the engine is hearth's own Python, with beancount beneath it, carried inside the app and run as a process of its own.
Nothing is computed twice: every figure on screen is one hearth worked out.

The plan and the screens it is building towards are in `.lavish/hearth-apple.html`.
For now the app opens a household's existing hearth data on this Mac in place, for a member who signs in, or the invented sample household.
It shows the Overview, the journal (Transactions), the accounts and each account's own page, lets a member review what their banks sent, and brings the household's connections up to date when the person allows it.

## Opening books

- On first launch the app asks which books to open.
  If hearth is set up on this Mac (`~/.local/share/hearth` holds `hearth.sqlite3`) it offers "Use this setup" and a sample; otherwise only the sample.
  The choice is remembered per data directory, and File > Open Your Books and Open the Sample switch between them.
- A household's books run the bundled engine's `hearth bridge` against that data directory, with only `HOME`, `TMPDIR`, `LC_ALL` and `HEARTH_DATA_DIR` in its environment.
  The engine answers nothing until a member signs in; it, not the app, checks the password (`docs/native.md`).
- The app asks for a username and password once, keeps them in the login keychain (service `Hearth`, the data directory as the account), and hands them to the engine on every launch.
  File > Sign Out forgets them.
  A one-time password is changed in hearth's web pages first.
- `HEARTH_DATA_DIR` in the app's own environment points it at another data directory, as the end-to-end test does with a household `tests/native/on_disk.py` invents.
  Nothing else the app remembers is shared between data directories.

## Asking the banks

- The app asks no bank for anything until the person allows it in Settings, Banks: one switch for new transactions (`read`), and one each for what brokerages hold (`holdings`) and their trades and dividends (`activity`), which say the Plaid charge they start before they are turned on.
  Connecting a bank, signing in to one again and removing a connection stay in Hearth in the browser.
- The switches are kept per set of books and per data directory (`HearthKit.Consent`), so the sample's never turn anything on for a household's own banks.
- The engine is started with `--approve-plaid` for exactly the switches that are on, and started again, signed in again, when one changes; it enforces the approval itself (`docs/native.md`).
- The toolbar's sync control, on every screen, says when the viewer's own banks were last updated, shows a sync running, and asks again now; with asking off it says so and leads to Settings.
  When the person asked, it then says what arrived from each of their banks, or what went wrong (`HearthKit.SyncModel`).
- While the app is open with asking allowed, the engine syncs at start-up and every morning, as `hearth serve` does.

## Review

- The sidebar's Review says how many of the member's own transactions wait, as the pages count them, and the Overview says so too, with how much of this month's own spending is not reviewed yet.
- Review lists what waits, groups first, largest first: a group that is one decision offers Accept and its count, any other opens to its rows; or every row one by one, in any order `hearth.review.queue` offers, narrowed by account, suggested category or words.
  Beside it, the row the person is on: its day and account, the amount, the cleaned-up name over the bank's own words, its category, the rows from the same merchant before it, and the decision.
- Every decision is the engine's, made through the member's own review desk (`docs/native.md`, "Reviewing"), bound to the rows the screen showed; a row that changed in between is left waiting and the person is told.
  After each, the screen moves on to the next item of the queue as it was, and what the decision did stays pinned under the queue with its Undo, offered until the next decision.
- The Review menu carries the queue's keys while the queue has the keyboard: the arrows, Return, C, X and S; Command-Z is Edit > Undo.
- An unreviewed row of the member's own opens the same detail beside Transactions, and is decided there.
- Rows whose other side Hearth does not see are counted apart and answered a group at a time under "Where did it go?", or a row alone; naming a new account for them stays in Hearth in the browser.
- There is no auto-accept, and no Split or note: the engine cannot split a row yet, and the review pages write no note.

## How it fits together

- `HearthKit/` is everything that is not a view: which books are open, who is signed in and what the engine may ask the banks (`Session`, `Setup`, `Consent`), starting the engine, signing in to it, asking it for a screen and asking it to sync (`Engine`), keeping a sign-in in the Keychain (`Keychain`, behind `CredentialStore`), what the answers hold (`Answer`, `Overview`, `Accounts`, `AccountPage`, `Journal`, `Banks`, `Review`, `Money`, `Day`), each screen's state (`Desk`, `ScreenModel`, `SyncModel`, `JournalModel` with its `JournalFilter`, and `ReviewModel` with its `ReviewQuery`), the review decisions and the one an Undo takes back (`Decisions`), and how figures, balances and decisions are written for a person (`Figures`, `AccountWords`, `SyncWords`, `ReviewWords`).
  It is a Swift package, so it builds and tests without the app.
- `Hearth/` is the app: one window, with the start screen, signing in, and the books, with the sync control in the toolbar: a sidebar of Overview, Review, Transactions, Accounts and every account the member may see, and the screen chosen.
  An account's name anywhere opens the account; a category opens Transactions narrowed to it.
- `engine/main.py` and `scripts/bundle-engine.sh` put a pinned Python, everything `uv.lock` names, and `src/hearth` into the app's resources, signed as the app is signed.
- On the Python side, `hearth.native` is the doorway: `hearth bridge` answers the app over standard input and output, one JSON object a line, with views from `hearth.disclosure` and nothing else (`docs/native.md`).

## Develop

`project.yml` is the source of truth; the Xcode project is generated and git-ignored.

```sh
brew install xcodegen
cp apple/Config/Local.example.xcconfig apple/Config/Local.xcconfig   # then set DEVELOPMENT_TEAM
xcodegen generate --spec apple/project.yml
open apple/Hearth.xcodeproj
```

The first build downloads the engine's Python and installs its dependencies into `~/Library/Caches/dev.hearth.engine`; later builds copy only what changed.
The app needs macOS 15 and Apple silicon.

Test the Swift core:

```sh
swift test --package-path apple/HearthKit
```

Check that a bundled engine stands on its own, with no uv, checkout or other Python:

```sh
apple/scripts/bundle-engine.sh /tmp/hearth-engine
/tmp/hearth-engine/python/bin/python3.14 -I apple/scripts/check-engine.py /tmp/hearth-engine
```

The real login keychain is tested only when asked, as it writes to the keychain of whoever runs it:

```sh
HEARTH_TEST_KEYCHAIN=1 swift test --package-path apple/HearthKit --filter Keychain
```

Run the whole chain, the app starting its engine and showing a net worth, for the sample and for an invented household on disk that a member signs in to and reviews:

```sh
export HEARTH_FIXTURE_PASSWORD=$(openssl rand -hex 16) HEARTH_FIXTURE_DATA_DIR=$(mktemp -d)/household
uv run python -m tests.native.on_disk "$HEARTH_FIXTURE_DATA_DIR"
TEST_RUNNER_HEARTH_FIXTURE_DATA_DIR=$HEARTH_FIXTURE_DATA_DIR TEST_RUNNER_HEARTH_FIXTURE_PASSWORD=$HEARTH_FIXTURE_PASSWORD \
  xcodebuild test -project apple/Hearth.xcodeproj -scheme HearthUI
```

Without the fixture variables the household's test is skipped.

A debug build opens straight onto a screen, so each can be looked at without a click; a Release build reads none of these:

```sh
APP=~/Library/Developer/Xcode/DerivedData/<yours>/Build/Products/Debug/Hearth.app/Contents/MacOS/Hearth
"$APP" -openBooks sample -startScreen transactions
"$APP" -openBooks sample -firstReview YES -startScreen review
HEARTH_DATA_DIR=$HEARTH_FIXTURE_DATA_DIR HEARTH_SIGN_IN_PASSWORD=$HEARTH_FIXTURE_PASSWORD \
  "$APP" -openBooks own -signInAs avery -startScreen "account:Everyday checking"
```

- `-openBooks sample|own` opens those books at once.
- `-startScreen overview|review|transactions|accounts|account:<name>` opens that screen; an account is found by its name once the accounts are in.
- `-firstReview YES` opens the sample as a first import leaves it: two years, over a thousand rows, none reviewed (`hearth demo --first-review`).
- `-sampleAsOf YYYY-MM-DD` opens the sample as it stood on that day (`hearth demo --as-of`).
  The sample is drawn back from the day it is opened, so what waits in its Review changes from day to day; the UI tests that work Review pin it this way.
- `-signInAs <username>` hands the engine that member with the password in `HEARTH_SIGN_IN_PASSWORD`, in place of the Keychain, which it leaves alone.
- `-appearance light|dark` draws the app that way whatever the Mac is set to, to look at each screen both ways.

A Mac must allow UI automation for that last one (`automationmodetool enable-automationmode-without-authentication`, which asks for an administrator's password); CI runs it on every push.

If `xcodebuild` says it needs Xcode, point it at one: `export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`.

## Sharp edges

- The engine's code is signed in the cache, not by Xcode: an app's signature seals its resources but does not sign code it finds there.
  A build signed for this Mac only ("-") leaves the hardened runtime off the engine, since that runtime refuses to load code signed by no team.
- Python runs with `-I` and `-B`: isolated from everything on the Mac it runs on, and writing no bytecode into a sealed bundle.
  Bytecode is compiled at build time with `unchecked-hash`, so Python never compares it with a source file's date.
- The Keychain item is the file-based login keychain's, which needs no provisioning; macOS asks the person before an app with another signature reads it, so a build signed for this Mac only ("-") may be asked about after every rebuild.
- The app's engine opens a household's databases in place and brings them up to the schema of the hearth it carries, as `hearth serve` of that version would.
  It reads no `HEARTH_LEDGER`: a household that inherits a ledger is shown from hearth's own books until the app learns to import one.
- The fixture household's bank keys were sealed under a vault key its builder kept in memory, so a sync of it in the end-to-end test finds no key in the Keychain and says so; that test is how the app's words for a Keychain that cannot give the keys are checked.
  The sample's sync, against the stand-in for Plaid, is how a sync that works is checked.
- A new screen is a new entry in `hearth.native.doorway.SCREENS`, a type in `HearthKit` that decodes it, and an example in `tests/native/fixtures` that both sides' tests read.
- What a screen is asked with, or a decision made on, travels as `Given`: text, whole numbers, and the rows a decision showed; the engine reads it strictly and answers anything it does not take as missing (`docs/native.md`).
- The start screen is the window's content, not a sheet: a modal sheet would keep the app from quitting until it was answered.
- A list is first drawn with at most a page of rows, and the rest follow on the next update (`FirstFill`).
  AppKit measures a table's rows 200 at a time, and a table filled with more than that on its first update re-enters its own measuring, which it logs as "a reentrant operation in its NSTableView delegate" and means to make an assert.
- Beancount is GPL-2.0-only, so the app is for private use and is shared together with its source.
