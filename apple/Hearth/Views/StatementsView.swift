import AppKit
import HearthKit
import SwiftUI

/// Statements: the books read the way a business reads its own, over the parts of the
/// household the viewer may see in full. Four tabs: this period against the same days a year
/// before, the income statement, the balance sheet, and every year the books hold. Every
/// figure that adds up rows opens the journal onto exactly those rows; every account opens its
/// page. The period and how holdings are valued are chosen here, for every tab at once.
struct StatementsView: View {
    let model: StatementsModel
    @State private var openedStart = false
    /// Whether tax is counted as spending, as the person chose; off unless they turn it on.
    @AppStorage(TaxInSpending.key) private var withTax = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            StatementsHead(model: model)
                .padding(.horizontal, 28)
                .padding(.top, 22)
                .padding(.bottom, 14)
            Divider()
            tab
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        // Year by year says which whole years there are to choose, on every tab.
        .task { await model.years.watch() }
        .task(id: model.tab) { await watch(model.tab) }
        .onAppear {
            guard !openedStart else { return }
            openedStart = true
            if let tab = StartScreen.statementsTab { model.tab = tab }
        }
    }

    private func watch(_ tab: StatementsModel.Tab) async {
        switch tab {
        case .summary: await model.summary.watch()
        case .incomeStatement: await model.incomeStatement.watch()
        case .balanceSheet: await model.balanceSheet.watch()
        case .years: break
        }
    }

    @ViewBuilder
    private var tab: some View {
        switch model.tab {
        case .summary:
            Answered(model: model.summary, loading: "statements.loading") { SummaryTab(summary: $0, withTax: withTax) }
        case .incomeStatement:
            Answered(model: model.incomeStatement, loading: "statements.loading") { IncomeTab(statement: $0, withTax: withTax) }
        case .balanceSheet:
            Answered(model: model.balanceSheet, loading: "statements.loading") { SheetTab(sheet: $0) }
        case .years:
            Answered(model: model.years, loading: "statements.loading") { YearsTab(years: $0, withTax: withTax) }
        }
    }
}

/// Where the app keeps whether tax is counted as spending: one choice, remembered across
/// launches, for Statements and the Overview alike. The engine never hears it; it sends every
/// figure that changes with it both ways.
enum TaxInSpending {
    static let key = "spending.includesTax"
}

/// The page's title, its tabs, and what every tab is asked for.
private struct StatementsHead: View {
    let model: StatementsModel
    @AppStorage(TaxInSpending.key) private var withTax = false

    /// The engine's own day, never the Mac's: "2026 so far" is the books' year.
    private var asOf: Day? { model.asOf }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // The title beside what every tab is asked for, and the tabs under them, so the
            // head is never wider than the narrowest window.
            HStack(alignment: .center, spacing: 12) {
                Text("Statements")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                Spacer(minLength: 12)
                choices
            }
            Picker("Statement", selection: tab) {
                ForEach(StatementsModel.Tab.allCases) { tab in
                    Text(tab.title).tag(tab)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .accessibilityIdentifier("statements.tabs")
        }
    }

