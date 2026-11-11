"""Real Postgres concurrency and durable H13 control boundaries, without requests."""

import contextlib
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_postgres_schema import fresh_database  # noqa: F401

from swingset.backup.checkpoint import (
    create_checkpoint,
    restore_checkpoint,
    restore_from_checkpoint,
)
from swingset.clock import FakeClock
from swingset.state import controls, engine
from swingset.state.attempts import begin_attempt, finish_attempt
from swingset.state.control_lock import ControlTimeout, control_lock
from swingset.state.controls import (
    ActionScope,
    ControlPaused,
    Selector,
    admission,
    change_control,
    matching_pauses,
    operation,
    recover_admissions,
    settle,
    status,
)
from swingset.state.db import SCHEMA_VERSION, open_database
from swingset.state.environment import FixtureIO
from swingset.state.work import WorkUnit, enqueue


class EmptyHub(FixtureIO):
    def head(self):
        return None


NOW = datetime(2026, 9, 13, tzinfo=UTC)
SCOPE = ActionScope(frozenset({"eepro"}), frozenset({"round_observations"}), "example.test")


def change(path, kind="all", identifier="all", *, paused=True, now=NOW, **kwargs):
    return change_control(
        path,
        selector=Selector(kind, identifier),
        paused=paused,
        actor="test:operator",
        reason="maintenance",
        now=now,
        hosts=("example.test",),
        **kwargs,
    )


def test_controls_commit_while_data_writer_lock_is_held_and_restart_retains_pause(tmp_path):
    with open_database(tmp_path) as db:
        receipt = change(tmp_path)
        assert receipt["persisted"] and receipt["state"] == "paused"
        assert (
            receipt["pause_id"]
            == db.connection.execute("SELECT pause_id FROM operator_pauses").fetchone()[0]
        )
        with pytest.raises(ControlPaused):
            with admission(db, action_id="denied", action_kind="request", scope=SCOPE, now=NOW):
                pytest.fail("paused action started")
        assert db.connection.execute("SELECT COUNT(*) FROM execution_admissions").fetchone()[0] == 0
    with open_database(tmp_path) as db:
        assert status(db.connection, now=NOW)["pauses"][0]["pause_id"] == receipt["pause_id"]
        resumed = change(tmp_path, paused=False)
        repeated = change(tmp_path, paused=False)
        assert resumed["changed"] and not repeated["changed"]
        assert resumed["control_revision"] == repeated["control_revision"]


def test_overlapping_scopes_expiry_and_resume_preserve_automatic_host_block(tmp_path):
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO hosts (host, paused_until, pause_reason) VALUES ('example.test', %s, 'Blocked')",
            ((NOW + timedelta(days=1)).isoformat(),),
        )
        change(tmp_path)
        change(tmp_path, "source", "eepro")
        timed = change(tmp_path, "kind", "round_observations", until=NOW + timedelta(seconds=5))
        change(tmp_path, paused=False)
        change(tmp_path, "source", "eepro", paused=False)
        assert len(matching_pauses(db.connection, SCOPE, now=NOW)) == 1
        later = NOW + timedelta(seconds=6)
        assert not matching_pauses(db.connection, SCOPE, now=later)
        assert (
            status(db.connection, now=later)["expired_pauses"][0]["pause_id"] == timed["pause_id"]
        )
        with admission(db, action_id="after-expiry", action_kind="request", scope=SCOPE, now=later):
            pass
        assert (
            db.connection.execute(
                "SELECT action FROM control_events WHERE pause_id = %s ORDER BY event_id DESC NULLS LAST",
                (timed["pause_id"],),
            ).fetchone()[0]
            == "expire"
        )
        assert db.connection.execute("SELECT pause_reason FROM hosts").fetchone()[0] == "Blocked"


def test_pausing_drain_and_uncertain_publication_require_receipt_settlement(tmp_path):
    with open_database(tmp_path) as db:
        with admission(
            db,
            action_id="publish",
            action_kind="publication",
            scope=SCOPE,
            candidate_id="candidate",
            now=NOW,
        ):
            pass
        receipt = change(tmp_path, now=NOW + timedelta(seconds=100))
        assert receipt["state"] == "pausing"
        assert not receipt["stuck_drain"]  # Drain age begins when the pause was committed.
        with db.transaction() as conn:
            settle(conn, "publish", now=NOW, outcome="lost_response", uncertain=True)
        assert status(db.connection, now=NOW + timedelta(seconds=161))["stuck_drain"]
    with open_database(tmp_path) as db:
        with db.transaction() as conn:
            assert recover_admissions(conn, now=NOW + timedelta(seconds=162)) == 0
        assert status(db.connection, now=NOW + timedelta(seconds=162))["state"] == "pausing"
        with db.transaction() as conn:
            settle(conn, "publish", now=NOW + timedelta(seconds=163), outcome="receipt_verified")
        assert status(db.connection, now=NOW + timedelta(seconds=163))["state"] == "paused"


