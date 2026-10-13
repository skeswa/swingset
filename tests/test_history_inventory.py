from datetime import datetime
from types import SimpleNamespace

import pytest

from swingset.clock import FakeClock
from swingset.model.observations import encode_payload
from swingset.project.history import accept_year, phase_two_allowed, reconcile_history
from swingset.project.registry import project_dancer
from swingset.project.writer import replace_scope
from swingset.sources.records import CalendarRow, DancerLookup, RegistryPlacement
from swingset.state.db import open_database

NOW = "2026-09-12T00:00:00+00:00"


def evidence(conn, payload, *, source="wsdc_registry", scope="dancer", ref="1", snapshot="snap"):
    watch = "watch-" + snapshot
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES (?,?,'index','GET','https://example.test/','events','live')",
        (watch, source),
    )
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES (?,?,'GET','https://example.test/',?,200,1,1,'run','Ok')",
        (snapshot, watch, NOW),
    )
    conn.execute(
        "INSERT INTO observations VALUES (?,?,?,?,?,?,0,'1','1',?)",
        ("obs-" + snapshot, watch, snapshot, payload.kind, scope, ref, encode_payload(payload)),
    )


def registry(conn):
    payload = DancerLookup(
        "dancer_lookup",
        "found",
        1,
        1,
        first_name="Test",
        last_name="Dancer",
        primary_role_raw="L",
        placements=(
            RegistryPlacement("leader", "NOV", "7", "Example Swing", "August 2012", "1", 1, "wcs"),
            RegistryPlacement("leader", "NOV", "7", "Example Swing", "August 2009", "1", 1, "wcs"),
        ),
    )
    evidence(conn, payload)
    replace_scope(
        conn,
        scope_kind="dancer",
        scope_id="1",
        projection=project_dancer(conn, "1", NOW, "run"),
        run_id="run",
        projected_at=NOW,
    )


def drain_projection(db):
    from swingset.project.process import process_unit
    from swingset.state.derivations import pending_units
    from swingset.state.work import next_work

    clock = FakeClock(datetime.fromisoformat(NOW))
    for _ in range(30):
        unit = next_work(db.connection, "project", now=clock.now())
        if unit is None:
            break
        process_unit(db, unit, SimpleNamespace(files={}), clock, "run")
    assert list(pending_units(db.connection, "project")) == []


def test_occurrence_floor_dates_and_phase_two_gates(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES ('run',?,NULL,1,NULL)",
            (NOW,),
        )
        registry(conn)
        assert reconcile_history(conn, now=NOW, run_id="run")
        event = dict(conn.execute("SELECT * FROM events").fetchone())
        assert event["event_id"] == "2012-08-example-swing"
        assert event["start_date"] is None and event["end_date"] is None
        assert event["date_precision"] == "month"
        assert (
            conn.execute(
                "SELECT event_id FROM registry_placements WHERE event_month LIKE '2009%' "
            ).fetchone()[0]
            is None
        )
        listing = CalendarRow(
            "calendar_row",
            "Example Swing",
            "2012-07-27",
            "2012-07-29",
            "registry",
            "City",
            None,
            None,
            (),
        )
        evidence(
            conn, listing, source="wsdc_calendar", scope="calendar", ref="wsdc", snapshot="listing"
        )
        assert reconcile_history(conn, now=NOW, run_id="run")
        event = dict(conn.execute("SELECT * FROM events").fetchone())
        assert event["event_id"] == "2012-08-example-swing"
        assert event["event_month"] == "2012-08"
        assert event["end_date"] == "2012-07-29" and event["date_precision"] == "day"
        assert event["source"] == "wsdc_calendar" and event["snapshot_id"] == "listing"
        assert set(__import__("json").loads(event["history_source"])) == {"registry", "calendar"}
        assert not reconcile_history(conn, now=NOW, run_id="run")
        drain_projection(db)
        accept_year(conn, 2012, accepted_by="owner", accepted_at=NOW)
        assert not phase_two_allowed(conn, 2012)
        conn.executemany(
            "INSERT INTO meta VALUES (?, 'true')", [("h7_deployed",), ("h10_deployed",)]
        )
        assert phase_two_allowed(conn, 2012)
        conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id=NULL WHERE stage='project' AND unit_kind='year'"
        )
        # A pending year writes no inventory digest column, so it neither
        # closes phase two nor blocks acceptance (D-0198).
        assert phase_two_allowed(conn, 2012)
        accept_year(conn, 2012, accepted_by="owner", accepted_at=NOW)
        conn.execute(
            "UPDATE derivation_scopes SET materialized_generation_id=NULL WHERE stage='project' AND unit_kind='inventory'"
        )
        assert not phase_two_allowed(conn, 2012)
        with pytest.raises(ValueError, match="projection is pending"):
            accept_year(conn, 2012, accepted_by="owner", accepted_at=NOW)
        conn.execute("DELETE FROM pending_work WHERE stage='project'")
        conn.execute("UPDATE events SET end_date='2012-07-30'")
        assert not phase_two_allowed(conn, 2012)


