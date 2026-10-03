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


def test_a_paced_inventory_leaves_phase_two_open_for_an_accepted_year(fixture):
    """A dancer change queues the inventory, and the pacing gate holds it for
    hours (D-0210). The phase-two gate then reads the committed event list, a
    fact (I5), instead of closing every year until the inventory runs."""
    from materialized_fixture import output_rows

    from swingset.history.acquisition import phase_two_refusals
    from swingset.schedule import pacing
    from swingset.schedule.fair_policy import SchedulerConfig
    from swingset.state import derivations
    from swingset.state.work import WorkUnit, enqueue

    accept(fixture)
    review_contract(fixture)

    def gate():
        return phase_two_gate(
            fixture.conn, source="eepro", source_ref=EVENT.source_ref, page_kind="eepro.autoindex"
        )

    assert gate() is None
    # The cycle runs the changed dancer; the inventory it re-queued waits.
    dancer = WorkUnit("project", "dancer", "999")
    enqueue(fixture.conn, (dancer,), enqueued_at=fixture.clock.now().isoformat())
    derivations.refresh(fixture.conn, fixture.clock.now())
    with fixture.db.transaction():
        selection = derivations.capture(fixture.conn, dancer, now=fixture.clock.now())
        derivations.complete(
            fixture.conn,
            selection,
            rows=output_rows(fixture.conn, dancer),
            now=fixture.clock.now(),
            run_id=fixture.run,
        )
    assert WorkUnit("project", "inventory", "all") in set(
        derivations.pending_units(fixture.conn, "project")
    )
    paced = SchedulerConfig(history_interval_seconds=43200, inventory_interval_seconds=43200)
    # Outside a run that names its scheduler nothing is held: it waits.
    assert gate() == "history_year_unaccepted"
    with pytest.raises(ValueError, match="projection is pending"):
        accept_year(
            fixture.conn, 2019, accepted_by="owner", accepted_at=fixture.clock.now().isoformat()
        )
    with phase_two_refusals() as refused, pacing.configured(paced, fixture.clock.now):
        assert gate() is None
        assert gate() is None
        # accept-year names its scheduler too: it records the committed list.
        accept_year(
            fixture.conn, 2019, accepted_by="owner", accepted_at=fixture.clock.now().isoformat()
        )
        assert gate() is None
    assert refused == {}
    # Once its interval ends the inventory is due and blocks again, and the
    # refusal is counted for the cycle summary (I4).
    later = fixture.clock.now() + timedelta(hours=13)
    with phase_two_refusals() as refused, pacing.configured(paced, lambda: later):
        assert gate() == "history_year_unaccepted"
    assert refused == {"history_year_unaccepted": 1}


def _dancer_change(fixture):
    """A changed dancer materializes and re-queues the inventory."""
    from materialized_fixture import output_rows

    from swingset.state import derivations
    from swingset.state.work import WorkUnit, enqueue

    dancer = WorkUnit("project", "dancer", "999")
    enqueue(fixture.conn, (dancer,), enqueued_at=fixture.clock.now().isoformat())
    derivations.refresh(fixture.conn, fixture.clock.now())
    with fixture.db.transaction():
        selection = derivations.capture(fixture.conn, dancer, now=fixture.clock.now())
        derivations.complete(
            fixture.conn,
            selection,
            rows=output_rows(fixture.conn, dancer),
            now=fixture.clock.now(),
            run_id=fixture.run,
        )


@pytest.mark.parametrize("dancer_changed", [True, False])
def test_a_pending_mapping_blocks_phase_two_unless_the_inventory_is_held(fixture, dancer_changed):
    """The gate skips mappings and resolutions only behind an inventory the
    pacing gate holds: they need a current inventory, so they cannot write
    until that one runs. A current inventory made within its interval holds
    nothing, and a pending mapping behind it can change the event list now."""
    from swingset.schedule import pacing
    from swingset.schedule.fair_policy import SchedulerConfig
    from swingset.state import derivations
    from swingset.state.work import WorkUnit

    accept(fixture)
    review_contract(fixture)
    if dancer_changed:
        _dancer_change(fixture)
    # An inventory cutoff that changed live rows moves its replay epoch: the
    # mapping is pending while the inventory keeps its generation.
    with fixture.db.transaction():
        derivations.bump_replay(fixture.conn, "project", "inventory")
    pending = set(derivations.pending_units(fixture.conn, "project"))
    assert WorkUnit("project", "mapping", "eepro") in pending
    assert (WorkUnit("project", "inventory", "all") in pending) is dancer_changed
    paced = SchedulerConfig(history_interval_seconds=43200, inventory_interval_seconds=43200)
    with pacing.configured(paced, fixture.clock.now):
        assert pacing.holds(fixture.conn, pacing.INVENTORY) is dancer_changed
        reason = phase_two_gate(
            fixture.conn, source="eepro", source_ref=EVENT.source_ref, page_kind="eepro.autoindex"
        )
    assert reason == (None if dancer_changed else "history_year_unaccepted")


