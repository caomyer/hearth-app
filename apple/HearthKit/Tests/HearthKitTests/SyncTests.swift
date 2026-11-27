import Foundation
import Testing

@testable import HearthKit

/// A `banks` answer, as the engine writes it.
private func banks(
    asking: [String] = ["read"], running: Bool = false, finishedAt: String? = nil, stopped: String? = nil,
    banks: String = #"[{"institution":"Larkspur Savings Bank","synced_at":"2026-10-15T13:02:00Z","needs_sign_in":false,"added":null,"trouble":null}]"#
) -> String {
    let asked = asking.map { "\"\($0)\"" }.joined(separator: ",")
    let finished = finishedAt.map { "\"\($0)\"" } ?? "null"
    let stop = stopped.map { "\"\($0)\"" } ?? "null"
    return #"{"state":"ready","asking":["# + asked + #"],"running":"# + "\(running)" + #","finished_at":"# + finished
        + #","stopped":"# + stop + #","banks":"# + banks + "}"
}

/// An engine for the sync control: asks answered from a script, actions from another, and
/// everything it was asked noted.
private actor ScriptedSyncs: Acting {
    private var answers: [String]
    private var outcomes: [ActOutcome]
    private(set) var acted: [String] = []

    init(_ answers: [String], outcomes: [ActOutcome] = []) {
        self.answers = answers
        self.outcomes = outcomes
    }

    func ask<Content: Decodable & Sendable>(
        _ screen: String, with given: Given, as content: Content.Type
    ) async throws -> Answer<Content> {
        precondition(screen == "banks")
        guard !answers.isEmpty else { throw Engine.Failure.stopped }
        return try decode(answers.removeFirst())
    }

    func act(_ action: String, with given: Given) async throws -> ActResult {
        acted.append(action)
        guard !outcomes.isEmpty else { throw Engine.Failure.stopped }
        return ActResult(outcome: outcomes.removeFirst())
    }
}

private let utc: Calendar = {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(identifier: "UTC")!
    return calendar
}()
private let afternoon = ISO8601DateFormatter().date(from: "2026-10-15T15:00:00Z")!

@Suite("The sync control")
@MainActor
struct SyncTests {
    @Test("the banks screen has the shape the engine writes")
    func shape() throws {
        guard case .ready(let found) = try replyDecoder().decode(Reply<Banks>.self, from: fixture("banks")).answer
        else {
            Issue.record("the example is a ready answer")
            return
        }
        #expect(found.asking == ["holdings", "read"])
        #expect(found.finishedAt == ISO8601DateFormatter().date(from: "2026-10-15T13:00:00Z"))
        #expect(found.banks.map(\.institution) == ["Larkspur Savings Bank", "Wrenfield Brokerage"])
        #expect(found.banks[0].added == 3)
        #expect(found.banks[1].trouble == .signIn)
        #expect(found.banks[1].needsSignIn)
    }

    @Test("every outcome of an action the engine has is one the app decodes")
    func outcomes() throws {
        let told = try JSONDecoder().decode([String].self, from: fixture("acted"))
        #expect(told.compactMap(ActOutcome.init(rawValue:)).count == told.count)
        #expect(Set(told) == Set(ActOutcome.told.map(\.rawValue)))
        let troubles = Trouble.allCases.map(\.rawValue)
        #expect(troubles == ["sign_in", "unreachable", "keys", "refused", "failed"])
    }

    @Test("with nothing allowed it says so, and asking does nothing")
    func off() async {
        let engine = ScriptedSyncs([banks(asking: [])])
        let model = SyncModel(engine: engine)

        await model.refresh()
        #expect(!model.mayAsk)
        #expect(model.label() == "Not asking your banks")

        await model.syncNow()
        #expect(await engine.acted.isEmpty)
    }

