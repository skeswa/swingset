"""Event-site review uses exact retained CDX evidence; it never acquires links."""

import csv
import io
import json
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from test_platform_backfill import EVENT, accept
from test_platform_backfill import fixture as fixture

from swingset.fetch.archive import Archive, canonical, digest, durable_write
from swingset.history.event_sites import Site, proposals, query_urls, reconcile, retained_hits

SITE = Site(EVENT.event_id, 2019, "https://dance.example/festival/")
PDF = "https://dance.example/festival/2019-finals.pdf"


@pytest.fixture
def sites(fixture):
    fixture.conn.execute("UPDATE events SET website=?", (SITE.website,))
    accept(fixture)
    return fixture


def retained(f, *, year=2019, mime="application/pdf", urls=(PDF,), bad_filter=False):
    query_url = next(
        url
        for url in query_urls(SITE)
        if parse_qs(urlsplit(url).query)["from"] == [str(year)]
        and "mimetype:" + mime in parse_qs(urlsplit(url).query)["filter"]
    )
    if bad_filter:
        query_url = query_url.replace("mimetype%3Aapplication%2Fpdf", "mimetype%3Atext%2Fplain")
    identifier = digest((query_url + repr(urls)).encode())
    body = canonical(
        [
            ["timestamp", "original", "digest", "mimetype", "length"],
            *[
                [f"{year}0601000000", url, f"digest-{index}", mime, "100"]
                for index, url in enumerate(urls)
            ],
        ]
    )
    archive = Archive(f.db.state_dir)
    body_sha = archive.store_body(body)
    receipt = {
        "query_id": identifier,
        "page": "0",
        "url": query_url,
        "fetched_at": f.clock.now().isoformat(),
        "http_status": 200,
        "headers": {},
        "body_sha256": body_sha,
    }
    durable_write(f.db.state_dir / "archive-cdx" / identifier / "0.json", canonical(receipt))
    f.conn.execute(
        "INSERT INTO archive_queries VALUES (?,?,?, ?,1,1,?,?)",
        (
            identifier,
            "event_sites",
            SITE.prefix,
            year,
            f.clock.now().isoformat(),
            f.clock.now().isoformat(),
        ),
    )
    f.conn.executemany(
        "INSERT INTO archive_captures VALUES (?,?,?,?,200,?,?,?,?)",
        [
            (
                "event_sites",
                url,
                f"{year}0601000000",
                f"digest-{index}",
                mime,
                100,
                f.clock.now().isoformat(),
                identifier,
            )
            for index, url in enumerate(urls)
        ],
    )
    return identifier, body_sha


def test_query_specs_are_site_scoped_filtered_and_only_event_year_plus_next():
    urls = query_urls(SITE)
    assert len(urls) == 4
    params = [parse_qs(urlsplit(url).query) for url in urls]
    assert {tuple(row["from"]) for row in params} == {("2019",), ("2020",)}
    assert all(row["from"] == row["to"] and row["url"] == [SITE.prefix] for row in params)
    assert all("statuscode:200" in row["filter"] for row in params)
    assert all(
        any(value.startswith("original:") for value in row["filter"])
        for row in params
        if "mimetype:text/html" in row["filter"]
    )
    assert not query_urls(replace(SITE, year=2009))
    assert not query_urls(replace(SITE, website="https://untrusted@dance.example/"))


def test_verified_pdf_becomes_draft_csv_with_exact_binding_and_no_controls(sites):
    retained(sites)
    before_watches = [tuple(row) for row in sites.conn.execute("SELECT * FROM watches")]
    assert reconcile(sites.db, now=sites.clock.now(), run_id=sites.run) == 1
    row = sites.conn.execute(
        "SELECT * FROM findings WHERE kind='history_event_site_review' AND closed_at IS NULL"
    ).fetchone()
    assert row["subject_id"] == SITE.event_id and "parser unavailable" in row["summary"]
    suggestion = next(csv.reader(io.StringIO(row["suggested_override"])))
    assert suggestion[:5] == [SITE.event_id, "generic", "round", PDF, "generic.pdf_table"]
    evidence = json.loads(row["evidence_json"])
    assert evidence["candidate_event_ids"] == [SITE.event_id]
    assert len(evidence["captures"]) == 1 and evidence["captures"][0]["receipt_sha256"]
    assert [tuple(row) for row in sites.conn.execute("SELECT * FROM watches")] == before_watches
    assert sites.conn.execute("SELECT COUNT(*) FROM history_origin_intents").fetchone()[0] == 0
    assert sites.conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0
    before = tuple(row)
    reconcile(sites.db, now=sites.clock.now(), run_id=sites.run)
    assert (
        tuple(
            sites.conn.execute(
                "SELECT * FROM findings WHERE kind='history_event_site_review' AND closed_at IS NULL"
            ).fetchone()
        )
        == before
    )


