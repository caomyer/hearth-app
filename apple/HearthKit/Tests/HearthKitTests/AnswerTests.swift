import Foundation
import Testing

@testable import HearthKit

@Suite("What the engine answers")
struct AnswerTests {
    @Test("the overview the engine's own tests check is the one the app reads")
    func overviewFixture() throws {
        let reply = try replyDecoder().decode(Reply<Overview>.self, from: fixture("overview"))

        guard case .ready(let overview) = reply.answer else {
            Issue.record("the fixture is a ready answer")
            return
        }
        #expect(reply.id == 1)
        #expect(overview.asOf == Day(year: 2026, month: 10, day: 15))
        #expect(overview.computedAt == Date(timeIntervalSince1970: 1_792_065_600))
        #expect(overview.currency == "USD")
        #expect(overview.connected)
        #expect(!overview.recomputing)
        #expect(overview.netWorth.value == money("162711.83"))
        #expect(overview.netWorth.isKnown)
        #expect(overview.netWorth.isPartial)
        #expect(overview.netWorth.without == ["Joint accounts"])
        #expect(overview.netWorth.sinceMonthStart == money("-1204.50"))
        #expect(overview.netWorth.overYear == money("18412.07"))
        #expect(overview.netWorth.pricedOn == Day(year: 2026, month: 10, day: 14))
        #expect(overview.netWorth.stalePrices == 1)
        #expect(overview.history.count == 3)
        #expect(overview.history.last?.value == overview.netWorth.value)
    }

    @Test("books not computed yet")
    func loading() throws {
        #expect(try decode(#"{"state":"loading"}"#, as: Overview.self) == .loading)
    }

    @Test("a failed recompute carries its one sentence")
    func failed() throws {
        let answer = try decode(#"{"state":"failed","summary":"The ledger could not be read."}"#, as: Overview.self)
        #expect(answer == .failed(summary: "The ledger could not be read."))
    }

    @Test("a screen that is not there, or not this viewer's")
    func missing() throws {
        #expect(try decode(#"{"state":"missing"}"#, as: Overview.self) == .missing)
    }

    @Test("an engine nobody is signed in to says so, and nothing else")
    func signedOut() throws {
        #expect(try decode(#"{"state":"signed_out"}"#, as: Overview.self) == .signedOut)
    }

    @Test("every outcome of signing in the engine's own tests know is one the app reads")
    func outcomes() throws {
        let told = try JSONDecoder().decode([SignInOutcome].self, from: fixture("outcomes"))

        #expect(told == SignInOutcome.allCases)
    }

    @Test("a sign-in's reply carries its outcome under the request's number")
    func signedReply() throws {
        let reply = try replyDecoder().decode(Signed.self, from: Data(#"{"id":4,"outcome":"must_change_password"}"#.utf8))

        #expect(reply.id == 4)
        #expect(reply.outcome == .mustChangePassword)
    }

    @Test("a state the app was never taught is an error, not a guess")
    func unknownState() {
        #expect(throws: DecodingError.self) {
            try decode(#"{"state":"sideways"}"#, as: Overview.self)
        }
    }

    @Test("figures not known are null, and stay unknown")
    func unknownFigures() throws {
        let json = #"""
            {"state":"ready","computed_at":"2026-10-15T12:00:00Z","as_of":"2026-10-15","currency":"USD",
             "recomputing":true,"connected":false,
             "net_worth":{"value":"0","is_known":false,"is_partial":false,"is_floor":false,"without":[],
                          "since_month_start":null,"over_year":null,"priced_on":null,"stale_prices":0},
             "history":[],"review":{"waiting":0,"spending":null}}
            """#
        guard case .ready(let overview) = try decode(json, as: Overview.self) else {
            Issue.record("a ready answer")
            return
        }
        #expect(overview.netWorth.sinceMonthStart == nil)
        #expect(overview.netWorth.overYear == nil)
        #expect(overview.netWorth.pricedOn == nil)
        #expect(!overview.netWorth.isKnown)
    }
}

@Suite("Money and days on the wire")
struct WireValueTests {
    @Test("money is read exactly", arguments: ["162711.83", "-1204.50", "0", "565376.84952", "-0.01"])
    func exactMoney(text: String) throws {
        let value = try JSONDecoder().decode(Money.self, from: Data("\"\(text)\"".utf8))
        #expect(value == money(text))
    }

    @Test("what is not a plain decimal is refused", arguments: ["\"12abc\"", "\"1e5\"", "\"\"", "\"1,000.00\"", "\" 5\"", "12.5", "null"])
    func notMoney(json: String) {
        #expect(throws: DecodingError.self) {
            try JSONDecoder().decode(Money.self, from: Data(json.utf8))
        }
    }

    @Test("what is not a day is refused", arguments: ["\"2026-13-01\"", "\"2026-1-5\"", "\"20261015\"", "\"2026-10-15T00:00:00Z\"", "\"2026-10-00\""])
    func notADay(json: String) {
        #expect(throws: DecodingError.self) {
            try JSONDecoder().decode(Day.self, from: Data(json.utf8))
        }
    }

    @Test("days sort by the calendar")
    func daysSort() {
        let days = [Day(year: 2026, month: 10, day: 2), Day(year: 2025, month: 12, day: 31), Day(year: 2026, month: 9, day: 30)]
        #expect(days.sorted().map(\.month) == [12, 9, 10])
    }

    @Test("a day is the same day on the calendar wherever the Mac is", arguments: ["Pacific/Auckland", "America/Los_Angeles", "Pacific/Kiritimati", "UTC"])
    func dayInAnyZone(zone: String) throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try #require(TimeZone(identifier: zone))

        let date = Day(year: 2026, month: 3, day: 8).date(in: calendar)

        #expect(calendar.dateComponents([.year, .month, .day], from: date) == DateComponents(year: 2026, month: 3, day: 8))
    }
}
