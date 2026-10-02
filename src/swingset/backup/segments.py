"""Segmented incremental backup sets (architecture phase 4, WP6; D-0232).

A set is the database plus every file it needs, written so that a daily set
sends only what is new:

1. **Snapshot.** Under the caller's shared state lock, `VACUUM INTO` copies
   the database in one read transaction while the loops keep running. Then,
   under the control lock (D-0164's order), the file closure comes from that
   copy: `retention.artifact_closure` (bodies, extracts, input bundles),
   every `objects` row, the release manifests and partition artifacts a
   retained manifest or a current partition names, the baseline and pending
   candidates, `holds/`, and the other loose state files a checkpoint copies.
   Files that change in place (candidates, holds, the rest) are copied into
   the set's scratch under the same lock, so the set holds the hold that
   produced the closure.
2. **Database.** The copy is streamed through zstd into `db/part-NNNN.zst`,
   a new part every `PART_BYTES`, with the sha256 of the raw file and of
   each part.
3. **Segments.** A file whose `path@sha256` key is not in `backup_objects`
   for this transport goes into a new segment: a PAX tar (mtime 0, owner 0,
   mode 0644, state-relative names) of at most `SEGMENT_BYTES`, named by its
   own sha256, under `checkpoints/segments/`. Each new file is verified as it
   is read (gunzip for bodies and packs).
4. **Index and commit.** `index.jsonl.zst` has one line per file,
   `[class, path, sha256, bytes, segment]`, sorted by path. `backup.json`
   (canonical) names the parts, the index and every segment the index needs,
   and is written last; the scratch directory is then renamed into place.
5. **Publish and record.** The transport sends the new segments and the set,
   `backup.json` last. Then `backup_objects` and `backup_sets` record it.

`prune_sets` keeps the newest local set and the newest drilled one; segments
no kept set names go, and their `backup_objects` rows for the local
transport with them.

`sha256` in the index is the digest of the decompressed bytes for the gzip
classes and of the stored bytes for every other class; for bodies, extracts
and objects it is the file's name.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import uuid
import zlib
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

from swingset.build.files import canonical_json, durable_write, fsync_dir
from swingset.state.db import Database

from .transport import (
    MANIFEST,
    MAX_FILE_BYTES,
    SEGMENT_NAME,
    SEGMENTS,
    SET_ID,
    SETS,
    Transport,
    TransportError,
)

FORMAT = "swingset-backup-set-v1"
#: Database part size (4 GiB); tests pass less.
PART_BYTES = MAX_FILE_BYTES
#: Segment size bound (4 GiB); tests pass less.
SEGMENT_BYTES = MAX_FILE_BYTES
#: The transport name of `checkpoints/` itself (local mode).
LOCAL_TRANSPORT = "local"
INDEX = "index.jsonl.zst"
#: Classes stored gzip; their sha256 is that of the decompressed bytes.
GZIP = frozenset({"body", "derivation_output", "parse_body", "label_body"})
CLASSES = (
    "body",
    "extract",
    "input_bundle",
    "derivation_output",
    "parse_body",
    "label_body",
    "release_manifest",
    "partition_artifact",
    "candidate",
    "hold",
    "other",
)
#: Top-level names that are a class's own directory, or never backed up.
_CLASS_DIRS = frozenset(
    {"blobs", "extracts", "inputs", "objects", "partitions", "candidates", "holds"}
)
_SKIPPED = frozenset({".cache", "venv", "uv-cache", "checkpoints", "gc", "tmp", "baseline"})
_SKIPPED_NAMES = frozenset(
    {
        "state.sqlite",
        "state.sqlite-wal",
        "state.sqlite-shm",
        "state.sqlite-journal",
        "state.lock",
        "control.lock",
        "RESTORE_PENDING",
    }
)
#: An abandoned scratch set or segment older than this goes (`clean_abandoned`).
ABANDONED = timedelta(days=1)
#: Room a set leaves on the disk beyond the database's bytes in use: the
#: zstd parts and the day's new segments, and the loops' own headroom.
HEADROOM_BYTES = 20_000_000_000
#: The ALERTS.log key of a set written with files it could not find.
INCOMPLETE_KEY = "backup-incomplete"
#: What `[backup-incomplete]` tells the operator; the watchdog says the same.
REPAIR = (
    "a restore or drill of it refuses. No local set held a good copy; what puts"
    " each class back is in docs/guides/backup-and-restore.md (lost files)."
    " `gc --collect` drops only rows nothing names"
)
_CHUNK = 1 << 20
#: tar end-of-archive blocks plus tarfile's record padding.
_TAR_RESERVE = 1024 + tarfile.RECORDSIZE
_DIGEST = re.compile(r"[0-9a-f]{64}")


class BackupError(RuntimeError):
    """A set could not be written; nothing was recorded."""


@dataclass(frozen=True, slots=True)
class Item:
    """One file of a set."""

    cls: str
    path: str
    sha256: str
    bytes: int
    #: Where to read it when not `state_dir/path`: a copy in the set's scratch.
    source: str | None = None
    #: An objects row whose file is gone, held by `segment` of an earlier set.
    absent: bool = False
    segment: str | None = None

    def file(self, state_dir: Path) -> Path:
        return Path(self.source) if self.source is not None else state_dir / self.path

    @property
    def key(self) -> str:
        """`backup_objects.key`: the path and its content, so a file that
        changes in place is sent again."""
        return f"{self.path}@{self.sha256}"


# ---------------------------------------------------------------------------
# Digests shared with restore and recovery
# ---------------------------------------------------------------------------


class Digest:
    """The sha256 an index line records: of decompressed bytes for a gzip
    class, of the bytes as stored otherwise. Feed stored bytes to `update`."""

    def __init__(self, cls: str) -> None:
        self.hasher = hashlib.sha256()
        self.stored = 0
        self._inflate: Any = zlib.decompressobj(31) if cls in GZIP else None
        self._open = False

    def update(self, data: bytes) -> None:
        self.stored += len(data)
        if self._inflate is None:
            self.hasher.update(data)
            return
        while data:
            self._open = True
            self.hasher.update(self._inflate.decompress(data, _CHUNK))
            data = self._inflate.unconsumed_tail
            if self._inflate.eof:
                # A second gzip member follows the first.
                data = self._inflate.unused_data + data
                self._inflate = zlib.decompressobj(31)
                self._open = False

    def hexdigest(self) -> str:
        """Raises `ValueError` for a gzip stream that ends early."""
        if self._inflate is not None and self._open:
            self.hasher.update(self._inflate.flush())
            if not self._inflate.eof:
                raise ValueError("gzip stream is truncated")
            self._open = False
        return self.hasher.hexdigest()


def digest_file(cls: str, path: Path) -> tuple[str, int]:
    digest = Digest(cls)
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest(), digest.stored


@dataclass(frozen=True, slots=True)
class Entry:
    """One index line."""

    cls: str
    path: str
    sha256: str
    bytes: int
    segment: str


def read_index(path: Path) -> Iterator[Entry]:
    """Stream a set's index; a malformed line raises `ValueError`."""
    import pyarrow as pa

    def entries(lines: Iterable[bytes]) -> Iterator[Entry]:
        for line in lines:
            value = json.loads(line)
            if (
                not isinstance(value, list)
                or len(value) != 5
                or value[0] not in CLASSES
                or not isinstance(value[1], str)
                or not isinstance(value[2], str)
                or not _DIGEST.fullmatch(value[2])
                or type(value[3]) is not int
                or value[3] < 0
                or not isinstance(value[4], str)
                or not _DIGEST.fullmatch(value[4])
            ):
                raise ValueError(f"malformed index line: {line[:200]!r}")
            yield Entry(value[0], value[1], value[2], value[3], value[4])

    try:
        with pa.input_stream(str(path), compression="zstd") as stream:
            rest = b""
            while chunk := stream.read(_CHUNK):
                lines = (rest + chunk).split(b"\n")
                rest = lines.pop()
                yield from entries(line for line in lines if line)
            if rest:
                yield from entries((rest,))
    except (OSError, pa.ArrowException) as error:
        raise ValueError(f"index is unreadable: {error}") from error


