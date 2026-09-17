"""Persist bounded observations of one explicit page-withdrawal edge."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from swingset.admission.event_retirement import FORMAT, verify_edge
from swingset.admission.page_evidence import Session
from swingset.fetch.archive import canonical, digest

from .event_accounting import ERRORS, encoded, fresh


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='event_retirement_observations'"
        ).fetchone()
        is not None
    )


def unknown(reason: str) -> dict[str, Any]:
    return dict(
        scope="current_immediate_predecessor_edge",
        assessment="unassessed",
        reason=reason,
        whole_event_retired=None,
        complete_retirement_history=False,
        verified_retirement_ids=None,
        verified_retirement_count=None,
        all_predecessor_obligations_retired=None,
    )


def prepare(session: Session, batch: dict[str, Any], now: datetime, max_age: float) -> bool:
    """Return whether an interrupted edge deserves this visit's first allowance."""
    if not available(session.conn):
        batch["_retirement_due"] = False
        return False
    try:
        rows = session.read(
            "SELECT enumeration_id,observed_at,valid_until,token_json,retry_fresh FROM event_retirement_observations WHERE source=? AND source_ref=?",
            (batch["source"], batch["source_ref"]),
            ("enumeration_id", "observed_at", "valid_until", "token_json", "retry_fresh"),
            cap=1,
        )
        old = rows[0] if rows else None
        same = old is not None and old["enumeration_id"] == batch["enumeration_id"]
        priority = same and old is not None and old["retry_fresh"] == 1
        batch["_retirement_due"] = not (
            old and fresh(old, batch["token"], now, max_age) and not priority
        )
        return bool(priority)
    except ERRORS:
        batch["_retirement_due"] = True
        return False


def observe(
    session: Session,
    batch: dict[str, Any],
    members: list[dict[str, Any]],
    now: datetime,
    max_age: float,
    *,
    fresh_budget: bool,
) -> None:
    if not batch.get("_retirement_due"):
        return
    value = verify_edge(
        session,
        source=batch["source"],
        source_ref=batch["source_ref"],
        successor_id=batch["enumeration_id"],
        successor_members=members,
    )
    retry = session.exhausted() and not fresh_budget
    batch["retirement"] = dict(
        value=value,
        observed_at=now.isoformat(),
        valid_until=(now + timedelta(seconds=max_age)).isoformat(),
        retry_fresh=retry,
    )
    # Cursor retry is handled by the existing outer event rotation. An edge
    # exhausting a fresh first-event budget is recorded unknown and yields.
    if retry and fresh_budget is False and not batch["pages"]:
        batch["retry_subject"] = True


def persist(conn: sqlite3.Connection, batch: dict[str, Any]) -> int:
    saved = batch.get("retirement")
    if saved is None:
        return 0
    value = saved["value"]
    conn.execute(
        "INSERT INTO event_retirement_observations VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,source_ref) DO UPDATE SET enumeration_id=excluded.enumeration_id,predecessor_id=excluded.predecessor_id,assessment=excluded.assessment,observed_at=excluded.observed_at,valid_until=excluded.valid_until,token_json=excluded.token_json,result_json=excluded.result_json,retry_fresh=excluded.retry_fresh",
        (
            batch["source"],
            batch["source_ref"],
            batch["enumeration_id"],
            value["predecessor_id"],
            value["assessment"],
            saved["observed_at"],
            saved["valid_until"],
            encoded(batch["token"]),
            encoded(value),
            saved["retry_fresh"],
        ),
    )
    if value["assessment"] != "verified":
        return 0
    proof = value["proof"]
    written = conn.execute(
        "INSERT INTO event_retirement_receipts(proof_digest,source,source_ref,enumeration_id,predecessor_id,generation_id,decision_id,observed_at,policy_digest,proof_json) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(proof_digest) DO NOTHING",
        (
            value["proof_digest"],
            batch["source"],
            batch["source_ref"],
            batch["enumeration_id"],
            value["predecessor_id"],
            proof["replacement"]["generation_id"],
            proof["replacement"]["decision_id"],
            saved["observed_at"],
            batch["token"]["policy_digest"],
            encoded(proof),
        ),
    )
    return written.rowcount


