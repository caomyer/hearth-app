import HearthKit
import SwiftUI

@main
struct HearthApp: App {
    @State private var session = Session()

    var body: some Scene {
        WindowGroup {
            RootView(session: session)
                .frame(minWidth: 760, minHeight: 520)
                .tint(Palette.accent)
        }
        .defaultSize(width: 1040, height: 680)
        .commands {
            // One window on one set of books: there is no second document to open.
            CommandGroup(replacing: .newItem) {}
        }
    }
}
