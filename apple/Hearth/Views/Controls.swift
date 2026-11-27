import SwiftUI

/// Hearth's mark, a house with a flame in it, drawn from the pages' own (`i-mark` in
/// `src/hearth/templates/_sprite.html`) on its 24-point grid.
struct Mark: View {
    var body: some View {
        Canvas { context, size in
            let scale = min(size.width, size.height) / 24
            context.scaleBy(x: scale, y: scale)
            context.stroke(Self.house, with: .foreground, style: StrokeStyle(lineWidth: 1.8, lineJoin: .round))
            context.fill(Self.flame, with: .foreground)
        }
        .accessibilityHidden(true)
    }

    private static let house = Path { path in
        path.move(to: CGPoint(x: 3.5, y: 10.6))
        path.addLine(to: CGPoint(x: 12, y: 3.8))
        path.addLine(to: CGPoint(x: 20.5, y: 10.6))
        path.addLine(to: CGPoint(x: 20.5, y: 20))
        path.addArc(tangent1End: CGPoint(x: 20.5, y: 20.9), tangent2End: CGPoint(x: 19.6, y: 20.9), radius: 0.9)
        path.addLine(to: CGPoint(x: 4.4, y: 20.9))
        path.addArc(tangent1End: CGPoint(x: 3.5, y: 20.9), tangent2End: CGPoint(x: 3.5, y: 20), radius: 0.9)
        path.closeSubpath()
    }

    private static let flame = Path { path in
        path.move(to: CGPoint(x: 12, y: 18.4))
        path.addCurve(to: CGPoint(x: 8.9, y: 15.4), control1: CGPoint(x: 10.1, y: 18.4), control2: CGPoint(x: 8.9, y: 17.1))
        path.addCurve(to: CGPoint(x: 11.1, y: 10.8), control1: CGPoint(x: 8.9, y: 13.5), control2: CGPoint(x: 10.5, y: 12.7))
        path.addCurve(to: CGPoint(x: 12.6, y: 12.7), control1: CGPoint(x: 11.3, y: 11.7), control2: CGPoint(x: 12.0, y: 12.3))
        path.addCurve(to: CGPoint(x: 13.3, y: 10.8), control1: CGPoint(x: 13.1, y: 12.1), control2: CGPoint(x: 13.3, y: 11.5))
        path.addCurve(to: CGPoint(x: 15.1, y: 14.5), control1: CGPoint(x: 14.6, y: 11.8), control2: CGPoint(x: 15.1, y: 13.5))
        path.addCurve(to: CGPoint(x: 12, y: 18.4), control1: CGPoint(x: 15.1, y: 16.8), control2: CGPoint(x: 13.8, y: 18.4))
        path.closeSubpath()
    }
}

/// The one thing to do on a screen: accent, as blue is kept for what can be acted on.
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .semibold))
            .lineLimit(1)
            .fixedSize()
            .foregroundStyle(isEnabled ? Palette.onAccent : Palette.ink3)
            .padding(.horizontal, 14)
            .frame(minHeight: 30)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(isEnabled ? Palette.accent : Palette.line)
                    .opacity(configuration.isPressed ? 0.85 : 1))
            .contentShape(RoundedRectangle(cornerRadius: 6))
    }
}

/// Something else that can be done, said as quietly as a link.
struct QuietButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 13, weight: .medium))
            .foregroundStyle(Palette.accent)
            .opacity(configuration.isPressed ? 0.7 : 1)
            .contentShape(Rectangle())
    }
}

/// A field a person types into: the control boundary, and the accent while it has focus.
struct FieldStyle: ViewModifier {
    let focused: Bool
    let invalid: Bool

    func body(content: Content) -> some View {
        content
            .textFieldStyle(.plain)
            .font(.system(size: 13))
            .foregroundStyle(Palette.ink)
            .padding(.horizontal, 10)
            .frame(height: 30)
            .background(RoundedRectangle(cornerRadius: 6).fill(Palette.surface))
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .strokeBorder(focused ? Palette.accent : invalid ? Palette.unfavourable : Palette.control, lineWidth: focused ? 2 : 1))
    }
}

/// How many rows a list is given when it is first drawn; the rest follow on the next update.
///
/// A SwiftUI `List` on the Mac is an `NSTableView` whose row heights AppKit measures lazily,
/// 200 rows around the row it needs (`NSTableRowHeightData`, `_cacheRowSpansAroundRow:`).
/// Filled with more than that on its first update, the table resizes itself in the middle of
/// measuring one span and starts measuring the next inside it, which AppKit logs as "a
/// reentrant operation in its NSTableView delegate" and says will become an assert. A table
/// already holding rows takes any number more without it, so no list is first drawn with more.
enum FirstFill {
    static let rows = 100
}

/// A screen with one thing happening on it, and nothing to read yet.
struct Waiting: View {
    let text: String
    let identifier: String

    var body: some View {
        VStack(spacing: 12) {
            ProgressView().controlSize(.regular)
            Text(text).foregroundStyle(Palette.ink2)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(identifier)
    }
}
