"""WP15 planning and admission are offline until an owner accepts the exact year."""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from materialized_fixture import materialize_seeded_outputs

from swingset.admission.contracts import contract_version
from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.client import FetchClient, FetchResult
from swingset.fetch.wayback import Capture
from swingset.history.acquisition import archive_watch_gate, phase_two_gate
from swingset.history.backfill import dispatch_one
from swingset.history.captures import next_capture
from swingset.history.platform import KnownEvent, Page, plan_platform, retained_plan
from swingset.model.canonical import Event
from swingset.project.history import accept_year
from swingset.project.writer import Projection, replace_scope
from swingset.state.db import open_database

URL = "https://eepro.com/results/example2019/"
EVENT = KnownEvent(
    "2019-01-example", "eepro", "eepro:example2019", 2019, "2019-01", date(2019, 1, 20)
)
CAPTURES = tuple(
    Capture(URL, stamp, f"digest-{index}", "text/html", 100)
    for index, stamp in enumerate(
        ("20190501000000", "20190401000000", "20190301000000", "20190201000000")
    )
)


@pytest.fixture
def fixture(tmp_path):
    with open_database(tmp_path) as db:
        clock = FakeClock()
        run = db.start_run(clock.now())
        conn = db.connection
        event = Event(
            event_id=EVENT.event_id,
            series_id="series",
            name="Example",
            year=EVENT.year,
            start_date="2019-01-18",
            end_date=EVENT.end_date.isoformat(),
            source="wsdc_registry",
            snapshot_id="inventory",
            parser_version="1",
            first_seen_at=clock.now().isoformat(),
            last_seen_at=clock.now().isoformat(),
            run_id=run,
        )
        replace_scope(
            conn,
            scope_kind="history",
            scope_id="fixture",
            projection=Projection((event,)),
            run_id=run,
            projected_at=clock.now().isoformat(),
        )
        conn.execute("DELETE FROM pending_work")
        conn.execute(
            "INSERT INTO source_event_map VALUES (?,?,?,'explicit',1)",
            (EVENT.source, EVENT.source_ref, EVENT.event_id),
        )
        conn.executemany(
            "INSERT INTO meta VALUES (?,'true')", [("h7_deployed",), ("h10_deployed",)]
        )
        conn.executemany(
            "INSERT INTO archive_captures VALUES (?,?,?,?,200,?,?,?,'retained')",
            [
                (
                    "eepro",
                    c.url,
                    c.timestamp,
                    c.digest,
                    c.mimetype,
                    c.length,
                    clock.now().isoformat(),
                )
                for c in CAPTURES
            ],
        )
        config = Config(
            {"web.archive.org": HostConfig(min_gap_seconds=10)}, {"eepro": SourceConfig(True)}
        )
        materialize_seeded_outputs(db, now=clock.now(), run_id=run)
        yield SimpleNamespace(db=db, conn=conn, clock=clock, run=run, config=config)


def review_contract(fixture, kind="eepro.autoindex", *, version=None, mode="enforce"):
    version = version or contract_version(kind)
    # Explicit test-only reviewed policy. This helper never touches real state or activates a source.
    fixture.conn.execute(
        "INSERT OR REPLACE INTO admission_policies VALUES (?,?,?,? ,?,'offline-reviewer',?)",
        (
            kind,
            version,
            mode,
            "review:test-" + kind,
            "test-" + kind,
            fixture.clock.now().isoformat(),
        ),
    )
    fixture.conn.execute(
        "INSERT OR IGNORE INTO admission_reviews VALUES (?,?,?,'[]','offline-reviewer',?,'synthetic gate test')",
        ("test-" + kind, kind, version, fixture.clock.now().isoformat()),
    )


def accept(fixture):
    materialize_seeded_outputs(fixture.db, now=fixture.clock.now(), run_id=fixture.run)
    accept_year(
        fixture.conn, 2019, accepted_by="offline-owner", accepted_at=fixture.clock.now().isoformat()
    )
    materialize_seeded_outputs(fixture.db, now=fixture.clock.now(), run_id=fixture.run)


def recorder():
    calls = []

    def fetch(watch_id, page_kind, run_id, *, deadline=None):
        calls.append((watch_id, page_kind.kind, deadline))
        return FetchResult(Classification(Outcome.INVALID), skipped="offline-record-only")

    return SimpleNamespace(fetch=fetch, calls=calls)


