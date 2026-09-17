"""Conservative local-work observations gate only entry into additional events."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

from swingset.config import Config
from swingset.state.db import Database

from .event_enumerations import memberships
from .event_request_kind import SOURCE_EVENT_PARSERS, is_source_event_request, purpose


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='event_pressure_state'").fetchone()
        is not None
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def policy(config: Config) -> dict[str, Any]:
    from .event_pressure_probe import FORMAT, Limits

    value = {
        "format": "event-pressure-v1",
        "probe_format": FORMAT,
        "high": config.scheduler.event_pressure_high,
        "low": config.scheduler.event_pressure_low,
        "max_age_seconds": config.scheduler.event_pressure_max_age_seconds,
        "refresh_events": config.scheduler.event_pressure_refresh_events,
        "probe_limits": asdict(Limits(members=config.scheduler.event_pressure_probe_pages)),
        "acceptance": "proposed_unmeasured",
    }
    return {"digest": sha256(_json(value).encode()).hexdigest(), "values": value}


def _identities(conn: sqlite3.Connection, watch: sqlite3.Row) -> set[tuple[str, str]]:
    result = {
        (row["source"], row["source_ref"])
        for row in memberships(conn, [watch["watch_id"]]).get(watch["watch_id"], [])
    }
    if is_source_event_request(
        source=watch["source"],
        parser=watch["parser"],
        watch_kind=watch["kind"],
        source_ref=watch["source_ref"],
    ):
        result.add((watch["source"], watch["source_ref"]))
    return result


def _start(
    conn: sqlite3.Connection,
    identity: tuple[str, str],
    *,
    now: datetime,
    basis: str,
    host: str | None,
    watch_id: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO event_pressure_subjects(source,source_ref,enrolled_at,start_basis) VALUES (?,?,?,?)",
        (*identity, now.isoformat(), basis),
    )
    conn.execute(
        "INSERT OR IGNORE INTO event_pressure_watches VALUES (?,?,?)", (watch_id, *identity)
    )
    if host:
        conn.execute("INSERT OR IGNORE INTO event_pressure_hosts VALUES (?,?,?)", (host, *identity))


def record_started(
    conn: sqlite3.Connection, config: Config, *, watch_id: str | None, host: str, now: datetime
) -> None:
    """Join the actual request debit transaction, including robots and redirects."""
    if not available(conn) or watch_id is None:
        return
    if not conn.in_transaction:
        raise RuntimeError("event pressure starts require the request debit transaction")
    watch = conn.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
    if watch is None or purpose(watch["parser"]) not in {"result", "event_index"}:
        return
    for identity in _identities(conn, watch):
        _start(conn, identity, now=now, basis="issued_request", host=host, watch_id=watch_id)
    persist_latches(conn, config, now=now)


def _pending(conn: sqlite3.Connection) -> bool:
    """Unfetched indexes alone never hold admission while metadata catches up."""
    results = _json(
        sorted(parser for parser in SOURCE_EVENT_PARSERS if purpose(parser) == "result")
    )
    return (
        conn.execute(
            "SELECT 1 FROM event_pressure_dirty d LEFT JOIN watches w USING(watch_id) WHERE "
            "EXISTS(SELECT 1 FROM event_pressure_watches p WHERE p.watch_id=d.watch_id) OR "
            "(w.parser IN (SELECT value FROM json_each(?)) AND ("
            "EXISTS(SELECT 1 FROM snapshots s WHERE s.watch_id=d.watch_id) OR "
            "(w.parser IN (SELECT value FROM json_each(?)) AND EXISTS(SELECT 1 FROM source_event_member_watches m "
            "JOIN source_event_inventory i USING(enumeration_id) WHERE m.watch_id=d.watch_id)))) LIMIT 1",
            (_json(sorted(SOURCE_EVENT_PARSERS)), results),
        ).fetchone()
        is not None
    )


def sync(
    conn: sqlite3.Connection, config: Config, *, now: datetime, limit: int = 100
) -> dict[str, Any]:
    """Enroll bounded dirty watch metadata; verification is a separate operation."""
    if not available(conn):
        return {"supported": False, "processed": 0}
    if not conn.in_transaction:
        raise RuntimeError("pressure metadata synchronization requires a transaction")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("pressure metadata limit must be between 1 and 100")
    dirty = list(
        conn.execute("SELECT watch_id FROM event_pressure_dirty ORDER BY rowid LIMIT ?", (limit,))
    )
    for (identifier,) in dirty:
        watch = conn.execute("SELECT * FROM watches WHERE watch_id=?", (identifier,)).fetchone()
        if watch is not None:
            kind = purpose(watch["parser"])
            declared = memberships(conn, [identifier]).get(identifier, [])
            retained = (
                conn.execute(
                    "SELECT 1 FROM snapshots WHERE watch_id=? LIMIT 1", (identifier,)
                ).fetchone()
                is not None
            )
            host = urlsplit(watch["archive_url"] or watch["url"]).hostname
            for identity in _identities(conn, watch):
                started = conn.execute(
                    "SELECT 1 FROM event_pressure_subjects WHERE source=? AND source_ref=?",
                    identity,
                ).fetchone()
                if (
                    started
                    or (kind in {"result", "event_index"} and retained)
                    or (kind == "result" and declared)
                ):
                    _start(
                        conn,
                        identity,
                        now=now,
                        basis="retained_evidence" if retained else "admitted_result_obligation",
                        host=host,
                        watch_id=identifier,
                    )
        conn.execute("DELETE FROM event_pressure_dirty WHERE watch_id=?", (identifier,))
    persist_latches(conn, config, now=now)
    return {"supported": True, "processed": len(dirty), "metadata_pending": _pending(conn)}


def bootstrap(
    database: Database, config: Config, *, now: datetime, limit: int = 100
) -> dict[str, Any]:
    with database.transaction() as conn:
        return sync(conn, config, now=now, limit=limit)


def token(
    conn: sqlite3.Connection, source: str, source_ref: str, policy_digest: str
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT p.revision,i.enumeration_id FROM event_pressure_subjects p LEFT JOIN source_event_inventory i USING(source,source_ref) WHERE p.source=? AND p.source_ref=?",
        (source, source_ref),
    ).fetchone()
    bundle = conn.execute("SELECT value FROM meta WHERE key='input_bundle_hash'").fetchone()
    return {
        "revision": row[0] if row else None,
        "enumeration_id": row[1] if row else None,
        "epoch": conn.execute("SELECT epoch FROM event_pressure_state").fetchone()[0],
        "input_bundle_hash": bundle[0] if bundle else None,
        "policy_digest": policy_digest,
    }


def subject_status(
    conn: sqlite3.Connection, row: sqlite3.Row, *, now: datetime, policy_digest: str
) -> str:
    observation = json.loads(row["observation_json"]) if row["observation_json"] else None
    if observation is None:
        return "unknown"
    if observation["token"] != token(conn, row["source"], row["source_ref"], policy_digest):
        return "changed"
    if datetime.fromisoformat(observation["valid_until"]) <= now:
        return "stale"
    if not observation["parent_valid"] or not observation["checked"] or observation["unknown"]:
        return "unknown"
    return (
        "local_gap"
        if any(
            observation[stage] != observation["checked"] for stage in ("acquired", "interpreted")
        )
        else "locally_accounted"
    )


def admission(
    conn: sqlite3.Connection, config: Config, *, host: str, now: datetime
) -> dict[str, Any]:
    """Read compact hints only. An open expansion gate grants no fetch permission."""
    if not available(conn):
        return {"supported": False, "deferred": False, "reason": "pressure_schema_unavailable"}
    captured = policy(config)
    counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT p.* FROM event_pressure_hosts h JOIN event_pressure_subjects p USING(source,source_ref) WHERE h.host=?",
        (host,),
    ):
        state = subject_status(conn, row, now=now, policy_digest=captured["digest"])
        counts[state] = counts.get(state, 0) + 1
    total = sum(counts.values())
    pressure = total - counts.get("locally_accounted", 0)
    old = conn.execute(
        "SELECT deferred FROM event_pressure_latches WHERE host=?", (host,)
    ).fetchone()
    deferred = bool(old[0]) if old else False
    if pressure > config.scheduler.event_pressure_high:
        deferred = True
    elif pressure < config.scheduler.event_pressure_low:
        deferred = False
    pending = _pending(conn)
    return {
        "supported": True,
        "host": host,
        "deferred": deferred or pending,
        "watermark_deferred": deferred,
        "metadata_pending": pending,
        "reason": "event pressure metadata pending"
        if pending
        else "event expansion pressure"
        if deferred
        else None,
        "started_subjects": total,
        "pressure_subjects": pressure,
        "states": counts,
        "policy": captured,
        "basis": "local acquisition/interpretation scheduling hints; no completion authority",
    }


def persist_latches(conn: sqlite3.Connection, config: Config, *, now: datetime) -> None:
    for (host,) in list(conn.execute("SELECT DISTINCT host FROM event_pressure_hosts")):
        value = admission(conn, config, host=host, now=now)
        conn.execute(
            "INSERT INTO event_pressure_latches VALUES (?,?,?,?,?) ON CONFLICT(host) DO UPDATE SET "
            "deferred=excluded.deferred,policy_digest=excluded.policy_digest,policy_json=excluded.policy_json,changed_at=excluded.changed_at "
            "WHERE deferred<>excluded.deferred OR policy_digest<>excluded.policy_digest",
            (
                host,
                int(value["watermark_deferred"]),
                value["policy"]["digest"],
                _json(value["policy"]["values"]),
                now.isoformat(),
            ),
        )


def request_denial(
    conn: sqlite3.Connection,
    config: Config,
    *,
    watch_id: str | None,
    host: str | None,
    now: datetime,
    host_decisions: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    if not available(conn) or watch_id is None:
        return None
    watch = conn.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
    if watch is None or purpose(watch["parser"]) != "event_index":
        return None
    identities = _identities(conn, watch)
    if not identities:
        return None
    if any(
        conn.execute(
            "SELECT 1 FROM event_pressure_subjects WHERE source=? AND source_ref=?", key
        ).fetchone()
        for key in identities
    ):
        return None
    # Metadata enrollment is bounded and may lag behind retained evidence.
    # Continuations stay continuations before their hint row is materialized.
    parsers = _json(sorted(SOURCE_EVENT_PARSERS))
    for key in identities:
        retained = conn.execute(
            "SELECT 1 FROM watches w WHERE w.source=? AND w.source_ref=? "
            "AND w.parser IN (SELECT value FROM json_each(?)) "
            "AND EXISTS(SELECT 1 FROM snapshots s WHERE s.watch_id=w.watch_id) LIMIT 1",
            (*key, parsers),
        ).fetchone()
        listed = conn.execute(
            "SELECT 1 FROM source_event_inventory i JOIN source_event_member_watches m "
            "USING(enumeration_id) JOIN watches w USING(watch_id) "
            "WHERE i.source=? AND i.source_ref=? "
            "AND w.parser IN (SELECT value FROM json_each(?)) LIMIT 1",
            (
                *key,
                _json(
                    sorted(parser for parser in SOURCE_EVENT_PARSERS if purpose(parser) == "result")
                ),
            ),
        ).fetchone()
        if retained or listed:
            return None
    actual_host = host or urlsplit(watch["archive_url"] or watch["url"]).hostname
    if actual_host is None:
        return None
    if host_decisions is not None and actual_host in host_decisions:
        value = host_decisions[actual_host]
    else:
        value = admission(conn, config, host=actual_host, now=now)
        if host_decisions is not None:
            host_decisions[actual_host] = value
    return value["reason"] if value["deferred"] else None


def report(
    conn: sqlite3.Connection,
    config: Config,
    *,
    now: datetime,
    source: str | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    if not available(conn):
        return {"supported": False, "reason": "pressure_schema_unavailable"}
    captured = policy(config)
    subjects = []
    for row in conn.execute(
        "SELECT * FROM event_pressure_subjects WHERE (? IS NULL OR source=?) AND (? IS NULL OR source_ref=?) ORDER BY source,source_ref",
        (source, source, source_ref, source_ref),
    ):
        subjects.append(
            {
                "source": row["source"],
                "source_ref": row["source_ref"],
                "enrolled_at": row["enrolled_at"],
                "start_basis": row["start_basis"],
                "status": subject_status(conn, row, now=now, policy_digest=captured["digest"]),
                "scan": json.loads(row["scan_json"]) if row["scan_json"] else None,
                "observation": json.loads(row["observation_json"])
                if row["observation_json"]
                else None,
            }
        )
    hosts = [
        admission(conn, config, host=row[0], now=now)
        for row in conn.execute(
            "SELECT DISTINCT host FROM event_pressure_hosts WHERE (? IS NULL OR source=?) AND (? IS NULL OR source_ref=?) ORDER BY host",
            (source, source, source_ref, source_ref),
        )
    ]
    return {
        "supported": True,
        "snapshot_at": now.astimezone(UTC).isoformat(),
        "subjects": subjects,
        "hosts": hosts,
        "policy": captured,
        "metadata_pending": _pending(conn),
        "basis": "scheduling hints only; pagination and publication unassessed",
    }


def refresh(
    database: Database,
    archive: Any,
    config: Config,
    *,
    now: datetime,
    max_events: int | None = None,
    wall_seconds: float = 5.0,
) -> dict[str, Any]:
    from .event_pressure_refresh import refresh as run

    return run(database, archive, config, now=now, max_events=max_events, wall_seconds=wall_seconds)
