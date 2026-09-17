"""HTTP dispatch spacing survives grant latency, shared clients and lost workers."""

import subprocess
import sys
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from test_fetch_controls import BODY, client
from test_fetch_controls import fetched as fetched_fixture

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.politeness import Gate, Grant, Paused, Wait
from swingset.sources.wsdc_calendar import EventsPage
from swingset.state.db import open_database

fetched = fetched_fixture


def policy(host="example.test", *, gap=10, budget=200):
    return Config({host: HostConfig(min_gap_seconds=gap, daily_request_budget=budget)}, {})


def test_actual_http_dispatches_keep_gap_despite_variable_post_grant_latency(fetched, monkeypatch):
    f = fetched
    f.config = replace(f.config, hosts={"example.test": HostConfig(min_gap_seconds=10)})
    starts = []
    ends = []

    def handler(request):
        starts.append(f.clock.monotonic())
        f.clock.sleep(0.2)
        ends.append(f.clock.monotonic())
        return (
            httpx.Response(200, text="User-agent: *\nCrawl-delay: 13\n")
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=BODY)
        )

    fetcher = client(f, handler)
    acquire = fetcher.gate.acquire
    delays = iter([0.025, 0.001, 0.009])

    def delayed_grant(*args, **kwargs):
        grant = acquire(*args, **kwargs)
        if isinstance(grant, Grant):
            f.clock.sleep(next(delays))
        return grant

    monkeypatch.setattr(fetcher.gate, "acquire", delayed_grant)
    try:
        fetcher.fetch(f.spec.watch_id, EventsPage(), f.run)
        fetcher.fetch(f.spec.watch_id, EventsPage(), f.run)
    finally:
        fetcher.close()
    assert len(starts) == 3
    assert starts[1] - ends[0] >= 10
    assert starts[2] - ends[1] >= 13
    assert f.db.connection.execute("SELECT requests FROM host_budget").fetchone()[0] == 3
    assert f.db.connection.execute("SELECT count(*) FROM scheduler_requests").fetchone()[0] == 3


