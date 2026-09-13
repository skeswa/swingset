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
        conn.execute("INSERT INTO runs VALUES ('run',?,NULL,1,NULL)", (NOW,))
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
            "UPDATE derivation_scopes SET materialized_generation_id=NULL WHERE stage='project' AND unit_kind='history'"
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
        conn.execute("INSERT INTO runs VALUES ('run',?,NULL,1,NULL)", (NOW,))
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
        conn.execute("INSERT INTO runs VALUES ('run',?,NULL,1,NULL)", (NOW,))
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
        conn.execute("INSERT INTO runs VALUES ('run',?,NULL,1,NULL)", (NOW,))
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


def test_two_editions_in_same_capture_require_review(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs VALUES ('run',?,NULL,1,NULL)", (NOW,))
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
