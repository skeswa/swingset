import json
import shutil

import pytest
from test_recovery import FakeHub

from swingset.build.service import build_release
from swingset.clock import FakeClock
from swingset.publish.service import PublishError, publish, reconcile
from swingset.state import engine
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture
from swingset.versions import versions


@pytest.fixture
def release(tmp_path, monkeypatch):
    monkeypatch.setenv("SWINGSET_REVISION", "publication-safety-fixture")
    config, overrides = tmp_path / "config", tmp_path / "overrides"
    shutil.copytree("config", config)
    overrides.mkdir()
    (overrides / "identity_overrides.csv").write_text(
        "decision_id,source,source_event,contest,round,participant,wsdc_id,decision,evidence,reason,author,date,supersedes\n"
    )
    (overrides / "suppressions.csv").write_text("wsdc_id,name_norm,reason,date\n")
    clock, hub = FakeClock(), FakeHub()
    with open_database(tmp_path / "state") as database:
        bundle = capture(config, overrides, database.state_dir, versions())
        accept(database, bundle, clock)
        database.connection.execute("DELETE FROM pending_work")
        run_id = database.start_run(clock.now())
        candidate = build_release(database, bundle, clock, run_id, hub)
        yield database, config, overrides, clock, hub, candidate


def changed_journal(database, config, overrides, clock):
    path = overrides / "identity_overrides.csv"
    path.write_text(path.read_text() + "\n")
    bundle = capture(config, overrides, database.state_dir, versions())
    accept(database, bundle, clock)
    return bundle


@pytest.mark.parametrize(
    "change",
    ["journal", "suppression", "invalid_input", "policy", "extra_file", "changed_file"],
)
def test_changed_candidate_inputs_never_reach_remote_commit(release, change):
    database, config, overrides, clock, hub, candidate = release
    if change == "journal":
        changed_journal(database, config, overrides, clock)
    elif change == "suppression":
        (overrides / "suppressions.csv").write_text(
            "wsdc_id,name_norm,reason,date\n42,,Private operator note,2026-01-01\n"
        )
    elif change == "policy":
        database.connection.execute(
            "INSERT INTO admission_policies (page_kind, contract_version, mode, policy_revision) VALUES "
            "('fixture', '1', 'enforce', 'changed')"
        )
    elif change == "invalid_input":
        (overrides / "identity_overrides.csv").write_text("invalid journal")
    elif change == "pending":
        database.connection.execute(
            "INSERT INTO pending_work (stage, unit_kind, unit_id, enqueued_at) VALUES ('parse', "
            "'snapshot', 'unrelated', '2026-01-01')"
        )
    elif change == "extra_file":
        (candidate.path / "data/unlisted.parquet").write_bytes(b"unreviewed")
    else:
        (candidate.path / "README.md").write_bytes(b"changed")
    with pytest.raises(PublishError):
        publish(database.state_dir, candidate.path, hub, connection=database.connection)
    assert hub.calls == 0
    assert not (candidate.path / "PUBLISHING").exists()
    assert (candidate.path / "REJECTED").is_file()


def test_publication_reserves_journal_acceptance_until_commit_returns(release):
    database, _config, _overrides, _clock, hub, candidate = release
    original = hub.create_commit
    checked = []

    def observing_commit(**kwargs):
        with engine.connect(engine.resolve_dsn(database.state_dir), timeout=0) as other:
            other.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
            from swingset.state.engine.locks import acquire_writer

            acquire_writer(other)
            with pytest.raises(engine.IntegrityError, match="publication"):
                other.execute("UPDATE meta SET value = 'unsafe' WHERE key = 'input_bundle_hash'")
            other.rollback()
        checked.append(True)
        return original(**kwargs)

    hub.create_commit = observing_commit
    result = publish(database.state_dir, candidate.path, hub, connection=database.connection)
    assert checked == [True] and result.state == "published"
    with engine.connect(engine.resolve_dsn(database.state_dir), timeout=0) as other:
        other.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
        from swingset.state.engine.locks import acquire_writer

        acquire_writer(other)
    receipt = json.loads((candidate.path / "PUBLISHED").read_text())
    assert receipt["correction_latency_seconds"] is not None
    assert not receipt["latency_is_upper_bound"]


