"""Retained review reports must preserve warnings and cannot rewrite their input."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "retained_phase1_review", ROOT / "journal/tools/collection/reconcile_retained_phase1.py"
)
assert SPEC is not None and SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)
INPUT = ROOT / "journal/evidence/collection/phase1-2026-09-13"


def test_report_keeps_parsed_warnings_separate_from_pending_acquisition():
    report = review.summarize(INPUT)
    assert len(report["pending_targets"]) == 17
    assert report["target_status_counts"] == {
        "parsed": 155,
        "empty": 33,
        "pending": 17,
        "finding": 4,
        "duplicate": 4,
    }
    assert [row["year"] for row in report["years"]] == list(range(2010, 2027))
    first = report["years"][0]
    assert first["recorded_capture_gaps_by_target_status"] == {
        "parsed": 16,
        "pending": 17,
        "finding": 4,
    }
    assert all(row["counts_match_export"] for row in report["years"])
    assert all(
        row["recorded_year_summary"]["events_accepted"] == "False" for row in report["years"]
    )


def test_report_refuses_overwrite_and_preserves_inputs(tmp_path):
    before = {name: (INPUT / name).read_bytes() for name in review.INPUTS}
    output = tmp_path / "review"
    report = review.write_report(INPUT, output)
    assert json.loads((output / "2026.json").read_bytes()) == report["years"][-1]
    with pytest.raises(FileExistsError):
        review.write_report(INPUT, output)
    assert before == {name: (INPUT / name).read_bytes() for name in review.INPUTS}
