"""One decision policy for source IDs, reviewed positives, and scored candidates."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from swingset.state.identity_journal import JournalToken, active_decisions, token
from swingset.state.identity_references import ReferenceBinding, SourceReference

POLICY_VERSION = "identity-decisions-v1"


@dataclass(frozen=True)
class DecisionResolution:
    token: JournalToken
    ref_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]
    migration_ids: tuple[str, ...]
    positive_wsdc_id: int | None
    blocked_wsdc_ids: frozenset[int]
    hold_subject: bool
    contradictions: tuple[str, ...]
    review_required: bool
    policy_version: str = POLICY_VERSION

    def allows(self, wsdc_id: int) -> bool:
        return (
            not self.hold_subject
            and wsdc_id not in self.blocked_wsdc_ids
            and (self.positive_wsdc_id is None or wsdc_id == self.positive_wsdc_id)
        )


class DecisionResolver:
    """Read an accepted journal once, then resolve durable refs without writes.

    Callers may supply baseline references without a current canonical subject.
    Recheck ``token`` in the write/publication transaction before selecting output.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.token = token(conn)
        self.decisions = active_decisions(conn)
        self.conn = conn
        self.migrations: dict[str, list[tuple[str, tuple[str, ...], str, str]]] = {}
        for row in conn.execute(
            "SELECT migration_id,from_ref_id,to_ref_ids_json,status,evidence FROM identity_reference_migrations WHERE migration_id NOT IN (SELECT supersedes FROM identity_reference_migrations WHERE supersedes IS NOT NULL)"
        ):
            self.migrations.setdefault(str(row[1]), []).append(
                (str(row[0]), tuple(json.loads(row[2])), str(row[3]), str(row[4]))
            )

    def _reachable(self, reference: str) -> tuple[set[str], bool, set[str]]:
        reachable = set()
        pending = [reference]
        ambiguous = False
        migrations = set()
        while pending:
            ref = pending.pop()
            if ref in reachable:
                continue
            reachable.add(ref)
            moves = self.migrations.get(ref, [])
            if len(moves) > 1:
                ambiguous = True
            for identifier, targets, status, _evidence in moves:
                migrations.add(identifier)
                ambiguous |= status != "approved" or len(targets) != 1
                pending.extend(targets)
        return reachable, ambiguous, migrations

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
            for decision in self.decisions:
                if (
                    decision.source != binding.reference.source
                    or decision.source_event != binding.reference.source_event
                ):
                    continue
                old_ref = decision.reference.ref_id
                if old_ref in current_ids:
                    continue
                reachable, ambiguous, _moves = self._reachable(old_ref)
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
                for _identifier, targets, status, evidence in self.migrations.get(
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
                if not any(d.reference.ref_id == old_ref for d in self.decisions):
                    continue
                reachable, ambiguous, _moves = self._reachable(old_ref)
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
        refs = {ref.ref_id for ref in references}
        if reference_problem and legacy_subject_id:
            refs.update(
                str(row[0])
                for row in self.conn.execute(
                    "SELECT ref_id FROM identity_reference_bindings WHERE subject_id=?",
                    (legacy_subject_id,),
                )
            )
        applicable = []
        migrations = set()
        problems = set()
        for reference in tuple(refs):
            reachable, ambiguous, moves = self._reachable(reference)
            refs.update(reachable)
            migrations.update(moves)
            if ambiguous:
                problems.add("ambiguous_reference_migration")
        for decision in self.decisions:
            targets, ambiguous, moves = self._reachable(decision.reference.ref_id)
            legacy = (
                decision.source == "legacy"
                and decision.participant == f"unmapped-entry:{legacy_subject_id}"
            )
            if not (targets & refs) and not legacy:
                continue
            applicable.append(decision)
            migrations.update(moves)
            if ambiguous:
                problems.add("ambiguous_reference_migration")
        if reference_problem:
            problems.add(reference_problem)
        positives = {int(d.wsdc_id) for d in applicable if d.decision == "same_person"}
        blocked = {
            int(d.wsdc_id)
            for d in applicable
            if d.decision in {"different_person", "insufficient_evidence"} and d.wsdc_id != "NONE"
        }
        held = any(
            d.decision == "hold_unlinked"
            or (
                d.decision == "insufficient_evidence"
                and (d.wsdc_id == "NONE" or d.source == "legacy")
            )
            for d in applicable
        )
        if len(positives) > 1:
            problems.add("conflicting_positive_decisions")
        if positives & blocked:
            problems.add("conflicting_positive_and_negative_decisions")
        if positives and held:
            problems.add("positive_decision_conflicts_with_subject_hold")
        strong = set(registry_wsdc_ids)
        if source_wsdc_id is not None:
            strong.add(source_wsdc_id)
        if positives and strong - positives:
            problems.add("new_evidence_conflicts_with_positive_decision")
        if strong & blocked:
            problems.add("new_evidence_conflicts_with_pair_restriction")
        if len(strong) > 1:
            problems.add("contradictory_source_and_registry_identities")
        return DecisionResolution(
            self.token,
            tuple(sorted(refs)),
            tuple(sorted(d.decision_id for d in applicable)),
            tuple(sorted(migrations)),
            next(iter(positives)) if len(positives) == 1 else None,
            frozenset(blocked),
            held or bool(problems),
            tuple(sorted(problems)),
            held
            or bool(problems)
            or any(d.decision == "insufficient_evidence" for d in applicable),
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