@pytest.mark.parametrize("paced", [True, False])
def test_the_accept_year_command_names_its_scheduler(fixture, tmp_path, monkeypatch, paced):
    """`swingset accept-year` configures the pacing gate from its config: a
    year can be accepted while the gate holds the inventory (D-0210)."""
    from contextlib import contextmanager

    from swingset import cli

    review_contract(fixture)
    materialize_seeded_outputs(fixture.db, now=fixture.clock.now(), run_id=fixture.run)
    _dancer_change(fixture)

    @contextmanager
    def the_fixture(*_args, **_kwargs):
        yield fixture.db

    monkeypatch.setattr(cli, "open_database", the_fixture)
    monkeypatch.setattr(cli, "SystemClock", lambda: fixture.clock)
    config = tmp_path / "config"
    config.mkdir()
    (config / "hosts.toml").write_text("")
    scheduling = "[scheduling]\nhistory_interval_seconds = 43200\n"
    if paced:
        scheduling += "inventory_interval_seconds = 43200\n"
    (config / "sources.toml").write_text(scheduling)
    command = ["accept-year", "--year", "2019", "--by", "owner"]
    code = cli.main([*command, "--state", str(tmp_path), "--config", str(config)])
    recorded = fixture.conn.execute(
        "SELECT count(*) FROM history_acceptance WHERE year=2019"
    ).fetchone()[0]
    assert (code, recorded) == ((0, 1) if paced else (1, 0))


@pytest.mark.parametrize("on", [True, False])
def test_the_accept_year_command_applies_the_cutoff_switch(fixture, tmp_path, monkeypatch, on):
    from contextlib import contextmanager

    from swingset import cli, cutoff
    from swingset.project import history

    @contextmanager
    def the_fixture(*_args, **_kwargs):
        yield fixture.db

    seen = []

    def record(*_args, **_kwargs):
        seen.append(cutoff.enabled())
        raise ValueError("recorded")

    monkeypatch.setattr(cli, "open_database", the_fixture)
    monkeypatch.setattr(history, "accept_year", record)
    config = tmp_path / "config"
    config.mkdir()
    (config / "hosts.toml").write_text("")
    (config / "sources.toml").write_text(f"[derivation]\ncutoff = {str(on).lower()}\n")
    command = ["accept-year", "--year", "2019", "--by", "owner", "--state", str(tmp_path)]
    assert cli.main([*command, "--config", str(config)]) == 1
    assert seen == [on]


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


def test_offers_answer_the_year_gate_once_per_year_and_stop_at_the_deadline(fixture, monkeypatch):
    from datetime import timedelta

    from swingset.history import backfill
    from swingset.project import history as history_module

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
    calls = []
    original = history_module.phase_two_allowed
    walks = []
    original_pending = history_module._pending_inventory_projection

    def counted(conn, year, **kwargs):
        calls.append(year)
        return original(conn, year, **kwargs)

    def counted_pending(conn):
        walks.append(True)
        return original_pending(conn)

    monkeypatch.setattr(history_module, "phase_two_allowed", counted)
    monkeypatch.setattr(history_module, "_pending_inventory_projection", counted_pending)
    candidates = backfill.offers(fixture.db, fixture.config, fixture.clock, plan=plan)
    assert len(candidates) == 2
    # Two pages in one year: the gate that walks every pending projection unit
    # ran once, not once per page (D-0181), and the walk itself ran once for
    # the whole pass, not once per year (D-0195).
    assert calls == [2019]
    assert len(walks) == 1
    # A deadline already reached offers nothing and evaluates no page.
    calls.clear()
    reached = fixture.clock.now() - timedelta(seconds=1)
    assert (
        backfill.offers(fixture.db, fixture.config, fixture.clock, plan=plan, deadline=reached)
        == ()
    )
    assert calls == []


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


def test_allocated_dispatch_evaluates_only_its_watch_and_walks_projection_once(
    fixture, monkeypatch
):
    from swingset.history import backfill
    from swingset.project import history as history_module

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
    offered = backfill.offers(fixture.db, fixture.config, fixture.clock, plan=plan)
    target = offered[0].watch_id
    evaluated = []
    original_candidate = backfill._candidate
    walks = []
    original_pending = history_module._pending_inventory_projection

    def counted_candidate(database, config, clock, item, plan, **kwargs):
        evaluated.append(item.page.url)
        return original_candidate(database, config, clock, item, plan, **kwargs)

    def counted_pending(conn):
        walks.append(True)
        return original_pending(conn)

    monkeypatch.setattr(backfill, "_candidate", counted_candidate)
    monkeypatch.setattr(history_module, "_pending_inventory_projection", counted_pending)
    result = backfill.dispatch_one(
        fixture.db,
        fixture.config,
        fixture.clock,
        fixture.run,
        deadline=fixture.clock.now() + timedelta(minutes=5),
        fetcher=recorder(),
        plan=plan,
        allocated=True,
        target_watch_id=target,
    )
    # A turn allocated to one watch evaluates that page only, inside the
    # bounded write, and answers the projection question once (D-0195).
    assert result.watch_id == target
    assert len(evaluated) == 1
    assert len(walks) <= 1
