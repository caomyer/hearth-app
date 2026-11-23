import HearthKit
import SwiftUI

/// One row waiting for review, as Review and Transactions show it: the day and account, the
/// amount, the cleaned-up name over the bank's own words, the category to settle it as, the
/// rows from the same merchant before it, and the decision.
struct RowDetail: View {
    let desk: Desk
    let id: String
    /// The row as the queue showed it, until its own detail is in.
    let shown: ReviewRow?
    /// Where it is to go: its suggestion to begin with.
    @Binding var chosen: Destination?
    @Binding var choosing: Bool
    /// In Review's queue, where deciding moves on to the next item; elsewhere the detail
    /// stays, and says what was decided.
    let inQueue: Bool

    @State private var model: ScreenModel<ReviewDetail>?

    var body: some View {
        Group {
            if let row {
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        head(row)
                        if let detail {
                            switch detail.decision {
                            case .waiting where row.isReconciling:
                                difference(row)
                                actions(row)
                            case .waiting:
                                category(row, detail)
                                if !detail.earlier.isEmpty {
                                    earlier(row, detail)
                                }
                                actions(row)
                            case .elsewhere:
                                settled(
                                    "Hearth does not see the other side of this one, so it is not asked for a category. Say where the money went under Review, Where did it go?"
                                )
                            case .reviewed:
                                settled("Reviewed: booked as \(detail.booked?.label ?? "chosen").")
                            case .excluded:
                                settled("Excluded: not a transaction. It books nothing.")
                            }
                        } else if model?.state == .missing {
                            settled("This transaction is no longer waiting for you.")
                        }
                        if !inQueue, let notice = desk.decisions.notice, desk.decisions.answered > 0 {
                            InlineNotice(notice: notice, decisions: desk.decisions)
                        }
                    }
                    .padding(22)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            } else if model?.state == .missing {
                Unavailable(title: "Nothing to review", detail: "This transaction is no longer waiting for you.")
            } else {
                Waiting(text: "Opening the transaction", identifier: "review.detail.loading")
            }
        }
        .task(id: desk.decisions.answered) {
            if model == nil { model = desk.reviewRow(id) }
            await model?.refresh()
        }
        .onChange(of: detail?.row.suggested) { _, suggested in
            if chosen == nil, let suggested { chosen = .category(suggested) }
        }
        .onAppear {
            if chosen == nil, let suggested = shown?.suggested { chosen = .category(suggested) }
        }
        .accessibilityIdentifier("review.detail")
    }

    private var detail: ReviewDetail? { model?.content }

    /// What is on screen of the row: its own detail once it is in, else what the queue showed.
    private var row: ReviewRow? { detail?.row ?? shown }

    private var currency: String { detail?.currency ?? desk.review.queue?.currency ?? "USD" }

    private func head(_ row: ReviewRow) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(dayAndAccount(row))
                .font(.system(size: 12))
                .foregroundStyle(Palette.ink3)
            Text(Figures.cents(row.amount, currency: currency))
                .font(.system(size: 32, weight: .semibold))
                .tracking(-0.5)
                .monospacedDigit()
                .foregroundStyle(row.amount.amount > 0 ? Palette.favourable : Palette.ink)
                .textSelection(.enabled)
                .accessibilityIdentifier("review.detail.amount")
            Text(row.title)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(Palette.ink)
                .textSelection(.enabled)
                .accessibilityIdentifier("review.detail.payee")
            if row.payee != nil, !row.narration.isEmpty, row.narration != row.payee {
                Text(row.narration)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Palette.ink3)
                    .textSelection(.enabled)
                    .padding(.top, 1)
            }
        }
    }

    private func dayAndAccount(_ row: ReviewRow) -> String {
        let day = Figures.weekday(row.date, asOf: detail?.asOf ?? desk.review.queue?.asOf ?? row.date)
        return [day, row.account?.name].compactMap { $0 }.joined(separator: " · ")
    }

    private func category(_ row: ReviewRow, _ detail: ReviewDetail) -> some View {
        Panel(title: "Category") {
            VStack(alignment: .leading, spacing: 8) {
                FlowChips(spacing: 6) {
                    if let suggested = row.suggested {
                        ChoiceChip(label: suggested.label, on: chosen == .category(suggested)) {
                            chosen = .category(suggested)
                        }
                        .accessibilityIdentifier("review.choice.\(suggested.label)")
                    }
                    if let chosen, chosen != row.suggested.map(Destination.category) {
                        ChoiceChip(label: chosen.label, on: true) {}
                            .accessibilityIdentifier("review.choice.\(chosen.label)")
                    }
                    ChoiceChip(label: "Other…", on: false) { choosing = true }
                        .accessibilityIdentifier("review.other")
                        .popover(isPresented: $choosing, arrowEdge: .leading) {
                            CategoryChooser(detail: detail) { destination in
                                chosen = destination
                                choosing = false
                            }
                        }
                }
                Text(reason(row))
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.ink3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// A carried account's difference with its bank: no category to choose, only what to do
    /// about it, and Confirm.
    private func difference(_ row: ReviewRow) -> some View {
        Panel(title: "Difference") {
            Text(row.reason)
                .font(.system(size: 12))
                .foregroundStyle(Palette.ink3)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("review.detail.reason")
        }
    }

    private func reason(_ row: ReviewRow) -> String {
        guard let suggested = row.suggested else {
            return "Nothing could be suggested for it. Choose where it belongs."
        }
        return "Suggested as \(suggested.label): \(row.reason.prefix(1).lowercased() + row.reason.dropFirst())."
    }

    private func earlier(_ row: ReviewRow, _ detail: ReviewDetail) -> some View {
        Panel(title: "Earlier from \(row.title)") {
            VStack(spacing: 6) {
                ForEach(Array(detail.earlier.enumerated()), id: \.offset) { _, earlier in
                    HStack(spacing: 10) {
                        Text(Figures.day(earlier.date, asOf: detail.asOf))
                            .foregroundStyle(Palette.ink2)
                            .frame(width: 92, alignment: .leading)
                        Text(standing(earlier))
                            .foregroundStyle(earlier.decision == .reviewed ? Palette.ink : Palette.ink3)
                            .lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Text(Figures.amount(earlier.amount))
                            .monospacedDigit()
                            .foregroundStyle(Palette.ink2)
                    }
                    .font(.system(size: 12))
                    .accessibilityElement(children: .combine)
                }
            }
        }
    }

    private func standing(_ earlier: ReviewDetail.Earlier) -> String {
        switch earlier.decision {
        case .reviewed: earlier.category?.label ?? "Reviewed"
        case .excluded: "Excluded"
        case .waiting: "\(earlier.category?.label ?? "Nothing suggested"), unchecked"
        case .elsewhere: "Waiting to be told where it went"
        }
    }

    private func actions(_ row: ReviewRow) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            // The keys beside the buttons where they fit whole, under them where they do not.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    decide(row)
                    Spacer(minLength: 8)
                    keys(row)
                }
                VStack(alignment: .leading, spacing: 8) {
                    decide(row)
                    keys(row)
                }
            }
            // A row its bank counts in the balance it reports offers no Exclude, which the
            // desk would refuse; nor does a difference with a bank, which is only confirmed.
            if row.excludable, !row.isReconciling {
                Button("Exclude: not a transaction") {
                    Task { await desk.decisions.make(.exclude(row)) }
                }
                .buttonStyle(QuietButtonStyle())
                .font(.system(size: 12))
                .disabled(desk.decisions.isDeciding)
                .accessibilityIdentifier("review.exclude")
            }
        }
        .padding(.top, 4)
    }

    private func decide(_ row: ReviewRow) -> some View {
        HStack(spacing: 8) {
            Button(decideLabel(row)) {
                if row.isReconciling {
                    Task { await desk.decisions.make(.accept(row)) }
                    return
                }
                guard let chosen else { return }
                Task { await desk.decisions.make(.settling(row, to: chosen)) }
            }
            .buttonStyle(PrimaryButtonStyle())
            .keyboardShortcut(inQueue ? nil : .defaultAction)
            .disabled((chosen == nil && !row.isReconciling) || desk.decisions.isDeciding)
            .accessibilityIdentifier("review.decide")
            if inQueue {
                Button("Skip") { desk.review.skip() }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("review.skip")
            }
        }
        .fixedSize()
    }

    @ViewBuilder
    private func keys(_ row: ReviewRow) -> some View {
        if inQueue {
            KeyHint(text: row.isReconciling ? "↑↓ · Return" : row.excludable ? "↑↓ · Return · C · X" : "↑↓ · Return · C")
                .fixedSize()
        }
    }

    private func decideLabel(_ row: ReviewRow) -> String {
        if row.isReconciling { return "Confirm" }
        guard let chosen else { return "Choose a category" }
        if case .category(let category) = chosen, category == row.suggested {
            return "Accept as \(category.label)"
        }
        if case .account(let account) = chosen {
            return "Move to \(account.name)"
        }
        return "Save as \(chosen.label)"
    }

    private func settled(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 13))
            .foregroundStyle(Palette.ink2)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier("review.detail.settled")
    }
}

