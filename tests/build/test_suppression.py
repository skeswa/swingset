import copy
import json

from swingset.build.suppression import apply_suppressions


def sample():
    return {
        "entries": [
            {
                "entry_id": "event/c/L-name-private-person",
                "name_raw": "Private Person",
                "name_norm": "private person",
                "wsdc_id": 42,
                "link_status": "confirmed",
                "link_confidence": 1.0,
                "partner_entry_id": "other",
                "partner_name_raw": "Other Dancer",
            },
            {
                "entry_id": "other",
                "name_raw": "Other Dancer",
                "name_norm": "other dancer",
                "wsdc_id": 43,
                "link_status": "confirmed",
                "partner_entry_id": "event/c/L-name-private-person",
                "partner_name_raw": "Private Person",
            },
        ],
        "judges": [
            {
                "judge_id": "event/judge/private-person",
                "name_raw": "Private Person",
                "initials": "PP",
                "wsdc_id": 42,
            }
        ],
        "rounds": [{"round_id": "r", "chief_judge_id": "event/judge/private-person"}],
        "callback_marks": [
            {
                "round_id": "r",
                "entry_id": "event/c/L-name-private-person",
                "judge_id": "event/judge/private-person",
                "mark": "yes",
                "mark_value": 10.0,
            }
        ],
        "callbacks": [
            {
                "round_id": "r",
                "entry_id": "event/c/L-name-private-person",
                "score_sum": 10.0,
                "yes_count": 1,
            }
        ],
        "final_marks": [{"placement_id": "p", "judge_id": "event/judge/private-person", "rank": 1}],
        "placements": [
            {
                "placement_id": "p",
                "leader_entry_id": "event/c/L-name-private-person",
                "follower_entry_id": "other",
                "leader_wsdc_id": 42,
                "follower_wsdc_id": 43,
                "registry_points_leader": 10,
                "registry_points_follower": 12,
                "registry_confirmed": True,
                "points_matches_expected": True,
            }
        ],
        "dancers": [
            {
                "wsdc_id": 42,
                "first_name": "Private",
                "last_name": "Person",
                "name_norm": "private person",
            },
            {
                "wsdc_id": 43,
                "first_name": "Other",
                "last_name": "Dancer",
                "name_norm": "other dancer",
            },
            {
                "wsdc_id": 44,
                "first_name": "Another",
                "last_name": "Private",
                "name_norm": "another private",
            },
        ],
        "registry_placements": [
            {"wsdc_id": 42, "event_id": "event", "points": 10},
            {"wsdc_id": 43, "event_id": "event", "points": 12},
            {"wsdc_id": 44, "event_id": "event", "points": 10},
        ],
        "identity_links": [
            {
                "link_id": "a",
                "subject_kind": "entry",
                "subject_id": "event/c/L-name-private-person",
                "wsdc_id": 42,
            },
            {"link_id": "b", "subject_kind": "entry", "subject_id": "other", "wsdc_id": 43},
        ],
        "link_candidates": [
            {"subject_id": "event/c/L-name-private-person", "wsdc_id": 42},
            {"subject_id": "other", "wsdc_id": 42},
            {"subject_id": "other", "wsdc_id": 43},
        ],
        "review_queue": [
            {
                "item_id": "review",
                "evidence_json": json.dumps({"name": "Private Person", "wsdc_id": 42}),
                "summary": "Review Private Person",
            }
        ],
    }


def change(table, key, field, old, new):
    return {
        "table": table,
        "record_key": json.dumps(key),
        "field": field,
        "old_value": json.dumps(old),
        "new_value": json.dumps(new),
        "reason": "new_source_data",
        "change_type": "updated",
    }


def test_suppression_preserves_structural_marks_and_scrubs_dependent_identity():
    rows = sample()
    policy = apply_suppressions(
        rows, [{"wsdc_id": "42", "reason": "PRIVATE OPERATOR NOTE", "date": "2026-09-13"}]
    )
    hidden = rows["entries"][0]
    assert hidden["name_raw"] is None and hidden["name_norm"] is None and hidden["wsdc_id"] is None
    assert hidden["link_status"] == "suppressed" and hidden["link_confidence"] == 0
    assert hidden["entry_id"].startswith("suppressed-")
    assert rows["callbacks"][0]["entry_id"] == hidden["entry_id"]
    assert rows["callback_marks"][0]["entry_id"] == hidden["entry_id"]
    assert rows["entries"][1]["partner_entry_id"] == hidden["entry_id"]
    assert rows["entries"][1]["partner_name_raw"] is None
    judge = rows["judges"][0]
    assert judge["name_raw"] is None and judge["initials"] is None
    assert (
        rows["rounds"][0]["chief_judge_id"]
        == judge["judge_id"]
        == rows["callback_marks"][0]["judge_id"]
        == rows["final_marks"][0]["judge_id"]
    )
    assert rows["callback_marks"][0]["mark_value"] == 10.0
    assert rows["callbacks"][0]["score_sum"] == 10.0
    placement = rows["placements"][0]
    assert placement["leader_entry_id"] == hidden["entry_id"]
    assert placement["leader_wsdc_id"] is None and placement["registry_points_leader"] is None
    assert placement["follower_wsdc_id"] == 43 and placement["registry_points_follower"] == 12
    assert placement["registry_confirmed"] is False and placement["points_matches_expected"] is None
    assert [r["wsdc_id"] for r in rows["registry_placements"]] == [43, 44]
    assert rows["link_candidates"] == [{"subject_id": "other", "wsdc_id": 43}]
    assert rows["review_queue"][0]["evidence_json"] is None
    assert "private person" not in json.dumps(rows).casefold()
    assert "private-person" not in json.dumps(rows).casefold()
    assert "PRIVATE OPERATOR NOTE" not in repr(policy.__dict__)
    again = copy.deepcopy(rows)
    policy.apply(rows)
    assert rows == again


