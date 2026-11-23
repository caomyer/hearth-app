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
