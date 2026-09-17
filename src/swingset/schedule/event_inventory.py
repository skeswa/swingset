"""Bounded local event evidence using the same request verifier as releases."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from typing import Any

from swingset.admission.enumeration_evidence import members as verified_members
from swingset.admission.evidence_budget import BudgetExceeded
from swingset.admission.page_evidence import FORMAT, Limits, Session
from swingset.admission.parent_evidence import parent_support
from swingset.fetch.archive import Archive

from .event_evidence import request, timestamp
from .event_gaps import accounted

_DEFAULT_LIMITS = Limits()
ENUMERATION_MEMBERS = 128


class _Evidence:
    def __init__(self, session: Session, source: str):
        self.session, self.source = session, source

    def parent(self, identifier: str) -> dict[str, Any]:
        return parent_support(self.session, source=self.source, generation_id=identifier)

    def page(self, member: dict[str, Any]) -> dict[str, Any]:
        identity = member["request"]
        declared = sorted(
            {spec["watch_id"] for claim in member["support"] for spec in claim["watches"]}
        )
        proof = self.session.verify_request(identity, classify_unavailability=True)
        acquired, interpreted = proof["acquired"], proof["interpreted"]
        unavailable = proof["unavailable"]
        acquisition, interpretation = proof["acquisition_support"], proof["interpretation_support"]
        supported = ([acquisition] if acquisition else []) + (
            interpretation["snapshots"] if interpretation else []
        )
        if proof["unavailability_support"] is not None:
            supported.append(proof["unavailability_support"])
        supported = [
            row
            for row in supported
            if request(identity["source"], row["method"], row["url"], row["form"]) == identity
        ]
        evidence_watches = {row["watch_id"] for row in supported}
        blockers = set(proof["reasons"])
        diagnostics_assessed = True
        diagnostic_reason = None
        latest = None
        latest_at = None
        try:
            # Declaration watches are useful diagnostics, not the complete
            # request evidence universe. Bound this secondary work too. Apply
            # the aware cutoff after parsing: SQLite accepts naive/invalid
            # calendar values that cannot certify a latest response here.
            candidates = self.session.read(
                "SELECT snapshot_id,watch_id,method,url,form,fetched_at,classification FROM snapshots WHERE watch_id IN (SELECT value FROM json_each(?)) ORDER BY julianday(fetched_at) DESC,snapshot_id DESC",
                (json.dumps(sorted(set(declared) | evidence_watches)),),
                (
                    "snapshot_id",
                    "watch_id",
                    "method",
                    "url",
                    "form",
                    "fetched_at",
                    "classification",
                ),
                cap=min(self.session.limits.candidates, self.session.limits.rows),
            )
            for candidate in candidates:
                if (
                    request(
                        identity["source"], candidate["method"], candidate["url"], candidate["form"]
                    )
                    != identity
                ):
                    continue
                fetched_at = timestamp(candidate["fetched_at"])
                if fetched_at is None:
                    diagnostics_assessed = False
                    diagnostic_reason = "response_time_unassessed"
                else:
                    at = datetime.fromisoformat(fetched_at)
                    if at <= self.session.cutoff and (latest_at is None or at > latest_at):
                        latest, latest_at = candidate, at
        except (BudgetExceeded, ValueError, TypeError, KeyError, RecursionError) as exc:
            diagnostics_assessed = False
            diagnostic_reason = (
                str(exc) if isinstance(exc, BudgetExceeded) else "response_metadata_unassessed"
            )
        if acquired is None or interpreted is None:
            blockers.add("page_evidence_unassessed")
        elif not acquired:
            blockers.add("not_acquired")
        elif not interpreted:
            blockers.add("interpretation_unavailable")
        if unavailable is None:
            blockers.add("source_unavailability_unassessed")
        elif unavailable:
            blockers.add("retained_origin_unavailable")
        if not declared and not evidence_watches:
            blockers.add("unsupported_page_kind")
        if (
            diagnostics_assessed
            and latest
            and latest["classification"] not in {"Ok", "NotModified"}
        ):
            blockers.add("latest_response_" + latest["classification"])
        return {
            "request_id": member["request_id"],
            "request": identity,
            "watch_ids": sorted(set(declared) | evidence_watches),
            "declared_watch_ids": declared,
            "evidence_watch_ids": sorted(evidence_watches),
            "first_known_at": member["first_known_at"],
            "acquired": acquired,
            "interpreted": interpreted,
            "unavailable": unavailable,
            "unavailability_support": proof["unavailability_support"],
            "accounted_for": accounted(interpreted, unavailable),
            "snapshot_ids": sorted({row["snapshot_id"] for row in supported}),
            "generation_ids": [interpretation["generation_id"]] if interpretation else [],
            "blockers": sorted(blockers),
            "latest_response_assessed": diagnostics_assessed,
            "latest_response_reason": diagnostic_reason,
            "latest_response_basis": "bounded_declared_and_verified_request_watches",
            "next_action": "verify_retained_evidence"
            if acquired is None or interpreted is None or unavailable is None
            else "await_selected_release"
            if interpreted
            else "review_source_unavailability"
            if unavailable
            else "unsupported_review"
            if not declared and not evidence_watches
            else "acquire_or_restore"
            if not acquired
            else "interpret_or_restore",
        }


def inventory(
    conn: sqlite3.Connection,
    archive: Archive,
    *,
    source: str,
    source_ref: str,
    now: datetime,
    limits: Limits = _DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Use one caller-owned query-only snapshot and one finite evidence budget.

    No files are recovered and no verdict is persisted. Unknown is distinct
    from missing; declaration watch IDs never constrain the evidence search.
    """
    at = timestamp(now.isoformat())
    if at is None:
        raise ValueError("inventory requires an aware snapshot time")
    if archive.recovery is not None:
        raise ValueError("inventory requires a read-only Archive without recovery")
    if (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_event_inventory'").fetchone()
        is None
    ):
        return {
            "supported": False,
            "reason": "event_inventory_schema_unavailable",
            "source": source,
            "source_ref": source_ref,
        }
    session = Session(conn, archive, cutoff=now, now=now, limits=limits)
    result: dict[str, Any] = {
        "supported": True,
        "source": source,
        "source_ref": source_ref,
        "canonical_event_id": None,
        "enumeration_id": None,
        "first_known_at": None,
        "verification_basis": "bounded_current_read_snapshot_and_digest_verified_local_artifacts",
        "verifier_format": FORMAT,
        "limits": asdict(limits),
        "enumeration_member_limit": ENUMERATION_MEMBERS,
        "verified_at": at,
        "wall_age_seconds": None,
        "published_pages": None,
        "eligible_service_age_seconds": None,
        "last_successful_progress_at": None,
        "pagination": "unknown",
        "listed_pages": None,
        "acquired_pages": None,
        "interpreted_pages": None,
        "acquisition_unknown_pages": None,
        "interpretation_unknown_pages": None,
        "unavailable_pages": None,
        "unavailability_unknown_pages": None,
        "unavailability_basis": "verified_retained_origin_response_without_usable_acquisition",
        "unsupported_pages": None,
        "parent_support": [],
        "members": [],
        "blockers": [],
        "known_pages_accounted_for": None,
        "member_records_complete": False,
        "stage_assessment_complete": False,
    }
    try:
        entries = session.read(
            "SELECT enumeration_id,first_known_at FROM source_event_inventory WHERE source=? AND source_ref=?",
            (source, source_ref),
            ("enumeration_id", "first_known_at"),
            cap=1,
        )
        if not entries:
            return {**result, "reason": "source_event_not_inventoried"}
        entry = entries[0]
        result.update(
            enumeration_id=entry["enumeration_id"], first_known_at=entry["first_known_at"]
        )
        first = timestamp(entry["first_known_at"]) if entry["first_known_at"] else None
        if first:
            result["wall_age_seconds"] = max(
                0, (now - datetime.fromisoformat(first)).total_seconds()
            )
        mapping = session.read(
            "SELECT event_id FROM source_event_map WHERE source=? AND source_ref=?",
            (source, source_ref),
            ("event_id",),
            cap=1,
        )
        result["canonical_event_id"] = mapping[0]["event_id"] if mapping else None
        if entry["enumeration_id"] is None:
            return {
                **result,
                "pagination_reason": "legacy_parent_not_admitted",
                "blockers": ["legacy_unassessed"],
            }
        members = verified_members(
            session,
            enumeration_id=entry["enumeration_id"],
            source=source,
            source_ref=source_ref,
            limit=ENUMERATION_MEMBERS,
        )
        headers = session.read(
            "SELECT predecessor_id,membership_digest,pagination,pagination_reason,added_json,removed_json,parent_support_json FROM source_event_enumerations WHERE enumeration_id=?",
            (entry["enumeration_id"],),
            (
                "predecessor_id",
                "membership_digest",
                "pagination",
                "pagination_reason",
                "added_json",
                "removed_json",
                "parent_support_json",
            ),
            cap=1,
        )
        header = headers[0]
        parent_ids = json.loads(header["parent_support_json"])
        if not isinstance(parent_ids, dict) or len(parent_ids) > limits.candidates:
            raise BudgetExceeded("parent_candidate_budget")
        result.update(
            predecessor_id=header["predecessor_id"],
            membership_digest=header["membership_digest"],
            pagination=header["pagination"],
            pagination_reason=header["pagination_reason"],
            added_request_ids=json.loads(header["added_json"]),
            removed_request_ids=json.loads(header["removed_json"]),
            listed_pages=len(members),
        )
        evidence = _Evidence(session, source)
        result["parent_support"] = [evidence.parent(identifier) for identifier in parent_ids]
        result["members"] = [evidence.page(member) for member in members]
        result["member_records_complete"] = True
    except (BudgetExceeded, ValueError, KeyError, TypeError, RecursionError) as exc:
        result["blockers"] = [
            str(exc) if isinstance(exc, BudgetExceeded) else "event_enumeration_evidence_invalid"
        ]
        result["acquisition_unknown_pages"] = result["interpretation_unknown_pages"] = result[
            "listed_pages"
        ]
        result["unavailability_unknown_pages"] = result["listed_pages"]
        return result
    blockers = {
        reason
        for parent in result["parent_support"]
        for reason in [parent["reason"], *parent.get("reasons", [])]
        if reason
    }
    blockers.update(reason for page in result["members"] for reason in page["blockers"])
    if result["canonical_event_id"] is None:
        blockers.add("canonical_mapping_unresolved")
    if not members:
        blockers.add("no_known_page_obligations")
    for stage, field in (
        ("acquired", "acquisition"),
        ("interpreted", "interpretation"),
        ("unavailable", "unavailability"),
    ):
        values = [page[stage] for page in result["members"]]
        unknown = sum(value is None for value in values)
        result[field + "_unknown_pages"] = unknown
        result[stage + "_pages"] = None if unknown else sum(value is True for value in values)
    result["stage_assessment_complete"] = all(
        page[stage] is not None
        for page in result["members"]
        for stage in ("acquired", "interpreted")
    )
    required = [parent["usable"] for parent in result["parent_support"]]
    required.extend(page["accounted_for"] for page in result["members"])
    result["known_pages_accounted_for"] = (
        False
        if not members or not parent_ids or any(value is False for value in required)
        else None
        if any(value is None for value in required)
        else True
    )
    result["blockers"] = sorted(blockers)
    return result
