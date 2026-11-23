import Foundation
import Observation

/// What the queue is narrowed to and what it puts first, as `hearth.review.queue` offers it.
public struct ReviewQuery: Sendable, Equatable {
    public var order: ReviewOrder = .size
    public var account: ReviewQueue.Choice?
    public var category: ReviewQueue.Choice?
    public var group: String?
    public var text = ""

    public init() {}

    /// Whether anything narrows the queue.
    public var narrows: Bool {
        account != nil || category != nil || group != nil || !words.isEmpty
    }

    var words: String { text.split(whereSeparator: \.isWhitespace).joined(separator: " ") }

    /// What the engine is asked with: only what differs from the whole queue, largest first.
    public var given: Given {
        var given: Given = [:]
        if order != .size { given["order"] = .text(order.rawValue) }
        if let account { given["account"] = .text(account.value) }
        if let category { given["category"] = .text(category.value) }
        if let group { given["group"] = .text(group) }
        if !words.isEmpty { given["text"] = .text(String(words.prefix(200))) }
        return given
    }
}

/// Something in the queue a person can be on: a group, or one row.
public enum ReviewItem: Sendable, Hashable {
    case group(String)
    case row(String)
}

/// The Review screen: the viewer's queue, where they are in it, and their decisions on it.
///
/// It decides nothing. The queue, its order and its counts are the engine's, from the
/// member's own review desk; every decision goes to the engine through `Decisions`, which
/// says what came of it. What is kept here is where the person is: the item they are on, the
/// groups they opened, and, after a decision, the next item in the queue as it was.
@MainActor
@Observable
public final class ReviewModel {
    public enum State: Equatable, Sendable {
        case starting
        /// The books are being computed, and nothing is on screen yet.
        case loading
        case ready
        case failed(String)
        case missing
        case signedOut
        case stopped
    }

    public private(set) var state: State = .starting
    public private(set) var queue: ReviewQueue?
    public private(set) var query = ReviewQuery()
    /// Rows one after another in the queue's order, rather than by group.
    public private(set) var oneByOne = false
    /// The item the person is on.
    public var selection: ReviewItem?
    /// Where the row the person is on is to go: its suggestion, until they choose another,
    /// which holds only while they stay on that row.
    public var chosen: Destination? {
        get {
            if let picked, picked.item == selection { return picked.destination }
            return selectedRow?.suggested.map(Destination.category)
        }
        set {
            picked = selection.map { ($0, newValue) }
        }
    }
    private var picked: (item: ReviewItem, destination: Destination?)?
    /// Counts the times the queue was answered after a decision, so a detail on screen asks
    /// again for the row it shows.
    public private(set) var revision = 0
    public let decisions: Decisions

    /// Groups opened or closed against how they start: a group that is one decision starts
    /// closed, as its rows are one accept; any other starts open, as each row needs a look.
    private var toggled: Set<String> = []
    private let engine: any Asking
    private let pause: @Sendable (Duration) async throws -> Void
    private let signedOut: @MainActor () -> Void
    /// Bumped by every change of what is asked, so an answer to an earlier ask is let go.
    @ObservationIgnored private var generation = 0

    public static let whileLoading: Duration = .milliseconds(300)
    public static let whileReady: Duration = .seconds(15)

    public init(
        engine: any Asking,
        decisions: Decisions,
        pause: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        signedOut: @escaping @MainActor () -> Void = {}
    ) {
        self.engine = engine
        self.decisions = decisions
        self.pause = pause
        self.signedOut = signedOut
        // After every decision, wherever it was made, the queue as the desk has it now.
        decisions.whenDecided { [weak self] in await self?.reload() }
    }

    // What is on screen

    /// Every item on screen, in order: each group, then its rows when it is open; or, one by
    /// one, every row.
    public var items: [ReviewItem] {
        guard let queue else { return [] }
        if oneByOne { return queue.rows.map { .row($0.id) } }
        return queue.groups.flatMap { group in
            [.group(group.name)] + (isOpen(group) ? group.rows.map { .row($0) } : [])
        }
    }

    public func isOpen(_ group: ReviewGroup) -> Bool {
        group.oneDecision == toggled.contains(group.name)
    }

    /// Opens a closed group, or closes an open one.
    public func toggle(_ group: ReviewGroup) {
        if toggled.contains(group.name) {
            toggled.remove(group.name)
        } else {
            toggled.insert(group.name)
        }
        if !isOpen(group), case .row(let id) = selection, group.rows.contains(id) {
            selection = .group(group.name)
        }
    }

    public var selectedRow: ReviewRow? {
        guard case .row(let id) = selection else { return nil }
        return queue?.row(id)
    }

