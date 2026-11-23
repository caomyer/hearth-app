import Foundation
import HearthKit
import Observation

/// The running app: the engine it started, and the screens that ask it.
///
/// For now the engine always serves hearth's invented household, and every screen says so.
/// Opening a household's real books comes with the next milestone.
@MainActor
@Observable
final class Session {
    /// The Overview screen, or nil when the engine could not be started at all.
    let overview: OverviewModel?
    /// True while the figures on screen are hearth's invented household.
    let isSample = true

    private let engine: Engine?

    init(bundle: Bundle = .main) {
        guard let resources = bundle.resourceURL else {
            engine = nil
            overview = nil
            return
        }
        let command = EngineCommand.bundled(
            in: resources, hearth: ["demo", "--bridge"],
            home: NSHomeDirectory(), temporary: NSTemporaryDirectory())
        do {
            let engine = try Engine.launch(command)
            self.engine = engine
            overview = OverviewModel(engine: engine)
        } catch {
            engine = nil
            overview = nil
        }
    }
}
