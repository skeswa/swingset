"""Explicit filtered execution uses only fake Archive HTTP through the real gate."""

import gzip
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from test_event_site_intake import package
from test_history_event_sites import PDF, SITE
from test_history_event_sites import sites as sites
from test_platform_backfill import fixture as fixture

from swingset.config import HostConfig, SourceConfig
from swingset.fetch.archive import Archive, canonical, digest
from swingset.fetch.client import FetchClient
from swingset.history.event_site_intake import ingest
from swingset.history.event_site_queries import execute
from swingset.history.event_sites import retained_hits
from swingset.state.controls import Selector, change_control


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        yield from self.chunks

    def close(self):
        self.closed = True


def client(f, handler, *, enabled=True, budget=200):
    config = replace(
        f.config,
        hosts={
            **f.config.hosts,
            "web.archive.org": HostConfig(min_gap_seconds=10, daily_request_budget=budget),
        },
        sources={**f.config.sources, "event_sites": SourceConfig(enabled)},
    )
    return FetchClient(
        f.conn,
        config,
        f.clock,
        Archive(f.db.state_dir),
        transport=httpx.MockTransport(handler),
        random_value=lambda: 0,
    )


def body(request, *, pages=1):
    params = parse_qs(request.url.query.decode())
    if "showNumPages" in params:
        return canonical(pages)
    mime = "application/pdf" if "mimetype:application/pdf" in params["filter"] else "text/html"
    url = PDF if mime == "application/pdf" else "https://dance.example/festival/2019-results.html"
    return canonical(
        [
            ["timestamp", "original", "digest", "mimetype", "length"],
            ["20190601000000", url, "A", mime, "100"],
        ]
    )


def run(f, c, **kwargs):
    return execute(
        c, SITE, year=2019, mime="application/pdf", run_id=f.run, execute_requests=True, **kwargs
    )


def test_disabled_and_dry_run_never_issue_or_create_intent(sites):
    def forbidden(request):
        raise AssertionError("no HTTP")

    with_client = client(sites, forbidden, enabled=False)
    assert run(sites, with_client).skipped == "event_sites disabled"
    with_client.close()
    with_client = client(sites, forbidden)
    assert execute(
        with_client, SITE, year=2019, mime="application/pdf", run_id=sites.run
    ).skipped.startswith("dry run")
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
    assert not (sites.db.state_dir / "history" / "event-site-queries").exists()
    with_client.close()


def test_filtered_query_reuses_receipts_and_charges_old_work_only(sites):
    requests = []

    def respond(request):
        requests.append((sites.clock.now(), str(request.url)))
        assert request.url.host == "web.archive.org"
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=body(request))
        )

    c = client(sites, respond)
    before = {
        table: [tuple(r) for r in sites.conn.execute("SELECT * FROM " + table)]
        for table in ("watches", "observations", "admission_policies", "history_acceptance")
    }
    try:
        result = run(sites, c)
        assert result.complete and result.admitted_attempts == 3
        assert len(requests) == 3 and all(
            b[0] - a[0] >= timedelta(seconds=10)
            for a, b in zip(requests, requests[1:], strict=False)
        )
        again = run(sites, c)
        assert again.complete and again.admitted_attempts == 0 and len(requests) == 3
        assert {
            tuple(r) for r in sites.conn.execute("SELECT category,host FROM scheduler_requests")
        } == {("old", "web.archive.org")}
        assert sites.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 3
        assert len(retained_hits(sites.conn, c.archive, SITE)) == 1
        for table, rows in before.items():
            assert [tuple(r) for r in sites.conn.execute("SELECT * FROM " + table)] == rows
    finally:
        c.close()


def test_partial_query_resumes_without_repeating_probe_or_successful_pages(sites):
    urls = []

    def respond(request):
        urls.append(str(request.url))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=body(request, pages=2))
        )

    c = client(sites, respond)
    try:
        first = run(sites, c, max_attempts=3)
        assert not first.complete and first.page_receipts == 1 and first.admitted_attempts == 3
        second = run(sites, c)
        assert second.complete and second.admitted_attempts == 1
        assert len(urls) == 4 and sum("showNumPages" in url for url in urls) == 1
        assert sum("page=0" in url for url in urls) == 1
        assert (
            sites.conn.execute(
                "SELECT COUNT(*) FROM archive_captures WHERE source='event_sites'"
            ).fetchone()[0]
            == 1
        )
        assert len(retained_hits(sites.conn, c.archive, SITE)) == 2
    finally:
        c.close()


