"""Every failure the vault reports. None of their messages ever carries a key or a token."""


class VaultError(Exception):
    """A token could not be stored or read."""


class KeychainUnavailableError(VaultError):
    """The Keychain cannot be used, so no key can be stored or read."""


class MasterKeyMissingError(VaultError):
    """Tokens are encrypted under a master key the Keychain does not hold."""


class TokenNotFoundError(VaultError):
    """No token is stored for the member and Item."""


class TokenTamperedError(VaultError):
    """A stored token failed authentication: it was altered, or moved from another row."""
