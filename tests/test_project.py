from swingset.model.canonical import Event
from swingset.project.writer import Projection, replace_scope
from swingset.state.db import open_database


def event(run: str, seen: str) -> Event:
    return Event(
        event_id="2026-08-summer-hummer",
        series_id="slug-summer-hummer",
        name="Summer Hummer",
        year=2026,
        start_date="2026-08-27",
        end_date="2026-08-30",
        wsdc_status="registry",
        sources=("wsdc_calendar",),
        source="wsdc_calendar",
        snapshot_id="snap_a",
        parser_version="1",
        first_seen_at=seen,
        last_seen_at=seen,
        run_id=run,
    )


def test_scope_replacement_preserves_seen_times_for_unchanged_facts(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-08T00:00:00Z',0)"
        )
        with database.transaction():
            assert replace_scope(
                conn,
                scope_kind="calendar",
                scope_id="wsdc",
                projection=Projection((event("run_a", "2026-09-08T00:00:00Z"),)),
                run_id="run_a",
                projected_at="2026-09-08T00:00:00Z",
            )
        with database.transaction():
            assert not replace_scope(
                conn,
                scope_kind="calendar",
                scope_id="wsdc",
                projection=Projection((event("run_b", "2026-09-09T00:00:00Z"),)),
                run_id="run_b",
                projected_at="2026-09-09T00:00:00Z",
            )
        row = conn.execute("SELECT first_seen_at,last_seen_at,run_id FROM events").fetchone()
        assert tuple(row) == ("2026-09-08T00:00:00Z", "2026-09-08T00:00:00Z", "run_a")
        assert conn.execute("SELECT value FROM revisions WHERE name='canonical'").fetchone()[0] == 1


def test_empty_scope_removes_owned_rows(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-08T00:00:00Z',0)"
        )
        with database.transaction():
            replace_scope(
                conn,
                scope_kind="calendar",
                scope_id="wsdc",
                projection=Projection((event("run_a", "2026-09-08T00:00:00Z"),)),
                run_id="run_a",
                projected_at="2026-09-08T00:00:00Z",
            )
        with database.transaction():
            replace_scope(
                conn,
                scope_kind="calendar",
                scope_id="wsdc",
                projection=Projection(),
                run_id="run_a",
                projected_at="2026-09-08T01:00:00Z",
            )
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0
