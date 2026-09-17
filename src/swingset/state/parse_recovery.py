"""Rebuild lost exact-snapshot parse hints from retained bookkeeping.

Only current declared member watches participate. This does not establish
event-stage absence: another unit's aggregate interpretation may already use
the snapshot without completing this snapshot's own parse task. No artifact,
network, watch-state, retry-clock, or success-history mutation occurs here.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any

from swingset.admission.generations import begin_attempt, selected_snapshot
from swingset.sources import get_page_kind
from swingset.sources.base import ParseContext

from .db import Database
from .work import WorkUnit
from .work_fingerprints import input_fingerprint

CURSOR = "event_parse_recovery_cursor"
METADATA_BYTES = 65536
GENERATION_LIMIT = 8
TOTAL_METADATA_BYTES = 2 * 1024 * 1024


class Unassessed(ValueError):
    pass


class BudgetEnded(Unassessed):
    pass


class MetadataBudget:
    def __init__(self, wall_seconds: float):
        self.remaining = TOTAL_METADATA_BYTES
        self.deadline = time.monotonic() + wall_seconds

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise BudgetEnded("metadata_time_budget")
        if self.remaining <= 0:
            raise BudgetEnded("metadata_byte_budget")

    def charge(self, size: int) -> None:
        self.check()
        if size > self.remaining:
            raise BudgetEnded("metadata_byte_budget")
        self.remaining -= size


def _rows(
    conn: sqlite3.Connection,
    budget: MetadataBudget,
    sql: str,
    parameters: tuple[Any, ...],
    columns: tuple[str, ...],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Bound returned metadata, not SQLite's internal indexed query duration."""
    budget.check()
    size = "+".join(f"coalesce(length(CAST({name} AS BLOB)),0)" for name in columns)
    bound = min(METADATA_BYTES, budget.remaining)
    values = ",".join(f"CASE WHEN ({size})<={bound} THEN {name} END AS {name}" for name in columns)
    result = []
    cursor = conn.execute(
        f"SELECT {values},({size}) AS metadata_bytes FROM ({sql}) LIMIT ?", (*parameters, limit)
    )
    for row in cursor:
        if row["metadata_bytes"] > METADATA_BYTES:
            raise Unassessed("retained_metadata_oversized")
        # Reserve copies subsequently read by the existing shared helpers too.
        budget.charge(row["metadata_bytes"] * 3)
        result.append(dict(row))
    return result


def _attempt(
    conn: sqlite3.Connection,
    budget: MetadataBudget,
    unit: WorkUnit,
) -> tuple[str | None, str | None]:
    rows = _rows(
        conn,
        budget,
        "SELECT outcome,work_token,input_fingerprint,retry_generation FROM work_attempts "
        "WHERE stage=? AND unit_kind=? AND unit_id=? ORDER BY attempt_id DESC",
        (unit.stage, unit.unit_kind, unit.unit_id),
        ("outcome", "work_token", "input_fingerprint", "retry_generation"),
        limit=1,
    )
    if not rows:
        return None, None
    row = rows[0]
    if row["outcome"] == "running":
        raise Unassessed("active_attempt_requires_normal_interruption_recovery")
    generations = _rows(
        conn,
        budget,
        "SELECT retry_generation FROM work_generations WHERE stage=? AND unit_kind=? AND unit_id=?",
        (unit.stage, unit.unit_kind, unit.unit_id),
        ("retry_generation",),
        limit=1,
    )
    retried = bool(generations) and generations[0]["retry_generation"] > row["retry_generation"]
    unchanged = row["input_fingerprint"] == input_fingerprint(conn, unit)
    if (
        unchanged
        and not retried
        and row["outcome"] in {"succeeded", "blocked", "unavailable", "superseded"}
    ):
        return "terminal_attempt", row["work_token"]
    return "recover", row["work_token"]


