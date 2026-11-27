import Charts
import HearthKit
import SwiftUI

/// Holdings: what the investment accounts the viewer may see hold, each holding's units, the
/// price it is valued at with that price's source and day, its value, what it cost and what it
/// has gained, at market value or at cost. Choosing a holding shows where its price came from,
/// and its history, beside the list. Read-only: no price or lot is changed here.
struct HoldingsView: View {
    let model: ScreenModel<Holdings>

    @Environment(Navigator.self) private var navigator
    /// The holding beside the list, by its account and its symbol.
    @State private var selected: HoldingKey?
    @State private var openedStart = false
    @AppStorage("holdings.atCost") private var atCost = false

    var body: some View {
        content
            .task { await model.watch() }
            .inspector(isPresented: inspecting) {
                if let selected, let holdings = model.content, let found = holdings.find(selected) {
                    PriceDetail(
                        desk: navigator.desk, held: found.held, account: found.account,
                        currency: holdings.currency
                    )
                    .id(selected)
                    // Opened at its narrowest, so that beside the list's narrowest fit and the
                    // sidebar it fits the smallest window: an inspector given more takes it from
                    // the sidebar, which is then cut. It can be drawn wider.
                    .inspectorColumnWidth(min: 240, ideal: 240, max: 420)
                }
            }
            .onChange(of: model.content) { _, holdings in
                // A holding no longer held, or no longer the viewer's to see, is not shown.
                if let selected, holdings?.find(selected) == nil { self.selected = nil }
                // A debug build asked to open on a holding opens it once it is listed.
                guard !openedStart, let symbol = StartScreen.holding, let holdings else { return }
                openedStart = true
                selected = holdings.accounts.lazy.flatMap { account in
                    account.held.map { HoldingKey(account: account.id, symbol: $0.symbol) }
                }.first { $0.symbol == symbol }
            }
    }

    private var inspecting: Binding<Bool> {
        Binding(get: { selected != nil }, set: { shown in if !shown { selected = nil } })
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .starting, .loading:
            Waiting(text: "Working out the books", identifier: "holdings.loading")
        case .signedOut:
            Color.clear
        case .failed(let summary):
            Unavailable(title: "The books could not be worked out", detail: summary)
        case .missing:
            Unavailable(title: "Nothing to show", detail: "There are no holdings for you here.")
        case .stopped:
            Unavailable(title: "Hearth's engine stopped", detail: "Quit Hearth and open it again.")
        case .ready(let holdings):
            HoldingsList(holdings: holdings, atCost: $atCost, selected: $selected)
        }
    }
}

/// One holding of one account: a security held in two accounts is two holdings.
struct HoldingKey: Hashable {
    let account: String
    let symbol: String
}

extension Holdings {
    fileprivate func find(_ key: HoldingKey) -> (account: HeldAccount, held: Held)? {
        guard let account = accounts.first(where: { $0.id == key.account }),
            let held = account.held.first(where: { $0.symbol == key.symbol && !$0.isCash })
        else { return nil }
        return (account, held)
    }
}

/// The columns, as wide as their widest figure, so every account's rows line up.
private enum Column {
    static let units: CGFloat = 96
    static let price: CGFloat = 140
    static let value: CGFloat = 112
    static let gain: CGFloat = 136
    static let spacing: CGFloat = 14
    /// The narrowest a holding's name is given before the figures beside it give way.
    static let name: CGFloat = 120
}

/// How many columns the list is wide enough for. Narrower, the units go under each holding's
/// name first, as the design has a narrow page drop that column first; narrower still, the
/// gain goes under the value. Nothing is ever dropped.
private enum Fit: Comparable {
    case narrow, medium, wide

    init(width: CGFloat) {
        let figures = Column.price + Column.value + Column.gain + 3 * Column.spacing
        if width >= Column.name + figures + Column.units + Column.spacing + 16 {
            self = .wide
        } else if width >= Column.name + figures + 16 {
            self = .medium
        } else {
            self = .narrow
        }
    }

    /// The narrowest the list is drawn, its margins included: a name beside a price and a value.
    static let narrowest = Column.name + Column.price + Column.gain + 2 * Column.spacing + 16 + 56

    var unitsColumn: Bool { self == .wide }
    var gainColumn: Bool { self != .narrow }
}

extension EnvironmentValues {
    /// How many columns the holdings list is wide enough for.
    @Entry fileprivate var fit = Fit.wide
}

