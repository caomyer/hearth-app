import Foundation

/// How a statement values what is held: at the latest price on or before the day, or at what
/// it cost.
public enum Valuation: String, Sendable, Hashable, CaseIterable {
    case market
    case cost
}

/// How an income statement splits its period into columns.
public enum Columns: String, Sendable, Hashable, CaseIterable {
    case month
    case quarter
    case year
}

/// The days a statement covers, both included.
public struct StatementPeriod: Decodable, Sendable, Hashable {
    public let from: Day
    public let to: Day

    public init(from: Day, to: Day) {
        self.from = from
        self.to = to
    }
}

/// Whose accounts a statement reads and what it leaves out (`hearth.disclosure.statements`).
/// A statement reads only the parts of the household the viewer may see in full.
public struct StatementScope: Decodable, Sendable, Equatable {
    /// "Avery", "Joint accounts".
    public let parts: [String]
    /// The parts left out, each named.
    public let without: [String]
    /// What it is without of accounts hearth does not see, in the words a total uses.
    public let leavesOut: String?
    /// How many of its balances are not known yet; while any are, nothing that adds up
    /// balances is a figure.
    public let awaiting: Int
}

/// What a change means for the household: never up or down, which the sign says.
public enum Tone: String, Decodable, Sendable {
    case favourable
    case unfavourable
    case quiet

    public init(from decoder: any Decoder) throws {
        // A tone an older app does not know is told without colour, never as good or bad.
        self = Tone(rawValue: try decoder.singleValueContainer().decode(String.self)) ?? .quiet
    }
}

/// One line of a statement: this period, the one it is set against, and what the change means.
public struct StatementLine: Decodable, Sendable, Equatable, Identifiable {
    public let key: String
    public let label: String
    /// Not known when nil, never zero.
    public let now: Money?
    /// The same days a year before, or for net worth the start of the period; nil where the
    /// books do not hold that period in full.
    public let before: Money?
    public let change: Money?
    /// The change as a share of `before`.
    public let share: Money?
    public let tone: Tone
    /// The categories whose rows it adds up; empty for a line that is no sum of rows.
    public let categories: [String]
    public let total: Bool
    public let depth: Int

    public var id: String { key }

    /// Whether the line is a ratio, written as a share, not money.
    public var isRate: Bool { key == "savings_rate" }
}

/// What rests on rows nobody has reviewed yet, and the rows nothing has placed yet.
public struct StatementUnreviewed: Decodable, Sendable, Equatable {
    public let rows: Int
    public let income: Money
    public let expenses: Money
    public let unplaced: Int
    public let unplacedOut: Money
    public let unplacedIn: Money
}

/// How the holdings behind a statement were valued.
public struct Valuing: Decodable, Sendable, Equatable {
    public let pricedOn: Day?
    /// Holdings with no price, not valued at market value, as no screen values them: a market
    /// value they are in is at least what it says.
    public let unpriced: [String]
    /// Holdings with neither a price nor a cost, left out.
    public let unvalued: [String]
    /// How many positions are valued at an old price.
    public let stale: Int
}

/// The Statements Summary: this period against the same days a year before
/// (`hearth.native.statements`).
public struct StatementSummary: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    public let period: StatementPeriod
    public let against: StatementPeriod
    public let valuation: String
    /// Whether the books hold every month of the period in full.
    public let covered: Bool
    public let againstCovered: Bool
    public let scope: StatementScope
    public let netWorth: StatementLine
    public let savingsRate: StatementLine
    public let spending: StatementLine
    /// What the market added or took away; nil while a balance is not known.
    public let market: Money?
    public let lines: [StatementLine]
    /// Why net worth changed; nil while a balance is not known.
    public let why: Why?
    public let valuing: Valuing
    public let unreviewed: StatementUnreviewed
    /// Whether net worth leaves out holdings with no price, so is at least what it says.
    public let isFloor: Bool
    /// Accounts whose holdings the books know only from inside the period: net worth's start,
    /// and why it changed, are without them.
    public let late: [String]

    /// Why net worth changed: from the start, through each part, to the end, and whatever is
    /// left, shown, never hidden.
    public struct Why: Decodable, Sendable, Equatable {
        public let start: Money
        public let saved: Money
        public let investmentIncome: Money
        /// Nil at cost, where nothing is valued at the market.
        public let market: Money?
        public let other: Money
        public let others: [Part]
        public let end: Money
        public let difference: Money

        public struct Part: Decodable, Sendable, Equatable, Identifiable {
            public let key: String
            public let label: String
            public let amount: Money

            public var id: String { key }
        }
    }
}

