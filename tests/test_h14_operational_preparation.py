"""Offline operational gates and the real schema-12-to-13 migration."""

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from test_h11_operational_preparation import module

from swingset.backup.checkpoint import create_checkpoint
from swingset.state import db as db_module
from swingset.state.db import open_database


def invoke(state, source, gate, output, *, execute=False):
    return driver.run(
        state,
        source,
        gate,
        output,
        expected_receipt=json.loads(gate.read_bytes())["source_receipt_sha256"],
        execute=execute,
    )


@pytest.fixture
def operation(tmp_path, monkeypatch):
    state, source, checkpoint = (tmp_path / name for name in ("live", "frozen-source", "saved"))
    root = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(root))
    global driver
    driver = module("accept_h14")
    shutil.copytree(root / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(root / "config", source / "config")
    (source / "journal/tools/runtime").mkdir(parents=True)
    shutil.copyfile(
        root / "journal/tools/runtime/accept_h11.py", source / "journal/tools/runtime/accept_h11.py"
    )
    runtime = source / "src/swingset/state/db.py"
    runtime.write_text(re.sub(r"SCHEMA_VERSION = \d+", "SCHEMA_VERSION = 13", runtime.read_text()))
    for path in (source / "src/swingset/state/migrations").glob("*.sql"):
        if int(path.name.split("_")[0]) > 13:
            path.unlink()
    with monkeypatch.context() as old:
        old.setattr(db_module, "SCHEMA_VERSION", 12)
        with open_database(state) as db:
            db.connection.execute(
                "INSERT INTO pending_work VALUES ('parse','snapshot','retained','2026-09-13T00:00:00+00:00')"
            )
            db.connection.execute(
                "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason) VALUES ('host','retained.example',NULL,'keep pause')"
            )
            db.connection.execute(
                "INSERT INTO accepted_inputs VALUES ('link','versions','keep input')"
            )
            db.connection.execute(
                "INSERT INTO requirement_cohorts(cohort_id,created_at,policy_version,bounded) VALUES ('h11','2026-09-13T00:00:00+00:00','1',0)"
            )
    with monkeypatch.context() as old:
        old.setattr(db_module, "SCHEMA_VERSION", 12)
        with open_database(state) as db:
            from swingset.clock import FakeClock
            from swingset.fetch.archive import Archive
            from swingset.schedule.watches import upsert_watch
            from swingset.sources.base import WatchSpec
            from swingset.state.attempts import begin_attempt, finish_attempt
            from swingset.state.controls import ActionScope, admission, settle
            from swingset.state.work import WorkUnit

            clock = FakeClock()
            run = db.start_run(clock.now())
            parent = WatchSpec(
                "",
                "scoringdance",
                "event",
                "GET",
                "https://scoring.dance/parent",
                "scoringdance.event",
            )
            child = WatchSpec(
                "",
                "scoringdance",
                "round",
                "GET",
                "https://scoring.dance/child",
                "scoringdance.round",
            )
            upsert_watch(db.connection, parent, clock.now())
            sha = Archive(state).store_body(b"retained parent body")
            db.connection.execute(
                "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) VALUES ('parent',?,'GET',?,?,200,?,20,1,?,'Ok')",
                (parent.watch_id, parent.url, clock.now().isoformat(), sha, run),
            )
            upsert_watch(
                db.connection,
                child,
                clock.now(),
                parent_watch_id=parent.watch_id,
                snapshot_id="parent",
            )
            db.connection.execute(
                "INSERT INTO host_budget VALUES ('scoring.dance','2026-09-13',725,0)"
            )
            attempt = begin_attempt(
                db, WorkUnit("parse", "snapshot", "retained"), now=clock.now(), run_id=run
            )
            finish_attempt(
                db, attempt, now=clock.now(), outcome="blocked", reason_code="retained-test-reason"
            )
            with admission(
                db,
                action_id="prior_h13_probe",
                action_kind="acceptance_probe",
                scope=ActionScope(host="prior-probe.invalid"),
                now=clock.now(),
            ):
                pass
            with db.transaction() as conn:
                settle(conn, "prior_h13_probe", now=clock.now(), outcome="completed")
    candidate = state / "candidates/v4"
    (candidate / "_meta").mkdir(parents=True)
    (candidate / "PUBLISHED").write_text(json.dumps({"commit": driver.V4_COMMIT}))
    (candidate / "BUILT").write_text("{}")
    (candidate / "_meta/manifest.json").write_text('{"files": {}}')
    (state / "baseline").symlink_to(candidate)
    with open_database(state, lock=False, read_only=True) as db:
        create_checkpoint(
            state, db.connection, checkpoint, schema_version=12, versions={}, input_bundle_hash=None
        )
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    receipt = {
        "format": "h14-reviewed-source-v1",
        "schema": 13,
        "acquisition_enabled": False,
        "repairs_activated": False,
        "files": {
            p.relative_to(source).as_posix(): driver.prior.digest(p)
            for p in source.rglob("*")
            if p.is_file()
        },
    }
    (source / "h14-source.json").write_text(json.dumps(receipt))
    source_hash = driver.prior.digest(source / "h14-source.json")
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 13)
    monkeypatch.setattr(db_module, "__file__", str(runtime))
    monkeypatch.setattr(
        driver.prior, "__file__", str(source / "journal/tools/runtime/accept_h11.py")
    )
    monkeypatch.setattr(driver.prior, "system_hold", lambda _: {"test_held": True})
    monkeypatch.setenv("PYTHONPATH", str(source / "src") + ":" + str(source))
    evidence = tmp_path / "backup.json"
    evidence.write_text(
        json.dumps(
            {
                "commit": "private-test-only",
                "checkpoint": str(checkpoint),
                "manifest_hash": driver.prior.digest(checkpoint / "checkpoint.json"),
                "files": len(manifest["files"]),
                "finished_at": "2026-09-13T00:00:00Z",
                "schema_version": 12,
                "baseline_commit": driver.V4_COMMIT,
                "source_receipt_sha256": driver.BASE_SOURCE_RECEIPT_SHA256,
                "verification_source_receipt_sha256": driver.BACKUP_VERIFIER_RECEIPT_SHA256,
                "verification_source": "/nix/store/0yq6dbr63yldsr1fkgfzyvqpm2vax1sg-swingset-h13-checkpoint-fix-source",
            }
        )
    )
    gate = {
        "format": "h14-operational-gate-v1",
        "v4_verified": True,
        "private_backup_verified": True,
        "private_backup_commit": "private-test-only",
        "verified_at": "2026-09-13T00:00:00Z",
        "private_backup_receipt": evidence.name,
        "evidence_files": {evidence.name: driver.prior.digest(evidence)},
        "v4_commit": driver.V4_COMMIT,
        "v4_manifest_sha256": driver.prior.digest(candidate / "_meta/manifest.json"),
        "checkpoint": str(checkpoint),
        "checkpoint_manifest_sha256": driver.prior.digest(checkpoint / "checkpoint.json"),
        "source_receipt_sha256": source_hash,
        "driver_sha256": driver.prior.digest(Path(driver.__file__)),
    }
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps(gate))
    return state, source, checkpoint, gate_path


