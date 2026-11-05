"""Filtered history COPY sets on one caller-pinned Postgres snapshot.

The caller owns state/backup locks, prepares this connection before its read
transaction, and captures mutable publication/hold files under B. This module
never starts transports, reservations, publications or independent snapshots.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pyarrow as pa
from psycopg import sql

from swingset.fetch.archive import Archive, canonical, durable_write

from . import history_catalog as history
from . import postgres_copy_codec, postgres_files
from . import postgres_schema as schema
from .postgres_import import Cell, _postgres_rows, _row_digest, _scalar

FORMAT = "swingset-postgres-history-v1"
MANIFEST = "history.json"
_METADATA = frozenset(
    {
        "baseline_candidate",
        "pending_candidates",
        "source_revision",
        "code_digest",
        "runtime_digest",
        "tool_identity",
        "input_bundle_hash",
    }
)


def prepare(conn: Any) -> None:
    history.prepare(conn)


def _hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            result.update(block)
    return result.hexdigest()


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _relative(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or "." in path.parts:
        raise ValueError("backup file has an unsafe relative path")
    return path


def _immutable_rows(conn: Any, table: str) -> Iterator[list[Cell]]:
    names = (*schema.semantic_columns(table), *(("rowid",) if table in schema.ROWID_TABLES else ()))
    for cells in _postgres_rows(conn, table, physical=True, predicate=history.where(table)):
        fields = history.immutable_fields(
            table, {name: cell.value for name, cell in zip(names, cells, strict=True)}
        )
        keep = set(fields) | ({"rowid"} if table in schema.ROWID_TABLES else set())
        yield [cell for name, cell in zip(names, cells, strict=True) if name in keep]


class _FileFlushes:
    """Bound local flushes; their descriptors remain valid after writers close."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="swingset-history-flush")
        self._pending: deque[Future[None]] = deque()

    @staticmethod
    def _flush(descriptor: int) -> None:
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def submit(self, descriptor: int) -> None:
        while len(self._pending) >= 8:
            self._pending.popleft().result()
        duplicate = os.dup(descriptor)
        try:
            self._pending.append(self._pool.submit(self._flush, duplicate))
        except BaseException:
            os.close(duplicate)
            raise

    def wait(self) -> None:
        while self._pending:
            self._pending.popleft().result()

    def close(self) -> None:
        self._pool.shutdown(wait=True)


def _copy_out(
    conn: Any, statement: str, path: Path, *, flush: Callable[[int], None] | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        pa.output_stream(str(path), compression="zstd") as output,
        conn.cursor().copy(statement) as source,
    ):
        for block in source:
            output.write(block)
    with path.open("rb") as handle:
        (flush or os.fsync)(handle.fileno())


def _copy_in(conn: Any, statement: str, path: Path) -> None:
    with (
        pa.input_stream(str(path), compression="zstd") as source,
        conn.cursor().copy(statement) as target,
    ):
        while block := source.read(1 << 16):
            target.write(block)


def _columns(table: str, *, physical: bool = True) -> tuple[str, ...]:
    return (
        *schema.semantic_columns(table),
        *(("rowid",) if physical and table in schema.ROWID_TABLES else ()),
    )


def _quoted_columns(columns: tuple[str, ...]) -> str:
    return ",".join('"' + column + '"' for column in columns)


