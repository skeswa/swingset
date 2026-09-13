"""Unmapped metadata must not turn a current map into a superseded attempt."""

from datetime import datetime

from test_map_event_retention import NOW, enriched_source_event  # noqa: F401

from swingset.clock import FakeClock
from swingset.project.materialization import output_rows
from swingset.project.process import process_unit
from swingset.state.derivations import capture, current, selected_generation
from swingset.state.work import WorkUnit


def test_scheduled_undated_proxy_preserves_enriched_map(enriched_source_event):  # noqa: F811
    db, event, bundle = enriched_source_event
    conn = db.connection
    clock = FakeClock(datetime.fromisoformat(NOW))
    proxy = WorkUnit("project", "source_event", "scoringdance:109")
    mapping = WorkUnit("project", "map", "all")
    with db.transaction():
        conn.execute(
            "INSERT INTO accepted_inputs VALUES ('pipeline','recipe/runtime','fixture-runtime')"
        )
        conn.execute(
            "UPDATE source_events SET source_ref=? WHERE source_ref='109'", (proxy.unit_id,)
        )
        conn.execute(
            "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) "
            "VALUES ('undated','scoringdance','event','GET','https://example.test/109',"
            "'scoringdance.event','active')"
        )
        conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
            "body_bytes,content_changed,run_id,classification) "
            "VALUES ('retained-undated','undated','GET','https://example.test/109',?,200,0,1,'test','ok')",
            (NOW,),
        )
        conn.execute(
            "INSERT INTO observations VALUES ('undated-observation','undated',"
            "'retained-undated','source_event','source_event',?,0,'1','1','{}')",
            (proxy.unit_id,),
        )
    process_unit(db, WorkUnit("project", "inventory", "all"), bundle, clock, "test")
    process_unit(db, mapping, bundle, clock, "test")
    generation = selected_generation(conn, mapping)
    before = list(output_rows(conn, mapping))
    assert generation is not None
    assert conn.execute(
        "SELECT event_id FROM events WHERE event_id=?", (event.event_id,)
    ).fetchone()
    with db.transaction():
        selection = capture(conn, proxy, now=clock.now())
    assert not process_unit(db, proxy, bundle, clock, "test", selection=selection)
    assert current(conn, proxy)
    assert selected_generation(conn, mapping) == generation
    assert list(output_rows(conn, mapping)) == before
    assert (
        conn.execute(
            "SELECT event_id FROM source_event_map WHERE source_ref=?", (proxy.unit_id,)
        ).fetchone()
        is None
    )
    assert conn.execute(
        "SELECT start_date,end_date FROM source_events WHERE source_ref=?", (proxy.unit_id,)
    ).fetchone()[:] == (None, None)
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
