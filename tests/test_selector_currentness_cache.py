"""Selection shares exact prerequisite answers without extending worker authority."""

import sqlite3
from collections import Counter

import pytest
from test_h15_acceptance import source_fixture as source_fixture

from swingset.clock import FakeClock
from swingset.schedule.fairness import next_offline
from swingset.state import derivation_query, derivations
from swingset.state.control_scopes import unit_allowed
from swingset.state.controls import ActionScope, ControlPaused, admission
from swingset.state.db import open_database
from swingset.state.work import WorkUnit, enqueue


def test_blocked_event_fleet_checks_shared_map_once_and_selects_same_healthy_work(
    source_fixture, monkeypatch
):
    f = source_fixture
    shared = WorkUnit("project", "map", "all")
    with f.db.transaction():
        selection = derivations.capture(f.conn, shared, now=f.corpus.clock.now())
        derivations.complete(
            f.conn, selection, rows=(), now=f.corpus.clock.now(), run_id=f.corpus.run
        )
    assert derivations.current(f.conn, shared)
    f.conn.execute(
        "UPDATE accepted_inputs SET digest='new reviewed recipe' WHERE input_name='recipe/runtime'"
    )
    fleet = [WorkUnit("project", "event", f"blocked-{i}") for i in range(40)]
    healthy = WorkUnit("parse", "snapshot", "healthy")
    enqueue(f.conn, (*fleet, healthy), enqueued_at=f.corpus.clock.now().isoformat())
    f.conn.execute(
        "INSERT INTO scheduler_offline_service VALUES ('parse','snapshot',1,?,999)",
        (f.corpus.clock.now().isoformat(),),
    )
    calls = Counter()
    desired = derivations.desired

    def measured(conn, unit, **kwargs):
        calls[unit] += 1
        return desired(conn, unit, **kwargs)

    monkeypatch.setattr(derivations, "desired", measured)

    def allowed(unit):
        return unit in fleet or unit == healthy

    optimized = next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed)
    assert optimized == healthy and calls[shared] == 1
    calls.clear()
    monkeypatch.setattr(derivation_query, "currentness", lambda conn, key, compute: compute())
    reference = next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed)
    assert reference == optimized and calls[shared] >= len(fleet)
    assert not f.conn.in_transaction and f.conn.execute("PRAGMA query_only").fetchone()[0] == 0


def test_currentness_lru_eviction_and_failed_compute_do_not_invent_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(derivation_query, "CURRENTNESS_LIMIT", 2)
    with open_database(tmp_path) as db:
        calls = Counter()

        def compute(key):
            calls[key] += 1
            return bool(key % 2)

        with derivation_query.read_snapshot(db.connection):
            for key in (1, 2, 1, 3, 2):
                assert derivation_query.currentness(
                    db.connection, key, lambda key=key: compute(key)
                ) == bool(key % 2)
            assert calls == {1: 1, 2: 2, 3: 1}

            def failed():
                raise ValueError("not cached")

            with pytest.raises(ValueError, match="not cached"):
                derivation_query.currentness(db.connection, "failed", failed)
            assert derivation_query.currentness(db.connection, "failed", lambda: True)
        with derivation_query.read_snapshot(db.connection):
            assert derivation_query.currentness(db.connection, 1, lambda: compute(1))
        assert calls[1] == 2


