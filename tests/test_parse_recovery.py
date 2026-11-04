from pathlib import Path

import httpx
from test_event_enumerations import event as event

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.watches import upsert_watch
from swingset.sources.wsdc_registry.adapter import SOURCE, DancerPage
from swingset.state.db import open_database
from swingset.state.work import next_work


def test_registry_invalid_parse_is_published_failure_and_forces_clean_retry(
    tmp_path: Path,
) -> None:
    clock = FakeClock()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json={"unexpected": True}, headers={"ETag": '"bad"'})

    config = Config(
        {"points.worldsdc.com": HostConfig(min_gap_seconds=2)},
        {"wsdc_registry": SourceConfig(True)},
    )
    with open_database(tmp_path) as database:
        run_id = database.start_run(clock.now())
        spec = SOURCE.watch(1_000_000)
        upsert_watch(database.connection, spec, clock.now())
        client = FetchClient(
            database.connection,
            config,
            clock,
            Archive(tmp_path),
            transport=httpx.MockTransport(handler),
        )
        fetched = client.fetch(spec.watch_id, DancerPage(), run_id)
        unit = next_work(database.connection, "parse")
        assert unit is not None
        attempt = parse_snapshot(database, Archive(tmp_path), unit, clock, run_id)
        assert attempt.failed
        watch = database.connection.execute(
            "SELECT body_sha256, fingerprint, etag, last_modified, next_check_at FROM watches"
        ).fetchone()
        assert tuple(watch)[:4] == (None, None, None, None)
        snapshot = database.connection.execute(
            "SELECT parse_status, extract_status, parser_version FROM snapshots WHERE snapshot_id = %s",
            (fetched.snapshot_id,),
        ).fetchone()
        assert tuple(snapshot) == ("failed", "ok", str(DancerPage.PARSER_VERSION))
        assert (
            database.connection.execute(
                "SELECT value FROM revisions WHERE name = 'snapshots'"
            ).fetchone()[0]
            == 2
        )
        assert (
            database.connection.execute(
                "SELECT kind FROM findings WHERE closed_at IS NULL"
            ).fetchone()[0]
            == "invalid_response"
        )
        client.close()


def test_failed_new_extractor_never_relabels_old_empty_extract_as_current(tmp_path, monkeypatch):
    import swingset.schedule.parse as module
    from swingset.sources.base import ExtractError, WatchSpec
    from swingset.sources.wsdc_calendar.adapter import EventsPage
    from swingset.state.work import WorkUnit

    clock = FakeClock()

    class BrokenPage(EventsPage):
        EXTRACT_VERSION = 100
        PARSER_VERSION = 100
        attempts = 0

        def extract(self, body):
            self.attempts += 1
            raise ExtractError("new layout not yet supported")

    page = BrokenPage()
    with open_database(tmp_path) as database:
        conn = database.connection
        archive = Archive(tmp_path)
        run_id = database.start_run(clock.now())
        spec = WatchSpec(
            "", "wsdc_calendar", "index", "GET", "https://worldsdc.com/events/", EventsPage.kind
        )
        upsert_watch(conn, spec, clock.now())
        body_sha = archive.store_body(b"new actual body")
        old_extract = archive.store_extract([])
        conn.execute(
            (
                "INSERT INTO snapshots (snapshot_id, watch_id, method, url, fetched_at, http_status, "
                "body_sha256, body_bytes, content_changed, run_id, classification, extract_sha256, "
                "extract_version, parser_version, extract_status) VALUES ('s', %s, 'GET', %s, %s, 200, %s, "
                "15, 1, %s, 'Ok', %s, '1', '1', 'ok')"
            ),
            (spec.watch_id, spec.url, clock.now().isoformat(), body_sha, run_id, old_extract),
        )
        monkeypatch.setattr(module, "get_page_kind", lambda _: page)
        for _ in range(2):
            assert parse_snapshot(
                database, archive, WorkUnit("parse", "snapshot", "s"), clock, run_id
            ).failed
        assert page.attempts == 2
        assert (
            conn.execute("SELECT extract_sha256 FROM snapshots WHERE snapshot_id = 's'").fetchone()[
                0
            ]
            is None
        )


def test_parse_hint_reconstruction_preserves_retained_token_and_original_fetch_anchor(event):
    from test_parse_hint_recovery import pending, ready

    from swingset.state.work import recover_parse_hints

    f = event
    ready(f)
    context, unit = pending(f)
    original = f.conn.execute(
        "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id=%s", (unit.unit_id,)
    ).fetchone()[0]
    assert original == context.fetched_at
    f.corpus.clock.sleep(3600)
    snapshots = [
        tuple(row) for row in f.conn.execute("SELECT * FROM snapshots ORDER BY snapshot_id")
    ]
    admissions = [
        tuple(row)
        for row in f.conn.execute("SELECT * FROM admission_decisions ORDER BY decision_id")
    ]
    first = recover_parse_hints(f.db, now=f.corpus.clock.now())
    assert first["enqueued"] == 0
    assert (
        f.conn.execute(
            "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id=%s",
            (unit.unit_id,),
        ).fetchone()[0]
        == original
    )
    f.conn.execute("DELETE FROM pending_work WHERE stage='parse' AND unit_id=%s", (unit.unit_id,))
    second = recover_parse_hints(f.db, now=f.corpus.clock.now())
    assert second["enqueued"] == 1
    assert (
        f.conn.execute(
            "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id=%s",
            (unit.unit_id,),
        ).fetchone()[0]
        == original
    )
    assert [
        tuple(row) for row in f.conn.execute("SELECT * FROM snapshots ORDER BY snapshot_id")
    ] == snapshots
    assert [
        tuple(row)
        for row in f.conn.execute("SELECT * FROM admission_decisions ORDER BY decision_id")
    ] == admissions
