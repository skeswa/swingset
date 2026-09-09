"""Transparent hand-weighted identity score."""

from dataclasses import dataclass

from .candidates import Candidate


@dataclass(frozen=True)
class Weights:
    name: float = 0.65
    role: float = 0.10
    recency: float = 0.05
    division: float = 0.20
    source_id: float = 1.0


def score_candidate(candidate: Candidate, weights: Weights | None = None) -> float:
    weights = weights or Weights()
    subject, dancer = candidate.subject, candidate.dancer
    if subject.source_wsdc_id == dancer.wsdc_id:
        return 1.0
    role = 1.0 if subject.role == dancer.primary_role else 0.0
    recency = (
        1.0
        if subject.event_year is not None and dancer.recent_year >= subject.event_year - 3
        else 0.0
    )
    division_weight = 0.0 if candidate.division_ok is None else weights.division
    division = 0.0 if candidate.division_ok is None else float(candidate.division_ok)
    total = weights.name + weights.role + weights.recency + division_weight
    score = min(
        1.0,
        (
            weights.name * candidate.name_similarity
            + weights.role * role
            + weights.recency * recency
            + division_weight * division
        )
        / total,
    )
    return score if candidate.division_ok is not False else score * 0.1
