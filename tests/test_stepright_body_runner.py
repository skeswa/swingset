"""Offline checks for the exact Step Right archived-parent-body runner."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import shutil
import signal
import sqlite3
import subprocess
import sys
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import journal.tools.admission.stepright_body_runner as runner_module
from journal.tools.admission.stepright_body_runner import (
    AUTHORITY,
    CAPTURE,
    HELD_UNITS,
    HOST,
    KIND,
    LOCATOR_AUDIT_SHA256,
    MAX_ELAPSED_SECONDS,
    MAX_REQUESTS,
    MAX_RESPONSE,
    MAX_TOTAL,
    ORIGINAL_URL,
    PUBLISHED_BASELINE,
    REPLAY_URL,
    RUNNER_HELPERS,
    SOURCE,
    StepRightBodyRunner,
    StepRightStopped,
    accounting_connection,
    archive_prestate,
    capture_time,
    check_ordinary_source_policy,
    decoded_chunks,
    preflight_report,
    sha256,
    validate_execution_gate,
    validate_locator_audit,
    verify_live_publication,
    verify_live_system,
    verify_loaded_swingset_modules,
    verify_runner_closure,
    verify_source,
)
from swingset.clock import FakeClock
from swingset.config import Config, HostConfig
from swingset.fetch.archive import Archive, canonical
from swingset.fetch.client import USER_AGENT
from swingset.state import db as db_module
from swingset.state.db import open_database

REPO = Path(__file__).resolve().parents[1]


def response(
    status: int = 200,
    body: bytes = b"<html><a href='/events/asianopen2015/round/1126'>round</a></html>",
    **headers: str,
) -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # The runner is frozen at the schema it was reviewed against (D-0013).
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 29)
    clock = FakeClock()
    state, output = tmp_path / "state", tmp_path / "quarantine"
    source_root = tmp_path / "source"
    config_dir = source_root / "config"
    config_dir.mkdir(parents=True)
    sources = config_dir / "sources.toml"
    hosts = config_dir / "hosts.toml"
    sources.write_text("[sources.other]\nenabled = true\n")
    hosts.write_text(
        '[hosts."web.archive.org"]\nmin_gap_seconds = 10\ndaily_request_budget = 200\n'
    )
    with open_database(state) as database:
        (state / "operator-hold").write_text("held\n")
        revision = int(
            database.connection.execute(
                "SELECT revision FROM control_state WHERE singleton=1"
            ).fetchone()[0]
        )
    candidate = state / "candidates/cand_8f31cad7226643ae"
    candidate.mkdir(parents=True)
    publication = {
        "commit": PUBLISHED_BASELINE,
        "candidate_id": candidate.name,
        "closure_digest": "reviewed-publication-closure",
    }
    publication_body = canonical(publication)
    (candidate / "PUBLISHED").write_bytes(publication_body)
    (state / "baseline").symlink_to(candidate)
    authorization = {
        "operation_id": "offline-stepright-body-001",
        "source": SOURCE,
        "kind": KIND,
        "capture": CAPTURE,
        "replay_url": REPLAY_URL,
        "state": str(state.resolve()),
        "quarantine": str(output.resolve()),
        "owner_decision_reference": AUTHORITY,
        "maximum_archive_body_requests": 1,
        "approved_at": clock.now().isoformat(),
        "execution_window": {
            "starts_at": clock.now().isoformat(),
            "expires_at": (clock.now() + timedelta(hours=1)).isoformat(),
        },
    }
    gate = {
        "format": "stepright-exact-body-execution-gate-v1",
        "source": str(source_root.resolve()),
        "source_receipt_sha256": "reviewed-source-receipt",
        "schema_version": 29,
        "state": str(state.resolve()),
        "quarantine": str(output.resolve()),
        "authorization_sha256": sha256(canonical(authorization)),
        "ordinary_source_config_sha256": sha256(sources.read_bytes()),
        "host_config_sha256": sha256(hosts.read_bytes()),
        "control_revision": revision,
        "source_name": SOURCE,
        "kind": KIND,
        "capture": CAPTURE,
        "replay_url": REPLAY_URL,
        "body_redirects": 0,
        "body_requests": 1,
        "robots_redirects": 0,
        "maximum_http_requests": MAX_REQUESTS,
        "maximum_response_bytes": MAX_RESPONSE,
        "maximum_total_bytes": MAX_TOTAL,
        "maximum_elapsed_seconds": MAX_ELAPSED_SECONDS,
        "origin_requests": 0,
        "production_facts_or_watches_created": 0,
        "inactive_units": list(HELD_UNITS),
        "system": "/nix/store/reviewed-system",
        "locator_audit_sha256": LOCATOR_AUDIT_SHA256,
        "published_commit": PUBLISHED_BASELINE,
        "published_candidate_id": candidate.name,
        "published_closure_digest": publication["closure_digest"],
        "publication_receipt_sha256": sha256(publication_body),
        "baseline_path": str(candidate.resolve()),
        "runner_closure_sha256": "reviewed-runner-closure",
        "operator_hold_sha256": sha256((state / "operator-hold").read_bytes()),
    }
    config = Config({HOST: HostConfig(min_gap_seconds=10, daily_request_budget=200)}, {})
    return state, output, source_root, sources, hosts, clock, authorization, gate, config


def make(setup, conn, handler, *, gate=None, config=None):
    state, output, _, sources, hosts, clock, authorization, reviewed_gate, ordinary = setup
    selected_gate = gate or reviewed_gate
    if gate is None:
        selected_gate["archive_prestate"] = archive_prestate(conn, state, observed_at=clock.now())
    return StepRightBodyRunner(
        conn,
        config or ordinary,
        clock,
        state=state,
        output=output,
        source_config=sources,
        host_config=hosts,
        authorization=authorization,
        execution_gate=selected_gate,
        runner_closure_sha256=selected_gate["runner_closure_sha256"],
        transport=httpx.MockTransport(handler),
        production_guard=lambda: None,
    )


def cache_robots(setup, body=b"User-agent: swingset\nAllow: /\n", status=200, *, fetched_at=None):
    state, _, _, _, _, clock, *_ = setup
    with open_database(state) as database:
        digest = Archive(state).store_body(body)
        database.connection.execute(
            "INSERT INTO hosts(host,robots_sha256,robots_status,robots_fetched_at) "
            "VALUES (?,?,?,?) ON CONFLICT(host) DO UPDATE SET "
            "robots_sha256=excluded.robots_sha256,robots_status=excluded.robots_status,"
            "robots_fetched_at=excluded.robots_fetched_at",
            (HOST, digest, status, (fetched_at or clock.now()).isoformat()),
        )


def production_facts(conn):
    excluded = {"hosts", "host_budget", "host_request_spacing"}
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {
        table: [tuple(row) for row in conn.execute(f'SELECT * FROM "{table}"')]
        for table in tables
        if table not in excluded
    }


def test_exact_body_uses_shared_gate_and_retains_required_receipt(setup):
    cache_robots(setup)
    state, output, *_ = setup
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == REPLAY_URL
        assert request.headers["user-agent"] == USER_AGENT
        assert request.headers["accept-encoding"] == "gzip"
        assert "cookie" not in request.headers
        return response(
            **{
                "memento-datetime": "Sat, 11 Jul 2015 03:58:13 GMT",
                "x-archive-src": "reviewed.warc.gz",
                "set-cookie": "do-not-retain=1",
            }
        )

    with accounting_connection(state) as conn:
        before = production_facts(conn)
        result = make(setup, conn, handler).run()
        assert result["status"] == "captured_pending_independent_review"
        assert production_facts(conn) == before
        assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM watches").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM pending_work").fetchone()[0] == 0
        assert tuple(
            conn.execute("SELECT requests,bytes FROM host_budget WHERE host=?", (HOST,)).fetchone()
        ) == (1, result["targets"][0]["body_bytes"])
    assert len(calls) == 1
    target = result["targets"][0]
    assert target["requested_url"] == target["final_url"] == REPLAY_URL
    assert target["original_url"] == ORIGINAL_URL
    assert target["memento_datetime"] == "2015-07-11T03:58:13+00:00"
    assert target["request_day"] == result["requests"][0]["request_day"]
    assert target["archive_receipt_metadata"]["x-archive-src"] == "reviewed.warc.gz"
    assert "set-cookie" not in target["headers"]
    assert result["requests"][0]["robots_revalidated"]["status"] == 200
    assert result["requests"][0]["robots_revalidated"]["policy_allows_replay"] is True
    assert (output / "bodies" / target["body_sha256"]).read_bytes().startswith(b"<html>")


def test_gzip_body_is_decoded_once_and_retains_original_headers(setup):
    cache_robots(setup)
    state, output, *_ = setup
    calls = []
    plain = b"<html><a href='/events/asianopen2015/round/1126'>round</a></html>"

    def handler(request):
        calls.append(request)
        return response(
            body=gzip.compress(plain),
            **{
                "content-encoding": "gzip",
                "memento-datetime": "Sat, 11 Jul 2015 03:58:13 GMT",
            },
        )

    with accounting_connection(state) as conn:
        result = make(setup, conn, handler).run()

    assert result["status"] == "captured_pending_independent_review"
    assert len(calls) == 1
    assert len(result["requests"]) == 1
    request = result["requests"][0]
    target = result["targets"][0]
    assert request["complete"] is True
    assert request["headers"]["content-encoding"] == "gzip"
    assert target["headers"]["content-encoding"] == "gzip"
    assert target["body_bytes"] == len(plain)
    assert (output / "bodies" / target["body_sha256"]).read_bytes() == plain


def test_explicitly_disabled_ordinary_source_cannot_be_overridden(setup):
    state, output, _, sources, _, _, _, _, _ = setup
    sources.write_text(f"[sources.{SOURCE}]\nenabled = false\n")
    with accounting_connection(state) as conn:
        with pytest.raises(StepRightStopped, match="explicitly disabled"):
            make(setup, conn, lambda _: pytest.fail("network request made"))
        assert conn.execute("SELECT count(*) FROM host_budget").fetchone()[0] == 0
    assert not output.exists()


def test_explicit_disable_inserted_after_admission_stops_before_socket(setup):
    cache_robots(setup)
    state, _, _, sources, *_ = setup
    with accounting_connection(state) as conn:
        runner = make(setup, conn, lambda _: pytest.fail("network request made"))
        acquire = runner.gate.acquire

        def disable_after_debit(*args, **kwargs):
            result = acquire(*args, **kwargs)
            sources.write_text(f"[sources.{SOURCE}]\nenabled = false\n")
            return result

        runner.gate.acquire = disable_after_debit
        result = runner.run()
        assert result["status"] == "stopped_incomplete"
        assert "explicitly disabled" in result["stop_reason"]
        assert result["requests"][0]["socket_dispatch_attempted"] is False
        assert conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == 1


def test_missing_robots_cache_fails_closed_without_network(setup):
    state = setup[0]
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return response()

    with accounting_connection(state) as conn:
        with pytest.raises(StepRightStopped, match="requires retained Archive robots prestate"):
            make(setup, conn, handler)
    assert calls == []
    assert not setup[1].exists()


def test_stale_robots_cache_fails_closed_before_body(setup):
    cache_robots(setup)
    state, _, _, _, _, clock, *_ = setup
    with open_database(state) as database:
        database.connection.execute(
            "UPDATE hosts SET robots_fetched_at=? WHERE host=?",
            ((clock.now() - timedelta(days=2)).isoformat(), HOST),
        )
    with accounting_connection(state) as conn:
        with pytest.raises(StepRightStopped, match="not fresh and allowing"):
            make(setup, conn, lambda _: pytest.fail("network request made"))
    assert not setup[1].exists()


def test_robots_near_expiry_becomes_stale_during_crawl_delay_before_dispatch(setup):
    state, _, _, _, _, clock, *_ = setup
    cache_robots(
        setup,
        b"User-agent: swingset\nCrawl-delay: 10\nAllow: /\n",
        fetched_at=clock.now() - timedelta(hours=24) + timedelta(seconds=5),
    )
    calls = []
    with accounting_connection(state) as conn:
        result = make(setup, conn, lambda request: calls.append(request) or response()).run()
        budget = conn.execute(
            "SELECT requests,bytes FROM host_budget WHERE host=?", (HOST,)
        ).fetchone()
    assert result["status"] == "stopped_incomplete"
    assert "became stale before dispatch" in result["stop_reason"]
    assert calls == []
    assert result["requests"][0]["socket_dispatch_attempted"] is False
    assert tuple(budget) == (1, MAX_RESPONSE)


def test_robots_near_expiry_becomes_stale_during_shared_spacing_wait(setup):
    state, _, _, _, _, clock, *_ = setup
    cache_robots(
        setup,
        b"",
        status=404,
        fetched_at=clock.now() - timedelta(hours=24) + timedelta(seconds=5),
    )
    with open_database(state) as database:
        database.connection.execute(
            "UPDATE hosts SET next_allowed_at=? WHERE host=?",
            ((clock.now() + timedelta(seconds=10)).isoformat(), HOST),
        )
    calls = []
    with accounting_connection(state) as conn:
        result = make(setup, conn, lambda request: calls.append(request) or response()).run()
        budget = conn.execute(
            "SELECT requests,bytes FROM host_budget WHERE host=?", (HOST,)
        ).fetchone()
    assert result["status"] == "stopped_incomplete"
    assert "became stale before dispatch" in result["stop_reason"]
    assert calls == []
    assert result["requests"][0]["socket_dispatch_attempted"] is False
    assert tuple(budget) == (1, MAX_RESPONSE)


def test_fresh_disallowing_robots_stops_without_request(setup):
    cache_robots(setup, b"User-agent: swingset\nDisallow: /web/\n")
    state = setup[0]
    with accounting_connection(state) as conn:
        with pytest.raises(StepRightStopped, match="not fresh and allowing"):
            make(setup, conn, lambda _: pytest.fail("network request made"))
        assert conn.execute("SELECT count(*) FROM host_budget").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("headers", "body", "message"),
    [
        ({}, b"body", "lacks Memento"),
        ({"memento-datetime": "bad"}, b"body", "malformed Memento"),
        (
            {"memento-datetime": "Sun, 12 Jul 2015 03:58:13 GMT"},
            b"body",
            "differs from the authorized",
        ),
        ({"memento-datetime": "Sat, 11 Jul 2015 03:58:13 GMT"}, b"", "body is empty"),
    ],
)
def test_invalid_or_empty_memento_body_is_terminal(setup, headers, body, message):
    cache_robots(setup)
    with accounting_connection(setup[0]) as conn:
        result = make(setup, conn, lambda _: response(body=body, **headers)).run()
    assert result["status"] == "stopped_incomplete"
    assert message in result["stop_reason"]
    assert len(result["requests"]) == 1
    assert result["targets"] == []


def test_body_redirect_is_retained_but_never_followed(setup):
    cache_robots(setup)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return response(302, b"moved", location=REPLAY_URL.replace(CAPTURE, "20150712000000"))

    with accounting_connection(setup[0]) as conn:
        result = make(setup, conn, handler).run()
    assert result["status"] == "stopped_incomplete"
    assert "body redirect is outside the exact fixture scope" in result["stop_reason"]
    assert calls == [REPLAY_URL]
    assert len(result["requests"]) == 1


def test_foreign_url_is_rejected_before_debit_or_io(setup):
    cache_robots(setup)
    state = setup[0]
    with accounting_connection(state) as conn:
        runner = make(setup, conn, lambda _: pytest.fail("network request made"))
        with httpx.Client(
            transport=httpx.MockTransport(lambda _: pytest.fail("request"))
        ) as client:
            with pytest.raises(StepRightStopped, match="outside the exact allowlist"):
                runner._request(client, "https://example.test/", "body")
        assert conn.execute("SELECT count(*) FROM host_budget").fetchone()[0] == 0


def test_host_pause_and_shared_request_budget_stop_before_io(setup):
    state, _, _, _, _, clock, *_ = setup
    with open_database(state) as database:
        database.connection.execute(
            "INSERT INTO host_budget(host,day,requests,bytes) VALUES (?,?,200,0)",
            (HOST, clock.now().date().isoformat()),
        )
    cache_robots(setup)
    with accounting_connection(state) as conn:
        result = make(setup, conn, lambda _: pytest.fail("network request made")).run()
    assert result["status"] == "stopped_incomplete"
    assert "request budget" in result["stop_reason"]
    assert result["requests"] == []


def test_existing_archive_cooldown_stops_before_io(setup):
    cache_robots(setup)
    state, _, _, _, _, clock, *_ = setup
    with open_database(state) as database:
        database.connection.execute(
            "UPDATE hosts SET paused_until=?,pause_reason='Blocked' WHERE host=?",
            ((clock.now() + timedelta(hours=1)).isoformat(), HOST),
        )
    with accounting_connection(state) as conn:
        result = make(setup, conn, lambda _: pytest.fail("network request made")).run()
    assert result["status"] == "stopped_incomplete"
    assert "shared Archive gate: Blocked" in result["stop_reason"]
    assert result["requests"] == []


def test_kind_pause_is_enforced_even_though_kind_is_unassessed(setup):
    cache_robots(setup)
    state = setup[0]
    with open_database(state) as database:
        database.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES ('kind',?,'test')",
            (KIND,),
        )
    with accounting_connection(state) as conn:
        result = make(setup, conn, lambda _: pytest.fail("network request made")).run()
    assert result["status"] == "stopped_incomplete"
    assert "operator control" in result["stop_reason"]
    assert result["requests"] == []


def test_response_cap_retains_prefix_and_never_claims_capture(setup):
    cache_robots(setup)
    with accounting_connection(setup[0]) as conn:
        result = make(
            setup,
            conn,
            lambda _: response(
                body=b"x" * (MAX_RESPONSE + 1),
                **{"memento-datetime": "Sat, 11 Jul 2015 03:58:13 GMT"},
            ),
        ).run()
        budget = conn.execute("SELECT requests,bytes FROM host_budget").fetchone()
    assert result["status"] == "stopped_incomplete"
    assert result["requests"][0]["complete"] is False
    assert result["requests"][0]["body_bytes"] == MAX_RESPONSE
    assert tuple(budget) == (1, MAX_RESPONSE)
    assert result["targets"] == []


def test_transport_failure_has_no_retry_and_keeps_full_reservation(setup):
    cache_robots(setup)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        raise httpx.ConnectError("offline", request=request)

    with accounting_connection(setup[0]) as conn:
        result = make(setup, conn, handler).run()
        budget = conn.execute("SELECT requests,bytes FROM host_budget").fetchone()
    assert result["status"] == "stopped_incomplete"
    assert calls == [REPLAY_URL]
    assert tuple(budget) == (1, MAX_RESPONSE)
    assert result["requests"][0]["classification"] == "ServerError"


def test_process_death_keeps_durable_dispatch_ambiguity_and_full_debit(setup, tmp_path):
    cache_robots(setup)
    state, output, _, sources, hosts, _, authorization, gate, _ = setup
    with accounting_connection(state) as conn:
        gate["archive_prestate"] = archive_prestate(conn, state, observed_at=setup[5].now())
    authorization_path = tmp_path / "authorization.json"
    gate_path = tmp_path / "gate.json"
    authorization_path.write_bytes(canonical(authorization))
    gate_path.write_bytes(canonical(gate))
    script = """
