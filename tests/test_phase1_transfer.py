import json
from pathlib import Path

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.config import Config, SourceConfig
from swingset.fetch.archive import Archive, canonical, durable_write
from swingset.fetch.client import FetchClient
from swingset.history.catalog import Target, save_catalog
from swingset.history.intake import run_intake
from swingset.history.transfer import export_evidence, import_evidence
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.sources.wsdc_calendar.adapter import EventsPage
from swingset.state.db import open_database
from swingset.state.work import WorkUnit


def test_repeat_import_preserves_live_watch_and_evidence_and_never_rewinds_budget(
    tmp_path, monkeypatch
):
    clock = FakeClock()
    config = Config({}, {"wsdc_calendar": SourceConfig(True)})
    url = "https://worldsdc.com/events/"
    target = Target(
        "wsdc_calendar",
        url,
        "wsdc_calendar.events",
        "20161113141128",
        catalog_evidence="research/verification/cdx.json",
    )
    evidence_path = tmp_path / target.catalog_evidence
    evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text("[]")
    fixture = Path("src/swingset/sources/wsdc_calendar/fixtures")
    archived_body = (fixture / "calendar-20161113.html").read_bytes()
    source_path, live_path, package = (tmp_path / name for name in ("source", "live", "export"))
    with open_database(source_path) as source:
        catalog = source_path / "phase1-catalog.json"
        save_catalog(catalog, (target,))
        run_intake(
            source,
            catalog,
            config=config,
            clock=clock,
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path == "/robots.txt"
                    else httpx.Response(200, content=archived_body)
                )
            ),
        )
        source.connection.execute(
            "INSERT INTO archive_queries VALUES ('q','wsdc_calendar','worldsdc.com/events/',2024,1,2,NULL,'2026-01-01')"
        )
        sha = Archive(source_path).store_body(b'{"pages":2}')
        durable_write(
            source_path / "archive-cdx/q/probe.json",
            canonical({"query_id": "q", "page": "probe", "body_sha256": sha}),
        )
        robots_sha = Archive(source_path).store_body(b"User-agent: *\nDisallow: /\n")
        source.connection.execute(
            "INSERT INTO hosts(host,robots_sha256,robots_fetched_at,robots_status) VALUES ('www.worldsdc.com',?,'2026-01-01',200)",
            (robots_sha,),
        )
        export_evidence(source, package, repository=tmp_path)
    manifest = json.loads((package / "phase1-export.json").read_bytes())
    assert Archive(package).read_body(manifest["catalog_inputs"][0]["body_sha256"]) == b"[]"
    with open_database(live_path) as live:
        conn = live.connection
        spec = WatchSpec("", "wsdc_calendar", "index", "GET", url, "wsdc_calendar.events")
        upsert_watch(conn, spec, clock.now())
        run_id = live.start_run(clock.now())
        client = FetchClient(
            conn,
            config,
            clock,
            Archive(live_path),
            transport=httpx.MockTransport(
                lambda request: (
                    httpx.Response(404)
                    if request.url.path == "/robots.txt"
                    else httpx.Response(
                        200, content=(fixture / "calendar-2026-09-09.html").read_bytes()
                    )
                )
            ),
        )
        try:
            fetched = client.fetch(spec.watch_id, EventsPage(), run_id)
        finally:
            client.close()
        parse_snapshot(
            live,
            Archive(live_path),
            WorkUnit("parse", "snapshot", fetched.snapshot_id),
            clock,
            run_id,
        )
        conn.execute(
            "UPDATE watches SET state='paused',paused_until='2030-01-01T00:00:00+00:00',notes='owner hold',priority=17 WHERE watch_id=?",
            (spec.watch_id,),
        )
        conn.execute(
            "INSERT INTO hosts(host,next_allowed_at,paused_until,pause_reason) VALUES ('web.archive.org','2030-01-01T00:00:00+00:00','2030-02-01T00:00:00+00:00','owner hold')"
        )
        conn.execute(
            "INSERT INTO host_budget VALUES ('web.archive.org',?,190,999999)",
            (clock.now().date().isoformat(),),
        )
        watch_before = dict(
            conn.execute("SELECT * FROM watches WHERE watch_id=?", (spec.watch_id,)).fetchone()
        )
        live_observations = [
            tuple(row)
            for row in conn.execute(
                "SELECT * FROM observations WHERE snapshot_id=?", (fetched.snapshot_id,)
            )
        ]
        import swingset.history.transfer as transfer

        original_parse = transfer.parse_snapshot

        def interrupted_parse(*args, **kwargs):
            original_parse(*args, **kwargs)
            raise KeyboardInterrupt("simulated interruption after nested parse commit")

        snapshots_before = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        with monkeypatch.context() as patch:
            patch.setattr(transfer, "parse_snapshot", interrupted_parse)
            with pytest.raises(KeyboardInterrupt):
                import_evidence(live, package, clock=clock)
        assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == snapshots_before
        assert (
            dict(
                conn.execute("SELECT * FROM watches WHERE watch_id=?", (spec.watch_id,)).fetchone()
            )
            == watch_before
        )
        assert not (live_path / "phase1-catalog.json").exists()
        first = import_evidence(live, package, clock=clock)
        conn.execute(
            "UPDATE archive_queries SET next_page=2,completed_at='2026-01-02' WHERE query_id='q'"
        )
        second = import_evidence(live, package, clock=clock)
        assert first["imported"] == 1 and second["imported"] == 0
        imported_robots = conn.execute(
            "SELECT robots_sha256 FROM hosts WHERE host='www.worldsdc.com'"
        ).fetchone()[0]
        assert Archive(live_path).read_body(imported_robots) == b"User-agent: *\nDisallow: /\n"
        assert tuple(
            conn.execute(
                "SELECT next_page,completed_at FROM archive_queries WHERE query_id='q'"
            ).fetchone()
        ) == (2, "2026-01-02")
        receipt = json.loads((live_path / "archive-cdx/q/probe.json").read_bytes())
        assert Archive(live_path).read_body(receipt["body_sha256"]) == b'{"pages":2}'
        assert (
            dict(
                conn.execute("SELECT * FROM watches WHERE watch_id=?", (spec.watch_id,)).fetchone()
            )
            == watch_before
        )
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT * FROM observations WHERE snapshot_id=?", (fetched.snapshot_id,)
            )
        ] == live_observations
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM observations WHERE scope_id='wsdc-history'"
            ).fetchone()[0]
            == 127
        )
        assert (
            conn.execute(
                "SELECT requests FROM host_budget WHERE host='web.archive.org'"
            ).fetchone()[0]
            == 190
        )
        assert (
            conn.execute("SELECT pause_reason FROM hosts WHERE host='web.archive.org'").fetchone()[
                0
            ]
            == "owner hold"
        )
        assert not conn.execute("SELECT 1 FROM watches WHERE kind!='index'").fetchone()
