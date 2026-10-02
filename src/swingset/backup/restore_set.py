"""Install a segmented backup set offline (architecture phase 4, WP6; D-0232).

`install` fetches `backup.json`, every database part, the index and every
segment the set names, and checks each digest before it decompresses
anything. It joins the parts into `state.sqlite` (raw sha256 checked),
checks the schema and, unless told not to, `integrity_check` and
`foreign_key_check`, then extracts from each segment exactly the files the
index assigns to it, verifying every one (gunzip for bodies and packs). All
of this happens in a scratch directory inside the target; only when every
check passed are the files moved into place and the baseline link made. A
failure removes the scratch and leaves the target as it was.

`install` needs no network beyond its transport and never activates
anything: the drill calls it alone. `restore_from_set` is `swingset restore
--backup-set`: the exclusive locks, `RESTORE_PENDING`, `install`, the public
hub check and activation, as a checkpoint restore does.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import uuid
from pathlib import Path
from typing import Any

from swingset.build.files import fsync_dir
from swingset.clock import Clock
from swingset.publish.service import Hub

from .segments import FORMAT, INDEX, Digest, read_index
from .transport import MANIFEST, SETS, Transport, TransportError, segment_path

_CHUNK = 1 << 20
#: Top-level names an index may not write: the database, the baseline link,
#: the restore marker, and directories a set never carries.
_RESERVED = frozenset(
    {
        "state.sqlite",
        "state.sqlite-wal",
        "state.sqlite-shm",
        "baseline",
        "RESTORE_PENDING",
        "checkpoints",
        "gc",
        "tmp",
    }
)


class SetError(RuntimeError):
    """A set failed to install; nothing was placed."""


def latest(source: Transport) -> str:
    found = source.sets()
    if not found:
        raise SetError("the transport holds no backup set")
    return found[-1]


def install(
    source: Transport,
    target: Path,
    *,
    set_id: str | None = None,
    maximum_schema_version: int | None = None,
    integrity: str | None = "full",
) -> dict[str, Any]:
    """Verify one set completely, then place it into `target`.

    `target` may hold lock files and `RESTORE_PENDING`, nothing else.
    `integrity` is "full", "quick" or None (no SQLite check).
    """
    from swingset.state.rwlock import is_lock_file

    if integrity not in {"full", "quick", None}:
        raise ValueError(f"unknown integrity check: {integrity}")
    if maximum_schema_version is None:
        from swingset.state.db import SCHEMA_VERSION

        maximum_schema_version = SCHEMA_VERSION
    target.mkdir(parents=True, exist_ok=True)
    for path in target.iterdir():
        if path.name.startswith(".install-") and path.is_dir():
            shutil.rmtree(path)  # a crashed install's scratch
        elif not is_lock_file(path.name) and path.name != "RESTORE_PENDING":
            raise SetError(f"restore target is not empty: {path.name}")
    work = target / f".install-{uuid.uuid4().hex}"
    stage, fetched = work / "state", work / "fetch"
    stage.mkdir(parents=True)
    fetched.mkdir()
    try:
        try:
            chosen = set_id or latest(source)
            summary = _install(source, chosen, stage, fetched, maximum_schema_version, integrity)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            tarfile.TarError,
            TransportError,
        ) as error:
            raise SetError(f"backup set failed verification: {error}") from error
        for path in sorted(stage.iterdir()):
            os.replace(path, target / path.name)
        fsync_dir(target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return summary


def _install(
    source: Transport,
    set_id: str,
    stage: Path,
    fetched: Path,
    maximum_schema_version: int,
    integrity: str | None,
) -> dict[str, Any]:
    from .huggingface import safe_relative

    prefix = f"{SETS}/{set_id}"
    body = source.fetch(f"{prefix}/{MANIFEST}", fetched).read_bytes()
    manifest = json.loads(body)
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise SetError(f"{set_id} is not a backup set")
    if manifest.get("set_id") != set_id:
        raise SetError(f"backup.json names {manifest.get('set_id')!r}, not {set_id!r}")
    schema = manifest.get("schema_version")
    if type(schema) is not int or not 1 <= schema <= maximum_schema_version:
        raise SetError(f"set schema {schema!r} is not supported by this program")
    database = manifest["database"]
    parts = []
    for part in database["parts"]:
        path = source.fetch(f"{prefix}/{safe_relative(part['path']).as_posix()}", fetched)
        _check_file(path, part["sha256"], part["bytes"], part["path"])
        parts.append(path)
    index = manifest["index"]
    if index["path"] != INDEX:
        raise SetError("unexpected index path")
    index_path = source.fetch(f"{prefix}/{INDEX}", fetched)
    _check_file(index_path, index["sha256"], index["bytes"], INDEX)
    segments: dict[str, Path] = {}
    for segment in manifest["segments"]:
        path = source.fetch(segment_path(str(segment)), fetched)
        _check_file(path, str(segment), None, f"segment {segment}")
        segments[str(segment)] = path
    # Every fetched digest matched: decompress and extract.
    _join(parts, stage / "state.sqlite", database["raw_sha256"], database["raw_bytes"])
    _check_database(stage / "state.sqlite", schema, integrity)
    wanted: dict[str, dict[str, tuple[str, str, int]]] = {}
    count = total = 0
    classes: dict[str, int] = {}
    for entry in read_index(index_path):
        relative = safe_relative(entry.path)
        if relative.parts[0] in _RESERVED or relative.as_posix() != entry.path:
            raise SetError(f"index names a reserved or unsafe path: {entry.path!r}")
        if entry.segment not in segments:
            raise SetError(f"index names a segment the set does not: {entry.segment}")
        bucket = wanted.setdefault(entry.segment, {})
        if entry.path in bucket:
            raise SetError(f"index names {entry.path} twice")
        bucket[entry.path] = (entry.cls, entry.sha256, entry.bytes)
        count += 1
        total += entry.bytes
        classes[entry.cls] = classes.get(entry.cls, 0) + 1
    if count != index["items"]:
        raise SetError(f"index has {count} items, backup.json says {index['items']}")
    for segment, expected in sorted(wanted.items()):
        _extract(segments[segment], expected, stage)
    for field in ("baseline_candidate", "pending_candidate"):
        name = manifest.get(field)
        if name is not None and not (stage / "candidates" / str(name) / "BUILT").is_file():
            raise SetError(f"{field} {name} is missing or incomplete")
    baseline = manifest.get("baseline_candidate")
    if integrity == "full":
        _check_closure(stage, manifest)
    else:
        # Cheap (one stat per object), so the drill's quick install runs it too.
        conn = sqlite3.connect(
            f"{(stage / 'state.sqlite').resolve().as_uri()}?mode=ro&immutable=1", uri=True
        )
        try:
            _check_objects(stage, conn, manifest)
        finally:
            conn.close()
    missing = manifest.get("missing") or []
    if missing:
        # A file the set left out (gone, or not matching its digest) that the
        # checks above do not reach, such as a body: the set is not whole.
        raise SetError(
            f"the set was written without {len(missing)} named files, first {missing[:3]}"
        )
    if baseline is not None:
        os.symlink(Path("candidates") / str(baseline), stage / "baseline")
    return {
        "set_id": set_id,
        "manifest_sha256": hashlib.sha256(body).hexdigest(),
        "schema_version": schema,
        "db_raw_bytes": database["raw_bytes"],
        "parts": len(parts),
        "segments": len(segments),
        "items": count,
        "item_bytes": total,
        "classes": dict(sorted(classes.items())),
        "baseline_candidate": baseline,
        "pending_candidate": manifest.get("pending_candidate"),
        "integrity": integrity,
    }


def _check_file(path: Path, sha256: str, size: int | None, label: str) -> None:
    hasher = hashlib.sha256()
    length = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)
            length += len(chunk)
    if (size is not None and length != size) or hasher.hexdigest() != sha256:
        raise SetError(f"{label} does not match its digest")


def _join(parts: list[Path], target: Path, sha256: str, size: int) -> None:
    import pyarrow as pa

    class Joined:
        def __init__(self) -> None:
            self.files = iter(parts)
            self.handle = next(self.files).open("rb")
            self.done = False

        def read(self, n: int = -1) -> bytes:
            while not self.done:
                data = self.handle.read(n)
                if data or n == 0:
                    return data
                following = next(self.files, None)
                if following is None:
                    self.done = True
                    break
                self.handle.close()
                self.handle = following.open("rb")
            return b""

        def close(self) -> None:
            self.handle.close()

        @property
        def closed(self) -> bool:
            return bool(self.handle.closed)

        def readable(self) -> bool:
            return True

    hasher = hashlib.sha256()
    length = 0
    joined = Joined()
    try:
        with (
            pa.CompressedInputStream(pa.PythonFile(joined, mode="r"), "zstd") as stream,
            target.open("xb") as output,
        ):
            while chunk := stream.read(_CHUNK):
                hasher.update(chunk)
                length += len(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except pa.ArrowException as error:
        raise SetError(f"database parts do not decompress: {error}") from error
    finally:
        with contextlib.suppress(OSError):
            joined.close()
    if length != size or hasher.hexdigest() != sha256:
        raise SetError("the joined database does not match its digest")


def _check_database(path: Path, schema: int, integrity: str | None) -> None:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        actual = int(conn.execute("PRAGMA user_version").fetchone()[0])
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if actual != schema or row is None or int(row[0]) != schema:
            raise SetError("set schema differs from its database")
        if integrity is None:
            return
        check = conn.execute(
            "PRAGMA quick_check" if integrity == "quick" else "PRAGMA integrity_check"
        ).fetchone()
        if check is None or check[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchone():
            raise SetError("set database failed integrity checks")
    except sqlite3.DatabaseError as error:
        raise SetError(f"set database is unreadable: {error}") from error
    finally:
        conn.close()


def _check_closure(stage: Path, manifest: dict[str, Any]) -> None:
    """Every file the restored database and candidates declare is present."""
    from swingset.state.retention import RetentionError, artifact_closure

    candidates = {
        stage / "candidates" / str(name)
        for name in (manifest.get("baseline_candidate"), manifest.get("pending_candidate"))
        if name is not None
    }
    conn = sqlite3.connect(
        f"{(stage / 'state.sqlite').resolve().as_uri()}?mode=ro&immutable=1", uri=True
    )
    try:
        artifact_closure(stage, conn, candidates)
        _check_objects(stage, conn, manifest)
    except RetentionError as error:
        raise SetError(f"restored closure is incomplete: {error}") from error
    finally:
        conn.close()


def _check_objects(stage: Path, conn: sqlite3.Connection, manifest: dict[str, Any]) -> None:
    """Every `objects` row has its file, and every pack a row names has a row.

    An evicted generation's only copy is its pack, so a set that lacks one
    must not restore as whole (the set's own `missing` names it).
    """
    from .segments import unindexed_packs

    tables = {
        str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "objects" not in tables:
        return
    lacking = [
        f"{cls}/{digest}"
        for cls, digest in conn.execute("SELECT class,digest FROM objects ORDER BY class,digest")
        if not (stage / "objects" / str(cls) / str(digest)[:2] / str(digest)).is_file()
    ]
    lacking.extend(
        f"{cls}/{digest} ({reason})" for cls, digest, reason in unindexed_packs(conn, tables)
    )
    if lacking:
        named = manifest.get("missing") or []
        raise SetError(
            f"restored objects are incomplete: {len(lacking)} missing, first {lacking[:3]}"
            + (f"; the set recorded {len(named)} missing when it was written" if named else "")
        )


def _extract(segment: Path, expected: dict[str, tuple[str, str, int]], stage: Path) -> None:
    remaining = dict(expected)
    with tarfile.open(segment, "r:") as archive:
        for member in archive:
            if member.name not in remaining:
                continue
            cls, sha256, size = remaining.pop(member.name)
            if not member.isfile() or member.size != size:
                raise SetError(f"segment member {member.name} is not the indexed file")
            reader = archive.extractfile(member)
            if reader is None:
                raise SetError(f"segment member {member.name} is unreadable")
            destination = stage / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = Digest(cls)
            with reader, destination.open("xb") as output:
                while chunk := reader.read(_CHUNK):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.stored != size or digest.hexdigest() != sha256:
                raise SetError(f"{member.name} does not match its digest")
    if remaining:
        raise SetError(f"segment {segment.stem} lacks {len(remaining)} indexed files")


def restore_from_set(
    source: Transport,
    set_id: str | None,
    state_dir: Path,
    hub: Hub,
    clock: Clock,
    *,
    lock_timeout: float,
    maximum_schema_version: int | None = None,
) -> dict[str, Any]:
    """Install a set under the writer lock, check the public hub, activate."""
    from swingset.build.files import durable_write
    from swingset.state.control_lock import control_lock
    from swingset.state.rwlock import LockBusy, exclusive, is_lock_file

    from .checkpoint import CheckpointError, activate_restored_state, verify_restored_public

    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        locks = exclusive(state_dir, timeout=lock_timeout, clock=clock)
    except LockBusy as error:
        raise CheckpointError(
            f"state writer lock timed out after {lock_timeout:g} seconds"
        ) from error
    try:
        with control_lock(state_dir, timeout=lock_timeout):
            marker = state_dir / "RESTORE_PENDING"
            existing = [path for path in state_dir.iterdir() if not is_lock_file(path.name)]
            if existing and not marker.is_file():
                raise CheckpointError("restore target contains an active or unmarked state")
            if marker.is_file():
                for path in existing:
                    if path == marker:
                        continue
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            else:
                durable_write(marker, b"verification pending\n")
            summary = install(
                source,
                state_dir,
                set_id=set_id,
                maximum_schema_version=maximum_schema_version,
            )
            verify_restored_public(state_dir, hub)
            activate_restored_state(state_dir)
    finally:
        locks.release()
    return summary
