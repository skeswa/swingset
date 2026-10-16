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


# The series-and-month rule (D-0253): a listing's contests follow the
# inventory's series decision, so a results-site name that differs from the
# registry's lands on the registry occurrence and its placeholder goes.
SERIES_ALIASES = (
    b"printed_name,series_id,source,note\n"
    b"Liberty Swing,wsdc-96,eepro,reviewed\n"
    b"GNDC,wsdc-22,eepro,reviewed\n"
    b"MADjam,wsdc-92,eepro,reviewed\n"
)


def _snapshot(conn, snapshot, source):
    conn.execute(
        "INSERT OR IGNORE INTO watches(watch_id,source,kind,method,url,parser,state) "
        "VALUES (?,?,'index','GET','https://example.test/','events','live')",
        ("watch-" + snapshot, source),
    )
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,"
        "content_changed,run_id,classification) "
        "VALUES (?,?,'GET','https://example.test/',?,200,1,1,'test','Ok')",
        (snapshot, "watch-" + snapshot, NOW),
    )


def _registry(conn, *occurrences):
    """One dancer whose placements report each (registry id, printed name, month)."""
    from swingset.model.observations import encode_payload
    from swingset.project.registry import project_dancer
    from swingset.sources.records import DancerLookup, RegistryPlacement

    payload = DancerLookup(
        "dancer_lookup",
        "found",
        1,
        1,
        first_name="Test",
        last_name="Dancer",
        primary_role_raw="L",
        placements=tuple(
            RegistryPlacement("leader", "NOV", number, name, month, "1", 1, "wcs")
            for number, name, month in occurrences
        ),
    )
    _snapshot(conn, "registry", "wsdc_registry")
    conn.execute(
        "INSERT INTO observations VALUES ('obs-registry','watch-registry','registry',?,"
        "'dancer','1',0,'1','1',?)",
        (payload.kind, encode_payload(payload)),
    )
    replace_scope(
        conn,
        scope_kind="dancer",
        scope_id="1",
        projection=project_dancer(conn, "1", NOW, "test"),
        run_id="test",
        projected_at=NOW,
    )


def _inventory(conn, aliases=SERIES_ALIASES):
    from swingset.project.history import prepare_inventory

    prepare_inventory(conn, now=NOW, run_id="test", aliases=aliases)


def _held(identifier, series, month, start, end, sources=("eepro",)):
    return Event(
        event_id=identifier,
        series_id=series,
        name=identifier,
        year=int(month[:4]),
        start_date=start,
        end_date=end,
        event_month=month,
        held="held",
        wsdc_status="registry",
        sources=sources,
        source=sources[0],
        snapshot_id="held",
        parser_version="1",
        first_seen_at=NOW,
        last_seen_at=NOW,
        run_id="test",
    )


