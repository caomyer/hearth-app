"""The base: a ledger the household kept before hearth, imported once and frozen.

A person who already keeps a beancount ledger imports it once (``hearth import-ledger``,
``hearth.ledger_import``). The import copies every file the ledger reads, includes resolved,
byte for byte, into hearth's data directory, where hearth owns it: written once, at the import,
and never again. From then on the app is the truth, and the base is where its books start.

On disk, in the data directory::

    ledger-base/
        manifest.json          what was imported, from where, when, and its digest
        <digest>/              the files as they were, byte for byte
            main.beancount     at their paths relative to the folder that held them all
            years/2026.beancount

Each file keeps its path relative to the nearest folder that held every file the ledger read,
so an `include` relative to the file that has it, a nested one and a glob all resolve in the
copy exactly as they did in the original, and a glob finds there only the files it found
then. An include by an absolute path would still lead out of the copy, back to the original;
the import refuses it, and every load of the base checks it reads no file outside the base.

The digest names the contents (`digest`): any byte of any file, any file's path, or which file
the ledger is opened from changes it. A base is loaded only once its files are checked against
it (`Base.verify`), so a base changed on disk is refused, never read.

`manifest.json` is written last, whole, in place of the one before, so a reader finds either
the old base or the new one, never half of either. A base it no longer names is kept in its
own folder, never discarded.

This module only keeps the base. Reading it is ``hearth.books``'s, through the same path as
any ledger (``books.load_ledger``).
"""

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

SCHEMA = "hearth.base.v1"
MANIFEST = "manifest.json"
NOTE = (
    "Hearth's frozen copy of the household's ledger, as it was imported. Hearth reads it and"
    " never writes it again; do not edit it. It is an input, unlike books.beancount beside it,"
    " which hearth writes on every recompute and never reads."
)


class BaseError(Exception):
    """The base on disk is not one hearth can trust: say why, naming no figure."""


@dataclass(frozen=True, slots=True)
class Base:
    """A frozen ledger's files: `files` are paths relative to `directory`, as POSIX paths,
    sorted; `root` is the one the ledger is opened from."""

    directory: Path
    root: str
    files: tuple[str, ...]
    digest: str

    @property
    def root_path(self) -> Path:
        return self.directory / self.root

    def paths(self) -> list[Path]:
        return [self.directory / name for name in self.files]

    def verify(self) -> None:
        """Raise `BaseError` unless every file is still exactly as it was imported."""
        try:
            contents = {name: (self.directory / name).read_bytes() for name in self.files}
        except OSError as error:
            missing = f"The imported ledger in {self.directory} is missing a file."
            raise BaseError(missing) from error
        if digest(self.root, contents) != self.digest:
            raise BaseError(
                f"The imported ledger in {self.directory} is not as it was imported:"
                " a file in it was changed."
            )


@dataclass(frozen=True, slots=True)
class Imported:
    """The base the manifest names, and what it says of its import."""

    base: Base
    source: Path
    """The file the ledger was opened from when it was imported, outside hearth."""
    imported_at: datetime
    rulings_through: int
    """The newest review ruling's id when it was imported, 0 for none: a ruling made after
    it rests on this base (``hearth.ledger_import``)."""


def digest(root: str, files: Mapping[str, bytes]) -> str:
    """The sha256 that names a base: of the schema, the root's path, and each file's path,
    length and bytes, in order of path.

    Every part is delimited, so no two different bases share the bytes it is taken over.
    """
    if root not in files:
        raise ValueError(f"the root {root} is not among the files")
    hashed = hashlib.sha256()
    hashed.update(f"{SCHEMA}\0{root}\0".encode())
    for name in sorted(files):
        content = files[name]
        hashed.update(f"{name}\0{len(content)}\0".encode())
        hashed.update(content)
    return hashed.hexdigest()


