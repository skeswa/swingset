"""Offline checks for the sealed shared Archive robots refresh."""

from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

import journal.tools.admission.refresh_archive_robots as refresh_module
from journal.tools.admission.refresh_archive_robots import (
    MAX_RESPONSE,
    MAX_TOTAL,
    ROBOTS_URL,
    SOURCE_HELPERS,
    ArchiveRobotsRefresh,
    accounting_connection,
    validate_execution_gate,
)
from journal.tools.admission.stepright_body_runner import (
    AUTHORITY,
    HELD_UNITS,
    HOST,
    KIND,
    PUBLISHED_BASELINE,
    SOURCE,
    StepRightStopped,
    archive_prestate,
    sha256,
)
from swingset.clock import FakeClock
from swingset.config import Config, HostConfig
from swingset.fetch.archive import Archive, canonical
from swingset.fetch.client import USER_AGENT
from swingset.state import db as db_module
from swingset.state.db import open_database

REPO = Path(__file__).resolve().parents[1]


def response(status: int, body: bytes = b"User-agent: *\nAllow: /\n", **headers: str):
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # The refresh tool is frozen at the schema it was reviewed against (D-0013).
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 29)
    clock = FakeClock()
    state, output = tmp_path / "state", tmp_path / "private-receipt"
    source = tmp_path / "source"
    config_dir = source / "config"
    config_dir.mkdir(parents=True)
    sources = config_dir / "sources.toml"
    hosts = config_dir / "hosts.toml"
    sources.write_text("[sources.other]\nenabled = true\n")
    hosts.write_text(
        '[hosts."web.archive.org"]\nmin_gap_seconds = 10\ndaily_request_budget = 200\n'
    )
    old_body = b""
    with open_database(state) as database:
        (state / "operator-hold").write_text("held\n")
        old_digest = Archive(state).store_body(old_body)
        database.connection.execute(
            "INSERT INTO hosts(host,robots_sha256,robots_status,robots_fetched_at) "
            "VALUES (?,?,404,?)",
            (HOST, old_digest, (clock.now() - timedelta(days=2)).isoformat()),
        )
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
        "format": "archive-robots-refresh-authorization-v1",
        "operation_id": "offline-archive-robots-refresh-001",
        "host": HOST,
        "url": ROBOTS_URL,
        "state": str(state.resolve()),
        "quarantine": str(output.resolve()),
        "owner_decision_reference": AUTHORITY,
        "maximum_http_requests": 1,
        "approved_at": clock.now().isoformat(),
        "execution_window": {
            "starts_at": clock.now().isoformat(),
            "expires_at": (clock.now() + timedelta(hours=1)).isoformat(),
        },
    }
    gate = {
        "format": "archive-robots-refresh-execution-gate-v1",
        "source": str(source.resolve()),
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
        "host": HOST,
        "url": ROBOTS_URL,
        "redirects": 0,
        "retries": 0,
        "maximum_http_requests": 1,
        "maximum_response_bytes": MAX_RESPONSE,
        "maximum_total_bytes": MAX_TOTAL,
        "maximum_elapsed_seconds": 15 * 60,
        "inactive_units": list(HELD_UNITS),
        "published_commit": PUBLISHED_BASELINE,
        "owner_decision_reference": AUTHORITY,
        "system": "/nix/store/reviewed-system",
        "published_candidate_id": candidate.name,
        "published_closure_digest": publication["closure_digest"],
        "publication_receipt_sha256": sha256(publication_body),
        "baseline_path": str(candidate.resolve()),
        "runner_closure_sha256": "reviewed-refresh-closure",
        "operator_hold_sha256": sha256((state / "operator-hold").read_bytes()),
    }
    with open_database(state) as database:
        gate["archive_prestate"] = archive_prestate(
            database.connection, state, observed_at=clock.now()
        )
    config = Config({HOST: HostConfig(min_gap_seconds=10, daily_request_budget=200)}, {})
    return (
        state,
        output,
        source,
        sources,
        hosts,
        clock,
        authorization,
        gate,
        config,
        old_digest,
    )


def make(setup, conn, handler, *, gate=None):
    state, output, _, sources, hosts, clock, authorization, reviewed_gate, config, _ = setup
    selected = gate or reviewed_gate
    return ArchiveRobotsRefresh(
        conn,
        config,
        clock,
        state=state,
        output=output,
        source_config=sources,
        host_config=hosts,
        authorization=authorization,
        execution_gate=selected,
        runner_closure_sha256=selected["runner_closure_sha256"],
        transport=httpx.MockTransport(handler),
        production_guard=lambda: None,
    )


