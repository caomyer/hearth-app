import AppKit
import Foundation
import HearthKit
import os

/// Ways to open a debug build straight onto a screen and a state, so each can be looked at
/// without a click: `-openBooks sample|own`, `-startScreen <screen>` (see `StartScreen`),
/// `-showSync YES`, `-signInProblem <problem>`, `-windowSize <width>x<height>`,
/// `-appearance light|dark`, `-firstReview YES` for a sample none of whose two years is
/// reviewed yet, `-sampleAsOf YYYY-MM-DD` for the sample as it stood that day, and, for a
/// household's own books, `-signInAs <username>` with the password in the environment's
/// `HEARTH_SIGN_IN_PASSWORD`; `-ledgerSheet <step>` with `-ledgerFile <path>` (see
/// `LedgerLaunch`); `-askAboutLedger NO`. A Release build reads none of them.
enum DebugLaunch {
    /// Draws the app light or dark whatever the Mac is set to, so each screen can be looked at
    /// both ways.
    @MainActor
    static func applyAppearance() {
        #if DEBUG
            switch UserDefaults.standard.string(forKey: "appearance") {
            case "dark": NSApplication.shared.appearance = NSAppearance(named: .darkAqua)
            case "light": NSApplication.shared.appearance = NSAppearance(named: .aqua)
            default: break
            }
        #endif
    }

    /// The books to open at once, instead of the ones chosen last time.
    static var books: Books? {
        #if DEBUG
            UserDefaults.standard.string(forKey: "openBooks").flatMap(Books.init(rawValue:))
        #else
            nil
        #endif
    }

    /// Whether the sample is the one a first import leaves: two years, none of it reviewed
    /// (`hearth demo --first-review`), to look at Review the size of a real first import.
    static var firstReview: Bool {
        #if DEBUG
            UserDefaults.standard.bool(forKey: "firstReview")
        #else
            false
        #endif
    }

    /// The day the sample stands on, instead of today (`hearth demo --as-of`), so what waits
    /// in its Review is the same whichever day it is opened, as the UI tests need.
    static var sampleAsOf: Day? {
        #if DEBUG
            UserDefaults.standard.string(forKey: "sampleAsOf").flatMap(Day.init)
        #else
            nil
        #endif
    }

    /// A sign-in to hand the engine instead of the one in the Keychain, which is left alone.
    static var credentials: (any CredentialStore)? {
        #if DEBUG
            guard let username = UserDefaults.standard.string(forKey: "signInAs"),
                let password = ProcessInfo.processInfo.environment["HEARTH_SIGN_IN_PASSWORD"]
            else { return nil }
            return GivenSignIn(Credential(username: username, password: password))
        #else
            nil
        #endif
    }

    /// A ledger file to read in place of the one the open panel would ask for, so its sheets
    /// can be looked at, and tested, without a panel.
    static var ledgerFile: URL? {
        #if DEBUG
            UserDefaults.standard.string(forKey: "ledgerFile").map { URL(fileURLWithPath: $0) }
        #else
            nil
        #endif
    }

    /// Whether an admin is asked once if they keep a ledger; `-askAboutLedger NO` leaves the
    /// question out, as a test about something else does.
    static var asksAboutLedger: Bool {
        #if DEBUG
            // A launch argument's NO arrives as text, which bool(forKey:) reads and a cast does not.
            UserDefaults.standard.object(forKey: "askAboutLedger") == nil
                || UserDefaults.standard.bool(forKey: "askAboutLedger")
        #else
            true
        #endif
    }

    /// Whether the sync control opens how the banks stand once it knows, as a click on it does.
    static var showSync: Bool {
        #if DEBUG
            UserDefaults.standard.bool(forKey: "showSync")
        #else
            false
        #endif
    }

