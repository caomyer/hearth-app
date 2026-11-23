import HearthKit
import SwiftUI

/// The window: the choice of books, signing in, or the books themselves.
struct RootView: View {
    let session: Session

    var body: some View {
        Group {
            switch session.screen {
            case .starting, .choosing:
                // The start sheet sits over an empty window: nothing is open yet.
                Color.clear
            case .opening:
                Waiting(text: "Opening your books", identifier: "session.opening")
            case .signIn(let problem):
                SignInView(session: session, problem: problem)
            case .open(let books):
                BooksView(session: session, isSample: books == .sample)
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
        .sheet(isPresented: .constant(isChoosing)) {
            StartSheet(session: session, offersSetup: offersSetup)
                .interactiveDismissDisabled()
        }
        .task { await session.start() }
    }

    private var isChoosing: Bool {
        if case .choosing = session.screen { true } else { false }
    }

    private var offersSetup: Bool {
        if case .choosing(let offers) = session.screen { offers } else { false }
    }
}

/// The books, open: the places you can go on the left, the place you are on the right.
private struct BooksView: View {
    let session: Session
    let isSample: Bool
    @State private var place: Place? = .overview

    enum Place: Hashable {
        case overview
    }

    var body: some View {
        NavigationSplitView {
            List(selection: $place) {
                Label("Overview", systemImage: "house").tag(Place.overview)
            }
            .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 260)
        } detail: {
            VStack(spacing: 0) {
                if isSample {
                    SampleBanner()
                }
                if let overview = session.overview {
                    OverviewView(model: overview)
                } else {
                    Waiting(text: "Opening your books", identifier: "session.opening")
                }
            }
            .background(Palette.surface)
            // The banner is the first thing under the toolbar, and the toolbar would otherwise
            // take its colour.
            .toolbarBackground(Palette.surface, for: .windowToolbar)
            .toolbarBackgroundVisibility(.visible, for: .windowToolbar)
        }
    }
}

/// Says, on every screen, that the figures are not anyone's real money.
private struct SampleBanner: View {
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
