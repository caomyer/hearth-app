import Foundation
import Observation

/// The sync control in the toolbar: how fresh the viewer's banks are, whether the engine is
/// asking them now, and asking it to, when the person allowed it.
///
/// It decides nothing. Whether a sync may run is the engine's to say, from what it was started
/// approved for (`Banks.asking`); how each bank answered is its `banks` screen.
@MainActor
@Observable
public final class SyncModel {
    public enum State: Equatable, Sendable {
        /// Nothing has been heard from the engine yet.
        case starting
        case ready(Banks)
        /// The engine stopped, or says nobody is signed in, or has no such screen.
        case unavailable
    }

    public private(set) var state: State = .starting
    /// True from the moment the person asks until the engine has the sync in hand.
    public private(set) var isAsking = false
    /// Counts the syncs the person asked for that have ended, so the control can say how one
    /// went the moment it ends.
    public private(set) var answered = 0

    private let engine: any Acting
    private let pause: @Sendable (Duration) async throws -> Void
    private let signedOut: @MainActor () -> Void

    /// How long to wait before asking again: often while a sync runs, so its end shows at once,
    /// and seldom otherwise, only to notice the morning's sync or one another process ran.
    public static var whileSyncing: Duration { .milliseconds(700) }
    public static var whileIdle: Duration { .seconds(20) }

    public init(
        engine: any Acting,
        pause: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        signedOut: @escaping @MainActor () -> Void = {}
    ) {
        self.engine = engine
        self.pause = pause
        self.signedOut = signedOut
    }

    /// The latest the engine said, once it said anything.
    public var banks: Banks? {
        if case .ready(let banks) = state { banks } else { nil }
    }

    /// Whether the engine may ask the banks anything: what the person allowed it.
    public var mayAsk: Bool { !(banks?.asking.isEmpty ?? true) }

    /// Whether the banks are being asked now, or about to be.
    public var isSyncing: Bool { isAsking || banks?.running == true }

    /// Whether the person can ask for a sync now.
    public var canSyncNow: Bool { mayAsk && !isSyncing }

    /// Asks once and takes the answer in. Returns how long to wait before asking again, or nil
    /// when there is no point in asking again.
    @discardableResult
    public func refresh() async -> Duration? {
        do {
            switch try await engine.ask("banks", as: Banks.self) {
            case .ready(let banks):
                state = .ready(banks)
                return banks.running ? Self.whileSyncing : Self.whileIdle
            case .loading, .failed:
                // The banks screen needs no books, so neither is ever its answer.
                return Self.whileIdle
            case .missing:
                state = .unavailable
                return nil
            case .signedOut:
                state = .unavailable
                signedOut()
                return nil
            }
        } catch {
            state = .unavailable
            return nil
        }
    }

    /// Keeps the control current until the task is cancelled or the engine stops.
    public func watch() async {
        while !Task.isCancelled, let wait = await refresh() {
            do {
                try await pause(wait)
            } catch {
                return
            }
        }
    }

    /// Asks the engine to sync now, and follows the sync until it ends.
    public func syncNow() async {
        guard canSyncNow else { return }
        isAsking = true
        let outcome: ActOutcome
        do {
            outcome = try await engine.act("sync")
        } catch {
            isAsking = false
            state = .unavailable
            return
        }
        switch outcome {
        case .started, .running:
            break
        case .notAllowed, .missing, .accepted, .booked, .moved, .excluded, .undone, .changed,
            .notOneDecision, .notAnAccount, .notYours, .banksAccount, .banksRow:
            // A sync is told only a sync's outcomes; anything else is as good as none.
            isAsking = false
            await refresh()
            return
        case .signedOut:
            isAsking = false
            state = .unavailable
            signedOut()
            return
        }
        // The engine has the sync in hand from the moment it replies, so the first answer that
        // says nothing is running is the one after it ended.
        while !Task.isCancelled {
            guard await refresh() != nil else {
                isAsking = false
                return
            }
            isAsking = false
            if banks?.running == false {
                answered += 1
                return
            }
            do {
                try await pause(Self.whileSyncing)
            } catch {
                return
            }
        }
    }
}