def test_mutable_caller_and_savepoint_rollback_never_reuse_currentness(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute("INSERT INTO meta VALUES ('selector-test','old')")

        def value():
            return (
                conn.execute("SELECT value FROM meta WHERE key='selector-test'").fetchone()[0]
                == "old"
            )

        with db.transaction():
            with derivation_query.read_snapshot(conn):
                assert derivation_query.currentness(conn, "key", value)
                conn.execute("SAVEPOINT temporary")
                conn.execute("UPDATE meta SET value='dirty' WHERE key='selector-test'")
                assert not derivation_query.currentness(conn, "key", value)
                conn.execute("ROLLBACK TO temporary")
                conn.execute("RELEASE temporary")
                assert derivation_query.currentness(conn, "key", value)
            assert conn.in_transaction and conn.execute("PRAGMA query_only").fetchone()[0] == 0


def test_other_connection_change_is_stable_within_snapshot_and_fresh_afterward(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute("INSERT INTO meta VALUES ('selector-test','old')")
        with sqlite3.connect(tmp_path / "state.sqlite", isolation_level=None) as other:

            def value():
                return (
                    conn.execute("SELECT value FROM meta WHERE key='selector-test'").fetchone()[0]
                    == "old"
                )

            with derivation_query.read_snapshot(conn):
                assert derivation_query.currentness(conn, "key", value)
                other.execute("UPDATE meta SET value='new' WHERE key='selector-test'")
                assert derivation_query.currentness(conn, "key", value)
            with derivation_query.read_snapshot(conn):
                assert not derivation_query.currentness(conn, "key", value)


@pytest.mark.parametrize("initial_read_only", [0, 1])
def test_callback_write_is_rejected_and_exception_restores_transaction_and_pragma(
    tmp_path, initial_read_only
):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        conn = db.connection
        enqueue(conn, [WorkUnit("parse", "snapshot", "one")], enqueued_at=clock.now().isoformat())
        conn.execute(f"PRAGMA query_only={initial_read_only}")

        def forbidden(unit):
            conn.execute("INSERT INTO meta VALUES ('callback-write','forbidden')")
            return True

        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            next_offline(conn, now=clock.now(), allowed=forbidden)
        assert not conn.in_transaction
        assert conn.execute("PRAGMA query_only").fetchone()[0] == initial_read_only
        assert conn.execute("SELECT 1 FROM meta WHERE key='callback-write'").fetchone() is None
        conn.execute("PRAGMA query_only=OFF")


def test_control_change_during_snapshot_is_rechecked_by_admission(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        conn = db.connection
        unit = WorkUnit("parse", "snapshot", "one")
        enqueue(conn, [unit], enqueued_at=clock.now().isoformat())
        with sqlite3.connect(tmp_path / "state.sqlite", isolation_level=None) as other:

            def allowed(candidate):
                result = unit_allowed(conn, candidate, now=clock.now())
                other.execute(
                    "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES ('all','all','new pause')"
                )
                return result

            assert next_offline(conn, now=clock.now(), allowed=allowed) == unit
        with pytest.raises(ControlPaused):
            with admission(
                db,
                action_id="must-stop",
                action_kind="parse",
                scope=ActionScope(all_sources=True, all_kinds=True),
                now=clock.now(),
            ):
                pytest.fail("new pause must stop admission")
        assert (
            next_offline(
                conn,
                now=clock.now(),
                allowed=lambda candidate: unit_allowed(conn, candidate, now=clock.now()),
            )
            is None
        )


def test_build_filesystem_currentness_is_never_memoized(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        values = iter([True, False])
        monkeypatch.setattr(derivations, "_current", lambda *args, **kwargs: next(values))
        with derivation_query.read_snapshot(db.connection):
            unit = WorkUnit("build", "release", "all")
            assert derivations.current(db.connection, unit)
            assert not derivations.current(db.connection, unit)


def test_context_is_part_of_the_currentness_key(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        calls = []

        def check(conn, unit, *, context=None):
            calls.append(context)
            return not bool(context)

        monkeypatch.setattr(derivations, "_current", check)
        unit = WorkUnit("project", "map", "all")
        with derivation_query.read_snapshot(db.connection):
            assert derivations.current(db.connection, unit)
            assert not derivations.current(db.connection, unit, context={"cutoff": "other"})
            assert derivations.current(db.connection, unit)
        assert len(calls) == 2


def test_no_currentness_answer_survives_same_connection_committed_input_change(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute("INSERT INTO meta VALUES ('selector-test','old')")

        def value():
            return (
                conn.execute("SELECT value FROM meta WHERE key='selector-test'").fetchone()[0]
                == "old"
            )

        with derivation_query.read_snapshot(conn):
            assert derivation_query.currentness(conn, "key", value)
        conn.execute("UPDATE meta SET value='changed' WHERE key='selector-test'")
        with derivation_query.read_snapshot(conn):
            assert not derivation_query.currentness(conn, "key", value)
