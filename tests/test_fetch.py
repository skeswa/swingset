import gzip
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.classify import Classification, Outcome, classify, retry_after
from swingset.fetch.client import USER_AGENT, FetchClient
from swingset.fetch.politeness import Gate, Grant, Paused, Wait
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.sources.wdr import RoundsPage
from swingset.sources.wsdc_calendar import EventsPage
from swingset.state.db import open_database

BODY = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()


def config(host="example.test", budget=200):
    return Config(
        {host: HostConfig(daily_request_budget=budget)}, {"wsdc_calendar": SourceConfig(True)}
    )


def test_classify_expected_403_is_watch_local():
    page = RoundsPage()
    response = httpx.Response(403)
    now = FakeClock().now()
    assert (
        classify(response, page, SimpleNamespace(ever_ok=0), now).outcome
        == Outcome.EXPECTED_UNAVAILABLE
    )
    assert classify(response, page, SimpleNamespace(ever_ok=1), now).outcome == Outcome.BLOCKED
    assert (
        classify(
            httpx.Response(200, text="Just a moment cf-chl"), page, SimpleNamespace(ever_ok=0), now
        ).outcome
        == Outcome.BLOCKED
    )


def test_retry_after_seconds_and_http_date():
    now = FakeClock().now()
    assert retry_after("5", now) == 60
    assert retry_after("120", now) == 120
    assert retry_after("Thu, 01 Jan 2026 00:02:00 GMT", now) == 120
    assert retry_after("garbage", now) is None


def test_gate_gap_budget_and_pause_doubling(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        gate = Gate(db.connection, config(budget=3), clock)
        assert isinstance(gate.acquire("example.test"), Grant)
        gate.release("example.test", Classification(Outcome.OK))
        assert gate.acquire("example.test") == Wait(5)
        clock.sleep(5)
        assert isinstance(gate.acquire("example.test"), Grant)
        gate.release("example.test", Classification(Outcome.THROTTLED))
        assert gate.acquire("example.test") == Paused("Throttled")
        clock.sleep(900)
        assert isinstance(gate.acquire("example.test"), Grant)
        gate.release("example.test", Classification(Outcome.THROTTLED))
        row = db.connection.execute("SELECT paused_until FROM hosts").fetchone()
        assert row[0] == (clock.now() + timedelta(seconds=1800)).isoformat()
        clock.sleep(1800)
        assert gate.acquire("example.test") == Paused("request budget")


def test_fetch_conditional_no_cookies_archive_and_fingerprint(tmp_path):
    clock = FakeClock()
    requests = []

    def handler(request):
        requests.append((clock.now(), request))
        assert request.headers["user-agent"] == USER_AGENT
        assert request.headers["accept-encoding"] == "gzip"
        assert "cookie" not in request.headers
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nCrawl-delay: 7\n", headers={"set-cookie": "secret=value"}
            )
        return httpx.Response(200, content=BODY, headers={"ETag": '"one"'})

    with open_database(tmp_path) as db:
        run = db.start_run(clock.now(), dry_run=True)
        upsert_watch(
            db.connection,
            WatchSpec(
                "w1",
                "wsdc_calendar",
                "index",
                "GET",
                "https://example.test/events",
                "wsdc_calendar.events",
            ),
            clock.now(),
        )
        client = FetchClient(
            db.connection,
            config(),
            clock,
            Archive(tmp_path),
            transport=httpx.MockTransport(handler),
        )
        watch_id = db.connection.execute("SELECT watch_id FROM watches").fetchone()[0]
        first = client.fetch(watch_id, EventsPage(), run)
        second = client.fetch(watch_id, EventsPage(), run)
        assert first.changed and not second.changed
        assert len(requests) == 3
        assert (requests[2][0] - requests[1][0]).total_seconds() >= 7
        assert requests[2][1].headers["if-none-match"] == '"one"'
        assert db.connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] == 1
        assert (
            Archive(tmp_path).read_body(
                db.connection.execute("SELECT body_sha256 FROM snapshots").fetchone()[0]
            )
            == BODY
        )
        client.close()


