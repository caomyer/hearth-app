import Foundation

/// What the person let Hearth ask the household's connected banks for, switch by switch, in
/// Settings, Banks. Everything is off until they turn it on.
///
/// Each switch is one of hearth's own approvals (`hearth.plaid.approval.Operation`), handed to
/// the engine when it starts, as `hearth serve --approve-plaid` hands them, and the engine
/// enforces them itself: nothing on the wire can widen them. Connecting a bank and removing a
/// connection are not among them; the app does neither.
public struct Consent: Sendable, Equatable, Codable {
    /// Ask connected banks for new transactions and balances (`read`).
    public var transactions: Bool
    /// Ask connected brokerages what they hold and today's prices (`holdings`). Its first ask
    /// adds Plaid's Investments product to each brokerage's connection, which Plaid bills for
    /// from then on.
    public var holdings: Bool
    /// Ask connected brokerages for their trades and dividends (`activity`). Its first ask
    /// adds Plaid's Investments Transactions subscription to each brokerage's connection,
    /// billed apart from holdings for as long as the connection lasts.
    public var activity: Bool

    public init(transactions: Bool = false, holdings: Bool = false, activity: Bool = false) {
        self.transactions = transactions
        self.holdings = holdings
        self.activity = activity
    }

    /// Nothing asked: how every household's books start.
    public static let nothing = Consent()

    /// The approvals each switch stands for, by hearth's own names, in a fixed order.
    public var operations: [String] {
        [(transactions, "read"), (holdings, "holdings"), (activity, "activity")]
            .filter(\.0).map(\.1)
    }

    /// What the engine is started with: one `--approve-plaid` for each switch that is on.
    public var arguments: [String] {
        operations.flatMap { ["--approve-plaid", $0] }
    }

    /// Whether Hearth may ask the banks anything at all.
    public var asksAnything: Bool { !operations.isEmpty }

    /// The consent for operations an engine says it was started with, as `banks` reports them.
    public init(operations: some Sequence<String>) {
        let named = Set(operations)
        self.init(
            transactions: named.contains("read"), holdings: named.contains("holdings"),
            activity: named.contains("activity"))
    }
}

/// Where the person's consent is kept: per set of books, so the sample's switches never turn
/// anything on for a household's own banks, and per data directory, as everything else the
/// app remembers is.
public struct ConsentStore {
    private let defaults: UserDefaults
    private let setup: Setup

    public init(defaults: UserDefaults, setup: Setup) {
        self.defaults = defaults
        self.setup = setup
    }

    public func consent(for books: Books) -> Consent {
        guard let data = defaults.data(forKey: key(books)),
            let kept = try? JSONDecoder().decode(Consent.self, from: data)
        else { return .nothing }
        return kept
    }

    public func save(_ consent: Consent, for books: Books) {
        guard let data = try? JSONEncoder().encode(consent) else { return }
        defaults.set(data, forKey: key(books))
    }

    private func key(_ books: Books) -> String { "consent \(books.rawValue) \(setup.key)" }
}
