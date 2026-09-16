"""Offline H13 schema 11→12 preflight; --execute only migrates and verifies.

Run with PYTHONPATH=<frozen-source>/src:<frozen-source>. The separately hashed
operations driver never starts cycles, requests sources, or activates policies.
"""

import argparse
import fcntl
import hashlib
import json
import selectors
import signal
import sqlite3
import subprocess
import sys
from contextlib import ExitStack, closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from journal.tools.runtime import accept_h11 as prior
from swingset import cli
from swingset.publish.service import pending_candidates
from swingset.state import db as db_module
from swingset.state.requirement_report import human_report

BASE_SOURCE_RECEIPT_SHA256 = "ad1e0b8395b28559e6f4623459955b31574637503edddd994354520b384450ee"
V4_COMMIT = "81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653"
NEW_TABLES = {"control_state", "control_events", "execution_admissions", "execution_dependencies"}
PROBE_HOST = "h13-acceptance.invalid"


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
        if name == "operator_pauses":
            tables[name] = prior.query_digest(
                conn,
                "SELECT scope_kind,scope_id,until_at,reason FROM operator_pauses ORDER BY scope_kind,scope_id",
            )
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


def verify_migration(conn: sqlite3.Connection) -> dict[str, Any]:
    pauses = conn.execute("SELECT COUNT(*) FROM operator_pauses").fetchone()[0]
    if conn.execute("SELECT revision FROM control_state WHERE singleton=1").fetchone()[0] != 0:
        raise ValueError("migration invented control revisions")
    if conn.execute("SELECT COUNT(*) FROM control_events").fetchone()[0] != pauses:
        raise ValueError("legacy pause audit count differs")
    if conn.execute(
        "SELECT 1 FROM operator_pauses p LEFT JOIN control_events e ON e.pause_id=p.pause_id "
        "WHERE p.pause_id IS NULL OR p.pause_id NOT LIKE 'legacy_%' OR p.actor!='legacy:unknown' "
        "OR p.control_revision!=0 OR p.created_at IS NOT NULL OR e.event_id IS NULL "
        "OR e.action!='legacy_import' OR e.control_revision!=0 OR e.actor!=p.actor "
        "OR e.scope_kind!=p.scope_kind OR e.scope_id!=p.scope_id OR e.reason!=p.reason "
        "OR e.until_at IS NOT p.until_at"
    ).fetchone():
        raise ValueError("migration changed or fabricated legacy pause provenance")
    if any(
        conn.execute(f"SELECT 1 FROM {name} LIMIT 1").fetchone()
        for name in ("execution_admissions", "execution_dependencies")
    ):
        raise ValueError("migration fabricated admitted work")
    from swingset.state.publication_fence import CONTROL_TABLES

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    expected = {
        f"publication_fence_{table}_{action}"
        for table in tables - CONTROL_TABLES
        for action in ("insert", "update", "delete")
    }
    actual = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'publication_fence_%'"
        )
    }
    if actual != expected:
        raise ValueError("publication semantic fence coverage differs")
    return {
        "legacy_pauses": pauses,
        "legacy_events": pauses,
        "fence_triggers": len(actual),
        "legacy_event_digest": prior.table_digest(conn, "control_events"),
    }


