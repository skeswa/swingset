"""Explicit packet checks; fake HTTP only against receipt-verified runtime003.

DCN_BODY_PACKET selects a fresh built packet. PYTHONPATH must place its helper
closure and frozen runtime003 src ahead of the current working tree.
"""

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fixture_helpers.fixture_exception import APPROVAL, MANIFEST_SHA, FixtureStopped, read_manifest

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig
from swingset.fetch.archive import Archive
from swingset.state.db import SCHEMA_VERSION, open_database

assert SCHEMA_VERSION == 28
PACKET = Path(os.environ["DCN_BODY_PACKET"]).resolve()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


driver = module("dcn_body_driver", PACKET / "dcn-results-body-h13-001.py")
preparation = module("dcn_body_preparer", PACKET / "prepare_fixture_exception.py")
HOST = "web.archive.org"
MANIFEST = read_manifest(PACKET / "helper-closure/proposal.json", PACKET)
TARGET = MANIFEST["targets"][0]


@pytest.fixture
def setup(tmp_path):
    state, output = tmp_path / "state", tmp_path / "quarantine"
    clock = FakeClock()
    with open_database(state):
        pass
    (state / "operator-hold").write_text("offline hold remains")
    authorization = dict(
        approval=APPROVAL,
        manifest_canonical_sha256=MANIFEST_SHA,
        state=str(state),
        quarantine=str(output),
        published_stages=["V2", "V4"],
        approved_by="offline test only",
        owner_decision_reference="offline",
        publication_receipt_reference="offline",
        approved_at=clock.now().isoformat(),
        execution_window=dict(
            starts_at=clock.now().isoformat(),
            expires_at=(clock.now() + timedelta(minutes=15)).isoformat(),
        ),
    )
    return state, output, clock, authorization


def response(status=200, body=b"", **headers):
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))


def handler(request):
    if request.url.path == "/robots.txt":
        return response(404)
    assert str(request.url) == TARGET["replay_url"]
    return response(
        body=b'<html><a href="https://example.org/unapproved.pdf">scores</a></html>',
        **{"memento-datetime": "Fri, 19 Jul 2019 20:49:19 GMT"},
    )


def make(setup, conn, reply=handler, *, policy=None):
    state, output, clock, authorization = setup
    return driver.ControlledFixtureRunner(
        conn,
        Config({HOST: policy or HostConfig()}, {}),
        clock,
        state=state,
        output=output,
        manifest=MANIFEST,
        authorization=authorization,
        transport=httpx.MockTransport(reply),
        monotonic=clock.monotonic,
    )


def test_exact_two_request_body_plan_has_no_children(setup):
    state, output, clock, _ = setup
    with driver.accounting_connection(state) as conn:
        result = make(setup, conn).run()
        assert result["status"] == "captured_pending_independent_review"
        assert [item["url"] for item in result["requests"]] == [
            f"https://{HOST}/robots.txt",
            TARGET["replay_url"],
        ]
        assert len(result["targets"]) == 1 and "cdx_pages_reported" not in result
        assert result["targets"][0]["captured_at"] == "2019-07-19T20:49:19+00:00"
        for table in [
            "watches",
            "snapshots",
            "source_generations",
            "observations",
            "pending_work",
            "runs",
            "host_request_spacing_baselines",
        ]:
            assert conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM execution_admissions WHERE state!='settled'"
            ).fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT sum(requests),sum(bytes) FROM host_budget").fetchone()[0] == 2
        assert (
            conn.execute("SELECT sum(bytes) FROM host_budget").fetchone()[0]
            == result["received_bytes"]
        )
        assert (state / "operator-hold").read_text() == "offline hold remains"
        for item in result["requests"]:
            assert (output / "bodies" / item["body_sha256"]).stat().st_size == item["body_bytes"]
    with driver.accounting_connection(state) as conn:
        with pytest.raises(FixtureStopped, match="single-use"):
            make(setup, conn)
    assert clock.elapsed >= 20