    @ViewBuilder
    private var choices: some View {
        if model.tab != .years, let asOf {
            Picker("Period", selection: period) {
                ForEach(model.periods, id: \.self) { period in
                    Text(period.label(asOf: asOf)).tag(period)
                }
            }
            .labelsHidden()
            .fixedSize()
            .accessibilityIdentifier("statements.period")
        }
        if model.tab == .incomeStatement {
            Picker("Columns", selection: columns) {
                Text("Month").tag(Columns.month)
                Text("Quarter").tag(Columns.quarter)
                Text("Year").tag(Columns.year)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .accessibilityIdentifier("statements.columns")
        } else {
            Picker("Value holdings", selection: valuation) {
                Text("At market value").tag(Valuation.market)
                Text("At cost").tag(Valuation.cost)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .accessibilityIdentifier("statements.valuation")
        }
        // Only where the books hold tax, and on the tabs whose figures it changes.
        if model.taxed, model.tab != .balanceSheet {
            Toggle("Include tax in spending", isOn: $withTax)
                .toggleStyle(.checkbox)
                .fixedSize()
                .help("Count tax as spending in every figure here and on the Overview")
                .accessibilityIdentifier("statements.withTax")
        }
    }

    private var tab: Binding<StatementsModel.Tab> {
        Binding(get: { model.tab }, set: { model.tab = $0 })
    }

    private var period: Binding<JournalFilter.Period> {
        Binding(get: { model.period }, set: { chosen in Task { await model.choose(chosen) } })
    }

    private var valuation: Binding<Valuation> {
        Binding(get: { model.valuation }, set: { chosen in Task { await model.choose(chosen) } })
    }

    private var columns: Binding<Columns> {
        Binding(get: { model.columns }, set: { chosen in Task { await model.choose(chosen) } })
    }
}

/// A tab's answer, and every state before it has one.
private struct Answered<Content: Screen, Shown: View>: View {
    let model: ScreenModel<Content>
    let loading: String
    @ViewBuilder let shown: (Content) -> Shown

    var body: some View {
        switch model.state {
        case .starting, .loading:
            Waiting(text: "Working out the books", identifier: loading)
        case .signedOut:
            Color.clear
        case .failed(let summary):
            Unavailable(title: "The books could not be worked out", detail: summary)
        case .missing:
            Unavailable(title: "Nothing to show", detail: "There is no statement for these days.")
        case .stopped:
            Unavailable(title: "Hearth's engine stopped", detail: "Quit Hearth and open it again.")
        case .ready(let content):
            shown(content)
        }
    }
}

// MARK: - Summary

private struct SummaryTab: View {
    let summary: StatementSummary
    let withTax: Bool
    @Environment(Navigator.self) private var navigator

    private var currency: String { summary.currency }
    /// Spending, the savings rate and the lines, as the person counts tax: the engine's own.
    private var shown: StatementSummary.Counted { summary.counted(withTax: withTax) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text(StatementWords.against(summary.period, summary.against))
                    .font(.callout)
                    .foregroundStyle(Palette.ink2)
                    .accessibilityIdentifier("statements.span")
                Text(StatementWords.scope(summary.scope))
                    .font(.callout)
                    .foregroundStyle(Palette.ink3)
                    .padding(.top, 2)
                    .accessibilityIdentifier("statements.scope")
                Notes(lines: coverage)
                    .padding(.top, 10)
                headline
                    .padding(.top, 18)
                // Side by side where the window is wide enough, one under the other where not.
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 36) {
                        StatementLines(summary: summary, lines: shown.lines).fixedSize()
                        WhyPanel(summary: summary).fixedSize()
                    }
                    VStack(alignment: .leading, spacing: 28) {
                        StatementLines(summary: summary, lines: shown.lines).fixedSize()
                        WhyPanel(summary: summary).fixedSize()
                    }
                }
                .padding(.top, 26)
                Notes(lines: StatementWords.valuing(summary.valuing) + StatementWords.unreviewed(summary.unreviewed, currency: currency))
                    .padding(.top, 22)
                    .accessibilityIdentifier("statements.notes")
            }
            .padding(.horizontal, 28)
            .padding(.vertical, 22)
            .frame(maxWidth: 1000, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var coverage: [String] {
        var lines: [String] = []
        if !summary.covered {
            lines.append("The books do not hold all of this period: an account that begins in it comes in as an opening balance.")
        }
        if !summary.againstCovered {
            lines.append("The books do not hold all of the same days a year before, so nothing is set against them.")
        }
        if !summary.late.isEmpty {
            let named = summary.late.formatted(.list(type: .and))
            lines.append("What \(named) held at the start is not known: it is known only from later, so net worth's start, and why it changed, are without it.")
        }
        if summary.scope.awaiting > 0 {
            lines.append("A balance is not known yet, so net worth and why it changed are not shown.")
        }
        return lines
    }

    private var headline: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 160), spacing: 12, alignment: .top)], alignment: .leading, spacing: 12) {
            Tile(
                label: summary.isFloor ? "Net worth, at least" : "Net worth",
                figure: StatementWords.figure(summary.netWorth.now, rate: false, currency: currency),
                detail: StatementWords.change(summary.netWorth, currency: currency),
                tone: summary.netWorth.tone, identifier: "statements.netWorth",
                note: summary.netWorth.change == nil ? nil : "since \(Figures.day(summary.period.from))")
            Tile(
                label: shown.savingsRate.label,
                figure: StatementWords.figure(shown.savingsRate.now, rate: true, currency: currency),
                detail: shown.savingsRate.before.map { "\(StatementWords.rate($0)) a year before" },
                tone: .quiet, identifier: "statements.savingsRate")
            Tile(
                label: shown.spending.label,
                figure: StatementWords.figure(shown.spending.now, rate: false, currency: currency),
                detail: StatementWords.change(shown.spending, currency: currency),
                tone: shown.spending.tone, identifier: "statements.spending",
                note: shown.spending.change == nil ? nil : "against the same days a year before",
                open: rows(shown.spending))
            Tile(
                label: (summary.market?.amount ?? 0) < 0 ? "Market took away" : "Market added",
                figure: summary.market.map { Figures.total(Money(abs($0.amount)), currency: currency) } ?? "\u{2013}",
                detail: "Gain not yet realised", tone: .quiet, identifier: "statements.market")
        }
    }

    private func rows(_ line: StatementLine) -> (() -> Void)? {
        guard !line.categories.isEmpty else { return nil }
        return {
            navigator.show(
                JournalFilter.StatementRows(label: line.label, categories: line.categories),
                from: summary.period.from, to: summary.period.to)
        }
    }
}

