"""Bounded blocker samples retain changes without manufacturing elapsed eligibility."""

import sqlite3
from dataclasses import replace

import pytest
from test_event_enumerations import admit_parent, finish_bootstrap
from test_event_enumerations import event as event

from swingset.backup.checkpoint import create_checkpoint, restore_checkpoint
from swingset.config import Config, HostConfig, SourceConfig
from swingset.schedule import event_blocker_history as history
from swingset.schedule import event_pressure
from swingset.schedule.watches import upsert_watch
from swingset.state.controls import Selector, change_control
from swingset.state.db import SCHEMA_VERSION


def config():
    return Config(
        {"eepro.com": HostConfig(daily_request_budget=2, daily_byte_budget=100)},
        {"eepro": SourceConfig(True)},
    )


def sample(f, policy=None, *, hold=False, limit=8):
    return history.refresh(
        f.db,
        policy or config(),
        now=f.corpus.clock.now(),
        run_id=f.corpus.run,
        operator_hold=hold,
        max_events=limit,
    )


def report(f, *, limit=20):
    return history.report(f.conn, source="eepro", source_ref="eepro:test", limit=limit)


def add_event(f, name):
    spec = replace(f.parent, url=f"https://eepro.com/results/{name}/", source_ref=f"eepro:{name}")
    upsert_watch(f.conn, spec, f.corpus.clock.now())
    finish_bootstrap(f)


def test_unchanged_samples_ignore_counters_and_clock_but_keep_refresh_summaries(event):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    first = sample(f)
    f.corpus.clock.sleep(5)
    f.conn.execute(
        "INSERT INTO host_budget VALUES ('eepro.com',?,1,50)",
        (f.corpus.clock.now().date().isoformat(),),
    )
    f.conn.execute(
        "UPDATE watches SET last_checked_at=?,unchanged_streak=unchanged_streak+1",
        (f.corpus.clock.now().isoformat(),),
    )
    second = sample(f)
    assert first["changed_events"] == 1 and second["changed_events"] == 0
    assert first["refresh_id"] != second["refresh_id"]
    assert (
        f.conn.execute(
            "SELECT count(*) FROM event_blocker_refreshes WHERE run_id=?", (f.corpus.run,)
        ).fetchone()[0]
        == 2
    )
    assert len(report(f)["recent"]) == 1
    assert report(f)["eligible_service_age_seconds"] is None
    assert report(f)["successful_progress_at"] is None


def test_overlapping_pause_budget_cooldown_and_retry_boundaries(event):
    f = event
    finish_bootstrap(f)
    now = f.corpus.clock.now()
    from datetime import timedelta

    boundary = (now + timedelta(seconds=10)).isoformat()
    f.conn.execute("UPDATE watches SET next_check_at=?,paused_until=?", (boundary, boundary))
    f.conn.execute(
        "INSERT INTO hosts(host,next_allowed_at,paused_until,pause_reason) VALUES ('eepro.com',?,?,'Blocked')",
        (boundary, boundary),
    )
    f.conn.execute(
        "INSERT INTO host_budget VALUES ('eepro.com',?,2,100)", (now.date().isoformat(),)
    )
    pause = change_control(
        f.db.state_dir,
        selector=Selector("source", "eepro"),
        paused=True,
        actor="test",
        reason="review",
        now=now,
        until=now + timedelta(seconds=10),
    )
    first = sample(f, hold=True)
    expected = {
        "operator_hold",
        "operator_pause",
        "watch_waiting",
        "watch_pause",
        "host_pause",
        "host_cooldown",
        "request_budget",
        "byte_budget",
    }
    assert expected <= set(first["reason_counts"])
    facts = report(f)["latest"]["facts"]
    assert facts["operator_pauses"][0]["pause_id"] == pause["pause_id"]
    assert facts["watches"][0]["next_check_at"] == boundary
    assert facts["hosts"][0]["next_reset_at"]
    f.corpus.clock.sleep(2)
    assert sample(f, hold=True)["changed_events"] == 0
    f.corpus.clock.sleep(8)
    after_expiry = sample(f)
    assert after_expiry["changed_events"] == 1
    assert set(after_expiry["reason_counts"]) == {"request_budget", "byte_budget"}
    # Expiry observation does not delete an operator record or refund usage.
    assert f.conn.execute("SELECT count(*) FROM operator_pauses").fetchone()[0] == 1
    assert f.conn.execute("SELECT requests,bytes FROM host_budget").fetchone()[:] == (2, 100)
    f.corpus.clock.sleep(86400)
    assert sample(f)["reason_counts"] == {}