import json, os, signal, sys
from pathlib import Path
import httpx
import swingset.state.db as db_module
# The runner is frozen at the schema it was reviewed against (D-0013).
db_module.SCHEMA_VERSION = 29
from journal.tools.admission.stepright_body_runner import StepRightBodyRunner, accounting_connection
from swingset.clock import FakeClock
from swingset.config import Config, HostConfig
state, output, sources, hosts, authorization_path, gate_path = map(Path, sys.argv[1:])
authorization = json.loads(authorization_path.read_bytes())
gate = json.loads(gate_path.read_bytes())
def crash(_request):
    os.kill(os.getpid(), signal.SIGKILL)
with accounting_connection(state) as conn:
    StepRightBodyRunner(
        conn, Config({'web.archive.org': HostConfig(min_gap_seconds=10)}, {}), FakeClock(),
        state=state, output=output, source_config=sources, host_config=hosts,
        authorization=authorization, execution_gate=gate,
        runner_closure_sha256=gate['runner_closure_sha256'],
        transport=httpx.MockTransport(crash), production_guard=lambda: None,
    ).run()
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(state),
            str(output),
            str(sources),
            str(hosts),
            str(authorization_path),
            str(gate_path),
        ],
        cwd=REPO,
        check=False,
    )
    assert process.returncode == -signal.SIGKILL
    receipt = json.loads((output / "receipt.json").read_bytes())
    request = receipt["requests"][0]
    assert receipt["status"] == "running"
    assert request["dispatch_state"] == "dispatch_ambiguous_or_issued"
    assert request["socket_dispatch_attempted"] is True
    assert request["reserved_bytes"] == MAX_RESPONSE
    assert request["complete"] is False
    with accounting_connection(state) as conn:
        assert tuple(
            conn.execute("SELECT requests,bytes FROM host_budget WHERE host=?", (HOST,)).fetchone()
        ) == (1, MAX_RESPONSE)
        with pytest.raises(StepRightStopped, match="single-use"):
            make(setup, conn, lambda _: pytest.fail("network request made"))


