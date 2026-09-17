"""Load durable identity review evidence and retain committed resolutions.

DecisionPolicy owns the shared, deterministic review rules. DecisionResolver
adds storage-backed source continuity checks for current and baseline records.
"""

from __future__ import annotations

import json
import sqlite3
from types import MappingProxyType

from swingset.state.identity_journal import JournalToken, active_decisions, token
from swingset.state.identity_references import ReferenceBinding, SourceReference

from .policy import POLICY_VERSION as POLICY_VERSION
from .policy import DecisionPolicy
from .policy import DecisionResolution as DecisionResolution


class DecisionResolver:
    """Read durable review evidence for linking and publication.

    Source bindings connect an entry or judge to an original source location.
    Continuity checks detect missing, replaced, or ambiguously moved locations
    so a correction cannot silently disappear. DecisionPolicy interprets the
    loaded human decisions and claims; it does not query this connection.
    Callers recheck the journal token before selecting output.
    """

    def __init__(self, conn: sqlite3.Connection):
        journal = token(conn)
        decisions = active_decisions(conn)
        self.conn = conn
        migrations: dict[str, list[tuple[str, tuple[str, ...], str, str]]] = {}
        for row in conn.execute(
            "SELECT migration_id,from_ref_id,to_ref_ids_json,status,evidence FROM identity_reference_migrations WHERE migration_id NOT IN (SELECT supersedes FROM identity_reference_migrations WHERE supersedes IS NOT NULL)"
        ):
            migrations.setdefault(str(row[1]), []).append(
                (str(row[0]), tuple(json.loads(row[2])), str(row[3]), str(row[4]))
            )

        self.policy = DecisionPolicy(
            journal,
            tuple(decisions),
            MappingProxyType({key: tuple(value) for key, value in migrations.items()}),
        )

    @property
    def token(self) -> JournalToken:
        return self.policy.token

    def previous_reference_ids(self, subject_id: str) -> tuple[str, ...]:
        return tuple(
            str(row[0])
            for row in self.conn.execute(
                "SELECT ref_id FROM identity_reference_bindings WHERE subject_id=?", (subject_id,)
            )
        )

    def binding_problem(
        self,
        bindings: tuple[ReferenceBinding, ...],
        *,
        subject_kind: str | None = None,
        subject_id: str | None = None,
    ) -> str | None:
        if (
            not bindings
            and subject_id is not None
            and self.conn.execute(
                "SELECT 1 FROM identity_reference_bindings WHERE subject_kind=? AND subject_id=? LIMIT 1",
                (subject_kind, subject_id),
            ).fetchone()
        ):
            return "source_reference_unavailable"
        if len({b.reference.ref_id for b in bindings}) != len(bindings):
            return "ambiguous_source_locator"
        current_ids = {binding.reference.ref_id for binding in bindings}
        for binding in bindings:
            for decision in self.policy.decisions:
                if (
                    decision.source != binding.reference.source
                    or decision.source_event != binding.reference.source_event
                ):
                    continue
                old_ref = decision.reference.ref_id
                if old_ref in current_ids:
                    continue
                reachable, ambiguous, _moves = self.policy.reachable(old_ref)
                if not ambiguous and (reachable & current_ids or len(reachable) > 1):
                    continue
                old_subjects = self.conn.execute(
                    "SELECT subject_kind,subject_id FROM identity_reference_bindings WHERE ref_id=?",
                    (old_ref,),
                ).fetchall()
                if not old_subjects or all(row[0] != binding.subject_kind for row in old_subjects):
                    continue
                if any(
                    self.conn.execute(
                        "SELECT 1 FROM entries WHERE entry_id=? UNION ALL SELECT 1 FROM judges WHERE judge_id=?",
                        (row[1], row[1]),
                    ).fetchone()
                    for row in old_subjects
                ):
                    continue
                prior = self.conn.execute(
                    "SELECT r.reason FROM identity_reference_bindings b JOIN identity_link_resolutions r USING(subject_kind,subject_id) WHERE b.ref_id=?",
                    (binding.reference.ref_id,),
                ).fetchall()
                if prior and all("orphaned_source_reference" not in str(row[0]) for row in prior):
                    continue
                return "orphaned_source_reference_requires_migration"
            older = self.conn.execute(
                "SELECT DISTINCT semantic_hash FROM identity_reference_bindings WHERE ref_id=?",
                (binding.reference.ref_id,),
            ).fetchall()
            if any(str(row[0]) != binding.semantic_hash for row in older):
                approved = False
                for _identifier, targets, status, evidence in self.policy.migrations.get(
                    binding.reference.ref_id, []
                ):
                    try:
                        details = json.loads(evidence)
                    except json.JSONDecodeError:
                        details = {}
                    approved |= (
                        status == "approved"
                        and targets == (binding.reference.ref_id,)
                        and isinstance(details, dict)
                        and details.get("semantic_hash") == binding.semantic_hash
                    )
                if not approved:
                    return "source_row_replacement_requires_reference_migration"
            previous = self.conn.execute(
                "SELECT DISTINCT ref_id FROM identity_reference_bindings WHERE subject_kind=? AND subject_id=?",
                (binding.subject_kind, binding.subject_id),
            ).fetchall()
            current_ids = {b.reference.ref_id for b in bindings}
            for row in previous:
                old_ref = str(row[0])
                if old_ref in current_ids:
                    continue
                if not any(d.reference.ref_id == old_ref for d in self.policy.decisions):
                    continue
                reachable, ambiguous, _moves = self.policy.reachable(old_ref)
                if not (reachable & current_ids) or ambiguous:
                    return "source_locator_changed_requires_reference_migration"
        return None

    def resolve(
        self,
        references: tuple[SourceReference, ...],
        *,
        source_wsdc_id: int | None = None,
        registry_wsdc_ids: frozenset[int] = frozenset(),
        legacy_subject_id: str | None = None,
        reference_problem: str | None = None,
    ) -> DecisionResolution:
        return self.policy.resolve(
            references,
            source_wsdc_id=source_wsdc_id,
            registry_wsdc_ids=registry_wsdc_ids,
            legacy_subject_id=legacy_subject_id,
            reference_problem=reference_problem,
            previous_reference_ids=self.previous_reference_ids(legacy_subject_id)
            if reference_problem and legacy_subject_id
            else (),
        )