private struct HoldingsList: View {
    let holdings: Holdings
    @Binding var atCost: Bool
    @Binding var selected: HoldingKey?

    /// Nothing until the window says: a list first drawn narrow never asks the window for more.
    @State private var width: CGFloat = 0

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                HoldingsHead(holdings: holdings, atCost: $atCost)
                    .padding(.bottom, 16)
                Flags(holdings: holdings)
                if holdings.accounts.isEmpty {
                    Text("No investment positions in the accounts you can see.")
                        .foregroundStyle(Palette.ink2)
                        .padding(.vertical, 12)
                        .accessibilityIdentifier("holdings.none")
                } else {
                    ColumnHeads(atCost: atCost)
                    ForEach(holdings.accounts) { account in
                        AccountHoldings(
                            account: account, holdings: holdings, atCost: atCost, selected: $selected)
                    }
                }
                ForEach(holdings.withheld) { withheld in
                    WithheldLine(withheld: withheld)
                        .padding(.top, 16)
                }
                if let provenance {
                    Text(provenance)
                        .font(.footnote)
                        .foregroundStyle(Palette.ink3)
                        .padding(.top, 24)
                        .accessibilityIdentifier("holdings.provenance")
                }
            }
            .font(.system(size: 13))
            .frame(maxWidth: 980, alignment: .leading)
            .environment(\.fit, Fit(width: min(width - 56, 980)))
            .padding(.horizontal, 28)
            .padding(.vertical, 24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // The width the window gives the list, never what the list would like to be: the list
        // asks for no more than its narrowest fit, and its columns follow what it is given.
        .frame(minWidth: Fit.narrowest, maxWidth: .infinity)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
    }

    /// Where the prices shown come from, and when the newest was fetched, as the net worth
    /// page's footer says it.
    private var provenance: String? {
        let prices = holdings.accounts.flatMap(\.held).compactMap(\.price)
        guard let fetched = prices.map(\.fetchedAt).max() else { return nil }
        let sources = Set(prices.map(\.source)).sorted().map(Holdings.source)
        let named = sources.count == 1 ? "Source: \(sources[0])" : "Sources: \(sources.joined(separator: "; "))"
        return "Each price is stored with its source and time. \(named). Fetched \(Figures.moment(fetched))."
    }
}

/// The title, the switch between market value and cost, and the total of what is shown.
private struct HoldingsHead: View {
    let holdings: Holdings
    @Binding var atCost: Bool

    private var totals: Holdings.Totals { holdings.totals }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text("Holdings")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                Spacer(minLength: 16)
                Picker("Value holdings", selection: $atCost) {
                    Text("At market value").tag(false)
                    Text("At cost").tag(true)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                .accessibilityIdentifier("holdings.basis")
            }
            Text("What the investment accounts you can see hold, and where every price comes from.")
                .foregroundStyle(Palette.ink2)
                .padding(.top, 2)
            if !holdings.accounts.isEmpty {
                Text(atCost ? "Positions you can see, at cost" : "Positions you can see")
                    .font(.callout)
                    .foregroundStyle(Palette.ink3)
                    .padding(.top, 16)
                    .accessibilityIdentifier("holdings.basisLabel")
                HStack(alignment: .firstTextBaseline, spacing: 14) {
                    // Not made selectable, as the Overview's figure is: this one changes with the
                    // switch above, and must read as what it says now.
                    Text(Holdings.total(totals, atCost: atCost, currency: holdings.currency))
                        .font(.system(size: 34, weight: .semibold))
                        .tracking(-0.5)
                        .monospacedDigit()
                        .foregroundStyle(Palette.ink)
                        .accessibilityIdentifier("holdings.total")
                    GainText(gain: totals.gain, currency: holdings.currency, whole: true)
                        .font(.callout.weight(.semibold))
                        .accessibilityIdentifier("holdings.gain")
                }
                .padding(.top, 2)
                ForEach(Holdings.leavesOut(totals, atCost: atCost), id: \.self) { note in
                    Text(note)
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink3)
                        .padding(.top, 4)
                }
                if let priced = pricedLine {
                    Text(priced)
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink3)
                        .padding(.top, 4)
                        .accessibilityIdentifier("holdings.priced")
                }
            }
        }
    }

    /// The day the prices are from, as the net worth page heads its positions.
    private var pricedLine: String? {
        guard let day = holdings.pricedOn else { return nil }
        let when = Figures.weekday(day, asOf: day)
        let stale = holdings.accounts.flatMap(\.held).contains { $0.freshness == .stale }
        return "Prices as of \(when) close" + (stale ? ", except where marked." : ".")
    }
}

