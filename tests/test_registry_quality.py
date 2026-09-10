from swingset.project.registry_events import reconcile_registry_events
from swingset.state.db import open_database


def _event(conn: object, event_id: str, name: str, end_date: str) -> None:
    conn.execute(
        "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,? ,?,2026,'2026-01-01',?,'registry','[]','test','snap','1','t','t','run')",
        (event_id, f"slug-{event_id}", name, end_date),
    )


def _run(conn: object) -> None:
    conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run','t',1)")


def _dancer_and_placement(conn: object) -> None:
    conn.execute(
        "INSERT INTO dancers(wsdc_id,first_name,last_name,name_norm,is_pro,primary_role,leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,leader_highest_level,leader_highest_points,follower_highest_level,follower_highest_points,recent_year,registry_internal_id,registry_fetched_at,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (1,'A','Dancer','a dancer',0,'leader','novice','novice','novice','novice','novice',1,'novice',1,2026,1,'t','test','snap','1','t','t','run')"
    )
    conn.execute(
        "INSERT INTO registry_placements(wsdc_id,role,dance_style,division,series_id,series_name_raw,event_month,result,points,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (1,'leader','wcs','novice','wsdc-77','  Exact   Event ','2026-08-01','1',10,'test','snap','1','t','t','run')"
    )


def test_unique_exact_name_and_month_sets_event_id_and_enqueues_link(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        _run(db.connection)
        _event(db.connection, "event-a", "Exact Event", "2026-08-30")
        _dancer_and_placement(db.connection)
        assert reconcile_registry_events(db.connection, reconciled_at="t", run_id="run")
        assert (
            db.connection.execute("SELECT event_id FROM registry_placements").fetchone()[0]
            == "event-a"
        )
        assert (
            db.connection.execute("SELECT value FROM revisions WHERE name='dancers'").fetchone()[0]
            == 1
        )
        assert (
            db.connection.execute("SELECT unit_id FROM pending_work WHERE stage='link'").fetchone()[
                0
            ]
            == "event-a"
        )


def test_ambiguous_event_match_is_withheld_and_reported(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        _run(db.connection)
        _event(db.connection, "event-a", "Exact Event", "2026-08-20")
        _event(db.connection, "event-b", "exact event", "2026-08-30")
        _dancer_and_placement(db.connection)
        reconcile_registry_events(db.connection, reconciled_at="t", run_id="run")
        assert (
            db.connection.execute("SELECT event_id FROM registry_placements").fetchone()[0] is None
        )
        finding = db.connection.execute(
            "SELECT kind,evidence_json FROM findings WHERE owner_kind='registry_reconciliation' AND owner_id='1'"
        ).fetchone()
        assert finding[0] == "event_alias"
        assert '"candidate_event_ids":["event-a","event-b"]' in finding[1]


def test_event_change_clears_stale_match_and_requeues_old_event(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        _run(db.connection)
        _event(db.connection, "event-a", "Exact Event", "2026-08-30")
        _dancer_and_placement(db.connection)
        reconcile_registry_events(db.connection, reconciled_at="t", run_id="run")
        db.connection.execute("DELETE FROM pending_work")
        db.connection.execute("UPDATE events SET name='Renamed Event' WHERE event_id='event-a'")
        reconcile_registry_events(db.connection, reconciled_at="t2", run_id="run")
        assert (
            db.connection.execute("SELECT event_id FROM registry_placements").fetchone()[0] is None
        )
        assert (
            db.connection.execute("SELECT unit_id FROM pending_work WHERE stage='link'").fetchone()[
                0
            ]
            == "event-a"
        )


def test_series_slug_normalization_matches_but_collisions_are_withheld(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        _run(db.connection)
        _event(db.connection, "event-a", "Exact Event 2026", "2026-08-20")
        _dancer_and_placement(db.connection)
        reconcile_registry_events(db.connection, reconciled_at="t", run_id="run", wsdc_id=1)
        assert (
            db.connection.execute("SELECT event_id FROM registry_placements").fetchone()[0]
            == "event-a"
        )

        _event(db.connection, "event-b", "Exact-Event", "2026-08-30")
        reconcile_registry_events(db.connection, reconciled_at="t2", run_id="run", wsdc_id=1)
        assert (
            db.connection.execute("SELECT event_id FROM registry_placements").fetchone()[0] is None
        )
        evidence = db.connection.execute(
            "SELECT evidence_json FROM findings WHERE owner_kind='registry_reconciliation' AND owner_id='1' AND closed_at IS NULL"
        ).fetchone()[0]
        assert '"candidate_event_ids":["event-a","event-b"]' in evidence


def test_dancer_scoped_reconciliation_does_not_touch_other_dancers(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        _run(db.connection)
        _event(db.connection, "event-a", "Exact Event", "2026-08-30")
        _event(db.connection, "stale", "Different Event", "2026-08-30")
        _dancer_and_placement(db.connection)
        db.connection.execute(
            "INSERT INTO dancers SELECT 2,first_name,last_name,name_norm,is_pro,primary_role,leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,leader_highest_level,leader_highest_points,follower_highest_level,follower_highest_points,recent_year,2,registry_fetched_at,merged_into_wsdc_id,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id FROM dancers WHERE wsdc_id=1"
        )
        db.connection.execute(
            "INSERT INTO registry_placements SELECT 2,role,dance_style,division,series_id,series_name_raw,event_month,'stale',result,points,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id FROM registry_placements WHERE wsdc_id=1"
        )
        reconcile_registry_events(db.connection, reconciled_at="t", run_id="run", wsdc_id=1)
        assert [
            tuple(row)
            for row in db.connection.execute(
                "SELECT wsdc_id,event_id FROM registry_placements ORDER BY wsdc_id"
            )
        ] == [(1, "event-a"), (2, "stale")]