def test_result_site_listings_attach_to_their_registry_occurrence(tmp_path):
    """Liberty (a calendar row, then a placeholder), GNDC (reported the month
    after it ended) and MADjam (a placeholder the registry adopts) each leave
    one event with the contests' listing and the registry points, status
    `registry`; no placeholder survives."""
    bundle = SimpleNamespace(files={"overrides/series_aliases.csv": SERIES_ALIASES})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            _calendar(
                conn,
                _event(
                    "2026-06-liberty-swing-dance-championships",
                    "Liberty Swing Dance Championships",
                    "2026-06-12",
                    "2026-06-14",
                ),
            )
            _snapshot(conn, "listing", "eepro")
            _listing(conn, "eepro", "eepro:liberty", "Liberty Swing", "2026-06-12", "2026-06-14")
            _listing(conn, "eepro", "eepro:madjam", "MADjam", "2026-03-06", "2026-03-08")
            # The results site lists the event before the registry reports it.
            assert project_map(conn, bundle, NOW, "test", 20)
        assert _rows(conn, "SELECT source_ref,event_id FROM source_event_map ORDER BY 1") == [
            ("eepro:liberty", "2026-06-liberty-swing"),
            ("eepro:madjam", "2026-03-madjam"),
        ]
        with db.transaction():
            _registry(
                conn,
                ("96", "Liberty Swing Dance Championships", "June 2026"),
                ("22", "USA Grand National Dance Championships", "June 2026"),
                ("92", "MADjam - Mid Atlantic Dance Jam", "March 2026"),
            )
            _listing(conn, "eepro", "eepro:gndc", "GNDC", "2026-05-29", "2026-05-31")
            _inventory(conn)
            assert project_map(conn, bundle, NOW, "test", 20)
        assert _rows(
            conn,
            "SELECT source_ref,event_id,match_method,match_confidence FROM source_event_map "
            "ORDER BY 1",
        ) == [
            ("eepro:gndc", "2026-06-usa-grand-national-dance-championships", "alias", 1.0),
            ("eepro:liberty", "2026-06-liberty-swing-dance-championships", "alias", 1.0),
            ("eepro:madjam", "2026-03-madjam", "alias", 1.0),
        ]
        assert _rows(
            conn,
            "SELECT event_id,series_id,held,wsdc_status,sources FROM events ORDER BY 1",
        ) == [
            ("2026-03-madjam", "wsdc-92", "held", "registry", '["eepro","wsdc_registry"]'),
            (
                "2026-06-liberty-swing-dance-championships",
                "wsdc-96",
                "held",
                "registry",
                '["eepro","wsdc_registry"]',
            ),
            (
                "2026-06-usa-grand-national-dance-championships",
                "wsdc-22",
                "held",
                "registry",
                '["eepro","wsdc_registry"]',
            ),
        ]
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_two_series_with_a_shared_word_stay_apart(tmp_path):
    """South Bay Dance Fling and Swing Fling: each listing follows its own
    registry series by printed name, never the other's."""
    bundle = SimpleNamespace(files={})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            _registry(
                conn,
                ("48", "South Bay Dance Fling", "September 2026"),
                ("59", "Swing Fling", "August 2026"),
            )
            _snapshot(conn, "listing", "scoringdance")
            _listing(conn, "scoringdance", "sd:fling", "Swing Fling", "2026-08-14", "2026-08-16")
            _listing(
                conn,
                "scoringdance",
                "sd:south-bay",
                "South Bay Dance Fling",
                "2026-09-11",
                "2026-09-13",
            )
            _inventory(conn, b"printed_name,series_id,source,note\n")
            assert project_map(conn, bundle, NOW, "test", 20)
        assert _rows(
            conn,
            "SELECT m.source_ref,m.event_id,e.series_id FROM source_event_map m "
            "JOIN events e USING(event_id) ORDER BY 1",
        ) == [
            ("sd:fling", "2026-08-swing-fling", "wsdc-59"),
            ("sd:south-bay", "2026-09-south-bay-dance-fling", "wsdc-48"),
        ]


def test_the_series_rule_refuses_ambiguous_months_and_disjoint_dates(tmp_path):
    """Two held occurrences of one series in the window, or an occurrence
    whose dates miss the listing's, attach nothing: the listing falls back to
    its name, here a placeholder. So does a listing of a month the series did
    not hold, whatever its name."""
    aliases = (
        b"printed_name,series_id,source,note\n"
        b"WCS Party,wsdc-357,scoringdance,reviewed\n"
        b"Liberty Swing,wsdc-96,eepro,reviewed\n"
    )
    bundle = SimpleNamespace(files={"overrides/series_aliases.csv": aliases})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            _calendar(
                conn,
                _held(
                    "2026-09-wcsparty",
                    "wsdc-357",
                    "2026-09",
                    "2026-09-18",
                    "2026-09-20",
                    ("scoringdance",),
                ),
                _held(
                    "2026-09-wcs-party-in-vienna",
                    "wsdc-357",
                    "2026-09",
                    "2026-09-18",
                    "2026-09-20",
                    ("scoringdance",),
                ),
                _held("2026-06-liberty", "wsdc-96", "2026-06", "2026-06-12", "2026-06-14"),
            )
            _listing(conn, "scoringdance", "sd:party", "WCS Party", "2026-09-18", "2026-09-20")
            # A second Liberty listing two weeks later, and one a year later.
            _listing(conn, "eepro", "e:late", "Liberty Swing", "2026-06-26", "2026-06-28")
            _listing(conn, "eepro", "e:next-year", "Liberty Swing", "2027-06-11", "2027-06-13")
            # The edition itself does attach.
            _listing(conn, "eepro", "e:liberty", "Liberty Swing", "2026-06-12", "2026-06-14")
            assert project_map(conn, bundle, NOW, "test", 20)
        assert _rows(
            conn, "SELECT source_ref,event_id,match_method FROM source_event_map ORDER BY 1"
        ) == [
            ("e:late", "2026-06-liberty-swing", "name_date"),
            ("e:liberty", "2026-06-liberty", "alias"),
            ("e:next-year", "2027-06-liberty-swing", "name_date"),
            ("sd:party", "2026-09-wcs-party", "name_date"),
        ]


