"""A slow atomic worker releases Postgres without selecting partial output."""

import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from swingset.state import engine, write_deadline
from swingset.state.db import open_database, reset_write_counters, write_counters
from swingset.state.write_deadline import WriteDeadlineExceeded


def test_python_work_rolls_back_and_next_transaction_can_commit(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.03)
        with pytest.raises(WriteDeadlineExceeded):
            with db.transaction() as conn:
                conn.execute("INSERT INTO meta (key, value) VALUES ('partial', 'forbidden')")
                while True:
                    pass
        assert not db.connection.in_transaction
        assert db.connection.execute("SELECT 1 FROM meta WHERE key = 'partial'").fetchone() is None
        with db.transaction() as conn:
            conn.execute("INSERT INTO meta (key, value) VALUES ('independent', 'committed')")
        assert (
            db.connection.execute("SELECT value FROM meta WHERE key = 'independent'").fetchone()[0]
            == "committed"
        )


def test_expired_closed_session_never_claims_confirmed_rollback(tmp_path):
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    assert previous_timer == (0, 0)
    with open_database(tmp_path) as db:
        conn = db.connection
        before = write_deadline.fired()
        with pytest.raises(
            engine.ConnectionLost, match="rollback could not be confirmed"
        ) as caught:
            with db.transaction():
                conn.execute("INSERT INTO meta(key,value) VALUES('closed_partial','forbidden')")
                conn.close()
                # Exercise the real owned alarm after the actual PostgreSQL
                # session has closed, without changing the 75 second policy.
                signal.raise_signal(signal.SIGALRM)
        assert not isinstance(caught.value, WriteDeadlineExceeded)
        assert conn.closed and conn._write_deadline is None
        assert write_deadline.fired() == before + 1
        assert signal.getsignal(signal.SIGALRM) is previous_handler
        assert signal.getitimer(signal.ITIMER_REAL) == previous_timer
    # An explicit independent session verifies the data; the failed operation
    # never reconnects or retries and makes no rollback-acknowledgement claim.
    with open_database(tmp_path) as reopened:
        assert (
            reopened.connection.execute("SELECT 1 FROM meta WHERE key='closed_partial'").fetchone()
            is None
        )


def test_nested_sql_work_inherits_deadline_and_rolls_back(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.03)
        with pytest.raises(WriteDeadlineExceeded):
            with db.transaction() as conn:
                conn.execute("INSERT INTO meta (key, value) VALUES ('partial', 'forbidden')")
                with db.transaction():
                    conn.execute(
                        "WITH RECURSIVE counter(n) AS (SELECT * FROM (VALUES (1)) AS _values UNION ALL SELECT n + 1 "
                        "FROM counter WHERE n < 100000000) SELECT SUM(n) FROM counter"
                    ).fetchone()
        assert not db.connection.in_transaction
        assert db.connection.execute("SELECT 1 FROM meta WHERE key = 'partial'").fetchone() is None


def test_deadline_delivered_while_arming_rolls_back_and_restores_the_boundary(
    tmp_path, monkeypatch
):
    with open_database(tmp_path) as db:
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.03)
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        assert previous_timer == (0, 0)
        real_setitimer = signal.setitimer
        armed = False

        def deliver_while_arming(which, seconds, interval=0):
            nonlocal armed
            if seconds and not armed:
                armed = True
                signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
            return real_setitimer(which, seconds, interval)

        monkeypatch.setattr(signal, "setitimer", deliver_while_arming)
        before = write_deadline.fired()
        with pytest.raises(WriteDeadlineExceeded):
            with db.transaction():
                pytest.fail("the deadline fired before the body was entered")
        assert armed
        assert write_deadline.fired() == before + 1
        assert signal.getsignal(signal.SIGALRM) == previous_handler
        assert signal.getitimer(signal.ITIMER_REAL) == previous_timer
        assert db.connection._write_deadline is None
        assert not db.connection.in_transaction
        assert db.connection.execute("SELECT 1").fetchone()[0] == 1


