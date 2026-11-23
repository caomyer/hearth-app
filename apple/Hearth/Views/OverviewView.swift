import HearthKit
import SwiftUI

/// The Overview screen: one hero, the household's net worth, and how it got here.
struct OverviewView: View {
    let model: OverviewModel

    var body: some View {
        Group {
            switch model.state {
            case .starting, .loading:
                VStack(spacing: 12) {
                    ProgressView()
                    Text("Working out the books").foregroundStyle(Palette.ink2)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .accessibilityIdentifier("overview.loading")
            case .ready(let overview):
                if overview.connected {
                    Figures(overview: overview)
                } else {
                    Unavailable(
                        title: "No accounts yet",
                        detail: "Net worth appears once an account is in the books.")
                }
            case .failed(let summary):
                Unavailable(title: "The books could not be worked out", detail: summary)
            case .missing:
                Unavailable(title: "Nothing to show", detail: "There is no overview for you here.")
            case .stopped:
                Unavailable(
                    title: "Hearth's engine stopped", detail: "Quit Hearth and open it again.")
            }
        }
        .task { await model.watch() }
    }
}

private struct Figures: View {
    let overview: Overview

    private var worth: Overview.NetWorth { overview.netWorth }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                Text(heroLabel)
                    .font(.callout)
                    .foregroundStyle(Palette.ink3)
                hero
                    .padding(.top, 4)
                changes
                    .padding(.top, 6)
                if overview.history.count > 1 {
                    NetWorthChart(points: overview.history, currency: overview.currency)
                        .padding(.top, 28)
                }
                Text(freshness)
                    .font(.footnote)
                    .foregroundStyle(Palette.ink3)
                    .padding(.top, 20)
                    .accessibilityIdentifier("overview.freshness")
            }
            .frame(maxWidth: 720, alignment: .leading)
            .padding(.horizontal, 28)
            .padding(.vertical, 26)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// A total that leaves a part of the household out says so, in its own label.
    private var heroLabel: String {
        var label = worth.isFloor ? "Net worth, at least" : "Net worth"
        if worth.isPartial {
            label += ", without " + worth.without.formatted(.list(type: .and))
        }
        return label
    }

    @ViewBuilder
    private var hero: some View {
        if worth.isKnown {
            Text(HearthKit.Figures.total(worth.value, currency: overview.currency))
                .font(.system(size: 46, weight: .semibold))
                .tracking(-1)
                .foregroundStyle(Palette.ink)
                .textSelection(.enabled)
                .accessibilityIdentifier("overview.netWorth")
        } else {
            Text("Not known yet")
                .font(.system(size: 34, weight: .semibold))
                .foregroundStyle(Palette.ink2)
                .accessibilityIdentifier("overview.netWorth")
        }
    }

    @ViewBuilder
    private var changes: some View {
        if worth.isKnown {
            HStack(spacing: 14) {
                if let since = worth.sinceMonthStart {
                    Change(amount: since, currency: overview.currency, over: "since \(monthStart)")
                }
                if let year = worth.overYear {
                    Change(amount: year, currency: overview.currency, over: "in 12 months")
                }
            }
            .font(.callout)
        }
    }

    private var monthStart: String {
        let first = Day(year: overview.asOf.year, month: overview.asOf.month, day: 1)
        return first.date().formatted(.dateTime.month(.abbreviated).day())
    }

    /// When the books were computed and how old their prices are: no figure without its date.
    private var freshness: String {
        var text = "Computed \(HearthKit.Figures.moment(overview.computedAt))"
        if let priced = worth.pricedOn {
            text += ", with prices from \(priced.date().formatted(.dateTime.weekday(.wide).month(.abbreviated).day()))"
        }
        text += "."
        if worth.stalePrices > 0 {
            let count = worth.stalePrices
            text += count == 1 ? " One price is older." : " \(count) prices are older."
        }
        return text
    }
}

/// One change in net worth: arrow, word, amount, period, and colour last.
private struct Change: View {
    let amount: Money
    let currency: String
    let over: String

    var body: some View {
        HStack(spacing: 4) {
            Text(HearthKit.Figures.change(amount, currency: currency))
                .fontWeight(.semibold)
                .foregroundStyle(colour)
            Text(over).foregroundStyle(Palette.ink2)
        }
    }

    /// Rising net worth is favourable, falling unfavourable; no change is neither.
    private var colour: Color {
        switch HearthKit.Figures.direction(amount) {
        case .up: Palette.favourable
        case .down: Palette.unfavourable
        case .none: Palette.ink2
        }
    }
}
