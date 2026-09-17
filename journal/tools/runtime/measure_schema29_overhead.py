"""Measure schema29 dispatch-fence trigger cost on one disposable database copy.

This bounded paired microbenchmark isolates schema29 no-op update trigger
cost with all older triggers disabled in both variants. It does not measure
trigger interactions, parsing, acquisition, durable commit cost, or fleet service.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import shutil
import sqlite3
import statistics
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

SOURCE = Path("/nix/store/z0rnsn69aav2h8p2wgzydar6k9rw5kk9-source")
SOURCE_RECEIPT_SHA256 = "9255e8641a24c21c0942512ec251b32dd294bc6f2a537868a0012cafe331dc99"
MIGRATION_RELATIVE = "src/swingset/state/migrations/0029_history_dispatch_fence.sql"
MIGRATION_SHA256 = "ba2f46accc102636d8268dfbfaf08a766e34aa6fbb369cf30ee465de81b739d4"
PACKET_SHA256 = "6748af985bbe7b6a74c30c094c72cc117fd6f259a661fb5aeb052e51e309d230"
MIGRATION_RECEIPT_SHA256 = "b2721d9b3920af9f54e4bc1504bac200a76f72f95dd95a84a21565427d73b97f"
TRIGGER = re.compile(
    r"CREATE\s+TRIGGER\s+([A-Za-z0-9_]+)\s+AFTER\s+"
    r"(INSERT|UPDATE|DELETE)\s+ON\s+([A-Za-z0-9_]+)",
    re.IGNORECASE,
)
TRIGGER_PREFIX = "history_timing_"
MIN_COHORT = 10
MAX_COHORT = 96
MAX_ROUNDS = 10_000
MAX_SAMPLES = 15
MAX_UPDATES_PER_SAMPLE = 25_000
MAX_TOTAL_UPDATES = 250_000
MAX_SPECIMEN_DB_BYTES = 6 * 1024**3
EXPECTED_HOLD_SHA256 = "965468bb03ba65b6d1d4950ab8e85c09c8f2f2a323461b7a3f2b6177bba5c638"


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def quoted(identifier: str) -> str:
    require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier) is not None, "unsafe SQL name")
    return '"' + identifier + '"'


def _normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).casefold()


def migration_triggers(sql: str) -> dict[str, dict[str, str]]:
    """Return each declared name and normalized exact trigger definition."""
    triggers: dict[str, dict[str, str]] = {}
    names: set[str] = set()
    statements = re.findall(r"\bCREATE\s+TRIGGER\b.*?\bEND\s*;", sql, re.IGNORECASE | re.DOTALL)
    for statement in statements:
        match = TRIGGER.search(statement)
        require(match is not None, "could not parse migration trigger header")
        assert match is not None
        name, action, table = match.groups()
        require(name.startswith(TRIGGER_PREFIX), "unexpected migration trigger name")
        require(name not in names, "duplicate migration trigger name")
        names.add(name)
        table_triggers = triggers.setdefault(table, {})
        actions = {item.rsplit("_", 1)[-1].upper() for item in table_triggers}
        require(action.upper() not in actions, "duplicate trigger action for table")
        table_triggers[name] = _normalize_sql(statement)
    require(bool(triggers), "migration declares no dispatch-fence triggers")
    return triggers


def trigger_names(conn: sqlite3.Connection) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE ? ORDER BY name",
        (TRIGGER_PREFIX + "%",),
    )
    return tuple(str(row[0]) for row in rows)


def trigger_definitions(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(name): _normalize_sql(str(sql))
        for name, sql in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE ? ORDER BY name",
            (TRIGGER_PREFIX + "%",),
        )
    }


def all_trigger_definitions(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(name): _normalize_sql(str(sql))
        for name, sql in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
        )
    }


@contextmanager
def dispatch_only_boundary(conn: sqlite3.Connection, dispatch_names: set[str]) -> Any:
    """Temporarily drop all older triggers and restore the exact inventory on rollback."""
    original = all_trigger_definitions(conn)
    conn.execute("SAVEPOINT schema29_overhead_run")
    try:
        for name in sorted(set(original) - dispatch_names):
            conn.execute(f"DROP TRIGGER {quoted(name)}")
        require(
            all_trigger_definitions(conn) == {name: original[name] for name in dispatch_names},
            "non-schema29 triggers were not isolated",
        )
        yield
    except BaseException:
        conn.execute("ROLLBACK TO schema29_overhead_run")
        conn.execute("RELEASE schema29_overhead_run")
        require(
            all_trigger_definitions(conn) == original,
            "full trigger inventory changed after exceptional rollback",
        )
        raise
    else:
        conn.execute("ROLLBACK TO schema29_overhead_run")
        conn.execute("RELEASE schema29_overhead_run")
        require(
            all_trigger_definitions(conn) == original,
            "full trigger inventory changed after rollback",
        )


def fixed_cohort(
    conn: sqlite3.Connection, trigger_tables: set[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pick one existing row from every nonempty dispatch target table."""
    table_modes = {
        str(row[1]): bool(row[4]) for row in conn.execute("PRAGMA table_list") if row[2] == "table"
    }
    cohort: list[dict[str, Any]] = []
    skipped: list[str] = []
    for table in sorted(trigger_tables):
        metadata = list(conn.execute(f"PRAGMA table_info({quoted(table)})"))
        if not metadata:
            skipped.append(table + ":no_columns")
            continue
        columns = [str(row[1]) for row in metadata]
        if table_modes.get(table, False):
            key_columns = [
                str(row[1])
                for row in sorted(metadata, key=lambda item: int(item[5]))
                if int(row[5])
            ]
        else:
            key_columns = ["rowid"]
        require(bool(key_columns), f"target table {table} has no stable row key")
        selected = ",".join(quoted(column) for column in key_columns)
        ordering = ",".join(quoted(column) for column in key_columns)
        row = conn.execute(
            f"SELECT {selected} FROM {quoted(table)} ORDER BY {ordering} LIMIT 1"
        ).fetchone()
        if row is None:
            skipped.append(table + ":empty")
            continue
        cohort.append(
            dict(
                table=table,
                key_columns=key_columns,
                key_values=[row[index] for index in range(len(key_columns))],
                column=columns[0],
            )
        )
    require(len(cohort) >= MIN_COHORT, f"nonempty cohort has fewer than {MIN_COHORT} tables")
    require(len(cohort) <= MAX_COHORT, "cohort exceeds fixed bound")
    return cohort, skipped


