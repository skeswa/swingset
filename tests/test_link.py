from swingset.link import (
    DancerRecord,
    ScoredPair,
    Subject,
    assign,
    expected_points,
    generate_candidates,
    score_candidate,
)


def test_candidates_include_nickname_and_source_id() -> None:
    subject = Subject("entry", "e1", "Mike Smith", "leader", event_year=2026)
    dancers = [
        DancerRecord(1, "Michael Smith", "leader", 2025),
        DancerRecord(2, "Other Jones", "leader", 2026),
    ]
    candidates = generate_candidates(subject, dancers, {"mike": "michael"})
    assert [candidate.dancer.wsdc_id for candidate in candidates] == [1]
    assert score_candidate(candidates[0]) >= 0.9


def test_candidate_order_is_deterministic() -> None:
    subject = Subject("entry", "e1", "Alex Lee")
    dancers = [
        DancerRecord(2, "Alex Lee", "unknown", 2026),
        DancerRecord(1, "Alex Lee", "unknown", 2026),
    ]
    assert [item.dancer.wsdc_id for item in generate_candidates(subject, dancers, {})] == [1, 2]


def test_assignment_enforces_unique_dancer() -> None:
    chosen = assign([ScoredPair("a", 1, 0.95), ScoredPair("b", 1, 0.80), ScoredPair("b", 2, 0.70)])
    assert {subject: pair.wsdc_id for subject, pair in chosen.items()} == {"a": 1, "b": 2}


def test_wsdc_points_chart_boundaries_and_extras() -> None:
    assert expected_points(4, 1) == 0
    assert expected_points(5, 1) == 3
    assert expected_points(20, 10) == 1
    assert expected_points(40, 12) == 1
    assert expected_points(80, 15) == 2
    assert expected_points(130, 1) == 25


def test_impossible_division_nearly_eliminates_name_match() -> None:
    subject = Subject("entry", "e", "Alex Lee", "leader", "novice", 2026)
    dancer = DancerRecord(1, "Alex Lee", "leader", 2026, False, "advanced", "champion")
    candidate = generate_candidates(subject, [dancer], {})[0]
    assert candidate.division_ok is False
    assert score_candidate(candidate) < 0.2