def test_policy_input_enumeration_and_control_revision_changes_are_recorded(event):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    sample(f)
    old = report(f)["latest"]["facts"]
    policy = replace(config(), sources={"eepro": SourceConfig(False)})
    assert sample(f, policy)["changed_events"] == 1
    assert report(f)["latest"]["facts"]["policy_digest"] != old["policy_digest"]
    f.conn.execute("INSERT INTO meta VALUES ('input_bundle_hash','new-bundle')")
    assert sample(f, policy)["changed_events"] == 1
    assert report(f)["latest"]["facts"]["input_bundle_hash"] == "new-bundle"
    admit_parent(f, ["one.htm", "two.htm"])
    finish_bootstrap(f)
    assert sample(f, policy)["changed_events"] == 1
    assert report(f)["latest"]["facts"]["enumeration_id"] != old["enumeration_id"]
    change_control(
        f.db.state_dir,
        selector=Selector("source", "eepro"),
        paused=True,
        actor="test",
        reason="review",
        now=f.corpus.clock.now(),
    )
    assert sample(f, policy)["changed_events"] == 1
    assert report(f)["latest"]["facts"]["control_revision"] > old["control_revision"]


def test_legacy_and_oversized_events_stay_explicitly_unassessed(event, monkeypatch):
    f = event
    finish_bootstrap(f)
    sample(f)
    assert report(f)["latest"]["facts"]["unassessed_inputs"] == ["legacy_enumeration_unknown"]
    admit_parent(f, [f"page-{i}.htm" for i in range(history.MEMBER_LIMIT + 1)])
    finish_bootstrap(f)
    original = history.readiness

    def bounded(*args, **kwargs):
        assert kwargs["watch_ids"] == []
        return original(*args, **kwargs)

    monkeypatch.setattr(history, "readiness", bounded)
    result = sample(f)
    assert result["unassessed_events"] == 1 and result["incomplete_observation_coverage"]
    facts = report(f)["latest"]["facts"]
    assert facts["unassessed_inputs"] == ["member_limit"]
    assert facts["incomplete_gate_assessment"] and not facts["watches"]


def test_watch_count_is_bounded_independently_of_members(event):
    f = event
    for i in range(history.WATCH_LIMIT + 1):
        upsert_watch(
            f.conn, replace(f.parent, url=f"https://eepro.com/extra/{i}/"), f.corpus.clock.now()
        )
    finish_bootstrap(f)
    assert sample(f)["unassessed_events"] == 1
    assert "watch_limit" in report(f)["latest"]["facts"]["unassessed_inputs"]


def test_expansion_denial_is_observed_without_claiming_other_requests_allowed(event):
    f = event
    policy = replace(
        config(), scheduler=replace(config().scheduler, event_pressure_high=2, event_pressure_low=1)
    )
    for name in ("one", "two", "three"):
        parent = replace(
            f.parent, url=f"https://eepro.com/results/{name}/", source_ref=f"eepro:{name}"
        )
        upsert_watch(f.conn, parent, f.corpus.clock.now())
        admit_parent(f, ["round.htm"], parent=parent)
    finish_bootstrap(f)
    while f.conn.execute("SELECT 1 FROM event_pressure_dirty LIMIT 1").fetchone():
        event_pressure.bootstrap(f.db, policy, now=f.corpus.clock.now())
    add_event(f, "newcomer")
    result = sample(f, policy)
    assert result["reason_counts"]["event_expansion_deferred"] >= 1
    facts = history.report(f.conn, source="eepro", source_ref="eepro:newcomer")["latest"]["facts"]
    assert facts["watches"][0]["expansion_denial"] == "event expansion pressure"
    assert facts["incomplete_gate_assessment"]
    assert "eligible" not in facts and "allowed" not in facts["watches"][0]


