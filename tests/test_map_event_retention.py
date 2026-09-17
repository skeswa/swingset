"""A mapped source occurrence keeps ownership after registry enrichment."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from swingset.model.canonical import Event
from swingset.project.map import project_map
from swingset.project.materialization import output_rows
from swingset.project.writer import Projection, replace_scope
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

NOW = "2026-09-13T00:00:00Z"


@pytest.fixture
def enriched_source_event(tmp_path):
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        event = Event(
            event_id="2024-07-saunaswing",
            series_id="saunaswing",
            name="SaunaSwing",
            year=2024,
            start_date="2024-07-01",
            end_date="2024-07-04",
            wsdc_status="registry",
            held="held",
            city="Helsinki",
            country="FI",
            source="wsdc_newsletter",
            sources=("scoringdance", "wsdc_newsletter"),
            history_source=("wsdc_newsletter",),
            snapshot_id="retained-newsletter",
            parser_version="1",
            first_seen_at=NOW,
            last_seen_at=NOW,
            run_id="test",
        )
        with db.transaction():
            replace_scope(
                conn,
                scope_kind="unmatched_source_events",
                scope_id="all",
                projection=Projection((event,)),
                run_id="test",
                projected_at=NOW,
            )
            conn.execute(
                "INSERT INTO source_events(source,source_ref,name_raw,start_date,end_date,url,"
                "snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
                "VALUES ('scoringdance','137','SaunaSwing','2024-07-01','2024-07-04',"
                "'https://example.test/137','retained-round','1',?,?, 'test')",
                (NOW, NOW),
            )
            conn.execute(
                "INSERT INTO source_events(source,source_ref,name_raw,url,snapshot_id,"
                "parser_version,first_seen_at,last_seen_at,run_id) "
                "VALUES ('scoringdance','109','Undated event','https://example.test/109',"
                "'retained-undated','1',?,?,'test')",
                (NOW, NOW),
            )
        bundle = SimpleNamespace(
            files={"overrides/event_aliases.csv": b"source,source_ref,event_id\n"}
        )
        yield db, event, bundle


def test_registry_enriched_self_match_retains_event_and_replays_identically(enriched_source_event):
    db, event, bundle = enriched_source_event
    conn = db.connection
    before = dict(
        conn.execute("SELECT * FROM events WHERE event_id=?", (event.event_id,)).fetchone()
    )
    with db.transaction():
        assert project_map(conn, bundle, NOW, "test", 19)
    assert (
        dict(conn.execute("SELECT * FROM events WHERE event_id=?", (event.event_id,)).fetchone())
        == before
    )
    assert conn.execute("SELECT event_id,match_confidence FROM source_event_map").fetchall()[0][
        :
    ] == (event.event_id, 1.0)
    assert (
        conn.execute(
            "SELECT count(*) FROM canonical_scope_rows WHERE scope_kind='unmatched_source_events'"
        ).fetchone()[0]
        == 1
    )
    unit = WorkUnit("project", "map", "all")
    output = list(output_rows(conn, unit))
    generations = conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0]
    with db.transaction():
        assert not project_map(conn, bundle, "2026-09-14T00:00:00Z", "test", 19)
    assert list(output_rows(conn, unit)) == output
    assert conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0] == generations
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("owner", ["unmatched_source_events", "override_events"])
def test_mapping_to_another_target_retires_unreferenced_source_event(enriched_source_event, owner):
    db, event, bundle = enriched_source_event
    conn = db.connection
    with db.transaction():
        conn.execute(
            "UPDATE canonical_scope_rows SET scope_kind=? WHERE scope_kind='unmatched_source_events'",
            (owner,),
        )
        project_map(conn, bundle, NOW, "test", 19)
        target = replace(event, event_id="calendar-saunaswing", source="wsdc_calendar")
        replace_scope(
            conn,
            scope_kind="calendar",
            scope_id="retained",
            projection=Projection((target,)),
            run_id="test",
            projected_at=NOW,
        )
    bundle.files["overrides/event_aliases.csv"] = (
        b"source,source_ref,event_id\nscoringdance,137,calendar-saunaswing\n"
    )
    with db.transaction():
        assert project_map(conn, bundle, NOW, "test", 19)
    assert conn.execute("SELECT event_id FROM source_event_map").fetchone()[0] == target.event_id
    assert (
        conn.execute("SELECT event_id FROM events WHERE event_id=?", (event.event_id,)).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM canonical_scope_rows WHERE scope_kind='unmatched_source_events'"
        ).fetchone()[0]
        == 0
    )
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_removed_override_can_keep_its_enriched_target_by_source_match(enriched_source_event):
    db, event, bundle = enriched_source_event
    conn = db.connection
    with db.transaction():
        conn.execute(
            "UPDATE canonical_scope_rows SET scope_kind='override_events' WHERE scope_kind='unmatched_source_events'"
        )
    with db.transaction():
        project_map(conn, bundle, NOW, "test", 19)
    assert conn.execute("SELECT event_id FROM source_event_map").fetchone()[0] == event.event_id
    assert (
        conn.execute("SELECT * FROM events WHERE event_id=?", (event.event_id,)).fetchone()
        is not None
    )
    before = list(output_rows(conn, WorkUnit("project", "map", "all")))
    with db.transaction():
        assert not project_map(conn, bundle, NOW, "test", 19)
    assert list(output_rows(conn, WorkUnit("project", "map", "all"))) == before
    assert (
        conn.execute(
            "SELECT count(*) FROM canonical_scope_rows WHERE scope_kind='override_events'"
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM canonical_scope_rows WHERE scope_kind='unmatched_source_events'"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize("status", ["unknown", "registry"])
@pytest.mark.parametrize("backing", ["history_source", "history_owner"])
def test_generated_id_collision_preserves_inventory_and_registry_month(
    enriched_source_event, status, backing
):
    from swingset.project.history import finalize_history

    db, event, bundle = enriched_source_event
    conn = db.connection
    # The listing's July ID and dates differ from the registry reporting month.
    # Neither unknown status nor a non-overlapping date grants overwrite authority.
    conn.execute(
        "UPDATE events SET series_id='wsdc-291',event_month='2024-08',"
        "start_date='2024-07-20',end_date='2024-07-21',wsdc_status=?",
        (status,),
    )
    if backing == "history_owner":
        conn.execute("UPDATE events SET history_source='[]'")
        conn.execute(
            "INSERT INTO canonical_scope_rows VALUES ('history','all','events',json_array(?))",
            (event.event_id,),
        )
    conn.execute(
        "INSERT INTO dancers(wsdc_id,first_name,last_name,name_norm,is_pro,primary_role,"
        "leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,"
        "leader_highest_level,leader_highest_points,follower_highest_level,follower_highest_points,"
        "recent_year,registry_internal_id,registry_fetched_at,source,snapshot_id,parser_version,"
        "first_seen_at,last_seen_at,run_id) VALUES (1,'Test','Dancer','test dancer',0,'leader',"
        "'novice','novice','novice','novice','novice',1,'novice',0,2024,1,?,'wsdc_registry',"
        "'registry','1',?,?,'test')",
        (NOW, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO registry_placements VALUES (1,'leader','wcs','novice','wsdc-291',"
        "'SaunaSwing','2024-08-01',?,'1',1,'wsdc_registry','registry','1',?,?,'test')",
        (event.event_id, NOW, NOW),
    )
    before = dict(conn.execute("SELECT * FROM events").fetchone())
    with db.transaction():
        assert project_map(conn, bundle, NOW, "test", 19)
        finalize_history(conn, now=NOW)
    assert dict(conn.execute("SELECT * FROM events").fetchone()) == before
    assert conn.execute("SELECT event_id FROM registry_placements").fetchone()[0] == event.event_id
    assert (
        conn.execute("SELECT event_id FROM source_event_map WHERE source_ref='137'").fetchone()[0]
        == event.event_id
    )
    assert conn.execute(
        "SELECT 1 FROM canonical_scope_rows WHERE scope_kind='unmatched_source_events' "
        "AND record_key=json_array(?)",
        (event.event_id,),
    ).fetchone()
    unit = WorkUnit("project", "map", "all")
    output = list(output_rows(conn, unit))
    count = conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0]
    with db.transaction():
        assert not project_map(conn, bundle, "2026-09-14T00:00:00Z", "test", 19)
        finalize_history(conn, now="2026-09-14T00:00:00Z")
    assert list(output_rows(conn, unit)) == output
    assert conn.execute("SELECT count(*) FROM derivation_generations").fetchone()[0] == count
    assert conn.execute("SELECT event_id FROM registry_placements").fetchone()[0] == event.event_id
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_provisional_source_only_collision_still_refreshes_listing(enriched_source_event):
    db, event, bundle = enriched_source_event
    conn = db.connection
    conn.execute("UPDATE events SET wsdc_status='unknown',history_source='[]',held='listed'")
    conn.execute(
        "UPDATE source_events SET end_date='2024-07-05',snapshot_id='new-listing' WHERE source_ref='137'"
    )
    with db.transaction():
        project_map(conn, bundle, NOW, "test", 19)
    row = conn.execute("SELECT * FROM events WHERE event_id=?", (event.event_id,)).fetchone()
    assert row["end_date"] == "2024-07-05"
    assert row["snapshot_id"] == "new-listing"
    assert row["source"] == "scoringdance"
