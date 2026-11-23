import Foundation
import Observation

/// The screens of the books while they are open to someone, each asking the one engine: the
/// Overview, the accounts the sidebar and the Accounts list show, the journal, and each
/// account's own page as it is opened.
@MainActor
@Observable
public final class Desk {
    public let overview: OverviewModel
    public let accounts: ScreenModel<Accounts>
    public let transactions: JournalModel
    /// The sync control every screen shows in the toolbar.
    public let sync: SyncModel

    private let engine: any Asking
    private let signedOut: @MainActor () -> Void

    public init(engine: any Acting, signedOut: @escaping @MainActor () -> Void = {}) {
        self.engine = engine
        self.signedOut = signedOut
        overview = OverviewModel(engine: engine, signedOut: signedOut)
        accounts = ScreenModel(engine: engine, screen: "accounts", signedOut: signedOut)
        transactions = JournalModel(engine: engine, signedOut: signedOut)
        sync = SyncModel(engine: engine, signedOut: signedOut)
    }

    /// One account's own page: its balance and what it rests on.
    public func account(_ id: String) -> ScreenModel<AccountPage> {
        ScreenModel(engine: engine, screen: "account", given: ["id": .text(id)], signedOut: signedOut)
    }

    /// One account's journal, which every filter of it keeps to that account.
    public func journal(of account: AccountRef) -> JournalModel {
        JournalModel(engine: engine, pinned: account, signedOut: signedOut)
    }
}
