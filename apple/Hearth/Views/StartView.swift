import HearthKit
import SwiftUI

/// The first thing a person is asked: which books to open. A household hearth already keeps on
/// this Mac is offered first; nothing about it is said before someone signs in to it.
///
/// It is the window's own content, as signing in is, not a sheet: a sheet is modal, and a
/// window with a modal sheet over it cannot be closed or quit until the sheet is answered.
struct StartView: View {
    let session: Session
    let offersSetup: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if offersSetup {
                Text("Hearth is already set up on this Mac")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                Text("Open your household's books and sign in as yourself.")
                    .foregroundStyle(Palette.ink2)
                HStack(spacing: 8) {
                    Button("Try a sample instead") { choose(.sample) }
                        .buttonStyle(QuietButtonStyle())
                        .accessibilityIdentifier("start.sample")
                    Spacer()
                    Button("Use this setup") { choose(.own) }
                        .buttonStyle(PrimaryButtonStyle())
                        .keyboardShortcut(.defaultAction)
                        .accessibilityIdentifier("start.setup")
                }
                .padding(.top, 6)
            } else {
                Text("How do you want to start?")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                Option(title: "Try a sample", detail: "Explore Hearth with example data.")
                HStack {
                    Spacer()
                    Button("Continue") { choose(.sample) }
                        .buttonStyle(PrimaryButtonStyle())
                        .keyboardShortcut(.defaultAction)
                        .accessibilityIdentifier("start.sample")
                }
                .padding(.top, 6)
            }
        }
        .font(.system(size: 13))
        .padding(20)
        .frame(width: 400, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10).fill(Palette.surface))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Palette.line, lineWidth: 1))
        .padding(.top, 120)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("start")
    }

    private func choose(_ books: Books) {
        Task { await session.choose(books) }
    }
}

/// One way to start, chosen.
private struct Option: View {
    let title: String
    let detail: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).fontWeight(.semibold).foregroundStyle(Palette.ink)
            Text(detail).font(.system(size: 12)).foregroundStyle(Palette.ink2)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(Palette.accentBackground))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Palette.accent, lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isSelected)
    }
}