def test_two_live_gates_share_inflight_and_original_gap_with_clock_jump(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        first = Gate(db.connection, policy(), clock)
        second = Gate(db.connection, policy(gap=5), clock)
        assert isinstance(first.acquire("example.test", crawl_delay=23), Grant)
        clock.sleep(40)
        assert second.acquire("example.test") == Wait(1)
        first.release("example.test", Classification(Outcome.OK))
        clock.current += timedelta(days=1)
        assert second.acquire("example.test") == Wait(23)
        clock.sleep(23)
        assert isinstance(second.acquire("example.test"), Grant)
        second.release("example.test", Classification(Outcome.OK))


@pytest.mark.parametrize(
    "host,sweep,gap",
    [
        ("example.test", False, 5),
        ("points.worldsdc.com", True, 2),
        ("points.worldsdc.com", False, 5),
    ],
)
def test_release_uses_original_floor_and_sweep_exception(tmp_path, host, sweep, gap):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        gate = Gate(db.connection, policy(host, gap=2), clock)
        assert isinstance(gate.acquire(host, sweep=sweep), Grant)
        clock.sleep(30)
        gate.release(host, Classification(Outcome.INVALID))
        assert gate.acquire(host, sweep=sweep) == Wait(gap)


def test_crash_after_late_dispatch_requires_full_original_gap_without_refund(tmp_path):
    # Process death leaves no live owner or monotonic deadline in this process.
    script = """
import os, sys
from swingset.clock import FakeClock
from swingset.config import Config
from swingset.fetch.politeness import Gate, Grant
from swingset.state.db import open_database
clock = FakeClock()
db = open_database(sys.argv[1])
gate = Gate(db.connection, Config({}, {}), clock)
assert isinstance(gate.acquire('example.test', crawl_delay=17), Grant)
clock.sleep(600)
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=True)
    clock = FakeClock()
    clock.sleep(600)
    with open_database(tmp_path) as db:
        gate = Gate(db.connection, policy(gap=5), clock)
        changes = db.connection.total_changes
        assert gate.assess("example.test")[0] == Paused("request spacing recovery required")
        assert db.connection.total_changes == changes
        assert gate.acquire("example.test") == Wait(17)
        clock.sleep(16)
        assert gate.acquire("example.test") == Wait(1)
        clock.sleep(1)
        assert isinstance(gate.acquire("example.test"), Grant)
        assert db.connection.execute("SELECT requests FROM host_budget").fetchone()[0] == 2
        gate.release("example.test", Classification(Outcome.OK))


def test_release_preserves_longer_existing_deadline_and_original_debit_day(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        gate = Gate(db.connection, policy(), clock)
        day = clock.now().date().isoformat()
        assert isinstance(gate.acquire("example.test", crawl_delay=19), Grant)
        clock.sleep(86400)
        deadline = clock.now() + timedelta(seconds=90)
        db.connection.execute("UPDATE hosts SET next_allowed_at=?", (deadline.isoformat(),))
        gate.release("example.test", Classification(Outcome.OK), body_bytes=9, request_day=day)
        assert gate.acquire("example.test") == Wait(19)
        clock.sleep(19)
        assert gate.acquire("example.test") == Wait(71)
        assert tuple(
            db.connection.execute("SELECT day,requests,bytes FROM host_budget").fetchone()
        ) == (day, 1, 9)


def test_failed_release_keeps_durable_reservation_and_request_debit(tmp_path):
    import sqlite3

    clock = FakeClock()
    with open_database(tmp_path) as db:
        gate = Gate(db.connection, policy(), clock)
        assert isinstance(gate.acquire("example.test"), Grant)
        db.connection.execute(
            "CREATE TRIGGER reject_bytes BEFORE UPDATE OF bytes ON host_budget BEGIN SELECT RAISE(ABORT,'offline failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="offline failure"):
            gate.release("example.test", Classification(Outcome.OK), body_bytes=4)
        assert (
            db.connection.execute("SELECT released_at FROM host_request_spacing").fetchone()[0]
            is None
        )
        assert tuple(
            db.connection.execute("SELECT requests,bytes FROM host_budget").fetchone()
        ) == (1, 0)
        assert not db.connection.in_transaction
        assert Gate(db.connection, policy(), clock).acquire("example.test") == Wait(10)


def test_unsupported_schema_does_not_infer_spacing_from_old_grant(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        db.connection.execute("DROP TABLE host_request_spacing")
        gate = Gate(db.connection, policy(), clock)
        assert gate.acquire("example.test") == Paused("request spacing schema unavailable")
        assert db.connection.execute("SELECT count(*) FROM host_budget").fetchone()[0] == 0


def test_schema_upgrade_requires_reviewed_legacy_baseline_without_refund(tmp_path, monkeypatch):
    from swingset.state import db as database_module

    clock = FakeClock()
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 27)
    with open_database(tmp_path) as db:
        db.connection.execute("INSERT INTO hosts(host) VALUES ('example.test')")
        db.connection.execute(
            "INSERT INTO host_budget VALUES ('example.test',?,12,34)",
            (clock.now().date().isoformat(),),
        )
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 28)
    with open_database(tmp_path) as db:
        gate = Gate(db.connection, policy(), clock)
        assert gate.acquire("example.test") == Paused("legacy request spacing unknown")
        assert gate.acquire("new.test") == Grant("new.test")
        gate.release("new.test", Classification(Outcome.OK))
        with db.transaction():
            receipt = gate.establish_spacing_baseline(
                "example.test",
                gap_seconds=17,
                stopped_at=clock.now(),
                evidence_ref="offline://verified-stopped-worker-and-robots",
            )
        assert gate.acquire("example.test") == Wait(17)
        clock.sleep(17)
        assert isinstance(gate.acquire("example.test"), Grant)
        gate.release("example.test", Classification(Outcome.OK))
        assert tuple(
            db.connection.execute(
                "SELECT requests,bytes FROM host_budget WHERE host='example.test'"
            ).fetchone()
        ) == (13, 34)
        baseline = db.connection.execute(
            "SELECT * FROM host_request_spacing_baselines WHERE reservation_id=?", (receipt,)
        ).fetchone()
        assert baseline["gap_seconds"] == 17
        assert baseline["prior_reservation_id"] == "legacy_example.test"
        assert baseline["evidence_ref"] == "offline://verified-stopped-worker-and-robots"


def test_legacy_baseline_requires_transaction_valid_gap_and_no_active_request(tmp_path):
    from swingset.state.controls import ActionScope, admission

    clock = FakeClock()
    with open_database(tmp_path) as db:
        db.connection.execute("INSERT INTO hosts(host) VALUES ('example.test')")
        db.connection.execute(
            "INSERT INTO host_budget VALUES ('example.test',?,1,0)",
            (clock.now().date().isoformat(),),
        )
        gate = Gate(db.connection, policy(), clock)
        kwargs = dict(gap_seconds=10, stopped_at=clock.now(), evidence_ref="offline://stopped")
        with pytest.raises(ValueError, match="writer transaction"):
            gate.establish_spacing_baseline("example.test", **kwargs)
        with db.transaction():
            with pytest.raises(ValueError, match="configured floor"):
                gate.establish_spacing_baseline("example.test", **{**kwargs, "gap_seconds": 5})
        with admission(
            db,
            action_id="active-request",
            action_kind="request",
            scope=ActionScope(host="example.test"),
            now=clock.now(),
        ):
            pass
        with db.transaction():
            with pytest.raises(ValueError, match="unsettled"):
                gate.establish_spacing_baseline("example.test", **kwargs)
        assert (
            db.connection.execute("SELECT count(*) FROM host_request_spacing_baselines").fetchone()[
                0
            ]
            == 0
        )


def test_aborted_legacy_baseline_never_authorizes_recovery(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        db.connection.execute("INSERT INTO hosts(host) VALUES ('example.test')")
        db.connection.execute(
            "INSERT INTO host_budget VALUES ('example.test',?,1,0)",
            (clock.now().date().isoformat(),),
        )
        gate = Gate(db.connection, policy(), clock)
        with pytest.raises(RuntimeError):
            with db.transaction():
                gate.establish_spacing_baseline(
                    "example.test",
                    gap_seconds=10,
                    stopped_at=clock.now(),
                    evidence_ref="offline://stopped",
                )
                raise RuntimeError("rollback")
        clock.sleep(60)
        assert gate.acquire("example.test") == Paused("legacy request spacing unknown")
        assert (
            db.connection.execute("SELECT count(*) FROM host_request_spacing_baselines").fetchone()[
                0
            ]
            == 0
        )


def test_new_clock_domain_waits_full_gap_even_after_recorded_completion(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        first = Gate(db.connection, policy(), clock)
        assert isinstance(first.acquire("example.test", crawl_delay=29), Grant)
        first.release("example.test", Classification(Outcome.OK))
        restarted = FakeClock(clock.now() + timedelta(days=30))
        gate = Gate(db.connection, policy(gap=5), restarted)
        assert gate.assess("example.test")[0] == Paused("request spacing recovery required")
        assert gate.acquire("example.test") == Wait(29)
        restarted.sleep(29)
        assert isinstance(gate.acquire("example.test"), Grant)
        gate.release("example.test", Classification(Outcome.OK))


def test_actual_client_midnight_latency_keeps_request_receipt_and_bytes_on_debit_day(
    fetched, monkeypatch
):
    from swingset.fetch.robots import RobotsPolicy

    f = fetched
    f.clock.current = f.clock.now().replace(hour=23, minute=59, second=59)
    day = f.clock.now().date().isoformat()
    fetcher = client(f, lambda _: httpx.Response(200, content=BODY))
    original = fetcher.gate.acquire

    def delayed_grant(*args, **kwargs):
        grant = original(*args, **kwargs)
        if isinstance(grant, Grant):
            f.clock.sleep(2)
        return grant

    monkeypatch.setattr(fetcher.gate, "acquire", delayed_grant)
    monkeypatch.setattr(fetcher.robots, "policy", lambda *_: RobotsPolicy(True))
    try:
        fetcher.fetch(f.spec.watch_id, EventsPage(), f.run)
    finally:
        fetcher.close()
    assert f.clock.now().date().isoformat() != day
    assert tuple(
        f.db.connection.execute("SELECT day,requests,bytes FROM host_budget").fetchone()
    ) == (day, 1, len(BODY))
    row = f.db.connection.execute(
        "SELECT day,issued_at,body_bytes FROM scheduler_requests"
    ).fetchone()
    assert row["day"] == day and row["issued_at"].startswith(day)
    assert row["body_bytes"] == len(BODY)


def test_budget_assessment_and_debit_use_one_instant_across_midnight(tmp_path):
    class JumpingClock(FakeClock):
        armed = False

        def now(self):
            result = super().now()
            if self.armed:
                self.armed = False
                self.current += timedelta(seconds=2)
            return result

    clock = JumpingClock()
    clock.current = clock.now().replace(hour=23, minute=59, second=40)
    with open_database(tmp_path) as db:
        gate = Gate(db.connection, policy(gap=5, budget=1), clock)
        assert isinstance(gate.acquire("example.test"), Grant)
        gate.release("example.test", Classification(Outcome.OK))
        clock.sleep(19)
        clock.armed = True
        assert gate.acquire("example.test") == Paused("request budget")
        assert db.connection.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == 1


def test_distinct_system_clocks_share_one_monotonic_wait_domain(tmp_path, monkeypatch):
    from swingset.clock import SystemClock

    elapsed = [0.0]
    monkeypatch.setattr(SystemClock, "monotonic", lambda _: elapsed[0])
    with open_database(tmp_path) as db:
        first = Gate(db.connection, policy(), SystemClock())
        second = Gate(db.connection, policy(), SystemClock())
        assert isinstance(first.acquire("example.test"), Grant)
        first.release("example.test", Classification(Outcome.OK))
        # Simulate a forward wall-clock change. The shared monotonic deadline
        # must survive alternating clients without restarting its wait.
        db.connection.execute("UPDATE hosts SET next_allowed_at=NULL")
        assert second.acquire("example.test") == Wait(10)
        elapsed[0] = 4
        assert first.acquire("example.test") == Wait(6)
        elapsed[0] = 10
        assert isinstance(second.acquire("example.test"), Grant)
        second.release("example.test", Classification(Outcome.OK))