def _fence_revision(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT singleton,revision FROM history_dispatch_fence ORDER BY singleton"
    ).fetchall()
    require(len(rows) == 1 and int(rows[0][0]) == 1, "dispatch fence shape differs")
    return int(rows[0][1])


def _row_predicate(item: dict[str, Any]) -> str:
    return " AND ".join(f"{quoted(column)}=?" for column in item["key_columns"])


def _row_parameters(item: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(item["key_values"])


def _read_cohort_row(conn: sqlite3.Connection, item: dict[str, Any]) -> tuple[Any, ...] | None:
    row = conn.execute(
        f"SELECT * FROM {quoted(item['table'])} WHERE {_row_predicate(item)}",
        _row_parameters(item),
    ).fetchone()
    return cast(tuple[Any, ...] | None, row)


def _timed_updates(
    conn: sqlite3.Connection,
    cohort: list[dict[str, Any]],
    rounds: int,
    *,
    disable_dispatch_triggers: bool,
    expected_delta: int,
) -> int:
    """Time only repeated no-op UPDATE statements, rolling every change back."""
    conn.execute("SAVEPOINT schema29_overhead_sample")
    try:
        original_names = trigger_names(conn)
        fence_before = _fence_revision(conn)
        if disable_dispatch_triggers:
            for name in original_names:
                conn.execute(f"DROP TRIGGER {quoted(name)}")
            require(not trigger_names(conn), "dispatch triggers remained disabled incompletely")
        start = time.perf_counter_ns()
        for _ in range(rounds):
            for item in cohort:
                table, column = quoted(item["table"]), quoted(item["column"])
                conn.execute(
                    f"UPDATE {table} SET {column}={column} WHERE {_row_predicate(item)}",
                    _row_parameters(item),
                )
        elapsed = time.perf_counter_ns() - start
        delta = _fence_revision(conn) - fence_before
        require(
            delta == expected_delta, f"dispatch fence delta {delta} != expected {expected_delta}"
        )
    finally:
        conn.execute("ROLLBACK TO schema29_overhead_sample")
        conn.execute("RELEASE schema29_overhead_sample")
    require(trigger_names(conn) == original_names, "trigger inventory changed after rollback")
    return elapsed


def paired_measurement(
    conn: sqlite3.Connection,
    migration_sql: str,
    *,
    rounds: int = 200,
    samples: int = 7,
) -> dict[str, Any]:
    """Compare only schema29 dispatch triggers inside a rollback-only boundary."""
    declared = migration_triggers(migration_sql)
    expected_definitions = {
        name: definition
        for table_triggers in declared.values()
        for name, definition in table_triggers.items()
    }
    require(
        trigger_definitions(conn) == expected_definitions,
        "database trigger definitions differ from migration",
    )
    original_trigger_count = len(all_trigger_definitions(conn))
    with dispatch_only_boundary(conn, set(expected_definitions)):
        result = _paired_measurement_with_older_triggers_disabled(
            conn, migration_sql, rounds=rounds, samples=samples
        )
    result["preexisting_trigger_count"] = original_trigger_count - len(expected_definitions)
    result["preexisting_triggers_disabled_in_both_variants"] = True
    result["trigger_inventory_restored"] = True
    return result


def _paired_measurement_with_older_triggers_disabled(
    conn: sqlite3.Connection,
    migration_sql: str,
    *,
    rounds: int = 200,
    samples: int = 7,
) -> dict[str, Any]:
    """Compare the same populated rows with v29 triggers off and on."""
    require(1 <= rounds <= MAX_ROUNDS, "round count outside bound")
    require(3 <= samples <= MAX_SAMPLES, "sample count outside bound")
    declared = migration_triggers(migration_sql)
    existing = trigger_names(conn)
    expected = tuple(sorted(name for name in existing if name.startswith(TRIGGER_PREFIX)))
    require(len(expected) > 0, "database has no schema29 dispatch triggers")
    tables = set(declared)
    require(bool(tables), "empty migration target set")
    actual_definitions = trigger_definitions(conn)
    expected_definitions = {
        name: definition
        for table_triggers in declared.values()
        for name, definition in table_triggers.items()
    }
    require(
        actual_definitions == expected_definitions,
        "database trigger definitions differ from migration",
    )
    actual_table_triggers: dict[str, dict[str, str]] = {str(table): {} for table in tables}
    for name, table in conn.execute(
        "SELECT name,tbl_name FROM sqlite_master WHERE type='trigger' AND name LIKE ?",
        (TRIGGER_PREFIX + "%",),
    ):
        require(str(table) in actual_table_triggers, "unexpected schema29 trigger table")
        actual_table_triggers[str(table)][str(name)] = actual_definitions[str(name)]
    require(actual_table_triggers == declared, "database trigger coverage differs from migration")
    cohort, skipped = fixed_cohort(conn, tables)
    start_revision = _fence_revision(conn)
    before_rows = {
        (item["table"], tuple(item["key_values"])): _read_cohort_row(conn, item) for item in cohort
    }
    baseline: list[int] = []
    candidate: list[int] = []
    order: list[str] = []
    updates_per_sample = rounds * len(cohort)
    require(updates_per_sample <= MAX_UPDATES_PER_SAMPLE, "updates per sample exceed bound")
    require(updates_per_sample * samples * 2 <= MAX_TOTAL_UPDATES, "total updates exceed bound")
    for index in range(samples):
        variants = ("baseline", "candidate") if index % 2 == 0 else ("candidate", "baseline")
        for variant in variants:
            elapsed = _timed_updates(
                conn,
                cohort,
                rounds,
                disable_dispatch_triggers=variant == "baseline",
                expected_delta=0 if variant == "baseline" else updates_per_sample,
            )
            order.append(variant)
            (baseline if variant == "baseline" else candidate).append(elapsed)
            require(
                _fence_revision(conn) == start_revision, "sample rollback changed fence revision"
            )
    after_rows = {
        (item["table"], tuple(item["key_values"])): _read_cohort_row(conn, item) for item in cohort
    }
    require(before_rows == after_rows, "sample rollback changed a fixed cohort row")
    baseline_median = statistics.median(baseline)
    candidate_median = statistics.median(candidate)
    writes = updates_per_sample
    return {
        "cohort": cohort,
        "cohort_skipped": skipped,
        "target_table_count": len(tables),
        "migration_trigger_count": len(expected),
        "rounds_per_sample": rounds,
        "updates_per_sample": writes,
        "samples": samples,
        "sample_order": order,
        "baseline_elapsed_ns": baseline,
        "candidate_elapsed_ns": candidate,
        "baseline_median_ns_per_update": baseline_median / writes,
        "candidate_median_ns_per_update": candidate_median / writes,
        "median_overhead_ns_per_update": (candidate_median - baseline_median) / writes,
        "median_overhead_ratio": candidate_median / baseline_median if baseline_median else None,
        "candidate_p95_ns_per_update": sorted(candidate)[min(samples - 1, int(samples * 0.95))]
        / writes,
        "fence_revision_before_and_after": start_revision,
        "rollback_verified": True,
    }


def copy_database(source: Path, destination: Path) -> str:
    """Copy one closed WAL-backed specimen to a new file while hashing the stream."""
    require(not destination.exists(), "working database must be new")
    require(source.stat().st_size <= MAX_SPECIMEN_DB_BYTES, "specimen exceeds copy byte bound")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(source) + suffix)
        require(not sidecar.exists() or sidecar.stat().st_size == 0, f"nonempty source {suffix}")
    digest = hashlib.sha256()
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        while block := input_stream.read(1024 * 1024):
            digest.update(block)
            output_stream.write(block)
        output_stream.flush()
    return digest.hexdigest()


