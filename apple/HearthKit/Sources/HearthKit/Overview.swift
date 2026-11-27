import Foundation

/// The Overview screen: the household's net worth and how it got here, as the viewer may see
/// it (`hearth.native.doorway.overview`).
public struct Overview: Decodable, Sendable, Equatable {
    /// When the books behind these figures were computed.
    public let computedAt: Date
    /// The day the figures are as of.
    public let asOf: Day
    public let currency: String
    /// Whether newer books are being computed right now.
    public let recomputing: Bool
    /// Whether anyone in the household has an account in the books yet. Until someone does,
    /// there is no figure to show: a total over nothing is not a total of zero.
    public let connected: Bool
    public let netWorth: NetWorth
    /// Net worth at the end of each month, oldest first, ending with today.
    public let history: [Point]
    /// What waits for the viewer in Review.
    public let review: Waiting

    /// What waits for the viewer in Review: their own rows nobody has checked, and how much of
    /// their own spending this month rests on them, when any does. Each figure is absent when
    /// there is none, the viewer may not know it, or the engine is older than it.
    public struct Waiting: Decodable, Sendable, Equatable {
        public let waiting: Int
        /// This month's own spending resting on unreviewed rows, at their suggested category.
        public let spending: Money?
        /// This month's rows taking money out that nobody has said what they were yet, such as
        /// a new merchant's: in no spending total until placed in Review.
        public let unplacedSpending: Unplaced?
        /// The same, of rows bringing money in: in no income total until placed.
        public let unplacedIncome: Unplaced?
        /// What this month was booked to expense categories that are not spending, such as a
        /// retirement contribution, which no spending figure counts.
        public let notSpending: Money?

        public init(
            waiting: Int, spending: Money? = nil, unplacedSpending: Unplaced? = nil,
            unplacedIncome: Unplaced? = nil, notSpending: Money? = nil
        ) {
            self.waiting = waiting
            self.spending = spending
            self.unplacedSpending = unplacedSpending
            self.unplacedIncome = unplacedIncome
            self.notSpending = notSpending
        }
    }

    /// Rows a total leaves out as nobody has said what they were yet: how many, and how much
    /// money they moved its way.
    public struct Unplaced: Decodable, Sendable, Equatable {
        public let rows: Int
        public let moved: Money
    }

    public struct NetWorth: Decodable, Sendable, Equatable {
        public let value: Money
        /// False while a connected account's balance has not arrived: the total is not known.
        public let isKnown: Bool
        /// True when the total leaves out a part of the household, named in `without`.
        public let isPartial: Bool
        /// True when a holding has no price, so the total is at least this much.
        public let isFloor: Bool
        public let without: [String]
        public let sinceMonthStart: Money?
        public let overYear: Money?
        /// The day the prices behind the total are from.
        public let pricedOn: Day?
        public let stalePrices: Int
    }

    public struct Point: Decodable, Sendable, Equatable {
        public let day: Day
        public let value: Money
        public let isFloor: Bool
    }
}
