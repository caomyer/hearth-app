import Foundation
import Testing

@testable import HearthKit

@Suite("Statements, as the engine's own tests check them")
struct StatementsTests {
    private func ready<Content: Decodable & Sendable>(_ name: String, as content: Content.Type) throws -> Content {
        let reply = try replyDecoder().decode(Reply<Content>.self, from: fixture(name))
        guard case .ready(let found) = reply.answer else {
            throw Failure.notReady
        }
        return found
    }

    private enum Failure: Error { case notReady }

    @Test("the summary decodes with whose accounts it reads and why net worth changed")
    func summary() throws {
        let summary = try ready("statement_summary", as: StatementSummary.self)
        #expect(summary.scope.parts == ["Avery", "Blake", "Joint accounts"])
        #expect(summary.scope.without == ["Casey"])
        #expect(summary.period == StatementPeriod(from: Day(year: 2026, month: 8, day: 1), to: Day(year: 2026, month: 10, day: 15)))
        #expect(summary.netWorth.now == money("158206.46"))
        #expect(summary.netWorth.before == nil)
        #expect(summary.spending.now == money("5701.81"))
        #expect(summary.spending.categories.contains("Expenses:Bonsai"))
        #expect(summary.lines.first?.key == "pay")
        let why = try #require(summary.why)
        #expect(why.end == money("158206.46"))
        #expect(why.difference == money("0"))
        #expect(why.others.map(\.key).contains("opening"))
        #expect(summary.savingsRate.isRate)
    }

    @Test("the income statement decodes as a tree, a cell per column")
    func incomeStatement() throws {
        let statement = try ready("income_statement", as: IncomeStatement.self)
        #expect(statement.columns.count == 3)
        #expect(statement.columns.last?.partial == true)
        #expect(statement.expenses.total == money("5701.81"))
        #expect(statement.expenses.children.first?.name == "Expenses:Bonsai")
        #expect(statement.expenses.cells.count == statement.columns.count)
        #expect(statement.net.total == money("2238.67"))
        // A branch adds up the rows of every category beneath it, and no others.
        #expect(statement.expenses.categories.count == statement.expenses.children.count)
        #expect(statement.expenses.outline != nil)
    }

    @Test("the balance sheet decodes its groups and accounts, start beside end")
    func balanceSheet() throws {
        let sheet = try ready("balance_sheet", as: BalanceSheet.self)
        #expect(sheet.assets.children.map(\.label) == ["Cash", "Investments"])
        #expect(sheet.netWorth.end?.value == money("158206.46"))
        #expect(sheet.netWorth.change == money("158206.46"))
        let shares = try #require(sheet.assets.children.last?.children.first)
        #expect(shares.account != nil)
        #expect(shares.invested)
        #expect(shares.since == Day(year: 2026, month: 8, day: 1))
    }

    @Test("year by year decodes, a year with too little history marked sparse")
    func years() throws {
        let years = try ready("year_by_year", as: YearByYear.self)
        let year = try #require(years.years.first)
        #expect(year.year == 2026)
        #expect(year.partial && year.sparse)
        #expect(year.savingsRate == nil)
    }

