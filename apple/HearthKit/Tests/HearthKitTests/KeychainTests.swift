import Foundation
import Testing

@testable import HearthKit

/// The real login keychain, under a service of the test's own that it removes again. It writes
/// to the keychain of whoever runs it, so it runs only when asked:
/// `HEARTH_TEST_KEYCHAIN=1 swift test --package-path apple/HearthKit`.
@Suite(
    "The login keychain",
    .enabled(if: ProcessInfo.processInfo.environment["HEARTH_TEST_KEYCHAIN"] == "1"))
struct KeychainTests {
    @Test("keeps one sign-in per household, replaces it, and forgets it")
    func keeps() async throws {
        let keychain = Keychain(service: "Hearth tests \(UUID().uuidString)")
        let here = "/tmp/hearth-one", there = "/tmp/hearth-two"
        defer {
            Task {
                try? await keychain.forget(for: here)
                try? await keychain.forget(for: there)
            }
        }
        let first = Credential(username: "avery", password: "an invented passphrase")
        let second = Credential(username: "blake", password: "another invented one")

        #expect(try await keychain.read(for: here) == nil)
        try await keychain.save(first, for: here)
        #expect(try await keychain.read(for: here) == first)
        #expect(try await keychain.read(for: there) == nil)

        try await keychain.save(second, for: here)
        #expect(try await keychain.read(for: here) == second)

        try await keychain.forget(for: here)
        #expect(try await keychain.read(for: here) == nil)
        try await keychain.forget(for: here)
    }
}
