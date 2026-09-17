from __future__ import annotations

import copy
import gzip
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from journal.tools.admission import dcn_origin_fixture_runner as runner_module
from journal.tools.admission.dcn_origin_fixture_runner import (
    ACKNOWLEDGED_CANDIDATE,
    DEPLOYED_SYSTEM,
    EVENT_ID,
    HELD_UNITS,
    HOST,
    PDF_URLS,
    REDIRECT_ROBOTS,
    OriginFixtureRunner,
    OriginStopped,
    _reserve_event_day,
    accounting_connection,
    check_ordinary_source_policy,
    db_module,
    decoded_chunks,
    origin_redirect,
    read_cached_robots,
    validate_execution_gate,
    verify_live_production_state,
    verify_packet,
)
from swingset.clock import FakeClock
from swingset.config import Config, HostConfig
from swingset.fetch.archive import Archive
from swingset.fetch.politeness import Gate, Grant, Wait
from swingset.state.controls import settle
from swingset.state.db import open_database


@pytest.fixture
def setup(tmp_path, monkeypatch):
    state, output, source_config = (
        tmp_path / "state",
        tmp_path / "quarantine",
        tmp_path / "sources.toml",
    )
    source_config.write_text("[sources.other]\nenabled = true\n")
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 28)
    with open_database(state):
        pass
    (state / "operator-hold").write_text("held\n")
    clock = FakeClock()
    authorization = {
        "operation_id": "origin-test-001",
        "event_id": EVENT_ID,
        "pdf_urls": list(PDF_URLS),
        "state": str(state.resolve()),
        "quarantine": str(output.resolve()),
        "owner_decision_reference": "journal/decisions/0087-authorize-remaining-v2-acquisition-and-operations.md",
        "approved_at": clock.now().isoformat(),
        "execution_window": {
            "starts_at": clock.now().isoformat(),
            "expires_at": (clock.now() + timedelta(hours=1)).isoformat(),
        },
    }
    policy = HostConfig(min_gap_seconds=10, daily_request_budget=200, daily_byte_budget=300_000_000)
    config = Config({HOST: policy}, {})
    return state, output, source_config, clock, authorization, config


def response(status=200, body=b"", *, headers=None, **extra_headers):
    return httpx.Response(
        status,
        headers={**(headers or {}), **extra_headers},
        stream=httpx.ByteStream(body),
    )


