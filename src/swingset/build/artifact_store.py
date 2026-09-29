"""Partition artifact files and their collector (plan phase 3, WP6).

An artifact is a directory `partitions/<aa>/<digest>/` under the state
directory: one Parquet file per non-empty table and `sidecar.json`. Its digest
is the sha256 of the sidecar bytes, and the sidecar holds each file's sha256,
so the digest names the whole directory. A write goes to a temporary
directory, is fsynced and then renamed; a crash leaves only a directory no
row names, which the collector removes (I8). Files are never rewritten,
except that a damaged artifact (`intact` false: a file missing, or its bytes
not those the sidecar records) is replaced by the next write of the same
digest; stage 1 reuses an artifact only when it is intact.

The collector runs in the daily maintain loop. It removes an artifact that
no current `release_partitions` row and no manifest of a retained candidate
(the baseline, pending candidates and the newest three built) names, once it
has been unnamed for a day, and temporary directories older than a day. It
removes a release manifest file and its `release_manifests` row the same way:
one no retained candidate and no current build generation names, written
more than a day ago (I6). And it removes the scratch a killed build left
under `state_dir/tmp` (`assembly-*`, `partitions-*`, `closure-*`) once it is
a day old.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from swingset.state import release_store
from swingset.state.retention import (
    _is_digest,
    artifact_directory,
    manifest_artifacts,
    manifest_file,
    partition_root,
    referenced_candidates,
)

from .files import canonical_json, fsync_dir, sha256_file

SIDECAR = "sidecar.json"
#: Unnamed artifacts and temporary directories younger than this stay.
UNNAMED_AGE = timedelta(days=1)
#: Newest built candidates whose manifests keep their artifacts.
NEWEST_CANDIDATES = 3
#: Directories removed per collection, so one write stays short.
COLLECT_BATCH = 500
_TEMPORARY = ".tmp-"
#: Scratch directories a build makes under `state_dir/tmp`; a killed build
#: leaves them (the baseline spool, the assembly's staging, the closure spool).
SCRATCH_PREFIXES = ("assembly-", "partitions-", "closure-")


def write(
    state_dir: Path, tables: Mapping[str, pa.Table], sidecar: Mapping[str, Any]
) -> tuple[str, int]:
    """Store one artifact and return its digest and bytes.

    ``sidecar`` is completed with a `tables` entry per file. An identical
    artifact already stored is kept as it is.
    """
    root = partition_root(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f"{_TEMPORARY}{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        files: dict[str, dict[str, Any]] = {}
        for name in sorted(tables):
            path = temporary / f"{name}.parquet"
            pq.write_table(tables[name], path, compression="zstd")
            _fsync(path)
            files[name] = {
                "file": path.name,
                "rows": tables[name].num_rows,
                "sha256": sha256_file(path),
            }
        body = canonical_json({**sidecar, "tables": files})
        (temporary / SIDECAR).write_bytes(body)
        _fsync(temporary / SIDECAR)
        fsync_dir(temporary)
        digest = hashlib.sha256(body).hexdigest()
        size = sum(path.stat().st_size for path in temporary.iterdir())
        final = artifact_directory(state_dir, digest)
        if intact(state_dir, digest):
            shutil.rmtree(temporary)
            return digest, size
        if final.exists():
            # A damaged copy of the same artifact: replace it whole.
            shutil.rmtree(final)
        final.parent.mkdir(parents=True, exist_ok=True)
        _publish(temporary, final)
        fsync_dir(final.parent)
        return digest, size
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _publish(temporary: Path, final: Path) -> None:
    os.replace(temporary, final)


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def exists(state_dir: Path, digest: str) -> bool:
    return (artifact_directory(state_dir, digest) / SIDECAR).is_file()


def intact(state_dir: Path, digest: str) -> bool:
    """Whether the sidecar hashes to ``digest`` and every file it lists to its sha256.

    About 73 MB read per build on production's artifacts.
    """
    directory = artifact_directory(state_dir, digest)
    try:
        body = (directory / SIDECAR).read_bytes()
        if hashlib.sha256(body).hexdigest() != digest:
            return False
        for entry in json.loads(body)["tables"].values():
            if sha256_file(directory / entry["file"]) != entry["sha256"]:
                return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return True


def sidecar(state_dir: Path, digest: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(
        (artifact_directory(state_dir, digest) / SIDECAR).read_bytes()
    )
    return value


def read_table(state_dir: Path, digest: str, table: str) -> list[dict[str, Any]]:
    """One table of an artifact as rows; empty when the artifact has no such file."""
    entry = sidecar(state_dir, digest)["tables"].get(table)
    if entry is None:
        return []
    rows: list[dict[str, Any]] = pq.read_table(
        artifact_directory(state_dir, digest) / entry["file"]
    ).to_pylist()
    return rows


def retained_manifests(state_dir: Path) -> set[str]:
    """Manifest digests of the baseline, pending candidates and the newest three built."""
    candidates = set(referenced_candidates(state_dir))
    directory = state_dir / "candidates"
    if directory.is_dir():
        built = sorted(
            (path for path in directory.iterdir() if (path / "BUILT").is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        candidates.update(path.resolve() for path in built[:NEWEST_CANDIDATES])
    result = set()
    for candidate in candidates:
        try:
            manifest = json.loads((candidate / "_meta" / "manifest.json").read_bytes())
        except (OSError, ValueError):
            continue
        policy = manifest.get("release_policy") if isinstance(manifest, dict) else None
        if isinstance(policy, dict) and policy.get("mode") == "partitioned":
            digest = policy.get("manifest_digest")
            if _is_digest(digest):
                result.add(str(digest))
    return result


def build_manifests(conn: sqlite3.Connection) -> set[str]:
    """Manifest digests the current build generations' candidates name.

    `derivations._artifact_valid` reads a partitioned candidate's manifest
    file, so the collector keeps it while a build pointer stands for it.
    """
    result = set()
    for (payload,) in conn.execute(
        "SELECT r.payload_json FROM derivation_scopes s JOIN derivation_rows r "
        "ON r.generation_id=s.materialized_generation_id WHERE s.stage='build' "
        "AND r.table_name='artifact'"
    ):
        try:
            path = Path(json.loads(payload)["path"])
            manifest = json.loads((path / "_meta" / "manifest.json").read_bytes())
        except (OSError, ValueError, KeyError, TypeError):
            continue
        policy = manifest.get("release_policy") if isinstance(manifest, dict) else None
        if isinstance(policy, dict) and _is_digest(policy.get("manifest_digest")):
            result.add(str(policy["manifest_digest"]))
    return result


def named(conn: sqlite3.Connection, state_dir: Path) -> set[str] | None:
    """Artifacts a current partition row or a retained manifest names.

    None when a retained manifest cannot be read: the collector then removes
    nothing rather than guess.
    """
    result = {
        str(row[0])
        for row in conn.execute(
            "SELECT artifact_digest FROM release_partitions WHERE artifact_digest IS NOT NULL"
        )
    }
    for manifest in retained_manifests(state_dir):
        try:
            result.update(artifact for _, artifact in manifest_artifacts(state_dir, manifest))
        except (OSError, ValueError, KeyError, TypeError):
            return None
    return result


def collect(database: Any, *, now: datetime) -> dict[str, int]:
    """Remove artifacts nothing has named for a day, and old temporary directories.

    Rows and directories go inside one write, so a build that names an
    artifact either sees it gone and writes it again, or names it first and
    keeps it.
    """
    state_dir: Path = database.state_dir
    root = partition_root(state_dir)
    result = {
        "removed": 0,
        "bytes": 0,
        "rows": 0,
        "temporary": 0,
        "kept": 0,
        "unreadable": 0,
        "manifests": 0,
        "scratch": 0,
    }
    cutoff = now - UNNAMED_AGE
    scratch = state_dir / "tmp"
    if scratch.is_dir():
        for path in sorted(scratch.iterdir()):
            if path.name.startswith(SCRATCH_PREFIXES) and _older(path, cutoff):
                result["bytes"] += _remove(path)
                result["scratch"] += 1
    if not _available(database.connection):
        return result
    with database.transaction() as conn:
        keep = named(conn, state_dir)
        if keep is None:
            result["unreadable"] = 1
            return result
        rows = {
            row.artifact_digest: row
            for row in release_store.all_rows(conn, release_store.ArtifactRow)
        }
        removed = 0
        for digest, row in sorted(rows.items()):
            if digest in keep:
                result["kept"] += 1
                continue
            if datetime.fromisoformat(row.last_named_at) >= cutoff or removed >= COLLECT_BATCH:
                continue
            result["bytes"] += _remove(artifact_directory(state_dir, digest))
            release_store.delete(conn, release_store.ArtifactRow, digest)
            result["rows"] += 1
            removed += 1
        if root.is_dir():
            for prefix in sorted(root.iterdir()):
                if prefix.name.startswith(_TEMPORARY):
                    if _older(prefix, cutoff) and removed < COLLECT_BATCH:
                        result["bytes"] += _remove(prefix)
                        result["temporary"] += 1
                        removed += 1
                    continue
                if len(prefix.name) != 2 or not prefix.is_dir():
                    continue
                for directory in sorted(prefix.iterdir()):
                    digest = directory.name
                    if (
                        not _is_digest(digest)
                        or digest in keep
                        or digest in rows
                        or not _older(directory, cutoff)
                        or removed >= COLLECT_BATCH
                    ):
                        continue
                    result["bytes"] += _remove(directory)
                    removed += 1
        _collect_manifests(conn, state_dir, cutoff, result, removed)
        result["removed"] = removed
    return result


def _collect_manifests(
    conn: sqlite3.Connection,
    state_dir: Path,
    cutoff: datetime,
    result: dict[str, int],
    removed: int,
) -> None:
    """Remove manifests no retained candidate or build pointer names, a day after writing."""
    keep = retained_manifests(state_dir) | build_manifests(conn)
    rows = {
        row.manifest_digest: row for row in release_store.all_rows(conn, release_store.ManifestRow)
    }
    for digest, row in sorted(rows.items()):
        if digest in keep or removed >= COLLECT_BATCH:
            continue
        path = manifest_file(state_dir, digest)
        if datetime.fromisoformat(row.created_at) >= cutoff or (
            path.exists() and not _older(path, cutoff)
        ):
            continue
        result["bytes"] += _remove(path)
        release_store.delete(conn, release_store.ManifestRow, digest)
        result["manifests"] += 1
        removed += 1
    directory = manifest_file(state_dir, "0" * 64).parent
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            digest = path.stem
            if (
                not _is_digest(digest)
                or digest in keep
                or digest in rows
                or not _older(path, cutoff)
                or removed >= COLLECT_BATCH
            ):
                continue
            result["bytes"] += _remove(path)
            result["manifests"] += 1
            removed += 1


def _available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='release_artifacts'"
        ).fetchone()
        is not None
    )


def _older(path: Path, cutoff: datetime) -> bool:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=cutoff.tzinfo) < cutoff
    except OSError:
        return False


def _remove(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        return size
    size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    shutil.rmtree(path, ignore_errors=True)
    return size