@pytest.mark.parametrize("mutation", ["pause", "year", "website"])
def test_change_during_robots_prevents_cdx_debit(sites, mutation):
    seen = []

    def respond(request):
        seen.append(str(request.url))
        assert request.url.path == "/robots.txt"
        if mutation == "pause":
            change_control(
                sites.db.state_dir,
                selector=Selector("kind", "source_event_mapping"),
                paused=True,
                actor="test",
                reason="pause while active",
                now=sites.clock.now(),
            )
        elif mutation == "year":
            sites.conn.execute("DELETE FROM history_acceptance")
        else:
            sites.conn.execute(
                "UPDATE events SET website='https://changed.example/' WHERE event_id=?",
                (SITE.event_id,),
            )
        return httpx.Response(404)

    c = client(sites, respond)
    try:
        result = run(sites, c)
        assert not result.complete and result.admitted_attempts == 1 and len(seen) == 1
        assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
        assert sites.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 1
    finally:
        c.close()


def test_accepted_year_changes_inside_admission_before_debit(sites, monkeypatch):
    import swingset.fetch.controls as controls

    original = controls.admission
    from contextlib import contextmanager

    @contextmanager
    def changed(*args, **kwargs):
        sites.conn.execute("DELETE FROM history_acceptance")
        with original(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(controls, "admission", changed)
    c = client(sites, lambda req: (_ for _ in ()).throw(AssertionError("no HTTP")))
    try:
        result = run(sites, c)
        assert result.admitted_attempts == 0
        assert sites.conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0
    finally:
        c.close()


@pytest.mark.parametrize(
    "target",
    [
        "https://dance.example/festival/results.pdf",
        "https://web.archive.org/web/20190101000000id_/https://dance.example/",
        "https://web.archive.org:444/cdx/search/cdx",
        "https://[broken/",
    ],
)
def test_redirect_scope_cannot_fetch_original_site_or_other_archive_resource(sites, target):
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(302, headers={"location": target})
        )

    c = client(sites, respond)
    try:
        result = run(sites, c)
        assert not result.complete and result.admitted_attempts == 2 and len(seen) == 2
        assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
    finally:
        c.close()


def test_retry_attempts_share_the_same_ceiling_and_failed_receipts_are_diagnostic(sites):
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(500, content=b"error")
        )

    c = client(sites, respond)
    try:
        result = run(sites, c, max_attempts=3)
        assert result.admitted_attempts == 3 and len(seen) == 3 and not result.complete
        assert sites.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 3
        assert (
            len(
                list(
                    (sites.db.state_dir / "archive-cdx" / result.query_id / "diagnostics").glob(
                        "*.json"
                    )
                )
            )
            == 2
        )
        assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
    finally:
        c.close()


@pytest.mark.parametrize("encoding", ["gzip-bomb", "gzip-trailing", "raw-large"])
def test_streaming_limits_never_select_partial_success(sites, encoding):
    from swingset.history.event_sites import MAX_BODY_BYTES

    stream = None

    def respond(request):
        nonlocal stream
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if encoding == "gzip-bomb":
            data = gzip.compress(b" " * (MAX_BODY_BYTES + 1))
        elif encoding == "gzip-trailing":
            data = gzip.compress(b"0") + gzip.compress(b"999")
        else:
            data = b"x" * (MAX_BODY_BYTES + 1)
        stream = Chunks([data])
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"} if encoding.startswith("gzip") else {},
            stream=stream,
        )

    c = client(sites, respond)
    try:
        result = run(sites, c)
        assert not result.complete and result.admitted_attempts == 2
        assert stream.closed and not c.gate.inflight
        assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
        diagnostics = list(
            (sites.db.state_dir / "archive-cdx" / result.query_id / "diagnostics").glob("*.json")
        )
        assert (
            len(diagnostics) == 1 and json.loads(diagnostics[0].read_bytes())["complete"] is False
        )
    finally:
        c.close()


