"""The offline event-site review tool proposes `source_urls.csv` rows and writes nothing."""

import csv
import io
import json
import sqlite3
import time
from dataclasses import replace

import pytest
from test_history_event_sites import PDF, SITE, retained
from test_history_event_sites import sites as sites
from test_platform_backfill import fixture as fixture

from journal.tools.collection import event_site_review as tool
from swingset.fetch.archive import Archive
from swingset.history.event_sites import Site, retained_hits


def run(f, **kwargs):
    conn = tool.open_state(f.db.state_dir)
    try:
        return tool.review(conn, f.db.state_dir, **kwargs)
    finally:
        conn.close()


def hits(f):
    return retained_hits(f.conn, Archive(f.db.state_dir), SITE)


def test_verified_pdf_becomes_draft_csv_with_exact_binding_and_no_controls(sites):
    retained(sites)
    before_watches = [tuple(row) for row in sites.conn.execute("SELECT * FROM watches")]
    rows = run(sites)
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == SITE.event_id and row["reason"] == "draft_override"
    assert row["parser_available"] is False
    suggestion = next(csv.reader(io.StringIO(row["source_urls_csv"])))
    assert suggestion[:5] == [SITE.event_id, "generic", "round", PDF, "generic.pdf_table"]
    assert row["candidate_event_ids"] == [SITE.event_id]
    assert len(row["captures"]) == 1 and row["captures"][0]["receipt_sha256"]
    assert [tuple(row) for row in sites.conn.execute("SELECT * FROM watches")] == before_watches
    assert sites.conn.execute("SELECT COUNT(*) FROM history_origin_intents").fetchone()[0] == 0
    assert sites.conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0
    assert (
        sites.conn.execute(
            "SELECT COUNT(*) FROM findings WHERE kind='history_event_site_review'"
        ).fetchone()[0]
        == 0
    )
    assert run(sites) == rows


def test_unaccepted_year_yields_no_proposal(sites):
    retained(sites)
    sites.conn.execute("DELETE FROM history_acceptance")
    assert run(sites) == []
    assert sites.conn.execute("SELECT 1 FROM meta WHERE key LIKE 'event_site%'").fetchone() is None


def test_year_gate_is_judged_on_facts_not_the_pending_projection(sites, monkeypatch):
    from swingset.project import history

    retained(sites)

    def pending(_conn):
        raise AssertionError("the tool asked the pending-projection question")

    monkeypatch.setattr(history, "_pending_inventory_projection", pending)
    assert [row["reason"] for row in run(sites)] == ["draft_override"]


def test_tool_writes_nothing(sites, tmp_path_factory):
    retained(sites)
    state = sites.db.state_dir
    database = state / "state.sqlite"
    files = sorted(path.relative_to(state) for path in state.rglob("*") if path.is_file())
    before = (
        database.stat().st_size,
        database.stat().st_mtime_ns,
        sites.conn.execute("PRAGMA data_version").fetchone()[0],
    )
    out = tmp_path_factory.mktemp("out") / "proposals.jsonl"
    assert tool.main(["--state", str(state), "--events", "10", "--out", str(out)]) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [row["reason"] for row in rows] == ["draft_override"]
    after = (
        database.stat().st_size,
        database.stat().st_mtime_ns,
        sites.conn.execute("PRAGMA data_version").fetchone()[0],
    )
    assert after == before
    assert sorted(path.relative_to(state) for path in state.rglob("*") if path.is_file()) == files
    conn = tool.open_state(state)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE written(x)")
    finally:
        conn.close()


def test_immutable_open_when_no_log_holds_data(sites):
    retained(sites)
    sites.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    database = sites.db.state_dir / "state.sqlite"
    stamp = database.stat().st_mtime_ns
    started = time.monotonic()
    rows = run(sites, events=10)
    assert time.monotonic() - started < 60
    assert [row["reason"] for row in rows] == ["draft_override"]
    assert database.stat().st_mtime_ns == stamp


def test_out_inside_the_state_is_refused(sites):
    with pytest.raises(SystemExit):
        tool.main(["--state", str(sites.db.state_dir), "--out", str(sites.db.state_dir / "x")])


def test_shared_site_adjacent_editions_abstain_without_printed_url_year(sites):
    url = "https://dance.example/festival/results.pdf"
    retained(sites, year=2020, urls=(url,))
    other = Site("2020-06-edition", 2020, SITE.website)
    rows = tool.proposals(SITE, (SITE, other), hits(sites))
    assert len(rows) == 1 and rows[0]["source_urls_csv"] is None
    assert rows[0]["candidate_event_ids"] == [SITE.event_id, other.event_id]
    assert rows[0]["reason"] == "ambiguous_event_or_format"


