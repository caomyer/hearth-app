import Foundation

/// What an account's balance rests on, in words, by hearth's rule that every absence says what
/// it is: a balance not known yet, one hearth never sees, one not shared, and one whose check
/// failed are never written alike, nor as a blank or a zero.
extension Account {
    /// The balance as the list and the account's page write it, or nil where there is none to
    /// write, which `status` then says why.
    public func balanceText(currency: String, locale: Locale = .current) -> String? {
        guard let value = known else { return nil }
        let text = Figures.cents(value, currency: currency, locale: locale)
        return balance?.isFloor == true ? "at least \(text)" : text
    }

    /// The balance in whole units, as the sidebar lists it.
    public func sidebarText(currency: String, locale: Locale = .current) -> String? {
        guard let value = known else { return nil }
        return Figures.total(value, currency: currency, locale: locale)
    }

    /// What the balance rests on, in one short line.
    public func status(asOf: Day, currency: String, locale: Locale = .current) -> String {
        func on(_ day: Day?) -> String {
            day.map { ", \(Figures.day($0, asOf: asOf, locale: locale))" } ?? ""
        }
        switch standing {
        case .matches:
            return "Matches bank" + on(checked)
        case .holdings:
            return "As its brokerage reported" + on(checked)
        case .checked:
            return "Matches its last balance check" + on(checked)
        case .unchecked:
            return reconciling ? "Difference with its bank to confirm" : "Not checked against a bank"
        case .notKnown:
            return "Not known yet"
        case .arriving:
            return "Being brought in"
        case .unseen:
            return "Not seen by hearth"
        case .failed:
            guard let failed else { return "Does not match its bank" }
            let said = Figures.cents(failed.asserted, currency: currency, locale: locale)
            return "Does not match: \(said) reported\(on(failed.asOf))"
        case .withheld:
            return "Not shared with you"
        }
    }

    /// Why a balance is not known yet, to its owners, who alone are told
    /// (`hearth.plaid.opening.Unknown`), or why one is not checked against its bank yet; nil
    /// for anyone else, or any other balance.
    public var reason: String? {
        if standing == .unchecked, reconciling {
            return "Its bank and its imported ledger disagree by an amount nobody has confirmed yet. Confirm it in Review, and its balance matches its bank."
        }
        guard standing == .notKnown, let why else { return nil }
        switch why {
        case "investment": return "Its brokerage has not said what it holds yet."
        case "kind": return "Its bank reports a balance that does not say plainly what it holds or owes."
        case "shared": return "More than one bank account goes to it, so no one balance is its own."
        case "no-balance": return "Its bank has not said what it holds yet."
        case "unfinished": return "Its bank's latest update did not finish."
        case "currency": return "Its bank reports its balance in another currency."
        case "gap": return "It was connected again, and not all its transactions are in yet."
        default: return nil
        }
    }

    /// When its data was last brought up to date, with a warning when that is long ago.
    public func freshness(asOf: Day, locale: Locale = .current) -> String? {
        if let updated {
            let day = Figures.day(updated, asOf: asOf, locale: locale)
            return behind ? "Data ends \(day)" : "Updated \(day)"
        }
        if let priced {
            return "Prices from \(Figures.day(priced, asOf: asOf, locale: locale))"
        }
        return nil
    }

    /// Where it is: its bank and the last digits, as far as the viewer may be told.
    public var whereAbouts: String? {
        let parts = [institution, mask.map { "••\u{00A0}\($0)" }].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// Whose it is, when it is not the viewer's alone.
    public var whose: String? {
        guard !(yours && owners.count == 1), !owners.isEmpty else { return nil }
        return owners.formatted(.list(type: .and))
    }

    /// What the viewer has to do for it, if anything: their own connection needs them.
    public var attentionText: String? {
        attention ? "Sign in to its bank again in Hearth in your browser" : nil
    }
}