def verify_probe_completion(
    conn: sqlite3.Connection, migration: dict[str, Any], probe: dict[str, Any]
) -> None:
    legacy = prior.query_digest(
        conn, "SELECT * FROM control_events WHERE action='legacy_import' ORDER BY event_id"
    )
    if legacy != migration["legacy_event_digest"]:
        raise ValueError("acceptance changed imported legacy control events")
    actions = [dict(row) for row in conn.execute("SELECT * FROM execution_admissions")]
    if len(actions) != 1 or any(
        actions[0][key] != value
        for key, value in {
            "action_id": probe["action_id"],
            "action_kind": "acceptance_probe",
            "host": PROBE_HOST,
            "state": "settled",
            "outcome": "completed",
            "run_id": None,
            "work_attempt_id": None,
            "all_sources": 0,
            "all_kinds": 0,
        }.items()
    ):
        raise ValueError("unexpected acceptance execution rows")
    if conn.execute("SELECT 1 FROM execution_dependencies").fetchone():
        raise ValueError("probe fabricated source dependencies")
    events = [
        tuple(row)
        for row in conn.execute(
            "SELECT action,pause_id,scope_kind,scope_id,actor,control_revision FROM control_events "
            "WHERE action!='legacy_import' ORDER BY control_revision"
        )
    ]
    if events != [
        (action, probe["pause_id"], "host", PROBE_HOST, "h13-acceptance", revision)
        for revision, action in ((1, "pause"), (2, "resume"))
    ]:
        raise ValueError("unexpected acceptance control events")
    if conn.execute("SELECT revision FROM control_state WHERE singleton=1").fetchone()[0] != 2:
        raise ValueError("unexpected final control revision")


def control_digest(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        name: prior.table_digest(conn, name) for name in sorted(NEW_TABLES | {"operator_pauses"})
    }


def verify_runtime(source: Path, gate: dict[str, Any], expected_receipt: str) -> None:
    receipt = source / "h13-source.json"
    if (
        gate.get("source_receipt_sha256") != expected_receipt
        or prior.digest(receipt) != expected_receipt
    ):
        raise ValueError("frozen H13 source receipt differs")
    assembly = json.loads(receipt.read_bytes())
    if (
        assembly.get("format") != "h13-reviewed-source-v1"
        or assembly.get("schema") != 12
        or assembly.get("acquisition_enabled") is not False
        or assembly.get("repairs_activated") is not False
    ):
        raise ValueError("expected reviewed schema 12 H13 source without activation")
    prior.verify_files(source, assembly["files"])
    actual = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.name != "h13-source.json"
    }
    if actual != set(assembly["files"]):
        raise ValueError("frozen source closure differs")
    if (
        db_module.SCHEMA_VERSION != 12
        or Path(db_module.__file__).resolve() != (source / "src/swingset/state/db.py").resolve()
        or Path(prior.__file__).resolve()
        != (source / "journal/tools/runtime/accept_h11.py").resolve()
    ):
        raise ValueError("runtime and H11 helpers must come from the frozen schema 12 source")