def test_completed_receipt_survives_metadata_crash_without_new_http(sites, monkeypatch):
    import swingset.history.event_site_queries as queries

    original = queries.ingest
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=body(request))
        )

    c = client(sites, respond)
    try:

        def crash(*args, **kwargs):
            raise RuntimeError("simulated crash before metadata")

        monkeypatch.setattr(queries, "ingest", crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            run(sites, c)
        assert (
            len(seen) == 3
            and sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
        )
        monkeypatch.setattr(queries, "ingest", original)
        result = run(sites, c)
        assert result.complete and result.admitted_attempts == 0 and len(seen) == 3
    finally:
        c.close()


def test_explicit_failed_receipt_is_never_admitted_by_local_import(sites, tmp_path):
    root = tmp_path / "package"
    sha, identifier = package(sites, root)
    path = root / "archive-cdx" / identifier / "0.json"
    value = json.loads(path.read_bytes())
    value.update(complete=False, failure_reason="truncated but parseable prefix")
    path.write_bytes(canonical(value))
    manifest = json.loads((root / "event-site-intake.json").read_bytes())
    manifest["queries"][0]["receipts"][1]["sha256"] = digest(path.read_bytes())
    raw = canonical(manifest)
    (root / "event-site-intake.json").write_bytes(raw)
    with pytest.raises(ValueError, match="identity or status"):
        ingest(
            sites.db,
            SITE,
            directory=root,
            manifest_sha256=digest(raw),
            now=sites.clock.now(),
            dry_run=False,
        )
    assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0


def test_current_previous_query_refresh_keeps_distinct_recipe_and_provenance(sites):
    sites.clock.current = datetime(2020, 1, 1, tzinfo=UTC)
    c = client(
        sites,
        lambda request: (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=body(request))
        ),
    )
    try:
        first = run(sites, c)
        assert first.complete
        sites.clock.sleep(91 * 86400)
        second = run(sites, c)
        assert second.complete and first.query_id != second.query_id
        assert {h.query_id for h in retained_hits(sites.conn, c.archive, SITE)} == {
            first.query_id,
            second.query_id,
        }
    finally:
        c.close()


def test_exhausted_budget_resumes_on_next_day_without_extra_capacity(sites):
    seen = []

    def respond(request):
        seen.append((sites.clock.now(), str(request.url)))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=body(request))
        )

    c = client(sites, respond, budget=2)
    try:
        first = run(sites, c)
        assert not first.complete and first.admitted_attempts == 2 and first.page_receipts == 0
        again = run(sites, c)
        assert again.admitted_attempts == 0 and len(seen) == 2
        sites.clock.sleep(86400)
        last = run(sites, c)
        assert last.complete and last.admitted_attempts == 2
        assert sum("showNumPages" in url for _, url in seen) == 1
        assert [
            r[0] for r in sites.conn.execute("SELECT requests FROM host_budget ORDER BY day")
        ] == [2, 2]
        assert all(
            b[0] - a[0] >= timedelta(seconds=10) for a, b in zip(seen, seen[1:], strict=False)
        )
    finally:
        c.close()


def test_pause_during_polite_wait_blocks_next_debit(sites, monkeypatch):
    seen = []

    def respond(request):
        seen.append(str(request.url))
        assert request.url.path == "/robots.txt"
        return httpx.Response(404)

    original = sites.clock.sleep

    def wait(seconds):
        change_control(
            sites.db.state_dir,
            selector=Selector("source", "event_sites"),
            paused=True,
            actor="test",
            reason="pause while waiting",
            now=sites.clock.now(),
            sources=("event_sites",),
        )
        original(seconds)

    monkeypatch.setattr(sites.clock, "sleep", wait)
    c = client(sites, respond)
    try:
        result = run(sites, c)
        assert result.admitted_attempts == 1 and len(seen) == 1
        assert sites.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 1
    finally:
        c.close()


