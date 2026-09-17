"""Real historical offer gates produce bounded, revocable timing evidence only."""

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from materialized_fixture import materialize_seeded_outputs
from test_event_enumerations import event as event
from test_event_timing import prepared
from test_platform_backfill import review_contract

from swingset.config import HostConfig, SourceConfig
from swingset.fetch.client import FetchClient
from swingset.fetch.eligibility import ordinary
from swingset.fetch.wayback import Capture
from swingset.history.archive_timing import OfferTiming, epoch
from swingset.history.backfill import offers
from swingset.model.canonical import Event
from swingset.project.history import accept_year
from swingset.project.writer import Projection, replace_scope
from swingset.schedule.event_timing_observer import (
    Observer,
    archive_offers,
    checkpoint,
    observing,
    resume,
)
from swingset.state.attempts import begin_attempt, finish_attempt
from swingset.state.work import WorkUnit, enqueue


@pytest.fixture
def historical(event):
    f = event
    f.clock, f.run = f.corpus.clock, f.corpus.run
    config = prepared(f)
    f.config = replace(
        config, hosts={**config.hosts, "web.archive.org": HostConfig(min_gap_seconds=10)}
    )
    canonical = Event(
        event_id="2019-01-test",
        series_id="series",
        name="Test",
        year=2019,
        start_date="2019-01-01",
        end_date="2019-01-03",
        source="wsdc_registry",
        snapshot_id="inventory",
        parser_version="1",
        first_seen_at=f.clock.now().isoformat(),
        last_seen_at=f.clock.now().isoformat(),
        run_id=f.run,
    )
    replace_scope(
        f.conn,
        scope_kind="history",
        scope_id="timing",
        projection=Projection((canonical,)),
        run_id=f.run,
        projected_at=f.clock.now().isoformat(),
    )
    f.conn.execute(
        "INSERT INTO source_event_map VALUES ('eepro','eepro:test','2019-01-test','explicit',1)"
    )
    f.conn.executemany(
        "INSERT OR REPLACE INTO meta VALUES (?,'true')", [("h7_deployed",), ("h10_deployed",)]
    )
    review_contract(f, "eepro.round")
    materialize_seeded_outputs(f.db, now=f.clock.now(), run_id=f.run)
    accept_year(f.conn, 2019, accepted_by="offline-owner", accepted_at=f.clock.now().isoformat())
    materialize_seeded_outputs(f.db, now=f.clock.now(), run_id=f.run)
    f.watch = f.conn.execute("SELECT watch_id FROM watches WHERE url LIKE '%one.htm'").fetchone()[0]
    f.capture = Capture(f.parent.url + "one.htm", "20190501000000", "retained", "text/html", 100)
    f.conn.execute(
        "INSERT INTO archive_captures VALUES ('eepro',?,?,?,200,?,?,?,'offline')",
        (
            f.capture.url,
            f.capture.timestamp,
            f.capture.digest,
            f.capture.mimetype,
            f.capture.length,
            f.clock.now().isoformat(),
        ),
    )
    body = f.archive.store_body(b"User-agent: *\nAllow: /\n")
    f.conn.execute(
        "INSERT INTO hosts(host,robots_sha256,robots_fetched_at,robots_status) VALUES ('web.archive.org',?,?,200)",
        (body, f.clock.now().isoformat()),
    )
    f.conn.execute("DELETE FROM pending_work")
    f.client = FetchClient(
        f.conn,
        f.config,
        f.clock,
        f.archive,
        transport=httpx.MockTransport(lambda _: pytest.fail("no request expected")),
    )
    try:
        yield f
    finally:
        f.client.close()


def proof(f):
    collected = []
    timing = OfferTiming(
        f.conn,
        f.config,
        f.clock,
        watches=(f.watch,),
        run_id=f.run,
        deadline=f.clock.now() + timedelta(seconds=100),
        accept=collected.extend,
    )
    offered = offers(f.db, f.config, f.clock, run_id=f.run, timing=timing)
    return offered, collected


