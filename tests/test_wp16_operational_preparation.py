"""Future WP16 migration acceptance uses real offline schema14 evidence only."""

import json
import shutil
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_h11_operational_preparation import module

from swingset.backup.checkpoint import create_checkpoint
from swingset.state import db as db_module
from swingset.state.db import open_database


@pytest.fixture
def operation(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(root))
    global driver
    driver = module("accept_wp16")
    state = tmp_path / "live"
    source = tmp_path / "source"
    checkpoint = tmp_path / "checkpoint"
    shutil.copytree(root / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(root / "config", source / "config")
    (source / "journal/tools/runtime").mkdir(parents=True)
    shutil.copyfile(
        root / "journal/tools/runtime/accept_h11.py", source / "journal/tools/runtime/accept_h11.py"
    )
    with monkeypatch.context() as old:
        old.setattr(db_module, "SCHEMA_VERSION", 14)
        with open_database(state) as db:
            conn = db.connection
            conn.execute(
                "INSERT INTO pending_work VALUES ('parse','snapshot','retained','2026-09-13T00:00:00+00:00')"
            )
            conn.execute(
                "INSERT INTO accepted_inputs VALUES ('pipeline','recipe/runtime','retained-identity')"
            )
            conn.execute("INSERT INTO host_budget VALUES ('web.archive.org','2026-09-13',200,123)")
            conn.execute(
                "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason) VALUES ('host','web.archive.org',NULL,'retained pause')"
            )
            conn.execute(
                "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project','calendar','existing','2026-09-13T00:00:00+00:00')"
            )
    candidate = state / "candidates" / "actual-h16-test-release"
    (candidate / "_meta").mkdir(parents=True)
    published = {
        "commit": "test-h16-public-commit",
        "verified_at": "2026-09-13T19:00:00+00:00",
        "candidate_id": candidate.name,
        "closure_digest": "test-closure",
        "evidence_cutoff": "2026-09-13T18:00:00+00:00",
    }
    (candidate / "PUBLISHED").write_text(json.dumps(published))
    (candidate / "BUILT").write_text("{}")
    (candidate / "_meta/manifest.json").write_text(
        json.dumps(
            {
                "files": {},
                "release_policy": {
                    "mode": "closure",
                    "closure": {"digest": "test-closure", "cutoff": "2026-09-13T18:00:00+00:00"},
                },
            }
        )
    )
    (state / "baseline").symlink_to(candidate)
    with open_database(state, lock=False, read_only=True) as db:
        create_checkpoint(
            state, db.connection, checkpoint, schema_version=14, versions={}, input_bundle_hash=None
        )
    manifest = json.loads((checkpoint / "checkpoint.json").read_bytes())
    assembly = {
        "format": "wp16-reviewed-source-v1",
        "schema": 15,
        "acquisition_enabled": False,
        "repairs_activated": False,
        "files": {
            p.relative_to(source).as_posix(): driver.prior.digest(p)
            for p in source.rglob("*")
            if p.is_file()
        },
    }
    (source / driver.SOURCE_RECEIPT).write_text(json.dumps(assembly))
    ops = tmp_path / "ops"
    ops.mkdir()
    backup = {
        "commit": "private-test-commit",
        "manifest_hash": driver.prior.digest(checkpoint / "checkpoint.json"),
        "checkpoint": str(checkpoint),
        "files": len(manifest["files"]),
        "schema_version": 14,
        "baseline_commit": published["commit"],
        "finished_at": "2026-09-13T19:01:00+00:00",
        "source_receipt_sha256": "actual-predecessor-test-hash",
    }
    private = {
        "verified": True,
        "private": True,
        "commit": backup["commit"],
        "manifest_sha256": backup["manifest_hash"],
        "verified_at": "2026-09-13T19:02:00+00:00",
    }
    public = {
        "verified": True,
        "commit": published["commit"],
        "manifest_sha256": driver.prior.digest(candidate / "_meta/manifest.json"),
        "verified_at": "2026-09-13T19:00:00+00:00",
    }
    for name, value in [("backup", backup), ("private", private), ("public", public)]:
        (ops / (name + ".json")).write_text(json.dumps(value))
    gate = {
        "format": "wp16-operational-gate-v1",
        "h16_release_verified": True,
        "verified_at": "2026-09-13T19:03:00+00:00",
        "driver_sha256": driver.prior.digest(Path(driver.__file__)),
        "source_receipt_sha256": driver.prior.digest(source / driver.SOURCE_RECEIPT),
        "predecessor_source_receipt_sha256": backup["source_receipt_sha256"],
        "checkpoint": str(checkpoint),
        "checkpoint_manifest_sha256": backup["manifest_hash"],
        "private_backup_commit": backup["commit"],
        "public_commit": public["commit"],
        "public_manifest_sha256": public["manifest_sha256"],
        "private_backup_receipt": "backup.json",
        "private_verification_receipt": "private.json",
        "public_verification_receipt": "public.json",
        "evidence_files": {
            name + ".json": driver.prior.digest(ops / (name + ".json"))
            for name in ("backup", "private", "public")
        },
    }
    gate_path = ops / "gate.json"
    gate_path.write_text(json.dumps(gate))
    monkeypatch.setattr(db_module, "__file__", str(source / "src/swingset/state/db.py"))
    monkeypatch.setattr(
        driver.prior, "__file__", str(source / "journal/tools/runtime/accept_h11.py")
    )
    monkeypatch.setattr(driver.prior, "system_hold", lambda _: {"test_fixture_hold": True})
    monkeypatch.setenv("PYTHONPATH", str(source / "src") + ":" + str(source))
    return SimpleNamespace(
        state=state,
        source=source,
        checkpoint=checkpoint,
        gate=gate_path,
        output=ops / "acceptance.json",
        expected=gate["source_receipt_sha256"],
    )


def run(f, execute=False):
    return driver.run(
        f.state, f.source, f.gate, f.output, expected_receipt=f.expected, execute=execute
    )


def test_default_preflight_keeps_schema_and_all_evidence_unchanged(operation):
    f = operation
    with open_database(f.state, lock=False, read_only=True) as db:
        before = driver.protected_state(db.connection)
    receipt = run(f)
    assert (
        receipt["preflight_passed"] and not receipt["executed"] and not receipt["service_executed"]
    )
    with open_database(f.state, lock=False, read_only=True) as db:
        assert driver.protected_state(db.connection) == before
    assert not (f.checkpoint / "state.sqlite-wal").exists()
    assert not (f.checkpoint / "state.sqlite-shm").exists()


def test_migration_only_preserves_authority_and_fresh_doctor_matches(operation):
    f = operation
    result = run(f, execute=True)
    assert result["passed"] and result["executed"]
    assert result["new_tables_empty"] == sorted(driver.NEW_TABLES)
    assert result["doctor"]["fresh_process_equal"]
    for key in (
        "network_requests",
        "published",
        "input_acceptance_executed",
        "service_executed",
        "activation_executed",
    ):
        assert not result[key]
    before, after = result["before"], result["after"]
    assert all(after["tables"][name] == value for name, value in before["tables"].items())
    assert after["schema"] == 15 and before["schema"] == 14
    assert after["schema_objects"] == before["schema_objects"] | driver.expected_additions(f.source)


@pytest.mark.parametrize(
    "suffix", [".sizes.json", ".intent.json", ".doctor.json", ".doctor.txt", ".json.tmp"]
)
def test_all_derived_output_symlinks_rejected_before_any_write(operation, suffix):
    f = operation
    protected = f.checkpoint / "witness"
    protected.write_text("original")
    target = f.output.with_suffix(suffix)
    target.symlink_to(protected)
    with pytest.raises(ValueError):
        run(f)
    assert protected.read_text() == "original"
    assert not f.output.exists()


@pytest.mark.parametrize(
    "kind", ["host_budget", "accepted_inputs", "identity_revision", "control", "schema"]
)
def test_preflight_detects_changed_live_authority(operation, kind):
    f = operation
    with closing(sqlite3.connect(f.state / "state.sqlite")) as conn:
        conn.execute(
            {
                "host_budget": "UPDATE host_budget SET requests=199",
                "accepted_inputs": "UPDATE accepted_inputs SET digest='changed'",
                "identity_revision": "UPDATE revisions SET value=value+1 WHERE name='identity_decisions'",
                "control": "UPDATE operator_pauses SET reason='changed'",
                "schema": "CREATE INDEX unreviewed_schema_object ON pending_work(stage)",
            }[kind]
        )
        conn.commit()
    with pytest.raises(ValueError, match="diverged"):
        run(f, execute=True)
    with closing(sqlite3.connect(f.state / "state.sqlite")) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14


@pytest.mark.parametrize(
    "receipt,field,value",
    [
        ("private", "private", False),
        ("public", "commit", "stale-public"),
        ("backup", "source_receipt_sha256", "stale-source"),
    ],
)
def test_mismatched_actual_receipts_cannot_authorize_migration(operation, receipt, field, value):
    f = operation
    path = f.gate.parent / (receipt + ".json")
    data = json.loads(path.read_bytes())
    data[field] = value
    path.write_text(json.dumps(data))
    gate = json.loads(f.gate.read_bytes())
    gate["evidence_files"][path.name] = driver.prior.digest(path)
    f.gate.write_text(json.dumps(gate))
    with pytest.raises(ValueError):
        run(f, execute=True)
    with closing(sqlite3.connect(f.state / "state.sqlite")) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14


def test_public_baseline_requires_completed_h16_closure_receipt(operation):
    f = operation
    path = f.state / "baseline/PUBLISHED"
    data = json.loads(path.read_bytes())
    data.pop("verified_at")
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="completed H16"):
        run(f)