def test_backpressure_cannot_use_repair_reserve_for_event_site_query(sites):
    c = client(sites, lambda request: (_ for _ in ()).throw(AssertionError("no HTTP")))
    c.config = replace(c.config, scheduler=replace(c.config.scheduler, pending_work_items=1))
    c.gate.config = c.config
    from swingset.state.work import WorkUnit, enqueue

    enqueue(
        sites.conn,
        [WorkUnit("link", "event", SITE.event_id)],
        enqueued_at=sites.clock.now().isoformat(),
    )
    try:
        assert sites.conn.execute("SELECT COUNT(*) FROM pending_work").fetchone()[0] > 0
        result = run(sites, c)
        assert result.admitted_attempts == 0 and "backpressure" in result.skipped
        assert sites.conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0
        assert sites.conn.execute("SELECT COUNT(*) FROM scheduler_requests").fetchone()[0] == 0
    finally:
        c.close()


@pytest.mark.parametrize(
    "selector",
    [("source", "event_sites"), ("kind", "source_event_mapping"), ("host", "web.archive.org")],
)
def test_scoped_pause_before_execution_issues_nothing(sites, selector):
    change_control(
        sites.db.state_dir,
        selector=Selector(*selector),
        paused=True,
        actor="test",
        reason="paused",
        now=sites.clock.now(),
        sources=("event_sites",),
        hosts=("web.archive.org",),
    )
    c = client(sites, lambda request: (_ for _ in ()).throw(AssertionError("no HTTP")))
    try:
        result = run(sites, c)
        assert result.admitted_attempts == 0 and not result.complete
        assert sites.conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0
    finally:
        c.close()


def test_pdf_and_html_queries_never_mix_recipe_or_resume_cursor(sites):
    c = client(
        sites,
        lambda request: (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=body(request))
        ),
    )
    try:
        pdf = run(sites, c)
        html = execute(
            c, SITE, year=2019, mime="text/html", run_id=sites.run, execute_requests=True
        )
        assert pdf.complete and html.complete and pdf.query_id != html.query_id
        hits = retained_hits(sites.conn, c.archive, SITE)
        assert {h.mimetype for h in hits} == {"application/pdf", "text/html"}
        assert (
            sites.conn.execute(
                "SELECT COUNT(*) FROM archive_queries WHERE source!='event_sites'"
            ).fetchone()[0]
            == 0
        )
    finally:
        c.close()