/// One headline figure: what it is, the figure, and its change with arrow, word and colour.
private struct Tile: View {
    let label: String
    let figure: String
    let detail: String?
    let tone: Tone
    let identifier: String
    /// What the change is over, under it: "since Jan 1".
    var note: String? = nil
    var open: (() -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.callout)
                .foregroundStyle(Palette.ink3)
            if let open {
                Button(action: open) {
                    Text(figure).font(.system(size: 24, weight: .semibold)).monospacedDigit()
                }
                .buttonStyle(.plain)
                .foregroundStyle(Palette.ink)
                .help("Show these transactions")
                .accessibilityIdentifier(identifier)
            } else {
                Text(figure)
                    .font(.system(size: 24, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(Palette.ink)
                    .accessibilityIdentifier(identifier)
            }
            if let detail {
                Text(detail)
                    .font(.footnote.weight(tone == .quiet ? .regular : .semibold))
                    .monospacedDigit()
                    .foregroundStyle(ToneColour.of(tone))
                    .accessibilityLabel([detail, note, StatementWords.meaning(tone)].compactMap(\.self).joined(separator: ", "))
            }
            if let note {
                Text(note)
                    .font(.footnote)
                    .foregroundStyle(Palette.ink3)
                    .accessibilityHidden(true)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, minHeight: 96, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 8).fill(Palette.background))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Palette.line, lineWidth: 1))
    }
}

/// Colour, last: what a change means for the household, never which way it went.
enum ToneColour {
    static func of(_ tone: Tone) -> Color {
        switch tone {
        case .favourable: Palette.favourable
        case .unfavourable: Palette.unfavourable
        case .quiet: Palette.ink2
        }
    }
}

/// The compact income statement, this period beside the same days a year before.
private struct StatementLines: View {
    let summary: StatementSummary
    /// The lines as the person counts tax (`StatementSummary.counted`).
    let lines: [StatementLine]
    @Environment(Navigator.self) private var navigator

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Income statement")
                .font(.headline)
                .foregroundStyle(Palette.ink)
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 7) {
                GridRow {
                    Text("")
                    Text(String(summary.period.to.year)).gridColumnAlignment(.trailing)
                    Text(String(summary.against.to.year)).gridColumnAlignment(.trailing)
                    Text("Change").gridColumnAlignment(.trailing)
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(Palette.ink3)
                Divider().gridCellUnsizedAxes(.horizontal)
                ForEach(lines) { line in
                    GridRow {
                        Text(line.label)
                            .fontWeight(line.total ? .semibold : .regular)
                            .padding(.leading, CGFloat(line.depth) * 14)
                            .foregroundStyle(line.depth > 0 ? Palette.ink2 : Palette.ink)
                        figure(line)
                        Text(StatementWords.figure(line.before, rate: line.isRate, currency: summary.currency))
                            .foregroundStyle(Palette.ink2)
                        Text(StatementWords.change(line, currency: summary.currency) ?? "")
                            .fontWeight(line.tone == .quiet ? .regular : .semibold)
                            .foregroundStyle(ToneColour.of(line.tone))
                            .accessibilityLabel(
                                [StatementWords.change(line, currency: summary.currency), StatementWords.meaning(line.tone)]
                                    .compactMap(\.self).joined(separator: ", "))
                    }
                    .monospacedDigit()
                    .accessibilityIdentifier("statements.line.\(line.key)")
                }
            }
            .font(.system(size: 13))
        }
    }

    @ViewBuilder
    private func figure(_ line: StatementLine) -> some View {
        let text = StatementWords.figure(line.now, rate: line.isRate, currency: summary.currency)
        if line.categories.isEmpty {
            Text(text).fontWeight(line.total ? .semibold : .regular)
        } else {
            Button(text) {
                navigator.show(
                    JournalFilter.StatementRows(label: line.label, categories: line.categories),
                    from: summary.period.from, to: summary.period.to)
            }
            .buttonStyle(.plain)
            .fontWeight(line.total ? .semibold : .regular)
            .foregroundStyle(Palette.ink)
            .help("Show these transactions")
        }
    }
}