# ---------------------------------------------------------------------------
# The set writer
# ---------------------------------------------------------------------------


def create_set(
    database: Database,
    transport: Transport,
    *,
    now: datetime,
    versions: dict[str, str],
    part_bytes: int = PART_BYTES,
    segment_bytes: int = SEGMENT_BYTES,
    control_timeout: float = 60.0,
    prune: bool = True,
    headroom: int = HEADROOM_BYTES,
) -> dict[str, Any]:
    """Write, publish and record one set; returns its summary.

    The caller holds the state lock, shared (a loop's) or exclusive, and no
    SQLite transaction. It refuses before `VACUUM INTO` unless the disk,
    quota included, has the database's bytes in use plus `headroom` free.
    A failure before publishing leaves no set; a failed publish removes the
    unrecorded local set, and the next run sends the same segments again
    (equal content, equal names). An `objects` file that is gone is put back
    from the newest local set that holds it (`restored`); failing that, an
    earlier segment the transport still holds stands for it (`absent`). An
    object, release item or new file that is still missing, or that does not
    match its digest, does not stop the set: it is listed under `missing` in
    `backup.json` and `[backup-incomplete]` goes to ALERTS.log; a restore or
    drill of that set then refuses. A missing body, extract or input bundle
    still fails the set (`retention.artifact_closure`).
    """
    if not 0 < part_bytes <= MAX_FILE_BYTES or not 0 < segment_bytes <= MAX_FILE_BYTES:
        raise ValueError("parts and segments are at most 4 GiB")
    conn = database.connection
    if conn.in_transaction:
        raise BackupError("a backup set cannot be taken inside a transaction")
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='backup_sets'"
    ).fetchone():
        raise BackupError("segmented backups need schema 36")
    checkpoints = database.state_dir / "checkpoints"
    sets_dir, segments_dir = checkpoints / SETS, checkpoints / SEGMENTS
    sets_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)
    # A killed run's scratch first: it may hold a whole database copy.
    cleaned = clean_abandoned(checkpoints, now=now)
    room = _room(database, checkpoints, headroom)
    # The one closure file that fails the set and is cheap to see first: no
    # minutes-long `VACUUM INTO` of the database only to refuse after it.
    bundle = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
    if bundle and bundle[0] and not (database.state_dir / "inputs" / str(bundle[0])).is_dir():
        raise BackupError(f"referenced input bundle is missing: {bundle[0]}")
    set_id = _new_set_id(sets_dir, now)
    staging = sets_dir / f".tmp-{set_id}"
    staging.mkdir()
    try:
        snapshot = _snapshot(database, staging, control_timeout)
        db_record = _write_database(staging / "db.sqlite", staging, part_bytes)
        (staging / "db.sqlite").unlink()
        present = transport.segments()
        _forget_missing(database, transport.name, present)
        placed = _segment(
            database, transport.name, snapshot.items, present, segments_dir, segment_bytes
        )
        if placed.dropped:
            # Unreadable, or gone with no earlier segment to name: recorded
            # under `missing`, and the set holds every other file.
            snapshot.items = [item for item in snapshot.items if item.path in placed.assigned]
            snapshot.missing = sorted(snapshot.missing + placed.dropped)
        index = _write_index(staging / INDEX, snapshot.items, placed.assigned)
        shutil.rmtree(staging / "files", ignore_errors=True)
        classes: dict[str, dict[str, int]] = {}
        for item in snapshot.items:
            entry = classes.setdefault(item.cls, {"items": 0, "bytes": 0})
            entry["items"] += 1
            entry["bytes"] += item.bytes
        manifest: dict[str, Any] = {
            "format": FORMAT,
            "set_id": set_id,
            "created_at": now.astimezone(UTC).isoformat(),
            "schema_version": snapshot.schema_version,
            "versions": versions,
            "input_bundle_hash": snapshot.input_bundle_hash,
            "database": db_record,
            "index": index,
            "segments": sorted(set(placed.assigned.values())),
            "new_segments": placed.new_segments,
            "new_items": len(placed.new_items),
            "classes": dict(sorted(classes.items())),
            "baseline_candidate": snapshot.baseline,
            "pending_candidate": snapshot.pending,
            "transport": transport.name,
            "missing": snapshot.missing,
        }
        body = canonical_json(manifest)
        durable_write(staging / MANIFEST, body)
        fsync_dir(staging)
        os.replace(staging, sets_dir / set_id)
        fsync_dir(sets_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        commit = transport.publish(checkpoints, set_id, manifest["segments"])
    except BaseException:
        # Unrecorded, so nothing would ever use it: about a database's worth.
        shutil.rmtree(sets_dir / set_id, ignore_errors=True)
        if prune:
            with contextlib.suppress(Exception):
                prune_sets(database, now=now)
        raise
    manifest_sha256 = hashlib.sha256(body).hexdigest()
    stamp = now.astimezone(UTC).isoformat()
    with database.transaction() as write:
        write.executemany(
            "INSERT INTO backup_objects(transport,key,segment,staged_at) VALUES (?,?,?,?) "
            "ON CONFLICT(transport,key) DO UPDATE SET segment=excluded.segment,"
            "staged_at=excluded.staged_at",
            (
                (transport.name, item.key, placed.assigned[item.path], stamp)
                for item in placed.new_items
            ),
        )
        write.execute(
            "INSERT INTO backup_sets(set_id,created_at,manifest_sha256,db_raw_bytes,db_raw_sha256,"
            "parts,objects,new_objects,transport,uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                set_id,
                stamp,
                manifest_sha256,
                db_record["raw_bytes"],
                db_record["raw_sha256"],
                len(db_record["parts"]),
                index["items"],
                len(placed.new_items),
                transport.name,
                stamp,
            ),
        )
    result: dict[str, Any] = {
        "set_id": set_id,
        "path": str(sets_dir / set_id),
        "manifest_sha256": manifest_sha256,
        "transport": transport.name,
        "commit": commit,
        "db_raw_bytes": db_record["raw_bytes"],
        "parts": len(db_record["parts"]),
        "part_bytes": sum(int(part["bytes"]) for part in db_record["parts"]),
        "items": index["items"],
        "item_bytes": index["item_bytes"],
        "new_items": len(placed.new_items),
        "new_bytes": sum(item.bytes for item in placed.new_items),
        "segments": len(manifest["segments"]),
        "new_segments": placed.new_segments,
        "classes": manifest["classes"],
        "missing": len(snapshot.missing),
        "restored": len(snapshot.restored),
        "absent": sum(1 for item in snapshot.items if item.absent),
        "room": room,
        "cleaned": cleaned,
    }
    if snapshot.missing:
        result["alerted"] = _alert_incomplete(database.state_dir, set_id, snapshot.missing, now)
    if transport.name != LOCAL_TRANSPORT:
        result["remote"] = collect_remote(database, transport, set_id=set_id, now=now)
    if prune:
        result["pruned"] = prune_sets(database, now=now)
    return result


