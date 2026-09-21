import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from swingset.clock import FakeClock
from swingset.config import Config
from swingset.history.catalog import Target, load_catalog, retained_catalog, save_catalog
from swingset.history.intake import run_intake
from swingset.sources.wsdc_newsletter.index import IndexPage
from swingset.state.db import open_database


def test_retained_catalog_is_finite_event_lists_only(tmp_path):
    targets = retained_catalog(Path("."))
    assert len(targets) == 152
    assert len([target for target in targets if target.source == "swingdancecouncil"]) == 41
    assert {target.parser for target in targets} == {
        "swingdancecouncil.events",
        "wsdc_calendar.events",
    }
    assert not any(urlsplit(target.url).query for target in targets)
    path = tmp_path / "catalog.json"
    save_catalog(path, targets)
    assert load_catalog(path) == targets


def test_intake_resumes_skips_digest_duplicates_and_explains_failures(tmp_path):
    clock = FakeClock()
    calls = []
    body = Path(
        "src/swingset/sources/swingdancecouncil/fixtures/council-20120825.html"
    ).read_bytes()

    def response(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, content=b"<html>bad page</html>" if "/201301" in request.url.path else body
        )

    targets = (
        Target(
            "swingdancecouncil",
            "http://swingdancecouncil.com/ActiveServerPages/UpcomingEvents.asp",
            "swingdancecouncil.events",
            "20120825221508",
            "digest1",
        ),
        Target(
            "swingdancecouncil",
            "http://www.swingdancecouncil.com/ActiveServerPages/UpcomingEvents.asp",
            "swingdancecouncil.events",
            "20120925221508",
            "digest1",
        ),
        Target(
            "swingdancecouncil",
            "http://swingdancecouncil.com/ActiveServerPages/UpcomingEvents.asp",
            "swingdancecouncil.events",
            "20130101000000",
            "digest2",
        ),
    )
    catalog = tmp_path / "phase1-catalog.json"
    save_catalog(catalog, targets)
    with open_database(tmp_path) as db:
        first = run_intake(
            db,
            catalog,
            config=Config({}, {}),
            clock=clock,
            max_targets=1,
            transport=httpx.MockTransport(response),
        )
        assert sum(row["status"] == "parsed" for row in first["targets"].values()) == 1
        second = run_intake(
            db, catalog, config=Config({}, {}), clock=clock, transport=httpx.MockTransport(response)
        )
        assert [second["targets"][target.target_id]["status"] for target in targets] == [
            "parsed",
            "duplicate",
            "finding",
        ]
        assert len(calls) == 3
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM findings WHERE owner_kind='phase1_capture' AND closed_at IS NULL"
            ).fetchone()[0]
            == 1
        )
        assert json.loads((tmp_path / "phase1-ledger.json").read_bytes()) == second
        second["targets"][targets[0].target_id]["parser_version"] = "0"
        (tmp_path / "phase1-ledger.json").write_text(json.dumps(second))
        replay = run_intake(
            db, catalog, config=Config({}, {}), clock=clock, transport=httpx.MockTransport(response)
        )
        assert replay["targets"][targets[0].target_id]["parser_version"] != "0"
        assert len(calls) == 3


def test_newsletter_index_keeps_same_host_issue_pdfs():
    from swingset.sources.base import ParseContext

    page = IndexPage()
    result = page.parse(
        page.extract(
            b'<a href="/wp-content/uploads/Vol6.pdf">Vol6</a><a href="https://other.test/other.pdf">Other</a><a href="/wp-content/uploads/WSDC-ByLaws.pdf">Bylaws</a>'
        ),
        ParseContext(
            "s",
            "w",
            "https://www.worldsdc.com/newsletter/",
            "wsdc_newsletter",
            page.kind,
            None,
            "2026-01-01T00:00:00+00:00",
        ),
    )
    assert [watch.url for watch in result.watches] == [
        "https://www.worldsdc.com/wp-content/uploads/Vol6.pdf"
    ]


