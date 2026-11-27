import HearthKit
import SwiftUI

/// Where did it go? The rows Hearth sees one side of, a group at a time: one answer settles
/// every row like it, and a row unlike the rest is answered on its own.
struct WhereList: View {
    let desk: Desk

    private var model: ScreenModel<WherePage> { desk.whereItWent }

    var body: some View {
        Group {
            switch model.state {
            case .starting, .loading:
                Waiting(text: "Working out the books", identifier: "where.loading")
            case .ready(let page):
                if page.groups.isEmpty {
                    Unavailable(
                        title: "Every place your money went has a name",
                        detail: "Nothing your banks sent is waiting to be told where it went.")
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 14) {
                            Text(lead(page))
                                .font(.system(size: 12))
                                .foregroundStyle(Palette.ink2)
                                .fixedSize(horizontal: false, vertical: true)
                            ForEach(page.groups) { group in
                                WhereCard(desk: desk, group: group, currency: page.currency, asOf: page.asOf)
                            }
                        }
                        .padding(24)
                        .frame(maxWidth: 760, alignment: .leading)
                    }
                }
            case .failed(let summary):
                Unavailable(title: "The books could not be worked out", detail: summary)
            case .missing, .signedOut:
                Color.clear
            case .stopped:
                Unavailable(title: "Hearth's engine stopped", detail: "Quit Hearth and open it again.")
            }
        }
        .background(Palette.surface)
        .task { await model.watch() }
    }

    private func lead(_ page: WherePage) -> String {
        let moved = Figures.cents(page.moved, currency: page.currency)
        return "Hearth sees one side of these: money left one of your connected accounts, or arrived in one, and Hearth has no record of the other side. \(ReviewWords.transactions(page.rows)), \(moved) moved. None of it is spending or income until you say."
    }
}

/// One group, and the one answer that settles it.
private struct WhereCard: View {
    let desk: Desk
    let group: WhereGroup
    let currency: String
    let asOf: Day

