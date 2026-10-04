"""Durable local fairness and configuration do not alter interpretation claims."""

import re
import shutil
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from swingset.clock import FakeClock
from swingset.config import Config
from swingset.schedule.fair_policy import SchedulerConfig, parse_scheduler, shares
from swingset.schedule.fairness import (
    backpressure,
    next_offline,
    next_offline_batch,
    record_offline_service,
    report,
)
from swingset.state.attempts import begin_attempt, finish_attempt
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture
from swingset.state.work import WorkUnit, enqueue


def test_host_shares_cover_every_class_and_sum_to_one_hundred():
    for host in ("scoring.dance", "points.worldsdc.com", "web.archive.org", "example.org"):
        weights = shares(host)
        assert tuple(weights) == ("new", "current", "identity", "old")
        assert sum(weights.values()) == 100
    assert shares("points.worldsdc.com")["current"] == 0


@pytest.mark.parametrize(
    "body",
    [
        b"[scheduling]\npending_parse_items=1.5",
        b"[scheduling]\npending_parse_items=true",
        # The cycle's phase shares went with it (D-0239).
        b"[scheduling]\nacquisition_share=0.9",
        b"[scheduling]\noffline_share=0.5",
        b"[scheduling]\nunrecognized=2",
    ],
)
def test_invalid_scheduler_policy_is_rejected(body):
    with pytest.raises(ValueError):
        parse_scheduler(body)


def test_scheduler_bundle_change_is_accepted_without_reinterpreting_sources(tmp_path):
    config = tmp_path / "config"
    overrides = tmp_path / "overrides"
    shutil.copytree("config", config)
    shutil.copytree("overrides", overrides)
    state = tmp_path / "state"
    clock = FakeClock()
    with open_database(state) as db:
        first = capture(config, overrides, state, {})
        accept(db, first, clock)
        db.connection.execute("DELETE FROM pending_work")
        path = config / "sources.toml"
        # The shipped configuration already carries a [scheduling] table.
        body = path.read_text()
        # It may already set the key (D-0221); drop that line before overriding it.
        body = re.sub(r"(?m)^pending_parse_items\s*=.*\n", "", body)
        body = body.replace("[scheduling]\n", "[scheduling]\npending_parse_items=501\n", 1)
        assert "pending_parse_items=501" in body
        path.write_text(body)
        second = capture(config, overrides, state, {})
        assert second.config.scheduler.pending_parse_items == 501
        assert second.digest != first.digest
        assert accept(db, second, clock) == {"config/sources.toml"}
        assert db.connection.execute("SELECT count(*) FROM pending_work").fetchone()[0] == 0
        assert not accept(db, second, clock)


def test_continuous_independent_demand_does_not_starve_other_unit_kinds_after_restart(tmp_path):
    clock = FakeClock()
    units = [
        WorkUnit("parse", "snapshot", "a"),
        WorkUnit("project", "dancer", "dancer"),
        WorkUnit("project", "calendar", "calendar"),
        WorkUnit("project", "source_index", "index"),
    ]
    with open_database(tmp_path) as db:
        enqueue(db.connection, units, enqueued_at=clock.now().isoformat())
        first = next_offline(db.connection, now=clock.now())
        assert first is not None
        record_offline_service(db.connection, first, now=clock.now())
    with open_database(tmp_path) as db:
        selected = [first]
        for _ in range(3):
            unit = next_offline(db.connection, now=clock.now())
            selected.append(unit)
            record_offline_service(db.connection, unit, now=clock.now())
            # Keep every unit pending: continuous arrivals cannot exhaust a class.
            enqueue(db.connection, units, enqueued_at=clock.now().isoformat())
        assert set(selected) == set(units)
        assert (
            db.connection.execute("SELECT sum(attempts) FROM scheduler_offline_service").fetchone()[
                0
            ]
            == 4
        )


