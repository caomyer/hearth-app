import Foundation
import Testing

@testable import HearthKit

@Suite("The engine process")
struct EngineTests {
    @Test("each ask gets the answer to its own request")
    func asks() async throws {
        let engine = try Engine.launch(echoEngine(answer: #"{"state":"loading"}"#))
        defer { Task { await engine.stop() } }

        let first = try await engine.ask("overview", as: Overview.self)
        let second = try await engine.ask("overview", as: Overview.self)

        #expect(first == .loading)
        #expect(second == .loading)
    }

    @Test("many asks at once are each answered")
    func concurrentAsks() async throws {
        let engine = try Engine.launch(echoEngine(answer: #"{"state":"missing"}"#))
        defer { Task { await engine.stop() } }

        let answers = try await withThrowingTaskGroup(of: Answer<Overview>.self) { group in
            for _ in 0..<25 {
                group.addTask { try await engine.ask("overview", as: Overview.self) }
            }
            return try await group.reduce(into: []) { $0.append($1) }
        }

        #expect(answers == Array(repeating: .missing, count: 25))
    }

    @Test("asking a stopped engine is an error")
    func stopped() async throws {
        let engine = try Engine.launch(echoEngine(answer: #"{"state":"loading"}"#))
        await engine.stop()

        await #expect(throws: Engine.Failure.stopped) {
            try await engine.ask("overview", as: Overview.self)
        }
    }

    @Test("an engine that exits without answering fails the ask, and does not end the app")
    func exits() async throws {
        let silent = EngineCommand(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", "read line; exit 3"])
        let engine = try Engine.launch(silent)

        await #expect(throws: Engine.Failure.stopped) {
            try await engine.ask("overview", as: Overview.self)
        }
        await #expect(throws: Engine.Failure.stopped) {
            try await engine.ask("overview", as: Overview.self)
        }
    }

    @Test("a request the engine refuses is an error for that request alone")
    func refused() async throws {
        let refusing = EngineCommand(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", #"read line; printf '{"id":null,"error":"bad request"}\n'; cat >/dev/null"#])
        let engine = try Engine.launch(refusing)
        defer { Task { await engine.stop() } }

        await #expect(throws: Engine.Failure.refused) {
            try await engine.ask("overview", as: Overview.self)
        }
    }

    @Test("signing in sends the username and password on one line, and reads the outcome")
    func signsIn() async throws {
        // Says signed_in only to the one request that carries this member's sign-in.
        let script = #"""
            while IFS= read -r line; do
              id=$(printf '%s' "$line" | sed -E 's/.*"id":([0-9]+).*/\1/')
              case "$line" in
                *'"sign_in":{"password":"an invented passphrase","username":"avery"}'*) outcome=signed_in ;;
                *'"sign_out":true'*) outcome=signed_out ;;
                *) outcome=rejected ;;
              esac
              printf '{"id":%s,"outcome":"%s"}\n' "$id" "$outcome"
            done
            """#
        let engine = try Engine.launch(EngineCommand(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script]))
        defer { Task { await engine.stop() } }

        #expect(try await engine.signIn(username: "avery", password: "an invented passphrase") == .signedIn)
        #expect(try await engine.signIn(username: "avery", password: "a guess") == .rejected)
        try await engine.signOut()
    }

    @Test("an ask carries what narrows it under with, and an ask of nothing carries no with")
    func narrows() async throws {
        let script = #"""
            while IFS= read -r line; do
              id=$(printf '%s' "$line" | sed -E 's/.*"id":([0-9]+).*/\1/')
              case "$line" in
                '{"ask":"account","id":'*',"with":{"id":"acc","limit":2}}') state=loading ;;
                *'"with"'*) state=missing ;;
                *) state=signed_out ;;
              esac
              printf '{"id":%s,"answer":{"state":"%s"}}\n' "$id" "$state"
            done
            """#
        let engine = try Engine.launch(EngineCommand(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script]))
        defer { Task { await engine.stop() } }

        let narrowed = try await engine.ask("account", with: ["id": .text("acc"), "limit": .number(2)], as: AccountPage.self)
        #expect(narrowed == .loading)
        #expect(try await engine.ask("accounts", as: Accounts.self) == .signedOut)
    }

    @Test("an engine that is not there cannot be launched")
    func notThere() {
        let nowhere = EngineCommand(executable: URL(fileURLWithPath: "/nonexistent/hearth-engine"), arguments: [])
        #expect(throws: (any Error).self) {
            try Engine.launch(nowhere)
        }
    }

    @Test("the engine is given only the environment it is told")
    func environment() async throws {
        let script = #"read line; printf '{"id":1,"answer":{"state":"failed","summary":"%s"}}\n' "${HOME:-unset}/${HEARTH_TEST:-unset}""#
        let command = EngineCommand(
            executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script], environment: ["HEARTH_TEST": "told"])
        let engine = try Engine.launch(command)
        defer { Task { await engine.stop() } }

        #expect(try await engine.ask("overview", as: Overview.self) == .failed(summary: "unset/told"))
    }
}

@Suite("The engine an app carries")
struct BundledEngineTests {
    @Test("runs the bundle's own Python, isolated, on the bundle's own hearth")
    func command() {
        let command = EngineCommand.bundled(
            in: URL(fileURLWithPath: "/Applications/Hearth.app/Contents/Resources"),
            hearth: ["demo", "--bridge"], home: "/Users/someone", temporary: "/tmp/x")

        #expect(command.executable.path == "/Applications/Hearth.app/Contents/Resources/engine/python/bin/python3.14")
        #expect(command.arguments == ["-I", "-B", "/Applications/Hearth.app/Contents/Resources/engine/main.py", "demo", "--bridge"])
        #expect(command.environment == ["HOME": "/Users/someone", "TMPDIR": "/tmp/x", "LC_ALL": "en_US.UTF-8"])
    }
}
