import Foundation
import Observation

/// Bringing the household's ledger in, and matching accounts to it, one sheet after another:
/// the question asked once, the file's check, the confirmation, the import while it runs, the
/// matching, and what was matched.
///
/// It decides nothing. Who may bring a ledger in, what a file holds, why one would be refused,
/// which account each connected one is and what matching it would meet are all the engine's
/// (`hearth.native.ledger`); this only asks, in order, and keeps what the person chose.
@MainActor
@Observable
public final class LedgerFlow {
    public enum Step: Equatable, Sendable {
        /// "Do you keep a beancount ledger?"
        case question
        /// The file is being read.
        case checking(URL)
        /// What the file holds, or why it cannot be brought in.
        case checked(URL, LedgerCheck)
        /// "Bring in household.beancount?"
        case confirming(URL, LedgerCheck)
        /// The engine is bringing it in.
        case bringingIn(URL)
        /// It was not brought in, for the reason given in the engine's words, or nil when
        /// something went wrong the engine does not know the cause of.
        case notBroughtIn(String?)
        /// Which of the ledger's accounts each connected account is.
        case matching(Matching)
        /// What was matched, and what is in the books now.
        case done(Matching)
        /// The engine stopped, or would not say.
        case unavailable
    }

    /// The sheet's step, or nil when no sheet is shown.
    public private(set) var step: Step?
    /// Where the household's ledger stands, once the engine has said.
    public private(set) var standing: LedgerStanding?
    /// What each connected account is to be matched to, by its id; an account with none is
    /// left as it is.
    public var chosen: [String: String] = [:]
    /// Each match the engine refused when asked, by account, since it was offered.
    public private(set) var refusedNow: Set<String> = []
    /// True while the engine is asked to do something.
    public private(set) var isWorking = false

    private let engine: any Acting
    private let pause: @Sendable (Duration) async throws -> Void
    private let signedOut: @MainActor () -> Void
    private let asked: () -> Bool
    private let remember: () -> Void

    /// How long to wait between asks while the engine brings a ledger in.
    public static var whileBringingIn: Duration { .milliseconds(500) }

    /// `asked` says whether the question was put to this member on this Mac before, and
    /// `remember` keeps that it was, so it is put once.
    public init(
        engine: any Acting,
        asked: @escaping () -> Bool = { true },
        remember: @escaping () -> Void = {},
        pause: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        signedOut: @escaping @MainActor () -> Void = {}
    ) {
        self.engine = engine
        self.asked = asked
        self.remember = remember
        self.pause = pause
        self.signedOut = signedOut
    }

    /// Asks where the household's ledger stands.
    @discardableResult
    public func refresh() async -> LedgerStanding? {
        do {
            switch try await engine.ask("ledger", as: LedgerStanding.self) {
            case .ready(let found):
                standing = found
                return found
            case .signedOut:
                signedOut()
                return nil
            case .loading, .failed, .missing:
                return nil
            }
        } catch {
            return nil
        }
    }

    /// Puts the question, once: to a member who may bring a ledger in, where none is in yet.
    public func askOnce() async {
        guard step == nil, !asked() else { return }
        guard let standing = await refresh(), standing.mayBringIn else { return }
        remember()
        step = .question
    }

    /// Opens the sheet on a step of the person's choosing: the question, or matching.
    public func begin(at step: Step) {
        self.step = step
    }

    /// The person said no, or closed the sheet.
    public func close() {
        step = nil
        chosen = [:]
        refusedNow = []
    }

    /// Reads the file the person chose, writing nothing.
    public func check(_ file: URL) async {
        step = .checking(file)
        do {
            switch try await engine.ask("ledger_check", with: ["path": .text(file.path)], as: LedgerCheck.self) {
            case .ready(let found):
                guard step == .checking(file) else { return }
                step = .checked(file, found)
            case .signedOut:
                close()
                signedOut()
            case .loading, .failed, .missing:
                step = .unavailable
            }
        } catch {
            step = .unavailable
        }
    }

    /// From the check to the confirmation, for a file that would be brought in.
    public func confirm() {
        guard case .checked(let file, let check) = step, check.refused == nil else { return }
        step = .confirming(file, check)
    }

