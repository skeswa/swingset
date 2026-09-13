"""A slow atomic worker releases SQLite without selecting partial output."""

import signal
import sqlite3
import time
from pathlib import Path

import pytest

from swingset.state import write_deadline
from swingset.state.db import open_database
from swingset.state.write_deadline import WriteDeadlineExceeded


def test_python_work_rolls_back_and_next_transaction_can_commit(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.03)
        with pytest.raises(WriteDeadlineExceeded):
            with db.transaction() as conn:
                conn.execute("INSERT INTO meta VALUES ('partial','forbidden')")
                while True:
                    pass
        assert not db.connection.in_transaction
        assert db.connection.execute("SELECT 1 FROM meta WHERE key='partial'").fetchone() is None
        with db.transaction() as conn:
            conn.execute("INSERT INTO meta VALUES ('independent','committed')")
        assert (
            db.connection.execute("SELECT value FROM meta WHERE key='independent'").fetchone()[0]
            == "committed"
        )


def test_nested_sql_work_inherits_deadline_and_rolls_back(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.03)
        with pytest.raises(WriteDeadlineExceeded):
            with db.transaction() as conn:
                conn.execute("INSERT INTO meta VALUES ('partial','forbidden')")
                with db.transaction():
                    conn.execute(
                        "WITH RECURSIVE counter(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM counter WHERE n<100000000) SELECT sum(n) FROM counter"
                    ).fetchone()
        assert not db.connection.in_transaction
        assert db.connection.execute("SELECT 1 FROM meta WHERE key='partial'").fetchone() is None


def test_long_read_snapshot_does_not_take_worker_write_budget(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.01)
        with db.transaction(immediate=False) as conn:
            conn.execute("SELECT COUNT(*) FROM meta").fetchone()
            time.sleep(0.03)


def test_read_snapshot_cannot_upgrade_through_nested_write(tmp_path):
    with open_database(tmp_path) as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            with db.transaction(immediate=False):
                with db.transaction() as conn:
                    conn.execute("INSERT INTO meta VALUES ('unbounded','forbidden')")
        assert not db.connection.in_transaction
        with db.transaction() as conn:
            conn.execute("INSERT INTO meta VALUES ('bounded','allowed')")


def test_existing_later_alarm_is_restored_with_elapsed_time(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.05)
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.setitimer(signal.ITIMER_REAL, 10)
        try:
            with db.transaction():
                time.sleep(0.01)
            remaining, interval = signal.getitimer(signal.ITIMER_REAL)
            assert 9 < remaining < 10 and interval == 0
            assert signal.getsignal(signal.SIGALRM) == previous_handler
        finally:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def test_timed_out_derivation_is_latched_while_independent_output_commits(tmp_path, monkeypatch):
    import swingset.project
    from swingset.clock import FakeClock
    from swingset.config import Config
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one
    from swingset.state.inputs import InputBundle
    from swingset.state.work import WorkUnit, enqueue, next_work

    clock = FakeClock()
    bundle = InputBundle("test", Path(tmp_path), {}, Config({}, {}))
    slow, healthy = [WorkUnit("project", "dancer", name) for name in ("slow", "healthy")]

    def project(db, unit, *_args, selection=None):
        db.connection.execute("INSERT INTO meta VALUES (?, 'selected')", (unit.unit_id,))
        if unit == slow:
            while True:
                pass
        from swingset.state import derivations

        derivations.complete(
            db.connection,
            selection,
            rows=(),
            now=clock.now(),
            run_id=selection.context.get("run_id", run),
        )
        db.connection.execute("DELETE FROM pending_work WHERE unit_id=?", (unit.unit_id,))

    monkeypatch.setattr(swingset.project, "process_unit", project)
    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        enqueue(db.connection, [slow, healthy], enqueued_at=clock.now().isoformat())
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.03)
        outcome = derive_one(db, Archive(tmp_path), slow, bundle, clock, run)
        assert outcome.failed and outcome.reason == "write_deadline_exceeded"
        assert not derive_one(db, Archive(tmp_path), healthy, bundle, clock, run).failed
        assert db.connection.execute("SELECT 1 FROM meta WHERE key='slow'").fetchone() is None
        assert (
            db.connection.execute("SELECT value FROM meta WHERE key='healthy'").fetchone()[0]
            == "selected"
        )
        assert db.connection.execute("SELECT unit_id FROM pending_work").fetchone()[0] == "slow"
        assert next_work(db.connection, "project", now=clock.now()) is None
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM execution_admissions WHERE state!='settled'"
            ).fetchone()[0]
            == 0
        )
    with open_database(tmp_path) as db:
        assert next_work(db.connection, "project", now=clock.now()) is None
        assert tuple(
            db.connection.execute(
                "SELECT outcome,reason_code FROM work_attempts WHERE unit_id='slow'"
            ).fetchone()
        ) == ("blocked", "write_deadline_exceeded")
