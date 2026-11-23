import XCTest

/// The whole chain, as a person meets it: the signed app starts the Python engine it carries,
/// the engine works out the invented household's books, and the app shows a net worth.
final class FirstLaunchTests: XCTestCase {
    @MainActor
    func testTheAppShowsNetWorthFromTheEngineItCarries() {
        let app = XCUIApplication()
        app.launch()

        let netWorth = app.staticTexts["overview.netWorth"]
        XCTAssertTrue(netWorth.waitForExistence(timeout: 60), "no net worth appeared within a minute")

        let figure = text(of: netWorth)
        XCTAssertNotNil(
            figure.wholeMatch(of: /\$\d{1,3}(,\d{3})+/), "\(figure) is not a total in whole dollars")
        XCTAssertTrue(app.staticTexts["sample.banner"].exists || app.otherElements["sample.banner"].exists)
        let freshness = text(of: app.staticTexts["overview.freshness"])
        XCTAssertTrue(freshness.hasPrefix("Computed "), "\(freshness) does not say when it was computed")
    }

    /// What a piece of text says. On macOS the accessibility tree carries a SwiftUI `Text`'s
    /// string as its value and leaves its label empty, so the label is only a fallback.
    @MainActor
    private func text(of element: XCUIElement) -> String {
        if let value = element.value as? String, !value.isEmpty {
            return value
        }
        return element.label
    }
}
