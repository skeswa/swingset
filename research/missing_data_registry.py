#!/usr/bin/env python3
"""Reproduce the 2026-09-12 offline registry gap audit."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

DIVISIONS = {
    "JRS": "juniors",
    "MSTR": "masters",
    "NEW": "newcomer",
    "NOV": "novice",
    "INT": "intermediate",
    "ADV": "advanced",
    "ALS": "allstar",
    "CHMP": "champion",
    "INV": "invitational",
    "SPH": "sophisticated",
    "PRO": "PRO",
    "TCH": "TCH",
}


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    capture = args.capture_directory
    output = args.output_directory
    output.mkdir(parents=True, exist_ok=True)

    with gzip.open(capture / "comparison.json.gz", "rt", encoding="utf-8") as stream:
        reference = json.load(stream)
    capture_meta = json.loads((capture / "capture.json").read_text())
    conn = sqlite3.connect(f"file:{capture / 'state.sqlite'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    reference_dancers = {int(row["id"]): row for row in reference["dancers"]}
    local_dancers = {
        int(row["wsdc_id"]): dict(row) for row in conn.execute("SELECT * FROM dancers")
    }
    reference_ids = set(reference_dancers)
    local_ids = set(local_dancers)
    missing_ids = sorted(reference_ids - local_ids)
    additional_ids = sorted(local_ids - reference_ids)

    occurrences = {int(row["id"]): row for row in reference["event_occurrences"]}
    events = {int(row["id"]): row for row in reference["events"]}
    placements_by_dancer: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in reference["placements"]:
        placements_by_dancer[int(row["dancer_id"])].append(row)

    missing_rows = []
    lookup_outcomes: Counter[str] = Counter()
    for wsdc_id in missing_ids:
        latest = conn.execute(
            """SELECT s.fetched_at,s.snapshot_id,s.http_status,s.classification,
                      json_extract(o.payload_json,'$.outcome') outcome
               FROM observations o JOIN snapshots s USING(snapshot_id)
               WHERE o.scope_kind='dancer' AND o.scope_id=?
               ORDER BY s.fetched_at DESC,s.snapshot_id DESC LIMIT 1""",
            (str(wsdc_id),),
        ).fetchone()
        outcome = str(latest["outcome"]) if latest else "unfetched"
        lookup_outcomes[outcome] += 1
        exposed = placements_by_dancer[wsdc_id]
        dated = [(occurrences[int(row["event_occurrence_id"])], row) for row in exposed]
        dates = [str(item[0]["date"]) for item in dated]
        examples = sorted(
            {f"{item[0]['date']} {events[int(item[0]['event_id'])]['name']}" for item in dated},
            reverse=True,
        )[:3]
        ref = reference_dancers[wsdc_id]
        missing_rows.append(
            {
                "wsdc_id": wsdc_id,
                "reference_name": f"{ref['first_name']} {ref['last_name']}",
                "latest_lookup_outcome": outcome,
                "latest_lookup_fetched_at": latest["fetched_at"] if latest else "",
                "latest_lookup_snapshot_id": latest["snapshot_id"] if latest else "",
                "latest_http_status": latest["http_status"] if latest else "",
                "reference_placement_count": len(exposed),
                "earliest_reference_placement": min(dates) if dates else "",
                "latest_reference_placement": max(dates) if dates else "",
                "reference_placements_2024_on": sum(date[:4] >= "2024" for date in dates),
                "examples": " | ".join(examples),
            }
        )
    write_csv(
        output / "registry-reference-missing-dancers.csv",
        missing_rows,
        list(missing_rows[0]),
    )

    raw_name_rows = []
    for wsdc_id in sorted(reference_ids & local_ids):
        latest = conn.execute(
            """SELECT json_extract(o.payload_json,'$.first_name') first_name,
                      json_extract(o.payload_json,'$.last_name') last_name,s.fetched_at
               FROM observations o JOIN snapshots s USING(snapshot_id)
               WHERE o.scope_kind='dancer' AND o.scope_id=?
                 AND json_extract(o.payload_json,'$.outcome')='found'
               ORDER BY s.fetched_at DESC,s.snapshot_id DESC LIMIT 1""",
            (str(wsdc_id),),
        ).fetchone()
        if latest is None:
            continue
        ref = reference_dancers[wsdc_id]
        before = (str(ref["first_name"]), str(ref["last_name"]))
        current = (str(latest["first_name"]), str(latest["last_name"]))
        if before == current:
            continue
        raw_name_rows.append(
            {
                "wsdc_id": wsdc_id,
                "reference_first_name": before[0],
                "reference_last_name": before[1],
                "lookup_first_name": current[0],
                "lookup_last_name": current[1],
                "difference": "whitespace_only"
                if tuple(value.strip() for value in before)
                == tuple(value.strip() for value in current)
                else "substantive",
                "lookup_fetched_at": latest["fetched_at"],
            }
        )
    write_csv(
        output / "registry-name-differences.csv",
        raw_name_rows,
        list(raw_name_rows[0]),
    )

    mapping_rows = []
    for row in conn.execute(
        """SELECT substr(event_month,1,4) year,
          COUNT(*) placement_rows,
          SUM(event_id IS NOT NULL) mapped_rows,
          SUM(event_id IS NULL) unmapped_rows,
          COUNT(DISTINCT series_id||'|'||substr(event_month,1,7)) occurrences,
          COUNT(DISTINCT CASE WHEN event_id IS NOT NULL
            THEN series_id||'|'||substr(event_month,1,7) END) mapped_occurrences
          FROM registry_placements GROUP BY 1 ORDER BY 1"""
    ):
        item = dict(row)
        item["contract_scope"] = "in_scope" if str(row["year"]) >= "2010" else "pre_2010"
        mapping_rows.append(item)
    write_csv(
        output / "registry-event-mapping-by-year.csv",
        mapping_rows,
        list(mapping_rows[0]),
    )

    occurrence_rows = [
        dict(row)
        for row in conn.execute(
            """SELECT series_id,series_name_raw,substr(event_month,1,7) event_month,
              COUNT(*) placement_rows,MIN(wsdc_id) example_wsdc_id
              FROM registry_placements WHERE event_id IS NULL
              GROUP BY 1,2,3 ORDER BY event_month,series_id,series_name_raw"""
        )
    ]
    write_csv(
        output / "registry-unmapped-occurrences.csv",
        occurrence_rows,
        list(occurrence_rows[0]),
    )

    conflict_rows = []
    for row in conn.execute(
        """SELECT subject_id,snapshot_id,evidence_json FROM findings
           WHERE kind='conflict' AND subject_kind='registry_placement'
             AND closed_at IS NULL ORDER BY subject_id"""
    ):
        evidence = json.loads(str(row["evidence_json"]))
        conflict_rows.append(
            {
                "subject_id": row["subject_id"],
                "snapshot_id": row["snapshot_id"],
                "source_row_count": evidence.get("row_count"),
                "claims_json": json.dumps(evidence.get("claims"), sort_keys=True),
            }
        )
    write_csv(
        output / "registry-withheld-conflicts.csv",
        conflict_rows,
        list(conflict_rows[0]),
    )

    reference_roles = {int(row["id"]): str(row["name"]).casefold() for row in reference["roles"]}
    reference_divisions = {
        int(row["id"]): DIVISIONS[str(row["abbreviation"])] for row in reference["divisions"]
    }
    reference_claims = {}
    for row in reference["placements"]:
        occurrence = occurrences[int(row["event_occurrence_id"])]
        key = (
            int(row["dancer_id"]),
            reference_roles[int(row["role_id"])],
            f"wsdc-{occurrence['event_id']}",
            str(occurrence["date"])[:7],
            reference_divisions[int(row["division_id"])],
            "wcs",
        )
        reference_claims[key] = (str(row["result"]), int(row["points"]))
    local_claims = {
        tuple(row[:6]): (str(row[6]), int(row[7]))
        for row in conn.execute(
            """SELECT wsdc_id,role,series_id,substr(event_month,1,7),division,
              dance_style,result,points FROM registry_placements WHERE dance_style='wcs'"""
        )
    }
    conflict_subjects = {row["subject_id"] for row in conflict_rows}
    missing_claim_rows = []
    for key in sorted(reference_claims.keys() - local_claims.keys()):
        subject_id = "|".join(
            str(value) for value in (key[0], key[1], key[2], f"{key[3]}-01", key[4], key[5])
        )
        reason = (
            "dancer_currently_not_found"
            if key[0] in missing_ids
            else "conflicting_claim_withheld"
            if subject_id in conflict_subjects
            else "unresolved_other"
        )
        result, points = reference_claims[key]
        missing_claim_rows.append(
            {
                "wsdc_id": key[0],
                "role": key[1],
                "series_id": key[2],
                "event_month": key[3],
                "division": key[4],
                "dance_style": key[5],
                "reference_result": result,
                "reference_points": points,
                "reason": reason,
            }
        )
    write_csv(
        output / "registry-reference-missing-placement-claims.csv",
        missing_claim_rows,
        list(missing_claim_rows[0]),
    )

    ref_occurrences = {
        (f"wsdc-{row['event_id']}", str(row["date"])[:7]) for row in reference["event_occurrences"]
    }
    local_occurrences = {
        (str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT DISTINCT series_id,substr(event_month,1,7) FROM registry_placements"
        )
    }
    reference_series = {f"wsdc-{row['id']}" for row in reference["events"]}
    local_series = {
        str(row[0]) for row in conn.execute("SELECT DISTINCT series_id FROM registry_placements")
    }
    public_dancer_table = pq.read_table(capture / "candidate/data/dancers/dancers.parquet")
    public_entry_table = pq.read_table(capture / "candidate/data/entries/entries.parquet")
    public_event_table = pq.read_table(capture / "candidate/data/events/events.parquet")
    public_link_table = pq.read_table(
        capture / "candidate/data/identity_links/identity_links.parquet"
    )
    public_dancers = public_dancer_table.num_rows
    public_placements = pq.read_table(
        capture / "candidate/data/registry_placements/registry_placements.parquet"
    ).num_rows
    public_dancer_ids = {int(value) for value in public_dancer_table.column("wsdc_id").to_pylist()}
    public_events = {
        str(row["event_id"]): str(row["name"]) for row in public_event_table.to_pylist()
    }
    public_link_methods = {
        str(row["subject_id"]): str(row["method"])
        for row in public_link_table.to_pylist()
        if row["subject_kind"] == "entry"
    }
    orphan_entries: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in public_entry_table.to_pylist():
        wsdc_id = row["wsdc_id"]
        if wsdc_id is not None and int(wsdc_id) not in public_dancer_ids:
            orphan_entries[int(wsdc_id)].append(row)
    orphan_rows = []
    for wsdc_id, entries in sorted(orphan_entries.items()):
        latest = conn.execute(
            """SELECT s.fetched_at,s.snapshot_id,s.http_status,s.classification,
                      json_extract(o.payload_json,'$.outcome') outcome
               FROM observations o JOIN snapshots s USING(snapshot_id)
               WHERE o.scope_kind='dancer' AND o.scope_id=?
               ORDER BY s.fetched_at DESC,s.snapshot_id DESC LIMIT 1""",
            (str(wsdc_id),),
        ).fetchone()
        examples = [
            f"{entry['event_id']} ({public_events.get(str(entry['event_id']), '')}): "
            f"{entry['name_raw']} [{entry['role']}]"
            for entry in sorted(entries, key=lambda item: str(item["entry_id"]))[:3]
        ]
        orphan_rows.append(
            {
                "wsdc_id": wsdc_id,
                "published_entry_count": len(entries),
                "entry_sources": "|".join(sorted({str(entry["source"]) for entry in entries})),
                "link_statuses": "|".join(sorted({str(entry["link_status"]) for entry in entries})),
                "link_methods": "|".join(
                    sorted(
                        {
                            public_link_methods.get(str(entry["entry_id"]), "missing_link")
                            for entry in entries
                        }
                    )
                ),
                "latest_lookup_outcome": latest["outcome"] if latest else "unfetched",
                "latest_lookup_fetched_at": latest["fetched_at"] if latest else "",
                "latest_lookup_snapshot_id": latest["snapshot_id"] if latest else "",
                "latest_lookup_http_status": latest["http_status"] if latest else "",
                "examples": " | ".join(examples),
            }
        )
    write_csv(
        output / "registry-entry-ids-without-dancer.csv",
        orphan_rows,
        list(orphan_rows[0]),
    )
    open_unknown = conn.execute(
        "SELECT COUNT(*) FROM findings WHERE kind='unknown_enum' AND subject_kind='dancer' AND closed_at IS NULL"
    ).fetchone()[0]
    supported_values = {}
    for column in ("role", "division", "dance_style", "result"):
        supported_values[column] = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                f"SELECT {column},COUNT(*) FROM registry_placements GROUP BY {column}"
            )
        }

    in_scope = [row for row in mapping_rows if row["contract_scope"] == "in_scope"]
    pre_scope = [row for row in mapping_rows if row["contract_scope"] == "pre_2010"]
    in_scope_styles = {
        str(row["dance_style"]): {
            "rows": int(row["placement_rows"]),
            "mapped_rows": int(row["mapped_rows"]),
            "unmapped_rows": int(row["unmapped_rows"]),
            "occurrences": int(row["occurrences"]),
            "mapped_occurrences": int(row["mapped_occurrences"]),
        }
        for row in conn.execute(
            """SELECT dance_style,COUNT(*) placement_rows,
              SUM(event_id IS NOT NULL) mapped_rows,
              SUM(event_id IS NULL) unmapped_rows,
              COUNT(DISTINCT series_id||'|'||substr(event_month,1,7)) occurrences,
              COUNT(DISTINCT CASE WHEN event_id IS NOT NULL
                THEN series_id||'|'||substr(event_month,1,7) END) mapped_occurrences
              FROM registry_placements WHERE substr(event_month,1,4)>='2010'
              GROUP BY dance_style"""
        )
    }
    summary = {
        "capture": capture_meta,
        "public_counts": {
            "dancers": public_dancers,
            "registry_placements": public_placements,
            "entries_with_wsdc_id_absent_from_dancers": sum(
                len(entries) for entries in orphan_entries.values()
            ),
            "distinct_entry_wsdc_ids_absent_from_dancers": len(orphan_entries),
            "entry_wsdc_ids_absent_from_dancers_by_lookup_outcome": dict(
                sorted(Counter(str(row["latest_lookup_outcome"]) for row in orphan_rows).items())
            ),
        },
        "reference_comparison": {
            "reference_dancers": len(reference_ids),
            "shared_dancers": len(reference_ids & local_ids),
            "reference_ids_absent_locally": len(missing_ids),
            "local_ids_absent_from_reference": len(additional_ids),
            "absent_id_latest_lookup_outcomes": dict(sorted(lookup_outcomes.items())),
            "absent_ids_with_reference_placements": sum(
                bool(placements_by_dancer[wsdc_id]) for wsdc_id in missing_ids
            ),
            "absent_id_reference_placement_rows": sum(
                len(placements_by_dancer[wsdc_id]) for wsdc_id in missing_ids
            ),
            "raw_name_differences": len(raw_name_rows),
            "raw_name_whitespace_only": sum(
                row["difference"] == "whitespace_only" for row in raw_name_rows
            ),
            "raw_name_substantive": sum(
                row["difference"] == "substantive" for row in raw_name_rows
            ),
        },
        "series_and_occurrences": {
            "reference_series": len(reference_series),
            "local_series": len(local_series),
            "reference_series_missing_locally": sorted(reference_series - local_series),
            "local_series_additional": sorted(local_series - reference_series),
            "reference_occurrences": len(ref_occurrences),
            "local_occurrences": len(local_occurrences),
            "reference_occurrences_missing_locally": sorted(ref_occurrences - local_occurrences),
            "local_occurrences_additional": sorted(local_occurrences - ref_occurrences),
        },
        "event_mapping": {
            "in_scope_2010_on_rows": sum(int(row["placement_rows"]) for row in in_scope),
            "in_scope_2010_on_mapped_rows": sum(int(row["mapped_rows"]) for row in in_scope),
            "in_scope_2010_on_unmapped_rows": sum(int(row["unmapped_rows"]) for row in in_scope),
            "in_scope_2010_on_occurrences": sum(int(row["occurrences"]) for row in in_scope),
            "in_scope_2010_on_mapped_occurrences": sum(
                int(row["mapped_occurrences"]) for row in in_scope
            ),
            "pre_2010_rows": sum(int(row["placement_rows"]) for row in pre_scope),
            "pre_2010_mapped_rows": sum(int(row["mapped_rows"]) for row in pre_scope),
            "pre_2010_occurrences": sum(int(row["occurrences"]) for row in pre_scope),
            "in_scope_by_dance_style": in_scope_styles,
        },
        "normalization_and_conflicts": {
            "open_unknown_registry_enum_findings": int(open_unknown),
            "open_withheld_conflicting_claim_groups": len(conflict_rows),
            "canonical_value_counts": supported_values,
        },
        "reference_placement_claim_comparison": {
            "scope_note": "Reference placement IDs were joined through its role, division, and event_occurrence tables; its placement schema has no dance-style field, so comparison is to local WCS claims.",
            "reference_claims": len(reference_claims),
            "local_wcs_claims": len(local_claims),
            "reference_claims_missing_locally": len(missing_claim_rows),
            "missing_claim_reasons": dict(
                sorted(Counter(row["reason"] for row in missing_claim_rows).items())
            ),
            "local_claims_additional": len(local_claims.keys() - reference_claims.keys()),
            "shared_claim_value_differences": sum(
                reference_claims[key] != local_claims[key]
                for key in reference_claims.keys() & local_claims.keys()
            ),
        },
    }
    (output / "registry-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
