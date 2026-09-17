"""WP16 uses retained archive proof and the ordinary actual-host acquisition gates."""

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from materialized_fixture import materialize_seeded_outputs
from test_platform_backfill import EVENT, URL, accept, review_contract
from test_platform_backfill import fixture as fixture

from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.history.backfill import dispatch_one, offer
from swingset.history.origin_dispatch import cadence_reason, candidate, request_gate, schedule
from swingset.history.platform import Page, PlannedPage, PlatformPlan
from swingset.schedule.fairness import next_watch
from swingset.sources import get_page_kind
from swingset.state.controls import Selector, change_control

BODY = Path("tests/fixtures/sources/eepro-archive/freedomswing2019.html").read_bytes()


@pytest.fixture
def origin(fixture):
    fixture.conn.execute("DELETE FROM archive_captures")
    fixture.config = Config(
        {
            "eepro.com": HostConfig(daily_request_budget=5),
            "web.archive.org": HostConfig(min_gap_seconds=10),
        },
        {"eepro": SourceConfig(True)},
    )
    fixture.plan = PlatformPlan(
        (
            PlannedPage(
                EVENT, Page("eepro", EVENT.source_ref, URL, "eepro.autoindex", "autoindex"), ()
            ),
        ),
        (),
    )
    queries(fixture)
    accept(fixture)
    review_contract(fixture)
    return fixture


def queries(f):
    f.conn.executemany(
        "INSERT INTO archive_queries VALUES (?,?,?,?,0,0,?,?)",
        [
            (
                f"q-{year}",
                "eepro",
                "eepro.com/results/*",
                year,
                f.clock.now().isoformat(),
                f.clock.now().isoformat(),
            )
            for year in range(2010, f.clock.now().year + 1)
        ],
    )


def run(f, handler, *, run_id=None):
    client = FetchClient(
        f.conn,
        f.config,
        f.clock,
        Archive(f.db.state_dir),
        transport=httpx.MockTransport(handler),
        random_value=lambda: 0,
    )
    try:
        return dispatch_one(
            f.db,
            f.config,
            f.clock,
            run_id or f.run,
            deadline=f.clock.now() + timedelta(minutes=10),
            fetcher=client,
            plan=f.plan,
            allocated=True,
        )
    finally:
        client.close()


def test_origin_dispatch_uses_actual_host_budget_and_retains_proof(origin):
    calls = []

    def response(request):
        calls.append((str(request.url), origin.clock.now()))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(
                200, content=b"<html><title>Index of /results/example2019/</title></html>"
            )
        )

    result = run(origin, response)
    assert result.reason == "dispatched" and result.capture is None
    assert result.result.snapshot_id and result.result.skipped is None
    assert len(calls) == 2 and (calls[1][1] - calls[0][1]).total_seconds() >= 5
    assert {row[0] for row in origin.conn.execute("SELECT host FROM host_budget")} == {"eepro.com"}
    assert origin.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 2
    assert origin.conn.execute("SELECT COUNT(*) FROM history_origin_requests").fetchone()[0] == 2
    intent = origin.conn.execute("SELECT * FROM history_origin_intents").fetchone()
    assert intent["dispatch_run_id"] is None
    assert json.loads(intent["evidence_json"])["reason"] == "archive_absence_proven"
    snapshot = origin.conn.execute("SELECT via,url,archive_url FROM snapshots").fetchone()
    assert tuple(snapshot) == ("origin", URL, None)
    # The normal due-watch queue cannot repeat a managed history operation.
    assert next_watch(origin.conn, origin.config, now=origin.clock.now()) is None


def test_unaccepted_year_or_incomplete_search_never_creates_origin_control(origin):
    origin.conn.execute("DELETE FROM archive_queries WHERE year=2014")
    result = run(origin, lambda _: pytest.fail("no HTTP without query closure"))
    assert result.reason == "archive_search_incomplete"
    assert origin.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0
    origin.conn.execute("DELETE FROM history_acceptance")
    result = run(origin, lambda _: pytest.fail("no HTTP without owner year acceptance"))
    assert result.watch_id is None
    assert origin.conn.execute("SELECT COUNT(*) FROM history_origin_intents").fetchone()[0] == 0


