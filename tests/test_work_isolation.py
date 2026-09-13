"""H12: real ordinary work progresses beside a retained deterministic failure."""

import shutil
from pathlib import Path

import httpx

from swingset.clock import FakeClock
from swingset.fetch.archive import Archive
from swingset.schedule.cycle import run_cycle
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import ExtractError, WatchSpec
from swingset.sources.wsdc_calendar.adapter import EventsPage
from swingset.state.db import open_database
from swingset.state.work import WorkUnit, enqueue

FIXTURE = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()


def seed_snapshot(database, clock, identifier, body):
    archive = Archive(database.state_dir)
    spec = WatchSpec(
        "",
        "wsdc_calendar",
        "index",
        "GET",
        f"https://worldsdc.com/{identifier}/",
        EventsPage.kind,
    )
    upsert_watch(database.connection, spec, clock.now())
    run_id = database.start_run(clock.now())
    sha = archive.store_body(body)
    database.connection.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
        "body_sha256,body_bytes,content_changed,run_id,classification) "
        "VALUES (?,?,'GET',?,?,200,?,?,1,?,'Ok')",
        (identifier, spec.watch_id, spec.url, clock.now().isoformat(), sha, len(body), run_id),
    )
    database.connection.execute(
        "UPDATE watches SET next_check_at='2099-01-01T00:00:00Z' WHERE watch_id=?",
        (spec.watch_id,),
    )
    unit = WorkUnit("parse", "snapshot", identifier)
    enqueue(database.connection, [unit], enqueued_at=clock.now().isoformat())
    return unit, sha, run_id


