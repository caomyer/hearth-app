import HearthKit
import SwiftUI

/// Fava's journal, natively: entries newest first, a day at a time, one line each until
/// opened, when every side shows. Balance checks sit in line, on the day they cover.
struct JournalView: View {
    let model: JournalModel
    /// The accounts the journal may be narrowed to, for the toolbar's account menu; none on an
    /// account's own page, whose journal keeps to it.
    let accounts: Accounts?
    let identifier: String

    @Environment(Navigator.self) private var navigator
    @State private var selected: Int?
    /// The review the inspector is on: the one the entry waited on when it was chosen, kept
    /// once it is decided, so what was done and its Undo stay beside the journal until
    /// another entry is chosen.
    @State private var review: String?
    @State private var text = ""
    @State private var choosingRange = false
    @State private var openedStart = false

    var body: some View {
        VStack(spacing: 0) {
            JournalHead(model: model)
                .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 28)
                .padding(.top, 16)
                .padding(.bottom, 12)
            Divider().overlay(Palette.line)
            content
        }
        .searchable(text: $text, placement: .toolbar, prompt: "Payee or description")
        .task(id: text) {
            // Asked once typing pauses, not at every key.
            guard text.trimmingCharacters(in: .whitespaces) != model.filter.words else { return }
            try? await Task.sleep(for: .milliseconds(250))
            guard !Task.isCancelled else { return }
            var filter = model.filter
            filter.text = text
            await model.apply(filter)
        }
        .onChange(of: model.filter.words) { _, words in
            if words != text.trimmingCharacters(in: .whitespaces) { text = words }
        }
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                PeriodMenu(model: model, choosingRange: $choosingRange)
                if let accounts, model.pinned == nil {
                    AccountMenu(model: model, accounts: accounts)
                }
            }
        }
        .task {
            if model.state == .starting { await model.reload() }
            await model.watch()
        }
        // An unreviewed row of the viewer's own opens its review beside the journal, decided
        // there as in Review.
        .inspector(isPresented: reviewing) {
            if let id = review {
                ReviewInspector(desk: navigator.desk, id: id)
                    .id(id)
                    .inspectorColumnWidth(min: 280, ideal: 300, max: 420)
            }
        }
        .onChange(of: model.entries) { _, entries in
            openStart(entries)
        }
        .onChange(of: selected) { _, selected in
            review = selected.flatMap { id in model.entries.first { $0.id == id }?.review }
        }
    }

    /// What a debug build was asked to open Transactions on: an entry by its payee, or the
    /// journal narrowed to words (`StartScreen`).
    private func openStart(_ entries: [Entry]) {
        guard identifier == "transactions", !openedStart, !entries.isEmpty else { return }
        openedStart = true
        if let words = StartScreen.transactionWords {
            text = words
        }
        if let payee = StartScreen.transaction {
            selected = entries.first { $0.payee == payee }?.id
        }
    }

    private var reviewing: Binding<Bool> {
        Binding(
            get: { review != nil },
            set: { shown in
                if !shown {
                    selected = nil
                    review = nil
                }
            })
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .starting, .loading:
            Waiting(text: "Working out the books", identifier: "\(identifier).loading")
        case .signedOut:
            Color.clear
        case .failed(let summary):
            Unavailable(title: "The books could not be worked out", detail: summary)
        case .missing:
            Unavailable(title: "Nothing to show", detail: "These transactions are not shared with you.")
        case .stopped:
            Unavailable(title: "Hearth's engine stopped", detail: "Quit Hearth and open it again.")
        case .ready:
            if model.entries.isEmpty {
                Unavailable(
                    title: model.filter.tokens.isEmpty && model.filter.show == .everything
                        ? "No transactions in this period" : "Nothing matches",
                    detail: model.filter.tokens.isEmpty && model.filter.show == .everything
                        ? "Choose another period from the toolbar." : "Remove a filter to see more.")
            } else {
                entries
            }
        }
    }

    private var entries: some View {
        EntryList(model: model, selected: $selected, identifier: identifier)
    }
}

/// The entries asked for so far, a day at a time, and more asked for at the end. Back on a
/// journal scrolled a long way, the list is first drawn with a page of them (`FirstFill`).
private struct EntryList: View {
    let model: JournalModel
    @Binding var selected: Int?
    let identifier: String
    @State private var filled = false
    /// Nothing until the window says: a list first drawn narrow never asks the window for more.
    @State private var width: CGFloat = 0