@pytest.mark.parametrize(
    "body,disarm,expected",
    [
        ("server_timeout", 1, WriteDeadlineExceeded),
        ("success", 2, WriteDeadlineExceeded),
        ("uncertain", 1, engine.CommitUncertain),
        ("acknowledged", 1, engine.CommitUncertain),
        ("interrupt", 1, KeyboardInterrupt),
        ("interrupt", None, KeyboardInterrupt),
        ("system_exit", 1, SystemExit),
        ("system_exit", None, SystemExit),
        ("lost", 1, engine.ConnectionLost),
        ("lost", None, engine.ConnectionLost),
    ],
)
def test_owned_alarm_cleanup_preserves_translation_and_original_failures(
    monkeypatch, body, disarm, expected
):
    from types import SimpleNamespace

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    assert previous_timer == (0, 0)
    real_setitimer = signal.setitimer
    closed, delivered, armed = [], [], []
    connection = SimpleNamespace(
        _write_deadline=None,
        _commit_serial=0,
        closed=body in {"uncertain", "interrupt", "system_exit", "lost"},
        close=lambda: closed.append(True),
    )
    error = (
        engine.CommitUncertain("original uncertain acknowledgement")
        if body == "uncertain"
        else KeyboardInterrupt("original user interrupt")
        if body == "interrupt"
        else SystemExit("original user exit")
        if body == "system_exit"
        else engine.ConnectionLost("original session loss")
        if body == "lost"
        else engine.WriteTimeout("original server timeout")
    )
    cancelled = 0

    def timer(which, seconds, interval=0):
        nonlocal cancelled
        if seconds:
            # Exercise the exact registered handler deterministically. The
            # existing SQL/Python cases retain real 30 ms OS timers.
            armed.append(seconds)
            return previous_timer
        cancelled += 1
        if cancelled == disarm:
            delivered.append(True)
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
        return real_setitimer(which, seconds, interval)

    monkeypatch.setattr(signal, "setitimer", timer)
    monkeypatch.setattr(write_deadline, "time", SimpleNamespace(monotonic=lambda: 1000.0))
    before = write_deadline.fired()
    with pytest.raises(expected) as raised:
        with write_deadline.bounded_write(connection, seconds=0.03):
            assert connection._write_deadline == 1000.03
            if body == "acknowledged":
                connection._commit_serial += 1
            if body != "success":
                raise error
    assert armed == [0.03]
    assert delivered == ([] if disarm is None else [True])
    assert write_deadline.fired() == before + (disarm is not None)
    if body in {"uncertain", "interrupt", "system_exit", "lost"}:
        assert raised.value is error
    elif body != "success":
        assert raised.value.__cause__ is error
    assert closed == ([True] if body == "acknowledged" else [])
    assert connection._write_deadline is None
    assert signal.getsignal(signal.SIGALRM) is previous_handler
    assert signal.getitimer(signal.ITIMER_REAL) == previous_timer


@pytest.mark.parametrize(
    "failure", [engine.CommitUncertain, engine.ConnectionLost, KeyboardInterrupt, SystemExit]
)
def test_owned_alarm_at_exception_delivery_keeps_the_original_fatal_error(monkeypatch, failure):
    from types import SimpleNamespace

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    previous_trace = sys.gettrace()
    assert previous_timer == (0, 0)
    actual_setitimer = signal.setitimer
    monkeypatch.setattr(
        signal,
        "setitimer",
        lambda which, seconds, interval=0: (
            previous_timer if seconds else actual_setitimer(which, seconds, interval)
        ),
    )
    monkeypatch.setattr(write_deadline, "time", SimpleNamespace(monotonic=lambda: 1000.0))
    connection = SimpleNamespace(
        _write_deadline=None, _commit_serial=0, closed=True, close=lambda: None
    )
    original = failure("original failure at generator boundary")
    delivered = []
    code = write_deadline.bounded_write.__wrapped__.__code__

    def on_exception(frame, event, value):
        if frame.f_code is code and event == "exception" and value[1] is original and not delivered:
            delivered.append(True)
            signal.getsignal(signal.SIGALRM)(signal.SIGALRM, frame)
        return on_exception

    before = write_deadline.fired()
    try:
        sys.settrace(on_exception)
        with pytest.raises(failure) as caught:
            with write_deadline.bounded_write(connection, seconds=0.03):
                raise original
    finally:
        sys.settrace(previous_trace)
    assert delivered == [True] and caught.value is original
    assert write_deadline.fired() == before + 1
    assert connection._write_deadline is None
    assert signal.getsignal(signal.SIGALRM) is previous_handler
    assert signal.getitimer(signal.ITIMER_REAL) == previous_timer


def test_long_read_snapshot_does_not_take_worker_write_budget(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.01)
        with db.transaction(immediate=False) as conn:
            conn.execute("SELECT COUNT(*) FROM meta").fetchone()
            time.sleep(0.03)


