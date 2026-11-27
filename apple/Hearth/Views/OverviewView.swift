import HearthKit
import SwiftUI

/// The Overview screen: one hero, the household's net worth, and how it got here, and what
/// waits for the viewer in Review. The books' view keeps it current, as the sidebar's count
/// reads it on every screen.
struct OverviewView: View {
    let model: OverviewModel

    var body: some View {
        Group {
            switch model.state {
            case .starting, .loading:
                Waiting(text: "Working out the books", identifier: "overview.loading")
            case .signedOut:
                // Gone the moment the engine says so; the window moves on to signing in.
                Color.clear
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
                if overview.review.waiting > 0 {
                    ReviewBanner(waiting: overview.review, currency: overview.currency, asOf: overview.asOf)
                        .padding(.top, 20)
                }
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

/// What waits in Review, and the way there: how many transactions, how much of this month's
/// own spending rests on them, what no total holds until it is placed, and what was booked as
/// not spending, as the person's own page says them.
private struct ReviewBanner: View {
    let waiting: Overview.Waiting
    let currency: String
    let asOf: Day
    @Environment(Navigator.self) private var navigator

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Circle()
                .fill(Palette.warning)
                .frame(width: 8, height: 8)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(count)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                    .accessibilityIdentifier("overview.review.count")
                if let spending = waiting.spending {
                    Text("\(HearthKit.Figures.total(spending, currency: currency)) of \(month)'s spending is not reviewed yet.")
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink2)
                        .accessibilityIdentifier("overview.review.spending")
                }
                ForEach(HearthKit.ReviewWords.unplaced(waiting, month: month, currency: currency), id: \.self) { line in
                    Text(line)
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("overview.review.unplaced")
                }
                if let kept = waiting.notSpending {
                    Text("\(HearthKit.Figures.total(kept, currency: currency)) booked as not spending, such as a retirement contribution, is not counted as spending.")
                        .font(.system(size: 12))
                        .foregroundStyle(Palette.ink2)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("overview.review.not-spending")
                }
            }
            Spacer(minLength: 12)
            Button("Review") { navigator.place = .review }
                .buttonStyle(PrimaryButtonStyle())
                .accessibilityIdentifier("overview.review")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(RoundedRectangle(cornerRadius: 8).fill(Palette.background))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Palette.line, lineWidth: 1))
    }

    private var count: String {
        waiting.waiting == 1 ? "1 transaction to review" : "\(waiting.waiting.formatted()) transactions to review"
    }

    private var month: String {
        Day(year: asOf.year, month: asOf.month, day: 1).date().formatted(.dateTime.month(.wide))
    }
}
