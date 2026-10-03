"""Rebuildable requirement inventory. Reconciliation never executes repairs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import date, datetime

from swingset.fetch.archive import Archive

from .db import Database
from .requirement_scopes import scope_page, scope_present
from .verification import usable_verification
from .work import bump_revision

POLICY_VERSION = "1"
STATES = frozenset(
    {
        "ready",
        "retry_wait",
        "waiting_for_source",
        "needs_implementation",
        "needs_review",
        "unavailable",
        "out_of_scope",
        "satisfied",
    }
)
UNMET = STATES - {"satisfied", "out_of_scope"}


@dataclass(frozen=True)
class Requirement:
    kind: str
    subject_key: str
    source: str | None
    state: str
    next_action: str
    evidence: object
    blocking_reason: str | None = None
    policy_version: str = POLICY_VERSION
    next_eligible_at: str | None = None

    @property
    def identifier(self) -> str:
        value = json.dumps((self.kind, self.subject_key, self.policy_version))
        return hashlib.sha256(value.encode()).hexdigest()[:16]


def reconcile_requirement(
    conn: sqlite3.Connection, requirement: Requirement, now: datetime, run_id: str
) -> str:
    """Commit only a checked postcondition.

    This writes the `findings` row itself rather than going through
    `replace_findings`, because a requirement owns columns that function does
    not write (state, desired fingerprint, next action, blocking reason, next
    eligible time) and its identity is the requirement's own, not a hash of the
    summary. It therefore declares no `finding_support_references` row, and a
    requirement never pins a file on its own: an `archive_artifact` requirement
    is about a digest that `snapshots.body_sha256` already pins for the file
    closure, and the `retirement` branch only restates a finding that exists.
    Anything a requirement could declare is already kept for another reason.
    """
    if requirement.state not in STATES:
        raise ValueError(f"unknown requirement state: {requirement.state}")
    evidence = json.dumps(requirement.evidence, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(evidence.encode()).hexdigest()
    at = now.isoformat()
    previous = conn.execute(
        "SELECT state,desired_fingerprint,next_action,blocking_reason,next_eligible_at FROM findings WHERE finding_id=?",
        (requirement.identifier,),
    ).fetchone()
    # A new row opens now: the transition history that kept a re-created
    # requirement's first opening time is gone (D-0237).
    conn.execute(
        """INSERT INTO findings(finding_id,owner_kind,owner_id,kind,subject_kind,subject_id,severity,summary,evidence_json,opened_at,run_id,policy_version,desired_fingerprint,state,source,next_action,blocking_reason,next_eligible_at,status_at,closed_at,last_progress_at)
        VALUES (?,'requirement',?,?,'scope',?,'warning',?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(finding_id) DO UPDATE SET evidence_json=excluded.evidence_json,
        desired_fingerprint=excluded.desired_fingerprint,state=excluded.state,source=excluded.source,
        next_action=excluded.next_action,blocking_reason=excluded.blocking_reason,
        next_eligible_at=excluded.next_eligible_at,closed_at=excluded.closed_at,
        status_at=CASE WHEN findings.state<>excluded.state THEN excluded.status_at ELSE findings.status_at END,
        last_progress_at=CASE WHEN findings.state<>excluded.state AND excluded.state='satisfied' THEN excluded.status_at ELSE findings.last_progress_at END""",
        (
            requirement.identifier,
            requirement.kind,
            requirement.kind,
            requirement.subject_key,
            requirement.next_action,
            evidence,
            at,
            run_id,
            requirement.policy_version,
            fingerprint,
            requirement.state,
            requirement.source,
            requirement.next_action,
            requirement.blocking_reason,
            requirement.next_eligible_at,
            at,
            at if requirement.state not in UNMET else None,
            at if requirement.state == "satisfied" else None,
        ),
    )
    public_change = requirement.state in UNMET or (previous is not None and previous[0] in UNMET)
    desired = (
        requirement.state,
        fingerprint,
        requirement.next_action,
        requirement.blocking_reason,
        requirement.next_eligible_at,
    )
    if public_change and (previous is None or tuple(previous) != desired):
        bump_revision(conn, "findings")
    return requirement.identifier


def _check(
    conn: sqlite3.Connection, archive: Archive, scope: str, key: str, history_start: date
) -> Requirement | None:
    if scope == "round":
        watch = conn.execute("SELECT source,state FROM watches WHERE watch_id=?", (key,)).fetchone()
        usable = conn.execute(
            "SELECT 1 FROM observations WHERE watch_id=? LIMIT 1", (key,)
        ).fetchone()
        state = "out_of_scope" if watch[1] == "retired" else "satisfied" if usable else "ready"
        return Requirement(
            "round_observations",
            key,
            watch[0],
            state,
            "fetch or reparse listed round",
            {"watch_id": key, "has_observations": bool(usable)},
        )
    if scope == "mapping":
        source, reference = json.loads(key)
        mapped = conn.execute(
            "SELECT event_id FROM source_event_map WHERE source=? AND source_ref=?",
            (source, reference),
        ).fetchone()
        return Requirement(
            "source_event_mapping",
            key,
            source,
            "satisfied" if mapped else "needs_review",
            "review event mapping evidence",
            {"source_ref": reference, "event_id": mapped[0] if mapped else None},
        )
    if scope == "source_id":
        from swingset.sources.wsdc_registry.adapter import SOURCE

        watch = SOURCE.watch(int(key))
        verification = usable_verification(conn, watch.watch_id)
        return Requirement(
            "source_id_checked",
            key,
            "wsdc_registry",
            "satisfied" if verification else "ready",
            "verify source-asserted registry ID",
            {
                "wsdc_id": int(key),
                "verification_id": verification["verification_id"] if verification else None,
            },
        )
    if scope == "occurrence":
        series, month = json.loads(key)
        rows = conn.execute(
            "SELECT DISTINCT event_id FROM registry_placements WHERE series_id=? AND event_month=?",
            (series, month),
        ).fetchall()
        mapped = len(rows) == 1 and rows[0][0] is not None
        state = (
            "out_of_scope"
            if month[:7] < history_start.strftime("%Y-%m")
            else "satisfied"
            if mapped
            else "needs_review"
        )
        return Requirement(
            "registry_event_association",
            key,
            "wsdc_registry",
            state,
            "review registry occurrence mapping",
            {
                "series_id": series,
                "event_month": month,
                "event_ids": [row[0] for row in rows],
                "history_start": history_start.isoformat(),
            },
        )
    if scope == "finalist":
        matched = conn.execute(
            """SELECT 1 FROM entries e JOIN contests c USING(contest_id) JOIN placements p ON (p.leader_entry_id=e.entry_id OR p.follower_entry_id=e.entry_id) JOIN registry_placements rp ON rp.wsdc_id=e.wsdc_id AND rp.event_id=e.event_id AND rp.role=e.role AND rp.division=c.division AND rp.dance_style=c.dance_style AND rp.result IN (CAST(p.place AS TEXT),'F') WHERE e.entry_id=? LIMIT 1""",
            (key,),
        ).fetchone()
        return Requirement(
            "first_point_reconsideration",
            key,
            "wsdc_registry",
            "satisfied" if matched else "waiting_for_source",
            "reconsider retained finalist after registry evidence changes",
            {"entry_id": key, "registry_matches": bool(matched)},
        )
    if scope == "artifact":
        # `key` is a body digest taken from `snapshots`, which is what keeps the
        # file in the retention closure. The requirement reports on that file;
        # it does not hold it. See `reconcile_requirement`.
        support = conn.execute(
            "SELECT s.snapshot_id,w.source FROM snapshots s JOIN watches w USING(watch_id) WHERE s.body_sha256=? ORDER BY s.snapshot_id",
            (key,),
        ).fetchall()
        sources = sorted({row[1] for row in support})
        failure = None
        try:
            archive.read_body(key)
        except (OSError, ValueError, EOFError, zlib.error) as exc:
            failure = type(exc).__name__
        return Requirement(
            "archive_artifact",
            key,
            sources[0] if len(sources) == 1 else None,
            "unavailable" if failure else "satisfied",
            "restore verified artifact by digest",
            {"snapshot_ids": [row[0] for row in support], "sources": sources, "body_sha256": key},
            failure,
        )
    if scope == "retirement":
        row = conn.execute("SELECT * FROM findings WHERE finding_id=?", (key,)).fetchone()
        if row["policy_version"] != POLICY_VERSION or (
            not scope_present(conn, row["kind"], row["subject_id"])
        ):
            return Requirement(
                row["kind"],
                row["subject_id"],
                row["source"],
                "out_of_scope",
                "scope or policy retired; retain history",
                json.loads(row["evidence_json"]),
                policy_version=row["policy_version"],
            )
    return None


def scan(
    database: Database,
    now: datetime,
    run_id: str,
    *,
    limit: int = 100,
    history_start: date = date(2010, 1, 1),
) -> int:
    """One bounded durable keyset page over all retained years, with no requests."""
    if limit < 1:
        raise ValueError("scan limit must be positive")
    with database.transaction() as conn:
        checkpoint = conn.execute(
            "SELECT cursor,started_at FROM requirement_scan WHERE singleton=1"
        ).fetchone()
        rows = scope_page(conn, checkpoint[0], limit)
        for row in rows:
            if row["scope"] == "finding":
                support = conn.execute(
                    "SELECT payload_json FROM finding_support WHERE finding_id=?", (row["id"],)
                ).fetchone()
                payload = json.loads(support[0])
                columns = ",".join(payload)
                marks = ",".join("?" for _ in payload)
                inserted = conn.execute(
                    f"INSERT OR IGNORE INTO findings(finding_id,{columns}) VALUES (?,{marks})",
                    (row["id"], *payload.values()),
                ).rowcount
                if inserted:
                    bump_revision(conn, "findings")
            elif requirement := _check(
                conn, Archive(database.state_dir), row["scope"], row["id"], history_start
            ):
                reconcile_requirement(conn, requirement, now, run_id)
        end = len(rows) < limit
        conn.execute(
            "UPDATE requirement_scan SET cursor=?,started_at=?,last_scanned_at=?,last_completed_at=CASE WHEN ? THEN ? ELSE last_completed_at END,scanned=scanned+? WHERE singleton=1",
            (
                "" if end else rows[-1]["key"],
                now.isoformat() if end or checkpoint[1] is None else checkpoint[1],
                now.isoformat(),
                int(end),
                now.isoformat(),
                len(rows),
            ),
        )
    return len(rows)
