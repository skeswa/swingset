#!/usr/bin/env python3
"""Audit published event/result coverage against captured discovery state.

Usage:
    .venv/bin/python research/missing_data_events.py CAPTURE_DIR OUTPUT_DIR

The capture directory must contain candidate/, state.sqlite, and capture.json.
The script is offline: published Parquet is the row-count authority; SQLite is
used only for discovery, watch, snapshot, and provenance evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def rows(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for parquet_path in sorted(path.rglob("*.parquet")):
        records.extend(pq.read_table(parquet_path).to_pylist())
    return records


def period(start: date, end: date, as_of: date) -> str:
    if end < as_of:
        return "past"
    if start > as_of:
        return "future"
    return "current"


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = sorted({key for record in records for key in record})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def read_leads(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["event_key"]: row for row in csv.DictReader(handle)}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def name_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if not (len(token) == 4 and token.isdigit())
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--as-of", type=date.fromisoformat)
    args = parser.parse_args()

    capture_dir = args.capture_dir.resolve()
    candidate = capture_dir / "candidate" / "data"
    state_path = capture_dir / "state.sqlite"
    capture_path = capture_dir / "capture.json"
    metadata = json.loads(capture_path.read_text(encoding="utf-8"))
    as_of = args.as_of or datetime.fromisoformat(metadata["finished_at"]).date()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    events = rows(candidate / "events")
    contests = rows(candidate / "contests")
    rounds = rows(candidate / "rounds")
    entries = rows(candidate / "entries")
    placements = rows(candidate / "placements")

    event_counts: dict[str, Counter[str]] = defaultdict(Counter)
    contest_sources: dict[str, Counter[str]] = defaultdict(Counter)
    round_sources: dict[str, Counter[str]] = defaultdict(Counter)
    for row in contests:
        event_counts[row["event_id"]]["contests"] += 1
        contest_sources[row["event_id"]][row["source"]] += 1
    for row in rounds:
        event_counts[row["contest_id"]]["unused"] += 0
        round_sources[row["contest_id"]][row["source"]] += 1
    contest_to_event = {row["contest_id"]: row["event_id"] for row in contests}
    for row in rounds:
        event_counts[contest_to_event[row["contest_id"]]]["rounds"] += 1
    for row in entries:
        event_counts[row["event_id"]]["entries"] += 1
    for row in placements:
        event_counts[row["event_id"]]["placements"] += 1

    db = sqlite3.connect(f"file:{state_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    maps = {
        (row["source"], row["source_ref"]): row["event_id"]
        for row in db.execute("SELECT source,source_ref,event_id FROM source_event_map")
    }
    source_events = [dict(row) for row in db.execute("SELECT * FROM source_events")]

    watches: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    watch_examples: dict[tuple[str, str], list[str]] = defaultdict(list)
    watch_query = """
        SELECT p.source,p.source_ref,c.kind,c.url,c.ever_ok,c.last_checked_at,
               c.current_observation_snapshot_id,
               (SELECT s.parse_status FROM snapshots s
                WHERE s.watch_id=c.watch_id ORDER BY s.fetched_at DESC LIMIT 1) parse_status
        FROM watches c JOIN watches p ON p.watch_id=c.parent_watch_id
        WHERE c.kind='round' AND p.source_ref IS NOT NULL
    """
    for row in db.execute(watch_query):
        key = (row["source"], row["source_ref"])
        watches[key]["listed_round_urls"] += 1
        if row["ever_ok"]:
            watches[key]["fetched_round_urls"] += 1
        else:
            watches[key]["unfetched_round_urls"] += 1
            if len(watch_examples[key]) < 3:
                watch_examples[key].append(row["url"])
        if row["last_checked_at"] is None:
            watches[key]["never_checked_round_urls"] += 1
        if row["ever_ok"] and row["current_observation_snapshot_id"] is None:
            watches[key]["fetched_without_current_observation"] += 1
        if row["parse_status"] not in (None, "ok"):
            watches[key]["latest_parse_not_ok"] += 1

    global_round_watches: dict[str, Counter[str]] = defaultdict(Counter)
    for row in db.execute(
        "SELECT source,parent_watch_id,last_checked_at,ever_ok FROM watches WHERE kind='round'"
    ):
        counter = global_round_watches[row["source"]]
        counter["all_round_watches"] += 1
        counter["round_watches_without_parent"] += row["parent_watch_id"] is None
        counter["all_never_checked_round_watches"] += row["last_checked_at"] is None
        counter["all_ever_ok_round_watches"] += bool(row["ever_ok"])

    parser_failures = [
        dict(row)
        for row in db.execute(
            """
            WITH latest AS (
              SELECT w.source,w.kind,w.source_ref,w.url,s.snapshot_id,
                     s.http_status,s.extract_status,s.parse_status,s.fetched_at,
                     ROW_NUMBER() OVER (PARTITION BY w.watch_id ORDER BY s.fetched_at DESC) rank
              FROM watches w JOIN snapshots s USING(watch_id)
            )
            SELECT source,kind,source_ref,url,snapshot_id,http_status,
                   extract_status,parse_status,fetched_at
            FROM latest WHERE rank=1 AND parse_status NOT IN ('ok','parsed')
            ORDER BY source,kind,source_ref
            """
        )
    ]

    provenance: dict[str, set[str]] = defaultdict(set)
    refs_by_event: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, event_id in maps.items():
        provenance[event_id].add(key[0])
        refs_by_event[event_id].append(key)

    event_rows: list[dict[str, Any]] = []
    for event in events:
        counts = event_counts[event["event_id"]]
        category = period(event["start_date"], event["end_date"], as_of)
        refs = refs_by_event[event["event_id"]]
        listed = sum(watches[key]["listed_round_urls"] for key in refs)
        fetched = sum(watches[key]["fetched_round_urls"] for key in refs)
        event_rows.append(
            {
                "event_id": event["event_id"],
                "year": event["year"],
                "period": category,
                "name": event["name"],
                "start_date": event["start_date"].isoformat(),
                "end_date": event["end_date"].isoformat(),
                "wsdc_status": event["wsdc_status"],
                "event_sources": "|".join(event["sources"] or []),
                "mapped_platforms": "|".join(sorted(provenance[event["event_id"]])),
                "contests": counts["contests"],
                "rounds": counts["rounds"],
                "entries": counts["entries"],
                "placements": counts["placements"],
                "listed_round_urls": listed,
                "fetched_round_urls": fetched,
                "unfetched_round_urls": listed - fetched,
                "never_checked_round_urls": sum(
                    watches[key]["never_checked_round_urls"] for key in refs
                ),
            }
        )

    by_year: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    for row in event_rows:
        counter = by_year[(row["year"], row["period"])]
        counter["events"] += 1
        counter["events_with_contests"] += row["contests"] > 0
        counter["events_without_contests"] += row["contests"] == 0
        counter["events_with_placements"] += row["placements"] > 0
        counter["events_without_placements"] += row["placements"] == 0
        for metric_name in (
            "contests",
            "rounds",
            "entries",
            "placements",
            "listed_round_urls",
            "unfetched_round_urls",
            "never_checked_round_urls",
        ):
            counter[metric_name] += int(row[metric_name])
    year_rows = [
        {"year": year, "period": category, **dict(counter)}
        for (year, category), counter in sorted(by_year.items())
    ]

    source_rows: list[dict[str, Any]] = []
    for source in ("eepro", "scoringdance", "wdr"):
        source_refs = {
            (mapped_source, source_ref): mapped_event_id
            for (mapped_source, source_ref), mapped_event_id in maps.items()
            if mapped_source == source
        }
        source_index = [row for row in source_events if row["source"] == source]
        source_rows.append(
            {
                "source": source,
                **dict(global_round_watches[source]),
                "indexed_source_events": len(source_index) if source != "wdr" else 0,
                "mapped_source_events": len(source_refs),
                "unmapped_source_events": sum(
                    (source, row["source_ref"]) not in maps for row in source_index
                ),
                "mapped_events_with_contests": len(
                    {
                        event_id
                        for event_id in source_refs.values()
                        if event_counts[event_id]["contests"]
                    }
                ),
                "mapped_events_without_contests": len(
                    {
                        event_id
                        for event_id in source_refs.values()
                        if not event_counts[event_id]["contests"]
                    }
                ),
                "listed_round_urls": sum(
                    watches[source_key]["listed_round_urls"] for source_key in source_refs
                ),
                "fetched_round_urls": sum(
                    watches[source_key]["fetched_round_urls"] for source_key in source_refs
                ),
                "unfetched_round_urls": sum(
                    watches[source_key]["unfetched_round_urls"] for source_key in source_refs
                ),
                "never_checked_round_urls": sum(
                    watches[source_key]["never_checked_round_urls"] for source_key in source_refs
                ),
                "fetched_without_current_observation": sum(
                    watches[source_key]["fetched_without_current_observation"]
                    for source_key in source_refs
                ),
                "latest_parse_not_ok": sum(
                    watches[source_key]["latest_parse_not_ok"] for source_key in source_refs
                ),
                "published_contests": sum(1 for row in contests if row["source"] == source),
                "published_rounds": sum(1 for row in rounds if row["source"] == source),
                "published_entries": sum(1 for row in entries if row["source"] == source),
                "published_placements": sum(1 for row in placements if row["source"] == source),
            }
        )

    gap_rows: list[dict[str, Any]] = []
    event_by_id = {row["event_id"]: row for row in event_rows}
    for source_event in source_events:
        source_key = (source_event["source"], source_event["source_ref"])
        event_id = maps.get(source_key)
        mapped_event = event_by_id.get(event_id or "")
        gap_rows.append(
            {
                "source": source_key[0],
                "source_ref": source_key[1],
                "source_name": source_event["name_raw"],
                "source_start_date": source_event["start_date"],
                "source_end_date": source_event["end_date"],
                "mapped_event_id": event_id,
                "period": mapped_event["period"] if mapped_event else "unknown",
                "published_contests": mapped_event["contests"] if mapped_event else 0,
                "published_rounds": mapped_event["rounds"] if mapped_event else 0,
                **dict(watches[source_key]),
                "unfetched_examples": "|".join(watch_examples[source_key]),
            }
        )

    listed_gap_years: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)
    for row in gap_rows:
        year = (row["source_start_date"] or "unknown")[:4]
        aggregate_key = (row["source"], year, row["period"])
        listed_gap_years[aggregate_key]["source_events"] += 1
        listed_gap_years[aggregate_key]["mapped_source_events"] += bool(row["mapped_event_id"])
        listed_gap_years[aggregate_key]["mapped_events_with_contests"] += bool(
            row["mapped_event_id"] and int(row["published_contests"]) > 0
        )
        listed_gap_years[aggregate_key]["mapped_events_without_contests"] += bool(
            row["mapped_event_id"] and int(row["published_contests"]) == 0
        )
        listed_gap_years[aggregate_key]["published_contests"] += int(row["published_contests"])
        listed_gap_years[aggregate_key]["published_rounds"] += int(row["published_rounds"])
        listed_gap_years[aggregate_key]["listed_round_urls"] += int(row.get("listed_round_urls", 0))
        listed_gap_years[aggregate_key]["fetched_round_urls"] += int(
            row.get("fetched_round_urls", 0)
        )
        listed_gap_years[aggregate_key]["never_checked_round_urls"] += int(
            row.get("never_checked_round_urls", 0)
        )
    listed_gap_rows = [
        {"source": source, "year": year, "period": category, **dict(counter)}
        for (source, year, category), counter in sorted(listed_gap_years.items())
    ]

    repo = Path(__file__).resolve().parents[1]
    event_leads = read_leads(repo / "research" / "events.csv")
    result_leads = read_leads(repo / "research" / "results-sources.csv")
    lead_rows: list[dict[str, Any]] = []
    published_ids = set(event_by_id)
    for event_key in sorted(set(event_leads) | set(result_leads)):
        event_lead = event_leads.get(event_key, {})
        result_lead = result_leads.get(event_key, {})
        status = event_lead.get("status", "")
        flags = event_lead.get("flags", "")
        cancelled = "cancel" in f"{status} {flags}".lower()
        end_raw = event_lead.get("end_date") or result_lead.get("end_date")
        lead_period = "unknown"
        if end_raw:
            end = date.fromisoformat(end_raw)
            lead_period = "future" if end > as_of else "past"
        if cancelled:
            lead_period = "cancelled"
        possible_matches: list[str] = []
        lead_tokens = name_tokens(event_lead.get("name") or result_lead.get("name", ""))
        for published in event_rows:
            if not end_raw or published["end_date"] != end_raw:
                continue
            published_tokens = name_tokens(published["name"])
            union = lead_tokens | published_tokens
            similarity = len(lead_tokens & published_tokens) / len(union) if union else 0
            if similarity >= 0.6:
                possible_matches.append(published["event_id"])
        lead_rows.append(
            {
                "event_key": event_key,
                "name": event_lead.get("name") or result_lead.get("name"),
                "end_date": end_raw,
                "period": lead_period,
                "research_status": status,
                "research_flags": flags,
                "cancelled": cancelled,
                "in_published_events": event_key in published_ids,
                "possible_published_matches": "|".join(possible_matches),
                "research_platform": result_lead.get("platform", ""),
                "research_has_results": result_lead.get("has_results", ""),
                "research_edition_held": result_lead.get("edition_held", ""),
                "research_confidence": result_lead.get("confidence", ""),
                "research_results_url": result_lead.get("results_url", ""),
                "lead_only_warning": "research lead; not accepted source evidence",
            }
        )

    summary = {
        "as_of": as_of.isoformat(),
        "publication_commit": metadata.get("publication", {}).get("commit"),
        "candidate": metadata.get("candidate"),
        "state_sha256_capture": metadata.get("state_sha256"),
        "state_sha256_local": sha256(state_path),
        "published": {
            "events": len(events),
            "contests": len(contests),
            "rounds": len(rounds),
            "entries": len(entries),
            "placements": len(placements),
        },
        "event_periods": dict(Counter(row["period"] for row in event_rows)),
        "events_with_contests": sum(row["contests"] > 0 for row in event_rows),
        "events_without_contests": sum(row["contests"] == 0 for row in event_rows),
        "events_with_placements": sum(row["placements"] > 0 for row in event_rows),
        "events_without_placements": sum(row["placements"] == 0 for row in event_rows),
        "source_coverage": source_rows,
        "research_leads": {
            "rows": len(lead_rows),
            "not_in_published_events": sum(not row["in_published_events"] for row in lead_rows),
            "cancelled": sum(row["cancelled"] for row in lead_rows),
            "edition_not_held": sum(row["research_edition_held"] == "no" for row in lead_rows),
            "result_leads_not_published": sum(
                bool(row["research_results_url"]) and not row["in_published_events"]
                for row in lead_rows
            ),
            "absent_exact_key_without_possible_match": sum(
                not row["in_published_events"] and not row["possible_published_matches"]
                for row in lead_rows
            ),
            "periods": dict(Counter(row["period"] for row in lead_rows)),
        },
        "current_events": [
            {
                "event_id": row["event_id"],
                "name": row["name"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
            }
            for row in event_rows
            if row["period"] == "current"
        ],
        "latest_parser_failures": parser_failures,
        "limits": [
            "An event without contests is not proof that results should exist.",
            "Platform indexes enumerate source events, not every contest expected at an event.",
            "Listed round watches are the only captured round-level completeness denominator.",
            "Research CSV rows are leads and are not accepted source evidence.",
            "The 2010 historical target is draft; this audit reports actual published coverage only.",
        ],
    }

    (output / "events-summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8"
    )
    write_csv(output / "events-by-year.csv", year_rows)
    write_csv(output / "events-coverage.csv", event_rows)
    write_csv(output / "events-by-source.csv", source_rows)
    write_csv(output / "events-source-gaps.csv", gap_rows)
    write_csv(output / "events-listed-round-gaps-by-year.csv", listed_gap_rows)
    write_csv(output / "events-research-leads.csv", lead_rows)
    write_csv(output / "events-parser-failures.csv", parser_failures)


if __name__ == "__main__":
    main()
