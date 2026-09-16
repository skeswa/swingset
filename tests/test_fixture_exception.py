"""The fixture exception never grants source admission or writes production facts."""

import importlib
import json
import sqlite3
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig
from swingset.fetch.archive import Archive, digest
from swingset.fetch.client import USER_AGENT
from swingset.state.db import open_database

REPO = Path(__file__).resolve().parents[1]
MANIFEST = (
    REPO
    / "journal/evidence/admission/fixture-exception/v2-new-source-fixture-targets-2026-09-13.json"
)
sys.path.insert(0, str(REPO))
try:
    exception = importlib.import_module("journal.tools.admission.fixture_exception")
    transport = importlib.import_module("journal.tools.admission.fixture_transport")
finally:
    sys.path.pop(0)
APPROVAL, HOST, MANIFEST_SHA = exception.APPROVAL, exception.HOST, exception.MANIFEST_SHA
FixtureStopped = exception.FixtureStopped
accounting_connection, authorize = exception.accounting_connection, exception.authorize
read_manifest = exception.read_manifest
FixtureRunner, archive_redirect = transport.FixtureRunner, transport.archive_redirect


@pytest.fixture
def setup(tmp_path):
    clock = FakeClock()
    state, output = tmp_path / "production", tmp_path / "quarantine"
    with open_database(state) as db:
        sha = Archive(state).store_body(b"User-agent: *\nAllow: /\n")
        db.connection.execute(
            "INSERT INTO hosts(host,robots_sha256,robots_status,robots_fetched_at) VALUES (?,?,200,?)",
            (HOST, sha, clock.now().isoformat()),
        )
    record = {
        "approval": APPROVAL,
        "manifest_canonical_sha256": MANIFEST_SHA,
        "state": str(state.resolve()),
        "quarantine": str(output.resolve()),
        "published_stages": ["V2", "V4"],
        "approved_by": "offline-test-owner",
        "owner_decision_reference": "offline-test-only",
        "publication_receipt_reference": "offline-test-publication",
        "approved_at": clock.now().isoformat(),
        "execution_window": {
            "starts_at": clock.now().isoformat(),
            "expires_at": (clock.now() + timedelta(hours=1)).isoformat(),
        },
    }
    return state, output, clock, record


def runner(setup, conn, handler, *, policy=None):
    state, output, clock, record = setup
    return FixtureRunner(
        conn,
        Config({HOST: policy or HostConfig()}, {}),
        clock,
        state=state,
        output=output,
        manifest=read_manifest(MANIFEST, REPO),
        authorization=record,
        transport=httpx.MockTransport(handler),
    )


def response(code=200, body=b"<html>Unreviewed fixture</html>", **headers):
    return httpx.Response(code, headers=headers, stream=httpx.ByteStream(body))


def facts(conn):
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {
        table: [tuple(row) for row in conn.execute(f'SELECT * FROM "{table}"')]
        for table in tables
        if table not in {"hosts", "host_budget"}
    }


def test_dry_run_does_not_open_state_or_request(tmp_path):
    output = subprocess.check_output(
        [
            sys.executable,
            "-m",
            "journal.tools.admission.fixture_exception",
            "--manifest",
            str(MANIFEST),
            "--state",
            str(tmp_path / "absent"),
            "--quarantine",
            str(tmp_path / "out"),
        ],
        cwd=REPO,
    )
    receipt = json.loads(output)
    assert receipt["dry_run"] and receipt["requests_made"] == 0
    assert not list(tmp_path.iterdir())