@pytest.mark.parametrize("jump", [9, -9])
def test_variable_latency_and_wall_jump_preserve_completion_floor(setup, monkeypatch, jump):
    state, _, clock, _ = setup
    dispatched = []

    def delayed(request):
        dispatched.append(clock.monotonic())
        clock.sleep(2 if len(dispatched) == 1 else 0.2)
        return handler(request)

    with driver.accounting_connection(state) as conn:
        runner = make(setup, conn, delayed)
        anchor = runner._anchor_completion

        def anchor_and_jump(request):
            anchor(request)
            if len(dispatched) == 1:
                clock.current += timedelta(seconds=jump)

        monkeypatch.setattr(runner, "_anchor_completion", anchor_and_jump)
        result = runner.run()
    assert result["status"] == "captured_pending_independent_review"
    assert dispatched[0] >= 10
    for previous, current in zip(result["requests"], result["requests"][1:], strict=False):
        assert (
            current["transport_dispatched_elapsed_seconds"]
            - previous["exchange_completed_elapsed_seconds"]
            >= 10
        )


@pytest.mark.parametrize(
    "kind,scope",
    [("all", "all"), ("source", "dcn"), ("host", HOST), ("kind", "source_event_mapping")],
)
def test_h13_scope_pauses_issue_zero_requests(setup, kind, scope):
    state = setup[0]
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES (?,?,?)",
            (kind, scope, "test"),
        )
    with driver.accounting_connection(state) as conn:
        result = make(setup, conn, lambda request: pytest.fail("paused network")).run()
    assert result["requests"] == [] and result["status"] == "stopped_incomplete"


@pytest.mark.parametrize("status", [302, 429, 500])
def test_no_redirect_or_retry_follows_any_failure(setup, status):
    with driver.accounting_connection(setup[0]) as conn:
        result = make(
            setup,
            conn,
            lambda request: (
                response(404)
                if request.url.path == "/robots.txt"
                else response(status, location=TARGET["replay_url"])
            ),
        ).run()
        assert (
            conn.execute(
                "SELECT count(*) FROM execution_admissions WHERE state!='settled'"
            ).fetchone()[0]
            == 0
        )
    assert result["status"] == "stopped_incomplete" and len(result["requests"]) == 2


def test_unknown_legacy_spacing_is_not_initialized_by_exception(setup):
    state, _, clock, _ = setup
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO host_budget VALUES (?,?,1,17)", (HOST, clock.now().date().isoformat())
        )
    with driver.accounting_connection(state) as conn:
        result = make(setup, conn, lambda request: pytest.fail("unknown spacing network")).run()
        assert tuple(conn.execute("SELECT requests,bytes FROM host_budget").fetchone()) == (1, 17)
        assert (
            conn.execute("SELECT count(*) FROM host_request_spacing_baselines").fetchone()[0] == 0
        )
    assert result["requests"] == [] and "legacy request spacing unknown" in result["stop_reason"]


def test_existing_spacing_recovery_and_shared_budget_are_preserved(setup):
    state, _, clock, _ = setup
    with open_database(state) as db:
        db.connection.execute("INSERT INTO hosts(host) VALUES (?)", (HOST,))
        db.connection.execute(
            "INSERT INTO host_budget VALUES (?,?,199,17)", (HOST, clock.now().date().isoformat())
        )
        db.connection.execute(
            "INSERT INTO host_request_spacing VALUES (?,?,10,?,?)",
            (HOST, "reviewed_prior", clock.now().isoformat(), clock.now().isoformat()),
        )
    with driver.accounting_connection(state) as conn:
        result = make(setup, conn).run()
        assert conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 200
    assert len(result["requests"]) == 1 and result["requests"][0]["purpose"] == "robots"
    assert result["requests"][0]["transport_dispatched_elapsed_seconds"] >= 20


def test_reservation_before_midnight_remains_on_paid_day(setup, monkeypatch):
    state, _, clock, authorization = setup
    clock.current = clock.current.replace(hour=23, minute=59, second=49)
    authorization["execution_window"]["starts_at"] = clock.now().isoformat()
    authorization["execution_window"]["expires_at"] = (
        clock.now() + timedelta(minutes=15)
    ).isoformat()
    paid_day = clock.now().date().isoformat()
    with driver.accounting_connection(state) as conn:
        runner = make(setup, conn)
        reserve = runner._reservation
        calls = 0

        def reserve_then_delay(day):
            nonlocal calls
            result = reserve(day)
            calls += 1
            if calls == 1:
                clock.sleep(2)
            return result

        monkeypatch.setattr(runner, "_reservation", reserve_then_delay)
        result = runner.run()
        assert result["status"] == "captured_pending_independent_review"
        first = result["requests"][0]
        assert first["request_day"] == paid_day
        assert first["transport_dispatched_at"][:10] != paid_day
        assert (
            conn.execute(
                "SELECT requests,bytes FROM host_budget WHERE day=?", (paid_day,)
            ).fetchone()[0]
            == 1
        )


