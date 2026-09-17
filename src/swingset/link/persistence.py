"""Commit a resolved event atomically after checking its input versions.

Identity policy has already run. This module stores its conclusions and audit
evidence, updates dependent tables, and completes the selected work together.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING

from swingset.clock import Clock
from swingset.project.materialization import helper_recipe, materializing
from swingset.state.db import Database
from swingset.state.findings import replace_findings
from swingset.state.identity_journal import token as journal_token
from swingset.state.identity_references import retain_binding
from swingset.state.work import WorkUnit, bump_revision, complete

from .decisions import retain_resolution
from .effects import seed_confirmation_watches, update_registry_points
from .evidence import ResolutionBasis
from .model import EventResolution, SubjectResolution

if TYPE_CHECKING:
    from swingset.state.inputs import InputBundle


class StaleIdentityResolution(RuntimeError):
    """A newly accepted decision keeps this work queued for recomputation."""


def commit_resolution(
    database: Database,
    basis: ResolutionBasis,
    resolution: EventResolution,
    bundle: InputBundle,
    clock: Clock,
    run_id: str,
    linker_version: str,
) -> bool:
    """Store an EventResolution only while its selected inputs remain current.

    A resolution contains answers already computed for the event. The basis
    identifies the input versions used to compute them: a newly accepted
    rejection of WSDC 100 must prevent an older confirmation from being stored.

    Persist conclusions, candidate evidence, findings, and history together
    with placement updates, confirmation watches, revisions, and work
    completion. Return whether the compared link/placement state changed.
    Stale input checks raise and leave the transaction's output uncommitted.
    """
    event_id = resolution.event_id
    old = _published_state(database.connection, event_id)
    now = clock.now().isoformat()
    unit = WorkUnit("link", "event", event_id)

    def write(db: sqlite3.Connection) -> None:
        with materializing(
            db,
            unit,
            now=clock.now(),
            run_id=run_id,
            selection=basis.selection,
            recipe=helper_recipe(db, bundle.files, "link"),
        ):
            if journal_token(db) != basis.journal:
                raise StaleIdentityResolution("identity decisions changed during linking")
            _replace_resolutions(db, resolution, now, run_id, linker_version)
            db.execute(
                "UPDATE placements SET leader_wsdc_id=(SELECT wsdc_id FROM entries WHERE entry_id=leader_entry_id),follower_wsdc_id=(SELECT wsdc_id FROM entries WHERE entry_id=follower_entry_id) WHERE event_id=?",
                (event_id,),
            )
            update_registry_points(db, event_id)
            seed_confirmation_watches(db, event_id, clock.now())
            if old != _published_state(db, event_id):
                bump_revision(db, "links")

    complete(database, unit, write)
    return old != _published_state(database.connection, event_id)


def _published_state(conn: sqlite3.Connection, event_id: str) -> list[tuple[object, ...]]:
    """The existing semantic revision comparison, excluding run metadata."""
    links = [
        tuple(row)
        for row in conn.execute(
            "SELECT subject_kind,subject_id,wsdc_id,method,status,confidence FROM identity_links WHERE subject_id IN (SELECT entry_id FROM entries WHERE event_id=? UNION SELECT judge_id FROM judges WHERE event_id=?) ORDER BY subject_kind,subject_id",
            (event_id, event_id),
        )
    ]
    placements = [
        ("placement", *tuple(row))
        for row in conn.execute(
            "SELECT placement_id,leader_wsdc_id,follower_wsdc_id,registry_points_leader,registry_points_follower,registry_confirmed,points_matches_expected FROM placements WHERE event_id=? ORDER BY placement_id",
            (event_id,),
        )
    ]
    return links + placements


def _replace_resolutions(
    db: sqlite3.Connection, resolution: EventResolution, now: str, run_id: str, linker_version: str
) -> None:
    subject_ids = [item.evidence.subject.subject_id for item in resolution.subjects]
    if subject_ids:
        marks = ",".join("?" for _ in subject_ids)
        db.execute(f"DELETE FROM link_candidates WHERE subject_id IN ({marks})", subject_ids)
        db.execute(f"DELETE FROM identity_links WHERE subject_id IN ({marks})", subject_ids)
    for item in resolution.subjects:
        _write_subject(db, item, now, run_id, linker_version)
    replace_findings(
        db,
        owner_kind="link",
        owner_id=resolution.event_id,
        findings=resolution.findings,
        opened_at=now,
        run_id=run_id,
    )


def _write_subject(
    db: sqlite3.Connection, item: SubjectResolution, now: str, run_id: str, linker_version: str
) -> None:
    subject, conclusion = item.evidence.subject, item.conclusion
    link_id = hashlib.sha256(f"{subject.subject_kind}|{subject.subject_id}".encode()).hexdigest()[
        :16
    ]
    db.execute(
        "INSERT INTO identity_links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            link_id,
            subject.subject_kind,
            subject.subject_id,
            conclusion.wsdc_id,
            conclusion.method,
            conclusion.status,
            conclusion.confidence,
            json.dumps(item.constraints),
            now,
            run_id,
            linker_version,
        ),
    )
    for rank, assessment in enumerate(item.candidates, 1):
        candidate = assessment.candidate
        db.execute(
            "INSERT INTO link_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                subject.subject_kind,
                subject.subject_id,
                candidate.dancer.wsdc_id,
                assessment.score,
                candidate.name_similarity,
                assessment.name_rarity,
                None if candidate.division_ok is None else int(candidate.division_ok),
                None if assessment.role_match is None else int(assessment.role_match),
                int(assessment.recent),
                None,
                int(assessment.bib_reuse),
                int(assessment.registry_confirmation),
                int(assessment.source_id),
                rank,
                int(assessment.selected),
                run_id,
            ),
        )
    if subject.subject_kind == "entry":
        db.execute(
            "UPDATE entries SET wsdc_id=?,link_status=?,link_confidence=? WHERE entry_id=?",
            (item.default_wsdc_id, conclusion.status, conclusion.confidence, subject.subject_id),
        )
    else:
        db.execute(
            "UPDATE judges SET wsdc_id=? WHERE judge_id=?",
            (item.default_wsdc_id, subject.subject_id),
        )
    for binding in item.evidence.bindings:
        retain_binding(db, binding, now=now)
    retain_resolution(
        db,
        subject.subject_kind,
        subject.subject_id,
        item.review,
        state=item.history_state,
        reason=item.history_reason,
        now=now,
    )
