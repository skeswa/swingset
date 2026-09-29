"""The map split: one mapping per source, one resolution per event (plan F19)."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

from test_h15_acceptance import drain_project, pointers
from test_h15_acceptance import source_fixture as source_fixture

from swingset.model.canonical import Event
from swingset.model.ids import event_id
from swingset.project.map import project_map
from swingset.project.writer import Projection, replace_scope
from swingset.state import derivation_dependencies as dependencies
from swingset.state.db import open_database
from swingset.state.work import WorkUnit, enqueue

NOW = "2026-09-13T00:00:00Z"


@dataclass(frozen=True)
class Bundle:
    files: MappingProxyType[str, bytes]


def test_reprojecting_unchanged_overrides_keeps_placeholder_events(tmp_path: Path) -> None:
    csv = b"event_id,source,kind,url,parser\n2026-08-desert-city-swing,worlddanceregistry,event,https://scores.worlddanceregistry.com/98011277-01cd-11f1-9a29-0aa72bbce9ea/rounds/routeInfo.json,wdr.rounds\n"
    bundle = Bundle(
        MappingProxyType(
            {
                "overrides/source_urls.csv": csv,
                "overrides/event_aliases.csv": b"event_id,source,source_ref\n",
            }
        )
    )
    with open_database(tmp_path, lock=False) as database:
        database.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-08T00:00:00Z',0)"
        )
        with database.transaction():
            assert project_map(database.connection, bundle, "2026-09-08T00:00:00Z", "run_a", 2)
        assert (
            database.connection.execute("SELECT event_id FROM events").fetchone()[0]
            == "2026-08-desert-city-swing"
        )
        with database.transaction():
            assert not project_map(database.connection, bundle, "2026-09-09T00:00:00Z", "run_a", 2)
        assert (
            database.connection.execute("SELECT event_id FROM events").fetchone()[0]
            == "2026-08-desert-city-swing"
        )


def _event(identifier, name, start, end, *, status="registry", source="wsdc_calendar"):
    return Event(
        event_id=identifier,
        series_id="slug-" + identifier,
        name=name,
        year=int(end[:4]),
        start_date=start,
        end_date=end,
        wsdc_status=status,
        source=source,
        snapshot_id="calendar",
        parser_version="1",
        first_seen_at=NOW,
        last_seen_at=NOW,
        run_id="test",
    )


def _listing(conn, source, ref, name, start, end, snapshot="listing"):
    conn.execute(
        "INSERT INTO source_events(source,source_ref,name_raw,start_date,end_date,url,"
        "snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
        "VALUES (?,?,?,?,?,'https://example.test/',?,'4',?,?,'test')",
        (source, ref, name, start, end, snapshot, NOW, NOW),
    )


def _calendar(conn, *events):
    replace_scope(
        conn,
        scope_kind="calendar",
        scope_id="2026",
        projection=Projection(tuple(events)),
        run_id="test",
        projected_at=NOW,
    )


def _rows(conn, sql):
    return [tuple(row) for row in conn.execute(sql)]


def test_mapping_and_resolution_follow_every_rule_of_the_map(tmp_path):
    """Override, unique name-and-date match, generated placeholder; ambiguous
    and undated listings stay unmapped. Each resolution owns its event's
    placeholder row; a catalog event keeps its calendar owner."""
    aliases = (
        b"source,source_ref,event_id\n"
        b"eepro,e:override,2026-07-override-weekend\n"
        b"scoringdance,s:catalog-override,catalog-b\n"
    )
    bundle = SimpleNamespace(files={"overrides/event_aliases.csv": aliases})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            _calendar(
                conn,
                _event("catalog-a", "Swing Weekend", "2026-03-01", "2026-03-03"),
                _event("catalog-b", "Other Weekend", "2026-04-01", "2026-04-03"),
                _event("twin-1", "Twin Open", "2026-05-01", "2026-05-03"),
                _event("twin-2", "Twin Open", "2026-05-02", "2026-05-04"),
            )
            _listing(conn, "eepro", "e:match", "Swing Weekend", "2026-03-02", "2026-03-02")
            _listing(conn, "eepro", "e:new", "New Weekend", "2026-06-05", "2026-06-07")
            _listing(conn, "eepro", "e:override", "Override Weekend", None, None)
            _listing(conn, "scoringdance", "s:twin", "Twin Open", "2026-05-02", "2026-05-03")
            _listing(conn, "scoringdance", "s:undated", "Undated", None, None)
            _listing(conn, "scoringdance", "s:catalog-override", "Unrelated", None, None)
            assert project_map(conn, bundle, NOW, "test", 20)
        generated = event_id(date(2026, 6, 7), "New Weekend")
        assert _rows(
            conn,
            "SELECT source,source_ref,event_id,match_method,match_confidence "
            "FROM source_event_map ORDER BY 1,2",
        ) == [
            ("eepro", "e:match", "catalog-a", "name_date", 1.0),
            ("eepro", "e:new", generated, "name_date", 0.5),
            ("eepro", "e:override", "2026-07-override-weekend", "override", 1.0),
            ("scoringdance", "s:catalog-override", "catalog-b", "override", 1.0),
        ]
        assert _rows(
            conn,
            "SELECT event_id,name,start_date,end_date,wsdc_status,sources,source,snapshot_id "
            "FROM events WHERE wsdc_status='unknown' ORDER BY 1",
        ) == [
            (
                "2026-06-new-weekend",
                "New Weekend",
                "2026-06-05",
                "2026-06-07",
                "unknown",
                '["eepro"]',
                "eepro",
                "listing",
            ),
            (
                "2026-07-override-weekend",
                "Override Weekend",
                "2026-07-01",
                "2026-07-31",
                "unknown",
                '["eepro"]',
                "eepro",
                "override",
            ),
        ]
        # Each placeholder is its resolution's; the override that names a
        # catalog event owns that row too, as the map's override scope did.
        assert _rows(
            conn,
            "SELECT scope_id,json_extract(record_key,'$[0]') FROM canonical_scope_rows "
            "WHERE scope_kind='resolution' ORDER BY 1",
        ) == [
            ("2026-06-new-weekend", "2026-06-new-weekend"),
            ("2026-07-override-weekend", "2026-07-override-weekend"),
            ("catalog-b", "catalog-b"),
        ]
        assert _rows(
            conn, "SELECT source,count(*) FROM source_event_proposals GROUP BY 1 ORDER BY 1"
        ) == [("eepro", 3), ("scoringdance", 1)]
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_a_placeholder_that_gains_a_catalog_match_moves_to_the_catalog_event(tmp_path):
    bundle = SimpleNamespace(files={})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            _listing(conn, "eepro", "e:1", "Late Listing", "2026-08-01", "2026-08-02")
            assert project_map(conn, bundle, NOW, "test", 20)
        placeholder = "2026-08-late-listing"
        assert conn.execute("SELECT 1 FROM events WHERE event_id=?", (placeholder,)).fetchone()
        with db.transaction():
            _calendar(conn, _event("calendar-late", "Late Listing", "2026-08-01", "2026-08-02"))
            assert project_map(conn, bundle, NOW, "test", 20)
        assert _rows(conn, "SELECT source_ref,event_id,match_method FROM source_event_map") == [
            ("e:1", "calendar-late", "name_date")
        ]
        assert (
            conn.execute("SELECT 1 FROM events WHERE event_id=?", (placeholder,)).fetchone() is None
        )
        assert _rows(
            conn,
            "SELECT scope_kind,scope_id FROM canonical_scope_rows "
            "WHERE record_key=json_array('calendar-late')",
        ) == [("calendar", "2026")]
        assert not conn.execute(
            "SELECT 1 FROM canonical_scope_rows WHERE scope_kind='resolution'"
        ).fetchone()


def test_a_listing_on_two_index_pages_maps_once(tmp_path):
    bundle = SimpleNamespace(files={})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            _listing(conn, "scoringdance", "s:1", "Paged Weekend", "2026-02-01", "2026-02-02")
            conn.executemany(
                "INSERT INTO source_event_scope_rows VALUES (?,'scoringdance','s:1')",
                [("scoringdance:1",), ("scoringdance:2",)],
            )
            assert project_map(conn, bundle, NOW, "test", 20)
        assert _rows(conn, "SELECT source,source_ref FROM source_event_proposals") == [
            ("scoringdance", "s:1")
        ]
        assert _rows(conn, "SELECT source_ref,event_id FROM source_event_map") == [
            ("s:1", "2026-02-paged-weekend")
        ]
        mapping = WorkUnit("project", "mapping", "scoringdance")
        assert dependencies.mapping_units(conn) == (mapping,)
        assert {
            unit
            for unit in dependencies.prerequisites(conn, mapping)
            if unit.unit_kind == "source_index"
        } == {
            WorkUnit("project", "source_index", "scoringdance:1"),
            WorkUnit("project", "source_index", "scoringdance:2"),
        }


def _minted(before, after):
    """Scopes whose pointer moved or that are new: the units that re-derived."""
    return {key for key, generation in after.items() if before.get(key) != generation}


def test_a_new_listing_rederives_only_its_mapping_resolution_event_and_year(source_fixture):
    f = source_fixture
    drain_project(f)
    before = pointers(f.conn)
    with f.db.transaction():
        _listing(f.conn, "eepro", "eepro:new", "New Weekend", "2026-03-05", "2026-03-08")
        enqueue(
            f.conn,
            (WorkUnit("project", "mapping", "eepro"),),
            enqueued_at=f.corpus.clock.now().isoformat(),
        )
    drain_project(f)
    # The inventory re-runs on the new listing and cuts off; event-a's
    # resolution reads only the proposals naming event-a, so it stays current.
    # The new map row registers the listing's proxy unit.
    assert _minted(before, pointers(f.conn)) == {
        ("project", "mapping", "eepro"),
        ("project", "resolution", "2026-03-new-weekend"),
        ("project", "event", "2026-03-new-weekend"),
        ("project", "source_event", "eepro:new"),
        ("project", "year", "2026"),
    }


def test_an_override_edit_rederives_the_mapping_and_the_events_it_names(source_fixture):
    from swingset.state.inputs import accept, capture
    from swingset.versions import versions

    f = source_fixture
    drain_project(f)
    before = pointers(f.conn)
    f.aliases.write_text(
        "source,source_ref,event_id,note\neepro,eepro:test,event-b,Reviewed move\n"
    )
    f.bundle = capture(f.config, f.overrides, f.db.state_dir, versions())
    accept(f.db, f.bundle, f.corpus.clock)
    drain_project(f)
    minted = _minted(before, pointers(f.conn))
    # Every project unit re-ran (the overrides are in every project recipe);
    # only these changed output, with the listing's proxy and the year.
    named = {
        ("project", "mapping", "eepro"),
        ("project", "resolution", "event-a"),
        ("project", "resolution", "event-b"),
        ("project", "event", "event-a"),
        ("project", "event", "event-b"),
    }
    assert named <= minted
    assert minted - named <= {
        ("project", "source_event", "eepro:test"),
        ("project", "year", "2026"),
    }


def test_a_new_listing_for_a_known_event_makes_its_resolution_stale(source_fixture):
    """An existing resolution goes stale through its proposals content group:
    a second listing that names event-a (same name and dates) is proposed to
    it, so resolution/event-a re-derives and maps the new listing."""
    from swingset.state import derivations

    f = source_fixture
    drain_project(f)
    resolution = WorkUnit("project", "resolution", "event-a")
    before = derivations.selected_generation(f.conn, resolution)
    assert before is not None and derivations.current(f.conn, resolution)
    with f.db.transaction():
        _listing(f.conn, "scoringdance", "sd:event-a", "event-a", "2026-01-01", "2026-01-04")
        enqueue(
            f.conn,
            (WorkUnit("project", "mapping", "scoringdance"),),
            enqueued_at=f.corpus.clock.now().isoformat(),
        )
    drain_project(f)
    assert derivations.selected_generation(f.conn, resolution) != before
    assert ("scoringdance", "sd:event-a", "event-a", "name_date") in _rows(
        f.conn, "SELECT source,source_ref,event_id,match_method FROM source_event_map"
    )
