import AppKit
import HearthKit
import SwiftUI

/// Bringing the household's ledger in, and matching accounts to it, one step after another,
/// over the books (`HearthKit.LedgerFlow`). Every sentence is the engine's, or written from
/// what it answered (`LedgerWords`).
struct LedgerSheet: View {
    let flow: LedgerFlow
    /// What "Open My Books" does once the ledger is in: the Overview.
    let openBooks: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            switch flow.step {
            case .question?:
                Question(flow: flow)
            case .checking(let file)?:
                Working(title: LedgerWords.checking, detail: file.lastPathComponent)
            case .checked(let file, let check)?:
                Checked(flow: flow, file: file, check: check)
            case .confirming(let file, _)?:
                Confirming(flow: flow, file: file)
            case .bringingIn?:
                Working(title: LedgerWords.bringingIn, detail: LedgerWords.bringingInDetail)
            case .waitingForAnother?:
                Working(title: LedgerWords.waitingForAnother, detail: LedgerWords.waitingForAnotherDetail)
            case .notBroughtIn(let refused)?:
                Finished(
                    title: LedgerWords.notBroughtIn, detail: refused?.said ?? LedgerWords.failed, flow: flow)
            case .inButBooksFailed?:
                InButBooksFailed(flow: flow, openBooks: openBooks)
            case .matching(let matching)?:
                MatchingStep(flow: flow, matching: matching)
            case .done(let matching)?:
                Done(flow: flow, matching: matching, openBooks: openBooks)
            case .unavailable?, nil:
                Finished(
                    title: "Hearth could not finish this",
                    detail: "Its engine stopped, or would not say how it went. Close this and try again.",
                    flow: flow)
            }
        }
        .font(.system(size: 13))
        .foregroundStyle(Palette.ink)
        .padding(20)
        .frame(width: 480, alignment: .leading)
        .background(Palette.surface)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("ledger.sheet")
    }
}

/// The sheet's heading.
private struct Heading: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 17, weight: .semibold))
            .foregroundStyle(Palette.ink)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityAddTraits(.isHeader)
            .accessibilityIdentifier("ledger.title")
    }
}

/// A plain sentence under the heading.
private struct Said: View {
    let text: String
    var identifier: String? = nil

    var body: some View {
        Text(text)
            .foregroundStyle(Palette.ink2)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier(identifier ?? "")
    }
}

/// The buttons along the sheet's foot: a quiet one on the left, then the others.
private struct Foot<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        HStack(spacing: 8) { content }
            .padding(.top, 8)
    }
}

/// Something the engine is doing now.
private struct Working: View {
    let title: String
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            ProgressView().controlSize(.small).padding(.top, 4)
            VStack(alignment: .leading, spacing: 4) {
                Heading(text: title)
                Said(text: detail)
            }
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("ledger.working")
    }
}

/// One fact of what the file holds, with its mark: passed, or to look at.
private struct Fact<More: View>: View {
    enum Mark { case passed, look, problem, listed }
    let mark: Mark
    let title: String
    let detail: String?
    var identifier: String
    @ViewBuilder var more: More

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(colour)
                .frame(width: 16)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).fontWeight(.semibold).foregroundStyle(Palette.ink)
                if let detail {
                    Text(detail)
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                }
                more
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 6).fill(Palette.background))
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Palette.line, lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(identifier)
    }

    private var symbol: String {
        switch mark {
        case .passed: "checkmark"
        case .look: "exclamationmark"
        case .problem: "xmark"
        case .listed: "list.bullet"
        }
    }

    private var colour: Color {
        switch mark {
        case .passed: Palette.favourable
        case .look: Palette.warning
        case .problem: Palette.unfavourable
        case .listed: Palette.ink3
        }
    }
}

extension Fact where More == EmptyView {
    init(mark: Mark, title: String, detail: String?, identifier: String) {
        self.init(mark: mark, title: title, detail: detail, identifier: identifier) { EmptyView() }
    }
}

/// "Show" and "Hide", as quietly as a link, for a list under a fact.
private struct Showing: View {
    @Binding var shown: Bool
    let identifier: String

