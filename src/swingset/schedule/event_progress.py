"""Bounded observations distinguish successful outputs from proven request progress."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from swingset.admission.enumeration_evidence import members as verified_members
from swingset.admission.evidence_budget import BudgetExceeded
from swingset.admission.page_evidence import FORMAT, Limits, Session
from swingset.config import Config
from swingset.fetch.archive import Archive
from swingset.state.db import Database
from swingset.state.event_progress import available

from . import event_accounting, event_gaps, event_retirement
from .event_evidence import request_id
from .event_pressure import token

EVENTS = 8
PAGES = 32
OPERATIONS = 32
ENUMERATION_MEMBERS = 128


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def policy(config: Config, limits: Limits) -> dict[str, Any]:
    values = {
        "format": "observed-event-progress-v4-unavailable-gaps",
        "verifier_format": FORMAT,
        "limits": asdict(limits),
        "events": EVENTS,
        "members_per_event": PAGES,
        "operation_candidates": OPERATIONS,
        "enumeration_members": ENUMERATION_MEMBERS,
        "max_age_seconds": config.scheduler.event_pressure_max_age_seconds,
        "acceptance": "proposed_unmeasured",
    }
    return {"digest": sha256(_json(values).encode()).hexdigest(), "values": values}


def _fence(conn: sqlite3.Connection, source: str, ref: str, digest: str) -> dict[str, Any]:
    if conn.execute(
        "SELECT 1 FROM meta WHERE key='input_bundle_hash' AND length(CAST(value AS BLOB))>4096"
    ).fetchone():
        raise ValueError("input_bundle_token_oversized")
    return token(conn, source, ref, digest)


def _compatible(previous: dict[str, Any], fence: dict[str, Any], now: datetime) -> bool:
    old = json.loads(previous["token_json"])
    if not isinstance(old, dict) or old.keys() != fence.keys():
        raise ValueError("observation_token_invalid")
    # Ordinary new snapshots/admissions change revision. They are precisely the
    # operations whose output may satisfy the previously missing request.
    return (
        previous["availability"] == 0
        and datetime.fromisoformat(previous["valid_until"]) > now
        and previous["enumeration_id"] == fence["enumeration_id"]
        and all(old[key] == fence[key] for key in old if key != "revision")
    )


def _qualify(
    session: Session,
    conn: sqlite3.Connection,
    *,
    page: dict[str, Any],
    stage: str,
    previous: dict[str, Any] | None,
    fence: dict[str, Any],
    now: datetime,
) -> tuple[int | None, str | None]:
    if previous is None or not _compatible(previous, fence, now):
        return None, "no_current_missing_baseline"
    columns = ("operation_id", "snapshot_id", "generation_id", "decision_id", "occurred_at")
    cap = min(OPERATIONS, session.limits.rows)
    searches: list[tuple[str, tuple[Any, ...]]]
    if stage == "acquired":
        searches = [
            (
                "SELECT o.* FROM event_stage_operations o WHERE source=? AND stage=? AND request_id=? AND operation_id>? ORDER BY operation_id LIMIT ?",
                (page["source"], stage, request_id(page), previous["operation_frontier"], cap),
            )
        ]
    else:
        parameters: tuple[Any, ...] = (
            fence["enumeration_id"],
            request_id(page),
            page["source"],
            stage,
            previous["operation_frontier"],
            cap,
        )
        searches = [
            (
                "SELECT DISTINCT o.* FROM source_event_member_watches m JOIN event_stage_operations o USING(watch_id) "
                "WHERE m.enumeration_id=? AND m.request_id=? AND o.source=? AND o.stage=? AND o.operation_id>? ORDER BY o.operation_id LIMIT ?",
                parameters,
            ),
            (
                "SELECT o.* FROM event_stage_operations o WHERE NOT EXISTS(SELECT 1 FROM source_event_member_watches m "
                "WHERE m.enumeration_id=? AND m.request_id=? AND m.watch_id=o.watch_id) "
                "AND o.source=? AND o.stage=? AND o.operation_id>? ORDER BY o.operation_id LIMIT ?",
                parameters,
            ),
        ]
    incomplete = False
    for sql, parameters in searches:
        candidates = session.read(sql, parameters, columns, cap=cap)
        incomplete = incomplete or len(candidates) == cap
        for operation in candidates:
            if datetime.fromisoformat(operation["occurred_at"]) < datetime.fromisoformat(
                previous["observed_at"]
            ):
                continue
            proof = session.verify_operation(
                page,
                snapshot_id=operation["snapshot_id"],
                generation_id=operation["generation_id"],
                decision_id=operation["decision_id"],
            )
            if proof[stage] is True:
                return int(operation["operation_id"]), None
            incomplete = incomplete or proof[stage] is None
    return None, "operation_candidates_unassessed" if incomplete else "no_verified_later_operation"


def _observe(
    session: Session,
    conn: sqlite3.Connection,
    subject: dict[str, Any],
    captured: dict[str, Any],
    now: datetime,
    frontier: int,
    *,
    fresh_subject_budget: bool,
    gap_revisions: dict[str, int | None],
) -> dict[str, Any]:
    source, ref, enumeration = subject["source"], subject["source_ref"], subject["enumeration_id"]
    fence = _fence(conn, source, ref, captured["digest"])
    result: dict[str, Any] = {
        "source": source,
        "source_ref": ref,
        "rowid": subject["rowid"],
        "enumeration_id": enumeration,
        "token": fence,
        "pages": [],
        "cursor": None,
        "cursor_assessed": False,
        "unknown": 0,
    }
    if enumeration is None or fence["revision"] is None:
        result["reason"] = "enumeration_or_invalidation_tracking_unavailable"
        return result
    try:
        result["gap_revision"] = event_gaps.revision(session, source, gap_revisions)
        members = verified_members(
            session,
            enumeration_id=enumeration,
            source=source,
            source_ref=ref,
            limit=ENUMERATION_MEMBERS,
        )
        scans = session.read(
            "SELECT enumeration_id,after_request_id FROM event_progress_scans WHERE source=? AND source_ref=?",
            (source, ref),
            ("enumeration_id", "after_request_id"),
            cap=1,
        )
        prior = scans[0] if scans else None
        cursor = (
            prior["after_request_id"] if prior and prior["enumeration_id"] == enumeration else None
        )
    except (BudgetExceeded, ValueError, KeyError, TypeError, RecursionError) as exc:
        result["reason"] = str(exc)
        result["retry_subject"] = session.exhausted() and not fresh_subject_budget
        return result

    max_age = captured["values"]["max_age_seconds"]
    if event_retirement.prepare(session, result, now, max_age):
        event_retirement.observe(
            session, result, members, now, max_age, fresh_budget=fresh_subject_budget
        )
        result["frontier"] = frontier
        return result
    accounting_supported = event_accounting.available(conn)
    if accounting_supported:
        try:
            if event_accounting.pages_ready(session, result, members, now, max_age):
                result["accounting"] = event_accounting.observe(
                    session, result, members, now, captured["values"]["max_age_seconds"]
                )
                if (
                    result["accounting"]["supports"]
                    or result["accounting"]["assessment"]["assessment"] == "locally_accounted"
                ):
                    event_retirement.observe(
                        session, result, members, now, max_age, fresh_budget=False
                    )
                    result["frontier"] = frontier
                    return result
        except event_accounting.ERRORS:
            # Accounting metadata must not consume a separate allowance or
            # turn a partial request pass into a completion claim.
            pass
    pending = [member for member in members if member["request_id"] > (cursor or "")]
    result["cursor_assessed"] = True
    result["cursor"] = pending[PAGES - 1]["request_id"] if len(pending) > PAGES else None
    for number, member in enumerate(pending[:PAGES]):
        key, page = member["request_id"], member["request"]
        observed: dict[str, Any] = {"request_id": key, "stages": {}}
        try:
            evidence = session.verify_request(page, classify_unavailability=True)
            observed["gap"] = event_gaps.observation(evidence, result["gap_revision"])
            for stage in ("acquired", "interpreted"):
                previous_rows = session.read(
                    "SELECT enumeration_id,availability,valid_until,token_json,operation_frontier,observed_at "
                    "FROM event_progress_observations WHERE source=? AND source_ref=? AND request_id=? AND stage=?",
                    (source, ref, key, stage),
                    (
                        "enumeration_id",
                        "availability",
                        "valid_until",
                        "token_json",
                        "operation_frontier",
                        "observed_at",
                    ),
                    cap=1,
                )
                previous = previous_rows[0] if previous_rows else None
                operation, reason = None, None
                if evidence[stage] is True:
                    operation, reason = _qualify(
                        session,
                        conn,
                        page=page,
                        stage=stage,
                        previous=previous,
                        fence=fence,
                        now=now,
                    )
                observed["stages"][stage] = {
                    "availability": evidence[stage],
                    "operation": operation,
                    "missing_observed_at": previous["observed_at"] if previous else None,
                    "reasons": {**evidence["reasons"], **({reason: 1} if reason else {})},
                }
        except (BudgetExceeded, ValueError, KeyError, TypeError, RecursionError) as exc:
            observed["stages"] = {
                stage: {
                    "availability": None,
                    "operation": None,
                    "missing_observed_at": None,
                    "reasons": {str(exc): 1},
                }
                for stage in ("acquired", "interpreted")
            }
        if any(value["availability"] is None for value in observed["stages"].values()):
            result["unknown"] += 1
        result["pages"].append(observed)
        if session.exhausted():
            if number or not fresh_subject_budget:
                # Prior work used the allowance: retry this member with a new
                # budget. A first member that exhausts a fresh visit advances
                # as unknown, so one oversized page cannot trap the event.
                result["cursor"] = pending[number - 1]["request_id"] if number else cursor
                result["retry_subject"] = number == 0
            else:
                result["cursor"] = key if key != pending[-1]["request_id"] else None
            break
    result["frontier"] = frontier
    if accounting_supported:
        result["accounting"] = event_accounting.observe(
            session, result, members, now, captured["values"]["max_age_seconds"]
        )
    if result["cursor"] is None:
        event_retirement.observe(session, result, members, now, max_age, fresh_budget=False)
    return result


def refresh(
    database: Database,
    archive: Archive,
    config: Config,
    *,
    now: datetime,
    run_id: str,
    max_events: int = EVENTS,
    wall_seconds: float = 5.0,
    limits: Limits | None = None,
) -> dict[str, Any]:
    """Caller owns H13 artifact admission; all reads share one bounded session."""
    if not 1 <= max_events <= EVENTS or isinstance(max_events, bool):
        raise ValueError("invalid event progress event bound")
    if not math.isfinite(wall_seconds) or wall_seconds < 0 or wall_seconds > 30:
        raise ValueError("invalid event progress wall_seconds")
    if archive.recovery is not None or database.connection.in_transaction:
        raise ValueError("progress refresh requires no recovery and separate transactions")
    summary: dict[str, Any] = {
        "supported": available(database.connection),
        "scanned_events": 0,
        "checked_pages": 0,
        "qualified_progress": 0,
        "discarded_events": 0,
        "unknown_pages": 0,
        "unassessed_events": 0,
        "reason_counts": {},
        "incomplete_observation_coverage": True,
        "parent_support": "bounded_observations"
        if event_accounting.available(database.connection)
        else "unassessed",
        "accounting_transitions": 0,
        "retirement_receipts": 0,
    }
    if not summary["supported"] or wall_seconds == 0:
        return summary
    base_limits = limits or Limits()
    effective_limits = replace(base_limits, seconds=min(base_limits.seconds, wall_seconds))
    captured = policy(config, effective_limits)
    with database.transaction(immediate=False) as conn:
        cursor = conn.execute(
            "SELECT CAST(last_rowid AS INTEGER),CAST(high_water AS INTEGER) FROM event_progress_cursor"
        ).fetchone()
        last, high = cursor
        if last >= high:
            last = 0
            high = conn.execute(
                "SELECT coalesce(max(rowid),0) FROM source_event_inventory"
            ).fetchone()[0]
        subjects = conn.execute(
            "SELECT rowid FROM source_event_inventory WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
            (last, high, max_events),
        ).fetchall()
        frontier = conn.execute(
            "SELECT coalesce(max(operation_id),0) FROM event_stage_operations"
        ).fetchone()[0]
        session = Session(conn, archive, cutoff=now, now=now, limits=effective_limits)
        batches = []
        gap_revisions: dict[str, int | None] = {}
        for number, key in enumerate(subjects):
            if session.exhausted():
                break
            previous_last = last
            last = key[0]
            summary["scanned_events"] += 1
            try:
                loaded = session.read(
                    "SELECT rowid,source,source_ref,enumeration_id FROM source_event_inventory WHERE rowid=?",
                    (key[0],),
                    ("rowid", "source", "source_ref", "enumeration_id"),
                    cap=1,
                )
                batch = _observe(
                    session,
                    conn,
                    loaded[0],
                    captured,
                    now,
                    frontier,
                    fresh_subject_budget=number == 0,
                    gap_revisions=gap_revisions,
                )
                batches.append(batch)
                if batch.get("retry_subject"):
                    last = previous_last
                if batch.get("reason"):
                    raise ValueError(batch["reason"])
            except (BudgetExceeded, ValueError, KeyError, TypeError, RecursionError) as exc:
                if number and session.exhausted():
                    last = previous_last
                summary["unassessed_events"] += 1
                reasons = summary["reason_counts"]
                reasons[str(exc)] = reasons.get(str(exc), 0) + 1
        if not subjects:
            last = high
        summary["budget_exhausted"] = session.exhausted()
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO event_progress_policies VALUES(?,?) ON CONFLICT(digest) DO NOTHING",
            (captured["digest"], _json(captured["values"])),
        )
        for batch in batches:
            source, ref = batch["source"], batch["source_ref"]
            try:
                fresh = _fence(conn, source, ref, captured["digest"]) == batch["token"]
            except ValueError:
                fresh = False
            if not fresh:
                summary["discarded_events"] += 1
                continue
            for page in batch["pages"]:
                summary["checked_pages"] += 1
                for stage, observed in page["stages"].items():
                    if observed["operation"] is not None:
                        receipt = conn.execute(
                            "INSERT INTO event_progress_receipts(source,source_ref,request_id,stage,enumeration_id,operation_id,missing_observed_at,verified_at,token_json) "
                            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source,source_ref,request_id,stage,operation_id) DO NOTHING",
                            (
                                source,
                                ref,
                                page["request_id"],
                                stage,
                                batch["enumeration_id"],
                                observed["operation"],
                                observed["missing_observed_at"],
                                now.isoformat(),
                                _json(batch["token"]),
                            ),
                        )
                        summary["qualified_progress"] += receipt.rowcount
                    conn.execute(
                        "INSERT INTO event_progress_observations VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(source,source_ref,request_id,stage) DO UPDATE SET "
                        "enumeration_id=excluded.enumeration_id,observed_at=excluded.observed_at,valid_until=excluded.valid_until,"
                        "availability=excluded.availability,operation_frontier=excluded.operation_frontier,token_json=excluded.token_json,reasons_json=excluded.reasons_json",
                        (
                            source,
                            ref,
                            page["request_id"],
                            stage,
                            batch["enumeration_id"],
                            now.isoformat(),
                            (
                                now
                                + timedelta(seconds=config.scheduler.event_pressure_max_age_seconds)
                            ).isoformat(),
                            observed["availability"],
                            batch["frontier"],
                            _json(batch["token"]),
                            _json(observed["reasons"]),
                        ),
                    )
            if batch.get("gap_revision") is not None:
                if event_gaps.unchanged(conn, batch):
                    event_gaps.persist(
                        conn,
                        batch,
                        now=now,
                        max_age=config.scheduler.event_pressure_max_age_seconds,
                    )
                elif "accounting" in batch:
                    batch["accounting"]["assessment"] = event_accounting.unknown(
                        "gap_evidence_domain_changed"
                    )
            if event_accounting.available(conn) and ("accounting" in batch or batch.get("reason")):
                summary["accounting_transitions"] += event_accounting.persist(conn, batch, now=now)
            summary["retirement_receipts"] += event_retirement.persist(conn, batch)
            summary["unknown_pages"] += batch["unknown"]
            conn.execute(
                "INSERT INTO event_progress_scans VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(source,source_ref) DO UPDATE SET enumeration_id=excluded.enumeration_id,"
                "after_request_id=CASE WHEN ? OR event_progress_scans.enumeration_id IS NOT excluded.enumeration_id "
                "THEN excluded.after_request_id ELSE event_progress_scans.after_request_id END,"
                "assessment_reason=excluded.assessment_reason,assessed_at=excluded.assessed_at",
                (
                    source,
                    ref,
                    batch["enumeration_id"],
                    batch["cursor"],
                    batch.get("reason"),
                    now.isoformat(),
                    batch["cursor_assessed"],
                ),
            )
        conn.execute("UPDATE event_progress_cursor SET last_rowid=?,high_water=?", (last, high))
    return summary