def test_authorization_and_exact_manifest_fail_closed(setup, tmp_path):
    state, output, clock, record = setup
    with pytest.raises(FixtureStopped, match="required"):
        authorize(None, state=state, output=output, now=clock.now())
    auth = tmp_path / "auth.json"
    for altered in [
        {**record, "approval": "pending"},
        {**record, "published_stages": ["V3"]},
        {**record, "quarantine": str(tmp_path / "different")},
        {
            **record,
            "execution_window": {
                "starts_at": clock.now().isoformat(),
                "expires_at": clock.now().isoformat(),
            },
        },
        {**record, "owner_decision_reference": ""},
    ]:
        auth.write_text(json.dumps(altered))
        with pytest.raises(FixtureStopped):
            authorize(auth, state=state, output=output, now=clock.now())
    auth.write_text(json.dumps(record))
    assert authorize(auth, state=state, output=output, now=clock.now()) == record
    bad = json.loads(MANIFEST.read_text())
    bad["targets"][0]["replay_url"] = "https://example.test/"
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(bad))
    with pytest.raises(FixtureStopped, match="exact reviewed"):
        read_manifest(changed, REPO)


def test_success_uses_shared_gate_and_preserves_all_production_facts(setup):
    state, output, clock, _ = setup
    calls = []

    def handler(request):
        calls.append((clock.now(), str(request.url)))
        assert request.headers["user-agent"] == USER_AGENT
        assert request.headers["accept-encoding"] == "identity"
        assert "cookie" not in request.headers
        if "showNumPages" in str(request.url):
            return response(body=b'{"pages":3}')
        if "/cdx/" in str(request.url):
            return response(
                body=b'[["timestamp","original"],["20250101000000","https://unapproved.test/"]]'
            )
        return response(**{"set-cookie": "must_not_escape=yes"})

    with accounting_connection(state) as conn:
        before = facts(conn)
        robots = conn.execute("SELECT robots_sha256 FROM hosts").fetchone()[0]
        result = runner(setup, conn, handler).run()
        assert result["status"] == "captured_pending_independent_review"
        assert facts(conn) == before
        assert len(calls) == 7
        assert all(
            (later[0] - earlier[0]).total_seconds() >= 10
            for earlier, later in zip(calls, calls[1:], strict=False)
        )
        budget = conn.execute("SELECT requests,bytes FROM host_budget").fetchone()
        assert tuple(budget) == (7, result["received_bytes"])
        assert result["cdx_further_pages_unexamined"] == 2
        assert len(result["targets"]) == 5
        assert conn.execute("SELECT robots_sha256 FROM hosts").fetchone()[0] == robots
        for item in result["requests"]:
            body = (output / "bodies" / item["body_sha256"]).read_bytes()
            assert digest(body) == item["body_sha256"]
            assert item["complete"]
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("INSERT INTO meta(key,value) VALUES ('forbidden','1')")
        with pytest.raises(FixtureStopped, match="single-use"):
            runner(setup, conn, handler)


@pytest.mark.parametrize(
    "location",
    [
        "https://steprightsolutions.com/events",
        "http://web.archive.org/robots.txt",
        "https://web.archive.org/web/20130413022752id_/https://unapproved.test/",
        "https://web.archive.org:444/robots.txt",
        "https://user@web.archive.org/robots.txt",
    ],
)
def test_redirect_escape_stops_after_one_charged_request(setup, location):
    state, output, _, _ = setup
    with accounting_connection(state) as conn:
        result = runner(setup, conn, lambda _: response(302, location=location)).run()
        assert result["status"] == "stopped_incomplete"
        assert "redirect" in result["stop_reason"]
        assert len(result["requests"]) == 1
        assert conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 1
    assert output.exists()


@pytest.mark.parametrize("status", [429, 503, 500])
def test_failure_never_retries_and_retains_host_pause(setup, status):
    with accounting_connection(setup[0]) as conn:
        result = runner(setup, conn, lambda _: response(status)).run()
        assert result["status"] == "stopped_incomplete"
        assert len(result["requests"]) == 1
        assert conn.execute("SELECT paused_until FROM hosts").fetchone()[0]


def test_shared_pause_and_exhausted_daily_budget_make_no_requests(setup):
    state, _, clock, _ = setup
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO host_budget VALUES (?,?,200,0)", (HOST, clock.now().date().isoformat())
        )
    with accounting_connection(state) as conn:
        result = runner(setup, conn, lambda _: pytest.fail("request made")).run()
        assert "request budget" in result["stop_reason"]
        assert not result["requests"]