    var body: some View {
        Button(shown ? "Hide" : "Show") { shown.toggle() }
            .buttonStyle(QuietButtonStyle())
            .font(.system(size: 12, weight: .medium))
            .accessibilityIdentifier(identifier)
    }
}

/// A short list of lines in a box that scrolls once it is long.
private struct Lines: View {
    let lines: [String]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(line)
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink2)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(.vertical, 4)
        }
        .frame(maxHeight: 120)
        .fixedSize(horizontal: false, vertical: lines.count < 7)
    }
}

/// "Do you keep a beancount ledger?"
private struct Question: View {
    let flow: LedgerFlow
    @State private var yes = true

    var body: some View {
        Heading(text: LedgerWords.question)
        Choosing(title: LedgerWords.yes, detail: LedgerWords.yesDetail, chosen: yes, identifier: "ledger.yes") {
            yes = true
        }
        Choosing(title: LedgerWords.no, detail: LedgerWords.noDetail, chosen: !yes, identifier: "ledger.no") {
            yes = false
        }
        Foot {
            Spacer()
            Button("Continue") {
                if yes { chooseFile(flow) } else { flow.close() }
            }
            .buttonStyle(PrimaryButtonStyle())
            .keyboardShortcut(.defaultAction)
            .accessibilityIdentifier("ledger.continue")
        }
    }
}

/// One answer to a question, chosen or not, as the start screen draws its options.
private struct Choosing: View {
    let title: String
    let detail: String
    let chosen: Bool
    let identifier: String
    let choose: () -> Void

    var body: some View {
        Button(action: choose) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).fontWeight(.semibold).foregroundStyle(Palette.ink)
                Text(detail).font(.system(size: 12)).foregroundStyle(Palette.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 6).fill(chosen ? Palette.accentBackground : Palette.surface))
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .strokeBorder(chosen ? Palette.accent : Palette.line, lineWidth: 1))
            .contentShape(RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(chosen ? .isSelected : [])
        .accessibilityIdentifier(identifier)
    }
}

/// Asks for the ledger's file and has the engine read it. A debug build given `-ledgerFile`
/// reads that instead of asking, as a test does.
@MainActor
func chooseFile(_ flow: LedgerFlow) {
    if let given = DebugLaunch.ledgerFile {
        Task { await flow.check(given) }
        return
    }
    let panel = NSOpenPanel()
    panel.title = "Choose Your Ledger"
    panel.message = "Choose the file your ledger is opened from, the one Fava opens."
    panel.prompt = "Choose"
    panel.canChooseDirectories = false
    panel.allowsMultipleSelection = false
    guard panel.runModal() == .OK, let file = panel.url else { return }
    Task { await flow.check(file) }
}

/// What the file holds, or why it cannot be brought in.
private struct Checked: View {
    let flow: LedgerFlow
    let file: URL
    let check: LedgerCheck
    @State private var showingUnused = false
    @State private var showingAccounts = false

    var body: some View {
        Heading(text: LedgerWords.checkTitle(file: file.lastPathComponent, check))
        if let found = check.found {
            Said(text: LedgerWords.summary(found), identifier: "ledger.summary")
            Fact(
                mark: LedgerWords.checksPass(found) ? .passed : .problem,
                title: LedgerWords.balanceChecks(found), detail: LedgerWords.worth(found),
                identifier: "ledger.checks")
            Fact(
                mark: .listed, title: LedgerWords.kinds(found), detail: nil, identifier: "ledger.accounts"
            ) {
                Showing(shown: $showingAccounts, identifier: "ledger.showAccounts")
                if showingAccounts {
                    Lines(lines: found.accounts.map(\.name))
                }
            }
            if let unused = LedgerWords.unusedTitle(found) {
                Fact(mark: .look, title: unused, detail: LedgerWords.unusedDetail(found), identifier: "ledger.unused") {
                    Showing(shown: $showingUnused, identifier: "ledger.showUnused")
                    if showingUnused {
                        Lines(lines: found.unused.map { "\($0.file), line \($0.line): \($0.what)" })
                    }
                }
            }
        }
        if let refused = check.refused {
            Refusal(text: refused.said)
        }
        Foot {
            Button("Choose Another File\u{2026}") { chooseFile(flow) }
                .buttonStyle(QuietButtonStyle())
                .accessibilityIdentifier("ledger.another")
            Spacer()
            Button("Cancel") { flow.close() }
                .keyboardShortcut(.cancelAction)
                .accessibilityIdentifier("ledger.cancel")
            if check.refused == nil {
                Button("Continue") { flow.confirm() }
                    .buttonStyle(PrimaryButtonStyle())
                    .keyboardShortcut(.defaultAction)
                    .accessibilityIdentifier("ledger.continue")
            }
        }
    }
}