@pytest.mark.parametrize(
    "selector",
    [
        Selector("source", "eepro"),
        Selector("kind", "source_event_mapping"),
        Selector("host", "eepro.com"),
    ],
)
def test_pause_spends_neither_requests_nor_event_cadence(origin, selector):
    change_control(
        origin.db.state_dir,
        selector=selector,
        paused=True,
        actor="test",
        reason="private note",
        now=origin.clock.now(),
        hosts=("eepro.com", "web.archive.org"),
    )
    result = run(origin, lambda _: pytest.fail("paused origin made HTTP request"))
    assert result.watch_id is None
    assert origin.conn.execute("SELECT COUNT(*) FROM history_origin_requests").fetchone()[0] == 0
    assert origin.conn.execute("SELECT COUNT(*) FROM host_budget").fetchone()[0] == 0


def test_depleted_actual_host_budget_does_not_consume_origin_event(origin):
    origin.conn.execute(
        "INSERT INTO host_budget VALUES ('eepro.com',?,5,0)",
        (origin.clock.now().date().isoformat(),),
    )
    result = run(origin, lambda _: pytest.fail("origin cap must prevent HTTP"))
    assert result.result.skipped == "request budget"
    assert origin.conn.execute("SELECT COUNT(*) FROM history_origin_requests").fetchone()[0] == 0
    assert origin.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 5


def test_robots_then_changed_policy_blocks_page_before_second_debit(origin):
    calls = []

    def response(request):
        calls.append(str(request.url))
        origin.conn.execute("UPDATE admission_policies SET mode='shadow'")
        return httpx.Response(404)

    result = run(origin, response)
    assert len(calls) == 1 and calls[0].endswith("/robots.txt")
    assert result.result.skipped == "history_contract_not_enforced"
    assert origin.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 1
    assert origin.conn.execute("SELECT COUNT(*) FROM history_origin_requests").fetchone()[0] == 1


def test_origin_redirect_cannot_expand_known_locator(origin):
    calls = []

    def response(request):
        calls.append(str(request.url))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(302, headers={"Location": "https://eepro.com/results/unapproved/"})
        )

    result = run(origin, response)
    assert len(calls) == 2
    assert result.result.skipped == "origin_redirect_unproven"
    assert origin.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 2


def test_direct_fetch_cannot_reuse_managed_origin_dispatch_reservation(origin):
    spec, proof, reason = candidate(
        origin.db, origin.config, origin.clock, origin.plan.pages[0], origin.plan, run_id=origin.run
    )
    assert reason == "eligible_origin"
    with origin.db.transaction():
        schedule(origin.conn, spec, proof, run_id=origin.run, now=origin.clock.now())
        origin.conn.execute("UPDATE history_origin_intents SET dispatch_run_id=NULL")
    client = FetchClient(
        origin.conn,
        origin.config,
        origin.clock,
        Archive(origin.db.state_dir),
        transport=httpx.MockTransport(
            lambda _: pytest.fail("direct origin fetch bypassed dispatcher")
        ),
    )
    try:
        result = client.fetch(spec.watch_id, get_page_kind(spec.parser), origin.run)
        assert result.skipped == "origin_dispatch_required"
    finally:
        client.close()


