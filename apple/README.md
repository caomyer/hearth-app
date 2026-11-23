# Hearth for Mac

A native Mac app over hearth's engine.
The app is SwiftUI; the engine is hearth's own Python, with beancount beneath it, carried inside the app and run as a process of its own.
Nothing is computed twice: every figure on screen is one hearth worked out.

The plan and the screens it is building towards are in `.lavish/hearth-apple.html`.
This is the first milestone: the app starts the engine it carries and shows the invented household's net worth.

## How it fits together

- `HearthKit/` is everything that is not a view: starting the engine and asking it for a screen (`Engine`), what the answers hold (`Answer`, `Overview`, `Money`, `Day`), and how figures are written for a person (`Figures`).
  It is a Swift package, so it builds and tests without the app.
- `Hearth/` is the app: one window, and for now one screen, Overview.
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

Run the whole chain, the app starting its engine and showing a net worth:

```sh
xcodebuild test -project apple/Hearth.xcodeproj -scheme HearthUI
```

A Mac must allow UI automation for that last one (`automationmodetool enable-automationmode-without-authentication`, which asks for an administrator's password); CI runs it on every push.

If `xcodebuild` says it needs Xcode, point it at one: `export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`.

## Sharp edges

- The engine's code is signed in the cache, not by Xcode: an app's signature seals its resources but does not sign code it finds there.
  A build signed for this Mac only ("-") leaves the hardened runtime off the engine, since that runtime refuses to load code signed by no team.
- Python runs with `-I` and `-B`: isolated from everything on the Mac it runs on, and writing no bytecode into a sealed bundle.
  Bytecode is compiled at build time with `unchecked-hash`, so Python never compares it with a source file's date.
- A new screen is a new entry in `hearth.native.doorway.SCREENS`, a type in `HearthKit` that decodes it, and an example in `tests/native/fixtures` that both sides' tests read.
- Beancount is GPL-2.0-only, so the app is for private use and is shared together with its source.
