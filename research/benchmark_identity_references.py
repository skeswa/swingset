"""Measure retained source-reference coverage without migrating or changing state."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from swingset.state.identity_references import ReferenceReader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--events", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--immutable-checkpoint",
        action="store_true",
        help="Read a declared immutable checkpoint without creating WAL sidecars; never use for live state.",
    )
    args = parser.parse_args()
    if args.events < 1:
        parser.error("--events must be positive")
    state = args.state if args.state.suffix == ".sqlite" else args.state / "state.sqlite"
    results = []
    immutable = args.immutable_checkpoint or "checkpoints" in state.resolve().parts
    uri = f"{state.resolve().as_uri()}?mode=ro" + ("&immutable=1" if immutable else "")
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        events = conn.execute(
            "SELECT event_id,COUNT(*) FROM entries GROUP BY event_id "
            "ORDER BY COUNT(*) DESC,event_id LIMIT ?",
            (args.events,),
        ).fetchall()
        for event_id, _count in events:
            reader = ReferenceReader(conn)
            started = time.monotonic()
            counts = {}
            missing = []
            for kind, table in (("entry", "entries"), ("judge", "judges")):
                totals = dict.fromkeys(
                    (
                        "subjects",
                        "no_reference",
                        "multiple_references",
                        "default_id_without_reference",
                    ),
                    0,
                )
                for row in conn.execute(f"SELECT * FROM {table} WHERE event_id=?", (event_id,)):
                    bindings = reader.for_subject(kind, row[f"{kind}_id"])
                    totals["subjects"] += 1
                    totals["no_reference"] += not bindings
                    totals["multiple_references"] += len(bindings) > 1
                    if not bindings and row["wsdc_id"] is not None:
                        totals["default_id_without_reference"] += 1
                        if len(missing) < 10:
                            missing.append(
                                {
                                    "kind": kind,
                                    "subject_id": row[f"{kind}_id"],
                                    "snapshot_id": row["snapshot_id"],
                                }
                            )
                counts[kind] = totals
            results.append(
                {
                    "event_id": event_id,
                    "seconds": time.monotonic() - started,
                    "counts": counts,
                    "missing_default_reference_examples": missing,
                }
            )
        schema = conn.execute("PRAGMA user_version").fetchone()[0]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "state": str(state),
        "schema_version": schema,
        "immutable_checkpoint_read": immutable,
        "selection": "Largest events by entry count; ties ordered by event ID",
        "interpretation": "Locator coverage only; multiple references may be valid rounds, and none are identity adjudications.",
        "events": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"events": len(results), "output": str(args.output)}))


if __name__ == "__main__":
    main()
