"""Guard reason rows (migration 34): one row per subject and reason."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.schedule.cycle import run_cycle
from swingset.state import guards
from swingset.state.db import open_database

AT = "2026-09-25T00:00:00Z"
LATER = "2026-09-25T01:00:00Z"


def test_guard_rows_round_trip_and_count_by_reason(tmp_path: Path) -> None:
    budget = guards.GuardRow("host:a.test", "request budget", "acquire", AT, AT, LATER)
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            guards.upsert(conn, budget)
            guards.upsert(
                conn, guards.GuardRow("host:b.test", "request budget", "acquire", AT, AT, LATER)
            )
            guards.upsert(
                conn, guards.GuardRow("host:a.test", "Throttled", "acquire", AT, AT, LATER)
            )
            assert guards.get(conn, "host:a.test", "request budget") == budget
            seen = guards.GuardRow(
                "host:a.test", "request budget", "acquire", AT, LATER, LATER, seen=3
            )
            guards.upsert(conn, seen)
            assert guards.get(conn, "host:a.test", "request budget") == seen
            assert guards.counts_by_reason(conn) == {"Throttled": 1, "request budget": 2}
            assert guards.delete(conn, "host:a.test", "Throttled") == 1
            assert guards.delete(conn, "host:b.test") == 1
            assert guards.delete(conn, "host:b.test") == 0
            assert guards.counts_by_reason(conn) == {"request budget": 1}


def test_a_guard_needs_its_expiry(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            with database.transaction() as conn:
                conn.execute(
                    "INSERT INTO guard_reasons(subject,reason,loop,since,last_seen_at) "
                    "VALUES ('loop:derive','publication_in_flight','derive',?,?)",
                    (AT, AT),
                )


# D-0219: which skips are guards, their expiries, and one pass's flush.
SINCE = datetime(2026, 9, 25, 20, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "reason, subject, expires",
    [
        ("legacy request spacing unknown", "host:a.test", "2026-09-25T21:30:00"),
        ("request spacing recovery required", "host:a.test", "2026-09-25T21:30:00"),
        ("request spacing schema unavailable", "host:a.test", "2026-09-25T21:30:00"),
        ("request budget", "host:a.test", "2026-09-26T01:00:00"),
        ("byte budget", "host:a.test", "2026-09-26T01:00:00"),
        ("operator", "host:a.test", "2026-09-27T20:30:00"),
        ("pending work backpressure", "loop:cycle", "2026-09-26T08:30:00"),
        ("event expansion pressure", "host:a.test", "2026-09-26T20:30:00"),
        ("event pressure metadata pending", "host:a.test", "2026-09-26T20:30:00"),
        ("publication_in_flight", "loop:cycle", "2026-09-25T21:30:00"),
        ("history_year_unaccepted", "watch:w1", "2026-10-25T20:30:00"),
        ("origin_dispatch_required", "watch:w1", "2026-10-25T20:30:00"),
        ("robots disallow", "watch:w1", "2026-10-25T20:30:00"),
        ("source disabled", "watch:w1", "2026-10-25T20:30:00"),
    ],
)
def test_each_guard_reason_has_its_subject_and_expiry(
    tmp_path: Path, reason: str, subject: str, expires: str
) -> None:
    rule = guards.rule(reason)
    assert rule is not None
    assert guards.subject(rule.subject, host="a.test", watch_id="w1", loop="cycle") == subject
    with open_database(tmp_path / "state", lock=False) as database:
        at = guards.expires_at(database.connection, subject, reason, SINCE, now=SINCE)
    assert at == datetime.fromisoformat(expires + "+00:00")


def test_a_host_pause_expires_an_hour_after_it_ends(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        # No pause row: an hour from since.
        assert guards.expires_at(conn, "host:a.test", "Throttled", SINCE, now=SINCE) == (
            SINCE + timedelta(hours=1)
        )
        with database.transaction():
            conn.execute(
                "INSERT INTO hosts(host,paused_until,pause_reason) VALUES "
                "('a.test','2026-09-26T03:00:00+00:00','Blocked')"
            )
        for reason in ("Throttled", "ServerError", "Blocked"):
            assert guards.rule(reason) == guards.Rule("host")
            assert guards.expires_at(conn, "host:a.test", reason, SINCE, now=SINCE) == datetime(
                2026, 9, 26, 4, tzinfo=UTC
            )


def test_an_operator_guard_expires_with_its_pause(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            conn.execute(
                "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason) VALUES "
                "('host','a.test','2026-09-29T00:00:00+00:00','maintenance')"
            )
        end = datetime(2026, 9, 29, tzinfo=UTC)
        assert guards.expires_at(conn, "host:a.test", "operator", SINCE, now=SINCE) == end
        # A pause the caller saw: its end wins; an open one gives 48 hours.
        assert guards.expires_at(
            conn, "host:b.test", "operator", SINCE, now=SINCE, until="2026-09-26T00:00:00Z"
        ) == datetime(2026, 9, 26, tzinfo=UTC)
        with database.transaction():
            conn.execute(
                "INSERT INTO operator_pauses(scope_kind,scope_id,until_at,reason) VALUES "
                "('all','all',NULL,'hold')"
            )
        assert guards.expires_at(conn, "host:a.test", "operator", SINCE, now=SINCE) == (
            SINCE + timedelta(hours=48)
        )


@pytest.mark.parametrize(
    "reason",
    [
        "budget",
        "budget or stop",
        "sealed",
        "archive required",
        "capture already archived",
        "request deadline",
        "invalid redirect URL",
    ],
)
def test_waits_deadlines_and_terminal_states_are_not_guards(reason: str) -> None:
    assert guards.rule(reason) is None


def test_a_history_gate_reason_is_a_watch_guard_whatever_its_text() -> None:
    assert guards.rule("not_yet_due") is None
    assert guards.rule("not_yet_due", history=True) == guards.Rule("watch", daily=True)


def test_a_flush_keeps_since_and_moves_the_rest(tmp_path: Path) -> None:
    later = SINCE + timedelta(minutes=15)
    key = ("host:a.test", "request budget")
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            assert guards.flush(conn, loop="cycle", seen={key: 2}, requested=(), now=SINCE) == {
                "recorded": 1,
                "cleared": 0,
            }
        with database.transaction():
            guards.flush(conn, loop="cycle", seen={key: 3}, requested=(), now=later)
        row = guards.get(conn, *key)
        assert row == guards.GuardRow(
            "host:a.test",
            "request budget",
            "cycle",
            "2026-09-25T20:30:00.000000Z",
            "2026-09-25T20:45:00.000000Z",
            "2026-09-26T01:00:00.000000Z",
            5,
        )


def test_a_request_clears_the_subjects_rows(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            guards.flush(
                conn,
                loop="cycle",
                seen={
                    ("host:a.test", "request budget"): 1,
                    ("host:a.test", "Throttled"): 1,
                    ("host:b.test", "Throttled"): 1,
                },
                requested=(),
                now=SINCE,
            )
        with database.transaction():
            # A skip met in the same pass as a request does not hold the host.
            assert guards.flush(
                conn,
                loop="cycle",
                seen={("host:a.test", "request budget"): 1},
                requested={"host:a.test"},
                now=SINCE + timedelta(minutes=5),
            ) == {"recorded": 0, "cleared": 2}
        assert guards.counts_by_reason(conn) == {"Throttled": 1}
        assert guards.get(conn, "host:b.test", "Throttled") is not None


def test_a_request_that_raised_keeps_its_clock_through_the_flush(tmp_path: Path) -> None:
    """A request that went out and then raised (its response write, say) held
    its watch; the pass's flush, which clears the watch as requested, keeps
    that row. Only a served request deletes it."""
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            held = guards.request_failed(conn, "w1", loop="acquire", now=SINCE)
            guards.upsert(
                conn,
                guards.GuardRow(
                    "watch:w1",
                    "robots disallow",
                    "acquire",
                    held.since,
                    held.since,
                    held.expires_at,
                    1,
                ),
            )
        with database.transaction():
            assert guards.flush(
                conn, loop="acquire", seen={}, requested={"watch:w1"}, now=SINCE
            ) == {"recorded": 0, "cleared": 1}
        assert guards.get(conn, "watch:w1", guards.REQUEST_ERROR) == held
        assert guards.requests_errored(conn, now=SINCE) == {"w1": True}
        assert guards.requests_errored(conn, now=SINCE + timedelta(hours=2)) == {"w1": False}


def test_a_watch_guard_is_written_once_a_day(tmp_path: Path) -> None:
    key = ("watch:w1", "history_year_unaccepted")
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        for minutes in (0, 60, 180, 24 * 60):
            with database.transaction():
                guards.flush(
                    conn,
                    loop="cycle",
                    seen={key: 4},
                    requested=(),
                    now=SINCE + timedelta(minutes=minutes),
                )
        row = guards.get(conn, *key)
        assert row is not None
        assert (row.since, row.last_seen_at, row.seen) == (
            "2026-09-25T20:30:00.000000Z",
            "2026-09-26T20:30:00.000000Z",
            2,
        )


def test_the_report_names_rows_seen_past_their_expiry(tmp_path: Path) -> None:
    with open_database(tmp_path / "state", lock=False) as database:
        conn = database.connection
        with database.transaction():
            for minutes in (0, 90):
                guards.flush(
                    conn,
                    loop="cycle",
                    seen={
                        ("host:a.test", "legacy request spacing unknown"): 1,
                        ("host:b.test", "request budget"): 1,
                    },
                    requested=(),
                    now=SINCE + timedelta(minutes=minutes),
                )
        report = guards.report(conn, now=SINCE)
        assert report["by_reason"] == {"legacy request spacing unknown": 1, "request budget": 1}
        assert report["expired"] == [
            {
                "subject": "host:a.test",
                "reason": "legacy request spacing unknown",
                "since": "2026-09-25T20:30:00.000000Z",
                "expires_at": "2026-09-25T21:30:00.000000Z",
            }
        ]


def test_the_cycle_records_and_clears_guards(tmp_path: Path) -> None:
    """A host whose spacing is unknown is a guard after the pass; a request clears it.

    The scheduler already leaves out hosts that are paused, over budget or
    under an operator pause, so the gate's spacing reasons are what reach
    the client in a cycle.
    """
    config = tmp_path / "config"
    shutil.copytree("config", config)
    (config / "sources.toml").write_text(
        '[sources.wsdc_calendar]\nenabled = true\nindex_urls = ["https://worldsdc.com/events/"]\n'
    )
    overrides = tmp_path / "overrides"
    shutil.copytree("overrides", overrides)
    (overrides / "source_urls.csv").write_text("event_id,source,kind,url,parser,notes\n")
    (overrides / "event_aliases.csv").write_text("source,source_ref,event_id,note\n")
    clock = FakeClock()
    transport = httpx.MockTransport(
        lambda request: (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=b"<html></html>")
        )
    )
    with open_database(tmp_path / "state") as database:
        with database.transaction() as conn:
            # Budget spent on an earlier day with no spacing row (D-0190).
            conn.execute("INSERT INTO host_budget VALUES ('worldsdc.com','2025-12-31',1,0)")
        held = run_cycle(
            database, config_dir=config, overrides_dir=overrides, clock=clock, transport=transport
        )
        assert held["checked"] == 0
        assert held["guards"]["recorded"] == 1
        assert held["guard_reasons"] == {"legacy request spacing unknown": 1}
        row = guards.get(database.connection, "host:worldsdc.com", "legacy request spacing unknown")
        assert row is not None and row.loop == "cycle"
        assert row.expires_at == guards.at(clock.now() + timedelta(hours=1))
        with database.transaction() as conn:
            conn.execute("DELETE FROM host_budget WHERE day='2025-12-31'")
        clock.sleep(60)
        cleared = run_cycle(
            database, config_dir=config, overrides_dir=overrides, clock=clock, transport=transport
        )
        assert cleared["checked"] == 1
        assert cleared["guards"]["cleared"] == 1
        assert guards.counts_by_reason(database.connection) == {}
