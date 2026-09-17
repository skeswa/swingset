"""Bounded, read-only evidence checks for conservative scheduling observations.

The caller owns the SQLite snapshot and observation fences. Exhaustion is
unknown, never completion. No SQL/filesystem hard deadline is claimed.
"""

from __future__ import annotations

import json
import sqlite3
import time as time
from datetime import datetime
from typing import Any

from swingset.admission.evidence_budget import Budget, _Unknown
from swingset.admission.evidence_budget import Limits as Limits
from swingset.fetch.archive import Archive

from .event_evidence import admission_reason, decode_generation, request, request_id, timestamp

FORMAT = "event-pressure-probe-v1"
_DEFAULT_LIMITS = Limits()


class _Probe(Budget):
    def __init__(self, conn: sqlite3.Connection, archive: Archive, limits: Limits):
        super().__init__(conn, archive, limits)
        self.generations: dict[str, tuple[bool, set[str]]] = {}
        self.unsupported: set[str] = set()

    def interpreted(self, identifier: str) -> tuple[bool, set[str]]:
        if identifier in self.generations:
            return self.generations[identifier]
        columns = (
            "generation_id",
            "unit_key",
            "input_fingerprint",
            "page_kind",
            "contract_version",
            "manifest_json",
            "recipe_json",
            "report_json",
            "result_json",
            "previous_generation_id",
            "work_token",
            "removal_authority",
            "state",
        )
        rows = self.read(
            "SELECT * FROM source_generations WHERE generation_id=?", (identifier,), columns, cap=1
        )
        if not rows:
            self.reasons["source_generation_missing"] += 1
            return False, set()
        try:
            value = decode_generation(rows[0], identifier)
            manifest = value["manifest"]
            if not isinstance(manifest, list):
                raise ValueError("invalid manifest")
            if len(manifest) > self.limits.manifest_members:
                raise _Unknown("manifest_member_budget")
            revoked = self.read(
                "SELECT unit_key,input_fingerprint,recipe_json FROM source_generations WHERE state='revoked' ORDER BY generation_id",
                (),
                ("unit_key", "input_fingerprint", "recipe_json"),
                cap=self.limits.candidates,
            )
            reason = admission_reason(
                self.conn,
                value,
                policy_version=self.policy_version,
                revoked_rows=[
                    (r["unit_key"], r["input_fingerprint"], r["recipe_json"]) for r in revoked
                ],
            )
            snapshots: set[str] = set()
            for member in manifest:
                self.tick()
                rows = self.read(
                    "SELECT watch_id,url,body_sha256,snapshot_id FROM snapshots WHERE snapshot_id=?",
                    (member["snapshot_id"],),
                    ("watch_id", "url", "body_sha256", "snapshot_id"),
                    cap=1,
                )
                if not rows or any(
                    rows[0][key] != member[key] for key in ("watch_id", "url", "body_sha256")
                ):
                    reason = reason or "snapshot_evidence_changed"
                snapshots.add(member["snapshot_id"])
                if not self.artifact("body", member["body_sha256"]):
                    reason = reason or "body_artifact_unavailable"
                if not self.artifact("extract", member["extract_sha256"]):
                    reason = reason or "extract_artifact_unavailable"
            if not snapshots:
                reason = reason or "manifest_evidence_missing"
            if reason == "source_interpretation_unsupported":
                self.unsupported.add(identifier)
            if reason:
                self.reasons[reason] += 1
            result = reason is None, snapshots
        except (ValueError, KeyError, TypeError, RecursionError):
            self.reasons["generation_evidence_invalid"] += 1
            result = False, set()
        self.generations[identifier] = result
        return result

    def page(self, row: dict[str, Any]) -> tuple[bool, bool, bool]:
        page = json.loads(row["request_json"])
        if request_id(page) != row["request_id"]:
            raise ValueError("request identity changed")
        watches = self.read(
            "SELECT watch_id FROM source_event_member_watches WHERE enumeration_id=? AND request_id=? ORDER BY watch_id",
            (row["enumeration_id"], row["request_id"]),
            ("watch_id",),
            cap=self.limits.candidates,
        )
        if not watches:
            self.reasons["unsupported_page_kind"] += 1
            return False, False, True
        acquired: set[str] = set()
        for watch in watches:
            snapshots = self.read(
                "SELECT snapshot_id,method,url,form,classification,body_sha256 FROM snapshots WHERE watch_id=? ORDER BY fetched_at DESC,snapshot_id DESC",
                (watch["watch_id"],),
                ("snapshot_id", "method", "url", "form", "classification", "body_sha256"),
                cap=self.limits.candidates,
            )
            for snapshot in snapshots:
                if snapshot["classification"] not in {"Ok", "NotModified"}:
                    continue
                if (
                    request_id(
                        request(
                            page["source"], snapshot["method"], snapshot["url"], snapshot["form"]
                        )
                    )
                    != row["request_id"]
                ):
                    continue
                if self.artifact("body", snapshot["body_sha256"]):
                    acquired.add(snapshot["snapshot_id"])
        if not acquired:
            self.reasons["not_acquired"] += 1
            return False, False, False
        unsupported = False
        for watch in watches:
            candidates = self.read(
                "SELECT g.generation_id FROM source_units u JOIN source_generations g USING(unit_key) WHERE u.watch_id=? AND EXISTS(SELECT 1 FROM admission_decisions d WHERE d.generation_id=g.generation_id AND d.state='accepted') ORDER BY g.created_at DESC,g.generation_id DESC",
                (watch["watch_id"],),
                ("generation_id",),
                cap=self.limits.candidates,
            )
            for candidate in candidates:
                usable, snapshots_used = self.interpreted(candidate["generation_id"])
                unsupported = unsupported or candidate["generation_id"] in self.unsupported
                if usable and acquired.intersection(snapshots_used):
                    return True, True, False
        self.reasons["interpretation_unavailable"] += 1
        return True, False, unsupported


