"""Shared stale dancer prerequisites stop links before per-event cohort assembly."""

import sqlite3

import pytest
from test_dancer_readiness import registry_fixture as registry_fixture
from test_h15_acceptance import source_fixture as source_fixture

from swingset.state import derivation_dependencies, derivation_query, derivations
from swingset.state.derivation_readiness import all_dancers_current
from swingset.state.work import WorkUnit, enqueue


def test_stale_shared_cohort_avoids_per_event_dependencies(registry_fixture, monkeypatch):
    f = registry_fixture
    f.conn.execute("UPDATE accepted_inputs SET digest='changed' WHERE input_name='recipe/runtime'")
    calls = []
    original = derivation_dependencies.dancer_prerequisites

    def count(conn):
        calls.append(1)
        return original(conn)

    monkeypatch.setattr(derivation_dependencies, "dancer_prerequisites", count)
    original_prerequisites = derivation_dependencies.prerequisites

    def unexpected(conn, unit):
        if unit.stage == "link":
            pytest.fail("blocked link constructed event-specific dependencies")
        return original_prerequisites(conn, unit)

    monkeypatch.setattr(derivation_dependencies, "prerequisites", unexpected)
    with derivation_query.read_snapshot(f.conn):
        for i in range(100):
            assert not derivations.ready(f.conn, WorkUnit("link", "event", str(i)))
    assert len(calls) == 1


def test_new_physical_or_queued_membership_invalidates_after_snapshot(registry_fixture):
    f = registry_fixture
    with derivation_query.read_snapshot(f.conn):
        assert all_dancers_current(f.conn)
    enqueue(
        f.conn,
        [WorkUnit("project", "dancer", "unknown")],
        enqueued_at=f.corpus.clock.now().isoformat(),
    )
    with derivation_query.read_snapshot(f.conn):
        assert not all_dancers_current(f.conn)


def test_caller_transaction_write_and_rollback_recompute(registry_fixture):
    f = registry_fixture
    with f.db.transaction(), derivation_query.read_snapshot(f.conn):
        assert all_dancers_current(f.conn)
        f.conn.execute("SAVEPOINT change")
        f.conn.execute(
            "UPDATE accepted_inputs SET digest='changed' WHERE input_name='recipe/runtime'"
        )
        assert not all_dancers_current(f.conn)
        f.conn.execute("ROLLBACK TO change")
        f.conn.execute("RELEASE change")
        assert all_dancers_current(f.conn)


def test_concurrent_input_change_visible_only_after_snapshot(registry_fixture):
    f = registry_fixture
    with sqlite3.connect(f.db.state_dir / "state.sqlite", isolation_level=None) as other:
        with derivation_query.read_snapshot(f.conn):
            assert all_dancers_current(f.conn)
            other.execute(
                "UPDATE accepted_inputs SET digest='changed' WHERE input_name='recipe/runtime'"
            )
            assert all_dancers_current(f.conn)
        with derivation_query.read_snapshot(f.conn):
            assert not all_dancers_current(f.conn)


def test_current_dancers_still_require_event_and_history(registry_fixture, monkeypatch):
    f = registry_fixture
    required = WorkUnit("project", "event", "missing-event")
    monkeypatch.setattr(derivation_dependencies, "prerequisites", lambda conn, unit: (required,))
    with derivation_query.read_snapshot(f.conn):
        assert all_dancers_current(f.conn)
        assert not derivations.ready(f.conn, WorkUnit("link", "event", "missing-event"))
