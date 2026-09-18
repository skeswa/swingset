"""Transactional replacement of durable diagnostic findings."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from .work import bump_revision

REFERENCE_KINDS = ("body", "extract", "generation")


@dataclass(frozen=True, slots=True)
class Reference:
    """One thing a finding relies on, declared rather than guessed at.

    `kind` is `body` for a file under `blobs/`, `extract` for one under
    `extracts/`, or `generation` for a derivation generation whose output must
    stay in the live database. Retention keeps exactly what open findings
    declare here; it does not read evidence for clues.

    Declaring nothing is not the same as declaring an empty set: a caller that
    passes no references leaves whatever the finding already declared in place,
    including the rows migration 30 backfilled from its evidence.
    """

    kind: str
    sha256: str


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
    references: tuple[Reference, ...] = ()


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
    declared: dict[str, tuple[tuple[str, str], ...]] = {}
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
        for reference in finding.references:
            if reference.kind not in REFERENCE_KINDS:
                raise ValueError(f"unknown finding reference kind: {reference.kind}")
        declared[identifier] = tuple(
            sorted({(item.kind, item.sha256) for item in finding.references})
        )
    references_supported = _references_supported(conn)
    recorded = _recorded_references(conn, set(desired)) if references_supported else {}
    changed = set(existing) != set(desired)
    if not changed:
        changed = any(tuple(existing[key][1:]) != desired[key] for key in desired)
    if not changed:
        changed = any(declared[key] and recorded.get(key, ()) != declared[key] for key in declared)
    if not changed:
        return False
    if references_supported:
        # A finding that names a generation is a new retention root, so its
        # output has to be in the live database before the finding claims it.
        # Nothing may be written if that check fails.
        #
        # Only what this call newly declares is a new root. A generation an
        # unchanged finding already declared is already a root, and rechecking
        # it would let one archived generation named by one unchanged finding
        # refuse every later finding for that owner.
        from .retention import require_local

        require_local(
            conn,
            sorted(
                {
                    sha256
                    for identifier, items in declared.items()
                    for kind, sha256 in set(items) - set(recorded.get(identifier, ()))
                    if kind == "generation"
                }
            ),
        )
    for identifier in set(existing) - set(desired):
        conn.execute(
            "UPDATE findings SET closed_at=?,closed_by=?,state='satisfied',status_at=?,last_progress_at=? WHERE finding_id=?",
            (opened_at, run_id, opened_at, opened_at, identifier),
        )
        conn.execute("UPDATE finding_support SET active=0 WHERE finding_id=?", (identifier,))
    for identifier, values in desired.items():
        conn.execute(
            "INSERT INTO findings(finding_id,owner_kind,owner_id,kind,subject_kind,subject_id,severity,summary,evidence_json,suggested_override,watch_id,snapshot_id,opened_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(finding_id) DO UPDATE SET severity=excluded.severity,evidence_json=excluded.evidence_json,suggested_override=excluded.suggested_override,watch_id=excluded.watch_id,snapshot_id=excluded.snapshot_id,closed_at=NULL,closed_by=NULL",
            (identifier, owner_kind, owner_id, *values, opened_at, run_id),
        )
        conn.execute(
            "UPDATE findings SET state='needs_review',status_at=? WHERE finding_id=? AND state<>'needs_review'",
            (opened_at, identifier),
        )
        row = conn.execute(
            "SELECT owner_kind,owner_id,kind,subject_kind,subject_id,severity,summary,evidence_json,suggested_override,watch_id,snapshot_id,opened_at,run_id FROM findings WHERE finding_id=?",
            (identifier,),
        ).fetchone()
        conn.execute(
            "INSERT INTO finding_support VALUES (?,?,1) ON CONFLICT(finding_id) DO UPDATE SET payload_json=excluded.payload_json,active=1",
            (identifier, json.dumps(dict(row), sort_keys=True)),
        )
        if (
            references_supported
            and declared[identifier]
            and recorded.get(identifier, ()) != declared[identifier]
        ):
            # An empty declaration never removes recorded support. Most callers
            # declare nothing, migration 30 backfilled what the old evidence
            # scan pinned, and a caller that declares nothing is saying nothing
            # rather than saying "keep none of it".
            conn.execute("DELETE FROM finding_support_references WHERE finding_id=?", (identifier,))
            conn.executemany(
                "INSERT INTO finding_support_references VALUES (?,?,?)",
                ((identifier, kind, sha256) for kind, sha256 in declared[identifier]),
            )
    bump_revision(conn, "findings")
    return True


def _references_supported(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='finding_support_references'"
        ).fetchone()
        is not None
    )


def _recorded_references(
    conn: sqlite3.Connection, identifiers: set[str]
) -> dict[str, tuple[tuple[str, str], ...]]:
    """What each of these findings currently declares, in the same sorted shape.

    Read by primary key, in batches, and never by scanning the table: this runs
    on every finding replacement, and a scan would cost more the more support
    the database has recorded.
    """
    grouped: dict[str, list[tuple[str, str]]] = {}
    wanted = sorted(identifiers)
    for start in range(0, len(wanted), 500):
        batch = wanted[start : start + 500]
        places = ",".join("?" for _ in batch)
        for finding_id, kind, sha256 in conn.execute(
            f"SELECT finding_id,kind,sha256 FROM finding_support_references "
            f"WHERE finding_id IN ({places}) ORDER BY finding_id,kind,sha256",
            batch,
        ):
            grouped.setdefault(str(finding_id), []).append((str(kind), str(sha256)))
    return {key: tuple(value) for key, value in grouped.items()}
