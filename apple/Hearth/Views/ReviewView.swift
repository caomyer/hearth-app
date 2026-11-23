import HearthKit
import SwiftUI

/// Review: what waits for the viewer, groups first, largest first, a group that is one
/// decision offered as one accept, the rest row by row; and beside it, the item they are on.
struct ReviewView: View {
    let desk: Desk
    @State private var section: Section = .queue
    @State private var text = ""
    @State private var choosing = false
    @FocusState private var queueFocused: Bool

    enum Section: Hashable {
        case queue
        case elsewhere
    }

    private var review: ReviewModel { desk.review }

    var body: some View {
        content
            .searchable(text: $text, placement: .toolbar, prompt: "Merchant or description")
            .task(id: text) {
                // Asked once typing pauses, not at every key.
                guard text != review.query.text else { return }
                try? await Task.sleep(for: .milliseconds(250))
                guard !Task.isCancelled else { return }
                var query = review.query
                query.text = text
                await review.apply(query)
            }
            .task { await review.watch() }
            .focusedSceneValue(\.reviewCommands, commands)
    }

    @ViewBuilder
    private var content: some View {
        switch review.state {
        case .starting, .loading:
            Waiting(text: "Working out the books", identifier: "review.loading")
        case .signedOut:
            Color.clear
        case .failed(let summary):
            Unavailable(title: "The books could not be worked out", detail: summary)
        case .missing:
            Unavailable(title: "Nothing to show", detail: "There is nothing for you to review here.")
        case .stopped:
            Unavailable(title: "Hearth's engine stopped", detail: "Quit Hearth and open it again.")
        case .ready:
            if let queue = review.queue {
                GeometryReader { proxy in
                HStack(spacing: 0) {
                    VStack(spacing: 0) {
                        ReviewHead(review: review, queue: queue, section: $section)
                            .padding(.horizontal, 24)
                            .padding(.top, 20)
                            .padding(.bottom, 12)
                        Divider().overlay(Palette.line)
                        switch section {
                        case .queue:
                            QueueList(review: review, focused: $queueFocused)
                        case .elsewhere:
                            WhereList(desk: desk)
                        }
                        NoticeBar(decisions: desk.decisions)
                    }
                    .frame(maxWidth: .infinity)
                    if section == .queue {
                        Divider().overlay(Palette.line)
                        // The queue and the item it is on share the width, the queue the larger.
                        ItemPane(desk: desk, choosing: $choosing)
                            .frame(width: min(max(proxy.size.width * 0.44, 300), 440))
                    }
                }
                }
                .onAppear { queueFocused = true }
                .onChange(of: queue.elsewhere.rows) { _, rows in
                    if rows == 0 { section = .queue }
                }
            }
        }
    }

    /// The Review menu's commands, while the queue has the keyboard: never while a field
    /// does, so a letter typed there is only ever a letter.
    private var commands: ReviewCommands? {
        guard queueFocused, section == .queue, review.state == .ready, !desk.decisions.isDeciding else {
            return nil
        }
        return ReviewCommands(
            next: { review.move(1) },
            previous: { review.move(-1) },
            accept: { Task { await review.acceptSelected() } },
            changeCategory: review.selectedRow == nil ? nil : { choosing = true },
            exclude: review.canExcludeSelected ? { if let row = review.selectedRow { Task { await review.exclude(row) } } } : nil,
            skip: { review.skip() })
    }
}

