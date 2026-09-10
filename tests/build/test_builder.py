from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from swingset.build.builder import (
    BuildError,
    BuildInput,
    BuildMetadata,
    ParquetRows,
    build_candidate,
)
from swingset.build.schema import PRIMARY_KEYS, SCHEMAS


def inputs(**tables: list[dict[str, object]]) -> BuildInput:
    return BuildInput(
        tables=tables,
        schemas=SCHEMAS,
        primary_keys=PRIMARY_KEYS,
        revisions={"canonical": 1},
        captured_file_hashes={"suppressions.csv": "abc"},
        input_bundle_hash="bundle",
    )


def input_version(revision: int, **tables: list[dict[str, object]]) -> BuildInput:
    data = inputs(**tables)
    return BuildInput(
        data.tables,
        data.schemas,
        data.primary_keys,
        {"canonical": revision},
        {"suppressions.csv": str(revision)},
        data.input_bundle_hash,
    )


def metadata(candidate_id: str) -> BuildMetadata:
    return BuildMetadata(
        run_id="run_1",
        repository_commit="code",
        expected_parent="base",
        schema_version=1,
        versions={"parser": "1"},
        card=b"card\n",
        built_at=datetime(2026, 1, 1, tzinfo=UTC),
        candidate_id=candidate_id,
    )


def test_empty_build_is_complete_and_byte_reusable(tmp_path: Path) -> None:
    first = build_candidate(tmp_path, inputs(), metadata("cand_a"))
    second = build_candidate(tmp_path, inputs(), metadata("cand_b"))
    assert second.reused
    assert second.path == first.path
    assert json.loads((first.path / "BUILT").read_text())["manifest_hash"] == first.manifest_hash
    assert all((first.path / "data" / name).exists() for name in SCHEMAS)


def test_bootstrap_candidate_with_new_remote_parent_is_not_reused(tmp_path: Path) -> None:
    dry = metadata("dry")
    dry = BuildMetadata(**{**dry.__dict__, "expected_parent": None})
    first = build_candidate(tmp_path, inputs(), dry)
    real = build_candidate(tmp_path, inputs(), metadata("real"))
    assert real.path != first.path
    assert json.loads((real.path / "BUILT").read_text())["expected_parent"] == "base"


def test_fresh_build_metadata_does_not_create_semantic_change(tmp_path: Path) -> None:
    first = build_candidate(tmp_path, inputs(), metadata("cand_a"))
    (first.path / "PUBLISHED").write_text('{"commit":"published-a"}')
    (tmp_path / "baseline").symlink_to(Path("candidates/cand_a"))
    later = BuildMetadata(
        run_id="run_2",
        repository_commit="code",
        expected_parent="published-a",
        schema_version=1,
        versions={"parser": "1"},
        card=b"card\n",
        built_at=datetime(2026, 1, 2, tzinfo=UTC),
        candidate_id="cand_b",
    )
    second = build_candidate(tmp_path, inputs(), later)
    assert second.content_hash == first.content_hash
    assert not second.changed


def test_registry_date_key_builds_and_rebuilds_changelog(tmp_path: Path) -> None:
    placement = {field.name: None for field in SCHEMAS["registry_placements"]}
    placement.update(
        {
            "wsdc_id": 1,
            "role": "leader",
            "dance_style": "wcs",
            "division": "novice",
            "series_id": "series",
            "series_name_raw": "Series",
            "event_month": date(2026, 8, 1),
            "result": "1",
            "points": 3,
        }
    )
    first = build_candidate(
        tmp_path, input_version(1, registry_placements=[placement]), metadata("cand_a")
    )
    (first.path / "PUBLISHED").write_text('{"commit":"a"}')
    (tmp_path / "baseline").symlink_to(Path("candidates/cand_a"))

    changed = {**placement, "points": 6}
    rebuilt = build_candidate(
        tmp_path, input_version(2, registry_placements=[changed]), metadata("cand_b")
    )

    history = pq.read_table(
        rebuilt.path / "data" / "changelog" / "changelog.parquet"
    ).to_pylist()
    point_change = next(row for row in history if row["field"] == "points")
    assert json.loads(point_change["record_key"]) == [
        1,
        "leader",
        "series",
        "2026-08-01",
        "novice",
        "wcs",
    ]
    assert (json.loads(point_change["old_value"]), json.loads(point_change["new_value"])) == (
        3,
        6,
    )


