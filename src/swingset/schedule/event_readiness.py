"""Recorded event blockers, without claiming that a request may execute."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from swingset.config import Config
from swingset.state import engine
from swingset.state.control_scopes import for_watch
from swingset.state.controls import ActionScope, matching_pauses

from .fairness import backpressure


def _future(value: str | None, now: datetime) -> bool:
    return bool(value and datetime.fromisoformat(value) > now)


def _host(conn: engine.Connection, config: Config, host: str, now: datetime) -> dict[str, Any]:
    row = conn.execute(
        (
            "SELECT hosts.host, hosts.next_allowed_at, hosts.paused_until, hosts.pause_reason, "
            "hosts.pause_streak, hosts.robots_sha256, hosts.robots_fetched_at, hosts.robots_status FROM "
            "hosts WHERE host = %s"
        ),
        (host,),
    ).fetchone()
    recorded = dict(row) if row else {}
    usage = conn.execute(
        "SELECT requests, bytes FROM host_budget WHERE host = %s AND day = %s",
        (host, now.date().isoformat()),
    ).fetchone()
    requests, size = tuple(usage) if usage else (0, 0)
    policy = config.host(host)
    byte_limit = policy.daily_byte_budget
    return {
        "host": host,
        "host_recorded": row is not None,
        "next_allowed_at": recorded.get("next_allowed_at"),
        "cooldown_active": _future(recorded.get("next_allowed_at"), now),
        "paused_until": recorded.get("paused_until"),
        "pause_active": _future(recorded.get("paused_until"), now),
        "pause_reason": recorded.get("pause_reason"),
        "daily_allowance": {
            "basis": "normal_scheduler_allowance; separately authorized sweep not assessed",
            "day": now.date().isoformat(),
            "requests_used": requests,
            "request_limit": policy.daily_request_budget,
            "requests_remaining": max(0, policy.daily_request_budget - requests),
            "requests_exhausted": requests >= policy.daily_request_budget,
            "bytes_used": size,
            "byte_limit": byte_limit,
            "bytes_remaining": max(0, byte_limit - size) if byte_limit is not None else None,
            "bytes_exhausted": byte_limit is not None and size >= byte_limit,
            "next_reset_at": (now + timedelta(days=1))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat(),
        },
    }


def report(
    conn: engine.Connection,
    config: Config,
    *,
    source: str,
    source_ref: str,
    watch_ids: Iterable[str],
    now: datetime,
    operator_hold: bool,
) -> dict[str, Any]:
    """Read the caller's snapshot; never acquire, expire, or reset anything.

    Watch membership is supplied by the event drilldown. Due times are recorded
    polling/retry facts, not a reconstruction of historical eligibility.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("event readiness requires an aware timestamp")
    now = now.astimezone(UTC)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT relname AS name FROM pg_catalog.pg_class WHERE relkind = 'r' AND (PG_TABLE_IS_VISIBLE(oid) AND relnamespace NOT IN ('pg_catalog'::regnamespace,'information_schema'::regnamespace))"
        )
    }
    required = {"watches", "hosts", "host_budget", "snapshots", "pending_work"}
    missing = sorted(required - tables)
    result: dict[str, Any] = {
        "schema_version": 1,
        "supported": not missing,
        "snapshot_at": now.isoformat(),
        "source": source,
        "source_ref": source_ref,
        "operator_hold": operator_hold,
        "source_enabled": config.enabled(source),
        "incomplete_gate_assessment": True,
        "unassessed_gates": [
            "historical_year_and_parent_admission",
            "archive_capture_and_origin_dispatch",
            "robots_and_redirect_hosts",
            "process_inflight_requests",
            "scheduler_class_selection_and_repair_reservation",
        ],
        "basis": "recorded blocker facts; no request eligibility or execution permission",
        "operator_pauses": [],
        "watches": [],
        "hosts": [],
        "backpressure": None,
    }
    if missing:
        return {**result, "reason": "readiness_schema_unavailable", "missing_tables": missing}
    controls_supported = {"control_state", "operator_pauses", "findings"} <= tables
    result["operator_controls_supported"] = controls_supported
    if not controls_supported:
        result["unassessed_gates"].append("operator_controls_schema_unavailable")
    pauses = (
        {
            pause["pause_id"]: pause
            for pause in matching_pauses(conn, ActionScope(sources=frozenset({source})), now=now)
        }
        if controls_supported
        else {}
    )
    identifiers = sorted(set(watch_ids))
    found = set()
    hosts = set()
    watches = []
    for row in conn.execute(
        (
            "SELECT watches.watch_id, watches.source, watches.kind, watches.method, watches.url, "
            "watches.form, watches.archive_url, watches.parser, watches.source_ref, watches.state, "
            "watches.next_check_at, watches.last_checked_at, watches.last_changed_at, "
            "watches.unchanged_streak, watches.etag, watches.last_modified, watches.body_sha256, "
            "watches.paused_until, watches.notes, watches.fingerprint, watches.extract_version, "
            "watches.priority, watches.created_by_snapshot_id, watches.parent_watch_id, watches.ever_ok, "
            "watches.current_observation_snapshot_id, watches.consecutive_404s, watches.first_404_at "
            "FROM watches WHERE watch_id IN (SELECT value FROM jsonb_array_elements_text(%s::jsonb)) ORDER BY watch_id NULLS "
            "FIRST"
        ),
        (json.dumps(identifiers),),
    ):
        found.add(row["watch_id"])
        request_url = row["archive_url"] or row["url"]
        host = urlsplit(request_url).hostname
        if host:
            hosts.add(host)
        watch_pauses = (
            matching_pauses(
                conn,
                for_watch(conn, source=row["source"], watch_id=row["watch_id"], host=host),
                now=now,
            )
            if controls_supported
            else []
        )
        pauses.update((pause["pause_id"], pause) for pause in watch_pauses)
        latest = conn.execute(
            (
                "SELECT snapshot_id, fetched_at, http_status, classification FROM snapshots WHERE watch_id = %s ORDER BY (CAST(EXTRACT(EPOCH FROM ops.try_timestamp(fetched_at)) AS DOUBLE PRECISION) / 86400.0 + 2440587.5) DESC NULLS LAST, snapshot_id DESC NULLS LAST LIMIT 1"
            ),
            (row["watch_id"],),
        ).fetchone()
        watches.append(
            {
                "watch_id": row["watch_id"],
                "source": row["source"],
                "source_ref": row["source_ref"],
                "source_enabled": config.enabled(row["source"]),
                "state": row["state"],
                "state_excludes_ordinary_selection": row["state"] in {"sealed", "retired"},
                "request_url": request_url,
                "request_host": host,
                "request_host_basis": "archive_url" if row["archive_url"] else "url",
                "next_check_at": row["next_check_at"],
                "due": not _future(row["next_check_at"], now) if row["next_check_at"] else None,
                "schedule_basis": "recorded polling or retry time; no eligibility inference",
                "last_checked_at": row["last_checked_at"],
                "paused_until": row["paused_until"],
                "pause_active": _future(row["paused_until"], now),
                "operator_pause_ids": [pause["pause_id"] for pause in watch_pauses],
                "latest_response": dict(latest) if latest else None,
            }
        )
    result.update(
        operator_pauses=sorted(
            pauses.values(), key=lambda value: (value["scope_kind"], value["scope_id"])
        ),
        watches=watches,
        missing_watch_ids=sorted(set(identifiers) - found),
        hosts=[_host(conn, config, host, now) for host in sorted(hosts)],
        backpressure=backpressure(conn, config),
    )
    return result
