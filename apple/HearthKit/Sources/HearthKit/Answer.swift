import Foundation

/// What the engine says when asked for a screen (`hearth.native.doorway`).
public enum Answer<Content: Decodable & Sendable>: Decodable, Sendable {
    /// The screen's figures.
    case ready(Content)
    /// The books have not been computed yet.
    case loading
    /// The last recompute failed and there are no earlier books to show.
    case failed(summary: String)
    /// There is no such screen, or none this viewer may see. The engine does not say which.
    case missing
    /// Nobody is signed in to the engine, so it says nothing about the household: not even
    /// whether the screen exists.
    case signedOut

    private enum CodingKeys: String, CodingKey {
        case state, summary
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let state = try container.decode(String.self, forKey: .state)
        switch state {
        case "ready": self = .ready(try Content(from: decoder))
        case "loading": self = .loading
        case "failed": self = .failed(summary: try container.decode(String.self, forKey: .summary))
        case "missing": self = .missing
        case "signed_out": self = .signedOut
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .state, in: container, debugDescription: "an answer in no known state: \(state)")
        }
    }
}

extension Answer: Equatable where Content: Equatable {}

/// One line from the engine: the answer to the request with the same number.
struct Reply<Content: Decodable & Sendable>: Decodable {
    let id: Int
    let answer: Answer<Content>
}

/// The engine's reply to signing in or out.
struct Signed: Decodable {
    let id: Int
    let outcome: SignInOutcome
}

/// The engine's reply to an action: its outcome, and for a review decision, what it decided
/// (`hearth.native.common.Decided`).
public struct ActResult: Decodable, Sendable, Equatable {
    public let outcome: ActOutcome
    /// The decision an Undo would take back, when one was recorded.
    public let ruling: Int?
    /// How many rows it decided, or for an Undo, took back.
    public let rows: Int
    /// How many of the rows shown it left waiting, because they changed since.
    public let changed: Int

    public init(outcome: ActOutcome, ruling: Int? = nil, rows: Int = 0, changed: Int = 0) {
        self.outcome = outcome
        self.ruling = ruling
        self.rows = rows
        self.changed = changed
    }

    private enum CodingKeys: String, CodingKey {
        case outcome, ruling, rows, changed
    }

    public init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        outcome = try container.decode(ActOutcome.self, forKey: .outcome)
        ruling = try container.decodeIfPresent(Int.self, forKey: .ruling)
        rows = try container.decodeIfPresent(Int.self, forKey: .rows) ?? 0
        changed = try container.decodeIfPresent(Int.self, forKey: .changed) ?? 0
    }
}

/// The decoder every reply goes through: the engine writes snake_case keys and UTC moments to
/// the second.
func replyDecoder() -> JSONDecoder {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    decoder.dateDecodingStrategy = .iso8601
    return decoder
}