    /// A refusal for the sign-in screen to say, whatever the engine said, so each can be looked
    /// at without being earned: `rejected`, `throttled`, `mustChangePassword` or
    /// `savedSignInFailed`.
    static var signInProblem: Session.SignInProblem? {
        #if DEBUG
            switch UserDefaults.standard.string(forKey: "signInProblem") {
            case "rejected": .rejected
            case "throttled": .throttled
            case "mustChangePassword": .mustChangePassword
            case "savedSignInFailed": .savedSignInFailed
            default: nil
            }
        #else
            nil
        #endif
    }

    /// The size to draw the window at, its frame's, such as `1000x600` for the smallest.
    static var windowSize: CGSize? {
        #if DEBUG
            guard let given = UserDefaults.standard.string(forKey: "windowSize") else { return nil }
            let parts = given.split(separator: "x").compactMap { Double($0) }
            guard parts.count == 2 else { return nil }
            return CGSize(width: parts[0], height: parts[1])
        #else
            nil
        #endif
    }

    /// Puts the window at the size asked for, keeping its top left where it is.
    @MainActor
    static func applyWindowSize() {
        #if DEBUG
            guard let size = windowSize,
                let window = NSApplication.shared.windows.first(where: { $0.styleMask.contains(.resizable) && $0.isVisible })
            else { return }
            // A size asked for is this run's alone: the window's saved frame is left as it was,
            // so the next launch, and the next test, opens as it would have.
            window.setFrameAutosaveName("")
            var frame = window.frame
            frame.origin.y = frame.maxY - size.height
            frame.size = size
            // Wholly on its screen, whatever frame it was restored at: a window hanging off a
            // small screen's edge is not one whose layout can be looked at.
            if let visible = window.screen?.visibleFrame {
                frame.origin.x = max(visible.minX, min(frame.minX, visible.maxX - frame.width))
                frame.origin.y = max(visible.minY, min(frame.minY, visible.maxY - frame.height))
            }
            window.setFrame(frame, display: true)
            // And held there, as a window on a screen too small for it to grow is: content that
            // asks for more than it is given then shows here as it would there, never hidden by
            // a window that quietly grew.
            window.maxSize = window.frame.size
            window.contentMaxSize = window.contentLayoutRect.size
        #endif
    }
}

/// The screen to open the books on, and what to open on it, from `-startScreen`:
///
/// - `overview`, `review`, `transactions`, `holdings`, `accounts`;
/// - `review:where`, Review's "Where did it go?";
/// - `review:row:<payee>`, Review on that row, and `review:choose:<payee>` with its category
///   chooser open;
/// - `transaction:<payee>`, Transactions with that entry open, beside its review when it waits
///   for one;
/// - `transactions:find:<words>`, Transactions narrowed to those words;
/// - `holding:<symbol>`, Holdings with that holding's price beside the list;
/// - `statements`, and `statements:<tab>` on its `summary`, `income`, `balance` or `years` tab;
/// - `account:<name>`, that account's page;
/// - `settings`, the Overview with the Settings window open over it.
///
/// An account, a holding, a row or an entry is found by its name once its screen is in.
enum StartScreen {
    private static var given: String? {
        #if DEBUG
            UserDefaults.standard.string(forKey: "startScreen")
        #else
            nil
        #endif
    }

    /// What follows `prefix` in the start screen asked for, when it starts so.
    private static func named(_ prefix: String) -> String? {
        guard let given, given.hasPrefix(prefix) else { return nil }
        return String(given.dropFirst(prefix.count))
    }

    static var requested: Place? {
        switch given {
        case "overview", "settings": .overview
        case let named? where named.hasPrefix("review"): .review
        case let named? where named.hasPrefix("transaction"): .transactions
        case let named? where named.hasPrefix("holding"): .holdings
        case let named? where named.hasPrefix("statements"): .statements
        case let named? where named.hasPrefix("account"): .accounts
        default: nil
        }
    }

    /// Whether the Settings window opens over the books.
    static var settings: Bool { given == "settings" }

