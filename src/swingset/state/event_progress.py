"""Atomic successful-output facts, independent of later event verification."""

from __future__ import annotations

import sqlite3


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='event_stage_operations'").fetchone()
        is not None
    )


def acquired(conn: sqlite3.Connection, *, snapshot_id: str, source: str, parser: str) -> None:
    """Called only alongside a newly retained successful fetch snapshot."""
    from swingset.schedule.event_evidence import request, request_id
    from swingset.schedule.event_request_kind import purpose

    if purpose(parser) not in {"result", "event_index"} or not available(conn):
        return
    if not conn.in_transaction:
        raise ValueError("stage operation must share its output transaction")
    row = conn.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    if row is None or row["classification"] not in {"Ok", "NotModified"}:
        raise ValueError("acquisition requires a successful retained snapshot")
    identity = request_id(request(source, row["method"], row["url"], row["form"]))
    conn.execute(
        "INSERT INTO event_stage_operations(stage,source,request_id,watch_id,snapshot_id,occurred_at,run_id) "
        "VALUES('acquired',?,?,?,?,?,?) ON CONFLICT(snapshot_id) DO NOTHING",
        (source, identity, row["watch_id"], snapshot_id, row["fetched_at"], row["run_id"]),
    )


def interpreted(
    conn: sqlite3.Connection,
    *,
    source: str,
    parser: str,
    watch_id: str,
    generation_id: str,
    decision_id: int,
    occurred_at: str,
    run_id: str,
) -> None:
    """Called after the accepted decision, before its transaction commits."""
    from swingset.schedule.event_request_kind import purpose

    if purpose(parser) not in {"result", "event_index"} or not available(conn):
        return
    if not conn.in_transaction:
        raise ValueError("stage operation must share its output transaction")
    decision = conn.execute(
        "SELECT 1 FROM admission_decisions WHERE decision_id=? AND generation_id=? AND state='accepted'",
        (decision_id, generation_id),
    ).fetchone()
    if decision is None:
        raise ValueError("interpretation requires its accepted decision")
    conn.execute(
        "INSERT INTO event_stage_operations(stage,source,watch_id,generation_id,decision_id,occurred_at,run_id) "
        "VALUES('interpreted',?,?,?,?,?,?) ON CONFLICT(generation_id) DO NOTHING",
        (source, watch_id, generation_id, decision_id, occurred_at, run_id),
    )
