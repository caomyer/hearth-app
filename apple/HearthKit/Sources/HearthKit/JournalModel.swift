import Foundation
import Observation

/// The journal on screen: what it is narrowed to, the entries asked for so far, and how it
/// keeps up with newer books.
///
/// The engine answers a window of entries at a time (`hearth.native.doorway.MOST`), so a
/// journal of thousands is never sent whole: the first page comes with each change of filter,
/// and the next as the person scrolls to the end of what is on screen.
@MainActor
@Observable
public final class JournalModel {
    public enum State: Equatable, Sendable {
        case starting
        /// The books are being computed, and nothing is on screen yet.
        case loading
        case ready
        case failed(String)
        /// The engine has no such journal for this viewer: an account they may not see into.
        case missing
        case signedOut
        case stopped
    }

    /// A day of the journal, with its entries newest first.
    public struct DayGroup: Sendable, Equatable, Identifiable {
        public let day: Day
        public let entries: [Entry]
        public var id: Day { day }
    }

    public private(set) var state: State = .starting
    public private(set) var filter: JournalFilter
    public private(set) var entries: [Entry] = []
    /// How many entries the whole filter leaves, of which `entries` are the first.
    public private(set) var total = 0
    public private(set) var counts = Journal.Counts.none
    /// The latest answer's own words on what it covers, and when its books were computed.
    public private(set) var header: Header?
    public private(set) var isLoadingMore = false

    public struct Header: Sendable, Equatable {
        public let from: Day
        public let to: Day
        public let asOf: Day
        public let currency: String
        public let computedAt: Date
    }

    /// The account every filter of this journal keeps, as an account's own page does.
    public let pinned: AccountRef?
    public let page: Int

    private let engine: any Asking
    private let pause: @Sendable (Duration) async throws -> Void
    private let signedOut: @MainActor () -> Void
    /// Bumped by every change of filter, so a page asked for under an earlier one is let go.
    private var generation = 0

    public static let defaultPage = 100
    public static let whileLoading: Duration = .milliseconds(300)
    public static let whileReady: Duration = .seconds(15)

    public init(
        engine: any Asking,
        filter: JournalFilter = JournalFilter(),
        pinned: AccountRef? = nil,
        page: Int = JournalModel.defaultPage,
        pause: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        signedOut: @escaping @MainActor () -> Void = {}
    ) {
        self.engine = engine
        self.pinned = pinned
        self.page = page
        self.pause = pause
        self.signedOut = signedOut
        var filter = filter
        if let pinned { filter.account = pinned }
        self.filter = filter
    }

    /// The entries on screen, a day at a time.
    public var days: [DayGroup] { firstDays(fitting: .max) }

    /// The first entries, a day at a time, in no more than `lines`, counting a line for each
    /// day's heading and one for each entry.
    public func firstDays(fitting lines: Int) -> [DayGroup] {
        var groups: [DayGroup] = []
        var current: [Entry] = []
        var used = 0
        for entry in entries {
            let opens = current.last.map { $0.date != entry.date } ?? true
            guard used + (opens ? 2 : 1) <= lines else { break }
            if opens, let last = current.last {
                groups.append(DayGroup(day: last.date, entries: current))
                current = []
            }
            used += opens ? 2 : 1
            current.append(entry)
        }
        if let last = current.last {
            groups.append(DayGroup(day: last.date, entries: current))
        }
        return groups
    }

    /// Whether there are entries the filter leaves that are not on screen yet.
    public var hasMore: Bool { entries.count < total }

    /// Narrows the journal to `filter` and asks for its first page. A pinned account stays.
    public func apply(_ filter: JournalFilter) async {
        var filter = filter
        if let pinned { filter.account = pinned }
        guard filter != self.filter || state != .ready else { return }
        self.filter = filter
        await reload()
    }

    /// Removes one token's filter.
    public func remove(_ token: JournalFilter.Token) async {
        await apply(filter.removing(token))
    }

    /// The tokens a person may remove: on an account's own page, not its account.
    public var tokens: [JournalFilter.Token] {
        filter.tokens.filter { token in
            if case .account = token, pinned != nil { return false }
            return true
        }
    }

    /// Asks for the first page under the current filter, replacing whatever is on screen.
    @discardableResult
    public func reload() async -> Duration? {
        generation += 1
        let mine = generation
        guard let journal = await take(offset: 0, generation: mine) else {
            switch state {
            case .loading: return Self.whileLoading
            // A failed recompute is asked about again later, as the Overview asks.
            case .failed: return Self.whileReady
            default: return mine == generation ? nil : Self.whileReady
            }
        }
        entries = journal.entries
        settle(journal)
        return journal.recomputing ? Self.whileLoading : Self.whileReady
    }

    /// Asks for the page after the entries on screen, when there is one.
    public func loadMore() async {
        guard state == .ready, hasMore, !isLoadingMore else { return }
        isLoadingMore = true
        defer { isLoadingMore = false }
        let mine = generation
        let shown = header?.computedAt
        guard let journal = await take(offset: entries.count, generation: mine) else { return }
        guard journal.computedAt == shown, journal.offset == entries.count else {
            // Newer books moved every entry: the journal starts again from its first page.
            await reload()
            return
        }
        entries += journal.entries
        settle(journal)
    }

    /// Asks whether newer books have been computed since what is on screen, and starts again
    /// from the first page if they have. Returns how long to wait before asking again.
    @discardableResult
    public func refresh() async -> Duration? {
        guard state == .ready, let shown = header else { return await reload() }
        let mine = generation
        do {
            switch try await engine.ask("transactions", with: filter.given(offset: 0, limit: 1), as: Journal.self) {
            case .ready(let journal):
                guard mine == generation else { return Self.whileReady }
                if journal.computedAt != shown.computedAt || journal.total != total {
                    return await reload()
                }
                return journal.recomputing ? Self.whileLoading : Self.whileReady
            case .loading:
                return Self.whileLoading
            case .failed(let summary):
                state = .failed(summary)
                return Self.whileReady
            case .missing:
                state = .missing
                return nil
            case .signedOut:
                end()
                return nil
            }
        } catch {
            state = .stopped
            return nil
        }
    }

    /// Keeps the journal current until the task is cancelled or the engine stops.
    public func watch() async {
        while !Task.isCancelled, let wait = await refresh() {
            do {
                try await pause(wait)
            } catch {
                return
            }
        }
    }

    private func take(offset: Int, generation mine: Int) async -> Journal? {
        do {
            let answer = try await engine.ask(
                "transactions", with: filter.given(offset: offset, limit: page), as: Journal.self)
            guard mine == generation else { return nil }
            switch answer {
            case .ready(let journal):
                return journal
            case .loading:
                if state != .ready { state = .loading }
            case .failed(let summary):
                state = .failed(summary)
            case .missing:
                entries = []
                total = 0
                counts = .none
                state = .missing
            case .signedOut:
                end()
            }
        } catch {
            guard mine == generation else { return nil }
            state = .stopped
        }
        return nil
    }

    private func settle(_ journal: Journal) {
        total = journal.total
        counts = journal.counts
        header = Header(
            from: journal.from, to: journal.to, asOf: journal.asOf, currency: journal.currency,
            computedAt: journal.computedAt)
        state = .ready
    }

    private func end() {
        // Nothing on screen may outlast the session it was shown in.
        entries = []
        state = .signedOut
        signedOut()
    }
}
