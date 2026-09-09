from swingset.state.db import open_database
from swingset.state.work import WorkUnit, accept_input, affected_work, complete


def event(conn: object, event_id: str) -> None:
    conn.execute(
        "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,?,2026,'2026-01-01','2026-01-04','registry','[]','test','snap','1','t','t','run')",
        (event_id, event_id, event_id),
    )


def accept_weights(db: object, digest: str) -> None:
    units = tuple(affected_work(db.connection, "link/weights.toml"))
    accept_input(
        db, "pipeline", "link/weights.toml", digest, units, accepted_at="2026-01-01T00:00:00Z"
    )


def test_second_weights_edit_requeues_completed_and_pending_events(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        event(db.connection, "a")
        event(db.connection, "b")
        accept_weights(db, "one")
        complete(db, WorkUnit("link", "event", "a"), lambda _conn: None)
        assert [
            row[0]
            for row in db.connection.execute("SELECT unit_id FROM pending_work WHERE stage='link'")
        ] == ["b"]
        accept_weights(db, "two")
        assert {
            row[0]
            for row in db.connection.execute("SELECT unit_id FROM pending_work WHERE stage='link'")
        } == {"a", "b"}
