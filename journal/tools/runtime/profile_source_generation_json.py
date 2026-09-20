"""Profile repeated structure in source-generation JSON on a disposable state copy.

The profiler opens SQLite with ``mode=ro&immutable=1`` and refuses the live
state root, a nonempty WAL, or any shared-memory sidecar. It retains only
aggregate counts and byte sizes for four fixed document columns and three
fixed array paths. Other than the required page-kind and state cohort labels,
source bodies, scalar values, URLs, identities, digests, and JSON key names
never enter either output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import socket
import sqlite3
import sys
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOL_VERSION = "profile-source-generation-json-v2"
REPORT_FORMAT = "source-generation-json-profile-v2"
RECEIPT_FORMAT = "source-generation-json-profile-receipt-v2"
PROTECTED_ROOTS = ("/var/lib/swingset",)
DOCUMENT_COLUMNS = ("manifest_json", "recipe_json", "result_json", "report_json")
STRUCTURAL_PATHS = (
    ("recipe_json.accepted_inputs[]", "recipe_json", "accepted_inputs"),
    ("report_json.fields[]", "report_json", "fields"),
    ("report_json.guards[]", "report_json", "guards"),
)
DEFAULT_MAX_ROWS = 100_000
DEFAULT_MAX_BYTES = 2_000_000_000
DEFAULT_MAX_ROW_BYTES = 4_000_000


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def signature(path: Path) -> dict[str, int]:
    """Cheap source identity and unchanged check: size, mtime, and inode."""
    status = path.stat()
    return {"bytes": status.st_size, "mtime_ns": status.st_mtime_ns, "inode": status.st_ino}


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def rendered(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resolve_database(target: Path) -> tuple[Path, Path]:
    target = target.expanduser()
    database = target if target.suffix in {".sqlite", ".db"} else target / "state.sqlite"
    require(database.is_file(), f"no state database at {database}")
    require(not database.is_symlink(), "state database must not be a symlink")
    state_dir = database.parent.resolve(strict=True)
    for root in PROTECTED_ROOTS:
        for guarded in {Path(root), Path(root).resolve(strict=False)}:
            require(
                not state_dir.is_relative_to(guarded),
                f"refusing {state_dir}: {root} is the live state. Profile a disposable copy.",
            )
    return state_dir, database.resolve(strict=True)


def resolve_destination(path: Path, label: str, state_dir: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    for root in PROTECTED_ROOTS:
        for guarded in {Path(root), Path(root).resolve(strict=False)}:
            require(
                not resolved.is_relative_to(guarded),
                f"refusing to write {label} under {root}: use scratch storage.",
            )
    require(not resolved.is_relative_to(state_dir), f"{label} must be outside the measured state")
    require(not state_dir.is_relative_to(resolved), f"{label} must not contain the measured state")
    require(not resolved.exists(), f"{label} must be new; {resolved} already exists")
    return resolved


def sidecar_state(database: Path) -> dict[str, int | bool]:
    result: dict[str, int | bool] = {}
    for name, suffix in (("wal", "-wal"), ("shm", "-shm"), ("journal", "-journal")):
        path = Path(str(database) + suffix)
        result[f"{name}_exists"] = path.exists()
        result[f"{name}_bytes"] = path.stat().st_size if path.exists() else 0
    return result


def require_closed_sidecars(database: Path, phase: str) -> dict[str, int | bool]:
    """Refuse state an immutable reader could silently omit."""
    sidecars = sidecar_state(database)
    require(
        sidecars["wal_bytes"] == 0,
        f"database has a nonempty write-ahead log {phase}; an immutable reader would ignore it. "
        "Close every connection to the copy, or run PRAGMA wal_checkpoint(TRUNCATE) on it.",
    )
    require(
        not sidecars["shm_exists"],
        f"database has a shared-memory file {phase}, so a connection is open on it. "
        "Profile a closed disposable copy.",
    )
    require(
        not sidecars["journal_exists"],
        f"database has a rollback journal {phase}, so it is not a closed disposable copy.",
    )
    return sidecars


def open_readonly(database: Path) -> sqlite3.Connection:
    require_closed_sidecars(database, "before opening")
    connection = sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def source_kind(state_dir: Path) -> str:
    return "checkpoint" if (state_dir / "checkpoint.json").is_file() else "state"


def meta_schema_version(connection: sqlite3.Connection) -> int | None:
    present = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='meta'"
    ).fetchone()
    if present is None:
        return None
    row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row is not None else None


def runtime_metadata() -> dict[str, str]:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
        "zlib_compile_version": zlib.ZLIB_VERSION,
        "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION,
    }


def compression_metadata() -> dict[str, Any]:
    return {
        "algorithm": "zlib",
        "level": 6,
        "framing": (
            "Each source document and each canonical structural item is compressed as one "
            "independent zlib stream. Aggregate fields sum those stream byte lengths; unique "
            "fields sum one stream per raw-byte-distinct document or canonical-distinct item."
        ),
    }


def _byte_length_sql(column: str) -> str:
    return f"length(CAST({column} AS BLOB))"


def _preflight(
    connection: sqlite3.Connection, *, max_rows: int, max_bytes: int, max_row_bytes: int
) -> dict[str, int]:
    for name, value in (
        ("max_rows", max_rows),
        ("max_bytes", max_bytes),
        ("max_row_bytes", max_row_bytes),
    ):
        require(value > 0, f"{name} must be positive")
    columns = {
        str(row[1]): str(row[2]).upper()
        for row in connection.execute("PRAGMA table_info(source_generations)")
    }
    require(bool(columns), "source_generations table is absent")
    for column in ("page_kind", "state", *DOCUMENT_COLUMNS):
        require(column in columns, f"source_generations.{column} is absent")
    lengths = [_byte_length_sql(column) for column in DOCUMENT_COLUMNS]
    total = " + ".join(lengths)
    row = connection.execute(
        f"SELECT COUNT(*),COALESCE(SUM({total}),0),COALESCE(MAX({total}),0) FROM source_generations"
    ).fetchone()
    assert row is not None
    result = {"rows": int(row[0]), "logical_utf8_bytes": int(row[1]), "max_row_bytes": int(row[2])}
    require(result["rows"] <= max_rows, f"row cap exceeded: {result['rows']} > {max_rows}")
    require(
        result["logical_utf8_bytes"] <= max_bytes,
        f"byte cap exceeded: {result['logical_utf8_bytes']} > {max_bytes}",
    )
    require(
        result["max_row_bytes"] <= max_row_bytes,
        f"row-byte cap exceeded: {result['max_row_bytes']} > {max_row_bytes}",
    )
    non_text = connection.execute(
        "SELECT COUNT(*) FROM source_generations WHERE "
        + " OR ".join(f"typeof({column}) != 'text'" for column in DOCUMENT_COLUMNS)
    ).fetchone()
    require(non_text is not None and int(non_text[0]) == 0, "JSON columns must contain text")
    return result


def _empty_document_profile() -> dict[str, int]:
    return {
        "rows": 0,
        "logical_utf8_bytes": 0,
        "distinct_documents": 0,
        "unique_logical_utf8_bytes": 0,
        "duplicate_logical_utf8_bytes": 0,
        "canonical_byte_identical_rows": 0,
        "zlib6_bytes": 0,
        "unique_zlib6_bytes": 0,
    }


def _empty_path_profile(path: str) -> dict[str, Any]:
    return {
        "path": path,
        "rows_with_array": 0,
        "rows_missing": 0,
        "rows_wrong_type": 0,
        "occurrences": 0,
        "logical_utf8_bytes": 0,
        "distinct_items": 0,
        "unique_logical_utf8_bytes": 0,
        "zlib6_bytes": 0,
        "unique_zlib6_bytes": 0,
    }


def _documents(connection: sqlite3.Connection) -> Iterator[sqlite3.Row]:
    yield from connection.execute(
        "SELECT page_kind,state,manifest_json,recipe_json,result_json,report_json "
        "FROM source_generations ORDER BY generation_id"
    )


def _profile_rows(
    connection: sqlite3.Connection,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    documents = {column: _empty_document_profile() for column in DOCUMENT_COLUMNS}
    paths = {path: _empty_path_profile(path) for path, _, _ in STRUCTURAL_PATHS}
    path_unique: dict[str, set[bytes]] = {path: set() for path, _, _ in STRUCTURAL_PATHS}
    cohorts: dict[tuple[str, str], dict[str, Any]] = {}
    for ordinal, row in enumerate(_documents(connection), start=1):
        decoded: dict[str, Any] = {}
        cohort_key = (str(row["page_kind"]), str(row["state"]))
        cohort = cohorts.setdefault(
            cohort_key,
            {
                "page_kind": cohort_key[0],
                "state": cohort_key[1],
                "rows": 0,
                "documents": {
                    column: {"logical_utf8_bytes": 0, "zlib6_bytes": 0}
                    for column in DOCUMENT_COLUMNS
                },
            },
        )
        cohort["rows"] += 1
        for column in DOCUMENT_COLUMNS:
            raw = str(row[column]).encode()
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid JSON in {column} at row {ordinal}") from error
            decoded[column] = value
            compressed = len(zlib.compress(raw, level=6))
            profile = documents[column]
            profile["rows"] += 1
            profile["logical_utf8_bytes"] += len(raw)
            profile["zlib6_bytes"] += compressed
            profile["canonical_byte_identical_rows"] += canonical(value) == raw
            cohort["documents"][column]["logical_utf8_bytes"] += len(raw)
            cohort["documents"][column]["zlib6_bytes"] += compressed
        for path, column, key in STRUCTURAL_PATHS:
            profile = paths[path]
            container = decoded[column]
            if not isinstance(container, dict) or key not in container:
                profile["rows_missing"] += 1
                continue
            items = container[key]
            if not isinstance(items, list):
                profile["rows_wrong_type"] += 1
                continue
            profile["rows_with_array"] += 1
            for item in items:
                body = canonical(item)
                profile["occurrences"] += 1
                profile["logical_utf8_bytes"] += len(body)
                profile["zlib6_bytes"] += len(zlib.compress(body, level=6))
                path_unique[path].add(body)
    for path, _, _ in STRUCTURAL_PATHS:
        unique = path_unique[path]
        paths[path]["distinct_items"] = len(unique)
        paths[path]["unique_logical_utf8_bytes"] = sum(map(len, unique))
        paths[path]["unique_zlib6_bytes"] = sum(
            len(zlib.compress(body, level=6)) for body in unique
        )
    return documents, list(paths.values()), [cohorts[key] for key in sorted(cohorts)]


def _profile_distinct_documents(connection: sqlite3.Connection, profiles: dict[str, Any]) -> None:
    for column in DOCUMENT_COLUMNS:
        distinct = 0
        unique_bytes = 0
        unique_zlib = 0
        expression = f"CAST({column} AS BLOB)"
        for row in connection.execute(
            f"SELECT {expression} FROM source_generations GROUP BY {expression}"
        ):
            body = bytes(row[0])
            distinct += 1
            unique_bytes += len(body)
            unique_zlib += len(zlib.compress(body, level=6))
        profile = profiles[column]
        profile["distinct_documents"] = distinct
        profile["unique_logical_utf8_bytes"] = unique_bytes
        profile["duplicate_logical_utf8_bytes"] = profile["logical_utf8_bytes"] - unique_bytes
        profile["unique_zlib6_bytes"] = unique_zlib


def _dbstat(connection: sqlite3.Connection, database: Path) -> dict[str, int]:
    table = connection.execute(
        "SELECT COALESCE(SUM(pgsize),0),COUNT(*) FROM dbstat WHERE name='source_generations'"
    ).fetchone()
    indexes = connection.execute(
        "SELECT COALESCE(SUM(pgsize),0),COUNT(*) FROM dbstat WHERE name IN "
        "(SELECT name FROM sqlite_schema WHERE type='index' AND tbl_name='source_generations')"
    ).fetchone()
    assert table is not None and indexes is not None
    return {
        "database_file_bytes": database.stat().st_size,
        "page_size": int(connection.execute("PRAGMA page_size").fetchone()[0]),
        "page_count": int(connection.execute("PRAGMA page_count").fetchone()[0]),
        "freelist_count": int(connection.execute("PRAGMA freelist_count").fetchone()[0]),
        "source_generations_table_bytes": int(table[0]),
        "source_generations_table_pages": int(table[1]),
        "source_generations_index_bytes": int(indexes[0]),
        "source_generations_index_pages": int(indexes[1]),
    }


def profile(
    target: Path,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_row_bytes: int = DEFAULT_MAX_ROW_BYTES,
    command: list[str] | None = None,
) -> dict[str, Any]:
    state_dir, database = resolve_database(target)
    tool_path = Path(__file__).resolve()
    tool_sha256 = file_sha256(tool_path)
    before_sidecars = require_closed_sidecars(database, "before profiling")
    before = signature(database)
    connection = open_readonly(database)
    try:
        schema_version = meta_schema_version(connection)
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        preflight = _preflight(
            connection,
            max_rows=max_rows,
            max_bytes=max_bytes,
            max_row_bytes=max_row_bytes,
        )
        documents, paths, cohorts = _profile_rows(connection)
        _profile_distinct_documents(connection, documents)
        storage = _dbstat(connection, database)
    finally:
        connection.close()
    after_sidecars = require_closed_sidecars(database, "after profiling")
    after = signature(database)
    require(after == before, "measured database changed while it was read")
    require(file_sha256(tool_path) == tool_sha256, "profiler code changed while it was running")
    return {
        "format": REPORT_FORMAT,
        "tool_version": TOOL_VERSION,
        "tool_sha256": tool_sha256,
        "measured_at": datetime.now(UTC).isoformat(),
        "source": {
            "root_path": str(state_dir),
            "database_path": str(database),
            "kind": source_kind(state_dir),
            "schema_version": schema_version,
            "user_version": user_version,
            "signature_before": before,
            "signature_after": after,
            "unchanged": True,
            "sidecars_before": before_sidecars,
            "sidecars_after": after_sidecars,
        },
        "invocation": {"command": command},
        "runtime": runtime_metadata(),
        "compression": compression_metadata(),
        "caps": {
            "max_rows": max_rows,
            "max_bytes": max_bytes,
            "max_row_bytes": max_row_bytes,
        },
        "preflight": preflight,
        "documents": documents,
        "structural_paths": paths,
        "cohorts": cohorts,
        "dbstat": storage,
        "limits": [
            "The profile covers one closed disposable copy at one moment.",
            "Logical and zlib byte counts do not predict SQLite page savings.",
            "Only fixed document columns, fixed array paths, and page-kind/state cohorts are read.",
            "Other than page-kind and state cohort labels, no source-data body, scalar value, "
            "URL, identity, digest, or JSON key name is retained in the report or receipt.",
            "SQLite may use automatically managed temporary storage containing source documents "
            "for GROUP BY. The tool does not select or retain that temporary storage.",
            "No production operation or network request is performed.",
        ],
    }


def build_receipt(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": RECEIPT_FORMAT,
        "tool_version": report["tool_version"],
        "tool_sha256": report["tool_sha256"],
        "measured_at": report["measured_at"],
        "source": report["source"],
        "invocation": report["invocation"],
        "runtime": report["runtime"],
        "compression": report["compression"],
        "caps": report["caps"],
        "preflight": report["preflight"],
        "documents": report["documents"],
        "structural_paths": report["structural_paths"],
        "dbstat": report["dbstat"],
        "cohort_count": len(report["cohorts"]),
        "limits": report["limits"],
    }


def run(
    target: Path,
    *,
    output: Path,
    receipt: Path,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_row_bytes: int = DEFAULT_MAX_ROW_BYTES,
    command: list[str] | None = None,
) -> dict[str, Any]:
    state_dir, _ = resolve_database(target)
    output = resolve_destination(output, "--output", state_dir)
    receipt = resolve_destination(receipt, "--receipt", state_dir)
    require(output != receipt, "--output and --receipt must be different paths")
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    report = profile(
        target,
        max_rows=max_rows,
        max_bytes=max_bytes,
        max_row_bytes=max_row_bytes,
        command=command,
    )
    with output.open("x") as handle:
        handle.write(rendered(report))
    with receipt.open("x") as handle:
        handle.write(rendered(build_receipt(report)))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state", type=Path, required=True, help="disposable state directory or database"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="new path for the full JSON report"
    )
    parser.add_argument(
        "--receipt", type=Path, required=True, help="new path for the compact JSON receipt"
    )
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-row-bytes", type=int, default=DEFAULT_MAX_ROW_BYTES)
    args = parser.parse_args()
    report = run(
        args.state,
        output=args.output,
        receipt=args.receipt,
        max_rows=args.max_rows,
        max_bytes=args.max_bytes,
        max_row_bytes=args.max_row_bytes,
        command=list(sys.argv),
    )
    print(
        json.dumps(
            {
                "format": report["format"],
                "rows": report["preflight"]["rows"],
                "output": str(args.output),
                "receipt": str(args.receipt),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
