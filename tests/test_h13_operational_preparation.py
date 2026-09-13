"""Offline operational gates and the real schema-11-to-12 migration."""

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
    driver = module("accept_h13")
    shutil.copytree(root / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(root / "config", source / "config")
    (source / "research").mkdir()
    shutil.copyfile(root / "research/accept_h11.py", source / "research/accept_h11.py")
    runtime = source / "src/swingset/state/db.py"
    runtime.write_text(re.sub(r"SCHEMA_VERSION = \d+", "SCHEMA_VERSION = 12", runtime.read_text()))
    for path in (source / "src/swingset/state/migrations").glob("*.sql"):
        if int(path.name.split("_")[0]) > 12:
            path.unlink()
    with monkeypatch.context() as old:
        old.setattr(db_module, "SCHEMA_VERSION", 11)
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
    candidate = state / "candidates/v4"
    (candidate / "_meta").mkdir(parents=True)
    (candidate / "PUBLISHED").write_text(json.dumps({"commit": driver.V4_COMMIT}))
    (candidate / "BUILT").write_text("{}")
    (candidate / "_meta/manifest.json").write_text('{"files": {}}')
    (state / "baseline").symlink_to(candidate)
    with open_database(state, lock=False, read_only=True) as db:
        create_checkpoint(
            state, db.connection, checkpoint, schema_version=11, versions={}, input_bundle_hash=None
        )
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    receipt = {
        "format": "h13-reviewed-source-v1",
        "schema": 12,
        "acquisition_enabled": False,
        "repairs_activated": False,
        "files": {
            p.relative_to(source).as_posix(): driver.prior.digest(p)
            for p in source.rglob("*")
            if p.is_file()
        },
    }
    (source / "h13-source.json").write_text(json.dumps(receipt))
    source_hash = driver.prior.digest(source / "h13-source.json")
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 12)
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
                "schema_version": 11,
                "baseline_commit": driver.V4_COMMIT,
                "source_receipt_sha256": driver.BASE_SOURCE_RECEIPT_SHA256,
            }
        )
    )
    gate = {
        "format": "h13-operational-gate-v1",
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
    result = invoke(state, source, gate, tmp_path / "preflight.json")
    assert result["before"]["schema"] == 11 and not result["executed"]
    assert {
        p.relative_to(checkpoint): p.read_bytes() for p in checkpoint.rglob("*") if p.is_file()
    } == before
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 11
        assert not db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='execution_admissions'"
        ).fetchone()


def test_real_migration_preserves_cohorts_controls_pending_and_restarts(
    operation, tmp_path, monkeypatch
):
    state, source, _, gate = operation
    real_doctor = driver.doctor_check

    def settled_doctor(*args):
        with open_database(state, lock=False, read_only=True) as db:
            assert not db.connection.execute(
                "SELECT 1 FROM execution_admissions WHERE state!='settled'"
            ).fetchone()
            assert not db.connection.execute(
                "SELECT 1 FROM operator_pauses WHERE scope_id=?", (driver.PROBE_HOST,)
            ).fetchone()
        return real_doctor(*args)

    monkeypatch.setattr(driver, "doctor_check", settled_doctor)
    result = invoke(state, source, gate, tmp_path / "execute.json", execute=True)
    assert result["passed"] and result["executed"] and result["network_requests"] == 0
    assert result["before"]["tables"] == result["after"]["tables"]
    assert result["migration"]["legacy_pauses"] == 1
    assert result["final_controls"]["control_events"]["count"] == 3
    assert result["control_probe"]["transitions"] == ["pausing", "paused", "running"]
    assert result["control_probe"]["restore"]["restore_pending_enforced"]
    assert result["control_probe"]["restart_controls"]["fresh_process_equal"]
    assert result["control_probe"]["active_seconds"] < 30
    assert result["doctor"]["fresh_process_equal"] and result["baseline_unchanged"]
    with pytest.raises(ValueError, match="unmigrated schema 11"):
        invoke(state, source, gate, tmp_path / "second.json", execute=True)


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
        monkeypatch.setattr(db_module, "SCHEMA_VERSION", 13)
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
        invoke(state, source, gate_path, tmp_path / "rejected.json", execute=True)
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 11


def test_legacy_pause_provenance_corruption_is_detected(operation):
    state, _, _, _ = operation
    with open_database(state) as db:
        driver.verify_migration(db.connection)
        db.connection.execute("UPDATE operator_pauses SET actor='invented'")
        with pytest.raises(ValueError, match="legacy pause provenance"):
            driver.verify_migration(db.connection)