/// The income statement: the income and expense account tree, a cell per column.
public struct IncomeStatement: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    public let period: StatementPeriod
    public let columnsBy: String
    public let covered: Bool
    public let scope: StatementScope
    public let columns: [Column]
    public let income: Node
    public let expenses: Node
    public let net: Net
    public let unreviewed: StatementUnreviewed

    public struct Column: Decodable, Sendable, Equatable {
        public let from: Day
        public let to: Day
        /// Whether the period cuts the month, quarter or year short.
        public let partial: Bool
        public let unreviewed: StatementUnreviewed
    }

    /// One account and everything beneath it.
    public struct Node: Decodable, Sendable, Equatable, Identifiable {
        /// Its ledger name, a category or a branch of them.
        public let name: String
        public let label: String
        /// The kind of money it is, for an account the books book to; nil for a branch.
        public let kind: String?
        public let cells: [Money]
        public let total: Money
        public let children: [Node]

        public var id: String { name }

        public init(name: String, label: String, kind: String?, cells: [Money], total: Money, children: [Node]) {
            self.name = name
            self.label = label
            self.kind = kind
            self.cells = cells
            self.total = total
            self.children = children
        }

        /// Its children, or nil for a leaf, as an outline asks.
        public var outline: [Node]? { children.isEmpty ? nil : children }

        /// Every category in it that the books book to: the rows it adds up.
        public var categories: [String] {
            (kind != nil ? [name] : []) + children.flatMap(\.categories)
        }
    }

    public struct Net: Decodable, Sendable, Equatable {
        public let cells: [Money]
        public let total: Money
    }
}

/// The balance sheet: every account at the start of the period beside its end.
public struct BalanceSheet: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    public let period: StatementPeriod
    public let valuation: String
    public let scope: StatementScope
    public let assets: Row
    public let liabilities: Row
    public let netWorth: Row
    public let valuing: Valuing

    /// What an account or a group held at the start or the end.
    public struct Held: Decodable, Sendable, Equatable {
        public let value: Money
        /// What it cost by its lots, as Holdings says it; nil where nothing says.
        public let cost: Money?
        /// Gain not yet realised: what it is worth less what it cost; nil without a cost or
        /// a price.
        public let gain: Money?
        public let securities: Bool
        /// Whether `value` leaves out holdings with no price.
        public let isFloor: Bool
    }

    /// An account, or a group of them.
    public struct Row: Decodable, Sendable, Equatable, Identifiable {
        public let label: String
        /// The account, which opens its page; nil for a group.
        public let account: AccountRef?
        /// The day an account's history begins, when that is inside the period.
        public let since: Day?
        /// Nil while a balance in it is not known.
        public let start: Held?
        public let end: Held?
        public let change: Money?
        public let children: [Row]

        public var id: String { account?.id ?? "group:" + label }

        public var outline: [Row]? { children.isEmpty ? nil : children }

        /// Whether its cost and gain are worth a column: it holds securities at either end.
        public var invested: Bool { start?.securities == true || end?.securities == true }
    }
}

/// Year by year: every year the books hold.
public struct YearByYear: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    public let valuation: String
    public let scope: StatementScope
    public let years: [Year]

    public struct Year: Decodable, Sendable, Equatable, Identifiable {
        public let year: Int
        /// The year in progress, so far.
        public let partial: Bool
        /// Too little of it is in the books to set against another year.
        public let sparse: Bool
        public let netWorth: Money?
        public let pay: Money
        public let spending: Money
        public let saved: Money
        public let savingsRate: Money?

        public var id: Int { year }
    }
}
