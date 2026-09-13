import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.fetch.wayback import (
    Capture,
    cdx_url,
    parse_cdx,
    query_due,
    select_captures,
    store_cdx_page,
)
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.sources.wsdc_calendar import EventsPage
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

CDX = json.dumps(
    [
        ["timestamp", "original", "digest", "mimetype", "length"],
        ["20190801000000", "https://worldsdc.com/events/", "A", "text/html", "23"],
    ]
).encode()


def test_selection_paging_and_query_clock(tmp_path):
    parsed = parse_cdx(CDX)
    assert (
        parsed[0].archive_url
        == "https://web.archive.org/web/20190801000000id_/https://worldsdc.com/events/"
    )
    candidates = parsed + (
        Capture(parsed[0].url, "20190802000000", "A", "text/html", 23),
        Capture(parsed[0].url, "20190601000000", "B", "text/html", 23),
        Capture(parsed[0].url, "20190901000000", "C", "text/html", 23, 404),
    )
    assert [c.timestamp for c in select_captures(candidates, date(2019, 6, 1))] == [
        "20190802000000",
        "20190601000000",
    ]
    assert "showNumPages=true" in cdx_url("https://worldsdc.com/*", 2019)
    assert "fl=" not in cdx_url("https://worldsdc.com/*", 2019)
    assert "output=" not in cdx_url("https://worldsdc.com/*", 2019)
    assert not query_due(2019, "2020-01-01T00:00:00+00:00", datetime(2026, 1, 1, tzinfo=UTC))
    with open_database(tmp_path) as db:
        store_cdx_page(
            db.connection,
            source="wsdc_calendar",
            prefix="worldsdc.com/*",
            year=2019,
            page=0,
            total_pages=2,
            body=CDX,
            queried_at="2026-01-01T00:00:00+00:00",
        )
        assert db.connection.execute("SELECT next_page FROM archive_queries").fetchone()[0] == 1
        with pytest.raises(ValueError, match="order"):
            store_cdx_page(
                db.connection,
                source="wsdc_calendar",
                prefix="worldsdc.com/*",
                year=2019,
                page=0,
                total_pages=2,
                body=CDX,
                queried_at="2026-01-01T00:00:00+00:00",
            )


def test_archive_fetch_parse_provenance_seals_and_never_contacts_origin(tmp_path):
    clock = FakeClock()
    requests = []

    def respond(request):
        requests.append((clock.now(), request))
        assert request.url.host == "web.archive.org"
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert "if-none-match" not in request.headers
        return httpx.Response(
            200,
            content=Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes(),
            headers={
                "memento-datetime": "Thu, 01 Aug 2019 00:00:00 GMT",
                "x-archive-orig-etag": "original",
            },
        )

    with open_database(tmp_path) as db:
        run = db.start_run(clock.now(), dry_run=True)
        spec = WatchSpec(
            "hint",
            "wsdc_calendar",
            "index",
            "GET",
            "https://worldsdc.com/events/",
            "wsdc_calendar.events",
            archive_url="https://web.archive.org/web/20190801000000id_/https://worldsdc.com/events/",
        )
        upsert_watch(db.connection, spec, clock.now())
        archive = Archive(tmp_path / "archive")
        config = Config(
            {"web.archive.org": HostConfig(min_gap_seconds=10)},
            {"wsdc_calendar": SourceConfig(True)},
        )
        client = FetchClient(
            db.connection, config, clock, archive, transport=httpx.MockTransport(respond)
        )
        result = client.fetch(spec.watch_id, EventsPage(), run)
        assert result.snapshot_id
        snap = dict(db.connection.execute("SELECT * FROM snapshots").fetchone())
        assert snap["via"] == "wayback" and snap["observed_at"] == "2019-08-01T00:00:00+00:00"
        assert snap["url"] == spec.url and snap["etag"] == "original"
        assert requests[1][0] - requests[0][0] >= timedelta(seconds=10)
        parse_snapshot(db, archive, WorkUnit("parse", "snapshot", result.snapshot_id), clock, run)
        assert db.connection.execute("SELECT state FROM watches").fetchone()[0] == "sealed"
        assert client.fetch(spec.watch_id, EventsPage(), run).skipped == "sealed"
        assert len(requests) == 2
        client.close()


def test_recorded_cdx_response():
    captures = parse_cdx(Path("tests/fixtures/sources/recorded_wsdc_cdx.json").read_bytes())
    assert len(captures) > 30
    assert captures[0].timestamp == "20191119113911"
    assert all(c.archive_url.startswith("https://web.archive.org/web/") for c in captures)


