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
        # Each event waits for its resolution, which waits for the inventory.
        units += [WorkUnit("project", "resolution", str(index)) for index in range(200)]
        units += [
            WorkUnit("project", "inventory", "all"),
            WorkUnit("project", "mapping", "eepro"),
            WorkUnit("project", "year", "2026"),
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
                ("mapping", "eepro"),
                ("event", "event"),
                ("source_event", "source"),
                # Retired: registered, never a scope (plan F17, F19).
                ("history", "all"),
                ("map", "all"),
            )
        ]
        conn.executemany(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES (?,?,?,?)",
            [(u.stage, u.unit_kind, u.unit_id, NOW.isoformat()) for u in units],
        )
        conn.execute(
            "INSERT INTO events(event_id,series_id,name,year,wsdc_status,sources,source,"
            "snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
            "VALUES ('event','s','Event',2026,'registry','[]','test','snap','1','t','t','run')"
        )
        conn.execute("INSERT INTO source_event_map VALUES ('eepro','source','event','explicit',1)")
        with query(conn):
            project = tuple(dependencies.scopes(conn, "project"))
            assert WorkUnit("project", "history", "all") not in project
            assert WorkUnit("project", "map", "all") not in project
            resolution = WorkUnit("project", "resolution", "event")
            assert resolution in project
            year = WorkUnit("project", "year", "2026")
            assert year in project
            assert dependencies.prerequisites(conn, year) == (
                WorkUnit("project", "calendar", "calendar"),
                WorkUnit("project", "inventory", "all"),
                resolution,
                WorkUnit("project", "event", "event"),
            )
            assert dependencies.prerequisites(conn, WorkUnit("project", "mapping", "eepro")) == (
                WorkUnit("project", "calendar", "calendar"),
                WorkUnit("project", "inventory", "all"),
            )
            # The mappings are waited for, not inputs: the proposals naming
            # the event are content, and a resolution deletes map rows whose
            # listing left it.
            assert dependencies.prerequisites(conn, resolution) == (
                WorkUnit("project", "calendar", "calendar"),
                WorkUnit("project", "inventory", "all"),
            )
            assert dependencies.waits_for(conn, resolution) == (
                WorkUnit("project", "mapping", "eepro"),
            )
            conn.execute(
                "INSERT INTO source_event_proposals VALUES ('scoringdance','s:1','event','name_date',1,NULL)"
            )
            assert dependencies.waits_for(conn, resolution) == (
                WorkUnit("project", "mapping", "eepro"),
                WorkUnit("project", "mapping", "scoringdance"),
            )
            assert dependencies.prerequisites(conn, WorkUnit("project", "event", "event")) == (
                resolution,
            )
            assert dependencies.prerequisites(
                conn, WorkUnit("project", "source_event", "source")
            ) == (resolution, WorkUnit("project", "event", "event"))
            # A link waits for its resolution and event, and for the dancers
            # its read set names, never the registry or a year (plan F20, D-0226).
            assert dependencies.prerequisites(conn, WorkUnit("link", "event", "event")) == tuple(
                u
                for u in project
                if u.unit_kind in {"event", "resolution"} and u.unit_id == "event"
            )
            conn.executemany(
                "INSERT INTO derivation_reads VALUES ('link','event','event',?)",
                [("project/dancer/2",), ("project/dancer/3",)],
            )
            links = dependencies.prerequisites(conn, WorkUnit("link", "event", "event"))
            # A read dancer with a unit is a prerequisite; one with none (a
            # printed id the registry lacks) and one never read are not (F27).
            assert WorkUnit("project", "dancer", "2") in links
            assert WorkUnit("project", "dancer", "3") not in links
            assert WorkUnit("project", "dancer", "1") not in links


@pytest.mark.parametrize("storage", ["registered", "queued", "physical"])
@pytest.mark.parametrize("kind", [*sorted(dependencies.PROJECT), "unknown"])
def test_mapping_dependency_subset_matches_full_scope_inference(tmp_path, storage, kind):
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
        for kind_name, direct in (
            ("mapping", dependencies.mapping_units),
            ("resolution", dependencies.resolution_units),
        ):
            expected = tuple(
                unit for unit in dependencies.scopes(conn, "project") if unit.unit_kind == kind_name
            )
            assert direct(conn) == expected


def test_resolution_lookup_preserves_empty_state_and_write_rollback_without_catalog_scan(
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
                    "INSERT INTO canonical_scope_rows VALUES ('resolution','event','events','[]')"
                )
                assert dependencies.prerequisites(conn, unit) == (
                    WorkUnit("project", "resolution", "event"),
                )
                conn.execute("ROLLBACK TO temporary_mapping")
                conn.execute("RELEASE temporary_mapping")
                assert dependencies.prerequisites(conn, unit) == ()
            assert dependencies.prerequisites(conn, unit) == ()


def test_mapping_units_come_from_listings_and_resolutions_from_map_rows(source_fixture):
    conn = source_fixture.conn
    project = tuple(dependencies.scopes(conn, "project"))
    mappings = tuple(unit for unit in project if unit.unit_kind == "mapping")
    assert mappings == (WorkUnit("project", "mapping", "eepro"),)
    resolution = WorkUnit("project", "resolution", "event-a")
    assert resolution in project
    assert dependencies.prerequisites(conn, WorkUnit("project", "event", "event-a")) == (
        resolution,
    )


@pytest.mark.parametrize("kind", ["history", "map"])
def test_a_retired_scope_is_never_scheduled_registered_or_run(tmp_path, kind):
    """project/history/all and project/map/all are retired (plan F17, F19):
    their rows and generations stay, but no catalog yields them, refresh never
    registers them, and a stale hint for one is dropped without running."""
    from types import SimpleNamespace

    from swingset.clock import FakeClock
    from swingset.project.process import process_unit
    from swingset.state import derivations

    history = WorkUnit("project", kind, "all")
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) "
            "VALUES ('project',?,'all',?)",
            (kind, NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO pending_work VALUES ('project',?,'all',?)", (kind, NOW.isoformat())
        )
        conn.execute(
            "INSERT INTO canonical_scope_rows VALUES (?,'all','events','[\"e\"]')", (kind,)
        )
        assert dependencies.retired(history) and history in dependencies.RETIRED
        assert history not in set(dependencies.scopes(conn, "project"))
        assert history not in set(derivations.pending_units(conn, "project"))
        assert history not in set(derivations.candidate_units(conn, "project"))
        assert next_offline(conn, now=NOW) != history
        conn.execute("DELETE FROM derivation_scopes WHERE unit_kind=?", (kind,))
        derivations.refresh(conn, NOW)
        assert not conn.execute(
            "SELECT 1 FROM derivation_scopes WHERE unit_kind=?", (kind,)
        ).fetchone()
        run = db.start_run(NOW)
        assert not process_unit(db, history, SimpleNamespace(files={}), FakeClock(NOW), run)
        assert not conn.execute("SELECT 1 FROM pending_work WHERE unit_kind=?", (kind,)).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM derivation_generations WHERE unit_kind=?", (kind,)
        ).fetchone()