/// Why net worth changed: from the start through each part to the end, and whether that adds
/// up to the balance sheet, any difference shown, never hidden.
private struct WhyPanel: View {
    let summary: StatementSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Why net worth changed")
                .font(.headline)
                .foregroundStyle(Palette.ink)
            if let why = summary.why {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 7) {
                    step("Start, \(Figures.day(summary.period.from, asOf: summary.asOf))", why.start, base: true)
                    Divider().gridCellUnsizedAxes(.horizontal)
                    step("Saved from work", why.saved)
                    step("Investment income", why.investmentIncome)
                    if let market = why.market {
                        step("Market movement", market)
                    }
                    step("Other", why.other)
                    ForEach(why.others.filter { worthALine($0.amount) }) { part in
                        GridRow {
                            Text(part.label).foregroundStyle(Palette.ink2).padding(.leading, 14)
                            Text(signed(part.amount)).foregroundStyle(Palette.ink2)
                        }
                        .font(.system(size: 12))
                    }
                    Divider().gridCellUnsizedAxes(.horizontal)
                    step("End, \(Figures.day(summary.period.to, asOf: summary.asOf))", why.end, base: true)
                }
                .font(.system(size: 13))
                .monospacedDigit()
                adds(why)
            } else {
                Text("Not known yet: a balance behind it has not arrived.")
                    .foregroundStyle(Palette.ink2)
            }
        }
    }

    private func step(_ label: String, _ amount: Money, base: Bool = false) -> some View {
        GridRow {
            Text(label).fontWeight(base ? .semibold : .regular)
            Text(base ? Figures.total(amount, currency: summary.currency) : signed(amount))
                .fontWeight(base ? .semibold : .regular)
                .gridColumnAlignment(.trailing)
        }
        .foregroundStyle(Palette.ink)
    }

    @ViewBuilder
    private func adds(_ why: StatementSummary.Why) -> some View {
        if worthALine(why.difference) {
            Text("Difference \(Figures.cents(why.difference, currency: summary.currency)): an amount with no price to convert it.")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(Palette.unfavourable)
                .accessibilityIdentifier("statements.why.difference")
        } else {
            Label("Matches the balance sheet", systemImage: "checkmark.circle")
                .font(.footnote)
                .foregroundStyle(Palette.favourable)
                .accessibilityIdentifier("statements.why.difference")
        }
    }

    /// A part of the change in whole units with its sign: "+60,700", "−777".
    private func signed(_ amount: Money) -> String {
        let text = Figures.total(Money(abs(amount.amount)), currency: summary.currency)
        if text.allSatisfy({ !("1"..."9").contains($0) }) { return text }
        return (amount.amount < 0 ? "\u{2212}" : "+") + text
    }

    /// Whether an amount is worth a line: a cent or more either way.
    private func worthALine(_ amount: Money) -> Bool {
        abs(amount.amount) >= Decimal(string: "0.005")!
    }
}

/// Lines of plain words under a figure.
private struct Notes: View {
    let lines: [String]
    /// Whether each line wraps to its full height, as it may on a page that scrolls; under a
    /// table, which takes every point the window has, each is one line, its whole in its help.
    var wraps = true

