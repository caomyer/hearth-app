import Foundation

/// How figures are written for a person, by the rules of hearth's design system
/// (`design/system.html`, Numbers).
public enum Figures {
    /// A total, in whole units of its currency: "$846,684", "−$1,285".
    ///
    /// Totals are whole dollars; only what reconciles keeps cents. The minus is a true minus
    /// sign, never a hyphen. Halves round away from zero, as the pages round them.
    public static func total(_ money: Money, currency: String, locale: Locale = .current) -> String {
        let style = Decimal.FormatStyle.Currency(code: currency, locale: locale)
            .precision(.fractionLength(0))
            .rounded(rule: .toNearestOrAwayFromZero)
        return trueMinus(magnitude(money).formatted(style), negative: money.amount < 0)
    }

    /// A balance to the cent, as what reconciles is written: "$3,250.40", "−$612.15".
    ///
    /// Cents round half to even, as the pages round an amount, so a figure the books hold to
    /// more places is never nudged one way.
    public static func cents(_ money: Money, currency: String, locale: Locale = .current) -> String {
        let style = Decimal.FormatStyle.Currency(code: currency, locale: locale)
            .precision(.fractionLength(2))
            .rounded(rule: .toNearestOrEven)
        return trueMinus(magnitude(money).formatted(style), negative: money.amount < 0)
    }

    /// A transaction's amount, to the cent and without the currency, signed: "+3,299.60",
    /// "−84.12". `signed: false` writes what is neither in nor out, as what moved between two
    /// of one's own accounts or a balance checked, with no plus: "300.00", "−612.15".
    public static func amount(_ money: Money, signed: Bool = true, locale: Locale = .current) -> String {
        let style = Decimal.FormatStyle(locale: locale)
            .precision(.fractionLength(2))
            .rounded(rule: .toNearestOrEven)
            .grouping(.automatic)
        let text = magnitude(money).formatted(style)
        let isZero = !text.contains(where: { ("1"..."9").contains($0) })
        if isZero { return text }
        if money.amount < 0 { return "\u{2212}" + text }
        return signed ? "+" + text : text
    }

    /// A day as a heading of the journal: "Saturday, Oct 3", with its year when it is not
    /// the year of `asOf`: "Saturday, Oct 3, 2025".
    public static func heading(_ day: Day, asOf: Day, locale: Locale = .current) -> String {
        var style = Date.FormatStyle(locale: locale).weekday(.wide).month(.abbreviated).day()
        if day.year != asOf.year { style = style.year() }
        return day.date(in: Day.calendar).formatted(style.utc)
    }

    /// A day in a line of text: "Oct 3", with its year when it is not the year of `asOf`.
    public static func day(_ day: Day, asOf: Day? = nil, locale: Locale = .current) -> String {
        var style = Date.FormatStyle(locale: locale).month(.abbreviated).day()
        if let asOf, day.year != asOf.year { style = style.year() }
        return day.date(in: Day.calendar).formatted(style.utc)
    }

    /// Two days as a span: "Jan 1 to Oct 15, 2026", "Dec 3, 2025 to Jan 2, 2026".
    public static func span(from: Day, to: Day, locale: Locale = .current) -> String {
        let end = Date.FormatStyle(locale: locale).month(.abbreviated).day().year()
        var start = Date.FormatStyle(locale: locale).month(.abbreviated).day()
        if from.year != to.year { start = start.year() }
        let first = from.date(in: Day.calendar).formatted(start.utc)
        return "\(first) to \(to.date(in: Day.calendar).formatted(end.utc))"
    }

    /// A change, said four ways as the design asks: arrow, word, amount, and the colour its
    /// caller gives it. "▲ Up $12,960", "▼ Down $1,205", "No change".
    public static func change(_ money: Money, currency: String, locale: Locale = .current) -> String {
        let amount = total(Money(abs(money.amount)), currency: currency, locale: locale)
        if rounded(money) == 0 { return "No change" }
        return money.amount > 0 ? "▲ Up \(amount)" : "▼ Down \(amount)"
    }

    /// Which way a change went once written in whole units: a change of forty cents is none.
    public static func direction(_ money: Money) -> Direction {
        let whole = rounded(money)
        if whole == 0 { return .none }
        return whole > 0 ? .up : .down
    }

    /// When something happened, as a person says it: "today, 6:00 AM", "yesterday, 6:00 AM",
    /// "Nov 14, 6:00 AM". Never "3 seconds ago": a moment that reads differently each time it
    /// is looked at tells nobody when the books were computed.
    public static func moment(
        _ date: Date, now: Date = .now, calendar: Calendar = .current, locale: Locale = .current
    ) -> String {
        var time = Date.FormatStyle(date: .omitted, time: .shortened)
        time.calendar = calendar
        time.timeZone = calendar.timeZone
        time.locale = locale
        let day: String
        if calendar.isDate(date, inSameDayAs: now) {
            day = "today"
        } else if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
            calendar.isDate(date, inSameDayAs: yesterday)
        {
            day = "yesterday"
        } else {
            var style = Date.FormatStyle.dateTime.month(.abbreviated).day()
            style.calendar = calendar
            style.timeZone = calendar.timeZone
            style.locale = locale
            day = date.formatted(style)
        }
        return "\(day), \(date.formatted(time))"
    }

    public enum Direction: Sendable {
        case up, down, none
    }

    private static func magnitude(_ money: Money) -> Decimal {
        abs(money.amount)
    }

    private static func rounded(_ money: Money) -> Decimal {
        var value = money.amount
        var result = Decimal()
        NSDecimalRound(&result, &value, 0, .plain)
        return result
    }

    private static func trueMinus(_ text: String, negative: Bool) -> String {
        // A total that rounds to zero is not written as minus zero.
        let isZero = !text.contains(where: { ("1"..."9").contains($0) })
        return negative && !isZero ? "\u{2212}\(text)" : text
    }
}