/// The hero count, what it moves, how far along the viewer is, and what narrows the queue.
private struct ReviewHead: View {
    let review: ReviewModel
    let queue: ReviewQueue
    @Binding var section: ReviewView.Section

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("Review")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                Spacer()
                if queue.elsewhere.rows > 0 || section == .elsewhere {
                    Picker("Show", selection: $section) {
                        Text("To review").tag(ReviewView.Section.queue)
                        Text("Where did it go?").tag(ReviewView.Section.elsewhere)
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .fixedSize()
                    .accessibilityIdentifier("review.section")
                }
            }
            if section == .queue {
                if queue.remaining.rows > 0 {
                    figures
                    tools
                } else {
                    caughtUp
                }
            }
        }
    }

    private var figures: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .bottom, spacing: 24) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Waiting for you")
                        .font(.callout)
                        .foregroundStyle(Palette.ink3)
                    Text(queue.remaining.rows.formatted())
                        .font(.system(size: 34, weight: .semibold))
                        .tracking(-0.5)
                        .monospacedDigit()
                        .foregroundStyle(Palette.ink)
                        .accessibilityIdentifier("review.remaining")
                }
                Spacer(minLength: 0)
                Progress(reviewed: queue.reviewed, total: queue.total, queue: queue)
                    .frame(width: 170)
                    .padding(.bottom, 8)
            }
            Text(summary)
                .font(.system(size: 12))
                .foregroundStyle(Palette.ink2)
                .monospacedDigit()
                .fixedSize(horizontal: false, vertical: true)
            if queue.elsewhere.rows > 0 {
                Button {
                    section = .elsewhere
                } label: {
                    Text(elsewhere)
                }
                .buttonStyle(QuietButtonStyle())
                .font(.system(size: 12))
                .padding(.top, 2)
                .accessibilityIdentifier("review.elsewhere")
            }
        }
    }

    private var summary: String {
        var text = "\(Figures.cents(queue.remaining.moved, currency: queue.currency)) moved, across "
        text += queue.groupsWaiting == 1 ? "1 group." : "\(queue.groupsWaiting.formatted()) groups."
        if let oldest = queue.oldest {
            text += " Oldest from \(Figures.day(oldest, asOf: queue.asOf))."
        }
        return text
    }

    private var elsewhere: String {
        let rows = queue.elsewhere.rows
        return "\(rows.formatted()) more \(rows == 1 ? "needs" : "need") to know where the money went"
    }

    private var tools: some View {
        VStack(alignment: .leading, spacing: 6) {
            // One row where it fits, two where the window is narrow.
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    viewPicker
                    narrowing
                    Spacer(minLength: 0)
                }
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 8) {
                        viewPicker
                        OrderMenu(review: review)
                        Spacer(minLength: 0)
                    }
                    HStack(spacing: 8) {
                        filters
                        Spacer(minLength: 0)
                    }
                }
            }
            if queue.filtered {
                HStack(spacing: 6) {
                    Text(filtered)
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink2)
                        .monospacedDigit()
                    Button("Show everything") {
                        var query = ReviewQuery()
                        query.order = review.query.order
                        Task { await review.apply(query) }
                    }
                    .buttonStyle(QuietButtonStyle())
                    .font(.system(size: 12))
                }
            }
            if queue.changed > 0 || queue.revised > 0 {
                Text(cameBack)
                    .font(.system(size: 11))
                    .foregroundStyle(Palette.ink3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var viewPicker: some View {
        Picker("View", selection: Binding(get: { review.oneByOne }, set: { review.showOneByOne($0) })) {
            Text("By group").tag(false)
            Text("One by one").tag(true)
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .fixedSize()
        .accessibilityIdentifier("review.view")
    }

    @ViewBuilder
    private var narrowing: some View {
        OrderMenu(review: review)
        filters
    }

    @ViewBuilder
    private var filters: some View {
        ChoiceMenu(
            title: "All accounts", symbol: "building.columns", choices: queue.accounts,
            chosen: review.query.account
        ) { choice in
            var query = review.query
            query.account = choice
            Task { await review.apply(query) }
        }
        .accessibilityIdentifier("review.account")
        ChoiceMenu(
            title: "All categories", symbol: "tag", choices: queue.categories,
            chosen: review.query.category
        ) { choice in
            var query = review.query
            query.category = choice
            Task { await review.apply(query) }
        }
        .accessibilityIdentifier("review.category")
    }

    private var filtered: String {
        guard queue.shown.rows > 0 else { return "Nothing waiting matches this filter." }
        let moved = Figures.cents(queue.shown.moved, currency: queue.currency)
        return "Showing \(queue.shown.rows.formatted()) of \(queue.remaining.rows.formatted()) waiting, \(moved) moved."
    }

    private var cameBack: String {
        var said: [String] = []
        if queue.changed > 0 {
            said.append(
                "\(ReviewWords.transactions(queue.changed)) came back because the suggestion changed after you accepted it.")
        }
        if queue.revised > 0 {
            said.append(
                "\(ReviewWords.transactions(queue.revised)) came back because the bank changed \(queue.revised == 1 ? "it" : "them") after \(queue.revised == 1 ? "it was" : "they were") reviewed.")
        }
        return said.joined(separator: " ")
    }

    private var caughtUp: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 18))
                .foregroundStyle(Palette.favourable)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(queue.total > 0 || queue.elsewhere.rows > 0 ? "All caught up" : "Nothing to review")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                    .accessibilityIdentifier("review.caughtUp")
                Text(caughtUpWords)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 6)
    }

    private var caughtUpWords: String {
        if queue.elsewhere.rows > 0 {
            let rows = queue.elsewhere.rows
            return "Every transaction has been checked except \(rows.formatted()) whose other side Hearth does not see. New ones wait here, counted at their suggested category, until you look at them."
        }
        return "Nothing in your accounts is waiting for review. New transactions wait here, counted at their suggested category, until you look at them."
    }
}