def test_read_snapshot_cannot_upgrade_through_nested_write(tmp_path):
    with open_database(tmp_path) as db:
        with pytest.raises(RuntimeError, match="cannot upgrade"):
            with db.transaction(immediate=False):
                with db.transaction() as conn:
                    conn.execute("INSERT INTO meta (key, value) VALUES ('unbounded', 'forbidden')")
        assert not db.connection.in_transaction
        with db.transaction() as conn:
            conn.execute("INSERT INTO meta (key, value) VALUES ('bounded', 'allowed')")


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
        db.connection.execute(
            "INSERT INTO meta (key, value) VALUES (%s, 'selected')", (unit.unit_id,)
        )
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
        db.connection.execute("DELETE FROM pending_work WHERE unit_id = %s", (unit.unit_id,))

    monkeypatch.setattr(swingset.project, "process_unit", project)
    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        enqueue(db.connection, [slow, healthy], enqueued_at=clock.now().isoformat())
        # Only the slow operation receives the injected deadline.
        with monkeypatch.context() as forced_timeout:
            forced_timeout.setattr(write_deadline, "WRITE_SECONDS", 0.03)
            outcome = derive_one(db, Archive(tmp_path), slow, bundle, clock, run)
        assert outcome.failed and outcome.reason == "write_deadline_exceeded"
        assert not derive_one(db, Archive(tmp_path), healthy, bundle, clock, run).failed
        assert db.connection.execute("SELECT 1 FROM meta WHERE key = 'slow'").fetchone() is None
        assert (
            db.connection.execute("SELECT value FROM meta WHERE key = 'healthy'").fetchone()[0]
            == "selected"
        )
        assert db.connection.execute("SELECT unit_id FROM pending_work").fetchone()[0] == "slow"
        assert next_work(db.connection, "project", now=clock.now()) is None
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM execution_admissions WHERE state <> 'settled'"
            ).fetchone()[0]
            == 0
        )
    with open_database(tmp_path) as db:
        assert next_work(db.connection, "project", now=clock.now()) is None
        assert tuple(
            db.connection.execute(
                "SELECT outcome, reason_code, retry_at FROM work_attempts WHERE unit_id = 'slow'"
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
            "SELECT outcome, retry_at FROM work_attempts WHERE unit_id = 'slow' ORDER BY attempt_id DESC NULLS LAST"
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
                "SELECT outcome, retry_at FROM work_attempts ORDER BY attempt_id DESC NULLS LAST"
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


def test_no_write_gets_a_longer_bound(tmp_path, monkeypatch, request):
    """The 75 s alarm is the one limit; the history allowance is gone (D-0217)."""
    from swingset.schedule import derive
    from swingset.state.write_deadline import bounded_write

    assert write_deadline.WRITE_SECONDS == 75.0
    assert not hasattr(write_deadline, "allowance")
    assert not hasattr(derive, "HISTORY_WRITE_SECONDS")
    monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.05)
    database = open_database(tmp_path)
    conn = database.connection
    request.addfinalizer(database.close)
    with pytest.raises(WriteDeadlineExceeded), bounded_write(conn):
        time.sleep(0.1)


def test_a_year_unit_over_the_bound_fails_like_any_other(tmp_path, monkeypatch):
    import signal

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
        # Deliver the real deadline signal only after admission. A tiny wall
        # timer can interrupt libpq setup first under parallel load, closing the
        # connection instead of exercising the year body's deadline handling.
        assert db.connection._write_deadline is not None
        signal.raise_signal(signal.SIGALRM)
        pytest.fail("the write deadline did not interrupt the year body")

    monkeypatch.setattr(swingset.project, "process_unit", project)
    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        enqueue(db.connection, [history], enqueued_at=clock.now().isoformat())
        before = write_deadline.fired()
        outcome = derive_one(db, Archive(tmp_path), history, bundle, clock, run)
        assert outcome.failed and outcome.reason == "write_deadline_exceeded"
        assert write_deadline.fired() == before + 1


def test_admission_write_deadline_rolls_back_and_keeps_its_retry_class(tmp_path, monkeypatch):
    import swingset.project
    from swingset.clock import FakeClock
    from swingset.config import Config
    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one
    from swingset.state import derivations, queue
    from swingset.state.inputs import InputBundle
    from swingset.state.work import WorkUnit, enqueue, next_work

    clock = FakeClock()
    unit = WorkUnit("project", "year", "2026")
    bundle = InputBundle("test", tmp_path, {}, Config({}, {}))
    entered = []

    def capture(conn, *_args, **_kwargs):
        entered.append(True)
        conn.execute("INSERT INTO meta(key,value) VALUES('admission_partial','forbidden')")
        # Fail this admission body without also shortening the later retry
        # bookkeeping transaction. Other cases exercise real short OS timers.
        assert conn._write_deadline is not None
        signal.raise_signal(signal.SIGALRM)
        pytest.fail("the write deadline did not interrupt admission")

    monkeypatch.setattr(derivations, "capture", capture)
    monkeypatch.setattr(
        swingset.project,
        "process_unit",
        lambda *_args, **_kwargs: pytest.fail("unit was not admitted"),
    )
    with open_database(tmp_path) as database:
        run = database.start_run(clock.now())
        enqueue(database.connection, [unit], enqueued_at=clock.now().isoformat())
        before = write_deadline.fired()
        result = derive_one(database, Archive(tmp_path), unit, bundle, clock, run)
        assert entered == [True]
        assert result.failed and result.reason == "write_deadline_exceeded"
        assert write_deadline.fired() == before + 1
        assert (
            database.connection.execute(
                "SELECT 1 FROM meta WHERE key='admission_partial'"
            ).fetchone()
            is None
        )
        assert database.connection.execute("SELECT count(*) FROM work_attempts").fetchone()[0] == 0
        held = queue.get(database.connection, unit)
        assert held is not None
        assert held.error_class == "write_deadline_exceeded"
        assert held.next_attempt_at == queue.at(clock.now() + timedelta(minutes=15))
        assert next_work(database.connection, "project", now=clock.now()) is None


class _Monotonic:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def test_a_body_can_pass_sixty_seconds_but_expires_at_seventy_five(tmp_path, monkeypatch, request):
    from swingset.state.write_deadline import bounded_write

    clock = _Monotonic()
    monkeypatch.setattr(write_deadline, "time", clock)
    database = open_database(tmp_path)
    conn = database.connection
    request.addfinalizer(database.close)
    before = write_deadline.fired()
    with bounded_write(conn):
        clock.now += 60.6
    assert write_deadline.fired() == before
    with bounded_write(conn):
        clock.now += 74.9
    assert write_deadline.fired() == before
    with pytest.raises(WriteDeadlineExceeded, match="exceeded 75 seconds"), bounded_write(conn):
        clock.now += 75
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
            conn.execute("INSERT INTO meta (key, value) VALUES ('fast', 'committed')")
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
        # One bookkeeping write runs past a short bound; the rest keep 75 s.
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
                "SELECT summary_json FROM runs WHERE run_id = %s", (result["run_id"],)
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
import sys,time
from swingset.state.db import open_database
with open_database(sys.argv[1],lock=False) as database:
    with database.transaction():
        print("LOCKED",flush=True)
        time.sleep(float(sys.argv[2]))
"""


def _hold_postgres_writer(path: Path, seconds: float) -> subprocess.Popen[str]:
    child = subprocess.Popen(
        [sys.executable, "-c", _WRITER, str(path), str(seconds)],
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
        child = _hold_postgres_writer(tmp_path, 3)
        with db.transaction() as conn:
            conn.execute("INSERT INTO meta (key, value) VALUES ('after-wait', 'committed')")
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
        child = _hold_postgres_writer(tmp_path, 0.6)
        with pytest.raises(engine.OperationalError, match="lock deadline"):
            with db.transaction() as conn:
                conn.execute("INSERT INTO meta (key, value) VALUES ('never', 'written')")
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


@pytest.mark.parametrize("close_before_expiry", [False, True])
def test_expiry_after_acknowledged_commit_never_claims_rollback(
    tmp_path, monkeypatch, close_before_expiry
):
    from swingset.state import engine
    from swingset.state.write_deadline import bounded_write

    monkeypatch.setattr(write_deadline, "WRITE_SECONDS", 0.05)
    with open_database(tmp_path) as database:
        conn = database.connection
        with pytest.raises(engine.CommitUncertain), bounded_write(conn):
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('acknowledged-before-expiry','retained')"
            )
            conn.commit()
            if close_before_expiry:
                conn.close()
            time.sleep(0.1)
        assert conn.closed
    with open_database(tmp_path) as database:
        assert (
            database.connection.execute(
                "SELECT value FROM meta WHERE key='acknowledged-before-expiry'"
            ).fetchone()[0]
            == "retained"
        )
