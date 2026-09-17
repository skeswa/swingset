"""Per-role maximum-weight assignment."""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment


@dataclass(frozen=True)
class ScoredPair:
    """The solver's compact proposal: a row key, registry ID, and score.

    ``subject_id`` is the solver row key. The event resolver uses a role/bib
    key such as ``leader:42`` within one contest, so subjects sharing that
    bib share a choice. It maps results back to actual subject keys afterward.
    """

    subject_id: str
    wsdc_id: int
    score: float


def assign(pairs: list[ScoredPair], threshold: float = 0.5) -> dict[str, ScoredPair]:
    """Choose compatible hypotheses across all supplied solver rows.

    If bibs 42 and 43 both prefer WSDC 100, independent top-score selection
    would give one identity to two bibs. This solver coordinates the choices,
    assigning each row and dancer at most once, then rejects low scores.

    The caller supplies permitted pairs from one contest/role scope; the
    solver itself has no contest or review knowledge. Its choices remain
    provisional until the event resolver applies confirmation precedence.
    """
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
