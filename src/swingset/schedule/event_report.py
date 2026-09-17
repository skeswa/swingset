"""Cheap enumeration catalog and an explicit, freshly verified event drill-down."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any

from swingset.fetch.archive import Archive

from .event_inventory import inventory
from .event_service import report as service_report

if TYPE_CHECKING:
    from swingset.config import Config


def report(
    conn: sqlite3.Connection,
    archive: Archive,
    *,
    now: datetime,
    source: str | None = None,
    source_ref: str | None = None,
    operator_hold: bool = False,
    config: Config | None = None,
) -> dict[str, Any]:
    """Use the caller's read snapshot; never bootstrap or recover artifacts."""
    if source_ref is not None and source is None:
        raise ValueError("--source-event requires --source")
    if archive.recovery is not None:
        raise ValueError("event reporting requires an Archive without recovery")
    supported = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_event_inventory'").fetchone()
        is not None
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "supported": supported,
        "snapshot_at": now.isoformat(),
        "operator_hold": operator_hold,
        "source": source,
        "source_ref": source_ref,
        "catalog_basis": "retained enumeration membership; local artifacts not verified",
        "local_stage_totals": None,
        "published_pages": None,
        "eligible_service_age_seconds": None,
        "detail": None,
    }
    if not supported:
        return {**result, "reason": "event_inventory_schema_unavailable", "events": []}
    from .event_accounting_report import report as accounting_report

    result["accounting"] = accounting_report(
        conn, archive, config, now=now, source=source, source_ref=source_ref
    )
    if config is not None:
        from .event_pressure import report as pressure_report

        result["expansion_pressure"] = pressure_report(
            conn, config, now=now, source=source, source_ref=source_ref
        )
    events = [
        dict(row)
        for row in conn.execute(
            "SELECT i.source,i.source_ref,i.enumeration_id,i.first_known_at,"
            "i.bootstrap_basis,m.event_id AS canonical_event_id,e.pagination,"
            "e.pagination_reason,CASE WHEN e.enumeration_id IS NOT NULL THEN "
            "(SELECT count(*) FROM source_event_enumeration_members p "
            "WHERE p.enumeration_id=e.enumeration_id) END AS listed_pages "
            "FROM source_event_inventory i "
            "LEFT JOIN source_event_enumerations e USING(enumeration_id) "
            "LEFT JOIN source_event_map m ON m.source=i.source AND m.source_ref=i.source_ref "
            "WHERE (? IS NULL OR i.source=?) AND (? IS NULL OR i.source_ref=?) "
            "ORDER BY i.source,i.source_ref",
            (source, source, source_ref, source_ref),
        )
    ]
    result.update(
        events=events,
        registered_events=len(events),
        enumerated_events=sum(row["enumeration_id"] is not None for row in events),
        legacy_unassessed_events=sum(row["enumeration_id"] is None for row in events),
        detail_command="doctor --source SOURCE --source-event SOURCE_REF --json",
    )
    if source is not None and source_ref is not None:
        result["detail"] = inventory(conn, archive, source=source, source_ref=source_ref, now=now)
        from .event_timing import report as timing_report

        result["detail"]["acquisition_timing"] = timing_report(
            conn, source=source, source_ref=source_ref, now=now, operator_hold=operator_hold
        )
        result["detail"]["service_history"] = service_report(
            conn, source=source, source_ref=source_ref
        )
        from .event_blocker_history import report as blocker_history
        from .event_publication import report as publication_report

        result["detail"]["blocker_history"] = blocker_history(
            conn, source=source, source_ref=source_ref
        )
        from .event_progress_report import report as progress_report

        result["detail"]["progress_history"] = progress_report(
            conn, source=source, source_ref=source_ref, now=now, config=config
        )
        result["detail"]["publication"] = publication_report(
            archive.state_dir, source=source, source_ref=source_ref
        )
        if config is not None:
            from .event_readiness import report as readiness_report

            watch_ids = {
                watch
                for member in result["detail"].get("members", [])
                for watch in member["watch_ids"]
            }
            watch_ids.update(
                row[0]
                for row in conn.execute(
                    "SELECT watch_id FROM watches WHERE source=? AND source_ref=?",
                    (source, source_ref),
                )
            )
            result["detail"]["request_blockers"] = readiness_report(
                conn,
                config,
                source=source,
                source_ref=source_ref,
                watch_ids=sorted(watch_ids),
                now=now,
                operator_hold=operator_hold,
            )
    return result
