import Foundation
import Testing

@testable import HearthKit

/// A review row, as the engine writes it.
private func row(
    _ id: String, group: String, amount: String = "-10.00", suggested: String? = "Expenses:Kites",
    excludable: Bool = true, says: String? = nil, kind: String = "transaction"
) -> String {
    let suggestion =
        suggested.map { #"{"name":"\#($0)","label":"\#($0.split(separator: ":").last!)"}"# } ?? "null"
    return #"""
        {"id":"\#(id)","kind":"\#(kind)","group":"\#(group)","date":"2026-10-04","payee":"Payee \#(id)","narration":"",
         "account":{"id":"acct-1","name":"Everyday checking"},"amount":"\#(amount)",
         "suggested":\#(suggestion),"reason":"Suggested by a rule","excludable":\#(excludable),
         "says":"\#(says ?? "says-\(id)")"}
        """#
}

/// A queue answer: each group by name, with its rows' ids and whether it is one decision.
private func queue(_ groups: [(name: String, rows: [String], one: Bool)], rows: [String]) -> String {
    let written = groups.map { group in
        let ids = group.rows.map { "\"\($0)\"" }.joined(separator: ",")
        return #"""
            {"name":"\#(group.name)","size":\#(group.rows.count),"total":"-10.00","moved":"10.00",
             "suggested":[],"one_decision":\#(group.one),"least_sure":"Suggested by a rule","rows":[\#(ids)]}
            """#
    }
    let count = groups.reduce(0) { $0 + $1.rows.count }
    return #"""
        {"state":"ready","computed_at":"2026-10-15T12:00:00Z","as_of":"2026-10-15","currency":"USD",
         "recomputing":false,"order":"size","remaining":{"rows":\#(count),"moved":"10.00"},
         "shown":{"rows":\#(count),"moved":"10.00"},"filtered":false,"reviewed":0,"total":\#(count),
         "groups_waiting":\#(groups.count),"oldest":null,"changed":0,"revised":0,"kept":0,
         "elsewhere":{"rows":0,"moved":"0"},"accounts":[],"categories":[],
         "groups":[\#(written.joined(separator: ","))],"rows":[\#(rows.joined(separator: ","))]}
        """#
}

/// An engine for Review: asks answered by screen, from a script, actions from another, and
/// everything it was asked noted.
private actor ScriptedReview: Acting {
    private var answers: [String: [String]]
    private var results: [ActResult]
    private(set) var acted: [(String, Given)] = []
    private(set) var asked: [(String, Given)] = []

    init(_ answers: [String: [String]], results: [ActResult] = []) {
        self.answers = answers
        self.results = results
    }

    func more(_ screen: String, _ answer: String) {
        answers[screen, default: []].append(answer)
    }

    func ask<Content: Decodable & Sendable>(
        _ screen: String, with given: Given, as content: Content.Type
    ) async throws -> Answer<Content> {
        asked.append((screen, given))
        guard var waiting = answers[screen], !waiting.isEmpty else { throw Engine.Failure.stopped }
        // The last answer of a screen stands for every later ask of it.
        let answer = waiting.count > 1 ? waiting.removeFirst() : waiting[0]
        answers[screen] = waiting
        return try decode(answer)
    }

    func act(_ action: String, with given: Given) async throws -> ActResult {
        acted.append((action, given))
        guard !results.isEmpty else { throw Engine.Failure.stopped }
        return results.removeFirst()
    }
}

private let kites = [row("k1", group: "Kites"), row("k2", group: "Kites"), row("k3", group: "Kites")]
private let unsure = row("u1", group: "Unsure", amount: "-23.45", suggested: nil, excludable: false)
private let full = queue(
    [("Kites", ["k1", "k2", "k3"], true), ("Unsure", ["u1"], false)], rows: kites + [unsure])

@Suite("What Review is told")
struct ReviewShapeTests {
    @Test("the queue has the shape the engine writes")
    func queueShape() throws {
        guard case .ready(let found) = try replyDecoder().decode(Reply<ReviewQueue>.self, from: fixture("review")).answer
        else {
            Issue.record("the example is a ready answer")
            return
        }
        #expect(found.remaining.rows == 5)
        #expect(found.remaining.moved == money("271.32"))
        #expect(found.elsewhere.rows == 2)
        #expect(found.kept == 1)
        #expect(found.order == .size)
        #expect(
            found.groups.map(\.name) == [
                "Food and drink: groceries", "Uncategorised", "Where the imported ledger and the bank disagree",
            ])
        #expect(found.groups[0].oneDecision)
        #expect(!found.groups[1].oneDecision)
        #expect(!found.groups[2].oneDecision)
        #expect(found.groups[0].suggested.first?.label == "Groceries")
        #expect(found.rows.count == 5)
        #expect(found.rows.filter(\.isReconciling).map(\.id) == ["d40a7be915"])
        let difference = try #require(found.row("d40a7be915"))
        #expect(!difference.excludable)
        #expect(difference.reason.hasPrefix("Confirm it once you know why they differ"))
        let pos = try #require(found.row("0e92b4d7c1"))
        #expect(pos.payee == nil)
        #expect(pos.title == "POS PURCHASE 3381")
        #expect(found.row("91d2e6f0a8")?.suggested == nil)
        #expect(found.row("7a1c9e04b2")?.excludable == false)
        #expect(found.categories.last?.value == "-")
    }

    @Test("a row's detail has the shape the engine writes")
    func detailShape() throws {
        guard case .ready(let found) = try replyDecoder().decode(Reply<ReviewDetail>.self, from: fixture("review_row")).answer
        else {
            Issue.record("the example is a ready answer")
            return
        }
        #expect(found.decision == .waiting)
        #expect(found.row.narration == "COBBLE ST MKT 0412")
        #expect(found.categories.map(\.label) == ["Groceries", "Home", "Wages"])
        #expect(found.accounts.map(\.name) == ["Harbor savings"])
        #expect(found.earlier.map(\.decision) == [.reviewed, .excluded])
        #expect(found.earlier[1].category == nil)
    }

    @Test("where it went has the shape the engine writes")
    func whereShape() throws {
        guard case .ready(let found) = try replyDecoder().decode(Reply<WherePage>.self, from: fixture("where")).answer
        else {
            Issue.record("the example is a ready answer")
            return
        }
        let group = try #require(found.groups.first)
        #expect(group.question == .went)
        #expect(group.learned?.like == 22)
        #expect(group.mask == "0417")
        #expect(group.outgoing)
        #expect(group.rows.count == 2)
        #expect(group.chosen == group.accounts.first?.id)
    }

    @Test("a decision's reply says what it decided")
    func decidedShape() throws {
        let found = try replyDecoder().decode(ActResult.self, from: fixture("decided"))
        #expect(found == ActResult(outcome: .changed, ruling: 41, rows: 2, changed: 1))
        // A sync's reply has nothing but its outcome.
        let sync = try replyDecoder().decode(ActResult.self, from: Data(#"{"id":2,"outcome":"started"}"#.utf8))
        #expect(sync == ActResult(outcome: .started))
    }

    @Test("the overview says what waits in Review, and a journal entry which review it waits on")
    func waitingShape() throws {
        guard case .ready(let overview) = try replyDecoder().decode(Reply<Overview>.self, from: fixture("overview")).answer,
            case .ready(let journal) = try replyDecoder().decode(Reply<Journal>.self, from: fixture("transactions")).answer
        else {
            Issue.record("the examples are ready answers")
            return
        }
        #expect(overview.review.waiting == 14)
        #expect(overview.review.spending == money("1120.37"))
        #expect(journal.entries.first?.review == "5f0c2a9e1d")
        #expect(journal.entries.dropFirst().allSatisfy { $0.review == nil })
    }

    @Test("every decision is bound to what was shown")
    func binding() throws {
        guard case .ready(let found) = try decode(full, as: ReviewQueue.self) else {
            Issue.record("a ready answer")
            return
        }
        let k1 = try #require(found.row("k1"))
        #expect(Decision.accept(k1).given == ["row": .text("k1"), "shown": .text("Expenses:Kites")])
        let group = Decision.acceptGroup(name: "Kites", rows: found.rows.filter { $0.group == "Kites" })
        #expect(
            group.given == [
                "group": .text("Kites"),
                "rows": .table(["k1": "Expenses:Kites", "k2": "Expenses:Kites", "k3": "Expenses:Kites"]),
            ])
        let account = AccountRef(id: "acct-9", name: "Harbor savings")
        #expect(
            Decision.book(k1, to: .account(account)).given == [
                "row": .text("k1"), "says": .text("says-k1"), "account": .text("acct-9"),
            ])
        #expect(
            Decision.book(k1, to: .typed("Expenses:Garden")).given == [
                "row": .text("k1"), "says": .text("says-k1"), "category": .text("Expenses:Garden"),
            ])
        #expect(Decision.exclude(k1).given == ["row": .text("k1"), "says": .text("says-k1")])
    }

    @Test("what the queue is narrowed to is asked for by the names the engine reads")
    func query() {
        var query = ReviewQuery()
        #expect(query.given.isEmpty)
        query.order = .amount
        query.category = ReviewQueue.Choice(value: "-", label: "Nothing suggested", rows: 1)
        query.text = "  corner   hardware "
        #expect(query.given == ["order": .text("amount"), "category": .text("-"), "text": .text("corner hardware")])
    }
}

@Suite("Working through Review")
@MainActor
struct ReviewModelTests {
    private func model(_ engine: ScriptedReview) -> (ReviewModel, Desk) {
        let desk = Desk(engine: engine, pause: { _ in })
        return (desk.review, desk)
    }

    @Test("the queue opens on its first group, with a group that is one decision closed")
    func opens() async {
        let engine = ScriptedReview(["review": [full]])
        let (review, _) = model(engine)

        await review.reload()

        #expect(review.state == .ready)
        #expect(review.selection == .group("Kites"))
        #expect(review.items == [.group("Kites"), .group("Unsure"), .row("u1")])
    }

    @Test("up and down move between items, and stop at either end")
    func moves() async throws {
        let (review, _) = model(ScriptedReview(["review": [full]]))
        await review.reload()

        review.move(1)
        #expect(review.selection == .group("Unsure"))
        review.move(1)
        review.move(1)
        #expect(review.selection == .row("u1"))
        review.move(-5)
        #expect(review.selection == .group("Kites"))
        review.toggle(try #require(review.queue?.group("Kites")))
        review.move(1)
        #expect(review.selection == .row("k1"))
    }

    @Test("opening or closing a group is seen by whatever drew the items")
    func toggleIsObserved() async throws {
        let (review, _) = model(ScriptedReview(["review": [full]]))
        await review.reload()
        let kites = try #require(review.queue?.group("Kites"))
        let changed = Flag()
        withObservationTracking {
            _ = review.items
        } onChange: {
            changed.raise()
        }

        review.toggle(kites)

        #expect(changed.raised, "a list drawn from the items would not show the group's rows")
        #expect(review.items == [.group("Kites"), .row("k1"), .row("k2"), .row("k3"), .group("Unsure"), .row("u1")])
    }

    @Test("Return accepts a group that is one decision, bound to its rows, and lands on the next")
    func acceptsAGroup() async throws {
        let after = queue([("Unsure", ["u1"], false)], rows: [unsure])
        let engine = ScriptedReview(
            ["review": [full, after], "overview": [], "where": []],
            results: [ActResult(outcome: .accepted, ruling: 7, rows: 3)])
        let (review, desk) = model(engine)
        await review.reload()

        await review.acceptSelected()

        let acted = await engine.acted
        #expect(acted.map(\.0) == ["accept_group"])
        #expect(acted.first?.1["rows"] == .table(["k1": "Expenses:Kites", "k2": "Expenses:Kites", "k3": "Expenses:Kites"]))
        #expect(review.selection == .group("Unsure"))
        #expect(desk.decisions.notice?.text == "Accepted 3 as Kites.")
        #expect(desk.decisions.canUndo)
    }

    @Test("a difference with a bank is only confirmed: Return accepts it, nothing excludes or rebooks it")
    func confirmsADifference() async throws {
        let difference = row(
            "d1", group: "Where the imported ledger and the bank disagree", amount: "-8.00",
            suggested: "Equity:Ledger-Bank-Difference", excludable: true, kind: "reconciling")
        let before = queue([("Where the imported ledger and the bank disagree", ["d1"], false)], rows: [difference])
        let after = queue([], rows: [])
        let engine = ScriptedReview(
            ["review": [before, after], "overview": [], "where": []],
            results: [ActResult(outcome: .accepted, ruling: 9, rows: 1)])
        let (review, desk) = model(engine)
        await review.reload()
        review.selection = .row("d1")
        let shown = try #require(review.selectedRow)

        #expect(shown.isReconciling)
        // Even were the engine to offer it, a difference is never excluded nor given a category.
        #expect(!review.canExcludeSelected)
        #expect(!review.canChooseForSelected)
        await review.exclude(shown)
        review.chosen = .typed("Expenses:Garden")
        await review.acceptSelected()

        let acted = await engine.acted
        #expect(acted.map(\.0) == ["accept"])
        #expect(acted.first?.1 == ["row": .text("d1"), "shown": .text("Equity:Ledger-Bank-Difference")])
        #expect(desk.decisions.notice?.text == "Confirmed. The difference stays booked as it is.")
    }

    @Test("a row kind this app does not know is read as a transaction, not a failed queue")
    func unknownKind() throws {
        let later = row("n1", group: "New", kind: "something-new")
        guard case .ready(let found) = try decode(queue([("New", ["n1"], false)], rows: [later]), as: ReviewQueue.self)
        else {
            Issue.record("a ready answer")
            return
        }
        #expect(found.row("n1")?.kind == .transaction)
    }

    @Test("a group that is not one decision is never accepted at once; Return opens its first row")
    func groupOfGuesses() async {
        let engine = ScriptedReview(["review": [full]])
        let (review, _) = model(engine)
        await review.reload()
        review.selection = .group("Unsure")

        await review.acceptSelected()
        await review.accept(review.queue!.group("Unsure")!)

        #expect(await engine.acted.isEmpty)
        #expect(review.selection == .row("u1"))
        // Nothing suggested is nothing to accept, and its bank counts it, so no Exclude.
        await review.acceptSelected()
        #expect(!review.canExcludeSelected)
        await review.exclude(review.selectedRow!)
        #expect(await engine.acted.isEmpty)
    }

    @Test("after a row is decided the next row is chosen, as the queue was")
    func keepsPlace() async throws {
        let rest = queue([("Kites", ["k2", "k3"], true), ("Unsure", ["u1"], false)], rows: Array(kites[1...]) + [unsure])
        let engine = ScriptedReview(
            ["review": [full, rest], "overview": []], results: [ActResult(outcome: .accepted, ruling: 8, rows: 1)])
        let (review, _) = model(engine)
        await review.reload()
        review.toggle(review.queue!.group("Kites")!)
        review.selection = .row("k1")

        await review.acceptSelected()

        #expect(await engine.acted.first?.1 == ["row": .text("k1"), "shown": .text("Expenses:Kites")])
        #expect(review.selection == .row("k2"))
        #expect(review.isOpen(review.queue!.group("Kites")!), "the group the person opened stays open")
    }

    @Test("a row that changed since it was shown stays where it is, and the person is told")
    func stale() async throws {
        let changed = queue(
            [("Kites", ["k1", "k2", "k3"], true), ("Unsure", ["u1"], false)],
            rows: [row("k1", group: "Kites", amount: "-30.00", says: "says-k1-now")] + Array(kites[1...]) + [unsure])
        let engine = ScriptedReview(
            ["review": [full, changed], "overview": []], results: [ActResult(outcome: .changed, changed: 1)])
        let (review, desk) = model(engine)
        await review.reload()
        review.toggle(review.queue!.group("Kites")!)
        review.selection = .row("k1")
        let before = review.revision

        await review.book(review.selectedRow!, to: .typed("Expenses:Garden"))

        #expect(review.selection == .row("k1"))
        #expect(review.selectedRow?.amount == money("-30.00"))
        #expect(review.revision > before, "the detail on screen is asked for again")
        let notice = try #require(desk.decisions.notice)
        #expect(notice.tone == .problem)
        #expect(notice.text.hasPrefix("That transaction changed since Hearth showed it"))
        #expect(!desk.decisions.canUndo, "nothing was decided, so there is nothing to take back")
    }

    @Test("Undo takes back exactly the last decision, and is offered only until the next")
    func undo() async throws {
        let engine = ScriptedReview(
            ["review": [full], "overview": []],
            results: [
                ActResult(outcome: .accepted, ruling: 11, rows: 1),
                ActResult(outcome: .excluded, ruling: 12, rows: 1),
                ActResult(outcome: .undone, rows: 1),
            ])
        let (review, desk) = model(engine)
        await review.reload()
        let k1 = review.queue!.row("k1")!
        let k2 = review.queue!.row("k2")!

        await review.accept(k1)
        await review.exclude(k2)
        await review.undo()

        let acted = await engine.acted
        #expect(acted.map(\.0) == ["accept", "exclude", "undo"])
        #expect(acted.last?.1 == ["ruling": .number(12)])
        #expect(desk.decisions.notice?.text == "Taken back. That transaction is waiting for review again.")
        #expect(!desk.decisions.canUndo, "an undo is not taken back")
        await review.undo()
        #expect(await engine.acted.count == 3)
    }

    @Test("choosing the suggestion is accepting it")
    func choosingTheSuggestion() async {
        let engine = ScriptedReview(
            ["review": [full], "overview": []], results: [ActResult(outcome: .accepted, ruling: 3, rows: 1)])
        let (review, _) = model(engine)
        await review.reload()
        let k1 = review.queue!.row("k1")!

        await review.book(k1, to: .category(k1.suggested!))

        #expect(await engine.acted.map(\.0) == ["accept"])
    }

    @Test("Return settles a row as chosen, and a choice lasts only while the person stays on it")
    func returnTakesTheChoice() async {
        let engine = ScriptedReview(
            ["review": [full], "overview": []], results: [ActResult(outcome: .booked, ruling: 4, rows: 1)])
        let (review, desk) = model(engine)
        await review.reload()
        review.toggle(review.queue!.group("Kites")!)
        review.selection = .row("k1")
        #expect(review.chosen == .category(CategoryRef(name: "Expenses:Kites", label: "Kites")))

        review.chosen = .typed("Expenses:Garden")
        review.move(1)
        #expect(review.chosen == .category(CategoryRef(name: "Expenses:Kites", label: "Kites")))
        review.move(-1)
        review.chosen = .typed("Expenses:Garden")
        await review.acceptSelected()

        let acted = await engine.acted
        #expect(acted.map(\.0) == ["book"])
        #expect(acted.first?.1["category"] == .text("Expenses:Garden"))
        #expect(desk.decisions.notice?.text == "Saved under Garden.")
    }

    @Test("a decision refused is said in words and decides nothing")
    func refused() async throws {
        let engine = ScriptedReview(
            ["review": [full], "overview": []], results: [ActResult(outcome: .banksRow)])
        let (review, desk) = model(engine)
        await review.reload()

        await desk.decisions.make(.exclude(review.queue!.row("k1")!))

        let notice = try #require(desk.decisions.notice)
        #expect(notice.tone == .problem)
        #expect(notice.text.contains("Its bank counts it in the balance it reports"))
        #expect(!desk.decisions.canUndo)
    }

    @Test("a category the books close on the row's day is refused in the page's words")
    func notOpen() async throws {
        let engine = ScriptedReview(
            ["review": [full], "overview": []], results: [ActResult(outcome: .notOpen)])
        let (review, desk) = model(engine)
        await review.reload()

        await desk.decisions.make(.book(review.queue!.row("k1")!, to: .typed("Expenses:Garden")))

        let notice = try #require(desk.decisions.notice)
        #expect(notice.tone == .problem)
        #expect(notice.text.contains("Your books close that category before the day of this transaction"))
        #expect(!desk.decisions.canUndo)
    }

    @Test("an outcome this app does not know says so, and offers no Undo")
    func unknownOutcome() async throws {
        let engine = ScriptedReview(
            ["review": [full], "overview": []], results: [ActResult(outcome: .unknown, ruling: 7, rows: 1)])
        let (review, desk) = model(engine)
        await review.reload()

        await desk.decisions.make(.exclude(review.queue!.row("k1")!))

        let notice = try #require(desk.decisions.notice)
        #expect(notice.tone == .problem)
        #expect(notice.text == "Hearth could not say what became of that. Here is what is waiting now.")
        #expect(!desk.decisions.canUndo)
    }

    @Test("what this month's totals leave out unplaced is said as the person's own page says it")
    func unplaced() {
        let waiting = Overview.Waiting(
            waiting: 4, unplacedSpending: Overview.Unplaced(rows: 1, moved: money("12.00")),
            unplacedIncome: Overview.Unplaced(rows: 2, moved: money("300.00")))
        #expect(
            ReviewWords.unplaced(waiting, month: "October", currency: "USD", locale: Locale(identifier: "en_US")) == [
                "October's spending leaves out 1 transaction of $12 nobody has said what it was yet, which is in no total until placed in Review.",
                "October's income leaves out 2 transactions of $300 nobody has said what they were yet, which are in no total until placed in Review.",
            ])
        #expect(ReviewWords.unplaced(Overview.Waiting(waiting: 4), month: "October", currency: "USD") == [])
    }

    @Test("narrowing asks again with what it is narrowed to")
    func narrows() async {
        let engine = ScriptedReview(["review": [full]])
        let (review, _) = model(engine)
        await review.reload()
        var query = review.query
        query.order = .confidence
        query.text = "kite"

        await review.apply(query)

        let asked = await engine.asked
        #expect(asked.last?.1 == ["order": .text("confidence"), "text": .text("kite")])
    }

    @Test("one by one lists every row in the queue's order")
    func oneByOne() async {
        let (review, _) = model(ScriptedReview(["review": [full]]))
        await review.reload()

        review.showOneByOne(true)

        #expect(review.items == [.row("k1"), .row("k2"), .row("k3"), .row("u1")])
        #expect(review.selection == .row("k1"))
    }

    @Test("an engine that says nobody is signed in ends the screen")
    func signedOut() async {
        var ended = false
        let engine = ScriptedReview(["review": [#"{"state":"signed_out"}"#]])
        let desk = Desk(engine: engine, signedOut: { ended = true }, pause: { _ in })

        await desk.review.reload()

        #expect(desk.review.state == .signedOut)
        #expect(ended)
    }
}

/// Raised once, from whichever thread observation calls back on.
private final class Flag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    var raised: Bool { lock.withLock { value } }

    func raise() { lock.withLock { value = true } }
}