def test_gate_validation_rejects_each_exact_scope_change(setup):
    state, output, source_root, sources, hosts, _, authorization, gate, _ = setup
    cache_robots(setup)
    with accounting_connection(state) as conn:
        gate["archive_prestate"] = archive_prestate(conn, state, observed_at=setup[5].now())
        inputs = {
            "conn": conn,
            "source": source_root,
            "state": state,
            "quarantine": output,
            "now": setup[5].now(),
            "authorization_sha256": sha256(canonical(authorization)),
            "source_config_sha256": sha256(sources.read_bytes()),
            "host_config_sha256": sha256(hosts.read_bytes()),
            "control_revision": gate["control_revision"],
            "runner_closure_sha256": gate["runner_closure_sha256"],
        }
        validate_execution_gate(gate, **inputs)
        for key, value in (
            ("replay_url", "https://example.test/"),
            ("source", "/nix/store/other-source"),
            ("body_redirects", 1),
            ("body_requests", 2),
            ("maximum_http_requests", MAX_REQUESTS + 1),
            ("runner_closure_sha256", "changed"),
            ("origin_requests", 1),
            ("ordinary_source_config_sha256", "changed"),
            ("control_revision", gate["control_revision"] + 1),
            ("inactive_units", []),
        ):
            changed = copy.deepcopy(gate)
            changed[key] = value
            with pytest.raises(StepRightStopped, match="execution gate"):
                validate_execution_gate(changed, **inputs)