/// Why something cannot be done, in the engine's words.
private struct Refusal: View {
    let text: String

    var body: some View {
        Text(text)
            .foregroundStyle(Palette.ink)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 6).fill(Palette.errorBackground))
            .accessibilityIdentifier("ledger.refused")
    }
}

/// "Bring in household.beancount?": once, and the books are the truth afterwards.
private struct Confirming: View {
    let flow: LedgerFlow
    let file: URL

    var body: some View {
        Heading(text: LedgerWords.confirmTitle(file: file.lastPathComponent))
        Said(text: LedgerWords.confirmDetail)
        Said(text: LedgerWords.once, identifier: "ledger.once")
        Foot {
            Spacer()
            Button("Cancel") { flow.close() }
                .keyboardShortcut(.cancelAction)
                .accessibilityIdentifier("ledger.cancel")
            Button(LedgerWords.bringIn) { Task { await flow.bringIn() } }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .disabled(flow.isWorking)
                .accessibilityIdentifier("ledger.bringIn")
        }
    }
}

/// An end with nothing more to do but close.
private struct Finished: View {
    let title: String
    let detail: String
    let flow: LedgerFlow

    var body: some View {
        Heading(text: title)
        Refusal(text: detail)
        Foot {
            Spacer()
            Button("Close") { flow.close() }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .accessibilityIdentifier("ledger.close")
        }
    }
}

/// Which of the ledger's accounts each connected account is.
private struct MatchingStep: View {
    @Bindable var flow: LedgerFlow
    let matching: Matching

    var body: some View {
        Heading(text: LedgerWords.matchTitle)
        Said(text: LedgerWords.matchDetail)
        if let unsure = matching.unsure {
            Refusal(text: unsure)
        }
        if flow.refusedNow {
            Refusal(text: LedgerWords.changedSince)
        }
        if matching.accounts.count < 4 {
            rows
        } else {
            // More than fit in the smallest window scroll, the sheet's foot staying in view.
            ScrollView { rows }
                .frame(maxHeight: 240)
        }
        if let cannot = LedgerWords.cannotMatch(matching) {
            Said(text: cannot, identifier: "ledger.cannotMatch")
        }
        Foot {
            Spacer()
            Button(LedgerWords.notNow) { flow.close() }
                .keyboardShortcut(.cancelAction)
                .accessibilityIdentifier("ledger.notNow")
            Button(LedgerWords.match) { Task { await flow.match() } }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .disabled(flow.isWorking)
                .accessibilityIdentifier("ledger.match")
        }
    }
}

extension MatchingStep {
    fileprivate var rows: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(matching.accounts) { account in
                MatchRow(flow: flow, account: account)
            }
        }
    }
}

