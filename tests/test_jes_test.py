"""Offline tests for Jes Test matching, coverage, and agreement."""

from copy import deepcopy

from journal.tools.quality.jes_test.core import evaluate, score_sheet_correspondence


def registry(result="1"):
    return {
        "wsdc_id": 7849,
        "role": "follower",
        "dance_style": "wcs",
        "division": "allstar",
        "series_id": "wsdc-53",
        "series_name_raw": "Boston Tea Party",
        "event_month": "2025-03-01",
        "event_id": "2025-03-the-boston-tea-party",
        "result": result,
        "points": 6,
        "snapshot_id": "registry-snapshot",
    }


def entry(**changes):
    row = {
        "entry_id": "entry-1",
        "contest_id": "contest-1",
        "event_id": "2025-03-boston-tea-party",
        "event_month": "2025-03",
        "contest_type": "jack_and_jill",
        "dance_style": "wcs",
        "division": "allstar",
        "age_division": "none",
        "role": "follower",
        "name_raw": "Jess Ann Nail",
        "wsdc_id": None,
        "snapshot_id": "sheet-snapshot",
        "rounds_danced": ["prelim", "final"],
    }
    row.update(changes)
    return row


def tables(result="1", place=1, **changes):
    result_tables = {
        "registry_placements": [registry(result)],
        "entries": [entry()],
        "rounds": [
            {"round_id": "contest-1/prelim", "entry_count": 18},
            {"round_id": "contest-1/final", "entry_count": 6},
        ],
        "placements": [
            {
                "placement_id": "placement-1",
                "round_id": "contest-1/final",
                "place": place,
                "leader_entry_id": None,
                "follower_entry_id": "entry-1",
                "couple_entry_id": None,
            }
        ]
        if place is not None
        else [],
        "callbacks": [{"entry_id": "entry-1", "round_id": "contest-1/prelim"}],
        "callback_marks": [
            {"entry_id": "entry-1", "round_id": "contest-1/prelim", "judge_id": "judge-1"}
        ],
        "final_marks": [
            {"placement_id": "placement-1", "round_id": "contest-1/final", "judge_id": "judge-1"}
        ],
        "snapshots": [{"snapshot_id": "sheet-snapshot"}],
    }
    result_tables.update(changes)
    return result_tables


def test_coverage_does_not_depend_on_result_agreement():
    report = evaluate(tables(place=2), {"candidate_id": "test"})
    assert report["coverage"]["individual_results"] == 1
    assert report["rows"][0]["agreement"] == "disagrees"


def test_preliminary_participation_is_covered_but_agreement_is_unknown():
    source = tables(place=None)
    source["entries"][0]["rounds_danced"] = ["prelim", "semifinal"]
    source["rounds"] = [
        {"round_id": "contest-1/prelim", "entry_count": 18},
        {"round_id": "contest-1/semifinal", "entry_count": 10},
    ]
    source["placements"] = []
    source["final_marks"] = []
    row = evaluate(source, {})["rows"][0]
    assert row["has_individual_result"] and not row["has_final_result"]
    assert row["agreement"] == "insufficient_evidence"


def test_finalist_claims_and_numeric_places():
    assert evaluate(tables(result="F", place=6), {})["rows"][0]["agreement"] == "agrees"
    assert evaluate(tables(result="F", place=2), {})["rows"][0]["agreement"] == "disagrees"
    assert (
        evaluate(tables(result="4", place=None), {})["rows"][0]["agreement"]
        == "insufficient_evidence"
    )


def test_explicit_name_event_role_division_and_contest_are_required():
    matched, _ = score_sheet_correspondence([registry()], [entry()])
    assert len(next(iter(matched.values()))) == 1
    for change in (
        {"name_raw": "Jesanne Nail"},
        {"event_id": "2025-02-boston-tea-party"},
        {"role": "leader"},
        {"division": "sophisticated"},
        {"contest_type": "strictly"},
        {"contest_type": "pro_am"},
    ):
        matched, _ = score_sheet_correspondence([registry()], [entry(**change)])
        assert next(iter(matched.values())) == []


def test_other_confirmed_person_is_excluded():
    matches, excluded = score_sheet_correspondence([registry()], [entry(wsdc_id=1234)])
    assert next(iter(matches.values())) == []
    assert excluded[0]["reason"] == "linked_to_another_wsdc_id"


def test_ambiguous_matches_do_not_inflate_coverage():
    source = tables()
    duplicate = deepcopy(source["entries"][0])
    duplicate["entry_id"] = "entry-2"
    source["entries"].append(duplicate)
    report = evaluate(source, {})
    assert report["coverage"]["ambiguous"] == 1
    assert report["coverage"]["individual_results"] == 0
    assert report["rows"][0]["agreement"] == "conflicting_evidence"


def test_snapshot_provenance_required():
    report = evaluate(tables(snapshots=[]), {})
    assert report["coverage"]["individual_results"] == 0
    assert not report["rows"][0]["evidence"][0]["snapshot_verified"]


def test_preliminary_only_does_not_agree_with_numeric_final_place():
    source = tables()
    source["entries"][0]["rounds_danced"] = ["prelim", "semifinal"]
    source["rounds"] = [
        {"round_id": "contest-1/prelim", "entry_count": 18},
        {"round_id": "contest-1/semifinal", "entry_count": 10},
    ]
    source["placements"] = []
    source["final_marks"] = []
    row = evaluate(source, {})["rows"][0]
    assert row["has_individual_result"] and row["agreement"] == "insufficient_evidence"


def test_baseline_comparison_tracks_shared_keys_and_new_denominator_rows():
    from journal.tools.quality.jes_test.core import compare_baseline

    previous = evaluate(tables(), {"commit": "old"})
    current = tables()
    extra = registry("2")
    extra["event_month"] = "2026-03-01"
    extra["event_id"] = "2026-03-new-york-flow-festival"
    current["registry_placements"].append(extra)
    comparison = compare_baseline(evaluate(current, {"commit": "new"}), previous)
    assert (
        comparison["shared_registry_entries"] == 1
        and len(comparison["added_registry_entries"]) == 1
    )


def test_zero_denominator_is_unassessable():
    report = evaluate({key: [] for key in tables()}, {})
    assert report["denominator"] == 0
    assert report["coverage"]["individual_results_percent"] is None
    assert report["agreement"]["percent_agree_when_assessable"] is None
