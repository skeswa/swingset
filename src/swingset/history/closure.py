"""Per-year event-list closure derived from the captured catalog and findings."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from typing import Any

from swingset.history.catalog import CALENDAR_PATHS, Target
from swingset.sources import get_page_kind
from swingset.state.findings import Finding, replace_findings


def affected_years(target: Target, final_year: int) -> set[int]:
    if target.source == "wsdc_calendar":
        # The 2021 inline calendar includes 2018–2023. Missing historical
        # data has no safe lower bound until the body is interpreted.
        return set(range(2010, final_year + 1))
    if target.timestamp:
        first = int(target.timestamp[:4])
        # Actual 2016 cards include 2018 editions; failed bodies may have the
        # same two-year horizon even before their dates can be interpreted.
        horizon = 2
        return set(range(max(2010, first), min(final_year, first + horizon) + 1))
    return set(range(2016, final_year + 1))


def year_gaps(
    conn: sqlite3.Connection,
    targets: tuple[Target, ...],
    ledger: dict[str, dict[str, Any]],
    *,
    final_year: int,
    history_start: date,
) -> dict[int, list[dict[str, Any]]]:
    gaps: dict[int, list[dict[str, Any]]] = {
        year: [] for year in range(history_start.year, final_year + 1)
    }
    warnings: dict[str, list[dict[str, Any]]] = {}
    for row in conn.execute(
        "SELECT snapshot_id,summary,evidence_json FROM findings WHERE closed_at IS NULL AND owner_kind IN ('parse','parse_failure','phase1_capture')"
    ):
        warnings.setdefault(str(row[0]), []).append(
            {"summary": str(row[1]), "evidence": json.loads(row[2])}
        )
    for target in targets:
        receipt = ledger.get(target.target_id, {"status": "pending"})
        notes = list(warnings.get(str(receipt.get("snapshot_id")), []))
        if receipt["status"] in {"parsed", "empty", "duplicate"}:
            parsed = conn.execute(
                "SELECT parser_version FROM snapshots WHERE snapshot_id=?",
                (receipt.get("snapshot_id"),),
            ).fetchone()
            if parsed is None or str(parsed[0]) != str(get_page_kind(target.parser).PARSER_VERSION):
                notes.append(
                    {
                        "summary": "Cached interpretation needs the current parser version",
                        "evidence": {"snapshot_id": receipt.get("snapshot_id")},
                    }
                )
        if receipt["status"] in {"parsed", "empty", "duplicate"} and not notes:
            continue
        relevant = affected_years(target, final_year)
        if notes:
            # Printed years in diagnostic rows can identify later editions in a
            # long horizon; a malformed year never narrows the capture horizon.
            for row in conn.execute(
                "SELECT DISTINCT substr(json_extract(payload_json,'$.end_date_raw'),1,4) FROM observations WHERE snapshot_id=? AND kind='calendar_row'",
                (receipt.get("snapshot_id"),),
            ):
                if row[0] and str(row[0]).isdigit():
                    relevant.add(int(row[0]))
            printed = [
                [note["summary"], note["evidence"].get("evidence", {})]
                for note in notes
                if isinstance(note["evidence"], dict)
            ]
            relevant.update(
                int(year)
                for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", json.dumps(printed))
                if history_start.year <= int(year) <= final_year
            )
        gap = {
            "target_id": target.target_id,
            "source": target.source,
            "url": target.url,
            "timestamp": target.timestamp,
            "status": receipt["status"],
            "warnings": notes,
        }
        for year in relevant & gaps.keys():
            gaps[year].append(gap)
    # Retained research CDX stops in 2023. Later years require explicit bounded
    # discovery; absence of rows before querying is not evidence of absence.
    for year in range(max(2024, history_start.year), final_year + 1):
        completed = {
            str(row[0])
            for row in conn.execute(
                "SELECT prefix FROM archive_queries WHERE source='wsdc_calendar' AND year=? AND completed_at IS NOT NULL",
                (year,),
            )
        }
        missing = sorted(
            "worldsdc.com" + path
            for path in CALENDAR_PATHS
            if "worldsdc.com" + path not in completed
        )
        if missing:
            gaps[year].append(
                {"kind": "phase1_discovery", "year": year, "missing_cdx_prefixes": missing}
            )
    return gaps


def synchronize_year_findings(
    conn: sqlite3.Connection, gaps: dict[int, list[dict[str, Any]]], *, now: str, run_id: str
) -> None:
    for year, pending in gaps.items():
        findings = (
            (
                Finding(
                    "phase1_incomplete",
                    "history_year",
                    str(year),
                    "warning",
                    "Event-list catalog has pending captures, unresolved parse findings, or missing discovery",
                    {"year": year, "gaps": pending},
                ),
            )
            if pending
            else ()
        )
        replace_findings(
            conn,
            owner_kind="phase1_year",
            owner_id=str(year),
            findings=findings,
            opened_at=now,
            run_id=run_id,
        )