#: What the last upload left, for the watchdog (`[backup-stale]`, `[archive-size]`).
REMOTE_RECORD = "remote.json"


def collect_remote(
    database: Database, transport: Transport, *, set_id: str, now: datetime
) -> dict[str, Any]:
    """After a recorded publish: the remote collector, then `remote.json`.

    A collection that fails does not fail the set, which is already
    recorded: the record carries the error and `[archive-size]` names it.
    `backup_objects` rows of segments the transport no longer holds go, so
    the next set sends those files again.
    """
    record: dict[str, Any] = {
        "transport": transport.name,
        "set_id": set_id,
        "uploaded_at": now.astimezone(UTC).isoformat(),
    }
    try:
        record.update(transport.collect())
        _forget_missing(database, transport.name, transport.segments())
    except (TransportError, OSError, ValueError) as error:
        record["collect_error"] = f"{type(error).__name__}: {error}"[:300]
    durable_write(database.state_dir / "checkpoints" / REMOTE_RECORD, canonical_json(record))
    return record


def _room(database: Database, checkpoints: Path, headroom: int) -> dict[str, Any]:
    """Refuse before `VACUUM INTO` without room for the copy and the headroom.

    `VACUUM INTO` copies only the pages in use, so a file whose free list is
    large (after `storage apply`, before reclaim) needs far less than its size.
    """
    from swingset.state import disk

    conn = database.connection
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
    in_use = page_size * (
        int(conn.execute("PRAGMA page_count").fetchone()[0])
        - int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    )
    found = disk.room(checkpoints)
    need = in_use + headroom
    measured = {"bytes_in_use": in_use, "needed_bytes": need, **found.document()}
    if found.free < need:
        raise BackupError(
            f"a backup set needs about {need} free bytes (the {in_use} bytes in use and "
            f"{headroom} of headroom) and the disk, quota included, has {found.free}; "
            "no set was written"
        )
    return measured