/// How many are reviewed of the total, as the desk counts them.
private struct Progress: View {
    let reviewed: Int
    let total: Int
    let queue: ReviewQueue

    var body: some View {
        VStack(alignment: .trailing, spacing: 6) {
            Text("\(reviewed.formatted()) of \(total.formatted()) reviewed")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(Palette.ink2)
                .monospacedDigit()
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule().fill(Palette.line)
                    Capsule()
                        .fill(Palette.favourable)
                        .frame(width: total > 0 ? proxy.size.width * CGFloat(reviewed) / CGFloat(total) : 0)
                }
            }
            .frame(height: 6)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(reviewed.formatted()) of \(total.formatted()) reviewed")
        .accessibilityIdentifier("review.progress")
    }
}

/// What the queue puts first.
private struct OrderMenu: View {
    let review: ReviewModel

    var body: some View {
        Menu {
            Picker("Put first", selection: Binding(get: { review.query.order }, set: choose)) {
                ForEach(ReviewOrder.allCases, id: \.self) { order in
                    Text(order.label).tag(order)
                }
            }
            .pickerStyle(.inline)
        } label: {
            Label("Put first: \(review.query.order.label)", systemImage: "arrow.up.arrow.down")
                .labelStyle(.titleAndIcon)
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .font(.system(size: 12))
        .help("What to put first")
        .accessibilityIdentifier("review.order")
    }

    private func choose(_ order: ReviewOrder) {
        var query = review.query
        query.order = order
        Task { await review.apply(query) }
    }
}

/// One way to narrow the queue: every value of it, with how many rows wait under each.
private struct ChoiceMenu: View {
    let title: String
    let symbol: String
    let choices: [ReviewQueue.Choice]
    let chosen: ReviewQueue.Choice?
    let choose: (ReviewQueue.Choice?) -> Void

    var body: some View {
        Menu {
            Button(title) { choose(nil) }
            Divider()
            ForEach(choices) { choice in
                Button("\(choice.label) (\(choice.rows.formatted()))") { choose(choice) }
            }
        } label: {
            Label(chosen?.label ?? title, systemImage: symbol)
                .labelStyle(.titleAndIcon)
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .font(.system(size: 12))
    }
}

/// The queue: each group, and its rows when it is open; or every row, one by one.
private struct QueueList: View {
    @Bindable var review: ReviewModel
    var focused: FocusState<Bool>.Binding