/// A stale or missing price, said once above the list, as the net worth page says it.
private struct Flags: View {
    let holdings: Holdings

    var body: some View {
        // An old price is said once, whichever accounts hold it; a holding with no price, once
        // for each account whose totals it leaves a floor.
        var said = Set<String>()
        let flagged = holdings.accounts.flatMap { account in
            account.held.filter { $0.freshness != .current }.map { Flagged(account: account, held: $0) }
        }.filter { $0.held.freshness == .unpriced || said.insert($0.held.symbol).inserted }
        if !flagged.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(flagged) { found in
                    Flag(account: found.account, held: found.held, currency: holdings.currency)
                }
            }
            .padding(.bottom, 16)
        }
    }

    private struct Flagged: Identifiable {
        let account: Holdings.HeldAccount
        let held: Holdings.Held

        var id: HoldingKey { HoldingKey(account: account.id, symbol: held.symbol) }
    }
}

private struct Flag: View {
    let account: Holdings.HeldAccount
    let held: Holdings.Held
    let currency: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: held.freshness == .stale ? "clock" : "exclamationmark.triangle")
                .foregroundStyle(Palette.warning)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(headline).font(.system(size: 13, weight: .semibold)).foregroundStyle(Palette.ink)
                Text(detail).font(.system(size: 12)).foregroundStyle(Palette.ink2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 10).fill(Palette.warningBackground))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("holdings.flag.\(held.symbol)")
    }

    private var headline: String {
        guard let price = held.price else { return "\(held.title) has no price at all." }
        return "\(held.title) has no price since \(Figures.weekday(price.day, asOf: price.day))."
    }

    private var detail: String {
        guard let price = held.price else {
            return "Its \(Figures.units(held.units)) units are left out, so every total that includes \(account.name) is a floor, labelled \u{201C}at least\u{201D}."
        }
        let close = Figures.cents(price.value, currency: currency)
        return "Its value uses that day\u{2019}s close of \(close), \(price.ageText), so it may be off. Every price is stored with its source and day, so this one will not pass as current."
    }
}

private struct ColumnHeads: View {
    let atCost: Bool
    @Environment(\.fit) private var fit

    var body: some View {
        HStack(spacing: Column.spacing) {
            Text("Holding").frame(maxWidth: .infinity, alignment: .leading)
            if fit.unitsColumn {
                Text("Units").frame(width: Column.units, alignment: .trailing)
            }
            Text("Price").frame(width: Column.price, alignment: .trailing)
            if fit.gainColumn {
                Text(atCost ? "Cost" : "Value").frame(width: Column.value, alignment: .trailing)
                Text("Gain not yet realised").frame(width: Column.gain, alignment: .trailing)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
            } else {
                Text(atCost ? "Cost and gain" : "Value and gain").frame(width: Column.gain, alignment: .trailing)
            }
        }
        .font(.system(size: 12, weight: .medium))
        .foregroundStyle(Palette.ink3)
        .padding(.horizontal, 8)
        .padding(.bottom, 6)
        .overlay(alignment: .bottom) { Rectangle().fill(Palette.lineStrong).frame(height: 1) }
    }
}

/// One account: its name, which opens it, whose it is, its totals, and what it holds.
private struct AccountHoldings: View {
    let account: Holdings.HeldAccount
    let holdings: Holdings
    let atCost: Bool
    @Binding var selected: HoldingKey?

