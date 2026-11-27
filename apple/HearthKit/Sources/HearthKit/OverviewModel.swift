import Foundation
import Observation

/// A screen the engine answers in one piece, and says whether newer books are on their way.
public protocol Screen: Decodable, Sendable, Equatable {
    /// Whether newer books are being computed right now.
    var recomputing: Bool { get }
}

extension Overview: Screen {}

/// What the Overview screen shows right now.
public typealias OverviewModel = ScreenModel<Overview>

/// What one screen shows right now, and the loop that keeps it current: the Overview, the
/// accounts, one account.
@MainActor
@Observable
public final class ScreenModel<Content: Screen> {
    public enum State: Equatable, Sendable {
        /// Nothing has been heard from the engine yet.
        case starting
        /// The engine is running and computing the books.
        case loading
        case ready(Content)
        /// The books could not be computed; the sentence says why.
        case failed(String)
        /// The engine has no such screen for this viewer.
        case missing
        /// The engine says nobody is signed in: the member's session ended elsewhere, as when
        /// their password was reset.
        case signedOut
        /// The engine is not running.
        case stopped
    }

    public private(set) var state: State = .starting

    /// What the screen is asked as, and what narrows it.
    public let screen: String
    public private(set) var given: Given

    private let engine: any Asking
    private let pause: @Sendable (Duration) async throws -> Void
    private let signedOut: @MainActor () -> Void

    /// How long to wait before asking again: often while the books are being computed, so the
    /// screen fills the moment they are ready, and seldom once they are, only to notice a
    /// recompute.
    public static var whileLoading: Duration { .milliseconds(300) }
    public static var whileReady: Duration { .seconds(15) }

    public init(
        engine: any Asking,
        screen: String = "overview",
        given: Given = [:],
        pause: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        signedOut: @escaping @MainActor () -> Void = {}
    ) {
        self.engine = engine
        self.screen = screen
        self.given = given
        self.pause = pause
        self.signedOut = signedOut
    }

    /// Asks once and takes the answer in. Returns how long to wait before asking again, or nil
    /// when there is no point in asking again.
    @discardableResult
    public func refresh() async -> Duration? {
        let asked = given
        do {
            let answer = try await engine.ask(screen, with: asked, as: Content.self)
            // An answer to what the screen was asked before it was narrowed again is not its.
            guard asked == given else { return Self.whileLoading }
            switch answer {
            case .ready(let content):
                state = .ready(content)
                return content.recomputing ? Self.whileLoading : Self.whileReady
            case .loading:
                // Figures already on screen stay there while newer ones are computed.
                if case .ready = state { return Self.whileLoading }
                state = .loading
                return Self.whileLoading
            case .failed(let summary):
                state = .failed(summary)
                return Self.whileReady
            case .missing:
                state = .missing
                return nil
            case .signedOut:
                // Nothing on screen may outlast the session it was shown in.
                state = .signedOut
                signedOut()
                return nil
            }
        } catch {
            state = .stopped
            return nil
        }
    }

    /// Asks again with `given` from now on, as a screen whose period or valuation a person
    /// changed: what is on screen stays there until the new answer arrives.
    public func narrow(to given: Given) async {
        guard given != self.given else { return }
        self.given = given
        await refresh()
    }

    /// Keeps the screen current until the task is cancelled or the engine stops.
    public func watch() async {
        while !Task.isCancelled, let wait = await refresh() {
            do {
                try await pause(wait)
            } catch {
                return
            }
        }
    }

    /// What the screen holds, once it holds anything.
    public var content: Content? {
        if case .ready(let content) = state { content } else { nil }
    }
}
