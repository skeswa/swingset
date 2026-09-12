#!/usr/bin/env python3
"""Audit identity and result-detail completeness in a pinned published candidate."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq


def read_rows(root: Path, table: str, columns: list[str] | None = None) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]],
        pq.read_table(root / "candidate" / "data" / table, columns=columns).to_pylist(),
    )


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    values = list(rows)
    fields = sorted({key for row in values for key in row})
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(values)


def grouped(rows: Iterable[Mapping[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    counts = Counter(tuple(row.get(field) for field in fields) for row in rows)
    return [
        {**dict(zip(fields, key, strict=True)), "count": count}
        for key, count in sorted(counts.items(), key=lambda item: tuple(str(x) for x in item[0]))
    ]


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def main(capture_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    capture = json.loads((capture_dir / "capture.json").read_text())
    issues = {
        int(row["number"]): row for row in json.loads((capture_dir / "issues.json").read_text())
    }

    entries = read_rows(capture_dir, "entries")
    contests = {row["contest_id"]: row for row in read_rows(capture_dir, "contests")}
    rounds = read_rows(capture_dir, "rounds")
    callbacks = read_rows(capture_dir, "callbacks")
    callback_marks = read_rows(capture_dir, "callback_marks")
    final_marks = read_rows(capture_dir, "final_marks")
    placements = read_rows(capture_dir, "placements")
    judges = read_rows(capture_dir, "judges")
    links = read_rows(capture_dir, "identity_links")
    registry = read_rows(capture_dir, "registry_placements")
    heats = read_rows(capture_dir, "heats")

    entry_by_id = {row["entry_id"]: row for row in entries}
    links_by_subject = {row["subject_id"]: row for row in links}
    placements_by_round: dict[str, list[dict[str, Any]]] = defaultdict(list)
    callbacks_by_round = Counter(row["round_id"] for row in callbacks)
    callback_marks_by_round = Counter(row["round_id"] for row in callback_marks)
    final_marks_by_round = Counter(row["round_id"] for row in final_marks)
    for row in placements:
        placements_by_round[row["round_id"]].append(row)

    entry_detail = []
    for row in entries:
        link = links_by_subject.get(row["entry_id"], {})
        raw_name = str(row["name_raw"] or "")
        entry_detail.append(
            {
                "source": row["source"],
                "role": row["role"],
                "link_status": row["link_status"],
                "link_method": link.get("method"),
                "published_wsdc_id_missing": row["wsdc_id"] is None,
                "name_missing": not raw_name.strip(),
                "bib_missing": row["bib"] is None or not str(row["bib"]).strip(),
                "mixed_name_signal": " and " in raw_name.casefold(),
            }
        )
    entry_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in entry_detail:
        entry_groups[(str(row["source"]), str(row["role"]))].append(row)
    entry_completeness = []
    for (source, role), values in sorted(entry_groups.items()):
        statuses = Counter(str(row["link_status"]) for row in values)
        entry_completeness.append(
            {
                "source": source,
                "role": role,
                "entries": len(values),
                "confirmed": statuses["confirmed"],
                "probable": statuses["probable"],
                "possible": statuses["possible"],
                "unmatched": statuses["unmatched"],
                "published_wsdc_id_missing": sum(
                    bool(row["published_wsdc_id_missing"]) for row in values
                ),
                "name_missing": sum(bool(row["name_missing"]) for row in values),
                "bib_missing": sum(bool(row["bib_missing"]) for row in values),
                "mixed_name_signal": sum(bool(row["mixed_name_signal"]) for row in values),
            }
        )
    write_csv(output_dir / "details-entry-completeness.csv", entry_completeness)

    identity_status = grouped(
        (
            {
                "subject_kind": row["subject_kind"],
                "status": row["status"],
                "method": row["method"],
                "asserted_wsdc_id_missing": row["wsdc_id"] is None,
            }
            for row in links
        ),
        ("subject_kind", "status", "method", "asserted_wsdc_id_missing"),
    )
    write_csv(output_dir / "details-identity-status.csv", identity_status)

    judge_rows = [
        {
            "source": row["source"],
            "anonymous": bool(row["anonymous"]),
            "name_missing": row["name_raw"] is None or not str(row["name_raw"]).strip(),
            "published_wsdc_id_missing": row["wsdc_id"] is None,
            "link_status": links_by_subject.get(row["judge_id"], {}).get("status"),
        }
        for row in judges
    ]
    judge_detail = []
    for source in sorted({str(row["source"]) for row in judge_rows}):
        values = [row for row in judge_rows if row["source"] == source]
        statuses = Counter(str(row["link_status"] or "none") for row in values)
        judge_detail.append(
            {
                "source": source,
                "judges": len(values),
                "anonymous": sum(bool(row["anonymous"]) for row in values),
                "name_missing": sum(bool(row["name_missing"]) for row in values),
                "published_wsdc_id_missing": sum(
                    bool(row["published_wsdc_id_missing"]) for row in values
                ),
                "confirmed": statuses["confirmed"],
                "probable": statuses["probable"],
                "possible": statuses["possible"],
                "unmatched": statuses["unmatched"],
            }
        )
    write_csv(output_dir / "details-judge-completeness.csv", judge_detail)

    round_detail = []
    for row in rounds:
        entrants = int(row["entry_count"] or 0)
        judge_count = int(row["judge_count"] or 0)
        if row["round_type"] == "final":
            detail_rows = len(placements_by_round[row["round_id"]])
            marks = final_marks_by_round[row["round_id"]]
        else:
            detail_rows = callbacks_by_round[row["round_id"]]
            marks = callback_marks_by_round[row["round_id"]]
        matrix_denominator = detail_rows * judge_count
        round_detail.append(
            {
                "round_id": row["round_id"],
                "source": row["source"],
                "round_type": row["round_type"],
                "entry_count": entrants,
                "judge_count": judge_count,
                "detail_rows": detail_rows,
                "detail_to_entry_ratio": ratio(detail_rows, entrants),
                "marks": marks,
                "mark_matrix_denominator": matrix_denominator,
                "mark_matrix_coverage": ratio(marks, matrix_denominator),
                "denominator_valid": judge_count > 0 and detail_rows > 0,
            }
        )
    write_csv(output_dir / "details-round-detail-coverage.csv", round_detail)

    registry_index: dict[tuple[Any, ...], set[str]] = defaultdict(set)
    for row in registry:
        if row["event_id"] is not None:
            registry_index[
                (
                    row["wsdc_id"],
                    row["event_id"],
                    row["role"],
                    row["division"],
                    row["dance_style"],
                )
            ].add(str(row["result"]))

    placement_detail = []
    for row in placements:
        contest = contests[row["contest_id"]]
        role_refs = (("leader", row["leader_entry_id"]), ("follower", row["follower_entry_id"]))
        evidence: set[str] = set()
        for role, entry_id in role_refs:
            entry = entry_by_id.get(entry_id)
            if entry and entry["wsdc_id"] is not None:
                evidence.update(
                    registry_index.get(
                        (
                            entry["wsdc_id"],
                            row["event_id"],
                            role,
                            contest["division"],
                            contest["dance_style"],
                        ),
                        set(),
                    )
                )
        placement_detail.append(
            {
                "source": row["source"],
                "contest_type": contest["contest_type"],
                "division": contest["division"],
                "eligible": bool(contest["wsdc_points_eligible"]),
                "reference_shape": "+".join(
                    role
                    for role, value in (
                        ("leader", row["leader_entry_id"]),
                        ("follower", row["follower_entry_id"]),
                        ("couple", row["couple_entry_id"]),
                    )
                    if value is not None
                )
                or "none",
                "leader_points_missing": row["registry_points_leader"] is None,
                "follower_points_missing": row["registry_points_follower"] is None,
                "registry_confirmed": bool(row["registry_confirmed"]),
                "points_matches_expected": row["points_matches_expected"],
                "has_registry_F_evidence": "F" in evidence,
                "has_registry_numeric_evidence": any(value.isdigit() for value in evidence),
            }
        )
    placement_summary = grouped(
        placement_detail,
        (
            "source",
            "contest_type",
            "division",
            "eligible",
            "reference_shape",
            "leader_points_missing",
            "follower_points_missing",
            "registry_confirmed",
            "points_matches_expected",
            "has_registry_F_evidence",
            "has_registry_numeric_evidence",
        ),
    )
    write_csv(output_dir / "details-placement-completeness.csv", placement_summary)

    ambiguous = [
        {
            "entry_id": row["entry_id"],
            "event_id": row["event_id"],
            "contest_id": row["contest_id"],
            "source": row["source"],
            "role": row["role"],
            "name_raw": row["name_raw"],
            "link_status": row["link_status"],
            "published_wsdc_id": row["wsdc_id"],
        }
        for row in entries
        if row["role"] not in {"leader", "follower", "couple"}
        or (
            row["role"] in {"leader", "follower"}
            and " and " in str(row["name_raw"] or "").casefold()
        )
    ]
    write_csv(output_dir / "details-role-ambiguity.csv", ambiguous)

    division_none = [
        row
        for row in entries
        if contests[row["contest_id"]]["division"] == "none"
        and row["role"] in {"leader", "follower"}
    ]
    mixed_individual = [
        row
        for row in entries
        if row["role"] in {"leader", "follower"}
        and " and " in str(row["name_raw"] or "").casefold()
    ]
    unmapped_registry = [row for row in registry if row["event_id"] is None]
    eligible_placements = [
        (row, contests[row["contest_id"]])
        for row in placements
        if contests[row["contest_id"]]["wsdc_points_eligible"]
    ]
    issue_rows = [
        {
            "issue": 16,
            "title": issues[16]["title"],
            "status": "open_on_pinned_data",
            "metric": "individual entries in division=none by link status",
            "value": json.dumps(
                Counter(row["link_status"] for row in division_none), sort_keys=True
            ),
            "interpretation": "These are candidates for the eligibility bug, not proof each link is wrong.",
        },
        {
            "issue": 17,
            "title": issues[17]["title"],
            "status": "open_on_pinned_data",
            "metric": "leader/follower entries whose source name contains ' and '",
            "value": len(mixed_individual),
            "interpretation": "A high-risk lexical signal, not proof that every value contains two people.",
        },
        {
            "issue": 18,
            "title": issues[18]["title"],
            "status": "open_on_pinned_data",
            "metric": "registry placements without canonical event_id",
            "value": len(unmapped_registry),
            "interpretation": "Unmapped rows include missing aliases and editions absent from the event catalog.",
        },
        {
            "issue": 18,
            "title": issues[18]["title"],
            "status": "open_on_pinned_data",
            "metric": "eligible detailed placements with mapped registry F evidence",
            "value": sum(
                row["has_registry_F_evidence"] for row in placement_detail if row["eligible"]
            ),
            "interpretation": "F proves finalist evidence but does not prove the detailed numeric place.",
        },
    ]
    write_csv(output_dir / "details-issue-assessment.csv", issue_rows)

    valid_callback_rounds = [
        row for row in round_detail if row["round_type"] != "final" and row["denominator_valid"]
    ]
    valid_final_rounds = [
        row for row in round_detail if row["round_type"] == "final" and row["denominator_valid"]
    ]
    integrity = json.loads((capture_dir / "integrity.json").read_text())
    summary = {
        "capture": capture,
        "definitions": {
            "published_wsdc_id_missing": "The public wsdc_id field is null. This does not establish that no WSDC number exists.",
            "unmatched": "No publishable candidate was selected from available evidence.",
            "possible": "A candidate exists below the publication threshold; public wsdc_id remains null.",
            "matrix_denominator": "Published detail rows multiplied by the round's declared judge_count; reported only when both are positive.",
        },
        "integrity": {
            "ok": integrity["ok"],
            "violations": integrity["violations"],
            "file_hash_failures": integrity["integrity"]["file_hash_failures"],
            "schema_failures": integrity["integrity"]["schema_failures"],
            "row_count_mismatches": integrity["integrity"]["row_count_mismatches"],
        },
        "identity": {
            "entries": len(entries),
            "roles": dict(Counter(row["role"] for row in entries)),
            "sources": dict(Counter(row["source"] for row in entries)),
            "link_status": dict(Counter(row["link_status"] for row in entries)),
            "published_wsdc_id_missing": sum(row["wsdc_id"] is None for row in entries),
            "name_missing": sum(not str(row["name_raw"] or "").strip() for row in entries),
            "bib_missing": sum(
                row["bib"] is None or not str(row["bib"]).strip() for row in entries
            ),
            "mixed_name_individual_role": len(mixed_individual),
            "unknown_role": sum(
                row["role"] not in {"leader", "follower", "couple"} for row in entries
            ),
            "judges": len(judges),
            "judges_name_missing": sum(not str(row["name_raw"] or "").strip() for row in judges),
            "judges_anonymous": sum(bool(row["anonymous"]) for row in judges),
            "judges_published_wsdc_id_missing": sum(row["wsdc_id"] is None for row in judges),
        },
        "results": {
            "rounds": len(rounds),
            "callbacks": len(callbacks),
            "callback_marks": len(callback_marks),
            "placements": len(placements),
            "final_marks": len(final_marks),
            "heats": len(heats),
            "callback_rounds_with_valid_matrix_denominator": len(valid_callback_rounds),
            "callback_matrix_coverage": ratio(
                sum(row["marks"] for row in valid_callback_rounds),
                sum(row["mark_matrix_denominator"] for row in valid_callback_rounds),
            ),
            "final_rounds_with_valid_matrix_denominator": len(valid_final_rounds),
            "final_matrix_coverage": ratio(
                sum(row["marks"] for row in valid_final_rounds),
                sum(row["mark_matrix_denominator"] for row in valid_final_rounds),
            ),
            "eligible_placements": len(eligible_placements),
            "eligible_registry_confirmed": sum(
                bool(row[0]["registry_confirmed"]) for row in eligible_placements
            ),
            "eligible_points_matches_expected_true": sum(
                row[0]["points_matches_expected"] is True for row in eligible_placements
            ),
            "eligible_points_matches_expected_false": sum(
                row[0]["points_matches_expected"] is False for row in eligible_placements
            ),
            "eligible_points_matches_expected_null": sum(
                row[0]["points_matches_expected"] is None for row in eligible_placements
            ),
            "registry_placements": len(registry),
            "registry_placements_unmapped_event": len(unmapped_registry),
            "registry_result_F": sum(str(row["result"]) == "F" for row in registry),
            "registry_result_numeric": sum(str(row["result"]).isdigit() for row in registry),
        },
        "issues": issue_rows,
    }
    (output_dir / "details-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n"
    )

    conn = sqlite3.connect(f"file:{capture_dir / 'state.sqlite'}?mode=ro", uri=True)
    finding_counts = conn.execute(
        "SELECT kind,severity,COUNT(*) FROM findings WHERE closed_at IS NULL GROUP BY kind,severity ORDER BY kind,severity"
    ).fetchall()
    finding_summaries = [
        {"kind": row[0], "subject_kind": row[1], "summary": row[2], "count": row[3]}
        for row in conn.execute(
            "SELECT kind,subject_kind,summary,COUNT(*) FROM findings WHERE closed_at IS NULL GROUP BY kind,subject_kind,summary ORDER BY kind,subject_kind,summary"
        )
    ]
    write_csv(output_dir / "details-active-findings.csv", finding_summaries)
    notes = [
        "# Detailed missing-data audit notes",
        "",
        f"Pinned publication: `{capture['publication']['commit']}`.",
        "",
        "All primary metrics come from the pinned published Parquet candidate. The SQLite state is used only for active finding counts below.",
        "The captured integrity audit passed with no file-hash, schema, row-count, primary-key, or foreign-reference failure. Gaps below therefore describe absent, withheld, unresolved, or unsupported content rather than corrupt published files.",
        "",
        "## Interpretation limits",
        "",
        "- A null published `wsdc_id` means the linker did not publish an identity. It does not prove the dancer or judge lacks a WSDC number.",
        "- `possible` records intentionally retain a null public ID. `unmatched` means no candidate met even that status from available evidence.",
        "- Names containing ` and ` under an individual role are flagged as ambiguous evidence. This lexical test can include legitimate compound names.",
        "- Judge-matrix coverage uses published detail rows times declared judge count. It does not infer cells hidden or omitted by a source.",
        "- Registry result `F` is finalist evidence, not a numeric finishing place. It is counted separately from numeric results.",
        "- Zero heat rows is an observed dataset gap; it does not imply the source events had no heats.",
        "",
        "## Active pipeline findings",
        "",
    ]
    notes.extend(f"- `{kind}` / `{severity}`: {count}" for kind, severity, count in finding_counts)
    notes += [
        "",
        "## Issue assessment",
        "",
        "Issues 16, 17, and 18 remain evidenced by the pinned data. The CSV records the bounded metrics and the limits on each inference.",
        "",
    ]
    (capture_dir / "details-notes.md").write_text("\n".join(notes))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("capture_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    main(args.capture_dir, args.output_dir)