def test_waiting_pause_wins_before_next_admission_after_atomic_unit(tmp_path, monkeypatch):
    acquired = threading.Event()
    original = controls.control_lock
    results = []

    @contextlib.contextmanager
    def observed_lock(*args, **kwargs):
        with original(*args, **kwargs):
            if threading.current_thread() is not threading.main_thread():
                acquired.set()
            yield

    monkeypatch.setattr(controls, "control_lock", observed_lock)
    with open_database(tmp_path) as db:
        with admission(db, action_id="first", action_kind="project", scope=SCOPE, now=NOW):
            pass
        with db.transaction() as conn:
            conn.execute("INSERT INTO meta (key, value) VALUES ('atomic-output', 'committed')")
            worker = threading.Thread(target=lambda: results.append(change(tmp_path, timeout=2)))
            worker.start()
            assert acquired.wait(1)
            assert not results  # Pause cannot acknowledge while this transaction owns SQLite.
            settle(conn, "first", now=NOW, outcome="succeeded")
        try:
            with pytest.raises(ControlPaused):
                with admission(db, action_id="next", action_kind="project", scope=SCOPE, now=NOW):
                    pytest.fail("a later unit bypassed the waiting pause")
        finally:
            worker.join(2)
        assert results[0]["persisted"]
        assert (
            db.connection.execute("SELECT value FROM meta WHERE key = 'atomic-output'").fetchone()[
                0
            ]
            == "committed"
        )


def test_control_timeout_never_claims_persistence(tmp_path):
    with open_database(tmp_path) as db:
        with db.transaction():
            with pytest.raises(ControlTimeout, match="no change was persisted"):
                change(tmp_path, timeout=0.01)
        assert not db.connection.execute("SELECT 1 FROM operator_pauses").fetchone()
        assert not db.connection.execute("SELECT 1 FROM control_events").fetchone()


def test_admission_rollback_and_lock_order_guard(tmp_path):
    with open_database(tmp_path) as db:
        with pytest.raises(RuntimeError, match="issuance failed"):
            with admission(
                db, action_id="rollback", action_kind="fetch", scope=SCOPE, now=NOW
            ) as conn:
                conn.execute("INSERT INTO meta (key, value) VALUES ('issued', 'yes')")
                raise RuntimeError("issuance failed")
        assert not db.connection.execute("SELECT 1 FROM execution_admissions").fetchone()
        assert not db.connection.execute("SELECT 1 FROM meta WHERE key = 'issued'").fetchone()
        with db.transaction():
            with pytest.raises(ValueError, match="precede"):
                with admission(db, action_id="deadlock", action_kind="fetch", scope=SCOPE, now=NOW):
                    pass


def test_shared_work_waits_whole_but_host_pause_allows_offline_work(tmp_path):
    with open_database(tmp_path) as db:
        change(tmp_path, "host", "example.test")
        with admission(
            db,
            action_id="offline",
            action_kind="project",
            scope=ActionScope(sources=SCOPE.sources),
            now=NOW,
        ):
            pass
        change(tmp_path, "source", "eepro")
        shared = ActionScope(sources=frozenset({"eepro", "wdr"}))
        with pytest.raises(ControlPaused) as caught:
            with admission(db, action_id="shared", action_kind="project", scope=shared, now=NOW):
                pass
        assert caught.value.pauses[0]["scope_id"] == "eepro"
        with admission(
            db,
            action_id="unrelated",
            action_kind="project",
            scope=ActionScope(sources=frozenset({"wdr"})),
            now=NOW,
        ):
            pass


def test_recovery_settles_requests_and_keeps_publication_uncertain(tmp_path):
    with open_database(tmp_path) as db:
        with admission(db, action_id="request", action_kind="fetch", scope=SCOPE, now=NOW):
            pass
        with admission(db, action_id="publish", action_kind="publish", scope=SCOPE, now=NOW):
            pass
    with open_database(tmp_path) as db:
        with db.transaction() as conn:
            assert recover_admissions(conn, now=NOW) == 2
            assert recover_admissions(conn, now=NOW) == 0
        rows = {
            row[0]: (row[1], row[2])
            for row in db.connection.execute(
                "SELECT action_id, state, outcome FROM execution_admissions"
            )
        }
        assert rows == {
            "request": ("settled", "process_interrupted"),
            "publish": ("uncertain", "receipt_reconciliation_required"),
        }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action_kind": "parse"},
        {"action_kind": "link"},
        {"action_kind": "project", "action_id": "work_0123"},
        {"action_kind": "request", "work_attempt_id": 1},
    ],
)
def test_admission_refuses_leased_units(tmp_path, kwargs):
    """A unit's lease is its in-flight record; the ledger never takes it back (D-0238)."""
    arguments = {"action_id": "unit", "scope": SCOPE, "now": NOW} | kwargs
    with open_database(tmp_path) as db:
        with pytest.raises(ValueError, match="leased units carry no admission"):
            with admission(db, **arguments):
                pytest.fail("a unit was admitted")
        assert not db.connection.execute("SELECT 1 FROM execution_admissions").fetchone()


def _ledger_row(conn, action_id, kind, settled_at, *, state="settled", outcome="completed"):
    conn.execute(
        (
            "INSERT INTO execution_admissions (action_id, action_kind, admitted_at, control_revision, "
            "all_sources, all_kinds, state, settled_at, outcome) VALUES (%s, %s, %s, 0, 0, 0, %s, %s, %s)"
        ),
        (action_id, kind, settled_at, state, None if state != "settled" else settled_at, outcome),
    )
    conn.execute(
        "INSERT INTO execution_dependencies (action_id, scope_kind, scope_id) VALUES (%s, 'source', 'eepro')",
        (action_id,),
    )