def test_unresolved_names_are_findings_and_listing_alias_is_reviewed(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES ('run',?,NULL,1,NULL)",
            (NOW,),
        )
        registry(conn)
        listing = CalendarRow(
            "calendar_row",
            "Other Festival",
            "2012-09-01",
            "2012-09-03",
            "trial",
            "City",
            None,
            None,
            ("cancelled",),
        )
        evidence(
            conn, listing, source="wsdc_calendar", scope="calendar", ref="wsdc", snapshot="listing"
        )
        reconcile_history(conn, now=NOW, run_id="run")
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM findings WHERE kind='series_alias' AND closed_at IS NULL"
            ).fetchone()[0]
            == 1
        )
        drain_projection(db)
        with pytest.raises(ValueError, match="unresolved"):
            accept_year(conn, 2012, accepted_by="owner", accepted_at=NOW)
        reconcile_history(
            conn,
            now=NOW,
            run_id="run",
            aliases=b"printed_name,series_id,source,note\nOther Festival,listed-other-festival,calendar,reviewed\n",
        )
        event = conn.execute(
            "SELECT held,wsdc_status FROM events WHERE event_id='2012-09-other-festival'"
        ).fetchone()
        assert tuple(event) == ("cancelled", "trial")
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM findings WHERE kind='series_alias' AND closed_at IS NULL"
            ).fetchone()[0]
            == 0
        )


def test_rolling_calendar_keeps_registry_editions_and_legacy_ids(tmp_path):
    from swingset.project.events import project_calendar
    from swingset.project.writer import Projection

    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES ('run',?,NULL,1,NULL)",
            (NOW,),
        )
        listing = CalendarRow(
            "calendar_row",
            "Example Swing",
            "2012-07-27",
            "2012-07-29",
            "registry",
            "City",
            None,
            None,
            (),
        )
        evidence(
            conn, listing, source="wsdc_calendar", scope="calendar", ref="wsdc", snapshot="listing"
        )
        replace_scope(
            conn,
            scope_kind="calendar",
            scope_id="wsdc",
            projection=project_calendar(conn, "wsdc", NOW, "run"),
            run_id="run",
            projected_at=NOW,
        )
        registry(conn)
        reconcile_history(conn, now=NOW, run_id="run")
        event = conn.execute("SELECT event_id,event_month FROM events").fetchone()
        assert tuple(event) == ("2012-07-example-swing", "2012-08")
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        replace_scope(
            conn,
            scope_kind="calendar",
            scope_id="wsdc",
            projection=Projection(),
            run_id="run",
            projected_at=NOW,
        )
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_later_observed_listing_corrects_dates_without_losing_corroboration(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES ('run',?,NULL,1,NULL)",
            (NOW,),
        )
        registry(conn)
        old = CalendarRow(
            "calendar_row",
            "Example Swing",
            "2012-07-20",
            "2012-07-22",
            "registry",
            "City",
            None,
            None,
            (),
        )
        new = CalendarRow(
            "calendar_row",
            "Example Swing",
            "2012-07-27",
            "2012-07-29",
            "registry",
            "City",
            None,
            None,
            (),
        )
        for snapshot, payload, source, observed in [
            ("old", old, "wsdc_calendar", "2012-05-01"),
            ("new", new, "wsdc_calendar", "2012-06-01"),
            ("corroboration", old, "wsdc_newsletter", "2012-04-01"),
        ]:
            evidence(
                conn,
                payload,
                source=source,
                scope="calendar",
                ref="wsdc-history",
                snapshot=snapshot,
            )
            conn.execute(
                "UPDATE snapshots SET observed_at=? WHERE snapshot_id=?",
                (observed + "T00:00:00+00:00", snapshot),
            )
        reconcile_history(conn, now=NOW, run_id="run")
        event = conn.execute("SELECT end_date,snapshot_id,history_source FROM events").fetchone()
        assert event[0] == "2012-07-29" and event[1] == "new"
        assert set(__import__("json").loads(event[2])) == {"registry", "calendar", "newsletter"}
        assert not conn.execute(
            "SELECT 1 FROM findings WHERE kind='event_alias' AND closed_at IS NULL"
        ).fetchone()


