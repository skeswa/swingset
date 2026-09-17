"""Resolve an event's identities from retained evidence, without storage access.

Read resolve_event for the workflow and _conclude for evidence precedence.
Assignment proposes hypotheses; confirmation and holds determine default joins.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Literal

from swingset.normalize.names import normalize_name
from swingset.state.findings import Finding

from .assign import ScoredPair, assign
from .candidates import Candidate, DancerRecord, generate_candidates
from .model import (
    CandidateAssessment,
    ConfirmedIdentity,
    EventEvidence,
    EventResolution,
    IdentityConclusion,
    LinkingRules,
    SubjectEvidence,
    SubjectKey,
    SubjectResolution,
    TentativeIdentity,
    UnmatchedIdentity,
    WithheldIdentity,
)
from .policy import DecisionResolution
from .score import score_candidate


@dataclass(frozen=True)
class _AssessedSubject:
    """One subject's evidence, resolved review restrictions, and scored pairs."""

    evidence: SubjectEvidence
    review: DecisionResolution
    candidates: tuple[CandidateAssessment, ...]


def resolve_event(evidence: EventEvidence, rules: LinkingRules) -> EventResolution:
    """Return every conclusion, candidate signal, and reason for one event.

    First resolve reviewed support and restrictions, then detect conflicting
    claims across subjects, assess candidate pairs, coordinate assignments,
    and choose conclusions. For fictional bib 42 named Alex Lee, this may
    retain registry candidates 100 and 200, reject 200 through review, and
    select 100 tentatively while leaving the default identity empty.

    Rejected candidates remain in the result. Nothing here reads a clock, accesses
    storage, or changes its inputs. Subject order and assignment ties retain the
    loaded event order and existing solver ordering respectively.
    """
    reviews = tuple(
        evidence.policy.resolve(
            tuple(binding.reference for binding in item.bindings),
            source_wsdc_id=item.subject.source_wsdc_id,
            registry_wsdc_ids=item.registry_ids,
            legacy_subject_id=item.subject.subject_id,
            reference_problem=item.reference_problem,
            previous_reference_ids=item.previous_reference_ids,
        )
        for item in evidence.subjects
    )
    reviews = _withhold_competing_claims(evidence.subjects, reviews)
    assessed = _assess_candidates(evidence, reviews, rules)
    assignments = _assign_by_contest_and_role(assessed)
    bib_counts = Counter(
        (item.subject.contest_id, item.subject.role, item.subject.bib)
        for item in evidence.subjects
        if item.subject.bib
    )
    source_groups: dict[tuple[str | None, str, int], set[str]] = defaultdict(set)
    for subject_evidence in evidence.subjects:
        subject = subject_evidence.subject
        if subject.source_wsdc_id is not None:
            source_groups[subject.contest_id, subject.role, subject.source_wsdc_id].add(
                subject.bib or subject.subject_id
            )
    resolutions = []
    for item in assessed:
        subject = item.evidence.subject
        reused_bib = (
            subject.bib is not None
            and bib_counts[subject.contest_id, subject.role, subject.bib] > 1
        )
        unique_printed_claim = (
            subject.source_wsdc_id is not None
            and len(source_groups[subject.contest_id, subject.role, subject.source_wsdc_id]) == 1
        )
        conclusion = _conclude(
            item, assignments.get(item.evidence.key), reused_bib, unique_printed_claim
        )
        candidates = tuple(
            replace(
                candidate,
                selected=conclusion.wsdc_id == candidate.candidate.dancer.wsdc_id,
                bib_reuse=reused_bib and conclusion.wsdc_id == candidate.candidate.dancer.wsdc_id,
            )
            for candidate in item.candidates
        )
        resolutions.append(
            SubjectResolution(item.evidence, item.review, candidates, conclusion, _findings(item))
        )
    return EventResolution(evidence.event_id, tuple(resolutions))


def _withhold_competing_claims(
    subjects: tuple[SubjectEvidence, ...], reviews: tuple[DecisionResolution, ...]
) -> tuple[DecisionResolution, ...]:
    """Hold distinct bibs making strong claims on one identity in the same scope.

    A strong claim comes from a printed ID, matching registry evidence, or a
    reviewed positive. If leader bibs 42 and 43 in one contest both claim
    WSDC 100, hold both subjects. A claim in another contest or role is
    separate. These checks run before choosing among scored hypotheses.
    """
    claims: dict[tuple[str | None, str, int], list[int]] = defaultdict(list)
    for index, (item, review) in enumerate(zip(subjects, reviews, strict=True)):
        subject = item.subject
        if subject.subject_kind != "entry" or review.hold_subject:
            continue
        strong_ids = set(item.registry_ids)
        if subject.source_wsdc_id is not None:
            strong_ids.add(subject.source_wsdc_id)
        if review.positive_wsdc_id is not None:
            strong_ids.add(review.positive_wsdc_id)
        for number in strong_ids:
            if review.allows(number):
                claims[subject.contest_id, subject.role, number].append(index)
    result = list(reviews)
    for claimants in claims.values():
        if len({subjects[i].subject.bib or subjects[i].subject.subject_id for i in claimants}) <= 1:
            continue
        for index in claimants:
            review = result[index]
            result[index] = replace(
                review,
                hold_subject=True,
                review_required=True,
                contradictions=tuple(
                    sorted(set(review.contradictions) | {"identity_claimed_by_distinct_bibs"})
                ),
            )
    return tuple(result)


