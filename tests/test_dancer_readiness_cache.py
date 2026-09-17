"""Whole-cohort readiness reuse is confined to owned selector read snapshots."""

import sqlite3
from contextlib import contextmanager

import pytest
from test_dancer_readiness import registry_fixture as registry_fixture
from test_h15_acceptance import source_fixture as source_fixture

from swingset.schedule.fairness import next_offline
from swingset.state import derivation_query, derivations
from swingset.state.derivation_readiness import dancers_current
from swingset.state.work import WorkUnit, enqueue

DANCER = WorkUnit("project", "dancer", "1")


@contextmanager
def bulk_reads(conn):
    statements = []
    conn.set_trace_callback(
        lambda sql: (
            statements.append(sql)
            if "SELECT s.unit_id,s.materialized_generation_id" in sql
            else None
        )
    )
    try:
        yield statements
    finally:
        conn.set_trace_callback(None)


def test_repeated_link_fleet_preserves_selected_work_and_reads_cohort_once(
    registry_fixture, monkeypatch
):
    f = registry_fixture
    fleet = [WorkUnit("link", "event", f"blocked-{i}") for i in range(40)]
    healthy = WorkUnit("parse", "snapshot", "healthy")
    enqueue(f.conn, (*fleet, healthy), enqueued_at=f.corpus.clock.now().isoformat())
    f.conn.execute(
        "INSERT INTO scheduler_offline_service VALUES ('parse','snapshot',1,?,999)",
        (f.corpus.clock.now().isoformat(),),
    )

    def allowed(unit):
        return unit in fleet or unit == healthy

    with bulk_reads(f.conn) as optimized_reads:
        optimized = next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed)
    assert optimized == healthy and len(optimized_reads) == 1
    monkeypatch.setattr(derivation_query, "currentness", lambda conn, key, compute: compute())
    with bulk_reads(f.conn) as reference_reads:
        reference = next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed)
    assert reference == optimized and len(reference_reads) >= len(fleet)
    assert not f.conn.in_transaction and f.conn.execute("PRAGMA query_only").fetchone()[0] == 0


def test_changed_cohort_and_input_get_independent_answers(registry_fixture):
    f = registry_fixture
    unknown = WorkUnit("project", "dancer", "missing-physical-scope")
    with derivation_query.read_snapshot(f.conn), bulk_reads(f.conn) as reads:
        assert dancers_current(f.conn, [DANCER], current=derivations.current)
        assert dancers_current(f.conn, (DANCER,), current=derivations.current)
        assert not dancers_current(f.conn, (DANCER, unknown), current=derivations.current)
        assert not dancers_current(f.conn, (DANCER, unknown), current=derivations.current)
        assert len(reads) == 2
    f.conn.execute(
        "UPDATE accepted_inputs SET digest='changed-runtime' WHERE input_name='recipe/runtime'"
    )
    with derivation_query.read_snapshot(f.conn):
        assert not dancers_current(f.conn, (DANCER,), current=derivations.current)


def test_mutable_transaction_and_savepoint_rollback_recompute(registry_fixture):
    f = registry_fixture
    with f.db.transaction(), derivation_query.read_snapshot(f.conn), bulk_reads(f.conn) as reads:
        assert dancers_current(f.conn, (DANCER,), current=derivations.current)
        f.conn.execute("SAVEPOINT changed")
        f.conn.execute(
            "UPDATE observations SET payload_json=replace(payload_json,'Diane','Changed') WHERE scope_kind='dancer'"
        )
        assert not dancers_current(f.conn, (DANCER,), current=derivations.current)
        f.conn.execute("ROLLBACK TO changed")
        f.conn.execute("RELEASE changed")
        assert dancers_current(f.conn, (DANCER,), current=derivations.current)
        assert len(reads) == 3 and f.conn.in_transaction


def test_other_connection_changes_are_fresh_after_snapshot(registry_fixture):
    f = registry_fixture
    with sqlite3.connect(f.db.state_dir / "state.sqlite", isolation_level=None) as other:
        with derivation_query.read_snapshot(f.conn), bulk_reads(f.conn) as reads:
            assert dancers_current(f.conn, (DANCER,), current=derivations.current)
            other.execute(
                "UPDATE observations SET payload_json=replace(payload_json,'Diane','Concurrent') WHERE scope_kind='dancer'"
            )
            assert dancers_current(f.conn, (DANCER,), current=derivations.current)
            assert len(reads) == 1
        with derivation_query.read_snapshot(f.conn):
            assert not dancers_current(f.conn, (DANCER,), current=derivations.current)


def test_read_only_caller_transaction_is_not_an_owned_cache(registry_fixture):
    f = registry_fixture
    f.conn.execute("PRAGMA query_only=ON")
    f.conn.execute("BEGIN")
    try:
        with derivation_query.read_snapshot(f.conn), bulk_reads(f.conn) as reads:
            assert dancers_current(f.conn, (DANCER,), current=derivations.current)
            assert dancers_current(f.conn, (DANCER,), current=derivations.current)
            assert len(reads) == 2
        assert f.conn.in_transaction
    finally:
        f.conn.rollback()
        f.conn.execute("PRAGMA query_only=OFF")


def test_custom_callback_with_mutable_non_database_state_is_not_cached(registry_fixture):
    f = registry_fixture
    unit = WorkUnit("project", "dancer", "unregistered")

    class MutableCurrent:
        __hash__ = None
        value = True

        def __call__(self, conn, item):
            return self.value

    custom = MutableCurrent()
    with derivation_query.read_snapshot(f.conn):
        assert dancers_current(f.conn, (unit,), current=custom)
        custom.value = False
        assert not dancers_current(f.conn, (unit,), current=custom)
        assert not dancers_current(f.conn, (unit,), current=derivations.current)


def test_failed_bulk_compute_is_not_memoized(registry_fixture, monkeypatch):
    from swingset.state import derivation_readiness

    f = registry_fixture
    original = derivation_readiness._dancers_current

    def failed(*args, **kwargs):
        raise ValueError("transient proof failure")

    with derivation_query.read_snapshot(f.conn):
        monkeypatch.setattr(derivation_readiness, "_dancers_current", failed)
        with pytest.raises(ValueError, match="transient"):
            dancers_current(f.conn, (DANCER,), current=derivations.current)
        monkeypatch.setattr(derivation_readiness, "_dancers_current", original)
        assert dancers_current(f.conn, (DANCER,), current=derivations.current)


def test_cohort_answer_eviction_recomputes(registry_fixture, monkeypatch):
    f = registry_fixture
    monkeypatch.setattr(derivation_query, "CURRENTNESS_LIMIT", 2)
    with derivation_query.read_snapshot(f.conn), bulk_reads(f.conn) as reads:
        assert dancers_current(f.conn, (DANCER,), current=derivations.current)
        assert derivation_query.currentness(f.conn, "other-1", lambda: True)
        assert derivation_query.currentness(f.conn, "other-2", lambda: True)
        assert dancers_current(f.conn, (DANCER,), current=derivations.current)
        assert len(reads) == 2
