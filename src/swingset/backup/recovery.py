"""Restore a lost file from local backup segments (phase 4, WP6; F40).

`LocalCheckpointRecovery` reads `checkpoints/run_*`, the full checkpoints.
Segmented sets have none, so `SegmentRecovery` finds the file through a local
set's index (the newest first), reads its member from the segment tar,
checks the digest (gunzip for a body or a pack) and writes it in place.
`recover` restores a body or an extract; `recover_object` restores an
`objects/` file (a pack, a parse body, a recipe or a manifest), which for an
evicted generation or a stubbed body is the only copy outside the sets.

`local_recovery` is what the loops, the cycle and the CLI build: the old
checkpoints first, then the segments. It lives in `backup/`, outside every
code recipe scope, and parse scopes never construct it.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import sqlite3
import tarfile
import tempfile
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from swingset.fetch.archive import (
    Archive,
    ArtifactKind,
    ArtifactRecovery,
    ArtifactUnavailable,
    digest,
    durable_write,
)
from swingset.fetch.recovery import LocalCheckpointRecovery

from .segments import INDEX, local_sets, read_index, read_manifest
from .transport import SEGMENTS, SETS, segment_path

_CHUNK = 1 << 20


class SegmentRecovery:
    """Share one instance across work units; healthy reads never call it.

    Each set's index is read once per instance into an on-disk lookup (a
    SQLite file in a temporary directory), and each segment's tar headers
    once into the same lookup with every member's data offset, so
    recovering many files costs one index read per set and one header scan
    per segment, then one seek per file.
    """

    def __init__(self, checkpoints: Path) -> None:
        self.checkpoints = checkpoints.resolve()
        self.restored: list[dict[str, str]] = []
        self._scratch: tempfile.TemporaryDirectory[str] | None = None
        self._lookup: sqlite3.Connection | None = None
        self._loaded: set[str] = set()
        self._scanned: set[str] = set()

    def _db(self) -> sqlite3.Connection:
        if self._lookup is None:
            self._scratch = tempfile.TemporaryDirectory(prefix="swingset-recovery-")
            self._lookup = sqlite3.connect(Path(self._scratch.name) / "index.sqlite")
            self._lookup.execute(
                "CREATE TABLE entries(set_id TEXT, path TEXT, sha256 TEXT, segment TEXT, "
                "bytes INTEGER, PRIMARY KEY(set_id,path)) WITHOUT ROWID"
            )
            self._lookup.execute(
                "CREATE TABLE members(segment TEXT, name TEXT, offset INTEGER, size INTEGER, "
                "PRIMARY KEY(segment,name)) WITHOUT ROWID"
            )
        return self._lookup

    def _entry(self, set_id: str, relative: str) -> tuple[str, int, str] | None:
        """(segment, bytes, sha256) of `relative` in a set's index, or None."""
        lookup = self._db()
        if set_id not in self._loaded:
            index = self.checkpoints / SETS / set_id / INDEX
            batch: list[tuple[str, str, str, str, int]] = []
            for item in read_index(index):
                batch.append((set_id, item.path, item.sha256, item.segment, item.bytes))
                if len(batch) >= 10_000:
                    lookup.executemany("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?)", batch)
                    batch.clear()
            lookup.executemany("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?)", batch)
            lookup.commit()
            self._loaded.add(set_id)
        row = lookup.execute(
            "SELECT segment,bytes,sha256 FROM entries WHERE set_id=? AND path=?",
            (set_id, relative),
        ).fetchone()
        return None if row is None else (str(row[0]), int(row[1]), str(row[2]))

    def find(self, relative: str, sha256: str) -> tuple[str, str, int] | None:
        """(set_id, segment, bytes) of the newest local set whose index holds
        `relative` with this digest, or None."""
        for set_id in reversed(local_sets(self.checkpoints)):
            with contextlib.suppress(OSError, ValueError, sqlite3.Error):
                read_manifest(self.checkpoints / SETS / set_id)
                entry = self._entry(set_id, relative)
                if entry is not None and entry[2] == sha256:
                    return set_id, entry[0], entry[1]
        return None

    def _member(self, segment: str, name: str) -> tuple[Path, int, int]:
        """(segment file, data offset, size) of one member, from one header scan."""
        path = self.checkpoints / segment_path(segment)
        if not path.is_file():
            raise FileNotFoundError(f"{SEGMENTS}/{path.name} is missing")
        lookup = self._db()
        if segment not in self._scanned:
            batch: list[tuple[str, str, int, int]] = []
            with tarfile.open(path, "r:") as archive:
                for member in archive:
                    if member.isfile():
                        batch.append((segment, member.name, member.offset_data, member.size))
                    if len(batch) >= 10_000:
                        lookup.executemany("INSERT OR REPLACE INTO members VALUES (?,?,?,?)", batch)
                        batch.clear()
            lookup.executemany("INSERT OR REPLACE INTO members VALUES (?,?,?,?)", batch)
            lookup.commit()
            self._scanned.add(segment)
        row = lookup.execute(
            "SELECT offset,size FROM members WHERE segment=? AND name=?", (segment, name)
        ).fetchone()
        if row is None:
            raise ValueError(f"{name} is not in {path.name}")
        return path, int(row[0]), int(row[1])

    def close(self) -> None:
        if self._lookup is not None:
            self._lookup.close()
            self._lookup = None
        if self._scratch is not None:
            self._scratch.cleanup()
            self._scratch = None
        self._loaded.clear()
        self._scanned.clear()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

    def recover(self, kind: ArtifactKind, sha256: str, destination: Path) -> None:
        Archive._validate_hash(sha256)
        if destination.resolve().is_relative_to(self.checkpoints):
            raise ArtifactUnavailable(kind, sha256, "recovery_target_is_immutable_checkpoint")
        relative = (
            f"blobs/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"
            if kind == "body"
            else f"extracts/{sha256}"
        )
        failures: list[dict[str, str]] = []
        for set_id in reversed(local_sets(self.checkpoints)):
            directory = self.checkpoints / SETS / set_id
            try:
                read_manifest(directory)
                entry = self._entry(set_id, relative)
                if entry is None:
                    continue
                segment, size, _ = entry
                body = b"".join(self._read(segment, relative, size))
                decoded = gzip.decompress(body) if kind == "body" else body
                if digest(decoded) != sha256 or len(body) != size:
                    raise ValueError("segment member does not match the requested digest")
                if kind == "extract":
                    json.loads(decoded)
                durable_write(destination, body)
                self.restored.append(
                    {"kind": kind, "sha256": sha256, "set": set_id, "segment": segment}
                )
                return
            except (
                OSError,
                ValueError,
                EOFError,
                zlib.error,
                tarfile.TarError,
                sqlite3.Error,
            ) as exc:
                failures.append({"set": set_id, "reason": str(exc)})
        raise ArtifactUnavailable(kind, sha256, "no_valid_matching_local_segment", tuple(failures))

    def _read(self, segment: str, name: str, size: int) -> Iterator[bytes]:
        path, offset, length = self._member(segment, name)
        if length != size:
            raise ValueError(f"{name} in {path.name} is {length} bytes, its index says {size}")
        with path.open("rb") as handle:
            handle.seek(offset)
            left = length
            while left:
                chunk = handle.read(min(_CHUNK, left))
                if not chunk:
                    raise ValueError(f"{path.name} ends inside {name}")
                left -= len(chunk)
                yield chunk

    def recover_object(self, store: Any, cls: str, sha256: str) -> dict[str, str]:
        """Put `objects/<cls>/<aa>/<sha256>` back from the newest local set that
        holds it, checked against its name; returns where it came from.

        `store` is the state's `objects.Store`. An intact file already there
        is kept. Raises `ObjectUnavailable` when no local set holds a good copy.
        """
        from swingset.state.objects import ObjectUnavailable

        relative = f"objects/{cls}/{sha256[:2]}/{sha256}"
        failures: list[str] = []
        for set_id in reversed(local_sets(self.checkpoints)):
            try:
                read_manifest(self.checkpoints / SETS / set_id)
                entry = self._entry(set_id, relative)
                if entry is None or entry[2] != sha256:
                    continue
                segment, size, _ = entry
                # `install` checks the copy against its name before it is placed.
                store.install(cls, sha256, self._read(segment, relative, size))
                found = {"class": cls, "sha256": sha256, "set": set_id, "segment": segment}
                self.restored.append(found)
                return found
            except (
                OSError,
                ValueError,
                EOFError,
                zlib.error,
                tarfile.TarError,
                sqlite3.Error,
                ObjectUnavailable,
            ) as exc:
                failures.append(f"{set_id}: {exc}")
        raise ObjectUnavailable(
            f"{cls}/{sha256} is in no local backup set"
            + (f" with a good copy ({'; '.join(failures[:3])})" if failures else "")
        )


