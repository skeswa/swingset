"""Read bounded sampled fleet accounting; never open or recover an artifact."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from swingset.admission.enumeration_evidence import members as verified_members
from swingset.admission.page_evidence import Limits, Session
from swingset.config import Config
from swingset.fetch.archive import Archive

from . import event_accounting as accounting
from . import event_gaps, event_retirement
from .event_progress import ENUMERATION_MEMBERS, _fence, policy


def report(
    conn: sqlite3.Connection,
    archive: Archive,
    config: Config | None,
    *,
    now: datetime,
    source: str | None = None,
    source_ref: str | None = None,
    after_event: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Catalog-page counts are subtotals, not global counts when truncated."""
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or type(after_event) is not int
        or not 0 <= after_event < 2**63
    ):
        raise ValueError("invalid accounting report bounds")
    if source_ref is not None and source is None:
        raise ValueError("source_ref requires source")
    result: dict[str, Any] = dict(
        supported=accounting.available(conn),
        events=[],
        counts=dict(locally_accounted=0, unfinished=0, unassessed=0),
        state_counts=dict(locally_accounted=0, waiting=0, explicitly_retired=0, unassessed=0),
        legacy_unenumerated=0,
        coverage_complete=False,
        next_cursor=None,
        scope="catalog_page",
        after_event=after_event,
        limit=limit,
        artifact_checks=False,
        availability_basis="sampled_observation_window",
        incomplete_historical_coverage=True,
        historical_reopening_total=None,
        retirement="unassessed",
        eligible_service_age_seconds=None,
        snapshot_at=now.isoformat(),
    )
    if not result["supported"]:
        result["reason"] = "event_accounting_schema_unavailable"
        return result
    if config is None:
        result["reason"] = "current_policy_unassessed"
        return result
    captured = policy(config, Limits())
    result["policy_digest"] = captured["digest"]
    # Session is used only for bounded SQL and enumeration hashing here. It
    # requires the caller's query-only transaction, as does live inventory.
    session = Session(conn, archive, cutoff=now, now=now, limits=Limits(rows=8192))
    keys = conn.execute(
        "SELECT rowid FROM source_event_inventory WHERE rowid>? AND (? IS NULL OR source=?) AND (? IS NULL OR source_ref=?) ORDER BY rowid LIMIT ?",
        (after_event, source, source, source_ref, source_ref, limit + 1),
    ).fetchall()
    last = after_event
    gap_revisions: dict[str, int | None] = {}
    for number, key in enumerate(keys[:limit]):
        if session.exhausted():
            break
        try:
            subject = session.read(
                "SELECT source,source_ref,enumeration_id FROM source_event_inventory WHERE rowid=?",
                (key[0],),
                ("source", "source_ref", "enumeration_id"),
                cap=1,
            )[0]
        except accounting.ERRORS as exc:
            # A corrupt first subject cannot trap catalog pagination forever.
            if number == 0:
                result["events"].append(dict(catalog_rowid=key[0], **accounting.unknown(str(exc))))
                result["counts"]["unassessed"] += 1
                result["state_counts"]["unassessed"] += 1
                last = key[0]
            break
        item = {**subject, "catalog_rowid": key[0], **accounting.unknown("enumeration_unassessed")}
        try:
            if subject["enumeration_id"] is None:
                result["legacy_unenumerated"] += 1
            else:
                members = verified_members(
                    session,
                    enumeration_id=subject["enumeration_id"],
                    source=subject["source"],
                    source_ref=subject["source_ref"],
                    limit=ENUMERATION_MEMBERS,
                )
                stored = session.read(
                    "SELECT policy_json FROM event_progress_policies WHERE digest=?",
                    (captured["digest"],),
                    ("policy_json",),
                    cap=1,
                )
                if not stored or json.loads(stored[0]["policy_json"]) != captured["values"]:
                    raise ValueError("current_policy_unassessed")
                fence = _fence(conn, subject["source"], subject["source_ref"], captured["digest"])
                if fence["revision"] is None:
                    raise ValueError("invalidation_tracking_unassessed")
                batch = {
                    **subject,
                    "token": fence,
                    "pages": [],
                    "gap_revision": event_gaps.revision(session, subject["source"], gap_revisions),
                }
                value = accounting.observe(
                    session,
                    batch,
                    members,
                    now,
                    config.scheduler.event_pressure_max_age_seconds,
                    verify_parents=False,
                )
                item.update(value["assessment"])
                item["page_retirement"] = event_retirement.report(
                    session,
                    source=subject["source"],
                    source_ref=subject["source_ref"],
                    fence=fence,
                    now=now,
                    max_age=config.scheduler.event_pressure_max_age_seconds,
                )
            history = session.read(
                "SELECT receipt_id,enumeration_id,assessment,transition,observed_at FROM event_accounting_receipts WHERE source=? AND source_ref=? ORDER BY receipt_id DESC LIMIT 1",
                (subject["source"], subject["source_ref"]),
                ("receipt_id", "enumeration_id", "assessment", "transition", "observed_at"),
                cap=1,
            )
            item["last_observed_transition"] = history[0] if history else None
            definite = session.read(
                "SELECT receipt_id,enumeration_id,assessment,transition,observed_at FROM event_accounting_receipts WHERE source=? AND source_ref=? AND assessment!='unassessed' ORDER BY receipt_id DESC LIMIT 1",
                (subject["source"], subject["source_ref"]),
                ("receipt_id", "enumeration_id", "assessment", "transition", "observed_at"),
                cap=1,
            )
            item["last_definite_assessment"] = definite[0] if definite else None
            item["recorded_history"] = {
                "source": subject["source"],
                "source_ref": subject["source_ref"],
                "streams": [
                    "accounting",
                    "enumerations",
                    "page_retirement",
                    "source_event_retirement",
                ],
                "pagination": "event_history.report; pin through from the first page",
                "complete_lifetime_history": False,
            }
            item["historical_reopening_total"] = None
            item["historical_count_assessment"] = "unassessed; latest transition only"
        except accounting.ERRORS as exc:
            item.update(accounting.unknown(str(exc)))
        item["current_state"] = (
            "explicitly_retired"
            if item.get("page_retirement", {}).get("whole_event_retired") is True
            else {
                "locally_accounted": "locally_accounted",
                "unfinished": "waiting",
                "unassessed": "unassessed",
            }[item["assessment"]]
        )
        result["events"].append(item)
        result["counts"][item["assessment"]] += 1
        result["state_counts"][item["current_state"]] += 1
        last = key[0]
        if session.exhausted():
            break
    result["coverage_complete"] = (
        len(keys) <= limit and len(result["events"]) == len(keys) and not session.exhausted()
    )
    if not result["coverage_complete"]:
        result["next_cursor"] = last
    result["budget_exhausted"] = session.exhausted()
    return result