    @State private var byAccount = true
    @State private var account: AccountRef?
    @State private var category: CategoryRef?
    @State private var showing = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(group.title)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                        .accessibilityIdentifier("where.title")
                    Text(about)
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 12)
                VStack(alignment: .trailing, spacing: 3) {
                    Text(Figures.cents(group.total, currency: currency))
                        .font(.system(size: 15, weight: .semibold))
                        .monospacedDigit()
                        .foregroundStyle(group.total.amount > 0 ? Palette.favourable : Palette.ink)
                    Text(ReviewWords.transactions(group.rows.count))
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink3)
                }
            }
            Text(ReviewWords.question(group.question))
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Palette.ink)
            answers
            HStack(spacing: 10) {
                Button(group.rows.count == 1 ? "Save" : "Save for all \(group.rows.count.formatted())") {
                    guard let destination else { return }
                    Task { await desk.decisions.make(.moveGroup(group, to: destination)) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(destination == nil || desk.decisions.isDeciding)
                .accessibilityIdentifier("where.save")
                Button(showing ? "Hide them" : "Show them") { showing.toggle() }
                    .buttonStyle(.bordered)
                Spacer(minLength: 0)
            }
            if showing {
                VStack(spacing: 0) {
                    ForEach(group.rows) { row in
                        WhereRow(desk: desk, row: row, group: group, currency: currency, asOf: asOf)
                        if row.id != group.rows.last?.id { Divider().overlay(Palette.line) }
                    }
                }
                .padding(.horizontal, 10)
                .background(RoundedRectangle(cornerRadius: 8).fill(Palette.background))
            }
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 10).fill(Palette.surface))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Palette.line, lineWidth: 1))
        .onAppear(perform: begin)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("where.group.\(group.title)")
    }

    private var about: String {
        let on = group.rows.compactMap(\.account?.name).first ?? "your account"
        var text: String
        switch group.question {
        case .paidFrom: text = "Payments into \(on) that Hearth found no other side for."
        case .paidTo: text = "Card payments out of \(on) that Hearth found no other side for."
        case .loan: text = group.outgoing ? "A loan payment out of \(on)." : "Money from a loan, into \(on)."
        case .came: text = "Into \(on)\(group.rows.count > 1 && group.describedOnce ? ", the same description each time" : "")."
        case .went: text = "Out of \(on)\(group.rows.count > 1 && group.describedOnce ? ", the same description each time" : "")."
        }
        text += " Since \(Figures.month(group.since))."
        if let learned = group.learned {
            text += " Like \(learned.like == 1 ? "1 past decision" : "\(learned.like.formatted()) past decisions"): suggested, and waiting for your yes."
        }
        return text
    }

    @ViewBuilder
    private var answers: some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker("Answer", selection: $byAccount) {
                Text(ReviewWords.mine(group.question)).tag(true)
                if !group.categories.isEmpty {
                    Text(ReviewWords.other(group.question)).tag(false)
                }
            }
            .pickerStyle(.radioGroup)
            .labelsHidden()
            .accessibilityIdentifier("where.answer")
            if byAccount {
                if group.accounts.isEmpty {
                    Text("Name the account in Hearth in the browser, under Review, Where did it go?, then answer here.")
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Picker("Which account", selection: $account) {
                        // Nothing is chosen for the person unless they chose it for rows like these.
                        if account == nil {
                            Text("Choose an account").tag(AccountRef?.none)
                        }
                        ForEach(group.accounts) { account in
                            Text(account.name).tag(Optional(account))
                        }
                    }
                    .fixedSize()
                    .accessibilityIdentifier("where.account")
                }
            } else {
                Picker("Category", selection: $category) {
                    ForEach(group.categories, id: \.name) { category in
                        Text(category.label).tag(Optional(category))
                    }
                }
                .fixedSize()
                .accessibilityIdentifier("where.category")
            }
        }
    }

    private var destination: Destination? {
        if byAccount { return account.map(Destination.account) }
        return category.map(Destination.category)
    }

    /// What is chosen to begin with is only what the viewer said of rows like these: one chosen
    /// for them otherwise would be a guess, saved by a careless click.
    private func begin() {
        byAccount = group.byAccount || group.categories.isEmpty
        account = group.accounts.first { $0.id == group.chosen }
        let wanted = group.question == .came ? "Income" : "Expenses"
        category = group.categories.first { $0.name.hasPrefix(wanted) } ?? group.categories.first
    }
}

/// One row of a group, which may be answered on its own.
private struct WhereRow: View {
    let desk: Desk
    let row: ReviewRow
    let group: WhereGroup
    let currency: String
    let asOf: Day

    var body: some View {
        HStack(spacing: 10) {
            Text(Figures.day(row.date, asOf: asOf))
                .foregroundStyle(Palette.ink3)
                .frame(width: 92, alignment: .leading)
            Text(row.title)
                .foregroundStyle(Palette.ink)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(Figures.amount(row.amount))
                .monospacedDigit()
                .foregroundStyle(row.amount.amount > 0 ? Palette.favourable : Palette.ink)
            Menu("Answer alone") {
                if !group.accounts.isEmpty {
                    Section(ReviewWords.mine(group.question)) {
                        ForEach(group.accounts) { account in
                            Button(account.name) { answer(.account(account)) }
                        }
                    }
                }
                if !group.categories.isEmpty {
                    Section(ReviewWords.other(group.question)) {
                        ForEach(group.categories, id: \.name) { category in
                            Button(category.label) { answer(.category(category)) }
                        }
                    }
                }
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .disabled(desk.decisions.isDeciding)
        }
        .font(.system(size: 12))
        .padding(.vertical, 7)
    }

    private func answer(_ destination: Destination) {
        Task { await desk.decisions.make(.moveRow(row, to: destination)) }
    }
}
