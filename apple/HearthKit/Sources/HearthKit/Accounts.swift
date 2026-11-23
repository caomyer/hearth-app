import Foundation

/// Every account the viewer may see, grouped: theirs by kind, then those hearth does not see,
/// then other members' they may see into, by whose they are (`hearth.native.doorway.accounts`).
public struct Accounts: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    public let groups: [Group]

    public struct Group: Decodable, Sendable, Equatable, Identifiable {
        public let name: String
        /// Whether these are the viewer's own accounts, alone or jointly.
        public let yours: Bool
        public let accounts: [Account]

        public var id: String { name }
    }

    /// The account with this id, wherever it is listed.
    public func account(_ id: String) -> Account? {
        groups.lazy.flatMap(\.accounts).first { $0.id == id }
    }
}

/// One account's own page: the account as it is listed, and how far its history reaches.
public struct AccountPage: Screen {
    public let computedAt: Date
    public let asOf: Day
    public let currency: String
    public let recomputing: Bool
    public let account: Account
    /// Each source of its history, and the days it covers. Empty when its transactions are not
    /// the viewer's to see.
    public let history: [Span]

    public struct Span: Decodable, Sendable, Equatable {
        public let origin: String
        public let from: Day
        public let to: Day
    }
}

/// An account the viewer may see, and what its balance rests on.
public struct Account: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let owners: [String]
    /// Whether the viewer is one of its owners.
    public let yours: Bool
    /// "Cash", "Credit" or "Investments"; nil when its balance is not shared with the viewer.
    public let kind: String?
    /// Nil when there is no balance to show: not shared, or one hearth never sees.
    public let balance: Balance?
    public let standing: Standing
    /// Why the balance is not known, told to its owners alone (`hearth.plaid.opening.Unknown`).
    public let why: String?
    /// The last day its bank's balance, or the books' latest check of it, covers.
    public let checked: Day?
    public let failed: Failed?
    /// The last day its data is known through.
    public let updated: Day?
    /// Whether that day is long enough ago to say so.
    public let behind: Bool
    /// The day of the newest price a brokerage account's holdings are valued at.
    public let priced: Day?
    /// Whether the viewer may see its transactions.
    public let transactions: Bool
    /// Its bank, when the viewer holds the connection to it or named it themself.
    public let institution: String?
    public let mask: String?
    /// Whether the viewer's own connection that feeds it has to be signed in to again.
    public let attention: Bool
    /// What kind of account its owner said one hearth does not see is: "Loan".
    public let unseenKind: String?

    public struct Balance: Decodable, Sendable, Equatable {
        /// Nil while it is not known, which is never the same as zero.
        public let value: Money?
        public let isKnown: Bool
        /// True when a holding has no price, so the balance is at least this much.
        public let isFloor: Bool
        public let unpriced: [String]
    }

    public struct Failed: Decodable, Sendable, Equatable {
        /// What the bank, or a statement, says it held.
        public let asserted: Money
        /// What the books say it held.
        public let computed: Money
        public let asOf: Day
    }

    /// What a balance rests on, as far as the viewer may be told. Every absence is its own.
    public enum Standing: String, Decodable, Sendable, CaseIterable {
        /// It agrees with the balance its bank last reported.
        case matches
        /// It is what its brokerage last said the account holds.
        case holdings
        /// It agrees with the books' own latest check of it, such as a statement's.
        case checked
        /// It is what the books hold; nothing checks it against a bank.
        case unchecked
        /// Not known yet: not zero.
        case notKnown = "not_known"
        /// Being brought in now.
        case arriving
        /// Hearth does not see the account, and is never told its balance.
        case unseen
        /// The latest recompute found it disagrees with its bank.
        case failed
        /// Its balance is not shared with the viewer.
        case withheld
    }

    /// The balance, when there is one to write.
    public var known: Money? {
        guard let balance, balance.isKnown else { return nil }
        return balance.value
    }
}