def _retained_file(conn: Any, name: str) -> bool:
    path = _relative(name)
    if path.name == "database.json" or path.name == ".env":
        raise ValueError("destination credentials must never enter a history backup")
    if path.parts[0] in {"blobs", "extracts", "inputs", "holds", "history", "archive-cdx"}:
        return True
    if path.parts[0] == "objects":
        if len(path.parts) != 4:
            raise ValueError("object path has an unsupported layout")
        kind, digest = path.parts[1], path.name
        return (
            conn.execute(
                f"SELECT 1 FROM ops.objects o JOIN {history.marker('objects')} h USING(class,digest) WHERE o.class=%s AND o.digest=%s",
                (kind, digest),
            ).fetchone()
            is not None
        )
    if path.parts[0] == "candidates":
        return path.name in {"BUILT", "PUBLISHING", "PUBLISHED"} or path.parts[-2:] == (
            "_meta",
            "manifest.json",
        )
    if path.parts[0] == "partitions":
        if path.name == "sidecar.json":
            return True  # Retained immutable metadata declares historical generation roots.
        return len(path.parts) == 3 and path.parts[1] == "manifests" and path.suffix == ".json"
    if len(path.parts) == 1 and path.name in {
        "ALERTS.log",
        "operator-hold",
        "phase1-baseline.json",
        "phase1-catalog.json",
        "phase1-ledger.json",
    }:
        return True
    if path.as_posix() == "seed.json":
        return False
    if path.parts[0] in {
        "verification",
        "runs",
        "tmp",
        ".cache",
        "gc",
        "checkpoints",
    } or path.name in {
        "state.sqlite",
        "state.sqlite-wal",
        "state.sqlite-shm",
        "RESTORE_PENDING",
        "checkpoint.json",
        "baseline",
    }:
        return False
    from .rwlock import is_lock_file

    if is_lock_file(path.name):
        return False
    raise ValueError("state file has no history backup rule: " + name)


def _check_content(name: str, path: Path) -> None:
    parts = Path(name).parts
    if parts[0] == "blobs":
        Archive._validate_hash(parts[-1])
        Archive._verify_body(path, parts[-1])
    elif parts[0] == "extracts":
        Archive._validate_hash(parts[-1])
        if _hash(path) != parts[-1]:
            raise ValueError("backup extract content does not match its identity")
    elif parts[0] == "objects":
        import gzip

        Archive._validate_hash(parts[-1])
        hasher = hashlib.sha256()
        opener = open if parts[1] == "release_manifest" else gzip.open
        with opener(path, "rb") as stream:
            for block in iter(lambda: stream.read(1 << 16), b""):
                hasher.update(block)
        if hasher.hexdigest() != parts[-1]:
            raise ValueError("backup object content does not match its identity")
    elif parts[0] == "partitions" and parts[-1] == "sidecar.json":
        if _hash(path) != parts[-2]:
            raise ValueError("backup partition sidecar identity differs")


def _sequence_floors(conn: Any) -> list[dict[str, Any]]:
    result = []
    for table, specification in schema.reference_manifest()["tables"].items():
        columns = [
            column["name"]
            for column in specification["columns"]
            if column["type"] == "INTEGER" and column["pk"] and len(history.keys(table)) == 1
        ]
        if table in schema.ROWID_TABLES:
            columns.append("rowid")
        for column in columns:
            sequence = conn.execute(
                "SELECT pg_get_serial_sequence(%s,%s)", (schema.qualified(table), column)
            ).fetchone()[0]
            if sequence is None:
                continue
            last, called = conn.execute(
                sql.SQL("SELECT last_value,is_called FROM {}").format(sql.SQL(sequence))
            ).fetchone()
            maximum = int(
                conn.execute(
                    f'SELECT coalesce(max("{column}"),0) FROM {schema.qualified(table)}'
                ).fetchone()[0]
            )
            result.append(
                {
                    "table": table,
                    "column": column,
                    "floor": max(maximum, int(last) if called else 0),
                }
            )
    return result


def _rebuild_inventory(conn: Any) -> list[dict[str, Any]]:
    from .derivation_dependencies import retired
    from .work import WorkUnit

    result = []
    for stage, kind, identifier, generation, label, count, dependency in conn.execute(
        "SELECT s.stage,s.unit_kind,s.unit_id,g.generation_id,g.output_digest,g.row_count,g.dependency_set_id FROM derive.derivation_scopes s JOIN derive.derivation_generations g ON g.generation_id=s.materialized_generation_id WHERE s.stage IN ('project','link') ORDER BY s.stage,s.unit_kind,s.unit_id"
    ):
        if not retired(WorkUnit(str(stage), str(kind), str(identifier))):
            result.append(
                {
                    "stage": str(stage),
                    "unit_kind": str(kind),
                    "unit_id": str(identifier),
                    "source_generation_id": str(generation),
                    "output_digest": str(label),
                    "row_count": int(count),
                    "dependency_set_id": str(dependency),
                }
            )
    return result


