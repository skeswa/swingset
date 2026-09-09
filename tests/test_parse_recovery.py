from pathlib import Path

import httpx

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
            "SELECT body_sha256,fingerprint,etag,last_modified,next_check_at FROM watches"
        ).fetchone()
        assert tuple(watch)[:4] == (None, None, None, None)
        snapshot = database.connection.execute(
            "SELECT parse_status,extract_status,parser_version FROM snapshots WHERE snapshot_id=?",
            (fetched.snapshot_id,),
        ).fetchone()
        assert tuple(snapshot) == ("failed", "ok", str(DancerPage.PARSER_VERSION))
        assert (
            database.connection.execute(
                "SELECT value FROM revisions WHERE name='snapshots'"
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
