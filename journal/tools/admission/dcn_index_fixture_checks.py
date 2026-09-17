"""Run explicitly against a built packet and the receipt-verified schema14 mirror.

Set DCN_FIXTURE_PACKET and PYTHONPATH to packet/helper-closure plus frozen src/root;
use PYTHONDONTWRITEBYTECODE=1. All HTTP uses MockTransport and disposable state.
"""

import importlib.util
import os
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fixture_helpers.fixture_exception import APPROVAL, MANIFEST_SHA, FixtureStopped, read_manifest

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig
from swingset.fetch.archive import Archive
from swingset.state.db import SCHEMA_VERSION, open_database

assert SCHEMA_VERSION == 14, "checks must use frozen runtime14, never current runtime"
PACKET = Path(os.environ["DCN_FIXTURE_PACKET"])
spec = importlib.util.spec_from_file_location(
    "dcn_fixture_driver", PACKET / "dcn-index-fixture-h13-001.py"
)
assert spec and spec.loader
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
HOST = "web.archive.org"


class ElapsedClock(FakeClock):
    elapsed = 0.0

    def sleep(self, seconds):
        super().sleep(seconds)
        self.elapsed += max(0, seconds)


@pytest.fixture
def setup(tmp_path):
    clock = ElapsedClock()
    state, output = tmp_path / "state", tmp_path / "quarantine"
    with open_database(state):
        pass
    (state / "operator-hold").touch()
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
            expires_at=(clock.now() + timedelta(hours=1)).isoformat(),
        ),
    )
    return state, output, clock, authorization


def make(setup, conn, handler):
    state, output, clock, authorization = setup
    return driver.ControlledFixtureRunner(
        conn,
        Config({HOST: HostConfig()}, {}),
        clock,
        state=state,
        output=output,
        manifest=read_manifest(PACKET / "helper-closure/proposal.json", PACKET),
        authorization=authorization,
        transport=httpx.MockTransport(handler),
        monotonic=lambda: clock.elapsed,
    )


def reply(code=200, content=b"<html>offline fixture</html>", **headers):
    return httpx.Response(code, stream=httpx.ByteStream(content), headers=headers)


@pytest.mark.parametrize("jump", [9, -9])
def test_wall_clock_adjustment_cannot_shorten_elapsed_floor(setup, monkeypatch, jump):
    state, _, clock, _ = setup
    dispatches = []

    def handler(request):
        dispatches.append(clock.elapsed)
        clock.sleep(2)
        return reply(404 if request.url.path == "/robots.txt" else 200)

    with driver.accounting_connection(state) as conn:
        runner = make(setup, conn, handler)
        original_anchor = runner._anchor_completion

        def anchor_then_jump(request):
            original_anchor(request)
            if len(dispatches) == 1:
                clock.current += timedelta(seconds=jump)

        monkeypatch.setattr(runner, "_anchor_completion", anchor_then_jump)
        result = runner.run()
    assert result["status"] == "captured_pending_independent_review"
    first, second = result["requests"]
    assert dispatches[0] >= 10  # Fresh processes also wait a conservative initial floor.
    assert (
        second["transport_dispatched_elapsed_seconds"] - first["exchange_completed_elapsed_seconds"]
        >= 10
    )
    assert dispatches[1] - dispatches[0] >= 12


