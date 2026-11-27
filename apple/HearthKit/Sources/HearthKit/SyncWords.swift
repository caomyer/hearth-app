import Foundation

/// How the sync control says how fresh the banks are and how the last sync went.
///
/// The control's line is the one thing the person most needs to know: a sync running, then
/// what needs them, then that asking is off, a choice they made and said plainly, then when
/// the banks were last updated. Only what needs the person is a warning.
///
/// The sample's banks are invented: asking them is said as such, and its bank that wants a
/// sign-in shows what that looks like without asking anything of anyone.
extension SyncModel {
    /// The control's one line: "Updating…", "Cedar Card needs you", "Not asking your banks",
    /// "Updated today, 6:02 AM".
    public func label(
        sample: Bool = false, now: Date = .now, calendar: Calendar = .current, locale: Locale = .current
    ) -> String {
        guard let banks else { return "" }
        if isSyncing { return "Updating\u{2026}" }
        if let headline = banks.headline(sample: sample) { return headline }
        if !mayAsk { return sample ? "Invented banks" : "Not asking your banks" }
        return banks.freshness(now: now, calendar: calendar, locale: locale)
    }

    /// Whether something needs the person: a bank to sign in to again, or a sync that went wrong.
    public var needsAttention: Bool { needsAttention(sample: false) }

    /// Whether something needs the person, in the sample or in their own books.
    public func needsAttention(sample: Bool) -> Bool {
        banks?.notes(sample: sample).contains(where: \.needsYou) ?? false
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
    public var troubles: [String] { notes().map(\.text) }

    /// One thing the control says of how the banks stand, and whether it needs the person.
    public struct Note: Hashable, Sendable {
        public let text: String
        public let needsYou: Bool
    }

    /// What went wrong, each said once, a bank to sign in to again first. In the sample, a bank
    /// that wants a sign-in is one of its inventions, said as what it shows, not as a warning.
    public func notes(sample: Bool = false) -> [Note] {
        var said: [Note] = []
        func say(_ sentence: String, needsYou: Bool = true) {
            if !said.contains(where: { $0.text == sentence }) {
                said.append(Note(text: sentence, needsYou: needsYou))
            }
        }
        for bank in banksToSignIn {
            if sample {
                say(
                    "\(bank.institution) shows a bank that wants you to sign in again. With your own banks, you do that in Hearth in the browser.",
                    needsYou: false)
            } else {
                say("\(bank.institution) needs you to sign in again. Do that in Hearth in the browser.")
            }
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

    /// The banks that want their member to sign in again.
    private var banksToSignIn: [Bank] { banks.filter { $0.needsSignIn || $0.trouble == .signIn } }

    /// What the control says in place of how fresh the banks are when something needs the
    /// person: who to sign in to again, or that the last update went wrong.
    func headline(sample: Bool) -> String? {
        let waiting = sample ? [] : banksToSignIn
        switch waiting.count {
        case 0: break
        case 1: return "\(waiting[0].institution) needs you"
        default: return "\(waiting.count) banks need you"
        }
        return notes(sample: sample).contains(where: \.needsYou) ? "Last update went wrong" : nil
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
