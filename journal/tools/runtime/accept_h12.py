"""Offline H12 schema 10→11 preflight; --execute only migrates and verifies.

Run with PYTHONPATH=<frozen-source>/src:<frozen-source>. The separately hashed
operations driver never starts cycles, requests sources, or activates policies.
"""

import argparse
import fcntl
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

SOURCE_RECEIPT_SHA256 = "ad1e0b8395b28559e6f4623459955b31574637503edddd994354520b384450ee"
V4_COMMIT = "81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653"
NEW_TABLES = {"work_attempts", "work_generations"}


def protected_state(conn: sqlite3.Connection) -> dict[str, Any]:
    """Hash every existing user table, including full H11 and interpretation rows."""
    names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    if any(not name.replace("_", "").isalnum() for name in names):
        raise ValueError("unexpected table identifier")
    tables = {}
    for name in names:
        if name in NEW_TABLES:
            continue
        tables[name] = (
            prior.query_digest(
                conn, "SELECT key,value FROM meta WHERE key!='schema_version' ORDER BY key"
            )
            if name == "meta"
            else prior.table_digest(conn, name)
        )
    return {
        "schema": conn.execute("PRAGMA user_version").fetchone()[0],
        "tables": tables,
        "backup_neutral_meta": prior.query_digest(
            conn,
            "SELECT key,value FROM meta WHERE key!='schema_version' AND key NOT LIKE 'last_backup%' ORDER BY key",
        ),
    }


def integrity(conn: sqlite3.Connection) -> None:
    if [tuple(row) for row in conn.execute("PRAGMA quick_check")] != [("ok",)]:
        raise ValueError("database quick_check failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("database foreign-key integrity failed")


def verify_new_tables(conn: sqlite3.Connection) -> dict[str, Any]:
    if conn.execute("SELECT COUNT(*) FROM work_attempts").fetchone()[0]:
        raise ValueError("migration must not create work attempts")
    missing = conn.execute(
        "SELECT stage,unit_kind,unit_id,1,0,NULL,NULL FROM pending_work EXCEPT "
        "SELECT stage,unit_kind,unit_id,generation,retry_generation,retry_reason,retry_requested_at FROM work_generations"
    ).fetchone()
    extra = conn.execute(
        "SELECT stage,unit_kind,unit_id,generation,retry_generation,retry_reason,retry_requested_at FROM work_generations EXCEPT "
        "SELECT stage,unit_kind,unit_id,1,0,NULL,NULL FROM pending_work"
    ).fetchone()
    if missing or extra:
        raise ValueError("initial queue generations differ from retained pending work")
    return {name: prior.table_digest(conn, name) for name in sorted(NEW_TABLES)}


def verify_runtime(source: Path, gate: dict[str, Any]) -> None:
    receipt = source / "h12-source.json"
    if (
        gate.get("source_receipt_sha256") != SOURCE_RECEIPT_SHA256
        or prior.digest(receipt) != SOURCE_RECEIPT_SHA256
    ):
        raise ValueError("frozen H12 source receipt differs")
    assembly = json.loads(receipt.read_bytes())
    if (
        assembly.get("format") != "h12-reviewed-source-v1"
        or assembly.get("schema") != 11
        or assembly.get("acquisition_enabled") is not False
        or assembly.get("repairs_activated") is not False
    ):
        raise ValueError("expected reviewed schema 11 H12 source without activation")
    prior.verify_files(source, assembly["files"])
    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.name != "h12-source.json"
    }
    if actual != set(assembly["files"]):
        raise ValueError("frozen source closure differs")
    if (
        db_module.SCHEMA_VERSION != 11
        or Path(db_module.__file__).resolve() != (source / "src/swingset/state/db.py").resolve()
        or Path(prior.__file__).resolve()
        != (source / "journal/tools/runtime/accept_h11.py").resolve()
    ):
        raise ValueError("runtime and H11 helpers must come from the frozen schema 11 source")


