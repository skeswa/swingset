"""Compare schema-29 derivation row storage on disposable full database copies.

The source must be a closed, disposable schema-29 state database outside
``/var/lib/swingset``. It is opened only with ``mode=ro&immutable=1`` and is
also copied as an ordinary file; it is never opened writable. The tool builds
two new full-database copies under a new scratch directory:

* ``baseline.sqlite`` keeps the ordinary rowid ``derivation_rows`` table.
* ``without-rowid.sqlite`` rebuilds only that table with ``WITHOUT ROWID``.

Both copies are analyzed and vacuumed. The candidate keeps the deferred
foreign key, primary key, logical-key uniqueness constraint, and every trigger
attached to the original table. The report compares whole-file and dbstat
bytes, exact streamed content, constraint behavior, query plans and timings,
and deterministic gzip sizes. Gzip is only a backup-size proxy; this tool does
not create or restore a Swingset checkpoint.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import platform
import shutil
import sqlite3
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOL_VERSION = "benchmark-derivation-row-layout-v2"
REPORT_FORMAT = "derivation-row-layout-benchmark-v2"
RECEIPT_FORMAT = "derivation-row-layout-benchmark-receipt-v2"
PROTECTED_ROOTS = ("/var/lib/swingset",)
EXPECTED_SCHEMA_VERSION = 29
ROW_COLUMNS = ("generation_id", "ordinal", "table_name", "record_key", "payload_json")

SOURCE_ROW_TABLE = """
CREATE TABLE derivation_rows (
    generation_id TEXT NOT NULL REFERENCES derivation_generations(generation_id)
        DEFERRABLE INITIALLY DEFERRED,
    ordinal INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    record_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(generation_id,ordinal),
    UNIQUE(generation_id,table_name,record_key)
)
"""

ROW_TABLE = """
CREATE TABLE benchmark_derivation_rows (
    generation_id TEXT NOT NULL REFERENCES derivation_generations(generation_id)
        DEFERRABLE INITIALLY DEFERRED,
    ordinal INTEGER NOT NULL,
    table_name TEXT NOT NULL,
    record_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(generation_id,ordinal),
    UNIQUE(generation_id,table_name,record_key)
) WITHOUT ROWID
"""

QUERY_SPECS = (
    (
        "generation_count",
        "SELECT count(*) FROM derivation_rows WHERE generation_id=?",
        "generation",
    ),
    (
        "generation_ordered_stream",
        "SELECT table_name,record_key,payload_json FROM derivation_rows "
        "WHERE generation_id=? ORDER BY ordinal",
        "generation",
    ),
    (
        "generation_artifacts",
        "SELECT payload_json FROM derivation_rows WHERE generation_id=? AND table_name='artifact'",
        "generation",
    ),
    (
        "exact_logical_key",
        "SELECT payload_json FROM derivation_rows "
        "WHERE generation_id=? AND table_name=? AND record_key=?",
        "row",
    ),
    (
        "retention_grouping",
        "SELECT generation_id,count(*),coalesce(sum(length(CAST(payload_json AS BLOB))),0) "
        "FROM derivation_rows GROUP BY generation_id",
        "none",
    ),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rendered(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_identity(path: Path) -> dict[str, int]:
    status = path.stat()
    return {
        "bytes": status.st_size,
        "mtime_ns": status.st_mtime_ns,
        "inode": status.st_ino,
    }


def file_signature(path: Path) -> dict[str, int | str]:
    return {**file_identity(path), "sha256": file_sha256(path)}


def runtime_metadata() -> dict[str, str]:
    tool = Path(__file__).resolve(strict=True)
    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "sqlite_version": sqlite3.sqlite_version,
        "tool_path": str(tool),
        "tool_sha256": file_sha256(tool),
    }


def _protected(path: Path) -> str | None:
    for name in PROTECTED_ROOTS:
        root = Path(name).resolve(strict=False)
        if path.is_relative_to(root):
            return name
    return None


def resolve_source(target: Path) -> tuple[Path, Path]:
    expanded = target.expanduser()
    candidate = expanded if expanded.suffix in {".sqlite", ".db"} else expanded / "state.sqlite"
    require(candidate.is_file(), f"no state database at {candidate}")
    require(not candidate.is_symlink(), "source database must not be a symlink")
    database = candidate.resolve(strict=True)
    source_root = database.parent
    protected = _protected(source_root)
    require(
        protected is None,
        f"refusing {source_root}: {protected} is the live state; use a disposable copy",
    )
    return source_root, database


def resolve_new_path(path: Path, label: str, source_root: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    protected = _protected(resolved)
    require(protected is None, f"refusing to write {label} under live root {protected}")
    require(not resolved.is_relative_to(source_root), f"{label} must be outside the source")
    require(not source_root.is_relative_to(resolved), f"{label} must not contain the source")
    require(not resolved.exists(), f"{label} must be new; {resolved} already exists")
    return resolved


def source_sidecars(database: Path) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(database) + suffix)
        result[suffix[1:]] = sidecar.stat().st_size if sidecar.exists() else None
    return result


def open_immutable(database: Path) -> sqlite3.Connection:
    sidecars = source_sidecars(database)
    require(
        sidecars["wal"] in (None, 0),
        "source has a nonempty WAL; close it and checkpoint a disposable copy first",
    )
    require(
        sidecars["shm"] is None,
        "source has a shared-memory file; close every connection to the disposable copy",
    )
    require(
        sidecars["journal"] is None,
        "source has a rollback journal; recover or recopy the closed disposable source first",
    )
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _normalized_sql(sql: str) -> str:
    return "".join(sql.split()).casefold().removesuffix(";")


def row_table_metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT type,sql FROM sqlite_schema WHERE name='derivation_rows'"
    ).fetchone()
    require(row is not None and row[0] == "table", "schema 29 derivation_rows table is required")
    indexes: list[dict[str, Any]] = []
    for _, name, unique, origin, partial in connection.execute(
        "PRAGMA index_list(derivation_rows)"
    ):
        key_columns = [
            {
                "name": str(column[2]),
                "descending": bool(column[3]),
                "collation": str(column[4]),
            }
            for column in connection.execute(f"PRAGMA index_xinfo('{str(name)}')")
            if bool(column[5])
        ]
        indexes.append(
            {
                "name": str(name),
                "unique": bool(unique),
                "origin": str(origin),
                "partial": bool(partial),
                "key_columns": key_columns,
            }
        )
    indexes.sort(key=lambda item: item["name"])
    return {
        "sql": str(row[1]),
        "columns": [
            {
                "cid": int(item[0]),
                "name": str(item[1]),
                "type": str(item[2]),
                "not_null": bool(item[3]),
                "default": item[4],
                "primary_key_position": int(item[5]),
                "hidden": int(item[6]),
            }
            for item in connection.execute("PRAGMA table_xinfo(derivation_rows)")
        ],
        "foreign_keys": [
            {
                "id": int(item[0]),
                "sequence": int(item[1]),
                "table": str(item[2]),
                "from": str(item[3]),
                "to": str(item[4]),
                "on_update": str(item[5]),
                "on_delete": str(item[6]),
                "match": str(item[7]),
            }
            for item in connection.execute("PRAGMA foreign_key_list(derivation_rows)")
        ],
        "indexes": indexes,
    }


def validate_source(connection: sqlite3.Connection) -> dict[str, Any]:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    require(version == EXPECTED_SCHEMA_VERSION, f"expected schema 29, found schema {version}")
    metadata = row_table_metadata(connection)
    require(
        _normalized_sql(metadata["sql"]) == _normalized_sql(SOURCE_ROW_TABLE),
        "derivation_rows definition differs from canonical schema 29",
    )
    require(
        [column["name"] for column in metadata["columns"]] == list(ROW_COLUMNS),
        "unexpected derivation_rows columns",
    )
    require(
        metadata["foreign_keys"]
        == [
            {
                "id": 0,
                "sequence": 0,
                "table": "derivation_generations",
                "from": "generation_id",
                "to": "generation_id",
                "on_update": "NO ACTION",
                "on_delete": "NO ACTION",
                "match": "NONE",
            }
        ],
        "derivation_rows foreign key differs from canonical schema 29",
    )
    require(
        [
            (index["origin"], [column["name"] for column in index["key_columns"]])
            for index in metadata["indexes"]
        ]
        == [
            ("pk", ["generation_id", "ordinal"]),
            ("u", ["generation_id", "table_name", "record_key"]),
        ],
        "derivation_rows indexes differ from canonical schema 29",
    )
    unexpected = list(
        connection.execute(
            "SELECT type,name,tbl_name FROM sqlite_schema "
            "WHERE sql LIKE '%derivation_rows%' AND name<>'derivation_rows' "
            "AND NOT (type='trigger' AND tbl_name='derivation_rows')"
        )
    )
    require(not unexpected, f"unsupported derivation_rows schema dependencies: {unexpected}")
    return metadata


def table_triggers(connection: sqlite3.Connection) -> list[dict[str, str]]:
    return [
        {"name": str(name), "sql": str(sql)}
        for name, sql in connection.execute(
            "SELECT name,sql FROM sqlite_schema WHERE type='trigger' "
            "AND tbl_name='derivation_rows' ORDER BY name"
        )
    ]


def copy_database(source: Path, destination: Path) -> float:
    started = time.perf_counter()
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=8 * 1024 * 1024)
    return time.perf_counter() - started


def _prepare_writable(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")


def compact_baseline(database: Path) -> dict[str, float]:
    started = time.perf_counter()
    with sqlite3.connect(database) as connection:
        _prepare_writable(connection)
        connection.execute("ANALYZE derivation_rows")
        vacuum_started = time.perf_counter()
        connection.execute("VACUUM")
        vacuum_seconds = time.perf_counter() - vacuum_started
    return {"prepare_seconds": time.perf_counter() - started, "vacuum_seconds": vacuum_seconds}


def rebuild_without_rowid(database: Path) -> dict[str, float]:
    started = time.perf_counter()
    with sqlite3.connect(database) as connection:
        _prepare_writable(connection)
        triggers = table_triggers(connection)
        foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        require(foreign_keys == 1, "could not enable foreign keys on candidate")
        connection.execute("PRAGMA foreign_keys=OFF")
        rebuild_started = time.perf_counter()
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(ROW_TABLE)
            connection.execute(
                "INSERT INTO benchmark_derivation_rows "
                "SELECT generation_id,ordinal,table_name,record_key,payload_json "
                "FROM derivation_rows ORDER BY generation_id,ordinal"
            )
            connection.execute("DROP TABLE derivation_rows")
            connection.execute("ALTER TABLE benchmark_derivation_rows RENAME TO derivation_rows")
            for trigger in triggers:
                connection.execute(trigger["sql"])
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        rebuild_seconds = time.perf_counter() - rebuild_started
        connection.execute("PRAGMA foreign_keys=ON")
        require(
            int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) == 1,
            "could not restore candidate foreign-key enforcement",
        )
        connection.execute("ANALYZE derivation_rows")
        vacuum_started = time.perf_counter()
        connection.execute("VACUUM")
        vacuum_seconds = time.perf_counter() - vacuum_started
    return {
        "prepare_seconds": time.perf_counter() - started,
        "rebuild_seconds": rebuild_seconds,
        "vacuum_seconds": vacuum_seconds,
    }


def _update_framed(digest: Any, values: Iterable[object]) -> None:
    for value in values:
        encoded = str(value).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)


def content_profile(connection: sqlite3.Connection) -> dict[str, Any]:
    label_digest = hashlib.sha256()
    row_digest = hashlib.sha256()
    generations = 0
    declared_rows = 0
    for generation_id, output_digest, row_count in connection.execute(
        "SELECT generation_id,output_digest,row_count FROM derivation_generations "
        "ORDER BY generation_id"
    ):
        generations += 1
        declared_rows += int(row_count)
        _update_framed(label_digest, (generation_id, output_digest, row_count))

    rows = 0
    payload_bytes = 0
    for item in connection.execute(
        "SELECT generation_id,ordinal,table_name,record_key,payload_json "
        "FROM derivation_rows ORDER BY generation_id,ordinal"
    ):
        rows += 1
        payload_bytes += len(str(item[4]).encode())
        _update_framed(row_digest, item)

    mismatch_count = 0
    mismatch_examples: list[dict[str, Any]] = []
    for generation_id, expected_digest, expected_count in connection.execute(
        "SELECT generation_id,output_digest,row_count FROM derivation_generations "
        "ORDER BY generation_id"
    ):
        actual = hashlib.sha256()
        actual_count = 0
        for table_name, record_key, payload_json in connection.execute(
            "SELECT table_name,record_key,payload_json FROM derivation_rows "
            "WHERE generation_id=? ORDER BY ordinal",
            (generation_id,),
        ):
            actual_count += 1
            actual.update(canonical((table_name, record_key, payload_json)).encode() + b"\n")
        actual_digest = actual.hexdigest()
        if (actual_digest, actual_count) != (str(expected_digest), int(expected_count)):
            mismatch_count += 1
            if len(mismatch_examples) < 20:
                mismatch_examples.append(
                    {
                        "generation_id": str(generation_id),
                        "expected_digest": str(expected_digest),
                        "actual_digest": actual_digest,
                        "expected_count": int(expected_count),
                        "actual_count": actual_count,
                    }
                )

    orphans = int(
        connection.execute(
            "SELECT count(*) FROM derivation_rows r LEFT JOIN derivation_generations g "
            "ON g.generation_id=r.generation_id WHERE g.generation_id IS NULL"
        ).fetchone()[0]
    )
    return {
        "generations": generations,
        "declared_rows": declared_rows,
        "rows": rows,
        "payload_bytes": payload_bytes,
        "generation_label_stream_sha256": label_digest.hexdigest(),
        "row_stream_sha256": row_digest.hexdigest(),
        "output_label_mismatch_count": mismatch_count,
        "output_label_mismatch_examples": mismatch_examples,
        "output_label_mismatch_examples_truncated": mismatch_count > len(mismatch_examples),
        "rows_without_generation": orphans,
    }


def integrity(connection: sqlite3.Connection) -> dict[str, Any]:
    integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    foreign_keys = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
    return {
        "integrity_check": integrity_rows,
        "foreign_key_check": foreign_keys,
        "passed": integrity_rows == ["ok"] and not foreign_keys,
    }


def storage_profile(connection: sqlite3.Connection, database: Path) -> dict[str, Any]:
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    freelist = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
    objects = {
        str(name): {"pages": int(pages), "bytes": int(size)}
        for name, pages, size in connection.execute(
            "SELECT name,count(*),sum(pgsize) FROM dbstat GROUP BY name"
        )
    }
    indexes: list[dict[str, Any]] = []
    for _, name, unique, origin, partial in connection.execute(
        "PRAGMA index_list(derivation_rows)"
    ):
        indexes.append(
            {
                "name": str(name),
                "unique": bool(unique),
                "origin": str(origin),
                "partial": bool(partial),
                "columns": [
                    str(row[2]) for row in connection.execute(f"PRAGMA index_info('{str(name)}')")
                ],
                "bytes": objects.get(str(name), {}).get("bytes", 0),
            }
        )
    indexes.sort(key=lambda item: item["name"])
    row_table_bytes = objects.get("derivation_rows", {}).get("bytes", 0)
    row_index_bytes = sum(int(item["bytes"]) for item in indexes)
    table_sql = str(
        connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='derivation_rows'"
        ).fetchone()[0]
    )
    return {
        "file_bytes": database.stat().st_size,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_pages": freelist,
        "dbstat_bytes": sum(int(item["bytes"]) for item in objects.values()),
        "derivation_rows": {
            "without_rowid": "WITHOUT ROWID" in table_sql.upper(),
            "table_bytes": row_table_bytes,
            "index_bytes": row_index_bytes,
            "total_bytes": row_table_bytes + row_index_bytes,
            "indexes": indexes,
        },
    }


def _savepoint_rejection(
    connection: sqlite3.Connection,
    name: str,
    sql: str,
    parameters: tuple[object, ...],
    expected_error: str,
) -> dict[str, Any]:
    connection.execute(f"SAVEPOINT {name}")
    rejected = False
    error = None
    error_type = None
    try:
        connection.execute(sql, parameters)
    except sqlite3.DatabaseError as caught:
        rejected = True
        error = str(caught)
        error_type = type(caught).__name__
    finally:
        connection.execute(f"ROLLBACK TO {name}")
        connection.execute(f"RELEASE {name}")
    return {
        "rejected": rejected,
        "error_type": error_type,
        "error": error,
        "expected_error": expected_error,
        "matches_expected": error_type == "IntegrityError" and error == expected_error,
    }


def constraint_probes(connection: sqlite3.Connection) -> dict[str, Any]:
    sample = connection.execute(
        "SELECT generation_id,ordinal,table_name,record_key,payload_json "
        "FROM derivation_rows ORDER BY generation_id,ordinal LIMIT 1"
    ).fetchone()
    require(sample is not None, "benchmark requires at least one derivation row")
    generation_id, ordinal, table_name, record_key, payload_json = sample
    next_ordinal = int(
        connection.execute(
            "SELECT max(ordinal)+1 FROM derivation_rows WHERE generation_id=?",
            (generation_id,),
        ).fetchone()[0]
    )
    result: dict[str, Any] = {
        "duplicate_ordinal": _savepoint_rejection(
            connection,
            "duplicate_ordinal",
            "INSERT INTO derivation_rows VALUES (?,?,?,?,?)",
            (generation_id, ordinal, "benchmark", "duplicate-ordinal", payload_json),
            "UNIQUE constraint failed: derivation_rows.generation_id, derivation_rows.ordinal",
        ),
        "duplicate_logical_key": _savepoint_rejection(
            connection,
            "duplicate_logical_key",
            "INSERT INTO derivation_rows VALUES (?,?,?,?,?)",
            (generation_id, next_ordinal, table_name, record_key, payload_json),
            "UNIQUE constraint failed: derivation_rows.generation_id, "
            "derivation_rows.table_name, derivation_rows.record_key",
        ),
        "update_immutable": _savepoint_rejection(
            connection,
            "update_immutable",
            "UPDATE derivation_rows SET payload_json=payload_json||' ' "
            "WHERE generation_id=? AND ordinal=?",
            (generation_id, ordinal),
            "derivation rows are immutable",
        ),
        "delete_immutable": _savepoint_rejection(
            connection,
            "delete_immutable",
            "DELETE FROM derivation_rows WHERE generation_id=? AND ordinal=?",
            (generation_id, ordinal),
            "derivation rows are immutable",
        ),
    }

    deferred_insert = False
    deferred_commit_rejected = False
    deferred_error = None
    connection.execute("BEGIN")
    try:
        try:
            connection.execute(
                "INSERT INTO derivation_rows VALUES (?,?,?,?,?)",
                (
                    "benchmark-missing-generation",
                    0,
                    "benchmark",
                    "deferred-fk",
                    payload_json,
                ),
            )
            deferred_insert = True
            connection.commit()
        except sqlite3.IntegrityError as caught:
            deferred_error = str(caught)
            deferred_commit_rejected = (
                deferred_insert and deferred_error == "FOREIGN KEY constraint failed"
            )
    finally:
        connection.rollback()
    result["deferred_foreign_key"] = {
        "insert_allowed_before_label": deferred_insert,
        "commit_rejected_without_label": deferred_commit_rejected,
        "error": deferred_error,
        "expected_error": "FOREIGN KEY constraint failed",
    }

    fence_table = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='history_dispatch_fence'"
    ).fetchone()
    fence_trigger = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='trigger' AND tbl_name='derivation_rows' "
        "AND upper(sql) LIKE '%AFTER INSERT%' AND sql LIKE '%history_dispatch_fence%'"
    ).fetchone()
    relevant = fence_table is not None and fence_trigger is not None
    fence_bumped = None
    if relevant:
        before = int(
            connection.execute(
                "SELECT revision FROM history_dispatch_fence WHERE singleton=1"
            ).fetchone()[0]
        )
        connection.execute("SAVEPOINT fence_probe")
        try:
            connection.execute(
                "INSERT INTO derivation_rows VALUES (?,?,?,?,?)",
                (
                    generation_id,
                    next_ordinal,
                    "benchmark",
                    "fence-probe-logical-key",
                    payload_json,
                ),
            )
            during = int(
                connection.execute(
                    "SELECT revision FROM history_dispatch_fence WHERE singleton=1"
                ).fetchone()[0]
            )
            fence_bumped = during == before + 1
        finally:
            connection.execute("ROLLBACK TO fence_probe")
            connection.execute("RELEASE fence_probe")
    result["history_fence"] = {"relevant": relevant, "insert_bumped_revision": fence_bumped}
    result["passed"] = all(
        bool(result[name]["matches_expected"])
        for name in (
            "duplicate_ordinal",
            "duplicate_logical_key",
            "update_immutable",
            "delete_immutable",
        )
    ) and (not relevant or fence_bumped is True)
    result["passed"] = bool(result["passed"]) and deferred_insert and deferred_commit_rejected
    return result


def query_parameters(connection: sqlite3.Connection) -> dict[str, tuple[object, ...]]:
    generation = connection.execute(
        "SELECT generation_id FROM derivation_generations ORDER BY row_count DESC,generation_id LIMIT 1"
    ).fetchone()
    require(generation is not None, "benchmark requires at least one generation")
    row = connection.execute(
        "SELECT generation_id,table_name,record_key FROM derivation_rows "
        "ORDER BY generation_id,ordinal LIMIT 1"
    ).fetchone()
    require(row is not None, "benchmark requires at least one derivation row")
    return {
        "generation": (generation[0],),
        "row": (row[0], row[1], row[2]),
        "none": (),
    }


def query_benchmark(connection: sqlite3.Connection, repeats: int) -> list[dict[str, Any]]:
    require(repeats > 0, "timing repeats must be positive")
    parameters = query_parameters(connection)
    results = []
    for name, sql, parameter_kind in QUERY_SPECS:
        params = parameters[parameter_kind]
        plan = [str(row[3]) for row in connection.execute("EXPLAIN QUERY PLAN " + sql, params)]
        timings = []
        returned = None
        for _ in range(repeats):
            started = time.perf_counter()
            count = sum(1 for _ in connection.execute(sql, params))
            timings.append(time.perf_counter() - started)
            returned = count
        results.append(
            {
                "name": name,
                "sql": sql,
                "plan": plan,
                "rows_returned": returned,
                "repeats": repeats,
                "seconds": timings,
                "best_seconds": min(timings),
            }
        )
    return results


def deterministic_gzip(database: Path, destination: Path) -> dict[str, Any]:
    started = time.perf_counter()
    source_identity_before = file_identity(database)
    source_digest = hashlib.sha256()
    with database.open("rb") as incoming, destination.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as zipped:
            while chunk := incoming.read(8 * 1024 * 1024):
                source_digest.update(chunk)
                zipped.write(chunk)
    source_identity_after = file_identity(database)
    return {
        "kind": "deterministic-gzip-whole-database-proxy",
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": file_sha256(destination),
        "seconds": time.perf_counter() - started,
        "gzip_mtime": 0,
        "compresslevel": 6,
        "source_database_sha256": source_digest.hexdigest(),
        "source_identity_before": source_identity_before,
        "source_identity_after": source_identity_after,
        "source_unchanged_while_compressing": source_identity_before == source_identity_after,
    }


def inspect_layout(database: Path, repeats: int) -> dict[str, Any]:
    # These are disposable benchmark copies. A normal connection is needed for
    # savepoint-wrapped rejection probes; every attempted change is rolled back.
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        return {
            "content": content_profile(connection),
            "integrity": integrity(connection),
            "row_table_metadata": row_table_metadata(connection),
            "storage": storage_profile(connection, database),
            "triggers": table_triggers(connection),
            "constraints": constraint_probes(connection),
            "queries": query_benchmark(connection, repeats),
        }


def gate_results(
    source: dict[str, Any],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    signature_before: dict[str, int | str],
    signature_after: dict[str, int | str],
    sidecars_before: dict[str, int | None],
    sidecars_after: dict[str, int | None],
) -> dict[str, Any]:
    def bytes_bound(layout: dict[str, Any]) -> bool:
        inspected = layout["inspected_database_signature"]
        compressed = layout["compressed_backup_proxy"]
        identity = {key: inspected[key] for key in ("bytes", "mtime_ns", "inode")}
        return bool(
            compressed["source_unchanged_while_compressing"]
            and compressed["source_database_sha256"] == inspected["sha256"]
            and compressed["source_identity_before"] == identity
            and compressed["source_identity_after"] == identity
        )

    def table_semantics_match(layout: dict[str, Any]) -> bool:
        source_table = source["row_table_metadata"]
        layout_table = layout["row_table_metadata"]
        return all(
            layout_table[key] == source_table[key] for key in ("columns", "foreign_keys", "indexes")
        )

    checks = {
        "source_signature_and_hash_unchanged": signature_before == signature_after,
        "source_sidecars_unchanged": sidecars_before == sidecars_after,
        "source_integrity": source["integrity"]["passed"],
        "source_output_labels_match_rows": source["content"]["output_label_mismatch_count"] == 0,
        "source_has_no_orphan_rows": source["content"]["rows_without_generation"] == 0,
        "baseline_content_matches_source": baseline["content"] == source["content"],
        "candidate_content_matches_source": candidate["content"] == source["content"],
        "baseline_integrity": baseline["integrity"]["passed"],
        "candidate_integrity": candidate["integrity"]["passed"],
        "baseline_constraints": baseline["constraints"]["passed"],
        "candidate_constraints": candidate["constraints"]["passed"],
        "baseline_table_definition_preserved": baseline["row_table_metadata"]
        == source["row_table_metadata"],
        "candidate_table_semantics_preserved": table_semantics_match(candidate),
        "baseline_inspection_bound_to_compressed_proxy": bytes_bound(baseline),
        "candidate_inspection_bound_to_compressed_proxy": bytes_bound(candidate),
        "triggers_preserved": baseline["triggers"] == source["triggers"]
        and candidate["triggers"] == source["triggers"],
        "baseline_is_rowid": not baseline["storage"]["derivation_rows"]["without_rowid"],
        "candidate_is_without_rowid": candidate["storage"]["derivation_rows"]["without_rowid"],
        "baseline_compact": baseline["storage"]["freelist_pages"] == 0,
        "candidate_compact": candidate["storage"]["freelist_pages"] == 0,
    }
    return {"passed": all(checks.values()), "checks": checks}


def build_receipt(report: dict[str, Any], command: list[str] | None) -> dict[str, Any]:
    layouts = {}
    for name in ("baseline", "without_rowid"):
        layout = report["layouts"][name]
        layouts[name] = {
            "database_bytes": layout["storage"]["file_bytes"],
            "database_sha256": layout["inspected_database_signature"]["sha256"],
            "derivation_rows_bytes": layout["storage"]["derivation_rows"]["total_bytes"],
            "compressed_proxy_bytes": layout["compressed_backup_proxy"]["bytes"],
            "compressed_proxy_sha256": layout["compressed_backup_proxy"]["sha256"],
            "row_stream_sha256": layout["content"]["row_stream_sha256"],
            "build": layout["build"],
        }
    return {
        "format": RECEIPT_FORMAT,
        "tool_version": TOOL_VERSION,
        "started_at": report["started_at"],
        "finished_at": report["finished_at"],
        "source": {
            "path": report["source"]["path"],
            "signature_before": report["source"]["signature_before"],
            "signature_after": report["source"]["signature_after"],
        },
        "runtime": report["runtime"],
        "layouts": layouts,
        "comparison": report["comparison"],
        "gates": report["gates"],
        "command": command,
        "production_operations": 0,
        "network_requests": 0,
        "limit": "gzip is a deterministic whole-database backup-size proxy, not a checkpoint",
    }


def run(
    source: Path,
    *,
    scratch: Path,
    output: Path,
    receipt: Path,
    timing_repeats: int = 3,
    command: list[str] | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(UTC).isoformat()
    runtime = runtime_metadata()
    source_root, database = resolve_source(source)
    scratch = resolve_new_path(scratch, "--scratch", source_root)
    output = resolve_new_path(output, "--output", source_root)
    receipt = resolve_new_path(receipt, "--receipt", source_root)
    require(output != receipt, "--output and --receipt must differ")
    require(output != scratch and receipt != scratch, "report paths must differ from --scratch")
    require(timing_repeats > 0, "timing repeats must be positive")

    sidecars_before = source_sidecars(database)
    signature_before = file_signature(database)
    with open_immutable(database) as connection:
        source_row_table = validate_source(connection)
        source_inspection = {
            "content": content_profile(connection),
            "integrity": integrity(connection),
            "row_table_metadata": source_row_table,
            "storage": storage_profile(connection, database),
            "triggers": table_triggers(connection),
        }

    scratch.mkdir(parents=True)
    baseline_path = scratch / "baseline.sqlite"
    candidate_path = scratch / "without-rowid.sqlite"

    baseline_copy_seconds = copy_database(database, baseline_path)
    baseline_build = compact_baseline(baseline_path)
    baseline_build["copy_seconds"] = baseline_copy_seconds
    baseline_build["total_seconds"] = baseline_copy_seconds + baseline_build["prepare_seconds"]

    candidate_copy_seconds = copy_database(database, candidate_path)
    candidate_build = rebuild_without_rowid(candidate_path)
    candidate_build["copy_seconds"] = candidate_copy_seconds
    candidate_build["total_seconds"] = candidate_copy_seconds + candidate_build["prepare_seconds"]

    baseline = inspect_layout(baseline_path, timing_repeats)
    candidate = inspect_layout(candidate_path, timing_repeats)
    baseline["build"] = baseline_build
    candidate["build"] = candidate_build
    baseline["path"] = str(baseline_path)
    candidate["path"] = str(candidate_path)
    baseline["inspected_database_signature"] = file_signature(baseline_path)
    candidate["inspected_database_signature"] = file_signature(candidate_path)
    baseline["compressed_backup_proxy"] = deterministic_gzip(
        baseline_path, scratch / "baseline.sqlite.gz"
    )
    candidate["compressed_backup_proxy"] = deterministic_gzip(
        candidate_path, scratch / "without-rowid.sqlite.gz"
    )

    signature_after = file_signature(database)
    sidecars_after = source_sidecars(database)
    gates = gate_results(
        source_inspection,
        baseline,
        candidate,
        signature_before,
        signature_after,
        sidecars_before,
        sidecars_after,
    )
    baseline_file = int(baseline["storage"]["file_bytes"])
    candidate_file = int(candidate["storage"]["file_bytes"])
    baseline_rows = int(baseline["storage"]["derivation_rows"]["total_bytes"])
    candidate_rows = int(candidate["storage"]["derivation_rows"]["total_bytes"])
    baseline_gzip = int(baseline["compressed_backup_proxy"]["bytes"])
    candidate_gzip = int(candidate["compressed_backup_proxy"]["bytes"])
    report = {
        "format": REPORT_FORMAT,
        "tool_version": TOOL_VERSION,
        "runtime": runtime,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "source": {
            "path": str(database),
            "root": str(source_root),
            "opened": "mode=ro&immutable=1",
            "signature_before": signature_before,
            "signature_after": signature_after,
            "sidecars_before": sidecars_before,
            "sidecars_after": sidecars_after,
            **source_inspection,
        },
        "scratch": str(scratch),
        "layouts": {"baseline": baseline, "without_rowid": candidate},
        "comparison": {
            "whole_database_bytes_delta": candidate_file - baseline_file,
            "whole_database_bytes_saved": baseline_file - candidate_file,
            "derivation_rows_bytes_delta": candidate_rows - baseline_rows,
            "derivation_rows_bytes_saved": baseline_rows - candidate_rows,
            "compressed_proxy_bytes_delta": candidate_gzip - baseline_gzip,
            "compressed_proxy_bytes_saved": baseline_gzip - candidate_gzip,
        },
        "gates": gates,
        "limits": [
            "The source must already be a closed disposable schema-29 copy.",
            "Timings are local warm-or-cold observations, not service levels.",
            "Deterministic gzip of the whole SQLite file is a backup-size proxy, not a checkpoint.",
            "This tool performs no production operation and makes no network request.",
        ],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(rendered(report))
    with receipt.open("x") as stream:
        stream.write(rendered(build_receipt(report, command)))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="schema-29 state dir or DB")
    parser.add_argument("--scratch", type=Path, required=True, help="new benchmark directory")
    parser.add_argument("--output", type=Path, required=True, help="new full JSON report")
    parser.add_argument("--receipt", type=Path, required=True, help="new compact JSON receipt")
    parser.add_argument("--timing-repeats", type=int, default=3)
    args = parser.parse_args()
    report = run(
        args.source,
        scratch=args.scratch,
        output=args.output,
        receipt=args.receipt,
        timing_repeats=args.timing_repeats,
        command=list(sys.argv),
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "receipt": str(args.receipt),
                "scratch": str(args.scratch),
                "passed": report["gates"]["passed"],
                "comparison": report["comparison"],
            },
            sort_keys=True,
        )
    )
    if not report["gates"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
