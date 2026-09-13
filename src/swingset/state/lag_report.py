"""Read-only schedule and unfinished-work ages, without declaring work executable."""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from .control_scopes import for_unit, for_watch
from .control_time import ControlTime
from .controls import ActionScope, matching_pauses
from .work import WorkUnit, unfinished_units


def pipeline_lag(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    source: str | None,
    pause_all: bool,
    paused_sources: set[str],
    paused_hosts: set[str],
    alert_after: float,
    control_time: ControlTime | None = None,
) -> dict[str, Any]:
    """Report observed lateness without inventing a missing change timestamp."""

    def age(value: str | None) -> float | None:
        try:
            return (now - datetime.fromisoformat(value)).total_seconds() if value else None
        except (ValueError, TypeError):
            return None

    control_time = control_time or ControlTime(conn, now)
    pause_cache: dict[ActionScope, list[dict[str, Any]]] = {}
    has_pauses = conn.execute("SELECT 1 FROM operator_pauses LIMIT 1").fetchone() is not None

    def holds(scope: ActionScope) -> list[dict[str, Any]]:
        if scope not in pause_cache:
            pause_cache[scope] = matching_pauses(conn, scope, now=now)
        return pause_cache[scope]

    cooldowns = {row[0]: row[1] for row in conn.execute("SELECT host,next_allowed_at FROM hosts")}
    acquisition = []
    unknown_schedule = 0
    for row in conn.execute(
        "SELECT watch_id,source,url,archive_url,next_check_at,paused_until FROM watches "
        "WHERE state NOT IN ('gone','sealed','retired') AND (? IS NULL OR source=?)",
        (source, source),
    ):
        overdue = age(row["next_check_at"])
        if overdue is None:
            unknown_schedule += 1
            continue
        if overdue <= 0:
            continue
        host = urlsplit(row["archive_url"] or row["url"]).hostname
        scope = (
            for_watch(conn, source=row["source"], watch_id=row["watch_id"], host=host)
            if has_pauses or control_time.has_history
            else None
        )
        controls = holds(scope) if scope is not None and has_pauses else []
        paused = (
            bool(controls)
            or host in paused_hosts
            or (row["paused_until"] is not None and (age(row["paused_until"]) or 0) < 0)
        )
        cooling = host is not None and (age(cooldowns.get(host)) or 0) < 0
        acquisition.append(
            {
                "watch_id": row["watch_id"],
                "source": row["source"],
                "scheduled_at": row["next_check_at"],
                "lag_seconds": overdue,
                "lag_clock": control_time.measure(scope, row["next_check_at"]),
                "paused": paused,
                "pauses": controls,
                "cooldown": cooling,
            }
        )
    acquisition.sort(key=lambda row: (-row["lag_seconds"], row["watch_id"]))
    pending = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM pending_work ORDER BY enqueued_at,stage,unit_kind,unit_id"
        )
    ]
    derived = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='derivation_scopes'").fetchone()
        is not None
    )
    if derived:
        hints = {(row["stage"], row["unit_kind"], row["unit_id"]): row for row in pending}
        pending = [
            hints.get(
                (unit.stage, unit.unit_kind, unit.unit_id),
                {
                    "stage": unit.stage,
                    "unit_kind": unit.unit_kind,
                    "unit_id": unit.unit_id,
                    "enqueued_at": None,
                },
            )
            for unit in unfinished_units(conn)
        ]
    for row in pending:
        measured = age(row["enqueued_at"])
        row["lag_seconds"] = max(0.0, measured) if measured is not None else None
        scope = (
            for_unit(conn, WorkUnit(row["stage"], row["unit_kind"], row["unit_id"]))
            if has_pauses or control_time.has_history
            else None
        )
        row["lag_clock"] = control_time.measure(scope, row["enqueued_at"])
        row["pauses"] = holds(scope) if scope is not None else []
        row["paused"] = bool(row["pauses"])
    known_pending = [row for row in pending if row["lag_seconds"] is not None]
    known_pending.sort(
        key=lambda row: (-row["lag_seconds"], row["stage"], row["unit_kind"], row["unit_id"])
    )
    alerts = []
    acquisition_alerts = [
        row
        for row in acquisition
        if row["lag_clock"]["unpaused_seconds"] is not None
        and row["lag_clock"]["unpaused_seconds"] > alert_after
        and not row["paused"]
        and not row["cooldown"]
    ]
    if acquisition_alerts:
        alerts.append(
            {
                "reason": "acquisition_schedule_lag",
                "count": len(acquisition_alerts),
                "oldest": acquisition_alerts[0],
                "next_action": "inspect source budget, schedule and acquisition blockers; no request is made by reporting",
            }
        )
    derivation_alerts = [
        row
        for row in known_pending
        if row["lag_clock"]["unpaused_seconds"] is not None
        and row["lag_clock"]["unpaused_seconds"] > alert_after
        and not row["paused"]
    ]
    if derivation_alerts:
        alerts.append(
            {
                "reason": "queued_derivation_lag",
                "count": len(derivation_alerts),
                "oldest": derivation_alerts[0],
                "next_action": "inspect the unfinished scope and its parse or derivation findings; reporting does not retry work",
            }
        )
    return {
        "acquisition": {
            "basis": "seconds past an existing watch's next_check_at; schedule age, not a failed verification or an execution-eligibility claim",
            "source_filter": source,
            "filter_scope": "source-wide schedules; requirement kind and ID filters do not redefine watch scheduling",
            "overdue_watches": len(acquisition),
            "paused_overdue_watches": sum(row["paused"] for row in acquisition),
            "cooldown_overdue_watches": sum(row["cooldown"] for row in acquisition),
            "unknown_schedule_watches": unknown_schedule,
            "oldest_lag_seconds": acquisition[0]["lag_seconds"]
            if acquisition
            else None
            if unknown_schedule
            else 0.0,
            "oldest": acquisition[0] if acquisition else None,
        },
        "derivation": {
            "basis": (
                "unfinished parse tokens and desired versus materialized derivations; ages use available queue hint times, which replacement inputs may reset; missing times remain unknown"
                if derived
                else "seconds since current legacy offline queue enqueue, including parse; replacement inputs may reset that timestamp; H15 supplies desired fingerprints"
            ),
            "filter_scope": "global unfinished work; requirement filters do not claim per-kind derivation ownership",
            "pending_units": len(pending),
            "paused_by_all": pause_all,
            "paused_units": sum(row["paused"] for row in pending),
            "by_stage": dict(Counter(row["stage"] for row in pending)),
            "unknown_enqueue_times": len(pending) - len(known_pending),
            "oldest_lag_seconds": known_pending[0]["lag_seconds"]
            if known_pending
            else None
            if pending
            else 0.0,
            "oldest": known_pending[0] if known_pending else None,
        },
        "alert_after_seconds": alert_after,
        "alert_basis": "diagnostic report staleness threshold excluding known matching operator pause intervals; historical dependencies and automatic host pause intervals are not reconstructed; host budgets and full work admission are not assessed; this is not an H14 service-gap objective",
        "alerts": alerts,
    }