def test_single_day_result_span_inside_listed_weekend_is_one_edition(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES ('run',?,NULL,1,NULL)",
            (NOW,),
        )
        registry(conn)
        weekend = CalendarRow(
            "calendar_row",
            "Example Swing",
            "2012-07-20",
            "2012-07-22",
            "registry",
            "City",
            None,
            None,
            (),
        )
        finals_day = CalendarRow(
            "calendar_row",
            "Example Swing",
            "2012-07-21",
            "2012-07-21",
            "registry",
            "City",
            None,
            None,
            (),
        )
        evidence(
            conn,
            weekend,
            source="wsdc_calendar",
            scope="calendar",
            ref="wsdc-history",
            snapshot="listing",
        )
        conn.execute(
            "INSERT INTO observations VALUES ('obs-day','watch-listing','listing','calendar_row','calendar','wsdc-history',1,'1','1',?)",
            (encode_payload(finals_day),),
        )
        reconcile_history(conn, now=NOW, run_id="run")
        # Nested spans are one edition dated by the widest span (D-0184).
        assert not conn.execute(
            "SELECT 1 FROM findings WHERE kind='event_alias' AND closed_at IS NULL"
        ).fetchone()
        assert tuple(
            conn.execute("SELECT start_date,end_date,date_precision FROM events").fetchone()
        ) == ("2012-07-20", "2012-07-22", "day")


def test_registry_month_beside_a_new_year_span_is_one_edition(tmp_path):
    from swingset.project.history import _widest_span

    # The registry files December points; the listing runs into January.
    assert _widest_span({("2010-12", "2010-12"), ("2010-12-30", "2011-01-01")}) == (
        "2010-12-30",
        "2011-01-01",
    )
    # A reporting month neither at the start nor the end stays ambiguous.
    assert _widest_span({("2014-03", "2014-03"), ("2014-04-17", "2014-04-20")}) is None
    # Partly overlapping dated spans stay ambiguous.
    assert _widest_span({("2012-09-05", "2012-09-08"), ("2012-09-06", "2012-09-09")}) is None
    # Nested dated spans resolve to the widest.
    assert _widest_span({("2012-07-21", "2012-07-21"), ("2012-07-20", "2012-07-22")}) == (
        "2012-07-20",
        "2012-07-22",
    )


def test_dated_alias_rows_pick_the_series_of_the_listing_month():
    from swingset.project.history import _alias_for, _alias_map

    body = (
        b"printed_name,series_id,source,note,from_month,to_month\n"
        b"Swingtime in the Rockies,wsdc-47,swingdancecouncil,current,,\n"
        b"Swingtime in the Rockies,wsdc-66,swingdancecouncil,2010 id,2010-01,2010-12\n"
        b"Swingtime in the Rockies,wsdc-66,swingdancecouncil,2014 to 2017 id,2014-01,2017-12\n"
    )
    aliases = _alias_map(body)
    slug = next(iter(aliases))
    assert _alias_for(aliases, slug, "2010-08") == "wsdc-66"
    assert _alias_for(aliases, slug, "2012-07") == "wsdc-47"
    assert _alias_for(aliases, slug, "2016-07") == "wsdc-66"
    assert _alias_for(aliases, slug, "2025-07") == "wsdc-47"
    assert _alias_for(aliases, "unknown-name", "2025-07") is None


