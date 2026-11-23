import Foundation
import Testing

@testable import HearthKit

/// An example reply kept with the engine's own tests (`tests/native/fixtures`), which check
/// that the engine still answers in its shape. There is one copy, read from the checkout.
func fixture(_ name: String, from file: String = #filePath) throws -> Data {
    let repository = URL(fileURLWithPath: file)
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent()
    return try Data(contentsOf: repository.appendingPathComponent("tests/native/fixtures/\(name).json"))
}

func decode<Content: Decodable & Sendable>(_ json: String, as content: Content.Type = Content.self) throws -> Answer<Content> {
    try replyDecoder().decode(Answer<Content>.self, from: Data(json.utf8))
}

func money(_ text: String) -> Money {
    Money(Decimal(string: text, locale: Locale(identifier: "en_US_POSIX"))!)
}

let unitedStates = Locale(identifier: "en_US")

/// An engine for tests: a shell loop that answers every request with the same answer, under
/// the request's own number, as hearth's bridge does.
func echoEngine(answer: String) -> EngineCommand {
    let script = """
        while IFS= read -r line; do
          id=$(printf '%s' "$line" | sed -E 's/.*"id":([0-9]+).*/\\1/')
          printf '{"id":%s,"answer":%s}\\n' "$id" '\(answer)'
        done
        """
    return EngineCommand(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script])
}

/// Answers from a list, in order, then says the engine has stopped.
actor Scripted: Asking {
    private var answers: [String]
    private(set) var asked: [String] = []

    init(_ answers: [String]) {
        self.answers = answers
    }

    func ask<Content: Decodable & Sendable>(
        _ screen: String, as content: Content.Type
    ) async throws -> Answer<Content> {
        asked.append(screen)
        guard !answers.isEmpty else { throw Engine.Failure.stopped }
        return try decode(answers.removeFirst())
    }
}