def test_readonly_preflight_keeps_original_schema_and_checkpoint_immutable(operation, tmp_path):
    state, source, checkpoint, gate = operation
    before = {
        path.relative_to(checkpoint): path.read_bytes()
        for path in checkpoint.rglob("*")
        if path.is_file()
    }
    result = invoke(state, source, gate, tmp_path / "preflight.json")
    assert not result["executed"] and result["before"]["schema"] == 12
    assert {
        path.relative_to(checkpoint): path.read_bytes()
        for path in checkpoint.rglob("*")
        if path.is_file()
    } == before
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 12
        assert not db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='scheduler_requests'"
        ).fetchone()


def test_real_migration_preserves_all_controls_attempts_and_readonly_doctor(operation, tmp_path):
    state, source, _, gate = operation
    result = invoke(state, source, gate, tmp_path / "execute.json", execute=True)
    assert result["passed"] and result["executed"] and result["network_requests"] == 0
    assert not result["service_executed"] and not result["published"]
    assert result["before"]["tables"] == result["after"]["tables"]
    assert result["before"]["columns"] == result["after"]["columns"]
    assert result["doctor"]["fresh_process_equal"] and result["baseline_unchanged"]
    assert not result["fair_inventory"]["service_executed"]
    assert result["migration"]["parent_relationships"]["count"] == 1
    assert result["before"]["tables"]["execution_admissions"]["count"] == 1
    assert result["before"]["tables"]["work_attempts"]["count"] == 1
    assert set(result["migration"]["empty_new_service_tables"]) == {
        "scheduler_requests",
        "scheduler_offline_service",
        "scheduler_watch_state",
    }
    with pytest.raises(ValueError, match="unmigrated schema 12"):
        invoke(state, source, gate, tmp_path / "second.json", execute=True)


