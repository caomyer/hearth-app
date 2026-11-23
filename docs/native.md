# The native app's doorway

The Mac app (`apple/`) shows hearth's figures in native screens.
It computes nothing itself: it starts hearth as a process of its own and asks it for each screen.
`hearth.native` is that doorway, and this document records how it works and what it must never become.

## The wire

`hearth bridge --as USERNAME` answers over its standard input and output, one JSON object a line (`hearth.native.stdio`).

```
-> {"id": 1, "ask": "overview"}
<- {"id": 1, "answer": {"state": "ready", "net_worth": {"value": "162711.83", ...}, ...}}
```

- There is no socket and no port.
  The only thing that can ask is the process that started the bridge, and the bridge ends when that process closes its end, which the system does for it if it dies.
- A line that is not a request is answered `{"id": null, "error": "bad request"}` and the bridge carries on.
- Nothing but replies is written to standard output; anything said for a person goes to standard error.
- `hearth demo --bridge` is the same bridge over the invented household, as its first member. The app's sample runs it.

## What an answer is

Every answer has a `state` (`hearth.native.doorway`):

| State | Meaning |
|---|---|
| `ready` | The screen's figures follow. |
| `loading` | The books have not been computed yet. |
| `failed` | The last recompute failed and there are no earlier books; `summary` is the one sentence anyone may read. |
| `missing` | There is no such screen, or none this viewer may see. The two are one answer. |

Money is a decimal string, never a JSON number, so nothing passes through a binary float on its way to the screen.
Days are ISO dates; moments are UTC to the second, always in the shape `2026-10-15T12:00:00Z`.

## The boundary

The doorway is a page by another name, and is held to everything a page is.

- Every answer is built from a view of `hearth.disclosure`, for the one member the bridge was started as.
  The doorway decides nothing about who may see what.
- It imports no ledger reader and touches none of the books' raw data; `tests/test_architecture.py` holds `hearth.native` to the rules it holds `hearth.web` to.
- `tests/native/test_doorway.py` asks every screen as every member under every sharing scenario the page crawl uses, and fails if an answer holds a datum its viewer may not see.
  A new screen is covered the moment it is added to `SCREENS`.
- Anything private answers exactly like something missing.

This is the one place hearth hands figures out as data.
It is not an HTTP endpoint and must not become one: a bridge that listened on a port could be asked by anything on the machine, and the rule that hearth is server-rendered exists to prevent exactly that.

## One shape, checked from both sides

`tests/native/fixtures/overview.json` is an example reply.
The Python tests check that the doorway still answers in its shape; the Swift tests (`apple/HearthKit`) decode the same file.
A change of shape that only one side has been taught fails that side's tests.

## Adding a screen

1. Write the function from `Disclosure` to a JSON object in `hearth.native.doorway` and add it to `SCREENS`.
2. Add an example to `tests/native/fixtures` and a shape test beside the overview's.
3. Add the type that decodes it to `apple/HearthKit`, with a test that reads the same example.