def test_retry_latches_and_pause_filter_remain_authoritative_for_fair_offline_work(tmp_path):
    clock = FakeClock()
    bad = WorkUnit("parse", "snapshot", "missing")
    paused = WorkUnit("project", "dancer", "paused")
    good = WorkUnit("project", "calendar", "healthy")
    with open_database(tmp_path) as db:
        run = db.start_run(clock.now())
        enqueue(db.connection, (bad, paused, good), enqueued_at=clock.now().isoformat())
        attempt = begin_attempt(db, bad, now=clock.now(), run_id=run)
        finish_attempt(
            db, attempt, now=clock.now(), outcome="blocked", reason_code="invalid_retained_input"
        )
        assert (
            next_offline(db.connection, now=clock.now(), allowed=lambda unit: unit != paused)
            == good
        )
        assert (
            next_offline(
                db.connection,
                now=clock.now() + timedelta(days=5),
                allowed=lambda unit: unit != paused,
                exclude=(good,),
            )
            is None
        )


def test_pending_item_pressure_does_not_require_artifact_rows(tmp_path):
    with open_database(tmp_path) as db:
        config = Config({}, {}, scheduler=replace(SchedulerConfig(), pending_parse_items=2))
        enqueue(
            db.connection,
            (WorkUnit("parse", "snapshot", str(i)) for i in range(2)),
            enqueued_at=FakeClock().now().isoformat(),
        )
        report = backpressure(db.connection, config)
        assert report["reasons"] == ["pending_parse_items"]
        assert report["pending_parse_bytes"] == 0
        assert report["active"]


def test_derivation_hints_do_not_pause_acquisition(tmp_path):
    """A code-only deploy queues every project and link unit as a hint; a
    derivation backlog must not close fetching (D-0214). Parse work does."""
    with open_database(tmp_path) as db:
        config = Config({}, {}, scheduler=replace(SchedulerConfig(), pending_work_items=3))
        at = FakeClock().now().isoformat()
        enqueue(
            db.connection,
            (
                *(WorkUnit("project", "dancer", str(i)) for i in range(5)),
                *(WorkUnit("link", "event", str(i)) for i in range(5)),
            ),
            enqueued_at=at,
        )
        assert not backpressure(db.connection, config)["active"]
        enqueue(
            db.connection, (WorkUnit("parse", "snapshot", str(i)) for i in range(3)), enqueued_at=at
        )
        assert backpressure(db.connection, config)["reasons"] == ["pending_work_items"]


def test_report_keeps_objectives_separate_and_supports_unmigrated_schema12(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO host_budget VALUES ('example.test',?,75,1234)",
            (clock.now().date().isoformat(),),
        )
        before = report(db.connection, Config({}, {}), now=clock.now())
        assert before["supported"]
        assert before["observed_attributed_service"] == []
        assert before["observed_host_usage"][0]["requests"] == 75
        assert before["initial_objectives"]["service_gap_seconds"] == 86400
        db.connection.execute("DROP TABLE scheduler_requests")
        db.connection.execute("UPDATE meta SET value='12' WHERE key='schema_version'")
        db.connection.execute("PRAGMA query_only=ON")
        old = report(db.connection, Config({}, {}), now=clock.now())
        assert not old["supported"]
        assert old["observed_attributed_service"] is None
        assert old["observed_host_usage"] == before["observed_host_usage"]
        assert (
            db.connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
            == "12"
        )


def test_daily_fair_share_counts_have_a_day_bounded_index(tmp_path):
    with open_database(tmp_path) as db:
        plan = " ".join(
            str(row[-1])
            for row in db.connection.execute(
                "EXPLAIN QUERY PLAN SELECT host,category,count(*) FROM scheduler_requests WHERE day=? GROUP BY host,category",
                ("2026-09-13",),
            )
        )
        assert (
            "SEARCH scheduler_requests USING COVERING INDEX scheduler_requests_class (day=?)"
            in plan
        )