@pytest.mark.parametrize(
    "redirect",
    [
        "https://[eepro.com/robots.txt",
        "https://eepro.com:bad/robots.txt",
        "https://eepro.com:65536/robots.txt",
        "https://eepro.com:8443/robots.txt",
    ],
)
def test_managed_request_gate_rejects_malformed_redirect_without_debit(origin, redirect):
    spec, proof, reason = candidate(
        origin.db, origin.config, origin.clock, origin.plan.pages[0], origin.plan, run_id=origin.run
    )
    assert reason == "eligible_origin"
    with origin.db.transaction():
        schedule(origin.conn, spec, proof, run_id=origin.run, now=origin.clock.now())
    watch = SimpleNamespace(
        **dict(
            origin.conn.execute(
                "SELECT * FROM watches WHERE watch_id=?", (spec.watch_id,)
            ).fetchone()
        ),
        _origin_run_id=origin.run,
    )
    kwargs = dict(now=origin.clock.now(), history_start=origin.config.history_start)
    assert request_gate(origin.conn, watch, request_url=URL, **kwargs) is None
    assert (
        request_gate(origin.conn, watch, request_url=redirect, **kwargs)
        == "origin_redirect_unproven"
    )
    for table in (
        "host_budget",
        "history_origin_requests",
        "scheduler_requests",
        "execution_admissions",
    ):
        assert origin.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_failed_planning_rolls_back_watch_and_intent(origin, monkeypatch):
    import swingset.history.backfill as backfill

    def crash(*args, **kwargs):
        raise RuntimeError("before commit")

    monkeypatch.setattr(backfill, "replace_findings", crash)
    with pytest.raises(RuntimeError, match="before commit"):
        run(origin, lambda _: pytest.fail("planning crash must not issue HTTP"))
    assert origin.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0
    assert origin.conn.execute("SELECT COUNT(*) FROM history_origin_intents").fetchone()[0] == 0


def test_origin_parent_retains_file_rows_and_existing_paused_child(origin):
    from swingset.schedule.parse import parse_snapshot
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.base import ParseContext
    from swingset.sources.eepro.adapter import AutoIndexPage
    from swingset.state.work import WorkUnit

    page = AutoIndexPage()
    declared = page.parse(
        page.extract(BODY),
        ParseContext(
            "raw",
            "parent",
            URL,
            "eepro",
            page.kind,
            EVENT.source_ref,
            origin.clock.now().isoformat(),
        ),
    )
    child = declared.watches[0]
    upsert_watch(origin.conn, child, origin.clock.now())
    origin.conn.execute(
        "UPDATE watches SET state='paused',next_check_at=NULL,notes='operator child',paused_until=? WHERE watch_id=?",
        ((origin.clock.now() + timedelta(days=30)).isoformat(), child.watch_id),
    )
    preserved = dict(
        origin.conn.execute("SELECT * FROM watches WHERE watch_id=?", (child.watch_id,)).fetchone()
    )
    materialize_seeded_outputs(origin.db, now=origin.clock.now(), run_id=origin.run)
    fetched = run(
        origin,
        lambda request: (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=BODY)
        ),
    )
    assert fetched.result.snapshot_id
    attempt = parse_snapshot(
        origin.db,
        Archive(origin.db.state_dir),
        WorkUnit("parse", "snapshot", fetched.result.snapshot_id),
        origin.clock,
        origin.run,
    )
    assert not attempt.failed
    assert len(declared.watches) == 7
    assert (
        origin.conn.execute("SELECT COUNT(*) FROM observations WHERE kind='file_row'").fetchone()[0]
        == 7
    )
    assert origin.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 2
    assert (
        dict(
            origin.conn.execute(
                "SELECT * FROM watches WHERE watch_id=?", (child.watch_id,)
            ).fetchone()
        )
        == preserved
    )
    retained = origin.conn.execute(
        "SELECT result_json FROM source_generations WHERE state='accepted'"
    ).fetchone()[0]
    assert child.url in retained
    assert (
        origin.conn.execute(
            "SELECT COUNT(*) FROM findings WHERE kind='acquisition_gate' AND closed_at IS NULL"
        ).fetchone()[0]
        == 6
    )


def test_actual_request_cadence_survives_restart_and_has_no_skipped_credit(origin):
    import sqlite3

    result = run(
        origin,
        lambda request: (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=BODY)
        ),
    )
    assert result.result.snapshot_id
    conn = sqlite3.connect(origin.db.state_dir / "state.sqlite")
    try:
        assert (
            cadence_reason(
                conn,
                source="eepro",
                event_id="another-event",
                run_id=origin.run,
                now=origin.clock.now(),
            )
            == "origin_event_cycle_limit"
        )
        assert (
            cadence_reason(
                conn,
                source="eepro",
                event_id=EVENT.event_id,
                run_id=origin.run,
                now=origin.clock.now(),
            )
            is None
        )
        assert (
            cadence_reason(
                conn,
                source="eepro",
                event_id="another-event",
                run_id="later-cycle",
                now=origin.clock.now(),
            )
            is None
        )
    finally:
        conn.close()