def test_a_dismissed_finding_is_not_opened_again(tmp_path):
    from swingset.project.history import dismissed_findings

    reviews = (
        b"kind,subject_id,decision,reviewer,note\n"
        b"event_alias,2012-08-example-swing,dismiss,claude,one edition; listings disagree by a day\n"
    )
    assert dismissed_findings(reviews) == {("event_alias", "2012-08-example-swing")}
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES ('run',?,NULL,1,NULL)",
            (NOW,),
        )
        registry(conn)
        first = CalendarRow(
            "calendar_row",
            "Example Swing",
            "2012-07-20",
            "2012-07-22",
            "registry",
            "City",
            None,
            None,
            (),
        )
        second = CalendarRow(
            "calendar_row",
            "Example Swing",
            "2012-07-27",
            "2012-07-29",
            "registry",
            "City",
            None,
            None,
            (),
        )
        evidence(
            conn,
            first,
            source="wsdc_calendar",
            scope="calendar",
            ref="wsdc-history",
            snapshot="listing",
        )
        conn.execute(
            "INSERT INTO observations VALUES ('obs-second','watch-listing','listing','calendar_row','calendar','wsdc-history',1,'1','1',?)",
            (encode_payload(second),),
        )
        reconcile_history(conn, now=NOW, run_id="run", reviews=reviews)
        open_rows = [
            tuple(row)
            for row in conn.execute(
                "SELECT kind,subject_id,summary FROM findings WHERE kind='event_alias' AND closed_at IS NULL"
            )
        ]
        assert not open_rows, open_rows


def test_two_editions_in_same_capture_require_review(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES ('run',?,NULL,1,NULL)",
            (NOW,),
        )
        registry(conn)
        first = CalendarRow(
            "calendar_row",
            "Example Swing",
            "2012-07-20",
            "2012-07-22",
            "registry",
            "City",
            None,
            None,
            (),
        )
        second = CalendarRow(
            "calendar_row",
            "Example Swing",
            "2012-07-27",
            "2012-07-29",
            "registry",
            "City",
            None,
            None,
            (),
        )
        evidence(
            conn,
            first,
            source="wsdc_calendar",
            scope="calendar",
            ref="wsdc-history",
            snapshot="listing",
        )
        conn.execute(
            "INSERT INTO observations VALUES ('obs-second','watch-listing','listing','calendar_row','calendar','wsdc-history',1,'1','1',?)",
            (encode_payload(second),),
        )
        reconcile_history(conn, now=NOW, run_id="run")
        assert conn.execute(
            "SELECT 1 FROM findings WHERE kind='event_alias' AND closed_at IS NULL"
        ).fetchone()
        assert conn.execute("SELECT date_precision FROM events").fetchone()[0] == "month"


def _platform_listing(conn, source, ref, name, start, end, snapshot):
    conn.execute(
        "INSERT OR IGNORE INTO watches(watch_id,source,kind,method,url,parser,state) VALUES (?,?,'index','GET','https://example.test/','events','live')",
        ("watch-" + snapshot, source),
    )
    conn.execute(
        "INSERT OR IGNORE INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES (?,?,'GET','https://example.test/',?,200,1,1,'run','Ok')",
        (snapshot, "watch-" + snapshot, NOW),
    )
    conn.execute(
        "INSERT INTO source_events(source,source_ref,name_raw,start_date,end_date,url,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,?,?,?,'https://example.test/',?,'4',?,?,'run')",
        (source, ref, name, start, end, snapshot, NOW, NOW),
    )


def test_an_adopted_placeholder_is_a_registry_event(tmp_path):
    """The registry adopts a results placeholder as its occurrence; the row
    becomes `registry`, and a held row left `unknown` earlier is repaired
    (D-0253)."""
    from swingset.model.canonical import Event
    from swingset.project.writer import Projection

    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES ('run',?,NULL,1,NULL)",
            (NOW,),
        )
        placeholder = Event(
            event_id="2012-08-example-swing",
            series_id="slug-example-swing",
            name="Example Swing",
            year=2012,
            start_date="2012-08-03",
            end_date="2012-08-05",
            sources=("eepro",),
            source="eepro",
            snapshot_id="listing",
            parser_version="4",
            first_seen_at=NOW,
            last_seen_at=NOW,
            run_id="run",
        )
        replace_scope(
            conn,
            scope_kind="resolution",
            scope_id=placeholder.event_id,
            projection=Projection((placeholder,)),
            run_id="run",
            projected_at=NOW,
        )
        _platform_listing(
            conn, "eepro", "eepro:example", "Example Swing", "2012-08-03", "2012-08-05", "listing"
        )
        registry(conn)
        reconcile_history(conn, now=NOW, run_id="run")
        query = "SELECT event_id,series_id,held,wsdc_status FROM events"
        assert [tuple(row) for row in conn.execute(query)] == [
            ("2012-08-example-swing", "wsdc-7", "held", "registry")
        ]
        conn.execute("UPDATE events SET wsdc_status='unknown'")
        reconcile_history(conn, now=NOW, run_id="run")
        assert [tuple(row) for row in conn.execute(query)] == [
            ("2012-08-example-swing", "wsdc-7", "held", "registry")
        ]