    @Environment(Navigator.self) private var navigator
    @Environment(\.fit) private var fit

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline, spacing: Column.spacing) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Button(account.name) {
                        navigator.open(AccountRef(id: account.id, name: account.name))
                    }
                    .buttonStyle(QuietButtonStyle())
                    .font(.system(size: 13, weight: .semibold))
                    .help("Open \(account.name)")
                    .accessibilityIdentifier("holdings.account.\(account.name)")
                    if let whose = account.whose {
                        Text("· \(whose)").foregroundStyle(Palette.ink3)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if account.known {
                    let total = Text(Holdings.total(account.totals, atCost: atCost, currency: holdings.currency))
                        .monospacedDigit()
                        .fontWeight(.semibold)
                        .foregroundStyle(Palette.ink)
                        .accessibilityIdentifier("holdings.account.total.\(account.name)")
                    let gain = GainText(gain: account.totals.gain, currency: holdings.currency, whole: true)
                    if fit.gainColumn {
                        total.frame(width: Column.value, alignment: .trailing)
                        gain.frame(width: Column.gain, alignment: .trailing)
                    } else {
                        VStack(alignment: .trailing, spacing: 2) {
                            total
                            gain.font(.system(size: 12))
                        }
                        .frame(width: Column.gain, alignment: .trailing)
                    }
                }
            }
            .padding(.horizontal, 8)
            .padding(.top, 16)
            .padding(.bottom, 6)
            if let absence = account.absence {
                NotKnown(account: account, absence: absence)
            }
            ForEach(account.held) { held in
                let key = HoldingKey(account: account.id, symbol: held.symbol)
                HoldingRow(
                    held: held, currency: holdings.currency, atCost: atCost, chosen: selected == key
                ) {
                    selected = held.isCash ? nil : key
                }
                Divider().overlay(Palette.line)
            }
        }
    }
}

/// An account whose holdings are not known: what it holds is not nothing, and its owner is
/// told why, with the way to let Hearth read them where that is what it waits on.
private struct NotKnown: View {
    let account: Holdings.HeldAccount
    let absence: String

    @Environment(Navigator.self) private var navigator
    @Environment(\.openSettings) private var openSettings

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: "clock")
                .foregroundStyle(Palette.warning)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(absence).foregroundStyle(Palette.ink2)
                if notReading {
                    HStack(spacing: 8) {
                        Text("Hearth is not reading what your brokerages hold. You can let it in Settings.")
                            .foregroundStyle(Palette.ink3)
                        Button("Open Settings") { openSettings() }
                            .buttonStyle(QuietButtonStyle())
                    }
                    .font(.system(size: 12))
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 12)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("holdings.notKnown.\(account.name)")
    }

    /// The owner's brokerage waits to be asked what it holds, and this engine may not ask.
    private var notReading: Bool {
        guard account.why == "investment", let banks = navigator.desk.sync.banks else { return false }
        return !banks.asking.contains("holdings")
    }
}

