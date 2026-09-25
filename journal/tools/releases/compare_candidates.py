#!/usr/bin/env python3
"""Compare the published rows of two built candidates, table by table.

For each table: row counts, a keyed digest of each side, and the keys added,
removed and changed, capped. Keys come from `build.schema.PRIMARY_KEYS`. A
row's digest leaves out the provenance columns a re-run rewrites without a
data change (`run_id`, `first_seen_at`, `last_seen_at`, `asserted_at`), so
two candidates built from the same data compare equal. For changed rows it
counts which columns differ. Runs offline and reads only the two
directories. Memory is capped (`--memory`, default 1 GB); DuckDB spills the
rest to a temporary directory it removes, so it fits the 8 GB worker.

The phase 1 deploy runs it before the switch against the published candidate
and after the drain against the first new candidate (architecture plan, deploy
steps 3 and 6).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import duckdb

from swingset.build.schema import PRIMARY_KEYS

PROVENANCE = ("run_id", "first_seen_at", "last_seen_at", "asserted_at")


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _files(candidate: Path, table: str) -> list[Path]:
    return sorted((candidate / "data" / table).glob("*.parquet"))


def _tables(candidate: Path) -> set[str]:
    data = candidate / "data"
    return {path.name for path in data.iterdir() if path.is_dir()} if data.is_dir() else set()


def _view(db: duckdb.DuckDBPyConnection, name: str, files: list[Path]) -> list[str]:
    paths = ", ".join("'" + str(path).replace("'", "''") + "'" for path in files)
    db.execute(
        f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet([{paths}], "
        "hive_partitioning=false, union_by_name=true)"
    )
    return [str(row[0]) for row in db.execute(f"DESCRIBE {name}").fetchall()]


def _keyed(db: duckdb.DuckDBPyConnection, name: str, keys: list[str], values: list[str]) -> None:
    """One row per key: the key as JSON and an order-free digest of its rows."""
    key = "to_json(struct_pack(" + ", ".join(f"{_quote(c)} := {_quote(c)}" for c in keys) + "))"
    row = (
        "md5_number(CAST(to_json(struct_pack("
        + ", ".join(f"{_quote(c)} := {_quote(c)}" for c in values)
        + ")) AS VARCHAR))"
    )
    db.execute(
        f"CREATE OR REPLACE TEMP TABLE {name}_keyed AS SELECT CAST({key} AS VARCHAR) AS k, "
        f"bit_xor({row}) AS d, count(*) AS n FROM {name} GROUP BY ALL"
    )


def compare_table(
    db: duckdb.DuckDBPyConnection,
    table: str,
    base: list[Path],
    new: list[Path],
    *,
    omit: tuple[str, ...],
    limit: int,
) -> dict[str, Any]:
    base_columns = _view(db, "base", base)
    new_columns = _view(db, "new", new)
    shared = [c for c in base_columns if c in new_columns]
    values = [c for c in shared if c not in omit]
    declared = list(PRIMARY_KEYS.get(table, ()))
    keys = declared if declared and all(c in shared for c in declared) else values
    _keyed(db, "base", keys, values)
    _keyed(db, "new", keys, values)

    def scalar(query: str) -> Any:
        return db.execute(query).fetchone()[0]

    def sample(query: str) -> list[Any]:
        found = db.execute(query + f" ORDER BY 1 LIMIT {limit}").fetchall()
        return [json.loads(row[0]) for row in found]

    report: dict[str, Any] = {
        "rows": {side: scalar(f"SELECT count(*) FROM {side}") for side in ("base", "new")},
        # Order-free: the XOR of each key's digest with the key's own.
        "digest": {
            side: str(
                scalar(f"SELECT coalesce(bit_xor(md5_number(k || ':' || d)), 0) FROM {side}_keyed")
            )
            for side in ("base", "new")
        },
        "key": keys,
        "left_out": [c for c in shared if c in omit],
        "columns_only_in_base": [c for c in base_columns if c not in new_columns],
        "columns_only_in_new": [c for c in new_columns if c not in base_columns],
        "duplicate_keys": {
            side: scalar(f"SELECT count(*) FROM {side}_keyed WHERE n > 1")
            for side in ("base", "new")
        },
    }
    joined = "FROM base_keyed b FULL JOIN new_keyed n USING (k)"
    for name, where in (
        ("added", "b.d IS NULL"),
        ("removed", "n.d IS NULL"),
        ("changed", "b.d IS NOT NULL AND n.d IS NOT NULL AND b.d <> n.d"),
    ):
        report[name] = scalar(f"SELECT count(*) {joined} WHERE {where}")
        report[name + "_keys"] = sample(f"SELECT k {joined} WHERE {where}")
    report["identical"] = report["digest"]["base"] == report["digest"]["new"] and not (
        report["columns_only_in_base"] or report["columns_only_in_new"]
    )
    if report["changed"] and keys != values and not any(report["duplicate_keys"].values()):
        on = " AND ".join(f"b.{_quote(c)} IS NOT DISTINCT FROM n.{_quote(c)}" for c in keys)
        counts = ", ".join(
            f"count(*) FILTER (WHERE b.{_quote(c)} IS DISTINCT FROM n.{_quote(c)})"
            for c in values
            if c not in keys
        )
        found = db.execute(f"SELECT {counts} FROM base b JOIN new n ON {on}").fetchone()
        report["changed_columns"] = {
            column: count
            for column, count in zip([c for c in values if c not in keys], found, strict=True)
            if count
        }
    return report


def compare(
    base: Path,
    new: Path,
    *,
    omit: tuple[str, ...] = PROVENANCE,
    limit: int = 20,
    memory: str = "1GB",
) -> dict[str, Any]:
    base_tables, new_tables = _tables(base), _tables(new)
    tables: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="compare-candidates-") as spill:
        db = duckdb.connect(
            config={
                "memory_limit": memory,
                "threads": 2,
                "temp_directory": spill,
                "preserve_insertion_order": False,
            }
        )
        try:
            for table in sorted(base_tables & new_tables):
                base_files, new_files = _files(base, table), _files(new, table)
                if not base_files or not new_files:
                    tables[table] = {
                        "error": "no parquet files",
                        "base": len(base_files),
                        "new": len(new_files),
                    }
                    continue
                tables[table] = compare_table(
                    db, table, base_files, new_files, omit=omit, limit=limit
                )
        finally:
            db.close()
    differing = sorted(
        table for table, value in tables.items() if not value.get("identical", False)
    )
    return {
        "base": str(base),
        "new": str(new),
        "left_out": list(omit),
        "limit": limit,
        "tables_only_in_base": sorted(base_tables - new_tables),
        "tables_only_in_new": sorted(new_tables - base_tables),
        "differing_tables": differing,
        "tables": tables,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("base", type=Path, help="candidate directory containing data/")
    parser.add_argument("new", type=Path, help="candidate directory containing data/")
    parser.add_argument("output", type=Path, nargs="?", help="optional JSON output path")
    parser.add_argument("--limit", type=int, default=20, help="keys listed per kind of difference")
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        choices=PROVENANCE,
        help="compare this provenance column too (repeatable)",
    )
    parser.add_argument(
        "--omit",
        action="append",
        default=[],
        help="also leave out this column, such as evidence_cutoff (repeatable)",
    )
    parser.add_argument("--memory", default="1GB", help="DuckDB memory limit (default 1GB)")
    args = parser.parse_args(argv)
    for path in (args.base, args.new):
        if not (path / "data").is_dir():
            parser.error(f"not a candidate directory: {path}")
    omit = tuple(column for column in PROVENANCE if column not in args.keep) + tuple(args.omit)
    report = compare(
        args.base.resolve(), args.new.resolve(), omit=omit, limit=args.limit, memory=args.memory
    )
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output:
        args.output.write_text(text + "\n")
    else:
        print(text)
    print(
        f"{len(report['differing_tables'])} of {len(report['tables'])} tables differ: "
        + (", ".join(report["differing_tables"]) or "none"),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