def test_prune_drops_old_settled_rows_but_newest_builds_publications_and_quota_rows(tmp_path):
    """Of builds, only the newest admitted and the newest completed stay:
    the build interval and release lag read nothing else."""
    from swingset.schedule.release_steps import build_deferred_for_interval
    from swingset.state.publication_fence import PUBLICATION_KINDS

    assert controls.KEPT_KINDS == PUBLICATION_KINDS
    old = (NOW - timedelta(days=31)).isoformat()
    with open_database(tmp_path) as db:
        with db.transaction() as conn:
            _ledger_row(conn, "old-request", "request", old)
            _ledger_row(conn, "new-request", "request", (NOW - timedelta(days=29)).isoformat())
            _ledger_row(conn, "older-build", "build", (NOW - timedelta(days=500)).isoformat())
            _ledger_row(conn, "old-build", "build", (NOW - timedelta(days=400)).isoformat())
            _ledger_row(
                conn,
                "newest-build",
                "build",
                (NOW - timedelta(days=40)).isoformat(),
                outcome="failed",
            )
            _ledger_row(conn, "old-publication", "publication", old)
            _ledger_row(conn, "active-request", "request", old, state="active")
            _ledger_row(conn, "quota-request", "request", old)
            run = db.start_run(NOW - timedelta(days=31))
            conn.execute(
                (
                    "INSERT INTO scheduler_requests (action_id, host, day, category, work_key, run_id, "
                    "issued_at) VALUES ('quota-request', 'example.test', 'd', 'c', 'k', %s, %s)"
                ),
                (run, old),
            )
        config = SimpleNamespace(scheduler=SimpleNamespace(build_interval_seconds=3600 * 24 * 90))
        before = build_deferred_for_interval(db.connection, config, now=NOW)
        with db.transaction() as conn:
            assert controls.prune(conn, now=NOW, batch=5000) == 2
            assert controls.prune(conn, now=NOW, batch=5000) == 0
        assert build_deferred_for_interval(db.connection, config, now=NOW) == before
        kept = {
            row[0] for row in db.connection.execute("SELECT action_id FROM execution_admissions")
        }
        assert kept == {
            "new-request",
            "newest-build",
            "old-build",
            "old-publication",
            "active-request",
            "quota-request",
        }
        assert {
            row[0] for row in db.connection.execute("SELECT action_id FROM execution_dependencies")
        } == kept
        assert not db.connection.execute(
            "SELECT conname FROM pg_catalog.pg_constraint WHERE contype='f' AND NOT convalidated"
        ).fetchall()


def test_pause_checkpoint_restore_preserves_controls_and_retry_evidence(tmp_path, legacy_sqlite):
    legacy_sqlite.assert_case(
        "test_controls.py", "test_pause_checkpoint_restore_preserves_controls_and_retry_evidence"
    )
    original, restored = tmp_path / "original", tmp_path / "restored"
    with open_database(original) as db:
        run = db.start_run(NOW)
        unit = WorkUnit("parse", "snapshot", "missing-artifact")
        enqueue(db.connection, [unit], enqueued_at=NOW.isoformat())
        attempt = begin_attempt(db, unit, now=NOW, run_id=run)
        finish_attempt(
            db,
            attempt,
            outcome="transient",
            reason_code="restore_delayed",
            now=NOW,
            retry_at=NOW + timedelta(minutes=5),
        )
        disposable = ("pending_work", "work_generations", "work_attempts")
        assert all(
            db.connection.execute(f"SELECT 1 FROM {table}").fetchone() for table in disposable
        )
        receipt = change(original)
        preserved = {
            table: [tuple(row) for row in db.connection.execute(f"SELECT * FROM {table}")]
            for table in ("operator_pauses", "control_events", "control_state")
        }
        checkpoint = create_checkpoint(
            original,
            db.connection,
            tmp_path / "checkpoint",
            schema_version=SCHEMA_VERSION,
            versions={},
            input_bundle_hash=None,
        )
    assert "control.lock" not in checkpoint.files
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
    with open_database(restored, lock=False, read_only=True, allow_restore_pending=True) as db:
        assert status(db.connection, now=NOW)["pauses"][0]["pause_id"] == receipt["pause_id"]
        for table, rows in preserved.items():
            assert [tuple(row) for row in db.connection.execute(f"SELECT * FROM {table}")] == rows
        # Queue hints and unrooted attempts are reconstructed from domain facts.
        # This missing-artifact hint has no retained domain root to rebuild.
        assert all(
            not db.connection.execute(f"SELECT 1 FROM {table}").fetchone() for table in disposable
        )
    change(restored, paused=False)
    assert (restored / "RESTORE_PENDING").is_file()
    with pytest.raises(RuntimeError, match="restore verification"):
        with open_database(restored):
            pytest.fail("resuming controls must not remove the restore barrier")
    with open_database(restored, lock=False, read_only=True, allow_restore_pending=True) as db:
        assert not db.connection.execute("SELECT 1 FROM operator_pauses").fetchone()
        assert (
            db.connection.execute("SELECT COUNT(*) FROM control_events").fetchone()[0]
            == len(preserved["control_events"]) + 1
        )
        assert [
            tuple(row)
            for row in db.connection.execute(
                "SELECT * FROM control_events ORDER BY event_id LIMIT %s",
                (len(preserved["control_events"]),),
            )
        ] == preserved["control_events"]
    activated = tmp_path / "activated"
    restore_from_checkpoint(
        checkpoint.path,
        activated,
        EmptyHub(),
        FakeClock(NOW),
        lock_timeout=0,
    )
    with open_database(activated) as db:
        with pytest.raises(ControlPaused):
            with admission(
                db, action_id="after-restore", action_kind="request", scope=SCOPE, now=NOW
            ):
                pytest.fail("restored indefinite pause must precede every admission")
        assert not db.connection.execute("SELECT 1 FROM execution_admissions").fetchone()
        for table, rows in preserved.items():
            assert [tuple(row) for row in db.connection.execute(f"SELECT * FROM {table}")] == rows
        # Queue hints and unrooted attempts are reconstructed from domain facts.
        # This missing-artifact hint has no retained domain root to rebuild.
        assert all(
            not db.connection.execute(f"SELECT 1 FROM {table}").fetchone() for table in disposable
        )
    before = {p.name for p in checkpoint.path.iterdir()}
    with pytest.raises(ValueError, match="immutable"):
        change(checkpoint.path)
    assert {p.name for p in checkpoint.path.iterdir()} == before


