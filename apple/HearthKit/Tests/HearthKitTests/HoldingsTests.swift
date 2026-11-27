import Foundation
import Testing

@testable import HearthKit

@Suite("Holdings, and a security's price history, as the engine's own tests check them")
struct HoldingsTests {
    private func holdings() throws -> Holdings {
        let reply = try replyDecoder().decode(Reply<Holdings>.self, from: fixture("holdings"))
        guard case .ready(let holdings) = reply.answer else {
            throw Unexpected.answer
        }
        return holdings
    }

    private enum Unexpected: Error { case answer }

    @Test("the holdings fixture decodes, every holding in a state of its own")
    func decodes() throws {
        let holdings = try holdings()
        #expect(holdings.pricedOn == Day(year: 2026, month: 10, day: 14))
        #expect(holdings.accounts.map(\.name) == ["Wombat Shares", "Juniper Brokerage"])
        let wombat = holdings.accounts[0]
        let held = Dictionary(uniqueKeysWithValues: wombat.held.map { ($0.symbol, $0) })
        let zqaa = try #require(held["ZQAA"])
        #expect(zqaa.units == Quantity(15))
        #expect(zqaa.price?.value == money("1234.56"))
        #expect(zqaa.price?.source == "eodhd")
        #expect(zqaa.price?.fetchedAt == ISO8601DateFormatter().date(from: "2026-10-15T06:00:00Z"))
        #expect(zqaa.gain == money("1172.80"))
        let copper = try #require(held["ZQCC"])
        #expect(copper.freshness == .unpriced)
        #expect(copper.value == nil)
        #expect(copper.gain == nil)
        #expect(held["ZQDD"]?.freshness == .stale)
        #expect(held["ZQDD"]?.price?.age == 10)
        #expect(held["ZQEE"]?.cost == nil)
        #expect(held["USD"]?.isCash == true)
        #expect(wombat.totals.unpriced == 1)
        let juniper = holdings.accounts[1]
        #expect(!juniper.known)
        #expect(juniper.why == "investment")
        #expect(holdings.totals.waiting == 1)
        #expect(holdings.withheld.map(\.inTotal) == [true, false])
    }

    @Test("a security's price history decodes oldest first, each close with its source")
    func history() throws {
        let reply = try replyDecoder().decode(Reply<PriceHistory>.self, from: fixture("price_history"))
        guard case .ready(let history) = reply.answer else {
            Issue.record("the fixture is a ready answer")
            return
        }
        #expect(history.symbol == "ZQAA")
        #expect(history.price?.day == Day(year: 2026, month: 10, day: 14))
        #expect(history.closes.map(\.day) == history.closes.map(\.day).sorted())
        #expect(history.closes.map(\.source) == ["eodhd", "eodhd", "hand", "eodhd"])
    }

    @Test("units keep three places, and every place the books hold")
    func units() {
        #expect(Figures.units(Quantity(Decimal(string: "1284.301")!), locale: unitedStates) == "1,284.301")
        #expect(Figures.units(Quantity(15), locale: unitedStates) == "15.000")
        #expect(Figures.units(Quantity(Decimal(string: "37.0625")!), locale: unitedStates) == "37.0625")
        #expect(Figures.units(Quantity(Decimal(string: "-2.5")!), locale: unitedStates) == "\u{2212}2.500")
    }

    @Test("a gain says its sign, its arrow and its word; a loss its true minus")
    func gain() {
        #expect(Figures.gain(money("1172.80"), currency: "USD", locale: unitedStates) == "▲ +$1,172.80 gain")
        #expect(Figures.gain(money("-310.2"), currency: "USD", locale: unitedStates) == "▼ \u{2212}$310.20 loss")
        #expect(Figures.gain(money("0.00"), currency: "USD", locale: unitedStates) == "No gain or loss")
        #expect(Figures.gain(money("1192.80"), currency: "USD", whole: true, locale: unitedStates) == "▲ +$1,193 gain")
        #expect(Figures.gain(money("0.40"), currency: "USD", whole: true, locale: unitedStates) == "No gain or loss")
        #expect(Figures.gainDirection(money("0.004")) == .none)
        #expect(Figures.gainDirection(money("-0.40"), whole: true) == .none)
        #expect(Figures.gainDirection(money("-0.40")) == .down)
    }

    @Test("a total that leaves a holding out is at least what it says, and says what it leaves out")
    func totals() throws {
        let totals = try holdings().totals
        #expect(Holdings.total(totals, atCost: false, currency: "USD", locale: unitedStates) == "at least $18,846")
        #expect(Holdings.total(totals, atCost: true, currency: "USD", locale: unitedStates) == "at least $17,788")
        #expect(
            Holdings.leavesOut(totals, atCost: false) == [
                "Leaves out 1 holding with no price.",
                "The gain is without 2 holdings whose gain is not known.",
                "Without 1 account whose holdings are not known yet.",
            ])
        #expect(Holdings.leavesOut(totals, atCost: true).first == "Leaves out 1 holding whose cost the books do not hold.")
    }

    @Test("a price says which close it is, how old it is and where it came from")
    func price() throws {
        let held = try holdings().accounts[0].held
        let current = try #require(held.first { $0.symbol == "ZQAA" }?.price)
        #expect(current.close(locale: unitedStates) == "Wed close")
        #expect(current.sourceText == "EODHD")
        let stale = try #require(held.first { $0.symbol == "ZQDD" }?.price)
        #expect(stale.close(locale: unitedStates) == "Oct 5 close")
        #expect(stale.ageText == "10 days old")
        #expect(Holdings.source("hand") == "Entered by hand")
    }

    @Test("every absence is said in words of its own")
    func absences() throws {
        let holdings = try holdings()
        #expect(holdings.accounts[1].absence == "Its brokerage has not said what it holds yet.")
        #expect(holdings.accounts[0].absence == nil)
        let cash = try #require(holdings.accounts[0].held.last)
        #expect((cash.title, cash.subtitle) == ("Cash", "Uninvested"))
        let unnamed = try #require(holdings.accounts[0].held.first { $0.symbol == "ZQEE" })
        #expect(unnamed.title == "ZQEE")
        #expect(
            holdings.withheld.map(\.sentence) == [
                "Blake\u{2019}s positions are private to Blake. Their value is inside the household\u{2019}s net worth.",
                "The joint accounts of Avery and Casey are private to their owners.",
            ])
    }

    @Test("a price history is asked for by its symbol")
    @MainActor
    func asked() async throws {
        let reply = try #require(try JSONSerialization.jsonObject(with: fixture("price_history")) as? [String: Any])
        let answer = try JSONSerialization.data(withJSONObject: try #require(reply["answer"]))
        let engine = Scripted([String(decoding: answer, as: UTF8.self)])
        let model = ScreenModel<PriceHistory>(engine: engine, screen: "price_history", given: ["symbol": .text("ZQAA")])
        await model.refresh()
        #expect(model.content?.closes.count == 4)
        #expect(await engine.asked == ["price_history"])
        #expect(await engine.given == [["symbol": .text("ZQAA")]])
    }
}