def test_broken_entry_reference_fails_without_complete_candidate(tmp_path: Path) -> None:
    mark = {field.name: None for field in SCHEMAS["callback_marks"]}
    mark.update({"round_id": "r", "entry_id": "missing", "judge_id": "j"})
    with pytest.raises(BuildError, match="missing entry"):
        build_candidate(tmp_path, inputs(callback_marks=[mark]), metadata("cand_bad"))
    assert not (tmp_path / "candidates" / "cand_bad").exists()


def test_reversed_event_dates_block_build(tmp_path: Path) -> None:
    event = {field.name: None for field in SCHEMAS["events"]}
    event.update(
        {
            "event_id": "new-year",
            "start_date": date(2026, 12, 30),
            "end_date": date(2026, 1, 2),
        }
    )
    with pytest.raises(BuildError, match="starts after it ends"):
        build_candidate(tmp_path, inputs(events=[event]), metadata("cand_bad_dates"))


def _callback_tables() -> dict[str, list[dict[str, object]]]:
    entry = {field.name: None for field in SCHEMAS["entries"]}
    entry.update({"entry_id": "e", "link_status": "unmatched"})
    round_ = {field.name: None for field in SCHEMAS["rounds"]}
    round_.update({"round_id": "r"})
    judges = []
    marks = []
    for judge_id, mark, value in (("j1", "yes", 10.0), ("j2", "alt2", 4.3), ("j3", "no", 0.0)):
        judge = {field.name: None for field in SCHEMAS["judges"]}
        judge.update({"judge_id": judge_id})
        judges.append(judge)
        callback_mark = {field.name: None for field in SCHEMAS["callback_marks"]}
        callback_mark.update(
            {
                "round_id": "r",
                "entry_id": "e",
                "judge_id": judge_id,
                "mark": mark,
                "mark_value": value,
            }
        )
        marks.append(callback_mark)
    callback = {field.name: None for field in SCHEMAS["callbacks"]}
    callback.update(
        {
            "round_id": "r",
            "entry_id": "e",
            "score_sum": 14.30001,
            "yes_count": 1,
            "alt_count": 1,
            "no_count": 1,
            "outcome": "promoted",
        }
    )
    return {
        "entries": [entry],
        "rounds": [round_],
        "judges": judges,
        "callback_marks": marks,
        "callbacks": [callback],
    }


@pytest.mark.parametrize("field,bad_value", [("score_sum", 10.0), ("yes_count", 0), ("alt_count", 0), ("no_count", 0)])
def test_callback_aggregate_must_match_retained_marks(
    tmp_path: Path, field: str, bad_value: object
) -> None:
    tables = _callback_tables()
    tables["callbacks"][0][field] = bad_value
    with pytest.raises(BuildError, match="disagrees with retained marks"):
        build_candidate(tmp_path, inputs(**tables), metadata(f"cand_bad_{field}"))


def test_callback_validation_allows_float_tolerance_and_withheld_summary(tmp_path: Path) -> None:
    tables = _callback_tables()
    build_candidate(tmp_path / "summarized", inputs(**tables), metadata("cand_valid"))
    tables["callbacks"] = []
    build_candidate(tmp_path / "withheld", inputs(**tables), metadata("cand_withheld"))


def test_callback_mark_with_missing_judge_blocks_build(tmp_path: Path) -> None:
    tables = _callback_tables()
    tables["judges"] = tables["judges"][1:]
    with pytest.raises(BuildError, match="references missing judge j1"):
        build_candidate(tmp_path, inputs(**tables), metadata("cand_missing_judge"))


def test_bib_identity_is_scoped_to_a_contest(tmp_path: Path) -> None:
    def linked(entry_id: str, contest_id: str, wsdc_id: int) -> dict[str, object]:
        row = {field.name: None for field in SCHEMAS["entries"]}
        row.update(
            {
                "entry_id": entry_id,
                "contest_id": contest_id,
                "event_id": "event",
                "role": "follower",
                "bib": "698",
                "wsdc_id": wsdc_id,
                "link_status": "confirmed",
            }
        )
        return row

    build_candidate(
        tmp_path / "valid",
        inputs(entries=[linked("c1/F-698", "c1", 17340), linked("c2/F-698", "c2", 15865)]),
        metadata("cand_reused_bib"),
    )
    with pytest.raises(BuildError, match="maps to multiple WSDC ids"):
        build_candidate(
            tmp_path / "conflict",
            inputs(entries=[linked("c1/a", "c1", 17340), linked("c1/b", "c1", 15865)]),
            metadata("cand_conflicting_bib"),
        )


