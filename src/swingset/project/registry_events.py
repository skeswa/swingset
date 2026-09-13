"""Evidence-based reconciliation of registry placements to canonical events."""

from __future__ import annotations

import sqlite3

from swingset.model.ids import series_slug
from swingset.state.findings import Finding, replace_findings
from swingset.state.work import WorkUnit, bump_revision, enqueue


def _name(value: str) -> str:
    return series_slug(value)


def reconcile_registry_events(
    conn: sqlite3.Connection,
    *,
    reconciled_at: str,
    run_id: str,
    wsdc_id: int | None = None,
    excluded_event_ids: frozenset[str] = frozenset(),
) -> bool:
    """Set event_id only when canonical series-name and month evidence is unique."""
    events: dict[tuple[str, str], list[str]] = {}
    for row in conn.execute(
        "SELECT event_id,name,COALESCE(NULLIF(event_month,''),substr(end_date,1,7)),series_id FROM events"
    ):
        if str(row[0]) in excluded_event_ids:
            continue
        events.setdefault((_name(str(row[1])), str(row[2])[:7]), []).append(str(row[0]))
        events.setdefault((str(row[3]), str(row[2])[:7]), []).append(str(row[0]))

    changed = False
    affected: set[str] = set()
    findings: dict[int, list[Finding]] = {}
    query = "SELECT wsdc_id,role,series_id,event_month,division,dance_style,series_name_raw,event_id FROM registry_placements"
    parameters: tuple[object, ...] = ()
    if wsdc_id is not None:
        query += " WHERE wsdc_id=?"
        parameters = (wsdc_id,)
    rows = conn.execute(query, parameters).fetchall()
    for row in rows:
        candidates = sorted(
            events.get(
                (str(row[2]), str(row[3])[:7]),
                events.get((_name(str(row[6])), str(row[3])[:7]), []),
            )
        )
        desired = candidates[0] if len(candidates) == 1 else None
        previous = str(row[7]) if row[7] is not None else None
        if len(candidates) > 1:
            subject_id = "|".join(str(value) for value in row[:6])
            findings.setdefault(int(row[0]), []).append(
                Finding(
                    kind="event_alias",
                    subject_kind="registry_placement",
                    subject_id=subject_id,
                    severity="warning",
                    summary="Registry placement matches multiple events by exact name and month",
                    evidence={
                        "series_name_raw": str(row[6]),
                        "event_month": str(row[3]),
                        "candidate_event_ids": candidates,
                    },
                )
            )
        if previous == desired:
            continue
        conn.execute(
            "UPDATE registry_placements SET event_id=? WHERE wsdc_id=? AND role=? AND series_id=? AND event_month=? AND division=? AND dance_style=?",
            (desired, *row[:6]),
        )
        changed = True
        affected.update(value for value in (previous, desired) if value is not None)

    owners = (
        {wsdc_id}
        if wsdc_id is not None
        else set(findings)
        | {
            int(row[0])
            for row in conn.execute(
                "SELECT owner_id FROM findings WHERE owner_kind='registry_reconciliation' AND closed_at IS NULL"
            )
        }
    )
    finding_change = False
    for owner in sorted(owners):
        finding_change |= replace_findings(
            conn,
            owner_kind="registry_reconciliation",
            owner_id=str(owner),
            findings=tuple(findings.get(owner, ())),
            opened_at=reconciled_at,
            run_id=run_id,
        )
    if changed:
        bump_revision(conn, "dancers")
    if affected:
        enqueue(
            conn,
            (WorkUnit("link", "event", event_id) for event_id in sorted(affected)),
            enqueued_at=reconciled_at,
        )
    return changed or finding_change
