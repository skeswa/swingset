"""Compatibility oracle captured from pinned P19 before the memory fix.
Source: /nix/store/lz99diyqwf6f95i8ggvx3ji0lvrjpfxd-source/src/swingset/build/builder.py
Only _changelog is copied; imports below make the captured function standalone."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from swingset.build.builder import _row_key


def _changelog(
    current: Mapping[str, list[dict[str, Any]]],
    baseline: Path | None,
    keys: Mapping[str, tuple[str, ...]],
    *,
    changed_at: datetime,
    run_id: str,
) -> list[dict[str, Any]]:
    if baseline is None:
        old: dict[str, list[dict[str, Any]]] = {}
    else:
        old = {}
        for table in current:
            if table == "changelog":
                continue
            files = list((baseline / "data" / table).glob("*.parquet"))
            old[table] = pq.read_table(files).to_pylist() if files else []
    delta: list[dict[str, Any]] = []
    for table, new_rows in current.items():
        if table == "changelog" or table not in keys:
            continue
        key_fields = keys[table]
        before = {_row_key(row, key_fields): row for row in old.get(table, [])}
        after = {_row_key(row, key_fields): row for row in new_rows}
        for row_key in sorted(before.keys() | after.keys(), key=repr):
            old_row, new_row = before.get(row_key), after.get(row_key)
            fields: Sequence[str | None]
            if old_row is None or new_row is None:
                fields = [None]
            else:
                fields = sorted(
                    field
                    for field in old_row.keys() | new_row.keys()
                    if old_row.get(field) != new_row.get(field)
                )
            for field in fields:
                old_value = old_row if field is None else old_row.get(field) if old_row else None
                new_value = new_row if field is None else new_row.get(field) if new_row else None
                reason = (
                    "suppression"
                    if (new_row and new_row.get("link_status") == "suppressed")
                    else "link_downgraded"
                    if table in {"entries", "judges", "identity_links", "placements"}
                    and field is not None
                    and ("wsdc_id" in field or field.startswith("registry_points_"))
                    and old_value is not None
                    and new_value is None
                    else "new_source_data"
                )
                delta.append(
                    {
                        "changed_at": changed_at,
                        "run_id": run_id,
                        "table": table,
                        "record_key": json.dumps(row_key, default=str),
                        "field": field,
                        "old_value": json.dumps(old_value, sort_keys=True, default=str),
                        "new_value": json.dumps(new_value, sort_keys=True, default=str),
                        "change_type": "added"
                        if old_row is None
                        else "removed"
                        if new_row is None
                        else "updated",
                        "reason": reason,
                    }
                )
    return delta