def _admission(
    conn: sqlite3.Connection, budget: MetadataBudget, snapshot: dict[str, Any]
) -> tuple[str, str | None]:
    page = get_page_kind(snapshot["parser"])
    context = ParseContext(
        snapshot["snapshot_id"],
        snapshot["watch_id"],
        snapshot["url"],
        snapshot["source"],
        snapshot["parser"],
        snapshot["source_ref"],
        snapshot["observed_at"] or snapshot["fetched_at"],
    )
    _rows(
        conn,
        budget,
        "SELECT accepted_generation_id FROM source_units WHERE unit_key IN (?,?)",
        (context.watch_id, f"{context.watch_id}/{context.snapshot_id}"),
        ("accepted_generation_id",),
        limit=2,
    )
    inputs = begin_attempt(conn, context, page.EXTRACT_VERSION, page.PARSER_VERSION)
    # Restrict the indexed unit history before inspecting small recipe metadata.
    # Oversized/malformed recipes stay candidates and therefore unassessed.
    rows = _rows(
        conn,
        budget,
        "SELECT g.generation_id,g.state,g.recipe_json,g.manifest_json,g.work_token,g.previous_generation_id,"
        "(SELECT d.state FROM admission_decisions d WHERE d.generation_id=g.generation_id ORDER BY d.decision_id DESC LIMIT 1) AS decision_state,"
        "(SELECT d.policy_revision FROM admission_decisions d WHERE d.generation_id=g.generation_id ORDER BY d.decision_id DESC LIMIT 1) AS policy_revision "
        "FROM source_generations g WHERE g.unit_key=? AND "
        "CASE WHEN length(CAST(g.recipe_json AS BLOB))<=? AND json_valid(g.recipe_json) "
        "THEN json_extract(g.recipe_json,'$.context.snapshot_id')=? ELSE 1 END "
        "ORDER BY g.created_at DESC,g.generation_id DESC",
        (inputs.unit_key, METADATA_BYTES, context.snapshot_id),
        (
            "generation_id",
            "state",
            "recipe_json",
            "manifest_json",
            "work_token",
            "previous_generation_id",
            "decision_state",
            "policy_revision",
        ),
        limit=GENERATION_LIMIT + 1,
    )
    policy = conn.execute(
        "SELECT policy_revision FROM admission_policies WHERE page_kind=?", (context.kind,)
    ).fetchone()
    original_token = None
    for row in rows[:GENERATION_LIMIT]:
        recipe, manifest = json.loads(row["recipe_json"]), json.loads(row["manifest_json"])
        if not isinstance(recipe, dict) or not isinstance(manifest, list):
            raise Unassessed("own_unit_receipt_invalid")
        if recipe["context"] != asdict(context):
            continue
        if (
            str(recipe["extract_version"]) != str(page.EXTRACT_VERSION)
            or str(recipe["parser_version"]) != str(page.PARSER_VERSION)
            or tuple(tuple(item) for item in recipe["accepted_inputs"]) != inputs.accepted_inputs
            or recipe.get("archive_url") != inputs.archive_url
        ):
            continue
        own = [member for member in manifest if member["snapshot_id"] == context.snapshot_id]
        if len(own) != 1 or any(
            own[0][key] != snapshot[key] for key in ("watch_id", "url", "body_sha256")
        ):
            raise Unassessed("own_unit_snapshot_evidence_changed")
        original_token = original_token or row["work_token"]
        if row["state"] != "staged" and row["decision_state"]:
            if policy is None or row["policy_revision"] != policy[0]:
                continue
            if (
                row["state"] == "accepted"
                or row["previous_generation_id"] == inputs.previous_generation_id
            ):
                return "terminal_admission", original_token
            raise Unassessed("own_unit_admission_inputs_ambiguous")
    if len(rows) > GENERATION_LIMIT:
        raise Unassessed("own_unit_history_budget")
    return "recover", original_token