@pytest.mark.parametrize(
    "kind,identifier",
    [("source", "unknown"), ("kind", "parse"), ("host", "unregistered.invalid"), ("all", "wrong")],
)
def test_unknown_selectors_do_not_change_control_history(tmp_path, kind, identifier):
    with open_database(tmp_path) as db:
        with pytest.raises(ValueError, match="unknown control selector"):
            change(tmp_path, kind, identifier)
        assert not db.connection.execute("SELECT 1 FROM control_events").fetchone()


def test_control_open_never_migrates_older_state(tmp_path):
    with open_database(tmp_path) as database:
        database.connection.execute("UPDATE ops.meta SET value='11' WHERE key='schema_version'")
    with pytest.raises(RuntimeError, match="schema version"):
        change(tmp_path)
    with engine.connect(engine.resolve_dsn(tmp_path)) as conn:
        assert (
            conn.execute("SELECT value FROM ops.meta WHERE key='schema_version'").fetchone()[0]
            == "11"
        )
        assert conn.execute("SELECT count(*) FROM operator_pauses").fetchone()[0] == 0


def test_control_mutex_fences_lifecycle_replacement(tmp_path):
    with open_database(tmp_path) as database:
        with control_lock(tmp_path, connection=database.connection):
            with pytest.raises(ControlTimeout):
                change(tmp_path, timeout=0)


