"""Event service explains issued receipts, never successful page progress."""

import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_event_capacity import capacity as capacity
from test_event_capacity import page
from test_event_enumerations import admit_parent, finish_bootstrap
from test_event_enumerations import event as event
from test_event_turns import debit, select
from test_event_turns import f as f

from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.classify import Classification, Outcome
from swingset.fetch.controls import issue, release
from swingset.fetch.politeness import Gate, Grant
from swingset.schedule import event_report
from swingset.schedule.event_service import report
from swingset.schedule.fairness import next_watch, prepare_event_turns, servicing
from swingset.state import db as state_db


def charge(f, choice, actual_host, *, outcome=Outcome.OK, body_bytes=7):
    gate = Gate(f.db.connection, f.config, f.clock)
    with servicing(choice, run_id=f.run):
        grant, action = issue(
            f.db,
            gate,
            f.clock,
            host=actual_host,
            source="wsdc_calendar",
            watch=SimpleNamespace(watch_id=choice.key, kind="round"),
            page_kind="scoringdance.round",
            crawl_delay=0,
            sweep=False,
        )
    assert isinstance(grant, Grant)
    release(
        f.db,
        gate,
        f.clock,
        action,
        actual_host,
        Classification(outcome),
        body_bytes=body_bytes,
        request_day=f.clock.now().date().isoformat(),
    )
    f.clock.sleep(5)
    return action


def test_mixed_hosts_policies_borrowing_and_failure_keep_exact_owner_counts(capacity):
    f = capacity
    result = page(f, "result", listed=True)
    index = page(f, "index", listed=False)
    f.members[result.watch_id].append(
        {"source": "wsdc_calendar", "source_ref": "shared", "enumeration_id": None}
    )
    f.config = replace(f.config, scheduler=replace(f.config.scheduler, event_turn_requests=1))
    first = select(f)
    assert first.capacity.competing and first.turn.source_ref == "result"
    charge(f, first, "example.test")
    charge(f, first, "other.test", outcome=Outcome.SERVER_ERROR)
    f.config = replace(
        f.config,
        scheduler=replace(f.config.scheduler, event_turn_requests=3, listed_page_percent=75),
    )
    next_choice = select(f, exclude=[index.watch_id])
    # The shared owner is still waiting. Select only the original owner for this
    # policy-change scenario, without fabricating any issued receipt.
    if next_choice.turn.source_ref != "result":
        f.members[result.watch_id] = [
            m for m in f.members[result.watch_id] if m["source_ref"] == "result"
        ]
        next_choice = select(f, exclude=[index.watch_id])
    assert not next_choice.capacity.competing
    final_action = charge(f, next_choice, "example.test")
    conn = f.db.connection
    snapshot = list(conn.iterdump())
    conn.execute("PRAGMA query_only=ON")
    try:
        result = report(conn, source="wsdc_calendar", source_ref="result", limit=2)
        shared = report(conn, source="wsdc_calendar", source_ref="shared")
    finally:
        conn.execute("PRAGMA query_only=OFF")
    assert list(conn.iterdump()) == snapshot
    assert result["total_recorded_requests"] == 3 and result["details_truncated"]
    assert {row["actual_host"]: row["issued_requests"] for row in result["actual_hosts"]} == {
        "example.test": 2,
        "other.test": 1,
    }
    assert sum(row["recorded_body_bytes"] for row in result["actual_hosts"]) == 21
    assert {row["reason"]: row["issued_requests"] for row in result["capacity_totals"]} == {
        "competing": 2,
        "borrowed": 1,
    }
    assert {row["purpose"] for row in result["capacity_totals"]} == {"listed_result"}
    assert result["capacity_recorded_requests"] == 3 and result["capacity_unrecorded_requests"] == 0
    assert len(result["recent_requests"]) == 2
    latest, failed = result["recent_requests"]
    assert latest["action_id"] == final_action and result["last_issued_at"] == latest["issued_at"]
    assert failed["recorded_outcome"] == "ServerError" and failed["actual_host"] == "other.test"
    assert latest["selected_host"] == failed["selected_host"] == "example.test"
    assert latest["capacity"]["reason"] == "borrowed"
    assert latest["capacity"]["credit_before"] == latest["capacity"]["credit_after"]
    assert len(result["turn_policies"]) == len(result["capacity_policies"]) == 2
    assert {
        p["policy"]["scheduler"]["event_turn_requests"] for p in result["turn_policies"].values()
    } == {1, 3}
    assert {p["policy"]["listed_page_percent"] for p in result["capacity_policies"].values()} == {
        50,
        75,
    }
    assert all(p["status"] == "verified_recorded" for p in result["turn_policies"].values())
    assert (
        result["current_turns"][0]["used"] == 1
        and result["current_turns"][0]["target_requests"] == 3
    )
    assert shared["total_recorded_requests"] == 0  # Shared evidence is not shared debit ownership.
    assert (
        result["successful_progress_at"] is None and result["eligible_service_age_seconds"] is None
    )
    assert result["current_eligibility"] is None and result["history_before_receipts"] == "unknown"