def test_two_mib_response_ceiling_stops_without_retry(setup):
    with driver.accounting_connection(setup[0]) as conn:
        result = make(
            setup,
            conn,
            lambda request: (
                response(404)
                if request.url.path == "/robots.txt"
                else response(body=b"x" * 2097153)
            ),
        ).run()
        assert conn.execute("SELECT sum(bytes) FROM host_budget").fetchone()[0] == 2097152
    assert result["status"] == "stopped_incomplete" and len(result["requests"]) == 2
    assert result["received_bytes"] == 2097152 and not result["requests"][-1]["complete"]


def test_deadline_during_response_stops_all_remaining_requests(setup):
    def slow(request):
        setup[2].sleep(901)
        return response(404)

    with driver.accounting_connection(setup[0]) as conn:
        result = make(setup, conn, slow).run()
    assert result["status"] == "stopped_incomplete" and len(result["requests"]) == 1


def test_cached_robots_disallow_requires_no_request(setup):
    state, _, clock, _ = setup
    with open_database(state) as db:
        sha = Archive(state).store_body(b"User-agent: *\nDisallow: /\n")
        db.connection.execute(
            "INSERT INTO hosts(host,robots_sha256,robots_status,robots_fetched_at) VALUES (?,?,200,?)",
            (HOST, sha, clock.now().isoformat()),
        )
    with driver.accounting_connection(state) as conn:
        result = make(setup, conn, lambda request: pytest.fail("robots disallow")).run()
    assert result["requests"] == [] and result["cached_robots"]["body_sha256"] == sha


@pytest.mark.parametrize("condition", ["hold", "restore", "authorization"])
def test_execution_interlocks_fail_before_request(setup, condition):
    state = setup[0]
    if condition == "hold":
        (state / "operator-hold").unlink()
        with driver.accounting_connection(state) as conn:
            assert make(setup, conn, lambda request: pytest.fail("no hold")).run()["requests"] == []
    elif condition == "restore":
        (state / "RESTORE_PENDING").touch()
        with pytest.raises(FixtureStopped, match="restore"):
            with driver.accounting_connection(state):
                pytest.fail("restore opened")
    else:
        setup[3]["approval"] = "approved_new_source_fixture_exception_only"
        with driver.accounting_connection(state) as conn:
            with pytest.raises(FixtureStopped, match="authorization"):
                make(setup, conn)


def test_quarantine_sql_writer_cannot_create_domain_records(setup):
    with driver.accounting_connection(setup[0]) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('no','now',1)")


def test_dry_run_needs_no_state_or_owner_record(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(PACKET / "dcn-results-body-h13-001.py"),
            "--manifest",
            str(PACKET / "helper-closure/proposal.json"),
            "--state",
            str(tmp_path / "absent"),
            "--quarantine",
            str(tmp_path / "quarantine"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)
    assert (
        report["dry_run"]
        and report["requests_made"] == 0
        and report["targets"] == [TARGET["replay_url"]]
    )
    assert report["metadata_queries"] == [] and not list(tmp_path.iterdir())


def test_preparation_pins_schema28_and_preserves_state_without_network(
    setup, tmp_path, monkeypatch
):
    state, output, clock, _ = setup
    packet = tmp_path / "packet"
    shutil.copytree(PACKET, packet)
    baseline = state / preparation.BASELINE_CANDIDATE
    (baseline / "_meta").mkdir(parents=True)
    (baseline / "PUBLISHED").write_text(json.dumps(dict(commit=preparation.BASELINE_COMMIT)))
    (baseline / "_meta/manifest.json").write_text("{}")
    (state / "baseline").symlink_to(baseline)
    monkeypatch.setattr(preparation, "verify_runtime", lambda source: 777)
    before = preparation.sha(state / "state.sqlite")
    result = preparation.prepare(
        packet=packet,
        state=state,
        quarantine=output,
        approved_by="offline",
        approved_at=clock.now(),
        decision="synthetic offline approval",
        publication="synthetic publication",
        now=clock.now(),
    )
    assert preparation.sha(state / "state.sqlite") == before and not output.exists()
    gate = json.loads((packet / "execution-gate.json").read_bytes())
    assert gate["schema"] == 28 and gate["source_receipt"] == "extension-source.json"
    assert gate["source"] == "/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source"
    assert result["command"][0] == "/run/current-system/sw/bin/env"
    assert result["network_requests"] == 0 and result["executed"] is False
    with driver.accounting_connection(state) as conn:
        driver.verify_database(conn, state, gate)
    with pytest.raises(ValueError, match="single-use"):
        preparation.prepare(
            packet=packet,
            state=state,
            quarantine=output,
            approved_by="offline",
            approved_at=clock.now(),
            decision="offline",
            publication="offline",
            now=clock.now(),
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://danceconvention.net/eventdirector/en/eventpage/1546230-riga-summer-swing/results",
        "https://web.archive.org/web/20180815191517id_/https://danceconvention.net/results",
        "https://web.archive.org/cdx/search/cdx?url=not-approved",
    ],
)
def test_arbitrary_body_origin_or_next_page_is_not_in_allowlist(setup, url):
    with driver.accounting_connection(setup[0]) as conn:
        runner = make(setup, conn)
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: pytest.fail("URL escaped"))
        ) as client:
            with pytest.raises(FixtureStopped, match="allowlist"):
                runner._request(client, url, "cdx")