    var body: some View {
        List(selection: $selected) {
            ForEach(filled ? model.days : model.firstDays(fitting: FirstFill.rows)) { day in
                Section {
                    ForEach(day.entries) { entry in
                        EntryRow(
                            entry: entry, open: selected == entry.id,
                            currency: model.header?.currency ?? "USD", pinned: model.pinned
                        )
                        .tag(entry.id)
                        .listRowSeparator(.hidden)
                        .onAppear {
                            if entry.id == model.entries.last?.id {
                                Task { await model.loadMore() }
                            }
                        }
                    }
                } header: {
                    Text(Figures.heading(day.day, asOf: model.header?.asOf ?? day.day))
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Palette.ink2)
                        .accessibilityAddTraits(.isHeader)
                }
            }
            if model.hasMore || model.isLoadingMore {
                HStack {
                    Spacer()
                    ProgressView().controlSize(.small)
                    Spacer()
                }
                .listRowSeparator(.hidden)
            }
        }
        .listStyle(.inset)
        .scrollContentBackground(.hidden)
        .background(Palette.surface)
        .accessibilityIdentifier("\(identifier).list")
        .task { filled = true }
        .environment(\.journalFit, JournalFit(width: width, pinned: model.pinned != nil))
        // The width the window gives the list, never what the list would like to be.
        .frame(minWidth: 0, idealWidth: JournalFit.narrowest, maxWidth: .infinity)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
    }
}

/// How many of the journal's columns its list is wide enough for. Narrower, the accounts go
/// under each entry's payee first, then its category too; the amount always keeps its column,
/// as the figures down the right edge are what the eye runs down. Nothing is ever dropped.
enum JournalFit {
    case narrow, medium, wide

    static let marker: CGFloat = 8
    static let spacing: CGFloat = 12
    /// The narrowest a payee is given before the columns beside it give way.
    static let payee: CGFloat = 160
    static let category: CGFloat = 160
    static let amount: CGFloat = 110
    /// A list row's own insets, both sides together.
    static let insets: CGFloat = 40

    /// The accounts column: on an account's own page it names only a transfer's other side.
    static func accounts(pinned: Bool) -> CGFloat { pinned ? 180 : 250 }

    /// The narrowest the list is drawn: a payee beside its amount.
    static let narrowest = marker + spacing + payee + spacing + amount + insets

    init(width: CGFloat, pinned: Bool) {
        let category = Self.narrowest + Self.spacing + Self.category
        if width >= category + Self.spacing + Self.accounts(pinned: pinned) {
            self = .wide
        } else if width >= category {
            self = .medium
        } else {
            self = .narrow
        }
    }

    var accountsColumn: Bool { self == .wide }
    var categoryColumn: Bool { self != .narrow }
}

extension EnvironmentValues {
    /// How many columns the journal's list is wide enough for.
    @Entry var journalFit = JournalFit.wide
}

/// What the journal is narrowed to: the chips, the filters as tokens, and how many entries
/// over which days that leaves.
private struct JournalHead: View {
    let model: JournalModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            // As many to a line as fit, never one cut short.
            FlowChips(spacing: 6) {
                ForEach(Show.allCases, id: \.self) { show in
                    Chip(
                        label: show.label, count: show == .everything ? nil : model.counts.count(show),
                        on: model.filter.show == show
                    ) {
                        var filter = model.filter
                        filter.show = show
                        Task { await model.apply(filter) }
                    }
                    .accessibilityIdentifier("journal.show.\(show.rawValue)")
                }
            }
            if !model.tokens.isEmpty {
                FlowChips(spacing: 6) {
                    ForEach(model.tokens) { token in
                        TokenView(label: label(token)) {
                            Task { await model.remove(token) }
                        }
                    }
                }
            }
            if let summary {
                Text(summary)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.ink3)
                    .monospacedDigit()
                    .accessibilityIdentifier("journal.summary")
            }
        }
    }

    private var summary: String? {
        guard let header = model.header, model.state == .ready else { return nil }
        let count = model.total == 1 ? "1 entry" : "\(model.total.formatted()) entries"
        let span = Figures.span(from: header.from, to: header.to)
        let whose = model.filter.account?.name ?? "all accounts"
        return "\(count) · \(span) · \(whose)"
    }

    private func label(_ token: JournalFilter.Token) -> String {
        switch token {
        case .period(let period): period.label(asOf: model.header?.asOf ?? Day(Date()))
        case .account(let account): account.name
        case .category(let category): category.label
        case .text(let words): "\u{201C}\(words)\u{201D}"
        case .statement(let rows): "\(rows.label), in Statements"
        }
    }
}