def export(
    conn: Any,
    target: Path,
    *,
    files: Mapping[str, Path],
    runtime_identity: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the manifest last; root locking and remote transport stay with caller."""
    if not runtime_identity or target.exists():
        raise ValueError("backup needs a runtime identity and a fresh destination")
    if (
        conn.execute("SHOW transaction_isolation").fetchone()[0] != "repeatable read"
        or conn.execute("SHOW transaction_read_only").fetchone()[0] != "on"
    ):
        raise ValueError(
            "history export requires the caller's pinned REPEATABLE READ READ ONLY transaction"
        )
    if conn.execute("SELECT to_regclass('pg_temp._swingset_history_tokens')").fetchone()[0] is None:
        raise ValueError("call prepare(conn) before pinning the read-only snapshot")
    if set(metadata or {}) - _METADATA:
        raise ValueError("unknown publication/tool metadata field")
    started = time.monotonic()
    target.parent.mkdir(parents=True, exist_ok=True)
    work = target.with_name("." + target.name + ".building-" + uuid.uuid4().hex)
    work.mkdir()
    flushes = _FileFlushes()
    try:
        counts = history.select_history(conn, files=files)
        epoch = conn.execute(
            "SELECT epoch FROM plan.event_pressure_state WHERE singleton=1"
        ).fetchone()
        reset_anchors = {"event_pressure_epoch": int(epoch[0]) if epoch else 0}
        file_closure = postgres_files.verify(conn, files, metadata or {})
        manifest: dict[str, Any] = {
            "format": FORMAT,
            "schema_version": schema.SCHEMA_VERSION,
            "migration_checksum": schema.migration_checksum(),
            "catalog_checksum": history.checksum(),
            "runtime_identity": runtime_identity,
            "postgres_version": str(conn.execute("SHOW server_version_num").fetchone()[0]),
            "source_environment": list(
                conn.execute(
                    "SELECT environment,database_id FROM ops.environment_identity WHERE singleton=1"
                ).fetchone()
            ),
            "metadata": dict(metadata or {}),
            "tables": {},
            "files": {},
            "sequences": _sequence_floors(conn),
            "complete": False,
            "file_closure": file_closure,
            "reset_anchors": reset_anchors,
            "rebuild_units": _rebuild_inventory(conn),
        }
        for table in schema.reference_manifest()["tables"]:
            table_file = f"tables/{schema.namespace(table)}.{table}.copy.zst"
            key_file = f"keys/{schema.namespace(table)}.{table}.copy.zst"
            columns = _quoted_columns(_columns(table))
            key_columns = _quoted_columns(history.keys(table))
            order = ",".join(f'"{name}"::text COLLATE "C"' for name in history.keys(table))
            _copy_out(
                conn,
                f"COPY(SELECT {columns} FROM {schema.qualified(table)} WHERE {history.where(table)} ORDER BY {order}) TO STDOUT WITH(FORMAT binary)",
                work / table_file,
                flush=flushes.submit,
            )
            _copy_out(
                conn,
                f"COPY(SELECT {key_columns} FROM {schema.qualified(table)} WHERE {history.where(table)} ORDER BY {order}) TO STDOUT WITH(FORMAT binary)",
                work / key_file,
                flush=flushes.submit,
            )
            count, digest = _row_digest(
                _postgres_rows(conn, table, physical=True, predicate=history.where(table))
            )
            immutable_count, immutable_digest = _row_digest(_immutable_rows(conn, table))
            if count != counts[table] or immutable_count != count:
                raise RuntimeError("history selection changed within the pinned snapshot")
            rule = history.classification(table)
            manifest["tables"][table] = {
                "class": rule.kind,
                "history_rule": rule.history_rule,
                "reset_rule": rule.reset_rule,
                "columns": list(_columns(table)),
                "rows": count,
                "sha256": digest,
                "immutable_sha256": immutable_digest,
                "data": table_file,
                "data_sha256": _hash(work / table_file),
                "keys": key_file,
                "keys_sha256": _hash(work / key_file),
            }
        for name, source in sorted(files.items()):
            parts = Path(name).parts
            if (
                parts
                and parts[0] == "inputs"
                and (len(parts) < 3 or parts[1] not in file_closure["input_bundle_hashes"])
            ):
                continue
            if not _retained_file(conn, name):
                continue
            source = Path(source)
            _check_content(name, source)
            copied = work / "files" / _relative(name)
            copied.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as input_file, copied.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file, length=1 << 20)
                output_file.flush()
                flushes.submit(output_file.fileno())
            manifest["files"][name] = {"sha256": _hash(copied), "bytes": copied.stat().st_size}
        # Every data descriptor is durable before the manifest can name it.
        flushes.wait()
        # File flushes do not persist their directory entries. Commit children
        # before their parents, including every newly created retained subtree.
        directories = [work, *(path for path in work.rglob("*") if path.is_dir())]
        for path in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            _sync_directory(path)
        manifest.update(complete=True, seconds=round(time.monotonic() - started, 3))
        durable_write(work / MANIFEST, canonical(manifest))
        _sync_directory(work)
        os.replace(work, target)
        _sync_directory(target.parent)
        return {
            **manifest,
            "manifest_sha256": _hash(target / MANIFEST),
            "bytes": sum(path.stat().st_size for path in target.rglob("*") if path.is_file()),
        }
    except BaseException:
        flushes.close()
        shutil.rmtree(work, ignore_errors=True)
        raise
    finally:
        flushes.close()


def verify(
    source: Path,
    *,
    runtime_identity: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Check every copied byte before any destination database/file mutation."""
    manifest_path = source / MANIFEST
    if expected_manifest_sha256 is not None and _hash(manifest_path) != expected_manifest_sha256:
        raise ValueError("history backup manifest digest differs")
    manifest: dict[str, Any] = json.loads(manifest_path.read_text())
    if manifest.get("format") != FORMAT or manifest.get("complete") is not True:
        raise ValueError("history backup is incomplete or has an unknown format")
    if (
        manifest.get("schema_version") != schema.SCHEMA_VERSION
        or manifest.get("migration_checksum") != schema.migration_checksum()
        or manifest.get("catalog_checksum") != history.checksum()
    ):
        raise ValueError("restore requires this backup's retained schema, image and row rules")
    if runtime_identity is not None and manifest.get("runtime_identity") != runtime_identity:
        raise ValueError("restore runtime identity differs from the retained image")
    if set(manifest.get("tables", {})) != set(schema.reference_manifest()["tables"]):
        raise ValueError("history backup omits a table class/reset rule")
    inventory = manifest.get("rebuild_units")
    if not isinstance(inventory, list):
        raise ValueError("history backup has no materialization rebuild inventory")
    seen = set()
    for unit in inventory:
        key = tuple(unit.get(name) for name in ("stage", "unit_kind", "unit_id"))
        if (
            key in seen
            or key[0] not in {"project", "link"}
            or not all(isinstance(value, str) for value in key)
            or not isinstance(unit.get("output_digest"), str)
            or type(unit.get("row_count")) is not int
            or unit["row_count"] < 0
        ):
            raise ValueError("history backup materialization inventory is invalid")
        seen.add(key)
    anchors = manifest.get("reset_anchors", {})
    epoch = anchors.get("event_pressure_epoch")
    if (
        set(anchors) != {"event_pressure_epoch"}
        or type(epoch) is not int
        or not -(1 << 63) <= epoch < (1 << 63) - 1
    ):
        raise ValueError("history backup operational epoch floor is absent or invalid")
    expected_paths = {MANIFEST}
    decoded_files = postgres_files.OfflineClosure()
    for table, entry in manifest["tables"].items():
        if entry.get("columns") != list(_columns(table)):
            raise ValueError("backup column order differs from the retained schema")
        for field in ("data", "keys"):
            name = str(entry[field])
            path = source / _relative(name)
            if (
                not path.resolve().is_relative_to(source.resolve())
                or _hash(path) != entry[field + "_sha256"]
            ):
                raise ValueError("a history table/key stream digest differs")
            expected_paths.add(name)
        columns = [
            dict(column) for column in schema.reference_manifest()["tables"][table]["columns"]
        ]
        if table in schema.ROWID_TABLES:
            columns.append({"name": "rowid", "type": "INTEGER", "pk": 0, "notnull": 1})
        key_names = history.keys(table)
        key_types = {column["name"]: column["type"] for column in columns}
        key_checksum = hashlib.sha256()

        def decoded_row(
            row: dict[str, Any],
            table: str = table,
            key_names: tuple[str, ...] = key_names,
            key_types: dict[str, Any] = key_types,
            key_checksum: Any = key_checksum,
        ) -> None:
            decoded_files.row(table, row)
            key_checksum.update(struct.pack(">Q", len(key_names)))
            for name in key_names:
                cell = _scalar(row[name], key_types[name], table + "." + name)
                key_checksum.update(cell.tag + struct.pack(">Q", cell.length))
                for block in cell.blocks():
                    key_checksum.update(block)

        def decoded_text(column: str, blocks: Iterable[bytes], table: str = table) -> None:
            decoded_files.text(table, column, blocks)

        count, digest = postgres_copy_codec.audit(
            source / entry["data"],
            columns,
            collect=decoded_files.COLUMNS.get(table, frozenset()) | frozenset(key_names),
            on_row=decoded_row,
            on_text=decoded_text,
        )
        if count != entry["rows"] or digest != entry["sha256"]:
            raise ValueError("binary COPY typed rows differ from the retained table audit")
        key_columns = [
            next(column for column in columns if column["name"] == name)
            for name in history.keys(table)
        ]
        key_count, key_digest = postgres_copy_codec.audit(source / entry["keys"], key_columns)
        if key_count != count or key_digest != key_checksum.hexdigest():
            raise ValueError("binary COPY keys differ from their decoded table rows")
    for name, entry in manifest["files"].items():
        path = source / "files" / _relative(name)
        if (
            not path.resolve().is_relative_to(source.resolve())
            or path.stat().st_size != entry["bytes"]
            or _hash(path) != entry["sha256"]
        ):
            raise ValueError("a history file digest differs")
        _check_content(name, path)
        expected_paths.add("files/" + name)
    actual_paths = {
        path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()
    }
    if actual_paths != expected_paths:
        raise ValueError("history backup file closure differs")
    decoded_files.verify(source, manifest)
    return manifest


def _load_keys(conn: Any, source: Path, manifest: Mapping[str, Any]) -> None:
    prepare(conn)
    conn.execute("DELETE FROM pg_temp._swingset_history_tokens")
    for table, entry in manifest["tables"].items():
        conn.execute(f"DELETE FROM {history.marker(table)}")
        _copy_in(
            conn,
            f"COPY {history.marker(table)}({_quoted_columns(history.keys(table))}) FROM STDIN WITH(FORMAT binary)",
            source / entry["keys"],
        )


def _copied_reference_rows(
    source: Path, manifest: Mapping[str, Any], table: str
) -> list[dict[str, Any]]:
    columns = [dict(column) for column in schema.reference_manifest()["tables"][table]["columns"]]
    result: list[dict[str, Any]] = []
    postgres_copy_codec.audit(
        source / manifest["tables"][table]["data"],
        columns,
        collect=frozenset(column["name"] for column in columns),
        on_row=result.append,
    )
    return result


def _control_revision(conn: Any, source: Path, manifest: Mapping[str, Any]) -> tuple[int, int]:
    old = _copied_reference_rows(source, manifest, "control_state")
    baseline = int(old[0]["revision"]) if old else 0
    found = conn.execute("SELECT revision FROM ops.control_state WHERE singleton=1").fetchone()
    current = int(found[0]) if found else 0
    if current < baseline:
        raise RuntimeError("restored control revision moved backwards")
    if current > baseline:
        first, last, distinct_count, total_count, invalid = conn.execute(
            "SELECT min(e.control_revision) AS first_revision,max(e.control_revision) AS last_revision,count(DISTINCT e.control_revision) AS distinct_revisions,count(*) AS event_count,count(*) FILTER(WHERE btrim(e.actor)='' OR btrim(e.reason)='' OR e.action NOT IN ('pause','resume','expire')) AS invalid_events FROM ops.control_events e WHERE e.control_revision>%s AND e.control_revision<=%s AND NOT "
            + history.where("control_events", "e"),
            (baseline, current),
        ).fetchone()
        if (first, last, distinct_count, total_count, invalid) != (
            baseline + 1,
            current,
            current - baseline,
            current - baseline,
            0,
        ):
            raise RuntimeError("restored control revision has no complete immutable event chain")
    return baseline, current


def _paused_history(
    conn: Any,
    source: Path,
    manifest: Mapping[str, Any],
    baseline: int,
    current: int,
    transitions: list[dict[str, Any]],
) -> Iterator[list[Cell]]:
    table = "operator_pauses"
    columns = schema.reference_manifest()["tables"][table]["columns"]
    names = tuple(column["name"] for column in columns)
    for original in _copied_reference_rows(source, manifest, table):
        found = conn.execute(
            "SELECT "
            + _quoted_columns(names)
            + " FROM ops.operator_pauses WHERE scope_kind=%s AND scope_id=%s",
            (original["scope_kind"], original["scope_id"]),
        ).fetchone()
        if found is None:
            event = conn.execute(
                "SELECT e.event_id,e.control_revision,e.action,e.actor,e.reason,e.occurred_at FROM ops.control_events e WHERE e.pause_id IS NOT DISTINCT FROM %s AND e.scope_kind=%s AND e.scope_id=%s AND e.action IN ('resume','expire') AND e.control_revision>%s AND e.control_revision<=%s AND NOT "
                + history.where("control_events", "e")
                + " ORDER BY e.control_revision LIMIT 1",
                (
                    original["pause_id"],
                    original["scope_kind"],
                    original["scope_id"],
                    baseline,
                    current,
                ),
            ).fetchone()
            if event is None or not str(event[3]).strip() or not str(event[4]).strip():
                raise RuntimeError(
                    "a restored pause disappeared without an authorized control event"
                )
            transitions.append(
                {
                    "pause_id": original["pause_id"],
                    "scope_kind": original["scope_kind"],
                    "scope_id": original["scope_id"],
                    "event_id": int(event[0]),
                    "control_revision": int(event[1]),
                    "action": str(event[2]),
                    "event_sha256": hashlib.sha256(canonical(tuple(event))).hexdigest(),
                }
            )
            values = [original[name] for name in names]
        else:
            values = list(found)
        yield [
            _scalar(value, column["type"], table + "." + column["name"])
            for value, column in zip(values, columns, strict=True)
        ]


def _prepare_object_clocks(conn: Any, source: Path, manifest: Mapping[str, Any]) -> None:
    """Load the original storage clocks before the read-only audit snapshot."""
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _swingset_original_objects (LIKE ops.objects) ON COMMIT PRESERVE ROWS"
    )
    conn.execute("TRUNCATE pg_temp._swingset_original_objects")
    _copy_in(
        conn,
        "COPY pg_temp._swingset_original_objects("
        + _quoted_columns(_columns("objects"))
        + ") FROM STDIN WITH(FORMAT binary)",
        source / manifest["tables"]["objects"]["data"],
    )


