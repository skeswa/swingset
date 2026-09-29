"""A link waits for what it read, never for the whole registry (plan F20)."""

import pytest
from test_dancer_readiness import registry_fixture as registry_fixture
from test_h15_acceptance import drain_project, link_all
from test_h15_acceptance import source_fixture as source_fixture

from swingset.state import (
    derivation_dependencies,
    derivation_query,
    derivation_readiness,
    derivations,
)
from swingset.state.work import WorkUnit

LINK = WorkUnit("link", "event", "event-a")
DANCER = WorkUnit("project", "dancer", "1")


@pytest.fixture
def settled(registry_fixture):
    f = registry_fixture
    drain_project(f)
    link_all(f)
    return f


def stale_dancer(f):
    f.conn.execute(
        "UPDATE observations SET payload_json=replace(payload_json,'Diane','Changed') "
        "WHERE scope_kind='dancer' AND scope_id='1'"
    )
    assert not derivations.current(f.conn, DANCER)


def read(f, key):
    f.conn.execute(
        "INSERT OR IGNORE INTO derivation_reads VALUES ('link','event','event-a',?)", (key,)
    )


def test_a_stale_dancer_blocks_only_the_links_that_read_it(settled, monkeypatch):
    f = settled
    f.conn.execute("DELETE FROM derivation_reads WHERE read_key='project/dancer/1'")
    stale_dancer(f)

    def forbidden(*args, **kwargs):
        raise AssertionError("link readiness read the dancer cohort")

    monkeypatch.setattr(derivation_readiness, "dancers_current", forbidden)
    with derivation_query.read_snapshot(f.conn):
        assert DANCER not in derivation_dependencies.prerequisites(f.conn, LINK)
        assert derivations.ready(f.conn, LINK)
    read(f, "project/dancer/1")
    with derivation_query.read_snapshot(f.conn):
        assert DANCER in derivation_dependencies.prerequisites(f.conn, LINK)
        assert not derivations.ready(f.conn, LINK)


def test_a_link_that_never_ran_waits_for_every_dancer_to_exist(settled):
    f = settled
    never = WorkUnit("link", "event", "never-ran")
    assert derivations.selected_generation(f.conn, never) is None
    f.conn.execute(
        "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
        "VALUES ('project','dancer','2',?)",
        (f.corpus.clock.now().isoformat(),),
    )
    with derivation_query.read_snapshot(f.conn):
        # An unmaterialized dancer could be any never-run link's candidate.
        assert not derivations.ready(f.conn, never)
        # A link that ran waits only for what its read set names.
        assert derivations.ready(f.conn, LINK)


def test_a_link_still_waits_for_its_event_and_resolution_and_never_a_year(settled, monkeypatch):
    f = settled
    prerequisites = derivation_dependencies.prerequisites(f.conn, LINK)
    assert not any(unit.unit_kind == "year" for unit in prerequisites)
    required = WorkUnit("project", "event", "event-a")
    assert required in prerequisites
    monkeypatch.setattr(
        derivations,
        "current",
        lambda conn, unit, required=required, **kwargs: unit != required,
    )
    assert not derivations.ready(f.conn, LINK)
    # A year that is not current holds no link: the link reads its registry
    # association as content, and a year that moves it queues the link.
    year = WorkUnit("project", "year", "2026")
    monkeypatch.setattr(
        derivations, "current", lambda conn, unit, year=year, **kwargs: unit != year
    )
    assert derivations.ready(f.conn, LINK)


def _queue_row(f, unit, state, next_attempt):
    f.conn.execute(
        "INSERT INTO work_queue(stage,unit_kind,unit_id,partition,state,attempts,"
        "next_attempt_at,enqueued_at,updated_at) VALUES (?,?,?,?,?,3,?,?,?)",
        (
            unit.stage,
            unit.unit_kind,
            unit.unit_id,
            f"{unit.unit_kind}/{unit.unit_id}",
            state,
            next_attempt,
            "2026-01-01T00:00:00Z",
            "2026-01-01T00:00:00Z",
        ),
    )


@pytest.mark.parametrize(
    "state, next_attempt, settles",
    [
        ("quarantined", "2099-01-01T00:00:00.000000Z", "quarantined"),
        ("waiting_input", "2099-01-01T00:00:00.000000Z", "waiting_input"),
        ("ready", "2099-01-01T00:00:00.000000Z", "backing_off"),
        # Due now: it runs, so its dependents wait for it.
        ("ready", "2000-01-01T00:00:00.000000Z", None),
    ],
)
def test_a_failing_prerequisite_is_settled_at_its_pointer(
    settled, monkeypatch, state, next_attempt, settles
):
    """One quarantined or backing-off unit holds none of its dependents (I1)."""
    f = settled
    event = WorkUnit("project", "event", "event-a")
    monkeypatch.setattr(
        derivations, "current", lambda conn, unit, event=event, **kwargs: unit != event
    )
    assert not derivations.ready(f.conn, LINK)
    _queue_row(f, event, state, next_attempt)
    with derivations.counting_settled() as counts:
        assert derivations.ready(f.conn, LINK) == (settles is not None)
    assert counts == ({settles: {"project/event/event-a"}} if settles else {})
    # The derive tick names them in its summary, the operator's signal.
    from swingset.fetch.archive import Archive
    from swingset.schedule import offline

    def selection(conn, **kwargs):
        derivations.ready(conn, LINK)
        return []

    monkeypatch.setattr(offline, "next_offline_batch", selection)
    summary = {"readiness_errors": {}}
    runner = offline.Offline(
        f.db, Archive(f.db.state_dir), f.bundle, f.corpus.clock, "run", summary, lambda unit: True
    )
    assert runner.batch() == []
    assert summary.get("settled_prerequisites") == (
        {settles: ["project/event/event-a"]} if settles else None
    )


def test_a_dancer_change_holds_no_link_and_the_paced_inventory_holds_no_year(source_fixture):
    """The transitive gate the review found: a dancer change left the inventory
    stale for its 12-hour interval, so every year waited, and every link with
    them. Links no longer wait for years; a year and everything else that
    reads the inventory take the paced one at its pointer."""
    from datetime import timedelta

    from test_h15_acceptance import add_dancer

    from swingset.schedule import pacing
    from swingset.schedule.fair_policy import SchedulerConfig

    f = source_fixture
    add_dancer(f)
    drain_project(f)
    link_all(f)
    inventory = WorkUnit("project", "inventory", "all")
    years = [u for u in derivation_dependencies.scopes(f.conn, "project") if u.unit_kind == "year"]
    links = list(derivation_dependencies.scopes(f.conn, "link"))
    assert years and links
    add_dancer(f, first_name="Changed")
    from swingset.project.process import process_unit

    process_unit(f.db, WorkUnit("project", "dancer", "999"), f.bundle, f.corpus.clock, f.corpus.run)
    assert not derivations.current(f.conn, inventory)
    assert all(derivations.ready(f.conn, link) for link in links)
    # Unpaced, a year waits for the inventory; paced, it reads its pointer.
    stale = [year for year in years if not derivations.current(f.conn, year)]
    assert stale
    assert not any(derivations.ready(f.conn, year) for year in stale)
    clock = f.corpus.clock
    gate = SchedulerConfig(inventory_interval_seconds=12 * 3600)
    with pacing.configured(gate, lambda: clock.now() + timedelta(seconds=1)):
        assert pacing.holds(f.conn, inventory)
        with derivations.counting_settled() as counts:
            assert all(derivations.ready(f.conn, year) for year in stale)
        assert counts == {"paced": {"project/inventory/all"}}
