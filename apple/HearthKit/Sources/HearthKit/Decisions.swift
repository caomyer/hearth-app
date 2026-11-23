import Foundation
import Observation

/// Where a row goes when it is not accepted as suggested.
public enum Destination: Sendable, Hashable {
    /// A category the viewer's rows already use.
    case category(CategoryRef)
    /// A full account name the person typed, as a category is named: "Expenses:Garden".
    case typed(String)
    /// One of the viewer's own accounts, by its id.
    case account(AccountRef)

    /// As the person reads it.
    public var label: String {
        switch self {
        case .category(let category): category.label
        case .typed(let name): name.split(separator: ":").last.map(String.init) ?? name
        case .account(let account): account.name
        }
    }

    var given: Given {
        switch self {
        case .category(let category): ["category": .text(category.name)]
        case .typed(let name): ["category": .text(name)]
        case .account(let account): ["account": .text(account.id)]
        }
    }
}

/// One review decision, as the review pages make it, bound to what the app showed: an accept
/// to the account each row proposed then, every other decision to what each row said then.
public enum Decision: Sendable, Equatable {
    /// One row, as suggested.
    case accept(ReviewRow)
    /// The rows of a group shown, each as suggested.
    case acceptGroup(name: String, rows: [ReviewRow])
    /// One row against another category, or one of the viewer's own accounts.
    case book(ReviewRow, to: Destination)
    /// One row is not a transaction.
    case exclude(ReviewRow)
    /// Every row of a group whose other side hearth does not see, to one place.
    case moveGroup(WhereGroup, to: Destination)
    /// One such row, on its own.
    case moveRow(ReviewRow, to: Destination)

    /// Settling one row against `destination`: accepting it, when that is its suggestion, as
    /// the page's row form does, and booking it otherwise.
    public static func settling(_ row: ReviewRow, to destination: Destination) -> Decision {
        if case .category(let category) = destination, category == row.suggested {
            return .accept(row)
        }
        return .book(row, to: destination)
    }

    /// The action the engine is asked to do (`hearth.native.review.DECISIONS`).
    public var action: String {
        switch self {
        case .accept: "accept"
        case .acceptGroup: "accept_group"
        case .book: "book"
        case .exclude: "exclude"
        case .moveGroup: "move_group"
        case .moveRow: "move_row"
        }
    }

    /// What the engine is told the decision is about: the rows shown, as they were shown.
    public var given: Given {
        switch self {
        case .accept(let row):
            ["row": .text(row.id), "shown": .text(row.suggested?.name ?? "")]
        case .acceptGroup(let name, let rows):
            [
                "group": .text(name),
                "rows": .table(
                    Dictionary(
                        rows.compactMap { row in row.suggested.map { (row.id, $0.name) } },
                        uniquingKeysWith: { first, _ in first })),
            ]
        case .book(let row, let destination), .moveRow(let row, let destination):
            ["row": .text(row.id), "says": .text(row.says)].merging(destination.given) { $1 }
        case .exclude(let row):
            ["row": .text(row.id), "says": .text(row.says)]
        case .moveGroup(let group, let destination):
            [
                "title": .text(group.title),
                "rows": .table(
                    Dictionary(group.rows.map { ($0.id, $0.says) }, uniquingKeysWith: { first, _ in first })),
            ].merging(destination.given) { $1 }
        }
    }

    /// How many rows the person decided about.
    var count: Int {
        switch self {
        case .accept, .book, .exclude, .moveRow: 1
        case .acceptGroup(_, let rows): rows.count
        case .moveGroup(let group, _): group.rows.count
        }
    }
}

/// What the last decision did, said in a sentence, and the Undo that goes with it while it is
/// the last decision.
public struct Notice: Sendable, Equatable {
    public enum Tone: Sendable, Equatable {
        /// It did what was asked.
        case done
        /// Something was left waiting, or refused: what to do about it is said.
        case problem
    }

    public let text: String
    public let tone: Tone
    /// The decision an Undo would take back.
    public let ruling: Int?
    /// How many transactions an Undo would take back.
    public let rows: Int

    public init(text: String, tone: Tone, ruling: Int? = nil, rows: Int = 0) {
        self.text = text
        self.tone = tone
        self.ruling = ruling
        self.rows = rows
    }

    /// What the Undo button says.
    public var undoLabel: String { "Undo" }
}

/// Every review decision the app makes, from Review or from Transactions, and the one it may
/// take back: exactly the last, offered until the next.
///
/// It decides nothing itself. Each decision goes to the engine, which makes it through the
/// signed-in member's own review desk by the review pages' rules, and says what came of it.
@MainActor
@Observable
public final class Decisions {
    /// What the last decision did, with its Undo while it is the last.
    public private(set) var notice: Notice?
    /// True while a decision is with the engine.
    public private(set) var isDeciding = false
    /// Counts decisions answered, so a screen can ask again after each.
    public private(set) var answered = 0

    private let engine: any Acting
    private let signedOut: @MainActor () -> Void
    @ObservationIgnored private var listeners: [@MainActor () async -> Void] = []

    public init(engine: any Acting, signedOut: @escaping @MainActor () -> Void = {}) {
        self.engine = engine
        self.signedOut = signedOut
    }

    /// Whether the last decision can be taken back.
    public var canUndo: Bool { !isDeciding && notice?.ruling != nil }

    /// Runs `listener` after every decision and every Undo the engine answered.
    public func whenDecided(_ listener: @escaping @MainActor () async -> Void) {
        listeners.append(listener)
    }

    /// Makes `decision`, and returns what the engine said of it; nil when it could not be asked.
    @discardableResult
    public func make(_ decision: Decision) async -> ActResult? {
        guard !isDeciding else { return nil }
        return await send(decision.action, decision.given) { ReviewWords.notice(for: decision, $0) }
    }

    /// Takes back the last decision; its rows wait for review again.
    @discardableResult
    public func undo() async -> ActResult? {
        guard canUndo, let ruling = notice?.ruling else { return nil }
        return await send("undo", ["ruling": .number(ruling)]) { ReviewWords.undone($0) }
    }

    /// Forgets the last notice, as when the person dismisses it. The Undo goes with it.
    public func dismiss() {
        notice = nil
    }

    private func send(
        _ action: String, _ given: Given, words: (ActResult) -> Notice?
    ) async -> ActResult? {
        isDeciding = true
        defer { isDeciding = false }
        let result: ActResult
        do {
            result = try await engine.act(action, with: given)
        } catch {
            notice = Notice(text: ReviewWords.stopped, tone: .problem)
            return nil
        }
        if result.outcome == .signedOut {
            notice = nil
            signedOut()
            return result
        }
        notice = words(result)
        answered += 1
        for listener in listeners {
            await listener()
        }
        return result
    }
}