def test_new_tables_must_remain_empty_after_migration(operation):
    f = operation
    with open_database(f.state, lock=False, read_only=True) as db:
        before = driver.protected_state(db.connection)
    with open_database(f.state) as db:
        driver.verify_migration(db.connection, before, f.source)
        db.connection.execute(
            "INSERT INTO history_origin_operator_refs VALUES ('eepro:test','unreviewed','2026-09-13')"
        )
        with pytest.raises(ValueError, match="fabricated origin"):
            driver.verify_migration(db.connection, before, f.source)


def test_exact_runtime_pin_and_source_closure_are_required(operation, monkeypatch):
    f = operation
    gate = json.loads(f.gate.read_bytes())
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 16)
    with pytest.raises(ValueError, match="frozen schema15"):
        driver.verify_runtime(f.source, gate, f.expected)
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 15)
    (f.source / "unreviewed.py").write_text("unexpected")
    with pytest.raises(ValueError, match="source closure"):
        driver.verify_runtime(f.source, gate, f.expected)


def test_missing_nested_checkpoint_manifest_is_not_excluded(operation):
    f = operation
    nested = f.checkpoint / "inputs" / "retained" / "checkpoint.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("{}")
    # An extra nested manifest is real retained content, unlike only the root
    # checkpoint manifest. It must never be skipped by a basename filter.
    with pytest.raises(ValueError, match="checkpoint closure"):
        run(f)


