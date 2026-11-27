import Foundation
import Testing

@testable import HearthKit

/// An engine for the ledger's sheets: each screen answered from a script of its own, actions
/// from another, and everything asked and acted noted.
private actor ScriptedLedger: Acting {
    private var answers: [String: [String]]
    private var outcomes: [ActOutcome]
    private(set) var asked: [String] = []
    private(set) var acted: [(String, Given)] = []

    init(_ answers: [String: [String]], outcomes: [ActOutcome] = []) {
        self.answers = answers
        self.outcomes = outcomes
    }

    func ask<Content: Decodable & Sendable>(
        _ screen: String, with given: Given, as content: Content.Type
    ) async throws -> Answer<Content> {
        asked.append(screen)
        guard var left = answers[screen], !left.isEmpty else { throw Engine.Failure.stopped }
        let next = left.removeFirst()
        // The last answer of a screen stands for every ask after it.
        answers[screen] = left.isEmpty ? [next] : left
        return try decode(next)
    }

    func act(_ action: String, with given: Given) async throws -> ActResult {
        acted.append((action, given))
        guard !outcomes.isEmpty else { throw Engine.Failure.stopped }
        return ActResult(outcome: outcomes.removeFirst())
    }
}

private func answer(of name: String) throws -> String {
    let data = try fixture(name)
    let reply = try JSONSerialization.jsonObject(with: data) as! [String: Any]
    let found = try JSONSerialization.data(withJSONObject: reply["answer"]!, options: [.sortedKeys])
    return String(decoding: found, as: UTF8.self)
}

private func ready<Content: Decodable & Sendable>(_ name: String, as content: Content.Type) throws -> Content {
    guard case .ready(let found) = try replyDecoder().decode(Reply<Content>.self, from: fixture(name)).answer
    else { throw Engine.Failure.refused }
    return found
}