    @Test("allowed, it says when the banks were last updated")
    func idle() async {
        let model = SyncModel(engine: ScriptedSyncs([banks()]))

        #expect(model.label() == "")
        await model.refresh()

        #expect(model.canSyncNow)
        #expect(model.label(now: afternoon, calendar: utc, locale: unitedStates) == "Updated today, 1:02\u{202F}PM")
        #expect(!model.needsAttention)
    }

    @Test("a sync asked for shows its progress, then what arrived")
    func syncs() async {
        let engine = ScriptedSyncs(
            [
                banks(),
                banks(running: true),
                banks(running: true),
                banks(
                    finishedAt: "2026-10-15T14:30:00Z",
                    banks: #"[{"institution":"Larkspur Savings Bank","synced_at":"2026-10-15T14:30:00Z","needs_sign_in":false,"added":3,"trouble":null}]"#),
            ],
            outcomes: [.started])
        let model = SyncModel(engine: engine, pause: { _ in })
        await model.refresh()

        await model.syncNow()

        #expect(await engine.acted == ["sync"])
        #expect(model.answered == 1)
        #expect(!model.isSyncing)
        #expect(model.banks?.arrived == "3 new transactions")
        #expect(model.label(now: afternoon, calendar: utc, locale: unitedStates) == "Updated today, 2:30\u{202F}PM")
    }

    @Test("while a sync runs it says so and cannot be asked again")
    func running() async {
        let engine = ScriptedSyncs([banks(running: true)])
        let model = SyncModel(engine: engine)

        await model.refresh()

        #expect(model.isSyncing)
        #expect(!model.canSyncNow)
        #expect(model.label() == "Updating\u{2026}")
        await model.syncNow()
        #expect(await engine.acted.isEmpty)
    }

    @Test("a sync already running when asked is followed to its end")
    func joins() async {
        let engine = ScriptedSyncs(
            [banks(), banks(running: true), banks(finishedAt: "2026-10-15T14:30:00Z")], outcomes: [.running])
        let model = SyncModel(engine: engine, pause: { _ in })
        await model.refresh()

        await model.syncNow()

        #expect(model.answered == 1)
        #expect(model.banks?.arrived == "No new transactions")
    }

    @Test("what went wrong is said in words that say what to do, a sign-in first")
    func troubles() async {
        let answer = banks(
            finishedAt: "2026-10-15T14:30:00Z",
            banks: #"""
                [{"institution":"Larkspur Savings Bank","synced_at":"2026-10-14T13:00:00Z","needs_sign_in":false,"added":0,"trouble":"unreachable"},
                 {"institution":"Wrenfield Brokerage","synced_at":null,"needs_sign_in":true,"added":0,"trouble":"sign_in"},
                 {"institution":"Corvid Credit Union","synced_at":null,"needs_sign_in":false,"added":0,"trouble":"unreachable"}]
                """#)
        let model = SyncModel(engine: ScriptedSyncs([answer]))
        await model.refresh()

        #expect(model.needsAttention)
        #expect(
            model.banks?.troubles == [
                "Wrenfield Brokerage needs you to sign in again. Do that in Hearth in the browser.",
                "Plaid could not be reached. Hearth tries again in the morning.",
            ])
        // The control's line says who needs the person, in place of how fresh the banks are.
        #expect(model.label(now: afternoon, calendar: utc, locale: unitedStates) == "Wrenfield Brokerage needs you")
    }

    @Test("asking switched off is a choice, said plainly; only what needs the person warns")
    func offIsNotATrouble() async {
        let model = SyncModel(engine: ScriptedSyncs([banks(asking: [])]))
        await model.refresh()

        #expect(model.label() == "Not asking your banks")
        #expect(!model.needsAttention)

        let stopped = SyncModel(
            engine: ScriptedSyncs([banks(finishedAt: "2026-10-15T14:30:00Z", stopped: "failed")]))
        await stopped.refresh()
        #expect(stopped.needsAttention)
        #expect(stopped.label() == "Last update went wrong")

        let two = SyncModel(
            engine: ScriptedSyncs([
                banks(
                    asking: [],
                    banks: #"""
                        [{"institution":"Larkspur Savings Bank","synced_at":null,"needs_sign_in":true,"added":null,"trouble":null},
                         {"institution":"Wrenfield Brokerage","synced_at":null,"needs_sign_in":true,"added":null,"trouble":null}]
                        """#)
            ]))
        await two.refresh()
        #expect(two.needsAttention)
        #expect(two.label() == "2 banks need you")
    }

    @Test("the sample's banks are said to be invented, and its sign-in is shown, not asked for")
    func sample() async {
        let answer = banks(
            asking: [],
            banks: #"""
                [{"institution":"Northfield Bank","synced_at":"2026-10-15T13:02:00Z","needs_sign_in":false,"added":null,"trouble":null},
                 {"institution":"Cedar Card","synced_at":"2026-10-12T12:00:00Z","needs_sign_in":true,"added":null,"trouble":null}]
                """#)
        let model = SyncModel(engine: ScriptedSyncs([answer]))
        await model.refresh()

        #expect(model.label(sample: true) == "Invented banks")
        #expect(!model.needsAttention(sample: true))
        #expect(
            model.banks?.notes(sample: true) == [
                Banks.Note(
                    text:
                        "Cedar Card shows a bank that wants you to sign in again. With your own banks, you do that in Hearth in the browser.",
                    needsYou: false)
            ])
        // The same banks in a household's own books need the person.
        #expect(model.needsAttention)
        #expect(model.label() == "Cedar Card needs you")
    }

    @Test("a sync that stopped, or keys the Keychain would not give, are said as such")
    func stopped() throws {
        func troubles(_ json: String) throws -> [String] {
            guard case .ready(let found) = try decode(json, as: Banks.self) else { return [] }
            return found.troubles
        }
        #expect(
            try troubles(banks(finishedAt: "2026-10-15T14:30:00Z", stopped: "failed", banks: "[]"))
                == ["The update stopped before it finished. Hearth's log says why."])
        #expect(
            try troubles(
                banks(
                    finishedAt: "2026-10-15T14:30:00Z",
                    banks: #"[{"institution":"Larkspur Savings Bank","synced_at":null,"needs_sign_in":false,"added":0,"trouble":"keys"}]"#)
            ) == ["Hearth could not open its Plaid keys in this Mac's Keychain."])
    }

    @Test("a viewer with no bank of their own is told so")
    func noBanks() async {
        let model = SyncModel(engine: ScriptedSyncs([banks(banks: "[]")]))
        await model.refresh()

        #expect(model.label() == "No banks connected")
        #expect(model.banks?.arrived == nil)
    }

    @Test("an engine that stopped, or signed the member out, leaves nothing to show")
    func gone() async {
        var told = false
        let model = SyncModel(engine: ScriptedSyncs([#"{"state":"signed_out"}"#]), signedOut: { told = true })

        #expect(await model.refresh() == nil)
        #expect(model.state == .unavailable)
        #expect(told)

        let stopped = SyncModel(engine: ScriptedSyncs([]))
        #expect(await stopped.refresh() == nil)
        #expect(stopped.state == .unavailable)
    }

    @Test("an engine that refuses the action is asked again what it allows")
    func refused() async {
        let engine = ScriptedSyncs([banks(), banks(asking: [])], outcomes: [.notAllowed])
        let model = SyncModel(engine: engine, pause: { _ in })
        await model.refresh()

        await model.syncNow()

        #expect(model.answered == 0)
        #expect(!model.mayAsk)
        #expect(!model.isSyncing)
    }

    @Test("counts are said as a person says them")
    func counts() {
        #expect(Banks.count(0) == "No new transactions")
        #expect(Banks.count(1) == "1 new transaction")
        #expect(Banks.count(1204).hasSuffix(" new transactions"))
    }
}
