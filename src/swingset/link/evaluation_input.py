"""Freeze the local identity population and source evidence for offline review."""

from __future__ import annotations

from collections import Counter
from typing import Any

from swingset.normalize.names import normalize_name, paired_names
from swingset.state import engine
from swingset.state.work import unfinished_units


def read_population(conn: engine.Connection) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Caller holds a read transaction; sampling never changes pipeline state."""
    if next(iter(unfinished_units(conn)), None) is not None:
        raise ValueError("identity sampling requires settled parse, project, and link work")
    dancers = {
        int(r["wsdc_id"]): dict(r)
        for r in conn.execute(
            "SELECT dancers.wsdc_id, dancers.first_name, dancers.last_name, dancers.name_norm, "
            "dancers.is_pro, dancers.primary_role, dancers.leader_required_level, "
            "dancers.leader_allowed_level, dancers.follower_required_level, "
            "dancers.follower_allowed_level, dancers.leader_highest_level, "
            "dancers.leader_highest_points, dancers.follower_highest_level, "
            "dancers.follower_highest_points, dancers.recent_year, dancers.registry_internal_id, "
            "dancers.registry_fetched_at, dancers.merged_into_wsdc_id, dancers.source, "
            "dancers.snapshot_id, dancers.parser_version, dancers.first_seen_at, dancers.last_seen_at, "
            "dancers.run_id FROM dancers"
        )
    }
    name_counts = Counter(str(row["name_norm"]).casefold() for row in dancers.values())
    links = {
        (str(r["subject_kind"]), str(r["subject_id"])): dict(r)
        for r in conn.execute(
            "SELECT identity_links.link_id, identity_links.subject_kind, identity_links.subject_id, "
            "identity_links.wsdc_id, identity_links.method, identity_links.status, "
            "identity_links.confidence, identity_links.constraints_applied, identity_links.asserted_at, "
            "identity_links.run_id, identity_links.linker_version FROM identity_links"
        )
    }
    candidates: dict[tuple[str, str], list[int]] = {}
    for row in conn.execute(
        "SELECT subject_kind, subject_id, wsdc_id FROM link_candidates ORDER BY rank NULLS FIRST, "
        "wsdc_id NULLS FIRST"
    ):
        candidates.setdefault((str(row[0]), str(row[1])), []).append(int(row[2]))
    snapshots = {
        str(r["snapshot_id"]): dict(r)
        for r in conn.execute(
            "SELECT s.snapshot_id, s.body_sha256, s.extract_sha256, s.extract_version, s.parser_version, "
            "s.url, s.fetched_at, s.observed_at, s.via, w.source_ref, w.parser FROM snapshots AS s JOIN "
            "watches AS w USING (watch_id)"
        )
    }
    finalist_ids = {
        identifier
        for row in conn.execute("SELECT leader_entry_id, follower_entry_id FROM placements")
        for identifier in row
        if identifier is not None
    }
    rows: list[dict[str, Any]] = []
    for kind, query in (
        (
            "entry",
            (
                "SELECT e.entry_id, e.contest_id, e.event_id, e.role, e.bib, e.name_raw, e.name_norm, "
                "e.partner_name_raw, e.city_raw, e.country_raw, e.wsdc_id, e.link_status, e.link_confidence, "
                "e.partner_entry_id, e.rounds_danced, e.best_round, e.source, e.snapshot_id, "
                "e.parser_version, e.first_seen_at, e.last_seen_at, e.run_id, v.year, c.division, "
                "c.wsdc_points_eligible, c.source_contest_ref FROM entries AS e JOIN events AS v USING "
                "(event_id) JOIN contests AS c USING (contest_id)"
            ),
        ),
        (
            "judge",
            (
                "SELECT j.judge_id, j.event_id, j.name_raw, j.initials, j.anonymous, j.wsdc_id, j.source, "
                "j.snapshot_id, j.parser_version, j.first_seen_at, j.last_seen_at, j.run_id, v.year FROM "
                "judges AS j JOIN events AS v USING (event_id)"
            ),
        ),
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
        "schema_version": int(
            conn.execute("SELECT value FROM ops.meta WHERE key='schema_version'").fetchone()[0]
        ),
        "revisions": dict(conn.execute("SELECT name, value FROM revisions")),
        "accepted_inputs": [
            dict(row)
            for row in conn.execute(
                "SELECT accepted_inputs.consumer, accepted_inputs.input_name, accepted_inputs.digest FROM "
                "accepted_inputs ORDER BY consumer NULLS FIRST, input_name NULLS FIRST"
            )
        ],
    }
    return rows, context