/// One chip over the journal, on or off, with how many entries it leaves.
private struct Chip: View {
    let label: String
    let count: Int?
    let on: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Text(label)
                if let count {
                    Text(count.formatted())
                        .monospacedDigit()
                        .foregroundStyle(on ? Palette.accent : Palette.ink3)
                }
            }
            .font(.system(size: 12, weight: on ? .semibold : .regular))
            .foregroundStyle(on ? Palette.accent : Palette.ink2)
            .padding(.horizontal, 10)
            .frame(height: 24)
            .background(Capsule().fill(on ? Palette.accentBackground : Color.clear))
            .overlay(Capsule().strokeBorder(on ? Palette.accent : Palette.lineStrong, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// A filter, which one click removes.
private struct TokenView: View {
    let label: String
    let remove: () -> Void

    var body: some View {
        HStack(spacing: 4) {
            Text(label)
                .lineLimit(1)
            Button(action: remove) {
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .bold))
                    .frame(width: 14, height: 14)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Remove \(label)")
        }
        .font(.system(size: 12))
        .foregroundStyle(Palette.ink)
        .padding(.leading, 9)
        .padding(.trailing, 5)
        .frame(height: 22)
        .background(RoundedRectangle(cornerRadius: 5).fill(Palette.background))
        .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(Palette.line, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("journal.token")
    }
}

/// One entry, a line until it is opened, then every side of it.
private struct EntryRow: View {
    let entry: Entry
    let open: Bool
    let currency: String
    let pinned: AccountRef?
    @Environment(\.journalFit) private var fit

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            line
            if open, !entry.sides.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(entry.sides.enumerated()), id: \.offset) { _, side in
                        SideLine(side: side)
                    }
                }
                .padding(.leading, 17)
                .padding(.top, 8)
                .padding(.bottom, 4)
            }
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("journal.entry")
    }

    private var line: some View {
        HStack(alignment: .firstTextBaseline, spacing: JournalFit.spacing) {
            marker
                .frame(width: JournalFit.marker)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .foregroundStyle(entry.kind == .balance ? Palette.favourable : Palette.ink)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("journal.payee")
                if !entry.narration.isEmpty {
                    Text(entry.narration)
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink3)
                        .lineLimit(2)
                }
                // What the columns would say, under the payee where the list is too narrow.
                if !fit.accountsColumn, showsAccounts {
                    AccountsCell(accounts: entry.accounts, pinned: pinned)
                        .font(.system(size: 12))
                }
                if !fit.categoryColumn {
                    CategoryCell(entry: entry)
                        .font(.system(size: 12))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if fit.accountsColumn {
                AccountsCell(accounts: entry.accounts, pinned: pinned)
                    .frame(width: JournalFit.accounts(pinned: pinned != nil), alignment: .leading)
            }
            if fit.categoryColumn {
                CategoryCell(entry: entry)
                    .frame(width: JournalFit.category, alignment: .leading)
            }
            Text(amount)
                .monospacedDigit()
                .foregroundStyle(amountColour)
                .frame(width: JournalFit.amount, alignment: .trailing)
                .accessibilityIdentifier("journal.amount")
        }
        .font(.system(size: 13))
    }

    /// Whether the accounts cell says anything: on an account's own page, only a transfer's
    /// other side.
    private var showsAccounts: Bool {
        guard let pinned else { return !entry.accounts.isEmpty }
        return entry.accounts.count > 1 && entry.accounts.contains { $0.id != pinned.id }
    }

    @ViewBuilder
    private var marker: some View {
        if entry.unreviewed {
            Circle()
                .fill(Palette.warning)
                .frame(width: 7, height: 7)
                .help("Not reviewed yet")
                .accessibilityLabel("Not reviewed yet")
        } else if entry.kind == .balance {
            Image(systemName: "checkmark")
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(Palette.favourable)
                .accessibilityHidden(true)
        } else {
            Color.clear.frame(width: 7, height: 7)
        }
    }

    private var title: String {
        guard entry.kind == .balance else { return entry.payee }
        switch entry.checked {
        case .bank: return "Balance matches bank"
        case .brokerage: return "Holdings match brokerage"
        case .books, nil: return "Balance check passed"
        }
    }

    private var amount: String {
        switch entry.kind {
        case .balance: Figures.amount(entry.amount, signed: false)
        case .transfer where entry.isOwnTransfer: Figures.amount(entry.amount, signed: false)
        // Opened from a statement's line, the part of a split row that line adds up.
        default: Figures.amount(entry.part ?? entry.amount)
        }
    }

    /// Money in is favourable; a check or a move between one's own accounts is neither.
    private var amountColour: Color {
        if entry.kind == .balance || entry.isOwnTransfer { return Palette.ink2 }
        return entry.amount.amount > 0 ? Palette.favourable : Palette.ink
    }
}

