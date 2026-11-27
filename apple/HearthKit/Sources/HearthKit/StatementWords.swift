import Foundation

/// How a statement's figures are written for a person: shares as percentages, changes with
/// their arrow, word and amount, and what a statement reads and leaves out, in a sentence.
public enum StatementWords {
    /// A ratio as a whole percentage: "47%", "−12%".
    public static func rate(_ share: Money, locale: Locale = .current) -> String {
        let style = Decimal.FormatStyle.Percent(locale: locale)
            .precision(.fractionLength(0))
            .rounded(rule: .toNearestOrAwayFromZero)
        let text = abs(share.amount).formatted(style)
        let isZero = !text.contains(where: { ("1"..."9").contains($0) })
        return share.amount < 0 && !isZero ? "\u{2212}" + text : text
    }

    /// A line's figure: money in whole units, or a rate as a percentage; "—" when not known.
    public static func figure(_ value: Money?, rate: Bool, currency: String, locale: Locale = .current) -> String {
        guard let value else { return "\u{2014}" }
        return rate ? self.rate(value, locale: locale) : Figures.total(value, currency: currency, locale: locale)
    }

    /// A line's change, with its arrow and word, and its share where it has one:
    /// "▲ Up $4,170, 7%", "▼ Down 3 points", "No change". Nil when nothing is set against it.
    public static func change(_ line: StatementLine, currency: String, locale: Locale = .current) -> String? {
        guard let change = line.change else { return nil }
        if line.isRate {
            let points = (change.amount * 100)
            var rounded = Decimal()
            var value = points
            NSDecimalRound(&rounded, &value, 0, .plain)
            if rounded == 0 { return "No change" }
            let count = abs(rounded).formatted(.number.locale(locale))
            let unit = abs(rounded) == 1 ? "point" : "points"
            return rounded > 0 ? "▲ Up \(count) \(unit)" : "▼ Down \(count) \(unit)"
        }
        let said = Figures.change(change, currency: currency, locale: locale)
        guard Figures.direction(change) != .none, let share = line.share else { return said }
        return "\(said), \(rate(Money(abs(share.amount)), locale: locale))"
    }

    /// What a change means, in words, for whoever cannot see its colour.
    public static func meaning(_ tone: Tone) -> String? {
        switch tone {
        case .favourable: "better for the household"
        case .unfavourable: "worse for the household"
        case .quiet: nil
        }
    }

    /// Whose accounts a statement reads, and whose it leaves out: "Avery and the joint
    /// accounts, without Casey", "Your accounts" when they are the viewer's alone.
    public static func scope(_ scope: StatementScope) -> String {
        let parts = scope.parts.map { $0 == "Joint accounts" ? "the joint accounts" : $0 }
        var text = parts.isEmpty ? "No accounts" : parts.formatted(.list(type: .and))
        text = text.prefix(1).uppercased() + text.dropFirst()
        if !scope.without.isEmpty {
            let left = scope.without.map { $0 == "Joint accounts" ? "the joint accounts" : $0 }
            text += ", without " + left.formatted(.list(type: .and))
        }
        if let unseen = scope.leavesOut {
            text += scope.without.isEmpty ? ", without " + unseen : " and " + unseen
        }
        return text
    }

    /// The periods a summary sets side by side: "Jan 1 to Oct 15, 2026, against Jan 1 to
    /// Oct 15, 2025".
    public static func against(_ period: StatementPeriod, _ against: StatementPeriod, locale: Locale = .current) -> String {
        let now = Figures.span(from: period.from, to: period.to, locale: locale)
        let then = Figures.span(from: against.from, to: against.to, locale: locale)
        return "\(now), against \(then)"
    }

    /// What rests on rows not reviewed yet, and what nothing has placed: "3 transactions not
    /// reviewed yet, $1,120 of spending and $300 of income", "2 placed nowhere yet, $79 out".
    public static func unreviewed(_ found: StatementUnreviewed, currency: String, locale: Locale = .current) -> [String] {
        var lines: [String] = []
        if found.rows > 0 {
            var line = found.rows == 1 ? "1 transaction not reviewed yet" : "\(found.rows.formatted()) transactions not reviewed yet"
            var amounts: [String] = []
            if found.expenses.amount != 0 { amounts.append("\(Figures.total(found.expenses, currency: currency, locale: locale)) of spending") }
            if found.income.amount != 0 { amounts.append("\(Figures.total(found.income, currency: currency, locale: locale)) of income") }
            if !amounts.isEmpty { line += ", " + amounts.formatted(.list(type: .and)) }
            lines.append(line + ".")
        }
        if found.unplaced > 0 {
            var line = found.unplaced == 1 ? "1 not placed in any category yet" : "\(found.unplaced.formatted()) not placed in any category yet"
            var moved: [String] = []
            if found.unplacedOut.amount != 0 { moved.append("\(Figures.total(found.unplacedOut, currency: currency, locale: locale)) out") }
            if found.unplacedIn.amount != 0 { moved.append("\(Figures.total(found.unplacedIn, currency: currency, locale: locale)) in") }
            if !moved.isEmpty { line += ", " + moved.formatted(.list(type: .and)) }
            lines.append(line + ", counted in no total.")
        }
        return lines
    }

    /// How holdings were valued, and what that leaves to say: "Prices from Fri, Nov 20.
    /// ZQX has no price, so it is valued at what it cost."
    public static func valuing(_ valuing: Valuing, locale: Locale = .current) -> [String] {
        var lines: [String] = []
        if let day = valuing.pricedOn {
            let style = Date.FormatStyle(locale: locale).weekday(.abbreviated).month(.abbreviated).day()
            lines.append("Prices from \(day.date(in: Day.calendar).formatted(style.utc)).")
        }
        if !valuing.unpriced.isEmpty {
            let named = valuing.unpriced.formatted(.list(type: .and))
            lines.append(
                valuing.unpriced.count == 1
                    ? "\(named) has no price, so it is not valued: each figure it is in is at least what it says."
                    : "\(named) have no price, so they are not valued: each figure they are in is at least what it says.")
        }
        if let line = later(valuing.later) {
            lines.append(line)
        }
        if valuing.stale > 0 {
            lines.append(valuing.stale == 1 ? "One price is older." : "\(valuing.stale.formatted()) prices are older.")
        }
        if !valuing.unvalued.isEmpty {
            let named = valuing.unvalued.formatted(.list(type: .and))
            lines.append("\(named) can be valued neither at a price nor at a cost, so it is left out.")
        }
        return lines
    }

    /// What a figure says of amounts booked before anything priced them: "2 amounts were
    /// booked before anything priced them, and are at the first price there was."
    public static func later(_ count: Int) -> String? {
        guard count > 0 else { return nil }
        let what = count == 1 ? "1 amount was" : "\(count.formatted()) amounts were"
        return "\(what) booked before anything priced \(count == 1 ? "it" : "them"), and \(count == 1 ? "is" : "are") at the first price there was."
    }

    /// A column of the income statement as its heading: "Aug", "Q3", "2026", each with its
    /// year when the columns span more than one: "Aug 2025", "Q3 2025". Never "Aug 26",
    /// which reads as a day.
    public static func column(
        _ column: IncomeStatement.Column, by: String, withYear: Bool, locale: Locale = .current
    ) -> String {
        let year = withYear ? " \(column.from.year)" : ""
        switch by {
        case "quarter": return "Q\((column.from.month - 1) / 3 + 1)\(year)"
        case "year": return String(column.from.year)
        default:
            let month = column.from.date(in: Day.calendar).formatted(Date.FormatStyle(locale: locale).month(.abbreviated).utc)
            return month + year
        }
    }
}