def test_the_series_rule_needs_the_inventory_to_have_attached_the_source(tmp_path):
    """An occurrence the inventory gave no listing of this source (say, it
    left two dated editions for review) takes no contests from it."""
    aliases = b"printed_name,series_id,source,note\nLiberty Swing,wsdc-96,eepro,reviewed\n"
    bundle = SimpleNamespace(files={"overrides/series_aliases.csv": aliases})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            _calendar(
                conn,
                _held(
                    "2026-06-liberty",
                    "wsdc-96",
                    "2026-06",
                    "2026-06-12",
                    "2026-06-14",
                    ("wsdc_calendar",),
                ),
            )
            _listing(conn, "eepro", "e:liberty", "Liberty Swing", "2026-06-12", "2026-06-14")
            assert project_map(conn, bundle, NOW, "test", 20)
        assert _rows(conn, "SELECT source_ref,event_id,match_method FROM source_event_map") == [
            ("e:liberty", "2026-06-liberty-swing", "name_date")
        ]


def _aloha_registry(conn, *months):
    """Dancer 1's registry page reporting The Aloha Open (wsdc-355) in each month."""
    from swingset.model.observations import encode_payload
    from swingset.project.registry import project_dancer
    from swingset.sources.records import DancerLookup, RegistryPlacement

    payload = DancerLookup(
        "dancer_lookup",
        "found",
        1,
        1,
        first_name="T",
        last_name="D",
        primary_role_raw="L",
        placements=tuple(
            RegistryPlacement("leader", "NOV", "355", "The Aloha Open", month, "1", 1, "wcs")
            for month in months
        ),
    )
    snapshot = f"registry-{len(months)}"
    conn.execute("DELETE FROM observations WHERE scope_kind='dancer'")
    _snapshot(conn, snapshot, "wsdc_registry")
    conn.execute(
        "INSERT INTO observations VALUES (?,?,?,?,'dancer','1',0,'1','1',?)",
        ("obs-" + snapshot, "watch-" + snapshot, snapshot, payload.kind, encode_payload(payload)),
    )
    replace_scope(
        conn,
        scope_kind="dancer",
        scope_id="1",
        projection=project_dancer(conn, "1", NOW, "test"),
        run_id="test",
        projected_at=NOW,
    )