/// One connected account: its name, the ledger's account it is to be, and what that meets.
private struct MatchRow: View {
    @Bindable var flow: LedgerFlow
    let account: ConnectedAccount

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(LedgerWords.name(account)).fontWeight(.semibold)
                    Text(account.institution).font(.system(size: 12)).foregroundStyle(Palette.ink2)
                }
                Spacer(minLength: 8)
                if account.fixed, let matched = account.matched?.account {
                    Text(matched).font(.system(size: 12, weight: .medium))
                } else if !LedgerFlow.open(account).isEmpty {
                    Picker("", selection: choice) {
                        Text(LedgerWords.leaveAsItIs).tag(String?.none)
                        ForEach(LedgerFlow.open(account)) { choice in
                            Text(choice.account).tag(Optional(choice.account))
                        }
                    }
                    .labelsHidden()
                    .pickerStyle(.menu)
                    .fixedSize()
                    .accessibilityIdentifier("ledger.choice.\(account.name)")
                }
            }
            ForEach(notes, id: \.self) { note in
                Text(note)
                    .font(.system(size: 12))
                    .foregroundStyle(Palette.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 6).fill(Palette.background))
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Palette.line, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("ledger.account.\(account.name)")
    }

    private var choice: Binding<String?> {
        Binding(
            get: { flow.chosen[account.id] },
            set: { flow.chosen[account.id] = $0 })
    }

    /// What its choice meets, why nothing is proposed, and that its match is fixed.
    private var notes: [String] {
        var said: [String] = []
        if account.fixed {
            said.append("A decision has been made in Review on its transactions, so its match is fixed.")
        }
        if let chosen = flow.chosen[account.id],
            let facing = account.choices.first(where: { $0.account == chosen })?.facing
        {
            said.append(LedgerWords.note(facing))
        } else if let why = LedgerWords.whyNot(account) {
            said.append(why)
        } else if LedgerFlow.open(account).isEmpty {
            said.append("None of your ledger's accounts can be matched to it.")
        }
        return said
    }
}

/// The ledger is in, but the books could not be worked out from it yet: matching waits on
/// them, and the Overview says why.
private struct InButBooksFailed: View {
    let flow: LedgerFlow
    let openBooks: () -> Void

    var body: some View {
        Heading(text: LedgerWords.inTitle)
        Refusal(text: LedgerWords.booksFailed)
        Foot {
            Spacer()
            Button(LedgerWords.openBooks) {
                flow.close()
                openBooks()
            }
            .buttonStyle(PrimaryButtonStyle())
            .keyboardShortcut(.defaultAction)
            .accessibilityIdentifier("ledger.openBooks")
        }
    }
}

/// What is matched, and what the books hold now.
private struct Done: View {
    let flow: LedgerFlow
    let matching: Matching
    let openBooks: () -> Void
    @State private var showing = false

    var body: some View {
        Heading(text: LedgerWords.doneTitle(matching))
        if matching.totals.of > 0 {
            Fact(
                mark: matching.totals.matched > 0 ? .passed : .look,
                title: LedgerWords.matchedCount(matching),
                detail: LedgerWords.matchedExample(matching), identifier: "ledger.matched"
            ) {
                if matching.totals.matched > 1 {
                    Showing(shown: $showing, identifier: "ledger.showMatched")
                    if showing {
                        Lines(lines: LedgerWords.matchedList(matching))
                    }
                }
            }
            if matching.totals.matched > 0 {
                Fact(
                    mark: .passed, title: LedgerWords.inLedger(matching), detail: LedgerWords.added(matching),
                    identifier: "ledger.inLedger")
            }
        }
        if flow.standing?.bringingIn?.booksFailed == true {
            Refusal(text: LedgerWords.booksFailed)
        }
        if let cannot = LedgerWords.cannotMatch(matching) {
            Said(text: cannot, identifier: "ledger.cannotMatch")
        } else if let unclaimed = LedgerWords.unclaimed(flow.standing) {
            Said(text: unclaimed, identifier: "ledger.unclaimed")
        }
        Foot {
            if matching.accounts.contains(where: { !$0.fixed && !LedgerFlow.open($0).isEmpty }) {
                Button(LedgerWords.change) { flow.change() }
                    .accessibilityIdentifier("ledger.change")
            }
            Spacer()
            Button(LedgerWords.openBooks) {
                flow.close()
                openBooks()
            }
            .buttonStyle(PrimaryButtonStyle())
            .keyboardShortcut(.defaultAction)
            .accessibilityIdentifier("ledger.openBooks")
        }
    }
}
