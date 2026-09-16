"""Time two inventory scans on an explicitly disposable state copy, offline.

Create the copy with SQLite backup from an immutable checkpoint and link its
blobs/extracts read-only. This script refuses state beneath /var/lib/swingset.
It records measurements, fixed cohort counts, query plans and input DB identity.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sqlite3
import statistics
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from swingset.state.db import open_database
from swingset.state.requirement_report import inventory
from swingset.state.requirements import capture_cohort, scan


def clone_checkpoint(source: Path, target: Path) -> dict:
    """Clone an immutable checkpoint; never open its database through the runtime."""
    if not (source / "checkpoint.json").is_file():
        raise ValueError("clone source must be an immutable checkpoint with its manifest")
    if (source / "state.sqlite-wal").exists():
        raise ValueError("checkpoint has a WAL sidecar; verify it before cloning")
    target.mkdir(exist_ok=False)
    started = perf_counter()
    with (source / "state.sqlite").open("rb") as stream:
        source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    with closing(
        sqlite3.connect(
            (source / "state.sqlite").resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        )
    ) as original:
        with closing(sqlite3.connect(target / "state.sqlite")) as copied:
            original.backup(copied)
            copied.commit()
        schema = original.execute("PRAGMA user_version").fetchone()[0]
    for name in ("blobs", "extracts"):
        (target / name).symlink_to((source / name).resolve(), target_is_directory=True)
    return {
        "source_database_sha256": source_hash,
        "source_schema": schema,
        "seconds": perf_counter() - started,
        "immutable_source_connection": True,
    }


def compare_reports(conn, now, previous_module: Path) -> dict:
    """Compare immutable previous code on the same disposable database snapshot."""
    spec = importlib.util.spec_from_file_location(
        "swingset.state.requirement_report_previous_benchmark", previous_module
    )
    if spec is None or spec.loader is None:
        raise ValueError("previous report module cannot be loaded")
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    measurements = []
    for iteration in range(3):
        order = [("schema9_report", previous.inventory), ("schema10_report", inventory)]
        if iteration % 2:
            order.reverse()
        for label, implementation in order:
            started = perf_counter()
            report = implementation(conn, now)
            measurements.append(
                {
                    "iteration": iteration + 1,
                    "report": label,
                    "seconds": perf_counter() - started,
                    "states": report["states"],
                    "cohort_count": len(report["cohorts"]),
                }
            )
    return {
        "same_database_alternating_reports": measurements,
        "old_module_path": str(previous_module),
        "old_module_sha256": hashlib.sha256(previous_module.read_bytes()).hexdigest(),
        "median_seconds": {
            label: statistics.median(
                row["seconds"] for row in measurements if row["report"] == label
            )
            for label in ("schema9_report", "schema10_report")
        },
        "method": "Three alternating reports per implementation over identical scratch rows, cohorts and clock; previous code comes from the explicit immutable module path.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--max-pages", type=int, default=2000)
    parser.add_argument("--previous-report-module", type=Path)
    parser.add_argument("--clone-checkpoint", action="store_true")
    args = parser.parse_args()
    state = args.state.resolve()
    if state.is_relative_to("/var/lib/swingset") or state.name not in {
        "swingset-h11-review",
        "swingset-h11-schema10-review",
    }:
        raise SystemExit("requires an explicitly named disposable H11 review state")
    if args.output.exists():
        raise SystemExit("receipt already exists")
    now = datetime.now(UTC)
    receipt = {
        "checkpoint": args.checkpoint,
        "scratch_state": str(state),
        "at": now.isoformat(),
        "network_requests": 0,
        "live_mutations": 0,
        "publication": "held",
        "scans": [],
    }
    if args.clone_checkpoint:
        receipt["clone"] = clone_checkpoint(Path(args.checkpoint), state)
    start = perf_counter()
    with open_database(state) as db:
        receipt["open_and_migrate_seconds"] = perf_counter() - start
        conn = db.connection
        receipt["schema_version"] = db.schema_version
        receipt["cohorts_before_scans"] = [
            dict(row)
            for row in conn.execute("SELECT * FROM requirement_cohorts ORDER BY cohort_id")
        ]
        receipt["counts"] = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("snapshots", "entries", "placements", "pending_work", "findings")
        }
        run_id = "h11-benchmark-" + now.strftime("%Y%m%dT%H%M%SZ")
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES (?,?,1)", (run_id, now.isoformat())
        )
        start = perf_counter()
        receipt["inventory_before_scans"] = inventory(conn, now)["cohorts"]
        receipt["inventory_before_scans_seconds"] = perf_counter() - start
        conn.execute("UPDATE requirement_scan SET cursor='',started_at=NULL")
        for iteration in range(2):
            start = perf_counter()
            checked = 0
            pages = []
            for page in range(args.max_pages):
                began = perf_counter()
                count = scan(db, now, run_id, limit=100)
                pages.append(perf_counter() - began)
                checked += count
                if count < 100:
                    break
                if page % 100 == 0:
                    print(
                        json.dumps(
                            {
                                "iteration": iteration + 1,
                                "pages": page + 1,
                                "checked": checked,
                                "seconds": perf_counter() - start,
                            }
                        ),
                        flush=True,
                    )
            complete = not conn.execute("SELECT cursor FROM requirement_scan").fetchone()[0]
            receipt["scans"].append(
                {
                    "iteration": iteration + 1,
                    "complete": complete,
                    "checked": checked,
                    "pages": len(pages),
                    "seconds": perf_counter() - start,
                    "max_page_seconds": max(pages),
                }
            )
            start = perf_counter()
            if iteration == 0:
                capture_cohort(conn, run_id, now)
            report = inventory(conn, now)
            receipt["scans"][-1]["doctor_inventory_seconds"] = perf_counter() - start
            receipt["scans"][-1]["states"] = report["states"]
            receipt["scans"][-1]["cohorts"] = report["cohorts"]
            measured_cohort = next(row for row in report["cohorts"] if row["cohort_id"] == run_id)
            receipt["scans"][-1]["captured_cohort_outside_count"] = measured_cohort[
                "outside_cohort"
            ]
            if not complete:
                break
        receipt["query_plans"] = {
            label: [list(row) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, values)]
            for label, sql, values in (
                (
                    "first_open_cutoffs",
                    "SELECT requirement_id,min(transition_id) FROM requirement_transitions GROUP BY requirement_id",
                    (),
                ),
                (
                    "artifact",
                    "SELECT snapshot_id FROM snapshots WHERE body_sha256=? ORDER BY snapshot_id",
                    ("a" * 64,),
                ),
                (
                    "transition_history",
                    "SELECT new_state FROM requirement_transitions WHERE requirement_id=? ORDER BY transition_id DESC LIMIT 1",
                    ("example",),
                ),
                (
                    "finalist",
                    "SELECT 1 FROM placements WHERE leader_entry_id=? OR follower_entry_id=? LIMIT 1",
                    ("example", "example"),
                ),
            )
        }
        if args.previous_report_module is not None:
            receipt["report_comparison"] = compare_reports(conn, now, args.previous_report_module)
        conn.execute(
            "UPDATE runs SET finished_at=? WHERE run_id=?", (datetime.now(UTC).isoformat(), run_id)
        )
    with args.output.open("x") as output:
        json.dump(receipt, output, indent=2, sort_keys=True)
        output.write("\n")
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