def _check_object_clocks(conn: Any) -> dict[str, int]:
    checked, advanced, unknown, invalid = conn.execute(
        'SELECT count(*) AS checked,count(*) FILTER(WHERE live.stored_at COLLATE "C">original.stored_at COLLATE "C") AS advanced,count(*) FILTER(WHERE original.stored_at IS NULL) AS unknown,count(*) FILTER(WHERE (original.stored_at IS NULL AND live.stored_at IS NOT NULL) OR (original.stored_at IS NOT NULL AND (live.stored_at IS NULL OR live.stored_at COLLATE "C"<original.stored_at COLLATE "C"))) AS invalid FROM pg_temp._swingset_original_objects original JOIN ops.objects live USING(class,digest)'
    ).fetchone()
    if invalid:
        raise RuntimeError("restored object GC clock moved backwards or changed its unknown state")
    return {"checked": int(checked), "advanced": int(advanced), "unknown": int(unknown)}


def audit(conn: Any, source: Path, *, after_rebuild: bool = False) -> dict[str, Any]:
    """Exact history before workers; immutable old fields/keys after rebuilding."""
    manifest = verify(source)
    _load_keys(conn, source, manifest)
    if after_rebuild:
        _prepare_object_clocks(conn, source, manifest)
    results = {}
    transitions: list[dict[str, Any]] = []
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        baseline, current = _control_revision(conn, source, manifest) if after_rebuild else (0, 0)
        object_clocks = _check_object_clocks(conn) if after_rebuild else None
        for table, entry in manifest["tables"].items():
            rows = (
                _paused_history(conn, source, manifest, baseline, current, transitions)
                if after_rebuild and table == "operator_pauses"
                else _immutable_rows(conn, table)
                if after_rebuild
                else _postgres_rows(conn, table, physical=True, predicate=history.where(table))
            )
            count, digest = _row_digest(rows)
            wanted = entry["immutable_sha256"] if after_rebuild else entry["sha256"]
            if count != entry["rows"] or digest != wanted:
                raise RuntimeError(f"restored history differs: {table}")
            results[table] = {"rows": count, "sha256": digest}
        for table in manifest["tables"]:
            history._json_rows(conn, table, after_rebuild=after_rebuild)
        history.assert_closed(conn, after_rebuild=after_rebuild)
    return {
        "equal": True,
        "after_rebuild": after_rebuild,
        "tables": results,
        "authorized_control_transitions": transitions,
        "object_clocks": object_clocks,
    }