@pytest.mark.parametrize("status", [200, 404, 410])
def test_one_direct_response_refreshes_shared_cache_and_accounting(setup, status):
    state, output, *_ = setup
    calls = []
    body = f"new robots {status}\n".encode()

    def handler(request):
        calls.append(request)
        assert str(request.url) == ROBOTS_URL
        assert request.headers["user-agent"] == USER_AGENT
        assert request.headers["accept-encoding"] == "gzip"
        assert "cookie" not in request.headers
        return response(status, body, **{"set-cookie": "discard=1"})

    with accounting_connection(state) as conn:
        result = make(setup, conn, handler).run()
        host = conn.execute(
            "SELECT robots_sha256,robots_status,robots_fetched_at FROM hosts WHERE host=?",
            (HOST,),
        ).fetchone()
        budget = conn.execute(
            "SELECT requests,bytes FROM host_budget WHERE host=?", (HOST,)
        ).fetchone()
        spacing = conn.execute(
            "SELECT gap_seconds,reserved_at,released_at FROM host_request_spacing WHERE host=?",
            (HOST,),
        ).fetchone()
    assert result["status"] == "refreshed_pending_independent_review"
    assert len(calls) == 1
    assert tuple(budget) == (1, len(body))
    assert tuple(spacing)[0] == 10
    assert spacing[1] is not None and spacing[2] is not None
    assert tuple(host) == (sha256(body), status, result["robots_update"]["fetched_at"])
    assert Archive(state).read_body(sha256(body)) == body
    request = result["requests"][0]
    assert request["socket_dispatch_attempted"] is True
    assert request["complete"] is True
    assert request["final_url"] == ROBOTS_URL
    assert "set-cookie" not in request["headers"]
    assert json.loads((output / "receipt.json").read_bytes()) == result


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_redirect_or_unapproved_status_is_terminal_without_cache_update(setup, status):
    state, _, *_, old_digest = setup
    calls = []
    with accounting_connection(state) as conn:
        result = make(
            setup,
            conn,
            lambda request: (
                calls.append(request)
                or response(status, b"not accepted", location="https://example.test/")
            ),
        ).run()
        host = conn.execute(
            "SELECT robots_sha256,robots_status FROM hosts WHERE host=?", (HOST,)
        ).fetchone()
    assert result["status"] == "stopped_incomplete"
    assert len(calls) == 1
    assert tuple(host) == (old_digest, 404)
    assert result["robots_update"] is None


def test_transport_failure_keeps_full_debit_and_ambiguous_evidence(setup):
    state = setup[0]
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx.ConnectError("offline", request=request)

    with accounting_connection(state) as conn:
        result = make(setup, conn, fail).run()
        budget = conn.execute(
            "SELECT requests,bytes FROM host_budget WHERE host=?", (HOST,)
        ).fetchone()
    assert result["status"] == "stopped_incomplete"
    assert len(calls) == 1
    assert tuple(budget) == (1, MAX_RESPONSE)
    assert result["requests"][0]["dispatch_state"] == "dispatch_ambiguous_or_issued"
    assert result["requests"][0]["classification"] == "ServerError"


def test_response_cap_never_updates_cache(setup):
    state, _, *_, old_digest = setup
    with accounting_connection(state) as conn:
        result = make(setup, conn, lambda _: response(200, b"x" * (MAX_RESPONSE + 1))).run()
        host = conn.execute(
            "SELECT robots_sha256,robots_status FROM hosts WHERE host=?", (HOST,)
        ).fetchone()
    assert result["status"] == "stopped_incomplete"
    assert result["requests"][0]["complete"] is False
    assert result["requests"][0]["body_bytes"] == MAX_RESPONSE
    assert tuple(host) == (old_digest, 404)


def test_explicitly_disabled_ordinary_source_stops_before_output(setup):
    state, output, _, sources, *_ = setup
    sources.write_text(f"[sources.{SOURCE}]\nenabled = false\n")
    with accounting_connection(state) as conn:
        with pytest.raises(StepRightStopped, match="explicitly disabled"):
            make(setup, conn, lambda _: pytest.fail("network request made"))
    assert not output.exists()