def test_multiple_suppressed_dancers_are_omitted_instead_of_duplicate_null_keys():
    rows = sample()
    apply_suppressions(rows, [{"wsdc_id": "42"}, {"wsdc_id": "44"}])
    assert [r["wsdc_id"] for r in rows["dancers"]] == [43]
    assert [r["wsdc_id"] for r in rows["registry_placements"]] == [43]


def test_bib_key_is_preserved_and_name_only_suppression_covers_unlinked_judge():
    rows = {
        "entries": [{"entry_id": "event/c/L-1", "name_raw": "Gül Nara", "wsdc_id": None}],
        "judges": [{"judge_id": "event/judge/gul-nara", "name_raw": "Gül Nara", "wsdc_id": None}],
        "callback_marks": [
            {"entry_id": "event/c/L-1", "judge_id": "event/judge/gul-nara", "mark_value": 42}
        ],
    }
    apply_suppressions(rows, [{"name_norm": "gul nara"}])
    assert rows["entries"][0]["entry_id"] == "event/c/L-1"
    assert rows["callback_marks"][0]["entry_id"] == "event/c/L-1"
    assert rows["callback_marks"][0]["mark_value"] == 42
    assert rows["judges"][0]["name_raw"] is None
    assert rows["callback_marks"][0]["judge_id"] == rows["judges"][0]["judge_id"]


def test_history_prescan_hides_earlier_name_and_later_id_for_removed_subject():
    rows = {"entries": []}
    policy = apply_suppressions(rows, [{"wsdc_id": 42}])
    history = [
        change("entries", ["removed"], "name_raw", None, "Private Person"),
        change("entries", ["removed"], "wsdc_id", None, 42),
        change("dancers", [42], "first_name", None, "Private"),
        change(
            "registry_placements",
            [42, "leader", "event", "2026-01", "novice", "wcs"],
            "points",
            1,
            2,
        ),
        change("entries", ["public"], "name_raw", None, "Public Person"),
    ]
    policy.discover_history(iter(history))
    assert list(policy.filter_changelog(iter(history))) == [history[-1]]
    assert "Private Person" not in json.dumps(list(policy.filter_changelog(iter(history))))


def test_current_suppression_also_filters_generated_deltas_and_nested_evidence():
    rows = sample()
    policy = apply_suppressions(rows, [{"wsdc_id": 42}])
    history = [
        change("entries", ["event/c/L-name-private-person"], "name_raw", "Private Person", None),
        change(
            "review_queue",
            ["old-review"],
            "evidence_json",
            json.dumps({"name": "Private Person", "wsdc_id": 42}),
            None,
        ),
        change("placements", ["p"], "leader_wsdc_id", 42, None),
        change("callbacks", ["r", "other"], "score_sum", 40, 42),
    ]
    policy.discover_history(iter(history))
    assert list(policy.filter_changelog(iter(history))) == [history[-1]]


def test_no_suppression_is_a_transparent_stream():
    rows = sample()
    before = copy.deepcopy(rows)
    policy = apply_suppressions(rows, [])
    history = [change("entries", ["entry"], "name_raw", None, "Private Person")]
    policy.discover_history(iter(history))
    assert list(policy.filter_changelog(iter(history))) == history
    assert rows == before


def test_history_discovery_reaches_fixed_point_before_any_alias_is_emitted():
    policy = apply_suppressions({}, [{"wsdc_id": 42}])
    history = [
        change("review_queue", ["early-review"], "summary", None, "Private Person needs review"),
        change("entries", ["removed"], "name_raw", None, "Private Person"),
        change("entries", ["removed"], "wsdc_id", None, 42),
    ]
    passes = 0
    while policy.discover_history(iter(history)):
        passes += 1
        assert passes < 8
    assert passes >= 2
    assert list(policy.filter_changelog(iter(history))) == []
    assert len(policy.history_keys) == 2
