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

The collector is `state/collector.py` (phase 4, WP5, D-0231):
`collect_artifacts` runs its `partition_artifact` and `release_manifest`
classes in the daily maintain loop, outside the build's code scope. It removes
an artifact that no current `release_partitions` row and no manifest of a
retained candidate (the baseline, pending candidates, the newest three built
and the last three published) names, once it has been unnamed for a day, and
temporary directories older than a day. It removes a release manifest object,
its rows and any phase 3 file the same way: one no retained candidate and no
current build generation names, written more than a day ago (I6). And it
removes the scratch a killed build left under `state_dir/tmp` (`assembly-*`,
`partitions-*`, `closure-*`) once it is a day old.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from swingset.state.retention import artifact_directory, partition_root

from .files import canonical_json, fsync_dir, sha256_file

SIDECAR = "sidecar.json"
_TEMPORARY = ".tmp-"


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
