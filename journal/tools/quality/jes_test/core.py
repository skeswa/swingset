"""Evaluate JesAnn Nail's registry coverage against retained individual results."""

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import duckdb

BENCHMARK_VERSION = 1
WSDC_ID = 7849
TABLES = (
    "registry_placements",
    "entries",
    "contests",
    "events",
    "rounds",
    "placements",
    "callbacks",
    "callback_marks",
    "final_marks",
    "snapshots",
)
KEY_FIELDS = ("wsdc_id", "role", "series_id", "event_month", "division", "dance_style")

# Edition labels differ between the independent registry and score-sheet sources.
EVENT_ALIASES = {
    "2025-03-the-boston-tea-party": "2025-03-boston-tea-party",
    "2026-03-the-boston-tea-party": "2026-03-boston-tea-party",
    "2025-11-dc-swing-experience-dcsx": "2025-11-dc-swing-experience",
    "2025-11-northeast-swing-classic": "2025-11-northeast-swing-clasic",
    "2026-03-madjam-mid-atlantic-dance-jam": "2026-03-madjam",
}
NAME_ALIASES = {"JesAnn Nail", "Jes Ann Nail", "Jess Ann Nail"}


def canonical_name(value: str) -> str:
    """Normalize the explicit benchmark name spellings without fuzzy matching."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


ALLOWED_NAMES = {canonical_name(name) for name in NAME_ALIASES}


def registry_key(row: dict[str, Any]) -> tuple[str, ...]:
    """Return the WSDC registry's natural result key in stable string form."""
    return tuple(str(row[name]) for name in KEY_FIELDS)


def key_json(row: dict[str, Any]) -> str:
    return json.dumps(dict(zip(KEY_FIELDS, registry_key(row), strict=True)), separators=(",", ":"))


def canonical_event(event_id: str | None) -> str:
    if event_id is None:
        return ""
    return EVENT_ALIASES.get(event_id, event_id)


