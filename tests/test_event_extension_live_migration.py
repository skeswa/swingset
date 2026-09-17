"""The live helper rejects failed upstream gates before any migration authority."""

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from journal.tools.runtime import accept_event_extension as helper
from journal.tools.runtime import rehearse_extension_migration as verifier


@pytest.fixture
def gate_receipts():
    before = {
        f"retained_{i}": {"sha256": "a" * 64, "rows": i, "columns": ["id"]} for i in range(71)
    }
    source = dict(source=str(helper.SOURCE), source_receipt_sha256=helper.SOURCE_RECEIPT)
    checkpoint = dict(path="/var/lib/swingset/checkpoints/offline", manifest_sha256="b" * 64)
    closed = dict(passed=True, finished_at="2026-09-17T12:00:00+00:00")
    evidence = {name + "_log": {"sha256": "c" * 64} for name in ("pytest", "ruff", "mypy")}
    gate = dict(checkpoint=checkpoint, evidence=evidence)
    rehearsal = dict(
        **source,
        **closed,
        checkpoint=checkpoint["path"],
        checkpoint_sha256=checkpoint["manifest_sha256"],
        target_schema=28,
        schema=28,
        changed_existing_tables=[],
        before=before,
        after=copy.deepcopy(before),
    )
    receipts = dict(
        backup=dict(
            **closed,
            format="h16-post-publication-checkpoint-v1",
            source="/nix/store/z689qy41inndill3d92ym8im852x3649-source",
            system=helper.OLD_SYSTEM,
            baseline=helper.BASELINE,
            live_database_changes=0,
            private_archive_commit="d" * 40,
            checkpoint=checkpoint,
        ),
        migration=dict(
            **rehearsal,
            format="event-extension-migration-rehearsal-v1",
            old_schema=14,
            integrity=[["ok"]],
            foreign_key_failure=False,
            scope="migration_only_without_runtime_input_acceptance",
        ),
        restore=dict(
            **rehearsal,
            format="extension-operational-restore-rehearsal-v1",
            stage="finished_held_without_input_acceptance",
            schema14_activated_under_hold=True,
            baseline=helper.BASELINE,
            restore_heads=[helper.BASELINE, helper.BASELINE],
            remote={"commit": helper.BASELINE},
            acknowledged_archive_commit="d" * 40,
            operator_hold_present=True,
            restore_pending=False,
            input_acceptance=False,
            workers_started=False,
            repairs_activated=False,
            source_requests=0,
            public_writes=0,
        ),
        validation=dict(
            **source,
            **closed,
            format="event-extension-validation-v1",
            schema=28,
            source_verified_before=True,
            source_verified_after=True,
            pytest=dict(
                exit_code=0,
                log_sha256="c" * 64,
                full_suite=True,
                passed=2000,
                failed=0,
                command="pytest -q",
            ),
            ruff=dict(exit_code=0, log_sha256="c" * 64),
            mypy=dict(exit_code=0, log_sha256="c" * 64, source_files=217),
        ),
        service_binding=dict(
            **source,
            **closed,
            format="event-extension-service-binding-v1",
            system=helper.SYSTEM,
            external_overrides_match=True,
            hold_conditions=True,
            backup_disk_tmp=True,
            package="/nix/store/offline-package",
        ),
    )
    return gate, receipts


def test_actual_passed_results_are_required_not_just_receipt_hashes(gate_receipts):
    gate, receipts = gate_receipts
    helper.validate_receipts(receipts, gate)
    for name in receipts:
        bad = copy.deepcopy(receipts)
        bad[name]["passed"] = False
        with pytest.raises(ValueError, match="did not pass"):
            helper.validate_receipts(bad, gate)


@pytest.mark.parametrize(
    "name,key,value",
    [
        ("migration", "target_schema", 27),
        ("restore", "schema", 27),
        ("migration", "checkpoint_sha256", "e" * 64),
        ("migration", "changed_existing_tables", ["host_budget"]),
        ("restore", "workers_started", True),
        ("restore", "schema14_activated_under_hold", False),
        ("restore", "restore_pending", True),
        ("restore", "restore_heads", [helper.BASELINE]),
        ("restore", "acknowledged_archive_commit", "e" * 40),
        ("validation", "source_verified_after", False),
        ("validation", "finished_at", ""),
        ("service_binding", "system", helper.OLD_SYSTEM),
        ("service_binding", "external_overrides_match", False),
        ("backup", "live_database_changes", 1),
        ("backup", "source", str(helper.SOURCE)),
    ],
)
def test_wrong_stage_source_restore_or_preservation_blocks_migration(
    gate_receipts, name, key, value
):
    gate, receipts = gate_receipts
    receipts[name][key] = value
    with pytest.raises(ValueError):
        helper.validate_receipts(receipts, gate)