def clean_abandoned(checkpoints: Path, *, now: datetime) -> dict[str, Any]:
    """Remove scratch sets (`sets/.tmp-*`) and segment writes (`segments/.writing-*`)
    older than `ABANDONED`: what a killed backup leaves. A younger one may be
    a backup still running in another process."""
    cutoff = now.timestamp() - ABANDONED.total_seconds()
    removed: list[str] = []
    removed_bytes = 0
    for directory, prefix in ((checkpoints / SETS, ".tmp-"), (checkpoints / SEGMENTS, ".writing-")):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.name.startswith(prefix) or _mtime(path) >= cutoff:
                continue
            removed_bytes += _size(path)
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
            removed.append(f"{directory.name}/{path.name}")
        fsync_dir(directory)
    return {"removed": removed, "removed_bytes": removed_bytes}


def _size(path: Path) -> int:
    try:
        if path.is_dir():
            return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
        return path.stat().st_size
    except OSError:
        return 0


def _new_set_id(sets_dir: Path, now: datetime) -> str:
    base = "set_" + now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate, suffix = base, 2
    while (sets_dir / candidate).exists() or (sets_dir / f".tmp-{candidate}").exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


@dataclass
class _Snapshot:
    items: list[Item]
    schema_version: int
    input_bundle_hash: str | None
    baseline: str | None
    pending: str | None
    #: Named files the closure could not find: `[class, key, reason]`.
    missing: list[list[str]] = field(default_factory=list)
    #: Objects put back from a local set's segment: `[class, digest, set]`.
    restored: list[list[str]] = field(default_factory=list)


