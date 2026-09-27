"""Publication holds persist during remote I/O without weakening H10 sealing."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from test_safety import changed_journal
from test_safety import release as release

from swingset.publish.service import PublishError, publish, reconcile
from swingset.state.controls import Selector, change_control, recover_admissions, status


def control(database, selector, *, paused=True):
    return change_control(
        database.state_dir,
        selector=selector,
        paused=paused,
        actor="offline-test",
        reason="private operator note",
        now=datetime.now(UTC),
        timeout=1,
        hosts=("eepro.com",),
    )


def current_status(database):
    return status(database.connection, now=datetime.now(UTC))


@pytest.mark.parametrize(
    "selector",
    [Selector("all", "all"), Selector("source", "eepro"), Selector("kind", "parse_failure")],
)
def test_automatic_commit_held_without_rejecting_valid_candidate(release, selector):
    database, _, _, _, hub, candidate = release
    control(database, selector)
    result = publish(database.state_dir, candidate.path, hub)
    assert result.state == "held" and hub.calls == 0
    assert "private operator note" not in result.reason
    assert (candidate.path / "PUBLISHING").exists()
    assert not (candidate.path / "REJECTED").exists()
    assert (
        database.connection.execute("SELECT COUNT(*) FROM execution_admissions").fetchone()[0] == 0
    )
    control(database, selector, paused=False)
    assert reconcile(database.state_dir, hub, dry_run=False).state == "published"
    assert hub.calls == 1


def test_host_pause_allows_postfetch_publication(release):
    database, _, _, _, hub, candidate = release
    control(database, Selector("host", "eepro.com"))
    assert publish(database.state_dir, candidate.path, hub).state == "published"
    assert hub.calls == 1


@pytest.mark.parametrize("change", ["file", "suppression", "journal", "input"])
def test_hold_does_not_hide_invalid_candidate_or_current_inputs(release, change):
    database, config, overrides, clock, hub, candidate = release
    control(database, Selector("all", "all"))
    if change == "file":
        (candidate.path / "README.md").write_text("changed public file")
    elif change == "suppression":
        (overrides / "suppressions.csv").write_text(
            "wsdc_id,name_norm,reason,date\n42,,private,2026-09-13\n"
        )
    elif change == "journal":
        changed_journal(database, config, overrides, clock)
    else:
        (overrides / "identity_overrides.csv").write_text("invalid journal")
    with pytest.raises(PublishError):
        publish(database.state_dir, candidate.path, hub)
    assert (candidate.path / "REJECTED").exists() and hub.calls == 0
    assert not database.connection.execute("SELECT 1 FROM execution_admissions").fetchone()


def test_pause_persists_in_flight_while_semantic_writes_are_fenced(release):
    database, _, _, _, hub, candidate = release
    real_create = hub.create_commit
    observed = []

    def commit(**kwargs):
        result = control(database, Selector("all", "all"))
        assert result["persisted"] and result["state"] == "pausing"
        assert result["draining_attempts"][0]["candidate_id"] == candidate.path.name
        # This connection can reserve SQLite writes; only unsafe semantics are
        # fenced. The old network-long BEGIN IMMEDIATE blocked even the pause.
        with sqlite3.connect(database.state_dir / "state.sqlite", timeout=0) as other:
            other.execute("BEGIN IMMEDIATE")
            statements = [
                "UPDATE meta SET value='unsafe' WHERE key='input_bundle_hash'",
                "INSERT INTO admission_policies(page_kind,contract_version,mode,policy_revision) VALUES ('unsafe','1','enforce','unsafe')",
                "INSERT INTO pending_work VALUES ('parse','snapshot','unsafe','2026-09-13')",
                "INSERT INTO identity_decisions VALUES ('unsafe','eepro','event','contest','round','participant','42','same_person','evidence','reason','author','2026-09-13',NULL,'{}','sha','digest','now')",
            ]
            for statement in statements:
                with pytest.raises(sqlite3.IntegrityError, match="publication"):
                    other.execute(statement)
        observed.append(True)
        return real_create(**kwargs)

    hub.create_commit = commit
    assert publish(database.state_dir, candidate.path, hub).state == "published"
    assert observed == [True]
    after = current_status(database)
    assert after["state"] == "paused" and not after["draining_attempts"]
    assert database.connection.execute("SELECT state,outcome FROM execution_admissions").fetchone()[
        :
    ] == ("settled", "published")


def test_uncertain_landed_request_recovers_despite_pause_and_new_journal(release):
    database, config, overrides, clock, hub, candidate = release
    real_create = hub.create_commit

    def commit(**kwargs):
        control(database, Selector("source", "eepro"))
        hub.lose_response = True
        return real_create(**kwargs)

    hub.create_commit = commit
    with pytest.raises(ConnectionError):
        publish(database.state_dir, candidate.path, hub)
    state = current_status(database)
    assert state["state"] == "pausing" and state["draining_attempts"][0]["state"] == "uncertain"
    changed_journal(database, config, overrides, clock)
    assert reconcile(database.state_dir, hub, dry_run=False).state == "recovered"
    assert hub.calls == 1 and current_status(database)["state"] == "paused"
    assert json.loads((candidate.path / "PUBLISHED").read_bytes())["latency_is_upper_bound"]


def test_uncertain_unlanded_request_settles_but_pause_holds_retry(release):
    database, _, _, _, hub, candidate = release
    original = hub.create_commit

    def offline(**_):
        control(database, Selector("kind", "parse_failure"))
        raise ConnectionError("not delivered")

    hub.create_commit = offline
    with pytest.raises(ConnectionError):
        publish(database.state_dir, candidate.path, hub)
    hub.create_commit = original
    assert reconcile(database.state_dir, hub, dry_run=False).state == "held"
    assert hub.calls == 0 and current_status(database)["state"] == "paused"
    assert database.connection.execute("SELECT state,outcome FROM execution_admissions").fetchone()[
        :
    ] == ("settled", "not_landed")
    assert not (candidate.path / "REJECTED").exists()
    control(database, Selector("kind", "parse_failure"), paused=False)
    assert reconcile(database.state_dir, hub, dry_run=False).state == "published"
    assert (
        database.connection.execute("SELECT COUNT(*) FROM execution_admissions").fetchone()[0] == 2
    )


def test_unrecovered_active_request_is_not_cleared_by_old_head(release):
    database, _, _, _, hub, candidate = release
    original = hub.create_commit

    def inflight(**kwargs):
        assert reconcile(database.state_dir, hub, dry_run=False).state == "draining"
        assert (
            database.connection.execute("SELECT state FROM execution_admissions").fetchone()[0]
            == "active"
        )
        return original(**kwargs)

    hub.create_commit = inflight
    assert publish(database.state_dir, candidate.path, hub).state == "published"
    assert hub.calls == 1


def test_restart_recovery_keeps_publication_uncertain_until_reconciled(release):
    database, _, _, _, hub, candidate = release

    def interrupted(**_):
        raise ConnectionError("interrupted request")

    hub.create_commit = interrupted
    with pytest.raises(ConnectionError):
        publish(database.state_dir, candidate.path, hub)
    # Model death before the exception handler persisted its uncertain outcome.
    database.connection.execute("UPDATE execution_admissions SET state='active',outcome=NULL")
    control(database, Selector("all", "all"))
    with database.transaction():
        assert recover_admissions(database.connection, now=datetime.now(UTC)) == 1
    assert current_status(database)["draining_attempts"][0]["state"] == "uncertain"
    assert reconcile(database.state_dir, hub, dry_run=False).state == "held"
    assert current_status(database)["state"] == "paused" and hub.calls == 0


def test_process_death_retains_active_pause_drain_until_recovery(release):
    import os
    import subprocess
    import sys
    from pathlib import Path

    database, _, _, _, hub, candidate = release
    root = Path(__file__).parents[2]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(root / "src"), str(root / "tests/publish")))
    code = (
        "import sys; from pathlib import Path; from test_recovery import FakeHub; "
        "from swingset.publish.service import publish; "
        "hub=FakeHub(); "
        "hub.create_commit=lambda **kwargs:(print('reserved',flush=True),sys.stdin.read())[1]; "
        "publish(Path(sys.argv[1]),Path(sys.argv[2]),hub)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(database.state_dir), str(candidate.path)],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "reserved"
        assert control(database, Selector("all", "all"))["state"] == "pausing"
        child.kill()
        child.wait(timeout=5)
        assert current_status(database)["draining_attempts"][0]["state"] == "active"
        with database.transaction():
            assert recover_admissions(database.connection, now=datetime.now(UTC)) == 1
        assert current_status(database)["draining_attempts"][0]["state"] == "uncertain"
        assert reconcile(database.state_dir, hub, dry_run=False).state == "held"
        assert current_status(database)["state"] == "paused"
        assert hub.calls == 0 and not (candidate.path / "REJECTED").exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_the_release_loop_publishes_through_a_hub(tmp_path, monkeypatch):
    """A release tick reconciles, builds and publishes (WP9); a dry run only builds."""
    import shutil

    from test_recovery import FakeHub

    from swingset.clock import FakeClock
    from swingset.config import load_config
    from swingset.loops import release as release_loop
    from swingset.loops.runtime import tick
    from swingset.state.db import open_database
    from swingset.state.inputs import accept, capture
    from swingset.versions import versions

    monkeypatch.setenv("SWINGSET_REVISION", "publication-safety-fixture")
    config, overrides, state = tmp_path / "config", tmp_path / "overrides", tmp_path / "state"
    shutil.copytree("config", config)
    overrides.mkdir()
    (overrides / "identity_overrides.csv").write_text(
        "decision_id,source,source_event,contest,round,participant,wsdc_id,decision,evidence,reason,author,date,supersedes\n"
    )
    (overrides / "suppressions.csv").write_text("wsdc_id,name_norm,reason,date\n")
    clock, hub = FakeClock(), FakeHub()
    with open_database(state) as database:
        accept(database, capture(config, overrides, state, versions()), clock)
        database.connection.execute("DELETE FROM pending_work")

    def release_tick(*, dry_run):
        with tick(
            "release",
            state=state,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            budget=1800,
            hub=hub,
            dry_run=dry_run,
        ) as current:
            release_loop.run(current, disk_file=tmp_path / "no-disk-quota")
        assert current.record is not None and not current.record["failed"], current.record
        return current.record

    published = release_tick(dry_run=False)
    assert not published["dry_run"] and published["reconciliation"]["state"] == "none"
    assert published["publication"]["state"] == "published" and hub.calls == 1
    assert published["publish_commit"] == hub.current == "commit-1"
    assert published["counters"]["publications"] == 1 and published["progress"] >= 1
    assert (state / "baseline").is_symlink()
    # Past the shipped build interval, a dry run with the same hub reconciles
    # and builds but commits nothing.
    clock.sleep(load_config(config).scheduler.build_interval_seconds + 3600)
    dry = release_tick(dry_run=True)
    assert dry["dry_run"] and dry["reconciliation"]["state"] == "none"
    assert dry["candidate_id"] and "publication" not in dry and hub.calls == 1
    with open_database(state, lock=False) as database:
        assert database.connection.execute(
            "SELECT state,outcome FROM execution_admissions WHERE action_kind='publication'"
        ).fetchone()[:] == ("settled", "published")
        assert (
            database.connection.execute(
                "SELECT dry_run FROM runs WHERE run_id=?", (published["run_id"],)
            ).fetchone()[0]
            == 0
        )


def test_a_publication_waits_out_a_busy_writer_at_admission_and_settle(release):
    """A loop's write can hold SQLite past the old 5 s busy timeout; the
    publication's admission and its settle after the upload wait as any
    writer does (F7), so the fence never stays up for want of a retry.
    The connection's timeout is asserted; the writers hold a second each,
    so the core suite does not wait out the old 5 s."""
    import threading
    import time

    from swingset.publish.safety import _connection

    database, _, _, _, hub, candidate = release
    path = database.state_dir / "state.sqlite"
    probe = _connection(database.state_dir)
    assert probe is not None
    try:
        assert probe.execute("PRAGMA busy_timeout").fetchone()[0] >= 70000
    finally:
        probe.close()
    holders: list[threading.Thread] = []

    def busy(seconds: float = 1.0) -> None:
        started = threading.Event()

        def hold() -> None:
            other = sqlite3.connect(path, isolation_level=None)
            try:
                other.execute("BEGIN IMMEDIATE")
                started.set()
                time.sleep(seconds)
                other.execute("COMMIT")
            finally:
                other.close()

        thread = threading.Thread(target=hold)
        thread.start()
        assert started.wait(5)
        holders.append(thread)

    upload = hub.create_commit

    def create_commit(**kwargs):
        sha = upload(**kwargs)
        busy()  # the settle after the upload meets a busy writer
        return sha

    hub.create_commit = create_commit
    busy()  # the admission meets a busy writer
    try:
        result = publish(database.state_dir, candidate.path, hub)
    finally:
        for thread in holders:
            thread.join()
    assert result.state == "published" and hub.calls == 1
    assert len(holders) == 2
    assert (
        database.connection.execute(
            "SELECT count(*) FROM execution_admissions WHERE state<>'settled'"
        ).fetchone()[0]
        == 0
    )
