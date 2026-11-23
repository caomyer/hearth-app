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

/// Something that answers when asked for a screen: the engine, or a stand-in for it in tests.
public protocol Asking: Sendable {
    func ask<Content: Decodable & Sendable>(
        _ screen: String, as content: Content.Type
    ) async throws -> Answer<Content>
}

/// Hearth's engine, running as a process of its own.
///
/// The engine is hearth's Python (`hearth bridge`). The app writes it one request a line and
/// reads one reply a line (`hearth.native.stdio`). It opens no port, and it ends when the app
/// closes its end of the pipe, which the system does for the app if the app dies.
public actor Engine: Asking {
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
        _ screen: String, as content: Content.Type = Content.self
    ) async throws -> Answer<Content> {
        let line = try await send(screen)
        return try replyDecoder().decode(Reply<Content>.self, from: line).answer
    }

    /// Closes the app's end, which is how the engine is told to finish.
    public func stop() {
        guard !stopped else { return }
        try? requests.close()
        finish()
    }

    private func send(_ screen: String) async throws -> Data {
        guard !stopped else { throw Failure.stopped }
        let id = nextID
        nextID += 1
        var line = try JSONSerialization.data(withJSONObject: ["id": id, "ask": screen])
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
    /// The engine is given a home, a place for temporary files and a text encoding, and nothing
    /// else of the app's environment.
    public static func bundled(
        in resources: URL, hearth arguments: [String], home: String, temporary: String
    ) -> EngineCommand {
        let engine = resources.appendingPathComponent("engine")
        return EngineCommand(
            executable: engine.appendingPathComponent("python/bin/python3.14"),
            arguments: ["-I", "-B", engine.appendingPathComponent("main.py").path] + arguments,
            environment: ["HOME": home, "TMPDIR": temporary, "LC_ALL": "en_US.UTF-8"])
    }
}
