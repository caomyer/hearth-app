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
        assertTransactionsAndAnAccount(in: app)

        // Opened again, the saved sign-in is handed to the engine without asking.
        app.terminate()
        app.launch()
        assertNetWorth(in: app)

        app.menuBarItems["File"].click()
        app.menuItems["Sign Out"].click()
        XCTAssertTrue(username.waitForExistence(timeout: 20), "signing out did not ask who is signing in")
        XCTAssertFalse(app.staticTexts["overview.netWorth"].exists)
    }

    /// Asking the banks is off until the person turns it on, and then the sync control brings
    /// the sample's banks up to date, all of them hearth's stand-in for Plaid, and says when.
    @MainActor
    func testTheSyncControlAsksTheBanksOnceAllowed() {
        let app = XCUIApplication()
        app.launchEnvironment["HEARTH_DATA_DIR"] = "/var/empty/no-hearth-\(UUID().uuidString)"
        app.launch()
        let sample = app.buttons["start.sample"]
        XCTAssertTrue(sample.waitForExistence(timeout: 20), "the start sheet offered no sample")
        sample.click()

        let status = app.buttons["sync.status"]
        XCTAssertTrue(status.waitForExistence(timeout: 60), "the toolbar has no sync control")
        XCTAssertEqual(status.label, "Not asking your banks")
        XCTAssertFalse(app.buttons["sync.now"].exists, "a sync was offered that may not run")

        // Its button leads to Settings, where asking is switched on.
        app.buttons["sync.settings"].click()
        let transactions = app.descendants(matching: .any)["settings.transactions"].firstMatch
        XCTAssertTrue(transactions.waitForExistence(timeout: 20), "Settings did not open on Banks")
        XCTAssertEqual(transactions.value as? Int, 0, "asking was on before anyone turned it on")
        transactions.click()
        app.typeKey("w", modifierFlags: .command)

        // The engine starts again, approved, syncs as it starts, and then may be asked again.
        let now = app.buttons["sync.now"]
        XCTAssertTrue(
            now.waitForExistence(timeout: 90),
            "no way to ask the banks; the sync control says \(status.exists ? status.label : "nothing")")
        now.click()
        let bank = app.descendants(matching: .any)["sync.bank.Northfield Bank"].firstMatch
        XCTAssertTrue(bank.waitForExistence(timeout: 90), "the sync did not say how it went")
        let line = text(of: bank)
        XCTAssertTrue(line.contains("Updated today"), "\(line) does not say the bank was updated today")
        XCTAssertTrue(line.contains("new transaction"), "\(line) says nothing of what arrived")
        XCTAssertTrue(status.label.hasPrefix("Updated today"), "the sync control says \(status.label)")
        let signIn = app.descendants(matching: .any).matching(identifier: "sync.trouble").firstMatch
        XCTAssertTrue(text(of: signIn).contains("Hearth in the browser"), "a bank to sign in to was not said")
    }

    /// A household whose vault key is not in this Mac's Keychain is told so in words, not shown
    /// a sync that seems to work. The fixture sealed its bank's keys under a key it kept in memory
    /// alone, so no Keychain anywhere holds it.
    @MainActor
    func testAHouseholdWhoseKeysTheKeychainCannotGiveIsToldSo() throws {
        let environment = ProcessInfo.processInfo.environment
        guard let household = environment["HEARTH_FIXTURE_DATA_DIR"],
            let password = environment["HEARTH_FIXTURE_PASSWORD"]
        else {
            throw XCTSkip("no fixture household was made for this run")
        }
        let app = XCUIApplication()
        app.launchEnvironment["HEARTH_DATA_DIR"] = household
        app.launchEnvironment["HEARTH_SIGN_IN_PASSWORD"] = password
        app.launchArguments += ["-openBooks", "own", "-signInAs", "avery"]
        app.launch()

        let status = app.buttons["sync.status"]
        XCTAssertTrue(status.waitForExistence(timeout: 60), "the toolbar has no sync control")
        XCTAssertEqual(status.label, "Not asking your banks")
        app.buttons["sync.settings"].click()
        let transactions = app.descendants(matching: .any)["settings.transactions"].firstMatch
        XCTAssertTrue(transactions.waitForExistence(timeout: 20), "Settings did not open on Banks")
        transactions.click()
        app.typeKey("w", modifierFlags: .command)

        let now = app.buttons["sync.now"]
        XCTAssertTrue(now.waitForExistence(timeout: 90), "no way to ask the banks")
        now.click()
        let trouble = app.descendants(matching: .any).matching(identifier: "sync.trouble").firstMatch
        XCTAssertTrue(trouble.waitForExistence(timeout: 90), "what went wrong was not said")
        XCTAssertTrue(text(of: trouble).contains("Keychain"), "\(text(of: trouble)) does not name the Keychain")

        // Left as it was found, for the household's other test.
        app.typeKey(",", modifierFlags: .command)
        XCTAssertTrue(transactions.waitForExistence(timeout: 20))
        transactions.click()
        app.typeKey("w", modifierFlags: .command)
    }

    /// Review, as a member works through it: the Overview says what waits and leads there, a
    /// group that is one decision is accepted in one go and the count falls with the Overview's
    /// banner following, one row is booked against another category, and Undo takes exactly
    /// that back. The fixture's bank sent two market rows Plaid filed as groceries
    /// (`tests/native/on_disk.py`).
    @MainActor
    func testReviewSettlesAGroupAndARowAndTakesTheLastBack() throws {
        let environment = ProcessInfo.processInfo.environment
        guard let household = environment["HEARTH_FIXTURE_DATA_DIR"],
            let password = environment["HEARTH_FIXTURE_PASSWORD"]
        else {
            throw XCTSkip("no fixture household was made for this run")
        }
        let app = XCUIApplication()
        app.launchEnvironment["HEARTH_DATA_DIR"] = household
        app.launchEnvironment["HEARTH_SIGN_IN_PASSWORD"] = password
        app.launchArguments += ["-openBooks", "own", "-signInAs", "avery"]
        app.launch()

        let banner = app.staticTexts["overview.review.count"]
        XCTAssertTrue(banner.waitForExistence(timeout: 60), "the Overview did not say what waits in Review")
        let waiting = try XCTUnwrap(count(in: text(of: banner)), "\(text(of: banner)) is not a count")
        app.buttons["overview.review"].click()

        let remaining = app.staticTexts["review.remaining"]
        XCTAssertTrue(remaining.waitForExistence(timeout: 30), "Review did not open")
        XCTAssertEqual(count(in: text(of: remaining)), waiting)
        let groceries = app.buttons["review.acceptGroup.Food and drink: groceries"]
        XCTAssertTrue(groceries.waitForExistence(timeout: 10), "the group that is one decision has no accept")
        XCTAssertEqual(groceries.label, "Accept 2")
        groceries.click()
        waitFor(remaining, toSay: "\(waiting - 2)")
        let notice = app.staticTexts["review.notice"]
        XCTAssertEqual(text(of: notice), "Accepted 2 as Groceries.")

        app.descendants(matching: .any)["sidebar.overview"].firstMatch.click()
        waitFor(banner, toSay: "\(waiting - 2) transactions to review")

        app.descendants(matching: .any)["sidebar.review"].firstMatch.click()
        XCTAssertTrue(remaining.waitForExistence(timeout: 30))
        let payroll = app.staticTexts.matching(
            NSPredicate(format: "identifier == 'review.payee' AND value == 'Payroll'")
        ).firstMatch
        XCTAssertTrue(payroll.waitForExistence(timeout: 10), "the payroll row is not in the queue")
        payroll.click()
        let other = app.buttons["review.other"]
        XCTAssertTrue(other.waitForExistence(timeout: 20), "the row's detail offers no other category")
        other.click()
        let field = app.descendants(matching: .any)["review.chooser.field"].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10), "choosing another category offered nothing")
        // Return takes the first category the member's own rows use, which is not payroll's.
        field.typeText("\r")
        let decide = app.buttons["review.decide"]
        XCTAssertTrue(decide.label.hasPrefix("Save as "), "\(decide.label) does not book another category")
        decide.click()
        waitFor(remaining, toSay: "\(waiting - 3)")
        XCTAssertTrue(text(of: notice).hasPrefix("Saved under "), "\(text(of: notice)) is not what was decided")

        app.buttons["review.undo"].click()
        waitFor(remaining, toSay: "\(waiting - 2)")
        XCTAssertEqual(text(of: notice), "Taken back. That transaction is waiting for review again.")
        XCTAssertFalse(app.buttons["review.undo"].exists, "an undo was offered to be taken back")
    }

    /// The first whole number in a piece of text.
    private func count(in text: String) -> Int? {
        text.firstMatch(of: /\d[\d,]*/).flatMap { Int($0.output.replacingOccurrences(of: ",", with: "")) }
    }

    /// Waits until `element` says `words`.
    @MainActor
    private func waitFor(_ element: XCUIElement, toSay words: String, timeout: TimeInterval = 30) {
        let said = expectation(
            for: NSPredicate(format: "value == %@ OR label == %@", words, words), evaluatedWith: element)
        wait(for: [said], timeout: timeout)
    }

    /// The start screen is the window's own, so the app quits from it as from anywhere else.
    @MainActor
    func testTheAppQuitsFromTheStartScreen() {
        let app = XCUIApplication()
        app.launchEnvironment["HEARTH_DATA_DIR"] = "/var/empty/no-hearth-\(UUID().uuidString)"
        app.launch()
        XCTAssertTrue(app.buttons["start.sample"].waitForExistence(timeout: 20), "no start screen")

        app.menuBarItems["Hearth"].click()
        app.menuItems["Quit Hearth"].click()

        XCTAssertTrue(app.wait(for: .notRunning, timeout: 20), "the app did not quit from its start screen")
    }

    /// The journal lists the household's rows, narrows to words, and an account opens from
    /// the sidebar with its balance to the cent.
    @MainActor
    private func assertTransactionsAndAnAccount(in app: XCUIApplication) {
        app.descendants(matching: .any)["sidebar.transactions"].firstMatch.click()
        let market = payee("Cobble Street Market", in: app)
        XCTAssertTrue(market.waitForExistence(timeout: 30), "the journal did not list a known row")
        XCTAssertTrue(payee("Quill Bookshop", in: app).exists)

        let search = app.searchFields.firstMatch
        search.click()
        search.typeText("quill")
        XCTAssertTrue(
            app.buttons["Remove \u{201C}quill\u{201D}"].waitForExistence(timeout: 20),
            "the words were not shown as a filter")
        let gone = expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: market)
        wait(for: [gone], timeout: 20)
        XCTAssertTrue(payee("Quill Bookshop", in: app).exists, "narrowing lost the row it was for")

        app.descendants(matching: .any)["sidebar.account.Everyday checking"].firstMatch.click()
        let balance = app.staticTexts["account.balance"]
        XCTAssertTrue(balance.waitForExistence(timeout: 30), "the account did not open")
        XCTAssertEqual(text(of: balance), "$3,250.40")
        let standing = app.descendants(matching: .any)["account.standing"].firstMatch
        XCTAssertTrue(text(of: standing).hasPrefix("Matches bank"), "\(text(of: standing)) is not a match")
        app.descendants(matching: .any)["sidebar.overview"].firstMatch.click()
    }

    /// A row of the journal, by its payee.
    @MainActor
    private func payee(_ name: String, in app: XCUIApplication) -> XCUIElement {
        app.staticTexts.matching(NSPredicate(format: "identifier == 'journal.payee' AND value == %@", name)).firstMatch
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
