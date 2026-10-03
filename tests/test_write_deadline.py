"""A slow atomic worker releases SQLite without selecting partial output."""

import signal
import sqlite3
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from swingset.state import write_deadline
from swingset.state.db import open_database, reset_write_counters, write_counters
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


def test_timed_out_derivation_backs_off_while_independent_output_commits(tmp_path, monkeypatch):
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
                "SELECT outcome,reason_code,retry_at FROM work_attempts WHERE unit_id='slow'"
            ).fetchone()
        ) == (
            "transient",
            "write_deadline_exceeded",
            (clock.now() + timedelta(minutes=15)).isoformat(),
        )
        # An overrun is retried without an input change or an operator, and
        # each consecutive overrun doubles the wait (D-0198).
        clock.sleep(15 * 60)
        assert next_work(db.connection, "project", now=clock.now()) == slow
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.03)
        assert derive_one(db, Archive(tmp_path), slow, bundle, clock, run).reason == (
            "write_deadline_exceeded"
        )
        latest = db.connection.execute(
            "SELECT outcome,retry_at FROM work_attempts WHERE unit_id='slow' ORDER BY attempt_id DESC"
        ).fetchone()
        assert tuple(latest) == ("transient", (clock.now() + timedelta(minutes=30)).isoformat())


@pytest.mark.parametrize("nondeterministic", [False, True])
def test_a_unit_that_raises_keeps_a_doubling_retry_clock(tmp_path, monkeypatch, nondeterministic):
    import swingset.project
    from swingset.clock import FakeClock
    from swingset.config import Config
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one
    from swingset.state.derivations import NondeterministicOutputError
    from swingset.state.inputs import InputBundle
    from swingset.state.work import WorkUnit, enqueue, next_work

    clock = FakeClock()
    bundle = InputBundle("test", Path(tmp_path), {}, Config({}, {}))
    unit = WorkUnit("project", "dancer", "broken")
    # Output that differs for identical inputs is a failure too, not a benign
    # supersession that parks the unit with no clock.
    error = NondeterministicOutputError if nondeterministic else RuntimeError
    reason = "nondeterministic_output" if nondeterministic else "unit_exception"

    def project(*_args, selection=None):
        raise error("defect in code outside the unit's recipe")

    monkeypatch.setattr(swingset.project, "process_unit", project)
    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        enqueue(db.connection, [unit], enqueued_at=clock.now().isoformat())
        for delay in (15, 30):
            outcome = derive_one(db, Archive(tmp_path), unit, bundle, clock, run)
            assert outcome.failed and outcome.reason == reason
            latest = db.connection.execute(
                "SELECT outcome,retry_at FROM work_attempts ORDER BY attempt_id DESC"
            ).fetchone()
            # Every non-success has a clock (I3): no input change or operator
            # retry is needed for a fixed deploy to pick the unit up again.
            assert tuple(latest) == (
                "transient",
                (clock.now() + timedelta(minutes=delay)).isoformat(),
            )
            assert next_work(db.connection, "project", now=clock.now()) is None
            clock.sleep(delay * 60)
            assert next_work(db.connection, "project", now=clock.now()) == unit


def test_no_write_gets_a_longer_bound(tmp_path, monkeypatch):
    """The 60 s alarm is the one limit; the history allowance is gone (D-0217)."""
    from swingset.schedule import derive
    from swingset.state.write_deadline import bounded_write

    assert write_deadline.WRITE_SECONDS == 60.0
    assert not hasattr(write_deadline, "allowance")
    assert not hasattr(derive, "HISTORY_WRITE_SECONDS")
    monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.05)
    conn = sqlite3.connect(tmp_path / "x.sqlite")
    with pytest.raises(WriteDeadlineExceeded), bounded_write(conn):
        time.sleep(0.1)


def test_a_year_unit_over_the_bound_fails_like_any_other(tmp_path, monkeypatch):
    import swingset.project
    from swingset.clock import FakeClock
    from swingset.config import Config
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one
    from swingset.state.inputs import InputBundle
    from swingset.state.work import WorkUnit, enqueue

    clock = FakeClock()
    bundle = InputBundle("test", Path(tmp_path), {}, Config({}, {}))
    history = WorkUnit("project", "year", "2026")

    def project(db, unit, *_args, selection=None):
        while True:
            pass

    monkeypatch.setattr(swingset.project, "process_unit", project)
    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        enqueue(db.connection, [history], enqueued_at=clock.now().isoformat())
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.03)
        before = write_deadline.fired()
        outcome = derive_one(db, Archive(tmp_path), history, bundle, clock, run)
        assert outcome.failed and outcome.reason == "write_deadline_exceeded"
        assert write_deadline.fired() == before + 1


class _Monotonic:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def test_a_body_past_sixty_seconds_raises_and_is_counted(tmp_path, monkeypatch):
    from swingset.state.write_deadline import bounded_write

    clock = _Monotonic()
    monkeypatch.setattr(write_deadline, "time", clock)
    conn = sqlite3.connect(tmp_path / "x.sqlite")
    before = write_deadline.fired()
    with bounded_write(conn):
        clock.now += 59
    assert write_deadline.fired() == before
    with pytest.raises(WriteDeadlineExceeded, match="exceeded 60 seconds"), bounded_write(conn):
        clock.now += 61
    assert write_deadline.fired() == before + 1
    # Any other error in the body is not a firing.
    with pytest.raises(KeyError), bounded_write(conn):
        raise KeyError("unrelated")
    assert write_deadline.fired() == before + 1


