"""Evidence and inspectable conclusions for event identity resolution.

A reviewed decision is human journal evidence. A resolution is the linker's
computed answer. Only ConfirmedIdentity supplies a default-join identity.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar, Literal

from swingset.normalize.names import paired_names
from swingset.state.findings import Finding
from swingset.state.identity_references import ReferenceBinding

from .candidates import Candidate, DancerRecord, Subject
from .policy import DecisionPolicy, DecisionResolution
from .score import Weights

SubjectKey = tuple[str, str]


@dataclass(frozen=True)
class SubjectEvidence:
    """Retained facts and provenance for one entry or judge.

    ``bindings`` connect the subject to original source locations. For bib 42,
    ``printed_ids={100}`` means an owned source cell prints WSDC 100;
    ``registry_ids={100}`` means the registry supports the matching result
    and normalized name. Neither fact bypasses review restrictions.
    Reference problems and previous references preserve corrections across
    source changes; ``previous_default_id`` lets history record revocation.
    """

    subject: Subject
    bindings: tuple[ReferenceBinding, ...] = ()
    printed_ids: frozenset[int] = frozenset()
    registry_ids: frozenset[int] = frozenset()
    reference_problem: str | None = None
    previous_reference_ids: tuple[str, ...] = ()
    previous_default_id: int | None = None

    @property
    def key(self) -> SubjectKey:
        return self.subject.subject_kind, self.subject.subject_id

    @property
    def mixed_person(self) -> bool:
        return self.subject.role == "couple" or bool(paired_names(self.subject.name_raw))


@dataclass(frozen=True)
class EventEvidence:
    """All subjects and registry/review facts needed to resolve one event.

    Subjects travel together because two different bibs can claim the same
    identity. ``eligible_judge_ids`` limits the registry pool for judges;
    ``policy`` contains the loaded reviewed decisions and reference migrations.
    """

    event_id: str
    subjects: tuple[SubjectEvidence, ...]
    dancers: tuple[DancerRecord, ...]
    eligible_judge_ids: frozenset[int]
    policy: DecisionPolicy


@dataclass(frozen=True)
class LinkingRules:
    """Loaded scoring weights and nickname mappings for this computation."""

    weights: Weights
    nicknames: Mapping[str, str]


@dataclass(frozen=True)
class CandidateAssessment:
    """A proposed pair's score, permissions, and retained supporting signals.

    ``score`` measures signal strength, ``allowed`` says whether review
    restrictions permit the pair, and ``selected`` says the final conclusion
    chooses its identity. These answer different questions: a rejected pair
    can retain score 1.0 with allowed=False; a selected tentative pair still
    supplies no default-join identity. A selection need not be confirmed.

    For example, (bib 42, WSDC 100) may have a perfect name score but neither
    a printed ID nor registry confirmation. ``source_id`` and
    ``registry_confirmation`` retain those evidence flags. ``name_rarity``
    is recorded without contributing to the score. ``bib_reuse`` marks the
    chosen identity on a repeated bib within the same contest and role.
    """

    candidate: Candidate
    score: float
    allowed: bool
    name_rarity: float
    role_match: bool | None
    recent: bool
    registry_confirmation: bool
    source_id: bool
    bib_reuse: bool = False
    selected: bool = False


@dataclass(frozen=True)
class ConfirmedIdentity:
    """An identity supported by an allowed confirmation route.

    For example, an owned source cell prints WSDC 100 and no restriction or
    conflict blocks it. ``method`` names the source, registry, or manual basis.
    This is the only conclusion that supplies a default-join identity.
    """

    wsdc_id: int
    method: str
    status: ClassVar[str] = "confirmed"
    confidence: ClassVar[float] = 1.0

    @property
    def reasons(self) -> tuple[str, ...]:
        return (self.method,)


@dataclass(frozen=True)
class TentativeIdentity:
    """An assigned hypothesis without a supported confirmation route.

    For example, bib 42 selects WSDC 100 on name and other scoring signals.
    Even confidence 1.0 leaves its default identity empty. ``status`` retains
    the probable/possible/ambiguous classification of this hypothesis.
    """

    wsdc_id: int
    method: str
    status: Literal["probable", "possible", "ambiguous"]
    confidence: float
    reasons: ClassVar[tuple[str, ...]] = ("no_confirmation_evidence",)


@dataclass(frozen=True)
class UnmatchedIdentity:
    """No identity was selected, for example because no assignment survived.

    Candidates may still exist: losing an assignment or having only restricted
    pairs can leave a subject unmatched. This alone does not establish that
    the person has no registry number.
    """

    reasons: tuple[str, ...] = ("no_permitted_assignment",)
    wsdc_id: ClassVar[None] = None
    method: ClassVar[str] = "none"
    status: ClassVar[str] = "unmatched"
    confidence: ClassVar[float] = 0.0


@dataclass(frozen=True)
class WithheldIdentity:
    """A hold, contradiction, or unresolved ownership prevents an identity join.

    For example, two distinct leader bibs in one contest claim WSDC 100.
    Reasons distinguish this outcome from an absent match, although both
    retain the database status ``unmatched``.
    """

    reasons: tuple[str, ...]
    wsdc_id: ClassVar[None] = None
    method: ClassVar[str] = "none"
    status: ClassVar[str] = "unmatched"
    confidence: ClassVar[float] = 0.0


IdentityConclusion = ConfirmedIdentity | TentativeIdentity | UnmatchedIdentity | WithheldIdentity


@dataclass(frozen=True)
class SubjectResolution:
    """The complete computed answer and explanation for one subject.

    ``review`` holds resolved human-review restrictions; ``conclusion`` is
    the final identity answer. ``findings`` describe evidence needing attention.
    For example, bib 42 can retain candidates 100 and 200, a rejection of 200,
    and a tentative conclusion selecting 100 with no default-join identity.
    """

    evidence: SubjectEvidence
    review: DecisionResolution
    candidates: tuple[CandidateAssessment, ...]
    conclusion: IdentityConclusion
    findings: tuple[Finding, ...]

    @property
    def default_wsdc_id(self) -> int | None:
        """The confirmed ID written to the ordinary entry or judge identity field."""
        return self.conclusion.wsdc_id if isinstance(self.conclusion, ConfirmedIdentity) else None

    @property
    def history_state(self) -> str:
        if self.default_wsdc_id is not None:
            return "accepted"
        return "revoked" if self.evidence.previous_default_id is not None else "unresolved"

    @property
    def history_reason(self) -> str:
        # Preserve the durable history convention: a hold explains revocation
        # even when paired-name ownership independently prevents a join.
        if self.review.hold_subject:
            return ",".join(self.review.contradictions) or "subject_hold"
        if self.evidence.mixed_person:
            return "paired_name_ownership_unresolved"
        return self.conclusion.method

    @property
    def constraints(self) -> tuple[str, ...]:
        return (
            ("paired_name_ownership_unresolved",)
            if self.evidence.mixed_person
            else ("contest_role_unique",)
        )


@dataclass(frozen=True)
class EventResolution:
    """All subject answers after resolving the event's competing claims.

    This is an in-memory result; computing it does not commit any identity.
    Persistence checks its input versions before storing the answers together.
    """

    event_id: str
    subjects: tuple[SubjectResolution, ...]

    @property
    def findings(self) -> tuple[Finding, ...]:
        return tuple(finding for subject in self.subjects for finding in subject.findings)
