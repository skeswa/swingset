"""Per-role maximum-weight assignment."""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment


@dataclass(frozen=True)
class ScoredPair:
    subject_id: str
    wsdc_id: int
    score: float


def assign(pairs: list[ScoredPair], threshold: float = 0.5) -> dict[str, ScoredPair]:
    subjects = sorted({pair.subject_id for pair in pairs})
    dancers = sorted({pair.wsdc_id for pair in pairs})
    if not subjects or not dancers:
        return {}
    lookup = {(pair.subject_id, pair.wsdc_id): pair for pair in pairs}
    costs = np.full((len(subjects), len(dancers)), 50.0)
    for row, subject in enumerate(subjects):
        for column, dancer in enumerate(dancers):
            if pair := lookup.get((subject, dancer)):
                costs[row, column] = -np.log(max(pair.score, 1e-12))
    rows, columns = linear_sum_assignment(costs)
    return {
        subjects[row]: pair
        for row, column in zip(rows, columns, strict=True)
        if (pair := lookup.get((subjects[row], dancers[column]))) is not None
        and pair.score >= threshold
    }
