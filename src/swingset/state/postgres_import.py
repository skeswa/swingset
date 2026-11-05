"""Read frozen SQLite checkpoints, COPY exact values, and independently audit.

SQLite is an import format only. Destination worker grants are withheld until
all tables, constraints, files and typed row digests have passed their audit.
"""

from __future__ import annotations

import codecs
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import struct
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from . import postgres_schema as schema

CHUNK = 1024 * 1024
LARGE = 256 * 1024
MAX_FIELD_BYTES = (1 << 30) - 1
FORMAT = "swingset-postgres-import-v1"


class ImportRejected(ValueError):
    """A checkpoint or value cannot be imported faithfully."""


def inspect_loaded_target(
    receipt_path: Path,
    conn: Any,
    *,
    receipt_sha256: str,
    source_sha256: str,
    database: str,
    database_oid: int,
    environment: str,
    database_id: str | None = None,
) -> dict[str, Any]:
    """Read a finalized loaded target without resuming or accepting its typed audit."""
    from .environment import identity

    descriptor = os.open(receipt_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 2 * 1024**2:
            raise ImportRejected("import receipt must be one bounded regular file")
        body = stream.read(2 * 1024**2 + 1)
    if len(body) > 2 * 1024**2 or hashlib.sha256(body).hexdigest() != receipt_sha256:
        raise ImportRejected("import receipt digest differs")
    previous = json.loads(body)
    if not isinstance(previous, dict):
        raise ImportRejected("import receipt must be an object")
    tables = previous.get("tables", {})
    if (
        not isinstance(tables, dict)
        or previous.get("format") != FORMAT
        or previous.get("source_sha256") != source_sha256
        or not re.fullmatch(r"[a-f0-9]{64}", source_sha256)
        or previous.get("migration_checksum") != schema.migration_checksum()
        or previous.get("loaded") != tables
        or set(tables) != set(schema.reference_manifest()["tables"])
        or any(
            not isinstance(value, dict)
            or set(value) != {"rows", "sha256"}
            or type(value["rows"]) is not int
            or value["rows"] < 0
            or not isinstance(value["sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
            for value in tables.values()
        )
    ):
        raise ImportRejected("inspection requires the exact fully loaded import receipt")
    if conn.in_transaction:
        raise ImportRejected("import inspection requires a fresh connection")
    if (
        conn.execute("SELECT current_setting('default_transaction_read_only')").fetchone()[0]
        != "on"
    ):
        raise ImportRejected("import inspection requires a read-only connection")
    with conn.read_snapshot():
        catalog = tuple(
            conn.execute(
                "SELECT datname,oid,datcollate,datctype,pg_encoding_to_char(encoding) FROM pg_database WHERE datname=current_database()"
            ).fetchone()
        )
        if catalog != (database, database_oid, "C", "C", "UTF8"):
            raise ImportRejected("import target database identity or locale differs")
        stored = identity(conn)
        if (
            stored.environment != environment
            or str(UUID(stored.database_id)) != stored.database_id
            or (database_id is not None and stored.database_id != database_id)
        ):
            raise ImportRejected("import target stored identity differs")
        parity = schema.validate_schema(conn, full=True)
        if not parity["equal"] or parity["tables"] != len(tables) + 1:
            raise ImportRejected("import target is not finalized")

        def require_quiet() -> None:
            conn.execute("SELECT pg_stat_clear_snapshot()")
            if conn.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datid=%s AND pid<>pg_backend_pid() AND (backend_type='client backend' OR backend_type IS NULL)",
                (database_oid,),
            ).fetchone()[0]:
                raise ImportRejected("import target has another client connection")

        require_quiet()
        counts = {
            name: conn.execute("SELECT count(*) FROM " + schema.qualified(name)).fetchone()[0]
            for name in sorted(tables)
        }
        if counts != {name: value["rows"] for name, value in tables.items()}:
            raise ImportRejected("import target row counts differ")
        size = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        require_quiet()
    from swingset.fetch.archive import canonical

    return {
        "format": "swingset-postgres-import-inspection-v1",
        "complete": True,
        "native_complete": previous.get("complete") is True,
        "typed_values_checked": False,
        "persistent_database_writes": 0,
        "database": database,
        "database_oid": database_oid,
        "database_id": stored.database_id,
        "environment": stored.environment,
        "schema": parity,
        "finalized_user_triggers": parity["triggers"],
        "migration_checksum": previous["migration_checksum"],
        "schema_fingerprint": previous.get("schema_fingerprint"),
        "counts": counts,
        "total_rows": sum(counts.values()),
        "database_bytes": size,
        "receipt_sha256": receipt_sha256,
        "source_sha256": source_sha256,
        "table_matrix_sha256": hashlib.sha256(canonical(tables)).hexdigest(),
        "other_client_sessions": 0,
    }


def _digest_file(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _checkpoint(source: Path, expected_sha256: str | None) -> tuple[Path, str]:
    source = source.resolve()
    path = source / "state.sqlite" if source.is_dir() else source
    manifest_path = path.parent / "checkpoint.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("schema_version") != schema.SOURCE_SCHEMA_VERSION:
            raise ImportRejected("checkpoint schema version differs")
        expected_files = manifest.get("files")
        if not isinstance(expected_files, dict) or "state.sqlite" not in expected_files:
            raise ImportRejected("checkpoint file manifest is malformed")
        actual_files = {
            p.relative_to(path.parent).as_posix()
            for p in path.parent.rglob("*")
            if p.is_file() and p != manifest_path
        }
        if actual_files != set(expected_files):
            raise ImportRejected("checkpoint file closure differs")
        for name, record in expected_files.items():
            candidate = path.parent / name
            if not candidate.resolve().is_relative_to(path.parent) or not candidate.is_file():
                raise ImportRejected("checkpoint names an unsafe or absent file")
            if (
                candidate.stat().st_size != int(record["size"])
                or _digest_file(candidate) != record["sha256"]
            ):
                raise ImportRejected("checkpoint file digest differs")
        expected_sha256 = expected_files["state.sqlite"]["sha256"]
    if expected_sha256 is None:
        raise ImportRejected("a checkpoint manifest or explicit source SHA-256 is required")
    if not path.is_file() or _digest_file(path) != expected_sha256:
        raise ImportRejected("source SQLite digest differs")
    if any(
        p.exists() and p.stat().st_size
        for p in (Path(str(path) + "-wal"), Path(str(path) + "-journal"))
    ):
        raise ImportRejected("source is not a frozen checkpoint: a journal remains")
    return path, expected_sha256


@contextmanager
def _source(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        conn.close()


def _catalog(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(zip(("type", "name", "tbl_name", "sql"), row, strict=True))
        for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
    ]


def source_schema_fingerprint(conn: sqlite3.Connection) -> str:
    return hashlib.sha256(
        json.dumps(
            _catalog(conn), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


@dataclass
class Cell:
    tag: bytes
    length: int
    value: Any
    stream: Callable[[], Iterator[bytes]] | None = None

    def blocks(self) -> Iterator[bytes]:
        if self.stream is not None:
            yield from self.stream()
        elif self.tag == b"N":
            return
        elif self.tag == b"T":
            yield self.value.encode("utf-8")
        elif self.tag == b"I":
            yield str(self.value).encode("ascii")
        elif self.tag == b"F":
            yield struct.pack(">d", self.value)
        else:
            raise ImportRejected("unknown cell type")


def _scalar(value: Any, kind: str, column: str) -> Cell:
    if value is None:
        return Cell(b"N", 0, None)
    if kind == "TEXT":
        if not isinstance(value, str) or "\x00" in value:
            raise ImportRejected(f"{column}: text storage class or NUL byte is invalid")
        try:
            body = value.encode("utf-8", errors="strict")
        except UnicodeError:
            raise ImportRejected(f"{column}: text is not valid UTF-8") from None
        return Cell(b"T", len(body), value)
    if kind == "INTEGER":
        if type(value) is not int or not -(1 << 63) <= value < (1 << 63):
            raise ImportRejected(f"{column}: integer storage class or range is invalid")
        return Cell(b"I", len(str(value)), value)
    if kind == "REAL":
        if type(value) is not float or not math.isfinite(value):
            raise ImportRejected(f"{column}: real storage class or finiteness is invalid")
        return Cell(b"F", 8, value)
    raise ImportRejected(f"{column}: unsupported declared storage class")


def _sqlite_text(
    conn: sqlite3.Connection, table: str, column: str, rowid: int | None, keys: tuple[Any, ...]
) -> Iterator[bytes]:
    if rowid is not None:
        with conn.blobopen(table, column, rowid, readonly=True) as blob:
            yield from iter(lambda: blob.read(CHUNK), b"")
        return
    key_names = _keys(table)
    where = " AND ".join(_quote(name) + "=?" for name in key_names)
    start = 1
    while True:
        row = conn.execute(
            f"SELECT substr(CAST({_quote(column)} AS BLOB),?,?) FROM {_quote(table)} WHERE {where}",
            (start, CHUNK, *keys),
        ).fetchone()
        if row is None or not row[0]:
            return
        yield bytes(row[0])
        start += len(row[0])


def _postgres_text(conn: Any, table: str, column: str, keys: tuple[Any, ...]) -> Iterator[bytes]:
    where = " AND ".join(_quote(name) + "=%s" for name in _keys(table))
    start = 1
    while True:
        row = conn.execute(
            f"SELECT ops.text_bytes({_quote(column)},%s,%s) FROM {schema.qualified(table)} WHERE {where}",
            (start, CHUNK, *keys),
        ).fetchone()
        if row is None or not row[0]:
            return
        body = bytes(row[0])
        yield body
        start += len(body)


def _sqlite_large(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    rowid: int | None,
    keys: tuple[Any, ...],
    size: int,
) -> Cell:
    def blocks() -> Iterator[bytes]:
        yield from _sqlite_text(conn, table, column, rowid, keys)

    return Cell(b"T", size, None, blocks)


def _postgres_large(conn: Any, table: str, column: str, keys: tuple[Any, ...], size: int) -> Cell:
    def blocks() -> Iterator[bytes]:
        yield from _postgres_text(conn, table, column, keys)

    return Cell(b"T", size, None, blocks)


def _keys(table: str) -> tuple[str, ...]:
    columns = schema.reference_manifest()["tables"][table]["columns"]
    return tuple(c["name"] for c in sorted(columns, key=lambda c: c["pk"]) if c["pk"])


def _sqlite_rows(
    conn: sqlite3.Connection, table: str, *, physical: bool = False
) -> Iterator[list[Cell]]:
    specification = schema.reference_manifest()["tables"][table]
    columns = specification["columns"]
    has_rowid = "WITHOUT ROWID" not in specification["sql"].upper()
    expressions = ["rowid" if has_rowid else "NULL", *map(_quote, _keys(table))]
    for col in columns:
        name = _quote(col["name"])
        expressions.extend(
            [
                f"CASE WHEN typeof({name})='text' AND length(CAST({name} AS BLOB))>{LARGE} THEN NULL ELSE {name} END",
                f"typeof({name})",
                f"length(CAST({name} AS BLOB))",
            ]
        )
    order = ",".join(f"CAST({_quote(name)} AS TEXT) COLLATE BINARY" for name in _keys(table))
    try:
        cursor = conn.execute(
            f"SELECT {','.join(expressions)} FROM {_quote(table)} ORDER BY {order}"
        )
        for row in cursor:
            key_values = tuple(row[1 : 1 + len(_keys(table))])
            fields: list[Cell] = []
            offset = 1 + len(key_values)
            for index, column in enumerate(columns):
                value, stored, size = row[offset + index * 3 : offset + index * 3 + 3]
                label = table + "." + column["name"]
                if size is not None and int(size) > MAX_FIELD_BYTES:
                    raise ImportRejected(f"{label}: field exceeds Postgres's limit")
                if column["pk"] and stored == "null":
                    raise ImportRejected(f"{label}: NULL key")
                wanted = {"TEXT": "text", "INTEGER": "integer", "REAL": "real"}[column["type"]]
                if stored not in {"null", wanted}:
                    raise ImportRejected(
                        f"{label}: SQLite storage class differs from the declared column"
                    )
                if stored == "text" and int(size) > LARGE:
                    cell = _sqlite_large(conn, table, column["name"], row[0], key_values, int(size))
                else:
                    cell = _scalar(value, column["type"], label)
                if cell.tag == b"T":
                    decoder = codecs.getincrementaldecoder("utf-8")("strict")
                    try:
                        for block in cell.blocks():
                            if b"\0" in block:
                                raise ImportRejected(f"{label}: NUL byte in text")
                            decoder.decode(block)
                        decoder.decode(b"", final=True)
                    except UnicodeError:
                        raise ImportRejected(f"{label}: invalid UTF-8") from None
                fields.append(cell)
            if physical and table in schema.ROWID_TABLES:
                fields.append(_scalar(int(row[0]), "INTEGER", table + ".rowid"))
            yield fields
    except sqlite3.OperationalError:
        raise ImportRejected(f"{table}: SQLite text could not be decoded or read") from None


def _postgres_rows(
    conn: Any, table: str, *, physical: bool = False, predicate: str = "TRUE"
) -> Iterator[list[Cell]]:
    columns = schema.reference_manifest()["tables"][table]["columns"]
    expressions = [f'{_quote(name)} AS "__key_{index}"' for index, name in enumerate(_keys(table))]
    for index, column in enumerate(columns):
        name = _quote(column["name"])
        if column["type"] == "TEXT":
            expressions.extend(
                [
                    f'CASE WHEN octet_length({name})>{LARGE} THEN NULL ELSE {name} END AS "__value_{index}"',
                    f'octet_length({name}) AS "__size_{index}"',
                ]
            )
        else:
            expressions.extend([f'{name} AS "__value_{index}"', f'NULL AS "__size_{index}"'])
    if physical and table in schema.ROWID_TABLES:
        expressions.append("rowid")
    order = ",".join(f'({_quote(name)})::text COLLATE "C"' for name in _keys(table))
    # A server cursor prevents even narrow metadata rows from accumulating.
    with conn.cursor(
        name="import_audit_" + hashlib.sha256(table.encode()).hexdigest()[:12]
    ) as cursor:
        cursor.execute(
            f"SELECT {','.join(expressions)} FROM {schema.qualified(table)} WHERE {predicate} ORDER BY {order}"
        )
        for row in cursor:
            keys = tuple(row[: len(_keys(table))])
            offset = len(keys)
            fields = []
            for index, column in enumerate(columns):
                value, size = row[offset + index * 2 : offset + index * 2 + 2]
                if column["type"] == "TEXT" and size is not None and int(size) > LARGE:
                    fields.append(_postgres_large(conn, table, column["name"], keys, int(size)))
                else:
                    fields.append(_scalar(value, column["type"], table + "." + column["name"]))
            if physical and table in schema.ROWID_TABLES:
                fields.append(_scalar(int(row[-1]), "INTEGER", table + ".rowid"))
            yield fields


def _row_digest(rows: Iterator[list[Cell]]) -> tuple[int, str]:
    checksum = hashlib.sha256()
    count = 0
    for cells in rows:
        # Row width closes ambiguity between the end of one row and the next.
        checksum.update(struct.pack(">Q", len(cells)))
        for field in cells:
            checksum.update(field.tag)
            checksum.update(struct.pack(">Q", field.length))
            actual = 0
            for block in field.blocks():
                checksum.update(block)
                actual += len(block)
            if actual != field.length:
                raise ImportRejected("field length changed while auditing a frozen source")
        count += 1
    return count, checksum.hexdigest()


def preflight(
    source: Path,
    *,
    expected_sha256: str | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Reject incompatible source bytes before creating destination tables."""
    started = time.monotonic()

    def report(phase: str, **details: Any) -> None:
        if progress is not None:
            progress({"phase": phase, "seconds": round(time.monotonic() - started, 3), **details})

    report("source_digest")
    path, sha256 = _checkpoint(source, expected_sha256)
    tables: dict[str, Any] = {}
    with _source(path) as conn:
        if conn.execute("PRAGMA user_version").fetchone()[
            0
        ] != schema.SOURCE_SCHEMA_VERSION or conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone() != (str(schema.SOURCE_SCHEMA_VERSION),):
            raise ImportRejected("SQLite user_version differs from schema 37")
        report("integrity")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ImportRejected("SQLite integrity or foreign-key check failed")
        report("foreign_keys")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ImportRejected("SQLite integrity or foreign-key check failed")
        actual = _catalog(conn)
        if actual != schema.reference_manifest()["objects"]:
            raise ImportRejected("SQLite schema or trigger fingerprint differs from schema 37")
        from .legacy_checkpoint import verify_sqlite_files

        report("files")
        file_receipt = verify_sqlite_files(path.parent, conn)
        for table, specification in sorted(schema.reference_manifest()["tables"].items()):
            report("values", table=table, checked_tables=len(tables))
            for column in specification["columns"]:
                if column["name"].endswith("_json"):
                    name = _quote(column["name"])
                    if (
                        conn.execute(
                            f"SELECT 1 FROM {_quote(table)} WHERE {name} IS NOT NULL AND NOT json_valid({name}) LIMIT 1"
                        ).fetchone()
                        is not None
                    ):
                        raise ImportRejected(table + "." + column["name"] + ": malformed JSON")
            count, checksum = _row_digest(_sqlite_rows(conn, table, physical=True))
            tables[table] = {"rows": count, "sha256": checksum}
    report("validated", checked_tables=len(tables))
    return {
        "format": FORMAT,
        "source": str(path),
        "source_sha256": sha256,
        "schema_fingerprint": hashlib.sha256(
            json.dumps(actual, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
        "tables": tables,
        "files": file_receipt,
        "complete": True,
    }


def _copy_cell(copy: Any, field: Cell) -> None:
    if field.tag == b"N":
        copy.write(b"\\N")
    elif field.tag == b"T":
        for block in field.blocks():
            copy.write(
                block.replace(b"\\", b"\\\\")
                .replace(b"\t", b"\\t")
                .replace(b"\n", b"\\n")
                .replace(b"\r", b"\\r")
            )
    else:
        copy.write(repr(field.value).encode("ascii"))


def audit(source: Path, conn: Any, *, expected_sha256: str | None = None) -> dict[str, Any]:
    """Independent readers hash their own values with the same typed encoding."""
    path, source_hash = _checkpoint(source, expected_sha256)
    tables = {}
    with _source(path) as reader, conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for table in sorted(schema.reference_manifest()["tables"]):
            expected = _row_digest(_sqlite_rows(reader, table, physical=True))
            found = _row_digest(_postgres_rows(conn, table, physical=True))
            if expected != found:
                raise ImportRejected(f"{table}: row count or typed value digest differs")
            tables[table] = {"rows": found[0], "sha256": found[1]}
    return {"source_sha256": source_hash, "tables": tables, "equal": True}


def load(
    source: Path,
    conn: Any,
    *,
    expected_sha256: str | None = None,
    receipt_path: Path | None = None,
    environment: str = "dev",
    stop: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Load a fresh database; restart only the table whose transaction was killed.

    The receipt is outside application data. Workers have no grants until the
    final audit and all constraints pass. A failed completed import is discarded
    by its caller; interrupted per-table loads can resume the same frozen source.
    """
    started = time.monotonic()
    checked = preflight(source, expected_sha256=expected_sha256)
    return _load_checked(
        checked,
        conn,
        receipt_path=receipt_path,
        environment=environment,
        stop=stop,
        started=started,
    )


def load_into(
    source: Path,
    destination: Callable[[], AbstractContextManager[Any]],
    *,
    expected_sha256: str | None = None,
    receipt_path: Path | None = None,
    environment: str = "dev",
    stop: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Validate before opening the owned destination session, then load and audit.

    Full-size SQLite validation can outlive a relay's idle timeout. Keep no
    destination session idle during it, and never reconnect during a load.
    """
    started = time.monotonic()

    def progress(value: dict[str, Any]) -> None:
        if receipt_path is not None:
            from swingset.fetch.archive import canonical, durable_write

            # Diagnostic only: this file never authorizes skipping validation
            # or replaces the native load/audit receipt during a resume.
            path = receipt_path.with_name(receipt_path.stem + "-preflight.json")
            durable_write(path, canonical(value))

    checked = preflight(source, expected_sha256=expected_sha256, progress=progress)
    with destination() as conn:
        return _load_checked(
            checked,
            conn,
            receipt_path=receipt_path,
            environment=environment,
            stop=stop,
            started=started,
        )


def _load_checked(
    checked: dict[str, Any],
    conn: Any,
    *,
    receipt_path: Path | None,
    environment: str,
    stop: Callable[[], bool],
    started: float,
) -> dict[str, Any]:
    path = Path(checked["source"])
    previous = (
        json.loads(receipt_path.read_text())
        if receipt_path is not None and receipt_path.exists()
        else None
    )
    receipt = {**checked, "complete": False, "loaded": {}, "audit": None}
    if previous is not None:
        if (
            previous.get("source_sha256") != checked["source_sha256"]
            or previous.get("schema_fingerprint") != checked["schema_fingerprint"]
            or previous.get("migration_checksum") != schema.migration_checksum()
        ):
            raise ImportRejected("resuming import requires the exact frozen source and migration")
        receipt["loaded"] = previous.get("loaded", {})
    receipt["migration_checksum"] = schema.migration_checksum()

    def record() -> None:
        if receipt_path is not None:
            from swingset.fetch.archive import canonical, durable_write

            durable_write(receipt_path, canonical(receipt))

    phases = schema.migration_phases()
    if previous is None:
        from .postgres_bootstrap import initialize_tables

        with conn.transaction():
            try:
                initialize_tables(conn)
            except RuntimeError as exc:
                raise ImportRejected(
                    "destination application database is not empty or prepared: " + str(exc)
                ) from exc
    record()
    with _source(path) as reader:
        for table in sorted(schema.reference_manifest()["tables"]):
            if stop():
                receipt["seconds"] = round(time.monotonic() - started, 3)
                record()
                return receipt
            if table in receipt["loaded"]:
                with conn.transaction():
                    if _row_digest(_postgres_rows(conn, table, physical=True)) != (
                        checked["tables"][table]["rows"],
                        checked["tables"][table]["sha256"],
                    ):
                        raise ImportRejected(
                            f"{table}: completed import table changed before resume"
                        )
                continue
            columns = [
                *schema.semantic_columns(table),
                *(["rowid"] if table in schema.ROWID_TABLES else []),
            ]
            with conn.transaction():
                conn.execute("TRUNCATE " + schema.qualified(table))
                with conn.cursor().copy(
                    "COPY "
                    + schema.qualified(table)
                    + " ("
                    + ",".join(map(_quote, columns))
                    + ") FROM STDIN WITH(FORMAT text)"
                ) as copy:
                    for cells in _sqlite_rows(reader, table, physical=True):
                        for index, cell in enumerate(cells):
                            if index:
                                copy.write(b"\t")
                            _copy_cell(copy, cell)
                        copy.write(b"\n")
                count, checksum = _row_digest(_postgres_rows(conn, table, physical=True))
                if (count, checksum) != (
                    checked["tables"][table]["rows"],
                    checked["tables"][table]["sha256"],
                ):
                    raise ImportRejected(f"{table}: loaded values differ from the source")
            receipt["loaded"][table] = {"rows": count, "sha256": checksum}
            record()
        _reset_sequences(reader, conn)
    finalized = conn.execute(
        "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal AND n.nspname=ANY(%s)",
        (sorted(set(schema.TABLE_NAMESPACES.values())),),
    ).fetchone()[0]
    if finalized:
        schema.validate_schema(conn, full=True)
    else:
        with conn.transaction():
            conn.execute(phases["constraints"], prepare=False)
            conn.execute(phases["triggers"], prepare=False)
            if phases["forward"]:
                conn.execute(phases["forward"], prepare=False)
            schema.seed_environment(conn, environment)
            schema.stamp_schema(conn)
            schema.verify(conn)
    receipt["audit"] = audit(path, conn, expected_sha256=checked["source_sha256"])
    with conn.transaction():
        schema.grant_roles(conn)
    # Native ANALYZE uses per-table transactions outside an explicit block.
    # Committed catalog ACLs cannot deadlock with automatic statistics updates.
    conn.execute("ANALYZE")
    receipt.update(complete=True, seconds=round(time.monotonic() - started, 3))
    record()
    return receipt


def _reset_sequences(source: sqlite3.Connection, conn: Any) -> None:
    sqlite_marks = dict(source.execute("SELECT name,seq FROM sqlite_sequence"))
    for table, specification in schema.reference_manifest()["tables"].items():
        identities = [
            c["name"]
            for c in specification["columns"]
            if c["pk"] and c["type"] == "INTEGER" and len(_keys(table)) == 1
        ]
        if table in schema.ROWID_TABLES:
            identities.append("rowid")
        for column in identities:
            maximum = conn.execute(
                f"SELECT coalesce(max({_quote(column)}),0) FROM {schema.qualified(table)}"
            ).fetchone()[0]
            position = max(int(maximum), int(sqlite_marks.get(table, 0)))
            conn.execute(
                "SELECT setval(pg_get_serial_sequence(%s,%s),%s,%s)",
                (schema.qualified(table), column, max(position, 1), position > 0),
            )