    /// Brings the file in, and follows the engine until it ends.
    public func bringIn() async {
        guard case .confirming(let file, _) = step, !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        step = .bringingIn(file)
        let outcome: ActOutcome
        do {
            outcome = try await engine.act("bring_in_ledger", with: ["path": .text(file.path)]).outcome
        } catch {
            step = .unavailable
            return
        }
        switch outcome {
        case .started, .running:
            break
        case .signedOut:
            close()
            signedOut()
            return
        default:
            step = .notBroughtIn(nil)
            return
        }
        // The engine has the import in hand once it replies, so the first answer that says it
        // is not running is the one after it ended.
        for _ in 0..<2400 {
            guard let found = await refresh() else {
                step = .unavailable
                return
            }
            if let bringing = found.bringingIn, !bringing.running {
                await ended(bringing)
                return
            }
            do {
                try await pause(Self.whileBringingIn)
            } catch {
                return
            }
        }
        step = .unavailable
    }

    private func ended(_ bringing: LedgerStanding.BringingIn) async {
        switch bringing.outcome {
        case .imported?, .unchanged?:
            await openMatching()
        case .refused?:
            step = .notBroughtIn(bringing.refused)
        case .failed?, .unknown?, nil:
            step = .notBroughtIn(nil)
        }
    }

    /// Asks which of the ledger's accounts each of the member's connected accounts is, and
    /// shows it: matching, or what is matched already when there is nothing left to choose.
    public func openMatching() async {
        // Where the ledger stands too, which the last step says what nobody sees yet of.
        await refresh()
        guard let found = await matching() else { return }
        refusedNow = []
        chosen = Self.chosen(in: found)
        let open = found.accounts.contains { $0.state != .matched && !$0.choices.isEmpty }
        step = open ? .matching(found) : .done(found)
    }

    /// What each account starts chosen as: where it is matched now, or else what the ledger
    /// proposes, when matching it there would not be refused.
    public static func chosen(in found: Matching) -> [String: String] {
        var chosen: [String: String] = [:]
        for account in found.accounts {
            if let matched = account.matched?.account {
                chosen[account.id] = matched
            } else if let proposed = account.choices.first(where: { $0.proposed && $0.refused == nil }) {
                chosen[account.id] = proposed.account
            }
        }
        return chosen
    }

    /// The choices the person may pick for `account`: those the engine would not refuse.
    public static func open(_ account: ConnectedAccount) -> [Choice] {
        account.choices.filter { $0.refused == nil || $0.account == account.matched?.account }
    }

    /// Matches every account the person chose an account for that it is not matched to now,
    /// then shows what is matched.
    public func match() async {
        guard case .matching(let found) = step, !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        var refused: Set<String> = []
        for account in found.accounts where !account.fixed {
            guard let to = chosen[account.id], to != account.matched?.account,
                Self.open(account).contains(where: { $0.account == to })
            else { continue }
            let outcome: ActOutcome
            do {
                outcome = try await engine.act("match_account", with: ["account": .text(account.id), "to": .text(to)]).outcome
            } catch {
                step = .unavailable
                return
            }
            switch outcome {
            case .matched:
                break
            case .signedOut:
                close()
                signedOut()
                return
            default:
                refused.insert(account.id)
            }
        }
        guard let after = await matching() else { return }
        if refused.isEmpty {
            step = .done(after)
        } else {
            // Something changed since it was shown: show it again as it is now, and say which.
            chosen = Self.chosen(in: after)
            refusedNow = refused
            step = .matching(after)
        }
    }

    /// From what was matched back to matching, to change it.
    public func change() {
        guard case .done(let found) = step else { return }
        chosen = Self.chosen(in: found)
        step = .matching(found)
    }

    private func matching() async -> Matching? {
        do {
            switch try await engine.ask("matching", as: Matching.self) {
            case .ready(let found):
                return found
            case .signedOut:
                close()
                signedOut()
                return nil
            case .loading, .failed, .missing:
                step = .unavailable
                return nil
            }
        } catch {
            step = .unavailable
            return nil
        }
    }
}
