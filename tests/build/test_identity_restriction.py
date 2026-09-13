import json
from copy import deepcopy

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from swingset.build.builder import BuildError, BuildInput
from swingset.build.identity_restriction import restrict_identity_expansion


def baseline_at(path):
    path.mkdir()
    (path / "PUBLISHED").write_text(json.dumps({"commit": "reviewed-baseline"}))
    for name, key, rows in (
        (
            "entries",
            "entry_id",
            [("same", 10), ("changed", 20), ("null", None), ("old-contest", 30)],
        ),
        ("judges", "judge_id", [("judge", None)]),
    ):
        directory = path / "data" / name
        directory.mkdir(parents=True)
        table = pa.table(
            {key: [r[0] for r in rows], "wsdc_id": pa.array([r[1] for r in rows], type=pa.int32())}
        )
        pq.write_table(table, directory / "part.parquet")
    return path


def test_restriction_preserves_evidence_and_retained_joins_clears_dependents(tmp_path):
    baseline = baseline_at(tmp_path / "baseline")
    rows = {
        "entries": [
            {"entry_id": "same", "role": "leader", "wsdc_id": 10, "link_status": "confirmed"},
            {"entry_id": "changed", "role": "follower", "wsdc_id": 21, "link_status": "confirmed"},
            {"entry_id": "null", "role": "follower", "wsdc_id": 22, "link_status": "confirmed"},
            {
                "entry_id": "new-contest",
                "role": "leader",
                "wsdc_id": 30,
                "link_status": "confirmed",
            },
        ],
        "judges": [{"judge_id": "judge", "name_raw": "Named Judge", "wsdc_id": 10}],
        "placements": [
            {
                "placement_id": "place",
                "leader_entry_id": "same",
                "follower_entry_id": "changed",
                "leader_wsdc_id": 10,
                "follower_wsdc_id": 21,
                "registry_points_leader": 3,
                "registry_points_follower": 4,
                "registry_confirmed": True,
                "points_matches_expected": True,
            }
        ],
        "identity_links": [
            {
                "subject_id": "changed",
                "wsdc_id": 21,
                "status": "confirmed",
                "method": "registry_placement",
            }
        ],
        "link_candidates": [{"subject_id": "changed", "wsdc_id": 21, "score": 1.0}],
    }
    original = deepcopy(rows)
    data = BuildInput(rows, {}, {}, {"links": 4}, {}, "bundle")
    result, report = restrict_identity_expansion(data, baseline)
    assert rows == original
    assert result.revisions == data.revisions
    assert [r["wsdc_id"] for r in result.tables["entries"]] == [10, None, None, None]
    assert result.tables["entries"][1]["link_status"] == "confirmed"
    assert result.tables["judges"][0] == {
        "judge_id": "judge",
        "name_raw": "Named Judge",
        "wsdc_id": None,
    }
    assert result.tables["identity_links"] is rows["identity_links"]
    assert result.tables["link_candidates"] is rows["link_candidates"]
    place = result.tables["placements"][0]
    assert (place["leader_wsdc_id"], place["follower_wsdc_id"]) == (10, None)
    assert (place["registry_points_leader"], place["registry_points_follower"]) == (3, None)
    assert place["registry_confirmed"] is False
    assert place["points_matches_expected"] is None
    assert report["subjects"]["entries"]["withheld_default_ids"] == 3
    assert report["subjects"]["judges"]["withheld_default_ids"] == 1
    assert report["placements"] == {
        "withheld_default_ids": 1,
        "withheld_registry_points": 1,
        "changed_rows": 1,
    }
    assert report["baseline_commit"] == "reviewed-baseline"
    assert len(report["baseline_file_hashes"]) == 3
    repeated, next_report = restrict_identity_expansion(result, baseline)
    assert repeated.tables == result.tables
    assert next_report["subjects"]["entries"]["withheld_default_ids"] == 0


def test_no_new_placement_join_is_created_from_retained_entry(tmp_path):
    baseline = baseline_at(tmp_path / "baseline")
    data = BuildInput(
        {
            "entries": [{"entry_id": "same", "role": "leader", "wsdc_id": 10}],
            "placements": [
                {
                    "placement_id": "place",
                    "leader_entry_id": "same",
                    "leader_wsdc_id": None,
                    "registry_points_leader": 3,
                    "registry_points_follower": None,
                    "registry_confirmed": False,
                    "points_matches_expected": True,
                }
            ],
        },
        {},
        {},
        {},
        {},
        "bundle",
    )
    result, _ = restrict_identity_expansion(data, baseline)
    assert result.tables["placements"][0]["leader_wsdc_id"] is None
    assert result.tables["placements"][0]["registry_points_leader"] is None


def test_missing_published_baseline_receipt_is_rejected(tmp_path):
    data = BuildInput({}, {}, {}, {}, {}, "bundle")
    with pytest.raises(BuildError, match="published baseline"):
        restrict_identity_expansion(data, tmp_path)