    /// Whether Review opens on "Where did it go?".
    static var elsewhere: Bool { given == "review:where" }

    /// The row Review opens on, by its payee, and whether its category chooser opens with it.
    static var reviewRow: (payee: String, choosing: Bool)? {
        if let payee = named("review:row:") { return (payee, false) }
        if let payee = named("review:choose:") { return (payee, true) }
        return nil
    }

    /// The entry Transactions opens, by its payee.
    static var transaction: String? { named("transaction:") }

    /// The words Transactions is narrowed to.
    static var transactionWords: String? { named("transactions:find:") }

    /// The holding to show the price of once Holdings is in, by its symbol.
    static var holding: String? { named("holding:") }

    /// The account to open once the accounts are known, by its name.
    static var account: String? { named("account:") }

    /// The tab of Statements to open on: `summary`, `income`, `balance` or `years`. Any other
    /// name opens the Summary and says so in the log.
    static var statementsTab: StatementsModel.Tab? {
        guard let tab = named("statements:") else { return nil }
        guard let found = StatementsModel.Tab(rawValue: tab) else {
            let known = StatementsModel.Tab.allCases.map(\.rawValue).joined(separator: ", ")
            let said = "-startScreen statements:\(tab) is no tab of Statements; the tabs are \(known)"
            Logger(subsystem: "dev.hearth.Hearth", category: "launch").error("\(said, privacy: .public)")
            // And on standard error, where whoever launched the build is looking.
            FileHandle.standardError.write(Data((said + "\n").utf8))
            return nil
        }
        return found
    }
}

#if DEBUG
    /// A sign-in given at launch, kept in memory for this run alone.
    private actor GivenSignIn: CredentialStore {
        private var kept: Credential?

        init(_ credential: Credential) {
            kept = credential
        }

        func read(for household: String) async throws -> Credential? { kept }
        func save(_ credential: Credential, for household: String) async throws { kept = credential }
        func forget(for household: String) async throws { kept = nil }
    }
#endif

/// The ledger's sheet to open the books on, from `-ledgerSheet`, each reached through the engine
/// as a person reaches it, with the file `-ledgerFile` names:
///
/// - `question`, "Do you keep a beancount ledger?";
/// - `check`, what the file holds, or why it cannot be brought in;
/// - `confirm`, the confirmation after the check;
/// - `bringingIn`, the import's progress, drawn without starting one;
/// - `refused`, the file's refusal as an import that was refused says it;
/// - `bringIn`, the file checked, confirmed and brought in for real, then matching;
/// - `matching`, matching the accounts connected to the ledger brought in;
/// - `done`, what matching leaves matched.
enum LedgerLaunch {
    enum Sheet: String {
        case question, check, confirm, bringingIn, refused, bringIn, matching, done
    }

    static var requested: Sheet? {
        #if DEBUG
            UserDefaults.standard.string(forKey: "ledgerSheet").flatMap(Sheet.init(rawValue:))
        #else
            nil
        #endif
    }

    @MainActor
    static func open(_ sheet: Sheet, flow: LedgerFlow) async {
        let file = DebugLaunch.ledgerFile ?? URL(fileURLWithPath: "/nowhere/household.beancount")
        switch sheet {
        case .question:
            flow.begin(at: .question)
        case .check:
            await flow.check(file)
        case .confirm:
            await flow.check(file)
            flow.confirm()
        case .bringingIn:
            flow.begin(at: .bringingIn(file))
        case .refused:
            await flow.check(file)
            if case .checked(_, let check) = flow.step {
                flow.begin(at: .notBroughtIn(check.refused))
            }
        case .bringIn:
            await flow.check(file)
            flow.confirm()
            await flow.bringIn()
        case .matching:
            await flow.openMatching()
        case .done:
            await flow.openMatching()
            if case .matching(let found) = flow.step {
                flow.begin(at: .done(found))
            }
        }
    }
}
