"""Password hashing with Argon2id."""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12
MAX_LENGTH = 256

_hasher = PasswordHasher()  # Argon2id with the RFC 9106 low-memory profile.

# Verified against when a username does not exist, so that an unknown username
# costs the same time as a wrong password and cannot be told apart by timing.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))

_TEMPORARY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class PasswordRejectedError(ValueError):
    """A proposed password does not meet the rules."""


def check_acceptable(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordRejectedError(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise PasswordRejectedError(f"Use at most {MAX_LENGTH} characters.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


def verify_nothing(password: str) -> None:
    """Spend the time a real verification would, for a username that does not exist."""
    verify(_DUMMY_HASH, password)


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def temporary_password() -> str:
    """A one-time password an admin hands over in person; about 79 bits of entropy."""
    groups = ("".join(secrets.choice(_TEMPORARY_ALPHABET) for _ in range(4)) for _ in range(4))
    return "-".join(groups)