@pytest.mark.parametrize(
    "problem",
    [
        "gate",
        "source_hash",
        "future_runtime",
        "checkpoint_sidecar",
        "checkpoint_size",
        "deployed_source",
        "verifier_source",
        "control_drift",
        "budget_drift",
    ],
)
def test_preflight_rejects_unreviewed_or_changed_inputs(operation, tmp_path, monkeypatch, problem):
    state, source, checkpoint, gate_path = operation
    gate = json.loads(gate_path.read_bytes())
    if problem == "gate":
        gate["private_backup_verified"] = False
    elif problem == "source_hash":
        gate["source_receipt_sha256"] = "unreviewed"
    elif problem == "future_runtime":
        monkeypatch.setattr(db_module, "SCHEMA_VERSION", 14)
    elif problem == "checkpoint_sidecar":
        (checkpoint / "state.sqlite-shm").write_bytes(b"extra")
    elif problem == "checkpoint_size":
        manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
        manifest["files"]["state.sqlite"]["size"] += 1
        (checkpoint / "checkpoint.json").write_text(json.dumps(manifest))
        gate["checkpoint_manifest_sha256"] = driver.prior.digest(checkpoint / "checkpoint.json")
    elif problem in {"deployed_source", "verifier_source"}:
        evidence_path = gate_path.parent / gate["private_backup_receipt"]
        evidence = json.loads(evidence_path.read_bytes())
        evidence[
            "source_receipt_sha256"
            if problem == "deployed_source"
            else "verification_source_receipt_sha256"
        ] = "unreviewed"
        evidence_path.write_text(json.dumps(evidence))
        gate["evidence_files"][evidence_path.name] = driver.prior.digest(evidence_path)
    else:
        with sqlite3.connect(state / "state.sqlite") as conn:
            if problem == "control_drift":
                conn.execute("UPDATE operator_pauses SET reason='changed'")
            else:
                conn.execute("UPDATE host_budget SET requests=requests+1")
    gate_path.write_text(json.dumps(gate))
    with pytest.raises(ValueError):
        invoke(state, source, gate_path, tmp_path / "refused.json")
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 12


def test_verification_only_selectors_cannot_write_production_service(
    operation, tmp_path, monkeypatch
):
    state, source, _, gate = operation
    original = driver.fair_inventory

    def attempted_service(conn, *args, **kwargs):
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute(
                "INSERT INTO scheduler_offline_service VALUES ('parse','snapshot',1,'now',1)"
            )
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(driver, "fair_inventory", attempted_service)
    assert invoke(state, source, gate, tmp_path / "execute.json", execute=True)["passed"]


def refresh_gate_evidence(gate_path, checkpoint):
    gate = json.loads(gate_path.read_bytes())
    manifest_path = checkpoint / "checkpoint.json"
    manifest = json.loads(manifest_path.read_bytes())
    gate["checkpoint_manifest_sha256"] = driver.prior.digest(manifest_path)
    evidence_path = gate_path.parent / gate["private_backup_receipt"]
    evidence = json.loads(evidence_path.read_bytes())
    evidence["manifest_hash"] = gate["checkpoint_manifest_sha256"]
    evidence["files"] = len(manifest["files"])
    evidence_path.write_text(json.dumps(evidence))
    gate["evidence_files"][evidence_path.name] = driver.prior.digest(evidence_path)
    gate_path.write_text(json.dumps(gate))