def test_dcn_daily_cadence_does_not_reset_with_new_run(origin):
    # Actual debit receipts use a source field so the daily rule is independent
    # of adapter activation. No DCN page policy is activated by this ledger test.
    from swingset.history.origin_dispatch import record_request
    from swingset.state.controls import ActionScope, admission, settle

    spec, proof, _ = candidate(
        origin.db, origin.config, origin.clock, origin.plan.pages[0], origin.plan, run_id=origin.run
    )
    with origin.db.transaction():
        schedule(origin.conn, spec, proof, run_id=origin.run, now=origin.clock.now())
        origin.conn.execute("UPDATE history_origin_intents SET source='dcn'")
    watch = SimpleNamespace(watch_id=spec.watch_id, _origin_run_id=origin.run)
    with admission(
        origin.db,
        action_id="actual-ledger-test",
        action_kind="request",
        scope=ActionScope(host="danceconvention.net"),
        now=origin.clock.now(),
    ):
        record_request(origin.conn, watch, action_id="actual-ledger-test", now=origin.clock.now())
    with origin.db.transaction():
        settle(origin.conn, "actual-ledger-test", now=origin.clock.now(), outcome="Ok")
    assert (
        cadence_reason(
            origin.conn,
            source="dcn",
            event_id="another-event",
            run_id="new-run",
            now=origin.clock.now(),
        )
        == "origin_event_daily_limit"
    )
    assert (
        cadence_reason(
            origin.conn,
            source="dcn",
            event_id=EVENT.event_id,
            run_id="new-run",
            now=origin.clock.now(),
        )
        is None
    )
    assert (
        cadence_reason(
            origin.conn,
            source="dcn",
            event_id="another-event",
            run_id="new-run",
            now=origin.clock.now() + timedelta(days=1),
        )
        is None
    )


def test_unrelated_origin_control_is_preserved_instead_of_bypassing_its_cadence(origin):
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.base import WatchSpec

    spec = WatchSpec(
        "", "eepro", "autoindex", "GET", URL, "eepro.autoindex", source_ref=EVENT.source_ref
    )
    upsert_watch(origin.conn, spec, origin.clock.now())
    origin.conn.execute(
        "UPDATE watches SET notes='existing live control',state='live' WHERE watch_id=?",
        (spec.watch_id,),
    )
    before = tuple(origin.conn.execute("SELECT * FROM watches").fetchone())
    result = run(origin, lambda _: pytest.fail("must not hijack independent polling"))
    assert result.reason == "independent_origin_control"
    assert tuple(origin.conn.execute("SELECT * FROM watches").fetchone()) == before
    assert origin.conn.execute("SELECT COUNT(*) FROM history_origin_intents").fetchone()[0] == 0


def test_same_source_archive_offer_precedes_origin_gap(origin):
    from dataclasses import replace

    from test_platform_backfill import CAPTURES, recorder

    from swingset.history.backfill import dispatch_one
    from swingset.history.platform import PlannedPage

    # A newer origin gap must not consume service while this source still has
    # an eligible retained capture. Both are independently mapped known events.
    older = replace(EVENT, event_id="2019-01-older", source_ref="eepro:older2019")
    columns = [r[1] for r in origin.conn.execute("PRAGMA table_info(events)")]
    row = dict(origin.conn.execute("SELECT * FROM events").fetchone())
    row["event_id"] = older.event_id
    origin.conn.execute(
        f"INSERT INTO events VALUES ({','.join('?' for _ in columns)})",
        tuple(row[c] for c in columns),
    )
    origin.conn.execute(
        "INSERT INTO source_event_map VALUES (?,?,?,'explicit',1)",
        ("eepro", older.source_ref, older.event_id),
    )
    accept(origin)
    old_url = "https://eepro.com/results/older2019/"
    capture = replace(CAPTURES[0], url=old_url)
    origin.conn.execute(
        "INSERT INTO archive_captures VALUES (?,?,?,?,200,?,?,?,'retained')",
        (
            "eepro",
            capture.url,
            capture.timestamp,
            capture.digest,
            capture.mimetype,
            capture.length,
            origin.clock.now().isoformat(),
        ),
    )
    old_page = PlannedPage(
        older, Page("eepro", older.source_ref, old_url, "eepro.autoindex", "autoindex"), (capture,)
    )
    plan = PlatformPlan((*origin.plan.pages, old_page), ())
    offered = offer(origin.db, origin.config, origin.clock, plan=plan, run_id=origin.run)
    assert offered.url == old_url and offered.archive_url == capture.archive_url
    result = dispatch_one(
        origin.db,
        origin.config,
        origin.clock,
        origin.run,
        deadline=origin.clock.now() + timedelta(minutes=10),
        fetcher=recorder(),
        plan=plan,
        allocated=True,
    )
    assert result.capture == capture
    assert origin.conn.execute("SELECT COUNT(*) FROM history_origin_intents").fetchone()[0] == 0


