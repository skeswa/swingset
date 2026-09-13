"""Generated changes remain bounded, repeatable, and publication compatible."""

import json
import runpy
import sqlite3
import tracemalloc
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from swingset.build.builder import BuildInput, BuildMetadata, build_candidate
from swingset.build.changelog import changes, generated_delta, sort_key
from swingset.build.schema import PRIMARY_KEYS, SCHEMAS

NOW = datetime(2026, 9, 13, tzinfo=UTC)


def test_disk_comparison_matches_captured_eager_oracle_for_typed_nullable_keys(tmp_path):
    original = runpy.run_path(
        str(Path(__file__).parent / "fixtures/build/changelog_before_memory_fix.py")
    )["_changelog"]
    keys = {"registry_placements": ("id", "date"), "entries": ("entry_id",)}
    before = {
        "registry_placements": [
            {"id": n, "date": date(2020, 1, 1), "value": "old"} for n in (10, 2, None)
        ],
        "entries": [{"entry_id": 'a"\\z', "wsdc_id": 7}, {"entry_id": "gone", "wsdc_id": 8}],
    }
    after = {
        "registry_placements": [
            {**row, "value": "changed"} for row in before["registry_placements"]
        ],
        "entries": [{"entry_id": 'a"\\z', "wsdc_id": None}, {"entry_id": "added", "wsdc_id": None}],
    }
    for table, rows in before.items():
        directory = tmp_path / "data" / table
        directory.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(rows), directory / "part.parquet")
    options = {"changed_at": NOW, "run_id": "run"}
    assert list(
        changes(after, tmp_path, keys, scratch=tmp_path / "compare.sqlite", **options)
    ) == original(after, tmp_path, keys, **options)
    assert not (tmp_path / "compare.sqlite").exists()


def test_spool_preserves_discovery_order_and_stable_json_sort_then_replaces_pass(tmp_path):
    keys = ("changed_at", "table", "record_key", "field")
    rows = [
        {"changed_at": NOW, "table": "entries", "record_key": key, "field": field, "ordinal": index}
        for index, (key, field) in enumerate(
            [('["z"]', None), ('["a"]', "name"), ('["a"]', None), ('["a"]', "name")]
        )
    ]
    path = tmp_path / "delta.sqlite"
    with generated_delta(path, keys) as delta:
        delta.replace(iter(rows))
        assert list(delta) == rows
        assert list(delta.sorted_rows()) == sorted(rows, key=lambda row: sort_key(row, keys))
        assert list(delta.sorted_rows()) == list(delta.sorted_rows())
        delta.replace(iter(rows[:1]))
        assert list(delta) == rows[:1]
    assert not path.exists()


def test_generated_spool_does_not_accumulate_payloads_in_python_memory(tmp_path):
    count = 30_000
    keys = ("record_key",)
    tracemalloc.start()
    try:
        with generated_delta(tmp_path / "delta.sqlite", keys) as delta:
            delta.replace(
                {"record_key": str(index), "old_value": str(index) + "x" * 4096}
                for index in range(count)
            )
            assert sum(1 for _ in delta) == count
            assert sum(1 for _ in delta.sorted_rows()) == count
            _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # An eager collection holds over 120 MiB of distinct payload strings alone.
    assert peak < 8 * 1024 * 1024


def test_spool_exception_discards_private_intermediates(tmp_path):
    def broken():
        yield {"id": "one"}
        raise RuntimeError("failed source")

    path = tmp_path / "delta.sqlite"
    with pytest.raises(RuntimeError, match="failed source"):
        with generated_delta(path, ("id",)) as delta:
            delta.replace(broken())
    assert not path.exists()


def test_candidate_streams_large_generated_delta_and_card_count_without_eager_helper(
    tmp_path, monkeypatch
):
    import swingset.build.builder as builder

    def eager_forbidden(*args, **kwargs):
        raise AssertionError("production must not materialize its delta")

    monkeypatch.setattr(builder, "_changelog", eager_forbidden)
    count = 17_000
    rows = [{"entry_id": str(index), "link_status": "unmatched"} for index in range(count)]
    data = BuildInput(
        tables={"entries": rows},
        schemas=SCHEMAS,
        primary_keys=PRIMARY_KEYS,
        revisions={},
        captured_file_hashes={},
        input_bundle_hash="test",
    )
    meta = BuildMetadata(
        run_id="test",
        repository_commit="test",
        expected_parent=None,
        schema_version=1,
        versions={},
        card=b"",
        built_at=NOW,
    )
    result = build_candidate(
        tmp_path,
        data,
        meta,
        card_renderer=lambda final: str(len(final.tables["changelog"])).encode(),
    )
    parquet = pq.ParquetFile(result.path / "data/changelog/changelog.parquet")
    assert parquet.metadata.num_rows == count
    assert (
        max(parquet.metadata.row_group(i).num_rows for i in range(parquet.num_row_groups)) <= 8192
    )
    assert (result.path / "README.md").read_text() == str(count)
    assert (
        json.loads((result.path / "_meta/manifest.json").read_text())["row_counts"]["changelog"]
        == count
    )
    assert not list(result.path.rglob("*.sqlite"))
    assert not list((tmp_path / "candidates").glob(".*.tmp-*"))