def test_nested_checkpoint_manifest_is_part_of_verified_closure(operation, tmp_path):
    state, source, checkpoint, gate = operation
    nested = checkpoint / "inputs/retained/checkpoint.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_bytes(b'{"retained":"nested manifest"}')
    manifest_path = checkpoint / "checkpoint.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][nested.relative_to(checkpoint).as_posix()] = {
        "size": nested.stat().st_size,
        "sha256": driver.prior.digest(nested),
    }
    manifest_path.write_text(json.dumps(manifest))
    refresh_gate_evidence(gate, checkpoint)
    assert invoke(state, source, gate, tmp_path / "preflight.json")["preflight_passed"]
    nested.write_bytes(b"changed retained manifest")
    with pytest.raises(ValueError):
        invoke(state, source, gate, tmp_path / "changed.json")


def test_extra_nested_source_receipt_does_not_escape_source_closure(operation, tmp_path):
    state, source, _, gate = operation
    nested = source / "unexpected/h14-source.json"
    nested.parent.mkdir()
    nested.write_text("{}")
    with pytest.raises(ValueError, match="source closure"):
        invoke(state, source, gate, tmp_path / "refused.json")


def test_unsettled_execution_is_not_silently_recovered_by_acceptance(operation, tmp_path):
    state, source, _, gate = operation
    with sqlite3.connect(state / "state.sqlite") as conn:
        conn.execute(
            "UPDATE execution_admissions SET state='active' WHERE action_id='prior_h13_probe'"
        )
    with pytest.raises(ValueError, match="coordinator reconciliation"):
        invoke(state, source, gate, tmp_path / "refused.json", execute=True)
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 12
        assert (
            db.connection.execute("SELECT state FROM execution_admissions").fetchone()[0]
            == "active"
        )


def test_original_column_comparison_survives_added_columns_but_detects_changed_values(tmp_path):
    global driver
    driver = module("accept_h14")
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(
            "CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE retained(id INTEGER,private_reason TEXT); INSERT INTO retained VALUES (1,'keep');"
        )
        before = driver.protected_state(conn)
        conn.execute("ALTER TABLE retained ADD COLUMN new_nullable TEXT")
        assert driver.protected_state(conn, before["columns"]) == before
        conn.execute("UPDATE retained SET private_reason='changed'")
        assert driver.protected_state(conn, before["columns"])["tables"] != before["tables"]


def test_readonly_inventory_respects_shared_offline_source_pause(tmp_path):
    from swingset.clock import FakeClock
    from swingset.state.controls import Selector, change_control
    from swingset.state.work import WorkUnit, enqueue

    helper = module("accept_h14")
    clock = FakeClock()
    with open_database(tmp_path) as db:
        enqueue(
            db.connection, [WorkUnit("project", "map", "all")], enqueued_at=clock.now().isoformat()
        )
        before = helper.fair_inventory(db.connection, Path(__file__).parents[1], now=clock.now())
        assert before["selected_offline"] == {
            "stage": "project",
            "unit_kind": "inventory",
            "unit_id": "all",
        }
        change_control(
            tmp_path,
            selector=Selector("source", "wsdc_registry"),
            paused=True,
            actor="offline-reviewer",
            reason="retained shared dependency",
            now=clock.now(),
        )
        db.connection.execute("PRAGMA query_only=ON")
        after = helper.fair_inventory(db.connection, Path(__file__).parents[1], now=clock.now())
        assert after["selected_offline"] is None
        assert not after["service_executed"]
        assert db.connection.execute("SELECT count(*) FROM pending_work").fetchone()[0] == 1
        assert (
            db.connection.execute("SELECT count(*) FROM scheduler_offline_service").fetchone()[0]
            == 0
        )
