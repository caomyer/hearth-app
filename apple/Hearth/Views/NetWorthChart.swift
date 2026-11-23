import Charts
import HearthKit
import SwiftUI

/// Net worth at the end of each month, ending today. The title says what the chart is of;
/// the line is the data and carries no colour of its own.
struct NetWorthChart: View {
    let points: [Overview.Point]
    let currency: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Net worth, end of each month")
                .font(.headline)
                .foregroundStyle(Palette.ink)
            Chart(points, id: \.day) { point in
                LineMark(
                    x: .value("Month end", point.day.date()),
                    y: .value("Net worth", value(point))
                )
                .foregroundStyle(Palette.chartLine)
                .lineStyle(StrokeStyle(lineWidth: 2))
                if point.day == points.last?.day {
                    PointMark(
                        x: .value("Month end", point.day.date()),
                        y: .value("Net worth", value(point))
                    )
                    .foregroundStyle(Palette.chartLine)
                    .symbolSize(50)
                }
            }
            .chartYScale(domain: .automatic(includesZero: false))
            .chartYAxis {
                AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { mark in
                    AxisGridLine().foregroundStyle(Palette.chartGrid)
                    AxisValueLabel {
                        if let amount = mark.as(Double.self) {
                            Text(compact(amount)).foregroundStyle(Palette.ink3)
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .stride(by: .month, count: 3)) { _ in
                    AxisValueLabel(format: .dateTime.month(.abbreviated))
                        .foregroundStyle(Palette.ink3)
                }
            }
            .frame(height: 220)
            .accessibilityIdentifier("overview.history")
        }
    }

    /// A chart places marks by a binary number; the figures people read never pass through one.
    private func value(_ point: Overview.Point) -> Double {
        NSDecimalNumber(decimal: point.value.amount).doubleValue
    }

    private func compact(_ amount: Double) -> String {
        amount.formatted(
            .currency(code: currency).notation(.compactName).precision(.fractionLength(0...1)))
    }
}
