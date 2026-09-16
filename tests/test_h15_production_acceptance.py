"""Offline operational gates and the real schema-13-to-14 migration."""

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
    driver = module("accept_h15")
    shutil.copytree(root / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(root / "config", source / "config")
    (source / "journal/tools/runtime").mkdir(parents=True)
    shutil.copyfile(
        root / "journal/tools/runtime/accept_h11.py", source / "journal/tools/runtime/accept_h11.py"
    )
    runtime = source / "src/swingset/state/db.py"
    runtime.write_text(re.sub(r"SCHEMA_VERSION = \d+", "SCHEMA_VERSION = 14", runtime.read_text()))
    for path in (source / "src/swingset/state/migrations").glob("*.sql"):
        if int(path.name.split("_")[0]) > 14:
            path.unlink()
    with monkeypatch.context() as old:
        old.setattr(db_module, "SCHEMA_VERSION", 13)
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
            db.connection.executemany(
                "INSERT INTO pending_work VALUES ('project',?,?, '2026-09-13T00:00:00+00:00')",
                [("calendar", "retained-year"), ("source_index", "retained-index")],
            )

            db.connection.execute(
                "INSERT INTO requirement_cohorts(cohort_id,created_at,policy_version,bounded) VALUES ('h11','2026-09-13T00:00:00+00:00','1',0)"
            )
    with monkeypatch.context() as old:
        old.setattr(db_module, "SCHEMA_VERSION", 13)
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
            db.connection.execute(
                "INSERT INTO observations VALUES ('retained-parent',?,'parent','event_sheet','source_event','scoringdance:parent',0,'1','1',?)",
                (
                    parent.watch_id,
                    '{"kind":"event_sheet","source_event_ref":"scoringdance:parent","name_raw":null,"round_links":[]}',
                ),
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
            state, db.connection, checkpoint, schema_version=13, versions={}, input_bundle_hash=None
        )
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    monkeypatch.setattr(driver, "PRIVATE_BACKUP_COMMIT", "private-test-only")
    monkeypatch.setattr(
        driver, "PRIVATE_BACKUP_MANIFEST", driver.prior.digest(checkpoint / "checkpoint.json")
    )
    receipt = {
        "format": "h15-reviewed-source-v1",
        "schema": 14,
        "acquisition_enabled": False,
        "repairs_activated": False,
        "files": {
            p.relative_to(source).as_posix(): driver.prior.digest(p)
            for p in source.rglob("*")
            if p.is_file()
        },
    }
    (source / "h15-source.json").write_text(json.dumps(receipt))
    source_hash = driver.prior.digest(source / "h15-source.json")
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 14)
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
                "schema_version": 13,
                "baseline_commit": driver.V4_COMMIT,
                "source_receipt_sha256": driver.BASE_SOURCE_RECEIPT_SHA256,
            }
        )
    )
    gate = {
        "format": "h15-operational-gate-v1",
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
    assert not result["executed"] and result["before"]["schema"] == 13
    assert {
        path.relative_to(checkpoint): path.read_bytes()
        for path in checkpoint.rglob("*")
        if path.is_file()
    } == before
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 13
        assert not db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='derivation_scopes'"
        ).fetchone()


def test_real_migration_preserves_all_controls_attempts_and_readonly_doctor(operation, tmp_path):
    state, source, _, gate = operation
    result = invoke(state, source, gate, tmp_path / "execute.json", execute=True)
    assert result["passed"] and result["executed"] and result["network_requests"] == 0
    assert not result["service_executed"] and not result["published"]
    assert result["before"]["tables"] == result["after"]["tables"]
    assert result["before"]["columns"] == result["after"]["columns"]
    assert result["doctor"]["fresh_process_equal"] and result["baseline_unchanged"]
    assert not result["retained_assessment"]["service_executed"]
    assert result["migration"]["unmaterialized_seeded_scopes"]["count"] == 3
    assert result["before"]["tables"]["execution_admissions"]["count"] == 1
    assert result["before"]["tables"]["work_attempts"]["count"] == 1
    assert set(result["migration"]["empty_proof_tables"]) == {
        "derivation_dependency_sets",
        "derivation_generations",
        "derivation_rows",
    }
    assert result["migration"]["snapshot_recipe_cache_null"]
    assert result["sizes"]["source_bytes"] > 0 and result["doctor"]["json_bytes"] > 0
    with pytest.raises(ValueError, match="unmigrated schema 13"):
        invoke(state, source, gate, tmp_path / "second.json", execute=True)


@pytest.mark.parametrize(
    "problem",
    [
        "gate",
        "backup_identity",
        "source_hash",
        "future_runtime",
        "checkpoint_sidecar",
        "checkpoint_size",
        "deployed_source",
        "control_drift",
        "budget_drift",
    ],
)
def test_preflight_rejects_unreviewed_or_changed_inputs(operation, tmp_path, monkeypatch, problem):
    state, source, checkpoint, gate_path = operation
    gate = json.loads(gate_path.read_bytes())
    if problem == "gate":
        gate["private_backup_verified"] = False
    elif problem == "backup_identity":
        gate["private_backup_commit"] = "different-private-backup"
    elif problem == "source_hash":
        gate["source_receipt_sha256"] = "unreviewed"
    elif problem == "future_runtime":
        monkeypatch.setattr(db_module, "SCHEMA_VERSION", 15)
    elif problem == "checkpoint_sidecar":
        (checkpoint / "state.sqlite-shm").write_bytes(b"extra")
    elif problem == "checkpoint_size":
        manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
        manifest["files"]["state.sqlite"]["size"] += 1
        (checkpoint / "checkpoint.json").write_text(json.dumps(manifest))
        gate["checkpoint_manifest_sha256"] = driver.prior.digest(checkpoint / "checkpoint.json")
    elif problem == "deployed_source":
        evidence_path = gate_path.parent / gate["private_backup_receipt"]
        evidence = json.loads(evidence_path.read_bytes())
        evidence["source_receipt_sha256"] = "unreviewed"
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
        assert db.schema_version == 13