def test_failed_page_diagnostic_does_not_prevent_a_later_successful_retry(sites):
    failing = True
    seen = []

    def respond(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if failing and "page=" in str(request.url):
            return httpx.Response(200, content=b"invalid CDX array")
        return httpx.Response(200, content=body(request))

    c = client(sites, respond)
    try:
        first = run(sites, c)
        assert not first.complete and first.page_receipts == 0
        assert not (sites.db.state_dir / "archive-cdx" / first.query_id / "0.json").exists()
        failing = False
        second = run(sites, c)
        assert second.complete and second.admitted_attempts == 1
        assert sum("showNumPages" in url for url in seen) == 1
        assert (
            len(
                list(
                    (sites.db.state_dir / "archive-cdx" / first.query_id / "diagnostics").glob(
                        "*.json"
                    )
                )
            )
            == 1
        )
    finally:
        c.close()


def test_lost_admission_ack_counts_charged_attempt_without_http(sites, monkeypatch):
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace

    import swingset.fetch.controls as controls
    from swingset.fetch.limits import RequestContext
    from swingset.fetch.politeness import Gate

    original = controls.admission

    @contextmanager
    def lost(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield conn
        raise RuntimeError("lost commit acknowledgment")

    monkeypatch.setattr(controls, "admission", lost)
    context = RequestContext(
        lambda conn, url: None, sites.clock.now() + timedelta(seconds=60), time.monotonic() + 60
    )
    gate = Gate(sites.conn, sites.config, sites.clock)
    with pytest.raises(RuntimeError, match="lost commit"):
        controls.issue(
            sites.db,
            gate,
            sites.clock,
            host="web.archive.org",
            source="eepro",
            watch=SimpleNamespace(kind="index"),
            page_kind="wayback.cdx",
            crawl_delay=0,
            sweep=False,
            request_url="https://web.archive.org/cdx/search/cdx",
            context=context,
        )
    assert context.admitted_attempts == context.uncertain_admissions == 1
    assert sites.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 1
    assert not gate.inflight


def test_unfiltered_entrypoint_cannot_use_filtered_namespace(sites):
    c = client(sites, lambda request: (_ for _ in ()).throw(AssertionError("no HTTP")))
    try:
        with pytest.raises(ValueError, match="filtered recipe"):
            c.index_archive(source="event_sites", prefix=SITE.prefix, year=2019)
        assert sites.conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0
    finally:
        c.close()


def test_valid_gzip_retains_decoded_body_and_original_headers(sites):
    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip", "content-type": "application/json"},
            stream=Chunks([gzip.compress(body(request))]),
        )

    c = client(sites, respond)
    try:
        result = run(sites, c)
        assert result.complete and result.wire_bytes > 0 and result.decoded_bytes > 0
        receipt = json.loads(
            (sites.db.state_dir / "archive-cdx" / result.query_id / "0.json").read_bytes()
        )
        assert receipt["headers"]["content-encoding"] == "gzip"
        assert json.loads(c.archive.read_body(receipt["body_sha256"]))[0][0] == "timestamp"
    finally:
        c.close()


def test_aggregate_stream_byte_limit_stops_further_admission(sites):
    import time
    from types import SimpleNamespace

    from swingset.fetch.cdx import CDXPage
    from swingset.fetch.limits import RequestContext

    c = client(sites, lambda request: httpx.Response(200, stream=Chunks([b"x" * 80])))
    context = RequestContext(
        lambda conn, url: None,
        sites.clock.now() + timedelta(seconds=60),
        time.monotonic() + 60,
        max_response_bytes=100,
        max_total_bytes=120,
    )
    try:
        kwargs = {"context": context, "robots": True}
        first = c._request(
            "GET",
            "https://web.archive.org/cdx/search/cdx",
            "event_sites",
            CDXPage(),
            SimpleNamespace(ever_ok=False),
            {},
            None,
            **kwargs,
        )
        second = c._request(
            "GET",
            "https://web.archive.org/cdx/search/cdx",
            "event_sites",
            CDXPage(),
            SimpleNamespace(ever_ok=False),
            {},
            None,
            **kwargs,
        )
        third = c._request(
            "GET",
            "https://web.archive.org/cdx/search/cdx",
            "event_sites",
            CDXPage(),
            SimpleNamespace(ever_ok=False),
            {},
            None,
            **kwargs,
        )
        assert first[2] is None and "byte ceiling" in second[2] and "byte ceiling" in third[2]
        assert context.admitted_attempts == 2 and context.wire_bytes == 160
        assert sites.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 2
        assert not c.gate.inflight
    finally:
        c.close()


@pytest.mark.parametrize("caller_seconds", [None, 3])
def test_each_dripping_raw_chunk_checks_earliest_deadline(sites, monkeypatch, caller_seconds):
    import swingset.fetch.limits as limits

    elapsed = [100.0]
    delivered = []
    closed = []
    monkeypatch.setattr(limits.time, "monotonic", lambda: elapsed[0])

    class Drip(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                elapsed[0] += 2
                delivered.append(b"x")
                yield b"x"

        def close(self):
            closed.append(True)

    seen = []

    def respond(request):
        seen.append(str(request.url))
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, stream=Drip())

    c = client(sites, respond)
    try:
        deadline = sites.clock.now() + timedelta(seconds=caller_seconds) if caller_seconds else None
        result = run(sites, c, wall_seconds=60, deadline=deadline)
        assert len(delivered) == (2 if caller_seconds else 30)
        assert closed and len(seen) == 1
        assert not result.complete and result.admitted_attempts == 1
        assert result.wire_bytes == len(delivered)
        assert sites.conn.execute("SELECT COUNT(*) FROM archive_queries").fetchone()[0] == 0
        assert not c.gate.inflight
    finally:
        c.close()