/// One holding: chosen, it shows where its price came from beside the list.
private struct HoldingRow: View {
    let held: Holdings.Held
    let currency: String
    let atCost: Bool
    let chosen: Bool
    let choose: () -> Void
    @Environment(\.fit) private var fit

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: Column.spacing) {
            VStack(alignment: .leading, spacing: 2) {
                Text(held.title).foregroundStyle(Palette.ink)
                Text(fit.unitsColumn || held.isCash ? held.subtitle : "\(held.subtitle) · \(Figures.units(held.units)) units")
                    .font(.system(size: 12)).foregroundStyle(Palette.ink3)
            }
            .frame(minWidth: Column.name, maxWidth: .infinity, alignment: .leading)
            if fit.unitsColumn {
                Text(held.isCash ? "\u{2013}" : Figures.units(held.units))
                    .monospacedDigit()
                    .foregroundStyle(held.isCash ? Palette.ink3 : Palette.ink)
                    .frame(width: Column.units, alignment: .trailing)
            }
            price.frame(width: Column.price, alignment: .trailing)
            if fit.gainColumn {
                value.frame(width: Column.value, alignment: .trailing)
                gain.frame(width: Column.gain, alignment: .trailing)
            } else {
                VStack(alignment: .trailing, spacing: 3) {
                    value
                    gain.font(.system(size: 12))
                }
                .frame(width: Column.gain, alignment: .trailing)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 9)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(chosen ? Palette.accentBackground : (held.freshness == .current ? Color.clear : Palette.warningBackground.opacity(0.55))))
        .contentShape(Rectangle())
        .onTapGesture(perform: choose)
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(held.isCash ? [] : [.isButton])
        .accessibilityAddTraits(chosen ? [.isSelected] : [])
        .accessibilityAction(.default, choose)
        .accessibilityIdentifier("holdings.row.\(held.symbol)")
    }

    @ViewBuilder
    private var price: some View {
        if held.isCash {
            Text("\u{2013}").foregroundStyle(Palette.ink3)
        } else if let price = held.price {
            VStack(alignment: .trailing, spacing: 2) {
                Text(Figures.cents(price.value, currency: currency)).monospacedDigit().foregroundStyle(Palette.ink)
                if held.freshness == .stale {
                    Label("\(price.close()), \(price.ageText)", systemImage: "clock")
                        .labelStyle(TrailingGlyph())
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.warning)
                } else {
                    Text(price.close() + (price.source == "hand" ? ", by hand" : ""))
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink3)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("holdings.price")
        } else {
            Label("No price", systemImage: "exclamationmark.triangle")
                .labelStyle(TrailingGlyph())
                .foregroundStyle(Palette.warning)
                .accessibilityIdentifier("holdings.price")
        }
    }

    @ViewBuilder
    private var value: some View {
        if atCost {
            if let cost = held.cost {
                Text(Figures.cents(cost, currency: currency)).monospacedDigit().foregroundStyle(Palette.ink)
                    .accessibilityIdentifier("holdings.value")
            } else {
                Text("No cost in the books").font(.system(size: 12)).foregroundStyle(Palette.ink3)
                    .multilineTextAlignment(.trailing)
                    .accessibilityIdentifier("holdings.value")
            }
        } else if let value = held.value {
            VStack(alignment: .trailing, spacing: 2) {
                Text(Figures.cents(value, currency: currency)).monospacedDigit().foregroundStyle(Palette.ink)
                if held.freshness == .stale {
                    Text("may be off").font(.system(size: 12)).foregroundStyle(Palette.warning)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("holdings.value")
        } else {
            Text("Not counted").foregroundStyle(Palette.warning)
                .accessibilityIdentifier("holdings.value")
        }
    }

    @ViewBuilder
    private var gain: some View {
        if held.isCash {
            Text("\u{2013}").foregroundStyle(Palette.ink3)
        } else if let gain = held.gain {
            GainText(gain: gain, currency: currency, whole: false)
                .accessibilityIdentifier("holdings.gainOf")
        } else {
            // No gain is shown against a cost or a value that is not known: never one against zero.
            Text(held.value == nil ? "No price" : "No cost in the books")
                .font(.system(size: 12))
                .foregroundStyle(Palette.ink3)
                .accessibilityIdentifier("holdings.gainOf")
        }
    }
}

/// A gain or a loss: its arrow, sign and word, then its colour.
struct GainText: View {
    let gain: Money
    let currency: String
    let whole: Bool

    var body: some View {
        Text(Figures.gain(gain, currency: currency, whole: whole))
            .monospacedDigit()
            .foregroundStyle(colour)
    }

    private var colour: Color {
        switch Figures.gainDirection(gain, whole: whole) {
        case .up: Palette.favourable
        case .down: Palette.unfavourable
        case .none: Palette.ink2
        }
    }
}

/// A glyph after its words, as a figure's flag reads in a right-aligned column.
private struct TrailingGlyph: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 4) {
            configuration.icon.accessibilityHidden(true)
            configuration.title
        }
    }
}

/// Someone's positions the viewer may not see, said as the net worth page says it.
private struct WithheldLine: View {
    let withheld: Holdings.Withheld

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "lock")
                .foregroundStyle(Palette.ink3)
                .accessibilityHidden(true)
            Text(withheld.sentence)
                .foregroundStyle(Palette.ink2)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 10).fill(Palette.background))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Palette.line, lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("holdings.withheld")
    }
}

/// Where a holding's price came from and when, and every price Hearth holds of it.
private struct PriceDetail: View {
    let held: Holdings.Held
    let account: Holdings.HeldAccount
    let currency: String
    /// Asked once for the holding it was made for, and kept while it is shown.
    @State private var model: ScreenModel<PriceHistory>