def test_legacy_pause_import_retains_selector_reason_expiry_and_unknown_actor(
    tmp_path,
    fresh_database,  # noqa: F811
):  # noqa: F811
    import hashlib
    import sqlite3 as checkpoint_sqlite
    from pathlib import Path

    from swingset.state import postgres_import

    service, name, destination = fresh_database
    source = tmp_path / "source" / "state.sqlite"
    source.parent.mkdir()
    with checkpoint_sqlite.connect(source) as conn:
        conn.executescript(Path("tests/fixtures/postgres/schema37.sql").read_text())
        conn.execute("PRAGMA user_version=37")
        conn.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason,pause_id,actor,control_revision,created_at) VALUES('source','eepro',NULL,'legacy maintenance','legacy_fixture','legacy:unknown',0,NULL)"
        )
        conn.execute(
            "INSERT INTO control_events(control_revision,pause_id,scope_kind,scope_id,action,actor,reason,until_at,occurred_at) VALUES(0,'legacy_fixture','source','eepro','legacy_import','legacy:unknown','legacy maintenance',NULL,?)",
            (NOW.isoformat(),),
        )
    imported = postgres_import.load(
        source, destination, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert imported["complete"] and imported["audit"]["equal"]
    state = tmp_path / "imported"
    engine.write_settings(
        state, service.dsn(name, role="worker"), control_dsn=service.dsn(name, role="control")
    )
    with open_database(state) as database:
        paused = status(database.connection, now=NOW)["pauses"][0]
        assert paused["scope_id"] == "eepro"
        assert paused["reason"] == "legacy maintenance"
        assert paused["actor"] == "legacy:unknown"
        assert paused["paused_duration_seconds"] is None
        assert paused["pause_id"].startswith("legacy_")
        assert (
            database.connection.execute("SELECT action FROM control_events").fetchone()[0]
            == "legacy_import"
        )


def test_paused_publication_still_validates_safety_before_admission(tmp_path):
    with open_database(tmp_path) as db:
        change(tmp_path)

        def validate(conn):
            assert conn.in_transaction
            raise ValueError("suppression inputs changed")

        with pytest.raises(ValueError, match="suppression inputs changed"):
            with admission(
                db,
                action_id="unsafe",
                action_kind="publication",
                scope=SCOPE,
                now=NOW,
                validate=validate,
            ):
                pass
        assert not db.connection.execute("SELECT 1 FROM execution_admissions").fetchone()


def test_checkpoint_control_snapshot_stays_consistent_while_controls_continue(
    tmp_path, monkeypatch
):
    """A resume after snapshot pinning commits without changing captured controls."""
    from swingset.backup import snapshot

    original_export = snapshot.postgres_backup.export
    state = tmp_path / "state"
    with open_database(state) as db:
        receipt = change(state)
        resumed = threading.Thread(target=lambda: change(state, paused=False))

        def capture_export(conn, *args, **kwargs):
            assert (
                conn.execute("SELECT pause_id FROM operator_pauses").fetchone()[0]
                == receipt["pause_id"]
            )
            resumed.start()
            resumed.join(5)
            # The short control turn is released once the snapshot and files
            # are pinned. A later resume commits without changing that snapshot.
            assert not resumed.is_alive()
            assert (
                conn.execute("SELECT pause_id FROM operator_pauses").fetchone()[0]
                == receipt["pause_id"]
            )
            assert not db.connection.execute("SELECT 1 FROM operator_pauses").fetchone()
            return original_export(conn, *args, **kwargs)

        monkeypatch.setattr(snapshot.postgres_backup, "export", capture_export)
        checkpoint = create_checkpoint(
            state,
            db.connection,
            tmp_path / "saved",
            schema_version=SCHEMA_VERSION,
            versions={},
            input_bundle_hash=None,
        )
        assert not resumed.is_alive()
    restored = tmp_path / "snapshot-restored"
    restore_checkpoint(checkpoint.path, restored, maximum_schema_version=SCHEMA_VERSION)
    with open_database(restored, lock=False, read_only=True, allow_restore_pending=True) as copied:
        conn = copied.connection
        assert (
            conn.execute("SELECT pause_id FROM operator_pauses").fetchone()[0]
            == receipt["pause_id"]
        )
        assert conn.execute("SELECT COUNT(*) FROM control_events").fetchone()[0] == 1


def test_publication_fence_allows_control_and_receipt_writes_only(tmp_path):
    with open_database(tmp_path) as db:
        with admission(db, action_id="publish", action_kind="publication", scope=SCOPE, now=NOW):
            pass
        with pytest.raises(engine.IntegrityError, match="fences semantic writes"):
            db.connection.execute("INSERT INTO meta (key, value) VALUES ('stale-input', 'bad')")
        with pytest.raises(engine.IntegrityError) as duplicate:
            with admission(db, action_id="second", action_kind="publish", scope=SCOPE, now=NOW):
                pass
        assert duplicate.value.sqlstate == "23505"
        assert change(tmp_path)["state"] == "pausing"
        with db.transaction() as conn:
            settle(conn, "publish", now=NOW, outcome="response_lost", uncertain=True)
        db.connection.execute("INSERT INTO meta (key, value) VALUES ('new-correction', 'accepted')")
        assert status(db.connection, now=NOW)["state"] == "pausing"


def test_future_migration_recovers_stale_reservation_and_fences_new_tables(tmp_path, monkeypatch):
    import shutil

    from swingset.dev.services import ensure_service
    from swingset.state import postgres_schema, rwlock
    from swingset.state.db import Database

    state = tmp_path / "state"
    with open_database(state) as database:
        name = database.connection.info.dbname
        identity = database.connection.execute(
            "SELECT database_id FROM ops.environment_identity"
        ).fetchone()[0]
        with admission(
            database, action_id="old-publication", action_kind="publication", scope=SCOPE, now=NOW
        ):
            pass
    migrations = tmp_path / "future-migrations"
    shutil.copytree(postgres_schema.ROOT, migrations)
    future_version = postgres_schema.SCHEMA_VERSION + 1
    (migrations / f"{future_version:04d}_fixture_probe.sql").write_text(
        "CREATE TABLE ops.future_evidence(id TEXT PRIMARY KEY);"
        "GRANT SELECT,INSERT,UPDATE,DELETE ON ops.future_evidence TO swingset_worker;"
    )
    monkeypatch.setattr(postgres_schema, "ROOT", migrations)
    monkeypatch.setattr(postgres_schema, "SCHEMA_VERSION", future_version)
    with pytest.raises(RuntimeError, match="schema version|checksum"):
        open_database(state)
    with ensure_service().connect(name) as raw:
        conn = engine.connect(raw.info.dsn, password=raw.info.password)
    held = rwlock.exclusive(state, connection=conn)
    with Database(state, conn, held) as migration:
        with control_lock(state, connection=conn), migration.transaction():
            recover_admissions(conn, now=NOW)
            postgres_schema.migrate(conn)
    with open_database(state) as database:
        assert database.schema_version == future_version
        assert (
            database.connection.execute(
                "SELECT database_id FROM ops.environment_identity"
            ).fetchone()[0]
            == identity
        )
        assert (
            database.connection.execute("SELECT state FROM execution_admissions").fetchone()[0]
            == "uncertain"
        )
        with admission(
            database, action_id="new-publication", action_kind="publication", scope=SCOPE, now=NOW
        ):
            pass
        with pytest.raises(engine.IntegrityError, match="fences semantic writes"):
            database.connection.execute("INSERT INTO ops.future_evidence(id) VALUES('unsafe')")


def test_operation_keeps_control_live_across_body_commits_and_records_failure(tmp_path):
    class Clock:
        def now(self):
            return NOW

    with open_database(tmp_path) as db:
        with pytest.raises(ValueError, match="body failed"):
            with operation(
                db, action_id="fetch-lifecycle", action_kind="fetch", scope=SCOPE, clock=Clock()
            ):
                assert not db.connection.in_transaction
                assert change(tmp_path)["state"] == "pausing"
                with db.transaction() as conn:
                    conn.execute(
                        "INSERT INTO meta (key, value) VALUES ('bounded-checkpoint', 'retained')"
                    )
                raise ValueError("body failed")
        assert status(db.connection, now=NOW)["state"] == "paused"
        assert (
            db.connection.execute("SELECT outcome FROM execution_admissions").fetchone()[0]
            == "failed"
        )
        assert (
            db.connection.execute(
                "SELECT value FROM meta WHERE key = 'bounded-checkpoint'"
            ).fetchone()[0]
            == "retained"
        )


def test_source_wait_does_not_include_unrelated_kind_paused_action(tmp_path):
    with open_database(tmp_path) as db:
        with admission(
            db,
            action_id="wdr",
            action_kind="request",
            scope=ActionScope(sources=frozenset({"wdr"}), kinds=SCOPE.kinds),
            now=NOW,
        ):
            pass
        change(tmp_path, "kind", "round_observations")
        receipt = change(tmp_path, "source", "eepro")
        assert receipt["state"] == "paused"
        assert not receipt["draining_attempts"]
        assert status(db.connection, now=NOW)["state"] == "pausing"


def test_resuming_all_still_reports_overlapping_operator_host_pause(tmp_path):
    with open_database(tmp_path):
        change(tmp_path)
        host = change(tmp_path, "host", "example.test")
        receipt = change(tmp_path, paused=False)
        assert receipt["state"] == "paused"
        assert receipt["pauses"][0]["pause_id"] == host["pause_id"]


def test_an_admission_outwaits_the_write_alarm():
    """A worker write ends by the 75 s alarm at the latest, and no unit gets a
    longer one (D-0217), so a request or operation admission waiting behind it
    does not time out. A unit takes no control lock (D-0238)."""
    from swingset.state import write_deadline

    assert write_deadline.WRITE_SECONDS == 75.0
    assert not hasattr(write_deadline, "allowance")
    assert write_deadline.WRITE_SECONDS < controls.ADMISSION_TIMEOUT_SECONDS


def test_write_deadline_persists_waiting_pause_before_control_bound(
    tmp_path, monkeypatch, record_property
):
    from swingset.state import write_deadline

    acquired = threading.Event()
    original_lock = controls.control_lock
    results = []

    @contextlib.contextmanager
    def observed_lock(*args, **kwargs):
        with original_lock(*args, **kwargs):
            if threading.current_thread() is not threading.main_thread():
                acquired.set()
            yield

    monkeypatch.setattr(controls, "control_lock", observed_lock)
    with open_database(tmp_path) as db:
        with admission(db, action_id="slow-unit", action_kind="project", scope=SCOPE, now=NOW):
            pass
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.1)
        started = time.monotonic()
        worker = threading.Thread(target=lambda: results.append(change(tmp_path, timeout=1)))
        try:
            with pytest.raises(write_deadline.WriteDeadlineExceeded):
                with db.transaction() as conn:
                    conn.execute(
                        "INSERT INTO meta (key, value) VALUES ('uncommitted-slow-output', 'must rollback')"
                    )
                    worker.start()
                    assert acquired.wait(1)
                    time.sleep(2)  # An otherwise unbounded Python phase holding SQLite.
        finally:
            worker.join(2)
        elapsed = time.monotonic() - started
        record_property("write_bound_seconds", 0.1)
        record_property("control_bound_seconds", 1)
        record_property("persisted_control_latency_seconds", elapsed)
        assert elapsed < 1
        assert results[0]["persisted"] and results[0]["state"] == "pausing"
        assert not db.connection.execute(
            "SELECT 1 FROM meta WHERE key = 'uncommitted-slow-output'"
        ).fetchone()
        with db.transaction() as conn:
            settle(conn, "slow-unit", now=NOW, outcome="write_deadline_exceeded")
        assert status(db.connection, now=NOW)["state"] == "paused"
        with pytest.raises(ControlPaused):
            with admission(db, action_id="later-unit", action_kind="project", scope=SCOPE, now=NOW):
                pytest.fail("a later unit bypassed the persisted control")


