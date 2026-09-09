from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from swingset.build.builder import BuildError, BuildInput, BuildMetadata, build_candidate
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