def test_unaccepted_year_does_not_emit_proposals_or_change_review_cursor(sites):
    retained(sites)
    sites.conn.execute("DELETE FROM history_acceptance")
    assert reconcile(sites.db, now=sites.clock.now(), run_id=sites.run) == 0
    assert (
        sites.conn.execute(
            "SELECT 1 FROM findings WHERE kind='history_event_site_review'"
        ).fetchone()
        is None
    )
    assert (
        sites.conn.execute("SELECT 1 FROM meta WHERE key='event_site_review_cursor'").fetchone()
        is None
    )


def test_shared_site_adjacent_editions_abstain_without_printed_url_year(sites):
    url = "https://dance.example/festival/results.pdf"
    retained(sites, year=2020, urls=(url,))
    hits = retained_hits(sites.conn, Archive(sites.db.state_dir), SITE)
    other = Site("2020-06-edition", 2020, SITE.website)
    findings = proposals(SITE, (SITE, other), hits)
    assert len(findings) == 1 and findings[0].suggested_override is None
    assert findings[0].evidence["candidate_event_ids"] == [SITE.event_id, other.event_id]
    assert findings[0].evidence["reason"] == "ambiguous_event_or_format"


def test_explicit_url_year_can_narrow_edition_but_never_extend_capture_window(sites):
    retained(sites, year=2020)
    hits = retained_hits(sites.conn, Archive(sites.db.state_dir), SITE)
    other = Site("2020-06-edition", 2020, SITE.website)
    assert proposals(SITE, (SITE, other), hits)[0].suggested_override
    outside = replace(hits[0], timestamp="20210601000000")
    assert not proposals(SITE, (SITE,), (outside,))


def test_html_requires_actual_path_keyword_even_when_response_violates_query_filter(sites):
    wanted = "https://dance.example/festival/2019-results.html"
    bad = "https://dance.example/festival/about.html?results=2019"
    wrong_host = "https://other.example/festival/2019-results.html"
    retained(sites, mime="text/html", urls=(wanted, bad, wrong_host))
    hits = retained_hits(sites.conn, Archive(sites.db.state_dir), SITE)
    assert [hit.url for hit in hits] == [wanted]
    suggestion = proposals(SITE, (SITE,), hits)[0].suggested_override
    assert next(csv.reader(io.StringIO(suggestion)))[4] == "generic.html_table"


@pytest.mark.parametrize(
    "problem", ["missing_body", "wrong_filter", "missing_capture", "oversized_cohort"]
)
def test_missing_or_mismatched_evidence_is_an_explicit_finding_without_csv(sites, problem):
    identifier, body_sha = retained(sites, bad_filter=problem == "wrong_filter")
    if problem == "missing_body":
        Archive(sites.db.state_dir).blob_path(body_sha).unlink()
    elif problem == "missing_capture":
        sites.conn.execute("DELETE FROM archive_captures WHERE cdx_query_id=?", (identifier,))
    elif problem == "oversized_cohort":
        sites.conn.execute("UPDATE archive_queries SET next_page=65,total_pages=65")
    reconcile(sites.db, now=sites.clock.now(), run_id=sites.run)
    row = sites.conn.execute(
        "SELECT summary,suggested_override FROM findings WHERE kind='history_event_site_review' AND closed_at IS NULL"
    ).fetchone()
    assert "incomplete or invalid" in row[0] and row[1] is None
    assert sites.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0


def test_reviewed_override_closes_suggestion_but_cannot_create_watch(sites):
    retained(sites)
    reconcile(sites.db, now=sites.clock.now(), run_id=sites.run)
    reconcile(
        sites.db,
        now=sites.clock.now(),
        run_id=sites.run,
        overrides=({"event_id": SITE.event_id, "url": PDF},),
    )
    assert (
        sites.conn.execute(
            "SELECT 1 FROM findings WHERE kind='history_event_site_review' AND closed_at IS NULL"
        ).fetchone()
        is None
    )
    assert sites.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0


