import Foundation

/// How bringing in a ledger, and matching accounts to it, is said: every sentence from what
/// the engine answered, counted and dated by the engine, and only written here.
public enum LedgerWords {
    // Asked once, after signing in.

    public static let question = "Do you keep a beancount ledger?"
    public static let yes = "Yes, bring it in"
    public static let yesDetail = "Choose its file next. Hearth keeps a copy and reads only that."
    public static let no = "No, use Hearth's own books"
    public static let noDetail = "Built from your connected accounts."

    // The file's check.

    public static let checking = "Reading your ledger"

    /// "household.beancount is ready to bring in", or that it cannot be.
    public static func checkTitle(file: String, _ check: LedgerCheck) -> String {
        check.refused == nil ? "\(file) is ready to bring in" : "\(file) cannot be brought in"
    }

    /// "9,412 entries · 64 accounts · Mar 2019 to Jul 2026".
    public static func summary(_ found: LedgerCheck.Found, locale: Locale = .current) -> String {
        var parts = [
            counted(found.entries, "entry", "entries", locale: locale),
            counted(found.accounts.count, "account", "accounts", locale: locale),
        ]
        if let first = found.first, let last = found.last {
            let from = Figures.month(first, locale: locale)
            let to = Figures.month(last, locale: locale)
            parts.append(from == to ? from : "\(from) to \(to)")
        }
        return parts.joined(separator: " \u{00B7} ")
    }

    /// "311 balance checks pass", or how many fail.
    public static func balanceChecks(_ found: LedgerCheck.Found, locale: Locale = .current) -> String {
        let checks = found.assertions
        if checks.count == 0 { return "No balance checks in it" }
        if checks.failing > 0 {
            let of = counted(checks.count, "balance check", "balance checks", locale: locale)
            return "\(number(checks.failing, locale: locale)) of \(of) \(checks.failing == 1 ? "fails" : "fail")"
        }
        return checks.count == 1
            ? "1 balance check passes"
            : "\(counted(checks.count, "balance check", "balance checks", locale: locale)) pass"
    }

    /// Whether every balance check in it passes.
    public static func checksPass(_ found: LedgerCheck.Found) -> Bool { found.assertions.failing == 0 }

    /// "Net worth today: $846,684, at the ledger's own prices".
    public static func worth(_ found: LedgerCheck.Found, locale: Locale = .current) -> String {
        let worth = found.worth
        let total = Figures.total(worth.value, currency: worth.currency, locale: locale)
        if worth.isFloor {
            return "Net worth today: at least \(total), at the ledger's own prices, without what it has no price for"
        }
        return "Net worth today: \(total), at the ledger's own prices"
    }

    /// "5 lines not used by Hearth", or nil when every line is used.
    public static func unusedTitle(_ found: LedgerCheck.Found, locale: Locale = .current) -> String? {
        guard !found.unused.isEmpty else { return nil }
        return "\(counted(found.unused.count, "line", "lines", locale: locale)) not used by Hearth"
    }

    /// "3 plugins and 2 custom directives. Hearth keeps them in its copy and never runs a
    /// plugin that is not beancount's own."
    public static func unusedDetail(_ found: LedgerCheck.Found, locale: Locale = .current) -> String {
        let kinds = found.unusedKinds
        var parts: [String] = []
        if kinds.plugins > 0 { parts.append(counted(kinds.plugins, "plugin", "plugins", locale: locale)) }
        if kinds.custom > 0 {
            parts.append(counted(kinds.custom, "custom directive", "custom directives", locale: locale))
        }
        if kinds.documents > 0 { parts.append(counted(kinds.documents, "document", "documents", locale: locale)) }
        let listed = list(parts)
        let kept =
            kinds.plugins > 0
            ? "They are kept in Hearth's copy, and a plugin that is not beancount's own never runs."
            : "They are kept in Hearth's copy as they are."
        return listed.isEmpty ? kept : "\(listed.prefix(1).uppercased())\(listed.dropFirst()). \(kept)"
    }

    /// "12 under Assets, 3 under Liabilities, 1 under Equity, 18 under Income and 30 under
    /// Expenses".
    public static func kinds(_ found: LedgerCheck.Found, locale: Locale = .current) -> String {
        list(found.kinds.map { "\(number($0.count, locale: locale)) under \($0.kind)" })
    }

    // Confirming.

    public static func confirmTitle(file: String) -> String { "Bring in \(file)?" }

    public static let confirmDetail =
        "Hearth keeps a copy of the ledger and the files it includes, and reads only that copy from now on."
    public static let once =
        "This happens once. What you change in the file afterwards is not brought in, and Hearth's books are the truth from then on."
    public static let bringIn = "Bring In"

    // Bringing it in.

    public static let bringingIn = "Bringing in your ledger"
    public static let bringingInDetail = "Hearth is copying it and working out your books. This can take a minute."
    public static let notBroughtIn = "Your ledger was not brought in"
    public static let failed =
        "Something went wrong while bringing it in, so nothing was kept. Hearth's log says what."
    public static let booksFailed =
        "Hearth could not work out your books from it yet. The Overview says why. Match your accounts in Settings, Ledger, once it can."

