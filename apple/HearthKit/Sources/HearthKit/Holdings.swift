import Foundation

/// The Holdings screen: what the investment accounts the viewer may see hold, each holding with
/// its price and where it came from, its value, cost and gain, and whose positions are withheld
/// (`hearth.native.doorway.holdings`).
public struct Holdings: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    /// The day of the newest price behind what is shown.
    public let pricedOn: Day?
    public let accounts: [HeldAccount]
    /// Every account listed, summed.
    public let totals: Totals
    /// Parts of the household whose positions the viewer may not see.
    public let withheld: [Withheld]

    /// One investment account, and what it holds.
    public struct HeldAccount: Decodable, Sendable, Equatable, Identifiable {
        public let id: String
        public let name: String
        public let owners: [String]
        /// Whether the viewer is one of its owners.
        public let yours: Bool
        /// Whether the books know what it holds. One whose brokerage has not said yet shows
        /// nothing, which is never holding nothing.
        public let known: Bool
        /// Why the books do not know it yet, told to its owners alone
        /// (`hearth.plaid.opening.Unknown`).
        public let why: String?
        public let held: [Held]
        public let totals: Totals
    }

    /// One holding: a security, or the account's cash.
    public struct Held: Decodable, Sendable, Equatable, Identifiable {
        /// What the books hold it as, which its price history is asked by.
        public let symbol: String
        public let ticker: String?
        /// What the books call it, when they name it.
        public let name: String?
        public let isCash: Bool
        public let units: Quantity
        public let freshness: Freshness
        /// Nil for cash, and for a holding with no price it can be valued at.
        public let price: Price?
        /// Nil when it has no price: never zero.
        public let value: Money?
        /// What it cost, from the books' lots; cash costs itself. Nil when the books do not hold
        /// its cost.
        public let cost: Money?
        /// Its value less its cost, when both are known. Cash has none.
        public let gain: Money?

        public var id: String { symbol }
    }

    public enum Freshness: String, Decodable, Sendable {
        /// Valued at a recent close.
        case current
        /// Valued at a close older than its source should lag: it may be off.
        case stale
        /// No price at all: left out of every total, which is then a floor.
        case unpriced
    }

    /// The stored price a holding is valued at, and where it came from.
    public struct Price: Decodable, Sendable, Equatable {
        public let value: Money
        /// The day it is the close of.
        public let day: Day
        /// Where it came from (`hearth.books.PRICE_SOURCES`).
        public let source: String
        public let fetchedAt: Date
        /// Calendar days from its day to the books' own.
        public let age: Int
    }

    /// Holdings summed at market value and at cost, and what each sum leaves out.
    public struct Totals: Decodable, Sendable, Equatable {
        public let value: Money
        /// Holdings with no price, left out of `value`, which is then a floor.
        public let unpriced: Int
        public let cost: Money
        /// Holdings whose cost the books do not hold, left out of `cost`, which is then a floor.
        public let uncosted: Int
        public let gain: Money
        /// Holdings, not cash, whose gain is not known, left out of `gain`.
        public let ungained: Int
        /// Accounts whose holdings are not known yet, so are in none of the sums.
        public let waiting: Int
    }

    /// Someone's positions the viewer may not see: a member's own, or a joint group's.
    public struct Withheld: Decodable, Sendable, Equatable, Identifiable {
        public let name: String
        public let owners: [String]
        public let joint: Bool
        /// Whether their value is inside the household's net worth, which it is only where
        /// they share their total.
        public let inTotal: Bool

        public var id: String { owners.joined(separator: ",") }
    }
}

/// Where one security's price comes from, and every price hearth holds of it
/// (`hearth.native.doorway.price_history`).
public struct PriceHistory: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    public let symbol: String
    public let ticker: String?
    public let name: String?
    public let freshness: Holdings.Freshness
    /// The price it is valued at today, nil when it has none.
    public let price: Holdings.Price?
    /// Oldest first, one a day: the price a valuation of that day would take.
    public let closes: [Close]

    public struct Close: Decodable, Sendable, Equatable {
        public let day: Day
        public let price: Money
        public let source: String
    }
}
