import Foundation
import Testing

@testable import HearthKit

@Suite("The accounts, an account, and the journal, as the engine's own tests check them")
struct ScreenFixtureTests {
    @Test("the accounts fixture decodes, every absence its own standing")
    func accounts() throws {
        let reply = try replyDecoder().decode(Reply<Accounts>.self, from: fixture("accounts"))
        guard case .ready(let accounts) = reply.answer else {
            Issue.record("the fixture is a ready answer")
            return
        }
        #expect(accounts.groups.map(\.name) == ["Cash", "Credit", "Investments", "Not seen by hearth", "Blake"])
        #expect(accounts.groups.map(\.yours) == [true, true, true, true, false])
        let checking = try #require(accounts.account("h1LQFxEvalLFwj4a"))
        #expect(checking.standing == .matches)
        #expect(checking.known == money("3250.40"))
        #expect(checking.checked == Day(year: 2026, month: 10, day: 15))
        #expect(checking.institution == "Larkspur Savings Bank")
        let savings = try #require(accounts.account("Ae06jAUHXgyYIk8D"))
        #expect(savings.standing == .notKnown)
        #expect(savings.balance?.value == nil)
        #expect(savings.known == nil)
        #expect(savings.why == "no-balance")
        #expect(savings.attention)
        let card = try #require(accounts.account("eDUVDA9IScyfzS3o"))
        #expect(card.failed?.asserted == money("-640.05"))
        #expect(card.behind)
        let brokerage = try #require(accounts.account("Wm3vQ0brK2xYe7Lp"))
        #expect(brokerage.balance?.isFloor == true)
        #expect(brokerage.priced == Day(year: 2026, month: 10, day: 14))
        let loan = try #require(accounts.account("10omYJBYKWbu7QCF"))
        #expect(loan.standing == .unseen)
        #expect(loan.balance == nil)
        #expect(loan.unseenKind == "Loan")
        let theirs = try #require(accounts.account("pQ8sTt2uVw9xYz0a"))
        #expect(theirs.standing == .withheld)
        #expect(theirs.kind == nil)
        #expect(theirs.transactions)
    }

    @Test("an account's page decodes with its history")
    func account() throws {
        let reply = try replyDecoder().decode(Reply<AccountPage>.self, from: fixture("account"))
        guard case .ready(let page) = reply.answer else {
            Issue.record("the fixture is a ready answer")
            return
        }
        #expect(page.account.name == "Everyday checking")
        #expect(page.history == [AccountPage.Span(origin: "plaid", from: Day(year: 2026, month: 9, day: 25), to: Day(year: 2026, month: 10, day: 3))])
        #expect(page.unseen == AccountPage.Days(from: Day(year: 2026, month: 9, day: 3), to: Day(year: 2026, month: 9, day: 24)))
        #expect(page.heldBack == nil)
        #expect(!page.account.reconciling)
    }

    @Test("an account its imported ledger holds back says how far its data reaches")
    func heldBack() throws {
        let json = """
            {"state":"ready","computed_at":"2026-10-15T12:00:00Z","as_of":"2026-10-15","currency":"USD",
             "recomputing":false,"account":\(AccountWordsTests.json(.unchecked, reconciling: false)),"history":[],
             "unseen":null,"held_back":{"through":"2026-09-01","from":"2027-03-01"}}
            """
        guard case .ready(let page) = try decode(json, as: AccountPage.self) else {
            Issue.record("a ready answer")
            return
        }
        #expect(page.heldBack?.through == Day(year: 2026, month: 9, day: 1))
        #expect(page.heldBack?.from == Day(year: 2027, month: 3, day: 1))
    }