    // Matching.

    public static let matchTitle = "Match your accounts"
    public static let matchDetail =
        "Say which of your ledger's accounts each connected account is. Your ledger's transactions are kept up to the day shown, and Hearth adds its bank's from then on."
    public static let leaveAsItIs = "Leave as it is"
    public static let notNow = "Not Now"
    public static let match = "Match"

    /// "Checking ···4417".
    public static func name(_ account: ConnectedAccount) -> String {
        guard let mask = account.mask, !mask.isEmpty else { return account.name }
        return "\(account.name) \u{00B7}\u{00B7}\u{00B7}\(mask)"
    }

    /// When the bank's transactions are added from on a choice: "from Nov 13".
    public static func from(_ facing: Facing, asOf: Day? = nil, locale: Locale = .current) -> String? {
        guard let cutOver = facing.cutOver else { return nil }
        let day = Figures.day(cutOver, asOf: asOf, locale: locale)
        if !facing.carried {
            return "history ends where your ledger's does"
        }
        if facing.postponed { return "from \(day), after your ledger's last entry" }
        return "from \(day)"
    }

    /// What carrying it on meets: "312 already in your ledger, 4 to add from Nov 13. 1 earlier
    /// one is not in your ledger, and is not brought in."
    public static func meets(_ facing: Facing, asOf: Day? = nil, locale: Locale = .current) -> String {
        let held = facing.inLedger.count
        var said = "\(number(held, locale: locale)) already in your ledger"
        if facing.carried, !facing.postponed {
            let booked = facing.booked.count
            said += booked == 0 ? ", nothing new to add yet" : ", \(number(booked, locale: locale)) to add"
        }
        said += "."
        let before = facing.before.count
        if before > 0, facing.carried {
            said +=
                before == 1
                ? " 1 earlier one is not in your ledger, and is not brought in."
                : " \(number(before, locale: locale)) earlier ones are not in your ledger, and are not brought in."
        }
        return said
    }

    /// What matching it to a choice means, in a line: "From Nov 13. 312 already in your
    /// ledger, 4 to add."
    public static func note(_ facing: Facing, asOf: Day? = nil, locale: Locale = .current) -> String {
        guard let from = from(facing, asOf: asOf, locale: locale) else { return meets(facing, asOf: asOf, locale: locale) }
        return "\(from.prefix(1).uppercased())\(from.dropFirst()). \(meets(facing, asOf: asOf, locale: locale))"
    }

    /// Why nothing is proposed for an account (`ConnectedAccount.whyNot`).
    public static func whyNot(_ account: ConnectedAccount) -> String? {
        switch account.whyNot {
        case nil: return nil
        case "nothing_sent": return "Its bank has sent no transactions for it yet, so there is nothing to match by."
        case "ids_disagree": return "Your ledger holds its transactions on more than one account."
        case "unplaced": return "Your ledger holds its transactions, but not which side of each is the bank's."
        case "no_side", "wrong_side": return "Hearth cannot tell which of your ledger's accounts it is."
        case "unclear": return "No account of your ledger clearly holds its transactions."
        case "taken": return "The account your ledger holds its transactions on is matched to another already."
        default: return "Hearth cannot tell which of your ledger's accounts it is."
        }
    }

    /// Why a choice would be refused (`Choice.refused`).
    public static func refused(_ refused: Choice.Refused, locale: Locale = .current) -> String {
        switch refused.why {
        case "fixed":
            return "A decision has been made in Review on its transactions, so its match is fixed."
        case "taken": return "Another connected account is matched to it already."
        case "closed": return "Your ledger closes it, so nothing can be added to it."
        case "moved_into": return "Transactions are moved into it in Review, so no bank can feed it."
        case "unclaimed": return "Nobody has said whose it is yet."
        case "named": return "It is an account Hearth does not see, which no bank feeds."
        case "not_yours": return "It is not yours."
        case "not_carried": return "Hearth does not carry an account of its kind on from a ledger yet."
        case "ruled_onto": return "Transactions were moved into the account Hearth keeps it as, in Review."
        case "other_owners": return "It belongs to other people than the account Hearth keeps it as."
        case "would_lose":
            let count = refused.count ?? 0
            let rows = count == 1 ? "1 transaction" : "\(number(count, locale: locale)) transactions"
            let day = refused.day.map { " from before \(Figures.day($0, locale: locale))" } ?? ""
            return "Your ledger does not hold \(rows)\(day) that Hearth has, so matching it would take them out of your books."
        case "unsure":
            return "Hearth cannot tell yet whether a decision rests on its match, so it is not changed."
        case "booking":
            return "Hearth cannot tell yet which of its bank's transactions it already has, so it cannot be matched now."
        case "unrecorded":
            return "Nobody has said whose it is, and your ledger does not show that it is this account."
        default: return "Hearth cannot match it to that account."
        }
    }

