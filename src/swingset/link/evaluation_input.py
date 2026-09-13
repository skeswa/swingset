"""Freeze the local identity population and source evidence for offline review."""

from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Any

from swingset.normalize.names import normalize_name, paired_names
from swingset.state.work import unfinished_units


def read_population(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Caller holds a read transaction; sampling never changes pipeline state."""
    if next(iter(unfinished_units(conn)), None) is not None:
        raise ValueError("identity sampling requires settled parse, project, and link work")
    dancers = {int(r["wsdc_id"]): dict(r) for r in conn.execute("SELECT * FROM dancers")}
    name_counts = Counter(str(row["name_norm"]).casefold() for row in dancers.values())
    links = {
        (str(r["subject_kind"]), str(r["subject_id"])): dict(r)
        for r in conn.execute("SELECT * FROM identity_links")
    }
    candidates: dict[tuple[str, str], list[int]] = {}
    for row in conn.execute(
        "SELECT subject_kind,subject_id,wsdc_id FROM link_candidates ORDER BY rank,wsdc_id"
    ):
        candidates.setdefault((str(row[0]), str(row[1])), []).append(int(row[2]))
    snapshots = {
        str(r["snapshot_id"]): dict(r)
        for r in conn.execute(
            "SELECT s.snapshot_id,s.body_sha256,s.extract_sha256,s.extract_version,s.parser_version,s.url,s.fetched_at,s.observed_at,s.via,w.source_ref,w.parser "
            "FROM snapshots s JOIN watches w USING(watch_id)"
        )
    }
    finalist_ids = {
        identifier
        for row in conn.execute("SELECT leader_entry_id,follower_entry_id FROM placements")
        for identifier in row
        if identifier is not None
    }
    rows: list[dict[str, Any]] = []
    for kind, query in (
        (
            "entry",
            "SELECT e.*,v.year,c.division,c.wsdc_points_eligible,c.source_contest_ref "
            "FROM entries e JOIN events v USING(event_id) JOIN contests c USING(contest_id)",
        ),
        ("judge", "SELECT j.*,v.year FROM judges j JOIN events v USING(event_id)"),
    ):
        for record in conn.execute(query):
            row = dict(record)
            subject_id = str(row[f"{kind}_id"])
            name = str(row["name_raw"] or "")
            # The unresolved review stream is entries, including zero candidates.
            link = links.get((kind, subject_id), {})
            accepted = (
                row["wsdc_id"] is not None
                and link.get("status") == "confirmed"
                and link.get("wsdc_id") == row["wsdc_id"]
            )
            if not accepted and (kind == "judge" or row.get("link_status") == "suppressed"):
                continue
            flags = []
            if name_counts[normalize_name(name).value] > 1:
                flags.append("common_name")
            if row.get("division") in {"none", "open"}:
                flags.append("unrestricted")
            if paired_names(name) or row.get("role") == "couple":
                flags.append("paired_name")
            if (
                subject_id in finalist_ids
                and row.get("wsdc_points_eligible")
                and row.get("division") in {"newcomer", "novice"}
            ):
                flags.append("first_point_eligible")
            dancer = dancers.get(int(row["wsdc_id"] or 0))
            if (
                dancer
                and row.get("role") in {"leader", "follower"}
                and row["role"] != dancer["primary_role"]
            ):
                flags.append("role_switching")
            snapshot = snapshots.get(str(row["snapshot_id"]), {})
            candidate_ids = candidates.get((kind, subject_id), [])
            evidence_ids = set(candidate_ids)
            if accepted:
                evidence_ids.add(int(row["wsdc_id"]))
            registry_evidence = []
            for number in sorted(evidence_ids):
                registry = dancers.get(number)
                registry_evidence.append(
                    {
                        "wsdc_id": number,
                        "dancer": registry,
                        "snapshot": snapshots.get(str(registry["snapshot_id"]))
                        if registry
                        else None,
                    }
                )
            rows.append(
                {
                    "subject_kind": kind,
                    "subject_id": subject_id,
                    "event_id": str(row["event_id"]),
                    "source": str(row["source"]),
                    "year": int(row["year"]),
                    "name_raw": name,
                    "role": row.get("role", "unknown"),
                    "flags": sorted(flags),
                    "stream": "accepted" if accepted else "unresolved",
                    "accepted_wsdc_id": row["wsdc_id"] if accepted else None,
                    "candidate_ids": candidate_ids,
                    "registry_evidence": registry_evidence,
                    "link_evidence": link,
                    "link_method": link.get("method"),
                    "link_status": link.get("status", "unmatched"),
                    "source_reference": {
                        "source_event": snapshot.get("source_ref"),
                        "contest": row.get("source_contest_ref"),
                        "bib": row.get("bib"),
                        "legacy_subject_id": subject_id,
                        "binding": "snapshot_and_legacy_locator; durable H8 reference unavailable",
                    },
                    "evidence": {"snapshot_id": row["snapshot_id"], **snapshot},
                }
            )
    # Future calendar listings without subjects do not define the review era.
    latest_year = max((row["year"] for row in rows), default=0)
    for row in rows:
        row["era"] = "historical" if row["year"] < latest_year else "latest_year"
    context = {
        "population": "local settled confirmed default joins and unresolved entries; not a published-release precision claim",
        "latest_event_year": latest_year,
        "schema_version": conn.execute("PRAGMA user_version").fetchone()[0],
        "revisions": dict(conn.execute("SELECT name,value FROM revisions")),
        "accepted_inputs": [
            dict(row)
            for row in conn.execute("SELECT * FROM accepted_inputs ORDER BY consumer,input_name")
        ],
    }
    return rows, context
