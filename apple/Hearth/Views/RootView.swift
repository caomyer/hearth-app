import HearthKit
import SwiftUI

/// The window: the choice of books, signing in, or the books themselves.
struct RootView: View {
    let session: Session

    var body: some View {
        Group {
            switch session.screen {
            case .starting:
                Color.clear
            case .choosing(let offersSetup):
                StartView(session: session, offersSetup: offersSetup)
            case .opening:
                Waiting(text: "Opening your books", identifier: "session.opening")
            case .signIn(let problem):
                SignInView(session: session, problem: problem)
            case .open(let books):
                if let desk = session.desk {
                    BooksView(desk: desk, isSample: books == .sample)
                        // New books, or the same ones from an engine started again, are a new view
                        // with its own screens, never the last one's kept on.
                        .id(ObjectIdentifier(desk))
                } else {
                    Waiting(text: "Opening your books", identifier: "session.opening")
                }
            case .unavailable(.engineMissing):
                Unavailable(
                    title: "Hearth could not start",
                    detail: "Its engine is missing from the app. Install Hearth again.")
            case .unavailable(.engineStopped):
                Unavailable(title: "Hearth's engine stopped", detail: "Quit Hearth and open it again.")
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Palette.background)
        .toolbarBackground(Palette.background, for: .windowToolbar)
        .navigationTitle("Hearth")
        .task {
            if let books = DebugLaunch.books {
                await session.choose(books)
            } else {
                await session.start()
            }
        }
    }
}

/// Says, on every screen, that the figures are not anyone's real money.
struct SampleBanner: View {
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "sparkles")
            Text("Sample household. Every figure and name is invented.")
            Spacer()
        }
        .font(.callout)
        .foregroundStyle(Palette.warning)
        .padding(.horizontal, 28)
        .padding(.vertical, 8)
        .background(Palette.warningBackground)
        .accessibilityIdentifier("sample.banner")
    }
}

/// A screen with nothing to show, and one sentence saying why.
struct Unavailable: View {
    let title: String
    let detail: String

    var body: some View {
        VStack(spacing: 6) {
            Text(title).font(.title3.weight(.semibold)).foregroundStyle(Palette.ink)
            Text(detail).foregroundStyle(Palette.ink2)
        }
        .multilineTextAlignment(.center)
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("unavailable")
    }
}