def test_schema_object_mutation_is_rejected_even_with_unchanged_rows(operation):
    f = operation
    with open_database(f.state, lock=False, read_only=True) as db:
        before = driver.protected_state(db.connection)
    with open_database(f.state) as db:
        db.connection.execute("DROP INDEX history_origin_requests_day")
        with pytest.raises(ValueError, match="schema objects"):
            driver.verify_migration(db.connection, before, f.source)


def failed_after_commit(f, monkeypatch):
    with monkeypatch.context() as failure:

        def fail_doctor(*_):
            raise TimeoutError("offline interruption after migration commit")

        failure.setattr(driver, "doctor_check", fail_doctor)
        with pytest.raises(TimeoutError):
            driver.run(
                f.state, f.source, f.gate, f.output, expected_receipt=f.expected, execute=True
            )
    intent = f.output.with_suffix(".intent.json")
    assert intent.is_file()
    with driver.db_module.open_database(f.state, lock=False, read_only=True) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 15
    return intent


def test_committed_migration_resumes_from_immutable_intent_without_writer(operation, monkeypatch):
    f = operation
    intent = failed_after_commit(f, monkeypatch)
    original = intent.read_bytes()
    f.output.unlink()  # A killed process need not have updated its mutable result.
    opener = driver.db_module.open_database
    readonly = []

    @contextmanager
    def only_reads(*args, **kwargs):
        assert kwargs.get("read_only") is True, "resume must never run migration opener"
        readonly.append(True)
        with opener(*args, **kwargs) as db:
            yield db

    with monkeypatch.context() as monitor:
        monitor.setattr(driver.db_module, "open_database", only_reads)
        receipt = driver.run(
            f.state,
            f.source,
            f.gate,
            f.output.with_name("resumed.json"),
            expected_receipt=f.expected,
            execute=True,
            resume=intent,
            resume_sha256=driver.prior.digest(intent),
        )
    assert receipt["passed"]
    assert readonly and intent.read_bytes() == original
    assert receipt["input_acceptance_executed"] is False
    assert receipt["service_executed"] is False
    assert receipt["network_requests"] == 0


