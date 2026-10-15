"""The event-site link tool reads an event's site politely and drafts WDR rows (D-0256)."""

import json
import sqlite3
from datetime import date

import httpx

from journal.tools.collection import event_site_links as tool
from swingset.fetch.client import USER_AGENT
from swingset.state.db import open_database

UUID = "20774cdb-8354-11f0-9a29-0aa72bbce9ea"
NOW = "2026-10-15T00:00:00+00:00"


def _event(conn, event_id, website, end, *, series="wsdc-273", held="held"):
    conn.execute(
        "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,website,wsdc_status,"
        "sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id,event_month,"
        "date_precision,held,coverage_tier,history_source) VALUES (?,?,?,2026,?,?,?,'registry',"
        "'[\"wsdc_calendar\"]','wsdc_calendar','s','7',?,?,'run',?,'day',?,'index_only','[\"calendar\"]')",
        (event_id, series, event_id, end, end, website, NOW, NOW, end[:7], held),
    )


def _state(tmp_path):
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run',?,0)", (NOW,))
        _event(conn, "2026-02-charlotte", "https://www.charlotte.test/", "2026-02-02")
        _event(conn, "2026-03-covered", "https://covered.test/", "2026-03-08")
        _event(conn, "2026-04-listed", "https://listed.test/", "2026-04-12", held="listed")
        _event(conn, "2026-05-other", "https://other.test/", "2026-05-18", series="slug-other")
        _event(conn, "2026-06-results", "https://results.test/", "2026-06-28")
        conn.execute(
            "INSERT INTO contests(contest_id,event_id,name_raw,division,age_division,contest_type,"
            "partner_mode,dance_style,wsdc_points_eligible,combined_from,parse_status,"
            "source_contest_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) "
            "VALUES ('c','2026-06-results','Novice','novice','open','jnj','random','wcs',1,'[]',"
            "'parsed','c','eepro','s','1',?,?,'run')",
            (NOW, NOW),
        )
    return tmp_path / "state.sqlite"


def test_selection_is_held_wsdc_events_without_results_or_an_override(tmp_path):
    conn = tool.open_state(_state(tmp_path))
    try:
        chosen = tool.select(
            conn,
            start=date(2026, 1, 1),
            end=date(2026, 10, 15),
            overrides=({"event_id": "2026-03-covered"},),
        )
        assert chosen == [("2026-02-charlotte", "https://www.charlotte.test/", "2026-02-02")]
        assert tool.select(
            conn, start=date(2026, 1, 1), end=date(2026, 1, 31), events=("2026-04-listed",)
        ) == [("2026-04-listed", "https://listed.test/", "2026-04-12")]
    finally:
        conn.close()


def test_scan_reads_robots_first_follows_result_pages_and_drafts_one_wdr_row():
    calls: list[str] = []
    sleeps: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["User-Agent"] == USER_AGENT
        assert request.headers["Accept-Encoding"] == "gzip"
        path = request.url.path
        if request.url.scheme == "http":
            return httpx.Response(301, headers={"Location": f"https://www.charlotte.test{path}"})
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\nCrawl-delay: 7\n")
        if path == "/":
            return httpx.Response(
                200,
                text=(
                    '<a href="/results">2026 Results</a> <a href="/private/scores">Scores</a>'
                    '<a href="/scores.pdf">Score sheets</a> <a href="/logo.png">results</a>'
                    '<a href="https://scoring.dance/enUS/events/1/results/">old results</a>'
                    '<a href="/about">About</a>'
                ),
            )
        if path == "/results":
            return httpx.Response(
                200,
                text=f'<a href="https://scores.worlddanceregistry.com/{UUID}">Results on WDR</a>',
            )
        raise AssertionError(f"unexpected request {request.url}")

    fetcher = tool.Fetcher(
        gap=1,
        transport=httpx.MockTransport(respond),
        sleep=sleeps.append,
        clock=lambda: 0.0,
    )
    try:
        row = tool.scan(
            fetcher, "2026-02-charlotte", "http://www.charlotte.test/", end="2026-02-02", pages=3
        )
    finally:
        fetcher.close()
    assert calls == [
        "http://www.charlotte.test/robots.txt",
        "https://www.charlotte.test/robots.txt",
        "http://www.charlotte.test/",
        "https://www.charlotte.test/",
        "https://www.charlotte.test/results",
    ]
    # The floor is 2 s; the robots.txt Crawl-delay is larger and wins.
    assert fetcher.gap == 2.0
    assert sleeps == [2.0, 7.0, 7.0, 7.0]
    assert row["wdr"] == {UUID: ["https://www.charlotte.test/results"]}
    assert row["platform_links"] == ["https://scoring.dance/enUS/events/1/results/"]
    assert row["result_pages"] == [
        "https://www.charlotte.test/private/scores",
        "https://www.charlotte.test/scores.pdf",
    ]
    assert row["wdr_made"] == {UUID: "2025-08-27"}
    assert row["requests"] == 5
    assert row["source_urls_csv"].startswith(
        "2026-02-charlotte,worlddanceregistry,event,"
        f"https://scores.worlddanceregistry.com/{UUID}/rounds/routeInfo.json,wdr.rounds,"
    )
    # The same link a year and a half later is an earlier edition's: no draft.
    fetcher = tool.Fetcher(transport=httpx.MockTransport(respond), sleep=lambda _: None)
    try:
        later = tool.scan(
            fetcher, "2027-03-charlotte", "https://www.charlotte.test/", end="2027-03-01"
        )
    finally:
        fetcher.close()
    assert later["wdr"] and later["source_urls_csv"] is None
    assert later["draft_refused"] == "no_wdr_id_of_this_edition"


def test_a_blocked_host_stops_and_the_budget_is_kept():
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "down.test":
            return httpx.Response(503)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text='<a href="/results">Results</a><a href="/scores">x</a>')

    fetcher = tool.Fetcher(
        max_requests=3, transport=httpx.MockTransport(respond), sleep=lambda _: None
    )
    try:
        down = tool.scan(fetcher, "a", "down.test")
        busy = tool.scan(fetcher, "b", "https://busy.test/", pages=5)
    finally:
        fetcher.close()
    assert down["pages"] == [
        {"url": "https://down.test", "status": 0, "final": "https://down.test"}
    ]
    assert calls == [
        "https://down.test/robots.txt",
        "https://busy.test/robots.txt",
        "https://busy.test/",
    ]
    assert busy["budget_spent"] is True and busy["source_urls_csv"] is None


def test_list_mode_fetches_nothing_and_leaves_the_state_unchanged(tmp_path, capsys):
    state = _state(tmp_path)
    before = state.read_bytes()

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request in --list mode")

    assert tool.main(["--state", str(state), "--list"], transport=httpx.MockTransport(refuse)) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["event_id"] for row in rows] == [
        "2026-02-charlotte",
        "2026-03-covered",
    ]
    assert state.read_bytes() == before
    assert not sqlite3.connect(state).execute("SELECT 1 FROM runs WHERE run_id!='run'").fetchone()