def _snapshot(database: Database, staging: Path, control_timeout: float) -> _Snapshot:
    """Copy the database, then read its file closure under the control lock.

    The copy comes first and outside the lock, as `checkpoint.create` does:
    `VACUUM INTO` of a large file takes minutes, and every database open and
    control waits on that lock. A hold is a file written under the lock after
    its targets exist, so a hold newer than the copy only adds files that are
    already on disk.
    """
    from swingset.publish.service import pending_candidates
    from swingset.state.control_lock import control_lock, holds_control_lock
    from swingset.state.retention import RetentionError, referenced_candidates

    from .recovery import SegmentRecovery

    state_dir = database.state_dir
    copy = staging / "db.sqlite"
    fence: contextlib.AbstractContextManager[None] = (
        contextlib.nullcontext()
        if holds_control_lock(state_dir)
        else control_lock(state_dir, timeout=control_timeout)
    )
    _vacuum_into(database, copy)
    with fence:
        candidates = referenced_candidates(state_dir)
        baseline_link = state_dir / "baseline"
        baseline = baseline_link.resolve().name if baseline_link.is_symlink() else None
        pending = [
            path.name
            for path in pending_candidates(state_dir)
            if path.resolve() in candidates and path.name != baseline
        ]
        snapshot = sqlite3.connect(f"{copy.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            schema = int(snapshot.execute("PRAGMA user_version").fetchone()[0])
            row = snapshot.execute(
                "SELECT value FROM meta WHERE key='input_bundle_hash'"
            ).fetchone()
            missing: list[list[str]] = []
            restored: list[list[str]] = []
            recovery = SegmentRecovery(state_dir / "checkpoints")
            try:
                items = list(
                    _closure(
                        state_dir,
                        snapshot,
                        candidates,
                        staging / "files",
                        missing,
                        _Lost(state_dir, recovery, restored),
                    )
                )
                items.sort(key=lambda item: item.path)
            except RetentionError as error:
                raise BackupError(str(error)) from error
            finally:
                recovery.close()
        finally:
            snapshot.close()
    return _Snapshot(
        items,
        schema,
        str(row[0]) if row and row[0] else None,
        baseline,
        pending[0] if pending else None,
        sorted(missing),
        sorted(restored),
    )


def _vacuum_into(database: Database, target: Path) -> None:
    row = database.connection.execute("PRAGMA database_list").fetchone()
    location = Path(str(row[2]))
    reader = sqlite3.connect(
        f"{location.resolve().as_uri()}?mode=ro", uri=True, isolation_level=None
    )
    try:
        reader.execute("PRAGMA busy_timeout=60000")
        reader.execute("VACUUM INTO ?", (str(target),))
    finally:
        reader.close()


class _Lost:
    """An `objects` file the closure names and cannot find (F40).

    The newest local set that holds it puts it back in the state, checked
    against its name. Failing that, the newest local index that names it
    gives its segment and size, and the set names that segment (`absent`)
    when the transport still holds it. Otherwise it is `missing`.
    """

    def __init__(self, state_dir: Path, recovery: Any, restored: list[list[str]]) -> None:
        self.state_dir = state_dir
        self.recovery = recovery
        self.restored = restored

    def __call__(self, cls: str, digest: str, relative: str) -> Item | None:
        from swingset.state.objects import ObjectUnavailable, Store

        try:
            found = self.recovery.recover_object(Store(self.state_dir), cls, digest)
        except ObjectUnavailable:
            hint = self.recovery.find(relative, digest)
            if hint is None:
                return None
            _, segment, size = hint
            return Item(cls, relative, digest, size, absent=True, segment=segment)
        self.restored.append([cls, digest, str(found["set"])])
        return Item(cls, relative, digest, os.stat(self.state_dir / relative).st_size)


def _closure(
    state_dir: Path,
    conn: sqlite3.Connection,
    candidates: set[Path],
    files: Path,
    missing: list[list[str]],
    lost: Callable[[str, str, str], Item | None] | None = None,
) -> Iterator[Item]:
    from swingset.state.objects import STORED
    from swingset.state.retention import artifact_closure

    root = state_dir.resolve()
    seen: set[str] = set()

    def once(item: Item) -> Iterator[Item]:
        if item.path not in seen:
            seen.add(item.path)
            yield item

    # Strings, not paths: a closure holds every body and extract (I10).
    prefix = f"{root}{os.sep}"
    names = sorted(str(path) for path in artifact_closure(state_dir, conn, candidates))
    for name in names:
        if not name.startswith(prefix):
            raise BackupError(f"closure file outside the state: {name}")
        relative = name[len(prefix) :].replace(os.sep, "/")
        top, _, rest = relative.partition("/")
        if top == "blobs":
            yield from once(Item("body", relative, rest.rsplit("/", 1)[-1], os.stat(name).st_size))
        elif top == "extracts":
            yield from once(Item("extract", relative, rest, os.stat(name).st_size))
        elif top == "inputs":
            yield from once(_hashed("input_bundle", relative, Path(name)))
        else:
            raise BackupError(f"unexpected closure file: {relative}")
    del names
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "objects" in tables:
        for cls, digest in conn.execute("SELECT class,digest FROM objects ORDER BY class,digest"):
            if cls not in STORED or not _DIGEST.fullmatch(str(digest)):
                raise BackupError(f"malformed objects row: {cls}/{digest}")
            relative = f"objects/{cls}/{digest[:2]}/{digest}"
            try:
                size = os.stat(state_dir / relative).st_size
            except FileNotFoundError:
                found = lost(str(cls), str(digest), relative) if lost is not None else None
                if found is None:
                    missing.append([str(cls), str(digest), "objects row without a file"])
                else:
                    yield from once(found)
                continue
            yield from once(Item(str(cls), relative, str(digest), size))
        missing.extend(unindexed_packs(conn, tables))
    yield from (
        item for found in _releases(state_dir, conn, tables, missing) for item in once(found)
    )
    for candidate in sorted(candidates):
        for path in sorted(candidate.rglob("*")):
            if path.is_file() and not path.is_symlink() and not _temporary(path.name):
                relative = path.relative_to(root).as_posix()
                yield from once(_captured("candidate", relative, path, files))
    holds = state_dir / "holds"
    if holds.is_dir():
        for path in sorted(holds.rglob("*")):
            if path.is_file() and not path.is_symlink():
                relative = path.relative_to(state_dir).as_posix()
                yield from once(_captured("hold", relative, path, files))
    for path in _others(state_dir):
        relative = path.relative_to(state_dir).as_posix()
        yield from once(_captured("other", relative, path, files))


#: Pack rows whose object has no `objects` row: the set would not hold it.
_UNINDEXED = (
    (
        "derivation_output",
        "derivation_evicted",
        "SELECT DISTINCT g.output_digest FROM derivation_evicted e "
        "JOIN derivation_generations g USING(generation_id) WHERE NOT EXISTS("
        "SELECT 1 FROM objects o WHERE o.class='derivation_output' AND o.digest=g.output_digest) "
        "ORDER BY 1",
    ),
    (
        "parse_body",
        "source_generation_packs",
        "SELECT DISTINCT p.pack_digest FROM source_generation_packs p WHERE NOT EXISTS("
        "SELECT 1 FROM objects o WHERE o.class='parse_body' AND o.digest=p.pack_digest) "
        "ORDER BY 1",
    ),
    (
        "label_body",
        "label_body_packs",
        "SELECT DISTINCT p.pack_digest FROM label_body_packs p WHERE NOT EXISTS("
        "SELECT 1 FROM objects o WHERE o.class='label_body' AND o.digest=p.pack_digest) "
        "ORDER BY 1",
    ),
)


def unindexed_packs(conn: sqlite3.Connection, tables: set[str]) -> list[list[str]]:
    """`[class, digest, reason]` of every pack a row names with no `objects` row.

    Every writer records the object's row with the pack row, so this is
    empty unless a writer is wrong; a set of such a state would restore an
    evicted generation or a packed body with nothing to read it from.
    """
    found: list[list[str]] = []
    for cls, table, sql in _UNINDEXED:
        if table in tables and "objects" in tables:
            found.extend(
                [cls, str(row[0]), f"{table} names no objects row"] for row in conn.execute(sql)
            )
    return found


def _alert_incomplete(
    state_dir: Path, set_id: str, missing: list[list[str]], now: datetime
) -> bool:
    """Append `[backup-incomplete]` to ALERTS.log with the watchdog's stamp."""
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    named = ", ".join(f"{cls}/{key[:16]} ({reason})" for cls, key, reason in missing[:3])
    line = (
        f"[{INCOMPLETE_KEY}] set {set_id} was written without {len(missing)} named files:"
        f" {named}; {REPAIR}"
    )
    try:
        with open(state_dir / "ALERTS.log", "a") as handle:
            handle.write(f"{stamp} {line}\n")
    except OSError:
        return False
    return True


def _releases(
    state_dir: Path, conn: sqlite3.Connection, tables: set[str], missing: list[list[str]]
) -> Iterator[Item]:
    """Release manifests written before phase 4 and the partition artifacts a
    current partition or a retained manifest names."""
    from swingset.state.collector import build_manifests, retained_manifests
    from swingset.state.retention import (
        artifact_directory,
        legacy_manifest_file,
        manifest_artifacts,
        manifest_file,
    )

    if "release_partitions" not in tables:
        return
    artifacts = {
        str(row[0])
        for row in conn.execute(
            "SELECT artifact_digest FROM release_partitions WHERE artifact_digest IS NOT NULL"
        )
    }
    for digest in sorted(retained_manifests(state_dir) | build_manifests(conn)):
        path = manifest_file(state_dir, digest)
        if not path.is_file():
            missing.append(["release_manifest", digest, "retained release manifest is missing"])
            continue
        if path == legacy_manifest_file(state_dir, digest):
            yield _hashed("release_manifest", path.relative_to(state_dir).as_posix(), path)
        try:
            artifacts.update(artifact for _, artifact in manifest_artifacts(state_dir, digest))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise BackupError(f"retained release manifest is unreadable: {digest}") from error
    for digest in sorted(artifacts):
        directory = artifact_directory(state_dir, digest)
        if not directory.is_dir():
            missing.append(["partition_artifact", digest, "named partition artifact is missing"])
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                yield _hashed("partition_artifact", path.relative_to(state_dir).as_posix(), path)


def _others(state_dir: Path) -> Iterator[Path]:
    """Loose state files a checkpoint copied: CDX receipts, alerts, notes."""
    from swingset.state.rwlock import is_lock_file

    for top in sorted(state_dir.iterdir()):
        name = top.name
        if (
            name in _CLASS_DIRS
            or name in _SKIPPED
            or name in _SKIPPED_NAMES
            or is_lock_file(name)
            or _temporary(name)
            or top.is_symlink()
        ):
            continue
        if top.is_file():
            yield top
        elif top.is_dir():
            for path in sorted(top.rglob("*")):
                relative = path.relative_to(top)
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and not any(_temporary(part) for part in relative.parts)
                ):
                    yield path