def test_suppression_scrubs_identity_and_removes_candidates(tmp_path: Path) -> None:
    entry = {field.name: None for field in SCHEMAS["entries"]}
    entry.update(
        {
            "entry_id": "e",
            "contest_id": "c",
            "event_id": "v",
            "role": "leader",
            "bib": "1",
            "name_raw": "Private Person",
            "name_norm": "private person",
            "wsdc_id": 42,
            "link_status": "confirmed",
        }
    )
    candidate = {field.name: None for field in SCHEMAS["link_candidates"]}
    candidate.update({"subject_kind": "entry", "subject_id": "e", "wsdc_id": 42})
    result = build_candidate(
        tmp_path,
        inputs(entries=[entry], link_candidates=[candidate]),
        metadata("cand_suppressed"),
        suppressions=[{"wsdc_id": "42", "name_norm": "private person"}],
    )
    entries = pq.read_table(result.path / "data" / "entries" / "entries.parquet").to_pylist()
    candidates = pq.read_table(result.path / "data" / "link_candidates" / "link_candidates.parquet")
    assert entries[0]["name_raw"] is None
    assert entries[0]["wsdc_id"] is None
    assert entries[0]["link_status"] == "suppressed"
    assert candidates.num_rows == 0


def test_stale_dry_run_candidate_rebuilds_against_new_baseline_history(tmp_path: Path) -> None:
    first = build_candidate(tmp_path, input_version(1), metadata("cand_a"))
    (first.path / "PUBLISHED").write_text('{"commit":"a"}')
    (tmp_path / "baseline").symlink_to(Path("candidates/cand_a"))
    b_row = {field.name: None for field in SCHEMAS["entries"]}
    b_row.update({"entry_id": "b", "link_status": "unmatched"})
    stale_b = build_candidate(tmp_path, input_version(2, entries=[b_row]), metadata("cand_b_old"))
    c_row = {field.name: None for field in SCHEMAS["entries"]}
    c_row.update({"entry_id": "c", "link_status": "unmatched"})
    c_meta = metadata("cand_c")
    c_meta = BuildMetadata(**{**c_meta.__dict__, "expected_parent": "a"})
    candidate_c = build_candidate(tmp_path, input_version(3, entries=[c_row]), c_meta)
    (candidate_c.path / "PUBLISHED").write_text('{"commit":"c"}')
    (tmp_path / "baseline").unlink()
    (tmp_path / "baseline").symlink_to(Path("candidates/cand_c"))
    b_meta = metadata("cand_b_new")
    b_meta = BuildMetadata(**{**b_meta.__dict__, "expected_parent": "c"})
    rebuilt_b = build_candidate(tmp_path, input_version(2, entries=[b_row]), b_meta)
    assert rebuilt_b.path != stale_b.path
    changes = pq.read_table(rebuilt_b.path / "data" / "changelog" / "changelog.parquet")
    assert changes.num_rows >= 3  # A->C history plus C->B removal/addition.


def test_changelog_stream_merge_preserves_sorted_history_and_nullable_keys(
    tmp_path: Path,
) -> None:
    def entry(identifier: str, name: str) -> dict[str, object]:
        row = {field.name: None for field in SCHEMAS["entries"]}
        row.update({"entry_id": identifier, "name_raw": name, "link_status": "unmatched"})
        return row

    first = build_candidate(
        tmp_path,
        input_version(1, entries=[entry("b", "Before")]),
        metadata("cand_a"),
    )
    first.path.joinpath("PUBLISHED").write_text('{"commit":"a"}')
    (tmp_path / "baseline").symlink_to(Path("candidates/cand_a"))
    second = build_candidate(
        tmp_path,
        input_version(2, entries=[entry("b", "After"), entry("a", "Added")]),
        metadata("cand_b"),
    )
    second.path.joinpath("PUBLISHED").write_text('{"commit":"b"}')
    (tmp_path / "baseline").unlink()
    (tmp_path / "baseline").symlink_to(Path("candidates/cand_b"))
    third = build_candidate(
        tmp_path,
        input_version(3, entries=[entry("a", "Added")]),
        metadata("cand_c"),
    )

    history = pq.read_table(
        third.path / "data" / "changelog" / "changelog.parquet"
    ).to_pylist()
    keys = [
        json.dumps(
            (row["changed_at"], row["table"], row["record_key"], row["field"]),
            default=str,
        )
        for row in history
    ]
    assert keys == sorted(keys)
    assert {row["change_type"] for row in history} >= {"added", "updated", "removed"}
    assert any(row["field"] is None for row in history)
    lazy = ParquetRows(third.path / "data" / "changelog" / "changelog.parquet")
    assert [row["record_key"] for row in lazy[::-1]] == [
        row["record_key"] for row in reversed(history)
    ]
