"""Export a read-only historical year-review from the completed parser-8 scratch."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

SOURCE = Path("/nix/store/rgyryll4d55rgzscdqhjcwmkr325a76f-source")
SOURCE_RECEIPT_SHA256 = "a2704c7c99ce5aef4fbd0f0df8b23343be5df2a688945e236b8e6856b320ad5f"
REPLAY_RECEIPT_SHA256 = "65f3627429d2ae47f3e5b1216d16c9ee19e365a583abbd21a954d885379765e2"
SUCCESSOR_LEDGER_SHA256 = "6db1bf303401537b2927ffbbaa5046e222d20c87cbb7ae75cf5860368289773c"
CATALOG_SHA256 = "0355e34d69e23fc5a7096e07a99d420d4353bdb4fc59284497483ae856d084d9"
PREDECESSOR_LEDGER_SHA256 = "370ad76d175e4e4fa9cf344ec2d47268338dbba128194ecef65904a74632653c"
SCHEMA = 29
APPLICATION_TABLES = 117
SCRATCH_ROOT = Path("/var/tmp")
SCRATCH_PREFIX = "swingset-schema29-newsletter-replay-"
OUTPUT_ROOT = Path("/var/tmp")
OUTPUT_PREFIX = "swingset-phase1-year-review-"
PRODUCTION_ROOT = Path("/var/lib/swingset")
REVIEW_FILES = frozenset(
    {
        "events.csv",
        "findings.csv",
        "occurrences.csv",
        "review.json",
        "series-review.csv",
        "targets.csv",
        "years.csv",
    }
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_pinned_json(path: Path, expected: str, label: str) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), label + " must be a regular file")
    require(sha(path) == expected, label + " hash differs")
    value = json.loads(path.read_bytes())
    require(isinstance(value, dict), label + " must be a JSON object")
    return cast(dict[str, Any], value)


def install_offline_audit_hook() -> None:
    denied = frozenset(
        {
            "os.fork",
            "os.forkpty",
            "os.posix_spawn",
            "os.spawn",
            "os.system",
            "pty.spawn",
            "subprocess.Popen",
        }
    )

    def audit(event: str, _args: tuple[object, ...]) -> None:
        if event.startswith("socket.") or event.startswith("os.exec") or event in denied:
            raise RuntimeError("offline year-review export denied audit event: " + event)

    sys.addaudithook(audit)


def verify_runtime_modules(*modules: object) -> None:
    root = (SOURCE / "src").resolve(strict=True)
    for module in modules:
        name = str(getattr(module, "__name__", "unknown"))
        module_path = getattr(module, "__file__", None)
        require(
            isinstance(module_path, str) and Path(module_path).resolve().is_relative_to(root),
            "mixed imported runtime module: " + name,
        )


def runtime_bound() -> None:
    verify_runtime_modules(
        *(
            module
            for name, module in tuple(sys.modules.items())
            if name == "swingset" or name.startswith("swingset.")
        )
    )


def runtime_path_injected(root: Path) -> bool:
    for entry in tuple(sys.path):
        try:
            raw = os.fsdecode(os.fspath(entry))
            path = Path.cwd() if raw == "" else Path(raw)
            if path.resolve() == root:
                return True
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    return False


def load_runtime() -> tuple[Callable[..., dict[str, Any]], type[Any]]:
    require(SOURCE.is_dir() and not SOURCE.is_symlink(), "candidate-006 source is unavailable")
    receipt = SOURCE / "extension-source.json"
    require(
        receipt.is_file() and not receipt.is_symlink() and sha(receipt) == SOURCE_RECEIPT_SHA256,
        "candidate-006 source receipt differs",
    )
    require(not os.environ.get("SWINGSET_REVISION"), "repository identity override is forbidden")
    runtime_bound()
    root = (SOURCE / "src").resolve(strict=True)
    require(not runtime_path_injected(root), "candidate runtime path already injected")
    sys.path.insert(0, str(root))
    review = importlib.import_module("swingset.history.review")
    db = importlib.import_module("swingset.state.db")
    runtime_bound()
    require(db.SCHEMA_VERSION == SCHEMA, "imported schema is not 29")
    return review.review_pack, db.Database


def verify_state_path(state: Path, output: Path) -> tuple[Path, Path]:
    require(state.is_absolute(), "scratch path must be absolute")
    require(state.is_dir() and not state.is_symlink(), "scratch must be a real directory")
    resolved = state.resolve(strict=True)
    production = PRODUCTION_ROOT.resolve()
    require(not resolved.is_relative_to(production), "production state is forbidden")
    root = SCRATCH_ROOT.resolve(strict=True)
    require(
        resolved.parent == root and resolved.name.startswith(SCRATCH_PREFIX),
        "state must be the explicit disposable parser-8 scratch",
    )
    require(output.is_absolute(), "output path must be absolute")
    require(not output.exists() and not output.is_symlink(), "output directory must be new")
    require(
        output.parent.is_dir() and not output.parent.is_symlink(),
        "output parent must be a real directory",
    )
    resolved_output = output.parent.resolve(strict=True) / output.name
    require(not resolved_output.is_relative_to(production), "production output is forbidden")
    output_root = OUTPUT_ROOT.resolve(strict=True)
    require(
        resolved_output.parent == output_root and resolved_output.name.startswith(OUTPUT_PREFIX),
        "output must be a fresh /var/tmp year-review path",
    )
    require(
        not resolved_output.is_relative_to(resolved)
        and not resolved.is_relative_to(resolved_output),
        "output and scratch paths must be separate",
    )
    return resolved, resolved_output


def verify_inputs(state: Path) -> tuple[dict[str, Any], bytes, bytes]:
    catalog_path = state / "phase1-catalog.json"
    predecessor_path = state / "phase1-ledger.json"
    successor_path = state / "phase1-ledger-parser8.json"
    replay_path = state / "phase1-newsletter-parser8-receipt.json"
    require(
        catalog_path.is_file()
        and predecessor_path.is_file()
        and successor_path.is_file()
        and not any(path.is_symlink() for path in (catalog_path, predecessor_path, successor_path)),
        "phase-one inputs must be regular files",
    )
    require(sha(catalog_path) == CATALOG_SHA256, "phase-one catalog hash differs")
    require(
        sha(predecessor_path) == PREDECESSOR_LEDGER_SHA256,
        "predecessor phase-one ledger hash differs",
    )
    require(sha(successor_path) == SUCCESSOR_LEDGER_SHA256, "successor ledger hash differs")
    replay = read_pinned_json(replay_path, REPLAY_RECEIPT_SHA256, "completed replay receipt")
    expected_tables = replay.get("table_hashes", {}).get("after")
    require(
        replay.get("format") == "phase1-newsletter-parser8-replay-v1"
        and replay.get("passed") is True
        and replay.get("source") == str(SOURCE)
        and replay.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and replay.get("catalog", {}).get("sha256") == CATALOG_SHA256
        and replay.get("ledger_before_sha256") == PREDECESSOR_LEDGER_SHA256
        and replay.get("ledger_after_sha256") == SUCCESSOR_LEDGER_SHA256
        and replay.get("original_ledger_preserved") is True
        and len(replay.get("targets", ())) == 28
        and replay.get("invariants", {}).get("after", {}).get("accepted_year_count") == 0
        and replay.get("network_requests") == 0
        and replay.get("subprocesses") == 0
        and replay.get("production_operations") == 0
        and replay.get("production_acceptance") is False
        and replay.get("year_acceptance") is False
        and replay.get("publication") is False
        and re.fullmatch(r"run_[0-9TZ-]+", str(replay.get("replay_run_id", ""))) is not None,
        "completed replay receipt authority differs",
    )
    require(
        isinstance(expected_tables, dict)
        and len(expected_tables) == APPLICATION_TABLES
        and all(
            isinstance(table, str)
            and re.fullmatch(r"[a-z_][a-z0-9_]*", table) is not None
            and isinstance(value, dict)
            and isinstance(value.get("rows"), int)
            and value["rows"] >= 0
            and re.fullmatch(r"[0-9a-f]{64}", str(value.get("sha256", ""))) is not None
            for table, value in expected_tables.items()
        ),
        "completed replay table-hash closure differs",
    )
    catalog = json.loads(catalog_path.read_bytes())
    successor = json.loads(successor_path.read_bytes())
    require(
        isinstance(catalog, dict)
        and catalog.get("version") == 1
        and len(catalog.get("targets", ())) == 213,
        "phase-one catalog closure differs",
    )
    require(
        isinstance(successor, dict)
        and successor.get("version") == 1
        and len(successor.get("targets", ())) == 213,
        "successor ledger closure differs",
    )
    return replay, catalog_path.read_bytes(), successor_path.read_bytes()


def database_closure(state: Path) -> dict[str, dict[str, Any]]:
    database = state / "state.sqlite"
    require(database.is_file() and not database.is_symlink(), "scratch database differs")
    result: dict[str, dict[str, Any]] = {}
    for path in (database, state / "state.sqlite-wal", state / "state.sqlite-shm"):
        if not path.exists() and not path.is_symlink():
            continue
        require(path.is_file() and not path.is_symlink(), "SQLite sidecar differs: " + path.name)
        result[path.name] = {"bytes": path.stat().st_size, "sha256": sha(path)}
    return result


def _normalize(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, float):
        if math.isnan(value):
            return {"float": "nan"}
        if math.isinf(value):
            return {"float": "+infinity" if value > 0 else "-infinity"}
        if value == 0:
            return 0.0
    return value


def table_hash(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    """Use the exact streamed table-hash-v1 algorithm sealed by parser replay."""
    require(re.fullmatch(r"[a-z_][a-z0-9_]*", table) is not None, "unsafe table name")
    schema = [
        [_normalize(value) for value in row]
        for row in conn.execute(f'PRAGMA table_xinfo("{table}")')
    ]
    require(bool(schema), "table schema is missing: " + table)
    columns = tuple(str(row[1]) for row in schema)
    primary = tuple(
        str(row[1]) for row in sorted(schema, key=lambda item: int(item[5])) if int(row[5])
    )

    def quote(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    projection = ",".join(quote(column) for column in columns)
    ordering_terms = [quote(column) for column in primary]
    for column in columns:
        quoted = quote(column)
        ordering_terms.extend(
            (
                f"CASE typeof({quoted}) WHEN 'null' THEN 0 WHEN 'integer' THEN 1 "
                "WHEN 'real' THEN 2 WHEN 'text' THEN 3 WHEN 'blob' THEN 4 ELSE 5 END",
                f"CASE WHEN typeof({quoted}) IN ('integer','real') THEN {quoted} END",
                f"(CASE WHEN typeof({quoted})='text' THEN {quoted} END) COLLATE BINARY",
                f"(CASE WHEN typeof({quoted})='blob' THEN hex({quoted}) END) COLLATE BINARY",
                f"quote({quoted}) COLLATE BINARY",
            )
        )
    cursor = conn.execute(f'SELECT {projection} FROM "{table}" ORDER BY {",".join(ordering_terms)}')
    digest = hashlib.sha256()

    def update(value: object) -> None:
        body = canonical(value)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)

    update({"format": "streamed-table-hash-v1", "schema": schema})
    rows = 0
    for row in cursor:
        update([_normalize(value) for value in row])
        rows += 1
    update({"rows": rows})
    return {"rows": rows, "sha256": digest.hexdigest()}


def all_table_hashes(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    return {table: table_hash(conn, table) for table in sorted(tables)}


@contextlib.contextmanager
def scratch_lock(state: Path) -> Iterator[None]:
    lock_path = state / "state.lock"
    require(lock_path.is_file() and not lock_path.is_symlink(), "scratch lock file differs")
    with lock_path.open("rb") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("scratch is already in use") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def deny_sql_writes(conn: sqlite3.Connection) -> None:
    write_actions = frozenset(
        action
        for action in (
            getattr(sqlite3, name, -1)
            for name in (
                "SQLITE_ALTER_TABLE",
                "SQLITE_ATTACH",
                "SQLITE_CREATE_INDEX",
                "SQLITE_CREATE_TABLE",
                "SQLITE_CREATE_TEMP_INDEX",
                "SQLITE_CREATE_TEMP_TABLE",
                "SQLITE_CREATE_TEMP_TRIGGER",
                "SQLITE_CREATE_TEMP_VIEW",
                "SQLITE_CREATE_TRIGGER",
                "SQLITE_CREATE_VIEW",
                "SQLITE_DELETE",
                "SQLITE_DETACH",
                "SQLITE_DROP_INDEX",
                "SQLITE_DROP_TABLE",
                "SQLITE_DROP_TEMP_INDEX",
                "SQLITE_DROP_TEMP_TABLE",
                "SQLITE_DROP_TEMP_TRIGGER",
                "SQLITE_DROP_TEMP_VIEW",
                "SQLITE_DROP_TRIGGER",
                "SQLITE_DROP_VIEW",
                "SQLITE_INSERT",
                "SQLITE_REINDEX",
                "SQLITE_UPDATE",
            )
        )
        if action >= 0
    )

    def authorize(
        action: int,
        _arg1: str | None,
        _arg2: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        return sqlite3.SQLITE_DENY if action in write_actions else sqlite3.SQLITE_OK

    conn.set_authorizer(authorize)


def open_read_only_database(state: Path) -> sqlite3.Connection:
    uri = (state / "state.sqlite").resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    deny_sql_writes(conn)
    return conn


@contextlib.contextmanager
def wal_aware_database_copy(
    state: Path, destination: Path, expected: dict[str, dict[str, Any]]
) -> Iterator[Path]:
    """Copy the closed scratch bytes so WAL-aware SQLite cannot add source sidecars."""
    destination.mkdir(mode=0o700)
    try:
        for name in ("state.sqlite", "state.sqlite-wal"):
            source = state / name
            if name not in expected:
                require(not source.exists() and not source.is_symlink(), "SQLite closure changed")
                continue
            target = destination / name
            with source.open("rb") as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            require(
                target.stat().st_size == expected[name]["bytes"]
                and sha(target) == expected[name]["sha256"],
                "disposable SQLite copy differs: " + name,
            )
        yield destination
    finally:
        if destination.exists() and not destination.is_symlink():
            shutil.rmtree(destination)


def verify_database(conn: sqlite3.Connection, replay: dict[str, Any]) -> tuple[str, str]:
    schema = int(conn.execute("PRAGMA user_version").fetchone()[0])
    meta = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    tables = int(
        conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    )
    accepted = int(conn.execute("SELECT COUNT(*) FROM history_acceptance").fetchone()[0])
    require(schema == SCHEMA and meta is not None and int(meta[0]) == SCHEMA, "schema differs")
    require(tables == APPLICATION_TABLES, "application table count differs")
    require(accepted == 0, "historical year acceptance is not empty")
    expected_tables = cast(dict[str, dict[str, Any]], replay["table_hashes"]["after"])
    actual_tables = all_table_hashes(conn)
    differences = tuple(
        sorted(
            table
            for table in expected_tables.keys() | actual_tables.keys()
            if expected_tables.get(table) != actual_tables.get(table)
        )
    )
    require(
        not differences,
        "scratch differs from completed replay tables: " + ",".join(differences[:16]),
    )
    run_id = str(replay["replay_run_id"])
    row = conn.execute("SELECT started_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
    require(row is not None and isinstance(row[0], str), "replay run is missing")
    return str(row[0]), hashlib.sha256(canonical(actual_tables)).hexdigest()


def hash_review_outputs(review_dir: Path) -> dict[str, dict[str, Any]]:
    files = {path.name for path in review_dir.iterdir() if path.is_file() and not path.is_symlink()}
    require(files == REVIEW_FILES, "year-review output closure differs")
    require(
        all(not path.is_symlink() and path.is_file() for path in review_dir.iterdir()),
        "year-review output contains a link or directory",
    )
    return {
        name: {"bytes": (review_dir / name).stat().st_size, "sha256": sha(review_dir / name)}
        for name in sorted(files)
    }


def verify_review(report: dict[str, Any], review_dir: Path, generated_at: str) -> None:
    stored = json.loads((review_dir / "review.json").read_bytes())
    require(stored == report, "stored year-review report differs")
    years = report.get("years")
    require(
        report.get("generated_at") == generated_at
        and report.get("run_id") == "read-only-review"
        and report.get("catalog_targets") == 213
        and isinstance(years, list)
        and [row.get("year") for row in years] == list(range(2010, 2027))
        and all(row.get("events_accepted") is False for row in years)
        and report.get("acceptance")
        == "Owner review is required. This pack does not set events_accepted or authorize phase2.",
        "year-review authority differs",
    )


def write_new(path: Path, body: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def export(state: Path, output: Path) -> dict[str, Any]:
    state, output = verify_state_path(state, output)
    replay, catalog_body, successor_body = verify_inputs(state)
    review_pack, database_type = load_runtime()
    output.mkdir(mode=0o700)
    try:
        input_dir = output / ".review-input"
        input_dir.mkdir(mode=0o700)
        write_new(input_dir / "phase1-catalog.json", catalog_body)
        write_new(input_dir / "phase1-ledger.json", successor_body)
        review_dir = output / "year-review"
        with scratch_lock(state):
            before = database_closure(state)
            with wal_aware_database_copy(state, output / ".database-copy", before) as copy:
                conn = open_read_only_database(copy)
                try:
                    generated_at, table_hashes_sha256 = verify_database(conn, replay)
                    database = database_type(input_dir, conn, None)
                    report = review_pack(
                        database,
                        review_dir,
                        now=generated_at,
                        reconcile=False,
                    )
                    require(isinstance(report, dict), "year-review result differs")
                    verify_review(report, review_dir, generated_at)
                finally:
                    conn.close()
            after = database_closure(state)
            require(before == after, "read-only export changed the SQLite closure")
        shutil.rmtree(input_dir)
        outputs = hash_review_outputs(review_dir)
        receipt = {
            "format": "phase1-year-review-export-v1",
            "passed": True,
            "source": str(SOURCE),
            "source_receipt_sha256": SOURCE_RECEIPT_SHA256,
            "replay_receipt_sha256": REPLAY_RECEIPT_SHA256,
            "catalog_sha256": CATALOG_SHA256,
            "predecessor_ledger_sha256": PREDECESSOR_LEDGER_SHA256,
            "successor_ledger_sha256": SUCCESSOR_LEDGER_SHA256,
            "schema": SCHEMA,
            "application_tables": APPLICATION_TABLES,
            "history_acceptance_rows": 0,
            "table_hashes": {
                "rows": APPLICATION_TABLES,
                "sha256": table_hashes_sha256,
            },
            "review_generated_at": generated_at,
            "review_years": [2010, 2026],
            "outputs": outputs,
            "database": before,
            "network_requests": 0,
            "subprocesses": 0,
            "production_operations": 0,
            "year_acceptance": False,
            "publication": False,
        }
        write_new(output / "receipt.json", canonical(receipt) + b"\n")
        return receipt
    except BaseException:
        if output.exists() and not output.is_symlink():
            shutil.rmtree(output)
        raise


def main() -> None:
    sys.dont_write_bytecode = True
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    install_offline_audit_hook()
    print(json.dumps(export(args.state, args.output), indent=2))


if __name__ == "__main__":
    main()
