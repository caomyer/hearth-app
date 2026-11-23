import HearthKit
import SwiftUI

/// The Transactions screen: the whole journal, as the viewer may see it.
struct TransactionsView: View {
    let model: JournalModel
    let accounts: Accounts?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Transactions")
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(Palette.ink)
                .padding(.horizontal, 28)
                .padding(.top, 22)
            JournalView(model: model, accounts: accounts, identifier: "transactions")
        }
    }
}

/// Every account the viewer may see: where it is, when it was last brought up to date, its
/// balance to the cent, and whether that agrees with its bank.
struct AccountsView: View {
    let model: ScreenModel<Accounts>

    var body: some View {
        switch model.state {
        case .starting, .loading:
            Waiting(text: "Working out the books", identifier: "accounts.loading")
        case .signedOut:
            Color.clear
        case .failed(let summary):
            Unavailable(title: "The books could not be worked out", detail: summary)
        case .missing:
            Unavailable(title: "Nothing to show", detail: "There are no accounts for you here.")
        case .stopped:
            Unavailable(title: "Hearth's engine stopped", detail: "Quit Hearth and open it again.")
        case .ready(let accounts):
            if accounts.groups.isEmpty {
                Unavailable(title: "No accounts yet", detail: "Accounts appear once one is in the books.")
            } else {
                AccountList(accounts: accounts)
            }
        }
    }
}

private struct AccountList: View {
    let accounts: Accounts
    @Environment(Navigator.self) private var navigator

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text("Accounts")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                    .padding(.bottom, 4)
                Text("Each account says when it was last updated and whether its balance agrees with its bank.")
                    .foregroundStyle(Palette.ink2)
                    .padding(.bottom, 18)
                ColumnHeads()
                ForEach(accounts.groups) { group in
                    Text(group.name.uppercased())
                        .font(.system(size: 11, weight: .semibold))
                        .tracking(0.6)
                        .foregroundStyle(Palette.ink3)
                        .padding(.top, 16)
                        .padding(.bottom, 6)
                        .accessibilityAddTraits(.isHeader)
                    ForEach(group.accounts) { account in
                        Button {
                            navigator.open(AccountRef(id: account.id, name: account.name))
                        } label: {
                            AccountLine(account: account, asOf: accounts.asOf, currency: accounts.currency)
                        }
                        .buttonStyle(.plain)
                        Divider().overlay(Palette.line)
                    }
                }
            }
            .font(.system(size: 13))
            .frame(maxWidth: 880, alignment: .leading)
            .padding(.horizontal, 28)
            .padding(.vertical, 22)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// The "Last updated" column: wide enough for a date with its year on one line, and for the
/// sign-in hint on two.
private enum LastUpdated {
    static let width: CGFloat = 200
}

private struct ColumnHeads: View {
    var body: some View {
        HStack(spacing: 16) {
            Text("Account").frame(maxWidth: .infinity, alignment: .leading)
            Text("Last updated").frame(width: LastUpdated.width, alignment: .leading)
            Text("Balance").frame(width: 130, alignment: .trailing)
            Text("Bank balance").frame(width: 220, alignment: .leading)
        }
        .font(.system(size: 12, weight: .medium))
        .foregroundStyle(Palette.ink3)
        .padding(.bottom, 6)
        .overlay(alignment: .bottom) { Rectangle().fill(Palette.lineStrong).frame(height: 1) }
    }
}