def assessment(f, value=None, *, run_id=None):
    return ordinary(
        f.client.gate,
        f.client.robots,
        f.watch,
        deadline=f.clock.now() + timedelta(seconds=100),
        archive_proof=value,
        run_id=run_id or f.run,
    )


def test_real_offer_produces_immutable_read_only_proof_without_scheduling(historical):
    f = historical
    before = f.conn.total_changes
    offered, proofs = proof(f)
    assert len(proofs) == 1 and proofs[0].spec in offered
    assert proofs[0].spec.archive_url == f.capture.archive_url
    assert assessment(f, proofs[0]).state == "eligible"
    assert f.conn.total_changes == before
    assert (
        f.conn.execute("SELECT archive_url FROM watches WHERE watch_id=?", (f.watch,)).fetchone()[0]
        is None
    )
    assert not f.conn.execute("SELECT 1 FROM host_budget").fetchone()
    assert (
        assessment(f, proofs[0], run_id="another-run").reason == "historical_dispatch_proof_stale"
    )


def test_real_observer_consumes_dispatch_handoff_and_retains_unknown_on_change(historical):
    f = historical
    observer = Observer(
        f.db, f.client, f.clock, run_id=f.run, deadline=f.clock.now() + timedelta(seconds=100)
    )
    with observing(observer):
        assert observer.subjects[0]["reason"] is None
        offers(f.db, f.config, f.clock, run_id=f.run, timing=archive_offers())
        assert observer.subjects[0]["archive_epoch"] is not None
        f.clock.sleep(7)
        checkpoint()
        resume()
        f.clock.sleep(3)
        f.conn.execute("DELETE FROM archive_captures")
        f.clock.sleep(3)
        checkpoint()
        resume()
        assert observer.subjects[0]["recorder"].row["state"] == "unknown"
    row = f.conn.execute("SELECT * FROM event_timing").fetchone()
    assert row["eligible_seconds"] == 7
    assert row["unknown_seconds"] == 6


@pytest.mark.parametrize("change", ["year", "kind", "source", "retry", "mapping"])
def test_dispatcher_withholds_proof_when_real_offer_gate_closes(historical, change):
    f = historical
    _, old = proof(f)
    assert old
    if change == "year":
        f.conn.execute("DELETE FROM history_acceptance")
    elif change == "kind":
        f.conn.execute("UPDATE admission_policies SET mode='shadow' WHERE page_kind='eepro.round'")
    elif change == "source":
        f.config = replace(f.config, sources={"eepro": SourceConfig(False)})
        f.client.config = f.client.gate.config = f.config
    elif change == "retry":
        f.conn.execute(
            "UPDATE watches SET paused_until=? WHERE watch_id=?",
            ((f.clock.now() + timedelta(seconds=30)).isoformat(), f.watch),
        )
    else:
        f.conn.execute("DELETE FROM source_event_map")
    _, current = proof(f)
    assert not current
    assert assessment(f, old[0]).state != "eligible"


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM archive_captures",
        "UPDATE archive_captures SET digest='changed'",
        "UPDATE source_units SET accepted_generation_id=NULL",
        "UPDATE source_generations SET state='revoked'",
        "UPDATE snapshots SET parse_status='failed'",
        "DELETE FROM admission_reviews",
        "UPDATE events SET end_date='2019-01-04'",
        "INSERT INTO findings(finding_id,kind,owner_kind,owner_id,subject_kind,subject_id,severity,summary,evidence_json,opened_at,run_id) VALUES ('new-year','history','history_year','2019','year','2019','warning','new finding','{}','2026-01-01','fixture')",
    ],
)
def test_semantic_dependency_changes_cannot_reuse_positive_proof(historical, sql):
    f = historical
    _, values = proof(f)
    before = epoch(f.conn)
    if "INSERT INTO findings" in sql:
        sql = sql.replace("'fixture'", "'" + f.run + "'")
    f.conn.execute(sql)
    assert epoch(f.conn) != before
    assert assessment(f, values[0]).reason == "historical_dispatch_proof_stale"