def test_archive_index_keeps_distinct_capture_observations(tmp_path):
    from swingset.fetch.wayback import schedule_capture

    clock = FakeClock()

    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        stamp = "2019" if "2019" in request.url.path else "2018"
        body = (
            Path("tests/fixtures/sources/synthetic_calendar.html")
            .read_bytes()
            .replace(b"2026", stamp.encode())
        )
        return httpx.Response(200, content=body)

    with open_database(tmp_path) as db:
        run = db.start_run(clock.now(), dry_run=True)
        archive = Archive(tmp_path / "archive")
        config = Config(
            {"web.archive.org": HostConfig(min_gap_seconds=10)},
            {"wsdc_calendar": SourceConfig(True)},
        )
        client = FetchClient(
            db.connection, config, clock, archive, transport=httpx.MockTransport(respond)
        )
        for year in (2019, 2018):
            spec = WatchSpec(
                "hint",
                "wsdc_calendar",
                "index",
                "GET",
                "https://worldsdc.com/events/",
                "wsdc_calendar.events",
                archive_url=f"https://web.archive.org/web/{year}0801000000id_/https://worldsdc.com/events/",
            )
            assert schedule_capture(db.connection, spec, now=clock.now())
            result = client.fetch(spec.watch_id, EventsPage(), run)
            parse_snapshot(
                db, archive, WorkUnit("parse", "snapshot", result.snapshot_id), clock, run
            )
        assert db.connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
        assert db.connection.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 1
        assert not schedule_capture(db.connection, spec, now=clock.now())
        client.close()


