"""Read-only alias proposals with retained row evidence and explicit human decisions."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from swingset.fetch.archive import canonical, durable_write
from swingset.model.ids import series_slug


def _next_month(month: str) -> str:
    year, number = map(int, month.split("-"))
    return f"{year + (number == 12):04d}-{number % 12 + 1:02d}"


def _registry(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    series: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT series_id,series_name_raw,event_month,MIN(snapshot_id) AS snapshot_id,"
        "COUNT(*) AS placements FROM registry_placements GROUP BY series_id,series_name_raw,event_month"
    ):
        item = series.setdefault(
            row["series_id"], {"names": set(), "months": set(), "occurrences": []}
        )
        item["names"].add(row["series_name_raw"])
        item["months"].add(row["event_month"][:7])
        item["occurrences"].append(dict(row))
    return series


def _evidence(conn: sqlite3.Connection, keys: set[str]) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT o.observation_id,o.payload_json,o.snapshot_id,w.source,s.url,s.archive_url,"
        "s.captured_at,s.fetched_at,s.body_sha256,o.parser_version FROM observations o "
        "JOIN snapshots s USING(snapshot_id) JOIN watches w USING(watch_id) "
        "WHERE o.scope_kind='calendar' ORDER BY s.observed_at,s.fetched_at,o.seq"
    ):
        payload = json.loads(row["payload_json"])
        key = series_slug(payload.get("name_raw", ""))
        if key in keys:
            item = dict(row)
            del item["payload_json"]
            item["raw_row"] = payload
            evidence[key].append(item)
    for row in conn.execute(
        "SELECT se.name_raw,se.start_date,se.end_date,se.location_raw,se.url AS listing_url,"
        "se.source,se.snapshot_id,se.parser_version,s.url,s.archive_url,s.captured_at,s.fetched_at,"
        "s.body_sha256 FROM source_events se JOIN snapshots s USING(snapshot_id) "
        "WHERE se.start_date IS NOT NULL AND se.end_date IS NOT NULL"
    ):
        key = series_slug(row["name_raw"] or "")
        if key in keys:
            item = dict(row)
            item["raw_row"] = {
                "name_raw": item.pop("name_raw"),
                "start_date_raw": item.pop("start_date"),
                "end_date_raw": item.pop("end_date"),
                "location_raw": item.pop("location_raw"),
                "website": item.pop("listing_url"),
            }
            evidence[key].append(item)
    return evidence


def _candidates(group: dict[str, Any], registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    key = group["group_id"]
    months = set(group["months"])
    following = {_next_month(month) for month in months}
    for identifier, series in registry.items():
        similarity = max(
            SequenceMatcher(None, key, series_slug(name)).ratio() for name in series["names"]
        )
        tokens = set(key.split("-"))
        token_overlap = max(
            len(tokens & set(series_slug(name).split("-")))
            / max(1, len(tokens | set(series_slug(name).split("-"))))
            for name in series["names"]
        )
        lexical = max(similarity, token_overlap)
        if lexical < 0.35 and identifier not in group["exact_candidates"]:
            continue
        same = sorted(months & series["months"])
        next_month = sorted((following & series["months"]) - set(same))
        overlap = len(same) / max(1, len(months))
        score = 0.8 * lexical + 0.15 * overlap + 0.05 * len(next_month) / max(1, len(months))
        reasons = [
            f"Name similarity {lexical:.0%}",
            f"{len(same)}/{len(months)} listed months also present in registry",
        ]
        if next_month:
            reasons.append(
                f"{len(next_month)} following-month registry occurrences (reporting lag is possible)"
            )
        if identifier in group["exact_candidates"]:
            reasons.append(
                "Existing exact normalized-name candidate; ambiguous IDs still require review"
            )
        candidates.append(
            {
                "series_id": identifier,
                "registry_names": sorted(series["names"]),
                "rank_score": round(score, 4),
                "name_similarity": round(lexical, 4),
                "same_months": same,
                "following_months": next_month,
                "missing_listed_months": sorted(months - series["months"]),
                "registry_occurrences": series["occurrences"],
                "reasons": reasons,
            }
        )
    candidates.sort(key=lambda row: (-row["rank_score"], row["series_id"]))
    selected = candidates[:5]
    selected_ids = {row["series_id"] for row in selected}
    selected.extend(
        row
        for row in candidates
        if row["series_id"] in group["exact_candidates"] and row["series_id"] not in selected_ids
    )
    return selected


def alias_proposals(conn: sqlite3.Connection, output: Path, *, now: str) -> dict[str, int]:
    """Write proposals only. Never change the database, overrides, or year acceptance."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT finding_id,subject_id,evidence_json,snapshot_id FROM findings "
        "WHERE kind='series_alias' AND owner_kind='history_year' AND closed_at IS NULL ORDER BY subject_id"
    ):
        finding = json.loads(row["evidence_json"])
        key = series_slug(finding["printed_name"])
        group = grouped.setdefault(
            key,
            {
                "group_id": key,
                "printed_names": set(),
                "months": set(),
                "exact_candidates": set(),
                "findings": [],
            },
        )
        group["printed_names"].add(finding["printed_name"])
        group["months"].add(row["subject_id"][:7])
        group["exact_candidates"].update(finding.get("candidates", []))
        group["findings"].append(
            {"finding_id": row["finding_id"], "snapshot_id": row["snapshot_id"], **finding}
        )
    registry = _registry(conn)
    evidence = _evidence(conn, set(grouped))
    for key, group in grouped.items():
        for field in ("printed_names", "months", "exact_candidates"):
            group[field] = sorted(group[field])
        group["evidence"] = evidence.get(key, [])
        group["variants"] = sorted(
            {(row["raw_row"]["name_raw"], row["source"]) for row in group["evidence"]}
        )
        group["candidates"] = _candidates(group, registry)
        group["suggested_new_id"] = "listed-" + key
    groups = sorted(grouped.values(), key=lambda row: (-len(row["findings"]), row["group_id"]))
    packet = {
        "generated_at": now,
        "notice": "Suggestions are not identity decisions. A human must review each group and add each alias. Month overlap is supporting evidence, not proof; registry reporting may lag event dates. Group keys use the runtime series normalization, including case, punctuation, edition years and ordinal labels.",
        "groups": groups,
    }
    durable_write(output / "alias-proposals.json", canonical(packet))
    template = Path(__file__).with_name("alias_review.html").read_text()
    embedded = json.dumps(packet, ensure_ascii=True).replace("<", "\\u003c")
    durable_write(output / "alias-review.html", template.replace("__PACKET__", embedded).encode())
    return {
        "findings": sum(len(row["findings"]) for row in groups),
        "groups": len(groups),
        "registry_series": len(registry),
    }