@pytest.mark.parametrize(
    ("released", "spacing_state"),
    [
        (False, "unreleased_reservation_recovery_required"),
        (True, "released_reservation_recovery_required"),
    ],
)
def test_archive_prestate_binds_budget_spacing_pause_and_robots(setup, released, spacing_state):
    cache_robots(setup, b"", status=404)
    state, _, _, _, _, clock, *_ = setup
    day = clock.now().date().isoformat()
    next_allowed = (clock.now() + timedelta(seconds=10)).isoformat()
    paused_until = (clock.now() + timedelta(hours=1)).isoformat()
    with open_database(state) as database:
        database.connection.execute(
            "INSERT INTO host_budget(host,day,requests,bytes) VALUES (?,?,20,2952065)",
            (HOST, day),
        )
        database.connection.execute(
            "UPDATE hosts SET next_allowed_at=?,paused_until=?,pause_reason='Blocked',"
            "pause_streak=2 WHERE host=?",
            (next_allowed, paused_until, HOST),
        )
        database.connection.execute(
            "INSERT INTO host_request_spacing"
            "(host,reservation_id,gap_seconds,reserved_at,released_at) VALUES (?,?,?,?,?)",
            (
                HOST,
                "reviewed-reservation",
                10,
                clock.now().isoformat(),
                clock.now().isoformat() if released else None,
            ),
        )
    with accounting_connection(state) as conn:
        observed = archive_prestate(conn, state, observed_at=clock.now())
    assert observed["usage"]["utc_day_request_debits"] == 20
    assert observed["usage"]["utc_day_bytes"] == 2_952_065
    assert observed["host_state"] == {
        "row_present": True,
        "next_allowed_at": next_allowed,
        "next_allowed_active_at_observation": True,
        "paused_until": paused_until,
        "pause_active_at_observation": True,
        "pause_reason": "Blocked",
        "pause_streak": 2,
    }
    assert observed["spacing"]["reservation_id"] == "reviewed-reservation"
    assert observed["spacing_state"] == spacing_state
    assert observed["reservation_recovery_required"] is True
    assert observed["robots"]["status"] == 404
    assert observed["robots"]["body_sha256"] == sha256(b"")
    assert observed["robots"]["age_seconds_at_observation"] == 0
    assert observed["robots"]["fresh_at_observation"] is True
    assert observed["robots"]["policy_allows_replay"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        "INSERT INTO host_budget(host,day,requests,bytes) VALUES ('web.archive.org','2026-01-01',1,7)",
        "UPDATE hosts SET next_allowed_at='2026-01-01T00:00:10+00:00' WHERE host='web.archive.org'",
        "UPDATE hosts SET paused_until='2026-01-01T01:00:00+00:00',pause_reason='Blocked' WHERE host='web.archive.org'",
        "INSERT INTO host_request_spacing(host,reservation_id,gap_seconds,reserved_at,released_at) VALUES ('web.archive.org','changed',10,'2026-01-01T00:00:00+00:00',NULL)",
        "UPDATE hosts SET robots_fetched_at='2025-12-31T23:59:59+00:00' WHERE host='web.archive.org'",
    ],
)
def test_execution_gate_rejects_each_changed_archive_prestate_fact(setup, mutation):
    cache_robots(setup)
    state, output, source_root, sources, hosts, clock, authorization, gate, _ = setup
    with open_database(state) as database:
        gate["archive_prestate"] = archive_prestate(
            database.connection, state, observed_at=clock.now()
        )
        database.connection.execute(mutation)
    with accounting_connection(state) as conn:
        with pytest.raises(StepRightStopped, match="shared Archive prestate differs"):
            validate_execution_gate(
                gate,
                conn=conn,
                source=source_root,
                state=state,
                quarantine=output,
                now=clock.now(),
                authorization_sha256=sha256(canonical(authorization)),
                source_config_sha256=sha256(sources.read_bytes()),
                host_config_sha256=sha256(hosts.read_bytes()),
                control_revision=gate["control_revision"],
                runner_closure_sha256=gate["runner_closure_sha256"],
            )


