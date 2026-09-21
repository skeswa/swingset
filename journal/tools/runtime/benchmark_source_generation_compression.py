"""Benchmark compressed source-generation documents on full database copies.

The source must be a closed, disposable schema-29 database outside
``/var/lib/swingset``. It is opened only with ``mode=ro&immutable=1`` and is
never mutated. The tool creates two new full copies: a compact baseline and a
candidate whose four JSON documents are independent zlib-6 streams in a
one-to-one ``WITHOUT ROWID`` side table. This is a research layout, not a
production migration.
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
import threading
import time
import zlib
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOL_VERSION = "benchmark-source-generation-compression-v2"
REPORT_FORMAT = "source-generation-compression-benchmark-v1"
RECEIPT_FORMAT = "source-generation-compression-benchmark-receipt-v1"
EXPECTED_SCHEMA_VERSION = 29
PROTECTED_ROOTS = ("/var/lib/swingset",)
DOCUMENT_NAMES = ("manifest", "recipe", "result", "report")
DOCUMENT_COLUMNS = tuple(f"{name}_json" for name in DOCUMENT_NAMES)
SOURCE_COLUMNS = (
    "generation_id",
    "unit_key",
    "page_kind",
    "contract_version",
    "input_fingerprint",
    *DOCUMENT_COLUMNS,
    "previous_generation_id",
    "work_token",
    "created_at",
    "run_id",
    "state",
    "removal_authority",
)
MAX_MISMATCH_EXAMPLES = 20

SOURCE_TABLE = """
CREATE TABLE source_generations (
    generation_id TEXT PRIMARY KEY,
    unit_key TEXT NOT NULL REFERENCES source_units(unit_key),
    page_kind TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    recipe_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    report_json TEXT NOT NULL,
    previous_generation_id TEXT REFERENCES source_generations(generation_id),
    work_token TEXT,
    created_at TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    state TEXT NOT NULL CHECK(state IN ('staged','waiting_for_inputs','needs_review','accepted','superseded','revoked')),
    removal_authority TEXT NOT NULL CHECK(removal_authority IN ('none','watch'))
)
"""

CANDIDATE_TABLE = """
CREATE TABLE benchmark_source_generations (
    generation_id TEXT PRIMARY KEY REFERENCES source_generation_documents(generation_id)
        DEFERRABLE INITIALLY DEFERRED,
    unit_key TEXT NOT NULL REFERENCES source_units(unit_key),
    page_kind TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    recipe_bytes INTEGER NOT NULL CHECK(recipe_bytes>=0),
    recipe_valid INTEGER NOT NULL CHECK(recipe_valid IN (0,1)),
    recipe_snapshot_id TEXT,
    recipe_source TEXT,
    previous_generation_id TEXT REFERENCES benchmark_source_generations(generation_id),
    work_token TEXT,
    created_at TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    state TEXT NOT NULL CHECK(state IN ('staged','waiting_for_inputs','needs_review','accepted','superseded','revoked')),
    removal_authority TEXT NOT NULL CHECK(removal_authority IN ('none','watch'))
)
"""

DOCUMENT_TABLE = """
CREATE TABLE source_generation_documents (
    generation_id TEXT PRIMARY KEY REFERENCES benchmark_source_generations(generation_id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    manifest_zlib BLOB NOT NULL,
    manifest_bytes INTEGER NOT NULL CHECK(manifest_bytes>=0),
    manifest_sha256 BLOB NOT NULL CHECK(length(manifest_sha256)=32),
    recipe_zlib BLOB NOT NULL,
    recipe_bytes INTEGER NOT NULL CHECK(recipe_bytes>=0),
    recipe_sha256 BLOB NOT NULL CHECK(length(recipe_sha256)=32),
    result_zlib BLOB NOT NULL,
    result_bytes INTEGER NOT NULL CHECK(result_bytes>=0),
    result_sha256 BLOB NOT NULL CHECK(length(result_sha256)=32),
    report_zlib BLOB NOT NULL,
    report_bytes INTEGER NOT NULL CHECK(report_bytes>=0),
    report_sha256 BLOB NOT NULL CHECK(length(report_sha256)=32)
) WITHOUT ROWID
"""

EXPECTED_INDEX_SQL = {
    "source_generations_unit_idx": (
        "CREATE INDEX source_generations_unit_idx ON "
        "source_generations(unit_key,created_at,generation_id)"
    ),
    "source_generations_previous_idx": (
        "CREATE INDEX source_generations_previous_idx ON source_generations(previous_generation_id)"
    ),
    "source_generations_run_idx": (
        "CREATE INDEX source_generations_run_idx ON source_generations(run_id)"
    ),
    "source_generations_state_idx": (
        "CREATE INDEX source_generations_state_idx ON source_generations(state,page_kind)"
    ),
    "source_generations_retained_source": """
        CREATE INDEX source_generations_retained_source ON source_generations(
            CASE WHEN json_valid(recipe_json) THEN
                CASE WHEN json_type(recipe_json,'$.context.source')='text'
                     THEN json_extract(recipe_json,'$.context.source') END
            END
        )
    """,
}

IMMUTABLE_TRIGGER = """
CREATE TRIGGER source_generation_immutable BEFORE UPDATE OF
unit_key,page_kind,contract_version,input_fingerprint,recipe_bytes,recipe_valid,
recipe_snapshot_id,recipe_source,previous_generation_id,work_token,created_at,
run_id,removal_authority ON source_generations
BEGIN SELECT RAISE(ABORT,'source generation evidence is immutable'); END
"""

DOCUMENT_IMMUTABLE_TRIGGER = """
CREATE TRIGGER source_generation_documents_immutable
BEFORE UPDATE ON source_generation_documents
BEGIN SELECT RAISE(ABORT,'source generation documents are immutable'); END
"""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def rendered(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_identity(path: Path) -> dict[str, int]:
    status = path.stat()
    return {"bytes": status.st_size, "mtime_ns": status.st_mtime_ns, "inode": status.st_ino}


def file_signature(path: Path) -> dict[str, int | str]:
    return {**file_identity(path), "sha256": file_sha256(path)}


def compress_document(raw: bytes) -> bytes:
    """Return one deterministic zlib level-6 stream."""
    return zlib.compress(raw, level=6)


def decode_document(blob: bytes, *, expected_bytes: int, max_bytes: int) -> bytes:
    """Decode one stream with stable failures and a hard output bound."""
    if expected_bytes < 0 or max_bytes < 0:
        raise ValueError("document byte limits must be nonnegative")
    if expected_bytes > max_bytes:
        raise ValueError("document exceeds decompressed byte cap")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(blob, max_bytes + 1)
        if len(raw) > max_bytes or decoder.unconsumed_tail:
            raise ValueError("document exceeds decompressed byte cap")
        raw += decoder.flush(max_bytes + 1 - len(raw))
    except zlib.error as error:
        raise ValueError("invalid compressed document") from error
    if len(raw) > max_bytes:
        raise ValueError("document exceeds decompressed byte cap")
    if not decoder.eof or decoder.unused_data:
        raise ValueError("invalid compressed document")
    if len(raw) != expected_bytes:
        raise ValueError("decompressed document length mismatch")
    return raw


def runtime_metadata() -> dict[str, Any]:
    tool = Path(__file__).resolve(strict=True)
    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python_version": sys.version,
        "sqlite_version": sqlite3.sqlite_version,
        "zlib_compile_version": zlib.ZLIB_VERSION,
        "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION,
        "tool_path": str(tool),
        "tool_sha256": file_sha256(tool),
    }


def _protected(path: Path) -> str | None:
    for name in PROTECTED_ROOTS:
        if path.is_relative_to(Path(name).resolve(strict=False)):
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
    require(protected is None, f"refusing {source_root}: {protected} is the live state")
    return source_root, database


def resolve_new_path(path: Path, label: str, source_root: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    protected = _protected(resolved)
    require(protected is None, f"refusing to write {label} under live root {protected}")
    require(not resolved.is_relative_to(source_root), f"{label} must be outside the source")
    require(not source_root.is_relative_to(resolved), f"{label} must not contain the source")
    require(not resolved.exists(), f"{label} must be new; {resolved} already exists")
    return resolved


def sidecar_state(database: Path) -> dict[str, int | None]:
    return {
        suffix[1:]: (sidecar.stat().st_size if sidecar.exists() else None)
        for suffix in ("-wal", "-shm", "-journal")
        for sidecar in (Path(str(database) + suffix),)
    }


def require_closed_sidecars(database: Path, phase: str) -> dict[str, int | None]:
    state = sidecar_state(database)
    require(state["wal"] is None, f"source has a write-ahead log {phase}")
    require(state["shm"] is None, f"source has a shared-memory file {phase}")
    require(state["journal"] is None, f"source has a rollback journal {phase}")
    return state


def open_immutable(database: Path) -> sqlite3.Connection:
    require_closed_sidecars(database, "before opening")
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _normalized_sql(sql: str) -> str:
    return "".join(sql.split()).casefold().removesuffix(";")


def _table_metadata(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)
    ).fetchone()
    require(row is not None, f"{table} table is required")
    return {
        "sql": str(row[0]),
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
            for item in connection.execute(f"PRAGMA table_xinfo('{table}')")
        ],
        "foreign_keys": sorted(
            [
                {
                    "table": str(item[2]),
                    "from": str(item[3]),
                    "to": str(item[4]),
                    "on_update": str(item[5]),
                    "on_delete": str(item[6]),
                    "match": str(item[7]),
                }
                for item in connection.execute(f"PRAGMA foreign_key_list('{table}')")
            ],
            key=lambda item: (item["from"], item["table"]),
        ),
    }


def _index_sql(connection: sqlite3.Connection, table: str) -> dict[str, str]:
    return {
        str(name): str(sql)
        for name, sql in connection.execute(
            "SELECT name,sql FROM sqlite_schema WHERE type='index' AND tbl_name=? "
            "AND sql IS NOT NULL ORDER BY name",
            (table,),
        )
    }


def _table_triggers(connection: sqlite3.Connection, table: str) -> list[dict[str, str]]:
    return [
        {"name": str(name), "sql": str(sql)}
        for name, sql in connection.execute(
            "SELECT name,sql FROM sqlite_schema WHERE type='trigger' AND tbl_name=? ORDER BY name",
            (table,),
        )
    ]


def _external_dependents(connection: sqlite3.Connection) -> list[dict[str, str]]:
    """Return triggers and views outside the anchor that query the anchor."""
    return [
        {"type": str(kind), "name": str(name), "table": str(table), "sql": str(sql)}
        for kind, name, table, sql in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema "
            "WHERE type IN ('trigger','view') AND tbl_name<>'source_generations' "
            "AND sql IS NOT NULL AND sql LIKE '%source_generations%' "
            "ORDER BY CASE type WHEN 'view' THEN 0 ELSE 1 END,name"
        )
    ]


def validate_source(connection: sqlite3.Connection) -> dict[str, Any]:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    require(version == EXPECTED_SCHEMA_VERSION, f"expected schema 29, found schema {version}")
    metadata = _table_metadata(connection, "source_generations")
    require(
        _normalized_sql(metadata["sql"]) == _normalized_sql(SOURCE_TABLE),
        "source_generations definition differs from canonical schema 29",
    )
    require(
        [column["name"] for column in metadata["columns"]] == list(SOURCE_COLUMNS),
        "unexpected source_generations columns",
    )
    indexes = _index_sql(connection, "source_generations")
    require(set(indexes) == set(EXPECTED_INDEX_SQL), "source_generations indexes drifted")
    for name, expected in EXPECTED_INDEX_SQL.items():
        require(
            _normalized_sql(indexes[name]) == _normalized_sql(expected),
            f"{name} definition drifted",
        )
    triggers = _table_triggers(connection, "source_generations")
    immutable = [item for item in triggers if item["name"] == "source_generation_immutable"]
    require(len(immutable) == 1, "source_generation_immutable trigger is required")
    for trigger in triggers:
        if trigger["name"] != "source_generation_immutable":
            require(
                not any(column in trigger["sql"].casefold() for column in DOCUMENT_COLUMNS),
                f"unsupported document dependency in trigger {trigger['name']}",
            )
    unexpected_dependencies = [
        (str(kind), str(name), str(table))
        for kind, name, table, sql in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL "
            "AND sql LIKE '%source_generations%' "
            "AND (sql LIKE '%manifest_json%' OR sql LIKE '%recipe_json%' "
            "OR sql LIKE '%result_json%' OR sql LIKE '%report_json%')"
        )
        if not (
            (kind == "table" and name == "source_generations")
            or (kind == "index" and name == "source_generations_retained_source")
            or (kind == "trigger" and name == "source_generation_immutable")
        )
    ]
    require(
        not unexpected_dependencies,
        f"unsupported source document schema dependencies: {unexpected_dependencies}",
    )
    return {
        "version": version,
        "table": metadata,
        "indexes": indexes,
        "triggers": triggers,
        "external_dependents": _external_dependents(connection),
    }


def copy_database(source: Path, destination: Path) -> float:
    started = time.perf_counter()
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=8 * 1024 * 1024)
    return time.perf_counter() - started


def _prepare_writable(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")


class ScratchMeter:
    """Sample all files under scratch, including transient journals and VACUUM files."""

    def __init__(self, root: Path, interval: float = 0.05) -> None:
        self.root = root
        self.interval = interval
        self.peak_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)

    def _sample(self) -> None:
        total = 0
        for path in self.root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass
        self.peak_bytes = max(self.peak_bytes, total)

    def _sample_loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._sample()

    def __enter__(self) -> ScratchMeter:
        self._sample()
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()


def compact_baseline(database: Path) -> dict[str, float]:
    started = time.perf_counter()
    with sqlite3.connect(database) as connection:
        _prepare_writable(connection)
        connection.execute("ANALYZE source_generations")
        vacuum_started = time.perf_counter()
        connection.execute("VACUUM")
        vacuum_seconds = time.perf_counter() - vacuum_started
    return {"prepare_seconds": time.perf_counter() - started, "vacuum_seconds": vacuum_seconds}


def _recipe_fields(connection: sqlite3.Connection, recipe: str) -> tuple[int, int, Any, str | None]:
    row = connection.execute(
        "SELECT length(CAST(? AS BLOB)),json_valid(?),"
        "CASE WHEN json_valid(?) THEN json_extract(?,'$.context.snapshot_id') END,"
        "CASE WHEN json_valid(?) AND json_type(?,'$.context.source')='text' "
        "THEN json_extract(?,'$.context.source') END",
        (recipe, recipe, recipe, recipe, recipe, recipe, recipe),
    ).fetchone()
    assert row is not None
    require(row[2] is None or isinstance(row[2], str), "recipe snapshot_id is not text or null")
    require(row[3] is None or isinstance(row[3], str), "recipe source is not text or null")
    return int(row[0]), int(row[1]), row[2], row[3]


def _document_values(values: Iterable[str]) -> tuple[Any, ...]:
    result: list[Any] = []
    for value in values:
        raw = value.encode()
        result.extend((compress_document(raw), len(raw), hashlib.sha256(raw).digest()))
    return tuple(result)


def rebuild_candidate(database: Path) -> dict[str, Any]:
    started = time.perf_counter()
    rows = 0
    raw_bytes = 0
    compressed_bytes = 0
    with sqlite3.connect(database) as connection:
        _prepare_writable(connection)
        source = validate_source(connection)
        kept_triggers = [
            item for item in source["triggers"] if item["name"] != "source_generation_immutable"
        ]
        kept_indexes = {
            name: sql
            for name, sql in source["indexes"].items()
            if name != "source_generations_retained_source"
        }
        external_dependents = source["external_dependents"]
        connection.execute("PRAGMA foreign_keys=OFF")
        rebuild_started = time.perf_counter()
        connection.execute("BEGIN IMMEDIATE")
        try:
            for dependent in reversed(external_dependents):
                kind = dependent["type"]
                require(kind in {"trigger", "view"}, "unsupported external dependent kind")
                connection.execute(f'DROP {kind.upper()} "{dependent["name"]}"')
            connection.execute(DOCUMENT_TABLE)
            connection.execute(CANDIDATE_TABLE)
            cursor = connection.execute(
                "SELECT generation_id,unit_key,page_kind,contract_version,input_fingerprint,"
                "manifest_json,recipe_json,result_json,report_json,previous_generation_id,"
                "work_token,created_at,run_id,state,removal_authority "
                "FROM source_generations ORDER BY generation_id"
            )
            for row in cursor:
                documents = tuple(str(value) for value in row[5:9])
                document_values = _document_values(documents)
                connection.execute(
                    "INSERT INTO source_generation_documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (row[0], *document_values),
                )
                recipe_fields = _recipe_fields(connection, documents[1])
                connection.execute(
                    "INSERT INTO benchmark_source_generations VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*row[:5], *recipe_fields, *row[9:]),
                )
                rows += 1
                raw_bytes += sum(int(document_values[index]) for index in (1, 4, 7, 10))
                compressed_bytes += sum(len(document_values[index]) for index in (0, 3, 6, 9))
            cursor.close()
            connection.execute("DROP TABLE source_generations")
            connection.execute(
                "ALTER TABLE benchmark_source_generations RENAME TO source_generations"
            )
            for sql in kept_indexes.values():
                connection.execute(sql)
            connection.execute(
                "CREATE INDEX source_generations_retained_source "
                "ON source_generations(recipe_source)"
            )
            connection.execute(IMMUTABLE_TRIGGER)
            connection.execute(DOCUMENT_IMMUTABLE_TRIGGER)
            for trigger in kept_triggers:
                connection.execute(trigger["sql"])
            for dependent in external_dependents:
                connection.execute(dependent["sql"])
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
        connection.execute("ANALYZE source_generations")
        connection.execute("ANALYZE source_generation_documents")
        vacuum_started = time.perf_counter()
        connection.execute("VACUUM")
        vacuum_seconds = time.perf_counter() - vacuum_started
    return {
        "prepare_seconds": time.perf_counter() - started,
        "rebuild_seconds": rebuild_seconds,
        "vacuum_seconds": vacuum_seconds,
        "rows_streamed": rows,
        "raw_document_bytes": raw_bytes,
        "compressed_document_bytes": compressed_bytes,
    }


def integrity(connection: sqlite3.Connection) -> dict[str, Any]:
    checked = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    foreign_keys = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
    return {
        "integrity_check": checked,
        "foreign_key_check": foreign_keys,
        "passed": checked == ["ok"] and not foreign_keys,
    }


def _update_framed(digest: Any, values: Iterable[object]) -> None:
    for value in values:
        if value is None:
            encoded = b"n"
        elif isinstance(value, bytes):
            encoded = b"b" + value
        else:
            encoded = b"s" + str(value).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)


def _update_raw_framed(digest: Any, values: Iterable[bytes]) -> None:
    for value in values:
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)


def _verify_generation(row: tuple[Any, ...], documents: tuple[bytes, ...]) -> tuple[bool, bool]:
    generation_id, unit_key, input_fingerprint, previous, work_token, removal = row
    manifest, recipe, result, report = documents
    try:
        manifest_value = json.loads(manifest)
        recipe_value = json.loads(recipe)
        report_value = json.loads(report)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, False
    actual_fingerprint = hashlib.sha256(
        canonical({"manifest": manifest_value, "recipe": recipe_value})
    ).hexdigest()
    evidence = {
        "unit": unit_key,
        "fingerprint": input_fingerprint,
        "report": report_value,
        "result": result.decode(),
        "previous": previous,
        "work_token": work_token,
    }
    actual_generation = "gen_" + hashlib.sha256(canonical(evidence)).hexdigest()
    authority_matches = (
        isinstance(report_value, dict) and report_value.get("proposed_removal") == removal
    )
    return (
        actual_fingerprint == input_fingerprint,
        actual_generation == generation_id and authority_matches,
    )


def _baseline_rows(
    connection: sqlite3.Connection,
) -> Iterator[tuple[tuple[Any, ...], tuple[bytes, ...], tuple[Any, ...]]]:
    sql = (
        "SELECT generation_id,unit_key,input_fingerprint,previous_generation_id,work_token,"
        "removal_authority,manifest_json,recipe_json,result_json,report_json,"
        "length(CAST(recipe_json AS BLOB)),json_valid(recipe_json),"
        "CASE WHEN json_valid(recipe_json) THEN json_extract(recipe_json,'$.context.snapshot_id') END,"
        "CASE WHEN json_valid(recipe_json) AND json_type(recipe_json,'$.context.source')='text' "
        "THEN json_extract(recipe_json,'$.context.source') END "
        "FROM source_generations ORDER BY generation_id"
    )
    for row in connection.execute(sql):
        documents = tuple(str(value).encode() for value in row[6:10])
        yield tuple(row[:6]), documents, tuple(row[10:14])


def _candidate_rows(
    connection: sqlite3.Connection,
) -> Iterator[tuple[tuple[Any, ...], tuple[bytes, ...], tuple[Any, ...]]]:
    sql = (
        "SELECT g.generation_id,g.unit_key,g.input_fingerprint,g.previous_generation_id,"
        "g.work_token,g.removal_authority,g.recipe_bytes,g.recipe_valid,g.recipe_snapshot_id,"
        "g.recipe_source,d.manifest_zlib,d.manifest_bytes,d.manifest_sha256,"
        "d.recipe_zlib,d.recipe_bytes,d.recipe_sha256,d.result_zlib,d.result_bytes,d.result_sha256,"
        "d.report_zlib,d.report_bytes,d.report_sha256 FROM source_generations g "
        "JOIN source_generation_documents d USING(generation_id) ORDER BY g.generation_id"
    )
    for row in connection.execute(sql):
        documents: list[bytes] = []
        for offset in (10, 13, 16, 19):
            expected = int(row[offset + 1])
            raw = decode_document(bytes(row[offset]), expected_bytes=expected, max_bytes=expected)
            require(
                hashlib.sha256(raw).digest() == bytes(row[offset + 2]),
                "source document SHA-256 mismatch",
            )
            documents.append(raw)
        yield tuple(row[:6]), tuple(documents), tuple(row[6:10])


def content_profile(connection: sqlite3.Connection, *, candidate: bool) -> dict[str, Any]:
    document_digest = hashlib.sha256()
    metadata_digest = hashlib.sha256()
    extracted_digest = hashlib.sha256()
    rows = 0
    document_bytes = {name: 0 for name in DOCUMENT_NAMES}
    fingerprint_mismatches = 0
    generation_mismatches = 0
    mismatch_examples: list[dict[str, Any]] = []
    iterator = _candidate_rows(connection) if candidate else _baseline_rows(connection)
    for metadata, documents, extracted in iterator:
        rows += 1
        _update_framed(metadata_digest, metadata)
        _update_framed(extracted_digest, (metadata[0], *extracted))
        _update_raw_framed(document_digest, (str(metadata[0]).encode(), *documents))
        for name, raw in zip(DOCUMENT_NAMES, documents, strict=True):
            document_bytes[name] += len(raw)
        fingerprint_ok, generation_ok = _verify_generation(metadata, documents)
        fingerprint_mismatches += int(not fingerprint_ok)
        generation_mismatches += int(not generation_ok)
        if (not fingerprint_ok or not generation_ok) and len(
            mismatch_examples
        ) < MAX_MISMATCH_EXAMPLES:
            mismatch_examples.append(
                {
                    "generation_id": str(metadata[0]),
                    "input_fingerprint_matches": fingerprint_ok,
                    "generation_and_authority_match": generation_ok,
                }
            )
    return {
        "rows": rows,
        "document_bytes": document_bytes,
        "total_document_bytes": sum(document_bytes.values()),
        "metadata_stream_sha256": metadata_digest.hexdigest(),
        "document_stream_sha256": document_digest.hexdigest(),
        "extracted_stream_sha256": extracted_digest.hexdigest(),
        "input_fingerprint_mismatch_count": fingerprint_mismatches,
        "generation_or_authority_mismatch_count": generation_mismatches,
        "mismatch_examples": mismatch_examples,
        "mismatch_examples_truncated": (
            fingerprint_mismatches + generation_mismatches > len(mismatch_examples)
        ),
    }


def storage_profile(connection: sqlite3.Connection, database: Path) -> dict[str, Any]:
    objects = {
        str(name): {"pages": int(pages), "bytes": int(size)}
        for name, pages, size in connection.execute(
            "SELECT name,count(*),sum(pgsize) FROM dbstat GROUP BY name"
        )
    }
    source_names = {"source_generations", "source_generation_documents"}
    for table in tuple(source_names):
        source_names.update(
            str(row[1]) for row in connection.execute(f"PRAGMA index_list('{table}')")
        )
    relevant = {name: objects[name] for name in sorted(source_names) if name in objects}
    return {
        "file_bytes": database.stat().st_size,
        "page_size": int(connection.execute("PRAGMA page_size").fetchone()[0]),
        "page_count": int(connection.execute("PRAGMA page_count").fetchone()[0]),
        "freelist_pages": int(connection.execute("PRAGMA freelist_count").fetchone()[0]),
        "dbstat_bytes": sum(int(item["bytes"]) for item in objects.values()),
        "source_generation_objects": relevant,
        "source_generation_total_bytes": sum(int(item["bytes"]) for item in relevant.values()),
    }


def one_to_one_profile(connection: sqlite3.Connection, *, candidate: bool) -> dict[str, Any]:
    if not candidate:
        return {"applicable": False, "passed": True}
    generation_rows = int(
        connection.execute("SELECT count(*) FROM source_generations").fetchone()[0]
    )
    document_rows = int(
        connection.execute("SELECT count(*) FROM source_generation_documents").fetchone()[0]
    )
    missing = int(
        connection.execute(
            "SELECT count(*) FROM source_generations g LEFT JOIN source_generation_documents d "
            "USING(generation_id) WHERE d.generation_id IS NULL"
        ).fetchone()[0]
    )
    orphan = int(
        connection.execute(
            "SELECT count(*) FROM source_generation_documents d LEFT JOIN source_generations g "
            "USING(generation_id) WHERE g.generation_id IS NULL"
        ).fetchone()[0]
    )
    return {
        "applicable": True,
        "generation_rows": generation_rows,
        "document_rows": document_rows,
        "missing_documents": missing,
        "orphan_documents": orphan,
        "passed": generation_rows == document_rows and missing == 0 and orphan == 0,
    }


def constraint_probes(connection: sqlite3.Connection, *, candidate: bool) -> dict[str, Any]:
    sample = connection.execute(
        "SELECT generation_id FROM source_generations ORDER BY generation_id LIMIT 1"
    ).fetchone()
    require(sample is not None, "benchmark requires at least one source generation")
    generation_id = str(sample[0])
    if candidate:
        sql = "UPDATE source_generation_documents SET manifest_zlib=manifest_zlib WHERE generation_id=?"
        expected = "source generation documents are immutable"
    else:
        sql = "UPDATE source_generations SET manifest_json=manifest_json WHERE generation_id=?"
        expected = "source generation evidence is immutable"
    rejected = False
    error = None
    connection.execute("SAVEPOINT immutable_probe")
    try:
        connection.execute(sql, (generation_id,))
    except sqlite3.IntegrityError as caught:
        rejected = True
        error = str(caught)
    finally:
        connection.execute("ROLLBACK TO immutable_probe")
        connection.execute("RELEASE immutable_probe")
    deferred_delete = None
    deferred_error = None
    if candidate:
        connection.execute("BEGIN")
        try:
            connection.execute(
                "DELETE FROM source_generation_documents WHERE generation_id=?", (generation_id,)
            )
            try:
                connection.commit()
                deferred_delete = False
            except sqlite3.IntegrityError as caught:
                deferred_error = str(caught)
                deferred_delete = deferred_error == "FOREIGN KEY constraint failed"
        finally:
            connection.rollback()
    return {
        "immutable_update_rejected": rejected,
        "immutable_error": error,
        "immutable_expected_error": expected,
        "immutable_error_matches": rejected and error == expected,
        "document_delete_commit_rejected": deferred_delete,
        "document_delete_error": deferred_error,
        "passed": rejected and error == expected and (not candidate or deferred_delete is True),
    }


def _query_parameters(connection: sqlite3.Connection) -> dict[str, tuple[object, ...]]:
    row = connection.execute(
        "SELECT generation_id,unit_key FROM source_generations ORDER BY generation_id LIMIT 1"
    ).fetchone()
    require(row is not None, "benchmark requires at least one source generation")
    source_expr = (
        "CASE WHEN json_valid(recipe_json) AND json_type(recipe_json,'$.context.source')='text' "
        "THEN json_extract(recipe_json,'$.context.source') END"
    )
    snapshot_expr = (
        "CASE WHEN json_valid(recipe_json) THEN "
        "json_extract(recipe_json,'$.context.snapshot_id') END"
    )
    # Pick values that exist so the measured reads return rows. A NULL parameter
    # would make both layouts return nothing and compare equal for no reason.
    source = connection.execute(
        f"SELECT {source_expr} FROM source_generations WHERE {source_expr} IS NOT NULL "
        "ORDER BY generation_id LIMIT 1"
    ).fetchone()
    snapshot = connection.execute(
        f"SELECT {snapshot_expr} FROM source_generations WHERE {snapshot_expr} IS NOT NULL "
        "ORDER BY generation_id LIMIT 1"
    ).fetchone()
    return {
        "generation": (row[0],),
        "unit": (row[1],),
        "source": (source[0] if source else None,),
        "snapshot": (snapshot[0] if snapshot else None,),
    }


def query_benchmark(
    connection: sqlite3.Connection,
    *,
    candidate: bool,
    repeats: int,
    parameters: dict[str, tuple[object, ...]],
) -> list[dict[str, Any]]:
    require(repeats > 0, "timing repeats must be positive")
    if candidate:
        specs = (
            (
                "unit_history",
                "SELECT generation_id,state,created_at FROM source_generations WHERE unit_key=? ORDER BY created_at,generation_id",
                "unit",
                None,
                True,
            ),
            (
                "retained_source",
                "SELECT generation_id FROM source_generations WHERE recipe_source=?",
                "source",
                None,
                False,
            ),
            (
                "snapshot",
                "SELECT generation_id FROM source_generations WHERE recipe_snapshot_id=?",
                "snapshot",
                None,
                False,
            ),
            (
                "point_documents",
                "SELECT manifest_zlib,manifest_bytes,recipe_zlib,recipe_bytes,result_zlib,result_bytes,report_zlib,report_bytes FROM source_generation_documents WHERE generation_id=?",
                "generation",
                "documents",
                True,
            ),
        )
    else:
        source_expr = "CASE WHEN json_valid(recipe_json) AND json_type(recipe_json,'$.context.source')='text' THEN json_extract(recipe_json,'$.context.source') END"
        snapshot_expr = "CASE WHEN json_valid(recipe_json) THEN json_extract(recipe_json,'$.context.snapshot_id') END"
        specs = (
            (
                "unit_history",
                "SELECT generation_id,state,created_at FROM source_generations WHERE unit_key=? ORDER BY created_at,generation_id",
                "unit",
                None,
                True,
            ),
            (
                "retained_source",
                f"SELECT generation_id FROM source_generations WHERE {source_expr}=?",
                "source",
                None,
                False,
            ),
            (
                "snapshot",
                f"SELECT generation_id FROM source_generations WHERE {snapshot_expr}=?",
                "snapshot",
                None,
                False,
            ),
            (
                "point_documents",
                "SELECT manifest_json,recipe_json,result_json,report_json FROM source_generations WHERE generation_id=?",
                "generation",
                "text_documents",
                True,
            ),
        )
    results = []
    for name, sql, kind, decoder, ordered in specs:
        params = parameters[kind]
        plan = [str(row[3]) for row in connection.execute("EXPLAIN QUERY PLAN " + sql, params)]
        timings: list[float] = []
        digest_value = None
        multiset_value = None
        returned = None
        for _ in range(repeats):
            digest = hashlib.sha256()
            row_digests: list[bytes] = []
            count = 0
            started = time.perf_counter()
            for row in connection.execute(sql, params):
                count += 1
                if decoder == "documents":
                    values = tuple(
                        decode_document(
                            bytes(row[offset]),
                            expected_bytes=int(row[offset + 1]),
                            max_bytes=int(row[offset + 1]),
                        )
                        for offset in (0, 2, 4, 6)
                    )
                elif decoder == "text_documents":
                    values = tuple(str(value).encode() for value in row)
                else:
                    values = tuple(row)
                _update_framed(digest, values)
                row_digest = hashlib.sha256()
                _update_framed(row_digest, values)
                row_digests.append(row_digest.digest())
            timings.append(time.perf_counter() - started)
            returned = count
            digest_value = digest.hexdigest()
            multiset = hashlib.sha256()
            _update_raw_framed(multiset, sorted(row_digests))
            multiset_value = multiset.hexdigest()
        results.append(
            {
                "name": name,
                "sql": sql,
                "plan": plan,
                "ordered": ordered,
                "rows_returned": returned,
                "output_sha256": digest_value,
                "output_multiset_sha256": multiset_value,
                "repeats": repeats,
                "seconds": timings,
                "best_seconds": min(timings),
            }
        )
    return results


def deterministic_gzip(database: Path, destination: Path) -> dict[str, Any]:
    started = time.perf_counter()
    identity_before = file_identity(database)
    digest = hashlib.sha256()
    with database.open("rb") as incoming, destination.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as zipped:
            while chunk := incoming.read(8 * 1024 * 1024):
                digest.update(chunk)
                zipped.write(chunk)
    identity_after = file_identity(database)
    return {
        "kind": "deterministic-gzip-whole-database-proxy",
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": file_sha256(destination),
        "seconds": time.perf_counter() - started,
        "compresslevel": 6,
        "gzip_mtime": 0,
        "source_database_sha256": digest.hexdigest(),
        "source_identity_before": identity_before,
        "source_identity_after": identity_after,
        "source_unchanged_while_compressing": identity_before == identity_after,
    }


def inspect_layout(
    database: Path,
    *,
    candidate: bool,
    repeats: int,
    parameters: dict[str, tuple[object, ...]],
) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        return {
            "content": content_profile(connection, candidate=candidate),
            "integrity": integrity(connection),
            "one_to_one": one_to_one_profile(connection, candidate=candidate),
            "constraints": constraint_probes(connection, candidate=candidate),
            "queries": query_benchmark(
                connection, candidate=candidate, repeats=repeats, parameters=parameters
            ),
            "storage": storage_profile(connection, database),
            "source_generations": _table_metadata(connection, "source_generations"),
            "documents": (
                _table_metadata(connection, "source_generation_documents") if candidate else None
            ),
            "indexes": _index_sql(connection, "source_generations"),
            "triggers": _table_triggers(connection, "source_generations"),
            "document_triggers": (
                _table_triggers(connection, "source_generation_documents") if candidate else []
            ),
            "external_dependents": _external_dependents(connection),
        }


def _gzip_bound(layout: dict[str, Any]) -> bool:
    inspected = layout["inspected_database_signature"]
    compressed = layout["compressed_backup_proxy"]
    identity = {key: inspected[key] for key in ("bytes", "mtime_ns", "inode")}
    return bool(
        compressed["source_unchanged_while_compressing"]
        and compressed["source_database_sha256"] == inspected["sha256"]
        and compressed["source_identity_before"] == identity
        and compressed["source_identity_after"] == identity
    )


def query_outputs_equivalent(
    baseline_queries: dict[str, dict[str, Any]],
    candidate_queries: dict[str, dict[str, Any]],
) -> bool:
    """Compare read results across layouts without demanding the same row order.

    A1 serves `retained_source` from an ordinary index and scans a much narrower
    anchor for `snapshot`, so SQLite may return the same rows in a different
    order. Neither statement has an ORDER BY, so that reordering is allowed and
    those reads are compared as multisets. A read whose SQL does fix the order
    must still match byte for byte in that order. An empty read proves nothing,
    so every read must return at least one row.
    """
    if set(baseline_queries) != set(candidate_queries):
        return False
    for name, candidate in candidate_queries.items():
        baseline = baseline_queries[name]
        if baseline["rows_returned"] != candidate["rows_returned"]:
            return False
        if int(candidate["rows_returned"]) < 1:
            return False
        if baseline["output_multiset_sha256"] != candidate["output_multiset_sha256"]:
            return False
        if (
            baseline["ordered"]
            and candidate["ordered"]
            and baseline["output_sha256"] != candidate["output_sha256"]
        ):
            return False
    return True


def gate_results(
    source: dict[str, Any],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    before: dict[str, int | str],
    after: dict[str, int | str],
    sidecars_before: dict[str, int | None],
    sidecars_after: dict[str, int | None],
) -> dict[str, Any]:
    source_content = source["content"]
    candidate_queries = {item["name"]: item for item in candidate["queries"]}
    baseline_queries = {item["name"]: item for item in baseline["queries"]}
    query_outputs_match = query_outputs_equivalent(baseline_queries, candidate_queries)
    content_keys = (
        "rows",
        "document_bytes",
        "total_document_bytes",
        "metadata_stream_sha256",
        "document_stream_sha256",
        "extracted_stream_sha256",
        "input_fingerprint_mismatch_count",
        "generation_or_authority_mismatch_count",
    )
    candidate_source_columns = [
        "generation_id",
        "unit_key",
        "page_kind",
        "contract_version",
        "input_fingerprint",
        "recipe_bytes",
        "recipe_valid",
        "recipe_snapshot_id",
        "recipe_source",
        "previous_generation_id",
        "work_token",
        "created_at",
        "run_id",
        "state",
        "removal_authority",
    ]
    candidate_document_columns = [
        "generation_id",
        *[
            column
            for name in DOCUMENT_NAMES
            for column in (f"{name}_zlib", f"{name}_bytes", f"{name}_sha256")
        ],
    ]
    candidate_source_foreign_keys = [
        {
            "table": "source_generation_documents",
            "from": "generation_id",
            "to": "generation_id",
            "on_update": "NO ACTION",
            "on_delete": "NO ACTION",
            "match": "NONE",
        },
        {
            "table": "source_generations",
            "from": "previous_generation_id",
            "to": "generation_id",
            "on_update": "NO ACTION",
            "on_delete": "NO ACTION",
            "match": "NONE",
        },
        {
            "table": "runs",
            "from": "run_id",
            "to": "run_id",
            "on_update": "NO ACTION",
            "on_delete": "NO ACTION",
            "match": "NONE",
        },
        {
            "table": "source_units",
            "from": "unit_key",
            "to": "unit_key",
            "on_update": "NO ACTION",
            "on_delete": "NO ACTION",
            "match": "NONE",
        },
    ]
    candidate_source_foreign_keys.sort(key=lambda item: (item["from"], item["table"]))
    candidate_document_foreign_keys = [
        {
            "table": "source_generations",
            "from": "generation_id",
            "to": "generation_id",
            "on_update": "NO ACTION",
            "on_delete": "CASCADE",
            "match": "NONE",
        }
    ]
    source_other_triggers = [
        item
        for item in source["schema"]["triggers"]
        if item["name"] != "source_generation_immutable"
    ]
    candidate_other_triggers = [
        item for item in candidate["triggers"] if item["name"] != "source_generation_immutable"
    ]
    candidate_schema_matches = bool(
        [item["name"] for item in candidate["source_generations"]["columns"]]
        == candidate_source_columns
        and candidate["source_generations"]["foreign_keys"] == candidate_source_foreign_keys
        and candidate["documents"] is not None
        and [item["name"] for item in candidate["documents"]["columns"]]
        == candidate_document_columns
        and candidate["documents"]["foreign_keys"] == candidate_document_foreign_keys
        and "WITHOUT ROWID" in candidate["documents"]["sql"].upper()
        and set(candidate["indexes"]) == set(EXPECTED_INDEX_SQL)
        and _normalized_sql(candidate["indexes"]["source_generations_retained_source"])
        == _normalized_sql(
            "CREATE INDEX source_generations_retained_source ON source_generations(recipe_source)"
        )
        and source_other_triggers == candidate_other_triggers
        and any(item["name"] == "source_generation_immutable" for item in candidate["triggers"])
        and candidate["document_triggers"]
        == [
            {
                "name": "source_generation_documents_immutable",
                "sql": candidate["document_triggers"][0]["sql"],
            }
        ]
    )
    checks = {
        "source_signature_and_hash_unchanged": before == after,
        "source_sidecars_unchanged_and_absent": sidecars_before == sidecars_after
        and all(value is None for value in sidecars_after.values()),
        "source_integrity": source["integrity"]["passed"],
        "generation_recomputation_results_preserved": (
            baseline["content"]["input_fingerprint_mismatch_count"]
            == candidate["content"]["input_fingerprint_mismatch_count"]
            == source_content["input_fingerprint_mismatch_count"]
            and baseline["content"]["generation_or_authority_mismatch_count"]
            == candidate["content"]["generation_or_authority_mismatch_count"]
            == source_content["generation_or_authority_mismatch_count"]
        ),
        "source_generation_ids_and_fingerprints_recompute": (
            source_content["input_fingerprint_mismatch_count"] == 0
            and source_content["generation_or_authority_mismatch_count"] == 0
        ),
        "baseline_content_matches_source": all(
            baseline["content"][key] == source_content[key] for key in content_keys
        ),
        "candidate_content_matches_source": all(
            candidate["content"][key] == source_content[key] for key in content_keys
        ),
        "candidate_build_matches_content": (
            candidate["build"]["rows_streamed"] == candidate["content"]["rows"]
            and candidate["build"]["raw_document_bytes"]
            == candidate["content"]["total_document_bytes"]
        ),
        "baseline_integrity": baseline["integrity"]["passed"],
        "candidate_integrity": candidate["integrity"]["passed"],
        "baseline_schema_preserved": (
            baseline["source_generations"] == source["schema"]["table"]
            and baseline["indexes"] == source["schema"]["indexes"]
            and baseline["triggers"] == source["schema"]["triggers"]
            and baseline["external_dependents"] == source["schema"]["external_dependents"]
        ),
        "candidate_schema_and_triggers_match_a1": (
            candidate_schema_matches
            and candidate["external_dependents"] == source["schema"]["external_dependents"]
        ),
        "candidate_one_to_one": candidate["one_to_one"]["passed"],
        "baseline_constraints": baseline["constraints"]["passed"],
        "candidate_constraints": candidate["constraints"]["passed"],
        "query_outputs_match": query_outputs_match,
        "baseline_bound_to_gzip": _gzip_bound(baseline),
        "candidate_bound_to_gzip": _gzip_bound(candidate),
        "baseline_compact": baseline["storage"]["freelist_pages"] == 0,
        "candidate_compact": candidate["storage"]["freelist_pages"] == 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "source_recomputation_passed": (
            source_content["input_fingerprint_mismatch_count"] == 0
            and source_content["generation_or_authority_mismatch_count"] == 0
        ),
    }


def build_receipt(report: dict[str, Any], command: list[str] | None) -> dict[str, Any]:
    layouts = {}
    for name in ("baseline", "compressed"):
        layout = report["layouts"][name]
        layouts[name] = {
            "database_bytes": layout["storage"]["file_bytes"],
            "database_sha256": layout["inspected_database_signature"]["sha256"],
            "source_generation_bytes": layout["storage"]["source_generation_total_bytes"],
            "gzip_proxy_bytes": layout["compressed_backup_proxy"]["bytes"],
            "gzip_proxy_sha256": layout["compressed_backup_proxy"]["sha256"],
            "document_stream_sha256": layout["content"]["document_stream_sha256"],
            "extracted_stream_sha256": layout["content"]["extracted_stream_sha256"],
            "build": layout["build"],
        }
    return {
        "format": RECEIPT_FORMAT,
        "tool_version": TOOL_VERSION,
        "started_at": report["started_at"],
        "finished_at": report["finished_at"],
        "runtime": report["runtime"],
        "source": {
            "path": report["source"]["path"],
            "signature_before": report["source"]["signature_before"],
            "signature_after": report["source"]["signature_after"],
        },
        "layouts": layouts,
        "comparison": report["comparison"],
        "scratch": report["scratch"],
        "gates": report["gates"],
        "command": command,
        "production_operations": 0,
        "network_requests": 0,
        "limit": "deterministic gzip is a whole-database backup-size proxy, not a checkpoint",
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
    started = time.perf_counter()
    started_at = datetime.now(UTC).isoformat()
    runtime = runtime_metadata()
    source_root, database = resolve_source(source)
    scratch = resolve_new_path(scratch, "--scratch", source_root)
    output = resolve_new_path(output, "--output", source_root)
    receipt = resolve_new_path(receipt, "--receipt", source_root)
    require(output != receipt, "--output and --receipt must differ")
    require(output != scratch and receipt != scratch, "report paths must differ from --scratch")
    require(timing_repeats > 0, "timing repeats must be positive")

    sidecars_before = require_closed_sidecars(database, "before inspection")
    signature_before = file_signature(database)
    with open_immutable(database) as connection:
        source_schema = validate_source(connection)
        parameters = _query_parameters(connection)
        source_inspection = {
            "schema": source_schema,
            "content": content_profile(connection, candidate=False),
            "integrity": integrity(connection),
            "storage": storage_profile(connection, database),
        }

    scratch.mkdir(parents=True)
    baseline_path = scratch / "baseline.sqlite"
    candidate_path = scratch / "compressed.sqlite"
    with ScratchMeter(scratch) as meter:
        baseline_copy = copy_database(database, baseline_path)
        baseline_build = compact_baseline(baseline_path)
        baseline_build["copy_seconds"] = baseline_copy
        baseline_build["total_seconds"] = baseline_copy + baseline_build["prepare_seconds"]

        candidate_copy = copy_database(database, candidate_path)
        candidate_build = rebuild_candidate(candidate_path)
        candidate_build["copy_seconds"] = candidate_copy
        candidate_build["total_seconds"] = candidate_copy + candidate_build["prepare_seconds"]

        baseline = inspect_layout(
            baseline_path, candidate=False, repeats=timing_repeats, parameters=parameters
        )
        candidate = inspect_layout(
            candidate_path, candidate=True, repeats=timing_repeats, parameters=parameters
        )
        for layout, path, build in (
            (baseline, baseline_path, baseline_build),
            (candidate, candidate_path, candidate_build),
        ):
            layout["path"] = str(path)
            layout["build"] = build
            layout["inspected_database_signature"] = file_signature(path)
        baseline["compressed_backup_proxy"] = deterministic_gzip(
            baseline_path, scratch / "baseline.sqlite.gz"
        )
        candidate["compressed_backup_proxy"] = deterministic_gzip(
            candidate_path, scratch / "compressed.sqlite.gz"
        )

    signature_after = file_signature(database)
    sidecars_after = require_closed_sidecars(database, "after inspection")
    runtime_after = runtime_metadata()
    runtime["tool_sha256_after"] = runtime_after["tool_sha256"]
    runtime["tool_unchanged"] = runtime["tool_sha256"] == runtime_after["tool_sha256"]
    gates = gate_results(
        source_inspection,
        baseline,
        candidate,
        signature_before,
        signature_after,
        sidecars_before,
        sidecars_after,
    )
    gates["checks"]["tool_sha256_unchanged"] = runtime["tool_unchanged"] is True
    gates["passed"] = all(gates["checks"].values())
    baseline_file = int(baseline["storage"]["file_bytes"])
    candidate_file = int(candidate["storage"]["file_bytes"])
    baseline_objects = int(baseline["storage"]["source_generation_total_bytes"])
    candidate_objects = int(candidate["storage"]["source_generation_total_bytes"])
    baseline_gzip = int(baseline["compressed_backup_proxy"]["bytes"])
    candidate_gzip = int(candidate["compressed_backup_proxy"]["bytes"])
    report = {
        "format": REPORT_FORMAT,
        "tool_version": TOOL_VERSION,
        "runtime": runtime,
        "command": command,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": time.perf_counter() - started,
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
        "scratch": {
            "path": str(scratch),
            "peak_bytes": meter.peak_bytes,
            "sampling_interval_seconds": meter.interval,
        },
        "layouts": {"baseline": baseline, "compressed": candidate},
        "comparison": {
            "whole_database_bytes_delta": candidate_file - baseline_file,
            "whole_database_bytes_saved": baseline_file - candidate_file,
            "source_generation_objects_bytes_delta": candidate_objects - baseline_objects,
            "source_generation_objects_bytes_saved": baseline_objects - candidate_objects,
            "gzip_proxy_bytes_delta": candidate_gzip - baseline_gzip,
            "gzip_proxy_bytes_saved": baseline_gzip - candidate_gzip,
        },
        "gates": gates,
        "limits": [
            "The source must already be a closed disposable schema-29 copy.",
            "The A1 schema is a benchmark prototype and is not a production migration.",
            "Timings are local observations, not service levels.",
            "Deterministic gzip of SQLite is a backup-size proxy, not a checkpoint.",
            "Peak scratch is sampled and can miss a shorter transient maximum.",
            "Each layout runs ANALYZE on its own source-generation tables before VACUUM: "
            "one table for the baseline, two for the candidate.",
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
