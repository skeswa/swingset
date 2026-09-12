#!/usr/bin/env python3
"""Summarize intake gaps from a captured SQLite state without fetching sources."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    capture = json.loads((args.capture / "capture.json").read_text())
    now = capture["started_at"]
    conn = sqlite3.connect(f"file:{args.capture / 'state.sqlite'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    queries = {
        "watch_coverage": "SELECT source,kind,state,COUNT(*) watches,SUM(last_checked_at IS NULL) never_checked,SUM(current_observation_snapshot_id IS NULL) without_observation FROM watches GROUP BY source,kind,state ORDER BY source,kind,state",
        "latest_snapshot_health": "SELECT w.source,s.classification,s.parse_status,COUNT(*) watches FROM watches w JOIN snapshots s ON s.snapshot_id=(SELECT snapshot_id FROM snapshots WHERE watch_id=w.watch_id ORDER BY fetched_at DESC,snapshot_id DESC LIMIT 1) GROUP BY w.source,s.classification,s.parse_status ORDER BY 1,2,3",
        "open_findings": "SELECT kind,severity,COUNT(*) findings FROM findings WHERE closed_at IS NULL GROUP BY kind,severity ORDER BY 1,2",
        "pending_work": "SELECT stage,COUNT(*) work FROM pending_work GROUP BY stage",
        "cursors": "SELECT name,value FROM cursors ORDER BY name",
        "budget": "SELECT * FROM host_budget WHERE day=substr(?,1,10) ORDER BY host",
        "registry_due": "SELECT CASE WHEN notes LIKE 'confirmation:%' THEN 'confirmation' ELSE COALESCE(notes,'other') END purpose,COUNT(*) watches FROM watches WHERE source='wsdc_registry' AND state!='gone' AND next_check_at<=? GROUP BY 1 ORDER BY 1",
        "registry_probe_watches": "SELECT source_ref,last_checked_at,next_check_at FROM watches WHERE notes='probe' ORDER BY CAST(substr(source_ref,6) AS INTEGER)",
        "paused_hosts": "SELECT host,paused_until,pause_reason FROM hosts WHERE paused_until>?",
        "backup": "SELECT key,value FROM meta WHERE key LIKE 'last_backup%' ORDER BY key",
        "source_events": "SELECT source,COUNT(*) events,SUM(NOT EXISTS(SELECT 1 FROM source_event_map m WHERE m.source=source_events.source AND m.source_ref=source_events.source_ref)) unmapped FROM source_events GROUP BY source",
    }
    report = {"capture": capture}
    for name, sql in queries.items():
        report[name] = [dict(r) for r in conn.execute(sql, (now,) if "?" in sql else ())]
    manifest = json.loads((args.capture / "candidate/_meta/manifest.json").read_text())
    report["public_state_counts"] = [
        {
            "table": table,
            "public": count,
            "state": conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0],
        }
        for table, count in manifest["row_counts"].items()
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    ]
    failures = [
        dict(r)
        for r in conn.execute(
            "SELECT finding_id,kind,subject_id,summary,evidence_json FROM findings WHERE closed_at IS NULL AND kind IN ('parse_failure','invalid_response','missing_identity') ORDER BY kind,subject_id"
        )
    ]
    with (args.output / "operations-failures.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["finding_id", "kind", "subject_id", "summary", "evidence_json"]
        )
        writer.writeheader()
        writer.writerows(failures)
    for filename, sql, parameters in [
        (
            "operations-unfetched-watches.csv",
            "SELECT watch_id,source,kind,source_ref,url,parent_watch_id,state,priority,next_check_at FROM watches WHERE last_checked_at IS NULL AND source!='crosscheck' ORDER BY source,url",
            (),
        ),
        (
            "operations-registry-refresh-due.csv",
            "SELECT watch_id,source_ref,url,notes,last_checked_at,next_check_at FROM watches WHERE source='wsdc_registry' AND state!='gone' AND next_check_at<=? ORDER BY source_ref",
            (now,),
        ),
    ]:
        cursor = conn.execute(sql, parameters)
        with (args.output / filename).open("w", newline="") as stream:
            row_writer = csv.writer(stream)
            row_writer.writerow([column[0] for column in cursor.description])
            row_writer.writerows(cursor)
    (args.output / "operations-summary.json").write_text(json.dumps(report, indent=2) + "\n")
    conn.close()


if __name__ == "__main__":
    main()
