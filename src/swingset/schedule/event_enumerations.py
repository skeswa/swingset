"""Recover durable page obligations from retained admitted parent documents.

No fetch, watch rescheduling, queue reset, or canonical matching occurs here.
Enumeration history owns obligations; inventory derives current stage evidence.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from swingset.admission.event_declarations import declarations as project_declarations
from swingset.fetch.archive import canonical, digest
from swingset.state import engine
from swingset.state.db import Database

from .event_evidence import (
    admission_reason,
    generation,
    parent_support,
    timestamp,
)
from .event_request_kind import SOURCE_EVENT_PARSERS, is_source_event_request, purpose

CURSOR = "event_enumerations/admission_decision/v1"
LEGACY_CURSOR = "event_enumerations/legacy_watch/v1"


def _json(value: Any) -> str:
    return canonical(value).decode()


def _earliest(values: list[str | None]) -> str | None:
    return min(value for value in values if value is not None) if values and all(values) else None


def _declarations(
    value: Mapping[str, Any], conn: engine.Connection
) -> dict[str, dict[str, dict[str, Any]]]:
    context = value["recipe"]["context"]
    watch = snapshot = None
    if purpose(context["kind"]) == "result":
        watch = conn.execute(
            (
                "SELECT watches.watch_id, watches.source, watches.kind, watches.method, watches.url, "
                "watches.form, watches.archive_url, watches.parser, watches.source_ref, watches.state, "
                "watches.next_check_at, watches.last_checked_at, watches.last_changed_at, "
                "watches.unchanged_streak, watches.etag, watches.last_modified, watches.body_sha256, "
                "watches.paused_until, watches.notes, watches.fingerprint, watches.extract_version, "
                "watches.priority, watches.created_by_snapshot_id, watches.parent_watch_id, watches.ever_ok, "
                "watches.current_observation_snapshot_id, watches.consecutive_404s, watches.first_404_at "
                "FROM watches WHERE watch_id = %s"
            ),
            (context["watch_id"],),
        ).fetchone()
        snapshot = conn.execute(
            "SELECT method, url, form FROM snapshots WHERE snapshot_id = %s",
            (context["snapshot_id"],),
        ).fetchone()
    return project_declarations(
        value,
        anchor_watch=dict(watch) if watch else None,
        anchor_snapshot=dict(snapshot) if snapshot else None,
    )


def _record(conn: engine.Connection, value: Mapping[str, Any], decision: int, now: str) -> int:
    reason = admission_reason(conn, value)
    if reason:
        return 0
    parent = parent_support(conn, value)
    source = value["recipe"]["context"]["source"]
    discovered = _earliest([row["discovered_at"] for row in parent["snapshots"]])
    additions = _declarations(value, conn)
    # Replacing one document may remove only that document's old claims. An
    # unrelated parent and a nonauthoritative omission retain their obligations.
    authoritative = (
        value["removal_authority"] == "watch" and value["report"].get("proposed_removal") == "watch"
    )
    if authoritative:
        for old in conn.execute(
            (
                "SELECT DISTINCT i.source_ref FROM source_event_inventory AS i JOIN "
                "source_event_enumeration_members AS m ON m.enumeration_id = i.enumeration_id CROSS JOIN "
                "jsonb_array_elements(m.support_json::jsonb) AS s WHERE i.source = %s AND JSON_EXTRACT_PATH_TEXT(CAST(s.value "
                "AS JSON), 'unit_key') = %s"
            ),
            (source, value["unit_key"]),
        ):
            additions.setdefault(old[0], {})
    changed = 0
    for ref, declarations in sorted(additions.items()):
        prior = conn.execute(
            "SELECT enumeration_id, first_known_at FROM source_event_inventory WHERE source = %s AND source_ref = %s",
            (source, ref),
        ).fetchone()
        predecessor = prior[0] if prior else None
        rows = {
            row["request_id"]: {
                "request": json.loads(row["request_json"]),
                "support": json.loads(row["support_json"]),
                "first_known_at": row["first_known_at"],
            }
            for row in conn.execute(
                (
                    "SELECT source_event_enumeration_members.enumeration_id, "
                    "source_event_enumeration_members.request_id, source_event_enumeration_members.request_json, "
                    "source_event_enumeration_members.support_json, "
                    "source_event_enumeration_members.first_known_at FROM source_event_enumeration_members WHERE "
                    "enumeration_id = %s"
                ),
                (predecessor,),
            )
        }
        before = set(rows)
        if authoritative:
            for key in list(rows):
                rows[key]["support"] = [
                    claim
                    for claim in rows[key]["support"]
                    if claim["unit_key"] != value["unit_key"]
                ]
                if not rows[key]["support"]:
                    del rows[key]
        for key, declaration in sorted(declarations.items()):
            age = conn.execute(
                (
                    "SELECT MIN(m.first_known_at) AS _value_0, COUNT(*) - COUNT(m.first_known_at) AS _value_1 FROM source_event_enumeration_members AS m JOIN source_event_enumerations AS e USING (enumeration_id) WHERE e.source = %s AND e.source_ref = %s AND m.request_id = %s"
                ),
                (source, ref, key),
            ).fetchone()
            first = None if age[1] else age[0] or discovered
            row = rows.setdefault(
                key, {"request": declaration["request"], "support": [], "first_known_at": first}
            )
            # A repeated declaration supersedes its own support for this member;
            # omitted members retain their earlier support unless removal is authorized.
            row["support"] = [
                claim for claim in row["support"] if claim["unit_key"] != value["unit_key"]
            ]
            row["support"].append(
                {**parent, "watches": declaration["watches"], "roles": declaration["roles"]}
            )
        support = {
            claim["generation_id"]: {
                k: v for k, v in claim.items() if k not in {"watches", "roles"}
            }
            for row in rows.values()
            for claim in row["support"]
        }
        support[value["generation_id"]] = parent
        members = [{"request_id": key, **row} for key, row in sorted(rows.items())]
        membership = digest(canonical(sorted(rows)))
        # The current adapter contract proves only captured_document_end. No
        # retained field currently certifies whole-event pagination completion.
        pagination = "unknown"
        reason = "event_pagination_not_proven"
        content = {
            "source": source,
            "source_ref": ref,
            "predecessor": predecessor,
            "generation_id": value["generation_id"],
            "parents": support,
            "members": members,
            "pagination": pagination,
        }
        identifier = "enumeration_" + digest(canonical(content))
        conn.execute(
            (
                "INSERT INTO source_event_enumerations (enumeration_id, source, source_ref, predecessor_id, "
                "generation_id, decision_id, parent_support_json, membership_digest, pagination, "
                "pagination_reason, added_json, removed_json, recorded_at) VALUES (%s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s)"
            ),
            (
                identifier,
                source,
                ref,
                predecessor,
                value["generation_id"],
                decision,
                _json(support),
                membership,
                pagination,
                reason,
                _json(sorted(set(rows) - before)),
                _json(sorted(before - set(rows))),
                now,
            ),
        )
        conn.executemany(
            (
                "INSERT INTO source_event_enumeration_members (enumeration_id, request_id, request_json, "
                "support_json, first_known_at) VALUES (%s, %s, %s, %s, %s)"
            ),
            [
                (
                    identifier,
                    key,
                    _json(row["request"]),
                    _json(row["support"]),
                    row["first_known_at"],
                )
                for key, row in sorted(rows.items())
            ],
        )
        associations = {
            (identifier, key, spec["watch_id"])
            for key, row in rows.items()
            for claim in row["support"]
            for spec in claim["watches"]
        }
        conn.executemany(
            "INSERT INTO source_event_member_watches (enumeration_id, request_id, watch_id) VALUES (%s, %s, %s)",
            sorted(associations),
        )
        first = _earliest([prior[1], discovered]) if prior else discovered
        conn.execute(
            (
                "INSERT INTO source_event_inventory (source, source_ref, enumeration_id, first_known_at, bootstrap_basis) VALUES (%s, %s, %s, %s, %s) ON CONFLICT(source, source_ref) DO UPDATE SET enumeration_id = excluded.enumeration_id, first_known_at = excluded.first_known_at"
            ),
            (source, ref, identifier, first, "admitted_parent"),
        )
        changed += 1
    return changed


def _cursor(conn: engine.Connection, name: str) -> int:
    row = conn.execute("SELECT value FROM cursors WHERE name = %s", (name,)).fetchone()
    return int(row[0]) if row else 0


def bootstrap(database: Database, *, now: datetime, limit: int = 100) -> dict[str, Any]:
    """Commit one bounded admission/legacy batch and its cursors atomically."""
    at = timestamp(now.isoformat())
    if at is None or not 1 <= limit <= 100:
        raise ValueError("bootstrap requires an aware time and limit 1..100")
    with database.transaction() as conn:
        decisions = conn.execute(
            (
                "SELECT decision_id, generation_id FROM admission_decisions WHERE decision_id > %s AND state "
                "= 'accepted' ORDER BY decision_id NULLS FIRST LIMIT %s"
            ),
            (_cursor(conn, CURSOR), limit),
        ).fetchall()
        changed, errors = 0, []
        for decision, identifier in decisions:
            if not conn.execute(
                "SELECT 1 AS _value_0 FROM event_enumeration_inputs WHERE generation_id = %s",
                (identifier,),
            ).fetchone():
                try:
                    with database.transaction():
                        kind = conn.execute(
                            "SELECT page_kind FROM source_generations WHERE generation_id = %s",
                            (identifier,),
                        ).fetchone()[0]
                        if kind in {
                            "wsdc_registry.dancer",
                            "wsdc_calendar.events",
                            "swingdancecouncil.events",
                            "wsdc_newsletter.events",
                            "wsdc_newsletter.index",
                        }:
                            created, outcome = 0, "ignored_non_event_parent"
                        else:
                            value = generation(conn, identifier)
                            reason = admission_reason(conn, value)
                            created = _record(conn, value, decision, at)
                            outcome = reason or (
                                "processed" if created else "ignored_non_event_parent"
                            )
                    changed += created
                except (ValueError, KeyError, TypeError) as exc:
                    outcome = "invalid_parent_evidence"
                    errors.append({"generation_id": identifier, "reason": str(exc)})
                conn.execute(
                    (
                        "INSERT INTO event_enumeration_inputs (generation_id, decision_id, outcome, recorded_at) "
                        "VALUES (%s, %s, %s, %s)"
                    ),
                    (identifier, decision, outcome, at),
                )
        if decisions:
            conn.execute(
                (
                    "INSERT INTO cursors (name, value) VALUES (%s, %s) ON CONFLICT(name) DO UPDATE SET value = excluded.value"
                ),
                (CURSOR, str(decisions[-1][0])),
            )
        legacy = conn.execute(
            (
                "SELECT rowid, watches.watch_id, watches.source, watches.kind, watches.method, watches.url, "
                "watches.form, watches.archive_url, watches.parser, watches.source_ref, watches.state, "
                "watches.next_check_at, watches.last_checked_at, watches.last_changed_at, "
                "watches.unchanged_streak, watches.etag, watches.last_modified, watches.body_sha256, "
                "watches.paused_until, watches.notes, watches.fingerprint, watches.extract_version, "
                "watches.priority, watches.created_by_snapshot_id, watches.parent_watch_id, watches.ever_ok, "
                "watches.current_observation_snapshot_id, watches.consecutive_404s, watches.first_404_at "
                "FROM watches WHERE rowid > %s AND (kind IN ('event', 'round') OR parser IN (SELECT value "
                "FROM jsonb_array_elements_text(%s::jsonb))) AND NOT source_ref IS NULL ORDER BY rowid NULLS FIRST LIMIT %s"
            ),
            (_cursor(conn, LEGACY_CURSOR), _json(sorted(SOURCE_EVENT_PARSERS)), limit),
        ).fetchall()
        for watch in legacy:
            if not is_source_event_request(
                source=watch["source"],
                parser=watch["parser"],
                watch_kind=watch["kind"],
                source_ref=watch["source_ref"],
            ):
                continue
            first = conn.execute(
                (
                    "SELECT MIN(first_seen_at) AS _value_0, COUNT(*) - COUNT(first_seen_at) AS _value_1 FROM scheduler_parent_links WHERE child_watch_id = %s"
                ),
                (watch["watch_id"],),
            ).fetchone()
            conn.execute(
                (
                    "INSERT INTO source_event_inventory (source, source_ref, enumeration_id, first_known_at, "
                    "bootstrap_basis) VALUES (%s, %s, NULL, %s, 'legacy_unassessed') ON CONFLICT DO NOTHING"
                ),
                (watch["source"], watch["source_ref"], None if first[1] else timestamp(first[0])),
            )
        if legacy:
            conn.execute(
                (
                    "INSERT INTO cursors (name, value) VALUES (%s, %s) ON CONFLICT(name) DO UPDATE SET value = excluded.value"
                ),
                (LEGACY_CURSOR, str(legacy[-1]["rowid"])),
            )
        return {
            "admissions_scanned": len(decisions),
            "legacy_watches_scanned": len(legacy),
            "enumerations_created": changed,
            "errors": errors,
            "admission_cursor": _cursor(conn, CURSOR),
            "legacy_cursor": _cursor(conn, LEGACY_CURSOR),
            "may_have_more": len(decisions) == limit or len(legacy) == limit,
        }


def memberships(
    conn: engine.Connection, watch_ids: Iterable[str]
) -> dict[str, list[dict[str, Any]]]:
    """Batch current obligations for scheduling; no artifact or eligibility checks."""
    if (
        conn.execute(
            "SELECT 1 AS _value_0 FROM pg_catalog.pg_class WHERE relname = 'source_event_member_watches' AND (PG_TABLE_IS_VISIBLE(oid) AND NOT relnamespace IN (CAST('pg_catalog' AS REGNAMESPACE), CAST('information_schema' AS REGNAMESPACE)))"
        ).fetchone()
        is None
    ):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    rows = conn.execute(
        (
            "SELECT w.watch_id, i.source, i.source_ref, i.enumeration_id, w.request_id, m.first_known_at "
            "FROM source_event_member_watches AS w JOIN source_event_inventory AS i USING "
            "(enumeration_id) JOIN source_event_enumeration_members AS m USING (enumeration_id, "
            "request_id) WHERE w.watch_id IN (SELECT value FROM jsonb_array_elements_text(%s::jsonb)) ORDER BY w.watch_id NULLS "
            "FIRST, i.source NULLS FIRST, i.source_ref NULLS FIRST, w.request_id NULLS FIRST"
        ),
        (_json(sorted(set(watch_ids))),),
    )
    for row in rows:
        result.setdefault(row["watch_id"], []).append(dict(row))
    return result