def report(
    session: Session,
    *,
    source: str,
    source_ref: str,
    fence: dict[str, Any],
    now: datetime,
    max_age: float,
) -> dict[str, Any]:
    result = unknown("retirement_observation_missing")
    if not available(session.conn):
        return unknown("retirement_schema_unavailable")
    try:
        history = session.read(
            "SELECT receipt_id,proof_digest,enumeration_id,predecessor_id,generation_id,decision_id,observed_at FROM event_retirement_receipts WHERE source=? AND source_ref=? ORDER BY receipt_id DESC LIMIT 1",
            (source, source_ref),
            (
                "receipt_id",
                "proof_digest",
                "enumeration_id",
                "predecessor_id",
                "generation_id",
                "decision_id",
                "observed_at",
            ),
            cap=1,
        )
        result["latest_historical_receipt"] = history[0] if history else None
        rows = session.read(
            "SELECT enumeration_id,observed_at,valid_until,token_json,result_json FROM event_retirement_observations WHERE source=? AND source_ref=?",
            (source, source_ref),
            ("enumeration_id", "observed_at", "valid_until", "token_json", "result_json"),
            cap=1,
        )
        if not rows:
            return result
        row = rows[0]
        if not fresh(row, fence, now, max_age):
            return {**result, "reason": "retirement_observation_stale_or_invalid"}
        value = json.loads(row["result_json"])
        if (
            not isinstance(value, dict)
            or value.get("format") != FORMAT
            or value.get("successor_id") != fence["enumeration_id"]
        ):
            raise ValueError("retirement_observation_invalid")
        assessment = value["assessment"]
        if assessment not in {"verified", "not_applicable", "unassessed"}:
            raise ValueError("retirement_assessment_invalid")
        ids = value["verified_retirement_ids"]
        if assessment == "verified":
            proof = value["proof"]
            if (
                not isinstance(ids, list)
                or not ids
                or len(ids) > 128
                or ids != sorted(set(ids))
                or proof["retired_requests"] != ids
                or proof["successor_id"] != fence["enumeration_id"]
                or proof["predecessor_id"] != value["predecessor_id"]
                or (proof["source"], proof["source_ref"]) != (source, source_ref)
            ):
                raise ValueError("retirement_proof_invalid")
            if any(
                type(proof[k]) is not int or not 0 <= proof[k] <= 128
                for k in ("predecessor_request_count", "successor_request_count")
            ) or proof["predecessor_request_count"] < len(ids):
                raise ValueError("retirement_denominator_invalid")
            fingerprint = digest(canonical(proof))
            receipts = session.read(
                "SELECT proof_json FROM event_retirement_receipts WHERE proof_digest=?",
                (fingerprint,),
                ("proof_json",),
                cap=1,
            )
            if (
                not receipts
                or json.loads(receipts[0]["proof_json"]) != proof
                or fingerprint != value["proof_digest"]
            ):
                raise ValueError("retirement_receipt_missing_or_invalid")
        elif assessment == "not_applicable" and ids != []:
            raise ValueError("retirement_empty_edge_invalid")
        elif assessment == "unassessed" and ids is not None:
            raise ValueError("retirement_unknown_ids_invalid")
        return {
            **result,
            "assessment": assessment,
            "reason": value.get("reason"),
            "predecessor_id": value["predecessor_id"],
            "successor_id": value["successor_id"],
            "verified_retirement_ids": ids,
            "verified_retirement_count": len(ids)
            if assessment in {"verified", "not_applicable"}
            else None,
            "all_predecessor_obligations_retired": (
                proof["predecessor_request_count"] == len(ids)
                and proof["successor_request_count"] == 0
            )
            if assessment == "verified"
            else None,
            "proof_digest": value.get("proof_digest") if assessment == "verified" else None,
            "observed_at": row["observed_at"],
            "valid_until": row["valid_until"],
            "whole_event_retired": None,
            "complete_retirement_history": False,
        }
    except ERRORS as exc:
        return {**result, "reason": str(exc)}
