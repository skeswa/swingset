#!/usr/bin/env python3
"""Build, execute, and review the finite v2 phase1 catalog. Never fetch score sheets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from swingset.clock import SystemClock
from swingset.config import load_config
from swingset.history.aliases import alias_proposals
from swingset.history.catalog import Target, retained_catalog, save_catalog
from swingset.history.discovery import discover_calendar
from swingset.history.intake import bootstrap, run_intake
from swingset.history.review import review_pack
from swingset.history.transfer import export_evidence, import_evidence
from swingset.project.process import process_unit
from swingset.state.db import open_database
from swingset.state.inputs import capture
from swingset.state.work import next_work


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "aliases",
            "catalog",
            "bootstrap",
            "run",
            "review",
            "discover",
            "project",
            "export",
            "import",
        ],
    )
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--max-targets", type=int, default=200)
    parser.add_argument("--wall-seconds", type=int, default=2700)
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument(
        "--no-reconcile",
        action="store_true",
        help="Read an in-flight intake without taking its writer lock",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    catalog = args.state / "phase1-catalog.json"
    if args.command == "bootstrap":
        if args.checkpoint is None:
            parser.error("bootstrap requires --checkpoint")
        bootstrap(args.checkpoint, args.state)
        print(json.dumps({"state": str(args.state)}))
    elif args.command == "catalog":
        targets = retained_catalog(args.repo) + (
            Target(
                "wsdc_newsletter",
                "https://www.worldsdc.com/newsletter/",
                "wsdc_newsletter.index",
                catalog_evidence="docs/reference/backfill.md",
            ),
        )
        sha = save_catalog(catalog, targets)
        print(
            json.dumps(
                {
                    "catalog": str(catalog),
                    "sha256": sha,
                    "targets": len(targets),
                    "archive": sum(bool(target.timestamp) for target in targets),
                }
            )
        )
    else:
        readonly = args.command in {"export", "aliases"} or (
            args.command == "review" and args.no_reconcile
        )
        with open_database(
            args.state,
            lock=not (args.command == "review" and args.no_reconcile),
            read_only=readonly,
        ) as database:
            if args.command == "aliases":
                print(
                    json.dumps(
                        alias_proposals(
                            database.connection,
                            args.output or args.state / "phase1-review",
                            now=SystemClock().now().isoformat(),
                        )
                    )
                )
            elif args.command in {"export", "import"}:
                if args.package is None:
                    parser.error("export/import requires --package")
                transfer_result = (
                    export_evidence(database, args.package, repository=args.repo)
                    if args.command == "export"
                    else import_evidence(database, args.package, clock=SystemClock())
                )
                print(json.dumps(transfer_result))
            elif args.command == "project":
                clock = SystemClock()
                run_id = database.start_run(clock.now(), dry_run=True)
                bundle = capture(args.repo / "config", args.repo / "overrides", args.state, {})
                processed = 0
                while (unit := next_work(database.connection, "project")) is not None:
                    process_unit(database, unit, bundle, clock, run_id)
                    processed += 1
                    print(
                        json.dumps(
                            {
                                "projected": unit.unit_kind,
                                "unit_id": unit.unit_id,
                                "processed": processed,
                            }
                        ),
                        flush=True,
                    )
                with database.transaction() as conn:
                    conn.execute(
                        "UPDATE runs SET finished_at=?,summary_json=? WHERE run_id=?",
                        (clock.now().isoformat(), json.dumps({"projected": processed}), run_id),
                    )
            elif args.command == "discover":
                count = discover_calendar(
                    database,
                    catalog,
                    config=load_config(args.repo / "config"),
                    clock=SystemClock(),
                    wall_seconds=args.wall_seconds,
                )
                print(json.dumps({"new_targets": count}))
            elif args.command == "run":
                result = run_intake(
                    database,
                    catalog,
                    config=load_config(args.repo / "config"),
                    clock=SystemClock(),
                    max_targets=args.max_targets,
                    wall_seconds=args.wall_seconds,
                    retry_failures=args.retry_failures,
                )
                print(
                    json.dumps(
                        {
                            "targets": len(result["targets"]),
                            "ledger": str(args.state / "phase1-ledger.json"),
                        }
                    )
                )
            else:
                result = review_pack(
                    database,
                    args.output or args.state / "phase1-review",
                    now=SystemClock().now().isoformat(),
                    aliases=(args.repo / "overrides/series_aliases.csv").read_bytes(),
                    reconcile=not readonly,
                )
                print(
                    json.dumps(
                        {
                            "years": result["years"],
                            "target_status_counts": result["target_status_counts"],
                        }
                    )
                )


if __name__ == "__main__":
    main()