def validate_pins(source: Path, receipt_sha: str, migration_receipt: Path) -> dict[str, Any]:
    require(source == SOURCE, "source path is not candidate005")
    source_receipt = source / "extension-source.json"
    require(sha(source_receipt) == SOURCE_RECEIPT_SHA256 == receipt_sha, "source receipt differs")
    inventory = json.loads(source_receipt.read_bytes())["files"]
    recorded_migration = inventory[MIGRATION_RELATIVE]
    expected_migration = (
        recorded_migration["sha256"] if isinstance(recorded_migration, dict) else recorded_migration
    )
    migration_path = source / MIGRATION_RELATIVE
    require(
        sha(migration_path) == expected_migration == MIGRATION_SHA256, "schema29 migration differs"
    )
    require(sha(migration_receipt) == MIGRATION_RECEIPT_SHA256, "migration receipt differs")
    report = cast(dict[str, Any], json.loads(migration_receipt.read_bytes()))
    require(
        report.get("passed") is True
        and report.get("target_schema") == 29
        and report.get("source") == str(source)
        and report.get("source_receipt_sha256") == SOURCE_RECEIPT_SHA256
        and report.get("old_schema") == 28,
        "migration specimen receipt does not bind candidate005 schema29",
    )
    return report


def validate_isolated_paths(
    specimen: Path, source: Path, scratch: Path, output: Path
) -> tuple[Path, Path, Path, Path]:
    specimen = specimen.resolve(strict=True)
    source = source.resolve(strict=True)
    scratch_parent = scratch.parent.resolve(strict=True)
    output_parent = output.parent.resolve(strict=False)
    scratch = scratch_parent / scratch.name
    output = output_parent / output.name
    require(
        scratch.is_relative_to(Path("/var/tmp").resolve(strict=True)),
        "scratch must be under /var/tmp",
    )
    require(not scratch.exists(), "scratch directory must be new")
    require(not output.exists(), "receipt path must be new")
    protected_roots = (specimen, source, Path("/var/lib/swingset").resolve(strict=False))
    for root in protected_roots:
        require(
            not scratch.is_relative_to(root) and not root.is_relative_to(scratch),
            "scratch overlaps protected state",
        )
        require(
            not output.is_relative_to(root) and not root.is_relative_to(output),
            "receipt overlaps protected state",
        )
    require(
        not output.is_relative_to(scratch) and not scratch.is_relative_to(output),
        "receipt overlaps scratch",
    )
    require(
        not scratch.is_symlink() and not output.is_symlink(), "scratch/output cannot be symlinks"
    )
    return specimen, source, scratch, output


