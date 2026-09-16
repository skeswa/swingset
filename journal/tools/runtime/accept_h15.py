"""Bounded H15 schema 13→14 migration acceptance; no service or source execution.

Default is read-only preflight. --execute is a coordinator-only migration and
read-only verification, using the exact frozen source and separately hashed gate.
"""

import argparse
import fcntl
import hashlib
import json
import signal
import sqlite3
import subprocess
import sys
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from journal.tools.runtime import accept_h11 as prior
from swingset import cli
from swingset.publish.service import pending_candidates
from swingset.state import db as db_module
from swingset.state.requirement_report import human_report

BASE_SOURCE_RECEIPT_SHA256 = "e70f7f1d29534836a06a14af1422cd609e871998f610643e5e67ed0c20554822"
PRIVATE_BACKUP_COMMIT = "a845bd78006fbc30f7bdf6ad1580d35fdec71a8c"
PRIVATE_BACKUP_MANIFEST = "f1ade4f3db6a29cf329c01de43db4540e0bd3d6768030fc7ff5fe56d9a275268"
V4_COMMIT = "81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653"
NEW_TABLES = {
    "derivation_scopes",
    "derivation_dependency_sets",
    "derivation_generations",
    "derivation_rows",
    "derivation_input_versions",
}


def integrity(conn: sqlite3.Connection) -> None:
    if [tuple(row) for row in conn.execute("PRAGMA quick_check")] != [("ok",)]:
        raise ValueError("database quick_check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("database foreign-key integrity failed")


def verify_runtime(source: Path, gate: dict[str, Any], expected_receipt: str) -> None:
    receipt = source / "h15-source.json"
    if (
        gate.get("source_receipt_sha256") != expected_receipt
        or prior.digest(receipt) != expected_receipt
    ):
        raise ValueError("frozen H15 source receipt differs")
    assembly = json.loads(receipt.read_bytes())
    if (
        assembly.get("format") != "h15-reviewed-source-v1"
        or assembly.get("schema") != 14
        or assembly.get("acquisition_enabled") is not False
        or assembly.get("repairs_activated") is not False
    ):
        raise ValueError("expected reviewed schema 14 H15 source without activation")
    prior.verify_files(source, assembly["files"])
    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path != source / "h15-source.json"
    }
    if actual != set(assembly["files"]):
        raise ValueError("frozen source closure differs")
    if (
        db_module.SCHEMA_VERSION != 14
        or Path(db_module.__file__).resolve() != (source / "src/swingset/state/db.py").resolve()
        or Path(prior.__file__).resolve()
        != (source / "journal/tools/runtime/accept_h11.py").resolve()
    ):
        raise ValueError("runtime and H11 helpers must come from the frozen schema 14 source")


