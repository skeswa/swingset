"""Atomic successful-output facts, independent of later event verification."""

from __future__ import annotations

from swingset.state import engine


def available(conn: engine.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 AS _value_0 FROM pg_catalog.pg_class WHERE relname = 'event_stage_operations' AND (PG_TABLE_IS_VISIBLE(oid) AND NOT relnamespace IN (CAST('pg_catalog' AS REGNAMESPACE), CAST('information_schema' AS REGNAMESPACE)))"
        ).fetchone()
        is not None
    )


def acquired(conn: engine.Connection, *, snapshot_id: str, source: str, parser: str) -> None:
    """Called only alongside a newly retained successful fetch snapshot."""
    from swingset.schedule.event_evidence import request, request_id
    from swingset.schedule.event_request_kind import purpose

    if purpose(parser) not in {"result", "event_index"} or not available(conn):
        return
    if not conn.in_transaction:
        raise ValueError("stage operation must share its output transaction")
    row = conn.execute(
        (
            "SELECT snapshots.snapshot_id, snapshots.watch_id, snapshots.method, snapshots.url, "
            "snapshots.form, snapshots.fetched_at, snapshots.http_status, snapshots.etag, "
            "snapshots.last_modified, snapshots.content_type, snapshots.body_sha256, "
            "snapshots.body_bytes, snapshots.content_changed, snapshots.run_id, snapshots.via, "
            "snapshots.headers_json, snapshots.classification, snapshots.extract_status, "
            "snapshots.extract_sha256, snapshots.parse_status, snapshots.parsed_at, "
            "snapshots.extract_version, snapshots.parser_version, snapshots.captured_at, "
            "snapshots.observed_at, snapshots.archive_url, snapshots.requested_archive_url, "
            "snapshots.extract_recipe_sha256 FROM snapshots WHERE snapshot_id = %s"
        ),
        (snapshot_id,),
    ).fetchone()
    if row is None or row["classification"] not in {"Ok", "NotModified"}:
        raise ValueError("acquisition requires a successful retained snapshot")
    identity = request_id(request(source, row["method"], row["url"], row["form"]))
    conn.execute(
        (
            "INSERT INTO event_stage_operations (stage, source, request_id, watch_id, snapshot_id, occurred_at, run_id) VALUES ('acquired', %s, %s, %s, %s, %s, %s) ON CONFLICT(snapshot_id) DO NOTHING"
        ),
        (source, identity, row["watch_id"], snapshot_id, row["fetched_at"], row["run_id"]),
    )


def interpreted(
    conn: engine.Connection,
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
        "SELECT 1 AS _value_0 FROM admission_decisions WHERE decision_id = %s AND generation_id = %s AND state = 'accepted'",
        (decision_id, generation_id),
    ).fetchone()
    if decision is None:
        raise ValueError("interpretation requires its accepted decision")
    conn.execute(
        (
            "INSERT INTO event_stage_operations (stage, source, watch_id, generation_id, decision_id, occurred_at, run_id) VALUES ('interpreted', %s, %s, %s, %s, %s, %s) ON CONFLICT(generation_id) DO NOTHING"
        ),
        (source, watch_id, generation_id, decision_id, occurred_at, run_id),
    )
