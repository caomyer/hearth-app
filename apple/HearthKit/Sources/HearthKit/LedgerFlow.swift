import Foundation
import Observation

/// Bringing the household's ledger in, and matching accounts to it, one sheet after another:
/// the question asked once, the file's check, the confirmation, the import while it runs, whose
/// each of the ledger's accounts is, the matching, and what was matched.
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
        /// The engine is bringing in a ledger someone else started on this Mac; whose is
        /// never said.
        case waitingForAnother
        /// It was not brought in, for the reason the engine gave, or nil when something went
        /// wrong the engine does not know the cause of.
        case notBroughtIn(LedgerRefusal?)
        /// It is in, but the books could not be worked out from it yet.
        case inButBooksFailed
        /// Whose each of the ledger's accounts nobody owns yet is: an admin's to say.
        case assigning(LedgerOwners)
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
    /// Whether the engine refused the last matches asked, since they were offered.
    public private(set) var refusedNow = false
    /// Whose each of the ledger's accounts is to be, by usernames; an account with nobody is
    /// left for later.
    public var owners: [String: [String]] = [:]
    /// Whether the engine refused the last owners asked, as one was claimed since it was shown.
    public private(set) var ownersRefusedNow = false
    /// True while the engine is asked to do something.
    public private(set) var isWorking = false

    private let engine: any Acting
    private let pause: @Sendable (Duration) async throws -> Void
    private let signedOut: @MainActor () -> Void
    private let asked: () -> Bool
    private let remember: () -> Void

    /// How long to wait between asks while the engine brings a ledger in, or works a slow
    /// screen out.
    public static var whileWorking: Duration { .milliseconds(400) }
    /// How many times to ask before saying the engine would not say: twenty minutes.
    static let mostAsks = 3000

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
        guard case .ready(let found)? = await settled("ledger", as: LedgerStanding.self) else { return nil }
        standing = found
        return found
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
        refusedNow = false
        owners = [:]
        ownersRefusedNow = false
    }

    /// Reads the file the person chose, writing nothing.
    public func check(_ file: URL) async {
        step = .checking(file)
        switch await settled("ledger_check", with: ["path": .text(file.path)], as: LedgerCheck.self) {
        case .ready(let found)?:
            guard step == .checking(file) else { return }
            step = .checked(file, found)
        case .signedOut?, nil:
            return
        case .loading?, .failed?, .missing?:
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
        case .started:
            await follow(mine: true)
        case .running:
            // Someone is bringing one in on this engine already: wait on it, never told whose.
            step = .waitingForAnother
            await follow(mine: false)
        case .signedOut:
            close()
            signedOut()
        default:
            step = .notBroughtIn(nil)
        }
    }

    /// Asks where the import stands until it ends. An import of this member's own says how it
    /// went; another's only that it ended, and whether a ledger is in now.
    private func follow(mine: Bool) async {
        for _ in 0..<Self.mostAsks {
            guard let found = await refresh() else {
                step = .unavailable
                return
            }
            if mine, let bringing = found.bringingIn, !bringing.running {
                await ended(bringing)
                return
            }
            if !mine, !found.importing {
                if found.imported != nil {
                    await openOwners()
                } else {
                    step = .notBroughtIn(nil)
                }
                return
            }
            do {
                try await pause(Self.whileWorking)
            } catch {
                return
            }
        }
        step = .unavailable
    }

    private func ended(_ bringing: LedgerStanding.BringingIn) async {
        switch bringing.outcome {
        case .imported?, .unchanged?:
            if bringing.booksFailed {
                step = .inButBooksFailed
            } else {
                await openOwners()
            }
        case .refused?:
            step = .notBroughtIn(bringing.refused)
        case .failed?, .unknown?, nil:
            step = .notBroughtIn(nil)
        }
    }

    /// Asks whose the ledger's accounts are to be, and shows them, each starting as the
    /// member's own; or goes on to matching when there is none to say, or the member may not.
    public func openOwners() async {
        switch await settled("ledger_owners", as: LedgerOwners.self) {
        case .ready(let found)?:
            ownersRefusedNow = false
            guard !found.accounts.isEmpty else {
                await openMatching()
                return
            }
            owners = Self.owners(in: found)
            step = .assigning(found)
        case .missing?:
            // Only an admin says whose an account is; anyone else goes on to their matching.
            await openMatching()
        case .signedOut?, nil:
            return
        case .loading?, .failed?:
            step = .unavailable
        }
    }

    /// What each account starts as: the member's own.
    public nonisolated static func owners(in found: LedgerOwners) -> [String: [String]] {
        Dictionary(uniqueKeysWithValues: found.accounts.map { ($0.account, [found.you]) })
    }

    /// Each group of the ledger's accounts, by the first two parts of their names, as the
    /// ledger itself groups them: `Assets:Harbor`, `Liabilities:Harbor`.
    public nonisolated static func groups(_ found: LedgerOwners) -> [(name: String, accounts: [LedgerOwners.Unowned])] {
        var order: [String] = []
        var grouped: [String: [LedgerOwners.Unowned]] = [:]
        for account in found.accounts {
            let name = account.account.split(separator: ":").prefix(2).joined(separator: ":")
            if grouped[name] == nil { order.append(name) }
            grouped[name, default: []].append(account)
        }
        return order.map { ($0, grouped[$0] ?? []) }
    }

    /// Says every account of `accounts` is `who`'s, or leaves each for later with nobody.
    public func setOwners(_ accounts: [LedgerOwners.Unowned], to who: [String]) {
        for account in accounts {
            owners[account.account] = who
        }
    }

    /// Says whose each account is, all in one act, then goes on to matching; an account left
    /// for later stays nobody's.
    public func assign() async {
        guard case .assigning(let found) = step, !isWorking else { return }
        let asked = owners.filter { name, who in !who.isEmpty && found.accounts.contains { $0.account == name } }
        guard !asked.isEmpty else {
            await openMatching()
            return
        }
        isWorking = true
        defer { isWorking = false }
        let outcome: ActOutcome
        do {
            outcome = try await engine.act("assign_owners", with: ["owners": .lists(asked)]).outcome
        } catch {
            step = .unavailable
            return
        }
        switch outcome {
        case .assigned:
            await openMatching()
        case .signedOut:
            close()
            signedOut()
        case .refused:
            // One was claimed since it was shown: show what is left, keeping what was chosen.
            let kept = owners
            guard case .ready(let after)? = await settled("ledger_owners", as: LedgerOwners.self) else {
                step = .unavailable
                return
            }
            guard !after.accounts.isEmpty else {
                await openMatching()
                return
            }
            owners = Self.owners(in: after).merging(kept.filter { name, _ in
                after.accounts.contains { $0.account == name }
            }) { _, chosen in chosen }
            ownersRefusedNow = true
            step = .assigning(after)
        default:
            step = .unavailable
        }
    }

    /// Asks which of the ledger's accounts each of the member's connected accounts is, and
    /// shows it: matching, or what is matched already when there is nothing left to choose.
    public func openMatching() async {
        // Where the ledger stands too, which the last step says what nobody sees yet of.
        await refresh()
        guard let found = await matching() else { return }
        refusedNow = false
        chosen = Self.chosen(in: found)
        step = Self.hasChoices(found) ? .matching(found) : .done(found)
    }

    /// Whether any account is left that the person may choose an account of the ledger for.
    public nonisolated static func hasChoices(_ found: Matching) -> Bool {
        found.accounts.contains { $0.state != .matched && !$0.fixed && !open($0).isEmpty }
    }

    /// What each account starts chosen as: where it is matched now, or else what the ledger
    /// proposes, when matching it there would not be refused.
    public nonisolated static func chosen(in found: Matching) -> [String: String] {
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
    public nonisolated static func open(_ account: ConnectedAccount) -> [Choice] {
        account.choices.filter { $0.refused == nil || $0.account == account.matched?.account }
    }

    /// The matches the person chose that are not as things stand, by connected account.
    public func changes(in found: Matching) -> [String: String] {
        var asked: [String: String] = [:]
        for account in found.accounts where !account.fixed {
            guard let to = chosen[account.id], to != account.matched?.account,
                Self.open(account).contains(where: { $0.account == to })
            else { continue }
            asked[account.id] = to
        }
        return asked
    }

    /// Matches every account the person chose an account for that it is not matched to now,
    /// all in one act, then shows what is matched.
    public func match() async {
        guard case .matching(let found) = step, !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        let asked = changes(in: found)
        if !asked.isEmpty {
            let outcome: ActOutcome
            do {
                outcome = try await engine.act("match_accounts", with: ["matches": .table(asked)]).outcome
            } catch {
                step = .unavailable
                return
            }
            switch outcome {
            case .matched:
                refusedNow = false
            case .signedOut:
                close()
                signedOut()
                return
            default:
                // Something changed since it was shown: show it again as it is now, and say so.
                guard let after = await matching() else { return }
                chosen = Self.chosen(in: after)
                refusedNow = true
                step = .matching(after)
                return
            }
        }
        guard let after = await matching() else { return }
        // What nobody's yet is as things stand after the matches, which may have moved claims.
        await refresh()
        step = .done(after)
    }

    /// From what was matched back to matching, to change it.
    public func change() {
        guard case .done(let found) = step else { return }
        chosen = Self.chosen(in: found)
        step = .matching(found)
    }

    private func matching() async -> Matching? {
        switch await settled("matching", as: Matching.self) {
        case .ready(let found)?:
            return found
        case .signedOut?, nil:
            return nil
        case .loading?, .failed?, .missing?:
            step = .unavailable
            return nil
        }
    }

    /// A screen's answer once the engine has worked it out: a slow one is answered `loading`
    /// while it is, and asked again. Nil when the engine stopped, which leaves the sheet saying
    /// so, or when nobody is signed in any more, which closes it.
    private func settled<Content: Decodable & Sendable>(
        _ screen: String, with given: Given = [:], as content: Content.Type
    ) async -> Answer<Content>? {
        for _ in 0..<Self.mostAsks {
            let answer: Answer<Content>
            do {
                answer = try await engine.ask(screen, with: given, as: content)
            } catch {
                step = .unavailable
                return nil
            }
            switch answer {
            case .loading:
                do {
                    try await pause(Self.whileWorking)
                } catch {
                    return nil
                }
            case .signedOut:
                close()
                signedOut()
                return nil
            default:
                return answer
            }
        }
        return .loading
    }
}