class RecoveryChain:
    """Try each recovery in order; the first that restores the file wins."""

    def __init__(self, *recoveries: ArtifactRecovery) -> None:
        self.recoveries = recoveries

    @property
    def restored(self) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        for recovery in self.recoveries:
            found.extend(getattr(recovery, "restored", ()))
        return found

    def recover(self, kind: ArtifactKind, sha256: str, destination: Path) -> None:
        attempts: list[dict[str, Any]] = []
        for recovery in self.recoveries:
            try:
                recovery.recover(kind, sha256, destination)
                return
            except ArtifactUnavailable as exc:
                attempts.append({"recovery": type(recovery).__name__, "reason": exc.reason})
                attempts.extend(exc.attempts)
        raise ArtifactUnavailable(kind, sha256, "no_valid_local_copy", tuple(attempts))


def local_recovery(state_dir: Path, *, maximum_schema_version: int) -> RecoveryChain:
    """Old full checkpoints first, then the segmented sets' segments."""
    checkpoints = state_dir / "checkpoints"
    return RecoveryChain(
        LocalCheckpointRecovery(checkpoints, maximum_schema_version=maximum_schema_version),
        SegmentRecovery(checkpoints),
    )


__all__ = ["RecoveryChain", "SegmentRecovery", "local_recovery"]