def probe(
    conn: sqlite3.Connection,
    archive: Archive,
    *,
    enumeration_id: str,
    after_request_id: str | None = None,
    limits: Limits = _DEFAULT_LIMITS,
    now: datetime,
) -> dict[str, Any]:
    """Check at most one bounded member batch without recovery or state changes.

    Oversized or unexamined members advance the cursor with unknown counts.
    `end_of_enumeration` describes traversal only, not acquired/complete history.
    Counts are conservative lower bounds when unknown is nonzero. The caller
    must discard batches whose enumeration/revision/epoch changed, and expire
    observations from the earliest contributing check, not the final batch.
    """
    at = timestamp(now.isoformat())
    if at is None:
        raise ValueError("probe requires an aware time")
    if archive.recovery is not None:
        raise ValueError("probe requires Archive without recovery")
    if (
        not isinstance(enumeration_id, str)
        or len(enumeration_id) > 256
        or (
            after_request_id is not None
            and (not isinstance(after_request_id, str) or len(after_request_id) > 256)
        )
    ):
        raise ValueError("invalid enumeration or cursor")
    result: dict[str, Any] = {
        "supported": False,
        "enumeration_id": enumeration_id,
        "after_request_id": after_request_id,
        "next_cursor": after_request_id,
        "end_of_enumeration": False,
        "parent_valid": None,
        "checked": 0,
        "acquired": 0,
        "interpreted": 0,
        "unknown": 0,
        "reasons": {},
        "earliest_checked_at": at,
        "source": None,
        "source_ref": None,
        "pagination": "unknown",
    }
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='source_event_enumerations'"
    ).fetchone():
        result["reasons"] = {"event_inventory_schema_unavailable": 1}
        return result
    evidence = _Probe(conn, archive, limits)
    try:
        rows = evidence.read(
            "SELECT source,source_ref,parent_support_json,pagination FROM source_event_enumerations WHERE enumeration_id=?",
            (enumeration_id,),
            ("source", "source_ref", "parent_support_json", "pagination"),
            cap=1,
        )
        if not rows:
            result["reasons"] = {"enumeration_missing": 1}
            return result
        if any(
            not isinstance(rows[0][key], str) or len(rows[0][key]) > 1024
            for key in ("source", "source_ref", "pagination")
        ):
            raise ValueError("invalid enumeration metadata")
        result.update(
            supported=True,
            source=rows[0]["source"],
            source_ref=rows[0]["source_ref"],
            pagination=rows[0]["pagination"],
        )
        parents = json.loads(rows[0]["parent_support_json"])
        if not isinstance(parents, dict) or not parents:
            raise ValueError("invalid parent support")
        if len(parents) > limits.candidates:
            raise _Unknown("parent_candidate_budget")
        valid = True
        for identifier in parents:
            usable, _ = evidence.interpreted(identifier)
            valid = valid and usable
        result["parent_valid"] = valid
    except _Unknown as exc:
        evidence.reasons[str(exc)] += 1
    except (ValueError, KeyError, TypeError, RecursionError):
        evidence.reasons["parent_evidence_invalid"] += 1
        result["parent_valid"] = False
    # Cursor scanning has its own fixed-size allowance so exhausted verification
    # cannot repeatedly trap a large parent or member. No large JSON is returned.
    members = conn.execute(
        "SELECT CASE WHEN length(CAST(request_id AS BLOB))<=256 THEN request_id END AS request_id FROM source_event_enumeration_members WHERE enumeration_id=? AND request_id>? ORDER BY request_id LIMIT ?",
        (enumeration_id, after_request_id or "", limits.members + 1),
    ).fetchall()
    result["end_of_enumeration"] = len(members) <= limits.members
    for member in members[: limits.members]:
        if not isinstance(member["request_id"], str):
            evidence.reasons["invalid_member_cursor"] += 1
            result["end_of_enumeration"] = False
            break
        result["checked"] += 1
        result["next_cursor"] = member["request_id"]
        try:
            evidence.tick()
            page_rows = evidence.read(
                "SELECT request_json FROM source_event_enumeration_members WHERE enumeration_id=? AND request_id=?",
                (enumeration_id, member["request_id"]),
                ("request_json",),
                cap=1,
            )
            acquired, interpreted, unknown = evidence.page(
                {**page_rows[0], **dict(member), "enumeration_id": enumeration_id}
            )
            result["acquired"] += acquired
            result["interpreted"] += interpreted
            result["unknown"] += unknown
        except _Unknown as exc:
            evidence.reasons[str(exc)] += 1
            result["unknown"] += 1
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            evidence.reasons["member_evidence_invalid"] += 1
            result["unknown"] += 1
    result["reasons"] = dict(sorted(evidence.reasons.items()))
    return result
