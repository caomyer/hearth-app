import Foundation

/// What each column of a statement's table shows, as text, so a column can be sized to the
/// widest of them and no figure is ever cut: the table draws exactly these strings.

/// A column of the balance sheet, beside the accounts.
public enum SheetColumn: CaseIterable, Sendable {
    case start, end, change, cost, gain
}

extension BalanceSheet {
    /// Every row the table can draw: assets and liabilities with everything beneath them,
    /// then net worth.
    public var everyRow: [Row] {
        func all(_ row: Row) -> [Row] { [row] + row.children.flatMap(all) }
        return all(assets) + all(liabilities) + [netWorth]
    }

    /// What `row` shows in `column`: a figure to the cent, "Not known" while a balance in it
    /// has not arrived, a dash where nothing says what it cost, or nothing at all.
    public static func text(_ row: Row, _ column: SheetColumn, locale: Locale = .current) -> String? {
        switch column {
        case .start: return figure(row.start?.value, known: row.start != nil, locale: locale)
        case .end: return figure(row.end?.value, known: row.end != nil, locale: locale)
        case .change: return row.change.map { Figures.amount($0, locale: locale) }
        case .cost:
            guard row.invested else { return nil }
            return figure(row.end?.cost, known: row.end != nil, locale: locale)
        case .gain:
            guard row.invested else { return nil }
            guard row.end != nil else { return notKnown }
            return row.end?.gain.map { Figures.amount($0, locale: locale) } ?? dash
        }
    }

    /// Every text `column` shows, over every row: what its width must hold.
    public func texts(_ column: SheetColumn, locale: Locale = .current) -> [String] {
        everyRow.compactMap { Self.text($0, column, locale: locale) }
    }

    public static let notKnown = "Not known"
    public static let dash = "\u{2014}"

    private static func figure(_ money: Money?, known: Bool, locale: Locale) -> String {
        guard known else { return notKnown }
        return money.map { Figures.amount($0, signed: false, locale: locale) } ?? dash
    }
}

extension IncomeStatement {
    /// Every node the table can draw, income and expenses and everything beneath them.
    public var everyNode: [Node] {
        func all(_ node: Node) -> [Node] { [node] + node.children.flatMap(all) }
        return all(income) + all(expenses)
    }

    /// What a node shows in the column at `index`, or in the total when it is nil.
    public static func text(_ node: Node, at index: Int?, locale: Locale = .current) -> String {
        Figures.amount(index.map { node.cells[$0] } ?? node.total, signed: false, locale: locale)
    }

    /// What a figure by kind shows in the column at `index`, or in the total when it is nil.
    public static func text(_ total: Total, at index: Int?, locale: Locale = .current) -> String {
        Figures.amount(index.map { total.cells[$0] } ?? total.total, signed: false, locale: locale)
    }

    /// Every text the column at `index`, or the total, shows: each node's, net income's, and
    /// every figure by kind's, whichever way tax is counted.
    public func texts(at index: Int?, locale: Locale = .current) -> [String] {
        let net = index.map { self.net.cells[$0] } ?? self.net.total
        return everyNode.map { Self.text($0, at: index, locale: locale) }
            + [Figures.amount(net, signed: false, locale: locale)]
            + totals.map { Self.text($0, at: index, locale: locale) }
    }
}

extension YearByYear {
    /// Every text a column of money shows, over every year.
    public func texts(_ value: (Year) -> Money?, currency: String, locale: Locale = .current) -> [String] {
        years.map { StatementWords.figure(value($0), rate: false, currency: currency, locale: locale) }
    }
}