def test_cached_robots_avoids_redundant_request(setup):
    state, _, clock, _ = setup
    with open_database(state) as db:
        sha = Archive(state).store_body(b"User-agent: *\nAllow: /\n")
        db.connection.execute(
            "INSERT INTO hosts(host,robots_sha256,robots_status,robots_fetched_at) VALUES (?,?,200,?)",
            (HOST, sha, clock.now().isoformat()),
        )
    with driver.accounting_connection(state) as conn:
        result = make(setup, conn).run()
    assert len(result["requests"]) == 1 and result["cached_robots"]["body_sha256"] == sha


def test_too_long_authorization_window_is_rejected(setup):
    setup[3]["execution_window"]["expires_at"] = (
        setup[2].now() + timedelta(minutes=16)
    ).isoformat()
    with driver.accounting_connection(setup[0]) as conn:
        with pytest.raises(FixtureStopped, match="15 minutes"):
            make(setup, conn)


@pytest.mark.parametrize("damage", ["helper", "extra", "symlink"])
def test_preparer_rejects_altered_helper_closure(tmp_path, damage):
    packet = tmp_path / "packet"
    shutil.copytree(PACKET, packet)
    if damage == "helper":
        (packet / "helper-closure/fixture_helpers/__init__.py").write_text("altered")
    elif damage == "extra":
        (packet / "helper-closure/extra.py").write_text("unreviewed")
    else:
        (packet / "helper-closure/linked").symlink_to(packet / "helper-closure/proposal.json")
    with pytest.raises(ValueError):
        preparation.verify_packet(packet)
    assert not (packet / "authorization.json").exists()


def test_preparer_and_executor_reject_schema14_without_migration(setup):
    state, _, clock, _ = setup
    baseline = state / preparation.BASELINE_CANDIDATE
    (baseline / "_meta").mkdir(parents=True)
    (baseline / "PUBLISHED").write_text(json.dumps(dict(commit=preparation.BASELINE_COMMIT)))
    (baseline / "_meta/manifest.json").write_text("{}")
    (state / "baseline").symlink_to(baseline)
    gate = dict(
        baseline_path=str(baseline),
        published_sha256=driver.digest(baseline / "PUBLISHED"),
        manifest_sha256=driver.digest(baseline / "_meta/manifest.json"),
        schema=28,
    )
    with open_database(state) as db:
        db.connection.execute("UPDATE meta SET value='14' WHERE key='schema_version'")
    with pytest.raises(ValueError, match="schema28"):
        preparation.inspect_state(state, now=clock.now())
    with driver.accounting_connection(state) as conn:
        with pytest.raises(FixtureStopped, match="schema"):
            driver.verify_database(conn, state, gate)
        assert (
            conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "14"
        )


def test_future_robots_cache_does_not_replace_fresh_policy(setup):
    state, _, clock, _ = setup
    with open_database(state) as db:
        sha = Archive(state).store_body(b"User-agent: *\nAllow: /\n")
        db.connection.execute(
            "INSERT INTO hosts(host,robots_sha256,robots_status,robots_fetched_at) VALUES (?,?,200,?)",
            (HOST, sha, (clock.now() + timedelta(hours=1)).isoformat()),
        )
    with driver.accounting_connection(state) as conn:
        result = make(setup, conn).run()
    assert len(result["requests"]) == 2 and "cached_robots" not in result


def test_preparer_verifies_full_real_frozen003_inventory():
    import swingset.state.db as db_module

    runtime = Path(db_module.__file__).resolve().parents[3]
    assert preparation.verify_runtime(runtime) > 638


