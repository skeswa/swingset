#!/usr/bin/env python3
"""Audit one built dataset candidate without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq

from swingset.build.schema import PRIMARY_KEYS, SCHEMAS


def sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path, help="candidate directory containing data/")
    parser.add_argument("output", type=Path, nargs="?", help="optional JSON output path")
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    manifest_path = candidate / "_meta" / "manifest.json"
    if not manifest_path.is_file():
        parser.error(f"manifest does not exist: {manifest_path}")

    manifest = json.loads(manifest_path.read_text())
    db = duckdb.connect()
    report: dict[str, Any] = {
        "candidate": str(candidate),
        "manifest": {
            key: manifest.get(key)
            for key in (
                "candidate_id",
                "built_at",
                "repository_commit",
                "latest_event_covered",
            )
        },
        "integrity": {},
        "coverage": {},
    }
    integrity = report["integrity"]
    coverage = report["coverage"]

    file_failures = []
    for relative, expected in manifest.get("files", {}).items():
        path = candidate / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != expected:
            file_failures.append({"file": relative, "expected": expected, "actual": actual})
    integrity["file_hash_failures"] = file_failures

    schema_failures = []
    actual_counts: dict[str, int] = {}
    for table, expected_schema in SCHEMAS.items():
        files = sorted((candidate / "data" / table).glob("*.parquet"))
        if not files:
            schema_failures.append({"table": table, "error": "no parquet files"})
            continue
        for path in files:
            actual_schema = pq.read_schema(path)
            if not actual_schema.equals(expected_schema):
                schema_failures.append(
                    {
                        "table": table,
                        "file": str(path.relative_to(candidate)),
                        "expected": str(expected_schema),
                        "actual": str(actual_schema),
                    }
                )
        glob = sql_path(candidate / "data" / table / "*.parquet")
        db.execute(
            f'CREATE VIEW "{table}" AS SELECT * FROM read_parquet('
            f"'{glob}', hive_partitioning=false)"
        )
        actual_counts[table] = int(db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
    integrity["schema_failures"] = schema_failures

    expected_counts = manifest.get("row_counts", {})
    integrity["row_count_mismatches"] = {
        table: {"expected": expected_counts.get(table), "actual": count}
        for table, count in actual_counts.items()
        if expected_counts.get(table) != count
    }
    integrity["manifest_tables_without_data"] = sorted(set(expected_counts) - set(actual_counts))

    def rows(query: str) -> list[dict[str, Any]]:
        cursor = db.execute(query)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]

    def scalar(query: str) -> int:
        return int(db.execute(query).fetchone()[0])

    duplicate_keys: dict[str, int] = {}
    for table, keys in PRIMARY_KEYS.items():
        if table not in actual_counts:
            continue
        columns = ",".join(f'"{key}"' for key in keys)
        duplicate_keys[table] = scalar(
            f"SELECT coalesce(sum(n - 1), 0) FROM ("
            f'SELECT count(*) n FROM "{table}" GROUP BY {columns} HAVING count(*) > 1)'
        )
    integrity["duplicate_primary_keys"] = duplicate_keys

    foreign_keys = [
        ("contests", "event_id", "events", "event_id"),
        ("rounds", "contest_id", "contests", "contest_id"),
        ("entries", "event_id", "events", "event_id"),
        ("entries", "contest_id", "contests", "contest_id"),
        ("entries", "partner_entry_id", "entries", "entry_id"),
        ("judges", "event_id", "events", "event_id"),
        ("heats", "round_id", "rounds", "round_id"),
        ("heats", "entry_id", "entries", "entry_id"),
        ("callback_marks", "round_id", "rounds", "round_id"),
        ("callback_marks", "entry_id", "entries", "entry_id"),
        ("callback_marks", "judge_id", "judges", "judge_id"),
        ("callbacks", "round_id", "rounds", "round_id"),
        ("callbacks", "entry_id", "entries", "entry_id"),
        ("placements", "round_id", "rounds", "round_id"),
        ("placements", "contest_id", "contests", "contest_id"),
        ("placements", "event_id", "events", "event_id"),
        ("placements", "leader_entry_id", "entries", "entry_id"),
        ("placements", "follower_entry_id", "entries", "entry_id"),
        ("placements", "couple_entry_id", "entries", "entry_id"),
        ("final_marks", "round_id", "rounds", "round_id"),
        ("final_marks", "placement_id", "placements", "placement_id"),
        ("final_marks", "judge_id", "judges", "judge_id"),
        ("registry_placements", "wsdc_id", "dancers", "wsdc_id"),
        ("registry_placements", "event_id", "events", "event_id"),
    ]
    missing_references = {}
    for child, column, parent, parent_key in foreign_keys:
        if child not in actual_counts or parent not in actual_counts:
            continue
        label = f"{child}.{column}->{parent}.{parent_key}"
        missing_references[label] = scalar(
            f'SELECT count(*) FROM "{child}" a LEFT JOIN "{parent}" b '
            f'ON a."{column}"=b."{parent_key}" WHERE a."{column}" IS NOT NULL '
            f'AND b."{parent_key}" IS NULL'
        )
    if {"identity_links", "entries", "judges", "dancers"} <= actual_counts.keys():
        missing_references["identity_links.subject_id->typed_subject"] = scalar(
            """SELECT count(*) FROM identity_links l
            WHERE (subject_kind='entry' AND NOT EXISTS
                    (SELECT 1 FROM entries e WHERE e.entry_id=l.subject_id))
               OR (subject_kind='judge' AND NOT EXISTS
                    (SELECT 1 FROM judges j WHERE j.judge_id=l.subject_id))
               OR (subject_kind='dancer' AND NOT EXISTS
                    (SELECT 1 FROM dancers d WHERE cast(d.wsdc_id AS varchar)=l.subject_id))"""
        )
    integrity["missing_foreign_references"] = missing_references

    snapshot_failures = {}
    for table, schema in SCHEMAS.items():
        if table == "snapshots" or table not in actual_counts or "snapshot_id" not in schema.names:
            continue
        snapshot_failures[table] = scalar(
            f'SELECT count(*) FROM "{table}" a LEFT JOIN snapshots s USING(snapshot_id) '
            "WHERE a.snapshot_id IS NULL OR (a.snapshot_id!='override' AND s.snapshot_id IS NULL)"
        )
    integrity["missing_snapshot_references"] = snapshot_failures

    integrity["invalid_event_dates"] = rows(
        "SELECT event_id,name,start_date,end_date FROM events WHERE start_date>end_date"
    )
    integrity["dirty_contest_headings"] = rows(
        """SELECT contest_id,name_raw FROM contests WHERE source='eepro' AND
        regexp_matches(name_raw, '(?i)[0-9]+ competed|when marks|ties broken|lowest sum used as tiebreaker')"""
    )
    integrity["relationship_errors"] = rows(
        """SELECT 'entry_event_vs_contest' check_name,count(*) n
        FROM entries e JOIN contests c USING(contest_id) WHERE e.event_id!=c.event_id
        UNION ALL SELECT 'placement_contest_vs_round',count(*)
        FROM placements p JOIN rounds r USING(round_id) WHERE p.contest_id!=r.contest_id
        UNION ALL SELECT 'placement_event_vs_contest',count(*)
        FROM placements p JOIN contests c USING(contest_id) WHERE p.event_id!=c.event_id
        UNION ALL SELECT 'callback_entry_vs_round_contest',count(*)
        FROM callbacks b JOIN entries e USING(entry_id) JOIN rounds r USING(round_id)
        WHERE e.contest_id!=r.contest_id
        UNION ALL SELECT 'partner_not_mutual',count(*)
        FROM entries a JOIN entries b ON a.partner_entry_id=b.entry_id
        WHERE b.partner_entry_id IS DISTINCT FROM a.entry_id
        UNION ALL SELECT 'partner_cross_contest',count(*)
        FROM entries a JOIN entries b ON a.partner_entry_id=b.entry_id
        WHERE a.contest_id!=b.contest_id
        UNION ALL SELECT 'final_mark_round_vs_placement',count(*)
        FROM final_marks f JOIN placements p USING(placement_id) WHERE f.round_id!=p.round_id"""
    )
    integrity["placement_role_errors"] = scalar(
        """SELECT count(*) FROM placements p
        LEFT JOIN entries l ON p.leader_entry_id=l.entry_id
        LEFT JOIN entries f ON p.follower_entry_id=f.entry_id
        LEFT JOIN entries c ON p.couple_entry_id=c.entry_id
        WHERE (l.role IS NOT NULL AND l.role!='leader')
           OR (f.role IS NOT NULL AND f.role!='follower')
           OR (c.role IS NOT NULL AND c.role!='couple')"""
    )
    integrity["placement_sequence_errors"] = scalar(
        """SELECT count(*) FROM (
        SELECT round_id,count(*) n,count(distinct place) unique_places,min(place) lo,max(place) hi
        FROM placements GROUP BY round_id HAVING lo!=1 OR hi!=n OR n!=unique_places)"""
    )
    integrity["callback_aggregate_mismatches"] = rows(
        """SELECT a.source,count(*) n FROM callbacks a JOIN (
        SELECT round_id,entry_id,sum(mark_value) score,
               count(*) FILTER(WHERE mark='yes') yes_n,
               count(*) FILTER(WHERE mark LIKE 'alt%') alt_n,
               count(*) FILTER(WHERE mark='no') no_n
        FROM callback_marks GROUP BY round_id,entry_id) b USING(round_id,entry_id)
        WHERE abs(a.score_sum-b.score)>0.001 OR a.yes_count!=b.yes_n
           OR a.alt_count!=b.alt_n OR a.no_count!=b.no_n GROUP BY a.source"""
    )
    integrity["registry_point_evidence_orphans"] = scalar(
        """SELECT count(*) FROM (
        SELECT p.placement_id FROM placements p JOIN contests c USING(contest_id)
        WHERE p.registry_points_leader IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM registry_placements rp
          WHERE rp.event_id=p.event_id AND rp.wsdc_id=p.leader_wsdc_id AND rp.role='leader'
            AND rp.division=c.division AND rp.dance_style=c.dance_style
            AND rp.result=cast(p.place AS varchar) AND rp.points=p.registry_points_leader)
        UNION ALL
        SELECT p.placement_id FROM placements p JOIN contests c USING(contest_id)
        WHERE p.registry_points_follower IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM registry_placements rp
          WHERE rp.event_id=p.event_id AND rp.wsdc_id=p.follower_wsdc_id AND rp.role='follower'
            AND rp.division=c.division AND rp.dance_style=c.dance_style
            AND rp.result=cast(p.place AS varchar) AND rp.points=p.registry_points_follower))"""
    )

    coverage["row_counts"] = actual_counts
    coverage["events_by_source"] = rows(
        """SELECT e.source,count(distinct e.event_id) events,
        count(distinct c.event_id) events_with_contests,
        count(distinct p.event_id) events_with_placements,count(distinct p.placement_id) placements
        FROM events e LEFT JOIN contests c USING(event_id)
        LEFT JOIN placements p ON p.contest_id=c.contest_id GROUP BY e.source ORDER BY e.source"""
    )
    coverage["results_by_source_year"] = rows(
        """SELECT p.source,e.year,count(distinct p.event_id) events,count(*) placements
        FROM placements p JOIN events e USING(event_id) GROUP BY p.source,e.year
        ORDER BY p.source,e.year"""
    )
    coverage["event_field_gaps"] = rows(
        """SELECT source,count(*) events,
        count(*) FILTER(WHERE snapshot_id='override') override_placeholders,
        count(*) FILTER(WHERE city IS NULL OR city='') missing_city,
        count(*) FILTER(WHERE country IS NULL OR country='') missing_country
        FROM events GROUP BY source ORDER BY source"""
    )
    coverage["contest_status"] = rows(
        "SELECT source,parse_status,count(*) n FROM contests GROUP BY source,parse_status ORDER BY 1,2"
    )
    coverage["callback_outcomes"] = rows(
        "SELECT source,outcome,count(*) n FROM callbacks GROUP BY source,outcome ORDER BY 1,2"
    )
    coverage["entry_identity"] = rows(
        """SELECT source,link_status,count(*) entries,
        count(*) FILTER(WHERE wsdc_id IS NOT NULL) with_wsdc_id,
        count(*) FILTER(WHERE name_raw IS NULL) nameless,
        count(*) FILTER(WHERE bib IS NULL) without_bib
        FROM entries GROUP BY source,link_status ORDER BY source,link_status"""
    )
    coverage["review_kinds"] = rows(
        "SELECT kind,count(*) n FROM review_queue GROUP BY kind ORDER BY n DESC,kind"
    )
    coverage["review_summaries"] = rows(
        """SELECT kind,summary,count(*) n FROM review_queue GROUP BY kind,summary
        ORDER BY n DESC,kind,summary LIMIT 50"""
    )
    coverage["registry"] = rows(
        """SELECT count(*) placements,
        count(*) FILTER(WHERE event_id IS NOT NULL) mapped_to_event,
        count(distinct wsdc_id) dancers_with_placements FROM registry_placements"""
    )[0]
    coverage["placement_registry_points"] = rows(
        """SELECT source,count(*) placements,
        count(*) FILTER(WHERE registry_confirmed) registry_confirmed,
        count(*) FILTER(WHERE registry_points_leader IS NOT NULL
                        OR registry_points_follower IS NOT NULL) with_registry_points,
        count(*) FILTER(WHERE points_matches_expected=true) expected_match,
        count(*) FILTER(WHERE points_matches_expected=false) expected_mismatch
        FROM placements GROUP BY source ORDER BY source"""
    )
    coverage["natural_entry_duplicates"] = rows(
        """SELECT source,count(*) duplicate_groups,sum(n-1) excess_rows FROM (
        SELECT source,contest_id,role,name_norm,count(*) n FROM entries
        WHERE name_norm IS NOT NULL AND name_norm!=''
        GROUP BY source,contest_id,role,name_norm HAVING count(*)>1)
        GROUP BY source ORDER BY source"""
    )
    coverage["named_placeholder_entries"] = rows(
        """SELECT source,count(*) n FROM entries
        WHERE regexp_matches(coalesce(name_raw,''),'\\*{3,}|(?i)^unknown$|(?i)^tbd$')
        GROUP BY source ORDER BY source"""
    )
    built_at = manifest.get("built_at")
    try:
        as_of = datetime.fromisoformat(str(built_at).replace("Z", "+00:00")).date()
    except ValueError:
        as_of = date.today()
    coverage["event_time_coverage"] = rows(
        f"""SELECT CASE WHEN start_date>DATE '{as_of}' THEN 'future'
        WHEN end_date<DATE '{as_of}' THEN 'past' ELSE 'current' END period,
        count(*) events,count(*) FILTER(WHERE event_id IN (SELECT event_id FROM contests)) with_contests,
        count(*) FILTER(WHERE event_id IN (SELECT event_id FROM placements)) with_placements
        FROM events GROUP BY 1 ORDER BY 1"""
    )

    def has_violation(value: Any) -> bool:
        if isinstance(value, int):
            return value != 0
        if isinstance(value, list):
            if value and all(isinstance(item, dict) and "n" in item for item in value):
                return any(int(item["n"]) != 0 for item in value)
            return bool(value)
        if isinstance(value, dict):
            return any(has_violation(item) for item in value.values())
        return bool(value)

    violations = sorted(key for key, value in integrity.items() if has_violation(value))
    report["ok"] = not violations
    report["violations"] = violations
    rendered = json.dumps(report, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        sys.stdout.write(rendered)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