def test_missing_source_is_distinct_from_explicit_disabled(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text("[sources.other]\nenabled = true\n")
    _, missing = check_ordinary_source_policy(path)
    assert missing
    path.write_text("[sources.dcn]\nenabled = false\n")
    with pytest.raises(OriginStopped, match="explicitly disabled"):
        check_ordinary_source_policy(path)


def test_exact_redirect_scope_allows_only_one_robots_hop():
    assert origin_redirect(
        "https://danceconvention.net/robots.txt",
        REDIRECT_ROBOTS,
        purpose="robots",
        redirects=0,
    )
    assert not origin_redirect(
        "https://danceconvention.net/robots.txt",
        REDIRECT_ROBOTS,
        purpose="robots",
        redirects=1,
    )
    assert not origin_redirect(
        PDF_URLS[0], "https://danceconvention.net/elsewhere", purpose="pdf", redirects=0
    )
    assert not origin_redirect(
        "https://danceconvention.net/robots.txt",
        "https://danceconvention.net/eventdirector/robots.txt?x=1",
        purpose="robots",
        redirects=0,
    )


def test_packet_closure_requires_exact_reviewed_files(tmp_path):
    packet = tmp_path / "packet"
    packet.mkdir()
    runner_body = Path(runner_module.__file__).read_bytes()
    files = {"dcn_origin_fixture_runner.py": runner_body, "manifest.json": b"{}"}
    closure = json.dumps(
        {
            "format": "dcn-origin-fixture-closure-v1",
            "files": {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    for name, body in files.items():
        (packet / name).write_bytes(body)
    (packet / "closure.json").write_bytes(closure)
    closure_hash = hashlib.sha256(closure).hexdigest()
    (packet / "build-receipt.json").write_text(
        json.dumps(
            {
                "format": "dcn-origin-fixture-build-v1",
                "closure_sha256": closure_hash,
                "files": {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
                "runner_sha256": hashlib.sha256(runner_body).hexdigest(),
            }
        )
    )
    assert verify_packet(packet, closure_hash) == {}
    (packet / "extra.json").write_text("{}")
    with pytest.raises(OriginStopped, match="undeclared"):
        verify_packet(packet, closure_hash)


def test_execution_gate_binds_all_live_source_and_operation_inputs(tmp_path):
    source = Path("/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source")
    state, quarantine = tmp_path / "state", tmp_path / "quarantine"
    manifest = {
        "reference_runtime": {
            "source": str(source),
            "source_receipt_sha256": "source-receipt",
            "schema": 28,
            "published_commit": "2a6c7dc744fb36eabb5163c0a527d787d3721f4f",
            "acknowledged_candidate": ACKNOWLEDGED_CANDIDATE,
        },
        "ordinary_config_sha256": "ordinary-config",
    }
    gate = {
        "format": "dcn-origin-fixture-execution-gate-v1",
        "source": str(source),
        "source_receipt_sha256": "source-receipt",
        "schema_version": 28,
        "ordinary_config_sha256": "ordinary-config",
        "state": str(state.resolve()),
        "quarantine": str(quarantine.resolve()),
        "authorization_sha256": "authorization",
        "source_event_id": EVENT_ID,
        "pdf_urls": list(PDF_URLS),
        "production_facts_or_watches_created": 0,
        "control_revision": 7,
        "system": DEPLOYED_SYSTEM,
        "inactive_units": list(HELD_UNITS),
        "published_commit": "2a6c7dc744fb36eabb5163c0a527d787d3721f4f",
        "acknowledged_candidate": ACKNOWLEDGED_CANDIDATE,
    }
    inputs = {
        "source": source,
        "state": state,
        "quarantine": quarantine,
        "ordinary_config_sha256": "ordinary-config",
        "authorization_sha256": "authorization",
        "control_revision": 7,
    }
    validate_execution_gate(gate, manifest, **inputs)
    for key, value in (
        ("schema_version", 29),
        ("ordinary_config_sha256", "changed"),
        ("control_revision", 8),
        ("authorization_sha256", "changed"),
        ("quarantine", str((tmp_path / "other").resolve())),
        ("inactive_units", []),
        ("published_commit", "other"),
    ):
        altered = copy.deepcopy(gate)
        altered[key] = value
        with pytest.raises(OriginStopped, match="execution gate"):
            validate_execution_gate(altered, manifest, **inputs)


def test_live_system_and_six_unit_guard_fails_closed():
    seen = []

    def inactive(unit):
        seen.append(unit)
        return "inactive"

    verify_live_production_state(
        active_system=DEPLOYED_SYSTEM,
        persistent_system=DEPLOYED_SYSTEM,
        unit_state=inactive,
    )
    assert seen == list(HELD_UNITS)
    with pytest.raises(OriginStopped, match="persistent system"):
        verify_live_production_state(
            active_system=DEPLOYED_SYSTEM,
            persistent_system="/nix/store/other-system",
            unit_state=inactive,
        )
    with pytest.raises(OriginStopped, match="swingset-cycle.timer"):
        verify_live_production_state(
            active_system=DEPLOYED_SYSTEM,
            persistent_system=DEPLOYED_SYSTEM,
            unit_state=lambda unit: "active" if unit == "swingset-cycle.timer" else "inactive",
        )


def test_cli_preflight_binds_reviewed_packet_and_state_without_execution(
    setup, tmp_path, monkeypatch, capsys
):
    state, _, _, _, _, _ = setup
    source = tmp_path / "source"
    config_dir = source / "config"
    config_dir.mkdir(parents=True)
    config_body = b"[sources.other]\nenabled = true\n"
    (config_dir / "sources.toml").write_bytes(config_body)
    receipt_hash = "receipt-hash"
    manifest = {
        "reference_runtime": {
            "source": str(source.resolve()),
            "source_receipt_sha256": receipt_hash,
            "schema": 28,
            "published_commit": "2a6c7dc744fb36eabb5163c0a527d787d3721f4f",
            "acknowledged_candidate": ACKNOWLEDGED_CANDIDATE,
        },
        "ordinary_config_sha256": hashlib.sha256(config_body).hexdigest(),
    }
    packet = tmp_path / "packet"
    packet.mkdir()
    file_set = {
        "dcn_origin_fixture_runner.py": Path(runner_module.__file__).read_bytes(),
        "manifest.json": json.dumps(manifest, sort_keys=True).encode(),
    }
    closure_body = json.dumps(
        {
            "format": "dcn-origin-fixture-closure-v1",
            "files": {name: hashlib.sha256(body).hexdigest() for name, body in file_set.items()},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    closure_hash = hashlib.sha256(closure_body).hexdigest()
    for name, body in file_set.items():
        (packet / name).write_bytes(body)
    (packet / "closure.json").write_bytes(closure_body)
    (packet / "build-receipt.json").write_text(
        json.dumps(
            {
                "format": "dcn-origin-fixture-build-v1",
                "closure_sha256": closure_hash,
                "files": {
                    name: hashlib.sha256(body).hexdigest() for name, body in file_set.items()
                },
                "runner_sha256": hashlib.sha256(
                    file_set["dcn_origin_fixture_runner.py"]
                ).hexdigest(),
            }
        )
    )
    auth = tmp_path / "authorization.json"
    auth_body = b"{}"
    auth.write_bytes(auth_body)
    quarantine = tmp_path / "quarantine"
    with accounting_connection(state) as conn:
        revision = int(
            conn.execute("SELECT revision FROM control_state WHERE singleton=1").fetchone()[0]
        )
    gate = {
        "format": "dcn-origin-fixture-execution-gate-v1",
        "packet_closure_sha256": closure_hash,
        "source": str(source.resolve()),
        "source_receipt_sha256": receipt_hash,
        "schema_version": 28,
        "ordinary_config_sha256": hashlib.sha256(config_body).hexdigest(),
        "state": str(state.resolve()),
        "quarantine": str(quarantine.resolve()),
        "authorization_sha256": hashlib.sha256(auth_body).hexdigest(),
        "source_event_id": EVENT_ID,
        "pdf_urls": list(PDF_URLS),
        "production_facts_or_watches_created": 0,
        "control_revision": revision,
        "system": DEPLOYED_SYSTEM,
        "inactive_units": list(HELD_UNITS),
        "published_commit": "2a6c7dc744fb36eabb5163c0a527d787d3721f4f",
        "acknowledged_candidate": ACKNOWLEDGED_CANDIDATE,
    }
    gate_path = tmp_path / "gate.json"
    gate_path.write_text(json.dumps(gate))
    monkeypatch.setattr(runner_module, "production_runtime_guard", lambda _: None)
    monkeypatch.setattr(runner_module, "_verify_runtime_source", lambda path, _: path.resolve())
    assert (
        runner_module.main(
            [
                "--packet",
                str(packet),
                "--gate",
                str(gate_path),
                "--source",
                str(source),
                "--state",
                str(state),
                "--authorization",
                str(auth),
                "--quarantine",
                str(quarantine),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "preflight_passed_no_execution"
    assert not quarantine.exists()


def test_exact_two_gzip_pdf_targets_are_captured_with_no_cookie_reuse(setup):
    state, output, _, clock, _, _ = setup
    decoded = b"%PDF-1.7\nscore fixture\n"
    encoded = gzip.compress(decoded)
    observed = []

    def reply(request):
        observed.append(request)
        assert request.headers["accept-encoding"] == "gzip"
        assert "cookie" not in request.headers
        if request.url.path == "/robots.txt":
            assert request.headers["user-agent"].startswith("swingset/")
            return response(301, headers={"location": REDIRECT_ROBOTS})
        if request.url.path == "/eventdirector/robots.txt":
            return response(200, b"User-agent: swingset\nAllow: /\n")
        return response(
            200,
            encoded,
            **{
                "content-encoding": "gzip",
                "content-type": "application/pdf",
                "set-cookie": "secret=1",
            },
        )

    with accounting_connection(state) as conn:
        runner = OriginFixtureRunner(
            conn,
            setup[-1],
            clock,
            state=state,
            output=output,
            source_config=setup[2],
            source_config_sha256=hashlib.sha256(setup[2].read_bytes()).hexdigest(),
            authorization=setup[4],
            transport=httpx.MockTransport(reply),
            monotonic=clock.monotonic,
        )
        result = runner.run()
        assert conn.execute("SELECT SUM(requests) FROM host_budget").fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM watches").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM pending_work").fetchone()[0] == 0
    assert result["status"] == "captured_pending_independent_review"
    assert len(observed) == 4
    assert [item["body_bytes"] for item in result["targets"]] == [len(decoded), len(decoded)]
    assert all(
        item["captured_at"] == item["fetched_at"] == item["observed_at"]
        for item in result["targets"]
    )
    assert all(
        "set-cookie" not in {key.lower() for key in item["headers"]} for item in result["targets"]
    )
    assert (state / "dcn-origin-event-days.json").is_file()
    assert (output / "bodies" / result["targets"][0]["body_sha256"]).read_bytes() == decoded


def test_gzip_expansion_stops_at_capacity_without_eof_probe():
    compressed = gzip.compress(b"x" * (2 * 1024 * 1024))

    class OneRawChunk(httpx.SyncByteStream):
        def __iter__(self):
            yield compressed
            pytest.fail("decoder must stop without probing beyond decoded capacity")

    response_obj = httpx.Response(
        200,
        headers={"content-encoding": "gzip"},
        stream=OneRawChunk(),
    )
    chunks, _ = decoded_chunks(response_obj, capacity=64 * 1024)
    body = bytearray()
    complete = None
    for item in chunks:
        if isinstance(item, bytes):
            body.extend(item)
        else:
            complete = item.complete
    assert len(body) == 64 * 1024
    assert complete is False


def test_malformed_gzip_stops_with_settled_request_and_full_reservation(setup):
    state, output, source_config, clock, authorization, config = setup

    def reply(request):
        if request.url.path == "/robots.txt":
            return response(404)
        return response(200, b"not-gzip", **{"content-encoding": "gzip"})

    with accounting_connection(state) as conn:
        result = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            transport=httpx.MockTransport(reply),
            monotonic=clock.monotonic,
        ).run()
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM execution_admissions WHERE state!='settled'"
            ).fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT SUM(bytes) FROM host_budget").fetchone()[0] == 2 * 1024 * 1024
    assert result["status"] == "stopped_incomplete"
    assert not result["targets"]


@pytest.mark.parametrize(
    ("status", "headers", "body", "expected"),
    [
        (429, {}, b"slow down", "Throttled"),
        (429, {"retry-after": "30"}, b"slow down", "Throttled"),
        (429, {"retry-after": "120"}, b"slow down", "Throttled"),
        (503, {}, b"busy", "Throttled"),
        (503, {"retry-after": "180"}, b"busy", "Throttled"),
        (200, {}, b"Just a moment cf-chl challenge-platform", "Blocked"),
        (
            302,
            {"location": "https://danceconvention.net/elsewhere"},
            b"challenge-platform",
            "Blocked",
        ),
        (403, {}, b"challenge-platform", "Blocked"),
        (500, {}, b"challenge-platform", "Blocked"),
    ],
)
def test_status_and_challenge_responses_are_classified_and_retained(
    setup, status, headers, body, expected
):
    state, output, source_config, clock, authorization, config = setup

    def reply(request):
        if request.url.path == "/robots.txt":
            return response(404)
        return response(status, body, headers=headers)

    with accounting_connection(state) as conn:
        result = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            transport=httpx.MockTransport(reply),
            monotonic=clock.monotonic,
        ).run()
        pdf = result["requests"][-1]
        assert pdf["http_status"] == status
        assert pdf["classified_outcome"] == expected
        assert pdf["body_sha256"] == hashlib.sha256(body).hexdigest()
        if status in (429, 503):
            assert pdf["host_gate_after_response"]["paused_until"] is not None
            expected_retry = (
                max(60.0, float(headers["retry-after"])) if headers.get("retry-after") else None
            )
            assert pdf["retry_after_seconds"] == expected_retry
            actual_pause = (
                datetime.fromisoformat(pdf["host_gate_after_response"]["paused_until"])
                - datetime.fromisoformat(pdf["completed_at"])
            ).total_seconds()
            assert actual_pause == (expected_retry or 900)
        if b"challenge" in body:
            assert pdf["host_gate_after_response"]["pause_reason"]
            actual_pause = (
                datetime.fromisoformat(pdf["host_gate_after_response"]["paused_until"])
                - datetime.fromisoformat(pdf["completed_at"])
            ).total_seconds()
            assert actual_pause == 86400
        if status in (429, 503, 302, 403, 500):
            assert conn.execute(
                "SELECT SUM(bytes) FROM host_budget WHERE host=?", (HOST,)
            ).fetchone()[0] == len(body)


def test_robots_403_is_retained_and_host_pause_is_not_cleared(setup):
    state, output, source_config, clock, authorization, config = setup

    def reply(request):
        return response(403, b"forbidden")

    with accounting_connection(state) as conn:
        result = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            transport=httpx.MockTransport(reply),
            monotonic=clock.monotonic,
        ).run()
        assert result["status"] == "stopped_incomplete"
        robots = result["requests"][0]
        assert robots["classified_outcome"] == "Blocked"
        assert robots["host_gate_after_response"]["paused_until"] is not None
        assert conn.execute("SELECT paused_until FROM hosts WHERE host=?", (HOST,)).fetchone()[0]


def test_non_pdf_origin_body_remains_a_finding_and_never_claims_capture(setup):
    state, output, source_config, clock, authorization, config = setup

    def reply(request):
        if request.url.path == "/robots.txt":
            return response(404)
        return response(200, b"<html>login page</html>", **{"content-type": "text/html"})

    with accounting_connection(state) as conn:
        result = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            transport=httpx.MockTransport(reply),
            monotonic=clock.monotonic,
        ).run()
    assert result["status"] == "stopped_incomplete"
    assert len(result["targets"]) == 1
    assert result["targets"][0]["content_finding"] == "unexpected_body"


def test_truncated_gzip_trailer_is_not_a_complete_response():
    body = gzip.compress(b"%PDF-1.7\nvalid prefix")[:-8]
    response_obj = httpx.Response(
        200,
        headers={"content-encoding": "gzip"},
        stream=httpx.ByteStream(body),
    )
    chunks, _ = decoded_chunks(response_obj, capacity=1024)
    with pytest.raises(OriginStopped, match="trailer"):
        list(chunks)


def test_pdf_redirect_is_refused_without_following_target(setup):
    state, output, source_config, clock, authorization, config = setup
    observed = []

    def reply(request):
        observed.append(str(request.url))
        if request.url.path == "/robots.txt":
            return response(404)
        return response(302, headers={"location": "https://danceconvention.net/elsewhere"})

    with accounting_connection(state) as conn:
        result = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            transport=httpx.MockTransport(reply),
            monotonic=clock.monotonic,
        ).run()
    assert result["status"] == "stopped_incomplete"
    assert observed == ["https://danceconvention.net/robots.txt", PDF_URLS[0]]
    assert len(result["requests"]) == 2
    assert result["requests"][-1]["http_status"] == 302


def test_network_failure_is_classified_and_keeps_full_byte_reservation(setup):
    state, output, source_config, clock, authorization, config = setup

    def reply(request):
        if request.url.path == "/robots.txt":
            return response(404)
        raise httpx.ConnectError("offline", request=request)

    with accounting_connection(state) as conn:
        result = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            transport=httpx.MockTransport(reply),
            monotonic=clock.monotonic,
        ).run()
        request = result["requests"][-1]
        assert request["socket_dispatch_attempted"] is True
        assert request["classified_outcome"] == "ServerError"
        assert request["host_gate_after_response"]["paused_until"] is not None
        assert (
            conn.execute("SELECT SUM(bytes) FROM host_budget WHERE host=?", (HOST,)).fetchone()[0]
            == 2 * 1024 * 1024
        )
    assert result["status"] == "stopped_incomplete"


def test_fresh_unbound_robots_cache_fails_closed_without_refresh(setup):
    state, output, _, clock, _, _ = setup
    with accounting_connection(state) as conn:
        body = b"User-agent: swingset\nDisallow: /\n"
        digest = Archive(state).store_body(body)
        conn.execute("INSERT INTO hosts(host) VALUES (?)", (HOST,))
        conn.execute(
            "UPDATE hosts SET robots_sha256=?,robots_status=200,robots_fetched_at=? WHERE host=?",
            (digest, clock.now().isoformat(), HOST),
        )
        output.mkdir()
        with pytest.raises(OriginStopped, match="provenance"):
            read_cached_robots(conn, Archive(state), clock, state, output)


def test_exact_fresh_robots_provenance_can_be_reused(setup):
    state, output, _, clock, _, _ = setup
    with accounting_connection(state) as conn:
        body = b"User-agent: swingset\nDisallow: /eventdirector/\n"
        digest = Archive(state).store_body(body)
        conn.execute("INSERT INTO hosts(host) VALUES (?)", (HOST,))
        conn.execute(
            "UPDATE hosts SET robots_sha256=?,robots_status=200,robots_fetched_at=? WHERE host=?",
            (digest, clock.now().isoformat(), HOST),
        )
        (state / "dcn-origin-robots-cache.json").write_text(
            json.dumps(
                {
                    "format": "dcn-origin-robots-cache-v1",
                    "host": HOST,
                    "requested_url": "https://danceconvention.net/robots.txt",
                    "final_url": REDIRECT_ROBOTS,
                    "status": 200,
                    "body_sha256": digest,
                    "fetched_at": clock.now().isoformat(),
                }
            )
        )
        output.mkdir()
        status, loaded, proof = read_cached_robots(conn, Archive(state), clock, state, output)
    assert status == 200 and loaded == body
    assert proof["final_url"] == REDIRECT_ROBOTS
    assert (output / "bodies" / digest).read_bytes() == body


def test_cached_crawl_delay_waits_without_same_run_robots_request(setup):
    state, output, source_config, clock, authorization, config = setup
    body = b"User-agent: swingset\nCrawl-delay: 12\nAllow: /\n"
    digest = Archive(state).store_body(body)
    fetched_at = clock.now().isoformat()
    with accounting_connection(state) as conn:
        conn.execute("INSERT INTO hosts(host) VALUES (?)", (HOST,))
        conn.execute(
            "UPDATE hosts SET robots_sha256=?,robots_status=200,robots_fetched_at=? WHERE host=?",
            (digest, fetched_at, HOST),
        )
        (state / "dcn-origin-robots-cache.json").write_text(
            json.dumps(
                {
                    "format": "dcn-origin-robots-cache-v1",
                    "host": HOST,
                    "requested_url": "https://danceconvention.net/robots.txt",
                    "final_url": REDIRECT_ROBOTS,
                    "status": 200,
                    "body_sha256": digest,
                    "fetched_at": fetched_at,
                }
            )
        )
        runner = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            transport=httpx.MockTransport(
                lambda req: pytest.fail("cache reuse must not request robots")
            ),
            monotonic=clock.monotonic,
        )
        output.mkdir()
        runner._check_robots(PDF_URLS[0], "pdf", httpx.Client(transport=runner.transport))
        assert not runner.receipt["requests"]
        assert runner.crawl_delay == 12
        assert clock.monotonic() >= 12


def test_pre_dispatch_receipt_failure_settles_paid_admission_as_not_issued(setup, monkeypatch):
    state, output, source_config, clock, authorization, config = setup
    with accounting_connection(state) as conn:
        runner = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            transport=httpx.MockTransport(
                lambda req: pytest.fail("receipt failure precedes dispatch")
            ),
            monotonic=clock.monotonic,
        )
        monkeypatch.setattr(runner, "_save", lambda: (_ for _ in ()).throw(OSError("disk full")))
        with httpx.Client(transport=runner.transport) as client:
            with pytest.raises(OriginStopped, match="disk full"):
                runner._exchange(client, "https://danceconvention.net/robots.txt", "robots")
        row = conn.execute(
            "SELECT state,outcome FROM execution_admissions WHERE action_id=?",
            ("dcn_origin_origin-test-001_1",),
        ).fetchone()
        assert tuple(row) == ("settled", "not_issued")
        assert runner.receipt["requests"][0]["paid_admission"] is True
        assert runner.receipt["requests"][0]["socket_dispatch_attempted"] is False