def test_verification_only_selectors_cannot_write_production_service(
    operation, tmp_path, monkeypatch
):
    state, source, _, gate = operation
    original = driver.retained_assessment

    def attempted_service(conn, *args, **kwargs):
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE derivation_scopes SET desired_fingerprint='fabricated'")
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(driver, "retained_assessment", attempted_service)
    assert invoke(state, source, gate, tmp_path / "execute.json", execute=True)["passed"]


def refresh_gate_evidence(gate_path, checkpoint):
    gate = json.loads(gate_path.read_bytes())
    manifest_path = checkpoint / "checkpoint.json"
    manifest = json.loads(manifest_path.read_bytes())
    gate["checkpoint_manifest_sha256"] = driver.prior.digest(manifest_path)
    driver.PRIVATE_BACKUP_MANIFEST = gate["checkpoint_manifest_sha256"]
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
    nested = source / "unexpected/h15-source.json"
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
        assert db.schema_version == 13
        assert (
            db.connection.execute("SELECT state FROM execution_admissions").fetchone()[0]
            == "active"
        )


def test_original_column_comparison_survives_added_columns_but_detects_changed_values(tmp_path):
    global driver
    driver = module("accept_h15")
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(
            "CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE retained(id INTEGER,private_reason TEXT); INSERT INTO retained VALUES (1,'keep');"
        )
        before = driver.protected_state(conn)
        conn.execute("ALTER TABLE retained ADD COLUMN new_nullable TEXT")
        assert driver.protected_state(conn, before["columns"]) == before
        conn.execute("UPDATE retained SET private_reason='changed'")
        assert driver.protected_state(conn, before["columns"])["tables"] != before["tables"]


def test_assessment_streams_lost_queue_scope_catalog_without_claiming_materialization(
    operation, tmp_path
):
    state, source, _, gate = operation
    result = invoke(state, source, gate, tmp_path / "execute.json", execute=True)
    with open_database(state, lock=False, read_only=True) as db:
        before = db.connection.total_changes
        tiny_pages = driver.retained_assessment(db.connection, page_size=1)
        large_pages = driver.retained_assessment(db.connection, page_size=1000)
        assert db.connection.total_changes == before
        assert tiny_pages["scope_counts"] == large_pages["scope_counts"]
        assert tiny_pages["pages"] == tiny_pages["scopes"]
        assert tiny_pages["scope_counts"]["project/source_event"] == 1
        assert tiny_pages["all_unmaterialized"] and tiny_pages["desired_fingerprints_computed"] == 0
        assert not db.connection.execute("SELECT 1 FROM derivation_generations").fetchone()
        assert not db.connection.execute(
            "SELECT 1 FROM pending_work WHERE unit_kind='source_event'"
        ).fetchone()
        assert result["migration"]["dependency_change_tokens"]["count"] == 1


@pytest.mark.parametrize(
    "cache", ["extract_recipe_sha256", "materialized_signature", "desired_fingerprint"]
)
def test_migration_never_adopts_legacy_rows_as_proven_current(operation, cache):
    state, _, _, _ = operation
    with open_database(state) as db:
        if cache == "extract_recipe_sha256":
            db.connection.execute("UPDATE snapshots SET extract_recipe_sha256='invented'")
        else:
            db.connection.execute(f"UPDATE derivation_scopes SET {cache}='invented'")
        with pytest.raises(ValueError, match="fabricated"):
            driver.verify_migration(db.connection)


def test_large_retained_scope_assessment_is_paginated_bounded_and_readonly(tmp_path):
    helper = module("accept_h15")
    with open_database(tmp_path) as db:
        db.connection.executemany(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project','source_index',?,'2026-09-13T00:00:00+00:00')",
            ((f"retained-{number:05d}",) for number in range(5000)),
        )
        db.connection.execute("PRAGMA query_only=ON")
        before = db.connection.total_changes
        result = helper.retained_assessment(db.connection, page_size=31)
        assert result["scope_counts"]["project/source_index"] == 5000
        assert result["scopes"] == 5000
        assert result["pages"] == 162
        assert len(result["sample"]) == 20
        assert len(json.dumps(result)) < 5000
        assert db.connection.total_changes == before
        assert not db.connection.execute("SELECT 1 FROM derivation_generations").fetchone()
        with pytest.raises(TimeoutError, match="bounded window"):
            helper.retained_assessment(db.connection, max_seconds=1e-12)
        # The progress handler is removed even after a timed-out scan.
        assert db.connection.execute("SELECT count(*) FROM derivation_scopes").fetchone()[0] == 5000