def test_a_results_platform_listing_does_not_claim_registry_status(tmp_path):
    """A platform listing's event takes its status from its series, or from a
    calendar listing of the same edition, never a fixed `registry` (D-0253)."""
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES ('run',?,NULL,1,NULL)",
            (NOW,),
        )
        registry(conn)
        _platform_listing(
            conn, "eepro", "eepro:hustle", "NJ Hustle Congress", "2012-10-05", "2012-10-07", "e1"
        )
        _platform_listing(
            conn, "eepro", "eepro:future", "Future Swing", "2012-11-02", "2012-11-04", "e1"
        )
        calendar = CalendarRow(
            "calendar_row",
            "River Nights",
            "2012-09-14",
            "2012-09-16",
            "Trial",
            "City",
            None,
            None,
            (),
        )
        evidence(
            conn, calendar, source="wsdc_calendar", scope="calendar", ref="wsdc", snapshot="c1"
        )
        # The platform listing of the same edition is observed later and wins.
        _platform_listing(
            conn, "eepro", "eepro:river", "River Nights", "2012-09-14", "2012-09-16", "e2"
        )
        conn.execute(
            "UPDATE snapshots SET observed_at='2026-09-13T00:00:00+00:00' WHERE snapshot_id='e2'"
        )
        reconcile_history(
            conn,
            now=NOW,
            run_id="run",
            aliases=(
                b"printed_name,series_id,source,note\n"
                b"NJ Hustle Congress,listed-nj-hustle-congress,eepro,reviewed\n"
                b"Future Swing,wsdc-8,eepro,reviewed\n"
                b"River Nights,listed-river-nights,calendar,reviewed\n"
            ),
        )
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT event_id,source,wsdc_status FROM events WHERE held!='held' ORDER BY 1"
            )
        ] == [
            ("2012-09-river-nights", "eepro", "trial"),
            ("2012-10-nj-hustle-congress", "eepro", "unknown"),
            ("2012-11-future-swing", "eepro", "registry"),
        ]


# Years (plan F17): project/history/all became one unit per year.