def test_output_checkpoint_rejected_before_lock_or_preflight(operation, monkeypatch):
    state, source, checkpoint, gate = operation
    monkeypatch.setattr(driver, "preflight", lambda *_: pytest.fail("must reject output first"))
    with pytest.raises(ValueError, match="receipt output"):
        invoke(state, source, gate, checkpoint / "receipt.json", execute=True)
    assert not (checkpoint / "receipt.json").exists()


def test_checkpoint_as_state_never_creates_lock(operation, tmp_path):
    _, source, checkpoint, gate = operation
    assert not (checkpoint / "state.lock").exists()
    with pytest.raises(ValueError, match="never migrate"):
        invoke(checkpoint, source, gate, tmp_path / "rejected.json", execute=True)
    assert not (checkpoint / "state.lock").exists()


def test_writer_lock_refuses_concurrent_execution(operation, tmp_path):
    import fcntl

    state, source, _, gate = operation
    with (state / "state.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            invoke(state, source, gate, tmp_path / "rejected.json", execute=True)
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 11


def test_doctor_side_effect_fails_acceptance_and_retains_failure_receipt(
    operation, tmp_path, monkeypatch
):
    state, source, _, gate = operation

    def corrupt_doctor(*_):
        with open_database(state, lock=False) as db:
            db.connection.execute("UPDATE accepted_inputs SET digest='accidental mutation'")
        return {"test_corruption": True}

    monkeypatch.setattr(driver, "doctor_check", corrupt_doctor)
    monkeypatch.setattr(driver, "control_probe", lambda *_: {})
    output = tmp_path / "failed.json"
    with pytest.raises(ValueError, match="acceptance changed protected"):
        invoke(state, source, gate, output, execute=True)
    receipt = json.loads(output.read_bytes())
    assert receipt["executed"] and not receipt["passed"]
    assert receipt["error"]["type"] == "ValueError"


@pytest.mark.parametrize("directory", ["blobs", "extracts", "inputs"])
def test_receipt_cannot_enter_artifact_roots(operation, directory):
    state, source, _, gate = operation
    with pytest.raises(ValueError, match="retained artifact roots"):
        invoke(state, source, gate, state / directory / "receipt.json")


def test_independent_source_receipt_parameter_must_match_gate(operation, tmp_path):
    state, source, _, gate = operation
    with pytest.raises(ValueError, match="frozen H13 source receipt"):
        driver.run(
            state, source, gate, tmp_path / "rejected.json", expected_receipt="0" * 64, execute=True
        )
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 11


def test_existing_global_pause_blocks_probe_without_migration(operation, tmp_path):
    state, source, _, gate = operation
    with sqlite3.connect(state / "state.sqlite") as conn:
        conn.execute("UPDATE operator_pauses SET scope_kind='all',scope_id='all'")
    with pytest.raises(ValueError, match="existing global pause"):
        invoke(state, source, gate, tmp_path / "rejected.json", execute=True)
    with open_database(state, lock=False, read_only=True) as db:
        assert db.schema_version == 11
        assert (
            db.connection.execute("SELECT reason FROM operator_pauses").fetchone()[0]
            == "keep pause"
        )


def test_failed_probe_preserves_its_pause_and_auditable_active_action(
    operation, tmp_path, monkeypatch
):
    state, source, _, gate = operation
    monkeypatch.setattr(
        driver,
        "paused_status_check",
        lambda *_: (_ for _ in ()).throw(RuntimeError("probe doctor failed")),
    )
    output = tmp_path / "failed-probe.json"
    with pytest.raises(RuntimeError, match="probe doctor failed"):
        invoke(state, source, gate, output, execute=True)
    receipt = json.loads(output.read_bytes())
    held = json.loads((tmp_path / "failed-probe-probe-held.json").read_bytes())
    assert receipt["executed"] and not receipt["passed"] and held["held"]
    with open_database(state, lock=False, read_only=True) as db:
        assert db.connection.execute(
            "SELECT 1 FROM operator_pauses WHERE scope_kind='host' AND scope_id=?",
            (driver.PROBE_HOST,),
        ).fetchone()
        assert (
            db.connection.execute(
                "SELECT state FROM execution_admissions WHERE action_id=?", (held["action_id"],)
            ).fetchone()[0]
            == "active"
        )
        assert (
            db.connection.execute(
                "SELECT reason FROM operator_pauses WHERE scope_id='retained.example'"
            ).fetchone()[0]
            == "keep pause"
        )