def test_http_capture_is_not_silently_rewritten_into_https_override(sites):
    retained(sites, urls=(PDF.replace("https:", "http:"),))
    finding = proposals(
        SITE, (SITE,), retained_hits(sites.conn, Archive(sites.db.state_dir), SITE)
    )[0]
    assert finding.suggested_override is None
    assert finding.evidence["reason"] == "http_locator_requires_review"


def test_no_query_evidence_stays_an_explicit_gap_not_verified_absence(sites):
    reconcile(sites.db, now=sites.clock.now(), run_id=sites.run)
    finding = sites.conn.execute(
        "SELECT evidence_json,suggested_override FROM findings WHERE kind='history_event_site_review' AND closed_at IS NULL"
    ).fetchone()
    assert json.loads(finding[0])["search_complete"] is False
    assert finding[1] is None


def test_review_cohort_rotates_and_scoped_control_keeps_it_unchanged(sites):
    from swingset.state.controls import Selector, change_control

    row = dict(sites.conn.execute("SELECT * FROM events").fetchone())
    columns = tuple(row)
    row["event_id"] = "2019-02-second"
    sites.conn.execute(
        f"INSERT INTO events VALUES ({','.join('?' for _ in columns)})",
        tuple(row[name] for name in columns),
    )
    accept(sites)
    reconcile(sites.db, now=sites.clock.now(), run_id=sites.run, event_limit=1)
    first = sites.conn.execute(
        "SELECT value FROM meta WHERE key='event_site_review_cursor'"
    ).fetchone()[0]
    change_control(
        sites.db.state_dir,
        selector=Selector("kind", "source_event_mapping"),
        paused=True,
        actor="test",
        reason="hold review",
        now=sites.clock.now(),
    )
    assert reconcile(sites.db, now=sites.clock.now(), run_id=sites.run, event_limit=1) == 0
    assert (
        sites.conn.execute(
            "SELECT value FROM meta WHERE key='event_site_review_cursor'"
        ).fetchone()[0]
        == first
    )
    change_control(
        sites.db.state_dir,
        selector=Selector("kind", "source_event_mapping"),
        paused=False,
        actor="test",
        reason="resume review",
        now=sites.clock.now(),
    )
    assert reconcile(sites.db, now=sites.clock.now(), run_id=sites.run, event_limit=1) == 1
    assert (
        sites.conn.execute(
            "SELECT value FROM meta WHERE key='event_site_review_cursor'"
        ).fetchone()[0]
        != first
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://[broken/results/",
        "https://dance.example:bad/festival/",
        "https://dance.example:444/festival/",
    ],
)
def test_malformed_or_nonstandard_site_locator_is_not_a_query_target(url):
    assert not query_urls(replace(SITE, website=url))


@pytest.mark.parametrize(
    "change",
    [
        lambda url: url.replace("web.archive.org", "user@web.archive.org"),
        lambda url: url.replace("web.archive.org", "web.archive.org:444"),
        lambda url: url + "#fragment",
        lambda url: url.replace("output=json", "output=xml"),
        lambda url: url.replace("collapse=digest", "collapse=urlkey"),
        lambda url: url.replace(
            "fl=timestamp%2Coriginal%2Cdigest%2Cmimetype%2Clength", "fl=original"
        ),
    ],
)
def test_receipt_must_match_exact_supported_cdx_endpoint_and_projection(sites, change):
    identifier, _ = retained(sites)
    path = sites.db.state_dir / "archive-cdx" / identifier / "0.json"
    receipt = json.loads(path.read_bytes())
    receipt["url"] = change(receipt["url"])
    durable_write(path, canonical(receipt))
    reconcile(sites.db, now=sites.clock.now(), run_id=sites.run)
    row = sites.conn.execute(
        "SELECT summary,suggested_override FROM findings WHERE kind='history_event_site_review' AND closed_at IS NULL"
    ).fetchone()
    assert "incomplete or invalid" in row[0] and row[1] is None


def test_malformed_capture_body_becomes_a_finding_without_crashing_review(sites):
    retained(sites, urls=(PDF, "https://[broken/results.pdf"))
    reconcile(sites.db, now=sites.clock.now(), run_id=sites.run)
    row = sites.conn.execute(
        "SELECT summary,suggested_override FROM findings WHERE kind='history_event_site_review' AND closed_at IS NULL"
    ).fetchone()
    assert "incomplete or invalid" in row[0] and row[1] is None