def test_backpressure_counts_do_not_create_changes_while_reasons_remain_the_same(event):
    f = event
    policy = replace(
        config(),
        scheduler=replace(
            config().scheduler, pending_parse_items=1, pending_parse_bytes=1, pending_work_items=1
        ),
    )
    f.corpus.snapshot("pending-one")
    finish_bootstrap(f)
    first = sample(f, policy)
    assert {"pending_parse_items", "pending_parse_bytes", "pending_work_items"} <= set(
        first["reason_counts"]
    )
    f.corpus.snapshot("pending-two")
    assert sample(f, policy)["changed_events"] == 0
    f.conn.execute("DELETE FROM pending_work")
    assert sample(f, policy)["changed_events"] == 1
    assert report(f)["latest"]["facts"]["backpressure_reasons"] == []


def test_frozen_catalog_pass_rotates_before_continuous_new_arrivals(event):
    f = event
    finish_bootstrap(f)
    add_event(f, "second")
    add_event(f, "third")
    observed = []
    for i in range(3):
        result = sample(f, limit=1)
        observed.append(
            f.conn.execute(
                "SELECT source_ref FROM event_blocker_observations WHERE refresh_id=?",
                (result["refresh_id"],),
            ).fetchone()[0]
        )
        add_event(f, f"arrival-{i}")
        assert result["scanned_events"] == 1 and result["incomplete_observation_coverage"]
    assert observed == ["eepro:test", "eepro:second", "eepro:third"]
    assert result["pass_finished"]
    # Revisit the beginning before walking the arrivals in the next frozen pass.
    f.conn.execute("UPDATE watches SET next_check_at=NULL WHERE source_ref='eepro:test'")
    following = sample(f, limit=1)
    assert (
        f.conn.execute(
            "SELECT source_ref FROM event_blocker_observations WHERE refresh_id=?",
            (following["refresh_id"],),
        ).fetchone()[0]
        == "eepro:test"
    )
    assert following["catalog_pass"] == result["catalog_pass"] + 1


def test_failed_sample_commit_rolls_back_history_summary_policy_and_cursor(event):
    f = event
    finish_bootstrap(f)
    add_event(f, "second")
    before = tuple(f.conn.execute("SELECT * FROM event_blocker_cursor").fetchone())
    f.conn.execute(
        "CREATE TEMP TRIGGER fail_second BEFORE INSERT ON event_blocker_observations WHEN NEW.source_ref='eepro:second' BEGIN SELECT RAISE(ABORT,'test failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        sample(f)
    for table in (
        "event_blocker_observations",
        "event_blocker_refreshes",
        "event_blocker_policies",
        "event_blocker_latest",
    ):
        assert not f.conn.execute(f"SELECT 1 FROM {table}").fetchone()
    assert tuple(f.conn.execute("SELECT * FROM event_blocker_cursor").fetchone()) == before


def test_history_report_is_read_only_bounded_and_survives_checkpoint_restore(event):
    f = event
    finish_bootstrap(f)
    sample(f)
    sample(f, hold=True)
    expected = report(f)
    saved = create_checkpoint(
        f.db.state_dir,
        f.conn,
        f.db.state_dir / "checkpoints" / "blockers",
        schema_version=SCHEMA_VERSION,
        versions={},
        input_bundle_hash=None,
    )
    target = f.db.state_dir / "restored"
    restore_checkpoint(saved.path, target, maximum_schema_version=SCHEMA_VERSION)
    with sqlite3.connect(
        (target / "state.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True
    ) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        try:
            actual = history.report(conn, source="eepro", source_ref="eepro:test")
            assert actual == expected
            bounded = history.report(conn, source="eepro", source_ref="eepro:test", limit=1)
            assert bounded["details_truncated"] and len(bounded["recent"]) == 1
            assert bounded["latest"] == expected["latest"]
            assert not conn.total_changes
        finally:
            conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        f.conn.execute("UPDATE event_blocker_observations SET observed_at='changed'")


def test_unknown_old_schema_and_bounds(event):
    with sqlite3.connect(":memory:") as conn:
        conn.execute("PRAGMA query_only=ON")
        assert not history.report(conn, source="eepro", source_ref="eepro:test")["supported"]
    for value in (0, 101, True, 1.5):
        with pytest.raises(ValueError):
            sample(event, limit=value)
        with pytest.raises(ValueError):
            report(event, limit=value)