def test_lost_response_recovers_landed_commit_after_new_decision(release):
    database, config, overrides, clock, hub, candidate = release
    hub.lose_response = True
    with pytest.raises(ConnectionError):
        publish(database.state_dir, candidate.path, hub, connection=database.connection)
    changed_journal(database, config, overrides, clock)
    result = reconcile(database.state_dir, hub, dry_run=False, connection=database.connection)
    assert result.state == "recovered" and hub.calls == 1
    assert (database.state_dir / "baseline").resolve() == candidate.path
    assert json.loads((candidate.path / "PUBLISHED").read_text())["latency_is_upper_bound"]


def test_unlanded_old_intent_is_rejected_then_new_build_can_publish(release):
    database, config, overrides, clock, hub, candidate = release
    original = hub.create_commit

    def fail_before_commit(**_kwargs):
        raise ConnectionError("request did not land")

    hub.create_commit = fail_before_commit
    with pytest.raises(ConnectionError):
        publish(database.state_dir, candidate.path, hub, connection=database.connection)
    bundle = changed_journal(database, config, overrides, clock)
    hub.create_commit = original
    with pytest.raises(PublishError, match="changed"):
        reconcile(database.state_dir, hub, dry_run=False, connection=database.connection)
    assert not (candidate.path / "PUBLISHING").exists()
    rebuilt = build_release(database, bundle, clock, database.start_run(clock.now()), hub)
    assert rebuilt.path != candidate.path
    assert (
        publish(database.state_dir, rebuilt.path, hub, connection=database.connection).state
        == "published"
    )
    assert hub.calls == 1


def test_restore_hold_keeps_unlanded_intent_retryable(release):
    database, _config, _overrides, _clock, hub, candidate = release
    original = hub.create_commit

    def fail_before_commit(**_kwargs):
        raise ConnectionError("request did not land")

    hub.create_commit = fail_before_commit
    with pytest.raises(ConnectionError):
        publish(database.state_dir, candidate.path, hub, connection=database.connection)
    hub.create_commit = original
    (database.state_dir / "RESTORE_PENDING").write_text("verify first")
    with pytest.raises(PublishError, match="restore verification"):
        reconcile(database.state_dir, hub, dry_run=False, connection=database.connection)
    assert hub.calls == 0 and (candidate.path / "PUBLISHING").exists()
    assert not (candidate.path / "REJECTED").exists()
    (database.state_dir / "RESTORE_PENDING").unlink()
    assert (
        reconcile(database.state_dir, hub, dry_run=False, connection=database.connection).state
        == "published"
    )


def test_pending_retry_rechecks_local_baseline(release):
    database, _config, _overrides, _clock, hub, candidate = release
    original = hub.create_commit

    def fail_before_commit(**_kwargs):
        raise ConnectionError("request did not land")

    hub.create_commit = fail_before_commit
    with pytest.raises(ConnectionError):
        publish(database.state_dir, candidate.path, hub, connection=database.connection)
    hub.create_commit = original
    elsewhere = database.state_dir / "other-baseline"
    elsewhere.mkdir()
    (elsewhere / "PUBLISHED").write_text('{"commit":"different"}')
    (database.state_dir / "baseline").symlink_to(elsewhere)
    with pytest.raises(PublishError, match="baseline changed"):
        reconcile(database.state_dir, hub, dry_run=False, connection=database.connection)
    assert hub.calls == 0


