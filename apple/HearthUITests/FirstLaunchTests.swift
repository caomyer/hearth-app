import XCTest

/// The whole chain, as a person meets it: the signed app starts the Python engine it carries,
/// the engine works out the books, and the app shows a net worth.
///
/// Each test points the app at a data directory of its own (`HEARTH_DATA_DIR`), so nothing it
/// remembers, and no household on the Mac that runs it, is part of the test.
final class FirstLaunchTests: XCTestCase {
    override func setUp() {
        continueAfterFailure = false
    }

    /// A Mac with no hearth on it is offered the sample, and the sample shows a net worth.
    @MainActor
    func testTheSampleShowsNetWorthFromTheEngineItCarries() {
        let app = XCUIApplication()
        app.launchEnvironment["HEARTH_DATA_DIR"] = "/var/empty/no-hearth-\(UUID().uuidString)"
        app.launch()

        let sample = app.buttons["start.sample"]
        XCTAssertTrue(sample.waitForExistence(timeout: 20), "the start sheet offered no sample")
        XCTAssertFalse(app.buttons["start.setup"].exists, "a setup was offered where there is none")
        sample.click()

        let netWorth = app.staticTexts["overview.netWorth"]
        XCTAssertTrue(netWorth.waitForExistence(timeout: 60), "no net worth appeared within a minute")
        let figure = text(of: netWorth)
        XCTAssertNotNil(
            figure.wholeMatch(of: /\$\d{1,3}(,\d{3})+/), "\(figure) is not a total in whole dollars")
        XCTAssertTrue(app.staticTexts["sample.banner"].exists || app.otherElements["sample.banner"].exists)
        let freshness = text(of: app.staticTexts["overview.freshness"])
        XCTAssertTrue(freshness.hasPrefix("Computed "), "\(freshness) does not say when it was computed")
    }

    /// A household hearth already keeps on this Mac opens in place once a member signs in, and
    /// opens again on the next launch with the sign-in saved in the Keychain, until they sign out.
    ///
    /// The household is the invented one `tests/native/on_disk.py` writes, which CI makes and
    /// names to the test runner (`TEST_RUNNER_HEARTH_FIXTURE_DATA_DIR`, with its members'
    /// password in `TEST_RUNNER_HEARTH_FIXTURE_PASSWORD`).
    @MainActor
    func testAHouseholdOnThisMacOpensForAMemberWhoSignsIn() throws {
        let environment = ProcessInfo.processInfo.environment
        guard let household = environment["HEARTH_FIXTURE_DATA_DIR"],
            let password = environment["HEARTH_FIXTURE_PASSWORD"]
        else {
            throw XCTSkip("no fixture household was made for this run")
        }
        let app = XCUIApplication()
        app.launchEnvironment["HEARTH_DATA_DIR"] = household
        app.launch()

        let setup = app.buttons["start.setup"]
        XCTAssertTrue(setup.waitForExistence(timeout: 20), "the start sheet did not offer the setup")
        XCTAssertTrue(app.buttons["start.sample"].exists)
        setup.click()

        let username = app.textFields["signIn.username"]
        XCTAssertTrue(username.waitForExistence(timeout: 60), "no sign-in appeared")
        signIn(app, username: "avery", password: "not the password at all")
        let problem = app.descendants(matching: .any)["signIn.problem"].firstMatch
        XCTAssertTrue(problem.waitForExistence(timeout: 30), "a wrong password was not refused")
        XCTAssertFalse(app.staticTexts["overview.netWorth"].exists)

        signIn(app, username: nil, password: password)
        assertNetWorth(in: app)

        // Opened again, the saved sign-in is handed to the engine without asking.
        app.terminate()
        app.launch()
        assertNetWorth(in: app)

        app.menuBarItems["File"].click()
        app.menuItems["Sign Out"].click()
        XCTAssertTrue(username.waitForExistence(timeout: 20), "signing out did not ask who is signing in")
        XCTAssertFalse(app.staticTexts["overview.netWorth"].exists)
    }

    @MainActor
    private func signIn(_ app: XCUIApplication, username: String?, password: String) {
        if let username {
            let field = app.textFields["signIn.username"]
            field.click()
            field.typeText(username)
        }
        let secret = app.secureTextFields["signIn.password"]
        secret.click()
        secret.typeText(password + "\r")
    }

    /// The household's own net worth: the fixture's, in whole dollars, with no sample banner.
    @MainActor
    private func assertNetWorth(in app: XCUIApplication) {
        let netWorth = app.staticTexts["overview.netWorth"]
        XCTAssertTrue(netWorth.waitForExistence(timeout: 60), "no net worth appeared within a minute")
        XCTAssertEqual(text(of: netWorth), "$21,038")
        XCTAssertFalse(app.staticTexts["sample.banner"].exists || app.otherElements["sample.banner"].exists)
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
