# The native app's doorway

The Mac app (`apple/`) shows hearth's figures in native screens.
It computes nothing itself: it starts hearth as a process of its own and asks it for each screen.
`hearth.native` is that doorway, and this document records how it works and what it must never become.

## The wire

`hearth bridge` answers over its standard input and output, one JSON object a line (`hearth.native.stdio`).
Every request carries a number and exactly one of `sign_in`, `sign_out` or `ask`; its reply carries the same number.

```
-> {"id": 1, "ask": "overview"}
<- {"id": 1, "answer": {"state": "signed_out"}}
-> {"id": 2, "sign_in": {"username": "avery", "password": "..."}}
<- {"id": 2, "outcome": "signed_in"}
-> {"id": 3, "ask": "overview"}
<- {"id": 3, "answer": {"state": "ready", "net_worth": {"value": "162711.83", ...}, ...}}
-> {"id": 4, "sign_out": true}
<- {"id": 4, "outcome": "signed_out"}
```

- There is no socket and no port.
  The only thing that can ask is the process that started the bridge, and the bridge ends when that process closes its end, which the system does for it if it dies.
- A line that is not a request is answered `{"id": null, "error": "bad request"}` and the bridge carries on.
  The refusal never repeats the line, which may hold a password.
- Nothing but replies is written to standard output; anything said for a person goes to standard error.
- `hearth bridge` reads the household in the data directory (`HEARTH_DATA_DIR`, or `~/.local/share/hearth`), and refuses to start where there is none rather than make an empty one.
  It recomputes as `hearth serve` does: at start-up, every morning, and whenever another process changes what the books are computed from.
- `hearth demo --bridge` is the same bridge over the invented household, already signed in as its first member with that member's password. The app's sample runs it.

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

## The boundary

The doorway is a page by another name, and is held to everything a page is.

- Every answer is built from a view of `hearth.disclosure`, for the one member signed in to the bridge.
  The doorway decides nothing about who may see what.
- Only `hearth.native.bridge` builds a `Doorway`, and only for the member of a live session; `tests/test_architecture.py` holds it to that.
- It imports no ledger reader and touches none of the books' raw data; `tests/test_architecture.py` holds `hearth.native` to the rules it holds `hearth.web` to.
- `tests/native/test_doorway.py` signs in to a bridge as every member under every sharing scenario the page crawl uses, asks every screen, and fails if an answer holds a datum its viewer may not see.
  A new screen is covered the moment it is added to `SCREENS`.
- Anything private answers exactly like something missing.

This is the one place hearth hands figures out as data.
It is not an HTTP endpoint and must not become one: a bridge that listened on a port could be asked by anything on the machine, and the rule that hearth is server-rendered exists to prevent exactly that.

## One shape, checked from both sides

`tests/native/fixtures/overview.json` is an example reply, and `tests/native/fixtures/outcomes.json` lists every outcome of signing in.
The Python tests check that the bridge still answers in their shape; the Swift tests (`apple/HearthKit`) decode the same files.
A change of shape that only one side has been taught fails that side's tests.

## Adding a screen

1. Write the function from `Disclosure` to a JSON object in `hearth.native.doorway` and add it to `SCREENS`.
2. Add an example to `tests/native/fixtures` and a shape test beside the overview's.
3. Add the type that decodes it to `apple/HearthKit`, with a test that reads the same example.
