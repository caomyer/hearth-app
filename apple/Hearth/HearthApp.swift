import AppKit
import HearthKit
import SwiftUI

@main
struct HearthApp: App {
    @State private var session = HearthApp.session()

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
        }
    }

    /// The session over this Mac's household: hearth's own data directory, or the one the
    /// app's environment names (`HEARTH_DATA_DIR`), as a test does to open a household of its own.
    private static func session() -> Session {
        let home = NSHomeDirectory()
        let setup = Setup.from(environment: ProcessInfo.processInfo.environment, home: home)
        let resources = Bundle.main.resourceURL
        let credentials = DebugLaunch.credentials ?? Keychain()
        return Session(setup: setup, defaults: .standard, credentials: credentials) { books in
            guard let resources else { throw Engine.Failure.stopped }
            let command = EngineCommand.bundled(
                books, setup: setup, in: resources, home: home, temporary: NSTemporaryDirectory())
            return try Engine.launch(command)
        }
    }
}
