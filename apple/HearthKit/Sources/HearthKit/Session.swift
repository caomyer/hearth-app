import Foundation
import Observation

/// The running app: which books it opened, the engine it started for them, and who is signed
/// in to it.
///
/// The app decides nothing about who may see what. It starts the engine over the books chosen
/// and hands it the username and password a member typed, or the ones it saved for them in
/// the Keychain; the engine checks them, every time, and answers nothing about a household
/// until it has (`hearth.native.bridge`).
@MainActor
@Observable
public final class Session {
    public enum Screen: Equatable, Sendable {
        /// Nothing decided yet.
        case starting
        /// Which books to open. A household set up on this Mac is offered only when there is one.
        case choosing(offersSetup: Bool)
        /// The engine is starting, or being handed the saved sign-in.
        case opening(Books)
        /// The household's books are open, and a member is to sign in to them.
        case signIn(SignInProblem?)
        /// The books are open, and the Overview shows them.
        case open(Books)
        case unavailable(Trouble)
    }

    /// Why the last sign-in did not get in.
    public enum SignInProblem: Equatable, Sendable {
        /// The username or the password is wrong; which of the two is never known here.
        case rejected
        case throttled
        /// A one-time password, to be changed in hearth's web pages first.
        case mustChangePassword
        /// The sign-in saved on this Mac no longer works, and has been forgotten.
        case savedSignInFailed
    }

    public enum Trouble: Equatable, Sendable {
        /// The engine could not be started at all.
        case engineMissing
        /// The engine stopped while the app was talking to it.
        case engineStopped
    }

    /// Starts the engine for the books given.
    public typealias Launch = @MainActor (Books) throws -> any Bridging

    public private(set) var screen: Screen = .starting
    /// The screens of the books, while they are open to someone.
    public private(set) var desk: Desk?
    /// The Overview screen, while the books are open to someone.
    public var overview: OverviewModel? { desk?.overview }
    /// True while a sign-in the person asked for is with the engine.
    public private(set) var isSigningIn = false
    public let setup: Setup

    private let defaults: UserDefaults
    private let credentials: any CredentialStore
    private let launch: Launch
    private var engine: (any Bridging)?
    private var running: Books?
    /// Bumped whenever what the app is doing changes, so an answer to something it has since
    /// moved on from is let go.
    private var generation = 0

    public init(
        setup: Setup, defaults: UserDefaults, credentials: any CredentialStore,
        launch: @escaping Launch
    ) {
        self.setup = setup
        self.defaults = defaults
        self.credentials = credentials
        self.launch = launch
    }

    /// The books chosen the last time, for this household's data directory.
    public var remembered: Books? {
        defaults.string(forKey: rememberedKey).flatMap(Books.init(rawValue:))
    }

    /// The books open or opening, if any.
    public var books: Books? {
        switch screen {
        case .opening(let books), .open(let books): books
        case .signIn: .own
        case .starting, .choosing, .unavailable: nil
        }
    }

    /// Whether a member is signed in, so there is something to sign out of.
    public var isSignedIn: Bool { screen == .open(.own) }

    /// On launch: the books chosen last time, or the choice.
    public func start() async {
        guard screen == .starting else { return }
        if let books = remembered, books == .sample || setup.isPresent {
            await open(books)
        } else {
            screen = .choosing(offersSetup: setup.isPresent)
        }
    }

    /// Opens `books` and remembers the choice for the next launch.
    public func choose(_ books: Books) async {
        guard books == .sample || setup.isPresent else {
            screen = .choosing(offersSetup: false)
            return
        }
        defaults.set(books.rawValue, forKey: rememberedKey)
        await open(books)
    }

    /// Hands the engine what the person typed. Saved in the Keychain only once it gets in.
    public func signIn(username: String, password: String) async {
        guard case .signIn = screen, !isSigningIn, let engine, running == .own else { return }
        isSigningIn = true
        defer { isSigningIn = false }
        let mine = generation
        do {
            let outcome = try await engine.signIn(username: username, password: password)
            guard mine == generation else { return }
            switch outcome {
            case .signedIn:
                // Not being able to save it costs only being asked again next time.
                try? await credentials.save(Credential(username: username, password: password), for: setup.key)
                guard mine == generation else { return }
                show(.own, on: engine)
            case .rejected, .signedOut: screen = .signIn(.rejected)
            case .throttled: screen = .signIn(.throttled)
            case .mustChangePassword: screen = .signIn(.mustChangePassword)
            }
        } catch {
            guard mine == generation else { return }
            screen = .unavailable(.engineStopped)
        }
    }

    /// Signs out of the engine and forgets the saved sign-in.
    public func signOut() async {
        guard isSignedIn, let engine else { return }
        generation += 1
        desk = nil
        screen = .signIn(nil)
        try? await credentials.forget(for: setup.key)
        try? await engine.signOut()
    }

    /// Stops the engine, as the app quits.
    public func stop() async {
        generation += 1
        desk = nil
        await stopEngine()
    }

    private var rememberedKey: String { "books \(setup.key)" }

    private func open(_ books: Books) async {
        generation += 1
        let mine = generation
        desk = nil
        if running != books || engine == nil {
            await stopEngine()
            guard mine == generation else { return }
            do {
                engine = try launch(books)
                running = books
            } catch {
                screen = .unavailable(.engineMissing)
                return
            }
        }
        guard let engine else { return }
        switch books {
        case .sample:
            show(.sample, on: engine)
        case .own:
            // Whoever was signed in before is not, until the engine has checked again.
            screen = .opening(.own)
            try? await engine.signOut()
            guard mine == generation else { return }
            await presentSaved(to: engine, generation: mine)
        }
    }

    /// Hands the engine the sign-in saved for this household, if there is one.
    private func presentSaved(to engine: any Bridging, generation mine: Int) async {
        let saved = try? await credentials.read(for: setup.key)
        guard mine == generation else { return }
        guard let saved else {
            screen = .signIn(nil)
            return
        }
        screen = .opening(.own)
        do {
            let outcome = try await engine.signIn(username: saved.username, password: saved.password)
            guard mine == generation else { return }
            switch outcome {
            case .signedIn:
                show(.own, on: engine)
            case .throttled:
                screen = .signIn(.throttled)
            case .rejected, .mustChangePassword, .signedOut:
                try? await credentials.forget(for: setup.key)
                guard mine == generation else { return }
                screen = .signIn(.savedSignInFailed)
            }
        } catch {
            guard mine == generation else { return }
            screen = .unavailable(.engineStopped)
        }
    }

    private func show(_ books: Books, on engine: any Bridging) {
        let mine = generation
        desk = Desk(engine: engine, signedOut: { [weak self] in self?.endedElsewhere(mine) })
        screen = .open(books)
    }

    /// The engine signed the member out on its own, as it does when their password is reset
    /// or their session runs its course: what is on screen goes, and the saved sign-in is
    /// tried once more.
    private func endedElsewhere(_ shown: Int) {
        guard shown == generation, running == .own, let engine else { return }
        generation += 1
        let mine = generation
        desk = nil
        screen = .opening(.own)
        Task { await presentSaved(to: engine, generation: mine) }
    }

    private func stopEngine() async {
        let stopping = engine
        engine = nil
        running = nil
        await stopping?.stop()
    }
}
