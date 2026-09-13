"""Offline operational gates and the real schema-14 read-only acceptance."""

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
    driver = module("accept_h16")
    shutil.copytree(root / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(root / "config", source / "config")
    (source / "research").mkdir()
    shutil.copyfile(root / "research/accept_h11.py", source / "research/accept_h11.py")
    runtime = source / "src/swingset/state/db.py"
    runtime.write_text(re.sub(r"SCHEMA_VERSION = \d+", "SCHEMA_VERSION = 14", runtime.read_text()))
    for path in (source / "src/swingset/state/migrations").glob("*.sql"):
        if int(path.name.split("_")[0]) > 14:
            path.unlink()
    with monkeypatch.context() as old:
        old.setattr(db_module, "SCHEMA_VERSION", 14)
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
        old.setattr(db_module, "SCHEMA_VERSION", 14)
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
            state, db.connection, checkpoint, schema_version=14, versions={}, input_bundle_hash=None
        )
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    monkeypatch.setattr(driver, "PRIVATE_BACKUP_COMMIT", "private-test-only")
    monkeypatch.setattr(
        driver, "PRIVATE_BACKUP_MANIFEST", driver.prior.digest(checkpoint / "checkpoint.json")
    )
    receipt = {
        "format": "h16-reviewed-source-v1",
        "schema": 14,
        "acquisition_enabled": False,
        "repairs_activated": False,
        "files": {
            p.relative_to(source).as_posix(): driver.prior.digest(p)
            for p in source.rglob("*")
            if p.is_file()
        },
    }
    (source / "h16-source.json").write_text(json.dumps(receipt))
    source_hash = driver.prior.digest(source / "h16-source.json")
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 14)
    monkeypatch.setattr(db_module, "__file__", str(runtime))
    monkeypatch.setattr(driver.prior, "__file__", str(source / "research/accept_h11.py"))
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
                "schema_version": 14,
                "baseline_commit": driver.V4_COMMIT,
                "source_receipt_sha256": driver.BASE_SOURCE_RECEIPT_SHA256,
            }
        )
    )
    gate = {
        "format": "h16-operational-gate-v1",
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


def test_readonly_acceptance_keeps_all_rows_proofs_controls_and_checkpoint(operation, tmp_path):
    state, source, checkpoint, gate = operation
    retained = {
        p.relative_to(checkpoint): driver.prior.digest(p)
        for p in checkpoint.rglob("*")
        if p.is_file()
    }
    with open_database(state, lock=False, read_only=True) as db:
        original = driver.protected_state(db.connection)
    assert not invoke(state, source, gate, tmp_path / "preflight.json")["executed"]
    result = invoke(state, source, gate, tmp_path / "execution.json", execute=True)
    assert result["passed"] and result["executed"]
    assert not result["migrated"] and not result["service_executed"] and not result["published"]
    assert result["network_requests"] == 0
    assert result["before"] == result["after"] == original
    assert result["doctor"]["fresh_process_equal"]
    assert result["retained_assessment"]["unfinished"] > 0
    assert result["retained_assessment"]["work_completed"] == 0
    assert result["retained_assessment"]["materialized_pointers"] == 0
    assert result["baseline_unchanged"]
    assert result["sizes"]["source_bytes"] > 0 and result["doctor"]["json_bytes"] > 0
    assert {
        p.relative_to(checkpoint): driver.prior.digest(p)
        for p in checkpoint.rglob("*")
        if p.is_file()
    } == retained
    with open_database(state, lock=False, read_only=True) as db:
        assert (
            db.connection.execute(
                "SELECT 1 FROM meta WHERE key LIKE 'correction_pending_%'"
            ).fetchone()
            is None
        )


@pytest.mark.parametrize(
    "problem",
    [
        "driver",
        "backup",
        "source",
        "future_runtime",
        "sidecar",
        "table_row",
        "schema_object",
        "sequence",
        "budget",
        "active",
    ],
)
def test_preflight_refuses_unreviewed_or_divergent_state(operation, tmp_path, monkeypatch, problem):
    state, source, checkpoint, path = operation
    gate = json.loads(path.read_bytes())
    if problem == "driver":
        gate["driver_sha256"] = "unknown"
    elif problem == "backup":
        gate["private_backup_commit"] = "unknown"
    elif problem == "source":
        (source / "unexpected.py").write_text("pass")
    elif problem == "future_runtime":
        monkeypatch.setattr(db_module, "SCHEMA_VERSION", 15)
    elif problem == "sidecar":
        (checkpoint / "state.sqlite-shm").write_bytes(b"unrecorded")
    else:
        with sqlite3.connect(state / "state.sqlite") as conn:
            if problem == "table_row":
                conn.execute("INSERT INTO meta VALUES ('correction_pending_since','fabricated')")
            elif problem == "schema_object":
                conn.execute("CREATE INDEX unexpected ON pending_work(unit_id)")
            elif problem == "sequence":
                changed = conn.execute(
                    "UPDATE sqlite_sequence SET seq=seq+100 WHERE name='work_attempts'"
                )
                assert changed.rowcount == 1
            elif problem == "budget":
                conn.execute("UPDATE host_budget SET requests=requests+1")
            else:
                conn.execute(
                    "UPDATE execution_admissions SET state='active' WHERE action_id='prior_h13_probe'"
                )
    path.write_text(json.dumps(gate))
    with pytest.raises(ValueError):
        invoke(state, source, path, tmp_path / "refused.json", execute=True)


def test_inventory_is_query_only_and_lost_enqueue_stays_unfinished(
    operation, tmp_path, monkeypatch
):
    state, source, _, gate = operation
    original = driver.retained_assessment

    def inspect(conn, *args, **kwargs):
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM pending_work")
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(driver, "retained_assessment", inspect)
    result = invoke(state, source, gate, tmp_path / "execute.json", execute=True)
    assert result["retained_assessment"]["unfinished_by_scope"]["project/inventory"] == 1
    assert result["before"]["tables"]["work_attempts"]["count"] == 1
    assert result["before"]["tables"]["execution_admissions"]["count"] == 1


def test_exact_fresh_doctor_mismatch_rejects_without_writing_state(
    operation, tmp_path, monkeypatch
):
    state, source, _, gate = operation
    original = driver.subprocess.run

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if isinstance(args[0], list) and "-c" in args[0]:
            result.stdout = '{"sha256":"changed","schema":14}'
        return result

    monkeypatch.setattr(driver.subprocess, "run", changed)
    with pytest.raises(ValueError, match="fresh-process doctor differs"):
        invoke(state, source, gate, tmp_path / "mismatch.json", execute=True)
    receipt = json.loads((tmp_path / "mismatch.json").read_bytes())
    assert not receipt["passed"] and not receipt["migrated"]


def test_checkpoint_cannot_be_target_even_before_lock_creation(operation, tmp_path):
    _, source, checkpoint, gate = operation
    before = {
        p.relative_to(checkpoint): driver.prior.digest(p)
        for p in checkpoint.rglob("*")
        if p.is_file()
    }
    with pytest.raises(ValueError, match="immutable checkpoint"):
        invoke(checkpoint, source, gate, tmp_path / "refused.json", execute=True)
    assert {
        p.relative_to(checkpoint): driver.prior.digest(p)
        for p in checkpoint.rglob("*")
        if p.is_file()
    } == before


@pytest.mark.parametrize(
    "suffix", [".json", ".sizes.json", ".doctor.json", ".doctor.txt", ".json.tmp"]
)
@pytest.mark.parametrize("symlink", [False, True])
def test_all_output_paths_are_new_and_cannot_overwrite_checkpoint_evidence(
    operation, tmp_path, suffix, symlink
):
    state, source, checkpoint, gate = operation
    output = tmp_path / "acceptance.json"
    unsafe = output.with_suffix(suffix)
    witness = checkpoint / "checkpoint.json"
    original = witness.read_bytes()
    if symlink:
        unsafe.symlink_to(witness)
    else:
        unsafe.write_bytes(b"existing operational evidence")
    with pytest.raises(ValueError, match="output paths must be new"):
        invoke(state, source, gate, output, execute=True)
    assert witness.read_bytes() == original
    assert (
        unsafe.is_symlink() if symlink else unsafe.read_bytes() == b"existing operational evidence"
    )
    assert not (output.with_suffix(".sizes.json")).exists() or suffix == ".sizes.json"