def test_operator_pause_is_not_bypassed(setup):
    state = setup[0]
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES ('all','all','hold')"
        )
    with accounting_connection(state) as conn:
        result = runner(setup, conn, lambda _: pytest.fail("request made")).run()
        assert "operator" in result["stop_reason"]
        assert not result["requests"]


def test_byte_ceiling_keeps_prefix_and_never_exceeds_shared_byte_capacity(setup):
    with accounting_connection(setup[0]) as conn:
        result = runner(
            setup,
            conn,
            lambda _: response(body=b"x" * 101),
            policy=HostConfig(daily_byte_budget=100),
        ).run()
        assert "byte ceiling" in result["stop_reason"]
        assert result["received_bytes"] == 100
        assert not result["requests"][0]["complete"]
        assert tuple(conn.execute("SELECT requests,bytes FROM host_budget").fetchone()) == (1, 100)


def test_request_ceiling_counts_redirects(setup):
    seen = {}

    def handler(request):
        url = str(request.url)
        original = url.split("id_/", 1)[1]
        seen[original] = seen.get(original, 0) + 1
        if seen[original] < 3:
            return response(
                302, location=f"https://{HOST}/web/2019010100000{seen[original]}id_/{original}"
            )
        return response()

    with accounting_connection(setup[0]) as conn:
        result = runner(setup, conn, handler).run()
        assert result["stop_reason"] == "fixture request ceiling"
        assert len(result["requests"]) == 12
        assert len(result["targets"]) == 4


def test_deadline_prevents_next_read_or_request(setup):
    clock = setup[2]

    def handler(request):
        clock.sleep(901)
        return response()

    with accounting_connection(setup[0]) as conn:
        result = runner(setup, conn, handler).run()
        assert "elapsed-time" in result["stop_reason"]
        assert len(result["requests"]) == 1
        assert not result["requests"][0]["complete"]


def test_fresh_robots_is_quarantined_without_dangling_production_blob_reference(setup):
    state = setup[0]
    with open_database(state) as db:
        db.connection.execute("UPDATE hosts SET robots_fetched_at=NULL")

    def handler(request):
        if request.url.path == "/robots.txt":
            return response(body=b"User-agent: *\nDisallow: /\n")
        pytest.fail("robots disallowed request")

    with accounting_connection(state) as conn:
        result = runner(setup, conn, handler).run()
        assert result["stop_reason"] == "robots disallow"
        assert len(result["requests"]) == 1
        assert conn.execute("SELECT robots_fetched_at FROM hosts").fetchone()[0] is None


def test_redirect_may_change_capture_but_not_original_resource():
    initial = (
        "https://web.archive.org/web/20130413022752id_/http://www.steprightsolutions.com:80/events"
    )
    assert archive_redirect(initial, initial.replace("20130413022752", "20130414000000"))
    assert not archive_redirect(initial, initial + "/another-event")


def test_source_pause_applies_to_fixture_purpose(setup):
    state = setup[0]
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES ('source','steprightsolutions','hold')"
        )
    with accounting_connection(state) as conn:
        result = runner(setup, conn, lambda _: pytest.fail("request made")).run()
        assert "operator" in result["stop_reason"]
        assert not result["requests"]


def test_existing_writer_lock_prevents_accounting_access(setup):
    with accounting_connection(setup[0]):
        with pytest.raises(FixtureStopped, match="writer lock"):
            with accounting_connection(setup[0]):
                pytest.fail("two owners acquired the writer lock")