_RESET = {
    "event_blocker_cursor": ((1, 0, 0, 0),),
    "event_pressure_state": ((1, 0, 0),),
    "event_progress_cursor": ((1, 0, 0),),
    "event_timing_cursor": ((1, 0, 0),),
    "requirement_scan": ((1, "", None, None, None, 0),),
    "revisions": tuple(
        (name, 0)
        for name in (
            "observations",
            "source_event_map",
            "source_events",
            "canonical",
            "dancers",
            "links",
            "findings",
            "snapshots",
            "identity_decisions",
        )
    ),
}


def restore(
    source: Path,
    conn: Any,
    *,
    environment: str = "dev",
    runtime_identity: str,
    files_target: Path | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Stage a fresh database and validate every history field before grants."""
    manifest = verify(
        source, runtime_identity=runtime_identity, expected_manifest_sha256=expected_manifest_sha256
    )
    from .postgres_bootstrap import initialize_tables

    phases = schema.migration_phases()
    with conn.transaction():
        try:
            initialize_tables(conn)
        except RuntimeError as exc:
            raise ValueError(
                "history restore requires a fresh or prepared destination database: " + str(exc)
            ) from exc
    for table, entry in manifest["tables"].items():
        with conn.transaction():
            _copy_in(
                conn,
                f"COPY {schema.qualified(table)}({_quoted_columns(_columns(table))}) FROM STDIN WITH(FORMAT binary)",
                source / entry["data"],
            )
    with conn.transaction():
        for table, rows in _RESET.items():
            fields = _quoted_columns(schema.semantic_columns(table))
            placeholders = ",".join("%s" for _ in schema.semantic_columns(table))
            for row in rows:
                conn.execute(
                    f"INSERT INTO {schema.qualified(table)}({fields}) VALUES({placeholders}) ON CONFLICT DO NOTHING",
                    row,
                )
        conn.execute(
            "UPDATE plan.event_pressure_state SET epoch=%s,sequence=0 WHERE singleton=1",
            (manifest["reset_anchors"]["event_pressure_epoch"],),
        )
        conn.execute(
            'INSERT INTO plan.event_pressure_dirty(watch_id) SELECT watch_id FROM "fetch".watches ON CONFLICT DO NOTHING'
        )
        for entry in manifest["sequences"]:
            if entry["table"] not in schema.reference_manifest()["tables"] or entry[
                "column"
            ] not in _columns(entry["table"]):
                raise ValueError("backup names an unknown identity sequence")
            maximum = int(
                conn.execute(
                    f'SELECT coalesce(max("{entry["column"]}"),0) FROM {schema.qualified(entry["table"])}'
                ).fetchone()[0]
            )
            floor = max(maximum, int(entry["floor"]))
            conn.execute(
                "SELECT setval(pg_get_serial_sequence(%s,%s),%s,%s)",
                (schema.qualified(entry["table"]), entry["column"], max(1, floor), floor > 0),
            )
        conn.execute(phases["constraints"], prepare=False)
        conn.execute(phases["triggers"], prepare=False)
        if phases["forward"]:
            conn.execute(phases["forward"], prepare=False)
        identity = schema.seed_environment(conn, environment)
        schema.stamp_schema(conn)
        schema.verify(conn)
    exact = audit(conn, source)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        postgres_files.verify(
            conn,
            {name: source / "files" / name for name in manifest["files"]},
            manifest["metadata"],
        )
    copied = 0
    if files_target is not None:
        for name, entry in manifest["files"].items():
            destination = files_target / _relative(name)
            if destination.exists() and (
                not destination.is_file() or _hash(destination) != entry["sha256"]
            ):
                raise ValueError("history restore would overwrite unrelated destination files")
        for name in manifest["files"]:
            destination = files_target / _relative(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                staged = destination.with_name(
                    "." + destination.name + ".restore-" + uuid.uuid4().hex
                )
                try:
                    with (
                        (source / "files" / name).open("rb") as input_file,
                        staged.open("xb") as output_file,
                    ):
                        shutil.copyfileobj(input_file, output_file, length=1 << 20)
                        output_file.flush()
                        os.fsync(output_file.fileno())
                    os.replace(staged, destination)
                finally:
                    staged.unlink(missing_ok=True)
            copied += 1
        baseline = manifest["metadata"].get("baseline_candidate")
        if baseline is not None:
            if Path(baseline).name != baseline or baseline in {".", ".."}:
                raise ValueError("backup baseline candidate identity is unsafe")
            required = files_target / "candidates" / baseline / "PUBLISHED"
            if not required.is_file():
                raise ValueError("backup baseline publication receipt is missing")
            os.symlink(Path("candidates") / baseline, files_target / "baseline")
    with conn.transaction():
        schema.grant_roles(conn)
    # Native ANALYZE uses per-table transactions outside an explicit block.
    # Committed catalog ACLs cannot deadlock with automatic statistics updates.
    conn.execute("ANALYZE")
    return {
        "complete": True,
        "audit": exact,
        "database_id": identity,
        "environment": environment,
        "files": copied,
        "runtime_identity": runtime_identity,
    }