    var body: some View {
        if let queue = review.queue, !queue.rows.isEmpty {
            ScrollViewReader { proxy in
                List(selection: $review.selection) {
                    if review.oneByOne {
                        Section {
                            ForEach(queue.rows) { row in
                                QueueRow(row: row, currency: queue.currency, asOf: queue.asOf, showsGroup: true)
                                    .tag(ReviewItem.row(row.id))
                                    .id(ReviewItem.row(row.id))
                            }
                        } header: {
                            Text("One by one, \(review.query.order.label.lowercased()) first")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(Palette.ink2)
                        }
                    } else {
                        ForEach(queue.groups) { group in
                            GroupLine(review: review, group: group, currency: queue.currency)
                                .tag(ReviewItem.group(group.name))
                                .id(ReviewItem.group(group.name))
                            if review.isOpen(group) {
                                ForEach(review.rows(of: group)) { row in
                                    QueueRow(row: row, currency: queue.currency, asOf: queue.asOf, showsGroup: false)
                                        .padding(.leading, 14)
                                        .tag(ReviewItem.row(row.id))
                                        .id(ReviewItem.row(row.id))
                                }
                            }
                        }
                    }
                }
                .listStyle(.inset)
                .scrollContentBackground(.hidden)
                .background(Palette.surface)
                .focused(focused)
                .accessibilityIdentifier("review.list")
                .onChange(of: review.selection) { _, item in
                    // The next item after a decision can be a long way down. Scrolled once the
                    // list has taken in what changed, never while it is still doing so.
                    guard let item else { return }
                    DispatchQueue.main.async {
                        withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo(item) }
                    }
                }
            }
        } else {
            Spacer()
        }
    }
}

/// A group: what it is, how many and how much, and its accept when it is one decision.
private struct GroupLine: View {
    let review: ReviewModel
    let group: ReviewGroup
    let currency: String

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Button {
                review.toggle(group)
            } label: {
                Image(systemName: "chevron.right")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Palette.ink3)
                    .rotationEffect(.degrees(review.isOpen(group) ? 90 : 0))
                    .frame(width: 14, height: 20)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(review.isOpen(group) ? "Hide its transactions" : "Show its transactions")
            VStack(alignment: .leading, spacing: 2) {
                Text("\(group.name) · \(group.size.formatted())")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1)
                    .accessibilityIdentifier("review.groupName")
                Text(caption)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.ink3)
                    .monospacedDigit()
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            if group.oneDecision {
                Button("Accept \(group.rows.count.formatted())") {
                    Task { await review.accept(group) }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(review.decisions.isDeciding)
                .help("Accept every one as \(group.suggested.first?.label ?? "suggested")")
                .accessibilityIdentifier("review.acceptGroup.\(group.name)")
            }
        }
        .padding(.vertical, 5)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("review.group.\(group.name)")
    }

    private var caption: String {
        let total = Figures.cents(group.total, currency: currency)
        if group.oneDecision, let suggested = group.suggested.first {
            return "\(total) · \(suggested.label)"
        }
        if group.suggested.isEmpty {
            return "\(total) · Nothing suggested; each needs a look"
        }
        return "\(total) · Not one kind; each needs its own look"
    }
}

/// One row waiting: its merchant, day, why it is suggested, its account, the suggestion and
/// the amount.
private struct QueueRow: View {
    let row: ReviewRow
    let currency: String
    let asOf: Day
    let showsGroup: Bool

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(row.title)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Palette.ink)
                    .lineLimit(1)
                    .accessibilityIdentifier("review.payee")
                Text(caption)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.ink3)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(row.suggested?.label ?? "None")
                .font(.system(size: 12))
                .foregroundStyle(row.suggested == nil ? Palette.ink3 : Palette.ink2)
                .lineLimit(1)
                .frame(width: 104, alignment: .leading)
            Text(Figures.amount(row.amount))
                .font(.system(size: 13))
                .monospacedDigit()
                .foregroundStyle(row.amount.amount > 0 ? Palette.favourable : Palette.ink)
                .frame(width: 84, alignment: .trailing)
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("review.row.\(row.id)")
    }

    private var caption: String {
        var parts = [Figures.day(row.date, asOf: asOf), row.reason]
        if showsGroup { parts.append(row.group) }
        if let account = row.account { parts.append(account.name) }
        return parts.joined(separator: " · ")
    }
}

/// What the last decision did, and its Undo while it is the last: pinned under the queue, as
/// the next item can be a long way from the one decided.
private struct NoticeBar: View {
    let decisions: Decisions