def test_disallowed_robots_prevents_pdf_request(setup):
    state, output, source_config, clock, authorization, config = setup
    robots = b"User-agent: swingset\nDisallow: /eventdirector/en/roundscores/\n"

    def reply(request):
        if request.url.path == "/robots.txt":
            return response(200, robots)
        pytest.fail("disallowed PDF request must not reach transport")

    with accounting_connection(state) as conn:
        result = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            transport=httpx.MockTransport(reply),
            monotonic=clock.monotonic,
        ).run()
    assert result["status"] == "stopped_incomplete"
    assert len(result["requests"]) == 1


def test_explicit_disabled_kill_switch_is_checked_before_request(setup):
    state, output, source_config, clock, authorization, config = setup
    source_config.write_text("[sources.dcn]\nenabled = false\n")
    source_sha = hashlib.sha256(source_config.read_bytes()).hexdigest()
    with accounting_connection(state) as conn:
        with pytest.raises(OriginStopped, match="explicitly disabled"):
            OriginFixtureRunner(
                conn,
                config,
                clock,
                state=state,
                output=output,
                source_config=source_config,
                source_config_sha256=source_sha,
                authorization=authorization,
                transport=httpx.MockTransport(lambda req: pytest.fail("no request")),
                monotonic=clock.monotonic,
            )


