import Foundation
import Observation

/// The screens of the books while they are open to someone, each asking the one engine: the
/// Overview, the accounts the sidebar and the Accounts list show, Review, the journal, Holdings,
/// Statements, and each account's own page as it is opened.
@MainActor
@Observable
public final class Desk {
    public let overview: OverviewModel
    public let accounts: ScreenModel<Accounts>
    public let transactions: JournalModel
    /// The sync control every screen shows in the toolbar.
    public let sync: SyncModel
    /// Every review decision, wherever it is made, and the one an Undo takes back.
    public let decisions: Decisions
    public let review: ReviewModel
    /// The rows whose other side hearth does not see, answered a group at a time.
    public let whereItWent: ScreenModel<WherePage>
    /// What the investment accounts the viewer may see hold.
    public let holdings: ScreenModel<Holdings>
    /// The books read the way a business reads its own: Summary, the income statement, the
    /// balance sheet and year by year.
    public let statements: StatementsModel
    /// Bringing the household's ledger in, and matching accounts to it.
    public let ledger: LedgerFlow

    private let engine: any Asking
    private let signedOut: @MainActor () -> Void
    @ObservationIgnored private var journals: [() -> JournalModel?] = []

    /// `ledgerAsked` says whether this member was asked on this Mac if they keep a ledger,
    /// and `rememberLedgerAsked` keeps that they were.
    public init(
        engine: any Acting, signedOut: @escaping @MainActor () -> Void = {},
        pause: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        ledgerAsked: @escaping () -> Bool = { true }, rememberLedgerAsked: @escaping () -> Void = {}
    ) {
        self.engine = engine
        self.signedOut = signedOut
        overview = OverviewModel(engine: engine, pause: pause, signedOut: signedOut)
        accounts = ScreenModel(engine: engine, screen: "accounts", pause: pause, signedOut: signedOut)
        transactions = JournalModel(engine: engine, pause: pause, signedOut: signedOut)
        sync = SyncModel(engine: engine, pause: pause, signedOut: signedOut)
        decisions = Decisions(engine: engine, signedOut: signedOut)
        review = ReviewModel(engine: engine, decisions: decisions, pause: pause, signedOut: signedOut)
        whereItWent = ScreenModel(engine: engine, screen: "where", pause: pause, signedOut: signedOut)
        holdings = ScreenModel(engine: engine, screen: "holdings", pause: pause, signedOut: signedOut)
        statements = StatementsModel(engine: engine, pause: pause, signedOut: signedOut)
        ledger = LedgerFlow(
            engine: engine, asked: ledgerAsked, remember: rememberLedgerAsked, pause: pause,
            signedOut: signedOut)
        let transactions = transactions
        journals = [{ transactions }]
        decisions.whenDecided { [weak self] in await self?.decided(pause: pause) }
    }

    /// One account's own page: its balance and what it rests on.
    public func account(_ id: String) -> ScreenModel<AccountPage> {
        ScreenModel(engine: engine, screen: "account", given: ["id": .text(id)], signedOut: signedOut)
    }

    /// One account's journal, which every filter of it keeps to that account.
    public func journal(of account: AccountRef) -> JournalModel {
        let journal = JournalModel(engine: engine, pinned: account, signedOut: signedOut)
        journals.append { [weak journal] in journal }
        return journal
    }

    /// Where one security's price comes from, and its history, as Holdings shows it beside a
    /// holding.
    public func priceHistory(_ symbol: String) -> ScreenModel<PriceHistory> {
        ScreenModel(engine: engine, screen: "price_history", given: ["symbol": .text(symbol)], signedOut: signedOut)
    }

    /// One row's detail, as Review and Transactions show it.
    public func reviewRow(_ id: String) -> ScreenModel<ReviewDetail> {
        ScreenModel(engine: engine, screen: "review_row", given: ["id": .text(id)], signedOut: signedOut)
    }

    /// After a decision, beside the queue, which asks again itself: the rows seen from one side
    /// and the counts at once, as the desk has them; the figures and journals as the recompute
    /// the decision started brings them.
    private func decided(pause: @escaping @Sendable (Duration) async throws -> Void) async {
        if whereItWent.state != .starting {
            await whereItWent.refresh()
        }
        await overview.refresh()
        let overview = overview
        let journals = journals.compactMap { $0() }
        Task {
            await Self.follow(pause: pause) { await overview.refresh() }
            for journal in journals where journal.state == .ready {
                await Self.follow(pause: pause) { await journal.refresh() }
            }
        }
    }

    /// Asks again while the books are being recomputed, so what a decision changed shows the
    /// moment they are ready, then leaves the screen to its own pace.
    private static func follow(
        pause: @Sendable (Duration) async throws -> Void, _ refresh: () async -> Duration?
    ) async {
        for _ in 0..<200 {
            guard let wait = await refresh(), wait < .seconds(1) else { return }
            do {
                try await pause(wait)
            } catch {
                return
            }
        }
    }
}