def test_future_dated_robots_cache_stops_before_output_debit_or_network(setup):
    state, output, _, _, _, clock, _, gate, *_ = setup
    with open_database(state) as database:
        database.connection.execute(
            "UPDATE hosts SET robots_fetched_at=? WHERE host=?",
            ((clock.now() + timedelta(hours=1)).isoformat(), HOST),
        )
        gate["archive_prestate"] = archive_prestate(
            database.connection, state, observed_at=clock.now()
        )
    calls = []
    with accounting_connection(state) as conn:
        with pytest.raises(StepRightStopped, match="stale non-future"):
            make(setup, conn, lambda request: calls.append(request) or response(404))
        assert conn.execute("SELECT count(*) FROM host_budget").fetchone()[0] == 0
    assert calls == []
    assert not output.exists()


def test_exact_24_hour_cache_age_is_eligible_for_refresh(setup):
    state, _, _, _, _, clock, _, gate, *_ = setup
    with open_database(state) as database:
        database.connection.execute(
            "UPDATE hosts SET robots_fetched_at=? WHERE host=?",
            ((clock.now() - timedelta(hours=24)).isoformat(), HOST),
        )
        gate["archive_prestate"] = archive_prestate(
            database.connection, state, observed_at=clock.now()
        )
    assert gate["archive_prestate"]["robots"]["age_seconds_at_observation"] == 24 * 60 * 60
    with accounting_connection(state) as conn:
        result = make(setup, conn, lambda _: response(404, b"refreshed\n")).run()
    assert result["status"] == "refreshed_pending_independent_review"
    assert len(result["requests"]) == 1


def test_exact_prestate_rejects_changed_budget_before_output(setup):
    state = setup[0]
    with open_database(state) as database:
        database.connection.execute(
            "INSERT INTO host_budget(host,day,requests,bytes) VALUES (?,?,1,7)",
            (HOST, setup[5].now().date().isoformat()),
        )
    with accounting_connection(state) as conn:
        with pytest.raises(StepRightStopped, match="prestate differs"):
            make(setup, conn, lambda _: pytest.fail("network request made"))
    assert not setup[1].exists()


def test_active_host_pause_and_legacy_spacing_fail_closed(setup):
    state, _, _, sources, hosts, clock, authorization, gate, *_ = setup
    with open_database(state) as database:
        database.connection.execute(
            "UPDATE hosts SET paused_until=?,pause_reason='Blocked' WHERE host=?",
            ((clock.now() + timedelta(hours=1)).isoformat(), HOST),
        )
        paused_gate = copy.deepcopy(gate)
        paused_gate["archive_prestate"] = archive_prestate(
            database.connection, state, observed_at=clock.now()
        )
    with accounting_connection(state) as conn:
        inputs = {
            "conn": conn,
            "source": setup[2],
            "state": state,
            "quarantine": setup[1],
            "now": clock.now(),
            "authorization_sha256": sha256(canonical(authorization)),
            "source_config_sha256": sha256(sources.read_bytes()),
            "host_config_sha256": sha256(hosts.read_bytes()),
            "control_revision": gate["control_revision"],
            "runner_closure_sha256": gate["runner_closure_sha256"],
        }
        with pytest.raises(StepRightStopped, match="host pause"):
            validate_execution_gate(paused_gate, **inputs)

    with open_database(state) as database:
        database.connection.execute(
            "UPDATE hosts SET paused_until=NULL,pause_reason=NULL WHERE host=?", (HOST,)
        )
        database.connection.execute(
            "INSERT INTO host_budget(host,day,requests,bytes) VALUES (?,?,1,0)",
            (HOST, "2025-12-31"),
        )
        database.connection.execute(
            "INSERT INTO host_request_spacing(host,reservation_id) VALUES (?,?)",
            (HOST, "legacy-web.archive.org"),
        )
        legacy_gate = copy.deepcopy(gate)
        legacy_gate["archive_prestate"] = archive_prestate(
            database.connection, state, observed_at=clock.now()
        )
    with accounting_connection(state) as conn:
        inputs["conn"] = conn
        with pytest.raises(StepRightStopped, match="unsafe or legacy"):
            validate_execution_gate(legacy_gate, **inputs)