/// The accounts an entry moved: one, or a transfer's from and to. On an account's own page
/// its own name goes without saying, and a transfer names only where the money went or came
/// from.
private struct AccountsCell: View {
    let accounts: [AccountRef]
    let pinned: AccountRef?

    var body: some View {
        HStack(spacing: 4) {
            if let pinned {
                if let index = accounts.firstIndex(where: { $0.id != pinned.id }), accounts.count > 1 {
                    Text(index == 0 ? "From" : "To").foregroundStyle(Palette.ink3)
                    AccountLink(account: accounts[index])
                }
            } else {
                ForEach(Array(accounts.enumerated()), id: \.element.id) { index, account in
                    if index > 0 {
                        Text("→").foregroundStyle(Palette.ink3).accessibilityLabel("to")
                    }
                    AccountLink(account: account)
                }
            }
        }
        .lineLimit(1)
    }
}

/// An entry's category, which opens the journal narrowed to it, or what it is instead.
private struct CategoryCell: View {
    let entry: Entry

    var body: some View {
        HStack(spacing: 4) {
            if let category = entry.category {
                CategoryLink(category: category)
                if entry.suggested {
                    // Why it is suggested so, as Review says it, for the member who reviews it.
                    Text("suggested")
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.warning)
                        .help(entry.reason ?? "Suggested by Hearth, not reviewed yet")
                }
            } else {
                Text(words)
                    .foregroundStyle(entry.waiting != nil ? Palette.warning : Palette.ink3)
            }
        }
        .lineLimit(1)
    }

    private var words: String {
        switch (entry.kind, entry.waiting) {
        case (.balance, _): "Balance check"
        case (_, .category): "Uncategorized"
        case (_, .transfer): "Transfer, other side not found"
        case (.transfer, nil): "Transfer"
        case (.transaction, nil): "Split, \(entry.sides.filter { $0.category != nil }.count)"
        }
    }
}

/// One side of an opened entry.
private struct SideLine: View {
    let side: Side

    var body: some View {
        HStack(spacing: 12) {
            Group {
                if let account = side.account {
                    AccountLink(account: account)
                } else if let category = side.category {
                    CategoryLink(category: category)
                } else if side.waiting {
                    Text("Uncategorized").foregroundStyle(Palette.warning)
                } else {
                    Text("Another account").foregroundStyle(Palette.ink3)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(Figures.amount(side.amount))
                .monospacedDigit()
                .foregroundStyle(Palette.ink2)
                .frame(width: 110, alignment: .trailing)
        }
        .font(.system(size: 12))
    }
}

/// An account's name, which opens the account.
struct AccountLink: View {
    let account: AccountRef
    @Environment(Navigator.self) private var navigator

    var body: some View {
        ClickThrough(text: account.name) { navigator.open(account) }
            .accessibilityIdentifier("link.account.\(account.name)")
    }
}

/// A category's name, which opens the journal narrowed to it.
struct CategoryLink: View {
    let category: CategoryRef
    @Environment(Navigator.self) private var navigator

    var body: some View {
        ClickThrough(text: category.label) { navigator.show(category) }
            .help(category.name)
            .accessibilityIdentifier("link.category.\(category.label)")
    }
}

/// Text that takes you somewhere: quiet until the pointer is over it, then the accent, as
/// blue is kept for what can be acted on.
struct ClickThrough: View {
    let text: String
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Text(text)
                .underline(hovering)
                .foregroundStyle(hovering ? Palette.accent : Palette.ink2)
                .lineLimit(1)
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .pointerStyle(.link)
    }
}

/// The period the journal covers: this year so far, a year, a month, or any range.
private struct PeriodMenu: View {
    let model: JournalModel
    @Binding var choosingRange: Bool

