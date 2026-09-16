#!/usr/bin/env python3
"""Profile null and blank fields in a pinned publication; absence is not always a defect."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import duckdb


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    conn = duckdb.connect()
    manifest = json.loads((args.capture / "candidate/_meta/manifest.json").read_text())
    rows = []
    for table in manifest["row_counts"]:
        path = str((args.capture / "candidate/data" / table / "*.parquet").resolve()).replace(
            "'", "''"
        )
        conn.execute(
            f"CREATE VIEW \"{table}\" AS SELECT * FROM read_parquet('{path}', hive_partitioning=false)"
        )
        columns = conn.execute(f'DESCRIBE "{table}"').fetchall()
        expressions = []
        for name, dtype, *_ in columns:
            quoted = '"' + name.replace('"', '""') + '"'
            expressions.append(f"count(*) FILTER (WHERE {quoted} IS NULL)")
            expressions.append(
                f"count(*) FILTER (WHERE trim({quoted})='')" if dtype == "VARCHAR" else "0"
            )
        counts = conn.execute(f'SELECT {",".join(expressions)} FROM "{table}"').fetchone()
        assert counts is not None
        for i, (name, dtype, *_) in enumerate(columns):
            rows.append(
                {
                    "table": table,
                    "column": name,
                    "type": dtype,
                    "rows": manifest["row_counts"][table],
                    "nulls": counts[2 * i],
                    "blank_strings": counts[2 * i + 1],
                }
            )
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "field-completeness.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["table", "column", "type", "rows", "nulls", "blank_strings"]
        )
        writer.writeheader()
        writer.writerows(rows)
    conn.close()


if __name__ == "__main__":
    main()
