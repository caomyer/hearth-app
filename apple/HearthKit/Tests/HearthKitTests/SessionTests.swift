import Foundation
import Testing

@testable import HearthKit

private let ready = #"""
    {"state":"ready","computed_at":"2026-10-15T12:00:00Z","as_of":"2026-10-15","currency":"USD",
     "recomputing":false,"connected":true,
     "net_worth":{"value":"21038.25","is_known":true,"is_partial":false,"is_floor":false,"without":[],
                  "since_month_start":null,"over_year":null,"priced_on":null,"stale_prices":0},
     "history":[{"day":"2026-10-15","value":"21038.25","is_floor":false}]}
    """#
private let signedOut = #"{"state":"signed_out"}"#

/// An engine for the session's tests: sign-ins answered in order from a script, every ask
/// answered with what `answer` holds, and everything it was told noted.
private actor ScriptedBridge: Bridging {
    let books: Books
    private var outcomes: [SignInOutcome]
    var answer: String
    private(set) var signIns: [Credential] = []
    private(set) var signOuts = 0
    private(set) var stopped = false
    private(set) var failing = false

    init(_ books: Books, outcomes: [SignInOutcome] = [], answer: String = ready) {
        self.books = books
        self.outcomes = outcomes
        self.answer = answer
    }

    func ask<Content: Decodable & Sendable>(_ screen: String, with given: Given, as content: Content.Type) async throws -> Answer<Content> {
        guard !stopped, !failing else { throw Engine.Failure.stopped }
        return try decode(answer)
    }

    func signIn(username: String, password: String) async throws -> SignInOutcome {
        guard !stopped, !failing else { throw Engine.Failure.stopped }
        signIns.append(Credential(username: username, password: password))
        return outcomes.isEmpty ? .rejected : outcomes.removeFirst()
    }

    func signOut() async throws {
        guard !stopped, !failing else { throw Engine.Failure.stopped }
        signOuts += 1
    }

    func stop() async { stopped = true }

    func answer(with json: String) { answer = json }
    func fail() { failing = true }
    func expect(_ more: [SignInOutcome]) { outcomes += more }
}

/// The Keychain's stand-in: sign-ins kept in memory, by household.
private actor MemoryCredentials: CredentialStore {
    private(set) var kept: [String: Credential] = [:]

    init(_ kept: [String: Credential] = [:]) {
        self.kept = kept
    }

    func read(for household: String) async throws -> Credential? { kept[household] }
    func save(_ credential: Credential, for household: String) async throws { kept[household] = credential }
    func forget(for household: String) async throws { kept[household] = nil }
}

/// A household's data directory, with hearth's database in it or not.
private func household(present: Bool) throws -> Setup {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("hearth-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    if present {
        try Data().write(to: directory.appendingPathComponent("hearth.sqlite3"))
    }
    return Setup(dataDirectory: directory)
}

@MainActor
private final class Harness {
    let setup: Setup
    let defaults = UserDefaults(suiteName: "hearth-tests-\(UUID().uuidString)")!
    let credentials: MemoryCredentials
    var launched: [ScriptedBridge] = []
    var next: [Books: ScriptedBridge] = [:]
    var canLaunch = true

    init(present: Bool = true, saved: Credential? = nil) throws {
        let setup = try household(present: present)
        self.setup = setup
        credentials = MemoryCredentials(saved.map { [setup.key: $0] } ?? [:])
    }

    func session() -> Session {
        Session(setup: setup, defaults: defaults, credentials: credentials) { [unowned self] books in
            guard self.canLaunch else { throw Engine.Failure.stopped }
            let bridge = self.next.removeValue(forKey: books) ?? ScriptedBridge(books)
            self.launched.append(bridge)
            return bridge
        }
    }

    var last: ScriptedBridge { launched.last! }
}

private let avery = Credential(username: "avery", password: "an invented passphrase")

@Suite("Opening books and signing in")
@MainActor
struct SessionTests {
    @Test("a Mac with hearth set up offers it beside the sample, and nothing is started yet")
    func offersSetup() async throws {
        let harness = try Harness(present: true)
        let session = harness.session()

        await session.start()

        #expect(session.screen == .choosing(offersSetup: true))
        #expect(harness.launched.isEmpty)
        #expect(session.overview == nil)
    }

    @Test("a Mac without hearth offers only the sample")
    func offersOnlySample() async throws {
        let harness = try Harness(present: false)
        let session = harness.session()

        await session.start()
        #expect(session.screen == .choosing(offersSetup: false))

        await session.choose(.own)
        #expect(session.screen == .choosing(offersSetup: false))
        #expect(harness.launched.isEmpty)
    }

    @Test("the sample opens at once, signed in by the engine, and is remembered")
    func sample() async throws {
        let harness = try Harness()
        let session = harness.session()
        await session.start()

        await session.choose(.sample)

        #expect(session.screen == .open(.sample))
        #expect(session.overview != nil)
        #expect(await harness.last.signIns.isEmpty)
        #expect(!session.isSignedIn)

        let again = harness.session()
        await again.start()
        #expect(again.screen == .open(.sample))
    }

    @Test("with nothing saved, the household's books ask who is signing in")
    func asksToSignIn() async throws {
        let harness = try Harness()
        let session = harness.session()
        await session.start()

        await session.choose(.own)

        #expect(session.screen == .signIn(nil))
        #expect(session.overview == nil)
        #expect(harness.launched.map(\.books) == [.own])
    }

    @Test("a sign-in the engine accepts opens the books and is saved for next time")
    func signsIn() async throws {
        let harness = try Harness()
        harness.next[.own] = ScriptedBridge(.own, outcomes: [.signedIn])
        let session = harness.session()
        await session.start()
        await session.choose(.own)

        await session.signIn(username: avery.username, password: avery.password)

        #expect(session.screen == .open(.own))
        #expect(session.isSignedIn)
        #expect(await harness.last.signIns == [avery])
        #expect(await harness.credentials.kept[harness.setup.key] == avery)
        await session.overview?.refresh()
        guard case .ready(let overview) = session.overview?.state else {
            Issue.record("the overview is ready")
            return
        }
        #expect(overview.netWorth.value == money("21038.25"))
    }

    @Test("each refusal is said, and nothing is saved or shown")
    func refusals() async throws {
        let harness = try Harness()
        harness.next[.own] = ScriptedBridge(.own, outcomes: [.rejected, .throttled, .mustChangePassword])
        let session = harness.session()
        await session.start()
        await session.choose(.own)

        await session.signIn(username: "avery", password: "wrong")
        #expect(session.screen == .signIn(.rejected))
        await session.signIn(username: "avery", password: "wrong again")
        #expect(session.screen == .signIn(.throttled))
        await session.signIn(username: "avery", password: "one-time")
        #expect(session.screen == .signIn(.mustChangePassword))

        #expect(session.overview == nil)
        #expect(await harness.credentials.kept.isEmpty)
    }

    @Test("the saved sign-in is handed to the engine on every launch, which checks it")
    func savedSignIn() async throws {
        let harness = try Harness(saved: avery)
        harness.defaults.set("own", forKey: "books \(harness.setup.key)")
        harness.next[.own] = ScriptedBridge(.own, outcomes: [.signedIn])
        let session = harness.session()

        await session.start()

        #expect(session.screen == .open(.own))
        #expect(await harness.last.signIns == [avery])
    }

    @Test("a saved sign-in the engine refuses is forgotten, and the person asked")
    func savedSignInRefused() async throws {
        for refusal in [SignInOutcome.rejected, .mustChangePassword] {
            let harness = try Harness(saved: avery)
            harness.defaults.set("own", forKey: "books \(harness.setup.key)")
            harness.next[.own] = ScriptedBridge(.own, outcomes: [refusal])
            let session = harness.session()

            await session.start()

            #expect(session.screen == .signIn(.savedSignInFailed))
            #expect(await harness.credentials.kept.isEmpty)
        }
    }

    @Test("a throttled saved sign-in is kept, to be tried again later")
    func savedSignInThrottled() async throws {
        let harness = try Harness(saved: avery)
        harness.defaults.set("own", forKey: "books \(harness.setup.key)")
        harness.next[.own] = ScriptedBridge(.own, outcomes: [.throttled])
        let session = harness.session()

        await session.start()

        #expect(session.screen == .signIn(.throttled))
        #expect(await harness.credentials.kept[harness.setup.key] == avery)
    }

    @Test("signing out forgets the saved sign-in and tells the engine")
    func signsOut() async throws {
        let harness = try Harness(saved: avery)
        harness.defaults.set("own", forKey: "books \(harness.setup.key)")
        harness.next[.own] = ScriptedBridge(.own, outcomes: [.signedIn])
        let session = harness.session()
        await session.start()
        let signOutsBefore = await harness.last.signOuts

        await session.signOut()

        #expect(session.screen == .signIn(nil))
        #expect(session.overview == nil)
        #expect(!session.isSignedIn)
        #expect(await harness.credentials.kept.isEmpty)
        #expect(await harness.last.signOuts == signOutsBefore + 1)
        #expect(harness.session().remembered == .own)
    }

    @Test("when the engine signs the member out itself, the screen goes and the saved sign-in is tried again")
    func endedElsewhere() async throws {
        let harness = try Harness(saved: avery)
        harness.defaults.set("own", forKey: "books \(harness.setup.key)")
        let bridge = ScriptedBridge(.own, outcomes: [.signedIn, .rejected])
        harness.next[.own] = bridge
        let session = harness.session()
        await session.start()
        await bridge.answer(with: signedOut)

        await session.overview?.refresh()
        // The saved sign-in is tried in the background; it is refused, as after a reset.
        for _ in 0..<200 where session.screen != .signIn(.savedSignInFailed) {
            try await Task.sleep(for: .milliseconds(5))
        }

        #expect(session.screen == .signIn(.savedSignInFailed))
        #expect(session.overview == nil)
        #expect(await bridge.signIns == [avery, avery])
        #expect(await harness.credentials.kept.isEmpty)
    }

    @Test("switching books stops the engine of the old ones and starts one for the new")
    func switches() async throws {
        let harness = try Harness()
        harness.next[.own] = ScriptedBridge(.own, outcomes: [.signedIn])
        let session = harness.session()
        await session.start()
        await session.choose(.own)
        await session.signIn(username: avery.username, password: avery.password)
        let own = harness.last

        await session.choose(.sample)

        #expect(session.screen == .open(.sample))
        #expect(await own.stopped)
        #expect(harness.launched.map(\.books) == [.own, .sample])
        #expect(harness.session().remembered == .sample)

        harness.next[.own] = ScriptedBridge(.own, outcomes: [.signedIn])
        await session.choose(.own)
        // The sign-in saved a moment ago is handed to the new engine.
        #expect(session.screen == .open(.own))
        #expect(await harness.last.signIns == [avery])
    }

    @Test("an engine that cannot start, or stops, is said to")
    func trouble() async throws {
        let harness = try Harness()
        harness.canLaunch = false
        let session = harness.session()
        await session.start()

        await session.choose(.sample)
        #expect(session.screen == .unavailable(.engineMissing))

        harness.canLaunch = true
        await session.choose(.own)
        #expect(session.screen == .signIn(nil))
        await harness.last.fail()
        await session.signIn(username: "avery", password: "anything")
        #expect(session.screen == .unavailable(.engineStopped))
    }

    @Test("a remembered choice of books no longer on this Mac asks again")
    func goneAway() async throws {
        let harness = try Harness(present: false)
        harness.defaults.set("own", forKey: "books \(harness.setup.key)")
        let session = harness.session()

        await session.start()

        #expect(session.screen == .choosing(offersSetup: false))
    }
}

@Suite("Where hearth keeps a household")
struct SetupTests {
    @Test("hearth's own default, unless the environment names another")
    func dataDirectory() {
        #expect(Setup.from(environment: [:], home: "/Users/someone").dataDirectory.path == "/Users/someone/.local/share/hearth")
        #expect(Setup.from(environment: ["HEARTH_DATA_DIR": "/tmp/fixture"], home: "/Users/someone").dataDirectory.path == "/tmp/fixture")
        #expect(Setup.from(environment: ["HEARTH_DATA_DIR": ""], home: "/Users/someone").dataDirectory.path == "/Users/someone/.local/share/hearth")
    }

    @Test("a household is there only when hearth's database is")
    func presence() throws {
        #expect(try household(present: true).isPresent)
        #expect(try !household(present: false).isPresent)
        #expect(!Setup(dataDirectory: URL(fileURLWithPath: "/nonexistent/hearth")).isPresent)
    }

    @Test("the engine for a household is told where it is, and nothing else of the app's")
    func command() {
        let setup = Setup(dataDirectory: URL(fileURLWithPath: "/Users/someone/.local/share/hearth"))
        let resources = URL(fileURLWithPath: "/Applications/Hearth.app/Contents/Resources")

        let own = EngineCommand.bundled(.own, setup: setup, in: resources, home: "/Users/someone", temporary: "/tmp/x")
        let sample = EngineCommand.bundled(.sample, setup: setup, in: resources, home: "/Users/someone", temporary: "/tmp/x")

        #expect(own.arguments.suffix(1) == ["bridge"])
        #expect(own.environment == [
            "HOME": "/Users/someone", "TMPDIR": "/tmp/x", "LC_ALL": "en_US.UTF-8",
            "HEARTH_DATA_DIR": "/Users/someone/.local/share/hearth",
        ])
        #expect(sample.arguments.suffix(2) == ["demo", "--bridge"])
        #expect(sample.environment["HEARTH_DATA_DIR"] == nil)
    }
}
