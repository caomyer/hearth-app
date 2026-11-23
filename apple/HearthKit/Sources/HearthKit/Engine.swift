import Foundation

/// How to start hearth's engine: a program, and what to tell it.
public struct EngineCommand: Sendable, Equatable {
    public var executable: URL
    public var arguments: [String]
    /// The engine's whole environment. Nothing of the app's own is passed on unless named here.
    public var environment: [String: String]

    public init(executable: URL, arguments: [String], environment: [String: String] = [:]) {
        self.executable = executable
        self.arguments = arguments
        self.environment = environment
    }
}

/// What an ask is given to narrow it, as the engine reads it (`hearth.native.doorway`): an
/// account's id, a day, a number of entries. Only text and whole numbers ever cross.
public enum Asked: Sendable, Hashable {
    case text(String)
    case number(Int)

    var json: Any {
        switch self {
        case .text(let text): text
        case .number(let number): number
        }
    }
}

/// Everything an ask is given, by the name the engine reads it by.
public typealias Given = [String: Asked]

/// Something that answers when asked for a screen: the engine, or a stand-in for it in tests.
public protocol Asking: Sendable {
    func ask<Content: Decodable & Sendable>(
        _ screen: String, with given: Given, as content: Content.Type
    ) async throws -> Answer<Content>
}

extension Asking {
    /// A screen asked for as it is, narrowed by nothing.
    public func ask<Content: Decodable & Sendable>(
        _ screen: String, as content: Content.Type = Content.self
    ) async throws -> Answer<Content> {
        try await ask(screen, with: [:], as: content)
    }
}

/// Something the app can ask to do something, not only to show something: the engine, or a
/// stand-in for it in tests.
public protocol Acting: Asking {
    /// Asks hearth to do `action`, which it does in the background; how it goes is a screen.
    func act(_ action: String) async throws -> ActOutcome
}

/// What the engine says when the app signs in or out (`hearth.native.bridge.Outcome`).
public enum SignInOutcome: String, Decodable, Sendable, CaseIterable {
    case signedIn = "signed_in"
    /// The username or the password is wrong. The engine never says which.
    case rejected
    /// Too many recent failures, for this username or from this app.
    case throttled
    /// The password is a one-time password, changed in hearth's web pages before the app may
    /// use it. The engine stays signed out.
    case mustChangePassword = "must_change_password"
    case signedOut = "signed_out"
}

/// A running engine the app can sign in to, ask, and stop: the engine, or a stand-in for it.
public protocol Bridging: Acting {
    /// Signs in as a member. Whoever was signed in before is not any more, whatever the outcome.
    func signIn(username: String, password: String) async throws -> SignInOutcome
    func signOut() async throws
    func stop() async
}