    @Test("the journal decodes: a suggested row, a check in line, a transfer once, a row waiting")
    func journal() throws {
        let reply = try replyDecoder().decode(Reply<Journal>.self, from: fixture("transactions"))
        guard case .ready(let journal) = reply.answer else {
            Issue.record("the fixture is a ready answer")
            return
        }
        #expect(journal.total == 412)
        #expect(journal.counts.count(.unreviewed) == 4)
        #expect(journal.counts.balanceChecks == 3)
        #expect(journal.from == Day(year: 2026, month: 1, day: 1))
        let entries = journal.entries
        #expect(entries.map(\.kind) == [.transaction, .balance, .transfer, .transaction, .transfer])
        #expect(entries[0].suggested)
        #expect(entries[0].reason == "Books twice before")
        #expect(entries[1].reason == nil)
        #expect(entries[3].reason == "New merchant")
        #expect(entries[0].category == CategoryRef(name: "Expenses:Shopping:Books", label: "Books"))
        #expect(entries[0].amount == money("-41.90"))
        #expect(entries[0].sides.count == 2)
        #expect(entries[1].checked == .bank)
        #expect(entries[2].isOwnTransfer)
        #expect(entries[2].accounts.map(\.name) == ["Everyday checking", "Rewards card"])
        #expect(entries[3].waiting == .category)
        #expect(entries[3].sides[1].waiting)
        #expect(!entries[4].isOwnTransfer)
        #expect(entries[4].sides[1].elsewhere)
    }

    @Test("a standing the app was never taught is an error, not a guess")
    func unknownStanding() {
        #expect(throws: DecodingError.self) {
            try JSONDecoder().decode(Account.Standing.self, from: Data(#""fine""#.utf8))
        }
    }
}

@Suite("Balances to the cent, and amounts")
struct CentsTests {
    @Test("a balance keeps its cents, with a true minus", arguments: [
        ("3250.40", "$3,250.40"), ("-612.15", "−$612.15"), ("18400.0", "$18,400.00"),
        ("0", "$0.00"), ("-0.004", "$0.00"), ("1234567.891", "$1,234,567.89"), ("0.125", "$0.12"),
    ])
    func cents(text: String, written: String) {
        #expect(Figures.cents(money(text), currency: "USD", locale: unitedStates) == written)
    }

    @Test("an amount is signed, without its currency", arguments: [
        ("-84.12", "−84.12"), ("5350", "+5,350.00"), ("0", "0.00"), ("-0.001", "0.00"),
    ])
    func amounts(text: String, written: String) {
        #expect(Figures.amount(money(text), locale: unitedStates) == written)
    }

    @Test("what is neither in nor out has no plus, and keeps a true minus")
    func unsigned() {
        #expect(Figures.amount(money("300"), signed: false, locale: unitedStates) == "300.00")
        #expect(Figures.amount(money("-612.15"), signed: false, locale: unitedStates) == "−612.15")
    }

    @Test("days and spans are said as a person says them")
    func days() {
        let asOf = Day(year: 2026, month: 10, day: 15)
        #expect(Figures.heading(Day(year: 2026, month: 10, day: 3), asOf: asOf, locale: unitedStates) == "Saturday, Oct 3")
        #expect(Figures.heading(Day(year: 2025, month: 12, day: 31), asOf: asOf, locale: unitedStates) == "Wednesday, Dec 31, 2025")
        #expect(Figures.span(from: Day(year: 2026, month: 1, day: 1), to: asOf, locale: unitedStates) == "Jan 1 to Oct 15, 2026")
        #expect(Figures.span(from: Day(year: 2025, month: 12, day: 3), to: asOf, locale: unitedStates) == "Dec 3, 2025 to Oct 15, 2026")
    }
}

@Suite("What an account's balance rests on, in words")
struct AccountWordsTests {
    static func json(_ standing: Account.Standing, why: String? = nil, reconciling: Bool) -> String {
        """
        {"id":"a","name":"Savings","owners":["Avery"],"yours":true,"kind":"Cash",
         "balance":{"value":"1.00","is_known":true,"is_floor":false,"unpriced":[]},
         "standing":"\(standing.rawValue)","why":\(why.map { "\"\($0)\"" } ?? "null"),"checked":null,
         "failed":null,"updated":null,"behind":false,"priced":null,"transactions":true,
         "institution":null,"mask":null,"attention":false,"unseen_kind":null,"reconciling":\(reconciling)}
        """
    }

    private func account(_ standing: Account.Standing, why: String? = nil, reconciling: Bool = false) throws -> Account {
        let decoder = replyDecoder()
        return try decoder.decode(Account.self, from: Data(Self.json(standing, why: why, reconciling: reconciling).utf8))
    }

