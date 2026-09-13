import json
import shutil
import sqlite3

import pytest
from test_recovery import FakeHub

from swingset.build.service import build_release
from swingset.clock import FakeClock
from swingset.publish.service import PublishError, publish, reconcile
from swingset.schedule.cycle import versions
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture


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
            "INSERT INTO admission_policies(page_kind,contract_version,mode,policy_revision) VALUES ('fixture','1','enforce','changed')"
        )
    elif change == "invalid_input":
        (overrides / "identity_overrides.csv").write_text("invalid journal")
    elif change == "pending":
        database.connection.execute(
            "INSERT INTO pending_work VALUES ('parse','snapshot','unrelated','2026-01-01')"
        )
    elif change == "extra_file":
        (candidate.path / "data/unlisted.parquet").write_bytes(b"unreviewed")
    else:
        (candidate.path / "README.md").write_bytes(b"changed")
    with pytest.raises(PublishError):
        publish(database.state_dir, candidate.path, hub)
    assert hub.calls == 0
    assert not (candidate.path / "PUBLISHING").exists()
    assert (candidate.path / "REJECTED").is_file()


def test_publication_reserves_journal_acceptance_until_commit_returns(release):
    database, _config, _overrides, _clock, hub, candidate = release
    original = hub.create_commit
    checked = []

    def observing_commit(**kwargs):
        with sqlite3.connect(database.state_dir / "state.sqlite", timeout=0) as other:
            other.execute("BEGIN IMMEDIATE")
            with pytest.raises(sqlite3.IntegrityError, match="publication"):
                other.execute("UPDATE meta SET value='unsafe' WHERE key='input_bundle_hash'")
        checked.append(True)
        return original(**kwargs)

    hub.create_commit = observing_commit
    result = publish(database.state_dir, candidate.path, hub)
    assert checked == [True] and result.state == "published"
    with sqlite3.connect(database.state_dir / "state.sqlite", timeout=0) as other:
        other.execute("BEGIN IMMEDIATE")
    receipt = json.loads((candidate.path / "PUBLISHED").read_text())
    assert receipt["correction_latency_seconds"] is not None
    assert not receipt["latency_is_upper_bound"]


def test_lost_response_recovers_landed_commit_after_new_decision(release):
    database, config, overrides, clock, hub, candidate = release
    hub.lose_response = True
    with pytest.raises(ConnectionError):
        publish(database.state_dir, candidate.path, hub)
    changed_journal(database, config, overrides, clock)
    result = reconcile(database.state_dir, hub, dry_run=False)
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
        publish(database.state_dir, candidate.path, hub)
    bundle = changed_journal(database, config, overrides, clock)
    hub.create_commit = original
    with pytest.raises(PublishError, match="changed"):
        reconcile(database.state_dir, hub, dry_run=False)
    assert not (candidate.path / "PUBLISHING").exists()
    rebuilt = build_release(database, bundle, clock, database.start_run(clock.now()), hub)
    assert rebuilt.path != candidate.path
    assert publish(database.state_dir, rebuilt.path, hub).state == "published"
    assert hub.calls == 1


def test_restore_hold_keeps_unlanded_intent_retryable(release):
    database, _config, _overrides, _clock, hub, candidate = release
    original = hub.create_commit

    def fail_before_commit(**_kwargs):
        raise ConnectionError("request did not land")

    hub.create_commit = fail_before_commit
    with pytest.raises(ConnectionError):
        publish(database.state_dir, candidate.path, hub)
    hub.create_commit = original
    (database.state_dir / "RESTORE_PENDING").write_text("verify first")
    with pytest.raises(PublishError, match="restore verification"):
        reconcile(database.state_dir, hub, dry_run=False)
    assert hub.calls == 0 and (candidate.path / "PUBLISHING").exists()
    assert not (candidate.path / "REJECTED").exists()
    (database.state_dir / "RESTORE_PENDING").unlink()
    assert reconcile(database.state_dir, hub, dry_run=False).state == "published"


def test_pending_retry_rechecks_local_baseline(release):
    database, _config, _overrides, _clock, hub, candidate = release
    original = hub.create_commit

    def fail_before_commit(**_kwargs):
        raise ConnectionError("request did not land")

    hub.create_commit = fail_before_commit
    with pytest.raises(ConnectionError):
        publish(database.state_dir, candidate.path, hub)
    hub.create_commit = original
    elsewhere = database.state_dir / "other-baseline"
    elsewhere.mkdir()
    (elsewhere / "PUBLISHED").write_text('{"commit":"different"}')
    (database.state_dir / "baseline").symlink_to(elsewhere)
    with pytest.raises(PublishError, match="baseline changed"):
        reconcile(database.state_dir, hub, dry_run=False)
    assert hub.calls == 0
