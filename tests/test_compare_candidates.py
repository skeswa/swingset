"""Tests for the candidate comparison tool the phase 1 deploy runs twice."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

MODULE_PATH = Path("journal/tools/releases/compare_candidates.py")
SPEC = importlib.util.spec_from_file_location("compare_candidates", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
COMPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARE)


def _candidate(root: Path, tables: dict[str, list[dict[str, object]]]) -> Path:
    for table, rows in tables.items():
        directory = root / "data" / table
        directory.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(rows), directory / "part-0.parquet")
    return root


def _event(event_id: str, tier: str, run: str = "run_1") -> dict[str, object]:
    return {
        "event_id": event_id,
        "coverage_tier": tier,
        "name": event_id.upper(),
        "run_id": run,
        "first_seen_at": "2026-09-01T00:00:00+00:00",
        "last_seen_at": f"2026-09-2{run[-1]}T00:00:00+00:00",
    }


def test_rows_are_compared_by_key_without_provenance(tmp_path: Path, capsys) -> None:
    base = _candidate(
        tmp_path / "base",
        {
            "events": [_event("e1", "full"), _event("e2", "full"), _event("e3", "full")],
            "judges": [{"judge_id": "j1", "name": "A", "run_id": "run_1"}],
            "snapshots": [{"snapshot_id": "s1", "run_id": "run_1"}],
        },
    )
    new = _candidate(
        tmp_path / "new",
        {
            # e1 gone, e2's tier moved, e3 only re-stamped, e4 new.
            "events": [
                _event("e2", "index_only", "run_2"),
                _event("e3", "full", "run_2"),
                _event("e4", "full", "run_2"),
            ],
            "judges": [{"judge_id": "j1", "name": "A", "run_id": "run_2"}],
            "coverage": [{"scope_kind": "event", "scope_id": "e4", "source": "x", "via": "o"}],
        },
    )
    output = tmp_path / "report.json"
    assert COMPARE.main([str(base), str(new), str(output), "--limit", "5"]) == 0
    report = json.loads(output.read_text())
    assert report["tables_only_in_base"] == ["snapshots"]
    assert report["tables_only_in_new"] == ["coverage"]
    assert report["differing_tables"] == ["events"]
    events = report["tables"]["events"]
    assert events["rows"] == {"base": 3, "new": 3}
    assert events["key"] == ["event_id"]
    assert sorted(events["left_out"]) == ["first_seen_at", "last_seen_at", "run_id"]
    assert (events["added"], events["removed"], events["changed"]) == (1, 1, 1)
    assert events["added_keys"] == [{"event_id": "e4"}]
    assert events["removed_keys"] == [{"event_id": "e1"}]
    assert events["changed_keys"] == [{"event_id": "e2"}]
    assert events["changed_columns"] == {"coverage_tier": 1}
    assert report["tables"]["judges"]["identical"]
    assert "1 of 2 tables differ: events" in capsys.readouterr().err
    # Keeping a provenance column makes the re-stamped rows differ too.
    kept = COMPARE.compare(base, new, omit=("first_seen_at", "last_seen_at", "asserted_at"))
    assert kept["tables"]["judges"]["changed"] == 1
    assert kept["tables"]["events"]["changed"] == 2
    # A further column can be left out, such as a build's evidence cutoff.
    extra = tmp_path / "extra.json"
    assert COMPARE.main([str(base), str(new), str(extra), "--omit", "coverage_tier"]) == 0
    assert json.loads(extra.read_text())["tables"]["events"]["changed"] == 0


def test_the_same_candidate_is_identical(tmp_path: Path) -> None:
    base = _candidate(tmp_path / "base", {"events": [_event("e1", "full")]})
    report = COMPARE.compare(base, base)
    assert report["differing_tables"] == []
    assert (
        report["tables"]["events"]["digest"]["base"]
        == (report["tables"]["events"]["digest"]["new"])
    )