def current(directory: Path) -> Imported | None:
    """The base kept in `directory` (``Settings.ledger_base``), or None when nothing was ever
    imported. Raises `BaseError` for a manifest hearth did not write as it stands."""
    directory = directory.absolute()
    manifest = directory / MANIFEST
    try:
        text = manifest.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise BaseError(f"{manifest} cannot be read: {error.strerror}.") from error
    try:
        found = json.loads(text)
        if found["schema"] != SCHEMA:
            raise BaseError(f"{manifest} is of a schema hearth does not read.")
        name = str(found["digest"])
        files = tuple(str(f["path"]) for f in found["files"])
        base = Base(directory / name, str(found["root"]), files, name)
        return Imported(
            base,
            Path(found["source"]),
            datetime.fromisoformat(found["imported_at"]),
            int(found["rulings_through"]),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise BaseError(f"{manifest} is not as hearth wrote it.") from error


def standing(directory: Path) -> str | None:
    """The digest of the base kept in `directory`, or None: cheap, and naming no figure."""
    found = current(directory)
    return found.base.digest if found is not None else None


def layout(paths: Sequence[Path], root: Path) -> tuple[Path, dict[str, Path]]:
    """The folder that holds every one of `paths`, and each path's name relative to it, as a
    POSIX path, as the base keeps it. `root` must be one of `paths`."""
    absolute = [Path(os.path.normpath(p)) for p in paths]
    common = Path(os.path.commonpath([p.parent for p in absolute]))
    names = {PurePosixPath(*p.relative_to(common).parts).as_posix(): p for p in absolute}
    if Path(os.path.normpath(root)) not in absolute:
        raise ValueError(f"the root {root} is not among the files")
    return common, names


IMPORTING = ".importing-"
"""How a folder an import stages a base in is named, in the data directory beside the base's
own."""
CHECKING = ".checking-"
"""How a folder a check of a ledger stages its copy in is named, inside the data directory."""
STALE = 600
"""How many seconds old a check's folder is before a sweep takes it for one left behind."""


@contextmanager
def staged(
    directory: Path, root: str, files: Mapping[str, bytes], *, prefix: str = IMPORTING
) -> Iterator[Base]:
    """`files` written as a base in a folder of their own inside `directory`, for the import to
    load before it commits to them (`commit`), or a check to load and let go; gone when the
    block ends unless committed. A process that dies inside it leaves the folder, which no
    manifest names, for `sweep` to take away."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=prefix, dir=directory))
    name = digest(root, files)
    try:
        folder = staging / name
        for relative, content in files.items():
            path = folder / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
            with os.fdopen(descriptor, "wb") as file:
                file.write(content)
        yield Base(folder, root, tuple(sorted(files)), name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


@contextmanager
def locked(directory: Path) -> Iterator[None]:
    """Hold the base's lock: every import holds it from asking whether the household may import
    through naming the new base, in this process or another, so imports take turns. The lock is
    the system's (`flock`), let go when the block ends or the process dies."""
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(_lock(directory), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _lock(directory: Path) -> Path:
    """The file the base's lock is held on: beside its directory, so asking for the lock
    makes no base directory where there is none."""
    return directory.with_name(f".{directory.name}.lock")


def sweep(directory: Path) -> None:
    """Take away what an import that died left in `directory`, the data directory: its staging
    folders, which no manifest names. Called holding the lock (`locked`), so no import is
    staging meanwhile."""
    if not directory.is_dir():
        return
    for left in directory.glob(f"{IMPORTING}*"):
        shutil.rmtree(left, ignore_errors=True)


def sweep_at_start(data_dir: Path, ledger_base: Path, now: float) -> None:
    """At start-up: sweep the base's directory if no import holds its lock, and take away a
    check's folder left in the data directory once it is older than any check takes."""
    if data_dir.is_dir():
        descriptor = os.open(_lock(ledger_base), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            sweep(data_dir)
        except BlockingIOError:
            pass
        finally:
            os.close(descriptor)
    if data_dir.is_dir():
        for left in data_dir.glob(f"{CHECKING}*"):
            try:
                old = now - left.stat().st_mtime > STALE
            except OSError:
                continue
            if old:
                shutil.rmtree(left, ignore_errors=True)


def commit(
    directory: Path, base: Base, *, source: Path, now: datetime, rulings_through: int
) -> Imported:
    """Keep the staged `base` in `directory` and name it in the manifest, in place of any
    base named before, which stays in its own folder. Returns what the manifest now says."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    kept = Base(directory / base.digest, base.root, base.files, base.digest)
    if kept.directory.exists():
        # The same contents imported before: the folder is that base already, if intact.
        kept.verify()
    else:
        base.directory.rename(kept.directory)
    imported = Imported(kept, source, now, rulings_through)
    document = {
        "schema": SCHEMA,
        "note": NOTE,
        "digest": base.digest,
        "root": base.root,
        "files": [
            {
                "path": name,
                "bytes": (kept.directory / name).stat().st_size,
                "sha256": hashlib.sha256((kept.directory / name).read_bytes()).hexdigest(),
            }
            for name in base.files
        ],
        "source": str(source),
        "imported_at": now.isoformat(),
        "rulings_through": rulings_through,
    }
    handle, partial = tempfile.mkstemp(dir=directory, prefix=f".{MANIFEST}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(document, file, indent=2, ensure_ascii=False)
            file.write("\n")
        Path(partial).replace(directory / MANIFEST)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise
    return imported
