"""Read-only profiling bounds include Python work and never execute a worker."""

import signal
import sqlite3
from types import SimpleNamespace

import pytest

from journal.tools.runtime import profile_offline_selector as helper


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.set_authorizer(helper.read_only)
    yield conn
    conn.close()


def modules():
    return SimpleNamespace(
        current=lambda *_: False, desired=lambda *_: None, ready=lambda *_: False
    ), SimpleNamespace(dancers_current=lambda *_: False)


def test_python_loop_is_interrupted_and_profile_closes(connection):
    derivations, readiness = modules()
    original = derivations.current

    def busy():
        while True:
            pass

    report, profile = helper.measure(
        connection, busy, seconds=0.03, derivations=derivations, readiness=readiness
    )
    assert report["status"] == "python_alarm_timeout"
    assert report["elapsed_seconds"] < 1
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
    assert derivations.current is original
    assert profile.getstats()


def test_sql_loop_is_interrupted_and_connection_remains_readable(connection):
    derivations, readiness = modules()
    report, _ = helper.measure(
        connection,
        lambda: connection.execute(
            "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<1000000000) SELECT sum(n) FROM x"
        ).fetchone(),
        seconds=0.03,
        derivations=derivations,
        readiness=readiness,
    )
    assert report["status"] in {"sqlite_progress_timeout", "python_alarm_timeout"}
    assert connection.execute("SELECT 1").fetchone() == (1,)
    assert not connection.in_transaction


def test_authorizer_denies_mutation_and_attach_but_preserves_baseline_query_mode(connection):
    assert connection.execute("PRAGMA query_only").fetchone() == (0,)
    connection.execute("PRAGMA query_only=ON")
    assert connection.execute("PRAGMA query_only").fetchone() == (1,)
    connection.execute("PRAGMA query_only=OFF")
    assert connection.execute("PRAGMA query_only").fetchone() == (0,)
    for sql in [
        "CREATE TABLE forbidden(id)",
        "PRAGMA user_version=29",
        "ATTACH ':memory:' AS other",
    ]:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(sql)
    assert connection.total_changes == 0


def test_successful_selection_reports_kind_without_sql_literals(connection):
    derivations, readiness = modules()
    unit = SimpleNamespace(stage="project", unit_kind="event", unit_id="public-event-id")

    def select():
        connection.execute("SELECT 'not retained raw name'").fetchone()
        derivations.current(connection, unit)
        return unit

    report, _ = helper.measure(
        connection, select, seconds=1, derivations=derivations, readiness=readiness
    )
    assert report["status"] == "selected" and report["selected"]["kind"] == "event"
    assert report["derivation_calls"][0]["false"] == 1
    assert "not retained raw name" not in str(report)
    assert report["baseline_query_only"] == 0
