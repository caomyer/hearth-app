import Foundation
import Testing

@testable import HearthKit

private let ready = #"""
    {"state":"ready","computed_at":"2026-10-15T12:00:00Z","as_of":"2026-10-15","currency":"USD",
     "recomputing":false,"connected":true,
     "net_worth":{"value":"162711.83","is_known":true,"is_partial":false,"is_floor":false,"without":[],
                  "since_month_start":"100.00","over_year":null,"priced_on":"2026-10-14","stale_prices":0},
     "history":[{"day":"2026-10-15","value":"162711.83","is_floor":false}],"review":{"waiting":0,"spending":null},"taxed":false}
    """#
private let recomputing = ready.replacingOccurrences(of: #""recomputing":false"#, with: #""recomputing":true"#)

@Suite("The Overview screen's state")
@MainActor
struct OverviewModelTests {
    @Test("starts with nothing heard, then loading, then the figures")
    func fills() async {
        let model = OverviewModel(engine: Scripted([#"{"state":"loading"}"#, ready]))
        #expect(model.state == .starting)

        #expect(await model.refresh() == OverviewModel.whileLoading)
        #expect(model.state == .loading)

        #expect(await model.refresh() == OverviewModel.whileReady)
        guard case .ready(let overview) = model.state else {
            Issue.record("ready")
            return
        }
        #expect(overview.netWorth.value == money("162711.83"))
    }

    @Test("figures on screen stay while newer ones are computed, and are asked for again soon")
    func staysWhileRecomputing() async {
        let model = OverviewModel(engine: Scripted([ready, #"{"state":"loading"}"#, recomputing]))

        await model.refresh()
        let before = model.state

        #expect(await model.refresh() == OverviewModel.whileLoading)
        #expect(model.state == before)
        #expect(await model.refresh() == OverviewModel.whileLoading)
    }

    @Test("a failure is shown in its sentence, and asked about again later")
    func failure() async {
        let model = OverviewModel(engine: Scripted([#"{"state":"failed","summary":"The ledger could not be read."}"#]))

        #expect(await model.refresh() == OverviewModel.whileReady)
        #expect(model.state == .failed("The ledger could not be read."))
    }

    @Test("nothing to show is not asked for again")
    func missing() async {
        let model = OverviewModel(engine: Scripted([#"{"state":"missing"}"#]))

        #expect(await model.refresh() == nil)
        #expect(model.state == .missing)
    }

    @Test("an engine that signs the member out ends the screen, and says so once")
    func signedOut() async {
        let ended = Waits()
        let model = OverviewModel(engine: Scripted([ready, #"{"state":"signed_out"}"#]), signedOut: { Task { await ended.note(.zero) } })

        await model.refresh()
        #expect(await model.refresh() == nil)

        #expect(model.state == .signedOut)
        await Task.yield()
        for _ in 0..<100 where await ended.all.isEmpty {
            await Task.yield()
        }
        #expect(await ended.all == [.zero])
    }

    @Test("an engine that has stopped is said to have stopped")
    func stopped() async {
        let model = OverviewModel(engine: Scripted([]))

        #expect(await model.refresh() == nil)
        #expect(model.state == .stopped)
    }

    @Test("watching asks until the engine stops, waiting between asks without sleeping here")
    func watches() async {
        let engine = Scripted([#"{"state":"loading"}"#, #"{"state":"loading"}"#, ready])
        let waits = Waits()
        let model = OverviewModel(engine: engine, pause: { await waits.note($0) })

        await model.watch()

        #expect(await engine.asked == Array(repeating: "overview", count: 4))
        #expect(await waits.all == [OverviewModel.whileLoading, OverviewModel.whileLoading, OverviewModel.whileReady])
        #expect(model.state == .stopped)
    }
}

private actor Waits {
    private(set) var all: [Duration] = []

    func note(_ duration: Duration) {
        all.append(duration)
    }
}