def test_cdx_resume_then_current_year_refresh_preserves_first_capture_provenance(tmp_path):
    clock = FakeClock()
    requests = []

    def respond(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if "showNumPages" in request.url.params:
            return httpx.Response(200, json={"pages": 2})
        return httpx.Response(200, content=CDX)

    with open_database(tmp_path) as db:
        archive = Archive(tmp_path / "archive")
        config = Config(
            {"web.archive.org": HostConfig(min_gap_seconds=10)},
            {"wsdc_calendar": SourceConfig(True)},
        )
        client = FetchClient(
            db.connection, config, clock, archive, transport=httpx.MockTransport(respond)
        )
        arguments = dict(source="wsdc_calendar", prefix="worldsdc.com/*", year=2026)
        assert client.index_archive(**arguments, deadline=clock.now() + timedelta(seconds=21)) == 1
        assert client.index_archive(**arguments) == 1
        first_query = db.connection.execute("SELECT cdx_query_id FROM archive_captures").fetchone()[
            0
        ]
        request_count = len(requests)
        assert client.index_archive(**arguments) == 0
        assert len(requests) == request_count
        clock.sleep(91 * 86400)
        assert client.index_archive(**arguments) == 2
        assert db.connection.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 2
        assert (
            db.connection.execute("SELECT cdx_query_id FROM archive_captures").fetchone()[0]
            == first_query
        )
        client.close()


def test_sheet_parse_failure_tries_next_distinct_capture(tmp_path):
    from materialized_fixture import materialize_seeded_outputs
    from test_admission import Corpus

    from swingset.model.canonical import Event
    from swingset.project.history import accept_year
    from swingset.project.writer import Projection, replace_scope

    clock = FakeClock()
    with open_database(tmp_path) as db:
        # The transport fallback uses an actually assessed sheet kind, with an
        # offline review of a retained EEPro body. Calendar pages are not sheets.
        corpus = Corpus(db)
        body = Path(
            "src/swingset/sources/eepro/fixtures/round-jjprelims-summerhummer2026-2026-09-09.body"
        ).read_bytes()
        generation, report = corpus.stage(corpus.snapshot("contract-control", body), body=body)
        assert not report.failures
        corpus.review(generation)
        now = clock.now().isoformat()
        run = db.start_run(clock.now(), dry_run=True)
        event = Event(
            event_id="2019-07-example",
            series_id="wsdc-1",
            name="Example",
            year=2019,
            start_date="2019-07-01",
            end_date="2019-07-03",
            source="wsdc_calendar",
            snapshot_id="override",
            parser_version="1",
            first_seen_at=now,
            last_seen_at=now,
            run_id=run,
        )
        replace_scope(
            db.connection,
            scope_kind="calendar",
            scope_id="test",
            projection=Projection((event,)),
            run_id=run,
            projected_at=now,
        )
        db.connection.execute(
            "INSERT INTO source_event_map VALUES ('eepro','example','2019-07-example','override',1)"
        )
        materialize_seeded_outputs(db, now=now, run_id=run)
        accept_year(db.connection, 2019, accepted_by="owner", accepted_at=now)
        db.connection.executemany(
            "INSERT INTO meta VALUES (?,'true')", [("h7_deployed",), ("h10_deployed",)]
        )
        url = "https://eepro.com/results/example/round.htm"
        spec = WatchSpec(
            "hint",
            "eepro",
            "round",
            "GET",
            url,
            "eepro.round",
            source_ref="example",
            archive_url=f"https://web.archive.org/web/20191001000000id_/{url}",
        )
        upsert_watch(db.connection, spec, clock.now())
        db.connection.executemany(
            "INSERT INTO archive_captures VALUES (?,?,?,?,200,?,?,?,?)",
            [
                (spec.source, url, stamp, digest, "text/html", 30, now, "query")
                for stamp, digest in [
                    ("20191001000000", "A"),
                    ("20190901000000", "B"),
                    ("20190801000000", "C"),
                ]
            ],
        )

        def respond(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(
                200,
                content=b"<html>invalid</html>" if "201910" in request.url.path else body,
            )

        archive = Archive(tmp_path)
        config = Config(
            {"web.archive.org": HostConfig(min_gap_seconds=10)},
            {"eepro": SourceConfig(True)},
        )
        client = FetchClient(
            db.connection, config, clock, archive, transport=httpx.MockTransport(respond)
        )
        failed = client.fetch(spec.watch_id, corpus.page, run)
        assert parse_snapshot(
            db, archive, WorkUnit("parse", "snapshot", failed.snapshot_id), clock, run
        ).failed
        assert (
            "201909"
            in db.connection.execute(
                "SELECT archive_url FROM watches WHERE watch_id=?", (spec.watch_id,)
            ).fetchone()[0]
        )
        success = client.fetch(spec.watch_id, corpus.page, run)
        assert not parse_snapshot(
            db, archive, WorkUnit("parse", "snapshot", success.snapshot_id), clock, run
        ).failed
        assert (
            db.connection.execute(
                "SELECT state FROM watches WHERE watch_id=?", (spec.watch_id,)
            ).fetchone()[0]
            == "sealed"
        )
        client.close()


def test_redirected_capture_is_reused_by_requested_and_final_url(tmp_path):
    from swingset.fetch.wayback import schedule_capture

    clock = FakeClock()
    original = "https://worldsdc.com/events/"
    requested = "https://web.archive.org/web/20190801000000id_/" + original
    final = "https://web.archive.org/web/20190802000000id_/" + original
    requests = []

    def respond(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if str(request.url) == requested:
            return httpx.Response(302, headers={"location": final})
        assert str(request.url) == final
        return httpx.Response(
            200,
            content=Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes(),
            headers={"memento-datetime": "Fri, 02 Aug 2019 00:00:00 GMT"},
        )

    with open_database(tmp_path) as db:
        run = db.start_run(clock.now(), dry_run=True)
        spec = WatchSpec(
            "",
            "wsdc_calendar",
            "index",
            "GET",
            original,
            "wsdc_calendar.events",
            archive_url=requested,
        )
        assert schedule_capture(db.connection, spec, now=clock.now())
        archive = Archive(tmp_path / "archive")
        config = Config(
            {"web.archive.org": HostConfig(min_gap_seconds=10)},
            {"wsdc_calendar": SourceConfig(True)},
        )
        client = FetchClient(
            db.connection, config, clock, archive, transport=httpx.MockTransport(respond)
        )
        result = client.fetch(spec.watch_id, EventsPage(), run)
        snapshot = db.connection.execute(
            "SELECT url,archive_url,requested_archive_url,captured_at FROM snapshots"
        ).fetchone()
        assert tuple(snapshot) == (original, final, requested, "2019-08-02T00:00:00+00:00")
        assert result.snapshot_id
        assert not schedule_capture(db.connection, spec, now=clock.now())
        final_spec = WatchSpec(
            "",
            "wsdc_calendar",
            "index",
            "GET",
            original,
            "wsdc_calendar.events",
            archive_url=final,
        )
        assert not schedule_capture(db.connection, final_spec, now=clock.now())
        # Direct callers receive the same suppression before parsing/sealing.
        assert client.fetch(spec.watch_id, EventsPage(), run).skipped == "capture already archived"
        assert len(requests) == 3
        client.close()


def test_empty_cdx_probe_is_complete_and_raw_response_is_retained(tmp_path):
    with open_database(tmp_path) as db:
        archive = Archive(tmp_path)
        client = FetchClient(
            db.connection,
            Config({}, {"wsdc_calendar": SourceConfig(True)}),
            FakeClock(),
            archive,
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path == "/robots.txt"
                    else httpx.Response(200, json=[])
                )
            ),
        )
        try:
            assert (
                client.index_archive(
                    source="wsdc_calendar", prefix="worldsdc.com/event-calendar/", year=2024
                )
                == 0
            )
        finally:
            client.close()
        row = db.connection.execute(
            "SELECT total_pages,completed_at FROM archive_queries"
        ).fetchone()
        assert row[0] == 0 and row[1]
        receipt = json.loads(next((tmp_path / "archive-cdx").glob("*/probe.json")).read_bytes())
        assert archive.read_body(receipt["body_sha256"]) == b"[]"
