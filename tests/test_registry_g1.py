"""Offline G1 rehearsal: migrate a paused v1 probe, then verify one identical miss."""

import json
import sqlite3
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


def legacy_state(path, *, damage=None):
    path.mkdir()
    conn = sqlite3.connect(path / "state.sqlite", isolation_level=None)
    conn.executescript(Path("src/swingset/state/migrations/0001_init.sql").read_text())
    conn.execute("PRAGMA user_version=1")
    spec = SOURCE.watch(NUMBER)
    body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1000000.body").read_bytes()
    archive = Archive(path)
    body_sha = archive.store_body(body)
    extract_sha = archive.store_extract(DancerPage().extract(body))
    conn.execute(
        "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('v1',?,1)", (OLD_CHECK.isoformat(),)
    )
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,form,parser,source_ref,state,next_check_at,"
        "last_checked_at,body_sha256,fingerprint,extract_version,current_observation_snapshot_id,notes) "
        "VALUES (?,?,'dancer','POST',?,?,?,?,'registry',?,?,?,?,?,'v1-snapshot','probe')",
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
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,"
        "content_changed,run_id,classification,extract_status,extract_sha256,parse_status,parsed_at,extract_version,parser_version) "
        "VALUES ('v1-snapshot',?,'POST',?,?,404,?,?,1,'v1','Ok','ok',?,'ok',?,?,?)",
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
        "INSERT INTO observations VALUES ('v1-observation',?,'v1-snapshot','dancer_lookup','dancer',?,0,?,?,?)",
        (
            spec.watch_id,
            str(NUMBER),
            str(DancerPage.EXTRACT_VERSION),
            str(DancerPage.PARSER_VERSION),
            encode_payload(DancerLookup("dancer_lookup", "not_found", NUMBER)),
        ),
    )
    conn.executemany(
        "INSERT INTO cursors(name,value) VALUES (?,?)",
        (
            ("registry_probe_cursor", str(NUMBER)),
            ("registry_probe_started_at", STARTED.isoformat()),
            ("registry_probe_misses", "0"),
        ),
    )
    conn.execute(
        "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES ('source','wsdc_registry','G1 rehearsal')"
    )
    if damage == "missing_body":
        archive.blob_path(body_sha).unlink()
    elif damage == "corrupt_body":
        archive.blob_path(body_sha).write_bytes(b"broken")
    elif damage == "missing_extract":
        archive.extract_path(extract_sha).unlink()
    elif damage == "obsolete_parser":
        conn.execute("UPDATE snapshots SET parser_version='obsolete'")
    conn.close()
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
def test_v1_migration_preserves_provenance_pause_and_unknown_freshness(tmp_path, damage, reason):
    path = tmp_path / "legacy"
    spec, digest, _body = legacy_state(path, damage=damage)
    with open_database(path) as db:
        check = db.connection.execute("SELECT * FROM registry_verifications").fetchone()
        assert check["checked_at"] == OLD_CHECK.isoformat()
        assert check["body_sha256"] == digest
        assert check["snapshot_id"] == "v1-snapshot"
        assert check["reason"] == reason
        assert bool(check["usable"]) == (damage is None)
        assert (
            db.connection.execute("SELECT snapshot_id FROM observations").fetchone()[0]
            == "v1-snapshot"
        )
        assert (
            db.connection.execute("SELECT reason FROM operator_pauses").fetchone()[0]
            == "G1 rehearsal"
        )
        advance_sweep(db, STARTED + timedelta(hours=2))
        assert db.connection.execute(
            "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
        ).fetchone()[0] == str(NUMBER)
    with open_database(path) as db:
        assert (
            db.connection.execute("SELECT COUNT(*) FROM registry_verifications").fetchone()[0] == 1
        )
        assert (usable_verification(db.connection, spec.watch_id) is not None) == (damage is None)


def test_g1_one_identical_miss_advances_migrated_probe_without_new_claim(tmp_path, capsys):
    path = tmp_path / "legacy"
    spec, digest, body = legacy_state(path)
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
            "DELETE FROM operator_pauses WHERE scope_kind='source' AND scope_id='wsdc_registry'"
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
            "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
        ).fetchone()[0] == str(NUMBER + 1)
        assert (
            db.connection.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_misses'"
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