def test_paid_budget_is_reassessed_without_refunding_or_reissuing(historical):
    f = historical
    _, values = proof(f)
    f.conn.execute(
        "INSERT INTO host_budget VALUES ('web.archive.org',?,200,99)",
        (f.clock.now().date().isoformat(),),
    )
    before = f.conn.total_changes
    result = assessment(f, values[0])
    assert result.state == "blocked" and result.reason == "request budget"
    assert f.conn.total_changes == before
    assert tuple(f.conn.execute("SELECT requests,bytes FROM host_budget").fetchone()) == (200, 99)


def test_operator_hold_stops_observed_archive_waiting(historical):
    f = historical
    with observing(
        Observer(
            f.db, f.client, f.clock, run_id=f.run, deadline=f.clock.now() + timedelta(seconds=100)
        )
    ):
        offers(f.db, f.config, f.clock, run_id=f.run, timing=archive_offers())
        f.clock.sleep(4)
        checkpoint()
        (f.db.state_dir / "operator-hold").write_text("offline hold")
        resume()
        f.clock.sleep(9)
    row = f.conn.execute("SELECT * FROM event_timing").fetchone()
    assert row["eligible_seconds"] == 4 and row["blocked_seconds"] == 9
    assert not f.conn.execute("SELECT 1 FROM host_budget").fetchone()


def parent_retry(f, seconds=5):
    capture = Capture(f.parent.url, "20190401000000", "failed-parent", "text/html", 10)
    f.conn.execute(
        "INSERT INTO archive_captures VALUES ('eepro',?,?,?,200,?,?,?,'offline')",
        (
            capture.url,
            capture.timestamp,
            capture.digest,
            capture.mimetype,
            capture.length,
            f.clock.now().isoformat(),
        ),
    )
    ctx = f.corpus.snapshot("retry-parent", b"unparseable", spec=f.parent, via="wayback")
    f.conn.execute(
        "UPDATE snapshots SET archive_url=?,requested_archive_url=?,parse_status='failed' WHERE snapshot_id=?",
        (capture.archive_url, capture.archive_url, ctx.snapshot_id),
    )
    attempt = begin_attempt(
        f.db, WorkUnit("parse", "snapshot", ctx.snapshot_id), now=f.clock.now(), run_id=f.run
    )
    finish_attempt(
        f.db,
        attempt,
        outcome="transient",
        reason_code="offline_retry",
        now=f.clock.now(),
        retry_at=f.clock.now() + timedelta(seconds=seconds),
    )
    return attempt


def test_real_parent_retry_expires_offer_without_database_write(historical):
    f = historical
    parent_retry(f)
    _, values = proof(f)
    assert len(values) == 1
    assert values[0].until == f.clock.now() + timedelta(seconds=5)
    assert assessment(f, values[0]).state == "eligible"
    before_epoch, changes = epoch(f.conn), f.conn.total_changes
    f.clock.sleep(5)
    assert epoch(f.conn) == before_epoch and f.conn.total_changes == changes
    assert assessment(f, values[0]).reason == "historical_dispatch_proof_stale"
    assert not proof(f)[1]  # Earlier parent capture now waits for interpretation.


