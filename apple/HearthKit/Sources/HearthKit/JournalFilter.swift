import Foundation

/// What the journal is narrowed to: a period, an account, a category, words, and a chip.
///
/// It is only ever what the app asks the engine for. The engine decides what the viewer may
/// see, and narrows to what is asked within that (`hearth.native.doorway.window`).
public struct JournalFilter: Sendable, Hashable {
    public enum Period: Sendable, Hashable {
        /// From the first of the books' year to the books' own day.
        case yearToDate
        case year(Int)
        case month(year: Int, month: Int)
        /// From one day to another, both included.
        case range(from: Day, to: Day)
    }

    public var period: Period
    public var account: AccountRef?
    public var category: CategoryRef?
    public var text: String
    public var show: Show
    /// The rows a statement's figure adds up, which it opens onto.
    public var statement: StatementRows?

    public init(
        period: Period = .yearToDate, account: AccountRef? = nil, category: CategoryRef? = nil,
        text: String = "", show: Show = .everything, statement: StatementRows? = nil
    ) {
        self.period = period
        self.account = account
        self.category = category
        self.text = text
        self.show = show
        self.statement = statement
    }

    /// The rows a statement's figure adds up: those of the categories it names, each exactly,
    /// among the rows the viewer's statements read, which leave out moves to accounts they
    /// do not read (`hearth.native.doorway`, `within`).
    public struct StatementRows: Sendable, Hashable {
        /// What the figure is called: "Dining", "Take-home pay".
        public let label: String
        /// Every category the figure adds up; empty for every row the statements read.
        public let categories: [String]

        public init(label: String, categories: [String]) {
            self.label = label
            self.categories = categories
        }
    }

    /// The words to find, as they will be asked for: without the spaces around them.
    public var words: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// What the engine is asked, for the entries from `offset`.
    public func given(offset: Int, limit: Int) -> Given {
        var given: Given = ["offset": .number(offset), "limit": .number(limit)]
        if let (from, to) = period.bounds {
            given["from"] = .text(from.iso)
            given["to"] = .text(to.iso)
        }
        if let account { given["account"] = .text(account.id) }
        if let category { given["category"] = .text(category.name) }
        // The engine takes at most 200 characters of words to find.
        if !words.isEmpty { given["text"] = .text(String(words.prefix(200))) }
        if show != .everything { given["show"] = .text(show.rawValue) }
        if let statement {
            given["within"] = .text("statements")
            if !statement.categories.isEmpty { given["categories"] = .list(statement.categories) }
        }
        return given
    }

    /// A filter shown as a token, which one click removes.
    public enum Token: Sendable, Hashable, Identifiable {
        case period(Period)
        case account(AccountRef)
        case category(CategoryRef)
        case text(String)
        case statement(StatementRows)

        public var id: String {
            switch self {
            case .period: "period"
            case .account: "account"
            case .category: "category"
            case .text: "text"
            case .statement: "statement"
            }
        }
    }

    /// Every filter beside the chips that narrows the journal, each removable. This year so far
    /// is where the journal starts, so it is no token.
    public var tokens: [Token] {
        var tokens: [Token] = []
        if period != .yearToDate { tokens.append(.period(period)) }
        if let account { tokens.append(.account(account)) }
        if let category { tokens.append(.category(category)) }
        if !words.isEmpty { tokens.append(.text(words)) }
        if let statement { tokens.append(.statement(statement)) }
        return tokens
    }

    /// The filter without `token`: a period removed goes back to this year so far.
    public func removing(_ token: Token) -> JournalFilter {
        var filter = self
        switch token {
        case .period: filter.period = .yearToDate
        case .account: filter.account = nil
        case .category: filter.category = nil
        case .text: filter.text = ""
        case .statement: filter.statement = nil
        }
        return filter
    }
}

extension JournalFilter.Period {
    /// The first and last day, both included; nil for this year so far, which the engine
    /// settles from the books' own day.
    public var bounds: (Day, Day)? {
        switch self {
        case .yearToDate:
            nil
        case .year(let year):
            (Day(year: year, month: 1, day: 1), Day(year: year, month: 12, day: 31))
        case .month(let year, let month):
            (Day(year: year, month: month, day: 1), Day(year: year, month: month, day: Day.days(inMonth: month, of: year)))
        case .range(let from, let to):
            from <= to ? (from, to) : (to, from)
        }
    }

    /// As a person says it: "2026 so far", "2025", "Oct 2026", "Sep 3 to Oct 1, 2026".
    public func label(asOf: Day, locale: Locale = .current) -> String {
        switch self {
        case .yearToDate:
            return "\(asOf.year) so far"
        case .year(let year):
            return String(year)
        case .month(let year, let month):
            let style = Date.FormatStyle(locale: locale).month(.abbreviated).year()
            return Day(year: year, month: month, day: 1).date(in: Day.calendar).formatted(style.utc)
        case .range:
            guard let (from, to) = bounds else { return "" }
            return Figures.span(from: from, to: to, locale: locale)
        }
    }
}

extension Day {
    /// The days in a month of the Gregorian calendar.
    public static func days(inMonth month: Int, of year: Int) -> Int {
        let first = Day(year: year, month: month, day: 1).date(in: calendar)
        return calendar.range(of: .day, in: .month, for: first)?.count ?? 31
    }

    /// The calendar days are worked out in: Gregorian, in UTC, so no time zone moves a day.
    static var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "UTC") ?? .gmt
        return calendar
    }

    /// The day as the engine reads it: "2026-10-15".
    public var iso: String {
        String(format: "%04d-%02d-%02d", year, month, day)
    }

    /// The day from a moment, on the Mac's own calendar.
    public init(_ date: Date, in calendar: Calendar = .current) {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        self.init(year: parts.year ?? 1970, month: parts.month ?? 1, day: parts.day ?? 1)
    }
}

extension Date.FormatStyle {
    /// The same style, read in UTC, as `Day.calendar` works out days.
    var utc: Date.FormatStyle {
        var style = self
        style.timeZone = TimeZone(identifier: "UTC") ?? .gmt
        style.calendar = Day.calendar
        return style
    }
}