def _temporary(name: str) -> bool:
    return name.startswith(".") and (".tmp-" in name or name.startswith(".writing-"))


def _hashed(cls: str, relative: str, path: Path) -> Item:
    digest, size = digest_file(cls, path)
    return Item(cls, relative, digest, size)


def _captured(cls: str, relative: str, path: Path, files: Path) -> Item:
    """Copy a file that changes in place into the scratch, hashing as it goes."""
    target = files / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as reader, target.open("xb") as writer:
        while chunk := reader.read(_CHUNK):
            hasher.update(chunk)
            size += len(chunk)
            writer.write(chunk)
    return Item(cls, relative, hasher.hexdigest(), size, str(target))


# ---------------------------------------------------------------------------
# Database parts
# ---------------------------------------------------------------------------


class _Parts(io.RawIOBase):
    """A write sink that cuts the compressed stream into numbered parts."""

    def __init__(self, directory: Path, limit: int) -> None:
        super().__init__()
        directory.mkdir(parents=True)
        self.directory = directory
        self.limit = limit
        self.records: list[dict[str, Any]] = []
        self._handle: IO[bytes] | None = None
        self._hasher = hashlib.sha256()
        self._size = 0

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        view = memoryview(data).cast("B")
        offset = 0
        while offset < len(view):
            if self._handle is None or self._size >= self.limit:
                self._finish()
                self._handle = (self.directory / f"part-{len(self.records):04d}.zst").open("xb")
                self._hasher = hashlib.sha256()
                self._size = 0
            take = min(len(view) - offset, self.limit - self._size)
            chunk = view[offset : offset + take]
            self._handle.write(chunk)
            self._hasher.update(chunk)
            self._size += take
            offset += take
        return len(view)

    def _finish(self) -> None:
        if self._handle is None:
            return
        self._handle.flush()
        os.fsync(self._handle.fileno())
        name = Path(self._handle.name).name
        self._handle.close()
        self._handle = None
        self.records.append(
            {"path": f"db/{name}", "bytes": self._size, "sha256": self._hasher.hexdigest()}
        )

    def close(self) -> None:
        if not self.closed:
            self._finish()
        super().close()


def _write_database(source: Path, staging: Path, part_bytes: int) -> dict[str, Any]:
    import pyarrow as pa

    parts = _Parts(staging / "db", part_bytes)
    raw = hashlib.sha256()
    size = 0
    with pa.CompressedOutputStream(pa.PythonFile(parts, mode="w"), "zstd") as output:
        with source.open("rb") as reader:
            while chunk := reader.read(_CHUNK):
                raw.update(chunk)
                size += len(chunk)
                output.write(chunk)
    parts.close()
    fsync_dir(staging / "db")
    return {"raw_bytes": size, "raw_sha256": raw.hexdigest(), "parts": parts.records}


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------


