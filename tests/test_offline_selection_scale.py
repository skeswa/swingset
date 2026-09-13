"""Fair selection shares one unchanged catalog without changing dependency rules."""

from collections import Counter
from datetime import UTC, datetime

import pytest
from test_h15_acceptance import source_fixture as source_fixture

from swingset.schedule.fairness import next_offline
from swingset.state import derivation_dependencies as dependencies
from swingset.state.db import open_database
from swingset.state.derivation_query import query
from swingset.state.work import WorkUnit

NOW = datetime(2026, 9, 13, tzinfo=UTC)


def test_blocked_event_catalog_is_read_once_before_ready_registry_work(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        conn = db.connection
        units = [WorkUnit("project", "event", str(index)) for index in range(200)]
        units += [
            WorkUnit("project", "inventory", "all"),
            WorkUnit("project", "map", "all"),
            WorkUnit("project", "history", "all"),
            WorkUnit("project", "dancer", "1"),
        ]
        conn.executemany(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES (?,?,?,?)",
            [(u.stage, u.unit_kind, u.unit_id, NOW.isoformat()) for u in units],
        )
        conn.execute(
            "INSERT INTO scheduler_offline_service VALUES ('project','dancer',1,?,10)",
            (NOW.isoformat(),),
        )
        calls = Counter()
        original = dependencies._scopes

        def counted(conn, stage):
            calls[stage] += 1
            yield from original(conn, stage)

        monkeypatch.setattr(dependencies, "_scopes", counted)
        assert next_offline(conn, now=NOW) == WorkUnit("project", "dancer", "1")
        assert calls == {"project": 1, "link": 1}


def test_fixed_and_shared_prerequisites_match_full_catalog_order_and_invalidate(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        units = [
            WorkUnit("project", kind, identifier)
            for kind, identifier in (
                ("calendar", "calendar"),
                ("dancer", "1"),
                ("dancer", "2"),
                ("source_index", "index"),
                ("inventory", "all"),
                ("map", "all"),
                ("event", "event"),
                ("source_event", "source"),
                ("history", "all"),
            )
        ]
        conn.executemany(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES (?,?,?,?)",
            [(u.stage, u.unit_kind, u.unit_id, NOW.isoformat()) for u in units],
        )
        conn.execute("INSERT INTO source_event_map VALUES ('eepro','source','event','explicit',1)")
        with query(conn):
            project = tuple(dependencies.scopes(conn, "project"))
            assert dependencies.prerequisites(conn, WorkUnit("project", "map", "all")) == (
                WorkUnit("project", "inventory", "all"),
            )
            assert dependencies.prerequisites(conn, WorkUnit("project", "event", "event")) == tuple(
                u for u in project if u.unit_kind == "map"
            )
            assert dependencies.prerequisites(
                conn, WorkUnit("project", "source_event", "source")
            ) == (WorkUnit("project", "map", "all"), WorkUnit("project", "event", "event"))
            assert dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == tuple(
                u
                for u in project
                if u.unit_kind in {"dancer", "history"}
                or (u.unit_kind == "event" and u.unit_id == "event")
            )
            conn.execute(
                "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project','dancer','3',?)",
                (NOW.isoformat(),),
            )
            assert WorkUnit("project", "dancer", "3") in dependencies.prerequisites(
                conn, WorkUnit("link", "event", "event")
            )


@pytest.mark.parametrize("storage", ["registered", "queued", "physical"])
@pytest.mark.parametrize("kind", [*sorted(dependencies.PROJECT), "unknown"])
def test_map_dependency_subset_matches_full_scope_inference(tmp_path, storage, kind):
    with open_database(tmp_path) as db:
        conn = db.connection
        if storage == "registered":
            conn.execute(
                "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project',?,'custom',?)",
                (kind, NOW.isoformat()),
            )
        elif storage == "queued":
            conn.execute(
                "INSERT INTO pending_work VALUES ('project',?,'custom',?)",
                (kind, NOW.isoformat()),
            )
        else:
            conn.execute(
                "INSERT INTO canonical_scope_rows VALUES (?,'custom','events','[]')", (kind,)
            )
        expected = tuple(
            unit for unit in dependencies.scopes(conn, "project") if unit.unit_kind == "map"
        )
        assert dependencies.prerequisites(conn, WorkUnit("project", "event", "event")) == expected


def test_map_lookup_preserves_empty_state_and_write_rollback_without_catalog_scan(
    tmp_path, monkeypatch
):
    with open_database(tmp_path) as db:
        conn = db.connection
        unit = WorkUnit("project", "event", "event")

        def forbidden(*args):
            raise AssertionError("event prerequisites enumerated the full catalog")

        monkeypatch.setattr(dependencies, "_scopes", forbidden)
        with query(conn):
            assert dependencies.prerequisites(conn, unit) == ()
            with db.transaction():
                conn.execute("SAVEPOINT temporary_mapping")
                conn.execute(
                    "INSERT INTO canonical_scope_rows VALUES ('map','custom','events','[]')"
                )
                assert dependencies.prerequisites(conn, unit) == (
                    WorkUnit("project", "map", "all"),
                    WorkUnit("project", "map", "custom"),
                )
                conn.execute("ROLLBACK TO temporary_mapping")
                conn.execute("RELEASE temporary_mapping")
                assert dependencies.prerequisites(conn, unit) == ()
            assert dependencies.prerequisites(conn, unit) == ()


def test_map_subset_infers_shared_scope_from_physical_observations(source_fixture):
    conn = source_fixture.conn
    expected = tuple(
        unit for unit in dependencies.scopes(conn, "project") if unit.unit_kind == "map"
    )
    assert dependencies.prerequisites(conn, WorkUnit("project", "event", "event-a")) == expected
    assert WorkUnit("project", "map", "all") in expected