def test_a_listing_seen_before_its_registry_occurrence_joins_the_calendar_edition(tmp_path):
    """The Aloha Open 2026: scoring.dance listed it, under a series the
    registry knew, before the registry reported the edition, so the
    inventory made a `listed` row beside the calendar's. When the registry
    reports it, the calendar edition becomes the occurrence, and the
    listing, its contests and the placements follow it (D-0256)."""
    from swingset.model.observations import encode_payload
    from swingset.project.events import project_calendar
    from swingset.project.history import finalize_history, prepare_inventory
    from swingset.sources.records import CalendarRow

    aliases = (
        b"printed_name,series_id,source,note\nAloha Open 2025,wsdc-355,scoringdance,reviewed\n"
    )
    bundle = SimpleNamespace(files={"overrides/series_aliases.csv": aliases})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            _aloha_registry(conn, "October 2025")
            prepare_inventory(conn, now=NOW, run_id="test", aliases=aliases)
            row = CalendarRow(
                "calendar_row",
                "The Aloha Open",
                "2026-09-23",
                "2026-09-27",
                "Registry Event",
                "Wailea, Hawaii/Maui, USA",
                "http://www.alohaopenwcs.com",
                "USA",
                (),
            )
            _snapshot(conn, "cal", "wsdc_calendar")
            conn.execute(
                "INSERT INTO observations VALUES ('obs-cal','watch-cal','cal',?,'calendar','wsdc',0,'1','1',?)",
                (row.kind, encode_payload(row)),
            )
            replace_scope(
                conn,
                scope_kind="calendar",
                scope_id="wsdc",
                projection=project_calendar(conn, "wsdc", NOW, "test"),
                run_id="test",
                projected_at=NOW,
            )
            _snapshot(conn, "listing", "scoringdance")
            _listing(
                conn,
                "scoringdance",
                "scoringdance:375",
                "Aloha Open 2026",
                "2026-09-23",
                "2026-09-23",
            )
            prepare_inventory(conn, now=NOW, run_id="test", aliases=aliases)
            project_map(conn, bundle, NOW, "test", 20)
            assert _rows(
                conn, "SELECT event_id,series_id,held FROM events WHERE year=2026 ORDER BY 1"
            ) == [
                # One unreported edition: the results listing joins the
                # calendar's row (D-0260).
                ("2026-09-the-aloha-open", "slug-the-aloha-open", "listed"),
            ]
            _aloha_registry(conn, "October 2025", "September 2026")
            # A new listing changes the mapping's inputs, as in production.
            _listing(
                conn, "scoringdance", "scoringdance:999", "Other 2026", "2026-01-10", "2026-01-10"
            )
            prepare_inventory(conn, now=NOW, run_id="test", aliases=aliases)
            project_map(conn, bundle, NOW, "test", 20)
            finalize_history(conn, now=NOW)
        assert _rows(
            conn,
            "SELECT event_id,series_id,held,wsdc_status FROM events WHERE held='held' AND year=2026",
        ) == [("2026-09-the-aloha-open", "wsdc-355", "held", "registry")]
        assert _rows(
            conn,
            "SELECT event_id,match_method FROM source_event_map WHERE source_ref='scoringdance:375'",
        ) == [("2026-09-the-aloha-open", "alias")]
        assert _rows(
            conn, "SELECT DISTINCT event_id FROM registry_placements WHERE event_month LIKE '2026%'"
        ) == [("2026-09-the-aloha-open",)]


def test_an_unreported_editions_results_join_its_calendar_row(tmp_path):
    """The Aloha Open 2024 was a trial: the registry reported no occurrence.
    Its scoring.dance listing and its calendar listing are one edition, so
    the results go to the calendar's row, which the release carries, and no
    second event appears (D-0260)."""
    from swingset.model.observations import encode_payload
    from swingset.project.history import prepare_inventory
    from swingset.sources.records import CalendarRow

    aliases = (
        b"printed_name,series_id,source,note\n"
        b"Aloha Open 2024,wsdc-355,scoringdance,reviewed\n"
        b"The Aloha Open,wsdc-355,wsdc_calendar,reviewed\n"
    )
    bundle = SimpleNamespace(files={"overrides/series_aliases.csv": aliases})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            row = CalendarRow(
                "calendar_row",
                "The Aloha Open",
                "2024-09-25",
                "2024-09-29",
                "Trial Event",
                "Maui, Hawaii, USA",
                "http://www.alohaopenwcs.com",
                "USA",
                ("event-trial",),
            )
            _snapshot(conn, "cal", "wsdc_calendar")
            conn.execute(
                "INSERT INTO observations VALUES ('obs-cal','watch-cal','cal',?,'calendar','wsdc-history',0,'1','1',?)",
                (row.kind, encode_payload(row)),
            )
            _snapshot(conn, "listing", "scoringdance")
            _listing(
                conn, "scoringdance", "sd:aloha", "Aloha Open 2024", "2024-09-25", "2024-09-25"
            )
            prepare_inventory(conn, now=NOW, run_id="test", aliases=aliases)
            project_map(conn, bundle, NOW, "test", 20)
        assert _rows(conn, "SELECT source_ref,event_id,match_method FROM source_event_map") == [
            ("sd:aloha", "2024-09-the-aloha-open", "alias")
        ]
        assert _rows(
            conn, "SELECT event_id,series_id,held,wsdc_status,sources,city FROM events ORDER BY 1"
        ) == [
            (
                "2024-09-the-aloha-open",
                "wsdc-355",
                "listed",
                "trial",
                '["scoringdance","wsdc_calendar"]',
                "Maui",
            )
        ]


