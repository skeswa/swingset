"""Bounded H16 schema-14 acceptance: read-only state, no migration or service.

Default preflight verifies pinned code, private backup, and published baseline.
--execute adds read-only inventory and deterministic fresh-process doctor checks.
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

BASE_SOURCE_RECEIPT_SHA256 = "be7001a7e3bd3bc496f40513662cb5b561217b55cf37a3e11255fa84deea1c8c"

PRIVATE_BACKUP_COMMIT = "c656e88c775b23ae5879661924d57fa1f93cfa7d"

PRIVATE_BACKUP_MANIFEST = "700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444"

V4_COMMIT = "81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653"


def integrity(conn: sqlite3.Connection) -> None:
    if [tuple(row) for row in conn.execute("PRAGMA quick_check")] != [("ok",)]:
        raise ValueError("database quick_check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("database foreign-key integrity failed")


def verify_runtime(source: Path, gate: dict[str, Any], expected_receipt: str) -> None:
    receipt = source / "h16-source.json"
    if (
        gate.get("source_receipt_sha256") != expected_receipt
        or prior.digest(receipt) != expected_receipt
    ):
        raise ValueError("frozen H16 source receipt differs")
    assembly = json.loads(receipt.read_bytes())
    if (
        assembly.get("format") != "h16-reviewed-source-v1"
        or assembly.get("schema") != 14
        or assembly.get("acquisition_enabled") is not False
        or assembly.get("repairs_activated") is not False
    ):
        raise ValueError("expected reviewed schema 14 H16 source without activation")
    prior.verify_files(source, assembly["files"])
    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path != source / "h16-source.json"
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
        raise ValueError("expected the exact verified H15 predecessor backup")
    if prior.digest(checkpoint / "checkpoint.json") != gate["checkpoint_manifest_sha256"]:
        raise ValueError("private checkpoint identity differs")
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    if (
        manifest.get("schema_version") != 14
        or manifest.get("pending_candidate") is not None
        or manifest.get("baseline_candidate") != (state / "baseline").resolve().name
    ):
        raise ValueError("private backup must retain this V4 baseline at schema 14")
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
        or backup.get("schema_version") != 14
        or backup.get("baseline_commit") != V4_COMMIT
        or backup.get("source_receipt_sha256") != BASE_SOURCE_RECEIPT_SHA256
    ):
        raise ValueError("verified private backup receipt does not bind this checkpoint")
    with closing(
        sqlite3.connect((checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as saved:
        integrity(saved)
        snapshot = protected_state(saved)
        if snapshot["schema"] != 14:
            raise ValueError("checkpoint database must be schema 14")
    return snapshot


def baseline(state: Path, gate: dict[str, Any]) -> dict[str, Any]:
    target = (state / "baseline").resolve(strict=True)
    result = {
        "commit": json.loads((target / "PUBLISHED").read_bytes())["commit"],
        "manifest_sha256": prior.digest(target / "_meta/manifest.json"),
    }
    if (
        result != {"commit": V4_COMMIT, "manifest_sha256": gate["v4_manifest_sha256"]}
        or gate.get("v4_commit") != V4_COMMIT
    ):
        raise ValueError("current public baseline differs from verified V4")
    prior.verify_files(
        target,
        json.loads((target / "_meta/manifest.json").read_bytes())["files"],
    )
    return result


def validate_output(output: Path, state: Path, source: Path, gate: Path) -> None:
    assembly = json.loads((source / "h16-source.json").read_bytes())
    outputs = (
        output,
        output.with_suffix(".sizes.json"),
        output.with_suffix(".doctor.json"),
        output.with_suffix(".doctor.txt"),
        output.with_suffix(output.suffix + ".tmp"),
    )
    for path in outputs:
        prior.validate_output_paths(path, state, source, gate)
        if any(
            path.resolve().is_relative_to((state / name).resolve())
            for name in ("blobs", "extracts", "inputs")
        ):
            raise ValueError("receipt output cannot enter retained artifact roots")
        for key in ("base", "reviewed"):
            if assembly.get(key) and path.resolve().is_relative_to(Path(assembly[key]).resolve()):
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
    with output.with_suffix(".doctor.json").open("x") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    with output.with_suffix(".doctor.txt").open("x") as stream:
        stream.write(human + "\n")
    return {
        "fixed_time": instant.isoformat(),
        "fresh_process_equal": True,
        "requirements_human_json_equal": True,
        "report_sha256": report_digest,
        "json_bytes": output.with_suffix(".doctor.json").stat().st_size,
        "human_bytes": output.with_suffix(".doctor.txt").stat().st_size,
    }


def size_inventory(state: Path, source: Path) -> dict[str, Any]:
    assembly = json.loads((source / "h16-source.json").read_bytes())
    return {
        "database_bytes": (state / "state.sqlite").stat().st_size,
        "source_files": len(assembly["files"]),
        "source_bytes": sum((source / name).stat().st_size for name in assembly["files"]),
        "doctor_output_policy": "one full settled report plus fresh-process digest; ops output only",
        "assessment_page_size": 256,
    }


def protected_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Hash every user table and original column, without excluding new proof tables."""
    columns = {}
    tables = {}
    for (name,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ):
        if not name.replace("_", "").isalnum():
            raise ValueError("unexpected schema identifier")
        columns[name] = [row[1] for row in conn.execute(f'PRAGMA table_info("{name}")')]
        order = ",".join(str(index + 1) for index in range(len(columns[name])))
        tables[name] = prior.query_digest(conn, f'SELECT * FROM "{name}" ORDER BY {order}')
    return {
        "schema": conn.execute("PRAGMA user_version").fetchone()[0],
        "autoincrement_sequences": prior.query_digest(
            conn, "SELECT name,seq FROM sqlite_sequence ORDER BY name"
        ),
        "columns": columns,
        "tables": tables,
        "schema_objects": prior.query_digest(
            conn, "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ),
        "backup_neutral_meta": prior.query_digest(
            conn, "SELECT key,value FROM meta WHERE key NOT LIKE 'last_backup%' ORDER BY key"
        ),
    }


def retained_assessment(conn: sqlite3.Connection, *, max_seconds: float = 120) -> dict[str, Any]:
    """Enumerate unfinished work, including missing enqueue hints; never execute it."""
    from swingset.state.work import unfinished_units

    if not 0 < max_seconds <= 180:
        raise ValueError("invalid inventory time bound")
    started = monotonic()
    conn.set_progress_handler(lambda: int(monotonic() - started > max_seconds), 10000)
    counts: dict[str, int] = {}
    sample: list[dict[str, str]] = []
    try:
        for unit in unfinished_units(conn):
            if monotonic() - started > max_seconds:
                raise TimeoutError("unfinished inventory exceeded bound")
            key = unit.stage + "/" + unit.unit_kind
            counts[key] = counts.get(key, 0) + 1
            if len(sample) < 20:
                sample.append({"stage": unit.stage, "kind": unit.unit_kind, "id": unit.unit_id})
        selected = conn.execute(
            "SELECT count(*),count(materialized_generation_id) FROM derivation_scopes"
        ).fetchone()
    except sqlite3.OperationalError as error:
        if "interrupted" in str(error):
            raise TimeoutError("unfinished inventory exceeded bound") from error
        raise
    finally:
        conn.set_progress_handler(None, 0)
    return {
        "unfinished": sum(counts.values()),
        "unfinished_by_scope": counts,
        "sample": sample,
        "registered_scopes": selected[0],
        "materialized_pointers": selected[1],
        "basis": "all unfinished scopes; materialized pointers are retained evidence, not an assertion of current output",
        "work_completed": 0,
        "service_executed": False,
        "elapsed_seconds": monotonic() - started,
    }


def preflight(state: Path, source: Path, gate_path: Path, expected_receipt: str) -> dict[str, Any]:
    if "checkpoints" in state.resolve().parts or (state / "checkpoint.json").exists():
        raise ValueError("acceptance target must be live state, never an immutable checkpoint")
    gate = json.loads(gate_path.read_bytes())
    if (
        gate.get("format") != "h16-operational-gate-v1"
        or gate.get("v4_verified") is not True
        or gate.get("private_backup_verified") is not True
        or not gate.get("verified_at")
        or not gate.get("evidence_files")
    ):
        raise ValueError("gate requires verified V4 and private H15 backup")
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
        if before["schema"] != 14:
            raise ValueError("H16 requires existing schema 14; migration is not authorized")
        if db.connection.execute(
            "SELECT 1 FROM execution_admissions WHERE state!='settled'"
        ).fetchone():
            raise ValueError("existing execution admission requires coordinator reconciliation")
    differences = {
        name for name in before["tables"] if before["tables"][name] != saved["tables"].get(name)
    }
    if (
        differences - {"meta"}
        or before["columns"] != saved["columns"]
        or before["schema_objects"] != saved["schema_objects"]
        or before["autoincrement_sequences"] != saved["autoincrement_sequences"]
        or before["backup_neutral_meta"] != saved["backup_neutral_meta"]
    ):
        raise ValueError("live protected state diverged from verified H15 checkpoint")
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
        raise ValueError("acceptance target must be live state, never an immutable checkpoint")
    validate_output(output, state, source, gate)
    output.parent.mkdir(parents=True, exist_ok=True)
    sizes = size_inventory(state, source)
    preview = json.loads(gate.read_bytes())
    manifest = json.loads((Path(preview["checkpoint"]) / "checkpoint.json").read_bytes())
    sizes.update(
        checkpoint_files_unverified=len(manifest["files"]),
        checkpoint_bytes_unverified=sum(item["size"] for item in manifest["files"].values()),
    )
    with output.with_suffix(".sizes.json").open("x") as stream:
        stream.write(json.dumps(sizes, indent=2, sort_keys=True) + "\n")
    with ExitStack() as guards:
        lock = guards.enter_context((state / "state.lock").open("a+b"))
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = preflight(state, source, gate, expected_receipt)
        receipt.update(
            executed=False,
            passed=False,
            preflight_passed=True,
            network_requests=0,
            published=False,
            migrated=False,
            service_executed=False,
        )

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
            receipt["executed"] = True
            with db_module.open_database(state, lock=False, read_only=True) as db:
                receipt["retained_assessment"] = retained_assessment(db.connection)
            save()
            receipt["doctor"] = doctor_check(state, source, output)
            with db_module.open_database(state, lock=False, read_only=True) as db:
                after = protected_state(db.connection)
                integrity(db.connection)
            receipt["after"] = after
            if after != receipt["before"]:
                raise ValueError("read-only acceptance changed persisted state")
            receipt["baseline_unchanged"] = baseline(state, receipt["gate"]) == receipt["baseline"]
            receipt["final_hold"] = prior.system_hold(state)
            if not receipt["baseline_unchanged"] or pending_candidates(state):
                raise ValueError("publication state changed during acceptance")
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
        raise TimeoutError("bounded H16 acceptance exceeded its execution window")

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