def dispatch(fixture, fetcher, **kwargs):
    return dispatch_one(
        fixture.db,
        fixture.config,
        fixture.clock,
        fixture.run,
        deadline=fixture.clock.now() + timedelta(minutes=10),
        fetcher=fetcher,
        **kwargs,
    )


def test_pure_plan_uses_event_year_breadth_and_parent_order():
    other = KnownEvent("2019-other", "scoringdance", "scoringdance:1", 2019, "2019-01")
    newest = KnownEvent("2020-one", "scoringdance", "scoringdance:2", 2020, "2020-02")
    extra = KnownEvent("2019-extra", "eepro", "eepro:extra2019", 2019, "2019-01")
    early = KnownEvent("2009-old", "eepro", "eepro:old2009", 2009, "2009-01")
    captures = [("eepro", c) for c in CAPTURES]
    for source, url in [
        ("eepro", URL + "final.htm"),
        ("eepro", "https://eepro.com/results/extra2019/"),
        ("scoringdance", "https://scoring.dance/enUS/events/1/results/"),
        ("scoringdance", "https://scoring.dance/enUS/events/2/results/"),
        ("eepro", "https://eepro.com/results/old2009/"),
    ]:
        captures.append((source, Capture(url, "20260101000000", url, "text/html", 1)))
    plan = plan_platform((EVENT, other, newest, extra, early), tuple(captures))
    assert plan.pages[0].event == newest
    assert [p.page.source for p in plan.pages[1:]] == ["eepro", "eepro", "eepro", "scoringdance"]
    assert [p.page.kind for p in plan.pages[1:4]] == ["autoindex", "autoindex", "round"]
    original = next(p for p in plan.pages if p.page.url == URL)
    assert original.captures == CAPTURES[:3]
    assert all(p.event.year >= 2010 for p in plan.pages)


def test_unmapped_sheet_never_creates_an_event_or_watch(fixture):
    before = fixture.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    capture = Capture(
        "https://scoring.dance/enUS/results/4321.html", "20200101000000", "a", "text/html", 1
    )
    plan = plan_platform((EVENT,), (("scoringdance", capture),))
    assert not plan.pages and plan.findings[0].kind == "history_unmapped_sheet"
    assert fixture.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before
    assert fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0


def test_no_year_acceptance_means_no_phase_two_control(fixture):
    review_contract(fixture)
    fetcher = recorder()
    result = dispatch(fixture, fetcher)
    assert result.reason == "history_year_unaccepted"
    assert not fetcher.calls
    assert fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0


@pytest.mark.parametrize(
    "mode,version,expected",
    [
        ("shadow", "5", "history_contract_not_enforced"),
        ("paused", "5", "history_contract_not_enforced"),
        ("enforce", "old", "history_contract_review_stale"),
    ],
)
def test_exact_kind_contract_required_even_with_global_deployment(fixture, mode, version, expected):
    accept(fixture)
    review_contract(fixture, version=version, mode=mode)
    assert dispatch(fixture, recorder()).reason == expected
    assert fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0


def test_wrong_kind_or_changed_inventory_cannot_reuse_acceptance(fixture):
    accept(fixture)
    review_contract(fixture, "eepro.round")
    assert dispatch(fixture, recorder()).reason == "history_contract_not_enforced"
    review_contract(fixture)
    fixture.conn.execute("UPDATE events SET end_date='2019-01-21'")
    assert dispatch(fixture, recorder()).reason == "pipeline_work_pending"
    materialize_seeded_outputs(fixture.db, now=fixture.clock.now(), run_id=fixture.run)
    assert dispatch(fixture, recorder()).reason == "history_year_unaccepted"
    assert (
        phase_two_gate(
            fixture.conn, source="eepro", source_ref=EVENT.source_ref, page_kind="wdr.rounds"
        )
        == "history_page_kind_source_mismatch"
    )


def test_index_label_cannot_bypass_phase_two_gate(fixture):
    assert (
        archive_watch_gate(
            fixture.conn,
            source="eepro",
            source_ref=EVENT.source_ref,
            page_kind="eepro.autoindex",
            watch_kind="index",
            archive_url=CAPTURES[0].archive_url,
        )
        == "history_year_unaccepted"
    )
    assert (
        archive_watch_gate(
            fixture.conn,
            source="wsdc_calendar",
            source_ref=None,
            page_kind="wsdc_calendar.events",
            watch_kind="index",
            archive_url=CAPTURES[0].archive_url,
        )
        is None
    )