def seed_years(conn):
    """Events and placements across three years, as the pure-function tests need.

    `dec` is a December 2024 event whose registry occurrence is reported in
    January 2025: year 2025 associates its placements, year 2024 counts them.
    One 2026 placement names a 2025 event (a stale association), one placement
    predates the history start, and two 2025 events share a series and month.
    """
    conn.execute(
        "INSERT INTO runs(run_id,started_at,finished_at,dry_run,summary_json) VALUES ('run',?,NULL,1,NULL)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) "
        "VALUES ('w','wsdc_registry','index','GET','https://example.test/','events','live')"
    )
    for snapshot, via in (("snap", "origin"), ("snap-archive", "wayback")):
        conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification,via) "
            "VALUES (?,'w','GET','https://example.test/',?,200,1,1,'run','Ok',?)",
            (snapshot, NOW, via),
        )
    for wsdc_id in (1, 2, 3):
        conn.execute(
            "INSERT INTO dancers(wsdc_id,first_name,last_name,name_norm,is_pro,primary_role,leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,leader_highest_level,leader_highest_points,follower_highest_level,follower_highest_points,recent_year,registry_internal_id,registry_fetched_at,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
            "VALUES (?,'A','B','a b',0,'leader','novice','advanced','novice','advanced','novice',1,'novice',1,2026,?,'t','wsdc_registry','snap','1',?,?,'run')",
            (wsdc_id, wsdc_id, NOW, NOW),
        )
    events = (
        # event_id, series, year, reporting month, history source, snapshot
        ("dec", "wsdc-1", 2024, "2025-01", '["registry"]', "snap"),
        ("mid24", "wsdc-4", 2024, "2024-06", '["calendar","registry"]', "snap-archive"),
        ("e25", "wsdc-2", 2025, "2025-06", '["registry"]', "snap"),
        ("e25b", "wsdc-2", 2025, "2025-06", '["calendar"]', "snap"),
        ("e26", "wsdc-3", 2026, "2026-01", '["registry"]', "snap"),
    )
    for event_id, series, year, month, history, snapshot in events:
        conn.execute(
            "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id,event_month,date_precision,held,coverage_tier,history_source) "
            "VALUES (?,?,?,?,NULL,NULL,'registry','[\"wsdc_registry\"]','wsdc_registry',?,'1',?,?,'run',?,'month','held','registry_only',?)",
            (event_id, series, event_id, year, snapshot, NOW, NOW, month, history),
        )
    placements = (
        # wsdc_id, series, month, division, event_id before association
        (1, "wsdc-1", "2025-01", "NOV", None),
        (2, "wsdc-1", "2025-01", "INT", None),
        (1, "wsdc-2", "2025-06", "NOV", None),
        (2, "wsdc-3", "2026-01", "NOV", None),
        (1, "wsdc-3", "2026-01", "INT", "e25"),
        (3, "wsdc-4", "2024-06", "NOV", None),
        (3, "wsdc-9", "2025-03", "NOV", None),
        (3, "wsdc-2", "2009-06", "NOV", "e25"),
    )
    for wsdc_id, series, month, division, event_id in placements:
        conn.execute(
            "INSERT INTO registry_placements VALUES (?,'leader','wcs',?,?,'Series',?,?,'1',1,'wsdc_registry',?,'1',?,?,'run')",
            (
                wsdc_id,
                division,
                series,
                month,
                event_id,
                "snap-archive" if series == "wsdc-3" else "snap",
                NOW,
                NOW,
            ),
        )
    conn.execute(
        "INSERT INTO contests(contest_id,event_id,name_raw,division,age_division,contest_type,partner_mode,dance_style,wsdc_points_eligible,combined_from,parse_status,source_contest_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
        "VALUES ('c25','e25','Novice','novice','open','jnj','random','wcs',1,'[]','parsed','c','eepro','snap-archive','1',?,?,'run')",
        (NOW, NOW),
    )
    conn.execute(
        "INSERT INTO rounds(round_id,contest_id,round_type,round_index,name_raw,scoring_method,callback_legend,judge_count,entry_count,source_round_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
        "VALUES ('r25','c25','final',0,'Final','relative','{}',3,5,'r','eepro','snap-archive','1',?,?,'run')",
        (NOW, NOW),
    )


def year_state(conn):
    return {
        "placements": conn.execute(
            "SELECT * FROM registry_placements ORDER BY wsdc_id,role,series_id,event_month,division"
        ).fetchall(),
        "events": conn.execute("SELECT * FROM events ORDER BY event_id").fetchall(),
        "coverage": conn.execute("SELECT * FROM coverage ORDER BY year,source,via").fetchall(),
    }


def test_each_year_associates_and_counts_what_the_global_pass_does(tmp_path):
    """Per-year association, tiers and coverage equal finalize_history's, with a
    December event whose placements are reported in January (plan F17)."""
    from swingset.project.history import finalize_history, finalize_year
    from swingset.state.derivation_dependencies import year_units

    with open_database(tmp_path / "global") as db:
        seed_years(db.connection)
        db.connection.execute(
            "INSERT INTO findings(finding_id,kind,subject_kind,subject_id,severity,summary,evidence_json,opened_at,owner_kind,owner_id,run_id) "
            "VALUES ('f','series_alias','history_listing','x','warning','s','{}',?,'history_year','2026','run')",
            (NOW,),
        )
        finalize_history(db.connection, now=NOW)
        expected = year_state(db.connection)
    with open_database(tmp_path / "years") as db:
        conn = db.connection
        seed_years(conn)
        conn.execute(
            "INSERT INTO findings(finding_id,kind,subject_kind,subject_id,severity,summary,evidence_json,opened_at,owner_kind,owner_id,run_id) "
            "VALUES ('f','series_alias','history_listing','x','warning','s','{}',?,'history_year','2026','run')",
            (NOW,),
        )
        years = [int(unit.unit_id) for unit in year_units(conn)]
        # Event years and reporting years with events or associations; the
        # stale 2009 association is its own year, and 2025-03 with no event
        # is none.
        assert years == [2009, 2024, 2025, 2026]
        # Any order: each year writes only its own rows.
        for year in reversed(years):
            finalize_year(conn, year, now=NOW)
        assert year_state(conn) == expected
    assert [
        tuple(row) for row in expected["placements"] if row["event_month"] == "2025-01"
    ] and all(
        row["event_id"] == "dec"
        for row in expected["placements"]
        if row["event_month"] == "2025-01"
    )
    # The December event counts in 2024 beside mid24, through its January
    # placements too.
    coverage = {(row["year"], row["source"], row["via"]): row for row in expected["coverage"]}
    assert coverage[2024, "wsdc_registry", "origin"]["events"] == 2
    assert coverage[2024, "wsdc_registry", "wayback"]["events"] == 1
    # The pre-start association stays as the dancer left it.
    assert [
        row["event_id"] for row in expected["placements"] if row["event_month"] == "2009-06"
    ] == ["e25"]


