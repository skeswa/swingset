"""Verify admitted parent support with the caller's shared evidence budget."""

from __future__ import annotations

import json
from typing import Any

from swingset.schedule.event_evidence import request

from .evidence_budget import BudgetExceeded
from .page_evidence import Session


def parent_support(session: Session, *, source: str, generation_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "generation_id": generation_id,
        "snapshot_ids": [],
        "usable": None,
        "reason": None,
    }
    try:
        rows = session.read(
            "SELECT recipe_json FROM source_generations WHERE generation_id=?",
            (generation_id,),
            ("recipe_json",),
            cap=1,
        )
        if not rows:
            return {**result, "usable": False, "reason": "source_generation_missing"}
        context = json.loads(rows[0]["recipe_json"])["context"]
        if context["source"] != source:
            return {**result, "usable": False, "reason": "parent_source_mismatch"}
        snapshots = session.read(
            "SELECT method,url,form FROM snapshots WHERE snapshot_id=?",
            (context["snapshot_id"],),
            ("method", "url", "form"),
            cap=1,
        )
        if not snapshots:
            return {**result, "usable": False, "reason": "parent_snapshot_missing"}
        decisions = session.read(
            "SELECT decision_id FROM admission_decisions WHERE generation_id=? AND state='accepted' AND (julianday(decided_at)<=julianday(?) OR julianday(decided_at) IS NULL) ORDER BY decision_id LIMIT 1",
            (generation_id, session.cutoff.isoformat()),
            ("decision_id",),
            cap=1,
        )
        if not decisions:
            return {**result, "usable": False, "reason": "source_generation_unadmitted"}
        snapshot = snapshots[0]
        identity = request(context["source"], snapshot["method"], snapshot["url"], snapshot["form"])
        proof = session.verify_operation(
            identity, generation_id=generation_id, decision_id=decisions[0]["decision_id"]
        )
        usable = proof["interpreted"]
        support = proof["interpretation_support"]
        reasons = sorted(proof["reasons"])
        return {
            **result,
            "usable": usable,
            "snapshot_ids": [row["snapshot_id"] for row in support["snapshots"]] if support else [],
            "reason": None
            if usable
            else reasons[0]
            if reasons
            else "parent_interpretation_unavailable",
            "reasons": reasons,
        }
    except BudgetExceeded as exc:
        return {**result, "reason": str(exc)}
    except (ValueError, KeyError, TypeError, RecursionError):
        return {**result, "usable": False, "reason": "generation_evidence_invalid"}