@pytest.mark.parametrize(
    ("scope_kind", "scope_id"),
    [("source", "dcn"), ("kind", "round_observations"), ("host", HOST), ("all", "all")],
)
def test_h13_source_kind_host_and_global_pauses_hold_origin_capture(setup, scope_kind, scope_id):
    state, output, source_config, clock, authorization, config = setup
    with accounting_connection(state) as conn:
        conn.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES (?,?,?)",
            (scope_kind, scope_id, "offline interlock test"),
        )
        runner = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            transport=httpx.MockTransport(
                lambda req: pytest.fail("H13 pause must stop before request")
            ),
            monotonic=clock.monotonic,
        )
        with pytest.raises(OriginStopped, match="operator control pauses"):
            runner._interlocks()


def test_event_day_claim_is_durable_and_single_operation(setup):
    state = setup[0]
    _reserve_event_day(
        state,
        day="2026-09-17",
        run_id="first",
        sequence=1,
        url="https://danceconvention.net/robots.txt",
        purpose="robots",
    )
    _reserve_event_day(
        state,
        day="2026-09-17",
        run_id="first",
        sequence=2,
        url=PDF_URLS[0],
        purpose="pdf",
    )
    ledger = json.loads((state / "dcn-origin-event-days.json").read_bytes())
    event_day = ledger["days"]["2026-09-17"]
    assert event_day["event_id"] == EVENT_ID and event_day["run_id"] == "first"
    assert event_day["requests"] == [
        {"sequence": 1, "url": "https://danceconvention.net/robots.txt", "purpose": "robots"},
        {"sequence": 2, "url": PDF_URLS[0], "purpose": "pdf"},
    ]
    with pytest.raises(OriginStopped, match="one-event-per-UTC-day"):
        _reserve_event_day(
            state,
            day="2026-09-17",
            run_id="second",
            sequence=1,
            url="https://danceconvention.net/robots.txt",
            purpose="robots",
        )
    with pytest.raises(OriginStopped, match="sequence"):
        _reserve_event_day(
            state,
            day="2026-09-17",
            run_id="first",
            sequence=1,
            url="https://danceconvention.net/robots.txt",
            purpose="robots",
        )