def _recover(conn: sqlite3.Connection, budget: MetadataBudget, rowid: int) -> str:
    columns = (
        "snapshot_id",
        "watch_id",
        "method",
        "url",
        "form",
        "fetched_at",
        "observed_at",
        "body_sha256",
        "source",
        "parser",
        "source_ref",
        "via",
        "archive_url",
        "captured_at",
        "body_bytes",
        "watch_kind",
        "watch_url",
        "watch_archive_url",
    )
    rows = _rows(
        conn,
        budget,
        "SELECT s.*,w.source,w.parser,w.source_ref,w.kind AS watch_kind,w.url AS watch_url,w.archive_url AS watch_archive_url FROM snapshots s JOIN watches w USING(watch_id) WHERE s.rowid=?",
        (rowid,),
        columns,
        limit=1,
    )
    if not rows:
        return "snapshot_missing"
    snapshot = rows[0]
    from swingset.schedule.event_evidence import request, request_id

    identity = request_id(
        request(snapshot["source"], snapshot["method"], snapshot["url"], snapshot["form"])
    )
    if not conn.execute(
        "SELECT 1 FROM source_event_member_watches m JOIN source_event_inventory i USING(enumeration_id) "
        "WHERE m.watch_id=? AND m.request_id=? AND i.source=? LIMIT 1",
        (snapshot["watch_id"], identity, snapshot["source"]),
    ).fetchone():
        return "request_not_currently_declared"
    # Guard the only additional snapshot scalar returned by selected_snapshot.
    selection_parameters: tuple[Any, ...]
    if snapshot["watch_archive_url"]:
        selection_sql = (
            "SELECT snapshot_id FROM snapshots WHERE watch_id=? AND via='wayback' "
            "AND body_sha256 IS NOT NULL AND (requested_archive_url=? OR archive_url=?) "
            "ORDER BY fetched_at DESC,snapshot_id DESC"
        )
        selection_parameters = (
            snapshot["watch_id"],
            snapshot["watch_archive_url"],
            snapshot["watch_archive_url"],
        )
    else:
        selection_sql = (
            "SELECT snapshot_id FROM snapshots WHERE watch_id=? AND body_sha256 IS NOT NULL "
            "ORDER BY COALESCE(observed_at,fetched_at) DESC,snapshot_id DESC"
        )
        selection_parameters = (snapshot["watch_id"],)
    _rows(conn, budget, selection_sql, selection_parameters, ("snapshot_id",), limit=1)
    if not selected_snapshot(conn, snapshot["watch_id"], snapshot["snapshot_id"]):
        return "superseded_snapshot"
    # Both existing shared helpers read this inventory. Bound the complete set
    # before allowing either helper to load it, including its repeated copies.
    accepted = _rows(
        conn,
        budget,
        "SELECT consumer,input_name,digest FROM accepted_inputs",
        (),
        ("consumer", "input_name", "digest"),
        limit=257,
    )
    if len(accepted) > 256 or sum(row["metadata_bytes"] for row in accepted) > METADATA_BYTES:
        raise Unassessed("accepted_input_metadata_budget")
    _rows(
        conn,
        budget,
        "SELECT * FROM admission_policies WHERE page_kind=?",
        (snapshot["parser"],),
        (
            "page_kind",
            "contract_version",
            "mode",
            "policy_revision",
            "reviewed_report_digest",
            "reviewed_by",
            "reviewed_at",
        ),
        limit=1,
    )
    unit = WorkUnit("parse", "snapshot", snapshot["snapshot_id"])
    outcome, original_token = _attempt(conn, budget, unit)
    if outcome is None:
        outcome, original_token = _admission(conn, budget, snapshot)
    if outcome != "recover":
        return str(outcome)
    enqueued_at = original_token or snapshot["fetched_at"]
    if not isinstance(enqueued_at, str) or datetime.fromisoformat(enqueued_at).tzinfo is None:
        raise Unassessed("queue_token_time_unknown")
    budget.check()
    inserted = conn.execute(
        "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) VALUES('parse','snapshot',?,?) "
        "ON CONFLICT(stage,unit_kind,unit_id) DO NOTHING",
        (snapshot["snapshot_id"], enqueued_at),
    ).rowcount
    return "enqueued" if inserted else "already_pending"


