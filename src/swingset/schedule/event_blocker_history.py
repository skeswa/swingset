"""Change-only samples of recorded blockers; unobserved intervals stay unknown."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from swingset.config import Config
from swingset.state.db import Database

from .event_pressure import request_denial as expansion_denial
from .event_readiness import report as readiness

MEMBER_LIMIT = 64
WATCH_LIMIT = 64
FORMAT = "event-blocker-observation-v1"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='event_blocker_cursor'").fetchone()
        is not None
    )


def _policy(config: Config) -> tuple[str, str]:
    raw = _json(
        {
            "format": FORMAT,
            "member_limit": MEMBER_LIMIT,
            "watch_limit": WATCH_LIMIT,
            "hosts": {name: asdict(value) for name, value in sorted(config.hosts.items())},
            "sources": {name: asdict(value) for name, value in sorted(config.sources.items())},
            "history_start": config.history_start.isoformat(),
            "scheduler": asdict(config.scheduler),
        }
    )
    return sha256(raw.encode()).hexdigest(), raw


def _watches(conn: sqlite3.Connection, event: sqlite3.Row) -> tuple[list[str], list[str]]:
    enumeration = event["enumeration_id"]
    if enumeration:
        members = list(
            conn.execute(
                "SELECT request_id FROM source_event_enumeration_members WHERE enumeration_id=? LIMIT ?",
                (enumeration, MEMBER_LIMIT + 1),
            )
        )
        if len(members) > MEMBER_LIMIT:
            return [], ["member_limit"]
        associated = list(
            conn.execute(
                "SELECT watch_id FROM source_event_member_watches WHERE enumeration_id=? LIMIT ?",
                (enumeration, WATCH_LIMIT + 1),
            )
        )
        if len(associated) > WATCH_LIMIT:
            return [], ["watch_association_limit"]
    else:
        associated = []
    own = list(
        conn.execute(
            "SELECT watch_id FROM watches WHERE source=? AND source_ref=? ORDER BY watch_id LIMIT ?",
            (event["source"], event["source_ref"], WATCH_LIMIT + 1),
        )
    )
    identifiers = sorted({row[0] for row in associated + own})
    return ([], ["watch_limit"]) if len(identifiers) > WATCH_LIMIT else (identifiers, [])


def _normalize(facts: dict[str, Any]) -> dict[str, Any]:
    """Exclude advancing counters and clocks while keeping blocker boundaries."""
    reasons = set()
    if facts["operator_hold"]:
        reasons.add("operator_hold")
    if not facts["source_enabled"]:
        reasons.add("source_disabled")
    pauses = [
        {
            key: pause.get(key)
            for key in (
                "pause_id",
                "scope_kind",
                "scope_id",
                "until_at",
                "actor",
                "reason",
                "control_revision",
            )
        }
        for pause in facts["operator_pauses"]
    ]
    if pauses:
        reasons.add("operator_pause")
    watches = []
    for watch in facts["watches"]:
        schedule = "unscheduled" if watch["due"] is None else "due" if watch["due"] else "waiting"
        if schedule != "due":
            reasons.add("watch_" + schedule)
        if watch["pause_active"]:
            reasons.add("watch_pause")
        if watch["state_excludes_ordinary_selection"]:
            reasons.add("watch_state_excluded")
        if not watch["source_enabled"]:
            reasons.add("source_disabled")
        response = watch["latest_response"]
        watches.append(
            {
                "watch_id": watch["watch_id"],
                "source": watch["source"],
                "source_enabled": watch["source_enabled"],
                "state": watch["state"],
                "request_host": watch["request_host"],
                "request_host_basis": watch["request_host_basis"],
                "schedule": schedule,
                "next_check_at": watch["next_check_at"] if schedule == "waiting" else None,
                "paused_until": watch["paused_until"] if watch["pause_active"] else None,
                "operator_pause_ids": watch["operator_pause_ids"],
                "last_response_classification": response["classification"] if response else None,
                "last_response_status": response["http_status"] if response else None,
            }
        )
    hosts = []
    for host in facts["hosts"]:
        allowance = host["daily_allowance"]
        active = {
            "host_cooldown": host["cooldown_active"],
            "host_pause": host["pause_active"],
            "request_budget": allowance["requests_exhausted"],
            "byte_budget": allowance["bytes_exhausted"],
        }
        reasons.update(key for key, value in active.items() if value)
        hosts.append(
            {
                "host": host["host"],
                **active,
                "next_allowed_at": host["next_allowed_at"] if host["cooldown_active"] else None,
                "paused_until": host["paused_until"] if host["pause_active"] else None,
                "pause_reason": host["pause_reason"] if host["pause_active"] else None,
                "next_reset_at": allowance["next_reset_at"]
                if allowance["requests_exhausted"] or allowance["bytes_exhausted"]
                else None,
            }
        )
    pressure_reasons = sorted((facts["backpressure"] or {}).get("reasons", []))
    reasons.update(pressure_reasons)
    return {
        "operator_hold": facts["operator_hold"],
        "source_enabled": facts["source_enabled"],
        "operator_pauses": pauses,
        "watches": watches,
        "hosts": hosts,
        "backpressure_reasons": pressure_reasons,
        "reasons": sorted(reasons),
        "incomplete_gate_assessment": True,
        "unassessed_gates": facts["unassessed_gates"],
        "missing_watch_ids": facts.get("missing_watch_ids", []),
    }


def _sample(
    conn: sqlite3.Connection,
    config: Config,
    event: sqlite3.Row,
    *,
    now: datetime,
    operator_hold: bool,
    policy_digest: str,
    host_decisions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    watches, unassessed = _watches(conn, event)
    facts = readiness(
        conn,
        config,
        source=event["source"],
        source_ref=event["source_ref"],
        watch_ids=watches,
        now=now,
        operator_hold=operator_hold,
    )
    normalized = _normalize(facts)
    for watch in normalized["watches"]:
        watch["expansion_denial"] = expansion_denial(
            conn,
            config,
            watch_id=watch["watch_id"],
            host=watch["request_host"],
            now=now,
            host_decisions=host_decisions,
        )
    if any(watch["expansion_denial"] for watch in normalized["watches"]):
        normalized["reasons"] = sorted({*normalized["reasons"], "event_expansion_deferred"})
    bundle = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
    revision = conn.execute("SELECT revision FROM control_state WHERE singleton=1").fetchone()
    if event["enumeration_id"] is None:
        unassessed.append("legacy_enumeration_unknown")
    if normalized["missing_watch_ids"]:
        unassessed.append("member_watch_missing")
    return {
        **normalized,
        "format": FORMAT,
        "enumeration_id": event["enumeration_id"],
        "policy_digest": policy_digest,
        "input_bundle_hash": bundle[0] if bundle else None,
        "control_revision": revision[0] if revision else None,
        "unassessed_inputs": unassessed,
        "basis": "recorded blocker sample; no continuous interval, eligibility, or stage progress",
    }


def refresh(
    database: Database,
    config: Config,
    *,
    now: datetime,
    run_id: str,
    operator_hold: bool,
    max_events: int = 8,
) -> dict[str, Any]:
    """Serialized cycle bookkeeping; failure rolls back samples, summary and cursor.

    Observation does not execute paused source work. It reads controls without
    expiring them and performs no artifact or source reads.
    """
    if type(max_events) is not int or not 1 <= max_events <= 100:
        raise ValueError("blocker refresh max_events must be between 1 and 100")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("blocker observation requires an aware time")
    now = now.astimezone(UTC)
    if not _available(database.connection):
        return {"supported": False, "reason": "blocker_history_schema_unavailable"}
    digest, raw_policy = _policy(config)
    with database.transaction() as conn:
        cursor = conn.execute("SELECT * FROM event_blocker_cursor").fetchone()
        last, high, pass_id = cursor["last_rowid"], cursor["high_water"], cursor["pass"]
        if high == 0:
            high = conn.execute(
                "SELECT coalesce(max(rowid),0) FROM source_event_inventory"
            ).fetchone()[0]
            last, pass_id = 0, pass_id + 1
        rows = list(
            conn.execute(
                "SELECT rowid,* FROM source_event_inventory WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
                (last, high, max_events + 1),
            )
        )
        ended = len(rows) <= max_events
        rows = rows[:max_events]
        total = conn.execute("SELECT count(*) FROM source_event_inventory").fetchone()[0]
        changes = []
        host_decisions: dict[str, dict[str, Any]] = {}
        reason_counts: Counter[str] = Counter()
        unassessed = 0
        for event in rows:
            facts = _sample(
                conn,
                config,
                event,
                now=now,
                operator_hold=operator_hold,
                policy_digest=digest,
                host_decisions=host_decisions,
            )
            raw = _json(facts)
            signature = sha256(raw.encode()).hexdigest()
            previous = conn.execute(
                "SELECT o.signature FROM event_blocker_latest l JOIN event_blocker_observations o USING(observation_id) WHERE l.source=? AND l.source_ref=?",
                (event["source"], event["source_ref"]),
            ).fetchone()
            if previous is None or previous[0] != signature:
                changes.append((event["source"], event["source_ref"], signature, raw))
            reason_counts.update(facts["reasons"])
            unassessed += bool(facts["unassessed_inputs"])
        summary = {
            "supported": True,
            "run_id": run_id,
            "observed_at": now.isoformat(),
            "scanned_events": len(rows),
            "changed_events": len(changes),
            "catalog_events": total,
            "unobserved_events": total - len(rows),
            "unassessed_events": unassessed,
            "incomplete_observation_coverage": len(rows) < total or bool(unassessed),
            "incomplete_gate_assessment": True,
            "reason_counts": dict(sorted(reason_counts.items())),
            "catalog_pass": pass_id,
            "pass_finished": ended,
            "basis": "this bounded sample only; gaps between samples remain unknown",
        }
        conn.execute(
            "INSERT INTO event_blocker_policies VALUES (?,?) ON CONFLICT(digest) DO NOTHING",
            (digest, raw_policy),
        )
        receipt = conn.execute(
            "INSERT INTO event_blocker_refreshes(run_id,observed_at,summary_json) VALUES (?,?,?)",
            (run_id, now.isoformat(), _json(summary)),
        ).lastrowid
        for source, source_ref, signature, raw in changes:
            observation = conn.execute(
                "INSERT INTO event_blocker_observations(source,source_ref,refresh_id,observed_at,signature,policy_digest,facts_json) VALUES (?,?,?,?,?,?,?)",
                (source, source_ref, receipt, now.isoformat(), signature, digest, raw),
            ).lastrowid
            conn.execute(
                "INSERT INTO event_blocker_latest VALUES (?,?,?) ON CONFLICT(source,source_ref) DO UPDATE SET observation_id=excluded.observation_id",
                (source, source_ref, observation),
            )
        conn.execute(
            "UPDATE event_blocker_cursor SET last_rowid=?,high_water=?,pass=? WHERE singleton=1",
            (0 if ended else rows[-1]["rowid"], 0 if ended else high, pass_id),
        )
    return {**summary, "refresh_id": receipt}


def report(
    conn: sqlite3.Connection, *, source: str, source_ref: str, limit: int = 20
) -> dict[str, Any]:
    """Read retained samples without filling gaps or reconstructing legacy ages."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("blocker history limit must be between 1 and 100")
    if not _available(conn):
        return {"supported": False, "reason": "blocker_history_schema_unavailable"}
    recent = [
        {**dict(row), "facts": json.loads(row["facts_json"])}
        for row in conn.execute(
            "SELECT o.*,r.run_id FROM event_blocker_observations o JOIN event_blocker_refreshes r USING(refresh_id) WHERE o.source=? AND o.source_ref=? ORDER BY observation_id DESC LIMIT ?",
            (source, source_ref, limit + 1),
        )
    ]
    truncated = len(recent) > limit
    recent = recent[:limit]
    for row in recent:
        del row["facts_json"]
    return {
        "supported": True,
        "source": source,
        "source_ref": source_ref,
        "latest": recent[0] if recent else None,
        "recent": recent,
        "details_truncated": truncated,
        "detail_limit": limit,
        "history_before_first_observation": "unknown",
        "eligible_service_age_seconds": None,
        "successful_progress_at": None,
        "basis": "change-only observations; absence of a change does not prove continuous eligibility",
    }
