"""Request pauses drain through evidence retention and survive exceptional I/O."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch import client as client_module
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.fetch.robots import RobotsPolicy
from swingset.schedule.derive import derive_one
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.sources.wsdc_calendar import EventsPage
from swingset.state.controls import Selector, change_control, status
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

BODY = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()


@pytest.fixture
def fetched(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        spec = WatchSpec(
            "", "wsdc_calendar", "index", "GET", "https://example.test/events", EventsPage.kind
        )
        upsert_watch(db.connection, spec, clock.now())
        config = Config(
            {name: HostConfig(daily_request_budget=200) for name in ("example.test", "other.test")},
            {"wsdc_calendar": SourceConfig(True)},
        )
        yield SimpleNamespace(
            db=db, clock=clock, spec=spec, run=run, config=config, archive=Archive(tmp_path)
        )


def pause(fixture, kind, identifier):
    return change_control(
        fixture.db.state_dir,
        selector=Selector(kind, identifier),
        paused=True,
        actor="offline-test",
        reason="private control note",
        now=fixture.clock.now(),
        hosts=("example.test", "other.test"),
        timeout=1,
    )


def report(fixture):
    return status(fixture.db.connection, now=fixture.clock.now())


def client(fixture, handler, *, connection=None):
    return FetchClient(
        connection or fixture.db.connection,
        fixture.config,
        fixture.clock,
        fixture.archive,
        transport=httpx.MockTransport(handler),
        random_value=lambda: 0,
    )


def test_pause_in_http_callback_drains_until_snapshot_and_body_are_retained(fetched, monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        result = pause(fetched, "source", "wsdc_calendar")
        assert result["state"] == "pausing"
        assert {row["action_kind"] for row in result["draining_attempts"]} == {"fetch", "request"}
        return httpx.Response(200, content=BODY)

    original_store = fetched.archive.store_body
    retained = []

    def store(body):
        sha = original_store(body)
        if body == BODY:
            current = report(fetched)
            assert current["state"] == "pausing"
            assert [row["action_kind"] for row in current["draining_attempts"]] == ["fetch"]
            assert not fetched.db.connection.execute("SELECT 1 FROM snapshots").fetchone()
            retained.append(sha)
        return sha

    monkeypatch.setattr(fetched.archive, "store_body", store)
    fetcher = client(fetched, handler)
    try:
        result = fetcher.fetch(fetched.spec.watch_id, EventsPage(), fetched.run)
        assert result.snapshot_id and len(seen) == 2 and len(retained) == 1
        assert fetched.archive.read_body(retained[0]) == BODY
        assert report(fetched)["state"] == "paused"
        assert not report(fetched)["draining_attempts"] and not fetcher.gate.inflight
        assert fetcher.fetch(fetched.spec.watch_id, EventsPage(), fetched.run).skipped == "operator"
        assert len(seen) == 2
    finally:
        fetcher.close()


@pytest.mark.parametrize(
    ("kind", "identifier"),
    [
        ("all", "all"),
        ("source", "wsdc_calendar"),
        ("kind", "source_event_mapping"),
        ("host", "example.test"),
    ],
)
def test_matching_pause_prevents_robots_http_and_budget(fetched, kind, identifier):
    pause(fetched, kind, identifier)
    fetcher = client(fetched, lambda _: pytest.fail("paused action must issue no request"))
    try:
        assert fetcher.fetch(fetched.spec.watch_id, EventsPage(), fetched.run).skipped == "operator"
        assert not fetched.db.connection.execute("SELECT 1 FROM host_budget").fetchone()
        assert not fetched.db.connection.execute("SELECT 1 FROM execution_admissions").fetchone()
    finally:
        fetcher.close()


@pytest.mark.parametrize("target", ["example.test", "other.test"])
def test_pause_after_redirect_blocks_next_actual_host_request(fetched, target):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        pause(fetched, "host", target)
        return httpx.Response(
            302, headers={"location": f"https://{target}/next"}, content=b"redirect"
        )

    fetcher = client(fetched, handler)
    try:
        assert fetcher.fetch(fetched.spec.watch_id, EventsPage(), fetched.run).skipped == "operator"
        assert seen == ["https://example.test/robots.txt", "https://example.test/events"]
        assert fetched.db.connection.execute(
            "SELECT SUM(requests),SUM(bytes) FROM host_budget"
        ).fetchone()[:] == (2, 8)
        assert not fetched.db.connection.execute(
            "SELECT 1 FROM host_budget WHERE host='other.test'"
        ).fetchone()
        assert report(fetched)["state"] == "paused" and not fetcher.gate.inflight
    finally:
        fetcher.close()


def test_host_pause_is_scoped_and_does_not_gate_retained_parse(fetched):
    pause(fetched, "host", "other.test")
    fetcher = client(
        fetched,
        lambda request: (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=BODY)
        ),
    )
    try:
        result = fetcher.fetch(fetched.spec.watch_id, EventsPage(), fetched.run)
        assert result.snapshot_id
        pause(fetched, "host", "example.test")
        derived = derive_one(
            fetched.db,
            fetched.archive,
            WorkUnit("parse", "snapshot", result.snapshot_id),
            SimpleNamespace(),
            fetched.clock,
            fetched.run,
        )
        assert not derived.failed and derived.reason is None
        assert (
            fetched.db.connection.execute(
                "SELECT COUNT(*) FROM observations WHERE snapshot_id=?", (result.snapshot_id,)
            ).fetchone()[0]
            > 0
        )
        assert (
            fetched.db.connection.execute("SELECT SUM(requests) FROM host_budget").fetchone()[0]
            == 2
        )
    finally:
        fetcher.close()


@pytest.mark.parametrize("failure", ["transport", "classification"])
def test_unexpected_request_error_releases_admission_and_keeps_actual_debit(
    fetched, monkeypatch, failure
):
    def handler(_):
        if failure == "transport":
            raise RuntimeError("unexpected transport failure")
        return httpx.Response(200, content=BODY)

    fetcher = client(fetched, handler)
    monkeypatch.setattr(fetcher.robots, "policy", lambda *_: RobotsPolicy(True))
    if failure == "classification":
        monkeypatch.setattr(
            client_module,
            "classify",
            lambda *_: (_ for _ in ()).throw(ValueError("unexpected classification")),
        )
    try:
        with pytest.raises((RuntimeError, ValueError), match="unexpected"):
            fetcher.fetch(fetched.spec.watch_id, EventsPage(), fetched.run)
        assert not fetcher.gate.inflight
        assert not fetched.db.connection.execute(
            "SELECT 1 FROM execution_admissions WHERE state!='settled'"
        ).fetchone()
        assert fetched.db.connection.execute("SELECT requests,bytes FROM host_budget").fetchone()[
            :
        ] == (1, len(BODY) if failure == "classification" else 0)
        assert (
            fetched.db.connection.execute(
                "SELECT outcome FROM execution_admissions WHERE action_kind='request'"
            ).fetchone()[0]
            == "Invalid"
        )
    finally:
        fetcher.close()


@pytest.mark.parametrize("committed", [False, True])
def test_request_admission_commit_failure_cleans_up_without_http(fetched, monkeypatch, committed):
    class FailingCommit(sqlite3.Connection):
        fail = True

        def commit(self):
            if (
                self.fail
                and self.execute(
                    "SELECT 1 FROM execution_admissions WHERE action_kind='request' AND state='active'"
                ).fetchone()
            ):
                self.fail = False
                if committed:
                    super().commit()
                raise sqlite3.OperationalError("simulated admission commit failure")
            super().commit()

    conn = sqlite3.connect(
        fetched.db.state_dir / "state.sqlite", isolation_level=None, factory=FailingCommit
    )
    conn.row_factory = sqlite3.Row
    fetcher = client(
        fetched, lambda _: pytest.fail("commit failure must precede HTTP"), connection=conn
    )
    monkeypatch.setattr(fetcher.robots, "policy", lambda *_: RobotsPolicy(True))
    try:
        with pytest.raises(sqlite3.OperationalError, match="commit failure"):
            fetcher.fetch(fetched.spec.watch_id, EventsPage(), fetched.run)
        assert not fetcher.gate.inflight and not conn.in_transaction
        if committed:
            assert conn.execute("SELECT requests,bytes FROM host_budget").fetchone()[:] == (1, 0)
            assert (
                conn.execute(
                    "SELECT outcome FROM execution_admissions WHERE action_kind='request'"
                ).fetchone()[0]
                == "not_issued"
            )
        else:
            assert not conn.execute("SELECT 1 FROM host_budget").fetchone()
        assert not conn.execute(
            "SELECT 1 FROM execution_admissions WHERE state!='settled'"
        ).fetchone()
    finally:
        fetcher.close()
        conn.close()


def test_transport_retry_does_not_recharge_previous_response_bytes(fetched, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500, content=b"server error")
        raise httpx.ConnectError("offline", request=request)

    fetcher = client(fetched, handler)
    monkeypatch.setattr(fetcher.robots, "policy", lambda *_: RobotsPolicy(True))
    try:
        result = fetcher.fetch(fetched.spec.watch_id, EventsPage(), fetched.run)
        assert result.snapshot_id is None and len(calls) == 4
        assert fetched.db.connection.execute("SELECT requests,bytes FROM host_budget").fetchone()[
            :
        ] == (4, len(b"server error"))
        assert not fetcher.gate.inflight
        assert not fetched.db.connection.execute(
            "SELECT 1 FROM execution_admissions WHERE state!='settled'"
        ).fetchone()
    finally:
        fetcher.close()
