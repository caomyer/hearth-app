import Foundation

/// How fresh the viewer's own banks are, and where the engine's syncs stand: the `banks`
/// screen (`hearth.native.doorway.banks`). Only the viewer's own connections are ever in it;
/// another member's never are, nor anything they answered.
public struct Banks: Decodable, Sendable, Equatable {
    /// What the engine was started approved to ask, by hearth's names: `read`, `holdings`,
    /// `activity`. Empty when the person allowed nothing.
    public let asking: [String]
    /// Whether the engine is asking the banks right now.
    public let running: Bool
    /// When the last sync this engine ran ended, its recompute included.
    public let finishedAt: Date?
    /// What stopped that sync before it could ask every connection, when something did.
    public let stopped: Trouble?
    public let banks: [Bank]

    public init(
        asking: [String], running: Bool, finishedAt: Date?, stopped: Trouble?, banks: [Bank]
    ) {
        self.asking = asking
        self.running = running
        self.finishedAt = finishedAt
        self.stopped = stopped
        self.banks = banks
    }

    /// When the viewer's banks were last asked and answered in full: the newest of them.
    public var updatedAt: Date? { banks.compactMap(\.syncedAt).max() }
}

/// One of the viewer's own connections.
public struct Bank: Decodable, Sendable, Equatable, Identifiable {
    public let institution: String
    /// When its bank was last asked and answered in full.
    public let syncedAt: Date?
    /// Its bank wants its member to sign in again, which is done in Hearth in the browser.
    public let needsSignIn: Bool
    /// How many new transactions the engine's last sync brought from it; nil when the engine
    /// has not asked it.
    public let added: Int?
    /// What went wrong the last time the engine asked it.
    public let trouble: Trouble?

    public var id: String { institution }

    public init(institution: String, syncedAt: Date?, needsSignIn: Bool, added: Int?, trouble: Trouble?) {
        self.institution = institution
        self.syncedAt = syncedAt
        self.needsSignIn = needsSignIn
        self.added = added
        self.trouble = trouble
    }
}

/// What kept an ask of a bank from bringing anything in (`hearth.connections.Trouble`).
public enum Trouble: String, Decodable, Sendable, CaseIterable {
    /// The bank wants its member to sign in to it again.
    case signIn = "sign_in"
    /// Plaid could not be reached at all.
    case unreachable
    /// The connection's token or its Plaid account's keys could not be opened from the Keychain.
    case keys
    /// Plaid answered with an error of its own.
    case refused
    /// Anything else; `hearth.log` says what.
    case failed
}

/// What the engine says when asked to do something (`hearth.native.bridge.Acted`).
public enum ActOutcome: String, Sendable, CaseIterable {
    case started
    /// It was already being done: a sync was running.
    case running
    /// The engine was started approved for nothing the action asks.
    case notAllowed = "not_allowed"
    /// There is no such action, it was given something it does not take, or what it names is
    /// not there for this viewer. The engine does not say which.
    case missing
    case signedOut = "signed_out"
    /// Every row shown was accepted as suggested.
    case accepted
    /// Saved under the category chosen.
    case booked
    /// Moved to the account of the viewer's own they chose.
    case moved
    /// Not a transaction: it books nothing.
    case excluded
    /// The decision was taken back.
    case undone
    /// Some of what was shown changed since, and was left waiting; the rest, if any, decided.
    case changed
    /// The group is not one kind of transaction, so it is not accepted all at once.
    case notOneDecision = "not_one_decision"
    /// The name typed is not a full account name.
    case notAnAccount = "not_an_account"
    /// A row goes only to a category or one of the viewer's own accounts.
    case notYours = "not_yours"
    /// That account's balance is its bank's, so only the bank's rows go in it.
    case banksAccount = "banks_account"
    /// Its bank counts the row in the balance it reports, so it cannot be left out.
    case banksRow = "banks_row"
    /// The books hold that category, but close it before a row's day or keep it for another
    /// currency, so no row may be booked there.
    case notOpen = "not_open"
    /// An outcome this app does not know yet, from a newer engine: nothing is assumed of it.
    case unknown
}

extension ActOutcome: Decodable {
    /// An outcome this app does not know is read as `unknown`, rather than failing the reply:
    /// the app then says it cannot tell what became of the decision, and shows Review afresh.
    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: raw).flatMap { $0 == .unknown ? nil : $0 } ?? .unknown
    }

    /// Every outcome the engine may send; `unknown` is the app's own.
    public static var told: [ActOutcome] { allCases.filter { $0 != .unknown } }
}
