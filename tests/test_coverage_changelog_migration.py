"""V4 year keys retain distinct history when H16 adds coverage scope keys."""

import json
from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.parquet as pq

from swingset.build.builder import _changelog
from swingset.build.schema import PRIMARY_KEYS


def test_old_year_coverage_does_not_collapse_to_one_missing_scope_key(tmp_path):
    baseline = tmp_path / "baseline"
    directory = baseline / "data" / "coverage"
    directory.mkdir(parents=True)
    old = [
        {"year": year, "source": "wsdc_registry", "via": "registry", "events": year - 2000}
        for year in (2010, 2011)
    ]
    pq.write_table(pa.Table.from_pylist(old), directory / "part.parquet")
    delta = _changelog(
        {"coverage": []},
        baseline,
        PRIMARY_KEYS,
        changed_at=datetime(2026, 9, 13, tzinfo=UTC),
        run_id="coverage-migration",
    )
    assert len(delta) == 2
    assert {json.loads(row["old_value"])["year"] for row in delta} == {2010, 2011}
    assert {tuple(json.loads(row["record_key"])) for row in delta} == {
        ("year", "2010", "wsdc_registry", "registry"),
        ("year", "2011", "wsdc_registry", "registry"),
    }


def test_scope_columns_extend_existing_year_rows_in_changelog(tmp_path):
    baseline = tmp_path / "baseline"
    directory = baseline / "data" / "coverage"
    directory.mkdir(parents=True)
    old = [
        {"year": year, "source": "wsdc_registry", "via": "registry", "events": year - 2000}
        for year in (2010, 2011)
    ]
    pq.write_table(pa.Table.from_pylist(old), directory / "part.parquet")
    current = [{**row, "scope_kind": "year", "scope_id": str(row["year"])} for row in old]
    delta = _changelog(
        {"coverage": current},
        baseline,
        PRIMARY_KEYS,
        changed_at=datetime(2026, 9, 13, tzinfo=UTC),
        run_id="coverage-migration",
    )
    assert len(delta) == 4
    assert {row["change_type"] for row in delta} == {"updated"}
    assert {row["field"] for row in delta} == {"scope_kind", "scope_id"}
    assert {json.loads(row["record_key"])[1] for row in delta} == {"2010", "2011"}