    @Test("a tone the app does not know is told without colour")
    func unknownTone() throws {
        let line = try replyDecoder().decode(
            StatementLine.self,
            from: Data(#"{"key":"pay","label":"Pay","now":"1","before":"1","change":"0","share":"0","tone":"glorious","categories":[],"total":false,"depth":0}"#.utf8))
        #expect(line.tone == .quiet)
    }

    @Test("a change is said with its arrow, its word, its amount and its share")
    func change() throws {
        let line = try replyDecoder().decode(
            StatementLine.self,
            from: Data(#"{"key":"spending","label":"Spending","now":"67700","before":"63530","change":"4170","share":"0.0656","tone":"quiet","categories":[],"total":true,"depth":0}"#.utf8))
        #expect(StatementWords.change(line, currency: "USD", locale: unitedStates) == "▲ Up $4,170, 7%")
        let rate = try replyDecoder().decode(
            StatementLine.self,
            from: Data(#"{"key":"savings_rate","label":"Savings rate","now":"0.44","before":"0.47","change":"-0.03","share":null,"tone":"quiet","categories":[],"total":false,"depth":0}"#.utf8))
        #expect(StatementWords.change(rate, currency: "USD", locale: unitedStates) == "▼ Down 3 points")
        #expect(StatementWords.rate(money("0.4712"), locale: unitedStates) == "47%")
        #expect(StatementWords.figure(nil, rate: false, currency: "USD") == "\u{2014}")
        #expect(StatementWords.meaning(.unfavourable) == "worse for the household")
    }

    @Test("a column is headed by its month or quarter, never as a day")
    func columnHeadings() throws {
        let statement = try ready("income_statement", as: IncomeStatement.self)
        let august = try #require(statement.columns.first)
        #expect(StatementWords.column(august, by: "month", withYear: false, locale: unitedStates) == "Aug")
        #expect(StatementWords.column(august, by: "month", withYear: true, locale: unitedStates) == "Aug 2026")
        #expect(StatementWords.column(august, by: "quarter", withYear: false, locale: unitedStates) == "Q3")
        #expect(StatementWords.column(august, by: "year", withYear: false, locale: unitedStates) == "2026")
    }

    @Test("whose accounts a statement reads, and what it is without, in a sentence")
    func scope() {
        let scope = StatementScope(parts: ["Avery", "Joint accounts"], without: ["Casey"], leavesOut: "1 of the joint accounts hearth does not see", awaiting: 0)
        #expect(StatementWords.scope(scope) == "Avery and the joint accounts, without Casey and 1 of the joint accounts hearth does not see")
        let alone = StatementScope(parts: ["Casey"], without: [], leavesOut: "1 account hearth does not see", awaiting: 0)
        #expect(StatementWords.scope(alone) == "Casey, without 1 account hearth does not see")
    }

    @Test("rows waiting are said with what they move")
    func unreviewed() {
        let found = StatementUnreviewed(
            rows: 2, income: money("0"), expenses: money("1120"), unplaced: 2, unplacedOut: money("79"), unplacedIn: money("0"))
        #expect(StatementWords.unreviewed(found, currency: "USD", locale: unitedStates) == [
            "2 transactions not reviewed yet, $1,120 of spending.",
            "2 not placed in any category yet, $79 out, counted in no total.",
        ])
    }

    @Test("every tab is asked for its days and how it values them, the defaults left to the engine")
    func given() {
        #expect(StatementsModel.given(.yearToDate, valuation: .market) == [:])
        #expect(StatementsModel.given(.year(2025), valuation: .cost) == [
            "from": .text("2025-01-01"), "to": .text("2025-12-31"), "valuation": .text("cost"),
        ])
        #expect(StatementsModel.given(.yearToDate, columns: .quarter) == ["columns": .text("quarter")])
    }

    @Test("a figure opens onto its rows: the statements' own, of exactly its categories")
    func rows() {
        let filter = JournalFilter(
            period: .range(from: Day(year: 2026, month: 1, day: 1), to: Day(year: 2026, month: 3, day: 31)),
            statement: .init(label: "Dining", categories: ["Expenses:Dining"]))
        let given = filter.given(offset: 0, limit: 100)
        #expect(given["within"] == .text("statements"))
        #expect(given["categories"] == .list(["Expenses:Dining"]))
        #expect(filter.tokens.contains(.statement(.init(label: "Dining", categories: ["Expenses:Dining"]))))
        #expect(filter.removing(.statement(.init(label: "Dining", categories: []))).statement == nil)
    }

    @Test("narrowed, every tab asks again for what was chosen")
    @MainActor
    func narrowed() async throws {
        let whole = try JSONSerialization.jsonObject(with: fixture("year_by_year")) as? [String: Any]
        let answer = try JSONSerialization.data(withJSONObject: try #require(whole?["answer"]))
        let engine = Scripted([String(decoding: answer, as: UTF8.self)])
        let model = StatementsModel(engine: engine)
        await model.years.refresh()
        #expect(model.years.content?.years.count == 1)
        // The year in progress is the only one, so it is the only period to choose.
        #expect(model.periods == [.yearToDate])
        await model.choose(.cost)
        #expect(await engine.asked == ["year_by_year", "statement_summary", "balance_sheet", "year_by_year"])
        // The income statement values nothing, so it is not asked again.
        #expect(await engine.given.last == ["valuation": .text("cost")])
        #expect(model.valuation == .cost)
    }
}