    @Test("every standing says something of its own")
    func distinct() throws {
        let asOf = Day(year: 2026, month: 10, day: 15)
        let said = try Account.Standing.allCases.map {
            try account($0).status(asOf: asOf, currency: "USD", locale: unitedStates)
        }
        #expect(Set(said).count == Account.Standing.allCases.count)
        #expect(said.allSatisfy { !$0.isEmpty })
    }

    @Test("why a balance is not known is said to whoever is told it")
    func reason() throws {
        #expect(try account(.notKnown, why: "gap").reason != nil)
        #expect(try account(.notKnown).reason == nil)
        #expect(try account(.matches, why: "gap").reason == nil)
    }

    @Test("a balance that meets its bank only by a difference nobody confirmed says so")
    func reconciling() throws {
        let asOf = Day(year: 2026, month: 10, day: 15)
        let differing = try account(.unchecked, reconciling: true)
        let plain = try account(.unchecked)
        #expect(differing.status(asOf: asOf, currency: "USD") == "Difference with its bank to confirm")
        #expect(differing.reason?.contains("Confirm it in Review") == true)
        #expect(plain.status(asOf: asOf, currency: "USD") == "Not checked against a bank")
        #expect(plain.reason == nil)
    }
}

@Suite("Narrowing the journal")
struct JournalFilterTests {
    @Test("this year so far asks for no days, and the engine settles them")
    func yearToDate() {
        let given = JournalFilter().given(offset: 0, limit: 100)
        #expect(given == ["offset": .number(0), "limit": .number(100)])
    }

    @Test("a period asks for its first and last day", arguments: [
        (JournalFilter.Period.year(2025), "2025-01-01", "2025-12-31"),
        (.month(year: 2024, month: 2), "2024-02-01", "2024-02-29"),
        (.month(year: 2026, month: 9), "2026-09-01", "2026-09-30"),
        (.range(from: Day(year: 2026, month: 10, day: 9), to: Day(year: 2026, month: 9, day: 3)), "2026-09-03", "2026-10-09"),
    ])
    func period(period: JournalFilter.Period, from: String, to: String) {
        let given = JournalFilter(period: period).given(offset: 0, limit: 100)
        #expect(given["from"] == .text(from))
        #expect(given["to"] == .text(to))
    }

    @Test("every filter is asked for by the name the engine reads it by")
    func everything() {
        let filter = JournalFilter(
            account: AccountRef(id: "acc", name: "Savings"),
            category: CategoryRef(name: "Expenses:Food", label: "Food"), text: "  market ", show: .balanceChecks)
        let given = filter.given(offset: 200, limit: 50)
        #expect(given == [
            "offset": .number(200), "limit": .number(50), "account": .text("acc"),
            "category": .text("Expenses:Food"), "text": .text("market"), "show": .text("balance_checks"),
        ])
    }

    @Test("each filter is a token, and removing one leaves the rest")
    func tokens() {
        let account = AccountRef(id: "acc", name: "Savings")
        let category = CategoryRef(name: "Expenses:Food", label: "Food")
        let filter = JournalFilter(period: .year(2025), account: account, category: category, text: "market")
        #expect(filter.tokens == [.period(.year(2025)), .account(account), .category(category), .text("market")])

        let without = filter.removing(.account(account)).removing(.period(.year(2025)))
        #expect(without.tokens == [.category(category), .text("market")])
        #expect(without.period == .yearToDate)
        #expect(JournalFilter(text: "   ").tokens == [])
    }

    @Test("periods are said as a person says them")
    func labels() {
        let asOf = Day(year: 2026, month: 10, day: 15)
        #expect(JournalFilter.Period.yearToDate.label(asOf: asOf) == "2026 so far")
        #expect(JournalFilter.Period.year(2025).label(asOf: asOf) == "2025")
        #expect(JournalFilter.Period.month(year: 2026, month: 9).label(asOf: asOf, locale: unitedStates) == "Sep 2026")
    }
}

