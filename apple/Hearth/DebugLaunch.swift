import AppKit
import Foundation
import HearthKit

/// Ways to open a debug build straight onto a screen, so each can be looked at without a
/// click: `-openBooks sample|own`,
/// `-startScreen overview|review|transactions|accounts|account:<name>`, `-appearance light|dark`,
/// `-firstReview YES` for a sample none of whose two years is reviewed yet, and, for a
/// household's own books, `-signInAs <username>` with the password in the environment's
/// `HEARTH_SIGN_IN_PASSWORD`. A Release build reads none of them.
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
}

/// The screen to open the books on.
enum StartScreen {
    static var requested: Place? {
        #if DEBUG
            switch UserDefaults.standard.string(forKey: "startScreen") {
            case "overview": .overview
            case "review": .review
            case "transactions": .transactions
            case "accounts": .accounts
            case let named? where named.hasPrefix("account:"): .accounts
            default: nil
            }
        #else
            nil
        #endif
    }

    /// The account to open once the accounts are known, by its name.
    static var account: String? {
        #if DEBUG
            guard let named = UserDefaults.standard.string(forKey: "startScreen"), named.hasPrefix("account:")
            else { return nil }
            return String(named.dropFirst("account:".count))
        #else
            nil
        #endif
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
