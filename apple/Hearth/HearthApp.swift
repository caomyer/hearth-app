import AppKit
import HearthKit
import SwiftUI

@main
struct HearthApp: App {
    @State private var session = HearthApp.session()

    init() {
        DebugLaunch.applyAppearance()
    }

    var body: some Scene {
        WindowGroup {
            RootView(session: session)
                .frame(minWidth: 1000, minHeight: 560)
                .tint(Palette.accent)
        }
        .defaultSize(width: 1180, height: 740)
        .commands {
            // One window on one set of books: there is no second document to open, only other
            // books to open in it.
            CommandGroup(replacing: .newItem) {
                Button("Open Your Books") { Task { await session.choose(.own) } }
                    .disabled(!session.setup.isPresent || session.books == .own)
                Button("Open the Sample") { Task { await session.choose(.sample) } }
                    .disabled(session.books == .sample)
                Divider()
                Button("Sign Out") { Task { await session.signOut() } }
                    .disabled(!session.isSignedIn)
            }
            ReviewMenu()
        }

        Settings {
            SettingsView(session: session)
                .tint(Palette.accent)
        }
    }

    /// The session over this Mac's household: hearth's own data directory, or the one the
    /// app's environment names (`HEARTH_DATA_DIR`), as a test does to open a household of its own.
    private static func session() -> Session {
        let home = NSHomeDirectory()
        let setup = Setup.from(environment: ProcessInfo.processInfo.environment, home: home)
        let resources = Bundle.main.resourceURL
        let credentials = DebugLaunch.credentials ?? Keychain()
        return Session(setup: setup, defaults: .standard, credentials: credentials) { books, consent in
            guard let resources else { throw Engine.Failure.stopped }
            let command = EngineCommand.bundled(
                books, setup: setup, consent: consent, firstReview: DebugLaunch.firstReview,
                sampleAsOf: DebugLaunch.sampleAsOf,
                in: resources, home: home, temporary: NSTemporaryDirectory())
            return try Engine.launch(command)
        }
    }
}

/// The Review menu: the queue's keys, as commands. They act only while the queue has the
/// keyboard; elsewhere they are shown without their keys, so a letter typed in a field is only
/// ever a letter. Command-Z is Edit > Undo, which takes back the last decision.
private struct ReviewMenu: Commands {
    @FocusedValue(\.reviewCommands) private var review

    var body: some Commands {
        CommandMenu("Review") {
            if let review {
                Button("Next") { review.next() }
                    .keyboardShortcut(.downArrow, modifiers: [])
                Button("Previous") { review.previous() }
                    .keyboardShortcut(.upArrow, modifiers: [])
                Divider()
                Button("Accept") { review.accept() }
                    .keyboardShortcut(.return, modifiers: [])
                Button("Change Category…") { review.changeCategory?() }
                    .keyboardShortcut("c", modifiers: [])
                    .disabled(review.changeCategory == nil)
                Button("Exclude") { review.exclude?() }
                    .keyboardShortcut("x", modifiers: [])
                    .disabled(review.exclude == nil)
                Button("Skip") { review.skip() }
                    .keyboardShortcut("s", modifiers: [])
            } else {
                Button("Next") {}.disabled(true)
                Button("Previous") {}.disabled(true)
                Divider()
                Button("Accept") {}.disabled(true)
                Button("Change Category…") {}.disabled(true)
                Button("Exclude") {}.disabled(true)
                Button("Skip") {}.disabled(true)
            }
        }
    }
}
