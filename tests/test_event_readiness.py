"""Event blocker reporting reads state without admitting or resetting work."""

import sqlite3
from datetime import UTC, timedelta, timezone

import pytest
from test_event_enumerations import event as event

from swingset.config import Config, HostConfig, SourceConfig
from swingset.schedule.event_readiness import report
from swingset.schedule.fair_policy import SchedulerConfig
from swingset.state import db as state_db
from swingset.state.controls import Selector, change_control


def inspect(f, config, *, now=None, operator_hold=False, watch_ids=None):
    return report(
        f.conn,
        config,
        source="eepro",
        source_ref="eepro:test",
        watch_ids=[f.corpus.spec.watch_id] if watch_ids is None else watch_ids,
        now=now or f.corpus.clock.now(),
        operator_hold=operator_hold,
    )


def pause(f, kind, identifier, *, until=None):
    return change_control(
        f.db.state_dir,
        selector=Selector(kind, identifier),
        paused=True,
        actor="test:operator",
        reason="readiness fixture",
        now=f.corpus.clock.now(),
        until=until,
        hosts=("web.archive.org", "eepro.com"),
    )["pause_id"]


def test_overlapping_recorded_blockers_use_archive_host_without_mutation(event):
    f = event
    snapshot = f.corpus.snapshot("failed-result")
    now = f.corpus.clock.now()
    future = (now + timedelta(hours=1)).isoformat()
    archive_url = "https://web.archive.org/web/20250101000000/https://eepro.com/final.htm"
    f.conn.execute(
        "UPDATE watches SET archive_url=?,state='archived',next_check_at=?,paused_until=?,"
        "last_checked_at=? WHERE watch_id=?",
        (archive_url, future, future, now.isoformat(), f.corpus.spec.watch_id),
    )
    f.conn.execute(
        "UPDATE snapshots SET classification='ServerError',http_status=503 WHERE snapshot_id=?",
        (snapshot.snapshot_id,),
    )
    f.conn.execute(
        "INSERT INTO hosts(host,next_allowed_at,paused_until,pause_reason) VALUES (?,?,?,'Blocked')",
        ("web.archive.org", future, future),
    )
    f.conn.execute(
        "INSERT INTO host_budget VALUES ('web.archive.org',?,2,100)", (now.date().isoformat(),)
    )
    expected_pauses = {
        pause(f, "all", "all"),
        pause(f, "source", "eepro"),
        pause(f, "host", "web.archive.org"),
        pause(f, "kind", "round_observations"),
    }
    origin_pause = pause(f, "host", "eepro.com")
    config = Config(
        {"web.archive.org": HostConfig(daily_request_budget=2, daily_byte_budget=100)},
        {"eepro": SourceConfig(False)},
        scheduler=SchedulerConfig(
            pending_parse_items=1, pending_parse_bytes=1, pending_work_items=1
        ),
    )
    before = list(f.conn.iterdump())
    changes = f.conn.total_changes
    f.conn.execute("PRAGMA query_only=ON")
    f.conn.execute("BEGIN")
    try:
        result = inspect(f, config, operator_hold=True)
        assert result["supported"] and result["incomplete_gate_assessment"]
        assert result["operator_hold"] and not result["source_enabled"]
        assert {p["pause_id"] for p in result["operator_pauses"]} == expected_pauses
        assert origin_pause not in {p["pause_id"] for p in result["operator_pauses"]}
        watch = result["watches"][0]
        assert watch["state"] == "archived" and not watch["state_excludes_ordinary_selection"]
        assert watch["next_check_at"] == future and watch["due"] is False
        assert watch["pause_active"] and watch["paused_until"] == future
        assert watch["latest_response"]["classification"] == "ServerError"
        assert watch["latest_response"]["http_status"] == 503
        assert watch["request_host"] == "web.archive.org"
        assert watch["request_url"] == archive_url and watch["request_host_basis"] == "archive_url"
        assert set(watch["operator_pause_ids"]) == expected_pauses
        host = result["hosts"][0]
        assert host["host"] == "web.archive.org" and host["cooldown_active"]
        assert host["pause_active"] and host["pause_reason"] == "Blocked"
        allowance = host["daily_allowance"]
        assert allowance["requests_used"] == 2 and allowance["requests_remaining"] == 0
        assert allowance["bytes_used"] == 100 and allowance["bytes_remaining"] == 0
        assert allowance["requests_exhausted"] and allowance["bytes_exhausted"]
        assert allowance["next_reset_at"] == (
            (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        )
        assert set(result["backpressure"]["reasons"]) == {
            "pending_parse_bytes",
            "pending_parse_items",
            "pending_work_items",
        }
        assert "eligible" not in result and "allowed" not in watch
        assert list(f.conn.iterdump()) == before
        assert f.conn.total_changes == changes
    finally:
        f.conn.rollback()


def test_caller_snapshot_stays_consistent_across_external_budget_change(event):
    f = event
    config = Config(
        {"eepro.com": HostConfig(daily_request_budget=2)}, {"eepro": SourceConfig(True)}
    )
    f.conn.execute("PRAGMA query_only=ON")
    f.conn.execute("BEGIN")
    try:
        first = inspect(f, config)
        assert first["watches"][0]["due"] is True
        assert not first["hosts"][0]["host_recorded"]
        with sqlite3.connect(f.db.state_dir / "state.sqlite") as writer:
            writer.execute(
                "INSERT INTO host_budget VALUES ('eepro.com',?,2,0)",
                (f.corpus.clock.now().date().isoformat(),),
            )
        assert inspect(f, config) == first
    finally:
        f.conn.rollback()
    changed = inspect(f, config)
    assert changed["hosts"][0]["daily_allowance"]["requests_exhausted"]
    assert changed["incomplete_gate_assessment"]
    assert not f.conn.execute("SELECT 1 FROM hosts").fetchone()


def test_latest_response_orders_instants_across_timezone_offsets(event):
    f = event
    f.corpus.snapshot("older-offset-response")
    f.corpus.snapshot("newer-utc-response")
    f.conn.execute(
        "UPDATE snapshots SET fetched_at='2026-09-16T12:00:00+02:00',"
        "classification='ServerError',http_status=503 WHERE snapshot_id='older-offset-response'"
    )
    f.conn.execute(
        "UPDATE snapshots SET fetched_at='2026-09-16T11:00:00+00:00' "
        "WHERE snapshot_id='newer-utc-response'"
    )
    f.conn.execute("PRAGMA query_only=ON")
    result = inspect(f, Config({}, {"eepro": SourceConfig(True)}))
    assert result["watches"][0]["latest_response"]["snapshot_id"] == "newer-utc-response"
    assert result["watches"][0]["latest_response"]["classification"] == "Ok"


def test_expired_pauses_are_ignored_without_deleting_records_and_days_reset_in_utc(event):
    f = event
    expiry = f.corpus.clock.now() + timedelta(seconds=5)
    pause_id = pause(f, "source", "eepro", until=expiry)
    config = Config({}, {"eepro": SourceConfig(True)})
    # At exact expiry, the existing control matcher stops applying the pause.
    local_now = expiry.astimezone(timezone(timedelta(hours=-7)))
    f.conn.execute("PRAGMA query_only=ON")
    result = inspect(f, config, now=local_now)
    assert result["operator_pauses"] == []
    assert f.conn.execute("SELECT pause_id FROM operator_pauses").fetchone()[0] == pause_id
    allowance = result["hosts"][0]["daily_allowance"]
    assert allowance["day"] == expiry.astimezone(UTC).date().isoformat()
    assert allowance["byte_limit"] is None and allowance["bytes_remaining"] is None
    assert not allowance["bytes_exhausted"]
    assert allowance["next_reset_at"] == (
        (expiry + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    )


@pytest.mark.parametrize("state", ["sealed", "retired"])
def test_unscheduled_terminal_watch_and_missing_members_remain_visible(event, state):
    f = event
    f.conn.execute(
        "UPDATE watches SET state=?,next_check_at=NULL WHERE watch_id=?",
        (state, f.corpus.spec.watch_id),
    )
    f.conn.execute("PRAGMA query_only=ON")
    result = inspect(
        f, Config({}, {}), watch_ids=[f.corpus.spec.watch_id, "missing", f.corpus.spec.watch_id]
    )
    assert len(result["watches"]) == 1
    assert result["watches"][0]["state_excludes_ordinary_selection"]
    assert result["watches"][0]["due"] is None
    assert result["missing_watch_ids"] == ["missing"]
    assert not result["source_enabled"] and result["incomplete_gate_assessment"]


def test_hold_without_watches_does_not_imply_execution_permission(event):
    result = inspect(event, Config({}, {}), operator_hold=True, watch_ids=[])
    assert result["operator_hold"] and result["incomplete_gate_assessment"]
    assert result["watches"] == result["hosts"] == []
    assert result["backpressure"]["active"] is False


def test_legacy_schema_reports_unavailable_controls_without_migration(tmp_path, monkeypatch):
    monkeypatch.setattr(state_db, "SCHEMA_VERSION", 1)
    with state_db.open_database(tmp_path) as db:
        from test_admission import Corpus

        corpus = Corpus(db)
        conn = db.connection
        before = list(conn.iterdump())
        conn.execute("PRAGMA query_only=ON")
        result = report(
            conn,
            Config({}, {}),
            source="eepro",
            source_ref="eepro:test",
            watch_ids=[corpus.spec.watch_id],
            now=corpus.clock.now(),
            operator_hold=True,
        )
        assert result["supported"] and not result["operator_controls_supported"]
        assert result["incomplete_gate_assessment"] and result["operator_hold"]
        assert result["operator_pauses"] == []
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert list(conn.iterdump()) == before


def test_missing_schema_and_naive_time_are_explicit(event):
    with sqlite3.connect(":memory:") as conn:
        result = report(
            conn,
            Config({}, {}),
            source="eepro",
            source_ref="eepro:test",
            watch_ids=[],
            now=event.corpus.clock.now(),
            operator_hold=False,
        )
        assert not result["supported"] and result["reason"] == "readiness_schema_unavailable"
        assert result["backpressure"] is None
    with pytest.raises(ValueError, match="aware timestamp"):
        inspect(event, Config({}, {}), now=event.corpus.clock.now().replace(tzinfo=None))