def test_cycle_dispatches_origin_with_actual_host_and_same_cycle_interpretation(
    fixture, tmp_path, monkeypatch
):
    from test_history_cycle import prepare_cycle

    from swingset.history import backfill
    from swingset.schedule import cycle

    config_dir, overrides_dir, target = prepare_cycle(fixture, tmp_path, monkeypatch)
    fixture.conn.execute("DELETE FROM archive_captures")
    queries(fixture)
    plan = PlatformPlan(
        (
            PlannedPage(
                EVENT, Page("eepro", EVENT.source_ref, URL, "eepro.autoindex", "autoindex"), ()
            ),
        ),
        (),
    )
    monkeypatch.setattr(backfill, "retained_plan", lambda *args, **kwargs: plan)
    seen = []

    def response(request):
        seen.append(str(request.url))
        assert request.url.host == "eepro.com"
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=BODY)
        )

    existing_rounds = {
        row[0] for row in fixture.conn.execute("SELECT watch_id FROM watches WHERE kind='round'")
    }
    result = cycle.run_cycle(
        fixture.db,
        config_dir=config_dir,
        overrides_dir=overrides_dir,
        clock=fixture.clock,
        budget=180,
        transport=httpx.MockTransport(response),
    )
    assert result["checked"] == 1
    assert len(seen) == 2
    assert fixture.conn.execute("SELECT COUNT(*) FROM history_origin_requests").fetchone()[0] == 2
    assert (
        fixture.conn.execute("SELECT COUNT(*) FROM observations WHERE kind='file_row'").fetchone()[
            0
        ]
        == 7
    )
    assert {
        row[0] for row in fixture.conn.execute("SELECT watch_id FROM watches WHERE kind='round'")
    } == existing_rounds


def test_archive_host_pause_does_not_pause_proven_origin_gap(origin):
    change_control(
        origin.db.state_dir,
        selector=Selector("host", "web.archive.org"),
        paused=True,
        actor="test",
        reason="archive host only",
        now=origin.clock.now(),
        hosts=("eepro.com", "web.archive.org"),
    )
    result = run(
        origin,
        lambda request: (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=BODY)
        ),
    )
    assert result.result.snapshot_id
    assert (
        origin.conn.execute("SELECT requests FROM host_budget WHERE host='eepro.com'").fetchone()[0]
        == 2
    )
    assert (
        origin.conn.execute("SELECT 1 FROM host_budget WHERE host='web.archive.org'").fetchone()
        is None
    )


def test_capture_arriving_during_robots_prevents_origin_page_request(origin):
    from test_platform_backfill import CAPTURES

    requests = []

    def response(request):
        requests.append(str(request.url))
        capture = CAPTURES[0]
        origin.conn.execute(
            "INSERT INTO archive_captures VALUES (?,?,?,?,200,?,?,?,'retained')",
            (
                "eepro",
                capture.url,
                capture.timestamp,
                capture.digest,
                capture.mimetype,
                capture.length,
                origin.clock.now().isoformat(),
            ),
        )
        return httpx.Response(404)

    result = run(origin, response)
    assert requests == ["https://eepro.com/robots.txt"]
    assert result.result.skipped == "archive_acquisition_or_interpretation_pending"
    assert origin.conn.execute("SELECT requests FROM host_budget").fetchone()[0] == 1