def test_variable_bookkeeping_and_body_latency_keep_completion_floor(setup, monkeypatch):
    state, output, clock, _ = setup
    dispatched = []

    def handler(request):
        dispatched.append((str(request.url), clock.now()))
        clock.sleep(7 if len(dispatched) == 1 else 2)
        return reply(404 if request.url.path == "/robots.txt" else 200)

    with driver.accounting_connection(state) as conn:
        runner = make(setup, conn, handler)
        reserve, save = runner._reservation, runner._save
        reservation_count = 0

        def delayed_reservation(day):
            nonlocal reservation_count
            reservation_count += 1
            clock.sleep(0.300 if reservation_count == 1 else 0.001)
            return reserve(day)

        def delayed_save():
            clock.sleep(0.100 if reservation_count == 1 else 0.002)
            return save()

        monkeypatch.setattr(runner, "_reservation", delayed_reservation)
        monkeypatch.setattr(runner, "_save", delayed_save)
        result = runner.run()
        assert result["status"] == "captured_pending_independent_review"
        assert len(result["requests"]) == len(dispatched) == 2 and len(result["targets"]) == 1
        first, second = result["requests"]
        assert dispatched[0][1].isoformat() == first["transport_dispatched_at"]
        assert dispatched[1][1].isoformat() == second["transport_dispatched_at"]
        assert dispatched[1][1] >= clock.current.fromisoformat(first["next_dispatch_not_before"])
        assert (
            dispatched[1][1] - clock.current.fromisoformat(first["exchange_completed_at"])
        ).total_seconds() >= 10
        assert (
            conn.execute("SELECT next_allowed_at FROM hosts WHERE host=?", (HOST,)).fetchone()[0]
            == second["next_dispatch_not_before"]
        )
        assert conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == 2
        for table in ("watches", "snapshots", "source_generations", "observations", "runs"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM execution_admissions WHERE state!='settled'"
            ).fetchone()[0]
            == 0
        )
    with pytest.raises(FixtureStopped, match="single-use"):
        with driver.accounting_connection(state) as conn:
            make(setup, conn, lambda _: pytest.fail("retry"))


@pytest.mark.parametrize(
    ("kind", "scope"),
    [("all", "all"), ("source", "dcn"), ("host", HOST), ("kind", "source_event_mapping")],
)
def test_h13_pauses_stop_before_network(setup, kind, scope):
    state, _, _, _ = setup
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO operator_pauses(scope_kind,scope_id,reason) VALUES (?,?,?)",
            (kind, scope, "offline hold"),
        )
    with driver.accounting_connection(state) as conn:
        result = make(setup, conn, lambda _: pytest.fail("paused HTTP")).run()
        assert result["status"] == "stopped_incomplete" and result["requests"] == []


def test_shared_budget_not_reset_and_paid_robots_retained(setup):
    state, _, clock, _ = setup
    with open_database(state) as db:
        db.connection.execute(
            "INSERT INTO host_budget VALUES (?,?,199,123)", (HOST, clock.now().date().isoformat())
        )
    with driver.accounting_connection(state) as conn:
        result = make(setup, conn, lambda _: reply(404)).run()
        assert result["status"] == "stopped_incomplete" and len(result["requests"]) == 1
        assert result["requests"][0]["purpose"] == "robots"
        assert conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 200


def test_exact_byte_ceiling_retains_prefix_without_any_alternate(setup):
    state = setup[0]
    with driver.accounting_connection(state) as conn:
        runner = make(
            setup,
            conn,
            lambda request: (
                reply(404, b"")
                if request.url.path == "/robots.txt"
                else reply(200, b"x" * (8388608 + 1))
            ),
        )
        assert runner.limits["max_total_received_bytes"] == 16777216
        result = runner.run()
        assert result["status"] == "stopped_incomplete"
        assert len(result["requests"]) == 2 and result["targets"] == []
        assert result["received_bytes"] == 8388608
        assert not result["requests"][-1]["complete"]
        assert conn.execute("SELECT sum(bytes) FROM host_budget").fetchone()[0] == 8388608


