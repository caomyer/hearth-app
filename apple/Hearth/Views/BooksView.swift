import HearthKit
import SwiftUI

/// A place in the books the sidebar leads to.
enum Place: Hashable {
    case overview
    case review
    case transactions
    case holdings
    case accounts
    case account(String)
}

/// Where the window is in the books, and the way anything on screen takes it elsewhere: an
/// account's name opens the account, a category opens the journal narrowed to it.
@MainActor
@Observable
final class Navigator {
    var place: Place? = .overview
    let desk: Desk
    @ObservationIgnored private var pages: [String: AccountScreen] = [:]

    init(desk: Desk, place: Place = .overview) {
        self.desk = desk
        self.place = place
    }

    func open(_ account: AccountRef) {
        place = .account(account.id)
    }

    /// The journal, narrowed to `category` over the period it already covers.
    func show(_ category: CategoryRef) {
        place = .transactions
        let period = desk.transactions.filter.period
        Task { await desk.transactions.apply(JournalFilter(period: period, category: category)) }
    }

    /// One account's own page, made the first time it is opened and kept while the books are.
    func page(for id: String, named name: String) -> AccountScreen {
        if let page = pages[id] { return page }
        let page = AccountScreen(
            account: desk.account(id), journal: desk.journal(of: AccountRef(id: id, name: name)))
        pages[id] = page
        return page
    }
}

/// What an account's page asks the engine: the account, and its own journal.
struct AccountScreen {
    let account: ScreenModel<AccountPage>
    let journal: JournalModel
}

/// The books, open: the places you can go on the left, the place you are on the right.
struct BooksView: View {
    let isSample: Bool
    @State private var navigator: Navigator
    @State private var openedStart = false
    @Environment(\.undoManager) private var undoManager
    @Environment(\.openSettings) private var openSettings

    init(desk: Desk, isSample: Bool) {
        self.isSample = isSample
        _navigator = State(initialValue: Navigator(desk: desk, place: StartScreen.requested ?? .overview))
    }

    var body: some View {
        NavigationSplitView {
            Sidebar(navigator: navigator)
                // Opened at its narrowest, which every screen's narrowest fit and its panel are
                // measured against at the smallest window; it can be drawn wider.
                .navigationSplitViewColumnWidth(min: 240, ideal: 240, max: 340)
        } detail: {
            VStack(spacing: 0) {
                if isSample {
                    SampleBanner()
                }
                detail
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .background(Palette.surface)
            // The banner is the first thing under the toolbar, and the toolbar would otherwise
            // take its colour.
            .toolbarBackground(Palette.surface, for: .windowToolbar)
            .toolbarBackgroundVisibility(.visible, for: .windowToolbar)
            .toolbar {
                // On every screen of the books: how fresh they are, and asking the banks again.
                ToolbarItem(placement: .primaryAction) {
                    SyncControl(model: navigator.desk.sync, isSample: isSample)
                }
            }
        }
        .environment(navigator)
        .task { await navigator.desk.overview.watch() }
        .task { await navigator.desk.accounts.watch() }
        .task { await navigator.desk.sync.watch() }
        .task {
            // A debug build asked to open on Settings opens it over the books.
            if StartScreen.settings { openSettings() }
        }
        .onChange(of: navigator.desk.decisions.notice) { _, notice in
            offerUndo(notice, decisions: navigator.desk.decisions)
        }
        .onChange(of: navigator.desk.accounts.content?.groups) { _, groups in
            // A debug build asked to open on an account by name opens it once it is listed.
            guard !openedStart, let name = StartScreen.account,
                let found = groups?.flatMap(\.accounts).first(where: { $0.name == name })
            else { return }
            openedStart = true
            navigator.place = .account(found.id)
        }
    }

    /// Edit > Undo, and Command-Z, take back exactly the last review decision while it is the
    /// last, as the Undo beside it does; never one before it, and never an Undo.
    private func offerUndo(_ notice: Notice?, decisions: Decisions) {
        guard let undoManager else { return }
        undoManager.removeAllActions(withTarget: decisions)
        guard notice?.ruling != nil else { return }
        undoManager.registerUndo(withTarget: decisions) { decisions in
            Task { @MainActor in await decisions.undo() }
        }
        undoManager.setActionName("Review Decision")
    }

    @ViewBuilder
    private var detail: some View {
        switch navigator.place ?? .overview {
        case .overview:
            OverviewView(model: navigator.desk.overview)
        case .review:
            ReviewView(desk: navigator.desk)
                .id("review")
        case .transactions:
            TransactionsView(model: navigator.desk.transactions, accounts: navigator.desk.accounts.content)
                .id("transactions")
        case .holdings:
            HoldingsView(model: navigator.desk.holdings)
        case .accounts:
            AccountsView(model: navigator.desk.accounts)
        case .account(let id):
            let name = navigator.desk.accounts.content?.account(id)?.name ?? ""
            AccountView(screen: navigator.page(for: id, named: name))
                .id(id)
        }
    }
}

/// The places in the books, then every account the viewer may see, grouped, each with its
/// balance in whole units and a mark when it needs its owner.
private struct Sidebar: View {
    @Bindable var navigator: Navigator

