"""Read successful-operation receipts separately from sampled request progress."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from hashlib import sha256
from typing import Any

from swingset.config import Config
from swingset.state.event_progress import available

from .event_accounting import available as accounting_available
from .event_pressure import token


def _observation(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    source: str,
    source_ref: str,
    now: datetime,
    config: Config | None,
    membership_assessed: bool,
) -> dict[str, Any]:
    result = dict(row)
    raw_token, raw_reasons = result.pop("token_json"), result.pop("reasons_json")
    result.update(
        fresh=False,
        policy_digest=None,
        current_default_policy_matches=None,
        availability_when_observed=result.pop("availability"),
        reasons={},
        observation_assessment="recorded",
    )
    reason = "observation_token_invalid"
    try:
        old_token = json.loads(raw_token)
        if not isinstance(old_token, dict) or not isinstance(old_token.get("policy_digest"), str):
            raise ValueError(reason)
        current = token(conn, source, source_ref, old_token["policy_digest"])
        if (
            old_token.keys() != current.keys()
            or type(old_token["epoch"]) is not int
            or (old_token["revision"] is not None and type(old_token["revision"]) is not int)
            or any(
                old_token[key] is not None and not isinstance(old_token[key], str)
                for key in ("enumeration_id", "input_bundle_hash")
            )
        ):
            raise ValueError(reason)
        result["policy_digest"] = old_token["policy_digest"]
        reason = "observation_reasons_invalid"
        reasons = json.loads(raw_reasons)
        if not isinstance(reasons, dict):
            raise ValueError(reason)
        result["reasons"] = reasons
        reason = "observation_policy_missing"
        captured = conn.execute(
            "SELECT policy_json FROM event_progress_policies WHERE digest=?",
            (old_token["policy_digest"],),
        ).fetchone()
        if captured is None:
            raise ValueError(reason)
        reason = "observation_policy_invalid"
        values = json.loads(captured[0])
        required = {
            "format",
            "verifier_format",
            "limits",
            "events",
            "members_per_event",
            "operation_candidates",
            "enumeration_members",
            "max_age_seconds",
            "acceptance",
        }
        if (
            not isinstance(values, dict)
            or values.keys() != required
            or not isinstance(values.get("limits"), dict)
        ):
            raise ValueError(reason)
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        if sha256(encoded).hexdigest() != old_token["policy_digest"]:
            raise ValueError(reason)
        if config is not None:
            from swingset.admission.page_evidence import Limits

            from .event_progress import policy

            result["current_default_policy_matches"] = (
                policy(config, Limits())["digest"] == old_token["policy_digest"]
            )
        reason = "observation_time_invalid"
        result["fresh"] = (
            current == old_token
            and datetime.fromisoformat(result["valid_until"]) > now
            and result["current_default_policy_matches"] is True
            and membership_assessed
        )
    except (ValueError, KeyError, TypeError, RecursionError):
        result.update(
            fresh=False, availability_when_observed=None, observation_assessment="unassessed"
        )
        result["reasons"][reason] = 1
    return result


def report(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_ref: str,
    now: datetime,
    limit: int = 20,
    config: Config | None = None,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("event progress report limit must be between 1 and 100")
    result: dict[str, Any] = {
        "supported": available(conn),
        "historical_success_times": "unknown_before_recording",
        "incomplete_observation_coverage": True,
        "eligible_service_age_seconds": None,
        "parent_support": "separate_accounting_observations"
        if accounting_available(conn)
        else "unassessed_by_progress_observer",
        "current_stage_authority": False,
        "last_observed_progress_at": None,
        "latest_qualified_operation_at": None,
        "last_recorded_operation_at": None,
        "observations": [],
        "progress": [],
        "operations": [],
        "observation_limit": limit,
    }
    if not result["supported"]:
        return result
    result["progress"] = [
        dict(row)
        for row in conn.execute(
            "SELECT p.*,o.occurred_at,o.snapshot_id,o.generation_id,o.decision_id FROM event_progress_receipts p "
            "JOIN event_stage_operations o USING(operation_id) WHERE p.source=? AND p.source_ref=? "
            "ORDER BY julianday(o.occurred_at) DESC,p.receipt_id DESC LIMIT ?",
            (source, source_ref, limit),
        )
    ]
    result["operations"] = [
        dict(row)
        for row in conn.execute(
            "SELECT o.* FROM event_stage_operations o WHERE o.source=? AND ("
            "EXISTS(SELECT 1 FROM watches w WHERE w.watch_id=o.watch_id AND w.source=? AND w.source_ref=?) OR "
            "EXISTS(SELECT 1 FROM source_event_inventory i JOIN source_event_member_watches m USING(enumeration_id) "
            "WHERE i.source=? AND i.source_ref=? AND m.watch_id=o.watch_id) OR "
            "EXISTS(SELECT 1 FROM event_progress_receipts p WHERE p.operation_id=o.operation_id AND p.source=? AND p.source_ref=?)) "
            "ORDER BY o.operation_id DESC LIMIT ?",
            (source, source, source_ref, source, source_ref, source, source_ref, limit),
        )
    ]
    result["operation_scope"] = (
        "direct and admitted member watches; other aggregate support only after qualification"
    )
    if result["progress"]:
        result["latest_qualified_operation_at"] = result["progress"][0]["occurred_at"]
        result["last_observed_progress_at"] = conn.execute(
            "SELECT verified_at FROM event_progress_receipts WHERE source=? AND source_ref=? "
            "ORDER BY julianday(verified_at) DESC,receipt_id DESC LIMIT 1",
            (source, source_ref),
        ).fetchone()[0]
    if result["operations"]:
        result["last_recorded_operation_at"] = result["operations"][0]["occurred_at"]
    assessment = conn.execute(
        "SELECT assessment_reason,assessed_at FROM event_progress_scans WHERE source=? AND source_ref=?",
        (source, source_ref),
    ).fetchone()
    result["last_membership_assessment"] = dict(assessment) if assessment else None
    for row in conn.execute(
        "SELECT p.*,EXISTS(SELECT 1 FROM source_event_inventory i JOIN source_event_enumeration_members m USING(enumeration_id) "
        "WHERE i.source=p.source AND i.source_ref=p.source_ref AND m.request_id=p.request_id) AS currently_listed "
        "FROM event_progress_observations p WHERE source=? AND source_ref=? ORDER BY request_id,stage LIMIT ?",
        (source, source_ref, limit),
    ):
        result["observations"].append(
            _observation(
                conn,
                row,
                source=source,
                source_ref=source_ref,
                now=now,
                config=config,
                membership_assessed=assessment is not None
                and assessment["assessment_reason"] is None,
            )
        )
    return result
