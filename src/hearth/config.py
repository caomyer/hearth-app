"""Where hearth keeps its state, and the secrets it needs to run."""

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

DATA_DIR_VARIABLE = "HEARTH_DATA_DIR"
_SIGNING_KEY_FILE = "signing.key"


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    signing_key: bytes
    # Set when served over HTTPS, so the session cookie is never sent in clear.
    secure_cookies: bool = False

    @property
    def database(self) -> Path:
        return self.data_dir / "hearth.sqlite3"

    @classmethod
    def in_directory(cls, data_dir: Path, *, secure_cookies: bool = False) -> Settings:
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return cls(data_dir, _signing_key(data_dir), secure_cookies)

    @classmethod
    def from_environment(cls) -> Settings:
        default = Path.home() / ".local" / "share" / "hearth"
        data_dir = Path(os.environ.get(DATA_DIR_VARIABLE, default)).expanduser()
        return cls.in_directory(data_dir, secure_cookies=os.environ.get("HEARTH_HTTPS") == "1")


def _signing_key(data_dir: Path) -> bytes:
    """The key that signs session cookies, created on first run and readable only by its owner."""
    path = data_dir / _SIGNING_KEY_FILE
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(descriptor, "wb") as file:
        key = secrets.token_bytes(32)
        file.write(key)
    return key