def test_gate_tampering_and_missing_gate_stop_before_state_access(tmp_path):
    path = tmp_path / "gate.json"
    path.write_text("{}")
    with pytest.raises(FixtureStopped, match="gate hash"):
        driver.execution_gate(path, "0" * 64, tmp_path)
    with pytest.raises(FixtureStopped, match="explicit pinned"):
        driver.parse_arguments(
            [
                "--manifest",
                "manifest",
                "--state",
                "state",
                "--quarantine",
                "quarantine",
                "--execute",
            ]
        )


def test_pragma_schema_mismatch_is_rejected_even_when_meta_still28(setup):
    state, _, clock, _ = setup
    baseline = state / preparation.BASELINE_CANDIDATE
    (baseline / "_meta").mkdir(parents=True)
    (baseline / "PUBLISHED").write_text(json.dumps(dict(commit=preparation.BASELINE_COMMIT)))
    (baseline / "_meta/manifest.json").write_text("{}")
    (state / "baseline").symlink_to(baseline)
    gate = dict(
        baseline_path=str(baseline),
        published_sha256=driver.digest(baseline / "PUBLISHED"),
        manifest_sha256=driver.digest(baseline / "_meta/manifest.json"),
        schema=28,
    )
    with sqlite3.connect(state / "state.sqlite") as conn:
        conn.execute("PRAGMA user_version=14")
    with pytest.raises(ValueError, match="schema28"):
        preparation.inspect_state(state, now=clock.now())
    with driver.accounting_connection(state) as conn:
        with pytest.raises(FixtureStopped, match="schema"):
            driver.verify_database(conn, state, gate)
        assert (
            conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "28"
        )


def test_restore_flag_appearing_during_lock_acquisition_blocks_database_open(setup, monkeypatch):
    state = setup[0]
    flock = driver.fcntl.flock

    def locked_then_restore(file, operation):
        flock(file, operation)
        (state / "RESTORE_PENDING").touch()

    monkeypatch.setattr(driver.fcntl, "flock", locked_then_restore)
    with pytest.raises(FixtureStopped, match="after writer lock"):
        with driver.accounting_connection(state):
            pytest.fail("restore crossed locked boundary")


def allow_cached_robots(setup):
    state, _, clock, _ = setup
    with open_database(state) as db:
        sha = Archive(state).store_body(b"User-agent: *\nAllow: /\n")
        db.connection.execute(
            "INSERT INTO hosts(host,robots_sha256,robots_status,robots_fetched_at) VALUES (?,?,200,?)",
            (HOST, sha, clock.now().isoformat()),
        )


def test_non_aligned_daily_remaining_capacity_is_rounded_down_without_slicing(setup):
    allow_cached_robots(setup)
    state, _, clock, _ = setup
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO host_budget VALUES (?,?,0,42)", (HOST, clock.now().date().isoformat())
        )
    with driver.accounting_connection(state) as conn:
        result = make(
            setup,
            conn,
            lambda request: response(body=b"x" * 65537),
            policy=HostConfig(daily_byte_budget=42 + 65536 + 17),
        ).run()
        assert conn.execute("SELECT bytes FROM host_budget").fetchone()[0] == 42 + 65536
    item = result["requests"][0]
    assert item["reserved_bytes"] == item["body_bytes"] == item["budget_charged_bytes"] == 65536
    assert result["status"] == "stopped_incomplete" and not item["complete"]
    assert result["byte_measurement"] == "response_body_bytes_exposed_by_iter_raw"
    assert result["transport_buffering_not_measured"] is True


def test_less_than_one_read_chunk_stops_without_http_and_keeps_paid_grant(setup):
    allow_cached_robots(setup)
    state, _, clock, _ = setup
    with driver.accounting_connection(state) as conn:
        result = make(
            setup,
            conn,
            lambda request: pytest.fail("no capacity HTTP"),
            policy=HostConfig(daily_byte_budget=65535),
        ).run()
        assert tuple(conn.execute("SELECT requests,bytes FROM host_budget").fetchone()) == (1, 0)
        assert (
            conn.execute(
                "SELECT count(*) FROM execution_admissions WHERE state!='settled'"
            ).fetchone()[0]
            == 0
        )
    assert result["status"] == "stopped_incomplete" and result["requests"] == []