def test_robots_specific_group_wins(tmp_path):
    clock = FakeClock()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200, text="User-agent: *\nAllow: /\n\nUser-agent: swingset\nDisallow: /\n"
        )

    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        upsert_watch(
            db.connection,
            WatchSpec(
                "w",
                "wsdc_calendar",
                "index",
                "GET",
                "https://example.test/events",
                "wsdc_calendar.events",
            ),
            clock.now(),
        )
        client = FetchClient(
            db.connection,
            config(),
            clock,
            Archive(tmp_path),
            transport=httpx.MockTransport(handler),
        )
        assert (
            client.fetch(
                db.connection.execute("SELECT watch_id FROM watches").fetchone()[0],
                EventsPage(),
                run,
            ).skipped
            == "robots disallow"
        )
        assert calls == ["/robots.txt"]
        client.close()


def test_archive_deduplicates_and_detects_corruption(tmp_path):
    archive = Archive(tmp_path)
    sha = archive.store_body(b"abc")
    assert archive.store_body(b"abc") == sha
    assert archive.read_body(sha) == b"abc"
    assert len(list((tmp_path / "blobs").rglob(sha))) == 1
    with pytest.raises(ValueError):
        archive.read_body("../secret")


@pytest.mark.parametrize(
    "status,outcome",
    [
        (304, Outcome.NOT_MODIFIED),
        (404, Outcome.GONE),
        (429, Outcome.THROTTLED),
        (500, Outcome.SERVER_ERROR),
        (302, Outcome.REDIRECT),
    ],
)
def test_status_classification(status, outcome):
    assert (
        classify(httpx.Response(status), EventsPage(), object(), FakeClock().now()).outcome
        == outcome
    )


def test_expected_unavailable_does_not_pause_other_watch(tmp_path):
    from swingset.sources.wdr import RoundsPage

    clock = FakeClock()
    fixture = Path("tests/fixtures/sources/synthetic_wdr_rounds.json").read_bytes()
    block_good = [False]

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(403)
        if "unpublished" in request.url.path or block_good[0]:
            return httpx.Response(403)
        return httpx.Response(200, content=fixture)

    cfg = Config({"example.test": HostConfig()}, {"wdr": SourceConfig(True)})
    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        specs = [
            WatchSpec(
                "",
                "wdr",
                "event",
                "GET",
                f"https://example.test/{name}/rounds/routeInfo.json",
                "wdr.rounds",
            )
            for name in ("unpublished", "good")
        ]
        for spec in specs:
            upsert_watch(db.connection, spec, clock.now())
        client = FetchClient(
            db.connection, cfg, clock, Archive(tmp_path), transport=httpx.MockTransport(handler)
        )
        assert (
            client.fetch(specs[0].watch_id, RoundsPage(), run).classification.outcome
            == Outcome.EXPECTED_UNAVAILABLE
        )
        assert (
            client.fetch(specs[1].watch_id, RoundsPage(), run).classification.outcome == Outcome.OK
        )
        assert db.connection.execute("SELECT paused_until FROM hosts").fetchone()[0] is None
        block_good[0] = True
        assert (
            client.fetch(specs[1].watch_id, RoundsPage(), run).classification.outcome
            == Outcome.BLOCKED
        )
        assert db.connection.execute("SELECT pause_reason FROM hosts").fetchone()[0] == "Blocked"
        client.close()


def test_three_retries_use_gate_and_archive_success(tmp_path):
    clock = FakeClock()
    attempts = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts.append(clock.now())
        if len(attempts) < 4:
            return httpx.Response(500)
        return httpx.Response(200, content=BODY)

    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        spec = WatchSpec(
            "",
            "wsdc_calendar",
            "index",
            "GET",
            "https://example.test/events",
            "wsdc_calendar.events",
        )
        upsert_watch(db.connection, spec, clock.now())
        client = FetchClient(
            db.connection,
            config(),
            clock,
            Archive(tmp_path),
            transport=httpx.MockTransport(handler),
            random_value=lambda: 0.5,
        )
        result = client.fetch(spec.watch_id, EventsPage(), run)
        assert result.changed
        assert len(attempts) == 4
        assert [(b - a).total_seconds() for a, b in zip(attempts, attempts[1:], strict=False)] == [
            5,
            10,
            20,
        ]
        assert db.connection.execute("SELECT requests FROM host_budget").fetchone()[0] == 5
        client.close()


