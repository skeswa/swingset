"""Frozen-schema regression for inventory-backed event and judge preservation.

Copied as a standalone test so the old frozen H16 fixture stays unchanged.
"""

from test_h16_acceptance import candidate_tables
from test_h16_acceptance import release_state as release_state
from test_project_event import EVENT

from swingset.build import service


def test_inventory_collision_keeps_baseline_supported_event_and_named_judges(release_state):
    from materialized_fixture import materialize_seeded_outputs

    from swingset.project.map import project_map
    from swingset.project.materialization import materializing
    from swingset.state.work import WorkUnit

    f = release_state
    before = dict(f.conn.execute("SELECT * FROM events WHERE event_id=?", (EVENT,)).fetchone())
    named = {
        row[0]
        for row in f.conn.execute(
            "SELECT judge_id FROM judges WHERE event_id=? AND name_raw IS NOT NULL AND wsdc_id IS NULL",
            (EVENT,),
        )
    }
    assert named
    with f.db.transaction():
        f.conn.execute(
            "INSERT INTO canonical_scope_rows VALUES ('history','all','events',json_array(?))",
            (EVENT,),
        )
        # An unsupported listing names the same month/slug, but conflicts with
        # the retained event dates. It cannot replace that event's public support.
        f.conn.execute(
            "INSERT INTO source_events(source,source_ref,name_raw,start_date,end_date,url,"
            "snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES "
            "('scoringdance','collision','Summer Hummer','2026-08-01','2026-08-02',"
            "'https://example.test/collision','unassessed-listing','1',?,?,?)",
            (f.clock.now().isoformat(), f.clock.now().isoformat(), f.run),
        )
        # This fixture retains precomputed inventory rows; capture the new
        # listing dependency before running its downstream map projector.
        with materializing(
            f.conn,
            WorkUnit("project", "inventory", "all"),
            now=f.clock.now(),
            run_id=f.run,
        ):
            pass
        project_map(f.conn, f.bundle, f.clock.now().isoformat(), f.run, 19)
    assert (
        dict(f.conn.execute("SELECT * FROM events WHERE event_id=?", (EVENT,)).fetchone()) == before
    )
    materialize_seeded_outputs(f.db, now=f.clock.now(), run_id=f.run)
    candidate = service.build_release(f.db, f.bundle, f.clock, f.run)
    tables = candidate_tables(candidate.path)
    assert any(row["event_id"] == EVENT for row in tables["events"])
    assert named <= {
        row["judge_id"]
        for row in tables["judges"]
        if row["name_raw"] is not None and row["wsdc_id"] is None
    }