    var body: some View {
        if !lines.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                ForEach(lines, id: \.self) { line in
                    if wraps {
                        Text(line)
                            .font(.footnote)
                            .foregroundStyle(Palette.ink3)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        Text(line)
                            .font(.footnote)
                            .foregroundStyle(Palette.ink3)
                            .lineLimit(1)
                            .truncationMode(.tail)
                            .help(line)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

// MARK: - Income statement

/// A tree shown as rows, each with its depth, under the branches left open.
private struct TreeRow<Node: Identifiable>: Identifiable {
    let node: Node
    let depth: Int
    let branch: Bool
    let total: Bool

    var id: Node.ID { node.id }
}

private struct IncomeTab: View {
    let statement: IncomeStatement
    let withTax: Bool
    @Environment(Navigator.self) private var navigator
    @State private var closed: Set<String> = []

    private struct ColumnRef: Identifiable {
        let id: Int
        let column: IncomeStatement.Column
    }

    private var refs: [ColumnRef] {
        statement.columns.enumerated().map { ColumnRef(id: $0.offset, column: $0.element) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(caption)
                .font(.callout)
                .foregroundStyle(Palette.ink2)
                .lineLimit(1)
                .truncationMode(.tail)
                .help(caption)
                .padding(.horizontal, 28)
                .padding(.vertical, 10)
            Table(rows) {
                TableColumn("Account") { row in
                    Disclosed(
                        label: row.node.label, depth: row.depth, branch: row.branch, total: row.total,
                        open: !closed.contains(row.node.name)
                    ) { toggle(row.node.name) }
                }
                .width(min: FigureWidth.names)
                TableColumnForEach(refs) { ref in
                    let title = StatementWords.column(ref.column, by: statement.columnsBy, withYear: spansYears)
                    TableColumn(title) { row in
                        cell(row, index: ref.id)
                    }
                    .width(width(ref.id, title))
                    .alignment(.trailing)
                }
                TableColumn(totalTitle) { row in
                    cell(row, index: nil)
                }
                .width(width(nil, totalTitle))
                .alignment(.trailing)
            }
            // Its columns scroll within it; they never widen the window.
            .frame(minWidth: 320, maxWidth: .infinity, minHeight: 0, maxHeight: .infinity)
            .accessibilityIdentifier("statements.income")
            Notes(lines: footer, wraps: false)
                .padding(.horizontal, 28)
                .padding(.vertical, 10)
        }
    }

    private var caption: String {
        var text = Figures.span(from: statement.period.from, to: statement.period.to)
        text += " · " + StatementWords.scope(statement.scope)
        return text
    }

    /// A column as wide as the widest figure it shows, so no figure is cut.
    private func width(_ index: Int?, _ title: String) -> CGFloat {
        FigureWidth.holding(statement.texts(at: index), title: title)
    }

    /// Whether the columns run over more than one year, so each heading needs its year.
    private var spansYears: Bool { statement.period.from.year != statement.period.to.year }

    private var totalTitle: String {
        statement.period.to == statement.asOf ? "\(statement.period.to.year) so far" : "Total"
    }

    /// Income and expenses, each open to the accounts beneath unless closed, then net income.
    private var rows: [TreeRow<IncomeStatement.Node>] {
        var found: [TreeRow<IncomeStatement.Node>] = []
        func add(_ node: IncomeStatement.Node, depth: Int) {
            found.append(TreeRow(node: node, depth: depth, branch: !node.children.isEmpty, total: depth == 0))
            guard !closed.contains(node.name) else { return }
            for child in node.children { add(child, depth: depth + 1) }
        }
        add(statement.income, depth: 0)
        add(statement.expenses, depth: 0)
        let net = IncomeStatement.Node(
            name: "net", label: "Net income", kind: nil, cells: statement.net.cells,
            total: statement.net.total, children: [])
        found.append(TreeRow(node: net, depth: 0, branch: false, total: true))
        // Spending and tax by kind, as the person counts tax, where the books hold any: tax is
        // its own line, under spending when it is counted in it.
        for total in statement.totalsShown(withTax: withTax) {
            let node = IncomeStatement.Node(
                name: Self.byKind + total.key, label: total.label, kind: nil, cells: total.cells,
                total: total.total, children: [])
            let under = withTax && total.key == "tax"
            found.append(TreeRow(node: node, depth: under ? 1 : 0, branch: false, total: !under))
        }
        if statement.unreviewed.rows > 0 {
            // How many rows each column rests on that nobody has reviewed yet.
            let waiting = IncomeStatement.Node(
                name: Self.waiting, label: "Not reviewed yet", kind: nil, cells: [], total: Money(0), children: [])
            found.append(TreeRow(node: waiting, depth: 0, branch: false, total: false))
        }
        return found
    }

    private func toggle(_ name: String) {
        if closed.contains(name) { closed.remove(name) } else { closed.insert(name) }
    }

    private static let waiting = "unreviewed"
    /// What a figure by kind's row is named, before its key: no ledger name starts so.
    private static let byKind = "kind:"

    /// The categories whose rows a row adds up: a figure by kind's, as the engine names them,
    /// or every category in a node.
    private func categories(_ node: IncomeStatement.Node) -> [String] {
        guard node.name.hasPrefix(Self.byKind) else { return node.categories }
        let key = String(node.name.dropFirst(Self.byKind.count))
        return statement.totals.first { $0.key == key }?.categories ?? []
    }

    @ViewBuilder
    private func cell(_ row: TreeRow<IncomeStatement.Node>, index: Int?) -> some View {
        if row.node.name == Self.waiting {
            let found = index.map { statement.columns[$0].unreviewed } ?? statement.unreviewed
            Text(found.rows == 0 ? "" : found.rows.formatted())
                .monospacedDigit()
                .foregroundStyle(Palette.warning)
                .help(StatementWords.unreviewed(found, currency: statement.currency).joined(separator: " "))
        } else {
            figureCell(row, index: index)
        }
    }

    @ViewBuilder
    private func figureCell(_ row: TreeRow<IncomeStatement.Node>, index: Int?) -> some View {
        let text = IncomeStatement.text(row.node, at: index)
        let categories = categories(row.node)
        if row.node.name == "net" || categories.isEmpty {
            Text(text).fontWeight(row.total ? .semibold : .regular).monospacedDigit().lineLimit(1)
        } else {
            let span = index.map { statement.columns[$0] }
            Button(text) {
                navigator.show(
                    JournalFilter.StatementRows(label: row.node.label, categories: categories),
                    from: span?.from ?? statement.period.from, to: span?.to ?? statement.period.to)
            }
            .buttonStyle(.plain)
            .fontWeight(row.total ? .semibold : .regular)
            .monospacedDigit()
            .help("Show these transactions")
        }
    }

    /// What of each column rests on rows not reviewed or placed yet.
    private var footer: [String] {
        StatementWords.unreviewed(statement.unreviewed, currency: statement.currency)
            + [StatementWords.later(statement.later)].compactMap(\.self)
    }
}

/// An account's name in a tree: indented to its depth, with a disclosure to open or close
/// the accounts beneath it.
private struct Disclosed: View {
    let label: String
    let depth: Int
    let branch: Bool
    let total: Bool
    let open: Bool
    let toggle: () -> Void

    var body: some View {
        HStack(spacing: 4) {
            if branch {
                Button(action: toggle) {
                    Image(systemName: open ? "chevron.down" : "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .frame(width: 12)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Palette.ink3)
                .accessibilityLabel(open ? "Close \(label)" : "Open \(label)")
            } else {
                Color.clear.frame(width: 12, height: 1)
            }
            Text(label)
                .fontWeight(total ? .semibold : .regular)
                .lineLimit(1)
        }
        .padding(.leading, CGFloat(depth) * 14)
    }
}

// MARK: - Balance sheet

private struct SheetTab: View {
    let sheet: BalanceSheet
    @Environment(Navigator.self) private var navigator
    @State private var closed: Set<String> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(caption)
                .font(.callout)
                .foregroundStyle(Palette.ink2)
                .lineLimit(1)
                .truncationMode(.tail)
                .help(caption)
                .padding(.horizontal, 28)
                .padding(.vertical, 10)
            Table(rows) {
                TableColumn("Account") { row in
                    if let account = row.node.account {
                        HStack(spacing: 0) {
                            Color.clear.frame(width: 16 + CGFloat(row.depth) * 14, height: 1)
                            Button(account.name) { navigator.open(account) }
                                .buttonStyle(.plain)
                                .foregroundStyle(Palette.accent)
                                .lineLimit(1)
                                .help(row.node.since.map { "In the books from \(Figures.day($0))" } ?? "Open \(account.name)")
                        }
                    } else {
                        Disclosed(
                            label: row.node.label, depth: row.depth, branch: row.branch, total: row.total,
                            open: !closed.contains(row.node.id)
                        ) { toggle(row.node.id) }
                    }
                }
                .width(min: FigureWidth.names)
                TableColumn(startTitle) { row in cell(row, .start) }
                    .width(width(.start, startTitle))
                    .alignment(.trailing)
                TableColumn(endTitle) { row in cell(row, .end) }
                    .width(width(.end, endTitle))
                    .alignment(.trailing)
                TableColumn("Change") { row in cell(row, .change) }
                    .width(width(.change, "Change"))
                    .alignment(.trailing)
                TableColumn("Cost") { row in cell(row, .cost) }
                    .width(width(.cost, "Cost"))
                    .alignment(.trailing)
                TableColumn(Self.gainTitle) { row in cell(row, .gain) }
                    .width(width(.gain, Self.gainTitle))
                    .alignment(.trailing)
            }
            // Its columns scroll within it; they never widen the window.
            .frame(minWidth: 320, maxWidth: .infinity, minHeight: 0, maxHeight: .infinity)
            .accessibilityIdentifier("statements.sheet")
            Notes(lines: StatementWords.valuing(sheet.valuing), wraps: false)
                .padding(.horizontal, 28)
                .padding(.vertical, 10)
        }
    }

    private var caption: String {
        var text = "As of \(Figures.day(sheet.period.to, asOf: sheet.asOf)), against the start of \(Figures.day(sheet.period.from, asOf: sheet.asOf))"
        text += " · " + StatementWords.scope(sheet.scope)
        return text
    }

    private var rows: [TreeRow<BalanceSheet.Row>] {
        var found: [TreeRow<BalanceSheet.Row>] = []
        func add(_ row: BalanceSheet.Row, depth: Int) {
            found.append(TreeRow(node: row, depth: depth, branch: !row.children.isEmpty, total: depth == 0))
            guard !closed.contains(row.id) else { return }
            for child in row.children { add(child, depth: depth + 1) }
        }
        add(sheet.assets, depth: 0)
        add(sheet.liabilities, depth: 0)
        found.append(TreeRow(node: sheet.netWorth, depth: 0, branch: false, total: true))
        return found
    }

    private func toggle(_ id: String) {
        if closed.contains(id) { closed.remove(id) } else { closed.insert(id) }
    }

    private static let gainTitle = "Gain not yet realised"
    private var startTitle: String { Figures.day(sheet.period.from, asOf: sheet.asOf) }
    private var endTitle: String { Figures.day(sheet.period.to, asOf: sheet.asOf) }

    /// A column as wide as the widest text it shows, so no figure is cut.
    private func width(_ column: SheetColumn, _ title: String) -> CGFloat {
        FigureWidth.holding(sheet.texts(column), title: title)
    }

    /// A figure to the cent, as a balance sheet reconciles; "Not known" while a balance in it
    /// has not arrived, and a dash where nothing says what it cost.
    @ViewBuilder
    private func cell(_ row: TreeRow<BalanceSheet.Row>, _ column: SheetColumn) -> some View {
        if let text = BalanceSheet.text(row.node, column) {
            let placeholder = text == BalanceSheet.notKnown || text == BalanceSheet.dash
            Text(text)
                .fontWeight(row.total && !placeholder ? .semibold : .regular)
                .monospacedDigit()
                .lineLimit(1)
                .foregroundStyle(placeholder || column == .change ? Palette.ink3 : Palette.ink)
                .help(help(row, text))
        }
    }

    private func help(_ row: TreeRow<BalanceSheet.Row>, _ text: String) -> String {
        if text == BalanceSheet.dash { return "Nothing says what it cost" }
        if row.node.end?.isFloor == true { return "At least this: a holding in it has no price" }
        return ""
    }
}

// MARK: - Year by year

private struct YearsTab: View {
    let years: YearByYear
    let withTax: Bool

    /// Whether the years hold any tax, so it is a column of its own and the switch applies.
    private var taxed: Bool { years.scope.taxed }
    private var counted: Bool { taxed && withTax }
    private var spendingTitle: String { StatementWords.spending(withTax: counted) }
    private var rateTitle: String { StatementWords.savingsRate(taxed: taxed, withTax: withTax) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(StatementWords.scope(years.scope))
                .font(.callout)
                .foregroundStyle(Palette.ink2)
                .padding(.horizontal, 28)
                .padding(.vertical, 10)
            if years.years.isEmpty {
                Unavailable(title: "No years yet", detail: "A year appears once the books hold any of it.")
            } else {
                Table(years.years.reversed()) {
                    TableColumn("Year") { year in
                        HStack(spacing: 6) {
                            Text(year.partial ? "\(year.year) so far" : String(year.year))
                            if year.sparse {
                                Text("Sparse")
                                    .font(.caption)
                                    .foregroundStyle(Palette.warning)
                                    .help("The books hold too little of this year to set it against another")
                            }
                        }
                    }
                    TableColumn("Net worth") { year in money(year.netWorth) }
                        .width(width(\.netWorth, "Net worth"))
                        .alignment(.trailing)
                    TableColumn("Take-home pay") { year in money(year.pay) }
                        .width(width(\.pay, "Take-home pay"))
                        .alignment(.trailing)
                    if taxed {
                        TableColumn("Tax") { year in money(year.tax) }
                            .width(width(\.tax, "Tax"))
                            .alignment(.trailing)
                    }
                    TableColumn(spendingTitle) { year in money(year.spending(withTax: counted)) }
                        .width(width({ $0.spending(withTax: counted) }, spendingTitle))
                        .alignment(.trailing)
                    TableColumn("Saved from work") { year in money(year.saved) }
                        .width(width(\.saved, "Saved from work"))
                        .alignment(.trailing)
                    TableColumn(rateTitle) { year in
                        Text(StatementWords.figure(year.savingsRate(withTax: counted), rate: true, currency: years.currency)).monospacedDigit()
                    }
                    .alignment(.trailing)
                }
                // A few years, never a page of them: stripes would run on down the empty table.
                .alternatingRowBackgrounds(.disabled)
                .frame(minWidth: 320, maxWidth: .infinity, minHeight: 0, maxHeight: .infinity)
                .accessibilityIdentifier("statements.years")
            }
        }
    }

    private func money(_ value: Money?) -> some View {
        Text(StatementWords.figure(value, rate: false, currency: years.currency)).monospacedDigit().lineLimit(1)
    }

    private func width(_ value: KeyPath<YearByYear.Year, Money?>, _ title: String) -> CGFloat {
        FigureWidth.holding(years.texts({ $0[keyPath: value] }, currency: years.currency), title: title)
    }

    private func width(_ value: KeyPath<YearByYear.Year, Money>, _ title: String) -> CGFloat {
        FigureWidth.holding(years.texts({ $0[keyPath: value] }, currency: years.currency), title: title)
    }

    private func width(_ value: @escaping (YearByYear.Year) -> Money, _ title: String) -> CGFloat {
        FigureWidth.holding(years.texts({ value($0) }, currency: years.currency), title: title)
    }
}

/// How wide a column of figures must be to hold the widest of them in the table's own font,
/// bold as a total is drawn, and its heading: a column is never narrower, so no figure is cut;
/// when the columns are wider than the window, the table scrolls sideways.
enum FigureWidth {
    /// What a cell keeps beside what it draws: its own inset. The table puts its intercell
    /// spacing between columns as well, so the gap between two figures is that spacing and
    /// this, never this twice.
    static let padding: CGFloat = 6
    /// The narrowest the name column goes, readable, before the table scrolls sideways: it
    /// takes all the width the figures leave, and is never squeezed to give them more.
    static let names: CGFloat = 180

    static func holding(_ texts: [String], title: String) -> CGFloat {
        let figures = NSFont.monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .semibold)
        let heading = NSFont.systemFont(ofSize: NSFont.smallSystemFontSize, weight: .medium)
        let widths = texts.map { measure($0, figures) } + [measure(title, heading)]
        return ceil(widths.max() ?? 0) + padding
    }

    private static func measure(_ text: String, _ font: NSFont) -> CGFloat {
        (text as NSString).size(withAttributes: [.font: font]).width
    }
}