def _candidate_pools(
    dancers: tuple[DancerRecord, ...], judge_ids: frozenset[int]
) -> tuple[dict[str, list[DancerRecord]], dict[str, list[DancerRecord]]]:
    """Index registry search pools by surname initial, before constructing pairs.

    A pool contains dancers, not candidates. Searching the L pool for Alex
    Lee can produce several subject/dancer pairs. Judge pools additionally
    restrict dancers to the loaded eligible IDs.
    """
    entries: dict[str, list[DancerRecord]] = {}
    judges: dict[str, list[DancerRecord]] = {}
    for dancer in dancers:
        surname = normalize_name(dancer.name_raw).last_token
        if not surname:
            continue
        entries.setdefault(surname[0], []).append(dancer)
        if dancer.wsdc_id in judge_ids:
            judges.setdefault(surname[0], []).append(dancer)
    return entries, judges


def _assess_candidates(
    evidence: EventEvidence, reviews: tuple[DecisionResolution, ...], rules: LinkingRules
) -> tuple[_AssessedSubject, ...]:
    """Attach scores, review permission, and audit signals to every proposed pair.

    Printed-ID variants retain evidence even when source cells disagree.
    A rejected pair can keep score 1.0 with allowed=False; filtering it out
    here would lose evidence. Assessments are ranked by score and registry ID.
    Final selection and bib-reuse flags are attached after conclusions.
    """
    entry_pools, judge_pools = _candidate_pools(evidence.dancers, evidence.eligible_judge_ids)
    surname_counts = Counter(
        dancer.name_raw.casefold().split()[-1]
        for dancer in evidence.dancers
        if dancer.name_raw.split()
    )
    nicknames = dict(rules.nicknames)
    result = []
    for item, review in zip(evidence.subjects, reviews, strict=True):
        subject = item.subject
        pools = entry_pools if subject.subject_kind == "entry" else judge_pools
        pool = pools.get(normalize_name(subject.name_raw).last_token[:1], [])
        generated: dict[int, Candidate] = {}
        variants = [
            replace(subject, source_wsdc_id=printed) for printed in sorted(item.printed_ids)
        ] or [subject]
        for variant in variants:
            for candidate in generate_candidates(variant, pool, nicknames):
                if candidate.dancer.wsdc_id in item.printed_ids:
                    candidate = replace(
                        candidate, subject=replace(subject, source_wsdc_id=candidate.dancer.wsdc_id)
                    )
                generated[candidate.dancer.wsdc_id] = candidate
        candidates = tuple(
            sorted(
                (
                    CandidateAssessment(
                        candidate=candidate,
                        score=score_candidate(candidate, rules.weights),
                        allowed=review.allows(candidate.dancer.wsdc_id),
                        name_rarity=1.0
                        / surname_counts[candidate.dancer.name_raw.casefold().split()[-1]],
                        role_match=None
                        if subject.subject_kind == "judge" and subject.role == "unknown"
                        else subject.role == candidate.dancer.primary_role,
                        recent=subject.event_year is not None
                        and candidate.dancer.recent_year >= subject.event_year - 3,
                        registry_confirmation=candidate.dancer.wsdc_id in item.registry_ids,
                        source_id=candidate.dancer.wsdc_id in item.printed_ids,
                    )
                    for candidate in generated.values()
                ),
                key=lambda item: (-item.score, item.candidate.dancer.wsdc_id),
            )
        )
        result.append(_AssessedSubject(item, review, candidates))
    return tuple(result)


@dataclass(frozen=True)
class _AssignmentScope:
    """One contest and role within which bibs compete for registry identities."""

    contest_id: str | None
    role: str