def test_dispatch_is_atomic_resumable_priority_six_and_reserves_two_minutes(fixture):
    accept(fixture)
    review_contract(fixture)
    fetcher = recorder()
    result = dispatch(fixture, fetcher)
    assert result.reason == "dispatched"
    watch = fixture.conn.execute("SELECT * FROM watches").fetchone()
    assert (watch["priority"], watch["state"], watch["archive_url"]) == (
        6,
        "backfill",
        CAPTURES[0].archive_url,
    )
    assert fetcher.calls[0][2] == fixture.clock.now() + timedelta(minutes=8)
    second = dispatch(fixture, fetcher)
    assert second.watch_id == result.watch_id
    assert fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 1


def test_watch_insertion_rolls_back_if_receipt_finding_write_crashes(fixture, monkeypatch):
    from swingset.history import backfill

    accept(fixture)
    review_contract(fixture)

    def crash(*args, **kwargs):
        raise RuntimeError("crash before transaction commit")

    monkeypatch.setattr(backfill, "replace_findings", crash)
    with pytest.raises(RuntimeError, match="crash"):
        dispatch(fixture, recorder())
    assert fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0


def test_busy_work_and_deadline_preserve_controls(fixture):
    accept(fixture)
    review_contract(fixture)
    fetcher = recorder()
    result = dispatch_one(
        fixture.db,
        fixture.config,
        fixture.clock,
        fixture.run,
        deadline=fixture.clock.now() + timedelta(seconds=120),
        fetcher=fetcher,
    )
    assert result.reason == "wall_clock_reserve"
    fixture.conn.execute(
        "INSERT INTO pending_work VALUES ('parse','snapshot','other',?)",
        (fixture.clock.now().isoformat(),),
    )
    assert dispatch(fixture, fetcher).reason == "pipeline_work_pending"
    fixture.conn.execute("DELETE FROM pending_work")
    fixture.conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,state,next_check_at,priority) VALUES ('other','eepro','index','GET','https://eepro.com/event.php','eepro.index','live',?,2)",
        (fixture.clock.now().isoformat(),),
    )
    assert dispatch(fixture, fetcher).reason == "other_watch_due"
    assert not fetcher.calls
    assert fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 1


def failed_snapshot(fixture, watch_id, capture, number, *, parse="failed", status=200):
    fixture.conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification,via,archive_url,requested_archive_url,extract_status,parse_status,extract_version,parser_version) VALUES (?,?,'GET',?,?,?,?,1,1,?,?,'wayback',?,?,'ok',?,'3','1')",
        (
            f"snap-{number}",
            watch_id,
            URL,
            fixture.clock.now().isoformat(),
            status,
            f"body-{number}",
            fixture.run,
            "Ok" if status == 200 else "Throttled",
            capture.archive_url,
            capture.archive_url,
            parse,
        ),
    )


def test_three_distinct_failed_captures_then_gap_without_fourth_fetch(fixture):
    accept(fixture)
    review_contract(fixture)
    fetcher = recorder()
    for index in range(3):
        result = dispatch(fixture, fetcher)
        assert result.capture == CAPTURES[index]
        failed_snapshot(fixture, result.watch_id, CAPTURES[index], index)
    result = dispatch(fixture, fetcher)
    assert result.watch_id is None and len(fetcher.calls) == 3
    finding = fixture.conn.execute(
        "SELECT evidence_json FROM findings WHERE kind='history_archive_gap' AND closed_at IS NULL"
    ).fetchone()
    assert json.loads(finding[0])["attempts_available"] == 3


def test_diagnostic_failure_does_not_spend_a_distinct_capture(fixture):
    accept(fixture)
    review_contract(fixture)
    first = dispatch(fixture, recorder())
    failed_snapshot(fixture, first.watch_id, CAPTURES[0], 1, status=503)
    page = retained_plan(fixture.conn).pages[0]
    assert next_capture(fixture.conn, page)[0] == CAPTURES[0]