@pytest.mark.parametrize("change", ["revocation", "manifest"])
def test_a_partitioned_candidate_rechecks_its_manifest_and_revocations(release, change):
    """A partitioned candidate is checked without a graph walk (D-0224): its
    release manifest must still hash to its digest, and no source generation
    may have been revoked since the build."""
    database, _config, _overrides, clock, hub, candidate = release
    policy = json.loads((candidate.path / "_meta/manifest.json").read_bytes())["release_policy"]
    assert policy["mode"] == "partitioned"
    if change == "revocation":
        conn = database.connection
        run_id = database.start_run(clock.now())
        conn.execute(
            "INSERT INTO watches (watch_id, source, kind, method, url, parser, state) VALUES ('w1', "
            "'wsdc', 'dancer', 'GET', 'https://example.test/', 'wsdc_dancer', 'active')"
        )
        conn.execute(
            "INSERT INTO source_units (unit_key, watch_id, page_kind) VALUES ('unit-w1', 'w1', 'dancer')"
        )
        conn.execute(
            (
                "INSERT INTO source_generations (generation_id, unit_key, page_kind, contract_version, "
                "input_fingerprint, manifest_json, recipe_json, result_json, report_json, created_at, "
                "run_id, state, removal_authority) VALUES ('sg-1', 'unit-w1', 'dancer', 'v1', 'fp', '[]', "
                "'{}', '{}', '{}', '2026-01-01T00:00:00+00:00', %s, 'revoked', 'none')"
            ),
            (run_id,),
        )
        reason = "revoked after build"
    else:
        from swingset.state.retention import manifest_file

        manifest_file(database.state_dir, policy["manifest_digest"]).write_bytes(b"{}")
        reason = "release manifest or artifact changed"
    with pytest.raises(PublishError, match=reason):
        publish(database.state_dir, candidate.path, hub, connection=database.connection)
    assert hub.calls == 0 and (candidate.path / "REJECTED").is_file()


def test_backup_is_verified_and_recorded_before_push_with_control_and_writer_free(release):
    database, _config, _overrides, _clock, hub, candidate = release
    original = hub.create_commit

    def checked(**kwargs):
        from swingset.state.control_lock import control_lock
        from swingset.state.engine.locks import acquire_writer

        intent = json.loads((candidate.path / "PUBLISHING").read_text())
        backup = intent["backup"]
        row = database.connection.execute(
            "SELECT manifest_sha256 FROM backup_sets WHERE set_id=%s", (backup["set_id"],)
        ).fetchone()
        assert row[0] == backup["manifest_sha256"]
        assert not database.connection.in_transaction
        with engine.connect(engine.resolve_dsn(database.state_dir), timeout=0) as other:
            with control_lock(database.state_dir, connection=other, timeout=0):
                other.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
                acquire_writer(other)
                other.rollback()
        return original(**kwargs)

    hub.create_commit = checked
    assert (
        publish(database.state_dir, candidate.path, hub, connection=database.connection).state
        == "published"
    )
    assert hub.calls == 1


def test_busy_backup_retains_uncertain_intent_without_push_and_retry_succeeds(release):
    from swingset.backup.snapshot import backup_lock

    database, _config, _overrides, _clock, hub, candidate = release
    with engine.connect(engine.resolve_dsn(database.state_dir)) as other:
        with backup_lock(other):
            result = publish(
                database.state_dir, candidate.path, hub, connection=database.connection
            )
    assert result.state == "held" and hub.calls == 0
    intent = json.loads((candidate.path / "PUBLISHING").read_text())
    assert "backup" not in intent
    assert (
        database.connection.execute(
            "SELECT state FROM execution_admissions WHERE action_id=%s", (intent["action_id"],)
        ).fetchone()[0]
        == "uncertain"
    )
    assert "[publication-backup-failed]" in (database.state_dir / "ALERTS.log").read_text()
    assert (
        reconcile(database.state_dir, hub, dry_run=False, connection=database.connection).state
        == "published"
    )
    assert hub.calls == 1


def test_failed_backup_restore_check_keeps_prior_set_and_never_pushes(release, monkeypatch):
    from swingset.backup import restore_set
    from swingset.backup.segments import LOCAL_TRANSPORT, create_set
    from swingset.backup.transport import LocalDirectoryTransport

    database, _config, _overrides, clock, hub, candidate = release
    transport = LocalDirectoryTransport(database.state_dir / "checkpoints", name=LOCAL_TRANSPORT)
    old = create_set(database, transport, now=clock.now(), versions={}, headroom=0)
    original = restore_set.verify_set

    def reject(*args, **kwargs):
        raise ValueError("restore check failed")

    monkeypatch.setattr(restore_set, "verify_set", reject)
    with pytest.raises(ValueError, match="restore check failed"):
        publish(database.state_dir, candidate.path, hub, connection=database.connection)
    assert hub.calls == 0 and (candidate.path / "PUBLISHING").is_file()
    assert [
        tuple(row)
        for row in database.connection.execute("SELECT set_id FROM backup_sets ORDER BY set_id")
    ] == [(old["set_id"],)]
    assert (database.state_dir / "checkpoints/sets" / old["set_id"]).is_dir()
    monkeypatch.setattr(restore_set, "verify_set", original)
    assert (
        reconcile(database.state_dir, hub, dry_run=False, connection=database.connection).state
        == "published"
    )


