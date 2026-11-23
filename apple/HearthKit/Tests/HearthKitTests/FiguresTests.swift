import Foundation
import Testing

@testable import HearthKit

@Suite("Figures, as a person reads them")
struct FiguresTests {
    @Test("a transaction's day has its weekday, and its year only when it is another year's")
    func weekday() {
        let asOf = Day(year: 2026, month: 10, day: 15)
        #expect(Figures.weekday(Day(year: 2026, month: 10, day: 3), asOf: asOf, locale: unitedStates) == "Sat, Oct 3")
        #expect(
            Figures.weekday(Day(year: 2025, month: 10, day: 3), asOf: asOf, locale: unitedStates) == "Fri, Oct 3, 2025")
        #expect(Figures.month(Day(year: 2026, month: 9, day: 30), locale: unitedStates) == "Sep 2026")
    }

    @Test("a total is whole dollars")
    func total() {
        #expect(Figures.total(money("846683.89"), currency: "USD", locale: unitedStates) == "$846,684")
        #expect(Figures.total(money("0"), currency: "USD", locale: unitedStates) == "$0")
    }

    @Test("a negative total has a true minus sign, before the currency")
    func negativeTotal() {
        #expect(Figures.total(money("-1284.55"), currency: "USD", locale: unitedStates) == "\u{2212}$1,285")
    }

    @Test("halves round away from zero, as the pages round them")
    func halves() {
        #expect(Figures.total(money("0.50"), currency: "USD", locale: unitedStates) == "$1")
        #expect(Figures.total(money("2.50"), currency: "USD", locale: unitedStates) == "$3")
        #expect(Figures.total(money("-2.50"), currency: "USD", locale: unitedStates) == "\u{2212}$3")
        #expect(Figures.total(money("2.49"), currency: "USD", locale: unitedStates) == "$2")
    }

    @Test("a total that rounds to nothing is not minus zero")
    func noMinusZero() {
        #expect(Figures.total(money("-0.40"), currency: "USD", locale: unitedStates) == "$0")
    }

    @Test("another currency is written as that currency")
    func otherCurrency() {
        #expect(Figures.total(money("1200"), currency: "EUR", locale: unitedStates) == "€1,200")
    }

    @Test("a change says its direction in an arrow and a word, and its size without a sign")
    func change() {
        #expect(Figures.change(money("12960.20"), currency: "USD", locale: unitedStates) == "▲ Up $12,960")
        #expect(Figures.change(money("-1204.50"), currency: "USD", locale: unitedStates) == "▼ Down $1,205")
        #expect(Figures.direction(money("12960.20")) == .up)
        #expect(Figures.direction(money("-1204.50")) == .down)
    }

    @Test("a change too small to write is no change")
    func noChange() {
        #expect(Figures.change(money("0.40"), currency: "USD", locale: unitedStates) == "No change")
        #expect(Figures.change(money("-0.40"), currency: "USD", locale: unitedStates) == "No change")
        #expect(Figures.direction(money("0.40")) == .none)
    }

    @Test("a moment is said by its day and time, never by how long ago")
    func moment() throws {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = try #require(TimeZone(identifier: "America/Los_Angeles"))
        let now = try #require(calendar.date(from: DateComponents(year: 2026, month: 11, day: 22, hour: 17, minute: 44)))
        func at(day: Int, hour: Int) throws -> Date {
            try #require(calendar.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour)))
        }
        func said(_ date: Date) -> String {
            Figures.moment(date, now: now, calendar: calendar, locale: unitedStates)
                .replacingOccurrences(of: "\u{202F}", with: " ")
        }

        #expect(said(try at(day: 22, hour: 6)) == "today, 6:00 AM")
        #expect(said(try at(day: 21, hour: 23)) == "yesterday, 11:00 PM")
        #expect(said(try at(day: 14, hour: 6)) == "Nov 14, 6:00 AM")
    }
}