    /// Everything waiting on the viewer, those waiting to be told where their money went
    /// among them, as the pages' own count is.
    private var waiting: Int { navigator.desk.overview.content?.review.waiting ?? 0 }

    var body: some View {
        List(selection: $navigator.place) {
            Section {
                Label("Overview", systemImage: "house")
                    .tag(Place.overview)
                    .accessibilityIdentifier("sidebar.overview")
                Label("Review", systemImage: "checklist")
                    .badge(waiting)
                    .tag(Place.review)
                    .accessibilityIdentifier("sidebar.review")
                Label("Transactions", systemImage: "list.bullet.rectangle")
                    .tag(Place.transactions)
                    .accessibilityIdentifier("sidebar.transactions")
                Label("Holdings", systemImage: "chart.line.uptrend.xyaxis")
                    .tag(Place.holdings)
                    .accessibilityIdentifier("sidebar.holdings")
                Label("Accounts", systemImage: "building.columns")
                    .tag(Place.accounts)
                    .accessibilityIdentifier("sidebar.accounts")
            }
            if let accounts = navigator.desk.accounts.content {
                ForEach(accounts.groups) { group in
                    Section(group.name) {
                        ForEach(group.accounts) { account in
                            SidebarAccount(account: account, currency: accounts.currency)
                                .tag(Place.account(account.id))
                        }
                    }
                }
            }
        }
        .listStyle(.sidebar)
    }
}

private struct SidebarAccount: View {
    let account: Account
    let currency: String

    var body: some View {
        HStack(spacing: 6) {
            // The name takes whatever the balance leaves, and is cut only when nothing is left.
            Text(account.name)
                .lineLimit(1)
                .truncationMode(.tail)
                .layoutPriority(1)
                .help(account.name)
            Spacer(minLength: 6)
            if account.attention {
                Image(systemName: "exclamationmark.circle.fill")
                    .foregroundStyle(Palette.warning)
                    .help("Sign in to its bank again")
                    .accessibilityLabel("Needs you")
            }
            trailing
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("sidebar.account.\(account.name)")
    }

    @ViewBuilder
    private var trailing: some View {
        if let text = account.sidebarText(currency: currency) {
            Text(text)
                .monospacedDigit()
                .foregroundStyle(account.standing == .failed ? Palette.unfavourable : Palette.ink2)
                .fixedSize()
        } else {
            Image(systemName: StandingMark.symbol(account.standing))
                .foregroundStyle(Palette.ink3)
                .help(account.status(asOf: Day(Date()), currency: currency))
                .accessibilityLabel(account.status(asOf: Day(Date()), currency: currency))
        }
    }
}

/// The glyph and colour that go with what a balance rests on, never without its words.
enum StandingMark {
    static func symbol(_ standing: Account.Standing) -> String {
        switch standing {
        case .matches, .holdings, .checked: "checkmark.circle"
        case .unchecked: "minus.circle"
        case .notKnown: "clock"
        case .arriving: "arrow.down.circle"
        case .unseen: "eye.slash"
        case .failed: "exclamationmark.triangle"
        case .withheld: "lock"
        }
    }

    static func colour(_ standing: Account.Standing) -> Color {
        switch standing {
        case .matches, .holdings, .checked: Palette.favourable
        case .failed: Palette.unfavourable
        case .notKnown, .arriving: Palette.warning
        case .unchecked, .unseen, .withheld: Palette.ink3
        }
    }
}
