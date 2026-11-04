"""Preserve the old G1 migration and run an identical miss on native retained facts."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from swingset.cli import main
from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.model.observations import encode_payload
from swingset.schedule.registry import advance_sweep
from swingset.sources.records import DancerLookup
from swingset.sources.wsdc_registry.adapter import SOURCE, DancerPage
from swingset.state.db import open_database
from swingset.state.verification import usable_verification

STARTED = datetime(2026, 9, 12, 14, tzinfo=UTC)
OLD_CHECK = STARTED - timedelta(days=7)
NUMBER = 29029


def retained_state(path, *, damage=None):
    database = open_database(path, lock=False)
    conn = database.connection
    spec = SOURCE.watch(NUMBER)
    body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1000000.body").read_bytes()
    archive = Archive(path)
    body_sha = archive.store_body(body)
    extract_sha = archive.store_extract(DancerPage().extract(body))
    conn.execute(
        "INSERT INTO runs (run_id, started_at, dry_run) VALUES ('v1', %s, 1)",
        (OLD_CHECK.isoformat(),),
    )
    conn.execute(
        (
            "INSERT INTO watches (watch_id, source, kind, method, url, form, parser, source_ref, state, "
            "next_check_at, last_checked_at, body_sha256, fingerprint, extract_version, "
            "current_observation_snapshot_id, notes) VALUES (%s, %s, 'dancer', 'POST', %s, %s, %s, %s, "
            "'registry', %s, %s, %s, %s, %s, 'v1-snapshot', 'probe')"
        ),
        (
            spec.watch_id,
            spec.source,
            spec.url,
            json.dumps(dict(spec.form)),
            spec.parser,
            spec.source_ref,
            (STARTED + timedelta(days=365)).isoformat(),
            (STARTED + timedelta(hours=1)).isoformat(),
            body_sha,
            extract_sha,
            str(DancerPage.EXTRACT_VERSION),
        ),
    )
    conn.execute(
        (
            "INSERT INTO snapshots (snapshot_id, watch_id, method, url, fetched_at, http_status, "
            "body_sha256, body_bytes, content_changed, run_id, classification, extract_status, "
            "extract_sha256, parse_status, parsed_at, extract_version, parser_version) VALUES "
            "('v1-snapshot', %s, 'POST', %s, %s, 404, %s, %s, 1, 'v1', 'Ok', 'ok', %s, 'ok', %s, %s, %s)"
        ),
        (
            spec.watch_id,
            spec.url,
            OLD_CHECK.isoformat(),
            body_sha,
            len(body),
            extract_sha,
            OLD_CHECK.isoformat(),
            str(DancerPage.EXTRACT_VERSION),
            str(DancerPage.PARSER_VERSION),
        ),
    )
    conn.execute(
        (
            "INSERT INTO observations (observation_id, watch_id, snapshot_id, kind, scope_kind, "
            "scope_id, seq, extract_version, parser_version, payload_json) VALUES ('v1-observation', %s, "
            "'v1-snapshot', 'dancer_lookup', 'dancer', %s, 0, %s, %s, %s)"
        ),
        (
            spec.watch_id,
            str(NUMBER),
            str(DancerPage.EXTRACT_VERSION),
            str(DancerPage.PARSER_VERSION),
            encode_payload(DancerLookup("dancer_lookup", "not_found", NUMBER)),
        ),
    )
    conn.executemany(
        "INSERT INTO cursors (name, value) VALUES (%s, %s)",
        (
            ("registry_probe_cursor", str(NUMBER)),
            ("registry_probe_started_at", STARTED.isoformat()),
            ("registry_probe_misses", "0"),
        ),
    )
    if damage == "missing_body":
        archive.blob_path(body_sha).unlink()
    elif damage == "corrupt_body":
        archive.blob_path(body_sha).write_bytes(b"broken")
    elif damage == "missing_extract":
        archive.extract_path(extract_sha).unlink()
    elif damage == "obsolete_parser":
        conn.execute("UPDATE snapshots SET parser_version = 'obsolete'")
    from swingset.state.controls import Selector, change_control
    from swingset.state.verification import record_check

    with database.transaction():
        record_check(
            conn,
            archive,
            watch_id=spec.watch_id,
            checked_at=OLD_CHECK.isoformat(),
            http_status=404,
            body_sha256=body_sha,
            successful=True,
            snapshot_id="v1-snapshot",
        )
    database.close()
    change_control(
        path,
        selector=Selector("source", "wsdc_registry"),
        paused=True,
        actor="test:operator",
        reason="G1 rehearsal",
        now=OLD_CHECK,
        hosts=("points.worldsdc.com",),
    )
    return spec, body_sha, body


@pytest.mark.parametrize(
    "damage,reason",
    [
        (None, "verified"),
        ("missing_body", "missing_artifact"),
        ("corrupt_body", "corrupt_artifact"),
        ("missing_extract", "missing_artifact"),
        ("obsolete_parser", "interpretation_stale"),
    ],
)
def test_v1_migration_preserves_provenance_pause_and_unknown_freshness(
    legacy_sqlite, damage, reason, request
):
    legacy_sqlite.assert_case("test_registry_g1.py", request.node.name)


def test_g1_one_identical_miss_advances_migrated_probe_without_new_claim(tmp_path, capsys):
    path = tmp_path / "legacy"
    spec, digest, body = retained_state(path)
    clock = FakeClock(STARTED + timedelta(hours=2))
    requests = []

    def respond(request):
        requests.append(request.url.path)
        return httpx.Response(404, content=b"" if request.url.path == "/robots.txt" else body)

    config = Config({"points.worldsdc.com": HostConfig()}, {"wsdc_registry": SourceConfig(True)})
    with open_database(path) as db:
        assert main(["doctor", "--state", str(path), "--json"]) == 0
        before_report = json.loads(capsys.readouterr().out)
        before_cursors = {row["name"]: row["value"] for row in before_report["registry_cursors"]}
        assert before_cursors["registry_probe_cursor"] == str(NUMBER)
        run = db.start_run(clock.now(), dry_run=True)
        client = FetchClient(
            db.connection, config, clock, Archive(path), transport=httpx.MockTransport(respond)
        )
        assert client.fetch(spec.watch_id, DancerPage(), run).skipped
        assert requests == []
        # This removes only the pause in the disposable test database.
        db.connection.execute(
            "DELETE FROM operator_pauses WHERE scope_kind = 'source' AND scope_id = 'wsdc_registry'"
        )
        response = client.fetch(spec.watch_id, DancerPage(), run)
        assert response.snapshot_id is None
        assert not response.changed
        with db.transaction():
            advance_sweep(db, clock.now())
        check = usable_verification(db.connection, spec.watch_id)
        assert check["body_sha256"] == digest
        assert check["checked_at"] > STARTED.isoformat()
        assert check["outcome"] == "not_found"
        assert db.connection.execute(
            "SELECT value FROM cursors WHERE name = 'registry_probe_cursor'"
        ).fetchone()[0] == str(NUMBER + 1)
        assert (
            db.connection.execute(
                "SELECT value FROM cursors WHERE name = 'registry_probe_misses'"
            ).fetchone()[0]
            == "1"
        )
        assert db.connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 0
        assert requests.count("/lookup2020/find") == 1
        client.close()
    assert main(["doctor", "--state", str(path), "--json"]) == 0
    after_report = json.loads(capsys.readouterr().out)
    after_cursors = {row["name"]: row["value"] for row in after_report["registry_cursors"]}
    assert after_cursors["registry_probe_cursor"] == str(NUMBER + 1)
    assert after_cursors["registry_probe_misses"] == "1"