def test_live_publication_receipt_and_baseline_path_are_exact(setup):
    state, _, _, _, _, _, _, gate, _ = setup
    verify_live_publication(state, gate)
    published = Path(gate["baseline_path"]) / "PUBLISHED"
    published.write_bytes(canonical({"commit": "changed"}))
    with pytest.raises(StepRightStopped, match="publication receipt differs"):
        verify_live_publication(state, gate)


def test_live_system_requires_both_generations_and_all_six_inactive():
    system = "/nix/store/reviewed-system"
    seen = []

    def inactive(unit):
        seen.append(unit)
        return "inactive"

    verify_live_system(
        active_system=system,
        persistent_system=system,
        expected_system=system,
        unit_state=inactive,
    )
    assert seen == list(HELD_UNITS)
    with pytest.raises(StepRightStopped, match="persistent system"):
        verify_live_system(
            active_system=system,
            persistent_system="/nix/store/other",
            expected_system=system,
            unit_state=inactive,
        )
    with pytest.raises(StepRightStopped, match="ordinary unit"):
        verify_live_system(
            active_system=system,
            persistent_system=system,
            expected_system=system,
            unit_state=lambda unit: "active" if unit == HELD_UNITS[-1] else "inactive",
        )


def test_preflight_report_explicitly_withholds_readiness_and_execution():
    report = preflight_report(gate_sha256="gate", runner_closure_sha256="closure")
    assert report == {
        "status": "preflight_checks_passed_not_operation_readiness",
        "requests": 0,
        "executed": False,
        "ready": False,
        "gate_sha256": "gate",
        "runner_closure_sha256": "closure",
    }