/// What a decision made outside the queue did, with its Undo while it is the last.
private struct InlineNotice: View {
    let notice: Notice
    let decisions: Decisions

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: notice.tone == .done ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .foregroundStyle(notice.tone == .done ? Palette.favourable : Palette.warning)
                .accessibilityHidden(true)
            Text(notice.text)
                .font(.system(size: 12))
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("review.notice")
            if decisions.canUndo {
                Button(notice.undoLabel) { Task { await decisions.undo() } }
                    .buttonStyle(QuietButtonStyle())
                    .help(ReviewWords.undoHelp(notice))
                    .accessibilityIdentifier("review.undo")
            }
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(notice.tone == .done ? Palette.background : Palette.warningBackground))
    }
}

/// Every category the viewer's own rows use and every account of theirs the row may move to,
/// narrowed as the person types, and any other category typed in full.
private struct CategoryChooser: View {
    let detail: ReviewDetail
    let choose: (Destination) -> Void
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField("Category, or a full name like Expenses:Garden", text: $text)
                .modifier(FieldStyle(focused: focused, invalid: false))
                .focused($focused)
                .onSubmit(submit)
                .accessibilityIdentifier("review.chooser.field")
            List {
                if !categories.isEmpty {
                    Section("Categories") {
                        ForEach(categories, id: \.name) { category in
                            option(category.label, detail: category.name) { choose(.category(category)) }
                                .accessibilityIdentifier("review.option.\(category.label)")
                        }
                    }
                }
                if !accounts.isEmpty {
                    Section("Your accounts") {
                        ForEach(accounts) { account in
                            option(account.name, detail: nil) { choose(.account(account)) }
                                .accessibilityIdentifier("review.option.\(account.name)")
                        }
                    }
                }
                if let typed {
                    Section("New") {
                        option("Use \(typed)", detail: nil) { choose(.typed(typed)) }
                            .accessibilityIdentifier("review.option.typed")
                    }
                }
            }
            .listStyle(.sidebar)
            .scrollContentBackground(.hidden)
            .frame(height: 240)
        }
        .padding(12)
        .frame(width: 300)
        .onAppear { focused = true }
    }

    private func option(_ label: String, detail: String?, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Text(label).foregroundStyle(Palette.ink)
                Spacer()
                if let detail {
                    Text(detail)
                        .font(.system(size: 11))
                        .foregroundStyle(Palette.ink3)
                        .lineLimit(1)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var words: String { text.trimmingCharacters(in: .whitespaces) }

    private var categories: [CategoryRef] {
        guard !words.isEmpty else { return detail.categories }
        return detail.categories.filter {
            $0.label.localizedCaseInsensitiveContains(words) || $0.name.localizedCaseInsensitiveContains(words)
        }
    }

    private var accounts: [AccountRef] {
        guard !words.isEmpty else { return detail.accounts }
        return detail.accounts.filter { $0.name.localizedCaseInsensitiveContains(words) }
    }

    /// A full category name typed that is not one already listed: the engine checks it.
    private var typed: String? {
        guard words.contains(":"), !detail.categories.contains(where: { $0.name == words }) else { return nil }
        return words
    }

    private func submit() {
        if let typed {
            choose(.typed(typed))
        } else if let first = categories.first {
            choose(.category(first))
        } else if let first = accounts.first {
            choose(.account(first))
        }
    }
}

/// A choice among a few, on or off.
private struct ChoiceChip: View {
    let label: String
    let on: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                if on {
                    Image(systemName: "checkmark")
                        .font(.system(size: 9, weight: .bold))
                        .accessibilityHidden(true)
                }
                Text(label).lineLimit(1)
            }
            .fixedSize()
            .font(.system(size: 12, weight: on ? .semibold : .regular))
            .foregroundStyle(on ? Palette.accent : Palette.ink2)
            .padding(.horizontal, 10)
            .frame(height: 24)
            .background(Capsule().fill(on ? Palette.accentBackground : Palette.surface))
            .overlay(Capsule().strokeBorder(on ? Palette.accent : Palette.lineStrong, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

/// Chips laid out in rows, as many to a row as fit.
private struct FlowChips: Layout {
    let spacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var line: CGFloat = 0
        var widest: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width {
                x = 0
                y += line + spacing
                line = 0
            }
            x += size.width + spacing
            widest = max(widest, x - spacing)
            line = max(line, size.height)
        }
        return CGSize(width: min(widest, width), height: y + line)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var line: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX {
                x = bounds.minX
                y += line + spacing
                line = 0
            }
            subview.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            line = max(line, size.height)
        }
    }
}
