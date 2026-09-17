"""Deterministic blocked identity candidate generation."""

from dataclasses import dataclass

from rapidfuzz.distance import JaroWinkler
from rapidfuzz.fuzz import token_set_ratio

from swingset.normalize.names import nickname_equivalent, normalize_name, paired_names


@dataclass(frozen=True)
class Subject:
    """An entry or judge whose registry identity we want to establish.

    For example, fictional Alex Lee, leader bib 42 in Novice Jack & Jill,
    is one subject. Alex entering another contest produces another subject.
    ``subject_id`` identifies the participation record, not a registry person.
    ``source_wsdc_id`` is a printed identity claim; restrictions may block it.
    """

    subject_kind: str
    subject_id: str
    name_raw: str
    role: str = "unknown"
    division: str | None = None
    event_year: int | None = None
    source_wsdc_id: int | None = None
    bib: str | None = None
    contest_id: str | None = None


@dataclass(frozen=True)
class DancerRecord:
    """A registry identity and the facts used to compare it with a subject.

    For example, fictional WSDC 100 might be Alex Lee, primarily a leader,
    with activity in 2026. Several subjects can refer to this same person.
    The loaded role levels are registry facts, not reconstructed past levels.
    """

    wsdc_id: int
    name_raw: str
    primary_role: str
    recent_year: int
    is_pro: bool = False
    leader_required_level: str = "none"
    leader_allowed_level: str = "none"
    follower_required_level: str = "none"
    follower_allowed_level: str = "none"


@dataclass(frozen=True)
class Candidate:
    """A hypothesis that one subject and one registry dancer are the same person.

    A candidate is a pair, not just a dancer. If registry IDs 100 and 200
    both name Alex Lee, bib 42 can have two candidates: (bib 42, WSDC 100)
    and (bib 42, WSDC 200). Neither pair is confirmed by being generated.

    ``exact`` compares normalized names; ``nickname`` records configured
    nickname equivalence, such as Mike/Michael. ``token_set`` compares name
    words and contributes to ``name_similarity``. These are name signals,
    not the final weighted score. ``division_ok`` is True for compatible
    evidence, False for incompatible evidence, and None when unavailable.
    """

    subject: Subject
    dancer: DancerRecord
    name_similarity: float
    exact: bool
    nickname: bool
    token_set: float
    division_ok: bool | None


_LEVEL = {
    "none": 0,
    "newcomer": 1,
    "new": 1,
    "novice": 2,
    "nov": 2,
    "intermediate": 3,
    "int": 3,
    "advanced": 4,
    "adv": 4,
    "allstar": 5,
    "als": 5,
    "champion": 6,
    "chmp": 6,
}


def _division_ok(subject: Subject, dancer: DancerRecord) -> bool | None:
    """Compare the contest division with the dancer's loaded role levels.

    None means there is no usable eligibility evidence, not a mismatch.
    """
    if (
        subject.division in {None, "none", "open"}
        or subject.division not in _LEVEL
        or subject.role not in {"leader", "follower"}
    ):
        return None
    required = getattr(dancer, f"{subject.role}_required_level").casefold()
    allowed = getattr(dancer, f"{subject.role}_allowed_level").casefold()
    if required not in _LEVEL or allowed not in _LEVEL or "none" in {required, allowed}:
        return None
    return _LEVEL[required] <= _LEVEL[subject.division] <= _LEVEL[allowed]


def generate_candidates(
    subject: Subject, dancers: list[DancerRecord], nicknames: dict[str, str]
) -> list[Candidate]:
    """Find subject/dancer pairs worth scoring in the supplied registry pool.

    For fictional bib 42 named Alex Lee, two registry dancers with that name
    yield two candidates. Pat Gomez fails the surname-initial block. With a
    configured nickname mapping, Mike Smith can match Michael Smith.

    The surname-initial block applies even to printed-ID candidates. Coupled
    or unsplit paired subjects yield no individual candidates. Name matching
    proposes possibilities; scoring, review restrictions, and confirmation
    are handled later. Results have deterministic name-similarity/ID order.
    """
    if subject.role == "couple" or paired_names(subject.name_raw):
        return []
    wanted = normalize_name(subject.name_raw)
    result = []
    for dancer in dancers:
        candidate = normalize_name(dancer.name_raw)
        if (
            not wanted.last_token
            or not candidate.last_token
            or wanted.last_token[0] != candidate.last_token[0]
        ):
            continue
        exact = wanted.value == candidate.value
        nickname = nickname_equivalent(wanted, candidate, nicknames)
        jaro = JaroWinkler.normalized_similarity(wanted.value, candidate.value)
        token = token_set_ratio(wanted.value, candidate.value) / 100.0
        if (
            exact
            or nickname
            or jaro >= 0.92
            or token >= 0.90
            or subject.source_wsdc_id == dancer.wsdc_id
        ):
            result.append(
                Candidate(
                    subject,
                    dancer,
                    max(jaro, token, 1.0 if nickname else 0.0),
                    exact,
                    nickname,
                    token,
                    _division_ok(subject, dancer),
                )
            )
    return sorted(result, key=lambda item: (-item.name_similarity, item.dancer.wsdc_id))