def test_offline_batch_matches_single_selection_order_and_stays_within_one_group(tmp_path):
    clock = FakeClock()
    units = [
        WorkUnit("parse", "snapshot", "a"),
        WorkUnit("parse", "snapshot", "b"),
        WorkUnit("parse", "snapshot", "c"),
        WorkUnit("project", "dancer", "dancer"),
    ]
    with open_database(tmp_path) as db:
        enqueue(db.connection, units, enqueued_at=clock.now().isoformat())
        first = next_offline(db.connection, now=clock.now())
        batch = next_offline_batch(db.connection, now=clock.now(), limit=2)
        assert first is not None and batch[0] == first
        assert len(batch) == 2 and len(set(batch)) == 2
        # A batch is one selection group: parse snapshots here, never mixed
        # with the dancer projection that a later group would serve.
        assert {unit.stage for unit in batch} == {"parse"}
        rest = next_offline_batch(db.connection, now=clock.now(), exclude=batch, limit=10)
        assert batch[0] not in rest and batch[1] not in rest
        assert WorkUnit("parse", "snapshot", "c") in rest
        assert next_offline_batch(db.connection, now=clock.now(), exclude=units, limit=10) == []


def test_build_is_deferred_only_while_backlog_and_fresh_baseline_coincide(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta

    from swingset.config import Config
    from swingset.schedule.release_steps import build_deferred_for_backlog

    config = Config({}, {})
    now = datetime(2026, 9, 21, 2, 0, tzinfo=UTC)
    (tmp_path / "candidates" / "cand_x").mkdir(parents=True)
    (tmp_path / "baseline").symlink_to(tmp_path / "candidates" / "cand_x")
    published = tmp_path / "candidates" / "cand_x" / "PUBLISHED"
    active = {"active": True}
    # No baseline receipt yet: build.
    assert not build_deferred_for_backlog(tmp_path, config, pressure=active, now=now)
    published.write_text(json.dumps({"verified_at": (now - timedelta(hours=1)).isoformat()}))
    assert build_deferred_for_backlog(tmp_path, config, pressure=active, now=now)
    # No backlog: build as before.
    assert not build_deferred_for_backlog(tmp_path, config, pressure={"active": False}, now=now)
    # An old enough baseline builds even under backlog.
    published.write_text(json.dumps({"verified_at": (now - timedelta(hours=7)).isoformat()}))
    assert build_deferred_for_backlog(tmp_path, config, pressure=active, now=now) is False


def test_a_kind_without_a_code_scope_leaves_the_other_kinds_runnable(tmp_path, monkeypatch):
    # recipe_inputs fails closed for a kind with no accepted code scope. On
    # the readiness path (a unit with a generation compares its certificate,
    # which reads the recipe) that error must cost only that kind (I1).
    from swingset.state import derivations
    from swingset.state.recipes import recipe_inputs

    ready = derivations.ready

    def certified(conn, unit):
        recipe_inputs(conn, unit.stage, unit.unit_kind)
        return ready(conn, unit)

    monkeypatch.setattr(derivations, "ready", certified)
    clock = FakeClock()
    dancer = WorkUnit("project", "dancer", "dancer")
    calendar = WorkUnit("project", "calendar", "calendar")
    with open_database(tmp_path) as db:
        conn = db.connection
        conn.executemany(
            "INSERT INTO accepted_inputs VALUES ('pipeline',?,?)",
            (("recipe/env", "env"), ("recipe/code/project/calendar", "calendar")),
        )
        enqueue(conn, [dancer, calendar], enqueued_at=clock.now().isoformat())
        errors: dict[str, str] = {}
        selected = []
        while batch := next_offline_batch(
            conn, now=clock.now(), exclude=selected, limit=10, errors=errors
        ):
            selected.extend(batch)
        assert selected == [calendar]
        assert errors == {
            "project/dancer": "accepted runtime has no code scope recipe/code/project/dancer"
        }


def test_backpressure_counts_only_parse_units_derive_could_run(tmp_path):
    """Blocked, backing-off and quarantined parses do not close acquisition (D-0216)."""
    from swingset.state.queue import at

    now = FakeClock().now()
    config = Config(
        {},
        {},
        scheduler=replace(SchedulerConfig(), pending_parse_items=2000, pending_work_items=2000),
    )

    def queued(conn, first, states, next_attempt):
        units = [WorkUnit("parse", "snapshot", f"s{i}") for i in range(first, first + 2000)]
        enqueue(conn, units, enqueued_at=now.isoformat())
        conn.executemany(
            "INSERT INTO work_queue(stage,unit_kind,unit_id,partition,state,next_attempt_at,"
            "lease_owner,lease_until,enqueued_at,updated_at) "
            "VALUES ('parse','snapshot',?,?,?,?,?,?,?,?)",
            [
                (unit.unit_id, f"snapshot/{unit.unit_id}", state, next_attempt)
                + (("derive:r", at(now)) if state == "leased" else (None, None))
                + (at(now), at(now))
                for i, unit in enumerate(units)
                for state in [states[i % len(states)]]
            ],
        )

    with open_database(tmp_path) as db:
        conn = db.connection
        # 2,000 parse units wait on a clock: for input, in quarantine, or backing off.
        queued(conn, 0, ("waiting_input", "quarantined", "ready"), at(now + timedelta(hours=1)))
        report = backpressure(conn, config, now=now)
        assert (report["pending"]["parse"], report["runnable_parse"]) == (2000, 0)
        assert not report["active"]
        # 2,000 that derive could run now hold acquisition.
        queued(conn, 2000, ("ready", "leased"), at(now))
        report = backpressure(conn, config, now=now)
        assert report["runnable_parse"] == 2000
        assert report["reasons"] == ["pending_parse_items", "pending_work_items"]
        # A backoff that ends makes its unit runnable again.
        assert backpressure(conn, config, now=now + timedelta(hours=2))["runnable_parse"] == 2666


def _robots_world(tmp_path, db, clock, *, blocked_due):
    """300 watches on a host whose robots read failed 20 minutes ago (the
    third in a row, so the retry is an hour after it, with the host pause
    lifted meanwhile), and one due watch on another host."""
    from swingset.config import HostConfig, SourceConfig
    from swingset.fetch.archive import Archive
    from swingset.schedule.watches import upsert_watch
    from swingset.sources.base import WatchSpec

    config = Config(
        {"blocked.test": HostConfig(), "open.test": HostConfig()},
        {"wsdc_calendar": SourceConfig(True)},
    )
    conn = db.connection
    for number in range(300):
        spec = WatchSpec(
            "",
            "wsdc_calendar",
            "index",
            "GET",
            f"https://blocked.test/events/{number}",
            "wsdc_calendar.events",
        )
        upsert_watch(conn, spec, clock.now())
    # Due two hours ago, so the blocked host ranks first; or not due at all.
    when = clock.now() + timedelta(hours=-2 if blocked_due else 2)
    conn.execute("UPDATE watches SET next_check_at=?", (when.isoformat(),))
    upsert_watch(
        conn,
        WatchSpec(
            "", "wsdc_calendar", "index", "GET", "https://open.test/events", "wsdc_calendar.events"
        ),
        clock.now(),
    )
    fetched = clock.now() - timedelta(minutes=20)
    conn.execute(
        "INSERT INTO hosts(host,robots_sha256,robots_fetched_at,robots_status,pause_streak) "
        "VALUES ('blocked.test',?,?,599,3)",
        (Archive(tmp_path).store_body(b""), fetched.isoformat()),
    )
    return config, fetched


def _robots_pass(tmp_path, db, config, clock, requested):
    """One 60 s acquisition pass where each selection costs 0.4 s, as in
    production; robots.txt answers 404 (no rules) on every host."""
    import httpx

    from swingset.fetch.archive import Archive
    from swingset.schedule.acquisition import Acquisition
    from swingset.schedule.fairness import next_watch

    body = Path("tests/fixtures/sources/synthetic_calendar.html").read_bytes()
    selections = []

    def handler(request):
        requested.append((request.url.host, request.url.path))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    def select(*args, **kwargs):
        clock.sleep(0.4)
        choice = next_watch(*args, **kwargs)
        selections.append(choice and choice.host)
        return choice

    summary = {
        "checked": 0,
        "changed": 0,
        "not_modified": 0,
        "nonce_unchanged": 0,
        "bytes_by_host": {},
        "stage_errors": [],
        "guards": {"passes": 0, "recorded": 0, "cleared": 0},
    }
    acquiring = Acquisition(
        db,
        config,
        clock,
        db.start_run(clock.now()),
        summary,
        archive=Archive(tmp_path),
        stop=lambda: False,
        should_stop=lambda: False,
        transport=httpx.MockTransport(handler),
        select=select,
    )
    try:
        issued, _ = acquiring.run(clock.now() + timedelta(seconds=60), lambda _until: ())
    finally:
        acquiring.close()
    return issued, summary, selections


def test_a_host_whose_robots_read_failed_does_not_starve_another_hosts_due_watch(tmp_path):
    """D-0242 (2026-10-08/09): a failed robots read on a host with hundreds of
    due watches left the registry unserved, because each of them was picked,
    skipped as robots-disallowed, and the selection re-ran over every due
    watch. The blocked host is left out and the other host's watch is
    fetched; after the retry a pass reads robots.txt and the guard goes."""
    from swingset.state import guards
    from swingset.state.queue import at

    clock = FakeClock()
    with open_database(tmp_path) as db:
        config, fetched = _robots_world(tmp_path, db, clock, blocked_due=True)
        conn = db.connection
        requested = []
        issued, summary, selections = _robots_pass(tmp_path, db, config, clock, requested)
        assert issued and summary["checked"] == 1
        assert requested == [("open.test", "/robots.txt"), ("open.test", "/events")]
        assert "blocked.test" not in selections
        # One host guard, not one per watch, expiring an hour after the retry.
        assert guards.counts_by_reason(conn) == {guards.ROBOTS_UNREAD: 1}
        row = guards.get(conn, "host:blocked.test", guards.ROBOTS_UNREAD)
        assert row is not None
        assert row.expires_at == at(fetched + timedelta(hours=2))
        # After the retry a pass reads robots.txt again, fetches the host's
        # watches, and the guard row goes with the host's request.
        clock.sleep(3600)
        requested.clear()
        issued, summary, _ = _robots_pass(tmp_path, db, config, clock, requested)
        assert issued and requested[0] == ("blocked.test", "/robots.txt")
        assert summary["checked"] > 1
        assert all(host == "blocked.test" for host, _ in requested)
        assert guards.counts_by_reason(conn) == {}


def test_a_blocked_host_with_nothing_due_leaves_no_guard_and_acquire_idle_alarms(tmp_path):
    """The guard says a fetch was held: a host whose robots read failed but
    had no due watch writes none, so it cannot silence acquire [loop-idle]
    (the watchdog's `guards_recent`)."""
    import importlib.util

    from swingset.state import guards

    spec = importlib.util.spec_from_file_location("swingset_watchdog", "nix/watchdog.py")
    assert spec is not None and spec.loader is not None
    watchdog = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watchdog)
    clock = FakeClock()
    with open_database(tmp_path) as db:
        config, _ = _robots_world(tmp_path, db, clock, blocked_due=False)
        issued, summary, _ = _robots_pass(tmp_path, db, config, clock, [])
        assert issued and summary["checked"] == 1
        assert guards.counts_by_reason(db.connection) == {}
        since = watchdog._queue_at((clock.now() - timedelta(hours=1)).timestamp())
        recent = db.connection.execute(watchdog.QUERIES["guards_recent"], {"since": since})
        assert recent.fetchone()[0] == 0
