"""Link selection checks each link's own prerequisites, with no registry precheck.

A link waits for the dancers its read set names (plan F20), so selection no
longer rejects the whole link group on a shared dancer cohort.
"""

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
    # Each link's event unit exists but never materialized: the link waits.
    f.conn.executemany(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
        "VALUES ('project','event',?,?)",
        [(unit.unit_id, f.corpus.clock.now().isoformat()) for unit in fleet],
    )
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


def no_cohort(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("link selection read the whole dancer cohort")

    monkeypatch.setattr(derivation_readiness, "dancers_current", forbidden)


def test_stale_registry_leaves_each_link_to_its_own_readiness(registry_fixture, monkeypatch):
    f = registry_fixture
    fleet, healthy = demand(f)
    f.conn.execute("UPDATE accepted_inputs SET digest='changed' WHERE input_name='recipe/env'")
    calls = link_calls(monkeypatch)
    no_cohort(monkeypatch)

    def allowed(unit):
        return unit in fleet or unit == healthy

    assert fairness.next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed) == healthy
    assert calls["current"] >= len(fleet) and calls["ready"] == len(fleet)
    assert not f.conn.in_transaction and f.conn.execute("PRAGMA query_only").fetchone()[0] == 0


def test_current_dancers_never_make_missing_event_prerequisites_ready(
    registry_fixture, monkeypatch
):
    f = registry_fixture
    fleet, healthy = demand(f, 3)
    calls = link_calls(monkeypatch)

    def allowed(unit):
        return unit in fleet or unit == healthy

    assert fairness.next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed) == healthy
    assert calls["current"] >= len(fleet) and calls["ready"] == len(fleet)
    with derivation_query.read_snapshot(f.conn):
        assert not derivations.ready(f.conn, fleet[0])


def test_physical_unregistered_dancer_blocks_links_that_never_ran(registry_fixture, monkeypatch):
    f = registry_fixture
    _, healthy = demand(f, 0)
    f.conn.execute("DELETE FROM pending_work WHERE stage IN ('project','link')")
    f.conn.execute("DELETE FROM derivation_scopes WHERE stage='link'")
    f.conn.execute(
        "INSERT INTO canonical_scope_rows VALUES ('dancer','physical-only','dancers','[999]')"
    )
    f.conn.execute(
        "INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
        "VALUES ('project','dancer','physical-only',?)",
        (f.corpus.clock.now().isoformat(),),
    )
    calls = link_calls(monkeypatch)

    def allowed(unit):
        return unit.stage == "link" or unit == healthy

    # Link scopes are found from retained physical events even without hints.
    assert fairness.next_offline(f.conn, now=f.corpus.clock.now(), allowed=allowed) == healthy
    assert calls["current"] > 0 and calls["ready"] > 0


def test_caller_transaction_checks_each_link(registry_fixture, monkeypatch):
    f = registry_fixture
    fleet, healthy = demand(f, 2)
    f.conn.execute("UPDATE accepted_inputs SET digest='changed' WHERE input_name='recipe/env'")
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


def test_consistency_group_keeps_its_individual_prerequisite_path(registry_fixture, monkeypatch):
    f = registry_fixture
    fleet, healthy = demand(f, 2)
    calls = link_calls(monkeypatch)
    no_cohort(monkeypatch)
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


def test_stale_links_do_not_skip_healthy_parse_or_bypass_controls_and_exclusions(
    registry_fixture,
):
    f = registry_fixture
    fleet, healthy = demand(f, 2)
    f.conn.execute("UPDATE accepted_inputs SET digest='changed' WHERE input_name='recipe/env'")

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