def test_a_lease_drains_only_under_a_pause_of_its_dependencies(tmp_path):
    """A leased unit drains like an admission, scoped by `for_unit` (D-0238);
    a caller with no resolver counts it against every pause."""
    from swingset.state import queue
    from swingset.state.control_scopes import for_unit

    unit = WorkUnit("project", "dancer", "d1")
    with open_database(tmp_path) as db:
        run = db.start_run(NOW)
        with db.transaction() as conn:
            enqueue(conn, [unit], enqueued_at=NOW.isoformat())
            queue.lease(db, unit, owner=queue.Owner("derive", run), now=NOW)
        change(tmp_path, "source", "eepro")
        scoped = status(db.connection, now=NOW, unit_scope=for_unit)
        assert scoped["state"] == "paused" and scoped["active_attempts"] == 1
        assert status(db.connection, now=NOW)["state"] == "pausing"
        change(tmp_path, "source", "wsdc_registry")
        (lease,) = status(db.connection, now=NOW, unit_scope=for_unit)["draining_attempts"]
        assert lease["action_id"] == "lease:project/dancer/d1" and lease["work_attempt_id"] is None
        assert lease["lease_until"] == queue.at(NOW + timedelta(seconds=1800))
        assert not db.connection.execute("SELECT 1 FROM execution_admissions").fetchone()