    var body: some View {
        if let notice = decisions.notice {
            VStack(spacing: 0) {
                Divider().overlay(Palette.line)
                HStack(alignment: .firstTextBaseline, spacing: 10) {
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
                        Button(notice.undoLabel) {
                            Task { await decisions.undo() }
                        }
                        .buttonStyle(QuietButtonStyle())
                        .help(ReviewWords.undoHelp(notice))
                        .accessibilityIdentifier("review.undo")
                    }
                }
                .padding(.horizontal, 24)
                .padding(.vertical, 10)
            }
            .background(Palette.surface)
        }
    }
}

/// The item the person is on: a row's detail and its decision, or a group's.
private struct ItemPane: View {
    let desk: Desk
    @Binding var choosing: Bool

    var body: some View {
        @Bindable var review = desk.review
        Group {
            if let row = review.selectedRow {
                RowDetail(
                    desk: desk, id: row.id, shown: row, chosen: $review.chosen, choosing: $choosing,
                    inQueue: true
                )
                .id(row.id)
            } else if let group = desk.review.selectedGroup {
                GroupDetail(review: desk.review, group: group, currency: desk.review.queue?.currency ?? "USD")
                    .id(group.name)
            } else {
                Color.clear
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Palette.surface)
    }
}

/// A group, chosen: what one decision would settle, or why there is none.
private struct GroupDetail: View {
    let review: ReviewModel
    let group: ReviewGroup
    let currency: String

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(ReviewWords.transactions(group.size))
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink3)
                    Text(Figures.cents(group.total, currency: currency))
                        .font(.system(size: 32, weight: .semibold))
                        .tracking(-0.5)
                        .monospacedDigit()
                        .foregroundStyle(Palette.ink)
                    Text(group.name)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                }
                Panel(title: "Suggested") {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(suggested)
                            .font(.system(size: 13, weight: .medium))
                            .foregroundStyle(group.suggested.isEmpty ? Palette.ink3 : Palette.ink)
                        Text(explained)
                            .font(.system(size: 12))
                            .foregroundStyle(Palette.ink2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                HStack(spacing: 8) {
                    if group.oneDecision {
                        Button("Accept \(group.rows.count.formatted()) as \(group.suggested.first?.label ?? "suggested")") {
                            Task { await review.accept(group) }
                        }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(review.decisions.isDeciding)
                        .accessibilityIdentifier("review.decideGroup")
                    }
                    Button(review.isOpen(group) ? "Hide them" : "Show them") { review.toggle(group) }
                        .buttonStyle(.bordered)
                    Spacer(minLength: 0)
                    KeyHint(text: group.oneDecision ? "↑↓ · Return" : "↑↓ · Return opens it")
                }
            }
            .padding(22)
        }
    }

    private var suggested: String {
        switch group.suggested.count {
        case 0: "Nothing suggested"
        case 1: group.suggested[0].label
        case 2, 3: "Varies: " + group.suggested.map(\.label).formatted(.list(type: .and))
        default: "Varies across \(group.suggested.count) categories"
        }
    }

    private var explained: String {
        if group.oneDecision {
            return "Each has the same suggestion, so one decision settles them. Least sure: \(group.leastSure.lowercased())."
        }
        if group.suggested.isEmpty {
            return "Nothing could be suggested; each needs a look."
        }
        return "Not one kind of transaction, so there is no accepting them together; each needs its own look."
    }
}

/// A box with a heading, as the pages' panels are.
struct Panel<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Palette.ink2)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(Palette.background))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Palette.line, lineWidth: 1))
    }
}

/// The keys that work here, said quietly.
struct KeyHint: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 11))
            .foregroundStyle(Palette.ink3)
            .lineLimit(1)
            .accessibilityHidden(true)
    }
}

/// The Review menu's commands, offered while the queue has the keyboard.
struct ReviewCommands {
    let next: () -> Void
    let previous: () -> Void
    let accept: () -> Void
    /// Nil where there is no row to change.
    let changeCategory: (() -> Void)?
    /// Nil where the desk would refuse it.
    let exclude: (() -> Void)?
    let skip: () -> Void
}

extension FocusedValues {
    @Entry var reviewCommands: ReviewCommands?
}
