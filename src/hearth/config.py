"""Where hearth keeps its state, and the secrets it needs to run."""

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

DATA_DIR_VARIABLE = "HEARTH_DATA_DIR"
LEDGER_VARIABLE = "HEARTH_LEDGER"
DATABASE_FILE = "hearth.sqlite3"
"""The app database, whose presence in a data directory is what makes it a household's."""
_SIGNING_KEY_FILE = "signing.key"


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    signing_key: bytes
    # Set when served over HTTPS, so the session cookie is never sent in clear.
    secure_cookies: bool = False
    # A beancount ledger of the household's own, for hearth to inherit: optional, read and
    # never written. Without one, hearth keeps the books itself (``hearth.ownbooks``).
    ledger: Path | None = None

    @property
    def database(self) -> Path:
        return self.data_dir / DATABASE_FILE

    @property
    def books_file(self) -> Path:
        """The books as hearth keeps them, written on every recompute and never read back."""
        return self.data_dir / "books.beancount"

    @property
    def feed_database(self) -> Path:
        """What Plaid sent, kept apart from the app database, which holds no money."""
        return self.data_dir / "feed.sqlite3"

    @classmethod
    def in_directory(
        cls, data_dir: Path, *, secure_cookies: bool = False, ledger: Path | None = None
    ) -> Settings:
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return cls(data_dir, _signing_key(data_dir), secure_cookies, ledger)

    @classmethod
    def from_environment(cls) -> Settings:
        ledger = os.environ.get(LEDGER_VARIABLE)
        return cls.in_directory(
            data_dir_from_environment(),
            secure_cookies=os.environ.get("HEARTH_HTTPS") == "1",
            ledger=Path(ledger).expanduser() if ledger else None,
        )


def data_dir_from_environment() -> Path:
    """Where the environment says hearth keeps its state, whether or not anything is there."""
    default = Path.home() / ".local" / "share" / "hearth"
    return Path(os.environ.get(DATA_DIR_VARIABLE, default)).expanduser()


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