def test_year_coverage_equals_the_global_rebuild(tmp_path):
    from swingset.project.coverage import rebuild_coverage, rebuild_year_coverage

    with open_database(tmp_path / "global") as db:
        seed_years(db.connection)
        rebuild_coverage(db.connection, now=NOW)
        expected = year_state(db.connection)
    with open_database(tmp_path / "years") as db:
        conn = db.connection
        seed_years(conn)
        for year in (2024, 2025, 2026, 2009):
            assert rebuild_year_coverage(conn, year, now=NOW) is (year != 2009)
        assert year_state(conn) == expected
        # Unchanged rows keep their clock; a second pass changes nothing.
        for year in (2024, 2025, 2026):
            assert not rebuild_year_coverage(conn, year, now="2026-10-01T00:00:00+00:00")
        assert year_state(conn) == expected
        # A year whose events left clears only its own rows.
        conn.execute("UPDATE events SET year=2025 WHERE year=2024")
        assert rebuild_year_coverage(conn, 2024, now=NOW)
        assert not conn.execute("SELECT 1 FROM coverage WHERE year=2024").fetchone()
        assert conn.execute("SELECT 1 FROM coverage WHERE year=2026").fetchone()


def derived_years(db):
    """`seed_years`, every project unit drained, and the years it made."""
    from swingset.state import derivations

    seed_years(db.connection)
    drain_projection(db)
    return {
        unit.unit_id: derivations.selected_generation(db.connection, unit)
        for unit in derivations.known_units(db.connection, "project")
        if unit.unit_kind == "year"
    }


def global_pass_agrees(conn):
    """finalize_history over the live state changes nothing the years wrote."""
    from swingset.project.history import finalize_history

    before = year_state(conn)
    conn.execute("SAVEPOINT global_pass")
    try:
        finalize_history(conn, now=NOW)
        return year_state(conn) == before
    finally:
        conn.execute("ROLLBACK TO global_pass")
        conn.execute("RELEASE global_pass")


def test_a_year_that_rewrote_its_own_association_is_current_at_once(tmp_path):
    """F23: a year's content inputs leave out the column it writes, so its own
    run never makes it stale; a second run with no input change writes nothing."""
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    with open_database(tmp_path) as db:
        conn = db.connection
        years = derived_years(db)
        assert set(years) == {"2009", "2024", "2025", "2026"}
        assert all(years.values())
        assert global_pass_agrees(conn)
        clock = FakeClock(datetime.fromisoformat(NOW))
        from swingset.project.process import process_unit

        def canonical():
            row = conn.execute("SELECT value FROM revisions WHERE name='canonical'").fetchone()
            return None if row is None else int(row[0])

        unchanged = canonical()
        for year in sorted(years):
            unit = WorkUnit("project", "year", year)
            for _ in range(2):
                process_unit(db, unit, SimpleNamespace(files={}), clock, "run")
                assert derivations.current(conn, unit), unit
                assert derivations.selected_generation(conn, unit) == years[year]
        # A run that moves no association leaves the replay witness alone.
        assert canonical() == unchanged
        # A dancer's reconcile moves a 2026 association to a 2025 event under
        # a cutoff, which moves the dancer epoch (F23).
        with db.transaction():
            conn.execute(
                "UPDATE registry_placements SET event_id='e25' "
                "WHERE event_month='2026-01' AND division='INT'"
            )
            derivations.bump_replay(conn, "project", "dancer", now=NOW)
        stale = {unit.unit_id for unit in derivations.pending_units(conn, "project")}
        assert {"2024", "2025", "2026"} <= stale
        unit = WorkUnit("project", "year", "2026")
        before = canonical()
        process_unit(db, unit, SimpleNamespace(files={}), clock, "run")
        # Moving the association back bumps the canonical revision (F2's
        # replay witness for a year whose output cuts off).
        assert canonical() is not None and canonical() != before
        # Its own rewrite of the association did not make it stale.
        assert derivations.current(conn, unit)
        assert derivations.selected_generation(conn, unit) == years["2026"]
        drain_projection(db)
        assert global_pass_agrees(conn)