def test_redirect_destinations_have_their_own_robots_and_gate(tmp_path):
    clock = FakeClock()
    calls = []

    def handler(request):
        calls.append((request.url.host, request.url.path))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "example.test":
            return httpx.Response(302, headers={"Location": "https://other.test/events"})
        return httpx.Response(200, content=BODY)

    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        spec = WatchSpec(
            "",
            "wsdc_calendar",
            "index",
            "GET",
            "https://example.test/events",
            "wsdc_calendar.events",
        )
        upsert_watch(db.connection, spec, clock.now())
        client = FetchClient(
            db.connection,
            config(),
            clock,
            Archive(tmp_path),
            transport=httpx.MockTransport(handler),
        )
        assert client.fetch(spec.watch_id, EventsPage(), run).changed
        assert calls == [
            ("example.test", "/robots.txt"),
            ("example.test", "/events"),
            ("other.test", "/robots.txt"),
            ("other.test", "/events"),
        ]
        assert len(db.connection.execute("SELECT * FROM host_budget").fetchall()) == 2
        client.close()


def test_stop_after_completed_5xx_records_terminal_response(tmp_path):
    clock = FakeClock()
    stopped = [False]

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        stopped[0] = True
        return httpx.Response(500, content=b"server failed")

    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        spec = WatchSpec(
            "",
            "wsdc_calendar",
            "index",
            "GET",
            "https://example.test/events",
            "wsdc_calendar.events",
        )
        upsert_watch(db.connection, spec, clock.now())
        client = FetchClient(
            db.connection,
            config(),
            clock,
            Archive(tmp_path),
            transport=httpx.MockTransport(handler),
            should_stop=lambda: stopped[0],
        )
        result = client.fetch(spec.watch_id, EventsPage(), run)
        assert result.skipped is None
        assert result.classification.outcome == Outcome.SERVER_ERROR
        snapshot = db.connection.execute(
            "SELECT classification,body_bytes FROM snapshots"
        ).fetchone()
        assert tuple(snapshot) == ("ServerError", len(b"server failed"))
        assert (
            db.connection.execute("SELECT pause_reason FROM hosts").fetchone()[0] == "ServerError"
        )
        client.close()


def test_deferred_robots_request_is_not_cached_as_failure(tmp_path):
    clock = FakeClock()
    cfg = config(budget=0)
    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        spec = WatchSpec(
            "",
            "wsdc_calendar",
            "index",
            "GET",
            "https://example.test/events",
            "wsdc_calendar.events",
        )
        upsert_watch(db.connection, spec, clock.now())
        client = FetchClient(
            db.connection,
            cfg,
            clock,
            Archive(tmp_path),
            transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
        )
        result = client.fetch(spec.watch_id, EventsPage(), run)
        assert result.skipped == "request budget"
        assert tuple(
            db.connection.execute("SELECT robots_fetched_at,robots_status FROM hosts").fetchone()
        ) == (None, None)
        client.close()


@pytest.mark.parametrize("encoding", ["gzip", "identity"])
def test_gzip_and_identity_bodies_archive_decoded_source_bytes(tmp_path, encoding):
    clock = FakeClock()

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = gzip.compress(BODY) if encoding == "gzip" else BODY
        return httpx.Response(200, content=body, headers={"Content-Encoding": encoding})

    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        spec = WatchSpec(
            "",
            "wsdc_calendar",
            "index",
            "GET",
            "https://example.test/events",
            "wsdc_calendar.events",
        )
        upsert_watch(db.connection, spec, clock.now())
        client = FetchClient(
            db.connection,
            config(),
            clock,
            Archive(tmp_path),
            transport=httpx.MockTransport(handler),
        )
        result = client.fetch(spec.watch_id, EventsPage(), run)
        body_hash = db.connection.execute(
            "SELECT body_sha256 FROM snapshots WHERE snapshot_id=?", (result.snapshot_id,)
        ).fetchone()[0]
        assert Archive(tmp_path).read_body(body_hash) == BODY
        client.close()
