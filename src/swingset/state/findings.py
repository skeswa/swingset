"""Transactional replacement of durable diagnostic findings."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from .work import bump_revision


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    subject_kind: str
    subject_id: str
    severity: str
    summary: str
    evidence: object
    suggested_override: str | None = None
    watch_id: str | None = None
    snapshot_id: str | None = None


def replace_findings(
    conn: sqlite3.Connection,
    *,
    owner_kind: str,
    owner_id: str,
    findings: tuple[Finding, ...],
    opened_at: str,
    run_id: str,
) -> bool:
    existing = {
        str(row[0]): row
        for row in conn.execute(
            "SELECT finding_id,kind,subject_kind,subject_id,severity,summary,evidence_json,suggested_override,watch_id,snapshot_id FROM findings WHERE owner_kind=? AND owner_id=? AND closed_at IS NULL",
            (owner_kind, owner_id),
        )
    }
    desired: dict[str, tuple[object, ...]] = {}
    for finding in findings:
        evidence_json = json.dumps(
            finding.evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        identity = json.dumps(
            (
                owner_kind,
                owner_id,
                finding.kind,
                finding.subject_kind,
                finding.subject_id,
                finding.summary,
            ),
            separators=(",", ":"),
        )
        identifier = hashlib.sha256(identity.encode()).hexdigest()[:16]
        desired[identifier] = (
            finding.kind,
            finding.subject_kind,
            finding.subject_id,
            finding.severity,
            finding.summary,
            evidence_json,
            finding.suggested_override,
            finding.watch_id,
            finding.snapshot_id,
        )
    changed = set(existing) != set(desired)
    if not changed:
        changed = any(tuple(existing[key][1:]) != desired[key] for key in desired)
    if not changed:
        return False
    for identifier in set(existing) - set(desired):
        conn.execute(
            "UPDATE findings SET closed_at=?,closed_by=? WHERE finding_id=?",
            (opened_at, run_id, identifier),
        )
    for identifier, values in desired.items():
        conn.execute(
            "INSERT INTO findings(finding_id,owner_kind,owner_id,kind,subject_kind,subject_id,severity,summary,evidence_json,suggested_override,watch_id,snapshot_id,opened_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(finding_id) DO UPDATE SET severity=excluded.severity,evidence_json=excluded.evidence_json,suggested_override=excluded.suggested_override,watch_id=excluded.watch_id,snapshot_id=excluded.snapshot_id,closed_at=NULL,closed_by=NULL",
            (identifier, owner_kind, owner_id, *values, opened_at, run_id),
        )
    bump_revision(conn, "findings")
    return True