def test_fetch_uses_actual_gate_budget_and_no_other_host(fixture):
    accept(fixture)
    review_contract(fixture)
    fixture.conn.execute(
        "INSERT INTO host_budget VALUES ('web.archive.org',?,200,0)",
        (fixture.clock.now().date().isoformat(),),
    )
    client = FetchClient(
        fixture.conn,
        fixture.config,
        fixture.clock,
        Archive(fixture.db.state_dir),
        transport=httpx.MockTransport(lambda _: pytest.fail("budget must prevent request")),
    )
    try:
        result = dispatch(fixture, client)
        assert result.result.skipped == "request budget"
        assert fixture.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 200
    finally:
        client.close()


def test_stale_planned_dates_cannot_pass_after_new_acceptance(fixture):
    plan = retained_plan(fixture.conn)
    fixture.conn.execute("UPDATE events SET end_date='2019-01-21'")
    accept(fixture)
    review_contract(fixture)
    assert dispatch(fixture, recorder(), plan=plan).reason == "plan_mapping_changed"
    assert fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0


def accepted_parent(fixture, *, existing_child=False):
    from pathlib import Path

    from swingset.schedule.parse import parse_snapshot
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.base import ParseContext
    from swingset.sources.eepro.adapter import AutoIndexPage
    from swingset.state.work import WorkUnit

    accept(fixture)
    review_contract(fixture)
    body = Path("tests/fixtures/sources/eepro-archive/freedomswing2019.html").read_bytes()
    page = AutoIndexPage()
    declared = page.parse(
        page.extract(body),
        ParseContext(
            "raw",
            "parent",
            URL,
            "eepro",
            page.kind,
            EVENT.source_ref,
            fixture.clock.now().isoformat(),
        ),
    )
    preserved = None
    if existing_child:
        parent_id = dispatch(fixture, recorder()).watch_id
        child = declared.watches[0]
        upsert_watch(fixture.conn, child, fixture.clock.now())
        fixture.conn.execute(
            "UPDATE watches SET state='paused',next_check_at=NULL,notes='operator control',parent_watch_id=? WHERE watch_id=?",
            (parent_id, child.watch_id),
        )
        preserved = dict(
            fixture.conn.execute(
                "SELECT * FROM watches WHERE watch_id=?", (child.watch_id,)
            ).fetchone()
        )
    client = FetchClient(
        fixture.conn,
        fixture.config,
        fixture.clock,
        Archive(fixture.db.state_dir),
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(404)
                if request.url.path == "/robots.txt"
                else httpx.Response(200, content=body)
            )
        ),
    )
    try:
        fetched = dispatch(fixture, client)
        assert fetched.result.snapshot_id
        parsed = parse_snapshot(
            fixture.db,
            Archive(fixture.db.state_dir),
            WorkUnit("parse", "snapshot", fetched.result.snapshot_id),
            fixture.clock,
            fixture.run,
        )
        assert not parsed.failed
    finally:
        client.close()
    return fetched, declared, preserved


def test_real_archived_parent_retains_generation_and_intents_without_origin_children(fixture):
    fetched, declared, preserved = accepted_parent(fixture, existing_child=True)
    assert len(declared.watches) == 7
    assert fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 2
    assert (
        fixture.conn.execute("SELECT COUNT(*) FROM observations WHERE kind='file_row'").fetchone()[
            0
        ]
        == 7
    )
    assert (
        dict(
            fixture.conn.execute(
                "SELECT * FROM watches WHERE watch_id=?", (preserved["watch_id"],)
            ).fetchone()
        )
        == preserved
    )
    generation = fixture.conn.execute(
        "SELECT result_json FROM source_generations WHERE state='accepted'"
    ).fetchone()
    assert generation and len(json.loads(generation[0])["watches"]) == 7
    assert (
        fixture.conn.execute(
            "SELECT COUNT(*) FROM findings WHERE kind='acquisition_gate' AND closed_at IS NULL"
        ).fetchone()[0]
        == 6
    )
    assert (
        fixture.conn.execute(
            "SELECT COUNT(*) FROM host_budget WHERE host<>'web.archive.org'"
        ).fetchone()[0]
        == 0
    )
    assert fetched.watch_id != preserved["watch_id"]


def test_advertised_missing_child_tries_another_parent_capture(fixture):
    from swingset.history.captures import capture_state

    fetched, _, _ = accepted_parent(fixture)
    plan = retained_plan(fixture.conn)
    parent = next(p for p in plan.pages if p.page.url == URL)
    state = capture_state(fixture.conn, parent, CAPTURES[0])
    assert state.state == "incomplete" and state.reason == "advertised_child_missing"
    assert next_capture(fixture.conn, parent)[0] == CAPTURES[1]
    assert any(f.kind == "history_archive_gap" for f in plan.findings)
    assert len(plan.pages) == 8
    assert fetched.result.snapshot_id == state.snapshot_id


