"""Independent H14 acceptance: bounded recovery and unchanged acquisition limits."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from swingset.clock import FakeClock
from swingset.config import Config, SourceConfig, parse_hosts
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.politeness import Gate, Grant, Paused, Wait
from swingset.model.canonical import Event
from swingset.project.writer import Projection, replace_scope
from swingset.schedule.watches import refresh_policy, upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.db import open_database


def config():
    return Config(
        parse_hosts(Path("config/hosts.toml").read_bytes()), {"scoringdance": SourceConfig(True)}
    )


def event_watch(suffix="event"):
    return WatchSpec(
        "",
        "scoringdance",
        "event",
        "GET",
        f"https://scoring.dance/{suffix}",
        "scoringdance.event",
        source_ref=f"scoringdance:{suffix}",
    )


def watch_row(conn, spec):
    return conn.execute("SELECT * FROM watches WHERE watch_id=?", (spec.watch_id,)).fetchone()


def test_dateless_success_is_bounded_and_refresh_does_not_postpone_recovery(tmp_path):
    clock = FakeClock()
    spec = event_watch()
    with open_database(tmp_path) as db:
        conn = db.connection
        upsert_watch(conn, spec, clock.now())
        for attempt, outcome in enumerate((Outcome.OK, Outcome.NOT_MODIFIED, Outcome.OK), 1):
            refresh_policy(conn, config(), spec.watch_id, clock.now(), outcome=outcome, jitter=0)
            row = watch_row(conn, spec)
            assert row["state"] == "metadata"
            delay = timedelta(days=1 if attempt < 3 else 30)
            assert datetime.fromisoformat(row["next_check_at"]) == clock.now() + delay
            before = row["next_check_at"]
            clock.sleep(3600)
            refresh_policy(conn, config(), spec.watch_id, clock.now(), jitter=0)
            assert watch_row(conn, spec)["next_check_at"] == before
            assert (
                conn.execute("SELECT metadata_attempts FROM scheduler_watch_state").fetchone()[0]
                == attempt
            )
            clock.sleep((datetime.fromisoformat(before) - clock.now()).total_seconds())
        # Reloaded process state retains the bounded cadence even after another 304.
        refresh_policy(
            conn, config(), spec.watch_id, clock.now(), outcome=Outcome.NOT_MODIFIED, jitter=0
        )
        assert datetime.fromisoformat(
            watch_row(conn, spec)["next_check_at"]
        ) == clock.now() + timedelta(days=30)


@pytest.mark.parametrize("outcome", [Outcome.BLOCKED, Outcome.THROTTLED, Outcome.SERVER_ERROR])
def test_transport_denial_does_not_consume_metadata_recovery(tmp_path, outcome):
    with open_database(tmp_path) as db:
        spec = event_watch()
        clock = FakeClock()
        upsert_watch(db.connection, spec, clock.now())
        refresh_policy(
            db.connection, config(), spec.watch_id, clock.now(), outcome=outcome, jitter=0
        )
        assert (
            db.connection.execute("SELECT metadata_attempts FROM scheduler_watch_state").fetchone()[
                0
            ]
            == 0
        )


def test_only_new_parent_relationship_reactivates_and_preserves_existing_control(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        conn = db.connection
        child, first, second = event_watch("child"), event_watch("parent1"), event_watch("parent2")
        for parent in (first, second):
            upsert_watch(conn, parent, clock.now())
        upsert_watch(conn, child, clock.now(), parent_watch_id=first.watch_id)
        refresh_policy(conn, config(), child.watch_id, clock.now(), outcome=Outcome.OK, jitter=0)
        pause_until = (clock.now() + timedelta(days=10)).isoformat()
        conn.execute(
            "UPDATE watches SET paused_until=?,first_404_at=?,consecutive_404s=2 WHERE watch_id=?",
            (pause_until, (clock.now() - timedelta(days=2)).isoformat(), child.watch_id),
        )
        refresh_policy(conn, config(), child.watch_id, clock.now(), outcome=Outcome.GONE, jitter=0)
        due = watch_row(conn, child)["next_check_at"]
        assert datetime.fromisoformat(due) == clock.now() + timedelta(days=90)
        clock.sleep(86400)
        upsert_watch(conn, child, clock.now(), parent_watch_id=first.watch_id)
        refresh_policy(conn, config(), child.watch_id, clock.now(), jitter=0)
        assert watch_row(conn, child)["state"] == "gone"
        assert watch_row(conn, child)["next_check_at"] == due
        upsert_watch(conn, child, clock.now(), parent_watch_id=second.watch_id)
        row = watch_row(conn, child)
        assert (
            row["state"],
            row["next_check_at"],
            row["paused_until"],
            row["parent_watch_id"],
        ) == ("live", clock.now().isoformat(), pause_until, first.watch_id)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM scheduler_parent_links WHERE child_watch_id=?",
                (child.watch_id,),
            ).fetchone()[0]
            == 2
        )
        assert (
            conn.execute(
                "SELECT metadata_attempts FROM scheduler_watch_state WHERE watch_id=?",
                (child.watch_id,),
            ).fetchone()[0]
            == 0
        )


def test_real_dates_restore_current_policy_without_invented_metadata(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        conn = db.connection
        run = db.start_run(clock.now())
        spec = event_watch()
        upsert_watch(conn, spec, clock.now())
        for _ in range(3):
            refresh_policy(conn, config(), spec.watch_id, clock.now(), outcome=Outcome.OK, jitter=0)
            clock.sleep(86400)
        assert watch_row(conn, spec)["state"] == "metadata"
        assert not conn.execute("SELECT 1 FROM events").fetchone()
        event = Event(
            event_id="actual",
            series_id="actual",
            name="Actual dates",
            year=2026,
            start_date=clock.now().date().isoformat(),
            end_date=clock.now().date().isoformat(),
            source="scoringdance",
            snapshot_id="retained",
            parser_version="1",
            first_seen_at=clock.now().isoformat(),
            last_seen_at=clock.now().isoformat(),
            run_id=run,
        )
        replace_scope(
            conn,
            scope_kind="event",
            scope_id="fixture",
            projection=Projection((event,)),
            run_id=run,
            projected_at=clock.now().isoformat(),
        )
        conn.execute(
            "INSERT INTO source_event_map VALUES (?,?,?,'explicit',1)",
            (spec.source, spec.source_ref, event.event_id),
        )
        refresh_policy(conn, config(), spec.watch_id, clock.now(), jitter=0)
        assert watch_row(conn, spec)["state"] == "live"
        assert datetime.fromisoformat(
            watch_row(conn, spec)["next_check_at"]
        ) == clock.now() + timedelta(minutes=15)


@pytest.mark.parametrize(
    "host,limit,gap",
    [
        ("worldsdc.com", 10, 5),
        ("points.worldsdc.com", 1500, 5),
        ("eepro.com", 600, 5),
        ("scoring.dance", 800, 5),
        ("scores.worlddanceregistry.com", 400, 5),
        ("danceconvention.net", 200, 10),
        ("web.archive.org", 200, 10),
        ("www.worldsdc.com", 200, 5),
    ],
)
def test_every_host_keeps_its_actual_daily_cap_and_request_floor(tmp_path, host, limit, gap):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        conn = db.connection
        assert config().host(host).daily_request_budget == limit
        conn.execute("INSERT INTO hosts(host) VALUES (?)", (host,))
        conn.execute(
            "INSERT INTO host_budget VALUES (?,?,?,0)",
            (host, clock.now().date().isoformat(), limit - 1),
        )
        gate = Gate(conn, config(), clock)
        # Seeded historical debits have no dispatch/completion evidence. Adopt
        # an explicit stopped-worker baseline before testing remaining capacity.
        with db.transaction():
            gate.establish_spacing_baseline(
                host,
                gap_seconds=gap,
                stopped_at=clock.now(),
                evidence_ref="offline://h14-seeded-debits",
            )
        assert gate.acquire(host) == Wait(gap)
        clock.sleep(gap)
        assert isinstance(gate.acquire(host), Grant)
        assert isinstance(gate.acquire(host), Wait)
        gate.release(host, Classification(Outcome.OK))
        assert gate.acquire(host) == Wait(gap)
        clock.sleep(gap)
        assert gate.acquire(host) == Paused("request budget")
        assert conn.execute("SELECT requests FROM host_budget").fetchone()[0] == limit


def test_retry_cooldown_and_depleted_quota_resume_without_burst(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        conn = db.connection
        gate = Gate(conn, config(), clock)
        host = "scoring.dance"
        assert isinstance(gate.acquire(host), Grant)
        gate.release(host, Classification(Outcome.THROTTLED, retry_after=3600))
        assert gate.acquire(host) == Paused("Throttled")
        clock.sleep(3600)
        gate = Gate(conn, config(), clock)
        assert isinstance(gate.acquire(host), Grant)
        gate.release(host, Classification(Outcome.OK))
        assert gate.acquire(host) == Wait(5)
        conn.execute("UPDATE host_budget SET requests=800")
        clock.sleep(7200)
        assert gate.acquire(host) == Paused("request budget")
        clock.sleep(86400)
        assert isinstance(gate.acquire(host), Grant)
        gate.release(host, Classification(Outcome.OK))
        assert gate.acquire(host) == Wait(5)
        assert (
            conn.execute(
                "SELECT requests FROM host_budget WHERE day=?", (clock.now().date().isoformat(),)
            ).fetchone()[0]
            == 1
        )


def test_dcn_response_can_overshoot_bytes_once_then_all_work_is_blocked(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        conn = db.connection
        host = "danceconvention.net"
        assert config().host(host).daily_byte_budget == 300_000_000
        conn.execute("INSERT INTO hosts(host) VALUES (?)", (host,))
        conn.execute(
            "INSERT INTO host_budget VALUES (?,?,0,299999999)",
            (host, clock.now().date().isoformat()),
        )
        gate = Gate(conn, config(), clock)
        assert isinstance(gate.acquire(host), Grant)
        gate.release(host, Classification(Outcome.OK), body_bytes=101)
        clock.sleep(10)
        assert gate.acquire(host) == Paused("byte budget")
        assert conn.execute("SELECT requests,bytes FROM host_budget").fetchone()[:] == (
            1,
            300_000_100,
        )


def seed_snapshots(db, clock, specs):
    """Give previously fetched watches real retained source bodies, without derivation."""
    from swingset.fetch.archive import Archive

    body = b'<a href="/results/final.html">Finals</a>'
    archive = Archive(db.state_dir)
    sha = archive.store_body(body)
    run = db.start_run(clock.now())
    for spec in specs:
        upsert_watch(db.connection, spec, clock.now())
        snapshot = "fixture_" + spec.watch_id
        db.connection.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,classification) VALUES (?,?,'GET',?,?,200,?,?,1,?,'Ok')",
            (snapshot, spec.watch_id, spec.url, clock.now().isoformat(), sha, len(body), run),
        )
        db.connection.execute(
            "UPDATE watches SET body_sha256=?,ever_ok=1,last_checked_at=? WHERE watch_id=?",
            (sha, clock.now().isoformat(), spec.watch_id),
        )
    return archive, run, body


def cached_robots(conn, archive, clock, host):
    sha = archive.store_body(b"")
    conn.execute("INSERT OR IGNORE INTO hosts(host) VALUES (?)", (host,))
    conn.execute(
        "UPDATE hosts SET robots_sha256=?,robots_fetched_at=?,robots_status=404 WHERE host=?",
        (sha, clock.now().isoformat(), host),
    )


def test_sustained_current_demand_services_old_work_using_actual_http_debits(tmp_path):
    import httpx

    from swingset.fetch.client import FetchClient
    from swingset.schedule.fairness import next_watch, servicing
    from swingset.sources.scoringdance.adapter import EventPage

    clock = FakeClock()
    with open_database(tmp_path) as db:
        current, old = event_watch("current"), event_watch("old")
        archive, run, body = seed_snapshots(db, clock, (current, old))
        db.connection.execute(
            "UPDATE watches SET state='archived' WHERE watch_id=?", (old.watch_id,)
        )
        cached_robots(db.connection, archive, clock, "scoring.dance")
        fetcher = FetchClient(
            db.connection,
            config(),
            clock,
            archive,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
            random_value=lambda: 0,
        )
        service = []
        try:
            for _ in range(100):
                choice = next_watch(db.connection, config(), now=clock.now())
                assert choice is not None
                service.append(choice.category)
                with servicing(choice, run_id=run):
                    result = fetcher.fetch(choice.watch_id, EventPage(), run)
                    assert result.classification.outcome == Outcome.OK and result.skipped is None
                clock.sleep(5)
        finally:
            fetcher.close()
        assert service.count("old") >= 30 and service.count("current") >= 50
        assert all("old" in service[index : index + 4] for index in range(97))
        assert db.connection.execute("SELECT COUNT(*) FROM scheduler_requests").fetchone()[0] == 100
        assert (
            db.connection.execute(
                "SELECT requests FROM host_budget WHERE host='scoring.dance'"
            ).fetchone()[0]
            == 100
        )
        assert db.connection.execute("SELECT SUM(body_bytes) FROM scheduler_requests").fetchone()[
            0
        ] == 100 * len(body)


def test_midday_migration_usage_is_not_new_capacity_or_catchup_credit(tmp_path):
    import httpx

    from swingset.fetch.client import FetchClient
    from swingset.schedule.fairness import WatchChoice, next_watch, servicing
    from swingset.sources.scoringdance.adapter import EventPage
    from swingset.state.controls import ActionScope

    clock = FakeClock()
    with open_database(tmp_path) as db:
        spec = event_watch("old")
        archive, run, body = seed_snapshots(db, clock, (spec,))
        db.connection.execute(
            "UPDATE watches SET state='archived',next_check_at=?",
            ((clock.now() - timedelta(days=60)).isoformat(),),
        )
        cached_robots(db.connection, archive, clock, "scoring.dance")
        db.connection.executemany(
            "INSERT INTO host_budget VALUES (?,?,?,0)",
            [
                ("scoring.dance", clock.now().date().isoformat(), 725),
                ("web.archive.org", clock.now().date().isoformat(), 200),
            ],
        )
        archived = WatchChoice(
            "archive-virtual",
            None,
            "web.archive.org",
            "old",
            ActionScope(),
            clock.now() - timedelta(days=60),
            archive=True,
        )
        calls = []
        fetcher = FetchClient(
            db.connection,
            config(),
            clock,
            archive,
            transport=httpx.MockTransport(
                lambda request: (
                    calls.append((str(request.url), clock.now()))
                    or httpx.Response(200, content=body)
                )
            ),
            random_value=lambda: 0,
        )
        with db.transaction():
            for host in ("scoring.dance", "web.archive.org"):
                fetcher.gate.establish_spacing_baseline(
                    host,
                    gap_seconds=max(5, config().host(host).min_gap_seconds),
                    stopped_at=clock.now(),
                    evidence_ref="offline://h14-midday-seeded-debits",
                )
        try:
            for _ in range(75):
                choice = next_watch(
                    db.connection, config(), now=clock.now(), extra_choices=(archived,)
                )
                assert choice and choice.watch_id == spec.watch_id
                with servicing(choice, run_id=run):
                    result = fetcher.fetch(spec.watch_id, EventPage(), run)
                    assert result.classification.outcome == Outcome.OK and result.skipped is None
                clock.sleep(5)
            assert (
                next_watch(db.connection, config(), now=clock.now(), extra_choices=(archived,))
                is None
            )
            assert fetcher.fetch(spec.watch_id, EventPage(), run).skipped == "request budget"
        finally:
            fetcher.close()
        assert len(calls) == 75
        assert (
            min(
                (right[1] - left[1]).total_seconds()
                for left, right in zip(calls, calls[1:], strict=False)
            )
            == 5
        )
        assert dict(db.connection.execute("SELECT host,requests FROM host_budget")) == {
            "scoring.dance": 800,
            "web.archive.org": 200,
        }
        assert db.connection.execute("SELECT COUNT(*) FROM scheduler_requests").fetchone()[0] == 75


def test_backpressure_reserves_two_actual_requests_for_a_declared_unblocker(tmp_path):
    import httpx

    from swingset.fetch.client import FetchClient
    from swingset.schedule.fairness import backpressure, next_watch, servicing
    from swingset.sources.wsdc_registry import SOURCE, DancerPage
    from swingset.state.requirements import Requirement, reconcile_requirement
    from swingset.state.work import WorkUnit, enqueue

    clock = FakeClock()
    with open_database(tmp_path) as db:
        ordinary = event_watch()
        archive, run, _ = seed_snapshots(db, clock, (ordinary,))
        spec = SOURCE.watch(1)
        upsert_watch(db.connection, spec, clock.now())
        cfg = Config(
            config().hosts,
            {"scoringdance": SourceConfig(True), "wsdc_registry": SourceConfig(True)},
        )
        requirement = Requirement(
            "source_id_checked",
            "1",
            "wsdc_registry",
            "ready",
            "fetch explicit source ID",
            {"wsdc_id": 1},
        )
        reconcile_requirement(db.connection, requirement, clock.now(), run)
        enqueue(
            db.connection,
            (WorkUnit("parse", "snapshot", f"retained-{index}") for index in range(1000)),
            enqueued_at=clock.now().isoformat(),
        )
        assert backpressure(db.connection, cfg)["active"]
        choice = next_watch(db.connection, cfg, now=clock.now())
        assert choice and choice.watch_id == spec.watch_id and choice.repair
        body = Path("src/swingset/sources/wsdc_registry/fixtures/lookup-1.body").read_bytes()
        calls = []
        fetcher = FetchClient(
            db.connection,
            cfg,
            clock,
            archive,
            transport=httpx.MockTransport(
                lambda request: (
                    calls.append(str(request.url))
                    or (
                        httpx.Response(404)
                        if request.url.path == "/robots.txt"
                        else httpx.Response(200, content=body)
                    )
                )
            ),
            random_value=lambda: 0,
        )
        try:
            with servicing(choice, run_id=run):
                assert fetcher.fetch(spec.watch_id, DancerPage(), run).snapshot_id
                clock.sleep(5)
                assert (
                    fetcher.fetch(spec.watch_id, DancerPage(), run).skipped
                    == "pending work backpressure"
                )
        finally:
            fetcher.close()
        assert len(calls) == 2  # robots and the source response both consume reserve.
        assert db.connection.execute(
            "SELECT SUM(pressure_reserved),COUNT(*) FROM scheduler_requests"
        ).fetchone()[:] == (2, 2)
        assert db.connection.execute("SELECT SUM(requests) FROM host_budget").fetchone()[0] == 2


@pytest.mark.parametrize(
    "selector_kind,selector_id", [("source", "scoringdance"), ("kind", "round_observations")]
)
def test_paused_and_cooled_work_yields_to_healthy_host_without_claiming_service(
    tmp_path, selector_kind, selector_id
):
    import httpx

    from swingset.fetch.client import FetchClient
    from swingset.schedule.fairness import next_watch, servicing
    from swingset.sources.scoringdance.adapter import RoundPage
    from swingset.sources.wsdc_calendar import EventsPage
    from swingset.state.controls import Selector, change_control

    clock = FakeClock()
    with open_database(tmp_path) as db:
        paused = WatchSpec(
            "",
            "scoringdance",
            "round",
            "GET",
            "https://scoring.dance/results/test.html",
            RoundPage.kind,
        )
        cooled = WatchSpec(
            "", "eepro", "autoindex", "GET", "https://eepro.com/results/old/", "eepro.autoindex"
        )
        healthy = WatchSpec(
            "", "wsdc_calendar", "index", "GET", "https://worldsdc.com/events", EventsPage.kind
        )
        archive, run, _ = seed_snapshots(db, clock, (paused, cooled, healthy))
        cfg = Config(
            config().hosts,
            {name: SourceConfig(True) for name in ("scoringdance", "eepro", "wsdc_calendar")},
        )
        for host in ("scoring.dance", "eepro.com", "worldsdc.com"):
            cached_robots(db.connection, archive, clock, host)
        selected_before_pause = next_watch(
            db.connection, cfg, now=clock.now(), exclude=(cooled.watch_id, healthy.watch_id)
        )
        assert selected_before_pause and selected_before_pause.watch_id == paused.watch_id
        change_control(
            db.state_dir,
            selector=Selector(selector_kind, selector_id),
            paused=True,
            actor="offline-reviewer",
            reason="acceptance fixture",
            now=clock.now(),
        )
        db.connection.execute(
            "UPDATE hosts SET paused_until=?,pause_reason='Throttled' WHERE host='eepro.com'",
            ((clock.now() + timedelta(hours=1)).isoformat(),),
        )
        choice = next_watch(db.connection, cfg, now=clock.now())
        assert choice and choice.watch_id == healthy.watch_id
        calls = []
        body = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()
        fetcher = FetchClient(
            db.connection,
            cfg,
            clock,
            archive,
            transport=httpx.MockTransport(
                lambda request: calls.append(str(request.url)) or httpx.Response(200, content=body)
            ),
            random_value=lambda: 0,
        )
        try:
            with servicing(selected_before_pause, run_id=run):
                assert fetcher.fetch(paused.watch_id, RoundPage(), run).skipped == "operator"
            assert not db.connection.execute("SELECT 1 FROM scheduler_requests").fetchone()
            with servicing(choice, run_id=run):
                assert fetcher.fetch(healthy.watch_id, EventsPage(), run).snapshot_id
        finally:
            fetcher.close()
        assert calls == [healthy.url]
        assert [
            tuple(row) for row in db.connection.execute("SELECT host,requests FROM host_budget")
        ] == [("worldsdc.com", 1)]
        assert [
            tuple(row)
            for row in db.connection.execute("SELECT host,work_key FROM scheduler_requests")
        ] == [("worldsdc.com", healthy.watch_id)]


def test_real_cycle_services_old_host_amid_current_demand_and_pending_derivation(tmp_path):
    import shutil

    import httpx

    from swingset.schedule.cycle import run_cycle
    from swingset.sources.scoringdance import SOURCE
    from swingset.sources.wsdc_calendar import EventsPage
    from swingset.state.work import WorkUnit, enqueue

    clock = FakeClock()
    config_dir, overrides = tmp_path / "config", tmp_path / "overrides"
    shutil.copytree("config", config_dir)
    shutil.copytree("overrides", overrides)
    (config_dir / "sources.toml").write_text(
        "[sources.wsdc_calendar]\nenabled=true\nindex_urls=[]\n[sources.scoringdance]\nenabled=true\n"
    )
    (overrides / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    (overrides / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    with open_database(tmp_path / "state") as db:
        current = tuple(
            WatchSpec(
                "",
                "wsdc_calendar",
                "index",
                "GET",
                f"https://worldsdc.com/current-{i}/",
                EventsPage.kind,
            )
            for i in range(12)
        )
        old = event_watch("old")
        archive, run, old_body = seed_snapshots(db, clock, (*current, old))
        # Default global scoring indexes have no demand in this controlled cohort.
        for seed in SOURCE.seed_watches(None, None):
            upsert_watch(db.connection, seed, clock.now())
            db.connection.execute(
                "UPDATE watches SET state='sealed',next_check_at=NULL WHERE watch_id=?",
                (seed.watch_id,),
            )
        event = Event(
            event_id="old",
            series_id="series",
            name="Old event",
            year=2019,
            start_date="2019-01-01",
            end_date="2019-01-02",
            source="scoringdance",
            snapshot_id="retained",
            parser_version="1",
            first_seen_at=clock.now().isoformat(),
            last_seen_at=clock.now().isoformat(),
            run_id=run,
        )
        replace_scope(
            db.connection,
            scope_kind="history",
            scope_id="fixture",
            projection=Projection((event,)),
            run_id=run,
            projected_at=clock.now().isoformat(),
        )
        db.connection.execute(
            "INSERT INTO source_event_map VALUES (?,?,?,'explicit',1)",
            (old.source, old.source_ref, "old"),
        )
        db.connection.execute(
            "UPDATE watches SET state='archived' WHERE watch_id=?", (old.watch_id,)
        )
        enqueue(
            db.connection,
            (WorkUnit("parse", "snapshot", "missing-independent-input"),),
            enqueued_at=clock.now().isoformat(),
        )
        for host in ("worldsdc.com", "scoring.dance"):
            cached_robots(db.connection, archive, clock, host)
        calls = []
        calendar = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()

        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(
                200, content=old_body if request.url.host == "scoring.dance" else calendar
            )

        result = run_cycle(
            db,
            config_dir=config_dir,
            overrides_dir=overrides,
            clock=clock,
            budget=30,
            transport=httpx.MockTransport(handler),
        )
        assert old.url in calls
        assert calls.index(old.url) < 4
        assert any(url.startswith("https://worldsdc.com/current-") for url in calls)
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM scheduler_requests WHERE run_id=? AND host='scoring.dance' AND category='old'",
                (result["run_id"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM pending_work WHERE unit_id='missing-independent-input'"
            ).fetchone()[0]
            == 1
        )
        assert db.connection.execute("SELECT SUM(requests) FROM host_budget").fetchone()[0] == len(
            calls
        )