def test_schema15_adds_only_empty_intent_receipts_preserving_prior_evidence(tmp_path, monkeypatch):
    from swingset.clock import FakeClock
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.base import WatchSpec
    from swingset.state import db as db_module

    clock = FakeClock()
    with monkeypatch.context() as old:
        old.setattr(db_module, "SCHEMA_VERSION", 14)
        with db_module.open_database(tmp_path) as database:
            database.start_run(clock.now())
            upsert_watch(
                database.connection,
                WatchSpec(
                    "",
                    "eepro",
                    "autoindex",
                    "GET",
                    URL,
                    "eepro.autoindex",
                    source_ref=EVENT.source_ref,
                ),
                clock.now(),
            )
            database.connection.execute(
                "UPDATE watches SET notes='existing operator evidence',state='paused',next_check_at=NULL"
            )
            names = [
                row[0]
                for row in database.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            before = {
                name: sorted(
                    tuple(row) for row in database.connection.execute(f'SELECT * FROM "{name}"')
                )
                for name in names
                if name != "meta"
            }
            meta = dict(
                database.connection.execute(
                    "SELECT key,value FROM meta WHERE key!='schema_version'"
                )
            )
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 15)
    with db_module.open_database(tmp_path) as database:
        assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 15
        assert {
            name: sorted(
                tuple(row) for row in database.connection.execute(f'SELECT * FROM "{name}"')
            )
            for name in names
            if name != "meta"
        } == before
        assert (
            dict(
                database.connection.execute(
                    "SELECT key,value FROM meta WHERE key!='schema_version'"
                )
            )
            == meta
        )
        for table in (
            "history_origin_intents",
            "history_origin_requests",
            "history_origin_operator_refs",
        ):
            assert database.connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0
        assert database.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_operator_receipt_is_separate_from_acquisition_and_year_acceptance(origin):
    from swingset.history.origin_dispatch import record_operator_reference

    with origin.db.transaction():
        with pytest.raises(ValueError, match="known event mapping"):
            record_operator_reference(
                origin.conn,
                source_ref="eepro:guessed2012",
                evidence_reference="operator conversation",
                recorded_at=origin.clock.now(),
            )
        record_operator_reference(
            origin.conn,
            source_ref=EVENT.source_ref,
            evidence_reference="retained operator correspondence #21",
            recorded_at=origin.clock.now(),
        )
    assert origin.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0] == 0
    assert origin.conn.execute("SELECT COUNT(*) FROM history_origin_requests").fetchone()[0] == 0


def test_three_failed_distinct_archive_captures_allow_proved_origin_fallback(origin):
    from test_platform_backfill import CAPTURES, failed_snapshot, recorder

    captures = CAPTURES
    origin.conn.executemany(
        "INSERT INTO archive_captures VALUES (?,?,?,?,200,?,?,?,'retained')",
        [
            (
                "eepro",
                c.url,
                c.timestamp,
                c.digest,
                c.mimetype,
                c.length,
                origin.clock.now().isoformat(),
            )
            for c in captures
        ],
    )
    origin.plan = PlatformPlan((PlannedPage(EVENT, origin.plan.pages[0].page, captures[:3]),), ())
    for number, capture in enumerate(captures[:3]):
        selected = dispatch_one(
            origin.db,
            origin.config,
            origin.clock,
            origin.run,
            deadline=origin.clock.now() + timedelta(minutes=10),
            fetcher=recorder(),
            plan=origin.plan,
            allocated=True,
        )
        assert selected.capture == capture
        failed_snapshot(origin, selected.watch_id, capture, number)
    calls = []

    def response(request):
        calls.append(str(request.url))
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=BODY)
        )

    fetched = run(origin, response)
    assert fetched.capture is None and fetched.result.snapshot_id
    assert all(url.startswith("https://eepro.com/") for url in calls)
    proof = json.loads(
        origin.conn.execute("SELECT evidence_json FROM history_origin_intents").fetchone()[0]
    )
    assert proof["reason"] == "archive_alternatives_unusable"
    assert proof["attempted_capture_urls"] == [capture.archive_url for capture in captures[:3]]
    assert captures[3].archive_url not in proof["attempted_capture_urls"]


