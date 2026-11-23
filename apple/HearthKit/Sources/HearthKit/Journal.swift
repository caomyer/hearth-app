import Foundation

/// A window onto the viewer's journal: entries newest first, each with every side the viewer
/// may see (`hearth.native.doorway.transactions`).
public struct Journal: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    /// The period the journal covers, as the engine settled it.
    public let from: Day
    public let to: Day
    /// How many entries the whole filter leaves; `entries` are those from `offset`.
    public let total: Int
    public let offset: Int
    public let counts: Counts
    public let entries: [Entry]

    /// How many entries each chip would leave, under the other filters.
    public struct Counts: Decodable, Sendable, Equatable {
        public let everything: Int
        public let unreviewed: Int
        public let transfers: Int
        public let balanceChecks: Int

        public static let none = Counts(everything: 0, unreviewed: 0, transfers: 0, balanceChecks: 0)

        public func count(_ show: Show) -> Int {
            switch show {
            case .everything: everything
            case .unreviewed: unreviewed
            case .transfers: transfers
            case .balanceChecks: balanceChecks
            }
        }
    }
}

/// Which entries the journal is narrowed to: the chips over it.
public enum Show: String, Decodable, Sendable, CaseIterable, Hashable {
    case everything
    case unreviewed
    case transfers
    case balanceChecks = "balance_checks"

    public var label: String {
        switch self {
        case .everything: "Everything"
        case .unreviewed: "Unreviewed"
        case .transfers: "Transfers"
        case .balanceChecks: "Balance checks"
        }
    }
}

/// An account as an entry names it: enough to open it.
public struct AccountRef: Decodable, Sendable, Hashable, Identifiable {
    public let id: String
    public let name: String

    public init(id: String, name: String) {
        self.id = id
        self.name = name
    }
}

/// A category by its ledger name, which narrows the journal to it, and as it reads.
public struct CategoryRef: Decodable, Sendable, Hashable {
    /// "Expenses:Food:Dining".
    public let name: String
    /// "Dining".
    public let label: String

    public init(name: String, label: String) {
        self.name = name
        self.label = label
    }
}

public struct Entry: Decodable, Sendable, Equatable, Identifiable {
    /// Where it is in the journal the filter leaves, counting from the newest.
    public let position: Int
    public let kind: Kind
    public let date: Day
    public let payee: String
    public let narration: String
    /// The viewer's accounts it moved, money out first: a transfer's from and to.
    public let accounts: [AccountRef]
    /// Its one category, when it has exactly one.
    public let category: CategoryRef?
    /// Nobody has checked it yet.
    public let unreviewed: Bool
    /// Its category is one hearth suggested, which nobody has checked yet.
    public let suggested: Bool
    public let waiting: Waiting?
    public let checked: Checked?
    /// What it did to the accounts it names; for a transfer between two of them, what moved.
    public let amount: Money
    public let sides: [Side]
    /// The review it waits on, when it is one of the viewer's own rows nobody has checked:
    /// decided from here as from Review.
    public let review: String?

    public var id: Int { position }

    public enum Kind: String, Decodable, Sendable {
        case transaction
        /// Money moved between accounts and nowhere else.
        case transfer
        /// A balance check, on the last day it covers.
        case balance
    }

    /// What an unreviewed entry with no category is waiting to be told.
    public enum Waiting: String, Decodable, Sendable {
        /// Where its money went.
        case category
        /// The other side of a card payment, not found yet.
        case transfer
    }

    /// What a balance check checked the books against.
    public enum Checked: String, Decodable, Sendable {
        case bank
        case brokerage
        case books
    }

    /// Whether the entry moved money between two of the viewer's own accounts, shown once.
    public var isOwnTransfer: Bool { kind == .transfer && accounts.count > 1 }
}

/// One side of an entry: one of the viewer's accounts, a category, money still waiting to be
/// told where it went, or another account the viewer does not see into.
public struct Side: Decodable, Sendable, Equatable {
    public let amount: Money
    public let account: AccountRef?
    public let category: CategoryRef?
    public let waiting: Bool
    public let elsewhere: Bool
}
