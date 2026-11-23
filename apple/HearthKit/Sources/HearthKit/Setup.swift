import Foundation

/// Which books the app shows: a household's own, kept by hearth on this Mac, or hearth's
/// invented household.
public enum Books: String, Sendable, Equatable, CaseIterable {
    case own
    case sample
}

/// Where hearth keeps a household's data on this Mac, as `hearth serve` finds it
/// (`hearth.config.data_dir_from_environment`).
public struct Setup: Sendable, Equatable {
    public let dataDirectory: URL

    /// The variable that names another data directory, read by hearth and by the app alike.
    public static let variable = "HEARTH_DATA_DIR"

    public init(dataDirectory: URL) {
        self.dataDirectory = dataDirectory.standardizedFileURL
    }

    /// Hearth's own default, `~/.local/share/hearth`, unless the app's environment names
    /// another, as a test does to point the app at a household of its own.
    public static func from(environment: [String: String], home: String) -> Setup {
        if let named = environment[variable], !named.isEmpty {
            let path = (named as NSString).expandingTildeInPath
            return Setup(dataDirectory: URL(fileURLWithPath: path, isDirectory: true))
        }
        let home = URL(fileURLWithPath: home, isDirectory: true)
        return Setup(dataDirectory: home.appendingPathComponent(".local/share/hearth", isDirectory: true))
    }

    /// Whether a household is set up here: hearth's app database is what makes a directory
    /// one. Nothing in it is opened to find out.
    public var isPresent: Bool {
        var isDirectory: ObjCBool = false
        let database = dataDirectory.appendingPathComponent("hearth.sqlite3").path
        return FileManager.default.fileExists(atPath: database, isDirectory: &isDirectory)
            && !isDirectory.boolValue
    }

    /// What the app's choices and saved sign-in for this household are kept under.
    public var key: String { dataDirectory.path }
}
