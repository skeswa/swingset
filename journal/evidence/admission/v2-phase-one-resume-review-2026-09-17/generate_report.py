#!/usr/bin/env python3
"""Rebuild a phase-one retained-evidence reconciliation without accepting years."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

INPUTS = ("years.csv", "events.csv", "findings.csv", "targets.csv", "series-review.csv")


def read_rows(directory: Path, name: str) -> list[dict[str, str]]:
    """Read one retained CSV without changing it."""
    with (directory / name).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def build_report(source: Path, derived_review: Path) -> dict[str, Any]:
    """Summarize the frozen export and its already-rendered year pages."""
    source = source.resolve()
    tables = {name: read_rows(source, name) for name in INPUTS}
    years = tables["years.csv"]
    events = tables["events.csv"]
    findings = tables["findings.csv"]
    targets = tables["targets.csv"]
    aliases = tables["series-review.csv"]
    occurrences = read_rows(source, "occurrences.csv")

    year_reports = []
    for row in years:
        year = int(row["year"])
        detail = json.loads((derived_review / f"{year}.json").read_text())
        year_reports.append(
            {
                "year": year,
                "events": int(row["events"]),
                "registry_occurrences": int(row["registry_occurrences"]),
                "unassociated_occurrences": int(row["unassociated_occurrences"]),
                "day_precision": int(row["events_day_precision"]),
                "month_precision": int(row["events_month_precision"]),
                "listed": int(row["events_listed"]),
                "cancelled": int(row["events_cancelled"]),
                "finding_count": int(row["event_findings"]),
                "finding_kinds": detail["finding_kinds"],
                "recorded_ready_for_owner_review": row["ready_for_owner_review"]
                == "True",
                "recorded_events_accepted": row["events_accepted"] == "True",
                "target_statuses_in_capture_gaps": detail[
                    "recorded_capture_gaps_by_target_status"
                ],
                "series_alias_groups": len(detail["alias_groups"]),
            }
        )

    pending = [
        {
            key: row[key]
            for key in (
                "target_id",
                "timestamp",
                "url",
                "source",
                "catalog_evidence",
                "digest",
            )
        }
        for row in targets
        if row["status"] == "pending"
    ]
    target_statuses = Counter(row["status"] for row in targets)
    finding_kinds = Counter(row["kind"] for row in findings)
    alias_candidates = Counter(
        "no_candidates" if json.loads(row["candidate_series"]) == [] else "ambiguous_multiple"
        for row in aliases
    )
    events_by_year = Counter(row["year"] for row in events)
    in_scope_years = {str(year) for year in range(2010, 2027)}

    return {
        "format": "v2-phase-one-resume-review-v1",
        "scope": "Read-only reconciliation of retained phase-one export and current-review pages. No source requests, production reads, acceptance, or publication.",
        "source_directory": source.relative_to(Path.cwd()).as_posix(),
        "inputs_sha256": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in INPUTS
        },
        "counts": {
            "targets": len(targets),
            "target_statuses": dict(sorted(target_statuses.items())),
            "pending_targets": len(pending),
            "phase1_findings": finding_kinds.get("phase1_incomplete", 0),
            "all_findings": len(findings),
            "finding_kinds": dict(sorted(finding_kinds.items())),
            "alias_review_groups": len(aliases),
            "alias_groups_without_candidates": alias_candidates["no_candidates"],
            "alias_groups_with_multiple_candidates": alias_candidates[
                "ambiguous_multiple"
            ],
            "in_scope_events_2010_2026": sum(
                int(row["events"]) for row in years
            ),
            "registry_occurrence_rows": sum(
                int(row["registry_occurrences"]) for row in years
            ),
            "in_scope_month_precision": sum(
                int(row["events_month_precision"]) for row in years
            ),
            "in_scope_listed": sum(int(row["events_listed"]) for row in years),
            "in_scope_cancelled": sum(
                int(row["events_cancelled"]) for row in years
            ),
            "events_by_year_in_event_export": {
                int(year): count
                for year, count in sorted(events_by_year.items())
            },
            "occurrence_evidence_rows": len(occurrences),
        },
        "pending_targets": pending,
        "in_scope_years": year_reports,
        "checks": {
            "all_17_years_present": {row["year"] for row in year_reports}
            == set(range(2010, 2027)),
            "all_years_recorded_unaccepted": all(
                not row["recorded_events_accepted"] for row in year_reports
            ),
            "all_years_have_findings": all(
                row["finding_count"] > 0 for row in year_reports
            ),
            "no_year_ready_for_owner_review": not any(
                row["recorded_ready_for_owner_review"] for row in year_reports
            ),
            "event_export_ids_unique": len(
                {row["event_id"] for row in events}
            )
            == len(events),
            "every_month_precision_row_has_event_month_and_no_day_dates": all(
                bool(row["event_month"])
                and not row["start_date"]
                and not row["end_date"]
                for row in events
                if row["date_precision"] == "month"
            ),
            "all_held_states_are_explicit": all(
                row["held"] in {"held", "listed", "cancelled"}
                for row in events
            ),
            "annual_counts_reconcile_to_event_export": all(
                sum(1 for event in events if event["year"] == str(row["year"]))
                == int(row["events"])
                for row in years
            ),
        },
        "limitations": [
            "The export is retained historical evidence, not a live production-state check.",
            "The 17 Archive catalog rows are pending body interpretation; this report does not claim each one changes each year.",
            "Month precision is permitted and reported by the backfill contract; it is not itself a finding.",
            "Listing-only and cancelled rows are retained explicitly. Their counts do not establish that every listing interpretation is correct.",
            "No historical year has explicit owner acceptance.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--derived-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = build_report(args.source, args.derived_review)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "checks_passed": sum(report["checks"].values()),
                "checks_total": len(report["checks"]),
                "pending_targets": len(report["pending_targets"]),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