def test_changed_edition_binding_during_review_cannot_install_proposal(sites, monkeypatch):
    import swingset.history.event_sites as event_sites

    retained(sites)
    original = event_sites.proposals

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        sites.conn.execute("UPDATE events SET website='https://replacement.example/'")
        return result

    monkeypatch.setattr(event_sites, "proposals", changed)
    assert reconcile(sites.db, now=sites.clock.now(), run_id=sites.run) == 0
    assert (
        sites.conn.execute(
            "SELECT 1 FROM findings WHERE kind='history_event_site_review'"
        ).fetchone()
        is None
    )
    assert (
        sites.conn.execute("SELECT 1 FROM meta WHERE key='event_site_review_cursor'").fetchone()
        is None
    )


def test_cycle_reconciles_retained_event_site_suggestion_without_source_calls(
    fixture, tmp_path, monkeypatch
):
    import httpx
    from test_history_cycle import prepare_cycle

    from swingset.history import backfill
    from swingset.schedule import cycle

    fixture.conn.execute("UPDATE events SET website=?", (SITE.website,))
    config_dir, overrides_dir, _ = prepare_cycle(fixture, tmp_path, monkeypatch)
    retained(fixture)
    monkeypatch.setattr(backfill, "offers", lambda *args, **kwargs: ())
    result = cycle.run_cycle(
        fixture.db,
        config_dir=config_dir,
        overrides_dir=overrides_dir,
        clock=fixture.clock,
        budget=30,
        transport=httpx.MockTransport(lambda _: pytest.fail("review attempted source acquisition")),
    )
    assert result["event_sites_reviewed"] == 1
    row = fixture.conn.execute(
        "SELECT suggested_override FROM findings WHERE kind='history_event_site_review' AND closed_at IS NULL"
    ).fetchone()
    assert row and PDF in row[0]
    assert (
        fixture.conn.execute("SELECT COUNT(*) FROM watches WHERE source='generic'").fetchone()[0]
        == 0
    )
    assert fixture.conn.execute("SELECT COUNT(*) FROM history_origin_requests").fetchone()[0] == 0


def test_review_keeps_pinning_the_cdx_bodies_its_proposals_were_read_from(sites, tmp_path):
    """A proposal declares the retained page it was read from, so review keeps it.

    Migration 31 backfilled a body reference for every digest an open finding's
    evidence named and a file existed for. The evidence of an event-site
    proposal names the CDX response body each capture came from. Declaring
    nothing and rewriting the finding would have deleted that backfilled row on
    the next review cadence, unpinning the very page the proposal asks a human
    to check.
    """
    from swingset.backup.checkpoint import create_checkpoint
    from swingset.state import retention
    from swingset.state.finding_reference_migration import backfill_finding_references

    _, body_sha = retained(sites)
    state = sites.db.state_dir
    blob = Archive(state).blob_path(body_sha)
    assert blob.is_file()
    assert reconcile(sites.db, now=sites.clock.now(), run_id=sites.run) == 1
    finding = sites.conn.execute(
        "SELECT finding_id FROM findings WHERE kind='history_event_site_review' AND closed_at IS NULL"
    ).fetchone()[0]
    declared = [
        tuple(row)
        for row in sites.conn.execute(
            "SELECT kind,sha256 FROM finding_support_references WHERE finding_id=?", (finding,)
        )
    ]
    assert declared == [("body", body_sha)]

    # The same rows migration 30 would have backfilled from this evidence, and
    # nothing more: the receipt itself lives under archive-cdx/, not blobs/, so
    # declaring it would name a file that is not there.
    sites.conn.execute("DELETE FROM finding_support_references")
    assert backfill_finding_references(sites.conn, state) == 1
    assert [
        tuple(row)
        for row in sites.conn.execute("SELECT kind,sha256 FROM finding_support_references")
    ] == declared

    # The next review cadence rewrites the same finding and keeps the pin.
    reconcile(sites.db, now=sites.clock.now(), run_id=sites.run)
    assert [
        tuple(row)
        for row in sites.conn.execute("SELECT kind,sha256 FROM finding_support_references")
    ] == declared
    assert blob.resolve() in retention.artifact_closure(state, sites.conn, set())
    assert (
        retention.plan(
            sites.conn,
            state,
            max_database_bytes=1 << 40,
            recent_window=1,
            collect_older_than=86400.0,
        ).content["unknown"]["missing_declared_references"]
        == []
    )
    checkpoint = create_checkpoint(
        state,
        sites.conn,
        tmp_path / "checkpoint",
        schema_version=sites.db.schema_version,
        versions={},
        input_bundle_hash=None,
    )
    assert blob.relative_to(state).as_posix() in checkpoint.files