def run(
    source: Path,
    receipt_sha: str,
    migration_receipt: Path,
    specimen: Path,
    scratch: Path,
    output: Path,
    rounds: int,
    samples: int,
) -> dict[str, Any]:
    require(not specimen.is_symlink() and specimen.is_dir(), "specimen must be a real directory")
    specimen, source, scratch, output = validate_isolated_paths(specimen, source, scratch, output)
    require(
        specimen == Path("/var/tmp/swingset-schema29-migration-20260917-001"),
        "specimen path differs from reviewed migration",
    )
    prior = validate_pins(source, receipt_sha, migration_receipt)
    require(
        sha(specimen / "migration-receipt.json") == MIGRATION_RECEIPT_SHA256,
        "specimen-local receipt differs",
    )
    require(
        prior.get("destination") in {None, str(specimen)}, "migration specimen destination differs"
    )
    scratch.mkdir(mode=0o700, parents=True)
    source_db = specimen / "state.sqlite"
    require(
        source_db.is_file() and not source_db.is_symlink() and source_db.stat().st_nlink == 1,
        "specimen database must be an unlinked regular file",
    )
    source_db_sha_before = sha(source_db)
    scratch_db = scratch / "state.sqlite"
    copied_sha = copy_database(source_db, scratch_db)
    require(copied_sha == source_db_sha_before, "source changed while database copy was made")
    require(
        not scratch_db.is_symlink() and scratch_db.stat().st_nlink == 1,
        "scratch database must be a new unlinked file",
    )
    source_hold = specimen / "operator-hold"
    require(
        source_hold.is_file() and not source_hold.is_symlink() and source_hold.stat().st_nlink == 1,
        "specimen hold must be an unlinked regular file",
    )
    hold_sha = sha(source_hold)
    shutil.copyfile(specimen / "operator-hold", scratch / "operator-hold")
    require(
        (scratch / "operator-hold").is_file() and not (scratch / "operator-hold").is_symlink(),
        "scratch hold must be regular",
    )
    with sqlite3.connect(f"file:{scratch_db}?mode=ro", uri=True) as raw:
        require(
            raw.execute("PRAGMA user_version").fetchone()[0] == 29,
            "copied specimen pragma is not schema29",
        )
        raw_meta = raw.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        require(
            raw_meta is not None and str(raw_meta[0]) == "29",
            "copied specimen meta schema is not29",
        )
        require(
            raw.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            == 117,
            "copied specimen table count differs",
        )
    import sys

    sys.path.insert(0, str(source / "src"))
    runtime_db = importlib.import_module("swingset.state.db")
    open_database = runtime_db.open_database

    require(
        Path(open_database.__code__.co_filename).resolve().is_relative_to(source / "src"),
        "runtime import escaped candidate source",
    )
    sql = (source / MIGRATION_RELATIVE).read_text()
    start = datetime.now(UTC).isoformat()
    with open_database(scratch, lock=True, lock_timeout=0) as database:
        require(database.schema_version == 29, "scratch database is not schema29")
        require(
            database.connection.execute("PRAGMA user_version").fetchone()[0] == 29,
            "scratch pragma schema differs",
        )
        report = paired_measurement(database.connection, sql, rounds=rounds, samples=samples)
        report["scratch_hold_sha256"] = sha(scratch / "operator-hold")
        require(
            report["scratch_hold_sha256"] == hold_sha == EXPECTED_HOLD_SHA256,
            "scratch hold differs",
        )
    report.update(
        format="schema29-dispatch-trigger-overhead-v1",
        started_at=start,
        finished_at=datetime.now(UTC).isoformat(),
        source=str(source),
        source_receipt_sha256=receipt_sha,
        migration_sql_sha256=MIGRATION_SHA256,
        rehearsal_packet_sha256=PACKET_SHA256,
        migration_receipt_sha256=sha(migration_receipt),
        migration_specimen=str(specimen),
        specimen_receipt_sha256=sha(specimen / "migration-receipt.json"),
        specimen_database_sha256=copied_sha,
        specimen_database_sha256_before=source_db_sha_before,
        specimen_database_bytes=source_db.stat().st_size,
        scratch_database_sha256_before=copied_sha,
        scratch_database_sha256_after=sha(scratch_db),
        scratch=str(scratch),
        production_operations=0,
        network_requests=0,
        publication=False,
        input_acceptance=False,
        repairs_activated=False,
        passed=True,
        scope=(
            "paired no-op UPDATEs on one existing row in each nonempty schema29 target table; "
            "all preexisting non-schema29 triggers are disabled in both variants, while only "
            "the exact history_timing_* trigger set differs between variants"
        ),
        limitations=[
            "This is an isolated schema29 dispatch-trigger diagnostic; it excludes interaction with preexisting triggers and their costs, durable commit/fsync cost, parsing, selection and network service.",
            "Empty target tables cannot provide an existing row and are listed in cohort_skipped; every nonempty target table is measured.",
            "This microbenchmark does not establish sustained replay throughput or fixed-cohort acquisition service.",
        ],
    )
    require(
        report["scratch_database_sha256_after"] == copied_sha,
        "scratch database bytes changed after rollback",
    )
    require(
        sha(source_db) == source_db_sha_before,
        "migration specimen database changed during measurement",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-receipt-sha256", required=True)
    parser.add_argument("--migration-receipt", type=Path, required=True)
    parser.add_argument("--specimen", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=200)
    parser.add_argument("--samples", type=int, default=7)
    args = parser.parse_args()
    report = run(
        args.source,
        args.source_receipt_sha256,
        args.migration_receipt,
        args.specimen,
        args.scratch,
        args.output,
        args.rounds,
        args.samples,
    )
    print(json.dumps({"passed": report["passed"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
