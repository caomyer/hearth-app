import Foundation

/// Where the household's own ledger stands: the `ledger` screen (`hearth.native.ledger`).
public struct LedgerStanding: Decodable, Sendable, Equatable {
    /// The ledger brought in, if one was.
    public struct Imported: Decodable, Sendable, Equatable {
        /// The day it was brought in.
        public let on: Day
        /// How many files Hearth's copy holds.
        public let files: Int
        /// How many of the books' accounts nobody has said are whose, so nobody sees: told to
        /// whoever may bring a ledger in, nil to anyone else.
        public let unclaimed: Int?
    }

    /// The import the engine last ran for this member, if it ran one.
    public struct BringingIn: Decodable, Sendable, Equatable {
        public let running: Bool
        /// `imported`, `unchanged`, `refused` or `failed`; nil while it runs.
        public let outcome: Outcome?
        /// Why it was refused.
        public let refused: LedgerRefusal?
        public let finishedAt: Date?
        /// Whether the books could not be worked out after it, so they do not hold it yet.
        public let booksFailed: Bool
    }

    /// How an import ended (`hearth.disclosure.ledger.BroughtIn`).
    public enum Outcome: String, Sendable, Equatable {
        case imported
        /// The ledger was Hearth's copy already, byte for byte.
        case unchanged
        case refused
        case failed
        /// An outcome this app does not know yet.
        case unknown
    }

    public let imported: Imported?
    /// Whether the engine reads a ledger where it is (`HEARTH_LEDGER`).
    public let inherited: Bool
    /// Whether the engine is bringing a ledger in now, whoever started it: told only to a
    /// member who may bring one in, and never whose it is.
    public let importing: Bool
    /// Whether this member may bring a ledger in now.
    public let mayBringIn: Bool
    /// Why not, to a member who could but for where the household stands.
    public let whyNot: LedgerRefusal?
    public let bringingIn: BringingIn?
}

extension LedgerStanding.Outcome: Decodable {
    public init(from decoder: any Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: raw).flatMap { $0 == .unknown ? nil : $0 } ?? .unknown
    }
}

/// Why a ledger cannot be brought in: the engine's word for it, and a sentence in the app's
/// words, which names no path, command or setting (`hearth.ledger_import.Refusal`).
public struct LedgerRefusal: Decodable, Sendable, Equatable {
    public let why: String
    public let said: String

    public init(why: String, said: String) {
        self.why = why
        self.said = said
    }
}

/// What a ledger file holds, and why it would not be brought in: the `ledger_check` screen.
public struct LedgerCheck: Decodable, Sendable, Equatable {
    /// What the file holds; nil where it does not load, or the household may not bring a ledger
    /// in at all.
    public let found: Found?
    /// Why it would not be brought in; nil when it would.
    public let refused: LedgerRefusal?

    public struct Found: Decodable, Sendable, Equatable {
        public let files: Int
        public let entries: Int
        public let first: Day?
        public let last: Day?
        public let accounts: [Opened]
        /// How many accounts of each kind, in the order a balance sheet reads them.
        public let kinds: [Kind]
        public let assertions: Assertions
        /// The ledger's operating currencies.
        public let currencies: [String]
        /// Everything else it holds or prices.
        public let commodities: [String]
        public let worth: Worth
        /// Beancount's own plugins it runs.
        public let runs: [String]
        /// Every line Hearth keeps but does not use.
        public let unused: [Unused]
        public let unusedKinds: UnusedKinds
    }

    public struct Opened: Decodable, Sendable, Equatable, Identifiable {
        public let name: String
        public let kind: String
        public let opened: Day
        public let closed: Day?
        public var id: String { name }
    }

    public struct Kind: Decodable, Sendable, Equatable {
        public let kind: String
        public let count: Int
    }

    public struct Assertions: Decodable, Sendable, Equatable {
        public let count: Int
        public let failing: Int
    }

    public struct Worth: Decodable, Sendable, Equatable {
        public let value: Money
        public let currency: String
        public let asOf: Day
        /// It leaves out what the ledger has no price for, so it is at least what it says.
        public let isFloor: Bool
        public let unpriced: [Unpriced]
    }

    public struct Unpriced: Decodable, Sendable, Equatable {
        public let commodity: String
        public let account: String
    }