def test_incomplete_stream_keeps_full_conservative_byte_reservation(setup):
    allow_cached_robots(setup)

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 65536
            raise httpx.ReadError("offline mid-body failure")

    with driver.accounting_connection(setup[0]) as conn:
        result = make(setup, conn, lambda request: httpx.Response(200, stream=BrokenStream())).run()
        assert conn.execute("SELECT bytes FROM host_budget").fetchone()[0] == 2097152
    item = result["requests"][0]
    assert (
        item["body_bytes"] == 65536
        and item["budget_charged_bytes"] == item["reserved_bytes"] == 2097152
    )
    assert not item["complete"] and result["status"] == "stopped_incomplete"


def test_exact_at_cap_stops_before_extra_eof_probe(setup):
    allow_cached_robots(setup)

    class ExactStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 2097152
            pytest.fail("must not consume EOF probe at the cap")

    with driver.accounting_connection(setup[0]) as conn:
        result = make(setup, conn, lambda request: httpx.Response(200, stream=ExactStream())).run()
    assert result["requests"][0]["body_bytes"] == 2097152
    assert not result["requests"][0]["complete"]


def test_chunk_delivered_at_deadline_is_retained_and_counted_before_stop(setup):
    allow_cached_robots(setup)

    class LateStream(httpx.SyncByteStream):
        def __iter__(self):
            setup[2].sleep(901)
            yield b"x" * 65536
            pytest.fail("deadline must stop before another read")

    with driver.accounting_connection(setup[0]) as conn:
        result = make(setup, conn, lambda request: httpx.Response(200, stream=LateStream())).run()
        assert conn.execute("SELECT bytes FROM host_budget").fetchone()[0] == 2097152
    item = result["requests"][0]
    assert item["body_bytes"] == result["received_bytes"] == 65536
    assert item["budget_charged_bytes"] == 2097152 and not item["complete"]
    assert (setup[1] / "bodies" / item["body_sha256"]).stat().st_size == 65536


@pytest.mark.parametrize("stamp", ["Fri, 19 Jul 2019 20:49:20 GMT", "malformed"])
def test_memento_mismatch_is_retained_but_not_accepted_or_retried(setup, stamp):
    def changed(request):
        return (
            response(404)
            if request.url.path == "/robots.txt"
            else response(body=b"different capture", **{"memento-datetime": stamp})
        )

    with driver.accounting_connection(setup[0]) as conn:
        result = make(setup, conn, changed).run()
    assert result["status"] == "stopped_incomplete" and len(result["requests"]) == 2
    assert result["targets"] == [] and result["requests"][-1]["complete"]
    assert (
        setup[1] / "bodies" / result["requests"][-1]["body_sha256"]
    ).read_bytes() == b"different capture"


def test_exact_memento_matches_approved_capture(setup):
    def exact(request):
        return (
            response(404)
            if request.url.path == "/robots.txt"
            else response(
                body=b"<html></html>", **{"memento-datetime": "Fri, 19 Jul 2019 20:49:19 GMT"}
            )
        )

    with driver.accounting_connection(setup[0]) as conn:
        result = make(setup, conn, exact).run()
    assert result["status"] == "captured_pending_independent_review"


def test_packaged_cdx_evidence_is_required_and_confined(tmp_path):
    from fixture_helpers.fixture_exception import retained_evidence_path

    assert (
        retained_evidence_path(tmp_path, "evidence/cdx-page0.json").read_bytes()
        == (PACKET / "helper-closure/evidence/cdx-page0.json").read_bytes()
    )
    with pytest.raises(FixtureStopped, match="escapes"):
        retained_evidence_path(tmp_path, str(PACKET / "build-receipt.json"))
    packet = tmp_path / "packet"
    shutil.copytree(PACKET, packet)
    (packet / "helper-closure/evidence/cdx-page0.json").write_text("[]")
    with pytest.raises(ValueError, match="bytes changed"):
        preparation.verify_packet(packet)


def test_missing_memento_does_not_turn_requested_timestamp_into_observed_capture(setup):
    with driver.accounting_connection(setup[0]) as conn:
        result = make(
            setup,
            conn,
            lambda request: (
                response(404)
                if request.url.path == "/robots.txt"
                else response(body=b"<html></html>")
            ),
        ).run()
    assert result["status"] == "stopped_incomplete" and len(result["requests"]) == 2
    assert "Memento" in result["stop_reason"] and result["targets"] == []
    assert (
        result["requests"][-1]["complete"]
        and (setup[1] / "bodies" / result["requests"][-1]["body_sha256"]).exists()
    )
