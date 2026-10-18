"""A string that refuses to be printed."""

import hmac
from typing import Never


class Secret:
    """Holds a secret value and keeps it out of every repr, str, format, log line and pickle.

    The value is only reachable through `reveal()`, so the one place a token is handed to a
    client library is explicit and easy to find.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("a secret must be a non-empty string")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    __hash__ = None  # type: ignore[assignment]

    def __reduce__(self) -> Never:
        raise TypeError("a Secret cannot be pickled")


class KeyMaterial:
    """Raw key bytes with the same protections as `Secret`, so keys never print either."""

    __slots__ = ("_value",)

    def __init__(self, value: bytes) -> None:
        if not isinstance(value, bytes) or not value:
            raise ValueError("key material must be non-empty bytes")
        self._value = value

    def reveal(self) -> bytes:
        return self._value

    def __repr__(self) -> str:
        return "KeyMaterial('***')"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, KeyMaterial):
            return NotImplemented
        return hmac.compare_digest(self._value, other._value)

    __hash__ = None  # type: ignore[assignment]

    def __reduce__(self) -> Never:
        raise TypeError("key material cannot be pickled")