def reconcile(
    database: Database, *, now: datetime, limit: int = 100, wall_seconds: float = 2.0
) -> dict[str, Any]:
    """Rebuild at most one metadata page; execution still uses ordinary work gates.

    A frozen snapshot-row high-water prevents later arrivals from delaying an
    existing pass. The disposable cursor may restart without changing receipts.
    Declared watch identity is required; undeclared normalized aliases are not
    searched. Completed snapshots and same-input terminal receipts are not
    reopened. A failed snapshot requires an explicit retry newer than its
    latest own attempt; an ordinary failed snapshot remains excluded. Returned metadata shares a
    2 MiB budget (including reserved helper copies) and a cooperative deadline;
    SQLite query execution itself is not preempted. Individual rows are limited
    to 64 KiB, and accepted input inventory is limited to 256 rows / 64 KiB.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("parse recovery limit must be between 1 and 100")
    if not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError("parse recovery wall_seconds must be finite and positive")
    budget = MetadataBudget(wall_seconds)
    if now.tzinfo is None:
        raise ValueError("parse recovery time requires timezone")
    summary: dict[str, Any] = {
        "supported": False,
        "scanned": 0,
        "enqueued": 0,
        "unassessed": 0,
        "reasons": {},
        "pass_complete": False,
    }
    if not database.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='source_event_member_watches'"
    ).fetchone():
        return summary
    summary["supported"] = True
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT CASE WHEN length(CAST(value AS BLOB))<=1024 THEN value END FROM meta WHERE key=?",
            (CURSOR,),
        ).fetchone()
        try:
            cursor = json.loads(row[0]) if row and row[0] else {"last": 0, "high": 0}
            if (
                not isinstance(cursor, dict)
                or set(cursor) != {"last", "high"}
                or any(
                    type(value) is not int or not 0 <= value <= 2**63 - 1
                    for value in cursor.values()
                )
                or cursor["last"] > cursor["high"]
            ):
                raise ValueError("invalid cursor")
        except (ValueError, TypeError):
            cursor = {"last": 0, "high": 0}
        if cursor["last"] >= cursor["high"]:
            cursor = {
                "last": 0,
                "high": conn.execute("SELECT coalesce(max(rowid),0) FROM snapshots").fetchone()[0],
            }
        rows = conn.execute(
            "SELECT s.rowid FROM snapshots s WHERE s.rowid>? AND s.rowid<=? "
            "AND (s.parse_status IS NULL OR s.parse_status='pending' OR (s.parse_status='failed' "
            "AND EXISTS(SELECT 1 FROM work_generations g WHERE g.stage='parse' "
            "AND g.unit_kind='snapshot' AND g.unit_id=s.snapshot_id AND g.retry_generation>"
            "(SELECT a.retry_generation FROM work_attempts a WHERE a.stage='parse' "
            "AND a.unit_kind='snapshot' AND a.unit_id=s.snapshot_id ORDER BY a.attempt_id DESC LIMIT 1)))) "
            "AND s.classification IN ('Ok','NotModified') AND s.body_sha256 IS NOT NULL "
            "AND EXISTS(SELECT 1 FROM source_event_member_watches m JOIN source_event_inventory i USING(enumeration_id) WHERE m.watch_id=s.watch_id) "
            "AND NOT EXISTS(SELECT 1 FROM pending_work p WHERE p.stage='parse' AND p.unit_kind='snapshot' AND p.unit_id=s.snapshot_id) "
            "ORDER BY s.rowid LIMIT ?",
            (cursor["last"], cursor["high"], limit + 1),
        ).fetchall()
        exhausted = False
        for candidate in rows[:limit]:
            try:
                budget.check()
            except BudgetEnded as exc:
                summary["reasons"][str(exc)] = summary["reasons"].get(str(exc), 0) + 1
                exhausted = True
                break
            earlier = summary["scanned"] > 0
            summary["scanned"] += 1
            try:
                reason = _recover(conn, budget, candidate[0])
            except BudgetEnded as exc:
                reason = str(exc)
                summary["unassessed"] += 1
                exhausted = True
            except Unassessed as exc:
                reason = str(exc)
                summary["unassessed"] += 1
            except (ValueError, KeyError, TypeError, RecursionError):
                reason = "retained_work_invalid"
                summary["unassessed"] += 1
            summary["enqueued"] += reason == "enqueued"
            summary["reasons"][reason] = summary["reasons"].get(reason, 0) + 1
            # A later interrupted candidate deserves a fresh budget next time.
            # A candidate exhausting its own fresh budget must not trap the pass.
            if not exhausted or not earlier:
                cursor["last"] = candidate[0]
            if exhausted:
                break
        summary["pass_complete"] = not exhausted and len(rows) <= limit
        if summary["pass_complete"]:
            cursor["last"] = cursor["high"]
        conn.execute(
            "INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (CURSOR, json.dumps(cursor, sort_keys=True)),
        )
    return summary