def verify_backup(state: Path, gate_path: Path, gate: dict[str, Any]) -> dict[str, Any]:
    checkpoint = Path(gate["checkpoint"]).resolve()
    if prior.digest(checkpoint / "checkpoint.json") != gate["checkpoint_manifest_sha256"]:
        raise ValueError("private checkpoint identity differs")
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    if (
        manifest.get("schema_version") != 10
        or manifest.get("pending_candidate") is not None
        or manifest.get("baseline_candidate") != (state / "baseline").resolve().name
    ):
        raise ValueError("private backup must retain this V4 baseline at schema 10")
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
        or backup.get("schema_version") != 10
        or backup.get("baseline_commit") != V4_COMMIT
        or backup.get("source_receipt_sha256") != SOURCE_RECEIPT_SHA256
    ):
        raise ValueError("verified private backup receipt does not bind this checkpoint")
    with closing(
        sqlite3.connect((checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as saved:
        integrity(saved)
        snapshot = protected_state(saved)
        if snapshot["schema"] != 10:
            raise ValueError("checkpoint database must be schema 10")
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


def preflight(state: Path, source: Path, gate_path: Path) -> dict[str, Any]:
    if "checkpoints" in state.resolve().parts or (state / "checkpoint.json").exists():
        raise ValueError("never migrate an immutable checkpoint")
    gate = json.loads(gate_path.read_bytes())
    if (
        gate.get("format") != "h12-operational-gate-v1"
        or gate.get("v4_verified") is not True
        or gate.get("private_backup_verified") is not True
        or not gate.get("private_backup_commit")
        or not gate.get("verified_at")
        or not gate.get("evidence_files")
    ):
        raise ValueError("gate requires completed V4 and private H11 backup verification")
    if gate.get("driver_sha256") != prior.digest(Path(__file__)):
        raise ValueError("operations driver differs from reviewed gate")
    prior.verify_files(gate_path.parent, gate["evidence_files"])
    verify_runtime(source, gate)
    hold = prior.system_hold(state)
    if pending_candidates(state):
        raise ValueError("publication intent is active")
    public = baseline(state, gate)
    saved = verify_backup(state, gate_path, gate)
    with db_module.open_database(state, lock=False, read_only=True) as db:
        integrity(db.connection)
        before = protected_state(db.connection)
        if before["schema"] != 10:
            raise ValueError("H12 acceptance requires unmigrated schema 10 state")
        if db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN ('work_attempts','work_generations')"
        ).fetchone():
            raise ValueError("schema 10 state unexpectedly contains H12 tables")
    # Backup upload legitimately records its own receipt in live meta afterward.
    # All other rows, including H11 cohorts and existing pending work, must match.
    differences = [
        name for name in before["tables"] if before["tables"][name] != saved["tables"].get(name)
    ]
    if (
        set(differences) - {"meta"}
        or set(before["tables"]) != set(saved["tables"])
        or before["backup_neutral_meta"] != saved["backup_neutral_meta"]
    ):
        raise ValueError("live protected state diverged from verified H11 checkpoint")
    return {
        "gate": gate,
        "gate_sha256": prior.digest(gate_path),
        "driver_sha256": prior.digest(Path(__file__)),
        "before": before,
        "checkpoint": saved,
        "baseline": public,
        "hold": hold,
    }


def validate_output(output: Path, state: Path, source: Path, gate: Path) -> None:
    prior.validate_output_paths(output, state, source, gate)
    if any(
        output.resolve().is_relative_to((state / name).resolve())
        for name in ("blobs", "extracts", "inputs")
    ):
        raise ValueError("receipt output cannot enter retained artifact roots")
    assembly = json.loads((source / "h12-source.json").read_bytes())
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
        "import argparse,json,sys; from pathlib import Path; from datetime import datetime; "
        "from types import SimpleNamespace; from swingset import cli; from swingset.state import db; "
        "assert db.SCHEMA_VERSION==11 and Path(db.__file__).resolve()==Path(sys.argv[4]).resolve(); "
        "cli.SystemClock=lambda:SimpleNamespace(now=lambda:datetime.fromisoformat(sys.argv[3])); "
        "print(json.dumps(cli.doctor(argparse.Namespace(state=Path(sys.argv[1]),config=Path(sys.argv[2])))))"
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
    if restarted != json.loads(json.dumps(report)) or report["schema_version"] != 11:
        raise ValueError("fresh-process doctor differs or reads an unexpected schema")
    output.with_suffix(".doctor.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    output.with_suffix(".doctor.txt").write_text(human + "\n")
    return {
        "fixed_time": instant.isoformat(),
        "fresh_process_equal": True,
        "human_json_equal": True,
    }


def run(
    state: Path, source: Path, gate: Path, output: Path, *, execute: bool = False
) -> dict[str, Any]:
    if "checkpoints" in state.resolve().parts or (state / "checkpoint.json").exists():
        raise ValueError("never migrate an immutable checkpoint")
    validate_output(output, state, source, gate)
    with ExitStack() as guards:
        if execute:
            lock = guards.enter_context((state / "state.lock").open("a+b"))
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = preflight(state, source, gate)
        receipt.update(
            executed=False, passed=False, preflight_passed=True, network_requests=0, published=False
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
                after = protected_state(db.connection)
                receipt["after"] = after
                if after["schema"] != 11 or after["tables"] != receipt["before"]["tables"]:
                    raise ValueError("migration changed protected state or target schema")
                integrity(db.connection)
                receipt["new_tables"] = verify_new_tables(db.connection)
            receipt["doctor"] = doctor_check(state, source, output)
            with db_module.open_database(state, lock=False, read_only=True) as db:
                if (
                    protected_state(db.connection) != after
                    or verify_new_tables(db.connection) != receipt["new_tables"]
                ):
                    raise ValueError("read-only doctor changed persisted state")
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
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-seconds", type=int, default=900)
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 1800:
        raise ValueError("execution bound must be 1–1800 seconds")

    def timed_out(*_: object) -> None:
        raise TimeoutError("bounded H12 acceptance exceeded its execution window")

    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(args.max_seconds)
    try:
        run(args.state, args.source, args.gate, args.output, execute=args.execute)
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    main()
