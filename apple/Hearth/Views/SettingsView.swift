import HearthKit
import SwiftUI

/// Hearth's Settings window.
struct SettingsView: View {
    let session: Session

    var body: some View {
        TabView {
            BanksPane(session: session)
                .tabItem { Label("Banks", systemImage: "building.columns") }
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