    init(desk: Desk, held: Holdings.Held, account: Holdings.HeldAccount, currency: String) {
        self.held = held
        self.account = account
        self.currency = currency
        _model = State(initialValue: desk.priceHistory(held.symbol))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text(held.title)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                    .accessibilityIdentifier("price.title")
                Text([held.ticker ?? "No ticker", account.name].joined(separator: " · "))
                    .foregroundStyle(Palette.ink3)
                    .padding(.top, 2)
                used.padding(.top, 16)
                history.padding(.top, 24)
            }
            .font(.system(size: 13))
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .background(Palette.surface)
        .task { await model.watch() }
    }

    /// The price the holding is valued at: what it is, the day it is for, its source, and
    /// when Hearth fetched it.
    @ViewBuilder
    private var used: some View {
        if let price = held.price {
            VStack(alignment: .leading, spacing: 8) {
                Text("Price used").font(.system(size: 12, weight: .semibold)).foregroundStyle(Palette.ink2)
                Text(Figures.cents(price.value, currency: currency))
                    .font(.system(size: 24, weight: .semibold))
                    .monospacedDigit()
                    .foregroundStyle(Palette.ink)
                    .textSelection(.enabled)
                    .accessibilityIdentifier("price.used")
                Fact(name: "For", value: "\(Figures.weekday(price.day, asOf: price.day)) close", identifier: "price.day")
                Fact(name: "Source", value: price.sourceText, identifier: "price.source")
                Fact(name: "Fetched", value: Figures.moment(price.fetchedAt), identifier: "price.fetched")
                if held.freshness == .stale {
                    Label("Its source has sent no newer close: it is \(price.ageText), so its value may be off.", systemImage: "clock")
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.warning)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else {
            Label(
                "It has no price at all, so it is left out of every total, which is then a floor.",
                systemImage: "exclamationmark.triangle"
            )
            .foregroundStyle(Palette.warning)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier("price.used")
        }
    }

    @ViewBuilder
    private var history: some View {
        switch model.state {
        case .starting, .loading:
            ProgressView().controlSize(.small)
        case .ready(let found):
            PriceChart(history: found, title: "Price of \(held.ticker ?? held.title), each close Hearth holds")
        case .missing, .failed, .signedOut, .stopped:
            Text("Its price history is not available.")
                .foregroundStyle(Palette.ink3)
        }
    }
}

private struct Fact: View {
    let name: String
    let value: String
    let identifier: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(name).foregroundStyle(Palette.ink3).frame(width: 62, alignment: .leading)
            Text(value).foregroundStyle(Palette.ink).fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(identifier)
    }
}

/// A security's price, each close Hearth holds of it. The title says what it is; the line is
/// the data and carries no colour of its own.
private struct PriceChart: View {
    let history: PriceHistory
    let title: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.headline)
                .foregroundStyle(Palette.ink)
                .fixedSize(horizontal: false, vertical: true)
            if history.closes.count > 1 {
                Chart(history.closes, id: \.day) { close in
                    LineMark(x: .value("Day", close.day.date()), y: .value("Price", value(close)))
                        .foregroundStyle(Palette.chartLine)
                        .lineStyle(StrokeStyle(lineWidth: 1.5))
                }
                .chartYScale(domain: .automatic(includesZero: false))
                .chartYAxis {
                    AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { mark in
                        AxisGridLine().foregroundStyle(Palette.chartGrid)
                        AxisValueLabel {
                            if let amount = mark.as(Double.self) {
                                Text(amount.formatted(.currency(code: history.currency).precision(.fractionLength(0...2))))
                                    .foregroundStyle(Palette.ink3)
                            }
                        }
                    }
                }
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 4)) { _ in
                        AxisValueLabel(format: .dateTime.month(.abbreviated).day())
                            .foregroundStyle(Palette.ink3)
                    }
                }
                .frame(height: 180)
                .accessibilityLabel(title)
                .accessibilityIdentifier("price.chart")
            }
            Text(summary)
                .font(.system(size: 12))
                .foregroundStyle(Palette.ink3)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("price.summary")
        }
    }

    /// How many closes, over which days, and from where.
    private var summary: String {
        guard let first = history.closes.first, let last = history.closes.last else {
            return "Hearth holds no price of it yet."
        }
        let sources = Set(history.closes.map(\.source)).sorted().map(Holdings.source)
        let from = "\(sources.count == 1 ? "Source" : "Sources"): \(sources.joined(separator: "; "))."
        if history.closes.count == 1 {
            return "Hearth holds one close of it, for \(Figures.day(first.day, asOf: history.asOf)). \(from)"
        }
        let span = Figures.span(from: first.day, to: last.day)
        return "\(history.closes.count.formatted()) closes, \(span). \(from)"
    }

    /// A chart places marks by a binary number; the figures people read never pass through one.
    private func value(_ close: PriceHistory.Close) -> Double {
        NSDecimalNumber(decimal: close.price.amount).doubleValue
    }
}