def test_an_event_moving_across_years_re_derives_both_years_only(tmp_path):
    """The plan's alias-across-a-year-boundary risk: an event that moves from
    2024 to 2025 re-derives those two years; 2026 stays current untouched."""
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    with open_database(tmp_path) as db:
        conn = db.connection
        years = derived_years(db)
        conn.execute("DELETE FROM pending_work")
        with db.transaction():
            conn.execute("UPDATE events SET year=2025,event_month='2025-06' WHERE event_id='mid24'")
        pending = {
            unit.unit_id
            for unit in derivations.pending_units(conn, "project")
            if unit.unit_kind == "year"
        }
        assert pending == {"2024", "2025"}
        drain_projection(db)
        after = {
            year: derivations.selected_generation(conn, WorkUnit("project", "year", year))
            for year in years
        }
        assert {year for year in years if after[year] != years[year]} == {"2024", "2025"}
        assert global_pass_agrees(conn)
        # Its 2024 placement lost its event; 2025 counts it now.
        assert (
            conn.execute(
                "SELECT event_id FROM registry_placements WHERE series_id='wsdc-4'"
            ).fetchone()[0]
            is None
        )
        assert not conn.execute(
            "SELECT 1 FROM coverage WHERE year=2024 AND via='wayback'"
        ).fetchone()


def test_accept_year_stages_its_year_alone(tmp_path):
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    with open_database(tmp_path) as db:
        conn = db.connection
        years = derived_years(db)
        unit = WorkUnit("project", "year", "2025")
        with derivations.chunking(conn, unit) as chunk:
            with db.transaction():
                accept_year(conn, 2025, accepted_by="owner", accepted_at=NOW)
            assert chunk.deferred is not None and chunk.deferred.selection.unit == unit
            derivations.finish_building(db, chunk.deferred)
        assert derivations.selected_generation(conn, unit) != years["2025"]
        assert (
            conn.execute("SELECT events_accepted FROM coverage WHERE year=2025 LIMIT 1").fetchone()[
                0
            ]
            == 1
        )
        for year, generation in years.items():
            other = WorkUnit("project", "year", year)
            assert derivations.current(conn, other), year
            if year != "2025":
                assert derivations.selected_generation(conn, other) == generation
        assert not any(
            item.unit_kind == "history" for item in derivations.known_units(conn, "project")
        )


def test_a_year_waits_for_the_events_reported_in_its_months(tmp_path):
    """A December event reported in January: the January year associates its
    placements, so the event is a prerequisite of both years (plan F17)."""
    from swingset.state import derivation_dependencies as dependencies
    from swingset.state.work import WorkUnit

    with open_database(tmp_path / "state", lock=False) as db:
        conn = db.connection
        for event_id, year, month in (
            ("december", 2025, "2026-01"),
            ("own-year", 2026, "2026-05"),
            ("elsewhere", 2025, "2025-06"),
        ):
            conn.execute(
                "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,"
                "sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id,"
                "event_month) VALUES (?,?,?,?,NULL,NULL,'registry','[]','test','snap','1',"
                "'t','t','run',?)",
                (event_id, "s-" + event_id, event_id, year, month),
            )
        events = {
            unit.unit_id
            for unit in dependencies.prerequisites(conn, WorkUnit("project", "year", "2026"))
            if unit.unit_kind == "event"
        }
        assert events == {"december", "own-year"}
        earlier = {
            unit.unit_id
            for unit in dependencies.prerequisites(conn, WorkUnit("project", "year", "2025"))
            if unit.unit_kind == "event"
        }
        assert earlier == {"december", "elsewhere"}