private func standing(
    running: Bool = false, outcome: String? = nil, refused: String? = nil, mayBringIn: Bool = true,
    importing: Bool = false, imported: Bool = false, booksFailed: Bool = false, mine: Bool = true
) -> String {
    let said = refused.map { #"{"why":"once","said":"\#($0)"}"# } ?? "null"
    let bringing =
        !mine || (outcome == nil && !running)
        ? "null"
        : #"{"running":\#(running),"outcome":\#(outcome.map { "\"\($0)\"" } ?? "null"),"refused":\#(said),"finished_at":null,"books_failed":\#(booksFailed)}"#
    let base = imported ? #"{"on":"2026-11-22","files":1,"unclaimed":null}"# : "null"
    return
        #"{"state":"ready","imported":\#(base),"inherited":false,"importing":\#(importing),"may_bring_in":\#(mayBringIn),"why_not":null,"bringing_in":\#(bringing)}"#
}

private let loading = #"{"state":"loading"}"#

private let file = URL(fileURLWithPath: "/Users/zq/Ledger/household.beancount")
private let noPause: @Sendable (Duration) async throws -> Void = { _ in }

@Suite("Bringing in a ledger")
@MainActor
struct LedgerTests {
    @Test("the ledger's three screens have the shapes the engine writes")
    func shapes() throws {
        let standing = try ready("ledger", as: LedgerStanding.self)
        #expect(standing.imported?.files == 1)
        #expect(standing.bringingIn?.outcome == .imported)
        #expect(standing.mayBringIn == false)
        let check = try ready("ledger_check", as: LedgerCheck.self)
        #expect(check.refused == nil)
        #expect(check.found?.entries == 20)
        #expect(check.found?.worth.value == money("3054.50"))
        #expect(check.found?.unusedKinds.plugins == 2)
        let matching = try ready("matching", as: Matching.self)
        #expect(matching.imported)
        #expect(matching.accounts.first?.state == .matched)
        #expect(matching.accounts.first?.facing?.inLedger.count != nil)
        #expect(matching.accounts.contains { $0.whyNot == "unclear" && $0.proposed == nil })
        #expect(matching.accounts.flatMap(\.choices).contains { $0.refused?.why == "taken" })
    }

    @Test("the check is said in counts, months and the ledger's own net worth")
    func check() throws {
        let found = try #require(try ready("ledger_check", as: LedgerCheck.self).found)

        #expect(LedgerWords.summary(found, locale: unitedStates) == "20 entries \u{00B7} 6 accounts \u{00B7} Jan 2026 to Jul 2026")
        #expect(LedgerWords.balanceChecks(found, locale: unitedStates) == "4 balance checks pass")
        #expect(LedgerWords.worth(found, locale: unitedStates) == "Net worth today: $3,055, at the ledger's own prices")
        #expect(LedgerWords.unusedTitle(found, locale: unitedStates) == "5 lines not used by Hearth")
        #expect(
            LedgerWords.unusedDetail(found, locale: unitedStates)
                == "2 plugins, 2 custom directives and 1 document. They are kept in Hearth's copy, and a plugin that is not beancount's own never runs."
        )
        #expect(
            LedgerWords.kinds(found, locale: unitedStates)
                == "2 under Assets, 1 under Liabilities, 1 under Equity, 1 under Income and 1 under Expenses")
        #expect(LedgerWords.checkTitle(file: "household.beancount", LedgerCheck(found: found, refused: nil))
            == "household.beancount is ready to bring in")
        #expect(LedgerWords.checkTitle(file: "household.beancount", LedgerCheck(found: nil, refused: LedgerRefusal(why: "not_clean", said: "No.")))
            == "household.beancount cannot be brought in")
    }

    @Test("matching says where each is added from and what its ledger holds")
    func matchingWords() throws {
        let matching = try ready("matching", as: Matching.self)
        let union = try #require(matching.accounts.first)
        let facing = try #require(union.facing)

        #expect(LedgerWords.name(union) == "SHARE DRAFT \u{00B7}\u{00B7}\u{00B7}0606")
        #expect(LedgerWords.from(facing, locale: unitedStates) == "from May 24")
        #expect(LedgerWords.meets(facing, locale: unitedStates).hasSuffix("already in your ledger, nothing new to add yet."))
        #expect(LedgerWords.matchedCount(matching, locale: unitedStates) == "1 of 8 accounts matched")
        #expect(LedgerWords.matchedExample(matching) == "SHARE DRAFT \u{00B7}\u{00B7}\u{00B7}0606 is Assets:Zq:Union.")
        #expect(LedgerWords.inLedger(matching, locale: unitedStates) == "8 transactions already in your ledger")
        let blakes = try #require(matching.accounts.first { $0.whyNot == "unclear" })
        #expect(LedgerWords.whyNot(blakes) == "No account of your ledger clearly holds its transactions.")
        #expect(
            LedgerWords.refused(Choice.Refused(why: "would_lose", count: 2, day: Day("2026-06-12")), locale: unitedStates)
                == "Your ledger does not hold 2 transactions from before Jun 12 that Hearth has, so matching it would take them out of your books."
        )
    }

    @Test("each account starts chosen as it is matched, or as the ledger proposes")
    func chosen() throws {
        let matching = try ready("matching", as: Matching.self)
        let chosen = LedgerFlow.chosen(in: matching)

        let ids = matching.accounts.map(\.id)
        #expect(chosen[ids[0]] == "Assets:Zq:Union")
        #expect(chosen[ids[1]] == "Liabilities:Zq:Card")
        #expect(chosen[ids[2]] == nil, "nothing is proposed, so it is left as it is")
        // A choice the engine would refuse is not offered.
        #expect(!LedgerFlow.open(matching.accounts[2]).contains { $0.account == "Assets:Zq:Union" })
    }

    @Test("the question is put once, to someone who may bring a ledger in")
    func once() async throws {
        let engine = ScriptedLedger(["ledger": [standing()]])
        var asked = false
        let flow = LedgerFlow(engine: engine, asked: { asked }, remember: { asked = true }, pause: noPause)

        await flow.askOnce()
        #expect(flow.step == .question)
        flow.close()
        await flow.askOnce()
        #expect(flow.step == nil)

        let other = LedgerFlow(
            engine: ScriptedLedger(["ledger": [standing(mayBringIn: false)]]), asked: { false }, pause: noPause)
        await other.askOnce()
        #expect(other.step == nil)
    }

    @Test("a file is checked, confirmed, brought in and its accounts matched, each through the engine")
    func flow() async throws {
        let engine = ScriptedLedger(
            [
                // Slow screens are worked out off the wire and answered `loading` meanwhile.
                "ledger_check": [loading, try answer(of: "ledger_check")],
                "ledger": [standing(running: true), standing(outcome: "imported")],
                "matching": [loading, try answer(of: "matching")],
            ],
            outcomes: [.started, .matched])
        let flow = LedgerFlow(engine: engine, pause: noPause)

        await flow.check(file)
        guard case .checked(_, let check) = flow.step else {
            Issue.record("the check was not shown: \(String(describing: flow.step))")
            return
        }
        #expect(check.refused == nil)
        flow.confirm()
        guard case .confirming = flow.step else {
            Issue.record("no confirmation")
            return
        }
        await flow.bringIn()
        guard case .matching(let matching) = flow.step else {
            Issue.record("matching was not shown: \(String(describing: flow.step))")
            return
        }
        await flow.match()
        guard case .done = flow.step else {
            Issue.record("what was matched was not shown: \(String(describing: flow.step))")
            return
        }
        let acted = await engine.acted
        #expect(acted.map(\.0) == ["bring_in_ledger", "match_accounts"])
        #expect(acted[0].1 == ["path": .text(file.path)])
        // One act for the whole step: only the card, as the union's is matched already, and
        // Blake's is left as it is.
        #expect(acted[1].1 == ["matches": .table([matching.accounts[1].id: "Liabilities:Zq:Card"])])
    }

    @Test("a refusal is said in the engine's words, and a refused file cannot be confirmed")
    func refused() async throws {
        let said = "Your ledger was brought in on Nov 22, 2026, and a ledger is brought in once: hearth's books are the truth from then on."
        let engine = ScriptedLedger([
            "ledger_check": [#"{"state":"ready","found":null,"refused":{"why":"once","said":"\#(said)"}}"#]
        ])
        let flow = LedgerFlow(engine: engine, pause: noPause)

        await flow.check(file)
        flow.confirm()
        guard case .checked(_, let check) = flow.step else {
            Issue.record("a refused file went on to its confirmation")
            return
        }
        #expect(check.refused == LedgerRefusal(why: "once", said: said))

        let importing = ScriptedLedger(
            ["ledger": [standing(outcome: "refused", refused: said)], "ledger_check": [try answer(of: "ledger_check")]],
            outcomes: [.started])
        let other = LedgerFlow(engine: importing, pause: noPause)
        await other.check(file)
        other.confirm()
        await other.bringIn()
        #expect(other.step == .notBroughtIn(LedgerRefusal(why: "once", said: said)))
    }

    @Test("an import someone else started is waited on, never taken for this one")
    func anothersImport() async throws {
        let engine = ScriptedLedger(
            [
                "ledger_check": [try answer(of: "ledger_check")],
                "ledger": [
                    standing(importing: true, mine: false),
                    standing(importing: false, imported: true, mine: false),
                ],
                "matching": [try answer(of: "matching")],
            ],
            outcomes: [.running])
        let flow = LedgerFlow(engine: engine, pause: noPause)
        await flow.check(file)
        flow.confirm()

        await flow.bringIn()

        guard case .matching = flow.step else {
            Issue.record("the ledger brought in by someone else did not lead to matching: \(String(describing: flow.step))")
            return
        }
        #expect(await engine.acted.map(\.0) == ["bring_in_ledger"])
    }

    @Test("a ledger in whose books could not be worked out does not go on to matching")
    func booksFailed() async throws {
        let engine = ScriptedLedger(
            [
                "ledger_check": [try answer(of: "ledger_check")],
                "ledger": [standing(outcome: "imported", booksFailed: true)],
            ],
            outcomes: [.started])
        let flow = LedgerFlow(engine: engine, pause: noPause)
        await flow.check(file)
        flow.confirm()

        await flow.bringIn()

        #expect(flow.step == .inButBooksFailed)
        #expect(!(await engine.asked).contains("matching"))
    }

    @Test("an account with nothing to match to is said once, plainly, and matching never dead-ends")
    func nothingToMatch() throws {
        let matching = try ready("matching", as: Matching.self)
        #expect(LedgerWords.cannotMatch(matching) == nil)
        #expect(LedgerFlow.hasChoices(matching))
        // Blake's savings as it would be with no account of Avery's to offer it.
        var reply = try JSONSerialization.jsonObject(with: fixture("matching")) as! [String: Any]
        var found = reply["answer"] as! [String: Any]
        var accounts = found["accounts"] as! [[String: Any]]
        accounts[2]["choices"] = []
        found["accounts"] = [accounts[2]]
        reply["answer"] = found
        let data = try JSONSerialization.data(withJSONObject: reply)
        guard case .ready(let stuck) = try replyDecoder().decode(Reply<Matching>.self, from: data).answer else {
            Issue.record("not a ready answer")
            return
        }
        #expect(LedgerWords.cannotMatch(stuck)?.hasPrefix("An account here can be matched only to one of your ledger's accounts that is yours.") == true)
        #expect(!LedgerFlow.hasChoices(stuck))
    }

    @Test("each refusal is headed by what happened, and the engine's sentence says the rest")
    func refusalHeadings() {
        #expect(LedgerWords.notBroughtIn(LedgerRefusal(why: "too_slow", said: "")) == "Hearth stopped reading it")
        #expect(LedgerWords.notBroughtIn(LedgerRefusal(why: "busy", said: "")) == "Another ledger is being brought in")
        #expect(LedgerWords.notBroughtIn(LedgerRefusal(why: "lock_unavailable", said: "")) == "Hearth cannot bring a ledger in here")
        #expect(LedgerWords.notBroughtIn(LedgerRefusal(why: "line_too_long", said: "")) == "That is not a ledger")
        #expect(LedgerWords.notBroughtIn(LedgerRefusal(why: "include_outside", said: "")) == LedgerWords.notBroughtIn)
        #expect(LedgerWords.notBroughtIn(nil) == LedgerWords.notBroughtIn)
    }

    @Test("a done step with nothing to match still says something true")
    func doneWithNothing() throws {
        let matching = try ready("matching", as: Matching.self)
        #expect(LedgerWords.doneBody(matching, standing: nil) == nil)
        var reply = try JSONSerialization.jsonObject(with: fixture("matching")) as! [String: Any]
        var found = reply["answer"] as! [String: Any]
        found["accounts"] = []
        reply["answer"] = found
        let data = try JSONSerialization.data(withJSONObject: reply)
        guard case .ready(let none) = try replyDecoder().decode(Reply<Matching>.self, from: data).answer else {
            Issue.record("not a ready answer")
            return
        }
        #expect(LedgerWords.doneBody(none, standing: nil)?.hasPrefix("Its history is in your books now.") == true)
    }

    @Test("an engine that stops says so, rather than leave a sheet waiting")
    func stopped() async {
        let flow = LedgerFlow(engine: ScriptedLedger([:]), pause: noPause)

        await flow.check(file)

        #expect(flow.step == .unavailable)
    }
}