def test_day_rollover_after_debit_stops_before_socket(setup):
    state, _, _, _, _, clock, authorization, gate, *_ = setup
    clock.current = clock.now().replace(hour=23, minute=59, second=59)
    authorization["approved_at"] = clock.now().isoformat()
    authorization["execution_window"] = {
        "starts_at": clock.now().isoformat(),
        "expires_at": (clock.now() + timedelta(minutes=15)).isoformat(),
    }
    gate["authorization_sha256"] = sha256(canonical(authorization))
    with open_database(state) as database:
        gate["archive_prestate"] = archive_prestate(
            database.connection, state, observed_at=clock.now()
        )
    calls = []
    with accounting_connection(state) as conn:
        runner = make(setup, conn, lambda request: calls.append(request) or response(404))
        acquire = runner.gate.acquire

        def delayed_acquire(*args, **kwargs):
            grant = acquire(*args, **kwargs)
            if getattr(grant, "debited_at", None) is not None:
                clock.sleep(2)
            return grant

        runner.gate.acquire = delayed_acquire
        result = runner.run()
        budget = conn.execute(
            "SELECT requests,bytes FROM host_budget WHERE host=?", (HOST,)
        ).fetchone()
    assert result["status"] == "stopped_incomplete"
    assert "UTC debit day rolled over" in result["stop_reason"]
    assert calls == []
    assert tuple(budget) == (1, MAX_RESPONSE)


def test_accounting_authorizer_is_narrow(setup):
    state = setup[0]
    with accounting_connection(state) as conn:
        conn.execute(
            "UPDATE hosts SET robots_status=410,robots_fetched_at=? WHERE host=?",
            (setup[5].now().isoformat(), HOST),
        )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM control_state")
        with pytest.raises(sqlite3.DatabaseError, match="exceeds scope"):
            conn.execute("INSERT INTO hosts(host,robots_status) VALUES ('smuggled.example',404)")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("PRAGMA user_version=30")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DROP TRIGGER archive_robots_hosts_insert_guard")


def _seal_source_and_closure(source: Path, tmp_path: Path) -> tuple[bytes, Path, bytes]:
    for name in SOURCE_HELPERS:
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
        "format": "archive-robots-refresh-runner-closure-v1",
        "runner_sha256": sha256(
            (REPO / "journal/tools/admission/refresh_archive_robots.py").read_bytes()
        ),
        "shared_runner_sha256": sha256(
            (REPO / "journal/tools/admission/stepright_body_runner.py").read_bytes()
        ),
        "source_helpers": {
            name: sha256((source / name).read_bytes()) for name in sorted(SOURCE_HELPERS)
        },
    }
    closure_body = canonical(closure)
    closure_path = tmp_path / "runner-closure.json"
    closure_path.write_bytes(closure_body)
    return source_receipt, closure_path, closure_body


def test_sealed_cli_preflight_has_no_requests_or_persistent_writes(
    setup, tmp_path, monkeypatch, capsys
):
    state, output, source, _, _, _, authorization, gate, *_ = setup
    now = datetime.now(UTC)
    authorization["approved_at"] = (now - timedelta(seconds=1)).isoformat()
    authorization["execution_window"] = {
        "starts_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    with open_database(state) as database:
        database.connection.execute(
            "UPDATE hosts SET robots_fetched_at=? WHERE host=?",
            ((now - timedelta(days=2)).isoformat(), HOST),
        )
        gate["archive_prestate"] = archive_prestate(database.connection, state, observed_at=now)
        before = {
            table: [tuple(row) for row in database.connection.execute(f'SELECT * FROM "{table}"')]
            for table in ("hosts", "host_budget", "host_request_spacing")
        }
    source_receipt, closure_path, closure_body = _seal_source_and_closure(source, tmp_path)
    gate["source_receipt_sha256"] = sha256(source_receipt)
    gate["runner_closure_sha256"] = sha256(closure_body)
    gate["authorization_sha256"] = sha256(canonical(authorization))
    authorization_path = tmp_path / "authorization.json"
    gate_path = tmp_path / "gate.json"
    authorization_path.write_bytes(canonical(authorization))
    gate_body = canonical(gate)
    gate_path.write_bytes(gate_body)
    monkeypatch.setattr(refresh_module, "production_runtime_guard", lambda *_: None)
    monkeypatch.setattr(refresh_module, "verify_loaded_swingset_modules", lambda *_: None)
    monkeypatch.setattr(
        refresh_module.shared_runner, "verify_loaded_swingset_modules", lambda *_: None
    )
    assert (
        refresh_module.main(
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
                "--runner-closure",
                str(closure_path),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["requests"] == report["writes"] == 0
    assert report["executed"] is report["ready"] is False
    assert not output.exists()
    with open_database(state) as database:
        after = {
            table: [tuple(row) for row in database.connection.execute(f'SELECT * FROM "{table}"')]
            for table in before
        }
    assert after == before