def test_pending_catalog_and_invalid_dates_keep_year_gate_closed(tmp_path):
    from datetime import date

    import pytest

    from swingset.history.closure import synchronize_year_findings, year_gaps
    from swingset.project.history import accept_year
    from swingset.state.findings import Finding, replace_findings

    target = Target(
        "swingdancecouncil",
        "http://swingdancecouncil.com/ActiveServerPages/UpcomingEvents.asp",
        "swingdancecouncil.events",
        "20150901000000",
    )
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs VALUES ('run','2016-01-01',NULL,1,NULL)")
        pending = year_gaps(conn, (target,), {}, final_year=2017, history_start=date(2010, 1, 1))
        assert pending[2015] and pending[2016] and pending[2017]
        assert not pending[2014]
        synchronize_year_findings(conn, pending, now="2016-01-01", run_id="run")
        with pytest.raises(ValueError, match="unresolved"):
            accept_year(conn, 2016, accepted_by="owner", accepted_at="2016-01-01T00:00:00+00:00")
        conn.execute(
            "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES ('w','swingdancecouncil','index','GET','https://example.test','swingdancecouncil.events','sealed')"
        )
        conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES ('capture','w','GET','https://example.test','2016-01-01',200,1,1,'run','Ok')"
        )
        replace_findings(
            conn,
            owner_kind="parse",
            owner_id="capture",
            findings=(
                Finding(
                    "unrecognized_listing_date",
                    "snapshot",
                    "capture",
                    "warning",
                    "Unknown date",
                    {"date": "Feb 12 - 14, 215"},
                ),
            ),
            opened_at="2016-01-01",
            run_id="run",
        )
        # The malformed year cannot narrow the affected capture horizon.
        conn.execute("UPDATE findings SET snapshot_id='capture' WHERE owner_kind='parse'")
        gaps = year_gaps(
            conn,
            (target,),
            {target.target_id: {"status": "parsed", "snapshot_id": "capture"}},
            final_year=2017,
            history_start=date(2010, 1, 1),
        )
        assert all(gaps[year] for year in (2015, 2016, 2017))


def test_newsletter_issue_year_bounds_its_affected_years():
    from swingset.history.closure import affected_years

    issue = Target(
        "wsdc_newsletter",
        "http://www.worldsdc.com/wp-content/uploads/2022/06/WSDC-Newsleter-Vol-22-June-2022.pdf",
        "wsdc_newsletter.events",
    )
    # A live newsletter has no capture timestamp; its upload path dates the
    # issue, and its sidebar reaches into the next year at most.
    assert affected_years(issue, 2026) == {2022, 2023}
    undated = Target(
        "wsdc_newsletter", "http://www.worldsdc.com/newsletter/", "wsdc_newsletter.index"
    )
    assert affected_years(undated, 2018) == {2016, 2017, 2018}


def test_dismissed_parse_failure_clears_a_finding_status_capture(tmp_path):
    from datetime import date

    from swingset.history.closure import year_gaps
    from swingset.state.findings import Finding, replace_findings

    target = Target(
        "wsdc_calendar",
        "http://www.worldsdc.com:80/events-map/",
        "wsdc_calendar.events",
        "20160710033710",
    )
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs VALUES ('run','2016-01-01',NULL,1,NULL)")
        conn.execute(
            "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES ('w','wsdc_calendar','index','GET','https://example.test','wsdc_calendar.events','sealed')"
        )
        conn.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES ('capture','w','GET','https://example.test','2016-01-01',200,1,1,'run','Ok')"
        )
        replace_findings(
            conn,
            owner_kind="parse_failure",
            owner_id="capture",
            findings=(
                Finding(
                    "parse_failure",
                    "watch",
                    "w",
                    "error",
                    "captured map marker lacks a name or printed date",
                    {},
                ),
            ),
            opened_at="2016-01-01",
            run_id="run",
        )
        conn.execute("UPDATE findings SET snapshot_id='capture' WHERE owner_kind='parse_failure'")
        ledger = {target.target_id: {"status": "finding", "snapshot_id": "capture"}}
        # A capture whose parser failed keeps every year open until a reviewer
        # dismisses that failure (D-0187); a map widget with unnamed markers
        # holds nothing an event list needs.
        assert year_gaps(conn, (target,), ledger, final_year=2017, history_start=date(2010, 1, 1))[
            2016
        ]
        gaps = year_gaps(
            conn,
            (target,),
            ledger,
            final_year=2017,
            history_start=date(2010, 1, 1),
            dismissed={("parse_failure", "w")},
        )
        assert not any(gaps.values())
