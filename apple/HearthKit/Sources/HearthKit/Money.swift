import Foundation

/// An amount of money, exactly as the books hold it.
///
/// The engine writes money as a decimal string so that nothing is rounded on the way to the
/// screen; a JSON number would pass through a binary float.
public struct Money: Decodable, Sendable, Hashable, Comparable {
    public let amount: Decimal

    public init(_ amount: Decimal) {
        self.amount = amount
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()
        let text = try container.decode(String.self)
        guard Self.isDecimal(text), let amount = Decimal(string: text, locale: Self.plain) else {
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "not an amount of money: \(text)")
        }
        self.amount = amount
    }

    public static func < (lhs: Money, rhs: Money) -> Bool {
        lhs.amount < rhs.amount
    }

    private static let plain = Locale(identifier: "en_US_POSIX")

    /// `Decimal(string:)` reads as much as it can and ignores the rest, so "12abc" would be 12.
    private static func isDecimal(_ text: String) -> Bool {
        text.wholeMatch(of: /-?\d+(\.\d+)?/) != nil
    }
}

/// A day on the calendar, with no time and no time zone: the day a balance is as of, or a
/// price is from.
public struct Day: Decodable, Sendable, Hashable, Comparable {
    public let year: Int
    public let month: Int
    public let day: Int

    public init(year: Int, month: Int, day: Int) {
        self.year = year
        self.month = month
        self.day = day
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()
        let text = try container.decode(String.self)
        guard let match = text.wholeMatch(of: /(\d{4})-(\d{2})-(\d{2})/),
            let year = Int(match.1), let month = Int(match.2), let day = Int(match.3),
            (1...12).contains(month), (1...31).contains(day)
        else {
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "not a day: \(text)")
        }
        self.init(year: year, month: month, day: day)
    }

    public static func < (lhs: Day, rhs: Day) -> Bool {
        (lhs.year, lhs.month, lhs.day) < (rhs.year, rhs.month, rhs.day)
    }

    /// Noon on this day in `calendar`, for charts and for formatting: noon, so that no change
    /// of clocks can move it onto the day before or after.
    public func date(in calendar: Calendar = .current) -> Date {
        let parts = DateComponents(year: year, month: month, day: day, hour: 12)
        return calendar.date(from: parts) ?? .distantPast
    }
}
