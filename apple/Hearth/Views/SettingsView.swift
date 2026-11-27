import AppKit
import HearthKit
import SwiftUI

/// Hearth's Settings window.
struct SettingsView: View {
    let session: Session

    var body: some View {
        TabView {
            BanksPane(session: session)
                .tabItem { Label("Banks", systemImage: "building.columns") }
            LedgerPane(session: session)
                .tabItem { Label("Ledger", systemImage: "book.closed") }
        }
        .frame(width: 520)
    }
}

/// What Hearth may ask the household's connected banks, switch by switch, each off until the
/// person turns it on. Each one is an approval the engine is started with and enforces itself.
private struct BanksPane: View {
    let session: Session

    var body: some View {
        Form {
            if let books = session.books {
                Section {
                    Toggle(isOn: binding(\.transactions)) {
                        Text("Ask your banks for new transactions")
                        Text(
                            "When Hearth opens, every morning while it is open, and when you press Update Now. Balances come with them."
                        )
                    }
                    .accessibilityIdentifier("settings.transactions")
                } footer: {
                    if books == .sample {
                        Text("For the sample household. Its banks are invented, and nothing reaches Plaid.")
                    }
                }
                Section {
                    Toggle(isOn: binding(\.holdings)) {
                        Text("Read what your brokerages hold")
                        Text(
                            "Starts a new Plaid charge. The first time, Plaid adds its Investments product to each brokerage's connection and bills for it from then on, until the connection is removed."
                        )
                    }
                    .accessibilityIdentifier("settings.holdings")
                    Toggle(isOn: binding(\.activity)) {
                        Text("Read your brokerages' trades and dividends")
                        Text(
                            "Starts another Plaid charge. The first time, Plaid adds its Investments Transactions subscription to each brokerage's connection, billed apart from holdings for as long as the connection lasts."
                        )
                    }
                    .accessibilityIdentifier("settings.activity")
                } header: {
                    Text("Brokerages")
                } footer: {
                    // What is not done here, said as quietly as the sample's footnote.
                    Text(
                        "Connecting a bank, signing in to one again, and removing a connection are done in Hearth in the browser."
                    )
                }
            } else {
                Text("Open your books to choose what Hearth may ask your banks.")
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .fixedSize(horizontal: false, vertical: true)
    }

    /// One switch of the consent for the books open. Changing it starts their engine again,
    /// approved for what is now on.
    private func binding(_ key: WritableKeyPath<Consent, Bool>) -> Binding<Bool> {
        Binding(
            get: { session.consent[keyPath: key] },
            set: { on in
                var consent = session.consent
                consent[keyPath: key] = on
                Task { await session.allow(consent) }
            })
    }
}

/// The household's own ledger: bringing one in, once, and matching connected accounts to it,
/// each done in a sheet over the books (`LedgerSheet`).
private struct LedgerPane: View {
    let session: Session

    var body: some View {
        Form {
            if let desk = session.desk, session.books == .own {
                let flow = desk.ledger
                Section {
                    if let standing = flow.standing {
                        Text(LedgerWords.standing(standing))
                            .accessibilityIdentifier("settings.ledger")
                        if let whyNot = standing.whyNot, standing.imported == nil {
                            Text(whyNot).foregroundStyle(.secondary)
                        }
                    }
                } footer: {
                    if let unclaimed = LedgerWords.unclaimed(flow.standing) {
                        Text(unclaimed)
                    }
                }
                Section {
                    if flow.standing?.mayBringIn == true {
                        Button("Bring In a Ledger\u{2026}") {
                            showBooks()
                            chooseFile(flow)
                        }
                        .accessibilityIdentifier("settings.bringIn")
                    }
                    if flow.standing?.imported != nil {
                        Button("Match Accounts\u{2026}") {
                            showBooks()
                            Task { await flow.openMatching() }
                        }
                        .accessibilityIdentifier("settings.match")
                    }
                } footer: {
                    if flow.standing?.imported != nil {
                        Text("Say which of your ledger's accounts each connected account is, such as one connected since.")
                    }
                }
                .task { await flow.refresh() }
            } else {
                Text("Open your books and sign in to bring in a ledger.")
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .fixedSize(horizontal: false, vertical: true)
    }

    /// The window of the books, in front, where the sheet opens.
    private func showBooks() {
        NSApplication.shared.windows
            .first { $0.styleMask.contains(.resizable) && $0.isVisible && !($0.identifier?.rawValue.contains("Settings") ?? false) }?
            .makeKeyAndOrderFront(nil)
    }
}