@pytest.mark.parametrize("change", ["accepted_inputs", "new_evidence"])
def test_resume_cannot_bless_changes_after_interrupted_migration(operation, monkeypatch, change):
    f = operation
    intent = failed_after_commit(f, monkeypatch)
    with driver.db_module.open_database(f.state) as db:
        if change == "accepted_inputs":
            db.connection.execute("UPDATE accepted_inputs SET digest='not-original-authority'")
        else:
            db.connection.execute(
                "INSERT INTO history_origin_operator_refs VALUES ('eepro:unexpected','unreviewed','2026-09-13')"
            )
    with pytest.raises(ValueError):
        driver.run(
            f.state,
            f.source,
            f.gate,
            f.output.with_name("rejected-resume.json"),
            expected_receipt=f.expected,
            execute=True,
            resume=intent,
            resume_sha256=driver.prior.digest(intent),
        )


@pytest.mark.parametrize("name", ["_meta/manifest.json", "PUBLISHED"])
def test_checkpoint_same_candidate_name_different_baseline_bytes_rejected(operation, name):
    f = operation
    checkpoint_path = f.checkpoint / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_bytes())
    candidate = checkpoint["baseline_candidate"]
    relative = f"candidates/{candidate}/{name}"
    path = f.checkpoint / relative
    value = json.loads(path.read_bytes())
    if name == "PUBLISHED":
        value["commit"] = "different-retained-publication"
    else:
        value["release_policy"]["closure"]["digest"] = "different-retained-manifest"
    path.write_text(json.dumps(value))
    checkpoint["files"][relative] = {
        "size": path.stat().st_size,
        "sha256": driver.prior.digest(path),
    }
    checkpoint_path.write_text(json.dumps(checkpoint))
    checkpoint_hash = driver.prior.digest(checkpoint_path)
    gate = json.loads(f.gate.read_bytes())
    gate["checkpoint_manifest_sha256"] = checkpoint_hash
    for receipt_key, hash_field in [
        ("private_backup_receipt", "manifest_hash"),
        ("private_verification_receipt", "manifest_sha256"),
    ]:
        evidence_path = f.gate.parent / gate[receipt_key]
        evidence = json.loads(evidence_path.read_bytes())
        evidence[hash_field] = checkpoint_hash
        evidence_path.write_text(json.dumps(evidence))
        gate["evidence_files"][evidence_path.name] = driver.prior.digest(evidence_path)
    f.gate.write_text(json.dumps(gate))
    with pytest.raises(ValueError, match="baseline|public|manifest|checkpoint"):
        driver.run(f.state, f.source, f.gate, f.output, expected_receipt=f.expected)


@pytest.mark.parametrize("change", ["hash", "source", "preimage", "schema14", "missing_execute"])
def test_resume_intent_cannot_select_unreviewed_authority(operation, monkeypatch, change):
    f = operation
    intent = failed_after_commit(f, monkeypatch)
    expected = driver.prior.digest(intent)
    if change in {"source", "preimage"}:
        data = json.loads(intent.read_bytes())
        if change == "source":
            data["source"] = "another-pin"
        else:
            data["before"]["tables"]["accepted_inputs"]["sha256"] = "fabricated-preimage"
        intent.write_text(json.dumps(data))
        expected = driver.prior.digest(intent)
    elif change == "hash":
        expected = "wrong-hash"
    elif change == "schema14":
        with driver.db_module.open_database(f.state) as db:
            db.connection.execute("PRAGMA user_version=14")
    with pytest.raises(ValueError):
        driver.run(
            f.state,
            f.source,
            f.gate,
            f.output.with_name("rejected.json"),
            expected_receipt=f.expected,
            execute=change != "missing_execute",
            resume=intent,
            resume_sha256=expected,
        )