def test_real_process_crash_leaves_conservative_budget_and_no_facts(setup, tmp_path):
    state, output, _, record = setup
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps(record))
    script = """
import json, os, sys
from pathlib import Path
import httpx
from journal.tools.admission.fixture_exception import accounting_connection, read_manifest
from journal.tools.admission.fixture_transport import FixtureRunner
from swingset.clock import FakeClock
from swingset.config import Config
state, output, auth, manifest, repo = map(Path, sys.argv[1:])
with accounting_connection(state) as conn:
    runner = FixtureRunner(conn, Config({}, {}), FakeClock(), state=state, output=output,
        manifest=read_manifest(manifest, repo), authorization=json.loads(auth.read_text()),
        transport=httpx.MockTransport(lambda _: os._exit(77)))
    runner.run()
"""
    with accounting_connection(state) as conn:
        before = facts(conn)
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(state),
            str(output),
            str(auth),
            str(MANIFEST),
            str(REPO),
        ],
        cwd=REPO,
        check=False,
    )
    assert process.returncode == 77
    receipt = json.loads((output / "receipt.json").read_bytes())
    assert receipt["status"] == "running"
    assert len(receipt["requests"]) == 1
    assert not receipt["requests"][0]["complete"]
    with accounting_connection(state) as conn:
        assert facts(conn) == before
        assert tuple(conn.execute("SELECT requests,bytes FROM host_budget").fetchone()) == (
            1,
            8 * 1024**2,
        )
        with pytest.raises(FixtureStopped, match="single-use"):
            runner(setup, conn, lambda _: pytest.fail("request made"))


def test_response_ceiling_and_total_ceiling_are_independent(setup):
    with accounting_connection(setup[0]) as conn:
        result = runner(setup, conn, lambda _: response(body=b"x" * (8 * 1024**2 + 1))).run()
        assert "byte ceiling" in result["stop_reason"]
        assert result["received_bytes"] == 8 * 1024**2
        assert len(result["requests"]) == 1


def test_aggregate_byte_ceiling_stops_before_cdx_or_extra_body(setup):
    with accounting_connection(setup[0]) as conn:
        result = runner(setup, conn, lambda _: response(body=b"x" * (7 * 1024**2))).run()
        assert "byte ceiling" in result["stop_reason"]
        assert result["received_bytes"] == 32 * 1024**2
        assert len(result["requests"]) == 5
        assert len(result["targets"]) == 4
        assert not result["requests"][-1]["complete"]
        assert conn.execute("SELECT bytes FROM host_budget").fetchone()[0] == 32 * 1024**2


def test_execute_without_authorization_never_opens_state(tmp_path):
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "journal.tools.admission.fixture_exception",
            "--manifest",
            str(MANIFEST),
            "--state",
            str(tmp_path / "absent"),
            "--quarantine",
            str(tmp_path / "out"),
            "--execute",
        ],
        cwd=REPO,
        capture_output=True,
        check=False,
    )
    assert process.returncode != 0
    assert b"explicit owner authorization record is required" in process.stderr
    assert not list(tmp_path.iterdir())


def test_unlisted_initial_archive_resource_is_refused_before_io(setup):
    with accounting_connection(setup[0]) as conn:
        prepared = runner(setup, conn, lambda _: pytest.fail("request made"))
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: pytest.fail("request made"))
        ) as client:
            with pytest.raises(FixtureStopped, match="absent from the exact"):
                prepared._request(
                    client,
                    "https://web.archive.org/web/20130413022752id_/http://unapproved.test/",
                    "srs-index",
                )
        assert conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0


def test_owner_authorization_persists_when_unstarted_execution_window_moves(setup, tmp_path):
    state, output, clock, record = setup
    record["approved_at"] = (clock.now() - timedelta(days=30)).isoformat()
    auth = tmp_path / "authorization.json"
    auth.write_text(json.dumps(record))
    assert authorize(auth, state=state, output=output, now=clock.now()) == record
    # The same human decision may support a later window before any intake starts.
    clock.sleep(2 * 86400)
    record["execution_window"] = {
        "starts_at": clock.now().isoformat(),
        "expires_at": (clock.now() + timedelta(hours=1)).isoformat(),
    }
    auth.write_text(json.dumps(record))
    assert authorize(auth, state=state, output=output, now=clock.now()) == record
    output.mkdir()
    with pytest.raises(FixtureStopped, match="single-use"):
        authorize(auth, state=state, output=output, now=clock.now())