def verify_backup(state: Path, gate_path: Path, gate: dict[str, Any]) -> dict[str, Any]:
    checkpoint = Path(gate["checkpoint"]).resolve()
    if (
        gate.get("private_backup_commit") != PRIVATE_BACKUP_COMMIT
        or gate.get("checkpoint_manifest_sha256") != PRIVATE_BACKUP_MANIFEST
    ):
        raise ValueError("expected the exact verified H14 predecessor backup")
    if prior.digest(checkpoint / "checkpoint.json") != gate["checkpoint_manifest_sha256"]:
        raise ValueError("private checkpoint identity differs")
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    if (
        manifest.get("schema_version") != 13
        or manifest.get("pending_candidate") is not None
        or manifest.get("baseline_candidate") != (state / "baseline").resolve().name
    ):
        raise ValueError("private backup must retain this V4 baseline at schema 13")
    actual = {
        path.relative_to(checkpoint).as_posix()
        for path in checkpoint.rglob("*")
        if path.is_file() and path != checkpoint / "checkpoint.json"
    }
    if actual != set(manifest["files"]):
        raise ValueError("checkpoint closure differs, including WAL/SHM sidecars")
    prior.verify_checkpoint_files(checkpoint, manifest["files"])
    receipt_name = gate["private_backup_receipt"]
    if receipt_name not in gate["evidence_files"]:
        raise ValueError("private backup receipt must be retained hashed evidence")
    backup = json.loads((gate_path.parent / receipt_name).read_bytes())
    if (
        backup.get("commit") != gate["private_backup_commit"]
        or backup.get("manifest_hash") != gate["checkpoint_manifest_sha256"]
        or Path(backup.get("checkpoint", "")).resolve() != checkpoint
        or backup.get("files") != len(manifest["files"])
        or not backup.get("finished_at")
        or backup.get("schema_version") != 13
        or backup.get("baseline_commit") != V4_COMMIT
        or backup.get("source_receipt_sha256") != BASE_SOURCE_RECEIPT_SHA256
    ):
        raise ValueError("verified private backup receipt does not bind this checkpoint")
    with closing(
        sqlite3.connect((checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as saved:
        integrity(saved)
        snapshot = protected_state(saved)
        if snapshot["schema"] != 13:
            raise ValueError("checkpoint database must be schema 13")
    return snapshot


def baseline(state: Path, gate: dict[str, Any]) -> dict[str, Any]:
    result = {
        "commit": json.loads((state / "baseline/PUBLISHED").read_bytes())["commit"],
        "manifest_sha256": prior.digest(state / "baseline/_meta/manifest.json"),
    }
    if (
        result != {"commit": V4_COMMIT, "manifest_sha256": gate["v4_manifest_sha256"]}
        or gate.get("v4_commit") != V4_COMMIT
    ):
        raise ValueError("current public baseline differs from verified V4")
    prior.verify_files(
        state / "baseline",
        json.loads((state / "baseline/_meta/manifest.json").read_bytes())["files"],
    )
    return result


def validate_output(output: Path, state: Path, source: Path, gate: Path) -> None:
    prior.validate_output_paths(output, state, source, gate)
    if any(
        output.resolve().is_relative_to((state / name).resolve())
        for name in ("blobs", "extracts", "inputs")
    ):
        raise ValueError("receipt output cannot enter retained artifact roots")
    assembly = json.loads((source / "h15-source.json").read_bytes())
    for key in ("base", "reviewed"):
        if assembly.get(key) and output.resolve().is_relative_to(Path(assembly[key]).resolve()):
            raise ValueError("receipt output cannot enter a source tree")


def doctor_check(state: Path, source: Path, output: Path) -> dict[str, Any]:
    instant = datetime.now(UTC)
    with patch.object(cli, "SystemClock", lambda: SimpleNamespace(now=lambda: instant)):
        report = cli.doctor(argparse.Namespace(state=state, config=source / "config"))
    human = human_report(report["requirements"])
    if not prior.human_matches(report["requirements"], human):
        raise ValueError("doctor human and JSON requirement inventories differ")
    code = (
        "import argparse,hashlib,json,sys; from pathlib import Path; from datetime import datetime; "
        "from types import SimpleNamespace; from swingset import cli; from swingset.state import db; "
        "assert db.SCHEMA_VERSION==14 and Path(db.__file__).resolve()==Path(sys.argv[4]).resolve(); "
        "cli.SystemClock=lambda:SimpleNamespace(now=lambda:datetime.fromisoformat(sys.argv[3])); "
        "report=cli.doctor(argparse.Namespace(state=Path(sys.argv[1]),config=Path(sys.argv[2]))); "
        "print(json.dumps({'sha256':hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'schema':report['schema_version']}))"
    )
    restarted = json.loads(
        subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(state),
                str(source / "config"),
                instant.isoformat(),
                str(source / "src/swingset/state/db.py"),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        ).stdout
    )
    report_digest = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if restarted != {"sha256": report_digest, "schema": 14} or report["schema_version"] != 14:
        raise ValueError("fresh-process doctor differs or reads an unexpected schema")
    output.with_suffix(".doctor.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    output.with_suffix(".doctor.txt").write_text(human + "\n")
    return {
        "fixed_time": instant.isoformat(),
        "fresh_process_equal": True,
        "requirements_human_json_equal": True,
        "report_sha256": report_digest,
        "json_bytes": output.with_suffix(".doctor.json").stat().st_size,
        "human_bytes": output.with_suffix(".doctor.txt").stat().st_size,
    }


def protected_state(
    conn: sqlite3.Connection, columns: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    """Compare original columns, retaining every prior control and attempt row."""
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if columns is None:
        columns = {
            name: [row[1] for row in conn.execute(f'PRAGMA table_info("{name}")')]
            for name in sorted(names - NEW_TABLES)
        }
    tables = {}
    for name, original in columns.items():
        if not name.replace("_", "").isalnum() or any(
            not column.replace("_", "").isalnum() for column in original
        ):
            raise ValueError("unexpected schema identifier")
        actual = {row[1] for row in conn.execute(f'PRAGMA table_info("{name}")')}
        if not original or not set(original).issubset(actual):
            raise ValueError("migration removed original columns")
        projection = ",".join(f'"{column}"' for column in original)
        order = ",".join(str(index + 1) for index in range(len(original)))
        where = " WHERE key!='schema_version'" if name == "meta" else ""
        tables[name] = prior.query_digest(
            conn, f'SELECT {projection} FROM "{name}"{where} ORDER BY {order}'
        )
    if names - NEW_TABLES != set(columns):
        raise ValueError("unexpected prior table closure")
    return {
        "schema": conn.execute("PRAGMA user_version").fetchone()[0],
        "columns": columns,
        "tables": tables,
        "backup_neutral_meta": prior.query_digest(
            conn,
            "SELECT key,value FROM meta WHERE key!='schema_version' AND key NOT LIKE 'last_backup%' ORDER BY key",
        ),
    }


def verify_migration(conn: sqlite3.Connection) -> dict[str, Any]:
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not NEW_TABLES.issubset(names):
        raise ValueError("missing H15 migration tables")
    for table in NEW_TABLES - {"derivation_scopes", "derivation_input_versions"}:
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            raise ValueError("migration fabricated derivation output or dependency proof")
    if conn.execute(
        "SELECT 1 FROM snapshots WHERE extract_recipe_sha256 IS NOT NULL LIMIT 1"
    ).fetchone():
        raise ValueError("migration fabricated snapshot recipe proof")
    if conn.execute(
        "SELECT 1 FROM derivation_scopes WHERE desired_fingerprint IS NOT NULL OR materialized_generation_id IS NOT NULL OR materialized_signature IS NOT NULL LIMIT 1"
    ).fetchone():
        raise ValueError("migration fabricated materialized or desired fingerprints")
    expected = prior.query_digest(
        conn,
        """SELECT stage,unit_kind,unit_id FROM pending_work WHERE stage IN ('project','link')
        UNION SELECT 'project',scope_kind,scope_id FROM observations WHERE scope_kind IN ('calendar','source_index','dancer','event','source_event')
        UNION SELECT 'project',scope_kind,scope_id FROM canonical_scope_rows WHERE scope_kind IN ('calendar','source_index','dancer','event','source_event')
        UNION SELECT 'project','event',event_id FROM events
        UNION SELECT 'link','event',event_id FROM events ORDER BY 1,2,3""",
    )
    actual = prior.query_digest(
        conn, "SELECT stage,unit_kind,unit_id FROM derivation_scopes ORDER BY 1,2,3"
    )
    if expected != actual:
        raise ValueError("seeded scope inventory differs from retained work and evidence")
    if conn.execute(
        "SELECT 1 FROM derivation_scopes d JOIN pending_work p USING(stage,unit_kind,unit_id) WHERE d.registered_at!=p.enqueued_at LIMIT 1"
    ).fetchone():
        raise ValueError("migration changed retained pending scope registration times")
    expected_versions = prior.query_digest(
        conn,
        """SELECT 'selected',json_array(d.stage,d.unit_kind),count(*) FROM derivation_scopes d
        WHERE NOT EXISTS(SELECT 1 FROM pending_work p WHERE p.stage=d.stage AND p.unit_kind=d.unit_kind AND p.unit_id=d.unit_id)
        GROUP BY d.stage,d.unit_kind ORDER BY 1,2,3""",
    )
    versions = prior.query_digest(
        conn, "SELECT namespace,input_key,version FROM derivation_input_versions ORDER BY 1,2,3"
    )
    if versions != expected_versions:
        raise ValueError("migration dependency change tokens differ from new scope registrations")
    from swingset.state.publication_fence import CONTROL_TABLES

    expected_triggers = {
        f"publication_fence_{table}_{action}"
        for table in names - CONTROL_TABLES
        if not table.startswith("sqlite_")
        for action in ("insert", "update", "delete")
    }
    actual_triggers = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'publication_fence_%'"
        )
    }
    if actual_triggers != expected_triggers:
        raise ValueError("publication semantic fence coverage differs")
    return {
        "empty_proof_tables": sorted(
            NEW_TABLES - {"derivation_scopes", "derivation_input_versions"}
        ),
        "unmaterialized_seeded_scopes": actual,
        "snapshot_recipe_cache_null": True,
        "dependency_change_tokens": versions,
        "fence_triggers": len(actual_triggers),
    }


# Enumerate retained scope identities independently of legacy enqueue pointers.
# SQL sorts once; Python holds one page, counts, and a bounded sample only.
SCOPE_QUERY = """
WITH roots(stage,unit_kind,unit_id) AS (
 SELECT stage,unit_kind,unit_id FROM derivation_scopes
 UNION SELECT stage,unit_kind,unit_id FROM pending_work WHERE stage IN ('project','link')
 UNION SELECT 'project',scope_kind,scope_id FROM observations WHERE scope_kind IN ('calendar','source_index','dancer','event','source_event','inventory','map','history')
 UNION SELECT 'project',scope_kind,scope_id FROM canonical_scope_rows WHERE scope_kind IN ('calendar','source_index','dancer','event','source_event','inventory','map','history')
 UNION SELECT 'project','source_index',scope_id FROM source_event_scope_rows
 UNION SELECT 'project','event',event_id FROM events
 UNION SELECT 'project','event',event_id FROM source_event_map
), projects(stage,unit_kind,unit_id) AS (
 SELECT * FROM roots WHERE stage='project'
 UNION SELECT 'project',value,'all' FROM json_each('["inventory","map","history"]') WHERE EXISTS(SELECT 1 FROM roots WHERE stage='project' AND unit_kind IN ('inventory','map','history') UNION SELECT 1 FROM observations WHERE scope_kind IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') UNION SELECT 1 FROM canonical_scope_rows WHERE scope_kind IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') UNION SELECT 1 FROM events UNION SELECT 1 FROM registry_placements UNION SELECT 1 FROM source_events)
), scopes(stage,unit_kind,unit_id) AS (
 SELECT * FROM projects
 UNION SELECT * FROM roots WHERE stage='link'
 UNION SELECT 'link','event',event_id FROM events
 UNION SELECT 'link','event',event_id FROM entries
 UNION SELECT 'link','event',event_id FROM judges
 UNION SELECT 'link','event',unit_id FROM projects WHERE unit_kind='event'
 UNION SELECT 'link','event',substr(subject_id,1,instr(subject_id,'/')-1) FROM identity_links WHERE instr(subject_id,'/')>0
)
SELECT s.stage,s.unit_kind,s.unit_id,d.materialized_generation_id
FROM scopes s LEFT JOIN derivation_scopes d USING(stage,unit_kind,unit_id)
ORDER BY s.stage,s.unit_kind,s.unit_id
"""


def retained_assessment(
    conn: sqlite3.Connection, *, page_size: int = 256, max_seconds: float = 60
) -> dict[str, Any]:
    """Bound memory and SQL time; never declare legacy output current."""
    if not 1 <= page_size <= 1000 or not 0 < max_seconds <= 120:
        raise ValueError("invalid retained assessment bounds")
    started = monotonic()
    conn.set_progress_handler(lambda: int(monotonic() - started > max_seconds), 10000)
    counts: dict[str, int] = {}
    sample: list[dict[str, Any]] = []
    pages = 0
    try:
        cursor = conn.execute(SCOPE_QUERY)
        while page := cursor.fetchmany(page_size):
            pages += 1
            for stage, kind, identifier, generation in page:
                if generation is not None:
                    raise ValueError("migration assessment found fabricated materialization")
                key = stage + "/" + kind
                counts[key] = counts.get(key, 0) + 1
                if len(sample) < 20:
                    sample.append(
                        {"stage": stage, "kind": kind, "id": identifier, "state": "unmaterialized"}
                    )
    except sqlite3.OperationalError as error:
        if "interrupted" in str(error):
            raise TimeoutError("retained scope assessment exceeded its bounded window") from error
        raise
    finally:
        conn.set_progress_handler(None, 0)
    return {
        "scope_counts": counts,
        "scopes": sum(counts.values()),
        "pages": pages,
        "page_size": page_size,
        "sample": sample,
        "elapsed_seconds": monotonic() - started,
        "all_unmaterialized": True,
        "desired_fingerprints_computed": 0,
        "service_executed": False,
    }


def size_inventory(state: Path, source: Path) -> dict[str, Any]:
    assembly = json.loads((source / "h15-source.json").read_bytes())
    return {
        "database_bytes": (state / "state.sqlite").stat().st_size,
        "source_files": len(assembly["files"]),
        "source_bytes": sum((source / name).stat().st_size for name in assembly["files"]),
        "doctor_output_policy": "one full settled report plus fresh-process digest; ops output only",
        "assessment_page_size": 256,
    }


def preflight(state: Path, source: Path, gate_path: Path, expected_receipt: str) -> dict[str, Any]:
    if "checkpoints" in state.resolve().parts or (state / "checkpoint.json").exists():
        raise ValueError("never migrate an immutable checkpoint")
    gate = json.loads(gate_path.read_bytes())
    if (
        gate.get("format") != "h15-operational-gate-v1"
        or gate.get("v4_verified") is not True
        or gate.get("private_backup_verified") is not True
        or not gate.get("private_backup_commit")
        or not gate.get("verified_at")
        or not gate.get("evidence_files")
    ):
        raise ValueError("gate requires completed V4 and private H14 backup verification")
    if gate.get("driver_sha256") != prior.digest(Path(__file__)):
        raise ValueError("operations driver differs from reviewed gate")
    prior.verify_files(gate_path.parent, gate["evidence_files"])
    verify_runtime(source, gate, expected_receipt)
    hold = prior.system_hold(state)
    if pending_candidates(state):
        raise ValueError("publication intent is active")
    public = baseline(state, gate)
    saved = verify_backup(state, gate_path, gate)
    with db_module.open_database(state, lock=False, read_only=True) as db:
        integrity(db.connection)
        before = protected_state(db.connection)
        if before["schema"] != 13:
            raise ValueError("H15 acceptance requires unmigrated schema 13 state")
        if any(
            db.connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()
            for name in NEW_TABLES
        ):
            raise ValueError("schema 13 unexpectedly contains H15 tables")
        if db.connection.execute(
            "SELECT 1 FROM execution_admissions WHERE state!='settled'"
        ).fetchone():
            raise ValueError("existing execution admission requires coordinator reconciliation")
    differences = [
        name for name in before["tables"] if before["tables"][name] != saved["tables"].get(name)
    ]
    if (
        set(differences) - {"meta"}
        or before["columns"] != saved["columns"]
        or before["backup_neutral_meta"] != saved["backup_neutral_meta"]
    ):
        raise ValueError("live protected state diverged from verified H14 checkpoint")
    return {
        "gate": gate,
        "gate_sha256": prior.digest(gate_path),
        "driver_sha256": prior.digest(Path(__file__)),
        "before": before,
        "checkpoint": saved,
        "baseline": public,
        "hold": hold,
        "sizes": size_inventory(state, source),
    }


def run(
    state: Path,
    source: Path,
    gate: Path,
    output: Path,
    *,
    expected_receipt: str,
    execute: bool = False,
) -> dict[str, Any]:
    if "checkpoints" in state.resolve().parts or (state / "checkpoint.json").exists():
        raise ValueError("never migrate an immutable checkpoint")
    validate_output(output, state, source, gate)
    # Record operation scale before hashing the full checkpoint or constructing
    # doctor output. These are estimates until preflight verifies the receipts.
    output.parent.mkdir(parents=True, exist_ok=True)
    size_note = size_inventory(state, source)
    gate_preview = json.loads(gate.read_bytes())
    checkpoint_manifest = json.loads(
        (Path(gate_preview["checkpoint"]) / "checkpoint.json").read_bytes()
    )
    size_note["checkpoint_files_unverified"] = len(checkpoint_manifest["files"])
    size_note["checkpoint_bytes_unverified"] = sum(
        item["size"] for item in checkpoint_manifest["files"].values()
    )
    output.with_suffix(".sizes.json").write_text(
        json.dumps(size_note, indent=2, sort_keys=True) + "\n"
    )
    with ExitStack() as guards:
        if execute:
            lock = guards.enter_context((state / "state.lock").open("a+b"))
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = preflight(state, source, gate, expected_receipt)
        receipt.update(
            executed=False,
            passed=False,
            preflight_passed=True,
            network_requests=0,
            published=False,
            service_executed=False,
        )
        output.parent.mkdir(parents=True, exist_ok=True)

        def save() -> None:
            temporary = output.with_suffix(output.suffix + ".tmp")
            with temporary.open("x") as stream:
                json.dump(receipt, stream, indent=2, sort_keys=True)
                stream.write("\n")
            temporary.replace(output)

        save()
        if not execute:
            return receipt
        started = monotonic()
        try:
            with db_module.open_database(state, lock=False) as db:
                receipt["executed"] = True
                after = protected_state(db.connection, receipt["before"]["columns"])
                receipt["after"] = after
                for table, original in receipt["before"]["columns"].items():
                    actual_columns = {
                        row[1] for row in db.connection.execute(f'PRAGMA table_info("{table}")')
                    }
                    expected_columns = set(original) | (
                        {"extract_recipe_sha256"} if table == "snapshots" else set()
                    )
                    if actual_columns != expected_columns:
                        raise ValueError("migration added unexpected columns to retained tables")
                if after["schema"] != 14 or after["tables"] != receipt["before"]["tables"]:
                    raise ValueError("migration changed protected state or target schema")
                integrity(db.connection)
                receipt["migration"] = verify_migration(db.connection)
            with db_module.open_database(state, lock=False, read_only=True) as db:
                receipt["retained_assessment"] = retained_assessment(db.connection)
            save()
            receipt["doctor"] = doctor_check(state, source, output)
            with db_module.open_database(state, lock=False, read_only=True) as db:
                if protected_state(db.connection, receipt["before"]["columns"]) != after:
                    raise ValueError("read-only acceptance changed protected persisted state")
                if verify_migration(db.connection) != receipt["migration"]:
                    raise ValueError("read-only acceptance changed new derivation state")
                integrity(db.connection)
            receipt["baseline_unchanged"] = baseline(state, receipt["gate"]) == receipt["baseline"]
            receipt["final_hold"] = prior.system_hold(state)
            if pending_candidates(state):
                raise ValueError("publication intent appeared during acceptance")
            receipt["passed"] = True
        except BaseException as error:
            receipt["error"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            receipt["elapsed_seconds"] = monotonic() - started
            save()
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("state", "source", "gate", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-receipt-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-seconds", type=int, default=900)
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 1800:
        raise ValueError("execution bound must be 1–1800 seconds")

    def timed_out(*_: object) -> None:
        raise TimeoutError("bounded H15 acceptance exceeded its execution window")

    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(args.max_seconds)
    try:
        run(
            args.state,
            args.source,
            args.gate,
            args.output,
            expected_receipt=args.source_receipt_sha256,
            execute=args.execute,
        )
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    main()