def test_pending_population_and_invalid_retry_times_leave_proof_unknown(historical):
    f = historical
    attempt = parent_retry(f)
    offered, _ = proof(f)
    f.conn.execute(
        "UPDATE work_attempts SET retry_at='not-a-time' WHERE attempt_id=?", (attempt.attempt_id,)
    )
    collected = []
    timing = OfferTiming(
        f.conn,
        f.config,
        f.clock,
        watches=(f.watch,),
        run_id=f.run,
        deadline=f.clock.now() + timedelta(seconds=100),
        accept=collected.extend,
    )
    timing.begin(connection=f.conn, config=f.config, clock=f.clock, run_id=f.run)
    timing.finish({value.watch_id: value for value in offered})
    assert not collected
    f.conn.execute("DELETE FROM pending_work")
    enqueue(
        f.conn,
        tuple(WorkUnit("parse", "snapshot", str(i)) for i in range(65)),
        enqueued_at=f.clock.now().isoformat(),
    )
    assert not proof(f)[1]


def test_producer_cannot_rebind_a_proof_to_a_different_run(historical):
    f = historical
    collected = []
    timing = OfferTiming(
        f.conn,
        f.config,
        f.clock,
        watches=(f.watch,),
        run_id=f.run,
        deadline=f.clock.now() + timedelta(seconds=100),
        accept=collected.extend,
    )
    assert offers(f.db, f.config, f.clock, run_id="different", timing=timing)
    assert not collected


def test_closed_archive_lower_bound_survives_real_failed_dispatch_and_reverification(historical):
    from swingset.history.backfill import dispatch_one

    f = historical
    f.client.close()
    f.client = FetchClient(
        f.conn,
        f.config,
        f.clock,
        f.archive,
        transport=httpx.MockTransport(lambda _: httpx.Response(500, content=b"failure")),
        random_value=lambda: 0,
    )
    observer = Observer(
        f.db, f.client, f.clock, run_id=f.run, deadline=f.clock.now() + timedelta(seconds=100)
    )
    with observing(observer):
        offers(f.db, f.config, f.clock, run_id=f.run, timing=archive_offers())
        f.clock.sleep(12)
        result = dispatch_one(
            f.db,
            f.config,
            f.clock,
            f.run,
            deadline=f.clock.now() + timedelta(seconds=30),
            fetcher=f.client,
            allocated=True,
            target_watch_id=f.watch,
        )
        assert result.result is not None and result.result.classification.outcome == "ServerError"
    before = dict(f.conn.execute("SELECT * FROM event_timing").fetchone())
    assert before["since_progress_seconds"] == 12 and before["last_progress_at"] is None
    f.clock.sleep(1000)
    f.conn.execute(
        "UPDATE watches SET next_check_at=?,paused_until=NULL WHERE watch_id=?",
        (f.clock.now().isoformat(), f.watch),
    )
    observer = Observer(
        f.db, f.client, f.clock, run_id=f.run, deadline=f.clock.now() + timedelta(seconds=100)
    )
    with observing(observer):
        offers(f.db, f.config, f.clock, run_id=f.run, timing=archive_offers())
        rec = observer.subjects[0]["recorder"]
        assert rec.row["episode_id"] == before["episode_id"]
        assert rec.row["since_progress_seconds"] == 12
        assert rec.row["state"] == "eligible"
        f.clock.sleep(4)
    after = f.conn.execute("SELECT * FROM event_timing").fetchone()
    assert after["since_progress_seconds"] == 16 and after["last_progress_at"] is None


def test_observer_caps_real_archive_interval_at_parse_retry_boundary(historical):
    f = historical
    parent_retry(f)
    with observing(
        Observer(
            f.db, f.client, f.clock, run_id=f.run, deadline=f.clock.now() + timedelta(seconds=100)
        )
    ):
        offers(f.db, f.config, f.clock, run_id=f.run, timing=archive_offers())
        f.clock.sleep(8)
    row = f.conn.execute("SELECT * FROM event_timing").fetchone()
    assert row["eligible_seconds"] == 5 and row["unknown_seconds"] == 3


def test_changed_offer_during_producer_cannot_grant_positive_proof(historical, monkeypatch):
    from swingset.history import backfill

    f = historical
    original = backfill._candidate

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if result[0] is not None:
            f.conn.execute("DELETE FROM archive_captures")
        return result

    monkeypatch.setattr(backfill, "_candidate", changed)
    offered, values = proof(f)
    assert offered and not values