/// A journal answer with `count` entries from `offset` of `total`, all on one day but the
/// first, computed at `minute` past noon. Its entries carry no `reason`, as an engine from before
/// reasons sends them, which the app reads as none.
private func journal(offset: Int, count: Int, total: Int, minute: Int = 0, payee: String = "Row") -> String {
    let entries = (0..<count).map { i in
        let day = offset + i == 0 ? "2026-10-15" : "2026-10-14"
        return """
            {"position":\(offset + i),"kind":"transaction","date":"\(day)","payee":"\(payee) \(offset + i)","narration":"",
             "accounts":[{"id":"a","name":"Savings"}],"category":null,"unreviewed":false,"suggested":false,
             "waiting":null,"checked":null,"amount":"-1.00","sides":[]}
            """
    }
    return """
        {"state":"ready","computed_at":"2026-10-15T12:\(String(format: "%02d", minute)):00Z","as_of":"2026-10-15",
         "currency":"USD","recomputing":false,"from":"2026-01-01","to":"2026-10-15","total":\(total),"offset":\(offset),
         "counts":{"everything":\(total),"unreviewed":0,"transfers":0,"balance_checks":0},
         "entries":[\(entries.joined(separator: ","))]}
        """
}

@Suite("The journal on screen, a page at a time")
@MainActor
struct JournalModelTests {
    @Test("the first page comes, then the next as it is scrolled to, and no further")
    func pages() async {
        let engine = Scripted([journal(offset: 0, count: 2, total: 3), journal(offset: 2, count: 1, total: 3)])
        let model = JournalModel(engine: engine, page: 2)

        await model.reload()
        #expect(model.state == .ready)
        #expect(model.entries.count == 2)
        #expect(model.hasMore)
        #expect(model.days.map(\.entries.count) == [1, 1])

        await model.loadMore()
        #expect(model.entries.map(\.position) == [0, 1, 2])
        #expect(!model.hasMore)
        await model.loadMore()

        #expect(await engine.given.map { $0["offset"] } == [.number(0), .number(2)])
        #expect(await engine.given.allSatisfy { $0["limit"] == .number(2) })
    }

    @Test("the first entries are laid out a day at a time within so many lines, headings counted")
    func daysFitting() async {
        let model = JournalModel(engine: Scripted([journal(offset: 0, count: 4, total: 4)]))
        await model.reload()

        #expect(model.firstDays(fitting: .max).map(\.entries.count) == [1, 3])
        #expect(model.firstDays(fitting: 4).map(\.entries.count) == [1, 1])
        #expect(model.firstDays(fitting: 3).map(\.entries.count) == [1])
        #expect(model.firstDays(fitting: 1).isEmpty)
    }

    @Test("a change of filter asks again from the first page")
    func filters() async {
        let engine = Scripted([journal(offset: 0, count: 2, total: 2), journal(offset: 0, count: 1, total: 1, payee: "Found")])
        let model = JournalModel(engine: engine)
        await model.reload()

        await model.apply(JournalFilter(text: "found"))

        #expect(model.entries.map(\.payee) == ["Found 0"])
        #expect(model.total == 1)
        #expect(model.tokens == [.text("found")])
        let last = await engine.given.last
        #expect(last?["text"] == .text("found"))
        #expect(last?["offset"] == .number(0))
    }

    @Test("removing a token asks without it")
    func removes() async {
        let engine = Scripted([journal(offset: 0, count: 1, total: 1), journal(offset: 0, count: 2, total: 2)])
        let category = CategoryRef(name: "Expenses:Food", label: "Food")
        let model = JournalModel(engine: engine, filter: JournalFilter(category: category))
        await model.reload()

        await model.remove(.category(category))

        #expect(model.filter.category == nil)
        #expect(await engine.given.last?["category"] == nil)
        #expect(model.entries.count == 2)
    }

    @Test("an account's own journal keeps to its account, which is no token to remove")
    func pinned() async {
        let account = AccountRef(id: "acc", name: "Savings")
        let engine = Scripted([journal(offset: 0, count: 1, total: 1), journal(offset: 0, count: 1, total: 1)])
        let model = JournalModel(engine: engine, pinned: account)
        await model.reload()

        await model.apply(JournalFilter(show: .unreviewed))

        #expect(model.filter.account == account)
        #expect(model.tokens == [])
        #expect(await engine.given.allSatisfy { $0["account"] == .text("acc") })
    }