def test_candidate_delta_files_are_deterministic_and_failed_write_is_incomplete(
    tmp_path, monkeypatch
):
    import swingset.build.builder as builder

    data = BuildInput(
        tables={
            "entries": [
                {"entry_id": "b", "link_status": "unmatched"},
                {"entry_id": "a", "link_status": "unmatched"},
            ]
        },
        schemas=SCHEMAS,
        primary_keys=PRIMARY_KEYS,
        revisions={},
        captured_file_hashes={},
        input_bundle_hash="test",
    )
    meta = BuildMetadata(
        run_id="test",
        repository_commit="test",
        expected_parent=None,
        schema_version=1,
        versions={},
        card=b"card",
        built_at=NOW,
    )
    first = build_candidate(tmp_path / "one", data, meta)
    reversed_data = replace(data, tables={"entries": list(reversed(data.tables["entries"]))})
    second = build_candidate(tmp_path / "two", reversed_data, meta)
    assert (first.path / "data/changelog/changelog.parquet").read_bytes() == (
        second.path / "data/changelog/changelog.parquet"
    ).read_bytes()
    assert first.content_hash == second.content_hash

    def fail_after_delta_created(*args, **kwargs):
        assert list((tmp_path / "failed/candidates").glob("*/delta.sqlite"))
        raise OSError("injected parquet write failure")

    monkeypatch.setattr(builder, "_write_parquet", fail_after_delta_created)
    with pytest.raises(OSError, match="injected parquet"):
        build_candidate(tmp_path / "failed", data, meta)
    assert list((tmp_path / "failed/candidates").iterdir()) == []


def test_non_utc_generated_timestamp_is_normalized_before_history_merge(tmp_path):
    data = BuildInput(
        tables={"entries": [{"entry_id": "one", "link_status": "unmatched"}]},
        schemas=SCHEMAS,
        primary_keys=PRIMARY_KEYS,
        revisions={},
        captured_file_hashes={},
        input_bundle_hash="first",
    )
    meta = BuildMetadata(
        run_id="test",
        repository_commit="test",
        expected_parent=None,
        schema_version=1,
        versions={},
        card=b"card",
        built_at=datetime(2026, 9, 13, 10, tzinfo=UTC),
    )
    first = build_candidate(tmp_path, data, meta)
    (first.path / "PUBLISHED").write_text('{"commit":"published"}')
    (tmp_path / "baseline").symlink_to(first.path)
    next_data = replace(
        data,
        tables={"entries": [{"entry_id": "one", "link_status": "unmatched", "name_raw": "Name"}]},
        input_bundle_hash="second",
    )
    second = build_candidate(
        tmp_path,
        next_data,
        replace(meta, built_at=datetime(2026, 9, 13, 8, tzinfo=timezone(timedelta(hours=-4)))),
    )
    rows = pq.read_table(second.path / "data/changelog/changelog.parquet").to_pylist()
    assert [row["changed_at"].hour for row in rows] == [10, 12]
    assert [sort_key(row, PRIMARY_KEYS["changelog"]) for row in rows] == sorted(
        sort_key(row, PRIMARY_KEYS["changelog"]) for row in rows
    )


def test_insert_failure_closes_comparison_even_when_traceback_is_retained(tmp_path, monkeypatch):
    import swingset.build.changelog as changelog

    opened = []

    class Connection(sqlite3.Connection):
        closed = False

        def executemany(self, sql, parameters):
            if sql == "INSERT INTO delta VALUES (?,?,?)":
                iterator = iter(parameters)
                next(iterator)  # The comparison generator now owns an open database.
                raise OSError("injected spool failure")
            return super().executemany(sql, parameters)

        def close(self):
            self.closed = True
            super().close()

    def tracked(path):
        # Production pragmas do not affect deterministic handle ownership.
        conn = sqlite3.connect(path, factory=Connection)
        opened.append(conn)
        return conn

    monkeypatch.setattr(changelog, "_open", tracked)
    data = BuildInput(
        tables={"entries": [{"entry_id": "one", "link_status": "unmatched"}]},
        schemas=SCHEMAS,
        primary_keys=PRIMARY_KEYS,
        revisions={},
        captured_file_hashes={},
        input_bundle_hash="test",
    )
    meta = BuildMetadata(
        run_id="test",
        repository_commit="test",
        expected_parent=None,
        schema_version=1,
        versions={},
        card=b"",
        built_at=NOW,
    )
    with pytest.raises(OSError, match="injected spool") as retained:
        build_candidate(tmp_path, data, meta)
    assert retained.traceback
    assert len(opened) == 2 and all(conn.closed for conn in opened)
    assert list((tmp_path / "candidates").iterdir()) == []
