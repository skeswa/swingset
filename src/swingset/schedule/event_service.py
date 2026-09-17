"""Explain recorded event-owned request service without inferring progress."""

from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from typing import Any


def _policies(conn: sqlite3.Connection, table: str, identifiers: set[str]) -> dict[str, Any]:
    result: dict[str, Any] = {key: {"status": "missing", "policy": None} for key in identifiers}
    for key, raw in conn.execute(
        f"SELECT digest,policy_json FROM {table} WHERE digest IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(identifiers)),),
    ):
        try:
            if not isinstance(raw, str):
                raise ValueError("recorded policy is not text")
            value = json.loads(raw)
            valid = isinstance(value, dict) and sha256(raw.encode()).hexdigest() == key
        except (ValueError, TypeError):
            value, valid = None, False
        result[key] = {
            "status": "verified_recorded" if valid else "invalid",
            "policy": value if valid else None,
        }
    return result


def report(
    conn: sqlite3.Connection, *, source: str, source_ref: str, limit: int = 20
) -> dict[str, Any]:
    """Use one caller-owned read snapshot; bound details, retain receipt totals.

    Counts belong to the event selected for each issued request, including
    redirects and failures. They are not page completions, current eligibility,
    or service attributed to every event sharing a watch. Historical gaps before
    event receipt recording remain unknown.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("event service history limit must be an integer from 1 to 100")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    supported = {
        "scheduler_event_requests",
        "scheduler_event_turns",
        "scheduler_event_policies",
        "scheduler_requests",
    } <= tables
    capacity = {"scheduler_capacity_requests", "scheduler_capacity_policies"} <= tables
    result: dict[str, Any] = {
        "supported": supported,
        "source": source,
        "source_ref": source_ref,
        "basis": "issued request receipts attributed to the selected event owner",
        "aggregate_scope": "all retained receipts for this selected event owner",
        "policy_detail_scope": "current turns and bounded recent request details",
        "history_before_receipts": "unknown",
        "successful_progress_at": None,
        "eligible_service_age_seconds": None,
        "current_eligibility": None,
        "capacity_supported": capacity,
        "total_recorded_requests": None,
        "last_issued_at": None,
        "actual_hosts": [],
        "capacity_totals": [],
        "capacity_recorded_requests": None,
        "capacity_unrecorded_requests": None,
        "current_turns": [],
        "recent_requests": [],
        "detail_limit": limit,
        "details_truncated": False,
        "turn_policies": {},
        "capacity_policies": {},
    }
    if not supported:
        return {**result, "reason": "event_service_schema_unavailable"}
    params = (source, source_ref)
    selected = "FROM scheduler_event_requests e JOIN scheduler_requests r USING(action_id) WHERE e.source=? AND e.source_ref=?"
    total = int(conn.execute("SELECT count(*) " + selected, params).fetchone()[0])
    hosts = [
        dict(row)
        for row in conn.execute(
            "SELECT r.host AS actual_host,count(*) AS issued_requests,sum(r.body_bytes) AS recorded_body_bytes "
            + selected
            + " GROUP BY r.host ORDER BY r.host",
            params,
        )
    ]
    turns = [
        dict(row)
        for row in conn.execute(
            "SELECT host AS selected_host,category,owner_key,position,used,target_requests,policy_digest,enrolled_at,last_issued_at FROM scheduler_event_turns WHERE source=? AND source_ref=? ORDER BY host,category,position",
            params,
        )
    ]
    capacity_join = "LEFT JOIN scheduler_capacity_requests c USING(action_id)" if capacity else ""
    capacity_fields = (
        ",c.lane,c.purpose,c.reason AS capacity_reason,c.policy_digest AS capacity_policy_digest,c.credit_before,c.credit_after"
        if capacity
        else ""
    )
    execution_join = (
        "LEFT JOIN execution_admissions a USING(action_id)"
        if "execution_admissions" in tables
        else ""
    )
    execution_fields = (
        ",a.state AS execution_state,a.outcome AS recorded_outcome"
        if execution_join
        else ",NULL AS execution_state,NULL AS recorded_outcome"
    )
    recent = [
        dict(row)
        for row in conn.execute(
            "SELECT e.action_id,e.selected_host,e.category,e.enumeration_id,e.turn_position,e.policy_digest AS turn_policy_digest,e.reason AS selection_reason,r.host AS actual_host,r.work_key,r.issued_at,r.body_bytes AS recorded_body_bytes"
            + capacity_fields
            + execution_fields
            + " FROM scheduler_event_requests e JOIN scheduler_requests r USING(action_id) "
            + capacity_join
            + " "
            + execution_join
            + " WHERE e.source=? AND e.source_ref=? ORDER BY julianday(r.issued_at) DESC,r.issued_at DESC,e.action_id DESC LIMIT ?",
            (*params, limit),
        )
    ]
    for row in recent:
        row["capacity"] = (
            {
                "lane": row.pop("lane"),
                "purpose": row.pop("purpose"),
                "reason": row.pop("capacity_reason"),
                "policy_digest": row.pop("capacity_policy_digest"),
                "credit_before": row.pop("credit_before"),
                "credit_after": row.pop("credit_after"),
            }
            if capacity
            else None
        )
        if row["capacity"] is not None and row["capacity"]["policy_digest"] is None:
            row["capacity"] = None
    capacity_totals = (
        [
            dict(row)
            for row in conn.execute(
                "SELECT c.selected_host,c.lane,c.purpose,c.reason,count(*) AS issued_requests FROM scheduler_event_requests e JOIN scheduler_capacity_requests c USING(action_id) WHERE e.source=? AND e.source_ref=? GROUP BY c.selected_host,c.lane,c.purpose,c.reason ORDER BY c.selected_host,c.lane,c.purpose,c.reason",
                params,
            )
        ]
        if capacity
        else []
    )
    attributed = sum(row["issued_requests"] for row in capacity_totals)
    turn_ids = {row["policy_digest"] for row in turns} | {
        row["turn_policy_digest"] for row in recent
    }
    capacity_ids = {
        row["capacity"]["policy_digest"] for row in recent if row["capacity"] is not None
    }
    return {
        **result,
        "reason": "recorded_attempts" if total else "no_recorded_attempts",
        "total_recorded_requests": total,
        "actual_hosts": hosts,
        "last_issued_at": recent[0]["issued_at"] if recent else None,
        "current_turns": turns,
        "recent_requests": recent,
        "details_truncated": total > len(recent),
        "capacity_totals": capacity_totals,
        "capacity_recorded_requests": attributed,
        "capacity_unrecorded_requests": total - attributed,
        "turn_policies": _policies(conn, "scheduler_event_policies", turn_ids),
        "capacity_policies": _policies(conn, "scheduler_capacity_policies", capacity_ids)
        if capacity
        else {},
    }
