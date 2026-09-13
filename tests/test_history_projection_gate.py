"""Only proven inventory-neutral result projections may coexist with year gates."""

from types import SimpleNamespace

import pytest

from swingset.clock import FakeClock
from swingset.model.canonical import Event
from swingset.project.history import accept_year, pending_inventory_projection, phase_two_allowed
from swingset.project.process import process_unit
from swingset.project.writer import Projection, replace_scope
from swingset.state.db import open_database
from swingset.state.work import WorkUnit, enqueue


def retain_listing(state, *, scope_kind="event", scope_id="held"):
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.base import WatchSpec

    spec = WatchSpec(
        "",
        "eepro",
        "autoindex",
        "GET",
        "https://eepro.com/results/held/",
        "eepro.autoindex",
        source_ref="eepro:held",
    )
    upsert_watch(state.conn, spec, state.clock.now())
    state.conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES ('index',?,'GET',?,?,200,0,1,?,'Ok')",
        (spec.watch_id, spec.url, state.clock.now().isoformat(), state.run),
    )
    state.conn.execute(
        "INSERT INTO observations VALUES ('listing',?,'index','event_sheet',?,?,0,'1','1',?)",
        (
            spec.watch_id,
            scope_kind,
            scope_id,
            '{"kind":"event_sheet","source_event_ref":"eepro:held","name_raw":null,"round_links":[]}',
        ),
    )


def drain(state):
    from swingset.state import derivations
    from swingset.state.work import next_work

    for _ in range(20):
        unit = next_work(state.conn, "project", now=state.clock.now())
        if unit is None:
            break
        process_unit(state.db, unit, SimpleNamespace(files={}), state.clock, state.run)
    assert list(derivations.pending_units(state.conn, "project")) == []


@pytest.fixture
def state(tmp_path):
    with open_database(tmp_path) as db:
        clock = FakeClock()
        run = db.start_run(clock.now())
        event = Event(
            event_id="held",
            series_id="series",
            name="Held",
            year=2019,
            start_date="2019-01-01",
            end_date="2019-01-02",
            source="wsdc_registry",
            snapshot_id="retained",
            parser_version="1",
            first_seen_at=clock.now().isoformat(),
            last_seen_at=clock.now().isoformat(),
            run_id=run,
        )
        replace_scope(
            db.connection,
            scope_kind="fixture_inventory",
            scope_id="fixture",
            projection=Projection((event,)),
            run_id=run,
            projected_at=clock.now().isoformat(),
        )
        from swingset.state import derivations
        from swingset.state.work import next_work

        for _ in range(20):
            unit = next_work(db.connection, "project", now=clock.now())
            if unit is None:
                break
            process_unit(db, unit, SimpleNamespace(files={}), clock, run)
        assert list(derivations.pending_units(db.connection, "project")) == []
        db.connection.execute("DELETE FROM pending_work")
        db.connection.executemany(
            "INSERT INTO meta VALUES (?,'true')", [("h7_deployed",), ("h10_deployed",)]
        )
        accept_year(
            db.connection, 2019, accepted_by="offline owner", accepted_at=clock.now().isoformat()
        )
        state = SimpleNamespace(db=db, conn=db.connection, clock=clock, run=run, event=event)
        drain(state)
        yield state


def test_real_result_dispatch_is_inventory_neutral_but_followup_history_still_blocks(state):
    retain_listing(state)
    unit = WorkUnit("project", "event", "held")
    enqueue(state.conn, (unit,), enqueued_at=state.clock.now().isoformat())
    assert not pending_inventory_projection(state.conn)
    assert phase_two_allowed(state.conn, 2019)
    accept_year(
        state.conn, 2019, accepted_by="offline owner", accepted_at=state.clock.now().isoformat()
    )
    before = tuple(state.conn.execute("SELECT * FROM events").fetchone())
    process_unit(state.db, unit, SimpleNamespace(files={}), state.clock, state.run)
    assert tuple(state.conn.execute("SELECT * FROM events").fetchone()) == before
    assert pending_inventory_projection(state.conn)
    assert not phase_two_allowed(state.conn, 2019)


@pytest.mark.parametrize(
    "kind,identifier",
    [
        ("map", "all"),
        ("calendar", "2026"),
        ("source_index", "2026"),
        ("history", "all"),
        ("dancer", "123"),
        ("source_event", "unmapped"),
        ("event", "unknown"),
        ("future_projector", "held"),
    ],
)
def test_unknown_or_inventory_capable_dispatch_blocks_both_entrypoints(state, kind, identifier):
    enqueue(
        state.conn,
        (WorkUnit("project", kind, identifier),),
        enqueued_at=state.clock.now().isoformat(),
    )
    state.conn.execute(
        "UPDATE derivation_scopes SET materialized_generation_id=NULL WHERE stage='project' AND unit_kind=? AND unit_id=?",
        (kind, identifier),
    )
    assert pending_inventory_projection(state.conn)
    assert not phase_two_allowed(state.conn, 2019)
    with pytest.raises(ValueError, match="projection is pending"):
        accept_year(
            state.conn, 2019, accepted_by="offline owner", accepted_at=state.clock.now().isoformat()
        )


def test_prior_event_scope_inventory_ownership_cannot_be_deleted_under_year_gate(state):
    retain_listing(state)
    state.conn.execute(
        "INSERT INTO canonical_scope_rows VALUES ('event','held','events','[\"held\"]')"
    )
    enqueue(
        state.conn,
        (WorkUnit("project", "event", "held"),),
        enqueued_at=state.clock.now().isoformat(),
    )
    assert pending_inventory_projection(state.conn)
    assert not phase_two_allowed(state.conn, 2019)


def test_inventory_digest_change_remains_blocked_despite_safe_pending_scope(state):
    enqueue(
        state.conn,
        (WorkUnit("project", "event", "held"),),
        enqueued_at=state.clock.now().isoformat(),
    )
    state.conn.execute("UPDATE events SET held='cancelled'")
    assert not pending_inventory_projection(state.conn)
    assert not phase_two_allowed(state.conn, 2019)


def test_source_event_requires_retained_unambiguous_mapping_before_neutral_dispatch(
    state, monkeypatch
):
    from swingset.project import process

    retain_listing(state, scope_kind="source_event", scope_id="eepro:held")
    unit = WorkUnit("project", "source_event", "eepro:held")
    enqueue(state.conn, (unit,), enqueued_at=state.clock.now().isoformat())
    assert pending_inventory_projection(state.conn)
    state.conn.execute(
        "INSERT INTO source_event_map VALUES ('eepro','eepro:held','held','explicit',1)"
    )
    # The new source scope itself changes history's dependency closure. Complete
    # that closure first; a subsequent source-only revision is inventory neutral.
    assert pending_inventory_projection(state.conn)
    drain(state)
    state.conn.execute(
        "UPDATE observations SET payload_json=replace(payload_json,'null','\"revised evidence\"') WHERE observation_id='listing'"
    )
    assert not pending_inventory_projection(state.conn)
    assert phase_two_allowed(state.conn, 2019)
    monkeypatch.setattr(
        process,
        "project_map",
        lambda *args: pytest.fail("neutral dispatch must not remap inventory"),
    )
    process_unit(state.db, unit, SimpleNamespace(files={}), state.clock, state.run)