def test_event_day_uses_actual_grant_date_after_wait_crosses_midnight(setup, monkeypatch):
    state, output, source_config, clock, authorization, config = setup
    clock.current = datetime(2026, 9, 17, 23, 59, 50, tzinfo=UTC)
    authorization["approved_at"] = clock.now().isoformat()
    authorization["execution_window"] = {
        "starts_at": clock.now().isoformat(),
        "expires_at": (clock.now() + timedelta(hours=1)).isoformat(),
    }
    responses = iter((Wait(20), None))

    def acquire(self, host, *, source="", crawl_delay=0, sweep=False):
        next_response = next(responses)
        if next_response is not None:
            return next_response
        return Grant(host, clock.now())

    monkeypatch.setattr(Gate, "acquire", acquire)
    with accounting_connection(state) as conn:
        runner = OriginFixtureRunner(
            conn,
            config,
            clock,
            state=state,
            output=output,
            source_config=source_config,
            source_config_sha256=hashlib.sha256(source_config.read_bytes()).hexdigest(),
            authorization=authorization,
            monotonic=clock.monotonic,
        )
        grant, action_id = runner._admit("https://danceconvention.net/robots.txt", "robots")
        assert grant.debited_at.date().isoformat() == "2026-09-18"
        ledger = json.loads((state / "dcn-origin-event-days.json").read_bytes())
        assert list(ledger["days"]) == ["2026-09-18"]
        conn.execute("BEGIN IMMEDIATE")
        settle(conn, action_id, now=clock.now(), outcome="not_issued")
        conn.commit()

    with pytest.raises(OriginStopped, match="sequence"):
        _reserve_event_day(
            state,
            day="2026-09-18",
            run_id=authorization["operation_id"],
            sequence=1,
            url="https://danceconvention.net/robots.txt",
            purpose="robots",
        )