/// Hearth's engine, running as a process of its own.
///
/// The engine is hearth's Python (`hearth bridge`). The app writes it one request a line and
/// reads one reply a line (`hearth.native.stdio`). It opens no port, and it ends when the app
/// closes its end of the pipe, which the system does for the app if the app dies.
public actor Engine: Bridging {
    public enum Failure: Error, Equatable {
        /// The engine is not running: it was stopped, it exited, or it never started.
        case stopped
        /// The engine refused a request as malformed, which is a fault in the app.
        case refused
    }

    private let process: Process
    private let requests: FileHandle
    private var nextID = 1
    private var waiting: [Int: CheckedContinuation<Data, any Error>] = [:]
    private var stopped = false

    /// Starts the engine and begins listening for its replies.
    public static func launch(_ command: EngineCommand) throws -> Engine {
        // Writing to an engine that has exited must be an error the app can show, not the
        // signal that would end the app.
        signal(SIGPIPE, SIG_IGN)
        let process = Process()
        process.executableURL = command.executable
        process.arguments = command.arguments
        process.environment = command.environment
        let requests = Pipe()
        let replies = Pipe()
        process.standardInput = requests
        process.standardOutput = replies
        // What the engine says for a person goes where the app's own log goes.
        process.standardError = FileHandle.standardError
        try process.run()
        let engine = Engine(process: process, requests: requests.fileHandleForWriting)
        let lines = replies.fileHandleForReading
        Task.detached {
            await engine.listen(to: lines)
        }
        return engine
    }

    private init(process: Process, requests: FileHandle) {
        self.process = process
        self.requests = requests
    }

    public func ask<Content: Decodable & Sendable>(
        _ screen: String, with given: Given, as content: Content.Type = Content.self
    ) async throws -> Answer<Content> {
        var request: [String: Any] = ["ask": screen]
        if !given.isEmpty {
            request["with"] = given.mapValues(\.json)
        }
        let line = try await send(request)
        return try replyDecoder().decode(Reply<Content>.self, from: line).answer
    }

    /// The password is written to the engine's pipe and kept by neither side.
    public func signIn(username: String, password: String) async throws -> SignInOutcome {
        let line = try await send(["sign_in": ["username": username, "password": password]])
        return try replyDecoder().decode(Signed.self, from: line).outcome
    }

    public func signOut() async throws {
        _ = try await send(["sign_out": true])
    }

    public func act(_ action: String) async throws -> ActOutcome {
        let line = try await send(["act": action])
        return try replyDecoder().decode(Acted.self, from: line).outcome
    }

    /// Closes the app's end, which is how the engine is told to finish.
    public func stop() {
        guard !stopped else { return }
        try? requests.close()
        finish()
    }

    private func send(_ request: [String: Any]) async throws -> Data {
        guard !stopped else { throw Failure.stopped }
        let id = nextID
        nextID += 1
        // Keys in one order, so the same request is always the same line.
        var line = try JSONSerialization.data(
            withJSONObject: request.merging(["id": id]) { $1 }, options: [.sortedKeys])
        line.append(0x0A)
        return try await withCheckedThrowingContinuation { continuation in
            waiting[id] = continuation
            do {
                try requests.write(contentsOf: line)
            } catch {
                waiting[id] = nil
                continuation.resume(throwing: Failure.stopped)
            }
        }
    }

    private func listen(to replies: FileHandle) async {
        do {
            for try await line in replies.bytes.lines {
                receive(Data(line.utf8))
            }
        } catch {
            // A broken pipe is the engine having gone, which `finish` reports.
        }
        finish()
    }

    private func receive(_ line: Data) {
        guard let reply = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else {
            return
        }
        guard let id = reply["id"] as? Int else {
            // A reply to no request is the engine refusing a line it could not read. The app
            // sends one request at a time per line, so whichever is oldest was the one.
            if let oldest = waiting.keys.min() {
                waiting.removeValue(forKey: oldest)?.resume(throwing: Failure.refused)
            }
            return
        }
        waiting.removeValue(forKey: id)?.resume(returning: line)
    }

    private func finish() {
        stopped = true
        let unanswered = waiting.values
        waiting = [:]
        for continuation in unanswered {
            continuation.resume(throwing: Failure.stopped)
        }
    }
}

extension EngineCommand {
    /// The engine an app carries in its resources (`apple/scripts/bundle-engine.sh`): its own
    /// Python, run in isolated mode so that nothing on this Mac changes what it imports, and
    /// told to write no bytecode, since the bundle is sealed by its signature.
    ///
    /// The engine is given a home, a place for temporary files, a text encoding and whatever
    /// `environment` adds, and nothing else of the app's environment.
    public static func bundled(
        in resources: URL, hearth arguments: [String], home: String, temporary: String,
        environment: [String: String] = [:]
    ) -> EngineCommand {
        let engine = resources.appendingPathComponent("engine")
        let base = ["HOME": home, "TMPDIR": temporary, "LC_ALL": "en_US.UTF-8"]
        return EngineCommand(
            executable: engine.appendingPathComponent("python/bin/python3.14"),
            arguments: ["-I", "-B", engine.appendingPathComponent("main.py").path] + arguments,
            environment: base.merging(environment) { $1 })
    }

    /// The engine an app carries, over the books `books` names: the invented household,
    /// signed in as its first member, or the household whose data is in `setup`, which says
    /// nothing until the app signs in as one of its members. It may ask the banks for what
    /// `consent` allows and nothing else, which the engine itself enforces.
    public static func bundled(
        _ books: Books, setup: Setup, consent: Consent = .nothing, in resources: URL, home: String,
        temporary: String
    ) -> EngineCommand {
        switch books {
        case .sample:
            bundled(
                in: resources, hearth: ["demo", "--bridge"] + consent.arguments, home: home,
                temporary: temporary)
        case .own:
            bundled(
                in: resources, hearth: ["bridge"] + consent.arguments, home: home, temporary: temporary,
                environment: ["HEARTH_DATA_DIR": setup.dataDirectory.path])
        }
    }
}