def test_detail_limit_does_not_truncate_aggregate_service(capacity):
    f = capacity
    page(f, "only", listed=True)
    for _ in range(25):
        debit(f, select(f))
    result = report(f.db.connection, source="wsdc_calendar", source_ref="only", limit=3)
    assert len(result["recent_requests"]) == 3 and result["total_recorded_requests"] == 25
    assert result["actual_hosts"][0]["issued_requests"] == 25
    assert result["capacity_totals"][0]["issued_requests"] == 25
    assert result["details_truncated"]


def test_populated_old_turn_schema_keeps_capacity_unknown(tmp_path, monkeypatch):
    from test_event_turns import add

    from swingset.clock import FakeClock
    from swingset.schedule.fairness import record_request
    from swingset.state.controls import admission, settle

    with monkeypatch.context() as legacy:
        legacy.setattr(state_db, "SCHEMA_VERSION", 17)
        with state_db.open_database(tmp_path) as database:
            clock = FakeClock()
            # Use the real legacy fallback owner; explicit event membership is
            # supplied through the same read-only seam as scheduler selection.
            from swingset.schedule import event_enumerations

            members = {}
            legacy.setattr(
                event_enumerations,
                "memberships",
                lambda conn, keys: {k: members[k] for k in keys if k in members},
            )
            f = SimpleNamespace(
                db=database,
                clock=clock,
                members=members,
                config=Config(
                    {"example.test": HostConfig()}, {"wsdc_calendar": SourceConfig(True)}
                ),
                run=database.start_run(clock.now()),
            )
            add(f, "legacy")
            choice = select(f)
            # Populate a retained schema17 receipt through its accounting seam.
            # Current fetching correctly refuses that unsupported spacing schema;
            # this test concerns read-only interpretation of historical receipts.
            with (
                servicing(choice, run_id=f.run),
                admission(
                    database,
                    action_id="retained-schema17-request",
                    action_kind="request",
                    scope=choice.scope,
                    now=clock.now(),
                ),
            ):
                conn = database.connection
                conn.execute("INSERT OR IGNORE INTO hosts(host) VALUES (?)", (choice.host,))
                conn.execute(
                    "INSERT INTO host_budget VALUES (?,?,1,0)",
                    (choice.host, clock.now().date().isoformat()),
                )
                record_request(
                    conn,
                    f.config,
                    action_id="retained-schema17-request",
                    host=choice.host,
                    watch_id=choice.key,
                    source="wsdc_calendar",
                    now=clock.now(),
                )
                settle(conn, "retained-schema17-request", now=clock.now(), outcome="Ok")
    conn = sqlite3.connect((tmp_path / "state.sqlite").as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        before = list(conn.iterdump())
        value = report(conn, source="wsdc_calendar", source_ref="legacy")
        assert value["supported"] and not value["capacity_supported"]
        assert value["total_recorded_requests"] == value["capacity_unrecorded_requests"] == 1
        assert value["recent_requests"][0]["capacity"] is None
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 17
        assert list(conn.iterdump()) == before
    finally:
        conn.close()


def test_pre_turn_schema_is_unavailable_and_new_empty_history_is_not_success(tmp_path):
    with sqlite3.connect(":memory:") as old:
        result = report(old, source="eepro", source_ref="eepro:test")
        assert not result["supported"] and result["total_recorded_requests"] is None
        assert result["reason"] == "event_service_schema_unavailable"
        assert not old.execute("SELECT name FROM sqlite_master").fetchall()
    with state_db.open_database(tmp_path) as database:
        result = report(database.connection, source="eepro", source_ref="eepro:test")
        assert result["supported"] and result["total_recorded_requests"] == 0
        assert result["reason"] == "no_recorded_attempts" and result["last_issued_at"] is None
        assert result["successful_progress_at"] is None


def test_only_explicit_event_drilldown_reads_service_and_preserves_stage_unknowns(
    event, monkeypatch
):
    f = event
    admit_parent(f, ["one.htm"])
    finish_bootstrap(f)
    clock, run = f.corpus.clock, f.corpus.run
    config = Config({"eepro.com": HostConfig()}, {"eepro": SourceConfig(True)})
    children = {
        row[0] for row in f.conn.execute("SELECT watch_id FROM watches WHERE parser='eepro.round'")
    }
    excluded = {row[0] for row in f.conn.execute("SELECT watch_id FROM watches")} - children
    with f.db.transaction() as conn:
        prepare_event_turns(conn, config, now=clock.now(), exclude=excluded, run_id=run)
    choice = next_watch(f.conn, config, now=clock.now(), exclude=excluded, run_id=run)
    gate = Gate(f.conn, config, clock)
    with servicing(choice, run_id=run):
        grant, action = issue(
            f.db,
            gate,
            clock,
            host=choice.host,
            source="eepro",
            watch=SimpleNamespace(watch_id=choice.key, kind="round"),
            page_kind="eepro.round",
            crawl_delay=0,
            sweep=False,
        )
    assert isinstance(grant, Grant)
    # A debit whose outcome is still active is attempted service, not acquisition.
    with f.db.transaction(immediate=False):
        detail = event_report.report(
            f.conn, f.archive, source="eepro", source_ref="eepro:test", now=clock.now()
        )["detail"]
    assert detail["service_history"]["total_recorded_requests"] == 1
    assert detail["service_history"]["recent_requests"][0]["execution_state"] == "active"
    assert detail["service_history"]["recent_requests"][0]["recorded_outcome"] is None
    assert detail["acquired_pages"] == 0 and detail["last_successful_progress_at"] is None
    assert detail["service_history"]["successful_progress_at"] is None

    def forbidden(*args, **kwargs):
        raise AssertionError("catalog must not scan event receipt history")

    monkeypatch.setattr(event_report, "service_report", forbidden)
    assert event_report.report(f.conn, Archive(f.db.state_dir), now=clock.now())["detail"] is None


@pytest.mark.parametrize("limit", [0, 101, -1, True, 1.5])
def test_detail_limit_is_bounded(limit):
    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(ValueError, match="history limit"):
            report(conn, source="eepro", source_ref="eepro:test", limit=limit)


@pytest.mark.parametrize(
    "table,key",
    [
        ("scheduler_event_policies", "turn_policies"),
        ("scheduler_capacity_policies", "capacity_policies"),
    ],
)
def test_blob_policy_corruption_is_reported_without_crashing(capacity, table, key):
    f = capacity
    page(f, "only", listed=True)
    debit(f, select(f))
    conn = f.db.connection
    policy_id, raw = conn.execute(f"SELECT digest,policy_json FROM {table}").fetchone()
    conn.execute(f"UPDATE {table} SET policy_json=? WHERE digest=?", (raw.encode(), policy_id))
    snapshot = list(conn.iterdump())
    result = report(conn, source="wsdc_calendar", source_ref="only")
    assert result[key][policy_id] == {"status": "invalid", "policy": None}
    assert result["total_recorded_requests"] == 1
    assert list(conn.iterdump()) == snapshot