/// One account in the list.
private struct AccountLine: View {
    let account: Account
    let asOf: Day
    let currency: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 16) {
            VStack(alignment: .leading, spacing: 2) {
                Text(account.name).foregroundStyle(Palette.ink)
                if let second {
                    Text(second).font(.system(size: 12)).foregroundStyle(Palette.ink3)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                Text(account.freshness(asOf: asOf) ?? "")
                    .foregroundStyle(account.behind ? Palette.warning : Palette.ink2)
                    .lineLimit(1)
                if let attention = account.attentionText {
                    Text(attention).font(.system(size: 12)).foregroundStyle(Palette.warning)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(width: LastUpdated.width, alignment: .leading)
            Text(account.balanceText(currency: currency) ?? "")
                .monospacedDigit()
                .foregroundStyle(Palette.ink)
                .frame(width: 130, alignment: .trailing)
            StandingLine(account: account, asOf: asOf, currency: currency)
                .frame(width: 220, alignment: .leading)
        }
        .padding(.vertical, 9)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("accounts.row.\(account.name)")
    }

    private var second: String? {
        let parts = [account.whereAbouts, account.unseenKind, account.whose].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

/// What a balance rests on: glyph, words, and colour last.
struct StandingLine: View {
    let account: Account
    let asOf: Day
    let currency: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Image(systemName: StandingMark.symbol(account.standing))
                .foregroundStyle(StandingMark.colour(account.standing))
                .accessibilityHidden(true)
            Text(account.status(asOf: asOf, currency: currency))
                .foregroundStyle(account.standing == .failed ? Palette.unfavourable : Palette.ink2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

/// One account's own page: its balance, what that rests on, and its transactions.
struct AccountView: View {
    let screen: AccountScreen

    var body: some View {
        content.task { await screen.account.watch() }
    }

    @ViewBuilder
    private var content: some View {
        switch screen.account.state {
        case .starting, .loading:
            Waiting(text: "Working out the books", identifier: "account.loading")
        case .signedOut:
            Color.clear
        case .failed(let summary):
            Unavailable(title: "The books could not be worked out", detail: summary)
        case .missing:
            Unavailable(title: "Nothing to show", detail: "There is no such account for you here.")
        case .stopped:
            Unavailable(title: "Hearth's engine stopped", detail: "Quit Hearth and open it again.")
        case .ready(let page):
            VStack(alignment: .leading, spacing: 0) {
                AccountHead(page: page)
                    .padding(.horizontal, 28)
                    .padding(.top, 22)
                    .padding(.bottom, 6)
                if page.account.transactions {
                    JournalView(model: screen.journal, accounts: nil, identifier: "account.journal")
                } else {
                    Divider().overlay(Palette.line).padding(.top, 12)
                    Unavailable(title: "Transactions not shared", detail: "Its transactions are not shared with you.")
                }
            }
        }
    }
}

private struct AccountHead: View {
    let page: AccountPage

    private var account: Account { page.account }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(account.name)
                .font(.system(size: 22, weight: .semibold))
                .foregroundStyle(Palette.ink)
                .accessibilityIdentifier("account.name")
            if let about {
                Text(about)
                    .foregroundStyle(Palette.ink3)
                    .padding(.top, 2)
            }
            Group {
                if let balance = account.balanceText(currency: page.currency) {
                    Text(balance)
                        .font(.system(size: 34, weight: .semibold))
                        .tracking(-0.5)
                        .monospacedDigit()
                        .foregroundStyle(Palette.ink)
                        .textSelection(.enabled)
                } else {
                    Text(absence)
                        .font(.system(size: 26, weight: .semibold))
                        .foregroundStyle(Palette.ink2)
                }
            }
            .padding(.top, 14)
            .accessibilityIdentifier("account.balance")
            StandingLine(account: account, asOf: page.asOf, currency: page.currency)
                .padding(.top, 6)
                .accessibilityIdentifier("account.standing")
            ForEach(notes, id: \.self) { note in
                Text(note)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.ink3)
                    .padding(.top, 4)
            }
            if let attention = account.attentionText {
                Text(attention)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.warning)
                    .padding(.top, 4)
            }
        }
    }

    /// The big line where a balance is not there to write: what it is instead.
    private var absence: String {
        switch account.standing {
        case .unseen: "Not seen by hearth"
        case .withheld: "Not shared with you"
        case .arriving: "Being brought in"
        default: "Not known yet"
        }
    }

    private var about: String? {
        let parts = [account.unseenKind ?? account.kind, account.whereAbouts, account.whose].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private var notes: [String] {
        var notes = [account.reason, account.freshness(asOf: page.asOf)].compactMap { $0 }
        if let first = page.history.first?.from {
            notes.append("History since \(Figures.day(first, asOf: Day(year: 0, month: 1, day: 1)))")
        }
        return notes
    }
}