def test_round_waits_for_parent_and_its_own_exact_contract(fixture):
    accept(fixture)
    review_contract(fixture)
    review_contract(fixture, "eepro.round")
    child = Page("eepro", EVENT.source_ref, URL + "final.htm", "eepro.round", "round", URL)
    capture = Capture(child.url, "20190501000000", "child", "text/html", 1)
    plan = plan_platform((EVENT,), (("eepro", capture),), declared_pages=(child,))
    assert dispatch(fixture, recorder(), plan=plan).reason == "parent_document_pending"
    assert fixture.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0


def test_complete_parent_precedes_round_and_round_needs_its_own_contract(fixture):
    from dataclasses import replace

    _, declared, _ = accepted_parent(fixture)
    fixture.conn.executemany(
        "INSERT INTO archive_captures VALUES (?,?,?,?,200,?,?,?,'retained-child')",
        [
            (
                "eepro",
                child.url,
                "20190501000000",
                f"child-{index}",
                "text/html",
                100,
                fixture.clock.now().isoformat(),
            )
            for index, child in enumerate(declared.watches)
        ],
    )
    # Simulate completed downstream outputs in this transport-only fixture.
    materialize_seeded_outputs(fixture.db, now=fixture.clock.now(), run_id=fixture.run)
    plan = retained_plan(fixture.conn)
    parent = next(item for item in plan.pages if item.page.url == URL)
    assert next_capture(fixture.conn, parent) == (None, "complete")
    # A newly listed capture does not displace a complete immutable interpretation.
    newer = Capture(URL, "20200101000000", "newer", "text/html", 100)
    assert next_capture(fixture.conn, replace(parent, captures=(newer, CAPTURES[0]))) == (
        None,
        "complete",
    )
    fetcher = recorder()
    assert dispatch(fixture, fetcher).reason == "history_contract_not_enforced"
    assert not fetcher.calls
    review_contract(fixture, "eepro.round")
    result = dispatch(fixture, fetcher)
    assert result.reason == "dispatched"
    child = fixture.conn.execute(
        "SELECT * FROM watches WHERE watch_id=?", (result.watch_id,)
    ).fetchone()
    assert child["kind"] == "round" and child["state"] == "backfill" and child["priority"] == 6
    assert child["archive_url"].startswith("https://web.archive.org/")
    assert (
        fixture.conn.execute(
            "SELECT COUNT(*) FROM watches WHERE kind='round' AND archive_url IS NULL"
        ).fetchone()[0]
        == 0
    )


def test_cooldown_is_preserved_instead_of_reset_for_backfill(fixture):
    accept(fixture)
    review_contract(fixture)
    first = dispatch(fixture, recorder())
    future = (fixture.clock.now() + timedelta(hours=1)).isoformat()
    fixture.conn.execute(
        "UPDATE watches SET next_check_at=? WHERE watch_id=?", (future, first.watch_id)
    )
    fetcher = recorder()
    assert dispatch(fixture, fetcher).reason == "watch_cooldown"
    assert not fetcher.calls
    assert (
        fixture.conn.execute(
            "SELECT next_check_at FROM watches WHERE watch_id=?", (first.watch_id,)
        ).fetchone()[0]
        == future
    )


