from dataclasses import replace

import pytest

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
            "INSERT INTO runs (run_id, started_at, dry_run) VALUES ('run_a', '2026-09-08T00:00:00Z', 0)"
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
        row = conn.execute("SELECT first_seen_at, last_seen_at, run_id FROM events").fetchone()
        assert tuple(row) == ("2026-09-08T00:00:00Z", "2026-09-08T00:00:00Z", "run_a")
        assert (
            conn.execute("SELECT value FROM revisions WHERE name = 'canonical'").fetchone()[0] == 1
        )


def test_empty_scope_removes_owned_rows(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        conn.execute(
            "INSERT INTO runs (run_id, started_at, dry_run) VALUES ('run_a', '2026-09-08T00:00:00Z', 0)"
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
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_scope_claims_are_bounded_idempotent_shared_and_transactional(tmp_path, monkeypatch):
    from swingset.project import writer

    records = tuple(
        replace(event("run_a", "2026-09-08T00:00:00Z"), event_id=f"event-{index}")
        for index in range(1025)
    )
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        conn.execute("INSERT INTO runs (run_id,started_at,dry_run) VALUES ('run_a','now',0)")

        def project(scope, rows):
            return replace_scope(
                conn,
                scope_kind="calendar",
                scope_id=scope,
                projection=Projection(rows),
                run_id="run_a",
                projected_at="2026-09-08T00:00:00Z",
                enqueue_links=False,
            )

        def owners():
            return [
                tuple(row)
                for row in conn.execute(
                    "SELECT scope_kind,scope_id,table_name,record_key FROM canonical_scope_rows "
                    "ORDER BY scope_kind,scope_id,table_name,record_key"
                )
            ]

        writes = []
        reads = []
        canonical_writes = []

        def observe(sql, params):
            if str(sql).startswith("INSERT INTO canonical_scope_rows"):
                writes.append(sql)
            if str(sql).startswith("SELECT * FROM events WHERE "):
                reads.append(tuple(params))
            if str(sql).startswith("INSERT INTO events "):
                canonical_writes.append(sql)

        fence = conn.execute("SELECT revision FROM history_dispatch_fence").fetchone()[0]
        with (
            database.transaction(),
            conn.observing(observe),
        ):
            assert project("primary", records)
        after = conn.execute("SELECT revision FROM history_dispatch_fence").fetchone()[0]
        assert len(writes) == 2, {"statements": len(writes), "fence_delta": after - fence}
        assert len(reads) == 2 and sum(map(len, reads)) == len(records)
        assert max(map(len, reads)) <= 1024
        assert len(canonical_writes) == 2
        # Preserve the original per-row path's measured trigger effects.
        assert after - fence == 5128
        assert len(owners()) == len(records)
        reads.clear()
        canonical_writes.clear()
        with database.transaction(), conn.observing(observe):
            assert not project("primary", records)
        assert len(reads) == 2 and sum(map(len, reads)) == len(records)
        assert not canonical_writes
        assert conn.execute("SELECT revision FROM history_dispatch_fence").fetchone()[0] == after

        mixed = (
            replace(records[0], name="Changed first", first_seen_at="ignored"),
            *records[1:1024],
            replace(records[1024], name="Changed last", first_seen_at="ignored"),
            replace(records[0], event_id="event-1025"),
        )

        def snapshot():
            return {
                table: [
                    tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
                ]
                for table, order in (
                    ("events", "event_id"),
                    ("canonical_scope_rows", "scope_kind,scope_id,table_name,record_key"),
                    ("history_dispatch_fence", "singleton"),
                    ("derivation_input_versions", "namespace,input_key"),
                    ("work_generations", "stage,unit_kind,unit_id"),
                    ("pending_work", "stage,unit_kind,unit_id"),
                    ("revisions", "name"),
                )
            }

        batch = writer._upsert_batch

        def individual(conn, table, rows):
            changed = False
            for row in rows:
                changed |= batch(conn, table, (row,))
            return changed

        with pytest.raises(RuntimeError, match="reference rollback"):
            with database.transaction(), monkeypatch.context() as patch:
                patch.setattr(writer, "_upsert_batch", individual)
                assert project("primary", mixed)
                expected = snapshot()
                raise RuntimeError("reference rollback")
        canonical_writes.clear()
        with database.transaction(), conn.observing(observe):
            assert project("primary", mixed)
        assert snapshot() == expected
        assert len(canonical_writes) == 2

        with database.transaction():
            assert not project("secondary", mixed[:1])
        before_owners = owners()
        before_fence = conn.execute("SELECT revision FROM history_dispatch_fence").fetchone()[0]
        with pytest.raises(RuntimeError, match="rollback"):
            with database.transaction():
                assert project("primary", ())
                raise RuntimeError("rollback")
        assert owners() == before_owners
        assert (
            conn.execute("SELECT revision FROM history_dispatch_fence").fetchone()[0]
            == before_fence
        )
        with database.transaction():
            assert project("primary", ())
        assert [row[0] for row in conn.execute("SELECT event_id FROM events")] == ["event-0"]
        assert owners() == [("calendar", "secondary", "events", '["event-0"]')]


def test_dropped_calendar_event_detaches_its_registry_placements(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        conn.execute(
            "INSERT INTO runs (run_id, started_at, dry_run) VALUES ('run_a', '2026-09-08T00:00:00Z', 0)"
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
            from swingset.state.postgres_schema import semantic_columns

            columns = semantic_columns("dancers")
            types = {
                row[0]: row[1]
                for row in conn.execute(
                    "SELECT a.attname,t.typname FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid WHERE a.attrelid='dancers'::regclass AND a.attnum>0 AND NOT a.attisdropped"
                )
            }
            values = [1 if types[name] in {"int8", "int4"} else "x" for name in columns]
            conn.execute(
                f"INSERT INTO dancers ({','.join(columns)}) VALUES ({','.join('%s' for _ in columns)})",
                values,
            )
            conn.execute(
                "INSERT INTO registry_placements (wsdc_id, role, dance_style, division, series_id, "
                "series_name_raw, event_month, event_id, result, points, source, snapshot_id, "
                "parser_version, first_seen_at, last_seen_at, run_id) VALUES (1, 'leader', 'wcs', 'novice', "
                "'slug-summer-hummer', 'Summer Hummer', '2026-08', '2026-08-summer-hummer', '1', 10, "
                "'wsdc_registry', 'snap_r', '1', '2026-09-08T00:00:00Z', '2026-09-08T00:00:00Z', 'run_a')"
            )
        # The live calendar stops listing an event once it has passed. The
        # registry row must survive the delete, unlinked, not block it.
        with database.transaction():
            replace_scope(
                conn,
                scope_kind="calendar",
                scope_id="wsdc",
                projection=Projection(),
                run_id="run_a",
                projected_at="2026-09-21T22:05:00Z",
            )
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert [tuple(row) for row in conn.execute("SELECT event_id FROM registry_placements")] == [
            (None,)
        ]
