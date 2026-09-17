"""Necessary shared link prerequisites can reject a group before candidate scans."""

from collections import Counter

import pytest
from test_dancer_readiness import registry_fixture as registry_fixture
from test_h15_acceptance import source_fixture as source_fixture

from swingset.schedule import fairness
from swingset.state import derivation_query, derivation_readiness, derivations
from swingset.state.control_scopes import unit_allowed
from swingset.state.work import WorkUnit, enqueue


def demand(f, count=40):
    fleet = [WorkUnit("link", "event", f"blocked-{i}") for i in range(count)]
    healthy = WorkUnit("parse", "snapshot", "healthy")
    enqueue(f.conn, (*fleet, healthy), enqueued_at=f.corpus.clock.now().isoformat())
    f.conn.execute(
        "INSERT INTO scheduler_offline_service VALUES ('parse','snapshot',1,?,999)",
        (f.corpus.clock.now().isoformat(),),
    )
    return fleet, healthy


def link_calls(monkeypatch):
    calls = Counter()
    current, ready = derivations.current, derivations.ready

    def measured_current(conn, unit, **kwargs):
        if unit.stage == "link":
            calls["current"] += 1
        return current(conn, unit, **kwargs)

    def measured_ready(conn, unit):
        if unit.stage == "link":
            calls["ready"] += 1
        return ready(conn, unit)

    monkeypatch.setattr(derivations, "current", measured_current)
    monkeypatch.setattr(derivations, "ready", measured_ready)
    return calls


def test_stale_group_skips_candidate_currentness_and_selects_same_healthy_work(
    registry_fixture, monkeypatch
):
    f = registry_fixture
    fleet, healthy = demand(f)
    f.conn.execute("UPDATE accepted_inputs SET digest='changed' WHERE input_name='recipe/runtime'")
    calls = link_calls(monkeypatch)

    def allowed(unit):
        return unit in fleet or unit == healthy

    assert fairness.next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed) == healthy
    assert not calls
    with derivation_query.read_snapshot(f.conn):
        assert fairness._next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed) == healthy
    assert calls["current"] >= len(fleet) and calls["ready"] == len(fleet)
    assert not f.conn.in_transaction and f.conn.execute("PRAGMA query_only").fetchone()[0] == 0


def test_true_shared_proof_still_checks_each_event_currentness_and_readiness(
    registry_fixture, monkeypatch
):
    f = registry_fixture
    fleet, healthy = demand(f, 3)
    calls = link_calls(monkeypatch)

    def allowed(unit):
        return unit in fleet or unit == healthy

    assert fairness.next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed) == healthy
    assert calls["current"] >= len(fleet) and calls["ready"] == len(fleet)
    # A true dancer proof never makes missing event/history prerequisites ready.
    with derivation_query.read_snapshot(f.conn):
        assert derivation_readiness.all_dancers_current(f.conn)
        assert not derivations.ready(f.conn, fleet[0])


def test_physical_unregistered_dancer_without_queue_blocks_then_next_snapshot_reopens(
    registry_fixture, monkeypatch
):
    f = registry_fixture
    _, healthy = demand(f, 0)
    f.conn.execute("DELETE FROM pending_work WHERE stage IN ('project','link')")
    f.conn.execute("DELETE FROM derivation_scopes WHERE stage='link'")
    f.conn.execute(
        "INSERT INTO canonical_scope_rows VALUES ('dancer','physical-only','dancers','[999]')"
    )
    f.conn.execute(
        "DELETE FROM derivation_scopes WHERE stage='project' AND unit_kind='dancer' AND unit_id='physical-only'"
    )
    calls = link_calls(monkeypatch)

    def allowed(unit):
        return unit.stage == "link" or unit == healthy

    assert fairness.next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed) == healthy
    assert not calls
    f.conn.execute(
        "DELETE FROM canonical_scope_rows WHERE scope_kind='dancer' AND scope_id='physical-only'"
    )
    with derivation_query.read_snapshot(f.conn):
        assert derivation_readiness.all_dancers_current(f.conn)
    assert fairness.next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed) == healthy
    # Link scopes are found from retained physical events even without queue hints.
    assert calls["current"] > 0 and calls["ready"] > 0


def test_caller_transaction_does_not_get_early_group_precheck(registry_fixture, monkeypatch):
    f = registry_fixture
    fleet, healthy = demand(f, 2)
    f.conn.execute("UPDATE accepted_inputs SET digest='changed' WHERE input_name='recipe/runtime'")
    calls = link_calls(monkeypatch)
    with f.db.transaction():
        assert (
            fairness.next_offline(
                f.conn,
                now=f.corpus.clock.now(),
                allowed=lambda unit: unit in fleet or unit == healthy,
            )
            == healthy
        )
        assert calls["current"] >= len(fleet) and f.conn.in_transaction
        assert f.conn.execute("PRAGMA query_only").fetchone()[0] == 0


def test_consistency_group_keeps_its_existing_individual_prerequisite_path(
    registry_fixture, monkeypatch
):
    f = registry_fixture
    fleet, healthy = demand(f, 2)
    calls = link_calls(monkeypatch)

    def forbidden(*args):
        pytest.fail("active consistency group must not use shared cohort rejection")

    monkeypatch.setattr(derivation_readiness, "all_dancers_current", forbidden)
    with f.db.transaction(), derivations.group(f.conn):
        assert (
            fairness.next_offline(
                f.conn,
                now=f.corpus.clock.now(),
                allowed=lambda unit: unit in fleet or unit == healthy,
            )
            == healthy
        )
        assert calls["current"] >= len(fleet) and calls["ready"] == len(fleet)


def test_shared_false_does_not_skip_healthy_parse_or_bypass_controls_and_exclusions(
    registry_fixture,
):
    f = registry_fixture
    fleet, healthy = demand(f, 2)
    f.conn.execute("UPDATE accepted_inputs SET digest='changed' WHERE input_name='recipe/runtime'")

    def allowed(unit):
        return (unit in fleet or unit == healthy) and unit_allowed(
            f.conn, unit, now=f.corpus.clock.now()
        )

    assert fairness.next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed) == healthy
    assert (
        fairness.next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed, exclude=(healthy,))
        is None
    )
    f.conn.execute(
        "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES ('all','all','new pause')"
    )
    assert fairness.next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed) is None
