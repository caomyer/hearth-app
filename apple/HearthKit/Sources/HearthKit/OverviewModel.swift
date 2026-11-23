import Foundation
import Observation

/// What the Overview screen shows right now, and the loop that keeps it current.
@MainActor
@Observable
public final class OverviewModel {
    public enum State: Equatable, Sendable {
        /// Nothing has been heard from the engine yet.
        case starting
        /// The engine is running and computing the books.
        case loading
        case ready(Overview)
        /// The books could not be computed; the sentence says why.
        case failed(String)
        /// The engine has no overview for this viewer.
        case missing
        /// The engine says nobody is signed in: the member's session ended elsewhere, as when
        /// their password was reset.
        case signedOut
        /// The engine is not running.
        case stopped
    }

    public private(set) var state: State = .starting

    private let engine: any Asking
    private let pause: @Sendable (Duration) async throws -> Void
    private let signedOut: @MainActor () -> Void

    /// How long to wait before asking again: often while the books are being computed, so the
    /// screen fills the moment they are ready, and seldom once they are, only to notice a
    /// recompute.
    public static let whileLoading: Duration = .milliseconds(300)
    public static let whileReady: Duration = .seconds(15)

    public init(
        engine: any Asking,
        pause: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        signedOut: @escaping @MainActor () -> Void = {}
    ) {
        self.engine = engine
        self.pause = pause
        self.signedOut = signedOut
    }

    /// Asks once and takes the answer in. Returns how long to wait before asking again, or nil
    /// when there is no point in asking again.
    @discardableResult
    public func refresh() async -> Duration? {
        do {
            switch try await engine.ask("overview", as: Overview.self) {
            case .ready(let overview):
                state = .ready(overview)
                return overview.recomputing ? Self.whileLoading : Self.whileReady
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
}