def score_sheet_correspondence(
    registry_rows: list[dict[str, Any]],
    entry_rows: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Match person, event, month, role and division; never inspect result or points."""
    matches: dict[str, list[dict[str, Any]]] = {}
    excluded: list[dict[str, Any]] = []
    for registry in registry_rows:
        key = key_json(registry)
        candidates = []
        for entry in entry_rows:
            if entry.get("contest_type") != "jack_and_jill":
                continue
            if entry.get("dance_style", "wcs") != registry.get("dance_style"):
                continue
            if entry.get("age_division", "none") != "none":
                sheet_division = entry["age_division"]
            else:
                sheet_division = entry.get("division")
            if sheet_division != registry.get("division") or entry.get("role") != registry.get(
                "role"
            ):
                continue
            if entry.get("event_month"):
                month = str(entry["event_month"])[:7]
            else:
                month = str(entry.get("event_date", ""))[:7]
            if month != str(registry.get("event_month", ""))[:7]:
                continue
            if canonical_event(entry.get("event_id")) != canonical_event(registry.get("event_id")):
                continue
            linked_id = entry.get("wsdc_id")
            name = canonical_name(str(entry.get("name_raw", "")))
            if linked_id is not None and int(linked_id) != WSDC_ID:
                if name in ALLOWED_NAMES:
                    excluded.append(
                        {
                            "registry_key": key,
                            "entry_id": entry.get("entry_id"),
                            "reason": "linked_to_another_wsdc_id",
                            "wsdc_id": int(linked_id),
                        }
                    )
                continue
            if linked_id is None and name not in ALLOWED_NAMES:
                continue
            candidates.append(entry)
        # Do not guess when a registry entry could correspond to multiple
        # individual entries. The report retains each ambiguous candidate.
        matches[key] = candidates
    return matches, excluded


def evidence_depth(
    entry: dict[str, Any],
    rounds: list[dict[str, Any]],
    placements: list[dict[str, Any]],
    callbacks: list[dict[str, Any]],
    callback_marks: list[dict[str, Any]],
    final_marks: list[dict[str, Any]],
    snapshot_ids: set[str],
) -> dict[str, Any]:
    """Describe independently retained participation, final, and score evidence."""
    entry_id = entry["entry_id"]
    known_rounds = {r["round_id"] for r in rounds if r.get("entry_count") is not None}
    entry_rounds = [
        name
        for name in entry.get("rounds_danced", [])
        if f"{entry['contest_id']}/{name}" in known_rounds
    ]
    entry_placements = [
        p
        for p in placements
        if entry_id
        in {p.get("leader_entry_id"), p.get("follower_entry_id"), p.get("couple_entry_id")}
    ]
    callback_rows = [
        c for c in callbacks if c.get("entry_id") == entry_id and c.get("round_id") in known_rounds
    ]
    received_callback_marks = [
        m
        for m in callback_marks
        if m.get("entry_id") == entry_id and m.get("round_id") in known_rounds
    ]
    received_final_marks = [
        m
        for m in final_marks
        if m.get("placement_id") in {p.get("placement_id") for p in entry_placements}
    ]
    snapshot_id = str(entry.get("snapshot_id", ""))
    source_verified = bool(snapshot_id and snapshot_id in snapshot_ids)
    final_recorded = bool(entry_placements) or "final" in entry_rounds
    participation = source_verified and bool(
        entry_rounds
        or entry_placements
        or callback_rows
        or received_callback_marks
        or received_final_marks
    )
    return {
        "participation": participation,
        "final_recorded": final_recorded and source_verified,
        "judge_marks_recorded": source_verified
        and bool(received_callback_marks or received_final_marks),
        "rounds_danced": sorted(set(entry_rounds)),
        "placement_count": len(entry_placements),
        "final_places": sorted(
            {int(p["place"]) for p in entry_placements if p.get("place") is not None}
        ),
        "callback_outcome_count": len(callback_rows),
        "callback_mark_count": len(received_callback_marks),
        "final_mark_count": len(received_final_marks),
        "snapshot_id": snapshot_id or None,
        "snapshot_verified": source_verified,
    }


def agreement_status(
    registry_result: str,
    evidence: dict[str, Any],
) -> tuple[str, str]:
    """Compare a registry claim with final evidence without filling in missing rank."""
    final_places = evidence["final_places"]
    if len(final_places) > 1:
        return "conflicting_evidence", f"score sheets report different final places: {final_places}"
    if not evidence["final_recorded"]:
        return "insufficient_evidence", "individual results do not establish a final placement"
    if final_places:
        sheet_place = final_places[0]
        if registry_result == "F":
            if sheet_place > 5:
                return (
                    "agrees",
                    "both sources establish a finalist place below the registry's top-five detail",
                )
            return "disagrees", f"registry reports F; score sheet reports {sheet_place}"
        if str(sheet_place) == registry_result:
            return "agrees", f"both sources report place {sheet_place}"
        return "disagrees", f"registry reports {registry_result}; score sheet reports {sheet_place}"
    if registry_result == "F":
        return "agrees", "both sources establish a final; the score sheet gives no exact place"
    return "insufficient_evidence", "final recorded but no exact score-sheet place is retained"


def evaluate(tables: dict[str, list[dict[str, Any]]], release: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a Parquet-shaped table set and return a reproducible report model."""
    registry_rows = [row for row in tables["registry_placements"] if int(row["wsdc_id"]) == WSDC_ID]
    registry_rows.sort(
        key=lambda row: (
            str(row["event_month"]),
            str(row["series_id"]),
            str(row["division"]),
            str(row["role"]),
        )
    )
    entries = tables["entries"]
    matches, identity_exclusions = score_sheet_correspondence(registry_rows, entries)
    rounds, placements = tables["rounds"], tables["placements"]
    callbacks, callback_marks, final_marks = (
        tables["callbacks"],
        tables["callback_marks"],
        tables["final_marks"],
    )
    snapshot_ids = {str(row["snapshot_id"]) for row in tables["snapshots"]}
    output_rows = []
    for registry in registry_rows:
        key = key_json(registry)
        candidates = matches[key]
        row: dict[str, Any] = {
            "registry_key": key,
            "wsdc_id": WSDC_ID,
            "role": registry["role"],
            "dance_style": registry["dance_style"],
            "division": registry["division"],
            "series_id": registry["series_id"],
            "series_name": registry.get("series_name_raw"),
            "event_month": str(registry["event_month"])[:7],
            "event_id": registry.get("event_id"),
            "registry_result": str(registry["result"]),
            "registry_points": registry.get("points"),
            "registry_snapshot_id": registry.get("snapshot_id"),
            "entry_ids": [entry["entry_id"] for entry in candidates],
            "identity_basis": [
                "wsdc_id" if entry.get("wsdc_id") == WSDC_ID else "explicit_name_alias"
                for entry in candidates
            ],
            "entry_names": [entry.get("name_raw") for entry in candidates],
        }
        depths = [
            evidence_depth(
                entry, rounds, placements, callbacks, callback_marks, final_marks, snapshot_ids
            )
            for entry in candidates
        ]
        row["evidence"] = depths
        row["has_individual_result"] = len(depths) == 1 and depths[0]["participation"]
        row["coverage_status"] = (
            "ambiguous"
            if len(candidates) > 1
            else "covered"
            if row["has_individual_result"]
            else "unverified"
            if len(candidates) == 1
            else "uncovered"
        )
        row["has_final_result"] = len(depths) == 1 and depths[0]["final_recorded"]
        row["has_judge_marks"] = len(depths) == 1 and depths[0]["judge_marks_recorded"]
        if len(depths) == 1:
            row["agreement"], row["agreement_note"] = agreement_status(
                str(registry["result"]), depths[0]
            )
        elif len(depths) > 1:
            row["agreement"], row["agreement_note"] = (
                "conflicting_evidence",
                "multiple individual entries match this registry claim",
            )
        else:
            row["agreement"], row["agreement_note"] = (
                "insufficient_evidence",
                "no corresponding individual result",
            )
        output_rows.append(row)

    coverage = sum(row["has_individual_result"] for row in output_rows)
    final_coverage = sum(row["has_final_result"] for row in output_rows)
    mark_coverage = sum(row["has_judge_marks"] for row in output_rows)
    agreement = Counter(row["agreement"] for row in output_rows if row["has_individual_result"])
    known_agreements = agreement["agrees"] + agreement["disagrees"]
    years: dict[str, dict[str, int]] = defaultdict(
        lambda: {"registry": 0, "covered": 0, "final": 0, "judge_marks": 0}
    )
    divisions: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"registry": 0, "covered": 0, "final": 0, "judge_marks": 0}
    )
    for row in output_rows:
        year_bucket = years[row["event_month"][:4]]
        division_bucket = divisions[(row["division"], row["role"])]
        for bucket in (year_bucket, division_bucket):
            bucket["registry"] += 1
            bucket["covered"] += int(row["has_individual_result"])
            bucket["final"] += int(row["has_final_result"])
            bucket["judge_marks"] += int(row["has_judge_marks"])
    config_payload = {
        "benchmark_version": BENCHMARK_VERSION,
        "wsdc_id": WSDC_ID,
        "name_aliases": sorted(NAME_ALIASES),
        "event_aliases": EVENT_ALIASES,
        "key_fields": KEY_FIELDS,
    }
    config_digest = hashlib.sha256(
        json.dumps(config_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "benchmark": "Jes Test",
        "benchmark_version": BENCHMARK_VERSION,
        "wsdc_id": WSDC_ID,
        "release": release,
        "configuration_sha256": config_digest,
        "denominator": len(output_rows),
        "coverage": {
            "individual_results": coverage,
            "individual_results_percent": round(100 * coverage / len(output_rows), 1)
            if output_rows
            else None,
            "final_results": final_coverage,
            "final_results_percent": round(100 * final_coverage / len(output_rows), 1)
            if output_rows
            else None,
            "individual_judge_marks": mark_coverage,
            "individual_judge_marks_percent": round(100 * mark_coverage / len(output_rows), 1)
            if output_rows
            else None,
            "uncovered": sum(
                not row["has_individual_result"] and row["coverage_status"] != "ambiguous"
                for row in output_rows
            ),
            "ambiguous": sum(row["coverage_status"] == "ambiguous" for row in output_rows),
            "identity_conflicts": len(identity_exclusions),
        },
        "agreement": {
            "agrees": agreement["agrees"],
            "disagrees": agreement["disagrees"],
            "insufficient_evidence": agreement["insufficient_evidence"],
            "conflicting_evidence": agreement["conflicting_evidence"],
            "assessable": known_agreements,
            "percent_agree_when_assessable": round(100 * agreement["agrees"] / known_agreements, 1)
            if known_agreements
            else None,
        },
        "by_year": dict(sorted(years.items())),
        "by_division_and_role": {
            f"{division}|{role}": values for (division, role), values in sorted(divisions.items())
        },
        "identity_exclusions": identity_exclusions,
        "rows": output_rows,
        "notes": [
            "A registry entry is covered by one uniquely corresponding individual Jack & Jill entry with verified retained snapshot provenance and participation, final, callback, or judge-mark evidence.",
            "Correspondence uses person, edition, month, role, division, and style; placement and points are excluded from the match.",
            "Explicit name aliases are benchmark-only and do not create production identity links.",
            "Registry points are reported as context and are not treated as independent validation.",
            "Coverage and agreement describe this dataset, not all real-world competition appearances.",
        ],
    }


def load_dataset(directory: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Load and hash-check the tables consumed by the benchmark."""
    manifest_path = directory / "_meta" / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    hashes = manifest.get("files", {})
    table_files: dict[str, list[str]] = {}
    for table in TABLES:
        prefix = f"data/{table}/"
        files = [name for name in hashes if name.startswith(prefix) and name.endswith(".parquet")]
        if not files:
            raise ValueError(f"release manifest has no Parquet input for {table}")
        for name in files:
            actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
            if actual != hashes[name]:
                raise ValueError(f"release file failed manifest verification: {name}")
        table_files[table] = files
    connection = duckdb.connect()
    connection.execute("SET TimeZone='UTC'")
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        paths = [str(directory / name).replace("'", "''") for name in table_files[table]]
        file_list = "[" + ",".join(f"'{path}'" for path in paths) + "]"
        connection.execute(
            f'CREATE VIEW "{table}" AS SELECT * FROM read_parquet({file_list}, hive_partitioning=false)'
        )
    for table in TABLES:
        if table == "entries":
            query = """SELECT e.*,c.division,c.age_division,c.contest_type,c.dance_style,
                      v.event_month FROM entries e
                      JOIN contests c USING(contest_id)
                      JOIN events v ON v.event_id=e.event_id"""
        else:
            query = f'SELECT * FROM "{table}"'
        tables[table] = connection.execute(query).to_arrow_table().to_pylist()
    connection.close()
    published_path = directory / "PUBLISHED"
    published = json.loads(published_path.read_bytes()) if published_path.exists() else None
    release = {
        "candidate_id": manifest.get("candidate_id"),
        "commit": published.get("commit") if published else None,
        "evidence_cutoff": published.get("evidence_cutoff") if published else None,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "published_verified_at": published.get("verified_at") if published else None,
        "built_at": manifest.get("built_at"),
    }
    return tables, release


def compare_baseline(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Report changes on shared natural keys and retain denominator additions/removals."""
    old = {row["registry_key"]: row for row in baseline["rows"]}
    new = {row["registry_key"]: row for row in current["rows"]}
    shared = old.keys() & new.keys()
    gained = sorted(
        key
        for key in shared
        if not old[key]["has_individual_result"] and new[key]["has_individual_result"]
    )
    lost = sorted(
        key
        for key in shared
        if old[key]["has_individual_result"] and not new[key]["has_individual_result"]
    )
    new_disagreements = sorted(
        key
        for key in shared
        if new[key]["agreement"] == "disagrees" and old[key]["agreement"] != "disagrees"
    )
    changed_claims = [
        {
            "registry_key": key,
            "previous_result": old[key]["registry_result"],
            "current_result": new[key]["registry_result"],
            "previous_points": old[key]["registry_points"],
            "current_points": new[key]["registry_points"],
        }
        for key in sorted(shared)
        if (old[key]["registry_result"], old[key]["registry_points"])
        != (new[key]["registry_result"], new[key]["registry_points"])
    ]
    return {
        "baseline_commit": baseline["release"].get("commit"),
        "baseline_candidate_id": baseline["release"].get("candidate_id"),
        "baseline_denominator": len(old),
        "current_denominator": len(new),
        "shared_registry_entries": len(shared),
        "added_registry_entries": sorted(new.keys() - old.keys()),
        "removed_registry_entries": sorted(old.keys() - new.keys()),
        "coverage_gained": gained,
        "coverage_lost": lost,
        "new_disagreements": new_disagreements,
        "registry_claim_changes": changed_claims,
        "final_evidence_gained": sorted(
            key
            for key in shared
            if not old[key]["has_final_result"] and new[key]["has_final_result"]
        ),
        "final_evidence_lost": sorted(
            key
            for key in shared
            if old[key]["has_final_result"] and not new[key]["has_final_result"]
        ),
        "judge_mark_evidence_gained": sorted(
            key for key in shared if not old[key]["has_judge_marks"] and new[key]["has_judge_marks"]
        ),
        "judge_mark_evidence_lost": sorted(
            key for key in shared if old[key]["has_judge_marks"] and not new[key]["has_judge_marks"]
        ),
        "coverage_percent_on_shared_entries": round(
            100 * sum(new[key]["has_individual_result"] for key in shared) / len(shared), 1
        )
        if shared
        else None,
    }


def write_report(
    report: dict[str, Any], output: Path, baseline: dict[str, Any] | None = None
) -> None:
    """Write deterministic JSON, CSV, and Markdown report files."""
    output.mkdir(parents=True, exist_ok=False)
    if baseline is not None:
        report["baseline_comparison"] = compare_baseline(report, baseline)
    rows = report["rows"]
    summary = {key: value for key, value in report.items() if key != "rows"}
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    (output / "entries.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    columns = (
        "registry_key",
        "event_month",
        "series_name",
        "role",
        "division",
        "dance_style",
        "registry_result",
        "registry_points",
        "coverage_status",
        "has_individual_result",
        "has_final_result",
        "has_judge_marks",
        "agreement",
        "agreement_note",
        "entry_ids",
        "identity_basis",
        "entry_names",
        "evidence",
    )
    with (output / "entries.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(row.get(key), sort_keys=True)
                    if isinstance(row.get(key), (list, dict))
                    else row.get(key)
                    for key in columns
                }
            )
    (output / "report.md").write_text(render_markdown(report))


def render_markdown(report: dict[str, Any]) -> str:
    coverage, agreement = report["coverage"], report["agreement"]
    percent = coverage["individual_results_percent"]
    lines = [
        "# Jes Test",
        "",
        f"Dataset candidate: `{report['release'].get('candidate_id')}`; published commit: `{report['release'].get('commit')}`.",
        "",
        "## Registry coverage",
        "",
        f"**{coverage['individual_results']}/{report['denominator']} ({percent if percent is not None else 'not assessable'}%)** registry entries have a unique individual result.",
        "",
        f"- Final result evidence: {coverage['final_results']}/{report['denominator']} ({coverage['final_results_percent']}%).",
        f"- Individual judge marks: {coverage['individual_judge_marks']}/{report['denominator']} ({coverage['individual_judge_marks_percent']}%).",
        f"- Uncovered: {coverage['uncovered']}; ambiguous: {coverage['ambiguous']}; identity conflicts: {coverage['identity_conflicts']}.",
        "",
        "## Registry and result agreement",
        "",
        f"{agreement['agrees']} agree; {agreement['disagrees']} disagree; {agreement['insufficient_evidence']} lack enough final evidence; {agreement['conflicting_evidence']} have conflicting score sheets.",
        f"Agreement among assessable entries: {agreement['percent_agree_when_assessable'] if agreement['percent_agree_when_assessable'] is not None else 'not assessable'}% ({agreement['assessable']} entries).",
        "",
        "## Coverage by year",
        "",
        "| Year | Registry | Individual result | Final result | Judge marks |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for year, values in report["by_year"].items():
        lines.append(
            f"| {year} | {values['registry']} | {values['covered']} | {values['final']} | {values['judge_marks']} |"
        )
    baseline = report.get("baseline_comparison")
    if baseline is not None:
        lines.extend(
            [
                "",
                "## Baseline comparison",
                "",
                f"Shared registry entries: {baseline['shared_registry_entries']}; gained coverage: {len(baseline['coverage_gained'])}; lost coverage: {len(baseline['coverage_lost'])}; new disagreements: {len(baseline['new_disagreements'])}.",
                f"Final evidence gained/lost: {len(baseline['final_evidence_gained'])}/{len(baseline['final_evidence_lost'])}; judge mark evidence gained/lost: {len(baseline['judge_mark_evidence_gained'])}/{len(baseline['judge_mark_evidence_lost'])}.",
                f"Changed registry claims: {len(baseline['registry_claim_changes'])}; registry entries added: {len(baseline['added_registry_entries'])}; removed: {len(baseline['removed_registry_entries'])}.",
            ]
        )
    lines.extend(
        ["", "## Method and limitations", "", *[f"- {note}" for note in report["notes"]], ""]
    )
    return "\n".join(lines)