def test_changed_fence_between_assessment_and_open_cannot_credit_archive_time(
    historical, monkeypatch
):
    from swingset.schedule import event_timing_observer as module

    f = historical
    observer = Observer(
        f.db, f.client, f.clock, run_id=f.run, deadline=f.clock.now() + timedelta(seconds=100)
    )
    with observing(observer):
        original = module.ordinary

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get("archive_proof") is not None:
                f.conn.execute("DELETE FROM archive_captures")
            return result

        monkeypatch.setattr(module, "ordinary", changed)
        offers(f.db, f.config, f.clock, run_id=f.run, timing=archive_offers())
        assert observer.subjects[0]["recorder"].row["state"] == "unknown"
        f.clock.sleep(5)
    assert f.conn.execute("SELECT eligible_seconds FROM event_timing").fetchone()[0] == 0


def test_samples_do_not_repeat_archive_plan_parent_or_year_inventory_reads(historical):
    import sqlite3

    f = historical
    observer = Observer(
        f.db, f.client, f.clock, run_id=f.run, deadline=f.clock.now() + timedelta(seconds=100)
    )
    with observing(observer):
        offers(f.db, f.config, f.clock, run_id=f.run, timing=archive_offers())
        forbidden = {
            "archive_captures",
            "archive_queries",
            "events",
            "source_event_map",
            "history_acceptance",
            "source_generations",
            "source_units",
        }

        def authorize(action, table, column, database, origin):
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_READ and table in forbidden
                else sqlite3.SQLITE_OK
            )

        f.conn.set_authorizer(authorize)
        try:
            checkpoint()
            resume()
            assert observer.subjects[0]["recorder"].row["state"] == "eligible"
            f.clock.sleep(3)
            checkpoint()
        finally:
            f.conn.set_authorizer(None)
    assert f.conn.execute("SELECT eligible_seconds FROM event_timing").fetchone()[0] == 3


def test_proof_is_process_local_and_population_limits_fail_closed(historical):
    import sqlite3

    f = historical
    offered, values = proof(f)
    with sqlite3.connect(
        (f.db.state_dir / "state.sqlite").as_uri() + "?mode=ro", uri=True
    ) as reader:
        assert not values[0].current(reader, f.config, f.clock.now(), f.run)
    collected = []
    timing = OfferTiming(
        f.conn,
        f.config,
        f.clock,
        watches=(f.watch,) * 257,
        run_id=f.run,
        deadline=f.clock.now() + timedelta(seconds=100),
        accept=collected.extend,
    )
    timing.begin(connection=f.conn, config=f.config, clock=f.clock, run_id=f.run)
    timing.finish({value.watch_id: value for value in offered})
    assert not collected


def test_direct_dispatch_busy_recheck_resumes_ordinary_observer(historical, monkeypatch):
    from swingset.history import backfill

    f = historical
    calls = []

    def busy(*args):
        calls.append(True)
        return "wall_clock_reserve" if len(calls) == 2 else None

    monkeypatch.setattr(backfill, "_busy", busy)
    observer = Observer(
        f.db, f.client, f.clock, run_id=f.run, deadline=f.clock.now() + timedelta(seconds=100)
    )
    with observing(observer):
        offers(f.db, f.config, f.clock, run_id=f.run, timing=archive_offers())
        f.clock.sleep(4)
        result = backfill.dispatch_one(
            f.db,
            f.config,
            f.clock,
            f.run,
            deadline=f.clock.now() + timedelta(minutes=10),
            fetcher=f.client,
        )
        assert result.reason == "wall_clock_reserve" and len(calls) == 2
        assert observer.subjects[0]["recorder"].start is not None
        f.clock.sleep(3)
    assert f.conn.execute("SELECT eligible_seconds FROM event_timing").fetchone()[0] == 7
