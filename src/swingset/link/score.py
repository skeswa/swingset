"""Transparent hand-weighted identity score."""

from dataclasses import dataclass

from swingset.normalize.names import normalize_name

from .candidates import Candidate


@dataclass(frozen=True)
class Weights:
    """Relative contributions to a candidate's ranking score.

    Missing division evidence and unknown judge roles omit their weights.
    The retained ``source_id`` setting is not used as a numeric weight:
    a matching printed ID directly returns a score of 1.0.
    """

    name: float = 0.65
    role: float = 0.10
    recency: float = 0.05
    division: float = 0.20
    source_id: float = 1.0


def _judge_name_support(candidate: Candidate) -> bool:
    """A shared first token cannot erase a conflicting given name or surname."""
    subject = normalize_name(candidate.subject.name_raw)
    dancer = normalize_name(candidate.dancer.name_raw)
    if len(subject.tokens) < 2 or len(dancer.tokens) < 2:
        return False
    if subject.last_token != dancer.last_token:
        return False
    if subject.first_token != dancer.first_token and not candidate.nickname:
        return False

    def subsequence(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
        remaining = iter(long)
        return all(any(part == item for item in remaining) for part in short)

    left, right = subject.tokens[1:-1], dancer.tokens[1:-1]
    return subsequence(left, right) or subsequence(right, left)


def score_candidate(candidate: Candidate, weights: Weights | None = None) -> float:
    """Rank one proposed match using name, role, recency, and division signals.

    The score is not a calibrated probability: 0.97 does not mean a 97%
    chance of correctness. A perfect name match with compatible other signals
    can score 1.0 and still remain tentative. Reviewed restrictions can also
    forbid a high-scoring pair; this function does not decide permission or
    confirmation. CandidateAssessment carries those separate facts.
    """
    weights = weights or Weights()
    subject, dancer = candidate.subject, candidate.dancer
    if subject.source_wsdc_id == dancer.wsdc_id:
        return 1.0
    role = 1.0 if subject.role == dancer.primary_role else 0.0
    role_weight = (
        0.0 if subject.subject_kind == "judge" and subject.role == "unknown" else weights.role
    )
    recency = (
        1.0
        if subject.event_year is not None and dancer.recent_year >= subject.event_year - 3
        else 0.0
    )
    division_weight = 0.0 if candidate.division_ok is None else weights.division
    division = 0.0 if candidate.division_ok is None else float(candidate.division_ok)
    total = weights.name + role_weight + weights.recency + division_weight
    score = min(
        1.0,
        (
            weights.name * candidate.name_similarity
            + role_weight * role
            + weights.recency * recency
            + division_weight * division
        )
        / total,
    )
    score = score if candidate.division_ok is not False else score * 0.1
    if subject.subject_kind == "judge" and not _judge_name_support(candidate):
        # Keep the candidate and every original signal, but do not turn fuzzy,
        # incomplete, or conflicting names into acceptance merely by fixing
        # the unavailable-role denominator.
        return min(score, 0.89)
    return score
