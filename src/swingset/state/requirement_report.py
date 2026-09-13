"""Consistent local reporting over requirements, transitions, and fixed cohorts."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from .control_scopes import for_requirement
from .control_time import ControlTime
from .controls import ActionScope, matching_pauses
from .controls import status as control_status
from .lag_report import pipeline_lag
from .requirements import POLICY_VERSION, UNMET, Requirement


def _age(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, (now - datetime.fromisoformat(value)).total_seconds())
    except (ValueError, TypeError):
        return None


def _derivation_inventory(conn: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    if "derivation_scopes" not in tables:
        return {
            "basis": "legacy pending work until H15 fingerprints",
            "pending": [
                dict(row)
                for row in conn.execute(
                    "SELECT stage,count(*) AS count FROM pending_work GROUP BY stage"
                )
            ],
        }
    from .work import unfinished_units

    pending = Counter(unit.stage for unit in unfinished_units(conn))
    return {
        "basis": "parse tokens; desired versus materialized fingerprints for projection and linking",
        "scope": "all known retained scopes, including previously materialized scopes",
        "pending": [{"stage": stage, "count": count} for stage, count in sorted(pending.items())],
        "registered_scopes": [
            dict(row)
            for row in conn.execute(
                "SELECT stage,count(*) AS count,sum(materialized_generation_id IS NULL) AS unassessed "
                "FROM derivation_scopes GROUP BY stage ORDER BY stage"
            )
        ],
        "retained_generations": [
            dict(row)
            for row in conn.execute(
                "SELECT stage,count(*) AS count FROM derivation_generations GROUP BY stage ORDER BY stage"
            )
        ],
        "build_materializations": [
            dict(row)
            for row in conn.execute(
                "SELECT s.unit_kind,s.unit_id,g.generation_id,g.input_fingerprint,g.created_at "
                "FROM derivation_scopes s JOIN derivation_generations g "
                "ON g.generation_id=s.materialized_generation_id WHERE s.stage='build' "
                "ORDER BY s.unit_kind,s.unit_id"
            )
        ],
        "build_basis": "last durable candidate materialization; this does not establish publication",
    }


def _active_attempts(
    conn: sqlite3.Connection,
    tables: set[str],
    source: str | None,
    kind: str | None,
    requirement_id: str | None,
) -> int:
    """Count durable running work even before its first requirement exists."""
    has_work = "work_attempts" in tables
    deduplicate = (
        " AND NOT EXISTS (SELECT 1 FROM work_attempts w WHERE a.attempt_id='work:'||w.attempt_id)"
        if has_work
        else ""
    )
    legacy = conn.execute(
        "SELECT count(*) FROM requirement_attempts a JOIN findings f ON f.finding_id=a.requirement_id "
        "WHERE a.outcome='running' AND (? IS NULL OR f.source=?) AND (? IS NULL OR f.kind=?) "
        "AND (? IS NULL OR f.finding_id=?)" + deduplicate,
        (source, source, kind, kind, requirement_id, requirement_id),
    ).fetchone()[0]
    if not has_work or kind not in {None, "work_attempt"}:
        return int(legacy)
    count = int(legacy)
    for row in conn.execute(
        "SELECT a.stage,a.unit_kind,a.unit_id,a.requirement_id,w.source "
        "FROM work_attempts a LEFT JOIN snapshots s ON a.stage='parse' AND s.snapshot_id=a.unit_id "
        "LEFT JOIN watches w ON w.watch_id=s.watch_id WHERE a.outcome='running'"
    ):
        if source is not None and row["source"] != source:
            continue
        identifier = (
            row["requirement_id"]
            or Requirement(
                "work_attempt",
                json.dumps(tuple(row[:3]), separators=(",", ":")),
                row["source"],
                "ready",
                "",
                {},
            ).identifier
        )
        if requirement_id is None or requirement_id == identifier:
            count += 1
    return count


def inventory(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    source: str | None = None,
    kind: str | None = None,
    requirement_id: str | None = None,
    since: datetime | None = None,
    stale_after: float = 1800,
    transition_after: int | None = None,
    attempt_after: int | None = None,
) -> dict[str, Any]:
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "requirement_scan" not in tables:
        return {
            "schema_version": 1,
            "snapshot_at": now.isoformat(),
            "available": False,
            "reason": "requirement migration pending",
            "stale": True,
        }
    selected = "(? IS NULL OR source=?) AND (? IS NULL OR kind=?) AND (? IS NULL OR finding_id=?)"
    values = (source, source, kind, kind, requirement_id, requirement_id)
    rows = [dict(row) for row in conn.execute(f"SELECT * FROM findings WHERE {selected}", values)]
    pauses = [
        dict(row)
        for row in conn.execute("SELECT * FROM operator_pauses")
        if row["until_at"] is None
        or (_age(row["until_at"], now) == 0 and datetime.fromisoformat(row["until_at"]) > now)
    ]
    paused_sources = {row["scope_id"] for row in pauses if row["scope_kind"] == "source"}
    paused_hosts = {row["scope_id"] for row in pauses if row["scope_kind"] == "host"}
    paused_hosts.update(
        row["host"]
        for row in conn.execute(
            "SELECT host,paused_until FROM hosts WHERE paused_until IS NOT NULL"
        )
        if datetime.fromisoformat(row["paused_until"]) > now
    )
    pause_all = any(row["scope_kind"] == "all" for row in pauses)
    pause_cache: dict[ActionScope, list[dict[str, Any]]] = {}
    control_time = ControlTime(conn, now)

    def row_pauses(row: dict[str, Any], scope: ActionScope | None = None) -> list[dict[str, Any]]:
        if not pauses:
            return []
        scope = scope or for_requirement(conn, row)
        if scope not in pause_cache:
            pause_cache[scope] = matching_pauses(conn, scope, now=now)
        return pause_cache[scope]

    for row in rows:
        scope = for_requirement(conn, row) if control_time.has_history or pauses else None
        row["pauses"] = row_pauses(row, scope)
        row["no_progress_clock"] = control_time.measure(
            scope, row["last_progress_at"] or row["opened_at"]
        )
        row["paused"] = bool(row["pauses"])
        row["eligible"] = (
            row["state"] == "ready"
            and not row["paused"]
            and (
                row["next_eligible_at"] is None
                or datetime.fromisoformat(row["next_eligible_at"]) <= now
            )
        )
        row["age_seconds"] = _age(row["opened_at"], now)
        row["status_age_seconds"] = _age(row["status_at"] or row["opened_at"], now)
    states = Counter(row["state"] for row in rows)
    snapshot = dict(conn.execute("SELECT * FROM requirement_scan WHERE singleton=1").fetchone())
    lag = _age(snapshot["last_completed_at"] or snapshot["started_at"], now)
    since = since or now - timedelta(days=1)
    transitions = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM requirement_transitions WHERE at>=? AND at<=? AND (? IS NULL OR source=?) AND (? IS NULL OR kind=?) AND (? IS NULL OR requirement_id=?) ORDER BY transition_id",
            (since.isoformat(), now.isoformat(), *values),
        )
    ]
    attempts = [
        dict(row)
        for row in conn.execute(
            f"SELECT a.* FROM requirement_attempts a JOIN findings f ON f.finding_id=a.requirement_id WHERE a.at>=? AND a.at<=? AND {selected}",
            (since.isoformat(), now.isoformat(), *values),
        )
    ]
    if transition_after is not None:
        transitions = [row for row in transitions if row["transition_id"] > transition_after]
    if attempt_after is not None:
        newer_attempts = {
            row[0]
            for row in conn.execute(
                "SELECT attempt_id FROM requirement_attempts WHERE rowid>?", (attempt_after,)
            )
        }
        attempts = [row for row in attempts if row["attempt_id"] in newer_attempts]
    closing = sum(row["state"] in UNMET for row in rows)
    increases = sum(
        row["old_state"] not in UNMET and row["new_state"] in UNMET for row in transitions
    )
    decreases = sum(
        row["old_state"] in UNMET and row["new_state"] not in UNMET for row in transitions
    )
    counts = Counter(row["event"] for row in transitions)
    counts["opened"] = sum(
        row["event"] == "opened" and row["new_state"] in UNMET for row in transitions
    )
    cohorts = []
    captured_cohorts = list(conn.execute("SELECT * FROM requirement_cohorts ORDER BY cohort_id"))
    first_open = (
        {
            row[0]: (row[1], datetime.fromisoformat(row[2]))
            for row in conn.execute(
                "SELECT t.requirement_id,t.transition_id,t.at FROM requirement_transitions t "
                "JOIN (SELECT requirement_id,min(transition_id) AS first_id FROM requirement_transitions GROUP BY requirement_id) firsts "
                "ON t.transition_id=firsts.first_id"
            )
        }
        if captured_cohorts
        else {}
    )
    for cohort in captured_cohorts:
        members = [
            dict(row)
            for row in conn.execute(
                "SELECT m.requirement_id,f.* FROM requirement_cohort_members m LEFT JOIN findings f ON f.finding_id=m.requirement_id WHERE m.cohort_id=?",
                (cohort["cohort_id"],),
            )
        ]
        member_ids = {member["requirement_id"] for member in members}
        blocked = any(
            row["state"] != "satisfied" and (row["state"] != "ready" or bool(row_pauses(row)))
            for row in members
        )
        compatible = cohort["policy_version"] == POLICY_VERSION and all(
            row["policy_version"] == cohort["policy_version"] for row in members
        )
        satisfied = sum(row["state"] == "satisfied" for row in members)
        cutoff = (
            cohort["first_open_transition_cutoff"]
            if "first_open_transition_cutoff" in cohort.keys()
            else None
        )
        baseline_at = datetime.fromisoformat(cohort["created_at"])
        newly_discovered = uncertain_discovery = 0
        for row in rows:
            if (
                row["finding_id"] in member_ids
                or row["policy_version"] != cohort["policy_version"]
                or (cohort["source"] is not None and row["source"] != cohort["source"])
                or (cohort["kind"] is not None and row["kind"] != cohort["kind"])
            ):
                continue
            opened = first_open.get(row["finding_id"])
            if opened is None:
                uncertain_discovery += 1
            elif cutoff is not None:
                newly_discovered += opened[0] > cutoff
            elif opened[1] > baseline_at:
                newly_discovered += 1
            elif opened[1] == baseline_at:
                uncertain_discovery += 1
        cohorts.append(
            {
                "cohort_id": cohort["cohort_id"],
                "baseline_at": cohort["created_at"],
                "total": len(members),
                "satisfied": satisfied,
                "retired": sum(row["state"] == "out_of_scope" for row in members),
                "missing": sum(row["state"] is None for row in members),
                "outside_cohort": newly_discovered if not uncertain_discovery else None,
                "outside_cohort_known_new": newly_discovered,
                "outside_cohort_uncertain": uncertain_discovery,
                "outside_cohort_basis": "first-open transition after captured cutoff"
                if cutoff is not None
                else "legacy capture: later timestamps only; same-time order unknown",
                "first_open_transition_cutoff": cutoff,
                "compatible": compatible,
                "blocked": blocked,
                "completion_percent": 100 * satisfied / len(members)
                if members and cohort["bounded"] and compatible and not blocked
                else None,
                "eta": None,
            }
        )
    unresolved_ages = [
        row["age_seconds"]
        for row in rows
        if row["state"] in UNMET and row["age_seconds"] is not None
    ]
    progress_ages = [_age(row["last_progress_at"], now) for row in rows if row["last_progress_at"]]
    last_attempts = {
        row["requirement_id"]: dict(row)
        for row in conn.execute(
            "SELECT a.* FROM requirement_attempts a JOIN "
            "(SELECT requirement_id,max(rowid) AS latest FROM requirement_attempts GROUP BY requirement_id) latest "
            "ON a.rowid=latest.latest"
        )
    }
    alerts = [
        {
            "requirement_id": row["finding_id"],
            "reason": "eligible_requirement_has_no_verified_progress",
            "evidence": row["evidence_json"],
            "blocking_reason": row["blocking_reason"],
            "last_attempt": last_attempts.get(row["finding_id"]),
            "next_action": row["next_action"],
        }
        for row in rows
        if row["eligible"]
        and row["no_progress_clock"]["unpaused_seconds"] is not None
        and row["no_progress_clock"]["unpaused_seconds"] > stale_after
    ]
    last_run = conn.execute(
        "SELECT started_at,finished_at FROM runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    worker_state = (
        "stopped_or_stale"
        if last_run is None or (_age(last_run[0], now) or 0) > stale_after
        else "recent_unfinished_run"
        if last_run[1] is None
        else "idle"
    )
    return {
        "schema_version": 1,
        "available": True,
        "snapshot_at": now.isoformat(),
        "transition_cursor": conn.execute(
            "SELECT coalesce(max(transition_id),0) FROM requirement_transitions"
        ).fetchone()[0],
        "attempt_cursor": conn.execute(
            "SELECT coalesce(max(rowid),0) FROM requirement_attempts"
        ).fetchone()[0],
        "mode": "shadow",
        "execution_enabled": False,
        "scan": snapshot,
        "worker_state": worker_state,
        "controls": control_status(conn, now=now),
        "worker_state_basis": "durable run timestamps; process liveness is unverified",
        "alerts": alerts,
        "scan_lag_seconds": lag,
        "stale": snapshot["last_scanned_at"] is None
        or (_age(snapshot["last_scanned_at"], now) or 0) > stale_after
        or (lag is not None and lag > stale_after),
        "states": dict(states),
        "unmet": closing,
        "eligible": sum(row["eligible"] for row in rows),
        "paused": sum(row["paused"] and row["state"] in UNMET for row in rows),
        "active_attempts": _active_attempts(conn, tables, source, kind, requirement_id),
        "unknown_no_progress_clocks": sum(
            row["no_progress_clock"]["unpaused_seconds"] is None
            for row in rows
            if row["state"] in UNMET
        ),
        "oldest_unresolved_age_seconds": max(unresolved_ages, default=None),
        "seconds_since_progress": min(
            (age for age in progress_ages if age is not None), default=None
        ),
        "requirements": rows,
        "cohorts": cohorts,
        "universe": "known retained scopes; future registry IDs are unbounded",
        "completion_percent": None,
        "eta": None,
        "changes": {
            "since": since.isoformat(),
            "opening_unmet": closing - increases + decreases,
            "opened": counts["opened"],
            "reopened": counts["reopened"],
            "satisfied": counts["satisfied"],
            "retired": counts["retired"],
            "closing_unmet": closing,
            "attempts": len(attempts),
            "failures": sum(
                row["outcome"] in {"failed", "blocked", "transient", "unavailable", "interrupted"}
                for row in attempts
            ),
        },
        "derivation": _derivation_inventory(conn, tables),
        "pipeline_lag": pipeline_lag(
            conn,
            now,
            source=source,
            pause_all=pause_all,
            paused_sources=paused_sources,
            paused_hosts=paused_hosts,
            alert_after=stale_after,
            control_time=control_time,
        ),
    }


def human_report(report: dict[str, Any]) -> str:
    """The human view renders the same inventory snapshot as JSON."""
    if not report.get("available"):
        return f"Requirement inventory unavailable: {report.get('reason')}"
    lines = [
        f"Requirements at {report['snapshot_at']} (shadow, {'stale' if report['stale'] else 'current'})",
        f"Unmet: {report['unmet']}; eligible: {report['eligible']}; paused: {report['paused']}; active: {report['active_attempts']}",
        f"Scan cursor: {report['scan']['cursor'] or '(start)'}; last complete: {report['scan']['last_completed_at']}",
    ]
    for row in report["requirements"]:
        lines.append(
            f"{row['finding_id']} {row['kind']} {row['state']}: {row['next_action']}"
            + (f" ({row['blocking_reason']})" if row["blocking_reason"] else "")
        )
        lines.append(json.dumps(row, sort_keys=True, default=str))
    for key, value in report.items():
        if key != "requirements":
            lines.append(f"{key}: {json.dumps(value, sort_keys=True, default=str)}")
    return "\n".join(lines)