def test_sealed_cli_preflight_checks_full_boundary_without_network_or_vm(
    setup, tmp_path, monkeypatch, capsys
):
    state, output, source, sources, hosts, _, authorization, gate, _ = setup
    now = datetime.now(UTC)
    authorization["approved_at"] = (now - timedelta(seconds=1)).isoformat()
    authorization["execution_window"] = {
        "starts_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    cache_robots(setup, fetched_at=now)

    for name in RUNNER_HELPERS:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / name, destination)
    inventory = {
        item.relative_to(source).as_posix(): {"sha256": sha256(item.read_bytes())}
        for item in source.rglob("*")
        if item.is_file() and item.name != "extension-source.json"
    }
    source_receipt = canonical({"files": inventory})
    (source / "extension-source.json").write_bytes(source_receipt)

    closure = {
        "format": "stepright-exact-body-runner-closure-v1",
        "runner_sha256": sha256(
            (REPO / "journal/tools/admission/stepright_body_runner.py").read_bytes()
        ),
        "source_helpers": {
            name: sha256((source / name).read_bytes()) for name in sorted(RUNNER_HELPERS)
        },
    }
    closure_path = tmp_path / "runner-closure.json"
    closure_body = canonical(closure)
    closure_path.write_bytes(closure_body)
    gate["runner_closure_sha256"] = sha256(closure_body)
    gate["source_receipt_sha256"] = sha256(source_receipt)
    gate["authorization_sha256"] = sha256(canonical(authorization))
    with open_database(state) as database:
        gate["archive_prestate"] = archive_prestate(database.connection, state, observed_at=now)
        before = list(
            database.connection.execute(
                "SELECT day,requests,bytes FROM host_budget WHERE host=? ORDER BY day", (HOST,)
            )
        )

    authorization_path = tmp_path / "authorization.json"
    gate_path = tmp_path / "gate.json"
    authorization_path.write_bytes(canonical(authorization))
    gate_body = canonical(gate)
    gate_path.write_bytes(gate_body)
    monkeypatch.setattr(runner_module, "production_runtime_guard", lambda *_: None)
    monkeypatch.setattr(runner_module, "verify_loaded_swingset_modules", lambda *_: None)
    assert (
        runner_module.main(
            [
                "--gate",
                str(gate_path),
                "--expected-gate-sha256",
                sha256(gate_body),
                "--source",
                str(source),
                "--state",
                str(state),
                "--authorization",
                str(authorization_path),
                "--quarantine",
                str(output),
                "--locator-audit",
                str(
                    REPO
                    / "journal/evidence/admission/stepright-next-controls-2026-09-17/attempt-001/locator-audit.json"
                ),
                "--runner-closure",
                str(closure_path),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "preflight_checks_passed_not_operation_readiness"
    assert report["requests"] == 0
    assert report["executed"] is False
    assert report["ready"] is False
    assert not output.exists()
    with open_database(state) as database:
        after = list(
            database.connection.execute(
                "SELECT day,requests,bytes FROM host_budget WHERE host=? ORDER BY day", (HOST,)
            )
        )
    assert [tuple(row) for row in after] == [tuple(row) for row in before]


def _runner_closure() -> dict:
    return {
        "format": "stepright-exact-body-runner-closure-v1",
        "runner_sha256": sha256(
            (REPO / "journal/tools/admission/stepright_body_runner.py").read_bytes()
        ),
        "source_helpers": {
            name: sha256((REPO / name).read_bytes()) for name in sorted(RUNNER_HELPERS)
        },
    }


def test_runner_closure_binds_executing_runner_and_exact_helpers(tmp_path):
    closure_path = tmp_path / "runner-closure.json"
    closure_path.write_bytes(canonical(_runner_closure()))
    expected = sha256(closure_path.read_bytes())
    assert verify_runner_closure(closure_path, source=REPO, expected_sha256=expected) == expected
    with pytest.raises(StepRightStopped, match="closure digest"):
        verify_runner_closure(closure_path, source=REPO, expected_sha256="0" * 64)


@pytest.mark.parametrize("member", ["runner", "helper"])
def test_runner_closure_rejects_runner_or_helper_mismatch(tmp_path, member):
    closure = _runner_closure()
    if member == "runner":
        closure["runner_sha256"] = "0" * 64
    else:
        closure["source_helpers"][sorted(RUNNER_HELPERS)[0]] = "0" * 64
    closure_path = tmp_path / "runner-closure.json"
    closure_path.write_bytes(canonical(closure))
    with pytest.raises(StepRightStopped, match="runner differs|helper differs"):
        verify_runner_closure(
            closure_path,
            source=REPO,
            expected_sha256=sha256(closure_path.read_bytes()),
        )


def test_verify_source_checks_receipt_inventory_and_file_bytes(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    member = source / "member.txt"
    member.write_bytes(b"reviewed\n")
    receipt = {"files": {"member.txt": {"sha256": sha256(member.read_bytes())}}}
    receipt_body = canonical(receipt)
    (source / "extension-source.json").write_bytes(receipt_body)
    monkeypatch.setattr(runner_module, "verify_loaded_swingset_modules", lambda _: None)
    verify_source(source, sha256(receipt_body))
    member.write_bytes(b"changed\n")
    with pytest.raises(StepRightStopped, match="runtime source file differs"):
        verify_source(source, sha256(receipt_body))


def test_accounting_connection_authorizes_only_gate_accounting_writes(setup):
    cache_robots(setup)
    state, _, _, _, _, clock, *_ = setup
    with accounting_connection(state) as conn:
        conn.execute(
            "INSERT INTO host_budget(host,day,requests,bytes) VALUES (?,?,0,0)",
            (HOST, clock.now().date().isoformat()),
        )
        conn.execute("UPDATE hosts SET next_allowed_at=NULL WHERE host=?", (HOST,))
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE hosts SET robots_status=410 WHERE host=?", (HOST,))
        with pytest.raises(sqlite3.DatabaseError, match="exceeds accounting scope"):
            conn.execute("INSERT INTO hosts(host,robots_status) VALUES ('smuggled.example',404)")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM control_state")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DROP TRIGGER stepright_hosts_insert_guard")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("PRAGMA user_version=30")
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 29


def test_loaded_swingset_module_must_come_from_reviewed_source(tmp_path):
    verify_loaded_swingset_modules(
        REPO, {"swingset.example": SimpleNamespace(__file__=str(REPO / "src/swingset/x.py"))}
    )
    foreign = tmp_path / "foreign.py"
    foreign.write_text("")
    with pytest.raises(StepRightStopped, match="outside reviewed source"):
        verify_loaded_swingset_modules(
            REPO, {"swingset.example": SimpleNamespace(__file__=str(foreign))}
        )


def test_retained_locator_audit_is_exact_and_byte_bound(tmp_path):
    audit = (
        REPO
        / "journal/evidence/admission/stepright-next-controls-2026-09-17/attempt-001/locator-audit.json"
    )
    assert validate_locator_audit(audit)["candidate_event_page"]["archive_url"] == REPLAY_URL
    changed = tmp_path / "locator-audit.json"
    changed.write_bytes(audit.read_bytes() + b"\n")
    with pytest.raises(StepRightStopped, match="locator audit differs"):
        validate_locator_audit(changed)


def test_policy_parser_rejects_false_and_malformed_but_reports_missing(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text("[sources.other]\nenabled=true\n")
    assert check_ordinary_source_policy(path)[1] is True
    path.write_text(f"[sources.{SOURCE}]\nenabled=true\n")
    assert check_ordinary_source_policy(path)[1] is False
    path.write_text(f"[sources.{SOURCE}]\nenabled=false\n")
    with pytest.raises(StepRightStopped, match="explicitly disabled"):
        check_ordinary_source_policy(path)
    path.write_text(f"[sources.{SOURCE}]\nenabled='maybe'\n")
    with pytest.raises(StepRightStopped, match="malformed"):
        check_ordinary_source_policy(path)


def test_memento_parser_never_substitutes_url_timestamp():
    assert (
        capture_time({"Memento-Datetime": "Sat, 11 Jul 2015 03:58:13 GMT"}).strftime("%Y%m%d%H%M%S")
        == CAPTURE
    )
    with pytest.raises(StepRightStopped, match="lacks"):
        capture_time({})


class _SplitStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        yield from self.chunks


def _decode(chunks, *, capacity, encoding="identity"):
    response_obj = httpx.Response(
        200,
        headers={"content-encoding": encoding},
        stream=_SplitStream(chunks),
    )
    body = bytearray()
    complete = None
    for item in decoded_chunks(response_obj, capacity=capacity):
        if isinstance(item, bytes):
            body.extend(item)
        else:
            complete = item.complete
    return bytes(body), complete


@pytest.mark.parametrize(
    ("chunks", "expected_complete"),
    [([b"ab", b"cd"], True), ([b"ab", b"cd", b"e"], False)],
)
def test_identity_exact_cap_uses_one_byte_split_chunk_lookahead(chunks, expected_complete):
    body, complete = _decode(chunks, capacity=4)
    assert body == b"abcd"
    assert complete is expected_complete


@pytest.mark.parametrize("overflow", [False, True])
def test_gzip_exact_cap_uses_one_decoded_byte_across_split_chunks(overflow):
    plain = b"abcd" + (b"e" if overflow else b"")
    encoded = gzip.compress(plain)
    chunks = [encoded[:3], encoded[3:8], encoded[8:-2], encoded[-2:]]
    body, complete = _decode(chunks, capacity=4, encoding="gzip")
    assert body == b"abcd"
    assert complete is (not overflow)


def test_gzip_header_with_identity_bytes_fails_closed():
    with pytest.raises(zlib.error):
        _decode([b"<", b"ht", b"ml>"], capacity=6, encoding="gzip")


def test_truncated_gzip_framing_fails_closed():
    with pytest.raises(StepRightStopped, match="ended before"):
        _decode([b"\x1f", b"\x8b"], capacity=4, encoding="gzip")


def test_unsupported_content_encoding_fails_before_reading_stream():
    with pytest.raises(StepRightStopped, match="unexpected content encoding"):
        _decode([b"decoded"], capacity=8, encoding="br")


def test_fixture_files_keep_authorization_and_gate_hash_bindings(setup):
    state, output, _, _, _, _, authorization, gate, _ = setup
    cache_robots(setup)
    with accounting_connection(state) as conn:
        result = make(
            setup,
            conn,
            lambda _: response(**{"memento-datetime": "Sat, 11 Jul 2015 03:58:13 GMT"}),
        ).run()
    assert result["status"] == "captured_pending_independent_review"
    assert hashlib.sha256(
        (output / "authorization.json").read_bytes().rstrip()
    ).hexdigest() == sha256(canonical(authorization))
    assert json.loads((output / "execution-gate.json").read_bytes()) == gate
