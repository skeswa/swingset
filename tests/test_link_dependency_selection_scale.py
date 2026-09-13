"""Exact link subsets preserve the complete catalog without enumerating it."""

from datetime import UTC, datetime

import pytest
from test_h15_acceptance import source_fixture as source_fixture

from swingset.state import derivation_dependencies as dependencies
from swingset.state.db import open_database
from swingset.state.derivation_query import query
from swingset.state.work import WorkUnit

NOW = datetime(2026, 9, 13, tzinfo=UTC).isoformat()


def expected(conn, identifier="event"):
    return tuple(
        unit
        for unit in dependencies.scopes(conn, "project")
        if unit.unit_kind in {"dancer", "history"}
        or (unit.unit_kind == "event" and unit.unit_id == identifier)
    )


@pytest.mark.parametrize("storage", ["registered", "queued", "physical"])
@pytest.mark.parametrize("kind", [*sorted(dependencies.PROJECT), "unknown"])
def test_link_subsets_match_full_catalog_for_every_stored_scope(tmp_path, storage, kind):
    with open_database(tmp_path) as db:
        conn = db.connection
        if storage == "registered":
            conn.execute(
                "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project',?,'event',?)",
                (kind, NOW),
            )
        elif storage == "queued":
            conn.execute("INSERT INTO pending_work VALUES ('project',?,'event',?)", (kind, NOW))
        else:
            conn.execute(
                "INSERT INTO canonical_scope_rows VALUES (?,'event','events','[]')", (kind,)
            )
        assert dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == expected(
            conn
        )
        assert dependencies.prerequisites(conn, WorkUnit("link", "event", "other")) == expected(
            conn, "other"
        )


@pytest.mark.parametrize("kind", [*sorted(dependencies.PROJECT), "unknown"])
def test_link_subsets_include_physical_observations(source_fixture, kind):
    conn = source_fixture.conn
    conn.execute("UPDATE observations SET scope_kind=?,scope_id='event'", (kind,))
    assert dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == expected(conn)


def test_link_subsets_do_not_promote_source_index_ownership_to_global_scope(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute("INSERT INTO source_event_scope_rows VALUES ('event','eepro','source')")
        assert tuple(dependencies.scopes(conn, "project")) == (
            WorkUnit("project", "source_index", "event"),
        )
        assert dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == ()
        conn.execute("INSERT INTO source_event_map VALUES ('eepro','source','event','explicit',1)")
        assert dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == expected(
            conn
        )
        assert dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == (
            WorkUnit("project", "event", "event"),
        )


def test_link_subsets_keep_all_dancers_sorted_and_never_scan_full_catalog(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        conn = db.connection
        units = [("dancer", str(number)) for number in range(1000)] + [
            ("history", "custom"),
            ("event", "event"),
        ]
        conn.executemany(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES ('project',?,?,?)",
            [(kind, identifier, NOW) for kind, identifier in units],
        )
        wanted = expected(conn)
        assert sum(unit.unit_kind == "dancer" for unit in wanted) == 1000

        def forbidden(*args):
            raise AssertionError("link prerequisites enumerated the full catalog")

        monkeypatch.setattr(dependencies, "_scopes", forbidden)
        assert dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == wanted
        with query(conn):
            assert dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == wanted
            with db.transaction():
                conn.execute("SAVEPOINT additional_dancer")
                conn.execute(
                    "INSERT INTO canonical_scope_rows VALUES ('dancer','new','dancers','[]')"
                )
                assert WorkUnit("project", "dancer", "new") in dependencies.prerequisites(
                    conn, WorkUnit("link", "event", "event")
                )
                conn.execute("ROLLBACK TO additional_dancer")
                conn.execute("RELEASE additional_dancer")
                assert (
                    dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == wanted
                )
            assert dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == wanted


@pytest.mark.parametrize("stage", ["parse", "link", "build"])
def test_other_stage_queues_do_not_create_project_dependencies(tmp_path, stage):
    with open_database(tmp_path) as db:
        conn = db.connection
        for kind in ("event", "dancer", "history"):
            conn.execute("INSERT INTO pending_work VALUES (?,?,?,?)", (stage, kind, "event", NOW))
        assert (
            dependencies.prerequisites(conn, WorkUnit("link", "event", "event"))
            == expected(conn)
            == ()
        )


@pytest.mark.parametrize("origin", ["events", "source_events", "registry_placements"])
def test_shared_inference_from_standalone_fact_tables(tmp_path, origin):
    with open_database(tmp_path) as db:
        conn = db.connection
        if origin == "events":
            from swingset.model.canonical import Event
            from swingset.project.writer import Projection, replace_scope

            replace_scope(
                conn,
                scope_kind="fixture",
                scope_id="event",
                projection=Projection(
                    (
                        Event(
                            event_id="event",
                            series_id="event",
                            name="Synthetic Event",
                            year=2026,
                            start_date="2026-01-01",
                            end_date="2026-01-02",
                            wsdc_status="unknown",
                            source="test",
                            snapshot_id="test",
                            parser_version="1",
                            first_seen_at=NOW,
                            last_seen_at=NOW,
                            run_id="test",
                        ),
                    )
                ),
                run_id="test",
                projected_at=NOW,
            )
            conn.execute("DELETE FROM canonical_scope_rows")
        elif origin == "source_events":
            conn.execute(
                "INSERT INTO source_events(source,source_ref,url,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('test','source','https://example.test','test','1',?,?,'test')",
                (NOW, NOW),
            )
        else:
            conn.execute(
                "INSERT INTO dancers(wsdc_id,first_name,last_name,name_norm,is_pro,primary_role,leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,leader_highest_level,leader_highest_points,follower_highest_level,follower_highest_points,recent_year,registry_internal_id,registry_fetched_at,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (1,'Test','Dancer','test dancer',0,'leader','novice','novice','novice','novice','novice',0,'novice',0,2026,1,?,'test','test','1',?,?,'test')",
                (NOW, NOW, NOW),
            )
            conn.execute(
                "INSERT INTO registry_placements VALUES (1,'leader','west_coast_swing','novice','series','Synthetic Series','2026-01',NULL,'F',0,'test','test','1',?,?,'test')",
                (NOW, NOW),
            )
        actual = dependencies.prerequisites(conn, WorkUnit("link", "event", "event"))
        assert actual == expected(conn)
        assert WorkUnit("project", "history", "all") in actual
        assert (WorkUnit("project", "event", "event") in actual) == (origin == "events")
        assert not any(unit.unit_kind == "dancer" for unit in actual)