@dataclass
class _Placed:
    assigned: dict[str, str] = field(default_factory=dict)
    new_items: list[Item] = field(default_factory=list)
    new_segments: list[str] = field(default_factory=list)
    #: Items the set leaves out: `[class, key, reason]`, as `missing` holds them.
    dropped: list[list[str]] = field(default_factory=list)


class _Unusable(BackupError):
    """A file could not be read whole, or does not match its digest; the tar
    stays whole and the set leaves the file out."""


def _key(item: Item) -> str:
    """The `missing` key: the digest for a file named by it, else the path."""
    return (
        item.sha256
        if item.cls in GZIP or item.cls == "extract" or item.path.startswith("objects/")
        else item.path
    )


def _forget_missing(database: Database, transport: str, present: frozenset[str]) -> None:
    """Rows naming a segment the transport no longer has are not a backup."""
    gone = [
        str(row[0])
        for row in database.connection.execute(
            "SELECT DISTINCT segment FROM backup_objects WHERE transport=?", (transport,)
        )
        if str(row[0]) not in present
    ]
    if gone:
        with database.transaction() as conn:
            conn.executemany(
                "DELETE FROM backup_objects WHERE transport=? AND segment=?",
                ((transport, segment) for segment in gone),
            )


def _segment(
    database: Database,
    transport: str,
    items: Sequence[Item],
    present: frozenset[str],
    directory: Path,
    limit: int,
) -> _Placed:
    placed = _Placed()
    writer = _SegmentWriter(database.state_dir, directory, limit)
    try:
        for item in items:
            row = database.connection.execute(
                "SELECT segment FROM backup_objects WHERE transport=? AND key=?",
                (transport, item.key),
            ).fetchone()
            if row is not None and str(row[0]) in present:
                placed.assigned[item.path] = str(row[0])
                continue
            if item.absent:
                if item.segment is not None and item.segment in present:
                    placed.assigned[item.path] = item.segment
                else:
                    placed.dropped.append(
                        [item.cls, _key(item), "objects row without a file; no held segment has it"]
                    )
                continue
            try:
                writer.add(item)
            except _Unusable as error:
                placed.dropped.append([item.cls, _key(item), str(error)])
                continue
            placed.new_items.append(item)
        writer.finish()
    except BaseException:
        writer.abandon()
        raise
    placed.assigned.update(writer.assigned)
    placed.new_segments = writer.finished
    return placed


class _Counting:
    """The tar's output file: counts and hashes what tarfile writes."""

    def __init__(self, handle: IO[bytes]) -> None:
        self.handle = handle
        self.hasher = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        self.handle.write(data)
        self.hasher.update(data)
        self.size += len(data)
        return len(data)

    def tell(self) -> int:
        return self.size

    def flush(self) -> None:
        self.handle.flush()


class _Checked:
    """A source file read into a tar, checked against its index digest.

    A short or failed read is padded with zeros, and a stream that does not
    decompress stops the check, so the tar member stays its declared size
    and the segment stays whole; `error` says what went wrong.
    """

    def __init__(self, handle: IO[bytes], item: Item) -> None:
        self.handle = handle
        self.digest = Digest(item.cls)
        self.error: str | None = None

    def read(self, size: int = -1) -> bytes:
        try:
            data = self.handle.read(size)
        except OSError as error:
            self.error = self.error or f"could not be read: {error}"
            data = b""
        if size > 0 and len(data) < size:
            self.error = self.error or "is shorter than when the closure listed it"
            data += bytes(size - len(data))
        if self.error is None:
            try:
                self.digest.update(data)
            except (zlib.error, ValueError) as error:
                self.error = f"does not decompress: {error}"
        return data


def _overhead(path: str) -> int:
    """Header bytes tarfile may write for one member, PAX record included."""
    return 3 * tarfile.BLOCKSIZE + 2 * len(path.encode())


