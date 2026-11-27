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
        // Asking off is the person's choice, not a warning, and the sample's banks say they are
        // invented, its bank that wants a sign-in among them.
        XCTAssertEqual(status.label, "Invented banks")
        XCTAssertNotEqual(status.value as? String, "Needs you", "a choice the person made was drawn as needing them")
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
        // The Overview counts everything waiting; Review counts the fixture's one transfer
        // apart, under "Where did it go?".
        XCTAssertEqual(count(in: text(of: remaining)), waiting - 1)
        let groceries = app.buttons["review.acceptGroup.Food and drink: groceries"]
        XCTAssertTrue(groceries.waitForExistence(timeout: 10), "the group that is one decision has no accept")
        XCTAssertEqual(groceries.label, "Accept 2")
        groceries.click()
        waitFor(remaining, toSay: "\(waiting - 3)")
        let notice = app.staticTexts["review.notice"]
        XCTAssertEqual(text(of: notice), "Accepted 2 as Groceries.")

        app.descendants(matching: .any)["sidebar.overview"].firstMatch.click()
        waitFor(banner, toSay: "\(waiting - 2) transactions to review")

        app.descendants(matching: .any)["sidebar.review"].firstMatch.click()
        XCTAssertTrue(remaining.waitForExistence(timeout: 30))
        // Plaid gave the bookshop's row no category, so its group is open, row by row.
        let bookshop = app.staticTexts.matching(
            NSPredicate(format: "identifier == 'review.payee' AND value == 'Quill Bookshop'")
        ).firstMatch
        XCTAssertTrue(bookshop.waitForExistence(timeout: 10), "the bookshop's row is not in the queue")
        bookshop.click()
        let other = app.buttons["review.other"]
        XCTAssertTrue(other.waitForExistence(timeout: 20), "the row's detail offers no other category")
        other.click()
        let field = app.descendants(matching: .any)["review.chooser.field"].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10), "choosing another category offered nothing")
        // Return takes the first category the member's own rows use.
        field.typeText("\r")
        let decide = app.buttons["review.decide"]
        XCTAssertTrue(decide.label.hasPrefix("Save as "), "\(decide.label) does not book another category")
        decide.click()
        waitFor(remaining, toSay: "\(waiting - 4)")
        XCTAssertTrue(text(of: notice).hasPrefix("Saved under "), "\(text(of: notice)) is not what was decided")

        app.buttons["review.undo"].click()
        waitFor(remaining, toSay: "\(waiting - 3)")
        XCTAssertEqual(text(of: notice), "Taken back. That transaction is waiting for review again.")
        XCTAssertFalse(app.buttons["review.undo"].exists, "an undo was offered to be taken back")
    }

    /// "Where did it go?", as a member answers it: the transfer the fixture's bank sent to an
    /// account nobody connected is one group, answered as someone else's with a category, which
    /// empties the section, and Undo puts it back as it was, for the household's other tests.
    @MainActor
    func testWhereDidItGoAnswersAGroupAndTakesItBack() throws {
        let environment = ProcessInfo.processInfo.environment
        guard let household = environment["HEARTH_FIXTURE_DATA_DIR"],
            let password = environment["HEARTH_FIXTURE_PASSWORD"]
        else {
            throw XCTSkip("no fixture household was made for this run")
        }
        let app = XCUIApplication()
        app.launchEnvironment["HEARTH_DATA_DIR"] = household
        app.launchEnvironment["HEARTH_SIGN_IN_PASSWORD"] = password
        app.launchArguments += ["-openBooks", "own", "-signInAs", "avery", "-startScreen", "review"]
        app.launch()

        let elsewhere = app.buttons["review.elsewhere"]
        XCTAssertTrue(elsewhere.waitForExistence(timeout: 60), "Review did not say what waits elsewhere")
        XCTAssertEqual(elsewhere.label, "1 more needs to know where the money went")
        elsewhere.click()

        let group = app.descendants(matching: .any)["where.group.Online transfer to SAV 0417"].firstMatch
        XCTAssertTrue(group.waitForExistence(timeout: 30), "the transfer is not a group of its own")
        let save = app.buttons["where.save"]
        XCTAssertEqual(save.label, "Save")
        // Nothing is chosen for the person: the account they would name is not named yet.
        XCTAssertFalse(save.isEnabled, "an answer was chosen for the person")
        app.radioButtons["Someone else"].click()
        let category = app.popUpButtons["where.category"]
        XCTAssertTrue(category.waitForExistence(timeout: 10), "someone else's offered no category")
        XCTAssertEqual(category.value as? String, "Groceries")
        XCTAssertTrue(save.isEnabled, "a category chosen could not be saved")
        save.click()

        let notice = app.staticTexts["review.notice"]
        XCTAssertTrue(notice.waitForExistence(timeout: 30), "what the answer did was not said")
        waitFor(notice, toSay: "Saved under Groceries.")
        // With nothing left there, the screen is the queue again, and says nothing waits elsewhere.
        XCTAssertTrue(app.staticTexts["review.remaining"].waitForExistence(timeout: 30))
        XCTAssertFalse(group.exists)
        XCTAssertFalse(elsewhere.exists, "an answered transfer still waits elsewhere")

        app.buttons["review.undo"].click()
        waitFor(notice, toSay: "Taken back. That transaction is waiting for review again.")
        XCTAssertTrue(elsewhere.waitForExistence(timeout: 30), "the transfer did not come back")
        XCTAssertEqual(elsewhere.label, "1 more needs to know where the money went")
    }

    /// The queue from the keyboard alone, as the Review menu offers it: the arrows move, C
    /// chooses another category by typing it, Return saves it, Command-Z takes it back, Return
    /// accepts a group that is one decision, S skips and X excludes. The sample, as it stood on
    /// `sampleDay`, waits on two rows: Kibble Co, a group one accept settles, and Greenleaf
    /// Market, in the group of guesses the import was unsure of.
    @MainActor
    func testTheQueueIsWorkedFromTheKeyboard() {
        let app = sample(startingOn: "review")

        let remaining = app.staticTexts["review.remaining"]
        XCTAssertTrue(remaining.waitForExistence(timeout: 60), "Review did not open")
        waitFor(remaining, toSay: "2")
        let group = app.staticTexts["review.detail.group"]
        XCTAssertTrue(group.waitForExistence(timeout: 20), "the queue did not open on its first group")
        XCTAssertEqual(text(of: group), "Kibble Co")

        // The queue has the keyboard as it opens.
        app.typeKey(.downArrow, modifierFlags: [])
        waitFor(group, toSay: "Unsure")
        app.typeKey(.downArrow, modifierFlags: [])
        let payee = app.staticTexts["review.detail.payee"]
        XCTAssertTrue(payee.waitForExistence(timeout: 20), "down did not reach the group's row")
        XCTAssertEqual(text(of: payee), "Greenleaf Market")
        app.typeKey(.upArrow, modifierFlags: [])
        waitFor(group, toSay: "Unsure")
        app.typeKey(.downArrow, modifierFlags: [])
        XCTAssertTrue(payee.waitForExistence(timeout: 20))
        let decide = app.buttons["review.decide"]
        XCTAssertTrue(decide.waitForExistence(timeout: 20), "the row offers no decision")
        waitFor(decide, toSay: "Accept as Groceries")

        // C opens the chooser, typing narrows it, and Return takes what is left.
        app.typeKey("c", modifierFlags: [])
        let field = app.descendants(matching: .any)["review.chooser.field"].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10), "C did not offer another category")
        app.typeText("pets")
        XCTAssertTrue(app.buttons["review.option.Pets"].waitForExistence(timeout: 10), "typing lost the category")
        XCTAssertFalse(app.buttons["review.option.Groceries"].exists, "typing narrowed nothing")
        app.typeKey(.return, modifierFlags: [])
        waitFor(decide, toSay: "Save as Pets")
        let closed = expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: field)
        wait(for: [closed], timeout: 10)
        XCTAssertTrue(app.buttons["review.choice.Pets"].exists, "the category chosen is not shown on")

        // Return, back in the queue, saves it; Command-Z takes it back.
        app.typeKey(.return, modifierFlags: [])
        waitFor(remaining, toSay: "1")
        let notice = app.staticTexts["review.notice"]
        waitFor(notice, toSay: "Saved under Pets.")
        app.typeKey("z", modifierFlags: .command)
        waitFor(remaining, toSay: "2")
        waitFor(notice, toSay: "Taken back. That transaction is waiting for review again.")

        // The queue is on the item after the one decided, as it was: Kibble Co's group, which
        // Return accepts whole.
        waitFor(group, toSay: "Kibble Co")
        app.typeKey(.return, modifierFlags: [])
        waitFor(remaining, toSay: "1")
        waitFor(notice, toSay: "Accepted as Pets.")

        // On to the next group; S leaves it waiting and moves on to its row, and X excludes that.
        waitFor(group, toSay: "Unsure")
        app.typeKey("s", modifierFlags: [])
        waitFor(payee, toSay: "Greenleaf Market")
        app.typeKey("x", modifierFlags: [])
        waitFor(notice, toSay: "Marked as not a transaction. It books nothing.")
        XCTAssertTrue(app.staticTexts["review.caughtUp"].waitForExistence(timeout: 30), "nothing said all is done")
        app.typeKey("z", modifierFlags: .command)
        waitFor(notice, toSay: "Taken back. That transaction is waiting for review again.")
        waitFor(remaining, toSay: "1")
    }

    /// Choosing another category with the pointer: Other… opens the chooser, which lists the
    /// categories the member's own rows use (the sample's Avery uses Groceries and Pets) and
    /// offers any other typed in full; a click takes it, and the decision books there.
    @MainActor
    func testAnotherCategoryIsChosenFromTheChooser() {
        let app = sample(startingOn: "review")

        let row = app.staticTexts.matching(
            NSPredicate(format: "identifier == 'review.payee' AND value == 'Greenleaf Market'")
        ).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 60), "the sample's guess is not in the queue")
        row.click()
        let other = app.buttons["review.other"]
        XCTAssertTrue(other.waitForExistence(timeout: 20), "the row's detail offers no other category")
        other.click()
        let field = app.descendants(matching: .any)["review.chooser.field"].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 10), "Other… offered nothing")
        XCTAssertTrue(app.buttons["review.option.Pets"].exists, "a category the member uses is not offered")
        field.typeText("Expenses:Garden")
        let typed = app.buttons["review.option.typed"]
        XCTAssertTrue(typed.waitForExistence(timeout: 10), "a full name typed was not offered")
        XCTAssertFalse(app.buttons["review.option.Pets"].exists, "typing narrowed nothing")
        typed.click()

        let decide = app.buttons["review.decide"]
        waitFor(decide, toSay: "Save as Garden")
        XCTAssertTrue(app.buttons["review.choice.Garden"].exists, "the category chosen is not shown on")
        XCTAssertTrue(app.buttons["review.choice.Groceries"].exists, "the suggestion is no longer offered")
        decide.click()
        waitFor(app.staticTexts["review.notice"], toSay: "Saved under Garden.")
        waitFor(app.staticTexts["review.remaining"], toSay: "1")
    }

    /// Statements opens on the Summary: net worth in whole dollars, why it changed adding up to
    /// the balance sheet, and spending opening the journal onto exactly its rows.
    @MainActor
    func testStatementsSummaryAddsUpAndOpensItsRows() {
        let app = sample(startingOn: "statements")

        let netWorth = app.staticTexts["statements.netWorth"]
        XCTAssertTrue(netWorth.waitForExistence(timeout: 60), "the summary did not open")
        let figure = text(of: netWorth)
        XCTAssertNotNil(figure.wholeMatch(of: /\$\d{1,3}(,\d{3})+/), "\(figure) is not a total in whole dollars")
        let adds = app.descendants(matching: .any)["statements.why.difference"].firstMatch
        XCTAssertTrue(adds.waitForExistence(timeout: 20), "why net worth changed is not shown")
        XCTAssertTrue(
            text(of: adds).contains("Matches the balance sheet") || adds.label.contains("Matches the balance sheet"),
            "\(text(of: adds)) does not say it matches the balance sheet")

        app.buttons["statements.spending"].click()
        XCTAssertTrue(
            app.buttons["Remove Spending, in Statements"].waitForExistence(timeout: 30),
            "the journal was not narrowed to the summary's spending")
    }

    /// The balance sheet lists every account the viewer's statements read, each opening its
    /// page.
    @MainActor
    func testTheBalanceSheetOpensAnAccount() {
        let app = sample(startingOn: "statements:balance")

        let sheet = app.descendants(matching: .any)["statements.sheet"].firstMatch
        XCTAssertTrue(sheet.waitForExistence(timeout: 60), "the balance sheet did not open")
        let checking = sheet.buttons["Everyday checking"].firstMatch
        XCTAssertTrue(checking.waitForExistence(timeout: 60), "the balance sheet did not list the account")
        checking.click()
        let balance = app.staticTexts["account.balance"]
        XCTAssertTrue(balance.waitForExistence(timeout: 30), "the account did not open from the balance sheet")
    }

    /// An unreviewed row of the member's own, opened from Transactions, is decided beside the
    /// journal; what was done and its Undo stay there after the row is no longer unreviewed.
    @MainActor
    func testTransactionsDecidesAnUnreviewedRow() {
        let app = sample(startingOn: "transactions")

        let unreviewed = app.buttons["journal.show.unreviewed"]
        XCTAssertTrue(unreviewed.waitForExistence(timeout: 60), "the journal did not open")
        unreviewed.click()
        let market = app.staticTexts.matching(
            NSPredicate(format: "identifier == 'journal.payee' AND value == 'Greenleaf Market'")
        ).firstMatch
        XCTAssertTrue(market.waitForExistence(timeout: 30), "the unreviewed row is not listed")
        market.click()

        let decide = app.buttons["review.decide"]
        XCTAssertTrue(decide.waitForExistence(timeout: 30), "the row's review did not open beside the journal")
        waitFor(decide, toSay: "Accept as Groceries")
        XCTAssertFalse(app.buttons["review.skip"].exists, "Skip is the queue's, not the journal's")
        decide.click()

        let settled = app.staticTexts["review.detail.settled"]
        XCTAssertTrue(settled.waitForExistence(timeout: 30), "the detail did not say what was decided")
        waitFor(settled, toSay: "Reviewed: booked as Groceries.")
        let notice = app.staticTexts["review.notice"]
        waitFor(notice, toSay: "Accepted as Groceries.")
        // Reviewed now, it leaves the unreviewed journal; its detail and Undo stay.
        let gone = expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: market)
        wait(for: [gone], timeout: 30)
        XCTAssertTrue(notice.exists, "the decision's notice went with the row")

        app.buttons["review.undo"].click()
        waitFor(notice, toSay: "Taken back. That transaction is waiting for review again.")
        waitFor(decide, toSay: "Accept as Groceries")
        XCTAssertTrue(market.waitForExistence(timeout: 30), "the row taken back is not unreviewed again")
    }

    /// Holdings totals what the member may see at market value and at cost; a holding chosen
    /// shows where its price came from and its history, and an account's name opens it.
    @MainActor
    func testHoldingsShowsWhereEachPriceComesFrom() {
        let app = sample(startingOn: "holdings")

        let total = app.staticTexts["holdings.total"]
        XCTAssertTrue(total.waitForExistence(timeout: 60), "Holdings did not open")
        XCTAssertNotNil(
            text(of: total).firstMatch(of: /\$\d{1,3}(,\d{3})+$/), "\(text(of: total)) is not a total in whole dollars")
        XCTAssertTrue(text(of: app.staticTexts["holdings.gain"]).hasSuffix(" gain"))

        // The lots the sample bought, at what they cost, whatever the prices did since.
        app.radioButtons["At cost"].click()
        waitFor(app.staticTexts["holdings.basisLabel"], toSay: "Positions you can see, at cost")
        XCTAssertEqual(text(of: total), "$235,860")
        app.radioButtons["At market value"].click()
        waitFor(app.staticTexts["holdings.basisLabel"], toSay: "Positions you can see")

        app.descendants(matching: .any)["holdings.row.BIDX"].firstMatch.click()
        let source = app.descendants(matching: .any)["price.source"].firstMatch
        XCTAssertTrue(source.waitForExistence(timeout: 30), "the holding's price was not shown beside the list")
        XCTAssertTrue(
            app.descendants(matching: .any)["price.chart"].firstMatch.waitForExistence(timeout: 30),
            "the price's history was not drawn")
        XCTAssertEqual(text(of: app.staticTexts["price.title"]), "Bond Index")

        app.buttons["holdings.account.Joint brokerage"].click()
        let name = app.staticTexts["account.name"]
        XCTAssertTrue(name.waitForExistence(timeout: 30), "the account did not open")
        XCTAssertEqual(text(of: name), "Joint brokerage")
    }

    /// At the smallest window, the queue's rows keep their day and why each is suggested so,
    /// whatever else gives way; and a holding's price opened beside Holdings leaves the sidebar
    /// whole, rather than taking its leading edge.
    @MainActor
    func testTheSmallestWindowCutsNothing() {
        let app = sample(startingOn: "review", size: "1000x560")
        let caption = app.staticTexts.matching(identifier: "review.caption").firstMatch
        XCTAssertTrue(caption.waitForExistence(timeout: 60), "the queue shows no row")
        let said = text(of: caption)
        XCTAssertTrue(said.hasPrefix("Nov\u{00A0}21 · Confident"), "\(said) does not keep its day and reason")
        app.terminate()

        // A Statements tab's table scrolls within itself; it never widens the window.
        let statements = sample(startingOn: "statements:balance", size: "1000x560")
        let sheet = statements.descendants(matching: .any)["statements.sheet"].firstMatch
        XCTAssertTrue(sheet.waitForExistence(timeout: 60), "the balance sheet did not open")
        let shown = statements.windows.firstMatch.frame
        let itsSidebar = statements.outlines.firstMatch.frame
        let sheetFrames = "window \(shown), sidebar \(itsSidebar), the sheet \(sheet.frame)"
        // A table's frame reaches a point past the view that holds it on either side.
        XCTAssertGreaterThanOrEqual(itsSidebar.minX, shown.minX - 1, "the sidebar was cut: \(sheetFrames)")
        XCTAssertLessThanOrEqual(sheet.frame.maxX, shown.maxX + 1, "the sheet runs past the window: \(sheetFrames)")
        statements.terminate()

        let holdings = sample(startingOn: "holding:BIDX", size: "1000x560")
        let source = holdings.descendants(matching: .any)["price.source"].firstMatch
        XCTAssertTrue(source.waitForExistence(timeout: 60), "the holding's price was not shown beside the list")
        // Every frame here is in the screen's coordinates, as the accessibility tree gives them.
        let window = holdings.windows.firstMatch.frame
        let sidebar = holdings.outlines.firstMatch.frame
        let overview = holdings.descendants(matching: .any)["sidebar.overview"].firstMatch.frame
        let panel = holdings.staticTexts["price.title"].frame
        let total = holdings.staticTexts["holdings.total"].frame
        let screen = XCUIScreen.main.screenshot().image.size
        let banner = holdings.descendants(matching: .any)["sample.banner"].firstMatch.frame
        let frames =
            "screen \(screen), window \(window), sidebar \(sidebar), its Overview \(overview), "
            + "the banner \(banner), the list's total \(total), the panel's title \(panel)"
        if sidebar.minX < window.minX - 1 || panel.maxX > window.maxX {
            // Every element and its frame, to say which one is wider than the window.
            print(holdings.windows.firstMatch.debugDescription)
        }
        XCTAssertTrue(
            window.minX >= 0 && window.maxX <= screen.width, "the window is not wholly on its screen: \(frames)")
        XCTAssertGreaterThanOrEqual(sidebar.minX, window.minX - 1, "the sidebar was cut at its leading edge: \(frames)")
        XCTAssertGreaterThan(overview.minX, sidebar.minX, "the sidebar's rows were cut: \(frames)")
        XCTAssertGreaterThan(total.minX, overview.maxX, "the list is under the sidebar: \(frames)")
        XCTAssertGreaterThan(panel.minX, total.minX, "the panel is not beside the list: \(frames)")
        XCTAssertLessThanOrEqual(panel.maxX, window.maxX, "the panel runs past the window: \(frames)")
    }

    /// The day the sample stands on for the tests that work its Review. The sample is drawn
    /// back from the day it is opened, so what waits in it changes from day to day; on this day
    /// Avery's queue is Kibble Co, a group of one, and Greenleaf Market, a guess.
    private let sampleDay = "2026-11-22"

    /// The sample as it stood on `sampleDay`, opened straight onto a screen, in a data
    /// directory of no household's.
    @MainActor
    private func sample(startingOn screen: String, size: String? = nil) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["HEARTH_DATA_DIR"] = "/var/empty/no-hearth-\(UUID().uuidString)"
        app.launchArguments += ["-openBooks", "sample", "-sampleAsOf", sampleDay, "-startScreen", screen]
        if let size { app.launchArguments += ["-windowSize", size] }
        app.launch()
        return app
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