def test_elapsed_deadline_stops_during_response_without_next_request(setup):
    state, _, clock, _ = setup

    def slow_response(request):
        clock.sleep(901)
        return reply(404)

    with driver.accounting_connection(state) as conn:
        result = make(setup, conn, slow_response).run()
        assert result["status"] == "stopped_incomplete" and len(result["requests"]) == 1
        assert "elapsed-time" in result["stop_reason"]
        assert (
            conn.execute(
                "SELECT count(*) FROM execution_admissions WHERE state!='settled'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("condition", ["hold", "restore"])
def test_runtime_interlocks_prevent_acquisition(setup, condition):
    state = setup[0]
    if condition == "hold":
        (state / "operator-hold").unlink()
        with driver.accounting_connection(state) as conn:
            assert make(setup, conn, lambda _: pytest.fail("held HTTP")).run()["requests"] == []
    else:
        (state / "RESTORE_PENDING").touch()
        with pytest.raises(FixtureStopped, match="restore"):
            with driver.accounting_connection(state):
                pytest.fail("restore opened")


def test_cached_robots_is_observed_and_disallow_stops_body(setup):
    state, _, clock, _ = setup
    with open_database(state) as db:
        sha = Archive(state).store_body(b"User-agent: *\nDisallow: /\n")
        db.connection.execute(
            "INSERT INTO hosts(host,robots_sha256,robots_fetched_at,robots_status) VALUES (?,?,?,200)",
            (HOST, sha, clock.now().isoformat()),
        )
    with driver.accounting_connection(state) as conn:
        result = make(setup, conn, lambda _: pytest.fail("robots-disallowed HTTP")).run()
        assert result["status"] == "stopped_incomplete" and result["requests"] == []
        assert result["cached_robots"]["body_sha256"] == sha


def test_three_archive_redirects_fit_exact_five_request_ceiling(setup):
    state = setup[0]
    body_calls = 0

    def handler(request):
        nonlocal body_calls
        if request.url.path == "/robots.txt":
            return reply(404)
        body_calls += 1
        return reply(302, location=str(request.url)) if body_calls <= 3 else reply()

    with driver.accounting_connection(state) as conn:
        result = make(setup, conn, handler).run()
        assert result["status"] == "captured_pending_independent_review"
        assert len(result["requests"]) == 5 and body_calls == 4


@pytest.mark.parametrize(
    "location",
    [
        "https://danceconvention.net/eventdirector/en/eventsarchive",
        "https://web.archive.org/cdx/search/cdx",
        "https://web.archive.org/web/20251112105828id_/https://danceconvention.net/roundscores/guessed.pdf",
    ],
)
def test_redirect_escape_is_never_followed(setup, location):
    state = setup[0]
    with driver.accounting_connection(state) as conn:
        result = make(
            setup,
            conn,
            lambda request: (
                reply(404) if request.url.path == "/robots.txt" else reply(302, location=location)
            ),
        ).run()
        assert result["status"] == "stopped_incomplete" and len(result["requests"]) == 2


def test_failure_keeps_completion_floor_and_cannot_retry(setup):
    state = setup[0]
    with driver.accounting_connection(state) as conn:
        result = make(
            setup,
            conn,
            lambda request: reply(404) if request.url.path == "/robots.txt" else reply(500),
        ).run()
        assert result["status"] == "stopped_incomplete" and len(result["requests"]) == 2
        assert result["requests"][-1]["next_dispatch_not_before"]
        assert conn.execute("SELECT paused_until FROM hosts WHERE host=?", (HOST,)).fetchone()[0]
        assert (
            conn.execute(
                "SELECT count(*) FROM execution_admissions WHERE state!='settled'"
            ).fetchone()[0]
            == 0
        )


def test_unapproved_manifest_or_authorization_rejected_before_constructor_http(setup):
    state, _, _, authorization = setup
    authorization["approval"] = "approved_new_source_fixture_exception_only"
    with driver.accounting_connection(state) as conn:
        with pytest.raises(FixtureStopped, match="authorization"):
            make(setup, conn, lambda _: pytest.fail("unapproved HTTP"))


def test_domain_writes_forbidden_and_migrated_schema_rejected(setup):
    state = setup[0]
    baseline = state / "candidate"
    (baseline / "_meta").mkdir(parents=True)
    (baseline / "PUBLISHED").write_text("published")
    (baseline / "_meta/manifest.json").write_text("manifest")
    (state / "baseline").symlink_to(baseline)
    gate = dict(
        baseline_path=str(baseline),
        published_sha256=driver.digest(baseline / "PUBLISHED"),
        manifest_sha256=driver.digest(baseline / "_meta/manifest.json"),
        schema=14,
    )
    with driver.accounting_connection(state) as conn:
        driver.verify_database(conn, state, gate)
        import sqlite3

        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('forbidden','now',1)")
    with open_database(state) as db:
        db.connection.execute("UPDATE meta SET value='28' WHERE key='schema_version'")
    with driver.accounting_connection(state) as conn:
        with pytest.raises(FixtureStopped, match="schema"):
            driver.verify_database(conn, state, gate)
