"""Offline operational gates and the real schema-10-to-11 migration."""

import json
import re
import shutil
from pathlib import Path

import pytest
from test_h11_operational_preparation import module

from swingset.backup.checkpoint import create_checkpoint
from swingset.state import db as db_module
from swingset.state.db import open_database


@pytest.fixture
def operation(tmp_path, monkeypatch):
    state, source, checkpoint = (tmp_path / name for name in ("live", "frozen-source", "saved"))
    root = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(root))
    global driver
    driver = module("accept_h12")
    shutil.copytree(root / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(root / "config", source / "config")
    (source / "research").mkdir()
    shutil.copyfile(root / "research/accept_h11.py", source / "research/accept_h11.py")
    runtime = source / "src/swingset/state/db.py"
    runtime.write_text(re.sub(r"SCHEMA_VERSION = \d+", "SCHEMA_VERSION = 11", runtime.read_text()))
    for path in (source / "src/swingset/state/migrations").glob("*.sql"):
        if int(path.name.split("_")[0]) > 11:
            path.unlink()
    with monkeypatch.context() as old:
        old.setattr(db_module, "SCHEMA_VERSION", 10)
        with open_database(state) as db:
            db.connection.execute(
                "INSERT INTO pending_work VALUES ('parse','snapshot','retained','2026-09-13')"
            )
            db.connection.execute("INSERT INTO operator_pauses VALUES ('all','',NULL,'keep pause')")
            db.connection.execute(
                "INSERT INTO accepted_inputs VALUES ('link','versions','keep input')"
            )
            db.connection.execute(
                "INSERT INTO requirement_cohorts(cohort_id,created_at,policy_version,bounded) VALUES ('h11','2026-09-13','1',0)"
            )
    candidate = state / "candidates/v4"
    (candidate / "_meta").mkdir(parents=True)
    (candidate / "PUBLISHED").write_text(json.dumps({"commit": driver.V4_COMMIT}))
    (candidate / "BUILT").write_text("{}")
    (candidate / "_meta/manifest.json").write_text('{"files": {}}')
    (state / "baseline").symlink_to(candidate)
    with open_database(state, lock=False, read_only=True) as db:
        create_checkpoint(
            state, db.connection, checkpoint, schema_version=10, versions={}, input_bundle_hash=None
        )
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    receipt = {
        "format": "h12-reviewed-source-v1",
        "schema": 11,
        "acquisition_enabled": False,
        "repairs_activated": False,
        "files": {
            p.relative_to(source).as_posix(): driver.prior.digest(p)
            for p in source.rglob("*")
            if p.is_file()
        },
    }
    (source / "h12-source.json").write_text(json.dumps(receipt))
    source_hash = driver.prior.digest(source / "h12-source.json")
    monkeypatch.setattr(driver, "SOURCE_RECEIPT_SHA256", source_hash)
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 11)
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
                "schema_version": 10,
                "baseline_commit": driver.V4_COMMIT,
                "source_receipt_sha256": source_hash,
            }
        )
    )
    gate = {
        "format": "h12-operational-gate-v1",
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


def test_readonly_preflight_preserves_checkpoint_and_live_schema(operation, tmp_path):
    state, source, checkpoint, gate = operation
    before = {
        p.relative_to(checkpoint): p.read_bytes() for p in checkpoint.rglob("*") if p.is_file()
    }
    result = driver.run(state, source, gate, tmp_path / "preflight.json")
    assert result["before"]["schema"] == 10 and not result["executed"]
    assert {
        p.relative_to(checkpoint): p.read_bytes() for p in checkpoint.rglob("*") if p.is_file()
    } == before
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 10
        assert not db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='work_attempts'"
        ).fetchone()


def test_real_migration_preserves_cohorts_controls_pending_and_restarts(operation, tmp_path):
    state, source, _, gate = operation
    result = driver.run(state, source, gate, tmp_path / "execute.json", execute=True)
    assert result["passed"] and result["executed"] and result["network_requests"] == 0
    assert result["before"]["tables"] == result["after"]["tables"]
    assert result["new_tables"]["work_attempts"]["count"] == 0
    assert result["new_tables"]["work_generations"]["count"] == 1
    assert result["doctor"]["fresh_process_equal"] and result["baseline_unchanged"]
    with pytest.raises(ValueError, match="unmigrated schema 10"):
        driver.run(state, source, gate, tmp_path / "second.json", execute=True)