def configuration(tmp_path):
    config, overrides = tmp_path / "config", tmp_path / "overrides"
    shutil.copytree("config", config)
    shutil.copytree("overrides", overrides)
    (config / "sources.toml").write_text(
        '[sources.wsdc_calendar]\nenabled=true\nindex_urls=["https://worldsdc.com/events/"]\n'
    )
    (overrides / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    (overrides / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    return config, overrides


def test_failed_parse_does_not_starve_healthy_fetch_or_derivation(tmp_path, monkeypatch):
    import swingset.schedule.parse as parsing

    clock = FakeClock()
    config, overrides = configuration(tmp_path)
    failed_calls = []
    requests = []

    class SelectivelyBrokenPage(EventsPage):
        def extract(self, body):
            if body == b"permanently unsupported layout":
                failed_calls.append(clock.now())
                raise ExtractError("deterministic unsupported layout")
            return super().extract(body)

    monkeypatch.setattr(parsing, "get_page_kind", lambda _: SelectivelyBrokenPage())

    def transport(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.url.path == "/events/"
        return httpx.Response(200, content=FIXTURE)

    state = tmp_path / "state"
    with open_database(state) as database:
        broken, broken_sha, _ = seed_snapshot(
            database, clock, "broken", b"permanently unsupported layout"
        )
        healthy, _, seed_run = seed_snapshot(database, clock, "healthy-derived", FIXTURE)
        assert not parse_snapshot(database, Archive(state), healthy, clock, seed_run).failed
        assert database.connection.execute("SELECT count(*) FROM events").fetchone()[0] == 0
        results = []
        for _ in range(3):
            results.append(
                run_cycle(
                    database,
                    config_dir=config,
                    overrides_dir=overrides,
                    clock=clock,
                    transport=httpx.MockTransport(transport),
                )
            )
            clock.sleep(1)
        assert database.connection.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert "https://worldsdc.com/events/" in requests
        assert (
            database.connection.execute(
                "SELECT count(*) FROM snapshots WHERE url='https://worldsdc.com/events/'"
            ).fetchone()[0]
            == 1
        )
        assert len(failed_calls) == 1
        assert Archive(state).read_body(broken_sha) == b"permanently unsupported layout"
        assert all("correction_candidate_id" not in result for result in results)
        attempts = database.connection.execute(
            "SELECT * FROM work_attempts WHERE stage='parse' AND unit_id=?", (broken.unit_id,)
        ).fetchall()
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "blocked"
        assert database.connection.execute(
            "SELECT 1 FROM findings WHERE closed_at IS NULL AND blocking_reason IS NOT NULL"
        ).fetchone()


def test_cycle_restores_missing_historical_body_from_real_checkpoint_and_commits(tmp_path):
    from swingset.backup.checkpoint import create_checkpoint

    clock = FakeClock()
    config, overrides = configuration(tmp_path)
    state = tmp_path / "state"
    historical = FIXTURE.replace(b"Example Swing", b"Historical Swing")
    requests = []

    def transport(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.url.path == "/events/"
        return httpx.Response(200, content=FIXTURE)

    with open_database(state) as database:
        unit, sha, _ = seed_snapshot(database, clock, "historical", historical)
        checkpoint = create_checkpoint(
            state,
            database.connection,
            state / "checkpoints/known-good",
            schema_version=database.schema_version,
            versions={},
            input_bundle_hash=None,
        )
        before_manifest = (checkpoint.path / "checkpoint.json").read_bytes()
        Archive(state).blob_path(sha).unlink()
        result = run_cycle(
            database,
            config_dir=config,
            overrides_dir=overrides,
            clock=clock,
            transport=httpx.MockTransport(transport),
        )
        assert Archive(state).read_body(sha) == historical
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM observations WHERE snapshot_id=?", (unit.unit_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM events WHERE name='Historical Swing'"
            ).fetchone()[0]
            == 1
        )
        row = database.connection.execute(
            "SELECT outcome FROM work_attempts WHERE stage='parse' AND unit_id=?", (unit.unit_id,)
        ).fetchone()
        assert row[0] == "succeeded"
        assert (
            database.connection.execute(
                "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?", (unit.unit_id,)
            ).fetchone()[0]
            == sha
        )
        assert not any("/historical/" in url for url in requests)
        assert "correction_candidate_id" not in result
        assert (checkpoint.path / "checkpoint.json").read_bytes() == before_manifest
        assert not (checkpoint.path / "state.sqlite-wal").exists()
        assert not (checkpoint.path / "state.sqlite-shm").exists()


def test_cycle_missing_artifact_without_backup_retains_unavailable_and_independent_progress(
    tmp_path,
):
    import json

    clock = FakeClock()
    config, overrides = configuration(tmp_path)
    state = tmp_path / "state"
    historical = FIXTURE.replace(b"Example Swing", b"Unavailable Historical Swing")
    requests = []

    def transport(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.url.path == "/events/"
        return httpx.Response(200, content=FIXTURE)

    with open_database(state) as database:
        broken, sha, _ = seed_snapshot(database, clock, "historical-missing", historical)
        healthy, _, run = seed_snapshot(database, clock, "healthy-derived", FIXTURE)
        assert not parse_snapshot(database, Archive(state), healthy, clock, run).failed
        Archive(state).blob_path(sha).unlink()
        for _ in range(3):
            result = run_cycle(
                database,
                config_dir=config,
                overrides_dir=overrides,
                clock=clock,
                transport=httpx.MockTransport(transport),
            )
            assert "correction_candidate_id" not in result
            clock.sleep(1)
        assert "https://worldsdc.com/events/" in requests
        assert (
            database.connection.execute(
                "SELECT COUNT(*) FROM events WHERE name='Example Swing'"
            ).fetchone()[0]
            == 1
        )
        attempts = database.connection.execute(
            "SELECT * FROM work_attempts WHERE stage='parse' AND unit_id=?", (broken.unit_id,)
        ).fetchall()
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "unavailable"
        assert json.loads(attempts[0]["evidence_json"])["sha256"] == sha
        finding = database.connection.execute(
            "SELECT * FROM findings WHERE finding_id=?", (attempts[0]["requirement_id"],)
        ).fetchone()
        assert finding["state"] == "unavailable" and finding["closed_at"] is None
        assert json.loads(finding["evidence_json"])["sha256"] == sha
        assert (
            database.connection.execute(
                "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?", (broken.unit_id,)
            ).fetchone()[0]
            == sha
        )
        assert database.connection.execute(
            "SELECT 1 FROM pending_work WHERE stage='parse' AND unit_id=?", (broken.unit_id,)
        ).fetchone()
        assert not Archive(state).blob_path(sha).exists()
        assert not any("/historical-missing/" in url for url in requests)
