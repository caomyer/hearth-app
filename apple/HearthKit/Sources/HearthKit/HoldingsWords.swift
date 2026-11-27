import Foundation

/// How holdings are written for a person, by hearth's rules (`design/system.html`, Numbers):
/// a holding to the cent, a total in whole units, a gain by its sign, arrow and word before
/// any colour, and every absence in words of its own, never a blank or a zero.
extension Figures {
    /// Units held, to at least three places and to every place the books hold them:
    /// "1,284.301", "15.000", "37.0625". A count is never rounded away from what is held.
    public static func units(_ quantity: Quantity, locale: Locale = .current) -> String {
        let places = max(3, decimalPlaces(quantity.amount))
        let style = Decimal.FormatStyle(locale: locale)
            .precision(.fractionLength(places))
            .grouping(.automatic)
        let text = abs(quantity.amount).formatted(style)
        return quantity.amount < 0 ? "\u{2212}" + text : text
    }

    /// What a holding has gained or lost and not realised yet, by its sign, its arrow and its
    /// word: "▲ +$1,172.80 gain", "▼ −$310.20 loss", "No gain or loss". `whole` writes it in
    /// whole units, as a total is.
    public static func gain(
        _ money: Money, currency: String, whole: Bool = false, locale: Locale = .current
    ) -> String {
        let size = Money(abs(money.amount))
        let amount = whole ? total(size, currency: currency, locale: locale) : cents(size, currency: currency, locale: locale)
        if !amount.contains(where: { ("1"..."9").contains($0) }) { return "No gain or loss" }
        return money.amount > 0 ? "▲ +\(amount) gain" : "▼ \u{2212}\(amount) loss"
    }

    /// Whether a gain, as written, is one at all: a gain of less than a cent is neither.
    public static func gainDirection(_ money: Money, whole: Bool = false) -> Direction {
        var value = money.amount
        var result = Decimal()
        NSDecimalRound(&result, &value, whole ? 0 : 2, .plain)
        if result == 0 { return .none }
        return result > 0 ? .up : .down
    }

    private static func decimalPlaces(_ value: Decimal) -> Int {
        max(0, -value.exponent)
    }
}

extension Holdings.Price {
    /// Which close it is: "Wed close" within the last week, "Oct 5 close" before, as the
    /// pages say it.
    public func close(locale: Locale = .current) -> String {
        if age < 7 {
            return day.date(in: Day.calendar).formatted(Date.FormatStyle(locale: locale).weekday(.abbreviated).utc) + " close"
        }
        return "\(Figures.day(day, locale: locale)) close"
    }

    /// How old it is, in days: "10 days old".
    public var ageText: String {
        age == 1 ? "1 day old" : "\(age) days old"
    }

    /// Where it came from, in words.
    public var sourceText: String { Holdings.source(source) }
}

extension Holdings {
    /// What a price's source is called (`hearth.books.PRICE_SOURCES`), as the pages call it.
    public static func source(_ source: String) -> String {
        switch source {
        case "eodhd": "EODHD"
        case "plaid": "Its brokerage's own price, through Plaid"
        case "hand": "Entered by hand"
        case "demo": "Invented for the sample"
        default: source
        }
    }

    /// A total at market value or at cost, in whole units, and "at least" when it leaves a
    /// holding out.
    public static func total(_ totals: Totals, atCost: Bool, currency: String, locale: Locale = .current) -> String {
        let text = Figures.total(atCost ? totals.cost : totals.value, currency: currency, locale: locale)
        return (atCost ? totals.uncosted : totals.unpriced) > 0 ? "at least \(text)" : text
    }

    /// What a total leaves out, in one sentence each, or none when it leaves out nothing.
    public static func leavesOut(_ totals: Totals, atCost: Bool) -> [String] {
        var said: [String] = []
        if atCost, totals.uncosted > 0 {
            said.append("Leaves out \(count(totals.uncosted, "holding")) whose cost the books do not hold.")
        }
        if !atCost, totals.unpriced > 0 {
            said.append("Leaves out \(count(totals.unpriced, "holding")) with no price.")
        }
        if totals.ungained > 0 {
            said.append("The gain is without \(count(totals.ungained, "holding")) whose gain is not known.")
        }
        if totals.waiting > 0 {
            said.append("Without \(count(totals.waiting, "account")) whose holdings are not known yet.")
        }
        return said
    }

    static func count(_ number: Int, _ thing: String) -> String {
        number == 1 ? "1 \(thing)" : "\(number.formatted()) \(thing)s"
    }
}

extension Holdings.Held {
    /// What it is called: its name, its symbol when the books name it nothing, or "Cash".
    public var title: String {
        isCash ? "Cash" : name ?? symbol
    }

    /// What it trades as, under its name: "TMIX", "No ticker", or "Uninvested" for cash.
    public var subtitle: String {
        if isCash { return "Uninvested" }
        return ticker ?? "No ticker"
    }
}

extension Holdings.HeldAccount {
    /// Why it shows nothing, when the books do not know what it holds: to its owners, why;
    /// to anyone else, only that it is not known.
    public var absence: String? {
        guard !known else { return nil }
        if let why, let reason = Account.unknown(why) { return reason }
        return "What it holds is not known yet."
    }

    /// Whose it is, when it is not the viewer's alone.
    public var whose: String? {
        guard !(yours && owners.count == 1), !owners.isEmpty else { return nil }
        return owners.formatted(.list(type: .and))
    }
}

extension Holdings.Withheld {
    /// That their positions are private, and whether their value is inside the household's
    /// net worth, as the net worth page says it.
    public var sentence: String {
        let whose = joint
            ? "The joint accounts of \(owners.formatted(.list(type: .and))) are private to their owners."
            : "\(name)\u{2019}s positions are private to \(name)."
        return inTotal ? "\(whose) Their value is inside the household\u{2019}s net worth." : whose
    }
}
