#!/usr/bin/env python3
"""Summarize retained phase-one CSVs without granting year acceptance or fetching."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

INPUTS = ("years.csv", "events.csv", "findings.csv", "targets.csv", "series-review.csv")


def summarize(source: Path) -> dict[str, Any]:
    """Retain original rows and distinguish recorded gaps from acquisition status."""
    tables = {}
    hashes = {}
    for name in INPUTS:
        body = (source / name).read_bytes()
        hashes[name] = hashlib.sha256(body).hexdigest()
        tables[name] = list(csv.DictReader(io.StringIO(body.decode())))
    targets = {row["target_id"]: row for row in tables["targets.csv"]}
    if len(targets) != len(tables["targets.csv"]):
        raise ValueError("duplicate target identity")
    years = {int(row["year"]): row for row in tables["years.csv"]}
    if len(years) != len(tables["years.csv"]):
        raise ValueError("duplicate year")
    if set(years) != set(range(2010, 2027)):
        raise ValueError("expected an explicit review row for every year 2010–2026")
    reports = []
    for year, row in sorted(years.items()):
        events = [r for r in tables["events.csv"] if r["year"] == str(year)]
        findings = [
            r
            for r in tables["findings.csv"]
            if r["owner_kind"] in {"history_year", "phase1_year"} and r["owner_id"] == str(year)
        ]
        gaps = [
            {"target_id": key, "retained_target": targets.get(key)}
            for key in json.loads(row["capture_gaps"])
        ]
        reports.append(
            {
                "year": year,
                "recorded_year_summary": row,
                "recorded_capture_gaps_by_target_status": dict(
                    sorted(
                        Counter(
                            gap["retained_target"]["status"]
                            if gap["retained_target"] is not None
                            else "not_in_target_export"
                            for gap in gaps
                        ).items()
                    )
                ),
                "capture_gaps": gaps,
                "events": events,
                "findings": findings,
                "finding_kinds": dict(sorted(Counter(r["kind"] for r in findings).items())),
                "alias_groups": [
                    r for r in tables["series-review.csv"] if year in json.loads(r["years"])
                ],
                "counts_match_export": len(events) == int(row["events"])
                and len(findings) == int(row["event_findings"]),
            }
        )
    return {
        "format": "retained-phase1-review-v1",
        "input_directory": str(source.resolve()),
        "input_sha256": hashes,
        "scope": "Retained export only. Not a live-state verification or year acceptance.",
        "capture_gap_rule": "Parsed gaps can retain interpretation warnings; acquisition status does not close them.",
        "target_status_counts": dict(
            sorted(Counter(r["status"] for r in targets.values()).items())
        ),
        "pending_targets": [r for r in targets.values() if r["status"] == "pending"],
        "source_findings": [
            r
            for r in tables["findings.csv"]
            if r["owner_kind"] not in {"history_year", "phase1_year"}
        ],
        "alias_groups_total": len(tables["series-review.csv"]),
        "years": reports,
    }


def write_report(source: Path, output: Path) -> dict[str, Any]:
    report = summarize(source)
    output.mkdir(parents=True, exist_ok=False)
    (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Retained phase-one year review",
        "",
        report["scope"],
        "All counts below come from the hashed export in [report.json](report.json).",
        "The per-year pages retain exact events, findings, alias groups and capture gaps.",
        "Parsed targets may still have interpretation warnings; they are not missing bodies.",
        "Resolve findings against current source evidence before requesting owner acceptance.",
        "",
        "| Year | Events | Day / month | Listed / cancelled | Findings | Recorded accepted |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for year in report["years"]:
        row = year["recorded_year_summary"]
        name = f"{year['year']}.json"
        (output / name).write_text(json.dumps(year, indent=2, sort_keys=True) + "\n")
        lines.append(
            f"| [{year['year']}]({name}) | {row['events']} | "
            f"{row['events_day_precision']} / {row['events_month_precision']} | "
            f"{row['events_listed']} / {row['events_cancelled']} | "
            f"{row['event_findings']} | {row['events_accepted']} |"
        )
    (output / "README.md").write_text("\n".join(lines) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = write_report(args.input, args.output)
    print(json.dumps({"target_status_counts": report["target_status_counts"], "years": 17}))


if __name__ == "__main__":
    main()
