"""Finite offline service proves completion from artifacts, not sealed watch flags."""

import json
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from test_admission import BODY, Corpus
from test_event_enumerations import admit_parent, directory, finish_bootstrap
from test_event_pressure import enroll

from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.model.canonical import Event
from swingset.project.writer import Projection, replace_scope
from swingset.schedule import event_pressure, event_progress
from swingset.schedule.event_accounting_report import report as accounting_report
from swingset.schedule.event_inventory import inventory
from swingset.schedule.fair_policy import shares
from swingset.schedule.fairness import next_watch, prepare_event_turns, servicing
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.watches import refresh_policy, upsert_watch
from swingset.sources import get_page_kind
from swingset.sources.base import WatchSpec
from swingset.state.control_scopes import for_unit
from swingset.state.controls import ActionScope, Selector, change_control, operation
from swingset.state.db import open_database
from swingset.state.work import WorkUnit


@pytest.fixture
def cohort(tmp_path):
    db = open_database(tmp_path)
    corpus = Corpus(db)
    parent = WatchSpec(
        "",
        "eepro",
        "autoindex",
        "GET",
        "https://eepro.com/results/monterey/",
        "eepro.autoindex",
        source_ref="eepro:monterey",
    )
    upsert_watch(db.connection, parent, corpus.clock.now())
    f = SimpleNamespace(
        db=db, conn=db.connection, corpus=corpus, parent=parent, archive=Archive(tmp_path)
    )
    try:
        yield f
    finally:
        f.db.close()


