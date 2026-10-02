"""Content-addressed objects under `state_dir/objects/` (architecture phase 4).

An object is a byte string named by the sha256 of its raw bytes and stored at
`objects/<class>/<aa>/<digest>`, where `aa` is the digest's first two hex
characters. Every class is stored gzip-compressed (mtime 0, no file name, so
equal bytes give an equal file), except `release_manifest`, which is stored raw
so a reader can `json.load` it.

A write streams into `objects/.writing-<uuid>`, fsyncs it, renames it into
place and fsyncs the directory, so a crash leaves at most a `.writing-` file,
never a partial object under its name. The collector removes such files after a
day. Callers write the file first and then its `objects` row (`record`) in the
transaction that references it, so a crash leaves only an unindexed file.

`blob_classes` in migration 36 holds the lifecycle of every class; `CLASSES`
mirrors it. This module never imports pyarrow.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import os
import re
import sqlite3
import uuid
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, cast

#: What a packed body column holds once its bytes are in an object.
STUB = '{"packed":true}'
#: A temporary object being written; never a finished object.
WRITING_PREFIX = ".writing-"
#: Bytes read or hashed at a time.
CHUNK_BYTES = 1 << 20
#: gzip level for stored objects: close to level 9 in size and much faster.
COMPRESS_LEVEL = 6

_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class BlobClass:
    """One `blob_classes` row: where a class lives and how it is collected."""

    name: str
    location: str
    roots: str
    collector: str
    min_age_seconds: int
    backed_up: bool


_DAY = 86400
#: The rows migration 36 seeds into `blob_classes`, in the same order.
CLASSES: dict[str, BlobClass] = {
    row.name: row
    for row in (
        BlobClass("body", "blobs/sha256", "permanent", "orphans", _DAY, True),
        BlobClass("extract", "extracts", "permanent", "orphans", _DAY, True),
        BlobClass("input_bundle", "inputs", "permanent", "orphans", _DAY, True),
        BlobClass(
            "derivation_output",
            "objects/derivation_output",
            "derivation_generations.output_digest",
            "mark_sweep",
            _DAY,
            True,
        ),
        BlobClass(
            "parse_body",
            "objects/parse_body",
            "source_generation_packs.pack_digest",
            "mark_sweep",
            _DAY,
            True,
        ),
        BlobClass(
            "label_body",
            "objects/label_body",
            "label_body_packs.pack_digest",
            "mark_sweep",
            _DAY,
            True,
        ),
        BlobClass(
            "release_manifest",
            "objects/release_manifest",
            "retained and build manifests, last 3 published",
            "mark_sweep",
            _DAY,
            True,
        ),
        BlobClass(
            "partition_artifact",
            "partitions",
            "release_artifacts named by a kept manifest",
            "mark_sweep",
            _DAY,
            True,
        ),
        BlobClass(
            "dependency_set",
            "sqlite",
            "sets named by a generation, equivalence, build recipe, candidate or building row",
            "mark_sweep",
            _DAY,
            False,
        ),
    )
}
#: Classes this store holds under `objects/`.
STORED = frozenset(name for name, row in CLASSES.items() if row.location.startswith("objects/"))
#: Stored classes kept as raw bytes rather than gzip.
RAW = frozenset({"release_manifest"})


class ObjectUnavailable(RuntimeError):
    """An object is missing, unreadable, or does not hash to its name."""


def stamp(moment: datetime) -> str:
    """`objects.stored_at` text: UTC, microseconds, `Z`; sorts as time."""
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class Store:
    """The objects of one state directory."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / "objects"

    def path(self, cls: str, digest: str) -> Path:
        _check(cls, digest)
        return self.root / cls / digest[:2] / digest

    def exists(self, cls: str, digest: str) -> bool:
        return self.path(cls, digest).is_file()

    def put_bytes(self, cls: str, raw: bytes) -> tuple[str, int]:
        """Store `raw`; returns its digest and the stored file's size."""
        digest, _, stored = self.put_stream(cls, (raw,))
        return digest, stored

    def put_stream(self, cls: str, chunks: Iterable[bytes]) -> tuple[str, int, int]:
        """Store the concatenated chunks; returns digest, raw bytes and stored bytes.

        An intact object already under the digest is kept and the new copy is
        dropped; a corrupt one is replaced.
        """
        _check_class(cls)
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f"{WRITING_PREFIX}{uuid.uuid4().hex}"
        hasher = hashlib.sha256()
        raw_bytes = 0
        try:
            with open(temporary, "xb") as handle:
                with _writer(cls, handle) as sink:
                    for chunk in chunks:
                        if not chunk:
                            continue
                        hasher.update(chunk)
                        raw_bytes += len(chunk)
                        sink.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            digest = hasher.hexdigest()
            final = self.path(cls, digest)
            if final.is_file() and _intact(self, cls, digest):
                temporary.unlink()
                return digest, raw_bytes, final.stat().st_size
            created = _make_dirs(final.parent)
            os.replace(temporary, final)
            # The new name, any directory made for it, and the old name's
            # removal from `objects/` all reach the disk.
            for directory in {final.parent, self.root, *(path.parent for path in created)}:
                _fsync_dir(directory)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
            raise
        return digest, raw_bytes, final.stat().st_size

    def install(self, cls: str, digest: str, stored: Iterable[bytes]) -> int:
        """Put a stored file back under `digest` from its stored bytes (a backup
        copy); returns its size. The copy is checked before it takes the name,
        and an intact file already there is kept. Raises `ObjectUnavailable`
        for a copy that does not hash to `digest`."""
        _check(cls, digest)
        final = self.path(cls, digest)
        if final.is_file() and _intact(self, cls, digest):
            return final.stat().st_size
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.root / f"{WRITING_PREFIX}{uuid.uuid4().hex}"
        try:
            with open(temporary, "xb") as handle:
                for chunk in stored:
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if _verify_path(cls, temporary) != digest:
                raise ObjectUnavailable(f"the copy of {cls}/{digest} does not match its digest")
            created = _make_dirs(final.parent)
            os.replace(temporary, final)
            for directory in {final.parent, self.root, *(path.parent for path in created)}:
                _fsync_dir(directory)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
            raise
        return final.stat().st_size

    @contextlib.contextmanager
    def open(self, cls: str, digest: str) -> Iterator[IO[bytes]]:
        """The object's raw bytes as a stream. It does not check the digest."""
        path = self.path(cls, digest)
        try:
            handle = (
                path.open("rb")
                if cls in RAW
                else cast(IO[bytes], gzip.GzipFile(filename=path, mode="rb"))
            )
        except FileNotFoundError as error:
            raise ObjectUnavailable(f"{cls}/{digest} is missing") from error
        with handle:
            yield handle

    def lines(self, cls: str, digest: str) -> Iterator[bytes]:
        """The raw bytes line by line, each with its newline (the last may lack one).

        The digest is checked when the lines run out; a mismatch, a missing
        file or a damaged stream raises `ObjectUnavailable`, so a reader that
        consumes every line never takes a wrong object as whole.
        """
        hasher = hashlib.sha256()
        try:
            with self.open(cls, digest) as handle:
                for line in handle:
                    hasher.update(line)
                    yield line
        except (OSError, EOFError, zlib.error) as error:
            raise ObjectUnavailable(f"{cls}/{digest} is unreadable: {error}") from error
        if hasher.hexdigest() != digest:
            raise ObjectUnavailable(f"{cls}/{digest} does not match its digest")

    def verify(self, cls: str, digest: str) -> int:
        """Hash the object in bounded chunks; returns its raw size or raises."""
        hasher = hashlib.sha256()
        size = 0
        try:
            with self.open(cls, digest) as handle:
                while chunk := handle.read(CHUNK_BYTES):
                    hasher.update(chunk)
                    size += len(chunk)
        except (OSError, EOFError, zlib.error) as error:
            raise ObjectUnavailable(f"{cls}/{digest} is unreadable: {error}") from error
        if hasher.hexdigest() != digest:
            raise ObjectUnavailable(f"{cls}/{digest} does not match its digest")
        return size