def test_a_results_listing_inside_a_cross_month_span_joins_its_occurrence(tmp_path):
    """SwingCouver 2023/24 ran from Dec 28 to Jan 1; the registry reports it
    in January, scoring.dance dates it Dec 28. The listing joins the
    occurrence: its source is on the row although the widest calendar span
    gives the dates, so the results follow it, and the row keeps the
    registry's name, the full span and the registry, calendar and platform
    sources (D-0260)."""
    from swingset.model.observations import encode_payload
    from swingset.project.history import prepare_inventory
    from swingset.sources.records import CalendarRow

    aliases = (
        b"printed_name,series_id,source,note\nSwingCouver 2023/24,wsdc-196,scoringdance,reviewed\n"
    )
    bundle = SimpleNamespace(files={"overrides/series_aliases.csv": aliases})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            _registry(conn, ("196", "SwingCouver", "January 2024"))
            row = CalendarRow(
                "calendar_row",
                "SwingCouver",
                "2023-12-28",
                "2024-01-01",
                "Registry Event",
                "Vancouver, WA, USA",
                None,
                "USA",
                (),
            )
            _snapshot(conn, "cal", "wsdc_calendar")
            conn.execute(
                "INSERT INTO observations VALUES ('obs-cal','watch-cal','cal',?,'calendar','wsdc-history',0,'1','1',?)",
                (row.kind, encode_payload(row)),
            )
            conn.execute(
                "UPDATE snapshots SET observed_at='2023-11-01T00:00:00Z' WHERE snapshot_id='cal'"
            )
            _snapshot(conn, "listing", "scoringdance")
            _listing(
                conn, "scoringdance", "sd:couver", "SwingCouver 2023/24", "2023-12-28", "2023-12-28"
            )
            prepare_inventory(conn, now=NOW, run_id="test", aliases=aliases)
            project_map(conn, bundle, NOW, "test", 20)
        assert _rows(conn, "SELECT source_ref,event_id,match_method FROM source_event_map") == [
            ("sd:couver", "2024-01-swingcouver", "alias")
        ]
        assert _rows(
            conn,
            "SELECT event_id,name,start_date,end_date,held,sources,history_source FROM events "
            "ORDER BY 1",
        ) == [
            (
                "2024-01-swingcouver",
                "SwingCouver",
                "2023-12-28",
                "2024-01-01",
                "held",
                '["scoringdance","wsdc_calendar","wsdc_registry"]',
                '["calendar","platform","registry"]',
            )
        ]


def _unheld(conn, event_id, series, name, start, end, sources, history):
    conn.execute(
        "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,"
        "source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id,event_month,"
        "date_precision,held,coverage_tier,history_source) VALUES (?,?,?,?,?,?,'trial',?,"
        "'wsdc_calendar','s','1',?,?,'test',?,'day','listed','index_only',?)",
        (event_id, series, name, int(end[:4]), start, end, sources, NOW, NOW, end[:7], history),
    )