@pytest.mark.parametrize(
    "problem",
    [
        "unverified",
        "receipt",
        "future_runtime",
        "source",
        "checkpoint_sidecar",
        "pending",
        "identity_meta",
    ],
)
def test_gate_failures_precede_migration(operation, tmp_path, monkeypatch, problem):
    state, source, checkpoint, gate_path = operation
    gate = json.loads(gate_path.read_bytes())
    if problem == "unverified":
        gate["private_backup_verified"] = False
    elif problem == "receipt":
        gate["private_backup_commit"] = "unrelated"
    elif problem == "future_runtime":
        monkeypatch.setattr(db_module, "SCHEMA_VERSION", 12)
    elif problem == "source":
        (source / "src/unreviewed.py").write_text("# future work")
    elif problem == "checkpoint_sidecar":
        (checkpoint / "state.sqlite-wal").write_bytes(b"unexpected")
    else:
        with open_database(state, lock=False, read_only=True) as db:
            # A separate explicit connection simulates an intervening live writer.
            import sqlite3

            with sqlite3.connect(state / "state.sqlite") as writer:
                if problem == "pending":
                    writer.execute("UPDATE pending_work SET enqueued_at='changed'")
                else:
                    writer.execute("INSERT INTO meta VALUES ('identity_changed','unsupported')")
    gate_path.write_text(json.dumps(gate))
    with pytest.raises(ValueError):
        driver.run(state, source, gate_path, tmp_path / "rejected.json", execute=True)
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 10


def test_new_queue_generation_corruption_is_detected(operation):
    state, _, _, _ = operation
    with open_database(state) as db:
        driver.verify_new_tables(db.connection)
        db.connection.execute("UPDATE work_generations SET retry_generation=1")
        with pytest.raises(ValueError, match="queue generations"):
            driver.verify_new_tables(db.connection)


def test_output_checkpoint_rejected_before_lock_or_preflight(operation, monkeypatch):
    state, source, checkpoint, gate = operation
    monkeypatch.setattr(driver, "preflight", lambda *_: pytest.fail("must reject output first"))
    with pytest.raises(ValueError, match="receipt output"):
        driver.run(state, source, gate, checkpoint / "receipt.json", execute=True)
    assert not (checkpoint / "receipt.json").exists()


def test_checkpoint_as_state_never_creates_lock(operation, tmp_path):
    _, source, checkpoint, gate = operation
    assert not (checkpoint / "state.lock").exists()
    with pytest.raises(ValueError, match="never migrate"):
        driver.run(checkpoint, source, gate, tmp_path / "rejected.json", execute=True)
    assert not (checkpoint / "state.lock").exists()


def test_writer_lock_refuses_concurrent_execution(operation, tmp_path):
    import fcntl

    state, source, _, gate = operation
    with (state / "state.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            driver.run(state, source, gate, tmp_path / "rejected.json", execute=True)
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 10


def test_doctor_side_effect_fails_acceptance_and_retains_failure_receipt(
    operation, tmp_path, monkeypatch
):
    state, source, _, gate = operation

    def corrupt_doctor(*_):
        with open_database(state, lock=False) as db:
            db.connection.execute("UPDATE accepted_inputs SET digest='accidental mutation'")
        return {"test_corruption": True}

    monkeypatch.setattr(driver, "doctor_check", corrupt_doctor)
    output = tmp_path / "failed.json"
    with pytest.raises(ValueError, match="read-only doctor changed"):
        driver.run(state, source, gate, output, execute=True)
    receipt = json.loads(output.read_bytes())
    assert receipt["executed"] and not receipt["passed"]
    assert receipt["error"]["type"] == "ValueError"


@pytest.mark.parametrize("directory", ["blobs", "extracts", "inputs"])
def test_receipt_cannot_enter_artifact_roots(operation, directory):
    state, source, _, gate = operation
    with pytest.raises(ValueError, match="retained artifact roots"):
        driver.run(state, source, gate, state / directory / "receipt.json")
