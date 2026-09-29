"""Commit a resolved event atomically after checking its input versions.

Identity policy has already run. This module stores its conclusions and audit
evidence, updates dependent tables, and completes the selected work together.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from swingset.clock import Clock
from swingset.project.materialization import helper_recipe, materializing
from swingset.state.db import Database
from swingset.state.findings import replace_findings
from swingset.state.identity_journal import token as journal_token
from swingset.state.identity_references import retain_binding
from swingset.state.work import WorkUnit, bump_revision, complete

from .decisions import resolution_columns, resolution_evidence, retain_resolution
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
    reads: Mapping[str, Any] | None = None,
) -> bool:
    """Store an EventResolution only while its selected inputs remain current.

    A resolution contains answers already computed for the event. The basis
    identifies the input versions used to compute them: a newly accepted
    rejection of WSDC 100 must prevent an older confirmation from being stored.
    ``reads`` is the run's read set with each key's value at load: the
    generation is selected from it, and completion records it (plan F20).

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
            reads=reads,
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
    # A subject whose conclusion, candidates and resolution are unchanged keeps
    # its rows. Rewriting it would append a superseded and an accepted history
    # row that record nothing new, about 100 rows per event on every re-run
    # (D-0209). Its processing times stay those of the run that changed it.
    changed = {
        index
        for index, item in enumerate(resolution.subjects)
        if not _unchanged(db, item, linker_version)
    }
    subject_ids = [resolution.subjects[index].evidence.subject.subject_id for index in changed]
    for subject_id in subject_ids:
        db.execute("DELETE FROM link_candidates WHERE subject_id=?", (subject_id,))
        db.execute("DELETE FROM identity_links WHERE subject_id=?", (subject_id,))
    for index, item in enumerate(resolution.subjects):
        if index in changed:
            _write_subject(db, item, now, run_id, linker_version)
        else:
            _write_dependents(db, item, now)
    replace_findings(
        db,
        owner_kind="link",
        owner_id=resolution.event_id,
        findings=resolution.findings,
        opened_at=now,
        run_id=run_id,
    )


def _link(item: SubjectResolution, linker_version: str) -> tuple[object, ...]:
    """The `identity_links` row without `asserted_at` and `run_id`."""
    subject, conclusion = item.evidence.subject, item.conclusion
    link_id = hashlib.sha256(f"{subject.subject_kind}|{subject.subject_id}".encode()).hexdigest()[
        :16
    ]
    return (
        link_id,
        subject.subject_kind,
        subject.subject_id,
        conclusion.wsdc_id,
        conclusion.method,
        conclusion.status,
        conclusion.confidence,
        json.dumps(item.constraints),
        linker_version,
    )


def _candidates(item: SubjectResolution) -> list[tuple[object, ...]]:
    """The `link_candidates` rows in rank order, without `run_id`."""
    subject = item.evidence.subject
    rows: list[tuple[object, ...]] = []
    for rank, assessment in enumerate(item.candidates, 1):
        candidate = assessment.candidate
        rows.append(
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
            )
        )
    return rows


_LINK_COLUMNS = (
    "link_id,subject_kind,subject_id,wsdc_id,method,status,confidence,constraints_applied,"
    "linker_version"
)
_CANDIDATE_COLUMNS = (
    "subject_kind,subject_id,wsdc_id,score,name_similarity,name_rarity,division_ok,role_ok,"
    "recency_ok,geography,bib_reuse,registry_confirms,source_id_confirms,rank,chosen"
)
_RESOLUTION_COLUMNS = (
    "ref_ids_json,decision_ids_json,policy_version,journal_digest,journal_generation,state,reason"
)


def _unchanged(db: sqlite3.Connection, item: SubjectResolution, linker_version: str) -> bool:
    """Whether storing this subject again would change no row but processing times.

    The link, its candidates, its resolution and the newest history row must
    all equal what this run would write, so the history keeps one row per change.
    """
    subject = item.evidence.subject
    key = (subject.subject_kind, subject.subject_id)
    link = db.execute(
        f"SELECT {_LINK_COLUMNS} FROM identity_links WHERE subject_kind=? AND subject_id=?", key
    ).fetchone()
    if link is None or tuple(link) != _link(item, linker_version):
        return False
    candidates = [
        tuple(row)
        for row in db.execute(
            f"SELECT {_CANDIDATE_COLUMNS} FROM link_candidates "
            "WHERE subject_kind=? AND subject_id=? ORDER BY rank",
            key,
        )
    ]
    if candidates != _candidates(item):
        return False
    state, reason = item.history_state, item.history_reason
    stored = db.execute(
        f"SELECT {_RESOLUTION_COLUMNS} FROM identity_link_resolutions "
        "WHERE subject_kind=? AND subject_id=?",
        key,
    ).fetchone()
    if stored is None or tuple(stored) != resolution_columns(
        item.review, state=state, reason=reason
    ):
        return False
    head = db.execute(
        "SELECT state,resolution_json,assertion_json FROM identity_link_history "
        "WHERE subject_kind=? AND subject_id=? ORDER BY history_id DESC LIMIT 1",
        key,
    ).fetchone()
    if head is None or (head[0], head[1]) != (
        state,
        resolution_evidence(item.review, reason=reason),
    ):
        return False
    recorded = json.loads(head[2])
    names = _LINK_COLUMNS.split(",")
    return [recorded.get(name) for name in names] == list(_link(item, linker_version))


def _write_subject(
    db: sqlite3.Connection, item: SubjectResolution, now: str, run_id: str, linker_version: str
) -> None:
    link = _link(item, linker_version)
    db.execute(
        "INSERT INTO identity_links VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (*link[:-1], now, run_id, link[-1]),
    )
    for row in _candidates(item):
        db.execute(
            "INSERT INTO link_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (*row, run_id),
        )
    _write_dependents(db, item, now)
    subject = item.evidence.subject
    retain_resolution(
        db,
        subject.subject_kind,
        subject.subject_id,
        item.review,
        state=item.history_state,
        reason=item.history_reason,
        now=now,
    )


def _write_dependents(db: sqlite3.Connection, item: SubjectResolution, now: str) -> None:
    """Idempotent writes every run makes: the subject's row and its reference bindings."""
    subject, conclusion = item.evidence.subject, item.conclusion
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