def test_event_day_claim_is_included_in_checkpoint_file_closure(setup, tmp_path):
    from swingset.backup.checkpoint import create_checkpoint, verify_checkpoint

    state = setup[0]
    _reserve_event_day(
        state,
        day="2026-09-17",
        run_id="first",
        sequence=1,
        url="https://danceconvention.net/robots.txt",
        purpose="robots",
    )
    cache_proof = state / "dcn-origin-robots-cache.json"
    cache_proof.write_text('{"format":"dcn-origin-robots-cache-v1"}\n')
    conn = sqlite3.connect(state / "state.sqlite", isolation_level=None)
    try:
        checkpoint = create_checkpoint(
            state,
            conn,
            tmp_path / "checkpoint",
            schema_version=28,
            versions={},
            input_bundle_hash=None,
        )
    finally:
        conn.close()
    assert (checkpoint.path / "dcn-origin-event-days.json").is_file()
    verified = verify_checkpoint(checkpoint.path, maximum_schema_version=28)
    assert (
        verified["files"]["dcn-origin-event-days.json"]["sha256"]
        == hashlib.sha256((state / "dcn-origin-event-days.json").read_bytes()).hexdigest()
    )
    assert (
        verified["files"]["dcn-origin-robots-cache.json"]["sha256"]
        == hashlib.sha256(cache_proof.read_bytes()).hexdigest()
    )


def test_wrong_schema_and_changed_configuration_stop_before_request(setup):
    state, output, source_config, clock, authorization, config = setup
    original_sha = hashlib.sha256(source_config.read_bytes()).hexdigest()
    conn = sqlite3.connect(state / "state.sqlite", isolation_level=None)
    conn.execute("UPDATE meta SET value='27' WHERE key='schema_version'")
    conn.execute("PRAGMA user_version=27")
    conn.close()
    with accounting_connection(state) as conn:
        with pytest.raises(OriginStopped, match="schema 28"):
            OriginFixtureRunner(
                conn,
                config,
                clock,
                state=state,
                output=output,
                source_config=source_config,
                source_config_sha256=original_sha,
                authorization=authorization,
                transport=httpx.MockTransport(lambda req: pytest.fail("no request")),
                monotonic=clock.monotonic,
            )