def _padded(size: int) -> int:
    return -(-size // tarfile.BLOCKSIZE) * tarfile.BLOCKSIZE


def tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.type = tarfile.REGTYPE
    return info


class _SegmentWriter:
    def __init__(self, state_dir: Path, directory: Path, limit: int) -> None:
        self.state_dir = state_dir
        self.directory = directory
        self.limit = limit
        self.assigned: dict[str, str] = {}
        self.finished: list[str] = []
        self._members: list[str] = []
        self._path: Path | None = None
        self._handle: IO[bytes] | None = None
        self._output: _Counting | None = None
        self._tar: tarfile.TarFile | None = None

    def add(self, item: Item) -> None:
        need = _overhead(item.path) + _padded(item.bytes)
        if need + _TAR_RESERVE > self.limit:
            raise BackupError(f"{item.path} ({item.bytes} bytes) does not fit in a segment")
        if (
            self._tar is not None
            and self._output is not None
            and (self._output.size + need + _TAR_RESERVE > self.limit)
        ):
            self._close()
        if self._tar is None:
            self._open()
        assert self._tar is not None
        try:
            source = item.file(self.state_dir).open("rb")
        except OSError as error:
            # Nothing is written to the tar before the file opens.
            raise _Unusable(f"could not be opened: {error}") from error
        with source:
            checked = _Checked(source, item)
            self._tar.addfile(tar_info(item.path, item.bytes), checked)
            if source.read(1):
                checked.error = checked.error or "grew after the closure listed it"
        if checked.error is not None:
            raise _Unusable(checked.error)
        try:
            actual = checked.digest.hexdigest()
        except ValueError as error:
            raise _Unusable(f"does not decompress: {error}") from error
        if actual != item.sha256:
            raise _Unusable("does not match its digest")
        self._members.append(item.path)

    def _open(self) -> None:
        self._path = self.directory / f".writing-{uuid.uuid4().hex}.tar"
        self._handle = self._path.open("xb")
        self._output = _Counting(self._handle)
        self._tar = tarfile.open(fileobj=self._output, mode="w", format=tarfile.PAX_FORMAT)  # type: ignore[call-overload]

    def _close(self) -> None:
        assert self._tar is not None and self._output is not None and self._handle is not None
        assert self._path is not None
        self._tar.close()
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._handle.close()
        if self._output.size > MAX_FILE_BYTES:
            raise BackupError("a segment grew over the 4 GiB limit")
        digest = self._output.hasher.hexdigest()
        os.replace(self._path, self.directory / f"{digest}.tar")
        fsync_dir(self.directory)
        for name in self._members:
            self.assigned[name] = digest
        self.finished.append(digest)
        self._members = []
        self._tar = self._output = self._handle = self._path = None

    def finish(self) -> None:
        if self._tar is not None:
            self._close()

    def abandon(self) -> None:
        if self._handle is not None:
            with contextlib.suppress(OSError):
                self._handle.close()
        if self._path is not None:
            with contextlib.suppress(FileNotFoundError):
                self._path.unlink()
        self._tar = self._output = self._handle = self._path = None


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


def _write_index(path: Path, items: Sequence[Item], assigned: dict[str, str]) -> dict[str, Any]:
    import pyarrow as pa

    count = total = 0
    with pa.output_stream(str(path), compression="zstd") as output:
        for item in items:
            line = json.dumps(
                [item.cls, item.path, item.sha256, item.bytes, assigned[item.path]],
                separators=(",", ":"),
                ensure_ascii=False,
            )
            output.write(line.encode() + b"\n")
            count += 1
            total += item.bytes
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)
    return {
        "path": INDEX,
        "bytes": path.stat().st_size,
        "sha256": hasher.hexdigest(),
        "items": count,
        "item_bytes": total,
    }


# ---------------------------------------------------------------------------
# Local sets
# ---------------------------------------------------------------------------


def local_sets(checkpoints: Path) -> list[str]:
    """Complete local set ids, oldest first."""
    directory = checkpoints / SETS
    if not directory.is_dir():
        return []
    return sorted(
        path.name
        for path in directory.iterdir()
        if SET_ID.match(path.name) and (path / MANIFEST).is_file()
    )


def read_manifest(set_dir: Path) -> dict[str, Any]:
    value = json.loads((set_dir / MANIFEST).read_bytes())
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        raise ValueError(f"{set_dir.name} is not a backup set")
    return value


def prune_sets(database: Database, *, now: datetime) -> dict[str, Any]:
    """Keep the newest local set and the newest drilled one; remove the rest.

    Segments no kept set names go, with their `backup_objects` rows for the
    local transport (a remote copy of the same segment keeps its rows). A
    scratch set or a segment younger than a day stays: a backup running in
    another process may still be writing it.
    """
    checkpoints = database.state_dir / "checkpoints"
    sets_dir, segments_dir = checkpoints / SETS, checkpoints / SEGMENTS
    complete = local_sets(checkpoints)
    kept: set[str] = set(complete[-1:])
    for (set_id,) in database.connection.execute(
        "SELECT set_id FROM backup_sets WHERE drilled_at IS NOT NULL ORDER BY set_id DESC"
    ):
        if str(set_id) in complete:
            kept.add(str(set_id))
            break
    cutoff = now.timestamp() - ABANDONED.total_seconds()
    removed_sets: list[str] = []
    for set_id in complete:
        if set_id not in kept:
            shutil.rmtree(sets_dir / set_id)
            removed_sets.append(set_id)
    abandoned = clean_abandoned(checkpoints, now=now)
    removed_sets.extend(
        name.removeprefix(f"{SETS}/") for name in abandoned["removed"] if name.startswith(SETS)
    )
    if sets_dir.is_dir():
        fsync_dir(sets_dir)
    needed: set[str] = set()
    for set_id in sorted(kept):
        needed.update(str(segment) for segment in read_manifest(sets_dir / set_id)["segments"])
    removed: list[str] = []
    removed_bytes = 0
    if segments_dir.is_dir():
        for path in sorted(segments_dir.iterdir()):
            match = SEGMENT_NAME.match(path.name)
            if match and match.group(1) not in needed and _mtime(path) < cutoff:
                with contextlib.suppress(FileNotFoundError):
                    removed_bytes += path.stat().st_size
                    path.unlink()
                if match:
                    removed.append(match.group(1))
        fsync_dir(segments_dir)
    if removed:
        with database.transaction() as conn:
            conn.executemany(
                "DELETE FROM backup_objects WHERE transport=? AND segment=?",
                ((LOCAL_TRANSPORT, segment) for segment in removed),
            )
    return {
        "kept": sorted(kept),
        "removed_sets": removed_sets,
        "removed_segments": len(removed),
        "removed_bytes": removed_bytes,
    }


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return float("inf")


__all__ = [
    "BackupError",
    "Digest",
    "Entry",
    "TransportError",
    "clean_abandoned",
    "create_set",
    "local_sets",
    "prune_sets",
    "read_index",
    "read_manifest",
]
