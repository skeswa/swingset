"""Durable local fairness and configuration do not alter interpretation claims."""

import shutil
from dataclasses import replace
from datetime import timedelta

import pytest

from swingset.clock import FakeClock
from swingset.config import Config
from swingset.schedule.fair_policy import SchedulerConfig, allocation, parse_scheduler
from swingset.schedule.fairness import backpressure, next_offline, record_offline_service, report
from swingset.state.attempts import begin_attempt, finish_attempt
from swingset.state.db import open_database
from swingset.state.inputs import accept, capture
from swingset.state.work import WorkUnit, enqueue


def test_soft_allocations_preserve_zero_budget_contract_and_reject_invalid_values():
    config = Config({}, {})
    result = allocation(config, 720)
    assert (result.reconciliation_seconds, result.acquisition_seconds, result.offline_seconds) == (
        30,
        300,
        390,
    )
    assert allocation(config, 0).acquisition_seconds == 0
    for value in (-1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            allocation(config, value)


@pytest.mark.parametrize(
    "body",
    [
        b"[scheduling]\npending_parse_items=1.5",
        b"[scheduling]\npending_parse_items=true",
        b"[scheduling]\nacquisition_share=0.9",
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
        path.write_text(path.read_text() + "\n[scheduling]\npending_parse_items=501\n")
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