def verify_backup(state: Path, gate_path: Path, gate: dict[str, Any]) -> dict[str, Any]:
    checkpoint = Path(gate["checkpoint"]).resolve()
    if prior.digest(checkpoint / "checkpoint.json") != gate["checkpoint_manifest_sha256"]:
        raise ValueError("private checkpoint identity differs")
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    if (
        manifest.get("schema_version") != 11
        or manifest.get("pending_candidate") is not None
        or manifest.get("baseline_candidate") != (state / "baseline").resolve().name
    ):
        raise ValueError("private backup must retain this V4 baseline at schema 11")
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
        or backup.get("schema_version") != 11
        or backup.get("baseline_commit") != V4_COMMIT
        or backup.get("source_receipt_sha256") != BASE_SOURCE_RECEIPT_SHA256
    ):
        raise ValueError("verified private backup receipt does not bind this checkpoint")
    with closing(
        sqlite3.connect((checkpoint / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as saved:
        integrity(saved)
        snapshot = protected_state(saved)
        if snapshot["schema"] != 11:
            raise ValueError("checkpoint database must be schema 11")
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


def preflight(state: Path, source: Path, gate_path: Path, expected_receipt: str) -> dict[str, Any]:
    if "checkpoints" in state.resolve().parts or (state / "checkpoint.json").exists():
        raise ValueError("never migrate an immutable checkpoint")
    gate = json.loads(gate_path.read_bytes())
    if (
        gate.get("format") != "h13-operational-gate-v1"
        or gate.get("v4_verified") is not True
        or gate.get("private_backup_verified") is not True
        or not gate.get("private_backup_commit")
        or not gate.get("verified_at")
        or not gate.get("evidence_files")
    ):
        raise ValueError("gate requires completed V4 and private H12 backup verification")
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
        if before["schema"] != 11:
            raise ValueError("H13 acceptance requires unmigrated schema 11 state")
        if db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN ('control_state','execution_admissions')"
        ).fetchone():
            raise ValueError("schema 11 state unexpectedly contains H13 tables")
        if db.connection.execute(
            "SELECT 1 FROM operator_pauses WHERE scope_kind='all' AND (until_at IS NULL OR until_at>?)",
            (datetime.now(UTC).isoformat(),),
        ).fetchone():
            raise ValueError("existing global pause prevents the bounded control probe")
        if db.connection.execute(
            "SELECT 1 FROM operator_pauses WHERE scope_kind='host' AND scope_id=?", (PROBE_HOST,)
        ).fetchone():
            raise ValueError("probe selector already belongs to an existing control")
        if db.connection.execute(
            "SELECT 1 FROM operator_pauses WHERE until_at IS NOT NULL AND until_at<=?",
            ((datetime.now(UTC) + timedelta(seconds=1800)).isoformat(),),
        ).fetchone():
            raise ValueError(
                "existing timed pause could expire during acceptance; preserve its control first"
            )
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
        raise ValueError("live protected state diverged from verified H12 checkpoint")
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
    prior.validate_output_paths(
        output.with_name(output.stem + "-probe-held.json"), state, source, gate
    )
    probe = output.with_suffix(".control-probe")
    prior.validate_output_paths(probe, state, source, gate)
    if any(
        output.resolve().is_relative_to((state / name).resolve())
        for name in ("blobs", "extracts", "inputs")
    ):
        raise ValueError("receipt output cannot enter retained artifact roots")
    assembly = json.loads((source / "h13-source.json").read_bytes())
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
        "assert db.SCHEMA_VERSION==12 and Path(db.__file__).resolve()==Path(sys.argv[4]).resolve(); "
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
    if restarted != {"sha256": report_digest, "schema": 12} or report["schema_version"] != 12:
        raise ValueError("fresh-process doctor differs or reads an unexpected schema")
    output.with_suffix(".doctor.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    output.with_suffix(".doctor.txt").write_text(human + "\n")
    return {
        "fixed_time": instant.isoformat(),
        "fresh_process_equal": True,
        "human_json_equal": True,
        "report_sha256": report_digest,
    }


PROBE_CODE = """
import selectors,sys
from pathlib import Path
from swingset.clock import SystemClock
from swingset.state.controls import ActionScope,operation
from swingset.state.db import open_database
with open_database(Path(sys.argv[1]),lock=False) as db:
    with operation(db,action_id=sys.argv[2],action_kind='acceptance_probe',
                   scope=ActionScope(host=sys.argv[3]),clock=SystemClock()):
        print('admitted',flush=True)
        with selectors.DefaultSelector() as poll:
            poll.register(sys.stdin,selectors.EVENT_READ)
            if not poll.select(30) or sys.stdin.readline().strip()!='finish':
                raise TimeoutError('bounded acceptance probe did not finish')
print('settled',flush=True)
"""


def captured_controls(state: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    names = sorted(NEW_TABLES | {"operator_pauses"})
    with db_module.open_database(state, lock=False, read_only=True) as live:
        captured = {
            name: [tuple(row) for row in live.connection.execute(f"SELECT * FROM {name}")]
            for name in names
        }
        expected = control_digest(live.connection)
    return captured, expected


def paused_status_check(state: Path) -> dict[str, Any]:
    """The doctor's actual control-status module, without its large inventory."""
    from swingset.state.controls import Selector, status

    instant = datetime.now(UTC)
    with db_module.open_database(state, lock=False, read_only=True) as db:
        expected = status(db.connection, now=instant, selector=Selector("host", PROBE_HOST))
    code = (
        "import json,sys; from datetime import datetime; from swingset.state.db import open_database; "
        "from swingset.state.controls import Selector,status; "
        "db=open_database(sys.argv[1],lock=False,read_only=True); "
        "result=status(db.connection,now=datetime.fromisoformat(sys.argv[2]),selector=Selector('host',sys.argv[3])); "
        "db.close(); print(json.dumps(result))"
    )
    actual = json.loads(
        subprocess.run(
            [sys.executable, "-c", code, str(state), instant.isoformat(), PROBE_HOST],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout
    )
    if actual != json.loads(json.dumps(expected)) or actual["state"] != "pausing":
        raise ValueError("paused control status changed across fresh-process read")
    return {
        "fixed_time": instant.isoformat(),
        "fresh_process_equal": True,
        "state": actual["state"],
    }


def control_restore_probe(
    captured: dict[str, Any], expected: dict[str, Any], root: Path
) -> dict[str, Any]:
    """Restore an exact small control-plane specimen, never production facts."""
    from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint
    from swingset.state.controls import Selector, change_control, status

    root.mkdir()
    specimen, checkpoint, restored = (
        root / name for name in ("specimen", "checkpoint", "restored")
    )
    with db_module.open_database(specimen) as db:
        with db.transaction() as conn:
            for name in (
                "execution_dependencies",
                "execution_admissions",
                "control_events",
                "control_state",
                "operator_pauses",
            ):
                conn.execute(f"DELETE FROM {name}")
            for name in (
                "operator_pauses",
                "control_state",
                "control_events",
                "execution_admissions",
                "execution_dependencies",
            ):
                rows = captured[name]
                if rows:
                    placeholders = ",".join("?" for _ in rows[0])
                    conn.executemany(f"INSERT INTO {name} VALUES ({placeholders})", rows)
        create_checkpoint(
            specimen,
            db.connection,
            checkpoint,
            schema_version=12,
            versions={},
            input_bundle_hash=None,
        )
    restore_checkpoint(checkpoint, restored, maximum_schema_version=12)
    with db_module.open_database(restored, lock=False, read_only=True) as db:
        integrity(db.connection)
        if control_digest(db.connection) != expected:
            raise ValueError("restored control-plane specimen differs")
        restored_status = status(
            db.connection, now=datetime.now(UTC), selector=Selector("host", PROBE_HOST)
        )
        if restored_status["state"] != "pausing":
            raise ValueError("restored specimen lost its active pause drain")
    try:
        change_control(
            restored,
            selector=Selector("host", PROBE_HOST),
            paused=False,
            actor="h13-acceptance",
            reason="restore guard test",
            now=datetime.now(UTC),
        )
    except RuntimeError as error:
        if "restore verification is pending" not in str(error):
            raise
    else:
        raise ValueError("restore verification marker failed to hold controls")
    return {
        "scope": "control-plane specimen; no production facts",
        "tables": expected,
        "checkpoint_manifest_sha256": prior.digest(checkpoint / "checkpoint.json"),
        "restored_pause_state": restored_status["state"],
        "restore_pending_enforced": True,
    }


def control_probe(state: Path, source: Path, output: Path) -> dict[str, Any]:
    from swingset.state.controls import Selector, change_control, status

    action_id = "h13_acceptance_" + uuid4().hex
    child = subprocess.Popen(
        [sys.executable, "-c", PROBE_CODE, str(state), action_id, PROBE_HOST],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    paused = False
    try:
        assert child.stdout is not None and child.stdin is not None
        with selectors.DefaultSelector() as poll:
            poll.register(child.stdout, selectors.EVENT_READ)
            if not poll.select(10) or child.stdout.readline().strip() != "admitted":
                raise RuntimeError("bounded subprocess did not admit its control probe")
        active_started = monotonic()
        began = monotonic()
        pause = change_control(
            state,
            selector=Selector("host", PROBE_HOST),
            paused=True,
            actor="h13-acceptance",
            reason="bounded offline control acceptance",
            now=datetime.now(UTC),
            timeout=5,
            hosts=(PROBE_HOST,),
        )
        paused = True
        elapsed = monotonic() - began
        if pause["state"] != "pausing" or [
            row["action_id"] for row in pause["draining_attempts"]
        ] != [action_id]:
            raise ValueError("pause did not persist during the active subprocess unit")
        restarted = paused_status_check(state)
        captured, expected = captured_controls(state)
        child.stdin.write("finish\n")
        child.stdin.flush()
        stdout, stderr = child.communicate(timeout=5)
        if child.returncode != 0 or stdout.strip() != "settled":
            raise RuntimeError("bounded control unit failed: " + stderr[-1000:])
        with db_module.open_database(state, lock=False, read_only=True) as db:
            current = status(
                db.connection, now=datetime.now(UTC), selector=Selector("host", PROBE_HOST)
            )
            if current["state"] != "paused" or current["draining_attempts"]:
                raise ValueError("completed unit left an unexplained drain")
        active_seconds = monotonic() - active_started
        resumed = change_control(
            state,
            selector=Selector("host", PROBE_HOST),
            paused=False,
            actor="h13-acceptance",
            reason="offline control acceptance complete",
            now=datetime.now(UTC),
            timeout=5,
            hosts=(PROBE_HOST,),
        )
        paused = False
        if resumed["state"] != "running":
            raise ValueError("probe resume did not persist")
        restored = control_restore_probe(captured, expected, output.with_suffix(".control-probe"))
        return {
            "action_id": action_id,
            "selector": PROBE_HOST,
            "pause_id": pause["pause_id"],
            "pause_persistence_seconds": elapsed,
            "transitions": ["pausing", "paused", "running"],
            "restart_controls": restarted,
            "active_seconds": active_seconds,
            "active_bound_seconds": 30,
            "restore": restored,
            "source_requests": 0,
        }
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
        # On failure preserve the diagnostic pause and any active admission for
        # explicit coordinator recovery. Never silently resume a failed probe.
        if paused:
            output.with_name(output.stem + "-probe-held.json").write_text(
                json.dumps(
                    {"action_id": action_id, "selector": PROBE_HOST, "held": True}, sort_keys=True
                )
                + "\n"
            )


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
                if after["schema"] != 12 or after["tables"] != receipt["before"]["tables"]:
                    raise ValueError("migration changed protected state or target schema")
                integrity(db.connection)
                receipt["migration"] = verify_migration(db.connection)
                migrated_controls = control_digest(db.connection)
            receipt["control_probe"] = control_probe(state, source, output)
            with db_module.open_database(state, lock=False, read_only=True) as db:
                before_doctor_controls = control_digest(db.connection)
            receipt["doctor"] = doctor_check(state, source, output)
            with db_module.open_database(state, lock=False, read_only=True) as db:
                if protected_state(db.connection) != after:
                    raise ValueError("acceptance changed protected persisted state")
                final_controls = control_digest(db.connection)
                if final_controls != before_doctor_controls:
                    raise ValueError("read-only doctor changed control rows")
                if final_controls["operator_pauses"] != migrated_controls["operator_pauses"]:
                    raise ValueError("control probe changed an existing pause")
                if (
                    final_controls["control_events"]["count"]
                    != receipt["migration"]["legacy_events"] + 2
                ):
                    raise ValueError("unexpected control audit additions")
                verify_probe_completion(
                    db.connection, receipt["migration"], receipt["control_probe"]
                )
                receipt["final_controls"] = final_controls
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
        raise TimeoutError("bounded H13 acceptance exceeded its execution window")

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
