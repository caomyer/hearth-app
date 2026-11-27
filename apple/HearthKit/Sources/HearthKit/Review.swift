import Foundation

/// The viewer's review queue (`hearth.native.review.queue_screen`): how much waits and how
/// much is done, as hearth's review desk counts it, then the groups in the order asked and
/// every row the filter leaves, in that order.
public struct ReviewQueue: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    public let order: ReviewOrder
    /// Every row waiting on the viewer, whatever the filter.
    public let remaining: Tally
    /// What the filter leaves.
    public let shown: Tally
    public let filtered: Bool
    public let reviewed: Int
    public let total: Int
    /// Groups with anything waiting, whatever the filter.
    public let groupsWaiting: Int
    public let oldest: Day?
    /// Rows that came back because their suggestion changed after they were accepted.
    public let changed: Int
    /// Rows that came back because their bank changed them after they were reviewed.
    public let revised: Int
    /// Rows that came back because their decision would change an account whose balance its
    /// bank decides now, which the books do not apply (`review.decision.guarded`).
    public let kept: Int
    /// Rows waiting to be told where their money went, counted apart.
    public let elsewhere: Tally
    public let accounts: [Choice]
    public let categories: [Choice]
    public let groups: [ReviewGroup]
    public let rows: [ReviewRow]

    /// A count of rows and the money they move, either way.
    public struct Tally: Decodable, Sendable, Equatable {
        public let rows: Int
        public let moved: Money
    }

    /// One value the queue can be narrowed to, and how many rows wait under it.
    public struct Choice: Decodable, Sendable, Hashable, Identifiable {
        public let value: String
        public let label: String
        public let rows: Int
        public var id: String { value }

        public init(value: String, label: String, rows: Int) {
            self.value = value
            self.label = label
            self.rows = rows
        }
    }

    /// A row on screen, by its id.
    public func row(_ id: String) -> ReviewRow? {
        rows.first { $0.id == id }
    }

    public func group(_ name: String) -> ReviewGroup? {
        groups.first { $0.name == name }
    }
}

/// What the queue puts first, each in the one direction useful for review.
public enum ReviewOrder: String, Decodable, Sendable, CaseIterable, Hashable {
    case size, confidence, amount, date, account, category

    /// As the review page names it.
    public var label: String {
        switch self {
        case .size: "Group size"
        case .confidence: "Lowest confidence"
        case .amount: "Largest amount"
        case .date: "Newest"
        case .account: "Account"
        case .category: "Category"
        }
    }
}

/// Rows of one kind: what one decision may settle, when it is one decision.
public struct ReviewGroup: Decodable, Sendable, Equatable, Identifiable {
    public let name: String
    /// All its rows waiting, whatever the filter.
    public let size: Int
    public let total: Money
    public let moved: Money
    public let suggested: [CategoryRef]
    /// Whether accepting it is one decision about one kind of transaction (`desk.one_decision`).
    public let oneDecision: Bool
    /// Why the least sure of its rows is suggested as it is, in words: "Dining twice before",
    /// "Bank says Restaurant", "Low confidence". Never a percentage.
    public let leastSure: String
    /// Its rows the filter leaves, by id, in the queue's order.
    public let rows: [String]

    public var id: String { name }
}

/// One row waiting for review.
public struct ReviewRow: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let kind: Kind
    public let group: String
    public let date: Day
    /// The merchant, cleaned up, when the bank or the import names one.
    public let payee: String?
    /// The bank's own words for it.
    public let narration: String
    public let account: AccountRef?
    public let amount: Money
    public let suggested: CategoryRef?
    /// Why it is suggested, or how sure the suggestion is, in words: never a percentage.
    public let reason: String
    /// Whether it may be excluded: not when its bank counts it in the balance it reports.
    public let excludable: Bool
    /// What it says, which binds a decision to the row as it was shown.
    public let says: String

    /// What the row is called on screen.
    public var title: String {
        if let payee, !payee.isEmpty { return payee }
        return narration.isEmpty ? "No description" : narration
    }

    /// Whether it is a carried account's difference with its bank, which is only confirmed.
    public var isReconciling: Bool { kind == .reconciling }

    /// What kind of row it is.
    public enum Kind: String, Sendable, Equatable {
        /// A transaction its bank or the import sent.
        case transaction
        /// The imported ledger and an account's bank disagree by this much as of a day: it is
        /// confirmed as it is, never booked elsewhere, excluded or accepted with its group.
        case reconciling
    }
}

extension ReviewRow.Kind: Decodable {
    /// A kind this app does not know yet is read as a transaction, whose decisions the engine
    /// still decides by its desk's rules, rather than failing the whole queue.
    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: raw) ?? .transaction
    }
}

/// One row's detail (`hearth.native.review.row_screen`): where it stands, what it may be
/// booked against, and the rows from the same merchant before it.
public struct ReviewDetail: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    public let row: ReviewRow
    public let decision: Standing
    /// What a reviewed row is booked against.
    public let booked: CategoryRef?
    /// The categories the viewer's own rows are or were suggested or booked as.
    public let categories: [CategoryRef]
    /// The viewer's own accounts it may be moved to.
    public let accounts: [AccountRef]
    public let earlier: [Earlier]

    public enum Standing: String, Decodable, Sendable {
        case waiting
        /// Waiting to be told where its money went, which is asked under Where did it go?
        case elsewhere
        case reviewed
        case excluded
    }

    /// An earlier row from the same merchant, and how it stands.
    public struct Earlier: Decodable, Sendable, Equatable {
        public let date: Day
        public let amount: Money
        public let decision: Standing
        /// What it is booked as, or for one still waiting, suggested as.
        public let category: CategoryRef?
    }
}

/// The rows whose other side hearth does not see (`hearth.native.review.where_screen`),
/// grouped so that one answer settles each group.
public struct WherePage: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    public let rows: Int
    public let moved: Money
    public let groups: [WhereGroup]
}

public struct WhereGroup: Decodable, Sendable, Equatable, Identifiable {
    public let key: String
    public let title: String
    public let question: Question
    public let total: Money
    public let moved: Money
    public let since: Day
    /// The last digits the bank's words name for the other side.
    public let mask: String?
    public let describedOnce: Bool
    /// What the viewer said rows like these went into before.
    public let learned: Learned?
    /// The viewer's own accounts hearth does not see, which may answer it.
    public let accounts: [AccountRef]
    public let categories: [CategoryRef]
    /// The account chosen to begin with, or `WhereGroup.new`.
    public let chosen: String
    /// Whether an account, rather than a category, is the answer to begin with.
    public let byAccount: Bool
    public let rows: [ReviewRow]

    public var id: String { key }

    /// What `chosen` is when no account is chosen to begin with.
    public static let new = "new"

    public enum Question: String, Decodable, Sendable {
        /// Money arrived at a card from an account hearth does not see.
        case paidFrom = "paid-from"
        /// Money left a bank account for a card hearth does not see.
        case paidTo = "paid-to"
        case went
        case came
        case loan
    }

    public struct Learned: Decodable, Sendable, Equatable {
        public let account: AccountRef
        public let like: Int
    }

    public var outgoing: Bool { total.amount < 0 }
}
