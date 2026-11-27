import Foundation
import Observation

/// The Statements page: its four tabs, and what every one of them is asked for, the period
/// and how holdings are valued, which the page chooses now and a toolbar filter may later.
@MainActor
@Observable
public final class StatementsModel {
    public enum Tab: String, Sendable, Hashable, CaseIterable, Identifiable {
        case summary
        case incomeStatement
        case balanceSheet
        case years

        public var id: String { rawValue }

        public var title: String {
            switch self {
            case .summary: "Summary"
            case .incomeStatement: "Income statement"
            case .balanceSheet: "Balance sheet"
            case .years: "Year by year"
            }
        }
    }

    public var tab: Tab
    /// The days every tab but Year by year covers: this year so far, or a whole year.
    public private(set) var period: JournalFilter.Period = .yearToDate
    public private(set) var valuation: Valuation = .market
    public private(set) var columns: Columns = .month

    public let summary: ScreenModel<StatementSummary>
    public let incomeStatement: ScreenModel<IncomeStatement>
    public let balanceSheet: ScreenModel<BalanceSheet>
    public let years: ScreenModel<YearByYear>

    public init(
        engine: any Asking, tab: Tab = .summary,
        pause: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        signedOut: @escaping @MainActor () -> Void = {}
    ) {
        self.tab = tab
        summary = ScreenModel(engine: engine, screen: "statement_summary", pause: pause, signedOut: signedOut)
        incomeStatement = ScreenModel(engine: engine, screen: "income_statement", pause: pause, signedOut: signedOut)
        balanceSheet = ScreenModel(engine: engine, screen: "balance_sheet", pause: pause, signedOut: signedOut)
        years = ScreenModel(engine: engine, screen: "year_by_year", pause: pause, signedOut: signedOut)
    }

    /// Every tab over another period.
    public func choose(_ period: JournalFilter.Period) async {
        self.period = period
        await narrow()
    }

    public func choose(_ valuation: Valuation) async {
        self.valuation = valuation
        await narrow()
    }

    public func choose(_ columns: Columns) async {
        self.columns = columns
        await narrow()
    }

    /// The years a period may be: the year in progress so far, then each whole year the
    /// books hold, newest first.
    public var periods: [JournalFilter.Period] {
        let whole = (years.content?.years ?? []).filter { !$0.partial }.map(\.year).sorted(by: >)
        return [.yearToDate] + whole.map { .year($0) }
    }

    private func narrow() async {
        await summary.narrow(to: Self.given(period, valuation: valuation))
        await balanceSheet.narrow(to: Self.given(period, valuation: valuation))
        await incomeStatement.narrow(to: Self.given(period, columns: columns))
        await years.narrow(to: Self.given(nil, valuation: valuation))
    }

    /// What a tab is asked: its days, unless they are this year so far, which the engine
    /// settles from the books' own day, and how it values or splits them, unless as it would.
    nonisolated static func given(
        _ period: JournalFilter.Period?, valuation: Valuation? = nil, columns: Columns? = nil
    ) -> Given {
        var given: Given = [:]
        if let (from, to) = period?.bounds {
            given["from"] = .text(from.iso)
            given["to"] = .text(to.iso)
        }
        if let valuation, valuation != .market { given["valuation"] = .text(valuation.rawValue) }
        if let columns, columns != .month { given["columns"] = .text(columns.rawValue) }
        return given
    }
}