@pytest.mark.parametrize("change", ["revoke", "parent_policy"])
@pytest.mark.parametrize("parent_via", ["origin", "wayback"])
def test_round_debit_rechecks_parent_after_robots(origin, change, parent_via):
    from swingset.admission.select import revoke_generation
    from swingset.schedule.parse import parse_snapshot
    from swingset.state.work import WorkUnit

    if parent_via == "wayback":
        from test_platform_backfill import CAPTURES, accepted_parent

        origin.conn.executemany(
            "INSERT INTO archive_captures VALUES (?,?,?,?,200,?,?,?,'retained')",
            [
                (
                    "eepro",
                    c.url,
                    c.timestamp,
                    c.digest,
                    c.mimetype,
                    c.length,
                    origin.clock.now().isoformat(),
                )
                for c in CAPTURES
            ],
        )
        fetched, _, _ = accepted_parent(origin)
    else:
        fetched = run(
            origin,
            lambda request: (
                httpx.Response(404)
                if request.url.path == "/robots.txt"
                else httpx.Response(200, content=BODY)
            ),
        )
        parsed = parse_snapshot(
            origin.db,
            Archive(origin.db.state_dir),
            WorkUnit("parse", "snapshot", fetched.result.snapshot_id),
            origin.clock,
            origin.run,
        )
        assert not parsed.failed
    parent = origin.conn.execute(
        "SELECT g.generation_id,g.result_json FROM source_units u JOIN source_generations g "
        "ON g.generation_id=u.accepted_generation_id WHERE u.watch_id=?",
        (fetched.watch_id,),
    ).fetchone()
    from swingset.admission.generations import deserialize_result

    child = deserialize_result(parent["result_json"]).watches[0]
    accept(origin)
    review_contract(origin, child.parser)
    origin.plan = PlatformPlan(
        (
            PlannedPage(
                EVENT, Page("eepro", EVENT.source_ref, URL, "eepro.autoindex", "autoindex"), ()
            ),
            PlannedPage(
                EVENT,
                Page(child.source, child.source_ref, child.url, child.parser, child.kind, URL),
                (),
            ),
        ),
        (),
    )
    # A fresh robots check creates a real request boundary after planning. The
    # already admitted partial parent still supplies this exact child locator.
    origin.conn.execute("UPDATE hosts SET robots_fetched_at=NULL")
    item = origin.plan.pages[1]
    spec, _, reason = candidate(
        origin.db, origin.config, origin.clock, item, origin.plan, run_id=origin.run
    )
    assert reason == "eligible_origin" and spec.watch_id == child.watch_id
    from dataclasses import replace

    from swingset.history.origin_dispatch import _parent_admitted

    assert not _parent_admitted(origin.conn, replace(item.page, page_kind="eepro.autoindex"))
    before_requests = origin.conn.execute("SELECT SUM(requests) FROM host_budget").fetchone()[0]
    calls = []

    def response(request):
        calls.append(str(request.url))
        assert request.url.path == "/robots.txt", "withdrawn parent must prevent round HTTP"
        if change == "revoke":
            revoke_generation(
                origin.db,
                parent["generation_id"],
                reason="offline parent revocation",
                now=origin.clock.now().isoformat(),
                run_id=origin.run,
            )
        else:
            origin.conn.execute(
                "UPDATE admission_policies SET mode='shadow' WHERE page_kind='eepro.autoindex'"
            )
        return httpx.Response(404)

    result = run(origin, response)
    assert result.watch_id == child.watch_id
    assert result.result.skipped == "parent_document_pending"
    assert len(calls) == 1
    assert (
        origin.conn.execute("SELECT SUM(requests) FROM host_budget").fetchone()[0]
        == before_requests + 1
    )
    assert (
        origin.conn.execute(
            "SELECT count(*) FROM history_origin_requests WHERE watch_id=?", (child.watch_id,)
        ).fetchone()[0]
        == 1
    )
    assert not origin.conn.execute(
        "SELECT 1 FROM snapshots WHERE watch_id=?", (child.watch_id,)
    ).fetchone()