def test_explicit_url_year_can_narrow_edition_but_never_extend_capture_window(sites):
    retained(sites, year=2020)
    found = hits(sites)
    other = Site("2020-06-edition", 2020, SITE.website)
    assert tool.proposals(SITE, (SITE, other), found)[0]["source_urls_csv"]
    outside = replace(found[0], timestamp="20210601000000")
    assert not tool.proposals(SITE, (SITE,), (outside,))


def test_html_requires_actual_path_keyword_even_when_response_violates_query_filter(sites):
    wanted = "https://dance.example/festival/2019-results.html"
    bad = "https://dance.example/festival/about.html?results=2019"
    wrong_host = "https://other.example/festival/2019-results.html"
    retained(sites, mime="text/html", urls=(wanted, bad, wrong_host))
    found = hits(sites)
    assert [hit.url for hit in found] == [wanted]
    suggestion = tool.proposals(SITE, (SITE,), found)[0]["source_urls_csv"]
    assert next(csv.reader(io.StringIO(suggestion)))[4] == "generic.html_table"


@pytest.mark.parametrize(
    "problem", ["missing_body", "wrong_filter", "missing_capture", "oversized_cohort"]
)
def test_missing_or_mismatched_evidence_is_an_explicit_gap_without_csv(sites, problem):
    identifier, body_sha = retained(sites, bad_filter=problem == "wrong_filter")
    if problem == "missing_body":
        Archive(sites.db.state_dir).blob_path(body_sha).unlink()
    elif problem == "missing_capture":
        sites.conn.execute("DELETE FROM archive_captures WHERE cdx_query_id=?", (identifier,))
    elif problem == "oversized_cohort":
        sites.conn.execute("UPDATE archive_queries SET next_page=65,total_pages=65")
    rows = run(sites)
    assert [row["reason"] for row in rows] == ["invalid_evidence"]
    assert rows[0]["error"] and "source_urls_csv" not in rows[0]
    assert sites.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0


def test_reviewed_override_closes_suggestion_but_cannot_create_watch(sites, tmp_path_factory):
    retained(sites)
    assert run(sites)
    overrides = tmp_path_factory.mktemp("overrides") / "source_urls.csv"
    overrides.write_text(
        "event_id,source,kind,url,parser,notes\n"
        f"{SITE.event_id},generic,round,{PDF},generic.pdf_table,reviewed\n"
    )
    assert run(sites, overrides=tool.read_overrides(overrides)) == []
    assert sites.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0


def test_override_for_another_event_makes_the_url_ambiguous(sites):
    retained(sites)
    rows = run(sites, overrides=({"event_id": "2019-02-other", "url": PDF},))
    assert [row["reason"] for row in rows] == ["ambiguous_event_or_format"]
    assert rows[0]["source_urls_csv"] is None


def test_http_capture_is_not_silently_rewritten_into_https_override(sites):
    retained(sites, urls=(PDF.replace("https:", "http:"),))
    row = tool.proposals(SITE, (SITE,), hits(sites))[0]
    assert row["source_urls_csv"] is None
    assert row["reason"] == "http_locator_requires_review"


def test_no_query_evidence_stays_an_explicit_gap_not_verified_absence(sites):
    rows = run(sites)
    assert rows == [
        {
            "event_id": SITE.event_id,
            "year": SITE.year,
            "website": SITE.website,
            "reason": "no_retained_hits",
            "search_complete": False,
        }
    ]


def test_malformed_capture_body_is_a_gap_without_crashing_review(sites):
    retained(sites, urls=(PDF, "https://[broken/results.pdf"))
    rows = run(sites)
    assert [row["reason"] for row in rows] == ["invalid_evidence"]


def test_cohort_pages_by_event_id_and_filters_by_year(sites):
    row = dict(sites.conn.execute("SELECT * FROM events").fetchone())
    columns = tuple(row)
    row["event_id"] = "2019-02-second"
    sites.conn.execute(
        f"INSERT INTO events VALUES ({','.join('?' for _ in columns)})",
        tuple(row[name] for name in columns),
    )
    from test_platform_backfill import accept

    accept(sites)
    first = run(sites, events=1)
    assert len(first) == 1
    second = run(sites, events=1, after=first[0]["event_id"])
    assert len(second) == 1 and second[0]["event_id"] > first[0]["event_id"]
    assert run(sites, year=2018) == []
    assert len(run(sites, year=2019)) == 2
