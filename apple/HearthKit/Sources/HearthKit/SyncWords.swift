import Foundation

/// How the sync control says how fresh the banks are and how the last sync went.
extension SyncModel {
    /// The control's one line: "Updated today, 6:02 AM", "Updating…", "Not asking your banks".
    public func label(now: Date = .now, calendar: Calendar = .current, locale: Locale = .current) -> String {
        guard let banks else { return "" }
        if isSyncing { return "Updating\u{2026}" }
        if !mayAsk { return "Not asking your banks" }
        return banks.freshness(now: now, calendar: calendar, locale: locale)
    }

    /// Whether something needs the person: a bank to sign in to again, or a sync that went wrong.
    public var needsAttention: Bool {
        !(banks?.troubles.isEmpty ?? true)
    }
}

extension Banks {
    /// When the viewer's banks were last brought up to date, as the control says it.
    public func freshness(now: Date = .now, calendar: Calendar = .current, locale: Locale = .current) -> String {
        if banks.isEmpty { return "No banks connected" }
        guard let updatedAt else { return "Not updated yet" }
        return "Updated \(Figures.moment(updatedAt, now: now, calendar: calendar, locale: locale))"
    }

    /// What went wrong, each said once, in words that say what to do: a bank to sign in to
    /// again first, as only the person can put that right.
    public var troubles: [String] {
        var said: [String] = []
        func say(_ sentence: String) {
            if !said.contains(sentence) { said.append(sentence) }
        }
        for bank in banks where bank.needsSignIn || bank.trouble == .signIn {
            say("\(bank.institution) needs you to sign in again. Do that in Hearth in the browser.")
        }
        if let stopped {
            say(Self.sentence(for: stopped, at: nil))
        }
        for bank in banks {
            if let trouble = bank.trouble, trouble != .signIn {
                say(Self.sentence(for: trouble, at: bank.institution))
            }
        }
        return said
    }

    /// What the engine's last sync brought, once it ran one: "3 new transactions".
    public var arrived: String? {
        guard finishedAt != nil else { return nil }
        let added = banks.compactMap(\.added).reduce(0, +)
        return Self.count(added)
    }

    /// "No new transactions", "1 new transaction", "12 new transactions".
    public static func count(_ added: Int) -> String {
        switch added {
        case 0: "No new transactions"
        case 1: "1 new transaction"
        default: "\(added.formatted()) new transactions"
        }
    }

    private static func sentence(for trouble: Trouble, at institution: String?) -> String {
        switch trouble {
        case .signIn:
            "\(institution ?? "A bank") needs you to sign in again. Do that in Hearth in the browser."
        case .unreachable:
            "Plaid could not be reached. Hearth tries again in the morning."
        case .keys:
            "Hearth could not open its Plaid keys in this Mac's Keychain."
        case .refused:
            if let institution {
                "Plaid could not update \(institution). Hearth's log says why."
            } else {
                "Plaid refused the update. Hearth's log says why."
            }
        case .failed:
            if let institution {
                "\(institution) could not be updated. Hearth's log says why."
            } else {
                "The update stopped before it finished. Hearth's log says why."
            }
        }
    }
}

extension Bank {
    /// One bank's line: when it was last updated, and what the engine's last sync brought.
    public func freshness(now: Date = .now, calendar: Calendar = .current, locale: Locale = .current) -> String {
        guard let syncedAt else { return "Not updated yet" }
        return "Updated \(Figures.moment(syncedAt, now: now, calendar: calendar, locale: locale))"
    }
}