def test_an_unreported_editions_results_never_go_to_another_series(tmp_path):
    """With no row of its own series or name, WCSParty 2024 (Vienna, Sep 26)
    does not go to the one other row of the month that has scoring.dance
    results, The Aloha Open (Maui); Aloha Open 2024 goes to the calendar's
    row, whose own name gives its series (D-0260)."""
    aliases = (
        b"printed_name,series_id,source,note\n"
        b"WCSParty 2024,wsdc-357,scoringdance,reviewed\n"
        b"Aloha Open 2024,wsdc-355,scoringdance,reviewed\n"
        b"The Aloha Open,wsdc-355,wsdc_calendar,reviewed\n"
    )
    bundle = SimpleNamespace(files={"overrides/series_aliases.csv": aliases})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            _unheld(
                conn,
                "2024-09-the-aloha-open",
                "slug-the-aloha-open",
                "The Aloha Open",
                "2024-09-25",
                "2024-09-29",
                '["scoringdance","wsdc_calendar"]',
                '["calendar","platform"]',
            )
            _snapshot(conn, "listing", "scoringdance")
            _listing(conn, "scoringdance", "sd:party", "WCSParty 2024", "2024-09-26", "2024-09-26")
            _listing(
                conn, "scoringdance", "sd:aloha", "Aloha Open 2024", "2024-09-25", "2024-09-25"
            )
            project_map(conn, bundle, NOW, "test", 20)
        assert _rows(
            conn, "SELECT source_ref,event_id,match_method FROM source_event_map ORDER BY 1"
        ) == [
            ("sd:aloha", "2024-09-the-aloha-open", "alias"),
            ("sd:party", "2024-09-wcsparty", "name_date"),
        ]


def test_a_results_listing_a_day_before_the_calendar_span_joins_the_edition(tmp_path):
    """scoring.dance dates WCS Party 2024 Sep 26, the calendar Sep 27 to 30:
    the inventory and the map apply the same one-day test, so the edition is
    one row with the results; a listing three weeks earlier stays apart
    (D-0260)."""
    from swingset.model.observations import encode_payload
    from swingset.project.history import prepare_inventory
    from swingset.sources.records import CalendarRow

    aliases = (
        b"printed_name,series_id,source,note\n"
        b"WCSParty 2024,wsdc-357,scoringdance,reviewed\n"
        b"WCS Party Warmup 2024,wsdc-357,scoringdance,reviewed\n"
        b"WCS Party,wsdc-357,wsdc_calendar,reviewed\n"
    )
    bundle = SimpleNamespace(files={"overrides/series_aliases.csv": aliases})
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('test',?,0)", (NOW,))
        with db.transaction():
            row = CalendarRow(
                "calendar_row",
                "WCS Party",
                "2024-09-27",
                "2024-09-30",
                "Trial Event",
                "Vienna, Austria",
                None,
                "AUT",
                (),
            )
            _snapshot(conn, "cal", "wsdc_calendar")
            conn.execute(
                "INSERT INTO observations VALUES ('obs-cal','watch-cal','cal',?,'calendar','wsdc-history',0,'1','1',?)",
                (row.kind, encode_payload(row)),
            )
            _snapshot(conn, "listing", "scoringdance")
            _listing(conn, "scoringdance", "sd:party", "WCSParty 2024", "2024-09-26", "2024-09-26")
            _listing(
                conn,
                "scoringdance",
                "sd:warmup",
                "WCS Party Warmup 2024",
                "2024-09-05",
                "2024-09-05",
            )
            prepare_inventory(conn, now=NOW, run_id="test", aliases=aliases)
            project_map(conn, bundle, NOW, "test", 20)
        assert _rows(
            conn, "SELECT source_ref,event_id,match_method FROM source_event_map ORDER BY 1"
        ) == [
            ("sd:party", "2024-09-wcs-party", "alias"),
            ("sd:warmup", "2024-09-wcs-party-warmup", "alias"),
        ]
        assert _rows(conn, "SELECT event_id,sources FROM events ORDER BY 1") == [
            ("2024-09-wcs-party", '["scoringdance","wsdc_calendar"]'),
            ("2024-09-wcs-party-warmup", '["scoringdance"]'),
        ]