def test_a_pause_commits_before_its_drain_report_and_survives_a_failing_unit_scope(tmp_path):
    """`change_control` resolves leased units after its commit, so the write
    lock is never held for those reads; a resolver that raises for one unit
    counts that lease against every pause, and the pause still stands (I1)."""
    from swingset.state import queue

    unit = WorkUnit("project", "dancer", "d1")
    with open_database(tmp_path) as db:
        run = db.start_run(NOW)
        with db.transaction() as conn:
            enqueue(conn, [unit], enqueued_at=NOW.isoformat())
            queue.lease(db, unit, owner=queue.Owner("derive", run), now=NOW)
    seen = []

    def failing(conn, leased):
        other = engine.connect(engine.resolve_dsn(tmp_path))
        try:
            seen.append(
                other.execute(
                    "SELECT COUNT(*) FROM operator_pauses WHERE scope_id = 'eepro'"
                ).fetchone()[0]
            )
        finally:
            other.close()
        raise engine.DatabaseError("malformed scope")

    result = change(tmp_path, "source", "eepro", unit_scope=failing)
    assert seen == [1]  # committed before the drain report read
    assert result["persisted"] and result["changed"] and result["state"] == "pausing"
    (lease,) = result["draining_attempts"]
    assert lease["action_id"] == "lease:project/dancer/d1"
    assert lease["scope_error"] == "DatabaseError: malformed scope"
    with open_database(tmp_path, lock=False) as db:
        (pause,) = matching_pauses(db.connection, SCOPE, now=NOW)
        assert pause["scope_id"] == "eepro"
        drained = status(db.connection, now=NOW, unit_scope=failing)
        assert drained["state"] == "pausing" and drained["draining_attempts"][0]["scope_error"]


def test_maintain_prunes_in_batches_until_done_or_stopped(tmp_path, monkeypatch):
    """`prune_admissions` repeats while a batch is full and checks stop()
    before each; `controls.prune` deletes at most one batch."""
    from swingset.loops.maintain import prune_admissions

    monkeypatch.setattr(controls, "PRUNE_BATCH", 2)
    old = (NOW - timedelta(days=31)).isoformat()

    def seed(db):
        with db.transaction() as conn:
            conn.execute("DELETE FROM execution_dependencies")
            conn.execute("DELETE FROM execution_admissions")
            for index in range(5):
                _ledger_row(conn, f"request-{index}", "request", old)

    def left(db):
        return db.connection.execute("SELECT COUNT(*) FROM execution_admissions").fetchone()[0]

    with open_database(tmp_path) as db:
        seed(db)
        with db.transaction() as conn:
            assert controls.prune(conn, now=NOW) == 5  # the default bound is fixed at import
        seed(db)
        with db.transaction() as conn:
            assert controls.prune(conn, now=NOW, batch=2) == 2
        seed(db)
        assert prune_admissions(db, now=NOW, stop=lambda: False) == 5
        assert left(db) == 0
        seed(db)
        calls = []

        def stop():
            calls.append(None)
            return len(calls) > 1

        assert prune_admissions(db, now=NOW, stop=stop) == 2
        assert left(db) == 3


def test_control_role_cannot_write_data_or_change_schema(tmp_path):
    with open_database(tmp_path) as database:
        with engine.connect(engine.resolve_control_dsn(tmp_path)) as control:
            for statement in (
                "INSERT INTO ops.meta(key,value) VALUES('forbidden','value')",
                "UPDATE ops.environment_identity SET environment='production'",
                "CREATE TABLE ops.forbidden_fixture(value TEXT)",
            ):
                with pytest.raises(engine.DatabaseError) as denied:
                    control.execute(statement)
                assert denied.value.sqlstate == "42501"
        assert (
            database.connection.execute("SELECT 1 FROM ops.meta WHERE key='forbidden'").fetchone()
            is None
        )


def test_private_copy_controls_use_its_settings_with_an_ambient_source_controller(
    tmp_path, monkeypatch
):
    from swingset.state.postgres_copy import Copies

    source, frozen, clone = (tmp_path / name for name in ("source", "frozen", "clone"))
    with open_database(source):
        monkeypatch.setenv("SWINGSET_CONTROL_DATABASE_URL", engine.resolve_control_dsn(source))
    with Copies(source) as copies:
        with open_database(source, read_only=True, lock=False) as database:
            copies.freeze(database.connection, frozen)
        copies.clone(frozen, clone)
        receipt = change(clone)
        assert receipt["persisted"] and receipt["state"] == "paused"
        with open_database(clone, read_only=True, lock=False) as isolated:
            assert (
                status(isolated.connection, now=NOW)["pauses"][0]["pause_id"] == receipt["pause_id"]
            )
        with open_database(source, read_only=True, lock=False) as original:
            assert status(original.connection, now=NOW)["pauses"] == []
        assert engine.resolve_control_dsn(
            tmp_path / "environment-only"
        ) == engine.resolve_control_dsn(source)