def _assign_by_contest_and_role(
    subjects: tuple[_AssessedSubject, ...],
) -> dict[SubjectKey, ScoredPair]:
    """Coordinate permitted hypotheses independently within each contest and role.

    Two different leader bibs in Novice Jack & Jill cannot both be assigned
    WSDC 100. Subjects sharing a bib share the solver's choice; that same bib
    in another contest belongs to a different scope. Results are keyed by
    (subject kind, subject ID) and remain provisional until _conclude runs.
    """
    scopes: dict[_AssignmentScope, list[_AssessedSubject]] = defaultdict(list)
    for item in subjects:
        subject = item.evidence.subject
        scopes[_AssignmentScope(subject.contest_id, subject.role)].append(item)
    assignments = {}
    for scope, members in scopes.items():
        # The solver's row key denotes one bib within this explicit scope.
        # Keep its historical ordering for deterministic equal-score ties.
        row_keys = {
            item.evidence.key: f"{scope.role}:{item.evidence.subject.bib or item.evidence.subject.subject_id}"
            for item in members
        }
        pairs = [
            ScoredPair(
                row_keys[item.evidence.key], candidate.candidate.dancer.wsdc_id, candidate.score
            )
            for item in members
            for candidate in item.candidates
            if candidate.allowed
        ]
        selected = assign(pairs)
        for item in members:
            if pair := selected.get(row_keys[item.evidence.key]):
                assignments[item.evidence.key] = pair
    return assignments


def _conclude(
    item: _AssessedSubject,
    assignment: ScoredPair | None,
    reused_bib: bool,
    unique_printed_claim: bool,
) -> IdentityConclusion:
    """Choose the final identity conclusion using the ordered evidence policy.

    Ownership problems and subject holds withhold the join. Otherwise prefer
    a supported reviewed positive, then a permitted unique printed identity,
    then unique permitted registry confirmation among generated candidates.
    An assignment supplies a tentative identity when no confirmation applies;
    absent an assignment, the subject is unmatched.

    For example, a perfect name score can yield a tentative identity for WSDC
    100, while an uncontradicted printed ID can confirm that identity. Two bibs
    claiming that identity can instead produce WithheldIdentity. Only the
    confirmed outcome supplies the subject's default-join identity.
    """
    subject, review = item.evidence.subject, item.review
    if item.evidence.mixed_person:
        return WithheldIdentity(("paired_name_ownership_unresolved",))
    if review.hold_subject:
        return WithheldIdentity(review.contradictions or ("subject_hold",))
    if review.positive_wsdc_id is not None:
        return ConfirmedIdentity(review.positive_wsdc_id, "manual")
    if (
        subject.source_wsdc_id is not None
        and review.allows(subject.source_wsdc_id)
        and unique_printed_claim
    ):
        return ConfirmedIdentity(subject.source_wsdc_id, "source_id")
    confirmed = {
        candidate.candidate.dancer.wsdc_id
        for candidate in item.candidates
        if candidate.registry_confirmation and candidate.allowed
    }
    if len(confirmed) == 1:
        return ConfirmedIdentity(next(iter(confirmed)), "registry_placement")
    if assignment is None:
        return UnmatchedIdentity()
    # Preserve ambiguity evidence from all candidates, including restricted ones.
    close = len(item.candidates) > 1 and item.candidates[1].score >= assignment.score - 0.05
    status: Literal["probable", "possible", "ambiguous"] = (
        "probable"
        if assignment.score >= 0.9 and not close
        else "possible"
        if assignment.score >= 0.7
        else "ambiguous"
    )
    method = "name_unique" if len(item.candidates) == 1 else "assignment"
    return TentativeIdentity(
        assignment.wsdc_id, "bib_reuse" if reused_bib else method, status, assignment.score
    )


def _findings(item: _AssessedSubject) -> tuple[Finding, ...]:
    """Describe evidence needing attention alongside the identity conclusion.

    For example, an unsplit 'Alex Lee and Sam Doe' cell produces a paired-name
    finding. Conflicting reviewed/source claims produce an identity-decision
    finding with the relevant IDs and restrictions. A finding explains a
    review need; it does not itself select a person.
    """
    subject, review = item.evidence.subject, item.review
    findings = []
    if item.evidence.mixed_person:
        findings.append(
            Finding(
                kind="paired_name",
                subject_kind=subject.subject_kind,
                subject_id=subject.subject_id,
                severity="warning",
                summary="Individual identity withheld for a paired subject",
                evidence={
                    "name_raw": subject.name_raw,
                    "reason": "paired_name_ownership_unresolved",
                    "source_wsdc_id": subject.source_wsdc_id,
                    "has_override": bool(review.decision_ids),
                },
            )
        )
    if review.review_required:
        findings.append(
            Finding(
                kind="identity_decision",
                subject_kind=subject.subject_kind,
                subject_id=subject.subject_id,
                severity="warning",
                summary="Identity requires decision-aware review",
                evidence={
                    "decision_ids": review.decision_ids,
                    "reference_ids": review.ref_ids,
                    "migration_ids": review.migration_ids,
                    "contradictions": review.contradictions,
                    "blocked_wsdc_ids": sorted(review.blocked_wsdc_ids),
                    "printed_wsdc_ids": sorted(item.evidence.printed_ids),
                    "hold_subject": review.hold_subject,
                    "journal_digest": review.token.digest,
                    "policy_version": review.policy_version,
                },
            )
        )
    return tuple(findings)