def retain_resolution(
    conn: sqlite3.Connection,
    kind: str,
    identifier: str,
    resolution: DecisionResolution,
    *,
    state: str,
    reason: str,
    now: str,
) -> None:
    values = (
        kind,
        identifier,
        json.dumps(resolution.ref_ids),
        json.dumps(resolution.decision_ids),
        resolution.policy_version,
        resolution.token.digest,
        resolution.token.generation,
        state,
        reason,
        now,
    )
    conn.execute(
        "INSERT INTO identity_link_resolutions VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(subject_kind,subject_id) DO UPDATE SET ref_ids_json=excluded.ref_ids_json,decision_ids_json=excluded.decision_ids_json,policy_version=excluded.policy_version,journal_digest=excluded.journal_digest,journal_generation=excluded.journal_generation,state=excluded.state,reason=excluded.reason,asserted_at=excluded.asserted_at",
        values,
    )
    cursor = conn.execute(
        "SELECT * FROM identity_links WHERE subject_kind=? AND subject_id=?", (kind, identifier)
    )
    row = cursor.fetchone()
    assertion = (
        {}
        if row is None
        else dict(zip((field[0] for field in cursor.description), row, strict=True))
    )
    evidence = {
        "refs": resolution.ref_ids,
        "decisions": resolution.decision_ids,
        "migrations": resolution.migration_ids,
        "policy_version": resolution.policy_version,
        "journal_digest": resolution.token.digest,
        "journal_generation": resolution.token.generation,
        "reason": reason,
    }
    conn.execute(
        "INSERT INTO identity_link_history(subject_kind,subject_id,state,assertion_json,resolution_json,recorded_at) VALUES (?,?,?,?,?,?)",
        (
            kind,
            identifier,
            state,
            json.dumps(assertion, sort_keys=True),
            json.dumps(evidence, sort_keys=True),
            now,
        ),
    )