def test_worker_credentials_cannot_stand_in_for_a_control_role(tmp_path):
    with open_database(tmp_path):
        worker = engine.resolve_dsn(tmp_path)
        engine.write_settings(tmp_path, worker, control_dsn=worker)
        with pytest.raises(PermissionError, match="restricted control role"):
            change(tmp_path)


def test_control_lost_commit_acknowledgement_never_claims_no_persistence(tmp_path, monkeypatch):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    with open_database(tmp_path) as database:
        control_user = conninfo_to_dict(engine.resolve_control_dsn(tmp_path))["user"]
        original = psycopg.Connection.commit
        calls = []

        def lose_ack(conn):
            original(conn)
            if conn.info.user == control_user:
                calls.append(1)
                raise psycopg.OperationalError("private transport detail")

        monkeypatch.setattr(psycopg.Connection, "commit", lose_ack)
        with pytest.raises(engine.CommitUncertain):
            change(tmp_path)
        assert calls == [1]
        assert (
            database.connection.execute("SELECT count(*) FROM operator_pauses").fetchone()[0] == 1
        )


def test_operation_checks_active_admission_before_any_outside_effect(tmp_path, monkeypatch):
    from swingset.state.db import Database

    original = controls.check_admission
    issued = []

    def revoke(conn, action_id):
        with Database(tmp_path, conn, None).transaction():
            settle(conn, action_id, now=NOW, outcome="revoked", uncertain=True)
        original(conn, action_id)

    with open_database(tmp_path) as database:
        monkeypatch.setattr(controls, "check_admission", revoke)
        with pytest.raises(controls.AdmissionLost):
            with operation(
                database,
                action_id="fixture-request",
                action_kind="request",
                scope=SCOPE,
                clock=FakeClock(NOW),
            ):
                issued.append("outside call")
        assert issued == []


def test_explicit_admission_can_reuse_its_callers_control_turn(tmp_path):
    with open_database(tmp_path) as database:
        with control_lock(tmp_path, connection=database.connection):
            with admission(
                database,
                action_id="owned-turn",
                action_kind="request",
                scope=SCOPE,
                now=NOW,
                reuse_control_turn=True,
            ):
                pass
            controls.check_admission(database.connection, "owned-turn")
        with admission(
            database, action_id="next-turn", action_kind="request", scope=SCOPE, now=NOW
        ):
            pass


@pytest.mark.parametrize("environment_flag", [None, "dev", "production"])
def test_control_credentials_for_another_database_are_rejected(
    tmp_path, monkeypatch, environment_flag
):
    state, other_state = tmp_path / "state", tmp_path / "other"
    with open_database(state) as database, open_database(other_state) as other:
        original = engine.read_settings(state)
        wrong = engine.resolve_control_dsn(other_state)
        assert (
            database.connection.execute(
                "SELECT database_id FROM ops.environment_identity"
            ).fetchone()[0]
            != other.connection.execute(
                "SELECT database_id FROM ops.environment_identity"
            ).fetchone()[0]
        )
        engine.write_settings(
            state, original["dsn"], environment=original["environment"], control_dsn=wrong
        )
        if environment_flag is None:
            monkeypatch.delenv("SWINGSET_ENV", raising=False)
        else:
            monkeypatch.setenv("SWINGSET_ENV", environment_flag)
        with pytest.raises(RuntimeError, match="configured state database|database identity"):
            change_control(
                state,
                selector=Selector("all", "all"),
                paused=True,
                actor="operator",
                reason="must refuse",
                now=NOW,
            )
        assert database.connection.execute("SELECT count(*) FROM control_events").fetchone()[0] == 0
        assert other.connection.execute("SELECT count(*) FROM control_events").fetchone()[0] == 0


def test_admission_rejects_a_connection_from_another_database(tmp_path):
    from swingset.state.db import Database

    with open_database(tmp_path / "state") as expected, open_database(tmp_path / "other") as other:
        mismatched = Database(expected.state_dir, other.connection, None)
        with pytest.raises(RuntimeError, match="database identity"):
            with admission(
                mismatched,
                action_id="request:wrong-db",
                action_kind="acquisition",
                scope=ActionScope(),
                now=NOW,
            ):
                raise AssertionError("a mismatched admission was issued")
        assert (
            other.connection.execute("SELECT count(*) FROM execution_admissions").fetchone()[0] == 0
        )


def test_selector_registration_uses_names_without_importing_adapters_and_keeps_retired_pauses(
    tmp_path, monkeypatch
):
    from swingset import sources
    from swingset.sources.registry import ADAPTERS

    with open_database(tmp_path) as database:
        change(tmp_path, "source", "eepro")
        monkeypatch.delitem(ADAPTERS, "eepro")
        monkeypatch.setitem(ADAPTERS, "registered_without_import", "not.an.importable.adapter")

        def forbidden_adapter_imports():
            raise AssertionError("listing source names must not import adapters")

        monkeypatch.setattr(sources, "sources", forbidden_adapter_imports)
        found = controls.registered_selectors(
            database.connection, sources=("configured_source",), hosts=("configured.test",)
        )
        assert found["source"] == sorted({*ADAPTERS, "configured_source", "eepro"})
        assert "configured.test" in found["host"]
        assert found["all"] == ["all"]
