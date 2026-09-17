"""Closed intervals cannot turn missing eligibility history into exact age."""

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from test_event_enumerations import admit_parent, finish_bootstrap
from test_event_enumerations import event as event
from test_event_pressure import config, enroll

from swingset.clock import FakeClock
from swingset.fetch.client import FetchClient
from swingset.fetch.eligibility import Assessment, union
from swingset.schedule.event_timing import Boundary, Recorder, alarms, report
from swingset.schedule.event_timing_observer import Observer, checkpoint, observing, resume
from swingset.state.db import open_database


def boundary(clock, **changes):
    return replace(Boundary(clock.now(), clock.monotonic(), 0, (0, 0, False), "token"), **changes)


def recorder(db, clock, **kwargs):
    return Recorder(
        db,
        source="eepro",
        source_ref="event",
        run_id="cycle",
        token="token",
        policy={"service_gap": 10, "no_successful_progress": 10},
        at=boundary(clock),
        **kwargs,
    )


def eligible(clock, seconds=100):
    return Assessment("eligible", "proven", clock.now() + timedelta(seconds=seconds))


def test_failed_service_resets_only_service_clock_and_hold_suppresses_alarm(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        rec = recorder(db, clock)
        rec.open(boundary(clock), eligible(clock))
        clock.sleep(12)
        rec.close(boundary(clock))
        assert (
            alarms(rec.row, now=clock.now(), operator_hold=False)["no_successful_progress"]["state"]
            == "alert"
        )
        rec.open(boundary(clock), eligible(clock), service=1)
        assert rec.row["since_service_seconds"] == 0
        assert rec.row["since_progress_seconds"] == 12
        assert (
            alarms(rec.row, now=clock.now(), operator_hold=True)["no_successful_progress"]["state"]
            == "suppressed_by_hold"
        )
        clock.sleep(4)
        rec.close(boundary(clock))
        rec.open(
            boundary(clock),
            eligible(clock),
            service=1,
            progress=1,
            progress_at=clock.now().isoformat(),
        )
        assert rec.row["since_progress_seconds"] == 0
        assert rec.row["eligible_seconds"] == 16


@pytest.mark.parametrize("change", ["control", "marker", "token", "wall"])
def test_between_sample_pause_hold_input_and_clock_jumps_are_unknown(tmp_path, change):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        rec = recorder(db, clock)
        rec.open(boundary(clock), eligible(clock))
        clock.sleep(12)
        at = boundary(clock)
        at = replace(
            at,
            **{
                "control": {"revision": 2},
                "marker": {"marker": (1, 0, False)},
                "token": {"token": "new"},
                "wall": {"wall": at.wall + timedelta(hours=1)},
            }[change],
        )
        rec.close(at)
        assert rec.row["eligible_seconds"] == 0
        assert rec.row["unknown_seconds"] == 12
        assert not rec.row["complete_coverage"]
        assert (
            alarms(rec.row, now=clock.now(), operator_hold=False)["service_gap"]["state"]
            == "unknown"
        )


def test_known_boundary_caps_eligibility_without_automatically_opening_other_gates(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        rec = recorder(db, clock)
        rec.open(boundary(clock), eligible(clock, 5))
        clock.sleep(12)
        rec.close(boundary(clock))
        assert rec.row["eligible_seconds"] == 5
        assert rec.row["unknown_seconds"] == 7
        rec.open(
            boundary(clock),
            Assessment("blocked", "daily_budget", clock.now() + timedelta(seconds=5)),
        )
        clock.sleep(8)
        rec.close(boundary(clock))
        assert rec.row["blocked_seconds"] == 5
        assert rec.row["unknown_seconds"] == 10
        assert rec.row["since_progress_seconds"] == 5


def test_union_does_not_double_count_shared_page_waiting_and_read_is_pure(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        rec = recorder(db, clock)
        rec.open(
            boundary(clock),
            union(
                [eligible(clock), eligible(clock)], deadline=clock.now() + timedelta(seconds=100)
            ),
        )
        clock.sleep(12)
        rec.close(boundary(clock))
        before = db.connection.total_changes
        result = report(
            db.connection,
            source="eepro",
            source_ref="event",
            now=clock.now() + timedelta(days=1),
            operator_hold=False,
        )
        assert db.connection.total_changes == before
        assert result["eligible_waiting_seconds_lower_bound"] == 12
        assert result["eligible_service_age_seconds"] is None
        assert result["alarms"]["service_gap"]["state"] == "unknown"


def test_crash_restart_keeps_closed_time_and_never_credits_open_interval(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        rec = recorder(db, clock)
        rec.open(boundary(clock), eligible(clock))
        clock.sleep(4)
        rec.close(boundary(clock))
        rec.open(boundary(clock), eligible(clock))
        clock.sleep(12)
        restarted = recorder(db, clock)
        assert restarted.row["eligible_seconds"] == 4
        assert restarted.row["unknown_seconds"] == 12
        assert not restarted.row["complete_coverage"]


def test_unqualified_success_disables_alarm_and_new_policy_preserves_old_episode(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        rec = recorder(db, clock)
        rec.open(boundary(clock), eligible(clock), unresolved_success=True)
        clock.sleep(12)
        rec.close(boundary(clock))
        assert (
            alarms(rec.row, now=clock.now(), operator_hold=False)["no_successful_progress"]["state"]
            == "unknown"
        )
        Recorder(
            db,
            source="eepro",
            source_ref="event",
            run_id="cycle",
            token="changed",
            policy={},
            at=boundary(clock, token="changed"),
        )
        assert db.connection.execute("SELECT count(*) FROM event_timing_history").fetchone()[0] == 2


def prepared(f, *, names=("one.htm",)):
    admit_parent(f, list(names))
    finish_bootstrap(f)
    policy = config()
    enroll(f, policy)
    f.conn.execute(
        "UPDATE watches SET next_check_at=?,state='live',paused_until=NULL",
        (f.corpus.clock.now().isoformat(),),
    )
    f.conn.execute("DELETE FROM pending_work")
    sha = f.archive.store_body(b"User-agent: *\nAllow: /\n")
    f.conn.execute(
        "INSERT OR REPLACE INTO hosts(host,robots_sha256,robots_fetched_at,robots_status) VALUES('eepro.com',?,?,200)",
        (sha, f.corpus.clock.now().isoformat()),
    )
    return policy


def test_real_observer_records_waiting_without_fetch_and_closes_phase(event):
    f, clock = event, event.corpus.clock
    policy = prepared(f, names=("one.htm", "two.htm"))
    client = FetchClient(
        f.conn,
        policy,
        clock,
        f.archive,
        transport=httpx.MockTransport(lambda _: pytest.fail("no request expected")),
    )
    try:
        observer = Observer(
            f.db, client, clock, run_id=f.corpus.run, deadline=clock.now() + timedelta(seconds=100)
        )
        with observing(observer):
            assert observer.subjects[0]["reason"] is None
            assert observer.subjects[0]["recorder"].row["state"] == "eligible"
            clock.sleep(12)
            checkpoint()
            resume()
        row = f.conn.execute("SELECT * FROM event_timing WHERE source_ref='eepro:test'").fetchone()
        assert row["eligible_seconds"] == 12
        assert row["state"] == "inactive"
        assert not f.conn.execute("SELECT 1 FROM host_budget").fetchone()
    finally:
        client.close()


def test_real_observer_oversized_membership_and_missing_robots_remain_unknown(event):
    f, clock = event, event.corpus.clock
    policy = prepared(f, names=tuple(f"{i}.htm" for i in range(33)))
    client = FetchClient(f.conn, policy, clock, f.archive)
    try:
        with observing(
            Observer(
                f.db,
                client,
                clock,
                run_id=f.corpus.run,
                deadline=clock.now() + timedelta(seconds=100),
            )
        ):
            clock.sleep(10)
        row = f.conn.execute("SELECT * FROM event_timing WHERE source_ref='eepro:test'").fetchone()
        assert row["eligible_seconds"] == 0
        assert row["unknown_seconds"] == 10
    finally:
        client.close()


def test_actual_network_wait_credits_other_host_event_and_preserves_one_debit_owner(event):
    from swingset.schedule.fairness import next_watch, prepare_event_turns, servicing
    from swingset.schedule.watches import upsert_watch
    from swingset.sources import get_page_kind

    f, clock = event, event.corpus.clock
    policy = prepared(f)
    other = replace(f.parent, url="https://other.test/results/second/", source_ref="eepro:second")
    upsert_watch(f.conn, other, clock.now())
    admit_parent(f, ["second.htm"], parent=other)
    finish_bootstrap(f)
    enroll(f, policy)
    f.conn.execute("DELETE FROM pending_work")
    f.conn.execute(
        "UPDATE watches SET next_check_at=?,state='live',paused_until=NULL",
        (clock.now().isoformat(),),
    )
    sha = f.archive.store_body(b"User-agent: *\nAllow: /\n")
    f.conn.execute(
        "INSERT OR REPLACE INTO hosts(host,robots_sha256,robots_fetched_at,robots_status) VALUES('other.test',?,?,200)",
        (sha, clock.now().isoformat()),
    )

    # Serve an unrelated request on eepro; both monitored events keep their
    # actual missing obligations and only the other host can pass its gate.
    def response(request):
        clock.sleep(12)
        return httpx.Response(500, content=b"temporary failure")

    client = FetchClient(
        f.conn,
        policy,
        clock,
        f.archive,
        transport=httpx.MockTransport(response),
        random_value=lambda: 0,
    )
    try:
        with f.db.transaction():
            prepare_event_turns(f.conn, policy, now=clock.now(), run_id=f.corpus.run)
        choice = next_watch(f.conn, policy, now=clock.now(), run_id=f.corpus.run)
        assert choice is not None
        observer = Observer(
            f.db, client, clock, run_id=f.corpus.run, deadline=clock.now() + timedelta(seconds=15)
        )
        with observing(observer), servicing(choice, run_id=f.corpus.run):
            parser = f.conn.execute(
                "SELECT parser FROM watches WHERE watch_id=?", (choice.watch_id,)
            ).fetchone()[0]
            client.fetch(
                choice.watch_id,
                get_page_kind(parser),
                f.corpus.run,
                deadline=clock.now() + timedelta(seconds=15),
            )
        rows = {
            row["source_ref"]: dict(row)
            for row in f.conn.execute(
                "SELECT * FROM event_timing WHERE source_ref IN ('eepro:test','eepro:second')"
            )
        }
        assert sum(row["eligible_seconds"] for row in rows.values()) >= 12
        assert sum(row["blocked_seconds"] for row in rows.values()) >= 12
        owners = list(f.conn.execute("SELECT DISTINCT source_ref FROM scheduler_event_requests"))
        assert len(owners) == 1
        assert all(row["last_progress_at"] is None for row in rows.values())
    finally:
        client.close()


def test_artifact_pause_skips_observer_without_blocking_acquisition_context(event):
    from swingset.state.controls import Selector, change_control

    f, clock = event, event.corpus.clock
    policy = prepared(f)
    change_control(
        f.db.state_dir,
        selector=Selector("kind", "archive_artifact"),
        paused=True,
        actor="test",
        reason="no artifact verification",
        now=clock.now(),
    )
    client = FetchClient(f.conn, policy, clock, f.archive)
    try:
        observer = Observer(
            f.db, client, clock, run_id=f.corpus.run, deadline=clock.now() + timedelta(seconds=100)
        )
        with observing(observer):
            assert observer.subjects == []
        assert not f.conn.execute("SELECT 1 FROM event_timing").fetchone()
    finally:
        client.close()


def test_preexisting_success_without_progress_receipt_does_not_disable_new_episode_alarm(event):
    from swingset.state.event_progress import acquired

    f, clock = event, event.corpus.clock
    policy = prepared(f)
    policy = replace(
        policy,
        scheduler=replace(
            policy.scheduler,
            event_acquisition_service_alarm_seconds=10,
            event_acquisition_progress_alarm_seconds=10,
        ),
    )
    snapshot = f.conn.execute(
        "SELECT snapshot_id FROM snapshots WHERE watch_id=?", (f.parent.watch_id,)
    ).fetchone()[0]
    with f.db.transaction():
        acquired(f.conn, snapshot_id=snapshot, source="eepro", parser="eepro.autoindex")
    assert not f.conn.execute("SELECT 1 FROM event_progress_receipts").fetchone()
    client = FetchClient(f.conn, policy, clock, f.archive)
    try:
        observer = Observer(
            f.db, client, clock, run_id=f.corpus.run, deadline=clock.now() + timedelta(seconds=100)
        )
        with observing(observer):
            clock.sleep(12)
            checkpoint()
            resume()
            rec = next(s["recorder"] for s in observer.subjects if s["source_ref"] == "eepro:test")
            assert not rec.row["unresolved_success"]
            assert (
                alarms(rec.row, now=clock.now(), operator_hold=False)["no_successful_progress"][
                    "state"
                ]
                == "alert"
            )
    finally:
        client.close()


def test_real_failed_fetch_does_not_reset_success_wait_across_reverified_phase(event):
    from swingset.sources import get_page_kind

    f, clock = event, event.corpus.clock
    policy = prepared(f)
    watch = f.conn.execute(
        "SELECT watch_id,parser FROM watches WHERE url LIKE '%one.htm'"
    ).fetchone()
    client = FetchClient(
        f.conn,
        policy,
        clock,
        f.archive,
        transport=httpx.MockTransport(lambda _: httpx.Response(500, content=b"failed")),
        random_value=lambda: 0,
    )
    try:
        first = Observer(
            f.db, client, clock, run_id=f.corpus.run, deadline=clock.now() + timedelta(seconds=100)
        )
        with observing(first):
            clock.sleep(12)
            client.fetch(
                watch["watch_id"],
                get_page_kind(watch["parser"]),
                f.corpus.run,
                deadline=clock.now() + timedelta(seconds=30),
            )
        old = dict(
            f.conn.execute("SELECT * FROM event_timing WHERE source_ref='eepro:test'").fetchone()
        )
        assert old["since_progress_seconds"] == 12
        clock.sleep(1000)
        second = Observer(
            f.db, client, clock, run_id=f.corpus.run, deadline=clock.now() + timedelta(seconds=100)
        )
        with observing(second):
            rec = next(s["recorder"] for s in second.subjects if s["source_ref"] == "eepro:test")
            assert rec.row["episode_id"] == old["episode_id"]
            assert rec.row["since_progress_seconds"] == 12
            clock.sleep(12)
        row = f.conn.execute("SELECT * FROM event_timing WHERE source_ref='eepro:test'").fetchone()
        assert row["since_progress_seconds"] == 24
        assert row["last_progress_at"] is None
    finally:
        client.close()


def test_pending_success_qualification_keeps_issued_service_alarm_independent(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        rec = recorder(db, clock)
        rec.open(boundary(clock), eligible(clock), unresolved_success=True)
        clock.sleep(12)
        rec.close(boundary(clock))
        result = alarms(rec.row, now=clock.now(), operator_hold=False)
        assert result["service_gap"]["state"] == "alert"
        assert result["no_successful_progress"]["state"] == "unknown"


def test_pause_between_gate_assessment_and_open_boundary_cannot_credit_paused_time(
    event, monkeypatch
):
    from swingset.schedule import event_timing_observer as observing_module
    from swingset.state.controls import Selector, change_control

    f, clock = event, event.corpus.clock
    policy = prepared(f)
    original = observing_module.ordinary
    changed = False

    def pause_after_assessment(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            change_control(
                f.db.state_dir,
                selector=Selector("source", "eepro"),
                paused=True,
                actor="test",
                reason="assessment race",
                now=clock.now(),
                sources=("eepro",),
            )
        return result

    monkeypatch.setattr(observing_module, "ordinary", pause_after_assessment)
    client = FetchClient(f.conn, policy, clock, f.archive)
    try:
        observer = Observer(
            f.db, client, clock, run_id=f.corpus.run, deadline=clock.now() + timedelta(seconds=100)
        )
        with observing(observer):
            rec = next(s["recorder"] for s in observer.subjects if s["source_ref"] == "eepro:test")
            assert rec.row["state"] == "unknown"
            assert rec.row["reason"] == "gate_proof_changed_while_assessing"
            clock.sleep(12)
        assert rec.row["eligible_seconds"] == 0
        assert rec.row["unknown_seconds"] == 12
    finally:
        client.close()


def test_advancing_clock_cycle_emits_lower_bound_breach_despite_bookkeeping_gaps(
    event, tmp_path, monkeypatch
):
    import shutil

    from swingset.schedule import cycle

    class AdvancingClock(FakeClock):
        def now(self):
            # Real work between checkpoints takes time even without sleep().
            self.current += timedelta(milliseconds=1)
            self.elapsed += 0.001
            return self.current

    f = event
    clock = AdvancingClock(f.corpus.clock.now())
    f.corpus.clock = clock
    config_dir, overrides_dir = tmp_path / "config", tmp_path / "overrides"
    shutil.copytree("config", config_dir)
    shutil.copytree("overrides", overrides_dir)
    (config_dir / "sources.toml").write_text(
        "[sources.eepro]\nenabled=true\nindex_urls=[]\n"
        "[scheduling]\nevent_acquisition_service_alarm_seconds=0.0001\n"
        "event_acquisition_progress_alarm_seconds=0.0001\n"
    )
    (overrides_dir / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    (overrides_dir / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    transport = httpx.MockTransport(lambda _: httpx.Response(404))
    cycle.run_cycle(
        f.db,
        config_dir=config_dir,
        overrides_dir=overrides_dir,
        clock=clock,
        budget=0,
        transport=transport,
    )
    prepared(f)
    observed = []
    select = cycle.next_watch

    def inspect_then_select(*args, **kwargs):
        for row in f.conn.execute("SELECT * FROM event_timing WHERE source_ref='eepro:test'"):
            observed.append((dict(row), alarms(dict(row), now=clock.now(), operator_hold=False)))
        return select(*args, **kwargs)

    monkeypatch.setattr(cycle, "next_watch", inspect_then_select)
    result = cycle.run_cycle(
        f.db,
        config_dir=config_dir,
        overrides_dir=overrides_dir,
        clock=clock,
        budget=30,
        transport=transport,
    )
    assert not result["failed"]
    breaches = [
        (row, alarm)
        for row, alarm in observed
        if alarm["no_successful_progress"]["assessment"] == "lower_bound_exceeded"
    ]
    assert breaches
    row, alarm = breaches[0]
    assert row["unknown_seconds"] > 0 and not row["complete_coverage"]
    assert alarm["no_successful_progress"]["state"] == "alert"
    assert alarm["service_gap"]["assessment"] == "lower_bound_exceeded"
    assert row["last_progress_at"] is None


def test_changed_dependency_keeps_age_but_withholds_lower_bound_alarm_until_receipt(tmp_path):
    clock = FakeClock()
    with open_database(tmp_path) as db:
        rec = recorder(db, clock)
        rec.open(boundary(clock), eligible(clock))
        clock.sleep(12)
        rec.close(boundary(clock), inactive=True)
        new = Recorder(
            db,
            source="eepro",
            source_ref="event",
            run_id="cycle",
            token="new",
            policy={"service_gap": 10, "no_successful_progress": 10},
            at=boundary(clock, token="new"),
        )
        new.open(boundary(clock, token="new"), eligible(clock))
        assert new.row["since_progress_seconds"] == 12
        assert (
            alarms(new.row, now=clock.now(), operator_hold=False)["no_successful_progress"]["state"]
            == "unknown"
        )
