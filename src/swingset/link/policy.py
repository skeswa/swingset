"""Deterministic reviewed-decision policy shared by linking and publication.

All journal and migration inputs are loaded before constructing this policy.
Resolving references never reads or writes storage.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from swingset.state.identity_journal import Decision, JournalToken
from swingset.state.identity_references import SourceReference

Migration = tuple[str, tuple[str, ...], str, str]


POLICY_VERSION = "identity-decisions-v1"


@dataclass(frozen=True)
class DecisionResolution:
    """Resolved review permissions and support for one subject, before selection.

    A reviewed decision is human journal evidence; this value is the policy's
    interpretation of applicable decisions and competing source claims. It is
    not the final SubjectResolution. Rejecting bib 42/WSDC 200 can leave WSDC
    100 allowed; ``hold_subject`` prevents every identity for bib 42.
    ``review_required`` flags attention and does not necessarily imply a hold.
    Decision, reference, and migration IDs explain which evidence applied.
    """

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
        """Whether review restrictions permit this pair, independent of its score."""
        return (
            not self.hold_subject
            and wsdc_id not in self.blocked_wsdc_ids
            and (self.positive_wsdc_id is None or wsdc_id == self.positive_wsdc_id)
        )


@dataclass(frozen=True)
class DecisionPolicy:
    """Loaded human decisions and source-reference continuity shared by callers.

    Journal entries can confirm a pair, reject a pair, or hold a whole subject.
    Migrations connect old source locators to reviewed replacements so those
    decisions continue to apply after a source changes its layout or numbering.
    """

    token: JournalToken
    decisions: tuple[Decision, ...]
    migrations: Mapping[str, tuple[Migration, ...]]

    def reachable(self, reference: str) -> tuple[set[str], bool, set[str]]:
        reachable = set()
        pending = [reference]
        ambiguous = False
        migrations = set()
        while pending:
            ref = pending.pop()
            if ref in reachable:
                continue
            reachable.add(ref)
            moves = self.migrations.get(ref, ())
            if len(moves) > 1:
                ambiguous = True
            for identifier, targets, status, _evidence in moves:
                migrations.add(identifier)
                ambiguous |= status != "approved" or len(targets) != 1
                pending.extend(targets)
        return reachable, ambiguous, migrations

    def resolve(
        self,
        references: tuple[SourceReference, ...],
        *,
        source_wsdc_id: int | None = None,
        registry_wsdc_ids: frozenset[int] = frozenset(),
        legacy_subject_id: str | None = None,
        reference_problem: str | None = None,
        previous_reference_ids: tuple[str, ...] = (),
    ) -> DecisionResolution:
        """Determine which reviewed support and restrictions apply to these refs.

        For example, a reviewed rejection of WSDC 200 restricts that pair.
        If an owned source cell also prints 200, the evidence conflicts and
        the subject is held. A supported reviewed positive identifies the
        permitted person but still passes through final ownership checks.

        References identify durable source locations, rather than guessed
        names. The returned token and evidence IDs let persistence and
        publication verify and explain the review basis. No storage is read.
        """
        refs = {ref.ref_id for ref in references}
        if reference_problem and legacy_subject_id:
            refs.update(previous_reference_ids)
        applicable = []
        migrations = set()
        problems = set()
        for reference in tuple(refs):
            reachable, ambiguous, moves = self.reachable(reference)
            refs.update(reachable)
            migrations.update(moves)
            if ambiguous:
                problems.add("ambiguous_reference_migration")
        for decision in self.decisions:
            targets, ambiguous, moves = self.reachable(decision.reference.ref_id)
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
