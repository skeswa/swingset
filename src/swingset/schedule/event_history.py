"""Read complete pages of recorded event transitions without inventing older history."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

STREAMS = {
    "accounting": "event_accounting_receipts",
    "enumerations": "source_event_enumerations",
    "page_retirement": "event_retirement_receipts",
    "source_event_retirement": "source_event_retirement_receipts",
}


def report(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_ref: str,
    stream: str = "accounting",
    after: int = 0,
    through: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Keyset pages pin a receipt high-water mark; counts cover only this page."""
    if stream not in STREAMS or not source or not source_ref:
        raise ValueError("source, source_ref and a known history stream are required")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("history limit must be an integer in 1..100")
    if type(after) is not int or not 0 <= after < 2**63:
        raise ValueError("invalid history cursor")
    if through is not None and (type(through) is not int or not after <= through < 2**63):
        raise ValueError("invalid history high-water mark")
    table = STREAMS[stream]
    result: dict[str, Any] = dict(
        format="recorded-source-event-history-v1",
        source=source,
        source_ref=source_ref,
        stream=stream,
        after=after,
        through=through,
        limit=limit,
        rows=[],
        page_counts={},
        count_scope="returned_page",
        next_cursor=None,
        reached_high_water=False,
        complete_lifetime_history=False,
        unobserved_transitions="unknown; sampled observations do not record every real-world change",
        legacy_history="unknown before first recorded receipt",
        artifact_checks=False,
    )
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
        return {**result, "supported": False, "reason": "history_schema_unavailable"}
    identifier = "rowid" if stream == "enumerations" else "receipt_id"
    time_column = "recorded_at" if stream == "enumerations" else "observed_at"
    first = conn.execute(
        f"SELECT {identifier},{time_column} FROM {table} WHERE source=? AND source_ref=? ORDER BY {identifier} LIMIT 1",
        (source, source_ref),
    ).fetchone()
    last = conn.execute(
        f"SELECT {identifier} FROM {table} WHERE source=? AND source_ref=? ORDER BY {identifier} DESC LIMIT 1",
        (source, source_ref),
    ).fetchone()
    recorded_max = last[0] if last else 0
    if through is None:
        through = recorded_max
    if not after <= through <= recorded_max:
        raise ValueError("history cursor exceeds recorded high-water mark")
    if first is not None and first[0] > through:
        first = None
    if stream == "accounting":
        columns = "receipt_id,enumeration_id,previous_receipt_id,assessment,transition,observed_at,earliest_checked_at,valid_until"
    elif stream == "enumerations":
        columns = "rowid AS receipt_id,enumeration_id,predecessor_id,generation_id,decision_id,recorded_at AS observed_at,pagination,pagination_reason"
    else:
        columns = "receipt_id,enumeration_id,predecessor_id,generation_id,decision_id,observed_at,proof_digest"
    cursor = conn.execute(
        f"SELECT {columns} FROM {table} WHERE source=? AND source_ref=? "
        f"AND {identifier}>? AND {identifier}<=? ORDER BY {identifier} LIMIT ?",
        (source, source_ref, after, through, limit + 1),
    )
    names = [column[0] for column in cursor.description]
    rows = [dict(zip(names, row, strict=True)) for row in cursor]
    selected = rows[:limit]
    counts: dict[str, int] = {}
    for row in selected:
        kind = row.get("transition", stream)
        counts[kind] = counts.get(kind, 0) + 1
    return {
        **result,
        "supported": True,
        "through": through,
        "recording_started_at": first[1] if first else None,
        "first_recorded_receipt_id": first[0] if first else None,
        "rows": selected,
        "page_counts": counts,
        "next_cursor": selected[-1]["receipt_id"] if len(rows) > limit else None,
        "reached_high_water": len(rows) <= limit,
        "recorded_prefix_included": after == 0,
        "meaning": "Historical observations, not current completion, successful work or publication authority",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--stream", choices=tuple(STREAMS), default="accounting")
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--through", type=int)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    from swingset.state.db import open_database

    with open_database(args.state, read_only=True, lock=False) as database:
        with database.transaction(immediate=False):
            value = report(
                database.connection,
                source=args.source,
                source_ref=args.source_ref,
                stream=args.stream,
                after=args.after,
                through=args.through,
                limit=args.limit,
            )
        print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