    public struct Unused: Decodable, Sendable, Equatable, Identifiable {
        public let file: String
        public let line: Int
        public let what: String
        public var id: String { "\(file):\(line)" }
    }

    public struct UnusedKinds: Decodable, Sendable, Equatable {
        /// Plugins that are not beancount's own, or are in an included file, and the
        /// `documents` option.
        public let plugins: Int
        public let custom: Int
        public let documents: Int
    }
}

/// Each of the member's own connected accounts, and which of the imported ledger's accounts it
/// is: the `matching` screen.
public struct Matching: Decodable, Sendable, Equatable {
    /// Whether there is an imported ledger to match to.
    public let imported: Bool
    /// Why Hearth cannot tell whether carrying an account on has begun, while it cannot.
    public let unsure: String?
    public let accounts: [ConnectedAccount]
    public let totals: Totals

    public struct Totals: Decodable, Sendable, Equatable {
        public let matched: Int
        public let of: Int
        /// How many of the banks' transactions the ledger holds already, on matched accounts.
        public let inLedger: Int
        /// How many Hearth adds from each matched account's cut-over on.
        public let booked: Int
    }
}

/// One of the member's connected accounts, as matching sees it.
public struct ConnectedAccount: Decodable, Sendable, Equatable, Identifiable {
    public enum State: String, Decodable, Sendable, Equatable {
        case matched
        /// Filed as an account Hearth keeps itself.
        case kept
        case unmatched
    }

    public struct Matched: Decodable, Sendable, Equatable {
        public let account: String
        public let on: Day?
    }

    public struct Proposed: Decodable, Sendable, Equatable {
        public let account: String
        /// `id`, by the bank's own transaction ids the ledger holds, or `content`, by its
        /// transactions alone.
        public let how: String
        public let agreeing: Int
        public let elsewhere: Int
        public let of: Int
    }

    /// Plaid's id for the account, which the engine matches it by.
    public let id: String
    public let institution: String
    public let name: String
    public let mask: String?
    public let kind: String
    public let state: State
    public let matched: Matched?
    /// A decision has been made in Review on its transactions, so its match is fixed.
    public let fixed: Bool
    /// The account Hearth keeps it as, when it keeps it itself.
    public let kept: String?
    public let proposed: Proposed?
    /// Why nothing is proposed, by the engine's word for it (`hearth.matching.Unproposed`).
    public let whyNot: String?
    public let choices: [Choice]
    /// What carrying it on meets where it is matched now.
    public let facing: Facing?
    /// Its bank's balance meets the books only by a difference that waits in Review.
    public let reconciling: Bool
}

/// One of the ledger's accounts a connected account may be matched to.
public struct Choice: Decodable, Sendable, Equatable, Identifiable {
    public struct Refused: Decodable, Sendable, Equatable {
        /// The engine's word for why (`hearth.matching.Refusal`).
        public let why: String
        public let count: Int?
        public let day: Day?
    }

    public let account: String
    public let proposed: Bool
    public let facing: Facing?
    public let refused: Refused?
    public var id: String { account }
}

/// What carrying a connected account on from one of the ledger's accounts meets.
public struct Facing: Decodable, Sendable, Equatable {
    public struct Stretch: Decodable, Sendable, Equatable {
        public let count: Int
        public let from: Day?
        public let to: Day?
    }

    public struct InLedger: Decodable, Sendable, Equatable {
        public let count: Int
        public let byId: Int
        public let byContent: Int
    }

    public let account: String
    /// The first day Hearth adds the bank's transactions on; before it, the ledger's.
    public let cutOver: Day?
    /// Whether an account of its kind is carried on at all; a brokerage is not yet.
    public let carried: Bool
    /// The ledger has an entry after today, so carrying it on waits until then.
    public let postponed: Bool
    /// How many posted transactions its bank has sent.
    public let rows: Int
    public let inLedger: InLedger
    /// Its bank's transactions before the cut-over the ledger does not hold: never brought in.
    public let before: Stretch
    /// Its transactions from the cut-over on the ledger does not hold: what Hearth adds.
    public let booked: Stretch
    public let lastEntry: Day?
    public let lastBalance: Day?
    public let lastPad: Day?
}