def test_h7_blocked_empty_table_advances_capture_even_when_transport_parse_status_is_pending(
    fixture,
):
    from pathlib import Path

    from test_admission import Corpus

    from swingset.fetch.wayback import schedule_capture
    from swingset.history.captures import capture_state
    from swingset.history.platform import PlannedPage
    from swingset.schedule.parse import parse_snapshot
    from swingset.sources.base import WatchSpec
    from swingset.state.work import WorkUnit

    corpus = Corpus(fixture.db)
    control = Path(
        "src/swingset/sources/eepro/fixtures/round-jjprelims-summerhummer2026-2026-09-09.body"
    ).read_bytes()
    generation, report = corpus.stage(corpus.snapshot("round-control", control), body=control)
    assert not report.failures
    corpus.review(generation)
    accept(fixture)
    url = URL + "round.htm"
    captures = tuple(
        Capture(url, c.timestamp, c.digest, c.mimetype, c.length) for c in CAPTURES[:3]
    )
    target = WatchSpec(
        "",
        "eepro",
        "round",
        "GET",
        url,
        "eepro.round",
        source_ref=EVENT.source_ref,
        archive_url=captures[0].archive_url,
    )
    planned = PlannedPage(
        EVENT, Page("eepro", EVENT.source_ref, url, "eepro.round", "round", URL), captures
    )
    with fixture.db.transaction():
        schedule_capture(fixture.conn, target, now=fixture.clock.now())
    empty = b"<table><tr><td>Novice Jack and Jill Finals</td></tr><tr><th>Place</th><th>Leader</th><th>Follower</th></tr></table>"
    archive = Archive(fixture.db.state_dir)
    client = FetchClient(
        fixture.conn,
        fixture.config,
        fixture.clock,
        archive,
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(404)
                if request.url.path == "/robots.txt"
                else httpx.Response(200, content=empty)
            )
        ),
    )
    try:
        fetched = client.fetch(target.watch_id, corpus.page, fixture.run)
        parsed = parse_snapshot(
            fixture.db,
            archive,
            WorkUnit("parse", "snapshot", fetched.snapshot_id),
            fixture.clock,
            fixture.run,
        )
    finally:
        client.close()
    assert not parsed.failed and parsed.selection in {"needs_review", "waiting_for_inputs"}
    assert (
        fixture.conn.execute(
            "SELECT parse_status FROM snapshots WHERE snapshot_id=?", (fetched.snapshot_id,)
        ).fetchone()[0]
        == "pending"
    )
    assert (
        fixture.conn.execute(
            "SELECT 1 FROM pending_work WHERE stage='parse' AND unit_id=?", (fetched.snapshot_id,)
        ).fetchone()
        is None
    )
    outcome = capture_state(fixture.conn, planned, captures[0])
    assert (outcome.state, outcome.reason) == ("incomplete", "admission_guard_incomplete")
    assert next_capture(fixture.conn, planned)[0] == captures[1]


@pytest.mark.parametrize("pause_kind", ["source", "kind"])
def test_paused_first_page_never_advances_capture_and_later_source_progresses(fixture, pause_kind):
    from dataclasses import replace

    from swingset.state.controls import Selector, change_control
    from swingset.state.findings import Finding, replace_findings

    other = KnownEvent(
        EVENT.event_id, "scoringdance", "scoringdance:1", 2019, EVENT.event_month, EVENT.end_date
    )
    other_capture = Capture(
        "https://scoring.dance/enUS/events/1/results/", "20190501000000", "other", "text/html", 100
    )
    fixture.conn.execute(
        "INSERT INTO source_event_map VALUES (?,?,?,'explicit',1)",
        (other.source, other.source_ref, other.event_id),
    )
    fixture.config = replace(
        fixture.config, sources={"eepro": SourceConfig(True), "scoringdance": SourceConfig(True)}
    )
    plan = plan_platform(
        (EVENT, other),
        tuple(("eepro", capture) for capture in CAPTURES) + (("scoringdance", other_capture),),
    )
    assert [item.page.source for item in plan.pages] == ["eepro", "scoringdance"]
    accept(fixture)
    review_contract(fixture)
    review_contract(fixture, "scoringdance.event")
    first = dispatch(fixture, recorder(), plan=plan)
    assert first.capture == CAPTURES[0]
    failed_snapshot(fixture, first.watch_id, CAPTURES[0], "paused-page")
    assert next_capture(fixture.conn, plan.pages[0], now=fixture.clock.now())[0] == CAPTURES[1]
    replace_findings(
        fixture.conn,
        owner_kind="test",
        owner_id=first.watch_id,
        findings=(
            Finding("manual_review", "watch", first.watch_id, "warning", "review first page", {}),
        ),
        opened_at=fixture.clock.now().isoformat(),
        run_id=fixture.run,
    )
    selector = Selector(pause_kind, "eepro" if pause_kind == "source" else "manual_review")
    change_control(
        fixture.db.state_dir,
        selector=selector,
        paused=True,
        actor="operator",
        reason="first page hold",
        now=fixture.clock.now(),
    )
    before = dict(
        fixture.conn.execute("SELECT * FROM watches WHERE watch_id=?", (first.watch_id,)).fetchone()
    )
    fetcher = recorder()
    later = dispatch(fixture, fetcher, plan=plan)
    assert later.capture == other_capture
    assert fetcher.calls[0][1] == "scoringdance.event"
    assert (
        dict(
            fixture.conn.execute(
                "SELECT * FROM watches WHERE watch_id=?", (first.watch_id,)
            ).fetchone()
        )
        == before
    )
    assert (
        fixture.conn.execute(
            "SELECT count(*) FROM snapshots WHERE watch_id=?", (first.watch_id,)
        ).fetchone()[0]
        == 1
    )
    change_control(
        fixture.db.state_dir,
        selector=selector,
        paused=False,
        actor="operator",
        reason="reviewed",
        now=fixture.clock.now(),
    )
    resumed = dispatch(fixture, recorder(), plan=plan)
    assert resumed.watch_id == first.watch_id and resumed.capture == CAPTURES[1]


