"""Release lifecycle rows: partitions, artifacts and manifests (migration 35).

`release_partitions` holds each partition's latest state: `built` with its
artifact digest, or `withheld` with a reason. `release_artifacts` and
`release_manifests` record the artifact directories and manifest files under
`state_dir/partitions/` that exist, their size, and when something last named
them. The sidecars and manifests are files, not rows. This module reads and
writes rows; the build and the collector decide when.
"""

from __future__ import annotations

import sqlite3
from dataclasses import astuple, dataclass, fields
from typing import Literal

PartitionState = Literal["built", "withheld"]


@dataclass(frozen=True)
class PartitionRow:
    partition: str
    inputs_digest: str
    state: PartitionState
    built_at: str
    run_id: str
    artifact_digest: str | None = None
    reason: str | None = None
    detail_json: str | None = None


@dataclass(frozen=True)
class ArtifactRow:
    artifact_digest: str
    partition: str
    bytes: int
    created_at: str
    last_named_at: str


@dataclass(frozen=True)
class ManifestRow:
    manifest_digest: str
    bytes: int
    created_at: str
    candidate_id: str | None = None
    verdict_json: str | None = None


Row = PartitionRow | ArtifactRow | ManifestRow

_TABLES: dict[type, tuple[str, str]] = {
    PartitionRow: ("release_partitions", "partition"),
    ArtifactRow: ("release_artifacts", "artifact_digest"),
    ManifestRow: ("release_manifests", "manifest_digest"),
}


def _columns(kind: type) -> tuple[str, ...]:
    return tuple(field.name for field in fields(kind))


def get[T: Row](conn: sqlite3.Connection, kind: type[T], key: str) -> T | None:
    table, primary = _TABLES[kind]
    row = conn.execute(
        f"SELECT {','.join(_columns(kind))} FROM {table} WHERE {primary}=?", (key,)
    ).fetchone()
    return None if row is None else kind(*row)


def upsert(conn: sqlite3.Connection, row: Row) -> None:
    table, primary = _TABLES[type(row)]
    columns = _columns(type(row))
    updates = ",".join(f"{name}=excluded.{name}" for name in columns if name != primary)
    conn.execute(
        f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' * len(columns))}) "
        f"ON CONFLICT({primary}) DO UPDATE SET {updates}",
        astuple(row),
    )


def delete(conn: sqlite3.Connection, kind: type[Row], key: str) -> bool:
    table, primary = _TABLES[kind]
    return conn.execute(f"DELETE FROM {table} WHERE {primary}=?", (key,)).rowcount > 0


def all_rows[T: Row](conn: sqlite3.Connection, kind: type[T]) -> list[T]:
    table, primary = _TABLES[kind]
    return [
        kind(*row)
        for row in conn.execute(
            f"SELECT {','.join(_columns(kind))} FROM {table} ORDER BY {primary}"
        )
    ]


def withheld(conn: sqlite3.Connection) -> list[PartitionRow]:
    """Every withheld partition, in partition order."""
    columns = ",".join(_columns(PartitionRow))
    return [
        PartitionRow(*row)
        for row in conn.execute(
            f"SELECT {columns} FROM release_partitions WHERE state='withheld' ORDER BY partition"
        )
    ]


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Partitions by state, withheld partitions by reason, artifacts and manifests."""
    result: dict[str, int] = {"built": 0, "withheld": 0}
    for state, n in conn.execute("SELECT state,count(*) FROM release_partitions GROUP BY state"):
        result[str(state)] = int(n)
    for reason, n in conn.execute(
        "SELECT reason,count(*) FROM release_partitions WHERE state='withheld' GROUP BY reason"
    ):
        result[f"withheld:{reason}"] = int(n)
    for name, table in (("artifacts", "release_artifacts"), ("manifests", "release_manifests")):
        n, size = conn.execute(f"SELECT count(*),coalesce(sum(bytes),0) FROM {table}").fetchone()
        result[name] = int(n)
        result[f"{name}_bytes"] = int(size)
    return result