    public var selectedGroup: ReviewGroup? {
        guard case .group(let name) = selection else { return nil }
        return queue?.group(name)
    }

    /// The rows a group shows, which its accept is bound to.
    public func rows(of group: ReviewGroup) -> [ReviewRow] {
        group.rows.compactMap { queue?.row($0) }
    }

    // Asking

    /// Narrows the queue, or orders it, and asks for it again.
    public func apply(_ query: ReviewQuery) async {
        guard query != self.query || state != .ready else { return }
        self.query = query
        await reload()
    }

    /// Rows one by one in the queue's order, or by group.
    public func showOneByOne(_ on: Bool) {
        guard on != oneByOne else { return }
        oneByOne = on
        settle(previous: [])
    }

    /// Asks for the queue under what it is narrowed to, keeping the person's place.
    @discardableResult
    public func reload() async -> Duration? {
        generation += 1
        let mine = generation
        let before = items
        do {
            let answer = try await engine.ask("review", with: query.given, as: ReviewQueue.self)
            guard mine == generation else { return Self.whileReady }
            switch answer {
            case .ready(let found):
                queue = found
                state = .ready
                settle(previous: before)
                return found.recomputing ? Self.whileLoading : Self.whileReady
            case .loading:
                if state != .ready { state = .loading }
                return Self.whileLoading
            case .failed(let summary):
                state = .failed(summary)
                return Self.whileReady
            case .missing:
                queue = nil
                state = .missing
                return nil
            case .signedOut:
                queue = nil
                state = .signedOut
                signedOut()
                return nil
            }
        } catch {
            guard mine == generation else { return nil }
            state = .stopped
            return nil
        }
    }

    /// Keeps the queue current until the task is cancelled or the engine stops.
    public func watch() async {
        while !Task.isCancelled, let wait = await reload() {
            do {
                try await pause(wait)
            } catch {
                return
            }
        }
    }

    // Moving through it

    /// The next item, or with a negative step the one before; the first when nothing is chosen.
    public func move(_ step: Int) {
        let shown = items
        guard !shown.isEmpty else { return }
        guard let selection, let index = shown.firstIndex(of: selection) else {
            self.selection = step < 0 ? shown.last : shown.first
            return
        }
        self.selection = shown[min(max(index + step, 0), shown.count - 1)]
    }

    /// Leaves the item waiting and moves on.
    public func skip() {
        move(1)
    }

    // Deciding

    /// What Return does: settles the row as `chosen`, its suggestion unless another was
    /// chosen; accepts the group when it is one decision; or moves into a group that is not,
    /// as the page's own key does.
    public func acceptSelected() async {
        if let row = selectedRow {
            if let chosen {
                await book(row, to: chosen)
            }
        } else if let group = selectedGroup {
            if group.oneDecision {
                await accept(group)
            } else if let first = group.rows.first {
                if !isOpen(group) { toggle(group) }
                selection = .row(first)
            }
        }
    }

    /// Whether the item the person is on may be excluded: a row its bank does not count.
    public var canExcludeSelected: Bool { selectedRow?.excludable == true }

    public func accept(_ row: ReviewRow) async {
        guard row.suggested != nil else { return }
        await decide(.accept(row))
    }

    /// Accepts the rows the group shows, when it is one decision; no other group is offered it.
    public func accept(_ group: ReviewGroup) async {
        guard group.oneDecision else { return }
        await decide(.acceptGroup(name: group.name, rows: rows(of: group)))
    }

    public func book(_ row: ReviewRow, to destination: Destination) async {
        await decide(.settling(row, to: destination))
    }

    public func exclude(_ row: ReviewRow) async {
        guard row.excludable else { return }
        await decide(.exclude(row))
    }

    /// Takes back the last decision, wherever it was made.
    public func undo() async {
        await decisions.undo()
    }

    private func decide(_ decision: Decision) async {
        await decisions.make(decision)
    }

    /// After the queue changed: the same item when it is still there; else the next one after
    /// it in the queue as it was, or the one before when it was the last; else the first.
    private func settle(previous: [ReviewItem]) {
        revision += 1
        let shown = items
        guard let current = selection else {
            selection = shown.first
            return
        }
        if shown.contains(current) { return }
        if let index = previous.firstIndex(of: current) {
            let present = Set(shown)
            if let next = previous[(index + 1)...].first(where: present.contains) {
                selection = next
                return
            }
            if let before = previous[..<index].last(where: present.contains) {
                selection = before
                return
            }
        }
        // A row whose group is closed now, as after showing it one by one.
        if case .row(let id) = current, let group = queue?.groups.first(where: { $0.rows.contains(id) }) {
            selection = .group(group.name)
            return
        }
        selection = shown.first
    }
}
