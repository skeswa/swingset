"""Read-only projected impact of explicitly unsupported numeric/Solo scoring."""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from swingset.model.canonical import Contest
from swingset.project.contests import project_event
from swingset.project.process import PROJECTOR_VERSION


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    current = sqlite3.connect(f"file:{args.state / 'state.sqlite'}?mode=ro", uri=True)
    baseline = sqlite3.connect(f"file:{args.baseline / 'state.sqlite'}?mode=ro", uri=True)
    current.row_factory = baseline.row_factory = sqlite3.Row
    events = [
        str(r[0])
        for r in current.execute(
            "SELECT DISTINCT m.event_id FROM watches w JOIN observations o USING(watch_id) "
            "JOIN source_event_map m ON m.source=w.source AND m.source_ref=o.scope_id "
            "WHERE w.parser IN ('eepro.round','wdr.rounds') AND o.kind='round_sheet' AND m.event_id IS NOT NULL"
        )
    ]
    report = {
        "projector_version": PROJECTOR_VERSION,
        "state": str(args.state),
        "baseline": str(args.baseline),
        "projected_events": len(events),
        "database_mutations": 0,
        "newly_unsupported": [],
    }
    totals = Counter()
    try:
        for event in events:
            projection = project_event(current, event, "2026-09-13", "read-only-review")
            for row in projection.rows:
                if not isinstance(row, Contest) or row.parse_status != "unsupported":
                    continue
                prior = baseline.execute(
                    "SELECT parse_status FROM contests WHERE contest_id=?", (row.contest_id,)
                ).fetchone()
                if prior is None or prior[0] == "unsupported":
                    continue
                counts = {}
                for table in ("rounds", "entries", "placements"):
                    counts[table] = baseline.execute(
                        f"SELECT count(*) FROM {table} WHERE contest_id=?", (row.contest_id,)
                    ).fetchone()[0]
                for table in ("callbacks", "callback_marks"):
                    counts[table] = baseline.execute(
                        f"SELECT count(*) FROM {table} t JOIN rounds r USING(round_id) WHERE r.contest_id=?",
                        (row.contest_id,),
                    ).fetchone()[0]
                counts["final_marks"] = baseline.execute(
                    "SELECT count(*) FROM final_marks f JOIN placements p USING(placement_id) WHERE p.contest_id=?",
                    (row.contest_id,),
                ).fetchone()[0]
                totals.update(counts)
                report["newly_unsupported"].append(
                    {
                        "contest_id": row.contest_id,
                        "source": row.source,
                        "name": row.name_raw,
                        "prior_parse_status": prior[0],
                        "prior_rows": counts,
                    }
                )
        report["prior_rows_in_newly_unsupported_contests"] = dict(totals)
    finally:
        current.close()
        baseline.close()
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "projected_events": len(events),
                "newly_unsupported": len(report["newly_unsupported"]),
                "prior_rows": dict(totals),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