def test_the_run_counters_report_firings_since_the_start(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        reset_write_counters()
        before = write_deadline.fired()
        with db.transaction() as conn:
            conn.execute("INSERT INTO meta VALUES ('fast','committed')")
        assert write_deadline.run_counters(before)["write_deadline_fired"] == 0
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.05)
        with pytest.raises(WriteDeadlineExceeded), db.transaction():
            time.sleep(0.1)
        counters = write_deadline.run_counters(before)
        assert set(counters) == {
            "write_deadline_fired",
            "write_seconds_max",
            "write_wait_seconds_max",
            "write_lock_timeouts",
        }
        assert counters["write_deadline_fired"] == 1
        assert counters["write_seconds_max"] >= 0.05
        assert counters["write_lock_timeouts"] == 0


def test_the_tick_record_carries_the_write_counters(tmp_path, monkeypatch):
    import json
    import shutil

    import httpx
    from helpers.loop_ticks import run_round

    from swingset.clock import FakeClock
    from swingset.state import requirements

    config, overrides = tmp_path / "config", tmp_path / "overrides"
    shutil.copytree("config", config)
    shutil.copytree("overrides", overrides)
    (config / "sources.toml").write_text("")

    def overran(database, *_args, **_kwargs):
        # One bookkeeping write runs past a short bound; the rest keep 60 s.
        with monkeypatch.context() as patch:
            patch.setattr(write_deadline, "WRITE_SECONDS", 0.05)
            with database.transaction():
                time.sleep(0.1)

    def forbidden(_request):
        raise AssertionError("no source is enabled")

    monkeypatch.setattr(requirements, "scan", overran)
    with open_database(tmp_path / "state") as db:
        # Derive accepts the inputs, then the plan tick runs the scan.
        result = run_round(
            db,
            config_dir=config,
            overrides_dir=overrides,
            clock=FakeClock(),
            loops=("derive", "plan"),
            transport=httpx.MockTransport(forbidden),
        )["plan"]
        assert result["write_deadline_fired"] == 1
        assert result["write_seconds_max"] >= 0.05
        assert result["write_wait_seconds_max"] >= 0
        assert result["write_lock_timeouts"] == 0
        assert result["stage_errors"][0]["stage"] == "requirements_scan"
        saved = json.loads(
            db.connection.execute(
                "SELECT summary_json FROM runs WHERE run_id=?", (result["run_id"],)
            ).fetchone()[0]
        )
        assert saved["write_deadline_fired"] == 1
        # The next tick starts its counters afresh.
        monkeypatch.setattr(requirements, "scan", lambda *_args, **_kwargs: 0)
        again = run_round(
            db,
            config_dir=config,
            overrides_dir=overrides,
            clock=FakeClock(),
            loops=("plan",),
            transport=httpx.MockTransport(forbidden),
        )["plan"]
        assert again["write_deadline_fired"] == 0
        assert again["write_seconds_max"] < 0.05


_WRITER = """
import sqlite3, sys, time
conn = sqlite3.connect(sys.argv[1], isolation_level=None)
conn.execute("BEGIN IMMEDIATE")
print("LOCKED", flush=True)
time.sleep(float(sys.argv[2]))
conn.commit()
"""


def _hold_sqlite_writer(path: Path, seconds: float) -> subprocess.Popen[str]:
    child = subprocess.Popen(
        [sys.executable, "-c", _WRITER, str(path / "state.sqlite"), str(seconds)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None and child.stdout.readline().strip() == "LOCKED"
    return child


def test_waiting_for_another_writer_is_counted_but_not_bounded(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 2)
        reset_write_counters()
        child = _hold_sqlite_writer(tmp_path, 3)
        with db.transaction() as conn:
            conn.execute("INSERT INTO meta VALUES ('after-wait','committed')")
        assert child.wait(timeout=10) == 0
        counters = write_counters()
        assert counters["writes"] == 1
        assert counters["write_wait_seconds_max"] >= 2.5
        assert counters["write_wait_seconds_total"] == counters["write_wait_seconds_max"]
        assert counters["write_seconds_max"] < 1
        assert counters["write_lock_timeouts"] == 0


def test_a_busy_timeout_raises_and_is_counted(tmp_path):
    open_database(tmp_path).close()
    with open_database(tmp_path, busy_timeout=0.2) as db:
        reset_write_counters()
        child = _hold_sqlite_writer(tmp_path, 0.6)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            with db.transaction() as conn:
                conn.execute("INSERT INTO meta VALUES ('never','written')")
        assert not db.connection.in_transaction
        assert write_counters()["write_lock_timeouts"] == 1
        assert write_counters()["writes"] == 0
        assert child.wait(timeout=10) == 0


def test_a_body_over_the_bound_still_raises_and_its_length_is_kept(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.05)
        reset_write_counters()
        with pytest.raises(WriteDeadlineExceeded):
            with db.transaction():
                time.sleep(0.2)
        assert write_counters()["write_seconds_max"] >= 0.05