    @Test("a page from newer books starts the journal again from its first page")
    func newerBooks() async {
        let engine = Scripted([
            journal(offset: 0, count: 2, total: 4), journal(offset: 2, count: 2, total: 5, minute: 5),
            journal(offset: 0, count: 2, total: 5, minute: 5),
        ])
        let model = JournalModel(engine: engine, page: 2)
        await model.reload()

        await model.loadMore()

        #expect(model.entries.map(\.position) == [0, 1])
        #expect(model.total == 5)
        #expect(await engine.given.map { $0["offset"] } == [.number(0), .number(2), .number(0)])
    }

    @Test("watching notices newer books and reloads, and is otherwise one small ask")
    func refresh() async {
        let engine = Scripted([
            journal(offset: 0, count: 2, total: 2), journal(offset: 0, count: 1, total: 2),
            journal(offset: 0, count: 1, total: 3, minute: 9), journal(offset: 0, count: 3, total: 3, minute: 9),
        ])
        let model = JournalModel(engine: engine)
        await model.reload()

        #expect(await model.refresh() == JournalModel.whileReady)
        #expect(model.entries.count == 2)
        await model.refresh()

        #expect(model.entries.count == 3)
        #expect(await engine.given.map { $0["limit"] } == [.number(100), .number(1), .number(1), .number(100)])
    }

    @Test("a failed recompute is shown, and asked about again until the books are back")
    func failed() async {
        let engine = Scripted([#"{"state":"failed","summary":"The ledger could not be read."}"#, journal(offset: 0, count: 1, total: 1)])
        let waits = Pauses()
        let model = JournalModel(engine: engine, pause: { await waits.note($0) })

        await model.watch()

        #expect(await waits.all.first == JournalModel.whileReady)
        #expect(model.state == .stopped)
        #expect(await engine.asked.count == 3)
    }

    @Test("an account the viewer may not see into is missing, and shows nothing")
    func missing() async {
        let model = JournalModel(engine: Scripted([#"{"state":"missing"}"#]))

        await model.reload()

        #expect(model.state == .missing)
        #expect(model.entries.isEmpty)
    }

    @Test("an engine that signs the member out clears the journal")
    func signedOut() async {
        var ended = 0
        let engine = Scripted([journal(offset: 0, count: 2, total: 4), #"{"state":"signed_out"}"#])
        let model = JournalModel(engine: engine, page: 2, signedOut: { ended += 1 })
        await model.reload()

        await model.loadMore()

        #expect(model.state == .signedOut)
        #expect(model.entries.isEmpty)
        #expect(ended == 1)
    }

    @Test("a page asked under an earlier filter is let go")
    func stale() async {
        let engine = Gated()
        let model = JournalModel(engine: engine)
        let first = Task { await model.reload() }
        await engine.waitForAsk()
        let second = Task { await model.apply(JournalFilter(text: "new")) }
        await engine.waitForAsk(count: 2)

        await engine.answer(journal(offset: 0, count: 1, total: 1, payee: "New"), at: 1)
        await second.value
        await engine.answer(journal(offset: 0, count: 2, total: 2, payee: "Old"), at: 0)
        _ = await first.value

        #expect(model.entries.map(\.payee) == ["New 0"])
    }
}

private actor Pauses {
    private(set) var all: [Duration] = []

    func note(_ duration: Duration) {
        all.append(duration)
    }
}

/// An engine whose answers are handed over by the test, one ask at a time, so two asks can be
/// answered in the order a test chooses.
private actor Gated: Asking {
    private var waiting: [CheckedContinuation<String, Never>?] = []

    func ask<Content: Decodable & Sendable>(
        _ screen: String, with given: Given, as content: Content.Type
    ) async throws -> Answer<Content> {
        let json = await withCheckedContinuation { continuation in
            waiting.append(continuation)
        }
        return try decode(json)
    }

    func waitForAsk(count: Int = 1) async {
        while waiting.count < count {
            await Task.yield()
        }
    }

    func answer(_ json: String, at index: Int) {
        waiting[index]?.resume(returning: json)
        waiting[index] = nil
    }
}
