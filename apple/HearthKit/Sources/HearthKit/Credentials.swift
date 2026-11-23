import Foundation
import Security

/// A member's username and password, as they typed them to sign in.
public struct Credential: Codable, Sendable, Equatable {
    public let username: String
    public let password: String

    public init(username: String, password: String) {
        self.username = username
        self.password = password
    }
}

/// Where the app keeps a member's sign-in between launches, one per household: the Keychain,
/// or a stand-in for it in tests. The app never checks a password itself; it hands what it
/// keeps to the engine, which does.
public protocol CredentialStore: Sendable {
    func read(for household: String) async throws -> Credential?
    func save(_ credential: Credential, for household: String) async throws
    func forget(for household: String) async throws
}

/// The login keychain: one generic password item per household, under the app's own service
/// name, with the household's data directory as its account.
///
/// The item is the file-based login keychain's, so a build that is not provisioned for the
/// data protection keychain can keep it too; only the app that wrote it reads it without the
/// system asking the person first.
public struct Keychain: CredentialStore {
    public enum Failure: Error, Equatable {
        case status(OSStatus)
    }

    public static let service = "Hearth"

    public let service: String

    public init(service: String = Keychain.service) {
        self.service = service
    }

    public func read(for household: String) async throws -> Credential? {
        var query = item(for: household)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var found: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &found)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = found as? Data else { throw Failure.status(status) }
        // An item this app did not write, or wrote in another shape, holds no sign-in.
        return try? JSONDecoder().decode(Credential.self, from: data)
    }

    public func save(_ credential: Credential, for household: String) async throws {
        let data = try JSONEncoder().encode(credential)
        let updated = SecItemUpdate(
            item(for: household) as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if updated == errSecSuccess { return }
        guard updated == errSecItemNotFound else { throw Failure.status(updated) }
        var added = item(for: household)
        added[kSecValueData as String] = data
        added[kSecAttrLabel as String] = "Hearth sign-in"
        let status = SecItemAdd(added as CFDictionary, nil)
        guard status == errSecSuccess else { throw Failure.status(status) }
    }

    public func forget(for household: String) async throws {
        let status = SecItemDelete(item(for: household) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw Failure.status(status) }
    }

    private func item(for household: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: household,
            kSecUseDataProtectionKeychain as String: false,
        ]
    }
}