def record(
    conn: sqlite3.Connection,
    cls: str,
    digest: str,
    raw_bytes: int,
    stored_bytes: int,
    now: datetime,
) -> None:
    """Index a stored object. Write the file first.

    A present row is kept with its `stored_at` moved up to `now`: a writer
    that found the file there and reused it has just written it, so the
    collector's one-day age protects it again. The file is checked after the
    row is written, inside the writer's transaction: a collector that removed
    the row and the file just before (it renames the file away inside its
    own transaction) makes this raise `ObjectUnavailable`, and the writer's
    transaction rolls back rather than name a file that is gone.
    """
    _check(cls, digest)
    conn.execute(
        "INSERT INTO objects(class,digest,raw_bytes,stored_bytes,stored_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(class,digest) DO UPDATE SET "
        "stored_at=max(stored_at,excluded.stored_at)",
        (cls, digest, raw_bytes, stored_bytes, stamp(now)),
    )
    store = store_for(conn) if _has_file(conn) else None
    if store is not None and not store.exists(cls, digest):
        raise ObjectUnavailable(f"{cls}/{digest} was removed before its row was written")


def _has_file(conn: sqlite3.Connection) -> bool:
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            return bool(row[2])
    return False


def store_for(conn: sqlite3.Connection) -> Store:
    """The store beside the connection's main database file.

    A checkpoint or a restore scratch directory gets its own objects, so a
    reader needs no state directory parameter.
    """
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main":
            if not row[2]:
                raise ValueError("an object store needs a database file")
            return Store(Path(row[2]).parent)
    raise ValueError("the connection has no main database")


def _check_class(cls: str) -> None:
    if cls not in STORED:
        raise ValueError(f"not an object class: {cls}")


def _check(cls: str, digest: str) -> None:
    _check_class(cls)
    if not _DIGEST.fullmatch(digest):
        raise ValueError(f"not a sha256 digest: {digest!r}")


@contextlib.contextmanager
def _writer(cls: str, handle: IO[bytes]) -> Iterator[IO[bytes]]:
    if cls in RAW:
        yield handle
        return
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=handle, compresslevel=COMPRESS_LEVEL, mtime=0
    ) as sink:
        yield cast(IO[bytes], sink)


def _verify_path(cls: str, path: Path) -> str | None:
    """The sha256 of a stored file's raw bytes, or None when it is unreadable."""
    hasher = hashlib.sha256()
    try:
        handle = (
            path.open("rb")
            if cls in RAW
            else cast(IO[bytes], gzip.GzipFile(filename=path, mode="rb"))
        )
        with handle:
            while chunk := handle.read(CHUNK_BYTES):
                hasher.update(chunk)
    except (OSError, EOFError, zlib.error):
        return None
    return hasher.hexdigest()


def _intact(store: Store, cls: str, digest: str) -> bool:
    try:
        store.verify(cls, digest)
    except ObjectUnavailable:
        return False
    return True


def _make_dirs(directory: Path) -> list[Path]:
    """Create `directory` and its missing parents; returns those it created."""
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir(exist_ok=True)
    return missing


def _fsync_dir(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
