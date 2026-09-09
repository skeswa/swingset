"""Deterministic blocked identity candidate generation."""

from dataclasses import dataclass

from rapidfuzz.distance import JaroWinkler
from rapidfuzz.fuzz import token_set_ratio

from swingset.normalize.names import nickname_equivalent, normalize_name


@dataclass(frozen=True)
class Subject:
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
    if subject.division not in _LEVEL or subject.role not in {"leader", "follower"}:
        return None
    required = getattr(dancer, f"{subject.role}_required_level").casefold()
    allowed = getattr(dancer, f"{subject.role}_allowed_level").casefold()
    if required not in _LEVEL or allowed not in _LEVEL:
        return None
    return _LEVEL[required] <= _LEVEL[subject.division] <= _LEVEL[allowed]


def generate_candidates(
    subject: Subject, dancers: list[DancerRecord], nicknames: dict[str, str]
) -> list[Candidate]:
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