    /// What cannot be done here, said once whenever some account has nothing to match to:
    /// matching offers only the ledger's accounts that are the member's own, and an account
    /// becomes someone's only on the command line for now.
    public static func cannotMatch(_ matching: Matching) -> String? {
        let stuck = matching.accounts.contains { $0.state != .matched && LedgerFlow.open($0).isEmpty }
        guard stuck else { return nil }
        return "An account here can be matched only to one of your ledger's accounts that is yours. Until someone says whose your ledger's accounts are, with hearth add-account on this Mac, those with nothing to choose stay as they are. Match them later in Settings, Ledger."
    }

    public static let waitingForAnother = "A ledger is being brought in"
    public static let waitingForAnotherDetail =
        "One is being brought in on this Mac already. Hearth waits for it to finish, then shows what it brought in."
    public static let inTitle = "Your ledger is in"
    public static let changedSince =
        "Something changed since these were shown, so none was matched. Here is how they stand now."

    // Done.

    /// "Your accounts are matched", or that the ledger is in when nothing is connected.
    public static func doneTitle(_ matching: Matching) -> String {
        if matching.accounts.isEmpty { return "Your ledger is in" }
        return matching.totals.matched == 0 ? "Your ledger is in" : "Your accounts are matched"
    }

    /// "5 of 5 accounts matched".
    public static func matchedCount(_ matching: Matching, locale: Locale = .current) -> String {
        let totals = matching.totals
        return "\(number(totals.matched, locale: locale)) of \(counted(totals.of, "account", "accounts", locale: locale)) matched"
    }

    /// "Checking ···4417 is Assets:Harbor:Checking, and 4 more."
    public static func matchedExample(_ matching: Matching, locale: Locale = .current) -> String? {
        let matched = matching.accounts.filter { $0.matched != nil }
        guard let first = matched.first, let account = first.matched?.account else { return nil }
        let more = matched.count - 1
        return more == 0
            ? "\(name(first)) is \(account)."
            : "\(name(first)) is \(account), and \(number(more, locale: locale)) more."
    }

    /// Every match, for Show: "Checking ···4417 is Assets:Harbor:Checking".
    public static func matchedList(_ matching: Matching) -> [String] {
        matching.accounts.compactMap { one in one.matched.map { "\(name(one)) is \($0.account)" } }
    }

    /// "3,898 transactions already in your ledger".
    public static func inLedger(_ matching: Matching, locale: Locale = .current) -> String {
        "\(counted(matching.totals.inLedger, "transaction", "transactions", locale: locale)) already in your ledger"
    }

    /// "14 from then on are added by Hearth."
    public static func added(_ matching: Matching, locale: Locale = .current) -> String {
        let booked = matching.totals.booked
        if booked == 0 { return "Hearth adds what your banks send from then on." }
        return booked == 1
            ? "1 from then on is added by Hearth."
            : "\(number(booked, locale: locale)) from then on are added by Hearth."
    }

    /// What nobody sees yet: "12 of your ledger's accounts are nobody's yet, so nobody sees them."
    public static func unclaimed(_ standing: LedgerStanding?, locale: Locale = .current) -> String? {
        guard let count = standing?.imported?.unclaimed, count > 0 else { return nil }
        let accounts = count == 1 ? "1 of your books' accounts is" : "\(number(count, locale: locale)) of your books' accounts are"
        return "\(accounts) nobody's yet, so nobody sees \(count == 1 ? "it" : "them"). Say whose each is with hearth add-account on this Mac."
    }

    public static let change = "Change"
    public static let openBooks = "Open My Books"

    // Settings.

    /// "Brought in on Nov 22, 2026, 5 files."
    public static func standing(_ standing: LedgerStanding, locale: Locale = .current) -> String {
        if let imported = standing.imported {
            let day = imported.on.date(in: Day.calendar).formatted(
                Date.FormatStyle(locale: locale).month(.abbreviated).day().year().utc)
            return "Your ledger was brought in on \(day), \(counted(imported.files, "file", "files", locale: locale))."
        }
        if standing.inherited { return "Hearth reads your ledger where it is." }
        if standing.importing { return "A ledger is being brought in." }
        return "No ledger is brought in. Hearth keeps your books from your connected accounts."
    }

    // Counting.

    static func number(_ count: Int, locale: Locale) -> String {
        count.formatted(.number.grouping(.automatic).locale(locale))
    }

    static func counted(_ count: Int, _ one: String, _ many: String, locale: Locale) -> String {
        "\(number(count, locale: locale)) \(count == 1 ? one : many)"
    }

    /// "a, b and c".
    static func list(_ parts: [String]) -> String {
        switch parts.count {
        case 0: return ""
        case 1: return parts[0]
        default: return parts.dropLast().joined(separator: ", ") + " and " + parts[parts.count - 1]
        }
    }
}
