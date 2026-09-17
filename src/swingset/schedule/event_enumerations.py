"""Recover durable page obligations from retained admitted parent documents.

No fetch, watch rescheduling, queue reset, or canonical matching occurs here.
Enumeration history owns obligations; inventory derives current stage evidence.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from swingset.admission.event_declarations import declarations as project_declarations
from swingset.fetch.archive import canonical, digest
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
    value: Mapping[str, Any], conn: sqlite3.Connection
) -> dict[str, dict[str, dict[str, Any]]]:
    context = value["recipe"]["context"]
    watch = snapshot = None
    if purpose(context["kind"]) == "result":
        watch = conn.execute(
            "SELECT * FROM watches WHERE watch_id=?", (context["watch_id"],)
        ).fetchone()
        snapshot = conn.execute(
            "SELECT method,url,form FROM snapshots WHERE snapshot_id=?", (context["snapshot_id"],)
        ).fetchone()
    return project_declarations(
        value,
        anchor_watch=dict(watch) if watch else None,
        anchor_snapshot=dict(snapshot) if snapshot else None,
    )


def _record(conn: sqlite3.Connection, value: Mapping[str, Any], decision: int, now: str) -> int:
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
            "SELECT DISTINCT i.source_ref FROM source_event_inventory i JOIN source_event_enumeration_members m ON m.enumeration_id=i.enumeration_id,json_each(m.support_json) s WHERE i.source=? AND json_extract(s.value,'$.unit_key')=?",
            (source, value["unit_key"]),
        ):
            additions.setdefault(old[0], {})
    changed = 0
    for ref, declarations in sorted(additions.items()):
        prior = conn.execute(
            "SELECT enumeration_id,first_known_at FROM source_event_inventory WHERE source=? AND source_ref=?",
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
                "SELECT * FROM source_event_enumeration_members WHERE enumeration_id=?",
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
                "SELECT min(m.first_known_at),count(*)-count(m.first_known_at) FROM source_event_enumeration_members m JOIN source_event_enumerations e USING(enumeration_id) WHERE e.source=? AND e.source_ref=? AND m.request_id=?",
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
            "INSERT INTO source_event_enumerations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
            "INSERT INTO source_event_enumeration_members VALUES (?,?,?,?,?)",
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
            "INSERT INTO source_event_member_watches VALUES (?,?,?)", sorted(associations)
        )
        first = _earliest([prior[1], discovered]) if prior else discovered
        conn.execute(
            "INSERT INTO source_event_inventory VALUES (?,?,?,?,?) ON CONFLICT(source,source_ref) DO UPDATE SET enumeration_id=excluded.enumeration_id,first_known_at=excluded.first_known_at",
            (source, ref, identifier, first, "admitted_parent"),
        )
        changed += 1
    return changed


def _cursor(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT value FROM cursors WHERE name=?", (name,)).fetchone()
    return int(row[0]) if row else 0


def bootstrap(database: Database, *, now: datetime, limit: int = 100) -> dict[str, Any]:
    """Commit one bounded admission/legacy batch and its cursors atomically."""
    at = timestamp(now.isoformat())
    if at is None or not 1 <= limit <= 100:
        raise ValueError("bootstrap requires an aware time and limit 1..100")
    with database.transaction() as conn:
        decisions = conn.execute(
            "SELECT decision_id,generation_id FROM admission_decisions WHERE decision_id>? AND state='accepted' ORDER BY decision_id LIMIT ?",
            (_cursor(conn, CURSOR), limit),
        ).fetchall()
        changed, errors = 0, []
        for decision, identifier in decisions:
            if not conn.execute(
                "SELECT 1 FROM event_enumeration_inputs WHERE generation_id=?", (identifier,)
            ).fetchone():
                try:
                    with database.transaction():
                        kind = conn.execute(
                            "SELECT page_kind FROM source_generations WHERE generation_id=?",
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
                    "INSERT INTO event_enumeration_inputs VALUES (?,?,?,?)",
                    (identifier, decision, outcome, at),
                )
        if decisions:
            conn.execute(
                "INSERT INTO cursors VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (CURSOR, str(decisions[-1][0])),
            )
        legacy = conn.execute(
            "SELECT rowid,* FROM watches WHERE rowid>? AND (kind IN ('event','round') OR parser IN (SELECT value FROM json_each(?))) AND source_ref IS NOT NULL ORDER BY rowid LIMIT ?",
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
                "SELECT min(first_seen_at),count(*)-count(first_seen_at) FROM scheduler_parent_links WHERE child_watch_id=?",
                (watch["watch_id"],),
            ).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO source_event_inventory VALUES (?,?,NULL,?, 'legacy_unassessed')",
                (watch["source"], watch["source_ref"], None if first[1] else timestamp(first[0])),
            )
        if legacy:
            conn.execute(
                "INSERT INTO cursors VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
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
    conn: sqlite3.Connection, watch_ids: Iterable[str]
) -> dict[str, list[dict[str, Any]]]:
    """Batch current obligations for scheduling; no artifact or eligibility checks."""
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='source_event_member_watches'"
        ).fetchone()
        is None
    ):
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    rows = conn.execute(
        "SELECT w.watch_id,i.source,i.source_ref,i.enumeration_id,w.request_id,m.first_known_at "
        "FROM source_event_member_watches w JOIN source_event_inventory i USING(enumeration_id) "
        "JOIN source_event_enumeration_members m USING(enumeration_id,request_id) "
        "WHERE w.watch_id IN (SELECT value FROM json_each(?)) ORDER BY w.watch_id,i.source,i.source_ref,w.request_id",
        (_json(sorted(set(watch_ids))),),
    )
    for row in rows:
        result.setdefault(row["watch_id"], []).append(dict(row))
    return result