def test_admitted_33_page_cohort_finishes_amid_discovery_and_current_refresh(
    cohort, record_property
):
    """One host, successful supported HTML; no network or throughput forecast.

    Synthetic EEPro-shaped fixtures, not captured Monterey source pages.
    Every loop adds a real unfetched index, and one retained live round is always
    due. Bodies go through FetchClient, parse_snapshot and enforced admission.
    The finite bound counts wire requests, including robots. No target watch is
    manually sealed or removed from scheduling to manufacture completion.
    """
    f = cohort
    clock, run = f.corpus.clock, f.corpus.run
    config = Config(
        {"eepro.com": HostConfig(min_gap_seconds=5, daily_request_budget=10000)},
        {"eepro": SourceConfig(True)},
    )
    # The fixture's reviewed round activates the contract, and supplies genuine
    # retained current demand outside the fixed 33-page source event.
    current = replace(f.corpus.spec, source_ref="eepro:live", url="https://eepro.com/live.htm")
    upsert_watch(f.conn, current, clock.now())
    ctx = f.corpus.snapshot("reviewed-live", BODY, spec=current)
    generation, inspected = f.corpus.stage(ctx)
    assert not inspected.failures
    f.corpus.review(generation)
    assert f.corpus.admit(generation) == "accepted"
    # A dated live competitor keeps the ordinary polling policy in the current
    # class. The target cohort deliberately remains unmapped and dateless.
    live_event = Event(
        event_id="fixture-live",
        series_id="fixture-live",
        name="Live competitor",
        year=clock.now().year,
        start_date=clock.now().date().isoformat(),
        end_date=clock.now().date().isoformat(),
        source="eepro",
        snapshot_id=ctx.snapshot_id,
        parser_version="1",
        first_seen_at=clock.now().isoformat(),
        last_seen_at=clock.now().isoformat(),
        run_id=run,
    )
    replace_scope(
        f.conn,
        scope_kind="event",
        scope_id="fixture-live",
        projection=Projection((live_event,)),
        run_id=run,
        projected_at=clock.now().isoformat(),
    )
    f.conn.execute(
        "INSERT INTO source_event_map VALUES ('eepro','eepro:live','fixture-live','explicit',1)"
    )
    # Corpus's unused default watch is setup scaffolding, not cohort demand.
    f.conn.execute("DELETE FROM watches WHERE watch_id=?", (f.corpus.spec.watch_id,))
    names = [f"round-{number:02}.htm" for number in range(33)]
    admit_parent(f, names)
    finish_bootstrap(f)
    enroll(f, config)
    refresh_policy(f.conn, config, f.parent.watch_id, clock.now(), jitter=0)
    with f.db.transaction(immediate=False):
        initial = inventory(
            f.conn, f.archive, source="eepro", source_ref="eepro:monterey", now=clock.now()
        )
    assert (initial["listed_pages"], initial["acquired_pages"], initial["interpreted_pages"]) == (
        33,
        0,
        0,
    )
    assert initial["canonical_event_id"] is None

    wire = []

    def respond(request):
        body = (
            b"User-agent: *\nAllow: /\n"
            if request.url.path == "/robots.txt"
            else directory(["one.htm"])
            if request.url.path.endswith("/")
            else BODY
        )
        wire.append((clock.now(), str(request.url), len(body)))
        return httpx.Response(200, content=body)

    target_urls = {f.parent.url + name for name in names}
    completed = set()
    max_watch_rows = 0
    max_due_rows = 0
    categories = Counter()
    lanes = Counter()
    target_positions = []
    paused_once = False
    # At most 600 selections / 601 requests: one reusable robots response plus
    # one successful body per choice. The 128-request maximum gap below is a
    # deterministic regression bound for q=4, listed share=50%, one arrival per
    # selection and always-ready current demand. Neither bound is a derived
    # guarantee or a calibrated operating objective.
    for number in range(600):
        arriving = replace(
            f.parent,
            url=f"https://eepro.com/results/arrival-{number}/",
            source_ref=f"eepro:arrival-{number}",
        )
        upsert_watch(f.conn, arriving, clock.now())
        # External stimulus: a live event remains due throughout the experiment.
        f.conn.execute(
            "UPDATE watches SET next_check_at=? WHERE watch_id=?",
            (clock.now().isoformat(), current.watch_id),
        )
        clock.sleep(5)
        max_watch_rows = max(
            max_watch_rows, f.conn.execute("SELECT count(*) FROM watches").fetchone()[0]
        )
        max_due_rows = max(
            max_due_rows,
            f.conn.execute(
                "SELECT count(*) FROM watches WHERE next_check_at<=? AND state NOT IN ('sealed','retired')",
                (clock.now().isoformat(),),
            ).fetchone()[0],
        )
        with f.db.transaction() as conn:
            prepare_event_turns(conn, config, now=clock.now(), run_id=run)
        choice = next_watch(f.conn, config, now=clock.now(), run_id=run)
        assert choice is not None
        watch = f.conn.execute(
            "SELECT * FROM watches WHERE watch_id=?", (choice.watch_id,)
        ).fetchone()
        client = FetchClient(
            f.conn,
            config,
            clock,
            f.archive,
            transport=httpx.MockTransport(respond),
            random_value=lambda: 0,
        )
        try:
            if len(completed) >= 4 and not paused_once:
                before = len(wire)
                usage = [tuple(row) for row in f.conn.execute("SELECT * FROM host_budget")]
                turns = [
                    tuple(row) for row in f.conn.execute("SELECT * FROM scheduler_event_turns")
                ]
                change_control(
                    f.db.state_dir,
                    selector=Selector("all", "all"),
                    paused=True,
                    actor="offline-cohort",
                    reason="bounded pause",
                    now=clock.now(),
                )
                assert next_watch(f.conn, config, now=clock.now(), run_id=run) is None
                with servicing(choice, run_id=run):
                    assert (
                        client.fetch(choice.watch_id, get_page_kind(watch["parser"]), run).skipped
                        == "operator"
                    )
                clock.sleep(60)
                assert len(wire) == before
                assert [tuple(row) for row in f.conn.execute("SELECT * FROM host_budget")] == usage
                assert [
                    tuple(row) for row in f.conn.execute("SELECT * FROM scheduler_event_turns")
                ] == turns
                change_control(
                    f.db.state_dir,
                    selector=Selector("all", "all"),
                    paused=False,
                    actor="offline-cohort",
                    reason="resume",
                    now=clock.now(),
                )
                paused_once = True
            with servicing(choice, run_id=run):
                fetched = client.fetch(choice.watch_id, get_page_kind(watch["parser"]), run)
        finally:
            client.close()
        assert fetched.skipped is None
        categories[choice.category] += 1
        if choice.capacity:
            lanes[choice.capacity.lane] += 1
        if fetched.snapshot_id:
            unit = WorkUnit("parse", "snapshot", fetched.snapshot_id)
            with operation(
                f.db,
                action_id=f"cohort-parse-{number}",
                action_kind="parse",
                scope=for_unit(f.conn, unit),
                clock=clock,
                run_id=run,
            ):
                parsed = parse_snapshot(f.db, f.archive, unit, clock, run)
            assert not parsed.failed and parsed.selection is None
            assert (
                f.conn.execute(
                    "SELECT parse_status FROM snapshots WHERE snapshot_id=?", (fetched.snapshot_id,)
                ).fetchone()[0]
                == "ok"
            )
        refresh_policy(
            f.conn,
            config,
            choice.watch_id,
            clock.now(),
            outcome=fetched.classification.outcome,
            jitter=0,
        )
        finish_bootstrap(f)
        enroll(f, config)
        if watch["url"] in target_urls:
            assert fetched.snapshot_id is not None
            completed.add(watch["url"])
            target_positions.append(len(wire))
        if len(completed) == 33:
            break
    assert completed == target_urls, (len(completed), categories, lanes)
    assert paused_once and len(wire) <= 601
    assert categories["current"] > 0 and categories["new"] > 33
    assert lanes["listed_result"] >= 33 and lanes["discovery"] > 0
    assert max(b - a for a, b in zip([0, *target_positions], target_positions, strict=False)) <= 128
    assert f.conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == len(wire)
    assert f.conn.execute("SELECT count(*) FROM scheduler_requests").fetchone()[0] == len(wire)
    assert f.conn.execute("SELECT sum(bytes) FROM host_budget").fetchone()[0] == sum(
        row[2] for row in wire
    )
    assert f.conn.execute("SELECT count(DISTINCT action_id) FROM scheduler_requests").fetchone()[
        0
    ] == len(wire)
    assert f.conn.execute("SELECT count(*) FROM scheduler_event_requests").fetchone()[0] == len(
        wire
    )
    assert (
        f.conn.execute(
            "SELECT count(*) FROM scheduler_event_requests WHERE source_ref='eepro:monterey'"
        ).fetchone()[0]
        == 34
    )  # 33 pages plus initial robots.
    assert all(
        (after[0] - before[0]).total_seconds() >= 5
        for before, after in zip(wire, wire[1:], strict=False)
    )

    with f.db.transaction(immediate=False):
        final = inventory(
            f.conn, f.archive, source="eepro", source_ref="eepro:monterey", now=clock.now()
        )
    assert (final["listed_pages"], final["acquired_pages"], final["interpreted_pages"]) == (
        33,
        33,
        33,
    )
    assert final["known_pages_accounted_for"] is True
    assert final["canonical_event_id"] is None and final["pagination"] == "unknown"
    assert final["eligible_service_age_seconds"] is None
    assert final["unsupported_pages"] == 0 and final["published_pages"] is None
    # Refresh the same bounded artifact observer used by the cycle, through its
    # normal operation gate. Continuous arrivals have stopped at the bound.
    for visit in range(100):
        with operation(
            f.db,
            action_id=f"cohort-observe-{visit}",
            action_kind="project",
            scope=ActionScope(
                all_sources=True,
                kinds=frozenset(
                    {"work_attempt", "archive_artifact", "admission_blocked", "parse_failure"}
                ),
            ),
            clock=clock,
            run_id=run,
        ):
            event_progress.refresh(f.db, f.archive, config, now=clock.now(), run_id=run)
        with f.db.transaction(immediate=False):
            accounting = accounting_report(
                f.conn,
                f.archive,
                config,
                now=clock.now(),
                source="eepro",
                source_ref="eepro:monterey",
            )
        if accounting["events"][0]["assessment"] == "locally_accounted":
            break
    observed = accounting["events"][0]
    assert observed["assessment"] == "locally_accounted"
    assert observed["stages"]["interpreted"] == dict(positive=33, negative=0, unknown=0)
    assert observed["listed_pages"] == 33
    assert len(wire) == f.conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0]

    metrics = {
        "max_watch_rows": max_watch_rows,
        "max_due_watch_rows": max_due_rows,
        "selections": sum(categories.values()),
        "wire_requests": len(wire),
        "category_selections": dict(categories),
        "capacity_selections": dict(lanes),
        "category_requests": dict(
            f.conn.execute("SELECT category,count(*) FROM scheduler_requests GROUP BY category")
        ),
        "capacity_requests": dict(
            f.conn.execute("SELECT lane,count(*) FROM scheduler_capacity_requests GROUP BY lane")
        ),
        "target_first_request": target_positions[0],
        "target_last_request": target_positions[-1],
        "target_max_request_gap": max(
            b - a for a, b in zip([0, *target_positions], target_positions, strict=False)
        ),
        "created_arrival_indexes": number + 1,
        "pending_arrival_indexes": f.conn.execute(
            "SELECT count(*) FROM watches WHERE source_ref LIKE 'eepro:arrival-%' AND parser='eepro.autoindex' AND last_checked_at IS NULL"
        ).fetchone()[0],
        "artifact_refresh_visits": visit + 1,
        "known_cohort_pages": 33,
        "canonical_map": None,
        "eligible_age": None,
        "operating_acceptance": False,
    }
    assert metrics["category_requests"] == {"new": 232, "current": 174}
    assert metrics["capacity_requests"] == {"listed_result": 133, "discovery": 99}
    assert metrics["wire_requests"] == 406 and metrics["selections"] == 405
    assert metrics["target_max_request_gap"] == 79
    assert metrics["pending_arrival_indexes"] == 306
    # Compare actual debits with configured weights; one request is the allowed
    # rounding tolerance. The only competing classes in this fixture are these.
    weights = shares("eepro.com")
    assert (
        abs(
            metrics["category_requests"]["current"]
            - metrics["wire_requests"] * weights["current"] / (weights["new"] + weights["current"])
        )
        <= 1
    )
    metrics["policy"] = {
        "turn_requests": config.scheduler.event_turn_requests,
        "listed_page_percent": config.scheduler.listed_page_percent,
        "class_weights": weights,
        "high": config.scheduler.event_pressure_high,
        "low": config.scheduler.event_pressure_low,
        "request_budget": config.host("eepro.com").daily_request_budget,
    }
    # The unrefreshed conservative hints reached the real high watermark. All
    # issued pages have now been interpreted, so bounded proof refresh can drain
    # pressure and admit another index without deleting any waiting watch.
    before_pressure = event_pressure.admission(f.conn, config, host="eepro.com", now=clock.now())
    assert before_pressure["watermark_deferred"]
    assert before_pressure["pressure_subjects"] == 101
    watch_count = f.conn.execute("SELECT count(*) FROM watches").fetchone()[0]
    for pressure_visit in range(15):
        with operation(
            f.db,
            action_id=f"cohort-pressure-{pressure_visit}",
            action_kind="project",
            scope=ActionScope(
                all_sources=True,
                kinds=frozenset(
                    {"work_attempt", "archive_artifact", "admission_blocked", "parse_failure"}
                ),
            ),
            clock=clock,
            run_id=run,
        ):
            event_pressure.refresh(f.db, f.archive, config, now=clock.now())
        after_pressure = event_pressure.admission(f.conn, config, host="eepro.com", now=clock.now())
        if not after_pressure["deferred"]:
            break
    assert not after_pressure["deferred"]
    assert after_pressure["pressure_subjects"] < config.scheduler.event_pressure_low
    assert f.conn.execute("SELECT count(*) FROM watches").fetchone()[0] == watch_count
    assert len(wire) == 406
    clock.sleep(5)
    with f.db.transaction() as conn:
        prepare_event_turns(conn, config, now=clock.now(), run_id=run)
    resumed = next_watch(f.conn, config, now=clock.now(), run_id=run)
    assert resumed and resumed.capacity and resumed.capacity.lane == "discovery"
    resumed_watch = f.conn.execute(
        "SELECT * FROM watches WHERE watch_id=?", (resumed.watch_id,)
    ).fetchone()
    assert resumed_watch["last_checked_at"] is None
    client = FetchClient(
        f.conn,
        config,
        clock,
        f.archive,
        transport=httpx.MockTransport(respond),
        random_value=lambda: 0,
    )
    try:
        with servicing(resumed, run_id=run):
            fetched = client.fetch(resumed.watch_id, get_page_kind(resumed_watch["parser"]), run)
        assert fetched.snapshot_id and fetched.skipped is None
    finally:
        client.close()
    assert len(wire) == 407
    assert f.conn.execute("SELECT sum(requests) FROM host_budget").fetchone()[0] == 407
    assert f.conn.execute("SELECT count(*) FROM scheduler_event_requests").fetchone()[0] == 407
    assert f.conn.execute("SELECT sum(bytes) FROM host_budget").fetchone()[0] == sum(
        row[2] for row in wire
    )
    metrics["pressure_refresh_visits"] = pressure_visit + 1
    metrics["pressure_before"] = before_pressure["pressure_subjects"]
    metrics["pressure_after"] = after_pressure["pressure_subjects"]
    metrics["post_drain_discovery_requests"] = 1
    record_property("cohort_metrics", json.dumps(metrics, sort_keys=True))
    print(json.dumps(metrics, sort_keys=True))
