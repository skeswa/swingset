"""Bounded H14 schema 12→13 migration acceptance; no service or source execution.

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
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from research import accept_h11 as prior
from swingset import cli
from swingset.publish.service import pending_candidates
from swingset.state import db as db_module
from swingset.state.requirement_report import human_report

BACKUP_VERIFIER_RECEIPT_SHA256 = "e359136cb07b2174c8ddd2394bceedb615abeb64e4ca1ec9a157e63c92320f5e"
BASE_SOURCE_RECEIPT_SHA256 = "ad95f42e33c551986af2ddaa0199937e4d4ef7bff9f88817f108f412216d0f7e"
V4_COMMIT = "81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653"
NEW_TABLES = {
    "scheduler_requests",
    "scheduler_offline_service",
    "scheduler_watch_state",
    "scheduler_parent_links",
}


def integrity(conn: sqlite3.Connection) -> None:
    if [tuple(row) for row in conn.execute("PRAGMA quick_check")] != [("ok",)]:
        raise ValueError("database quick_check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("database foreign-key integrity failed")


def verify_runtime(source: Path, gate: dict[str, Any], expected_receipt: str) -> None:
    receipt = source / "h14-source.json"
    if (
        gate.get("source_receipt_sha256") != expected_receipt
        or prior.digest(receipt) != expected_receipt
    ):
        raise ValueError("frozen H14 source receipt differs")
    assembly = json.loads(receipt.read_bytes())
    if (
        assembly.get("format") != "h14-reviewed-source-v1"
        or assembly.get("schema") != 13
        or assembly.get("acquisition_enabled") is not False
        or assembly.get("repairs_activated") is not False
    ):
        raise ValueError("expected reviewed schema 13 H14 source without activation")
    prior.verify_files(source, assembly["files"])
    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path != source / "h14-source.json"
    }
    if actual != set(assembly["files"]):
        raise ValueError("frozen source closure differs")
    if (
        db_module.SCHEMA_VERSION != 13
        or Path(db_module.__file__).resolve() != (source / "src/swingset/state/db.py").resolve()
        or Path(prior.__file__).resolve() != (source / "research/accept_h11.py").resolve()
    ):
        raise ValueError("runtime and H11 helpers must come from the frozen schema 13 source")


def verify_backup(state: Path, gate_path: Path, gate: dict[str, Any]) -> dict[str, Any]:
    checkpoint = Path(gate["checkpoint"]).resolve()
    if prior.digest(checkpoint / "checkpoint.json") != gate["checkpoint_manifest_sha256"]:
        raise ValueError("private checkpoint identity differs")
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    if (
        manifest.get("schema_version") != 12
        or manifest.get("pending_candidate") is not None
        or manifest.get("baseline_candidate") != (state / "baseline").resolve().name
    ):
        raise ValueError("private backup must retain this V4 baseline at schema 12")
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
        or backup.get("schema_version") != 12
        or backup.get("baseline_commit") != V4_COMMIT
        or backup.get("source_receipt_sha256") != BASE_SOURCE_RECEIPT_SHA256
        or backup.get("verification_source_receipt_sha256") != BACKUP_VERIFIER_RECEIPT_SHA256
        or backup.get("verification_source")
        != "/nix/store/0yq6dbr63yldsr1fkgfzyvqpm2vax1sg-swingset-h13-checkpoint-fix-source"
    ):
        raise ValueError("verified private backup receipt does not bind this checkpoint")
    with closing(
        sqlite3.connect((checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as saved:
        integrity(saved)
        snapshot = protected_state(saved)
        if snapshot["schema"] != 12:
            raise ValueError("checkpoint database must be schema 12")
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
    assembly = json.loads((source / "h14-source.json").read_bytes())
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
        "assert db.SCHEMA_VERSION==13 and Path(db.__file__).resolve()==Path(sys.argv[4]).resolve(); "
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
    if restarted != {"sha256": report_digest, "schema": 13} or report["schema_version"] != 13:
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
        raise ValueError("missing H14 migration tables")
    for table in NEW_TABLES - {"scheduler_parent_links"}:
        if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            raise ValueError("migration fabricated scheduling service or metadata attempts")
    expected = prior.query_digest(
        conn,
        "SELECT parent_watch_id,watch_id,created_by_snapshot_id,NULL FROM watches WHERE parent_watch_id IS NOT NULL ORDER BY 1,2,3,4",
    )
    actual = prior.query_digest(
        conn,
        "SELECT parent_watch_id,child_watch_id,first_snapshot_id,first_seen_at FROM scheduler_parent_links ORDER BY 1,2,3,4",
    )
    if expected != actual:
        raise ValueError("seeded parent relationships differ from retained watches")
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
        "empty_new_service_tables": sorted(NEW_TABLES - {"scheduler_parent_links"}),
        "parent_relationships": actual,
        "fence_triggers": len(actual_triggers),
    }


def fair_inventory(conn: sqlite3.Connection, source: Path, *, now: datetime) -> dict[str, Any]:
    """Read selection hints without admission, policy refresh or service accounting."""
    from swingset.config import load_config
    from swingset.schedule.fair_policy import allocation, shares
    from swingset.schedule.fairness import backpressure, next_offline, next_watch
    from swingset.state.control_scopes import unit_allowed

    config = load_config(source / "config")
    choice = next_watch(conn, config, now=now)
    offline = next_offline(conn, now=now, allowed=lambda unit: unit_allowed(conn, unit, now=now))
    hosts = []
    names = set(config.hosts) | {row[0] for row in conn.execute("SELECT host FROM hosts")}
    for host in sorted(names):
        policy = config.host(host)
        row = conn.execute(
            "SELECT requests,bytes FROM host_budget WHERE host=? AND day=?",
            (host, now.date().isoformat()),
        ).fetchone()
        used, size = tuple(row) if row else (0, 0)
        hosts.append(
            {
                "host": host,
                "shares": shares(host),
                "daily_request_budget": policy.daily_request_budget,
                "daily_byte_budget": policy.daily_byte_budget,
                "requests_used": used,
                "bytes_used": size,
                "ordinary_requests_remaining": max(0, policy.daily_request_budget - used),
            }
        )
    return {
        "at": now.isoformat(),
        "allocation_720s": asdict(allocation(config, 720)),
        "backpressure": backpressure(conn, config),
        "hosts": hosts,
        "due_by_source_state": [
            dict(row)
            for row in conn.execute(
                "SELECT source,state,count(*) AS count FROM watches WHERE next_check_at<=? AND state NOT IN ('sealed','retired') GROUP BY source,state ORDER BY source,state",
                (now.isoformat(),),
            )
        ],
        "selected_watch": None
        if choice is None
        else {
            "watch_id": choice.watch_id,
            "host": choice.host,
            "category": choice.category,
            "repair": choice.repair,
        },
        "selected_offline": None if offline is None else asdict(offline),
        "service_executed": False,
    }


def preflight(state: Path, source: Path, gate_path: Path, expected_receipt: str) -> dict[str, Any]:
    if "checkpoints" in state.resolve().parts or (state / "checkpoint.json").exists():
        raise ValueError("never migrate an immutable checkpoint")
    gate = json.loads(gate_path.read_bytes())
    if (
        gate.get("format") != "h14-operational-gate-v1"
        or gate.get("v4_verified") is not True
        or gate.get("private_backup_verified") is not True
        or not gate.get("private_backup_commit")
        or not gate.get("verified_at")
        or not gate.get("evidence_files")
    ):
        raise ValueError("gate requires completed V4 and private H13 backup verification")
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
        if before["schema"] != 12:
            raise ValueError("H14 acceptance requires unmigrated schema 12 state")
        if any(
            db.connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()
            for name in NEW_TABLES
        ):
            raise ValueError("schema 12 unexpectedly contains H14 tables")
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
        raise ValueError("live protected state diverged from verified H13 checkpoint")
    return {
        "gate": gate,
        "gate_sha256": prior.digest(gate_path),
        "driver_sha256": prior.digest(Path(__file__)),
        "before": before,
        "checkpoint": saved,
        "baseline": public,
        "hold": hold,
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
                if after["schema"] != 13 or after["tables"] != receipt["before"]["tables"]:
                    raise ValueError("migration changed protected state or target schema")
                integrity(db.connection)
                receipt["migration"] = verify_migration(db.connection)
            with db_module.open_database(state, lock=False, read_only=True) as db:
                receipt["fair_inventory"] = fair_inventory(
                    db.connection, source, now=datetime.now(UTC)
                )
            receipt["doctor"] = doctor_check(state, source, output)
            with db_module.open_database(state, lock=False, read_only=True) as db:
                if protected_state(db.connection, receipt["before"]["columns"]) != after:
                    raise ValueError("read-only acceptance changed protected persisted state")
                if verify_migration(db.connection) != receipt["migration"]:
                    raise ValueError("read-only acceptance changed new scheduling state")
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
        raise TimeoutError("bounded H14 acceptance exceeded its execution window")

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