def test_killed_backup_cleans_unrecorded_set_before_retry_without_reusing_key(release):
    import os
    import subprocess
    import sys
    from pathlib import Path

    database, _config, _overrides, _clock, hub, candidate = release
    script = """
import os, signal, sys
from pathlib import Path
from swingset.backup.transport import LocalDirectoryTransport
from swingset.publish.service import publish
from swingset.state import engine
from test_recovery import FakeHub

def killed(self, local, set_id, segments):
    os.kill(os.getpid(), signal.SIGKILL)
LocalDirectoryTransport.publish = killed
state, candidate = map(Path, sys.argv[1:])
with engine.connect(engine.resolve_dsn(state)) as connection:
    connection.state_dir = state
    publish(state, candidate, FakeHub(), connection=connection)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(Path("src").resolve()), str(Path("tests/publish").resolve())]
    )
    killed = subprocess.run(
        [sys.executable, "-c", script, str(database.state_dir), str(candidate.path)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert killed.returncode == -9, killed.stderr
    sets = database.state_dir / "checkpoints/sets"
    orphan = list(sets.glob(".pending-*.json"))
    assert len(orphan) == 1
    record = json.loads(orphan[0].read_text())
    assert (sets / record["set_id"]).is_dir()
    assert database.connection.execute("SELECT count(*) FROM backup_sets").fetchone()[0] == 0
    assert hub.calls == 0 and (candidate.path / "PUBLISHING").is_file()
    # Startup recovers only the admission whose process died; no remote request happened.
    from datetime import UTC, datetime

    from swingset.state.controls import recover_admissions

    with database.transaction() as conn:
        recover_admissions(conn, now=datetime.now(UTC))
    assert (
        reconcile(database.state_dir, hub, dry_run=False, connection=database.connection).state
        == "published"
    )
    assert not orphan[0].exists() and not (sets / record["set_id"]).exists()
    intent_receipt = json.loads((candidate.path / "PUBLISHED").read_text())
    assert intent_receipt["commit"] == "commit-1" and hub.calls == 1
    assert (
        database.connection.execute("SELECT set_id FROM backup_sets").fetchone()[0]
        != record["set_id"]
    )


def test_killed_after_verified_backup_keeps_receipt_and_reconciles_before_retry(release):
    import os
    import subprocess
    import sys
    from datetime import UTC, datetime
    from pathlib import Path

    from swingset.state.controls import recover_admissions

    database, _config, _overrides, _clock, hub, candidate = release
    script = """
import os, signal, sys
from pathlib import Path
from swingset.publish.service import publish
from swingset.state import engine
from test_recovery import FakeHub
class KilledHub(FakeHub):
    def create_commit(self, **kwargs):
        os.kill(os.getpid(), signal.SIGKILL)
state,candidate = map(Path, sys.argv[1:])
with engine.connect(engine.resolve_dsn(state)) as connection:
    connection.state_dir = state
    publish(state,candidate,KilledHub(),connection=connection)
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(Path("src").resolve()), str(Path("tests/publish").resolve())]
    )
    child = subprocess.run(
        [sys.executable, "-c", script, str(database.state_dir), str(candidate.path)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert child.returncode == -9, child.stderr
    intent = json.loads((candidate.path / "PUBLISHING").read_text())
    backup = intent["backup"]
    assert (
        database.connection.execute(
            "SELECT manifest_sha256 FROM backup_sets WHERE set_id=%s", (backup["set_id"],)
        ).fetchone()[0]
        == backup["manifest_sha256"]
    )
    assert hub.calls == 0 and not (candidate.path / "PUBLISHED").exists()
    with database.transaction() as conn:
        recover_admissions(conn, now=datetime.now(UTC))
    assert (
        reconcile(database.state_dir, hub, dry_run=False, connection=database.connection).state
        == "published"
    )
    assert hub.calls == 1
    rows = [str(row[0]) for row in database.connection.execute("SELECT set_id FROM backup_sets")]
    assert len(rows) == 2 and backup["set_id"] in rows