    var body: some View {
        Menu {
            Button("This year so far") { choose(.yearToDate) }
            Section("Year") {
                ForEach(0..<3, id: \.self) { back in
                    Button(String(asOf.year - back)) { choose(.year(asOf.year - back)) }
                }
            }
            Section("Month") {
                ForEach(months, id: \.self) { month in
                    Button(JournalFilter.Period.month(year: month.year, month: month.month).label(asOf: asOf)) {
                        choose(.month(year: month.year, month: month.month))
                    }
                }
            }
            Divider()
            Button("Custom Range…") { choosingRange = true }
        } label: {
            Label(model.filter.period.label(asOf: asOf), systemImage: "calendar")
                .labelStyle(.titleAndIcon)
        }
        .help("Period")
        .accessibilityIdentifier("journal.period")
        .popover(isPresented: $choosingRange, arrowEdge: .bottom) {
            RangePicker(asOf: asOf) { from, to in
                choosingRange = false
                choose(.range(from: from, to: to))
            }
        }
    }

    private var asOf: Day { model.header?.asOf ?? Day(Date()) }

    /// The last twelve months, newest first.
    private var months: [Day] {
        (0..<12).map { back in
            let index = asOf.year * 12 + (asOf.month - 1) - back
            return Day(year: index / 12, month: index % 12 + 1, day: 1)
        }
    }

    private func choose(_ period: JournalFilter.Period) {
        var filter = model.filter
        filter.period = period
        Task { await model.apply(filter) }
    }
}

/// Two days, both included.
private struct RangePicker: View {
    let asOf: Day
    let pick: (Day, Day) -> Void
    @State private var from = Date()
    @State private var to = Date()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            DatePicker("From", selection: $from, displayedComponents: .date)
            DatePicker("To", selection: $to, displayedComponents: .date)
            HStack {
                Spacer()
                Button("Show") { pick(Day(from), Day(to)) }
                    .buttonStyle(PrimaryButtonStyle())
                    .keyboardShortcut(.defaultAction)
            }
        }
        .datePickerStyle(.field)
        .font(.system(size: 13))
        .padding(16)
        .frame(width: 260)
        .onAppear {
            to = asOf.date()
            from = Day(year: asOf.year, month: asOf.month, day: 1).date()
        }
    }
}

/// The account the journal is narrowed to, or all of them.
private struct AccountMenu: View {
    let model: JournalModel
    let accounts: Accounts

    var body: some View {
        Menu {
            Button("All Accounts") { choose(nil) }
            ForEach(accounts.groups) { group in
                let open = group.accounts.filter(\.transactions)
                if !open.isEmpty {
                    Section(group.name) {
                        ForEach(open) { account in
                            Button(account.name) { choose(AccountRef(id: account.id, name: account.name)) }
                        }
                    }
                }
            }
        } label: {
            Label(model.filter.account?.name ?? "All accounts", systemImage: "building.columns")
                .labelStyle(.titleAndIcon)
        }
        .help("Account")
        .accessibilityIdentifier("journal.account")
    }

    private func choose(_ account: AccountRef?) {
        var filter = model.filter
        filter.account = account
        Task { await model.apply(filter) }
    }
}

/// A row of the journal nobody has checked, as Review shows it, decided from here.
private struct ReviewInspector: View {
    let desk: Desk
    let id: String
    @State private var chosen: Destination?
    @State private var choosing = false

    var body: some View {
        RowDetail(desk: desk, id: id, shown: nil, chosen: $chosen, choosing: $choosing, inQueue: false)
            .background(Palette.surface)
    }
}