def test_allocated_offer_is_read_only_and_ignores_ordinary_backlog(fixture):
    from swingset.history.backfill import offer

    accept(fixture)
    review_contract(fixture)
    fixture.conn.execute(
        "INSERT INTO pending_work VALUES ('link','event','unrelated',?)",
        (fixture.clock.now().isoformat(),),
    )
    offered = offer(fixture.db, fixture.config, fixture.clock)
    assert offered is not None
    assert fixture.conn.execute("SELECT count(*) FROM watches").fetchone()[0] == 0
    assert dispatch(fixture, recorder()).reason == "pipeline_work_pending"
    fetcher = recorder()
    deadline = fixture.clock.now() + timedelta(seconds=60)
    result = dispatch_one(
        fixture.db,
        fixture.config,
        fixture.clock,
        fixture.run,
        deadline=deadline,
        fetcher=fetcher,
        allocated=True,
        target_watch_id=offered.watch_id,
    )
    assert result.watch_id == offered.watch_id
    assert fetcher.calls[0][2] == deadline
    assert fixture.conn.execute("SELECT count(*) FROM pending_work").fetchone()[0] == 1


def test_all_historical_offers_are_visible_without_creating_controls(fixture):
    from swingset.history.backfill import offer, offers

    review_contract(fixture)
    other = KnownEvent(
        EVENT.event_id, "eepro", "eepro:other2019", 2019, EVENT.event_month, EVENT.end_date
    )
    fixture.conn.execute(
        "INSERT INTO source_event_map VALUES (?,?,?,'explicit',1)",
        (other.source, other.source_ref, other.event_id),
    )
    accept(fixture)
    other_capture = Capture(
        "https://eepro.com/results/other2019/", "20190501000000", "other", "text/html", 100
    )
    plan = plan_platform((EVENT, other), (("eepro", CAPTURES[0]), ("eepro", other_capture)))
    before = fixture.conn.total_changes
    candidates = offers(fixture.db, fixture.config, fixture.clock, plan=plan)
    assert {page.source_ref for page in candidates} == {EVENT.source_ref, other.source_ref}
    assert offer(fixture.db, fixture.config, fixture.clock, plan=plan) == candidates[0]
    assert fixture.conn.total_changes == before
    assert not fixture.conn.execute("SELECT 1 FROM watches").fetchone()
    # Merely exposing more candidates grants neither year acceptance nor a
    # capture pointer. Every offered page still passes the original gate.
    fixture.conn.execute("DELETE FROM history_acceptance")
    assert offers(fixture.db, fixture.config, fixture.clock, plan=plan) == ()


def test_pause_after_offer_prevents_allocated_capture_advancement(fixture):
    from swingset.history.backfill import offer
    from swingset.state.controls import Selector, change_control

    accept(fixture)
    review_contract(fixture)
    offered = offer(fixture.db, fixture.config, fixture.clock)
    assert offered is not None
    change_control(
        fixture.db.state_dir,
        selector=Selector("source", "eepro"),
        paused=True,
        actor="operator",
        reason="pause after selection",
        now=fixture.clock.now(),
    )
    fetcher = recorder()
    result = dispatch_one(
        fixture.db,
        fixture.config,
        fixture.clock,
        fixture.run,
        deadline=fixture.clock.now() + timedelta(seconds=60),
        fetcher=fetcher,
        allocated=True,
        target_watch_id=offered.watch_id,
    )
    assert result.reason == "operator_pause"
    assert not fetcher.calls
    assert fixture.conn.execute("SELECT count(*) FROM watches").fetchone()[0] == 0