@pytest.mark.parametrize(
    "check,key,value",
    [
        ("pytest", "failed", 1),
        ("pytest", "full_suite", False),
        ("pytest", "passed", 0),
        ("ruff", "exit_code", 1),
        ("mypy", "log_sha256", "e" * 64),
    ],
)
def test_validation_must_cover_a_successful_full_run_and_exact_logs(
    gate_receipts, check, key, value
):
    gate, receipts = gate_receipts
    receipts["validation"][check][key] = value
    with pytest.raises(ValueError):
        helper.validate_receipts(receipts, gate)


def test_pinned_file_substitution_and_parent_traversal_are_rejected(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"passed":false}')
    digest = helper.sha(receipt)
    assert helper.reference(tmp_path, {"path": receipt.name, "sha256": digest}) == receipt
    receipt.write_text('{"passed":true}')
    with pytest.raises(ValueError, match="differs"):
        helper.reference(tmp_path, {"path": receipt.name, "sha256": digest})
    with pytest.raises(ValueError, match="traverses"):
        helper.reference(tmp_path, {"path": "../receipt.json", "sha256": digest})


def test_execute_is_rejected_before_prerequisites_when_system_phase_is_old(tmp_path):
    gate = tmp_path / "gate.json"
    gate.write_text(
        json.dumps(
            dict(
                format="event-extension-live-migration-gate-v1",
                state=str(helper.STATE),
                source=str(helper.SOURCE),
                helper_sha256=helper.sha(Path(helper.__file__)),
                phase="before_deployment",
            )
        )
    )
    with pytest.raises(ValueError, match="already deployed"):
        helper.run(gate, gate_sha256=helper.sha(gate), execute=True)


def test_locked_migration_preserves_all_actual_schema14_tables(tmp_path, monkeypatch):
    from swingset.state import db as db_module
    from swingset.state.control_lock import control_lock

    state, rehearsal = tmp_path / "state", tmp_path / "rehearsal"
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 14)
    with db_module.open_database(state) as db:
        db.connection.execute("INSERT INTO hosts(host) VALUES ('example.test')")
        db.connection.execute("INSERT INTO host_budget VALUES ('example.test','2026-09-17',9,99)")
        before = helper.protected(db.connection, verifier, 14)
        assert len(before) == 71
        rehearsal.mkdir()
        with sqlite3.connect(rehearsal / "state.sqlite") as target:
            db.connection.backup(target)
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 28)
    with db_module.open_database(rehearsal) as db:
        expected_after = helper.protected(db.connection, verifier, 28)
    with db_module.open_database(state, read_only=True) as writer:
        with control_lock(state):
            after = helper.migrate_locked(
                state, verifier, db_module, before, expected_after, verify_inputs=lambda: None
            )
        assert verifier.compare(before, after) == []
        assert writer.connection.execute("PRAGMA user_version").fetchone()[0] == 28
        assert tuple(
            writer.connection.execute("SELECT requests,bytes FROM host_budget").fetchone()
        ) == (9, 99)
        assert (
            writer.connection.execute("SELECT gap_seconds FROM host_request_spacing").fetchone()[0]
            is None
        )


def test_locked_prestate_change_stops_before_any_schema_write(tmp_path, monkeypatch):
    from swingset.state import db as db_module
    from swingset.state.control_lock import control_lock

    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 14)
    with db_module.open_database(tmp_path) as db:
        before = helper.protected(db.connection, verifier, 14)
        db.connection.execute("INSERT INTO hosts(host) VALUES ('changed.test')")
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 28)
    with db_module.open_database(tmp_path, read_only=True) as db:
        with control_lock(tmp_path):
            with pytest.raises(ValueError, match="prestate changed"):
                helper.migrate_locked(
                    tmp_path, verifier, db_module, before, {}, verify_inputs=lambda: None
                )
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 14


def test_external_change_during_long_prestate_read_stops_before_migration(tmp_path, monkeypatch):
    from swingset.state import db as db_module
    from swingset.state.control_lock import control_lock

    state = tmp_path / "state"
    external = tmp_path / "external-config"
    external.write_text("reviewed")
    spec = {"path": str(external), "sha256": helper.sha(external)}
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 14)
    with db_module.open_database(state) as db:
        before = helper.protected(db.connection, verifier, 14)
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 28)
    original = helper.protected

    def changed_during_read(*args):
        result = original(*args)
        external.write_text("changed while hashing")
        return result

    monkeypatch.setattr(helper, "protected", changed_during_read)
    with db_module.open_database(state, read_only=True) as db:
        with control_lock(state):
            with pytest.raises(ValueError, match="pinned file differs"):
                helper.migrate_locked(
                    state,
                    verifier,
                    db_module,
                    before,
                    {},
                    verify_inputs=lambda: helper.reference(tmp_path, spec),
                )
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 14
